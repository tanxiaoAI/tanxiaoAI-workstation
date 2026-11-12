#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import hashlib
import io
import importlib.util
import json
import re
import secrets
import sys
import time
from pathlib import Path
from typing import Mapping


class ContentStageCoordinatorError(RuntimeError):
    """Raised when the content-production task dispatcher cannot continue."""


# 内容生产主链路：内容定位 → 内容初稿 → 内容终稿；内容审查是独立的手动旁路。
CONTRACT_VERSION = "6"
INPUT_SNAPSHOT_SCHEMA_VERSION = 1
EMPTY_CONTENT_RECORD = (
    "# 内容生产记录\n\n"
    "## 内容定位\n\n"
    "## 内容初稿\n\n"
    "## 内容终稿\n"
)
TRANSITION_ACTIONS = {"rework-stage", "block", "unblock"}
STATUS_SEMANTICS = {
    "进行中": "in_progress",
    "等待用户确认": "waiting_for_user",
    "被阻断": "retryable_failed",
    "已完成": "completed",
}
CANONICAL_MEDIA_FORMS = ("视频", "图文", "文章", "其他")
CONTENT_TASKS = ("流量", "涨粉", "交易")
BENCHMARK_CONTENT_LIBRARY_PATH = "对标资料/对标内容库.csv"
BENCHMARK_REFERENCE_REQUIRED_COLUMNS = {
    "内容ID",
    "标题",
    "内容分析路径",
}
MEDIA_FORM_ALIASES = {
    "口播": "视频",
    "口播视频": "视频",
    "视频口播": "视频",
    "真人口播": "视频",
    "短视频": "视频",
    "视频": "视频",
    "图文": "图文",
    "图文内容": "图文",
    "图文笔记": "图文",
    "图片": "图文",
    "文章": "文章",
    "文章内容": "文章",
    "长文": "文章",
    "其他": "其他",
}
NO_FORMAL_TOPIC_MESSAGE = (
    "当前没有可恢复的内容生产任务。你可以提供选题ID进入关联选题生产，或直接提供内容需求进入独立生产；独立生产不要求先创建选题。"
)
TOPIC_EVALUATION_REQUIRED_MESSAGE = (
    "这个选题还没有正式的分析结论，暂时不能进入内容生产。请先完成这条选题的选题分析，再回来继续。"
)
POSITIONING_CORE_FIELDS = (
    "主题（这条内容主要讲什么）",
    "目标用户（主要写给谁）",
    "核心痛点（他们现在卡在哪里）",
    "收益承诺（看完能得到什么）",
    "核心命题（最后希望用户相信什么）",
    "切口（从哪个具体角度开始讲）",
)


def _nonrecommended_message(recommendation: str, reason: str) -> tuple[str, str]:
    effective_reason = reason or "请查看正式选题分析中的核心判断。"
    if recommendation == "暂缓":
        return (
            "这条选题当前的分析结论是“暂缓”。\n\n"
            f"主要原因是：{effective_reason}\n\n"
            "目前不建议直接进入内容生产，但这不是禁止你继续。\n\n"
            "请选择：\n\n"
            "**继续内容生产**  \n"
            "按当前选题进入内容生产。\n\n"
            "**讨论暂缓原因**  \n"
            "先继续讨论为什么暂缓，以及是否需要调整选题后再决定。",
            "讨论暂缓原因",
        )
    return (
        "这条选题当前的分析结论是“不建议做”。\n\n"
        f"主要原因是：{effective_reason}\n\n"
        "目前不建议进入内容生产，但这不是禁止你继续。\n\n"
        "请选择：\n\n"
        "**继续内容生产**  \n"
        "按当前选题进入内容生产。\n\n"
        "**讨论不建议做的原因**  \n"
        "先继续讨论为什么不建议做，以及是否需要调整选题后再决定。",
        "讨论不建议做的原因",
    )

STAGE_CONTRACTS = {
    ("内容定位", ""): {
        "id": "positioning",
        "name": "内容定位",
        "mode": "",
        "flow_file": "执行流程/内容定位流程.md",
        "record_sections": ["内容定位"],
        "requires_confirmation": True,
    },
    ("内容初稿", ""): {
        "id": "draft",
        "name": "内容初稿",
        "mode": "",
        "flow_file": "执行流程/内容初稿流程.md",
        "record_sections": ["内容初稿"],
        "requires_confirmation": True,
    },
    ("内容终稿", ""): {
        "id": "final",
        "name": "内容终稿",
        "mode": "",
        "flow_file": "执行流程/内容终稿流程.md",
        "record_sections": ["内容终稿"],
        "requires_confirmation": True,
    },
}

# 只定义协调器能校验和执行的机器协议值；用户选择的业务含义由当前 workflow 定义。
ACCEPTED_DECISIONS = {
    ("内容定位", ""): ["confirm"],
    ("内容初稿", ""): ["confirm", "revise_current", "rework_upstream"],
    ("内容终稿", ""): ["confirm", "revise_current", "rework_upstream"],
}
REVIEW_REPORT_SECTIONS = ["通用审查"]

# 候选文件的结构（字段、表格、固定标记）由当前阶段流程文档指定的提示词定义。
# minimal_example 只表示二级区块骨架；不写清这一点，模型会照着它写出被校验拒绝的候选。
STRUCTURE_CONTRACT_NOTE = (
    "候选的字段、表格与固定标记以当前阶段流程文档指定的提示词为准；"
    "minimal_example 只表示二级区块骨架，不代表完整结构。"
)


def _load_sibling(file_name: str, module_name: str):
    path = Path(__file__).resolve().parent / file_name
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        raise ContentStageCoordinatorError(f"无法加载执行单元: {file_name}")
    module = importlib.util.module_from_spec(spec)
    previous_path = list(sys.path)
    sys.path.insert(0, str(path.parent))
    try:
        spec.loader.exec_module(module)
    except Exception as exc:
        raise ContentStageCoordinatorError(f"无法加载执行单元{file_name}: {exc}") from exc
    finally:
        sys.path[:] = previous_path
    return module


def _workspace_root(value: str | Path) -> Path:
    root = Path(value).expanduser().resolve()
    if not root.is_dir():
        raise ContentStageCoordinatorError(f"工作区根目录不存在或不是目录: {root}")
    return root


def _workspace_file(root: Path, value: str | Path, label: str) -> Path:
    raw = Path(value).expanduser()
    candidate = raw if raw.is_absolute() else root / raw
    resolved = candidate.resolve()
    try:
        resolved.relative_to(root)
    except ValueError as exc:
        raise ContentStageCoordinatorError(f"{label}必须位于当前工作区内: {value}") from exc
    if not resolved.is_file():
        raise ContentStageCoordinatorError(f"{label}不存在或不是普通文件: {resolved}")
    return resolved


def _logical_path(root: Path, path: Path) -> str:
    return path.resolve().relative_to(root).as_posix()


def _state_file_from_route(root: Path, route: Mapping[str, object]) -> str:
    content_dir = str(route.get("当前内容目录", "")).strip().rstrip("/")
    if not content_dir:
        raise ContentStageCoordinatorError("阶段准备结果缺少当前内容目录")
    state_file = f"{content_dir}/过程记录/当前阶段状态.json"
    _workspace_file(root, state_file, "当前阶段状态")
    return state_file


def _stage_contract(stage_name: str, *, mode: str = "") -> dict[str, object]:
    normalized_stage = str(stage_name or "").strip()
    normalized_mode = str(mode or "").strip()
    contract = STAGE_CONTRACTS.get((normalized_stage, normalized_mode))
    if contract is None:
        suffix = f"/{normalized_mode}" if normalized_mode else ""
        raise ContentStageCoordinatorError(f"未声明的内容生产阶段: {normalized_stage}{suffix}")
    return {
        key: list(value) if isinstance(value, list) else value
        for key, value in contract.items()
    }


def _normalize_media_form(value: str) -> str:
    normalized = str(value or "").strip()
    if not normalized:
        return ""
    canonical = MEDIA_FORM_ALIASES.get(normalized)
    if canonical:
        return canonical
    raise ContentStageCoordinatorError(
        "媒介形式无法归一，只允许视频、图文、文章、其他，口播会自动归入视频"
    )


def _validate_benchmark_reference(
    root: Path,
    *,
    reference_content_id: str,
    reference_analysis_path: str,
) -> dict[str, str] | None:
    has_reference = any(
        value.strip()
        for value in (reference_content_id, reference_analysis_path)
    )
    if not has_reference:
        return None
    if not reference_content_id.strip() or not reference_analysis_path.strip():
        raise ContentStageCoordinatorError(
            "对标内容直达生产必须同时提供参考内容ID和正式内容分析路径"
        )

    library_path = _workspace_file(root, BENCHMARK_CONTENT_LIBRARY_PATH, "对标内容库")
    try:
        with library_path.open("r", encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle)
            fields = set(reader.fieldnames or [])
            rows = [dict(row) for row in reader]
    except (OSError, UnicodeError, csv.Error) as exc:
        raise ContentStageCoordinatorError(f"无法读取对标内容库以核验参考内容: {exc}") from exc
    missing = sorted(BENCHMARK_REFERENCE_REQUIRED_COLUMNS - fields)
    if missing:
        raise ContentStageCoordinatorError(
            "对标内容库缺少参考内容核验字段: " + ", ".join(missing)
        )
    matches = [
        row for row in rows
        if row.get("内容ID", "").strip() == reference_content_id.strip()
    ]
    if len(matches) != 1:
        raise ContentStageCoordinatorError(
            f"参考内容ID必须在对标内容库中唯一，实际匹配到{len(matches)}条"
        )
    row = matches[0]
    expected_analysis = row.get("内容分析路径", "").strip()
    if not expected_analysis:
        raise ContentStageCoordinatorError("参考内容尚未保存正式内容分析，不能直接进入内容生产")
    requested_analysis = _workspace_file(root, reference_analysis_path, "参考内容分析")
    formal_analysis = _workspace_file(root, expected_analysis, "对标内容库中的正式内容分析")
    if requested_analysis != formal_analysis:
        raise ContentStageCoordinatorError("参考内容分析路径与对标内容库登记的正式路径不一致")

    # 借鉴判断从内容分析的「借鉴判断」表读取。
    return {
        "content_id": reference_content_id.strip(),
        "title": row.get("标题", "").strip(),
        "analysis_path": _logical_path(root, requested_analysis),
    }


