#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import tempfile
from pathlib import Path
from typing import Mapping

from csv安全写入 import read_csv_table_with_snapshot
from 内容特征词典 import ContentFeatureDictionaryError, load_feature_catalog
from 账号衍生字段计算批处理 import compute_account_rank


class ContentPatternReportError(RuntimeError):
    """Raised when a generated content-pattern report does not match its source result."""


# 自 2026-10-28 起本执行器服务新版账号级报告：固定 3 节，落账号目录。
# 第 1 节是机器数字区，第 3 节是标签效果表格区；第 2 节的打法由提示词
# 按机器统计整理，保存器只校验机器数字和节结构。
REPORT_TITLE = "账号内容规律分析"
REQUIRED_HEADINGS = (
    "1. 分析范围",
    "2. 内容打法",
    "3. 表现比较",
)
# 我方账号报告多一节「与上期相比」，并在第 1 节记录对比基线：
# 对标报告回答「他怎么做」，我方报告还要回答「我这期和上期变了什么」。
OWN_REQUIRED_HEADINGS = REQUIRED_HEADINGS + ("4. 与上期相比",)
OWN_BASELINE_LABEL = "对比基线"
OWN_STAGE_HEADING = "按环节看"
OWN_MECHANISM_HEADING = "按机制看"
OWN_MECHANISM_ORDER = ("吸引", "持续", "信任", "获得", "沉淀")
OWN_SUMMARY_HEADING = "我的打法小结"
OWN_UNDECIDED_HEADING = "暂时无法判断"
OWN_UNDECIDED_COLUMNS = 5
# 「暂时无法判断」只收没有明确判定的做法，出现 1 篇的不展开。
OWN_UNDECIDED_MIN_HITS = 2
REPORT_KIND_BENCHMARK = "benchmark"
REPORT_KIND_OWN = "own"
REPORT_KINDS = (REPORT_KIND_BENCHMARK, REPORT_KIND_OWN)
SCOPE_SECTION = "1. 分析范围"
EFFECT_SECTION = "3. 表现比较"
OWN_COMPARE_SECTION = "4. 与上期相比"
# 第 3 节先单列账号共性，再看内容类别，其余做法按「数据好／数据差」分组；
# 差不到阈值的做法不单列（只保留在逐条数据里，不展示也不判好坏）。
EFFECT_GROUP_COMMON = "common"
EFFECT_GROUP_CONTENT_TYPE = "content_type"
EFFECT_GROUP_HIGH = "high"
EFFECT_GROUP_LOW = "low"
EFFECT_GROUP_TITLES = (
    (EFFECT_GROUP_COMMON, "账号通常使用"),
    (EFFECT_GROUP_CONTENT_TYPE, "先看：选哪类内容"),
    (EFFECT_GROUP_HIGH, "数据好的内容更常用"),
    (EFFECT_GROUP_LOW, "数据差的内容更常用"),
)
CONTENT_TYPE_CATEGORY = "内容类别"
COMMON_TABLE_HEADERS = ("标签", "标签类型", "定义", "全部")
EFFECT_TABLE_HEADERS = (
    "标签",
    "标签类型",
    "定义",
    "高档",
    "中档",
    "低档",
    "结论",
)
COMMON_TABLE_COLUMNS = len(COMMON_TABLE_HEADERS)
EFFECT_TABLE_COLUMNS = len(EFFECT_TABLE_HEADERS)

