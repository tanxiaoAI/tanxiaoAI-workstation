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
from datetime import date
from pathlib import Path
from typing import Any

_EXECUTION_UNIT_DIR = Path(__file__).resolve().parent
if str(_EXECUTION_UNIT_DIR) not in sys.path:
    sys.path.insert(0, str(_EXECUTION_UNIT_DIR))

from 账号阶段状态 import build_account_stage_state, write_state_atomically
from 详细数据阶段合同 import DetailDataContractError, validate_identity


class SelfAccountDiagnosisPreflightError(RuntimeError):
    pass


REQUIRED_OVERVIEW_SECTIONS = ("自身条件概要", "用户与需求概要", "产品与变现概要", "账号与内容概要")
PLACEHOLDERS = {"", "待填写", "未知", "暂无", "待补齐", "暂未确认"}
FORMAL_PATTERN = re.compile(r"^(\d{4})年(\d{1,2})月(\d{1,2})日账号详细诊断\.md$")
ACTION_PATTERN = re.compile(r"^(\d{4})年(\d{1,2})月(\d{1,2})日账号改进行动清单\.md$")


def _workspace_root(value: str | Path) -> Path:
    root = Path(value).expanduser().resolve()
    if not root.is_dir():
        raise SelfAccountDiagnosisPreflightError("workspace_root_invalid")
    return root


def _resolve_inside(root: Path, value: str | Path, label: str) -> Path:
    raw_text = str(value or "").strip()
    if not raw_text:
        raise SelfAccountDiagnosisPreflightError(f"{label}_missing")
    raw = Path(raw_text).expanduser()
    target = (raw if raw.is_absolute() else root / raw).resolve()
    try:
        target.relative_to(root)
    except ValueError as exc:
        raise SelfAccountDiagnosisPreflightError(f"{label}_outside_workspace") from exc
    return target


def _read_csv(path: Path, label: str) -> list[dict[str, str]]:
    try:
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            return [
                {str(key): str(value or "").strip() for key, value in row.items() if key is not None}
                for row in csv.DictReader(handle)
            ]
    except (OSError, UnicodeError, csv.Error) as exc:
        raise SelfAccountDiagnosisPreflightError(f"{label}_unreadable") from exc


def _readable_text(path: Path) -> bool:
    try:
        return bool(path.is_file() and path.read_text(encoding="utf-8-sig").strip())
    except (OSError, UnicodeError):
        return False


def _readable_file(path: Path) -> bool:
    try:
        return path.is_file() and path.stat().st_size > 0
    except OSError:
        return False


def _first_value(*rows: dict[str, str], fields: tuple[str, ...]) -> str:
    for row in rows:
        for field in fields:
            value = str(row.get(field) or "").strip()
            if value:
                return value
    return ""


def _evidence_file(root: Path, *rows: dict[str, str], field: str, text: bool = False) -> bool:
    value = _first_value(*rows, fields=(field,))
    if not value:
        return False
    try:
        path = _resolve_inside(root, value, field)
    except SelfAccountDiagnosisPreflightError:
        return False
    return _readable_text(path) if text else _readable_file(path)


def _section_body(text: str, title: str) -> str:
    match = re.search(rf"^##\s+{re.escape(title)}\s*$", text, flags=re.MULTILINE)
    if not match:
        return ""
    following = re.search(r"^##\s+", text[match.end() :], flags=re.MULTILINE)
    end = match.end() + (following.start() if following else len(text[match.end() :]))
    return text[match.end() : end].strip()


def _overview_has_required_sections(path: Path) -> bool:
    if not path.is_file():
        return False
    try:
        text = path.read_text(encoding="utf-8-sig")
    except (OSError, UnicodeError):
        return False
    return all(
        (body := _section_body(text, title))
        and re.sub(r"^#{3,6}\s+.*$", "", body, flags=re.MULTILINE).strip().lower() not in PLACEHOLDERS
        for title in REQUIRED_OVERVIEW_SECTIONS
    )


