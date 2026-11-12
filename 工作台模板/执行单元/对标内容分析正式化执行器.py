#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import tempfile
from pathlib import Path, PurePosixPath
from typing import Any

from csv安全写入 import read_csv_table_with_snapshot, serialize_csv
from 内容特征词典 import (
    ContentFeatureDictionaryError,
    load_feature_catalog as load_workspace_feature_catalog,
)
from 对标内容账号列表同步 import (
    AccountContentListSyncError,
    prepare_account_content_list_sync,
    verify_account_content_list_sync,
)
from 内容特征写入执行器 import analysis_version_from_feature_set
from 对标内容分析运行状态 import (
    BenchmarkContentRunStateError,
    bind_run_paths,
    prepare_run_state_update,
    verify_snapshot_run_binding,
)


CONTENT_LIBRARY = Path("对标资料/对标内容库.csv")
# 主特征按「特征小类」准入：每个小类最多 1 条。
# （原 CORE_FEATURE_LIMITS = {"策略层": 1, "机制层": 2, "动作层": 3} 的按层配额已废止）
CORE_FEATURE_LIMIT_PER_TYPE = 1
# 候补标准特征总数上限。
CANDIDATE_FEATURE_LIMIT = 4
FEATURE_JUDGMENT_CANDIDATE_SECTIONS = (
    "1. 核心标准特征",
    "2. 候补标准特征",
    "3. 待补充标签",
)
CONTENT_TASKS = {"流量", "涨粉", "交易"}
BENCHMARK_CONTENT_TASK = "未知"


class ContentAnalysisFinalizeError(RuntimeError):
    pass


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="分阶段校验并原子保存对标内容分析")
    parser.add_argument("--workspace-root", required=True)
    parser.add_argument("--content-id", required=True)
    parser.add_argument("--platform", default="")
    parser.add_argument("--stage", required=True, choices=("analysis", "features"))
    parser.add_argument("--analysis-input-file", required=True)
    parser.add_argument("--analysis-candidate", default="")
    parser.add_argument("--receipt-file", default="")
    parser.add_argument("--feature-candidate", default="")
    parser.add_argument("--analysis-receipt-file", default="")
    parser.add_argument("--decision-state-file", default="")
    parser.add_argument("--validate-only", action="store_true")
    return parser.parse_args()


def resolve_under(root: Path, raw_path: str, *, required_prefix: str = "") -> Path:
    root = root.resolve()
    text = str(raw_path or "").strip().replace("\\", "/")
    if not text:
        raise ContentAnalysisFinalizeError("path_missing")
    logical = PurePosixPath(text)
    candidate = Path(text).expanduser()
    resolved = candidate.resolve() if candidate.is_absolute() else root.joinpath(*logical.parts).resolve()
    try:
        relative = resolved.relative_to(root)
    except ValueError as exc:
        raise ContentAnalysisFinalizeError(f"path_outside_workspace:{text}") from exc
    if required_prefix and not relative.as_posix().startswith(required_prefix):
        raise ContentAnalysisFinalizeError(f"path_outside_required_prefix:{text}:{required_prefix}")
    return resolved


def relative_path(root: Path, path: Path) -> str:
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError as exc:
        raise ContentAnalysisFinalizeError(f"path_outside_workspace:{path}") from exc


def decode_markdown(path: Path) -> tuple[str, bytes]:
    try:
        payload = path.read_bytes()
        text = payload.decode("utf-8-sig")
    except (OSError, UnicodeError) as exc:
        raise ContentAnalysisFinalizeError(f"candidate_read_failed:{path}:{exc}") from exc
    if not text.strip():
        raise ContentAnalysisFinalizeError(f"candidate_empty:{path.name}")
    return text, text.encode("utf-8")


def validate_title(markdown: str, title: str) -> None:
    if len(re.findall(rf"(?m)^#\s+{re.escape(title)}\s*$", markdown)) != 1:
        raise ContentAnalysisFinalizeError(f"candidate_title_invalid:{title}")


def validate_sections(markdown: str, sections: tuple[str, ...]) -> None:
    positions: list[int] = []
    for section in sections:
        matches = list(re.finditer(rf"(?m)^##\s+{re.escape(section)}\s*$", markdown))
        if len(matches) != 1:
            raise ContentAnalysisFinalizeError(f"candidate_section_invalid:{section}")
        positions.append(matches[0].start())
    if positions != sorted(positions):
        raise ContentAnalysisFinalizeError("candidate_section_order_invalid")


def extract_field(
    markdown: str,
    label: str,
    *,
    allow_blank: bool = False,
    section: str = "",
) -> str:
    source = section_body(markdown, section) if section else markdown
    matches = re.findall(rf"(?m)^-\s*{re.escape(label)}\s*[：:]\s*(.*?)\s*$", source)
    if len(matches) != 1 or (not allow_blank and not matches[0].strip()):
        raise ContentAnalysisFinalizeError(f"candidate_field_invalid:{label}")
    return matches[0].strip().strip("`")


def replace_field_in_section(markdown: str, label: str, line: str, section: str) -> str:
    heading = re.search(rf"(?m)^##\s+{re.escape(section)}\s*$", markdown)
    if heading is None:
        return markdown
    following = re.search(r"(?m)^##\s+", markdown[heading.end():])
    end = heading.end() + following.start() if following else len(markdown)
    body = markdown[heading.end():end]
    pattern = rf"(?m)^-\s*{re.escape(label)}\s*[：:].*$"
    if re.search(pattern, body):
        body = re.sub(pattern, line, body, count=1)
    else:
        body = "\n" + line + body
    return markdown[:heading.end()] + body + markdown[end:]


def section_body(markdown: str, section: str) -> str:
    match = re.search(rf"(?m)^##\s+{re.escape(section)}\s*$", markdown)
    if match is None:
        return ""
    following = re.search(r"(?m)^##\s+", markdown[match.end():])
    end = match.end() + following.start() if following else len(markdown)
    return markdown[match.end():end]
def load_feature_catalog(
    workspace_root: Path | str | None = None,
) -> dict[str, dict[str, object]]:
    try:
        return load_workspace_feature_catalog(workspace_root)
    except ContentFeatureDictionaryError as exc:
        raise ContentAnalysisFinalizeError(f"feature_catalog_read_failed:{exc}") from exc
