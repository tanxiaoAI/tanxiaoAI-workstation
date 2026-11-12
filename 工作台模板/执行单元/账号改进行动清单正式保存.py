#!/usr/bin/env python3
"""账号改进行动清单 · 正式保存执行器。

把模型生成的《账号改进行动清单》候选稿校验后原子写入：
`<账号目录>/<YYYY年M月D日>账号改进行动清单.md`。

本执行器不写账号库、不改规划、不改诊断，只负责校验和落盘。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any

if str(Path(__file__).resolve().parent) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parent))

from csv安全写入 import read_csv_table


class ActionListError(RuntimeError):
    pass


SUMMARY_START = "<!-- account-action-list:start -->"
SUMMARY_END = "<!-- account-action-list:end -->"
SUMMARY_VERSION = "account-action-list-v1"
REQUIRED_ACTION_FIELDS = ("方向", "对应问题", "动作", "具体怎么做", "优先原因", "参考做法", "验证指标")
EMPTY_REFERENCE_VALUES = {"", "无", "—", "-"}


def _normalize_reference_text(text: str) -> str:
    return re.sub(r"[\s《》\"'“”‘’?？!！,，.。:：;；、·—\-_()（）\[\]【】#]", "", str(text or "")).lower()


def _load_reference_titles(path: Path) -> set[str]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ActionListError("reference_candidates_unreadable") from exc
    if not isinstance(payload, dict):
        raise ActionListError("reference_candidates_invalid")
    rows = payload.get("候选")
    if not isinstance(rows, list):
        raise ActionListError("reference_candidates_invalid")
    titles: set[str] = set()
    for row in rows:
        if not isinstance(row, dict):
            continue
        title = _normalize_reference_text(str(row.get("标题") or ""))
        if title:
            titles.add(title)
    return titles


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="校验并保存账号改进行动清单")
    parser.add_argument("--workspace-root", required=True)
    parser.add_argument("--account-id", required=True)
    parser.add_argument("--platform", default="")
    parser.add_argument("--candidate-file", required=True)
    parser.add_argument("--diagnosis-file", required=True)
    parser.add_argument("--reference-candidates-file", required=True)
    return parser.parse_args()


def _resolve_inside(root: Path, value: str | Path, label: str) -> Path:
    raw = str(value or "").strip()
    if not raw:
        raise ActionListError(f"{label}_missing")
    candidate = Path(raw).expanduser()
    target = candidate.resolve() if candidate.is_absolute() else (root / candidate).resolve()
    try:
        target.relative_to(root)
    except ValueError as exc:
        raise ActionListError(f"{label}_outside_workspace") from exc
    if not target.is_file():
        raise ActionListError(f"{label}_missing")
    return target


def _account_row(root: Path, account_id: str, platform: str) -> dict[str, str]:
    path = root / "我方资料/我方账号库.csv"
    if not path.is_file():
        raise ActionListError("account_library_missing")
    _, rows = read_csv_table(path)
    matches = [
        row for row in rows
        if str(row.get("账号ID") or "").strip() == account_id
        and (not platform or str(row.get("平台") or "").strip() == platform)
    ]
    if len(matches) != 1:
        raise ActionListError(f"account_not_unique:{len(matches)}")
    return {str(key): str(value or "").strip() for key, value in matches[0].items()}


def _extract_summary(markdown: str) -> dict[str, Any]:
    if markdown.count(SUMMARY_START) != 1 or markdown.count(SUMMARY_END) != 1:
        raise ActionListError("summary_block_invalid")
    block = markdown.split(SUMMARY_START, 1)[1].split(SUMMARY_END, 1)[0]
    match = re.search(r"```json\s*\n?(.*?)\n?```", block, re.S)
    if match is None:
        raise ActionListError("summary_json_missing")
    try:
        payload = json.loads(match.group(1))
    except json.JSONDecodeError as exc:
        raise ActionListError(f"summary_json_invalid:{exc}") from exc
    if not isinstance(payload, dict):
        raise ActionListError("summary_json_not_object")
    return payload


def _require_text(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ActionListError(f"field_invalid:{label}")
    return value.strip()


def _validate_candidate(
    markdown: str,
    *,
    account_id: str,
    account_name: str,
    diagnosis_name: str,
    reference_titles: set[str],
) -> None:
    first_line = markdown.splitlines()[0].strip() if markdown.splitlines() else ""
    if first_line != f"# {account_name}账号改进行动清单":
        raise ActionListError("candidate_title_invalid")
    payload = _extract_summary(markdown)
    if payload.get("版本") != SUMMARY_VERSION:
        raise ActionListError("summary_version_invalid")
    if str(payload.get("账号ID") or "").strip() != account_id:
        raise ActionListError("summary_account_id_mismatch")
    if str(payload.get("账号名") or "").strip() != account_name:
        raise ActionListError("summary_account_name_mismatch")
    if str(payload.get("对应诊断") or "").strip() != diagnosis_name:
        raise ActionListError("summary_diagnosis_mismatch")
    actions = payload.get("行动")
    if not isinstance(actions, list) or len(actions) > 3:
        raise ActionListError("summary_action_count_invalid")
    seen: set[str] = set()
    for index, item in enumerate(actions, start=1):
        if not isinstance(item, dict):
            raise ActionListError(f"summary_action_invalid:{index}")
        action_id = _require_text(item.get("ID"), f"行动.{index}.ID")
        if action_id in seen:
            raise ActionListError(f"summary_action_duplicate:{action_id}")
        seen.add(action_id)
        for field in REQUIRED_ACTION_FIELDS:
            if field == "具体怎么做":
                value = item.get(field)
                if not isinstance(value, list) or not value or any(not str(step).strip() for step in value):
                    raise ActionListError(f"summary_action_steps_invalid:{index}")
            else:
                _require_text(item.get(field), f"行动.{index}.{field}")
        reference = str(item.get("参考做法") or "").strip()
        if reference in EMPTY_REFERENCE_VALUES:
            continue
        normalized = _normalize_reference_text(reference)
        if not any(title and title in normalized for title in reference_titles):
            raise ActionListError(f"action_reference_not_in_candidates:{action_id}")


def _atomic_write(path: Path, payload: bytes) -> None:
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


def run(args: argparse.Namespace) -> dict[str, Any]:
    root = Path(args.workspace_root).expanduser().resolve()
    account = _account_row(root, args.account_id, args.platform)
    account_name = str(account.get("账号名") or "").strip()
    content_list = _resolve_inside(root, account.get("内容列表路径", ""), "content_list")
    account_dir = content_list.parent
    diagnosis_path = _resolve_inside(root, args.diagnosis_file, "diagnosis_file")
    candidate_path = _resolve_inside(root, args.candidate_file, "candidate_file")
    reference_path = _resolve_inside(root, args.reference_candidates_file, "reference_candidates_file")
    reference_titles = _load_reference_titles(reference_path)
    try:
        markdown = candidate_path.read_text(encoding="utf-8-sig").strip()
    except (OSError, UnicodeError) as exc:
        raise ActionListError("candidate_unreadable") from exc
    if not markdown:
        raise ActionListError("candidate_empty")
    _validate_candidate(
        markdown,
        account_id=str(account.get("账号ID") or "").strip(),
        account_name=account_name,
        diagnosis_name=diagnosis_path.name,
        reference_titles=reference_titles,
    )
    now = datetime.now()
    formal_path = account_dir / f"{now.year}年{now.month}月{now.day}日账号改进行动清单.md"
    payload = (markdown + "\n").encode("utf-8")
    _atomic_write(formal_path, payload)
    digest = hashlib.sha256(formal_path.read_bytes()).hexdigest()
    return {
        "ok": True,
        "status": "committed",
        "account_id": str(account.get("账号ID") or "").strip(),
        "account_name": account_name,
        "formal_path": formal_path.relative_to(root).as_posix(),
        "sha256": digest,
    }


def main() -> int:
    try:
        print(json.dumps(run(parse_args()), ensure_ascii=False, indent=2))
        return 0
    except ActionListError as exc:
        print(json.dumps({"ok": False, "status": "failed", "error_code": str(exc)}, ensure_ascii=False))
        return 1
    except Exception as exc:  # noqa: BLE001
        print(json.dumps({"ok": False, "status": "failed", "error_code": "unexpected_error", "message": str(exc)}, ensure_ascii=False))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
