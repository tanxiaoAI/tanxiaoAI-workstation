#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import tempfile
from collections import Counter
from datetime import datetime
from pathlib import Path, PurePosixPath
from typing import Callable, Mapping

from csv安全写入 import CsvTransactionError, read_csv_table_with_snapshot, serialize_csv


class TopicAnalysisFinalizeError(RuntimeError):
    """Raised when a topic analysis cannot be finalized safely."""


SCHEMA_VERSION = "2"
TOPIC_LIBRARY_PATH = Path("我方资料") / "选题库.csv"
TOPIC_ANALYSIS_DIR = PurePosixPath("我方资料/选题分析")
TOPIC_ID_FIELD = "选题ID"
TOPIC_NAME_FIELD = "选题名称"
TOPIC_STATUS_FIELD = "当前状态"
TOPIC_RECOMMENDATION_FIELD = "是否建议做"
TOPIC_ANALYSIS_PATH_FIELD = "选题分析记录路径"
CONTENT_PRODUCTION_PATH_FIELD = "内容生产记录路径"
TOPIC_RECORDED_AT_FIELD = "灵感记录时间"
TOPIC_UPDATED_AT_FIELD = "更新时间"
TOPIC_ID_PATTERN = re.compile(r"^选题(?P<number>\d{3,})$")
ALLOWED_RECOMMENDATIONS = {"建议做", "暂缓", "不建议做"}
REQUIRED_ANALYSIS_FIELDS = (
    "主题",
    "目标用户",
    "核心痛点",
    "收益承诺",
    "核心命题",
    "切口",
)
OPTIONAL_METADATA_FIELDS = (
    "选题来源",
    "参考内容链接",
    "参考内容说明",
    "参考内容分析路径",
)
ALLOWED_METADATA_FIELDS = {
    TOPIC_ID_FIELD,
    *OPTIONAL_METADATA_FIELDS,
}
REQUIRED_MARKDOWN_SECTIONS = ("选题结论", "选题定义")
ReplaceOperation = Callable[[Path, Path], object]
PostCommitCheck = Callable[[], object]
Clock = Callable[[], datetime]


