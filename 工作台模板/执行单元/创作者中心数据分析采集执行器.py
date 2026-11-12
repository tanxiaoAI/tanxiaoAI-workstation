#!/usr/bin/env python3
"""创作者中心账号级与内容级数据采集执行器（小红书 / 抖音）。

只服务「我的账号」详细数据链路：

- ``prepare``：按本轮批次上下文生成采集计划和身份基准；
- ``commit``：校验 AI 从创作者中心页面读到的候选，写入账号目录下的正式数据。

平台由账号库的「平台」字段决定，只支持小红书与抖音：

- 小红书：采集范围固定为「内容分析 → 笔记数据」当前第一页可见笔记行，
  逐条进入「分析详情」读取后台指标。
- 抖音：账号级数据取「数据中心」当前可见指标；内容级取「内容管理」当前
  第一页可见作品行，公开作品逐条进入「作品数据详情」读取总览指标。

不翻页、不调整统计周期。执行器不访问页面，只负责身份校验、匹配和正式写入。
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import urllib.parse
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Tuple

_EXECUTION_UNIT_DIR = Path(__file__).resolve().parent
if str(_EXECUTION_UNIT_DIR) not in sys.path:
    sys.path.insert(0, str(_EXECUTION_UNIT_DIR))

from csv安全写入 import read_csv_table, read_csv_table_with_snapshot, write_csv_tables_atomically
from 全局内容ID import next_content_id
from 互动数解析 import parse_count
from 过程记录路径 import build_content_dir_name
from 账号衍生字段计算批处理 import compute_data_level, compute_time_slot
from 账号阶段状态 import write_json_atomically
from 账号后续详细处理批处理 import resolve_batch_context


SCHEMA_VERSION = "creator-center-content-analysis-v1"
PLAN_SCHEMA_VERSION = "creator-center-capture-plan-v1"
SCOPE_RULE = "visible_first_page_only"
CAPTURE_TIME_PATTERN = re.compile(r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2} [+-]\d{4}$")

XHS_ROW_FIELDS = (
    "标题",
    "发布时间",
    "公开状态",
    "曝光",
    "观看",
    "封面点击率",
    "点赞",
    "评论",
    "收藏",
    "涨粉",
    "分享",
    "人均观看时长",
    "弹幕",
    "备注",
    "后台详情",
)
XHS_DETAIL_GROUPS = {
    "笔记诊断": ("流量转化", "互动表现", "视频画质", "内容深度", "开头吸引力"),
    "核心数据": (
        "曝光数",
        "曝光数粉丝占比",
        "观看数",
        "观看数粉丝占比",
        "封面点击率",
        "封面点击率粉丝值",
        "平均观看时长",
        "平均观看时长粉丝值",
        "完播率",
        "完播率粉丝值",
        "2秒退出率",
        "2秒退出率粉丝值",
        "涨粉数",
    ),
    "互动数据": (
        "点赞数",
        "点赞数粉丝占比",
        "评论数",
        "评论数粉丝占比",
        "收藏数",
        "收藏数粉丝占比",
        "分享数",
        "分享数粉丝占比",
    ),
    "观看趋势": ("5秒内离开提示值", "观看趋势备注"),
}

DY_ROW_FIELDS = (
    "标题",
    "发布时间",
    "公开状态",
    "播放",
    "点赞",
    "评论",
    "分享",
    "收藏",
    "完播率",
    "2秒跳出率",
    "吸粉量",
    "封面点击率",
    "平均播放占比",
    "弹幕",
    "备注",
    "后台详情",
)
DY_DETAIL_GROUPS = {
    "核心数据": (
        "播放量",
        "点赞量",
        "评论量",
        "分享量",
        "收藏量",
        "弹幕量",
        "完播率",
        "2s跳出率",
        "涨粉量",
        "脱粉量",
        "粉丝播放占比",
    ),
}

PLATFORM_CONFIG = {
    "小红书": {
        "host": "creator.xiaohongshu.com",
        "page_url": "https://creator.xiaohongshu.com/statistics/data-analysis",
        "page": "内容分析 → 笔记数据",
        "overview_url": "https://creator.xiaohongshu.com/statistics/account/v2",
        "row_fields": XHS_ROW_FIELDS,
        "detail_groups": XHS_DETAIL_GROUPS,
        "core_metric_fields": ("曝光", "观看", "封面点击率", "点赞", "评论", "收藏", "涨粉", "分享"),
        "interaction_map": {
            "点赞数": ("互动数据", "点赞数", "点赞"),
            "收藏数": ("互动数据", "收藏数", "收藏"),
            "评论数": ("互动数据", "评论数", "评论"),
            "分享数": ("互动数据", "分享数", "分享"),
        },
        "detail_source": "小红书创作服务平台 note-detail",
        "detail_url_hint": "note-detail",
        "public_url_template": "https://www.xiaohongshu.com/explore/{native_id}",
        "capture_rule": {
            "time_range": "保持页面默认的最近一个月，不点击日期选择器、不调整时间区间",
            "pagination": "只取第一页可见行，不翻页、不切换每页条数、不滚动加载更多",
            "detail_page": "对这批可见笔记逐条进入「分析详情」，读取后台指标；不采集列表外的笔记",
            "account_overview": "账号概览在「数据看板 → 账号概览」，落地地址形如 https://creator.xiaohongshu.com/statistics/account/v2，必须把这一页的完整地址写进 account_overview.页面链接（填首页 /new/home 会被拒绝）。要采满这十项：统计周期（页面上的日期区间，如「10-21 至 10-27」）、曝光数、封面点击率、平均观看时长、视频完播率、观看数、涨粉数、主页访客数、发布数、互动数。前四项带「环比 ±X%」要连同环比一起保留；后五项带「超过 X% 同类创作者」要写成「值（超过X%同类创作者）」。关注数、粉丝数、获赞与收藏是账号主页数据，不要写进概览。只读页头那三个汇总数属于没采到。读不到就记未获取",
        },
    },
    "抖音": {
        "host": "creator.douyin.com",
        "page_url": "https://creator.douyin.com/creator-micro/content/manage",
        "page": "内容管理 + 数据中心",
        "overview_url": "https://creator.douyin.com/creator-micro/data-center/operation",
        "row_fields": DY_ROW_FIELDS,
        "detail_groups": DY_DETAIL_GROUPS,
        "core_metric_fields": ("播放", "点赞", "评论", "分享", "收藏"),
        "interaction_map": {
            "点赞数": ("核心数据", "点赞量", "点赞"),
            "收藏数": ("核心数据", "收藏量", "收藏"),
            "评论数": ("核心数据", "评论量", "评论"),
            "分享数": ("核心数据", "分享量", "分享"),
        },
        "detail_source": "抖音创作者中心 work-detail",
        "detail_url_hint": "work-management/work-detail/",
        "public_url_template": "https://www.douyin.com/video/{native_id}",
        "capture_rule": {
            "time_range": "保持数据中心当前统计周期，不切换昨天/近7天/近30天，不点击日期选择器",
            "pagination": "内容管理只取第一页可见作品，不翻页、不切换排序、不滚动加载更多",
            "detail_page": "只对公开作品点击播放数进入「作品数据详情」，读取总览指标与评论管理首屏评论响应",
            "account_overview": "账号概览在数据中心（https://creator.douyin.com/creator-micro/data-center/operation），必须把这一页的完整地址写进 account_overview.页面链接（不是 /data-center 下的页面会被拒绝）。要采满这十项：统计周期（作品统计周期与粉丝统计周期都记）、播放量、互动率、完播率、作品数、粉丝净增、5秒完播率、2秒跳出率、封面点击率、平均播放时长；数据总览五项要连同「超过/低于 X% 同类作者」一起读取，写成「值（超过X%同类作者）」或「值（低于X%同类作者）」。关注数、粉丝总数是账号主页数据，不要写进概览。读不到就记未获取",
        },
    },
}
SUPPORTED_PLATFORMS = tuple(PLATFORM_CONFIG)
# 账号概览必须采到的指标（2026-11-01 定）。
# 前四项＝近 7 天汇总（工作台「最近数据」那四张卡），后五项＝雷达图五维。
# 关注数 / 粉丝数 / 获赞与收藏 **不在这里**：它们属于账号主页基础数据，
# 已经写在我方账号库里，重复塞进概览会让「近 7 天」这块看着像采到了。
OVERVIEW_REQUIRED_METRICS = {
    "小红书": (
        "统计周期",
        "曝光数",
        "封面点击率",
        "平均观看时长",
        "视频完播率",
        "观看数",
        "涨粉数",
        "主页访客数",
        "发布数",
        "互动数",
    ),
    "抖音": (
        "统计周期",
        "播放量",
        "互动率",
        "完播率",
        "作品数",
        "粉丝净增",
        "5秒完播率",
        "2秒跳出率",
        "封面点击率",
        "平均播放时长",
    ),
}
# 雷达五维：这五项一项都采不到时，「账号概览」实质没采到，状态记未获取。
OVERVIEW_RADAR_METRICS = {
    "小红书": ("观看数", "涨粉数", "主页访客数", "发布数", "互动数"),
    "抖音": ("播放量", "完播率", "粉丝净增", "作品数", "互动率"),
}
# 账号概览页的落点前缀：页面链接必须命中它，才算真的读了概览页。
# 2026-11-01 踩过：AI 拿创作者中心首页顶栏的三个汇总数当概览提交，这里直接拦掉。
OVERVIEW_PAGE_PREFIX = {
    "小红书": "/statistics/account",
    "抖音": "/data-center",
}
CONTENT_LIST_FIELDS = (
    "内容ID",
    "平台原生内容ID",
    "平台",
    "账号ID",
    "账号名",
    "标题",
    "内容目录路径",
    "封面图原链接",
    "封面图路径",
    "视频下载链接",
    "媒介形式",
    "公开状态",
    "点赞数",
    "收藏数",
    "分享数",
    "评论数",
    "核心互动数",
    "数据水平",
    "发布时间",
    "发布时间区间",
    "对外页面链接",
    "基础数据获取方式",
    "bvid",
    "aid",
    "cid",
    "视频文案路径",
    "内容分析路径",
    "备注",
)


class CreatorCenterError(RuntimeError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="创作者中心账号级/内容级数据采集（小红书 / 抖音）")
    parser.add_argument("--workspace-root", required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--action", required=True, choices=("prepare", "commit"))
    parser.add_argument("--candidate-file", default="", help="commit 时 AI 写入的候选 JSON")
    parser.add_argument(
        "--flow-type",
        default="self_account",
        choices=("self_account",),
        help="本执行器只服务我的账号链路",
    )
    return parser.parse_args()


def _relative(root: Path, path: Path) -> str:
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError as exc:
        raise CreatorCenterError("path_outside_workspace", "创作者中心数据路径必须位于当前工作区") from exc


def _load_candidate(path: Path) -> Dict[str, Any]:
    if not path.is_file() or path.is_symlink():
        raise CreatorCenterError("candidate_missing", "候选文件不存在或不是普通文件")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise CreatorCenterError("candidate_invalid", "候选文件不是合法 UTF-8 JSON") from exc
    if not isinstance(payload, dict):
        raise CreatorCenterError("candidate_invalid", "候选顶层必须是对象")
    return payload


def _text(value: Any) -> str:
    return "" if value is None else str(value).strip()


def _normalize_time(value: Any) -> str:
    text = _text(value)
    if not text:
        return ""
    match = re.search(
        r"(\d{4})[-/年](\d{1,2})[-/月](\d{1,2})日?\s*(\d{1,2}):(\d{2})",
        text,
    )
    if not match:
        return ""
    year, month, day, hour, minute = (int(part) for part in match.groups())
    return f"{year:04d}-{month:02d}-{day:02d} {hour:02d}:{minute:02d}"


def _parse_capture_time(value: Any) -> datetime:
    text = _text(value)
    if not CAPTURE_TIME_PATTERN.fullmatch(text):
        raise CreatorCenterError(
            "captured_at_invalid",
            "captured_at 必须是 `date '+%Y-%m-%d %H:%M:%S %z'` 的原样输出",
        )
    return datetime.strptime(text[:19], "%Y-%m-%d %H:%M:%S")


def _account_row(root: Path, account_id: str) -> Dict[str, str]:
    _, rows = read_csv_table(root / "我方资料/我方账号库.csv")
    for row in rows:
        if _text(row.get("账号ID")) == account_id:
            return row
    raise CreatorCenterError("account_not_found", "我方账号库中没有本轮账号")


def _content_rows(content_list: Path) -> List[Dict[str, str]]:
    _, rows = read_csv_table(content_list)
    return [row for row in rows if _text(row.get("内容ID"))]


def _string_group(value: Any, group: str, keys: Tuple[str, ...]) -> Dict[str, str]:
    if value is None:
        return {key: "" for key in keys}
    if not isinstance(value, dict):
        raise CreatorCenterError("candidate_row_invalid", f"{group} 必须是对象")
    unknown = sorted(set(value) - set(keys))
    if unknown:
        raise CreatorCenterError(
            "candidate_row_invalid",
            f"{group} 包含未声明字段：{', '.join(unknown)}",
        )
    normalized: Dict[str, str] = {}
    for key in keys:
        item = value.get(key)
        if isinstance(item, (dict, list)):
            raise CreatorCenterError("candidate_row_invalid", f"{group}.{key} 不能是对象或数组")
        normalized[key] = _text(item)
    return normalized


def _detail_required_keys(platform: str) -> Tuple[str, ...]:
    groups = tuple(PLATFORM_CONFIG[platform]["detail_groups"])
    return (
        "状态",
        "平台原生内容ID",
        "后台详情页链接",
        *groups,
        "字段口径告警",
        "失败原因",
    )


def _validate_detail(value: Any, platform: str) -> Dict[str, Any]:
    config = PLATFORM_CONFIG[platform]
    groups = config["detail_groups"]
    if value is None:
        return {
            "状态": "未获取",
            "平台原生内容ID": "",
            "后台详情页链接": "",
            **{group: {key: "" for key in keys} for group, keys in groups.items()},
            "字段口径告警": [],
            "失败原因": "未进入后台详情",
        }
    if not isinstance(value, dict):
        raise CreatorCenterError("candidate_row_invalid", "后台详情 必须是对象")
    unknown = sorted(set(value) - set(_detail_required_keys(platform)))
    if unknown:
        raise CreatorCenterError(
            "candidate_row_invalid",
            "后台详情 包含未声明字段：" + ", ".join(unknown),
        )
    status = _text(value.get("状态")) or "未获取"
    if status not in {"成功", "失败", "未获取"}:
        raise CreatorCenterError("candidate_row_invalid", "后台详情.状态 只能是成功、失败或未获取")
    native_id = _text(value.get("平台原生内容ID"))
    detail_url = _text(value.get("后台详情页链接"))
    failure = _text(value.get("失败原因"))
    if status == "成功":
        if not native_id:
            raise CreatorCenterError("candidate_row_invalid", "后台详情成功时缺少平台原生内容ID")
        if (
            not detail_url.startswith(f"https://{config['host']}/")
            or config["detail_url_hint"] not in detail_url
        ):
            raise CreatorCenterError(
                "candidate_row_invalid",
                f"后台详情页链接必须是 {config['host']} 的 {config['detail_url_hint']} 页面",
            )
    if status == "失败" and not failure:
        raise CreatorCenterError("candidate_row_invalid", "后台详情失败时必须写明失败原因")
    warnings = value.get("字段口径告警")
    if warnings is None:
        warnings = []
    if not isinstance(warnings, list) or any(isinstance(item, (dict, list)) for item in warnings):
        raise CreatorCenterError("candidate_row_invalid", "字段口径告警 必须是字符串数组")
    normalized: Dict[str, Any] = {
        "状态": status,
        "平台原生内容ID": native_id,
        "后台详情页链接": detail_url,
        "字段口径告警": [_text(item) for item in warnings if _text(item)],
        "失败原因": failure,
    }
    for group, keys in groups.items():
        normalized[group] = _string_group(value.get(group), group, keys)
    return normalized


def _validate_row(row: Any, index: int, platform: str) -> Dict[str, Any]:
    if not isinstance(row, dict):
        raise CreatorCenterError("candidate_row_invalid", f"第 {index} 行不是对象")
    row_fields = PLATFORM_CONFIG[platform]["row_fields"]
    unknown = sorted(set(row) - set(row_fields))
    if unknown:
        raise CreatorCenterError(
            "candidate_row_invalid",
            f"第 {index} 行包含未声明字段：{', '.join(unknown)}",
        )
    cleaned: Dict[str, Any] = {}
    for field in row_fields:
        value = row.get(field)
        if field == "后台详情":
            cleaned[field] = _validate_detail(value, platform)
            continue
        if isinstance(value, (dict, list)):
            raise CreatorCenterError("candidate_row_invalid", f"第 {index} 行的 {field} 不能是对象或数组")
        cleaned[field] = _text(value)
    if not cleaned["标题"]:
        raise CreatorCenterError("candidate_row_invalid", f"第 {index} 行缺少标题")
    if not cleaned["发布时间"]:
        raise CreatorCenterError("candidate_row_invalid", f"第 {index} 行缺少发布时间")
    if platform == "抖音":
        # 抖音内容管理把公开作品显示为「已发布」，统一归一为「公开」；
        # 其余文字（私密 / 审核中 / 未通过 / 其他）原样保留，按非公开跳过。
        cleaned["公开状态"] = {"已发布": "公开"}.get(cleaned["公开状态"], cleaned["公开状态"])
    return cleaned


def _validate_account_overview(value: Any, platform: str) -> Dict[str, Any]:
    if value is None:
        return {"状态": "未获取", "数据来源": "", "页面链接": "", "指标": {}, "备注": "本轮未读取账号概览页"}
    if not isinstance(value, dict):
        raise CreatorCenterError("account_overview_invalid", "account_overview 必须是对象")
    unknown = sorted(set(value) - {"状态", "数据来源", "页面链接", "指标", "备注"})
    if unknown:
        raise CreatorCenterError(
            "account_overview_invalid",
            "account_overview 包含未声明字段：" + ", ".join(unknown),
        )
    status = _text(value.get("状态")) or "未获取"
    if status not in {"成功", "未获取"}:
        raise CreatorCenterError("account_overview_invalid", "account_overview.状态 只能是「成功」或「未获取」")
    page_url = _text(value.get("页面链接"))
    # 「成功」必须证明读的是账号概览页本身：2026-11-01 实测有 AI 把创作者中心首页顶栏的
    # 「关注 / 粉丝 / 获赞与收藏」当成账号概览提交。落点不对直接拒绝，不落盘。
    if status == "成功":
        prefix = OVERVIEW_PAGE_PREFIX.get(platform, "")
        if not page_url.startswith(f"https://{PLATFORM_CONFIG[platform]['host']}"):
            raise CreatorCenterError(
                "account_overview_page_invalid",
                f"账号概览.页面链接 必须是 {PLATFORM_CONFIG[platform]['host']} 下的页面",
            )
        if prefix and prefix not in page_url:
            raise CreatorCenterError(
                "account_overview_page_invalid",
                f"账号概览.页面链接 必须落在 {prefix} 下；读到的是 {page_url}，看起来是别的页面（首页顶栏那三个汇总数不算账号概览）",
            )
    metrics = value.get("指标")
    if metrics is None:
        metrics = {}
    if not isinstance(metrics, dict):
        raise CreatorCenterError("account_overview_invalid", "account_overview.指标 必须是对象")
    normalized: Dict[str, str] = {}
    for key, item in metrics.items():
        name = _text(key)
        if not name:
            continue
        if isinstance(item, (dict, list)):
            raise CreatorCenterError("account_overview_invalid", f"账号概览指标 {name} 不能是对象或数组")
        normalized[name] = _text(item)
    # 「成功」必须真的采到近 7 天数据：雷达五维一项都没有时，说明只读了页头那三个汇总数，
    # 实质没采到账号概览。这时降成「未获取」，宁可让工作台显示未采集，也不让空数据冒充成功。
    radar = OVERVIEW_RADAR_METRICS.get(platform, ())
    note = _text(value.get("备注"))
    if status == "成功" and radar and not any(normalized.get(name) for name in radar):
        status = "未获取"
        note = (note + "；" if note else "") + "账号概览只读到账号级汇总数，近 7 天指标一项没采到，按未获取处理"
    return {
        "状态": status,
        "数据来源": _text(value.get("数据来源")),
        "页面链接": page_url,
        "指标": normalized,
        "备注": note,
    }


def _account_overview_gaps(platform: str, overview: Dict[str, Any]) -> List[str]:
    """概览缺了哪些必采指标。只用于回执提示，不阻断落盘。"""
    if overview.get("状态") != "成功":
        return []
    metrics = overview.get("指标") or {}
    return [name for name in OVERVIEW_REQUIRED_METRICS.get(platform, ()) if not metrics.get(name)]


def _match_content(
    title: Any,
    native_id: str,
    rows: List[Dict[str, str]],
) -> Tuple[Dict[str, str] | None, str]:
    exact_title = _text(title)
    if not exact_title:
        return None, ""
    exact = [row for row in rows if _text(row.get("标题")) == exact_title]
    if len(exact) == 1:
        return exact[0], "标题完全匹配"
    if len(exact) > 1:
        return None, "标题重复"
    if native_id:
        by_native = [row for row in rows if _text(row.get("平台原生内容ID")) == native_id]
        if len(by_native) == 1:
            return by_native[0], "平台原生内容ID"
    return None, ""


def _detail_metric(detail: Dict[str, Any], group: str, key: str, fallback: str = "") -> str:
    value = _text((detail.get(group) or {}).get(key))
    return value or fallback


XHS_TOKEN_PATTERN = re.compile(r"[?&]xsec_token=([^&#\s]+)")


def xiaohongshu_public_url(native_id: str, title: str, *candidates: str) -> str:
    """小红书笔记页必须带 xsec_token，否则别人打开是「页面不见了」。

    创作者中心后台链接通常不带 token，所以逐个候选链接里找，找到就用；
    实在没有就退回搜索结果页，不落死链。
    """
    for candidate in candidates:
        match = XHS_TOKEN_PATTERN.search(str(candidate or ""))
        if match:
            token = urllib.parse.unquote(match.group(1))
            return (
                f"https://www.xiaohongshu.com/explore/{native_id}"
                f"?xsec_token={token}&xsec_source=pc_creatormng"
            )
    if title:
        return f"https://www.xiaohongshu.com/search_result?keyword={urllib.parse.quote(title)}"
    return f"https://www.xiaohongshu.com/explore/{native_id}"


def _public_url(
    platform: str,
    native_id: str,
    title: str,
    detail: Dict[str, Any],
    row: Dict[str, Any],
) -> str:
    if not native_id:
        return ""
    if platform == "小红书":
        candidates = [_text(detail.get("后台详情页链接"))]
        candidates.extend(value for value in (row or {}).values() if isinstance(value, str))
        return xiaohongshu_public_url(native_id, title, *candidates)
    return PLATFORM_CONFIG[platform]["public_url_template"].format(native_id=native_id)


def _build_new_content_row(
    *,
    root: Path,
    fieldnames: List[str],
    platform: str,
    account_id: str,
    account_name: str,
    row: Dict[str, Any],
    detail: Dict[str, Any],
    captured_at: str,
    created_ids: List[str],
) -> Tuple[Dict[str, str], str]:
    config = PLATFORM_CONFIG[platform]
    content_id = next_content_id(root, additional_ids=created_ids)
    native_id = _text(detail.get("平台原生内容ID"))
    title = _text(row.get("标题"))
    publish_time = _text(row.get("发布时间"))
    interaction = config["interaction_map"]

    def pick_metric(name: str) -> str:
        group, key, fallback_field = interaction[name]
        return _detail_metric(detail, group, key, _text(row.get(fallback_field)))

    likes = pick_metric("点赞数")
    collects = pick_metric("收藏数")
    comments = pick_metric("评论数")
    shares = pick_metric("分享数")
    likes_number = parse_count(likes)
    collects_number = parse_count(collects)
    comments_number = parse_count(comments)
    core = ""
    level = ""
    if likes_number is not None and collects_number is not None and comments_number is not None:
        core = str(likes_number + collects_number + comments_number)
        try:
            level = compute_data_level(platform, int(core))
        except ValueError:
            level = ""
    media_dir = build_content_dir_name(
        root / "我方资料/我方内容库.csv",
        {"内容ID": content_id, "标题": title},
    )
    content_dir = root / "我方资料/我方内容" / media_dir
    content_dir.mkdir(parents=True, exist_ok=True)
    detail_payload = {
        "schema_version": SCHEMA_VERSION,
        "内容ID": content_id,
        "平台原生内容ID": native_id,
        "平台": platform,
        "账号ID": account_id,
        "账号名": account_name,
        "标题": title,
        "发布时间": publish_time,
        "采集时间": captured_at,
        "数据来源": config["detail_source"],
        "后台详情页链接": _text(detail.get("后台详情页链接")),
        **{
            group: detail.get(group) or {}
            for group in config["detail_groups"]
        },
        "字段口径告警": detail.get("字段口径告警") or [],
    }
    write_json_atomically(content_dir / "创作者中心详情.json", detail_payload)
    normalized_publish_time = _normalize_time(publish_time) or publish_time
    new_row = {field: "" for field in fieldnames}
    new_row.update(
        {
            "内容ID": content_id,
            "平台原生内容ID": native_id,
            "平台": platform,
            "账号ID": account_id,
            "账号名": account_name,
            "标题": title,
            "内容目录路径": _relative(root, content_dir),
            "公开状态": "公开",
            "点赞数": likes,
            "收藏数": collects,
            "分享数": shares,
            "评论数": comments,
            "核心互动数": core,
            "数据水平": level,
            "发布时间": normalized_publish_time,
            "发布时间区间": compute_time_slot(normalized_publish_time) if normalized_publish_time else "",
            "对外页面链接": _public_url(platform, native_id, title, detail, row),
            "基础数据获取方式": "creator_center",
            "备注": "由创作者中心自动补建；后台详情见 创作者中心详情.json",
        }
    )
    return new_row, media_dir


def _insert_row_by_publish_time(rows: List[Dict[str, str]], new_row: Dict[str, str]) -> None:
    new_time = _normalize_time(new_row.get("发布时间"))
    if not new_time:
        rows.append(new_row)
        return
    for index, row in enumerate(rows):
        row_time = _normalize_time(row.get("发布时间"))
        if row_time and row_time < new_time:
            rows.insert(index, new_row)
            return
    rows.append(new_row)


def _build_report(
    *,
    root: Path,
    platform: str,
    account_id: str,
    account_name: str,
    run_id: str,
    captured_at: str,
    formal_dir: Path,
    latest_pointer: Path,
    rows: List[Dict[str, Any]],
    account_overview: Dict[str, Any],
    existing_matched_ids: List[str],
    created_ids: List[str],
    created_titles: List[str],
) -> Dict[str, Any]:
    core_metric_fields = PLATFORM_CONFIG[platform]["core_metric_fields"]
    unmatched_titles: List[str] = []
    skipped_non_public: List[str] = []
    missing_fields: List[str] = []
    detail_failures: List[str] = []
    for row in rows:
        if row.get("匹配状态") == "非公开跳过":
            skipped_non_public.append(row.get("标题") or "")
            continue
        if row.get("匹配状态") not in {"已匹配", "新建内容"}:
            unmatched_titles.append(row.get("标题") or "")
        missing = [field for field in core_metric_fields if not row.get(field)]
        if missing:
            missing_fields.append(f"{row.get('标题') or row.get('内容ID') or ''}：{'、'.join(missing)}")
        detail = row.get("后台详情") or {}
        if detail.get("状态") == "失败":
            detail_failures.append(f"{row.get('标题') or row.get('内容ID') or ''}：{detail.get('失败原因') or '后台详情失败'}")
    return {
        "schema_version": SCHEMA_VERSION,
        "run_id": run_id,
        "account_id": account_id,
        "account_name": account_name,
        "platform": platform,
        "captured_at": captured_at,
        "scope_rule": SCOPE_RULE,
        "formal_dir": _relative(root, formal_dir),
        "latest_pointer": _relative(root, latest_pointer),
        "visible_row_count": len(rows),
        "matched_count": len(existing_matched_ids),
        "unmatched_count": len(unmatched_titles),
        "created_count": len(created_ids),
        "created_content_ids": created_ids,
        "created_titles": created_titles,
        "skipped_non_public_count": len(skipped_non_public),
        "skipped_non_public_titles": skipped_non_public,
        "matched_content_ids": existing_matched_ids,
        "unmatched_titles": unmatched_titles,
        "account_overview_status": account_overview.get("状态", "未获取"),
        "account_overview_missing": _account_overview_gaps(platform, account_overview),
        "missing_fields": missing_fields,
        "detail_failures": detail_failures,
    }


def _formal_dir(account_dir: Path, captured_at: str, run_id: str) -> Path:
    stamp = _parse_capture_time(captured_at).strftime("%Y%m%d-%H%M%S")
    base = account_dir / "创作者中心数据"
    target = base / stamp
    if target.exists():
        target = base / f"{stamp}-{run_id[-6:]}"
    return target


def run_prepare(args: argparse.Namespace) -> Dict[str, Any]:
    root = Path(args.workspace_root).expanduser().resolve()
    context = resolve_batch_context(root, args.run_id, flow_type=args.flow_type)
    account_id = _text(context.get("account_id"))
    content_list: Path = context["content_list"]
    account = _account_row(root, account_id)
    rows = _content_rows(content_list)
    platform = _text(account.get("平台")) or (_text(rows[0].get("平台")) if rows else "")
    if platform not in SUPPORTED_PLATFORMS:
        return {
            "ok": True,
            "status": "skipped_platform_unsupported",
            "platform": platform,
            "message": "创作者中心账号数据当前只支持小红书和抖音",
        }
    data_source = _text(context.get("data_source"))
    if data_source != "chrome_devtools":
        return {
            "ok": True,
            "status": "skipped_data_source_unsupported",
            "platform": platform,
            "message": "创作者中心数据只能在 chrome_devtools 数据源下采集",
        }
    selected = context.get("content_ids") or []
    selected_set = set(selected)
    selected_rows = [row for row in rows if _text(row.get("内容ID")) in selected_set]
    in_scope_rows = selected_rows or rows
    plan = {
        "schema_version": PLAN_SCHEMA_VERSION,
        "flow_type": "self_account",
        "run_id": args.run_id,
        "account_id": account_id,
        "account_name": _text(account.get("账号名")),
        "platform": platform,
        "data_source": data_source,
        "scope_confirmed": bool((context.get("scope") or {}).get("confirmed")),
        "capture_rule": {
            "page": PLATFORM_CONFIG[platform]["page"],
            "page_url": PLATFORM_CONFIG[platform]["page_url"],
            "scope_rule": SCOPE_RULE,
            **PLATFORM_CONFIG[platform]["capture_rule"],
        },
        # 账号概览的清单随计划一起下发：AI 照着采，不用去猜「哪些算账号概览」。
        "account_overview": {
            "页面链接": PLATFORM_CONFIG[platform]["overview_url"],
            "页面链接必须包含": OVERVIEW_PAGE_PREFIX.get(platform, ""),
            "必采指标": list(OVERVIEW_REQUIRED_METRICS.get(platform, ())),
            "雷达五维": list(OVERVIEW_RADAR_METRICS.get(platform, ())),
        },
        "candidate_contents": [
            {
                "内容ID": _text(row.get("内容ID")),
                "标题": _text(row.get("标题")),
                "发布时间": _text(row.get("发布时间")),
                "公开状态": _text(row.get("公开状态")),
                "在本轮范围": _text(row.get("内容ID")) in selected_set,
            }
            for row in in_scope_rows
        ],
        "account_snapshot": {
            "账号名": _text(account.get("账号名")),
            "粉丝数": _text(account.get("粉丝数")),
            "获赞数": _text(account.get("获赞数")),
        },
    }
    plan_path = root / ".meiling/staging/self-account-diagnose" / args.run_id / "创作者中心数据/采集计划.json"
    write_json_atomically(plan_path, plan)
    return {
        "ok": True,
        "status": "planned",
        "run_id": args.run_id,
        "account_id": account_id,
        "account_name": plan["account_name"],
        "platform": platform,
        "scope_rule": SCOPE_RULE,
        "plan_path": _relative(root, plan_path),
        "candidate_count": len(plan["candidate_contents"]),
        "scope_confirmed": plan["scope_confirmed"],
    }


def run_commit(args: argparse.Namespace) -> Dict[str, Any]:
    root = Path(args.workspace_root).expanduser().resolve()
    context = resolve_batch_context(root, args.run_id, flow_type=args.flow_type)
    account_id = _text(context.get("account_id"))
    content_list: Path = context["content_list"]
    account = _account_row(root, account_id)
    platform = _text(account.get("平台"))
    if platform not in SUPPORTED_PLATFORMS:
        raise CreatorCenterError("platform_unsupported", "创作者中心账号数据当前只支持小红书和抖音")
    candidate_path_raw = _text(args.candidate_file)
    if not candidate_path_raw:
        raise CreatorCenterError("candidate_missing", "commit 必须提供 --candidate-file")
    candidate_path = Path(candidate_path_raw).expanduser()
    if not candidate_path.is_absolute():
        candidate_path = root / candidate_path
    payload = _load_candidate(candidate_path)

    if _text(payload.get("schema_version")) != SCHEMA_VERSION:
        raise CreatorCenterError("candidate_invalid", "候选 schema_version 无效")
    if _text(payload.get("run_id")) != args.run_id:
        raise CreatorCenterError("candidate_invalid", "候选 run_id 与本轮不一致")
    if _text(payload.get("account_id")) != account_id:
        raise CreatorCenterError("candidate_invalid", "候选 account_id 与本轮不一致")
    if _text(payload.get("platform")) != platform:
        raise CreatorCenterError("candidate_invalid", f"候选 platform 必须是{platform}，与账号平台一致")
    if _text(payload.get("scope_rule")) != SCOPE_RULE:
        raise CreatorCenterError(
            "candidate_invalid",
            f"候选 scope_rule 必须是 {SCOPE_RULE}（只取第一页可见行）",
        )
    page_url = _text(payload.get("page_url"))
    if not page_url.startswith(f"https://{PLATFORM_CONFIG[platform]['host']}/"):
        raise CreatorCenterError(
            "candidate_invalid",
            f"候选 page_url 必须是 {PLATFORM_CONFIG[platform]['host']} 的创作者中心页面",
        )
    captured_at = _text(payload.get("captured_at"))
    _parse_capture_time(captured_at)
    raw_rows = payload.get("content_rows")
    if not isinstance(raw_rows, list):
        raise CreatorCenterError("candidate_invalid", "content_rows 必须是数组")
    rows = [_validate_row(item, index + 1, platform) for index, item in enumerate(raw_rows)]
    account_overview = _validate_account_overview(payload.get("account_overview"), platform)

    fieldnames, content_rows, original_bytes = read_csv_table_with_snapshot(content_list)
    missing_fields = [field for field in CONTENT_LIST_FIELDS if field not in fieldnames]
    if missing_fields:
        raise CreatorCenterError(
            "content_list_invalid",
            "内容列表缺少字段：" + "、".join(missing_fields),
        )
    account_name = _text(account.get("账号名"))
    seen_ids: set[str] = set()
    existing_matched_ids: List[str] = []
    created_ids: List[str] = []
    created_titles: List[str] = []
    pending_new_rows: List[Dict[str, str]] = []
    for row in rows:
        detail = row.get("后台详情") or {}
        if _text(row.get("公开状态")) != "公开":
            row["内容ID"] = ""
            row["匹配状态"] = "非公开跳过"
            row["匹配依据"] = ""
            row["账号名"] = ""
            continue
        matched, basis = _match_content(row.get("标题"), _text(detail.get("平台原生内容ID")), content_rows)
        if matched:
            content_id = _text(matched.get("内容ID"))
            if content_id in seen_ids:
                raise CreatorCenterError("candidate_invalid", f"同一内容被重复采集：{content_id}")
            seen_ids.add(content_id)
            existing_matched_ids.append(content_id)
            row["内容ID"] = content_id
            row["匹配状态"] = "已匹配"
            row["匹配依据"] = basis
            row["账号名"] = _text(matched.get("账号名"))
            continue
        row["内容ID"] = ""
        if basis == "标题重复":
            row["匹配状态"] = "标题重复"
            row["匹配依据"] = basis
            row["账号名"] = ""
            continue
        if detail.get("状态") == "成功":
            new_row, _media_dir = _build_new_content_row(
                root=root,
                fieldnames=fieldnames,
                platform=platform,
                account_id=account_id,
                account_name=account_name,
                row=row,
                detail=detail,
                captured_at=captured_at,
                created_ids=created_ids + [item.get("内容ID", "") for item in pending_new_rows],
            )
            pending_new_rows.append(new_row)
            created_ids.append(new_row["内容ID"])
            created_titles.append(new_row["标题"])
            seen_ids.add(new_row["内容ID"])
            row["内容ID"] = new_row["内容ID"]
            row["匹配状态"] = "新建内容"
            row["匹配依据"] = "标题完全匹配未命中"
            row["账号名"] = account_name
        else:
            row["匹配状态"] = "未匹配"
            row["匹配依据"] = ""
            row["账号名"] = ""

    if pending_new_rows:
        for new_row in pending_new_rows:
            _insert_row_by_publish_time(content_rows, new_row)
        try:
            write_csv_tables_atomically(
                [(content_list, fieldnames, content_rows)],
                expected_originals={content_list: original_bytes},
            )
        except Exception as exc:  # noqa: BLE001
            raise CreatorCenterError("content_list_write_failed", f"内容列表写入失败：{exc}") from exc

    account_dir = content_list.parent
    formal_dir = _formal_dir(account_dir, captured_at, args.run_id)
    latest_pointer = account_dir / "创作者中心数据/最新采集.json"
    formal_dir.mkdir(parents=True, exist_ok=True)

    content_payload = {
        "schema_version": SCHEMA_VERSION,
        "账号ID": account_id,
        "账号名": account_name,
        "平台": platform,
        "采集时间": captured_at,
        "页面链接": page_url,
        "页面时间范围": _text(payload.get("page_range_text")),
        "采集范围规则": SCOPE_RULE,
        "可见行数": len(rows),
        "内容": rows,
    }
    overview_payload = {
        "schema_version": SCHEMA_VERSION,
        "账号ID": account_id,
        "账号名": account_name,
        "平台": platform,
        "采集时间": captured_at,
        **account_overview,
    }
    report = _build_report(
        root=root,
        platform=platform,
        account_id=account_id,
        account_name=account_name,
        run_id=args.run_id,
        captured_at=captured_at,
        formal_dir=formal_dir,
        latest_pointer=latest_pointer,
        rows=rows,
        account_overview=account_overview,
        existing_matched_ids=existing_matched_ids,
        created_ids=created_ids,
        created_titles=created_titles,
    )
    write_json_atomically(formal_dir / "内容分析.json", content_payload)
    write_json_atomically(formal_dir / "账号概览.json", overview_payload)
    write_json_atomically(formal_dir / "采集报告.json", report)
    write_json_atomically(
        latest_pointer,
        {
            "schema_version": SCHEMA_VERSION,
            "采集时间": captured_at,
            "采集目录": _relative(root, formal_dir),
            "内容分析": _relative(root, formal_dir / "内容分析.json"),
            "账号概览": _relative(root, formal_dir / "账号概览.json"),
            "采集报告": _relative(root, formal_dir / "采集报告.json"),
            "可见行数": len(rows),
            "已匹配数": report["matched_count"],
            "未匹配数": report["unmatched_count"],
            "新建内容数": report["created_count"],
            "账号概览状态": report["account_overview_status"],
        },
    )
    return {"ok": True, "status": "committed", **report}


def run(args: argparse.Namespace) -> Dict[str, Any]:
    if args.action == "prepare":
        return run_prepare(args)
    return run_commit(args)


def main() -> int:
    try:
        print(json.dumps(run(parse_args()), ensure_ascii=False, indent=2))
        return 0
    except CreatorCenterError as exc:
        print(json.dumps({"ok": False, "status": "failed", "error_code": exc.code, "message": str(exc)}, ensure_ascii=False))
        return 1
    except Exception as exc:  # noqa: BLE001
        print(json.dumps({"ok": False, "status": "failed", "error_code": "unexpected_error", "message": str(exc)}, ensure_ascii=False))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
