#!/usr/bin/env python3
from __future__ import annotations

import argparse
import importlib.util
import json
import shutil
import sys
import time
from pathlib import Path
from typing import Callable, Mapping


class ContentIntakeError(RuntimeError):
    """Raised when content intake cannot be committed as one transaction."""


FailureHook = Callable[[str], None]
ACTION_KEYS = {
    "选题ID",
    "选题名称",
    "媒介形式",
    "参考内容分析路径",
}
REQUIRED_ACTION_KEYS = set()
NON_EMPTY_ACTION_KEYS = set()
MEDIA_FORMS = {"视频", "图文", "文章", "其他"}
CONTENT_REQUIRED_FIELDS = {
    "内容ID",
    "选题ID",
    "选题名称",
    "标题",
    "内容目录路径",
    "媒介形式",
    "当前状态",
    "参考内容分析路径",
    "内容生产记录路径",
    "备注",
}
TOPIC_REQUIRED_FIELDS = {
    "选题ID",
    "选题名称",
    "当前状态",
    "是否建议做",
    "参考内容分析路径",
}


def _load_sibling(file_name: str, module_name: str):
    path = Path(__file__).with_name(file_name)
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        raise ContentIntakeError(f"无法加载执行单元: {path}")
    module = importlib.util.module_from_spec(spec)
    previous_path = list(sys.path)
    sys.path.insert(0, str(path.parent))
    try:
        spec.loader.exec_module(module)
    finally:
        sys.path[:] = previous_path
    return module


def _require_action_data(value: object) -> dict[str, str]:
    if not isinstance(value, Mapping):
        raise ContentIntakeError("建档动作数据必须是对象")
    keys = set(value)
    missing = sorted(REQUIRED_ACTION_KEYS - keys)
    unknown = sorted(keys - ACTION_KEYS)
    if missing or unknown:
        details: list[str] = []
        if missing:
            details.append("缺少字段: " + ", ".join(missing))
        if unknown:
            details.append("未知字段: " + ", ".join(unknown))
        raise ContentIntakeError("建档动作数据字段不符合合同（" + "；".join(details) + "）")

    normalized: dict[str, str] = {}
    for key in ACTION_KEYS:
        raw = value.get(key, "")
        if not isinstance(raw, str):
            raise ContentIntakeError(f"建档动作数据.{key}必须是字符串")
        normalized[key] = raw.strip()
    for key in NON_EMPTY_ACTION_KEYS:
        if not normalized[key]:
            raise ContentIntakeError(f"建档动作数据.{key}不得为空")
    if normalized["媒介形式"] and normalized["媒介形式"] not in MEDIA_FORMS:
        raise ContentIntakeError("建档动作数据.媒介形式只允许视频、图文、文章或其他")
    return normalized


def _read_table(csv_module, path: Path, required: set[str], label: str):
    try:
        fields, rows, snapshot = csv_module.read_csv_table_with_snapshot(path)
    except Exception as exc:
        raise ContentIntakeError(f"无法读取{label}{path}: {exc}") from exc
    missing = sorted(required - set(fields))
    if missing:
        raise ContentIntakeError(f"{label}缺少字段: {', '.join(missing)}")
    return fields, rows, snapshot


def _unique_row(
    rows: list[dict[str, str]],
    expected: Mapping[str, str],
    label: str,
) -> dict[str, str]:
    matches = [
        row for row in rows if all((row.get(key) or "").strip() == value for key, value in expected.items())
    ]
    if len(matches) != 1:
        raise ContentIntakeError(f"{label}必须且只能匹配一条记录，实际为{len(matches)}条")
    return matches[0]


def _logical_content_dir(content_id: str, topic_name: str, path_module) -> str:
    folder = f"{content_id}-{path_module.sanitize_title(topic_name)}"
    return (Path("我方资料") / "我方内容" / folder).as_posix()


def _state_logical_path(content_dir: str) -> str:
    return f"{content_dir.rstrip('/')}/过程记录/当前阶段状态.json"


def _validate_frozen_paths(row: Mapping[str, str]) -> tuple[str, str]:
    content_id = (row.get("内容ID") or "").strip()
    content_dir = (row.get("内容目录路径") or "").strip()
    record_path = (row.get("内容生产记录路径") or "").strip()
    if not content_id or not content_dir or not record_path:
        raise ContentIntakeError("已有生产中内容缺少冻结的内容ID、目录或生产记录路径")
    if record_path != f"{content_dir.rstrip('/')}/内容生产记录.md":
        raise ContentIntakeError("已有生产中内容的目录与生产记录路径互相冲突")
    return content_id, content_dir


