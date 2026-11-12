#!/usr/bin/env python3
from __future__ import annotations

import json
import hashlib
import os
import tempfile
import csv
import importlib.util
import sys
from datetime import datetime
from contextlib import contextmanager
from pathlib import Path, PurePosixPath
from typing import Any, Iterator, Mapping, Sequence

try:
    import fcntl
except ImportError:  # pragma: no cover - current desktop runtime is POSIX.
    fcntl = None


STAGING_ROOT = Path(".meiling/staging/benchmark-content-diagnose")
STATE_FILE_NAME = "运行状态.json"
ASSET_RECEIPT_FILE_NAME = "资产处理回执.json"
STATE_SCHEMA_VERSION = "3"
ASSET_RECEIPT_SCHEMA_VERSION = "2"
ASSET_RECEIPT_BINDING_VERSION = "1"
STATE_FIELDS = {
    "schema_version",
    "workflow_id",
    "run_id",
    "staging_dir",
    "content_id",
    "platform",
    "intent",
    "status",
    "last_stage",
    "analysis_input_path",
    "analysis_path",
    "analysis_sha256",
    "feature_candidate_path",
    "analysis_version",
    "feature_candidate_status",
    "asset_processing_receipt_path",
    "superseded_by",
    "superseded_at",
    "supersede_reason",
}
LEGACY_STATE_FIELDS = STATE_FIELDS - {"asset_processing_receipt_path", "superseded_by", "superseded_at", "supersede_reason"}
RUN_STATUSES = {"active", "completed", "superseded", "failed"}
FEATURE_CANDIDATE_STATUSES = {"not_checked", "pending", "none", "resolved"}
RUN_STAGES = {
    "prepared",
    "analysis_committed",
    "features_committed",
    "feature_decision_committed",
    "completed",
}
UPDATABLE_FIELDS = STATE_FIELDS - {
    "schema_version", "workflow_id", "run_id", "staging_dir", "content_id", "platform", "intent",
}


class BenchmarkContentRunStateError(RuntimeError):
    pass


def _root(workspace_root: str | Path) -> Path:
    root = Path(workspace_root).expanduser().resolve()
    if not root.is_dir():
        raise BenchmarkContentRunStateError("workspace_root_invalid")
    return root


def _run_dir(root: Path, run_id: str) -> Path:
    clean = str(run_id or "").strip()
    if not clean or PurePosixPath(clean).name != clean or clean in {".", ".."}:
        raise BenchmarkContentRunStateError("run_id_invalid")
    return (root / STAGING_ROOT / clean).resolve()


def _state_path(run_dir: Path) -> Path:
    return run_dir / STATE_FILE_NAME


@contextmanager
def _exclusive_lock(path: Path) -> Iterator[None]:
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = path.open("a+")
    try:
        if fcntl is not None:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        yield
    finally:
        if fcntl is not None:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        handle.close()


