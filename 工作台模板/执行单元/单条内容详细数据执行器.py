#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
import shlex
import subprocess
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Dict, Iterable, List, Sequence

_EXECUTION_UNIT_DIR = Path(__file__).resolve().parent
if str(_EXECUTION_UNIT_DIR) not in sys.path:
    sys.path.insert(0, str(_EXECUTION_UNIT_DIR))

from csv安全写入 import read_csv_table, sync_owned_fields
from 过程记录路径 import (
    build_benchmark_content_dir_name,
    build_content_dir_name,
    build_content_process_dir,
    resolve_existing_content_dir as resolve_safe_existing_content_dir,
    sanitize_title,
    UnsafeContentDirectoryError,
)
from 权限错误 import permission_error_code
from 账号阶段状态 import write_json_atomically
from 详细数据阶段合同 import DetailDataContractError, structured_error, validate_identity


def resolve_workspace_id(workspace_root: Path, configured_value: str = "") -> str:
    """Use the explicit ID when present, otherwise consume the local identity contract."""
    explicit = str(configured_value or "").strip()
    if explicit:
        return explicit
    try:
        from 工作区身份执行器 import inspect_identity

        identity = inspect_identity(workspace_root)
    except Exception:  # noqa: BLE001 - identity absence is reported by ASR contract.
        return ""
    return str(identity.get("workspace_id") or "").strip()


CONTENT_FIELDS = [
    "内容ID",
    "平台",
    "账号ID",
    "账号名",
    "标题",
    "内容目录路径",
    "封面图原链接",
    "封面图路径",
    "视频下载链接",
    "媒介形式",
    "公开状态",
    "点赞数",
    "收藏数",
    "分享数",
    "评论数",
    "核心互动数",
    "数据水平",
    "发布时间",
    "发布时间区间",
    "对外页面链接",
    "基础数据获取方式",
    "bvid",
    "aid",
    "cid",
    "视频文案路径",
    "内容分析路径",
    "备注",
]
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
        "封面图原链接",
        CONTENT_DIR_FIELD,  # 同上：路径只填空、不覆盖
        "对外页面链接",
        "内容链接",
        "笔记链接",
    }
)
CONTENT_LIBRARY_OWNED_FIELDS = CONTENT_LIBRARY_FILL_ONLY_FIELDS | frozenset(
    {CONTENT_DIR_FIELD, "封面图路径", "视频下载链接", "基础数据获取方式", "视频文案路径", "备注"}
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


@dataclass
class StepResult:
    status: str
    output_path: str = ""
    failure_reason: str = ""


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="执行单条视频的机械处理：下载封面图、获取视频文案，并为当前 AI 准备总结输入。"
    )
    parser.add_argument("--account-id", required=True, help="账号ID，例如 账号001")
    parser.add_argument("--content-id", required=True, help="内容ID，例如 内容001")
    parser.add_argument("--content-list", required=True, help="账号内容列表 CSV 路径")
    parser.add_argument("--content-library", required=True, help="内容库 CSV 路径")
    parser.add_argument(
        "--workspace-root",
        default=".",
        help="工作区根目录，默认使用调用方当前工作目录",
    )
    parser.add_argument(
        "--cover-command",
        default="",
        help="下载封面图命令模板，支持 {content_id} {title} {platform} {cover_url} {output_path} {content_dir}",
    )
    parser.add_argument(
        "--transcript-command",
        default="",
        help="获取视频文案命令模板，支持 {content_id} {title} {platform} {media_type} {video_url} {note_url} {fetch_method} {bvid} {aid} {cid} {output_path} {content_dir}",
    )
    parser.add_argument("--workspace-id", default="", help="托管 ASR 绑定的工作区 ID")
    parser.add_argument("--asr-ticket", default="", help="托管 ASR 短期票据")
    parser.add_argument("--run-id", default="", help="逐条处理使用的本轮唯一标识")
    parser.add_argument("--flow-type", default="", choices=("", "benchmark_account", "self_account"))
    parser.add_argument("--task-id", default="", help="公开调度任务编号；与 flow_type/run_id 一起使用")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="只做目录和路径预计算，不执行外部命令",
    )
    return parser.parse_args()


