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

from csv安全写入 import read_csv_table_with_snapshot
from 对标内容数据建档执行器 import normalize_url
from 对标内容分析运行状态 import (
    BenchmarkContentRunStateError,
    complete_run,
    create_or_refresh_run_state,
    executable_next,
    next_action,
    process_feature_assets,
    resume_run,
)
from 分析诊断模型投影 import (
    ModelProjectionError,
    assert_snapshot_write_compatible,
    write_content_model_projection,
)


CONTENT_LIBRARY = Path("对标资料/对标内容库.csv")
FEATURE_CATALOG = Path("沉淀结论/内容特征词典.csv")
BODY_EMPTY_MARKER = "正文状态：原内容确认未填写文字正文。"
ANALYSIS_INTENTS = {"full_analysis", "reanalyze"}
FROZEN_ROW_FIELDS = (
    "内容ID", "平台", "账号ID", "标题", "媒介形式", "内容目录路径",
    "视频文案路径", "封面图路径", "点赞数", "收藏数", "分享数", "评论数",
    "核心互动数", "数据水平", "对外页面链接",
)


class ContentAnalysisPreflightError(RuntimeError):
    pass


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _relative(root: Path, path: Path) -> str:
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError as exc:
        raise ContentAnalysisPreflightError(f"path_outside_workspace:{path}") from exc


def _resolve_inside(root: Path, raw_path: str, label: str) -> Path:
    text = str(raw_path or "").strip().replace("\\", "/")
    if not text:
        raise ContentAnalysisPreflightError(f"{label}_path_missing")
    logical = PurePosixPath(text)
    candidate = Path(text).expanduser()
    resolved = candidate.resolve() if candidate.is_absolute() else root.joinpath(*logical.parts).resolve()
    try:
        resolved.relative_to(root.resolve())
    except ValueError as exc:
        raise ContentAnalysisPreflightError(f"{label}_path_outside_workspace") from exc
    return resolved


def _snapshot_file(root: Path, path: Path, *, external: bool = False) -> dict[str, str]:
    if not path.is_file():
        raise ContentAnalysisPreflightError(f"snapshot_file_missing:{path}")
    return {
        "path": str(path.resolve()) if external else _relative(root, path),
        "sha256": _sha256(path),
    }


def _snapshot_optional_file(root: Path, path: Path) -> dict[str, object]:
    result: dict[str, object] = {
        "path": _relative(root, path),
        "exists": path.is_file(),
        "sha256": "",
    }
    if path.is_file():
        result["sha256"] = _sha256(path)
    return result


