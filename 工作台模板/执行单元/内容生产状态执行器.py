#!/usr/bin/env python3
from __future__ import annotations

import argparse
import copy
import csv
import hashlib
import io
import json
import os
import re
import tempfile
import time
import unicodedata
from collections import Counter
from contextlib import contextmanager
from pathlib import Path
from pathlib import PurePosixPath
from typing import Callable, Mapping


class StateValidationError(ValueError):
    """Raised when a content-production state does not match the contract."""


class StateTransitionError(ValueError):
    """Raised when a state change violates workflow transition rules."""


class StateWriteError(RuntimeError):
    """Raised when a validated state cannot be written atomically."""


ReplaceOperation = Callable[[Path, Path], object]

PROTOCOL_VERSION = 3
WORKFLOW_NAME = "内容生产"
STAGES = ("建档与恢复", "内容定位", "内容初稿", "内容终稿")
STAGE_STATUS = {
    "未开始",
    "进行中",
    "等待用户确认",
    "已确认",
    "已失效",
    "被阻断",
}
FLOW_STATUS = {"进行中", "等待用户确认", "已完成", "被阻断"}

TOP_LEVEL_KEYS = {
    "状态协议版本",
    "工作流",
    "内容ID",
    "选题ID",
    "当前内容目录",
    "内容生产记录路径",
    "当前阶段",
    "流程状态",
    "当前是否完成",
    "是否需要用户确认",
    "下一步推荐",
    "允许的下一步",
    "阶段",
    "失效记录",
    "阻断原因",
    "机器数据",
}
OPTIONAL_TOP_LEVEL_KEYS: set[str] = set()
STAGE_KEYS = {"版本", "状态", "依赖版本"}
INVALIDATION_KEYS = {"是否存在", "起始阶段", "失效阶段", "原因"}
MACHINE_DATA_REQUIRED_KEYS = {"内容任务", "待确认问题"}
MACHINE_DATA_OPTIONAL_KEYS: set[str] = set()
MACHINE_DATA_KEYS = MACHINE_DATA_REQUIRED_KEYS
MACHINE_DATA_BASE_KEYS = MACHINE_DATA_REQUIRED_KEYS
STABLE_KEYS = (
    "工作流",
    "内容ID",
    "选题ID",
    "当前内容目录",
    "内容生产记录路径",
)
DIRECT_DEPENDENCY = {
    "建档与恢复": None,
    "内容定位": "建档与恢复",
    "内容初稿": "内容定位",
    "内容终稿": "内容初稿",
}
DEPENDENCY_KEYS = {
    "建档与恢复": set(),
    "内容定位": set(),
    "内容初稿": {"内容定位"},
    "内容终稿": {"内容初稿"},
}
ACTIVE_STAGE_STATUS = {"进行中", "等待用户确认", "已确认"}
VERSIONED_STAGE_STATUS = {"等待用户确认", "已确认", "已失效"}
ALLOWED_STAGE_TRANSITIONS = {
    "未开始": {"未开始", "进行中", "等待用户确认", "被阻断"},
    "进行中": {"进行中", "等待用户确认", "被阻断", "已失效"},
    "等待用户确认": {"进行中", "等待用户确认", "已确认", "被阻断", "已失效"},
    "已确认": {"已确认", "进行中", "等待用户确认", "已失效"},
    "已失效": {"已失效", "未开始"},
    "被阻断": {"被阻断", "未开始", "进行中", "等待用户确认", "已失效"},
}
RECORD_SECTIONS = {
    "内容定位": ("内容定位",),
    "内容初稿": ("内容初稿",),
    "内容终稿": ("内容终稿",),
}
RECORD_VERSION_PLACEHOLDER = "vNEXT"
MAX_TITLE_DISPLAY_CHARACTERS = 20
ACTIONS = {
    "initialize",
    "save-stage-candidate",
    "replace-stage-candidate",
    "revise-current",
    "reopen-final",
    "confirm-current",
    "rework-stage",
    "block",
    "unblock",
    "complete",
}