def _require_mapping(value: object, label: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise TopicAnalysisFinalizeError(f"invalid_metadata:{label}_must_be_object")
    return value


def _validate_metadata(metadata_document: object) -> dict[str, str]:
    root = _require_mapping(metadata_document, "metadata_document")
    if set(root) != {"schema_version", "metadata"}:
        missing = sorted({"schema_version", "metadata"} - set(root))
        unknown = sorted(set(root) - {"schema_version", "metadata"})
        raise TopicAnalysisFinalizeError(
            f"invalid_metadata:missing_keys={missing}:unknown_keys={unknown}"
        )
    if root["schema_version"] != SCHEMA_VERSION:
        raise TopicAnalysisFinalizeError(
            f"unsupported_schema_version:expected={SCHEMA_VERSION}:actual={root['schema_version']}"
        )

    raw_metadata = _require_mapping(root["metadata"], "metadata")
    if any(not isinstance(field, str) or not field.strip() for field in raw_metadata):
        raise TopicAnalysisFinalizeError(
            "invalid_metadata:field_names_must_be_non_empty_strings"
        )
    if any(not isinstance(value, str) for value in raw_metadata.values()):
        raise TopicAnalysisFinalizeError("invalid_metadata:field_values_must_be_strings")

    metadata = {str(field): str(value) for field, value in raw_metadata.items()}
    unknown_fields = sorted(set(metadata) - ALLOWED_METADATA_FIELDS)
    if unknown_fields:
        raise TopicAnalysisFinalizeError(f"unknown_metadata_fields:{unknown_fields}")
    blank_fields = sorted(
        field for field, value in metadata.items() if not value.strip()
    )
    if blank_fields:
        raise TopicAnalysisFinalizeError(
            f"invalid_metadata:blank_fields_must_be_omitted:{blank_fields}"
        )
    padded_fields = sorted(
        field for field, value in metadata.items() if value != value.strip()
    )
    if padded_fields:
        raise TopicAnalysisFinalizeError(
            f"invalid_metadata:outer_whitespace_not_allowed:{padded_fields}"
        )
    topic_id = metadata.get(TOPIC_ID_FIELD, "").strip()
    if not topic_id:
        raise TopicAnalysisFinalizeError("invalid_metadata:missing_topic_id")
    if TOPIC_ID_PATTERN.fullmatch(topic_id) is None:
        raise TopicAnalysisFinalizeError(f"invalid_topic_id:{topic_id}")
    return metadata


def _read_library(
    library_path: Path,
) -> tuple[list[str], list[dict[str, str]], bytes]:
    if not library_path.is_file():
        raise TopicAnalysisFinalizeError(f"topic_library_missing:{library_path}")
    try:
        fieldnames, rows, source_bytes = read_csv_table_with_snapshot(library_path)
    except CsvTransactionError as exc:
        raise TopicAnalysisFinalizeError(str(exc)) from exc

    required_fields = {
        TOPIC_ID_FIELD,
        TOPIC_NAME_FIELD,
        TOPIC_STATUS_FIELD,
        TOPIC_RECOMMENDATION_FIELD,
        TOPIC_ANALYSIS_PATH_FIELD,
        *REQUIRED_ANALYSIS_FIELDS,
    }
    missing_fields = sorted(required_fields - set(fieldnames))
    if missing_fields:
        raise TopicAnalysisFinalizeError(f"topic_library_missing_fields:{missing_fields}")
    return fieldnames, rows, source_bytes


def _validate_existing_topic_ids(rows: list[dict[str, str]]) -> list[int]:
    topic_ids = [row.get(TOPIC_ID_FIELD, "").strip() for row in rows]
    duplicates = sorted(
        topic_id
        for topic_id, count in Counter(
            topic_id for topic_id in topic_ids if topic_id
        ).items()
        if count > 1
    )
    if duplicates:
        raise TopicAnalysisFinalizeError(f"duplicate_topic_id:{duplicates}")

    numbers: list[int] = []
    for topic_id in topic_ids:
        if not topic_id:
            continue
        match = TOPIC_ID_PATTERN.fullmatch(topic_id)
        if match is None:
            raise TopicAnalysisFinalizeError(f"invalid_existing_topic_id:{topic_id}")
        numbers.append(int(match.group("number")))
    return numbers


def _expected_next_topic_id(existing_numbers: list[int]) -> str:
    return f"选题{(max(existing_numbers, default=0) + 1):03d}"


def _ensure_timestamp_fields(
    fieldnames: list[str],
    rows: list[dict[str, str]],
) -> tuple[list[str], list[dict[str, str]]]:
    prepared_fieldnames = list(fieldnames)
    for field in (TOPIC_RECORDED_AT_FIELD, TOPIC_UPDATED_AT_FIELD):
        if field not in prepared_fieldnames:
            prepared_fieldnames.append(field)
    prepared_rows = [dict(row) for row in rows]
    for row in prepared_rows:
        for field in (TOPIC_RECORDED_AT_FIELD, TOPIC_UPDATED_AT_FIELD):
            row.setdefault(field, "")
    return prepared_fieldnames, prepared_rows


def _current_timestamp(clock: Clock | None) -> str:
    current = clock() if clock is not None else datetime.now().astimezone()
    if not isinstance(current, datetime):
        raise TopicAnalysisFinalizeError("clock_must_return_datetime")
    return current.strftime("%Y-%m-%d %H:%M:%S")


def _prepare_row(
    fieldnames: list[str],
    rows: list[dict[str, str]],
    row_values: dict[str, str],
    operation: str,
    timestamp: str,
) -> tuple[list[dict[str, str]], dict[str, str], dict[str, str] | None]:
    unknown_fields = sorted(set(row_values) - set(fieldnames))
    if unknown_fields:
        raise TopicAnalysisFinalizeError(f"topic_library_missing_fields:{unknown_fields}")

    existing_numbers = _validate_existing_topic_ids(rows)
    topic_id = row_values[TOPIC_ID_FIELD]
    prepared_rows = [dict(row) for row in rows]

    if operation == "create":
        expected_topic_id = _expected_next_topic_id(existing_numbers)
        if topic_id != expected_topic_id:
            raise TopicAnalysisFinalizeError(
                f"expected_next_topic_id:{expected_topic_id}:actual={topic_id}"
            )
        prepared_row = {field: "" for field in fieldnames}
        prepared_row.update(row_values)
        prepared_row[TOPIC_STATUS_FIELD] = "已完成分析"
        prepared_row[TOPIC_RECORDED_AT_FIELD] = timestamp
        prepared_row[TOPIC_UPDATED_AT_FIELD] = timestamp
        prepared_rows.append(prepared_row)
        return prepared_rows, prepared_row, None

    matches = [
        index
        for index, row in enumerate(rows)
        if row.get(TOPIC_ID_FIELD) == topic_id
    ]
    if len(matches) != 1:
        raise TopicAnalysisFinalizeError(
            f"topic_id_match_count:topic_id={topic_id}:expected=1:actual={len(matches)}"
        )
    row_index = matches[0]
    previous_row = dict(prepared_rows[row_index])
    prepared_row = dict(previous_row)
    has_production = bool(
        prepared_row.get(CONTENT_PRODUCTION_PATH_FIELD, "").strip()
    )
    was_produced = prepared_row.get(TOPIC_STATUS_FIELD, "") == "已生产"
    prepared_row.update(row_values)
    prepared_row[TOPIC_STATUS_FIELD] = (
        "已生产" if has_production or was_produced else "已完成分析"
    )
    prepared_row[TOPIC_UPDATED_AT_FIELD] = timestamp
    prepared_rows[row_index] = prepared_row
    return prepared_rows, prepared_row, previous_row


def _sanitize_filename_component(value: str, *, limit: int = 60) -> str:
    cleaned = re.sub(r'[\\/:*?"<>|\x00-\x1f]', "", value)
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" .")
    return cleaned[:limit].rstrip(" .") or "选题"