def _relative(root: Path, path: Path) -> str:
    return path.resolve().relative_to(root).as_posix()


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _write_json_inside(root: Path, raw_path: str | Path, payload: dict[str, Any]) -> None:
    target = _resolve_inside(root, raw_path, "output_json")
    target.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{target.name}.", suffix=".tmp", dir=str(target.parent)
    )
    temporary = Path(temporary_name)
    encoded = (json.dumps(payload, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, target)
        if target.read_bytes() != encoded:
            raise SelfAccountDiagnosisPreflightError("output_json_post_write_mismatch")
    finally:
        temporary.unlink(missing_ok=True)


def _route(
    checks: dict[str, bool],
    facts: list[dict[str, str]],
) -> tuple[str, str]:
    if not checks["account_library_exists"] or not checks["account_unique"]:
        return "basic_data", "账号未能在账号库中唯一定位"
    if (
        not checks["content_library_exists"]
        or not checks["content_list_readable"]
        or not checks["sample_sufficient"]
        or not checks["detail_data_formal_completed"]
        or any(
            item.get("code") in {"selected_content_missing", "selected_content_visibility_mismatch"}
            for item in facts
        )
    ):
        return "detail_data", "详细数据阶段尚未完成正式交接，或本轮没有可用的内容证据"
    if not checks["planning_nonempty"] and not checks["questionnaire_nonempty"]:
        return "continue_diagnosis", "自媒体规划与调查问卷都不可用，按通用账号诊断继续；不能形成个人目标适配结论"
    return "continue_diagnosis", "诊断所需账号、内容、详细数据和目标基准均已通过检查"


def _detail_data_formal_completion(
    root: Path,
    account: dict[str, str],
    *,
    run_id: str = "",
    visibility_scope: str = "public",
    selected_content_ids: list[str] | None = None,
) -> tuple[bool, str]:
    """Verify the run-bound canonical detailed-data report before diagnosis."""
    run = str(run_id or "").strip()
    if not run:
        return False, "detail_run_identity_missing"
    raw_content_list = str(account.get("内容列表路径") or account.get("content_list_path") or "").strip()
    if not raw_content_list:
        return False, "content_list_path_missing"
    try:
        content_list = _resolve_inside(root, raw_content_list, "content_list")
    except SelfAccountDiagnosisPreflightError:
        return False, "content_list_path_invalid"
    report_path = root / ".meiling/staging/self-account-diagnose" / run / "detail-data" / "canonical-detail-report.json"
    try:
        report_path = _resolve_inside(root, report_path, "detail_canonical_report")
    except SelfAccountDiagnosisPreflightError:
        return False, "detail_canonical_report_invalid"
    if not report_path.is_file():
        return False, "detail_canonical_report_missing"
    try:
        report = json.loads(report_path.read_text(encoding="utf-8"))
    except (OSError, ValueError, json.JSONDecodeError):
        return False, "detail_canonical_report_invalid"
    if not isinstance(report, dict):
        return False, "detail_canonical_report_invalid"
    try:
        from 详细数据阶段合同 import validate_canonical_report
        validate_canonical_report(report)
    except Exception:
        return False, "detail_canonical_report_invalid"
    if str(report.get("flow_type") or "").strip() != "self_account":
        return False, "detail_canonical_flow_mismatch"
    if str(report.get("run_id") or "").strip() != run:
        return False, "detail_canonical_identity_mismatch"
    if str(report.get("account_id") or "").strip() != str(account.get("账号ID") or account.get("account_id") or "").strip():
        return False, "detail_canonical_account_mismatch"
    expected_scope = str(visibility_scope or "public").strip().casefold()
    if expected_scope not in {"public", "private", "all"}:
        return False, "detail_visibility_scope_invalid"
    if str(report.get("visibility_scope") or "").strip().casefold() != expected_scope:
        return False, "detail_canonical_visibility_mismatch"
    expected_ids = [str(value).strip() for value in (selected_content_ids or []) if str(value).strip()]
    report_ids = [str(value).strip() for value in (report.get("requested_content_ids") or []) if str(value).strip()]
    if expected_ids and report_ids != expected_ids:
        return False, "detail_canonical_scope_mismatch"
    scope_hash = str(report.get("scope_hash") or "").strip()
    if scope_hash:
        expected_hash = hashlib.sha256(json.dumps({"visibility_scope": expected_scope, "selected_content_ids": report_ids}, ensure_ascii=False, separators=(",", ":")).encode("utf-8")).hexdigest()
        if scope_hash != expected_hash:
            return False, "detail_canonical_scope_hash_mismatch"
    if report.get("status") not in {"completed", "partial"}:
        return False, "detail_canonical_report_incomplete"
    return True, "detail_data_formal_completed"


def _find_account(rows: list[dict[str, str]], account_id: str, platform: str) -> dict[str, str]:
    matches = [
        row
        for row in rows
        if row.get("账号ID", "") == account_id
        and (not platform or row.get("平台", "") == platform)
    ]
    if len(matches) != 1:
        raise SelfAccountDiagnosisPreflightError(f"account_not_unique:{len(matches)}")
    return matches[0]


def _latest_dated_path(account_dir: Path, pattern: re.Pattern[str]) -> Path | None:
    candidates: list[tuple[date, Path]] = []
    try:
        entries = account_dir.iterdir()
    except OSError:
        return None
    for path in entries:
        match = pattern.fullmatch(path.name)
        if not match or not path.is_file():
            continue
        try:
            parsed = date(int(match.group(1)), int(match.group(2)), int(match.group(3)))
        except ValueError:
            continue
        candidates.append((parsed, path))
    if not candidates:
        return None
    return max(candidates, key=lambda item: item[0])[1]


def _latest_formal_diagnosis(root: Path, account_dir: Path) -> str:
    path = _latest_dated_path(account_dir, FORMAL_PATTERN)
    return _relative(root, path) if path is not None else ""


def _latest_action_list(root: Path, account_dir: Path) -> str:
    path = _latest_dated_path(account_dir, ACTION_PATTERN)
    return _relative(root, path) if path is not None else ""


def _collect_comment_files(
    root: Path,
    content_list_path: Path,
    selected_content_ids: list[str],
) -> dict[str, Path]:
    """Collect formal comment archives for the selected detail sample."""
    if not selected_content_ids:
        return {}
    try:
        rows = _read_csv(content_list_path, "content_list")
    except SelfAccountDiagnosisPreflightError:
        return {}
    selected = set(selected_content_ids)
    found: dict[str, Path] = {}
    for row in rows:
        content_id = str(row.get("内容ID") or "").strip()
        if content_id not in selected:
            continue
        raw_dir = str(row.get("内容目录路径") or "").strip()
        if not raw_dir:
            continue
        try:
            content_dir = _resolve_inside(root, raw_dir, "content_dir")
        except SelfAccountDiagnosisPreflightError:
            continue
        for name, label in (("评论.md", "comment_md"), ("评论.json", "comment_json")):
            path = content_dir / name
            if path.is_file():
                found[f"{label}_{content_id}"] = path
    return found


VISIBILITY_PUBLIC = {"公开", "public", "可见", "正常公开", "已公开"}
VISIBILITY_PRIVATE = {"私密", "private", "仅自己可见", "仅自己", "仅自己可见内容"}
VISIBILITY_DRAFT = {"草稿", "draft"}


def _content_visibility(row: dict[str, str]) -> str:
    raw = ""
    for field in ("公开状态", "可见性", "内容状态", "状态"):
        value = str(row.get(field) or "").strip()
        if value:
            raw = value
            break
    normalized = raw.casefold()
    if normalized in {value.casefold() for value in VISIBILITY_PUBLIC}:
        return "public"
    if normalized in {value.casefold() for value in VISIBILITY_PRIVATE}:
        return "private"
    if normalized in {value.casefold() for value in VISIBILITY_DRAFT}:
        return "draft"
    if raw:
        return "other"
    return "unknown"


def _content_stats(
    root: Path,
    content_list_path: Path,
    content_library_path: Path,
    account: dict[str, str],
    selected_content_ids: list[str] | None,
    visibility_scope: str = "public",
) -> tuple[dict[str, Any], list[dict[str, str]]]:
    facts: list[dict[str, str]] = []
    rows = _read_csv(content_list_path, "content_list") if content_list_path.is_file() else []
    library_rows = _read_csv(content_library_path, "content_library") if content_library_path.is_file() else []
    account_id = account.get("账号ID", "")
    platform = account.get("平台", "")
    account_rows = [
        row for row in rows
        if row.get("账号ID", "") == account_id and row.get("平台", "") == platform
    ]
    visibility_counts = {key: 0 for key in ("public", "private", "draft", "other", "unknown")}
    visibility_by_id: dict[str, str] = {}
    for row in account_rows:
        content_id = str(row.get("内容ID") or "").strip()
        if not content_id:
            continue
        visibility = _content_visibility(row)
        visibility_by_id[content_id] = visibility
        visibility_counts[visibility] += 1
    normalized_scope = str(visibility_scope or "public").strip().casefold()
    if normalized_scope not in {"public", "private", "all"}:
        raise SelfAccountDiagnosisPreflightError("visibility_scope_invalid")
    content_ids = [
        row.get("内容ID", "")
        for row in account_rows
        if row.get("内容ID", "")
        and (normalized_scope == "all" or _content_visibility(row) == normalized_scope)
    ]
    content_by_id = {row.get("内容ID", ""): row for row in account_rows if row.get("内容ID", "") in content_ids}
    # 我方内容库是工作区级表，没有账号/平台列（见 内容生产建档执行器.py 的
    # CONTENT_REQUIRED_FIELDS）。内容ID 在工作区内全局唯一，直接按内容ID 建索引；
    # 账号维度只存在于账号级的内容列表，由上面的 account_rows 负责。
    library_by_id: dict[str, dict[str, str]] = {}
    for row in library_rows:
        content_id = row.get("内容ID", "")
        if content_id and content_id not in library_by_id:
            library_by_id[content_id] = row

    explicit_scope = selected_content_ids is not None
    scope_ids: list[str] = []
    for content_id in selected_content_ids if explicit_scope else content_ids:
        normalized = str(content_id or "").strip()
        if normalized and normalized not in scope_ids:
            scope_ids.append(normalized)

    missing_scope_ids = [content_id for content_id in scope_ids if content_id not in content_by_id]
    scope_visibility_mismatch = [
        content_id
        for content_id in (selected_content_ids or [])
        if str(content_id or "").strip()
        and content_id in visibility_by_id
        and normalized_scope != "all"
        and visibility_by_id[content_id] != normalized_scope
    ]
    if scope_visibility_mismatch:
        facts.append({
            "code": "selected_content_visibility_mismatch",
            "content_ids": ",".join(scope_visibility_mismatch),
        })
    if missing_scope_ids:
        facts.append({"code": "selected_content_missing", "content_ids": ",".join(missing_scope_ids)})
    missing_library_count = 0
    evidence_items: list[dict[str, Any]] = []
    for content_id in scope_ids:
        content_row = content_by_id.get(content_id, {})
        library_row = library_by_id.get(content_id, {})
        if content_id in content_by_id and not library_row:
            missing_library_count += 1
        cover_available = _evidence_file(root, content_row, library_row, field="封面图路径")
        transcript_available = _evidence_file(
            root, content_row, library_row, field="视频文案路径", text=True
        )
        title_available = bool(_first_value(content_row, library_row, fields=("标题",)))
        page_available = bool(
            _first_value(
                content_row,
                library_row,
                fields=("对外页面链接", "内容链接", "笔记链接"),
            )
        )
        performance_available = any(
            _first_value(content_row, library_row, fields=(field,))
            for field in ("点赞数", "收藏数", "分享数", "评论数", "核心互动数", "发布时间")
        )
        usable = bool(
            content_row
            and (title_available or page_available or cover_available or transcript_available)
        )
        full = bool(cover_available and transcript_available)
        evidence_items.append(
            {
                "content_id": content_id,
                "evidence_mode": "full" if full else "degraded" if usable else "insufficient",
                "cover_available": cover_available,
                "transcript_available": transcript_available,
                "title_available": title_available,
                "page_available": page_available,
                "performance_available": performance_available,
            }
        )
    if missing_library_count:
        facts.append({"code": "content_library_rows_missing", "count": str(missing_library_count)})

    total_count = len(content_ids)
    usable_count = sum(item["evidence_mode"] != "insufficient" for item in evidence_items)
    full_count = sum(item["evidence_mode"] == "full" for item in evidence_items)
    degraded_count = sum(item["evidence_mode"] == "degraded" for item in evidence_items)
    insufficient_count = len(evidence_items) - usable_count
    # An explicit scope must be usable item by item. An implicit whole-account
    # diagnosis only needs one usable item; missing library rows remain a
    # reported limitation because content_list can still provide evidence.
    required_count = len(scope_ids) if explicit_scope else 1 if scope_ids else 0
    sufficient = (
        bool(scope_ids)
        and usable_count >= required_count
        and not missing_scope_ids
    )
    warnings: list[dict[str, str]] = []
    if usable_count > 0 and full_count == 0:
        warnings.append(
            {
                "code": "no_full_content_evidence",
                "message": "本轮没有完整正文级内容证据，相关内容将使用标题、封面、页面信息和表现数据进行降级分析。",
            }
        )
    if missing_library_count:
        warnings.append(
            {
                "code": "content_library_rows_missing",
                "message": f"内容库缺少 {missing_library_count} 条本轮内容记录，已存在的内容列表字段仍可用于降级分析。",
            }
        )
    if usable_count == 0:
        warnings.append(
            {
                "code": "no_usable_content_evidence",
                "message": "本轮没有可用内容证据，无法形成可靠的内容层判断；如继续，只能做账号层或方法层说明。",
            }
        )
    if visibility_counts["private"]:
        warnings.append(
            {
                "code": "private_content_excluded",
                "message": f"账号共有 {visibility_counts['private']} 条私密内容；当前范围为 {normalized_scope}，私密内容未与公开内容混合分析。",
            }
        )
    if visibility_counts["unknown"]:
        warnings.append(
            {
                "code": "unknown_visibility_excluded",
                "message": f"有 {visibility_counts['unknown']} 条内容缺少公开状态，未默认纳入公开诊断范围。",
            }
        )
    return {
        "total_count": total_count,
        "selected_count": len(scope_ids),
        "selected_content_ids": scope_ids,
        "usable_evidence_count": usable_count,
        "required_usable_count": required_count,
        "full_evidence_count": full_count,
        "degraded_evidence_count": degraded_count,
        "insufficient_evidence_count": insufficient_count,
        "readable_cover_count": sum(bool(item["cover_available"]) for item in evidence_items),
        "readable_transcript_count": sum(bool(item["transcript_available"]) for item in evidence_items),
        "sample_sufficient": sufficient,
        "content_ids": content_ids,
        "visibility_scope": normalized_scope,
        "visibility_counts": visibility_counts,
        "public_count": visibility_counts["public"],
        "private_count": visibility_counts["private"],
        "draft_count": visibility_counts["draft"],
        "other_visibility_count": visibility_counts["other"],
        "unknown_visibility_count": visibility_counts["unknown"],
        "public_face": [
            {
                "内容ID": str(row.get("内容ID") or "").strip(),
                "标题": str(row.get("标题") or "").strip(),
                "发布时间": str(row.get("发布时间") or "").strip(),
                "公开状态": str(row.get("公开状态") or "").strip(),
                "媒介形式": str(row.get("媒介形式") or "").strip(),
                "点赞数": str(row.get("点赞数") or "").strip(),
                "收藏数": str(row.get("收藏数") or "").strip(),
                "分享数": str(row.get("分享数") or "").strip(),
                "评论数": str(row.get("评论数") or "").strip(),
                "核心互动数": str(row.get("核心互动数") or "").strip(),
                "数据水平": str(row.get("数据水平") or "").strip(),
            }
            for row in account_rows
            if _content_visibility(row) == "public" and str(row.get("内容ID") or "").strip()
        ],
        "evidence_items": evidence_items,
        "warnings": warnings,
    }, facts


def _date_parts(value: str) -> tuple[int, int, int] | None:
    text = str(value or "").strip()
    match = re.search(r"(\d{4})[-/年](\d{1,2})[-/月](\d{1,2})", text)
    if not match:
        return None
    return int(match.group(1)), int(match.group(2)), int(match.group(3))


def _previous_diagnosis_date(path: Path | None) -> tuple[int, int, int] | None:
    if path is None:
        return None
    return _date_parts(path.name)


def _new_since_previous(
    public_face: list[dict[str, str]],
    previous_date: tuple[int, int, int] | None,
) -> list[str]:
    """目标口径：上次诊断日之后发布的公开内容；没有历史时返回空，交给调用方按全量处理。"""
    if previous_date is None:
        return []
    new_ids: list[str] = []
    for row in public_face:
        published = _date_parts(row.get("发布时间", ""))
        if published and published > previous_date and row.get("内容ID"):
            new_ids.append(row["内容ID"])
    return new_ids


def inspect_diagnosis_inputs(
    workspace_root: str | Path,
    *,
    account_id: str,
    platform: str = "",
    selected_content_ids: list[str] | None = None,
    visibility_scope: str = "public",
    run_id: str = "",
) -> dict[str, Any]:
    root = _workspace_root(workspace_root)
    normalized_id = str(account_id or "").strip()
    if not normalized_id:
        raise SelfAccountDiagnosisPreflightError("account_id_missing")
    account_library_path = root / "我方资料/我方账号库.csv"
    content_library_path = root / "我方资料/我方内容库.csv"
    account_library_exists = account_library_path.is_file()
    content_library_exists = content_library_path.is_file()
    account_rows = _read_csv(account_library_path, "account_library") if account_library_exists else []
    facts: list[dict[str, str]] = []
    if not account_library_exists:
        facts.append({"code": "account_library_missing"})
    if not content_library_exists:
        facts.append({"code": "content_library_missing"})
    account = {}
    if account_rows:
        try:
            account = _find_account(account_rows, normalized_id, str(platform or "").strip())
        except SelfAccountDiagnosisPreflightError as exc:
            facts.append({"code": "account_not_unique", "detail": str(exc).split(":", 1)[-1]})
    elif account_library_exists:
        facts.append({"code": "account_not_unique", "detail": "0"})

    content_list_path = Path()
    content_list_exists = False
    content_list_readable = False
    content_stats: dict[str, Any] = {
        "total_count": 0,
        "selected_count": 0,
        "selected_content_ids": [],
        "usable_evidence_count": 0,
        "required_usable_count": 0,
        "full_evidence_count": 0,
        "degraded_evidence_count": 0,
        "insufficient_evidence_count": 0,
        "readable_cover_count": 0,
        "readable_transcript_count": 0,
        "readable_summary_count": 0,
        "sample_sufficient": False,
        "content_ids": [],
        "evidence_items": [],
        "visibility_scope": str(visibility_scope or "public").strip().casefold(),
        "visibility_counts": {key: 0 for key in ("public", "private", "draft", "other", "unknown")},
        "public_count": 0,
        "private_count": 0,
        "draft_count": 0,
        "other_visibility_count": 0,
        "unknown_visibility_count": 0,
    }
    detail_data_formal_completed = False
    detail_data_completion_reason = "account_unresolved"
    if account:
        try:
            content_list_path = _resolve_inside(root, account.get("内容列表路径", ""), "content_list")
            content_list_exists = content_list_path.is_file()
            content_list_readable = content_list_exists
            if not content_list_exists:
                facts.append({"code": "content_list_missing"})
        except SelfAccountDiagnosisPreflightError:
            facts.append({"code": "content_list_missing"})
        if content_list_readable:
            content_stats, content_facts = _content_stats(
                root,
                content_list_path,
                content_library_path,
                account,
                selected_content_ids,
                visibility_scope,
            )
            facts.extend(content_facts)
            detail_data_formal_completed, detail_data_completion_reason = _detail_data_formal_completion(
                root,
                account,
                run_id=run_id,
                visibility_scope=content_stats.get("visibility_scope") or visibility_scope,
                selected_content_ids=content_stats.get("selected_content_ids") or [],
            )
            if not detail_data_formal_completed:
                facts.append({"code": "detail_data_formal_incomplete", "detail": detail_data_completion_reason})

    planning_path = root / "我方资料/我方自媒体规划.md"
    overview_path = root / "我方资料/我方当前概要.md"
    questionnaire_path = root / "我方资料/调查问卷.md"
    planning_exists = planning_path.is_file()
    overview_exists = overview_path.is_file()
    questionnaire_exists = questionnaire_path.is_file()
    planning_nonempty = _readable_text(planning_path)
    overview_nonempty = _readable_text(overview_path)
    questionnaire_nonempty = _readable_text(questionnaire_path)
    overview_sections_valid = _overview_has_required_sections(overview_path)
    if not planning_exists or not planning_nonempty:
        facts.append({"code": "planning_missing_or_empty"})
    if not questionnaire_exists or not questionnaire_nonempty:
        facts.append({"code": "questionnaire_missing_or_empty"})
    if not planning_nonempty and not questionnaire_nonempty:
        facts.append({"code": "basis_missing_or_empty"})
    if not overview_exists or not overview_nonempty:
        facts.append({"code": "overview_missing_or_empty"})
    if overview_nonempty and not overview_sections_valid:
        facts.append({"code": "overview_sections_incomplete"})

    image_path = str(account.get("图片说明路径", "")).strip() if account else ""
    image_exists = False
    if image_path:
        try:
            image_exists = _resolve_inside(root, image_path, "image_material").is_dir()
        except SelfAccountDiagnosisPreflightError:
            image_exists = False
    account_dir = content_list_path.parent if content_list_readable else root
    checks = {
        "account_unique": bool(account),
        "planning_file_exists": planning_exists,
        "planning_nonempty": planning_nonempty,
        "questionnaire_file_exists": questionnaire_exists,
        "questionnaire_nonempty": questionnaire_nonempty,
        "overview_file_exists": overview_exists,
        "overview_nonempty": overview_nonempty,
        "overview_has_required_sections": overview_sections_valid,
        "account_library_exists": account_library_exists,
        "content_library_exists": content_library_exists,
        "content_list_exists": content_list_exists,
        "content_list_readable": content_list_readable,
        "image_material_exists": image_exists,
        "sample_sufficient": content_stats["sample_sufficient"],
        "detail_data_formal_completed": detail_data_formal_completed,
    }
    next_route, route_reason = _route(checks, facts)
    latest_diagnosis_path = _latest_dated_path(account_dir, FORMAL_PATTERN) if content_list_readable else None
    public_face = content_stats.get("public_face") or []
    previous_date = _previous_diagnosis_date(latest_diagnosis_path)
    content_stats["previous_diagnosis_date"] = (
        f"{previous_date[0]:04d}-{previous_date[1]:02d}-{previous_date[2]:02d}" if previous_date else ""
    )
    content_stats["new_since_previous_diagnosis_ids"] = _new_since_previous(public_face, previous_date)
    content_stats["new_since_previous_diagnosis_count"] = len(content_stats["new_since_previous_diagnosis_ids"])
    content_stats["public_face_count"] = len(public_face)
    latest_action_path = _latest_dated_path(account_dir, ACTION_PATTERN) if content_list_readable else None
    creator_center_path = account_dir / "创作者中心数据/最新采集.json"
    comment_files = (
        _collect_comment_files(root, content_list_path, content_stats.get("selected_content_ids") or [])
        if content_list_readable
        else {}
    )
    snapshot_files: dict[str, dict[str, str]] = {}
    snapshot_candidates = {
        "account_library": account_library_path,
        "content_library": content_library_path,
        "content_list": content_list_path if content_list_readable else None,
        "overview": overview_path,
        "planning": planning_path,
        "questionnaire": questionnaire_path,
        "previous_diagnosis": latest_diagnosis_path,
        "previous_action_list": latest_action_path,
        "creator_center": creator_center_path if creator_center_path.is_file() else None,
        **comment_files,
    }
    for label, path in snapshot_candidates.items():
        if path is not None and path.is_file():
            snapshot_files[label] = {
                "path": _relative(root, path),
                "sha256": _sha256(path),
            }
    return {
        "schema_version": "2",
        "status": "checked",
        "run_id": str(run_id or "").strip(),
        "account": account,
        "content": content_stats,
        "paths": {
            "account_library": _relative(root, account_library_path) if account_library_exists else "",
            "content_library": _relative(root, content_library_path) if content_library_exists else "",
            "content_list": _relative(root, content_list_path) if content_list_readable else "",
            "image_material": _relative(root, _resolve_inside(root, image_path, "image_material")) if image_exists else "",
            "creator_center": _relative(root, creator_center_path) if creator_center_path.is_file() else "",
            "comments": [_relative(root, path) for path in comment_files.values()],
            "previous_action_list": _latest_action_list(root, account_dir) if content_list_readable else "",
            "planning": _relative(root, planning_path) if planning_nonempty else "",
            "questionnaire": _relative(root, questionnaire_path) if questionnaire_nonempty else "",
        },
        "latest_formal_diagnosis": _relative(root, latest_diagnosis_path) if latest_diagnosis_path else "",
        "latest_action_list": _latest_action_list(root, account_dir) if content_list_readable else "",
        "creator_center": _relative(root, creator_center_path) if creator_center_path.is_file() else "",
        "comment_files": [_relative(root, path) for path in comment_files.values()],
        "checks": checks,
        "next_route": next_route,
        "route_reason": route_reason,
        "personal_adaptation_available": bool(planning_nonempty or questionnaire_nonempty),
        "basis": (
            "planning" if planning_nonempty else ("questionnaire" if questionnaire_nonempty else "")
        ),
        "input_snapshot": {
            "schema_version": "1",
            "files": snapshot_files,
        },
        "objective_checks_passed": all(checks[key] for key in (
            "account_unique",
            "account_library_exists",
            "content_library_exists",
            "content_list_readable",
            "sample_sufficient",
            "detail_data_formal_completed",
        )),
        "warnings": content_stats.get("warnings", []),
        "facts": facts,
    }


def preflight_state_path(
    workspace_root: str | Path,
    result: dict[str, Any],
    *,
    stage_state_path: str | Path = "",
    task_content_list_path: str | Path = "",
) -> Path:
    root = _workspace_root(workspace_root)
    supplied_path: Path | None = None
    if str(stage_state_path or "").strip():
        supplied_path = _resolve_inside(root, stage_state_path, "stage_state_path")
        if tuple(supplied_path.relative_to(root).parts[-2:]) != ("过程记录", "当前阶段状态.json"):
            raise SelfAccountDiagnosisPreflightError("stage_state_path_invalid")
    task_content_list: Path | None = None
    if str(task_content_list_path or "").strip():
        task_content_list = _resolve_inside(root, task_content_list_path, "task_content_list_path")
        expected_supplied_path = task_content_list.parent / "过程记录/当前阶段状态.json"
        if supplied_path is not None and supplied_path != expected_supplied_path:
            raise SelfAccountDiagnosisPreflightError("stage_state_path_mismatch")
    paths = result.get("paths")
    content_list = str(paths.get("content_list") or "").strip() if isinstance(paths, dict) else ""
    if not content_list:
        account = result.get("account")
        if isinstance(account, dict):
            content_list = str(account.get("内容列表路径") or account.get("content_list_path") or "").strip()
    if not content_list and task_content_list is None:
        raise SelfAccountDiagnosisPreflightError("stage_state_account_unresolved")
    if not content_list:
        return task_content_list.parent / "过程记录/当前阶段状态.json"
    resolved_content_list = _resolve_inside(root, content_list, "content_list")
    derived_path = resolved_content_list.parent / "过程记录/当前阶段状态.json"
    if task_content_list is not None and task_content_list != resolved_content_list:
        raise SelfAccountDiagnosisPreflightError("task_content_list_path_mismatch")
    if supplied_path is not None and supplied_path != derived_path:
        raise SelfAccountDiagnosisPreflightError("stage_state_path_mismatch")
    return derived_path


def build_preflight_stage_state(
    workspace_root: str | Path,
    result: dict[str, Any],
    *,
    diagnosis_input_path: str | Path,
    run_id: str = "",
    task_account: dict[str, Any] | None = None,
) -> dict[str, Any]:
    root = _workspace_root(workspace_root)
    route = str(result.get("next_route") or "").strip()
    account = result.get("account")
    content = result.get("content")
    if not isinstance(account, dict) or not account:
        if route != "basic_data" or not isinstance(task_account, dict) or not task_account:
            raise SelfAccountDiagnosisPreflightError("stage_state_account_unresolved")
        account = task_account
    if not str(account.get("账号ID") or account.get("account_id") or "").strip():
        raise SelfAccountDiagnosisPreflightError("stage_state_account_unresolved")
    if not isinstance(content, dict):
        raise SelfAccountDiagnosisPreflightError("stage_state_content_invalid")
    # 本流程不再有等待用户或等待其他流程的分支，三条路由恒为进行中。
    route_states = {
        "continue_diagnosis": (
            "in_progress",
            "diagnosis",
            "continue_diagnosis",
            ("执行流程/自我账号详细诊断流程.md",),
        ),
        "basic_data": (
            "in_progress",
            "base_data",
            "continue_base_data",
            ("执行流程/我方账号基础数据获取流程.md",),
        ),
        "detail_data": (
            "in_progress",
            "detail_data",
            "continue_detail_processing",
            ("执行流程/我方账号详细数据获取流程.md",),
        ),
    }
    if route not in route_states:
        raise SelfAccountDiagnosisPreflightError(f"stage_state_route_invalid:{route or 'empty'}")
    normalized_run_id = str(run_id or "").strip()
    if not normalized_run_id:
        raise SelfAccountDiagnosisPreflightError("run_identity_missing")
    status, current_entry, next_action, next_flows = route_states[route]
    input_path = _resolve_inside(root, diagnosis_input_path, "diagnosis_input")
    recovery_context: dict[str, Any] = {
        "status": status,
        "current_entry": current_entry,
        "next_action": next_action,
        "selected_content_ids": content.get("selected_content_ids") or [],
        "diagnosis_input_path": _relative(root, input_path),
    }
    state = build_account_stage_state(
        stage="自我账号详细诊断",
        complete=False,
        platform=str(account.get("平台") or account.get("platform") or ""),
        account_id=str(account.get("账号ID") or account.get("account_id") or ""),
        account_name=str(account.get("账号名") or account.get("account_name") or ""),
        next_flows=next_flows,
        recovery_context=recovery_context,
    )
    state["flow_type"] = "self_account"
    state["run_id"] = normalized_run_id
    return state


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="汇总自我账号详细诊断的客观前置检查事实")
    parser.add_argument("--workspace-root", required=True)
    parser.add_argument("--account-id", required=True)
    parser.add_argument("--platform", default="")
    parser.add_argument("--flow-type", default="", choices=("", "benchmark_account", "self_account"))
    parser.add_argument("--run-id", default="", help="当前一次具体执行的唯一标识")
    parser.add_argument("--stage-state-path", default="", help="本轮账号的阶段状态路径")
    parser.add_argument("--task-content-list-path", default="", help="本轮账号的内容列表路径")
    parser.add_argument("--selected-content-ids", default="", help="本轮诊断内容 ID，逗号分隔")
    parser.add_argument(
        "--visibility-scope",
        choices=("public", "private", "all"),
        default="public",
        help="诊断内容范围；默认只分析公开内容，私密内容必须单独选择",
    )
    parser.add_argument("--output-json", default="", help="将检查结果原子写入工作区内的 JSON 文件")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        validate_identity(
            flow_type=str(args.flow_type or ""),
            run_id=str(args.run_id or ""),
            standalone=not str(args.flow_type or "").strip(),
        )
        result = inspect_diagnosis_inputs(
            args.workspace_root,
            account_id=args.account_id,
            platform=args.platform,
            selected_content_ids=(
                [value.strip() for value in args.selected_content_ids.split(",") if value.strip()]
                if args.selected_content_ids.strip()
                else None
            ),
            visibility_scope=args.visibility_scope,
            run_id=args.run_id,
        )
        if args.output_json.strip():
            root = Path(args.workspace_root).expanduser().resolve()
            _write_json_inside(root, args.output_json, result)
            state_path = preflight_state_path(
                root,
                result,
                stage_state_path=args.stage_state_path,
                task_content_list_path=args.task_content_list_path,
            )
            state = build_preflight_stage_state(
                root,
                result,
                diagnosis_input_path=args.output_json,
                run_id=args.run_id,
                task_account={"账号ID": args.account_id, "平台": args.platform},
            )
            write_state_atomically(state_path, state)
    except (OSError, ValueError, DetailDataContractError, SelfAccountDiagnosisPreflightError) as exc:
        print(json.dumps({"ok": False, "status": "failed", "error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
