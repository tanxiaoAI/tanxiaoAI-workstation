#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import re
import sys
import tempfile
from datetime import datetime
from pathlib import Path, PurePosixPath
from typing import Sequence


class AccountEvidenceChunkError(RuntimeError):
    """Raised when account-analysis evidence cannot be chunked safely."""


ACCOUNT_LIBRARY_PATHS = {
    "benchmark": Path("对标资料/对标账号库.csv"),
    "self": Path("我方资料/我方账号库.csv"),
}
CONTENT_LIBRARY_PATHS = {
    "benchmark": Path("对标资料/对标内容库.csv"),
    "self": Path("我方资料/我方内容库.csv"),
}
# 对标侧和我方的正文级证据都直接读取「视频文案路径」的原文。
BENCHMARK_PRIMARY_PATH_FIELDS = {"视频文案路径": "原文正文"}
SELF_PATH_FIELDS = {"视频文案路径": "原文正文"}
INSTANCE_PATH = Path("沉淀结论/内容特征实例.csv")
INSTANCE_EVIDENCE_TYPE = "内容特征实例"
SUMMARY_REQUIRED_SECTIONS = ("分布与模式证据", "差异与反例", "代表性证据索引", "信息不足")
SUMMARY_BODY_PLACEHOLDERS = {
    "",
    "待填写",
    "未知",
    "暂无",
    "待补齐",
    "无",
    "none",
    "null",
    "已处理全部输入",
}
SUMMARY_CONTENT_LIMIT = 50
BENCHMARK_DETAIL_CONTENT_LIMIT = 20
STRUCTURED_DETAIL_FILE = "内容基础结构化数据.json"
SUMMARY_CONTEXT_FIELDS = ("标题", "点赞数", "发布时间")
# 详细分析模式只保留分析真正会用到的字段。封面图原链接、视频下载链接等纯网址
# 字段不参与分析，单条却可达数百字符，是证据体积的最大来源。
# 「核心互动数」「数据水平」虽是机器算出的派生字段，但内容层「用户吸引机制」
# 一节要用「数据水平」做高低数据反例、要用「核心互动数」比对量级，故保留；
# 两者只在详细样本上有值，成本可忽略。
DETAIL_CONTEXT_FIELDS = (
    "标题",
    "发布时间",
    "点赞数",
    "媒介形式",
    "收藏数",
    "分享数",
    "评论数",
    "核心互动数",
    "数据水平",
)
# 直读阈值：剥字段后的证据总字符数不超过此值时，不再切块、也不要求写分块摘要，
# 直接产出可读合并证据交给内容层。0 表示禁用直读、始终按分块链路处理。
DIRECT_READ_MAX_CHARS = 60000


def _read_csv(path: Path) -> tuple[list[str], list[dict[str, str]], bytes]:
    try:
        payload = path.read_bytes()
    except OSError as exc:
        raise AccountEvidenceChunkError(f"csv_read_failed:{path}:{exc}") from exc
    try:
        reader = csv.DictReader(payload.decode("utf-8-sig").splitlines())
        fields = list(reader.fieldnames or [])
        rows = [
            {str(key): str(value or "") for key, value in row.items() if key is not None}
            for row in reader
        ]
    except (UnicodeError, csv.Error) as exc:
        raise AccountEvidenceChunkError(f"csv_parse_failed:{path}:{exc}") from exc
    if not fields:
        raise AccountEvidenceChunkError(f"csv_header_missing:{path}")
    return fields, rows, payload


def _resolve_workspace_path(root: Path, raw_path: str, label: str) -> Path:
    normalized = str(raw_path or "").strip().replace("\\", "/")
    if not normalized:
        raise AccountEvidenceChunkError(f"{label}_missing")
    logical = PurePosixPath(normalized)
    candidate = Path(normalized).expanduser()
    resolved = candidate.resolve() if candidate.is_absolute() else root.joinpath(*logical.parts).resolve()
    try:
        resolved.relative_to(root)
    except ValueError as exc:
        raise AccountEvidenceChunkError(f"{label}_outside_workspace:{normalized}") from exc
    return resolved


def _resolve_output_dir(root: Path, raw_path: str) -> tuple[Path, str]:
    normalized = str(raw_path or "").strip().replace("\\", "/")
    logical = PurePosixPath(normalized)
    if (
        not normalized
        or logical.is_absolute()
        or ".." in logical.parts
        or not logical.parts
        or len(logical.parts) < 4
        or logical.parts[:2] != (".meiling", "staging")
    ):
        raise AccountEvidenceChunkError(f"output_dir_outside_workspace:{normalized}")
    resolved = root.joinpath(*logical.parts).resolve()
    try:
        resolved.relative_to(root)
    except ValueError as exc:
        raise AccountEvidenceChunkError(f"output_dir_outside_workspace:{normalized}") from exc
    return resolved, logical.as_posix()


def _json_text(payload: object) -> str:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True)


def _sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _publish_time_value(raw_value: str) -> float:
    text = str(raw_value or "").strip()
    if not text:
        return float("-inf")
    if text.isdigit():
        value = int(text)
        return float(value / 1000 if len(text) >= 13 else value)
    normalized = text.replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(normalized).timestamp()
    except ValueError:
        pass
    for pattern in (
        "%Y/%m/%d %H:%M:%S",
        "%Y/%m/%d %H:%M",
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d %H:%M",
    ):
        try:
            return datetime.strptime(text, pattern).timestamp()
        except ValueError:
            continue
    return float("-inf")


def _write_json_atomically(path: Path, payload: object) -> bytes:
    encoded = (_json_text(payload) + "\n").encode("utf-8")
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent)
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    except OSError as exc:
        temporary.unlink(missing_ok=True)
        raise AccountEvidenceChunkError(f"json_write_failed:{path}:{exc}") from exc
    return encoded


def _write_text_atomically(path: Path, text: str) -> bytes:
    encoded = text.encode("utf-8")
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent)
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    except OSError as exc:
        temporary.unlink(missing_ok=True)
        raise AccountEvidenceChunkError(f"text_write_failed:{path}:{exc}") from exc
    return encoded


