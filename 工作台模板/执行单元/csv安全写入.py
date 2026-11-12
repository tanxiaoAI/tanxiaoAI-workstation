#!/usr/bin/env python3
from __future__ import annotations

import csv
import io
import os
import tempfile
from pathlib import Path
from typing import Callable, Iterable, Mapping, MutableMapping, Sequence


class CsvTransactionError(RuntimeError):
    """Raised when a staged CSV transaction cannot commit cleanly."""


CsvRows = Sequence[Mapping[str, object]]
CsvWrite = tuple[Path, Sequence[str], CsvRows]
ReplaceOperation = Callable[[Path, Path], object]


def _validate_fieldnames(csv_path: Path, fieldnames: Sequence[str]) -> list[str]:
    normalized = [str(field or "").strip() for field in fieldnames]
    if not normalized:
        raise CsvTransactionError(f"CSV missing header: {csv_path}")
    if any(not field for field in normalized):
        raise CsvTransactionError(f"CSV header contains empty field: {csv_path}")
    if len(set(normalized)) != len(normalized):
        raise CsvTransactionError(f"CSV header contains duplicate fields: {csv_path}")
    return normalized


def _parse_csv_payload(
    csv_path: Path, payload: bytes
) -> tuple[list[str], list[dict[str, str]]]:
    try:
        with io.StringIO(payload.decode("utf-8-sig"), newline="") as handle:
            reader = csv.reader(handle)
            try:
                raw_fieldnames = next(reader)
            except StopIteration as exc:
                raise CsvTransactionError(f"CSV missing header: {csv_path}") from exc
            fieldnames = _validate_fieldnames(csv_path, raw_fieldnames)
            rows: list[dict[str, str]] = []
            for record_number, record in enumerate(reader, start=2):
                if not record:
                    continue
                if len(record) != len(fieldnames):
                    raise CsvTransactionError(
                        "csv_row_width_mismatch:"
                        f"{csv_path}:record={record_number}:"
                        f"expected={len(fieldnames)}:actual={len(record)}"
                    )
                rows.append(dict(zip(fieldnames, record)))
            return fieldnames, rows
    except CsvTransactionError:
        raise
    except (OSError, UnicodeError, csv.Error) as exc:
        raise CsvTransactionError(f"CSV read failed: {csv_path}: {exc}") from exc


def read_csv_table_with_snapshot(
    csv_path: Path,
) -> tuple[list[str], list[dict[str, str]], bytes]:
    normalized_path = Path(csv_path)
    try:
        payload = normalized_path.read_bytes()
    except OSError as exc:
        raise CsvTransactionError(f"CSV read failed: {normalized_path}: {exc}") from exc
    fieldnames, rows = _parse_csv_payload(normalized_path, payload)
    return fieldnames, rows, payload


def read_csv_table(csv_path: Path) -> tuple[list[str], list[dict[str, str]]]:
    fieldnames, rows, _ = read_csv_table_with_snapshot(csv_path)
    return fieldnames, rows


def serialize_csv(fieldnames: Sequence[str], rows: CsvRows) -> bytes:
    output = io.StringIO(newline="")
    normalized_fieldnames = _validate_fieldnames(Path("<memory>"), fieldnames)
    writer = csv.DictWriter(output, fieldnames=normalized_fieldnames, lineterminator="\n")
    writer.writeheader()
    for row in rows:
        writer.writerow({field: row.get(field, "") for field in normalized_fieldnames})
    return output.getvalue().encode("utf-8-sig")


def replace_file(source: Path, target: Path) -> None:
    os.replace(source, target)