def _validate(state: object) -> dict[str, str]:
    if not isinstance(state, Mapping):
        raise BenchmarkContentRunStateError("run_state_contract_invalid")
    # 旧状态升级（schema 1/2）：那一版把「内容画像与特征卡」当产物，状态里存的是
    # 卡路径 + 卡 sha。卡退役后这两个键换成「候选路径」+「分析版本」。
    if "feature_card_path" in state or "feature_card_sha256" in state:
        state = {
            key: value for key, value in state.items()
            if key not in {"feature_card_path", "feature_card_sha256"}
        }
        state = {**state, "feature_candidate_path": "", "analysis_version": ""}
    # `last_stage` 的历史取值一并归位（2026-09-27 改名：feature_card_committed
    # → features_committed），否则中断过的旧 run 会卡在「未知阶段」。
    if state.get("last_stage") == "feature_card_committed":
        state = {**state, "last_stage": "features_committed"}
    for field, default in (
        ("schema_version", STATE_SCHEMA_VERSION),
        ("asset_processing_receipt_path", ""),
        ("superseded_by", ""),
        ("superseded_at", ""),
        ("supersede_reason", ""),
    ):
        if field not in state:
            state = {**state, field: default}
    if state.get("schema_version") in {"1", "2"}:
        state = {**state, "schema_version": STATE_SCHEMA_VERSION}
    if set(state) != STATE_FIELDS:
        raise BenchmarkContentRunStateError("run_state_contract_invalid")
    if any(not isinstance(state[field], str) for field in STATE_FIELDS):
        raise BenchmarkContentRunStateError("run_state_field_invalid")
    prepared = {field: str(state[field]).strip() for field in STATE_FIELDS}
    if prepared["schema_version"] != STATE_SCHEMA_VERSION:
        raise BenchmarkContentRunStateError("run_state_schema_invalid")
    if prepared["workflow_id"] != "benchmark-content-diagnose":
        raise BenchmarkContentRunStateError("run_state_workflow_invalid")
    if prepared["status"] not in RUN_STATUSES:
        raise BenchmarkContentRunStateError("run_state_status_invalid")
    if prepared["last_stage"] not in RUN_STAGES:
        raise BenchmarkContentRunStateError("run_state_stage_invalid")
    if prepared["feature_candidate_status"] not in FEATURE_CANDIDATE_STATUSES:
        raise BenchmarkContentRunStateError("run_state_feature_candidate_status_invalid")
    if prepared["status"] == "completed" and prepared["last_stage"] != "completed":
        raise BenchmarkContentRunStateError("run_state_completion_invalid")
    expected_dir = (STAGING_ROOT / prepared["run_id"]).as_posix()
    if prepared["staging_dir"] != expected_dir:
        raise BenchmarkContentRunStateError("run_state_staging_dir_invalid")
    if not prepared["content_id"] or prepared["intent"] not in {"full_analysis", "reanalyze"}:
        raise BenchmarkContentRunStateError("run_state_identity_invalid")
    return prepared


def encode_state(state: Mapping[str, str]) -> bytes:
    return (json.dumps(_validate(state), ensure_ascii=False, indent=2) + "\n").encode("utf-8")


def _write_state(path: Path, state: Mapping[str, str]) -> None:
    payload = encode_state(state)
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    temporary = Path(name)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def read_run_state(
    workspace_root: str | Path,
    *,
    run_id: str = "",
    run_dir: str | Path | None = None,
) -> tuple[Path, dict[str, str], bytes]:
    root = _root(workspace_root)
    if run_dir is None:
        resolved_dir = _run_dir(root, run_id)
    else:
        resolved_dir = Path(run_dir).expanduser().resolve()
        try:
            relative = resolved_dir.relative_to(root)
        except ValueError as exc:
            raise BenchmarkContentRunStateError("run_dir_outside_workspace") from exc
        if relative.parent != STAGING_ROOT:
            raise BenchmarkContentRunStateError("run_dir_invalid")
    path = _state_path(resolved_dir)
    try:
        raw = path.read_bytes()
        payload = json.loads(raw.decode("utf-8-sig"))
    except FileNotFoundError as exc:
        raise BenchmarkContentRunStateError(f"run_state_missing:{resolved_dir.name}") from exc
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise BenchmarkContentRunStateError(f"run_state_unreadable:{resolved_dir.name}") from exc
    state = _validate(payload)
    if state["run_id"] != resolved_dir.name:
        raise BenchmarkContentRunStateError("run_state_id_mismatch")
    return path, state, raw


