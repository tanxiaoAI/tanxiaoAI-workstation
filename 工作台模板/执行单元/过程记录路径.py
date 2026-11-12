#!/usr/bin/env python3
from __future__ import annotations

import re
from collections import Counter
from pathlib import Path
from typing import Dict, Iterable, Mapping


PROCESS_DIR_NAME = "过程记录"
BATCH_REPORT_DIR_NAME = "批处理报告"
CONTENT_PROCESS_DIR_NAME = "内容过程记录"


class UnsafeContentDirectoryError(ValueError):
    """A CSV path is outside the workspace's supported content roots."""


def sanitize_title(title: str, limit: int = 10) -> str:
    short = (title or "").strip()[:limit]
    short = re.sub(r'[\\/:*?"<>|]', "", short).strip()
    return short or "未命名内容"


def sanitize_path_component(value: str) -> str:
    text = re.sub(r'[\\/:*?"<>|]', "", str(value or "").strip())
    return text or "未命名账号"


def build_content_dir_name(
    content_library_path: Path, row: Mapping[str, str], title: str | None = None
) -> str:
    """内容目录的唯一命名规则，对标与明细补全阶段共用。

    标题统一走 ``sanitize_title`` 的默认截断长度——**只取标题的前 10 个字符**。
    例如标题「审计实测：把重复工作交给智能体」，算出来的目录名是
    「内容636-审计实测：把重复工作」。所以**目录名不等于完整标题**，人工对账时
    不要拿完整标题去匹配目录名（2026-09-14 实测踩过这个误解）。

    同一内容在任何阶段都必须算出同一个目录名，否则两处会各建一套目录。
    截断长度属于这套规则的一部分，**不得单独调整**：已建档内容是按当时长度生成的，
    改了长度会让同一个内容在下次访问时算出不同名字，进而建出重复目录。
    """
    raw_title = str(title if title is not None else (row.get("标题") or "")).strip()
    sanitized_title = sanitize_title(raw_title)
    if "我方资料" in content_library_path.parts:
        content_id = str(row.get("内容ID") or "").strip()
        if content_id:
            return f"{sanitize_path_component(content_id)}-{sanitized_title}"
        return sanitized_title
    if not raw_title:
        return build_benchmark_content_dir_name(str(row.get("内容ID") or ""), raw_title)
    # 账号名列存在历史别名，统一在此兼容，避免各调用方各写一套。
    account_name = sanitize_path_component(
        row.get("账号名") or row.get("账号名称") or row.get("账号昵称") or ""
    )
    return f"{account_name}-{sanitized_title}"


def plan_content_dir_names(
    rows: Iterable[Mapping[str, str]],
    content_library_path: Path,
    *,
    existing_names: Mapping[str, str] | None = None,
) -> Dict[str, str]:
    """为一批内容预分配唯一的内容目录名，返回 ``{内容ID: 目录名}``。

    目录名默认是 ``账号名-标题前10字``（见 ``build_content_dir_name``）。标题
    前缀高度重复时——小红书标题普遍以 ``#话题#`` 开头，前 10 字经常完全相同
    ——多条内容会算出同一个名字，于是共用同一个目录、产物互相覆盖。这里对
    重名内容追加 ``-内容ID`` 兜底，保证同批次内每条内容都有独立目录。

    ``existing_names`` 传入既有的「目录名 -> 内容ID」占用关系（来自 CSV 已记录
    的路径或磁盘上已有的目录），避免新内容抢走别条内容正在用的目录名。
    """
    prepared: list[tuple[str, str]] = []
    for row in rows:
        if not row:
            continue
        content_id = str(row.get("内容ID") or "").strip()
        if not content_id:
            continue
        title = str(row.get("标题") or row.get("内容标题") or "").strip()
        prepared.append((content_id, build_content_dir_name(content_library_path, row, title)))

    counts = Counter(name for _, name in prepared)
    taken: Dict[str, str] = dict(existing_names or {})
    planned: Dict[str, str] = {}
    for content_id, name in prepared:
        if name in planned.values() or counts[name] > 1 or taken.get(name) not in (None, content_id):
            final = f"{name}-{content_id}"
        else:
            final = name
        planned[content_id] = final
        taken[final] = content_id
    return planned


