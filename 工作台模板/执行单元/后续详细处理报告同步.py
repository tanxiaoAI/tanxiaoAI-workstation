#!/usr/bin/env python3
from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Dict, List

from 权限错误 import permission_error_code
from 账号阶段状态 import write_json_atomically


RECOVERY_CLASSIFICATIONS = [
    "正常视频_直链过期",
    "正常视频_ASR远程拉流被拦",
    "正文型样本_article_or_text",
    "图文或无声占位_不可ASR",
    "详情API整批失败",
    "必要输入缺失",
    "未知类型_需要人工确认",
]


def output_file_is_valid(path: Path) -> bool:
    return path.exists() and path.is_file() and path.stat().st_size > 0


def read_csv_rows(csv_path: Path) -> List[Dict[str, str]]:
    with csv_path.open("r", encoding="utf-8-sig", newline="") as handle:
        return [dict(row) for row in csv.DictReader(handle)]


def build_row_index(rows: List[Dict[str, str]], key: str) -> Dict[str, Dict[str, str]]:
    index: Dict[str, Dict[str, str]] = {}
    for row in rows:
        value = (row.get(key) or "").strip()
        if value:
            index[value] = row
    return index


def build_content_library_key(row: Dict[str, str]) -> str:
    return (row.get("内容ID") or "").strip()


def build_content_library_index(rows: List[Dict[str, str]]) -> Dict[str, Dict[str, str]]:
    index: Dict[str, Dict[str, str]] = {}
    for row in rows:
        key = build_content_library_key(row)
        if key:
            index[key] = row
    return index


def get_item_recovery_meta(item_result: Dict[str, object]) -> Dict[str, object]:
    meta = item_result.get("recovery_meta", {})
    recovery_meta = dict(meta) if isinstance(meta, dict) else {}
    for field_name in [
        "failure_classification",
        "classification_evidence",
        "recovery_path",
        "recovery_status",
    ]:
        if field_name in item_result and field_name not in recovery_meta:
            recovery_meta[field_name] = item_result[field_name]
    return recovery_meta


def validate_item_consistency(
    item_result: Dict[str, object],
    *,
    content_list_index: Dict[str, Dict[str, str]],
    content_library_index: Dict[str, Dict[str, str]],
) -> List[str]:
    issues: List[str] = []
    content_id = str(item_result.get("content_id", "")).strip()
    content_dir = str(item_result.get("content_dir", "")).strip()
    result_json = str(item_result.get("result_json", "")).strip()
    if not content_id:
        return ["缺少 content_id，无法校验"]

    content_list_row = content_list_index.get(content_id)
    if content_list_row is None:
        issues.append("内容列表缺少该内容行")
    content_library_key = build_content_library_key(content_list_row or {})
    content_library_row = content_library_index.get(content_library_key) if content_library_key else None
    if content_library_row is None:
        issues.append("内容库缺少该内容行")

    if result_json and not output_file_is_valid(Path(result_json)):
        issues.append("过程记录中的执行结果.json 缺失或为空")

    if not content_dir:
        return issues

    expected_files = {
        "封面图路径": Path(content_dir) / "封面图.png",
        "视频文案路径": Path(content_dir) / "视频文案.md",
    }

    for row_name, row in [("内容列表", content_list_row), ("内容库", content_library_row)]:
        if row is None:
            continue
        for field_name, expected_path in expected_files.items():
            if field_name not in row:
                continue
            actual_path = (row.get(field_name) or "").strip()
            file_exists = output_file_is_valid(expected_path)
            if actual_path:
                actual_file = Path(actual_path)
                if not output_file_is_valid(actual_file):
                    issues.append(f"{row_name} 的 {field_name} 指向文件不存在或为空")
                elif actual_file != expected_path:
                    issues.append(f"{row_name} 的 {field_name} 与内容目录实际文件路径不一致")
            elif file_exists:
                issues.append(f"{row_name} 的 {field_name} 为空，但内容目录中已存在文件")

    return issues


