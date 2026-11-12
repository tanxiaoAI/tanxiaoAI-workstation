#!/usr/bin/env python3
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

from csv安全写入 import read_csv_table_with_snapshot, serialize_csv
from 账号分析证据分块执行器 import (
    AccountEvidenceChunkError,
    verify_account_evidence_sources,
    verify_account_evidence_summaries,
)
from 账号阶段状态 import build_account_business_stage_state
from 内容列表完整性判据 import (
    BASE_REPORT_RELATIVE,
    derive_content_list_completeness,
    read_content_list_fact_from_report,
)
from 详细数据报告存储器 import DetailDataContractError, load_report


ACCOUNT_LIBRARY_PATH = Path("对标资料/对标账号库.csv")
REPORT_TITLE_SUFFIX = "账号详细分析"
SUMMARY_START = "<!-- account-insight-summary:start -->"
SUMMARY_END = "<!-- account-insight-summary:end -->"
SUMMARY_VERSION = "account-insight-v1"
REQUIRED_H2 = (
    "综合结论",
    "账号表现",
    "1. 用户画像",
    "2. 产品服务体系",
    "3. 价值主张",
    "4. 人设风格",
    "5. 差异化",
    "6. 成立逻辑",
    "资料范围与来源",
)
MODULE_NAMES = REQUIRED_H2[2:-1]
# 页面数据区只承载五张卡；成立逻辑只出现在完整分析里，不进 JSON。
PAGE_MODULES = ("用户画像", "产品服务体系", "价值主张", "人设风格", "差异化")
MODULE_HEADINGS = {name: REQUIRED_H2[index + 2] for index, name in enumerate(PAGE_MODULES)}
MODULE_SUBSECTIONS = ("核心描述", "细分分析", "依据与边界")
FORBIDDEN_HEADINGS = {
    "1. 模式层",
    "2. 内容层",
    "3. 账号层",
    "模式层",
    "内容层",
    "账号层",
    "转化路径",
    "关键依赖",
    "待确认点",
    "成功要素",
}
PERFORMANCE_FIELDS = ("一句话结论", "图表解读")
PRODUCT_TYPES = {"免费资源", "工具", "社群", "课程", "线下课", "咨询", "定制", "陪跑", "其他"}
PRICE_STATES = {"免费", "明确标价", "未公开标价", "未获取", "口径冲突"}
EVIDENCE_TYPES = {"公开资料", "作者自述", "人工补充", "程序统计"}
PERSONA_CATEGORIES = {"身份", "阶段", "需求", "行为条件"}
TENDENCY_ENUMS = {
    "语气": {"偏委婉", "中间或混合", "偏直给"},
    "互动姿态": {"偏讲授", "中间或混合", "偏交流"},
}
DIFF_COMPARE_STATES = {"已提供同类对比", "未提供同类对比"}


class AccountDetailFinalizeError(RuntimeError):
    pass


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="校验并正式保存账号详细分析")
    # prepare 只做干跑校验，并返回内容层「证据范围」字段唯一合法取值；
    # commit 负责正式写入。流程必须先调 prepare 拿 evidence_scope，再调 commit。
    parser.add_argument("--phase", choices=("prepare", "commit"), default="prepare")
    parser.add_argument("--workspace-root", required=True)
    parser.add_argument("--account-id", required=True)
    parser.add_argument("--platform", default="")
    parser.add_argument("--evidence-dir", required=True)
    parser.add_argument("--handoff-json", required=True)
    parser.add_argument(
        "--candidate",
        default="",
        help="新版单份候选稿（标题 + 页面数据区 + 综合结论 + 账号表现 + 六模块）",
    )
    parser.add_argument(
        "--stats-file",
        default="",
        help="账号表现程序统计 JSON（可选；提供时校验其存在与账号一致）",
    )
    return parser.parse_args()


