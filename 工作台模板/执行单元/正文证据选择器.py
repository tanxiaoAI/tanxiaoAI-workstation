"""统一选择正文证据，并对每个候选做确定性的身份校验。"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Mapping

SOURCES = {"formal_transcript", "platform_caption", "asr", "unavailable"}
STATUSES = {"success", "unavailable", "failed"}


def video_url_hash(url: object) -> str:
    value = str(url or "").strip()
    return hashlib.sha256(value.encode("utf-8")).hexdigest() if value else ""


def normalize_status(value: object) -> str:
    marker = str(value or "").strip().casefold()
    return {
        "已获取": "success", "success": "success", "ok": "success", "成功": "success",
        "确认无正文": "unavailable", "无字幕": "unavailable", "unavailable": "unavailable",
        "不适用": "unavailable", "获取失败": "failed", "失败": "failed", "failed": "failed",
    }.get(marker, marker)


def _first(node: Mapping[str, Any], *keys: str) -> str:
    for key in keys:
        value = node.get(key)
        if value is not None and str(value).strip():
            return str(value).strip()
    return ""


def _payload_node(payload: Mapping[str, Any]) -> Mapping[str, Any]:
    nested = payload.get("data")
    return nested if isinstance(nested, Mapping) else payload


def platform_caption_evidence(
    payload: Mapping[str, Any] | None,
    *,
    content_id: str,
    run_id: str = "",
    video_url: str = "",
) -> dict[str, Any]:
    """Normalize a Chrome/API payload into the shared platform-caption fact."""
    if not isinstance(payload, Mapping):
        return {"source": "unavailable", "status": "unavailable", "failure_reason": "平台字幕结果缺失"}
    node = _payload_node(payload)
    # v1 is written from a staging-only raw-detail capture.  It is a
    # provenance-bearing fact, not a generic caption-shaped payload: do not
    # silently downgrade its binding contract when it reaches a consumer.
    if node.get("binding_version") == 1:
        evidence = node.get("evidence")
        if not isinstance(evidence, Mapping):
            return {"source": "platform_caption", "status": "failed", "failure_reason": "平台字幕绑定证据缺失"}
        expected_content_id = str(content_id or "").strip()
        expected_run_id = str(run_id or "").strip()
        expected_hash = video_url_hash(video_url)
        observed_content_id = _first(evidence, "content_id")
        observed_run_id = _first(evidence, "run_id")
        observed_hash = _first(evidence, "url_hash")
        raw_detail = evidence.get("raw_detail")
        observed_native_id = _first(evidence, "native_content_id")
        if not expected_content_id or observed_content_id != expected_content_id:
            return {"source": "platform_caption", "status": "failed", "failure_reason": "平台字幕内容身份不一致"}
        if not expected_run_id or observed_run_id != expected_run_id:
            return {"source": "platform_caption", "status": "failed", "failure_reason": "平台字幕运行标识不一致"}
        if not expected_hash or observed_hash.casefold() != expected_hash:
            return {"source": "platform_caption", "status": "failed", "failure_reason": "平台字幕视频身份不一致"}
        if (
            not isinstance(raw_detail, Mapping)
            or not _first(raw_detail, "path")
            or not _first(raw_detail, "sha256")
            or not _first(raw_detail, "native_content_id")
            or not observed_native_id
            or observed_native_id != _first(raw_detail, "native_content_id")
            or _first(node, "content_id") != expected_content_id
            or _first(node, "run_id") != expected_run_id
            or _first(node, "url_hash").casefold() != expected_hash
        ):
            return {"source": "platform_caption", "status": "failed", "failure_reason": "平台字幕绑定证据不完整"}
        fields = node.get("fields") if isinstance(node.get("fields"), Mapping) else node
        text = _first(fields, "正文文本", "字幕文本", "caption_text", "caption", "transcript", "text", "body")
        status = normalize_status(_first(fields, "正文获取状态", "字幕状态", "caption_status", "status"))
        if text and status == "success":
            return {
                "source": "platform_caption", "status": "success", "text": text,
                "evidence": dict(evidence), "failure_reason": "",
            }
        if status == "failed":
            return {"source": "platform_caption", "status": "failed", "evidence": dict(evidence), "failure_reason": _first(fields, "失败原因", "error", "message") or "平台字幕获取失败"}
        return {"source": "platform_caption", "status": "unavailable", "evidence": dict(evidence), "failure_reason": _first(fields, "失败原因", "error", "message") or "平台未提供字幕"}
    returned_id = _first(node, "content_id", "内容ID")
    if returned_id and returned_id != str(content_id or "").strip():
        return {"source": "platform_caption", "status": "failed", "failure_reason": "平台字幕内容身份不一致"}
    returned_run = _first(node, "run_id", "运行标识")
    if run_id and returned_run and returned_run != str(run_id).strip():
        return {"source": "platform_caption", "status": "failed", "failure_reason": "平台字幕运行标识不一致"}
    expected_hash = video_url_hash(video_url)
    observed_hash = _first(node, "url_hash", "视频URL哈希", "video_url_hash")
    observed_url = _first(node, "video_url", "视频下载链接")
    if expected_hash and observed_url and video_url_hash(observed_url) != expected_hash:
        return {"source": "platform_caption", "status": "failed", "failure_reason": "平台字幕视频身份不一致"}
    if expected_hash and observed_hash and observed_hash.casefold() != expected_hash:
        return {"source": "platform_caption", "status": "failed", "failure_reason": "平台字幕视频身份不一致"}
    fields = node.get("fields") if isinstance(node.get("fields"), Mapping) else node
    text = _first(fields, "正文文本", "字幕文本", "caption_text", "caption", "transcript", "text", "body")
    status = normalize_status(_first(fields, "正文获取状态", "字幕状态", "caption_status", "status"))
    if text:
        return {
            "source": "platform_caption", "status": "success", "text": text,
            "evidence": {"content_id": str(content_id).strip(), "run_id": str(run_id).strip(), "url_hash": expected_hash or observed_hash},
            "failure_reason": "",
        }
    if status == "failed":
        return {"source": "platform_caption", "status": "failed", "failure_reason": _first(fields, "失败原因", "error", "message") or "平台字幕获取失败"}
    if status in {"success", "unavailable"}:
        return {"source": "platform_caption", "status": "unavailable", "failure_reason": "平台字幕为空" if status == "success" else "平台未提供字幕"}
    return {"source": "platform_caption", "status": "unavailable", "failure_reason": "平台字幕未取得"}


def choose_transcript(
    *,
    content_id: str,
    run_id: str = "",
    video_url: str = "",
    formal_text: str = "",
    formal_path: str = "",
    caption: Mapping[str, Any] | None = None,
    asr_text: str = "",
    asr_status: str = "",
    asr_failure_reason: str = "",
) -> dict[str, Any]:
    """Apply the only supported正文证据优先级."""
    if str(formal_text or "").strip() or str(formal_path or "").strip():
        return {"source": "formal_transcript", "status": "success", "text": str(formal_text or "").strip(), "text_path": str(formal_path or "").strip(), "evidence": {"content_id": content_id, "run_id": run_id}, "failure_reason": ""}
    caption_fact = platform_caption_evidence(caption, content_id=content_id, run_id=run_id, video_url=video_url) if caption is not None else {"status": "unavailable", "source": "platform_caption", "failure_reason": "平台字幕未取得"}
    if caption_fact.get("status") == "success":
        return caption_fact
    if str(asr_text or "").strip() and normalize_status(asr_status or "success") == "success":
        return {"source": "asr", "status": "success", "text": str(asr_text).strip(), "evidence": {"content_id": content_id, "run_id": run_id, "url_hash": video_url_hash(video_url)}, "failure_reason": ""}
    asr_status_normalized = normalize_status(asr_status)
    if asr_status_normalized == "failed" or asr_failure_reason:
        return {"source": "asr", "status": "failed", "evidence": {"content_id": content_id, "run_id": run_id, "url_hash": video_url_hash(video_url)}, "failure_reason": str(asr_failure_reason or "ASR 执行失败").strip()}
    return {"source": "unavailable", "status": "unavailable", "evidence": {"content_id": content_id, "run_id": run_id, "url_hash": video_url_hash(video_url)}, "failure_reason": str(asr_failure_reason or caption_fact.get("failure_reason") or "正文缺口").strip()}


def read_json_object(path: str | Path) -> dict[str, Any]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("正文证据必须是 JSON 对象")
    return payload