def _candidate_table_rows(markdown: str, section: str, header: tuple[str, ...]) -> list[list[str]]:
    body = section_body(markdown, section)
    actual_header: list[str] | None = None
    rows: list[list[str]] = []
    for line in body.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if not stripped.startswith("|"):
            continue
        if re.fullmatch(r"\|?\s*:?-{3,}:?(?:\s*\|\s*:?-{3,}:?)+\s*\|?", stripped):
            continue
        cells = [cell.strip().strip("`") for cell in stripped.strip("|").split("|")]
        if actual_header is None:
            actual_header = cells
            continue
        rows.append(cells)
    if actual_header is None or tuple(actual_header[: len(header)]) != header:
        raise ContentAnalysisFinalizeError(
            f"feature_candidate_table_header_invalid:{section}"
        )
    return rows


def parse_feature_candidate_rows(
    markdown: str,
    catalog: dict[str, dict[str, object]],
) -> tuple[list[dict[str, str]], list[dict[str, str]]]:
    """解析候选：「1. 核心标准特征」每小类 ≤1，「2. 候补标准特征」总数 ≤4。"""
    type_counts: dict[str, int] = {}
    seen_ids: set[str] = set()
    result: list[dict[str, str]] = []
    for cells in _candidate_table_rows(
        markdown, "1. 核心标准特征", ("特征ID", "实例证据", "证据部位")
    ):
        if len(cells) < 3:
            raise ContentAnalysisFinalizeError("feature_candidate_column_count_invalid")
        feature_id, evidence, *location_parts = cells
        location = " | ".join(location_parts).strip()
        definition = catalog.get(feature_id)
        if definition is None:
            raise ContentAnalysisFinalizeError(f"unknown_feature_id:{feature_id or 'missing'}")
        if feature_id in seen_ids:
            raise ContentAnalysisFinalizeError(f"feature_id_duplicate:{feature_id}")
        if not evidence or not location:
            raise ContentAnalysisFinalizeError(f"feature_evidence_missing:{feature_id}")
        seen_ids.add(feature_id)
        layer = str(definition.get("layer") or "")
        feature_type = str(definition.get("feature_type") or "")
        type_counts[feature_type] = type_counts.get(feature_type, 0) + 1
        if type_counts[feature_type] > CORE_FEATURE_LIMIT_PER_TYPE:
            raise ContentAnalysisFinalizeError(
                f"feature_type_count_exceeded:{feature_type}:"
                f"maximum={CORE_FEATURE_LIMIT_PER_TYPE}:actual={type_counts[feature_type]}"
            )
        result.append({
            "feature_id": feature_id,
            "layer": layer,
            "feature_type": feature_type,
            "evidence": evidence,
            "location": location,
        })
    candidates: list[dict[str, str]] = []
    seen_candidate_ids: set[str] = set()
    for cells in _candidate_table_rows(
        markdown,
        "2. 候补标准特征",
        ("特征ID", "实例证据", "证据部位", "候补理由"),
    ):
        if len(cells) < 4:
            raise ContentAnalysisFinalizeError("candidate_feature_column_count_invalid")
        feature_id, evidence, *rest = cells
        location = rest[0].strip()
        reason = " | ".join(rest[1:]).strip()
        definition = catalog.get(feature_id)
        if definition is None:
            raise ContentAnalysisFinalizeError(
                f"unknown_candidate_feature_id:{feature_id or 'missing'}"
            )
        if feature_id in seen_candidate_ids or feature_id in seen_ids:
            raise ContentAnalysisFinalizeError(f"candidate_feature_id_duplicate:{feature_id}")
        if not evidence or not location or not reason:
            raise ContentAnalysisFinalizeError(f"candidate_feature_field_missing:{feature_id}")
        seen_candidate_ids.add(feature_id)
        candidates.append({
            "feature_id": feature_id,
            "layer": str(definition.get("layer") or ""),
            "feature_type": str(definition.get("feature_type") or ""),
            "evidence": evidence,
            "location": location,
            "reason": reason,
        })
    if len(candidates) > CANDIDATE_FEATURE_LIMIT:
        raise ContentAnalysisFinalizeError(
            f"candidate_feature_count_exceeded:maximum={CANDIDATE_FEATURE_LIMIT}:actual={len(candidates)}"
        )
    return result, candidates


def validate_feature_judgment_candidate(
    markdown: str,
    *,
    root: Path,
) -> tuple[list[dict[str, str]], list[dict[str, str]], str, str]:
    validate_title(markdown, "内容特征判断候选")
    validate_sections(markdown, FEATURE_JUDGMENT_CANDIDATE_SECTIONS)
    features, candidates = parse_feature_candidate_rows(markdown, load_feature_catalog(root))
    # 「2. 候补标准特征」与「3. 待补充标签」都允许为空（本篇可能没有候补、也没发现词典缺口），
    # 因此不做 require_meaningful_body；节结构由 validate_sections 保证。
    uncovered = section_body(markdown, "3. 待补充标签").strip()
    return features, candidates, uncovered, "none"
def infer_content_dir(root: Path, row: dict[str, str]) -> Path:
    for field in ("内容目录路径", "视频文案路径", "内容分析路径"):
        value = row.get(field, "").strip()
        if not value:
            continue
        path = resolve_under(root, value)
        return path if field == "内容目录路径" else path.parent
    raise ContentAnalysisFinalizeError("content_dir_missing")


def stage_payload(target: Path, payload: bytes, suffix: str) -> Path:
    target.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{target.name}.", suffix=suffix, dir=str(target.parent)
    )
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
    for path, expected in (expected_originals or {}).items():
        if originals.get(path) != expected:
            raise ContentAnalysisFinalizeError(f"concurrent_source_change:{path}")
    staged: dict[Path, Path] = {}
    committed: list[Path] = []
    try:
        for target, payload in payloads.items():
            staged[target] = stage_payload(target, payload, ".stage")
        for target, original in originals.items():
            current = target.read_bytes() if target.exists() else None
            if current != original:
                raise ContentAnalysisFinalizeError(f"concurrent_source_change:{target}")
        for target in payloads:
            os.replace(staged[target], target)
            committed.append(target)
        for target, payload in payloads.items():
            if target.read_bytes() != payload:
                raise ContentAnalysisFinalizeError(f"post_commit_mismatch:{target}")
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
            raise ContentAnalysisFinalizeError(
                f"commit_failed:{exc};rollback_failed:{';'.join(rollback_errors)}"
            ) from exc
        if isinstance(exc, ContentAnalysisFinalizeError):
            raise
        raise ContentAnalysisFinalizeError(f"commit_failed:{exc};rollback_completed") from exc
    finally:
        for temporary in staged.values():
            temporary.unlink(missing_ok=True)


