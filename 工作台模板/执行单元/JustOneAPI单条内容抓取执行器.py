#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import re
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime
from pathlib import Path, PurePosixPath
from typing import Any, Callable, Mapping

import 证书信任  # noqa: F401  进程级统一 HTTPS 证书信任
from 权限错误 import permission_error_code
from 抖音内容链接解析 import canonicalize_douyin_content_url, extract_douyin_content_id


DOUYIN_TRANSFER_PATH = "/api/douyin/share-url-transfer/v1"
DOUYIN_DETAIL_PATH = "/api/douyin/get-video-detail/v2"
# ⚠️ v5 必须排在第一位，别为了省一次请求把它降序（2026-09-23 实测踩过）：
#   - v5 返回**扁平结构**（data 本身就是笔记），且**含 video 流地址**（video.master_url / backup_urls）。
#   - v4/v2/v1 返回 data[0].note_list[0]，字段更多，但**没有 video 键** → 视频笔记拿不到下载链接。
#   → 顺序影响的不只是请求次数，而是**取到的数据完整性**（视频流只在 v5 里有）。
# v5 的已知缺陷：对**部分**笔记会返回「结构完整、值全空」的空壳（同一 note_id 用 v4 却有真数据）
#   → 由 xiaohongshu_response_usable 把空壳筛掉并自动退到 v4（能建档，但该条没有视频流）。
XIAOHONGSHU_DETAIL_PATHS = (
    "/api/xiaohongshu/get-note-detail/v5",
    "/api/xiaohongshu/get-note-detail/v4",
    "/api/xiaohongshu/get-note-detail/v2",
    "/api/xiaohongshu/get-note-detail/v1",
)
# 笔记对象在各版本响应里的位置：v4/v2/v1 在 data[0].note_list[0]，v5 直接就是 data 自身。
XIAOHONGSHU_NOTE_PATHS = (
    (0, "note_list", 0),
    ("items", 0, "note_card"),
    ("item", "note_card"),
    ("note",),
    ("note_card",),
)
BILIBILI_DETAIL_PATH = "/api/bilibili/get-video-detail/v2"
SENSITIVE_QUERY_KEYS = {
    "token", "key", "api_key", "apikey", "access_token", "xsec_token", "xsec_source"
}
XIAOHONGSHU_PUBLIC_QUERY_KEYS = {"xsec_source", "xsec_token"}


class JustOneApiFetchError(RuntimeError):
    pass


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="通过 JustOneAPI 获取单条内容基础数据")
    parser.add_argument("--workspace-root", required=True)
    parser.add_argument("--source-url", required=True)
    parser.add_argument("--output-json", required=True)
    return parser.parse_args()


def resolve_under(root: Path, raw_path: str) -> Path:
    root = root.resolve()
    text = str(raw_path or "").strip().replace("\\", "/")
    if not text:
        raise JustOneApiFetchError("output_path_missing")
    logical = PurePosixPath(text)
    candidate = Path(text).expanduser()
    resolved = candidate.resolve() if candidate.is_absolute() else root.joinpath(*logical.parts).resolve()
    try:
        resolved.relative_to(root)
    except ValueError as exc:
        raise JustOneApiFetchError(f"output_path_outside_workspace:{text}") from exc
    return resolved


