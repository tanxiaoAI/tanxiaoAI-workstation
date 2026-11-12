"""详细数据阶段共享运行合同。"""
from __future__ import annotations

from copy import deepcopy
import re
from pathlib import Path
from urllib.parse import urlsplit
from typing import Any, Iterable

CONTRACT_VERSION = 1
CHROME_CONTRACT_VERSION = 2
FLOW_TYPES = {"benchmark_account", "self_account"}
DETAIL_STATUSES = {"completed", "partial", "failed"}
STATUSES = DETAIL_STATUSES
CHROME_STATUSES = {"待采集", "成功", "部分成功", "失败"}
CHROME_FIELDS = ("平台", "账号ID", "媒介形式", "发布时间", "点赞数", "收藏数", "分享数", "评论数")
VIDEO_FIELD = "视频下载链接"
CHROME_BODY_FIELDS = ("正文文本", "正文获取状态")
# 「正文获取状态」的取值枚举。必须同时写进产出的 schema（chrome_result_schema）：
# 只写 "type": "string" 的话，执行者会填自由文本，提交时被 validate_chrome_item 拒绝
# （2026-09-14 实测：填写「无需正文」被拒，报 `Chrome 正文获取状态无效`，且错误信息不列可选值）。
# 空字符串表示本字段未填写，与校验函数保持一致。
CHROME_BODY_STATUSES = ("", "已获取", "确认无正文", "不适用", "获取失败", "success", "unavailable", "failed")
CANONICAL_ITEM_FACT_FIELDS = ("content_id", "base_data_available", "missing_deliverables", "blocking_errors")
CANONICAL_STATUSES = ("completed", "partial", "failed")


class DetailDataContractError(ValueError):
    pass


ERROR_DEFINITIONS = {
    "publish_time_unavailable": (False, False),
    "title_unavailable": (False, False),
    "video_transcript_unavailable": (False, False),
    "content_identity_missing": (True, True),
    "chrome_content_identity_mismatch": (True, True),
    "chrome_content_identity_unconfirmed": (True, True),
    "content_evidence_missing": (True, True),
    "media_type_unknown": (True, False),
}


def structured_error(code: str, message: str, *, blocking: bool | None = None, retryable: bool | None = None) -> dict[str, Any]:
    """Return the stable error contract consumed by batch and dispatch layers."""
    normalized = _text(code)
    default_blocking, default_retryable = ERROR_DEFINITIONS.get(normalized, (False, False))
    return {
        "code": normalized,
        "blocking": default_blocking if blocking is None else bool(blocking),
        "retryable": default_retryable if retryable is None else bool(retryable),
        "message": _text(message),
    }


def has_blocking_errors(errors: Any) -> bool:
    return any(isinstance(item, dict) and item.get("blocking") is True for item in (errors or []))