# 三档对比：分档**只用于展示**，不参与「这算不算一条规律」的判定。
# 档次本身由「账号衍生字段计算批处理.compute_account_rank」定：
#   高＝互动数 ≥ 账号中位数 × 2 且排在全账号前 30%；
#   低＝互动数 ≤ 账号中位数 ÷ 1.5 且排在全账号后 30%；其余为中档。
INSTANCE_PATH = Path("沉淀结论/内容特征实例.csv")
RANK_HIGH = "高"
RANK_MIDDLE = "中"
RANK_LOW = "低"
RANK_TIERS = (RANK_HIGH, RANK_MIDDLE, RANK_LOW)
RANK_HIT_KEYS = {RANK_HIGH: "high", RANK_MIDDLE: "middle", RANK_LOW: "low"}
RANK_RULE_TEXT = (
    "头部＝互动数达到账号中位数的 2 倍且排在全账号前 30%；"
    "低档＝互动数只有中位数的 1.5 分之一及以下且排在全账号后 30%；其余为中档"
)
# 任一档内容少于此数就不做高低档判断。
RANK_MIN_TIER_SAMPLES = 2
# 高低档使用率至少相差 20 个百分点；做法至少出现 3 次，较多的一档至少出现 2 次。
RANK_GAP_MIN_PERCENT = 20
RANK_MIN_TOTAL_HITS = 3
RANK_MIN_LEADING_HITS = 2
# 覆盖 80% 以上内容的做法只记为账号共性，不参与好坏判断。
RANK_COMMON_COVERAGE = 0.8
VERDICT_COMMON = "账号通常使用"
VERDICT_INSUFFICIENT = "样本不足"
VERDICT_HIGH = "数据好的内容更常用"
VERDICT_LOW = "数据差的内容更常用"
VERDICT_NONE = "区分不出好坏"
VERDICT_UNDECIDED = (VERDICT_INSUFFICIENT, VERDICT_NONE)


def _field(markdown: str, label: str) -> str:
    matches = re.findall(rf"(?m)^-\s*{re.escape(label)}\s*[：:]\s*(.*?)\s*$", markdown)
    if len(matches) != 1 or not matches[0].strip():
        raise ContentPatternReportError(f"report_field_invalid:{label}")
    return matches[0].strip().strip("`")


def _section_body(markdown: str, name: str) -> str:
    pattern = re.compile(rf"(?m)^##\s+{re.escape(name)}\s*$")
    match = pattern.search(markdown)
    if match is None:
        raise ContentPatternReportError(f"report_section_missing:{name}")
    following = re.compile(r"(?m)^##\s+\d+\.\s*").search(markdown, match.end())
    body_end = following.start() if following else len(markdown)
    return markdown[match.end():body_end]


def _section_field(markdown: str, section: str, label: str) -> str:
    """只在该节里找字段。

    字段标签在正文别处以「- 标签：值」的形式出现（比如在第 4 节里补一句
    口径说明）不该被当成重复声明，所以把查找范围收窄到它该在的节。
    """
    return _field(_section_body(markdown, section), label)


def _subsection_body(section_body: str, heading_prefix: str) -> str:
    """在一节正文里按 `### 小标题` 取子节，找不到返回空串。

    我方报告的子标题带「我」的措辞（例如「按环节看：我每一步用什么形式」），
    这里只匹配前缀，不要求整句逐字相同。
    """
    matches = list(re.finditer(r"(?m)^###\s+(.+?)\s*$", section_body))
    for index, match in enumerate(matches):
        if match.group(1).startswith(heading_prefix):
            end = matches[index + 1].start() if index + 1 < len(matches) else len(section_body)
            return section_body[match.end():end]
    return ""


def _own_table_rows(text: str, header_first: str) -> list[list[str]]:
    """把子节里的表格数据行取出来，跳过表头与分隔线。"""
    rows: list[list[str]] = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line.startswith("|"):
            continue
        cells = [cell.strip() for cell in line.strip("|").split("|")]
        if not cells or not cells[0] or cells[0] == header_first:
            continue
        if all(set(cell) <= {"-", ":"} for cell in cells):
            continue
        rows.append(cells)
    return rows