ANALYSIS_STAGING_PREFIX = ".meiling/staging/benchmark-content-diagnose/"
FROZEN_ROW_FIELDS = (
    "内容ID", "平台", "账号ID", "标题", "媒介形式", "内容目录路径",
    "视频文案路径", "封面图路径", "点赞数", "收藏数", "分享数", "评论数",
    "核心互动数", "数据水平", "对外页面链接",
)
BODY_EMPTY_MARKER = "正文状态：原内容确认未填写文字正文。"


def _expected_key_data(row: dict[str, str]) -> str:
    labels = (("点赞", "点赞数"), ("收藏", "收藏数"), ("分享", "分享数"), ("评论", "评论数"))
    return " / ".join(f"{label} {row.get(field, '').strip() or '缺失'}" for label, field in labels)


def _body_status(path: Path) -> str:
    try:
        text = path.read_text(encoding="utf-8-sig").strip()
    except (OSError, UnicodeError) as exc:
        raise ContentAnalysisFinalizeError("analysis_transcript_unreadable") from exc
    if not text:
        raise ContentAnalysisFinalizeError("analysis_transcript_empty")
    return "确认无正文" if text == BODY_EMPTY_MARKER else "已获取"


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _load_json_file(root: Path, raw_path: str | Path, label: str) -> tuple[Path, dict[str, Any]]:
    path = resolve_under(root, str(raw_path), required_prefix=ANALYSIS_STAGING_PREFIX)
    if not path.is_file():
        raise ContentAnalysisFinalizeError(f"{label}_missing")
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ContentAnalysisFinalizeError(f"{label}_invalid") from exc
    if not isinstance(payload, dict):
        raise ContentAnalysisFinalizeError(f"{label}_invalid")
    return path, payload


def _load_feature_decision_state(
    root: Path,
    raw_path: str | Path,
    *,
    run_dir: Path,
    run_state: dict[str, str],
    catalog: dict[str, dict[str, object]],
) -> dict[str, Any]:
    path, payload = _load_json_file(root, raw_path, "feature_decision_state")
    expected_fields = {
        "schema_version", "run_id", "feature_candidate_path", "analysis_version",
        "projected_core_features", "decisions",
    }
    if set(payload) != expected_fields or payload.get("schema_version") != "1":
        raise ContentAnalysisFinalizeError("feature_decision_state_contract_invalid")
    if path.parent != run_dir or str(payload.get("run_id") or "") != run_state["run_id"]:
        raise ContentAnalysisFinalizeError("feature_decision_state_run_mismatch")
    if (
        str(payload.get("feature_candidate_path") or "")
        != run_state["feature_candidate_path"]
        or str(payload.get("analysis_version") or "") != run_state["analysis_version"]
    ):
        raise ContentAnalysisFinalizeError("feature_decision_state_source_mismatch")
    source_candidate = resolve_under(root, run_state["feature_candidate_path"])
    if not source_candidate.is_file():
        raise ContentAnalysisFinalizeError("feature_decision_state_source_changed")
    decisions = payload.get("decisions")
    projected = payload.get("projected_core_features")
    if not isinstance(decisions, dict) or not decisions or not isinstance(projected, list):
        raise ContentAnalysisFinalizeError("feature_decision_state_incomplete")
    seen: set[str] = set()
    prepared: list[dict[str, str]] = []
    for item in projected:
        if not isinstance(item, dict) or set(item) != {"feature_id", "layer"}:
            raise ContentAnalysisFinalizeError("feature_decision_projected_core_invalid")
        feature_id = str(item.get("feature_id") or "").strip()
        layer = str(item.get("layer") or "").strip()
        definition = catalog.get(feature_id)
        if (
            not feature_id
            or feature_id in seen
            or definition is None
            or str(definition.get("layer") or "") != layer
        ):
            raise ContentAnalysisFinalizeError("feature_decision_projected_core_invalid")
        seen.add(feature_id)
        prepared.append({"feature_id": feature_id, "layer": layer})
    payload["projected_core_features"] = prepared
    return payload


def _locate_row(
    rows: list[dict[str, str]], content_id: str, platform: str
) -> dict[str, str]:
    matches = [
        row for row in rows
        if row.get("内容ID", "").strip() == content_id
        and (not platform or row.get("平台", "").strip() == platform)
    ]
    if len(matches) != 1:
        raise ContentAnalysisFinalizeError(f"content_match_count:{len(matches)}")
    return matches[0]


def _verify_snapshot_file(
    root: Path,
    *,
    label: str,
    item: object,
) -> None:
    if not isinstance(item, dict):
        raise ContentAnalysisFinalizeError(f"analysis_input_{label}_snapshot_missing")
    raw_path = str(item.get("path") or "").strip()
    expected = str(item.get("sha256") or "").strip()
    # The library is shared by many contents. Its full-byte hash is retained
    # for audit, while the selected row is checked by the semantic fingerprint.
    if label == "content_library":
        return
    if label == "feature_catalog":
        path = resolve_under(root, "沉淀结论/内容特征词典.csv")
        if resolve_under(root, raw_path) != path:
            raise ContentAnalysisFinalizeError("analysis_input_feature_catalog_path_mismatch")
    else:
        path = resolve_under(root, raw_path)
    expected_exists = item.get("exists", True)
    if expected_exists is False:
        if path.exists():
            raise ContentAnalysisFinalizeError(f"stale_analysis_input:{label}")
        return
    if expected_exists is not True:
        raise ContentAnalysisFinalizeError(f"analysis_input_{label}_snapshot_invalid")
    if not path.is_file() or not expected or _sha256_file(path) != expected:
        raise ContentAnalysisFinalizeError(f"stale_analysis_input:{label}")


