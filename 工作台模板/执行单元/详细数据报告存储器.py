"""Canonical detail-data report storage.

The detail-data producers share this module instead of constructing and
persisting report objects independently.  The module deliberately knows
nothing about scheduler routing or CSV contents; it only owns report identity,
validation, atomic writes, and the small AI-summary update operation.
"""
from __future__ import annotations

import importlib.util
import json
from copy import deepcopy
from pathlib import Path
from typing import Any, Iterable


_HERE = Path(__file__).resolve().parent


def _load_local(name: str):
    try:
        return __import__(name)
    except ModuleNotFoundError:
        spec = importlib.util.spec_from_file_location(name, _HERE / f"{name}.py")
        if spec is None or spec.loader is None:
            raise
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module


_contract = _load_local("详细数据阶段合同")
DetailDataContractError = _contract.DetailDataContractError
canonical_report_base = _contract.canonical_report_base
finalize_item_facts = _contract.finalize_item_facts
validate_canonical_report = _contract.validate_canonical_report
validate_identity = _contract.validate_identity
_state = _load_local("账号阶段状态")
write_json_atomically = _state.write_json_atomically


_LEGACY_DIAGNOSIS_HANDOFF_FIELD = "diagnosis" + "_handoff"

_FORBIDDEN_REPORT_FIELDS = frozenset({
    "stage_run_id",
    _LEGACY_DIAGNOSIS_HANDOFF_FIELD,
    "stage_result",
    "detail_stage_result",
    "asr_ticket",
    "asr_ticket_id",
    "asr_ticket_path",
})


def canonical_report_path(
    workspace_root: str | Path,
    *,
    flow_type: str,
    run_id: str,
    task_id: str = "",
) -> Path:
    """Return the only legal canonical report path for a flow."""
    root = Path(workspace_root).expanduser().resolve()
    flow, task, run = validate_identity(
        flow_type=flow_type, task_id=task_id, run_id=run_id
    )
    flow_root = "benchmark-account-diagnose" if flow == "benchmark_account" else "self-account-diagnose"
    return root / ".meiling/staging" / flow_root / run / "detail-data/canonical-detail-report.json"


def _assert_report_identity(
    report: dict[str, Any],
    *,
    flow_type: str = "",
    task_id: str = "",
    run_id: str = "",
    account_id: str = "",
) -> None:
    flow = str(flow_type or report.get("flow_type") or "").strip()
    task = str(task_id or report.get("task_id") or "").strip()
    run = str(run_id or report.get("run_id") or "").strip()
    account = str(account_id or report.get("account_id") or "").strip()
    validate_identity(flow_type=flow, task_id=task, run_id=run)
    if flow != str(report.get("flow_type") or "").strip():
        raise DetailDataContractError("canonical report flow_type 不一致")
    if run != str(report.get("run_id") or "").strip():
        raise DetailDataContractError("canonical report run_id 不一致")
    if account and account != str(report.get("account_id") or "").strip():
        raise DetailDataContractError("canonical report account_id 不一致")


def _reject_forbidden_fields(value: Any) -> None:
    """Reject retired transport fields anywhere in a canonical report."""
    if isinstance(value, dict):
        forbidden = sorted(_FORBIDDEN_REPORT_FIELDS.intersection(value))
        if forbidden:
            raise DetailDataContractError(
                "canonical report 不得包含旧冗余字段: " + ", ".join(forbidden)
            )
        for item in value.values():
            _reject_forbidden_fields(item)
    elif isinstance(value, list):
        for item in value:
            _reject_forbidden_fields(item)


def _canonical_workspace_root(
    path: Path,
    report: dict[str, Any],
    *,
    workspace_root: str | Path | None,
) -> Path:
    """Validate that ``path`` is the unique canonical path for ``report``."""
    flow = str(report.get("flow_type") or "").strip()
    task = str(report.get("task_id") or "").strip()
    run = str(report.get("run_id") or "").strip()
    if workspace_root is not None:
        root = Path(workspace_root).expanduser().resolve()
    else:
        suffix = Path(
            ".meiling/staging/benchmark-account-diagnose"
            if flow == "benchmark_account"
            else ".meiling/staging/self-account-diagnose"
        )
        identity_suffix = suffix / run / "detail-data/canonical-detail-report.json"
        if len(path.parts) <= len(identity_suffix.parts) or tuple(path.parts[-len(identity_suffix.parts):]) != identity_suffix.parts:
            raise DetailDataContractError("canonical_report_path_mismatch")
        root = Path(*path.parts[:-len(identity_suffix.parts)])
    expected = canonical_report_path(root, flow_type=flow, task_id=task, run_id=run)
    if path != expected:
        raise DetailDataContractError("canonical_report_path_mismatch")
    return root


