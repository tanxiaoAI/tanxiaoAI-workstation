#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import re
import shlex
import shutil
import string
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping

_EXECUTION_UNIT_DIR = Path(__file__).resolve().parent
if str(_EXECUTION_UNIT_DIR) not in sys.path:
    sys.path.insert(0, str(_EXECUTION_UNIT_DIR))

from csv安全写入 import (
    read_csv_table,
    read_csv_table_with_snapshot,
    sync_owned_fields,
    write_csv_tables_atomically,
)
from 过程记录路径 import (
    build_benchmark_content_dir_name,
    build_content_dir_name,
    build_content_process_dir,
    collect_content_dir_owners,
    plan_content_dir_names,
    resolve_existing_content_dir as resolve_safe_existing_content_dir,
    sanitize_title,
    UnsafeContentDirectoryError,
)
from 本地配置读取 import load_local_config
import 证书信任  # noqa: F401  进程级统一 HTTPS 证书信任
from 权限错误 import permission_error_code
from 账号阶段状态 import build_account_stage_state, write_json_atomically, write_state_atomically
from 账号基础数据建档批处理 import normalize_public_status
from 互动数解析 import count_text
from 发布时间解析 import normalize_publish_time_text
from 详细数据阶段合同 import (
    CHROME_FIELDS,
    CHROME_BODY_FIELDS,
    CHROME_CONTRACT_VERSION,
    DetailDataContractError,
    derive_item_status,
    finalize_item_facts,
    chrome_result_schema,
    has_blocking_errors,
    is_valid_http_url,
    structured_error,
    validate_canonical_report,
    validate_chrome_item,
    validate_identity,
)
import 详细数据报告存储器 as canonical_store


DETAIL_FIELDS = ["发布时间", "视频下载链接", "收藏数", "分享数", "评论数"]
INTERACTION_FIELDS = ("点赞数", "收藏数", "分享数", "评论数")
SENSITIVE_QUERY_KEYS = {"token", "key", "api_key", "apikey", "access_token"}

OPTIONAL_DETAIL_FIELDS = ["点赞数"]
STRUCTURED_CONTEXT_FIELDS = ["封面图原链接", "媒介形式"]
STRUCTURED_DETAIL_FILE_NAME = "内容基础结构化数据.json"
STRUCTURED_CLI_TEMPLATE_FIELDS = frozenset(
    {
        "content_id",
        "title",
        "platform",
        "account_id",
        "account_name",
        "note_url",
        "content_dir",
        "output_path",
        "workspace_root",
        "fetch_method",
    }
)
CONTENT_DIR_FIELD = "内容目录路径"
CONTENT_DIR_PATH_FIELDS = [
    CONTENT_DIR_FIELD,
    "封面图路径",
    "视频文案路径",
    "内容分析路径",
    "发布数据路径",
    "复盘总结路径",
    "内容生产记录路径",
]
CONTENT_LIBRARY_FIELD_ALIASES = {
    "账号名": ("账号名称", "账号昵称"),
    "账号名称": ("账号名", "账号昵称"),
    "账号昵称": ("账号名", "账号名称"),
    "对外页面链接": ("内容链接", "笔记链接"),
    "内容链接": ("对外页面链接", "笔记链接"),
    "笔记链接": ("对外页面链接", "内容链接"),
}
CONTENT_LIBRARY_FILL_ONLY_FIELDS = frozenset(
    {
        "内容ID",
        "平台",
        "账号ID",
        "账号名",
        "账号名称",
        "账号昵称",
        "标题",
        "媒介形式",
        "公开状态",
        "封面图原链接",
        CONTENT_DIR_FIELD,  # 同上：路径只填空、不覆盖
        "对外页面链接",
        "内容链接",
        "笔记链接",
    }
)
CONTENT_LIBRARY_OWNED_FIELDS = CONTENT_LIBRARY_FILL_ONLY_FIELDS | frozenset(
    {
        CONTENT_DIR_FIELD,
        "发布时间",
        "视频下载链接",
        "点赞数",
        "收藏数",
        "分享数",
        "评论数",
        "基础数据获取方式",
        "备注",
    }
)
# 路径字段只填空、不覆盖：命中已有行时，这些路径是「内容生产/复盘」链路建的正式
# 引用，而刷新链路手上的同名路径指向它自己算出来的目录 —— 覆盖会把正式引用指到别处。
OWN_CONTENT_LIBRARY_FILL_ONLY_FIELDS = frozenset(
    {
        "内容ID",
        "标题",
        "媒介形式",
        CONTENT_DIR_FIELD,
        "封面图路径",
        "视频文案路径",
        }
)
OWN_CONTENT_LIBRARY_OWNED_FIELDS = OWN_CONTENT_LIBRARY_FILL_ONLY_FIELDS | frozenset(
    {CONTENT_DIR_FIELD, "封面图路径", "视频文案路径", "备注"}
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="批量补全账号内容的发布时间、视频链接与互动字段。")
    parser.add_argument("--account-id", default="", help=argparse.SUPPRESS)
    parser.add_argument("--content-list", default="", help=argparse.SUPPRESS)
    parser.add_argument(
        "--content-library",
        default="",
        help=argparse.SUPPRESS,
    )
    parser.add_argument("--content-ids", default="", help=argparse.SUPPRESS)
    parser.add_argument(
        "--workspace-root",
        default=".",
        help='工作区根目录；默认取当前目录（"."），从其他目录调用时必须显式传入',
    )
    parser.add_argument("--run-id", default="", help="本轮批次唯一标识")
    parser.add_argument("--flow-type", default="", choices=("", "benchmark_account", "self_account"), help=argparse.SUPPRESS)
    parser.add_argument("--visibility-scope", default="", choices=("", "public", "private", "all"), help=argparse.SUPPRESS)
    parser.add_argument(
        "--task-id",
        default="",
        help=argparse.SUPPRESS,
    )
    parser.add_argument(
        "--action",
        choices=("", "init-batch", "prepare-chrome", "commit-chrome", "collect-details", "calculate-derived", "retry", "finalize"),
        default="",
        help="批次动作；必须与 --run-id 配合使用。范围准备不传本参数，改用 --prepare-content-scope / --confirm-content-scope",
    )
    parser.add_argument(
        "--prepare-content-scope",
        action="store_true",
        help="范围准备：只读计算并输出本轮内容范围，不写入任何正式文件",
    )
    parser.add_argument(
        "--confirm-content-scope",
        action="store_true",
        help="范围准备：必须与 --prepare-content-scope 同时使用，锁定并写入本轮内容清单；不得单独调用",
    )
    parser.add_argument(
        "--scope-mode",
        choices=("list_order", "latest", "all", "custom"),
        default="",
        help="范围口径；仅与 --prepare-content-scope 配合使用",
    )
    parser.add_argument(
        "--scope-count",
        type=int,
        default=0,
        help="范围条数；latest 与 list_order 模式下生效",
    )
    parser.add_argument(
        "--data-source",
        choices=("chrome_devtools", "justoneapi"),
        default="",
        help=argparse.SUPPRESS,
    )
    parser.add_argument(
        "--acquisition-attempt",
        type=int,
        choices=(1, 2),
        default=1,
        help=argparse.SUPPRESS,
    )
    parser.add_argument(
        "--structured-fetch-command",
        default="",
        help=argparse.SUPPRESS,
    )
    parser.add_argument(
        "--allow-source-switch",
        action="store_true",
        help=argparse.SUPPRESS,
    )
    parser.add_argument("--dry-run", action="store_true", help=argparse.SUPPRESS)
    return parser.parse_args()


load_env = load_local_config


def _workspace_relative(root: Path, path: Path, label: str) -> str:
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError as exc:
        raise ValueError(f"{label}必须位于当前工作区内") from exc


def _public_chrome_output(workspace_root: Path, payload: Dict[str, object]) -> Dict[str, object]:
    """Expose only workspace-relative business paths; keep execution paths internal."""
    output = dict(payload)
    for key in (
        "content_list_path",
        "content_library_path",
        "result_file_exact_path",
        "template_file_exact_path",
        "schema_file_exact_path",
        "chrome_plan_json",
        "chrome_template_json",
        "chrome_schema_json",
        "chrome_result_json",
    ):
        value = output.get(key)
        if value:
            output[key] = _workspace_relative(workspace_root, Path(str(value)), key)
    targets = output.get("targets")
    if isinstance(targets, list):
        output["targets"] = [
            {
                **target,
                "output_json": _workspace_relative(
                    workspace_root, Path(str(target["output_json"])), "Chrome 结构化明细"
                ),
            }
            if isinstance(target, dict) and target.get("output_json")
            else target
            for target in targets
        ]
    return output


def build_detail_stage_state(
    *,
    workspace_root: Path,
    content_list_path: Path,
    account_id: str,
    account_name: str,
    platform: str,
    report_path: Path,
    summary: Dict[str, Any],
    task_id: str = "",
    run_id: str = "",
    scope: Dict[str, Any] | None = None,
) -> Dict[str, Any]:
    retry_required = bool(summary.get("retry_required"))
    if retry_required:
        status = "retryable_failed"
    else:
        status = "in_progress"
    selected = summary.get("requested_content_ids") or []
    unresolved = summary.get("unresolved_content_ids") or summary.get("retry_content_ids") or []
    state = build_account_stage_state(
        stage=detail_stage_name(content_list_path),
        complete=False,
        platform=platform,
        account_id=account_id,
        account_name=account_name,
        waiting_for_user="",
        next_flows=(),
        recovery_context={
            "status": status,
            "current_entry": "detail_data",
            "next_action": "",
            "report_path": _workspace_relative(workspace_root, report_path, "详细数据报告"),
            "data_source": str(summary.get("data_source") or "").strip(),
            "selected_content_ids": selected,
            "unresolved_content_ids": unresolved,
            "acquisition_attempt": int(summary.get("acquisition_attempt") or 0),
        },
    )
    if task_id or run_id:
        state["task_id"] = str(task_id or "").strip()
        state["run_id"] = str(run_id or "").strip()
    if isinstance(scope, dict):
        state["scope"] = {
            "scope_mode": str(scope.get("scope_mode") or "").strip(),
            "scope_count": scope.get("scope_count"),
            **({"visibility_scope": str(scope.get("visibility_scope") or "").strip()} if scope.get("visibility_scope") else {}),
            "selected_content_ids": list(scope.get("selected_content_ids") or []),
            "selected_count": int(scope.get("selected_count") or 0),
            "available_count": int(scope.get("available_count") or 0),
            "pending_count": int(scope.get("pending_count") or 0),
            "scope_json": str(scope.get("scope_json") or "").strip(),
            "confirmed": scope.get("confirmed") is True,
        }
    return state


def build_scope_stage_state(
    *,
    workspace_root: Path,
    content_list_path: Path,
    account_id: str,
    account_name: str,
    platform: str,
    task_id: str,
    run_id: str,
    scope: Dict[str, Any],
    data_source: str,
) -> Dict[str, Any]:
    scope_json = str(scope.get("scope_json") or "").strip()
    state = build_account_stage_state(
        stage=detail_stage_name(content_list_path),
        complete=False,
        platform=platform,
        account_id=account_id,
        account_name=account_name,
        waiting_for_user="",
        next_flows=(),
        recovery_context={
            "status": "in_progress",
            "current_entry": "detail_data",
            "next_action": "",
            "data_source": str(data_source or "").strip(),
            "selected_content_ids": list(scope.get("selected_content_ids") or []),
        },
    )
    state["task_id"] = str(task_id or "").strip()
    state["run_id"] = str(run_id or "").strip()
    state["scope"] = {
        "scope_mode": str(scope.get("scope_mode") or "").strip(),
        "scope_count": scope.get("scope_count"),
        **({"visibility_scope": str(scope.get("visibility_scope") or "").strip()} if scope.get("visibility_scope") else {}),
        "selected_content_ids": list(scope.get("selected_content_ids") or []),
        "selected_count": int(scope.get("selected_count") or 0),
        "available_count": int(scope.get("available_count") or 0),
        "pending_count": int(scope.get("pending_count") or 0),
        "scope_json": _workspace_relative(workspace_root, Path(scope_json), "内容范围") if scope_json else "",
        "confirmed": scope.get("confirmed") is True,
    }
    return state



def redact_url(url: str) -> str:
    parsed = urllib.parse.urlsplit(url)
    query = urllib.parse.parse_qsl(parsed.query, keep_blank_values=True)
    redacted = []
    for key, value in query:
        if key.lower() in SENSITIVE_QUERY_KEYS:
            redacted.append((key, "***"))
        else:
            redacted.append((key, value))
    return urllib.parse.urlunsplit((parsed.scheme, parsed.netloc, parsed.path, urllib.parse.urlencode(redacted), parsed.fragment))


def read_http_error_body(exc: urllib.error.HTTPError) -> str:
    try:
        body = exc.read().decode("utf-8", errors="replace")
    except Exception:  # noqa: BLE001
        return ""
    return body[:800]


def http_get_json(url: str, *, timeout: int = 60, headers: dict[str, str] | None = None) -> dict:
    request = urllib.request.Request(
        url,
        headers=headers or {"User-Agent": "Mozilla/5.0", "Accept": "application/json"},
        method="GET",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body = read_http_error_body(exc)
        raise RuntimeError(f"HTTP {exc.code} {redact_url(url)} body={body or '-'}") from exc
    except urllib.error.URLError as exc:
        code = permission_error_code(exc, "network")
        if code:
            raise RuntimeError(code) from exc
        raise RuntimeError(f"URL error {redact_url(url)} reason={exc.reason}") from exc


def ensure_fieldnames(fieldnames: List[str], required_fields: list[str]) -> List[str]:
    result = list(fieldnames)
    for field in required_fields:
        if field not in result:
            result.append(field)
    return result


def build_row_index(rows: List[Dict[str, str]], key: str) -> Dict[str, Dict[str, str]]:
    index: Dict[str, Dict[str, str]] = {}
    for row in rows:
        value = (row.get(key) or "").strip()
        if value:
            index[value] = row
    return index


def load_detail_csv_tables(
    content_list_path: Path,
    content_library_path: Path,
) -> tuple[
    list[str], list[Dict[str, str]], list[str], list[Dict[str, str]], Dict[Path, bytes]
]:
    """Load the two business tables, preserving a single in-memory table for aliases.

    同时返回两张表「读入这一刻」的原始字节。内存里的行会带着走完页面取数、封面下载、
    转写和生成总结这些耗时步骤，写盘时必须能证明「这张表从读到现在没被别人动过」，
    否则会把窗口内别人的改动整体覆盖掉。
    """
    content_fieldnames, content_rows, content_snapshot = read_csv_table_with_snapshot(
        content_list_path
    )
    if content_list_path == content_library_path:
        # The two business paths are aliases for one file in some real-account
        # layouts. Sharing objects keeps both update paths in the final table.
        return (
            content_fieldnames,
            content_rows,
            content_fieldnames,
            content_rows,
            {content_list_path: content_snapshot},
        )
    library_fieldnames, library_rows, library_snapshot = read_csv_table_with_snapshot(
        content_library_path
    )
    return (
        content_fieldnames,
        content_rows,
        library_fieldnames,
        library_rows,
        {
            content_list_path: content_snapshot,
            content_library_path: library_snapshot,
        },
    )


def build_detail_csv_writes(
    content_list_path: Path,
    content_fieldnames: list[str],
    content_rows: list[Dict[str, str]],
    content_library_path: Path,
    library_fieldnames: list[str],
    library_rows: list[Dict[str, str]],
) -> list[tuple[Path, list[str], list[Dict[str, str]]]]:
    writes = [(content_list_path, content_fieldnames, content_rows)]
    if content_list_path != content_library_path:
        writes.append((content_library_path, library_fieldnames, library_rows))
    return writes


def normalize_requested_ids(value: str) -> List[str]:
    return list(dict.fromkeys(item.strip() for item in value.split(",") if item.strip()))


def validate_requested_account(
    requested_ids: List[str],
    content_index: Dict[str, Dict[str, str]],
    account_id: str,
) -> None:
    expected = account_id.strip()
    if not expected:
        raise ValueError("账号ID不能为空")
    mismatches = []
    for content_id in requested_ids:
        row = content_index.get(content_id)
        if row is None:
            continue
        actual = (row.get("账号ID") or "").strip()
        if actual != expected:
            mismatches.append(f"{content_id}: {actual or '缺失'}")
    if mismatches:
        raise ValueError(
            f"本轮账号ID与内容列表不一致，预期 {expected}，实际 " + "；".join(mismatches)
        )


def build_content_library_key(row: Dict[str, str]) -> str:
    return (row.get("内容ID") or "").strip()


# 发布记录目录名形态：<平台>-<账号ID>-<平台原生内容ID>。
# 内容库按设计只存内容本体、不存平台发布属性，所以「这条平台作品对应库里
# 哪条内容」只能从发布记录目录名上读回来。
PUBLICATION_DIR_PATTERN = re.compile(r"^(?P<platform>[^-]+?)-(?P<account>.+?)-(?P<native>[^-]+)$")
PUBLICATION_PLATFORMS = frozenset({"小红书", "抖音", "B站"})
NATIVE_CONTENT_KEY_PREFIX = "平台原生::"


NATIVE_LINK_PATTERNS = (
    re.compile(r"/(?:explore|discovery/item)/([0-9a-fA-F]{16,})"),  # 小红书
    re.compile(r"/video/(BV[0-9a-zA-Z]{10})"),                      # B站
    re.compile(r"/(?:video|note)/(\d{15,})"),                       # 抖音
)


def content_basics_native_ids(workspace_root: Path | str, row: Dict[str, str]) -> List[str]:
    """从内容目录下 `内容基础结构化数据.json` 的 `请求页面链接` 抽平台原生内容ID。

    发布记录目录只有「生产/复盘后已发布」的内容才有；只取过基础数据、还没发布
    （或发布记录被清过）的内容，原生ID 只留在这个文件里。读不到一律返回空列表。
    """
    raw = (row.get("内容目录路径") or "").strip()
    if not raw:
        return []
    content_dir = Path(raw)
    if not content_dir.is_absolute():
        content_dir = Path(workspace_root) / raw
    basics = content_dir / "内容基础结构化数据.json"
    if not basics.is_file():
        return []
    try:
        data = json.loads(basics.read_text(encoding="utf-8"))
    except Exception:
        return []
    if not isinstance(data, dict):
        return []
    link = str(data.get("请求页面链接") or "")
    if not link:
        return []
    native_ids: List[str] = []
    for pattern in NATIVE_LINK_PATTERNS:
        matched = pattern.search(link)
        if matched:
            native_id = matched.group(1).strip()
            if native_id and native_id not in native_ids:
                native_ids.append(native_id)
    return native_ids


def publication_native_ids(workspace_root: Path | str, row: Dict[str, str]) -> List[str]:
    """该行的平台原生内容ID：发布记录目录名 + 基础结构化数据链接，两处都读。

    只读发布记录会漏掉「没发布过、或发布记录被清理过」的内容 —— 那类只能从
    `内容基础结构化数据.json` 的链接里认。任一处读不到就跳过，不抛错。
    """
    native_ids: List[str] = []
    raw = (row.get("内容目录路径") or "").strip()
    content_dir: Path | None = None
    if raw:
        content_dir = Path(raw)
        if not content_dir.is_absolute():
            content_dir = Path(workspace_root) / raw
    if content_dir is not None:
        publication_root = content_dir / "发布记录"
        if publication_root.is_dir():
            for entry in sorted(publication_root.iterdir()):
                if not entry.is_dir():
                    continue
                matched = PUBLICATION_DIR_PATTERN.match(entry.name)
                if not matched or matched.group("platform") not in PUBLICATION_PLATFORMS:
                    continue
                native_id = matched.group("native").strip()
                if native_id and native_id not in native_ids:
                    native_ids.append(native_id)
    for native_id in content_basics_native_ids(workspace_root, row):
        if native_id not in native_ids:
            native_ids.append(native_id)
    return native_ids
def build_content_library_index(
    rows: List[Dict[str, str]],
    workspace_root: Path | str | None = None,
) -> Dict[str, Dict[str, str]]:
    """内容库行索引：内容ID 之外，把发布记录里的平台原生内容ID 也登记为键。

    传了 workspace_root 才会读发布记录；不传时行为与旧版完全一致。
    """
    index: Dict[str, Dict[str, str]] = {}
    for row in rows:
        key = build_content_library_key(row)
        if key:
            index[key] = row
        if workspace_root is None:
            continue
        for native_id in publication_native_ids(workspace_root, row):
            index.setdefault(NATIVE_CONTENT_KEY_PREFIX + native_id, row)
    return index


def lookup_content_library_row(
    library_index: Dict[str, Dict[str, str]],
    row: Dict[str, str],
) -> Dict[str, str] | None:
    """取内容库对应行：先按内容ID，找不到再按平台原生内容ID。

    第二档是为跨链路归并：同一篇内容由「内容生产/复盘」链路建行、又被
    「账号刷新」链路按平台作品再发现时，两条链路的 内容ID 不同，只有
    平台原生内容ID 是共通的。找不到返回 None，由调用方决定是否新建。
    """
    key = build_content_library_key(row)
    if key and key in library_index:
        return library_index[key]
    native_id = (row.get("平台原生内容ID") or "").strip()
    if not native_id:
        return None
    return library_index.get(NATIVE_CONTENT_KEY_PREFIX + native_id)


def ensure_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)


