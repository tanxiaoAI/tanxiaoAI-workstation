#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any


class RecommendedTopicsError(RuntimeError):
    pass


SCHEMA = "meiling-recommended-topics/1"
PLAN_PATH = "我方资料/我方自媒体规划.md"
CONTENT_LIBRARY_PATH = "我方资料/我方内容库.csv"
OUTPUT_PATH = "我方资料/推荐选题/推荐选题结果.json"
MARKDOWN_PATH = "我方资料/推荐选题/推荐选题结果.md"
TOPIC_COUNT = 9
GOALS = ("流量", "涨粉", "交易")
TEXT_FIELDS = ("title", "theme", "audience", "pain", "promise", "proposition", "angle", "mainline")
BASIS_DEFAULT = "账号方向推导"
BASIS_MAX_LENGTH = 80


def _read_standard_input() -> dict[str, object]:
    if sys.stdin.isatty():
        raise RecommendedTopicsError("input_requires_stdin_redirect")
    try:
        payload = json.loads(sys.stdin.buffer.read().decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RecommendedTopicsError("input_json_invalid") from exc
    if not isinstance(payload, dict):
        raise RecommendedTopicsError("input_json_object_required")
    return payload


def _text(value: object, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise RecommendedTopicsError(f"topic_field_empty:{field}")
    return value.strip()


def _outline(value: object, field: str) -> list[str]:
    if not isinstance(value, list) or not 3 <= len(value) <= 5:
        raise RecommendedTopicsError(f"topic_outline_count_invalid:{field}")
    items = [_text(item, f"{field}.outline") for item in value]
    if len(set(items)) != len(items):
        raise RecommendedTopicsError(f"topic_outline_duplicate:{field}")
    return items


def _basis(value: object, field: str) -> str:
    """选题出处：可省略（省略视为从账号方向推出的），写了就必须是短句。"""
    if value is None:
        return BASIS_DEFAULT
    if not isinstance(value, str):
        raise RecommendedTopicsError(f"topic_basis_invalid:{field}")
    text = value.strip()
    if not text:
        return BASIS_DEFAULT
    if len(text) > BASIS_MAX_LENGTH:
        raise RecommendedTopicsError(f"topic_basis_too_long:{field}")
    return text


def _normalize_topic(raw: object, index: int) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise RecommendedTopicsError(f"topic_object_required:{index}")
    label = f"topics[{index}]"
    rank = raw.get("rank")
    if isinstance(rank, bool) or not isinstance(rank, int):
        raise RecommendedTopicsError(f"topic_rank_invalid:{label}")

    topic: dict[str, Any] = {"rank": rank}
    for field in TEXT_FIELDS:
        topic[field] = _text(raw.get(field), f"{label}.{field}")
    goal = _text(raw.get("goal"), f"{label}.goal")
    if goal not in GOALS:
        raise RecommendedTopicsError(f"topic_goal_invalid:{label}")
    topic["goal"] = goal
    topic["basis"] = _basis(raw.get("basis"), f"{label}.basis")
    topic["outline"] = _outline(raw.get("outline"), label)
    return topic


def _normalize_topics(raw: object) -> list[dict[str, Any]]:
    if not isinstance(raw, list):
        raise RecommendedTopicsError("topics_array_required")
    if len(raw) != TOPIC_COUNT:
        raise RecommendedTopicsError("topics_must_contain_exactly_9")
    topics = [_normalize_topic(item, index) for index, item in enumerate(raw)]
    ranks = sorted(topic["rank"] for topic in topics)
    if ranks != list(range(1, TOPIC_COUNT + 1)):
        raise RecommendedTopicsError("topic_ranks_must_be_1_to_9")
    titles = [topic["title"] for topic in topics]
    if len(set(titles)) != len(titles):
        raise RecommendedTopicsError("topic_titles_must_be_unique")
    topics.sort(key=lambda item: item["rank"])
    return topics


def _render_markdown(result: dict[str, Any], has_history: bool) -> str:
    """人看的版本：和 json 一起落盘，内容一样，只是排版成能读的文档。"""
    history_note = (
        "来源：本地账号方向（我方自媒体规划）与历史内容库（历史库仅用于方向参考和去重）；"
        "没有读取问卷、文案和对标，也没有搜索平台、取数。"
        if has_history
        else "来源：本地账号方向（我方自媒体规划）；历史内容库为空或不存在，本次没有历史方向可参考。"
    )
    lines = [
        "# 推荐选题（9 条）",
        "",
        f"- 生成时间：{result['generated_at']}",
        f"- 账号方向：{result['plan_path']}",
        f"- {history_note}",
        "",
        "9 条按「先能被目标用户点开 → 再建立信任 → 最后承接产品」的顺序排；"
        "每条的目标是这条内容想拿到的结果，不代表流量或成交预测。",
        "- 选题出处写的是规划里的用户群体、具体工作场景和内容主线；不引用问卷、历史内容标题或内部内容 ID。",
        "",
    ]
    for topic in result["topics"]:
        lines.extend([
            f"## {topic['rank']:02d}. {topic['title']}",
            "",
            f"- 主题：{topic['theme']}",
            f"- 目标用户：{topic['audience']}",
            f"- 核心痛点：{topic['pain']}",
            f"- 收益承诺：{topic['promise']}",
            f"- 核心命题：{topic['proposition']}",
            f"- 切口：{topic['angle']}",
            f"- 内容主线：{topic['mainline']}",
            f"- 目标：{topic['goal']}",
            f"- 选题出处：{topic['basis']}",
            "- 内容提纲：",
        ])
        for index, item in enumerate(topic["outline"], start=1):
            lines.append(f"  {index}. {item}")
        lines.append("")
    return "\n".join(lines).rstrip("\n") + "\n"


def _temporary(target: Path, payload: bytes) -> Path:
    target.parent.mkdir(parents=True, exist_ok=True)
    descriptor, name = tempfile.mkstemp(prefix=f".{target.name}.", suffix=".tmp", dir=target.parent)
    temporary = Path(name)
    with os.fdopen(descriptor, "wb") as handle:
        handle.write(payload)
        handle.flush()
        os.fsync(handle.fileno())
    return temporary


def _has_content_library(root: Path) -> bool:
    """历史内容库只用于判断是否要写「没有历史方向可参考」，不解析内容。"""
    path = root / CONTENT_LIBRARY_PATH
    if not path.is_file():
        return False
    try:
        lines = path.read_text(encoding="utf-8-sig").strip().splitlines()
    except (OSError, UnicodeError):
        return False
    return len(lines) > 1


def save_recommended_topics(workspace_root: str | Path, payload: dict[str, object]) -> dict[str, Any]:
    root = Path(workspace_root).expanduser().resolve()
    if not root.is_dir():
        raise RecommendedTopicsError("workspace_root_invalid")

    schema = payload.get("schema")
    if schema != SCHEMA:
        raise RecommendedTopicsError("schema_invalid")
    plan_path = payload.get("plan_path", PLAN_PATH)
    if plan_path != PLAN_PATH:
        raise RecommendedTopicsError("plan_path_invalid")

    plan_file = root / PLAN_PATH
    if not plan_file.is_file():
        raise RecommendedTopicsError("plan_unavailable")
    plan_bytes = plan_file.read_bytes()
    if not plan_bytes.strip():
        raise RecommendedTopicsError("plan_empty")

    topics = _normalize_topics(payload.get("topics"))
    result = {
        "schema": SCHEMA,
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "plan_path": PLAN_PATH,
        "plan_sha256": hashlib.sha256(plan_bytes).hexdigest(),
        "topics": topics,
    }
    target = root / OUTPUT_PATH
    markdown_target = root / MARKDOWN_PATH
    payloads = [
        (target, (json.dumps(result, ensure_ascii=False, indent=2) + "\n").encode("utf-8")),
        (markdown_target, _render_markdown(result, _has_content_library(root)).encode("utf-8")),
    ]
    previous = {
        path: (path.read_bytes() if path.is_file() else None) for path, _ in payloads
    }
    temporaries: list[Path] = []
    try:
        for path, bytes_payload in payloads:
            temporary = _temporary(path, bytes_payload)
            temporaries.append(temporary)
            os.replace(temporary, path)
        for path, bytes_payload in payloads:
            if path.read_bytes() != bytes_payload:
                raise RecommendedTopicsError("post_write_mismatch")
    except Exception:
        for path, before in previous.items():
            if before is None:
                path.unlink(missing_ok=True)
            else:
                os.replace(_temporary(path, before), path)
        raise
    finally:
        for temporary in temporaries:
            temporary.unlink(missing_ok=True)

    return {
        "ok": True,
        "status": "committed",
        "count": len(topics),
        "output_path": OUTPUT_PATH,
        "markdown_path": MARKDOWN_PATH,
        "generated_at": result["generated_at"],
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="校验并保存 9 条推荐选题")
    parser.add_argument("--workspace-root", required=True)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        result = save_recommended_topics(args.workspace_root, _read_standard_input())
    except (OSError, UnicodeError, RecommendedTopicsError) as exc:
        print(json.dumps({"ok": False, "status": "failed", "error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