def build_default_command_templates(executor_dir: Path) -> dict[str, str]:
    cover_script = executor_dir / "下载封面图执行器.py"
    transcript_script = executor_dir / "获取视频文案执行器.py"
    python_bin = shlex.quote(sys.executable)
    return {
        "cover": (
            f"{python_bin} {shlex.quote(str(cover_script))} "
            "--content-id {content_id} "
            "--title {title} "
            "--platform {platform} "
            "--cover-url {cover_url} "
            "--output-path {output_path}"
        ),
        "transcript": (
            f"{python_bin} {shlex.quote(str(transcript_script))} "
            "--content-id {content_id} "
            "--title {title} "
            "--platform {platform} "
            "--media-type {media_type} "
            "--video-url {video_url} "
            "--fetch-method {fetch_method} "
            "--bvid {bvid} "
            "--aid {aid} "
            "--cid {cid} "
            "--note-url {note_url} "
            "--output-path {output_path} "
            "--refresh-meta-path {refresh_meta_path} "
            "--workspace-root {workspace_root}"
            " --workspace-id {workspace_id}"
            " --run-id {run_id}"
        ),
    }


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


def pick_row_value(row: Dict[str, str], *keys: str) -> str:
    for key in keys:
        value = (row.get(key) or "").strip()
        if value:
            return value
    return ""


def output_file_is_valid(path: Path) -> bool:
    return path.exists() and path.is_file() and path.stat().st_size > 0


def existing_output_path(row: Dict[str, str], field: str, workspace_root: Path) -> Path | None:
    value = (row.get(field) or "").strip()
    if not value:
        return None
    stored_path = Path(value).expanduser()
    check_path = stored_path if stored_path.is_absolute() else workspace_root / stored_path
    if not output_file_is_valid(check_path):
        return None
    return stored_path if stored_path.is_absolute() else check_path