def _validate_own_sections(markdown: str, expected: Mapping[str, object], rule_count: int) -> None:
    """我方报告特有的结构校验。

    比对标报告多出的内容都在这里守住：
    - 第 2 节必须有按环节表、完整的五个机制、非空小结（防止又生成一份只有概括句的残稿）；
    - 第 3 节的「暂时无法判断」只能收机器判为样本不足或区分不出好坏、且出现 2 篇以上的做法；
    - 第 4 节不能整节空着。
    """
    own_body = _section_body(markdown, "2. 内容打法")
    stage_body = _subsection_body(own_body, OWN_STAGE_HEADING)
    if not stage_body:
        raise ContentPatternReportError("own_stage_section_missing")
    if rule_count > 0 and not _own_table_rows(stage_body, "环节"):
        raise ContentPatternReportError("own_stage_table_empty")
    mechanism_body = _subsection_body(own_body, OWN_MECHANISM_HEADING)
    if not mechanism_body:
        raise ContentPatternReportError("own_mechanism_section_missing")
    names = [cells[0] for cells in _own_table_rows(mechanism_body, "机制")]
    if tuple(names) != OWN_MECHANISM_ORDER:
        raise ContentPatternReportError(f"own_mechanism_rows_invalid:{names}")
    summary_body = _subsection_body(own_body, OWN_SUMMARY_HEADING)
    if not summary_body.strip():
        raise ContentPatternReportError("own_summary_missing")

    effect_body = _section_body(markdown, EFFECT_SECTION)
    undecided_body = _subsection_body(effect_body, OWN_UNDECIDED_HEADING)
    if not undecided_body:
        raise ContentPatternReportError("own_undecided_section_missing")
    rows = expected.get("rows") or []
    by_label = {str(row["标准标签"]): row for row in rows}
    overall_total = int(expected.get("analysis_content_count") or 0)
    # 已经进入四组表格的做法不能在这里重复出现。
    shown_labels = set(_effect_rows_from_report(markdown))
    for cells in _own_table_rows(undecided_body, "标签"):
        if len(cells) != OWN_UNDECIDED_COLUMNS:
            raise ContentPatternReportError("own_undecided_table_shape")
        label = cells[0]
        if label in shown_labels:
            raise ContentPatternReportError(f"own_undecided_row_duplicated:{label}")
        row = by_label.get(label)
        if row is None:
            raise ContentPatternReportError(f"own_undecided_row_unknown:{label}")
        if str(row["判断"]) not in VERDICT_UNDECIDED:
            raise ContentPatternReportError(f"own_undecided_verdict_invalid:{label}")
        if int(row["全部出现"]) < OWN_UNDECIDED_MIN_HITS:
            raise ContentPatternReportError(f"own_undecided_hits_too_low:{label}")
        if cells[3] != _count_with_percent(row["全部出现"], overall_total):
            raise ContentPatternReportError(f"own_undecided_count_mismatch:{label}")
        if cells[4] != str(row["判断"]):
            raise ContentPatternReportError(f"own_undecided_reason_mismatch:{label}")

    if not _section_body(markdown, OWN_COMPARE_SECTION).strip():
        raise ContentPatternReportError("own_compare_section_empty")


def _atomic_replace(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=str(path.parent))
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _resolve_inside(root: Path, raw_path: Path | str, label: str) -> Path:
    path = Path(raw_path).expanduser()
    resolved = path.resolve() if path.is_absolute() else (root / path).resolve()
    try:
        resolved.relative_to(root)
    except ValueError as exc:
        raise ContentPatternReportError(f"{label}_outside_workspace") from exc
    return resolved


ACCOUNT_LIBRARY_PATHS = {
    "对标内容": Path("对标资料/对标账号库.csv"),
    "我方内容": Path("我方资料/我方账号库.csv"),
}


def _rank_index(
    root: Path,
    source: str,
    platform: str,
    account_id: str,
) -> dict[str, object] | None:
    """取某个账号的「全部核心互动数 + 内容ID→互动数」。

    分档必须是**账号内相对排名**，所以整个账号有互动数的内容要一起上；
    本条自己的互动数按内容ID 回查（实例表里没有这一列）。
    """
    library_rel = ACCOUNT_LIBRARY_PATHS.get(source)
    if library_rel is None or not account_id:
        return None
    library_path = root / library_rel
    if not library_path.is_file():
        return None
    _, library_rows, _ = read_csv_table_with_snapshot(library_path)
    list_rel = ""
    for row in library_rows:
        if str(row.get("账号ID") or "").strip() != account_id:
            continue
        if platform and str(row.get("平台") or "").strip() != platform:
            continue
        list_rel = str(row.get("内容列表路径") or "").strip()
        break
    if not list_rel:
        return None
    list_path = root / list_rel
    if not list_path.is_file():
        return None
    _, content_rows, _ = read_csv_table_with_snapshot(list_path)
    values: list[int] = []
    by_content: dict[str, int] = {}
    for row in content_rows:
        text = str(row.get("核心互动数") or "").strip()
        if not text.isdigit():
            continue
        value = int(text)
        values.append(value)
        content_id = str(row.get("内容ID") or "").strip()
        if content_id:
            by_content[content_id] = value
    if not values:
        return None
    return {"values": values, "by_content": by_content}


def _rank_index_key(row: Mapping[str, str]) -> tuple[str, str, str]:
    return (
        str(row.get("样本来源") or "").strip(),
        str(row.get("平台") or "").strip(),
        str(row.get("账号ID") or "").strip(),
    )


