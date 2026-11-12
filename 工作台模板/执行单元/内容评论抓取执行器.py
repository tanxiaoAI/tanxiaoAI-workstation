#!/usr/bin/env python3
"""评论原文抓取执行器（一轮抓取、去重追加、直接写正式内容目录）。

职责边界：
- 只做「取原始评论 -> 规范化 -> 去重合并 -> 写正式存档 -> 出回执」，不判断内容身份、不改内容库。
- 只抓一级评论；一级评论响应里自带的少量二级回复随正文一并保留，不额外请求楼中楼。
- 正式产物固定写在调用方传入的 --content-dir 下：评论.md（人读）、评论.json（机读全量）。
- 续拉必须与已存档的数据源一致；来源不同直接报错停止，不静默混源。

数据源分工（与既有链路一致）：
- justoneapi：本执行器直接调接口翻页。
- chrome_devtools：取数由流程执行者按 执行流程/对标内容数据获取流程.md 的 2.8 步骤完成，
  本执行器只读取其落盘的原始响应文件。
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import sys
from pathlib import Path
from typing import Any, Iterable, Mapping


COMMENT_MARKDOWN_NAME = "评论.md"
COMMENT_JSON_NAME = "评论.json"
SUPPORTED_PLATFORMS = ("小红书", "抖音", "B站")
SOURCES = ("chrome_devtools", "justoneapi")
# 平台声明评论数为 0 时的回执状态：不发起抓取，但照样产出存档，
# 让下游能区分「这条内容真的没有评论」和「抓取失败 / 未取到」。
STATUS_SKIPPED_ZERO = "skipped_zero_comments"

# 每轮抓取页数：Chrome 免费但慢，放宽到 3 页；JustOneAPI 按请求计费，严格 1 页。
DEFAULT_PAGES = {"chrome_devtools": 3, "justoneapi": 1}

JUSTONEAPI_PATHS = {
    "抖音": "/api/douyin/get-video-comment/v1",
    "小红书": "/api/xiaohongshu/get-note-comment/v2",
    "B站": "/api/bilibili/get-video-comment/v2",
}
# 各接口的分页参数名不同：抖音用 page、小红书用 lastCursor、B站用 page。
JUSTONEAPI_PAGE_PARAM = {"抖音": "page", "小红书": "lastCursor", "B站": "page"}
JUSTONEAPI_ID_PARAM = {"抖音": "awemeId", "小红书": "noteId", "B站": "aid"}

BILIBILI_VIEW_API = "https://api.bilibili.com/x/web-interface/view"

COMMENT_LIST_KEYS = ("comments", "replies", "comment_list", "comment_info_list", "list", "items")
CONTAINER_KEYS = ("data", "result", "response", "payload", "cursor")

# 一条评论在存档里的字段集合；读旧档时按它过滤，免得历史版本的废弃字段被带进新档。
COMMENT_FIELDS = (
    "评论ID", "正文", "作者昵称", "作者ID",
    "点赞数", "回复数", "发布时间", "IP属地", "平台", "数据源",
)


class CommentFetchError(RuntimeError):
    """评论抓取阶段的确定性失败；消息即错误码。"""


# ---------------------------------------------------------------------------
# 基础工具
# ---------------------------------------------------------------------------

def resolve_under(root: Path, raw: str, *, label: str) -> Path:
    text = str(raw or "").strip()
    if not text:
        raise CommentFetchError(f"{label}_missing")
    candidate = Path(text).expanduser()
    path = candidate.resolve() if candidate.is_absolute() else (root / candidate).resolve()
    try:
        path.relative_to(root)
    except ValueError as exc:
        raise CommentFetchError(f"{label}_outside_workspace") from exc
    return path


def relative_path(root: Path, path: Path) -> str:
    return path.resolve().relative_to(root).as_posix()


def now_iso() -> str:
    return dt.datetime.now().astimezone().replace(microsecond=0).isoformat()


def format_timestamp(value: Any) -> str:
    """把平台给的时间戳统一成 `YYYY-MM-DD HH:MM:SS`；无法识别时原样返回字符串。"""
    if value in (None, ""):
        return ""
    number: float | None = None
    if isinstance(value, (int, float)):
        number = float(value)
    else:
        text = str(value).strip()
        if text.isdigit():
            number = float(text)
        else:
            return text
    if number is None:
        return ""
    while number > 10_000_000_000:  # 毫秒时间戳降级到秒
        number /= 1000.0
    try:
        return dt.datetime.fromtimestamp(number).strftime("%Y-%m-%d %H:%M:%S")
    except (OverflowError, OSError, ValueError):
        return str(value)


def pick(item: Mapping[str, Any], *keys: str, default: Any = "") -> Any:
    for key in keys:
        if key in item and item[key] not in (None, ""):
            return item[key]
    return default


def to_int(value: Any) -> int:
    text = str(value if value is not None else "").strip().replace(",", "")
    if not text:
        return 0
    try:
        return int(float(text))
    except ValueError:
        return 0


def write_json_atomically(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + ".tmp")
    temp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(temp, path)


def write_text_atomically(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + ".tmp")
    temp.write_text(text, encoding="utf-8")
    os.replace(temp, path)


# ---------------------------------------------------------------------------
# 原始响应解析
# ---------------------------------------------------------------------------

def iter_comment_items(payload: Any) -> Iterable[Mapping[str, Any]]:
    """从任意包裹结构里找出评论条目列表。

    平台响应层级各不相同（抖音 `comments[]`、小红书 `data.comments[]`、
    B站 `data.replies[]`、JustOneAPI 的 B站 还多一层 `data.data.replies[]`），
    这里按已知键名做有限深度的宽松查找，找不到就返回空。
    """
    if isinstance(payload, list):
        for item in payload:
            if isinstance(item, Mapping):
                yield item
        return
    if not isinstance(payload, Mapping):
        return
    for key in COMMENT_LIST_KEYS:
        value = payload.get(key)
        if isinstance(value, list):
            for item in value:
                if isinstance(item, Mapping):
                    yield item
            return
    for key in CONTAINER_KEYS:
        value = payload.get(key)
        if isinstance(value, (Mapping, list)):
            found = list(iter_comment_items(value))
            if found:
                yield from found
                return


def page_total(payload: Any) -> int:
    if not isinstance(payload, Mapping):
        return 0
    for key in ("total", "total_count", "total_number", "all_count", "comment_count", "comment_count_l1"):
        value = payload.get(key)
        if value not in (None, ""):
            return to_int(value)
    for key in CONTAINER_KEYS:
        nested = payload.get(key)
        if isinstance(nested, Mapping):
            found = page_total(nested)
            if found:
                return found
    return 0


def page_has_more(payload: Any) -> bool | None:
    if not isinstance(payload, Mapping):
        return None
    for key in ("has_more", "hasMore"):
        value = payload.get(key)
        if isinstance(value, bool):
            return value
        if value in (0, 1):
            return bool(value)
    if "is_end" in payload:
        # B 站 用 is_end 表达「已经到底」，语义与 has_more 相反。
        return not bool(payload.get("is_end"))
    for key in CONTAINER_KEYS:
        nested = payload.get(key)
        if isinstance(nested, Mapping):
            found = page_has_more(nested)
            if found is not None:
                return found
    return None


def page_cursor(payload: Any) -> Any:
    if not isinstance(payload, Mapping):
        return ""
    cursor = payload.get("cursor")
    if isinstance(cursor, Mapping):
        # B 站 把游标包在 cursor 对象里，续拉要的是它的 next。
        inner = cursor.get("next")
        if inner not in (None, ""):
            return inner
    for key in ("cursor", "lastCursor", "next"):
        value = payload.get(key)
        if value not in (None, ""):
            return value
    for key in CONTAINER_KEYS:
        nested = payload.get(key)
        if isinstance(nested, Mapping):
            found = page_cursor(nested)
            if found != "":
                return found
    return ""


def normalize_comment(platform: str, item: Mapping[str, Any], source: str) -> dict[str, Any] | None:
    """把一条平台原生评论投影成统一结构；没有评论 ID 的条目直接丢弃。"""
    nested_user = item.get("user") if isinstance(item.get("user"), Mapping) else {}
    user_info = item.get("user_info") if isinstance(item.get("user_info"), Mapping) else {}
    member = item.get("member") if isinstance(item.get("member"), Mapping) else {}
    content = item.get("content")
    if isinstance(content, Mapping):
        body = str(content.get("message") or "").strip()
    else:
        body = str(pick(item, "text", "content", "message", "body") or "").strip()

    comment_id = str(
        pick(item, "cid", "id", "rpid", "comment_id", "commentId", "commentIdStr") or ""
    ).strip()
    if not comment_id:
        return None

    reply_control = item.get("reply_control")
    location = ""
    if isinstance(reply_control, Mapping):
        location = str(reply_control.get("location") or "").strip()
    if not location:
        location = str(pick(item, "ip_label", "ip_location", "location", "ip_location_text") or "").strip()
    # B站 的属地是整串「IP属地：浙江」，抖音与小红书只给地名，这里统一去掉前缀。
    for prefix in ("IP属地：", "IP属地:"):
        if location.startswith(prefix):
            location = location[len(prefix):].strip()
            break

    return {
        "评论ID": comment_id,
        "正文": body,
        "作者昵称": str(
            pick(nested_user, "nickname", "name")
            or pick(user_info, "nickname", "name", "screen_name")
            or pick(member, "uname", "name")
            or pick(item, "nickname")
            or ""
        ).strip(),
        "作者ID": str(
            pick(nested_user, "uid", "user_id", "sec_uid")
            or pick(user_info, "user_id", "userId")
            or pick(member, "mid")
            or ""
        ).strip(),
        "点赞数": to_int(pick(item, "digg_count", "like_count", "like", "liked_count")),
        "回复数": to_int(pick(item, "reply_comment_total", "sub_comment_count", "rcount", "reply_count")),
        "发布时间": format_timestamp(pick(item, "create_time", "ctime", "time", "publish_time")),
        "IP属地": location,
        "平台": platform,
        "数据源": source,
    }


def collect_comments(platform: str, source: str, payloads: Iterable[Any]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """把多页原始响应合并成规范化列表，并汇总分页状态。"""
    records: list[dict[str, Any]] = []
    total = 0
    has_more: bool | None = None
    cursor: Any = ""
    for payload in payloads:
        total = total or page_total(payload)
        more = page_has_more(payload)
        if more is not None:
            has_more = more
        current_cursor = page_cursor(payload)
        if current_cursor != "":
            cursor = current_cursor
        for item in iter_comment_items(payload):
            record = normalize_comment(platform, item, source)
            if record is not None:
                records.append(record)
    meta = {"total": total, "has_more": has_more, "cursor": cursor}
    return records, meta


# ---------------------------------------------------------------------------
# 数据源：JustOneAPI 直调
# ---------------------------------------------------------------------------

def load_unit_helper():
    """复用 JustOneAPI 单条执行器的 HTTP 与配置工具，避免重复实现。"""
    unit_dir = Path(__file__).resolve().parent
    if str(unit_dir) not in sys.path:
        sys.path.insert(0, str(unit_dir))
    try:
        from JustOneAPI单条内容抓取执行器 import build_api_url, http_get_json, load_config  # type: ignore
    except Exception as exc:  # pragma: no cover - 仅在文件缺失或损坏时触发
        raise CommentFetchError(f"justoneapi_helper_unavailable:{exc}") from exc
    return build_api_url, http_get_json, load_config


def load_justoneapi_config(root: Path) -> dict[str, str]:
    _, _, load_config = load_unit_helper()
    config = load_config(root / "本地配置.txt")
    token = str(config.get("JUSTONEAPI_TOKEN", "")).strip()
    if not token:
        raise CommentFetchError("justoneapi_token_missing")
    base_url = str(config.get("JUSTONEAPI_BASE_URL", "")).strip() or "https://api.justoneapi.com"
    return {"token": token, "base_url": base_url}


def resolve_bilibili_aid(native_id: str) -> str:
    """B站 接口要 aid；给的是 BV 号时先换算。"""
    text = str(native_id or "").strip()
    if text.isdigit():
        return text
    _, http_get_json, _ = load_unit_helper()
    payload = http_get_json(f"{BILIBILI_VIEW_API}?bvid={text}", timeout=30)
    aid = payload.get("data", {}).get("aid") if isinstance(payload.get("data"), Mapping) else None
    if not aid:
        raise CommentFetchError(f"bilibili_aid_unresolved:{text}")
    return str(aid)


def fetch_justoneapi(root: Path, platform: str, native_id: str, pages: int) -> list[Any]:
    build_api_url, http_get_json, _ = load_unit_helper()
    config = load_justoneapi_config(root)
    page_param = JUSTONEAPI_PAGE_PARAM[platform]
    target_id = resolve_bilibili_aid(native_id) if platform == "B站" else native_id
    payloads: list[Any] = []
    cursor = ""
    for index in range(max(1, pages)):
        params = {
            "token": config["token"],
            JUSTONEAPI_ID_PARAM[platform]: target_id,
            page_param: cursor if page_param == "lastCursor" else str(index + 1),
        }
        if platform == "小红书":
            # 已定口径：小红书走 JustOneAPI 时固定按点赞数排序，用来拿热评。
            params["sort"] = "like_count"
        url = build_api_url(config["base_url"], JUSTONEAPI_PATHS[platform], params)
        payload = http_get_json(url, timeout=90)
        code = str(payload.get("code", ""))
        if code not in ("0", ""):
            raise CommentFetchError(f"justoneapi_error:{code}:{payload.get('message') or ''}")
        payloads.append(payload)
        next_cursor = page_cursor(payload)
        if page_has_more(payload) is False:
            break
        if next_cursor in ("", None) or next_cursor == cursor:
            break
        cursor = str(next_cursor)
    return payloads


# ---------------------------------------------------------------------------
# 数据源：Chrome 原始响应
# ---------------------------------------------------------------------------

def load_chrome_payloads(path: Path) -> list[Any]:
    if not path.is_file():
        raise CommentFetchError(f"raw_input_missing:{path.name}")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise CommentFetchError(f"raw_input_invalid:{path.name}") from exc
    if isinstance(payload, Mapping):
        for key in ("pages", "raw_pages", "responses"):
            value = payload.get(key)
            if isinstance(value, list):
                return list(value)
        return [payload]
    if isinstance(payload, list):
        return list(payload)
    raise CommentFetchError("raw_input_unrecognised")


# ---------------------------------------------------------------------------
# 存档读写
# ---------------------------------------------------------------------------

def read_existing_archive(json_path: Path) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    if not json_path.is_file():
        return [], {}
    try:
        payload = json.loads(json_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise CommentFetchError(f"existing_archive_invalid:{json_path.name}") from exc
    if not isinstance(payload, Mapping):
        raise CommentFetchError("existing_archive_unrecognised")
    records = [item for item in payload.get("评论", []) if isinstance(item, Mapping)]
    meta = {
        "数据源": str(payload.get("数据源") or ""),
        "平台": str(payload.get("平台") or ""),
        "平台原生内容ID": str(payload.get("平台原生内容ID") or ""),
        "响应总数": to_int(payload.get("响应总数")),
        "续拉状态": payload.get("续拉状态") if isinstance(payload.get("续拉状态"), Mapping) else {},
    }
    return [{key: item.get(key) for key in COMMENT_FIELDS if key in item} for item in records], meta


def markdown_cell(value: Any) -> str:
    """表格单元里不能出现裸换行与竖线：换行压成空格，竖线转义。"""
    text = str(value if value is not None else "").replace("\r\n", "\n").replace("\r", "\n")
    flattened = " ".join(part.strip() for part in text.split("\n") if part.strip())
    return flattened.replace("|", "\\|").strip()


def render_markdown(meta: Mapping[str, Any], records: list[dict[str, Any]]) -> str:
    has_more = (meta.get("续拉状态") or {}).get("has_more")
    total = to_int(meta.get("响应总数"))
    cumulative = to_int(meta.get("累计条数"))
    # 「一共」取一级评论口径：平台已声明没有更多时就是实际取到的条数；
    # 还有更多时只有平台声明的总数可用（它可能把二级回复一并计入），取两者较大值兜底。
    if has_more is False or not total:
        overall = cumulative
    else:
        overall = max(cumulative, total)

    lines = [
        "# 评论",
        "",
        f"目前只获取一级评论，一共 {overall} 条一级评论，已获取 {cumulative} 条。",
        "",
    ]
    if records:
        lines.extend(
            [
                "| # | 作者 | 评论内容 | 点赞数 | 回复数 | 发布时间 | IP属地 |",
                "| --- | --- | --- | --- | --- | --- | --- |",
            ]
        )
        for index, record in enumerate(records, 1):
            lines.append(
                "| {n} | {author} | {body} | {likes} | {replies} | {time} | {ip} |".format(
                    n=index,
                    author=markdown_cell(record.get("作者昵称")) or "（未署名）",
                    body=markdown_cell(record.get("正文")) or "（空评论）",
                    likes=to_int(record.get("点赞数")),
                    replies=to_int(record.get("回复数")),
                    time=markdown_cell(record.get("发布时间")) or "未知",
                    ip=markdown_cell(record.get("IP属地")) or "未知",
                )
            )
    else:
        lines.append("（本轮未取到评论）")

    return "\n".join(lines).rstrip("\n") + "\n"


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------

def declared_zero_comment(raw: Any) -> bool:
    """只有能确定解析出 0 才返回 True。

    空值、以及解析不出来的值（脏数据、异常文案）都算「未知」，一律返回 False，
    交由调用方照常抓取 —— 宁可白抓一次，也不能把未知当成 0 真漏掉评论。
    注意不要用 to_int 判断：它把解析失败也归成 0。
    """
    text = str(raw if raw is not None else "").strip().replace(",", "")
    if not text:
        return False
    try:
        return int(float(text)) == 0
    except ValueError:
        return False


def zero_comment_skip(
    *,
    root: Path,
    args: argparse.Namespace,
    platform: str,
    source: str,
    json_path: Path,
    markdown_path: Path,
    output_path: Path,
    existing_meta: Mapping[str, Any] | None,
) -> dict[str, Any] | None:
    """平台声明评论数为 0 时不发起抓取；已有存档则原样保留。非跳过场景返回 None。"""
    if not declared_zero_comment(getattr(args, "comment_count", "")):
        return None

    warnings: list[str] = []
    if existing_meta:
        cumulative = to_int(existing_meta.get("累计条数"))
        warnings.append(
            f"平台声明评论数为 0，但内容目录里已有存档（累计 {cumulative} 条）；"
            "已保留原存档，未覆盖、未发起抓取"
        )
    else:
        write_json_atomically(
            json_path,
            {
                "文件说明": "内容评论原文。只保存一级评论。",
                "平台": platform,
                "平台原生内容ID": str(args.native_id or "").strip(),
                "数据源": source,
                "抓取时间": now_iso(),
                "响应总数": 0,
                "本轮新增条数": 0,
                "去重丢弃条数": 0,
                "累计条数": 0,
                "抓取页数": 0,
                "是否截断": False,
                "续拉状态": {"cursor": "", "has_more": False},
                "未发起抓取原因": "平台声明评论数为 0",
                "评论": [],
            },
        )
        write_text_atomically(
            markdown_path,
            "\n".join(("# 评论", "", "平台声明该内容评论数为 0，未发起抓取。", "", "（无评论）", "")),
        )
        warnings.append("平台声明评论数为 0，未发起抓取")
        cumulative = 0

    result = {
        "ok": True,
        "status": STATUS_SKIPPED_ZERO,
        "platform": platform,
        "source": source,
        "fetched": 0,
        "deduped": 0,
        "cumulative": cumulative,
        "total": 0,
        "has_more": False,
        "pages": 0,
        "next": {"cursor": "", "has_more": False},
        "output_path": relative_path(root, markdown_path),
        "json_path": relative_path(root, json_path),
        "warnings": warnings,
    }
    write_json_atomically(output_path, result)
    return result


def fetch_comments(args: argparse.Namespace) -> dict[str, Any]:
    root = Path(args.workspace_root).expanduser().resolve()
    if not root.is_dir():
        raise CommentFetchError(f"workspace_root_invalid:{root}")
    platform = str(args.platform or "").strip()
    if platform not in SUPPORTED_PLATFORMS:
        raise CommentFetchError(f"platform_unsupported:{platform}")
    source = str(args.source or "").strip()
    if source not in SOURCES:
        raise CommentFetchError(f"source_unsupported:{source}")

    content_dir = resolve_under(root, args.content_dir, label="content_dir")
    output_path = resolve_under(root, args.output_json, label="output_json")
    json_path = content_dir / COMMENT_JSON_NAME
    markdown_path = content_dir / COMMENT_MARKDOWN_NAME

    pages = args.pages if args.pages and args.pages > 0 else DEFAULT_PAGES[source]

    # 先读已有存档并校验来源一致：来源不同就没必要发请求（JustOneAPI 按次计费），也不允许混源。
    existing, existing_meta = read_existing_archive(json_path)
    warnings: list[str] = []
    if existing_meta:
        archived_source = str(existing_meta.get("数据源") or "")
        if archived_source and archived_source != source:
            raise CommentFetchError(f"archive_source_conflict:{archived_source}!={source}")
        archived_platform = str(existing_meta.get("平台") or "")
        if archived_platform and archived_platform != platform:
            raise CommentFetchError(f"archive_platform_conflict:{archived_platform}!={platform}")

    # 平台声明评论数为 0 时不再发起抓取（小红书侧页面不会发出评论请求，硬抓必然失败）。
    skipped = zero_comment_skip(
        root=root,
        args=args,
        platform=platform,
        source=source,
        json_path=json_path,
        markdown_path=markdown_path,
        output_path=output_path,
        existing_meta=existing_meta,
    )
    if skipped is not None:
        return skipped

    if source == "justoneapi":
        payloads = fetch_justoneapi(root, platform, str(args.native_id or "").strip(), pages)
    else:
        raw_path = resolve_under(root, args.raw_input_json, label="raw_input_json")
        payloads = load_chrome_payloads(raw_path)

    incoming, meta = collect_comments(platform, source, payloads)
    if existing_meta and to_int(existing_meta.get("响应总数")) and not meta["total"]:
        meta["total"] = to_int(existing_meta.get("响应总数"))

    merged: dict[str, dict[str, Any]] = {}
    order: list[str] = []
    for record in existing:
        key = str(record.get("评论ID") or "")
        if key and key not in merged:
            merged[key] = record
            order.append(key)
    cumulative_before = len(order)
    copied = 0
    for record in incoming:
        key = str(record.get("评论ID") or "")
        if not key:
            copied += 1
            continue
        if key in merged:
            updated = dict(merged[key])
            updated.update(record)
            merged[key] = updated
            copied += 1
            continue
        merged[key] = record
        order.append(key)

    records = [merged[key] for key in order]
    deduped = len(incoming) - (len(order) - cumulative_before)

    meta_out = {
        "文件说明": "内容评论原文。只保存一级评论。",
        "平台": platform,
        "平台原生内容ID": str(args.native_id or "").strip(),
        "数据源": source,
        "抓取时间": now_iso(),
        "响应总数": meta["total"],
        "本轮新增条数": max(0, len(order) - cumulative_before),
        "去重丢弃条数": max(0, deduped),
        "累计条数": len(order),
        "抓取页数": pages,
        "是否截断": False,
        "续拉状态": {"cursor": meta["cursor"], "has_more": meta["has_more"]},
        "评论": records,
    }

    markdown_text = render_markdown(meta_out, records)
    write_json_atomically(json_path, meta_out)
    write_text_atomically(markdown_path, markdown_text)

    if not records:
        warnings.append("本轮未取到任何评论")
    total_value = to_int(meta["total"])
    if meta["has_more"]:
        remaining = max(0, total_value - len(order)) if total_value else 0
        warnings.append(
            f"评论未拉完：累计 {len(order)} 条 / 平台声明总数 {meta['total']}"
            f"（该口径可能含二级回复），估算还有 {remaining} 条"
        )
    elif total_value and total_value > len(order):
        warnings.append(
            f"平台声明总数 {meta['total']} 条，实际累计 {len(order)} 条一级评论；"
            "平台已声明没有更多可拉取的一级评论，差额通常来自总数口径含二级回复"
        )

    result = {
        "ok": True,
        "status": "written",
        "platform": platform,
        "source": source,
        "fetched": meta_out["本轮新增条数"],
        "deduped": meta_out["去重丢弃条数"],
        "cumulative": len(order),
        "total": meta["total"],
        "has_more": meta["has_more"],
        "pages": pages,
        "next": meta_out["续拉状态"],
        "output_path": relative_path(root, markdown_path),
        "json_path": relative_path(root, json_path),
        "warnings": warnings,
    }
    # 回执除 stdout 外，同时原子落盘到 --output-json：流程 2.10 直接读该文件取评论条数，
    # 只 print 会让文件缺失、被误判成「合同损坏」。存档（md/json）已在上方写好，此处只补回执。
    write_json_atomically(output_path, result)
    return result


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="抓取一条内容的评论原文，去重合并后写入正式内容目录。")
    parser.add_argument("--workspace-root", required=True)
    parser.add_argument("--platform", required=True, help="小红书 / 抖音 / B站")
    parser.add_argument("--native-id", default="", help="平台原生内容ID")
    parser.add_argument(
        "--comment-count",
        default="",
        help="本轮基础数据里的评论数（平台声明口径）。传 0 时不发起抓取、直接产出空存档；"
        "留空表示未知，照常抓取（未知不等于 0）。",
    )
    parser.add_argument("--content-dir", required=True, help="正式内容目录（prepare 冻结）")
    parser.add_argument("--source", required=True, choices=SOURCES)
    parser.add_argument(
        "--raw-input-json",
        default="",
        help="chrome_devtools 且确实要抓取时必填：本轮抓取的原始响应文件（含 pages 数组）。"
        "--comment-count 为 0 时不发请求，本参数不需要传。",
    )
    parser.add_argument("--pages", type=int, default=0, help="抓取页数；缺省按数据源取值")
    parser.add_argument(
        "--continue",
        dest="continue_flag",
        action="store_true",
        help="显式声明续拉；存档存在时始终按评论 ID 合并去重，不回退已抓内容",
    )
    parser.add_argument("--output-json", required=True, help="本轮抓取回执输出路径")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        result = fetch_comments(args)
    except CommentFetchError as exc:
        print(json.dumps({"ok": False, "status": "failed", "error": str(exc)}, ensure_ascii=False))
        return 1
    except Exception as exc:  # noqa: BLE001 - 未预期错误也要给出可读回执
        print(json.dumps({"ok": False, "status": "failed", "error": f"unexpected:{exc}"}, ensure_ascii=False))
        return 1
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