def create_or_refresh_run_state(
    workspace_root: str | Path,
    *,
    run_dir: Path,
    snapshot: Mapping[str, Any],
    analysis_input_path: str,
) -> dict[str, str]:
    root = _root(workspace_root)
    resolved_dir = run_dir.resolve()
    try:
        relative_dir = resolved_dir.relative_to(root)
    except ValueError as exc:
        raise BenchmarkContentRunStateError("run_dir_outside_workspace") from exc
    if relative_dir.parent != STAGING_ROOT:
        raise BenchmarkContentRunStateError("run_dir_invalid")
    content = snapshot.get("content")
    if not isinstance(content, Mapping):
        raise BenchmarkContentRunStateError("analysis_input_content_missing")
    content_id = str(content.get("content_id") or "").strip()
    platform = str(content.get("platform") or "").strip()
    intent = str(snapshot.get("intent") or "").strip()
    path = _state_path(resolved_dir)
    lock_path = resolved_dir / ".run-state.lock"
    target_lock = root / STAGING_ROOT / ".target-state.lock"
    with _exclusive_lock(target_lock):
      with _exclusive_lock(lock_path):
        if path.is_file():
            _, state, _ = read_run_state(root, run_dir=resolved_dir)
            if (state["content_id"], state["platform"], state["intent"]) != (
                content_id, platform, intent,
            ):
                raise BenchmarkContentRunStateError("run_state_identity_mismatch")
            if state["status"] != "active":
                raise BenchmarkContentRunStateError("run_already_completed")
            state["analysis_input_path"] = analysis_input_path
        else:
            # Keep one active run for a target. Older runs remain auditable but
            # cannot be resumed or committed once a new snapshot is frozen.
            base = root / STAGING_ROOT
            for old_path in sorted(base.glob(f"*/{STATE_FILE_NAME}")):
                if old_path.parent == resolved_dir:
                    continue
                try:
                    _, old_state, _ = read_run_state(root, run_dir=old_path.parent)
                except BenchmarkContentRunStateError:
                    continue
                if old_state["status"] == "active" and (old_state["content_id"], old_state["platform"], old_state["intent"]) == (content_id, platform, intent):
                    old_state.update({"status": "superseded", "superseded_by": resolved_dir.name, "superseded_at": datetime.now().astimezone().isoformat(timespec="seconds"), "supersede_reason": "newer_snapshot"})
                    _write_state(old_path, old_state)
            state = {
                "schema_version": STATE_SCHEMA_VERSION,
                "workflow_id": "benchmark-content-diagnose",
                "run_id": resolved_dir.name,
                "staging_dir": relative_dir.as_posix(),
                "content_id": content_id,
                "platform": platform,
                "intent": intent,
                "status": "active",
                "last_stage": "prepared",
                "analysis_input_path": analysis_input_path,
                "analysis_path": "",
                "analysis_sha256": "",
                "feature_candidate_path": "",
                "analysis_version": "",
                "feature_candidate_status": "not_checked",
                "asset_processing_receipt_path": "",
                "superseded_by": "",
                "superseded_at": "",
                "supersede_reason": "",
            }
        _write_state(path, state)
    return state


def prepare_run_state_update(
    workspace_root: str | Path,
    *,
    run_dir: Path,
    updates: Mapping[str, str],
) -> tuple[Path, bytes, bytes, dict[str, str]]:
    if set(updates) - UPDATABLE_FIELDS:
        raise BenchmarkContentRunStateError("run_state_update_fields_invalid")
    path, state, original = read_run_state(workspace_root, run_dir=run_dir)
    if state["status"] != "active":
        raise BenchmarkContentRunStateError("run_already_completed")
    prepared = dict(state)
    prepared.update({key: str(value).strip() for key, value in updates.items()})
    payload = encode_state(prepared)
    return path, original, payload, prepared


def update_run_state(
    workspace_root: str | Path,
    *,
    run_dir: str | Path,
    updates: Mapping[str, str],
) -> dict[str, str]:
    root = _root(workspace_root)
    resolved_dir = Path(run_dir).expanduser().resolve()
    lock_path = resolved_dir / ".run-state.lock"
    with _exclusive_lock(lock_path):
        path, _, payload, prepared = prepare_run_state_update(
            root,
            run_dir=resolved_dir,
            updates=updates,
        )
        _write_state(path, prepared)
        if path.read_bytes() != payload:
            raise BenchmarkContentRunStateError("run_state_post_update_mismatch")
    return prepared


def bind_run_paths(
    workspace_root: str | Path,
    paths: Sequence[str | Path],
) -> tuple[Path, dict[str, str], bytes]:
    root = _root(workspace_root)
    staging_root = (root / STAGING_ROOT).resolve()
    run_dirs: set[Path] = set()
    for raw in paths:
        text = str(raw or "").strip().replace("\\", "/")
        if not text:
            raise BenchmarkContentRunStateError("run_bound_path_missing")
        logical = PurePosixPath(text)
        candidate = Path(text).expanduser()
        resolved = candidate.resolve() if candidate.is_absolute() else root.joinpath(*logical.parts).resolve()
        if resolved.parent.parent != staging_root:
            raise BenchmarkContentRunStateError(f"run_bound_path_invalid:{text}")
        run_dirs.add(resolved.parent)
    if len(run_dirs) != 1:
        raise BenchmarkContentRunStateError("run_bound_paths_mismatch")
    run_dir = next(iter(run_dirs))
    _, state, raw_state = read_run_state(root, run_dir=run_dir)
    return run_dir, state, raw_state