def _matches_rank_scope(row: Mapping[str, str], scope: Mapping[str, object]) -> bool:
    """只统计本轮范围内的实例行（与汇总执行器的 scope 口径一致）。"""
    source = str(scope.get("source") or "").strip()
    if source and row.get("样本来源", "") != source:
        return False
    dimensions = {
        "platform": "平台",
        "account_id": "账号ID",
        "media_form": "媒介形式",
    }
    return all(
        not str(scope.get(key) or "").strip()
        or row.get(field, "") == str(scope.get(key) or "").strip()
        for key, field in dimensions.items()
    )


def _account_scale(root: Path, scope: Mapping[str, object] | None) -> dict[str, object]:
    """取本轮范围内那个账号的互动数口径：取到互动数的条数 + 中位数。

    全量分析（scope 为空）或查不到账号时返回空 dict —— 第 3 节的口径行就交给
    模型按全量口径说明，不硬塞一个账号的说法进去。
    """
    if not isinstance(scope, Mapping):
        return {}
    source = str(scope.get("source") or "").strip()
    platform = str(scope.get("platform") or "").strip()
    account_id = str(scope.get("account_id") or "").strip()
    library_rel = ACCOUNT_LIBRARY_PATHS.get(source)
    if library_rel is None or not account_id:
        return {}
    library_path = root / library_rel
    if not library_path.is_file():
        return {}
    _, library_rows, _ = read_csv_table_with_snapshot(library_path)
    list_rel = ""
    account_name = ""
    for row in library_rows:
        if str(row.get("账号ID") or "").strip() != account_id:
            continue
        if platform and str(row.get("平台") or "").strip() != platform:
            continue
        account_name = str(row.get("账号名") or "").strip()
        list_rel = str(row.get("内容列表路径") or "").strip()
        break
    if not list_rel:
        return {}
    list_path = root / list_rel
    if not list_path.is_file():
        return {}
    _, content_rows, _ = read_csv_table_with_snapshot(list_path)
    values: list[int] = []
    for row in content_rows:
        raw = str(row.get("核心互动数") or "").strip()
        if not raw:
            continue
        try:
            values.append(int(float(raw)))
        except ValueError:
            continue
    if not values:
        return {}
    values.sort()
    size = len(values)
    if size % 2:
        median: float = float(values[size // 2])
    else:
        median = (values[size // 2 - 1] + values[size // 2]) / 2
    return {
        "account_name": account_name,
        "platform": platform,
        "content_total": size,
        "median": median,
    }


def _percent(count: int, total: int) -> int:
    if total <= 0:
        return 0
    return int(count * 100 / total + 0.5)


def _count_with_percent(count: int | str, total: int) -> str:
    value = int(count)
    return f"{value}（{_percent(value, total)}%）"


def _render_effect_markdown(
    groups: Mapping[str, list[dict[str, object]]],
    *,
    overall_total: int,
    high_total: int,
    middle_total: int,
    low_total: int,
) -> str:
    """把账号共性与三档对比渲染成第 3 节的表格骨架。

    共性表只展示全部内容里的条数和占比；三档表的单元格统一写成「条数（比例）」。
    「数据好的内容更常用」和「数据差的内容更常用」为空时都保留表头，避免整组消失。
    """
    blocks: list[str] = []
    for key, title in EFFECT_GROUP_TITLES:
        rows = list(groups.get(key) or [])
        if not rows and key not in (EFFECT_GROUP_HIGH, EFFECT_GROUP_LOW):
            continue
        if key == EFFECT_GROUP_COMMON:
            lines = [
                f"### {title}",
                "",
                "以下做法覆盖本轮 80% 以上的内容，只描述账号习惯，不判断与数据好坏的关系。",
                "",
                f"| 标签 | 标签类型 | 定义 | 全部（{overall_total}条） |",
                "| --- | --- | --- | --- |",
            ]
            for row in rows:
                lines.append(
                    "| {label} | {ftype} | {definition} | {total} |".format(
                        label=row["标准标签"],
                        ftype=row["特征小类"],
                        definition=row["定义"],
                        total=_count_with_percent(row["全部出现"], overall_total),
                    )
                )
            blocks.append("\n".join(lines))
            continue
        header = (
            f"| 标签 | 标签类型 | 定义 | 高档（{high_total}条） | 中档（{middle_total}条） "
            f"| 低档（{low_total}条） | 结论 |"
        )
        lines = [
            f"### {title}",
            "",
            header,
            "| --- | --- | --- | --- | --- | --- | --- |",
        ]
        for row in rows:
            lines.append(
                "| {label} | {ftype} | {definition} | {high} | {middle} | {low} | {verdict} |".format(
                    label=row["标准标签"],
                    ftype=row["特征小类"],
                    definition=row["定义"],
                    high=_count_with_percent(row["高档出现"], high_total),
                    middle=_count_with_percent(row["中档出现"], middle_total),
                    low=_count_with_percent(row["低档出现"], low_total),
                    verdict=row["判断"],
                )
            )
        blocks.append("\n".join(lines))
    return "\n\n".join(blocks)


def build_rank_comparison(
    workspace_root: Path | str,
    *,
    min_tier_samples: int = RANK_MIN_TIER_SAMPLES,
    gap_min_percent: int = RANK_GAP_MIN_PERCENT,
    scope: Mapping[str, object] | None = None,
) -> dict[str, object]:
    """统计账号常用做法，并比较特征在高档、中档、低档内容里的使用率。

    档次按「样本来源 + 平台 + 账号ID」反查内容列表后现算。所有比例的分母都是
    去重内容数，不是实例行数。整体覆盖率达到 80% 的做法直接进入「账号通常使用」。

    其余做法在任一档少于 `min_tier_samples` 时标「样本不足」；否则同时满足
    「总出现至少 3 次、较多的一档至少 2 次、高低档使用率相差达到指定个百分点」
    才判更常用。内容类别单列，其他未达标做法只留在逐条数据里。
    """
    root = Path(workspace_root).expanduser().resolve()
    instance_path = root / INSTANCE_PATH
    if not instance_path.is_file():
        raise ContentPatternReportError(
            f"rank_comparison_instance_missing:{INSTANCE_PATH.as_posix()}"
        )
    _, instance_rows, _ = read_csv_table_with_snapshot(instance_path)
    try:
        catalog = load_feature_catalog(root)
    except ContentFeatureDictionaryError as exc:
        raise ContentPatternReportError(f"feature_catalog_invalid:{exc}") from exc

    contents_by_tier: dict[str, set[str]] = {tier: set() for tier in RANK_TIERS}
    hits: dict[str, dict[str, set[str]]] = {}
    overall_contents: set[str] = set()
    overall_hits: dict[str, set[str]] = {}
    # 档次现算；整体共性则使用全部有效特征实例，即使内容没有可用档次也能统计。
    rank_cache: dict[tuple[str, str, str], dict[str, object] | None] = {}
    for row in instance_rows:
        if row.get("当前有效") != "是" or row.get("实际采用状态") != "已采用":
            continue
        if scope is not None and not _matches_rank_scope(row, scope):
            continue
        sample_key = f"{row.get('样本来源', '')}::{row.get('样本键', '')}"
        feature_id = (row.get("特征ID") or "").strip()
        if not feature_id:
            continue
        overall_contents.add(sample_key)
        overall_hits.setdefault(feature_id, set()).add(sample_key)

        cache_key = _rank_index_key(row)
        if cache_key not in rank_cache:
            rank_cache[cache_key] = _rank_index(root, *cache_key)
        index = rank_cache[cache_key]
        if index is None:
            continue
        current = index["by_content"].get(str(row.get("内容ID") or "").strip())  # type: ignore[union-attr]
        if current is None:
            continue
        rank = compute_account_rank(index["values"], int(current))  # type: ignore[arg-type]
        if rank not in contents_by_tier:
            continue
        contents_by_tier[rank].add(sample_key)
        hits.setdefault(
            feature_id, {key: set() for key in RANK_HIT_KEYS.values()}
        )[RANK_HIT_KEYS[rank]].add(sample_key)

    overall_total = len(overall_contents)
    high_total = len(contents_by_tier[RANK_HIGH])
    middle_total = len(contents_by_tier[RANK_MIDDLE])
    low_total = len(contents_by_tier[RANK_LOW])
    insufficient = min(high_total, middle_total, low_total) < min_tier_samples
    rows: list[dict[str, object]] = []
    for feature_id in sorted(overall_hits):
        definition = catalog.get(feature_id)
        label = str(definition["label"]) if definition else ""
        feature_type = str(definition["feature_type"]) if definition else ""
        feature_definition = str(definition["definition"]) if definition else ""
        feature_hits = hits.get(feature_id)
        high_hits = len(feature_hits["high"]) if feature_hits else 0
        middle_hits = len(feature_hits["middle"]) if feature_hits else 0
        low_hits = len(feature_hits["low"]) if feature_hits else 0
        total_hits = len(overall_hits[feature_id])
        if overall_total and total_hits / overall_total >= RANK_COMMON_COVERAGE:
            verdict = VERDICT_COMMON
        elif insufficient:
            verdict = VERDICT_INSUFFICIENT
        elif total_hits >= RANK_MIN_TOTAL_HITS and max(high_hits, low_hits) >= RANK_MIN_LEADING_HITS:
            difference = (high_hits / high_total - low_hits / low_total) * 100
            if difference >= gap_min_percent:
                verdict = VERDICT_HIGH
            elif difference <= -gap_min_percent:
                verdict = VERDICT_LOW
            else:
                verdict = VERDICT_NONE
        else:
            verdict = VERDICT_NONE
        rows.append(
            {
                "特征ID": feature_id,
                "标准标签": label,
                "特征小类": feature_type,
                "定义": feature_definition,
                "全部出现": total_hits,
                "高档出现": high_hits,
                "中档出现": middle_hits,
                "低档出现": low_hits,
                "判断": verdict,
            }
        )
    groups: dict[str, list[dict[str, object]]] = {
        key: [] for key, _ in EFFECT_GROUP_TITLES
    }
    for row in rows:
        if row["判断"] == VERDICT_COMMON:
            groups[EFFECT_GROUP_COMMON].append(row)
        elif row["特征小类"] == CONTENT_TYPE_CATEGORY:
            groups[EFFECT_GROUP_CONTENT_TYPE].append(row)
        elif row["判断"] == VERDICT_HIGH:
            groups[EFFECT_GROUP_HIGH].append(row)
        elif row["判断"] == VERDICT_LOW:
            groups[EFFECT_GROUP_LOW].append(row)
        # 差不到阈值的做法不单列，只保留在 rows 里留档。
    for group_key, _ in EFFECT_GROUP_TITLES:
        if group_key == EFFECT_GROUP_COMMON:
            groups[group_key].sort(
                key=lambda item: (-int(item["全部出现"]), str(item["特征ID"]))
            )
        else:
            groups[group_key].sort(
                key=lambda item: (
                    -int(item["高档出现"]),
                    -int(item["中档出现"]),
                    str(item["特征ID"]),
                )
            )
    warnings: list[str] = []
    if insufficient:
        warnings.append(
            f"样本不足:高档 {high_total} 条／中档 {middle_total} 条／低档 {low_total} 条，"
            f"任一档少于 {min_tier_samples} 条不做高低档判断"
        )
    markdown = _render_effect_markdown(
        groups,
        overall_total=overall_total,
        high_total=high_total,
        middle_total=middle_total,
        low_total=low_total,
    )
    return {
        "ok": True,
        "status": "ready",
        "analysis_content_count": overall_total,
        "high_content_count": high_total,
        "middle_content_count": middle_total,
        "low_content_count": low_total,
        "min_tier_samples": min_tier_samples,
        "gap_min_percent": gap_min_percent,
        "min_total_hits": RANK_MIN_TOTAL_HITS,
        "min_leading_hits": RANK_MIN_LEADING_HITS,
        "common_coverage": RANK_COMMON_COVERAGE,
        "rank_rule": RANK_RULE_TEXT,
        "rows": rows,
        "groups": groups,
        "warnings": warnings,
        # 本轮账号的互动数口径（取到互动数的条数 + 中位数）；全量分析时为 None。
        "account": _account_scale(root, scope) or None,
        # 报告第 3 节的表格骨架 —— AI 照抄表格，解读自己写。
        "markdown": markdown,
    }


def _effect_rows_from_report(markdown: str) -> dict[str, list[str]]:
    """把报告第 3 节里的共性表和三档表解析成 标签 -> 单元格列表。"""
    body = _section_body(markdown, EFFECT_SECTION)
    parsed: dict[str, list[str]] = {}
    for raw in body.splitlines():
        line = raw.strip()
        if not line.startswith("|"):
            continue
        cells = [cell.strip() for cell in line.strip("|").split("|")]
        if len(cells) not in (COMMON_TABLE_COLUMNS, EFFECT_TABLE_COLUMNS):
            continue
        if not cells[0] or cells[0] == COMMON_TABLE_HEADERS[0]:
            continue
        if all(set(cell) <= {"-", ":"} for cell in cells):
            continue
        parsed[cells[0]] = cells
    return parsed


def _validate_effect_section(markdown: str, expected: Mapping[str, object]) -> None:
    """第 3 节的共性占比、三档条数与比例、判定词必须与机器结果一致。"""
    groups = expected.get("groups")
    common_labels: set[str] = set()
    if isinstance(groups, Mapping) and groups:
        common_rows = groups.get(EFFECT_GROUP_COMMON) or []
        common_labels = {str(row["标准标签"]) for row in common_rows}
        rows = [row for group_rows in groups.values() for row in (group_rows or [])]
    else:
        rows = expected.get("rows") or []
    overall_total = int(expected.get("analysis_content_count") or 0)
    tier_totals = {
        "高档出现": int(expected.get("high_content_count") or 0),
        "中档出现": int(expected.get("middle_content_count") or 0),
        "低档出现": int(expected.get("low_content_count") or 0),
    }
    parsed = _effect_rows_from_report(markdown)
    for row in rows:
        label = str(row["标准标签"])
        cells = parsed.get(label)
        if cells is None:
            raise ContentPatternReportError(f"report_effect_row_missing:{label}")
        if label in common_labels:
            if len(cells) != COMMON_TABLE_COLUMNS:
                raise ContentPatternReportError(f"report_effect_table_shape:{label}")
            expected_total = _count_with_percent(row["全部出现"], overall_total)
            if cells[3] != expected_total:
                raise ContentPatternReportError(
                    f"report_effect_count_mismatch:{label}:全部出现"
                )
            continue
        if len(cells) != EFFECT_TABLE_COLUMNS:
            raise ContentPatternReportError(f"report_effect_table_shape:{label}")
        for index, key in ((3, "高档出现"), (4, "中档出现"), (5, "低档出现")):
            expected_count = _count_with_percent(row[key], tier_totals[key])
            if cells[index] != expected_count:
                raise ContentPatternReportError(
                    f"report_effect_count_mismatch:{label}:{key}"
                )
        if str(row["判断"]) not in cells[6]:
            raise ContentPatternReportError(f"report_effect_verdict_mismatch:{label}")
    known = {str(row["标准标签"]) for row in rows}
    for label in parsed:
        if label not in known:
            raise ContentPatternReportError(f"report_effect_row_unknown:{label}")


def save_report(
    workspace_root: Path | str,
    candidate_file: Path | str,
    preparation_result_file: Path | str,
    *,
    report_path: Path | str,
    report_kind: str = REPORT_KIND_BENCHMARK,
) -> dict[str, object]:
    """校验并原子写入**账号级**内容规律分析。

    `preparation_result_file` 是 `账号内容规律分析准备执行器.py` 产出的冻结结果，
    里面的分档对比（`rank_comparison`）与第 1 节四项数字就是本轮的唯一真源 ——
    候选稿必须逐字对上它，不在保存阶段重算（重算会因实例表变动而误报）。

    `report_kind` 只影响结构校验：对标报告 3 节，我方报告 4 节且多出
    「暂时无法判断」和上期对比；两边的数字校验完全共用。
    """
    kind = str(report_kind or "").strip()
    if kind not in REPORT_KINDS:
        raise ContentPatternReportError(f"report_kind_invalid:{kind}")
    root = Path(workspace_root).expanduser().resolve()
    if not str(report_path or "").strip():
        raise ContentPatternReportError("report_path_required")
    candidate = _resolve_inside(root, candidate_file, "candidate")
    result_file = _resolve_inside(root, preparation_result_file, "preparation_result")
    for path, label in ((candidate, "candidate"), (result_file, "preparation_result")):
        if not path.is_file():
            raise ContentPatternReportError(f"{label}_missing")
    try:
        result = json.loads(result_file.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ContentPatternReportError(f"preparation_result_invalid:{exc}") from exc
    if not isinstance(result, Mapping) or result.get("status") != "ready":
        raise ContentPatternReportError("preparation_result_not_ready")
    rank_comparison = result.get("rank_comparison")
    if not isinstance(rank_comparison, Mapping):
        raise ContentPatternReportError("preparation_result_rank_comparison_missing")
    markdown = candidate.read_text(encoding="utf-8")
    if len(re.findall(rf"(?m)^#\s+{re.escape(REPORT_TITLE)}\s*$", markdown)) != 1:
        raise ContentPatternReportError("report_title_invalid")
    expected_headings = OWN_REQUIRED_HEADINGS if kind == REPORT_KIND_OWN else REQUIRED_HEADINGS
    headings = re.findall(r"(?m)^##\s+(.+?)\s*$", markdown)
    if tuple(headings) != expected_headings:
        raise ContentPatternReportError(f"report_headings_invalid:{headings}")
    if kind == REPORT_KIND_OWN:
        _section_field(markdown, SCOPE_SECTION, OWN_BASELINE_LABEL)
    if _section_field(markdown, SCOPE_SECTION, "生成时间") != str(result.get("generated_at") or ""):
        raise ContentPatternReportError("report_generated_at_mismatch")
    # 分析范围写账号名（自然语言），机器只校验非空；写对与否由流程约束。
    _section_field(markdown, SCOPE_SECTION, "分析范围")
    if _section_field(markdown, SCOPE_SECTION, "纳入内容数") != str(result.get("active_sample_count")):
        raise ContentPatternReportError("report_sample_count_mismatch")
    if _section_field(markdown, SCOPE_SECTION, "规律数量") != str(result.get("rule_count")):
        raise ContentPatternReportError("report_rule_count_mismatch")
    # 第 3 节表格由机器出：共性占比、三档条数与比例、判定词必须对上冻结结果。
    _validate_effect_section(markdown, rank_comparison)
    if kind == REPORT_KIND_OWN:
        _validate_own_sections(markdown, rank_comparison, int(result.get("rule_count") or 0))
    payload = markdown.encode("utf-8")
    target = _resolve_inside(root, report_path, "report_path")
    _atomic_replace(target, payload)
    if target.read_bytes() != payload:
        raise ContentPatternReportError("report_post_write_mismatch")
    return {
        "ok": True,
        "status": "committed",
        "report_path": target.relative_to(root).as_posix(),
        "sha256": hashlib.sha256(payload).hexdigest(),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="安全保存账号内容规律分析报告")
    parser.add_argument("--workspace-root", required=True, type=Path)
    parser.add_argument("--candidate-file", default=None, type=Path)
    parser.add_argument("--preparation-result-file", default=None, type=Path)
    parser.add_argument(
        "--report-path",
        default=None,
        type=Path,
        help="报告写到哪（必填）：对标传 <账号目录>/账号内容规律分析.md；我方传 <账号目录>/<日期>账号内容规律分析.md",
    )
    parser.add_argument(
        "--rank-comparison",
        action="store_true",
        help="只输出第 3 节的三组对比表格骨架，不保存报告（可配 --source/--platform/--account-id）",
    )
    parser.add_argument("--source", choices=("对标内容", "我方内容"), default="")
    parser.add_argument("--platform", default="")
    parser.add_argument("--account-id", default="")
    parser.add_argument(
        "--report-kind",
        choices=REPORT_KINDS,
        default=REPORT_KIND_BENCHMARK,
        help="benchmark＝对标 3 节；own＝我方 4 节",
    )
    args = parser.parse_args()
    try:
        if args.rank_comparison:
            scope = {
                "source": args.source,
                "platform": args.platform,
                "account_id": args.account_id,
            }
            print(
                json.dumps(
                    build_rank_comparison(
                        args.workspace_root,
                        scope=scope if any(scope.values()) else None,
                    ),
                    ensure_ascii=False,
                    indent=2,
                )
            )
            return 0
        if args.candidate_file is None or args.preparation_result_file is None:
            raise ContentPatternReportError(
                "candidate_file_and_preparation_result_file_required"
            )
        if args.report_path is None:
            raise ContentPatternReportError("report_path_required")
        result = save_report(
            args.workspace_root,
            args.candidate_file,
            args.preparation_result_file,
            report_path=args.report_path,
            report_kind=args.report_kind,
        )
    except (OSError, UnicodeError, ContentPatternReportError) as exc:
        print(json.dumps({"status": "failed", "error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