def _require_mapping(value: object, label: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise StateValidationError(f"{label}必须是对象")
    return value


def _require_exact_keys(value: Mapping[str, object], expected: set[str], label: str) -> None:
    actual = set(value)
    if actual == expected:
        return
    missing = sorted(expected - actual)
    unknown = sorted(actual - expected)
    details: list[str] = []
    if missing:
        details.append(f"缺少字段: {', '.join(missing)}")
    if unknown:
        details.append(f"未知字段: {', '.join(unknown)}")
    raise StateValidationError(f"{label}字段不符合合同（{'；'.join(details)}）")


def _require_string(value: object, label: str, *, allow_empty: bool = False) -> str:
    if not isinstance(value, str):
        raise StateValidationError(f"{label}必须是字符串")
    if not allow_empty and not value.strip():
        raise StateValidationError(f"{label}不得为空")
    return value


def _require_bool(value: object, label: str) -> bool:
    if not isinstance(value, bool):
        raise StateValidationError(f"{label}必须是布尔值")
    return value


def _require_version(value: object, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise StateValidationError(f"{label}必须是非负整数")
    return value


def _require_logical_path(value: object, label: str) -> str:
    path_value = _require_string(value, label)
    path = PurePosixPath(path_value)
    if path.is_absolute() or ".." in path.parts or "\\" in path_value:
        raise StateValidationError(f"{label}必须是工作区内使用正斜杠的相对路径")
    if str(path) in {"", "."}:
        raise StateValidationError(f"{label}不得指向工作区根目录")
    return path.as_posix()


def _validate_stage(stage_name: str, raw_stage: object) -> Mapping[str, object]:
    stage = _require_mapping(raw_stage, f"阶段.{stage_name}")
    _require_exact_keys(stage, STAGE_KEYS, f"阶段.{stage_name}")
    version = _require_version(stage["版本"], f"阶段.{stage_name}.版本")
    status = _require_string(stage["状态"], f"阶段.{stage_name}.状态")
    if status not in STAGE_STATUS:
        raise StateValidationError(f"阶段.{stage_name}.状态不是允许值: {status}")
    if status in VERSIONED_STAGE_STATUS and version == 0:
        raise StateValidationError(f"阶段.{stage_name}处于{status}时版本必须大于0")

    dependencies = _require_mapping(stage["依赖版本"], f"阶段.{stage_name}.依赖版本")
    expected_dependencies = DEPENDENCY_KEYS[stage_name]
    if set(dependencies) != expected_dependencies:
        raise StateValidationError(
            f"阶段.{stage_name}.依赖版本必须且只能包含: "
            f"{', '.join(sorted(expected_dependencies)) or '无'}"
        )
    for dependency_name, dependency_version in dependencies.items():
        _require_version(dependency_version, f"阶段.{stage_name}.依赖版本.{dependency_name}")
    return stage


def _require_unique_string_list(
    value: object,
    label: str,
    *,
    allowed_values: set[str] | None = None,
) -> list[str]:
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        raise StateValidationError(f"{label}必须是字符串数组")
    if any(not item.strip() for item in value):
        raise StateValidationError(f"{label}不得包含空字符串")
    if len(set(value)) != len(value):
        raise StateValidationError(f"{label}不得包含重复值")
    if allowed_values is not None:
        invalid = sorted(set(value) - allowed_values)
        if invalid:
            raise StateValidationError(f"{label}包含非法值: {', '.join(invalid)}")
    return list(value)


def _validate_machine_data(raw_machine_data: object) -> None:
    machine_data = _require_mapping(raw_machine_data, "机器数据")
    keys = set(machine_data)
    if not MACHINE_DATA_REQUIRED_KEYS <= keys or (
        keys - MACHINE_DATA_REQUIRED_KEYS - MACHINE_DATA_OPTIONAL_KEYS
    ):
        raise StateValidationError("机器数据字段不符合合同")
    _require_string(machine_data.get("内容任务", ""), "机器数据.内容任务", allow_empty=True)
    if machine_data.get("内容任务", "") and machine_data["内容任务"] not in {"流量", "涨粉", "交易"}:
        raise StateValidationError("机器数据.内容任务必须是流量、涨粉或交易")
    _require_string(machine_data["待确认问题"], "机器数据.待确认问题", allow_empty=True)



def validate_state(state: object) -> None:
    root = _require_mapping(state, "状态")
    if "状态协议版本" not in root:
        raise StateValidationError("状态字段不符合合同（缺少字段: 状态协议版本）")
    protocol_version = _require_version(root["状态协议版本"], "状态协议版本")
    if protocol_version != PROTOCOL_VERSION:
        raise StateValidationError(f"不支持的状态协议版本: {protocol_version}")
    root_keys = set(root)
    missing_keys = TOP_LEVEL_KEYS - root_keys
    unknown_keys = root_keys - TOP_LEVEL_KEYS - OPTIONAL_TOP_LEVEL_KEYS
    if missing_keys or unknown_keys:
        details: list[str] = []
        if missing_keys:
            details.append(f"缺少字段: {', '.join(sorted(missing_keys))}")
        if unknown_keys:
            details.append(f"未知字段: {', '.join(sorted(unknown_keys))}")
        raise StateValidationError(f"状态字段不符合合同（{'；'.join(details)}）")
    _validate_machine_data(root["机器数据"])
    machine_data = _require_mapping(root["机器数据"], "机器数据")
    pending_question = str(machine_data["待确认问题"])
    if _require_string(root["工作流"], "工作流") != WORKFLOW_NAME:
        raise StateValidationError(f"工作流必须是{WORKFLOW_NAME}")
    _require_string(root["内容ID"], "内容ID")
    _require_string(root["选题ID"], "选题ID", allow_empty=True)
    current_content_dir = _require_logical_path(root["当前内容目录"], "当前内容目录")
    production_record_path = _require_logical_path(
        root["内容生产记录路径"], "内容生产记录路径"
    )
    expected_record_path = f"{current_content_dir.rstrip('/')}/内容生产记录.md"
    if production_record_path != expected_record_path:
        raise StateValidationError("内容生产记录路径必须位于当前内容目录并指向内容生产记录.md")

    current_stage = _require_string(root["当前阶段"], "当前阶段")
    if current_stage not in STAGES:
        raise StateValidationError(f"当前阶段不是允许值: {current_stage}")
    flow_status = _require_string(root["流程状态"], "流程状态")
    if flow_status not in FLOW_STATUS:
        raise StateValidationError(f"流程状态不是允许值: {flow_status}")
    is_complete = _require_bool(root["当前是否完成"], "当前是否完成")
    needs_confirmation = _require_bool(root["是否需要用户确认"], "是否需要用户确认")

    next_recommendation = _require_string(root["下一步推荐"], "下一步推荐", allow_empty=True)
    if next_recommendation and next_recommendation not in STAGES:
        raise StateValidationError(f"下一步推荐不是允许阶段: {next_recommendation}")
    allowed_next = root["允许的下一步"]
    if not isinstance(allowed_next, list) or any(not isinstance(item, str) for item in allowed_next):
        raise StateValidationError("允许的下一步必须是阶段名字符串数组")
    if len(set(allowed_next)) != len(allowed_next):
        raise StateValidationError("允许的下一步不得包含重复值")
    invalid_next = [item for item in allowed_next if item not in STAGES]
    if invalid_next:
        raise StateValidationError(f"允许的下一步包含非法阶段: {', '.join(invalid_next)}")
    if next_recommendation and next_recommendation not in allowed_next:
        raise StateValidationError("下一步推荐必须包含在允许的下一步中")
    if not is_complete and (
        next_recommendation != current_stage or allowed_next != [current_stage]
    ):
        raise StateValidationError("未完成流程的下一步推荐和允许列表必须唯一指向当前阶段")

    stages = _require_mapping(root["阶段"], "阶段")
    if set(stages) != set(STAGES):
        raise StateValidationError("阶段必须且只能包含四个固定阶段")
    normalized_stages = {name: _validate_stage(name, stages[name]) for name in STAGES}

    if normalized_stages[current_stage]["状态"] == "已失效":
        raise StateValidationError("当前阶段不得是已失效阶段")
    if current_stage != "内容终稿" and normalized_stages[current_stage]["状态"] == "已确认":
        raise StateValidationError("非终稿阶段确认后必须把当前阶段推进到直接下游")
    for stage_name, stage in normalized_stages.items():
        if stage_name != current_stage and stage["状态"] in {
            "进行中",
            "等待用户确认",
            "被阻断",
        }:
            raise StateValidationError(f"只有当前阶段可以处于活动状态: {stage_name}")

    for stage_name in STAGES:
        stage = normalized_stages[stage_name]
        if stage["状态"] not in ACTIVE_STAGE_STATUS and stage_name != current_stage:
            continue
        predecessor = DIRECT_DEPENDENCY[stage_name]
        if predecessor is None:
            continue
        predecessor_stage = normalized_stages[predecessor]
        if predecessor_stage["状态"] != "已确认":
            raise StateValidationError(f"{stage_name}前进前必须确认上游阶段{predecessor}")
        if predecessor in stage["依赖版本"]:
            dependency_version = stage["依赖版本"][predecessor]
            if dependency_version != predecessor_stage["版本"]:
                raise StateValidationError(
                    f"{stage_name}依赖的{predecessor}版本与当前已确认版本不一致"
                )

    invalidation = _require_mapping(root["失效记录"], "失效记录")
    _require_exact_keys(invalidation, INVALIDATION_KEYS, "失效记录")
    has_invalidation = _require_bool(invalidation["是否存在"], "失效记录.是否存在")
    invalidation_start = _require_string(
        invalidation["起始阶段"], "失效记录.起始阶段", allow_empty=True
    )
    invalid_stages = invalidation["失效阶段"]
    if not isinstance(invalid_stages, list) or any(not isinstance(item, str) for item in invalid_stages):
        raise StateValidationError("失效记录.失效阶段必须是阶段名字符串数组")
    if len(set(invalid_stages)) != len(invalid_stages):
        raise StateValidationError("失效记录.失效阶段不得包含重复值")
    if any(item not in STAGES for item in invalid_stages):
        raise StateValidationError("失效记录.失效阶段包含非法阶段")
    invalidation_reason = _require_string(
        invalidation["原因"], "失效记录.原因", allow_empty=True
    )
    invalid_stage_states = {
        stage_name
        for stage_name, stage in normalized_stages.items()
        if stage["状态"] == "已失效"
    }
    if has_invalidation:
        if invalidation_start not in STAGES or not invalid_stages or not invalidation_reason:
            raise StateValidationError("存在失效时必须填写起始阶段、失效阶段和原因")
        start_index = STAGES.index(invalidation_start)
        if any(STAGES.index(item) <= start_index for item in invalid_stages):
            raise StateValidationError("失效阶段必须位于起始阶段之后")
        if any(normalized_stages[item]["状态"] != "已失效" for item in invalid_stages):
            raise StateValidationError("失效记录中的阶段状态必须是已失效")
        if invalid_stages != sorted(invalid_stages, key=STAGES.index):
            raise StateValidationError("失效记录.失效阶段必须按阶段顺序排列")
        if set(invalid_stages) != invalid_stage_states:
            raise StateValidationError("失效记录必须完整列出所有已失效阶段")
    elif invalidation_start or invalid_stages or invalidation_reason:
        raise StateValidationError("无失效时失效记录的其余字段必须为空")
    elif invalid_stage_states:
        raise StateValidationError("存在已失效阶段时必须填写失效记录")

    blocked_reason = _require_string(root["阻断原因"], "阻断原因", allow_empty=True)
    if flow_status == "等待用户确认":
        if not needs_confirmation or normalized_stages[current_stage]["状态"] != "等待用户确认":
            raise StateValidationError("等待用户确认时流程、确认标记和当前阶段状态必须一致")
        if not pending_question:
            raise StateValidationError("等待用户确认时必须填写机器数据.待确认问题")
    elif needs_confirmation:
        raise StateValidationError("只有等待用户确认状态才允许设置确认标记")
    elif normalized_stages[current_stage]["状态"] == "等待用户确认":
        raise StateValidationError("当前阶段等待用户确认时流程状态必须同步")
    elif pending_question:
        raise StateValidationError("不等待用户确认时机器数据.待确认问题必须为空")

    if flow_status == "被阻断":
        if not blocked_reason or normalized_stages[current_stage]["状态"] not in {
            "被阻断",
            "已确认",
        }:
            raise StateValidationError("被阻断时必须填写原因，且当前阶段必须被阻断或已确认")
        if normalized_stages[current_stage]["状态"] == "已确认" and current_stage != "内容终稿":
            raise StateValidationError("只有终稿确认后的机械收尾失败可保留已确认阶段")
    elif blocked_reason:
        raise StateValidationError("流程未被阻断时阻断原因必须为空")
    elif normalized_stages[current_stage]["状态"] == "被阻断":
        raise StateValidationError("当前阶段被阻断时流程状态必须同步")

    if is_complete:
        if flow_status != "已完成" or any(
            normalized_stages[name]["状态"] != "已确认" for name in STAGES
        ):
            raise StateValidationError("流程完成时四个阶段必须全部确认")
        if allowed_next or next_recommendation:
            raise StateValidationError("流程完成时不得再推荐下一步")
        if current_stage != "内容终稿":
            raise StateValidationError("流程完成时当前阶段必须保留为内容终稿")
    elif flow_status == "已完成":
        raise StateValidationError("流程状态为已完成时当前是否完成必须为true")


def validate_transition(previous: object, next_state: object) -> None:
    validate_state(previous)
    validate_state(next_state)
    previous_root = _require_mapping(previous, "旧状态")
    next_root = _require_mapping(next_state, "新状态")

    previous_protocol = int(previous_root["状态协议版本"])
    next_protocol = int(next_root["状态协议版本"])
    if previous_protocol != PROTOCOL_VERSION or next_protocol != PROTOCOL_VERSION:
        raise StateTransitionError(f"状态协议版本必须保持为{PROTOCOL_VERSION}")

    for key in STABLE_KEYS:
        if previous_root[key] != next_root[key]:
            raise StateTransitionError(f"稳定字段不得变化: {key}")

    previous_stages = _require_mapping(previous_root["阶段"], "旧状态.阶段")
    next_stages = _require_mapping(next_root["阶段"], "新状态.阶段")
    changed_upstream: list[str] = []
    rework_roots: list[str] = []
    changed_versions: list[str] = []
    newly_confirmed: list[str] = []
    restored_invalid_stages: list[str] = []
    for stage_name in STAGES:
        previous_stage = _require_mapping(previous_stages[stage_name], f"旧状态.阶段.{stage_name}")
        next_stage = _require_mapping(next_stages[stage_name], f"新状态.阶段.{stage_name}")
        previous_version = int(previous_stage["版本"])
        next_version = int(next_stage["版本"])
        previous_status = str(previous_stage["状态"])
        next_status = str(next_stage["状态"])
        if next_status not in ALLOWED_STAGE_TRANSITIONS[previous_status]:
            raise StateTransitionError(
                f"阶段状态不得从{previous_status}转换为{next_status}: {stage_name}"
            )
        if next_version < previous_version:
            raise StateTransitionError(f"阶段版本不得倒退: {stage_name}")
        if next_version > previous_version + 1:
            raise StateTransitionError(f"单次状态提交只能让阶段版本增加1: {stage_name}")
        if next_version > previous_version:
            if next_status != "等待用户确认":
                raise StateTransitionError(f"阶段版本只能在形成等待确认候选时增加: {stage_name}")
            changed_versions.append(stage_name)
        if previous_stage["状态"] != "已确认" and next_stage["状态"] == "已确认":
            newly_confirmed.append(stage_name)
        if previous_status == "已失效" and next_status == "未开始":
            restored_invalid_stages.append(stage_name)
        if (
            next_status == "等待用户确认"
            and previous_status != "等待用户确认"
            and next_version != previous_version + 1
        ):
            raise StateTransitionError(f"阶段进入等待确认时必须形成一个新版本: {stage_name}")
        if (
            previous_status == "已确认"
            and next_status == "等待用户确认"
            and next_version != previous_version + 1
        ):
            raise StateTransitionError(f"已确认阶段重新形成候选时版本必须增加1: {stage_name}")
        entered_rework = previous_stage["状态"] == "已确认" and next_stage["状态"] != "已确认"
        if next_version > previous_version or entered_rework:
            changed_upstream.append(stage_name)
        if (
            next_stage["状态"] != "已失效"
            and (next_version > previous_version or entered_rework)
        ):
            rework_roots.append(stage_name)

    if len(changed_versions) > 1:
        raise StateTransitionError("每次状态提交最多只能形成一个语义阶段的新版本")
    if len(newly_confirmed) > 1:
        raise StateTransitionError("每次状态提交最多只能确认一个语义阶段")
    if changed_versions and newly_confirmed:
        raise StateTransitionError("确认阶段和形成下游候选必须分开提交")

    previous_current_stage = str(previous_root["当前阶段"])
    previous_current = _require_mapping(
        previous_stages[previous_current_stage],
        f"旧状态.阶段.{previous_current_stage}",
    )
    next_current = _require_mapping(
        next_stages[previous_current_stage],
        f"新状态.阶段.{previous_current_stage}",
    )
    if (
        previous_current["状态"] == "等待用户确认"
        and next_current["状态"] == "等待用户确认"
        and int(previous_current["版本"]) == int(next_current["版本"])
        and previous_root != next_root
    ):
        raise StateTransitionError("同版本等待确认只能幂等重试，内容变化必须形成新版本")

    if newly_confirmed:
        confirmed_stage = newly_confirmed[0]
        previous_confirmed = _require_mapping(
            previous_stages[confirmed_stage], f"旧状态.阶段.{confirmed_stage}"
        )
        if (
            confirmed_stage != previous_current_stage
            or previous_confirmed["状态"] != "等待用户确认"
        ):
            raise StateTransitionError("只能确认旧状态中正在等待用户确认的当前阶段")
        previous_machine = dict(
            _require_mapping(previous_root["机器数据"], "旧状态.机器数据")
        )
        next_machine = dict(
            _require_mapping(next_root["机器数据"], "新状态.机器数据")
        )
        previous_machine.pop("待确认问题", None)
        next_machine.pop("待确认问题", None)
        if previous_machine != next_machine:
            raise StateTransitionError("确认阶段时不得改写机器数据，只允许清空待确认问题")
    if len(restored_invalid_stages) > 1:
        raise StateTransitionError("每次状态提交最多只能恢复一个已失效阶段")
    if restored_invalid_stages:
        restored_stage = restored_invalid_stages[0]
        predecessor = DIRECT_DEPENDENCY[restored_stage]
        if (
            next_root["当前阶段"] != restored_stage
            or predecessor is None
            or newly_confirmed != [predecessor]
        ):
            raise StateTransitionError("已失效阶段只能在其直接上游本次确认后恢复为当前未开始阶段")

    next_invalidation = _require_mapping(next_root["失效记录"], "新状态.失效记录")
    previous_invalidation = _require_mapping(previous_root["失效记录"], "旧状态.失效记录")
    if not previous_invalidation["是否存在"] and next_invalidation["是否存在"]:
        if (
            len(rework_roots) != 1
            or next_invalidation["起始阶段"] != rework_roots[0]
        ):
            raise StateTransitionError("首次产生失效记录时，起始阶段必须是本次重做的唯一阶段")
    for stage_name in changed_upstream:
        stage_index = STAGES.index(stage_name)
        previously_progressed = [
            downstream
            for downstream in STAGES[stage_index + 1 :]
            if _require_mapping(
                previous_stages[downstream], f"旧状态.阶段.{downstream}"
            )["状态"]
            not in {"未开始", "已失效"}
        ]
        if not previously_progressed:
            continue
        invalidated = set(next_invalidation["失效阶段"])
        if not next_invalidation["是否存在"] or not set(previously_progressed).issubset(invalidated):
            raise StateTransitionError(f"重做{stage_name}时必须显式失效所有已推进的下游阶段")

    if not bool(previous_root["当前是否完成"]) and bool(next_root["当前是否完成"]):
        previous_final = _require_mapping(
            previous_stages["内容终稿"], "旧状态.阶段.内容终稿"
        )
        if previous_final["状态"] != "已确认":
            raise StateTransitionError("流程完成前必须先确认内容终稿")

    if bool(previous_root["当前是否完成"]) and not bool(next_root["当前是否完成"]):
        if next_root["流程状态"] not in {"进行中", "等待用户确认"} or not changed_upstream:
            raise StateTransitionError("已完成流程只能因明确形成阶段重做而重新进入生产")


def _ensure_target_matches_state(
    target_path: Path,
    state: object,
    workspace_root: str | Path,
) -> None:
    root = Path(workspace_root).resolve()
    state_root = _require_mapping(state, "状态")
    logical_content_dir = _require_logical_path(state_root["当前内容目录"], "当前内容目录")
    expected_target = (root / logical_content_dir / "过程记录" / "当前阶段状态.json").resolve()
    try:
        expected_target.relative_to(root)
    except ValueError as exc:
        raise StateWriteError("状态文件目标路径不得逃逸当前工作区") from exc
    if target_path.resolve() != expected_target:
        raise StateWriteError("状态文件目标路径与候选状态中的当前内容目录不一致")


def _resolve_workspace_path(
    workspace_root: Path,
    logical_path: str,
    label: str,
) -> Path:
    resolved = (workspace_root / logical_path).resolve()
    try:
        resolved.relative_to(workspace_root)
    except ValueError as exc:
        raise StateWriteError(f"{label}不得逃逸当前工作区") from exc
    return resolved


def _resolve_workspace_file(
    workspace_root: Path,
    logical_path: str,
    label: str,
) -> Path:
    resolved = _resolve_workspace_path(workspace_root, logical_path, label)
    if not resolved.is_file():
        raise StateWriteError(f"{label}不存在或不是普通文件: {resolved}")
    return resolved


def _extract_record_version(markdown: str, section_name: str, version: int) -> str | None:
    section_names = tuple(name for names in RECORD_SECTIONS.values() for name in names)
    section_pattern = re.compile(
        r"^## (" + "|".join(re.escape(name) for name in section_names) + r")[ \t]*$",
        flags=re.MULTILINE,
    )
    headings = list(section_pattern.finditer(markdown))
    matching_sections = [
        (index, heading)
        for index, heading in enumerate(headings)
        if heading.group(1) == section_name
    ]
    if len(matching_sections) > 1:
        raise StateWriteError(f"内容生产记录包含重复阶段标题: ## {section_name}")
    if not matching_sections:
        return None
    index, heading = matching_sections[0]
    section_end = headings[index + 1].start() if index + 1 < len(headings) else len(markdown)
    section_body = markdown[heading.end() : section_end]
    version_headings = list(
        re.finditer(r"^### v([1-9][0-9]*)[ \t]*$", section_body, flags=re.MULTILINE)
    )
    duplicate_versions = sorted(
        version_number
        for version_number, count in Counter(
            heading.group(1) for heading in version_headings
        ).items()
        if count > 1
    )
    if duplicate_versions:
        raise StateWriteError(
            f"内容生产记录的## {section_name}包含重复版本标题: "
            + ", ".join(f"v{item}" for item in duplicate_versions)
        )
    version_heading = next(
        (heading for heading in version_headings if int(heading.group(1)) == version),
        None,
    )
    if version_heading is None:
        return None
    next_version = next(
        (heading for heading in version_headings if heading.start() > version_heading.start()),
        None,
    )
    body_end = next_version.start() if next_version is not None else len(section_body)
    return section_body[version_heading.end() : body_end].strip()


def _replace_record_version_body(
    markdown: str,
    section_name: str,
    version: int,
    replacement_body: str,
) -> str:
    """替换已存在的单一阶段版本正文，不新增版本。"""
    section_names = tuple(name for names in RECORD_SECTIONS.values() for name in names)
    section_pattern = re.compile(
        r"^## (" + "|".join(re.escape(name) for name in section_names) + r")[ \t]*$",
        flags=re.MULTILINE,
    )
    headings = list(section_pattern.finditer(markdown))
    matching_sections = [
        (index, heading)
        for index, heading in enumerate(headings)
        if heading.group(1) == section_name
    ]
    if len(matching_sections) != 1:
        raise StateWriteError(f"内容生产记录必须且只能包含一个阶段标题: ## {section_name}")
    section_index, section_heading = matching_sections[0]
    section_end = headings[section_index + 1].start() if section_index + 1 < len(headings) else len(markdown)
    section_body = markdown[section_heading.end() : section_end]
    version_headings = list(
        re.finditer(r"^### v([1-9][0-9]*)[ \t]*$", section_body, flags=re.MULTILINE)
    )
    matching_versions = [
        heading for heading in version_headings if int(heading.group(1)) == version
    ]
    if len(matching_versions) != 1 or len(version_headings) != 1:
        raise StateWriteError(
            f"内容生产记录的## {section_name}必须且只能包含当前版本v{version}"
        )
    version_heading = matching_versions[0]
    body_start = section_heading.end() + version_heading.end()
    normalized_body = replacement_body.strip()
    if not normalized_body:
        raise StateWriteError("阶段版本正文不得为空")
    return markdown[:body_start] + "\n\n" + normalized_body + "\n\n" + markdown[section_end:]


def _materialize_record_candidate_version(markdown: str, state: object) -> str:
    placeholder_heading = f"### {RECORD_VERSION_PLACEHOLDER}"
    if not re.search(rf"^{re.escape(placeholder_heading)}[ \t]*$", markdown, flags=re.MULTILINE):
        return markdown

    root = _require_mapping(state, "状态")
    stages = _require_mapping(root["阶段"], "阶段")
    current_stage = str(root["当前阶段"])
    if current_stage not in RECORD_SECTIONS:
        raise StateWriteError("当前阶段不允许在记录候选中使用vNEXT")
    current = _require_mapping(stages[current_stage], f"阶段.{current_stage}")
    if current["状态"] != "等待用户确认":
        raise StateWriteError("只有等待用户确认的阶段候选可以使用vNEXT")
    version = int(current["版本"])

    section_names = tuple(name for names in RECORD_SECTIONS.values() for name in names)
    section_pattern = re.compile(
        r"^## (" + "|".join(re.escape(name) for name in section_names) + r")[ \t]*$",
        flags=re.MULTILINE,
    )
    headings = list(section_pattern.finditer(markdown))
    allowed_sections = set(RECORD_SECTIONS[current_stage])
    parts: list[str] = []
    cursor = 0
    replacements = 0
    for index, heading in enumerate(headings):
        section_end = headings[index + 1].start() if index + 1 < len(headings) else len(markdown)
        parts.append(markdown[cursor : heading.end()])
        body = markdown[heading.end() : section_end]
        placeholder_count = len(
            re.findall(rf"^{re.escape(placeholder_heading)}[ \t]*$", body, flags=re.MULTILINE)
        )
        if placeholder_count and heading.group(1) not in allowed_sections:
            raise StateWriteError("vNEXT只能出现在当前阶段的记录区块")
        if placeholder_count > 1:
            raise StateWriteError(f"## {heading.group(1)}只能包含一个vNEXT")
        if placeholder_count == 1:
            body = re.sub(
                rf"^{re.escape(placeholder_heading)}[ \t]*$",
                f"### v{version}",
                body,
                count=1,
                flags=re.MULTILINE,
            )
            replacements += 1
        parts.append(body)
        cursor = section_end
    parts.append(markdown[cursor:])
    materialized = "".join(parts)
    if replacements == 0 or re.search(
        rf"^{re.escape(placeholder_heading)}[ \t]*$", materialized, flags=re.MULTILINE
    ):
        raise StateWriteError("记录候选中的vNEXT无法对应当前阶段")
    return materialized


def _parse_stage_candidate(markdown: str, state: object) -> dict[str, str]:
    root = _require_mapping(state, "状态")
    stages = _require_mapping(root["阶段"], "阶段")
    current_stage = str(root["当前阶段"])
    if current_stage not in RECORD_SECTIONS:
        raise StateWriteError("当前阶段不接受阶段记录候选")
    current = _require_mapping(stages[current_stage], f"阶段.{current_stage}")
    if current["状态"] != "等待用户确认":
        raise StateWriteError("只有等待用户确认的当前阶段可以提交阶段记录候选")

    headings = list(re.finditer(r"^##[ \t]+(.+?)[ \t]*$", markdown, flags=re.MULTILINE))
    actual_sections = [heading.group(1).strip() for heading in headings]
    expected_sections = list(RECORD_SECTIONS[current_stage])
    if actual_sections != expected_sections:
        raise StateWriteError(
            "阶段记录候选只能按顺序包含当前阶段区块: "
            + "、".join(f"## {name}" for name in expected_sections)
        )
    if markdown[: headings[0].start()].strip():
        raise StateWriteError("阶段记录候选不得包含当前阶段区块以外的正文")
    if re.search(r"^###[ \t]+v(?:NEXT|[1-9][0-9]*)[ \t]*$", markdown, flags=re.MULTILINE):
        raise StateWriteError("阶段记录候选不得填写版本标题，版本由执行器生成")

    sections: dict[str, str] = {}
    for index, heading in enumerate(headings):
        section_name = actual_sections[index]
        section_end = headings[index + 1].start() if index + 1 < len(headings) else len(markdown)
        body = markdown[heading.end() : section_end].strip()
        if not body and section_name != "终稿":
            raise StateWriteError(f"阶段记录候选的## {section_name}不得为空")
        sections[section_name] = body
    return sections


def _merge_stage_candidate(markdown: str, stage_candidate: str, state: object) -> str:
    root = _require_mapping(state, "状态")
    stages = _require_mapping(root["阶段"], "阶段")
    current_stage = str(root["当前阶段"])
    current = _require_mapping(stages[current_stage], f"阶段.{current_stage}")
    version = int(current["版本"])
    candidate_sections = _parse_stage_candidate(stage_candidate, state)
    if current_stage == "内容终稿":
        _validate_final_candidate_titles(candidate_sections.get("内容终稿", ""))

    section_names = tuple(name for names in RECORD_SECTIONS.values() for name in names)
    section_pattern = re.compile(
        r"^## (" + "|".join(re.escape(name) for name in section_names) + r")[ \t]*$",
        flags=re.MULTILINE,
    )
    headings = list(section_pattern.finditer(markdown))
    counts = Counter(heading.group(1) for heading in headings)
    missing = [name for name in candidate_sections if counts[name] == 0]
    duplicates = [name for name in candidate_sections if counts[name] > 1]
    if missing:
        raise StateWriteError("正式内容生产记录缺少当前阶段区块: " + "、".join(missing))
    if duplicates:
        raise StateWriteError("正式内容生产记录包含重复阶段区块: " + "、".join(duplicates))

    parts: list[str] = []
    cursor = 0
    for index, heading in enumerate(headings):
        section_end = headings[index + 1].start() if index + 1 < len(headings) else len(markdown)
        parts.append(markdown[cursor : heading.end()])
        section_name = heading.group(1)
        if section_name in candidate_sections:
            existing_body = markdown[heading.end() : section_end]
            version_headings = list(
                re.finditer(r"^### v([1-9][0-9]*)[ \t]*$", existing_body, flags=re.MULTILINE)
            )
            matching_versions = [
                item for item in version_headings if int(item.group(1)) == version
            ]
            if len(matching_versions) > 1:
                raise StateWriteError(
                    f"内容生产记录的## {section_name}包含重复版本标题: v{version}"
                )

            body = candidate_sections[section_name]
            version_entry = f"### v{version}"
            if body:
                version_entry += f"\n\n{body}"

            if current_stage == "内容终稿":
                # 终稿只保留当前一份：每次保存都整体替换，不保留历史版本。
                parts.append("\n\n" + version_entry + "\n\n")
            elif matching_versions:
                # 同一版本重提只覆盖该版本正文，历史版本保持不变。
                version_heading = matching_versions[0]
                next_version = next(
                    (item for item in version_headings if item.start() > version_heading.start()),
                    None,
                )
                version_end = next_version.start() if next_version is not None else len(existing_body)
                parts.append(
                    existing_body[: version_heading.start()]
                    + version_entry
                    + "\n\n"
                    + existing_body[version_end:]
                )
            else:
                # 新版本追加在既有版本之后，不能覆盖此前的审查或优化记录。
                parts.append(existing_body.rstrip() + "\n\n" + version_entry + "\n\n")
        else:
            parts.append(markdown[heading.end() : section_end])
        cursor = section_end
    parts.append(markdown[cursor:])
    return "".join(parts)


def _record_field_matches(markdown: str, label: str) -> list[str]:
    bullet_matches = re.findall(
        rf"^-\s*{re.escape(label)}\s*[：:]\s*(.*?)\s*$",
        markdown,
        flags=re.MULTILINE,
    )
    table_matches = re.findall(
        rf"^\|\s*{re.escape(label)}\s*\|\s*(.*?)\s*\|\s*$",
        markdown,
        flags=re.MULTILINE,
    )
    return bullet_matches + table_matches


def _validate_workspace_artifacts(
    state: object,
    workspace_root: str | Path,
    *,
    record_override: str | None = None,
) -> None:
    root = Path(workspace_root).resolve()
    state_root = _require_mapping(state, "状态")
    record_logical_path = _require_logical_path(
        state_root["内容生产记录路径"], "内容生产记录路径"
    )
    record_path = _resolve_workspace_path(root, record_logical_path, "内容生产记录")
    if record_override is None:
        if not record_path.is_file():
            raise StateWriteError(f"内容生产记录不存在或不是普通文件: {record_path}")
        try:
            record = record_path.read_text(encoding="utf-8")
        except (OSError, UnicodeError) as exc:
            raise StateWriteError(f"无法读取内容生产记录{record_path}: {exc}") from exc
    else:
        record = record_override

    stages = _require_mapping(state_root["阶段"], "阶段")
    for stage_name, section_names in RECORD_SECTIONS.items():
        stage = _require_mapping(stages[stage_name], f"阶段.{stage_name}")
        version = int(stage["版本"])
        if version == 0:
            continue
        for section_name in section_names:
            body = _extract_record_version(record, section_name, version)
            if not body:
                raise StateWriteError(
                    f"内容生产记录缺少非空版本区块: ## {section_name} / ### v{version}"
                )



def _read_csv_rows(
    workspace_root: Path,
    logical_path: str,
    label: str,
    required_columns: set[str],
) -> list[dict[str, str]]:
    path = _resolve_workspace_file(workspace_root, logical_path, label)
    try:
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle)
            fieldnames = reader.fieldnames
            if fieldnames is None:
                raise StateWriteError(f"{label}缺少表头")
            if any(not fieldname for fieldname in fieldnames):
                raise StateWriteError(f"{label}包含空表头")
            if len(fieldnames) != len(set(fieldnames)):
                raise StateWriteError(f"{label}包含重复表头")
            missing_columns = sorted(required_columns - set(fieldnames))
            if missing_columns:
                raise StateWriteError(
                    f"{label}缺少完成态核对字段: {', '.join(missing_columns)}"
                )
            return [dict(row) for row in reader]
    except StateWriteError:
        raise
    except (OSError, UnicodeError, csv.Error) as exc:
        raise StateWriteError(f"无法读取{label}{path}: {exc}") from exc


def _prepare_content_library_reopen(
    workspace_root: Path,
    previous_state: object,
    next_state: object,
    requested_status: str,
) -> tuple[Path, bytes, bytes]:
    if requested_status != "生产中":
        raise StateWriteError("重新打开终稿时内容库状态只能恢复为生产中")

    previous_root = _require_mapping(previous_state, "旧状态")
    next_root = _require_mapping(next_state, "新状态")
    previous_stages = _require_mapping(previous_root["阶段"], "旧状态.阶段")
    next_stages = _require_mapping(next_root["阶段"], "新状态.阶段")
    previous_final = _require_mapping(previous_stages["内容终稿"], "旧状态.阶段.内容终稿")
    next_final = _require_mapping(next_stages["内容终稿"], "新状态.阶段.内容终稿")
    if (
        previous_final["状态"] != "已确认"
        or next_final["状态"] == "已确认"
        or not bool(previous_root["当前是否完成"])
        or bool(next_root["当前是否完成"])
        or next_root["流程状态"] != "进行中"
    ):
        raise StateWriteError("内容库状态只能随已确认内容重新进入生产的动作恢复")

    content_path = _resolve_workspace_file(
        workspace_root,
        "我方资料/我方内容库.csv",
        "我方内容库",
    )
    try:
        source = content_path.read_bytes()
        with io.StringIO(source.decode("utf-8-sig"), newline="") as handle:
            reader = csv.DictReader(handle)
            fieldnames = reader.fieldnames
            if fieldnames is None or any(not fieldname for fieldname in fieldnames):
                raise StateWriteError("我方内容库缺少有效表头")
            if len(fieldnames) != len(set(fieldnames)):
                raise StateWriteError("我方内容库包含重复表头")
            required = {"内容ID", "选题ID", "内容目录路径", "内容生产记录路径", "当前状态"}
            missing = sorted(required - set(fieldnames))
            if missing:
                raise StateWriteError("我方内容库缺少重新打开终稿所需字段: " + ", ".join(missing))
            rows = [dict(row) for row in reader]
    except StateWriteError:
        raise
    except (OSError, UnicodeError, csv.Error) as exc:
        raise StateWriteError(f"无法读取我方内容库{content_path}: {exc}") from exc

    expected_fields = {
        "内容ID": str(previous_root["内容ID"]),
        "选题ID": str(previous_root["选题ID"]),
        "内容目录路径": str(previous_root["当前内容目录"]),
        "内容生产记录路径": str(previous_root["内容生产记录路径"]),
    }
    matches = [
        row
        for row in rows
        if all(row.get(key) == value for key, value in expected_fields.items())
    ]
    if len(matches) != 1:
        raise StateWriteError("重新打开终稿前必须唯一定位我方内容库正式记录")
    current_status = matches[0]["当前状态"]
    if current_status in {"已发布", "已复盘"}:
        raise StateWriteError(f"内容当前状态为{current_status}，不能重新打开终稿")
    matches[0]["当前状态"] = requested_status

    output = io.StringIO(newline="")
    writer = csv.DictWriter(output, fieldnames=fieldnames, lineterminator="\n")
    writer.writeheader()
    writer.writerows({field: row.get(field, "") for field in fieldnames} for row in rows)
    return content_path, source, output.getvalue().encode("utf-8-sig")


def _display_character_count(value: str) -> int:
    text = unicodedata.normalize("NFC", value.strip())
    count = 0
    regional_indicator_run = 0
    joins_previous = False
    for character in text:
        codepoint = ord(character)
        if codepoint == 0x200D:
            joins_previous = True
            regional_indicator_run = 0
            continue
        if (
            unicodedata.combining(character)
            or unicodedata.category(character) in {"Mn", "Mc", "Me"}
            or 0x1F3FB <= codepoint <= 0x1F3FF
            or 0xE0020 <= codepoint <= 0xE007F
        ):
            continue
        if 0x1F1E6 <= codepoint <= 0x1F1FF:
            if joins_previous:
                joins_previous = False
            elif regional_indicator_run % 2 == 0:
                count += 1
            regional_indicator_run += 1
            continue
        regional_indicator_run = 0
        if joins_previous:
            joins_previous = False
            continue
        count += 1
    return count


def _validate_title_length(value: str, label: str) -> None:
    length = _display_character_count(value)
    if length > MAX_TITLE_DISPLAY_CHARACTERS:
        raise StateWriteError(
            f"{label}最多允许{MAX_TITLE_DISPLAY_CHARACTERS}个展示字符，当前为{length}个"
        )


def _extract_required_final_field(final_body: str, field_name: str) -> str:
    matches = re.findall(
        rf"^{re.escape(field_name)}：(.*)$",
        final_body,
        flags=re.MULTILINE,
    )
    if len(matches) != 1 or not matches[0].strip():
        raise StateWriteError(f"当前终稿必须且只能包含一个非空{field_name}")
    return matches[0].strip()


def _validate_final_candidate_titles(final_body: str) -> None:
    """在终稿候选落正式记录前，前置校验最终标题字段。"""
    final_content_title = _extract_required_final_field(final_body, "最终内容标题")
    _validate_title_length(final_content_title, "候选终稿.最终内容标题")


def _validate_completion_references(
    state: object,
    workspace_root: str | Path,
    *,
    require_ready_status: bool,
    record_override: str | None = None,
) -> None:
    state_root = _require_mapping(state, "状态")
    if not bool(state_root["当前是否完成"]):
        return

    root = Path(workspace_root).resolve()
    if record_override is None:
        record_path = _resolve_workspace_file(
            root,
            _require_logical_path(state_root["内容生产记录路径"], "内容生产记录路径"),
            "内容生产记录",
        )
        try:
            record = record_path.read_text(encoding="utf-8")
        except (OSError, UnicodeError) as exc:
            raise StateWriteError(f"无法读取内容生产记录{record_path}: {exc}") from exc
    else:
        record = record_override
    final_stage = _require_mapping(
        _require_mapping(state_root["阶段"], "阶段")["内容终稿"],
        "阶段.内容终稿",
    )
    final_body = _extract_record_version(record, "内容终稿", int(final_stage["版本"]))
    if not final_body:
        raise StateWriteError("流程完成时缺少当前版本终稿")
    final_content_title = _extract_required_final_field(final_body, "最终内容标题")
    _validate_title_length(final_content_title, "当前终稿.最终内容标题")

    content_rows = _read_csv_rows(
        root,
        "我方资料/我方内容库.csv",
        "我方内容库",
        {
            "内容ID",
            "选题ID",
            "内容目录路径",
            "标题",
            "当前状态",
            "内容生产记录路径",
        },
    )
    expected_content_fields = {
        "内容ID": str(state_root["内容ID"]),
        "选题ID": str(state_root["选题ID"]),
        "内容目录路径": str(state_root["当前内容目录"]),
        "内容生产记录路径": str(state_root["内容生产记录路径"]),
    }
    matching_content_rows = [
        row
        for row in content_rows
        if all(row.get(key) == value for key, value in expected_content_fields.items())
    ]
    if len(matching_content_rows) != 1:
        raise StateWriteError(
            "流程完成时我方内容库必须存在且只能存在一条与状态稳定标识和路径完全一致的记录"
        )
    content_row = matching_content_rows[0]
    _validate_title_length(content_row["标题"], "我方内容库.标题")
    if content_row["标题"] != final_content_title:
        raise StateWriteError("我方内容库.标题必须与当前终稿一致")
    content_status = content_row["当前状态"]
    if require_ready_status and content_status != "已完成":
        raise StateWriteError("首次进入完成态时我方内容库当前记录的当前状态必须是已完成")
    if not require_ready_status and content_status not in {"已完成", "待发布", "已发布", "已复盘"}:
        raise StateWriteError("已完成流程复核时我方内容库当前记录不得倒退到生产前状态")

    if str(state_root["选题ID"]).strip():
        topic_rows = _read_csv_rows(
            root,
            "我方资料/选题库.csv",
            "选题库",
            {"选题ID", "当前状态", "内容生产记录路径"},
        )
        matching_topic_rows = [
            row for row in topic_rows if row.get("选题ID") == state_root["选题ID"]
        ]
        if len(matching_topic_rows) != 1:
            raise StateWriteError("流程完成时选题库必须存在且只能存在一条当前选题ID记录")
        topic_row = matching_topic_rows[0]
        if topic_row["当前状态"] != "已生产":
            raise StateWriteError("流程完成时选题库当前记录的当前状态必须是已生产")
        if topic_row["内容生产记录路径"] != state_root["内容生产记录路径"]:
            raise StateWriteError("流程完成时选题库内容生产记录路径必须指向当前内容生产记录")


@contextmanager
def _exclusive_state_lock(target_path: Path):
    lock_path = target_path.parent / f".{target_path.name}.lock"
    with lock_path.open("a+b") as handle:
        if os.name == "nt":
            import msvcrt

            handle.seek(0, os.SEEK_END)
            if handle.tell() == 0:
                handle.write(b"\0")
                handle.flush()
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK, 1)
        else:
            import fcntl

            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            if os.name == "nt":
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def _read_state(path: Path) -> object:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise StateWriteError(f"无法读取已有状态文件{path}: {exc}") from exc