def _normalized_positioning_core(markdown: str) -> dict[str, str]:
    rows: dict[str, str] = {}
    for line in markdown.splitlines():
        if not line.lstrip().startswith("|"):
            continue
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        if len(cells) >= 2 and cells[0] in POSITIONING_CORE_FIELDS:
            rows[cells[0]] = " ".join(cells[1].split())
    return {field: rows.get(field, "") for field in POSITIONING_CORE_FIELDS}


def _positioning_core_sha256(markdown: str) -> str:
    payload = json.dumps(
        _normalized_positioning_core(markdown),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _positioning_media_form(markdown: str) -> str:
    values: list[str] = []
    for line in markdown.splitlines():
        if not line.lstrip().startswith("|"):
            continue
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        if len(cells) >= 2 and cells[0] == "媒介形式":
            values.append(cells[1])
    if not values:
        return ""
    normalized = {_normalize_media_form(value) for value in values}
    if len(normalized) != 1:
        raise ContentStageCoordinatorError("已确认内容定位中的媒介形式不唯一")
    return normalized.pop()


def _positioning_content_task(markdown: str) -> str:
    values: list[str] = []
    for line in markdown.splitlines():
        if not line.lstrip().startswith("|"):
            continue
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        if len(cells) >= 2 and cells[0] == "主目标侧重":
            values.append(cells[1])
    if len(values) != 1 or values[0] not in CONTENT_TASKS:
        raise ContentStageCoordinatorError("定位方案必须且只能填写一个有效的主目标侧重")
    return values[0]


def _display_identity(
    root: Path,
    state: Mapping[str, object],
    *,
    media_form: str = "",
    content_task: str = "",
) -> dict[str, str]:
    content_id = str(state.get("内容ID", "")).strip()
    topic_id = str(state.get("选题ID", "")).strip()
    csv_path = root / "我方资料" / "我方内容库.csv"
    try:
        with csv_path.open("r", encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle)
            rows = [dict(row) for row in reader]
    except (OSError, UnicodeError, csv.Error) as exc:
        raise ContentStageCoordinatorError(f"无法读取我方内容库以生成展示信息: {exc}") from exc
    matches = [
        row
        for row in rows
        if row.get("内容ID") == content_id and row.get("选题ID") == topic_id
    ]
    if len(matches) != 1:
        raise ContentStageCoordinatorError("我方内容库无法唯一定位当前内容以生成展示信息")
    row = matches[0]
    machine_data = state.get("机器数据")
    state_task = str(machine_data.get("内容任务", "")).strip() if isinstance(machine_data, Mapping) else ""
    return {
        "content_name": str(row.get("标题") or row.get("选题名称") or content_id).strip(),
        "content_id": content_id,
        "media_form": media_form or str(row.get("媒介形式", "")).strip(),
        "content_task": content_task or state_task,
    }


def _current_positioning_settings(root: Path, state: Mapping[str, object]) -> tuple[str, str]:
    machine_data = state.get("机器数据")
    content_task = str(machine_data.get("内容任务", "")).strip() if isinstance(machine_data, Mapping) else ""
    if content_task not in CONTENT_TASKS:
        raise ContentStageCoordinatorError("当前内容定位缺少有效内容目标")
    stage = state.get("阶段")
    positioning = stage.get("内容定位") if isinstance(stage, Mapping) else None
    version = int(positioning.get("版本", 0)) if isinstance(positioning, Mapping) else 0
    record_path = _workspace_file(root, str(state.get("内容生产记录路径", "")), "内容生产记录")
    state_executor = _load_sibling(
        "内容生产状态执行器.py",
        "content_state_positioning_settings_for_coordinator",
    )
    body = state_executor._extract_record_version(
        record_path.read_text(encoding="utf-8"),
        "内容定位",
        version,
    ) or ""
    media_form = _positioning_media_form(body) if body else ""
    if not media_form:
        media_form = _display_identity(root, state)["media_form"]
    if media_form not in CANONICAL_MEDIA_FORMS:
        raise ContentStageCoordinatorError("当前内容定位缺少有效媒介形式")
    return media_form, content_task


def _sync_confirmed_positioning_media(
    root: Path,
    state: Mapping[str, object],
) -> str:
    record_path = _workspace_file(
        root,
        str(state.get("内容生产记录路径", "")),
        "内容生产记录",
    )
    version = int(state.get("阶段", {}).get("内容定位", {}).get("版本", 0))
    state_executor = _load_sibling(
        "内容生产状态执行器.py",
        "content_state_positioning_media_sync_for_coordinator",
    )
    body = state_executor._extract_record_version(
        record_path.read_text(encoding="utf-8"),
        "内容定位",
        version,
    )
    if not body:
        raise ContentStageCoordinatorError("已确认内容定位版本不存在，无法同步媒介形式")
    media_form = _positioning_media_form(body)
    if not media_form:
        return ""

    csv_path = root / "我方资料" / "我方内容库.csv"
    try:
        with csv_path.open("r", encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle)
            fields = list(reader.fieldnames or [])
            rows = [dict(row) for row in reader]
    except (OSError, UnicodeError, csv.Error) as exc:
        raise ContentStageCoordinatorError(f"无法读取我方内容库以同步媒介形式: {exc}") from exc
    required = {"内容ID", "选题ID", "媒介形式"}
    if not required.issubset(fields):
        raise ContentStageCoordinatorError("我方内容库缺少内容ID、选题ID或媒介形式字段")
    matches = [
        row for row in rows
        if row.get("内容ID") == str(state.get("内容ID", ""))
        and row.get("选题ID") == str(state.get("选题ID", ""))
    ]
    if len(matches) != 1:
        raise ContentStageCoordinatorError("我方内容库无法唯一定位当前内容以同步媒介形式")
    if matches[0].get("媒介形式", "").strip() == media_form:
        return media_form
    matches[0]["媒介形式"] = media_form
    buffer = io.StringIO(newline="")
    writer = csv.DictWriter(buffer, fieldnames=fields, lineterminator="\n")
    writer.writeheader()
    writer.writerows(rows)
    state_executor._replace_bytes_atomically(csv_path, buffer.getvalue().encode("utf-8-sig"))
    return media_form


def _decision_request_schema(
    state_file: str,
    decisions: list[str],
    *,
    allowed_targets: list[str] | None = None,
) -> dict[str, object]:
    def variant(
        decision_schema: Mapping[str, object],
        *,
        target_required: bool = False,
    ) -> dict[str, object]:
        properties: dict[str, object] = {
            "action": {"const": "decide"},
            "state_file": {"const": state_file},
            "decision": dict(decision_schema),
            "reason": {"type": "string"},
        }
        required = ["action", "state_file", "decision"]
        if target_required:
            properties["target_stage"] = {"enum": list(allowed_targets or [])}
            required.append("target_stage")
        return {
            "type": "object",
            "required": required,
            "additionalProperties": False,
            "properties": properties,
        }

    if allowed_targets and "rework_upstream" in decisions:
        regular_decisions = [item for item in decisions if item != "rework_upstream"]
        variants = []
        if regular_decisions:
            variants.append(variant({"enum": regular_decisions}))
        variants.append(variant({"const": "rework_upstream"}, target_required=True))
        return {"oneOf": variants}
    return variant({"enum": decisions})


def _recovery_action_contract(
    state_file: str,
) -> dict[str, object]:
    required = ["action", "state_file", "transition_action"]
    properties: dict[str, object] = {
        "action": {"const": "transition"},
        "state_file": {"const": state_file},
        "transition_action": {"const": "unblock"},
    }
    return {
        "current_action": "unblock_then_prepare",
        "submit_request_schema": {
            "type": "object",
            "required": required,
            "additionalProperties": False,
            "properties": properties,
        },
    }


def _decision_contract(
    *,
    state_file: str,
    stage_name: str,
    decisions: list[str],
    allowed_targets: list[str] | None = None,
) -> dict[str, object]:
    return {
        "stage": stage_name,
        "submit_request_schema": _decision_request_schema(
            state_file,
            decisions,
            allowed_targets=allowed_targets,
        ),
    }


def _final_adopted_features_schema() -> dict[str, object]:
    feature_properties = {
        "目标ID": {"type": "string", "minLength": 1},
        "对象类型": {"const": "特征"},
        "作用环节": {"enum": ["吸引", "持续", "信任", "获得", "沉淀"]},
        "采用目的": {"type": "string", "minLength": 1},
        "实际采用状态": {"const": "已落实"},
        "正文证据": {"type": "string", "minLength": 1},
    }
    return {
        "type": "object",
        "required": ["采用特征"],
        "additionalProperties": False,
        "properties": {
            "采用特征": {
                "type": "array",
                "items": {
                    "type": "object",
                    "required": list(feature_properties),
                    "additionalProperties": False,
                    "properties": feature_properties,
                },
            },
        },
    }


def _positioning_prepare_input_contract(state_file: str) -> dict[str, object]:
    """Declare the only machine field names allowed when resuming positioning."""
    return {
        "current_action": "prepare",
        "submit_request_schema": {
            "type": "object",
            "required": ["action", "state_file", "media_form", "content_task"],
            "additionalProperties": False,
            "properties": {
                "action": {"const": "prepare"},
                "state_file": {"const": state_file},
                "media_form": {"enum": list(CANONICAL_MEDIA_FORMS)},
                "content_task": {"enum": ["流量", "涨粉", "交易"]},
            },
        },
        "cli_argument_map": {
            "state_file": "--state-file",
            "media_form": "--media-form",
            "content_task": "--content-task",
        },
    }


def _new_stage_run(
    root: Path,
    stage: Mapping[str, object],
    content_identity: object,
) -> Path:
    identity = content_identity if isinstance(content_identity, Mapping) else {}
    content_id = str(identity.get("内容ID", "unknown")).strip() or "unknown"
    stage_id = str(stage.get("id", "stage")).strip() or "stage"
    mode = str(stage.get("mode", "")).strip()
    run_id = f"{content_id}-{stage_id}{('-' + mode) if mode else ''}-{time.strftime('%Y%m%d-%H%M%S')}-{secrets.token_hex(4)}"
    run_dir = root / ".meiling" / "staging" / "create-content" / run_id
    run_dir.mkdir(parents=True, exist_ok=False)
    return run_dir


def _stage_action_contract(
    root: Path,
    stage: Mapping[str, object],
    inputs: object,
    state_file: str,
    content_identity: object,
) -> dict[str, object]:
    stage_name = str(stage.get("name", "")).strip()
    run_dir = _new_stage_run(root, stage, content_identity)
    input_items = inputs if isinstance(inputs, list) else []
    input_names = [
        str(item.get("name", "")).strip()
        for item in input_items
        if isinstance(item, Mapping) and str(item.get("name", "")).strip()
    ]

    record_sections = list(stage.get("record_sections", []))
    stage_candidate_path = _logical_path(root, run_dir / "阶段记录候选.md")
    if stage_name == "内容定位":
        return {
            "current_action": "generate_stage_candidate",
            "required_inputs": input_names,
            "required_outputs": [
                {
                    "name": "stage_candidate_file",
                    "type": "markdown_file",
                    "exact_path": stage_candidate_path,
                    "required_level_two_sections": record_sections,
                    "section_order": record_sections,
                    "forbidden": ["其他二级区块", "版本标题（由阶段协调器生成）"],
                    "minimal_example": "## 内容定位\n\n<当前阶段内容>",
                    "structure_contract": STRUCTURE_CONTRACT_NOTE,
                },
            ],
            "forbidden_extra_outputs": True,
            "submit_request_schema": {
                "type": "object",
                "required": ["action", "state_file", "stage_candidate_file"],
                "additionalProperties": False,
                "properties": {
                    "action": {"const": "save"},
                    "state_file": {"const": state_file},
                    "stage_candidate_file": {"const": stage_candidate_path},
                },
            },
        }
    contract: dict[str, object] = {
        "current_action": "generate_stage_candidate",
        "required_inputs": input_names,
        "required_outputs": [
            {
                "name": "stage_candidate_file",
                "type": "markdown_file",
                "exact_path": stage_candidate_path,
                "required_level_two_sections": record_sections,
                "section_order": record_sections,
                "forbidden": ["其他二级区块", "版本标题（由阶段协调器生成）"],
                "minimal_example": "\n\n".join(f"## {section}\n\n<当前阶段内容>" for section in record_sections),
                "structure_contract": STRUCTURE_CONTRACT_NOTE,
            },
        ],
        "forbidden_extra_outputs": True,
        "submit_request_schema": {
            "type": "object",
            "required": ["action", "state_file", "stage_candidate_file"],
            "additionalProperties": False,
            "properties": {
                "action": {"const": "save"},
                "state_file": {"const": state_file},
                "stage_candidate_file": {"const": stage_candidate_path},
            },
        },
    }
    if stage_name == "内容终稿":
        stage_output = contract["required_outputs"][0]
        stage_output["required_final_fields"] = ["最终内容标题"]
        stage_output["minimal_example"] = (
            "## 内容终稿\n\n"
            "最终内容标题：<标题>\n\n<完整终稿>"
        )
    return contract


def _stable_json_bytes(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _snapshot_digest(snapshot: Mapping[str, object]) -> str:
    unsigned = dict(snapshot)
    unsigned.pop("snapshot_sha256", None)
    return hashlib.sha256(_stable_json_bytes(unsigned)).hexdigest()


def _stage_version_from_result(root: Path, result: Mapping[str, object]) -> int:
    state_file = _workspace_file(root, str(result.get("state_file", "")), "当前阶段状态")
    try:
        state = json.loads(state_file.read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ContentStageCoordinatorError(f"无法读取当前阶段状态以生成输入快照: {exc}") from exc
    stage_name = str(result.get("current_stage", {}).get("name", "")) if isinstance(result.get("current_stage"), Mapping) else ""
    stages = state.get("阶段", {})
    stage = stages.get(stage_name, {}) if isinstance(stages, Mapping) else {}
    try:
        return int(stage.get("版本", 0))
    except (TypeError, ValueError) as exc:
        raise ContentStageCoordinatorError("当前阶段版本不是有效整数，无法生成输入快照") from exc


def _run_dir_from_contract(root: Path, action_contract: Mapping[str, object]) -> Path:
    outputs = action_contract.get("required_outputs", [])
    if not isinstance(outputs, list) or not outputs:
        raise ContentStageCoordinatorError("阶段动作合同缺少输出路径，无法生成输入快照")
    first = outputs[0]
    if not isinstance(first, Mapping) or not first.get("exact_path"):
        raise ContentStageCoordinatorError("阶段动作合同缺少有效输出路径，无法生成输入快照")
    raw_output = Path(str(first["exact_path"])).expanduser()
    output = (raw_output if raw_output.is_absolute() else root / raw_output).resolve()
    try:
        output.relative_to(root)
    except ValueError as exc:
        raise ContentStageCoordinatorError("阶段候选输出路径必须位于当前工作区内") from exc
    run_dir = output.parent
    staging_root = (root / ".meiling" / "staging" / "create-content").resolve()
    try:
        run_dir.relative_to(staging_root)
    except ValueError as exc:
        raise ContentStageCoordinatorError("输入快照目录必须位于create-content暂存区") from exc
    return run_dir


def _new_snapshot_run_dir(root: Path, result: Mapping[str, object]) -> Path:
    identity = result.get("content_identity")
    content_id = str(identity.get("内容ID", "content")) if isinstance(identity, Mapping) else "content"
    stage_name = str(result.get("current_stage", {}).get("name", "stage")) if isinstance(result.get("current_stage"), Mapping) else "stage"
    safe_content_id = re.sub(r"[^0-9A-Za-z一-龥_-]+", "_", content_id) or "content"
    safe_stage = re.sub(r"[^0-9A-Za-z一-龥_-]+", "_", stage_name) or "stage"
    run_dir = root / ".meiling" / "staging" / "create-content" / (
        f"{safe_content_id}-{safe_stage}-snapshot-{time.strftime('%Y%m%d-%H%M%S')}-{secrets.token_hex(4)}"
    )
    run_dir.mkdir(parents=True, exist_ok=False)
    staging_root = (root / ".meiling" / "staging" / "create-content").resolve()
    try:
        run_dir.resolve().relative_to(staging_root)
    except ValueError as exc:
        raise ContentStageCoordinatorError("输入快照目录必须位于create-content暂存区") from exc
    return run_dir


def _write_input_snapshot(
    root: Path,
    *,
    result: Mapping[str, object],
    action_contract: Mapping[str, object] | None,
    inputs: list[object],
) -> dict[str, object]:
    run_dir = (
        _run_dir_from_contract(root, action_contract)
        if action_contract is not None
        else _new_snapshot_run_dir(root, result)
    )
    stage_name = str(result.get("current_stage", {}).get("name", "")) if isinstance(result.get("current_stage"), Mapping) else ""
    snapshot: dict[str, object] = {
        "schema_version": INPUT_SNAPSHOT_SCHEMA_VERSION,
        "contract_version": CONTRACT_VERSION,
        "content_identity": result.get("content_identity", {}),
        "state_file": str(result.get("state_file", "")),
        "stage_name": stage_name,
        "stage_version": _stage_version_from_result(root, result),
        "preparation_sha256": str(result.get("preparation_sha256", "")),
        "inputs": inputs,
    }
    snapshot["snapshot_sha256"] = _snapshot_digest(snapshot)
    target = run_dir / "输入快照.json"
    temporary = run_dir / ".输入快照.json.tmp"
    try:
        temporary.write_bytes(_stable_json_bytes(snapshot) + b"\n")
        temporary.replace(target)
    except OSError as exc:
        raise ContentStageCoordinatorError(f"无法写入输入快照: {exc}") from exc
    return {
        "path": _logical_path(root, target),
        "sha256": str(snapshot["snapshot_sha256"]),
        "schema_version": INPUT_SNAPSHOT_SCHEMA_VERSION,
        "state_file": str(result.get("state_file", "")),
        "stage_name": stage_name,
        "stage_version": snapshot["stage_version"],
        "input_count": len(inputs),
    }


def _load_input_snapshot(
    root: Path,
    *,
    path: str | Path,
    expected_sha256: str = "",
    state_file: str = "",
    content_id: str = "",
    stage_name: str = "",
    stage_version: int | None = None,
) -> dict[str, object]:
    target = _workspace_file(root, path, "输入快照")
    try:
        snapshot = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ContentStageCoordinatorError(f"无法读取输入快照: {exc}") from exc
    if not isinstance(snapshot, dict):
        raise ContentStageCoordinatorError("输入快照必须是对象")
    if snapshot.get("schema_version") != INPUT_SNAPSHOT_SCHEMA_VERSION:
        raise ContentStageCoordinatorError("输入快照版本不受支持")
    actual_sha256 = _snapshot_digest(snapshot)
    if actual_sha256 != str(snapshot.get("snapshot_sha256", "")):
        raise ContentStageCoordinatorError("输入快照校验失败，文件可能已被篡改")
    if expected_sha256 and actual_sha256 != expected_sha256:
        raise ContentStageCoordinatorError("输入快照与回执声明的校验值不一致")
    if state_file and str(snapshot.get("state_file", "")) != state_file:
        raise ContentStageCoordinatorError("输入快照与当前状态文件不一致")
    if content_id:
        identity = snapshot.get("content_identity")
        snapshot_content_id = identity.get("内容ID", "") if isinstance(identity, Mapping) else ""
        if str(snapshot_content_id) != content_id:
            raise ContentStageCoordinatorError("输入快照与当前内容ID不一致")
    if stage_name and str(snapshot.get("stage_name", "")) != stage_name:
        raise ContentStageCoordinatorError("输入快照与当前阶段不一致")
    if stage_version is not None and int(snapshot.get("stage_version", -1)) != int(stage_version):
        raise ContentStageCoordinatorError("输入快照对应的阶段版本已过期")
    inputs = snapshot.get("inputs")
    if not isinstance(inputs, list):
        raise ContentStageCoordinatorError("输入快照缺少完整inputs")
    for item in inputs:
        if not isinstance(item, Mapping):
            raise ContentStageCoordinatorError("输入快照包含无效输入项")
        if "content" in item:
            expected_content_sha256 = str(item.get("content_sha256", ""))
            actual_content_sha256 = hashlib.sha256(
                str(item.get("content", "")).encode("utf-8")
            ).hexdigest()
            if expected_content_sha256 and actual_content_sha256 != expected_content_sha256:
                raise ContentStageCoordinatorError(
                    f"输入快照中的{item.get('name', '输入')}正文校验失败"
                )
    return snapshot


_STAGE_INLINE_INPUTS = {
    "内容定位": None,
    "内容初稿": {"state_route", "state_machine_data", "content_row", "topic_row", "confirmed_positioning", "current_draft"},
    "内容终稿": {"state_route", "state_machine_data", "content_row", "topic_row", "confirmed_positioning", "confirmed_draft", "current_final"},
}


def _slim_inputs(inputs: list[object], stage_name: str, snapshot: Mapping[str, object]) -> list[dict[str, object]]:
    allowed = _STAGE_INLINE_INPUTS.get(stage_name)
    result: list[dict[str, object]] = []
    snapshot_ref = {
        "path": str(snapshot.get("path", "")),
        "sha256": str(snapshot.get("sha256", "")),
        "schema_version": INPUT_SNAPSHOT_SCHEMA_VERSION,
        "state_file": str(snapshot.get("state_file", "")),
        "stage_name": str(snapshot.get("stage_name", stage_name)),
        "stage_version": snapshot.get("stage_version", 0),
    }
    for raw in inputs:
        if not isinstance(raw, Mapping):
            continue
        item = dict(raw)
        name = str(item.get("name", ""))
        content = item.pop("content", None)
        keep_content = allowed is None or name in allowed
        if keep_content and content is not None:
            item["content"] = content
            item["content_available"] = True
        else:
            item["content_available"] = False
        item["snapshot"] = dict(snapshot_ref)
        result.append(item)
    return result


def _write_run_manifest(
    root: Path,
    *,
    result: Mapping[str, object],
    action_contract: Mapping[str, object],
) -> None:
    outputs = action_contract.get("required_outputs", [])
    paths: list[Path] = []
    if isinstance(outputs, list):
        for item in outputs:
            if isinstance(item, Mapping) and item.get("exact_path"):
                raw = Path(str(item["exact_path"])).expanduser()
                candidate = (raw if raw.is_absolute() else root / raw).resolve()
                try:
                    candidate.relative_to(root)
                except ValueError as exc:
                    raise ContentStageCoordinatorError("候选输出路径必须位于当前工作区内") from exc
                paths.append(candidate)
    if not paths:
        return
    run_dir = paths[0].parent
    state_file = _workspace_file(root, str(result.get("state_file", "")), "当前阶段状态")
    state = json.loads(state_file.read_text(encoding="utf-8-sig"))
    stage_name = str(result.get("current_stage", {}).get("name", "")) if isinstance(result.get("current_stage"), Mapping) else ""
    stages = state.get("阶段", {})
    stage = stages.get(stage_name, {}) if isinstance(stages, Mapping) else {}
    inputs = result.get("inputs", [])
    positioning_input_names = (
        ("current_positioning", "confirmed_positioning")
        if stage_name == "内容定位"
        else ("confirmed_positioning",)
    )
    positioning_core = ""
    if isinstance(inputs, list):
        for input_name in positioning_input_names:
            positioning_core = next(
                (
                    str(item.get("content", ""))
                    for item in inputs
                    if isinstance(item, Mapping) and item.get("name") == input_name
                ),
                "",
            )
            if positioning_core:
                break
    if not positioning_core:
        snapshot_ref = result.get("input_snapshot")
        if isinstance(snapshot_ref, Mapping) and snapshot_ref.get("path"):
            snapshot = _load_input_snapshot(
                root,
                path=str(snapshot_ref["path"]),
                expected_sha256=str(snapshot_ref.get("sha256", "")),
            )
            snapshot_inputs = snapshot.get("inputs", [])
            if isinstance(snapshot_inputs, list):
                for input_name in positioning_input_names:
                    positioning_core = next(
                        (
                            str(item.get("content", ""))
                            for item in snapshot_inputs
                            if isinstance(item, Mapping) and item.get("name") == input_name
                        ),
                        "",
                    )
                    if positioning_core:
                        break
    manifest = {
        "schema_version": 1,
        "state_file": str(result.get("state_file", "")),
        "stage": stage_name,
        "stage_version": int(stage.get("版本", 0)) if isinstance(stage, Mapping) else 0,
        "content_id": str(state.get("内容ID", "")),
        "positioning_core_sha256": _positioning_core_sha256(positioning_core),
        "positioning_content_task": str(result.get("positioning_content_task", "")),
        "positioning_media_form": str(result.get("positioning_media_form", "")),
    }
    snapshot_ref = result.get("input_snapshot")
    if isinstance(snapshot_ref, Mapping):
        manifest["input_snapshot"] = {
            "path": str(snapshot_ref.get("path", "")),
            "sha256": str(snapshot_ref.get("sha256", "")),
            "schema_version": snapshot_ref.get("schema_version", INPUT_SNAPSHOT_SCHEMA_VERSION),
        }
    target = run_dir / "运行清单.json"
    target.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _validate_prepared_run(
    root: Path,
    *,
    state_file: str | Path,
    candidate_paths: list[Path],
) -> dict[str, object]:
    if not candidate_paths:
        raise ContentStageCoordinatorError("提交候选不能为空")
    run_dir = candidate_paths[0].parent
    staging_root = (root / ".meiling" / "staging" / "create-content").resolve()
    try:
        run_dir.relative_to(staging_root)
    except ValueError as exc:
        raise ContentStageCoordinatorError("候选必须来自create-content本次prepare生成的运行目录") from exc
    if any(path.parent != run_dir for path in candidate_paths):
        raise ContentStageCoordinatorError("同一次提交的候选必须位于同一个运行目录")
    manifest_path = run_dir / "运行清单.json"
    if not manifest_path.is_file():
        raise ContentStageCoordinatorError("候选缺少本次prepare生成的运行清单，拒绝提交")
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        state_path = _workspace_file(root, state_file, "当前阶段状态")
        state = json.loads(state_path.read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ContentStageCoordinatorError(f"无法读取运行清单或当前状态: {exc}") from exc
    if manifest.get("state_file") != _logical_path(root, state_path):
        raise ContentStageCoordinatorError("运行清单与当前状态文件不一致")
    stage_name = str(state.get("当前阶段", ""))
    stage = state.get("阶段", {}).get(stage_name, {}) if isinstance(state.get("阶段"), Mapping) else {}
    if manifest.get("stage") != stage_name or int(manifest.get("stage_version", -1)) != int(stage.get("版本", -2)):
        raise ContentStageCoordinatorError("运行清单对应的阶段版本已过期，拒绝提交")
    if manifest.get("content_id") != str(state.get("内容ID", "")):
        raise ContentStageCoordinatorError("运行清单与当前内容ID不一致")
    snapshot_ref = manifest.get("input_snapshot")
    if isinstance(snapshot_ref, Mapping) and snapshot_ref.get("path"):
        _load_input_snapshot(
            root,
            path=str(snapshot_ref["path"]),
            expected_sha256=str(snapshot_ref.get("sha256", "")),
            state_file=str(manifest.get("state_file", "")),
            content_id=str(manifest.get("content_id", "")),
            stage_name=stage_name,
            stage_version=int(stage.get("版本", 0)),
        )
    if str(manifest.get("stage", "")) == "内容定位":
        expected_task = str(manifest.get("positioning_content_task", "")).strip()
        if expected_task not in CONTENT_TASKS:
            raise ContentStageCoordinatorError(
                "还没有确认这条内容的主目标侧重（流量 / 涨粉 / 交易），请先在内容定位阶段确认"
            )
        expected_media_form = str(manifest.get("positioning_media_form", "")).strip()
        if expected_media_form:
            candidate_media_form = _positioning_media_form(
                candidate_paths[0].read_text(encoding="utf-8")
            )
            if not candidate_media_form:
                raise ContentStageCoordinatorError("定位候选缺少「媒介形式」行")
            if candidate_media_form != expected_media_form:
                raise ContentStageCoordinatorError(
                    f"定位候选的媒介形式（{candidate_media_form}）与本次确认的（{expected_media_form}）不一致"
                )
        if _positioning_content_task(candidate_paths[0].read_text(encoding="utf-8")) != expected_task:
            raise ContentStageCoordinatorError("定位候选的主目标侧重与本次prepare不一致")
    current_core = ""
    record_path = _workspace_file(
        root,
        str(state.get("内容生产记录路径", "")),
        "内容生产记录",
    )
    current_positioning = state.get("阶段", {}).get("内容定位", {})
    current_positioning_version = (
        int(current_positioning.get("版本", 0))
        if isinstance(current_positioning, Mapping)
        else 0
    )
    if current_positioning_version > 0:
        state_executor = _load_sibling(
            "内容生产状态执行器.py",
            "content_state_core_validation_for_coordinator",
        )
        current_core = state_executor._extract_record_version(
            record_path.read_text(encoding="utf-8"),
            "内容定位",
            current_positioning_version,
        ) or ""
    if manifest.get("positioning_core_sha256") != _positioning_core_sha256(current_core):
        raise ContentStageCoordinatorError("内容核心已变化，候选已过期")
    return dict(manifest)


def _prepared_result(
    root: Path,
    payload: Mapping[str, object],
    *,
    prepare_positioning_settings: bool = False,
) -> dict[str, object]:
    preparation_mode = str(payload.get("preparation_mode", "")).strip()
    if preparation_mode == "topic_evaluation_required":
        route = payload.get("route", {})
        topic_id = str(route.get("选题ID", "")).strip() if isinstance(route, Mapping) else ""
        return {
            "ok": True,
            "contract_version": CONTRACT_VERSION,
            "status": "needs_topic_evaluation",
            "message": TOPIC_EVALUATION_REQUIRED_MESSAGE,
            **({"topic_id": topic_id} if topic_id else {}),
        }
    if preparation_mode == "selection":
        content_candidates = payload.get("content_candidates", [])
        topic_candidates = payload.get("topic_candidates", [])
        if not content_candidates and not topic_candidates:
            return {
                "ok": True,
                "contract_version": CONTRACT_VERSION,
                "status": "needs_production_input",
                "message": NO_FORMAL_TOPIC_MESSAGE,
            }
        return {
            "ok": True,
            "contract_version": CONTRACT_VERSION,
            "status": "needs_selection",
            "route": payload.get("route", {}),
            "content_candidates": content_candidates,
            "topic_candidates": topic_candidates,
        }
    if preparation_mode == "intake":
        return {
            "ok": True,
            "contract_version": CONTRACT_VERSION,
            "status": "needs_intake",
            "route": payload.get("route", {}),
            "content_identity": payload.get("content_identity", {}),
            "inputs": payload.get("inputs", []),
            "resume_candidates": payload.get("resume_candidates", []),
            "required_fields": [],
        }
    route = payload.get("route")
    if not isinstance(route, Mapping):
        raise ContentStageCoordinatorError("阶段准备结果缺少正式路由")
    if preparation_mode != "stage":
        raise ContentStageCoordinatorError("阶段准备结果缺少有效模式")
    state_file = _state_file_from_route(root, route)
    stage_name = str(payload.get("stage_name", "")).strip()
    flow_status = str(route.get("流程状态", "")).strip()
    if flow_status not in STATUS_SEMANTICS:
        raise ContentStageCoordinatorError(f"阶段准备结果包含未知流程状态: {flow_status or '空'}")
    stage_contract = _stage_contract(stage_name)
    _workspace_file(root, str(stage_contract["flow_file"]), "当前阶段本地流程")
    if flow_status == "等待用户确认" and not prepare_positioning_settings:
        context = _read_state_context(root, state_file)
        decisions = _accepted_decisions(stage_name)
        allowed_targets = [
            str(item)
            for item in context["confirmed_stages"]
            if str(item) != stage_name
        ]
        result = {
            "ok": True,
            "contract_version": CONTRACT_VERSION,
            "status": "waiting_for_user",
            "status_semantic": STATUS_SEMANTICS[flow_status],
            "current_stage": stage_contract,
            "route": dict(route),
            "state_file": state_file,
            "inputs": payload.get("inputs", []),
            "decision_contract": _decision_contract(
                state_file=state_file,
                stage_name=stage_name,
                decisions=decisions,
                allowed_targets=(allowed_targets if "rework_upstream" in decisions else None),
            ),
            "display_identity": _display_identity(root, context["state"]),
        }
        full_inputs = list(result["inputs"]) if isinstance(result["inputs"], list) else []
        snapshot = _write_input_snapshot(
            root,
            result=result,
            action_contract=None,
            inputs=full_inputs,
        )
        result["input_snapshot"] = snapshot
        result["inputs"] = _slim_inputs(full_inputs, stage_name, snapshot)
        return result
    if prepare_positioning_settings:
        if stage_name != "内容定位" or flow_status != "等待用户确认":
            raise ContentStageCoordinatorError("只有等待用户确认的内容定位可以更新设置")
    if flow_status == "被阻断":
        return {
            "ok": True,
            "contract_version": CONTRACT_VERSION,
            "status": "blocked",
            "status_semantic": STATUS_SEMANTICS[flow_status],
            "route": dict(route),
            "state_file": state_file,
            "error": str(route.get("阻断原因", "")).strip(),
            "retry_action": "unblock_then_prepare",
            "recovery_action_contract": _recovery_action_contract(state_file),
        }
    if flow_status == "已完成":
        return {
            "ok": True,
            "contract_version": CONTRACT_VERSION,
            "status": "completed",
            "status_semantic": STATUS_SEMANTICS[flow_status],
            "route": dict(route),
            "state_file": state_file,
        }
    result = {
        "ok": True,
        "contract_version": CONTRACT_VERSION,
        "status": "ready",
        "status_semantic": STATUS_SEMANTICS["进行中"] if prepare_positioning_settings else STATUS_SEMANTICS[flow_status],
        "current_stage": stage_contract,
        "route": dict(route),
        "content_identity": payload.get("content_identity", {}),
        "state_file": state_file,
        "inputs": payload.get("inputs", []),
        "preparation_sha256": payload.get("preparation_sha256", ""),
        "task": {
            "content_id": str(payload.get("content_identity", {}).get("内容ID", ""))
            if isinstance(payload.get("content_identity"), Mapping)
            else "",
            "topic_id": str(payload.get("content_identity", {}).get("选题ID", ""))
            if isinstance(payload.get("content_identity"), Mapping)
            else "",
            "current_stage": stage_name,
            "state_file": state_file,
            "status": str(route.get("流程状态", "")),
            "needs_user_confirmation": bool(route.get("是否需要用户确认", False)),
        },
    }
    if stage_name == "内容定位":
        overview_context = payload.get("overview_context")
        if not isinstance(overview_context, Mapping):
            raise ContentStageCoordinatorError("内容定位准备结果缺少概要上下文")
        result["overview_context"] = dict(overview_context)
        result["prepare_input_contract"] = _positioning_prepare_input_contract(state_file)
    result["action_contract"] = _stage_action_contract(
        root,
        stage_contract,
        result["inputs"],
        state_file,
        result["content_identity"],
    )
    snapshot = _write_input_snapshot(
        root,
        result=result,
        action_contract=result["action_contract"],
        inputs=list(result["inputs"]) if isinstance(result["inputs"], list) else [],
    )
    result["input_snapshot"] = snapshot
    result["inputs"] = _slim_inputs(
        list(result["inputs"]) if isinstance(result["inputs"], list) else [],
        stage_name,
        snapshot,
    )
    _write_run_manifest(root, result=result, action_contract=result["action_contract"])
    return result


def _apply_positioning_identity(
    root: Path,
    result: dict[str, object],
    content_task: str,
    media_form: str = "",
) -> None:
    if result.get("status") != "ready":
        if content_task:
            raise ContentStageCoordinatorError("--content-task只能用于内容定位的新阶段准备")
        return
    current_stage = result.get("current_stage")
    if not isinstance(current_stage, Mapping):
        if content_task:
            raise ContentStageCoordinatorError("--content-task只能用于内容定位阶段")
        return
    stage_name = str(current_stage.get("name", "")).strip()
    if stage_name != "内容定位":
        if content_task:
            raise ContentStageCoordinatorError("--content-task只能用于内容定位阶段")
        return
    identity = result.get("content_identity")
    if not isinstance(identity, Mapping):
        raise ContentStageCoordinatorError(f"{stage_name}准备结果缺少内容身份")
    current_task = str(identity.get("content_task", "")).strip()
    effective_task = content_task or current_task
    if not effective_task:
        return
    effective_media_form = media_form or str(identity.get("媒介形式", "")).strip()
    identity["媒介形式"] = effective_media_form
    # 身份里的 content_task 必须与 positioning_content_task 同步写回：否则同一份回执里会
    # 出现「content_identity.content_task 为空、positioning_content_task 有值」的自相矛盾，
    # 让执行者无法判断本轮内容任务到底定了没有（2026-09-14 实测）。
    identity["content_task"] = effective_task
    result["positioning_content_task"] = effective_task
    result["positioning_media_form"] = effective_media_form
    contract = result.get("action_contract")
    if isinstance(contract, Mapping):
        _write_run_manifest(root, result=result, action_contract=contract)


def _create_record_candidate(root: Path) -> Path:
    run_id = f"prepare-{time.strftime('%Y%m%d-%H%M%S')}-{secrets.token_hex(4)}"
    run_dir = root / ".meiling" / "staging" / "内容生产协调" / run_id
    run_dir.mkdir(parents=True, exist_ok=False)
    candidate = run_dir / "内容生产记录候选.md"
    candidate.write_text(EMPTY_CONTENT_RECORD, encoding="utf-8")
    return candidate


def _prepare(
    root: Path,
    *,
    state_file: str | Path | None,
    content_id: str,
    topic_id: str,
    topic_name: str,
    media_form: str,
    nonrecommended_decision: str,
    content_task: str,
    reference_content_id: str,
    reference_analysis_path: str,
) -> dict[str, object]:
    preparer = _load_sibling("内容生产阶段准备执行器.py", "content_stage_preparer_for_coordinator")
    try:
        canonical_media_form = _normalize_media_form(media_form)
        media_form_note = ""
        if media_form.strip() and canonical_media_form != media_form.strip():
            # 归一本身是设计（文档要求必须归一为 视频/图文/文章/其他），
            # 但静默改写会让用户以为系统记住的是他说的那个词，所以在回执里回显一次。
            media_form_note = (
                f"媒介形式「{media_form.strip()}」已按规范归一为「{canonical_media_form}」，"
                "后续显示与记录都使用归一后的值。"
            )
        reference = _validate_benchmark_reference(
            root,
            reference_content_id=reference_content_id,
            reference_analysis_path=reference_analysis_path,
        )
        if reference and (state_file is not None or content_id.strip() or topic_id.strip()):
            raise ContentStageCoordinatorError(
                "对标内容直达生产只能用于新建独立内容，不能与内容ID、状态文件或选题ID同时使用"
            )
        if state_file is not None:
            if topic_name.strip():
                raise ContentStageCoordinatorError(
                    "继续已有独立内容时不得同时提供topic_name；请沿用state_file或content_id"
                )
            prepare_positioning_settings = False
            if canonical_media_form or content_task:
                context = _read_state_context(root, state_file)
                if (
                    str(context["current_stage"]) == "内容定位"
                    and str(context["state"].get("流程状态", "")) == "等待用户确认"
                ):
                    if not canonical_media_form or content_task not in CONTENT_TASKS:
                        raise ContentStageCoordinatorError(
                            "修改媒介形式或内容目标时必须同时提供有效的媒介形式和内容目标"
                        )
                    prepare_positioning_settings = True
            payload = preparer.prepare_content_stage(
                root,
                state_file=state_file,
            )
            result = _prepared_result(
                root,
                payload,
                prepare_positioning_settings=prepare_positioning_settings,
            )
            _apply_positioning_identity(root, result, content_task, canonical_media_form)
            return result
        if content_id.strip():
            if topic_name.strip():
                raise ContentStageCoordinatorError(
                    "继续已有独立内容时不得同时提供topic_name；请沿用state_file或content_id"
                )
            payload = preparer.prepare_content_stage(
                root,
                content_id=content_id.strip(),
            )
            result = _prepared_result(root, payload)
            _apply_positioning_identity(root, result, content_task, canonical_media_form)
            return result
        if not topic_id.strip() and (topic_name.strip() or content_task or canonical_media_form or reference):
            if topic_name.strip() and (canonical_media_form or content_task):
                content_path = root / "我方资料" / "我方内容库.csv"
                try:
                    with content_path.open("r", encoding="utf-8-sig", newline="") as handle:
                        rows = list(csv.DictReader(handle))
                except (OSError, UnicodeError, csv.Error) as exc:
                    raise ContentStageCoordinatorError(
                        f"无法检查独立内容是否已建档: {exc}"
                    ) from exc
                duplicate = next(
                    (
                        row
                        for row in rows
                        if not str(row.get("选题ID") or "").strip()
                        and str(row.get("标题") or "").strip() == topic_name.strip()
                        and str(row.get("当前状态") or "").strip() == "生产中"
                    ),
                    None,
                )
                if duplicate is not None:
                    duplicate_id = str(duplicate.get("内容ID") or "").strip() or "未知"
                    raise ContentStageCoordinatorError(
                        f"独立内容{duplicate_id}已存在生产中记录；补充媒介形式或内容目标时必须提供原state_file或content_id，拒绝重复建档"
                    )
            intake = _load_sibling("内容生产建档执行器.py", "content_intake_for_independent_coordinator")
            effective_topic_name = topic_name.strip() or str(reference["title"] if reference else "")
            record_candidate = _create_record_candidate(root)
            intake_result = intake.finalize_content_intake(
                root,
                action_data={
                    "选题ID": "",
                    "选题名称": effective_topic_name,
                    "媒介形式": canonical_media_form,
                    "参考内容分析路径": str(reference["analysis_path"] if reference else ""),
                },
                record_candidate_file=record_candidate,
            )
            # 建档完成后这个空白记录模板就没有读者了；不清理会让每次建档都留下一个暂存目录。
            _cleanup_success_files([record_candidate])
            payload = preparer.prepare_content_stage(root, state_file=intake_result["state_file"])
            result = _prepared_result(root, payload)
            _apply_positioning_identity(root, result, content_task, canonical_media_form)
            result["content_mode"] = "independent"
            if media_form_note:
                result["media_form_note"] = media_form_note
            result["intake"] = {
                "reused": bool(intake_result.get("reused")),
                "content_id": intake_result.get("content_id", ""),
                "independent": True,
            }
            if reference:
                result["reference_content"] = reference
            return result
        if not topic_id.strip():
            selection_payload = preparer.prepare_content_stage(root)
            return _prepared_result(root, selection_payload)

        intake_payload = preparer.prepare_content_stage(root, topic_id=topic_id.strip())
        if intake_payload.get("preparation_mode") == "topic_evaluation_required":
            return _prepared_result(root, intake_payload)
        resume_candidates = intake_payload.get("resume_candidates", [])
        if isinstance(resume_candidates, list) and resume_candidates:
            return {
                "ok": True,
                "contract_version": CONTRACT_VERSION,
                "status": "needs_selection",
                "route": intake_payload.get("route", {}),
                "content_candidates": resume_candidates,
                "topic_candidates": [],
                "question": "这个选题已有未完成的内容生产任务，请先确认要继续哪一条；如果要新建内容，请明确说明。",
            }
        topic_evaluation = intake_payload.get("topic_evaluation", {})
        if not isinstance(topic_evaluation, Mapping):
            raise ContentStageCoordinatorError("建档准备结果缺少正式选题结论")
        recommendation = str(topic_evaluation.get("recommendation", "")).strip()
        if recommendation not in {"建议做", "暂缓", "不建议做"}:
            raise ContentStageCoordinatorError("正式选题缺少有效建议结论")
        if recommendation in {"暂缓", "不建议做"} and nonrecommended_decision != "continue":
            message, discussion_label = _nonrecommended_message(
                recommendation,
                str(topic_evaluation.get("reason", "")).strip(),
            )
            continue_request = {
                "action": "prepare",
                "topic_id": topic_id.strip(),
                "nonrecommended_decision": "continue",
            }
            if canonical_media_form:
                continue_request["media_form"] = canonical_media_form
            if content_task:
                continue_request["content_task"] = content_task
            return {
                "ok": True,
                "contract_version": CONTRACT_VERSION,
                "status": "needs_production_decision",
                "topic_id": topic_id.strip(),
                "recommendation": recommendation,
                "reason": str(topic_evaluation.get("reason", "")).strip(),
                "message": message,
                "decision_contract": {
                    "choices": ["继续内容生产", discussion_label],
                    "continue_request": continue_request,
                    "discussion_request": {
                        "skill": "topic-evaluate",
                        "topic_id": topic_id.strip(),
                    },
                },
            }
        intake = _load_sibling("内容生产建档执行器.py", "content_intake_for_coordinator")
        record_candidate = _create_record_candidate(root)
        intake_result = intake.finalize_content_intake(
            root,
            action_data={
                "选题ID": topic_id.strip(),
                "媒介形式": canonical_media_form,
            },
            record_candidate_file=record_candidate,
        )
        # 建档完成后这个空白记录模板就没有读者了；不清理会让每次建档都留下一个暂存目录。
        _cleanup_success_files([record_candidate])
        payload = preparer.prepare_content_stage(root, state_file=intake_result["state_file"])
        result = _prepared_result(root, payload)
        _apply_positioning_identity(root, result, content_task, canonical_media_form)
        if media_form_note:
            result["media_form_note"] = media_form_note
        result["intake"] = {
            "reused": bool(intake_result.get("reused")),
            "content_id": intake_result.get("content_id", ""),
        }
        return result
    except Exception as exc:
        if isinstance(exc, ContentStageCoordinatorError):
            raise
        raise ContentStageCoordinatorError(str(exc)) from exc


def _cleanup_success_files(paths: list[Path]) -> None:
    parents: set[Path] = set()
    for path in paths:
        path.unlink(missing_ok=True)
        parents.add(path.parent)
    for parent in parents:
        try:
            parent.rmdir()
        except OSError:
            pass


def _write_state_action(
    root: Path,
    *,
    state_file: str | Path,
    state_action: str,
    action_data: object | None = None,
    record_candidate_file: str | Path | None = None,
    record_text: str | None = None,
    stage_candidate_file: str | Path | None = None,
    stage_candidate_text: str | None = None,
    content_library_status: str | None = None,
) -> dict[str, object]:
    state_executor = _load_sibling("内容生产状态执行器.py", "content_state_for_coordinator")
    state_path = _workspace_file(root, state_file, "当前阶段状态")
    candidate_path = (
        _workspace_file(root, stage_candidate_file, "阶段记录候选")
        if stage_candidate_file is not None
        else None
    )
    record_candidate_path = (
        _workspace_file(root, record_candidate_file, "内容生产记录候选")
        if record_candidate_file is not None
        else None
    )
    try:
        state_executor._validate_run_candidates(
            root,
            record_candidate_path,
            candidate_path,
        )
        previous = state_executor._read_state(state_path)
        previous_route = state_executor._route_from_state(previous)
        next_state = state_executor.build_state_for_action(previous, state_action, action_data)
        result = state_executor.write_state_atomically(
            state_path,
            next_state,
            record_candidate_file=record_candidate_path,
            record_text=record_text,
            stage_candidate_file=candidate_path,
            stage_candidate_text=stage_candidate_text,
            content_library_status=content_library_status,
            workspace_root=root,
        )
        return {
            "ok": True,
            "contract_version": CONTRACT_VERSION,
            "state_file": _logical_path(root, state_path),
            "previous_route": previous_route,
            **result,
        }
    except Exception as exc:
        raise ContentStageCoordinatorError(str(exc)) from exc


def _read_state_context(root: Path, state_file: str | Path) -> dict[str, object]:
    state_executor = _load_sibling("内容生产状态执行器.py", "content_state_context_for_coordinator")
    state_path = _workspace_file(root, state_file, "当前阶段状态")
    try:
        state = state_executor._read_state(state_path)
        state_executor.validate_state(state)
    except Exception as exc:
        raise ContentStageCoordinatorError(str(exc)) from exc
    stages = state.get("阶段")
    current_stage = str(state.get("当前阶段", "")).strip()
    if not isinstance(stages, Mapping) or current_stage not in stages:
        raise ContentStageCoordinatorError("正式状态缺少当前阶段")
    confirmed = [
        name
        for name in state_executor.STAGES
        if name != "建档与恢复"
        and isinstance(stages.get(name), Mapping)
        and stages[name].get("状态") == "已确认"
    ]
    return {
        "state": state,
        "current_stage": current_stage,
        "confirmed_stages": confirmed,
    }


def _blocked_result(
    root: Path,
    *,
    state_file: str | Path,
    reason: str,
) -> dict[str, object]:
    written = _write_state_action(
        root,
        state_file=state_file,
        state_action="block",
        action_data={"原因": reason},
    )
    state_logical_path = str(written["state_file"])
    return {
        **written,
        "status": "blocked",
        "status_semantic": STATUS_SEMANTICS["被阻断"],
        "error": reason,
        "retry_action": "unblock_then_prepare",
        "recovery_action_contract": _recovery_action_contract(
            state_logical_path,
        ),
    }


def _block_after_failure(
    root: Path,
    *,
    state_file: str | Path,
    reason: str,
    cause: Exception,
) -> dict[str, object]:
    try:
        return _blocked_result(
            root,
            state_file=state_file,
            reason=reason,
        )
    except Exception as block_exc:
        raise ContentStageCoordinatorError(
            f"{reason}；且无法写入阻断状态：{block_exc}"
        ) from cause


def _prepare_or_block(
    root: Path,
    *,
    state_file: str | Path,
    failure_prefix: str,
    success_fields: Mapping[str, object] | None = None,
) -> dict[str, object]:
    try:
        prepared = _prepare(
            root,
            state_file=state_file,
            content_id="",
            topic_id="",
            topic_name="",
            media_form="",
            nonrecommended_decision="",
            content_task="",
            reference_content_id="",
            reference_analysis_path="",
        )
    except Exception as exc:
        reason = f"{failure_prefix}：{exc}"
        return _block_after_failure(
            root,
            state_file=state_file,
            reason=reason,
            cause=exc,
        )
    return {
        **prepared,
        **dict(success_fields or {}),
    }


def _accepted_decisions(stage_name: str, *, mode: str = "") -> list[str]:
    decisions = ACCEPTED_DECISIONS.get((stage_name, mode))
    if decisions is None:
        suffix = f"/{mode}" if mode else ""
        raise ContentStageCoordinatorError(f"未声明的决定提交协议: {stage_name}{suffix}")
    return list(decisions)


def _decide(
    root: Path,
    *,
    state_file: str | Path,
    decision: str,
    target_stage: str,
    reason: str,
) -> dict[str, object]:
    context = _read_state_context(root, state_file)
    stage_name = str(context["current_stage"])
    allowed = _accepted_decisions(stage_name)
    normalized_decision = str(decision or "").strip()
    if normalized_decision not in allowed:
        raise ContentStageCoordinatorError(
            f"当前{stage_name}不接受提交值{normalized_decision or '空'}；可接受值: {', '.join(allowed)}"
        )

    if normalized_decision == "confirm":
        written = _write_state_action(
            root,
            state_file=state_file,
            state_action="confirm-current",
        )
        route = written.get("route", {})
        if stage_name == "内容终稿":
            finalizer = _load_sibling(
                "内容生产收尾执行器.py",
                "content_finalizer_for_confirm",
            )
            try:
                finalized = finalizer.finalize_content_production(
                    root,
                    state_file=_workspace_file(
                        root,
                        written["state_file"],
                        "当前阶段状态",
                    ),
                )
            except Exception as exc:
                return _block_after_failure(
                    root,
                    state_file=written["state_file"],
                    reason=f"确认终稿后完成收尾失败：{exc}",
                    cause=exc,
                )
            return {
                "ok": True,
                "contract_version": CONTRACT_VERSION,
                "status": "completed",
                "status_semantic": STATUS_SEMANTICS["已完成"],
                "completed_stage": stage_name,
                **finalized,
            }
        next_stage = route.get("当前阶段", "") if isinstance(route, Mapping) else ""
        if not next_stage:
            raise ContentStageCoordinatorError("确认后状态机没有返回下一阶段")
        if stage_name == "内容定位":
            try:
                confirmed_context = _read_state_context(root, written["state_file"])
                _sync_confirmed_positioning_media(root, confirmed_context["state"])
            except Exception as exc:
                reason_text = f"确认后同步媒介形式失败：{exc}"
                return _block_after_failure(
                    root,
                    state_file=written["state_file"],
                    reason=reason_text,
                    cause=exc,
                )
        return _prepare_or_block(
            root,
            state_file=written["state_file"],
            failure_prefix="确认后准备下一阶段失败",
            success_fields={
                "completed_stage": stage_name,
                "next_stage": next_stage,
            },
        )

    if normalized_decision == "revise_current":
        written = _write_state_action(
            root,
            state_file=state_file,
            state_action="revise-current",
        )
        return _prepare_or_block(
            root,
            state_file=written["state_file"],
            failure_prefix="重新打开当前阶段后准备失败",
        )

    target = target_stage.strip()
    confirmed_stages = [str(item) for item in context["confirmed_stages"]]
    if not target or target not in confirmed_stages:
        raise ContentStageCoordinatorError(
            "必须明确选择回到协调器返回的某个已确认阶段"
        )
    written = _write_state_action(
        root,
        state_file=state_file,
        state_action="rework-stage",
        action_data={
            "目标阶段": target,
            "原因": reason.strip() or f"用户主动要求回到{target}重新处理，未说明具体原因",
        },
        content_library_status=(
            "生产中" if bool(context["state"].get("当前是否完成")) else None
        ),
    )
    return _prepare_or_block(
        root,
        state_file=written["state_file"],
        failure_prefix=f"回到{target}重新准备失败",
    )


def _save_review_report(
    root: Path,
    *,
    state_file: str | Path,
    review_candidate_file: str | Path | None,
    platforms: str,
) -> dict[str, object]:
    """把手动内容审查结果保存为旁路报告；不改变主流程状态。"""
    if review_candidate_file is None:
        raise ContentStageCoordinatorError("save-review必须提供内容审查候选")
    context = _read_state_context(root, state_file)
    stage_name = str(context["current_stage"])
    state = context["state"]
    if stage_name not in {"内容初稿", "内容终稿"}:
        raise ContentStageCoordinatorError("只有内容初稿或内容终稿可以发起内容审查")
    stages = state.get("阶段")
    stage = stages.get(stage_name) if isinstance(stages, Mapping) else None
    version = int(stage.get("版本", 0)) if isinstance(stage, Mapping) else 0
    if version == 0:
        raise ContentStageCoordinatorError("当前阶段还没有可审查的稿件")

    state_executor = _load_sibling(
        "内容生产状态执行器.py",
        "content_state_for_review",
    )
    record_path = _workspace_file(
        root,
        str(state.get("内容生产记录路径", "")),
        "内容生产记录",
    )
    record = record_path.read_text(encoding="utf-8")
    target_body = state_executor._extract_record_version(record, stage_name, version) or ""
    if not target_body:
        raise ContentStageCoordinatorError("当前阶段版本正文为空，无法审查")

    candidate_path = _workspace_file(root, review_candidate_file, "内容审查候选")
    candidate_text = candidate_path.read_text(encoding="utf-8").strip()
    if not candidate_text:
        raise ContentStageCoordinatorError("内容审查候选不得为空")
    if "## 通用审查" not in candidate_text:
        raise ContentStageCoordinatorError("内容审查候选必须包含「## 通用审查」区块")

    content_dir = str(state.get("当前内容目录", "")).rstrip("/")
    report_dir = (root / content_dir / "过程记录" / "内容审查").resolve()
    try:
        report_dir.relative_to(root)
    except ValueError as exc:
        raise ContentStageCoordinatorError("内容审查报告目录必须位于当前工作区内") from exc
    report_dir.mkdir(parents=True, exist_ok=True)
    existing = sorted(
        int(match.group(1))
        for path in report_dir.glob("审查报告-v*.md")
        if (match := re.fullmatch(r"审查报告-v([1-9][0-9]*)\.md", path.name))
    )
    report_version = (max(existing) if existing else 0) + 1
    report_path = report_dir / f"审查报告-v{report_version}.md"

    platform_text = "、".join(
        item.strip() for item in re.split(r"[、,，]", platforms) if item.strip()
    ) or "未指定（只做通用审查）"
    fingerprint = hashlib.sha256(target_body.encode("utf-8")).hexdigest()[:12]
    header = (
        f"# 内容审查报告 v{report_version}\n\n"
        f"- 审查对象：{stage_name} v{version}\n"
        f"- 审查平台：{platform_text}\n"
        f"- 稿件指纹：{fingerprint}\n"
        f"- 审查时间：{time.strftime('%Y-%m-%d %H:%M:%S')}\n\n"
    )
    report_path.write_text(header + candidate_text + "\n", encoding="utf-8")
    _cleanup_success_files([candidate_path])
    return {
        "ok": True,
        "contract_version": CONTRACT_VERSION,
        "status": "review_saved",
        "status_semantic": "review_saved",
        "state_file": _logical_path(root, _workspace_file(root, state_file, "当前阶段状态")),
        "report_path": _logical_path(root, report_path),
        "report_version": report_version,
        "review_target_stage": stage_name,
        "review_target_version": version,
        "review_platforms": platform_text,
        "review_target_fingerprint": fingerprint,
        "does_not_block_confirmation": True,
    }


def _pending_confirmation_question(stage_name: str) -> str:
    return f"请确认当前{stage_name}是否可以进入下一阶段。"


def dispatch_content_production_task(
    workspace_root: str | Path,
    *,
    action: str,
    state_file: str | Path | None = None,
    content_id: str = "",
    topic_id: str = "",
    topic_name: str = "",
    media_form: str = "",
    nonrecommended_decision: str = "",
    content_task: str = "",
    reference_content_id: str = "",
    reference_analysis_path: str = "",
    stage_candidate_file: str | Path | None = None,
    review_candidate_file: str | Path | None = None,
    review_platforms: str = "",
    expected_stage_version: int | None = None,
    positioning_content: str = "",
    decision: str = "",
    transition_action: str = "",
    target_stage: str = "",
    reason: str = "",
) -> dict[str, object]:
    root = _workspace_root(workspace_root)
    if action == "prepare":
        result = _prepare(
            root,
            state_file=state_file,
            content_id=content_id,
            topic_id=topic_id,
            topic_name=topic_name,
            media_form=media_form,
            nonrecommended_decision=nonrecommended_decision,
            content_task=content_task,
            reference_content_id=reference_content_id,
            reference_analysis_path=reference_analysis_path,
        )
        return result
    if state_file is None:
        raise ContentStageCoordinatorError(f"{action}必须提供正式状态文件")
    if action == "save-review":
        return _save_review_report(
            root,
            state_file=state_file,
            review_candidate_file=review_candidate_file,
            platforms=review_platforms,
        )
    if action == "update-positioning":
        context = _read_state_context(root, state_file)
        if str(context["current_stage"]) != "内容定位":
            raise ContentStageCoordinatorError("update-positioning只能用于内容定位阶段")
        if expected_stage_version is None:
            raise ContentStageCoordinatorError("update-positioning必须提供当前阶段版本")
        if not positioning_content.strip():
            raise ContentStageCoordinatorError("update-positioning必须提供完整定位方案")
        expected_media_form, expected_content_task = _current_positioning_settings(
            root,
            context["state"],
        )
        if _positioning_media_form(positioning_content) != expected_media_form:
            raise ContentStageCoordinatorError(
                "直接修改定位方案时不得改变媒介形式；请按修改设置流程处理"
            )
        if _positioning_content_task(positioning_content) != expected_content_task:
            raise ContentStageCoordinatorError(
                "直接修改定位方案时不得改变内容目标；请按修改设置流程处理"
            )
        result = _write_state_action(
            root,
            state_file=state_file,
            state_action="replace-stage-candidate",
            action_data={
                "预期阶段版本": expected_stage_version,
                "待确认问题": "请确认当前内容定位是否准确；确认后将进入内容初稿。",
            },
            stage_candidate_text=positioning_content,
        )
        return {
            **result,
            "status": "waiting_for_user",
            "status_semantic": STATUS_SEMANTICS["等待用户确认"],
            "current_stage": _stage_contract("内容定位"),
            "decision_contract": _decision_contract(
                state_file=str(result["state_file"]),
                stage_name="内容定位",
                decisions=_accepted_decisions("内容定位"),
            ),
            "display_identity": _display_identity(
                root,
                context["state"],
                media_form=expected_media_form,
                content_task=expected_content_task,
            ),
            "prepare_input_contract": _positioning_prepare_input_contract(
                str(result["state_file"])
            ),
        }
    if action == "reopen-final":
        context = _read_state_context(root, state_file)
        stages = context["state"].get("阶段", {})
        final_stage = stages.get("内容终稿") if isinstance(stages, Mapping) else None
        if (
            str(context["current_stage"]) != "内容终稿"
            or not isinstance(final_stage, Mapping)
            or final_stage.get("状态") != "已确认"
        ):
            raise ContentStageCoordinatorError("只有已确认的内容终稿才能重新打开")
        written = _write_state_action(
            root,
            state_file=state_file,
            state_action="reopen-final",
            action_data={"原因": reason.strip() or "用户要求重新修改已确认终稿"},
            content_library_status="生产中",
        )
        return _prepare_or_block(
            root,
            state_file=written["state_file"],
            failure_prefix="重新打开内容终稿后准备修改资源失败",
            success_fields={
                "reopened_stage": "内容终稿",
                "previous_final_version": int(final_stage.get("版本", 0)),
            },
        )
    if action == "save":
        if stage_candidate_file is None:
            raise ContentStageCoordinatorError("save必须提供阶段记录候选")
        stage_candidate_path = _workspace_file(root, stage_candidate_file, "阶段记录候选")
        context = _read_state_context(root, state_file)
        stage_name = str(context["current_stage"])
        candidate_paths = [stage_candidate_path]
        manifest = _validate_prepared_run(
            root,
            state_file=state_file,
            candidate_paths=candidate_paths,
        )
        positioning_media_form = ""
        positioning_content_task = ""
        action_data: dict[str, object] = {
            "待确认问题": _pending_confirmation_question(stage_name),
        }
        if stage_name == "内容定位":
            positioning_text = stage_candidate_path.read_text(encoding="utf-8")
            positioning_media_form = _positioning_media_form(positioning_text)
            positioning_content_task = _positioning_content_task(positioning_text)
            action_data = {
                "待确认问题": "请确认当前内容定位是否准确；确认后将进入内容初稿。",
                "机器数据更新": {
                    "内容任务": str(manifest["positioning_content_task"]),
                },
            }
        decisions = _accepted_decisions(stage_name)
        allowed_targets = [
            str(item)
            for item in context["confirmed_stages"]
            if str(item) != stage_name
        ]
        result = _write_state_action(
            root,
            state_file=state_file,
            state_action="save-stage-candidate",
            action_data=action_data,
            stage_candidate_file=stage_candidate_path,
        )
        _cleanup_success_files(candidate_paths)
        stage_contract = _stage_contract(stage_name)
        response = {
            **result,
            "status": "waiting_for_user",
            "status_semantic": STATUS_SEMANTICS["等待用户确认"],
            "current_stage": stage_contract,
            "decision_contract": _decision_contract(
                state_file=str(result["state_file"]),
                stage_name=stage_name,
                decisions=decisions,
                allowed_targets=(allowed_targets if "rework_upstream" in decisions else None),
            ),
        }
        saved_context = _read_state_context(root, result["state_file"])
        if stage_name == "内容定位":
            response["display_identity"] = _display_identity(
                root,
                saved_context["state"],
                media_form=positioning_media_form,
                content_task=positioning_content_task,
            )
            response["prepare_input_contract"] = _positioning_prepare_input_contract(
                str(result["state_file"])
            )
        else:
            response["display_identity"] = _display_identity(root, saved_context["state"])
        return response
    if action == "decide":
        return _decide(
            root,
            state_file=state_file,
            decision=decision,
            target_stage=target_stage,
            reason=reason,
        )
    if action == "confirm":
        return _decide(
            root,
            state_file=state_file,
            decision="confirm",
            target_stage=target_stage,
            reason=reason,
        )
    if action == "transition":
        if transition_action not in TRANSITION_ACTIONS:
            raise ContentStageCoordinatorError("transition只允许rework-stage、block或unblock")
        if transition_action == "block":
            return _blocked_result(
                root,
                state_file=state_file,
                reason=reason.strip(),
            )
        if transition_action == "rework-stage":
            data = {"目标阶段": target_stage.strip(), "原因": reason.strip()}
        else:
            data = {}
        content_library_status = None
        if transition_action == "rework-stage":
            previous_context = _read_state_context(root, state_file)
            if bool(previous_context["state"].get("当前是否完成")):
                content_library_status = "生产中"
        written = _write_state_action(
            root,
            state_file=state_file,
            state_action=transition_action,
            action_data=data,
            content_library_status=content_library_status,
        )
        return _prepare_or_block(
            root,
            state_file=written["state_file"],
            failure_prefix=(
                "解除阻断后重新准备失败"
                if transition_action == "unblock"
                else f"回到{target_stage.strip() or '指定阶段'}重新准备失败"
            ),
        )
    raise ContentStageCoordinatorError(f"不支持的协调动作: {action}")


def _add_flag(parser: argparse.ArgumentParser, name: str, **kwargs: object) -> None:
    """登记一个同时接受 --kebab-case 与 --snake_case 的选项。

    回执里的 submit_request_schema / decision_contract 用的是下划线键名
    （state_file、stage_candidate_file 这类），命令行惯用连字符。两种写法都
    接受，照抄回执键名拼命令时不会再撞 unrecognized arguments。
    """
    options = [f"--{name.replace('_', '-')}"]
    if f"--{name}" not in options:
        options.append(f"--{name}")
    parser.add_argument(*options, dest=name, **kwargs)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="内容生产任务级状态调度器")
    _add_flag(parser, "workspace_root", required=True, type=Path)
    _add_flag(
        parser,
        "action",
        required=True,
        choices=("prepare", "save", "save-review", "update-positioning", "reopen-final", "decide", "transition"),
    )
    _add_flag(parser, "state_file", type=Path)
    _add_flag(parser, "content_id", default="")
    _add_flag(parser, "topic_id", default="")
    _add_flag(parser, "topic_name", default="", help="独立生产时使用的临时标题")
    _add_flag(parser, "media_form", default="")
    _add_flag(parser, "nonrecommended_decision", choices=("", "continue"), default="")
    _add_flag(parser, "content_task", choices=("", "流量", "涨粉", "交易"), default="")
    _add_flag(parser, "reference_content_id", default="", help="对标内容直达生产时使用的参考内容ID")
    _add_flag(parser, "reference_analysis_path", default="", help="对标内容已正式保存的内容分析路径")
    _add_flag(parser, "stage_candidate_file", type=Path)
    _add_flag(parser, "review_candidate_file", type=Path)
    _add_flag(parser, "review_platforms", default="")
    _add_flag(parser, "expected_stage_version", type=int)
    _add_flag(parser, "positioning_content", default="")
    _add_flag(parser, "decision", default="")
    _add_flag(parser, "transition_action", choices=sorted(TRANSITION_ACTIONS), default="")
    _add_flag(parser, "target_stage", default="")
    _add_flag(parser, "reason", default="")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    started_at = time.perf_counter()
    try:
        result = dispatch_content_production_task(
            args.workspace_root,
            action=args.action,
            state_file=args.state_file,
            content_id=args.content_id,
            topic_id=args.topic_id,
            topic_name=args.topic_name,
            media_form=args.media_form,
            nonrecommended_decision=args.nonrecommended_decision,
            content_task=args.content_task,
            reference_content_id=args.reference_content_id,
            reference_analysis_path=args.reference_analysis_path,
            stage_candidate_file=args.stage_candidate_file,
            review_candidate_file=args.review_candidate_file,
            review_platforms=args.review_platforms,
            expected_stage_version=args.expected_stage_version,
            positioning_content=args.positioning_content,
            decision=args.decision,
            transition_action=args.transition_action,
            target_stage=args.target_stage,
            reason=args.reason,
        )
    except ContentStageCoordinatorError as exc:
        print(
            json.dumps(
                {
                    "ok": False,
                    "error_code": "content_stage_coordination_failed",
                    "message": str(exc),
                    "elapsed_ms": round((time.perf_counter() - started_at) * 1000, 2),
                },
                ensure_ascii=False,
            ),
            file=sys.stderr,
        )
        return 1
    print(
        json.dumps(
            {**result, "elapsed_ms": round((time.perf_counter() - started_at) * 1000, 2)},
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


# Backward-compatible import name for existing local callers and tests.
coordinate_content_stage = dispatch_content_production_task
