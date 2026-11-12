#!/usr/bin/env python3
"""账号内容规律分析 · 准备执行器

挂在两条账号详细分析流程（对标 / 我方）的 `2.5 校验并保存正式结果` 之后，
负责把模型写正文之前该有的机器数据一次算好，冻结成一份 JSON：

1. **门槛判定**：数该账号「有特征实例」的去重内容条数；0 条 → `skipped_no_instances`，
   流程跳过本步（跳过不是失败，反馈里如实写一句即可）。
2. **第 3 节共性与分档对比**：复用 `内容规律报告写入执行器.build_rank_comparison`，
   拿共性表、三档表格骨架与各档计数。
3. **第 1 节四项**：`生成时间` / `分析范围` / `纳入内容数` / `规律数量`。

本执行器**不写任何共享资产**——账号轮只产出账号目录下那一份
`账号内容规律分析.md`。
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from pathlib import Path
from typing import Mapping

from csv安全写入 import read_csv_table_with_snapshot
from 内容特征汇总执行器 import ContentFeatureAggregationError, _now_text, aggregate_workspace
from 内容规律报告写入执行器 import ContentPatternReportError, build_rank_comparison


class AccountPatternPrepError(RuntimeError):
    """Raised when the account-level content-pattern input cannot be prepared."""


ACCOUNT_LIBRARY_PATHS = {
    "对标内容": Path("对标资料/对标账号库.csv"),
    "我方内容": Path("我方资料/我方账号库.csv"),
}
INSTANCE_PATH = Path("沉淀结论/内容特征实例.csv")
# 门槛：该账号只要有 1 条特征实例就跑（2026-09-27 定，与两条详细分析链路同口径）。
# 第 4 节的「样本不足」是机器已有的降级路径，不需要在门槛这里再挡一次。
MIN_INSTANCE_CONTENTS = 1


def _resolve_inside(root: Path, value: str | Path, label: str) -> Path:
    raw = Path(str(value or "").strip()).expanduser()
    if not str(value or "").strip():
        raise AccountPatternPrepError(f"{label}_missing")
    target = (raw if raw.is_absolute() else root / raw).resolve()
    try:
        target.relative_to(root)
    except ValueError as exc:
        raise AccountPatternPrepError(f"{label}_outside_workspace") from exc
    return target


def _atomic_write_json(path: Path, payload: Mapping[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    data = text.encode("utf-8")
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=str(path.parent))
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)
    if path.read_bytes() != data:
        raise AccountPatternPrepError("preparation_result_post_write_mismatch")


def _read_account(
    root: Path, source: str, account_id: str, platform: str
) -> dict[str, str]:
    library_rel = ACCOUNT_LIBRARY_PATHS.get(source)
    if library_rel is None:
        raise AccountPatternPrepError(f"scope_source_invalid:{source}")
    library_path = root / library_rel
    if not library_path.is_file():
        raise AccountPatternPrepError(f"account_library_missing:{library_rel.as_posix()}")
    _, rows, _ = read_csv_table_with_snapshot(library_path)
    matches = [
        row
        for row in rows
        if str(row.get("账号ID") or "").strip() == account_id
        and (not platform or str(row.get("平台") or "").strip() == platform)
    ]
    if not matches:
        raise AccountPatternPrepError("account_not_found")
    if len(matches) > 1:
        raise AccountPatternPrepError(f"account_not_unique:{len(matches)}")
    return {str(key): str(value or "").strip() for key, value in matches[0].items()}


def _instance_content_count(
    root: Path, source: str, account_id: str, platform: str
) -> int:
    """数该账号「做完特征识别」的去重内容条数。

    去重键与 `build_rank_comparison` 一致（`样本来源::样本键`）——一行是
    「一条内容 × 一个特征」，同一篇内容命中多个特征会占多行，不能按行数算。
    """
    instance_path = root / INSTANCE_PATH
    if not instance_path.is_file():
        raise AccountPatternPrepError(f"instance_missing:{INSTANCE_PATH.as_posix()}")
    _, rows, _ = read_csv_table_with_snapshot(instance_path)
    keys: set[str] = set()
    for row in rows:
        if str(row.get("当前有效") or "").strip() != "是":
            continue
        if str(row.get("实际采用状态") or "").strip() != "已采用":
            continue
        if str(row.get("样本来源") or "").strip() != source:
            continue
        if str(row.get("账号ID") or "").strip() != account_id:
            continue
        if platform and str(row.get("平台") or "").strip() != platform:
            continue
        keys.add(f"{row.get('样本来源', '')}::{row.get('样本键', '')}")
    return len(keys)


def prepare(
    workspace_root: str | Path,
    *,
    source: str,
    account_id: str,
    platform: str = "",
    output_file: str | Path,
    now: str | None = None,
) -> dict[str, object]:
    root = Path(workspace_root).expanduser().resolve()
    if not root.is_dir():
        raise AccountPatternPrepError(f"workspace_root_invalid:{root}")
    normalized_id = str(account_id or "").strip()
    if not normalized_id:
        raise AccountPatternPrepError("account_id_missing")
    source = str(source or "").strip()
    platform = str(platform or "").strip()
    target = _resolve_inside(root, output_file, "output_file")

    account = _read_account(root, source, account_id=normalized_id, platform=platform)
    account_name = account.get("账号名", "")
    scope = {
        "source": source,
        "platform": platform,
        "account_id": normalized_id,
    }
    scope_label = f"{platform}-{account_name}" if platform else account_name
    timestamp = _now_text(now)
    instance_contents = _instance_content_count(
        root, source, account_id=normalized_id, platform=platform
    )

    common: dict[str, object] = {
        "generated_at": timestamp,
        "analysis_scope_label": scope_label,
        "scope": scope,
        "account": {
            "账号ID": normalized_id,
            "平台": platform,
            "账号名": account_name,
            "内容列表路径": account.get("内容列表路径", ""),
        },
        "instance_content_count": instance_contents,
    }

    if instance_contents < MIN_INSTANCE_CONTENTS:
        payload: dict[str, object] = {
            "ok": True,
            "status": "skipped_no_instances",
            "reason": (
                f"该账号完成特征识别的内容数为 {instance_contents}，"
                f"低于门槛 {MIN_INSTANCE_CONTENTS}，本步跳过（不算失败）"
            ),
            **common,
        }
        _atomic_write_json(target, payload)
        return {**payload, "output_path": target.relative_to(root).as_posix()}

    try:
        rank = build_rank_comparison(root, scope=scope)
    except ContentPatternReportError as exc:
        raise AccountPatternPrepError(f"rank_comparison_failed:{exc}") from exc
    try:
        aggregation = aggregate_workspace(
            root,
            source=source,
            platform=platform,
            account_id=normalized_id,
            now=timestamp,
        )
    except ContentFeatureAggregationError as exc:
        raise AccountPatternPrepError(f"aggregation_failed:{exc}") from exc

    table_contents = (
        int(rank["high_content_count"])
        + int(rank["middle_content_count"])
        + int(rank["low_content_count"])
    )
    payload = {
        "ok": True,
        "status": "ready",
        **common,
        # 第 1 节「纳入内容数」= 该账号有特征实例的去重内容条数。
        "active_sample_count": instance_contents,
        # 第 1 节「规律数量」= 该账号切片算出的规律条数（不写主库）。
        "rule_count": int(aggregation["rule_count"]),
        "status_distribution": aggregation["status_distribution"],
        # 第 2 节写作要用的规律明细（标准名称 / 特征小类 / 作用环节 / 定义 / 去重样本数）。
        "rules": aggregation["rules"],
        # 第 3 节表格覆盖的内容数（三档之和）。正常情形下等于 instance_content_count；
        # 有内容拿不到档次时会更小，报告第 3 节的口径行要按这个数写。
        "table_content_count": table_contents,
        "rank_rule": rank["rank_rule"],
        "rank_comparison": rank,
        "markdown": rank["markdown"],
        "warnings": list(rank["warnings"]),
    }
    _atomic_write_json(target, payload)
    return {**payload, "output_path": target.relative_to(root).as_posix()}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="准备账号内容规律分析的机器数据")
    parser.add_argument("--workspace-root", type=Path, required=True)
    parser.add_argument("--source", choices=("对标内容", "我方内容"), required=True)
    parser.add_argument("--account-id", required=True)
    parser.add_argument("--platform", default="")
    parser.add_argument("--output-file", required=True, type=Path,
                        help="准备结果 JSON 写到哪（本轮暂存目录内）")
    parser.add_argument("--now", default=None, help="固定生成时间（测试用）")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        result = prepare(
            args.workspace_root,
            source=args.source,
            account_id=args.account_id,
            platform=args.platform,
            output_file=args.output_file,
            now=args.now,
        )
    except (OSError, UnicodeError, AccountPatternPrepError) as exc:
        print(json.dumps({"ok": False, "status": "failed", "error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 1
    print(json.dumps(
        {key: value for key, value in result.items() if key not in {"rank_comparison", "markdown", "rules"}},
        ensure_ascii=False,
    ))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