def resolve_under(root: Path, raw_path: str, *, required_prefix: str = "") -> Path:
    normalized = str(raw_path or "").strip().replace("\\", "/")
    if not normalized:
        raise AccountDetailFinalizeError("path_missing")
    logical = PurePosixPath(normalized)
    candidate = Path(normalized).expanduser()
    resolved = candidate.resolve() if candidate.is_absolute() else root.joinpath(*logical.parts).resolve()
    try:
        relative = resolved.relative_to(root)
    except ValueError as exc:
        raise AccountDetailFinalizeError(f"path_outside_workspace:{normalized}") from exc
    if required_prefix and not relative.as_posix().startswith(required_prefix):
        raise AccountDetailFinalizeError(f"path_outside_required_prefix:{normalized}:{required_prefix}")
    return resolved


def extract_unique_field(markdown: str, label: str) -> str:
    matches = re.findall(rf"(?m)^-\s*{re.escape(label)}\s*[：:]\s*(.*?)\s*$", markdown)
    if len(matches) != 1 or not matches[0].strip():
        raise AccountDetailFinalizeError(f"layer_field_invalid:{label}")
    return matches[0].strip()


def format_evidence_scope(
    base_scope_fact: dict[str, object],
    incomplete: bool | None = None,
    *,
    selected: int,
    selected_body: int,
    selected_basic_only: int,
    scope_mode: str,
    content_list_status: str = "",
    stop_reasons: object = None,
) -> str:
    try:
        total = int(base_scope_fact["total_content_count"])
    except (KeyError, TypeError, ValueError) as exc:
        raise AccountDetailFinalizeError("handoff_scope_fact_invalid") from exc
    if (
        min(total, selected, selected_body, selected_basic_only) < 0
        or selected > total
        or selected_body + selected_basic_only != selected
    ):
        raise AccountDetailFinalizeError("handoff_scope_fact_invalid")
    status = str(content_list_status or "").strip()
    if status:
        # 完整性判据由 内容列表完整性判据.py 单点提供，概要分析共用同一套规则。
        # 只读状态与采集停止原因，不读 data_limitations（那是资料缺口总表，
        # 会把截图失败误报成内容列表未抓全）。
        completeness = derive_content_list_completeness(status, stop_reasons)
    else:
        # Compatibility is limited to callers that already have a machine
        # boolean; production handoff paths always pass the base-report fact.
        completeness = "可能未抓全" if incomplete is True else "完整" if incomplete is False else "未知"
    scope_labels = {
        "latest": f"最近 {selected} 条",
        "list_order": f"列表前 {selected} 条",
        "all": f"全部 {selected} 条",
        "custom": f"指定的 {selected} 条",
    }
    if scope_mode not in scope_labels:
        raise AccountDetailFinalizeError("handoff_scope_invalid")
    return (
        f"全量基础字段覆盖账号内容列表共 {total} 条；"
        f"详细样本使用{scope_labels[scope_mode]}，其中 "
        f"{selected_body} 条有正文级证据、{selected_basic_only} 条只有基础数据；"
        f"内容列表完整性：{completeness}"
    )


def read_canonical_detail_report(path: Path, *, account_id: str, platform: str) -> dict[str, object]:
    """Consume a report already validated by the canonical store."""
    if path.name != "canonical-detail-report.json" or path.parent.name != "detail-data":
        raise AccountDetailFinalizeError("canonical_report_path_invalid")
    try:
        report = load_report(
            path,
            flow_type="benchmark_account",
            account_id=account_id,
        )
    except DetailDataContractError as exc:
        message = str(exc)
        if "account_id 不一致" in message:
            raise AccountDetailFinalizeError("handoff_account_mismatch") from exc
        raise AccountDetailFinalizeError(f"canonical_report_invalid:{message}") from exc
    if report.get("status") not in {"completed", "partial"}:
        raise AccountDetailFinalizeError("canonical_report_not_ready")
    collection = report["detail_collection"]
    return {
        "status": report["status"],
        "account": {"account_id": account_id, "platform": platform},
        "selected_content_ids": list(report["requested_content_ids"]),
        "scope_mode": report.get("scope_mode"),
        "scope_count": report.get("scope_count"),
        "facts": list(collection["items"]),
        "counts": dict(report["counts"]),
    }


