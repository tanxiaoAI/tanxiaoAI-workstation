#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import hashlib
import importlib.util
import io
import json
import re
import sys
import time
from pathlib import Path
from typing import Mapping, Sequence

from 内容特征词典 import (
    ContentFeatureDictionaryError,
    build_content_category_scope,
    build_opening_label_scope,
    load_feature_catalog,
)


class StagePreparationError(RuntimeError):
    """Raised when a content-production stage cannot be prepared deterministically."""


CONTRACT_VERSION = "1"
CONTENT_LIBRARY_PATH = "我方资料/我方内容库.csv"
TOPIC_LIBRARY_PATH = "我方资料/选题库.csv"
CURRENT_SUMMARY_PATH = "我方资料/我方当前概要.md"

CONTENT_REQUIRED_COLUMNS = {
    "内容ID",
    "选题ID",
    "选题名称",
    "内容目录路径",
    "媒介形式",
    "当前状态",
    "内容生产记录路径",
}
TOPIC_REQUIRED_COLUMNS = {
    "选题ID",
    "选题名称",
    "当前状态",
    "是否建议做",
    "参考内容分析路径",
    "选题分析记录路径",
    "内容生产记录路径",
}
RESUMABLE_CONTENT_STATUSES = {"生产中"}
FORMAL_TOPIC_STATUSES = {"已完成分析", "已生产"}