def verify_snapshot_run_binding(
    snapshot: Mapping[str, Any],
    *,
    input_path: Path,
    run_dir: Path,
    state: Mapping[str, str],
) -> None:
    expected_dir = state["staging_dir"]
    if str(snapshot.get("run_id") or "").strip() != state["run_id"]:
        raise BenchmarkContentRunStateError("analysis_input_run_id_mismatch")
    if str(snapshot.get("staging_dir") or "").strip() != expected_dir:
        raise BenchmarkContentRunStateError("analysis_input_staging_dir_mismatch")
    actual_input = (Path(expected_dir) / input_path.name).as_posix()
    if str(snapshot.get("analysis_input_path") or "").strip() != actual_input:
        raise BenchmarkContentRunStateError("analysis_input_path_binding_mismatch")
    if state["analysis_input_path"] != actual_input:
        raise BenchmarkContentRunStateError("run_state_analysis_input_mismatch")
    if run_dir.name != state["run_id"] or (STAGING_ROOT / run_dir.name).as_posix() != expected_dir:
        raise BenchmarkContentRunStateError("run_directory_binding_mismatch")


def list_active_runs(
    workspace_root: str | Path,
    *,
    content_id: str,
    platform: str = "",
) -> list[dict[str, object]]:
    root = _root(workspace_root)
    base = root / STAGING_ROOT
    if not base.is_dir():
        return []
    matches: list[dict[str, object]] = []
    for path in sorted(base.glob(f"*/{STATE_FILE_NAME}")):
        _, state, _ = read_run_state(root, run_dir=path.parent)
        if state["status"] != "active" or state["content_id"] != str(content_id or "").strip():
            continue
        if platform and state["platform"] != str(platform).strip():
            continue
        matches.append(
            {
                **state,
                "next_action": next_action(state),
                "next": executable_next(state),
            }
        )
    return matches


def next_action(state: Mapping[str, str]) -> str:
    stage = str(state.get("last_stage") or "")
    candidate_status = str(state.get("feature_candidate_status") or "")
    if stage == "prepared":
        return "generate_internal_analysis"
    if stage == "analysis_committed":
        return (
            "finalize_confirmed_features"
            if candidate_status == "pending"
            else "generate_features"
        )
    if stage == "feature_decision_committed":
        return "resume_feature_decisions"
    if stage == "features_committed":
        if state.get("asset_processing_receipt_path"):
            return "resume_feature_decisions" if candidate_status == "pending" else "complete_run"
        if candidate_status == "pending":
            return "write_feature_assets_then_resume_feature_decisions"
        return "write_feature_assets_then_complete"
    if stage == "completed":
        return "completed"
    raise BenchmarkContentRunStateError("run_state_next_action_invalid")


def executable_next(state: Mapping[str, str]) -> dict[str, object] | None:
    action = next_action(state)
    if action == "completed":
        return None
    input_fields = {
        "generate_internal_analysis": ("analysis_input_path",),
        "generate_features": (
            "analysis_input_path", "analysis_path", "analysis_sha256",
        ),
        "finalize_confirmed_features": (
            "analysis_input_path", "analysis_path", "analysis_sha256",
        ),
        "resume_feature_decisions": (
            "feature_candidate_path", "analysis_version", "feature_candidate_status",
        ),
        "write_feature_assets_then_resume_feature_decisions": (
            "feature_candidate_path", "analysis_version", "feature_candidate_status",
        ),
        "write_feature_assets_then_complete": (
            "feature_candidate_path", "analysis_version", "feature_candidate_status",
        ),
        "complete_run": (
            "feature_candidate_path", "analysis_version", "asset_processing_receipt_path",
        ),
    }
    fields = input_fields.get(action)
    if fields is None:
        raise BenchmarkContentRunStateError("run_state_next_action_invalid")
    return {
        "action": action,
        "run_id": str(state.get("run_id") or ""),
        "staging_dir": str(state.get("staging_dir") or ""),
        "inputs": {
            field: str(state.get(field) or "")
            for field in fields
        },
    }


def _candidate_run(state: Mapping[str, str]) -> dict[str, object]:
    return {
        "run_id": str(state.get("run_id") or ""),
        "content_id": str(state.get("content_id") or ""),
        "platform": str(state.get("platform") or ""),
        "intent": str(state.get("intent") or ""),
        "last_stage": str(state.get("last_stage") or ""),
        "next": executable_next(state),
    }


