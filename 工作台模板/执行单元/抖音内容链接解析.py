"""抖音内容页面链接的统一解析与归一化。"""

from __future__ import annotations

import re
import urllib.parse
import urllib.request
from typing import Any

import 证书信任  # noqa: F401  进程级统一 HTTPS 证书信任


DOUYIN_CONTENT_ID_RE = re.compile(r"\d{15,24}")
DOUYIN_HOSTS = {"douyin.com", "www.douyin.com", "iesdouyin.com", "www.iesdouyin.com"}
DOUYIN_SHORT_HOST = "v.douyin.com"
_ID_QUERY_KEYS = ("vid", "modal_id", "aweme_id", "item_id")


class DouyinLinkError(ValueError):
    """抖音链接无法解析或短链跳转失败。"""


def _valid_id(value: Any) -> str:
    text = str(value or "").strip()
    return text if DOUYIN_CONTENT_ID_RE.fullmatch(text) else ""


def _hostname(url: str) -> str:
    return (urllib.parse.urlsplit(url).hostname or "").lower().rstrip(".")


def resolve_douyin_short_url(url: str, *, timeout: int = 30) -> str:
    """跟随抖音分享短链，返回最终 HTTP(S) 地址。"""
    source = str(url or "").strip()
    parsed = urllib.parse.urlsplit(source)
    if parsed.scheme.lower() not in {"http", "https"} or _hostname(source) != DOUYIN_SHORT_HOST:
        raise DouyinLinkError("douyin_short_url_invalid")
    request = urllib.request.Request(source, headers={"User-Agent": "Mozilla/5.0"}, method="GET")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            final_url = str(response.geturl() or "").strip()
    except Exception as exc:  # noqa: BLE001
        raise DouyinLinkError(f"douyin_short_url_resolve_failed:{exc}") from exc
    if not final_url or urllib.parse.urlsplit(final_url).scheme.lower() not in {"http", "https"}:
        raise DouyinLinkError("douyin_short_url_target_invalid")
    return final_url


def _candidate_ids(url: str) -> list[tuple[str, str]]:
    parsed = urllib.parse.urlsplit(url)
    path_match = re.search(r"/(?:video|note)/(\d{15,24})(?:/|$)", parsed.path, flags=re.IGNORECASE)
    path_id = _valid_id(path_match.group(1) if path_match else "")
    candidates: list[tuple[str, str]] = []
    if path_id:
        candidates.append(("path", path_id))
    query = urllib.parse.parse_qsl(parsed.query, keep_blank_values=False)
    by_key: dict[str, str] = {}
    for key, value in query:
        lowered = key.lower()
        if lowered in _ID_QUERY_KEYS and lowered not in by_key:
            candidate = _valid_id(value)
            if candidate:
                by_key[lowered] = candidate
    candidates.extend((key, by_key[key]) for key in _ID_QUERY_KEYS if key in by_key)
    return candidates


def extract_douyin_content_id(url: Any, *, resolve_short: bool = True) -> str:
    """按固定优先级提取内容 ID；数字字符串也可直接传入。"""
    text = str(url or "").strip()
    direct = _valid_id(text)
    if direct:
        return direct
    if not text:
        return ""
    host = _hostname(text)
    if host not in DOUYIN_HOSTS and host != DOUYIN_SHORT_HOST:
        return ""
    candidates = _candidate_ids(text)
    if candidates and candidates[0][0] == "path":
        return candidates[0][1]
    if candidates:
        return candidates[0][1]
    if resolve_short and host == DOUYIN_SHORT_HOST:
        try:
            return extract_douyin_content_id(resolve_douyin_short_url(text), resolve_short=False)
        except DouyinLinkError:
            return ""
    return ""


def canonicalize_douyin_content_url(url: Any, *, resolve_short: bool = True) -> dict[str, object]:
    """返回统一内容身份；没有内容 ID 的账号主页不会被改写成视频页。"""
    source = str(url or "").strip()
    result: dict[str, object] = {
        "platform": "抖音",
        "content_id": "",
        "canonical_url": "",
        "source_type": "",
        "conflict": False,
        "warning": "",
    }
    if not source:
        return result
    source_host = _hostname(source)
    if source_host not in DOUYIN_HOSTS and source_host != DOUYIN_SHORT_HOST:
        return result
    resolved = source
    if resolve_short and source_host == DOUYIN_SHORT_HOST:
        try:
            resolved = resolve_douyin_short_url(source)
        except DouyinLinkError as exc:
            result["warning"] = str(exc)
            return result
    if _hostname(resolved) not in DOUYIN_HOSTS:
        result["warning"] = "douyin_short_url_target_invalid"
        return result
    candidates = _candidate_ids(resolved)
    if not candidates:
        result["canonical_url"] = resolved if resolved.startswith(("http://", "https://")) else ""
        return result
    chosen_source, content_id = candidates[0]
    unique_ids = {candidate_id for _, candidate_id in candidates}
    conflict = len(unique_ids) > 1
    result.update(
        {
            "content_id": content_id,
            "canonical_url": f"https://www.douyin.com/video/{content_id}",
            "source_type": chosen_source,
            "conflict": conflict,
            "warning": (
                f"抖音链接存在多个不一致内容 ID，已按优先级使用 {chosen_source}: {content_id}"
                if conflict
                else ""
            ),
        }
    )
    return result


__all__ = [
    "DouyinLinkError",
    "canonicalize_douyin_content_url",
    "extract_douyin_content_id",
    "resolve_douyin_short_url",
]
