#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath

from csv安全写入 import read_csv_table_with_snapshot, serialize_csv
from 内容列表完整性判据 import (
    BASE_REPORT_RELATIVE,
    derive_content_list_completeness,
    read_content_list_fact_from_report,
)
from 账号分析证据分块执行器 import (
    AccountEvidenceChunkError,
    verify_account_evidence_sources,
    verify_account_summary_input,
)
from 账号阶段状态 import build_account_business_stage_state


ACCOUNT_LIBRARY_PATH = Path("对标资料/对标账号库.csv")
REQUIRED_SECTIONS = ["1. 账号速览", "2. 关键观察", "3. 研究价值", "4. 深挖建议", "5. 当前判断边界"]
QUALITY_VALUES = {"值得研究", "一般", "可放弃"}
FIT_VALUES = {"高适配", "中适配", "低适配", "不能判断"}
DEEP_DIVE_VALUES = {"建议深入", "暂不建议深入"}
COMPLETENESS_VALUES = {"完整", "可能未抓全", "未知"}


class AccountSummaryFinalizeError(RuntimeError):
    pass


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="校验并正式保存账号概要分析")
    parser.add_argument("--workspace-root", required=True)
    parser.add_argument("--candidate-path", required=True)
    parser.add_argument("--evidence-manifest", required=True)
    parser.add_argument("--account-id", required=True)
    parser.add_argument("--platform", default="")
    return parser.parse_args()


def resolve_workspace_path(root: Path, raw_path: str, *, required_prefix: str = "") -> Path:
    normalized = str(raw_path or "").strip().replace("\\", "/")
    if not normalized:
        raise AccountSummaryFinalizeError("path_missing")
    logical = PurePosixPath(normalized)
    candidate = Path(normalized).expanduser()
    resolved = candidate.resolve() if candidate.is_absolute() else root.joinpath(*logical.parts).resolve()
    try:
        relative = resolved.relative_to(root)
    except ValueError as exc:
        raise AccountSummaryFinalizeError(f"path_outside_workspace:{normalized}") from exc
    if required_prefix and not relative.as_posix().startswith(required_prefix):
        raise AccountSummaryFinalizeError(f"path_outside_required_prefix:{normalized}:{required_prefix}")
    return resolved


def read_json(path: Path) -> dict[str, object]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise AccountSummaryFinalizeError(f"json_read_failed:{path}:{exc}") from exc
    if not isinstance(payload, dict):
        raise AccountSummaryFinalizeError(f"json_object_required:{path}")
    return payload


def extract_unique_field(markdown: str, label: str) -> str:
    matches = re.findall(rf"(?m)^-\s*{re.escape(label)}\s*[：:]\s*(.*?)\s*$", markdown)
    if len(matches) != 1 or not matches[0].strip():
        raise AccountSummaryFinalizeError(f"candidate_field_invalid:{label}")
    return matches[0].strip()