def _validate_formal_relative_path(relative_path: str) -> PurePosixPath:
    if not relative_path or "\\" in relative_path:
        raise TopicAnalysisFinalizeError(
            f"analysis_path_invalid:{relative_path or '<empty>'}"
        )
    path = PurePosixPath(relative_path)
    if path.is_absolute() or ".." in path.parts or path.suffix.lower() != ".md":
        raise TopicAnalysisFinalizeError(f"analysis_path_invalid:{relative_path}")
    if path.parent != TOPIC_ANALYSIS_DIR:
        raise TopicAnalysisFinalizeError(f"analysis_path_outside_formal_dir:{relative_path}")
    return path


def _determine_formal_path(
    root: Path,
    prepared_row: dict[str, str],
    previous_row: dict[str, str] | None,
) -> tuple[str, Path]:
    previous_path = (
        previous_row.get(TOPIC_ANALYSIS_PATH_FIELD, "").strip()
        if previous_row
        else ""
    )
    if previous_path:
        relative = _validate_formal_relative_path(previous_path)
    else:
        safe_name = _sanitize_filename_component(prepared_row[TOPIC_NAME_FIELD])
        relative = TOPIC_ANALYSIS_DIR / (
            f"{prepared_row[TOPIC_ID_FIELD]}-{safe_name}.md"
        )

    target = root.joinpath(*relative.parts)
    current = root
    for part in TOPIC_ANALYSIS_DIR.parts:
        current = current / part
        if current.exists() and current.is_symlink():
            raise TopicAnalysisFinalizeError(
                f"analysis_directory_symlink_not_allowed:{current}"
            )
    formal_root = root.joinpath(*TOPIC_ANALYSIS_DIR.parts).resolve()
    resolved_target = target.resolve()
    try:
        formal_root.relative_to(root)
        resolved_target.relative_to(formal_root)
    except ValueError as exc:
        raise TopicAnalysisFinalizeError(
            f"analysis_path_outside_workspace:{relative.as_posix()}"
        ) from exc
    if target.is_symlink():
        raise TopicAnalysisFinalizeError(
            f"analysis_path_symlink_not_allowed:{relative.as_posix()}"
        )
    return relative.as_posix(), target