def read_refresh_meta(meta_path: Path) -> Dict[str, str]:
    if not meta_path.exists() or not meta_path.is_file():
        return {}
    try:
        payload = json.loads(meta_path.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001
        code = permission_error_code(exc, "filesystem")
        if code:
            raise RuntimeError(code) from exc
        return {}
    if not isinstance(payload, dict):
        return {}
    return {str(key): str(value) if value is not None else "" for key, value in payload.items()}


def bool_text(value: str) -> bool:
    return str(value).strip().lower() in {"1", "true", "yes", "y", "是", "成功"}


def build_recovery_meta(
    *,
    row: Dict[str, str],
    failures: List[str],
    steps: Dict[str, StepResult],
    refresh_meta: Dict[str, str],
) -> Dict[str, object]:
    failure_text = "；".join(failures)
    media_type = pick_row_value(row, "媒介形式")
    note_url = pick_row_value(row, "对外页面链接", "内容链接", "笔记链接")
    recovery_meta: Dict[str, object] = {}
    evidence: List[str] = []

    transcript_step = steps.get("获取视频文案")
    transcript_succeeded = transcript_step is not None and transcript_step.status == "success"
    refresh_attempted = bool_text(refresh_meta.get("refresh_attempted", ""))
    refresh_succeeded = bool_text(refresh_meta.get("refresh_succeeded", ""))

    if refresh_succeeded:
        source = (refresh_meta.get("refresh_source") or "").strip()
        evidence.append(f"刷新视频直链成功{f'({source})' if source else ''}")
        recovery_meta.update(
            {
                "failure_classification": "正常视频_直链过期",
                "classification_evidence": evidence,
                "recovery_path": "刷新视频直链 -> 重新获取视频文案",
                "recovery_status": "成功" if transcript_succeeded else "失败",
            }
        )
        return recovery_meta

    recovery_tokens = [
        "获取视频文案",
        "ASR",
        "视频下载链接",
        "当前版本只处理视频内容",
        "SUCCESS_WITH_NO_VALID_FRAGMENT",
        "直链",
        "article",
        "无有效片段",
    ]
    if not refresh_attempted and not any(token in failure_text for token in recovery_tokens):
        return recovery_meta

    if failure_text:
        evidence.append(failure_text)
    if media_type:
        evidence.append(f"媒介形式={media_type}")
    if note_url and "article" in note_url.lower():
        evidence.append("对外页面链接包含 article")

    classification = "未知类型_需要人工确认"
    recovery_path = "停止自动补齐，等待人工确认"
    lowered_failure = failure_text.lower()

    if "article" in lowered_failure or (note_url and "article" in note_url.lower()):
        classification = "正文型样本_article_or_text"
        recovery_path = "提取页面正文 -> 写入视频文案.md"
    elif "当前版本只处理视频内容" in failure_text or (media_type and media_type != "视频"):
        classification = "图文或无声占位_不可ASR"
        recovery_path = "提取页面正文或图文说明；无正文时保留失败"
    elif "视频下载链接不可用" in failure_text or "本地预检失败" in failure_text:
        classification = "正常视频_直链过期"
        recovery_path = "刷新视频直链 -> 本地下载验证 -> 重新获取视频文案"
    elif any(
        token in failure_text
        for token in ["HTTP 403", "HTTP 400", "HTTP 500", "SUCCESS_WITH_NO_VALID_FRAGMENT", "ASR 返回为空", "无有效片段", "拉流"]
    ):
        classification = "正常视频_ASR远程拉流被拦"
        recovery_path = "本地下载视频 -> 提取音频 -> 本地文件识别"

    recovery_meta.update(
        {
            "failure_classification": classification,
            "classification_evidence": evidence,
            "recovery_path": recovery_path,
            "recovery_status": "失败" if failures else "跳过",
        }
    )
    return recovery_meta


def run_template_command(
    template: str,
    variables: Dict[str, str],
    *,
    cwd: Path,
    dry_run: bool,
) -> subprocess.CompletedProcess[str]:
    shell_safe_variables = {key: shlex.quote(str(value)) for key, value in variables.items()}
    command = shlex.split(template.format(**shell_safe_variables))
    if dry_run:
        return subprocess.CompletedProcess(command, 0, stdout="dry-run", stderr="")
    return subprocess.run(
        command,
        cwd=str(cwd),
        text=True,
        capture_output=True,
        check=False,
    )


def extract_step_failure_reason(stdout: str, stderr: str) -> str:
    """取执行器给出的真正失败原因。

    子执行器失败时会把含 ``失败原因`` 的 JSON 打到 stdout；整段直接当作失败
    原因会夹带 timing、路径等噪音写进备注，这里解析出干净的原因。解析不到时
    才退回原始输出。
    """
    for text in (stdout, stderr):
        for line in reversed(str(text or "").splitlines()):
            candidate = line.strip()
            if not (candidate.startswith("{") and candidate.endswith("}")):
                continue
            try:
                payload = json.loads(candidate)
            except json.JSONDecodeError:
                continue
            if isinstance(payload, dict):
                reason = str(payload.get("失败原因") or payload.get("failure_reason") or "").strip()
                if reason:
                    return reason
    return (str(stderr or "").strip() or str(stdout or "").strip())


def execute_step(
    *,
    step_name: str,
    command_template: str,
    variables: Dict[str, str],
    expected_output: Path,
    cwd: Path,
    dry_run: bool,
    precomputed_path: Path | None = None,
) -> StepResult:
    if precomputed_path is not None and precomputed_path.is_file():
        try:
            payload = json.loads(precomputed_path.read_text(encoding="utf-8"))
            step = payload.get("step") if isinstance(payload, dict) else None
            if isinstance(step, dict) and step.get("status") in {"success", "failed", "degraded"}:
                return StepResult(
                    status=str(step.get("status")),
                    output_path=str(step.get("output_path") or ""),
                    failure_reason=str(step.get("failure_reason") or ""),
                )
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            pass
    if output_file_is_valid(expected_output):
        return StepResult(status="success", output_path=str(expected_output))
    if not command_template:
        return StepResult(status="skipped", failure_reason=f"{step_name}未配置命令模板")
    result = run_template_command(command_template, variables, cwd=cwd, dry_run=dry_run)
    if result.returncode == 0 and (dry_run or output_file_is_valid(expected_output)):
        return StepResult(status="success", output_path=str(expected_output))
    reason = extract_step_failure_reason(result.stdout or "", result.stderr or "") or f"{step_name}执行失败"
    return StepResult(status="failed", failure_reason=reason)


def clear_row_paths(row: Dict[str, str], fields: Sequence[str]) -> None:
    for field in fields:
        row[field] = ""


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


def strip_remark_prefix(existing: str, prefix: str) -> str:
    parts = [part.strip() for part in str(existing or "").split("；") if part.strip()]
    return "；".join(part for part in parts if not part.startswith(prefix))


def sync_content_library_row(
    library_rows: List[Dict[str, str]],
    library_index: Dict[str, Dict[str, str]],
    content_row: Dict[str, str],
    library_fieldnames: List[str],
    *,
    clear_fields: Iterable[str] = (),
    own_content_library: bool = False,
) -> None:
    """Compatibility helper; the single-item main path deliberately does not call it."""
    key = build_content_library_key(content_row)
    if not key:
        return
    target = lookup_content_library_row(library_index, content_row)
    if target is None:
        target = {field: "" for field in library_fieldnames}
        library_rows.append(target)
        library_index[key] = target
    owned = OWN_CONTENT_LIBRARY_OWNED_FIELDS if own_content_library else CONTENT_LIBRARY_OWNED_FIELDS
    fill_only = OWN_CONTENT_LIBRARY_FILL_ONLY_FIELDS if own_content_library else CONTENT_LIBRARY_FILL_ONLY_FIELDS
    sync_owned_fields(
        target,
        content_row,
        library_fieldnames,
        owned_fields=owned,
        field_aliases=CONTENT_LIBRARY_FIELD_ALIASES,
        fill_only_fields=fill_only,
        clear_fields=clear_fields,
    )
    if "当前状态" in library_fieldnames:
        current_status = str(target.get("当前状态") or "").strip()
        if own_content_library:
            if current_status in {"", "待发布", "已发布", "已复盘"}:
                target["当前状态"] = "已完成"
        elif current_status != "已复盘":
            target["当前状态"] = "已发布"


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
    steps: Dict[str, StepResult],
    failures: List[str],
    refresh_meta: Dict[str, str],
    recovery_meta: Dict[str, object],
) -> Dict[str, object]:
    if failures:
        status = "部分成功" if any(step.status == "success" for step in steps.values()) else "失败"
    elif any(step.status == "pending_ai" for step in steps.values()):
        status = "等待AI总结"
    elif any(step.status == "degraded" for step in steps.values()):
        status = "部分完成"
    else:
        status = "成功"
    degradation_reasons = [
        result.failure_reason
        for result in steps.values()
        if result.status == "degraded" and result.failure_reason
    ]
    errors = []
    for name, result in steps.items():
        if result.status in {"degraded", "failed"} and result.failure_reason:
            code = "video_transcript_unavailable" if name == "获取视频文案" else "detail_fetch_failed"
            errors.append(structured_error(code, result.failure_reason, blocking=False, retryable=True))
    execution_status = "failed" if failures and not any(step.status == "success" for step in steps.values()) else "degraded" if degradation_reasons else "success"
    delivery_status = "completed" if not failures and not degradation_reasons and not any(step.status == "pending_ai" for step in steps.values()) else "partial" if any(step.status == "success" for step in steps.values()) else "failed"
    result = {
        "account_id": account_id,
        "content_id": content_id,
        "title": title,
        "status": status,
        "execution_status": execution_status,
        "delivery_status": delivery_status,
        "evidence_mode": "full" if delivery_status == "completed" else "degraded" if delivery_status == "partial" else "insufficient",
        "content_dir": str(content_dir),
        "process_dir": str(process_dir),
        "result_json": str(process_dir / "执行结果.json"),
        "steps": {name: asdict(result) for name, result in steps.items()},
        "failures": failures,
        "errors": errors,
        "degradation_reasons": degradation_reasons,
        "refresh_meta_path": str(process_dir / "视频文案刷新结果.json"),
        "refresh_meta": refresh_meta,
        "recovery_meta": recovery_meta,
        "failure_classification": recovery_meta.get("failure_classification", ""),
        "classification_evidence": recovery_meta.get("classification_evidence", []),
        "recovery_path": recovery_meta.get("recovery_path", ""),
        "recovery_status": recovery_meta.get("recovery_status", ""),
        "generated_files": [
            result.output_path for result in steps.values() if result.status == "success" and result.output_path
        ],
    }
    return result


