from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from datetime import datetime
from pathlib import Path, PurePosixPath
from typing import Any, Iterable, Mapping


class AccountStageStateError(RuntimeError):
    pass


RECOVERY_STATUSES = {
    "in_progress",
    "ready_for_commit",
    "waiting_for_user",
    "waiting_for_child",
    "retryable_failed",
    "completed",
}
RECOVERY_ENTRIES = {"base_data", "detail_data", "diagnosis"}
RECOVERY_ACTIONS = {
    "",
    "await_detail_scope",
    "continue_base_data",
    "retry_base_data",
    "await_base_failure_decision",
    "continue_detail_processing",
    "retry_detail_data",
    "await_detail_failure_decision",
    "continue_diagnosis",
}
RECOVERY_FIELDS = {
    "status",
    "current_entry",
    "next_action",
    "report_path",
    "followup_report_path",
    "data_source",
    "selected_content_ids",
    "unresolved_content_ids",
    "acquisition_attempt",
    "diagnosis_input_path",
    "return_to",
    "return_action",
}
RECOVERY_PATH_FIELDS = {
    "report_path",
    "followup_report_path",
    "diagnosis_input_path",
}
COMPLETION_REASONS = {"", "verified_empty"}


def _clean_ids(value: object, field: str) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, (list, tuple)):
        raise AccountStageStateError(f"{field}_invalid")
    result: list[str] = []
    for item in value:
        normalized = str(item or "").strip()
        if normalized and normalized not in result:
            result.append(normalized)
    return result


def _clean_relative_path(value: object, field: str) -> str:
    normalized = str(value or "").strip().replace("\\", "/")
    if not normalized:
        return ""
    logical = PurePosixPath(normalized)
    if logical.is_absolute() or any(part in {"", ".", ".."} for part in logical.parts):
        raise AccountStageStateError(f"recovery_path_invalid:{field}")
    return logical.as_posix()


def normalize_recovery_context(value: Mapping[str, object]) -> dict[str, Any]:
    extra = set(value) - RECOVERY_FIELDS
    if extra:
        raise AccountStageStateError("recovery_fields_invalid:" + ",".join(sorted(extra)))
    status = str(value.get("status") or "").strip()
    entry = str(value.get("current_entry") or "").strip()
    action = str(value.get("next_action") or "").strip()
    if status not in RECOVERY_STATUSES:
        raise AccountStageStateError(f"recovery_status_invalid:{status or 'empty'}")
    if entry not in RECOVERY_ENTRIES:
        raise AccountStageStateError(f"recovery_entry_invalid:{entry or 'empty'}")
    if action not in RECOVERY_ACTIONS:
        raise AccountStageStateError(f"recovery_action_invalid:{action or 'empty'}")
    attempt = value.get("acquisition_attempt", 0)
    if isinstance(attempt, bool) or not isinstance(attempt, int) or attempt not in {0, 1, 2}:
        raise AccountStageStateError("recovery_attempt_invalid")
    context: dict[str, Any] = {
        "status": status,
        "status_semantic": status,
        "current_entry": entry,
        "next_action": action,
    }
    for field in RECOVERY_PATH_FIELDS:
        normalized = _clean_relative_path(value.get(field), field)
        if normalized:
            context[field] = normalized
    for field in ("data_source", "return_to", "return_action"):
        normalized = str(value.get(field) or "").strip()
        if normalized:
            context[field] = normalized
    for field in ("selected_content_ids", "unresolved_content_ids"):
        items = _clean_ids(value.get(field), field)
        if items:
            context[field] = items
    if attempt:
        context["acquisition_attempt"] = attempt
    return context


def build_account_business_stage_state(
    *,
    stage: str,
    complete: bool,
    platform: str,
    account_id: str,
    account_name: str,
    completion_reason: str = "",
) -> dict[str, Any]:
    normalized_completion_reason = str(completion_reason or "").strip()
    if normalized_completion_reason not in COMPLETION_REASONS:
        raise AccountStageStateError(
            f"completion_reason_invalid:{normalized_completion_reason or 'empty'}"
        )
    if normalized_completion_reason and not complete:
        raise AccountStageStateError("completion_reason_requires_complete_state")
    return {
        "schema_version": "1",
        "当前阶段": str(stage).strip(),
        "当前是否完成": bool(complete),
        "completion_reason": normalized_completion_reason,
        "目标账号": {
            "平台": str(platform).strip(),
            "账号ID": str(account_id).strip(),
            "账号名": str(account_name).strip(),
        },
        "updated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
    }