def write_json_atomically(path: Path, payload: Mapping[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".stage", dir=str(path.parent)
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def load_config(path: Path) -> dict[str, str]:
    if not path.is_file():
        return {}
    values: dict[str, str] = {}
    try:
        lines = path.read_text(encoding="utf-8-sig").splitlines()
    except (OSError, UnicodeError) as exc:
        raise JustOneApiFetchError(f"config_read_failed:{exc}") from exc
    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def redact_url(url: str) -> str:
    parsed = urllib.parse.urlsplit(url)
    redacted = [
        (key, "***" if key.lower() in SENSITIVE_QUERY_KEYS else value)
        for key, value in urllib.parse.parse_qsl(parsed.query, keep_blank_values=True)
    ]
    return urllib.parse.urlunsplit(
        (parsed.scheme, parsed.netloc, parsed.path, urllib.parse.urlencode(redacted), parsed.fragment)
    )


def xiaohongshu_page_url(native_id: str, source_url: str) -> str:
    """Keep a real XHS content URL; only use explore as the documented fallback."""
    parsed = urllib.parse.urlsplit(str(source_url or "").strip())
    host = (parsed.hostname or "").lower().rstrip(".")
    is_page = (
        host == "xiaohongshu.com"
        or host.endswith(".xiaohongshu.com")
    ) and bool(
        re.fullmatch(r"/explore/[0-9A-Za-z_-]+/?", parsed.path)
        or re.fullmatch(r"/discovery/item/[0-9A-Za-z_-]+/?", parsed.path)
        or re.fullmatch(r"/user/profile/[^/?#]+/[0-9A-Za-z_-]+/?", parsed.path)
    )
    query = urllib.parse.urlencode([
        (key, value)
        for key, value in urllib.parse.parse_qsl(parsed.query, keep_blank_values=True)
        if key.lower() in XIAOHONGSHU_PUBLIC_QUERY_KEYS
    ])
    if not query:
        query = "xsec_source=pc_feed"
    if is_page:
        return urllib.parse.urlunsplit((parsed.scheme.lower(), parsed.netloc, parsed.path.rstrip("/"), query, ""))
    if not native_id:
        return ""
    return urllib.parse.urlunsplit(
        ("https", "www.xiaohongshu.com", f"/explore/{native_id}", query, "")
    )


def http_get_json(url: str, *, timeout: int = 60) -> dict[str, Any]:
    request = urllib.request.Request(
        url,
        headers={"User-Agent": "Mozilla/5.0", "Accept": "application/json"},
        method="GET",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        raise JustOneApiFetchError(f"HTTP {exc.code} {redact_url(url)}") from exc
    except urllib.error.URLError as exc:
        code = permission_error_code(exc, "network")
        if code:
            raise JustOneApiFetchError(code) from exc
        raise JustOneApiFetchError(f"URL error {redact_url(url)} reason={exc.reason}") from exc
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise JustOneApiFetchError(f"response_json_invalid:{redact_url(url)}") from exc
    if not isinstance(payload, dict):
        raise JustOneApiFetchError(f"response_object_required:{redact_url(url)}")
    return payload


def build_api_url(base_url: str, path: str, params: Mapping[str, str]) -> str:
    query = urllib.parse.urlencode(
        {key: value for key, value in params.items() if str(value or "").strip()}
    )
    return f"{base_url.rstrip('/')}{path}?{query}"


def response_succeeded(response: Mapping[str, object]) -> bool:
    return (
        response.get("code") == 0 and response.get("data") is not None
    ) or (
        response.get("success") is True and response.get("data") is not None
    )


def call_justoneapi(
    urls: list[str],
    *,
    max_attempts: int = 1,
    retryable_codes: set[int] | None = None,
    retry_delay_seconds: float = 0.5,
    accept: Callable[[Mapping[str, object]], bool] | None = None,
) -> dict[str, Any]:
    """逐个候选地址调用，返回第一个「可用」的响应。

    `accept` 默认只看传输层成功（code==0 且有 data）。小红书额外传
    `xiaohongshu_response_usable`，再加一道「data 里到底有没有真内容」的校验——
    空响应体（如 v5 的空壳）必须继续试下一个版本，否则会把空壳当成功、再往下就卡死。
    """
    errors: list[str] = []
    retry_codes = retryable_codes or set()
    is_acceptable = accept or response_succeeded
    for url in urls:
        last_error = ""
        for attempt in range(max(1, max_attempts)):
            try:
                response = http_get_json(url)
            except JustOneApiFetchError as exc:
                if str(exc) == "network_permission_denied":
                    raise
                last_error = str(exc)
                break
            if is_acceptable(response):
                return response
            response_code = response.get("code")
            if response_succeeded(response):
                last_error = "empty_payload"
            else:
                last_error = str(response.get("message") or response.get("msg") or response_code or "unknown")
            if response_code not in retry_codes or attempt + 1 >= max_attempts:
                break
            time.sleep(retry_delay_seconds * (2**attempt))
        errors.append(f"{urllib.parse.urlsplit(url).path}:{last_error}")
    raise JustOneApiFetchError("api_request_failed:" + "；".join(errors))


def detect_platform(source_url: str) -> str:
    parsed = urllib.parse.urlsplit(source_url)
    if parsed.scheme not in {"http", "https"}:
        raise JustOneApiFetchError("source_url_invalid")
    host = parsed.netloc.lower()
    if "douyin.com" in host:
        return "抖音"
    if "xiaohongshu.com" in host or "xhslink.com" in host:
        return "小红书"
    if "bilibili.com" in host or host.endswith("b23.tv"):
        return "B站"
    raise JustOneApiFetchError("unsupported_platform")


def nested(node: Any, *path: object) -> Any:
    current = node
    for part in path:
        if isinstance(part, int):
            if not isinstance(current, list) or part >= len(current):
                return None
            current = current[part]
        else:
            if not isinstance(current, Mapping) or part not in current:
                return None
            current = current[part]
    return current


def first_path(node: Any, paths: tuple[tuple[object, ...], ...]) -> Any:
    for path in paths:
        value = nested(node, *path)
        if value not in (None, "", [], {}):
            return value
    return ""


def text(value: Any) -> str:
    return str(value or "").strip()


def number_text(value: Any) -> str:
    value_text = text(value).replace(",", "")
    if not value_text:
        return ""
    try:
        return str(int(float(value_text)))
    except ValueError:
        return value_text


def timestamp_text(value: Any) -> str:
    value_text = text(value)
    if not value_text:
        return ""
    try:
        timestamp = int(float(value_text))
    except ValueError:
        return value_text
    if timestamp > 10_000_000_000:
        timestamp //= 1000
    return datetime.fromtimestamp(timestamp).astimezone().strftime("%Y/%m/%d %H:%M")


def xiaohongshu_note_from_payload(payload: Any) -> Any:
    """按各版本共有的位置取笔记对象；都取不到时退回 payload 自身（v5 形态）。"""
    return first_path(payload, XIAOHONGSHU_NOTE_PATHS) or payload


def xiaohongshu_response_usable(response: Mapping[str, object]) -> bool:
    """小红书：传输层成功（code==0 且有 data）还不够。

    2026-09-23 实测 v5 会返回「结构完整、字段全空」的空壳——标题、正文、note_id、
    作者、互动数全是空值，但 code 仍是 0。只判 code 会把它当成功，于是永远走不到
    后面有真数据的版本，最终报 required_identity_missing:标题。
    这里额外要求能读出至少一个标识性字段，读不出就视为不可用、继续试下一个版本。
    """
    if not response_succeeded(response):
        return False
    payload = response.get("data") or response
    note = xiaohongshu_note_from_payload(payload)
    if not isinstance(note, Mapping):
        return False
    identity_paths = (
        ("note_id",),
        ("id",),
        ("item_id",),
        ("title",),
        ("display_title",),
        ("desc",),
        ("content",),
        ("text",),
    )
    return bool(text(first_path(note, identity_paths)))


def extract_douyin_id(source_url: str) -> str:
    return extract_douyin_content_id(source_url)


def fetch_douyin(base_url: str, token: str, source_url: str) -> dict[str, object]:
    # 短链交给 JustOneAPI transfer 兜底，避免批处理为每条短链重复发起跳转请求。
    canonical = canonicalize_douyin_content_url(source_url, resolve_short=False)
    video_id = str(canonical.get("content_id") or "")
    if not video_id:
        transfer_url = build_api_url(
            base_url,
            DOUYIN_TRANSFER_PATH,
            {"token": token, "shareUrl": source_url},
        )
        transfer = call_justoneapi(
            [transfer_url], max_attempts=3, retryable_codes={301}
        )
        video_id = text(first_path(transfer, (
            ("data", "videoId"),
            ("data", "video_id"),
            ("data", "aweme_id"),
            ("data", "item_id"),
        )))
    if not re.fullmatch(r"\d{15,24}", video_id):
        raise JustOneApiFetchError("douyin_video_id_missing")
    detail_url = build_api_url(
        base_url,
        DOUYIN_DETAIL_PATH,
        {
            "token": token,
            "videoId": video_id,
            "video_id": video_id,
            "aweme_id": video_id,
            "item_id": video_id,
        },
    )
    response = call_justoneapi([detail_url])
    payload = response.get("data") or response
    aweme = nested(payload, "aweme_detail") or payload
    author = nested(aweme, "author") or {}
    video = nested(aweme, "video") or {}
    statistics = nested(aweme, "statistics") or nested(aweme, "stats") or {}
    native_id = text(nested(aweme, "aweme_id")) or video_id
    return {
        "平台": "抖音",
        "平台原生内容ID": native_id,
        "账号ID": text(first_path(author, (("sec_uid",), ("uid",), ("unique_id",)))),
        "账号名": text(first_path(author, (("nickname",), ("name",)))),
        "标题": text(first_path(aweme, (("desc",), ("title",)))),
        "封面说明": "",
        "封面图原链接": text(first_path(video, (
            ("cover", "url_list", 0),
            ("origin_cover", "url_list", 0),
        ))),
        "视频下载链接": text(first_path(video, (
            ("play_addr", "url_list", 0),
            ("download_addr", "url_list", 0),
            ("play_addr", "url"),
            ("download_addr", "url"),
        ))),
        "媒介形式": "视频",
        "正文文本": "",
        "正文获取状态": "不适用",
        "点赞数": number_text(first_path(statistics, (("digg_count",), ("like_count",)))),
        "收藏数": number_text(first_path(statistics, (("collect_count",), ("collectCount",)))),
        "分享数": number_text(first_path(statistics, (("share_count",), ("shareCount",)))),
        "评论数": number_text(first_path(statistics, (("comment_count",), ("commentCount",)))),
        "发布时间": timestamp_text(first_path(aweme, (("create_time",), ("publish_time",)))),
        "对外页面链接": f"https://www.douyin.com/video/{native_id}",
        "B站视频标识": {"bvid": "", "aid": "", "cid": ""},
    }


def extract_xiaohongshu_id(source_url: str) -> str:
    match = re.search(
        r"/(?:explore|discovery/item)/([0-9A-Za-z_-]+)|/user/profile/[^/?#]+/([0-9A-Za-z_-]+)",
        source_url,
    )
    return (match.group(1) or match.group(2)) if match else ""


def fetch_xiaohongshu(base_url: str, token: str, source_url: str) -> dict[str, object]:
    if urllib.parse.urlsplit(source_url).netloc.lower().endswith("xhslink.com"):
        source_url = resolve_short_url(source_url)
        resolved_host = urllib.parse.urlsplit(source_url).netloc.lower()
        if "xiaohongshu.com" not in resolved_host:
            raise JustOneApiFetchError("xiaohongshu_short_url_target_invalid")
    note_id = extract_xiaohongshu_id(source_url)
    if not note_id:
        raise JustOneApiFetchError("xiaohongshu_note_id_missing")
    urls = [
        build_api_url(
            base_url,
            path,
            {"token": token, "noteId": note_id, "note_id": note_id, "url": source_url},
        )
        for path in XIAOHONGSHU_DETAIL_PATHS
    ]
    response = call_justoneapi(urls, accept=xiaohongshu_response_usable)
    payload = response.get("data") or response
    note = xiaohongshu_note_from_payload(payload)
    user = nested(note, "user") or nested(note, "author") or {}
    interaction = (
        nested(note, "interactInfo")
        or nested(note, "interact_info")
        or nested(note, "interactionInfo")
        or nested(note, "interaction_info")
        or {}
    )
    # 互动数位置各版本不同：v5 在 interact_info 里，v4/v2/v1 在笔记对象顶层
    # （liked_count / collected_count / comments_count / shared_count）。
    # 按字段逐个兜底，别整体二选一——同一版本可能只提供其中一部分。
    counter_sources = (interaction, note) if interaction else (note,)
    video = nested(note, "video") or {}
    native_id = text(first_path(note, (("note_id",), ("id",), ("item_id",)))) or note_id
    if not native_id:
        raise JustOneApiFetchError("xiaohongshu_note_id_missing")
    video_url = text(first_path(video, (
        ("media", "stream", "h264", 0, "master_url"),
        ("master_url",),
        ("video_url",),
        ("media", "stream", "h264", 0, "video_url"),
        ("consumer", "origin_video_key"),
    )))
    # v4/v2/v1 没有 cover 键，封面帧在 images_list[0]（字段是 url / original，没有 url_default）
    cover_url = text(first_path(note, (
        ("cover", "url_default"),
        ("cover", "url"),
        ("image_list", 0, "url_default"),
        ("image_list", 0, "url"),
        ("images_list", 0, "url_default"),
        ("images_list", 0, "url"),
        ("images_list", 0, "original"),
    )))
    note_type = text(first_path(note, (("type",), ("note_type",)))).lower()
    media_form = "视频" if video_url or note_type == "video" else "图文"
    body_paths = (("desc",), ("content",), ("text",))
    body_text = "" if media_form == "视频" else text(first_path(note, body_paths))
    body_field_present = any(nested(note, *path) is not None for path in body_paths)
    if media_form == "视频":
        body_status = "不适用"
    elif body_text:
        body_status = "已获取"
    else:
        body_status = "确认无正文" if body_field_present else "获取失败"

    def counter_text(paths: tuple[tuple[object, ...], ...]) -> str:
        for source in counter_sources:
            value = number_text(first_path(source, paths))
            if value:
                return value
        return ""

    return {
        "平台": "小红书",
        "平台原生内容ID": native_id,
        "账号ID": text(first_path(user, (("userId",), ("user_id",), ("userid",), ("id",)))),
        "账号名": text(first_path(user, (("nickname",), ("nick_name",), ("name",)))),
        "标题": text(first_path(note, (("title",), ("display_title",), ("desc",)))),
        "封面说明": "",
        "封面图原链接": cover_url,
        "视频下载链接": video_url,
        "媒介形式": media_form,
        "正文文本": body_text,
        "正文获取状态": body_status,
        "点赞数": counter_text((("liked_count",), ("likedCount",), ("like_count",))),
        "收藏数": counter_text((("collected_count",), ("collectedCount",), ("collect_count",))),
        "分享数": counter_text((("share_count",), ("shareCount",), ("shared_count",))),
        "评论数": counter_text((("comment_count",), ("commentCount",), ("comments_count",))),
        "发布时间": timestamp_text(first_path(note, (("time",), ("publish_time",), ("create_time",)))),
        "对外页面链接": xiaohongshu_page_url(native_id, source_url),
        "B站视频标识": {"bvid": "", "aid": "", "cid": ""},
    }


def extract_bvid(source_url: str) -> str:
    match = re.search(r"(BV[0-9A-Za-z]+)", source_url, flags=re.IGNORECASE)
    return match.group(1) if match else ""


def resolve_short_url(source_url: str) -> str:
    request = urllib.request.Request(
        source_url,
        headers={"User-Agent": "Mozilla/5.0"},
        method="GET",
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return str(response.geturl() or "").strip()
    except Exception as exc:  # noqa: BLE001
        code = permission_error_code(exc, "network")
        if code:
            raise JustOneApiFetchError(code) from exc
        raise JustOneApiFetchError(f"short_url_resolve_failed:{source_url}") from exc


def _bilibili_stream_url_complete(value: object) -> bool:
    """Accept only an intact signed DASH URL; preserve it byte-for-byte."""
    url = text(value)
    parsed = urllib.parse.urlsplit(url)
    if parsed.scheme.lower() not in {"http", "https"} or not parsed.netloc:
        return False
    if not parsed.query or not parsed.path.lower().endswith(".m4s"):
        return False
    query = {
        key.lower(): item
        for key, item in urllib.parse.parse_qsl(parsed.query, keep_blank_values=True)
    }
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


def _bilibili_stream_url_options(value: object) -> list[str]:
    """Normalize one URL or a B站 backup URL list without rebuilding it."""
    if isinstance(value, list):
        return [text(item) for item in value if text(item)]
    value_text = text(value)
    return [value_text] if value_text else []


def fetch_bilibili(base_url: str, token: str, source_url: str) -> dict[str, object]:
    bvid = extract_bvid(source_url)
    if not bvid and urllib.parse.urlsplit(source_url).netloc.lower().endswith("b23.tv"):
        source_url = resolve_short_url(source_url)
        bvid = extract_bvid(source_url)
    if not bvid:
        raise JustOneApiFetchError("bilibili_bvid_missing")
    detail_url = build_api_url(
        base_url,
        BILIBILI_DETAIL_PATH,
        {"token": token, "bvid": bvid, "aid": "", "cid": ""},
    )
    response = call_justoneapi([detail_url])
    payload = response.get("data") or response
    video_data = nested(payload, "videoData") or nested(payload, "video_data") or payload
    owner = nested(video_data, "owner") or nested(video_data, "author") or {}
    statistics = nested(video_data, "stat") or nested(video_data, "stats") or {}
    dash = nested(payload, "dash_info") or nested(payload, "dashInfo") or {}
    native_bvid = text(first_path(video_data, (("bvid",),))) or bvid
    aid = text(first_path(video_data, (("aid",),)))
    cid = text(first_path(video_data, (("cid",),))) or text(first_path(payload, (("cid",),)))
    first_video = first_path(dash, (
        ("dash", "video", 0, "base_url"),
        ("dash", "video", 0, "baseUrl"),
    ))
    backup_video = first_path(dash, (
        ("dash", "video", 0, "backup_url"),
        ("dash", "video", 0, "backupUrl"),
    ))
    video_url = next(
        (candidate for candidate in _bilibili_stream_url_options(first_video)
         if _bilibili_stream_url_complete(candidate)),
        "",
    )
    if not video_url:
        video_url = next(
            (candidate for candidate in _bilibili_stream_url_options(backup_video)
             if _bilibili_stream_url_complete(candidate)),
            "",
        )
    if not video_url:
        raise JustOneApiFetchError("bilibili_video_url_unavailable")
    return {
        "平台": "B站",
        "平台原生内容ID": native_bvid,
        "账号ID": text(first_path(owner, (("mid",), ("uid",), ("id",)))),
        "账号名": text(first_path(owner, (("name",), ("nickname",)))),
        "标题": text(first_path(video_data, (("title",),))),
        "封面说明": "",
        "封面图原链接": text(first_path(video_data, (("pic",), ("cover",)))),
        "视频下载链接": video_url,
        "媒介形式": "视频",
        "正文文本": "",
        "正文获取状态": "不适用",
        "点赞数": number_text(first_path(statistics, (("like",), ("like_count",)))),
        "收藏数": number_text(first_path(statistics, (("favorite",), ("collect_count",)))),
        "分享数": number_text(first_path(statistics, (("share",), ("share_count",)))),
        "评论数": number_text(first_path(statistics, (("reply",), ("comment_count",)))),
        "发布时间": timestamp_text(first_path(video_data, (("pubdate",), ("publish_time",)))),
        "对外页面链接": f"https://www.bilibili.com/video/{native_bvid}",
        "B站视频标识": {"bvid": native_bvid, "aid": aid, "cid": cid},
    }


def fetch(base_url: str, token: str, source_url: str) -> dict[str, object]:
    platform = detect_platform(source_url)
    if platform == "抖音":
        return fetch_douyin(base_url, token, source_url)
    if platform == "小红书":
        return fetch_xiaohongshu(base_url, token, source_url)
    return fetch_bilibili(base_url, token, source_url)


def run(args: argparse.Namespace) -> dict[str, object]:
    root = Path(args.workspace_root).expanduser().resolve()
    if not root.is_dir():
        raise JustOneApiFetchError(f"workspace_root_invalid:{root}")
    output_path = resolve_under(root, args.output_json)
    config = load_config(root / "本地配置.txt")
    token = config.get("JUSTONEAPI_TOKEN", "").strip()
    if not token:
        raise JustOneApiFetchError("JUSTONEAPI_TOKEN_missing")
    base_url = config.get("JUSTONEAPI_BASE_URL", "").strip() or "https://api.justoneapi.com"
    result = fetch(base_url, token, args.source_url.strip())
    result["请求页面链接"] = args.source_url.strip()
    required = (
        "平台", "平台原生内容ID", "标题", "媒介形式", "对外页面链接", "正文获取状态"
    )
    missing = [field for field in required if not text(result.get(field))]
    if missing:
        raise JustOneApiFetchError(f"required_identity_missing:{','.join(missing)}")
    body_text = text(result.get("正文文本"))
    body_status = text(result.get("正文获取状态"))
    media_form = text(result.get("媒介形式"))
    if media_form == "视频" and body_status != "不适用":
        raise JustOneApiFetchError("video_body_status_invalid")
    if media_form != "视频":
        if body_text and body_status != "已获取":
            raise JustOneApiFetchError("body_text_status_mismatch")
        if not body_text and body_status not in {"确认无正文", "获取失败"}:
            raise JustOneApiFetchError("empty_body_status_invalid")
    result = {"状态": "成功", "基础数据获取方式": "JustOneAPI", **result}
    result["输出路径"] = output_path.relative_to(root).as_posix()
    result["失败原因"] = ""
    write_json_atomically(output_path, result)
    return result


def main() -> int:
    args = parse_args()
    try:
        result = run(args)
    except (JustOneApiFetchError, OSError, ValueError) as exc:
        result = {
            "状态": "失败",
            "基础数据获取方式": "JustOneAPI",
            "请求页面链接": args.source_url.strip(),
            "失败原因": str(exc),
        }
        try:
            root = Path(args.workspace_root).expanduser().resolve()
            if root.is_dir():
                write_json_atomically(resolve_under(root, args.output_json), result)
        except (JustOneApiFetchError, OSError, ValueError):
            pass
        print(json.dumps(result, ensure_ascii=False))
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