def _validate_prepared_row(prepared_row: dict[str, str]) -> None:
    required = (
        TOPIC_NAME_FIELD,
        TOPIC_STATUS_FIELD,
        TOPIC_RECOMMENDATION_FIELD,
        "判断理由",
        TOPIC_UPDATED_AT_FIELD,
        *REQUIRED_ANALYSIS_FIELDS,
    )
    missing = [field for field in required if not prepared_row.get(field, "").strip()]
    if missing:
        raise TopicAnalysisFinalizeError(f"formal_topic_fields_missing:{missing}")
    if prepared_row[TOPIC_STATUS_FIELD] not in {"已完成分析", "已生产"}:
        raise TopicAnalysisFinalizeError(
            f"formal_topic_status_invalid:{prepared_row[TOPIC_STATUS_FIELD]}"
        )
    if prepared_row[TOPIC_RECOMMENDATION_FIELD] not in ALLOWED_RECOMMENDATIONS:
        raise TopicAnalysisFinalizeError(
            "formal_topic_recommendation_invalid:"
            f"{prepared_row[TOPIC_RECOMMENDATION_FIELD]}"
        )


def _parse_markdown(markdown: str) -> tuple[bytes, dict[str, str]]:
    normalized = markdown.replace("\r\n", "\n").replace("\r", "\n").strip() + "\n"
    first_line = normalized.splitlines()[0] if normalized else ""
    title_match = re.fullmatch(r"# 选题分析：(\S.*?)", first_line)
    if title_match is None or title_match.group(1) != title_match.group(1).strip():
        raise TopicAnalysisFinalizeError(f"analysis_title_invalid:{first_line}")
    topic_name = title_match.group(1)
    for section in REQUIRED_MARKDOWN_SECTIONS:
        if len(re.findall(rf"(?m)^##\s+{re.escape(section)}\s*$", normalized)) != 1:
            raise TopicAnalysisFinalizeError(
                f"analysis_section_count_invalid:{section}"
            )
    recommendation_matches = re.findall(
        r"(?m)^-\s*是否建议做\s*[：:]\s*(建议做|暂缓|不建议做)\s*$",
        normalized,
    )
    if len(recommendation_matches) != 1:
        raise TopicAnalysisFinalizeError(
            f"analysis_recommendation_count_invalid:actual={len(recommendation_matches)}"
        )
    judgment_matches = re.findall(
        r"(?m)^-\s*核心判断\s*[：:]\s*(\S.*?)\s*$",
        normalized,
    )
    if len(judgment_matches) != 1:
        raise TopicAnalysisFinalizeError(
            f"analysis_judgment_count_invalid:actual={len(judgment_matches)}"
        )
    analysis_fields: dict[str, str] = {
        TOPIC_NAME_FIELD: topic_name,
        TOPIC_RECOMMENDATION_FIELD: recommendation_matches[0],
        "判断理由": judgment_matches[0],
    }
    for field in REQUIRED_ANALYSIS_FIELDS:
        matches = re.findall(
            rf"(?m)^\|\s*{re.escape(field)}(?:（[^\n|]*）)?\s*\|\s*([^|\n]+?)\s*\|\s*$",
            normalized,
        )
        if len(matches) != 1:
            raise TopicAnalysisFinalizeError(
                f"analysis_field_row_count_invalid:{field}:actual={len(matches)}"
            )
        value = matches[0].strip()
        if not value:
            raise TopicAnalysisFinalizeError(f"analysis_field_value_empty:{field}")
        analysis_fields[field] = value
    return normalized.encode("utf-8"), analysis_fields


def _stage_payload(target: Path, payload: bytes, suffix: str) -> Path:
    target.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{target.name}.", suffix=suffix, dir=str(target.parent)
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
    except Exception:
        temporary.unlink(missing_ok=True)
        raise
    return temporary


