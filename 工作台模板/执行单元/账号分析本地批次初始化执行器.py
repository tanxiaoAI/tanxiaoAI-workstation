#!/usr/bin/env python3
"""Allocate the single local run identity for one account analysis flow."""
from __future__ import annotations

import argparse
import json
import re
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path


SAFE_RUN_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}")
FLOW_STAGING_ROOTS = {
    "benchmark_account": "benchmark-account-diagnose",
    "self_account": "self-account-diagnose",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="为账号分析创建本地 run_id")
    parser.add_argument("--workspace-root", required=True)
    parser.add_argument("--flow-type", default="benchmark_account", choices=sorted(FLOW_STAGING_ROOTS))
    parser.add_argument("--run-id", default="", help="仅用于恢复同一已存在批次")
    return parser.parse_args()


def run(args: argparse.Namespace) -> dict[str, str]:
    root = Path(args.workspace_root).expanduser().resolve()
    if not root.is_dir():
        raise RuntimeError("workspace_root_invalid")
    flow_type = str(getattr(args, "flow_type", "") or "").strip() or "benchmark_account"
    if flow_type not in FLOW_STAGING_ROOTS:
        raise RuntimeError("flow_type_invalid")
    requested = str(args.run_id or "").strip()
    if requested and not SAFE_RUN_ID.fullmatch(requested):
        raise RuntimeError("run_id_invalid")
    run_id = requested or f"run-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}-{uuid.uuid4().hex[:10]}"
    run_dir = root / ".meiling/staging" / FLOW_STAGING_ROOTS[flow_type] / run_id
    if run_dir.exists() and not requested:
        raise RuntimeError("run_id_collision")
    run_dir.mkdir(parents=True, exist_ok=True)
    return {
        "ok": True,
        "flow_type": flow_type,
        "run_id": run_id,
        "staging_dir": run_dir.relative_to(root).as_posix(),
    }


def main() -> int:
    try:
        print(json.dumps(run(parse_args()), ensure_ascii=False))
        return 0
    except (OSError, RuntimeError) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