def _chunk_payload(chunk_id: str, units: Sequence[dict[str, object]]) -> dict[str, object]:
    return {"schema_version": "1", "chunk_id": chunk_id, "evidence_units": list(units)}


def _split_text_unit(unit: dict[str, object], max_chars: int) -> list[dict[str, object]]:
    text = str(unit.get("正文", ""))
    if not text:
        raise AccountEvidenceChunkError(f"evidence_unit_exceeds_budget:{unit.get('内容ID', '')}")
    base = dict(unit)
    base.pop("正文", None)
    low, high = 1, len(text)
    best = 0
    while low <= high:
        middle = (low + high) // 2
        probe = dict(base)
        probe.update({"片段序号": 1, "片段总数": 1, "正文": text[:middle]})
        if len(_json_text(_chunk_payload("chunk-000", [probe]))) <= max_chars:
            best = middle
            low = middle + 1
        else:
            high = middle - 1
    if best <= 0:
        raise AccountEvidenceChunkError(f"evidence_unit_metadata_exceeds_budget:{unit.get('内容ID', '')}")
    parts = [text[index : index + best] for index in range(0, len(text), best)]
    result = []
    for index, part in enumerate(parts, start=1):
        item = dict(base)
        item.update({"片段序号": index, "片段总数": len(parts), "正文": part})
        result.append(item)
    return result


def _normalize_units(units: list[dict[str, object]], max_chars: int) -> list[dict[str, object]]:
    result: list[dict[str, object]] = []
    for unit in units:
        if len(_json_text(_chunk_payload("chunk-000", [unit]))) <= max_chars:
            result.append(unit)
        else:
            result.extend(_split_text_unit(unit, max_chars))
    return result


def _pack_chunks(
    units: list[dict[str, object]], max_items: int, max_chars: int
) -> list[dict[str, object]]:
    chunks: list[dict[str, object]] = []
    current: list[dict[str, object]] = []
    for unit in units:
        chunk_id = f"chunk-{len(chunks) + 1:03d}"
        candidate = [*current, unit]
        if current and (
            len(candidate) > max_items
            or len(_json_text(_chunk_payload(chunk_id, candidate))) > max_chars
        ):
            chunks.append(_chunk_payload(chunk_id, current))
            current = [unit]
        else:
            current = candidate
    if current:
        chunks.append(_chunk_payload(f"chunk-{len(chunks) + 1:03d}", current))
    return chunks


def _demote_headings(text: str, levels: int = 3) -> str:
    """把证据正文里行首的 markdown 标题整体降级。

    原文正文或历史摘要本身可能带 `#`／`##`；直接拼进直读证据会与交付文档
    自己的节标题混在同一层级，让读者分不清结构。降级后它们永远落在交付文档
    节标题之下。围栏代码块内的内容不动。（内容特征实例是表格，不含标题。）
    """
    lines: list[str] = []
    in_fence = False
    for line in text.splitlines():
        stripped = line.lstrip()
        if stripped.startswith("```") or stripped.startswith("~~~"):
            in_fence = not in_fence
            lines.append(line)
            continue
        if not in_fence and stripped.startswith("#"):
            indent = line[: len(line) - len(stripped)]
            marks = len(stripped) - len(stripped.lstrip("#"))
            if 1 <= marks <= 6:
                lines.append(
                    indent + "#" * min(marks + levels, 6) + stripped[marks:]
                )
                continue
        lines.append(line)
    return "\n".join(lines)


def _render_direct_evidence(
    *,
    account: dict[str, object],
    units: Sequence[dict[str, object]],
    total_content_count: int,
    content_ids: Sequence[str],
) -> str:
    """渲染未分块的直读证据：列表字段成表，正文级证据按内容分组。"""
    lines: list[str] = [
        "# 账号详细分析证据（未分块直读）",
        "",
        f"- 账号：{account.get('账号名', '')}（{account.get('平台', '')} / {account.get('账号ID', '')}）",
        f"- 内容总数：{total_content_count} 条",
        f"- 本轮详细样本：{len(content_ids)} 条",
        f"- 详细样本内容ID：{json.dumps(list(content_ids), ensure_ascii=False)}",
        f"- 证据单元：{len(units)} 个",
        "",
        "## 内容列表字段",
        "",
        "| 内容ID | " + " | ".join(DETAIL_CONTEXT_FIELDS) + " |",
        "| " + " | ".join(["---"] * (len(DETAIL_CONTEXT_FIELDS) + 1)) + " |",
    ]
    body_units: list[dict[str, object]] = []
    for unit in units:
        if unit.get("证据类型") != "内容列表字段":
            body_units.append(unit)
            continue
        fields = unit.get("字段")
        fields = fields if isinstance(fields, dict) else {}
        cells = [str(unit.get("内容ID", ""))]
        cells.extend(str(fields.get(key, "")) for key in DETAIL_CONTEXT_FIELDS)
        lines.append("| " + " | ".join(cells) + " |")
    if body_units:
        lines.extend(["", "## 正文级证据", ""])
        for unit in body_units:
            heading = f"### {unit.get('内容ID', '')} · {unit.get('证据类型', '')}"
            title = str(unit.get("标题", "")).strip()
            if title:
                heading += f" · {title}"
            lines.extend(
                [heading, "", _demote_headings(str(unit.get("正文", "")).strip()), ""]
            )
    return "\n".join(lines).rstrip() + "\n"


def _render_instance_evidence(rows: Sequence[dict[str, str]]) -> str:
    """把一条内容的有效特征实例渲染成 markdown 表，作为正文级证据。

    单元格里的 `|` 要换成全角，否则会把表格列切错。
    """
    columns = ("特征ID", "标准标签", "所属层级", "特征小类", "实例证据", "证据位置")
    lines = [
        "| " + " | ".join(columns) + " |",
        "| " + " | ".join(["---"] * len(columns)) + " |",
    ]
    for row in rows:
        cells = [str(row.get(key, "")).replace("|", "／").replace("\n", " ") for key in columns]
        lines.append("| " + " | ".join(cells) + " |")
    return "\n".join(lines)