def _route_from_state(state: object) -> dict[str, object]:
    root = _require_mapping(state, "状态")
    stages = _require_mapping(root["阶段"], "阶段")
    current_stage = str(root["当前阶段"])
    return {
        "内容ID": root["内容ID"],
        "选题ID": root["选题ID"],
        "当前内容目录": root["当前内容目录"],
        "内容生产记录路径": root["内容生产记录路径"],
        "当前阶段": current_stage,
        "流程状态": root["流程状态"],
        "当前是否完成": root["当前是否完成"],
        "是否需要用户确认": root["是否需要用户确认"],
        "下一步推荐": root["下一步推荐"],
        "当前阶段状态": dict(_require_mapping(stages[current_stage], f"阶段.{current_stage}")),
        "允许的下一步": list(root["允许的下一步"]),
        "阻断原因": root["阻断原因"],
    }


def _replace_bytes_atomically(
    path: Path,
    payload: bytes,
    *,
    replace_operation: ReplaceOperation | None = None,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".stage", dir=str(path.parent)
    )
    temporary_path = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        (replace_operation or os.replace)(temporary_path, path)
    finally:
        temporary_path.unlink(missing_ok=True)


def _restore_snapshot(path: Path, payload: bytes | None) -> None:
    if payload is None:
        path.unlink(missing_ok=True)
        return
    _replace_bytes_atomically(path, payload)