def _json_text(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _load_state_executor():
    unit_dir = Path(__file__).resolve().parent
    executor_path = unit_dir / "内容生产状态执行器.py"
    spec = importlib.util.spec_from_file_location(
        "content_production_state_for_stage_preparation",
        executor_path,
    )
    if spec is None or spec.loader is None:
        raise StagePreparationError("无法加载内容生产状态执行器")
    module = importlib.util.module_from_spec(spec)
    previous_path = list(sys.path)
    sys.path.insert(0, str(unit_dir))
    try:
        spec.loader.exec_module(module)
    except Exception as exc:
        raise StagePreparationError(f"无法加载内容生产状态执行器: {exc}") from exc
    finally:
        sys.path[:] = previous_path
    return module


def _workspace_root(value: str | Path) -> Path:
    root = Path(value).expanduser().resolve()
    if not root.is_dir():
        raise StagePreparationError(f"工作区根目录不存在或不是目录: {root}")
    return root


def _resolve_workspace_file(
    root: Path,
    value: str | Path,
    label: str,
    *,
    required: bool = True,
) -> Path | None:
    raw_path = Path(value).expanduser()
    candidate = raw_path if raw_path.is_absolute() else root / raw_path
    lexical = candidate.absolute()
    resolved = lexical.resolve()
    try:
        resolved.relative_to(root)
    except ValueError as exc:
        raise StagePreparationError(f"{label}必须位于当前工作区内: {value}") from exc

    current = lexical
    workspace_components: list[Path] = []
    while current != current.parent and current.resolve() != root:
        workspace_components.append(current)
        current = current.parent
    if current.resolve() != root:
        raise StagePreparationError(f"{label}无法建立工作区内路径边界: {value}")
    if any(component.is_symlink() for component in workspace_components):
        raise StagePreparationError(f"{label}不得经过符号链接: {value}")
    if not resolved.is_file():
        if not required and not resolved.exists():
            return None
        raise StagePreparationError(f"{label}不存在或不是普通文件: {resolved}")
    return resolved


def _logical_path(root: Path, path: Path) -> str:
    return path.resolve().relative_to(root).as_posix()


def _read_bytes(path: Path, label: str) -> bytes:
    try:
        return path.read_bytes()
    except OSError as exc:
        raise StagePreparationError(f"无法读取{label}{path}: {exc}") from exc


def _read_text(path: Path, label: str) -> tuple[bytes, str]:
    source = _read_bytes(path, label)
    try:
        return source, source.decode("utf-8-sig")
    except UnicodeError as exc:
        raise StagePreparationError(f"{label}不是有效的UTF-8文本: {path}") from exc


def _input(
    *,
    name: str,
    path: str,
    selector: str,
    source: bytes,
    content: str,
) -> dict[str, object]:
    return {
        "name": name,
        "path": path,
        "selector": selector,
        "source_sha256": _sha256(source),
        "content_sha256": _sha256(content.encode("utf-8")),
        "content": content,
    }


def _append_opening_label_input(inputs: list[dict[str, object]], root: Path) -> None:
    try:
        catalog = load_feature_catalog(root)
        options = build_opening_label_scope(catalog)
    except ContentFeatureDictionaryError as exc:
        if "feature_dictionary_missing" in str(exc):
            # 开头标签只是定位阶段的参考（见 执行流程/内容定位流程.md 2.2 的 inputs 说明）。
            # 词典文件不存在属于正常情况——用户还没做过对标内容分析时就没有词典。
            # 这时降级为空列表继续建档，不能把内容生产卡在门口。
            options = []
        else:
            # 文件在但表头或内容损坏，属于真实的材料问题，照旧报错。
            raise StagePreparationError(f"无法读取开头标签范围: {exc}") from exc
    _append_derived_input(
        inputs,
        name="opening_label_options",
        logical_path="沉淀结论/内容特征词典.csv",
        source=json.dumps(options, ensure_ascii=False, sort_keys=True).encode("utf-8"),
        selector="特征小类=开头标签",
        value=options,
    )


def _append_content_category_input(inputs: list[dict[str, object]], root: Path) -> None:
    try:
        catalog = load_feature_catalog(root)
        options = build_content_category_scope(catalog)
    except ContentFeatureDictionaryError as exc:
        if "feature_dictionary_missing" in str(exc):
            # 内容类别的取值同样来自内容特征词典（见 执行流程/内容定位流程.md 的 inputs 说明）。
            # 词典文件不存在属于正常情况——用户还没做过对标内容分析时就没有词典。
            # 这时降级为空列表继续建档，由定位阶段按“信息不足”处理，不能把内容生产卡在门口。
            options = []
        else:
            # 文件在但表头或内容损坏，属于真实的材料问题，照旧报错。
            raise StagePreparationError(f"无法读取内容类别范围: {exc}") from exc
    _append_derived_input(
        inputs,
        name="content_category_options",
        logical_path="沉淀结论/内容特征词典.csv",
        source=json.dumps(options, ensure_ascii=False, sort_keys=True).encode("utf-8"),
        selector="特征小类=内容类别",
        value=options,
    )


def _append_file_input(
    inputs: list[dict[str, object]],
    root: Path,
    logical_path: str,
    *,
    name: str,
    selector: str = "all",
    required: bool = True,
) -> None:
    path = _resolve_workspace_file(root, logical_path, name, required=required)
    if path is None:
        return
    source, content = _read_text(path, name)
    if not content.strip() and not required:
        return
    inputs.append(
        _input(
            name=name,
            path=_logical_path(root, path),
            selector=selector,
            source=source,
            content=content,
        )
    )


def _overview_context(inputs: Sequence[Mapping[str, object]]) -> dict[str, object]:
    summary = next(
        (item for item in inputs if item.get("name") == "current_summary"),
        None,
    )
    if summary is None:
        return {
            "available": False,
            "path": CURRENT_SUMMARY_PATH,
            "reason": "missing_or_empty",
        }
    return {
        "available": True,
        "path": str(summary.get("path", CURRENT_SUMMARY_PATH)),
        "source_sha256": str(summary.get("source_sha256", "")),
        "content_sha256": str(summary.get("content_sha256", "")),
    }


def _read_csv(
    root: Path,
    logical_path: str,
    label: str,
    required_columns: set[str],
) -> tuple[Path, bytes, list[dict[str, str]]]:
    path = _resolve_workspace_file(root, logical_path, label)
    assert path is not None
    source, text = _read_text(path, label)
    try:
        reader = csv.DictReader(io.StringIO(text, newline=""))
        fields = reader.fieldnames
        if fields is None:
            raise StagePreparationError(f"{label}缺少表头")
        if any(not field for field in fields) or len(fields) != len(set(fields)):
            raise StagePreparationError(f"{label}包含空表头或重复表头")
        missing = sorted(required_columns - set(fields))
        if missing:
            raise StagePreparationError(f"{label}缺少字段: {', '.join(missing)}")
        return path, source, [dict(row) for row in reader]
    except csv.Error as exc:
        raise StagePreparationError(f"无法解析{label}{path}: {exc}") from exc


def _unique_row(
    rows: Sequence[Mapping[str, str]],
    key: str,
    value: str,
    label: str,
) -> dict[str, str]:
    matches = [dict(row) for row in rows if row.get(key) == value]
    if len(matches) != 1:
        raise StagePreparationError(f"{label}必须且只能存在一条{key}={value}的记录，实际为{len(matches)}条")
    return matches[0]


def _append_row_input(
    inputs: list[dict[str, object]],
    *,
    name: str,
    logical_path: str,
    source: bytes,
    selector: str,
    row: Mapping[str, str],
) -> None:
    inputs.append(
        _input(
            name=name,
            path=logical_path,
            selector=selector,
            source=source,
            content=_json_text(dict(row)),
        )
    )


def _append_derived_input(
    inputs: list[dict[str, object]],
    *,
    name: str,
    logical_path: str,
    source: bytes,
    selector: str,
    value: object,
) -> None:
    inputs.append(
        _input(
            name=name,
            path=logical_path,
            selector=selector,
            source=source,
            content=_json_text(value),
        )
    )


def _append_record_section(
    inputs: list[dict[str, object]],
    *,
    state_executor,
    record_path: Path,
    record_source: bytes,
    record_text: str,
    state: Mapping[str, object],
    stage_name: str,
    input_name: str,
    skip_if_unstarted: bool = False,
) -> None:
    stages = state.get("阶段")
    if not isinstance(stages, Mapping) or not isinstance(stages.get(stage_name), Mapping):
        raise StagePreparationError(f"状态缺少阶段: {stage_name}")
    stage = stages[stage_name]
    if skip_if_unstarted and stage.get("状态") == "未开始":
        # 上游重做后，下游旧版本只保留作历史记录，不能作为本轮生成输入。
        return
    version = int(stage.get("版本", 0))
    if version <= 0:
        return
    section_names = state_executor.RECORD_SECTIONS[stage_name]
    sections: list[str] = []
    for section_name in section_names:
        body = state_executor._extract_record_version(record_text, section_name, version)
        if body:
            sections.append(f"## {section_name}\n\n### v{version}\n\n{body.strip()}")
    if not sections:
        raise StagePreparationError(f"内容生产记录缺少{stage_name} v{version}正式区块")
    inputs.append(
        _input(
            name=input_name,
            path=record_path.as_posix(),
            selector=f"{stage_name}/v{version}",
            source=record_source,
            content="\n\n".join(sections),
        )
    )


def _minimal_content_candidate(row: Mapping[str, str]) -> dict[str, str]:
    return {
        key: row.get(key, "")
        for key in (
            "内容ID",
            # 标题是用户识别「这条是哪条内容」的唯一线索；独立生产时选题名称是空的，
            # 只给内容ID 用户没法选。
            "标题",
            "选题ID",
            "选题名称",
            "媒介形式",
            "当前状态",
            "内容目录路径",
            "内容生产记录路径",
        )
    }


def _minimal_topic_candidate(row: Mapping[str, str]) -> dict[str, str]:
    return {
        key: row.get(key, "")
        for key in (
            "选题ID",
            "选题名称",
            "当前状态",
            "是否建议做",
            "判断理由",
            "选题分析记录路径",
        )
    }


def _base_indexes(root: Path):
    content_path, content_source, content_rows = _read_csv(
        root,
        CONTENT_LIBRARY_PATH,
        "我方内容库",
        CONTENT_REQUIRED_COLUMNS,
    )
    topic_path, topic_source, topic_rows = _read_csv(
        root,
        TOPIC_LIBRARY_PATH,
        "选题库",
        TOPIC_REQUIRED_COLUMNS,
    )
    return {
        "content": (content_path, content_source, content_rows),
        "topic": (topic_path, topic_source, topic_rows),
    }


def _prepare_selection(root: Path, indexes: Mapping[str, object]) -> dict[str, object]:
    content_rows = indexes["content"][2]
    topic_rows = indexes["topic"][2]
    return {
        "preparation_mode": "selection",
        "route": {"mode": "selection"},
        "content_identity": {},
        "inputs": [],
        "content_candidates": [
            _minimal_content_candidate(row)
            for row in content_rows
            if row.get("当前状态") in RESUMABLE_CONTENT_STATUSES
        ],
        "topic_candidates": [
            _minimal_topic_candidate(row)
            for row in topic_rows
            if row.get("当前状态") in FORMAL_TOPIC_STATUSES
        ],
    }


def _prepare_intake(root: Path, indexes: Mapping[str, object], topic_id: str) -> dict[str, object]:
    content_path, content_source, content_rows = indexes["content"]
    topic_path, topic_source, topic_rows = indexes["topic"]
    topic_row = _unique_row(topic_rows, "选题ID", topic_id, "选题库") if topic_id else {}
    if topic_row.get("当前状态", "").strip() not in FORMAL_TOPIC_STATUSES:
        return {
            "preparation_mode": "topic_evaluation_required",
            "route": {"mode": "topic_evaluation_required", "选题ID": topic_id},
            "content_identity": {
                "选题ID": topic_id,
                "选题名称": topic_row.get("选题名称", ""),
            },
            "inputs": [],
        }
    analysis_path = topic_row.get("选题分析记录路径", "").strip()
    if not analysis_path:
        raise StagePreparationError(f"{topic_id} 缺少正式选题分析路径")

    inputs: list[dict[str, object]] = []
    _append_file_input(
        inputs,
        root,
        CURRENT_SUMMARY_PATH,
        name="current_summary",
        required=False,
    )
    if topic_id:
        _append_row_input(
            inputs,
            name="topic_row",
            logical_path=_logical_path(root, topic_path),
            source=topic_source,
            selector=f"选题ID={topic_id}",
            row=topic_row,
        )
    _append_file_input(inputs, root, analysis_path, name="topic_analysis")
    resume_candidates = [
        _minimal_content_candidate(row)
        for row in content_rows
        if row.get("选题ID") == topic_id and row.get("当前状态") in RESUMABLE_CONTENT_STATUSES
    ]
    if resume_candidates:
        _append_derived_input(
            inputs,
            name="resume_candidates",
            logical_path=_logical_path(root, content_path),
            source=content_source,
            selector=f"选题ID={topic_id};当前状态=生产中",
            value=resume_candidates,
        )
    return {
        "preparation_mode": "intake",
        "route": {"mode": "intake", "选题ID": topic_id},
        "content_identity": {"选题ID": topic_id, "选题名称": topic_row.get("选题名称", "")},
        "topic_evaluation": {
            "recommendation": topic_row.get("是否建议做", "").strip(),
            "reason": topic_row.get("判断理由", "").strip(),
        },
        "inputs": inputs,
        "resume_candidates": resume_candidates,
    }


def _state_path_from_content(root: Path, content_row: Mapping[str, str]) -> Path:
    content_dir = content_row.get("内容目录路径", "").strip()
    if not content_dir:
        raise StagePreparationError("我方内容库当前记录缺少内容目录路径")
    path = _resolve_workspace_file(
        root,
        f"{content_dir.rstrip('/')}/过程记录/当前阶段状态.json",
        "当前阶段状态",
    )
    assert path is not None
    return path


def _prepare_state(
    root: Path,
    indexes: Mapping[str, object],
    state_path: Path,
    *,
    requested_content_id: str = "",
) -> dict[str, object]:
    state_executor = _load_state_executor()
    state_source = _read_bytes(state_path, "当前阶段状态")
    try:
        state = json.loads(state_source.decode("utf-8-sig"))
        state_executor.validate_state(state)
        state_executor._ensure_target_matches_state(state_path, state, root)
        state_executor._validate_workspace_artifacts(state, root)
        state_executor._validate_completion_references(
            state,
            root,
            require_ready_status=False,
        )
    except Exception as exc:
        raise StagePreparationError(f"正式内容生产状态校验失败: {exc}") from exc
    if not isinstance(state, Mapping):
        raise StagePreparationError("正式内容生产状态不是对象")
    route = state_executor._route_from_state(state)
    content_id = str(state.get("内容ID", ""))
    topic_id = str(state.get("选题ID", ""))
    if requested_content_id and content_id != requested_content_id:
        raise StagePreparationError("状态中的内容ID与请求内容ID不一致")

    content_path, content_source, content_rows = indexes["content"]
    topic_path, topic_source, topic_rows = indexes["topic"]
    content_row = _unique_row(content_rows, "内容ID", content_id, "我方内容库")
    topic_row = _unique_row(topic_rows, "选题ID", topic_id, "选题库") if topic_id else {}
    expected = {
        "选题ID": topic_id,
        "内容目录路径": str(state.get("当前内容目录", "")),
        "内容生产记录路径": str(state.get("内容生产记录路径", "")),
    }
    mismatches = [key for key, value in expected.items() if content_row.get(key) != value]
    if mismatches:
        raise StagePreparationError(f"我方内容库与正式状态不一致: {', '.join(mismatches)}")

    record_path = _resolve_workspace_file(
        root,
        str(state["内容生产记录路径"]),
        "内容生产记录",
    )
    assert record_path is not None
    record_source, record_text = _read_text(record_path, "内容生产记录")
    state_logical_path = _logical_path(root, state_path)
    record_logical_path = _logical_path(root, record_path)

    current_stage = str(state.get("当前阶段", ""))
    if current_stage not in state_executor.STAGES:
        raise StagePreparationError(f"当前阶段不在本地工作流中: {current_stage}")

    inputs: list[dict[str, object]] = []
    _append_derived_input(
        inputs,
        name="state_route",
        logical_path=state_logical_path,
        source=state_source,
        selector="route",
        value=route,
    )
    _append_derived_input(
        inputs,
        name="state_machine_data",
        logical_path=state_logical_path,
        source=state_source,
        selector="机器数据+阶段",
        value={
            "阶段": state.get("阶段"),
            "机器数据": state.get("机器数据"),
        },
    )
    _append_row_input(
        inputs,
        name="content_row",
        logical_path=_logical_path(root, content_path),
        source=content_source,
        selector=f"内容ID={content_id}",
        row=content_row,
    )
    if topic_id:
        _append_row_input(
            inputs,
            name="topic_row",
            logical_path=_logical_path(root, topic_path),
            source=topic_source,
            selector=f"选题ID={topic_id}",
            row=topic_row,
        )

    if current_stage == "内容定位":
        _append_file_input(inputs, root, CURRENT_SUMMARY_PATH, name="current_summary", required=False)
        analysis_path = (
            topic_row.get("选题分析记录路径", "").strip()
            if topic_id
            else content_row.get("参考内容分析路径", "").strip()
        )
        if analysis_path:
            _append_file_input(
                inputs,
                root,
                analysis_path,
                name="topic_analysis" if topic_id else "reference_content_analysis",
            )
        _append_opening_label_input(inputs, root)
        _append_content_category_input(inputs, root)
        _append_record_section(
            inputs,
            state_executor=state_executor,
            record_path=Path(record_logical_path),
            record_source=record_source,
            record_text=record_text,
            state=state,
            stage_name="内容定位",
            input_name="current_positioning",
            skip_if_unstarted=True,
        )
    elif current_stage == "内容初稿":
        for stage_name, input_name in (
            ("内容定位", "confirmed_positioning"),
            ("内容初稿", "current_draft"),
        ):
            _append_record_section(
                inputs,
                state_executor=state_executor,
                record_path=Path(record_logical_path),
                record_source=record_source,
                record_text=record_text,
                state=state,
                stage_name=stage_name,
                input_name=input_name,
                skip_if_unstarted=input_name.startswith("current_"),
            )
    elif current_stage == "内容终稿":
        for stage_name, input_name in (
            ("内容定位", "confirmed_positioning"),
            ("内容初稿", "confirmed_draft"),
        ):
            _append_record_section(
                inputs,
                state_executor=state_executor,
                record_path=Path(record_logical_path),
                record_source=record_source,
                record_text=record_text,
                state=state,
                stage_name=stage_name,
                input_name=input_name,
            )
        stages = state.get("阶段")
        final_stage = stages.get("内容终稿") if isinstance(stages, Mapping) else None
        if isinstance(final_stage, Mapping) and int(final_stage.get("版本", 0)) > 0:
            _append_record_section(
                inputs,
                state_executor=state_executor,
                record_path=Path(record_logical_path),
                record_source=record_source,
                record_text=record_text,
                state=state,
                stage_name="内容终稿",
                input_name="current_final",
                skip_if_unstarted=False,
            )

    return {
        "preparation_mode": "stage",
        "stage_name": current_stage,
        "route": route,
        "content_identity": {
            "内容ID": content_id,
            "选题ID": topic_id,
            "媒介形式": content_row.get("媒介形式", ""),
            "内容目录路径": content_row.get("内容目录路径", ""),
            "content_task": str(state.get("机器数据", {}).get("内容任务", ""))
            if isinstance(state.get("机器数据"), Mapping)
            else "",
        },
        "inputs": inputs,
        **({"overview_context": _overview_context(inputs)} if current_stage == "内容定位" else {}),
    }


def prepare_content_stage(
    workspace_root: str | Path,
    *,
    state_file: str | Path | None = None,
    content_id: str = "",
    topic_id: str = "",
) -> dict[str, object]:
    root = _workspace_root(workspace_root)
    selectors = int(state_file is not None) + int(bool(content_id.strip())) + int(bool(topic_id.strip()))
    if selectors > 1:
        raise StagePreparationError("--state-file、--content-id和--topic-id只能选择一个")
    indexes = _base_indexes(root)
    if topic_id.strip():
        payload = _prepare_intake(root, indexes, topic_id.strip())
    elif content_id.strip():
        content_rows = indexes["content"][2]
        content_row = _unique_row(content_rows, "内容ID", content_id.strip(), "我方内容库")
        payload = _prepare_state(
            root,
            indexes,
            _state_path_from_content(root, content_row),
            requested_content_id=content_id.strip(),
        )
    elif state_file is not None:
        state_path = _resolve_workspace_file(root, state_file, "当前阶段状态")
        assert state_path is not None
        payload = _prepare_state(
            root,
            indexes,
            state_path,
        )
    else:
        payload = _prepare_selection(root, indexes)

    result = {
        "ok": True,
        "contract_version": CONTRACT_VERSION,
        **payload,
    }
    result["preparation_sha256"] = _sha256(_json_text(result).encode("utf-8"))
    return result


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="只读准备内容生产当前阶段的确定性输入")
    parser.add_argument("--workspace-root", required=True, type=Path, help="当前工作区根目录绝对路径")
    selector = parser.add_mutually_exclusive_group()
    selector.add_argument("--state-file", type=Path, help="正式当前阶段状态.json路径")
    selector.add_argument("--content-id", default="", help="从我方内容库唯一定位正式状态")
    selector.add_argument("--topic-id", default="", help="准备建档输入并返回已有生产中内容")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    started_at = time.perf_counter()
    try:
        result = prepare_content_stage(
            args.workspace_root,
            state_file=args.state_file,
            content_id=args.content_id,
            topic_id=args.topic_id,
        )
        result["elapsed_ms"] = round((time.perf_counter() - started_at) * 1000, 2)
        print(json.dumps(result, ensure_ascii=False))
        return 0
    except StagePreparationError as exc:
        print(
            json.dumps(
                {
                    "ok": False,
                    "error_code": "content_stage_preparation_failed",
                    "message": str(exc),
                    "elapsed_ms": round((time.perf_counter() - started_at) * 1000, 2),
                },
                ensure_ascii=False,
            ),
            file=sys.stderr,
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