def collect_content_dir_owners(
    rows: Iterable[Mapping[str, str] | None],
    *,
    content_root: Path,
    workspace_root: Path,
) -> Dict[str, str]:
    """从既有 CSV 行收集「内容库直属子目录名 -> 内容ID」的占用关系。

    只统计直接位于内容库根目录下的目录（即一条内容一个目录的那种），用来给
    ``plan_content_dir_names`` 做跨批次冲突检测。
    """
    workspace = workspace_root.resolve()
    root = content_root.resolve()
    owners: Dict[str, str] = {}
    for row in rows:
        if not row:
            continue
        content_id = str(row.get("内容ID") or "").strip()
        raw = str(row.get("内容目录路径") or "").strip()
        if not content_id or not raw:
            continue
        candidate = Path(raw).expanduser()
        if not candidate.is_absolute():
            candidate = workspace / candidate
        candidate = candidate.resolve()
        if candidate.parent != root:
            continue
        owners.setdefault(candidate.name, content_id)
    return owners


def build_benchmark_content_dir_name(content_id: str, title: str) -> str:
    """Build the stable directory name required only for empty benchmark titles."""
    raw_title = str(title or "").strip()
    if raw_title:
        raise ValueError("共享对标目录命名函数只用于空标题")
    raw_content_id = str(content_id or "").strip()
    safe_content_id = re.sub(r'[\\/:*?"<>|]', "", raw_content_id)
    if (
        not raw_content_id
        or safe_content_id != raw_content_id
        or safe_content_id in {"", ".", ".."}
    ):
        raise ValueError("空标题的新建对标内容目录缺少有效内容ID")
    return f"{safe_content_id}-未命名内容"


def build_account_process_dir(content_list_path: Path) -> Path:
    return content_list_path.resolve().parent / PROCESS_DIR_NAME


def build_batch_report_dir(content_list_path: Path) -> Path:
    return build_account_process_dir(content_list_path) / BATCH_REPORT_DIR_NAME


def build_batch_report_path(content_list_path: Path, report_name: str) -> Path:
    return build_batch_report_dir(content_list_path) / report_name


def build_content_process_dir(content_list_path: Path, content_id: str, title: str) -> Path:
    folder_name = f"{content_id}-{sanitize_title(title)}"
    return build_account_process_dir(content_list_path) / CONTENT_PROCESS_DIR_NAME / folder_name


def build_content_process_path(content_list_path: Path, content_id: str, title: str, file_name: str) -> Path:
    return build_content_process_dir(content_list_path, content_id, title) / file_name


def _is_child(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return path != root


def _safe_path_from_text(path_text: str, workspace_root: Path) -> Path:
    raw_path = Path(path_text).expanduser()
    if not raw_path.is_absolute():
        raw_path = workspace_root / raw_path
    return raw_path.resolve()


def resolve_existing_content_dir(
    *,
    workspace_root: Path,
    content_root: Path,
    rows: Iterable[Mapping[str, str] | None],
    path_fields: Iterable[str],
) -> Path | None:
    """Resolve a legacy content directory without allowing CSV path escapes.

    Explicit ``内容目录路径`` wins when valid. Other output paths are only used
    when they point into one supported root and agree on the same parent.
    """
    workspace = workspace_root.resolve()
    preferred_root = content_root.resolve()
    allowed_roots = {
        (workspace / "我方资料" / "我方内容").resolve(),
        (workspace / "对标资料" / "对标内容").resolve(),
        preferred_root,
    }
    field_list = list(path_fields)
    explicit_field = "内容目录路径"
    explicit_candidates: list[Path] = []
    legacy_candidates: list[Path] = []

    def append_candidate(path_text: str, *, explicit: bool) -> None:
        candidate = _safe_path_from_text(path_text, workspace)
        if not any(_is_child(candidate, root) for root in allowed_roots):
            raise UnsafeContentDirectoryError(
                f"内容路径超出工作区允许目录: {path_text}"
            )
        if not candidate.exists():
            return
        directory = candidate if candidate.is_dir() else candidate.parent
        if not any(_is_child(directory, root) for root in allowed_roots):
            raise UnsafeContentDirectoryError(
                f"内容目录超出工作区允许目录: {path_text}"
            )
        if explicit:
            explicit_candidates.append(directory)
        else:
            legacy_candidates.append(directory)

    for row in rows:
        if not row:
            continue
        explicit_text = str(row.get(explicit_field) or "").strip()
        if explicit_text:
            append_candidate(explicit_text, explicit=True)
        for field in field_list:
            if field == explicit_field:
                continue
            path_text = str(row.get(field) or "").strip()
            if path_text:
                append_candidate(path_text, explicit=False)

    if explicit_candidates:
        unique = list(dict.fromkeys(explicit_candidates))
        if len(unique) > 1:
            raise UnsafeContentDirectoryError("CSV 中的内容目录路径互相冲突")
        return unique[0]
    if not legacy_candidates:
        return None
    unique = list(dict.fromkeys(legacy_candidates))
    if len(unique) > 1:
        raise UnsafeContentDirectoryError("CSV 中的旧输出路径指向多个内容目录")
    return unique[0]
