#!/usr/bin/env python3
"""内容列表完整性的唯一判据。

这个字段回答的问题是：**这份内容列表能不能当作"该账号的全部内容"来用**。
它不回答"本轮取数有没有失败"，也不等于内容列表获取状态。

取值固定三选一：

- `完整`：采集侧明确声明已经到达列表末尾（确实拿到了平台给出的结束信号）。
- `可能未抓全`：内容列表获取状态不是 `成功`，或采集停止原因没有声明已到达末尾。
  默认取这个值。
- `未知`：基础建档报告缺失、不可读，或内容列表获取状态无法识别。

**判据只读两个输入**：`content_list_status` 和采集停止原因。

刻意**不读 `data_limitations`**。那个字段是"本轮交付的资料缺口"总表，里面混着
截图失败等与内容列表无关的条目；把它当作内容列表边界的信号，会让"截图失败"
被误报成"内容列表可能未抓全"。内容列表自身的异常已经由 `content_list_status`
表达（非 `成功` 即判可能未抓全），停止原因那条写入也只在状态非成功时发生，
所以不读 `data_limitations` 不会漏判。

采集侧只有在确实拿到平台结束信号时（例如接口返回 `has_more = false`、
页面明确显示没有更多内容、读到列表末页），才可以在停止原因里声明
"已到达列表末尾"；只是"滚动后不再出现新内容"不算，因为页面可能仍在加载。
没有声明就按 `可能未抓全` 处理。

判定按**分句**进行：先按句号和逗号切开停止原因，某个分句里同时满足
「含"到达末尾"类正向表述」且「不含任何否定词」时，才算声明到底。
这样可以避免「未能确认已到达列表末尾」这类否定句被正向词误判成已经到底。
多个分句之间独立判断，只要有一个肯定分句成立就算声明；采集侧不应当写出
自相矛盾的停止原因。

概要分析和详细分析必须共用本模块，不得各写一套判据：
概要侧由正式化执行器用本模块算出真值、再与候选稿里的值做等值校验，
详细侧由正式化执行器用本模块直接生成 `evidence_scope`。
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, List, Tuple

CONTENT_LIST_STATUSES = ("成功", "部分成功", "失败")

# 基础建档报告在账号目录中的固定相对位置。
BASE_REPORT_RELATIVE = Path("过程记录") / "批处理报告" / "本轮账号基础建档结果.json"

# 采集侧可以声明的"已到达末尾"表述。只匹配我们自己采集侧会写的说法，
# 不依赖任何平台字段名，因此不受平台改版影响。
COMPLETE_MARKERS = (
    "已到达列表末尾",
    "已到达末尾",
    "已到列表末尾",
    "已到末尾",
    "已确认到达列表末尾",
    "确认已到达列表末尾",
    "已确认到达末尾",
    "无更多内容",
    "没有更多内容",
    "无下一页",
    "已翻到最后一页",
    "读到列表末页",
    "已读取到列表末页",
    "读取到列表末页",
    "读到末尾",
    "已读取到末尾",
    "读取到末尾",
    "已读到最后",
    "已读完全部",
    "已抓全",
    "已抓取完整",
    "已完整抓取",
    "has_more=false",
    "has_more = false",
)

# 否定词。声明句里只要出现否定词，这句就不算"声明已到达末尾"，
# 否则「未能确认已到达列表末尾」会被正向词误判成已经到底。
NEGATION_MARKERS = ("未", "不", "没", "无", "无法", "不能", "别", "尚未")

_CLAUSE_SEPARATORS = re.compile(r"[。；;！!？?\n]+|，|,")

STOP_REASON_KEYS = ("停止或失败原因", "内容列表停止原因", "停止原因")


def _text(value: Any) -> str:
    return str(value or "").strip()


def declares_content_list_end(text: Any) -> bool:
    """判断一段文字里是否有"肯定地声明已到达列表末尾"的分句。"""
    content = _text(text)
    if not content:
        return False
    for clause in _CLAUSE_SEPARATORS.split(content):
        clause = clause.strip()
        if not clause:
            continue
        if not any(marker in clause for marker in COMPLETE_MARKERS):
            continue
        if any(negation in clause for negation in NEGATION_MARKERS):
            continue
        return True
    return False


def collect_stop_reasons(report: Any) -> List[str]:
    """从基础建档报告里取出全部采集侧停止原因，兼容历史字段名。"""
    if not isinstance(report, dict):
        return []
    reasons: List[str] = []
    legacy = _text(report.get("内容列表停止原因"))
    if legacy:
        reasons.append(legacy)
    sources = report.get("source_results")
    if isinstance(sources, list):
        for item in sources:
            if not isinstance(item, dict):
                continue
            for key in STOP_REASON_KEYS:
                value = _text(item.get(key))
                if value:
                    reasons.append(value)
    return reasons


def read_content_list_fact_from_report(report_path: Any) -> Tuple[str, List[str]]:
    """读基础建档报告，返回 (内容列表获取状态, 采集停止原因列表)。

    报告缺失、不可读或状态无法识别时返回 `("", [])`，调用方据此判 `未知`。
    """
    try:
        payload = json.loads(Path(report_path).read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValueError, json.JSONDecodeError):
        return "", []
    if not isinstance(payload, dict):
        return "", []
    status = _text(payload.get("content_list_status"))
    if status not in CONTENT_LIST_STATUSES:
        return "", []
    return status, collect_stop_reasons(payload)


def derive_content_list_completeness(
    content_list_status: Any,
    stop_reasons: Any = None,
) -> str:
    """按唯一判据推导内容列表完整性。只读状态与停止原因，不读 data_limitations。"""
    status = _text(content_list_status)
    if status not in CONTENT_LIST_STATUSES:
        return "未知"
    if status in {"部分成功", "失败"}:
        return "可能未抓全"
    if isinstance(stop_reasons, str):
        declared = stop_reasons
    elif isinstance(stop_reasons, (list, tuple)):
        declared = " ".join(_text(item) for item in stop_reasons)
    else:
        declared = ""
    if declares_content_list_end(declared):
        return "完整"
    return "可能未抓全"
