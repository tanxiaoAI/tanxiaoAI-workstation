#!/usr/bin/env python3
"""Deterministic entry point for the topic-evaluation semantic stage."""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import re
import secrets
import sys
import tempfile
import time
from pathlib import Path
from typing import Mapping

from 对标内容数据建档执行器 import normalize_url
from 选题分析正式化执行器 import TopicAnalysisFinalizeError, finalize_topic_analysis


class TopicWorkflowCoordinatorError(RuntimeError):
    pass


TOPIC_ID_PATTERN = re.compile(r"^选题(?P<number>\d{3,})$")
BENCHMARK_CONTENT_LIBRARY = "对标资料/对标内容库.csv"
TOPIC_SUMMARY_FIELDS = (
    "选题ID",
    "选题名称",
    "当前状态",
    "核心命题",
    "是否建议做",
    "参考内容链接",
)
CONTEXT_KEYS = {
    "schema_version", "workflow_id", "run_id", "topic_id", "topic_name", "operation",
    "reference_content_id", "reference_url", "reference_analysis_path",
    "reference_transcript_path",
    "reference_decision",
}


def _root(value: str | Path) -> Path:
    root = Path(value).expanduser().resolve()
    if not root.is_dir():
        raise TopicWorkflowCoordinatorError(f"工作区根目录不存在: {root}")
    return root


def _inside(root: Path, value: str | Path, label: str, *, required: bool = True) -> Path | None:
    raw = Path(value).expanduser()
    path = raw.resolve() if raw.is_absolute() else (root / raw).resolve()
    try:
        path.relative_to(root)
    except ValueError as exc:
        raise TopicWorkflowCoordinatorError(f"{label}必须位于当前工作区内") from exc
    if path.is_symlink() or not path.is_file():
        if required:
            raise TopicWorkflowCoordinatorError(f"{label}不存在或不是普通文件: {path}")
        return None
    return path


def _logical(root: Path, path: Path) -> str:
    return path.resolve().relative_to(root).as_posix()


def _read_json(path: Path, label: str) -> Mapping[str, object]:
    try:
        value = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise TopicWorkflowCoordinatorError(f"无法读取{label}: {exc}") from exc
    if not isinstance(value, Mapping):
        raise TopicWorkflowCoordinatorError(f"{label}格式无效")
    return value


