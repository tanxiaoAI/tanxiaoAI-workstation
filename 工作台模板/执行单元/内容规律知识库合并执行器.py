#!/usr/bin/env python3
"""内容规律知识库 · 合并执行器

把「内容规律分析」从一次性报告改成**持续积累的知识库**（2026-09-27 定）：
按账号切片现算每个账号的标签效果，再把所有账号的结论**按做法归并**、按证据强度分层，
产出人读的 `沉淀结论/内容规律知识库.md`。

四层（一个做法只进一层，取最高）：

| 层 | 判据 |
| --- | --- |
| L1 我方效果验证过的 | 我方账号里判出结论 |
| L2 多账号对标验证过的 | ≥2 个对标账号里判出结论 |
| L3 单账号里成立的 | 恰好 1 个对标账号里判出结论 |
| L4 出现过、但判不出效果的 | 没判出结论，只有出现记录 |

**「判出结论」**＝在该账号的三档对比里判成「数据好的内容更常用」或「数据差的内容更常用」
（三档样本都不少于 2 条、且高档与低档差到 2 条以上）。判定词**逐字写进证据栏**，不另开
一列、也不自造符号。

L4 收两类：① 在已建档账号里出现过、但三档判不出方向的（列出三档分布）；② 只在没建档的
内容里出现过的（结构上永远判不出）。两类合起来还有一条门槛：整个工作区去重后出现不足
`LEAD_MIN_OCCURRENCE` 篇的做法不进 L4。文档按表格呈现，一个做法一行。

**本执行器只产出人读的知识库 md**——既不再写规律库 CSV（2026-09-28 退役），
也不产账号级 CSV；账号级结论在这里按账号切片现算。它不处理新特征入库，也不读取
任何入库回执：词典的维护由「内容特征入库流程」独立负责，两条流程互不调用。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import tempfile
from collections import OrderedDict
from pathlib import Path
from typing import Mapping

from csv安全写入 import read_csv_table_with_snapshot
from 内容特征词典 import ContentFeatureDictionaryError, load_feature_catalog
from 内容特征汇总执行器 import _now_text
from 内容规律报告写入执行器 import (
    INSTANCE_PATH,
    VERDICT_HIGH,
    VERDICT_LOW,
    ContentPatternReportError,
    build_rank_comparison,
)


class ContentKnowledgeBaseError(RuntimeError):
    """Raised when the content-pattern knowledge base cannot be built."""


OUTPUT_PATH = Path("沉淀结论/内容规律知识库.md")
ACCOUNT_LIBRARY_PATHS = (
    ("对标内容", Path("对标资料/对标账号库.csv")),
    ("我方内容", Path("我方资料/我方账号库.csv")),
)
MAX_CASES = 3
UNBUILT_LABEL = "没建档的内容"
# 第 5 节（出现过、但判不出效果的）的最低出现篇数：整个工作区去重后不足这个数的做法
# 不进库 —— 出现 1～2 篇既不是这个账号的习惯、也判不出效果（2026-09-27 定）
LEAD_MIN_OCCURRENCE = 3
LAYER_SELF = "L1"
LAYER_MULTI = "L2"
LAYER_SINGLE = "L3"
LAYER_LEAD = "L4"
LAYER_TITLES = (
    (LAYER_SELF, "我方效果验证过的"),
    (LAYER_MULTI, "多账号对标验证过的"),
    (LAYER_SINGLE, "单账号里成立的"),
    (LAYER_LEAD, "出现过、但判不出效果的"),
)
LAYER_NOTES = {
    LAYER_SELF: "在我方账号自己的数据里验证成立——能看到后台数据，最硬。",
    LAYER_MULTI: "两个以上对标账号都成立，靠数量抵消单账号的偶然。",
    LAYER_SINGLE: "只有一个对标账号给出结论，能当线索，不能当规律。",
    LAYER_LEAD: (
        "这些做法在内容里用过，但判不出效果：账号的内容按互动数分成高、中、低三档后，"
        "「用它」和「不用它」跟数据好坏看不出稳定关系（高档和低档差不到 2 条，或者某一档的"
        "内容太少）。三档分布＝这个做法在那一档的内容里出现几篇，只算该账号里能分档的内容。"
        f"只列全工作区出现 {LEAD_MIN_OCCURRENCE} 篇以上的做法，出现 1～2 篇的不进库。"
    ),
}


def _resolve_inside(root: Path, value: str | Path, label: str) -> Path:
    raw_text = str(value or "").strip()
    if not raw_text:
        raise ContentKnowledgeBaseError(f"{label}_missing")
    raw = Path(raw_text).expanduser()
    target = (raw if raw.is_absolute() else root / raw).resolve()
    try:
        target.relative_to(root)
    except ValueError as exc:
        raise ContentKnowledgeBaseError(f"{label}_outside_workspace") from exc
    return target


def _atomic_write(path: Path, text: str) -> str:
    payload = text.encode("utf-8")
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
    if path.read_bytes() != payload:
        raise ContentKnowledgeBaseError("knowledge_base_post_write_mismatch")
    return hashlib.sha256(payload).hexdigest()


def _account_inventory(root: Path) -> list[dict[str, str]]:
    inventory: list[dict[str, str]] = []
    for source, rel in ACCOUNT_LIBRARY_PATHS:
        path = root / rel
        if not path.is_file():
            raise ContentKnowledgeBaseError(f"account_library_missing:{rel.as_posix()}")
        _, rows, _ = read_csv_table_with_snapshot(path)
        for row in rows:
            account_id = str(row.get("账号ID") or "").strip()
            if not account_id:
                continue
            inventory.append(
                {
                    "source": source,
                    "账号ID": account_id,
                    "平台": str(row.get("平台") or "").strip(),
                    "账号名": str(row.get("账号名") or "").strip(),
                }
            )
    return inventory


def _accepted_instance_rows(root: Path) -> list[dict[str, str]]:
    path = root / INSTANCE_PATH
    if not path.is_file():
        raise ContentKnowledgeBaseError(f"instance_missing:{INSTANCE_PATH.as_posix()}")
    _, rows, _ = read_csv_table_with_snapshot(path)
    return [
        row
        for row in rows
        if str(row.get("当前有效") or "").strip() == "是"
        and str(row.get("实际采用状态") or "").strip() == "已采用"
    ]


def _account_label(account: Mapping[str, str]) -> str:
    platform = str(account.get("平台") or "").strip()
    name = str(account.get("账号名") or "").strip() or str(account.get("账号ID") or "")
    return f"{platform}-{name}" if platform else name


def _layer_of(established: list[dict[str, object]]) -> str:
    self_hits = [info for info in established if info["source"] == "我方内容"]
    bench_hits = [info for info in established if info["source"] == "对标内容"]
    if self_hits:
        return LAYER_SELF
    if len(bench_hits) >= 2:
        return LAYER_MULTI
    if len(bench_hits) == 1:
        return LAYER_SINGLE
    return LAYER_LEAD


def _cell(value: object) -> str:
    """表格单元格：管道符和换行会破坏 md 表格，统一换掉。"""
    return str(value or "").replace("|", "／").replace("\n", " ").strip()


def _entry_row(entry: Mapping[str, object], evidence: str) -> str:
    return (
        "| "
        + " | ".join(
            [
                _cell(entry["标准名称"]),
                _cell(entry["特征小类"]),
                _cell(entry["定义"]),
                _cell(evidence),
                f"{entry['出现内容数']} 篇",
                _cell("".join(f"《{name}》" for name in entry["案例"])),  # type: ignore[union-attr]
            ]
        )
        + " |"
    )


def _render_markdown(
    *,
    generated_at: str,
    account_stats: list[dict[str, object]],
    by_layer: Mapping[str, list[dict[str, object]]],
    catalog_size: int,
    content_total: int,
) -> str:
    contributing = [item for item in account_stats if int(item["content_count"]) > 0]
    lines: list[str] = [
        "# 规律分析",
        "",
        "## 1. 本轮说明",
        "",
        "| 项 | 值 |",
        "| --- | --- |",
        f"| 生成时间 | {generated_at} |",
        f"| 覆盖账号数 | {len(contributing)} |",
        f"| 纳入内容数 | {content_total} |",
        f"| 做法总数 | {catalog_size} |",
        "",
        "**样本构成**（每个账号贡献多少条能统计的内容）",
        "",
        "| 账号 | 可统计内容数 |",
        "| --- | --- |",
    ]
    for item in account_stats:
        count = int(item["content_count"])
        suffix = "" if count else "（本次没有可统计的内容）"
        lines.append(f"| {_cell(item['label'])} | {count}{suffix} |")
    lines += [
        "",
        "**表格怎么读**",
        "",
        "- 证据栏里写的是：这个做法在哪个账号里判出了结论，括号里是「高档内容的出现条数／"
        "低档内容的出现条数」，再加上判定——「数据好的内容更常用」或「数据差的内容更常用」；",
        "- 出现：用过这个做法的内容篇数，整个工作区去重。它可以比上面「纳入内容数」大——"
        "纳入内容数只算能分档的内容（账号在库、又取到了互动数），而「出现」还包括账号没建档、"
        "或者没取到互动数、分不了档的那些；",
        "- 案例最多给 3 个标题。",
        "",
        "**边界**：数据好坏的依据是平台公开的点赞、收藏、评论这类互动数，**不等于赚钱多少**；"
        "不同定位、不同赛道的账号混在一起看时，结论只能当线索。",
        "",
    ]
    for index, (key, title) in enumerate(LAYER_TITLES, start=2):
        rows = list(by_layer.get(key) or [])
        lines += [f"## {index}. {title}（{len(rows)} 条）", ""]
        if not rows:
            lines += [_empty_layer_note(key), ""]
            continue
        lines += [f"> {LAYER_NOTES[key]}", ""]
        if key == LAYER_LEAD:
            lines += [
                "| 做法 | 类型 | 定义 | 账号 · 三档分布 | 出现 | 案例 |",
                "| --- | --- | --- | --- | --- | --- |",
            ]
            for entry in rows:
                parts = [
                    f"{item['label']}：高档 {item['high']}／中档 {item['middle']}／低档 {item['low']}"
                    for item in entry["三档分布"]  # type: ignore[union-attr]
                ]
                unbuilt = int(entry["没建档内容数"])
                if unbuilt:
                    parts.append(f"{unbuilt} 篇来自没建档的内容（分不了档）")
                lines.append(_entry_row(entry, "；".join(parts)))
        else:
            lines += [
                "| 做法 | 类型 | 定义 | 证据 | 出现 | 案例 |",
                "| --- | --- | --- | --- | --- | --- |",
            ]
            for entry in rows:
                evidence = "；".join(
                    f"{info['label']}：高档 {info['high']}／低档 {info['low']}，{info['verdict']}"
                    for info in entry["成立账号"]  # type: ignore[union-attr]
                )
                lines.append(_entry_row(entry, evidence))
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def _empty_layer_note(key: str) -> str:
    if key == LAYER_SELF:
        return "暂时没有：我方内容还没攒够能分档的样本。"
    if key == LAYER_MULTI:
        return "暂时没有：还没有第二个对标账号能给出结论。"
    if key == LAYER_SINGLE:
        return "暂时没有：没有对标账号判出结论。"
    return "暂时没有：没有出现过、又判不出效果的做法。"


def build_knowledge_base(
    workspace_root: str | Path,
    *,
    now: str | None = None,
) -> dict[str, object]:
    root = Path(workspace_root).expanduser().resolve()
    if not root.is_dir():
        raise ContentKnowledgeBaseError(f"workspace_root_invalid:{root}")
    generated_at = _now_text(now)
    inventory = _account_inventory(root)
    try:
        catalog = load_feature_catalog(root)
    except ContentFeatureDictionaryError as exc:
        raise ContentKnowledgeBaseError(f"feature_catalog_invalid:{exc}") from exc
    instance_rows = _accepted_instance_rows(root)

    features: "OrderedDict[str, dict[str, object]]" = OrderedDict()
    account_stats: list[dict[str, object]] = []

    def _slot(feature_id: str) -> dict[str, object]:
        return features.setdefault(
            feature_id,
            {
                "成立账号": {},              # account_key -> 证据信息（判出结论的）
                "三档分布": OrderedDict(),    # account_key -> 该账号里的高/中/低出现条数
                "出现账号": OrderedDict(),    # 来源标签 -> 内容键集合
                "出现内容键": set(),
                "案例": [],
            },
        )

    # 一、按账号切片现算：只有拿到「数据好的／数据差的更常用」的账号才算成立。
    for account in inventory:
        scope = {
            "source": account["source"],
            "platform": account["平台"],
            "account_id": account["账号ID"],
        }
        try:
            rank = build_rank_comparison(root, scope=scope)
        except ContentPatternReportError as exc:
            raise ContentKnowledgeBaseError(f"rank_comparison_failed:{exc}") from exc
        content_count = (
            int(rank["high_content_count"])
            + int(rank["middle_content_count"])
            + int(rank["low_content_count"])
        )
        account_stats.append(
            {
                "label": _account_label(account),
                "source": account["source"],
                "content_count": content_count,
            }
        )
        account_key = f"{account['source']}::{account['账号ID']}"
        account_label = _account_label(account)
        for row in rank["rows"]:
            verdict = str(row["判断"])
            slot = _slot(str(row["特征ID"]))
            # 三档分布：不管判没判出方向都记下来 —— 第 5 节要展示「高档 X／中档 Y／低档 Z」
            slot["三档分布"][account_key] = {  # type: ignore[index]
                "label": account_label,
                "source": account["source"],
                "high": int(row["高档出现"]),
                "middle": int(row["中档出现"]),
                "low": int(row["低档出现"]),
            }
            if verdict not in (VERDICT_HIGH, VERDICT_LOW):
                continue
            slot["成立账号"][account_key] = {  # type: ignore[index]
                "label": account_label,
                "source": account["source"],
                "verdict": verdict,
                "high": int(row["高档出现"]),
                "low": int(row["低档出现"]),
            }

    # 二、出现记录与案例：用全部已采用实例行，覆盖「账号不在库里」的那些内容。
    for row in instance_rows:
        feature_id = str(row.get("特征ID") or "").strip()
        if not feature_id:
            continue
        slot = _slot(feature_id)
        source = str(row.get("样本来源") or "").strip()
        account_id = str(row.get("账号ID") or "").strip()
        label = ""
        for account in inventory:
            if account["source"] == source and account["账号ID"] == account_id:
                label = _account_label(account)
                break
        label = label or UNBUILT_LABEL
        content_key = f"{source}::{row.get('样本键', '')}"
        slot["出现账号"].setdefault(label, set()).add(content_key)  # type: ignore[union-attr]
        slot["出现内容键"].add(content_key)  # type: ignore[union-attr]
        title = str(row.get("标题") or "").strip()
        cases = slot["案例"]
        if title and title not in cases and len(cases) < MAX_CASES:  # type: ignore[arg-type]
            cases.append(title)  # type: ignore[union-attr]

    entries: list[dict[str, object]] = []
    for feature_id, slot in features.items():
        definition = catalog.get(feature_id)
        established = list(slot["成立账号"].values())  # type: ignore[union-attr]
        established.sort(key=lambda info: (info["source"] != "我方内容", str(info["label"])))
        tiers = list(slot["三档分布"].values())  # type: ignore[union-attr]
        tiers.sort(key=lambda info: (info["source"] != "我方内容", str(info["label"])))
        layer = _layer_of(established)
        unbuilt_count = len(slot["出现账号"].get(UNBUILT_LABEL, ()))  # type: ignore[union-attr]
        occurrence = len(slot["出现内容键"])  # type: ignore[arg-type]
        # 第 5 节的门槛：出现过、但判不出效果的做法，出现篇数太少就不进库（2026-09-27 定）
        if layer == LAYER_LEAD and occurrence < LEAD_MIN_OCCURRENCE:
            continue
        entries.append(
            {
                "特征ID": feature_id,
                "标准名称": str(definition["label"]) if definition else feature_id,
                "特征小类": str(definition["feature_type"]) if definition else "",
                "定义": str(definition["definition"]) if definition else "",
                "分层": layer,
                "成立账号数": len(established),
                "涉及账号数": len(slot["出现账号"]),  # type: ignore[arg-type]
                "出现内容数": len(slot["出现内容键"]),  # type: ignore[arg-type]
                "成立账号": established,
                "三档分布": tiers,
                "没建档内容数": unbuilt_count,
                "出现账号": [
                    {"label": label, "count": len(keys)}
                    for label, keys in sorted(
                        slot["出现账号"].items(), key=lambda item: (-len(item[1]), item[0])  # type: ignore[union-attr]
                    )
                ],
                "案例": list(slot["案例"]),  # type: ignore[arg-type]
            }
        )

    by_layer: dict[str, list[dict[str, object]]] = {key: [] for key, _ in LAYER_TITLES}
    for entry in entries:
        by_layer[str(entry["分层"])].append(entry)
    for rows in by_layer.values():
        rows.sort(
            key=lambda item: (
                -int(item["成立账号数"]),
                -int(item["出现内容数"]),
                str(item["特征ID"]),
            )
        )

    content_total = sum(int(item["content_count"]) for item in account_stats)
    markdown = _render_markdown(
        generated_at=generated_at,
        account_stats=account_stats,
        by_layer=by_layer,
        catalog_size=len(catalog),
        content_total=content_total,
    )
    return {
        "ok": True,
        "status": "ready",
        "generated_at": generated_at,
        "account_count": sum(1 for item in account_stats if int(item["content_count"]) > 0),
        "account_total": len(account_stats),
        "account_stats": account_stats,
        "content_total": content_total,
        "feature_total": len(entries),
        "layer_counts": {key: len(by_layer[key]) for key, _ in LAYER_TITLES},
        "entries": entries,
        "markdown": markdown,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="合并生成内容规律知识库")
    parser.add_argument("--workspace-root", type=Path, required=True)
    parser.add_argument("--output-file", type=Path, default=OUTPUT_PATH)
    parser.add_argument("--now", default=None, help="固定生成时间（测试用）")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    root = Path(args.workspace_root).expanduser().resolve()
    try:
        result = build_knowledge_base(
            root,
            now=args.now,
        )
        output = _resolve_inside(root, args.output_file, "output_file")
        sha256 = _atomic_write(output, str(result["markdown"]))
    except (OSError, UnicodeError, ContentKnowledgeBaseError) as exc:
        print(json.dumps({"ok": False, "status": "failed", "error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 1
    print(json.dumps(
        {
            "ok": True,
            "status": "committed",
            "output_path": output.relative_to(root).as_posix(),
            "sha256": sha256,
            "generated_at": result["generated_at"],
            "account_count": result["account_count"],
            "account_total": result["account_total"],
            "content_total": result["content_total"],
            "feature_total": result["feature_total"],
            "layer_counts": result["layer_counts"],
        },
        ensure_ascii=False,
    ))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
