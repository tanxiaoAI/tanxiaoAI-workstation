#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Mapping

from csv安全写入 import CsvTransactionError, write_csv_tables_atomically
from 内容特征词典 import (
    EFFECT_COLUMNS,
    ContentFeatureDictionaryError,
    read_feature_dictionary,
    validate_feature_rows,
)


class ContentFeatureDictionaryUpdateError(RuntimeError):
    """Raised when a confirmed dictionary decision is malformed."""


SCHEMA_VERSION = "2"
ACTIONS = {"新增", "合并到已有特征", "仅保留为单篇观察", "用户暂不采纳"}
FEATURE_ID_NAMESPACES = {
    ("策略层", "内容类别"): "STR-CAT",
    ("动作层", "标题"): "ACT-TITLE",
    ("动作层", "开头"): "ACT-OPEN",
    ("动作层", "内容结构"): "ACT-STRUCT",
    ("动作层", "表达风格"): "ACT-STYLE",
    ("动作层", "互动设计"): "ACT-INTERACT",
}
# 这几个小类的标签不属于任何作用环节（内容类别只回答「靠哪类内容交付价值」），
# 所以提交候选时允许 `effect_stages` 为空；其余小类仍然必填。
NO_EFFECT_STAGE_TYPES = {"内容类别"}