def run_consistency_check(
    *,
    items: List[Dict[str, object]],
    content_list_path: Path,
    content_library_path: Path,
) -> Dict[str, object]:
    content_list_rows = read_csv_rows(content_list_path)
    content_library_rows = read_csv_rows(content_library_path)
    content_list_index = build_row_index(content_list_rows, "内容ID")
    content_library_index = build_content_library_index(content_library_rows)
    validation_items = []
    issue_count = 0
    for item_result in items:
        issues = validate_item_consistency(
            item_result,
            content_list_index=content_list_index,
            content_library_index=content_library_index,
        )
        if issues:
            issue_count += len(issues)
        validation_items.append(
            {
                "content_id": item_result.get("content_id", ""),
                "status": "通过" if not issues else "异常",
                "issues": issues,
            }
        )
    return {
        "status": "通过" if issue_count == 0 else "异常",
        "checked_count": len(validation_items),
        "issue_count": issue_count,
        "items": validation_items,
    }


def summarize_items(
    *,
    account_id: str,
    report_path: Path,
    run_mode: str,
    requested_count: int,
    items: List[Dict[str, object]],
    content_list_path: Path,
    content_library_path: Path,
    skip_consistency_check: bool = False,
) -> Dict[str, object]:
    summary = {
        "account_id": account_id,
        "requested_count": requested_count,
        "processed_count": len(items),
        "cover_success_count": 0,
        "transcript_success_count": 0,
        "transcript_degraded_count": 0,
        "degraded_content_ids": [],
        "refresh_attempted_count": 0,
        "refresh_success_count": 0,
        "refreshed_content_ids": [],
        "followup_success_content_ids": [],
        "failed_items": [],
        "errors": [],
        "items": items,
        "report_json": str(report_path),
        "run_mode": run_mode,
        "recovery_summary": {
            "attempted_count": 0,
            "success_count": 0,
            "failed_count": 0,
            "classification_counts": {name: 0 for name in RECOVERY_CLASSIFICATIONS},
        },
    }

    refreshed_ids: List[str] = []
    followup_success_ids: List[str] = []
    for item_result in items:
        content_id = str(item_result.get("content_id", "")).strip()
        steps = item_result.get("steps", {}) if isinstance(item_result.get("steps"), dict) else {}
        if isinstance(steps.get("下载封面图"), dict) and steps["下载封面图"].get("status") == "success":
            summary["cover_success_count"] += 1
        if isinstance(steps.get("获取视频文案"), dict) and steps["获取视频文案"].get("status") == "success":
            summary["transcript_success_count"] += 1
        if isinstance(steps.get("获取视频文案"), dict) and steps["获取视频文案"].get("status") == "degraded":
            summary["transcript_degraded_count"] += 1
            if content_id and content_id not in summary["degraded_content_ids"]:
                summary["degraded_content_ids"].append(content_id)
        if str(item_result.get("status", "")).strip() != "失败" and content_id and content_id not in followup_success_ids:
            followup_success_ids.append(content_id)

        failures = item_result.get("failures", [])
        structured_errors = item_result.get("errors") if isinstance(item_result.get("errors"), list) else []
        summary["errors"].extend(
            {**error, "content_id": content_id}
            for error in structured_errors
            if isinstance(error, dict)
        )
        blocking_errors = [
            error for error in structured_errors
            if isinstance(error, dict) and error.get("blocking") is True
        ]
        if blocking_errors or (failures and not structured_errors):
            failed_item = {
                "content_id": content_id,
                "title": item_result.get("title", ""),
                "failures": failures,
                "errors": blocking_errors,
            }
            recovery_meta_for_failure = get_item_recovery_meta(item_result)
            if recovery_meta_for_failure:
                failed_item.update(
                    {
                        "failure_classification": recovery_meta_for_failure.get("failure_classification", ""),
                        "classification_evidence": recovery_meta_for_failure.get("classification_evidence", []),
                        "recovery_path": recovery_meta_for_failure.get("recovery_path", ""),
                        "recovery_status": recovery_meta_for_failure.get("recovery_status", ""),
                    }
                )
            summary["failed_items"].append(failed_item)

        recovery_meta = get_item_recovery_meta(item_result)
        recovery_classification = str(recovery_meta.get("failure_classification") or "").strip()
        recovery_path = str(recovery_meta.get("recovery_path") or "").strip()
        recovery_status = str(recovery_meta.get("recovery_status") or "").strip()
        if recovery_classification or recovery_path or recovery_status:
            recovery_summary = summary["recovery_summary"]
            recovery_summary["attempted_count"] += 1
            if recovery_classification:
                classification_counts = recovery_summary["classification_counts"]
                if recovery_classification not in classification_counts:
                    classification_counts[recovery_classification] = 0
                classification_counts[recovery_classification] += 1
            if recovery_status == "成功":
                recovery_summary["success_count"] += 1
            elif recovery_status == "失败":
                recovery_summary["failed_count"] += 1

        refresh_meta = item_result.get("refresh_meta", {}) if isinstance(item_result.get("refresh_meta"), dict) else {}
        if str(refresh_meta.get("refresh_attempted", "")).lower() == "true":
            summary["refresh_attempted_count"] += 1
        if str(refresh_meta.get("refresh_succeeded", "")).lower() == "true":
            summary["refresh_success_count"] += 1
            if content_id and content_id not in refreshed_ids:
                refreshed_ids.append(content_id)

    summary["refreshed_content_ids"] = refreshed_ids
    summary["followup_success_content_ids"] = followup_success_ids
    if skip_consistency_check:
        summary["consistency_check"] = {
            "status": "跳过",
            "reason": "dry-run 不执行文件与回写一致性校验",
        }
    else:
        summary["consistency_check"] = run_consistency_check(
            items=items,
            content_list_path=content_list_path,
            content_library_path=content_library_path,
        )
    usable_count = sum(
        1 for item in items
        if isinstance(item, dict)
        and item.get("content_id")
        and not any(
            isinstance(error, dict) and error.get("blocking") is True
            for error in (item.get("errors") if isinstance(item.get("errors"), list) else [])
        )
    )
    summary["status"] = "failed" if not usable_count else "partial" if summary["errors"] or summary["failed_items"] else "completed"
    return summary


