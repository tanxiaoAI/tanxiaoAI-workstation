#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import re
import sys
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any

class SelfAccountDiagnosisFinalizeError(RuntimeError):
    pass


SUMMARY_START = "<!-- self-account-diagnosis-summary:start -->"
SUMMARY_END = "<!-- self-account-diagnosis-summary:end -->"
SUMMARY_VERSION = "self-account-diagnosis-v2"
REQUIRED_H2 = ("综合诊断", "目标与实际", "这段时间的变化", "关键问题", "本轮依据与边界")
TARGET_DIMENSIONS = ("目标用户", "账号定位", "内容主线")
STAGE_CHANGE_FIELDS = ("方面", "结论", "文案")
STAGE_CHANGE_VERDICTS = {"变好", "变差", "没变", "仍然差", "无法比较"}
BOUNDARY_FIELDS = (
    "目标账号",
    "判断基准",
    "已获取内容",
    "公开与私密范围",
    "正文级证据",
    "仅基础数据",
    "创作者中心数据",
    "评论文本",
    "历史对照",
    "其他信息边界",
)
ISSUE_FIELDS = ("标题", "类型", "具体表现", "影响", "依据")
FORBIDDEN_SUMMARY_KEYS = ("优先行动", "一句话结论", "账号与内容表现")
PROBLEM_TYPES = {"方向偏差", "执行不足", "证据不足", "方向正确"}
JUDGEMENTS = {"一致", "不一致", "暂无法判断"}
STAGE_STATES = {"首次诊断", "有改善", "没变", "变差", "有好有坏"}


def _resolve_inside(root: Path, value: str | Path, label: str, *, staging: bool = False) -> Path:
    raw_text = str(value or "").strip()
    if not raw_text:
        raise SelfAccountDiagnosisFinalizeError(f"{label}_missing")
    raw = Path(raw_text).expanduser()
    target = (raw if raw.is_absolute() else root / raw).resolve()
    try:
        relative = target.relative_to(root).as_posix()
    except ValueError as exc:
        raise SelfAccountDiagnosisFinalizeError(f"{label}_outside_workspace") from exc
    if staging and not relative.startswith(".meiling/staging/self-account-diagnose/"):
        raise SelfAccountDiagnosisFinalizeError(f"{label}_outside_diagnosis_staging")
    if not target.is_file():
        raise SelfAccountDiagnosisFinalizeError(f"{label}_missing")
    return target


def _read_account(root: Path, account_id: str, platform: str) -> dict[str, str]:
    path = root / "我方资料/我方账号库.csv"
    if not path.is_file():
        raise SelfAccountDiagnosisFinalizeError("account_library_missing")
    try:
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            rows = [dict(row) for row in csv.DictReader(handle)]
    except (OSError, UnicodeError, csv.Error) as exc:
        raise SelfAccountDiagnosisFinalizeError("account_library_unreadable") from exc
    matches = [
        row for row in rows
        if str(row.get("账号ID", "")).strip() == account_id
        and (not platform or str(row.get("平台", "")).strip() == platform)
    ]
    if len(matches) != 1:
        raise SelfAccountDiagnosisFinalizeError(f"account_not_unique:{len(matches)}")
    return {str(key): str(value or "").strip() for key, value in matches[0].items()}


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _next_formal_path(account_dir: Path, timestamp: datetime) -> Path:
    """同日覆盖：每天只保留一份正式诊断，同一天重跑直接覆盖当天文件。"""
    return account_dir / f"{timestamp.year}年{timestamp.month}月{timestamp.day}日账号详细诊断.md"