# Compatibility alias for callers that still use the old private name.  It is
# intentionally read-only and does not perform full schema validation.
read_handoff = read_canonical_detail_report


def section_body(markdown: str, name: str) -> str:
    pattern = re.compile(rf"(?m)^##\s+{re.escape(name)}\s*$")
    match = pattern.search(markdown)
    if match is None:
        raise AccountDetailFinalizeError(f"report_section_missing:{name}")
    following = re.compile(r"(?m)^##\s+").search(markdown, match.end())
    return markdown[match.end(): following.start() if following else len(markdown)]


def subsection_body(module_body: str, name: str) -> str:
    pattern = re.compile(rf"(?m)^###\s+{re.escape(name)}\s*$")
    match = pattern.search(module_body)
    if match is None:
        raise AccountDetailFinalizeError(f"module_subsection_missing:{name}")
    following = re.compile(r"(?m)^###\s+").search(module_body, match.end())
    return module_body[match.end(): following.start() if following else len(module_body)]


def _require_text(value: object, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise AccountDetailFinalizeError(f"summary_field_invalid:{label}")
    return value.strip()


def _require_list(value: object, label: str) -> list:
    if not isinstance(value, list):
        raise AccountDetailFinalizeError(f"summary_field_invalid:{label}")
    return value


def _require_dict(value: object, label: str) -> dict:
    if not isinstance(value, dict):
        raise AccountDetailFinalizeError(f"summary_field_invalid:{label}")
    return value


def _evidence_ids(node: object) -> set[str]:
    found: set[str] = set()
    if isinstance(node, dict):
        for key, value in node.items():
            if key == "证据ID":
                for item in _require_list(value, "证据ID"):
                    found.add(_require_text(item, "证据ID"))
            else:
                found.update(_evidence_ids(value))
    elif isinstance(node, list):
        for item in node:
            found.update(_evidence_ids(item))
    return found


def extract_summary_json(markdown: str) -> dict[str, object]:
    if markdown.count(SUMMARY_START) != 1 or markdown.count(SUMMARY_END) != 1:
        raise AccountDetailFinalizeError("summary_block_marker_invalid")
    if markdown.index(SUMMARY_START) > markdown.index(SUMMARY_END):
        raise AccountDetailFinalizeError("summary_block_marker_order_invalid")
    block = markdown.split(SUMMARY_START, 1)[1].split(SUMMARY_END, 1)[0]
    match = re.search(r"```json\s*\n?(.*?)\n?```", block, re.S)
    if match is None:
        raise AccountDetailFinalizeError("summary_json_fence_missing")
    try:
        data = json.loads(match.group(1))
    except json.JSONDecodeError as exc:
        raise AccountDetailFinalizeError(f"summary_json_invalid:{exc}") from exc
    if not isinstance(data, dict):
        raise AccountDetailFinalizeError("summary_json_not_object")
    return data


def validate_summary_data(
    data: dict[str, object],
    *,
    account_id: str,
    account_name: str,
    markdown: str,
    evidence_scope: str,
    require_performance_evidence: bool,
) -> None:
    if data.get("版本") != SUMMARY_VERSION:
        raise AccountDetailFinalizeError("summary_version_invalid")
    summary_account_id = data.get("账号ID")
    if summary_account_id is not None and str(summary_account_id).strip() != account_id:
        raise AccountDetailFinalizeError("summary_account_id_mismatch")
    summary_account_name = data.get("账号名")
    if summary_account_name is not None and str(summary_account_name).strip() != account_name:
        raise AccountDetailFinalizeError("summary_account_name_mismatch")

    for key in ("综合结论", "账号表现", *PAGE_MODULES):
        _require_dict(data.get(key), key)

    conclusion = _require_dict(data.get("综合结论"), "综合结论")
    conclusion_text = _require_text(conclusion.get("文本"), "综合结论.文本")
    conclusion_phrases = _require_list(conclusion.get("重点短语"), "综合结论.重点短语")
    for phrase in conclusion_phrases:
        if _require_text(phrase, "综合结论.重点短语") not in conclusion_text:
            raise AccountDetailFinalizeError("summary_phrase_not_in_text:综合结论")
    if section_body(markdown, "综合结论").strip() != conclusion_text:
        raise AccountDetailFinalizeError("report_conclusion_mismatch")

    performance = _require_dict(data.get("账号表现"), "账号表现")
    performance_sentence = _require_text(
        performance.get("一句话结论"), "账号表现.一句话结论"
    )
    _require_text(performance.get("图表解读"), "账号表现.图表解读")
    performance_evidence = _require_list(performance.get("证据ID"), "账号表现.证据ID")
    if require_performance_evidence and not performance_evidence:
        raise AccountDetailFinalizeError("summary_performance_evidence_missing")
    performance_body = section_body(markdown, "账号表现")
    performance_fields = {
        label: extract_unique_field(performance_body, label) for label in PERFORMANCE_FIELDS
    }
    if performance_fields["一句话结论"] != performance_sentence:
        raise AccountDetailFinalizeError("report_performance_mismatch")

    for module in PAGE_MODULES:
        block = _require_dict(data.get(module), module)
        description = _require_text(block.get("核心描述"), f"{module}.核心描述")
        module_body = section_body(markdown, MODULE_HEADINGS[module])
        if subsection_body(module_body, "核心描述").strip() != description:
            raise AccountDetailFinalizeError(f"report_module_description_mismatch:{module}")

    user = _require_dict(data.get("用户画像"), "用户画像")
    tags = _require_list(user.get("标签"), "用户画像.标签")
    if len(tags) > 4:
        raise AccountDetailFinalizeError("summary_user_tag_count_invalid")
    for tag in tags:
        item = _require_dict(tag, "用户画像.标签")
        if _require_text(item.get("类别"), "用户画像.标签.类别") not in PERSONA_CATEGORIES:
            raise AccountDetailFinalizeError("summary_user_tag_category_invalid")
        _require_text(item.get("文本"), "用户画像.标签.文本")
        _require_list(item.get("证据ID"), "用户画像.标签.证据ID")
    needs = _require_list(user.get("需求"), "用户画像.需求")
    if len(needs) > 3:
        raise AccountDetailFinalizeError("summary_user_need_count_invalid")
    for need in needs:
        item = _require_dict(need, "用户画像.需求")
        _require_text(item.get("文本"), "用户画像.需求.文本")
        _require_list(item.get("证据ID"), "用户画像.需求.证据ID")

    products = _require_list(
        _require_dict(data.get("产品服务体系"), "产品服务体系").get("产品"),
        "产品服务体系.产品",
    )
    for product in products:
        item = _require_dict(product, "产品服务体系.产品")
        if _require_text(item.get("类型"), "产品服务体系.产品.类型") not in PRODUCT_TYPES:
            raise AccountDetailFinalizeError("summary_product_type_invalid")
        price = _require_dict(item.get("价格"), "产品服务体系.产品.价格")
        state = _require_text(price.get("状态"), "产品服务体系.产品.价格.状态")
        if state not in PRICE_STATES:
            raise AccountDetailFinalizeError("summary_product_price_state_invalid")
        amount = price.get("金额")
        if state == "明确标价":
            if not isinstance(amount, (int, float)):
                raise AccountDetailFinalizeError("summary_product_price_amount_invalid")
        elif amount is not None:
            raise AccountDetailFinalizeError("summary_product_price_amount_unexpected")

    value = _require_dict(data.get("价值主张"), "价值主张")
    value_text = _require_text(value.get("核心描述"), "价值主张.核心描述")
    for phrase in _require_list(value.get("重点短语"), "价值主张.重点短语"):
        if _require_text(phrase, "价值主张.重点短语") not in value_text:
            raise AccountDetailFinalizeError("summary_phrase_not_in_text:价值主张")

    persona = _require_dict(data.get("人设风格"), "人设风格")
    for key in ("身份标签", "角色标签", "表达标签"):
        for tag in _require_list(persona.get(key), f"人设风格.{key}"):
            item = _require_dict(tag, f"人设风格.{key}")
            _require_text(item.get("文本"), f"人设风格.{key}.文本")
            _require_list(item.get("证据ID"), f"人设风格.{key}.证据ID")
    for tendency in _require_list(persona.get("表达倾向"), "人设风格.表达倾向"):
        item = _require_dict(tendency, "人设风格.表达倾向")
        dimension = _require_text(item.get("维度"), "人设风格.表达倾向.维度")
        if dimension not in TENDENCY_ENUMS:
            raise AccountDetailFinalizeError("summary_tendency_dimension_invalid")
        if _require_text(item.get("倾向"), "人设风格.表达倾向.倾向") not in TENDENCY_ENUMS[dimension]:
            raise AccountDetailFinalizeError("summary_tendency_value_invalid")

    difference = _require_dict(data.get("差异化"), "差异化")
    if _require_text(difference.get("对比状态"), "差异化.对比状态") not in DIFF_COMPARE_STATES:
        raise AccountDetailFinalizeError("summary_difference_status_invalid")
    for feature in _require_list(difference.get("特点"), "差异化.特点"):
        item = _require_dict(feature, "差异化.特点")
        _require_text(item.get("本账号特点"), "差异化.特点.本账号特点")

    index = _require_list(data.get("证据索引"), "证据索引")
    known_ids: set[str] = set()
    for entry in index:
        item = _require_dict(entry, "证据索引")
        evidence_id = _require_text(item.get("ID"), "证据索引.ID")
        if evidence_id in known_ids:
            raise AccountDetailFinalizeError(f"summary_evidence_id_duplicate:{evidence_id}")
        known_ids.add(evidence_id)
        if _require_text(item.get("来源类型"), "证据索引.来源类型") not in EVIDENCE_TYPES:
            raise AccountDetailFinalizeError("summary_evidence_type_invalid")
        _require_text(item.get("来源定位"), "证据索引.来源定位")
    missing_ids = sorted(_evidence_ids(data) - known_ids)
    if missing_ids:
        raise AccountDetailFinalizeError(
            "summary_evidence_id_unknown:" + ",".join(missing_ids)
        )

    scope_body = section_body(markdown, "资料范围与来源")
    if evidence_scope and evidence_scope not in scope_body:
        raise AccountDetailFinalizeError("report_evidence_scope_missing")


def validate_candidate(
    path: Path,
    *,
    account_id: str,
    account_name: str,
    evidence_scope: str,
    require_performance_evidence: bool,
) -> str:
    try:
        markdown = path.read_text(encoding="utf-8-sig").strip()
    except (OSError, UnicodeError) as exc:
        raise AccountDetailFinalizeError(f"candidate_read_failed:{exc}") from exc
    if not markdown:
        raise AccountDetailFinalizeError("candidate_empty")
    expected_title = f"# {account_name}{REPORT_TITLE_SUFFIX}"
    if markdown.splitlines()[0].strip() != expected_title:
        raise AccountDetailFinalizeError("candidate_title_invalid")
    headings = [line.strip() for line in markdown.splitlines() if line.startswith("## ")]
    if [line[3:].strip() for line in headings] != list(REQUIRED_H2):
        raise AccountDetailFinalizeError("candidate_headings_invalid")
    for line in markdown.splitlines():
        stripped = line.strip()
        if not (stripped.startswith("## ") or stripped.startswith("### ")):
            continue
        name = stripped.lstrip("#").strip()
        if name in FORBIDDEN_HEADINGS:
            raise AccountDetailFinalizeError(f"candidate_forbidden_heading:{name}")
    for module in MODULE_NAMES:
        subsections = [
            item.strip()
            for item in re.findall(
                r"(?m)^###\s+(.+?)\s*$", section_body(markdown, module)
            )
        ]
        if subsections != list(MODULE_SUBSECTIONS):
            raise AccountDetailFinalizeError(f"candidate_module_subsections_invalid:{module}")
    data = extract_summary_json(markdown)
    validate_summary_data(
        data,
        account_id=account_id,
        account_name=account_name,
        markdown=markdown,
        evidence_scope=evidence_scope,
        require_performance_evidence=require_performance_evidence,
    )
    return markdown


def stage_payload(target: Path, payload: bytes, suffix: str) -> Path:
    target.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{target.name}.", suffix=suffix, dir=str(target.parent)
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
    except Exception:
        temporary.unlink(missing_ok=True)
        raise
    return temporary


def commit_payloads_atomically(
    payloads: dict[Path, bytes], *, expected_originals: dict[Path, bytes | None]
) -> None:
    originals = {path: path.read_bytes() if path.exists() else None for path in payloads}
    for path, expected in expected_originals.items():
        if originals.get(path) != expected:
            raise AccountDetailFinalizeError(f"concurrent_source_change:{path}")
    staged: dict[Path, Path] = {}
    committed: list[Path] = []
    try:
        for target, payload in payloads.items():
            staged[target] = stage_payload(target, payload, ".stage")
        for target, original in originals.items():
            current = target.read_bytes() if target.exists() else None
            if current != original:
                raise AccountDetailFinalizeError(f"concurrent_source_change:{target}")
        for target in payloads:
            os.replace(staged[target], target)
            committed.append(target)
        for target, payload in payloads.items():
            if target.read_bytes() != payload:
                raise AccountDetailFinalizeError(f"post_commit_mismatch:{target}")
    except Exception as exc:
        rollback_errors: list[str] = []
        for target in reversed(committed):
            restore: Path | None = None
            try:
                original = originals[target]
                if original is None:
                    target.unlink(missing_ok=True)
                else:
                    restore = stage_payload(target, original, ".rollback")
                    os.replace(restore, target)
                    restore = None
            except Exception as rollback_exc:
                rollback_errors.append(f"{target}:{rollback_exc}")
            finally:
                if restore is not None:
                    restore.unlink(missing_ok=True)
        if rollback_errors:
            raise AccountDetailFinalizeError(
                f"commit_failed:{exc};rollback_failed:{';'.join(rollback_errors)}"
            ) from exc
        if isinstance(exc, AccountDetailFinalizeError):
            raise
        raise AccountDetailFinalizeError(f"commit_failed:{exc};rollback_completed") from exc
    finally:
        for temporary in staged.values():
            temporary.unlink(missing_ok=True)


def prepare_evidence_contract(root: Path, args: argparse.Namespace) -> dict[str, object]:
    evidence_dir = resolve_under(root, args.evidence_dir, required_prefix=".meiling/staging/")
    manifest_path = evidence_dir / "chunk_manifest.json"
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise AccountDetailFinalizeError(f"manifest_read_failed:{exc}") from exc
    account_node = manifest.get("account")
    if not isinstance(account_node, dict):
        raise AccountDetailFinalizeError("manifest_account_invalid")
    if manifest.get("account_type") != "benchmark" or manifest.get("mode") != "detail":
        raise AccountDetailFinalizeError("manifest_scope_invalid")
    if str(account_node.get("账号ID") or "").strip() != args.account_id:
        raise AccountDetailFinalizeError("manifest_account_mismatch")
    if args.platform and str(account_node.get("平台") or "").strip() != args.platform:
        raise AccountDetailFinalizeError("manifest_platform_mismatch")
    if not manifest.get("coverage_complete"):
        raise AccountDetailFinalizeError("manifest_coverage_incomplete")
    verify_account_evidence_sources(root, manifest)
    verified = verify_account_evidence_summaries(root, output_dir=evidence_dir.relative_to(root).as_posix())
    if verified.get("status") != "verified":
        raise AccountDetailFinalizeError("evidence_verification_failed")

    handoff_path = resolve_under(root, args.handoff_json)
    handoff = read_handoff(
        handoff_path,
        account_id=args.account_id,
        platform=args.platform,
    )
    try:
        manifest_total_count = int(manifest.get("total_content_count") or 0)
    except (TypeError, ValueError) as exc:
        raise AccountDetailFinalizeError("handoff_scope_fact_invalid") from exc
    # The canonical report stores per-item facts, not a second coverage
    # aggregate.  Manifest/base-data scope remains the sole total-count fact.
    if manifest_total_count != int(manifest.get("base_context_content_count") or 0):
        raise AccountDetailFinalizeError("manifest_base_context_count_mismatch")
    if manifest.get("detailed_content_ids") != handoff.get("selected_content_ids"):
        raise AccountDetailFinalizeError("handoff_manifest_selection_mismatch")
    try:
        selected_count = int(manifest["selected_content_count"])
        selected_body_count = int(manifest["selected_body_evidence_content_count"])
        selected_basic_only_count = int(manifest["selected_basic_only_content_count"])
    except (KeyError, TypeError, ValueError) as exc:
        raise AccountDetailFinalizeError("manifest_scope_fact_invalid") from exc
    content_list_status, stop_reasons = read_base_content_list_fact(
        root,
        account_id=args.account_id,
        platform=args.platform,
    )
    evidence_scope = format_evidence_scope(
        {"total_content_count": manifest_total_count},
        None,
        selected=selected_count,
        selected_body=selected_body_count,
        selected_basic_only=selected_basic_only_count,
        scope_mode=str(handoff.get("scope_mode") or ""),
        content_list_status=content_list_status,
        stop_reasons=stop_reasons,
    )
    return {
        "evidence_dir": evidence_dir,
        "handoff_path": handoff_path,
        "verified": verified,
        "evidence_scope": evidence_scope,
    }


def read_base_content_list_fact(
    root: Path, *, account_id: str, platform: str
) -> tuple[str, list[str]]:
    """Read the sole content-list boundary fact from the base-data report.

    返回内容列表获取状态和采集侧停止原因；这两项是内容列表完整性判据的
    全部输入。data_limitations 是资料缺口总表，不作为判据输入。
    """
    unknown = ("", [])
    library_path = root / ACCOUNT_LIBRARY_PATH
    try:
        fields, rows, _ = read_csv_table_with_snapshot(library_path)
    except (OSError, UnicodeError, ValueError):
        return unknown
    if not fields:
        return unknown
    matches = [
        row for row in rows
        if str(row.get("账号ID") or "").strip() == account_id
        and (not platform or str(row.get("平台") or "").strip() == platform)
    ]
    if len(matches) != 1:
        return unknown
    content_raw = str(matches[0].get("内容列表路径") or "").strip()
    if not content_raw:
        return unknown
    try:
        content_path = resolve_under(root, content_raw)
    except AccountDetailFinalizeError:
        return unknown
    report_path = content_path.parent / BASE_REPORT_RELATIVE
    if report_path.is_symlink():
        return unknown
    return read_content_list_fact_from_report(report_path)


def run(args: argparse.Namespace) -> dict[str, object]:
    root = Path(args.workspace_root).expanduser().resolve()
    if not root.is_dir():
        raise AccountDetailFinalizeError(f"workspace_root_invalid:{root}")
    phase = str(getattr(args, "phase", "commit") or "commit").strip()
    if phase not in {"prepare", "commit"}:
        raise AccountDetailFinalizeError(f"phase_invalid:{phase}")
    contract = prepare_evidence_contract(root, args)
    verified = contract["verified"]
    if not isinstance(verified, dict):
        raise AccountDetailFinalizeError("evidence_verification_failed")
    evidence_scope = str(contract["evidence_scope"])
    if phase == "prepare":
        return {
            "ok": True,
            "status": "prepared",
            "verified_evidence_path": verified["verified_evidence_path"],
            "verified_chunk_count": verified["verified_chunk_count"],
            "evidence_scope": evidence_scope,
        }

    candidate_raw = str(args.candidate or "").strip()
    if not candidate_raw:
        raise AccountDetailFinalizeError("candidate_missing")
    candidate_path = resolve_under(
        root, candidate_raw, required_prefix=".meiling/staging/"
    )
    stats_path = (
        resolve_under(root, args.stats_file, required_prefix=".meiling/staging/")
        if str(args.stats_file or "").strip()
        else None
    )
    if stats_path is not None:
        try:
            stats = json.loads(stats_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise AccountDetailFinalizeError(f"stats_read_failed:{exc}") from exc
        stats_account = stats.get("账号") if isinstance(stats, dict) else None
        if not isinstance(stats_account, dict):
            raise AccountDetailFinalizeError("stats_account_invalid")
        if str(stats_account.get("账号ID") or "").strip() != args.account_id:
            raise AccountDetailFinalizeError("stats_account_mismatch")

    handoff_path = contract["handoff_path"]
    if not isinstance(handoff_path, Path):
        raise AccountDetailFinalizeError("handoff_path_invalid")
    account_library_path = root / ACCOUNT_LIBRARY_PATH
    fields, rows, account_library_original = read_csv_table_with_snapshot(account_library_path)
    matches = [
        row
        for row in rows
        if row.get("账号ID", "").strip() == args.account_id
        and (not args.platform or row.get("平台", "").strip() == args.platform)
    ]
    if len(matches) != 1:
        raise AccountDetailFinalizeError(f"account_match_count:{len(matches)}")
    account = matches[0]
    for field in ("账号名", "平台", "内容列表路径", "详细分析路径"):
        if field not in fields:
            raise AccountDetailFinalizeError(f"account_library_field_missing:{field}")
    account_name = account.get("账号名", "").strip()
    if not account_name:
        raise AccountDetailFinalizeError("account_name_missing")
    content_list_path = resolve_under(root, account.get("内容列表路径", ""))
    if handoff_path.name != "canonical-detail-report.json" or handoff_path.parent.name != "detail-data":
        raise AccountDetailFinalizeError("canonical_report_path_invalid")
    # 正式文档只用无后缀文件名：重跑直接覆盖，旧版与新版页面读同一份。
    formal_path = content_list_path.parent / "详细分析.md"
    state_path = content_list_path.parent / "过程记录" / "当前阶段状态.json"
    formal_relative = formal_path.relative_to(root).as_posix()
    account["详细分析路径"] = formal_relative

    report = validate_candidate(
        candidate_path,
        account_id=args.account_id,
        account_name=account_name,
        evidence_scope=evidence_scope,
        require_performance_evidence=stats_path is not None,
    )
    report = report.rstrip() + "\n"
    report_payload = report.encode("utf-8")
    state = build_account_business_stage_state(
        stage="账号详细分析",
        complete=True,
        platform=account.get("平台", ""),
        account_id=args.account_id,
        account_name=account_name,
    )
    state_payload = (json.dumps(state, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    csv_payload = serialize_csv(fields, rows)
    commit_payloads_atomically(
        {formal_path: report_payload, account_library_path: csv_payload, state_path: state_payload},
        expected_originals={account_library_path: account_library_original},
    )
    return {
        "ok": True,
        "status": "committed",
        "formal_path": formal_relative,
        "account_library_path": ACCOUNT_LIBRARY_PATH.as_posix(),
        "stage_state_path": state_path.relative_to(root).as_posix(),
        "verified_evidence_path": verified["verified_evidence_path"],
        "verified_chunk_count": verified["verified_chunk_count"],
        "evidence_scope": evidence_scope,
        "formal_sha256": hashlib.sha256(report_payload).hexdigest(),
    }


def main() -> int:
    try:
        result = run(parse_args())
    except (OSError, ValueError, AccountDetailFinalizeError, AccountEvidenceChunkError) as exc:
        print(json.dumps({"ok": False, "status": "failed", "error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
