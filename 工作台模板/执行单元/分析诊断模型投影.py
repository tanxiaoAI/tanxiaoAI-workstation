#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import json
import os
import tempfile
from pathlib import Path
from typing import Any, Mapping


class ModelProjectionError(RuntimeError):
    pass


MODEL_INPUT_FILE = "model_input.json"
RECEIPT_FILE = "model_input_receipt.json"


def _canonical_bytes(payload: Mapping[str, Any]) -> bytes:
    return json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _relative(root: Path, path: Path) -> str:
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError as exc:
        raise ModelProjectionError("model_projection_path_outside_workspace") from exc


def _write_immutable_json(path: Path, payload: Mapping[str, Any]) -> None:
    encoded = (json.dumps(payload, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    if path.exists():
        try:
            existing = path.read_bytes()
        except OSError as exc:
            raise ModelProjectionError("model_projection_unreadable") from exc
        if existing != encoded:
            raise ModelProjectionError("model_projection_immutable_conflict")
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    temporary = Path(name)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
        try:
            os.link(temporary, path)
        except (FileExistsError, PermissionError) as exc:
            # 带文件代理的宿主会把 os.link 的 EEXIST 转成不带 errno 的 PermissionError。
            # 只有确认目标确实已存在时才按「本轮已经写过」处理，其余权限问题照旧抛出。
            if isinstance(exc, PermissionError) and not path.exists():
                raise
            if path.read_bytes() != encoded:
                raise ModelProjectionError("model_projection_immutable_conflict")
        else:
            if path.read_bytes() != encoded:
                raise ModelProjectionError("model_projection_post_write_mismatch")
    finally:
        temporary.unlink(missing_ok=True)


def assert_snapshot_write_compatible(
    workspace_root: str | Path,
    *,
    snapshot_path: str | Path,
    snapshot_payload: Mapping[str, Any],
) -> None:
    """Reject a rewrite that would invalidate an immutable projection receipt."""
    root = Path(workspace_root).expanduser().resolve()
    source = Path(snapshot_path).resolve()
    snapshot_relative = _relative(root, source)
    suffix = source.stem
    for prefix in ("分析输入快照", "诊断输入快照"):
        if suffix.startswith(prefix):
            suffix = suffix[len(prefix):]
            break
    model_path = source.parent / ("model_input" + suffix + ".json")
    receipt_path = source.parent / ("model_input_receipt" + suffix + ".json")
    if not model_path.exists() and not receipt_path.exists():
        return
    if not model_path.is_file() or not receipt_path.is_file():
        raise ModelProjectionError("model_projection_incomplete")
    try:
        receipt = json.loads(receipt_path.read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ModelProjectionError("model_projection_receipt_unreadable") from exc
    if not isinstance(receipt, Mapping):
        raise ModelProjectionError("model_projection_receipt_invalid")
    expected_bytes = (json.dumps(snapshot_payload, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    if (
        str(receipt.get("snapshot_path") or "") != snapshot_relative
        or str(receipt.get("snapshot_sha256") or "") != hashlib.sha256(expected_bytes).hexdigest()
    ):
        raise ModelProjectionError("model_projection_snapshot_conflict")


def _snapshot_files(snapshot: Mapping[str, Any]) -> dict[str, dict[str, str]]:
    input_snapshot = snapshot.get("input_snapshot")
    files = input_snapshot.get("files") if isinstance(input_snapshot, Mapping) else None
    if not isinstance(files, Mapping):
        return {}
    result: dict[str, dict[str, str]] = {}
    for label, raw in sorted(files.items()):
        if not isinstance(raw, Mapping):
            continue
        path = str(raw.get("path") or "").strip()
        sha256 = str(raw.get("sha256") or "").strip()
        if path:
            result[str(label)] = {"path": path, "sha256": sha256}
    return result


def _projection(
    *,
    workflow: str,
    field_contract: Mapping[str, Any],
    evidence_paths: Mapping[str, Any],
    run: Mapping[str, Any],
    revision: Mapping[str, Any],
    context: Mapping[str, Any],
    snapshot_sha256: str,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "schema_version": "1",
        "workflow": workflow,
        "field_contract": field_contract,
        "evidence_paths": evidence_paths,
        "run": run,
        "revision": revision,
        "snapshot_sha256": snapshot_sha256,
        "context": context,
    }
    payload["projection_sha256"] = hashlib.sha256(_canonical_bytes(payload)).hexdigest()
    return payload


def _content_projection(snapshot: Mapping[str, Any], *, snapshot_sha256: str) -> dict[str, Any]:
    content = snapshot.get("content") if isinstance(snapshot.get("content"), Mapping) else {}
    interactions = snapshot.get("interaction_data") if isinstance(snapshot.get("interaction_data"), Mapping) else {}
    expectations = snapshot.get("candidate_expectations") if isinstance(snapshot.get("candidate_expectations"), Mapping) else {}
    semantic = {}
    input_snapshot = snapshot.get("input_snapshot")
    if isinstance(input_snapshot, Mapping) and isinstance(input_snapshot.get("semantic_fingerprint"), Mapping):
        semantic = dict(input_snapshot["semantic_fingerprint"])
    return _projection(
        workflow="benchmark-content-diagnose",
        field_contract={
            "required": [
                "content.content_id", "content.platform", "content.title", "content.media_type",
                "context.interaction_data", "context.candidate_expectations",
            ],
            "enums": {
                "intent": ["full_analysis", "reanalyze"],
                "body_status": ["已获取", "确认无正文", ""],
                "transcript_quality_warning": [
                    "transcript_too_short", "transcript_control_character", "transcript_repeated_fragment",
                ],
            },
        },
        evidence_paths=_snapshot_files(snapshot),
        run={"run_id": str(snapshot.get("run_id") or "")},
        revision={"semantic_fingerprint": semantic},
        context={
            "intent": str(snapshot.get("intent") or ""),
            "content": {
                key: content.get(key, "")
                for key in ("content_id", "platform", "title", "media_type", "body_status", "transcript_quality_warnings")
            },
            "interaction_data": dict(interactions),
            "candidate_expectations": dict(expectations),
        },
        snapshot_sha256=snapshot_sha256,
    )


def _self_account_projection(
    snapshot: Mapping[str, Any],
    *,
    snapshot_sha256: str,
    task_id: str,
    run_id: str,
    task_revision: int | None,
) -> dict[str, Any]:
    account = snapshot.get("account") if isinstance(snapshot.get("account"), Mapping) else {}
    content = snapshot.get("content") if isinstance(snapshot.get("content"), Mapping) else {}
    warnings = snapshot.get("warnings") if isinstance(snapshot.get("warnings"), list) else []
    warning_codes = [str(item.get("code") or "") for item in warnings if isinstance(item, Mapping)]
    return _projection(
        workflow="self-account-diagnose",
        field_contract={
            "required": [
                "account.account_id", "account.platform", "content.selected_content_ids",
                "context.next_route", "context.checks",
            ],
            "enums": {
                "visibility_scope": ["public", "private", "all"],
                "next_route": ["continue_diagnosis", "basic_data", "detail_data"],
                "evidence_mode": ["full", "degraded", "insufficient"],
            },
        },
        evidence_paths=_snapshot_files(snapshot),
        run={"task_id": task_id, "run_id": run_id},
        revision={"task_revision": task_revision, "source": "dispatcher_state" if task_revision is not None else "unavailable"},
        context={
            "account": {
                "account_id": str(account.get("账号ID") or account.get("account_id") or ""),
                "platform": str(account.get("平台") or account.get("platform") or ""),
                "account_name": str(account.get("账号名") or account.get("account_name") or ""),
            },
            "content": {
                key: content.get(key)
                for key in ("selected_content_ids", "selected_count", "usable_evidence_count", "full_evidence_count", "degraded_evidence_count", "insufficient_evidence_count", "visibility_scope", "evidence_items")
            },
            "next_route": str(snapshot.get("next_route") or ""),
            "route_reason": str(snapshot.get("route_reason") or ""),
            "checks": dict(snapshot.get("checks") or {}),
            "warning_codes": warning_codes,
        },
        snapshot_sha256=snapshot_sha256,
    )


def write_content_model_projection(
    workspace_root: str | Path,
    *,
    snapshot_path: str | Path,
    snapshot: Mapping[str, Any],
) -> dict[str, Any]:
    root = Path(workspace_root).expanduser().resolve()
    source = Path(snapshot_path).resolve()
    snapshot_sha256 = _file_sha256(source)
    projection = _content_projection(snapshot, snapshot_sha256=snapshot_sha256)
    return _write_projection_files(root, source, projection, snapshot_sha256)


def write_self_account_model_projection(
    workspace_root: str | Path,
    *,
    snapshot_path: str | Path,
    snapshot: Mapping[str, Any],
    task_id: str,
    run_id: str,
    task_revision: int | None,
) -> dict[str, Any]:
    root = Path(workspace_root).expanduser().resolve()
    source = Path(snapshot_path).resolve()
    snapshot_sha256 = _file_sha256(source)
    projection = _self_account_projection(
        snapshot,
        snapshot_sha256=snapshot_sha256,
        task_id=task_id,
        run_id=run_id,
        task_revision=task_revision,
    )
    return _write_projection_files(root, source, projection, snapshot_sha256)


def _write_projection_files(
    root: Path,
    snapshot_path: Path,
    projection: Mapping[str, Any],
    snapshot_sha256: str,
) -> dict[str, Any]:
    snapshot_relative = _relative(root, snapshot_path)
    if "/.meiling/staging/" not in f"/{snapshot_relative}":
        raise ModelProjectionError("model_projection_snapshot_outside_staging")
    suffix = snapshot_path.stem
    for prefix in ("分析输入快照", "诊断输入快照"):
        if suffix.startswith(prefix):
            suffix = suffix[len(prefix):]
            break
    model_name = "model_input" + suffix + ".json"
    receipt_name = "model_input_receipt" + suffix + ".json"
    model_path = snapshot_path.parent / model_name
    receipt_path = snapshot_path.parent / receipt_name
    projection_hash = str(projection.get("projection_sha256") or "")
    receipt = {
        "schema_version": "1",
        "workflow": str(projection.get("workflow") or ""),
        "model_input_path": _relative(root, model_path),
        "projection_sha256": projection_hash,
        "snapshot_path": snapshot_relative,
        "snapshot_sha256": snapshot_sha256,
        "run": projection.get("run"),
        "revision": projection.get("revision"),
        "metrics": {
            "snapshot_bytes": snapshot_path.stat().st_size,
            "model_input_bytes": len((json.dumps(projection, ensure_ascii=False, indent=2) + "\n").encode("utf-8")),
        },
    }
    _write_immutable_json(model_path, projection)
    _write_immutable_json(receipt_path, receipt)
    return {
        "model_input_path": _relative(root, model_path),
        "model_input_receipt_path": _relative(root, receipt_path),
        "projection_sha256": projection_hash,
    }
