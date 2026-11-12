#!/usr/bin/env python3
"""搜索选题数据执行器：一轮平台搜索、结果归一化，并为指定内容补正文与一级评论。"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import sys
import tempfile
import urllib.parse
from pathlib import Path
from typing import Any, Iterable, Mapping

from 抖音内容链接解析 import canonicalize_douyin_content_url
from JustOneAPI单条内容抓取执行器 import (
    build_api_url,
    call_justoneapi,
    fetch as fetch_detail,
    first_path,
    load_config,
    number_text,
    text,
    timestamp_text,
)
from 内容评论抓取执行器 import collect_comments, fetch_justoneapi, load_chrome_payloads


WORKFLOW_ID = "topic-search"
SCHEMA_VERSION = "1"
PLATFORMS = ("小红书", "抖音", "B站")
SEARCH_PATHS = {
    "小红书": "/api/xiaohongshu/search-note/v2",
    "抖音": "/api/douyin/search-video/v4",
}
# 与账号方向直接相关的核心词；命中这些词的内容优先进入评论补取。
RELEVANCE_CORE_TERMS = (
    "工作台", "选题", "知识库", "自媒体", "博主", "起号", "创作者",
    "内容创作", "内容运营", "文案", "复盘", "个人ip", "个人IP",
)
# 明显偏离账号方向的品类词；只有品类词、没有核心词时判为不相关。
IRRELEVANT_CATEGORY_TERMS = (
    "comfyui", "ai生图", "ai绘图", "ai绘画", "短剧", "带货", "直播",
    "电商", "招聘", "游戏", "动漫", "影视解说", "小说推文", "壁纸", "头像",
)
# 只索取资料、没有提出具体问题的低信息评论。
COMMENT_ASSET_PATTERNS = (
    r"^(求|需要|想要|要)(一?份?|个)?\s*(蓝皮书|提示词|资料|模板|链接|文件|文档|skill|教程|工作台|源码|pdf|word)",
    r"^(蓝皮书|提示词|求分享|求资料|求模板|求链接|求文件|求教程|求skill|求源码|求带)$",
    r"(已关注|已点赞|已收藏|关注你了|点赞了|收藏了|码住|打卡|互赞|互关|留1|求回关|求带)",
)
COMMENT_GENERIC_PATTERNS = (
    r"^(谢谢|感谢|感谢分享|谢谢分享|支持|牛逼|太牛了|厉害|666|赞|好棒|学到了|收藏了|码住)$",
    r"^@[^\s]+$",
)
COMMENT_QUESTION_PATTERN = re.compile(
    r"[？?]|怎么|为什么|为啥|是不是|能不能|如何|哪里|哪个|什么|嘛|吗|问一下|请问|卡在|卡这|不会|不懂|求教"
)


class TopicSearchDataError(RuntimeError):
    pass


def root_path(value: str) -> Path:
    root = Path(value).expanduser().resolve()
    if not root.is_dir():
        raise TopicSearchDataError(f"workspace_root_invalid:{root}")
    return root


def inside(root: Path, raw: str, label: str) -> Path:
    candidate = Path(raw).expanduser()
    path = candidate.resolve() if candidate.is_absolute() else (root / candidate).resolve()
    try:
        path.relative_to(root)
    except ValueError as exc:
        raise TopicSearchDataError(f"{label}_outside_workspace") from exc
    if path.is_symlink() or not path.is_file():
        raise TopicSearchDataError(f"{label}_missing:{path}")
    return path


def logical(root: Path, path: Path) -> str:
    return path.resolve().relative_to(root).as_posix()


def read_json(path: Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise TopicSearchDataError(f"{label}_invalid:{exc}") from exc
    if not isinstance(value, dict):
        raise TopicSearchDataError(f"{label}_not_object")
    return value


def write_json(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(value, handle, ensure_ascii=False, indent=2, allow_nan=False)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def load_plan(root: Path, raw: str) -> tuple[Path, dict[str, Any]]:
    path = inside(root, raw, "plan_file")
    plan = read_json(path, "plan_file")
    if plan.get("schema_version") != SCHEMA_VERSION or plan.get("workflow_id") != WORKFLOW_ID:
        raise TopicSearchDataError("plan_contract_invalid")
    if not isinstance(plan.get("queries"), list) or not plan["queries"]:
        raise TopicSearchDataError("plan_queries_missing")
    if plan.get("data_source") not in {"chrome_devtools", "justoneapi"}:
        raise TopicSearchDataError("plan_data_source_invalid")
    return path, plan


XHS_TOKEN_PATTERN = re.compile(r"[?&]xsec_token=([^&#]+)")


def xhs_token_from(value: str) -> str:
    """从带 xsec_token 的小红书链接里取出 token，没有就返回空。"""
    match = XHS_TOKEN_PATTERN.search(str(value or ""))
    return urllib.parse.unquote(match.group(1)) if match else ""


def xhs_content_url(native_id: str, token: str, title: str) -> str:
    """小红书笔记页必须带 xsec_token，否则别人打开是「页面不见了」。

    token 是搜索页下发的短时票；没有 token 时退回搜索结果页，
    虽然还要登录才能看，但至少不会落到 404。
    """
    if token:
        return f"https://www.xiaohongshu.com/explore/{native_id}?xsec_token={token}&xsec_source=pc_search"
    if title:
        return f"https://www.xiaohongshu.com/search_result?keyword={urllib.parse.quote(title)}"
    return f"https://www.xiaohongshu.com/explore/{native_id}"


def canonical_content_url(
    platform: str,
    native_id: str,
    raw_url: str,
    token: str = "",
    title: str = "",
) -> str:
    """统一存储链接：抖音复用现成解析器，小红书保留 xsec_token，B站固定到 video 页。"""
    if platform == "抖音":
        canonical = canonicalize_douyin_content_url(raw_url, resolve_short=False)
        if canonical.get("content_id"):
            return str(canonical["canonical_url"])
        return f"https://www.douyin.com/video/{native_id}"
    if platform == "小红书":
        return xhs_content_url(native_id, text(token) or xhs_token_from(raw_url), title)
    if platform == "B站":
        return f"https://www.bilibili.com/video/{native_id}"
    return str(raw_url or "").strip()


def relevance_for(title: str, body: str) -> dict[str, str]:
    """标记搜索结果与本账号方向的相关度，避免把品类内容当成需求证据。"""
    text = f"{title} {body}".casefold()
    core_hits = [term for term in RELEVANCE_CORE_TERMS if term.casefold() in text]
    category_hits = [term for term in IRRELEVANT_CATEGORY_TERMS if term.casefold() in text]
    if category_hits and not core_hits:
        return {"level": "irrelevant", "reason": f"只有品类词：{'、'.join(category_hits[:3])}"}
    if core_hits:
        return {"level": "relevant", "reason": f"命中核心词：{'、'.join(core_hits[:3])}"}
    return {"level": "weak", "reason": "没有命中账号核心词，只能作为弱相关线索"}


def strip_comment_noise(value: str) -> str:
    text = re.sub(r"\[[^\]]{1,12}\]", "", value or "")
    text = re.sub(r"[\s\W_]+", "", text, flags=re.UNICODE)
    return text


def comment_is_low_information(value: str) -> bool:
    """过滤索取资料、纯表情、纯赞美等低信息互动；提出具体问题的保留。"""
    raw = re.sub(r"\s+", " ", str(value or "")).strip()
    normalized = strip_comment_noise(raw)
    if not normalized:
        return True
    if any(re.fullmatch(pattern, normalized, flags=re.IGNORECASE) for pattern in COMMENT_GENERIC_PATTERNS):
        return True
    if any(re.search(pattern, normalized, flags=re.IGNORECASE) for pattern in COMMENT_ASSET_PATTERNS):
        return not bool(COMMENT_QUESTION_PATTERN.search(normalized))
    if len(normalized) <= 3:
        return not bool(COMMENT_QUESTION_PATTERN.search(normalized))
    return False


def counter(sources: Iterable[Mapping[str, Any]], paths: tuple[tuple[object, ...], ...]) -> str:
    for source in sources:
        value = number_text(first_path(source, paths))
        if value:
            return value
    return ""


def normalize_native_content(platform: str, raw_item: Mapping[str, Any], query_id: str) -> dict[str, Any] | None:
    item = raw_item.get("note_card") or raw_item.get("aweme_detail") or raw_item
    if not isinstance(item, Mapping):
        return None
    if platform == "小红书":
        native_id = text(first_path(item, (("note_id",), ("id",), ("item_id",))))
        user = item.get("user") or item.get("author") or {}
        interaction = item.get("interactInfo") or item.get("interact_info") or item.get("interactionInfo") or {}
        sources = (interaction, item) if isinstance(interaction, Mapping) else (item,)
        title = text(first_path(item, (("title",), ("display_title",), ("desc",))))
        body = text(first_path(item, (("desc",), ("content",), ("text",))))
        metrics = {
            "likes": counter(sources, (("liked_count",), ("likedCount",), ("like_count",))),
            "collects": counter(sources, (("collected_count",), ("collectedCount",), ("collect_count",))),
            "comments": counter(sources, (("comment_count",), ("commentCount",), ("comments_count",))),
            "shares": counter(sources, (("share_count",), ("shareCount",), ("shared_count",))),
            "views": counter(sources, (("view_count",), ("views",), ("read_count",))),
        }
        author_name = text(first_path(user, (("nickname",), ("nick_name",), ("name",)))) if isinstance(user, Mapping) else ""
        author_id = text(first_path(user, (("userId",), ("user_id",), ("userid",), ("id",)))) if isinstance(user, Mapping) else ""
        publish_time = timestamp_text(first_path(item, (("time",), ("publish_time",), ("create_time",))))
        raw_url = text(first_path(item, (("url",), ("share_url",), ("link",))))
        token = text(first_path(item, (("xsec_token",), ("xsecToken",), ("xsec_token_str",))))
        url = canonical_content_url(platform, native_id, raw_url, token, title)
    else:
        native_id = text(first_path(item, (("aweme_id",), ("item_id",), ("id",))))
        author = item.get("author") or item.get("user") or {}
        statistics = item.get("statistics") or item.get("stats") or item
        sources = (statistics, item) if isinstance(statistics, Mapping) else (item,)
        title = text(first_path(item, (("desc",), ("title",))))
        body = text(first_path(item, (("desc",), ("content",), ("text",))))
        metrics = {
            "likes": counter(sources, (("digg_count",), ("like_count",))),
            "collects": counter(sources, (("collect_count",), ("collectCount",))),
            "comments": counter(sources, (("comment_count",), ("commentCount",))),
            "shares": counter(sources, (("share_count",), ("shareCount",))),
            "views": counter(sources, (("play_count",), ("view_count",))),
        }
        author_name = text(first_path(author, (("nickname",), ("name",)))) if isinstance(author, Mapping) else ""
        author_id = text(first_path(author, (("sec_uid",), ("uid",), ("unique_id",)))) if isinstance(author, Mapping) else ""
        publish_time = timestamp_text(first_path(item, (("create_time",), ("publish_time",))))
        raw_url = text(first_path(item, (("share_url",), ("url",), ("link",))))
        url = canonical_content_url(platform, native_id, raw_url)
    if not native_id or not title:
        return None
    return {
        "content_id": f"{platform}:{native_id}",
        "platform": platform,
        "native_id": native_id,
        "title": title,
        "body": body,
        "author_name": author_name,
        "author_id": author_id,
        "publish_time": publish_time,
        "url": url,
        "metrics": metrics,
        "relevance_hint": relevance_for(title, body),
        "query_ids": [query_id],
        "评论状态": "未抓取",
        "评论条数": 0,
        "评论": [],
        "评论错误": "",
    }


def normalize_chrome_content(raw_item: Mapping[str, Any]) -> dict[str, Any] | None:
    platform = text(raw_item.get("platform"))
    query_id = text(raw_item.get("query_id"))
    if platform not in PLATFORMS or not query_id:
        return None
    native_id = text(raw_item.get("native_id"))
    title = text(raw_item.get("title"))
    if not native_id or not title:
        return None
    metrics = raw_item.get("metrics") if isinstance(raw_item.get("metrics"), Mapping) else {}
    body = text(raw_item.get("body"))
    return {
        "content_id": f"{platform}:{native_id}",
        "platform": platform,
        "native_id": native_id,
        "title": title,
        "body": body,
        "author_name": text(raw_item.get("author_name")),
        "author_id": text(raw_item.get("author_id")),
        "publish_time": text(raw_item.get("publish_time")),
        "url": canonical_content_url(
            platform,
            native_id,
            text(raw_item.get("url")),
            text(raw_item.get("xsec_token")),
            title,
        ),
        "metrics": {key: number_text(metrics.get(key)) for key in ("likes", "collects", "comments", "shares", "views")},
        "relevance_hint": relevance_for(title, body),
        "query_ids": [query_id],
        "评论状态": "未抓取",
        "评论条数": 0,
        "评论": [],
        "评论错误": "",
    }


BILIBILI_TAG_PATTERN = re.compile(r"<[^>]+>")


def looks_like_douyin_search(payload: Mapping[str, Any]) -> bool:
    """抖音搜索接口 /aweme/v1/web/general/search/single/ 的响应：data[] 里每条带 aweme_info。"""
    rows = payload.get("data")
    if not isinstance(rows, list):
        return False
    return any(
        isinstance(row, Mapping) and isinstance(row.get("aweme_info") or row.get("aweme_detail"), Mapping)
        for row in rows
    )


def looks_like_bilibili_search(payload: Mapping[str, Any]) -> bool:
    """B站搜索接口的响应：data.result[] 或 data.list[] 里每条带 bvid。"""
    data = payload.get("data")
    if not isinstance(data, Mapping):
        return False
    rows = data.get("result") if isinstance(data.get("result"), list) else data.get("list")
    if not isinstance(rows, list):
        return False
    return any(isinstance(row, Mapping) and row.get("bvid") for row in rows)


def normalize_bilibili_content(raw_item: Mapping[str, Any], query_id: str) -> dict[str, Any] | None:
    """B站搜索结果里标题带 <em> 高亮标签，入库前必须剥掉。"""
    native_id = text(raw_item.get("bvid"))
    title = BILIBILI_TAG_PATTERN.sub("", text(raw_item.get("title"))).strip()
    body = text(raw_item.get("description")) or text(raw_item.get("desc"))
    if not native_id or not title:
        return None
    metrics = {
        "likes": number_text(raw_item.get("like")),
        "collects": number_text(raw_item.get("favorites")),
        "comments": number_text(raw_item.get("video_review") or raw_item.get("review")),
        "shares": "",
        "views": number_text(raw_item.get("play")),
    }
    return {
        "content_id": f"B站:{native_id}",
        "platform": "B站",
        "native_id": native_id,
        "title": title,
        "body": body,
        "author_name": text(raw_item.get("author")),
        "author_id": text(raw_item.get("mid")),
        "publish_time": timestamp_text(raw_item.get("pubdate")),
        "url": canonical_content_url("B站", native_id, text(raw_item.get("arcurl"))),
        "metrics": metrics,
        "relevance_hint": relevance_for(title, body),
        "query_ids": [query_id],
        "评论状态": "未抓取",
        "评论条数": 0,
        "评论": [],
        "评论错误": "",
    }


def platform_rows_from_payload(payload: Mapping[str, Any], platform: str, query_id: str) -> list[dict[str, Any]]:
    """把平台自己的搜索接口响应直接转成标准内容行，省掉人工从页面抄字段这一步。"""
    rows: list[dict[str, Any]] = []
    if platform == "抖音" and looks_like_douyin_search(payload):
        for item in payload.get("data") or []:
            if not isinstance(item, Mapping):
                continue
            aweme = item.get("aweme_info") or item.get("aweme_detail")
            if not isinstance(aweme, Mapping):
                continue
            content = normalize_native_content("抖音", aweme, query_id)
            if content is not None:
                rows.append(content)
        return rows
    if platform == "B站" and looks_like_bilibili_search(payload):
        data = payload.get("data") or {}
        candidates = data.get("result") if isinstance(data.get("result"), list) else data.get("list")
        for item in candidates or []:
            if not isinstance(item, Mapping):
                continue
            content = normalize_bilibili_content(item, query_id)
            if content is not None:
                rows.append(content)
        return rows
    return rows


def iter_native_items(value: Any, platform: str) -> Iterable[Mapping[str, Any]]:
    if isinstance(value, Mapping):
        identity_keys = ("note_id", "id", "item_id") if platform == "小红书" else ("aweme_id", "item_id", "id")
        title_keys = ("title", "display_title", "desc")
        if any(value.get(key) for key in identity_keys) and any(value.get(key) for key in title_keys):
            yield value
        for child in value.values():
            yield from iter_native_items(child, platform)
    elif isinstance(value, list):
        for child in value:
            yield from iter_native_items(child, platform)


def new_document(plan: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "workflow_id": WORKFLOW_ID,
        "run_id": plan["run_id"],
        "data_source": plan["data_source"],
        "generated_at": dt.datetime.now().astimezone().replace(microsecond=0).isoformat(),
        "queries": plan["queries"],
        "contents": [],
        "errors": [],
    }


def merge_content(document: dict[str, Any], content: Mapping[str, Any]) -> None:
    existing = next((item for item in document["contents"] if item["content_id"] == content["content_id"]), None)
    if existing is None:
        document["contents"].append(dict(content))
        return
    existing["query_ids"] = sorted(set(existing["query_ids"]) | set(content["query_ids"]))
    for field in ("body", "author_name", "author_id", "publish_time"):
        if not existing.get(field) and content.get(field):
            existing[field] = content[field]
    if content.get("url"):
        existing["url"] = canonical_content_url(
            content["platform"],
            content["native_id"],
            content["url"],
            text(content.get("xsec_token")),
            text(content.get("title")),
        )
    if content.get("relevance_hint"):
        existing["relevance_hint"] = content["relevance_hint"]
    for key, value in content["metrics"].items():
        if not existing["metrics"].get(key) and value:
            existing["metrics"][key] = value


def search(root: Path, plan_path: Path, plan: Mapping[str, Any]) -> dict[str, Any]:
    if plan["data_source"] != "justoneapi":
        raise TopicSearchDataError("chrome_search_requires_normalize_action")
    config = load_config(root / "本地配置.txt")
    token = text(config.get("JUSTONEAPI_TOKEN"))
    if not token:
        raise TopicSearchDataError("justoneapi_token_missing")
    base_url = text(config.get("JUSTONEAPI_BASE_URL")) or "https://api.justoneapi.com"
    document = new_document(plan)
    raw_dir = plan_path.parent / "原始响应"
    for query in plan["queries"]:
        for platform in query["platforms"]:
            if platform not in PLATFORMS:
                continue
            if platform not in SEARCH_PATHS:
                # B站没有对应的 JustOneAPI 搜索接口，只能走 Chrome 取数
                document["errors"].append({"query_id": query["query_id"], "platform": platform, "error": "platform_not_supported_by_justoneapi"})
                continue
            params = {"token": token, "keyword": query["keyword"], "page": "1"}
            if platform == "小红书":
                params["sort"] = "general"
            else:
                params["sortType"] = "_0"
            url = build_api_url(base_url, SEARCH_PATHS[platform], params)
            try:
                response = call_justoneapi([url], max_attempts=2, retryable_codes={301, 302})
                write_json(raw_dir / f"{query['query_id']}-{platform}.json", response)
                before = len(document["contents"])
                for raw_item in iter_native_items(response, platform):
                    content = normalize_native_content(platform, raw_item, query["query_id"])
                    if content is not None:
                        merge_content(document, content)
                if len(document["contents"]) == before:
                    document["errors"].append({"query_id": query["query_id"], "platform": platform, "error": "no_normalized_results"})
            except Exception as exc:  # 单查询失败不抹掉其他平台已经获得的结果
                document["errors"].append({"query_id": query["query_id"], "platform": platform, "error": str(exc)})
    if not document["contents"]:
        raise TopicSearchDataError(f"search_empty:{document['errors']}")
    output_path = plan_path.with_name("选题证据.json")
    write_json(output_path, document)
    write_json(plan_path.with_name("搜索结果.json"), document)
    return {"ok": True, "status": "search_completed", "evidence_file": logical(root, output_path), "content_count": len(document["contents"]), "errors": document["errors"]}


def seed_from_existing(document: dict[str, Any], plan_path: Path, plan: Mapping[str, Any]) -> int:
    """同一个 run 里分多次 normalize（抖音、B站、小红书各来一次）时，把已有结果接上，
    避免后一次把前一次的平台结果冲掉。run_id 不一致就当作旧数据，不接。"""
    path = plan_path.with_name("选题证据.json")
    if not path.is_file():
        return 0
    try:
        existing = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return 0
    if not isinstance(existing, dict) or existing.get("run_id") != plan["run_id"]:
        return 0
    rows = existing.get("contents")
    if not isinstance(rows, list):
        return 0
    count = 0
    for item in rows:
        if isinstance(item, dict) and item.get("content_id"):
            document["contents"].append(item)
            count += 1
    return count


def normalize(
    root: Path,
    plan_path: Path,
    plan: Mapping[str, Any],
    raw_input: str,
    query_id: str = "",
    platform: str = "",
) -> dict[str, Any]:
    if plan["data_source"] != "chrome_devtools":
        raise TopicSearchDataError("justoneapi_requires_search_action")
    raw_path = inside(root, raw_input, "raw_input")
    payload = read_json(raw_path, "raw_input")
    allowed_queries = {item["query_id"]: item for item in plan["queries"]}
    document = new_document(plan)
    seed_from_existing(document, plan_path, plan)
    rows = payload.get("contents")
    if not isinstance(rows, list):
        # 不是统一合同，就按平台自己的搜索接口响应解析：抖音 aweme_info / B站 bvid
        target_query = text(query_id)
        target_platform = text(platform)
        if not target_query or not target_platform:
            raise TopicSearchDataError("raw_input_contents_missing")
        if target_query not in allowed_queries:
            raise TopicSearchDataError(f"query_id_not_in_plan:{target_query}")
        if target_platform not in PLATFORMS:
            raise TopicSearchDataError(f"platform_invalid:{target_platform}")
        if target_platform not in allowed_queries[target_query]["platforms"]:
            raise TopicSearchDataError(f"platform_not_in_query:{target_query}:{target_platform}")
        parsed = platform_rows_from_payload(payload, target_platform, target_query)
        if not parsed:
            raise TopicSearchDataError(f"raw_input_unsupported:{target_platform}")
        for content in parsed:
            merge_content(document, content)
        if not document["contents"]:
            raise TopicSearchDataError("normalize_empty")
        output_path = plan_path.with_name("选题证据.json")
        write_json(output_path, document)
        write_json(plan_path.with_name("搜索结果.json"), document)
        return {
            "ok": True,
            "status": "normalized",
            "evidence_file": logical(root, output_path),
            "content_count": len(document["contents"]),
            "parsed_from": f"{target_platform}平台响应",
            "errors": document["errors"],
        }
    for row in rows:
        if not isinstance(row, Mapping):
            continue
        content = normalize_chrome_content(row)
        if content is None:
            document["errors"].append({"error": "invalid_chrome_content", "native_id": text(row.get("native_id"))})
            continue
        if content["query_ids"][0] not in allowed_queries:
            document["errors"].append({"error": "query_id_not_in_plan", "query_id": content["query_ids"][0]})
            continue
        merge_content(document, content)
    if not document["contents"]:
        raise TopicSearchDataError("normalize_empty")
    output_path = plan_path.with_name("选题证据.json")
    write_json(output_path, document)
    write_json(plan_path.with_name("搜索结果.json"), document)
    return {"ok": True, "status": "normalized", "evidence_file": logical(root, output_path), "content_count": len(document["contents"]), "errors": document["errors"]}


def comment_count(value: Any) -> int:
    try:
        return int(float(str(value or "0").replace(",", "")))
    except ValueError:
        return 0


def apply_comment_filter(content: dict[str, Any], comments: list[dict[str, Any]]) -> None:
    """只保留有具体问题或经验的评论，并记录过滤前后的数量。"""
    raw_count = len(comments)
    kept: list[dict[str, Any]] = []
    seen: set[str] = set()
    for comment in comments:
        body = str(comment.get("正文") or "")
        if comment_is_low_information(body):
            continue
        key = strip_comment_noise(body).casefold()
        if key in seen:
            continue
        seen.add(key)
        kept.append(comment)
    content["评论"] = kept
    content["评论条数"] = len(kept)
    content["原始评论条数"] = raw_count
    content["低信息评论条数"] = raw_count - len(kept)
    content["评论状态"] = "已获取" if kept else "本轮未取到有效评论"


def enrich(root: Path, plan_path: Path, plan: Mapping[str, Any], content_ids: str) -> dict[str, Any]:
    evidence_path = plan_path.with_name("选题证据.json")
    if not evidence_path.is_file():
        raise TopicSearchDataError("evidence_file_missing_run_search_first")
    document = read_json(evidence_path, "evidence_file")
    selected = [item.strip() for item in content_ids.split(",") if item.strip()]
    if not selected or len(set(selected)) != len(selected):
        raise TopicSearchDataError("content_ids_invalid")
    by_id = {item["content_id"]: item for item in document["contents"]}
    missing = [item for item in selected if item not in by_id]
    if missing:
        raise TopicSearchDataError(f"content_ids_not_in_results:{missing}")
    config = load_config(root / "本地配置.txt") if plan["data_source"] == "justoneapi" else {}
    token = text(config.get("JUSTONEAPI_TOKEN"))
    base_url = text(config.get("JUSTONEAPI_BASE_URL")) or "https://api.justoneapi.com"
    completed = 0
    for content_id in selected:
        content = by_id[content_id]
        if plan["data_source"] == "justoneapi":
            if not token:
                raise TopicSearchDataError("justoneapi_token_missing")
            if not content["body"]:
                try:
                    detail = fetch_detail(base_url, token, content["url"])
                    content["body"] = text(detail.get("正文文本"))
                except Exception as exc:
                    content["详情错误"] = str(exc)
            if comment_count(content["metrics"].get("comments")) == 0 and text(content["metrics"].get("comments")):
                content["评论状态"] = "评论为0"
                content["评论"] = []
                content["评论条数"] = 0
                content["原始评论条数"] = 0
                content["低信息评论条数"] = 0
                continue
            try:
                payloads = fetch_justoneapi(root, content["platform"], content["native_id"], 1)
                comments, _ = collect_comments(content["platform"], "justoneapi", payloads)
                apply_comment_filter(content, comments)
                content["评论错误"] = ""
                completed += 1
            except Exception as exc:
                content["评论状态"] = "获取失败"
                content["评论错误"] = str(exc)
        else:
            raw_path = plan_path.parent / "评论原始" / f"{content['native_id']}.json"
            if not raw_path.is_file():
                content["评论状态"] = "未采集"
                content["评论错误"] = "缺少页面评论原始响应"
                continue
            try:
                payloads = load_chrome_payloads(raw_path)
                comments, _ = collect_comments(content["platform"], "chrome_devtools", payloads)
                apply_comment_filter(content, comments)
                content["评论错误"] = ""
                completed += 1
            except Exception as exc:
                content["评论状态"] = "获取失败"
                content["评论错误"] = str(exc)
    document["generated_at"] = dt.datetime.now().astimezone().replace(microsecond=0).isoformat()
    write_json(evidence_path, document)
    return {"ok": True, "status": "evidence_ready", "evidence_file": logical(root, evidence_path), "selected_count": len(selected), "comment_fetch_completed": completed, "action_contract": {"candidate_path": logical(root, plan_path.with_name("选题推荐候选.json"))}}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="搜索选题数据执行器")
    parser.add_argument("--workspace-root", required=True)
    parser.add_argument("--action", choices=("search", "normalize", "enrich"), required=True)
    parser.add_argument("--plan-file", required=True)
    parser.add_argument("--raw-input", default="")
    parser.add_argument("--content-ids", default="")
    parser.add_argument("--query-id", default="", help="raw-input 是平台原生搜索响应时，指定它属于哪个 query_id")
    parser.add_argument("--platform", default="", help="raw-input 是平台原生搜索响应时，指定平台（抖音 / B站）")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        root = root_path(args.workspace_root)
        plan_path, plan = load_plan(root, args.plan_file)
        if args.action == "search":
            result = search(root, plan_path, plan)
        elif args.action == "normalize":
            if not args.raw_input:
                raise TopicSearchDataError("raw_input_required")
            result = normalize(root, plan_path, plan, args.raw_input, args.query_id, args.platform)
        else:
            result = enrich(root, plan_path, plan, args.content_ids)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except (TopicSearchDataError, OSError, ValueError) as exc:
        print(json.dumps({"ok": False, "status": "blocked", "error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
