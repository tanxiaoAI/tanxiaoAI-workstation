#!/usr/bin/env python3
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import re
import subprocess
import sys
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Dict, Iterable, Iterator, List

_EXECUTION_UNIT_DIR = Path(__file__).resolve().parent
if str(_EXECUTION_UNIT_DIR) not in sys.path:
    sys.path.insert(0, str(_EXECUTION_UNIT_DIR))

from csv安全写入 import (
    read_csv_table,
    read_csv_table_with_snapshot,
    write_csv_tables_atomically,
)
from 后续详细处理报告同步 import output_file_is_valid, summarize_items
from 过程记录路径 import (
    build_content_dir_name,
    build_content_process_dir,
    collect_content_dir_owners,
    plan_content_dir_names,
)
from 账号基础数据建档批处理 import normalize_public_status
from 账号阶段状态 import (
    build_account_stage_state,
    write_bytes_atomically,
    write_json_atomically,
    write_state_atomically,
)
from 详细数据阶段合同 import (
    DetailDataContractError,
    finalize_item_facts,
    retryable_content_ids,
    structured_error,
    validate_canonical_report,
    validate_identity,
)
from 获取视频文案执行器 import (
    douyin_asr_url_rejection,
    extract_creator_center_play_addr,
    extract_douyin_play_addr,
    fetch_bilibili_audio_url,
    fetch_bilibili_ids,
    materialize_bilibili_platform_caption,
    materialize_xiaohongshu_platform_caption,
)
from 正文证据选择器 import platform_caption_evidence, video_url_hash
import 详细数据报告存储器 as canonical_store


def _bilibili_audio_url_for_row(row: Dict[str, Any], video_url: str) -> str:
    """B站 的音视频是分离流：台账里的下载链接可能只有画面、没有声音。

    与 `对标内容数据建档执行器.asr_audio_url_for` 同源。B站 DASH 把画面和声音拆成两条
    轨道，列表/接口给的「视频下载链接」不保证是带音轨的那条；把纯视频轨交给 ASR，火山会
    直接失败（45000000）或先失败一次再回落本地重跑。所以进 ASR 前对 B站 重新解析一次音频轨。

    台账可能只存了 bvid、aid/cid 为空（例如 chrome_devtools 取数），此时先按 bvid 从公开
    视频信息接口补齐，再解析音频轨，保证与单条内容处理链路一致。缺 cid 或解析失败时回落到
    原 URL，不阻断本轮。
    """
    if str(row.get("平台") or "").strip() != "B站":
        return video_url
    bvid = str(row.get("平台原生内容ID") or row.get("bvid") or "").strip()
    aid = str(row.get("aid") or "").strip()
    cid = str(row.get("cid") or "").strip()
    if not cid or not (bvid or aid):
        link = str(row.get("对外页面链接") or "").strip() or (
            f"https://www.bilibili.com/video/{bvid}" if bvid else video_url
        )
        try:
            resolved_bvid, resolved_aid, resolved_cid = fetch_bilibili_ids(link)
        except RuntimeError:  # noqa: BLE001 - 补齐失败不阻断，交回原 URL 让 ASR 如实失败
            resolved_bvid, resolved_aid, resolved_cid = "", "", ""
        bvid = bvid or resolved_bvid
        aid = aid or resolved_aid
        cid = cid or resolved_cid
    if not cid or not (bvid or aid):
        return video_url
    try:
        return fetch_bilibili_audio_url(bvid, aid, cid)
    except Exception:  # noqa: BLE001 - 解析失败不阻断，交回原 URL 让 ASR 如实失败
        return video_url




def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="按锁定批次执行详细数据处理并维护唯一 canonical report。"
    )
    parser.add_argument("--account-id", default="", help=argparse.SUPPRESS)
    parser.add_argument("--content-list", default="", help=argparse.SUPPRESS)
    parser.add_argument("--content-library", default="", help=argparse.SUPPRESS)
    parser.add_argument(
        "--content-ids",
        default="",
        help=argparse.SUPPRESS,
    )
    parser.add_argument(
        "--workspace-root",
        default=".",
        help="工作区根目录",
    )
    parser.add_argument(
        "--cover-command",
        default="",
        help=argparse.SUPPRESS,
    )
    parser.add_argument(
        "--transcript-command",
        default="",
        help=argparse.SUPPRESS,
    )
    parser.add_argument("--run-id", default="", help="本轮唯一标识")
    parser.add_argument("--action", choices=("process", "finalize"), default="", help="批次动作；必须与 --run-id 配合使用")
    parser.add_argument("--asr-result", default="", help=argparse.SUPPRESS)
    parser.add_argument("--flow-type", default="", choices=("", "benchmark_account", "self_account"), help=argparse.SUPPRESS)
    parser.add_argument("--task-id", default="", help=argparse.SUPPRESS)
    parser.add_argument(
        "--failed-only",
        action="store_true",
        help=argparse.SUPPRESS,
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help=argparse.SUPPRESS,
    )
    return parser.parse_args(argv)


def build_single_item_command(
    args: argparse.Namespace,
    content_id: str,
    executor_path: Path,
    *,
    cover_only: bool = False,
) -> List[str]:
    command = [
        sys.executable,
        str(executor_path),
        "--account-id",
        args.account_id,
        "--content-id",
        content_id,
        "--content-list",
        args.content_list,
        "--content-library",
        args.content_library,
        "--workspace-root",
        str(Path(args.workspace_root).resolve()),
        "--cover-command",
        args.cover_command,
        "--transcript-command",
        args.transcript_command,
        "--flow-type",
        str(getattr(args, "flow_type", "") or ""),
    ]
    run_id = str(getattr(args, "run_id", "") or "").strip()
    if run_id:
        command.extend(["--run-id", run_id])
    task_id = str(getattr(args, "task_id", "") or "").strip()
    # 对标账号详细动作只以 run_id 绑定，内部 task_id 不得进入公开单条执行器契约。
    if task_id and str(getattr(args, "flow_type", "") or "").strip() == "self_account":
        command.extend(["--task-id", task_id])
    if args.dry_run:
        command.append("--dry-run")
    return command


def run_single_item_executor(command: List[str], *, cwd: Path) -> Dict[str, object]:
    result = subprocess.run(
        command,
        cwd=str(cwd),
        text=True,
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        stderr = (result.stderr or result.stdout or "").strip()
        raise RuntimeError(stderr if stderr else "单条内容执行器运行失败")
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"单条内容执行器返回的不是有效 JSON: {exc}") from exc


def build_default_report_path(content_list_path: Path) -> Path:
    raise RuntimeError("canonical report path must come from the locked batch context")


def build_task_stage_paths(
    workspace_root: Path,
    task_id: str,
    run_id: str,
    content_list_path: Path,
    account_id: str = "",
    flow_type: str = "self_account",
) -> Dict[str, object]:
    """Resolve the run-scoped detail-data paths for one flow.

    Both flows are run-scoped: the batch lives under
    `.meiling/staging/<flow-root>/<run_id>/detail-data/`, and no scheduler state
    tree is consulted. `task_id` is retained only for signature compatibility.
    """
    safe_id = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}")
    if not safe_id.fullmatch(run_id):
        raise RuntimeError("run-id 必须是安全的单段标识")
    if flow_type not in {"benchmark_account", "self_account"}:
        raise RuntimeError("任务模式缺少有效 flow_type")
    root = workspace_root.resolve()
    flow_root = "benchmark-account-diagnose" if flow_type == "benchmark_account" else "self-account-diagnose"
    stage_dir = root / ".meiling/staging" / flow_root / run_id / "detail-data"
    if stage_dir.exists() and stage_dir.resolve() != stage_dir:
        raise RuntimeError("当前任务暂存目录不能经过符号链接")
    detail_candidates = [stage_dir / "canonical-detail-report.json"]
    detail_report_path: Path | None = None
    detail_report_error = ""
    try:
        confirmed_report = next(
            (path.resolve() for path in detail_candidates if path.is_file() and not path.is_symlink()),
            None,
        )
        if confirmed_report is not None:
            report = canonical_store.load_report(confirmed_report, run_id=run_id, validate=False)
        detail_report_path = confirmed_report
    except (OSError, UnicodeError, json.JSONDecodeError, RuntimeError) as exc:
        detail_report_error = str(exc)
    return {
        "stage_dir": stage_dir,
        "stage_state_json": stage_dir / "批次上下文.json",
        "task_state_json": None,
        "scope_json": stage_dir / "内容范围.json",
        "detail_report_candidates": detail_candidates,
        "detail_report_json": detail_report_path,
        "detail_report_error": detail_report_error,
    }


def require_exact_path(raw_value: object, expected: Path, label: str) -> Path:
    value = str(raw_value or "").strip()
    if expected.is_symlink():
        raise RuntimeError(f"{label}固定路径不能是符号链接")
    if value and Path(value).expanduser().resolve() != expected.resolve():
        raise RuntimeError(f"{label}必须使用当前任务固定路径")
    return expected.resolve()


def print_progress(message: str) -> None:
    print(message, file=sys.stderr, flush=True)


def file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_normalized_asr_batch_result(path: Path) -> Dict[str, object]:
    """Load only the host-normalized ASR business object.

    MCP envelopes are transport data, not batch results. Accepting them here
    would force the model or this executor to guess which nested field is
    authoritative and would reintroduce an unsafe manual parsing boundary.
    """
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise RuntimeError("asr_batch_payload_invalid") from exc
    if not isinstance(payload, dict):
        raise RuntimeError("asr_batch_payload_invalid")
    if "structuredContent" in payload or "content" in payload:
        raise RuntimeError("asr_batch_result_not_normalized")
    return payload


