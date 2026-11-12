#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import tempfile
from pathlib import Path
from typing import Any


class MediaStrategyFinalizeError(RuntimeError):
    pass


PLAN_TOP_HEADINGS = [(1, "我方账号方向"), (2, "方向总览"), (2, "1. 自身条件"), (2, "2. 用户与需求"), (2, "3. 产品与变现"), (2, "4. 账号与内容")]
OVERVIEW_HEADINGS = [(1, "我方当前概要"), (2, "自身条件概要"), (2, "用户与需求概要"), (2, "产品与变现概要"), (2, "账号与内容概要")]
SECTION_NAMES = ["自身条件", "用户与需求", "产品与变现", "账号与内容"]


def _normalize_text(value: object, label: str) -> str:
    if not isinstance(value, str):
        raise MediaStrategyFinalizeError(f"{label}_invalid")
    text = value.strip()
    if not text:
        raise MediaStrategyFinalizeError(f"{label}_empty")
    return text + "\n"


def _read_standard_input() -> dict[str, object]:
    if sys.stdin.isatty():
        raise MediaStrategyFinalizeError("input_requires_stdin_redirect")
    try:
        payload = json.loads(sys.stdin.buffer.read().decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise MediaStrategyFinalizeError("input_json_invalid") from exc
    if not isinstance(payload, dict):
        raise MediaStrategyFinalizeError("input_json_object_required")
    return payload


def _headings(text: str) -> list[tuple[int, str]]:
    return [(len(m.group(1)), m.group(2).strip()) for m in re.finditer(r"^(#{1,6})[ \t]+(.+?)[ \t]*$", text, flags=re.MULTILINE)]


def _top_headings(text: str) -> list[tuple[int, str]]:
    return [heading for heading in _headings(text) if heading[0] <= 2]


def _section_body(text: str, heading: str) -> str:
    match = re.search(rf"^##[ \t]+{re.escape(heading)}[ \t]*$", text, flags=re.MULTILINE)
    if not match:
        return ""
    following = re.search(r"^##[ \t]+", text[match.end() :], flags=re.MULTILINE)
    end = match.end() + (following.start() if following else len(text[match.end() :]))
    return text[match.end() : end].strip()


def _validate_plan(plan_text: str) -> None:
    if _top_headings(plan_text) != PLAN_TOP_HEADINGS:
        raise MediaStrategyFinalizeError("plan_heading_contract_invalid")
    for index, section in enumerate(SECTION_NAMES, start=1):
        body = _section_body(plan_text, f"{index}. {section}")
        if not body:
            raise MediaStrategyFinalizeError(f"plan_section_invalid:{section}")


def _validate_overview(overview_text: str) -> None:
    if _top_headings(overview_text) != OVERVIEW_HEADINGS:
        raise MediaStrategyFinalizeError("overview_heading_contract_invalid")
    for heading in ("自身条件概要", "用户与需求概要", "产品与变现概要", "账号与内容概要"):
        if not _section_body(overview_text, heading):
            raise MediaStrategyFinalizeError(f"overview_section_invalid:{heading}")


def _temporary(target: Path, payload: bytes) -> Path:
    target.parent.mkdir(parents=True, exist_ok=True)
    descriptor, name = tempfile.mkstemp(prefix=f".{target.name}.", suffix=".tmp", dir=target.parent)
    temporary = Path(name)
    with os.fdopen(descriptor, "wb") as handle:
        handle.write(payload)
        handle.flush()
        os.fsync(handle.fileno())
    return temporary


def finalize_media_strategy(workspace_root: str | Path, *, plan_text: str, overview_text: str) -> dict[str, Any]:
    root = Path(workspace_root).expanduser().resolve()
    if not root.is_dir():
        raise MediaStrategyFinalizeError("workspace_root_invalid")
    plan_text = _normalize_text(plan_text, "plan")
    overview_text = _normalize_text(overview_text, "overview")
    _validate_plan(plan_text)
    _validate_overview(overview_text)
    payloads = {root / "我方资料/我方自媒体规划.md": plan_text.encode("utf-8"), root / "我方资料/我方当前概要.md": overview_text.encode("utf-8")}
    snapshots = {path: path.read_bytes() if path.is_file() else None for path in payloads}
    temporaries = {path: _temporary(path, payload) for path, payload in payloads.items()}
    committed: list[Path] = []
    try:
        for target, payload in payloads.items():
            os.replace(temporaries[target], target)
            committed.append(target)
            if target.read_bytes() != payload:
                raise MediaStrategyFinalizeError(f"post_write_mismatch:{target.name}")
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
    return {"ok": True, "status": "committed", "plan_path": "我方资料/我方自媒体规划.md", "overview_path": "我方资料/我方当前概要.md", "plan_sha256": hashlib.sha256(payloads[root / "我方资料/我方自媒体规划.md"]).hexdigest(), "overview_sha256": hashlib.sha256(payloads[root / "我方资料/我方当前概要.md"]).hexdigest()}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="从标准输入读取账号方向正文，校验并原子保存方向与四栏概要")
    parser.add_argument("--workspace-root", required=True)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        payload = _read_standard_input()
        result = finalize_media_strategy(args.workspace_root, plan_text=payload.get("plan_text"), overview_text=payload.get("overview_text"))
    except (OSError, UnicodeError, ValueError, MediaStrategyFinalizeError) as exc:
        print(json.dumps({"ok": False, "status": "failed", "error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
