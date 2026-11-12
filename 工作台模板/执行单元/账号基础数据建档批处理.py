#!/usr/bin/env python3
"""标准化并回写账号基础建档结果。

本脚本只处理结构化结果的合并与落盘，不负责浏览器/API 抓取。
抓取阶段应先产出账号基础信息 JSON 与账号内容原始列表 JSON，再调用本脚本完成：
- 账号目录创建
- 账号库回写
- 内容列表标准化、内容ID分配与合并
- 批处理报告与阶段状态写入
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
import shutil
import sys
import urllib.parse
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Tuple

from csv安全写入 import read_csv_table_with_snapshot, write_csv_tables_atomically
from 全局内容ID import content_id_number, next_content_id as next_global_content_id
import 证书信任  # noqa: F401  进程级统一 HTTPS 证书信任
from 权限错误 import permission_error_code
from 账号阶段状态 import build_account_stage_state
from 发布时间解析 import normalize_publish_time_text

ACCOUNT_BASE_FIELDS = [
    "账号ID",
    "账号名",
    "平台",
    "简介",
    "性别",
    "关注数",
    "粉丝数",
    "获赞数",
    "标签",
    "主页链接",
    "头像原链接",
]

BENCHMARK_ACCOUNT_FIELDS = [
    *ACCOUNT_BASE_FIELDS,
    "头像路径",
    "图片说明路径",
    "内容列表路径",
    "人工先验说明路径",
    "概要分析路径",
    "详细分析路径",
    "录入时间",
    "质量判断",
    "适配判断",
    "备注",
]

SELF_ACCOUNT_FIELDS = [
    *ACCOUNT_BASE_FIELDS,
    "头像路径",
    "图片说明路径",
    "内容列表路径",
    "过程记录路径",
    "备注",
]

CONTENT_LIST_FIELDS = [
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
]

PRESERVE_CONTENT_FIELDS = [
    "内容ID",
    "内容目录路径",
    "封面图路径",
    "视频下载链接",
    "核心互动数",
    "数据水平",
    "发布时间",
    "发布时间区间",
    "视频文案路径",
    "内容分析路径",
]

LIST_LAYER_FIELDS = [
    "平台原生内容ID",
    "平台",
    "账号ID",
    "账号名",
    "标题",
    "封面图原链接",
    "媒介形式",
    "公开状态",
    "点赞数",
    "收藏数",
    "分享数",
    "评论数",
    "发布时间",
    "对外页面链接",
    "基础数据获取方式",
    "bvid",
    "aid",
    "cid",
]

VALID_ACCOUNT_TYPES = {
    "benchmark": "benchmark",
    "对标": "benchmark",
    "duibiao": "benchmark",
    "self": "self",
    "我方": "self",
    "wofang": "self",
}
BENCHMARK_NEXT_FLOWS = {
    "执行流程/账号概要分析流程.md",
    "执行流程/账号详细数据获取流程.md",
    "执行流程/账号详细分析流程.md",
}
DEFAULT_BENCHMARK_NEXT_FLOW = "执行流程/账号详细数据获取流程.md"

RUN_ID_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}")
STAGING_FLOW_ROOTS = ("benchmark-account-diagnose", "self-account-diagnose")


def derive_run_id(*candidate_values: str) -> str:
    """从本轮暂存目录名推导 run_id。

    本轮暂存目录固定为 `<工作区>/.meiling/staging/<flow-root>/<run_id>/`，
    账号基础信息和内容原始列表候选 JSON 都放在该目录下，因此 run_id 恒等于
    候选文件所在目录名，与入口执行器分配值一致。目录形状不符时不猜测。
    """
    for value in candidate_values:
        raw = str(value or "").strip()
        if not raw:
            continue
        candidate_dir = Path(raw).expanduser().resolve().parent
        if candidate_dir.parent.name not in STAGING_FLOW_ROOTS:
            continue
        candidate_run_id = candidate_dir.name
        if RUN_ID_PATTERN.fullmatch(candidate_run_id):
            return candidate_run_id
    return ""


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="回写账号基础建档结果，生成账号库、内容列表、报告和阶段状态。")
    parser.add_argument("--workspace-root", default=".", help="当前工作区根目录")
    parser.add_argument("--account-type", required=True, help="账号类型：benchmark/对标 或 self/我方")
    parser.add_argument("--task-id", default="", help="可选：我方账号公开调度任务编号")
    parser.add_argument("--account-info-json", required=True, help="获取账号基础信息执行单元产出的 JSON")
    parser.add_argument(
        "--raw-list-json",
        default=[],
        action="append",
        help="获取账号内容原始列表执行单元产出的 JSON；正式建档必填，多来源时重复传入本参数",
    )
    parser.add_argument(
        "--prepare-only",
        action="store_true",
        help="只检查账号身份、重复记录和目标目录，不创建目录或写入正式文件",
    )
    parser.add_argument(
        "--acquisition-attempt",
        type=int,
        choices=(1, 2),
        default=1,
        help="本轮来源采集尝试次数；最多允许首次采集和一次集中重试",
    )
    parser.add_argument(
        "--data-source",
        choices=("chrome_devtools", "justoneapi"),
        default="",
        help="用户已明确选择的数据源；传入后禁止候选结果偷换来源",
    )
    parser.add_argument("--avatar-file", default="", help="可选：已抓取的本地头像文件，复制为当前账号目录的头像文件")
    parser.add_argument("--account-dir", default="", help="可选：强制指定当前账号目录")
    parser.add_argument("--expected-platform", default="", help="可选：取数前已经确认的目标平台")
    parser.add_argument("--expected-account-id", default="", help="可选：取数前已经确认的目标账号ID")
    parser.add_argument(
        "--run-id",
        default="",
        help="可选：本轮批次唯一标识；不传时从候选 JSON 所在的暂存目录名推导",
    )
    parser.add_argument(
        "--next-flow",
        default="",
        help="可选：对标账号基础建档完成后的返回流程，只接受概要、详细数据或详细分析流程",
    )
    parser.add_argument("--report-json", default="", help="可选：批处理报告输出路径")
    parser.add_argument("--allow-suspected-duplicate", action="store_true", help="允许同平台同账号名但账号ID不同的疑似重复记录继续建档")
    parser.add_argument("--dry-run", action="store_true", help="只生成报告和 stdout，不正式写入 CSV/状态文件")
    return parser.parse_args()


def normalize_account_type(value: str) -> str:
    normalized = VALID_ACCOUNT_TYPES.get(str(value or "").strip().lower()) or VALID_ACCOUNT_TYPES.get(str(value or "").strip())
    if not normalized:
        raise ValueError("--account-type 只支持 benchmark/对标 或 self/我方")
    return normalized


def normalize_benchmark_next_flow(value: str) -> str:
    normalized = str(value or "").strip() or DEFAULT_BENCHMARK_NEXT_FLOW
    if normalized not in BENCHMARK_NEXT_FLOWS:
        raise ValueError("--next-flow 只支持账号概要分析、账号详细数据获取或账号详细分析流程")
    return normalized


def read_json(path: Path) -> Dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        data = json.load(handle)
    if not isinstance(data, dict):
        raise ValueError(f"JSON 根节点必须是对象: {path}")
    return data


def read_csv_rows(path: Path) -> Tuple[List[str], List[Dict[str, str]]]:
    if not path.exists():
        return [], []
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        return list(reader.fieldnames or []), [dict(row) for row in reader]


def read_csv_rows_with_snapshot(
    path: Path,
) -> Tuple[List[str], List[Dict[str, str]], bytes | None]:
    if not path.exists():
        return [], [], None
    fieldnames, rows, payload = read_csv_table_with_snapshot(path)
    return fieldnames, rows, payload


def write_json(path: Path, payload: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    tmp_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp_path.replace(path)


def file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def ensure_fieldnames(existing: Iterable[str], required: Iterable[str]) -> List[str]:
    result = [field for field in existing if field]
    for field in required:
        if field not in result:
            result.append(field)
    return result


def sanitize_path_component(value: str) -> str:
    text = str(value or "").strip() or "未命名"
    text = re.sub(r"[\\/:*?\"<>|\n\r\t]+", "-", text)
    text = re.sub(r"\s+", " ", text).strip(" .")
    return text[:80] or "未命名"


def normalize_number_text(value: Any) -> str:
    if value is None:
        return ""
    text = str(value).strip()
    if not text:
        return ""
    text = text.replace(",", "")
    match = re.fullmatch(r"([0-9]+(?:\.[0-9]+)?)(万|w|W)?", text)
    if match:
        number = float(match.group(1))
        if match.group(2):
            number *= 10000
        return str(int(number))
    digits = re.sub(r"[^0-9-]", "", text)
    return "" if digits in {"", "-"} else digits


def normalize_url(value: Any) -> str:
    text = str(value or "").strip()
    if not text.startswith(("http://", "https://")):
        return ""
    return text.split("#", 1)[0].rstrip("/")


def normalize_page_url(value: Any) -> str:
    """Remove transient query/fragment data from account and content page identities."""
    text = normalize_url(value)
    if not text:
        return ""
    parsed = urllib.parse.urlsplit(text)
    return urllib.parse.urlunsplit(
        (parsed.scheme, parsed.netloc, parsed.path.rstrip("/"), "", "")
    ).rstrip("/")


def normalize_text(value: Any) -> str:
    return str(value or "").strip()


def normalize_publish_time(value: Any) -> str:
    """Normalize API/Chrome timestamps and relative display dates."""
    return normalize_publish_time_text(value)


def normalize_public_status(
    value: Any,
    *,
    is_private: Any = None,
    is_published: Any = None,
    is_draft: Any = None,
    is_unpublished: Any = None,
) -> str:
    """Normalize observed content visibility without dropping non-public items."""
    if is_private is True or normalize_text(is_private).lower() in {"1", "true", "yes", "是"}:
        return "私密"
    if is_draft is True or normalize_text(is_draft).lower() in {"1", "true", "yes", "是"}:
        return "草稿"
    if is_unpublished is True or normalize_text(is_unpublished).lower() in {"1", "true", "yes", "是"}:
        return "草稿"
    if is_published is False or normalize_text(is_published).lower() in {"0", "false", "no", "否"}:
        return "草稿"
    marker = re.sub(r"[\s-]+", "_", normalize_text(value).lower())
    if not marker:
        return "未知"
    if marker in {"public", "visible", "public_page_visible", "published", "公开", "已发布"}:
        return "公开"
    if marker in {"draft", "unpublished", "not_published", "未发布", "草稿"}:
        return "草稿"
    if marker in {"private", "self_only", "only_self", "only_me", "仅自己可见", "私密"}:
        return "私密"
    if marker in {"unknown", "未知"}:
        return "未知"
    return "其他"


def normalize_formal_content_visibility(
    value: Any,
    *,
    account: Dict[str, Any],
) -> str:
    """Apply the sole formal visibility rule at the content merge boundary.

    Benchmark intake only accepts content from the account's public homepage,
    so observed placeholders cannot override that page-level fact. Self-account
    rows keep explicit private/draft observations, but an unobserved status
    ("未知") is formalized as public: what the login-free public page lists is
    public content, and a lingering "未知" would drop it from the public scope
    in every downstream stage.
    """
    account_type = normalize_text(
        account.get("账号类型") or account.get("account_type")
    ).lower()
    if account_type in {"benchmark", "对标", "duibiao"}:
        return "公开"
    status = normalize_public_status(value)
    return status


def source_family(value: Any) -> str:
    normalized = normalize_text(value).lower()
    if "chrome" in normalized or "devtools" in normalized:
        return "chrome_devtools"
    if "justoneapi" in normalized:
        return "justoneapi"
    return ""


def pick(data: Dict[str, Any], *keys: str) -> str:
    for key in keys:
        value = data.get(key)
        if value not in (None, ""):
            return normalize_text(value)
    return ""


def account_info_node(payload: Dict[str, Any]) -> Dict[str, Any]:
    for key in ["账号基础信息", "account_info", "data", "fields"]:
        node = payload.get(key)
        if isinstance(node, dict):
            merged = dict(payload)
            merged.update(node)
            return merged
    return payload


def normalize_account_info(payload: Dict[str, Any]) -> Dict[str, str]:
    node = account_info_node(payload)
    status = pick(node, "状态", "status")
    if status and status not in {"成功", "success", "ok"}:
        reason = pick(node, "失败原因", "error", "message") or "账号基础信息执行单元返回失败"
        raise ValueError(reason)
    result = {
        "账号ID": pick(node, "账号ID", "account_id", "sec_uid", "userid", "mid"),
        "账号名": pick(node, "账号名", "account_name", "nickname", "name"),
        "平台": pick(node, "平台", "platform"),
        "简介": pick(node, "简介", "desc", "signature", "sign", "bio"),
        "性别": pick(node, "性别", "gender", "sex"),
        "关注数": normalize_number_text(pick(node, "关注数", "following_count", "follows", "following")),
        "粉丝数": normalize_number_text(pick(node, "粉丝数", "follower_count", "fans", "follower")),
        "获赞数": normalize_number_text(pick(node, "获赞数", "total_favorited", "liked_count", "like_count")),
        "标签": pick(node, "标签", "tags", "account_labels"),
        "主页链接": normalize_page_url(pick(node, "主页链接", "账号主页链接", "home_url", "profile_url", "url")),
        "头像原链接": normalize_url(pick(node, "头像原链接", "avatar_url", "avatar_larger", "avatar_medium", "avatar_thumb", "head_url", "avatar")),
        "基础数据获取方式": pick(node, "基础数据获取方式", "fetch_method", "source"),
    }
    missing = [field for field in ["平台", "账号ID", "账号名", "主页链接"] if not result.get(field)]
    if missing:
        raise ValueError("账号基础信息缺失: " + ", ".join(missing))
    return result


def raw_list_node(payload: Dict[str, Any]) -> Tuple[List[Dict[str, Any]], Dict[str, str]]:
    status_info = {
        "状态": pick(payload, "状态", "status") or "成功",
        "基础数据获取方式": pick(payload, "基础数据获取方式", "fetch_method", "source"),
        "平台": pick(payload, "平台", "platform"),
        "内容列表获取状态": pick(payload, "内容列表获取状态", "list_status") or "失败",
        "内容列表停止原因": pick(payload, "内容列表停止原因", "stop_reason"),
        "内容列表停止位置": pick(payload, "内容列表停止位置", "stop_position"),
        "内容列表为空原因": pick(payload, "内容列表为空原因", "empty_reason"),
        "主页长截图状态": pick(payload, "主页长截图状态", "screenshot_status") or "跳过",
        "主页长截图路径": pick(payload, "主页长截图路径", "screenshot_path"),
        "主页长截图结果JSON路径": pick(payload, "主页长截图结果JSON路径", "screenshot_result_json"),
        "主页长截图失败原因": pick(payload, "主页长截图失败原因", "screenshot_error"),
        "失败原因": pick(payload, "失败原因", "error", "message"),
    }
    for key in ["账号内容原始列表", "content_items", "items", "data"]:
        node = payload.get(key)
        if isinstance(node, list):
            return [item for item in node if isinstance(item, dict)], status_info
        if isinstance(node, dict):
            for inner_key in ["账号内容原始列表", "content_items", "items", "list"]:
                inner = node.get(inner_key)
                if isinstance(inner, list):
                    return [item for item in inner if isinstance(item, dict)], status_info
    return [], status_info


def has_usable_content_identity(item: Dict[str, Any], platform: str) -> bool:
    return bool(
        extract_native_id(platform, item)
        or normalize_url(pick(item, "对外页面链接", "内容链接", "note_url", "url", "share_url", "href"))
        or pick(item, "标题", "内容标题", "title", "desc")
    )


def merge_raw_list_payloads(
    payloads: List[Dict[str, Any]],
    account: Dict[str, str] | None = None,
) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    all_items: List[Dict[str, Any]] = []
    source_results: List[Dict[str, Any]] = []
    methods: List[str] = []
    stop_reasons: List[str] = []
    screenshot: Dict[str, str] | None = None
    confirmed_empty_sources = 0

    for payload in payloads:
        items, status = raw_list_node(payload)
        platform = account["平台"] if account else pick(payload, "平台", "platform")
        status["平台"] = platform
        if account:
            payload_platform = pick(payload, "平台", "platform")
            payload_account_id = pick(payload, "账号ID", "account_id")
            if payload_platform and payload_platform != account["平台"]:
                raise ValueError("原始内容列表的平台与当前账号不一致")
            if payload_account_id and payload_account_id != account["账号ID"]:
                raise ValueError("原始内容列表的账号ID与当前账号不一致")
            for item in items:
                item_platform = pick(item, "平台", "platform")
                item_account_id = pick(item, "账号ID", "account_id")
                if item_platform and item_platform != account["平台"]:
                    raise ValueError("原始内容列表中存在其他平台的内容")
                if item_account_id and item_account_id != account["账号ID"]:
                    raise ValueError("原始内容列表中存在其他账号的内容")
        invalid_item_count = sum(1 for item in items if not has_usable_content_identity(item, platform))
        items = [item for item in items if has_usable_content_identity(item, platform)]
        method = status.get("基础数据获取方式") or next(
            (pick(item, "基础数据获取方式", "fetch_method", "source") for item in items if pick(item, "基础数据获取方式", "fetch_method", "source")),
            "未知方式",
        )
        list_status = status.get("内容列表获取状态", "失败")
        evidence_issues: List[str] = []
        invalid_reason = ""
        if invalid_item_count:
            invalid_reason = f"过滤 {invalid_item_count} 条缺少内容ID、内容链接和标题的无效记录"
        # 内容列表状态只回答“是否拿到了可识别内容”，不回答是否完整。
        # 兼容上游旧的“部分成功”值，但只要有有效内容就按成功交接。
        # 上游明确声明“账号当前没有公开内容”时同样按成功交接：这不是取数失败，
        # 否则会走成失败重试，把空账号误判成抓取失败。
        declared_list_status = str(pick(payload, "内容列表获取状态", "list_status") or "").strip()
        source_confirms_empty = bool(
            not items
            and declared_list_status == "成功"
            and str(status.get("内容列表为空原因") or "").strip() == "no_public_content"
        )
        if source_confirms_empty:
            confirmed_empty_sources += 1
        list_status = "成功" if (items or source_confirms_empty) else "失败"
        if not items:
            invalid_reason = invalid_reason or (
                "" if source_confirms_empty else "数据源没有返回可识别的内容"
            )
        if method not in methods:
            methods.append(method)
        reason = status.get("内容列表停止原因") or status.get("失败原因")
        if invalid_reason:
            reason = "；".join(part for part in [reason, invalid_reason] if part)
        if reason and reason not in stop_reasons:
            stop_reasons.append(reason)
        source_results.append(
            {
                "基础数据获取方式": method,
                "内容列表获取状态": list_status,
                "内容条数": len(items),
                "过滤无效内容条数": invalid_item_count,
                "validation_issues": evidence_issues,
                "停止或失败原因": reason,
            }
        )
        all_items.extend(items)
        if screenshot is None or (
            status.get("主页长截图状态") == "成功" and screenshot.get("主页长截图状态") != "成功"
        ):
            screenshot = {key: status.get(key, "") for key in [
                "主页长截图状态",
                "主页长截图路径",
                "主页长截图结果JSON路径",
                "主页长截图失败原因",
            ]}

    if all_items:
        combined_status = "成功"
        combined_reason = "；".join(stop_reasons) or "已取得可识别内容"
        combined_empty_reason = ""
    elif payloads and confirmed_empty_sources == len(payloads):
        # 所有已验证来源都明确确认账号当前没有公开内容：这是“已确认无内容”，
        # 由正式建档判定 verified_empty，不得改写为取数失败。
        combined_status = "成功"
        combined_reason = "；".join(stop_reasons) or "已确认账号当前没有公开内容"
        combined_empty_reason = "no_public_content"
    else:
        combined_status = "失败"
        combined_reason = "；".join(stop_reasons) or "所有已验证数据源都未返回有效内容"
        combined_empty_reason = "no_identifiable_content"

    combined: Dict[str, Any] = {
        "状态": "成功" if combined_status != "失败" else "失败",
        "基础数据获取方式": " + ".join(methods),
        "内容列表获取状态": combined_status,
        "内容列表停止原因": combined_reason,
        "内容列表停止位置": " + ".join(methods),
        "内容列表为空原因": combined_empty_reason,
        "失败原因": combined_reason if combined_status == "失败" else "",
        "数据源结果": source_results,
        "validation_issues": [
            issue
            for result in source_results
            for issue in result.get("validation_issues", [])
        ],
    }
    combined.update(
        screenshot
        or {
            "主页长截图状态": "跳过",
            "主页长截图路径": "",
            "主页长截图结果JSON路径": "",
            "主页长截图失败原因": "",
        }
    )
    return all_items, combined


def account_library_path(workspace_root: Path, account_type: str) -> Path:
    if account_type == "benchmark":
        return workspace_root / "对标资料" / "对标账号库.csv"
    return workspace_root / "我方资料" / "我方账号库.csv"


def account_base_dir(workspace_root: Path, account_type: str) -> Path:
    if account_type == "benchmark":
        return workspace_root / "对标资料" / "对标账号"
    return workspace_root / "我方资料" / "我方账号"


def row_key(row: Dict[str, str]) -> Tuple[str, str]:
    return (normalize_text(row.get("平台")), normalize_text(row.get("账号ID")))


def find_account_rows(rows: List[Dict[str, str]], account: Dict[str, str]) -> Tuple[Dict[str, str] | None, List[Dict[str, str]]]:
    target_key = (account["平台"], account["账号ID"])
    exact = None
    suspected = []
    for row in rows:
        if row_key(row) == target_key:
            exact = row
        elif normalize_text(row.get("平台")) == account["平台"] and normalize_text(row.get("账号名")) == account["账号名"]:
            suspected.append(row)
    return exact, suspected


def infer_account_dir(
    account_type: str,
    workspace_root: Path,
    account: Dict[str, str],
    existing_row: Dict[str, str] | None,
    *,
    disambiguate_name: bool = False,
) -> Path:
    path_fields = ["头像路径", "内容列表路径", "图片说明路径", "人工先验说明路径", "过程记录路径"]
    if existing_row:
        for field in path_fields:
            value = normalize_text(existing_row.get(field))
            if not value:
                continue
            path = Path(value).expanduser()
            if not path.is_absolute():
                path = workspace_root / path
            try:
                path = path.resolve()
                if not path.exists() or workspace_root not in path.parents:
                    continue
            except OSError:
                continue
            return path.parent
    directory_name = f"{account['平台']}-{account['账号名']}"
    if disambiguate_name:
        directory_name += f"-{account['账号ID']}"
    return (account_base_dir(workspace_root, account_type) / sanitize_path_component(directory_name)).resolve()


def ensure_account_structure(account_type: str, account_dir: Path, dry_run: bool) -> Dict[str, Path]:
    paths = {
        "account_dir": account_dir,
        "avatar": account_dir / "头像.png",
        "image_dir": account_dir / "图片说明",
        "process_dir": account_dir / "过程记录",
        "content_list": account_dir / "内容列表.csv",
    }
    if account_type == "benchmark":
        paths["manual_prior"] = account_dir / "人工先验说明.md"
    if not dry_run:
        paths["image_dir"].mkdir(parents=True, exist_ok=True)
        paths["process_dir"].mkdir(parents=True, exist_ok=True)
        if account_type == "benchmark":
            paths["manual_prior"].parent.mkdir(parents=True, exist_ok=True)
            paths["manual_prior"].touch(exist_ok=True)
    return paths


def has_image_material(image_dir: Path) -> bool:
    """Return True only when 图片说明 actually holds at least one file.

    The directory is always created, because Chrome collection may write the
    homepage screenshot into it.  Registering its path unconditionally made
    every downstream stage re-discover an empty 图片说明, and made an
    interface-limited run (for example JustOneAPI, which never produces a
    screenshot) look like it had image material that merely failed to load.
    """
    try:
        if not image_dir.is_dir():
            return False
        return any(
            item.is_file() and not item.name.startswith(".")
            for item in image_dir.iterdir()
        )
    except OSError:
        return False


def find_existing_avatar(
    *,
    existing_row: Dict[str, str] | None,
    workspace_root: Path,
    account_dir: Path,
) -> Path | None:
    candidates: List[Path] = []
    recorded_path = normalize_text(existing_row.get("头像路径")) if existing_row else ""
    if recorded_path:
        recorded_avatar = Path(recorded_path).expanduser()
        if not recorded_avatar.is_absolute():
            recorded_avatar = workspace_root / recorded_avatar
        candidates.append(recorded_avatar)
    candidates.extend(account_dir / f"头像{extension}" for extension in [".png", ".jpg", ".jpeg", ".webp"])
    for candidate in candidates:
        try:
            if candidate.is_file():
                return candidate.resolve()
        except OSError:
            continue
    return None


def materialize_avatar_file(source: str, account_dir: Path, dry_run: bool) -> Path | None:
    if not source:
        return None
    source_path = Path(source).expanduser().resolve()
    if not source_path.is_file():
        raise ValueError(f"头像文件不存在: {source_path}")
    with source_path.open("rb") as handle:
        data = handle.read(64)
    extension = avatar_extension(data, "")
    target = account_dir / f"头像{extension}"
    if not dry_run:
        target.parent.mkdir(parents=True, exist_ok=True)
        if source_path == target.resolve():
            return target
        temporary = target.with_suffix(target.suffix + ".tmp")
        try:
            shutil.copyfile(source_path, temporary)
            temporary.replace(target)
        finally:
            temporary.unlink(missing_ok=True)
    return target


def materialize_screenshot_artifacts(
    list_status: Dict[str, Any], paths: Dict[str, Path], dry_run: bool
) -> Tuple[str, str]:
    if list_status.get("主页长截图状态") != "成功":
        return "", ""
    source_image = Path(str(list_status.get("主页长截图路径") or "")).expanduser().resolve()
    source_result = Path(str(list_status.get("主页长截图结果JSON路径") or "")).expanduser().resolve()
    if not source_image.is_file() or not source_result.is_file():
        raise ValueError("主页长截图或截图结果 JSON 不存在")
    with source_image.open("rb") as handle:
        png_signature = handle.read(8)
    if source_image.suffix.lower() != ".png" or png_signature != b"\x89PNG\r\n\x1a\n":
        raise ValueError("主页长截图不是有效 PNG 文件")
    result_payload = read_json(source_result)
    if result_payload.get("fullPage") is not True or str(result_payload.get("format") or "").lower() != "png":
        raise ValueError("主页长截图结果未确认 fullPage=true 和 PNG")

    target_image = paths["image_dir"] / "主页长截图.png"
    target_result = paths["process_dir"] / "主页长截图结果.json"
    if not dry_run:
        target_image.parent.mkdir(parents=True, exist_ok=True)
        if source_image != target_image.resolve():
            temporary = target_image.with_suffix(".png.tmp")
            try:
                shutil.copyfile(source_image, temporary)
                temporary.replace(target_image)
            finally:
                temporary.unlink(missing_ok=True)
        write_json(target_result, result_payload)
    return str(target_image), str(target_result)


def avatar_extension(data: bytes, content_type: str) -> str:
    normalized_type = content_type.split(";", 1)[0].strip().lower()
    if normalized_type in {"image/jpeg", "image/jpg"} or data.startswith(b"\xff\xd8\xff"):
        return ".jpg"
    if normalized_type == "image/png" or data.startswith(b"\x89PNG\r\n\x1a\n"):
        return ".png"
    if normalized_type == "image/webp" or data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return ".webp"
    raise ValueError("头像响应不是受支持的 PNG/JPG/WEBP 图片")


def prefer_web_image_url(url: str) -> str:
    """把 HEIC/HEIF 图片链接换成受支持的格式再下载。

    抖音图床按路径扩展名实时转码：同一个头像对象把 `.heic` 换成 `.jpeg` 会返回
    真正的 JPEG，因此无需引入 HEIC 解码依赖。非 HEIC 链接原样返回，行为不变。
    """
    cleaned = str(url or "").strip()
    if not cleaned:
        return cleaned
    parsed = urllib.parse.urlsplit(cleaned)
    rewritten_path = re.sub(r"\.(heic|heif)$", ".jpeg", parsed.path, flags=re.IGNORECASE)
    if rewritten_path == parsed.path:
        return cleaned
    return urllib.parse.urlunsplit(
        (parsed.scheme, parsed.netloc, rewritten_path, parsed.query, parsed.fragment)
    )


def download_avatar_file(url: str, account_dir: Path, dry_run: bool) -> Path | None:
    if not url or dry_run:
        return None
    url = prefer_web_image_url(url)
    request = urllib.request.Request(
        url,
        headers={"User-Agent": "Mozilla/5.0", "Accept": "image/*"},
        method="GET",
    )
    with urllib.request.urlopen(request, timeout=10) as response:
        content_type = response.headers.get("Content-Type", "")
        data = response.read(8 * 1024 * 1024 + 1)
    if not data:
        raise ValueError("头像响应为空")
    if len(data) > 8 * 1024 * 1024:
        raise ValueError("头像文件超过 8MB 限制")
    extension = avatar_extension(data, content_type)
    target = account_dir / f"头像{extension}"
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_suffix(target.suffix + ".tmp")
    try:
        temporary.write_bytes(data)
        temporary.replace(target)
    finally:
        temporary.unlink(missing_ok=True)
    return target


def extract_native_id(platform: str, item: Dict[str, Any]) -> str:
    for key in ["平台原生内容ID", "内容原生ID", "aweme_id", "video_id", "item_id", "note_id", "bvid", "aid", "cid"]:
        value = pick(item, key)
        if value:
            return value
    url = normalize_url(pick(item, "对外页面链接", "内容链接", "note_url", "url", "href"))
    if platform == "抖音":
        match = re.search(r"/video/(\d+)", url)
        return match.group(1) if match else ""
    if platform == "小红书":
        for pattern in (
            r"/explore/([0-9A-Za-z]+)",
            r"/discovery/item/([0-9A-Za-z]+)",
            r"/user/profile/[^/?#]+/([0-9A-Za-z]+)",
        ):
            match = re.search(pattern, url)
            if match:
                return match.group(1)
        return ""
    if platform == "B站":
        match = re.search(r"(BV[0-9A-Za-z]+)", url)
        return match.group(1) if match else ""
    return ""


def content_match_keys(row: Dict[str, Any], platform: str, account_id: str) -> List[Tuple[str, str]]:
    keys: List[Tuple[str, str]] = []
    native_id = extract_native_id(platform, row)
    if native_id:
        keys.append(("native", f"{platform}::{native_id}"))
    url = normalize_url(row.get("对外页面链接") or row.get("内容链接") or row.get("note_url") or row.get("url") or row.get("href"))
    if url:
        keys.append(("url", url))
    if keys:
        return keys
    title = normalize_text(row.get("标题") or row.get("内容标题") or row.get("title") or row.get("desc"))
    if title:
        keys.append(("title", f"{platform}::{account_id}::{title}"))
    return keys


def build_existing_content_indexes(rows: List[Dict[str, str]], platform: str, account_id: str) -> Dict[Tuple[str, str], Dict[str, str]]:
    indexes: Dict[Tuple[str, str], Dict[str, str]] = {}
    for row in rows:
        for key in content_match_keys(row, platform, account_id):
            indexes.setdefault(key, row)
    return indexes


def normalize_raw_content_item(
    item: Dict[str, Any],
    account: Dict[str, str],
) -> Dict[str, str]:
    platform = account["平台"]
    row = {field: "" for field in CONTENT_LIST_FIELDS}
    native_id = extract_native_id(platform, item)
    media_type = normalize_media_type_value(
        pick(item, "媒介形式", "type", "media_type"),
        platform,
    )
    public_status = normalize_public_status(
        pick(item, "公开状态", "可见性", "visibility", "publish_status", "publication_status"),
        is_private=item.get("is_private") if isinstance(item, dict) else None,
        is_published=item.get("is_published") if isinstance(item, dict) else None,
        is_draft=item.get("is_draft") if isinstance(item, dict) else None,
        is_unpublished=item.get("is_unpublished") if isinstance(item, dict) else None,
    )
    publish_time = normalize_publish_time(
        item.get("发布时间")
        or item.get("publish_time")
        or item.get("create_time")
        or item.get("time")
        or item.get("timestamp")
    )
    row.update(
        {
            "平台": platform,
            "账号ID": account["账号ID"],
            "账号名": account["账号名"],
            "标题": pick(item, "标题", "内容标题", "title", "desc"),
            "封面图原链接": normalize_url(pick(item, "封面图原链接", "cover", "cover_url", "pic")),
            "媒介形式": media_type,
            "公开状态": public_status,
            "点赞数": normalize_number_text(pick(item, "点赞数", "liked_count", "digg_count", "like_count", "play")),
            "发布时间": publish_time,
            "对外页面链接": normalize_url(pick(item, "对外页面链接", "内容链接", "note_url", "url", "share_url", "href")),
            "基础数据获取方式": pick(item, "基础数据获取方式", "fetch_method", "source")
            or account.get("基础数据获取方式", ""),
            "bvid": pick(item, "bvid", "BVID"),
            "aid": pick(item, "aid"),
            "cid": pick(item, "cid"),
            "平台原生内容ID": native_id,
        }
    )
    if platform == "B站" and native_id.startswith("BV") and not row["bvid"]:
        row["bvid"] = native_id
    return row


def normalize_media_type_value(value: Any, platform: str) -> str:
    marker = re.sub(r"[\s-]+", "_", normalize_text(value).lower())
    if marker in {"video", "video_note", "movie", "视频"}:
        return "视频"
    if marker in {"normal", "image", "images", "text", "note", "图文"}:
        return "图文"
    if marker in {"unknown", "未知"}:
        return "未知"
    if marker:
        return "其他"
    return "视频" if platform in {"抖音", "B站"} else "未知"


def merge_content_rows(existing: Dict[str, str] | None, incoming: Dict[str, str]) -> Dict[str, str]:
    result = {field: "" for field in CONTENT_LIST_FIELDS}
    if existing:
        result.update({field: normalize_text(existing.get(field)) for field in existing.keys()})
    for field in LIST_LAYER_FIELDS:
        if incoming.get(field):
            result[field] = incoming[field]
    for field in PRESERVE_CONTENT_FIELDS:
        if existing and normalize_text(existing.get(field)):
            result[field] = normalize_text(existing.get(field))
    for field in CONTENT_LIST_FIELDS:
        result.setdefault(field, "")
    result["媒介形式"] = normalize_media_type_value(result.get("媒介形式"), result.get("平台", ""))
    result["公开状态"] = normalize_public_status(result.get("公开状态"))
    return result


def assign_missing_content_ids(
    rows: List[Dict[str, str]],
    workspace_root: str | Path,
) -> List[str]:
    existing_ids = [normalize_text(row.get("内容ID")) for row in rows if normalize_text(row.get("内容ID"))]
    first_new_id = next_global_content_id(workspace_root, additional_ids=existing_ids)
    first_number = content_id_number(first_new_id)
    assert first_number is not None
    counter = first_number - 1
    assigned = []
    seen: set[str] = set()
    for row in rows:
        current = normalize_text(row.get("内容ID"))
        if current and current not in seen:
            seen.add(current)
            continue
        row["内容ID"] = ""
        counter += 1
        new_id = f"内容{counter:03d}"
        row["内容ID"] = new_id
        seen.add(new_id)
        assigned.append(new_id)
    return assigned


def normalize_and_merge_content_list(
    *,
    raw_items: List[Dict[str, Any]],
    existing_rows: List[Dict[str, str]],
    account: Dict[str, str],
    workspace_root: str | Path,
) -> Tuple[List[Dict[str, str]], List[str], int, int]:
    indexes = build_existing_content_indexes(existing_rows, account["平台"], account["账号ID"])
    used_existing_ids = set()
    merged_rows: List[Dict[str, str]] = []
    added_count = 0
    updated_count = 0

    seen_indexes: Dict[Tuple[str, str], int] = {}
    for item in raw_items:
        if not has_usable_content_identity(item, account["平台"]):
            continue
        incoming = normalize_raw_content_item(
            item,
            account,
        )
        keys = content_match_keys(incoming, account["平台"], account["账号ID"])
        stable_key = keys[0] if keys else ("title", incoming.get("标题", ""))
        matching_index = next((seen_indexes[key] for key in keys if key in seen_indexes), None)
        if matching_index is None and stable_key in seen_indexes:
            matching_index = seen_indexes[stable_key]
        if matching_index is not None:
            merged_rows[matching_index] = merge_content_rows(merged_rows[matching_index], incoming)
            for key in keys or [stable_key]:
                seen_indexes[key] = matching_index
            continue

        existing = None
        for key in keys:
            if key in indexes:
                existing = indexes[key]
                break
        if existing:
            used_existing_ids.add(id(existing))
            updated_count += 1
        else:
            added_count += 1
        merged_rows.append(merge_content_rows(existing, incoming))
        row_index = len(merged_rows) - 1
        for key in keys or [stable_key]:
            seen_indexes[key] = row_index

    for old_row in existing_rows:
        if id(old_row) not in used_existing_ids:
            merged_rows.append({field: normalize_text(old_row.get(field)) for field in ensure_fieldnames(old_row.keys(), CONTENT_LIST_FIELDS)})

    # This is the only formal content merge boundary. Normalize both incoming
    # rows and retained CSV rows together so new, updated, and untouched rows
    # share the same benchmark/public-homepage truth.
    for row in merged_rows:
        row["公开状态"] = normalize_formal_content_visibility(
            row.get("公开状态"), account=account
        )

    assigned = assign_missing_content_ids(merged_rows, workspace_root)
    return merged_rows, assigned, added_count, updated_count


def stored_relative_path(workspace_root: Path, path: Path) -> str:
    """把工作区内路径统一写成相对工作区根的 posix 路径。

    账号库的路径字段落库形态必须唯一。绝对路径在迁移工作区、打包交付时会
    把本机目录带出去；读取端两种形态都能解析（infer_account_dir、
    resolve_under、normalizeStoredPath 等都有相对分支），但写入端只允许
    相对形态。工作区外的路径没有可移植形式，写空值。
    """
    try:
        return path.resolve().relative_to(Path(workspace_root).resolve()).as_posix()
    except (OSError, ValueError):
        return ""


def build_account_row(
    *,
    account_type: str,
    account: Dict[str, str],
    existing_row: Dict[str, str] | None,
    paths: Dict[str, Path],
    content_list_status: str,
    workspace_root: Path,
) -> Dict[str, str]:
    fields = BENCHMARK_ACCOUNT_FIELDS if account_type == "benchmark" else SELF_ACCOUNT_FIELDS
    row = {field: "" for field in fields}
    if existing_row:
        row.update({field: normalize_text(existing_row.get(field)) for field in existing_row.keys()})
    for field in ACCOUNT_BASE_FIELDS:
        if account.get(field):
            row[field] = account[field]
    row["头像路径"] = stored_relative_path(workspace_root, paths["avatar"]) if paths["avatar"].is_file() else ""
    row["图片说明路径"] = stored_relative_path(workspace_root, paths["image_dir"]) if has_image_material(paths["image_dir"]) else ""
    row["内容列表路径"] = stored_relative_path(workspace_root, paths["content_list"])
    if account_type == "benchmark":
        row["人工先验说明路径"] = stored_relative_path(workspace_root, paths["manual_prior"])
        row["录入时间"] = normalize_text(existing_row.get("录入时间")) if existing_row else datetime.now().isoformat(timespec="seconds")
        for field in ["概要分析路径", "详细分析路径", "质量判断", "适配判断"]:
            row[field] = normalize_text(existing_row.get(field)) if existing_row else ""
    else:
        row["过程记录路径"] = stored_relative_path(workspace_root, paths["process_dir"])

    is_refresh = bool(existing_row)
    if content_list_status == "成功":
        row["备注"] = "当前账号已刷新基础数据；内容列表已同步刷新" if is_refresh else "当前账号已完成基础建档与内容列表建档"
    elif content_list_status == "部分成功":
        row["备注"] = "当前账号已刷新基础数据，内容列表仅部分获取成功" if is_refresh else "当前账号已完成基础建档，内容列表仅部分获取成功"
    else:
        row["备注"] = "当前账号已刷新基础数据，内容列表获取失败" if is_refresh else "当前账号已完成基础建档，内容列表获取失败"
    return row


def upsert_account_row(rows: List[Dict[str, str]], account_row: Dict[str, str]) -> List[Dict[str, str]]:
    target = row_key(account_row)
    replaced = False
    result = []
    for row in rows:
        if row_key(row) == target:
            result.append(account_row)
            replaced = True
        else:
            result.append(row)
    if not replaced:
        result.append(account_row)
    return result


def validate_intake_tables(
    *,
    account: Dict[str, str],
    account_rows: List[Dict[str, str]],
    content_rows: List[Dict[str, str]],
    expected_content_list_path: Path,
) -> None:
    target = (account["平台"], account["账号ID"])
    matching_accounts = [row for row in account_rows if row_key(row) == target]
    if len(matching_accounts) != 1:
        raise ValueError(
            "账号建档主表校验失败: "
            f"期望唯一账号 {target[0]}/{target[1]}，实际 {len(matching_accounts)} 条"
        )
    stored_content_path = Path(normalize_text(matching_accounts[0].get("内容列表路径"))).expanduser()
    if not stored_content_path.is_absolute():
        stored_content_path = expected_content_list_path.parents[3] / stored_content_path
    if stored_content_path.resolve() != expected_content_list_path.resolve():
        raise ValueError("账号建档主表校验失败: 内容列表路径与正式文件不一致")
    mismatched = [
        row
        for row in content_rows
        if row_key(row) != target
        and any(normalize_text(value) for value in row.values())
    ]
    if mismatched:
        raise ValueError("账号建档内容列表校验失败: 存在其他账号的内容行")

    content_ids = [normalize_text(row.get("内容ID")) for row in content_rows if normalize_text(row.get("内容ID"))]
    if len(content_ids) != len(set(content_ids)):
        raise ValueError("账号建档内容列表校验失败: 内容ID重复")

    seen_identities: set[Tuple[str, str]] = set()
    for row in content_rows:
        if not any(normalize_text(value) for value in row.values()):
            continue
        keys = content_match_keys(row, account["平台"], account["账号ID"])
        if not keys:
            continue
        identity = keys[0]
        if identity in seen_identities:
            raise ValueError("账号建档内容列表校验失败: 内容身份重复")
        seen_identities.add(identity)


def validate_preserved_content_fields(
    *,
    existing_rows: List[Dict[str, str]],
    committed_rows: List[Dict[str, str]],
    account: Dict[str, str],
) -> bool:
    committed_by_id = {
        normalize_text(row.get("内容ID")): row
        for row in committed_rows
        if normalize_text(row.get("内容ID"))
    }
    committed_by_key: Dict[Tuple[str, str], Dict[str, str]] = {}
    for row in committed_rows:
        for key in content_match_keys(row, account["平台"], account["账号ID"]):
            committed_by_key.setdefault(key, row)

    for existing in existing_rows:
        if not any(normalize_text(value) for value in existing.values()):
            continue
        # 非公开内容按新规则不保留：允许从正式列表移除，不要求逐字段保留。
        if normalize_public_status(existing.get("公开状态")) != "公开":
            continue
        content_id = normalize_text(existing.get("内容ID"))
        committed = committed_by_id.get(content_id) if content_id else None
        if committed is None:
            committed = next(
                (
                    committed_by_key[key]
                    for key in content_match_keys(existing, account["平台"], account["账号ID"])
                    if key in committed_by_key
                ),
                None,
            )
        if committed is None:
            raise ValueError(f"已有内容记录丢失: {content_id or normalize_text(existing.get('标题')) or '未知内容'}")
        for field in PRESERVE_CONTENT_FIELDS:
            old_value = normalize_text(existing.get(field))
            if old_value and normalize_text(committed.get(field)) != old_value:
                raise ValueError(f"已有详细字段被清空或改写: {content_id or '未知内容'} / {field}")
    return True


def confirm_committed_files(*paths: Path) -> Dict[str, str]:
    """确认原子写入后的文件存在且可读，业务不变量已在写入前完成。"""
    hashes: Dict[str, str] = {}
    for path in paths:
        if not path.is_file() or path.is_symlink():
            raise ValueError(f"正式建档文件写回确认失败: {path}")
        hashes[str(path)] = file_sha256(path)
    return hashes


def default_report_path(paths: Dict[str, Path]) -> Path:
    return paths["process_dir"] / "批处理报告" / "本轮账号基础建档结果.json"


def resolve_report_path(
    *,
    account_type: str,
    requested_path: str,
    paths: Dict[str, Path],
) -> Tuple[Path, bool]:
    """Keep self-account handoff reports at the dispatcher-owned formal path."""
    formal_path = default_report_path(paths)
    if account_type == "self":
        requested = Path(requested_path).expanduser().resolve() if requested_path else formal_path
        return formal_path, bool(requested_path and requested != formal_path)
    return (
        Path(requested_path).expanduser().resolve() if requested_path else formal_path,
        False,
    )


def build_stage_state(
    *,
    account_type: str,
    account: Dict[str, str],
    complete: bool,
    blockers: List[str],
    requested_next_flow: str = "",
    verified_empty: bool = False,
    recovery_context: Dict[str, Any] | None = None,
    stage_status: str = "",
) -> Dict[str, Any]:
    if account_type == "benchmark":
        stage = "账号基础数据获取"
        current_flow = "执行流程/账号基础数据获取流程.md"
        completed_flow = normalize_benchmark_next_flow(requested_next_flow)
    else:
        stage = "我方账号基础数据获取"
        current_flow = "执行流程/我方账号基础数据获取流程.md"
        completed_flow = "执行流程/我方账号详细数据获取流程.md"
    if complete and verified_empty:
        waiting_for_user = "当前没有公开内容；基础账号信息已建档，不进入详细数据获取"
        next_flows: Tuple[str, ...] = ()
    else:
        waiting_for_user = "" if complete else (blockers[0] if blockers else "处理当前取数问题")
        next_flows = (completed_flow if complete else current_flow,)
    state = build_account_stage_state(
        stage=stage,
        complete=complete,
        platform=account["平台"],
        account_id=account["账号ID"],
        account_name=account["账号名"],
        waiting_for_user=waiting_for_user,
        next_flows=next_flows,
        recovery_context=recovery_context,
        completion_reason="verified_empty" if complete and verified_empty else "",
    )
    state["stage_status"] = stage_status or (
        "verified_empty" if complete and verified_empty else "completed" if complete else "failed"
    )
    return state


def build_base_handoff(
    *,
    account: Dict[str, str],
    formal_commit: Dict[str, Any],
    complete: bool,
    verified_empty: bool,
    validation_status: str,
    blockers: List[str],
    data_limitations: List[str],
) -> Dict[str, Any]:
    """生成对标账号基础数据交给详细数据的最小入口合同。

    合同只负责确认基础建档是否允许交接，并提供唯一账号身份。详细数据流程必须
    重新读取账号库、内容列表和正式建档报告；路径、完整性及截图状态不在此复制。
    """
    ready = bool(
        formal_commit.get("validated")
        and complete
        and not verified_empty
        and validation_status == "accepted"
    )
    if ready:
        blocking_reason = ""
    elif verified_empty:
        blocking_reason = "账号当前没有可用于详细分析的公开内容"
    else:
        blocking_reason = next(
            (
                str(item).strip()
                for item in [*blockers, *data_limitations]
                if str(item).strip()
            ),
            "账号基础资料未完成正式建档或校验，不能进入详细数据获取",
        )

    return {
        "status": "ready" if ready else "blocked",
        "blocking_reason": blocking_reason,
        "platform": account["平台"],
        "account_id": account["账号ID"],
    }


def main() -> int:
    try:
        args = parse_args()
        workspace_root = Path(args.workspace_root).expanduser().resolve()
        account_type = normalize_account_type(args.account_type)
        task_id = str(getattr(args, "task_id", "") or "").strip()
        if task_id and not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}", task_id):
            raise ValueError("--task-id 必须是安全的单段标识")
        if account_type == "benchmark":
            requested_next_flow = normalize_benchmark_next_flow(args.next_flow)
        else:
            if str(args.next_flow or "").strip():
                raise ValueError("--next-flow 仅用于对标账号基础数据建档")
            requested_next_flow = ""
        account_payload = read_json(Path(args.account_info_json).expanduser().resolve())
        account_node = account_info_node(account_payload)
        account_info = normalize_account_info(account_payload)
        account_info["账号类型"] = account_type
        requested_run_id = normalize_text(args.run_id)
        if requested_run_id and not RUN_ID_PATTERN.fullmatch(requested_run_id):
            raise ValueError("--run-id 只允许字母、数字、点、下划线和短横线")
        derived_run_id = derive_run_id(
            args.account_info_json, *args.raw_list_json, args.report_json
        )
        if requested_run_id and derived_run_id and requested_run_id != derived_run_id:
            raise ValueError(
                f"--run-id {requested_run_id} 与本轮暂存目录 {derived_run_id} 不一致"
            )
        run_id = requested_run_id or derived_run_id
        expected_platform = normalize_text(args.expected_platform)
        expected_account_id = normalize_text(args.expected_account_id)
        if bool(expected_platform) != bool(expected_account_id):
            raise ValueError("预期账号身份必须同时提供平台和账号ID")
        if expected_platform and (
            account_info["平台"] != expected_platform
            or account_info["账号ID"] != expected_account_id
        ):
            raise ValueError(
                "实际取回的账号身份与本轮目标不一致: "
                f"预期 {expected_platform}/{expected_account_id}，"
                f"实际 {account_info['平台']}/{account_info['账号ID']}"
            )
        account_csv_path = account_library_path(workspace_root, account_type)
        account_fields, account_rows, account_original = read_csv_rows_with_snapshot(account_csv_path)
        required_account_fields = BENCHMARK_ACCOUNT_FIELDS if account_type == "benchmark" else SELF_ACCOUNT_FIELDS
        account_fields = ensure_fieldnames(account_fields, required_account_fields)
        existing_account, suspected_duplicates = find_account_rows(account_rows, account_info)
        account_dir = (
            Path(args.account_dir).expanduser().resolve()
            if args.account_dir
            else infer_account_dir(
                account_type,
                workspace_root,
                account_info,
                existing_account,
                disambiguate_name=bool(suspected_duplicates),
            )
        )
        if args.prepare_only:
            needs_confirmation = bool(suspected_duplicates and not args.allow_suspected_duplicate)
            prepare_result = {
                "ok": True,
                "mode": "prepare",
                "status": "duplicate_confirmation_required" if needs_confirmation else "ready",
                "account_exists": existing_account is not None,
                "account": account_info,
                "account_dir": str(account_dir),
                "expected_platform": account_info["平台"] if existing_account else "",
                "expected_account_id": account_info["账号ID"] if existing_account else "",
                "suspected_duplicates": [
                    {
                        "平台": normalize_text(row.get("平台")),
                        "账号ID": normalize_text(row.get("账号ID")),
                        "账号名": normalize_text(row.get("账号名")),
                    }
                    for row in suspected_duplicates
                ],
                "user_confirmation_required": needs_confirmation,
            }
            print(json.dumps(prepare_result, ensure_ascii=False, indent=2))
            return 0
        if not args.raw_list_json:
            raise ValueError("正式建档必须提供 --raw-list-json")
        selected_source = str(getattr(args, "data_source", "") or "").strip().lower()
        if not selected_source:
            raise ValueError("正式建档必须提供 --data-source")
        avatar_file = args.avatar_file or pick(account_node, "头像临时文件路径", "avatar_file")
        upstream_avatar_hint = pick(account_node, "头像获取提示", "avatar_warning")
        raw_list_payloads = [read_json(Path(path).expanduser().resolve()) for path in args.raw_list_json]
        if selected_source:
            account_source = source_family(account_info.get("基础数据获取方式"))
            if account_source != selected_source:
                actual = account_info.get("基础数据获取方式") or "缺失"
                raise ValueError(f"账号基础信息数据源 {actual} 与用户已选择的 {selected_source} 不一致")
            for payload in raw_list_payloads:
                source_method = pick(payload, "基础数据获取方式", "fetch_method", "source")
                # Multiple payloads are allowed for an explicit source-switch
                # merge; a single candidate must declare the selected source.
                if len(raw_list_payloads) == 1 and source_family(source_method) != selected_source:
                    raise ValueError(
                        f"内容列表数据源 {source_method or '缺失'} 与用户已选择的 {selected_source} 不一致"
                    )
        raw_items, list_status = merge_raw_list_payloads(raw_list_payloads, account_info)
        if list_status["内容列表获取状态"] not in {"成功", "失败"}:
            list_status["内容列表获取状态"] = "成功" if raw_items else "失败"

        if suspected_duplicates and not args.allow_suspected_duplicate:
            names = [f"{row.get('平台', '')}/{row.get('账号ID', '')}/{row.get('账号名', '')}" for row in suspected_duplicates]
            raise ValueError("发现同平台同账号名但账号ID不同的历史疑似重复账号，请人工确认后再加 --allow-suspected-duplicate: " + "；".join(names))

        content_status_value = list_status["内容列表获取状态"]
        validation_issues = list(list_status.get("validation_issues", []))
        # 失败且没有有效内容时，只有新账号需要停在暂存区；已有账号保留
        # 正式内容列表，仍可更新账号基础信息。
        failed_without_content = content_status_value == "失败" and not raw_items
        if failed_without_content and existing_account is None:
            reason = (
                list_status.get("内容列表停止原因")
                or list_status.get("失败原因")
                or "内容列表没有完整获取成功"
            )
            blockers = list(dict.fromkeys([reason, *validation_issues]))
            data_limitations = (
                [f"本轮取得 {len(raw_items)} 条候选内容，但列表没有确认完整；{reason}"]
                if raw_items
                else []
            )
            # 新账号的失败采集只保留在暂存区，避免重试被误认为已正式提交。
            staging_root = Path(args.account_info_json).expanduser().resolve().parent
            try:
                staging_root.relative_to(workspace_root / ".meiling" / "staging")
            except ValueError:
                staging_root = workspace_root / ".meiling" / "staging" / "account-intake-failure"
            failure_process_dir = staging_root / "过程记录"
            rejected_report_path = (
                Path(args.report_json).expanduser().resolve()
                if args.report_json
                else failure_process_dir / "批处理报告" / "本轮账号基础建档结果.json"
            )
            stage_state_path = failure_process_dir / "当前阶段状态.json"
            formal_content_path = account_dir / "内容列表.csv"
            _, existing_content_rows = read_csv_rows(formal_content_path)
            retry_required = args.acquisition_attempt == 1
            user_decision_required = args.acquisition_attempt >= 2
            recovery_context = None
            if account_type == "self":
                recovery_context = {
                    "status": "retryable_failed" if retry_required else "waiting_for_user",
                    "current_entry": "base_data",
                    "next_action": (
                        "retry_base_data" if retry_required else "await_base_failure_decision"
                    ),
                    "report_path": rejected_report_path.relative_to(workspace_root).as_posix(),
                    "data_source": selected_source,
                    "acquisition_attempt": args.acquisition_attempt,
                }
            stage_state = build_stage_state(
                account_type=account_type,
                account=account_info,
                complete=False,
                blockers=blockers,
                requested_next_flow=requested_next_flow,
                recovery_context=recovery_context,
            )
            if account_type == "self" and task_id:
                stage_state["task_id"] = task_id
            if run_id:
                stage_state["run_id"] = run_id
            screenshot_status = str(list_status.get("主页长截图状态") or "跳过").strip()
            screenshot_path = str(list_status.get("主页长截图路径") or "").strip()
            screenshot_result_json = str(list_status.get("主页长截图结果JSON路径") or "").strip()
            screenshot_failure = str(list_status.get("主页长截图失败原因") or "").strip()
            if screenshot_status == "成功":
                screenshot_files_valid = bool(
                    screenshot_path
                    and screenshot_result_json
                    and Path(screenshot_path).expanduser().is_file()
                    and Path(screenshot_result_json).expanduser().is_file()
                )
                if not screenshot_files_valid:
                    screenshot_status = "失败"
                    screenshot_path = ""
                    screenshot_result_json = ""
                    screenshot_failure = screenshot_failure or "截图状态为成功但暂存截图文件不存在"
            report = {
                "ok": True,
                **({"task_id": task_id} if account_type == "self" and task_id else {}),
                **({"run_id": run_id} if run_id else {}),
                "data_source": selected_source,
                "dry_run": bool(args.dry_run),
                "account_type": account_type,
                "account": account_info,
                "account_dir": str(account_dir),
                "account_exists": existing_account is not None,
                "account_library_path": str(account_csv_path),
                "content_list_path": str(formal_content_path),
                "avatar_path": "",
                "avatar_status": "跳过",
                "content_list_status": content_status_value,
                "stage_status": "failed",
                "content_list_empty_reason": list_status.get("内容列表为空原因", ""),
                "screenshot_status": screenshot_status,
                "screenshot_path": screenshot_path,
                "screenshot_result_json": screenshot_result_json,
                "screenshot_error": screenshot_failure,
                "complete": False,
                "verified_empty": False,
                "next_flow": "执行流程/我方账号基础数据获取流程.md" if account_type == "self" else "执行流程/账号基础数据获取流程.md",
                "official_content_written": False,
                "partial_content_list_path": "",
                "raw_item_count": len(raw_items),
                "final_content_count": len(existing_content_rows),
                "new_content_count": 0,
                "matched_existing_content_count": 0,
                "assigned_content_ids": [],
                "formal_commit": {"validated": False, "written": False},
                "file_hashes": {},
                "stage_state_path": str(stage_state_path),
                "report_json": str(rejected_report_path),
                "blockers": blockers,
                "data_limitations": data_limitations,
                "source_results": list_status.get("数据源结果", []),
                "warnings": [],
                "acquisition_attempt": args.acquisition_attempt,
                "validation_status": "rejected",
                "validation_issues": validation_issues,
                "retry_required": retry_required,
                "user_decision_required": user_decision_required,
            }
            if account_type == "benchmark":
                report["base_handoff"] = build_base_handoff(
                    account=account_info,
                    formal_commit=report["formal_commit"],
                    complete=False,
                    verified_empty=False,
                    validation_status="rejected",
                    blockers=blockers,
                    data_limitations=data_limitations,
                )
            if not args.dry_run:
                write_json(rejected_report_path, report)
                write_json(stage_state_path, stage_state)
            print(json.dumps(report, ensure_ascii=False, indent=2))
            return 0

        paths = ensure_account_structure(account_type, account_dir, args.dry_run)
        existing_avatar = find_existing_avatar(
            existing_row=existing_account,
            workspace_root=workspace_root,
            account_dir=account_dir,
        )
        if existing_avatar:
            paths["avatar"] = existing_avatar
        avatar_warnings: List[str] = [upstream_avatar_hint] if upstream_avatar_hint else []
        try:
            materialized_avatar = materialize_avatar_file(avatar_file, account_dir, args.dry_run)
        except (OSError, ValueError) as exc:
            code = permission_error_code(exc, "filesystem")
            if code:
                raise RuntimeError(code) from exc
            materialized_avatar = None
            avatar_warnings.append(f"头像保存失败，已跳过且不影响账号建档: {exc}")
        if materialized_avatar:
            paths["avatar"] = materialized_avatar
        elif not avatar_file and not existing_avatar and account_info.get("头像原链接"):
            try:
                downloaded_avatar = download_avatar_file(
                    account_info["头像原链接"],
                    account_dir,
                    args.dry_run,
                )
            except Exception as exc:  # noqa: BLE001
                downloaded_avatar = None
                avatar_warnings.append(f"头像自动下载失败，已跳过且不影响账号建档: {exc}")
            if downloaded_avatar:
                materialized_avatar = downloaded_avatar
                paths["avatar"] = downloaded_avatar
        content_fields, existing_content_rows, content_original = read_csv_rows_with_snapshot(paths["content_list"])
        content_fields = ensure_fieldnames(content_fields, CONTENT_LIST_FIELDS)
        merged_content_rows, assigned_ids, added_count, updated_count = normalize_and_merge_content_list(
            raw_items=raw_items,
            existing_rows=existing_content_rows,
            account=account_info,
            workspace_root=workspace_root,
        )

        official_content_written = False
        generated_files = [str(account_csv_path), str(paths["content_list"]), str(paths["process_dir"])]
        if materialized_avatar:
            generated_files.append(str(materialized_avatar))
        if not args.dry_run:
            paths["account_dir"].mkdir(parents=True, exist_ok=True)
            paths["image_dir"].mkdir(parents=True, exist_ok=True)
            paths["process_dir"].mkdir(parents=True, exist_ok=True)

        screenshot_warnings: List[str] = []
        try:
            screenshot_path, screenshot_result_json = materialize_screenshot_artifacts(
                list_status, paths, args.dry_run
            )
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            screenshot_path, screenshot_result_json = "", ""
            list_status["主页长截图状态"] = "失败"
            list_status["主页长截图路径"] = ""
            list_status["主页长截图结果JSON路径"] = ""
            list_status["主页长截图失败原因"] = str(exc)
            screenshot_warnings.append(f"主页长截图不可用，已跳过且不影响基础建档: {exc}")
        if screenshot_path:
            list_status["主页长截图路径"] = screenshot_path
            list_status["主页长截图结果JSON路径"] = screenshot_result_json
            generated_files.extend([screenshot_path, screenshot_result_json])

        partial_content_list_path = ""
        formal_content_rows = [
            row
            for row in merged_content_rows
            if normalize_public_status(row.get("公开状态")) == "公开"
        ]
        official_content_written = False

        account_row = build_account_row(
            account_type=account_type,
            account=account_info,
            existing_row=existing_account,
            paths=paths,
            content_list_status=content_status_value,
            workspace_root=workspace_root,
        )
        next_account_rows = upsert_account_row(account_rows, account_row)
        validate_intake_tables(
            account=account_info,
            account_rows=next_account_rows,
            content_rows=formal_content_rows,
            expected_content_list_path=paths["content_list"],
        )
        validate_preserved_content_fields(
            existing_rows=existing_content_rows,
            committed_rows=formal_content_rows,
            account=account_info,
        )
        write_content_file = not (failed_without_content and existing_account is not None)
        if not args.dry_run:
            tables = [(account_csv_path, account_fields, next_account_rows)]
            originals = {account_csv_path: account_original}
            if write_content_file:
                tables.append((paths["content_list"], content_fields, formal_content_rows))
                originals[paths["content_list"]] = content_original
            write_csv_tables_atomically(tables, expected_originals=originals)
            official_content_written = write_content_file
            confirm_committed_files(account_csv_path, paths["content_list"])
            formal_commit = {
                "written": True,
                "validated": True,
                "preserved_fields_validated": True,
                "account_library_path": str(account_csv_path),
                "content_list_path": str(paths["content_list"]),
                "content_list_written": write_content_file,
                "account_library_sha256": file_sha256(account_csv_path),
                "content_list_sha256": file_sha256(paths["content_list"]),
            }
        else:
            formal_commit = {
                "written": False,
                "validated": False,
                "preserved_fields_validated": False,
                "dry_run": True,
            }

        blockers: List[str] = []
        data_limitations: List[str] = []
        complete = False
        # 完整性不再触发重试或人工决策；已有有效内容直接正常交接。
        retry_required = False
        user_decision_required = False
        verified_empty = (
            content_status_value == "成功"
            and list_status.get("内容列表为空原因") == "no_public_content"
            and not formal_content_rows
        )
        if verified_empty:
            stage_status = "verified_empty"
            complete = True
        elif content_status_value == "成功":
            stage_status = "completed"
            complete = True
        elif args.dry_run:
            stage_status = "planned"
        elif failed_without_content and existing_account is not None:
            stage_status = "partial"
        else:
            stage_status = "partial"
        if failed_without_content and existing_account is not None:
            data_limitations.append("本轮内容列表获取失败，已保留既有正式内容列表")
        if list_status.get("内容列表停止原因") and content_status_value != "成功":
            data_limitations.append(str(list_status["内容列表停止原因"]))
        warnings = [*avatar_warnings, *screenshot_warnings]
        if screenshot_warnings:
            data_limitations.extend(screenshot_warnings)

        report_path, report_path_normalized = resolve_report_path(
            account_type=account_type,
            requested_path=args.report_json,
            paths=paths,
        )
        report_warnings = (
            ["我方账号基础建档报告已统一写入账号目录正式报告路径"]
            if report_path_normalized
            else []
        )

        stage_state = build_stage_state(
            account_type=account_type,
            account=account_info,
            complete=complete,
            blockers=blockers,
            requested_next_flow=requested_next_flow,
            verified_empty=verified_empty,
            stage_status=stage_status if stage_status != "planned" else "failed",
            recovery_context=(
                {
                    "status": "completed" if verified_empty else "waiting_for_user",
                    "current_entry": "base_data",
                    "next_action": (
                        ""
                        if verified_empty
                        else "await_detail_scope"
                        if complete
                        else "await_base_failure_decision"
                    ),
                    "report_path": report_path.relative_to(workspace_root).as_posix(),
                    "data_source": selected_source,
                    "acquisition_attempt": args.acquisition_attempt,
                }
                if account_type == "self"
                else None
            ),
        )
        # 正式报告与阶段状态必须记录同一 run_id（见《我方账号基础数据获取流程》）。
        # 候选文件不在 <流程根>/<run_id>/ 暂存目录下时推导不出来，此时不能静默成功：
        # 下一阶段的详细数据会因缺 run_id 直接失败，用户看到的是「上一步成功、下一步报错」。
        if not run_id and not args.dry_run:
            raise ValueError(
                "正式建档必须记录 run_id：请显式提供 --run-id，"
                "或把候选文件放在 <流程根>/<run_id>/ 的暂存目录下再跑"
            )
        if account_type == "self" and task_id:
            stage_state["task_id"] = task_id
        if run_id:
            stage_state["run_id"] = run_id
        stage_state_path = paths["process_dir"] / "当前阶段状态.json"
        generated_files.extend([str(stage_state_path), str(report_path)])

        report = {
            "ok": True,
            **({"task_id": task_id} if account_type == "self" and task_id else {}),
            **({"run_id": run_id} if run_id else {}),
            "data_source": selected_source,
            "dry_run": bool(args.dry_run),
            "account_type": account_type,
            "account": account_info,
            "account_dir": str(paths["account_dir"]),
            "account_library_path": str(account_csv_path),
            "content_list_path": str(paths["content_list"]),
            "avatar_path": str(paths["avatar"]) if paths["avatar"].is_file() else "",
            "avatar_status": "成功" if paths["avatar"].is_file() else "失败" if avatar_warnings else "跳过",
            "content_list_status": content_status_value,
            "stage_status": stage_status,
            "content_list_empty_reason": list_status.get("内容列表为空原因", ""),
            "screenshot_status": list_status.get("主页长截图状态", ""),
            "screenshot_path": list_status.get("主页长截图路径", ""),
            "screenshot_result_json": list_status.get("主页长截图结果JSON路径", ""),
            "complete": complete,
            "verified_empty": verified_empty,
            "next_flow": stage_state["下一步流程"][0] if stage_state["下一步流程"] else "",
            "official_content_written": official_content_written,
            "partial_content_list_path": partial_content_list_path,
            "raw_item_count": len(raw_items),
            "final_content_count": len(formal_content_rows),
            "new_content_count": added_count,
            "matched_existing_content_count": updated_count,
            "assigned_content_ids": assigned_ids,
            "formal_commit": formal_commit,
            "file_hashes": {
                "account_library_sha256": formal_commit.get("account_library_sha256", ""),
                "content_list_sha256": formal_commit.get("content_list_sha256", ""),
            },
            "stage_state_path": str(stage_state_path),
            "report_json": str(report_path),
            "blockers": blockers,
            "data_limitations": data_limitations,
            "source_results": list_status.get("数据源结果", []),
            "warnings": [*warnings, *report_warnings],
            "acquisition_attempt": args.acquisition_attempt,
            "validation_status": "accepted" if complete and not args.dry_run else "preview" if args.dry_run else "partial",
            "validation_issues": validation_issues,
            "retry_required": retry_required,
            "user_decision_required": user_decision_required,
        }
        if account_type == "benchmark":
            report["base_handoff"] = build_base_handoff(
                account=account_info,
                formal_commit=formal_commit,
                complete=complete,
                verified_empty=verified_empty,
                validation_status=str(report["validation_status"]),
                blockers=blockers,
                data_limitations=data_limitations,
            )
        if not args.dry_run:
            write_json(stage_state_path, stage_state)
            write_json(report_path, report)
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0
    except Exception as exc:  # noqa: BLE001
        error = {"ok": False, "error": str(exc)}
        error_code = permission_error_code(exc, "filesystem")
        if error_code:
            error["error_code"] = error_code
        print(json.dumps(error, ensure_ascii=False, indent=2), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
