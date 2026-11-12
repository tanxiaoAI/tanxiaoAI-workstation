#!/usr/bin/env python3
"""从内容特征实例里筛出「参考做法」候选。

只读两份表：
- `沉淀结论/内容特征实例.csv`：已通过校验、当前有效、实际采用的特征实例；
- `对标资料/对标内容库.csv`：补数据水平、核心互动数和对外链接。

筛选口径：
1. 只取对标内容，不取我方内容；
2. 只取 `当前有效 = 是` 且 `实际采用状态 = 已采用`；
3. 内容必须能在对标内容库定位，且有 `内容分析` 文件；
4. 只保留数据水平在 `小爆款` 以上的内容；某类不足时用 `正常` 补足；
5. 每个特征小类最多保留 `--max-per-category` 条，先按数据水平、再按核心互动数排序。

输出是给行动清单生成用的冻结候选，不是正式分析结论。
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any

if str(Path(__file__).resolve().parent) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parent))

from csv安全写入 import read_csv_table

LEVEL_RANK = {"大爆款": 5, "优秀": 4, "小爆款": 3, "正常": 2, "偏弱": 1, "差": 0}
GOOD_LEVELS = {"大爆款", "优秀", "小爆款"}
FALLBACK_LEVEL = "正常"
# 「无互动设计」这类否定型标签描述的是「没做什么」，不能当参考做法。
EXCLUDED_TAG_PREFIXES = ("无",)

# 动作方向 → 可用的特征小类。方向名取自《账号改进行动清单》的动作方向库。
DIRECTION_CATEGORIES: dict[str, list[str]] = {
    "定位与内容主线": ["内容类别"],
    "主页与账号介绍": ["标题", "内容类别"],
    "新增内容与选题": ["内容类别"],
    "开头与标题封面": ["开头", "标题"],
    "正文结构与表达": ["内容结构", "表达风格"],
    "案例与信任建设": ["内容结构", "内容类别"],
    "互动与咨询承接": ["互动设计"],
    "历史内容治理": ["内容类别", "标题"],
    "发布节奏与系列化": ["内容结构"],
    "验证复盘与做法放大": ["内容结构", "表达风格"],
}


class ReferenceCandidateError(RuntimeError):
    pass


def _read_table(path: Path, label: str) -> list[dict[str, str]]:
    if not path.is_file():
        raise ReferenceCandidateError(f"{label}_missing:{path}")
    try:
        _, rows = read_csv_table(path)
    except Exception as exc:  # noqa: BLE001 - 统一转成可读错误
        raise ReferenceCandidateError(f"{label}_unreadable:{exc}") from exc
    return [dict(row) for row in rows]


def _text(value: Any) -> str:
    return str(value or "").strip()


def _build_directions(category: str) -> list[str]:
    return [direction for direction, categories in DIRECTION_CATEGORIES.items() if category in categories]


def _build_candidates(
    root: Path,
    *,
    max_per_category: int,
    only_directions: set[str] | None,
) -> dict[str, Any]:
    instance_path = root / "沉淀结论/内容特征实例.csv"
    library_path = root / "对标资料/对标内容库.csv"
    instances = _read_table(instance_path, "feature_instances")
    library = _read_table(library_path, "benchmark_library")

    library_by_id = {_text(row.get("内容ID")): row for row in library if _text(row.get("内容ID"))}

    grouped: dict[str, list[dict[str, Any]]] = {}
    for row in instances:
        if _text(row.get("样本来源")) != "对标内容":
            continue
        if _text(row.get("当前有效")) != "是":
            continue
        if _text(row.get("实际采用状态")) != "已采用":
            continue
        category = _text(row.get("特征小类"))
        if not category:
            continue
        tag = _text(row.get("标准标签"))
        if not tag or tag.startswith(EXCLUDED_TAG_PREFIXES):
            continue
        directions = _build_directions(category)
        if only_directions is not None and not (set(directions) & only_directions):
            continue

        content_id = _text(row.get("内容ID"))
        library_row = library_by_id.get(content_id)
        if not library_row:
            continue
        analysis_path = _text(library_row.get("内容分析路径"))
        if not analysis_path or not (root / analysis_path).is_file():
            continue
        level = _text(library_row.get("数据水平"))
        if level not in LEVEL_RANK:
            continue
        if level == "差" or level == "偏弱":
            continue

        try:
            interactions = int(float(_text(library_row.get("核心互动数")) or 0))
        except ValueError:
            interactions = 0

        grouped.setdefault(category, []).append({
            "特征实例ID": _text(row.get("特征实例ID")),
            "内容ID": content_id,
            "标题": _text(library_row.get("标题")),
            "平台": _text(library_row.get("平台")),
            "账号名": _text(library_row.get("账号名")),
            "对外页面链接": _text(library_row.get("对外页面链接")),
            "数据水平": level,
            "核心互动数": interactions,
            "特征ID": _text(row.get("特征ID")),
            "特征小类": category,
            "标准标签": tag,
            "适用动作方向": directions,
            "实例证据": _text(row.get("实例证据")),
            "证据位置": _text(row.get("证据位置")),
            "内容分析路径": analysis_path,
        })

    candidates: list[dict[str, Any]] = []
    group_summary: dict[str, dict[str, int]] = {}
    for category in sorted(grouped):
        rows = sorted(
            grouped[category],
            key=lambda item: (LEVEL_RANK.get(item["数据水平"], 0), item["核心互动数"]),
            reverse=True,
        )
        picked: list[dict[str, Any]] = []
        seen_content: set[str] = set()
        for row in rows:
            if row["数据水平"] not in GOOD_LEVELS:
                continue
            if row["内容ID"] in seen_content:
                continue
            seen_content.add(row["内容ID"])
            picked.append(row)
        if len(picked) < max_per_category:
            for row in rows:
                if len(picked) >= max_per_category:
                    break
                if row["数据水平"] != FALLBACK_LEVEL:
                    continue
                if row["内容ID"] in seen_content:
                    continue
                seen_content.add(row["内容ID"])
                picked.append(row)
        picked = picked[:max_per_category]
        candidates.extend(picked)
        group_summary[category] = {"候选数": len(picked)}

    return {
        "schema_version": "1",
        "status": "ok",
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "source": instance_path.relative_to(root).as_posix(),
        "library": library_path.relative_to(root).as_posix(),
        "summary": {
            "候选数": len(candidates),
            "内容数": len({item["内容ID"] for item in candidates}),
            "分组": group_summary,
        },
        "候选": candidates,
    }


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    body = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
    handle, temp_name = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
    try:
        with os.fdopen(handle, "wb") as stream:
            stream.write(body)
        os.replace(temp_name, path)
    except Exception:
        Path(temp_name).unlink(missing_ok=True)
        raise


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="检索行动清单可用的参考做法候选")
    parser.add_argument("--workspace-root", required=True)
    parser.add_argument("--output-json", required=True)
    parser.add_argument("--max-per-category", type=int, default=4)
    parser.add_argument("--directions", default="", help="只输出这些动作方向，逗号分隔；留空输出全部")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    root = Path(args.workspace_root).expanduser().resolve()
    if not root.is_dir():
        print(json.dumps({"status": "error", "error": "workspace_root_missing"}, ensure_ascii=False))
        return 1
    if args.max_per_category < 1:
        print(json.dumps({"status": "error", "error": "max_per_category_invalid"}, ensure_ascii=False))
        return 1
    only_directions = {
        item.strip() for item in str(args.directions or "").split(",") if item.strip()
    } or None
    try:
        payload = _build_candidates(
            root,
            max_per_category=args.max_per_category,
            only_directions=only_directions,
        )
        output = Path(args.output_json)
        output = output if output.is_absolute() else (root / output)
        _write_json(output, payload)
        payload["output_path"] = str(output)
        print(json.dumps(payload, ensure_ascii=False))
        return 0
    except ReferenceCandidateError as exc:
        print(json.dumps({"status": "error", "error": str(exc)}, ensure_ascii=False))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
