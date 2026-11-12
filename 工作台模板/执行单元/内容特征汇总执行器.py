#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Mapping

from csv安全写入 import (
    CsvTransactionError,
    read_csv_table_with_snapshot,
)
from 内容特征词典 import ContentFeatureDictionaryError, load_feature_catalog


class ContentFeatureAggregationError(RuntimeError):
    """Raised when content feature evidence cannot be aggregated safely."""


INSTANCE_PATH = Path("沉淀结论/内容特征实例.csv")
# 规律库（沉淀结论/内容特征规律库.csv）已于 2026-09-28 退役：本执行器只算、不落盘。

INSTANCE_REQUIRED_FIELDS = {
    "样本键", "样本来源", "内容ID", "平台", "账号ID", "媒介形式",
    "当前有效", "特征ID", "标准标签", "实际采用状态",
}
EVIDENCE_STATUSES = (
    "外部重复", "外部多篇观察", "单篇观察", "我方观察",
)


def _read_table(
    root: Path,
    relative_path: Path,
    required_fields: set[str],
) -> tuple[Path, list[str], list[dict[str, str]], bytes]:
    path = root / relative_path
    if not path.is_file():
        raise ContentFeatureAggregationError(f"required_table_missing:{relative_path.as_posix()}")
    try:
        fields, rows, snapshot = read_csv_table_with_snapshot(path)
    except CsvTransactionError as exc:
        raise ContentFeatureAggregationError(str(exc)) from exc
    missing = sorted(required_fields - set(fields))
    if missing:
        raise ContentFeatureAggregationError(
            f"required_table_missing_fields:{relative_path.as_posix()}:{missing}"
        )
    return path, fields, rows, snapshot


def _load_catalog(root: Path) -> dict[str, dict[str, object]]:
    try:
        return load_feature_catalog(root)
    except ContentFeatureDictionaryError as exc:
        raise ContentFeatureAggregationError(f"feature_catalog_invalid:{exc}") from exc


def _rule_id(parts: tuple[str, ...]) -> str:
    digest = hashlib.sha256("\x1f".join(parts).encode("utf-8")).hexdigest()[:12]
    return f"规律{digest}"


def _now_text(now: str | None) -> str:
    return now or datetime.now().astimezone().isoformat(timespec="seconds")


def _evidence_status(
    evidence_scope: str,
    *,
    sample_count: int,
) -> str:
    """按证据范围给出证据状态。

    「外部重复」只看对标内容的篇数（≥5 篇），**不要求跨账号**：同一个账号里
    反复出现的做法同样是「反复出现」，账号数只作为随行字段写进规律的
    「涉及账号数」列，不参与分档。原来叠加「≥2 个账号」会让样本集中在单一
    账号的规律永远卡在「外部多篇观察」，把「做法本身重复」和「来源够不够散」
    两件事混在一起判。

    「我方观察」的口径是「我方内容中的重复观察」，所以只有在本账号里出现
    2 篇及以上才算，单篇只能算「单篇观察」。不能让只出现过一次的做法冒称
    重复观察，否则 1 篇和 N 篇会被压成同一档。
    """
    if evidence_scope == "同平台对标":
        if sample_count >= 5:
            return "外部重复"
        return "单篇观察" if sample_count == 1 else "外部多篇观察"
    return "单篇观察" if sample_count == 1 else "我方观察"


def _validate_rows(
    instance_rows: list[dict[str, str]],
    catalog: Mapping[str, object],
) -> None:
    active_keys: set[tuple[str, str, str]] = set()
    invalid: list[str] = []
    duplicated: list[str] = []
    for row in instance_rows:
        if row.get("当前有效") != "是" or row.get("实际采用状态") != "已采用":
            continue
        # 身份要带样本来源：对标内容和我方内容各自编号，只按「样本键 + 特征ID」
        # 判唯一会把两条互不相干的内容当成重复。
        key = (row.get("样本来源", ""), row.get("样本键", ""), row.get("特征ID", ""))
        if not key[1] or key[2] not in catalog:
            invalid.append(str(key))
            continue
        if key in active_keys:
            duplicated.append(str(key))
            continue
        active_keys.add(key)
    # 一次把所有冲突列全：只报第一条会让用户改一条、重算一次、再撞下一条。
    # 错误码前缀保持不变，下游文档按前缀识别。
    if invalid or duplicated:
        parts: list[str] = []
        if invalid:
            parts.append(f"active_instance_invalid:{'|'.join(invalid)}")
        if duplicated:
            parts.append(f"duplicate_active_instance:{'|'.join(duplicated)}")
        raise ContentFeatureAggregationError("；".join(parts))

def _matches_scope(row: Mapping[str, str], scope: Mapping[str, str]) -> bool:
    source = scope.get("source", "")
    if source and row.get("样本来源", "") != source:
        return False
    dimensions = {
        "platform": "平台",
        "account_id": "账号ID",
        "media_form": "媒介形式",
    }
    return all(not scope.get(key) or row.get(field, "") == scope[key] for key, field in dimensions.items())