def _validate_report_at_path(
    path: Path,
    report: dict[str, Any],
    *,
    workspace_root: str | Path | None,
    flow_type: str = "",
    task_id: str = "",
    run_id: str = "",
    account_id: str = "",
    schema_validated: bool = False,
) -> dict[str, Any]:
    _reject_forbidden_fields(report)
    validated = report if schema_validated else validate_canonical_report(report)
    _assert_report_identity(
        validated,
        flow_type=flow_type,
        task_id=task_id,
        run_id=run_id,
        account_id=account_id,
    )
    _canonical_workspace_root(path, validated, workspace_root=workspace_root)
    return validated


def load_report(
    report_path: str | Path,
    *,
    flow_type: str = "",
    task_id: str = "",
    run_id: str = "",
    account_id: str = "",
    workspace_root: str | Path | None = None,
    validate: bool = False,
) -> dict[str, Any]:
    """Read one canonical report and perform identity/path checks.

    Canonical reports are fully validated even when legacy callers pass
    ``validate=False``.  A report read from disk is a trust boundary, not a
    partially validated transport object.
    """
    path = Path(report_path).expanduser().resolve()
    # Benchmark reports live directly below ``detail-data`` while self-account
    # reports add the run namespace below it.  The exact flow-bound path is
    # validated after decoding the report identity by ``_canonical_workspace_root``.
    if path.name != "canonical-detail-report.json" or not (
        path.parent.name == "detail-data" or path.parent.parent.name == "detail-data"
    ):
        raise DetailDataContractError("canonical_report_path_invalid")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise DetailDataContractError(f"canonical_report_read_failed:{exc}") from exc
    if not isinstance(payload, dict):
        raise DetailDataContractError("canonical report 必须是 JSON 对象")
    _validate_report_at_path(
        path,
        payload,
        workspace_root=workspace_root,
        flow_type=flow_type,
        task_id=task_id,
        run_id=run_id,
        account_id=account_id,
    )
    return payload


def write_report(
    report_path: str | Path,
    report: dict[str, Any],
    *,
    workspace_root: str | Path | None = None,
    validate: bool = True,
) -> dict[str, Any]:
    """Validate and atomically replace the canonical report."""
    if not isinstance(report, dict):
        raise DetailDataContractError("canonical report 必须是 JSON 对象")
    path = Path(report_path).expanduser().resolve()
    candidate = deepcopy(report)
    validate_canonical_report(candidate)
    _validate_report_at_path(
        path, candidate, workspace_root=workspace_root, schema_validated=True
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    write_json_atomically(path, candidate)
    return candidate


def create_report(
    *,
    workspace_root: str | Path,
    flow_type: str,
    run_id: str,
    account_id: str,
    requested_content_ids: Iterable[str],
    scope_mode: str = "",
    scope_count: int | None = None,
    task_id: str = "",
    report_path: str | Path | None = None,
    initial_fields: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Create and persist a valid initial canonical report."""
    report = canonical_report_base(
        flow_type=flow_type,
        task_id=task_id,
        run_id=run_id,
        scope_mode=scope_mode,
        scope_count=scope_count,
        requested_content_ids=requested_content_ids,
        account_id=account_id,
    )
    if initial_fields:
        report.update(deepcopy(initial_fields))
    # Producers may provide facts and delivery metadata only.  Final status and
    # counts are always recalculated here, so caller-supplied projections can
    # never become a second mutable truth.
    collection = report.get("detail_collection") if isinstance(report.get("detail_collection"), dict) else {}
    finalized = finalize_item_facts(
        collection.get("items") or [], requested_content_ids=report["requested_content_ids"]
    )
    report["detail_collection"] = {
        **collection,
        "items": finalized["items"],
        "collected_count": sum(item["base_data_available"] for item in finalized["items"]),
    }
    report["status"] = finalized["status"]
    report["counts"] = finalized["counts"]
    expected_path = canonical_report_path(
        workspace_root, flow_type=flow_type, run_id=run_id, task_id=task_id
    )
    path = Path(report_path).expanduser().resolve() if report_path is not None else expected_path
    if path != expected_path:
        raise DetailDataContractError("canonical_report_path_mismatch")
    return write_report(path, report, workspace_root=workspace_root)