def _load_diagnosis_input(
    root: Path,
    input_file: str | Path,
    *,
    account_id: str,
    platform: str,
    run_id: str,
    input_sha256: str,
) -> dict[str, Any]:
    target = _resolve_inside(root, input_file, "diagnosis_input", staging=True)
    try:
        payload = json.loads(target.read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise SelfAccountDiagnosisFinalizeError("diagnosis_input_invalid") from exc
    if not isinstance(payload, dict) or payload.get("status") != "checked":
        raise SelfAccountDiagnosisFinalizeError("diagnosis_input_not_checked")
    if payload.get("next_route") != "continue_diagnosis":
        raise SelfAccountDiagnosisFinalizeError("diagnosis_input_route_not_ready")
    payload_run_id = str(payload.get("run_id") or "").strip()
    if not run_id:
        raise SelfAccountDiagnosisFinalizeError("run_id_missing")
    if payload_run_id and payload_run_id != run_id:
        raise SelfAccountDiagnosisFinalizeError("diagnosis_input_run_mismatch")
    actual_input_sha256 = _sha256(target)
    if not re.fullmatch(r"[0-9a-f]{64}", str(input_sha256 or "").strip()):
        raise SelfAccountDiagnosisFinalizeError("input_sha256_invalid")
    if actual_input_sha256 != str(input_sha256).strip().lower():
        raise SelfAccountDiagnosisFinalizeError("diagnosis_input_sha256_mismatch")
    if payload.get("objective_checks_passed") is not True:
        raise SelfAccountDiagnosisFinalizeError("diagnosis_input_checks_failed")

    account = payload.get("account")
    if not isinstance(account, dict):
        raise SelfAccountDiagnosisFinalizeError("diagnosis_input_account_missing")
    input_account_id = str(account.get("账号ID") or account.get("account_id") or "").strip()
    input_platform = str(account.get("平台") or account.get("platform") or "").strip()
    if input_account_id != account_id or (platform and input_platform != platform):
        raise SelfAccountDiagnosisFinalizeError("diagnosis_input_account_mismatch")

    content = payload.get("content")
    if not isinstance(content, dict) or not content.get("selected_content_ids"):
        raise SelfAccountDiagnosisFinalizeError("diagnosis_input_scope_missing")

    snapshot = payload.get("input_snapshot")
    files = snapshot.get("files") if isinstance(snapshot, dict) else None
    if not isinstance(files, dict):
        raise SelfAccountDiagnosisFinalizeError("diagnosis_input_snapshot_missing")
    required_snapshot_labels = ("account_library", "content_library", "content_list")
    for label in required_snapshot_labels:
        item = files.get(label)
        if not isinstance(item, dict):
            raise SelfAccountDiagnosisFinalizeError(f"diagnosis_input_{label}_snapshot_missing")
        path = _resolve_inside(root, item.get("path", ""), label)
        expected = str(item.get("sha256") or "").strip()
        if not expected or _sha256(path) != expected:
            raise SelfAccountDiagnosisFinalizeError(f"stale_diagnosis_input:{label}")
    # 目标基准、规划摘要都进入快照后即成为冻结输入；任何一份变化都视为输入过期。
    for label in ("overview", "planning", "questionnaire"):
        item = files.get(label)
        if item is None:
            continue
        if not isinstance(item, dict):
            raise SelfAccountDiagnosisFinalizeError(f"diagnosis_input_{label}_snapshot_missing")
        path = _resolve_inside(root, item.get("path", ""), label)
        expected = str(item.get("sha256") or "").strip()
        if not expected or _sha256(path) != expected:
            raise SelfAccountDiagnosisFinalizeError(f"stale_diagnosis_input:{label}")
    return payload


def _text(value: Any) -> str:
    return str(value if value is not None else "").strip()


def _heading_text(line: str) -> tuple[int, str]:
    match = re.match(r"^(#{1,6})\s+(.+?)\s*$", line.strip())
    if not match:
        return 0, ""
    title = re.sub(r"^\d+(?:\.\d+)?[.、．]?\s+", "", match.group(2)).strip()
    return len(match.group(1)), title


def _section_lines(text: str) -> dict[str, list[str]]:
    """Return H2 title -> body lines for the candidate body."""
    sections: dict[str, list[str]] = {}
    current = ""
    for line in text.splitlines():
        level, title = _heading_text(line)
        if level == 2:
            current = title
            sections[current] = []
            continue
        if level == 1:
            current = ""
            continue
        if current:
            sections[current].append(line)
    return sections


def _subsection_lines(lines: list[str]) -> dict[str, list[str]]:
    """Return H3 title -> body lines inside one H2 section."""
    subsections: dict[str, list[str]] = {}
    current = ""
    for line in lines:
        level, title = _heading_text(line)
        if level == 3:
            current = title
            subsections[current] = []
            continue
        if level and level < 3:
            current = ""
            continue
        if current:
            subsections[current].append(line)
    return subsections


def _validate_summary_block(
    markdown: str,
    *,
    account_id: str,
    account_name: str,
) -> dict[str, Any]:
    start = markdown.find(SUMMARY_START)
    end = markdown.find(SUMMARY_END)
    if start < 0 or end < 0 or end < start:
        raise SelfAccountDiagnosisFinalizeError("summary_block_missing")
    block = markdown[start + len(SUMMARY_START) : end]
    fence = re.search(r"```json\s*([\s\S]*?)```", block)
    if not fence:
        raise SelfAccountDiagnosisFinalizeError("summary_json_missing")
    try:
        data = json.loads(fence.group(1))
    except json.JSONDecodeError as exc:
        raise SelfAccountDiagnosisFinalizeError("summary_json_invalid") from exc
    if not isinstance(data, dict):
        raise SelfAccountDiagnosisFinalizeError("summary_json_invalid")
    if _text(data.get("版本")) != SUMMARY_VERSION:
        raise SelfAccountDiagnosisFinalizeError("summary_version_invalid")
    if _text(data.get("账号ID")) != account_id:
        raise SelfAccountDiagnosisFinalizeError("summary_account_mismatch")
    if account_name and _text(data.get("账号名")) != account_name:
        raise SelfAccountDiagnosisFinalizeError("summary_account_name_mismatch")
    for key in FORBIDDEN_SUMMARY_KEYS:
        if key in data:
            raise SelfAccountDiagnosisFinalizeError(f"summary_forbidden_key:{key}")

    overall = data.get("综合诊断")
    if not isinstance(overall, dict):
        raise SelfAccountDiagnosisFinalizeError("summary_overall_missing")
    if not _text(overall.get("文本")):
        raise SelfAccountDiagnosisFinalizeError("summary_overall_text_missing")
    if _text(overall.get("问题类型")) not in PROBLEM_TYPES:
        raise SelfAccountDiagnosisFinalizeError("summary_overall_type_invalid")

    targets = data.get("目标与实际")
    if not isinstance(targets, list) or len(targets) != len(TARGET_DIMENSIONS):
        raise SelfAccountDiagnosisFinalizeError("summary_targets_invalid")
    for index, dimension in enumerate(TARGET_DIMENSIONS):
        item = targets[index]
        if not isinstance(item, dict):
            raise SelfAccountDiagnosisFinalizeError("summary_targets_invalid")
        if _text(item.get("维度")) != dimension:
            raise SelfAccountDiagnosisFinalizeError(f"summary_target_dimension_mismatch:{dimension}")
        for field in ("原因", "建议"):
            if not _text(item.get(field)):
                raise SelfAccountDiagnosisFinalizeError(f"summary_target_field_missing:{dimension}.{field}")
        if _text(item.get("结论")) not in JUDGEMENTS:
            raise SelfAccountDiagnosisFinalizeError(f"summary_target_judgement_invalid:{dimension}")

    issues = data.get("关键问题")
    if not isinstance(issues, list):
        raise SelfAccountDiagnosisFinalizeError("summary_issues_invalid")
    if len(issues) > 3:
        raise SelfAccountDiagnosisFinalizeError("summary_issues_too_many")
    for index, item in enumerate(issues):
        if not isinstance(item, dict):
            raise SelfAccountDiagnosisFinalizeError("summary_issues_invalid")
        for field in ISSUE_FIELDS:
            if not _text(item.get(field)):
                raise SelfAccountDiagnosisFinalizeError(f"summary_issue_field_missing:{index + 1}.{field}")
        if _text(item.get("类型")) not in PROBLEM_TYPES - {"方向正确"}:
            raise SelfAccountDiagnosisFinalizeError(f"summary_issue_type_invalid:{index + 1}")

    stage = data.get("这段时间的变化")
    if not isinstance(stage, dict):
        raise SelfAccountDiagnosisFinalizeError("summary_stage_missing")
    state = _text(stage.get("总体"))
    if state not in STAGE_STATES:
        raise SelfAccountDiagnosisFinalizeError("summary_stage_state_invalid")
    if not _text(stage.get("一句话总结")):
        raise SelfAccountDiagnosisFinalizeError("summary_stage_conclusion_missing")
    changes = stage.get("变化")
    if not isinstance(changes, list):
        raise SelfAccountDiagnosisFinalizeError("summary_stage_changes_invalid")
    if state == "首次诊断":
        if changes:
            raise SelfAccountDiagnosisFinalizeError("summary_stage_changes_not_allowed")
    else:
        if not 2 <= len(changes) <= 4:
            raise SelfAccountDiagnosisFinalizeError("summary_stage_changes_count_invalid")
        for index, item in enumerate(changes):
            if not isinstance(item, dict):
                raise SelfAccountDiagnosisFinalizeError(f"summary_stage_change_invalid:{index + 1}")
            for field in STAGE_CHANGE_FIELDS:
                if not _text(item.get(field)):
                    raise SelfAccountDiagnosisFinalizeError(
                        f"summary_stage_change_field_missing:{index + 1}.{field}"
                    )
            if _text(item.get("结论")) not in STAGE_CHANGE_VERDICTS:
                raise SelfAccountDiagnosisFinalizeError(f"summary_stage_change_verdict_invalid:{index + 1}")
    if stage.get("上次行动核对"):
        raise SelfAccountDiagnosisFinalizeError("summary_stage_verification_not_allowed")

    boundary = data.get("本轮依据与边界")
    if not isinstance(boundary, dict):
        raise SelfAccountDiagnosisFinalizeError("summary_boundary_missing")
    for field in BOUNDARY_FIELDS:
        if not _text(boundary.get(field)):
            raise SelfAccountDiagnosisFinalizeError(f"summary_boundary_field_missing:{field}")
    return data


def _validate_candidate(text: str, *, account_id: str, account_name: str) -> str:
    normalized = text.strip() + "\n"
    if not re.search(r"^# 自我账号详细诊断\s*$", normalized, flags=re.MULTILINE):
        raise SelfAccountDiagnosisFinalizeError("candidate_title_invalid")
    if normalized.count(SUMMARY_START) != 1 or normalized.count(SUMMARY_END) != 1:
        raise SelfAccountDiagnosisFinalizeError("summary_block_invalid")
    summary = _validate_summary_block(
        normalized, account_id=account_id, account_name=account_name
    )

    sections = _section_lines(normalized)
    headings = list(sections)
    if headings != list(REQUIRED_H2):
        raise SelfAccountDiagnosisFinalizeError(
            "candidate_sections_invalid:" + "、".join(headings)
        )
    for title, lines in sections.items():
        if not any(line.strip() for line in lines):
            raise SelfAccountDiagnosisFinalizeError(f"candidate_section_empty:{title}")

    target_sub = [
        title for title in (_heading_text(line)[1] for line in sections["目标与实际"] if _heading_text(line)[0] == 3)
    ]
    if target_sub != list(TARGET_DIMENSIONS):
        raise SelfAccountDiagnosisFinalizeError(
            "candidate_target_subsections_invalid:" + "、".join(target_sub)
        )
    if not summary.get("关键问题"):
        body = "".join(sections["关键问题"])
        if "当前未发现需要优先处理的关键问题" not in body:
            raise SelfAccountDiagnosisFinalizeError("candidate_no_issue_statement_missing")

    stage_state = _text(summary["这段时间的变化"].get("总体"))
    stage_subs = _subsection_lines(sections["这段时间的变化"])
    stage_titles = list(stage_subs)
    if stage_state == "首次诊断":
        if stage_titles:
            raise SelfAccountDiagnosisFinalizeError(
                "candidate_stage_subsections_not_allowed:" + "、".join(stage_titles)
            )
    else:
        if not 2 <= len(stage_titles) <= 4:
            raise SelfAccountDiagnosisFinalizeError(
                "candidate_stage_subsections_invalid:" + "、".join(stage_titles)
            )
        change_titles = stage_titles
        if len(change_titles) != len(summary["这段时间的变化"]["变化"]):
            raise SelfAccountDiagnosisFinalizeError("candidate_stage_changes_mismatch")
        for title in change_titles:
            if not title.startswith("变化"):
                raise SelfAccountDiagnosisFinalizeError(f"candidate_stage_change_title_invalid:{title}")
            body = "".join(line.strip() for line in stage_subs[title])
            if not body:
                raise SelfAccountDiagnosisFinalizeError(f"candidate_stage_change_body_missing:{title}")
    return normalized


def _temporary(path: Path, payload: bytes) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    temporary = Path(name)
    with os.fdopen(descriptor, "wb") as handle:
        handle.write(payload)
        handle.flush()
        os.fsync(handle.fileno())
    return temporary


def finalize_diagnosis(
    workspace_root: str | Path,
    *,
    account_id: str,
    platform: str = "",
    candidate_file: str | Path,
    diagnosis_input_file: str | Path,
    run_id: str = "",
    input_sha256: str = "",
    now: datetime | None = None,
) -> dict[str, Any]:
    root = Path(workspace_root).expanduser().resolve()
    if not root.is_dir():
        raise SelfAccountDiagnosisFinalizeError("workspace_root_invalid")
    normalized_id = str(account_id or "").strip()
    if not normalized_id:
        raise SelfAccountDiagnosisFinalizeError("account_id_missing")
    run_id = str(run_id or "").strip()
    if not run_id or Path(run_id).name != run_id or run_id in {".", ".."}:
        raise SelfAccountDiagnosisFinalizeError("run_id_invalid")
    # 只做校验：快照身份、路由与输入哈希不符时在此抛出。
    _load_diagnosis_input(
        root,
        diagnosis_input_file,
        account_id=normalized_id,
        platform=str(platform or "").strip(),
        run_id=run_id,
        input_sha256=str(input_sha256 or "").strip().lower(),
    )
    account = _read_account(root, normalized_id, str(platform or "").strip())
    content_list = _resolve_inside(root, account.get("内容列表路径", ""), "content_list")
    candidate = _resolve_inside(root, candidate_file, "candidate", staging=True)
    text = _validate_candidate(
        candidate.read_text(encoding="utf-8-sig"),
        account_id=normalized_id,
        account_name=str(account.get("账号名") or "").strip(),
    )
    timestamp = now or datetime.now().astimezone()
    account_dir = content_list.parent
    formal = _next_formal_path(account_dir, timestamp)
    payloads = {formal: text.encode("utf-8")}
    snapshots = {path: path.read_bytes() if path.is_file() else None for path in payloads}
    temporaries = {path: _temporary(path, payload) for path, payload in payloads.items()}
    committed: list[Path] = []
    try:
        for target, payload in payloads.items():
            os.replace(temporaries[target], target)
            committed.append(target)
            if target.read_bytes() != payload:
                raise SelfAccountDiagnosisFinalizeError(f"post_write_mismatch:{target.name}")
    except Exception:
        for target in reversed(committed):
            snapshot = snapshots[target]
            if snapshot is None:
                target.unlink(missing_ok=True)
            else:
                os.replace(_temporary(target, snapshot), target)
        raise
    finally:
        for temporary in temporaries.values():
            temporary.unlink(missing_ok=True)
    return {
        "ok": True,
        "status": "committed",
        "formal_path": formal.relative_to(root).as_posix(),
        "formal_sha256": hashlib.sha256(payloads[formal]).hexdigest(),
        "diagnosis_input_path": _resolve_inside(
            root, diagnosis_input_file, "diagnosis_input", staging=True
        ).relative_to(root).as_posix(),
        "run_id": str(run_id).strip(),
        "input_sha256": str(input_sha256).strip().lower(),
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="校验并正式保存自我账号详细诊断")
    parser.add_argument("--workspace-root", required=True)
    parser.add_argument("--account-id", required=True)
    parser.add_argument("--platform", default="")
    parser.add_argument("--candidate-file", required=True)
    parser.add_argument("--diagnosis-input-file", required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--input-sha256", required=True)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        result = finalize_diagnosis(
            args.workspace_root,
            account_id=args.account_id,
            platform=args.platform,
            candidate_file=args.candidate_file,
            diagnosis_input_file=args.diagnosis_input_file,
            run_id=args.run_id,
            input_sha256=args.input_sha256,
        )
    except (OSError, UnicodeError, ValueError, SelfAccountDiagnosisFinalizeError) as exc:
        print(json.dumps({"ok": False, "status": "failed", "error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
