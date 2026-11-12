#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import importlib.util
import io
import json
import sys
import time
from pathlib import Path
from typing import Callable, Mapping


class ContentFinalizationError(RuntimeError):
    """Raised when the final content-production transaction cannot complete."""


FailureHook = Callable[[str], None]


def _load_sibling(file_name: str, module_name: str):
    path = Path(__file__).with_name(file_name)
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        raise ContentFinalizationError(f"无法加载执行单元: {path}")
    module = importlib.util.module_from_spec(spec)
    previous_path = list(sys.path)
    sys.path.insert(0, str(path.parent))
    try:
        spec.loader.exec_module(module)
    finally:
        sys.path[:] = previous_path
    return module


def _read_csv(path: Path, required: set[str], label: str) -> tuple[list[str], list[dict[str, str]]]:
    try:
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle)
            fields = list(reader.fieldnames or [])
            if not fields or len(fields) != len(set(fields)) or any(not field for field in fields):
                raise ContentFinalizationError(f"{label}表头无效")
            missing = sorted(required - set(fields))
            if missing:
                raise ContentFinalizationError(f"{label}缺少字段: {', '.join(missing)}")
            return fields, [dict(row) for row in reader]
    except ContentFinalizationError:
        raise
    except (OSError, UnicodeError, csv.Error) as exc:
        raise ContentFinalizationError(f"无法读取{label}{path}: {exc}") from exc


def _csv_bytes(fields: list[str], rows: list[dict[str, str]]) -> bytes:
    buffer = io.StringIO(newline="")
    writer = csv.DictWriter(buffer, fieldnames=fields, lineterminator="\n")
    writer.writeheader()
    writer.writerows(rows)
    return buffer.getvalue().encode("utf-8")


def _unique_row(
    rows: list[dict[str, str]],
    expected: Mapping[str, str],
    label: str,
) -> dict[str, str]:
    matches = [row for row in rows if all(row.get(key, "") == value for key, value in expected.items())]
    if len(matches) != 1:
        raise ContentFinalizationError(f"{label}必须且只能匹配一条记录，实际为{len(matches)}条")
    return matches[0]


def finalize_content_production(
    workspace_root: str | Path,
    *,
    state_file: str | Path,
    failure_hook: FailureHook | None = None,
) -> dict[str, object]:
    """确认内容终稿后，把终稿落到内容库并完成生产流程。"""
    root = Path(workspace_root).expanduser().resolve()
    if not root.is_dir():
        raise ContentFinalizationError(f"工作区不存在: {root}")
    state_path = Path(state_file).expanduser().resolve()

    state_executor = _load_sibling("内容生产状态执行器.py", "content_production_state_executor")
    try:
        current_state = state_executor.validate_state_file(state_path, workspace_root=root)
    except Exception as exc:
        raise ContentFinalizationError(str(exc)) from exc

    if not isinstance(current_state, Mapping):
        raise ContentFinalizationError("正式状态不是对象")
    stages = current_state["阶段"]
    final_stage = stages["内容终稿"] if isinstance(stages, Mapping) else None
    if (
        not isinstance(final_stage, Mapping)
        or final_stage.get("状态") != "已确认"
        or current_state["当前是否完成"]
    ):
        raise ContentFinalizationError("只有内容终稿已确认且流程尚未完成时才能执行收尾")

    record_path = root / str(current_state["内容生产记录路径"])
    try:
        record = record_path.read_text(encoding="utf-8")
        final_version = int(final_stage["版本"])
        final_body = state_executor._extract_record_version(record, "内容终稿", final_version)
        if not final_body:
            raise ContentFinalizationError("当前终稿版本不存在或为空")
        final_title = state_executor._extract_required_final_field(final_body, "最终内容标题")
        state_executor._validate_title_length(final_title, "当前终稿.最终内容标题")
    except ContentFinalizationError:
        raise
    except Exception as exc:
        raise ContentFinalizationError(str(exc)) from exc

    content_path = root / "我方资料" / "我方内容库.csv"
    topic_path = root / "我方资料" / "选题库.csv"
    content_required = {
        "内容ID", "选题ID", "内容目录路径", "标题", "当前状态",
        "内容生产记录路径", "媒介形式",
    }
    topic_required = {"选题ID", "当前状态", "内容生产记录路径"}
    content_fields, content_rows = _read_csv(content_path, content_required, "我方内容库")
    topic_fields, topic_rows = _read_csv(topic_path, topic_required, "选题库")
    content_row = _unique_row(
        content_rows,
        {"内容ID": str(current_state["内容ID"]), "选题ID": str(current_state["选题ID"])},
        "我方内容库当前内容",
    )
    topic_row = None
    if str(current_state["选题ID"]).strip():
        topic_row = _unique_row(
            topic_rows,
            {"选题ID": str(current_state["选题ID"])},
            "选题库当前选题",
        )
    if content_row["内容目录路径"] != current_state["当前内容目录"]:
        raise ContentFinalizationError("我方内容库当前内容目录与正式状态不一致")

    try:
        completed_state = state_executor.build_state_for_action(current_state, "complete")
    except Exception as exc:
        raise ContentFinalizationError(str(exc)) from exc

    content_row.update(
        {
            "标题": final_title,
            "当前状态": "已完成",
            "内容目录路径": str(current_state["当前内容目录"]),
            "内容生产记录路径": str(current_state["内容生产记录路径"]),
        }
    )
    if topic_row is not None:
        topic_row.update(
            {
                "当前状态": "已生产",
                "内容生产记录路径": str(current_state["内容生产记录路径"]),
            }
        )
    content_bytes = _csv_bytes(content_fields, content_rows)
    topic_bytes = _csv_bytes(topic_fields, topic_rows)

    targets = (
        content_path,
        *((topic_path,) if topic_row is not None else ()),
        state_path,
    )
    snapshots = {path: path.read_bytes() if path.is_file() else None for path in targets}
    try:
        state_executor._replace_bytes_atomically(content_path, content_bytes)
        if topic_row is not None:
            state_executor._replace_bytes_atomically(topic_path, topic_bytes)
        if failure_hook:
            failure_hook("indexes_written")

        state_result = state_executor.write_state_atomically(
            state_path,
            completed_state,
            workspace_root=root,
        )
        if failure_hook:
            failure_hook("completed")
    except Exception as exc:
        rollback_errors: list[str] = []
        for path in reversed(targets):
            try:
                state_executor._restore_snapshot(path, snapshots[path])
            except Exception as rollback_exc:  # pragma: no cover - catastrophic I/O
                rollback_errors.append(f"{path}: {rollback_exc}")
        if rollback_errors:
            raise ContentFinalizationError(
                "内容生产收尾失败且回滚不完整: " + "；".join(rollback_errors)
            ) from exc
        raise ContentFinalizationError(f"内容生产收尾失败，已恢复全部旧文件: {exc}") from exc

    return {
        "state_file": str(state_path),
        "state_sha256": state_result["state_sha256"],
        "record_path": str(record_path),
        "content_library_path": str(content_path),
        "topic_library_path": str(topic_path) if topic_row is not None else "",
        "route": state_result["route"],
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="确认内容终稿后完成生产收尾")
    parser.add_argument("--workspace-root", required=True, type=Path)
    parser.add_argument("--state-file", required=True, type=Path)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    started_at = time.perf_counter()
    try:
        result = finalize_content_production(
            args.workspace_root,
            state_file=args.state_file,
        )
    except ContentFinalizationError as exc:
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
