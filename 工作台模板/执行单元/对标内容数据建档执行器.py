#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
import time
import uuid
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path, PurePosixPath
from typing import Any, Mapping
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

try:
    import fcntl
except ImportError:  # pragma: no cover - Windows uses msvcrt below.
    fcntl = None

try:
    import msvcrt
except ImportError:  # pragma: no cover - POSIX uses fcntl above.
    msvcrt = None

from csv安全写入 import (
    CsvTransactionError,
    read_csv_table_with_snapshot,
    write_csv_tables_atomically,
)
from 全局内容ID import GlobalContentIdError, next_content_id as next_global_content_id
from 互动数解析 import parse_count
from 正文证据选择器 import (
    choose_transcript,
    normalize_status,
    platform_caption_evidence,
    video_url_hash,
)
from 获取视频文案执行器 import materialize_bilibili_platform_caption, materialize_xiaohongshu_platform_caption
from 抖音内容链接解析 import canonicalize_douyin_content_url, extract_douyin_content_id
from 对标内容账号列表同步 import (
    AccountContentListSyncError,
    prepare_account_content_list_sync,
    verify_account_content_list_sync,
)


CONTENT_LIBRARY = Path("对标资料/对标内容库.csv")
ACCOUNT_LIBRARY = Path("对标资料/对标账号库.csv")
SUPPORTED_PLATFORMS = {"小红书", "抖音", "B站"}
CONFIRMED_EMPTY_BODY_MARKER = "正文状态：原内容确认未填写文字正文。"
SUMMARY_HEADINGS = ("内容主题", "开头钩子", "核心观点", "结构拆解", "结尾动作")
REQUIRED_CONTENT_FIELDS = {
    "内容ID", "平台", "账号ID", "账号名", "标题", "媒介形式",
    "内容目录路径",
    "对外页面链接", "视频文案路径",
    "内容分析路径", "备注",
}
TRACKING_QUERY_KEYS = {
    "utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content",
    "share_from_user_hidden", "share_item_id", "share_source", "share_id",
    "timestamp", "from", "from_source",
}
IDENTITY_QUERY_KEYS = {"modal_id", "aweme_id", "item_id", "note_id", "bvid"}
XIAOHONGSHU_PUBLIC_QUERY_KEYS = {"xsec_source", "xsec_token"}
LOCAL_STAGING_PREFIX = ".meiling/staging/benchmark-content-base/"
LOCAL_STAGING_ROOT = Path(LOCAL_STAGING_PREFIX.rstrip("/"))
LOCAL_CONFIG_FILE = Path("本地配置.txt")
DATA_SOURCE_PREFERENCE_KEY = "DATA_SOURCE_PREFERENCE"
DATA_SOURCE_ALIASES = {
    "chrome_devtools": "chrome_devtools",
    "chrome": "chrome_devtools",
    "justoneapi": "justoneapi",
    "just_one_api": "justoneapi",
}


def read_local_config(root: Path) -> dict[str, str]:
    """读取工作区 `本地配置.txt`；与工作台设置页读写的是同一份文件。"""
    try:
        text = (root / LOCAL_CONFIG_FILE).read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return {}
    values: dict[str, str] = {}
    for line in text.splitlines():
        match = re.match(r"^\s*([A-Za-z0-9_]+)\s*=(.*)$", line)
        if not match:
            continue
        raw = match.group(2).strip()
        if len(raw) >= 2 and raw[0] == raw[-1] and raw[0] in {"'", '"'}:
            raw = raw[1:-1].strip()
        values[match.group(1)] = raw
    return values


def resolve_source_choice(root: Path) -> dict[str, object]:
    """按设置页的首选取数方式决定本轮来源；不再询问用户，也不自动换源。"""
    config = read_local_config(root)
    justoneapi_configured = bool(str(config.get("JUSTONEAPI_TOKEN", "")).strip())
    preference = str(config.get(DATA_SOURCE_PREFERENCE_KEY, "")).strip().lower()
    selected = DATA_SOURCE_ALIASES.get(preference, "")
    if selected == "justoneapi" and not justoneapi_configured:
        return {
            "ok": False,
            "status": "source_unavailable",
            "selected_source": "",
            "preference": preference,
            "justoneapi_configured": False,
            "reason": "preferred_justoneapi_without_token",
        }
    if not selected:
        selected = "chrome_devtools"
    return {
        "ok": True,
        "status": "resolved",
        "selected_source": selected,
        "preference": preference or "auto",
        "justoneapi_configured": justoneapi_configured,
        "selection_rule": "preferred" if preference else "default_chrome",
    }


class ContentIntakeError(RuntimeError):
    pass


SINGLE_CHROME_SCHEMA_VERSION = 1

# This is the only canonical Chrome candidate definition.  The CLI contract
# action serializes these entries directly; callers must not infer a template
# from the compatibility input allowlist below.
CHROME_CANONICAL_FIELD_SPECS = (
    {"name": "schema_version", "json_types": ("integer",), "const": SINGLE_CHROME_SCHEMA_VERSION, "required": True, "allow_empty": False, "template": SINGLE_CHROME_SCHEMA_VERSION},
    {"name": "data_source", "json_types": ("string",), "const": "chrome_devtools", "required": True, "allow_empty": False, "template": "chrome_devtools"},
    {"name": "状态", "json_types": ("string",), "enum": ("成功", "失败"), "required": True, "allow_empty": False, "template": "成功"},
    {"name": "失败原因", "json_types": ("string",), "required": True, "allow_empty": True, "required_when": {"状态": "失败"}, "empty_when": {"状态": "成功"}, "template": ""},
    {"name": "平台", "json_types": ("string",), "enum": tuple(sorted(SUPPORTED_PLATFORMS)), "required_when": {"状态": "成功"}, "allow_empty": False, "template": "<小红书|抖音|B站>"},
    {"name": "平台原生内容ID", "json_types": ("string",), "required_when": {"状态": "成功"}, "allow_empty": False, "template": "<从当前页面读取>"},
    {"name": "账号ID", "json_types": ("string",), "required_when": {"状态": "成功"}, "allow_empty": False, "template": "<从当前页面读取>"},
    {"name": "账号名", "json_types": ("string",), "required_when": {"状态": "成功"}, "allow_empty": False, "template": "<从当前页面读取>"},
    {"name": "标题", "json_types": ("string",), "required_when": {"状态": "成功"}, "allow_empty": False, "template": "<从当前页面读取>"},
    {"name": "封面图原链接", "json_types": ("string",), "required": False, "allow_empty": True, "template": ""},
    {"name": "视频下载链接", "json_types": ("string",), "required": False, "allow_empty": True, "template": "", "description": "B站视频允许含签名查询参数的 .m4s 临时流地址；普通平台仍使用完整 HTTP(S) 地址"},
    {"name": "媒介形式", "json_types": ("string",), "enum": ("视频", "图文"), "required_when": {"状态": "成功"}, "allow_empty": False, "template": "<视频|图文>"},
    {"name": "互动数据", "json_types": ("object",), "required": False, "allow_empty": True, "object_fields": {"点赞数": ("string", "integer", "number"), "收藏数": ("string", "integer", "number"), "分享数": ("string", "integer", "number"), "评论数": ("string", "integer", "number")}, "template": {"点赞数": 0, "收藏数": 0, "分享数": 0, "评论数": 0}},
    {"name": "发布时间", "json_types": ("string", "integer", "number"), "required": False, "allow_empty": True, "template": ""},
    {"name": "对外页面链接", "json_types": ("string",), "required_when": {"状态": "成功"}, "allow_empty": False, "template": "<当前页面真实 URL>"},
    # 正文获取状态是由正文证据确定性推导的内部字段，不属于 Chrome
    # candidate 的模型填写边界。页面正文仍可作为可选证据保留。
    {"name": "正文文本", "json_types": ("string",), "required": False, "allow_empty": True, "template": ""},
    {"name": "bvid", "json_types": ("string",), "required": False, "allow_empty": True, "template": ""},
    {"name": "aid", "json_types": ("string", "integer"), "required": False, "allow_empty": True, "template": ""},
    {"name": "cid", "json_types": ("string", "integer"), "required": False, "allow_empty": True, "template": ""},
)

# Aliases are legacy input only.  In particular, desc is a title fallback and
# is deliberately absent from CHROME_CANONICAL_FIELD_SPECS and its template.
CHROME_COMPATIBILITY_ALIASES = {
    "data_source": ("基础数据获取方式", "fetch_method", "source"),
    "状态": ("status",),
    "失败原因": ("failure_reason", "error", "message"),
    "平台": ("platform",),
    "平台原生内容ID": ("内容原生ID", "note_id", "item_id", "aweme_id"),
    "账号ID": ("account_id", "author_id", "uid"),
    "账号名": ("account_name", "author_name", "nickname"),
    "标题": ("title", "desc"),
    "封面图原链接": ("cover_url",),
    "视频下载链接": ("video_url",),
    "媒介形式": ("media_form", "media_type", "type"),
    "互动数据": ("stats", "interactions"),
    "发布时间": ("published_at", "publish_time", "create_time", "time", "timestamp"),
    "对外页面链接": ("url", "source_url", "content_url"),
    "正文文本": ("字幕文本", "caption_text", "transcript", "text"),
    "正文获取状态": ("字幕状态", "caption_status"),
    "bvid": (),
    "aid": (),
    "cid": (),
}
CHROME_COMPATIBILITY_ROOT_FIELDS = frozenset({"data", "content_id", "fields", "evidence", "likes", "like_count", "collects", "collect_count", "shares", "share_count", "comments", "comment_count", "点赞数", "收藏数", "分享数", "评论数", "B站视频标识"})
SINGLE_CHROME_ROOT_FIELDS = frozenset(
    {str(spec["name"]) for spec in CHROME_CANONICAL_FIELD_SPECS}
    | CHROME_COMPATIBILITY_ROOT_FIELDS
    | {"正文获取状态"}
    | {alias for aliases in CHROME_COMPATIBILITY_ALIASES.values() for alias in aliases}
)


def chrome_canonical_contract() -> dict[str, object]:
    """Return the compact model-facing contract; validators keep full specs private."""
    template: dict[str, object] = {}
    for spec in CHROME_CANONICAL_FIELD_SPECS:
        template[str(spec["name"])] = spec["template"]
    return {
        "ok": True,
        "status": "chrome_canonical_contract",
        "contract_version": SINGLE_CHROME_SCHEMA_VERSION,
        "canonical_output": {
            "template": template,
            "required_when_success": [
                "平台", "平台原生内容ID", "账号ID", "账号名", "标题", "媒介形式", "对外页面链接",
            ],
            "write_rule": "只能使用 template 中的字段；未获取到的非必填字段留空；禁止附加页面原始对象或调试字段。",
            "raw_platform_detail_policy": "staging_only",
        },
    }


def _json_type_matches(value: object, accepted: tuple[str, ...]) -> bool:
    for type_name in accepted:
        if type_name == "string" and isinstance(value, str):
            return True
        if type_name == "object" and isinstance(value, Mapping):
            return True
        if type_name == "integer" and isinstance(value, int) and not isinstance(value, bool):
            return True
        if type_name == "number" and isinstance(value, (int, float)) and not isinstance(value, bool):
            return True
    return False


def _value_is_empty(value: object) -> bool:
    return value is None or (isinstance(value, str) and not value.strip())


