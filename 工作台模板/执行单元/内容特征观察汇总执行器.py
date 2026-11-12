#!/usr/bin/env python3
"""只读汇总「内容特征观察表」里的新特征位，作为新特征候选的输入。

自 2026-09-27 起不再扫 `内容画像与特征卡.md`（卡已退役）：对标内容分析、
发布后复盘、账号详细数据获取三条链路落表时，都会把候选第 3 节「待补充标签」
直接写进 `沉淀结论/内容特征观察表.csv`。本执行器读**那一张表**，只取
`当前有效=是` 的行，把散在各篇里的新特征位收集、归一、聚类，供内容规律分析
判断是否形成新特征（同一观察要在 ≥3 篇不同内容出现才自动入词典）。

表里的「标准标签」是模型自己拟的名字（词典里还没有），「观察说明」是这一篇的
具体观察 —— 与本执行器无关的、属于簇级候选的通用定义，到入词典那一步才写。

本执行器不写任何正式资产，也不修改观察表、词典或实例。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from datetime import datetime
from difflib import SequenceMatcher
from pathlib import Path
from typing import Sequence

from csv安全写入 import CsvTransactionError, read_csv_table_with_snapshot


class ContentObservationCollectError(RuntimeError):
    """Raised when the observation table cannot be read safely."""


OBSERVATION_PATH = Path("沉淀结论/内容特征观察表.csv")
OBSERVATION_REQUIRED_FIELDS = {
    "观察ID", "样本来源", "样本键", "内容ID", "特征小类", "标准标签",
    "观察说明", "证据部位", "当前有效",
}
# 「标准标签」栏被填成空标记时（模型有时会写「（无）」），改用「观察说明」栏当聚类
# 文本，否则所有行都会被归一成同一个「（无）」，聚类直接失效。
BLANK_MARKERS = {"", "（无）", "(无)", "无", "-", "—", "–", "/", "不适用", "未知"}
NORMALIZE_DROP = re.compile(r"[\s“”‘’\"'（）()\[\]【】]+")
NORMALIZE_TAIL = re.compile(r"[。．.、,，;；:：]+$")
LIKE_THRESHOLD = 0.86
CLUSTER_ID_LENGTH = 12


def _normalize(text: str) -> str:
    return NORMALIZE_TAIL.sub("", NORMALIZE_DROP.sub("", text)).lower()


def _cluster(entries: Sequence[dict[str, str]]) -> list[dict[str, object]]:
    exact: dict[str, dict[str, object]] = {}
    for entry in entries:
        key = _normalize(entry["text"])
        if not key:
            continue
        bucket = exact.get(key)
        if bucket is None:
            bucket = {"key": key, "texts": [], "sources": []}
            exact[key] = bucket
        bucket["texts"].append(entry["text"])  # type: ignore[union-attr]
        bucket["sources"].append(entry)  # type: ignore[union-attr]

    merged: list[dict[str, object]] = []
    for key in sorted(exact):
        bucket = exact[key]
        target = None
        for existing in merged:
            if SequenceMatcher(None, str(existing["key"]), key).ratio() >= LIKE_THRESHOLD:
                target = existing
                break
        if target is None:
            merged.append(
                {
                    "key": key,
                    "texts": list(bucket["texts"]),  # type: ignore[arg-type]
                    "sources": list(bucket["sources"]),  # type: ignore[arg-type]
                }
            )
            continue
        target["texts"].extend(bucket["texts"])  # type: ignore[union-attr]
        target["sources"].extend(bucket["sources"])  # type: ignore[union-attr]

    clusters: list[dict[str, object]] = []
    for item in merged:
        seen: set[tuple[str, str, str]] = set()
        sources: list[dict[str, str]] = []
        for entry in item["sources"]:  # type: ignore[union-attr]
            # 去重键带「来源」：对标内容和我方内容各自编号，只看内容ID 会把
            # 两侧重号的两篇算成一篇，而「≥3 篇才自动入词典」正是靠这个数。
            marker = (entry["source"], str(entry.get("content_key") or ""), entry["observation_id"])
            if marker in seen:
                continue
            seen.add(marker)
            sources.append(
                {
                    "source": entry["source"],
                    "content_id": entry["content_id"],
                    "observation_id": entry["observation_id"],
                    "feature_category": entry.get("feature_category", ""),
                }
            )
        texts = list(item["texts"])  # type: ignore[arg-type]
        clusters.append(
            {
                "cluster_id": "观察"
                + hashlib.sha256(str(item["key"]).encode("utf-8")).hexdigest()[:CLUSTER_ID_LENGTH],
                "representative": min(texts, key=lambda value: (len(value), value)),
                "occurrences": len(texts),
                # 同一篇内容里同一个标签只算一次；跨来源的同名内容ID 也算两篇。
                "source_count": len(
                    {(source["source"], source["content_id"]) for source in sources}
                ),
                "sources": sources,
                "variants": sorted(set(texts)),
            }
        )
    clusters.sort(key=lambda item: (-int(item["occurrences"]), str(item["representative"])))
    return clusters


def collect_uncovered_observations(workspace_root: Path | str) -> dict[str, object]:
    root = Path(workspace_root).expanduser().resolve()
    if not root.is_dir():
        raise ContentObservationCollectError(f"workspace_root_invalid:{root}")

    path = root / OBSERVATION_PATH
    if not path.is_file():
        raise ContentObservationCollectError(
            f"observation_table_missing:{OBSERVATION_PATH.as_posix()}"
        )
    try:
        fields, rows, _ = read_csv_table_with_snapshot(path)
    except CsvTransactionError as exc:
        raise ContentObservationCollectError(str(exc)) from exc
    missing = sorted(OBSERVATION_REQUIRED_FIELDS - set(fields))
    if missing:
        raise ContentObservationCollectError(f"observation_table_missing_fields:{missing}")

    entries: list[dict[str, str]] = []
    source_counts: dict[str, int] = {}
    seen: set[tuple[str, str, str]] = set()
    active = 0
    for row in rows:
        if str(row.get("当前有效") or "").strip() != "是":
            continue
        active += 1
        source = str(row.get("样本来源") or "").strip()
        content_id = str(row.get("内容ID") or "").strip()
        content_key = str(row.get("样本键") or "").strip() or content_id
        label = str(row.get("标准标签") or "").strip()
        note = str(row.get("观察说明") or "").strip()
        text = label if label not in BLANK_MARKERS else note
        if not text:
            continue
        marker = (source, content_key, label)
        if marker in seen:
            continue
        seen.add(marker)
        entries.append(
            {
                "text": text,
                "source": source,
                "content_id": content_id,
                "content_key": content_key,
                "observation_id": str(row.get("观察ID") or "").strip(),
                "feature_category": str(row.get("特征小类") or "").strip(),
            }
        )
        source_counts[source] = source_counts.get(source, 0) + 1

    clusters = _cluster(entries)
    return {
        "status": "collected",
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "table_path": OBSERVATION_PATH.as_posix(),
        "table_fingerprint": hashlib.sha256(path.read_bytes()).hexdigest(),
        "scanned_rows": len(rows),
        "active_rows": active,
        # 本轮实际检查到的内容条数（有观察记录的；同一内容多行只算一次）。
        # 观察表只登记「有缺口」的内容，所以它 ≤ 工作区里已分析的内容总数。
        "active_content_count": len({(e["source"], e["content_key"]) for e in entries}),
        "source_counts": source_counts,
        "observation_count": len(entries),
        "cluster_count": len(clusters),
        "clusters": clusters,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="汇总内容特征观察表里词典尚未覆盖的观察")
    parser.add_argument("--workspace-root", required=True, type=Path, help="当前工作区根目录")
    parser.add_argument("--output-json", default=None, type=Path, help="可选：同时写入该 JSON 文件")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        result = collect_uncovered_observations(args.workspace_root)
        if args.output_json is not None:
            root = Path(args.workspace_root).expanduser().resolve()
            target = Path(args.output_json).expanduser()
            target = target.resolve() if target.is_absolute() else (root / target).resolve()
            try:
                target.relative_to(root)
            except ValueError as exc:
                raise ContentObservationCollectError("output_json_outside_workspace") from exc
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(
                json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
            )
    except (ContentObservationCollectError, OSError, UnicodeError, ValueError) as exc:
        print(
            json.dumps({"ok": False, "status": "failed", "error": str(exc)}, ensure_ascii=False),
            file=sys.stderr,
        )
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