def _restore_snapshot(state_executor, path: Path, payload: bytes | None) -> None:
    state_executor._restore_snapshot(path, payload)


def finalize_content_intake(
    workspace_root: str | Path,
    *,
    action_data: object,
    record_candidate_file: str | Path,
    failure_hook: FailureHook | None = None,
) -> dict[str, object]:
    root = Path(workspace_root).expanduser().resolve()
    if not root.is_dir():
        raise ContentIntakeError(f"工作区不存在: {root}")
    data = _require_action_data(action_data)
    candidate_path = Path(record_candidate_file).expanduser().resolve()

    state_executor = _load_sibling("内容生产状态执行器.py", "content_production_state_executor")
    csv_module = _load_sibling("csv安全写入.py", "content_production_csv_transaction")
    path_module = _load_sibling("过程记录路径.py", "content_production_record_paths")
    content_id_module = _load_sibling("全局内容ID.py", "global_content_id")
    try:
        state_executor._validate_run_candidates(root, candidate_path)
    except Exception as exc:
        raise ContentIntakeError(str(exc)) from exc

    content_path = root / "我方资料" / "我方内容库.csv"
    topic_path = root / "我方资料" / "选题库.csv"
    content_fields, content_rows, content_snapshot = _read_table(
        csv_module, content_path, CONTENT_REQUIRED_FIELDS, "我方内容库"
    )
    _, topic_rows, _ = _read_table(csv_module, topic_path, TOPIC_REQUIRED_FIELDS, "选题库")

    topic = None
    if data["选题ID"]:
        topic = _unique_row(topic_rows, {"选题ID": data["选题ID"]}, "当前选题")
        topic_name = (topic.get("选题名称") or "").strip()
        if not topic_name:
            raise ContentIntakeError("当前选题名称为空，无法建档")
        if (topic.get("当前状态") or "").strip() not in {"已完成分析", "已生产"}:
            raise ContentIntakeError("当前选题尚未完成正式分析，不能进入内容生产")
    else:
        topic_name = data["选题名称"]
    # 独立生产没有稳定选题身份，不能按临时标题猜测并恢复历史任务。
    identity = {"选题ID": data["选题ID"]} if data["选题ID"] else {"选题ID": "__independent_never_match__"}
    matches = [
        row
        for row in content_rows
        if all((row.get(key) or "").strip() == value for key, value in identity.items())
    ]
    if len(matches) > 1:
        raise ContentIntakeError("当前选题匹配到多条内容记录，拒绝猜测应恢复哪一条")

    reusing = bool(matches)
    if reusing:
        current_row = matches[0]
        if (current_row.get("当前状态") or "").strip() != "生产中":
            raise ContentIntakeError("同选题已存在非生产中内容，拒绝重复建档")
        content_id, content_dir = _validate_frozen_paths(current_row)
    else:
        try:
            content_id = content_id_module.next_content_id(
                root,
                additional_ids=[row.get("内容ID", "") for row in content_rows],
            )
        except Exception as exc:
            raise ContentIntakeError(f"无法分配全局内容ID: {exc}") from exc
        content_dir = _logical_content_dir(content_id, topic_name or "独立内容", path_module)
        current_row = {field: "" for field in content_fields}
        content_rows.append(current_row)

    record_logical_path = f"{content_dir.rstrip('/')}/内容生产记录.md"
    state_logical_path = _state_logical_path(content_dir)
    state_path = root / state_logical_path
    record_path = root / record_logical_path
    content_dir_path = root / content_dir

    if state_path.is_file():
        try:
            route = state_executor.read_state_route(state_path, workspace_root=root)
        except Exception as exc:
            raise ContentIntakeError(f"已有生产中内容的正式状态不可恢复: {exc}") from exc
        if (
            route["内容ID"] != content_id
            or route["选题ID"] != data["选题ID"]
            or route["当前内容目录"] != content_dir
            or route["内容生产记录路径"] != record_logical_path
        ):
            raise ContentIntakeError("已有状态与内容库冻结身份不一致")
        return {
            "ok": True,
            "reused": True,
            "content_id": content_id,
            "content_dir": content_dir,
            "record_path": record_logical_path,
            "state_file": state_logical_path,
            "route": route,
        }

    if record_path.is_file():
        try:
            existing_record = record_path.read_bytes()
            candidate_record = candidate_path.read_bytes()
        except OSError as exc:
            raise ContentIntakeError(f"无法核对已有生产记录: {exc}") from exc
        if existing_record != candidate_record:
            raise ContentIntakeError("已有正式生产记录但状态缺失，拒绝用本轮候选覆盖")

    current_row.update(
        {
            "内容ID": content_id,
            "选题ID": data["选题ID"],
            "选题名称": topic_name if data["选题ID"] else "",
            "标题": topic_name,
            "内容目录路径": content_dir,
            "媒介形式": data["媒介形式"],
            "当前状态": "生产中",
            "参考内容分析路径": (
                (topic.get("参考内容分析路径") or "").strip()
                if topic
                else data["参考内容分析路径"]
            ),
            "内容生产记录路径": record_logical_path,
            "备注": "",
        }
    )
    initial_state = state_executor.build_state_for_action(
        None,
        "initialize",
        {
            "内容ID": content_id,
            "选题ID": data["选题ID"],
            "当前内容目录": content_dir,
        },
    )

    record_snapshot = record_path.read_bytes() if record_path.is_file() else None
    state_snapshot = state_path.read_bytes() if state_path.is_file() else None
    content_dir_existed = content_dir_path.exists()
    try:
        csv_module.write_csv_tables_atomically(
            [(content_path, content_fields, content_rows)],
            expected_originals={content_path: content_snapshot},
        )
        if failure_hook:
            failure_hook("content_library_written")
        state_result = state_executor.write_state_atomically(
            state_path,
            initial_state,
            allow_create=True,
            record_candidate_file=candidate_path,
            workspace_root=root,
        )
        if failure_hook:
            failure_hook("state_initialized")

        _, committed_rows, _ = _read_table(
            csv_module, content_path, CONTENT_REQUIRED_FIELDS, "我方内容库"
        )
        committed = _unique_row(
            committed_rows,
            {"选题ID": data["选题ID"], "内容ID": content_id},
            "建档后的内容",
        )
        if (
            committed.get("选题ID") != data["选题ID"]
            or committed.get("内容目录路径") != content_dir
            or committed.get("内容生产记录路径") != record_logical_path
        ):
            raise ContentIntakeError("内容库写后回读与冻结身份或路径不一致")
    except Exception as exc:
        rollback_errors: list[str] = []
        for rollback_path, snapshot in (
            (state_path, state_snapshot),
            (record_path, record_snapshot),
            (content_path, content_snapshot),
        ):
            try:
                _restore_snapshot(state_executor, rollback_path, snapshot)
            except Exception as rollback_exc:  # pragma: no cover - catastrophic I/O
                rollback_errors.append(f"{rollback_path}: {rollback_exc}")
        if not content_dir_existed and content_dir_path.exists():
            try:
                shutil.rmtree(content_dir_path)
            except OSError as rollback_exc:  # pragma: no cover - catastrophic I/O
                rollback_errors.append(f"{content_dir_path}: {rollback_exc}")
        if rollback_errors:
            raise ContentIntakeError(
                "内容生产建档失败且回滚不完整: " + "；".join(rollback_errors)
            ) from exc
        raise ContentIntakeError(f"内容生产建档失败，已恢复到调用前: {exc}") from exc

    return {
        "ok": True,
        "reused": reusing,
        "content_id": content_id,
        "content_dir": content_dir,
        "record_path": record_logical_path,
        "state_file": state_logical_path,
        "route": state_result["route"],
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="一次提交内容库、生产记录和初始状态")
    parser.add_argument("--workspace-root", required=True, type=Path)
    parser.add_argument("--action-data-file", required=True, type=Path)
    parser.add_argument("--record-candidate-file", required=True, type=Path)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    started_at = time.perf_counter()
    try:
        action_data = json.loads(args.action_data_file.read_text(encoding="utf-8"))
        result = finalize_content_intake(
            args.workspace_root,
            action_data=action_data,
            record_candidate_file=args.record_candidate_file,
        )
    except (ContentIntakeError, OSError, UnicodeError, json.JSONDecodeError) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False))
        return 1
    print(
        json.dumps(
            {
                **result,
                "elapsed_ms": round((time.perf_counter() - started_at) * 1000, 2),
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
