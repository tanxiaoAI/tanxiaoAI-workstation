#!/usr/bin/env python3
"""Validate one AI JSON candidate and atomically write an allowed business file."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import tempfile
from pathlib import Path
from typing import Any

from 权限错误 import permission_error_code


MAX_INPUT_BYTES = 2 * 1024 * 1024
DOCUMENT_TYPES = {
    "temporary-workflow-input",
    "content-feature-candidate",
}


class ControlledJsonWriteError(RuntimeError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="从标准输入或受控暂存文件校验并原子写入允许的 JSON 文件")
    parser.add_argument("--workspace-root", default=".")
    parser.add_argument("--document-type", choices=sorted(DOCUMENT_TYPES), required=True)
    parser.add_argument("--input-path", default="")
    parser.add_argument("--output-path", required=True)
    return parser.parse_args()


def reject_constant(value: str) -> None:
    raise ControlledJsonWriteError("json_invalid", f"JSON 不允许非常规数值：{value}")


def unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ControlledJsonWriteError("json_duplicate_key", f"JSON 字段重复：{key}")
        result[key] = value
    return result


def read_payload(path: Path | None = None) -> dict[str, Any]:
    if path is None:
        raw = sys.stdin.buffer.read(MAX_INPUT_BYTES + 1)
    else:
        try:
            with path.open("rb") as handle:
                raw = handle.read(MAX_INPUT_BYTES + 1)
        except OSError as exc:
            raise ControlledJsonWriteError("json_input_unreadable", "JSON 暂存输入无法读取") from exc
    if len(raw) > MAX_INPUT_BYTES:
        raise ControlledJsonWriteError("json_too_large", "JSON 输入超过 2 MiB")
    if not raw.strip():
        raise ControlledJsonWriteError("json_input_empty", "JSON 输入没有内容")
    try:
        text = raw.decode("utf-8")
        payload = json.loads(
            text,
            object_pairs_hook=unique_object,
            parse_constant=reject_constant,
        )
    except ControlledJsonWriteError:
        raise
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ControlledJsonWriteError("json_invalid", "JSON 输入不是合法 UTF-8 JSON") from exc
    if not isinstance(payload, dict):
        raise ControlledJsonWriteError("json_structure_invalid", "JSON 顶层必须是对象")
    return payload


def resolve_input(workspace_root: Path, raw_path: str) -> Path:
    path = resolve_output(workspace_root, raw_path)
    relative = path.relative_to(workspace_root.resolve())
    if len(relative.parts) < 4 or relative.parts[:2] != (".meiling", "staging"):
        raise ControlledJsonWriteError("input_path_invalid", "JSON 输入必须位于本轮受控暂存目录")
    if path.is_symlink() or not path.is_file():
        raise ControlledJsonWriteError("input_path_invalid", "JSON 暂存输入必须是普通文件")
    return path


def resolve_output(workspace_root: Path, raw_path: str) -> Path:
    lexical_root = Path(os.path.abspath(workspace_root.expanduser()))
    root = lexical_root.resolve()
    candidate = Path(raw_path).expanduser()
    unresolved = candidate if candidate.is_absolute() else lexical_root / candidate
    lexical = Path(os.path.abspath(unresolved))
    resolved = lexical.resolve()
    try:
        resolved.relative_to(root)
    except ValueError as exc:
        raise ControlledJsonWriteError("path_outside_workspace", "JSON 输出必须位于当前工作区") from exc
    try:
        relative = lexical.relative_to(lexical_root)
    except ValueError as exc:
        raise ControlledJsonWriteError("path_outside_workspace", "JSON 输出必须位于当前工作区") from exc
    current = lexical_root
    for part in relative.parts:
        current = current / part
        if current.is_symlink():
            raise ControlledJsonWriteError("path_symlink_forbidden", "JSON 输出路径不能包含符号链接")
    if resolved.exists() and not resolved.is_file():
        raise ControlledJsonWriteError("output_path_invalid", "JSON 输出路径不是普通文件")
    return resolved


def validate_target(workspace_root: Path, output: Path, document_type: str) -> None:
    relative = output.relative_to(workspace_root).as_posix()
    parts = output.relative_to(workspace_root).parts
    valid = False
    if document_type == "temporary-workflow-input":
        valid = (
            len(parts) >= 5
            and parts[:2] == (".meiling", "staging")
            and output.suffix.lower() == ".json"
        )
    elif document_type == "content-feature-candidate":
        valid = (
            output.name == "内容特征候选.json"
            and len(parts) >= 4
            and parts[:2] == (".meiling", "staging")
        )
    if not valid:
        raise ControlledJsonWriteError(
            "output_path_invalid",
            f"{document_type} 不允许写入目标路径：{relative}",
        )


def validate_structure(payload: dict[str, Any], document_type: str) -> None:
    if document_type == "content-feature-candidate":
        required = {
            "schema_version", "content_task", "profile", "identified_features",
            "observations", "tradeoffs",
        }
        if payload.get("schema_version") != "1" or set(payload) != required:
            raise ControlledJsonWriteError("json_structure_invalid", "内容特征候选基础结构无效")


def atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".stage", dir=str(path.parent)
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2, allow_nan=False)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def run(args: argparse.Namespace) -> dict[str, Any]:
    workspace_input = Path(args.workspace_root).expanduser()
    workspace_root = workspace_input.resolve()
    input_path = (
        resolve_input(workspace_input, args.input_path)
        if str(args.input_path or "").strip()
        else None
    )
    output = resolve_output(workspace_input, args.output_path)
    if input_path is not None and input_path == output:
        raise ControlledJsonWriteError("input_path_invalid", "JSON 暂存输入不能同时作为正式输出")
    validate_target(workspace_root, output, args.document_type)
    payload = read_payload(input_path)
    validate_structure(payload, args.document_type)
    atomic_write_json(output, payload)
    if input_path is not None:
        input_path.unlink()
    return {
        "ok": True,
        "status": "written",
        "document_type": args.document_type,
        "output_path": output.relative_to(workspace_root).as_posix(),
        "sha256": sha256_file(output),
        "input_consumed": True,
        "input_source": "file" if input_path is not None else "stdin",
    }


def main() -> int:
    try:
        result = run(parse_args())
    except ControlledJsonWriteError as exc:
        print(
            json.dumps(
                {"ok": False, "status": "failed", "error_code": exc.code, "message": str(exc)},
                ensure_ascii=False,
            )
        )
        return 1
    except Exception as exc:  # noqa: BLE001
        error_code = permission_error_code(exc, "filesystem") or "unexpected_error"
        print(
            json.dumps(
                {
                    "ok": False,
                    "status": "failed",
                    "error_code": error_code,
                    "message": "JSON 写入发生未预期错误",
                },
                ensure_ascii=False,
            )
        )
        return 1
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