def sync_item_into_report(
    *,
    report_path: Path,
    item_result: Dict[str, object],
    account_id: str,
    content_list_path: Path,
    content_library_path: Path,
    run_mode: str = "normal",
    requested_count: int | None = None,
    skip_consistency_check: bool = False,
) -> Dict[str, object]:
    existing_items: List[Dict[str, object]] = []
    existing_run_mode = run_mode
    existing_requested_count = requested_count
    existing_account_id = account_id
    if report_path.exists():
        try:
            existing_report = json.loads(report_path.read_text(encoding="utf-8"))
        except Exception as exc:  # noqa: BLE001
            code = permission_error_code(exc, "filesystem")
            if code:
                raise RuntimeError(code) from exc
            existing_report = {}
        if isinstance(existing_report, dict):
            raw_items = existing_report.get("items") or []
            if isinstance(raw_items, list):
                existing_items = [item for item in raw_items if isinstance(item, dict)]
            existing_run_mode = str(existing_report.get("run_mode") or run_mode)
            if existing_requested_count is None:
                try:
                    existing_requested_count = int(existing_report.get("requested_count") or 0)
                except Exception:  # noqa: BLE001
                    existing_requested_count = None
            existing_account_id = str(existing_report.get("account_id") or account_id)

    content_id = str(item_result.get("content_id", "")).strip()
    merged_items: List[Dict[str, object]] = []
    replaced = False
    for item in existing_items:
        if str(item.get("content_id", "")).strip() == content_id:
            merged_items.append(item_result)
            replaced = True
        else:
            merged_items.append(item)
    if not replaced:
        merged_items.append(item_result)

    final_requested_count = existing_requested_count if existing_requested_count is not None else len(merged_items)
    summary = summarize_items(
        account_id=existing_account_id or account_id,
        report_path=report_path,
        run_mode=existing_run_mode or run_mode,
        requested_count=max(final_requested_count, len(merged_items)),
        items=merged_items,
        content_list_path=content_list_path,
        content_library_path=content_library_path,
        skip_consistency_check=skip_consistency_check,
    )
    report_path.parent.mkdir(parents=True, exist_ok=True)
    write_json_atomically(report_path, summary)
    return summary