def validate_state_file(
    target: str | Path,
    *,
    workspace_root: str | Path = Path("."),
) -> object:
    target_path = Path(target)
    if not target_path.is_file():
        raise StateWriteError(f"状态文件不存在或不是普通文件: {target_path}")
    try:
        with _exclusive_state_lock(target_path):
            state = _read_state(target_path)
            validate_state(state)
            _ensure_target_matches_state(target_path, state, workspace_root)
            _validate_workspace_artifacts(state, workspace_root)
            _validate_completion_references(
                state,
                workspace_root,
                require_ready_status=False,
            )
            return state
    except (StateValidationError, StateTransitionError, StateWriteError):
        raise
    except Exception as exc:
        raise StateWriteError(f"状态文件校验失败{target_path}: {exc}") from exc


def read_state_route(
    target: str | Path,
    *,
    workspace_root: str | Path = Path("."),
) -> dict[str, object]:
    """Validate in-memory state invariants and return only dispatch fields."""
    target_path = Path(target)
    if not target_path.is_file():
        raise StateWriteError(f"状态文件不存在或不是普通文件: {target_path}")
    try:
        with _exclusive_state_lock(target_path):
            state = _read_state(target_path)
            validate_state(state)
            _ensure_target_matches_state(target_path, state, workspace_root)
            return _route_from_state(state)
    except (StateValidationError, StateTransitionError, StateWriteError):
        raise
    except Exception as exc:
        raise StateWriteError(f"状态路由读取失败{target_path}: {exc}") from exc