def validate_canonical_chrome_output(payload: Mapping[str, object]) -> None:
    """Enforce the queried contract only when a caller uses its canonical form."""
    if payload.get("data_source") != "chrome_devtools":
        return
    aliases = {
        alias
        for values in CHROME_COMPATIBILITY_ALIASES.values()
        for alias in values
    }
    if set(payload) & (aliases | CHROME_COMPATIBILITY_ROOT_FIELDS):
        return
    for spec in CHROME_CANONICAL_FIELD_SPECS:
        name = str(spec["name"])
        required = bool(spec.get("required"))
        required_when = spec.get("required_when")
        if isinstance(required_when, Mapping):
            required = required or all(payload.get(key) == value for key, value in required_when.items())
        if name not in payload:
            if required:
                raise ContentIntakeError(f"chrome_canonical_required_field_missing:{name}")
            continue
        value = payload[name]
        accepted = spec.get("json_types", ())
        if isinstance(accepted, tuple) and not _json_type_matches(value, accepted):
            raise ContentIntakeError(f"chrome_canonical_field_type_invalid:{name}")
        const = spec.get("const")
        if const is not None and value != const:
            raise ContentIntakeError(f"chrome_canonical_field_const_invalid:{name}")
        allowed = spec.get("enum")
        if isinstance(allowed, tuple) and value not in allowed:
            raise ContentIntakeError(f"chrome_canonical_field_enum_invalid:{name}")
        if required and not bool(spec.get("allow_empty")) and _value_is_empty(value):
            raise ContentIntakeError(f"chrome_canonical_required_field_empty:{name}")
        empty_when = spec.get("empty_when")
        if isinstance(empty_when, Mapping) and all(payload.get(key) == expected for key, expected in empty_when.items()):
            if not _value_is_empty(value):
                raise ContentIntakeError(f"chrome_canonical_field_must_be_empty:{name}")


def validate_single_chrome_result(payload: Mapping[str, object]) -> None:
    """Reject drifted single-item Chrome objects before parsing them."""
    if not isinstance(payload, Mapping):
        raise ContentIntakeError("chrome_single_result_object_required")
    if str(payload.get("data_source") or payload.get("基础数据获取方式") or "").strip() == "chrome_devtools":
        unknown = sorted(set(payload) - SINGLE_CHROME_ROOT_FIELDS)
        if unknown:
            raise ContentIntakeError(f"chrome_single_unknown_root_fields:{unknown}")
        version = payload.get("schema_version")
        if version is not None and version != SINGLE_CHROME_SCHEMA_VERSION:
            raise ContentIntakeError("chrome_single_schema_version_invalid")
        nested = payload.get("互动数据") or payload.get("stats")
        if nested is not None and not isinstance(nested, Mapping):
            raise ContentIntakeError("chrome_single_interactions_invalid")
        # 正文获取状态来自代码侧正文证据推导；即使旧调用方携带该字段，
        # 也不把它作为 Chrome 提交的阻塞校验项。
        validate_canonical_chrome_output(payload)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="查询、准备并原子写入单条对标内容")
    parser.add_argument("--phase", required=True, choices=("chrome-canonical-contract", "resolve-source", "start", "inspect", "prepare", "process", "write-asr-result", "resume", "commit", "local-start", "local-prepare", "local-write-transcript", "local-asr", "local-commit"))
    parser.add_argument("--workspace-root", required=True)
    parser.add_argument("--source-url", default="")
    parser.add_argument("--selected-source", default="")
    parser.add_argument("--source-json", default="")
    parser.add_argument("--raw-detail-json", default="", help="本轮 staging 中的原始平台详情，仅绑定路径和哈希")
    parser.add_argument("--prepare-json", default="")
    parser.add_argument("--transcript-json", default="", help="本轮暂存中的正文证据输入")
    parser.add_argument("--cover-result-json", default="")
    parser.add_argument("--transcript-result-json", default="")
    parser.add_argument("--process-json", default="", help="process 阶段回执")
    parser.add_argument("--asr-envelope-json", default="", help="宿主传入的 transcribe_video_batch envelope")
    parser.add_argument("--asr-provider", default="")
    parser.add_argument("--staged-cover-path", default="")
    parser.add_argument("--staged-transcript-path", default="")
    parser.add_argument("--staged-summary-path", default="")
    parser.add_argument("--staged-comments-path", default="")
    parser.add_argument("--output-json", default="")
    return parser.parse_args()


def resolve_under(root: Path, raw_path: str, *, required_prefix: str = "", label: str = "") -> Path:
    root = root.resolve()
    text = str(raw_path or "").strip().replace("\\", "/")
    if not text:
        # label 由调用方给出参数名，便于直接看出是哪个入参缺失；未提供时保持旧错误码。
        raise ContentIntakeError(f"{label}_path_missing" if label else "path_missing")
    logical = PurePosixPath(text)
    candidate = Path(text).expanduser()
    resolved = candidate.resolve() if candidate.is_absolute() else root.joinpath(*logical.parts).resolve()
    try:
        relative = resolved.relative_to(root)
    except ValueError as exc:
        raise ContentIntakeError(f"path_outside_workspace:{text}") from exc
    if required_prefix and not relative.as_posix().startswith(required_prefix):
        raise ContentIntakeError(f"path_outside_required_prefix:{text}:{required_prefix}")
    return resolved


def relative_path(root: Path, path: Path) -> str:
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError as exc:
        raise ContentIntakeError(f"path_outside_workspace:{path}") from exc


def atomic_write(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".stage", dir=str(path.parent)
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


@contextmanager
def content_library_write_lock(library_path: Path):
    """Serialize the short read/merge/write window for the content library."""
    lock_id = hashlib.sha256(str(library_path.resolve()).encode("utf-8")).hexdigest()
    lock_path = Path(tempfile.gettempdir()) / f"meiling-content-library-{lock_id}.lock"
    handle = lock_path.open("a+b")
    backend = ""
    try:
        if fcntl is not None:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            backend = "fcntl"
        elif msvcrt is not None:
            handle.seek(0, os.SEEK_END)
            if handle.tell() == 0:
                handle.write(b"\0")
                handle.flush()
                os.fsync(handle.fileno())
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK, 1)
            backend = "msvcrt"
        else:  # pragma: no cover - supported desktop platforms provide one backend.
            raise ContentIntakeError("file_lock_backend_unavailable")
        yield
    finally:
        if backend == "fcntl":
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        elif backend == "msvcrt":
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        handle.close()