def build_account_evidence_chunks(
    workspace_root: Path | str,
    *,
    account_type: str,
    account_id: str,
    platform: str = "",
    mode: str,
    output_dir: str,
    selected_content_ids: list[str] | None = None,
    max_items: int = 50,
    max_chars: int = 30000,
    direct_read_max_chars: int = DIRECT_READ_MAX_CHARS,
    now: str | None = None,
    reset_output_dir: bool = False,
) -> dict[str, object]:
    root = Path(workspace_root).expanduser().resolve()
    if not root.is_dir():
        raise AccountEvidenceChunkError(f"workspace_root_invalid:{root}")
    if account_type not in ACCOUNT_LIBRARY_PATHS:
        raise AccountEvidenceChunkError(f"account_type_invalid:{account_type}")
    if mode not in {"summary", "detail"}:
        raise AccountEvidenceChunkError(f"mode_invalid:{mode}")
    if max_items <= 0 or max_chars < 256:
        raise AccountEvidenceChunkError("chunk_budget_invalid")
    if direct_read_max_chars < 0:
        raise AccountEvidenceChunkError("direct_read_budget_invalid")
    normalized_account_id = str(account_id or "").strip()
    normalized_platform = str(platform or "").strip()
    if not normalized_account_id:
        raise AccountEvidenceChunkError("account_id_missing")
    target_dir, relative_dir = _resolve_output_dir(root, output_dir)
    if target_dir.exists() and any(target_dir.iterdir()):
        if not reset_output_dir:
            raise AccountEvidenceChunkError(f"output_dir_not_empty:{relative_dir}")
        # 显式重置：把旧分块目录整体改名归档（不删数据），后面会按当前内容重建，
        # 避免再靠人工搬文件绕过。
        archive_dir = target_dir.with_name(
            f"{target_dir.name}.superseded-{datetime.now().strftime('%Y%m%d-%H%M%S')}"
        )
        target_dir.rename(archive_dir)

    account_path = root / ACCOUNT_LIBRARY_PATHS[account_type]
    _, account_rows, account_bytes = _read_csv(account_path)
    matches = [
        row
        for row in account_rows
        if row.get("账号ID", "").strip() == normalized_account_id
        and (not normalized_platform or row.get("平台", "").strip() == normalized_platform)
    ]
    if len(matches) != 1:
        raise AccountEvidenceChunkError(
            f"account_match_count:expected=1:actual={len(matches)}:{normalized_account_id}"
        )
    account = dict(matches[0])
    content_list_path = _resolve_workspace_path(
        root, account.get("内容列表路径", ""), "content_list_path"
    )
    _, content_rows, content_bytes = _read_csv(content_list_path)
    detail_sorted_rows = sorted(
        content_rows,
        key=lambda row: (
            row.get("内容ID", ""), row.get("对外页面链接", ""), row.get("标题", "")
        ),
    )
    all_content_ids = [row.get("内容ID", "").strip() for row in content_rows]
    if any(not content_id for content_id in all_content_ids) or len(set(all_content_ids)) != len(all_content_ids):
        raise AccountEvidenceChunkError("content_identity_invalid_or_duplicate")
    total_content_count = len(content_rows)
    if selected_content_ids is not None:
        requested_ids = [str(value or "").strip() for value in selected_content_ids]
        if (
            mode != "detail"
            or not requested_ids
            or any(not value for value in requested_ids)
            or len(requested_ids) != len(set(requested_ids))
        ):
            raise AccountEvidenceChunkError("selected_content_ids_invalid")
        rows_by_id = {row.get("内容ID", "").strip(): row for row in content_rows}
        missing_ids = [content_id for content_id in requested_ids if content_id not in rows_by_id]
        if missing_ids:
            raise AccountEvidenceChunkError(
                "selected_content_ids_missing:" + ",".join(missing_ids)
            )
        sorted_rows = [rows_by_id[content_id] for content_id in requested_ids]
        selection_strategy = "explicit_content_ids"
    elif mode == "summary":
        if total_content_count < 1:
            raise AccountEvidenceChunkError("summary_content_count_below_minimum:minimum=1:actual=0")
        sorted_rows = sorted(
            content_rows,
            key=lambda row: _publish_time_value(row.get("发布时间", "")),
            reverse=True,
        )[:SUMMARY_CONTENT_LIMIT]
        selection_strategy = "latest_by_publish_time"
    elif account_type == "benchmark":
        sorted_rows = sorted(
            content_rows,
            key=lambda row: _publish_time_value(row.get("发布时间", "")),
            reverse=True,
        )[:BENCHMARK_DETAIL_CONTENT_LIMIT]
        selection_strategy = "latest_by_publish_time"
    else:
        sorted_rows = detail_sorted_rows
        selection_strategy = "all_available_content"
    content_ids = [row.get("内容ID", "").strip() for row in sorted_rows]
    base_context_rows = content_rows if mode == "detail" else sorted_rows
    base_context_ids = [row.get("内容ID", "").strip() for row in base_context_rows]
    detailed_content_ids = set(content_ids) if mode == "detail" else set()
    selected_video_content_count = sum(
        1 for row in sorted_rows if row.get("媒介形式", "").strip() == "视频"
    )

    units: list[dict[str, object]] = []
    source_hashes = {
        ACCOUNT_LIBRARY_PATHS[account_type].as_posix(): _sha256_bytes(account_bytes),
        content_list_path.relative_to(root).as_posix(): _sha256_bytes(content_bytes),
    }
    library_rows: list[dict[str, str]] = []
    library_by_content_id: dict[str, dict[str, str]] = {}
    if mode == "detail":
        content_library_path = root / CONTENT_LIBRARY_PATHS[account_type]
        _, all_library_rows, library_bytes = _read_csv(content_library_path)
        library_rows = [
            row
            for row in all_library_rows
            if row.get("账号ID", "").strip() == normalized_account_id
            and (
                not normalized_platform
                or row.get("平台", "").strip() == normalized_platform
            )
        ]
        for library_row in library_rows:
            library_content_id = library_row.get("内容ID", "").strip()
            if not library_content_id:
                continue
            if library_content_id in library_by_content_id:
                raise AccountEvidenceChunkError(
                    f"content_library_duplicate:{normalized_account_id}:{library_content_id}"
                )
            library_by_content_id[library_content_id] = library_row
        source_hashes[CONTENT_LIBRARY_PATHS[account_type].as_posix()] = _sha256_bytes(library_bytes)

    # 对标侧：内容特征实例（卡退役后的替代证据）。整表只读一次、按内容ID 分组，
    # 并计入 source_hashes —— 取证之后实例表若被改写，verify 会拦下来。
    instance_rows_by_content: dict[str, list[dict[str, str]]] = {}
    if mode == "detail" and account_type != "self":
        instance_path = root / INSTANCE_PATH
        if instance_path.is_file():
            _, all_instance_rows, instance_bytes = _read_csv(instance_path)
            source_hashes[INSTANCE_PATH.as_posix()] = _sha256_bytes(instance_bytes)
            for instance_row in all_instance_rows:
                if instance_row.get("当前有效", "").strip() != "是":
                    continue
                if instance_row.get("样本来源", "").strip() != "对标内容":
                    continue
                if instance_row.get("账号ID", "").strip() != normalized_account_id:
                    continue
                if (
                    normalized_platform
                    and instance_row.get("平台", "").strip() != normalized_platform
                ):
                    continue
                instance_content_id = instance_row.get("内容ID", "").strip()
                if instance_content_id:
                    instance_rows_by_content.setdefault(instance_content_id, []).append(instance_row)

    body_evidence_content_ids: set[str] = set()
    for list_row in base_context_rows:
        row = dict(list_row)
        library_row = library_by_content_id.get(list_row["内容ID"].strip())
        if library_row:
            row.update({key: value for key, value in library_row.items() if str(value).strip()})
        content_id = row["内容ID"].strip()
        if mode == "summary":
            # Summary AI only needs compact performance/title evidence. Keep
            # content ID on the machine-side unit for deduplication and
            # traceability, but do not expose account IDs or long URLs here.
            metadata = {
                key: row.get(key, "")
                for key in SUMMARY_CONTEXT_FIELDS
                if str(row.get(key, "")).strip()
            }
        else:
            metadata = {
                key: row.get(key, "")
                for key in DETAIL_CONTEXT_FIELDS
                if str(row.get(key, "")).strip()
            }
        units.append({"内容ID": content_id, "证据类型": "内容列表字段", "字段": metadata})
        if mode != "detail" or content_id not in detailed_content_ids:
            continue
        evidence_candidates = tuple(BENCHMARK_PRIMARY_PATH_FIELDS.items()) if account_type != "self" else tuple(SELF_PATH_FIELDS.items())
        for path_field, evidence_type in evidence_candidates:
            raw_path = row.get(path_field, "").strip()
            if not raw_path:
                continue
            evidence_path = _resolve_workspace_path(root, raw_path, path_field)
            try:
                evidence_bytes = evidence_path.read_bytes()
                evidence_text = evidence_bytes.decode("utf-8")
            except (OSError, UnicodeError) as exc:
                raise AccountEvidenceChunkError(
                    f"evidence_read_failed:{content_id}:{path_field}:{exc}"
                ) from exc
            source_hashes[evidence_path.relative_to(root).as_posix()] = _sha256_bytes(evidence_bytes)
            body_evidence_content_ids.add(content_id)
            units.append(
                {
                    "内容ID": content_id,
                    "标题": row.get("标题", ""),
                    "证据类型": evidence_type,
                    "证据路径": evidence_path.relative_to(root).as_posix(),
                    "正文": evidence_text,
                }
            )
            # 每条内容只取一份正文证据。
            break
        if account_type != "self":
            feature_rows = instance_rows_by_content.get(content_id)
            if feature_rows:
                units.append(
                    {
                        "内容ID": content_id,
                        "标题": row.get("标题", ""),
                        "证据类型": INSTANCE_EVIDENCE_TYPE,
                        "证据路径": INSTANCE_PATH.as_posix(),
                        "正文": _render_instance_evidence(feature_rows),
                    }
                )
        if account_type == "self" and row.get("媒介形式", "").strip() != "视频":
            raw_content_dir = row.get("内容目录路径", "").strip()
            if raw_content_dir:
                content_dir = _resolve_workspace_path(root, raw_content_dir, "内容目录路径")
                structured_path = content_dir / STRUCTURED_DETAIL_FILE
                try:
                    structured_bytes = structured_path.read_bytes()
                    structured = json.loads(structured_bytes.decode("utf-8"))
                except (OSError, UnicodeError, json.JSONDecodeError):
                    structured = {}
                    structured_bytes = b""
                body_text = (
                    str(structured.get("正文文本") or structured.get("body_text") or "").strip()
                    if isinstance(structured, dict)
                    else ""
                )
                if body_text:
                    relative_structured = structured_path.relative_to(root).as_posix()
                    source_hashes[relative_structured] = _sha256_bytes(structured_bytes)
                    body_evidence_content_ids.add(content_id)
                    units.append(
                        {
                            "内容ID": content_id,
                            "标题": row.get("标题", ""),
                            "证据类型": "公开正文",
                            "证据路径": relative_structured,
                            "正文": body_text,
                        }
                    )

    normalized_units = _normalize_units(units, max_chars)
    if mode == "summary":
        # Summary analysis is intentionally a single AI input. Sorting and
        # limiting happen on the machine; only compact list fields cross the
        # model boundary. Detailed analysis keeps the chunk workflow below.
        summary_rows = [
            {
                "标题": str(unit.get("字段", {}).get("标题") or "").strip(),
                "点赞数": str(unit.get("字段", {}).get("点赞数") or "").strip(),
                "发布时间": str(unit.get("字段", {}).get("发布时间") or "").strip(),
            }
            for unit in units
        ]
        direct_payload = {
            "schema_version": "1",
            "mode": "summary",
            "account": {
                "账号ID": account.get("账号ID", ""),
                "平台": account.get("平台", ""),
                "账号名": account.get("账号名", ""),
            },
            "items": summary_rows,
        }
        summary_input_path = target_dir / "summary_input.json"
        summary_input_bytes = _write_json_atomically(summary_input_path, direct_payload)
        summary_input_relative = summary_input_path.relative_to(root).as_posix()
        summary_input_sha256 = _sha256_bytes(summary_input_bytes)
        # Machine-side compatibility index for readers that enumerate evidence
        # units. It is never sent to the model and has no summary output.
        units_path = target_dir / "summary_units.json"
        units_payload = {
            "schema_version": "1",
            "mode": "summary",
            "evidence_units": [
                {
                    "内容ID": str(unit.get("内容ID") or ""),
                    "证据类型": "内容列表字段",
                    "字段": {
                        "标题": str(unit.get("字段", {}).get("标题") or ""),
                        "点赞数": str(unit.get("字段", {}).get("点赞数") or ""),
                        "发布时间": str(unit.get("字段", {}).get("发布时间") or ""),
                    },
                }
                for unit in units
            ],
        }
        units_bytes = _write_json_atomically(units_path, units_payload)
        units_relative = units_path.relative_to(root).as_posix()
        manifest = {
            "schema_version": "1",
            "generated_at": now or datetime.now().astimezone().isoformat(timespec="seconds"),
            "account_type": account_type,
            "mode": mode,
            "summary_input_mode": "direct",
            "summary_input_path": summary_input_relative,
            "summary_input_sha256": summary_input_sha256,
            "account": {
                "账号ID": account.get("账号ID", ""),
                "平台": account.get("平台", ""),
                "账号名": account.get("账号名", ""),
            },
            "selection_strategy": selection_strategy,
            "total_content_count": total_content_count,
            "selected_content_count": len(content_ids),
            "selected_video_content_count": selected_video_content_count,
            "summary_content_limit": SUMMARY_CONTENT_LIMIT,
            "base_context_content_count": len(base_context_ids),
            "expected_content_count": len(base_context_ids),
            "covered_content_count": len(covered_content_ids := [
                str(unit.get("内容ID", "")) for unit in normalized_units if unit.get("内容ID")
            ]),
            "evidence_unit_count": len(normalized_units),
            "coverage_complete": sorted(set(covered_content_ids)) == sorted(base_context_ids),
            "content_ids": content_ids,
            "source_sha256": source_hashes,
            "chunks": [{
                "chunk_id": "summary-input",
                "path": units_relative,
                "input_sha256": _sha256_bytes(units_bytes),
                "item_count": len(units),
                "content_ids": content_ids,
            }],
        }
        manifest_path = target_dir / "chunk_manifest.json"
        manifest_bytes = _write_json_atomically(manifest_path, manifest)
        return {
            "ok": True,
            "status": "committed",
            "manifest_path": manifest_path.relative_to(root).as_posix(),
            "summary_input_path": summary_input_relative,
            "summary_input_sha256": summary_input_sha256,
            "manifest_sha256": _sha256_bytes(manifest_bytes),
            "chunk_count": 1,
            "expected_content_count": len(base_context_ids),
            "base_context_content_count": len(base_context_ids),
            "total_content_count": total_content_count,
            "selected_content_count": len(content_ids),
            "selected_video_content_count": selected_video_content_count,
            "selection_strategy": selection_strategy,
            "evidence_unit_count": len(normalized_units),
            "coverage_complete": bool(manifest["coverage_complete"]),
            "total_input_sha256": _sha256_bytes(_json_text(summary_rows).encode("utf-8")),
            "evidence_scope": f"账号共 {total_content_count} 条，本次使用最近 {len(content_ids)} 条。",
        }
    total_units_chars = len(_json_text(normalized_units))
    if (
        mode == "detail"
        and direct_read_max_chars > 0
        and total_units_chars <= direct_read_max_chars
    ):
        # 证据量在阈值以内时不分块，直接产出可读的合并证据，省掉逐块生成摘要
        # 的模型调用。交付形态与分块路径保持一致（两种路径都由 verify 产出
        # 「已验证账号诊断证据摘要.md」），下游流程无需区分。
        target_dir.mkdir(parents=True, exist_ok=True)
        direct_path = target_dir / "detail_direct_evidence.md"
        direct_bytes = _write_text_atomically(
            direct_path,
            _render_direct_evidence(
                account=account,
                units=normalized_units,
                total_content_count=total_content_count,
                content_ids=content_ids,
            ),
        )
        direct_relative = direct_path.relative_to(root).as_posix()
        direct_sha256 = _sha256_bytes(direct_bytes)
        direct_covered_ids = sorted(
            {str(unit.get("内容ID", "")) for unit in normalized_units if unit.get("内容ID")}
        )
        direct_input_sha256 = hashlib.sha256(
            _json_text(normalized_units).encode("utf-8")
        ).hexdigest()
        manifest = {
            "schema_version": "1",
            "generated_at": now or datetime.now().astimezone().isoformat(timespec="seconds"),
            "account_type": account_type,
            "mode": mode,
            "detail_delivery_mode": "direct",
            "direct_evidence_path": direct_relative,
            "direct_evidence_sha256": direct_sha256,
            "direct_evidence_char_count": total_units_chars,
            "direct_read_max_chars": direct_read_max_chars,
            "account": {
                "账号ID": account.get("账号ID", ""),
                "平台": account.get("平台", ""),
                "账号名": account.get("账号名", ""),
            },
            "selection_strategy": selection_strategy,
            "total_content_count": total_content_count,
            "selected_content_count": len(content_ids),
            "selected_video_content_count": selected_video_content_count,
            "summary_content_limit": None,
            "max_items_per_batch": max_items,
            "max_chars_per_batch": max_chars,
            "base_context_content_count": len(base_context_ids),
            "expected_content_count": len(base_context_ids),
            "covered_content_count": len(direct_covered_ids),
            "evidence_unit_count": len(normalized_units),
            "coverage_complete": direct_covered_ids == sorted(base_context_ids),
            "total_input_sha256": direct_input_sha256,
            "source_sha256": source_hashes,
            "detail_content_limit": (
                BENCHMARK_DETAIL_CONTENT_LIMIT if account_type == "benchmark" else None
            ),
            "detailed_content_ids": content_ids,
            "selected_body_evidence_content_count": len(body_evidence_content_ids),
            "selected_basic_only_content_count": len(content_ids)
            - len(body_evidence_content_ids),
            "chunks": [],
        }
        if not manifest["coverage_complete"]:
            raise AccountEvidenceChunkError("coverage_incomplete")
        manifest_path = target_dir / "chunk_manifest.json"
        manifest_bytes = _write_json_atomically(manifest_path, manifest)
        return {
            "ok": True,
            "status": "committed",
            "detail_delivery_mode": "direct",
            "manifest_path": manifest_path.relative_to(root).as_posix(),
            "manifest_sha256": _sha256_bytes(manifest_bytes),
            "chunk_count": 0,
            "direct_evidence_path": direct_relative,
            "direct_evidence_sha256": direct_sha256,
            "direct_evidence_char_count": total_units_chars,
            "expected_content_count": len(base_context_ids),
            "base_context_content_count": len(base_context_ids),
            "total_content_count": total_content_count,
            "selected_content_count": len(content_ids),
            "selected_video_content_count": selected_video_content_count,
            "selection_strategy": selection_strategy,
            "evidence_unit_count": len(normalized_units),
            "coverage_complete": True,
            "total_input_sha256": direct_input_sha256,
        }
    chunks = _pack_chunks(normalized_units, max_items, max_chars)
    target_dir.mkdir(parents=True, exist_ok=True)
    chunk_records = []
    for chunk in chunks:
        chunk_id = str(chunk["chunk_id"])
        chunk_path = target_dir / f"{chunk_id}.json"
        encoded = _write_json_atomically(chunk_path, chunk)
        chunk_records.append(
            {
                "chunk_id": chunk_id,
                "path": chunk_path.relative_to(root).as_posix(),
                "summary_path": (target_dir / "摘要" / f"{chunk_id}.md")
                .relative_to(root)
                .as_posix(),
                "input_sha256": _sha256_bytes(encoded),
                "item_count": len(chunk["evidence_units"]),
                "char_count": len(_json_text(chunk)),
                "content_ids": sorted(
                    {str(unit.get("内容ID", "")) for unit in chunk["evidence_units"]}
                ),
            }
        )

    covered_content_ids = sorted(
        {str(unit.get("内容ID", "")) for unit in normalized_units if unit.get("内容ID")}
    )
    total_input_sha256 = hashlib.sha256(_json_text(normalized_units).encode("utf-8")).hexdigest()
    manifest = {
        "schema_version": "1",
        "generated_at": now or datetime.now().astimezone().isoformat(timespec="seconds"),
        "account_type": account_type,
        "mode": mode,
        "account": {
            "账号ID": account.get("账号ID", ""),
            "平台": account.get("平台", ""),
            "账号名": account.get("账号名", ""),
        },
        "selection_strategy": selection_strategy,
        "total_content_count": total_content_count,
        "selected_content_count": len(content_ids),
        "selected_video_content_count": selected_video_content_count,
        "summary_content_limit": SUMMARY_CONTENT_LIMIT if mode == "summary" else None,
        "max_items_per_batch": max_items,
        "max_chars_per_batch": max_chars,
        "base_context_content_count": len(base_context_ids),
        "expected_content_count": len(base_context_ids),
        "covered_content_count": len(covered_content_ids),
        "evidence_unit_count": len(normalized_units),
        "coverage_complete": covered_content_ids == sorted(base_context_ids),
        "total_input_sha256": total_input_sha256,
        "source_sha256": source_hashes,
        "chunks": chunk_records,
    }
    if mode == "detail":
        manifest.update(
            {
                "detail_content_limit": (
                    BENCHMARK_DETAIL_CONTENT_LIMIT if account_type == "benchmark" else None
                ),
                "detailed_content_ids": content_ids,
                "selected_body_evidence_content_count": len(body_evidence_content_ids),
                "selected_basic_only_content_count": len(content_ids)
                - len(body_evidence_content_ids),
            }
        )
    if not manifest["coverage_complete"]:
        raise AccountEvidenceChunkError("coverage_incomplete")
    manifest_path = target_dir / "chunk_manifest.json"
    manifest_bytes = _write_json_atomically(manifest_path, manifest)
    result = {
        "ok": True,
        "status": "committed",
        "manifest_path": manifest_path.relative_to(root).as_posix(),
        "manifest_sha256": _sha256_bytes(manifest_bytes),
        "chunk_count": len(chunks),
        "expected_content_count": len(base_context_ids),
        "base_context_content_count": len(base_context_ids),
        "total_content_count": total_content_count,
        "selected_content_count": len(content_ids),
        "selected_video_content_count": selected_video_content_count,
        "selection_strategy": selection_strategy,
        "evidence_unit_count": len(normalized_units),
        "coverage_complete": True,
        "total_input_sha256": total_input_sha256,
    }
    if mode == "summary":
        result["evidence_scope"] = (
            f"账号共 {total_content_count} 条，本次使用最近 {len(content_ids)} 条。"
        )
    return result