def resume_run(
    workspace_root: str | Path,
    *,
    content_id: str,
    platform: str = "",
    run_id: str = "",
) -> dict[str, object]:
    root = _root(workspace_root)
    if run_id:
        _, state, _ = read_run_state(root, run_id=run_id)
        matches = [state] if state["status"] == "active" else []
        if matches and (
            state["content_id"] != str(content_id or "").strip()
            or (platform and state["platform"] != str(platform).strip())
        ):
            raise BenchmarkContentRunStateError("resume_run_identity_mismatch")
    else:
        matches = list_active_runs(root, content_id=content_id, platform=platform)
    if not matches:
        return {
            "ok": False,
            "status": "blocked",
            "reason": "unfinished_run_not_found",
            "next": None,
        }
    if len(matches) > 1:
        return {
            "ok": False,
            "status": "blocked",
            "reason": "multiple_unfinished_runs",
            "candidate_runs": [_candidate_run(state) for state in matches],
            "next": None,
        }
    state = matches[0]
    return {
        **state,
        "ok": True,
        "run_status": state["status"],
        "status": "run_resumed",
        "next_action": next_action(state),
        "next": executable_next(state),
    }


def _verify_formal_file(root: Path, *, raw_path: str, expected_sha256: str, label: str) -> None:
    if not raw_path or not expected_sha256:
        raise BenchmarkContentRunStateError(f"run_{label}_missing")
    path = (root / PurePosixPath(raw_path)).resolve()
    try:
        path.relative_to(root)
    except ValueError as exc:
        raise BenchmarkContentRunStateError(f"run_{label}_outside_workspace") from exc
    if not path.is_file():
        raise BenchmarkContentRunStateError(f"run_{label}_missing")
    if hashlib.sha256(path.read_bytes()).hexdigest() != expected_sha256:
        raise BenchmarkContentRunStateError(f"run_{label}_hash_mismatch")


def _relative_file(root: Path, raw_path: object, *, label: str) -> Path:
    text = str(raw_path or "").strip()
    if not text or "\\" in text:
        raise BenchmarkContentRunStateError(f"asset_receipt_{label}_path_invalid")
    pure = PurePosixPath(text)
    if pure.is_absolute() or ".." in pure.parts:
        raise BenchmarkContentRunStateError(f"asset_receipt_{label}_path_invalid")
    path = root.joinpath(*pure.parts).resolve()
    try:
        path.relative_to(root)
    except ValueError as exc:
        raise BenchmarkContentRunStateError(f"asset_receipt_{label}_path_outside_workspace") from exc
    if not path.is_file():
        raise BenchmarkContentRunStateError(f"asset_receipt_{label}_path_missing")
    return path