def persist_asr_batch_result(
    *, workspace_root: Path, report_path: Path, run_id: str,
    envelope: object, result_path: Path | None = None,
) -> Dict[str, object]:
    """Normalize one MCP response and atomically persist the fixed batch result.

    The MCP envelope is deliberately consumed here, at the host boundary. The
    commit step only reads the resulting business JSON and never asks a model to
    copy fields out of ``content``/``structuredContent``.
    """
    input_path, fixed_result_path, _ = _asr_batch_paths(report_path)
    target = require_exact_path(result_path, fixed_result_path, "ASR批次结果") if result_path is not None else fixed_result_path.resolve()
    if not input_path.is_file():
        raise RuntimeError("asr_batch_input_missing")
    try:
        expected = json.loads(input_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise RuntimeError("asr_batch_input_invalid") from exc
    if not isinstance(expected, dict):
        raise RuntimeError("asr_batch_input_invalid")

    candidate: object = envelope
    if isinstance(envelope, dict) and isinstance(envelope.get("structuredContent"), dict):
        candidate = envelope["structuredContent"]
    elif isinstance(envelope, dict) and isinstance(envelope.get("content"), list):
        texts = [item.get("text") for item in envelope["content"] if isinstance(item, dict) and isinstance(item.get("text"), str)]
        if len(texts) != 1:
            raise RuntimeError("asr_batch_envelope_not_normalized")
        try:
            candidate = json.loads(texts[0])
        except json.JSONDecodeError as exc:
            raise RuntimeError("asr_batch_envelope_not_normalized") from exc
    if not isinstance(candidate, dict):
        raise RuntimeError("asr_batch_result_invalid")
    if "structuredContent" in candidate or "content" in candidate:
        raise RuntimeError("asr_batch_result_not_normalized")
    if str(candidate.get("run_id") or "").strip() != str(run_id or "").strip():
        raise RuntimeError("asr_batch_run_id_mismatch")
    expected_items = expected.get("items") if isinstance(expected.get("items"), list) else []
    result_items = candidate.get("items")
    if not isinstance(result_items, list):
        raise RuntimeError("asr_batch_items_mismatch")
    expected_by_id = {str(item.get("content_id") or "").strip(): item for item in expected_items if isinstance(item, dict)}
    result_by_id = {str(item.get("content_id") or "").strip(): item for item in result_items if isinstance(item, dict)}
    if len(result_by_id) != len(result_items) or set(result_by_id) != set(expected_by_id):
        raise RuntimeError("asr_batch_items_mismatch")
    for content_id, item in result_by_id.items():
        expected_item = expected_by_id[content_id]
        expected_hash = str(expected_item.get("url_hash") or "").strip().lower()
        if str(item.get("url_hash") or "").strip().lower() != expected_hash:
            raise RuntimeError("asr_batch_result_url_hash_mismatch")
        status = str(item.get("status") or "").strip().lower()
        if status not in {"success", "failed"}:
            raise RuntimeError("asr_batch_item_status_invalid")
        if status == "success" and not str(item.get("text") or item.get("transcript") or "").strip():
            # Empty text is retained as a failed business result, never treated
            # as a successful transcript.
            item["status"] = "failed"
            item.setdefault("error_code", "asr_empty_text")
    normalized = dict(candidate)
    normalized["run_id"] = str(run_id).strip()
    if expected.get("flow_type") and "flow_type" not in normalized:
        normalized["flow_type"] = expected["flow_type"]
    if expected.get("task_id") and "task_id" not in normalized:
        normalized["task_id"] = expected["task_id"]
    if expected.get("visibility_scope") and "visibility_scope" not in normalized:
        normalized["visibility_scope"] = expected["visibility_scope"]
    target.parent.mkdir(parents=True, exist_ok=True)
    serialized = json.dumps(normalized, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    if target.is_file():
        try:
            existing = json.loads(target.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise RuntimeError("asr_batch_result_invalid") from exc
        if existing == normalized:
            return {"status": "idempotent", "run_id": run_id, "result_path": str(target)}
        raise RuntimeError("asr_batch_result_already_written")
    write_bytes_atomically(target, serialized.encode("utf-8"))
    return {"status": "written", "run_id": run_id, "result_path": str(target)}


def resolve_readable_file(workspace_root: Path, value: object) -> Path | None:
    raw_text = str(value or "").strip()
    if not raw_text:
        return None
    raw = Path(raw_text).expanduser()
    target = (raw if raw.is_absolute() else workspace_root / raw).resolve()
    try:
        target.relative_to(workspace_root)
    except ValueError:
        return None
    try:
        return target if target.is_file() and target.stat().st_size > 0 else None
    except OSError:
        return None


def first_value(*rows: Dict[str, str], fields: tuple[str, ...]) -> str:
    for row in rows:
        for field in fields:
            value = str(row.get(field) or "").strip()
            if value:
                return value
    return ""


def infer_workspace_root(content_library_path: Path) -> Path:
    for parent in content_library_path.resolve().parents:
        if parent.name in {"我方资料", "对标资料"}:
            return parent.parent
    return content_library_path.resolve().parent


def resolve_scheduled_identity(
    workspace_root: Path,
    *,
    flow_type: str,
    task_id: str,
    run_id: str,
    account_id: str,
) -> tuple[str, str, str]:
    """Validate explicitly supplied run-scoped identity; never infer flow by scanning directories."""
    flow = str(flow_type or "").strip()
    task = str(task_id or "").strip()
    run = str(run_id or "").strip()
    if not run:
        raise RuntimeError("详细数据后续处理必须显式提供 run_id")
    if task:
        raise RuntimeError("详细数据后续处理只接受 run_id，不得推断 task_id")
    if flow == "self_account":
        return flow, "", run
    if flow == "benchmark_account":
        return flow, "", run
    return "benchmark_account", "", run


def _detect_locked_flow(root: Path, run_id: str) -> str:
    """Return the flow owning ``run_id`` from its locked context, else "".

    A run namespace is single-flow, so an existing ``批次上下文.json`` under
    either flow root uniquely identifies the batch.  This lets public actions
    stay run-scoped without exposing the internal ``--flow-type`` switch.
    """
    for flow, flow_root in (
        ("self_account", "self-account-diagnose"),
        ("benchmark_account", "benchmark-account-diagnose"),
    ):
        context_path = root / ".meiling/staging" / flow_root / run_id / "detail-data" / "批次上下文.json"
        if not context_path.is_file() or context_path.is_symlink():
            continue
        try:
            loaded = json.loads(context_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError):
            continue
        if isinstance(loaded, dict) and str(loaded.get("run_id") or "").strip() == run_id:
            return flow
    return ""


def resolve_batch_context(workspace_root: Path, run_id: str, *, task_id: str = "", flow_type: str = "") -> dict[str, Any]:
    """Resolve the locked batch context for a public run_id-only action.

    Both flows are local-MVP/run-scoped: their context is stored beside the
    detail-data artifacts, and neither flow depends on a scheduler state tree.
    """
    root = workspace_root.resolve()
    run = str(run_id or "").strip()
    if not run:
        raise RuntimeError("run_id_missing")
    if str(task_id or "").strip():
        raise RuntimeError("详细数据后续处理只接受 run_id，不得传入 task_id")
    flow = str(flow_type or "").strip()
    if not flow:
        flow = _detect_locked_flow(root, run) or "benchmark_account"
    if flow not in {"benchmark_account", "self_account"}:
        raise RuntimeError("run_id_context_flow_type_invalid")
    is_self = flow == "self_account"
    flow_root = "self-account-diagnose" if is_self else "benchmark-account-diagnose"
    state: dict[str, Any] = {}
    local_context_path = root / ".meiling/staging" / flow_root / run / "detail-data" / "批次上下文.json"
    if not local_context_path.is_file() or local_context_path.is_symlink():
        raise RuntimeError("run_id_context_missing")
    try:
        loaded = json.loads(local_context_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise RuntimeError("run_id_context_invalid") from exc
    if not isinstance(loaded, dict) or str(loaded.get("run_id") or "").strip() != run:
        raise RuntimeError("run_id_context_run_id_mismatch")
    if str(loaded.get("flow_type") or "").strip() != flow:
        raise RuntimeError("run_id_context_flow_type_mismatch")
    state = loaded
    account_id = str(state.get("account_id") or state.get("active_account_id") or state.get("account", {}).get("account_id") or "").strip()
    account = state.get("account") if isinstance(state.get("account"), dict) else {}
    if not account_id:
        account_id = str(account.get("account_id") or account.get("账号ID") or "").strip()
    if not account_id:
        raise RuntimeError("run_id_account_context_missing")
    catalog = root / ("我方资料/我方账号库.csv" if is_self else "对标资料/对标账号库.csv")
    fields, rows = read_csv_table(catalog)
    row = next((item for item in rows if str(item.get("账号ID") or item.get("account_id") or "").strip() == account_id), None)
    if not row:
        raise RuntimeError("run_id_catalog_context_missing")
    content_list = resolve_readable_file(root, state.get("content_list_path") or row.get("内容列表路径"))
    library = resolve_readable_file(root, state.get("content_library_path") or row.get("内容库路径")) or content_list
    if content_list is None:
        raise RuntimeError("run_id_content_list_missing")
    context_path = root / ".meiling/staging" / flow_root / run / "detail-data" / "批次上下文.json"
    context: dict[str, Any] = {}
    if context_path.is_file():
        loaded = json.loads(context_path.read_text(encoding="utf-8"))
        if isinstance(loaded, dict):
            context = loaded
    # The base-data handoff is the only fallback for a newly-created detail
    # batch.  Once a context exists, its locked source remains authoritative.
    data_source = str(context.get("data_source") or "").strip()
    if not data_source:
        state_handoff = state.get("handoff") if isinstance(state.get("handoff"), dict) else {}
        account_handoff = account.get("handoff") if isinstance(account.get("handoff"), dict) else {}
        data_source = str(
            account_handoff.get("data_source")
            or state_handoff.get("data_source")
            or ""
        ).strip()
    if not data_source:
        catalog_source = str(row.get("基础数据获取方式") or row.get("data_source") or "").strip()
        candidates = [
            content_list.parent / "过程记录/批处理报告/本轮账号基础建档结果.json",
            content_list.parent / "过程记录/批处理报告/账号基础数据获取结果.json",
            content_list.parent / "过程记录/批处理报告/本轮账号基础数据获取结果.json",
            content_list.parent / "过程记录/批处理报告/基础数据获取结果.json",
        ]
        for candidate in candidates:
            if not candidate.is_file():
                continue
            try:
                report = json.loads(candidate.read_text(encoding="utf-8"))
            except (OSError, UnicodeError, json.JSONDecodeError, DetailDataContractError):
                continue
            if isinstance(report, dict):
                data_source = str(report.get("data_source") or "").strip()
            if data_source:
                break
        data_source = data_source or catalog_source
    selected = context.get("selected_content_ids") if isinstance(context.get("selected_content_ids"), list) else []
    scope = {
        "scope_mode": str(context.get("scope_mode") or "").strip(),
        "scope_count": context.get("scope_count"),
        **({"visibility_scope": str(context.get("visibility_scope") or "public").strip().casefold()} if is_self else {}),
        "selected_content_ids": list(dict.fromkeys(str(item).strip() for item in selected if str(item).strip())),
        "available_count": context.get("available_count"),
        "selected_count": context.get("selected_count"),
        "confirmed": context.get("confirmed") is True,
        "data_source": data_source,
    }
    return {
        "flow_type": "self_account" if is_self else "benchmark_account",
        "task_id": str(state.get("task_id") or task_id or "").strip(),
        "run_id": run,
        "account_id": account_id,
        "content_list": content_list,
        "content_library": library,
        "content_ids": list(dict.fromkeys(str(item).strip() for item in selected if str(item).strip())),
        "data_source": data_source,
        "scope": scope,
        "context_path": context_path,
    }


def write_detail_delivery_state(
    *,
    workspace_root: Path,
    content_list_path: Path,
    report_path: Path,
    summary: Dict[str, object],
) -> Path:
    root = workspace_root.resolve()
    content_list = content_list_path.resolve()
    report = report_path.resolve()
    try:
        report_relative = report.relative_to(root).as_posix()
        content_list.relative_to(root)
    except ValueError as exc:
        raise RuntimeError("详细数据状态路径必须位于当前工作区内") from exc
    try:
        report_payload = canonical_store.load_report(report, validate=False)
    except (OSError, UnicodeError, json.JSONDecodeError, DetailDataContractError) as exc:
        raise RuntimeError(f"canonical_report_invalid:{exc}") from exc
    selected_ids = [
        str(value).strip()
        for value in (report_payload.get("requested_content_ids") or [])
        if str(value).strip()
    ]
    _, content_rows = read_csv_table(content_list)
    account_id = str(report_payload.get("account_id") or summary.get("account_id") or "").strip()
    account_row = next(
        (
            row
            for row in content_rows
            if str(row.get("账号ID") or "").strip() == account_id
            and (not selected_ids or str(row.get("内容ID") or "").strip() in selected_ids)
        ),
        {},
    )
    status = str(report_payload.get("status") or "").strip()
    followup = report_payload.get("followup_delivery") if isinstance(report_payload.get("followup_delivery"), dict) else {}
    followup_ready = str(followup.get("status") or "").strip() == "completed" and int(followup.get("missing_count") or 0) == 0
    recovery_status = "ready_for_commit" if status in {"completed", "partial"} and followup_ready else "in_progress"
    task_id = str(report_payload.get("task_id") or summary.get("task_id") or "").strip()
    run_id = str(report_payload.get("run_id") or summary.get("run_id") or "").strip()
    flow_type = str(report_payload.get("flow_type") or summary.get("flow_type") or "").strip()
    detail_report_relative = report_relative
    if flow_type == "benchmark_account":
        # 对标账号只使用既有批次上下文和 canonical report；此辅助逻辑
        # 不得再创建第二份账号本地交接或阶段状态来源。
        return report
    task_paths: Dict[str, object] | None = None
    if not run_id:
        raise RuntimeError("我方详细数据报告必须包含 run_id")
    task_paths = build_task_stage_paths(root, task_id, run_id, content_list, account_id, flow_type)
    confirmed_detail = task_paths.get("detail_report_json")
    if not isinstance(confirmed_detail, Path):
        raise RuntimeError(str(task_paths.get("detail_report_error") or "当前阶段没有已确认最终明细报告"))
    detail_report_relative = confirmed_detail.relative_to(root).as_posix()
    # 只对我方链路生效：上方 `flow_type == "benchmark_account"` 分支已提前 return，
    # 对标账号的阶段状态不由本函数写入（对标阶段名是 `账号详细数据获取`，
    # 由 账号内容明细补全批处理.py 的 detail_stage_name() 按链路给出）。
    state = build_account_stage_state(
        stage="我方账号详细数据获取",
        complete=recovery_status == "ready_for_commit",
        platform=str(account_row.get("平台") or ""),
        account_id=account_id,
        account_name=str(account_row.get("账号名") or ""),
        waiting_for_user="",
        next_flows=(),
        recovery_context={
            "status": recovery_status,
            "current_entry": "detail_data",
            "next_action": "",
            "report_path": detail_report_relative,
            "selected_content_ids": selected_ids,
        },
    )
    if run_id:
        state["run_id"] = run_id
    run_id = str(summary.get("run_id") or "").strip()
    if run_id:
        state["run_id"] = run_id
    original_scope_ids = selected_ids
    scope_json_value = str(summary.get("scope_json") or "").strip()
    scope_json_relative = ""
    if scope_json_value:
        scope_json_path = Path(scope_json_value).expanduser().resolve()
        try:
            scope_json_relative = scope_json_path.relative_to(root).as_posix()
        except ValueError as exc:
            raise RuntimeError("内容范围路径必须位于当前工作区内") from exc
    state["scope"] = {
        "scope_mode": str(report_payload.get("scope_mode") or "").strip(),
        "scope_count": report_payload.get("scope_count"),
        "selected_content_ids": original_scope_ids,
        "selected_count": len(original_scope_ids),
        "scope_json": scope_json_relative,
        "confirmed": True,
    }
    state_path = content_list.parent / "过程记录/当前阶段状态.json"
    write_state_atomically(state_path, state)
    return state_path


def _build_detail_delivery_summary(
    *,
    account_id: str,
    requested_ids: List[str],
    workspace_root: Path,
    content_list_path: Path,
    content_library_path: Path,
    summary: Dict[str, object],
) -> Dict[str, object]:
    _, rows = read_csv_table(content_list_path)
    _, library_rows = read_csv_table(content_library_path)
    account_rows = [
        row
        for row in rows
        if str(row.get("账号ID") or "").strip() == account_id.strip()
    ]
    selected = {
        str(row.get("内容ID") or "").strip(): row
        for row in rows
        if str(row.get("内容ID") or "").strip() in requested_ids
    }
    if set(selected) != set(requested_ids):
        raise RuntimeError("正式内容列表缺少本轮已确认内容")
    platforms = {str(row.get("平台") or "").strip() for row in selected.values()}
    account_ids = {str(row.get("账号ID") or "").strip() for row in selected.values()}
    if len(platforms) != 1 or "" in platforms or account_ids != {account_id.strip()}:
        raise RuntimeError("正式内容列表中的平台或账号身份与本轮不一致")

    library_by_id = {
        str(row.get("内容ID") or "").strip(): row
        for row in library_rows
        if str(row.get("内容ID") or "").strip() in requested_ids
    }
    evidence_items: List[Dict[str, object]] = []
    structured_errors = summary.get("errors") if isinstance(summary.get("errors"), list) else []
    errors_by_content: Dict[str, List[Dict[str, object]]] = {}
    for error in structured_errors:
        if isinstance(error, dict):
            errors_by_content.setdefault(str(error.get("content_id") or "").strip(), []).append(error)
    for content_id in requested_ids:
        content_row = selected[content_id]
        library_row = library_by_id.get(content_id, {})
        cover_path = resolve_readable_file(
            workspace_root,
            first_value(content_row, library_row, fields=("封面图路径",)),
        )
        transcript_path = resolve_readable_file(
            workspace_root,
            first_value(content_row, library_row, fields=("视频文案路径",)),
        )
        title = first_value(content_row, library_row, fields=("标题",))
        page_url = first_value(
            content_row,
            library_row,
            fields=("对外页面链接", "内容链接", "笔记链接"),
        )
        media_type = first_value(content_row, library_row, fields=("媒介形式",))
        is_video = media_type == "视频"
        performance_available = any(
            first_value(content_row, library_row, fields=(field,))
            for field in ("点赞数", "收藏数", "分享数", "评论数", "核心互动数", "发布时间")
        )
        cover_reference = first_value(content_row, library_row, fields=("封面图原链接", "封面图路径"))
        content_identity = bool(content_id and (page_url or cover_path or cover_reference))
        usable = content_identity
        item_errors = errors_by_content.get(content_id, [])
        if media_type not in {"视频", "图文"}:
            item_errors = [*item_errors, structured_error("media_type_unknown", "媒介形式未知、为空或不受支持")]
        if not content_identity:
            item_errors = [*item_errors, structured_error("content_evidence_missing", "缺少页面或封面证据")]
        blocking_errors = [item for item in item_errors if item.get("blocking") is True]
        missing_deliverables = []
        if media_type not in {"视频", "图文"}:
            missing_deliverables.append("媒介形式")
        if not cover_path:
            missing_deliverables.append("封面")
        if is_video and not transcript_path:
            missing_deliverables.append("视频文案")
        evidence_items.append(
            {
                "content_id": content_id,
                "title": title,
                "media_type": media_type or "未知",
                "base_data_available": bool(usable),
                "missing_deliverables": missing_deliverables,
                "cover_available": bool(cover_path),
                "transcript_available": bool(transcript_path),
                "title_available": bool(title),
                "page_available": bool(page_url),
                "performance_available": performance_available,
                "errors": item_errors,
                "blocking_errors": blocking_errors,
                "content_identity": content_identity,
            }
        )

    facts = [
        {
            "content_id": item["content_id"],
            "base_data_available": bool(item["base_data_available"]),
            "missing_deliverables": list(item["missing_deliverables"]),
            "blocking_errors": list(item["blocking_errors"]),
        }
        for item in evidence_items
    ]
    finalized = finalize_item_facts(facts, requested_content_ids=requested_ids)
    completed_item_count = finalized["counts"]["completed"]
    failed_item_count = finalized["counts"]["failed"]
    missing_deliverables = [
        {"content_id": item["content_id"], "items": item["missing_deliverables"]}
        for item in evidence_items
        if item["missing_deliverables"]
    ]
    completed_deliverables = []
    for item in evidence_items:
        completed_items = []
        if item["cover_available"]:
            completed_items.append("封面")
        if item["media_type"] == "视频" and item["transcript_available"]:
            completed_items.append("视频文案")
        if completed_items:
            completed_deliverables.append(
                {"content_id": item["content_id"], "items": completed_items}
            )

    consistency = summary.get("consistency_check")
    consistency_ready = (
        isinstance(consistency, dict) and consistency.get("status") == "通过"
    ) or summary.get("status") in {"completed", "partial"}
    processed_count = int(summary.get("processed_count") or 0)
    ready = (
        consistency_ready
        and processed_count == len(requested_ids)
        and completed_item_count == len(requested_ids)
        and not summary.get("failed_items")
    )
    # The batch status is derived from the same immutable facts that are
    # persisted in canonical detail-data reports.  Readiness additionally
    # accounts for pending AI summaries and collection processing.
    delivery_status = finalized["status"]
    if ready and delivery_status == "completed":
        delivery_status = "completed"
    elif delivery_status == "completed":
        delivery_status = "partial"
    return {
        "status": delivery_status,
        "batch_complete": ready and finalized["status"] == "completed",
        "account": {
            "account_id": account_id.strip(),
            "platform": next(iter(platforms)),
        },
        "selected_content_ids": requested_ids,
        "content_list_path": str(content_list_path),
        "content_library_path": str(content_library_path),
        "formal_files": {
            "content_list": {
                "path": str(content_list_path),
                "sha256": file_sha256(content_list_path),
            },
            "content_library": {
                "path": str(content_library_path),
                "sha256": file_sha256(content_library_path),
            },
        },
        "failed_count": len(summary.get("failed_items") or []),
        "counts": dict(finalized["counts"]),
        "detail_collection": {
            "items": finalized["items"],
            "collected_count": sum(item["base_data_available"] for item in finalized["items"]),
        },
        "evidence_items": evidence_items,
        "completed_deliverables": completed_deliverables,
        "missing_deliverables": missing_deliverables,
    }


def build_diagnosis_handoff(*, account_id: str, requested_ids: List[str], workspace_root: Path,
                            content_list_path: Path, content_library_path: Path,
                            summary: Dict[str, object]) -> Dict[str, object]:
    """Compatibility-only evidence projection; never used for formal handoff.

    New code must consume ``canonical-detail-report.json``.  This wrapper is
    retained for older local tooling while keeping the retired name out of the
    canonical report and current stage state.
    """
    return _build_detail_delivery_summary(
        account_id=account_id,
        requested_ids=requested_ids,
        workspace_root=workspace_root,
        content_list_path=content_list_path,
        content_library_path=content_library_path,
        summary=summary,
    )


def load_collected_item_results(
    *,
    content_list_path: Path,
    requested_ids: List[str],
    account_id: str,
    run_id: str,
) -> List[Dict[str, object]]:
    _, rows = read_csv_table(content_list_path)
    row_index = {
        str(row.get("内容ID") or "").strip(): row
        for row in rows
        if str(row.get("内容ID") or "").strip()
    }
    item_results: List[Dict[str, object]] = []
    for content_id in requested_ids:
        row = row_index.get(content_id, {})
        title = str(row.get("标题") or "").strip()
        result_path = build_content_process_dir(content_list_path, content_id, title) / "执行结果.json"
        failure = ""
        try:
            item_result = json.loads(result_path.read_text(encoding="utf-8"))
            if not isinstance(item_result, dict):
                raise ValueError("单条结果不是 JSON 对象")
            if str(item_result.get("run_id") or "").strip() != run_id:
                raise ValueError("单条结果不属于本轮处理")
            if str(item_result.get("content_id") or "").strip() != content_id:
                raise ValueError("单条结果内容 ID 不一致")
            if str(item_result.get("account_id") or "").strip() != account_id.strip():
                raise ValueError("单条结果账号 ID 不一致")
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            failure = str(exc)
            item_result = {
                "run_id": run_id,
                "account_id": account_id,
                "content_id": content_id,
                "title": title,
                "status": "失败",
                "content_dir": str(row.get("内容目录路径") or ""),
                "steps": {},
                "failures": [f"未取得本轮单条执行结果: {failure}"],
                "generated_files": [],
            }
        item_results.append(item_result)
    return item_results


def finalize_item_results(
    *,
    account_id: str,
    requested_ids: List[str],
    item_results: List[Dict[str, object]],
    workspace_root: Path,
    content_list_path: Path,
    content_library_path: Path,
    report_path: Path,
    run_mode: str,
    run_id: str = "",
    flow_type: str = "",
    task_id: str = "",
    scope: Dict[str, Any] | None = None,
) -> Dict[str, object]:
    # 唯一正式提交点：把单条 JSON 中的行更新一次性合并到两份 CSV。
    # 快照必须和行一起读，写盘时用它证明这两张表从读入到现在没被别人动过。
    content_fields, content_rows, content_snapshot = read_csv_table_with_snapshot(content_list_path)
    library_fields, library_rows, library_snapshot = read_csv_table_with_snapshot(content_library_path)
    content_by_id = {str(row.get("内容ID") or "").strip(): row for row in content_rows}
    library_by_id = {str(row.get("内容ID") or "").strip(): row for row in library_rows}
    for item in item_results:
        if not isinstance(item, dict):
            continue
        content_id = str(item.get("content_id") or "").strip()
        updates = item.get("row_updates")
        if not content_id or not isinstance(updates, dict):
            continue
        target = content_by_id.get(content_id)
        if target is not None:
            for field, value in updates.items():
                target[str(field)] = str(value or "")
        library_target = library_by_id.get(content_id)
        if library_target is not None:
            for field, value in updates.items():
                if field in library_target or field in {"内容目录路径", "封面图路径", "视频文案路径", "备注"}:
                    library_target[str(field)] = str(value or "")
    update_fields = {
        str(field)
        for item in item_results
        if isinstance(item, dict) and isinstance(item.get("row_updates"), dict)
        for field in item["row_updates"]
    }
    for field in sorted(update_fields):
        if field not in content_fields:
            content_fields.append(field)
        if field not in library_fields:
            library_fields.append(field)
    if content_rows:
        tables = [(content_list_path, content_fields, content_rows)]
        expected_originals = {content_list_path: content_snapshot}
        if content_library_path.resolve() != content_list_path.resolve():
            tables.append((content_library_path, library_fields, library_rows))
            expected_originals[content_library_path] = library_snapshot
        write_csv_tables_atomically(tables, expected_originals=expected_originals)
    summary = summarize_items(
        account_id=account_id,
        report_path=report_path,
        run_mode=run_mode,
        requested_count=len(requested_ids),
        items=item_results,
        content_list_path=content_list_path,
        content_library_path=content_library_path,
    )
    summary["flow_type"] = str(flow_type or "").strip()
    summary["task_id"] = str(task_id or "").strip()
    summary["run_id"] = str(run_id or "").strip()
    if isinstance(scope, dict):
        summary["scope_mode"] = str(scope.get("scope_mode") or "").strip()
        summary["scope_count"] = scope.get("scope_count")
        summary["scope_json"] = str(scope.get("scope_json") or "").strip()
        summary["scope_selected_content_ids"] = list(scope.get("selected_content_ids") or [])
    detail_delivery = _build_detail_delivery_summary(
        account_id=account_id,
        requested_ids=requested_ids,
        workspace_root=workspace_root,
        content_list_path=content_list_path,
        content_library_path=content_library_path,
        summary=summary,
    )
    summary["status"] = detail_delivery.get("status", "failed")
    summary["missing_deliverables"] = detail_delivery.get("missing_deliverables", [])
    retry_ids = list(dict.fromkeys(
        str(item.get("content_id") or "").strip()
        for item in item_results
        if isinstance(item, dict) and item.get("retry_required") is True
    ))
    summary["retry_content_ids"] = retry_ids
    summary["detail_collection"] = {
        "requested_content_ids": list(requested_ids),
        "item_checkpoint_count": len(item_results),
        "retry_content_ids": retry_ids,
    }
    summary["followup_delivery"] = {
        "status": summary["status"],
        "completed_deliverables": detail_delivery.get("completed_deliverables", []),
        "missing_deliverables": summary["missing_deliverables"],
    }
    report_path.parent.mkdir(parents=True, exist_ok=True)
    # The canonical report is the only persisted formal handoff. Keep the
    # verbose internal summary in memory, but never write duplicate routing
    # fields to disk.
    facts = [
        {
            "content_id": str(item.get("content_id") or "").strip(),
            "base_data_available": bool(item.get("content_identity")),
            "missing_deliverables": list(item.get("missing_deliverables") or []),
            "blocking_errors": list(item.get("blocking_errors") or []),
        }
        for item in (detail_delivery.get("evidence_items") or [])
        if isinstance(item, dict) and str(item.get("content_id") or "").strip()
    ]
    finalized = finalize_item_facts(facts, requested_content_ids=requested_ids)
    report_flow = str(flow_type or "benchmark_account").strip()
    canonical = canonical_store.create_report(
        workspace_root=workspace_root,
        flow_type=report_flow,
        task_id=str(task_id or "").strip(),
        run_id=str(run_id or "").strip(),
        account_id=account_id,
        scope_mode=str(summary.get("scope_mode") or "").strip(),
        scope_count=summary.get("scope_count"),
        requested_content_ids=requested_ids,
        report_path=report_path,
        initial_fields={
            "status": finalized["status"],
            "counts": finalized["counts"],
            "processed_content_ids": [
                str(item.get("content_id") or "").strip()
                for item in item_results
                if isinstance(item, dict) and str(item.get("content_id") or "").strip()
            ],
            "detail_collection": {
                "items": finalized["items"],
                "collected_count": sum(item["base_data_available"] for item in finalized["items"]),
            },
            "followup_delivery": {
                "status": "completed",
                "items": [], "completed_count": 0,
                "missing_count": 0,
            },
            "retry_content_ids": retry_ids,
            # 顶层供人和执行者阅读。机器所需的逐条
            # 交付状态由 detail_collection.items 承载，不再把内部内容 ID 写在这里。
            "missing_deliverables": _missing_deliverable_summaries(
                detail_delivery.get("evidence_items") or []
            ),
            **({"visibility_scope": str((scope or {}).get("visibility_scope") or "public").strip().casefold()} if report_flow == "self_account" else {}),
            "artifacts": {"detail_data_report": report_path.relative_to(workspace_root.resolve()).as_posix()},
        },
    )
    write_detail_delivery_state(
        workspace_root=workspace_root,
        content_list_path=content_list_path,
        report_path=report_path,
        summary=summary,
    )
    return summary


def build_stdout_summary(summary: Dict[str, object], report_path: Path) -> Dict[str, object]:
    output = {
        "run_id": summary.get("run_id", ""),
        "account_id": summary["account_id"],
        "requested_count": summary["requested_count"],
        "processed_count": summary["processed_count"],
        "cover_success_count": summary["cover_success_count"],
        "transcript_success_count": summary["transcript_success_count"],
        "transcript_degraded_count": summary["transcript_degraded_count"],
        "followup_success_content_ids": summary.get("followup_success_content_ids", []),
        "refreshed_content_ids": summary.get("refreshed_content_ids", []),
        "failed_count": len(summary["failed_items"]),
        "status": summary.get("status", ""),
        "missing_deliverables": summary.get("missing_deliverables", []),
        "consistency_status": (summary.get("consistency_check") or {}).get("status", ""),
        "consistency_issue_count": (summary.get("consistency_check") or {}).get("issue_count", 0),
        "report_json": str(report_path),
    }
    task_id = str(summary.get("task_id") or "").strip()
    if task_id:
        output["task_id"] = task_id
    return output


def _ordered_content_ids(values: object) -> List[str]:
    if not isinstance(values, list):
        return []
    return list(dict.fromkeys(str(value).strip() for value in values if str(value).strip()))


def _deliverables_by_id(values: object) -> Dict[str, List[str]]:
    result: Dict[str, List[str]] = {}
    if not isinstance(values, list):
        return result
    for value in values:
        if not isinstance(value, dict):
            continue
        content_id = str(value.get("content_id") or "").strip()
        if not content_id:
            continue
        items = value.get("items")
        normalized = (
            list(dict.fromkeys(str(item).strip() for item in items if str(item).strip()))
            if isinstance(items, list)
            else []
        )
        result[content_id] = normalized
    return result


def _missing_deliverable_summaries(items: object) -> List[str]:
    """Render gaps as sentences readable without internal content IDs.

    The canonical report's top-level ``missing_deliverables`` is read by people
    and by the executing agent, so it carries a display title rather than a
    machine identifier that a reader cannot map back to a piece of content.
    """
    if not isinstance(items, list):
        return []
    summaries: List[str] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        labels = [
            str(label).strip()
            for label in (item.get("missing_deliverables") or [])
            if isinstance(label, str) and label.strip()
        ]
        if not labels:
            continue
        display_name = str(item.get("title") or "").strip() or str(item.get("content_id") or "").strip()
        if not display_name:
            continue
        summaries.append(f"「{display_name}」缺{'、'.join(labels)}")
    return summaries


def _missing_deliverables_projection(report: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Rebuild the machine-readable {content_id, items} projection from facts.

    The canonical top-level field is a human-readable sentence list, so stage
    results that need per-content delivery status derive it from the per-item
    facts instead of copying that top level.
    """
    collection = report.get("detail_collection")
    items = collection.get("items") if isinstance(collection, dict) else []
    projection: List[Dict[str, Any]] = []
    for item in items or []:
        if not isinstance(item, dict):
            continue
        content_id = str(item.get("content_id") or "").strip()
        labels = [
            str(label).strip()
            for label in (item.get("missing_deliverables") or [])
            if isinstance(label, str) and label.strip()
        ]
        if content_id and labels:
            projection.append({"content_id": content_id, "items": labels})
    return projection


def _removed_legacy_detail_stage_result_api() -> None:
    """The canonical report is the only detail-stage handoff."""
    raise RuntimeError("detail stage-result API removed; use canonical-detail-report.json")
    for field in (
        "requested_content_ids",
        "failed_items",
    ):
        if field not in detail_report or not isinstance(detail_report.get(field), list):
            raise RuntimeError(f"最终明细报告字段格式无效: {field}")
    requested_ids = _ordered_content_ids(detail_report.get("requested_content_ids"))
    if not requested_ids:
        raise RuntimeError("最终明细报告缺少原始内容范围")
    if int(detail_report.get("requested_count") or len(requested_ids)) != len(requested_ids):
        raise RuntimeError("最终明细报告的原始内容范围与数量不一致")

    scope_mode = str(scope.get("scope_mode") or "").strip()
    scope_ids = _ordered_content_ids(scope.get("selected_content_ids"))
    if (
        str(scope.get("account_id") or "").strip() != str(account_id or "").strip()
        or scope_mode not in {"latest", "list_order", "all", "custom"}
        or scope_ids != requested_ids
    ):
        raise RuntimeError("本轮范围准备结果与最终明细报告不一致")
    try:
        available_count = int(scope["available_count"])
        selected_count = int(scope["selected_count"])
    except (KeyError, TypeError, ValueError) as exc:
        raise RuntimeError("本轮范围准备结果数量无效") from exc
    if available_count < len(requested_ids) or selected_count != len(requested_ids):
        raise RuntimeError("本轮范围准备结果数量无效")
    scope_count_value = scope.get("scope_count")
    if scope_mode in {"latest", "list_order"}:
        if isinstance(scope_count_value, bool) or not isinstance(scope_count_value, int) or scope_count_value <= 0:
            raise RuntimeError("本轮范围准备结果数量无效")
        if len(requested_ids) > scope_count_value:
            raise RuntimeError("本轮范围准备结果数量无效")
    elif scope_count_value is not None:
        raise RuntimeError("本轮范围准备结果不应包含数量上限")
    if scope_mode == "all" and len(requested_ids) != available_count:
        raise RuntimeError("全部内容范围与可用内容数不一致")

    detail_account_id = str(detail_report.get("account_id") or "").strip()
    followup_account_id = str(followup_report.get("account_id") or "").strip()
    expected_account_id = str(account_id or "").strip()
    if (
        not expected_account_id
        or detail_account_id != expected_account_id
        or followup_account_id != expected_account_id
    ):
        raise RuntimeError("最终明细报告与后续处理报告的账号不一致")
    expected_task_id = str(task_id or "").strip()
    expected_run_id = str(run_id or "").strip()
    if expected_task_id and not expected_run_id:
        raise RuntimeError("我方任务必须同时提供 task_id 和 run_id")
    if expected_task_id:
        for label, payload in (
            ("范围准备结果", scope),
            ("最终明细报告", detail_report),
            ("后续处理报告", followup_report),
        ):
            if str(payload.get("task_id") or "").strip() != expected_task_id:
                raise RuntimeError(f"{label}不属于当前任务")
            if str(payload.get("run_id") or "").strip() != expected_run_id:
                raise RuntimeError(f"{label}不属于当前阶段运行")

    handoff_value = followup_report.get("diagnosis_handoff")
    if not isinstance(handoff_value, dict):
        raise RuntimeError("后续处理报告缺少有效 diagnosis_handoff")
    handoff = copy.deepcopy(handoff_value)
    handoff_account = handoff.get("account")
    if (
        not isinstance(handoff_account, dict)
        or str(handoff_account.get("account_id") or "").strip() != expected_account_id
    ):
        raise RuntimeError("后续处理交接账号与本轮账号不一致")
    selected_ids = _ordered_content_ids(handoff.get("selected_content_ids"))
    if not selected_ids or not set(selected_ids).issubset(set(requested_ids)):
        raise RuntimeError("后续处理交接范围不属于最终明细原始范围")
    # 后续范围由正式 CSV 当前内容决定，历史资格列表只作为兼容字段读取，
    # 不再参与范围门禁或与单条结果互相校验。

    status = str(handoff.get("status") or "").strip()
    if status not in {"completed", "partial", "failed"}:
        raise RuntimeError("后续处理交接状态无效")
    if status == "failed":
        raise RuntimeError("后续处理没有可用交付结果")
    for field in ("completed_deliverables", "missing_deliverables"):
        if field not in handoff or not isinstance(handoff.get(field), list):
            raise RuntimeError(f"后续处理交接字段格式无效: {field}")

    failed_items = detail_report.get("failed_items") or []
    if any(not isinstance(item, dict) for item in failed_items):
        raise RuntimeError("最终明细报告 failed_items 格式无效")
    failed_detail_ids = {
        str(item.get("content_id") or "").strip()
        for item in failed_items
        if isinstance(item, dict) and str(item.get("content_id") or "").strip()
    }
    detail_gap_ids = failed_detail_ids
    completed = _deliverables_by_id(handoff.get("completed_deliverables"))
    missing = _deliverables_by_id(handoff.get("missing_deliverables"))
    selected_set = set(selected_ids)
    for content_id in requested_ids:
        if content_id in detail_gap_ids:
            missing.setdefault(content_id, []).append("内容明细")
        if content_id not in selected_set:
            missing.setdefault(content_id, []).append("固定后续处理")
        if content_id not in completed and content_id not in missing:
            raise RuntimeError(f"后续处理交接缺少内容交付状态: {content_id}")
    for mapping in (completed, missing):
        for content_id, items in mapping.items():
            mapping[content_id] = list(dict.fromkeys(items))

    completed_stage = not detail_gap_ids and status == "completed" and selected_ids == requested_ids
    handoff["scope_mode"] = scope_mode
    handoff["scope_count"] = scope_count_value
    if not completed_stage:
        handoff.update(
            {
                "status": "partial",
                "batch_complete": False,
            }
        )

    return {
        "flow_type": str(flow_type or ""),
        "task_id": expected_task_id,
        "run_id": expected_run_id,
        "stage": "detail_data",
        "status": "completed" if completed_stage else "partial",
        "completed_deliverables": [
            {"content_id": content_id, "items": completed[content_id]}
            for content_id in requested_ids
            if content_id in completed
        ],
        "missing_deliverables": [
            {"content_id": content_id, "items": missing[content_id]}
            for content_id in requested_ids
            if content_id in missing
        ],
        "artifacts": {
            "detail_data_report": str(followup_report_path),
            "detail_data_source_report": str(detail_report_path),
            "detail_data_delivery_status": "completed" if completed_stage else "partial",
        },
        "handoff": handoff,
    }


def _removed_detail_stage_result_action(
    *,
    account_id: str,
    detail_report_path: Path,
    followup_report_path: Path,
    scope_path: Path,
    task_id: str = "",
    run_id: str = "",
    flow_type: str = "",
) -> Dict[str, Any]:
    try:
        detail_report = json.loads(detail_report_path.read_text(encoding="utf-8"))
        followup_report = json.loads(followup_report_path.read_text(encoding="utf-8"))
        scope = json.loads(scope_path.read_text(encoding="utf-8"))
        if not isinstance(detail_report, dict) or not isinstance(followup_report, dict) or not isinstance(scope, dict):
            raise RuntimeError("阶段结果输入报告必须是 JSON 对象")
        if task_id:
            identity = {
                "flow_type": flow_type,
                "task_id": task_id,
                "run_id": run_id,
                "account_id": account_id,
            }
            detail_report = bind_task_payload_identity(
                detail_report, label="最终明细报告", **identity
            )
            followup_report = bind_task_payload_identity(
                followup_report, label="后续处理报告", **identity
            )
            scope = bind_task_payload_identity(
                scope, label="范围准备结果", **identity
            )
        if detail_report.get("schema_version") == 1 and detail_report.get("stage") == "detail_data":
            # 这里只读取本地产物；canonical schema 校验由报告存储层和
            # 当前流程的交接校验负责。
            stage_result = {
                "flow_type": detail_report["flow_type"],
                "task_id": detail_report["task_id"],
                "run_id": detail_report["run_id"],
                "stage": "detail_data",
                "status": detail_report.get("status"),
                "artifacts": {**(detail_report.get("artifacts") or {}), "canonical_detail_report": str(detail_report_path)},
                "handoff": detail_report.get("followup_delivery") or {},
                "completed_deliverables": detail_report.get("processed_content_ids") or [],
                # 本字段是机器契约（{content_id, items}），而 canonical 报告顶层已改为
                # 人可读句子，因此从逐条事实重建，不从顶层搬运。
                "missing_deliverables": _missing_deliverables_projection(detail_report),
            }
        else:
            stage_result = build_detail_stage_result(
                account_id=account_id,
                detail_report=detail_report,
                followup_report=followup_report,
                detail_report_path=detail_report_path,
                followup_report_path=followup_report_path,
                scope=scope,
                task_id=task_id,
                run_id=run_id,
                flow_type=flow_type,
            )
    except (OSError, TypeError, ValueError, KeyError, RuntimeError, json.JSONDecodeError) as exc:
        # A stage-result file is a business object, never a transport receipt.
        raise RuntimeError(str(exc)) from exc
    return stage_result


def infer_base_boundary_for_legacy_call(content_list_path: Path) -> str:
    """继承基础阶段已确认的结论；找不到证据时才返回未知。"""
    candidates = [content_list_path.parent / "过程记录/当前阶段状态.json"]
    process_dir = content_list_path.parent / "过程记录"
    candidates.extend(
        process_dir / "批处理报告" / name
        for name in (
            "本轮账号基础建档结果.json",
            "账号基础数据获取结果.json",
            "本轮账号基础数据获取结果.json",
            "基础数据获取结果.json",
        )
    )
    candidates.extend(
        process_dir / name
        for name in ("账号基础数据获取结果.json", "本轮账号基础数据获取结果.json", "基础数据获取结果.json")
    )

    def from_payload(payload: object) -> str:
        if not isinstance(payload, dict):
            return "未知"
        markers = [
            payload.get("内容列表是否可能未抓全"),
        ]
        for marker in markers:
            if marker in ("否", False, 0):
                return "完整"
            if marker in ("是", True, 1):
                return "可能未抓全"
        status = str(payload.get("content_list_status") or payload.get("内容列表获取状态") or "").strip()
        limitations = payload.get("data_limitations")
        if status == "成功" and (not isinstance(limitations, list) or not limitations):
            return "完整"
        if status in {"部分成功", "失败"}:
            return "可能未抓全"
        return "未知"

    for candidate in candidates:
        try:
            if not candidate.is_file() or candidate.is_symlink():
                continue
            result = from_payload(json.loads(candidate.read_text(encoding="utf-8")))
            if result != "未知":
                return result
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            continue
    return "未知"


def load_failed_content_ids(report_path: Path) -> List[str]:
    if not report_path.exists():
        raise RuntimeError(f"failed-only 模式未找到报告文件: {report_path}")
    data = json.loads(report_path.read_text(encoding="utf-8"))
    failed_ids = retryable_content_ids(data)
    if not failed_ids:
        raise RuntimeError(f"failed-only 模式下报告中没有失败内容: {report_path}")
    return failed_ids


def resolve_requested_ids(content_ids_arg: str, *, failed_only: bool, report_path: Path) -> List[str]:
    explicit_ids = list(dict.fromkeys(item.strip() for item in content_ids_arg.split(",") if item.strip()))
    if not failed_only:
        return explicit_ids
    failed_ids = load_failed_content_ids(report_path)
    if not explicit_ids:
        return failed_ids
    explicit_set = set(explicit_ids)
    return [content_id for content_id in failed_ids if content_id in explicit_set]


def validate_requested_account(content_list_path: Path, requested_ids: List[str], account_id: str) -> None:
    _, rows = read_csv_table(content_list_path)
    index = {
        (row.get("内容ID") or "").strip(): row
        for row in rows
        if (row.get("内容ID") or "").strip()
    }
    expected = account_id.strip()
    if not expected:
        raise RuntimeError("账号ID不能为空")
    mismatches = []
    for content_id in requested_ids:
        row = index.get(content_id)
        if row is None:
            continue
        actual = (row.get("账号ID") or "").strip()
        if actual != expected:
            mismatches.append(f"{content_id}: {actual or '缺失'}")
    if mismatches:
        raise RuntimeError(
            f"本轮账号ID与内容列表不一致，预期 {expected}，实际 " + "；".join(mismatches)
        )


def _asr_url_hash(video_url: str) -> str:
    return hashlib.sha256(str(video_url or "").strip().encode("utf-8")).hexdigest()


def _asr_batch_paths(report_path: Path) -> tuple[Path, Path, Path]:
    root = report_path.parent
    return root / "asr-batch-input.json", root / "asr-batch-result.json", root / "asr-batch-checkpoint.json"


def _batch_json_hash(payload: object) -> str:
    """Hash the canonical JSON representation used by the handoff files."""
    return hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _require_inside_workspace(path: Path, workspace: Path) -> None:
    try:
        path.relative_to(workspace)
    except ValueError as exc:
        raise RuntimeError("content_dir_outside_workspace") from exc


def _resolve_inside_workspace(value: str, workspace: Path) -> Path:
    path = Path(value).expanduser()
    if not path.is_absolute():
        path = workspace / path
    path = path.resolve()
    _require_inside_workspace(path, workspace)
    return path


def _resolve_content_dir_for_batch(
    workspace_root: Path,
    content_library_path: Path,
    row: Dict[str, str],
    library_row: Dict[str, str] | None = None,
    planned_dir_names: Dict[str, str] | None = None,
    dir_owners: Dict[str, str] | None = None,
) -> Path:
    """定位本条内容的内容目录。

    ``内容目录路径`` 语义上就是目录，直接采用；不再用 ``Path.suffix`` 反推
    「这是文件还是目录」——目录名里带点（例如账号名的 ``9.11``）会让
    ``suffix`` 非空、被误判成文件，于是取父目录，把内容目录塌缩到内容库
    根目录。只有文件类字段（封面 / 文案 / 总结）才取所在目录。
    """
    workspace = workspace_root.resolve()
    root = workspace / ("我方资料/我方内容" if "我方资料" in content_library_path.parts else "对标资料/对标内容")
    candidates = (row, library_row or {})

    content_id = str(row.get("内容ID") or "").strip()

    def _claimed_by_other(name: str) -> bool:
        """目录名已被别条内容占用：属于历史撞名数据，不能复用它。"""
        owner = (dir_owners or {}).get(name)
        return owner is not None and owner != content_id

    for candidate in candidates:
        value = str(candidate.get("内容目录路径") or "").strip()
        if not value:
            continue
        path = _resolve_inside_workspace(value, workspace)
        # 内容库根目录不是任何一条内容的内容目录。历史脏数据把它写进这个
        # 字段时直接忽略，继续往下解析，避免整批内容塌缩到根目录。
        if path == root:
            continue
        # 历史脏数据也可能把文件路径写进目录字段，此时退回它的所在目录。
        directory = path.parent if path.is_file() else path
        # 该目录已被别条内容占用，交给下方按本批次唯一名重新分配。
        if _claimed_by_other(directory.name):
            continue
        return directory

    for candidate in candidates:
        for field in ("封面图路径", "视频文案路径"):
            value = str(candidate.get(field) or "").strip()
            if not value:
                continue
            path = _resolve_inside_workspace(value, workspace)
            # 产物直接躺在内容库根目录同样是脏数据，不是逐条目录。
            if path.parent == root:
                continue
            if _claimed_by_other(path.parent.name):
                continue
            return path.parent

    title = str(row.get("标题") or row.get("内容标题") or "").strip()
    name = (planned_dir_names or {}).get(content_id) or build_content_dir_name(content_library_path, row, title)
    result = (root / name).resolve()
    _require_inside_workspace(result, workspace)
    return result


def _relative_workspace_path(path: Path, workspace_root: Path) -> str:
    try:
        return path.resolve().relative_to(workspace_root.resolve()).as_posix()
    except ValueError as exc:
        raise RuntimeError("formal_output_outside_workspace") from exc


def backfill_delivery_paths_from_disk(
    *,
    workspace_root: Path,
    content_list_path: Path,
    content_library_path: Path,
    requested_ids: List[str],
) -> List[str]:
    """补跑成功之后，产物已经在内容目录里，但两份 CSV 的路径列还是空的。

    以前只认 CSV：视频文案.md 写出来了，`视频文案路径` 还空着，于是下一轮
    明细补全 / canonical 报告仍判「缺视频文案」，特征落表按 `no_transcript`
    把这条跳过（2026-11-01 实测）。这里以磁盘实际文件为准回填路径，只补
    `内容目录路径 / 封面图路径 / 视频文案路径` 三列，不动任何内容字段。

    返回被回填的内容 ID；没有需要补的就返回空列表，不写文件。
    """
    if not requested_ids:
        return []
    workspace = workspace_root.resolve()
    content_fields, content_rows, content_snapshot = read_csv_table_with_snapshot(content_list_path)
    if content_library_path == content_list_path:
        library_fields, library_rows, library_snapshot = content_fields, content_rows, content_snapshot
    else:
        library_fields, library_rows, library_snapshot = read_csv_table_with_snapshot(content_library_path)
    content_by_id = {str(row.get("内容ID") or "").strip(): row for row in content_rows}
    library_by_id = {str(row.get("内容ID") or "").strip(): row for row in library_rows}
    content_root = workspace / ("我方资料/我方内容" if "我方资料" in content_list_path.parts else "对标资料/对标内容")
    patched: List[str] = []
    for content_id in requested_ids:
        content_id = str(content_id or "").strip()
        row = content_by_id.get(content_id)
        if row is None:
            continue
        content_dir: Path | None = None
        directory_value = str(row.get("内容目录路径") or "").strip()
        if directory_value:
            candidate = (workspace / directory_value).resolve()
            if candidate.is_relative_to(workspace) and candidate.is_dir():
                content_dir = candidate
        if content_dir is None and content_root.is_dir():
            # 目录名形如 `<内容ID>-<标题>`；历史数据缺列时按前缀找回来。
            for candidate in sorted(content_root.iterdir()):
                if candidate.is_dir() and candidate.name.startswith(f"{content_id}-"):
                    content_dir = candidate
                    break
        if content_dir is None:
            continue
        changed = False
        for field, filename in (("封面图路径", "封面图.png"), ("视频文案路径", "视频文案.md")):
            if str(row.get(field) or "").strip():
                continue
            target = content_dir / filename
            if not (target.is_file() and target.stat().st_size > 0):
                continue
            relative = _relative_workspace_path(target, workspace)
            row[field] = relative
            if content_id in library_by_id:
                library_by_id[content_id][field] = relative
            changed = True
        if not str(row.get("内容目录路径") or "").strip():
            relative_dir = _relative_workspace_path(content_dir, workspace)
            row["内容目录路径"] = relative_dir
            if content_id in library_by_id:
                library_by_id[content_id]["内容目录路径"] = relative_dir
        if changed:
            patched.append(content_id)
    if not patched:
        return []
    for field in ("内容目录路径", "封面图路径", "视频文案路径"):
        if field not in content_fields:
            content_fields.append(field)
        if field not in library_fields:
            library_fields.append(field)
    tables = [(content_list_path, content_fields, content_rows)]
    expected_originals = {content_list_path: content_snapshot}
    if content_library_path != content_list_path:
        tables.append((content_library_path, library_fields, library_rows))
        expected_originals[content_library_path] = library_snapshot
    write_csv_tables_atomically(tables, expected_originals=expected_originals)
    return patched


def _read_cover_failure_reason(receipt_path: Path, completed: "subprocess.CompletedProcess[str]") -> str:
    """取回封面失败的真正原因；拿不到回执时才退回命令输出。"""
    try:
        if receipt_path.is_file():
            payload = json.loads(receipt_path.read_text(encoding="utf-8"))
            if isinstance(payload, dict):
                reason = str(payload.get("失败原因") or payload.get("failure_reason") or "").strip()
                if reason:
                    return reason
    except (OSError, UnicodeError, json.JSONDecodeError):
        pass
    detail = str(completed.stderr or "").strip() or str(completed.stdout or "").strip()
    return detail[:300]


def _cover_command_for_batch(
    *, workspace_root: Path, args: argparse.Namespace, content_id: str, title: str,
    cover_url: str, output_path: Path, platform: str = "",
    result_output_json: Path | None = None,
) -> List[str]:
    template = str(getattr(args, "cover_command", "") or "").strip()
    variables = {
        "content_id": content_id,
        "title": title,
        "platform": platform,
        "cover_url": cover_url,
        "output_path": str(output_path),
        "content_dir": str(output_path.parent),
        "workspace_root": str(workspace_root),
        "result_output_json": str(result_output_json or ""),
    }
    if template:
        try:
            return [str(part) for part in template.format(**variables).split()]
        except (KeyError, ValueError) as exc:
            raise RuntimeError(f"cover_command_invalid:{exc}") from exc
    script = _EXECUTION_UNIT_DIR / "下载封面图执行器.py"
    command = [
        sys.executable, str(script), "--content-id", content_id, "--title", title,
        "--platform", platform,
        "--cover-url", cover_url, "--output-path", str(output_path),
    ]
    if result_output_json is not None:
        # 让执行器把真实失败原因落盘；否则下游只剩一个笼统的失败码，
        # 排查时只能靠猜。
        command.extend([
            "--result-output-json", str(result_output_json),
            "--workspace-root", str(workspace_root),
        ])
    return command


def run_cover_batch(
    *, workspace_root: Path, report_path: Path, account_id: str, requested_ids: List[str],
    content_list_path: Path, content_library_path: Path, args: argparse.Namespace,
) -> Dict[str, object]:
    """Download/reuse covers and commit only the cover-owned CSV fields."""
    cover_input = report_path.parent / "cover-batch-input.json"
    cover_result = report_path.parent / "cover-batch-result.json"
    cover_receipt_dir = report_path.parent / "cover-results"
    # 字段名、数据行和并发校验用的快照取自同一次读，避免三次读之间文件已变。
    content_fields, content_rows, content_snapshot = read_csv_table_with_snapshot(content_list_path)
    if content_library_path.resolve() == content_list_path.resolve():
        library_fields, library_rows, library_snapshot = [], content_rows, content_snapshot
    else:
        library_fields, library_rows, library_snapshot = read_csv_table_with_snapshot(content_library_path)
    by_id = {str(row.get("内容ID") or "").strip(): row for row in content_rows}
    library_by_id = {str(row.get("内容ID") or "").strip(): row for row in library_rows}
    results: List[Dict[str, object]] = []
    dir_owners = collect_content_dir_owners(
        [*content_rows, *library_rows],
        content_root=workspace_root / (
            "我方资料/我方内容" if "我方资料" in content_library_path.parts else "对标资料/对标内容"
        ),
        workspace_root=workspace_root,
    )
    planned_dir_names = plan_content_dir_names(
        (by_id.get(content_id) for content_id in requested_ids),
        content_library_path,
        existing_names=dir_owners,
    )
    for content_id in requested_ids:
        row = by_id.get(content_id)
        if row is None or str(row.get("账号ID") or "").strip() != account_id.strip():
            raise RuntimeError("cover_batch_content_identity_mismatch")
        library_row = library_by_id.get(content_id, {})
        title = str(row.get("标题") or row.get("内容标题") or content_id).strip()
        content_dir = _resolve_content_dir_for_batch(workspace_root, content_library_path, row, library_row, planned_dir_names, dir_owners)
        cover_path = content_dir / "封面图.png"
        existing = resolve_readable_file(workspace_root, row.get("封面图路径")) or resolve_readable_file(workspace_root, library_row.get("封面图路径"))
        failure_reason = ""
        if existing is not None:
            selected_path = existing
            status, reused, error_code = "reused", True, ""
        else:
            cover_url = str(row.get("封面图原链接") or library_row.get("封面图原链接") or "").strip()
            if not cover_url:
                selected_path, status, reused, error_code = cover_path, "failed", False, "cover_url_missing"
                failure_reason = "封面图原链接为空"
            else:
                receipt_path = cover_receipt_dir / f"{content_id}.json"
                try:
                    command = _cover_command_for_batch(
                        workspace_root=workspace_root, args=args, content_id=content_id,
                        title=title, cover_url=cover_url, output_path=cover_path,
                        platform=str(row.get("平台") or library_row.get("平台") or row.get("目标平台") or library_row.get("目标平台") or "").strip(),
                        result_output_json=receipt_path,
                    )
                    completed = subprocess.run(command, cwd=str(workspace_root), text=True, capture_output=True, check=False)
                    valid = completed.returncode == 0 and output_file_is_valid(cover_path)
                    selected_path, status, reused = cover_path, ("downloaded" if valid else "failed"), False
                    error_code = "" if valid else "cover_download_failed"
                    if not valid:
                        failure_reason = _read_cover_failure_reason(receipt_path, completed)
                except (OSError, RuntimeError) as exc:
                    selected_path, status, reused = cover_path, "failed", False
                    error_code = f"cover_download_failed:{exc}"
                    failure_reason = str(exc)
        if status in {"reused", "downloaded"} and output_file_is_valid(selected_path):
            relative = _relative_workspace_path(selected_path, workspace_root)
            row["封面图路径"] = relative
            if library_row:
                library_row["封面图路径"] = relative
        results.append({
            "content_id": content_id, "status": status, "cover_path": _relative_workspace_path(selected_path, workspace_root),
            "reused": reused, "error_code": error_code, "failure_reason": failure_reason,
        })
    cover_identity = {
        "schema_version": 1, "run_id": str(report_path.parent.parent.name), "account_id": account_id,
        "requested_content_ids": list(requested_ids),
    }
    cover_payload = {**cover_identity, "items": results}
    if cover_input.is_file():
        try:
            existing = json.loads(cover_input.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise RuntimeError("cover_batch_input_invalid") from exc
        # Only identity and scope are locked.  `items` carries per-run execution
        # results, and a retry reuses an already-downloaded cover (flipping
        # `reused`), so comparing it would lock the batch out of every retry.
        if not isinstance(existing, dict) or any(
            existing.get(key) != value for key, value in cover_identity.items()
        ):
            raise RuntimeError("cover_batch_input_already_locked")
    write_json_atomically(cover_input, cover_payload)
    with rollback_files_on_error([content_list_path, *( [] if content_library_path.resolve() == content_list_path.resolve() else [content_library_path]), cover_result]):
        tables = [(content_list_path, content_fields, content_rows)]
        expected_originals = {content_list_path: content_snapshot}
        if content_library_path.resolve() != content_list_path.resolve():
            tables.append((content_library_path, library_fields, library_rows))
            expected_originals[content_library_path] = library_snapshot
        write_csv_tables_atomically(tables, expected_originals=expected_originals)
        write_json_atomically(cover_result, {**cover_payload, "input_sha256": _batch_json_hash(cover_payload)})
    return {"input_path": cover_input, "result_path": cover_result, "payload": cover_payload, "results": results}


def build_asr_batch_input(
    *, workspace_root: Path, report_path: Path, run_id: str, account_id: str,
    requested_ids: List[str], content_list_path: Path, content_library_path: Path | None = None,
    flow_type: str = "benchmark_account", task_id: str = "",
    visibility_scope: str = "", selected_content_ids: List[str] | None = None,
    data_source: str = "",
    write_input: bool = True,
) -> Dict[str, object]:
    """Collect platform-caption results and the videos needing local ASR."""
    if not run_id:
        raise RuntimeError("asr_batch_run_id_missing")
    flow = str(flow_type or "benchmark_account").strip()
    task = str(task_id or "").strip()
    # The retired scheduler task id is not part of the self contract: the local
    # transcript path consumes nothing but `run_id`.  Same rule as the
    # detail-data stage contract.
    if flow == "benchmark_account" and task:
        raise RuntimeError("asr_batch_benchmark_task_id_forbidden")
    if flow not in {"benchmark_account", "self_account"}:
        raise RuntimeError("asr_batch_flow_type_invalid")
    scope = str(visibility_scope or "").strip().casefold()
    if flow == "self_account" and scope not in {"public", "private", "all"}:
        raise RuntimeError("asr_batch_visibility_scope_invalid")
    selected = [str(item).strip() for item in (selected_content_ids or requested_ids) if str(item).strip()]
    if selected != list(requested_ids):
        raise RuntimeError("asr_batch_scope_ids_mismatch")
    _, rows = read_csv_table(content_list_path)
    by_id = {str(row.get("内容ID") or "").strip(): row for row in rows}
    items: List[Dict[str, object]] = []
    local_results: List[Dict[str, object]] = []
    for content_id in requested_ids:
        row = by_id.get(content_id)
        if not row or str(row.get("账号ID") or "").strip() != account_id.strip():
            raise RuntimeError("asr_batch_content_identity_mismatch")
        if flow == "self_account" and scope in {"public", "private"}:
            expected_visibility = "公开" if scope == "public" else "私密"
            actual_visibility = normalize_public_status(row.get("公开状态"))
            if actual_visibility != expected_visibility:
                raise RuntimeError("asr_batch_visibility_scope_mismatch")
        media_type = str(row.get("媒介形式") or "").strip()
        video_url = str(row.get("视频下载链接") or row.get("下载笔记原链接") or "").strip()
        if str(row.get("平台") or "").strip() == "抖音":
            # 创作者中心 work_list 的直链（cold 域名）火山能拉取；公开页那组拉不到。
            # 先读 2.5 保存的首屏作品列表，再退回公开页详情响应。
            native_id = str(row.get("平台原生内容ID") or "").strip()
            staged_url = extract_creator_center_play_addr(
                report_path.parent.parent / "创作者中心数据" / "作品列表原始响应.json",
                native_id,
            )
            if not staged_url:
                for candidate_path in (
                    report_path.parent / "raw-details" / f"{content_id}-detail.network-response",
                    report_path.parent / "raw-details" / f"{content_id}.json",
                    report_path.parent / "raw-note-details" / f"{content_id}.json",
                ):
                    staged_url = extract_douyin_play_addr(candidate_path, native_id)
                    if staged_url:
                        break
            if staged_url:
                video_url = staged_url
        transcript_path = resolve_readable_file(workspace_root, row.get("视频文案路径"))
        if media_type != "视频":
            local_results.append({"content_id": content_id, "status": "skipped", "failure_reason": "非视频内容，跳过视频文案"})
            continue
        if transcript_path is not None:
            local_results.append({"content_id": content_id, "status": "skipped", "failure_reason": "已有有效视频文案", "transcript_path": _relative_workspace_path(transcript_path, workspace_root)})
            continue
        # Only the current detail-data staging area may provide platform
        # subtitle material.  Formal content JSON is deliberately excluded.
        raw_detail = report_path.parent / "raw-note-details" / f"{content_id}.json"
        raw_binding = report_path.parent / "raw-note-details" / f"{content_id}.binding.json"
        caption_evidence = report_path.parent / "platform-captions" / f"{content_id}.json"
        binding: dict[str, object] = {}
        binding_reason = "本轮暂存目录未提供平台原始详情绑定"
        if raw_binding.is_file():
            try:
                binding = json.loads(raw_binding.read_text(encoding="utf-8"))
                if not isinstance(binding, dict):
                    raise ValueError("binding_not_object")
                if str(binding.get("run_id") or "").strip() != run_id:
                    raise ValueError("binding_run_id_mismatch")
                if str(binding.get("content_id") or "").strip() != content_id:
                    raise ValueError("binding_content_id_mismatch")
                if str(binding.get("raw_detail_path") or "").strip() != _relative_workspace_path(raw_detail, workspace_root):
                    raise ValueError("binding_raw_detail_path_mismatch")
                raw_hash = str(binding.get("raw_detail_sha256") or "").strip().lower()
                if not raw_hash or not raw_detail.is_file() or hashlib.sha256(raw_detail.read_bytes()).hexdigest() != raw_hash:
                    raise ValueError("binding_raw_detail_sha256_mismatch")
                if not str(binding.get("native_content_id") or "").strip():
                    raise ValueError("binding_native_content_id_missing")
                binding_reason = ""
            except (OSError, UnicodeError, ValueError, json.JSONDecodeError) as exc:
                binding = {}
                binding_reason = f"平台原始详情绑定无效:{exc}"
        # API/JustOneAPI already returns the authoritative media URL and must
        # skip the Chrome-only platform-caption probe. Chrome keeps captions
        # as the first choice and falls back to this URL batch only on failure.
        api_source = str(data_source or "").strip().casefold() in {"api", "justoneapi", "justone_api"} or "justoneapi" in str(data_source or "").strip().casefold()
        platform_name = str(row.get("平台") or "").strip()
        if not api_source and platform_name == "小红书":
            caption = materialize_xiaohongshu_platform_caption(
                workspace_root=workspace_root,
                raw_detail_json=str(raw_detail) if binding else "",
                evidence_json=str(caption_evidence),
                content_id=content_id,
                run_id=run_id,
                video_url=video_url,
                native_content_id=str(binding.get("native_content_id") or ""),
                expected_raw_sha256=str(binding.get("raw_detail_sha256") or ""),
            )
            if not binding and not str(caption.get("failure_reason") or "").strip():
                caption["failure_reason"] = binding_reason
        elif not api_source and platform_name == "B站":
            # B站 的字幕不落在页面结构化状态里，单独取；取不到就如实回退到 ASR。
            caption = materialize_bilibili_platform_caption(
                workspace_root=workspace_root,
                content_id=content_id,
                run_id=run_id,
                video_url=video_url,
                note_url=str(row.get("对外页面链接") or row.get("平台原生内容ID") or ""),
                evidence_json=str(caption_evidence),
            )
        else:
            caption = {"status": "unavailable", "failure_reason": "本轮暂存目录未提供平台原始详情"}
        caption_fact = platform_caption_evidence(
            caption,
            content_id=content_id,
            run_id=run_id,
            video_url=video_url,
        )
        if caption_fact.get("status") == "success" and str(caption_fact.get("text") or "").strip():
            staged_caption = report_path.parent / "platform-captions" / f"{content_id}.md"
            staged_caption.parent.mkdir(parents=True, exist_ok=True)
            staged_caption.write_text(str(caption_fact["text"]).strip() + "\n", encoding="utf-8")
            local_results.append({
                "content_id": content_id,
                "status": "success",
                "source": "platform_caption",
                "text": str(caption_fact["text"]).strip(),
                "transcript_path": _relative_workspace_path(staged_caption, workspace_root),
                "evidence": caption_fact["evidence"],
            })
            continue
        if not video_url.startswith(("http://", "https://")):
            local_results.append({"content_id": content_id, "status": "failed", "source": "unavailable", "failure_reason": "视频缺少视频下载链接"})
            continue
        if platform_name == "抖音":
            rejection = douyin_asr_url_rejection(video_url)
            if rejection:
                local_results.append({"content_id": content_id, "status": "failed", "source": "unavailable", "failure_reason": rejection})
                continue
        asr_video_url = _bilibili_audio_url_for_row(row, video_url)
        items.append({"content_id": content_id, "video_url": asr_video_url, "url_hash": _asr_url_hash(asr_video_url)})
    payload: Dict[str, object] = {
        "schema_version": 1,
        "run_id": run_id,
        "flow_type": flow,
        **({"task_id": task} if task else {}),
        "account_id": account_id,
        **({"visibility_scope": scope, "selected_content_ids": selected} if flow == "self_account" else {}),
        "purpose": "content_transcript",
        "requested_content_ids": list(requested_ids),
        "items": items,
        "local_results": local_results,
        "idempotency_key": hashlib.sha256(
            f"{run_id}:{account_id}:{','.join(requested_ids)}:{','.join(str(item['url_hash']) for item in items)}".encode("utf-8")
        ).hexdigest(),
    }
    input_path, _result_path, _checkpoint_path = _asr_batch_paths(report_path)
    if write_input:
        input_path.parent.mkdir(parents=True, exist_ok=True)
        if input_path.is_file():
            try:
                existing = json.loads(input_path.read_text(encoding="utf-8"))
            except (OSError, UnicodeError, json.JSONDecodeError) as exc:
                raise RuntimeError("asr_batch_input_invalid") from exc
            if isinstance(existing, dict) and existing != payload:
                raise RuntimeError("asr_batch_input_already_locked")
        else:
            write_json_atomically(input_path, payload)
    return {"input_path": input_path, "result_path": _result_path, "payload": payload}


def run_local_transcript_batch(
    *, workspace_root: Path, report_path: Path, run_id: str, account_id: str,
    requested_ids: List[str], content_list_path: Path, content_library_path: Path,
    payload: Dict[str, object], args: argparse.Namespace,
) -> Dict[str, object]:
    """Run the checked-in local transcript executor for each missing video."""
    # 字段名、数据行和并发校验用的快照取自同一次读，避免三次读之间文件已变。
    content_fields, content_rows, content_snapshot = read_csv_table_with_snapshot(content_list_path)
    if content_library_path.resolve() == content_list_path.resolve():
        library_fields, library_rows, library_snapshot = [], content_rows, content_snapshot
    else:
        library_fields, library_rows, library_snapshot = read_csv_table_with_snapshot(content_library_path)
    by_id = {str(row.get("内容ID") or "").strip(): row for row in content_rows}
    library_by_id = {str(row.get("内容ID") or "").strip(): row for row in library_rows}
    result_items: List[Dict[str, object]] = []
    result_dir = report_path.parent / "local-transcript-results"
    transcript_executor = _EXECUTION_UNIT_DIR / "获取视频文案执行器.py"
    dir_owners = collect_content_dir_owners(
        [*content_rows, *library_rows],
        content_root=workspace_root / (
            "我方资料/我方内容" if "我方资料" in content_library_path.parts else "对标资料/对标内容"
        ),
        workspace_root=workspace_root,
    )
    planned_dir_names = plan_content_dir_names(
        (by_id.get(content_id) for content_id in requested_ids),
        content_library_path,
        existing_names=dir_owners,
    )
    for item in payload.get("items") if isinstance(payload.get("items"), list) else []:
        if not isinstance(item, dict):
            continue
        content_id = str(item.get("content_id") or "").strip()
        row = by_id.get(content_id)
        if not content_id or row is None or str(row.get("账号ID") or "").strip() != account_id.strip():
            result_items.append({"content_id": content_id, "status": "failed", "failure_reason": "本地 ASR 内容身份校验失败"})
            continue
        library_row = library_by_id.get(content_id, {})
        title = str(row.get("标题") or row.get("内容标题") or content_id).strip()
        platform = str(row.get("平台") or library_row.get("平台") or "").strip()
        media_type = str(row.get("媒介形式") or library_row.get("媒介形式") or "").strip()
        video_url = str(item.get("video_url") or row.get("视频下载链接") or "").strip()
        content_dir = _resolve_content_dir_for_batch(workspace_root, content_library_path, row, library_row, planned_dir_names, dir_owners)
        transcript_path = content_dir / "视频文案.md"
        result_path = result_dir / f"{content_id}.json"
        caption_path = report_path.parent / "platform-captions" / f"{content_id}.json"
        command = [
            sys.executable, str(transcript_executor),
            "--content-id", content_id,
            "--title", title,
            "--platform", platform,
            "--media-type", media_type,
            "--video-url", video_url,
            "--fetch-method", str(row.get("基础数据获取方式") or "").strip(),
            "--note-url", str(row.get("对外页面链接") or "").strip(),
            "--output-path", str(transcript_path),
            "--result-output-json", str(result_path),
            "--platform-caption-evidence-json", str(caption_path),
            "--workspace-root", str(workspace_root),
            "--run-id", run_id,
        ]
        native_id = str(row.get("平台原生内容ID") or "").strip()
        if native_id:
            command.extend(["--native-content-id", native_id])
        raw_detail = report_path.parent / "raw-note-details" / f"{content_id}.json"
        if raw_detail.is_file():
            command.extend(["--raw-detail-json", str(raw_detail)])
        try:
            receipt = run_single_item_executor(command, cwd=workspace_root)
            if not output_file_is_valid(transcript_path):
                raise RuntimeError("本地 ASR 未生成视频文案")
            relative_dir = _relative_workspace_path(content_dir, workspace_root)
            relative_transcript = _relative_workspace_path(transcript_path, workspace_root)
            row["内容目录路径"] = relative_dir
            row["视频文案路径"] = relative_transcript
            if library_row:
                library_row["内容目录路径"] = relative_dir
                library_row["视频文案路径"] = relative_transcript
            result_items.append({
                "content_id": content_id,
                "status": "success",
                "source": str(receipt.get("source") or "asr"),
                "transcript_path": relative_transcript,
                "result_path": _relative_workspace_path(result_path, workspace_root),
            })
        except (OSError, RuntimeError, ValueError, json.JSONDecodeError) as exc:
            result_items.append({"content_id": content_id, "status": "failed", "source": "asr", "failure_reason": str(exc)})
    for field in ("内容目录路径", "视频文案路径"):
        if field not in content_fields:
            content_fields.append(field)
        if field not in library_fields:
            library_fields.append(field)
    result_payload = {
        "schema_version": 1,
        "run_id": run_id,
        "account_id": account_id,
        "requested_content_ids": list(requested_ids),
        "items": result_items,
    }
    result_file = report_path.parent / "local-transcript-batch-result.json"
    result_file.parent.mkdir(parents=True, exist_ok=True)
    tables = [(content_list_path, content_fields, content_rows)]
    expected_originals = {content_list_path: content_snapshot}
    if content_library_path.resolve() != content_list_path.resolve():
        tables.append((content_library_path, library_fields, library_rows))
        expected_originals[content_library_path] = library_snapshot
    transaction_paths = [content_list_path, result_file]
    if content_library_path.resolve() != content_list_path.resolve():
        transaction_paths.append(content_library_path)
    with rollback_files_on_error(transaction_paths):
        write_csv_tables_atomically(tables, expected_originals=expected_originals)
        write_json_atomically(result_file, result_payload)
    return {"result_path": result_file, "payload": result_payload, "items": result_items}


def _build_canonical_facts(
    *, workspace_root: Path, content_list_path: Path, content_library_path: Path,
    requested_ids: List[str], account_id: str,
) -> List[Dict[str, object]]:
    _, rows = read_csv_table(content_list_path)
    _, library_rows = read_csv_table(content_library_path) if content_library_path.resolve() != content_list_path.resolve() else ([], rows)
    library_by_id = {str(row.get("内容ID") or "").strip(): row for row in library_rows}
    facts: List[Dict[str, object]] = []
    for content_id in requested_ids:
        row = next((item for item in rows if str(item.get("内容ID") or "").strip() == content_id), {})
        library_row = library_by_id.get(content_id, {})
        media = str(row.get("媒介形式") or library_row.get("媒介形式") or "").strip()
        cover = resolve_readable_file(workspace_root, row.get("封面图路径")) or resolve_readable_file(workspace_root, library_row.get("封面图路径"))
        transcript = resolve_readable_file(workspace_root, row.get("视频文案路径")) or resolve_readable_file(workspace_root, library_row.get("视频文案路径"))
        page = str(row.get("对外页面链接") or row.get("内容链接") or row.get("笔记链接") or "").strip()
        identity = bool(content_id and (page or cover or str(row.get("封面图原链接") or "").strip()))
        missing: List[str] = []
        if not cover:
            missing.append("封面")
        if media == "视频" and not transcript:
            missing.append("视频文案")
        errors = [structured_error("content_evidence_missing", "缺少页面或封面证据")] if not identity else []
        facts.append({"content_id": content_id, "base_data_available": identity, "missing_deliverables": missing, "blocking_errors": errors})
    return facts


def _write_or_update_canonical_for_batch(
    *, workspace_root: Path, report_path: Path, flow_type: str, task_id: str, run_id: str,
    account_id: str, requested_ids: List[str], scope: Dict[str, Any], content_list_path: Path,
    content_library_path: Path,
    retry_content_ids: List[str] | None = None,
) -> Dict[str, object]:
    facts = _build_canonical_facts(
        workspace_root=workspace_root, content_list_path=content_list_path,
        content_library_path=content_library_path, requested_ids=requested_ids, account_id=account_id,
    )
    finalized = finalize_item_facts(facts, requested_content_ids=requested_ids)
    return canonical_store.create_report(
        workspace_root=workspace_root, flow_type=flow_type, task_id=task_id, run_id=run_id,
        account_id=account_id, scope_mode=str(scope.get("scope_mode") or ""),
        scope_count=scope.get("scope_count"), requested_content_ids=requested_ids,
        report_path=report_path,
        initial_fields={
            "status": finalized["status"], "counts": finalized["counts"],
            "processed_content_ids": list(requested_ids),
            "detail_collection": {"items": finalized["items"], "collected_count": sum(item["base_data_available"] for item in finalized["items"])},
            "followup_delivery": {"status": "completed", "items": [], "completed_count": 0, "missing_count": 0},
            # 视频文案降级的条目必须进入重试清单。ASR 编排路径不产出单条
            # 「执行结果.json」，重试清单只能由批次结果直接带过来，否则恒为空。
            "retry_content_ids": list(dict.fromkeys(
                str(value).strip() for value in (retry_content_ids or []) if str(value).strip()
            )),
            **({"visibility_scope": str(scope.get("visibility_scope") or "public").strip().casefold()} if flow_type == "self_account" else {}),
            "artifacts": {"detail_data_report": _relative_workspace_path(report_path, workspace_root)},
        },
    )


def commit_asr_batch(
    *, workspace_root: Path, report_path: Path, run_id: str, account_id: str,
    content_list_path: Path, content_library_path: Path,
    flow_type: str = "benchmark_account", task_id: str = "",
    visibility_scope: str = "", selected_content_ids: List[str] | None = None,
) -> Dict[str, object]:
    """Validate and commit one server batch result; replay is idempotent."""
    input_path, result_path, checkpoint_path = _asr_batch_paths(report_path)
    if not input_path.is_file() or not result_path.is_file():
        raise RuntimeError("asr_batch_input_or_result_missing")
    try:
        expected = json.loads(input_path.read_text(encoding="utf-8"))
        result = load_normalized_asr_batch_result(result_path)
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise RuntimeError("asr_batch_payload_invalid") from exc
    if not isinstance(expected, dict) or not isinstance(result, dict):
        raise RuntimeError("asr_batch_payload_invalid")
    # A result without an explicit run_id is unscoped and must never be
    # treated as belonging to the currently requested batch.
    flow = str(flow_type or expected.get("flow_type") or "benchmark_account").strip()
    task = str(task_id or expected.get("task_id") or "").strip()
    if flow == "benchmark_account" and task:
        raise RuntimeError("asr_batch_benchmark_task_id_forbidden")
    if str(expected.get("run_id") or "").strip() != run_id or str(result.get("run_id") or "").strip() != run_id:
        raise RuntimeError("asr_batch_run_id_mismatch")
    if str(expected.get("flow_type") or "benchmark_account").strip() != flow:
        raise RuntimeError("asr_batch_flow_type_mismatch")
    if task and str(expected.get("task_id") or "").strip() != task:
        raise RuntimeError("asr_batch_task_id_mismatch")
    if str(result.get("flow_type") or flow).strip() != flow:
        raise RuntimeError("asr_batch_flow_type_mismatch")
    if task and str(result.get("task_id") or "").strip() != task:
        raise RuntimeError("asr_batch_task_id_mismatch")
    if str(expected.get("account_id") or "").strip() != account_id:
        raise RuntimeError("asr_batch_account_mismatch")
    if str(expected.get("purpose") or "").strip() != "content_transcript":
        raise RuntimeError("asr_batch_purpose_invalid")
    if flow == "self_account":
        expected_scope = str(expected.get("visibility_scope") or "").strip().casefold()
        requested_scope = str(visibility_scope or expected_scope).strip().casefold()
        if expected_scope not in {"public", "private", "all"} or requested_scope != expected_scope:
            raise RuntimeError("asr_batch_visibility_scope_mismatch")
        expected_selected = [str(item).strip() for item in (expected.get("selected_content_ids") or []) if str(item).strip()]
        expected_requested = [str(item).strip() for item in (expected.get("requested_content_ids") or []) if str(item).strip()]
        if expected_selected != expected_requested or (selected_content_ids is not None and [str(item).strip() for item in selected_content_ids if str(item).strip()] != expected_requested):
            raise RuntimeError("asr_batch_scope_ids_mismatch")
        result_scope = str(result.get("visibility_scope") or expected_scope).strip().casefold()
        if result_scope != expected_scope:
            raise RuntimeError("asr_batch_visibility_scope_mismatch")
        result_selected = [str(item).strip() for item in (result.get("selected_content_ids") or expected_requested) if str(item).strip()]
        if result_selected != expected_requested:
            raise RuntimeError("asr_batch_scope_ids_mismatch")
    serialized_result = json.dumps(result, ensure_ascii=False, sort_keys=True).encode("utf-8")
    result_hash = hashlib.sha256(serialized_result).hexdigest()
    if checkpoint_path.is_file():
        checkpoint = json.loads(checkpoint_path.read_text(encoding="utf-8"))
        if isinstance(checkpoint, dict) and checkpoint.get("result_sha256") == result_hash:
            return {
                "status": "idempotent", "run_id": run_id, "checkpoint": str(checkpoint_path),
                "next_action": "finalize",
            }
        raise RuntimeError("asr_batch_result_already_committed")
    expected_items = {str(item.get("content_id") or "").strip(): item for item in expected.get("items", []) if isinstance(item, dict)}
    local_results = [item for item in expected.get("local_results", []) if isinstance(item, dict)]
    result_items = result.get("items")
    result_items = result_items if isinstance(result_items, list) else None
    result_ids = {
        str(item.get("content_id") or "").strip()
        for item in result_items or []
        if isinstance(item, dict)
    }
    # A batch with no pending ASR items is valid: platform captions were
    # already committed to local_results and the server returns items=[].
    result_invalid = (
        result_items is None
        or any(not isinstance(item, dict) for item in result_items)
        or len(result_ids) != len(result_items)
        or result_ids - set(expected_items)
        or (expected_items and result_ids != set(expected_items))
        or (not expected_items and result_ids)
    )
    if result_invalid:
        raise RuntimeError("asr_batch_items_mismatch")
    if "ticket" in json.dumps(result, ensure_ascii=False).lower():
        raise RuntimeError("asr_batch_ticket_forbidden")
    # Resolve aliases before reading so the same CSV is read and committed
    # once; csv_safe_write rejects duplicate transaction targets.
    content_list_path = Path(content_list_path).expanduser().resolve()
    content_library_path = Path(content_library_path).expanduser().resolve()
    content_fields, content_rows, content_snapshot = read_csv_table_with_snapshot(content_list_path)
    if content_library_path == content_list_path:
        library_fields, library_rows, library_snapshot = content_fields, content_rows, content_snapshot
    else:
        library_fields, library_rows, library_snapshot = read_csv_table_with_snapshot(content_library_path)
    content_by_id = {str(row.get("内容ID") or "").strip(): row for row in content_rows}
    library_by_id = {str(row.get("内容ID") or "").strip(): row for row in library_rows}
    committed_items: List[Dict[str, object]] = []
    planned_writes: List[Dict[str, object]] = []
    for local in local_results:
        local_id = str(local.get("content_id") or "").strip()
        if not local_id:
            continue
        local_status = str(local.get("status") or "failed").strip()
        if local_status == "success" and str(local.get("source") or "") == "platform_caption" and str(local.get("text") or "").strip():
            evidence = local.get("evidence")
            if not isinstance(evidence, dict) or not str(evidence.get("url_hash") or "").strip():
                raise RuntimeError("asr_batch_platform_caption_evidence_invalid")
            row = content_by_id.get(local_id)
            if row is None:
                raise RuntimeError("asr_batch_content_missing")
            title = str(row.get("标题") or local_id).strip()
            content_dir_value = str(row.get("内容目录路径") or "").strip()
            default_root = "我方资料/我方内容" if flow == "self_account" else "对标资料/对标内容"
            content_dir = (workspace_root / content_dir_value).resolve() if content_dir_value else (workspace_root / default_root / local_id).resolve()
            content_dir.relative_to(workspace_root.resolve())
            transcript_path = content_dir / "视频文案.md"
            row["内容目录路径"] = _relative_workspace_path(content_dir, workspace_root)
            row["视频文案路径"] = _relative_workspace_path(transcript_path, workspace_root)
            if local_id in library_by_id:
                library_by_id[local_id]["内容目录路径"] = row["内容目录路径"]
                library_by_id[local_id]["视频文案路径"] = row["视频文案路径"]
            process_dir = build_content_process_dir(content_list_path, local_id, title)
            planned_writes.append({
                "content_id": local_id, "text": str(local["text"]).strip(),
                "content_dir": content_dir, "transcript_path": transcript_path,
                "process_dir": process_dir, "asr_record_path": process_dir / "ASR批次提交.json",
                "source": {"url_hash": evidence["url_hash"], "source": "platform_caption"},
            })
            committed_items.append({"content_id": local_id, "status": "success", "source": "platform_caption", "transcript_path": row["视频文案路径"]})
        else:
            committed_items.append({"content_id": local_id, "status": local_status, "source": str(local.get("source") or "unavailable"), "failure_reason": str(local.get("failure_reason") or "")})
    if flow == "self_account":
        expected_scope = str(expected.get("visibility_scope") or "").strip().casefold()
        expected_status = "公开" if expected_scope == "public" else "私密"
        for content_id in expected.get("requested_content_ids") or []:
            row = content_by_id.get(str(content_id).strip())
            if row is None or normalize_public_status(row.get("公开状态")) != expected_status:
                raise RuntimeError("asr_batch_visibility_scope_mismatch")
    for item in result_items:
        if not isinstance(item, dict):
            raise RuntimeError("asr_batch_item_invalid")
        content_id = str(item.get("content_id") or "").strip()
        source = expected_items[content_id]
        if str(source.get("url_hash") or "") and str(source.get("url_hash")) != _asr_url_hash(str(source.get("video_url") or "")):
            raise RuntimeError("asr_batch_url_hash_mismatch")
        if str(item.get("url_hash") or "") != str(source.get("url_hash") or ""):
            raise RuntimeError("asr_batch_result_url_hash_mismatch")
        status = str(item.get("status") or "failed").strip()
        row = content_by_id.get(content_id)
        if row is None:
            raise RuntimeError("asr_batch_content_missing")
        text = str(item.get("text") or item.get("transcript") or "").strip()
        if status == "success" and text:
            title = str(row.get("标题") or content_id).strip()
            content_dir_value = str(row.get("内容目录路径") or "").strip()
            default_root = "我方资料/我方内容" if flow == "self_account" else "对标资料/对标内容"
            content_dir = (workspace_root / content_dir_value).resolve() if content_dir_value and not Path(content_dir_value).is_absolute() else Path(content_dir_value).resolve() if content_dir_value else (workspace_root / default_root / content_id).resolve()
            content_dir.relative_to(workspace_root.resolve())
            transcript_path = content_dir / "视频文案.md"
            row["内容目录路径"] = _relative_workspace_path(content_dir, workspace_root)
            row["视频文案路径"] = _relative_workspace_path(transcript_path, workspace_root)
            if content_id in library_by_id:
                library_by_id[content_id]["内容目录路径"] = row["内容目录路径"]
                library_by_id[content_id]["视频文案路径"] = row["视频文案路径"]
            process_dir = build_content_process_dir(content_list_path, content_id, title)
            planned_writes.append({
                "content_id": content_id,
                "text": text,
                "content_dir": content_dir,
                "transcript_path": transcript_path,
                "process_dir": process_dir,
                "asr_record_path": process_dir / "ASR批次提交.json",
                "source": source,
            })
            committed_items.append({"content_id": content_id, "status": "success", "transcript_path": row["视频文案路径"]})
        else:
            committed_items.append({"content_id": content_id, "status": "failed", "error_code": str(item.get("error_code") or "asr_failed")})
    update_fields = {"内容目录路径", "视频文案路径"}
    for field in update_fields:
        if field not in content_fields: content_fields.append(field)
        if field not in library_fields: library_fields.append(field)
    checkpoint = {
        "schema_version": 1, "run_id": run_id, "flow_type": flow,
        **({"task_id": task} if task else {}),
        **({"visibility_scope": str(expected.get("visibility_scope") or visibility_scope or "public"), "selected_content_ids": list(expected.get("selected_content_ids") or expected.get("requested_content_ids") or [])} if flow == "self_account" else {}),
        "account_id": account_id, "result_sha256": result_hash, "items": committed_items,
    }
    transaction_paths: List[Path] = [content_list_path, checkpoint_path]
    if content_library_path != content_list_path:
        transaction_paths.append(content_library_path)
    for planned in planned_writes:
        transaction_paths.extend([
            planned["transcript_path"],
            planned["asr_record_path"],
        ])
    with rollback_files_on_error(transaction_paths):
        for planned in planned_writes:
            content_dir = planned["content_dir"]
            process_dir = planned["process_dir"]
            transcript_path = planned["transcript_path"]
            asr_record_path = planned["asr_record_path"]
            source = planned["source"]
            content_dir.mkdir(parents=True, exist_ok=True)
            transcript_path.write_text(str(planned["text"]) + "\n", encoding="utf-8")
            process_dir.mkdir(parents=True, exist_ok=True)
            write_json_atomically(asr_record_path, {
                "run_id": run_id,
                "content_id": planned["content_id"],
                "url_hash": source.get("url_hash"),
                "status": "success",
                "transcript_path": str(transcript_path),
                "result_sha256": result_hash,
            })
        tables = [(content_list_path, content_fields, content_rows)]
        expected_originals = {content_list_path: content_snapshot}
        if content_library_path != content_list_path:
            tables.append((content_library_path, library_fields, library_rows))
            expected_originals[content_library_path] = library_snapshot
        write_csv_tables_atomically(tables, expected_originals=expected_originals)
        write_json_atomically(checkpoint_path, checkpoint)
    requested_ids = _ordered_content_ids(expected.get("requested_content_ids"))
    if not requested_ids:
        requested_ids = list(expected_items)
    report = canonical_store.load_report(report_path, validate=False) if report_path.is_file() else None
    report_scope = report if isinstance(report, dict) else {}
    if flow == "self_account":
        selected = [str(item).strip() for item in (selected_content_ids or expected.get("requested_content_ids") or requested_ids) if str(item).strip()]
        if selected != list(requested_ids):
            raise RuntimeError("asr_batch_scope_ids_mismatch")
        report_scope = {**report_scope, "scope_mode": report_scope.get("scope_mode") or "selected", "visibility_scope": visibility_scope or expected.get("visibility_scope") or "public"}
    _write_or_update_canonical_for_batch(
        workspace_root=workspace_root, report_path=report_path, flow_type=flow, task_id=task,
        run_id=run_id, account_id=account_id, requested_ids=requested_ids,
        scope=report_scope, content_list_path=content_list_path, content_library_path=content_library_path,
    )
    return {
        "status": "committed",
        "run_id": run_id, "checkpoint": str(checkpoint_path), "items": committed_items,
        "next_action": "finalize",
    }


@contextmanager
def rollback_files_on_error(paths: Iterable[Path]) -> Iterator[None]:
    """多文件正式提交中途失败时，把这几个文件恢复成进入事务前的样子。

    快照在进入事务前一次性读好；回滚本身再失败时，把原始异常和回滚错误一起报出来，
    不吞掉任何一边。
    """
    snapshots: Dict[Path, bytes | None] = {}
    for path in paths:
        resolved = path.resolve()
        if resolved not in snapshots:
            snapshots[resolved] = resolved.read_bytes() if resolved.is_file() else None
    try:
        yield
    except BaseException as original_error:
        rollback_errors: List[str] = []
        for path, original_payload in snapshots.items():
            try:
                if original_payload is None:
                    if path.exists() or path.is_symlink():
                        path.unlink()
                else:
                    write_bytes_atomically(path, original_payload)
            except Exception as rollback_error:  # noqa: BLE001
                rollback_errors.append(f"{path}: {rollback_error}")
        if rollback_errors:
            raise RuntimeError(
                f"详细数据正式提交失败，且部分文件回滚失败: {'；'.join(rollback_errors)}"
            ) from original_error
        raise


def canonical_main() -> int:
    """Run one locked batch action; all formal output is canonical-detail-report.json."""
    args = parse_args()
    workspace_root = Path(args.workspace_root).resolve()
    if not args.action:
        raise SystemExit("详细数据批处理必须使用 --action process 或 --action finalize")
    if str(args.flow_type or "").strip() == "benchmark_account" and str(args.task_id or "").strip():
        print("对标详细数据公开动作只接受 run_id，不得传入 task_id", file=sys.stderr)
        return 1
    try:
        context = resolve_batch_context(workspace_root, args.run_id, task_id=args.task_id, flow_type=args.flow_type)
        args.flow_type = str(context["flow_type"])
        args.task_id = str(context.get("task_id") or "")
        args.account_id = str(context["account_id"])
        args.content_list = str(context["content_list"])
        args.content_library = str(context["content_library"])
        locked_scope = context.get("scope") if isinstance(context.get("scope"), dict) else {}
        if not locked_scope.get("confirmed"):
            raise RuntimeError("批次上下文尚未确认")
        if not str(locked_scope.get("scope_mode") or "").strip():
            raise RuntimeError("批次上下文缺少已确认的 scope_mode")
        if args.flow_type == "self_account" and str(locked_scope.get("visibility_scope") or "").strip().casefold() not in {"public", "private", "all"}:
            raise RuntimeError("批次上下文缺少已确认的 visibility_scope")
        if not str(context.get("data_source") or "").strip():
            raise RuntimeError("批次上下文缺少已确认的数据源")
        if context.get("content_ids"):
            args.content_ids = ",".join(context["content_ids"])
        flow_type, public_task_id, run_id = resolve_scheduled_identity(
            workspace_root,
            flow_type=args.flow_type,
            task_id="" if args.flow_type == "benchmark_account" else args.task_id,
            run_id=args.run_id,
            account_id=args.account_id,
        )
    except (OSError, UnicodeError, json.JSONDecodeError, RuntimeError) as exc:
        print(str(exc), file=sys.stderr)
        return 1
    content_list_path = Path(args.content_list).resolve()
    content_library_path = Path(args.content_library).resolve()
    flow_root = "benchmark-account-diagnose" if flow_type == "benchmark_account" else "self-account-diagnose"
    canonical_path = (
        workspace_root / ".meiling/staging" / flow_root / run_id / "detail-data/canonical-detail-report.json"
    )
    if not canonical_path.is_relative_to(workspace_root):
        print("canonical report path outside workspace", file=sys.stderr)
        return 1
    if args.action == "finalize":
        try:
            if not canonical_path.is_file():
                raise RuntimeError("canonical_report_missing")
            # 只跑 finalize 也要能纠正「产物在盘上、CSV 路径没回填」：先补路径，再重算报告，
            # 否则单跑 finalize 拿到的是旧缺口（2026-11-01）。
            finalize_ids = [str(value).strip() for value in str(args.content_ids or "").split(",") if str(value).strip()]
            backfilled_content_ids = backfill_delivery_paths_from_disk(
                workspace_root=workspace_root,
                content_list_path=content_list_path,
                content_library_path=content_library_path,
                requested_ids=finalize_ids,
            )
            if backfilled_content_ids:
                _write_or_update_canonical_for_batch(
                    workspace_root=workspace_root, report_path=canonical_path, flow_type=flow_type,
                    task_id=public_task_id, run_id=run_id, account_id=args.account_id,
                    requested_ids=finalize_ids, scope=locked_scope,
                    content_list_path=content_list_path, content_library_path=content_library_path,
                )
            report = canonical_store.load_report(canonical_path, account_id=args.account_id, validate=False)
            report_status = str(report.get("status") or "").strip()
            if report_status == "failed":
                print(json.dumps({
                    "run_id": run_id, "status": report.get("status"),
                    "next_action": "retry_or_review",
                    "backfilled_content_ids": backfilled_content_ids,
                    "report_json": canonical_path.relative_to(workspace_root).as_posix(),
                }, ensure_ascii=False, indent=2))
                return 0
            if report_status not in {"completed", "partial"}:
                raise RuntimeError("canonical_report_status_invalid")
            print(json.dumps({
                "run_id": run_id,
                "status": report.get("status"),
                "next_action": "continue_detail_flow",
                "backfilled_content_ids": backfilled_content_ids,
                "report_json": canonical_path.relative_to(workspace_root).as_posix(),
            }, ensure_ascii=False, indent=2))
            return 0
        except (OSError, ValueError, RuntimeError, json.JSONDecodeError, DetailDataContractError) as exc:
            print(str(exc), file=sys.stderr)
            return 1
    requested_ids = [str(value).strip() for value in str(args.content_ids or "").split(",") if str(value).strip()]
    if not requested_ids:
        print("本轮内容范围尚未确认", file=sys.stderr)
        return 1

    try:
        validate_requested_account(content_list_path, requested_ids, args.account_id)
    except RuntimeError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    if args.dry_run:
        print(json.dumps({"status": "planned", "run_id": run_id, "content_ids": requested_ids, "canonical_report": canonical_path.relative_to(workspace_root).as_posix()}, ensure_ascii=False, indent=2))
        return 0
    if args.action == "process":
        try:
            # 先把「产物已经在盘上、但 CSV 路径列还空着」的补上，再判断谁缺封面/文案；
            # 否则补跑成功的那条会被当成没做，一直卡在缺口里（2026-11-01）。
            backfilled_content_ids = backfill_delivery_paths_from_disk(
                workspace_root=workspace_root,
                content_list_path=content_list_path,
                content_library_path=content_library_path,
                requested_ids=requested_ids,
            )
            run_cover_batch(
                workspace_root=workspace_root, report_path=canonical_path, account_id=args.account_id,
                requested_ids=requested_ids, content_list_path=content_list_path,
                content_library_path=content_library_path, args=args,
            )
            _write_or_update_canonical_for_batch(
                workspace_root=workspace_root, report_path=canonical_path, flow_type=flow_type,
                task_id=public_task_id, run_id=run_id, account_id=args.account_id,
                requested_ids=requested_ids, scope=locked_scope,
                content_list_path=content_list_path, content_library_path=content_library_path,
            )
            batch = build_asr_batch_input(
                workspace_root=workspace_root,
                report_path=canonical_path,
                run_id=run_id,
                account_id=args.account_id,
                requested_ids=requested_ids,
                content_list_path=content_list_path,
                content_library_path=content_library_path,
                flow_type=flow_type,
                task_id=public_task_id,
                visibility_scope=str(locked_scope.get("visibility_scope") or ""),
                selected_content_ids=list(locked_scope.get("selected_content_ids") or []),
                data_source=str(context.get("data_source") or ""),
                write_input=False,
            )
        except (OSError, ValueError, RuntimeError, json.JSONDecodeError) as exc:
            print(str(exc), file=sys.stderr)
            return 1
        payload = batch["payload"]
        if isinstance(payload, dict) and payload.get("items"):
            local_batch = run_local_transcript_batch(
                workspace_root=workspace_root,
                report_path=canonical_path,
                run_id=run_id,
                account_id=args.account_id,
                requested_ids=requested_ids,
                content_list_path=content_list_path,
                content_library_path=content_library_path,
                payload=payload,
                args=args,
            )
            asr_retry_ids = [
                str(item.get("content_id") or "").strip()
                for item in local_batch["items"]
                if isinstance(item, dict)
                and str(item.get("status") or "").strip() == "failed"
                and str(item.get("content_id") or "").strip()
            ]
            _write_or_update_canonical_for_batch(
                workspace_root=workspace_root, report_path=canonical_path, flow_type=flow_type,
                task_id=public_task_id, run_id=run_id, account_id=args.account_id,
                requested_ids=requested_ids, scope=locked_scope,
                content_list_path=content_list_path, content_library_path=content_library_path,
                retry_content_ids=asr_retry_ids,
            )
            payload["local_results"] = list(payload.get("local_results") or []) + list(local_batch["items"])
        canonical_status = "partial"
        try:
            canonical_status = str(canonical_store.load_report(canonical_path, validate=False).get("status") or "partial")
        except (OSError, UnicodeError, json.JSONDecodeError, DetailDataContractError):
            canonical_status = "partial"
        if canonical_status == "failed":
            print(json.dumps({
                "run_id": run_id,
                "status": "failed",
                "next_action": "retry_or_review",
                "report_json": _relative_workspace_path(canonical_path, workspace_root),
            }, ensure_ascii=False, indent=2))
            return 0
        output = {
            "run_id": run_id, "status": "partial",
            "next_action": "finalize",
            "asr_batch_skipped": True,
            "backfilled_content_ids": backfilled_content_ids,
        }
        print(json.dumps(output, ensure_ascii=False, indent=2))
        return 0
    item_results: List[Dict[str, object]] = []
    executor_path = _EXECUTION_UNIT_DIR / "单条内容详细数据执行器.py"
    for content_id in requested_ids:
        command = build_single_item_command(args, content_id, executor_path)
        try:
            item_results.append(run_single_item_executor(command, cwd=workspace_root))
        except RuntimeError as exc:
            item_results.append({"account_id": args.account_id, "content_id": content_id, "status": "失败", "steps": {}, "failures": [str(exc)], "generated_files": []})
    try:
        summary = finalize_item_results(account_id=args.account_id, requested_ids=requested_ids, item_results=item_results, workspace_root=workspace_root, content_list_path=content_list_path, content_library_path=content_library_path, report_path=canonical_path, run_mode="failed-only" if args.failed_only else "normal", run_id=run_id, flow_type=flow_type, task_id=public_task_id, scope=locked_scope)
        output = build_stdout_summary(summary, canonical_path)
        output["run_id"] = run_id
        output["status"] = summary.get("status")
        output["report_json"] = canonical_path.relative_to(workspace_root).as_posix()
        # 采集动作只生成本轮 canonical report；是否继续、重试或交接
        # 由公开流程统一判断，不能在采集动作内提前交接。
        output["next_action"] = "continue_detail_flow"
        output["backfilled_content_ids"] = backfilled_content_ids
        print(json.dumps(output, ensure_ascii=False, indent=2))
        return 0
    except (OSError, ValueError, RuntimeError, json.JSONDecodeError, DetailDataContractError) as exc:
        print(str(exc), file=sys.stderr)
        return 1
def main() -> int:
    return canonical_main()


if __name__ == "__main__":
    raise SystemExit(canonical_main())
