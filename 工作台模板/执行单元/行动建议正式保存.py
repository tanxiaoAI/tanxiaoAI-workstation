#!/usr/bin/env python3
"""行动建议 · 正式保存执行器

把模型生成的《行动建议》候选稿校验后原子写入对标账号目录：
`<账号目录>/行动建议.md`。

本执行器不写账号库、不改阶段状态，只负责校验和落盘。行动建议可以在
《自媒体规划》变化后单独重跑，因此和详细分析、内容规律分析分开保存。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import tempfile
from pathlib import Path, PurePosixPath

if str(Path(__file__).resolve().parent) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parent))

from csv安全写入 import read_csv_table_with_snapshot


class ActionAdviceError(RuntimeError):
    pass


ACCOUNT_LIBRARY_PATH = Path("对标资料/对标账号库.csv")
REPORT_TITLE_SUFFIX = "行动建议"
SUMMARY_START = "<!-- action-advice-summary:start -->"
SUMMARY_END = "<!-- action-advice-summary:end -->"
SUMMARY_VERSION = "action-advice-v2"
REQUIRED_H2 = ("适配结论", "行动卡片")
LEGACY_CARD_FIELDS = ("行动类型", "尝试动作", "借鉴结构", "我方调整", "依据")


def resolve_under(root: Path, raw_path: str, *, label: str) -> Path:
    normalized = str(raw_path or "").strip().replace("\\", "/")
    if not normalized:
        raise ActionAdviceError(f"{label}_missing")
    logical = PurePosixPath(normalized)
    candidate = Path(normalized).expanduser()
    resolved = candidate.resolve() if candidate.is_absolute() else root.joinpath(*logical.parts).resolve()
    try:
        resolved.relative_to(root)
    except ValueError as exc:
        raise ActionAdviceError(f"{label}_outside_workspace") from exc
    return resolved


def require_text(value: object, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ActionAdviceError(f"summary_field_invalid:{label}")
    return value.strip()


def require_list(value: object, label: str) -> list:
    if not isinstance(value, list):
        raise ActionAdviceError(f"summary_field_invalid:{label}")
    return value


def require_dict(value: object, label: str) -> dict:
    if not isinstance(value, dict):
        raise ActionAdviceError(f"summary_field_invalid:{label}")
    return value


def extract_summary_json(markdown: str) -> dict[str, object]:
    if markdown.count(SUMMARY_START) != 1 or markdown.count(SUMMARY_END) != 1:
        raise ActionAdviceError("summary_block_marker_invalid")
    if markdown.index(SUMMARY_START) > markdown.index(SUMMARY_END):
        raise ActionAdviceError("summary_block_marker_order_invalid")
    block = markdown.split(SUMMARY_START, 1)[1].split(SUMMARY_END, 1)[0]
    match = re.search(r"```json\s*\n?(.*?)\n?```", block, re.S)
    if match is None:
        raise ActionAdviceError("summary_json_fence_missing")
    try:
        data = json.loads(match.group(1))
    except json.JSONDecodeError as exc:
        raise ActionAdviceError(f"summary_json_invalid:{exc}") from exc
    if not isinstance(data, dict):
        raise ActionAdviceError("summary_json_not_object")
    return data


def read_account(root: Path, account_id: str, platform: str) -> dict[str, str]:
    library_path = root / ACCOUNT_LIBRARY_PATH
    if not library_path.is_file():
        raise ActionAdviceError(f"account_library_missing:{ACCOUNT_LIBRARY_PATH.as_posix()}")
    _, rows, _ = read_csv_table_with_snapshot(library_path)
    matches = [
        row
        for row in rows
        if str(row.get("账号ID") or "").strip() == account_id
        and (not platform or str(row.get("平台") or "").strip() == platform)
    ]
    if len(matches) != 1:
        raise ActionAdviceError(f"account_match_count:expected=1:actual={len(matches)}")
    return {str(key): str(value or "").strip() for key, value in matches[0].items()}


def read_content_ids(root: Path, content_list_path: str) -> set[str]:
    path = resolve_under(root, content_list_path, label="content_list_path")
    _, rows, _ = read_csv_table_with_snapshot(path)
    return {str(row.get("内容ID") or "").strip() for row in rows if str(row.get("内容ID") or "").strip()}


def validate_candidate(
    path: Path,
    *,
    account_id: str,
    account_name: str,
    content_ids: set[str],
) -> str:
    try:
        markdown = path.read_text(encoding="utf-8-sig").strip()
    except (OSError, UnicodeError) as exc:
        raise ActionAdviceError(f"candidate_read_failed:{exc}") from exc
    if not markdown:
        raise ActionAdviceError("candidate_empty")
    expected_title = f"# {account_name}{REPORT_TITLE_SUFFIX}"
    if markdown.splitlines()[0].strip() != expected_title:
        raise ActionAdviceError("candidate_title_invalid")
    headings = [line.strip()[3:].strip() for line in markdown.splitlines() if line.startswith("## ")]
    if headings != list(REQUIRED_H2):
        raise ActionAdviceError(f"candidate_headings_invalid:{headings}")
    data = extract_summary_json(markdown)
    if data.get("版本") != SUMMARY_VERSION:
        raise ActionAdviceError("summary_version_invalid")
    if data.get("账号ID") is not None and str(data.get("账号ID")).strip() != account_id:
        raise ActionAdviceError("summary_account_id_mismatch")
    if data.get("账号名") is not None and str(data.get("账号名")).strip() != account_name:
        raise ActionAdviceError("summary_account_name_mismatch")

    adaptation = require_dict(data.get("适配结论"), "适配结论")
    overall = require_text(adaptation.get("总体判断"), "适配结论.总体判断")
    reasons = require_list(adaptation.get("理由"), "适配结论.理由")
    if not reasons:
        raise ActionAdviceError("summary_adaptation_reason_missing")
    for reason in reasons:
        require_text(reason, "适配结论.理由")
    require_text(adaptation.get("适用边界"), "适配结论.适用边界")
    adaptation_body = markdown.split("## 适配结论", 1)[1].split("## 行动卡片", 1)[0]
    if overall not in adaptation_body:
        raise ActionAdviceError("report_adaptation_mismatch")

    cards = require_list(data.get("行动卡片"), "行动卡片")
    if not 0 <= len(cards) <= 3:
        raise ActionAdviceError(f"summary_action_card_count_invalid:{len(cards)}")
    seen_ids: set[str] = set()
    for card in cards:
        item = require_dict(card, "行动卡片")
        card_id = require_text(item.get("ID"), "行动卡片.ID")
        if card_id in seen_ids:
            raise ActionAdviceError(f"summary_action_card_id_duplicate:{card_id}")
        seen_ids.add(card_id)
        for legacy in LEGACY_CARD_FIELDS:
            if legacy in item:
                raise ActionAdviceError(f"summary_action_legacy_field:{card_id}:{legacy}")
        require_text(item.get("标题"), "行动卡片.标题")
        require_text(item.get("具体怎么做"), "行动卡片.具体怎么做")
        signals = require_list(item.get("观察信号"), "行动卡片.观察信号")
        if not 1 <= len(signals) <= 3:
            raise ActionAdviceError(f"summary_action_signal_count_invalid:{card_id}")
        for signal in signals:
            require_text(signal, "行动卡片.观察信号")
        works = require_list(item.get("参考作品"), "行动卡片.参考作品")
        if len(works) != 1:
            raise ActionAdviceError(f"summary_reference_work_count_invalid:{card_id}")
        for work in works:
            node = require_dict(work, "行动卡片.参考作品")
            content_id = require_text(node.get("内容ID"), "行动卡片.参考作品.内容ID")
            require_text(node.get("标题"), "行动卡片.参考作品.标题")
            if content_ids and content_id not in content_ids:
                raise ActionAdviceError(f"summary_reference_work_unknown:{card_id}:{content_id}")
    return markdown


def atomic_write(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=str(path.parent))
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)
    if path.read_bytes() != payload:
        raise ActionAdviceError("advice_post_write_mismatch")


def finalize_action_advice(
    workspace_root: str | Path,
    *,
    account_id: str,
    platform: str = "",
    candidate_file: str | Path,
) -> dict[str, object]:
    root = Path(workspace_root).expanduser().resolve()
    if not root.is_dir():
        raise ActionAdviceError(f"workspace_root_invalid:{root}")
    normalized_id = str(account_id or "").strip()
    if not normalized_id:
        raise ActionAdviceError("account_id_missing")
    account = read_account(root, normalized_id, str(platform or "").strip())
    account_name = account.get("账号名", "").strip()
    if not account_name:
        raise ActionAdviceError("account_name_missing")
    content_list_rel = account.get("内容列表路径", "")
    if not content_list_rel:
        raise ActionAdviceError("account_content_list_path_missing")
    content_ids = read_content_ids(root, content_list_rel)
    candidate = resolve_under(root, str(candidate_file), label="candidate_file")
    if not candidate.is_file():
        raise ActionAdviceError("candidate_missing")
    markdown = validate_candidate(
        candidate,
        account_id=normalized_id,
        account_name=account_name,
        content_ids=content_ids,
    )
    content_list_path = resolve_under(root, content_list_rel, label="content_list_path")
    # 正式文档只用无后缀文件名：重跑直接覆盖，不再生成 -新 副本。
    formal_path = content_list_path.parent / "行动建议.md"
    payload = (markdown.rstrip() + "\n").encode("utf-8")
    atomic_write(formal_path, payload)
    return {
        "ok": True,
        "status": "committed",
        "formal_path": formal_path.relative_to(root).as_posix(),
        "formal_sha256": hashlib.sha256(payload).hexdigest(),
        "action_card_count": len(require_list(extract_summary_json(markdown).get("行动卡片"), "行动卡片")),
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="校验并正式保存账号行动建议")
    parser.add_argument("--workspace-root", type=Path, required=True)
    parser.add_argument("--account-id", required=True)
    parser.add_argument("--platform", default="")
    parser.add_argument("--candidate-file", required=True, type=Path)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        result = finalize_action_advice(
            args.workspace_root,
            account_id=args.account_id,
            platform=args.platform,
            candidate_file=args.candidate_file,
        )
        print(json.dumps(result, ensure_ascii=False))
        return 0
    except (OSError, UnicodeError, ValueError, ActionAdviceError) as exc:
        print(
            json.dumps({"ok": False, "status": "failed", "error": str(exc)}, ensure_ascii=False),
            file=sys.stderr,
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