def output_file_is_valid(path: Path) -> bool:
    return path.exists() and path.is_file() and path.stat().st_size > 0


def print_progress(message: str) -> None:
    print(message, file=sys.stderr, flush=True)


def build_default_report_path(content_list_path: Path) -> Path:
    raise RuntimeError("canonical report path must come from the locked batch context")


def is_benchmark_content_path(*paths: Path | None) -> bool:
    """Identify benchmark account files before task-bound self-account validation."""
    return any(
        path is not None
        and ({"对标资料", "对标账号"} & set(path.resolve().parts))
        for path in paths
    )


def is_self_content_path(*paths: Path | None) -> bool:
    """Identify this workspace's own account files by their formal CSV location."""
    return any(
        path is not None
        and ({"我方资料", "我方账号"} & set(path.resolve().parts))
        for path in paths
    )


def detail_stage_name(content_list_path: Path) -> str:
    """详细数据阶段的阶段名，必须与对应流程文档同名。

    对标链路走 `执行流程/账号详细数据获取流程.md`，阶段名 `账号详细数据获取`；
    我方链路走 `执行流程/我方账号详细数据获取流程.md`，阶段名 `我方账号详细数据获取`。
    此前两个写入点都硬编码成我方名字，导致对标账号的阶段状态写成
    `我方账号详细数据获取`（工作台档案与阶段判断都会看到错名字）。
    """
    return "我方账号详细数据获取" if is_self_content_path(content_list_path) else "账号详细数据获取"


def hydrate_locked_batch_context(
    workspace_root: Path,
    *,
    run_id: str,
    flow_type: str,
    task_id: str,
) -> tuple[str, str, Path, Path, Path, Dict[str, Path]]:
    """Load account/CSV identity for a run-scoped detail action.

    Scope preparation is the first public detail action, so its caller cannot
    rely on a previously confirmed batch context.  The identity comes from this
    run's ``批次上下文.json`` under ``.meiling/staging/<flow>/<run_id>/detail-data/``
    (see ``账号后续详细处理批处理.resolve_batch_context``); this workspace keeps no
    scheduler state tree, and the model never supplies paths.
    """
    from 账号后续详细处理批处理 import resolve_batch_context

    context = resolve_batch_context(workspace_root, run_id, task_id=task_id, flow_type=flow_type)
    resolved_flow = str(context.get("flow_type") or flow_type or "").strip()
    resolved_task = str(context.get("task_id") or task_id or "").strip()
    account_id = str(context.get("account_id") or "").strip()
    content_list = Path(context["content_list"]).resolve()
    content_library = Path(context["content_library"]).resolve()
    paths = build_stage_artifact_paths(
        workspace_root,
        run_id,
        acquisition_attempt=1,
        allow_source_switch=False,
        task_id="",
        flow_type=resolved_flow,
    )
    context_path = paths["batch_context_json"]
    if not context_path.is_file():
        context_path.parent.mkdir(parents=True, exist_ok=True)
        write_state_atomically(
            context_path,
            {
                "schema_version": 1,
                "flow_type": resolved_flow,
                **({"task_id": resolved_task} if resolved_flow == "self_account" and resolved_task else {}),
                "run_id": run_id,
                "account_id": account_id,
                "content_list_path": _workspace_relative(workspace_root, content_list, "内容列表"),
                "content_library_path": _workspace_relative(workspace_root, content_library, "内容库"),
                "data_source": str(context.get("data_source") or "").strip(),
                **({"visibility_scope": str(context.get("scope", {}).get("visibility_scope") or "public").strip()} if resolved_flow == "self_account" else {}),
                "selected_content_ids": [],
                "confirmed": False,
            },
        )
    elif str(context.get("data_source") or "").strip():
        # A context created before source hydration must inherit the base-data
        # handoff once; subsequent actions read this locked value.
        try:
            existing = json.loads(context_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError):
            existing = None
        if isinstance(existing, dict) and not str(existing.get("data_source") or "").strip():
            existing["data_source"] = str(context["data_source"]).strip()
            write_state_atomically(context_path, existing)
    public_task = resolved_task if resolved_flow == "self_account" else ""
    return resolved_flow, public_task, account_id, content_list, content_library, paths


def build_stage_artifact_paths(
    workspace_root: Path,
    run_id: str,
    *,
    acquisition_attempt: int,
    allow_source_switch: bool,
    task_id: str = "",
    flow_type: str = "",
) -> Dict[str, Path]:
    """Resolve run-scoped detail-data artifact paths for one flow.

    Both flows are run-scoped: `<flow-root>/<run_id>/detail-data/`. `task_id` is
    retained only so legacy callers keep working; it never contributes a path
    component any more.
    """
    run_id = str(run_id or "").strip()
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}", run_id):
        raise ValueError("run-id 必须是安全的单段运行标识")
    flow = str(flow_type or "").strip()
    if flow not in {"benchmark_account", "self_account"}:
        flow = "self_account" if str(task_id or "").strip() else "benchmark_account"
    flow_root = "self-account-diagnose" if flow == "self_account" else "benchmark-account-diagnose"
    stage_dir = (
        workspace_root.resolve()
        / ".meiling"
        / "staging"
        / flow_root
        / run_id
        / "detail-data"
    )
    if allow_source_switch:
        suffix = "改源"
        previous_report = stage_dir / "canonical-detail-report.json"
    elif acquisition_attempt == 2:
        suffix = "重试"
        previous_report = stage_dir / "canonical-detail-report.json"
    else:
        suffix = "首次"
        previous_report = None
    paths = {
        "stage_dir": stage_dir,
        "scope_json": stage_dir / "批次上下文.json",
        "batch_context_json": stage_dir / "批次上下文.json",
        "chrome_plan_json": stage_dir / f"Chrome批次计划-{suffix}.json",
        "chrome_template_json": stage_dir / f"Chrome整批结果模板-{suffix}.json",
        "chrome_schema_json": stage_dir / f"Chrome结果.schema-{suffix}.json",
        "chrome_result_json": stage_dir / f"Chrome整批结果-{suffix}.json",
        # There is one formal report for a task/run. Retry and source-switch
        # outputs remain internal checkpoints and must not become new reports.
        "report_json": stage_dir / "canonical-detail-report.json",
    }
    if previous_report is not None:
        paths["previous_report_json"] = previous_report
    return paths


def merge_remark(existing: str, addition: str) -> str:
    current = (existing or "").strip()
    extra = (addition or "").strip()
    if not extra:
        return current
    if not current:
        return extra
    if extra in current:
        return current
    return f"{current}；{extra}"


def strip_remark_prefixes(existing: str, prefixes: list[str]) -> str:
    parts = [part.strip() for part in str(existing or "").split("；") if part.strip()]
    kept = [part for part in parts if not any(part.startswith(prefix) for prefix in prefixes)]
    return "；".join(kept)


def normalize_url(value: Any) -> str:
    text = str(value or "").strip()
    if text.startswith("http://") or text.startswith("https://"):
        return text
    return ""


def is_xiaohongshu_page_url(value: Any) -> bool:
    text = str(value or "").strip()
    if not re.match(r"^https?://", text, flags=re.IGNORECASE):
        return False
    parsed = urllib.parse.urlsplit(text)
    host = (parsed.hostname or "").lower().rstrip(".")
    if host != "xiaohongshu.com" and not host.endswith(".xiaohongshu.com"):
        return False
    return bool(
        re.fullmatch(r"/explore/[0-9A-Za-z_-]+/?", parsed.path)
        or re.fullmatch(r"/discovery/item/[0-9A-Za-z_-]+/?", parsed.path)
        or re.fullmatch(r"/user/profile/[^/?#]+/[0-9A-Za-z_-]+/?", parsed.path)
    )


def row_xiaohongshu_page_url(row: Dict[str, str]) -> str:
    for key in ("对外页面链接", "内容链接", "笔记链接"):
        candidate = normalize_url(row.get(key))
        if is_xiaohongshu_page_url(candidate):
            return candidate
    return ""


def normalize_chrome_identity_url(value: Any) -> str:
    """Normalize a public page URL only for the no-native-ID fallback."""
    raw_url = str(value or "").strip()
    if not re.match(r"^https?://", raw_url, flags=re.IGNORECASE):
        return ""
    parsed = urllib.parse.urlsplit(raw_url)
    host = parsed.netloc.lower()
    path = re.sub(r"/{2,}", "/", parsed.path or "/")
    if path != "/":
        path = path.rstrip("/")
    query = urllib.parse.urlencode(
        sorted(urllib.parse.parse_qsl(parsed.query, keep_blank_values=True))
    )
    return urllib.parse.urlunsplit((parsed.scheme.lower(), host, path, query, ""))


def normalize_content_identity(platform: str, value: object) -> str:
    """按平台统一内容 ID 的书写形式，供身份比对使用。

    B站 `bvid` 在项目内统一大写存储（内容列表与建档执行器同口径），页面地址栏或
    页面内嵌状态里的书写可能带小写，因此比对前两侧都要过这里；其他平台保持原样。
    本函数只用于比对，不改变写入报告或 CSV 的原始值。
    """
    normalized = str(value or "").strip()
    if str(platform or "").strip() == "B站":
        return normalized.upper()
    return normalized


def extract_expected_content_identity(row: Dict[str, str], platform: str, page_url: str) -> str:
    """Use existing source facts or a platform URL; never inspect a live page here."""
    for key in (
        "平台原生内容ID", "内容原生ID", "aweme_id", "video_id", "item_id", "note_id", "bvid", "aid", "cid",
    ):
        value = str(row.get(key) or "").strip()
        if value:
            return value.upper() if platform == "B站" else value
    if platform == "小红书":
        parsed = urllib.parse.urlsplit(page_url)
        match = re.search(r"/(?:explore|discovery/item)/([^/?#]+)", parsed.path)
        if match:
            return match.group(1)
        query = dict(urllib.parse.parse_qsl(parsed.query, keep_blank_values=False))
        return str(query.get("note_id") or query.get("item_id") or "").strip()
    if platform == "抖音":
        match = re.search(r"/(?:video|note)/(\d{15,24})", page_url)
        if match:
            return match.group(1)
        query = dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(page_url).query, keep_blank_values=False))
        return str(query.get("modal_id") or query.get("aweme_id") or query.get("item_id") or "").strip()
    if platform == "B站":
        return extract_bilibili_bvid(page_url).upper()
    return ""


def normalize_timestamp(value: Any) -> str:
    return normalize_publish_time_text(value)


def normalize_datetime_text(value: Any) -> str:
    return normalize_publish_time_text(value)


def normalize_detail_field_value(field: str, value: Any) -> str:
    if field == "发布时间":
        return normalize_datetime_text(value)
    if field in {"视频下载链接", "下载笔记原链接"}:
        return normalize_url(value)
    if field in {"点赞数", "收藏数", "分享数", "评论数"}:
        return count_text(value)
    return str(value or "").strip()


def has_valid_content_evidence(row: Dict[str, str]) -> bool:
    """Use media-specific minimum evidence; title and publish time are optional."""
    content_id = str(row.get("内容ID") or row.get("content_id") or "").strip()
    media_type = str(row.get("媒介形式") or row.get("media_type") or "").strip()
    if not media_type and "媒介形式" not in row and "media_type" not in row:
        media_type = "视频" if normalize_url(row.get("视频下载链接")) else ""
    if not content_id or media_type not in {"视频", "图文"}:
        return False
    if media_type == "视频":
        return bool(
            normalize_url(row.get("视频下载链接"))
            or normalize_url(row.get("下载笔记原链接"))
            or str(row.get("视频文案路径") or "").strip()
        )
    page_or_cover = any(
        normalize_url(row.get(field)) or str(row.get(field) or "").strip()
        for field in ("对外页面链接", "内容链接", "笔记链接", "封面图原链接", "封面图路径")
    )
    interaction_evidence = any(
        row_has_valid_detail_field(row, field) for field in INTERACTION_FIELDS
    )
    return bool(page_or_cover and interaction_evidence)


def content_evidence_labels(row: Dict[str, str]) -> list[str]:
    labels: list[str] = []
    if str(row.get("内容ID") or row.get("content_id") or "").strip():
        labels.append("content_id")
    media_type = str(row.get("媒介形式") or row.get("media_type") or "").strip()
    if str(row.get("标题") or row.get("title") or "").strip():
        labels.append("title")
    if media_type == "图文" and any(
        str(row.get(field) or "").strip()
        for field in ("对外页面链接", "内容链接", "笔记链接", "封面图原链接", "封面图路径")
    ):
        labels.append("page_or_cover")
    if any(row_has_valid_detail_field(row, field) for field in INTERACTION_FIELDS):
        labels.append("interactions")
    if row_has_valid_detail_field(row, "发布时间"):
        labels.append("publish_time")
    if row_has_valid_detail_field(row, "视频下载链接") or row_has_valid_detail_field(row, "下载笔记原链接"):
        labels.append("video_url")
    if str(row.get("正文") or row.get("正文文本") or "").strip():
        labels.append("body")
    return labels


def normalize_empty_interactions(row: Dict[str, str]) -> None:
    """页面已确认内容后，平台未提供的互动数字统一落为字符串 0。

    2026-11-01：互动数以内容列表（创作者中心列表接口）为准。页面上**一个互动数都没拿到**
    时分不清是「平台真的没有」还是「页面这一版没返回」（小红书对 0 值会返回空字符串），
    留空交给上游已有的值，不要把真数据压成 0。
    """
    if not has_valid_content_evidence(row):
        return
    if not any(row_has_valid_detail_field(row, field) for field in INTERACTION_FIELDS):
        return
    for field in INTERACTION_FIELDS:
        if not row_has_valid_detail_field(row, field):
            row[field] = "0"


def row_has_valid_detail_field(row: Dict[str, str], field: str) -> bool:
    return bool(normalize_detail_field_value(field, row.get(field)))


def _publish_time_sort_value(row: Dict[str, str]) -> tuple[int, datetime]:
    normalized = normalize_datetime_text(row.get("发布时间"))
    if normalized:
        try:
            return 1, datetime.strptime(normalized, "%Y/%m/%d %H:%M")
        except ValueError:
            pass
    return 0, datetime.min


DETAIL_SCOPE_CONFIG_KEY = "BENCHMARK_ACCOUNT_DETAIL_LIMIT"
DEFAULT_BENCHMARK_DETAIL_LIMIT = 20
MAX_BENCHMARK_DETAIL_LIMIT = 500
SELF_DETAIL_SCOPE_CONFIG_KEY = "SELF_ACCOUNT_DETAIL_LIMIT"
DEFAULT_SELF_DETAIL_LIMIT = 20
MAX_SELF_DETAIL_LIMIT = 500


def resolve_configured_self_detail_scope(
    workspace_root: Path,
    *,
    scope_mode: str,
    scope_count: int,
    content_ids: str,
) -> tuple[str, int, str]:
    """我的账号默认按最近 20 条；用户本次明确指定则优先。"""
    mode = str(scope_mode or "").strip()
    count = int(scope_count or 0)
    ids = str(content_ids or "").strip()
    if mode or count or ids:
        return mode, count, "explicit"
    values = load_local_config(workspace_root / "本地配置.txt")
    raw = str(values.get(SELF_DETAIL_SCOPE_CONFIG_KEY, "")).strip()
    configured = int(raw) if raw.isdigit() and int(raw) > 0 else DEFAULT_SELF_DETAIL_LIMIT
    return "latest", min(configured, MAX_SELF_DETAIL_LIMIT), "config_default"