def _stage_bytes(target: Path, payload: bytes, *, suffix: str) -> Path:
    file_descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{target.name}.",
        suffix=suffix,
        dir=str(target.parent),
    )
    temporary_path = Path(temporary_name)
    try:
        with os.fdopen(file_descriptor, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
    except Exception:
        temporary_path.unlink(missing_ok=True)
        raise
    return temporary_path


def _cleanup_paths(paths: Iterable[Path | None]) -> None:
    for path in paths:
        if path is not None:
            path.unlink(missing_ok=True)


def write_csv_tables_atomically(
    writes: Sequence[CsvWrite],
    *,
    replace_operation: ReplaceOperation | None = None,
    expected_originals: Mapping[Path, bytes | None] | None = None,
) -> None:
    operation = replace_operation or replace_file
    prepared: list[dict[str, object]] = []
    seen_targets: set[Path] = set()

    try:
        for raw_target, fieldnames, rows in writes:
            target = Path(raw_target)
            if target in seen_targets:
                raise ValueError(f"duplicate CSV transaction target: {target}")
            seen_targets.add(target)
            existed = target.exists()
            original = target.read_bytes() if existed else None
            if expected_originals is not None and target in expected_originals:
                expected = expected_originals[target]
                if original != expected:
                    raise CsvTransactionError(f"concurrent source change: {target}")
            prepared.append(
                {
                    "target": target,
                    "payload": serialize_csv(fieldnames, rows),
                    "existed": existed,
                    "original": original,
                    "stage": None,
                }
            )
    except Exception as exc:
        raise CsvTransactionError(f"CSV transaction serialization failed: {exc}") from exc

    try:
        for item in prepared:
            target = item["target"]
            payload = item["payload"]
            assert isinstance(target, Path)
            assert isinstance(payload, bytes)
            item["stage"] = _stage_bytes(target, payload, suffix=".stage")
    except Exception as exc:
        _cleanup_paths(item.get("stage") for item in prepared)
        raise CsvTransactionError(f"CSV transaction staging failed: {exc}") from exc

    try:
        for item in prepared:
            target = item["target"]
            assert isinstance(target, Path)
            existed = bool(item["existed"])
            if target.exists() != existed:
                raise CsvTransactionError(f"concurrent source change: {target}")
            if existed:
                original = item["original"]
                assert isinstance(original, bytes)
                if target.read_bytes() != original:
                    raise CsvTransactionError(f"concurrent source change: {target}")
    except Exception as exc:
        _cleanup_paths(item.get("stage") for item in prepared)
        if isinstance(exc, CsvTransactionError):
            raise
        raise CsvTransactionError(f"CSV transaction source verification failed: {exc}") from exc

    attempted: list[dict[str, object]] = []
    try:
        for item in prepared:
            target = item["target"]
            stage = item["stage"]
            assert isinstance(target, Path)
            assert isinstance(stage, Path)
            attempted.append(item)
            operation(stage, target)
            item["stage"] = None
        for item in prepared:
            target = item["target"]
            payload = item["payload"]
            assert isinstance(target, Path)
            assert isinstance(payload, bytes)
            if not target.exists() or target.read_bytes() != payload:
                raise CsvTransactionError(f"post-commit byte mismatch: {target}")
    except Exception as commit_exc:
        rollback_errors: list[str] = []
        for item in reversed(attempted):
            target = item["target"]
            assert isinstance(target, Path)
            restore_stage: Path | None = None
            try:
                if item["existed"]:
                    original = item["original"]
                    assert isinstance(original, bytes)
                    restore_stage = _stage_bytes(target, original, suffix=".rollback")
                    operation(restore_stage, target)
                    restore_stage = None
                else:
                    target.unlink(missing_ok=True)
            except Exception as rollback_exc:
                rollback_errors.append(f"{target}: {rollback_exc}")
            finally:
                _cleanup_paths([restore_stage])

        _cleanup_paths(item.get("stage") for item in prepared)
        if rollback_errors:
            details = "; ".join(rollback_errors)
            raise CsvTransactionError(
                f"CSV transaction commit failed: {commit_exc}; rollback failure: {details}"
            ) from commit_exc
        raise CsvTransactionError(
            f"CSV transaction commit failed: {commit_exc}; rollback completed"
        ) from commit_exc
    finally:
        _cleanup_paths(item.get("stage") for item in prepared)


def sync_owned_fields(
    target_row: MutableMapping[str, str],
    source_row: Mapping[str, str],
    target_fieldnames: Sequence[str],
    *,
    owned_fields: Iterable[str],
    field_aliases: Mapping[str, Sequence[str]] | None = None,
    fill_only_fields: Iterable[str] = (),
    clear_fields: Iterable[str] = (),
) -> None:
    owned = set(owned_fields)
    fields_to_clear = set(clear_fields)
    invalid_clear_fields = fields_to_clear - owned
    if invalid_clear_fields:
        raise ValueError(
            "clear_fields must be a subset of owned_fields: "
            + ", ".join(sorted(invalid_clear_fields))
        )

    aliases = field_aliases or {}
    fill_only = set(fill_only_fields)
    for field in target_fieldnames:
        if field not in owned:
            continue
        if field in fields_to_clear:
            target_row[field] = ""
            continue

        source_value: str | None = None
        for candidate in (field, *aliases.get(field, ())):
            if candidate not in source_row:
                continue
            candidate_value = str(source_row.get(candidate) or "")
            if candidate_value.strip():
                source_value = candidate_value
                break
        if source_value is None:
            continue
        if field in fill_only and str(target_row.get(field) or "").strip():
            continue
        target_row[field] = source_value