def _mapping(value: object, label: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise ContentFeatureDictionaryUpdateError(f"{label}_must_be_object")
    return value


def _text(value: object, label: str, *, allow_blank: bool = False) -> str:
    if not isinstance(value, str) or value != value.strip() or (not allow_blank and not value):
        raise ContentFeatureDictionaryUpdateError(f"{label}_invalid")
    return value


def _next_feature_id(rows: list[dict[str, str]], *, layer: str, feature_type: str) -> str:
    namespace = FEATURE_ID_NAMESPACES.get((layer, feature_type))
    if namespace is None:
        raise ContentFeatureDictionaryUpdateError(
            f"feature_id_namespace_unsupported:{layer}:{feature_type}"
        )
    pattern = re.compile(rf"^{re.escape(namespace)}-(\d{{3}})$")
    highest = max(
        (
            int(match.group(1))
            for row in rows
            if (match := pattern.fullmatch(str(row.get("特征ID") or "").strip()))
        ),
        default=0,
    )
    if highest >= 999:
        raise ContentFeatureDictionaryUpdateError(f"feature_id_namespace_exhausted:{namespace}")
    return f"{namespace}-{highest + 1:03d}"


def _validate_replacement(
    replace_feature_id: str,
    *,
    target_layer: str,
    catalog: Mapping[str, Mapping[str, object]],
) -> None:
    if not replace_feature_id:
        return
    replacement = catalog.get(replace_feature_id)
    if replacement is None:
        raise ContentFeatureDictionaryUpdateError(
            f"replace_feature_unknown:{replace_feature_id}"
        )
    if replacement.get("layer") != target_layer:
        raise ContentFeatureDictionaryUpdateError(
            f"replace_feature_layer_mismatch:{replace_feature_id}:{target_layer}"
        )


def apply_confirmed_decision(
    workspace_root: Path | str,
    candidate: object,
    *,
    validate_only: bool = False,
) -> dict[str, object]:
    root = Path(workspace_root).expanduser().resolve()
    payload = _mapping(candidate, "root")
    if set(payload) != {
        "schema_version", "action", "candidate", "merge_target_id", "replace_feature_id",
    }:
        raise ContentFeatureDictionaryUpdateError("root_keys_invalid")
    if payload.get("schema_version") != SCHEMA_VERSION:
        raise ContentFeatureDictionaryUpdateError("schema_version_invalid")
    action = _text(payload.get("action"), "action")
    if action not in ACTIONS:
        raise ContentFeatureDictionaryUpdateError(f"action_invalid:{action}")
    item = _mapping(payload.get("candidate"), "candidate")
    merge_target_id = _text(payload.get("merge_target_id"), "merge_target_id", allow_blank=True)
    replace_feature_id = _text(
        payload.get("replace_feature_id"), "replace_feature_id", allow_blank=True
    )
    path, fields, rows, snapshot, catalog = read_feature_dictionary(root)

    if action == "合并到已有特征":
        if merge_target_id not in catalog:
            raise ContentFeatureDictionaryUpdateError(f"merge_target_unknown:{merge_target_id}")
        target_layer = str(catalog[merge_target_id]["layer"])
        _validate_replacement(
            replace_feature_id, target_layer=target_layer, catalog=catalog
        )
        if replace_feature_id == merge_target_id:
            raise ContentFeatureDictionaryUpdateError("replace_feature_is_merge_target")
        return {
            "status": "validated" if validate_only else "confirmed",
            "action": action,
            "target_feature_id": merge_target_id,
            "replace_feature_id": replace_feature_id,
            "dictionary_write_status": "not_needed",
        }
    if action == "用户暂不采纳":
        if merge_target_id:
            raise ContentFeatureDictionaryUpdateError("merge_target_must_be_blank")
        if replace_feature_id:
            raise ContentFeatureDictionaryUpdateError("replace_feature_must_be_blank")
        return {
            "status": "validated" if validate_only else "confirmed",
            "action": action,
            "target_feature_id": "",
            "replace_feature_id": "",
            "dictionary_write_status": "not_needed",
        }
    if action == "仅保留为单篇观察":
        if merge_target_id:
            raise ContentFeatureDictionaryUpdateError("merge_target_must_be_blank")
        if replace_feature_id:
            raise ContentFeatureDictionaryUpdateError("replace_feature_must_be_blank")
        return {
            "status": "validated" if validate_only else "confirmed",
            "action": action,
            "replace_feature_id": "",
            "dictionary_write_status": "not_needed",
        }
    if merge_target_id:
        raise ContentFeatureDictionaryUpdateError("merge_target_must_be_blank")

    expected = {
        "layer", "feature_type", "label", "definition", "include_boundary",
        "exclude_boundary", "effect_stages", "version", "notes",
    }
    if set(item) != expected:
        raise ContentFeatureDictionaryUpdateError("candidate_keys_invalid")
    layer = _text(item.get("layer"), "layer")
    feature_type = _text(item.get("feature_type"), "feature_type")
    stages = item.get("effect_stages")
    if not isinstance(stages, list) or any(not isinstance(value, str) for value in stages):
        raise ContentFeatureDictionaryUpdateError("effect_stages_invalid")
    if not stages and feature_type not in NO_EFFECT_STAGE_TYPES:
        raise ContentFeatureDictionaryUpdateError("effect_stages_invalid")
    _validate_replacement(replace_feature_id, target_layer=layer, catalog=catalog)
    new_row = {field: "" for field in fields}
    new_row.update(
        {
            "特征ID": _next_feature_id(rows, layer=layer, feature_type=feature_type),
            "所属层级": layer,
            "特征小类": feature_type,
            "标准标签": _text(item.get("label"), "label"),
            "定义": _text(item.get("definition"), "definition"),
            "适用边界": _text(item.get("include_boundary"), "include_boundary"),
            "排除边界": _text(item.get("exclude_boundary"), "exclude_boundary"),
            "状态": "启用",
            "版本": _text(item.get("version"), "version"),
            "备注": _text(item.get("notes"), "notes", allow_blank=True),
        }
    )
    stage_set = set(stages)
    for field, stage in EFFECT_COLUMNS:
        new_row[field] = "是" if stage in stage_set else "否"
    prepared = [dict(row) for row in rows] + [new_row]
    validate_feature_rows(prepared)
    if validate_only:
        return {
            "status": "validated",
            "action": action,
            "target_feature_id": new_row["特征ID"],
            "replace_feature_id": replace_feature_id,
            "dictionary_write_status": "not_started",
        }
    try:
        write_csv_tables_atomically(
            [(path, fields, prepared)], expected_originals={path: snapshot}
        )
    except CsvTransactionError as exc:
        raise ContentFeatureDictionaryUpdateError(f"dictionary_write_failed:{exc}") from exc
    read_feature_dictionary(root)
    return {
        "status": "committed",
        "action": action,
        "target_feature_id": new_row["特征ID"],
        "replace_feature_id": replace_feature_id,
        "dictionary_write_status": "succeeded",
    }


def apply_confirmed_decisions_atomically(
    workspace_root: Path | str,
    candidates: list[object],
    *,
    validate_only: bool = False,
) -> list[dict[str, object]]:
    """一次校验并提交一组发布后复盘的特征决定。"""
    root = Path(workspace_root).expanduser().resolve()
    if not isinstance(candidates, list) or not candidates:
        raise ContentFeatureDictionaryUpdateError("decision_batch_invalid")
    path, fields, rows, snapshot, catalog = read_feature_dictionary(root)
    projected_rows = [dict(row) for row in rows]
    projected_catalog = dict(catalog)
    results: list[dict[str, object]] = []
    has_dictionary_change = False

    for raw_payload in candidates:
        payload = _mapping(raw_payload, "root")
        if set(payload) != {
            "schema_version", "action", "candidate", "merge_target_id", "replace_feature_id",
        }:
            raise ContentFeatureDictionaryUpdateError("root_keys_invalid")
        if payload.get("schema_version") != SCHEMA_VERSION:
            raise ContentFeatureDictionaryUpdateError("schema_version_invalid")
        action = _text(payload.get("action"), "action")
        if action not in ACTIONS:
            raise ContentFeatureDictionaryUpdateError(f"action_invalid:{action}")
        item = _mapping(payload.get("candidate"), "candidate")
        merge_target_id = _text(payload.get("merge_target_id"), "merge_target_id", allow_blank=True)
        replace_feature_id = _text(
            payload.get("replace_feature_id"), "replace_feature_id", allow_blank=True
        )
        # 此字段仅为旧调用兼容；发布后复盘未实现替换、停用能力。
        if replace_feature_id:
            raise ContentFeatureDictionaryUpdateError("replace_feature_not_supported_in_batch")

        if action == "合并到已有特征":
            if merge_target_id not in projected_catalog:
                raise ContentFeatureDictionaryUpdateError(f"merge_target_unknown:{merge_target_id}")
            results.append({
                "status": "validated" if validate_only else "confirmed",
                "action": action,
                "target_feature_id": merge_target_id,
                "replace_feature_id": "",
                "dictionary_write_status": "not_needed",
            })
            continue
        if action == "用户暂不采纳":
            if merge_target_id:
                raise ContentFeatureDictionaryUpdateError("merge_target_must_be_blank")
            results.append({
                "status": "validated" if validate_only else "confirmed",
                "action": action,
                "target_feature_id": "",
                "replace_feature_id": "",
                "dictionary_write_status": "not_needed",
            })
            continue
        if action == "仅保留为单篇观察":
            if merge_target_id:
                raise ContentFeatureDictionaryUpdateError("merge_target_must_be_blank")
            results.append({
                "status": "validated" if validate_only else "confirmed",
                "action": action,
                "target_feature_id": "",
                "replace_feature_id": "",
                "dictionary_write_status": "not_needed",
            })
            continue
        if merge_target_id:
            raise ContentFeatureDictionaryUpdateError("merge_target_must_be_blank")
        expected = {
            "layer", "feature_type", "label", "definition", "include_boundary",
            "exclude_boundary", "effect_stages", "version", "notes",
        }
        if set(item) != expected:
            raise ContentFeatureDictionaryUpdateError("candidate_keys_invalid")
        layer = _text(item.get("layer"), "layer")
        feature_type = _text(item.get("feature_type"), "feature_type")
        stages = item.get("effect_stages")
        if not isinstance(stages, list) or any(not isinstance(value, str) for value in stages):
            raise ContentFeatureDictionaryUpdateError("effect_stages_invalid")
        if not stages and feature_type not in NO_EFFECT_STAGE_TYPES:
            raise ContentFeatureDictionaryUpdateError("effect_stages_invalid")
        new_row = {field: "" for field in fields}
        new_row.update({
            "特征ID": _next_feature_id(projected_rows, layer=layer, feature_type=feature_type),
            "所属层级": layer,
            "特征小类": feature_type,
            "标准标签": _text(item.get("label"), "label"),
            "定义": _text(item.get("definition"), "definition"),
            "适用边界": _text(item.get("include_boundary"), "include_boundary"),
            "排除边界": _text(item.get("exclude_boundary"), "exclude_boundary"),
            "状态": "启用",
            "版本": _text(item.get("version"), "version"),
            "备注": _text(item.get("notes"), "notes", allow_blank=True),
        })
        stage_set = set(stages)
        for field, stage in EFFECT_COLUMNS:
            new_row[field] = "是" if stage in stage_set else "否"
        projected_rows.append(new_row)
        projected_catalog = validate_feature_rows(projected_rows)
        has_dictionary_change = True
        results.append({
            "status": "validated" if validate_only else "committed",
            "action": action,
            "target_feature_id": new_row["特征ID"],
            "replace_feature_id": "",
            "dictionary_write_status": "planned" if validate_only else "succeeded",
        })

    if has_dictionary_change and not validate_only:
        try:
            write_csv_tables_atomically(
                [(path, fields, projected_rows)], expected_originals={path: snapshot}
            )
        except CsvTransactionError as exc:
            raise ContentFeatureDictionaryUpdateError(f"dictionary_write_failed:{exc}") from exc
        read_feature_dictionary(root)
    return results


def main() -> int:
    parser = argparse.ArgumentParser(description="提交用户已经确认的内容特征词典变更")
    parser.add_argument("--workspace-root", required=True, type=Path)
    parser.add_argument("--candidate-file", required=True, type=Path)
    parser.add_argument("--validate-only", action="store_true")
    args = parser.parse_args()
    try:
        candidate = json.loads(args.candidate_file.read_text(encoding="utf-8"))
        result = apply_confirmed_decision(
            args.workspace_root, candidate, validate_only=args.validate_only
        )
    except (
        OSError,
        UnicodeError,
        json.JSONDecodeError,
        ContentFeatureDictionaryError,
        ContentFeatureDictionaryUpdateError,
    ) as exc:
        print(json.dumps({"status": "failed", "error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