def _commit_payloads_atomically(
    payloads: Mapping[Path, bytes],
    *,
    expected_originals: Mapping[Path, bytes | None] | None = None,
    replace_operation: ReplaceOperation | None = None,
    post_commit_check: PostCommitCheck | None = None,
) -> None:
    operation = replace_operation or os.replace
    originals = {
        path: path.read_bytes() if path.exists() else None for path in payloads
    }
    for path, expected in (expected_originals or {}).items():
        if originals.get(path) != expected:
            raise TopicAnalysisFinalizeError(f"concurrent_source_change:{path}")

    staged: dict[Path, Path] = {}
    committed: list[Path] = []
    try:
        for target, payload in payloads.items():
            staged[target] = _stage_payload(target, payload, ".stage")
        for target, original in originals.items():
            current = target.read_bytes() if target.exists() else None
            if current != original:
                raise TopicAnalysisFinalizeError(f"concurrent_source_change:{target}")
        for target in payloads:
            operation(staged[target], target)
            committed.append(target)
        for target, payload in payloads.items():
            if target.read_bytes() != payload:
                raise TopicAnalysisFinalizeError(f"post_commit_mismatch:{target}")
        if post_commit_check is not None:
            post_commit_check()
    except Exception as exc:
        rollback_errors: list[str] = []
        for target in reversed(committed):
            restore: Path | None = None
            try:
                original = originals[target]
                if original is None:
                    target.unlink(missing_ok=True)
                else:
                    restore = _stage_payload(target, original, ".rollback")
                    os.replace(restore, target)
                    restore = None
            except Exception as rollback_exc:
                rollback_errors.append(f"{target}:{rollback_exc}")
            finally:
                if restore is not None:
                    restore.unlink(missing_ok=True)
        if rollback_errors:
            raise TopicAnalysisFinalizeError(
                f"commit_failed:{exc};rollback_failed:{';'.join(rollback_errors)}"
            ) from exc
        if isinstance(exc, TopicAnalysisFinalizeError):
            raise
        raise TopicAnalysisFinalizeError(
            f"commit_failed:{exc};rollback_completed"
        ) from exc
    finally:
        for temporary in staged.values():
            temporary.unlink(missing_ok=True)


def _verify_committed(
    library_path: Path,
    analysis_path: Path,
    expected_row: Mapping[str, str],
    expected_markdown: bytes,
) -> None:
    _, rows, _ = _read_library(library_path)
    topic_id = expected_row[TOPIC_ID_FIELD]
    matches = [row for row in rows if row.get(TOPIC_ID_FIELD) == topic_id]
    if len(matches) != 1:
        raise TopicAnalysisFinalizeError(
            f"post_write_topic_id_match_count:topic_id={topic_id}:actual={len(matches)}"
        )
    mismatched = [
        field
        for field, expected in expected_row.items()
        if matches[0].get(field, "") != expected
    ]
    if mismatched:
        raise TopicAnalysisFinalizeError(f"post_write_field_mismatch:{mismatched}")
    if not analysis_path.is_file() or analysis_path.read_bytes() != expected_markdown:
        raise TopicAnalysisFinalizeError(f"post_write_analysis_mismatch:{analysis_path}")