def _validate_summary_body(text: str, chunk_id: str) -> None:
    sections: dict[str, list[str]] = {}
    current_section = ""
    for line in text.splitlines():
        match = re.match(r"^\s*##\s+(.+?)\s*$", line)
        if match:
            current_section = match.group(1).strip()
            sections.setdefault(current_section, [])
            continue
        if current_section:
            sections[current_section].append(line)

    missing = [name for name in SUMMARY_REQUIRED_SECTIONS if name not in sections]
    if missing:
        raise AccountEvidenceChunkError(
            f"summary_body_invalid:{chunk_id}:missing_sections:{','.join(missing)}"
        )

    meaningful_lines: list[str] = []
    for line in sections["分布与模式证据"]:
        normalized = re.sub(r"^[\s\-*\d.]+", "", line).strip().rstrip("。")
        if normalized.lower() not in SUMMARY_BODY_PLACEHOLDERS:
            meaningful_lines.append(normalized)
    if not any(meaningful_lines):
        raise AccountEvidenceChunkError(
            f"summary_body_invalid:{chunk_id}:distribution_evidence_empty"
        )


def verify_account_evidence_sources(
    workspace_root: Path | str, manifest: dict[str, object]
) -> None:
    root = Path(workspace_root).expanduser().resolve()
    source_hashes = manifest.get("source_sha256")
    if not isinstance(source_hashes, dict) or not source_hashes:
        raise AccountEvidenceChunkError("manifest_source_hashes_invalid")
    for raw_path, expected_hash in source_hashes.items():
        if not isinstance(raw_path, str) or not isinstance(expected_hash, str) or not expected_hash:
            raise AccountEvidenceChunkError("manifest_source_hashes_invalid")
        source_path = _resolve_workspace_path(root, raw_path, "source_path")
        try:
            current_hash = _sha256_bytes(source_path.read_bytes())
        except OSError as exc:
            raise AccountEvidenceChunkError(f"source_read_failed:{raw_path}:{exc}") from exc
        if current_hash != expected_hash:
            raise AccountEvidenceChunkError(f"source_hash_mismatch:{raw_path}")