def resolve_configured_detail_scope(
    workspace_root: Path,
    *,
    scope_mode: str,
    scope_count: int,
    content_ids: str,
) -> tuple[str, int, str]:
    """本轮没有明确范围时，直接读设置页默认值；用户本次明确指定则优先。"""
    mode = str(scope_mode or "").strip()
    count = int(scope_count or 0)
    ids = str(content_ids or "").strip()
    if mode or count or ids:
        return mode, count, "explicit"
    values = load_local_config(workspace_root / "本地配置.txt")
    raw = str(values.get(DETAIL_SCOPE_CONFIG_KEY, "")).strip()
    configured = int(raw) if raw.isdigit() and int(raw) > 0 else DEFAULT_BENCHMARK_DETAIL_LIMIT
    return "latest", min(configured, MAX_BENCHMARK_DETAIL_LIMIT), "config_default"


def prepare_content_scope(
    rows: List[Dict[str, str]],
    *,
    account_id: str,
    scope_mode: str,
    scope_count: int,
    content_ids: str,
    visibility_scope: str = "",
) -> Dict[str, Any]:
    expected_account_id = str(account_id or "").strip()
    if not expected_account_id:
        raise ValueError("账号ID不能为空")
    if scope_mode not in {"list_order", "latest", "all", "custom"}:
        raise ValueError("范围准备缺少有效的 scope-mode")

    account_rows: List[Dict[str, str]] = []
    seen_ids: set[str] = set()
    duplicate_ids: List[str] = []
    mismatched_ids: List[str] = []
    for row in rows:
        content_id = str(row.get("内容ID") or "").strip()
        if not content_id:
            continue
        actual_account_id = str(row.get("账号ID") or "").strip()
        if actual_account_id != expected_account_id:
            mismatched_ids.append(f"{content_id}: {actual_account_id or '缺失'}")
            continue
        if content_id in seen_ids:
            duplicate_ids.append(content_id)
            continue
        seen_ids.add(content_id)
        account_rows.append(row)

    if mismatched_ids:
        raise ValueError(
            f"内容列表包含其他账号内容，预期 {expected_account_id}，实际 "
            + "；".join(mismatched_ids)
        )
    if duplicate_ids:
        raise ValueError("内容列表存在重复内容ID: " + ",".join(dict.fromkeys(duplicate_ids)))
    if not account_rows:
        raise ValueError("当前账号内容列表中没有有效内容")

    normalized_visibility = str(visibility_scope or "").strip().casefold()
    if normalized_visibility:
        if normalized_visibility not in {"public", "private", "all"}:
            raise ValueError("内容范围缺少有效的 visibility-scope")
        if normalized_visibility != "all":
            expected_status = "公开" if normalized_visibility == "public" else "私密"
            account_rows = [
                row for row in account_rows
                if normalize_public_status(row.get("公开状态")) == expected_status
            ]
            if not account_rows:
                raise ValueError(f"当前账号没有{expected_status}内容")

    ordered_ids = [str(row.get("内容ID") or "").strip() for row in account_rows]
    visible_ids = set(ordered_ids)
    if scope_mode == "custom":
        selected_ids = normalize_requested_ids(content_ids)
        if not selected_ids:
            raise ValueError("custom 模式缺少内容ID")
        unknown_ids = [content_id for content_id in selected_ids if content_id not in visible_ids]
        if unknown_ids:
            label = "当前可见性范围" if normalized_visibility else "当前账号内容列表"
            raise ValueError(f"指定内容ID不属于{label}: " + ",".join(unknown_ids))
    elif scope_mode == "all":
        selected_ids = ordered_ids
    else:
        if scope_count <= 0:
            raise ValueError(f"{scope_mode} 模式的 scope-count 必须大于0")
        if scope_mode == "latest":
            selected_rows = sorted(account_rows, key=_publish_time_sort_value, reverse=True)
            selected_ids = [
                str(row.get("内容ID") or "").strip() for row in selected_rows[:scope_count]
            ]
        else:
            selected_ids = ordered_ids[:scope_count]

    return {
        "account_id": expected_account_id,
        **({"visibility_scope": normalized_visibility} if normalized_visibility else {}),
        "scope_mode": scope_mode,
        "scope_count": scope_count if scope_mode in {"list_order", "latest"} else None,
        "available_count": len(account_rows),
        "pending_count": sum(1 for row in account_rows if content_needs_detail_completion(row)),
        "pending_content_ids": [
            str(row.get("内容ID") or "").strip()
            for row in account_rows
            if content_needs_detail_completion(row)
        ],
        "recent_preview_count": min(20, len(account_rows)),
        "selected_count": len(selected_ids),
        "selected_content_ids": selected_ids,
    }


def iter_values(node: Any) -> Iterable[Any]:
    if isinstance(node, dict):
        for value in node.values():
            yield value
            yield from iter_values(value)
    elif isinstance(node, list):
        for item in node:
            yield item
            yield from iter_values(item)


def find_first_by_keys(node: Any, keys: Iterable[str], *, normalizer=None) -> str:
    """按调用方给定的优先级取值，不依赖字段在 JSON 中的出现顺序。

    与 ``JustOneAPI账号基础抓取执行器.find_first`` 同一套规则：候选名必须是
    有序序列，命中顺序由调用方决定。原实现遍历 ``node.items()``，顺序由接口
    返回的字段顺序决定，``time`` 这类通用名会抢在更准确的字段前面。
    """
    ordered_keys = tuple(keys)
    if isinstance(node, dict):
        for key in ordered_keys:
            if key in node:
                value = node[key]
                normalized = normalizer(value) if normalizer else str(value or "").strip()
                if normalized:
                    return normalized
        for value in node.values():
            result = find_first_by_keys(value, ordered_keys, normalizer=normalizer)
            if result:
                return result
    elif isinstance(node, list):
        for item in node:
            result = find_first_by_keys(item, ordered_keys, normalizer=normalizer)
            if result:
                return result
    return ""


def get_nested(node: Any, path: list[Any]) -> Any:
    current = node
    for part in path:
        if isinstance(part, int):
            if not isinstance(current, list) or len(current) <= part:
                return None
            current = current[part]
        else:
            if not isinstance(current, dict) or part not in current:
                return None
            current = current[part]
    return current


def find_first_by_paths(node: Any, paths: list[list[Any]], *, normalizer=None) -> str:
    for path in paths:
        value = get_nested(node, path)
        if value is None:
            continue
        normalized = normalizer(value) if normalizer else str(value or "").strip()
        if normalized:
            return normalized
    return ""


def extract_xiaohongshu_note_id(note_url: str) -> str:
    match = re.search(
        r"/(?:explore|discovery/item)/([0-9a-zA-Z_-]+)|/user/profile/[^/?#]+/([0-9a-zA-Z_-]+)",
        note_url,
    )
    if not match:
        return ""
    return match.group(1) or match.group(2)


def extract_bilibili_bvid(note_url: str, fallback: str = "") -> str:
    match = re.search(r"(BV[0-9A-Za-z]+)", note_url)
    if match:
        return match.group(1)
    return (fallback or "").strip()


def build_api_url(base_url: str, path: str, params: dict[str, str]) -> str:
    query = urllib.parse.urlencode({key: value for key, value in params.items() if str(value).strip()})
    return f"{base_url.rstrip('/')}{path}?{query}"


def is_retryable_justoneapi_exception(exc: Exception) -> bool:
    message = str(exc)
    status_match = re.match(r"HTTP (\d{3})\b", message)
    if status_match:
        status = int(status_match.group(1))
        return status in {408, 425, 429} or status >= 500
    return message.startswith("URL error ")


def call_justoneapi(
    urls: list[str],
    *,
    max_attempts: int = 1,
    retryable_codes: set[int] | None = None,
    retry_delay_seconds: float = 0.5,
) -> dict:
    errors: list[str] = []
    attempts = max(1, max_attempts)
    retry_codes = retryable_codes or set()
    for url in urls:
        last_error = ""
        for attempt in range(attempts):
            retryable = False
            try:
                response = http_get_json(url)
            except Exception as exc:  # noqa: BLE001
                code = permission_error_code(exc, "network")
                if code:
                    raise RuntimeError(code) from exc
                last_error = str(exc)
                retryable = is_retryable_justoneapi_exception(exc)
            else:
                if response.get("code") == 0 and response.get("data") is not None:
                    return response
                if response.get("success") is True and response.get("data") is not None:
                    return response
                response_code = response.get("code")
                retryable = response_code in retry_codes
                last_error = (
                    f"API failed {redact_url(url)} "
                    f"message={response.get('message') or response.get('msg') or response_code or response}"
                )
            if not retryable or attempt + 1 >= attempts:
                break
            time.sleep(retry_delay_seconds * (2**attempt))
        errors.append(last_error or f"API failed {redact_url(url)}")
    raise RuntimeError("；".join(errors) if errors else "JustOneAPI 接口全部失败")


def normalize_douyin_video_id(value: Any) -> str:
    text = str(value or "").strip()
    return text if re.fullmatch(r"\d{15,24}", text) else ""


def extract_douyin_video_id(note_url: str, row: Dict[str, str]) -> str:
    for key in ("videoId", "video_id", "aweme_id", "item_id", "内容ID"):
        video_id = normalize_douyin_video_id(row.get(key))
        if video_id:
            return video_id
    for pattern in (r"/video/(\d{15,24})", r"[?&](?:modal_id|aweme_id|item_id)=(\d{15,24})"):
        match = re.search(pattern, note_url)
        if match:
            return match.group(1)
    return ""


def fetch_xiaohongshu_detail(base_url: str, token: str, row: Dict[str, str]) -> dict:
    note_url = row_xiaohongshu_page_url(row)
    note_id = extract_xiaohongshu_note_id(note_url)
    if not note_id:
        for key in ("平台原生内容ID", "内容原生ID", "note_id", "noteId"):
            value = str(row.get(key) or "").strip()
            if value:
                note_id = value
                break
    if not note_id:
        raise RuntimeError("小红书 noteId 缺失")
    urls = []
    for path in [
        "/api/xiaohongshu/get-note-detail/v5",
        "/api/xiaohongshu/get-note-detail/v4",
        "/api/xiaohongshu/get-note-detail/v2",
        "/api/xiaohongshu/get-note-detail/v1",
    ]:
        urls.append(
            build_api_url(
                base_url,
                path,
                {
                    "token": token,
                    "noteId": note_id,
                    "note_id": note_id,
                    "url": note_url,
                },
            )
        )
    return call_justoneapi(urls)


def fetch_douyin_detail(base_url: str, token: str, row: Dict[str, str]) -> dict:
    note_url = (row.get("对外页面链接") or row.get("内容链接") or row.get("笔记链接") or "").strip()
    video_id = extract_douyin_video_id(note_url, row)
    if not video_id:
        if not note_url:
            raise RuntimeError("抖音内容缺少对外页面链接和 videoId")
        transfer_url = build_api_url(
            base_url,
            "/api/douyin/share-url-transfer/v1",
            {"token": token, "shareUrl": note_url},
        )
        transfer_data = call_justoneapi(
            [transfer_url],
            max_attempts=3,
            retryable_codes={301},
        )
        video_id = (
            find_first_by_keys(transfer_data, ("aweme_id", "item_id", "video_id", "videoId"), normalizer=str)
            or ""
        ).strip()
    if not video_id:
        raise RuntimeError("抖音 videoId 缺失")
    detail_url = build_api_url(
        base_url,
        "/api/douyin/get-video-detail/v2",
        {
            "token": token,
            "videoId": video_id,
            "video_id": video_id,
            "aweme_id": video_id,
            "item_id": video_id,
        },
    )
    return call_justoneapi([detail_url])


def fetch_bilibili_detail(base_url: str, token: str, row: Dict[str, str]) -> dict:
    note_url = (row.get("对外页面链接") or row.get("内容链接") or row.get("笔记链接") or "").strip()
    bvid = extract_bilibili_bvid(note_url, row.get("bvid", ""))
    if not bvid:
        raise RuntimeError("B站 bvid 缺失")
    detail_url = build_api_url(
        base_url,
        "/api/bilibili/get-video-detail/v2",
        {
            "token": token,
            "bvid": bvid,
            "aid": (row.get("aid") or "").strip(),
            "cid": (row.get("cid") or "").strip(),
        },
    )
    return call_justoneapi([detail_url])


def extract_xiaohongshu_fields(response: dict) -> dict[str, str]:
    payload = response.get("data") or response
    fields = {
        "发布时间": find_first_by_keys(payload, ("publish_time", "create_time", "time"), normalizer=normalize_timestamp),
        "视频下载链接": find_first_by_paths(
            payload,
            [
                ["note", "video", "media", "stream", "h264", 0, "master_url"],
                ["note", "video", "master_url"],
                ["note", "video", "video_url"],
                ["note", "video", "consumer", "origin_video_key"],
                ["video", "media", "stream", "h264", 0, "master_url"],
                ["video", "master_url"],
                ["video", "video_url"],
                ["video", "consumer", "origin_video_key"],
            ],
            normalizer=normalize_url,
        ),
        "收藏数": find_first_by_keys(payload, ("collected_count", "collectedCount"), normalizer=count_text),
        "分享数": find_first_by_keys(payload, ("share_count", "shareCount"), normalizer=count_text),
        "评论数": find_first_by_keys(payload, ("comment_count", "commentCount"), normalizer=count_text),
        "点赞数": find_first_by_keys(payload, ("liked_count", "likedCount"), normalizer=count_text),
    }
    return fields


def extract_douyin_fields(response: dict) -> dict[str, str]:
    payload = response.get("data") or response
    download_url = find_first_by_paths(
        payload,
        [
            ["video", "play_addr", "url_list", 0],
            ["video", "download_addr", "url_list", 0],
            ["video", "play_addr", "url"],
            ["video", "download_addr", "url"],
            ["aweme_detail", "video", "play_addr", "url_list", 0],
            ["aweme_detail", "video", "download_addr", "url_list", 0],
            ["aweme_detail", "video", "play_addr", "url"],
            ["aweme_detail", "video", "download_addr", "url"],
        ],
        normalizer=normalize_url,
    )
    return {
        "发布时间": find_first_by_keys(payload, ("create_time", "publish_time"), normalizer=normalize_timestamp),
        "视频下载链接": download_url,
        "收藏数": find_first_by_keys(payload, ("collect_count", "collectCount"), normalizer=count_text),
        "分享数": find_first_by_keys(payload, ("share_count", "shareCount"), normalizer=count_text),
        "评论数": find_first_by_keys(payload, ("comment_count", "commentCount"), normalizer=count_text),
        "点赞数": find_first_by_keys(payload, ("digg_count", "diggCount"), normalizer=count_text),
    }


def extract_bilibili_fields(response: dict) -> dict[str, str]:
    payload = response.get("data") or response
    video_data = payload.get("videoData") or payload.get("video_data") or {}
    stat = video_data.get("stat") or {}
    dash_info = payload.get("dash_info") or payload.get("dashInfo") or {}
    return {
        "发布时间": normalize_timestamp(video_data.get("pubdate")),
        "视频下载链接": find_first_by_paths(
            dash_info,
            [
                ["dash", "video", 0, "base_url"],
                ["dash", "video", 0, "baseUrl"],
                ["dash", "video", 0, "backup_url"],
                ["dash", "video", 0, "backupUrl"],
            ],
            normalizer=normalize_url,
        ),
        "收藏数": count_text(stat.get("favorite")),
        "分享数": count_text(stat.get("share")),
        "评论数": count_text(stat.get("reply")),
        "点赞数": count_text(stat.get("like")),
    }


def normalize_detail_fields(detail_node: Dict[str, Any]) -> dict[str, str]:
    def first_present(*keys: str) -> Any:
        for key in keys:
            if key in detail_node and detail_node[key] not in (None, ""):
                return detail_node[key]
        return ""

    return {
        "封面图原链接": normalize_url(
            first_present("封面图原链接", "cover_url", "cover", "coverUrl")
        ),
        "媒介形式": normalize_media_type_from_detail(
            first_present("媒介形式", "media_type", "note_type", "type")
        ),
        "发布时间": normalize_datetime_text(
            first_present("发布时间", "publish_time", "create_time", "time")
        ),
        "视频下载链接": normalize_url(
            first_present("视频下载链接", "下载笔记原链接", "video_url", "master_url")
        ),
        "收藏数": count_text(first_present("收藏数", "collected_count", "collect_count")),
        "分享数": count_text(first_present("分享数", "share_count")),
        "评论数": count_text(first_present("评论数", "comment_count")),
        "点赞数": count_text(first_present("点赞数", "liked_count", "digg_count")),
        "正文文本": str(first_present("正文文本", "字幕文本", "caption_text", "transcript", "text") or "").strip(),
        "正文获取状态": str(first_present("正文获取状态", "字幕状态", "caption_status") or "").strip(),
    }


def normalize_media_type_from_detail(value: Any) -> str:
    marker = re.sub(r"[\s_-]+", "", str(value or "").strip().lower())
    if marker in {"video", "videonote", "movie", "视频"}:
        return "视频"
    if marker in {"normal", "image", "images", "text", "note", "图文"}:
        return "图文"
    return ""


def fetch_detail_fields(platform: str, base_url: str, token: str, row: Dict[str, str]) -> dict[str, str]:
    if platform == "小红书":
        return extract_xiaohongshu_fields(fetch_xiaohongshu_detail(base_url, token, row))
    if platform == "抖音":
        return extract_douyin_fields(fetch_douyin_detail(base_url, token, row))
    if platform == "B站":
        return extract_bilibili_fields(fetch_bilibili_detail(base_url, token, row))
    raise RuntimeError(f"当前平台暂不支持明细补全: {platform}")


def build_structured_detail_path(content_dir: Path) -> Path:
    return content_dir / STRUCTURED_DETAIL_FILE_NAME