def _snapshot_expected_original(root: Path, *, label: str, item: object) -> bytes | None:
    if not isinstance(item, dict):
        raise ContentAnalysisFinalizeError(f"analysis_input_{label}_snapshot_missing")
    path = resolve_under(root, str(item.get("path") or ""))
    if item.get("exists", True) is False:
        if path.exists():
            raise ContentAnalysisFinalizeError(f"stale_analysis_input:{label}")
        return None
    try:
        payload = path.read_bytes()
    except OSError as exc:
        raise ContentAnalysisFinalizeError(f"stale_analysis_input:{label}") from exc
    expected = str(item.get("sha256") or "").strip()
    if not expected or hashlib.sha256(payload).hexdigest() != expected:
        raise ContentAnalysisFinalizeError(f"stale_analysis_input:{label}")
    return payload


_ROW_FINGERPRINT_EXCLUDED = {
    "内容分析路径",
    "备注",
}


def _content_row_fingerprint(row: dict[str, str]) -> str:
    """本行「事实列」的规范化指纹，用于跨阶段判断本行是否被改动。

    内容库是整表重写的，任何一行变化都会让整表字节哈希改变。并发场景下用整表
    哈希会误伤：别的会话写了别的内容，本行其实没被动过。这里只锁本行。
    指纹在写入前计算，并排除下游产物指针与人工备注列，因此本流程自己写
    「内容分析路径」不会导致自身校验失败。
    """
    components = {
        str(key): str(value or "")
        for key, value in row.items()
        if str(key) not in _ROW_FINGERPRINT_EXCLUDED
    }
    encoded = json.dumps(
        components, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _load_verified_analysis_input(
    root: Path,
    *,
    analysis_input_file: str | Path,
    content_id: str,
    platform: str,
    stage: str,
    receipt: dict[str, Any] | None = None,
    committed_replay: bool = False,
) -> tuple[Path, dict[str, Any], list[str], list[dict[str, str]], bytes, dict[str, str]]:
    input_path, payload = _load_json_file(root, analysis_input_file, "analysis_input")
    if payload.get("status") != "checked":
        raise ContentAnalysisFinalizeError("analysis_input_not_checked")
    if payload.get("analysis_ready") is not True:
        raise ContentAnalysisFinalizeError("analysis_input_not_ready")
    if payload.get("objective_checks_passed") is not True:
        raise ContentAnalysisFinalizeError("analysis_input_checks_failed")
    content = payload.get("content")
    if not isinstance(content, dict):
        raise ContentAnalysisFinalizeError("analysis_input_content_missing")
    if str(content.get("content_id") or "").strip() != content_id:
        raise ContentAnalysisFinalizeError("analysis_input_content_id_mismatch")
    input_platform = str(content.get("platform") or "").strip()
    if platform and input_platform != platform:
        raise ContentAnalysisFinalizeError("analysis_input_platform_mismatch")

    snapshot = payload.get("input_snapshot")
    files = snapshot.get("files") if isinstance(snapshot, dict) else None
    if not isinstance(files, dict):
        raise ContentAnalysisFinalizeError("analysis_input_snapshot_missing")
    required_files = {
        "content_library", "feature_catalog", "formal_analysis",
    }
    if not required_files <= set(files):
        missing = sorted(required_files - set(files))
        raise ContentAnalysisFinalizeError(f"analysis_input_snapshot_files_missing:{missing}")
    frozen = content.get("frozen_row")
    if not isinstance(frozen, dict):
        raise ContentAnalysisFinalizeError("analysis_input_frozen_row_missing")
    frozen_content_dir = infer_content_dir(root, {str(key): str(value or "") for key, value in frozen.items()})
    expected_paths = {
        "content_library": CONTENT_LIBRARY.as_posix(),
        "transcript": str(content.get("frozen_row", {}).get("视频文案路径") or "").strip()
        if isinstance(content.get("frozen_row"), dict) else "",
        "cover": str(content.get("frozen_row", {}).get("封面图路径") or "").strip()
        if isinstance(content.get("frozen_row"), dict) else "",
        "formal_analysis": relative_path(root, frozen_content_dir / "内容分析.md"),
    }
    for label, expected_path in expected_paths.items():
        item = files.get(label)
        if item is None:
            # 封面、正文、评论都可以缺席：缺正文时按有限报告继续，缺评论时评论章节写证据不足。
            if label in {"cover", "transcript", "comments"}:
                continue
            raise ContentAnalysisFinalizeError(f"analysis_input_{label}_snapshot_missing")
        if not str(expected_path or "").strip() and label in {"transcript", "cover"}:
            continue
        if not isinstance(item, dict) or str(item.get("path") or "").strip() != expected_path:
            raise ContentAnalysisFinalizeError(f"analysis_input_{label}_path_mismatch")
    # 这两份在 features 阶段必然已与快照不同 —— `content_library` 由 analysis 阶段
    # 同步过路径、`formal_analysis` 由 analysis 阶段刚写入 —— 所以不核它们的快照哈希。
    # （卡退役后本阶段自己不再产出正式文件，这里列的是上游产物。）
    feature_stage_outputs = {"content_library", "formal_analysis"}
    replay_outputs = {"content_library", "formal_analysis"}
    for label, item in files.items():
        if (
            (stage == "features" and label in feature_stage_outputs)
            or (committed_replay and label in replay_outputs)
        ):
            continue
        _verify_snapshot_file(root, label=label, item=item)

    library_path = root / CONTENT_LIBRARY
    fields, rows, library_snapshot = read_csv_table_with_snapshot(library_path)
    required_fields = {
        "内容ID", "平台", "账号ID", "标题", "媒介形式", "内容分析路径",
    }
    missing = sorted(required_fields - set(fields))
    if missing:
        raise ContentAnalysisFinalizeError(f"content_library_missing_fields:{missing}")
    row = _locate_row(rows, content_id, platform)
    for field in FROZEN_ROW_FIELDS:
        if str(row.get(field) or "").strip() != str(frozen.get(field) or "").strip():
            raise ContentAnalysisFinalizeError(f"stale_analysis_input:content_row:{field}")

    semantic = snapshot.get("semantic_fingerprint")
    if not isinstance(semantic, dict) or semantic.get("version") != "1":
        raise ContentAnalysisFinalizeError("analysis_input_semantic_fingerprint_missing")
    components = semantic.get("components")
    if not isinstance(components, dict):
        raise ContentAnalysisFinalizeError("analysis_input_semantic_fingerprint_invalid")
    current_files: dict[str, dict[str, object]] = {}
    for label, item in files.items():
        if not isinstance(item, dict):
            continue
        raw = str(item.get("path") or "").strip()
        if not raw:
            continue
        path = resolve_under(root, raw)
        current_files[label] = {
            "path": raw,
            "exists": path.is_file(),
            "sha256": _sha256_file(path) if path.is_file() else "",
        }
    current_components = {
        "content_row": {field: str(row.get(field) or "") for field in FROZEN_ROW_FIELDS},
        "files": {
            label: current_files[label]
            for label in sorted(current_files)
            if label in {"transcript", "cover", "feature_catalog"}
        },
    }
    encoded = json.dumps(current_components, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    current_sha = hashlib.sha256(encoded).hexdigest()
    if str(semantic.get("sha256") or "") != current_sha or components != current_components:
        raise ContentAnalysisFinalizeError("stale_analysis_input:semantic_evidence")

    expectations = payload.get("candidate_expectations")
    if not isinstance(expectations, dict):
        raise ContentAnalysisFinalizeError("analysis_input_candidate_expectations_missing")
    transcript_raw = str(row.get("视频文案路径", "") or "").strip()
    actual_body_status = _body_status(resolve_under(root, transcript_raw)) if transcript_raw else ""
    actual_expectations = {
        "正文获取状态": actual_body_status,
        "关键数据": _expected_key_data(row),
        "数据水平": row.get("数据水平", "").strip() or "缺失",
    }
    for label, expected_value in actual_expectations.items():
        if str(expectations.get(label) or "").strip() != expected_value:
            raise ContentAnalysisFinalizeError(f"analysis_input_expectation_mismatch:{label}")

    if stage == "features":
        if not isinstance(receipt, dict):
            raise ContentAnalysisFinalizeError("analysis_receipt_missing")
        expected_input_hash = str(receipt.get("analysis_input_sha256") or "")
        if expected_input_hash != _sha256_file(input_path):
            raise ContentAnalysisFinalizeError("analysis_receipt_input_mismatch")
        if not committed_replay:
            expected_row_hash = str(receipt.get("content_row_sha256") or "")
            if expected_row_hash:
                # 只比目标行。别人改了同一张表的其他行，不影响本行分析继续提交。
                if _content_row_fingerprint(row) != expected_row_hash:
                    raise ContentAnalysisFinalizeError(
                        "content_library_changed_after_analysis_commit"
                    )
            else:
                # 旧回执没有行级指纹，退回整表校验，保持向后兼容。
                expected_library_hash = str(receipt.get("content_library_sha256") or "")
                if (
                    not expected_library_hash
                    or hashlib.sha256(library_snapshot).hexdigest() != expected_library_hash
                ):
                    raise ContentAnalysisFinalizeError(
                        "content_library_changed_after_analysis_commit"
                    )
    return input_path, payload, fields, rows, library_snapshot, row


def _validated_content_dir(root: Path, row: dict[str, str]) -> Path:
    content_dir = infer_content_dir(root, row)
    try:
        content_dir.relative_to((root / "对标资料" / "对标内容").resolve())
    except ValueError as exc:
        raise ContentAnalysisFinalizeError("content_dir_outside_benchmark_content") from exc
    return content_dir


def _add_account_sync_payload(
    payloads: dict[Path, bytes],
    expected_originals: dict[Path, bytes | None],
    account_sync: object,
) -> None:
    if getattr(account_sync, "status", "") != "synced":
        return
    path = getattr(account_sync, "path", None)
    fields = getattr(account_sync, "fields", None)
    rows = getattr(account_sync, "rows", None)
    if path is None or fields is None or rows is None:
        raise ContentAnalysisFinalizeError("account_sync_payload_incomplete")
    payloads[path] = serialize_csv(fields, rows)
    expected_originals[path] = getattr(account_sync, "snapshot", None)


def _verify_library_path(
    library_path: Path,
    *,
    content_id: str,
    field: str,
    expected: str,
) -> dict[str, str]:
    _, rows, _ = read_csv_table_with_snapshot(library_path)
    matches = [row for row in rows if row.get("内容ID", "").strip() == content_id]
    if len(matches) != 1:
        raise ContentAnalysisFinalizeError("content_library_post_commit_match_failed")
    if matches[0].get(field, "").strip() != expected:
        raise ContentAnalysisFinalizeError(f"{field}_post_commit_mismatch")
    return matches[0]


def finalize_analysis(
    workspace_root: str | Path,
    *,
    content_id: str,
    platform: str = "",
    analysis_candidate: str | Path,
    analysis_input_file: str | Path,
    receipt_file: str | Path,
    validate_only: bool = False,
) -> dict[str, object]:
    root = Path(workspace_root).expanduser().resolve()
    if not root.is_dir():
        raise ContentAnalysisFinalizeError(f"workspace_root_invalid:{root}")
    run_dir, run_state, _ = bind_run_paths(
        root, (analysis_input_file, analysis_candidate, receipt_file)
    )
    requested_receipt = resolve_under(
        root, str(receipt_file), required_prefix=ANALYSIS_STAGING_PREFIX
    )
    committed_replay = not validate_only and run_state["last_stage"] in {
        "analysis_committed", "features_committed", "completed",
    }
    if (
        run_state["last_stage"] == "features_committed"
        and run_state["feature_candidate_status"] == "pending"
        and not requested_receipt.is_file()
    ):
        committed_replay = False
    input_path, analysis_input, fields, rows, library_snapshot, row = _load_verified_analysis_input(
        root,
        analysis_input_file=analysis_input_file,
        content_id=content_id,
        platform=platform,
        stage="analysis",
        committed_replay=committed_replay,
    )
    verify_snapshot_run_binding(
        analysis_input,
        input_path=input_path,
        run_dir=run_dir,
        state=run_state,
    )
    if run_state["content_id"] != content_id or (
        platform and run_state["platform"] != platform
    ):
        raise ContentAnalysisFinalizeError("run_state_content_identity_mismatch")
    candidate = resolve_under(root, str(analysis_candidate), required_prefix=ANALYSIS_STAGING_PREFIX)
    markdown, _candidate_payload = decode_markdown(candidate)
    # 内容分析不再做结构 / 值域校验：候选写成什么样就存什么样。
    # 只保留「主目标侧重」的既有固化口径（对标内容无作者目标可查证，一律「未知」）。
    content_task = BENCHMARK_CONTENT_TASK
    try:
        candidate_task = extract_field(markdown, "主目标侧重", allow_blank=True)
        if candidate_task in CONTENT_TASKS:
            content_task = candidate_task
    except ContentAnalysisFinalizeError:
        pass
    expectations = analysis_input.get("candidate_expectations")
    if not isinstance(expectations, dict):
        raise ContentAnalysisFinalizeError("analysis_input_candidate_expectations_missing")
    markdown = normalize_analysis_markdown(
        markdown,
        content_id=content_id,
        row=row,
        expectations=expectations,
    )
    analysis_payload = markdown.encode("utf-8")

    content_dir = _validated_content_dir(root, row)
    formal_analysis = content_dir / "内容分析.md"
    analysis_path = relative_path(root, formal_analysis)
    receipt_path = requested_receipt
    analysis_sha = hashlib.sha256(analysis_payload).hexdigest()
    if committed_replay:
        _, committed_receipt = _load_analysis_receipt(
            root, receipt_path, content_id=content_id, platform=platform
        )
        replay_bindings = {
            "run_id": run_state["run_id"],
            "staging_dir": run_state["staging_dir"],
            "analysis_input_path": relative_path(root, input_path),
            "analysis_input_sha256": _sha256_file(input_path),
            "analysis_path": analysis_path,
            "analysis_sha256": analysis_sha,
        }
        if (
            any(str(committed_receipt.get(key) or "") != value for key, value in replay_bindings.items())
            or run_state["analysis_path"] != analysis_path
            or run_state["analysis_sha256"] != analysis_sha
            or row.get("内容分析路径", "").strip() != analysis_path
            or not formal_analysis.is_file()
            or _sha256_file(formal_analysis) != analysis_sha
        ):
            raise ContentAnalysisFinalizeError("analysis_commit_replay_mismatch")
        account_sync = prepare_account_content_list_sync(
            root, row, required_sync_fields=("内容分析路径",)
        )
        verify_account_content_list_sync(
            account_sync, row, required_sync_fields=("内容分析路径",)
        )
        return {
            **committed_receipt,
            "analysis_receipt_path": relative_path(root, receipt_path),
            "account_content_list_sync_status": account_sync.status,
            "features_handoff": {
                "status": "ready_after_analysis_commit",
                "analysis_path": analysis_path,
                "analysis_sha256": analysis_sha,
            },
            "reused": True,
        }

    row["内容分析路径"] = analysis_path
    library_payload = serialize_csv(fields, rows)
    library_path = root / CONTENT_LIBRARY
    account_sync = prepare_account_content_list_sync(
        root, row, required_sync_fields=("内容分析路径",)
    )
    receipt = {
        "schema_version": "1",
        "ok": True,
        "status": "analysis_committed",
        "content_id": content_id,
        "platform": row.get("平台", "").strip(),
        "run_id": run_state["run_id"],
        "staging_dir": run_state["staging_dir"],
        "analysis_input_path": relative_path(root, input_path),
        "analysis_input_sha256": _sha256_file(input_path),
        "analysis_path": analysis_path,
        "analysis_sha256": analysis_sha,
        # Retain a legacy internal task for downstream pattern joins. The
        # reader-facing report is normalized to 主目标侧重=未知.
        "content_task": content_task if content_task in CONTENT_TASKS else BENCHMARK_CONTENT_TASK,
        "content_library_sha256": hashlib.sha256(library_payload).hexdigest(),
        # 目标行指纹：特征卡阶段据此判断「本行」而不是「整表」有没有被改动。
        "content_row_sha256": _content_row_fingerprint(row),
    }
    result: dict[str, object] = {
        **receipt,
        "status": "analysis_validated" if validate_only else "analysis_committed",
        "analysis_receipt_path": relative_path(root, receipt_path),
        "account_content_list_sync_status": account_sync.status,
        "features_handoff": {
            "status": "ready_after_analysis_commit" if not validate_only else "validation_only",
            "analysis_path": analysis_path,
            "analysis_sha256": analysis_sha,
        },
        "reused": False,
    }
    if validate_only:
        return result

    receipt_payload = (json.dumps(receipt, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    payloads = {
        formal_analysis: analysis_payload,
        library_path: library_payload,
        receipt_path: receipt_payload,
    }
    snapshot_files = analysis_input["input_snapshot"]["files"]
    expected_originals: dict[Path, bytes | None] = {
        library_path: library_snapshot,
        formal_analysis: _snapshot_expected_original(
            root, label="formal_analysis", item=snapshot_files["formal_analysis"]
        ),
    }
    state_path, state_original, state_payload, _ = prepare_run_state_update(
        root,
        run_dir=run_dir,
        updates={
            "last_stage": "analysis_committed",
            "analysis_input_path": relative_path(root, input_path),
            "analysis_path": analysis_path,
            "analysis_sha256": analysis_sha,
            "asset_processing_receipt_path": "",
        },
    )
    payloads[state_path] = state_payload
    expected_originals[state_path] = state_original
    _add_account_sync_payload(payloads, expected_originals, account_sync)
    commit_payloads_atomically(payloads, expected_originals=expected_originals)
    verified = _verify_library_path(
        library_path,
        content_id=content_id,
        field="内容分析路径",
        expected=analysis_path,
    )
    verify_account_content_list_sync(
        account_sync, verified, required_sync_fields=("内容分析路径",)
    )
    if _sha256_file(formal_analysis) != analysis_sha:
        raise ContentAnalysisFinalizeError("analysis_hash_post_commit_mismatch")
    if _sha256_file(receipt_path) != hashlib.sha256(receipt_payload).hexdigest():
        raise ContentAnalysisFinalizeError("analysis_receipt_post_commit_mismatch")
    return result


def _load_analysis_receipt(
    root: Path,
    raw_path: str | Path,
    *,
    content_id: str,
    platform: str,
) -> tuple[Path, dict[str, Any]]:
    path, receipt = _load_json_file(root, raw_path, "analysis_receipt")
    if receipt.get("status") != "analysis_committed":
        raise ContentAnalysisFinalizeError("analysis_receipt_not_committed")
    if str(receipt.get("content_id") or "").strip() != content_id:
        raise ContentAnalysisFinalizeError("analysis_receipt_content_id_mismatch")
    if platform and str(receipt.get("platform") or "").strip() != platform:
        raise ContentAnalysisFinalizeError("analysis_receipt_platform_mismatch")
    return path, receipt
def normalize_analysis_markdown(
    markdown: str,
    *,
    content_id: str,
    row: dict[str, str],
    expectations: dict[str, object],
) -> str:
    """Rewrite deterministic fields while preserving model-authored analysis text."""
    fixed = {
        "内容ID": content_id,
        "标题": row.get("标题", "").strip(),
        "平台": row.get("平台", "").strip(),
        "媒介形式": row.get("媒介形式", "").strip(),
        "正文获取状态": str(expectations.get("正文获取状态") or "").strip(),
        "关键数据": str(expectations.get("关键数据") or "").strip(),
        "数据水平": str(expectations.get("数据水平") or "").strip(),
    }
    for label, value in fixed.items():
        line = f"- {label}：{value}"
        markdown = replace_field_in_section(markdown, label, line, "1. 基础信息")

    target_pattern = r"(?m)^-\s*主目标侧重\s*[：:].*$"
    markdown = re.sub(target_pattern, "", markdown)
    # 只按节名匹配、不写死节号 —— 内容分析的子节会随层级调整顺延（机制层已删除，
    # 策略层从 2.4 变成 2.3），写死节号会静默失配、把「主目标侧重」整行删掉又补不回来。
    strategy = re.search(r"(?m)^###\s+\d+(?:\.\d+)?\s*策略层\s*$", markdown)
    if strategy:
        markdown = markdown[:strategy.end()] + "\n- 主目标侧重：未知" + markdown[strategy.end():]

    return markdown
def finalize_features(
    workspace_root: str | Path,
    *,
    content_id: str,
    platform: str = "",
    feature_candidate: str | Path,
    analysis_input_file: str | Path,
    analysis_receipt_file: str | Path,
    decision_state_file: str | Path = "",
    validate_only: bool = False,
) -> dict[str, object]:
    root = Path(workspace_root).expanduser().resolve()
    if not root.is_dir():
        raise ContentAnalysisFinalizeError(f"workspace_root_invalid:{root}")
    bound_paths: list[str | Path] = [
        analysis_input_file, analysis_receipt_file, feature_candidate,
    ]
    if decision_state_file:
        bound_paths.append(decision_state_file)
    run_dir, run_state, _ = bind_run_paths(root, bound_paths)
    committed_replay = not validate_only and run_state["last_stage"] in {
        "features_committed", "completed",
    }
    if (
        run_state["feature_candidate_status"] in {"pending", "resolved"}
        and not decision_state_file
        and not (
            committed_replay and run_state["feature_candidate_status"] == "pending"
        )
    ):
        raise ContentAnalysisFinalizeError("feature_decision_state_required")
    receipt_path, receipt = _load_analysis_receipt(
        root, analysis_receipt_file, content_id=content_id, platform=platform
    )
    input_path, analysis_input, fields, rows, library_snapshot, row = _load_verified_analysis_input(
        root,
        analysis_input_file=analysis_input_file,
        content_id=content_id,
        platform=platform,
        stage="features",
        receipt=receipt,
        committed_replay=committed_replay,
    )
    verify_snapshot_run_binding(
        analysis_input,
        input_path=input_path,
        run_dir=run_dir,
        state=run_state,
    )
    if (
        str(receipt.get("run_id") or "") != run_state["run_id"]
        or str(receipt.get("staging_dir") or "") != run_state["staging_dir"]
        or receipt_path.parent != run_dir
    ):
        raise ContentAnalysisFinalizeError("analysis_receipt_run_binding_mismatch")
    if run_state["content_id"] != content_id or (
        platform and run_state["platform"] != platform
    ):
        raise ContentAnalysisFinalizeError("run_state_content_identity_mismatch")
    formal_analysis = resolve_under(root, str(receipt.get("analysis_path") or ""))
    # 特征卡不再读取内容分析正文，也不再校验它的哈希：内容分析怎么写、事后改没改，都不影响本步。
    # 回执照原样带上 analysis_sha256（取自 2.3 的回执），仅供下游标识版本。
    expected_analysis_sha = str(receipt.get("analysis_sha256") or "")
    candidate = resolve_under(root, str(feature_candidate), required_prefix=ANALYSIS_STAGING_PREFIX)
    candidate_markdown, _ = decode_markdown(candidate)
    expectations = analysis_input.get("candidate_expectations")
    if not isinstance(expectations, dict):
        raise ContentAnalysisFinalizeError("analysis_input_candidate_expectations_missing")
    core_features, candidate_features, uncovered, candidate_status = (
        validate_feature_judgment_candidate(candidate_markdown, root=root)
    )
    candidate_rel = relative_path(root, candidate)
    # 分析版本 = **特征集合的规范化 sha256**（核心 + 候补），与写入器同一份实现。
    # 卡退役后不再有「卡文件 sha」这个来源；本步算好存进 run state，2.4.3 逐字复用。
    analysis_version = analysis_version_from_feature_set(
        [
            (str(item["feature_id"]), str(item["evidence"]), str(item["location"]))
            for item in [*core_features, *candidate_features]
        ]
    )
    feature_count = len(core_features)
    catalog = load_feature_catalog(root)
    committed_candidate_status = (
        run_state["feature_candidate_status"] if committed_replay else candidate_status
    )
    if decision_state_file and not committed_replay:
        if run_state["feature_candidate_status"] != "pending":
            raise ContentAnalysisFinalizeError("feature_decision_state_not_expected")
        if candidate_status != "none":
            raise ContentAnalysisFinalizeError("final_feature_candidates_remaining")
        decision_state = _load_feature_decision_state(
            root,
            decision_state_file,
            run_dir=run_dir,
            run_state=run_state,
            catalog=catalog,
        )
        actual = {(item["feature_id"], item["layer"]) for item in core_features}
        expected = {
            (item["feature_id"], item["layer"])
            for item in decision_state["projected_core_features"]
        }
        if actual != expected:
            raise ContentAnalysisFinalizeError("final_core_features_mismatch_decisions")
        committed_candidate_status = "resolved"
    # 卡退役后本阶段**不再渲染、不再写任何正式文件**：候选本身就是结构化的，
    # 渲染成卡再由写入器解析回来是一次纯往返。这里只做两件事：
    #   ① 校验候选（标题 / 三节 / 表头 / 每小类名额 / 特征 ID）
    #   ② 把「候选路径 + 分析版本」记进 run state，交给 2.4.3 落两张表
    if committed_replay:
        replay_matches = not (
            run_state["feature_candidate_path"] != candidate_rel
            or run_state["analysis_version"] != analysis_version
            or run_state["feature_candidate_status"] != committed_candidate_status
        )
        if not replay_matches:
            if (
                run_state["feature_candidate_status"] == "pending"
                and not decision_state_file
            ):
                raise ContentAnalysisFinalizeError("feature_decision_state_required")
            raise ContentAnalysisFinalizeError("feature_candidate_commit_replay_mismatch")
        return {
            "ok": True,
            "status": "features_committed",
            "content_id": content_id,
            "platform": row.get("平台", "").strip(),
            "run_id": run_state["run_id"],
            "staging_dir": run_state["staging_dir"],
            "analysis_input_path": relative_path(root, input_path),
            "analysis_input_sha256": _sha256_file(input_path),
            "analysis_path": relative_path(root, formal_analysis),
            "analysis_sha256": expected_analysis_sha,
            "feature_candidate_path": candidate_rel,
            "analysis_version": analysis_version,
            "core_feature_count": feature_count,
            "feature_candidate_status": committed_candidate_status,
            "asset_handoff": {
                "status": "ready",
                "source": "对标内容",
                "content_id": content_id,
                "platform": row.get("平台", "").strip(),
                "analysis_path": relative_path(root, formal_analysis),
                "feature_candidate_path": candidate_rel,
                "analysis_version": analysis_version,
            },
            "reused": True,
        }

    result: dict[str, object] = {
        "ok": True,
        "status": "features_validated" if validate_only else "features_committed",
        "content_id": content_id,
        "platform": row.get("平台", "").strip(),
        "run_id": run_state["run_id"],
        "staging_dir": run_state["staging_dir"],
        "analysis_input_path": relative_path(root, input_path),
        "analysis_input_sha256": _sha256_file(input_path),
        "analysis_path": relative_path(root, formal_analysis),
        "analysis_sha256": expected_analysis_sha,
        "feature_candidate_path": candidate_rel,
        "analysis_version": analysis_version,
        "core_feature_count": feature_count,
        "feature_candidate_status": committed_candidate_status,
        "asset_handoff": {
            "status": "ready",
            "source": "对标内容",
            "content_id": content_id,
            "platform": row.get("平台", "").strip(),
            "analysis_path": relative_path(root, formal_analysis),
            "feature_candidate_path": candidate_rel,
            "analysis_version": analysis_version,
        },
        "reused": False,
    }
    if validate_only:
        return result

    state_path, state_original, state_payload, _ = prepare_run_state_update(
        root,
        run_dir=run_dir,
        updates={
            "last_stage": "features_committed",
            "analysis_input_path": relative_path(root, input_path),
            "analysis_path": relative_path(root, formal_analysis),
            "analysis_sha256": expected_analysis_sha,
            "feature_candidate_path": candidate_rel,
            "analysis_version": analysis_version,
            "feature_candidate_status": committed_candidate_status,
            "asset_processing_receipt_path": "",
        },
    )
    # 本阶段唯一的写入就是 run state（候选文件由模型写成、已在暂存目录里）。
    commit_payloads_atomically(
        {state_path: state_payload},
        expected_originals={state_path: state_original},
    )
    return result


def run(args: argparse.Namespace) -> dict[str, object]:
    if args.stage == "analysis":
        return finalize_analysis(
            args.workspace_root,
            content_id=args.content_id,
            platform=args.platform,
            analysis_candidate=args.analysis_candidate,
            analysis_input_file=args.analysis_input_file,
            receipt_file=args.receipt_file,
            validate_only=args.validate_only,
        )
    return finalize_features(
        args.workspace_root,
        content_id=args.content_id,
        platform=args.platform,
        feature_candidate=args.feature_candidate,
        analysis_input_file=args.analysis_input_file,
        analysis_receipt_file=args.analysis_receipt_file,
        decision_state_file=args.decision_state_file,
        validate_only=args.validate_only,
    )


def _has_committed_analysis(args: argparse.Namespace) -> bool:
    try:
        root = Path(args.workspace_root).expanduser().resolve()
        _, receipt = _load_analysis_receipt(
            root,
            args.analysis_receipt_file,
            content_id=args.content_id,
            platform=args.platform,
        )
        formal = resolve_under(root, str(receipt.get("analysis_path") or ""))
        expected = str(receipt.get("analysis_sha256") or "")
        return formal.is_file() and bool(expected) and _sha256_file(formal) == expected
    except (
        AccountContentListSyncError,
        BenchmarkContentRunStateError,
        ContentAnalysisFinalizeError,
        OSError,
        ValueError,
    ):
        return False


def main() -> int:
    args = parse_args()
    try:
        result = run(args)
    except (
        AccountContentListSyncError,
        BenchmarkContentRunStateError,
        ContentAnalysisFinalizeError,
        OSError,
        ValueError,
    ) as exc:
        partial = args.stage == "features" and _has_committed_analysis(args)
        error_result: dict[str, object] = {
            "ok": False,
            "status": "partially_committed" if partial else "failed",
            "analysis_status": "committed" if partial else "not_committed",
            "feature_status": "failed" if partial else "not_started",
            "error": str(exc),
        }
        if partial:
            error_result["asset_handoff"] = {"status": "blocked", "reason": str(exc)}
        print(json.dumps(error_result, ensure_ascii=False))
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