def _verify_direct_evidence(
    *,
    root: Path,
    manifest: dict[str, object],
    manifest_path: Path,
    target_dir: Path,
) -> dict[str, object]:
    """校验未分块的直读证据，并产出与分块路径同名的已验证交付物。"""
    direct_path = _resolve_workspace_path(
        root, str(manifest.get("direct_evidence_path") or ""), "direct_evidence_path"
    )
    try:
        direct_bytes = direct_path.read_bytes()
        direct_text = direct_bytes.decode("utf-8").strip()
    except (OSError, UnicodeError) as exc:
        raise AccountEvidenceChunkError(f"direct_evidence_read_failed:{exc}") from exc
    expected_sha256 = str(manifest.get("direct_evidence_sha256") or "")
    if not expected_sha256 or _sha256_bytes(direct_bytes) != expected_sha256:
        raise AccountEvidenceChunkError("direct_evidence_hash_mismatch")
    if not direct_text:
        raise AccountEvidenceChunkError("direct_evidence_empty")
    total_output_sha256 = _sha256_bytes(
        _json_text([{"delivery_mode": "direct", "sha256": expected_sha256}]).encode("utf-8")
    )
    manifest["verified_chunk_count"] = 0
    manifest["verified_delivery_mode"] = "direct"
    manifest["total_output_sha256"] = total_output_sha256
    manifest_bytes = _write_json_atomically(manifest_path, manifest)
    output_path = target_dir / "已验证账号诊断证据摘要.md"
    body_lines = [
        "# 已验证账号诊断证据摘要",
        "",
        "- delivery_mode：direct",
        f"- source_evidence_path：{direct_path.relative_to(root).as_posix()}",
        f"- source_sha256：{expected_sha256}",
        "- source_verified：true",
        "",
        direct_text,
    ]
    encoded = _write_text_atomically(output_path, "\n".join(body_lines).rstrip() + "\n")
    return {
        "ok": True,
        "status": "verified",
        "detail_delivery_mode": "direct",
        "verified_chunk_count": 0,
        "verified_evidence_path": output_path.relative_to(root).as_posix(),
        "verified_evidence_sha256": _sha256_bytes(encoded),
        "manifest_sha256": _sha256_bytes(manifest_bytes),
        "total_output_sha256": total_output_sha256,
    }