def write_json_atomically(path: Path, payload: Mapping[str, object]) -> None:
    encoded = (json.dumps(payload, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    atomic_write(path, encoded)


def read_json(path: Path) -> dict[str, object]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ContentIntakeError(f"json_read_failed:{path}:{exc}") from exc
    if not isinstance(payload, dict):
        raise ContentIntakeError(f"json_object_required:{path}")
    return payload


def validate_raw_detail_candidate(payload: bytes, *, expected_native_content_id: str) -> tuple[str, str]:
    """Classify a staged XHS raw response without executing page code."""
    try:
        text = payload.decode("utf-8-sig")
    except UnicodeDecodeError:
        return "raw_detail_unavailable", "原始详情不是 UTF-8 JSON"
    marker = text.lstrip().casefold()
    if not text.strip() or marker.startswith(("<!doctype", "<html", "<script")) or "window.__initial_state__" in marker:
        return "raw_detail_unavailable", "原始详情是 HTML 或为空"
    try:
        decoded = json.loads(text)
    except json.JSONDecodeError:
        return "raw_detail_unavailable", "原始详情不是合法 JSON"
    if not isinstance(decoded, dict):
        return "raw_detail_unavailable", "原始详情根节点必须是 JSON 对象"
    note_node = None
    for candidate in (decoded.get("note"), decoded.get("data"), decoded):
        if not isinstance(candidate, dict):
            continue
        nested = candidate.get("note")
        if isinstance(nested, dict):
            note_node = nested
            break
        if any(key in candidate for key in ("noteId", "note_id", "currentNoteId")):
            note_node = candidate
            break
    if note_node is None:
        return "raw_detail_unavailable", "原始详情结构不是已知小红书 note 对象"
    observed = ""
    for node in (decoded, note_node):
        if not isinstance(node, dict):
            continue
        for key in ("currentNoteId", "noteId", "note_id", "id"):
            if str(node.get(key) or "").strip():
                observed = str(node[key]).strip()
                break
        if observed:
            break
    if not observed or observed != str(expected_native_content_id or "").strip():
        return "raw_detail_unavailable", "原始详情内容 ID 与目标不一致"
    return "", ""


def pick(payload: Mapping[str, object], *keys: str) -> str:
    for key in keys:
        value = payload.get(key)
        if value is not None and str(value).strip():
            return str(value).strip()
    return ""


def normalize_publish_time(value: object) -> str:
    """Normalize Chrome/API seconds or milliseconds to the shared display format."""
    text = str(value or "").strip()
    if not text:
        return ""
    try:
        numeric = float(text)
    except ValueError:
        numeric = None
    if numeric is not None:
        if abs(numeric) >= 10_000_000_000:
            numeric /= 1000
        try:
            return datetime.fromtimestamp(numeric).astimezone().strftime("%Y/%m/%d %H:%M")
        except (OverflowError, OSError, ValueError):
            return ""
    normalized = text.replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(normalized)
        if parsed.tzinfo is None:
            parsed = parsed.astimezone()
        return parsed.astimezone().strftime("%Y/%m/%d %H:%M")
    except ValueError:
        pass
    for pattern in ("%Y/%m/%d %H:%M:%S", "%Y/%m/%d %H:%M", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"):
        try:
            return datetime.strptime(text, pattern).strftime("%Y/%m/%d %H:%M")
        except ValueError:
            continue
    return ""


def normalize_media_type(value: object) -> str:
    marker = re.sub(r"[\s-]+", "_", str(value or "").strip().lower())
    if marker in {"video", "video_note", "movie", "视频"}:
        return "视频"
    if marker in {"normal", "image", "images", "text", "note", "图文"}:
        return "图文"
    if marker in {"unknown", "未知"}:
        return "未知"
    if marker:
        return "其他"
    return ""


def normalize_platform(value: str, url: str = "") -> str:
    text = value.strip().lower()
    if text in {"小红书", "xiaohongshu", "xhs"}:
        return "小红书"
    if text in {"抖音", "douyin", "tiktok-cn"}:
        return "抖音"
    if text in {"b站", "bilibili", "哔哩哔哩"}:
        return "B站"
    host = urlsplit(url).netloc.lower()
    if "xiaohongshu.com" in host or "xhslink.com" in host:
        return "小红书"
    if "douyin.com" in host:
        return "抖音"
    if "bilibili.com" in host or "b23.tv" in host:
        return "B站"
    return value.strip()


def normalize_url(raw_url: str) -> str:
    text = str(raw_url or "").strip()
    if not text:
        return ""
    if not re.match(r"^https?://", text, flags=re.IGNORECASE):
        return ""
    raw_host = (urlsplit(text).hostname or "").lower().rstrip(".")
    if raw_host in {"douyin.com", "www.douyin.com", "iesdouyin.com", "www.iesdouyin.com", "v.douyin.com"}:
        canonical = canonicalize_douyin_content_url(text, resolve_short=False)
        if canonical.get("content_id"):
            return str(canonical["canonical_url"])
    parts = urlsplit(text)
    host = parts.netloc.lower()
    if host.startswith("www."):
        host = host[4:]
    path = re.sub(r"/{2,}", "/", parts.path or "/")
    if path != "/":
        path = path.rstrip("/")
    query_items = []
    for key, value in parse_qsl(parts.query, keep_blank_values=False):
        lowered = key.lower()
        if lowered in IDENTITY_QUERY_KEYS:
            query_items.append((lowered, value))
        elif host == "xiaohongshu.com" and lowered in XIAOHONGSHU_PUBLIC_QUERY_KEYS:
            query_items.append((lowered, value))
        elif lowered not in TRACKING_QUERY_KEYS and host in {"v.douyin.com", "b23.tv", "xhslink.com"}:
            query_items.append((lowered, value))
    query = urlencode(sorted(query_items))
    return urlunsplit(("https", host, path, query, ""))


def native_identity(platform: str, payload: Mapping[str, object], url: str) -> str:
    if platform == "B站":
        nested = payload.get("B站视频标识")
        bvid = pick(nested, "bvid") if isinstance(nested, Mapping) else ""
        bvid = bvid or pick(payload, "bvid", "BVID")
        match = re.search(r"/(BV[0-9A-Za-z]+)", url, flags=re.IGNORECASE)
        return (bvid or (match.group(1) if match else "")).upper()
    if platform == "抖音":
        parsed_id = extract_douyin_content_id(url, resolve_short=False)
        direct = pick(payload, "aweme_id", "item_id", "内容原生ID", "平台原生内容ID")
        return parsed_id or direct
    if platform == "小红书":
        direct = pick(payload, "note_id", "item_id", "内容原生ID", "平台原生内容ID")
        match = re.search(r"/(?:explore|discovery/item)/([0-9A-Za-z_-]+)", url)
        query = dict(parse_qsl(urlsplit(url).query))
        return direct or (match.group(1) if match else "") or query.get("item_id", "")
    return ""


def _bilibili_signed_video_url(url: str) -> bool:
    """Validate a complete, signed B站 DASH video URL without rewriting it."""
    parsed = urlsplit(str(url or "").strip())
    if parsed.scheme.lower() not in {"http", "https"} or not parsed.netloc:
        return False
    if not parsed.query or not parsed.path.lower().endswith(".m4s"):
        return False
    query = {key.lower(): value for key, value in parse_qsl(parsed.query, keep_blank_values=True)}
    if not any(query.get(key) for key in ("deadline", "upsig", "sign")):
        return False
    deadline = query.get("deadline")
    if deadline:
        try:
            if float(deadline) <= time.time():
                return False
        except ValueError:
            return False
    return True


def _bilibili_video_url_matches_identity(url: str, aid: str, cid: str) -> bool:
    parsed = urlsplit(str(url or ""))
    haystack = f"{parsed.path}?{parsed.query}".lower()
    identities = [str(value or "").strip().lower() for value in (cid, aid) if str(value or "").strip()]
    return bool(identities) and any(identity in haystack for identity in identities)


def row_url(row: Mapping[str, str]) -> str:
    return (row.get("对外页面链接") or row.get("内容链接") or row.get("笔记链接") or "").strip()


def find_matches(
    rows: list[dict[str, str]],
    *,
    source_url: str,
    data: Mapping[str, object] | None = None,
) -> list[dict[str, str]]:
    urls = {value for value in (normalize_url(source_url), normalize_url(pick(data or {}, "对外页面链接", "url"))) if value}
    platform = normalize_platform(pick(data or {}, "平台", "platform"), source_url)
    token = native_identity(platform, data or {}, pick(data or {}, "对外页面链接", "url") or source_url)
    exact: list[dict[str, str]] = []
    for row in rows:
        normalized_existing = normalize_url(row_url(row))
        existing_platform = normalize_platform(row.get("平台", ""), row_url(row))
        existing_token = native_identity(existing_platform, row, row_url(row))
        if urls and normalized_existing in urls:
            exact.append(row)
        elif token and existing_platform == platform and existing_token == token:
            exact.append(row)
    if exact:
        return list({id(row): row for row in exact}.values())
    if data is None:
        return []
    account_id = pick(data, "账号ID", "account_id")
    title = pick(data, "标题", "title")
    if not (platform and account_id and title):
        return []
    fallback = [
        row for row in rows
        if normalize_platform(row.get("平台", "")) == platform
        and row.get("账号ID", "").strip() == account_id
        and row.get("标题", "").strip() == title
    ]
    return fallback


def row_sha256(row: Mapping[str, str]) -> str:
    canonical = {str(key): str(value or "") for key, value in row.items()}
    encoded = json.dumps(canonical, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def deterministic_concurrent_target_merge(
    row: Mapping[str, str], source: Mapping[str, str]
) -> bool:
    """Accept a newly appeared target only when its stable identity agrees."""
    for field in ("平台", "账号ID", "标题", "媒介形式", "对外页面链接"):
        current = str(row.get(field) or "").strip()
        expected = str(source.get(field) or "").strip()
        if current and expected and current != expected:
            return False
    return True


def infer_content_dir(root: Path, row: Mapping[str, str]) -> Path | None:
    for field in (
        "内容目录路径", "视频文案路径", "内容分析路径",
        "封面图路径",
    ):
        value = str(row.get(field) or "").strip()
        if not value:
            continue
        path = resolve_under(root, value)
        return path if field == "内容目录路径" else path.parent
    return None


def readable_file(root: Path, raw_path: str) -> bool:
    if not str(raw_path or "").strip():
        return False
    try:
        path = resolve_under(root, raw_path)
        return path.is_file() and path.stat().st_size > 0
    except (OSError, ContentIntakeError):
        return False


def analysis_handoff(root: Path, row: Mapping[str, str]) -> dict[str, object]:
    content_dir_path = infer_content_dir(root, row)
    content_dir = relative_path(root, content_dir_path) if content_dir_path else ""
    missing_identity = [field for field in ("内容ID", "平台", "标题", "媒介形式") if not str(row.get(field) or "").strip()]
    if not content_dir:
        missing_identity.append("内容目录路径")
    media_type = str(row.get("媒介形式") or "").strip()
    transcript_ready = readable_file(root, str(row.get("视频文案路径") or ""))
    # 图文内容没有视频文案是正常状态；只有视频内容缺少正文时才阻断分析交接。
    body_ready = media_type == "图文" or transcript_ready
    status = "ready" if not missing_identity and body_ready else "blocked"
    return {
        "status": status,
        "content_id": str(row.get("内容ID") or ""),
        "platform": str(row.get("平台") or ""),
        "title": str(row.get("标题") or ""),
        "media_type": media_type,
        "content_dir": content_dir,
        "transcript_path": str(row.get("视频文案路径") or ""),
        "missing_identity_fields": missing_identity,
        "blocking_reason": "" if status == "ready" else (
            "视频正文不可用或内容身份不完整" if media_type == "视频" else "内容身份不完整"
        ),
    }


def validate_library(fields: list[str]) -> None:
    missing = sorted(REQUIRED_CONTENT_FIELDS - set(fields))
    if missing:
        raise ContentIntakeError(f"content_library_missing_fields:{missing}")


def sanitize_component(value: str, fallback: str) -> str:
    text = re.sub(r"[\\/:*?\"<>|\x00-\x1f]", "-", str(value or "").strip())
    text = re.sub(r"\s+", " ", text).strip(" .-")
    return (text or fallback)[:80]


def account_list_content_id(root: Path, source: Mapping[str, str]) -> str:
    platform = source.get("平台", "").strip()
    account_id = source.get("账号ID", "").strip()
    if not platform or not account_id:
        return ""
    account_path = root / ACCOUNT_LIBRARY
    if not account_path.is_file():
        return ""
    fields, account_rows, _ = read_csv_table_with_snapshot(account_path)
    missing = sorted({"平台", "账号ID", "内容列表路径"} - set(fields))
    if missing:
        raise ContentIntakeError(f"account_library_missing_fields:{missing}")
    accounts = [
        row for row in account_rows
        if row.get("平台", "").strip() == platform
        and row.get("账号ID", "").strip() == account_id
    ]
    if not accounts:
        return ""
    if len(accounts) != 1:
        raise ContentIntakeError(f"account_match_count_for_content_id:{len(accounts)}")
    list_path = resolve_under(root, accounts[0].get("内容列表路径", ""))
    if not list_path.is_file():
        return ""
    list_fields, list_rows, _ = read_csv_table_with_snapshot(list_path)
    if "内容ID" not in list_fields:
        raise ContentIntakeError("account_content_list_missing_field:内容ID")
    matches = find_matches(list_rows, source_url=source["对外页面链接"], data=source)
    if len(matches) > 1:
        raise ContentIntakeError(f"account_content_match_count:{len(matches)}")
    if not matches:
        return ""
    content_id = matches[0].get("内容ID", "").strip()
    if not re.fullmatch(r"内容[0-9]+", content_id):
        raise ContentIntakeError("account_content_id_invalid")
    return content_id


def parse_source(payload: Mapping[str, object]) -> dict[str, str]:
    validate_single_chrome_result(payload)
    nested = payload.get("data")
    source = nested if isinstance(nested, Mapping) else payload
    if isinstance(payload.get("fields"), Mapping):
        source = {**payload, **dict(payload["fields"])}
    if payload.get("content_id") and "内容ID" not in source:
        source = {**source, "内容ID": payload.get("content_id")}
    if payload.get("content_url") and "对外页面链接" not in source:
        source = {**source, "对外页面链接": payload.get("content_url")}
    state = pick(source, "状态", "status")
    if state.lower() not in {"成功", "success", "ok"}:
        raise ContentIntakeError(f"source_failed:{pick(source, '失败原因', 'error', 'message')}")
    url = pick(source, "对外页面链接", "url", "source_url")
    platform = normalize_platform(pick(source, "平台", "platform"), url)
    bstation = source.get("B站视频标识")
    identifiers = bstation if isinstance(bstation, Mapping) else {}
    interactions = source.get("互动数据") if isinstance(source.get("互动数据"), Mapping) else source.get("stats") if isinstance(source.get("stats"), Mapping) else source.get("interactions") if isinstance(source.get("interactions"), Mapping) else source
    result = {
        "平台": platform,
        "平台原生内容ID": pick(
            source, "平台原生内容ID", "内容原生ID", "aweme_id", "note_id", "item_id"
        ),
        "账号ID": pick(source, "账号ID", "account_id", "author_id", "uid"),
        "账号名": pick(source, "账号名", "account_name", "author_name", "nickname"),
        "标题": pick(source, "标题", "title", "desc"),
        "封面图原链接": pick(source, "封面图原链接", "cover_url"),
        "视频下载链接": pick(source, "视频下载链接", "video_url"),
        "媒介形式": normalize_media_type(
            pick(source, "媒介形式", "media_form", "media_type", "type")
        ),
        "点赞数": pick(interactions, "点赞数", "likes", "like_count"),
        "收藏数": pick(interactions, "收藏数", "collects", "collect_count"),
        "分享数": pick(interactions, "分享数", "shares", "share_count"),
        "评论数": pick(interactions, "评论数", "comments", "comment_count"),
        "发布时间": normalize_publish_time(
            pick(source, "发布时间", "published_at", "publish_time", "create_time", "time", "timestamp")
        ),
        "对外页面链接": url,
        "基础数据获取方式": pick(source, "data_source", "基础数据获取方式", "fetch_method", "source"),
        "bvid": pick(identifiers, "bvid") or pick(source, "bvid"),
        "aid": pick(identifiers, "aid") or pick(source, "aid"),
        "cid": pick(identifiers, "cid") or pick(source, "cid"),
        "正文文本": pick(source, "正文文本", "字幕文本", "caption_text", "transcript", "text"),
        # Chrome candidate 不再接收模型填写的派生状态；单条视频链路
        # 只根据页面正文是否真实存在生成内部默认值，后续字幕/ASR
        # 状态由对应证据选择器确定。
        "正文获取状态": "已获取" if pick(source, "正文文本", "字幕文本", "caption_text", "transcript", "text") else "不适用",
    }
    if platform not in SUPPORTED_PLATFORMS:
        raise ContentIntakeError(f"unsupported_platform:{platform or 'missing'}")
    for field in ("标题", "媒介形式", "对外页面链接"):
        if not result[field]:
            raise ContentIntakeError(f"required_source_field_missing:{field}")
    if not normalize_url(result["对外页面链接"]):
        raise ContentIntakeError("source_url_invalid")
    if not native_identity(platform, result, result["对外页面链接"]):
        raise ContentIntakeError("stable_native_identity_missing")
    # Chrome candidates must prove that the captured stream belongs to the
    # current cid/aid. API responses already bind the URL to their detail
    # payload and are checked by the JustOneAPI adapter itself.
    if (
        platform == "B站"
        and result["媒介形式"] == "视频"
        and result["基础数据获取方式"] == "chrome_devtools"
    ):
        if not _bilibili_signed_video_url(result["视频下载链接"]):
            raise ContentIntakeError("bilibili_video_url_invalid")
        if not _bilibili_video_url_matches_identity(
            result["视频下载链接"], result["aid"], result["cid"]
        ):
            raise ContentIntakeError("bilibili_video_url_identity_mismatch")
    return result


def compute_data_level(platform: str, core: int) -> str:
    """按平台的绝对阈值给「数据水平」（大爆款／小爆款／优秀／正常／差）。

    ⚠️ **同一份逻辑在 `账号衍生字段计算批处理.py` 里还有一份**（那边写成 if 链，
    阈值完全相同）。**改阈值时两处必须一起改。**

    没有合并成一份的原因：两边对「平台不在支持的三种里」的反应不同（这边是
    `KeyError`，那边 `raise ValueError`），合并会改变异常类型，对调用方是行为变更，
    风险大于收益。
    """
    thresholds = {
        "小红书": ((10000, "大爆款"), (3000, "小爆款"), (1000, "优秀"), (100, "正常")),
        "抖音": ((30000, "大爆款"), (10000, "小爆款"), (3000, "优秀"), (500, "正常")),
        "B站": ((5000, "大爆款"), (2000, "小爆款"), (800, "优秀"), (100, "正常")),
    }
    for minimum, label in thresholds[platform]:
        if core >= minimum:
            return label
    return "差"


def _parse_publish_datetime(value: str) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    candidates = [text, text.replace("/", "-"), text.replace("Z", "+00:00")]
    candidates.append(candidates[1].replace("Z", "+00:00"))
    for candidate in candidates:
        candidate = re.sub(r"([+-]\d{2})(\d{2})$", r"\1:\2", candidate)
        try:
            return datetime.fromisoformat(candidate)
        except ValueError:
            continue
    for pattern in (
        "%Y/%m/%d %H:%M:%S", "%Y/%m/%d %H:%M",
        "%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M",
        "%Y年%m月%d日 %H时%M分%S秒", "%Y年%m月%d日 %H时%M分",
        "%Y年%m月%d日 %H:%M:%S", "%Y年%m月%d日 %H:%M",
    ):
        try:
            return datetime.strptime(text, pattern)
        except ValueError:
            continue
    return None


def time_slot(value: str) -> str:
    parsed = _parse_publish_datetime(value)
    if parsed is None:
        return ""
    return f"{parsed.hour:02d}:00-{(parsed.hour + 1) % 24:02d}:00"


def initialize_run(root: Path, source_url: str) -> dict[str, object]:
    normalized = normalize_url(source_url)
    platform = normalize_platform("", normalized)
    if not normalized or platform not in SUPPORTED_PLATFORMS:
        raise ContentIntakeError("source_url_invalid")
    run_id = f"{datetime.now().astimezone().strftime('%Y%m%dT%H%M%S')}-{uuid.uuid4().hex[:10]}"
    run_dir = root / ".meiling/staging/benchmark-content-base" / run_id
    run_dir.mkdir(parents=True, exist_ok=False)
    receipt_path = run_dir / "运行准备结果.json"
    result = {
        "ok": True,
        "status": "started",
        "run_id": run_id,
        "run_dir": relative_path(root, run_dir),
        "source_url": source_url.strip(),
        "normalized_url": normalized,
        "platform": platform,
        "start_receipt": relative_path(root, receipt_path),
    }
    write_json_atomically(receipt_path, result)
    return result


def inspect(root: Path, source_url: str) -> dict[str, object]:
    normalized = normalize_url(source_url)
    if not normalized:
        raise ContentIntakeError("source_url_invalid")
    fields, rows, _ = read_csv_table_with_snapshot(root / CONTENT_LIBRARY)
    validate_library(fields)
    matches = find_matches(rows, source_url=source_url)
    if len(matches) > 1:
        return {
            "ok": True,
            "status": "duplicate_conflict",
            "normalized_url": normalized,
            "match_count": len(matches),
            "existing_record": None,
            "analysis_handoff": {"status": "blocked", "blocking_reason": "内容库存在多条匹配记录"},
        }
    if not matches:
        return {
            "ok": True,
            "status": "not_found",
            "normalized_url": normalized,
            "match_count": 0,
            "existing_record": None,
            "analysis_handoff": {"status": "blocked", "blocking_reason": "内容尚未建档"},
        }

    row = matches[0]
    content_dir = infer_content_dir(root, row)
    analysis_path = content_dir / "内容分析.md" if content_dir else None
    has_formal_analysis = bool(
        analysis_path and analysis_path.is_file() and analysis_path.stat().st_size > 0
    )
    content_id = row.get("内容ID", "").strip()
    account_id = row.get("账号ID", "").strip()
    assets = {
        "transcript": readable_file(root, row.get("视频文案路径", "")),
        "cover": readable_file(root, row.get("封面图路径", "")),
        "comments": readable_file(root, row.get("评论路径", "")),
    }
    return {
        "ok": True,
        "status": "analyzed" if has_formal_analysis else "matched",
        "normalized_url": normalized,
        "match_count": 1,
        "content_id": content_id,
        "account_id": account_id,
        "title": row.get("标题", "").strip(),
        "platform": row.get("平台", "").strip(),
        "content_dir": relative_path(root, content_dir) if content_dir else "",
        "has_formal_analysis": has_formal_analysis,
        "formal_analysis_path": relative_path(root, analysis_path) if has_formal_analysis else "",
        "ready_for_analysis": bool(assets["transcript"]),
        "assets": assets,
        "detail_link": (
            f"content?kind=benchmark&id={account_id}::{content_id}"
            if content_id and account_id
            else ""
        ),
        "existing_record": row,
        "analysis_handoff": analysis_handoff(root, row),
    }


def _latest_asset_time(root: Path, row: Mapping[str, str]) -> str:
    """已建档内容最近一次资产写入时间，用来提示「本篇此前已经抓过」。"""
    content_dir = infer_content_dir(root, row)
    if content_dir is None or not content_dir.is_dir():
        return ""
    stamps: list[float] = []
    for path in content_dir.rglob("*"):
        try:
            if path.is_file():
                stamps.append(path.stat().st_mtime)
        except OSError:
            continue
    if not stamps:
        return ""
    return datetime.fromtimestamp(max(stamps)).astimezone().isoformat(timespec="seconds")


def _write_run_identity(run_dir: Path, identity: Mapping[str, object]) -> None:
    """把本轮 run 的身份写进暂存目录，供并发判断直接读取。

    暂存目录里原本只有「内容基础结构化数据-*.json」这类文件名，执行者想判断
    另一个 run 是不是同一条内容，只能靠目录名猜（实测猜错过）。落一份可读身份，
    不必再猜。
    """
    write_json_atomically(run_dir / "运行身份.json", identity)


def prepare(root: Path, source_path: Path, raw_detail_json: str = "") -> dict[str, object]:
    source_payload = source_path.read_bytes()
    source = parse_source(read_json(source_path))
    library_path = root / CONTENT_LIBRARY
    fields, rows, _ = read_csv_table_with_snapshot(library_path)
    validate_library(fields)
    matches = find_matches(rows, source_url=source["对外页面链接"], data=source)
    if len(matches) > 1:
        raise ContentIntakeError(f"content_match_count:{len(matches)}")
    existing = matches[0] if matches else None
    content_id_origin = "existing"
    if existing:
        content_id = existing.get("内容ID", "").strip()
    else:
        content_id = account_list_content_id(root, source)
        if content_id:
            content_id_origin = "account_list"
        else:
            content_id_origin = "global"
            content_id = next_global_content_id(
                root,
                additional_ids=[row.get("内容ID", "") for row in rows],
            )
    if not content_id:
        raise ContentIntakeError("existing_content_id_missing")
    content_dir = infer_content_dir(root, existing) if existing else None
    if content_dir is None:
        directory_name = sanitize_component(
            f"{source['账号名']}-{source['标题'][:10]}", content_id
        )
        content_dir = (root / "对标资料" / "对标内容" / directory_name).resolve()
        if content_dir.exists() and not existing:
            content_dir = content_dir.with_name(f"{content_dir.name}-{content_id}")
    relative_dir = relative_path(root, content_dir)
    expected_transcript_path = relative_path(root, content_dir / "视频文案.md")
    run_dir = source_path.parent.resolve()
    run_id = run_dir.name
    if not run_id or run_id in {".", ".."}:
        raise ContentIntakeError("run_id_missing")
    raw_detail_path = ""
    raw_detail_sha256 = ""
    raw_detail_status = ""
    raw_detail_code = ""
    raw_detail_failure_reason = ""
    if str(raw_detail_json or "").strip():
        raw_path = resolve_under(root, raw_detail_json, required_prefix=".meiling/staging/")
        if raw_path.parent != run_dir or raw_path.name != "current-note-detail.json":
            raise ContentIntakeError("raw_detail_path_not_in_current_run")
        raw_detail_path = relative_path(root, raw_path)
        raw_payload = raw_path.read_bytes()
        raw_detail_sha256 = hashlib.sha256(raw_payload).hexdigest()
        if source["平台"] == "小红书":
            raw_detail_code, raw_detail_failure_reason = validate_raw_detail_candidate(
                raw_payload,
                expected_native_content_id=native_identity(
                    source["平台"], source, source["对外页面链接"]
                ),
            )
            raw_detail_status = "unavailable" if raw_detail_code else "available"
    record_mode = "update" if existing else "create"
    native_content_id = native_identity(
        source["平台"], source, source["对外页面链接"]
    )
    normalized_url = normalize_url(source["对外页面链接"])
    existing_source = ""
    existing_assets_updated_at = ""
    if existing is not None:
        existing_source = str(existing.get("基础数据获取方式") or "").strip()
        existing_assets_updated_at = _latest_asset_time(root, existing)
    _write_run_identity(
        run_dir,
        {
            "schema_version": "1",
            "run_id": run_id,
            "content_id": content_id,
            "平台": source["平台"],
            "平台原生内容ID": native_content_id,
            "标题": source["标题"],
            "媒介形式": source["媒介形式"],
            "记录模式": record_mode,
            "取数方式": str(source.get("基础数据获取方式") or source.get("data_source") or "").strip(),
            "对外页面链接": normalized_url,
            "写入时间": datetime.now().astimezone().isoformat(timespec="seconds"),
        },
    )
    return {
        "ok": True,
        "status": "prepared",
        "record_mode": record_mode,
        "content_id": content_id,
        "run_id": run_id,
        "platform": source["平台"],
        "content_dir": relative_dir,
        "content_id_origin": content_id_origin,
        "target_row_sha256": row_sha256(existing) if existing else "",
        "source_url": source["对外页面链接"],
        "source_sha256": hashlib.sha256(source_payload).hexdigest(),
        "raw_detail_path": raw_detail_path,
        "raw_detail_sha256": raw_detail_sha256,
        "raw_detail_status": raw_detail_status or ("available" if raw_detail_path else ""),
        "raw_detail_code": raw_detail_code,
        "raw_detail_failure_reason": raw_detail_failure_reason,
        "normalized_url": normalized_url,
        "native_content_id": native_content_id,
        "existing_record_source": existing_source,
        "existing_assets_updated_at": existing_assets_updated_at,
        "expected_transcript_path": expected_transcript_path,
        "expected_cover_result_path": relative_path(root, run_dir / "封面图获取结果.json"),
        "expected_transcript_result_path": relative_path(root, run_dir / "正文获取结果.json"),
        "expected_staged_cover_path": relative_path(root, run_dir / "待提交封面图.png"),
        "expected_staged_transcript_path": relative_path(root, run_dir / "待提交正文.md"),
    }


def _asr_url_hash(video_url: str) -> str:
    return hashlib.sha256(str(video_url or "").strip().encode("utf-8")).hexdigest()


def _load_process_context(root: Path, source_path: Path, prepare_path: Path) -> tuple[dict[str, object], dict[str, str], Path, str]:
    prepared = read_json(prepare_path)
    if prepared.get("status") != "prepared":
        raise ContentIntakeError("prepare_status_invalid")
    source = parse_source(read_json(source_path))
    if hashlib.sha256(source_path.read_bytes()).hexdigest() != pick(prepared, "source_sha256"):
        raise ContentIntakeError("prepare_source_sha256_mismatch")
    content_id = pick(prepared, "content_id")
    if not content_id:
        raise ContentIntakeError("prepare_content_id_missing")
    run_dir = source_path.parent.resolve()
    run_id = pick(prepared, "run_id") or run_dir.name
    if run_id != run_dir.name:
        raise ContentIntakeError("prepare_run_id_mismatch")
    return prepared, source, run_dir, run_id


def _workspace_id(root: Path) -> str:
    identity = root / ".meiling" / "workspace.json"
    if not identity.is_file():
        return ""
    try:
        payload = json.loads(identity.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return ""
    return str(payload.get("workspace_id") or "").strip() if isinstance(payload, Mapping) else ""


def _contains_sensitive(value: object, path: str = "") -> bool:
    sensitive = {"ticket", "asr_ticket", "token", "access_token", "api_key", "apikey", "key"}
    if isinstance(value, Mapping):
        for key, nested in value.items():
            if str(key).lower() in sensitive:
                return True
            if _contains_sensitive(nested, f"{path}.{key}"):
                return True
    elif isinstance(value, list):
        return any(_contains_sensitive(item, path) for item in value)
    return False


def _normalize_asr_envelope(envelope: object) -> dict[str, object]:
    candidate = envelope
    if isinstance(envelope, Mapping) and isinstance(envelope.get("structuredContent"), Mapping):
        candidate = envelope["structuredContent"]
    elif isinstance(envelope, Mapping) and isinstance(envelope.get("content"), list):
        texts = [item.get("text") for item in envelope["content"] if isinstance(item, Mapping) and isinstance(item.get("text"), str)]
        if len(texts) != 1:
            raise ContentIntakeError("asr_envelope_not_normalized")
        try:
            candidate = json.loads(texts[0])
        except json.JSONDecodeError as exc:
            raise ContentIntakeError("asr_envelope_not_normalized") from exc
    if not isinstance(candidate, Mapping) or "structuredContent" in candidate or "content" in candidate:
        raise ContentIntakeError("asr_result_not_normalized")
    normalized = dict(candidate)
    if _contains_sensitive(normalized):
        raise ContentIntakeError("asr_result_contains_sensitive_fields")
    return normalized


def process(root: Path, source_path: Path, prepare_path: Path) -> dict[str, object]:
    prepared, source, run_dir, run_id = _load_process_context(root, source_path, prepare_path)
    content_id = pick(prepared, "content_id")
    video_url = pick(source, "视频下载链接", "video_url")
    body_text = pick(source, "正文文本", "字幕文本", "caption_text", "transcript", "text")
    media_type = pick(source, "媒介形式", "media_type")
    caption_evidence = run_dir / "platform-caption-evidence.json"
    if caption_evidence.is_file():
        evidence = read_json(caption_evidence)
        caption_fact = platform_caption_evidence(
            evidence,
            content_id=content_id,
            run_id=run_id,
            video_url=video_url,
        )
        if caption_fact.get("status") == "success" and str(caption_fact.get("text") or "").strip():
            return {
                "ok": True, "status": "transcript_ready", "run_id": run_id,
                "content_id": content_id, "source": "platform_caption",
                "evidence_path": relative_path(root, caption_evidence),
                "result_path": relative_path(root, run_dir / "正文获取结果.json"),
            }
    elif source.get("平台") == "B站":
        # B站 的字幕不在 Chrome 页面结构化状态里，单独取；取不到继续往下走 ASR。
        evidence = materialize_bilibili_platform_caption(
            workspace_root=root,
            content_id=content_id,
            run_id=run_id,
            video_url=video_url,
            note_url=pick(source, "对外页面链接", "平台原生内容ID"),
            evidence_json=relative_path(root, caption_evidence),
        )
        if evidence.get("status") == "success" and str(evidence.get("text") or "").strip():
            return {
                "ok": True, "status": "transcript_ready", "run_id": run_id,
                "content_id": content_id, "source": "platform_caption",
                "evidence_path": relative_path(root, caption_evidence),
                "result_path": relative_path(root, run_dir / "正文获取结果.json"),
            }
    elif source.get("平台") == "小红书" and pick(prepared, "raw_detail_path"):
        evidence = materialize_xiaohongshu_platform_caption(
            workspace_root=root,
            raw_detail_json=pick(prepared, "raw_detail_path"),
            evidence_json=relative_path(root, caption_evidence),
            content_id=content_id,
            run_id=run_id,
            video_url=video_url,
            native_content_id=pick(prepared, "native_content_id"),
            expected_raw_sha256=pick(prepared, "raw_detail_sha256"),
        )
        if evidence.get("status") == "success" and str(evidence.get("text") or "").strip():
            return {
                "ok": True, "status": "transcript_ready", "run_id": run_id,
                "content_id": content_id, "source": "platform_caption",
                "evidence_path": relative_path(root, caption_evidence),
                "result_path": relative_path(root, run_dir / "正文获取结果.json"),
            }
    # Page body text is evidence in its own right; legacy status fields are
    # ignored so model-provided values cannot override the evidence.
    if body_text:
        return {"ok": True, "status": "transcript_ready", "run_id": run_id, "content_id": content_id, "source": "platform_caption", "result_path": relative_path(root, run_dir / "正文获取结果.json")}
    if media_type != "视频":
        return {"ok": True, "status": "unavailable", "run_id": run_id, "content_id": content_id, "reason": "图文内容不适用 ASR", "analysis_handoff": {"status": "blocked", "blocking_reason": "图文内容没有视频正文"}}
    if not re.match(r"^https?://", video_url, flags=re.IGNORECASE):
        return {"ok": True, "status": "unavailable", "run_id": run_id, "content_id": content_id, "reason": "视频缺少合法视频下载链接", "analysis_handoff": {"status": "blocked", "blocking_reason": "视频缺少合法视频下载链接"}}
    url_hash = _asr_url_hash(video_url)
    idempotency_key = hashlib.sha256(f"benchmark-content:{run_id}:{content_id}:{url_hash}".encode("utf-8")).hexdigest()
    input_path = run_dir / "ASR输入.json"
    result_path = run_dir / "ASR结果.json"
    input_payload = {"run_id": run_id, "purpose": "content_transcript", "content_id": content_id, "items": [{"content_id": content_id, "video_url": video_url, "url_hash": url_hash}], "idempotency_key": idempotency_key}
    if input_path.is_file():
        existing = read_json(input_path)
        if existing != input_payload:
            raise ContentIntakeError("asr_input_already_written")
    else:
        write_json_atomically(input_path, input_payload)
    result_rel = relative_path(root, result_path)
    return {"ok": True, "status": "asr_batch_pending", "run_id": run_id, "content_id": content_id, "asr_input_path": relative_path(root, input_path), "asr_result_path": result_rel, "result_path": result_rel, "idempotency_key": idempotency_key, "next": {"type": "mcp", "tool": "transcribe_video_batch", "result_path": result_rel, "arguments": {"workspace_id": _workspace_id(root), "run_id": run_id, "purpose": "content_transcript", "items": [{"content_id": content_id, "video_url": video_url}], "idempotency_key": idempotency_key}}}


def write_asr_result(root: Path, process_path: Path, envelope_path: Path) -> dict[str, object]:
    process_payload = read_json(process_path)
    if process_payload.get("status") != "asr_batch_pending":
        raise ContentIntakeError("asr_process_status_invalid")
    result_path = resolve_under(root, pick(process_payload, "asr_result_path", "result_path"), required_prefix=".meiling/staging/")
    envelope = read_json(envelope_path)
    candidate = _normalize_asr_envelope(envelope)
    run_id = pick(process_payload, "run_id")
    if pick(candidate, "run_id") != run_id:
        raise ContentIntakeError("asr_result_run_id_mismatch")
    expected_items = process_payload.get("next", {}).get("arguments", {}).get("items", []) if isinstance(process_payload.get("next"), Mapping) else []
    result_items = candidate.get("items")
    if not isinstance(expected_items, list) or len(expected_items) != 1 or not isinstance(result_items, list) or len(result_items) != 1:
        raise ContentIntakeError("asr_result_single_item_required")
    expected = expected_items[0]
    item = result_items[0]
    if not isinstance(expected, Mapping) or not isinstance(item, Mapping):
        raise ContentIntakeError("asr_result_item_invalid")
    if pick(item, "content_id") != pick(expected, "content_id"):
        raise ContentIntakeError("asr_result_content_id_mismatch")
    if str(item.get("url_hash") or "").strip().lower() != _asr_url_hash(pick(expected, "video_url")):
        raise ContentIntakeError("asr_result_url_hash_mismatch")
    status = pick(item, "status").lower()
    if status not in {"success", "failed"}:
        raise ContentIntakeError("asr_result_status_invalid")
    if status == "success" and not pick(item, "text", "transcript"):
        raise ContentIntakeError("asr_result_text_empty")
    normalized = dict(candidate)
    normalized["run_id"] = run_id
    # 服务端回执里的 idempotency_key 是它自己的批次缓存键（掺了 workspace_id / run_id
    # 再哈希一次），不是本链路 process 冻结的那个值；而正文执行器要求两者相等。落盘一律
    # 以冻结键为准，与同文件 local_asr 分支保持一致。
    frozen_key = str(process_payload.get("idempotency_key") or "").strip()
    if not frozen_key:
        next_block = process_payload.get("next")
        arguments = next_block.get("arguments") if isinstance(next_block, Mapping) else None
        if isinstance(arguments, Mapping):
            frozen_key = str(arguments.get("idempotency_key") or "").strip()
    if frozen_key:
        normalized["idempotency_key"] = frozen_key
    if result_path.is_file():
        if read_json(result_path) == normalized:
            return {"ok": True, "status": "idempotent", "run_id": run_id, "result_path": relative_path(root, result_path)}
        raise ContentIntakeError("asr_result_already_written")
    write_json_atomically(result_path, normalized)
    return {"ok": True, "status": "written", "run_id": run_id, "result_path": relative_path(root, result_path)}


def resume(root: Path, source_path: Path, prepare_path: Path, process_path: Path) -> dict[str, object]:
    prepared, source, run_dir, run_id = _load_process_context(root, source_path, prepare_path)
    process_payload = read_json(process_path)
    status = pick(process_payload, "status")
    content_id = pick(prepared, "content_id")
    if status == "asr_batch_pending":
        asr_path = resolve_under(root, pick(process_payload, "asr_result_path", "result_path"), required_prefix=".meiling/staging/")
        if not asr_path.is_file():
            return {"ok": False, "status": "data_blocked", "run_id": run_id, "content_id": content_id, "error": "asr_result_missing", "analysis_handoff": {"status": "blocked", "blocking_reason": "ASR结果.json 不存在，禁止启动正文执行器"}}
        asr_payload = read_json(asr_path)
        if pick(asr_payload, "run_id") != run_id:
            raise ContentIntakeError("asr_result_run_id_mismatch")
        item = asr_payload.get("items", [None])[0] if isinstance(asr_payload.get("items"), list) else None
        if not isinstance(item, Mapping) or pick(item, "content_id") != content_id:
            raise ContentIntakeError("asr_result_content_id_mismatch")
        if pick(item, "status").lower() != "success":
            return {"ok": False, "status": "data_blocked", "run_id": run_id, "content_id": content_id, "error": pick(item, "error", "error_code") or "asr_failed", "analysis_handoff": {"status": "blocked", "blocking_reason": "ASR执行失败，禁止进入正文和分析"}}
    if status not in {"asr_batch_pending", "transcript_ready"}:
        return {"ok": False, "status": "data_blocked", "run_id": run_id, "content_id": content_id, "error": pick(process_payload, "reason") or "transcript_unavailable", "analysis_handoff": {"status": "blocked", "blocking_reason": pick(process_payload, "reason") or "正文不可用"}}
    transcript_path = resolve_under(root, pick(prepared, "expected_staged_transcript_path"), required_prefix=".meiling/staging/")
    result_path = resolve_under(root, pick(prepared, "expected_transcript_result_path"), required_prefix=".meiling/staging/")
    caption_evidence = run_dir / "platform-caption-evidence.json"
    executor = Path(__file__).with_name("获取视频文案执行器.py")
    command = [
        sys.executable, str(executor), "--content-id", content_id,
        "--title", source["标题"], "--platform", source["平台"],
        "--media-type", source["媒介形式"], "--video-url", source.get("视频下载链接", ""),
        "--fetch-method", source.get("基础数据获取方式", ""),
        "--note-url", source.get("对外页面链接", ""), "--body-source-json", str(source_path),
        "--asr-result-json", str(run_dir / "ASR结果.json"), "--run-id", run_id,
        # 本链路的幂等键用 benchmark-content 命名空间，正文执行器默认按 own-content 重算，
        # 不把这里冻结的键显式传过去就必然判「幂等键不一致」。
        "--asr-idempotency-key", pick(process_payload, "idempotency_key"),
        "--output-path", str(transcript_path), "--workspace-root", str(root),
    ]
    if source.get("平台") == "小红书" and pick(prepared, "raw_detail_path"):
        command.extend([
            "--raw-detail-json", str(resolve_under(root, pick(prepared, "raw_detail_path"), required_prefix=".meiling/staging/")),
            "--raw-detail-sha256", pick(prepared, "raw_detail_sha256"),
            "--native-content-id", pick(prepared, "native_content_id"),
        ])
    # 平台字幕证据路径与 raw_detail 解耦。该参数同时充当执行器物化证据的
    # 「输出路径」，所以小红书带 raw_detail 时必须照传（文件还不存在也一样）；
    # 其余平台（如 B站）只要本轮真的物化过证据就一并透传 —— 原先它只挂在
    # 「小红书 + raw_detail_path」条件下，B站 拿不到参数便退化成
    # 「读不到字幕 → 走 ASR → ASR结果.json 不存在 → transcript_executor_failed」。
    if caption_evidence.is_file() or (
        source.get("平台") == "小红书" and pick(prepared, "raw_detail_path")
    ):
        command.extend(["--platform-caption-evidence-json", str(caption_evidence)])
    completed = subprocess.run(command, text=True, capture_output=True, check=False)
    if completed.stdout.strip():
        atomic_write(result_path, completed.stdout.encode("utf-8"))
    if completed.returncode != 0:
        return {"ok": False, "status": "data_blocked", "run_id": run_id, "content_id": content_id, "transcript_result_path": relative_path(root, result_path), "error": "transcript_executor_failed", "analysis_handoff": {"status": "blocked", "blocking_reason": completed.stderr.strip() or "正文执行器失败"}}
    return {"ok": True, "status": "transcript_ready", "run_id": run_id, "content_id": content_id, "transcript_result_path": relative_path(root, result_path), "staged_transcript_path": relative_path(root, transcript_path)}


def merge_remark(existing: str, warnings: list[str]) -> str:
    parts = [part.strip() for part in str(existing or "").split("；") if part.strip()]
    parts = [part for part in parts if not part.startswith("单条建档:")]
    if warnings:
        parts.append("单条建档:" + "，".join(warnings))
    return "；".join(parts)


def transcript_result(
    root: Path,
    raw_path: str,
    *,
    expected_content_id: str,
    expected_run_id: str = "",
    expected_output_path: Path,
) -> tuple[bool, str]:
    if not str(raw_path or "").strip():
        raise ContentIntakeError("transcript_result_missing")
    path = resolve_under(root, raw_path)
    payload = read_json(path)
    if pick(payload, "content_id") != expected_content_id:
        raise ContentIntakeError("transcript_content_id_mismatch")
    returned_run_id = pick(payload, "run_id", "运行标识")
    if expected_run_id and returned_run_id and returned_run_id != expected_run_id:
        raise ContentIntakeError("transcript_run_id_mismatch")
    actual_output_path = resolve_under(root, pick(payload, "output_path"))
    if actual_output_path != expected_output_path:
        raise ContentIntakeError("transcript_output_path_mismatch")
    state = pick(payload, "状态", "status").lower()
    if state in {"成功", "success", "ok"}:
        source = pick(payload, "source", "正文来源")
        if source and source not in {"formal_transcript", "platform_caption", "asr"}:
            raise ContentIntakeError("transcript_source_invalid")
        text_path = resolve_under(root, pick(payload, "text_path", "输出路径", "output_path"))
        if not text_path.is_file() or not text_path.read_text(encoding="utf-8").strip():
            raise ContentIntakeError("transcript_text_path_empty")
        return True, ""
    if state in {"unavailable", "确认无正文", "不适用"}:
        return False, pick(payload, "failure_reason", "失败原因", "error", "message") or "正文不可用"
    reason = pick(payload, "失败原因", "error", "message") or "本轮正文获取失败"
    return False, reason


def cover_result(
    root: Path,
    raw_path: str,
    *,
    expected_content_id: str,
    expected_output_path: Path,
) -> tuple[bool, str]:
    if not str(raw_path or "").strip():
        return False, "封面图未执行"
    path = resolve_under(root, raw_path)
    relative = relative_path(root, path)
    if not (relative.startswith(".meiling/staging/") or relative.startswith("对标资料/对标内容/")):
        raise ContentIntakeError("cover_outside_allowed_directory")
    if not path.is_file():
        return False, "封面图执行结果缺失"
    try:
        payload = read_json(path)
    except ContentIntakeError:
        return False, "封面图执行结果损坏"
    returned_content_id = pick(payload, "content_id", "内容ID")
    if returned_content_id and returned_content_id != expected_content_id:
        raise ContentIntakeError("cover_content_id_mismatch")
    returned_output = pick(payload, "output_path", "输出路径", "封面图路径")
    if returned_output and resolve_under(root, returned_output) != expected_output_path:
        raise ContentIntakeError("cover_output_path_mismatch")
    state = pick(payload, "状态", "status").lower()
    if state in {"成功", "success", "ok"}:
        if not returned_content_id:
            raise ContentIntakeError("cover_content_id_mismatch")
        if not returned_output:
            raise ContentIntakeError("cover_output_path_mismatch")
        return True, ""
    reason = pick(payload, "失败原因", "error", "message") or "本轮封面图获取失败"
    return False, reason


def staged_payload(root: Path, raw_path: str, *, label: str, required: bool) -> bytes | None:
    if not str(raw_path or "").strip():
        if required:
            raise ContentIntakeError(f"{label}_path_missing")
        return None
    path = resolve_under(root, raw_path)
    relative = relative_path(root, path)
    if not (
        relative.startswith(".meiling/staging/")
        or relative.startswith("对标资料/对标内容/")
    ):
        raise ContentIntakeError(f"{label}_outside_allowed_directory")
    if not path.is_file() or path.stat().st_size == 0:
        if required:
            raise ContentIntakeError(f"{label}_missing_or_empty")
        return None
    return path.read_bytes()


def file_snapshot(path: Path) -> bytes | None:
    if not path.exists():
        return None
    if not path.is_file():
        raise ContentIntakeError(f"formal_target_not_file:{path}")
    return path.read_bytes()


def restore_file(path: Path, payload: bytes | None) -> None:
    if payload is None:
        path.unlink(missing_ok=True)
    else:
        atomic_write(path, payload)


def commit(
    root: Path,
    source_path: Path,
    prepare_path: Path,
    transcript_result_path: str = "",
    *,
    cover_result_path: str = "",
    staged_cover_path: str = "",
    staged_transcript_path: str = "",
    staged_comments_path: str = "",
) -> dict[str, object]:
    prepared = read_json(prepare_path)
    if prepared.get("status") != "prepared":
        raise ContentIntakeError("prepare_status_invalid")
    if hashlib.sha256(source_path.read_bytes()).hexdigest() != pick(prepared, "source_sha256"):
        raise ContentIntakeError("prepare_source_sha256_mismatch")
    source = parse_source(read_json(source_path))
    content_id = pick(prepared, "content_id")
    platform = pick(prepared, "platform")
    if not content_id:
        raise ContentIntakeError("prepare_content_id_missing")
    if platform != source["平台"]:
        raise ContentIntakeError("prepare_source_platform_mismatch")
    if pick(prepared, "normalized_url") != normalize_url(source["对外页面链接"]):
        raise ContentIntakeError("prepare_source_url_mismatch")
    current_native_id = native_identity(source["平台"], source, source["对外页面链接"])
    if pick(prepared, "native_content_id") != current_native_id:
        raise ContentIntakeError("prepare_native_content_id_mismatch")
    content_dir = resolve_under(root, pick(prepared, "content_dir"), required_prefix="对标资料/对标内容/")
    expected_transcript_path = resolve_under(root, pick(prepared, "expected_transcript_path"))
    if expected_transcript_path != content_dir / "视频文案.md":
        raise ContentIntakeError("prepare_expected_transcript_path_mismatch")
    expected_cover_result = resolve_under(
        root, pick(prepared, "expected_cover_result_path"), required_prefix=".meiling/staging/"
    )
    expected_transcript_result = resolve_under(
        root, pick(prepared, "expected_transcript_result_path"), required_prefix=".meiling/staging/"
    )
    expected_staged_cover = resolve_under(root, pick(prepared, "expected_staged_cover_path"))
    expected_staged_transcript = resolve_under(root, pick(prepared, "expected_staged_transcript_path"))
    expected_staged_comments = resolve_under(root, pick(prepared, "expected_staged_comments_path"))
    actual_cover_result = resolve_under(
        root, cover_result_path, required_prefix=".meiling/staging/", label="cover_result_json"
    )
    actual_transcript_result = resolve_under(
        root, transcript_result_path, required_prefix=".meiling/staging/", label="transcript_result_json"
    )
    actual_staged_cover = resolve_under(root, staged_cover_path, label="staged_cover_path")
    actual_staged_transcript = resolve_under(root, staged_transcript_path, label="staged_transcript_path")
    actual_staged_comments = resolve_under(root, staged_comments_path, label="staged_comments_path")
    if actual_cover_result != expected_cover_result:
        raise ContentIntakeError("cover_result_path_mismatch")
    if actual_transcript_result != expected_transcript_result:
        raise ContentIntakeError("transcript_result_path_mismatch")
    if actual_staged_cover != expected_staged_cover:
        raise ContentIntakeError("staged_cover_path_mismatch")
    if actual_staged_transcript != expected_staged_transcript:
        raise ContentIntakeError("staged_transcript_path_mismatch")
    if actual_staged_comments != expected_staged_comments:
        raise ContentIntakeError("staged_comments_path_mismatch")

    current_transcript = transcript_result(
        root,
        transcript_result_path,
        expected_content_id=content_id,
        expected_run_id=pick(prepared, "run_id"),
        expected_output_path=expected_staged_transcript,
    )
    current_cover = (
        cover_result(
            root,
            cover_result_path,
            expected_content_id=content_id,
            expected_output_path=expected_staged_cover,
        )
        if source.get("封面图原链接")
        else (False, "封面图原链接缺失，本轮未执行")
    )
    cover_payload = (
        staged_payload(root, staged_cover_path, label="staged_cover", required=True)
        if current_cover[0] else None
    )
    transcript_payload = (
        staged_payload(root, staged_transcript_path, label="staged_transcript", required=True)
        if current_transcript[0] else None
    )
    comments_payload = staged_payload(root, staged_comments_path, label="staged_comments", required=False)
    transcript_confirmed_empty = bool(
        transcript_payload
        and transcript_payload.decode("utf-8", errors="ignore").strip() == CONFIRMED_EMPTY_BODY_MARKER
    )

    return commit_latest_library(
        root=root, source=source, prepared=prepared, staged_content_id=content_id,
        content_dir=content_dir, current_cover=current_cover,
        current_transcript=current_transcript, cover_payload=cover_payload,
        transcript_payload=transcript_payload,
        comments_payload=comments_payload,
        transcript_confirmed_empty=transcript_confirmed_empty,
    )


def _local_run_dir(root: Path, raw_path: str) -> Path:
    path = resolve_under(root, raw_path, required_prefix=LOCAL_STAGING_PREFIX)
    run_dir = path if path.is_dir() else path.parent
    if run_dir.parent != (root / LOCAL_STAGING_ROOT).resolve():
        raise ContentIntakeError("local_run_directory_invalid")
    return run_dir


def local_start(root: Path, source_url: str, selected_source: str) -> dict[str, object]:
    if selected_source not in {"chrome_devtools", "justoneapi"}:
        raise ContentIntakeError("selected_source_invalid:chrome_devtools_or_justoneapi_required")
    result = initialize_run(root, source_url)
    result = {**result, "status": "local_started", "selected_source": selected_source}
    run_dir = resolve_under(root, str(result["run_dir"]), required_prefix=LOCAL_STAGING_PREFIX)
    write_json_atomically(run_dir / "运行准备结果.json", result)
    return result


def local_prepare(root: Path, source_path: Path, selected_source: str, raw_detail_json: str = "") -> dict[str, object]:
    source = parse_source(read_json(source_path))
    method = source.get("基础数据获取方式", "").lower()
    if selected_source not in {"chrome_devtools", "justoneapi"} or selected_source not in method:
        raise ContentIntakeError("selected_source_mismatch")
    run_dir = source_path.parent.resolve()
    result = prepare(root, source_path, raw_detail_json)
    content_dir = resolve_under(root, str(result.get("content_dir") or ""), required_prefix="对标资料/对标内容/")
    result = {
        **result,
        "local_status": "local_prepared",
        "selected_source": selected_source,
        "expected_staged_cover_path": relative_path(root, content_dir / "封面图.png"),
        "expected_staged_transcript_path": relative_path(root, content_dir / "视频文案.md"),
        "expected_staged_comments_path": relative_path(root, content_dir / "评论.md"),
    }
    # Keep the accepted candidate envelope intact for downstream phases.
    # The normalized source is an internal parse result and omits the
    # success status required by parse_source when the run is resumed.
    write_json_atomically(run_dir / "基础数据.json", read_json(source_path))
    write_json_atomically(run_dir / "资料准备结果.json", result)
    return result


def local_write_transcript(root: Path, prepare_path: Path, transcript_path: Path) -> dict[str, object]:
    prepared = read_json(prepare_path)
    if prepared.get("status") != "prepared" or prepared.get("local_status") != "local_prepared":
        raise ContentIntakeError("prepare_status_invalid")
    run_dir = prepare_path.parent.resolve()
    if transcript_path.parent.resolve() != run_dir:
        raise ContentIntakeError("transcript_json_not_in_current_run")
    body = read_json(transcript_path)
    content_id = str(prepared.get("content_id") or "")
    run_id = run_dir.name
    if body.get("content_id") != content_id or body.get("run_id") != run_id:
        raise ContentIntakeError("transcript_identity_mismatch")
    source_path = run_dir / "基础数据.json"
    source = parse_source(read_json(source_path))
    video_url = source.get("视频下载链接", "")
    source_name = str(body.get("source") or "")
    status = normalize_status(body.get("status"))
    text = str(body.get("text") or "").strip()
    if source_name not in {"formal_transcript", "platform_caption", "asr", "unavailable"}:
        raise ContentIntakeError("transcript_contract_invalid")
    if source_name == "formal_transcript":
        selected = choose_transcript(content_id=content_id, run_id=run_id, video_url=video_url, formal_text=text)
    elif source_name == "platform_caption":
        selected = choose_transcript(content_id=content_id, run_id=run_id, video_url=video_url, caption=body)
    else:
        selected = choose_transcript(content_id=content_id, run_id=run_id, video_url=video_url, asr_text=text, asr_status=status, asr_failure_reason=str(body.get("failure_reason") or ""))
    selected_status = str(selected.get("status") or "")
    staged = resolve_under(root, str(prepared.get("expected_transcript_path") or ""), required_prefix="对标资料/对标内容/")
    if selected_status == "success" and str(selected.get("text") or "").strip():
        atomic_write(staged, (str(selected["text"]).strip() + "\n").encode("utf-8"))
    elif selected_status == "unavailable":
        atomic_write(staged, (CONFIRMED_EMPTY_BODY_MARKER + "\n").encode("utf-8"))
    else:
        raise ContentIntakeError(str(selected.get("failure_reason") or "transcript_unavailable"))
    result = {"ok": True, "status": selected_status, "状态": "成功" if selected_status == "success" else "确认无正文", "content_id": content_id, "run_id": run_id, "source": selected.get("source"), "output_path": relative_path(root, staged), "text_path": relative_path(root, staged), "failure_reason": selected.get("failure_reason", "")}
    write_json_atomically(run_dir / "正文获取结果.json", result)
    return result


def local_asr(root: Path, prepare_path: Path, provider: str) -> dict[str, object]:
    prepared = read_json(prepare_path)
    run_dir = prepare_path.parent.resolve()
    source = parse_source(read_json(run_dir / "基础数据.json"))
    video_url = source.get("视频下载链接", "")
    if source.get("媒介形式") != "视频" or not video_url:
        raise ContentIntakeError("asr_video_url_missing")
    import 获取视频文案执行器 as transcript_executor
    try:
        value = transcript_executor.run_selected_local_asr(
            root,
            video_url=video_url,
            provider=provider,
            platform=source.get("平台", ""),
            bvid=source.get("bvid", ""),
            aid=source.get("aid", ""),
            cid=source.get("cid", ""),
        )
    except Exception as exc:
        raise ContentIntakeError(f"local_asr_failed:{exc}") from exc
    result = {"content_id": prepared.get("content_id", ""), "run_id": run_dir.name, "source": "asr", "status": "success", "video_url": video_url, "url_hash": video_url_hash(video_url), "text": value["text"], "provider": provider, "model": value["model"]}
    write_json_atomically(run_dir / "本地ASR结果.json", result)
    return result


def local_commit(root: Path, source_path: Path, prepare_path: Path) -> dict[str, object]:
    run_dir = prepare_path.parent.resolve()
    prepared = read_json(prepare_path)
    result = commit(root, source_path, prepare_path, relative_path(root, run_dir / "正文获取结果.json"), cover_result_path=relative_path(root, run_dir / "封面图获取结果.json"), staged_cover_path=str(prepared.get("expected_staged_cover_path") or ""), staged_transcript_path=str(prepared.get("expected_staged_transcript_path") or ""), staged_comments_path=str(prepared.get("expected_staged_comments_path") or ""))
    # analysis_handoff 是数据流程交给分析流程的唯一入口合同，必须保留在本轮回执中。
    result = {key: value for key, value in result.items() if key not in {"next", "next_action", "run_state_path"}}
    write_json_atomically(run_dir / "资料提交结果.json", result)
    return result


def commit_latest_library(
    *,
    root: Path,
    source: Mapping[str, str],
    prepared: Mapping[str, object],
    staged_content_id: str,
    content_dir: Path,
    current_cover: tuple[bool, str],
    current_transcript: tuple[bool, str],
    cover_payload: bytes | None,
    transcript_payload: bytes | None,
    comments_payload: bytes | None,
    transcript_confirmed_empty: bool,
) -> dict[str, object]:
    """Merge one target into a freshly-read table while holding its write lock."""
    library_path = root / CONTENT_LIBRARY
    with content_library_write_lock(library_path):
        fields, rows, snapshot = read_csv_table_with_snapshot(library_path)
        validate_library(fields)

        id_matches = [row for row in rows if row.get("内容ID", "").strip() == staged_content_id]
        identity_matches = find_matches(rows, source_url=source["对外页面链接"], data=source)
        if len(id_matches) > 1:
            raise ContentIntakeError(f"content_id_conflict:duplicate:{staged_content_id}")
        if len(identity_matches) > 1:
            raise ContentIntakeError("target_content_conflict:identity_match_count")

        prepared_mode = pick(prepared, "record_mode")
        initial_row_sha = pick(prepared, "target_row_sha256")
        reassigned_from = ""
        reused_concurrent_assets = False
        record_mode = "create"
        row: dict[str, str]
        if identity_matches:
            row = identity_matches[0]
            content_id = row.get("内容ID", "").strip()
            if not content_id:
                raise ContentIntakeError("target_content_conflict:content_id_missing")
            if prepared_mode == "update" and row_sha256(row) != initial_row_sha:
                raise ContentIntakeError("target_content_conflict:changed_since_prepare")
            if prepared_mode == "create" and not deterministic_concurrent_target_merge(row, source):
                raise ContentIntakeError("target_content_conflict:merge_ambiguous")
            if prepared_mode == "create":
                reused_concurrent_assets = any(
                    readable_file(root, row.get(field, ""))
                    for field in ("封面图路径", "视频文案路径", "评论路径")
                )
                if reused_concurrent_assets:
                    cover_payload = None
                    transcript_payload = None
                    comments_payload = None
            record_mode = "update"
            existing_dir = infer_content_dir(root, row)
            if existing_dir is not None:
                content_dir = existing_dir
        elif prepared_mode == "update":
            # The row inspected by prepare disappeared or no longer identifies this target.
            raise ContentIntakeError("target_content_conflict:target_missing")
        elif id_matches:
            if pick(prepared, "content_id_origin") != "global":
                raise ContentIntakeError(f"content_id_conflict:occupied:{staged_content_id}")
            reassigned_from = staged_content_id
            content_id = next_global_content_id(
                root, additional_ids=[row.get("内容ID", "") for row in rows]
            )
            if not content_id or content_id == staged_content_id:
                raise ContentIntakeError(f"content_id_conflict:reassign_failed:{staged_content_id}")
            content_dir = content_dir.with_name(f"{content_dir.name}-{content_id}")
            if content_dir.exists():
                raise ContentIntakeError(f"content_id_conflict:content_dir_occupied:{content_id}")
            row = {field: "" for field in fields}
            rows.append(row)
        else:
            content_id = staged_content_id
            row = {field: "" for field in fields}
            rows.append(row)

        update_fields = (
            "平台", "账号ID", "账号名", "标题", "封面图原链接", "视频下载链接",
            "媒介形式", "点赞数", "收藏数", "分享数", "评论数", "发布时间",
            "对外页面链接", "基础数据获取方式", "bvid", "aid", "cid",
        )
        row["内容ID"] = content_id
        row["内容目录路径"] = relative_path(root, content_dir)
        for field in update_fields:
            if field in fields and source.get(field, ""):
                row[field] = source[field]

        asset_targets = {
            "封面图路径": (content_dir / "封面图.png", cover_payload),
            "视频文案路径": (content_dir / "视频文案.md", transcript_payload),
            "评论路径": (content_dir / "评论.md", comments_payload),
        }
        for field, (path, payload) in asset_targets.items():
            if field in fields and (payload is not None or (path.is_file() and path.stat().st_size > 0)):
                row[field] = relative_path(root, path)

        warnings: list[str] = []
        if reused_concurrent_assets:
            warnings.append("当前内容已由并发任务建档，复用最新正式记录和资产")
        counts = {field: parse_count(row.get(field, "")) for field in ("点赞数", "收藏数", "评论数")}
        if all(value is not None for value in counts.values()):
            core = sum(value for value in counts.values() if value is not None)
            if "核心互动数" in fields:
                row["核心互动数"] = str(core)
            if "数据水平" in fields:
                row["数据水平"] = compute_data_level(row["平台"], core)
        else:
            warnings.append("互动数据不完整，数据水平无法重新计算")
        if "发布时间区间" in fields and row.get("发布时间", "").strip():
            slot = time_slot(row["发布时间"])
            if slot:
                row["发布时间区间"] = slot
            else:
                warnings.append("发布时间格式无法识别，发布时间区间未更新")
        if cover_payload is None:
            if readable_file(root, row.get("封面图路径", "")):
                warnings.append(f"本轮封面图未更新，保留旧文件：{current_cover[1]}")
            else:
                warnings.append(f"本轮封面图未保存：{current_cover[1]}")
        if not current_transcript[0]:
            warnings.append(f"本轮正文获取失败：{current_transcript[1]}")
        row["备注"] = merge_remark(row.get("备注", ""), warnings)

        account_sync = prepare_account_content_list_sync(root, row)
        writes = [(library_path, fields, rows)]
        expected = {library_path: snapshot}
        if account_sync.status == "synced":
            assert account_sync.path is not None
            assert account_sync.fields is not None
            assert account_sync.rows is not None
            writes.append((account_sync.path, account_sync.fields, account_sync.rows))
            expected[account_sync.path] = account_sync.snapshot

        formal_updates = [
            (path, payload)
            for path, payload in (
                (content_dir / "封面图.png", cover_payload),
                (content_dir / "视频文案.md", transcript_payload),
                (content_dir / "评论.md", comments_payload),
            )
            if payload is not None
        ]
        previous_files = {path: file_snapshot(path) for path, _ in formal_updates}
        created_content_dir = not content_dir.exists()
        csv_committed = False
        try:
            content_dir.mkdir(parents=True, exist_ok=True)
            for path, payload in formal_updates:
                atomic_write(path, payload)
            write_csv_tables_atomically(writes, expected_originals=expected)
            csv_committed = True

            verified_fields, verified_rows, verified_snapshot = read_csv_table_with_snapshot(library_path)
            verified = [item for item in verified_rows if item.get("内容ID", "").strip() == content_id]
            files_match = all(path.is_file() and path.read_bytes() == payload for path, payload in formal_updates)
            if verified_fields != fields or len(verified) != 1 or not files_match:
                raise ContentIntakeError("content_library_post_commit_verification_failed")
            verify_account_content_list_sync(account_sync, verified[0])
            handoff = analysis_handoff(root, verified[0])
            if not current_transcript[0]:
                # 没有视频文案不再阻断分析：交给分析流程出有限报告，正文相关结论写「信息不足」。
                handoff = {
                    **handoff,
                    "transcript_missing": True,
                    "transcript_warning": f"本轮正文获取失败，按有限报告继续分析：{current_transcript[1]}",
                }
        except Exception as exc:
            rollback_errors: list[str] = []
            if csv_committed:
                for path, payload in reversed(list(expected.items())):
                    try:
                        restore_file(path, payload)
                    except OSError as rollback_exc:
                        rollback_errors.append(f"{path}:{rollback_exc}")
            for path, _ in reversed(formal_updates):
                try:
                    restore_file(path, previous_files[path])
                except OSError as rollback_exc:
                    rollback_errors.append(f"{path}:{rollback_exc}")
            if created_content_dir:
                try:
                    content_dir.rmdir()
                except OSError:
                    pass
            if rollback_errors:
                raise ContentIntakeError(
                    f"content_commit_failed:{exc};rollback_failed:{';'.join(rollback_errors)}"
                ) from exc
            if isinstance(exc, (AccountContentListSyncError, ContentIntakeError, CsvTransactionError)):
                raise
            raise ContentIntakeError(f"content_commit_failed:{exc};rollback_completed") from exc

        asset_status = {
            "cover": "committed" if cover_payload is not None else (
                "retained_existing" if readable_file(root, row.get("封面图路径", "")) else "missing"
            ),
            "transcript": "committed" if transcript_payload is not None else (
                "retained_existing_blocked" if readable_file(root, row.get("视频文案路径", "")) else "missing_blocked"
            ),
            "comments": "committed" if comments_payload is not None else (
                "retained_existing" if readable_file(root, row.get("评论路径", "")) else "missing"
            ),
        }
        return {
            "ok": True,
            "status": "committed",
            "record_mode": record_mode,
            "content_id": content_id,
            "staged_content_id": staged_content_id,
            "reassigned_from_content_id": reassigned_from,
            "content_dir": relative_path(root, content_dir),
            "content_library_sha256": hashlib.sha256(verified_snapshot).hexdigest(),
            "account_content_list_sync_status": account_sync.status,
            "committed_files": [relative_path(root, path) for path, _ in formal_updates],
            "asset_status": asset_status,
            "warnings": warnings,
            "analysis_handoff": handoff,
        }


def run(args: argparse.Namespace) -> dict[str, object]:
    root = Path(args.workspace_root).expanduser().resolve()
    if not root.is_dir():
        raise ContentIntakeError(f"workspace_root_invalid:{root}")
    if args.phase == "chrome-canonical-contract":
        return chrome_canonical_contract()
    if args.phase == "resolve-source":
        return resolve_source_choice(root)
    if args.phase == "local-start":
        return local_start(root, args.source_url, args.selected_source)
    elif args.phase == "local-prepare":
        source_path = resolve_under(root, args.source_json, required_prefix=LOCAL_STAGING_PREFIX, label="source_json")
        return local_prepare(root, source_path, args.selected_source, args.raw_detail_json)
    elif args.phase == "local-write-transcript":
        prepare_path = resolve_under(root, args.prepare_json, required_prefix=LOCAL_STAGING_PREFIX, label="prepare_json")
        transcript_path = resolve_under(root, args.transcript_json, required_prefix=LOCAL_STAGING_PREFIX, label="transcript_json")
        return local_write_transcript(root, prepare_path, transcript_path)
    elif args.phase == "local-asr":
        prepare_path = resolve_under(root, args.prepare_json, required_prefix=LOCAL_STAGING_PREFIX, label="prepare_json")
        return local_asr(root, prepare_path, args.asr_provider)
    elif args.phase == "local-commit":
        source_path = resolve_under(root, args.source_json, required_prefix=LOCAL_STAGING_PREFIX, label="source_json")
        prepare_path = resolve_under(root, args.prepare_json, required_prefix=LOCAL_STAGING_PREFIX, label="prepare_json")
        return local_commit(root, source_path, prepare_path)
    elif args.phase == "start":
        return initialize_run(root, args.source_url)
    else:
        output_path = resolve_under(root, args.output_json, required_prefix=".meiling/staging/", label="output_json")
        if args.phase == "inspect":
            result = inspect(root, args.source_url)
        elif args.phase == "prepare":
            source_path = resolve_under(root, args.source_json, required_prefix=".meiling/staging/", label="source_json")
            result = prepare(root, source_path, args.raw_detail_json)
        elif args.phase == "process":
            source_path = resolve_under(root, args.source_json, required_prefix=".meiling/staging/", label="source_json")
            prepare_path = resolve_under(root, args.prepare_json, required_prefix=".meiling/staging/", label="prepare_json")
            result = process(root, source_path, prepare_path)
        elif args.phase == "write-asr-result":
            process_path = resolve_under(root, args.process_json, required_prefix=".meiling/staging/", label="process_json")
            envelope_path = resolve_under(root, args.asr_envelope_json, required_prefix=".meiling/staging/", label="asr_envelope_json")
            result = write_asr_result(root, process_path, envelope_path)
        elif args.phase == "resume":
            source_path = resolve_under(root, args.source_json, required_prefix=".meiling/staging/", label="source_json")
            prepare_path = resolve_under(root, args.prepare_json, required_prefix=".meiling/staging/", label="prepare_json")
            process_path = resolve_under(root, args.process_json, required_prefix=".meiling/staging/", label="process_json")
            result = resume(root, source_path, prepare_path, process_path)
        else:
            source_path = resolve_under(root, args.source_json, required_prefix=".meiling/staging/")
            prepare_path = resolve_under(root, args.prepare_json, required_prefix=".meiling/staging/")
            result = commit(
                root,
                source_path,
                prepare_path,
                args.transcript_result_json,
                cover_result_path=args.cover_result_json,
                staged_cover_path=args.staged_cover_path,
                staged_transcript_path=args.staged_transcript_path,
                staged_comments_path=args.staged_comments_path,
            )
        write_json_atomically(output_path, result)
        return result


def main() -> int:
    args = parse_args()
    try:
        result = run(args)
    except (
        AccountContentListSyncError,
        ContentIntakeError,
        CsvTransactionError,
        GlobalContentIdError,
        OSError,
        ValueError,
    ) as exc:
        failure = {"ok": False, "status": "failed", "error": str(exc)}
        if args.output_json:
            try:
                root = Path(args.workspace_root).expanduser().resolve()
                output_path = resolve_under(root, args.output_json, required_prefix=".meiling/staging/")
                write_json_atomically(output_path, failure)
            except (ContentIntakeError, OSError, ValueError):
                pass
        print(json.dumps(failure, ensure_ascii=False))
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