def _build_rules(
    instance_rows: list[dict[str, str]],
    catalog: Mapping[str, Mapping[str, object]],
    *,
    now: str,
    scope: Mapping[str, str] | None = None,
) -> tuple[list[dict[str, str]], set[str]]:
    active_instances = [
        row for row in instance_rows
        if row.get("当前有效") == "是"
        and row.get("实际采用状态") == "已采用"
        and (scope is None or _matches_scope(row, scope))
    ]
    groups: dict[tuple[str, str, str, str, str, str], dict[str, object]] = {}
    for row in active_instances:
        feature_id = row["特征ID"]
        source = row["样本来源"]
        if source == "我方内容":
            evidence_scope = "我方同账号"
            scope_account = row.get("账号ID", "")
        elif source == "对标内容":
            evidence_scope = "同平台对标"
            scope_account = ""
        else:
            raise ContentFeatureAggregationError(f"instance_source_invalid:{source}")
        group_key = (
            feature_id,
            evidence_scope,
            row.get("平台", ""),
            scope_account,
            row.get("媒介形式", ""),
        )
        group = groups.setdefault(
            group_key,
            {"samples": set(), "accounts": set(), "stages": set()},
        )
        group["samples"].add(row["样本键"])
        if row.get("账号ID", ""):
            group["accounts"].add(row["账号ID"])
        group["stages"].update(catalog[feature_id].get("effect_stages") or [])

    rules: list[dict[str, str]] = []
    for group_key in sorted(groups):
        feature_id, evidence_scope, platform, account_id, media_form = group_key
        group = groups[group_key]
        samples = set(group["samples"])
        accounts = set(group["accounts"])
        status = _evidence_status(
            evidence_scope,
            sample_count=len(samples),
        )
        rules.append(
            {
                "规律ID": _rule_id(tuple(group_key)),
                "目标ID": feature_id,
                "标准名称": str(catalog[feature_id]["label"]),
                "证据范围": evidence_scope,
                "平台": platform,
                "账号ID": account_id,
                "媒介形式": media_form,
                "作用环节": "|".join(sorted(value for value in group["stages"] if value)),
                # 报告第 2 节要写出「这个做法是什么意思」，所以每条规律多带一份词典定义
                # （只进汇总结果，不落盘）。
                "定义": str(catalog[feature_id]["definition"]),
                "特征小类": str(catalog[feature_id]["feature_type"]),
                "去重样本数": str(len(samples)),
                # 列保留给老客户端；它的值原本和「去重样本数」完全一样，不再写值。
                "实际采用次数": "",
                "涉及账号数": str(len(accounts)),
                "证据状态": status,
                "更新时间": now,
                "备注": "仅表示该做法在该场景反复出现，不能证明单一特征造成结果",
            }
        )
    return rules, {row["样本键"] for row in active_instances}


def aggregate_workspace(
    workspace_root: Path | str,
    *,
    source: str = "",
    platform: str = "",
    account_id: str = "",
    media_form: str = "",
    now: str | None = None,
) -> dict[str, object]:
    root = Path(workspace_root).expanduser().resolve()
    if not root.is_dir():
        raise ContentFeatureAggregationError(f"workspace_root_invalid:{root}")
    if source not in {"", "对标内容", "我方内容"}:
        raise ContentFeatureAggregationError(f"scope_source_invalid:{source}")

    _, _, instance_rows, instance_snapshot = _read_table(
        root, INSTANCE_PATH, INSTANCE_REQUIRED_FIELDS
    )
    catalog = _load_catalog(root)
    _validate_rows(instance_rows, catalog)

    timestamp = _now_text(now)
    all_rules, all_samples = _build_rules(instance_rows, catalog, now=timestamp)
    scope = {
        "source": source,
        "platform": platform,
        "account_id": account_id,
        "media_form": media_form,
    }
    if any(scope.values()):
        summary_rules, selected_samples = _build_rules(
            instance_rows,
            catalog,
            now=timestamp,
            scope=scope,
        )
    else:
        summary_rules, selected_samples = all_rules, all_samples

    result: dict[str, object] = {
        "status": "committed",
        "generated_at": timestamp,
        "scope": scope,
        "source_counts": {
            INSTANCE_PATH.as_posix(): len(instance_rows),
        },
        "source_fingerprints": {
            INSTANCE_PATH.as_posix(): hashlib.sha256(instance_snapshot).hexdigest(),
        },
        "active_sample_count": len(selected_samples),
        "rule_count": len(summary_rules),
        "status_distribution": {
            status: sum(1 for row in summary_rules if row["证据状态"] == status)
            for status in EVIDENCE_STATUSES
        },
        "rules": summary_rules,
    }
    return result


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="汇总内容特征实例的场景观察")
    parser.add_argument("--workspace-root", type=Path, required=True, help="当前工作区根目录")
    parser.add_argument("--source", choices=("对标内容", "我方内容"), default="")
    parser.add_argument("--platform", default="")
    parser.add_argument("--account-id", default="")
    parser.add_argument("--media-form", default="")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        result = aggregate_workspace(
            args.workspace_root,
            source=args.source,
            platform=args.platform,
            account_id=args.account_id,
            media_form=args.media_form,
        )
    except ContentFeatureAggregationError as exc:
        print(json.dumps({"status": "failed", "error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