def verify_account_evidence_summaries(
    workspace_root: Path | str, *, output_dir: str
) -> dict[str, object]:
    root = Path(workspace_root).expanduser().resolve()
    if not root.is_dir():
        raise AccountEvidenceChunkError(f"workspace_root_invalid:{root}")
    target_dir, _ = _resolve_output_dir(root, output_dir)
    manifest_path = target_dir / "chunk_manifest.json"
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise AccountEvidenceChunkError(f"manifest_read_failed:{manifest_path}:{exc}") from exc
    if manifest.get("detail_delivery_mode") == "direct":
        return _verify_direct_evidence(
            root=root,
            manifest=manifest,
            manifest_path=manifest_path,
            target_dir=target_dir,
        )
    chunks = manifest.get("chunks")
    if not isinstance(chunks, list) or not chunks:
        raise AccountEvidenceChunkError("manifest_chunks_invalid")

    verified_summaries: list[tuple[str, str, dict[str, object]]] = []
    updated_chunks: list[dict[str, object]] = []
    output_fingerprints: list[dict[str, str]] = []
    seen_chunk_ids: set[str] = set()
    for record in chunks:
        if not isinstance(record, dict):
            raise AccountEvidenceChunkError("manifest_chunk_invalid")
        chunk_id = str(record.get("chunk_id", "")).strip()
        if not chunk_id or chunk_id in seen_chunk_ids:
            raise AccountEvidenceChunkError(f"manifest_chunk_id_invalid:{chunk_id}")
        seen_chunk_ids.add(chunk_id)
        chunk_path = _resolve_workspace_path(root, str(record.get("path", "")), "chunk_path")
        summary_path = _resolve_workspace_path(
            root, str(record.get("summary_path", "")), "summary_path"
        )
        try:
            chunk_bytes = chunk_path.read_bytes()
            summary_bytes = summary_path.read_bytes()
            summary_text = summary_bytes.decode("utf-8").strip()
        except (OSError, UnicodeError) as exc:
            raise AccountEvidenceChunkError(f"summary_read_failed:{chunk_id}:{exc}") from exc
        expected_hash = str(record.get("input_sha256", "")).strip()
        if not expected_hash or _sha256_bytes(chunk_bytes) != expected_hash:
            raise AccountEvidenceChunkError(f"chunk_hash_mismatch:{chunk_id}")

        _validate_summary_body(summary_text, chunk_id)
        output_sha256 = _sha256_bytes(summary_bytes)
        updated_record = dict(record)
        updated_record["output_sha256"] = output_sha256
        updated_chunks.append(updated_record)
        output_fingerprints.append(
            {"chunk_id": chunk_id, "output_sha256": output_sha256}
        )
        verified_summaries.append(
            (
                chunk_id,
                summary_text,
                {
                    "source_chunk_id": chunk_id,
                    "input_sha256": expected_hash,
                    "expected_unit_count": int(record.get("item_count", 0)),
                    "covered_content_ids": [
                        str(value) for value in record.get("content_ids", [])
                    ],
                    "output_sha256": output_sha256,
                },
            )
        )

    total_output_sha256 = _sha256_bytes(
        _json_text(output_fingerprints).encode("utf-8")
    )
    manifest["chunks"] = updated_chunks
    manifest["verified_chunk_count"] = len(verified_summaries)
    manifest["total_output_sha256"] = total_output_sha256
    manifest["coverage_complete"] = bool(manifest.get("coverage_complete")) and (
        len(verified_summaries) == len(chunks)
    )
    manifest_bytes = _write_json_atomically(manifest_path, manifest)

    output_path = target_dir / "已验证账号诊断证据摘要.md"
    sections = ["# 已验证账号诊断证据摘要"]
    for chunk_id, summary_text, metadata in verified_summaries:
        sections.extend(
            [
                "",
                f"## {chunk_id}",
                "",
                f"- source_chunk_id：{metadata['source_chunk_id']}",
                f"- input_sha256：{metadata['input_sha256']}",
                f"- expected_unit_count：{metadata['expected_unit_count']}",
                "- covered_content_ids："
                + json.dumps(metadata["covered_content_ids"], ensure_ascii=False),
                f"- output_sha256：{metadata['output_sha256']}",
                "- source_verified：true",
                "",
                summary_text,
            ]
        )
    encoded = _write_text_atomically(output_path, "\n".join(sections).rstrip() + "\n")
    return {
        "ok": True,
        "status": "verified",
        "verified_chunk_count": len(verified_summaries),
        "verified_evidence_path": output_path.relative_to(root).as_posix(),
        "verified_evidence_sha256": _sha256_bytes(encoded),
        "manifest_sha256": _sha256_bytes(manifest_bytes),
        "total_output_sha256": total_output_sha256,
    }


