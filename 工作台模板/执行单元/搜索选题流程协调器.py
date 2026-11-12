#!/usr/bin/env python3
"""搜索选题流程协调器：冻结账号方向与查询计划，并校验、保存 9 条正式推荐。"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import re
import secrets
import sys
import tempfile
from pathlib import Path
from typing import Any, Mapping


WORKFLOW_ID = "topic-search"
SCHEMA_VERSION = "1"
STAGING_ROOT = Path(".meiling/staging/topic-search")
FORMAL_ROOT = Path("我方资料/搜索选题")
PLATFORMS = ("小红书", "抖音", "B站")
EVIDENCE_TYPES = ("评论证据", "正文证据", "标题与互动")
SCORE_WEIGHTS = (
    ("score_account_fit", 30),
    ("score_demand", 25),
    ("score_reference", 20),
    ("score_differentiation", 15),
    ("score_production", 10),
)


class TopicSearchCoordinatorError(RuntimeError):
    pass


def root_path(value: str) -> Path:
    root = Path(value).expanduser().resolve()
    if not root.is_dir():
        raise TopicSearchCoordinatorError(f"workspace_root_invalid:{root}")
    return root


def inside(root: Path, raw: str, label: str, *, required: bool = True) -> Path | None:
    candidate = Path(raw).expanduser()
    path = candidate.resolve() if candidate.is_absolute() else (root / candidate).resolve()
    try:
        path.relative_to(root)
    except ValueError as exc:
        raise TopicSearchCoordinatorError(f"{label}_outside_workspace") from exc
    if path.is_symlink() or not path.is_file():
        if required:
            raise TopicSearchCoordinatorError(f"{label}_missing:{path}")
        return None
    return path


def logical(root: Path, path: Path) -> str:
    return path.resolve().relative_to(root).as_posix()


def read_json(path: Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise TopicSearchCoordinatorError(f"{label}_invalid:{exc}") from exc
    if not isinstance(value, dict):
        raise TopicSearchCoordinatorError(f"{label}_not_object")
    return value


def write_json(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(value, handle, ensure_ascii=False, indent=2, allow_nan=False)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def write_text(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(value)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def load_context(root: Path, raw: str) -> tuple[Path, dict[str, Any]]:
    path = inside(root, raw, "context_file")
    assert path is not None
    relative = path.relative_to(root)
    if len(relative.parts) != 5 or relative.parts[:3] != (".meiling", "staging", "topic-search") or relative.name != "context.json":
        raise TopicSearchCoordinatorError("context_file_outside_run")
    context = read_json(path, "context_file")
    required = {"schema_version", "workflow_id", "run_id", "created_at", "data_source", "platforms", "overview_path", "overview_sha256"}
    if set(context) != required or context.get("schema_version") != SCHEMA_VERSION or context.get("workflow_id") != WORKFLOW_ID:
        raise TopicSearchCoordinatorError("context_contract_invalid")
    if context["run_id"] != path.parent.name or context["data_source"] not in {"chrome_devtools", "justoneapi"}:
        raise TopicSearchCoordinatorError("context_identity_invalid")
    if not isinstance(context["platforms"], list) or not context["platforms"] or any(item not in PLATFORMS for item in context["platforms"]):
        raise TopicSearchCoordinatorError("context_platforms_invalid")
    return path, context


def prepare(root: Path, args: argparse.Namespace) -> dict[str, Any]:
    overview = inside(root, args.overview_path, "overview_path")
    assert overview is not None
    raw = overview.read_bytes()
    if not raw.strip():
        raise TopicSearchCoordinatorError("overview_empty")
    platforms = [item.strip() for item in args.platforms.split(",") if item.strip()]
    if not platforms or len(set(platforms)) != len(platforms) or any(item not in PLATFORMS for item in platforms):
        raise TopicSearchCoordinatorError("platforms_invalid")
    run_id = f"run-{dt.datetime.now().strftime('%Y%m%d-%H%M%S')}-{secrets.token_hex(4)}"
    run_dir = root / STAGING_ROOT / run_id
    context_path = run_dir / "context.json"
    snapshot_path = run_dir / "账号方向快照.md"
    snapshot_path.parent.mkdir(parents=True, exist_ok=True)
    snapshot_path.write_bytes(raw)
    context = {
        "schema_version": SCHEMA_VERSION,
        "workflow_id": WORKFLOW_ID,
        "run_id": run_id,
        "created_at": dt.datetime.now().astimezone().replace(microsecond=0).isoformat(),
        "data_source": args.data_source,
        "platforms": platforms,
        "overview_path": logical(root, snapshot_path),
        "overview_sha256": sha256_bytes(raw),
    }
    write_json(context_path, context)
    candidate = run_dir / "搜索计划候选.json"
    plan_path = run_dir / "搜索计划.json"
    return {
        "ok": True,
        "status": "prepared",
        "workflow_id": WORKFLOW_ID,
        "run_id": run_id,
        "context_file": logical(root, context_path),
        "inputs": {"account_overview": context["overview_path"], "sha256": context["overview_sha256"]},
        "action_contract": {
            "current_action": "generate_query_plan_candidate",
            "required_outputs": [{"name": "query_plan_candidate", "exact_path": logical(root, candidate)}],
            "freeze_queries_request": {
                "action": "freeze-queries",
                "context_file": logical(root, context_path),
                "query_plan": logical(root, candidate),
            },
            "search_plan_path": logical(root, plan_path),
        },
    }


def freeze_queries(root: Path, args: argparse.Namespace) -> dict[str, Any]:
    context_path, context = load_context(root, args.context_file)
    candidate_path = inside(root, args.query_plan, "query_plan")
    assert candidate_path is not None
    if candidate_path.parent != context_path.parent:
        raise TopicSearchCoordinatorError("query_plan_outside_run")
    candidate = read_json(candidate_path, "query_plan")
    if set(candidate) != {"schema_version", "queries"} or candidate.get("schema_version") != SCHEMA_VERSION:
        raise TopicSearchCoordinatorError("query_plan_contract_invalid")
    queries = candidate.get("queries")
    if not isinstance(queries, list) or not 3 <= len(queries) <= 6:
        raise TopicSearchCoordinatorError("query_count_must_be_3_to_6")
    normalized: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    seen_pairs: set[tuple[str, str]] = set()
    for item in queries:
        if not isinstance(item, dict) or set(item) != {"query_id", "keyword", "platforms", "rationale"}:
            raise TopicSearchCoordinatorError("query_item_contract_invalid")
        query_id = str(item["query_id"]).strip()
        keyword = re.sub(r"\s+", " ", str(item["keyword"])).strip()
        rationale = str(item["rationale"]).strip()
        platforms = item["platforms"]
        if not re.fullmatch(r"Q\d{3}", query_id) or query_id in seen_ids:
            raise TopicSearchCoordinatorError(f"query_id_invalid:{query_id}")
        if not keyword or len(keyword) > 80 or not rationale:
            raise TopicSearchCoordinatorError(f"query_text_invalid:{query_id}")
        if not isinstance(platforms, list) or not platforms or any(platform not in context["platforms"] for platform in platforms):
            raise TopicSearchCoordinatorError(f"query_platforms_invalid:{query_id}")
        for platform in platforms:
            pair = (platform, keyword.casefold())
            if pair in seen_pairs:
                raise TopicSearchCoordinatorError(f"duplicate_platform_keyword:{platform}:{keyword}")
            seen_pairs.add(pair)
        seen_ids.add(query_id)
        normalized.append({"query_id": query_id, "keyword": keyword, "platforms": platforms, "rationale": rationale})
    covered = {platform for item in normalized for platform in item["platforms"]}
    missing = [platform for platform in context["platforms"] if platform not in covered]
    if missing:
        raise TopicSearchCoordinatorError(f"query_platform_coverage_missing:{missing}")
    plan = {**context, "queries": normalized}
    plan_path = context_path.with_name("搜索计划.json")
    write_json(plan_path, plan)
    return {
        "ok": True,
        "status": "queries_frozen",
        "workflow_id": WORKFLOW_ID,
        "run_id": context["run_id"],
        "plan_file": logical(root, plan_path),
        "query_count": len(normalized),
        "action_contract": {
            "search_request": {
                "action": "search" if context["data_source"] == "justoneapi" else "normalize",
                "workspace_root": str(root),
                "plan_file": logical(root, plan_path),
            }
        },
    }


def require_text(topic: Mapping[str, Any], field: str, rank: int) -> str:
    value = str(topic.get(field) or "").strip()
    if not value:
        raise TopicSearchCoordinatorError(f"topic_field_missing:{rank}:{field}")
    return value


def read_scores(topic: Mapping[str, Any], rank: int) -> dict[str, int]:
    scores: dict[str, int] = {}
    for field, _ in SCORE_WEIGHTS:
        value = topic.get(field)
        if not isinstance(value, int) or isinstance(value, bool) or not 1 <= value <= 5:
            raise TopicSearchCoordinatorError(f"topic_score_invalid:{rank}:{field}")
        scores[field] = value
    return scores


def total_score(scores: Mapping[str, int]) -> int:
    return sum(scores[field] * weight // 5 for field, weight in SCORE_WEIGHTS)


def validate_candidate(
    candidate: Mapping[str, Any],
    evidence: Mapping[str, Any],
    run_id: str,
    allowed_content_ids: set[str],
    kept_comments_by_content: Mapping[str, set[str]],
) -> list[dict[str, Any]]:
    if set(candidate) != {"schema_version", "workflow_id", "run_id", "recommended_topics"}:
        raise TopicSearchCoordinatorError("candidate_contract_invalid")
    if candidate.get("schema_version") != SCHEMA_VERSION or candidate.get("workflow_id") != WORKFLOW_ID or candidate.get("run_id") != run_id:
        raise TopicSearchCoordinatorError("candidate_identity_invalid")
    topics = candidate.get("recommended_topics")
    if not isinstance(topics, list) or len(topics) != 9:
        raise TopicSearchCoordinatorError("recommended_topics_must_contain_exactly_9")
    contents = evidence.get("contents")
    if not isinstance(contents, list) or not contents:
        raise TopicSearchCoordinatorError("evidence_contents_missing")
    content_by_id = {str(item.get("content_id")): item for item in contents if isinstance(item, dict)}
    output: list[dict[str, Any]] = []
    seen_titles: set[str] = set()
    for expected_rank, raw_topic in enumerate(topics, start=1):
        if not isinstance(raw_topic, dict):
            raise TopicSearchCoordinatorError(f"topic_not_object:{expected_rank}")
        rank = raw_topic.get("rank")
        if rank != expected_rank:
            raise TopicSearchCoordinatorError(f"topic_rank_invalid:{expected_rank}")
        title = require_text(raw_topic, "title", expected_rank)
        folded = title.casefold()
        if folded in seen_titles:
            raise TopicSearchCoordinatorError(f"duplicate_topic_title:{title}")
        seen_titles.add(folded)
        angle = require_text(raw_topic, "angle", expected_rank)
        audience = require_text(raw_topic, "audience", expected_rank)
        pain = require_text(raw_topic, "pain", expected_rank)
        fit_reason = require_text(raw_topic, "account_fit_reason", expected_rank)
        evidence_type = require_text(raw_topic, "evidence_type", expected_rank)
        if evidence_type not in EVIDENCE_TYPES:
            raise TopicSearchCoordinatorError(f"evidence_type_invalid:{expected_rank}:{evidence_type}")
        scores = read_scores(raw_topic, expected_rank)
        reference_ids = raw_topic.get("reference_content_ids")
        comment_ids = raw_topic.get("evidence_comment_ids")
        if not isinstance(reference_ids, list) or not 1 <= len(reference_ids) <= 3 or len(set(reference_ids)) != len(reference_ids):
            raise TopicSearchCoordinatorError(f"reference_content_ids_invalid:{expected_rank}")
        if not isinstance(comment_ids, list):
            raise TopicSearchCoordinatorError(f"evidence_comment_ids_invalid:{expected_rank}")
        references = []
        available_comments: set[str] = set()
        kept_comments: set[str] = set()
        has_body = False
        for content_id in reference_ids:
            content_id = str(content_id)
            content = content_by_id.get(content_id)
            if content is None:
                raise TopicSearchCoordinatorError(f"reference_not_in_search_results:{expected_rank}:{content_id}")
            if content_id not in allowed_content_ids:
                raise TopicSearchCoordinatorError(f"reference_rejected_by_ai_relevance:{expected_rank}:{content_id}")
            references.append(content)
            has_body = has_body or bool(str(content.get("body") or "").strip())
            comments = content.get("评论") or []
            if isinstance(comments, list):
                available_comments.update(str(item.get("评论ID")) for item in comments if isinstance(item, dict))
            kept_comments.update(kept_comments_by_content.get(content_id, set()))
        normalized_comment_ids = [str(item) for item in comment_ids]
        if len(set(normalized_comment_ids)) != len(normalized_comment_ids) or any(item not in available_comments for item in normalized_comment_ids):
            raise TopicSearchCoordinatorError(f"evidence_comment_not_in_references:{expected_rank}")
        if any(item not in kept_comments for item in normalized_comment_ids):
            raise TopicSearchCoordinatorError(f"comment_rejected_by_ai_filter:{expected_rank}")
        if evidence_type == "评论证据" and not normalized_comment_ids:
            raise TopicSearchCoordinatorError(f"comment_evidence_required:{expected_rank}")
        if evidence_type == "正文证据" and not has_body:
            raise TopicSearchCoordinatorError(f"body_evidence_required:{expected_rank}")
        if evidence_type == "标题与互动" and normalized_comment_ids:
            raise TopicSearchCoordinatorError(f"title_only_cannot_use_comments:{expected_rank}")
        output.append({
            "rank": expected_rank,
            "title": title,
            "angle": angle,
            "audience": audience,
            "pain": pain,
            "account_fit_reason": fit_reason,
            "evidence_type": evidence_type,
            "reference_content_ids": [str(item) for item in reference_ids],
            "evidence_comment_ids": normalized_comment_ids,
            **scores,
            "total_score": total_score(scores),
        })
    output.sort(key=lambda item: -item["total_score"])
    for index, topic in enumerate(output, start=1):
        topic["rank"] = index
    return output


def filter_doc_identity(document: Mapping[str, Any], run_id: str, required_keys: set[str]) -> None:
    if set(document) != {"schema_version", "workflow_id", "run_id", *required_keys}:
        raise TopicSearchCoordinatorError("filter_contract_invalid")
    if document.get("schema_version") != SCHEMA_VERSION or document.get("workflow_id") != WORKFLOW_ID or document.get("run_id") != run_id:
        raise TopicSearchCoordinatorError("filter_identity_invalid")


def filter_relevance(root: Path, args: argparse.Namespace) -> dict[str, Any]:
    """AI 判断搜索结果是否相关；执行器只校验结构、覆盖范围和出口。"""
    context_path, context = load_context(root, args.context_file)
    candidate_path = inside(root, args.filter_json, "relevance_filter")
    assert candidate_path is not None
    if candidate_path.parent != context_path.parent:
        raise TopicSearchCoordinatorError("relevance_filter_outside_run")
    document = read_json(candidate_path, "relevance_filter")
    filter_doc_identity(document, context["run_id"], {"decisions"})
    evidence = read_json(context_path.with_name("选题证据.json"), "evidence_file")
    contents = evidence.get("contents") if isinstance(evidence.get("contents"), list) else []
    content_ids = {str(item.get("content_id")) for item in contents if isinstance(item, dict)}
    if not content_ids:
        raise TopicSearchCoordinatorError("evidence_contents_missing")
    decisions = document.get("decisions")
    if not isinstance(decisions, list):
        raise TopicSearchCoordinatorError("relevance_decisions_invalid")
    seen: set[str] = set()
    selected: list[str] = []
    normalized: list[dict[str, Any]] = []
    for item in decisions:
        if not isinstance(item, dict) or set(item) != {"content_id", "decision", "reason", "use_for_enrich"}:
            raise TopicSearchCoordinatorError("relevance_decision_contract_invalid")
        content_id = str(item["content_id"]).strip()
        decision = str(item["decision"]).strip()
        reason = str(item["reason"]).strip()
        use_for_enrich = item["use_for_enrich"]
        if content_id not in content_ids or content_id in seen:
            raise TopicSearchCoordinatorError(f"relevance_content_invalid:{content_id}")
        if decision not in {"relevant", "weak", "irrelevant"} or not reason:
            raise TopicSearchCoordinatorError(f"relevance_decision_invalid:{content_id}")
        if not isinstance(use_for_enrich, bool):
            raise TopicSearchCoordinatorError(f"relevance_use_flag_invalid:{content_id}")
        if decision == "irrelevant" and use_for_enrich:
            raise TopicSearchCoordinatorError(f"irrelevant_cannot_be_enriched:{content_id}")
        seen.add(content_id)
        if use_for_enrich:
            selected.append(content_id)
        normalized.append({"content_id": content_id, "decision": decision, "reason": reason, "use_for_enrich": use_for_enrich})
    missing = sorted(content_ids - seen)
    if missing:
        raise TopicSearchCoordinatorError(f"relevance_coverage_missing:{missing}")
    if not selected:
        raise TopicSearchCoordinatorError("relevance_selection_empty")
    output_path = context_path.with_name("相关性筛选结果.json")
    write_json(output_path, {
        "schema_version": SCHEMA_VERSION,
        "workflow_id": WORKFLOW_ID,
        "run_id": context["run_id"],
        "decisions": normalized,
    })
    return {
        "ok": True,
        "status": "relevance_filtered",
        "run_id": context["run_id"],
        "filter_file": logical(root, output_path),
        "enrich_content_ids": selected,
        "counts": {
            "relevant": sum(1 for item in normalized if item["decision"] == "relevant"),
            "weak": sum(1 for item in normalized if item["decision"] == "weak"),
            "irrelevant": sum(1 for item in normalized if item["decision"] == "irrelevant"),
        },
    }


def filter_comments(root: Path, args: argparse.Namespace) -> dict[str, Any]:
    """AI 对执行器初筛后的评论再做语义过滤；执行器只校验评论 ID 归属。"""
    context_path, context = load_context(root, args.context_file)
    candidate_path = inside(root, args.filter_json, "comment_filter")
    assert candidate_path is not None
    if candidate_path.parent != context_path.parent:
        raise TopicSearchCoordinatorError("comment_filter_outside_run")
    document = read_json(candidate_path, "comment_filter")
    filter_doc_identity(document, context["run_id"], {"contents"})
    evidence = read_json(context_path.with_name("选题证据.json"), "evidence_file")
    comment_ids_by_content: dict[str, set[str]] = {}
    for content in evidence.get("contents") or []:
        if not isinstance(content, dict):
            continue
        comments = content.get("评论") if isinstance(content.get("评论"), list) else []
        if comments:
            comment_ids_by_content[str(content["content_id"])] = {
                str(item.get("评论ID")) for item in comments if isinstance(item, dict)
            }
    if not comment_ids_by_content:
        raise TopicSearchCoordinatorError("no_comments_to_filter")
    entries = document.get("contents")
    if not isinstance(entries, list):
        raise TopicSearchCoordinatorError("comment_filter_entries_invalid")
    seen: set[str] = set()
    kept_by_content: dict[str, set[str]] = {}
    for entry in entries:
        if not isinstance(entry, dict) or set(entry) != {"content_id", "kept_comment_ids", "dropped_comment_ids", "reason"}:
            raise TopicSearchCoordinatorError("comment_filter_entry_contract_invalid")
        content_id = str(entry["content_id"]).strip()
        if content_id not in comment_ids_by_content or content_id in seen:
            raise TopicSearchCoordinatorError(f"comment_filter_content_invalid:{content_id}")
        kept = entry["kept_comment_ids"]
        dropped = entry["dropped_comment_ids"]
        if not isinstance(kept, list) or not isinstance(dropped, list) or not str(entry["reason"]).strip():
            raise TopicSearchCoordinatorError(f"comment_filter_lists_invalid:{content_id}")
        kept_set = {str(item) for item in kept}
        dropped_set = {str(item) for item in dropped}
        if kept_set & dropped_set:
            raise TopicSearchCoordinatorError(f"comment_filter_overlap:{content_id}")
        if kept_set | dropped_set != comment_ids_by_content[content_id]:
            raise TopicSearchCoordinatorError(f"comment_filter_coverage_invalid:{content_id}")
        seen.add(content_id)
        kept_by_content[content_id] = kept_set
    missing = sorted(set(comment_ids_by_content) - seen)
    if missing:
        raise TopicSearchCoordinatorError(f"comment_filter_coverage_missing:{missing}")
    output_path = context_path.with_name("评论筛选结果.json")
    write_json(output_path, {
        "schema_version": SCHEMA_VERSION,
        "workflow_id": WORKFLOW_ID,
        "run_id": context["run_id"],
        "contents": entries,
    })
    return {
        "ok": True,
        "status": "comments_filtered",
        "run_id": context["run_id"],
        "filter_file": logical(root, output_path),
        "counts": {
            "contents": len(entries),
            "kept": sum(len(item["kept_comment_ids"]) for item in entries),
            "dropped": sum(len(item["dropped_comment_ids"]) for item in entries),
        },
    }


def render_markdown(document: Mapping[str, Any]) -> str:
    lines = [
        "# 搜索选题推荐（9条）",
        "",
        f"- 本轮运行：{document['run_id']}",
        f"- 数据源：{document['data_source']}",
        f"- 搜索关键词：{'、'.join(item['keyword'] for item in document['queries'])}",
        "",
        "同一轮搜索结果同时用于归纳选题和查看来源；没有再次为选题搜索对标内容。",
        "9 条按综合评分从高到低排列；总分是本轮 9 条之间的相对优先级，不代表播放量或爆款概率。",
        "",
    ]
    content_by_id = {item["content_id"]: item for item in document["source_contents"]}
    for topic in document["topics"]:
        lines.extend([
            f"## {topic['rank']}. {topic['title']}",
            "",
            f"- 综合评分：{topic['total_score']} / 100",
            f"- 评分：账号匹配 {topic['score_account_fit']}/5 · 需求强度 {topic['score_demand']}/5 · 来源参考 {topic['score_reference']}/5 · 差异化 {topic['score_differentiation']}/5 · 可生产 {topic['score_production']}/5",
            f"- 推荐角度：{topic['angle']}",
            f"- 目标用户：{topic['audience']}",
            f"- 用户问题：{topic['pain']}",
            f"- 账号匹配：{topic['account_fit_reason']}",
            f"- 依据类型：{topic['evidence_type']}",
        ])
        if topic["evidence_type"] == "评论证据":
            valid_comments = sum(len(content_by_id[item].get("评论") or []) for item in topic["reference_content_ids"])
            filtered_comments = sum(int(content_by_id[item].get("低信息评论条数") or 0) for item in topic["reference_content_ids"])
            lines.append(f"- 评论过滤：有效 {valid_comments} 条，低信息互动 {filtered_comments} 条")
        lines.append("- 来源内容：")
        for content_id in topic["reference_content_ids"]:
            content = content_by_id[content_id]
            metrics = content.get("metrics") or {}
            metric_text = " / ".join(
                f"{label} {metrics[key]}" for key, label in (("likes", "赞"), ("collects", "藏"), ("comments", "评")) if metrics.get(key) not in (None, "")
            )
            lines.append(f"  - [{content['platform']}｜{content['title']}]({content['url']})" + (f"（{metric_text}）" if metric_text else ""))
        if topic["evidence_comment_ids"]:
            lines.append("- 代表性评论：")
            wanted = set(topic["evidence_comment_ids"])
            for content_id in topic["reference_content_ids"]:
                for comment in content_by_id[content_id].get("评论") or []:
                    if str(comment.get("评论ID")) in wanted:
                        lines.append(f"  - {comment.get('正文', '')}（{comment.get('作者昵称', '未知作者')}，赞 {comment.get('点赞数', 0)}）")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def save(root: Path, args: argparse.Namespace) -> dict[str, Any]:
    context_path, context = load_context(root, args.context_file)
    evidence_path = context_path.with_name("选题证据.json")
    if not evidence_path.is_file():
        raise TopicSearchCoordinatorError("evidence_file_missing")
    evidence = read_json(evidence_path, "evidence_file")
    if evidence.get("workflow_id") != WORKFLOW_ID or evidence.get("run_id") != context["run_id"]:
        raise TopicSearchCoordinatorError("evidence_identity_invalid")
    relevance_path = context_path.with_name("相关性筛选结果.json")
    if not relevance_path.is_file():
        raise TopicSearchCoordinatorError("relevance_filter_missing")
    relevance_filter = read_json(relevance_path, "relevance_filter")
    filter_doc_identity(relevance_filter, context["run_id"], {"decisions"})
    allowed_content_ids = {
        str(item["content_id"])
        for item in relevance_filter["decisions"]
        if item.get("decision") in {"relevant", "weak"}
    }
    comment_path = context_path.with_name("评论筛选结果.json")
    if not comment_path.is_file():
        raise TopicSearchCoordinatorError("comment_filter_missing")
    comment_filter = read_json(comment_path, "comment_filter")
    filter_doc_identity(comment_filter, context["run_id"], {"contents"})
    kept_comments_by_content = {
        str(item["content_id"]): {str(comment_id) for comment_id in item["kept_comment_ids"]}
        for item in comment_filter["contents"]
    }
    candidate_path = inside(root, args.candidate, "candidate")
    assert candidate_path is not None
    if candidate_path.parent != context_path.parent:
        raise TopicSearchCoordinatorError("candidate_outside_run")
    candidate = read_json(candidate_path, "candidate")
    topics = validate_candidate(
        candidate, evidence, context["run_id"], allowed_content_ids, kept_comments_by_content
    )
    plan_path = context_path.with_name("搜索计划.json")
    plan = read_json(plan_path, "search_plan")
    referenced_ids = {content_id for topic in topics for content_id in topic["reference_content_ids"]}
    source_contents = [item for item in evidence["contents"] if item.get("content_id") in referenced_ids]
    document = {
        "schema_version": SCHEMA_VERSION,
        "workflow_id": WORKFLOW_ID,
        "run_id": context["run_id"],
        "generated_at": dt.datetime.now().astimezone().replace(microsecond=0).isoformat(),
        "data_source": context["data_source"],
        "queries": plan["queries"],
        "topics": topics,
        "source_contents": source_contents,
    }
    formal_dir = root / FORMAL_ROOT / context["run_id"]
    if formal_dir.exists():
        raise TopicSearchCoordinatorError("formal_run_already_exists")
    json_path = formal_dir / "搜索选题结果.json"
    markdown_path = formal_dir / "搜索选题结果.md"
    write_json(json_path, document)
    write_text(markdown_path, render_markdown(document))
    return {
        "ok": True,
        "status": "completed",
        "commit_status": "committed",
        "workflow_id": WORKFLOW_ID,
        "run_id": context["run_id"],
        "topic_count": len(topics),
        "ranked_topics": [
            {"rank": item["rank"], "title": item["title"], "total_score": item["total_score"]}
            for item in topics
        ],
        "json_path": logical(root, json_path),
        "markdown_path": logical(root, markdown_path),
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="搜索选题流程协调器")
    parser.add_argument("--workspace-root", required=True)
    parser.add_argument(
        "--action",
        choices=("prepare", "freeze-queries", "filter-relevance", "filter-comments", "save"),
        required=True,
    )
    parser.add_argument("--overview-path", default="我方资料/我方当前概要.md")
    parser.add_argument("--data-source", choices=("chrome_devtools", "justoneapi"), default="")
    parser.add_argument("--platforms", default="小红书,抖音,B站")
    parser.add_argument("--context-file", default="")
    parser.add_argument("--query-plan", default="")
    parser.add_argument("--filter-json", default="")
    parser.add_argument("--candidate", default="")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        root = root_path(args.workspace_root)
        if args.action == "prepare":
            if not args.data_source:
                raise TopicSearchCoordinatorError("data_source_required")
            result = prepare(root, args)
        elif args.action == "freeze-queries":
            result = freeze_queries(root, args)
        elif args.action == "filter-relevance":
            result = filter_relevance(root, args)
        elif args.action == "filter-comments":
            result = filter_comments(root, args)
        else:
            result = save(root, args)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except (TopicSearchCoordinatorError, OSError, ValueError) as exc:
        print(json.dumps({"ok": False, "status": "blocked", "error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