def build_account_stage_state(
    *,
    stage: str,
    complete: bool,
    platform: str,
    account_id: str,
    account_name: str,
    waiting_for_user: str = "",
    next_flows: Iterable[str] = (),
    recovery_context: Mapping[str, object] | None = None,
    completion_reason: str = "",
) -> dict[str, Any]:
    state = build_account_business_stage_state(
        stage=stage,
        complete=complete,
        platform=platform,
        account_id=account_id,
        account_name=account_name,
        completion_reason=completion_reason,
    )
    state["等待用户"] = str(waiting_for_user).strip()
    state["下一步流程"] = [str(item).strip() for item in next_flows if str(item).strip()]
    if recovery_context is not None:
        state["恢复上下文"] = normalize_recovery_context(recovery_context)
    return state


def parse_bool(value: str) -> bool:
    normalized = str(value or "").strip().lower()
    if normalized == "true":
        return True
    if normalized == "false":
        return False
    raise argparse.ArgumentTypeError("只允许 true 或 false")


def resolve_state_path(workspace_root: Path, raw_path: str) -> Path:
    normalized = str(raw_path or "").strip().replace("\\", "/")
    if not normalized:
        raise AccountStageStateError("state_path_missing")
    logical = PurePosixPath(normalized)
    candidate = Path(normalized).expanduser()
    resolved = candidate.resolve() if candidate.is_absolute() else workspace_root.joinpath(*logical.parts).resolve()
    try:
        relative = resolved.relative_to(workspace_root)
    except ValueError as exc:
        raise AccountStageStateError(f"state_path_outside_workspace:{normalized}") from exc
    if tuple(relative.parts[-2:]) != ("过程记录", "当前阶段状态.json"):
        raise AccountStageStateError(f"state_path_invalid:{normalized}")
    return resolved


def write_bytes_atomically(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent)
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        if path.read_bytes() != payload:
            raise AccountStageStateError(f"post_write_mismatch:{path}")
    except Exception:
        temporary.unlink(missing_ok=True)
        raise


def write_json_atomically(path: Path, payload: Mapping[str, Any]) -> None:
    serialized = (json.dumps(payload, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    write_bytes_atomically(path, serialized)


def write_state_atomically(path: Path, state: dict[str, Any]) -> None:
    write_json_atomically(path, state)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="原子写入账号阶段状态")
    parser.add_argument("--workspace-root", required=True)
    parser.add_argument("--state-path", required=True)
    parser.add_argument("--stage", required=True)
    parser.add_argument("--complete", required=True, type=parse_bool)
    parser.add_argument("--platform", default="")
    parser.add_argument("--account-id", required=True)
    parser.add_argument("--account-name", default="")
    parser.add_argument("--waiting-for-user", default="")
    parser.add_argument("--next-flow", action="append", default=[])
    parser.add_argument("--completion-reason", default="")
    return parser.parse_args()


def run(args: argparse.Namespace) -> dict[str, Any]:
    workspace_root = Path(args.workspace_root).expanduser().resolve()
    if not workspace_root.is_dir():
        raise AccountStageStateError(f"workspace_root_invalid:{workspace_root}")
    if not str(args.stage or "").strip():
        raise AccountStageStateError("stage_missing")
    if not str(args.account_id or "").strip():
        raise AccountStageStateError("account_id_missing")
    state_path = resolve_state_path(workspace_root, args.state_path)
    state = build_account_stage_state(
        stage=args.stage,
        complete=args.complete,
        platform=args.platform,
        account_id=args.account_id,
        account_name=args.account_name,
        waiting_for_user=args.waiting_for_user,
        next_flows=args.next_flow,
        completion_reason=args.completion_reason,
    )
    write_state_atomically(state_path, state)
    return {
        "ok": True,
        "status": "committed",
        "state_path": state_path.relative_to(workspace_root).as_posix(),
    }


def main() -> int:
    try:
        result = run(parse_args())
    except (OSError, ValueError, AccountStageStateError) as exc:
        print(json.dumps({"ok": False, "status": "failed", "error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