def main() -> int:
    args = parse_args()
    workspace_root = Path(args.workspace_root).resolve()
    default_templates = build_default_command_templates(Path(__file__).resolve().parent)
    if not args.cover_command:
        args.cover_command = default_templates["cover"]
    if not args.transcript_command:
        args.transcript_command = default_templates["transcript"]
    content_list_path = Path(args.content_list).resolve()
    content_library_path = Path(args.content_library).resolve()
    run_id = str(getattr(args, "run_id", "") or "").strip()
    task_id = str(getattr(args, "task_id", "") or "").strip()
    flow_type = str(getattr(args, "flow_type", "") or "").strip()
    try:
        flow_type, task_id, run_id = validate_identity(
            flow_type=flow_type, task_id=task_id, run_id=run_id, standalone=not task_id and not flow_type
        )
    except DetailDataContractError as exc:
        print(str(exc), file=sys.stderr)
        return 1

    if not content_list_path.exists():
        print(f"内容列表不存在: {content_list_path}", file=sys.stderr)
        return 1
    if not content_library_path.exists():
        print(f"内容库不存在: {content_library_path}", file=sys.stderr)
        return 1

    content_fieldnames, content_rows = read_csv_table(content_list_path)
    if not content_fieldnames:
        content_fieldnames = list(CONTENT_FIELDS)
    if not args.dry_run:
        content_fieldnames = ensure_fieldnames(content_fieldnames, [CONTENT_DIR_FIELD])
    content_index = build_row_index(content_rows, "内容ID")
    library_fieldnames, library_rows = read_csv_table(content_library_path)
    if not library_fieldnames:
        library_fieldnames = list(CONTENT_FIELDS)
    if not args.dry_run:
        library_fieldnames = ensure_fieldnames(library_fieldnames, [CONTENT_DIR_FIELD])
    library_index = build_content_library_index(library_rows, workspace_root)

    row = content_index.get(args.content_id)
    if row is None:
        print(f"内容ID不在内容列表中: {args.content_id}", file=sys.stderr)
        return 1
    original_row = dict(row)
    actual_account_id = (row.get("账号ID") or "").strip()
    if actual_account_id != args.account_id.strip():
        print(
            f"本轮账号ID与内容列表不一致，预期 {args.account_id.strip()}，实际 {actual_account_id or '缺失'}",
            file=sys.stderr,
        )
        return 1
    library_row = lookup_content_library_row(library_index, row)

    title = pick_row_value(row, "标题")
    content_root = resolve_content_root(workspace_root, content_library_path)
    existing_content_dir = resolve_existing_content_dir(workspace_root, content_root, row, library_row)
    if existing_content_dir is not None:
        content_dir = existing_content_dir
    else:
        content_dir = content_root / build_content_dir_name(content_library_path, row, title)
    process_dir = build_content_process_dir(content_list_path, args.content_id, title)

    cover_path = existing_output_path(row, "封面图路径", workspace_root) or content_dir / "封面图.png"
    transcript_path = existing_output_path(row, "视频文案路径", workspace_root) or content_dir / "视频文案.md"
    refresh_meta_path = process_dir / "视频文案刷新结果.json"

    if args.dry_run:
        transcript_ticket_required = (
            pick_row_value(row, "媒介形式") == "视频"
            and not output_file_is_valid(transcript_path)
        )
        preview_steps = {
            "下载封面图": StepResult(
                status="success" if output_file_is_valid(cover_path) else "planned",
                output_path=str(cover_path),
            ),
            "获取视频文案": StepResult(
                status="success" if output_file_is_valid(transcript_path) else "planned",
                output_path=str(transcript_path),
            ),
        }
        preview_paths = {
            "content_dir": str(content_dir),
            "process_dir": str(process_dir),
            "cover": str(cover_path),
            "transcript": str(transcript_path),
            "result_json": str(process_dir / "执行结果.json"),
        }
        print(
            json.dumps(
                {
                    "run_id": run_id,
                    "task_id": task_id,
                    "flow_type": flow_type,
                    "account_id": args.account_id,
                    "content_id": args.content_id,
                    "requested_content_id": args.content_id,
                    "title": title,
                    "status": "planned",
                    "transcript_ticket_required": transcript_ticket_required,
                    "preview_paths": preview_paths,
                    "steps": {name: asdict(result) for name, result in preview_steps.items()},
                    "failures": [],
                    "generated_files": [],
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0

    ensure_dir(content_dir)
    ensure_dir(process_dir)
    row[CONTENT_DIR_FIELD] = str(content_dir)

    resolved_workspace_id = resolve_workspace_id(
        workspace_root,
        str(getattr(args, "workspace_id", "") or ""),
    )
    shared_vars = {
        "content_id": args.content_id,
        "title": title,
        "platform": pick_row_value(row, "平台"),
        "media_type": row.get("媒介形式", ""),
        "cover_url": row.get("封面图原链接", ""),
        "video_url": pick_row_value(row, "视频下载链接", "下载笔记原链接"),
        "note_url": pick_row_value(row, "对外页面链接", "内容链接", "笔记链接"),
        "fetch_method": pick_row_value(row, "基础数据获取方式"),
        "bvid": pick_row_value(row, "bvid"),
        "aid": pick_row_value(row, "aid"),
        "cid": pick_row_value(row, "cid"),
        "content_dir": str(content_dir),
        "workspace_root": str(workspace_root),
        "refresh_meta_path": str(refresh_meta_path),
        "workspace_id": resolved_workspace_id,
        "run_id": run_id,
    }

    step_results: Dict[str, StepResult] = {}
    failures: List[str] = []
    library_clear_fields: set[str] = set()
    # 有效封面是正式结果，存在时完全跳过下载；临时失败不能清空旧路径。
    cover_result = execute_step(
        step_name="下载封面图",
        command_template=args.cover_command,
        variables={**shared_vars, "output_path": str(cover_path)},
        expected_output=cover_path,
        cwd=workspace_root,
        dry_run=args.dry_run,
        precomputed_path=None,
    )
    step_results["下载封面图"] = cover_result
    if cover_result.status == "success":
        row["封面图路径"] = cover_result.output_path
    elif not output_file_is_valid(cover_path):
        # 仅对本轮没有任何有效封面时记录缺口。
        clear_row_paths(row, ["封面图路径"])
        library_clear_fields.add("封面图路径")
        if cover_result.failure_reason and cover_result.status == "failed":
            failures.append(f"下载封面图: {cover_result.failure_reason}")

    media_type = pick_row_value(row, "媒介形式")
    if media_type == "视频":
        video_url = pick_row_value(row, "视频下载链接")
        if media_type == "视频" and not video_url.startswith(("http://", "https://")):
            transcript_result = StepResult(
                status="failed",
                failure_reason="视频缺少视频下载链接",
            )
        else:
            # 凭证由托管流程或统一服务端配置负责获取；本地缺少中间参数时仍尝试执行器。
            transcript_result = execute_step(
                step_name="获取视频文案",
                command_template=args.transcript_command,
                variables={**shared_vars, "output_path": str(transcript_path)},
                expected_output=transcript_path,
                cwd=workspace_root,
                dry_run=args.dry_run,
            )
            if transcript_result.status == "failed":
                transcript_result = StepResult(
                    status="degraded",
                    failure_reason=(
                        f"视频文案提取未完成，使用标题、封面、页面信息和表现数据进行降级分析："
                        f"{transcript_result.failure_reason}"
                    ),
                )
    else:
        transcript_result = StepResult(status="skipped", failure_reason="非视频内容，跳过视频文案")
    step_results["获取视频文案"] = transcript_result
    refresh_meta = read_refresh_meta(refresh_meta_path)
    refreshed_video_url = (refresh_meta.get("active_video_url") or refresh_meta.get("refreshed_video_url") or "").strip()
    if refreshed_video_url:
        row["视频下载链接"] = refreshed_video_url
    if transcript_result.status == "success":
        row["视频文案路径"] = transcript_result.output_path
        refresh_source = (refresh_meta.get("refresh_source") or "").strip()
        if refresh_source:
            current_fetch_method = (row.get("基础数据获取方式") or "").strip()
            refresh_tag = f"直链刷新({refresh_source})"
            if current_fetch_method:
                if refresh_tag not in current_fetch_method:
                    row["基础数据获取方式"] = f"{current_fetch_method} -> {refresh_tag}"
            else:
                row["基础数据获取方式"] = refresh_tag
    elif transcript_result.status in {"failed", "degraded"}:
        if not output_file_is_valid(transcript_path):
            clear_row_paths(row, ["视频文案路径"])
            library_clear_fields.update({"视频文案路径"})
        if transcript_result.failure_reason and transcript_result.status == "failed":
            failures.append(f"获取视频文案: {transcript_result.failure_reason}")


    recovery_meta = build_recovery_meta(
        row=row,
        failures=failures,
        steps=step_results,
        refresh_meta=refresh_meta,
    )

    base_remark = strip_remark_prefix(row.get("备注", ""), "后续详细处理:")
    if failures:
        remark_parts = list(failures)
        failure_classification = str(recovery_meta.get("failure_classification") or "").strip()
        recovery_path = str(recovery_meta.get("recovery_path") or "").strip()
        if failure_classification:
            remark_parts.append(f"失败分型: {failure_classification}")
        if recovery_path:
            remark_parts.append(f"建议补齐路径: {recovery_path}")
        row["备注"] = merge_remark(base_remark, "后续详细处理: " + "，".join(remark_parts))
    else:
        row["备注"] = base_remark

    result = build_item_result(
        account_id=args.account_id,
        content_id=args.content_id,
        title=title,
        content_dir=content_dir,
        process_dir=process_dir,
        steps=step_results,
        failures=failures,
        refresh_meta=refresh_meta,
        recovery_meta=recovery_meta,
    )
    result["run_id"] = run_id
    result["task_id"] = task_id
    result["flow_type"] = flow_type
    result["retry_required"] = bool(
        media_type == "视频" and transcript_result.status == "degraded"
    )
    result["retry_content_ids"] = [args.content_id] if result["retry_required"] else []
    result["row_updates"] = {
        key: value for key, value in row.items() if value != original_row.get(key)
    }
    result_path = process_dir / "执行结果.json"
    write_json_atomically(result_path, result)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except UnsafeContentDirectoryError as exc:
        print(json.dumps({"ok": False, "error_code": "unsafe_content_path", "error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        raise SystemExit(1)