def _canonical_rows_hash(rows: Sequence[Mapping[str, str]]) -> str:
    normalized = [
        {str(key): str(value) for key, value in sorted(row.items())}
        for row in rows
    ]
    payload = json.dumps(normalized, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _read_csv_rows(root: Path, raw_path: str, *, label: str) -> list[dict[str, str]]:
    path = _relative_file(root, raw_path, label=label)
    try:
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            return [dict(row) for row in csv.DictReader(handle)]
    except (OSError, UnicodeError, csv.Error) as exc:
        raise BenchmarkContentRunStateError(f"asset_receipt_{label}_unreadable") from exc


def _require_receipt_string(receipt: Mapping[str, object], field: str, *, allow_blank: bool = False) -> str:
    value = receipt.get(field)
    if not isinstance(value, str) or value != value.strip() or (not allow_blank and not value):
        raise BenchmarkContentRunStateError(f"asset_receipt_{field}_invalid")
    return value


def _require_receipt_mapping(receipt: Mapping[str, object], field: str) -> Mapping[str, object]:
    value = receipt.get(field)
    if not isinstance(value, Mapping):
        raise BenchmarkContentRunStateError(f"asset_receipt_{field}_invalid")
    return value


def _instance_rows_for_receipt(
    root: Path,
    *,
    state: Mapping[str, str],
    analysis_version: str,
    feature_ids: Sequence[str],
) -> list[dict[str, str]]:
    rows = _read_csv_rows(root, "沉淀结论/内容特征实例.csv", label="feature_instances")
    selected = [
        row for row in rows
        if row.get("样本键") == state["content_id"]
        and row.get("内容ID") == state["content_id"]
        and row.get("平台") == state["platform"]
        and row.get("样本来源") == "对标内容"
        and row.get("分析版本") == analysis_version
        and row.get("当前有效") == "是"
    ]
    actual_ids = sorted(row.get("特征ID", "") for row in selected)
    if actual_ids != list(feature_ids) or len(actual_ids) != len(set(actual_ids)):
        raise BenchmarkContentRunStateError("asset_receipt_feature_instances_binding_mismatch")
    return sorted(selected, key=lambda row: row.get("特征ID", ""))


def _asset_receipt_path(root: Path, *, run_dir: Path, raw_path: str) -> Path:
    path = _relative_file(root, raw_path, label="file")
    try:
        path.relative_to(run_dir)
    except ValueError as exc:
        raise BenchmarkContentRunStateError("asset_receipt_file_outside_run") from exc
    return path


def _load_asset_receipt(root: Path, *, run_dir: Path, state: Mapping[str, str]) -> tuple[Path, Mapping[str, object]]:
    raw_path = state["asset_processing_receipt_path"]
    if not raw_path:
        raise BenchmarkContentRunStateError("asset_processing_receipt_missing")
    path = _asset_receipt_path(root, run_dir=run_dir, raw_path=raw_path)
    try:
        receipt = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise BenchmarkContentRunStateError("asset_processing_receipt_unreadable") from exc
    if not isinstance(receipt, Mapping):
        raise BenchmarkContentRunStateError("asset_processing_receipt_contract_invalid")
    return path, receipt


def _verify_asset_receipt(
    root: Path,
    *,
    run_dir: Path,
    state: Mapping[str, str],
) -> dict[str, object]:
    _, receipt = _load_asset_receipt(root, run_dir=run_dir, state=state)
    expected_fields = {
        "schema_version", "binding_version", "producer", "run_id", "content_id",
        "feature_candidate_path", "analysis_version", "feature_write",
        "feature_write_reason", "outputs",
    }
    if set(receipt) != expected_fields:
        raise BenchmarkContentRunStateError("asset_processing_receipt_contract_invalid")
    if _require_receipt_string(receipt, "schema_version") != ASSET_RECEIPT_SCHEMA_VERSION:
        raise BenchmarkContentRunStateError("asset_processing_receipt_schema_invalid")
    if _require_receipt_string(receipt, "binding_version") != ASSET_RECEIPT_BINDING_VERSION:
        raise BenchmarkContentRunStateError("asset_processing_receipt_binding_version_invalid")
    if _require_receipt_string(receipt, "producer") != "benchmark-content-diagnose.asset-processing":
        raise BenchmarkContentRunStateError("asset_processing_receipt_producer_invalid")
    for field in ("run_id", "content_id", "feature_candidate_path"):
        if _require_receipt_string(receipt, field) != state[field]:
            raise BenchmarkContentRunStateError(f"asset_processing_receipt_{field}_mismatch")
    # 版本号 = **特征集合的规范化 sha256**，由 2.4.2 算好后存进 run state，
    # 2.4.3 直接复用（不再有「卡文件 sha」这个来源）。
    analysis_version = _require_receipt_string(receipt, "analysis_version")
    if analysis_version != state["analysis_version"]:
        raise BenchmarkContentRunStateError("asset_processing_receipt_analysis_version_mismatch")
    feature_write = _require_receipt_string(receipt, "feature_write")
    feature_reason = _require_receipt_string(receipt, "feature_write_reason", allow_blank=True)
    if feature_write not in {"committed", "failed"}:
        raise BenchmarkContentRunStateError("asset_processing_receipt_feature_write_not_terminal")
    if (feature_write == "committed" and feature_reason) or (feature_write == "failed" and not feature_reason):
        raise BenchmarkContentRunStateError("asset_processing_receipt_feature_write_reason_invalid")
    outputs = _require_receipt_mapping(receipt, "outputs")
    if set(outputs) != {"feature_instances"}:
        raise BenchmarkContentRunStateError("asset_processing_receipt_outputs_invalid")

    if feature_write == "failed":
        if outputs["feature_instances"] is not None:
            raise BenchmarkContentRunStateError("asset_processing_receipt_failure_outputs_invalid")
        return {
            "status": "failed",
            "warnings": [f"feature_write_failed:{feature_reason}"],
            "feature_write": feature_write,
        }

    instance = _require_receipt_mapping({"value": outputs["feature_instances"]}, "value")
    if set(instance) != {"path", "rows_sha256", "feature_ids"}:
        raise BenchmarkContentRunStateError("asset_processing_receipt_feature_instances_invalid")
    if _require_receipt_string(instance, "path") != "沉淀结论/内容特征实例.csv":
        raise BenchmarkContentRunStateError("asset_processing_receipt_feature_instances_path_invalid")
    feature_ids = instance.get("feature_ids")
    if not isinstance(feature_ids, list) or any(not isinstance(value, str) or not value for value in feature_ids):
        raise BenchmarkContentRunStateError("asset_processing_receipt_feature_ids_invalid")
    if feature_ids != sorted(set(feature_ids)):
        raise BenchmarkContentRunStateError("asset_processing_receipt_feature_ids_invalid")
    instance_rows = _instance_rows_for_receipt(
        root, state=state, analysis_version=analysis_version, feature_ids=feature_ids
    )
    if _require_receipt_string(instance, "rows_sha256") != _canonical_rows_hash(instance_rows):
        raise BenchmarkContentRunStateError("asset_processing_receipt_feature_instances_hash_mismatch")

    if not feature_ids:
        return {
            "status": "not_applicable",
            "warnings": ["no_core_features"],
            "feature_write": feature_write,
        }

    return {
        "status": "committed",
        "warnings": [],
        "feature_write": feature_write,
    }


def _write_json_atomically(path: Path, payload: Mapping[str, object]) -> None:
    encoded = (json.dumps(payload, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    descriptor, name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    temporary = Path(name)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _load_asset_executor(file_name: str, module_name: str) -> object:
    """Load sibling executors both from their CLI and isolated unit tests."""
    unit_dir = Path(__file__).resolve().parent
    spec = importlib.util.spec_from_file_location(module_name, unit_dir / file_name)
    if spec is None or spec.loader is None:
        raise BenchmarkContentRunStateError(f"asset_executor_load_failed:{file_name}")
    module = importlib.util.module_from_spec(spec)
    previous = list(sys.path)
    sys.path.insert(0, str(unit_dir))
    try:
        spec.loader.exec_module(module)
    finally:
        sys.path[:] = previous
    return module


def process_feature_assets(workspace_root: str | Path, *, run_id: str) -> dict[str, object]:
    """Run the two asset executors and bind their terminal facts to this run."""
    root = _root(workspace_root)
    run_dir = _run_dir(root, run_id)
    lock_path = run_dir / ".run-state.lock"
    with _exclusive_lock(lock_path):
        state_path, state, _ = read_run_state(root, run_dir=run_dir)
        if state["status"] != "active" or state["last_stage"] != "features_committed":
            raise BenchmarkContentRunStateError("run_not_ready_for_asset_processing")
        _verify_formal_file(root, raw_path=state["analysis_path"], expected_sha256=state["analysis_sha256"], label="analysis")
        candidate_file = root / PurePosixPath(state["feature_candidate_path"])
        if not state["feature_candidate_path"] or not candidate_file.is_file():
            raise BenchmarkContentRunStateError("feature_candidate_missing")
        if state["asset_processing_receipt_path"]:
            try:
                verified = _verify_asset_receipt(root, run_dir=run_dir, state=state)
            except BenchmarkContentRunStateError:
                # A changed shared CSV or an interrupted receipt is recovered by
                # replaying the idempotent executors below.
                pass
            else:
                return {
                    "ok": True,
                    "status": "asset_processing_reused",
                    "run_id": state["run_id"],
                    "asset_processing_receipt_path": state["asset_processing_receipt_path"],
                    "asset_processing": verified,
                    "reused": True,
                }

        # 版本号由 2.4.2 算好（特征集合的规范化 sha256），这里只做一致性校验。
        analysis_version = state["analysis_version"]
        if not analysis_version:
            raise BenchmarkContentRunStateError("analysis_version_missing")
        feature_write = "failed"
        feature_reason = ""
        outputs: dict[str, object] = {"feature_instances": None}
        writer = _load_asset_executor("内容特征写入执行器.py", "benchmark_content_asset_writer")
        try:
            feature_result = writer.apply_feature_candidate(
                root,
                source="对标内容",
                content_id=state["content_id"],
                candidate_path=state["feature_candidate_path"],
                platform=state["platform"],
                analysis_version=analysis_version,
            )
        except writer.ContentFeatureWriteError as exc:
            feature_reason = str(exc) or exc.__class__.__name__
        else:
            if not isinstance(feature_result, Mapping):
                raise BenchmarkContentRunStateError("feature_write_result_invalid")
            if feature_result.get("status") != "committed":
                raise BenchmarkContentRunStateError("feature_write_nonterminal_result")
            if (
                feature_result.get("feature_candidate_path")
                != state["feature_candidate_path"]
                or feature_result.get("analysis_version") != analysis_version
            ):
                raise BenchmarkContentRunStateError("feature_write_card_binding_mismatch")
            feature_ids = feature_result.get("feature_ids")
            if (
                not isinstance(feature_ids, list)
                or any(not isinstance(value, str) or not value for value in feature_ids)
                or feature_ids != sorted(set(feature_ids))
                or feature_result.get("feature_count") != len(feature_ids)
            ):
                raise BenchmarkContentRunStateError("feature_write_result_invalid")
            instance_rows = _instance_rows_for_receipt(
                root, state=state, analysis_version=analysis_version, feature_ids=feature_ids
            )
            outputs["feature_instances"] = {
                "path": "沉淀结论/内容特征实例.csv",
                "rows_sha256": _canonical_rows_hash(instance_rows),
                "feature_ids": feature_ids,
            }
            feature_write = "committed"

        receipt: dict[str, object] = {
            "schema_version": ASSET_RECEIPT_SCHEMA_VERSION,
            "binding_version": ASSET_RECEIPT_BINDING_VERSION,
            "producer": "benchmark-content-diagnose.asset-processing",
            "run_id": state["run_id"],
            "content_id": state["content_id"],
            "feature_candidate_path": state["feature_candidate_path"],
            "analysis_version": analysis_version,
            "feature_write": feature_write,
            "feature_write_reason": feature_reason,
            "outputs": outputs,
        }
        receipt_path = run_dir / ASSET_RECEIPT_FILE_NAME
        _write_json_atomically(receipt_path, receipt)
        state["asset_processing_receipt_path"] = receipt_path.relative_to(root).as_posix()
        _write_state(state_path, state)
        verified = _verify_asset_receipt(root, run_dir=run_dir, state=state)
    return {
        "ok": True,
        "status": "asset_processing_committed",
        "run_id": state["run_id"],
        "asset_processing_receipt_path": state["asset_processing_receipt_path"],
        "asset_processing": verified,
        "reused": False,
    }


def complete_run(workspace_root: str | Path, *, run_id: str) -> dict[str, object]:
    root = _root(workspace_root)
    run_dir = _run_dir(root, run_id)
    lock_path = run_dir / ".run-state.lock"
    with _exclusive_lock(lock_path):
        path, state, _ = read_run_state(root, run_dir=run_dir)
        if state["status"] == "superseded":
            raise BenchmarkContentRunStateError("run_superseded")
        if state["status"] == "failed":
            raise BenchmarkContentRunStateError("run_failed")
        if state["status"] != "completed" and state["last_stage"] != "features_committed":
            raise BenchmarkContentRunStateError("run_not_ready_for_completion")
        if state["feature_candidate_status"] == "pending":
            raise BenchmarkContentRunStateError("feature_candidates_pending")
        if state["feature_candidate_status"] not in {"none", "resolved"}:
            raise BenchmarkContentRunStateError("run_not_ready_for_completion")
        _verify_formal_file(
            root,
            raw_path=state["analysis_path"],
            expected_sha256=state["analysis_sha256"],
            label="analysis",
        )
        candidate_file = root / PurePosixPath(state["feature_candidate_path"])
        if not state["feature_candidate_path"] or not candidate_file.is_file():
            raise BenchmarkContentRunStateError("feature_candidate_missing")
        asset_processing = _verify_asset_receipt(root, run_dir=run_dir, state=state)
        if state["status"] != "completed":
            state["status"] = "completed"
            state["last_stage"] = "completed"
            _write_state(path, state)
    return {**state, "asset_processing": asset_processing}