def validate_candidate(
    payload: bytes,
    *,
    account_name: str,
    overview_available: bool,
    total_content_count: int,
    selected_content_count: int,
    expected_completeness: str,
) -> dict[str, str]:
    try:
        markdown = payload.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise AccountSummaryFinalizeError("candidate_not_utf8") from exc
    if not markdown.strip():
        raise AccountSummaryFinalizeError("candidate_empty")
    expected_title = f"# {account_name}账号概要分析"
    if len(re.findall(rf"(?m)^{re.escape(expected_title)}\s*$", markdown)) != 1:
        raise AccountSummaryFinalizeError("candidate_title_invalid")
    positions: list[int] = []
    for section in REQUIRED_SECTIONS:
        matches = list(re.finditer(rf"(?m)^##\s+{re.escape(section)}\s*$", markdown))
        if len(matches) != 1:
            raise AccountSummaryFinalizeError(f"candidate_section_invalid:{section}")
        positions.append(matches[0].start())
    if positions != sorted(positions):
        raise AccountSummaryFinalizeError("candidate_section_order_invalid")

    quality = extract_unique_field(markdown, "质量判断")
    fit = extract_unique_field(markdown, "适配判断")
    deep_dive = extract_unique_field(markdown, "深挖建议")
    direction = extract_unique_field(markdown, "建议验证方向")
    if quality not in QUALITY_VALUES:
        raise AccountSummaryFinalizeError(f"quality_value_invalid:{quality}")
    if fit not in FIT_VALUES:
        raise AccountSummaryFinalizeError(f"fit_value_invalid:{fit}")
    if deep_dive not in DEEP_DIVE_VALUES:
        raise AccountSummaryFinalizeError(f"deep_dive_value_invalid:{deep_dive}")
    if not overview_available and fit != "不能判断":
        raise AccountSummaryFinalizeError("fit_must_be_unknown_without_self_overview")
    if deep_dive == "建议深入" and direction == "无":
        raise AccountSummaryFinalizeError("deep_dive_direction_required")
    if deep_dive == "暂不建议深入" and direction != "无":
        raise AccountSummaryFinalizeError("deep_dive_direction_must_be_empty")
    range_matches = re.findall(
        r"(?m)^-\s*内容范围\s*[：:]\s*账号共\s*(\d+)\s*条，本次使用最近\s*(\d+)\s*条。?\s*$",
        markdown,
    )
    if range_matches != [(str(total_content_count), str(selected_content_count))]:
        raise AccountSummaryFinalizeError("content_range_mismatch")
    # 内容列表完整性是机器字段：取值只能来自唯一判据的真值，候选稿不得改写。
    # 判据与详细分析共用 执行单元/内容列表完整性判据.py。
    completeness = extract_unique_field(markdown, "内容列表完整性")
    if completeness not in COMPLETENESS_VALUES:
        raise AccountSummaryFinalizeError(f"content_list_completeness_invalid:{completeness}")
    if completeness != expected_completeness:
        raise AccountSummaryFinalizeError(
            "content_list_completeness_mismatch:"
            f"expected={expected_completeness}:actual={completeness}"
        )
    return {
        "quality": quality,
        "fit": fit,
        "deep_dive": deep_dive,
        "direction": direction,
        "completeness": completeness,
    }


def stage_payload(target: Path, payload: bytes, suffix: str) -> Path:
    target.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{target.name}.", suffix=suffix, dir=str(target.parent))
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
    except Exception:
        temporary.unlink(missing_ok=True)
        raise
    return temporary


def commit_payloads_atomically(
    payloads: dict[Path, bytes],
    *,
    expected_originals: dict[Path, bytes | None] | None = None,
) -> None:
    originals = {path: path.read_bytes() if path.exists() else None for path in payloads}
    if expected_originals:
        for path, expected in expected_originals.items():
            if originals.get(path) != expected:
                raise AccountSummaryFinalizeError(f"concurrent_source_change:{path}")
    staged: dict[Path, Path] = {}
    committed: list[Path] = []
    try:
        for target, payload in payloads.items():
            staged[target] = stage_payload(target, payload, ".stage")
        for target, original in originals.items():
            current = target.read_bytes() if target.exists() else None
            if current != original:
                raise AccountSummaryFinalizeError(f"concurrent_source_change:{target}")
        for target in payloads:
            os.replace(staged[target], target)
            committed.append(target)
        for target, payload in payloads.items():
            if target.read_bytes() != payload:
                raise AccountSummaryFinalizeError(f"post_commit_mismatch:{target}")
    except Exception as exc:
        rollback_errors: list[str] = []
        for target in reversed(committed):
            restore: Path | None = None
            try:
                original = originals[target]
                if original is None:
                    target.unlink(missing_ok=True)
                else:
                    restore = stage_payload(target, original, ".rollback")
                    os.replace(restore, target)
                    restore = None
            except Exception as rollback_exc:
                rollback_errors.append(f"{target}:{rollback_exc}")
            finally:
                if restore is not None:
                    restore.unlink(missing_ok=True)
        if rollback_errors:
            raise AccountSummaryFinalizeError(
                f"commit_failed:{exc};rollback_failed:{';'.join(rollback_errors)}"
            ) from exc
        if isinstance(exc, AccountSummaryFinalizeError):
            raise
        raise AccountSummaryFinalizeError(f"commit_failed:{exc};rollback_completed") from exc
    finally:
        for temporary in staged.values():
            temporary.unlink(missing_ok=True)


