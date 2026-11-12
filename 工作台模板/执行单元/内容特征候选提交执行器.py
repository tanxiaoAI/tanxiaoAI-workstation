#!/usr/bin/env python3
"""把内容特征入库流程产出的新特征候选，按用户确认后的决定原子写入内容特征词典。

链路位置：内容特征入库流程 2.4。模型按入库判定规则先给出建议，用户整批确认后，
流程把没被采纳的 `新增` 改成 `用户暂不采纳`；本执行器解析这份候选，把决定交给
内容特征词典更新执行器 统一校验收口，不重复实现词典写入逻辑。

词典是全局共享资产，因此入库门槛由程序守住：`新增` 行的 `出现次数`（来源内容篇数）
必须不小于 AUTO_ADMISSION_MIN_SOURCES。该阈值与
`提示词/内容规律分析-新特征判断提示词.md` 的「入库判定规则」必须一致，
**改门槛要同时改这两处**；流程文档与 Skill 都不承载数值。
只有 `新增` 会写词典，其余三类决定都只记录，不改正式资产。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import tempfile
from pathlib import Path, PurePosixPath
from typing import Mapping, Sequence

from 内容特征词典 import LAYERS
from 内容特征词典更新执行器 import (
    FEATURE_ID_NAMESPACES,
    NO_EFFECT_STAGE_TYPES,
    ContentFeatureDictionaryUpdateError,
    apply_confirmed_decisions_atomically,
)


class ContentFeatureCandidateError(RuntimeError):
    """Raised when a new-feature candidate cannot be committed safely."""


CANDIDATE_TITLE = "内容特征候选"
CANDIDATE_SECTIONS = ("1. 本轮范围", "2. 新特征候选", "3. 处理说明")
SCOPE_LABELS = ("生成时间", "观察覆盖内容数", "未覆盖观察条数", "候选数量")
SCOPE_LINE = re.compile(r"(?m)^-\s*(\S+?)\s*[：:]\s*(.*?)\s*$")
CANDIDATE_TABLE_HEADER = (
    "候选序号",
    "最终决定",
    "所属层级",
    "特征小类",
    "标准标签",
    "定义",
    "适用边界",
    "排除边界",
    "作用环节",
    "合并目标",
    "出现次数",
    "判断依据",
)
DECISION_ACTIONS = ("新增", "合并到已有特征", "仅保留为单篇观察", "用户暂不采纳")
EFFECT_STAGES = ("吸引", "持续", "信任", "获得", "沉淀")
BLANK_MARKERS = {"", "—", "-", "–", "/", "无", "不适用"}
STAGING_PREFIX = ".meiling/staging/"
RECEIPT_SCHEMA_VERSION = "1"
REQUIRED_SINGLE_FIELDS = ("标准标签", "定义", "适用边界", "排除边界")
AUTO_ADMISSION_MIN_SOURCES = 3
OCCURRENCE_COLUMN = "出现次数"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _resolve_staging(root: Path, raw_path: str | Path, label: str) -> Path:
    text = str(raw_path or "").strip().replace("\\", "/")
    if not text:
        raise ContentFeatureCandidateError(f"{label}_missing")
    logical = PurePosixPath(text)
    candidate = Path(text).expanduser()
    resolved = candidate.resolve() if candidate.is_absolute() else root.joinpath(*logical.parts).resolve()
    try:
        relative = resolved.relative_to(root).as_posix()
    except ValueError as exc:
        raise ContentFeatureCandidateError(f"{label}_outside_workspace") from exc
    if not relative.startswith(STAGING_PREFIX):
        raise ContentFeatureCandidateError(f"{label}_outside_staging")
    return resolved


def _section_body(markdown: str, name: str) -> str:
    pattern = re.compile(rf"(?m)^##\s+{re.escape(name)}\s*$")
    match = pattern.search(markdown)
    if match is None:
        raise ContentFeatureCandidateError(f"candidate_section_missing:{name}")
    following = re.compile(r"(?m)^##\s+\d+\.\s*").search(markdown, match.end())
    body_end = following.start() if following else len(markdown)
    return markdown[match.end():body_end]


def _cells(line: str) -> list[str]:
    return [cell.strip() for cell in line.strip().strip("|").split("|")]


def _is_separator(line: str) -> bool:
    return bool(line) and all(set(cell) <= {"-", ":", " "} and cell for cell in _cells(line))


def _candidate_rows(body: str) -> list[dict[str, str]]:
    lines = [line.strip() for line in body.splitlines() if line.strip().startswith("|")]
    if not lines:
        return []
    if tuple(_cells(lines[0])) != CANDIDATE_TABLE_HEADER:
        raise ContentFeatureCandidateError("candidate_table_header_invalid")
    if len(lines) < 2 or not _is_separator(lines[1]):
        raise ContentFeatureCandidateError("candidate_table_separator_invalid")
    rows: list[dict[str, str]] = []
    for index, line in enumerate(lines[2:], start=1):
        cells = _cells(line)
        if len(cells) != len(CANDIDATE_TABLE_HEADER):
            raise ContentFeatureCandidateError(f"candidate_row_width_invalid:{index}")
        rows.append(dict(zip(CANDIDATE_TABLE_HEADER, cells)))
    return rows


def _blank(value: str) -> bool:
    return value.strip() in BLANK_MARKERS


def _require(value: str, label: str, index: int) -> str:
    text = value.strip()
    if not text or text in BLANK_MARKERS:
        raise ContentFeatureCandidateError(f"candidate_field_missing:{index}:{label}")
    return text


def _scope(markdown: str) -> dict[str, str]:
    body = _section_body(markdown, "1. 本轮范围")
    values: dict[str, str] = {}
    for match in SCOPE_LINE.finditer(body):
        label, value = match.group(1).strip(), match.group(2).strip().strip("`")
        if label in SCOPE_LABELS:
            if label in values:
                raise ContentFeatureCandidateError(f"candidate_scope_duplicated:{label}")
            values[label] = value
    for label in SCOPE_LABELS:
        if not values.get(label):
            raise ContentFeatureCandidateError(f"candidate_scope_field_invalid:{label}")
    return values


def _validate_scope(scope: Mapping[str, str], observation: Mapping[str, object]) -> None:
    expected = {
        "生成时间": str(observation.get("generated_at") or ""),
        "观察覆盖内容数": str(observation.get("active_content_count")),
        "未覆盖观察条数": str(observation.get("observation_count")),
    }
    for label, value in expected.items():
        if scope[label] != value:
            raise ContentFeatureCandidateError(f"candidate_scope_mismatch:{label}")
    count = scope["候选数量"]
    if not count.isdigit():
        raise ContentFeatureCandidateError("candidate_scope_field_invalid:候选数量")


def _observation_sources(observation: Mapping[str, object] | None) -> str:
    """把本轮观察覆盖到的来源内容，写成新特征入库时的备注。

    候选表固定 12 列、没有来源列，但用户事后在词典里需要知道一条新特征是
    从哪些内容里总结出来的，所以这里从观察汇总的来源里补一句。
    """
    if not isinstance(observation, Mapping):
        return ""
    ordered: list[str] = []
    clusters = observation.get("clusters")
    if isinstance(clusters, list):
        for cluster in clusters:
            if not isinstance(cluster, Mapping):
                continue
            for item in cluster.get("sources") or []:
                if isinstance(item, Mapping):
                    value = str(item.get("content_id") or "").strip()
                    if value and value not in ordered:
                        ordered.append(value)
    if not ordered:
        return ""
    head = ordered[:5]
    text = "、".join(head)
    if len(ordered) > len(head):
        return f"本轮观察来源：{text} 等 {len(ordered)} 篇"
    return f"本轮观察来源：{text}"


def _build_payloads(
    rows: Sequence[Mapping[str, str]],
    *,
    expected_count: int,
    observation: Mapping[str, object] | None = None,
) -> list[dict[str, object]]:
    if len(rows) != expected_count:
        raise ContentFeatureCandidateError(
            f"candidate_count_mismatch:expected={expected_count}:actual={len(rows)}"
        )
    payloads: list[dict[str, object]] = []
    notes = _observation_sources(observation)
    for index, row in enumerate(rows, start=1):
        sequence = row["候选序号"].strip()
        if not re.fullmatch(r"\d{3}", sequence) or sequence != f"{index:03d}":
            raise ContentFeatureCandidateError(f"candidate_sequence_invalid:{index}")
        action = row["最终决定"].strip()
        if action not in DECISION_ACTIONS:
            raise ContentFeatureCandidateError(f"candidate_decision_invalid:{index}")
        merge_target = "" if _blank(row["合并目标"]) else row["合并目标"].strip()
        if action == "新增":
            if merge_target:
                raise ContentFeatureCandidateError(f"candidate_merge_target_not_allowed:{index}")
            layer = _require(row["所属层级"], "所属层级", index)
            feature_type = _require(row["特征小类"], "特征小类", index)
            if layer not in LAYERS:
                raise ContentFeatureCandidateError(f"candidate_layer_invalid:{index}")
            if (layer, feature_type) not in FEATURE_ID_NAMESPACES:
                raise ContentFeatureCandidateError(f"candidate_feature_type_invalid:{index}")
            stages = [
                value.strip()
                for value in re.split(r"[|、／/]", row["作用环节"])
                if value.strip()
            ]
            if any(stage not in EFFECT_STAGES for stage in stages):
                raise ContentFeatureCandidateError(f"candidate_effect_stage_invalid:{index}")
            # 内容类别不属于任何作用环节，允许留空；其余小类必填。
            if not stages and feature_type not in NO_EFFECT_STAGE_TYPES:
                raise ContentFeatureCandidateError(f"candidate_effect_stage_invalid:{index}")
            if len(set(stages)) != len(stages):
                raise ContentFeatureCandidateError(f"candidate_effect_stage_duplicated:{index}")
            occurrences = row[OCCURRENCE_COLUMN].strip()
            if not occurrences.isdigit():
                raise ContentFeatureCandidateError(f"candidate_occurrences_invalid:{index}")
            if int(occurrences) < AUTO_ADMISSION_MIN_SOURCES:
                raise ContentFeatureCandidateError(
                    f"candidate_source_count_below_threshold:{index}:{occurrences}"
                )
            payloads.append(
                {
                    "schema_version": "2",
                    "action": action,
                    "candidate": {
                        "layer": layer,
                        "feature_type": feature_type,
                        "label": _require(row["标准标签"], "标准标签", index),
                        "definition": _require(row["定义"], "定义", index),
                        "include_boundary": _require(row["适用边界"], "适用边界", index),
                        "exclude_boundary": _require(row["排除边界"], "排除边界", index),
                        "effect_stages": stages,
                        "version": "v1",
                        "notes": notes,
                    },
                    "merge_target_id": "",
                    "replace_feature_id": "",
                }
            )
            continue
        if action == "合并到已有特征":
            if not merge_target:
                raise ContentFeatureCandidateError(f"candidate_merge_target_missing:{index}")
            payloads.append(
                {
                    "schema_version": "2",
                    "action": action,
                    "candidate": {},
                    "merge_target_id": merge_target,
                    "replace_feature_id": "",
                }
            )
            continue
        if merge_target:
            raise ContentFeatureCandidateError(f"candidate_merge_target_not_allowed:{index}")
        payloads.append(
            {
                "schema_version": "2",
                "action": action,
                "candidate": {},
                "merge_target_id": "",
                "replace_feature_id": "",
            }
        )
    return payloads


def _candidate_evidence(
    row: Mapping[str, str], observation: Mapping[str, object] | None
) -> str:
    """给一条候选找它在观察汇总里的那一簇，拼一句粗证据。

    提交回执的「实例证据」列由模型润色成可读的一句话，这里只提供来源坐标，
    让它不必回头再读观察结果文件。
    """
    if not isinstance(observation, Mapping):
        return ""
    label = str(row.get("标准标签") or "").strip()
    clusters = observation.get("clusters")
    if not label or not isinstance(clusters, list):
        return ""
    for cluster in clusters:
        if not isinstance(cluster, Mapping):
            continue
        representative = str(cluster.get("representative") or "").strip()
        variants = [str(value).strip() for value in (cluster.get("variants") or [])]
        if label != representative and label not in variants:
            continue
        ids: list[str] = []
        for item in cluster.get("sources") or []:
            if not isinstance(item, Mapping):
                continue
            value = str(item.get("content_id") or "").strip()
            if value and value not in ids:
                ids.append(value)
        text = representative or label
        if ids:
            return f"{text}（来源：{'、'.join(ids[:5])}）"
        return text
    return ""


def _decision_details(
    rows: Sequence[Mapping[str, str]],
    observation: Mapping[str, object] | None,
    decisions: Sequence[Mapping[str, object]],
) -> list[dict[str, object]]:
    """把候选表的内容并进提交回执，让本轮反馈一次凑齐六列。"""
    if len(decisions) != len(rows):
        return [dict(item) for item in decisions]
    enriched: list[dict[str, object]] = []
    for row, item in zip(rows, decisions):
        merged = dict(item)
        merged["候选标签"] = str(row.get("标准标签") or "").strip()
        merged["特征小类"] = str(row.get("特征小类") or "").strip()
        merged["定义"] = str(row.get("定义") or "").strip()
        merged["出现篇数"] = str(row.get(OCCURRENCE_COLUMN) or "").strip()
        merged["实例证据"] = _candidate_evidence(row, observation)
        merged["是否入库"] = (
            "是" if item.get("dictionary_write_status") == "succeeded" else "否"
        )
        enriched.append(merged)
    return enriched


def _write_json(path: Path, payload: Mapping[str, object]) -> None:
    data = (json.dumps(payload, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    temporary = Path(name)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def commit_feature_candidates(
    workspace_root: Path | str,
    *,
    candidate_file: str | Path,
    observation_result_file: str | Path,
    receipt_file: str | Path,
    validate_only: bool = False,
) -> dict[str, object]:
    root = Path(workspace_root).expanduser().resolve()
    if not root.is_dir():
        raise ContentFeatureCandidateError(f"workspace_root_invalid:{root}")
    candidate_path = _resolve_staging(root, candidate_file, "candidate_file")
    observation_path = _resolve_staging(root, observation_result_file, "observation_result_file")
    receipt_path = _resolve_staging(root, receipt_file, "receipt_file")
    if candidate_path.parent != observation_path.parent or candidate_path.parent != receipt_path.parent:
        raise ContentFeatureCandidateError("staging_directory_mismatch")
    for path, label in (
        (candidate_path, "candidate_file"),
        (observation_path, "observation_result_file"),
    ):
        if not path.is_file():
            raise ContentFeatureCandidateError(f"{label}_missing")

    candidate_sha256 = _sha256(candidate_path)
    if receipt_path.is_file():
        try:
            existing = json.loads(receipt_path.read_text(encoding="utf-8-sig"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise ContentFeatureCandidateError("feature_decision_receipt_invalid") from exc
        if not isinstance(existing, Mapping) or existing.get("schema_version") != RECEIPT_SCHEMA_VERSION:
            raise ContentFeatureCandidateError("feature_decision_receipt_invalid")
        if existing.get("candidate_sha256") == candidate_sha256:
            return {**existing, "ok": True, "reused": True}

    try:
        observation = json.loads(observation_path.read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ContentFeatureCandidateError("observation_result_invalid") from exc
    if not isinstance(observation, Mapping) or observation.get("status") != "collected":
        raise ContentFeatureCandidateError("observation_result_invalid")

    markdown = candidate_path.read_text(encoding="utf-8-sig")
    if len(re.findall(rf"(?m)^#\s+{re.escape(CANDIDATE_TITLE)}\s*$", markdown)) != 1:
        raise ContentFeatureCandidateError("candidate_title_invalid")
    headings = re.findall(r"(?m)^##\s+(.+?)\s*$", markdown)
    if tuple(headings) != CANDIDATE_SECTIONS:
        raise ContentFeatureCandidateError(f"candidate_sections_invalid:{headings}")
    if not _section_body(markdown, "3. 处理说明").strip():
        raise ContentFeatureCandidateError("candidate_notes_empty")
    scope = _scope(markdown)
    _validate_scope(scope, observation)
    rows = _candidate_rows(_section_body(markdown, "2. 新特征候选"))
    payloads = _build_payloads(
        rows,
        expected_count=int(scope["候选数量"]),
        observation=observation,
    )

    if payloads:
        try:
            decisions = apply_confirmed_decisions_atomically(
                root, payloads, validate_only=validate_only
            )
        except ContentFeatureDictionaryUpdateError as exc:
            raise ContentFeatureCandidateError(f"dictionary_update_failed:{exc}") from exc
    else:
        decisions = []
    committed = sum(
        1 for item in decisions if item.get("dictionary_write_status") == "succeeded"
    )
    write_status = "succeeded" if committed else "not_needed"
    details = _decision_details(rows, observation, decisions)
    result: dict[str, object] = {
        "ok": True,
        "status": "validated" if validate_only else "committed",
        "schema_version": RECEIPT_SCHEMA_VERSION,
        "candidate_file": candidate_path.relative_to(root).as_posix(),
        "candidate_sha256": candidate_sha256,
        "candidate_count": len(payloads),
        "dictionary_committed_count": committed,
        "dictionary_write_status": write_status,
        "decisions": details,
        "reused": False,
    }
    if not validate_only:
        _write_json(receipt_path, result)
    return result


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="提交内容规律分析的新特征入库决定")
    parser.add_argument("--workspace-root", required=True, type=Path)
    parser.add_argument("--candidate-file", required=True, type=Path)
    parser.add_argument("--observation-result-file", required=True, type=Path)
    parser.add_argument("--receipt-file", required=True, type=Path)
    parser.add_argument("--validate-only", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        result = commit_feature_candidates(
            args.workspace_root,
            candidate_file=args.candidate_file,
            observation_result_file=args.observation_result_file,
            receipt_file=args.receipt_file,
            validate_only=args.validate_only,
        )
    except (
        ContentFeatureCandidateError,
        ContentFeatureDictionaryUpdateError,
        OSError,
        UnicodeError,
        ValueError,
    ) as exc:
        print(
            json.dumps({"ok": False, "status": "failed", "error": str(exc)}, ensure_ascii=False),
            file=sys.stderr,
        )
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