def _semantic_fingerprint(snapshot_files: dict[str, dict[str, object]], row: dict[str, str]) -> dict[str, object]:
    """Hash only the target row and evidence artifacts used by this analysis."""
    components = {
        "content_row": {field: str(row.get(field) or "") for field in FROZEN_ROW_FIELDS},
        "files": {
            label: {
                "path": str(item.get("path") or ""),
                "exists": bool(item.get("exists", True)),
                "sha256": str(item.get("sha256") or ""),
            }
            for label, item in sorted(snapshot_files.items())
            if label in {"transcript", "cover", "feature_catalog"}
        },
    }
    encoded = json.dumps(components, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return {"version": "1", "sha256": hashlib.sha256(encoded).hexdigest(), "components": components}


def _read_body(path: Path) -> tuple[bool, str]:
    if not path.is_file():
        return False, ""
    try:
        text = path.read_text(encoding="utf-8-sig").strip()
    except (OSError, UnicodeError):
        return False, ""
    if not text:
        return False, ""
    return True, "确认无正文" if text == BODY_EMPTY_MARKER else "已获取"


def _transcript_quality_warnings(path: Path | None) -> list[str]:
    """Flag deterministic text anomalies without blocking structural analysis."""
    if path is None or not path.is_file():
        return []
    try:
        text = path.read_text(encoding="utf-8-sig").strip()
    except (OSError, UnicodeError):
        return []
    if not text or text == BODY_EMPTY_MARKER:
        return []
    warnings: list[str] = []
    if len(text) < 20:
        warnings.append("transcript_too_short")
    if re.search(r"[\u0000-\u0008\u000b\u000c\u000e-\u001f]", text):
        warnings.append("transcript_control_character")
    if re.search(r"(.{1,8})\1{3,}", text):
        warnings.append("transcript_repeated_fragment")
    return warnings


def _infer_content_dir(root: Path, row: dict[str, str]) -> Path | None:
    for field in ("内容目录路径", "视频文案路径", "内容分析路径"):
        value = str(row.get(field) or "").strip()
        if not value:
            continue
        try:
            path = _resolve_inside(root, value, field)
        except ContentAnalysisPreflightError:
            continue
        return path if field == "内容目录路径" else path.parent
    return None


def _find_content(
    rows: list[dict[str, str]],
    *,
    content_id: str,
    platform: str,
    title: str,
    source_url: str,
) -> dict[str, str]:
    normalized_id = str(content_id or "").strip()
    normalized_platform = str(platform or "").strip()
    normalized_title = str(title or "").strip()
    normalized_url = str(source_url or "").strip()
    canonical_url = normalize_url(normalized_url) if normalized_url else ""
    if not any((normalized_id, normalized_title, normalized_url)):
        raise ContentAnalysisPreflightError("content_locator_missing")
    matches = []
    for row in rows:
        if normalized_id and str(row.get("内容ID") or "").strip() != normalized_id:
            continue
        if normalized_platform and str(row.get("平台") or "").strip() != normalized_platform:
            continue
        if normalized_title and str(row.get("标题") or "").strip() != normalized_title:
            continue
        if normalized_url and normalize_url(str(row.get("对外页面链接") or "").strip()) != canonical_url:
            continue
        matches.append(row)
    if len(matches) != 1:
        raise ContentAnalysisPreflightError(f"content_match_count:{len(matches)}")
    return {str(key): str(value or "").strip() for key, value in matches[0].items()}


def _key_data(row: dict[str, str]) -> str:
    labels = (("点赞", "点赞数"), ("收藏", "收藏数"), ("分享", "分享数"), ("评论", "评论数"))
    return " / ".join(f"{label} {row.get(field, '').strip() or '缺失'}" for label, field in labels)


def _comment_summary(root: Path, row: dict[str, str]) -> dict[str, object]:
    """读该内容已存档的评论，供 `2.5` 反馈一级评论条数。

    口径与 `评论.md` 一致：平台已声明没有更多时「一共有」＝已获取；
    还有更多时用平台声明的总数兜底。未获取 = 一共有 - 已获取。
    评论只是随资料一并反馈，**不参与本轮分析**。
    """
    missing = {"status": "missing", "path": "", "一共有": 0, "已获取": 0, "未获取": 0}
    raw_path = str(row.get("评论路径") or "").strip()
    if not raw_path:
        return dict(missing)
    try:
        markdown_path = _resolve_inside(root, raw_path, "评论路径")
    except Exception:
        return dict(missing)
    json_path = markdown_path.with_name("评论.json")
    if not json_path.is_file():
        return dict(missing)
    try:
        payload = json.loads(json_path.read_text(encoding="utf-8"))
    except Exception:
        return {**missing, "status": "unreadable", "path": _relative(root, markdown_path)}
    if not isinstance(payload, dict):
        return {**missing, "status": "unreadable", "path": _relative(root, markdown_path)}

    def _as_int(value: object) -> int:
        try:
            return int(value)  # type: ignore[arg-type]
        except (TypeError, ValueError):
            return 0

    cumulative = _as_int(payload.get("累计条数"))
    declared = _as_int(payload.get("响应总数"))
    resume = payload.get("续拉状态")
    more = resume.get("has_more") if isinstance(resume, dict) else None
    overall = cumulative if more is False or not declared else max(cumulative, declared)
    return {
        "status": "available",
        "path": _relative(root, markdown_path),
        "一共有": overall,
        "已获取": cumulative,
        "未获取": max(0, overall - cumulative),
        "平台声明总数": declared,
        "还有更多": more,
    }


def inspect_analysis_inputs(
    workspace_root: str | Path,
    *,
    content_id: str = "",
    platform: str = "",
    title: str = "",
    source_url: str = "",
    intent: str = "full_analysis",
) -> dict[str, Any]:
    root = Path(workspace_root).expanduser().resolve()
    if not root.is_dir():
        raise ContentAnalysisPreflightError("workspace_root_invalid")
    normalized_intent = str(intent or "").strip()
    if normalized_intent not in ANALYSIS_INTENTS:
        raise ContentAnalysisPreflightError(f"intent_invalid:{normalized_intent}")

    library_path = root / CONTENT_LIBRARY
    fields, rows, _ = read_csv_table_with_snapshot(library_path)
    required_fields = {"内容ID", "平台", "标题", "媒介形式", "视频文案路径"}
    missing_fields = sorted(required_fields - set(fields))
    if missing_fields:
        raise ContentAnalysisPreflightError(f"content_library_missing_fields:{missing_fields}")
    row = _find_content(
        rows,
        content_id=content_id,
        platform=platform,
        title=title,
        source_url=source_url,
    )
    content_dir = _infer_content_dir(root, row)
    transcript_path: Path | None = None
    transcript_path_value = row.get("视频文案路径", "")
    if transcript_path_value:
        transcript_path = _resolve_inside(root, transcript_path_value, "transcript")
    transcript_readable, body_status = _read_body(transcript_path) if transcript_path else (False, "")
    transcript_quality_warnings = _transcript_quality_warnings(transcript_path)

    cover_path: Path | None = None
    cover_value = row.get("封面图路径", "")
    if cover_value:
        resolved_cover = _resolve_inside(root, cover_value, "cover")
        if resolved_cover.is_file():
            cover_path = resolved_cover

    formal_analysis_path = content_dir / "内容分析.md" if content_dir is not None else None
    formal_analysis = formal_analysis_path if formal_analysis_path and formal_analysis_path.is_file() else None

    identity_ready = all(row.get(field, "").strip() for field in ("内容ID", "平台", "标题", "媒介形式"))
    missing_requirements: list[str] = []
    if not identity_ready:
        missing_requirements.append("content_identity_incomplete")
    if content_dir is None:
        missing_requirements.append("content_dir_unavailable")
    # 视频文案不是阻断项：拿不到正文时按现有资料出有限报告，正文相关结论写「信息不足」。
    analysis_ready = not missing_requirements

    comment_summary = _comment_summary(root, row)
    comments_path = None
    if str(comment_summary.get("path") or "").strip():
        candidate = root / str(comment_summary["path"])
        if candidate.is_file():
            comments_path = candidate

    feature_catalog_path = root / FEATURE_CATALOG
    snapshot_files: dict[str, dict[str, object]] = {
        "content_library": _snapshot_file(root, library_path),
        "feature_catalog": _snapshot_file(root, feature_catalog_path),
    }
    if transcript_path is not None and transcript_path.is_file():
        snapshot_files["transcript"] = _snapshot_file(root, transcript_path)
    if cover_path is not None:
        snapshot_files["cover"] = _snapshot_file(root, cover_path)
    if comments_path is not None:
        snapshot_files["comments"] = _snapshot_file(root, comments_path)
    if formal_analysis_path is not None:
        snapshot_files["formal_analysis"] = _snapshot_optional_file(root, formal_analysis_path)

    return {
        "schema_version": "1",
        "status": "checked",
        "intent": normalized_intent,
        "analysis_ready": analysis_ready,
        "missing_requirements": missing_requirements,
        "content": {
            "content_id": row.get("内容ID", ""),
            "platform": row.get("平台", ""),
            "title": row.get("标题", ""),
            "media_type": row.get("媒介形式", ""),
            "body_status": body_status,
            "transcript_quality_warnings": transcript_quality_warnings,
            "library_row": row,
            "frozen_row": {field: row.get(field, "") for field in FROZEN_ROW_FIELDS},
        },
        "paths": {
            "content_library": _relative(root, library_path),
            "content_dir": _relative(root, content_dir) if content_dir else "",
            "transcript": _relative(root, transcript_path) if transcript_path and transcript_path.is_file() else "",
            "cover": _relative(root, cover_path) if cover_path else "",
            "comments": _relative(root, comments_path) if comments_path else "",
            "formal_analysis": _relative(root, formal_analysis) if formal_analysis else "",
        },
        "interaction_data": {
            field: row.get(field, "")
            for field in ("点赞数", "收藏数", "分享数", "评论数", "核心互动数", "数据水平")
        },
        "comment_summary": comment_summary,
        "candidate_expectations": {
            "正文获取状态": body_status,
            "关键数据": _key_data(row),
            "数据水平": row.get("数据水平", "") or "缺失",
        },
        "checks": {
            "content_unique": True,
            "identity_ready": identity_ready,
            "transcript_readable": transcript_readable,
            "transcript_available": bool(transcript_path and transcript_path.is_file()),
            "transcript_quality_ok": not transcript_quality_warnings,
            "cover_readable": bool(cover_path),
            "comments_available": bool(comments_path),
            "formal_analysis_readable": bool(formal_analysis),
        },
        "objective_checks_passed": analysis_ready,
        "input_snapshot": {
            "schema_version": "1",
            "files": snapshot_files,
            "semantic_fingerprint": _semantic_fingerprint(snapshot_files, row),
        },
    }


def _write_json(root: Path, raw_path: str, payload: dict[str, Any]) -> None:
    target = _resolve_inside(root, raw_path, "output_json")
    relative = target.relative_to(root).as_posix()
    if not relative.startswith(".meiling/staging/benchmark-content-diagnose/"):
        raise ContentAnalysisPreflightError("output_json_outside_analysis_staging")
    target.parent.mkdir(parents=True, exist_ok=True)
    data = (json.dumps(payload, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    descriptor, name = tempfile.mkstemp(prefix=f".{target.name}.", suffix=".tmp", dir=target.parent)
    temporary = Path(name)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, target)
    finally:
        temporary.unlink(missing_ok=True)


def persist_analysis_input(
    workspace_root: str | Path,
    payload: dict[str, Any],
    *,
    output_json: str = "",
) -> dict[str, Any]:
    if payload.get("analysis_ready") is not True or payload.get("objective_checks_passed") is not True:
        return dict(payload)
    root = Path(workspace_root).expanduser().resolve()
    staging_root = root / ".meiling/staging/benchmark-content-diagnose"
    if output_json:
        target = _resolve_inside(root, output_json, "output_json")
        relative = target.relative_to(root).as_posix()
        if not relative.startswith(".meiling/staging/benchmark-content-diagnose/"):
            raise ContentAnalysisPreflightError("output_json_outside_analysis_staging")
    else:
        staging_root.mkdir(parents=True, exist_ok=True)
        run_dir = Path(tempfile.mkdtemp(prefix="run-", dir=staging_root))
        target = run_dir / "分析输入快照.json"
        relative = target.relative_to(root).as_posix()

    run_dir = target.parent
    try:
        run_dir.relative_to(staging_root)
    except ValueError as exc:
        raise ContentAnalysisPreflightError("output_json_outside_analysis_staging") from exc
    result = dict(payload)
    result.update(
        {
            "run_id": run_dir.name,
            "staging_dir": run_dir.relative_to(root).as_posix(),
            "analysis_input_path": relative,
        }
    )
    assert_snapshot_write_compatible(root, snapshot_path=target, snapshot_payload=result)
    _write_json(root, relative, result)
    write_content_model_projection(root, snapshot_path=target, snapshot=result)
    state = create_or_refresh_run_state(
        root,
        run_dir=run_dir,
        snapshot=result,
        analysis_input_path=relative,
    )
    result["run_state_path"] = (run_dir / "运行状态.json").relative_to(root).as_posix()
    result["next_action"] = next_action(state)
    result["next"] = executable_next(state)
    return result


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="定位对标内容并冻结分析证据快照")
    parser.add_argument("--workspace-root", required=True)
    parser.add_argument(
        "--action",
        choices=("inspect", "resume", "process-assets", "complete"),
        default="inspect",
    )
    parser.add_argument("--content-id", default="")
    parser.add_argument("--platform", default="")
    parser.add_argument("--title", default="")
    parser.add_argument("--source-url", default="")
    parser.add_argument("--intent", choices=sorted(ANALYSIS_INTENTS), default="full_analysis")
    parser.add_argument("--output-json", default="")
    parser.add_argument("--run-id", default="")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        if args.action == "resume":
            result = resume_run(
                args.workspace_root,
                content_id=args.content_id,
                platform=args.platform,
                run_id=args.run_id,
            )
        elif args.action == "process-assets":
            result = process_feature_assets(args.workspace_root, run_id=args.run_id)
        elif args.action == "complete":
            state = complete_run(args.workspace_root, run_id=args.run_id)
            result = {
                **state,
                "ok": True,
                "run_status": state["status"],
                "status": "run_completed",
                "next_action": next_action(state),
                "next": executable_next(state),
            }
        else:
            result = inspect_analysis_inputs(
                args.workspace_root,
                content_id=args.content_id,
                platform=args.platform,
                title=args.title,
                source_url=args.source_url,
                intent=args.intent,
            )
            result = persist_analysis_input(
                args.workspace_root, result, output_json=args.output_json
            )
    except (
        BenchmarkContentRunStateError,
        ContentAnalysisPreflightError,
        ModelProjectionError,
        OSError,
        UnicodeError,
        ValueError,
    ) as exc:
        print(json.dumps({"ok": False, "status": "failed", "error": str(exc)}, ensure_ascii=False))
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result.get("ok", True) else 1


if __name__ == "__main__":
    raise SystemExit(main())