def finalize_topic_analysis(
    workspace_root: Path | str,
    metadata_document: object,
    analysis_markdown: str,
    operation: str,
    *,
    validate_only: bool = False,
    replace_operation: ReplaceOperation | None = None,
    clock: Clock | None = None,
) -> dict[str, object]:
    if operation not in {"create", "update"}:
        raise TopicAnalysisFinalizeError(f"unsupported_operation:{operation}")
    root = Path(workspace_root).expanduser().resolve()
    if not root.is_dir():
        raise TopicAnalysisFinalizeError(f"workspace_root_invalid:{root}")

    metadata = _validate_metadata(metadata_document)
    analysis_payload, analysis_fields = _parse_markdown(analysis_markdown)
    row_values = {**metadata, **analysis_fields}
    library_path = root / TOPIC_LIBRARY_PATH
    fieldnames, rows, library_snapshot = _read_library(library_path)
    fieldnames, rows = _ensure_timestamp_fields(fieldnames, rows)
    timestamp = _current_timestamp(clock)
    prepared_rows, prepared_row, previous_row = _prepare_row(
        fieldnames, rows, row_values, operation, timestamp
    )
    relative_analysis_path, analysis_path = _determine_formal_path(
        root, prepared_row, previous_row
    )
    prepared_row[TOPIC_ANALYSIS_PATH_FIELD] = relative_analysis_path
    prepared_index = (
        len(prepared_rows) - 1
        if operation == "create"
        else next(
            index
            for index, row in enumerate(prepared_rows)
            if row.get(TOPIC_ID_FIELD) == prepared_row[TOPIC_ID_FIELD]
        )
    )
    prepared_rows[prepared_index] = prepared_row
    _validate_prepared_row(prepared_row)
    library_payload = serialize_csv(fieldnames, prepared_rows)

    result = {
        "status": "planned" if validate_only else "committed",
        "operation": operation,
        "topic_id": prepared_row[TOPIC_ID_FIELD],
        "analysis_path": relative_analysis_path,
        "analysis_sha256": hashlib.sha256(analysis_payload).hexdigest(),
        "updated_at": prepared_row[TOPIC_UPDATED_AT_FIELD],
        "main_index_write_status": "not_started" if validate_only else "succeeded",
    }
    if validate_only:
        return result

    analysis_snapshot = analysis_path.read_bytes() if analysis_path.exists() else None
    _commit_payloads_atomically(
        {
            analysis_path: analysis_payload,
            library_path: library_payload,
        },
        expected_originals={
            analysis_path: analysis_snapshot,
            library_path: library_snapshot,
        },
        replace_operation=replace_operation,
        post_commit_check=lambda: _verify_committed(
            library_path,
            analysis_path,
            prepared_row,
            analysis_payload,
        ),
    )
    return result


def _resolve_temp_input(root: Path, input_path: Path, label: str) -> Path:
    resolved = input_path.expanduser().resolve()
    try:
        relative = resolved.relative_to(root)
    except ValueError as exc:
        raise TopicAnalysisFinalizeError(f"{label}_outside_workspace:{resolved}") from exc
    if (
        len(relative.parts) != 5
        or relative.parts[0] != ".meiling"
        or relative.parts[1] != "staging"
    ):
        raise TopicAnalysisFinalizeError(f"{label}_outside_run_directory:{relative}")
    if not resolved.is_file():
        raise TopicAnalysisFinalizeError(f"{label}_missing:{resolved}")
    return resolved


def _load_metadata(metadata_path: Path) -> object:
    try:
        return json.loads(metadata_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise TopicAnalysisFinalizeError(
            f"metadata_file_invalid:{metadata_path}:{exc}"
        ) from exc


def _load_markdown(analysis_path: Path) -> str:
    try:
        return analysis_path.read_text(encoding="utf-8-sig")
    except (OSError, UnicodeError) as exc:
        raise TopicAnalysisFinalizeError(
            f"analysis_candidate_invalid:{analysis_path}:{exc}"
        ) from exc


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="原子校验并写入选题分析正文和选题库记录"
    )
    parser.add_argument("--workspace-root", type=Path, required=True)
    parser.add_argument("--metadata-file", type=Path, required=True)
    parser.add_argument("--analysis-candidate", type=Path, required=True)
    parser.add_argument("--operation", choices=("create", "update"), required=True)
    parser.add_argument("--validate-only", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        workspace_root = args.workspace_root.expanduser().resolve()
        metadata_path = _resolve_temp_input(
            workspace_root, args.metadata_file, "metadata_file"
        )
        analysis_candidate = _resolve_temp_input(
            workspace_root, args.analysis_candidate, "analysis_candidate"
        )
        if metadata_path.parent != analysis_candidate.parent:
            raise TopicAnalysisFinalizeError("input_run_directory_mismatch")
        result = finalize_topic_analysis(
            workspace_root,
            _load_metadata(metadata_path),
            _load_markdown(analysis_candidate),
            args.operation,
            validate_only=args.validate_only,
        )
    except TopicAnalysisFinalizeError as exc:
        print(
            json.dumps({"status": "failed", "error": str(exc)}, ensure_ascii=False),
            file=sys.stderr,
        )
        return 1
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