def verify_account_summary_input(
    workspace_root: Path | str, manifest: dict[str, object]
) -> dict[str, object]:
    """Verify the direct summary input without requiring model-generated chunks."""
    root = Path(workspace_root).expanduser().resolve()
    if manifest.get("mode") != "summary" or manifest.get("summary_input_mode") != "direct":
        raise AccountEvidenceChunkError("summary_input_mode_invalid")
    raw_path = str(manifest.get("summary_input_path") or "")
    input_path = _resolve_workspace_path(root, raw_path, "summary_input_path")
    try:
        encoded = input_path.read_bytes()
        payload = json.loads(encoded.decode("utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise AccountEvidenceChunkError(f"summary_input_read_failed:{exc}") from exc
    if _sha256_bytes(encoded) != str(manifest.get("summary_input_sha256") or ""):
        raise AccountEvidenceChunkError("summary_input_hash_mismatch")
    items = payload.get("items") if isinstance(payload, dict) else None
    if (
        not isinstance(items, list)
        or not items
        or len(items) > SUMMARY_CONTENT_LIMIT
        or len(items) != int(manifest.get("selected_content_count") or 0)
    ):
        raise AccountEvidenceChunkError("summary_input_items_invalid")
    if any(
        not isinstance(item, dict)
        or set(item) != {"标题", "点赞数", "发布时间"}
        for item in items
    ):
        raise AccountEvidenceChunkError("summary_input_fields_invalid")
    return {
        "ok": True,
        "status": "verified",
        "summary_input_path": input_path.relative_to(root).as_posix(),
        "summary_input_sha256": _sha256_bytes(encoded),
        "item_count": len(items),
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="为账号概要或详细分析生成确定性证据分块")
    parser.add_argument("--phase", choices=("build", "verify"), default="build")
    parser.add_argument("--workspace-root", type=Path, required=True)
    parser.add_argument("--account-type", choices=sorted(ACCOUNT_LIBRARY_PATHS))
    parser.add_argument("--account-id")
    parser.add_argument("--platform", default="")
    parser.add_argument("--mode", choices=("summary", "detail"))
    parser.add_argument("--output-dir", required=True)
    parser.add_argument(
        "--selected-content-ids",
        default="",
        help="详细分析的已确认内容ID，逗号分隔；未传时对标账号默认取最新20条",
    )
    parser.add_argument("--max-items", type=int, default=50)
    parser.add_argument("--max-chars", type=int, default=30000)
    parser.add_argument(
        "--direct-read-max-chars",
        type=int,
        default=DIRECT_READ_MAX_CHARS,
        help="详细分析证据不再分块、直接交付的字符阈值；0 表示禁用直读、始终分块",
    )
    parser.add_argument(
        "--reset-output-dir",
        action="store_true",
        help="输出目录非空时先把旧分块改名归档再重建；不加时保持原来的报错行为",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.phase == "verify":
            result = verify_account_evidence_summaries(
                args.workspace_root, output_dir=args.output_dir
            )
        else:
            if not args.account_type or not args.account_id or not args.mode:
                raise AccountEvidenceChunkError(
                    "build_arguments_missing:account_type,account_id,mode"
                )
            result = build_account_evidence_chunks(
                args.workspace_root,
                account_type=args.account_type,
                account_id=args.account_id,
                platform=args.platform,
                mode=args.mode,
                output_dir=args.output_dir,
                selected_content_ids=(
                    [value.strip() for value in args.selected_content_ids.split(",") if value.strip()]
                    if args.selected_content_ids.strip()
                    else None
                ),
                max_items=args.max_items,
                max_chars=args.max_chars,
                direct_read_max_chars=args.direct_read_max_chars,
                reset_output_dir=args.reset_output_dir,
            )
    except AccountEvidenceChunkError as exc:
        print(json.dumps({"ok": False, "status": "failed", "error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