def _normalize_fact(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise DetailDataContractError("canonical 单条事实必须是对象")
    unknown = sorted(set(value) - set(CANONICAL_ITEM_FACT_FIELDS))
    if unknown:
        raise DetailDataContractError("canonical 单条事实不得包含状态副本: " + ", ".join(unknown))
    content_id = _text(value.get("content_id"))
    if not content_id:
        raise DetailDataContractError("canonical 单条事实缺少 content_id")
    missing = value.get("missing_deliverables", [])
    errors = value.get("blocking_errors", [])
    if not isinstance(missing, list) or any(not _text(item) for item in missing):
        raise DetailDataContractError("canonical 单条事实 missing_deliverables 无效")
    if not isinstance(errors, list) or any(not isinstance(item, dict) for item in errors):
        raise DetailDataContractError("canonical 单条事实 blocking_errors 无效")
    if not isinstance(value.get("base_data_available"), bool):
        raise DetailDataContractError("canonical 单条事实 base_data_available 必须是布尔值")
    return {
        "content_id": content_id,
        "base_data_available": value["base_data_available"],
        "missing_deliverables": list(dict.fromkeys(_text(item) for item in missing)),
        "blocking_errors": deepcopy(errors),
    }


def derive_item_status(fact: dict[str, Any]) -> str:
    """Derive one final status solely from immutable per-item facts."""
    normalized = _normalize_fact(fact)
    if not normalized["base_data_available"] or has_blocking_errors(normalized["blocking_errors"]):
        return "failed"
    return "partial" if normalized["missing_deliverables"] else "completed"


def finalize_item_facts(facts: Iterable[dict[str, Any]], *, requested_content_ids: Iterable[str] = ()) -> dict[str, Any]:
    """Return the only canonical status/counts projection for a fact set."""
    rows = [_normalize_fact(item) for item in facts]
    requested = list(dict.fromkeys(_text(value) for value in requested_content_ids if _text(value)))
    if requested:
        if len(rows) != len(requested) or {item["content_id"] for item in rows} != set(requested):
            raise DetailDataContractError("canonical 单条事实范围不一致")
        order = {content_id: index for index, content_id in enumerate(requested)}
        rows.sort(key=lambda item: order[item["content_id"]])
    statuses = [derive_item_status(item) for item in rows]
    counts = {status: statuses.count(status) for status in CANONICAL_STATUSES}
    if not rows or all(status == "failed" for status in statuses):
        batch_status = "failed" if rows else "partial"
    elif all(status == "completed" for status in statuses):
        batch_status = "completed"
    else:
        batch_status = "partial"
    return {"items": rows, "statuses": statuses, "counts": counts, "status": batch_status}


def validate_detail_identity(*, flow_type: str = "", task_id: str = "", run_id: str = "", account_id: str = "") -> tuple[str, str, str, str]:
    """Validate the two public detail-data modes.

    Both modes are run-scoped: `run_id` alone binds a detail-data batch.
    Self-account no longer requires the retired scheduler task id; the parameter
    is still accepted so existing callers keep working and is ignored for self.
    """
    flow, task, run, account = _text(flow_type), _text(task_id), _text(run_id), _text(account_id)
    if flow == "benchmark_account":
        if task:
            raise DetailDataContractError("对标详细数据模式不得提供 task_id")
        if not run:
            raise DetailDataContractError("对标详细数据模式必须提供 run_id")
    elif flow == "self_account":
        if not run:
            raise DetailDataContractError("我方详细数据模式必须提供 run_id")
    else:
        raise DetailDataContractError("详细数据模式必须是 benchmark_account 或 self_account")
    if not account:
        raise DetailDataContractError("详细数据模式必须提供 account_id")
    _validate_path_component(run, "run_id")
    return flow, "", run, account


def _text(value: Any) -> str:
    return str(value or "").strip()


def _contains_legacy_key(value: Any) -> bool:
    if isinstance(value, dict):
        return "stage_run_id" in value or any(_contains_legacy_key(item) for item in value.values())
    if isinstance(value, list):
        return any(_contains_legacy_key(item) for item in value)
    return False


def validate_identity(*, flow_type: str = "", task_id: str = "", run_id: str = "", standalone: bool = False) -> tuple[str, str, str]:
    """Validate public stage identity.

    Both flow types are run-scoped: `run_id` alone binds the stage. Benchmark
    still rejects an explicit task id; self accepts but ignores it, because the
    retired scheduler task id is no longer part of the self contract.
    """
    flow, task, run = _text(flow_type), _text(task_id), _text(run_id)
    if standalone and not flow and not task:
        if not run:
            raise DetailDataContractError("独立执行器必须提供 run_id")
        _validate_path_component(run, "run_id")
        return "", "", run
    if flow not in FLOW_TYPES:
        raise DetailDataContractError("flow_type 必须是 benchmark_account 或 self_account")
    if not run:
        raise DetailDataContractError("公开阶段调用必须提供 run_id")
    _validate_path_component(run, "run_id")
    if flow == "benchmark_account" and task:
        raise DetailDataContractError("对标公开阶段不得提供 task_id")
    return flow, task, run


def _validate_path_component(value: str, field: str) -> None:
    """Reject identity values that could escape their staging namespace."""
    if value in {".", ".."} or "/" in value or "\\" in value or "\x00" in value:
        raise DetailDataContractError(f"{field} 不得包含路径分隔符或目录跳转")


def canonical_report_relative_path(*, flow_type: str, run_id: str, task_id: str = "") -> str:
    """Return the sole workspace-relative path allowed for a canonical report."""
    flow, task, run = validate_identity(flow_type=flow_type, task_id=task_id, run_id=run_id)
    root_name = "benchmark-account-diagnose" if flow == "benchmark_account" else "self-account-diagnose"
    return f".meiling/staging/{root_name}/{run}/detail-data/canonical-detail-report.json"


def validate_canonical_report_path(
    path: str | Path,
    *,
    workspace_root: str | Path,
    flow_type: str,
    run_id: str,
    task_id: str = "",
) -> Path:
    """Require a canonical path to match its flow namespace exactly."""
    root = Path(workspace_root).expanduser().resolve()
    candidate = Path(path).expanduser().resolve()
    expected = (root / canonical_report_relative_path(
        flow_type=flow_type, task_id=task_id, run_id=run_id
    )).resolve()
    if candidate != expected:
        raise DetailDataContractError("canonical_report_path_mismatch")
    return candidate


def migrate_legacy_identity(payload: dict[str, Any], *, flow_type: str = "") -> dict[str, Any]:
    """Reject legacy identity objects; new flows never migrate them."""
    if not isinstance(payload, dict):
        raise DetailDataContractError("身份对象必须是 JSON 对象")
    if _contains_legacy_key(payload):
        raise DetailDataContractError("新对象不得包含 stage_run_id")
    result = deepcopy(payload)
    if flow_type and not _text(result.get("flow_type")):
        result["flow_type"] = _text(flow_type)
    return result


def canonical_report_base(*, flow_type: str, task_id: str = "", run_id: str, scope_mode: str, scope_count: int | None, requested_content_ids: Iterable[str], account_id: str = "") -> dict[str, Any]:
    validate_identity(flow_type=flow_type, task_id=task_id, run_id=run_id)
    ids = list(dict.fromkeys(_text(value) for value in requested_content_ids if _text(value)))
    if not ids:
        raise DetailDataContractError("requested_content_ids 不能为空")
    return {
        "schema_version": CONTRACT_VERSION,
        "flow_type": _text(flow_type), **({"task_id": _text(task_id)} if _text(task_id) else {}), "run_id": _text(run_id),
        "stage": "detail_data", "status": "partial", "account_id": _text(account_id),
        "scope_mode": _text(scope_mode), "scope_count": scope_count,
        "requested_content_ids": ids, "processed_content_ids": [],
        "detail_collection": {
            "items": [
                {"content_id": content_id, "base_data_available": False,
                 "missing_deliverables": [], "blocking_errors": []}
                for content_id in ids
            ],
            "collected_count": 0,
        },
        "followup_delivery": {"items": [], "completed_count": 0, "missing_count": 0},
        "retry_content_ids": [], "missing_deliverables": [], "artifacts": {},
        "counts": {"completed": 0, "partial": 0, "failed": len(ids)},
    }


def validate_canonical_report(report: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(report, dict):
        raise DetailDataContractError("canonical report 必须是 JSON 对象")
    if _contains_legacy_key(report):
        raise DetailDataContractError("canonical report 不得包含 stage_run_id")
    required = ("schema_version", "flow_type", "run_id", "stage", "status")
    missing = [field for field in required if not _text(report.get(field))]
    if missing:
        raise DetailDataContractError("canonical report 缺少字段: " + ", ".join(missing))
    if report.get("schema_version") != CONTRACT_VERSION:
        raise DetailDataContractError("canonical report schema_version 无效")
    flow = _text(report["flow_type"])
    task = _text(report.get("task_id"))
    validate_identity(flow_type=flow, task_id=task, run_id=_text(report["run_id"]))
    if not _text(report.get("account_id")):
        raise DetailDataContractError("canonical report 缺少 account_id")
    # Self-account canonical reports are run-scoped: the retired scheduler task id
    # has no producer, so its absence is legal here.  Same rule as `validate_identity`.
    if report.get("stage") != "detail_data":
        raise DetailDataContractError("canonical report 的 stage 必须是 detail_data")
    if report.get("status") not in DETAIL_STATUSES:
        raise DetailDataContractError("canonical report status 无效")
    redundant = {
        "delivery_status",
        "can_continue_diagnosis",
        "diagnosis_ready",
        "stage_status",
        "failed_items",
        "unresolved_content_ids",
        "diagnosis_handoff",
        "diagnosis_evidence_mode",
        "stage_result",
        # Content-list completeness belongs to the immutable base-data report;
        # accepting it here would create a model-editable second truth.
        "content_list_completeness",
        "content_list_may_be_incomplete",
        "content_list_incomplete",
    }
    present = sorted(field for field in redundant if field in report)
    if present:
        raise DetailDataContractError("canonical report 不得包含冗余状态字段: " + ", ".join(present))
    requested = report.get("requested_content_ids")
    if not isinstance(requested, list) or not requested or any(not _text(item) for item in requested):
        raise DetailDataContractError("requested_content_ids 必须是非空字符串数组")
    collection = report.get("detail_collection")
    if not isinstance(collection, dict) or not isinstance(collection.get("items"), list):
        raise DetailDataContractError("canonical report detail_collection.items 必须是数组")
    if "success_count" in collection or "failed_count" in collection:
        raise DetailDataContractError("canonical report detail_collection 不得包含旧计数字段")
    if "evidence_coverage" in report:
        raise DetailDataContractError("canonical report 不得包含 evidence_coverage")
    if "collected_count" not in collection or not isinstance(collection.get("collected_count"), int):
        raise DetailDataContractError("canonical report detail_collection 缺少 collected_count")
    finalized = finalize_item_facts(collection["items"], requested_content_ids=requested)
    counts = report.get("counts")
    if counts != finalized["counts"] or report.get("status") != finalized["status"]:
        raise DetailDataContractError("canonical report status/counts 必须由逐条事实推导")
    if collection["collected_count"] != sum(item["base_data_available"] for item in finalized["items"]):
        raise DetailDataContractError("canonical report collected_count 不一致")
    for key in ("followup_delivery", "artifacts"):
        if not isinstance(report.get(key), dict):
            raise DetailDataContractError(f"canonical report {key} 必须是对象")
    for key in ("retry_content_ids", "missing_deliverables", "processed_content_ids"):
        if not isinstance(report.get(key), list):
            raise DetailDataContractError(f"canonical report {key} 必须是数组")
    return report


def chrome_result_schema() -> dict[str, Any]:
    fields = {field: {"type": "string"} for field in (*CHROME_FIELDS, VIDEO_FIELD, *CHROME_BODY_FIELDS)}
    # 唯一的枚举字段，必须显式写出来，见 CHROME_BODY_STATUSES 的注释
    fields["正文获取状态"] = {"type": "string", "enum": list(CHROME_BODY_STATUSES)}
    item = {
        "type": "object", "additionalProperties": False,
        "required": ["target_index", "content_id", "request_url", "observed_content_identity", "status", "fields", "failure_reason"],
        "properties": {
            "target_index": {"type": "integer", "minimum": 1}, "content_id": {"type": "string", "minLength": 1},
            "request_url": {"type": "string", "minLength": 1, "pattern": "^[Hh][Tt][Tt][Pp][Ss]?://\\S+$"}, "observed_content_identity": {"type": "string"}, "status": {"enum": sorted(CHROME_STATUSES)},
            "fields": {"type": "object", "additionalProperties": False, "properties": fields},
            "failure_reason": {"type": "string"},
        },
        "allOf": [
            {
                "if": {"properties": {"status": {"const": "成功"}}},
                "then": {"properties": {"fields": {"required": list(CHROME_FIELDS)}, "failure_reason": {"const": ""}}},
            },
            {
                "if": {"properties": {"status": {"enum": ["部分成功", "失败"]}}},
                "then": {"properties": {"failure_reason": {"minLength": 1}}},
            },
            {
                "if": {"properties": {"status": {"const": "成功"}, "fields": {"properties": {"媒介形式": {"const": "视频"}}}}},
                "then": {"properties": {"fields": {"required": [VIDEO_FIELD]}}},
            },
        ],
    }
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema", "$id": "meiling://detail-data/chrome-result.schema.json",
        "type": "object", "additionalProperties": False, "required": ["schema_version", "data_source", "items"],
        "properties": {"schema_version": {"const": CHROME_CONTRACT_VERSION}, "data_source": {"const": "chrome_devtools"}, "items": {"type": "array", "items": item}},
    }


def validate_chrome_item(item: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(item, dict):
        raise DetailDataContractError("Chrome 单条结果必须是对象")
    unknown = sorted(set(item) - {"target_index", "content_id", "request_url", "observed_content_identity", "status", "fields", "failure_reason"})
    if unknown:
        raise DetailDataContractError("Chrome 结果存在未知字段: " + ", ".join(unknown))
    if "observed_content_identity" not in item:
        raise DetailDataContractError("Chrome 结果缺少 observed_content_identity")
    if not is_valid_http_url(item.get("request_url")):
        raise DetailDataContractError("Chrome 结果 request_url 必须是有效的 http(s) URL")
    if not isinstance(item.get("observed_content_identity"), str):
        raise DetailDataContractError("Chrome 结果 observed_content_identity 必须是字符串")
    status, fields, reason = _text(item.get("status")), item.get("fields"), _text(item.get("failure_reason"))
    if status not in CHROME_STATUSES - {"待采集"}:
        raise DetailDataContractError("Chrome 结果状态无效")
    if not isinstance(fields, dict):
        raise DetailDataContractError("Chrome 结果 fields 必须是对象")
    unknown_fields = sorted(set(fields) - set((*CHROME_FIELDS, VIDEO_FIELD, *CHROME_BODY_FIELDS)))
    if unknown_fields:
        raise DetailDataContractError("Chrome fields 存在未知字段: " + ", ".join(unknown_fields))
    if status == "成功":
        missing = [field for field in CHROME_FIELDS if not _text(fields.get(field))]
        if missing:
            raise DetailDataContractError("Chrome 成功结果缺少字段: " + ", ".join(missing))
        if _text(fields.get("媒介形式")) == "视频" and not _text(fields.get(VIDEO_FIELD)):
            raise DetailDataContractError("Chrome 视频成功结果缺少字段: 视频下载链接")
        if reason:
            raise DetailDataContractError("Chrome 成功结果不得包含 failure_reason")
        body_status = _text(fields.get("正文获取状态"))
        if body_status and body_status not in CHROME_BODY_STATUSES:
            raise DetailDataContractError(
                "Chrome 正文获取状态无效，可选值：" + " / ".join(v for v in CHROME_BODY_STATUSES if v)
            )
        if body_status in {"已获取", "success"} and not _text(fields.get("正文文本")):
            raise DetailDataContractError("Chrome 正文状态为成功但正文为空")
    elif not reason:
        raise DetailDataContractError("Chrome 非成功结果必须提供 failure_reason")
    return item


def is_valid_http_url(value: Any) -> bool:
    text = _text(value)
    if not text or re.search(r"\s", text):
        return False
    parsed = urlsplit(text)
    return parsed.scheme.lower() in {"http", "https"} and bool(parsed.netloc)


def retryable_content_ids(report: dict[str, Any]) -> list[str]:
    if not isinstance(report, dict):
        raise DetailDataContractError("retry report 必须是 JSON 对象")
    if _contains_legacy_key(report) or "unresolved_content_ids" in report or "failed_items" in report:
        raise DetailDataContractError("retry report 使用了旧合同字段")
    raw = report.get("retry_content_ids")
    if not isinstance(raw, list):
        raise DetailDataContractError("retry report 缺少 retry_content_ids")
    return list(dict.fromkeys(_text(value) for value in raw if _text(value)))
