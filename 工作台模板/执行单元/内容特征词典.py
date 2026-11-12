#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import Mapping, Sequence

from csv安全写入 import CsvTransactionError, read_csv_table_with_snapshot


class ContentFeatureDictionaryError(RuntimeError):
    """Raised when the workspace feature dictionary is missing or inconsistent."""


DICTIONARY_PATH = Path("沉淀结论/内容特征词典.csv")
FIELDS = [
    "特征ID", "所属层级", "特征小类", "标准标签", "定义", "适用边界", "排除边界",
    "吸引作用", "持续作用", "信任作用", "获得作用", "沉淀作用", "状态", "版本", "备注",
]
LAYERS = {"策略层", "动作层"}
STATUSES = {"启用", "停用"}
BOOLEAN_VALUES = {"是", "否"}
# 五个作用环节是**标签的属性**，不是独立的一层。一条标签可以同时挂多个环节，
# 也可以一个都不挂（内容类别不属于任何环节）。
EFFECT_COLUMNS = (
    ("吸引作用", "吸引"),
    ("持续作用", "持续"),
    ("信任作用", "信任"),
    ("获得作用", "获得"),
    ("沉淀作用", "沉淀"),
)
FEATURE_ID_PATTERN = re.compile(r"^(?:STR|ACT)-[A-Z]+-\d{3}$")


def default_workspace_root() -> Path:
    return Path(__file__).resolve().parents[1]


def dictionary_path(workspace_root: Path | str) -> Path:
    return Path(workspace_root).expanduser().resolve() / DICTIONARY_PATH


def validate_feature_rows(rows: Sequence[Mapping[str, str]]) -> dict[str, dict[str, object]]:
    catalog: dict[str, dict[str, object]] = {}
    for index, row in enumerate(rows, start=2):
        feature_id = str(row.get("特征ID") or "").strip()
        if not FEATURE_ID_PATTERN.fullmatch(feature_id):
            raise ContentFeatureDictionaryError(f"feature_id_invalid:row={index}:{feature_id}")
        if feature_id in catalog:
            raise ContentFeatureDictionaryError(f"feature_id_duplicate:{feature_id}")
        layer = str(row.get("所属层级") or "").strip()
        status = str(row.get("状态") or "").strip()
        if layer not in LAYERS:
            raise ContentFeatureDictionaryError(f"feature_layer_invalid:{feature_id}:{layer}")
        if status not in STATUSES:
            raise ContentFeatureDictionaryError(f"feature_status_invalid:{feature_id}:{status}")
        for field in ("特征小类", "标准标签", "定义", "适用边界", "排除边界", "版本"):
            value = str(row.get(field) or "")
            if not value or value != value.strip():
                raise ContentFeatureDictionaryError(f"feature_field_invalid:{feature_id}:{field}")
        stages: list[str] = []
        for field, stage in EFFECT_COLUMNS:
            value = str(row.get(field) or "").strip()
            if value not in BOOLEAN_VALUES:
                raise ContentFeatureDictionaryError(
                    f"feature_effect_flag_invalid:{feature_id}:{field}:{value}"
                )
            if value == "是":
                stages.append(stage)
        # 五个环节列可以全是「否」：内容类别这类标签不属于任何环节，是合法状态。
        if status == "启用":
            catalog[feature_id] = {
                "id": feature_id,
                "layer": layer,
                "feature_type": str(row["特征小类"]),
                "label": str(row["标准标签"]),
                "definition": str(row["定义"]),
                "include_boundary": str(row["适用边界"]),
                "exclude_boundary": str(row["排除边界"]),
                "effect_stages": stages,
                "version": str(row["版本"]),
            }
    return catalog


def read_feature_dictionary(
    workspace_root: Path | str | None = None,
) -> tuple[Path, list[str], list[dict[str, str]], bytes, dict[str, dict[str, object]]]:
    root = default_workspace_root() if workspace_root is None else Path(workspace_root).expanduser().resolve()
    path = root / DICTIONARY_PATH
    if not path.is_file():
        raise ContentFeatureDictionaryError(f"feature_dictionary_missing:{DICTIONARY_PATH.as_posix()}")
    try:
        fields, rows, snapshot = read_csv_table_with_snapshot(path)
    except CsvTransactionError as exc:
        raise ContentFeatureDictionaryError(f"feature_dictionary_invalid:{exc}") from exc
    if fields != FIELDS:
        raise ContentFeatureDictionaryError(
            f"feature_dictionary_header_invalid:expected={FIELDS}:actual={fields}"
        )
    catalog = validate_feature_rows(rows)
    return path, fields, rows, snapshot, catalog


def load_feature_catalog(
    workspace_root: Path | str | None = None,
) -> dict[str, dict[str, object]]:
    return read_feature_dictionary(workspace_root)[4]


# 以下两个函数只准备模型可以选择的确定性范围；它们不判断一篇内容
# 是否真正命中特征。命中判断和新特征发现仍属于模型工作。
PROFILE_FIELDS = ("主题", "命题", "切口", "目标用户", "核心痛点", "收益承诺")


def build_feature_options(catalog: Mapping[str, Mapping[str, object]]) -> list[dict[str, object]]:
    """Return the small, read-only selection range exposed to the model."""
    return [
        {
            "target_id": feature_id,
            "label": str(item["label"]),
            "effect_stages": list(item["effect_stages"]),
        }
        for feature_id, item in sorted(catalog.items())
    ]


def build_feature_type_scope(
    catalog: Mapping[str, Mapping[str, object]],
    feature_type: str,
) -> list[dict[str, object]]:
    """Return the read-only options of one dictionary feature type, ordered by feature id."""
    return [
        {
            "target_id": feature_id,
            "label": str(item["label"]),
            "definition": str(item["definition"]),
            "include_boundary": str(item["include_boundary"]),
            "exclude_boundary": str(item["exclude_boundary"]),
        }
        for feature_id, item in sorted(catalog.items())
        if item["feature_type"] == feature_type
    ]


def build_opening_label_scope(
    catalog: Mapping[str, Mapping[str, object]],
) -> list[dict[str, object]]:
    """Return the only reusable entry approaches shown during positioning."""
    return build_feature_type_scope(catalog, "开头")


def build_content_category_scope(
    catalog: Mapping[str, Mapping[str, object]],
) -> list[dict[str, object]]:
    """Return the dictionary-defined content categories positioning has to choose from."""
    return build_feature_type_scope(catalog, "内容类别")


def build_feature_selection_scope(
    workspace_root: Path | str | None = None,
    *,
    catalog: Mapping[str, Mapping[str, object]] | None = None,
) -> dict[str, object]:
    """Build the only dictionary data passed to a feature-identification model."""
    resolved_catalog = dict(catalog) if catalog is not None else load_feature_catalog(workspace_root)
    return {
        "schema_version": "1",
        "feature_options": build_feature_options(resolved_catalog),
        "opening_label_options": build_opening_label_scope(resolved_catalog),
        "profile_fields": list(PROFILE_FIELDS),
    }


def attach_feature_selection_scope(
    snapshot: Mapping[str, object],
    *,
    workspace_root: Path | str | None = None,
    catalog: Mapping[str, Mapping[str, object]] | None = None,
) -> dict[str, object]:
    """Add a compact, frozen selection scope without changing business evidence."""
    prepared = dict(snapshot)
    scope = build_feature_selection_scope(workspace_root, catalog=catalog)
    encoded = repr(scope).encode("utf-8")
    prepared["feature_selection_scope"] = {
        **scope,
        "sha256": hashlib.sha256(encoded).hexdigest(),
    }
    return prepared