def read_structured_detail_fields(content_dir: Path) -> tuple[dict[str, str], str]:
    candidate_path = content_dir / STRUCTURED_DETAIL_FILE_NAME
    if not candidate_path.exists():
        return {}, ""
    try:
        payload = json.loads(candidate_path.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001
        code = permission_error_code(exc, "filesystem")
        if code:
            raise RuntimeError(code) from exc
        return {}, ""
    detail_node = payload.get("fields") if isinstance(payload, dict) and isinstance(payload.get("fields"), dict) else payload
    if not isinstance(detail_node, dict):
        return {}, ""
    fields = normalize_detail_fields(detail_node)
    source = str(
        detail_node.get("基础数据获取方式")
        or payload.get("基础数据获取方式", "")
        or payload.get("source", "")
    ).strip()
    return fields, source


def read_structured_detail_status(content_dir: Path) -> str:
    candidate_path = content_dir / STRUCTURED_DETAIL_FILE_NAME
    if not candidate_path.exists():
        return ""
    try:
        payload = json.loads(candidate_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return ""
    if not isinstance(payload, dict):
        return ""
    return str(payload.get("状态") or payload.get("status") or "").strip()


def select_structured_fetch_command(explicit_command: str, _env_values: dict[str, str] | None = None) -> str:
    return str(explicit_command or "").strip()


def is_mcp_tool_command(command: List[str]) -> bool:
    if not command:
        return False
    if Path(command[0]).name.casefold().startswith("mcp__"):
        return True
    tokens = [re.sub(r"[^a-z0-9_]+", "", token.casefold()) for token in command]
    if "chrome_devtools" in tokens:
        return True
    if "chromedevtoolsmcp" in tokens:
        return True
    return any(tokens[index : index + 3] == ["chrome", "devtools", "mcp"] for index in range(len(tokens) - 2))


def resolve_structured_cli_adapter(command: List[str]) -> List[str]:
    if not command:
        raise RuntimeError("CLI 适配器命令为空")
    if is_mcp_tool_command(command):
        raise RuntimeError("MCP 工具名或调用描述不能作为 CLI 适配器执行")

    resolved_command = list(command)
    executable = resolved_command[0]
    executable_path = Path(executable).expanduser()
    if any(separator in executable for separator in ("/", "\\")) and not executable_path.is_absolute():
        raise RuntimeError("CLI 适配器使用路径时必须传入可执行文件的绝对路径")
    resolved_executable = shutil.which(str(executable_path) if executable_path.is_absolute() else executable)
    if not resolved_executable:
        raise RuntimeError(f"CLI 适配器未安装或不可执行: {executable}")
    resolved_command[0] = resolved_executable
    return resolved_command


def preflight_structured_cli_template(command_template: str) -> List[str]:
    try:
        command = shlex.split(command_template)
    except ValueError as exc:
        raise RuntimeError(f"CLI 适配器模板无法解析: {exc}") from exc
    if not command:
        raise RuntimeError("CLI 适配器命令为空")
    if "{" in command[0] or "}" in command[0]:
        raise RuntimeError("CLI 适配器可执行文件不得包含占位符")

    formatter = string.Formatter()
    sentinel_values = {field: f"__{field}__" for field in STRUCTURED_CLI_TEMPLATE_FIELDS}
    for argument in command[1:]:
        try:
            parsed_fields = list(formatter.parse(argument))
        except ValueError as exc:
            raise RuntimeError(f"CLI 适配器模板无法解析: {exc}") from exc
        for _, field_name, format_spec, conversion in parsed_fields:
            if field_name is None:
                continue
            if field_name not in STRUCTURED_CLI_TEMPLATE_FIELDS:
                raise RuntimeError(f"CLI 适配器模板字段不受支持: {field_name}")
            if conversion is not None:
                raise RuntimeError(f"CLI 适配器模板字段禁止 conversion: {field_name}")
            if format_spec:
                raise RuntimeError(f"CLI 适配器模板字段禁止 format_spec: {field_name}")
        try:
            formatter.vformat(argument, (), sentinel_values)
        except (AttributeError, IndexError, KeyError, ValueError) as exc:
            raise RuntimeError(f"CLI 适配器模板格式化校验失败: {exc}") from exc
    return resolve_structured_cli_adapter(command)


def format_preflighted_structured_cli_command(command_template: List[str], variables: Dict[str, str]) -> List[str]:
    try:
        return [command_template[0], *(argument.format(**variables) for argument in command_template[1:])]
    except (KeyError, ValueError) as exc:
        raise RuntimeError(f"CLI 适配器模板格式化失败: {exc}") from exc


def run_structured_fetch_command(
    command_template: List[str],
    *,
    row: Dict[str, str],
    content_id: str,
    title: str,
    content_dir: Path,
    output_path: Path,
    workspace_root: Path,
) -> tuple[dict[str, str], str]:
    note_url = (row.get("对外页面链接") or row.get("内容链接") or row.get("笔记链接") or "").strip()
    variables = {
        "content_id": content_id,
        "title": title,
        "platform": (row.get("平台") or "").strip(),
        "account_id": (row.get("账号ID") or "").strip(),
        "account_name": (row.get("账号名") or row.get("账号名称") or row.get("账号昵称") or "").strip(),
        "note_url": note_url,
        "content_dir": str(content_dir),
        "output_path": str(output_path),
        "workspace_root": str(workspace_root),
        "fetch_method": "chrome_devtools",
    }
    command = format_preflighted_structured_cli_command(command_template, variables)
    try:
        result = subprocess.run(
            command,
            cwd=str(workspace_root),
            text=True,
            capture_output=True,
            check=False,
        )
    except OSError as exc:
        code = permission_error_code(exc, "process")
        if code:
            raise RuntimeError(code) from exc
        raise
    if result.returncode != 0:
        reason = (result.stderr or result.stdout or "").strip()
        raise RuntimeError(reason or "结构化 CLI 适配器执行失败")
    if not output_file_is_valid(output_path):
        stdout = (result.stdout or "").strip()
        if not stdout:
            raise RuntimeError("结构化 CLI 适配器未生成输出文件，也未返回 JSON")
        try:
            payload = json.loads(stdout)
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"结构化 CLI 适配器 stdout 不是有效 JSON: {exc}") from exc
        if not isinstance(payload, dict):
            raise RuntimeError("结构化 CLI 适配器 stdout 必须是 JSON 对象")
        write_json_atomically(output_path, payload)
    fields, source = read_structured_detail_fields(content_dir)
    if not has_structured_detail_payload(fields):
        raise RuntimeError("结构化 CLI 适配器结果缺少可用明细字段")
    return fields, source or "CLI适配器"


def has_structured_detail_payload(fields: dict[str, str]) -> bool:
    return any(
        (fields.get(field) or "").strip()
        for field in STRUCTURED_CONTEXT_FIELDS + DETAIL_FIELDS + OPTIONAL_DETAIL_FIELDS + ["正文文本", "正文获取状态"]
    )


def merge_detail_fields(row: Dict[str, str], fields: dict[str, str], *, overwrite: bool = True) -> None:
    cover_url = (fields.get("封面图原链接") or "").strip()
    if cover_url and (overwrite or not (row.get("封面图原链接") or "").strip()):
        row["封面图原链接"] = cover_url
    media_type = (fields.get("媒介形式") or "").strip()
    current_media_type = (row.get("媒介形式") or "").strip()
    if media_type and (
        overwrite
        or not current_media_type
        or current_media_type in {"未知", "其他"}
        or (media_type == "视频" and current_media_type != "视频")
    ):
        row["媒介形式"] = media_type
    if fields.get("点赞数") and (overwrite or not row_has_valid_detail_field(row, "点赞数")):
        row["点赞数"] = fields["点赞数"]
    for field in DETAIL_FIELDS:
        if fields.get(field) and (overwrite or not row_has_valid_detail_field(row, field)):
            row[field] = fields[field]
    for field in ("正文文本", "正文获取状态"):
        if fields.get(field) and (overwrite or not str(row.get(field) or "").strip()):
            row[field] = fields[field]


def merge_official_detail_fields(row: Dict[str, str], library_row: Dict[str, str] | None) -> list[str]:
    sources: list[str] = []
    for source_row in [row, library_row]:
        if not source_row:
            continue
        fields = normalize_detail_fields(source_row)
        if not has_structured_detail_payload(fields):
            continue
        merge_detail_fields(row, fields, overwrite=False)
        source = (source_row.get("基础数据获取方式") or "").strip()
        if source:
            sources.append(source)
    return sources


def sync_content_library_row(
    library_rows: List[Dict[str, str]],
    library_index: Dict[str, Dict[str, str]],
    content_row: Dict[str, str],
    library_fieldnames: List[str],
    *,
    clear_fields: Iterable[str] = (),
    own_content_library: bool = False,
) -> None:
    content_id = (content_row.get("内容ID") or "").strip()
    library_key = build_content_library_key(content_row)
    if not (content_id and library_key):
        return
    library_row = lookup_content_library_row(library_index, content_row)
    if library_row is None:
        library_row = {field: "" for field in library_fieldnames}
        library_rows.append(library_row)
        library_index[library_key] = library_row
    owned_fields = OWN_CONTENT_LIBRARY_OWNED_FIELDS if own_content_library else CONTENT_LIBRARY_OWNED_FIELDS
    fill_only_fields = (
        OWN_CONTENT_LIBRARY_FILL_ONLY_FIELDS if own_content_library else CONTENT_LIBRARY_FILL_ONLY_FIELDS
    )
    effective_clear_fields = (
        set(clear_fields) & owned_fields if own_content_library else clear_fields
    )
    sync_owned_fields(
        library_row,
        content_row,
        library_fieldnames,
        owned_fields=owned_fields,
        field_aliases=CONTENT_LIBRARY_FIELD_ALIASES,
        fill_only_fields=fill_only_fields,
        clear_fields=effective_clear_fields,
    )
    if "当前状态" in library_fieldnames:
        current_status = (library_row.get("当前状态") or "").strip()
        if own_content_library:
            if current_status in {"", "待发布", "已发布", "已复盘"}:
                library_row["当前状态"] = "已完成"
        elif current_status != "已复盘":
            library_row["当前状态"] = "已发布"
        if not (library_row.get("当前状态") or "").strip():
            raise ValueError("内容库写入后当前状态不得为空")


def resolve_content_root(workspace_root: Path, content_library_path: Path) -> Path:
    parts = content_library_path.parts
    if "我方资料" in parts:
        return workspace_root / "我方资料" / "我方内容"
    return workspace_root / "对标资料" / "对标内容"


def resolve_existing_content_dir(
    workspace_root: Path,
    content_root: Path,
    *rows: Dict[str, str] | None,
) -> Path | None:
    return resolve_safe_existing_content_dir(
        workspace_root=workspace_root,
        content_root=content_root,
        rows=rows,
        path_fields=CONTENT_DIR_PATH_FIELDS,
    )


def build_item_result(
    *,
    account_id: str,
    content_id: str,
    title: str,
    content_dir: Path,
    process_dir: Path,
    before_row: Dict[str, str],
    after_row: Dict[str, str],
    failures: List[str],
    errors: List[Dict[str, Any]] | None = None,
) -> Dict[str, Any]:
    required_fields = required_detail_fields(after_row)
    valid_evidence = has_valid_content_evidence(after_row)
    filled_fields = []
    for field in required_fields:
        if not row_has_valid_detail_field(before_row, field) and row_has_valid_detail_field(after_row, field):
            filled_fields.append(field)
    normalized_errors = [item for item in (errors or []) if isinstance(item, dict)]
    blocking = has_blocking_errors(normalized_errors)
    if valid_evidence and not blocking:
        status = "成功"
    elif filled_fields:
        status = "部分成功"
    else:
        status = "失败"
    missing_deliverables: List[str] = []
    if valid_evidence:
        if not row_has_valid_detail_field(after_row, "封面图路径"):
            missing_deliverables.append("封面")
        if str(after_row.get("媒介形式") or "").strip() == "视频":
            if not row_has_valid_detail_field(after_row, "视频文案路径"):
                missing_deliverables.append("视频文案")
    result = {
        "account_id": account_id,
        "content_id": content_id,
        "title": title,
        "status": status,
        # Canonical stage status is derived downstream from these immutable facts.
        # Do not persist parallel execution/delivery/evidence status fields.
        "missing_deliverables": missing_deliverables,
        "content_status": "success" if valid_evidence else "failed",
        "valid_evidence": content_evidence_labels(after_row),
        "retryable": any(item.get("retryable") is True for item in normalized_errors),
        "metrics": {
            field: (
                normalize_detail_field_value(field, after_row.get(field)) or "0"
                if valid_evidence
                else normalize_detail_field_value(field, after_row.get(field))
            )
            for field in INTERACTION_FIELDS
        },
        "filled_fields": filled_fields,
        # 部分成功仍保留逐字段缺口，供后续报告展示；缺口本身不等于整条失败。
        "missing_fields": [field for field in required_fields if not row_has_valid_detail_field(after_row, field)],
        "content_dir": str(content_dir),
        "process_dir": str(process_dir),
        "result_json": str(process_dir / "明细补全结果.json"),
        "failures": failures,
        "errors": normalized_errors,
        "blocking_errors": [item for item in normalized_errors if item.get("blocking") is True],
        "base_data_available": bool(valid_evidence and not blocking),
    }
    return result


NON_BLOCKING_FIELD_GAP_LABELS = {
    "title_unavailable": "标题",
    "publish_time_unavailable": "发布时间",
}


def non_blocking_field_gaps(item: Mapping[str, Any]) -> List[str]:
    """Return human-readable labels for non-blocking per-field gaps.

    A missing title or publish time is deliberately non-blocking, so it never
    reaches ``blocking_errors``.  Without this projection it also never reaches
    the canonical report, and downstream stages silently read the content as
    complete — which contradicts the documented promise that these gaps are
    carried forward through ``missing_deliverables``.
    """
    labels: List[str] = []
    for error in item.get("errors") or []:
        if not isinstance(error, dict) or error.get("blocking") is True:
            continue
        label = NON_BLOCKING_FIELD_GAP_LABELS.get(str(error.get("code") or "").strip())
        if label:
            labels.append(label)
    return labels


def _text_labels(values: Any) -> List[str]:
    """Keep only non-empty string labels; drop anything else silently."""
    if not isinstance(values, (list, tuple)):
        return []
    return [value.strip() for value in values if isinstance(value, str) and value.strip()]


def missing_gap_labels(item: Mapping[str, Any]) -> List[str]:
    """Return every gap label for one content, in the canonical fact vocabulary.

    The canonical per-item fact and the top-level summary must be built from the
    same three sources.  When they drift, the report shows a gap per item while
    the summary reads "nothing missing" — the two halves of one report disagree.
    """
    return list(dict.fromkeys([
        *_text_labels(item.get("missing_deliverables")),
        *_text_labels(item.get("missing_fields")),
        *non_blocking_field_gaps(item),
    ]))


def canonical_facts_from_items(items: Iterable[Any]) -> List[Dict[str, Any]]:
    """Project per-item detail results into canonical single-item facts.

    逐条明细结果（`build_item_result()` 产物）带 `valid_evidence` / `errors` /
    `missing_fields` 等状态副本字段，canonical 事实只接受 content_id /
    base_data_available / missing_deliverables / blocking_errors，多一个都会被
    `详细数据阶段合同._normalize_fact()` 拒。

    **可用性只认 `base_data_available`，不要再读 `valid_evidence`。** 重试合并
    （`merge_retry_report()`）会把上一份 canonical report 的既存事实并回来，那些
    事实是规范化过的、**没有 `valid_evidence`**；若在这里按 `valid_evidence`
    判断，上一轮已经成取得的内容会被整批判成未取得（2026-09-19 实测）。
    """
    facts: List[Dict[str, Any]] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        content_id = str(item.get("content_id") or "").strip()
        if not content_id:
            continue
        facts.append(
            {
                "content_id": content_id,
                "base_data_available": bool(item.get("base_data_available")),
                "missing_deliverables": missing_gap_labels(item),
                "blocking_errors": [
                    error
                    for error in (item.get("blocking_errors") or [])
                    if isinstance(error, dict)
                ],
            }
        )
    return facts


def missing_deliverable_summaries(items: Iterable[Any]) -> List[str]:
    """Render gaps as sentences a person can read without knowing content IDs.

    Example: ``["「AI帮我7天涨了1.5w粉！」缺视频文案"]``.  Internal content IDs
    are machine identifiers that a reader cannot map back to a piece of content,
    so the title is the display name; the ID is only a fallback when no title is
    available.
    """
    summaries: List[str] = []
    for item in items:
        if not isinstance(item, Mapping):
            continue
        labels = missing_gap_labels(item)
        if not labels:
            continue
        display_name = str(item.get("title") or "").strip() or str(item.get("content_id") or "").strip()
        if not display_name:
            continue
        summaries.append(f"「{display_name}」缺{'、'.join(labels)}")
    return summaries


def required_detail_fields(row: Dict[str, str]) -> List[str]:
    media_type = str(row.get("媒介形式") or "").strip()
    if media_type == "视频":
        return ["发布时间", "视频下载链接", "收藏数", "分享数", "评论数"]
    if media_type == "图文":
        # Title and publish time are intentionally absent: they are non-blocking gaps.
        return ["点赞数", "收藏数", "分享数", "评论数"]
    return []


def content_needs_detail_completion(row: Dict[str, str]) -> bool:
    """Return True when a row still lacks at least one completable detail field.

    This is the machine definition of 「待处理内容」 shown in the scope prompt:
    only contents that detail collection can still complete count.  Non-blocking
    gaps such as a missing 图文 title are excluded, because no data source can
    fill them and counting them would make the number permanent noise.
    """
    return any(
        not row_has_valid_detail_field(row, field)
        for field in required_detail_fields(row)
    )


def can_calculate_derived(row: Dict[str, str]) -> bool:
    return all(
        row_has_valid_detail_field(row, field)
        for field in ["发布时间", "点赞数", "收藏数", "评论数"]
    )


def derived_content_ids_from_report(
    canonical: Dict[str, Any], requested_ids: List[str]
) -> List[str]:
    """Resolve the derived-field scope without relying on a transient field.

    Older canonical reports do not persist ``eligible_for_derived_content_ids``.
    In that case, processed content IDs are the durable scope boundary; the
    derived-field executor still validates each row and records any gaps.
    """
    if "eligible_for_derived_content_ids" in canonical:
        candidates = canonical.get("eligible_for_derived_content_ids") or []
    else:
        candidates = canonical.get("processed_content_ids") or []
        if not candidates:
            collection = canonical.get("detail_collection")
            items = collection.get("items") if isinstance(collection, dict) else []
            candidates = [
                item.get("content_id")
                for item in items
                if isinstance(item, dict) and item.get("base_data_available")
            ]
    return ordered_ids(requested_ids, candidates)


def can_enter_followup(row: Dict[str, str]) -> bool:
    media_type = str(row.get("媒介形式") or "").strip()
    if media_type == "视频":
        return bool(normalize_url(row.get("视频下载链接")))
    if media_type == "图文":
        return True
    return False


def resolve_workspace_json_path(workspace_root: Path, raw_path: str, label: str) -> Path:
    text = str(raw_path or "").strip()
    if not text:
        raise ValueError(f"{label}缺失")
    candidate = Path(text).expanduser()
    resolved = candidate.resolve() if candidate.is_absolute() else (workspace_root / candidate).resolve()
    try:
        resolved.relative_to(workspace_root.resolve())
    except ValueError as exc:
        raise ValueError(f"{label}超出当前工作区: {text}") from exc
    if resolved.suffix.lower() != ".json":
        raise ValueError(f"{label}必须是 JSON 文件: {text}")
    return resolved


def validate_xiaohongshu_raw_detail(
    payload: bytes,
    *,
    expected_native_content_id: str,
) -> tuple[bool, str, str]:
    """Validate a staged XHS detail without interpreting page HTML/scripts.

    The raw response is deliberately treated as data only.  We accept the
    documented ``note.note`` object shape and require its native note ID to
    match the locked target.  Invalid JSON, HTML, unknown shapes, and identity
    mismatches all use the same stable ``raw_detail_unavailable`` code so the
    caller can continue with the normal ASR fallback.
    """
    try:
        text = payload.decode("utf-8-sig")
    except UnicodeDecodeError:
        return False, "raw_detail_unavailable", "小红书原始详情不是 UTF-8 JSON"
    if not text.strip():
        return False, "raw_detail_unavailable", "小红书原始详情为空"
    # A document response is never a raw detail candidate.  Do not extract or
    # execute inline state such as window.__INITIAL_STATE__.
    marker = text.lstrip().casefold()
    if marker.startswith(("<!doctype", "<html", "<script")) or "window.__initial_state__" in marker:
        return False, "raw_detail_unavailable", "小红书原始详情是 HTML 文档"
    try:
        decoded = json.loads(text)
    except json.JSONDecodeError:
        return False, "raw_detail_unavailable", "小红书原始详情不是合法 JSON"
    if not isinstance(decoded, dict):
        return False, "raw_detail_unavailable", "小红书原始详情根节点必须是 JSON 对象"

    note_node: dict[str, object] | None = None
    candidates: list[object] = [decoded.get("note"), decoded.get("data"), decoded]
    for candidate in candidates:
        if not isinstance(candidate, dict):
            continue
        nested = candidate.get("note")
        if isinstance(nested, dict):
            note_node = nested
            break
        if any(key in candidate for key in ("noteId", "note_id", "currentNoteId")):
            note_node = candidate
            break
    if note_node is None:
        return False, "raw_detail_unavailable", "小红书原始详情结构不是已知 note 对象"
    observed = ""
    for node in (decoded, note_node):
        if not isinstance(node, dict):
            continue
        for key in ("currentNoteId", "noteId", "note_id", "id"):
            value = str(node.get(key) or "").strip()
            if value:
                observed = value
                break
        if observed:
            break
    expected = str(expected_native_content_id or "").strip()
    if not observed or not expected or observed != expected:
        return False, "raw_detail_unavailable", "小红书原始详情内容 ID 与目标不一致"
    return True, "", ""


def build_chrome_batch_targets(
    *,
    workspace_root: Path,
    content_list_path: Path,
    content_library_path: Path,
    content_index: Dict[str, Dict[str, str]],
    library_index: Dict[str, Dict[str, str]],
    requested_ids: List[str],
    account_id: str,
    raw_detail_dir: Path | None = None,
) -> List[Dict[str, object]]:
    targets: List[Dict[str, object]] = []
    content_root = resolve_content_root(workspace_root, content_library_path)
    for target_index, content_id in enumerate(requested_ids, start=1):
        row = content_index.get(content_id)
        if row is None:
            raise ValueError(f"Chrome 目标内容ID不在内容列表中: {content_id}")
        platform = (row.get("平台") or "").strip()
        row_account_id = (row.get("账号ID") or "").strip()
        url = (
            row.get("对外页面链接")
            or row.get("内容链接")
            or row.get("笔记链接")
            or ""
        ).strip()
        if not platform:
            raise ValueError(f"Chrome 目标缺少平台: {content_id}")
        if row_account_id != account_id.strip():
            raise ValueError(f"Chrome 目标账号不一致: {content_id}")
        parsed_url = urllib.parse.urlsplit(url)
        if parsed_url.scheme not in {"http", "https"} or not parsed_url.netloc:
            raise ValueError(f"Chrome 目标缺少有效页面链接: {content_id}")

        title = (row.get("标题") or "").strip()
        library_row = lookup_content_library_row(library_index, row)
        content_dir = resolve_existing_content_dir(
            workspace_root, content_root, row, library_row
        )
        if content_dir is None:
            content_dir = content_root / build_content_dir_name(content_library_path, row, title)
        output_json = build_structured_detail_path(content_dir).resolve()
        try:
            output_json.relative_to(workspace_root.resolve())
        except ValueError as exc:
            raise ValueError(f"Chrome 目标输出路径超出当前工作区: {content_id}") from exc
        target = {
                "target_index": target_index,
                "content_id": content_id,
                "title": title,
                "platform": platform,
                "account_id": row_account_id,
                "url": url,
                "expected_content_identity": extract_expected_content_identity(row, platform, url),
                "output_json": str(output_json),
        }
        if raw_detail_dir is not None and platform == "小红书":
            raw_path = (raw_detail_dir / f"{content_id}.json").resolve()
            binding_path = (raw_detail_dir / f"{content_id}.binding.json").resolve()
            raw_path.relative_to(workspace_root.resolve())
            binding_path.relative_to(workspace_root.resolve())
            target.update({"raw_detail_json": str(raw_path), "raw_detail_binding_json": str(binding_path)})
        targets.append(target)
    return targets


def prepare_chrome_batch(
    *,
    workspace_root: Path,
    plan_path: Path,
    result_path: Path,
    account_id: str,
    content_list_path: Path,
    content_library_path: Path,
    requested_ids: List[str],
    targets: List[Dict[str, object]],
    template_path: Path | None = None,
    schema_path: Path | None = None,
) -> Dict[str, object]:
    template_path = template_path or result_path.with_name(f"{result_path.stem}-模板{result_path.suffix}")
    schema_path = schema_path or result_path.with_name(f"{result_path.stem}.schema{result_path.suffix}")
    if len({plan_path, result_path, template_path, schema_path}) != 4:
        raise ValueError("Chrome 计划、模板、schema 和结果必须使用不同文件")
    for target in targets:
        raw_detail_json = str(target.get("raw_detail_json") or "").strip()
        if raw_detail_json:
            raw_detail_path = resolve_workspace_json_path(
                workspace_root, raw_detail_json, "Chrome 原始详情"
            )
            if not _workspace_relative(workspace_root, raw_detail_path, "Chrome 原始详情").startswith(".meiling/staging/"):
                raise ValueError("Chrome 原始详情必须位于本轮暂存目录")
            raw_detail_path.parent.mkdir(parents=True, exist_ok=True)
        if "expected_content_identity" not in target:
            target["expected_content_identity"] = extract_expected_content_identity(
                {}, str(target.get("platform") or ""), str(target.get("url") or "")
            )
    plan = {
        "schema_version": CHROME_CONTRACT_VERSION,
        "status": "chrome_targets_locked",
        "data_source": "chrome_devtools",
        "account_id": account_id.strip(),
        "content_list_path": str(content_list_path),
        "content_library_path": str(content_library_path),
        "requested_count": len(requested_ids),
        "requested_content_ids": requested_ids,
        "result_file_exact_path": str(result_path),
        "template_file_exact_path": str(template_path),
        "schema_file_exact_path": str(schema_path),
        "targets": targets,
    }
    template = {
        "schema_version": CHROME_CONTRACT_VERSION,
        "data_source": "chrome_devtools",
        "items": [
            {
                "target_index": target["target_index"],
                "content_id": target["content_id"],
                "request_url": "",
                "observed_content_identity": "",
                "status": "待采集",
                "fields": {field: "" for field in (*CHROME_FIELDS, "视频下载链接")},
                "failure_reason": "",
            }
            for target in targets
        ],
    }
    schema = chrome_result_schema()
    plan_path.parent.mkdir(parents=True, exist_ok=True)
    write_json_atomically(plan_path, plan)
    write_json_atomically(template_path, template)
    write_json_atomically(schema_path, schema)
    return {
        **plan,
        "chrome_plan_json": str(plan_path),
        "chrome_template_json": str(template_path),
        "chrome_schema_json": str(schema_path),
        "chrome_result_json": str(result_path),
    }


def commit_chrome_batch(
    *,
    workspace_root: Path,
    plan_path: Path,
    result_path: Path,
    account_id: str,
    content_list_path: Path,
    content_library_path: Path,
    requested_ids: List[str],
    expected_targets: List[Dict[str, object]],
    run_id: str = "",
) -> Dict[str, object]:
    if not plan_path.is_file():
        raise ValueError(f"Chrome 计划 JSON 不存在: {plan_path}")
    if not result_path.is_file():
        raise ValueError(f"Chrome 整批结果 JSON 不存在: {result_path}")
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    result = json.loads(result_path.read_text(encoding="utf-8"))
    if not isinstance(plan, dict) or plan.get("status") != "chrome_targets_locked":
        raise ValueError("Chrome 计划 JSON 格式或状态无效")
    if plan.get("schema_version") != CHROME_CONTRACT_VERSION:
        raise ValueError("Chrome 计划版本过旧，必须重新执行 prepare-chrome")
    if not isinstance(result, dict) or result.get("schema_version") != CHROME_CONTRACT_VERSION or result.get("data_source") != "chrome_devtools":
        raise ValueError("Chrome 整批结果必须是 JSON 对象")
    unknown_root_keys = sorted(set(result) - {"schema_version", "data_source", "items"})
    if unknown_root_keys:
        raise ValueError(f"Chrome 整批结果存在非 canonical 顶层字段: {unknown_root_keys}")

    expected_plan_values = {
        "schema_version": CHROME_CONTRACT_VERSION,
        "data_source": "chrome_devtools",
        "account_id": account_id.strip(),
        "content_list_path": str(content_list_path),
        "content_library_path": str(content_library_path),
        "requested_content_ids": requested_ids,
        "result_file_exact_path": str(result_path),
        "template_file_exact_path": plan.get("template_file_exact_path"),
        "schema_file_exact_path": plan.get("schema_file_exact_path"),
        "targets": expected_targets,
    }
    for key, expected in expected_plan_values.items():
        if plan.get(key) != expected:
            raise ValueError(f"Chrome 计划与当前批次不一致: {key}")

    items = result.get("items")
    if not isinstance(items, list):
        raise ValueError("Chrome 整批结果缺少 items 数组")
    by_id: Dict[str, Dict[str, Any]] = {}
    for item in items:
        if not isinstance(item, dict):
            raise ValueError("Chrome 整批结果中的单条结果必须是对象")
        content_id = str(item.get("content_id") or "").strip()
        if not content_id:
            raise ValueError("Chrome 整批结果存在缺少 content_id 的条目")
        if content_id in by_id:
            raise ValueError(f"Chrome 整批结果存在重复内容ID: {content_id}")
        by_id[content_id] = item
    actual_ids = list(by_id)
    if set(actual_ids) != set(requested_ids) or len(actual_ids) != len(requested_ids):
        missing = [content_id for content_id in requested_ids if content_id not in by_id]
        extra = [content_id for content_id in actual_ids if content_id not in set(requested_ids)]
        raise ValueError(
            "Chrome 整批结果与锁定目标不一致"
            f"；缺少={missing or []}；多出={extra or []}"
        )

    prepared_outputs: List[tuple[str, Path, Dict[str, Any]]] = []
    raw_bindings: List[tuple[Path, Dict[str, object]]] = []
    raw_detail_errors: List[Dict[str, str]] = []
    failed_items: List[Dict[str, str]] = []
    for target in expected_targets:
        content_id = str(target["content_id"])
        item = by_id[content_id]
        if item.get("target_index") != target["target_index"]:
            raise ValueError(f"Chrome 结果 target_index 不匹配: {content_id}")
        allowed_item_keys = {"target_index", "content_id", "request_url", "observed_content_identity", "status", "fields", "failure_reason"}
        unknown_keys = sorted(set(item) - allowed_item_keys)
        if unknown_keys:
            raise ValueError(f"Chrome 结果存在非 canonical 字段: {content_id}: {unknown_keys}")
        request_url = str(item.get("request_url") or "").strip()
        if not is_valid_http_url(request_url):
            failed_items.append(
                {
                    "content_id": content_id,
                    "code": "chrome_content_identity_unconfirmed",
                    "failure_reason": "Chrome 当前页面 URL 无法确认",
                }
            )
            continue
        try:
            validate_chrome_item(item)
        except DetailDataContractError as exc:
            raise ValueError(f"Chrome 结果字段合同无效: {content_id}: {exc}") from exc
        expected_identity = normalize_content_identity(target.get("platform"), target.get("expected_content_identity"))
        observed_identity = normalize_content_identity(target.get("platform"), item.get("observed_content_identity"))
        identity_error: tuple[str, str] | None = None
        if expected_identity and observed_identity:
            if expected_identity != observed_identity:
                identity_error = ("chrome_content_identity_mismatch", "Chrome 页面实际内容 ID 与目标不一致")
        elif expected_identity or observed_identity:
            identity_error = ("chrome_content_identity_unconfirmed", "Chrome 页面内容 ID 无法确认")
        elif normalize_chrome_identity_url(request_url) != normalize_chrome_identity_url(target["url"]):
            identity_error = ("chrome_content_identity_unconfirmed", "Chrome 页面链接无法确认")
        if identity_error is not None:
            failed_items.append(
                {"content_id": content_id, "code": identity_error[0], "failure_reason": identity_error[1]}
            )
            continue
        status = str(item.get("status") or "").strip()
        if status not in {"成功", "部分成功", "失败"}:
            raise ValueError(f"Chrome 结果状态无效: {content_id}")
        fields = item.get("fields")
        if not isinstance(fields, dict):
            raise ValueError(f"Chrome 结果 fields 必须是对象: {content_id}")
        for key in CHROME_FIELDS:
            actual = str(fields.get(key) or "").strip()
            expected = str(target["platform"] if key == "平台" else target["account_id"])
            if key in {"平台", "账号ID"} and actual and actual != expected:
                raise ValueError(f"Chrome 结果{key}不匹配: {content_id}")
            if status == "成功" and not actual:
                raise ValueError(f"Chrome 成功结果缺少字段 {key}: {content_id}")
        if status == "成功" and str(fields.get("媒介形式") or "").strip() == "视频" and not str(fields.get("视频下载链接") or "").strip():
            raise ValueError(f"Chrome 视频成功结果缺少字段 视频下载链接: {content_id}")
        payload = dict(fields)
        for key in CHROME_BODY_FIELDS:
            payload[key] = str(fields.get(key) or "").strip()
        for field in ("点赞数", "收藏数", "分享数", "评论数"):
            payload[field] = count_text(payload.get(field))
        if status != "成功":
            payload["失败原因"] = str(item.get("failure_reason") or "").strip()
        payload.update(
            {
                "状态": status,
                "基础数据获取方式": "chrome_devtools",
                "请求页面链接": request_url,
                "内容ID": content_id,
                "平台": target["platform"],
                "账号ID": target["account_id"],
            }
        )
        if status != "成功" and not str(item.get("failure_reason") or "").strip():
            raise ValueError(f"Chrome 非成功结果缺少失败原因: {content_id}")
        raw_path_value = str(target.get("raw_detail_json") or "").strip()
        binding_path_value = str(target.get("raw_detail_binding_json") or "").strip()
        if raw_path_value or binding_path_value:
            if not run_id:
                raise ValueError("Chrome 原始详情绑定缺少 run_id")
            raw_path = resolve_workspace_json_path(workspace_root, raw_path_value, "Chrome 原始详情")
            binding_path = resolve_workspace_json_path(workspace_root, binding_path_value, "Chrome 原始详情绑定")
            if raw_path.is_file():
                raw_payload = raw_path.read_bytes()
                valid_raw, raw_code, raw_reason = validate_xiaohongshu_raw_detail(
                    raw_payload,
                    expected_native_content_id=expected_identity,
                )
                if valid_raw:
                    raw_bindings.append((binding_path, {
                        "schema_version": 1,
                        "source": "chrome_network",
                        "run_id": run_id,
                        "content_id": content_id,
                        "native_content_id": expected_identity,
                        "raw_detail_path": _workspace_relative(workspace_root, raw_path, "Chrome 原始详情"),
                        "raw_detail_sha256": hashlib.sha256(raw_payload).hexdigest(),
                    }))
                else:
                    raw_detail_errors.append({
                        "content_id": content_id,
                        "code": raw_code or "raw_detail_unavailable",
                        "failure_reason": raw_reason or "小红书原始详情不可用",
                    })
        prepared_outputs.append((content_id, Path(str(target["output_json"])), payload))

    for _content_id, output_path, payload in prepared_outputs:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        write_json_atomically(output_path, payload)
    for binding_path, binding in raw_bindings:
        write_json_atomically(binding_path, binding)
    return {
        "status": "chrome_batch_committed",
        "requested_count": len(requested_ids),
        "committed_count": len(prepared_outputs),
        "committed_content_ids": [content_id for content_id, _path, _payload in prepared_outputs],
        "failed_items": failed_items,
        "retry_content_ids": [item["content_id"] for item in failed_items],
        "chrome_plan_json": str(plan_path),
        "chrome_result_json": str(result_path),
        "raw_detail_binding_count": len(raw_bindings),
        "raw_detail_errors": raw_detail_errors,
    }


def ordered_ids(original_order: List[str], *groups: Iterable[str]) -> List[str]:
    selected = {
        str(content_id).strip()
        for group in groups
        for content_id in group
        if str(content_id).strip()
    }
    return [content_id for content_id in original_order if content_id in selected]


def validate_retry_report_scope(
    previous: Dict[str, Any],
    *,
    account_id: str,
    data_source: str,
    current_requested_ids: List[str],
    allow_source_switch: bool = False,
) -> List[str]:
    try:
        validate_canonical_report(previous)
    except DetailDataContractError as exc:
        raise ValueError(f"上一份明细报告不是新 canonical report: {exc}") from exc
    if str(previous.get("account_id") or "").strip() != account_id.strip():
        raise ValueError("canonical report 与本次重试的账号不一致")
    previous_source = str(previous.get("data_source") or "").strip()
    if previous_source and previous_source != data_source and not allow_source_switch:
        raise ValueError("canonical report 与本次重试的数据源不一致")
    original_ids = [
        str(value).strip()
        for value in (previous.get("requested_content_ids") or [])
        if str(value).strip()
    ]
    if allow_source_switch:
        if previous.get("data_source") == data_source:
            raise ValueError("改源补抓必须使用与上一份报告不同的数据源")
        if "source_switch" in previous:
            raise ValueError("同一阶段只允许切换一次数据源")
        allowed_scope = previous.get("retry_content_ids") or []
        scope_name = "retry_content_ids"
    else:
        allowed_scope = previous.get("retry_content_ids") or []
        scope_name = "retry_content_ids"
    previous_retry_ids = {
        str(value).strip()
        for value in allowed_scope
        if str(value).strip()
    }
    if not original_ids:
        raise ValueError("canonical report 缺少原始内容ID清单")
    if not set(current_requested_ids).issubset(previous_retry_ids):
        raise ValueError(f"本次补抓内容不属于上一份报告的 {scope_name}")
    return original_ids


def merge_retry_report(
    previous: Dict[str, Any],
    current: Dict[str, Any],
    *,
    current_requested_ids: List[str],
    content_index: Dict[str, Dict[str, str]],
    allow_source_switch: bool = False,
) -> Dict[str, Any]:
    original_ids = validate_retry_report_scope(
        previous,
        account_id=str(current.get("account_id") or ""),
        data_source=str(current.get("data_source") or ""),
        current_requested_ids=current_requested_ids,
        allow_source_switch=allow_source_switch,
    )

    # 上一份报告是 canonical report，逐条事实在 `detail_collection.items`。
    # 这里必须先把它按 content_id 种进来，再用本次重试结果覆盖对应几条——否则
    # 合并结果只剩本次重试的那些，而 `requested_content_ids` 仍是整批，
    # 契约会以「canonical 单条事实范围不一致」拒掉（2026-09-19 实测）。
    previous_items = {
        str(item.get("content_id") or "").strip(): item
        for item in ((previous.get("detail_collection") or {}).get("items") or [])
        if isinstance(item, dict) and str(item.get("content_id") or "").strip()
    }
    current_items = {
        str(item.get("content_id") or "").strip(): item
        for item in (current.get("items") or [])
        if isinstance(item, dict) and str(item.get("content_id") or "").strip()
    }
    previous_items.update(current_items)

    retried = set(current_requested_ids)
    failed_items = [item for item in (current.get("failed_items") or []) if isinstance(item, dict)]
    unresolved_ids = [
        str(item.get("content_id") or "").strip()
        for item in (current.get("failed_items") or [])
        if isinstance(item, dict) and str(item.get("content_id") or "").strip()
    ]

    merged = dict(current)
    merged.update(
        {
            "requested_count": len(original_ids),
            "requested_content_ids": original_ids,
            "processed_count": len(set(previous.get("processed_content_ids") or []) | set(previous_items) | set(current_items)),
            "items": [previous_items[content_id] for content_id in original_ids if content_id in previous_items],
            "failed_items": failed_items,
            "detail_success_content_ids": ordered_ids(
                original_ids,
                previous.get("processed_content_ids") or [],
                previous.get("detail_success_content_ids") or [],
                current.get("detail_success_content_ids") or [],
            ),
            "validated_detail_content_ids": ordered_ids(
                original_ids,
                previous.get("processed_content_ids") or [],
                previous.get("validated_detail_content_ids") or [],
                current.get("validated_detail_content_ids") or [],
            ),
            "eligible_for_derived_content_ids": ordered_ids(
                original_ids,
                previous.get("eligible_for_derived_content_ids") or [],
                current.get("eligible_for_derived_content_ids") or [],
            ),
            "eligible_for_followup_content_ids": ordered_ids(
                original_ids,
                previous.get("eligible_for_followup_content_ids") or [],
                current.get("eligible_for_followup_content_ids") or [],
            ),
            "retry_content_ids": [],
            "retry_required": False,
        }
    )
    merged.pop("user_decision_required", None)
    rows = [content_index[content_id] for content_id in original_ids if content_id in content_index]
    merged["publish_time_success_count"] = sum(row_has_valid_detail_field(row, "发布时间") for row in rows)
    merged["video_url_success_count"] = sum(
        row_has_valid_detail_field(row, "视频下载链接")
        or row_has_valid_detail_field(row, "下载笔记原链接")
        for row in rows
    )
    merged["collect_success_count"] = sum(row_has_valid_detail_field(row, "收藏数") for row in rows)
    merged["share_success_count"] = sum(row_has_valid_detail_field(row, "分享数") for row in rows)
    merged["comment_success_count"] = sum(row_has_valid_detail_field(row, "评论数") for row in rows)
    return merged


def is_batch_wide_justoneapi_error(exc: Exception) -> bool:
    message = str(exc).strip().lower()
    status_match = re.search(r"\bhttp (\d{3})\b", message)
    if status_match:
        status = int(status_match.group(1))
        return status in {401, 403, 408, 425, 429} or status >= 500
    return any(
        marker in message
        for marker in [
            "network_permission_denied",
            "url error ",
            "timed out",
            "timeout",
            "connection refused",
            "connection reset",
            "name or service not known",
            "temporary failure in name resolution",
            "unauthorized",
            "invalid token",
            "token invalid",
            "鉴权失败",
        ]
    )


def _main() -> int:
    args = parse_args()
    action = str(getattr(args, "action", "") or "").strip()
    if action == "retry":
        args.acquisition_attempt = 2
    data_source = getattr(args, "data_source", "")
    acquisition_attempt = int(getattr(args, "acquisition_attempt", 1))
    workspace_root = Path(args.workspace_root).resolve()
    allow_source_switch = bool(getattr(args, "allow_source_switch", False))
    prepare_content_scope_mode = bool(getattr(args, "prepare_content_scope", False))
    confirm_content_scope = bool(getattr(args, "confirm_content_scope", False))
    prepare_chrome = action == "prepare-chrome"
    commit_chrome = action == "commit-chrome"
    calculate_derived = action == "calculate-derived"
    stage_paths: Dict[str, Path] = {}
    task_id = str(getattr(args, "task_id", "") or "").strip()
    flow_type = str(getattr(args, "flow_type", "") or "").strip()
    visibility_scope = str(getattr(args, "visibility_scope", "") or "").strip().casefold()
    run_id = str(getattr(args, "run_id", "") or "").strip()
    supplied_content_list = bool(str(getattr(args, "content_list", "") or "").strip())
    locked_context_action = False
    try:
        flow_type, task_id, run_id = validate_identity(
            flow_type=flow_type,
            task_id=task_id,
            run_id=run_id,
            standalone=not task_id and not flow_type,
        )
    except DetailDataContractError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    if action == "init-batch":
        try:
            if not str(args.account_id or "").strip() or not str(args.content_list or "").strip() or not str(args.content_library or "").strip():
                raise ValueError("init-batch 需要账号和两份正式 CSV 路径")
            content_list_path = Path(args.content_list).resolve()
            content_library_path = Path(str(args.content_library or "")).resolve()
            if not flow_type:
                # The retired scheduler no longer supplies the flow signal, so
                # derive it from the locked formal CSVs instead of defaulting a
                # self-account batch into the benchmark namespace.
                if is_benchmark_content_path(content_list_path, content_library_path):
                    flow_type = "benchmark_account"
                elif is_self_content_path(content_list_path, content_library_path):
                    flow_type = "self_account"
                args.flow_type = flow_type
            stage_paths = build_stage_artifact_paths(
                workspace_root,
                run_id,
                acquisition_attempt=1,
                allow_source_switch=False,
                task_id="",
                flow_type=flow_type,
            )
            context = {
                "schema_version": 1,
                "flow_type": flow_type or "benchmark_account",
                **({"task_id": task_id} if task_id else {}),
                "run_id": run_id,
                "account_id": str(args.account_id or "").strip(),
                "content_list_path": _workspace_relative(workspace_root, content_list_path, "内容列表"),
                "content_library_path": _workspace_relative(workspace_root, content_library_path, "内容库"),
                "data_source": str(data_source or "").strip(),
                **({"visibility_scope": visibility_scope or "public"} if flow_type == "self_account" else {}),
                "selected_content_ids": normalize_requested_ids(args.content_ids),
                "confirmed": False,
            }
            write_state_atomically(stage_paths["batch_context_json"], context)
            print(json.dumps({"status": "initialized", "run_id": run_id, "batch_context": stage_paths["batch_context_json"].relative_to(workspace_root).as_posix()}, ensure_ascii=False, indent=2))
            return 0
        except (OSError, ValueError, DetailDataContractError) as exc:
            print(str(exc), file=sys.stderr)
            return 1
    if confirm_content_scope and not prepare_content_scope_mode:
        print("--confirm-content-scope 只能与 --prepare-content-scope 一起使用", file=sys.stderr)
        return 1
    if run_id and not flow_type:
        # A locked batch context is the authoritative flow signal: the run
        # namespace is single-flow, so public run-scoped actions never need the
        # internal `--flow-type` switch once a batch exists.
        for probe_flow, probe_root in (
            ("self_account", "self-account-diagnose"),
            ("benchmark_account", "benchmark-account-diagnose"),
        ):
            probe_context_path = (
                workspace_root.resolve() / ".meiling" / "staging" / probe_root / run_id
                / "detail-data" / "批次上下文.json"
            )
            if not probe_context_path.is_file():
                continue
            try:
                probe_context = json.loads(probe_context_path.read_text(encoding="utf-8"))
            except (OSError, UnicodeError, json.JSONDecodeError):
                continue
            if not isinstance(probe_context, dict) or str(probe_context.get("run_id") or "").strip() != run_id:
                continue
            probe_flow_type = str(probe_context.get("flow_type") or "").strip()
            flow_type = probe_flow_type if probe_flow_type in {"benchmark_account", "self_account"} else probe_flow
            args.flow_type = flow_type
            break
    if run_id:
        try:
            stage_paths = build_stage_artifact_paths(
                workspace_root,
                run_id,
                acquisition_attempt=acquisition_attempt,
                allow_source_switch=allow_source_switch,
                task_id="",
                flow_type=flow_type,
            )
        except ValueError as exc:
            print(str(exc), file=sys.stderr)
            return 1
    if prepare_content_scope_mode and run_id and not str(args.content_list or "").strip():
        try:
            locked_context_action = True
            flow_type, task_id, args.account_id, resolved_content_list, resolved_content_library, stage_paths = hydrate_locked_batch_context(
                workspace_root,
                run_id=run_id,
                flow_type=flow_type,
                task_id=task_id,
            )
            args.flow_type = flow_type
            # The scheduler may return its internal task id while resolving a
            # benchmark run, but benchmark detail objects remain run-scoped.
            if flow_type == "benchmark_account":
                task_id = ""
            args.task_id = task_id
            args.content_list = str(resolved_content_list)
            args.content_library = str(resolved_content_library)
            context_path = stage_paths["batch_context_json"]
            existing_context = json.loads(context_path.read_text(encoding="utf-8"))
            if isinstance(existing_context, dict) and not data_source:
                data_source = str(existing_context.get("data_source") or "").strip()
            if flow_type == "self_account":
                locked_visibility = str(existing_context.get("visibility_scope") or "public").strip().casefold()
                if visibility_scope and visibility_scope != locked_visibility:
                    raise ValueError("可见性范围与已锁定批次不一致")
                visibility_scope = locked_visibility
        except (OSError, UnicodeError, json.JSONDecodeError, RuntimeError, ValueError, KeyError) as exc:
            print(str(exc), file=sys.stderr)
            return 1
    if action and action != "init-batch" and run_id and not str(args.content_list or "").strip():
        context_path = stage_paths.get("batch_context_json") if stage_paths else None
        try:
            locked_context_action = True
            context = json.loads(Path(context_path).read_text(encoding="utf-8")) if isinstance(context_path, Path) else {}
            if not isinstance(context, dict) or context.get("confirmed") is not True:
                raise ValueError("批次上下文尚未确认")
            flow_type = str(context.get("flow_type") or flow_type or "").strip()
            args.flow_type = flow_type
            args.account_id = str(context.get("account_id") or "").strip()
            if flow_type == "self_account":
                locked_visibility = str(context.get("visibility_scope") or "public").strip().casefold()
                if visibility_scope and visibility_scope != locked_visibility:
                    raise ValueError("可见性范围与已锁定批次不一致")
                visibility_scope = locked_visibility
            args.content_list = str(workspace_root / str(context.get("content_list_path") or ""))
            args.content_library = str(workspace_root / str(context.get("content_library_path") or ""))
            # retry 的内容 ID 只能来自 canonical report 的 retry_content_ids。若在这里
            # 先把它填成整批 selected_content_ids，下面 retry 分支的集合比较必然不等，
            # 会以「retry 只能处理 canonical report 中的 retry_content_ids」失败。
            if not str(args.content_ids or "").strip() and action != "retry":
                args.content_ids = ",".join(context.get("selected_content_ids") or [])
            if not data_source:
                data_source = str(context.get("data_source") or "").strip()
            if not data_source:
                # Older contexts may predate source hydration. Resolve the
                # immutable base-data handoff once and persist the result.
                from 账号后续详细处理批处理 import resolve_batch_context

                resolved = resolve_batch_context(
                    workspace_root, run_id, task_id=task_id, flow_type=flow_type
                )
                data_source = str(resolved.get("data_source") or "").strip()
                if data_source:
                    context["data_source"] = data_source
                    write_state_atomically(Path(context_path), context)
            # Keep the local value and argparse namespace in sync.  The
            # collection and retry paths consume the local variable below.
            args.data_source = data_source
            if action == "retry":
                canonical_path = stage_paths.get("report_json")
                try:
                    canonical = json.loads(canonical_path.read_text(encoding="utf-8")) if isinstance(canonical_path, Path) else {}
                    validate_canonical_report(canonical)
                except (OSError, UnicodeError, json.JSONDecodeError, DetailDataContractError) as exc:
                    raise ValueError(f"retry 需要可用的 canonical report: {exc}") from exc
                retry_ids = normalize_requested_ids(",".join(canonical.get("retry_content_ids") or []))
                if not retry_ids:
                    raise ValueError("当前 canonical report 没有可重试内容")
                supplied_ids = normalize_requested_ids(args.content_ids)
                if supplied_ids and set(supplied_ids) != set(retry_ids):
                    raise ValueError("retry 只能处理 canonical report 中的 retry_content_ids")
                args.content_ids = ",".join(retry_ids)
        except (OSError, UnicodeError, json.JSONDecodeError, ValueError, RuntimeError) as exc:
            print(str(exc), file=sys.stderr)
            return 1
    content_list_path = Path(args.content_list).resolve()
    content_library_value = str(getattr(args, "content_library", "") or "").strip()
    content_library_path = Path(content_library_value).resolve() if content_library_value else None
    if not flow_type and not task_id and is_benchmark_content_path(content_list_path, content_library_path):
        flow_type = "benchmark_account"
    if flow_type == "benchmark_account" and task_id:
        print("对标账号详细数据只接受 run_id，不得传入 task_id", file=sys.stderr)
        return 1
    if flow_type == "self_account":
        visibility_scope = visibility_scope or "public"
        if visibility_scope not in {"public", "private", "all"}:
            print("我方账号详细数据缺少有效的 visibility_scope", file=sys.stderr)
            return 1
    if not content_list_path.exists():
        print(f"内容列表不存在: {content_list_path}", file=sys.stderr)
        return 1
    if task_id and is_benchmark_content_path(content_list_path, content_library_path):
        print(
            "对标账号详细数据不能使用我方账号的 --task-id；请只使用 --run-id 绑定本轮，避免写入 self-account-diagnose 暂存目录",
            file=sys.stderr,
        )
        return 1

    content_fieldnames, content_rows = read_csv_table(content_list_path)
    if prepare_content_scope_mode:
        reused_confirmed_scope = False
        if prepare_chrome or commit_chrome or allow_source_switch:
            print("范围准备不能与采集、提交、重试或改源模式同时使用", file=sys.stderr)
            return 1
        scope_mode_value = str(getattr(args, "scope_mode", "") or "")
        scope_count_value = int(getattr(args, "scope_count", 0) or 0)
        scope_source = "explicit"
        if flow_type == "benchmark_account":
            scope_mode_value, scope_count_value, scope_source = resolve_configured_detail_scope(
                workspace_root,
                scope_mode=scope_mode_value,
                scope_count=scope_count_value,
                content_ids=str(getattr(args, "content_ids", "") or ""),
            )
        elif flow_type == "self_account":
            scope_mode_value, scope_count_value, scope_source = resolve_configured_self_detail_scope(
                workspace_root,
                scope_mode=scope_mode_value,
                scope_count=scope_count_value,
                content_ids=str(getattr(args, "content_ids", "") or ""),
            )
        try:
            scope = prepare_content_scope(
                content_rows,
                account_id=args.account_id,
                scope_mode=scope_mode_value,
                scope_count=scope_count_value,
                content_ids=str(getattr(args, "content_ids", "") or ""),
                visibility_scope=visibility_scope if flow_type == "self_account" else "",
            )
        except ValueError as exc:
            print(str(exc), file=sys.stderr)
            return 1
        scope["task_id"] = task_id
        scope["run_id"] = run_id
        scope["confirmed"] = confirm_content_scope
        scope["scope_source"] = scope_source
        scope["data_source"] = str(data_source or "").strip()
        if locked_context_action and not data_source:
            print("批次上下文缺少已确认的数据源", file=sys.stderr)
            return 1
        if stage_paths:
            scope_path = stage_paths["batch_context_json"]
            if not confirm_content_scope:
                # Preparation is read-only: never persist an unconfirmed
                # second source of truth.
                print(json.dumps(scope, ensure_ascii=False, indent=2))
                return 0
            existing_scope: dict[str, Any] = {}
            if scope_path.is_file():
                try:
                    existing_scope = json.loads(scope_path.read_text(encoding="utf-8"))
                except (OSError, json.JSONDecodeError) as exc:
                    print(f"既有内容范围无法读取: {exc}", file=sys.stderr)
                    return 1
                if isinstance(existing_scope, dict) and existing_scope.get("confirmed") is True:
                    same_scope = all(
                        existing_scope.get(field) == scope.get(field)
                        for field in ("task_id", "run_id", "account_id", "scope_mode", "scope_count", "visibility_scope", "selected_content_ids")
                    )
                    if not same_scope:
                        print("本任务内容范围已经确认，不能改写原始范围", file=sys.stderr)
                        return 1
                    scope = existing_scope
                    reused_confirmed_scope = True
                elif isinstance(existing_scope, dict):
                    scope = {**existing_scope, **scope}
            scope["schema_version"] = int(scope.get("schema_version") or 1)
            scope["flow_type"] = flow_type
            if flow_type == "self_account":
                scope["visibility_scope"] = visibility_scope
            if task_id:
                scope["task_id"] = task_id
            write_state_atomically(scope_path, scope)
            scope["scope_json"] = scope_path.relative_to(workspace_root).as_posix()
            if task_id and confirm_content_scope:
                state_path = content_list_path.parent / "过程记录/当前阶段状态.json"
                preserve_later_state = False
                if reused_confirmed_scope and state_path.is_file() and not state_path.is_symlink():
                    try:
                        current_state = json.loads(state_path.read_text(encoding="utf-8"))
                    except (OSError, json.JSONDecodeError):
                        current_state = None
                    if isinstance(current_state, dict):
                        target_account = current_state.get("目标账号")
                        recovery = current_state.get("恢复上下文")
                        preserve_later_state = (
                            str(current_state.get("task_id") or "").strip() == task_id
                            and str(current_state.get("run_id") or "").strip() == run_id
                            and isinstance(target_account, dict)
                            and str(target_account.get("账号ID") or "").strip()
                            == str(args.account_id or "").strip()
                            and isinstance(recovery, dict)
                            and bool(str(recovery.get("report_path") or "").strip())
                        )
                if preserve_later_state:
                    print(json.dumps(scope, ensure_ascii=False, indent=2))
                    return 0
                first_row = next(
                    (
                        row for row in content_rows
                        if str(row.get("账号ID") or "").strip() == str(args.account_id or "").strip()
                    ),
                    {},
                )
                scope_state = build_scope_stage_state(
                    workspace_root=workspace_root,
                    content_list_path=content_list_path,
                    account_id=args.account_id,
                    account_name=str(first_row.get("账号名") or ""),
                    platform=str(first_row.get("平台") or ""),
                    task_id=task_id,
                    run_id=run_id,
                    scope=scope,
                    data_source=data_source,
                )
                write_state_atomically(state_path, scope_state)
        print(json.dumps(scope, ensure_ascii=False, indent=2))
        return 0

    if content_library_path is None:
        print("缺少内容库路径", file=sys.stderr)
        return 1
    if not content_library_path.exists():
        print(f"内容库不存在: {content_library_path}", file=sys.stderr)
        return 1

    (
        content_fieldnames,
        content_rows,
        library_fieldnames,
        library_rows,
        table_snapshots,
    ) = load_detail_csv_tables(
        content_list_path,
        content_library_path,
    )

    requested_ids = normalize_requested_ids(args.content_ids)
    if not requested_ids:
        print("未提供有效的内容ID清单", file=sys.stderr)
        return 1

    bound_scope: Dict[str, Any] | None = None
    # Self-account is task-bound; benchmark is run-bound but still requires
    # the same confirmed scope persisted in the run context.
    requires_locked_scope = bool(
        task_id
        or (flow_type == "benchmark_account" and (bool(action) or not supplied_content_list))
    )
    if requires_locked_scope:
        scope_path = stage_paths.get("scope_json")
        try:
            bound_scope = json.loads(scope_path.read_text(encoding="utf-8")) if scope_path else None
        except (OSError, json.JSONDecodeError) as exc:
            print(f"本任务已确认范围无法读取: {exc}", file=sys.stderr)
            return 1
        if (
            not isinstance(bound_scope, dict)
            or bound_scope.get("confirmed") is not True
            or str(bound_scope.get("task_id") or "").strip() != task_id
            or str(bound_scope.get("run_id") or "").strip() != run_id
            or str(bound_scope.get("account_id") or "").strip() != str(args.account_id or "").strip()
            or (flow_type == "self_account" and str(bound_scope.get("visibility_scope") or "").strip().casefold() != visibility_scope)
        ):
            print("本任务缺少身份一致的已确认内容范围", file=sys.stderr)
            return 1
        original_scope_ids = normalize_requested_ids(",".join(bound_scope.get("selected_content_ids") or []))
        if acquisition_attempt == 1 and requested_ids != original_scope_ids:
            print("首次采集内容必须与本任务已确认原始范围完全一致", file=sys.stderr)
            return 1

    content_index = build_row_index(content_rows, "内容ID")
    try:
        validate_requested_account(requested_ids, content_index, args.account_id)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    library_index = build_content_library_index(library_rows, workspace_root)
    report_path = stage_paths.get("report_json")
    if not isinstance(report_path, Path):
        print("canonical report path missing from locked batch context", file=sys.stderr)
        return 1

    if requires_locked_scope and not data_source:
        print("当前账号明细采集缺少已确认的数据源", file=sys.stderr)
        return 1
    if requires_locked_scope and isinstance(bound_scope, dict):
        confirmed_source = str(bound_scope.get("data_source") or "").strip()
        if acquisition_attempt == 1 and confirmed_source and confirmed_source != data_source:
            print("首次采集数据源与确认范围时的数据源不一致", file=sys.stderr)
            return 1

    previous_report: Dict[str, Any] | None = None
    previous_report_value = str(stage_paths.get("previous_report_json", "") or "").strip()
    if previous_report_value:
        if acquisition_attempt != 2:
            print("上一轮 canonical report 只允许在第二次采集时恢复", file=sys.stderr)
            return 1
        try:
            previous_report_path = resolve_workspace_json_path(
                workspace_root, previous_report_value, "上一份明细报告"
            )
            previous_report = json.loads(previous_report_path.read_text(encoding="utf-8"))
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            print(str(exc), file=sys.stderr)
            return 1
        if not isinstance(previous_report, dict):
            print("上一份明细报告必须是 JSON 对象", file=sys.stderr)
            return 1
        if requires_locked_scope and (
            str(previous_report.get("task_id") or "").strip() != task_id
            or str(previous_report.get("run_id") or "").strip() != run_id
        ):
            print("上一份明细报告不属于当前任务和阶段运行", file=sys.stderr)
            return 1
        try:
            validate_retry_report_scope(
                previous_report,
                account_id=args.account_id,
                data_source=data_source,
                current_requested_ids=requested_ids,
                allow_source_switch=allow_source_switch,
            )
        except ValueError as exc:
            print(str(exc), file=sys.stderr)
            return 1

    if calculate_derived:
        if previous_report is None:
            try:
                canonical = json.loads(report_path.read_text(encoding="utf-8"))
                validate_canonical_report(canonical)
            except (OSError, UnicodeError, json.JSONDecodeError, DetailDataContractError) as exc:
                print(f"calculate-derived 需要可用的 canonical report: {exc}", file=sys.stderr)
                return 1
        else:
            canonical = previous_report
        if str(canonical.get("account_id") or "").strip() != str(args.account_id or "").strip():
            print("canonical report 与当前账号不一致", file=sys.stderr)
            return 1
        if str(canonical.get("flow_type") or "").strip() != flow_type or str(canonical.get("run_id") or "").strip() != run_id:
            print("canonical report 与当前批次身份不一致", file=sys.stderr)
            return 1
        if task_id and str(canonical.get("task_id") or "").strip() != task_id:
            print("canonical report 与当前任务不一致", file=sys.stderr)
            return 1
        derived_ids = derived_content_ids_from_report(canonical, requested_ids)
        if not derived_ids:
            print(json.dumps({
                "status": "derived_fields_skipped",
                "account_id": args.account_id,
                "run_id": run_id,
                "requested_count": 0,
                "derived_content_ids": [],
                "report_json": "",
                "reason": "canonical report 没有可计算衍生字段的内容",
            }, ensure_ascii=False, indent=2))
            return 0
        command = [
            sys.executable,
            str(_EXECUTION_UNIT_DIR / "账号衍生字段计算批处理.py"),
            "--account-id", str(args.account_id),
            "--content-list", _workspace_relative(workspace_root, content_list_path, "内容列表"),
            "--content-library", _workspace_relative(workspace_root, content_library_path, "内容库"),
            "--content-ids", ",".join(derived_ids),
            "--workspace-root", str(workspace_root),
            "--flow-type", flow_type,
            "--run-id", run_id,
        ]
        if task_id:
            command.extend(["--task-id", task_id])
        try:
            completed = subprocess.run(
                command,
                cwd=str(workspace_root),
                capture_output=True,
                text=True,
                check=False,
            )
        except OSError as exc:
            print(f"启动衍生字段批处理失败: {exc}", file=sys.stderr)
            return 1
        if completed.returncode != 0:
            message = (completed.stderr or completed.stdout or "衍生字段批处理失败").strip()
            print(message, file=sys.stderr)
            return completed.returncode or 1
        try:
            derived_report = json.loads(completed.stdout)
        except json.JSONDecodeError as exc:
            print(f"衍生字段批处理返回不是 JSON: {exc}", file=sys.stderr)
            return 1
        if not isinstance(derived_report, dict):
            print("衍生字段批处理返回必须是 JSON 对象", file=sys.stderr)
            return 1
        if (
            str(derived_report.get("flow_type") or "").strip() != flow_type
            or str(derived_report.get("run_id") or "").strip() != run_id
            or str(derived_report.get("account_id") or "").strip() != str(args.account_id or "").strip()
            or (task_id and str(derived_report.get("task_id") or "").strip() != task_id)
        ):
            print("衍生字段批处理返回身份不一致", file=sys.stderr)
            return 1
        returned_ids = ordered_ids(derived_ids, derived_report.get("derived_success_content_ids") or [])
        print(json.dumps({
            "status": "derived_fields_calculated",
            "account_id": args.account_id,
            "run_id": run_id,
            **({"task_id": task_id} if task_id else {}),
            "requested_count": len(derived_ids),
            "derived_content_ids": returned_ids,
            "failed_count": int(derived_report.get("failed_count") or 0),
            "report_json": str(derived_report.get("report_json") or ""),
        }, ensure_ascii=False, indent=2))
        return 0
    if prepare_chrome and commit_chrome:
        print("Chrome 批次不能同时 prepare 和 commit", file=sys.stderr)
        return 1
    if prepare_chrome or commit_chrome:
        if data_source != "chrome_devtools":
            print("Chrome 批次控制只允许 data_source=chrome_devtools", file=sys.stderr)
            return 1
        try:
            plan_path = resolve_workspace_json_path(
                workspace_root,
                    str(stage_paths.get("chrome_plan_json", "")),
                "Chrome 计划 JSON",
            )
            result_path = resolve_workspace_json_path(
                workspace_root,
                    str(stage_paths.get("chrome_result_json", "")),
                "Chrome 整批结果 JSON",
            )
            targets = build_chrome_batch_targets(
                workspace_root=workspace_root,
                content_list_path=content_list_path,
                content_library_path=content_library_path,
                content_index=content_index,
                library_index=library_index,
                requested_ids=requested_ids,
                account_id=args.account_id,
                raw_detail_dir=Path(stage_paths["stage_dir"]) / "raw-note-details",
            )
            if prepare_chrome:
                chrome_output = prepare_chrome_batch(
                    workspace_root=workspace_root,
                    plan_path=plan_path,
                    result_path=result_path,
                    template_path=resolve_workspace_json_path(
                        workspace_root,
                        str(stage_paths.get("chrome_template_json", ""))
                        or str(result_path.with_name(f"{result_path.stem}-模板{result_path.suffix}")),
                        "Chrome 结果模板 JSON",
                    ),
                    schema_path=resolve_workspace_json_path(
                        workspace_root,
                        str(stage_paths.get("chrome_schema_json", ""))
                        or str(result_path.with_name(f"{result_path.stem}.schema{result_path.suffix}")),
                        "Chrome 结果 schema JSON",
                    ),
                    account_id=args.account_id,
                    content_list_path=content_list_path,
                    content_library_path=content_library_path,
                    requested_ids=requested_ids,
                    targets=targets,
                )
            else:
                chrome_output = commit_chrome_batch(
                    workspace_root=workspace_root,
                    plan_path=plan_path,
                    result_path=result_path,
                    account_id=args.account_id,
                    content_list_path=content_list_path,
                    content_library_path=content_library_path,
                    requested_ids=requested_ids,
                    expected_targets=targets,
                    run_id=run_id,
                )
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            print(str(exc), file=sys.stderr)
            return 1
        print(json.dumps(_public_chrome_output(workspace_root, chrome_output), ensure_ascii=False, indent=2))
        return 0

    if args.dry_run:
        preview_items: List[Dict[str, object]] = []
        for content_id in requested_ids:
            row = content_index.get(content_id)
            if row is None:
                preview_items.append(
                    {
                        "content_id": content_id,
                        "status": "planned",
                        "validation": "内容ID不在内容列表中",
                    }
                )
                continue
            title = (row.get("标题") or "").strip()
            library_row = lookup_content_library_row(library_index, row)
            content_root = resolve_content_root(workspace_root, content_library_path)
            content_dir = resolve_existing_content_dir(workspace_root, content_root, row, library_row)
            if content_dir is None:
                content_dir = content_root / build_content_dir_name(content_library_path, row, title)
            process_dir = build_content_process_dir(content_list_path, content_id, title)
            preview_items.append(
                {
                    "content_id": content_id,
                    "title": title,
                    "status": "planned",
                    "content_dir": _workspace_relative(workspace_root, content_dir, "内容目录"),
                    "process_dir": _workspace_relative(workspace_root, process_dir, "过程目录"),
                    "result_json": _workspace_relative(
                        workspace_root, process_dir / "明细补全结果.json", "明细补全结果"
                    ),
                    "structured_detail_json": _workspace_relative(
                        workspace_root, build_structured_detail_path(content_dir), "结构化明细"
                    ),
                    "missing_fields": [
                        field for field in DETAIL_FIELDS if not row_has_valid_detail_field(row, field)
                    ],
                }
            )
        print(
            json.dumps(
                {
                    "status": "planned",
                    "account_id": args.account_id,
                    "requested_count": len(requested_ids),
                    "requested_content_ids": requested_ids,
                    "preview_paths": {
                        "report_json": _workspace_relative(workspace_root, report_path, "canonical report"),
                        "items": preview_items,
                    },
                    "items": preview_items,
                    "generated_files": [],
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0

    structured_fetch_command = select_structured_fetch_command(
        getattr(args, "structured_fetch_command", "")
    )
    structured_fetch_template: List[str] = []
    if structured_fetch_command:
        try:
            structured_fetch_template = preflight_structured_cli_template(structured_fetch_command)
        except RuntimeError as exc:
            print(f"结构化 CLI 适配器配置无效: {exc}", file=sys.stderr)
            return 1

    env_values = load_env(workspace_root / "本地配置.txt")
    justone_token = env_values.get("JUSTONEAPI_TOKEN", "") if data_source == "justoneapi" else ""
    justone_base_url = env_values.get("JUSTONEAPI_BASE_URL", "https://api.justoneapi.com")
    if content_rows:
        content_fieldnames = ensure_fieldnames(
            content_fieldnames,
            [CONTENT_DIR_FIELD, *STRUCTURED_CONTEXT_FIELDS, *DETAIL_FIELDS, *OPTIONAL_DETAIL_FIELDS],
        )
    if content_list_path == content_library_path:
        # Keep the aliased table's schema unified after content-list fields are
        # expanded; both business views commit through the one target below.
        library_fieldnames = content_fieldnames
    elif (library_rows or content_rows) and "我方资料" not in content_library_path.parts:
        library_fieldnames = ensure_fieldnames(
            library_fieldnames,
            [CONTENT_DIR_FIELD, *STRUCTURED_CONTEXT_FIELDS, *DETAIL_FIELDS, *OPTIONAL_DETAIL_FIELDS],
        )
    report_path.parent.mkdir(parents=True, exist_ok=True)

    summary: Dict[str, Any] = {
        "flow_type": flow_type,
        "task_id": task_id,
        "run_id": run_id,
        "account_id": args.account_id,
        "requested_count": len(requested_ids),
        "requested_content_ids": requested_ids,
        "processed_count": 0,
        "publish_time_success_count": 0,
        "video_url_success_count": 0,
        "collect_success_count": 0,
        "share_success_count": 0,
        "comment_success_count": 0,
        "detail_success_content_ids": [],
        "validated_detail_content_ids": [],
        "retry_content_ids": [],
        "eligible_for_derived_content_ids": [],
        "eligible_for_followup_content_ids": [],
        "failed_items": [],
        "errors": [],
        "items": [],
        "report_json": _workspace_relative(workspace_root, report_path, "canonical report"),
        "detail_collection": {},
        "followup_delivery": {},
        "missing_deliverables": [],
    }
    if isinstance(bound_scope, dict):
        summary["scope_mode"] = str(bound_scope.get("scope_mode") or "").strip()
        summary["scope_count"] = bound_scope.get("scope_count")
        summary["scope_json"] = str(stage_paths.get("scope_json") or "")

    justone_blocker = ""

    # 方案C 兜底：标题前 10 字重复的内容（小红书标题普遍以 #话题# 开头）会算出
    # 同一个目录名进而共用目录、产物互相覆盖。这里先按整批预分配唯一目录名，
    # 并避开 CSV 里别条内容已在用的目录名。
    content_dir_owners = collect_content_dir_owners(
        [*(library_rows or []), *(content_rows or [])],
        content_root=resolve_content_root(workspace_root, content_library_path),
        workspace_root=workspace_root,
    )
    planned_content_dir_names = plan_content_dir_names(
        (content_index.get(content_id) for content_id in requested_ids),
        content_library_path,
        existing_names=content_dir_owners,
    )

    for index, content_id in enumerate(requested_ids, start=1):
        print_progress(f"[明细补全] 开始第 {index}/{len(requested_ids)} 条：{content_id}")
        row = content_index.get(content_id)
        if row is None:
            summary["failed_items"].append({
                "content_id": content_id,
                "title": "",
                "failure_reason": "内容ID不在内容列表中",
                "failures": ["内容ID不在内容列表中"],
            })
            summary["retry_content_ids"].append(content_id)
            continue

        title = (row.get("标题") or "").strip()
        library_row = lookup_content_library_row(library_index, row)
        content_root = resolve_content_root(workspace_root, content_library_path)
        existing_content_dir = resolve_existing_content_dir(workspace_root, content_root, row, library_row)
        # 历史撞名数据：这个目录已被别条内容占用（标题前10字相同导致），不能复用，
        # 置空后按本批次预分配的唯一目录名重建。
        if existing_content_dir is not None and content_dir_owners.get(existing_content_dir.name) not in (None, content_id):
            existing_content_dir = None
        if existing_content_dir is not None:
            content_dir = existing_content_dir
        else:
            dir_name = planned_content_dir_names.get(content_id) or build_content_dir_name(content_library_path, row, title)
            content_dir = content_root / dir_name
        process_dir = build_content_process_dir(content_list_path, content_id, title)
        failures: List[str] = []
        fetch_attempt_errors: List[str] = []
        before_row = dict(row)

        fetched_sources: List[str] = []
        detail_obtained = False
        structured_status = read_structured_detail_status(content_dir)
        if structured_status in {"失败", "failed", "error"}:
            fetch_attempt_errors.append("结构化明细候选明确标记为失败")
        elif structured_status in {"部分成功", "partial"}:
            # 部分成功只表示字段不完整，已取得字段仍应继续合并落盘。
            pass
        official_sources = merge_official_detail_fields(row, library_row)
        if official_sources:
            detail_obtained = True
            fetched_sources.extend(official_sources)

        structured_fields, structured_source = read_structured_detail_fields(content_dir)
        if structured_fields:
            merge_detail_fields(row, structured_fields, overwrite=False)
            if has_structured_detail_payload(structured_fields):
                detail_obtained = True
                if structured_source:
                    fetched_sources.append(structured_source)

        if data_source == "justoneapi" and any(
            not row_has_valid_detail_field(row, field)
            for field in DETAIL_FIELDS + OPTIONAL_DETAIL_FIELDS
        ) and structured_fetch_template:
            structured_output_path = build_structured_detail_path(content_dir)
            try:
                ensure_dir(content_dir)
                fetched, fetched_source = run_structured_fetch_command(
                    structured_fetch_template,
                    row=row,
                    content_id=content_id,
                    title=title,
                    content_dir=content_dir,
                    output_path=structured_output_path,
                    workspace_root=workspace_root,
                )
            except Exception as exc:  # noqa: BLE001
                code = permission_error_code(exc, "process")
                if code:
                    raise RuntimeError(code) from exc
                fetch_attempt_errors.append(f"结构化 CLI 适配器失败: {exc}")
            else:
                merge_detail_fields(row, fetched, overwrite=False)
                if has_structured_detail_payload(fetched):
                    detail_obtained = True
                    fetched_sources.append(fetched_source or "CLI适配器")

        required_missing = [
            field for field in required_detail_fields(row)
            if not row_has_valid_detail_field(row, field)
        ]
        if required_missing:
            if data_source != "justoneapi":
                fetch_attempt_errors.append(
                    "明细字段缺失，当前数据源为 Chrome，流程不自动切换 JustOneAPI"
                )
            elif not justone_token:
                fetch_attempt_errors.append("明细字段缺失且 JUSTONEAPI_TOKEN 未配置")
            elif justone_blocker:
                fetch_attempt_errors.append(f"JustOneAPI 兜底已停止: {justone_blocker}")
            else:
                try:
                    fetched = fetch_detail_fields((row.get("平台") or "").strip(), justone_base_url, justone_token, row)
                except Exception as exc:  # noqa: BLE001
                    code = permission_error_code(exc, "network")
                    if code:
                        raise RuntimeError(code) from exc
                    fetched = {}
                    if is_batch_wide_justoneapi_error(exc):
                        justone_blocker = str(exc)
                    fetch_attempt_errors.append(f"JustOneAPI 分支失败: {exc}")
                else:
                    merge_detail_fields(row, fetched, overwrite=False)
                    if has_structured_detail_payload(fetched):
                        detail_obtained = True
                        fetched_sources.append("JustOneAPI")

        if detail_obtained and fetched_sources:
            row["基础数据获取方式"] = " -> ".join(dict.fromkeys(source for source in fetched_sources if source))

        if detail_obtained or existing_content_dir is not None:
            # 落库形态必须唯一：工作区内路径一律写成相对工作区根的 posix 路径。
            # 直接写 str(content_dir) 会把本机绝对路径带进正式 CSV，迁移工作区或
            # 打包交付时全部失效（对照 账号后续详细处理批处理.py 的 _relative_workspace_path）。
            row[CONTENT_DIR_FIELD] = _workspace_relative(workspace_root, content_dir, CONTENT_DIR_FIELD)

        explicit_structured_failure = structured_status in {"失败", "failed", "error"}
        media_type = str(row.get("媒介形式") or "").strip()
        content_id_present = bool(str(row.get("内容ID") or "").strip())
        errors: List[Dict[str, Any]] = []
        if not content_id_present:
            errors.append(structured_error("content_identity_missing", "缺少内容ID"))
        if media_type not in {"视频", "图文"}:
            errors.append(structured_error("media_type_unknown", "媒介形式未知、为空或不受支持"))
        if explicit_structured_failure:
            errors.append(structured_error("content_evidence_missing", "结构化明细明确标记为失败"))
        valid_content = has_valid_content_evidence(row) and not explicit_structured_failure
        if valid_content:
            normalize_empty_interactions(row)
        elif media_type in {"视频", "图文"} and content_id_present and not explicit_structured_failure:
            errors.append(structured_error("content_evidence_missing", "缺少可确认内容归属的页面、封面或互动证据"))
        missing_detail_fields = [
            field for field in required_detail_fields(row)
            if not row_has_valid_detail_field(row, field)
        ]
        if media_type == "图文":
            for field in ("标题", "发布时间"):
                if not row_has_valid_detail_field(row, field):
                    missing_detail_fields.append(field)
        field_error_codes = {
            "发布时间": "publish_time_unavailable",
            "标题": "title_unavailable",
            "视频文案路径": "video_transcript_unavailable",
        }
        for field in missing_detail_fields:
            errors.append(structured_error(
                field_error_codes.get(field, f"{field}_unavailable"),
                f"{field}缺失",
            ))
        errors.extend(
            structured_error("detail_fetch_failed", message, blocking=False, retryable=True)
            for message in fetch_attempt_errors
        )
        failures.extend(
            item["message"] for item in errors if item.get("blocking") is True
        )
        base_remark = strip_remark_prefixes(row.get("备注", ""), ["内容明细补全:"])
        if failures:
            row["备注"] = merge_remark(base_remark, "内容明细补全: " + "，".join(dict.fromkeys(failures)))
        else:
            row["备注"] = base_remark

        sync_content_library_row(
            library_rows,
            library_index,
            row,
            library_fieldnames,
            own_content_library="我方资料" in content_library_path.parts,
        )
        result = build_item_result(
            account_id=args.account_id,
            content_id=content_id,
            title=title,
            content_dir=content_dir,
            process_dir=process_dir,
            before_row=before_row,
            after_row=row,
            failures=list(dict.fromkeys(failures)),
            errors=errors,
        )
        base_detail_available = valid_content
        if explicit_structured_failure:
            result["status"] = "失败"
            base_detail_available = False
        result["eligible_for_followup"] = can_enter_followup(row)
        summary["items"].append(result)
        summary["errors"].extend(
            {**item, "content_id": content_id}
            for item in errors
            if isinstance(item, dict)
        )
        # 每条内容完成即持久化 checkpoint；正式 CSV/report 仍由末尾 finalizer 一次提交。
        item_result_path = Path(result["result_json"])
        item_result_path.parent.mkdir(parents=True, exist_ok=True)
        write_json_atomically(item_result_path, result)
        summary["processed_count"] += 1
        if row_has_valid_detail_field(row, "发布时间"):
            summary["publish_time_success_count"] += 1
        if row_has_valid_detail_field(row, "视频下载链接") or row_has_valid_detail_field(row, "下载笔记原链接"):
            summary["video_url_success_count"] += 1
        if row_has_valid_detail_field(row, "收藏数"):
            summary["collect_success_count"] += 1
        if row_has_valid_detail_field(row, "分享数"):
            summary["share_success_count"] += 1
        if row_has_valid_detail_field(row, "评论数"):
            summary["comment_success_count"] += 1
        if base_detail_available:
            summary["detail_success_content_ids"].append(content_id)
            summary["validated_detail_content_ids"].append(content_id)
        if base_detail_available and can_calculate_derived(row):
            summary["eligible_for_derived_content_ids"].append(content_id)
        if result["eligible_for_followup"]:
            summary["eligible_for_followup_content_ids"].append(content_id)
        if result["blocking_errors"]:
            summary["failed_items"].append(
                {
                    "content_id": content_id,
                    "title": title,
                    "failure_reason": "；".join(result["failures"]),
                    "failures": result["failures"],
                    "errors": result["blocking_errors"],
                }
            )
        if any(item.get("retryable") is True for item in errors):
            summary["retry_content_ids"].append(content_id)
        print_progress(f"[明细补全] 完成第 {index}/{len(requested_ids)} 条：{content_id} -> {result['status']}")

    failed_count = len(summary["failed_items"])
    usable_count = sum(
        1 for item in summary["items"]
        if item.get("valid_evidence") and not item.get("blocking_errors")
    )
    has_blocking = any(item.get("blocking") is True for item in summary["errors"])
    if summary["processed_count"] == 0 or usable_count == 0:
        summary["status"] = "failed"
    elif failed_count or any(
        derive_item_status(
            {
                "content_id": item.get("content_id"),
                "base_data_available": bool(item.get("base_data_available")),
                "missing_deliverables": list(item.get("missing_deliverables") or []),
                "blocking_errors": list(item.get("blocking_errors") or []),
            }
        ) != "completed"
        for item in summary["items"]
    ):
        summary["status"] = "partial"
    else:
        summary["status"] = "completed"
    summary["next_action"] = "continue_detail_analysis" if summary["status"] in {"completed", "partial"} else "report_blockers"
    # 汇总缺口供人和执行者直接阅读（示例：`「标题」缺视频文案`）。
    # 不再写「内容 ID + 缺口」的对象结构：内容 ID 是机器标识，读者认不出是哪条内容。
    # 口径统一走 missing_gap_labels，与下方逐条 canonical 事实同源，避免"逐条有缺口、汇总为空"。
    summary["missing_deliverables"] = missing_deliverable_summaries(summary["items"])
    summary.update(
        {
            "data_source": data_source,
            "acquisition_attempt": acquisition_attempt,
            "retry_required": bool(summary["retry_content_ids"]) and acquisition_attempt == 1,
        }
    )
    if previous_report is not None:
        try:
            summary = merge_retry_report(
                previous_report,
                summary,
                current_requested_ids=requested_ids,
                content_index=content_index,
                allow_source_switch=allow_source_switch,
            )
        except ValueError as exc:
            print(str(exc), file=sys.stderr)
            return 1
        if allow_source_switch:
            summary["source_switch"] = {
                "from": previous_report.get("data_source"),
                "to": data_source,
            }
    summary["retry_content_ids"] = list(dict.fromkeys(summary["retry_content_ids"]))
    summary["detail_collection"] = {
        "requested_content_ids": list(requested_ids),
        "processed_count": summary["processed_count"],
        "validated_detail_content_ids": list(summary["validated_detail_content_ids"]),
        "retry_content_ids": list(summary["retry_content_ids"]),
    }
    summary["followup_delivery"] = {
        "status": summary["status"],
        "missing_deliverables": list(summary.get("missing_deliverables") or []),
    }
    # Persist only the canonical report. Batch counters and per-item failure
    # details remain process-local or in per-item checkpoints.
    if flow_type and run_id:
        facts = canonical_facts_from_items(summary.get("items", []))
        finalized = finalize_item_facts(facts, requested_content_ids=summary.get("requested_content_ids") or requested_ids)
        # 明细重跑只更新明细部分。后续处理与报告字段由 详细数据报告存储器 统一维护。
        # 写入）已经落进 canonical report 的交付进度必须原样保留：本步骤不读旧报告就整体覆盖，
        # 会让"已完成"在重跑一次收集动作后退回"待处理"，用户看到的是凭空消失的进度。
        previous_followup: Dict[str, Any] = {}
        if report_path.is_file():
            try:
                previous_report = json.loads(report_path.read_text(encoding="utf-8"))
            except (OSError, UnicodeError, json.JSONDecodeError):
                previous_report = None
            if isinstance(previous_report, dict):
                previous_delivery = previous_report.get("followup_delivery")
                if isinstance(previous_delivery, dict) and previous_delivery.get("updated_by"):
                    previous_followup = previous_delivery
        canonical_fields = {
            "status": finalized["status"],
            "counts": finalized["counts"],
            "data_source": data_source,
            "processed_content_ids": list(summary.get("validated_detail_content_ids") or []),
            "detail_collection": {
                "items": finalized["items"],
                "collected_count": sum(item["base_data_available"] for item in finalized["items"]),
            },
            "followup_delivery": previous_followup or {
                "status": summary["status"], "items": [],
                "completed_count": len(summary.get("eligible_for_followup_content_ids") or []),
                "missing_count": len(summary.get("missing_deliverables") or []),
            },
            "retry_content_ids": list(summary.get("retry_content_ids") or []),
            "missing_deliverables": list(summary.get("missing_deliverables") or []),
            "artifacts": {
                "content_list_csv": _workspace_relative(workspace_root, content_list_path, "内容列表"),
                "content_library_csv": _workspace_relative(workspace_root, content_library_path, "内容库"),
                "detail_data_report": _workspace_relative(workspace_root, report_path, "canonical report"),
            },
        }
    else:
        canonical = summary
    # A wholly failed collection has no trustworthy business data to commit.
    if summary["status"] != "failed":
        write_csv_tables_atomically(
            build_detail_csv_writes(
                content_list_path,
                content_fieldnames,
                content_rows,
                content_library_path,
                library_fieldnames,
                library_rows,
            ),
            expected_originals=table_snapshots,
        )
    for item in summary["items"]:
        item_result_path = Path(item["result_json"])
        item_result_path.parent.mkdir(parents=True, exist_ok=True)
        write_json_atomically(item_result_path, item)
    if flow_type and run_id:
        canonical = canonical_store.create_report(
            workspace_root=workspace_root, flow_type=flow_type, task_id=task_id,
            run_id=run_id, account_id=args.account_id,
            scope_mode=(bound_scope or {}).get("scope_mode") if isinstance(bound_scope, dict) else "",
            scope_count=(bound_scope or {}).get("scope_count") if isinstance(bound_scope, dict) else None,
            requested_content_ids=summary.get("requested_content_ids") or requested_ids,
            report_path=report_path, initial_fields=canonical_fields,
        )
    else:
        canonical = summary
        write_json_atomically(report_path, canonical)
    first_row = next((content_index.get(content_id) for content_id in requested_ids if content_index.get(content_id)), {})
    stage_state = build_detail_stage_state(
        workspace_root=workspace_root,
        content_list_path=content_list_path,
        account_id=args.account_id,
        account_name=str(first_row.get("账号名") or ""),
        platform=str(first_row.get("平台") or ""),
        report_path=report_path,
        summary=summary,
        task_id=task_id,
        run_id=run_id,
        scope={
            **(bound_scope or {}),
            "scope_json": str(stage_paths.get("scope_json") or ""),
        } if isinstance(bound_scope, dict) else None,
    )
    write_state_atomically(content_list_path.parent / "过程记录/当前阶段状态.json", stage_state)

    stdout_summary = {
        "flow_type": flow_type,
        "run_id": run_id,
        **({"task_id": task_id} if task_id else {}),
        "account_id": args.account_id,
        "requested_count": summary["requested_count"],
        "processed_count": summary["processed_count"],
        "publish_time_success_count": summary["publish_time_success_count"],
        "video_url_success_count": summary["video_url_success_count"],
        "collect_success_count": summary["collect_success_count"],
        "share_success_count": summary["share_success_count"],
        "comment_success_count": summary["comment_success_count"],
        "detail_success_content_ids": summary["detail_success_content_ids"],
        "validated_detail_content_ids": summary["validated_detail_content_ids"],
        "retry_content_ids": summary["retry_content_ids"] if acquisition_attempt == 1 else [],
        "retry_required": bool(summary["retry_content_ids"]) and acquisition_attempt == 1,
        "acquisition_attempt": acquisition_attempt,
        "eligible_for_derived_content_ids": summary["eligible_for_derived_content_ids"],
        "eligible_for_followup_count": len(summary["eligible_for_followup_content_ids"]),
        "eligible_for_followup_content_ids": summary["eligible_for_followup_content_ids"],
        "failed_count": len(summary["failed_items"]),
        "status": summary["status"],
        "errors": summary["errors"],
        "next_action": summary["next_action"],
        "missing_deliverables": summary["missing_deliverables"],
        "report_json": _workspace_relative(workspace_root, report_path, "canonical report"),
    }
    print(json.dumps(stdout_summary, ensure_ascii=False, indent=2))
    return 0


def main() -> int:
    try:
        return _main()
    except UnsafeContentDirectoryError as exc:
        print(json.dumps({"ok": False, "error_code": "unsafe_content_path", "error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 1
    except Exception as exc:  # noqa: BLE001
        error_code = permission_error_code(exc, "filesystem")
        if not error_code:
            raise
        print(
            json.dumps({"ok": False, "error_code": error_code, "error": str(exc)}, ensure_ascii=False),
            file=sys.stderr,
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