def write_state_atomically(
    target: str | Path,
    state: object,
    *,
    allow_create: bool = False,
    recover_invalid_state: bool = False,
    expected_invalid_sha256: str = "",
    record_candidate_file: str | Path | None = None,
    record_text: str | None = None,
    stage_candidate_file: str | Path | None = None,
    stage_candidate_text: str | None = None,
    content_library_status: str | None = None,
    replace_operation: ReplaceOperation | None = None,
    workspace_root: str | Path = Path("."),
) -> dict[str, object]:
    target_path = Path(target)
    root_path = Path(workspace_root).resolve()
    validate_state(state)
    _ensure_target_matches_state(target_path, state, root_path)
    state_root = _require_mapping(state, "状态")
    record_logical_path = _require_logical_path(
        state_root["内容生产记录路径"], "内容生产记录路径"
    )
    record_path = _resolve_workspace_path(root_path, record_logical_path, "内容生产记录")
    if record_candidate_file is not None and record_text is not None:
        raise StateWriteError("完整记录候选与记录正文不能同时提交")
    if (record_candidate_file is not None or record_text is not None) and (
        stage_candidate_file is not None or stage_candidate_text is not None
    ):
        raise StateWriteError("完整记录候选与阶段记录候选不能同时提交")
    if stage_candidate_file is not None and stage_candidate_text is not None:
        raise StateWriteError("阶段记录只能通过文件或文本提交一次")
    if not record_path.is_file() and record_candidate_file is None and record_text is None:
        raise StateWriteError(f"内容生产记录不存在或不是普通文件: {record_path}")

    candidate_record_bytes: bytes | None = None
    candidate_record_text: str | None = None
    candidate_stage_text: str | None = None
    content_library_path: Path | None = None
    content_library_snapshot: bytes | None = None
    content_library_bytes: bytes | None = None
    if record_candidate_file is not None:
        candidate_path = Path(record_candidate_file).expanduser().resolve()
        if candidate_path == record_path:
            raise StateWriteError("记录候选文件不得直接指向正式内容生产记录")
        try:
            candidate_record_bytes = candidate_path.read_bytes()
            candidate_record_text = candidate_record_bytes.decode("utf-8")
        except (OSError, UnicodeError) as exc:
            raise StateWriteError(f"无法读取内容生产记录候选{candidate_path}: {exc}") from exc
        if not candidate_record_text.strip():
            raise StateWriteError("内容生产记录候选不得为空")
        candidate_record_text = _materialize_record_candidate_version(candidate_record_text, state)
        candidate_record_bytes = candidate_record_text.encode("utf-8")
    elif record_text is not None:
        if not record_text.strip():
            raise StateWriteError("内容生产记录正文不得为空")
        candidate_record_text = record_text
        candidate_record_bytes = candidate_record_text.encode("utf-8")
    if stage_candidate_file is not None:
        stage_candidate_path = Path(stage_candidate_file).expanduser().resolve()
        if stage_candidate_path == record_path:
            raise StateWriteError("阶段记录候选文件不得直接指向正式内容生产记录")
        try:
            candidate_stage_text = stage_candidate_path.read_text(encoding="utf-8")
        except (OSError, UnicodeError) as exc:
            raise StateWriteError(f"无法读取阶段记录候选{stage_candidate_path}: {exc}") from exc
        if not candidate_stage_text.strip():
            raise StateWriteError("阶段记录候选不得为空")
    elif stage_candidate_text is not None:
        candidate_stage_text = stage_candidate_text
        if not candidate_stage_text.strip():
            raise StateWriteError("阶段记录候选不得为空")

    try:
        target_path.parent.mkdir(parents=True, exist_ok=True)
        with _exclusive_state_lock(target_path):
            state_snapshot = target_path.read_bytes() if target_path.exists() else None
            record_snapshot = record_path.read_bytes() if record_path.is_file() else None
            if candidate_stage_text is not None:
                if record_snapshot is None:
                    raise StateWriteError("阶段记录候选必须合并到已有正式内容生产记录")
                try:
                    formal_record_text = record_snapshot.decode("utf-8")
                except UnicodeError as exc:
                    raise StateWriteError("正式内容生产记录不是有效UTF-8文本") from exc
                candidate_record_text = _merge_stage_candidate(
                    formal_record_text,
                    candidate_stage_text,
                    state,
                )
                candidate_record_bytes = candidate_record_text.encode("utf-8")
            mutation_started = False
            require_ready_status = True
            previous_state: object | None = None
            invalid_state_backup: tuple[Path, bytes] | None = None
            if target_path.exists():
                previous_bytes = target_path.read_bytes()
                try:
                    previous_state = json.loads(previous_bytes.decode("utf-8"))
                    validate_state(previous_state)
                except (UnicodeError, json.JSONDecodeError, StateValidationError) as exc:
                    if not recover_invalid_state:
                        if isinstance(exc, StateValidationError):
                            raise
                        raise StateWriteError(f"无法读取已有状态文件{target_path}: {exc}") from exc
                    if not allow_create:
                        raise StateWriteError("损坏状态恢复必须同时显式传入allow_create")
                    if not re.fullmatch(r"[a-f0-9]{64}", expected_invalid_sha256):
                        raise StateWriteError("损坏状态恢复必须提供64位小写expected_invalid_sha256")
                    actual_invalid_sha256 = hashlib.sha256(previous_bytes).hexdigest()
                    if actual_invalid_sha256 != expected_invalid_sha256:
                        raise StateWriteError("损坏状态SHA-256已经变化，拒绝覆盖")
                    backup_path = target_path.parent / (
                        f".{target_path.name}.invalid.{actual_invalid_sha256[:12]}.bak"
                    )
                    if backup_path.exists() and backup_path.read_bytes() != previous_bytes:
                        raise StateWriteError(f"损坏状态备份路径冲突: {backup_path}")
                    invalid_state_backup = (backup_path, previous_bytes)
                else:
                    if recover_invalid_state:
                        raise StateWriteError("正式状态仍然合法，不得使用损坏状态恢复模式")
                    validate_transition(previous_state, state)
                    previous_root = _require_mapping(previous_state, "旧状态")
                    require_ready_status = not bool(previous_root["当前是否完成"])
            elif not allow_create:
                raise StateWriteError(f"状态文件不存在，且未允许新建: {target_path}")
            elif recover_invalid_state:
                raise StateWriteError("状态文件不存在时不得使用损坏状态恢复模式")
            _validate_workspace_artifacts(
                state,
                root_path,
                record_override=candidate_record_text,
            )
            _validate_completion_references(
                state,
                root_path,
                require_ready_status=require_ready_status,
                record_override=candidate_record_text,
            )
            if content_library_status is not None:
                if previous_state is None:
                    raise StateWriteError("新建内容时不能更新内容库生命周期状态")
                (
                    content_library_path,
                    content_library_snapshot,
                    content_library_bytes,
                ) = _prepare_content_library_reopen(
                    root_path,
                    previous_state,
                    state,
                    content_library_status,
                )
            if (
                previous_state == state
                and candidate_record_bytes is not None
                and record_snapshot is not None
                and candidate_record_bytes != record_snapshot
            ):
                raise StateTransitionError("同状态重试不得改写内容生产记录，正文变化必须形成新版本")

            if invalid_state_backup is not None:
                backup_path, backup_bytes = invalid_state_backup
                if not backup_path.exists():
                    _replace_bytes_atomically(backup_path, backup_bytes)

            payload = (json.dumps(state, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
            try:
                mutation_started = True
                if candidate_record_bytes is not None:
                    _replace_bytes_atomically(record_path, candidate_record_bytes)
                if content_library_path is not None and content_library_bytes is not None:
                    if content_library_path.read_bytes() != content_library_snapshot:
                        raise StateWriteError("我方内容库已被并发修改，拒绝重新打开内容")
                    _replace_bytes_atomically(content_library_path, content_library_bytes)
                _replace_bytes_atomically(
                    target_path,
                    payload,
                    replace_operation=replace_operation,
                )
                if target_path.read_bytes() != payload:
                    raise StateWriteError("正式状态写入后回读不一致")
                if candidate_record_bytes is not None and record_path.read_bytes() != candidate_record_bytes:
                    raise StateWriteError("正式内容生产记录写入后回读不一致")
                if content_library_path is not None and content_library_bytes is not None:
                    if content_library_path.read_bytes() != content_library_bytes:
                        raise StateWriteError("我方内容库写入后回读不一致")
            except Exception as exc:
                if mutation_started:
                    rollback_errors: list[str] = []
                    rollback_items: list[tuple[Path, bytes | None]] = [
                        (record_path, record_snapshot),
                        (target_path, state_snapshot),
                    ]
                    if content_library_path is not None:
                        rollback_items.insert(0, (content_library_path, content_library_snapshot))
                    for rollback_path, snapshot in rollback_items:
                        try:
                            _restore_snapshot(rollback_path, snapshot)
                        except Exception as rollback_exc:  # pragma: no cover - catastrophic I/O
                            rollback_errors.append(f"{rollback_path}: {rollback_exc}")
                    if rollback_errors:
                        raise StateWriteError(
                            "内容生产联合提交失败且回滚不完整: " + "；".join(rollback_errors)
                        ) from exc
                raise

            return {
                "state_file": str(target_path),
                "record_path": str(record_path),
                "state_sha256": hashlib.sha256(payload).hexdigest(),
                "record_sha256": hashlib.sha256(record_path.read_bytes()).hexdigest(),
                **(
                    {
                        "content_library_path": str(content_library_path),
                        "content_library_sha256": hashlib.sha256(content_library_path.read_bytes()).hexdigest(),
                    }
                    if content_library_path is not None
                    else {}
                ),
                "route": _route_from_state(state),
            }
    except (StateValidationError, StateTransitionError, StateWriteError):
        raise
    except Exception as exc:
        raise StateWriteError(f"状态文件原子写入失败{target_path}: {exc}") from exc


def _load_candidate(path: Path) -> object:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise StateWriteError(f"无法读取候选状态{path}: {exc}") from exc


def _empty_machine_data() -> dict[str, object]:
    return {
        "内容任务": "",
        "待确认问题": "",
    }


def _initial_state(action_data: Mapping[str, object]) -> dict[str, object]:
    required_keys = {"内容ID", "选题ID", "当前内容目录"}
    actual_keys = set(action_data)
    if actual_keys != required_keys:
        raise StateValidationError("initialize动作数据字段不符合合同")
    content_id = _require_string(action_data["内容ID"], "initialize.内容ID")
    topic_id = _require_string(action_data["选题ID"], "initialize.选题ID", allow_empty=True)
    content_dir = _require_logical_path(action_data["当前内容目录"], "initialize.当前内容目录")
    state: dict[str, object] = {
        "状态协议版本": PROTOCOL_VERSION,
        "工作流": WORKFLOW_NAME,
        "内容ID": content_id,
        "选题ID": topic_id,
        "当前内容目录": content_dir,
        "内容生产记录路径": f"{content_dir.rstrip('/')}/内容生产记录.md",
        "当前阶段": "内容定位",
        "流程状态": "进行中",
        "当前是否完成": False,
        "是否需要用户确认": False,
        "下一步推荐": "内容定位",
        "允许的下一步": ["内容定位"],
        "阶段": {
            "建档与恢复": {"版本": 1, "状态": "已确认", "依赖版本": {}},
            "内容定位": {"版本": 0, "状态": "未开始", "依赖版本": {}},
            "内容初稿": {"版本": 0, "状态": "未开始", "依赖版本": {"内容定位": 0}},
            "内容终稿": {"版本": 0, "状态": "未开始", "依赖版本": {"内容初稿": 0}},
        },
        "机器数据": _empty_machine_data(),
        "失效记录": {"是否存在": False, "起始阶段": "", "失效阶段": [], "原因": ""},
        "阻断原因": "",
    }
    validate_state(state)
    return state


def _next_stage(stage_name: str) -> str | None:
    stage_index = STAGES.index(stage_name)
    if stage_index + 1 >= len(STAGES):
        return None
    return STAGES[stage_index + 1]


def _clear_invalidation_if_empty(state: dict[str, object]) -> None:
    invalidation = state["失效记录"]
    if not isinstance(invalidation, dict):
        raise StateValidationError("失效记录必须是对象")
    if not invalidation["失效阶段"]:
        state["失效记录"] = {
            "是否存在": False,
            "起始阶段": "",
            "失效阶段": [],
            "原因": "",
        }


def _invalidate_downstream(
    state: dict[str, object],
    stage_name: str,
    reason: str,
) -> None:
    stages = state["阶段"]
    if not isinstance(stages, dict):
        raise StateValidationError("阶段必须是对象")
    stage_index = STAGES.index(stage_name)
    invalidated: list[str] = []
    for downstream in STAGES[stage_index + 1 :]:
        stage = stages[downstream]
        if not isinstance(stage, dict):
            raise StateValidationError(f"阶段.{downstream}必须是对象")
        if stage["版本"] > 0 or stage["状态"] not in {"未开始", "已失效"}:
            stage["状态"] = "已失效"
        if stage["状态"] == "已失效":
            invalidated.append(downstream)
    if invalidated:
        state["失效记录"] = {
            "是否存在": True,
            "起始阶段": stage_name,
            "失效阶段": invalidated,
            "原因": reason,
        }


def _apply_machine_data_update(
    state: dict[str, object],
    update: object,
    *,
    current_stage: str,
) -> None:
    update_map = _require_mapping(update, "save-stage-candidate.机器数据更新")
    allowed = MACHINE_DATA_KEYS - {"待确认问题"}
    if not set(update_map).issubset(allowed):
        unknown = sorted(set(update_map) - allowed)
        raise StateValidationError(f"机器数据更新包含未知字段: {', '.join(unknown)}")
    if "内容任务" in update_map and current_stage != "内容定位":
        raise StateValidationError("内容任务只能由内容定位候选更新")
    machine_data = state["机器数据"]
    if not isinstance(machine_data, dict):
        raise StateValidationError("机器数据必须是对象")
    for key, value in update_map.items():
        machine_data[key] = copy.deepcopy(value)


def build_state_for_action(
    previous: object | None,
    action: str,
    action_data: object | None = None,
) -> dict[str, object]:
    """Derive the full state from a small, explicit workflow action."""
    if action not in ACTIONS:
        raise StateValidationError(f"不支持的动作: {action}")
    data = _require_mapping(action_data or {}, f"{action}动作数据")
    if action == "initialize":
        if previous is not None:
            raise StateTransitionError("已有正式状态时不得再次initialize")
        return _initial_state(data)
    if previous is None:
        raise StateTransitionError(f"状态不存在，不能执行{action}")

    validate_state(previous)
    state = copy.deepcopy(previous)
    if not isinstance(state, dict):
        raise StateValidationError("状态必须是对象")
    stages = state["阶段"]
    machine_data = state["机器数据"]
    if not isinstance(stages, dict) or not isinstance(machine_data, dict):
        raise StateValidationError("状态阶段或机器数据不是对象")
    current_stage = str(state["当前阶段"])
    current = stages[current_stage]
    if not isinstance(current, dict):
        raise StateValidationError(f"阶段.{current_stage}必须是对象")

    if action in {"save-stage-candidate", "replace-stage-candidate"}:
        allowed_keys = {"待确认问题", "机器数据更新"}
        if action == "replace-stage-candidate":
            allowed_keys.add("预期阶段版本")
        if not set(data).issubset(allowed_keys) or "待确认问题" not in data:
            raise StateValidationError("save-stage-candidate只接受待确认问题和可选机器数据更新")
        if action == "replace-stage-candidate":
            if "预期阶段版本" not in data:
                raise StateValidationError("replace-stage-candidate必须提供预期阶段版本")
            expected_version = _require_version(
                data["预期阶段版本"],
                "replace-stage-candidate.预期阶段版本",
            )
            if current["状态"] != "等待用户确认":
                raise StateTransitionError("只有等待用户确认的当前阶段可以直接修改")
            if int(current["版本"]) != expected_version:
                raise StateTransitionError("当前阶段版本已变化，请基于最新版本重新修改")
        question = _require_string(data["待确认问题"], "save-stage-candidate.待确认问题")
        if current_stage == "建档与恢复" or state["当前是否完成"]:
            raise StateTransitionError("当前状态不能保存阶段候选")
        if current["状态"] == "被阻断":
            raise StateTransitionError("请先unblock，再保存阶段候选")
        was_confirmed = current["状态"] == "已确认"
        current["版本"] = int(current["版本"]) + 1
        current["状态"] = "等待用户确认"
        state["流程状态"] = "等待用户确认"
        state["当前是否完成"] = False
        state["是否需要用户确认"] = True
        state["下一步推荐"] = current_stage
        state["允许的下一步"] = [current_stage]
        state["阻断原因"] = ""
        machine_data["待确认问题"] = question
        if "机器数据更新" in data:
            _apply_machine_data_update(
                state,
                data["机器数据更新"],
                current_stage=current_stage,
            )
        if current_stage == "内容定位" and not machine_data.get("内容任务", ""):
            raise StateValidationError("内容定位候选必须提供内容任务")
        if was_confirmed:
            _invalidate_downstream(state, current_stage, "用户确认重做当前阶段")

    elif action == "revise-current":
        _require_exact_keys(data, set(), "revise-current动作数据")
        if current["状态"] != "等待用户确认":
            raise StateTransitionError("只有正在等待用户确认的当前阶段可以进入修改")
        current["状态"] = "进行中"
        state["流程状态"] = "进行中"
        state["当前是否完成"] = False
        state["是否需要用户确认"] = False
        state["下一步推荐"] = current_stage
        state["允许的下一步"] = [current_stage]
        state["阻断原因"] = ""
        machine_data["待确认问题"] = ""

    elif action == "reopen-final":
        _require_exact_keys(data, {"原因"}, "reopen-final动作数据")
        _require_string(data["原因"], "reopen-final.原因")
        if current_stage != "内容终稿" or current["状态"] != "已确认":
            raise StateTransitionError("只有已确认的内容终稿才能重新打开")
        current["状态"] = "进行中"
        state["流程状态"] = "进行中"
        state["当前是否完成"] = False
        state["是否需要用户确认"] = False
        state["下一步推荐"] = current_stage
        state["允许的下一步"] = [current_stage]
        state["阻断原因"] = ""
        machine_data["待确认问题"] = ""

    elif action == "confirm-current":
        _require_exact_keys(data, set(), "confirm-current动作数据")
        if current["状态"] != "等待用户确认":
            raise StateTransitionError("只有正在等待用户确认的当前阶段可以确认")
        current["状态"] = "已确认"
        state["流程状态"] = "进行中"
        state["是否需要用户确认"] = False
        state["阻断原因"] = ""
        machine_data["待确认问题"] = ""
        downstream = _next_stage(current_stage)
        if downstream is not None:
            next_value = stages[downstream]
            if not isinstance(next_value, dict):
                raise StateValidationError(f"阶段.{downstream}必须是对象")
            was_invalid = next_value["状态"] == "已失效"
            next_value["状态"] = "未开始"
            dependency = DIRECT_DEPENDENCY[downstream]
            if dependency is not None:
                next_value["依赖版本"][dependency] = current["版本"]
            state["当前阶段"] = downstream
            state["下一步推荐"] = downstream
            state["允许的下一步"] = [downstream]
            if was_invalid:
                invalidation = state["失效记录"]
                invalidation["失效阶段"].remove(downstream)
                _clear_invalidation_if_empty(state)
        else:
            state["下一步推荐"] = current_stage
            state["允许的下一步"] = [current_stage]

    elif action == "rework-stage":
        _require_exact_keys(data, {"目标阶段", "原因"}, "rework-stage动作数据")
        target_stage = _require_string(data["目标阶段"], "rework-stage.目标阶段")
        reason = _require_string(data["原因"], "rework-stage.原因")
        if target_stage not in STAGES[1:]:
            raise StateTransitionError("重做目标必须是内容定位、内容初稿或内容终稿中的一个阶段")
        target = stages[target_stage]
        if not isinstance(target, dict) or target["状态"] != "已确认":
            raise StateTransitionError("只能重做已经确认的阶段")
        state["当前阶段"] = target_stage
        target["状态"] = "进行中"
        state["流程状态"] = "进行中"
        state["当前是否完成"] = False
        state["是否需要用户确认"] = False
        state["下一步推荐"] = target_stage
        state["允许的下一步"] = [target_stage]
        state["阻断原因"] = ""
        machine_data["待确认问题"] = ""
        _invalidate_downstream(state, target_stage, reason)

    elif action == "block":
        _require_exact_keys(data, {"原因"}, "block动作数据")
        # label 会原样出现在错误信息里（"<label>不得为空"），所以用中文，
        # 不要用动作名+点号（原来会报出"block.原因不得为空"）。
        reason = _require_string(data["原因"], "阻断原因")
        if state["当前是否完成"]:
            raise StateTransitionError("已完成流程不能标记为阻断")
        if state["流程状态"] == "等待用户确认":
            raise StateTransitionError("等待用户确认时不得改写为阻断")
        if current["状态"] != "已确认":
            current["状态"] = "被阻断"
        state["流程状态"] = "被阻断"
        state["是否需要用户确认"] = False
        state["阻断原因"] = reason
        machine_data["待确认问题"] = ""

    elif action == "unblock":
        _require_exact_keys(data, set(), "unblock动作数据")
        if state["流程状态"] != "被阻断":
            raise StateTransitionError("当前流程没有处于阻断状态")
        if current["状态"] == "被阻断":
            current["状态"] = "进行中"
        state["流程状态"] = "进行中"
        state["阻断原因"] = ""

    elif action == "complete":
        _require_exact_keys(data, set(), "complete动作数据")
        state["流程状态"] = "已完成"
        state["当前是否完成"] = True
        state["是否需要用户确认"] = False
        state["下一步推荐"] = ""
        state["允许的下一步"] = []
        state["阻断原因"] = ""
        machine_data["待确认问题"] = ""

    validate_transition(previous, state)
    return state


def _load_action_data(path: Path | None) -> object:
    if path is None:
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise StateWriteError(f"无法读取动作数据{path}: {exc}") from exc


def _validate_run_candidates(
    workspace_root: Path,
    *candidate_paths: Path | None,
) -> None:
    run_dir: Path | None = None
    temporary_root = (workspace_root.resolve() / ".meiling" / "staging").resolve()
    for candidate_path in candidate_paths:
        if candidate_path is None:
            continue
        resolved = candidate_path.expanduser().resolve()
        try:
            relative = resolved.relative_to(temporary_root)
        except ValueError as exc:
            raise StateWriteError(f"候选文件必须位于{temporary_root}/<run_id>/") from exc
        if len(relative.parts) != 3:
            raise StateWriteError(
                "候选文件必须直接位于本轮.meiling/staging/<当前流程>/<run_id>/目录"
            )
        current_run_dir = temporary_root / relative.parts[0] / relative.parts[1]
        if run_dir is None:
            run_dir = current_run_dir
        elif current_run_dir != run_dir:
            raise StateWriteError("动作数据和记录候选必须来自同一个run_id")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="校验并原子写入内容生产阶段状态")
    parser.add_argument("--state-file", required=True, type=Path, help="正式当前阶段状态.json路径")
    parser.add_argument("--action", choices=sorted(ACTIONS), help="由执行器生成完整状态的动作")
    parser.add_argument("--action-data-file", type=Path, help="动作所需的最小JSON数据")
    parser.add_argument(
        "--record-candidate-file",
        type=Path,
        help="仅供initialize使用的完整内容生产记录候选",
    )
    parser.add_argument(
        "--stage-candidate-file",
        type=Path,
        help="save-stage-candidate使用的当前阶段记录候选",
    )
    parser.add_argument("--allow-create", action="store_true", help="允许首次创建状态文件")
    parser.add_argument(
        "--recover-invalid-state",
        action="store_true",
        help="显式恢复已经存在但不符合合同的状态文件，必须同时提供旧文件SHA-256",
    )
    parser.add_argument(
        "--expected-invalid-sha256",
        default="",
        help="准备恢复的损坏状态文件当前SHA-256",
    )
    parser.add_argument("--validate-only", action="store_true", help="只校验正式状态，不执行写入")
    parser.add_argument(
        "--route-only",
        action="store_true",
        help="只校验状态内存合同并返回调度字段，不读取业务产物",
    )
    parser.add_argument(
        "--workspace-root",
        type=Path,
        default=Path("."),
        help="调用者当前工作区根目录，默认使用当前工作目录",
    )
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    started_at = time.perf_counter()
    read_only_modes = int(args.validate_only) + int(args.route_only)
    if read_only_modes > 1:
        parser.error("--validate-only不能与--route-only同时使用")
    if read_only_modes and args.action is not None:
        parser.error("只读模式不能与--action同时使用")
    if read_only_modes and args.action_data_file is not None:
        parser.error("只读模式不能与--action-data-file同时使用")
    if read_only_modes and args.record_candidate_file is not None:
        parser.error("只读模式不能与--record-candidate-file同时使用")
    if read_only_modes and args.stage_candidate_file is not None:
        parser.error("只读模式不能与--stage-candidate-file同时使用")
    if read_only_modes and args.allow_create:
        parser.error("只读模式不能与--allow-create同时使用")
    if read_only_modes and (args.recover_invalid_state or args.expected_invalid_sha256):
        parser.error("只读模式不能使用损坏状态恢复参数")
    if not read_only_modes and args.action is None:
        parser.error("执行写入时必须提供--action")
    if args.action_data_file is not None and args.action is None:
        parser.error("--action-data-file必须与--action同时使用")
    if args.recover_invalid_state and not args.allow_create:
        parser.error("--recover-invalid-state必须与--allow-create同时使用")
    if bool(args.expected_invalid_sha256) != bool(args.recover_invalid_state):
        parser.error("--recover-invalid-state与--expected-invalid-sha256必须同时提供")
    try:
        if args.route_only:
            route = read_state_route(
                args.state_file,
                workspace_root=args.workspace_root,
            )
            print(
                json.dumps(
                    {
                        "ok": True,
                        "route": route,
                        "elapsed_ms": round((time.perf_counter() - started_at) * 1000, 2),
                    },
                    ensure_ascii=False,
                )
            )
            return 0
        if args.validate_only:
            validate_state_file(
                args.state_file,
                workspace_root=args.workspace_root,
            )
            print(
                json.dumps(
                    {
                        "ok": True,
                        "state_file": str(args.state_file),
                        "elapsed_ms": round((time.perf_counter() - started_at) * 1000, 2),
                    },
                    ensure_ascii=False,
                )
            )
            return 0
        _validate_run_candidates(
            args.workspace_root,
            args.action_data_file,
            args.record_candidate_file,
            args.stage_candidate_file,
        )
        previous_state = None
        if args.state_file.is_file() and not args.recover_invalid_state:
            previous_state = _read_state(args.state_file)
        next_state = build_state_for_action(
            previous_state,
            args.action,
            _load_action_data(args.action_data_file),
        )
        if args.action == "initialize":
            if args.record_candidate_file is None or args.stage_candidate_file is not None:
                raise StateWriteError("initialize必须且只能提供--record-candidate-file")
        elif args.action == "save-stage-candidate":
            if args.stage_candidate_file is None or args.record_candidate_file is not None:
                raise StateWriteError("save-stage-candidate必须且只能提供--stage-candidate-file")
        elif args.record_candidate_file is not None or args.stage_candidate_file is not None:
            raise StateWriteError(f"{args.action}不得提供记录候选文件")
        result = write_state_atomically(
            args.state_file,
            next_state,
            allow_create=args.allow_create,
            recover_invalid_state=args.recover_invalid_state,
            expected_invalid_sha256=args.expected_invalid_sha256,
            record_candidate_file=args.record_candidate_file,
            stage_candidate_file=args.stage_candidate_file,
            workspace_root=args.workspace_root,
        )
    except (StateValidationError, StateTransitionError, StateWriteError) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False))
        return 1
    print(
        json.dumps(
            {
                "ok": True,
                **result,
                "elapsed_ms": round((time.perf_counter() - started_at) * 1000, 2),
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