def run(args: argparse.Namespace) -> dict[str, object]:
    root = Path(args.workspace_root).expanduser().resolve()
    if not root.is_dir():
        raise AccountSummaryFinalizeError(f"workspace_root_invalid:{root}")
    candidate_path = resolve_workspace_path(root, args.candidate_path, required_prefix=".meiling/staging/")
    manifest_path = resolve_workspace_path(root, args.evidence_manifest, required_prefix=".meiling/staging/")
    manifest = read_json(manifest_path)
    manifest_account = manifest.get("account")
    if not isinstance(manifest_account, dict):
        raise AccountSummaryFinalizeError("manifest_account_invalid")
    if manifest.get("mode") != "summary" or manifest.get("selection_strategy") != "latest_by_publish_time":
        raise AccountSummaryFinalizeError("manifest_summary_selection_invalid")
    if str(manifest_account.get("账号ID") or "") != args.account_id:
        raise AccountSummaryFinalizeError("manifest_account_mismatch")
    if args.platform and str(manifest_account.get("平台") or "") != args.platform:
        raise AccountSummaryFinalizeError("manifest_platform_mismatch")
    try:
        total_count = int(manifest.get("total_content_count") or 0)
        selected_count = int(manifest.get("selected_content_count") or 0)
        selected_video_count = int(manifest.get("selected_video_content_count"))
        expected_count = int(manifest.get("expected_content_count") or 0)
        covered_count = int(manifest.get("covered_content_count") or 0)
    except (TypeError, ValueError) as exc:
        raise AccountSummaryFinalizeError("manifest_content_count_invalid") from exc
    if not (total_count >= selected_count >= 1 and selected_count <= 50):
        raise AccountSummaryFinalizeError("manifest_content_range_invalid")
    if not (0 <= selected_video_count <= selected_count):
        raise AccountSummaryFinalizeError("manifest_video_content_count_invalid")
    if not manifest.get("coverage_complete") or expected_count != selected_count or covered_count != selected_count:
        raise AccountSummaryFinalizeError("manifest_coverage_invalid")
    verify_account_evidence_sources(root, manifest)
    verified = verify_account_summary_input(root, manifest)
    if verified.get("status") != "verified":
        raise AccountSummaryFinalizeError("evidence_verification_failed")

    account_library_path = root / ACCOUNT_LIBRARY_PATH
    fields, rows, account_library_original = read_csv_table_with_snapshot(account_library_path)
    matches = [
        row for row in rows
        if row.get("账号ID", "").strip() == args.account_id
        and (not args.platform or row.get("平台", "").strip() == args.platform)
    ]
    if len(matches) != 1:
        raise AccountSummaryFinalizeError(f"account_match_count:{len(matches)}")
    account = matches[0]
    for field in ("概要分析路径", "质量判断", "适配判断", "内容列表路径"):
        if field not in fields:
            raise AccountSummaryFinalizeError(f"account_library_field_missing:{field}")
    content_list_path = resolve_workspace_path(root, account.get("内容列表路径", ""))
    formal_path = content_list_path.parent / "概要分析.md"
    state_path = content_list_path.parent / "过程记录" / "当前阶段状态.json"
    account_name = account.get("账号名", "").strip()
    if not account_name:
        raise AccountSummaryFinalizeError("account_name_missing")
    overview_path = root / "我方资料/我方当前概要.md"
    overview_available = overview_path.is_file() and bool(overview_path.read_text(encoding="utf-8").strip())
    # 内容列表完整性的真值由唯一判据从基础建档报告算出，候选稿只能逐字复制。
    base_status, base_stop_reasons = read_content_list_fact_from_report(
        content_list_path.parent / BASE_REPORT_RELATIVE
    )
    expected_completeness = derive_content_list_completeness(base_status, base_stop_reasons)
    candidate_payload = candidate_path.read_bytes()
    result = validate_candidate(
        candidate_payload,
        account_name=account_name,
        overview_available=overview_available,
        total_content_count=total_count,
        selected_content_count=selected_count,
        expected_completeness=expected_completeness,
    )

    # 开源版不再要求概要阶段先创建调度器 task_id，但详细数据阶段仍需要
    # 一个稳定的 run_id 绑定本轮 staging。由本地执行器生成（而不是让模型
    # 从目录名猜测），并把最小批次上下文写入 benchmark staging。
    run_id = ""
    candidate_parts = candidate_path.relative_to(root).parts
    try:
        staging_index = candidate_parts.index("staging")
        if (
            staging_index + 2 < len(candidate_parts)
            and candidate_parts[staging_index + 1] == "benchmark-account-diagnose"
        ):
            candidate_run = str(candidate_parts[staging_index + 2]).strip()
            if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}", candidate_run):
                run_id = candidate_run
    except ValueError:
        pass
    if not run_id:
        run_id = f"run-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}-{uuid.uuid4().hex[:10]}"
    # 账号库表头没有「基础数据获取方式」列，直接读它必然是空值；数据源的权威
    # 来源是本轮基础建档报告的顶层 `data_source`。保留账号库读取以兼容历史工作区。
    data_source = str(account.get("基础数据获取方式") or "").strip()
    if not data_source:
        try:
            base_report = json.loads(
                (content_list_path.parent / BASE_REPORT_RELATIVE).read_text(encoding="utf-8")
            )
        except (OSError, UnicodeError, json.JSONDecodeError):
            base_report = {}
        if isinstance(base_report, dict):
            data_source = str(base_report.get("data_source") or "").strip()
    batch_context_path = root / ".meiling/staging/benchmark-account-diagnose" / run_id / "detail-data" / "批次上下文.json"
    batch_context = {
        "schema_version": 1,
        "flow_type": "benchmark_account",
        "run_id": run_id,
        "account_id": args.account_id,
        "account_name": account_name,
        "platform": str(account.get("平台") or args.platform or "").strip(),
        "content_list_path": str(account.get("内容列表路径") or "").strip(),
        "content_library_path": str(account.get("内容库路径") or "对标资料/对标内容库.csv").strip(),
        "data_source": data_source,
        "selected_content_ids": [],
        "confirmed": False,
    }
    batch_context_path.parent.mkdir(parents=True, exist_ok=True)
    if batch_context_path.exists():
        try:
            existing_context = read_json(batch_context_path)
        except AccountSummaryFinalizeError:
            existing_context = {}
        if str(existing_context.get("account_id") or "").strip() not in {"", args.account_id}:
            raise AccountSummaryFinalizeError("batch_context_account_mismatch")
        batch_context = {**batch_context, **existing_context, "run_id": run_id, "account_id": args.account_id}

    formal_relative = formal_path.relative_to(root).as_posix()
    state_relative = state_path.relative_to(root).as_posix()
    account["概要分析路径"] = formal_relative
    account["质量判断"] = result["quality"]
    account["适配判断"] = result["fit"]
    detail_analysis_completed = False
    detail_analysis_entry_available = selected_count > 0
    detail_analysis_basis = "summary_samples"
    stage_state = build_account_business_stage_state(
        stage="账号概要分析",
        complete=True,
        platform=account.get("平台", ""),
        account_id=args.account_id,
        account_name=account_name,
    )
    state_payload = (json.dumps(stage_state, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    batch_context_payload = (json.dumps(batch_context, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    csv_payload = serialize_csv(fields, rows)
    commit_payloads_atomically(
        {
            formal_path: candidate_payload,
            account_library_path: csv_payload,
            state_path: state_payload,
            batch_context_path: batch_context_payload,
        },
        expected_originals={account_library_path: account_library_original},
    )
    return {
        "ok": True,
        "status": "committed",
        "formal_path": formal_relative,
        "account_library_path": ACCOUNT_LIBRARY_PATH.as_posix(),
        "stage_state_path": state_relative,
        "quality": result["quality"],
        "fit": result["fit"],
        "deep_dive": result["deep_dive"],
        "direction": result["direction"],
        "content_list_completeness": result["completeness"],
        "total_content_count": total_count,
        "selected_content_count": selected_count,
        "selected_video_content_count": selected_video_count,
        "detail_analysis_completed": detail_analysis_completed,
        "detail_analysis_entry_available": detail_analysis_entry_available,
        "detail_analysis_basis": detail_analysis_basis,
        "formal_sha256": hashlib.sha256(candidate_payload).hexdigest(),
        "run_id": run_id,
        "batch_context_path": batch_context_path.relative_to(root).as_posix(),
    }


def main() -> int:
    try:
        result = run(parse_args())
    except (OSError, ValueError, AccountSummaryFinalizeError, AccountEvidenceChunkError) as exc:
        print(json.dumps({"ok": False, "status": "failed", "error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
