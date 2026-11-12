#!/usr/bin/env python3
from __future__ import annotations

import csv
import json
import re
import time
from pathlib import Path, PurePosixPath
from typing import Iterable


CONTENT_ID_PATTERN = re.compile(r"内容(?P<number>[0-9]+)")
FORMAL_CONTENT_TABLES = (
    Path("对标资料/对标内容库.csv"),
    Path("我方资料/我方内容库.csv"),
)
ACCOUNT_TABLES = (
    Path("对标资料/对标账号库.csv"),
    Path("我方资料/我方账号库.csv"),
)
# 暂存里「已声明身份、尚未提交」的运行同样算号已被占用：并发会话各自 prepare
# 取号时若只读正式表，会同时算到同一个号，只能在 commit 阶段靠改号兜底。
# 加租期是为了避免废弃的旧 run 永久占号（表现为号段无意义地跳号）。
STAGING_ROOT = Path(".meiling/staging")
STAGED_LEASE_SECONDS = 24 * 60 * 60
STAGED_IDENTITY_FILENAMES = ("运行身份.json", "运行状态.json")
STAGED_DIR_NAME_PATTERN = re.compile(r"内容[0-9]+")


class GlobalContentIdError(RuntimeError):
    """Raised when the workspace content-ID namespace cannot be read safely."""


def content_id_number(value: object) -> int | None:
    match = CONTENT_ID_PATTERN.fullmatch(str(value or "").strip())
    return int(match.group("number")) if match else None


def _read_rows(path: Path) -> tuple[list[str], list[dict[str, str]]]:
    try:
        with path.open(encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle)
            if reader.fieldnames is None:
                raise GlobalContentIdError(f"csv_header_missing:{path}")
            return list(reader.fieldnames), [dict(row) for row in reader]
    except (OSError, UnicodeError, csv.Error) as exc:
        raise GlobalContentIdError(f"csv_read_failed:{path}:{exc}") from exc


def _resolve_account_list(root: Path, raw_path: str) -> Path:
    text = str(raw_path or "").strip().replace("\\", "/")
    if not text:
        raise GlobalContentIdError("account_content_list_path_blank")
    candidate = Path(text).expanduser()
    resolved = (
        candidate.resolve()
        if candidate.is_absolute()
        else root.joinpath(*PurePosixPath(text).parts).resolve()
    )
    try:
        resolved.relative_to(root)
    except ValueError as exc:
        raise GlobalContentIdError(f"account_content_list_outside_workspace:{text}") from exc
    return resolved


def content_table_paths(workspace_root: str | Path) -> list[Path]:
    root = Path(workspace_root).expanduser().resolve()
    if not root.is_dir():
        raise GlobalContentIdError(f"workspace_root_invalid:{root}")
    paths: list[Path] = [root / relative for relative in FORMAL_CONTENT_TABLES]
    for relative in ACCOUNT_TABLES:
        account_table = root / relative
        if not account_table.is_file():
            continue
        fields, rows = _read_rows(account_table)
        if "内容列表路径" not in fields:
            continue
        for row in rows:
            raw_path = (row.get("内容列表路径") or "").strip()
            if raw_path:
                paths.append(_resolve_account_list(root, raw_path))
    return list(dict.fromkeys(path.resolve() for path in paths))


def staged_content_ids(
    workspace_root: str | Path,
    *,
    max_age_seconds: float = STAGED_LEASE_SECONDS,
    now: float | None = None,
) -> set[str]:
    """收集暂存目录里仍在租期内、尚未提交的运行所占用的内容号。

    并发会话各自 prepare 取号时只按正式表计算，互不知情就会撞号，只能在
    commit 阶段靠改号兜底。把暂存里已声明的身份一并算进来，取号即可避开。
    """
    staging = Path(workspace_root).expanduser() / STAGING_ROOT
    if not staging.is_dir():
        return set()
    cutoff = (time.time() if now is None else now) - max_age_seconds
    found: set[str] = set()
    for link_dir in staging.iterdir():
        if not link_dir.is_dir():
            continue
        for run_dir in link_dir.iterdir():
            if not run_dir.is_dir():
                continue
            try:
                if run_dir.stat().st_mtime < cutoff:
                    continue
            except OSError:
                continue
            for name in STAGED_IDENTITY_FILENAMES:
                identity_path = run_dir / name
                if not identity_path.is_file():
                    continue
                try:
                    payload = json.loads(identity_path.read_text(encoding="utf-8"))
                except (OSError, UnicodeError, ValueError):
                    continue
                if not isinstance(payload, dict):
                    continue
                value = str(payload.get("content_id") or "").strip()
                if content_id_number(value) is not None:
                    found.add(value)
            dir_match = STAGED_DIR_NAME_PATTERN.search(run_dir.name)
            if dir_match and content_id_number(dir_match.group(0)) is not None:
                found.add(dir_match.group(0))
    return found


def collect_content_ids(
    workspace_root: str | Path,
    *,
    additional_ids: Iterable[object] = (),
) -> set[str]:
    content_ids = {
        str(value).strip()
        for value in additional_ids
        if content_id_number(value) is not None
    }
    for path in content_table_paths(workspace_root):
        if not path.is_file():
            continue
        fields, rows = _read_rows(path)
        if "内容ID" not in fields:
            continue
        content_ids.update(
            value
            for row in rows
            if content_id_number(value := (row.get("内容ID") or "").strip()) is not None
        )
    content_ids.update(staged_content_ids(workspace_root))
    return content_ids


def next_content_id(
    workspace_root: str | Path,
    *,
    additional_ids: Iterable[object] = (),
) -> str:
    numbers = [
        number
        for content_id in collect_content_ids(workspace_root, additional_ids=additional_ids)
        if (number := content_id_number(content_id)) is not None
    ]
    return f"内容{max(numbers, default=0) + 1:03d}"