def _write_json(path: Path, value: Mapping[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    temporary = Path(name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(value, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _library(root: Path) -> tuple[list[str], list[dict[str, str]]]:
    path = _inside(root, "我方资料/选题库.csv", "选题库")
    try:
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle)
            fields = list(reader.fieldnames or [])
            rows = [dict(row) for row in reader]
    except (OSError, UnicodeError, csv.Error) as exc:
        raise TopicWorkflowCoordinatorError(f"无法读取选题库: {exc}") from exc
    if "选题ID" not in fields or "选题名称" not in fields:
        raise TopicWorkflowCoordinatorError("选题库缺少选题ID或选题名称字段")
    ids = [row.get("选题ID", "").strip() for row in rows if row.get("选题ID", "").strip()]
    if len(set(ids)) != len(ids):
        raise TopicWorkflowCoordinatorError("选题库存在重复选题ID")
    return fields, rows


def _validate_reference_artifacts(
    root: Path,
    *,
    content_id: str = "",
    reference_url: str,
    analysis: Mapping[str, object],
) -> None:
    library_path = _inside(root, BENCHMARK_CONTENT_LIBRARY, "对标内容库")
    try:
        with library_path.open("r", encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle)
            fields = set(reader.fieldnames or [])
            rows = [dict(row) for row in reader]
    except (OSError, UnicodeError, csv.Error) as exc:
        raise TopicWorkflowCoordinatorError(f"无法读取对标内容库: {exc}") from exc
    # 特征卡已退出选题分析：只核验正式内容分析。
    required = {"内容ID", "对外页面链接", "内容分析路径"}
    missing = sorted(required - fields)
    if missing:
        raise TopicWorkflowCoordinatorError(f"对标内容库缺少必要字段: {missing}")

    normalized_reference = normalize_url(reference_url)
    if not normalized_reference:
        raise TopicWorkflowCoordinatorError("当前选题的参考内容链接无效")
    matches = [
        row
        for row in rows
        if normalize_url(row.get("对外页面链接", "")) == normalized_reference
    ]
    if not matches:
        raise TopicWorkflowCoordinatorError("当前参考链接未在对标内容库中唯一建档")
    if len(matches) != 1:
        raise TopicWorkflowCoordinatorError("当前参考链接在对标内容库中对应多条内容")

    row = matches[0]
    expected_content_id = row.get("内容ID", "").strip() or "未知内容"
    if content_id and content_id != expected_content_id:
        raise TopicWorkflowCoordinatorError("返回的内容 ID 不属于当前参考链接")
    expected_analysis = _inside(
        root, row.get("内容分析路径", ""), f"{expected_content_id}的正式内容分析"
    )
    if analysis.get("path") != _logical(root, expected_analysis):
        raise TopicWorkflowCoordinatorError("返回的内容分析不属于当前参考内容")


def _next_id(rows: list[dict[str, str]]) -> str:
    numbers = []
    for row in rows:
        value = row.get("选题ID", "").strip()
        if not value:
            continue
        match = TOPIC_ID_PATTERN.fullmatch(value)
        if match is None:
            raise TopicWorkflowCoordinatorError(f"选题库存在非法选题ID: {value}")
        numbers.append(int(match.group("number")))
    return f"选题{max(numbers, default=0) + 1:03d}"


def _new_context(root: Path, payload: Mapping[str, str]) -> Path:
    run_id = f"run-{time.strftime('%Y%m%d-%H%M%S')}-{secrets.token_hex(4)}"
    path = root / ".meiling/staging/topic-evaluate" / run_id / "context.json"
    _write_json(path, {"schema_version": "1", "workflow_id": "topic-evaluate", **payload, "run_id": run_id})
    return path


def _load_context(root: Path, value: str) -> tuple[Path, dict[str, str]]:
    path = _inside(root, value, "选题工作流上下文")
    relative = path.relative_to(root)
    if len(relative.parts) != 5 or relative.parts[:3] != (".meiling", "staging", "topic-evaluate") or relative.name != "context.json":
        raise TopicWorkflowCoordinatorError("选题工作流上下文必须位于本轮暂存目录")
    payload = _read_json(path, "选题工作流上下文")
    legacy_keys = CONTEXT_KEYS - {"reference_decision"}
    if set(payload) == legacy_keys:
        payload = {**payload, "reference_decision": ""}
    if set(payload) != CONTEXT_KEYS or payload.get("schema_version") != "1" or payload.get("workflow_id") != "topic-evaluate":
        raise TopicWorkflowCoordinatorError("选题工作流上下文合同无效")
    if any(not isinstance(payload[key], str) for key in CONTEXT_KEYS):
        raise TopicWorkflowCoordinatorError("选题工作流上下文字段类型无效")
    return path, {key: str(payload[key]) for key in CONTEXT_KEYS}


def _input(root: Path, logical_path: str, name: str) -> dict[str, object] | None:
    if not logical_path:
        return None
    path = _inside(root, logical_path, name, required=False)
    if path is None:
        return None
    try:
        content = path.read_text(encoding="utf-8-sig")
    except (OSError, UnicodeError):
        return None
    if not content.strip():
        return None
    return {"name": name, "path": _logical(root, path), "sha256": hashlib.sha256(content.encode()).hexdigest(), "content": content}


def _topic_summary(row: Mapping[str, str]) -> dict[str, str]:
    return {field: row.get(field, "").strip() for field in TOPIC_SUMMARY_FIELDS}


def _prepare_request(**values: str) -> dict[str, str]:
    return {"action": "prepare", **{key: value for key, value in values.items() if value}}


def _old_topic_inputs(
    root: Path,
    context_path: Path,
    context: Mapping[str, str],
    rows: list[dict[str, str]],
) -> list[dict[str, object]]:
    if context["operation"] != "update":
        return []
    row = next(
        (item for item in rows if item.get("选题ID", "").strip() == context["topic_id"]),
        None,
    )
    if row is None:
        raise TopicWorkflowCoordinatorError("已确认的老题不再存在于选题库")
    snapshot = context_path.with_name("老题完整记录.json")
    _write_json(snapshot, row)
    record = _input(root, _logical(root, snapshot), "老题完整记录")
    if record is None:
        raise TopicWorkflowCoordinatorError("无法冻结老题完整记录")
    old_analysis = _input(
        root,
        row.get("选题分析记录路径", "").strip(),
        "旧版选题分析正文",
    )
    return [item for item in (record, old_analysis) if item is not None]


def _candidate_contract(
    root: Path,
    context: Path,
    identity: Mapping[str, str],
    inputs: list[dict[str, object]],
) -> dict[str, object]:
    candidate = context.with_name("选题分析候选.md")
    metadata = context.with_name("提交请求.json")
    required_submit_fields = ["action", "context_file", "analysis_candidate"]
    save_request = {
        "action": "save",
        "context_file": _logical(root, context),
        "analysis_candidate": _logical(root, candidate),
    }
    result = {
        "ok": True, "status": "stage_ready", "workflow_id": "topic-evaluate", "stage": "analysis",
        "run_id": context.parent.name,
        "topic_identity": dict(identity), "context_file": _logical(root, context), "inputs": inputs,
        "action_contract": {
            "current_action": "generate_topic_analysis_candidate",
            "required_outputs": [{"name": "analysis_candidate", "exact_path": _logical(root, candidate)}],
            "request_file_exact_path": _logical(root, metadata),
            "submit_request_schema": {"type": "object", "required": required_submit_fields, "additionalProperties": False},
            "save_request": save_request,
            "forbidden": ["读取其他语义包", "手工写入选题库", "跳过正式化执行器"],
        },
    }
    return result


def _persist_context(context_path: Path, context: Mapping[str, str]) -> None:
    """每个 prepare 分支返回前都必须落盘。

    reference_decision / reference_* 这类字段只在本次调用里 merge 进内存上下文，
    一旦某个分支提前 return 而不落盘，决策就会在下一次 prepare 丢失，
    表现为同一个门被反复重问（例如先问要不要用参考链接、答完又退回再问一遍）。
    """
    _write_json(context_path, {"schema_version": "1", "workflow_id": "topic-evaluate", **context})


def _prepare(root: Path, **raw: str) -> dict[str, object]:
    _, rows = _library(root)
    context_file = raw.get("context_file", "").strip()
    if context_file:
        context_path, context = _load_context(root, context_file)
    else:
        context = {
            "run_id": "",
            "topic_id": "",
            "topic_name": "",
            "operation": "",
            "reference_content_id": raw.get("content_id", "").strip(),
            "reference_url": raw.get("reference_url", "").strip(),
            "reference_analysis_path": raw.get("reference_analysis_path", "").strip(),
            "reference_transcript_path": raw.get("transcript_path", "").strip(),
            "reference_decision": raw.get("reference_decision", "").strip(),
        }
        context_path = _new_context(root, context)
        context["run_id"] = context_path.parent.name

    # A later prepare may supply the formal benchmark result after the context
    # was created.  Merge only supplied fields and keep the first-run context.
    for key, raw_key in (
        ("reference_content_id", "content_id"),
        ("reference_url", "reference_url"),
        ("reference_analysis_path", "reference_analysis_path"),
        ("reference_transcript_path", "transcript_path"),
        ("reference_decision", "reference_decision"),
    ):
        value = raw.get(raw_key, "").strip()
        if value:
            context[key] = value

    incoming_analysis = _input(root, context["reference_analysis_path"], "参考内容分析")
    if context["reference_url"] and not incoming_analysis and context["reference_decision"] != "skip":
        _write_json(context_path, {"schema_version": "1", "workflow_id": "topic-evaluate", **context})
        return {
            "ok": True,
            "status": "needs_reference_analysis",
            "workflow_id": "topic-evaluate",
            "run_id": context["run_id"],
            "context_file": _logical(root, context_path),
            "action_contract": {
                "continue_without_reference_request": _prepare_request(
                    context_file=_logical(root, context_path),
                    topic_id=raw.get("topic_id", "").strip(),
                    topic_name=raw.get("topic_name", "").strip(),
                    topic_intent=raw.get("topic_intent", "").strip(),
                    reference_decision="skip",
                ),
                "benchmark_then_continue_request": {
                    "next_workflow": "benchmark-content",
                    "required_inputs": ["reference_url"],
                    "after_benchmark": "将正式参考结果填入同一 context_file 后重新执行 prepare",
                },
            },
        }
    if incoming_analysis:
        _validate_reference_artifacts(
            root,
            content_id=context["reference_content_id"],
            reference_url=context["reference_url"],
            analysis=incoming_analysis,
        )

    if not context["topic_id"] and not context["topic_name"]:
        topic_id = raw.get("topic_id", "").strip()
        topic_name = raw.get("topic_name", "").strip()
        topic_intent = raw.get("topic_intent", "").strip()
        if topic_intent not in {"", "reanalyze"}:
            raise TopicWorkflowCoordinatorError(f"不支持的选题处理意图: {topic_intent}")
        if not topic_id and not topic_name:
            _write_json(context_path, {"schema_version": "1", "workflow_id": "topic-evaluate", **context})
            return {
                "ok": True,
                "status": "needs_topic_input",
                "workflow_id": "topic-evaluate",
                "run_id": context["run_id"],
                "context_file": _logical(root, context_path),
                "action_contract": {"continue_request": _prepare_request(context_file=_logical(root, context_path))},
            }

        existing = next((row for row in rows if row.get("选题ID", "").strip() == topic_id), None) if topic_id else None
        if topic_id and existing is None:
            raise TopicWorkflowCoordinatorError(f"选题ID不存在: {topic_id}")
        if existing:
            stored = existing.get("选题名称", "").strip()
            # ID 就是明确的老题身份。名称不一致时默认沿用库内名称；
            # 只有用户明确要求改名，流程才传 rename_confirm=true。
            final_name = topic_name if topic_name and raw.get("rename_confirm") == "true" else stored
            context.update(topic_id=topic_id, topic_name=final_name, operation="update")
            if not context["reference_url"]:
                context["reference_url"] = existing.get("参考内容链接", "").strip()
            if not context["reference_analysis_path"]:
                context["reference_analysis_path"] = existing.get("参考内容分析路径", "").strip()
        else:
            matches = [row for row in rows if row.get("选题名称", "").strip() == topic_name]
            reanalyze = topic_intent == "reanalyze" and raw.get("create_new") != "true"
            if reanalyze and len(matches) > 1:
                # 只有「明确重跑老题 + 名称对应多条」才需要用户指明对象。
                _persist_context(context_path, context)
                return {
                    "ok": True, "status": "needs_topic_selection", "workflow_id": "topic-evaluate",
                    "run_id": context["run_id"],
                    "context_file": _logical(root, context_path),
                    "topic_candidates": [_topic_summary(row) for row in matches],
                    "action_contract": {
                        "selection_requests": [_prepare_request(
                            context_file=_logical(root, context_path),
                            topic_id=row.get("选题ID", "").strip(), topic_name=row.get("选题名称", "").strip(),
                            topic_intent="reanalyze") for row in matches],
                        "create_new_request": _prepare_request(
                            context_file=_logical(root, context_path), topic_name=topic_name, create_new="true"),
                    },
                }
            if reanalyze and len(matches) == 1:
                row = matches[0]
                context.update(
                    topic_id=row.get("选题ID", "").strip(),
                    topic_name=row.get("选题名称", "").strip(),
                    operation="update",
                )
                if not context["reference_url"]:
                    context["reference_url"] = row.get("参考内容链接", "").strip()
                if not context["reference_analysis_path"]:
                    context["reference_analysis_path"] = row.get("参考内容分析路径", "").strip()
            else:
                # 未给 ID、也未明确要求重跑时，一律直接新建；同名不触发确认。
                context.update(topic_id=_next_id(rows), topic_name=topic_name, operation="create")

    _write_json(context_path, {"schema_version": "1", "workflow_id": "topic-evaluate", **context})
    logical_context = _logical(root, context_path)
    reference_analysis = _input(root, context["reference_analysis_path"], "参考内容分析")
    reference_transcript = _input(root, context["reference_transcript_path"], "视频文案")
    if reference_analysis:
        _validate_reference_artifacts(
            root,
            content_id=context["reference_content_id"],
            reference_url=context["reference_url"],
            analysis=reference_analysis,
        )
    # 特征卡已退出选题分析：只看正式内容分析是否到位。
    if context["reference_url"] and not reference_analysis:
        reference_boundary = "当前参考链接尚未对应可用的正式内容分析，本轮不使用参考证据。"
    else:
        reference_boundary = ""
    overview = _input(root, "我方资料/我方当前概要.md", "我方当前概要")
    overview_boundary = "" if overview else "缺乏自媒体规划，无法判断该选题是否适合当前账号"
    inputs = [item for item in [overview, reference_analysis, reference_transcript] if item]
    inputs.extend(_old_topic_inputs(root, context_path, context, rows))
    result = _candidate_contract(root, context_path, {"选题ID": context["topic_id"], "选题名称": context["topic_name"]}, inputs)
    boundaries = [item for item in (reference_boundary, overview_boundary) if item]
    if boundaries:
        # 仅供模型判断资料范围，不要求在正式正文里添加固定边界栏目。
        result["analysis_boundaries"] = boundaries
    result["reference_status"] = "ready" if reference_analysis else ("skipped" if context["reference_decision"] == "skip" else "absent")
    result["overview_status"] = "ready" if overview else "unavailable"
    return result


def _already_committed_result(
    root: Path,
    context: Mapping[str, str],
    candidate_content: str,
) -> dict[str, str] | None:
    matches = [
        row
        for row in _library(root)[1]
        if row.get("选题ID", "").strip() == context["topic_id"]
    ]
    if len(matches) != 1:
        return None
    row = matches[0]
    expected_metadata = (
        ("reference_url", "参考内容链接"),
        ("reference_analysis_path", "参考内容分析路径"),
    )
    if any(row.get(field, "").strip() != context[key] for key, field in expected_metadata):
        return None
    logical_path = row.get("选题分析记录路径", "").strip()
    formal = _inside(root, logical_path, "正式选题分析", required=False)
    if formal is None:
        return None
    normalized_candidate = candidate_content.replace("\r\n", "\n").replace("\r", "\n").strip() + "\n"
    expected_payload = normalized_candidate.encode("utf-8")
    if formal.read_bytes() != expected_payload:
        return None
    recommendation = row.get("是否建议做", "").strip()
    if recommendation not in {"建议做", "暂缓", "不建议做"}:
        return None
    return {
        "analysis_path": _logical(root, formal),
        "analysis_sha256": hashlib.sha256(expected_payload).hexdigest(),
        "recommendation": recommendation,
    }


def _backup_old_analysis(root: Path, context: Mapping[str, str]) -> str:
    if context["operation"] != "update":
        return ""
    row = next(
        (
            item
            for item in _library(root)[1]
            if item.get("选题ID", "").strip() == context["topic_id"]
        ),
        None,
    )
    if row is None:
        raise TopicWorkflowCoordinatorError("待备份的老题不存在于选题库")
    logical_path = row.get("选题分析记录路径", "").strip()
    if not logical_path:
        return ""
    source = _inside(root, logical_path, "旧版选题分析", required=False)
    if source is None:
        return ""
    payload = source.read_bytes()
    digest = hashlib.sha256(payload).hexdigest()[:16]
    backup = (
        root
        / ".meiling/backup/选题分析"
        / context["topic_id"]
        / f"{source.stem}.{digest}{source.suffix}"
    )
    if not backup.exists():
        backup.parent.mkdir(parents=True, exist_ok=True)
        fd, name = tempfile.mkstemp(prefix=f".{backup.name}.", suffix=".tmp", dir=backup.parent)
        temporary = Path(name)
        try:
            with os.fdopen(fd, "wb") as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, backup)
        finally:
            temporary.unlink(missing_ok=True)
    return _logical(root, backup)


def _save(root: Path, *, context_file: str, analysis_candidate: str) -> dict[str, object]:
    # 缺参数时给出明确提示；否则 _inside 会拿空串拼成工作区根目录，
    # 报出「候选不存在: <工作区根目录>」这种把人往路径错误上带的文案。
    if not context_file.strip():
        raise TopicWorkflowCoordinatorError("缺少选题工作流上下文路径：请按 save_request 传入 --context-file")
    if not analysis_candidate.strip():
        raise TopicWorkflowCoordinatorError("缺少选题分析候选路径：请按 save_request 传入 --analysis-candidate")
    context_path, context = _load_context(root, context_file)
    candidate = _inside(root, analysis_candidate, "选题分析候选")
    if candidate.parent != context_path.parent or candidate.name != "选题分析候选.md":
        raise TopicWorkflowCoordinatorError("选题分析候选必须位于当前暂存目录")
    metadata = {"schema_version": "2", "metadata": {"选题ID": context["topic_id"]}}
    for key, field in (("reference_url", "参考内容链接"), ("reference_analysis_path", "参考内容分析路径")):
        if context[key]: metadata["metadata"][field] = context[key]
    receipt = context_path.with_name("提交请求.json")
    _write_json(receipt, metadata)
    candidate_content = candidate.read_text(encoding="utf-8-sig")
    committed = _already_committed_result(root, context, candidate_content)
    if committed is None:
        previous_analysis_backup = _backup_old_analysis(root, context)
        try:
            result = finalize_topic_analysis(root, metadata, candidate_content, context["operation"])
        except TopicAnalysisFinalizeError as exc:
            raise TopicWorkflowCoordinatorError(str(exc)) from exc
        formal = _inside(root, str(result["analysis_path"]), "正式选题分析")
        recommendation = next(
            (
                row.get("是否建议做", "").strip()
                for row in _library(root)[1]
                if row.get("选题ID", "").strip() == context["topic_id"]
            ),
            "",
        )
        if recommendation not in {"建议做", "暂缓", "不建议做"}:
            raise TopicWorkflowCoordinatorError("正式选题记录缺少有效建议结论")
        committed = {
            "analysis_path": str(result["analysis_path"]),
            "analysis_sha256": str(result["analysis_sha256"]),
            "recommendation": recommendation,
        }
        if previous_analysis_backup:
            committed["previous_analysis_backup"] = previous_analysis_backup
        if hashlib.sha256(formal.read_bytes()).hexdigest() != committed["analysis_sha256"]:
            raise TopicWorkflowCoordinatorError("正式选题分析哈希核对失败")
    recommendation = committed["recommendation"]
    result = committed
    response = {"ok": True, "status": "completed", "commit_status": "committed", "run_id": context["run_id"], "topic_id": context["topic_id"], "recommendation": recommendation, "analysis_path": result["analysis_path"], "analysis_sha256": result["analysis_sha256"], "presentation": {"result_state": "formal", "document": {"path": result["analysis_path"]}, "display_markdown": f"[查看正式选题分析]({result['analysis_path']})"}}
    if "previous_analysis_backup" in result:
        response["previous_analysis_backup"] = result["previous_analysis_backup"]
    context_path.unlink(missing_ok=True)
    candidate.unlink(missing_ok=True)
    receipt.unlink(missing_ok=True)
    context_path.with_name("老题完整记录.json").unlink(missing_ok=True)
    return response


def coordinate_topic_workflow(workspace_root: str | Path, *, action: str, **kwargs: str) -> dict[str, object]:
    root = _root(workspace_root)
    if action == "prepare":
        return _prepare(root, **kwargs)
    if action == "save":
        return _save(root, context_file=kwargs.get("context_file", ""), analysis_candidate=kwargs.get("analysis_candidate", ""))
    raise TopicWorkflowCoordinatorError(f"不支持的动作: {action}")


def _add_flag(parser: argparse.ArgumentParser, name: str, **kwargs: object) -> None:
    """登记一个同时接受 --kebab-case 与 --snake_case 的选项。

    回执里的 action_contract / save_request 用的是下划线键名（context_file、
    analysis_candidate 这类），命令行惯用连字符。两种写法都接受，模型照抄
    回执键名拼命令时不会再撞 unrecognized arguments。
    """
    options = [f"--{name.replace('_', '-')}"]
    if f"--{name}" not in options:
        options.append(f"--{name}")
    parser.add_argument(*options, dest=name, **kwargs)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="选题分析阶段协调器")
    _add_flag(parser, "workspace_root", required=True)
    _add_flag(parser, "action", choices=("prepare", "save"), required=True)
    for name in ("topic_id", "topic_name", "topic_intent", "content_id", "reference_url", "reference_analysis_path", "transcript_path", "reference_decision", "create_new", "topic_confirm", "rename_confirm", "context_file", "analysis_candidate"):
        _add_flag(parser, name, default="")
    args = parser.parse_args(argv)
    try:
        result = coordinate_topic_workflow(args.workspace_root, action=args.action, **{name: getattr(args, name) for name in vars(args) if name not in {"workspace_root", "action"}})
    except TopicWorkflowCoordinatorError as exc:
        result = {"ok": False, "status": "blocked", "reason": str(exc)}
    # 与项目其他执行单元（内容生产阶段协调器、自媒体规划正式保存、选题分析正式化执行器）保持一致：
    # 成功回执走 stdout，错误回执走 stderr。
    print(json.dumps(result, ensure_ascii=False), file=sys.stdout if result.get("ok") else sys.stderr)
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
