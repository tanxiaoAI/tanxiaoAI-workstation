#!/usr/bin/env python3
from __future__ import annotations

import argparse
import base64
import hashlib
import importlib.util
import ipaddress
import json
import platform
import re
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from collections.abc import Mapping
from pathlib import Path

import 证书信任  # noqa: F401  进程级统一 HTTPS 证书信任
from 权限错误 import permission_error_code
from 抖音内容链接解析 import canonicalize_douyin_content_url, extract_douyin_content_id
from 本地配置读取 import load_local_config
from 正文证据选择器 import platform_caption_evidence, read_json_object, video_url_hash


VOLCENGINE_ASR_URL = "https://openspeech.bytedance.com/api/v3/auc/bigmodel/recognize/flash"
# Retained for the legacy hosted/batch code paths in this shared executor.
DASHSCOPE_BASE_URL = "https://dashscope.aliyuncs.com"
SENSITIVE_QUERY_KEYS = {"token", "key", "api_key", "apikey", "access_token"}
MAX_PLATFORM_CAPTION_BYTES = 2 * 1024 * 1024
PLATFORM_CAPTION_TIMEOUT_SECONDS = 20



def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="获取单条内容的视频文案。")
    parser.add_argument("--content-id", required=True)
    parser.add_argument("--title", required=True)
    parser.add_argument("--platform", required=True)
    parser.add_argument("--media-type", required=True)
    parser.add_argument("--video-url", default="")
    parser.add_argument("--fetch-method", default="")
    parser.add_argument("--bvid", default="")
    parser.add_argument("--aid", default="")
    parser.add_argument("--cid", default="")
    parser.add_argument("--note-url", default="")
    parser.add_argument("--output-path", required=True)
    parser.add_argument("--result-output-json", default="", help="正文结果回执的原子输出路径")
    parser.add_argument("--body-source-json", default="", help="图文或正文型内容的结构化数据 JSON")
    parser.add_argument("--content-production-record-path", default="", help="当前内容已确认终稿所在的内容生产记录")
    parser.add_argument("--content-state-path", default="", help="当前内容正式生产状态文件")
    parser.add_argument("--raw-detail-json", default="", help="本轮 staging 中的平台原始详情 JSON")
    parser.add_argument("--raw-detail-sha256", default="", help="原始详情在 prepare 前冻结的 SHA256")
    parser.add_argument("--native-content-id", default="", help="prepare 返回的平台原生内容 ID")
    parser.add_argument("--platform-caption-binding-json", default="", help="本轮平台字幕原始详情绑定回执")
    parser.add_argument("--platform-caption-evidence-json", default="", help="平台字幕证据输出路径，必须位于本轮 staging")
    parser.add_argument("--refresh-meta-path", default="")
    parser.add_argument("--workspace-root", default=".", help="工作区根目录，默认使用调用方当前工作目录")
    parser.add_argument(
        "--asr-result-json",
        default="",
        help="统一 URL ASR 服务返回的单元素批次结果 JSON",
    )
    parser.add_argument("--run-id", default="", help="当前流程运行标识，用于绑定批量 ASR 结果")
    parser.add_argument(
        "--asr-idempotency-key",
        default="",
        help="调用方为本批次冻结的幂等键；不传时按本执行器的命名空间重算",
    )
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args()


load_env = load_local_config



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


def http_post_json(url: str, payload: dict, *, headers: dict[str, str], timeout: int = 120) -> dict:
    request = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers=headers,
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except Exception as exc:  # noqa: BLE001
        code = permission_error_code(exc, "network")
        if code:
            raise RuntimeError(code) from exc
        raise


def normalize_url(value: object) -> str:
    text = str(value or "").strip()
    if text.startswith("http://") or text.startswith("https://"):
        return text
    return ""


def is_fragmented_xiaohongshu_url(url: str) -> bool:
    """小红书把视频切成 `.m4s` 分片时，火山的 ASR 会直接拒（45000151）。

    B 站拿 `.m4s` 音频流是正常做法，所以这个判断只对小红书生效。
    解析 query 之前先按路径判断，避免 `?sign=...` 里偶发出现 `.m4s` 字样误伤。
    """
    path = str(url or "").split("?", 1)[0].split("#", 1)[0].strip().lower()
    return path.endswith(".m4s")


_H265_URL_MARKERS = ("hvc1", "h265", "hevc", "media-video-hvc")


def douyin_asr_url_rejection(video_url: str) -> str:
    """抖音直链里这些形态火山一定拉不到，提交前就拦下。

    抖音用 MSE 播放时 `<video>` 的 `currentSrc` 是 `blob:`，退回 Network 只能拿到
    `media-video-hvc1` 的 H.265 分路。火山是由服务端去下载这个地址，实测分别报
    `45000006`（音频拉不到）和 `45000151`（格式不正确），重试不会改变结果。
    """
    text = str(video_url or "").strip().lower()
    if text.startswith("blob:"):
        return "视频直链是 blob: 浏览器内部地址，火山 ASR 拉不到；请从公开页详情接口的 play_addr_h264 取 H.264 mp4"
    if any(marker in text for marker in _H265_URL_MARKERS):
        return "视频直链是 H.265 分路（media-video-hvc1），火山服务端拉不到；请从公开页详情接口的 play_addr_h264 取 H.264 mp4"
    path = text.split("?", 1)[0].split("#", 1)[0]
    if path.endswith(".m4s") or path.endswith(".m3u8"):
        return "视频直链是分片/播放列表，火山 ASR 只接受整段 mp4"
    return ""


def extract_creator_center_play_addr(raw_list_path: Path | str, expected_native_id: str = "") -> str:
    """从创作者中心「内容管理」首屏作品列表响应里取 H.264 直链。

    2026-11-04 实测：同一账号下，创作者中心 `work_list` 给的
    `v5-se-gddgtc-cold.douyinvod.com` / `v95-hzyy-thr-daily-colda.douyinvod.com`
    直链，火山 URL 转写能正常拉取；公开视频页那组 `v95-zjwz2tc-web.douyinvod.com`
    反而拉不到（45000006）。所以自账号优先用创作者中心的直链。
    """
    path = Path(raw_list_path)
    if not path.is_file():
        return ""
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return ""
    items = payload.get("aweme_list") if isinstance(payload, Mapping) else None
    if not isinstance(items, list):
        items = payload.get("items") if isinstance(payload, Mapping) else None
    if not isinstance(items, list):
        return ""
    expected = str(expected_native_id or "").strip()
    for item in items:
        if not isinstance(item, Mapping):
            continue
        native_id = str(item.get("aweme_id") or item.get("id") or "").strip()
        if expected and native_id != expected:
            continue
        video = item.get("video")
        if not isinstance(video, Mapping):
            continue
        candidates: list[str] = []
        for key in ("play_addr", "download_addr"):
            node = video.get(key)
            urls = node.get("url_list") if isinstance(node, Mapping) else []
            for raw_url in urls or []:
                url = normalize_url(raw_url)
                if url and not douyin_asr_url_rejection(url):
                    candidates.append(url)
        for url in candidates:
            if "mime_type=video_mp4" in url:
                return url
        if candidates:
            return candidates[0]
    return ""


def extract_douyin_play_addr(raw_detail_path: Path | str, expected_native_id: str = "") -> str:
    """从本轮已保存的抖音详情响应里取 H.264 mp4 直链。

    2.3 采集时页面会自己发出 `aweme/v1/web/aweme/detail/`，响应落盘在
    `raw-details/<内容ID>-detail.network-response`。这里直接从这份响应取链，
    不再让后续步骤重新打开页面猜 `currentSrc`（MSE 页面永远是 blob）。
    """
    path = Path(raw_detail_path)
    if not path.is_file():
        return ""
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return ""
    aweme = payload.get("aweme_detail") if isinstance(payload, Mapping) else None
    if not isinstance(aweme, Mapping):
        return ""
    native_id = str(aweme.get("aweme_id") or "").strip()
    expected = str(expected_native_id or "").strip()
    if expected and native_id and native_id != expected:
        return ""
    video = aweme.get("video")
    if not isinstance(video, Mapping):
        return ""
    candidates: list[str] = []
    for key in ("play_addr_h264", "play_addr"):
        node = video.get(key)
        urls = node.get("url_list") if isinstance(node, Mapping) else []
        for item in urls or []:
            url = normalize_url(item)
            if url and not douyin_asr_url_rejection(url):
                candidates.append(url)
    for url in candidates:
        if "mime_type=video_mp4" in url:
            return url
    return candidates[0] if candidates else ""


MAX_ASR_UPLOAD_BYTES = 20 * 1024 * 1024

_ASR_DOWNLOAD_REFERERS = {
    "小红书": "https://www.xiaohongshu.com/",
    "抖音": "https://www.douyin.com/",
    "B站": "https://www.bilibili.com/",
}


def download_media_bytes(video_url: str, platform: str = "") -> bytes:
    """把视频整段下载到内存，供 base64 直传 ASR。

    上限 20MB：再大 base64 后的请求体超过服务端 28MB 的 JSON 限制，也没法
    保证火山能接收。超限时给用户可执行的失败原因，不做半截上传。
    抖音 CDN 对高频请求偶发 403/429，这里对可重试状态重试两次。
    """
    parsed = urllib.parse.urlsplit(str(video_url or "").strip())
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise RuntimeError("视频直链格式无效，无法下载转写")
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/156.0.0.0 Safari/537.36"
        ),
        "Accept": "*/*",
        "Accept-Language": "zh-CN,zh;q=0.9",
    }
    referer = _ASR_DOWNLOAD_REFERERS.get(platform)
    if referer:
        headers["Referer"] = referer
    last_error: Exception | None = None
    for attempt in range(1, 4):
        try:
            request = urllib.request.Request(video_url, headers=headers)
            with urllib.request.urlopen(request, timeout=120) as response:
                declared = int(response.headers.get("Content-Length") or 0)
                if declared and declared > MAX_ASR_UPLOAD_BYTES:
                    raise RuntimeError(
                        f"视频文件约 {declared / 1048576:.1f}MB，超过 20MB 上传上限，无法转写"
                    )
                payload = response.read(MAX_ASR_UPLOAD_BYTES + 1)
            break
        except urllib.error.HTTPError as exc:
            last_error = exc
            if exc.code not in {403, 429, 500, 502, 503, 504} or attempt >= 3:
                raise RuntimeError(f"视频下载失败（HTTP {exc.code}），无法转写") from exc
            time.sleep(1.0 * attempt)
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            last_error = exc
            if attempt >= 3:
                raise RuntimeError(f"视频下载失败：{exc}") from exc
            time.sleep(1.0 * attempt)
    else:
        raise RuntimeError(f"视频下载失败：{last_error}") from last_error
    if len(payload) > MAX_ASR_UPLOAD_BYTES:
        raise RuntimeError("视频文件超过 20MB 上传上限，无法转写")
    if not payload:
        raise RuntimeError("视频下载结果为空，无法转写")
    return payload


def sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def atomic_write_bytes(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{time.time_ns()}.tmp")
    try:
        temporary.write_bytes(payload)
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def atomic_write_text(path: Path, text: str) -> None:
    atomic_write_bytes(path, (text + "\n").encode("utf-8"))


def validate_result_output(raw_path: str, workspace_root: Path, run_id: str = "") -> str:
    text = str(raw_path or "").strip()
    if not text:
        return ""
    path = Path(text).expanduser()
    if not path.is_absolute():
        path = workspace_root / path
    path = path.resolve()
    try:
        relative = path.relative_to(workspace_root.resolve())
    except ValueError as exc:
        raise RuntimeError("正文结果回执路径越界") from exc
    if not relative.as_posix().startswith(".meiling/staging/"):
        raise RuntimeError("正文结果回执必须位于暂存目录")
    if run_id and run_id not in relative.parts:
        raise RuntimeError("正文结果回执不属于当前运行")
    return str(path)


def write_json_atomically(path: Path, payload: dict[str, object]) -> None:
    atomic_write_bytes(path, (json.dumps(payload, ensure_ascii=False, indent=2) + "\n").encode("utf-8"))


def _staging_path(workspace_root: Path, raw_path: str, *, run_id: str = "") -> Path:
    path = Path(raw_path).expanduser()
    if not path.is_absolute():
        path = workspace_root / path
    path = path.resolve()
    try:
        relative = path.relative_to(workspace_root.resolve())
    except ValueError as exc:
        raise RuntimeError("平台字幕原始详情路径越界") from exc
    if not relative.as_posix().startswith(".meiling/staging/"):
        raise RuntimeError("平台字幕原始详情必须位于本轮暂存目录")
    if run_id and run_id not in relative.parts:
        raise RuntimeError("平台字幕原始详情不属于当前运行")
    return path


def _safe_public_http_url(value: object) -> tuple[str, str]:
    url = str(value or "").strip()
    parsed = urllib.parse.urlsplit(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password:
        return "", "字幕URL必须是无凭证的HTTP(S)公网地址"
    host = parsed.hostname.rstrip(".").lower()
    try:
        addresses = {item[4][0] for item in socket.getaddrinfo(host, parsed.port or (443 if parsed.scheme == "https" else 80), type=socket.SOCK_STREAM)}
    except socket.gaierror:
        return "", "字幕URL主机无法解析"
    if not addresses:
        return "", "字幕URL主机无可用地址"
    for address in addresses:
        try:
            ip = ipaddress.ip_address(address)
        except ValueError:
            return "", "字幕URL主机地址无效"
        if not ip.is_global:
            return "", "字幕URL指向非公网地址"
    return url, ""


class _SafeCaptionRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # type: ignore[override]
        accepted, reason = _safe_public_http_url(newurl)
        if not accepted:
            raise RuntimeError(f"字幕URL重定向不安全：{reason}")
        return super().redirect_request(req, fp, code, msg, headers, accepted)


def download_platform_caption_srt(url: str) -> bytes:
    accepted, reason = _safe_public_http_url(url)
    if not accepted:
        raise RuntimeError(reason)
    request = urllib.request.Request(accepted, headers={"User-Agent": "Mozilla/5.0", "Accept": "text/plain,text/vtt,*/*"}, method="GET")
    opener = urllib.request.build_opener(_SafeCaptionRedirectHandler())
    try:
        with opener.open(request, timeout=PLATFORM_CAPTION_TIMEOUT_SECONDS) as response:
            content_length = response.headers.get("Content-Length", "")
            if content_length.isdigit() and int(content_length) > MAX_PLATFORM_CAPTION_BYTES:
                raise RuntimeError("字幕响应超过体积上限")
            payload = response.read(MAX_PLATFORM_CAPTION_BYTES + 1)
    except urllib.error.HTTPError as exc:
        raise RuntimeError(f"字幕下载HTTP失败:{exc.code}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"字幕下载失败:{permission_error_code(exc, 'network') or exc.reason}") from exc
    if len(payload) > MAX_PLATFORM_CAPTION_BYTES:
        raise RuntimeError("字幕响应超过体积上限")
    return payload


def srt_to_text(payload: bytes) -> str:
    try:
        source = payload.decode("utf-8-sig")
    except UnicodeDecodeError:
        source = payload.decode("utf-8", errors="replace")
    lines: list[str] = []
    for line in source.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        item = line.strip()
        if not item or item.isdigit() or "-->" in item:
            continue
        item = re.sub(r"<[^>]+>", "", item).strip()
        if item:
            lines.append(item)
    return "\n".join(lines).strip()


CONFIRMED_EMPTY_BODY_MARKER = "正文状态：原内容确认未填写文字正文。"
BODY_FETCH_STATUSES = {"已获取", "确认无正文", "获取失败", "不适用", "success", "unavailable", "failed"}


def load_body_evidence(source_path: str) -> tuple[str, str]:
    if not source_path.strip():
        return "", "获取失败"
    path = Path(source_path).resolve()
    if not path.is_file():
        raise RuntimeError("正文来源文件不存在")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"正文来源文件无法读取: {exc}") from exc
    if not isinstance(payload, dict):
        raise RuntimeError("正文来源文件格式错误")
    fields = payload.get("fields") if isinstance(payload.get("fields"), dict) else payload
    body_text = str(
        fields.get("正文文本") or fields.get("字幕文本") or fields.get("caption_text")
        or fields.get("transcript") or fields.get("text") or ""
    ).strip()
    body_status = str(fields.get("正文获取状态") or fields.get("字幕状态") or "").strip()
    if body_status and body_status not in BODY_FETCH_STATUSES:
        raise RuntimeError(f"正文获取状态不支持: {body_status}")
    if body_text:
        if body_status not in {"", "已获取", "success"}:
            raise RuntimeError("正文文本与正文获取状态冲突")
        return body_text, "已获取"
    if body_status in {"确认无正文", "unavailable"}:
        return "", body_status
    if body_status in {"已获取", "success"}:
        raise RuntimeError("正文状态为已获取但正文文本为空")
    return "", body_status or "获取失败"


def _workspace_file(workspace_root: Path, raw_path: str, label: str) -> Path:
    path = Path(raw_path).expanduser()
    if not path.is_absolute():
        path = workspace_root / path
    path = path.resolve()
    try:
        path.relative_to(workspace_root.resolve())
    except ValueError as exc:
        raise RuntimeError(f"{label}路径越界") from exc
    if not path.is_file() or path.is_symlink():
        raise RuntimeError(f"{label}不存在或不是普通文件")
    return path


def _load_content_state_executor():
    path = Path(__file__).with_name("内容生产状态执行器.py")
    spec = importlib.util.spec_from_file_location("transcript_content_state_executor", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("内容生产状态校验器不可用")
    module = importlib.util.module_from_spec(spec)
    previous_path = list(sys.path)
    sys.path.insert(0, str(path.parent))
    try:
        spec.loader.exec_module(module)
    except Exception as exc:
        raise RuntimeError("内容生产状态校验器不可用") from exc
    finally:
        sys.path[:] = previous_path
    return module


FINAL_BLOCK_FIELD_PREFIXES = ("最终内容标题：",)


def strip_final_metadata(text: str) -> str:
    """Drop the leading 最终内容标题 line from a 终稿 block.

    The 内容终稿 block is written by design as "one field line + clean body"
    (see 执行流程/内容终稿流程.md).  When it is reused as 发布正文 only the body
    belongs there: keeping the field line would let the review mistake the planned
    content title for the actual body text.  Only leading field lines are removed,
    so a body that legitimately starts with the same words is untouched.
    """
    lines = text.splitlines()
    index = 0
    while index < len(lines):
        stripped = lines[index].strip()
        if not stripped or stripped.startswith(FINAL_BLOCK_FIELD_PREFIXES):
            index += 1
            continue
        break
    return "\n".join(lines[index:]).strip()


def load_confirmed_final_text(
    workspace_root: Path,
    *,
    content_id: str,
    production_record_path: str,
    state_path: str,
) -> tuple[str, int]:
    """Return only the current confirmed final version, never a draft candidate."""
    record_path = _workspace_file(workspace_root, production_record_path, "内容生产记录")
    formal_state_path = _workspace_file(workspace_root, state_path, "内容生产状态")
    state_executor = _load_content_state_executor()
    try:
        state = state_executor.validate_state_file(formal_state_path, workspace_root=workspace_root)
    except Exception as exc:
        raise RuntimeError(f"内容生产状态不可用：{exc}") from exc
    if not isinstance(state, dict) or str(state.get("内容ID") or "").strip() != content_id:
        raise RuntimeError("内容生产状态与当前内容不一致")
    if state.get("当前是否完成") is not True:
        raise RuntimeError("内容生产流程尚未完成")
    stages = state.get("阶段")
    final_stage = stages.get("终稿优化") if isinstance(stages, dict) else None
    if not isinstance(final_stage, dict) or final_stage.get("状态") != "已确认":
        raise RuntimeError("当前终稿尚未确认")
    expected_record = _workspace_file(
        workspace_root, str(state.get("内容生产记录路径") or ""), "状态记录中的内容生产记录"
    )
    if expected_record != record_path:
        raise RuntimeError("内容生产记录与正式状态不一致")
    try:
        final_version = int(final_stage.get("版本"))
        final_text = state_executor._extract_record_version(
            record_path.read_text(encoding="utf-8-sig"), "终稿", final_version
        )
    except Exception as exc:
        raise RuntimeError(f"确认终稿无法读取：{exc}") from exc
    if not final_text:
        raise RuntimeError("已确认终稿不存在或为空")
    body = strip_final_metadata(final_text)
    if not body:
        raise RuntimeError("已确认终稿正文为空")
    return body, final_version


def emit_confirmed_final_fallback(
    args: argparse.Namespace, output_path: Path, *, trigger: str
) -> tuple[bool, str]:
    try:
        final_text, final_version = load_confirmed_final_text(
            Path(args.workspace_root).expanduser().resolve(),
            content_id=str(args.content_id or "").strip(),
            production_record_path=str(getattr(args, "content_production_record_path", "") or ""),
            state_path=str(getattr(args, "content_state_path", "") or ""),
        )
    except RuntimeError as exc:
        return False, f"{trigger}；确认终稿回退失败：{exc}"
    atomic_write_text(output_path, final_text)
    emit_result(
        args,
        output_path,
        "成功",
        输出文件=output_path.name,
        输出路径=str(output_path),
        正文来源="内容生产记录的已确认终稿",
        正文获取状态="已获取",
        source="confirmed_final",
        status="success",
        text_path=str(output_path),
        evidence={
            "content_id": args.content_id,
            "run_id": getattr(args, "run_id", ""),
            "final_version": final_version,
            "fallback_trigger": trigger,
        },
    )
    return True, ""


def load_platform_caption_evidence(
    source_path: str,
    *,
    content_id: str,
    run_id: str = "",
    video_url: str = "",
) -> dict[str, object]:
    if not str(source_path or "").strip():
        return {"source": "platform_caption", "status": "unavailable", "failure_reason": "平台字幕未取得"}
    try:
        payload = read_json_object(Path(source_path))
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as exc:
        return {"source": "platform_caption", "status": "failed", "failure_reason": f"平台字幕结果无法读取：{exc}"}
    return platform_caption_evidence(payload, content_id=content_id, run_id=run_id, video_url=video_url)


def validate_caption_evidence_raw_hash(fact: dict[str, object], *, workspace_root: Path, run_id: str) -> dict[str, object]:
    """Verify the raw-detail bytes before reusing a successful caption fact."""
    evidence = fact.get("evidence")
    raw = evidence.get("raw_detail") if isinstance(evidence, dict) else None
    if fact.get("status") != "success" or not isinstance(raw, dict):
        return fact
    raw_path = _staging_path(workspace_root, str(raw.get("path") or ""), run_id=run_id)
    observed = sha256_bytes(raw_path.read_bytes())
    if observed != str(raw.get("sha256") or "").strip().lower():
        return {**fact, "status": "failed", "failure_reason": "平台字幕原始详情SHA256不一致"}
    return fact


def _xiaohongshu_note_node(payload: object) -> dict[str, object] | None:
    if not isinstance(payload, dict):
        return None
    candidates = (
        payload.get("note"),
        payload.get("item"),
        payload.get("data"),
        payload,
    )
    for candidate in candidates:
        if not isinstance(candidate, dict):
            continue
        nested = candidate.get("note")
        if isinstance(nested, dict) and isinstance(nested.get("video"), dict):
            return nested
        if isinstance(candidate.get("video"), dict):
            return candidate
    return None


def _xiaohongshu_native_note_id(payload: dict[str, object], note: dict[str, object]) -> str:
    for node in (payload, note):
        for key in ("currentNoteId", "noteId", "note_id", "id"):
            value = str(node.get(key) or "").strip()
            if value:
                return value
    return ""


def xiaohongshu_raw_detail_native_id(payload: object) -> str:
    """Return only the native note id needed to bind a staged raw detail."""
    if not isinstance(payload, dict):
        return ""
    note = _xiaohongshu_note_node(payload)
    return _xiaohongshu_native_note_id(payload, note) if note else ""


def load_xiaohongshu_caption_binding(
    binding_json: str,
    *,
    workspace_root: Path,
    content_id: str,
    run_id: str,
    video_url: str,
) -> tuple[dict[str, str], str]:
    """Load an own-content bind receipt without trusting caller-supplied hashes."""
    if not str(binding_json or "").strip():
        return {}, ""
    try:
        binding_path = _staging_path(workspace_root, binding_json, run_id=run_id)
        payload = read_json_object(binding_path)
    except (OSError, UnicodeError, json.JSONDecodeError, RuntimeError, ValueError) as exc:
        return {}, f"平台字幕绑定回执无法读取：{exc}"
    expected_hash = video_url_hash(video_url)
    raw_path = str(payload.get("raw_detail_path") or "").strip()
    raw_hash = str(payload.get("raw_detail_sha256") or "").strip().lower()
    native_id = str(payload.get("native_content_id") or "").strip()
    if (
        payload.get("binding_version") != 1
        or str(payload.get("status") or "").strip() != "platform_caption_bound"
        or str(payload.get("content_id") or "").strip() != str(content_id or "").strip()
        or not str(run_id or "").strip()
        or str(payload.get("run_id") or "").strip() != str(run_id).strip()
        or not expected_hash
        or str(payload.get("url_hash") or "").strip().lower() != expected_hash
        or not raw_path
        or not raw_hash
        or not native_id
    ):
        return {}, "平台字幕绑定回执身份或字段不完整"
    try:
        resolved_raw = _staging_path(workspace_root, raw_path, run_id=run_id)
        if resolved_raw.parent != binding_path.parent:
            return {}, "平台字幕绑定回执原始详情不属于当前绑定目录"
        if sha256_bytes(resolved_raw.read_bytes()) != raw_hash:
            return {}, "平台字幕绑定回执原始详情SHA256不一致"
    except (OSError, RuntimeError) as exc:
        return {}, f"平台字幕绑定回执原始详情不可用：{exc}"
    return {
        "raw_detail_json": str(resolved_raw),
        "raw_detail_sha256": raw_hash,
        "native_content_id": native_id,
    }, ""


def extract_xiaohongshu_caption_url(payload: object) -> tuple[str, bool, str]:
    """Read the documented mediaV2 JSON only; opaque1 is a support marker."""
    note = _xiaohongshu_note_node(payload)
    if note is None:
        return "", False, "小红书原始详情缺少note.video"
    video = note.get("video")
    if not isinstance(video, dict):
        return "", False, "小红书原始详情缺少note.video"
    media_v2 = video.get("mediaV2")
    if not isinstance(media_v2, str) or not media_v2.strip():
        return "", False, "小红书mediaV2缺失或不是JSON字符串"
    try:
        decoded = json.loads(media_v2)
    except json.JSONDecodeError:
        return "", False, "小红书mediaV2不是有效JSON"
    if not isinstance(decoded, dict) or not isinstance(decoded.get("video"), dict):
        return "", False, "小红书mediaV2缺少video对象"
    media_video = decoded["video"]
    opaque = media_video.get("opaque1")
    support = bool(isinstance(opaque, dict) and str(opaque.get("isSupportSubtitle") or "").strip().casefold() == "true")
    subtitles = media_video.get("subtitles")
    if not isinstance(subtitles, dict):
        return "", support, "小红书未提供字幕列表"
    for key in ("source", "zh-CN"):
        items = subtitles.get(key)
        if isinstance(items, list) and items and isinstance(items[0], dict):
            url = str(items[0].get("url") or "").strip()
            if url:
                return url, support, ""
    return "", support, "小红书未提供可用字幕URL"


def materialize_xiaohongshu_platform_caption(
    *,
    workspace_root: Path,
    raw_detail_json: str,
    evidence_json: str,
    content_id: str,
    run_id: str,
    video_url: str,
    native_content_id: str = "",
    expected_raw_sha256: str = "",
) -> dict[str, object]:
    """Build the one staging-only platform-caption fact used before ASR."""
    evidence_path = _staging_path(workspace_root, evidence_json, run_id=run_id)
    result: dict[str, object] = {
        "binding_version": 1,
        "schema_version": 1,
        "source": "platform_caption",
        "status": "unavailable",
        "content_id": str(content_id or "").strip(),
        "run_id": str(run_id or "").strip(),
        "url_hash": video_url_hash(video_url),
        "fields": {"字幕状态": "unavailable"},
        "raw_detail": {},
        "evidence": {
            "content_id": str(content_id or "").strip(),
            "run_id": str(run_id or "").strip(),
            "url_hash": video_url_hash(video_url),
            "raw_detail": {},
            "native_content_id": "",
        },
        "subtitle_url_security": {"status": "not_checked"},
        "failure_reason": "平台字幕未取得",
    }
    try:
        if not str(raw_detail_json or "").strip():
            raise RuntimeError("本轮未取得小红书原始详情（未执行或未命中 Network 详情请求）")
        raw_path = _staging_path(workspace_root, raw_detail_json, run_id=run_id)
        if not raw_path.is_file() or raw_path.is_symlink():
            raise RuntimeError("本轮未取得小红书原始详情（原始详情文件不存在）")
        raw_bytes = raw_path.read_bytes()
        raw_hash = sha256_bytes(raw_bytes)
        expected_hash = str(expected_raw_sha256 or "").strip().lower()
        if not str(content_id or "").strip() or not str(run_id or "").strip() or not video_url_hash(video_url):
            raise RuntimeError("平台字幕绑定字段缺失")
        if not expected_hash:
            raise RuntimeError("平台字幕原始详情SHA256绑定缺失")
        if expected_hash and raw_hash != expected_hash:
            raise RuntimeError("平台字幕原始详情SHA256不一致")
        payload = json.loads(raw_bytes.decode("utf-8"))
        if not isinstance(payload, dict):
            raise RuntimeError("平台字幕原始详情必须是JSON对象")
        observed_native_id = xiaohongshu_raw_detail_native_id(payload)
        expected_native_id = str(native_content_id or "").strip()
        if not expected_native_id or not observed_native_id or observed_native_id != expected_native_id:
            raise RuntimeError("平台字幕原始详情内容身份不一致")
        result["raw_detail"] = {
            "path": raw_path.relative_to(workspace_root.resolve()).as_posix(),
            "sha256": raw_hash,
            "native_content_id": observed_native_id,
        }
        result["evidence"] = {
            "content_id": str(content_id).strip(),
            "run_id": str(run_id).strip(),
            "url_hash": video_url_hash(video_url),
            "raw_detail": dict(result["raw_detail"]),
            "native_content_id": observed_native_id,
        }
        subtitle_url, support, reason = extract_xiaohongshu_caption_url(payload)
        result["subtitle_supported"] = support
        if not subtitle_url:
            raise RuntimeError(reason)
        accepted, unsafe_reason = _safe_public_http_url(subtitle_url)
        result["subtitle_url_sha256"] = video_url_hash(subtitle_url)
        result["subtitle_url_security"] = {
            "status": "accepted" if accepted else "rejected",
            "reason": unsafe_reason,
            "scheme": urllib.parse.urlsplit(subtitle_url).scheme,
            "host": urllib.parse.urlsplit(subtitle_url).hostname or "",
        }
        if not accepted:
            raise RuntimeError(unsafe_reason)
        text = srt_to_text(download_platform_caption_srt(accepted))
        if not text:
            raise RuntimeError("小红书字幕SRT为空")
        result.update({
            "status": "success",
            "text": text,
            "fields": {"字幕状态": "success", "字幕文本": text},
            "failure_reason": "",
        })
    except (OSError, UnicodeError, json.JSONDecodeError, RuntimeError) as exc:
        reason = str(exc)
        failed = any(marker in reason for marker in ("不一致", "不安全", "SHA256", "下载", "HTTP", "公网", "凭证", "体积上限"))
        result.update({
            "status": "failed" if failed else "unavailable",
            "fields": {"字幕状态": "failed" if failed else "unavailable", "失败原因": reason},
            "failure_reason": reason,
        })
    write_json_atomically(evidence_path, result)
    return result


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


def extract_douyin_video_id(note_url: str) -> str:
    return extract_douyin_content_id(note_url)


def find_first_by_paths(node: object, paths: list[list[object]]) -> str:
    for path in paths:
        current = node
        ok = True
        for part in path:
            if isinstance(part, int):
                if not isinstance(current, list) or len(current) <= part:
                    ok = False
                    break
                current = current[part]
            else:
                if not isinstance(current, dict) or part not in current:
                    ok = False
                    break
                current = current[part]
        if ok:
            normalized = normalize_url(current)
            if normalized:
                return normalized
    return ""


def extract_xiaohongshu_note_id(note_url: str) -> str:
    match = re.search(
        r"/(?:explore|discovery/item)/([0-9a-zA-Z_-]+)|/user/profile/[^/?#]+/([0-9a-zA-Z_-]+)",
        note_url,
    )
    return (match.group(1) or match.group(2)) if match else ""


def fetch_xiaohongshu_fresh_video_url(token: str, base_url: str, note_url: str) -> str:
    note_id = extract_xiaohongshu_note_id(note_url)
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
                {"token": token, "noteId": note_id, "note_id": note_id, "url": note_url},
            )
        )
    payload = (call_justoneapi(urls).get("data") or {})
    fresh_url = find_first_by_paths(
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
    )
    if not fresh_url:
        raise RuntimeError("未从小红书详情接口拿到新视频直链")
    return fresh_url


def fetch_douyin_fresh_video_url(token: str, base_url: str, note_url: str) -> str:
    canonical = canonicalize_douyin_content_url(note_url, resolve_short=False)
    video_id = str(canonical.get("content_id") or "")
    if not video_id:
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
        for key in ("videoId", "video_id", "aweme_id", "item_id"):
            raw = ((transfer_data.get("data") or {}).get(key)) or transfer_data.get(key)
            if str(raw or "").strip():
                video_id = str(raw).strip()
                break
    if not video_id:
        raise RuntimeError("抖音 videoId 缺失")
    detail_url = build_api_url(
        base_url,
        "/api/douyin/get-video-detail/v2",
        {"token": token, "videoId": video_id, "video_id": video_id, "aweme_id": video_id, "item_id": video_id},
    )
    payload = (call_justoneapi([detail_url]).get("data") or {})
    fresh_url = find_first_by_paths(
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
    )
    if not fresh_url:
        raise RuntimeError("未从抖音详情接口拿到新视频直链")
    return fresh_url


def refresh_video_url(platform: str, note_url: str, token: str, base_url: str) -> tuple[str, str]:
    platform_name = (platform or "").strip()
    if not token:
        raise RuntimeError("未配置 JUSTONEAPI_TOKEN，无法刷新视频直链")
    if not note_url.strip():
        raise RuntimeError("缺少 note_url，无法刷新视频直链")
    if platform_name == "小红书":
        return fetch_xiaohongshu_fresh_video_url(token, base_url, note_url), "JustOneAPI"
    if platform_name == "抖音":
        return fetch_douyin_fresh_video_url(token, base_url, note_url), "JustOneAPI"
    raise RuntimeError(f"当前平台暂不支持刷新视频直链: {platform_name}")


def write_refresh_meta(meta_path: Path | None, payload: dict[str, object]) -> None:
    if meta_path is None:
        return
    meta_path.parent.mkdir(parents=True, exist_ok=True)
    meta_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def extract_bvid(url: str) -> str:
    patterns = [
        r"(BV[0-9A-Za-z]+)",
        r"/video/(BV[0-9A-Za-z]+)",
    ]
    for pattern in patterns:
        match = re.search(pattern, url)
        if match:
            return match.group(1)
    return ""


def fetch_bilibili_ids(note_url: str) -> tuple[str, str, str]:
    bvid = extract_bvid(note_url)
    if not bvid:
        return "", "", ""
    encoded = urllib.parse.urlencode({"bvid": bvid})
    data = http_get_json(f"https://api.bilibili.com/x/web-interface/view?{encoded}")
    if data.get("code") != 0:
        return bvid, "", ""
    payload = data.get("data", {})
    aid = str(payload.get("aid", "")).strip()
    pages = payload.get("pages") or []
    cid = str(pages[0].get("cid", "")).strip() if pages else ""
    return bvid, aid, cid


def resolve_bilibili_ids(args: argparse.Namespace) -> tuple[str, str, str]:
    bvid = (args.bvid or "").strip()
    aid = (args.aid or "").strip()
    cid = (args.cid or "").strip()
    if cid and (bvid or aid):
        return bvid, aid, cid
    fallback_bvid, fallback_aid, fallback_cid = fetch_bilibili_ids(args.note_url or args.video_url)
    return bvid or fallback_bvid, aid or fallback_aid, cid or fallback_cid


def _bilibili_audio_url_accessible(url: str) -> bool:
    """Probe only the first two bytes; do not download the media locally.

    探测**刻意不带 Referer**：ASR 侧也是拿 URL 自己去拉，两边条件一致才更接近真实情况。
    若给探测补上 Referer，会把校验 Referer 的流误判成可用——不要在这里加 Referer。

    边界：探测跑在客户端本机，实际拉取发生在服务端，两边网络环境并不相同，所以这里的结果
    只是近似。09-17 曾据本机探测的 403 判定「DASH 必然不可用」并改用体积大得多的 html5
    durl；09-18 复测又同时出现「同一批档位连测 3 次全 206」和「抽查 3 条全 403」两种结果，
    **说明 B站 的放行策略随时段与 CDN 节点变化，不能写成绝对结论**。调用方因此采用
    「DASH 优先 + durl 兜底」，并且每次重新解析、不复用旧链接。
    """
    request = urllib.request.Request(
        url,
        headers={"User-Agent": "Mozilla/5.0", "Range": "bytes=0-1"},
        method="GET",
    )
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            response.read(2)
            return response.status in {200, 206}
    except Exception:
        return False


def _bilibili_playurl(extra: dict[str, str], *, bvid: str, aid: str) -> dict:
    params = dict(extra)
    params["bvid" if bvid else "aid"] = bvid or aid
    return http_get_json(
        "https://api.bilibili.com/x/player/playurl?" + urllib.parse.urlencode(params),
        headers={"User-Agent": "Mozilla/5.0", "Accept": "application/json"},
    )


def _bilibili_candidates_from_dash(payload: dict) -> list[str]:
    """按带宽升序返回 DASH 纯音频候选，最省流量的排在前面。

    返回顺序不保证按带宽排序（实测 ``audio[0]`` 是 30232 / 891 kbps 档，最小的是排在
    后面的 30216），所以这里显式排序 —— ASR 只需要能听清，档位越低体积越小。
    """
    audio = ((payload.get("data") or {}).get("dash") or {}).get("audio") or []
    rows = [item for item in audio if isinstance(item, dict)]
    rows.sort(key=lambda item: int(item.get("bandwidth") or 0))
    candidates: list[str] = []
    for item in rows:
        for key in ("baseUrl", "base_url", "backupUrl", "backup_url"):
            value = item.get(key)
            for candidate in (value if isinstance(value, list) else [value]):
                candidate = str(candidate or "").strip()
                if candidate and candidate not in candidates:
                    candidates.append(candidate)
    return candidates


def _bilibili_candidates_from_durl(payload: dict) -> list[str]:
    candidates: list[str] = []
    for item in (((payload.get("data") or {}).get("durl")) or []):
        if not isinstance(item, dict):
            continue
        for key in ("url", "backup_url", "backupUrl"):
            value = item.get(key)
            for candidate in (value if isinstance(value, list) else [value]):
                candidate = str(candidate or "").strip()
                if candidate and candidate not in candidates:
                    candidates.append(candidate)
    return candidates


def fetch_bilibili_audio_url(bvid: str, aid: str, cid: str) -> str:
    """Resolve a current B站 media URL that the ASR provider can download itself.

    ASR（托管与本地回退同形）拿到的只是一个 URL，媒体由服务端自己去拉，因此那个请求里
    **不带 Referer**。托管那条 URL 在生成 ``ASR输入.json`` 时就已经过本函数解析
    （见 ``对标内容数据建档执行器.asr_audio_url_for`` 与
    ``账号后续详细处理批处理._bilibili_audio_url_for_row``），所以两条路吃到的是同一个流。

    台账里的 ``视频下载链接`` **不能直接交给 ASR**：B站 DASH 是音视频分离流，那个链接
    可能只有画面没有声音（实测文件头 ``vide=True`` / ``soun=False``，清晰度 id 是服务端
    增强产物 30032），火山拿到会直接失败（45000000）。两条解析路径的取舍按同一视频实测如下：

    - ``fnval=16``（DASH）：返回**纯音频**，最小档 5.2 MB。不带 Referer 的可达性
      **不稳定** —— 09-17 那批链接全部 403，09-18 同一批档位连测 3 次又全 206。
    - ``fnval=0`` + ``platform=html5``（durl）：CDN 对 html5 播放器放行，可达性更稳；
      但它返回的是**整段音视频合流 mp4**，实测 123.1 MB，把 ``qn`` 调到 16 仍有
      45.2 MB —— 交给 ASR 就等于让服务端下整段视频。

    所以先试 DASH 音频（体积小一个数量级），逐个探测不通再回退 html5 durl。**durl 的
    兜底不能删**：DASH 可达性确实会随 CDN 节点变化。链接签名时效只有约两小时，所以每次
    都必须重新解析，不得复用正式内容列表里保存的旧链接。
    """
    if not cid or not (bvid or aid):
        raise RuntimeError("B站音频流解析失败：缺少 bvid / aid / cid")
    attempts = (
        (
            "dash",
            {"cid": cid, "fnval": "16", "qn": "64", "fnver": "0"},
            _bilibili_candidates_from_dash,
        ),
        (
            "html5",
            {"cid": cid, "fnval": "0", "platform": "html5", "qn": "64", "fnver": "0", "high_quality": "1"},
            _bilibili_candidates_from_durl,
        ),
    )
    failures: list[str] = []
    for label, extra, picker in attempts:
        try:
            payload = _bilibili_playurl(extra, bvid=bvid, aid=aid)
            if payload.get("code") != 0:
                failures.append(f"{label}:{payload.get('message') or payload.get('code')}")
                continue
            for candidate in picker(payload):
                if _bilibili_audio_url_accessible(candidate):
                    return candidate
            failures.append(f"{label}:no_accessible_stream")
        except RuntimeError as exc:
            failures.append(f"{label}:{exc}")
    raise RuntimeError("B站播放接口返回的音频流均不可直接访问（" + "；".join(failures) + "）")


def fetch_bilibili_caption_from_justoneapi(token: str, base_url: str, bvid: str, aid: str, cid: str) -> str:
    params = urllib.parse.urlencode({"token": token, "bvid": bvid, "aid": aid, "cid": cid})
    data = http_get_json(f"{base_url.rstrip('/')}/api/bilibili/get-video-caption/v2?{params}")
    if data.get("code") != 0:
        raise RuntimeError(f"JustOneAPI 返回失败: {data.get('message') or data.get('code')}")
    subtitle_items = (((data.get("data") or {}).get("data")) or [])
    if not subtitle_items:
        raise RuntimeError("JustOneAPI 未返回字幕列表")
    subtitle_url = subtitle_items[0].get("subtitle_url", "")
    if not subtitle_url:
        raise RuntimeError("字幕链接为空")
    subtitle_data = http_get_json(subtitle_url, timeout=60, headers={"User-Agent": "Mozilla/5.0"})
    body_items = subtitle_data.get("body") or []
    text = "\n".join((item.get("content") or "").strip() for item in body_items if (item.get("content") or "").strip())
    if not text.strip():
        raise RuntimeError("字幕内容为空")
    return text.strip()


def fetch_bilibili_caption_from_public_api(bvid: str, aid: str, cid: str, sessdata: str = "") -> str:
    params = {"cid": cid}
    if bvid:
        params["bvid"] = bvid
    elif aid:
        params["aid"] = aid
    else:
        raise RuntimeError("缺少 bvid / aid，无法获取字幕")
    headers = {"User-Agent": "Mozilla/5.0", "Accept": "application/json"}
    if sessdata:
        headers["Cookie"] = f"SESSDATA={sessdata}"
    query = urllib.parse.urlencode(params)
    data = http_get_json(f"https://api.bilibili.com/x/player/v2?{query}", headers=headers)
    if data.get("code") != 0:
        raise RuntimeError(f"B站播放器接口失败: {data.get('message') or data.get('code')}")
    subtitle_block = ((data.get("data") or {}).get("subtitle")) or {}
    subtitle_items = subtitle_block.get("subtitles") or []
    if not subtitle_items:
        raise RuntimeError("B站播放器未返回可用字幕")
    preferred_item = None
    for item in subtitle_items:
        lan = str(item.get("lan") or "").lower()
        lan_doc = str(item.get("lan_doc") or "")
        if "zh" in lan or "中文" in lan_doc:
            preferred_item = item
            break
    if preferred_item is None:
        preferred_item = subtitle_items[0]
    subtitle_url = str(preferred_item.get("subtitle_url") or "").strip()
    if not subtitle_url:
        raise RuntimeError("字幕链接为空")
    if subtitle_url.startswith("//"):
        subtitle_url = "https:" + subtitle_url
    elif subtitle_url.startswith("http://"):
        subtitle_url = "https://" + subtitle_url[len("http://") :]
    subtitle_data = http_get_json(subtitle_url, timeout=60, headers={"User-Agent": "Mozilla/5.0"})
    body_items = subtitle_data.get("body") or []
    text = "\n".join((item.get("content") or "").strip() for item in body_items if (item.get("content") or "").strip())
    if not text.strip():
        raise RuntimeError("字幕内容为空")
    return text.strip()


def resolve_bilibili_caption_ids(note_url: str, video_url: str) -> tuple[str, str, str]:
    """Resolve bvid / aid / cid for a caption lookup without touching the saved video URL."""
    bvid = extract_bvid(note_url) or extract_bvid(video_url)
    if not bvid:
        raise RuntimeError("B站字幕获取失败：无法从链接解析 bvid")
    data = http_get_json(
        "https://api.bilibili.com/x/web-interface/view?" + urllib.parse.urlencode({"bvid": bvid}),
        headers={"User-Agent": "Mozilla/5.0", "Accept": "application/json"},
    )
    if data.get("code") != 0:
        raise RuntimeError(f"B站视频信息接口失败：{data.get('message') or data.get('code')}")
    node = data.get("data") or {}
    cid = str(node.get("cid") or "").strip()
    if not cid:
        raise RuntimeError("B站字幕获取失败：视频信息未返回 cid")
    return bvid, str(node.get("aid") or "").strip(), cid


def materialize_bilibili_platform_caption(
    *,
    workspace_root: Path,
    content_id: str,
    run_id: str,
    video_url: str,
    note_url: str = "",
    evidence_json: str = "",
) -> dict[str, object]:
    """Fetch this run's B站 platform captions and persist them as caption evidence.

    B站 的字幕不落在 Chrome 页面结构化状态里，必须单独取。这里走 JustOneAPI 的
    B站 字幕接口（与公开播放器接口同源），取不到就如实返回 unavailable，由调用方
    按 ``platform_caption > asr`` 的顺序回退；绝不把 ASR 结果冒充成平台字幕。
    """
    root = Path(workspace_root).expanduser().resolve()
    result: dict[str, object] = {
        "schema_version": 1,
        "source": "platform_caption",
        "status": "unavailable",
        "content_id": str(content_id or "").strip(),
        "run_id": str(run_id or "").strip(),
        "url_hash": video_url_hash(video_url),
        "fields": {"字幕状态": "unavailable"},
        "failure_reason": "B站平台字幕未取得",
    }
    try:
        if not str(content_id or "").strip() or not str(run_id or "").strip() or not video_url_hash(video_url):
            raise RuntimeError("平台字幕绑定字段缺失")
        config = load_local_config(root / "本地配置.txt")
        token = str(config.get("JUSTONEAPI_TOKEN") or "").strip()
        if not token:
            raise RuntimeError("未配置 JUSTONEAPI_TOKEN，无法获取 B站 平台字幕")
        base_url = str(config.get("JUSTONEAPI_BASE_URL") or "https://api.justoneapi.com").strip()
        bvid, aid, cid = resolve_bilibili_caption_ids(note_url, video_url)
        text = fetch_bilibili_caption_from_justoneapi(token, base_url, bvid, aid, cid)
        if not text:
            raise RuntimeError("B站平台字幕为空")
        result.update({
            "status": "success",
            "text": text,
            "fields": {"字幕状态": "success", "字幕文本": text},
            "failure_reason": "",
        })
    except (OSError, UnicodeError, json.JSONDecodeError, RuntimeError) as exc:
        reason = str(exc)
        failed = any(marker in reason for marker in ("不一致", "SHA256", "下载", "HTTP", "接口失败", "为空"))
        result.update({
            "status": "failed" if failed else "unavailable",
            "fields": {"字幕状态": "failed" if failed else "unavailable", "失败原因": reason},
            "failure_reason": reason,
        })
    evidence_path = str(evidence_json or "").strip()
    if evidence_path:
        write_json_atomically(Path(evidence_path), result)
    return result


def submit_dashscope_asr(api_key: str, model: str, video_url: str, language: str) -> str:
    base_url = DASHSCOPE_BASE_URL.rstrip("/")
    submit_data = http_post_json(
        f"{base_url}/api/v1/services/audio/asr/transcription",
        {
            "model": model,
            "input": {"file_url": video_url},
            "parameters": {"language": language, "enable_itn": False},
        },
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "X-DashScope-Async": "enable",
        },
    )
    task_id = (((submit_data.get("output") or {}).get("task_id")) or "").strip()
    if not task_id:
        raise RuntimeError("ASR 任务提交失败，未返回 task_id")
    return task_id


def poll_dashscope_asr(api_key: str, task_id: str, *, timeout_seconds: int = 600) -> dict:
    deadline = time.time() + timeout_seconds
    url = f"{DASHSCOPE_BASE_URL.rstrip('/')}/api/v1/tasks/{task_id}"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "X-DashScope-Async": "enable",
    }
    while time.time() < deadline:
        result = http_get_json(url, timeout=60, headers=headers)
        output = result.get("output") or {}
        status = output.get("task_status")
        if status == "SUCCEEDED":
            transcription_url = ((output.get("result") or {}).get("transcription_url") or "").strip()
            if not transcription_url:
                raise RuntimeError("ASR 结果缺少 transcription_url")
            return http_get_json(transcription_url, timeout=60, headers={"User-Agent": "Mozilla/5.0"})
        if status == "FAILED":
            raise RuntimeError(output.get("message") or output.get("code") or "ASR 任务失败")
        time.sleep(3)
    raise RuntimeError("ASR 任务超时")


def extract_asr_text(result_json: dict) -> str:
    transcripts = result_json.get("transcripts") or []
    if not transcripts:
        return ""
    text = "\n".join((item.get("text") or "").strip() for item in transcripts if (item.get("text") or "").strip())
    if text.strip():
        return text.strip()
    sentences = []
    for transcript in transcripts:
        for sentence in transcript.get("sentences") or []:
            content = (sentence.get("text") or "").strip()
            if content:
                sentences.append(content)
    return "\n".join(sentences).strip()


VOLCENGINE_DEFAULT_RESOURCE_ID = "volc.seedasr.auc"
# 首次 + 最多两次重试。
VOLCENGINE_ASR_ATTEMPTS = 3
VOLCENGINE_ASR_RETRY_DELAY_SECONDS = 2.0

VOLCENGINE_STATUS_MEANINGS: dict[str, str] = {
    "20000001": "任务仍在排队",
    "20000002": "任务仍在处理中",
    "20000003": "音频为静音，没有可识别的内容",
    "45000001": "请求参数无效：缺少必填字段或字段值不合法",
    "45000002": "音频为空",
    "45000151": "音频格式不正确",
    "45000030": "请求的资源 ID 未被授予（通常是资源 ID 与账号已开通的服务对不上）",
    "55000031": "火山服务繁忙（服务过载）",
}

class VolcengineASRError(RuntimeError):
    """火山 ASR 失败，携带服务端原始信封，供重试判断与事后追溯。"""

    def __init__(
        self,
        reason: str,
        *,
        status_code: str = "",
        provider_message: str = "",
        request_id: str = "",
        log_id: str = "",
        retryable: bool = False,
        attempts: int = 1,
    ) -> None:
        super().__init__(reason)
        self.status_code = str(status_code or "").strip()
        self.provider_message = str(provider_message or "").strip()
        self.request_id = str(request_id or "").strip()
        self.log_id = str(log_id or "").strip()
        self.retryable = bool(retryable)
        self.attempts = max(1, int(attempts))

    def as_evidence(self) -> dict[str, object]:
        """失败证据：够复核、够提工单，且不含任何密钥。"""
        return {
            "status_code": self.status_code,
            "provider_message": self.provider_message,
            "request_id": self.request_id,
            "log_id": self.log_id,
            "retryable": self.retryable,
            "attempts": self.attempts,
        }


def _volcengine_envelope(payload: Mapping[str, object] | None) -> tuple[str, str, str]:
    """Read code/message/reqid from a Volcengine envelope.

    The service nests them under ``header``; some payloads put them at the top
    level, so both shapes are accepted.
    """
    body = payload if isinstance(payload, Mapping) else {}
    header = body.get("header") if isinstance(body.get("header"), Mapping) else {}
    code = str(header.get("code") or body.get("code") or "").strip()
    message = str(header.get("message") or body.get("message") or "").strip()
    request_id = str(header.get("reqid") or body.get("reqid") or "").strip()
    return code, message, request_id


def classify_volcengine_failure(
    exc: Exception,
    *,
    status: str = "",
    payload: Mapping[str, object] | None = None,
    log_id: str = "",
) -> VolcengineASRError:
    """Turn one Volcengine failure into a real reason plus a retry verdict.

    Classification uses only the status code and message the service returned.
    A bare ``HTTP 403`` text is never treated as evidence about the video
    source: that conflated auth/quota problems with origin-server problems.
    """
    code, message, request_id = _volcengine_envelope(payload)
    status_code = str(status or "").strip() or code
    provider_message = message or code
    detail = provider_message or str(exc)
    lowered = f"{provider_message} {exc}".lower()
    evidence = {
        "status_code": status_code,
        "provider_message": provider_message,
        "request_id": request_id,
        "log_id": log_id,
    }

    if (
        isinstance(exc, (TimeoutError, socket.timeout))
        or isinstance(getattr(exc, "reason", None), (TimeoutError, socket.timeout))
        or "timed out" in lowered
        or "timeout" in lowered
    ):
        return VolcengineASRError(f"网络读取超时：{detail}", retryable=True, **evidence)
    if status_code.startswith("550"):
        return VolcengineASRError(
            f"火山服务端错误（状态码 {status_code}）：{VOLCENGINE_STATUS_MEANINGS.get(status_code) or detail}",
            retryable=True,
            **evidence,
        )
    if status_code in {"20000001", "20000002"}:
        return VolcengineASRError(
            f"火山仍在处理这次转写（状态码 {status_code}："
            f"{VOLCENGINE_STATUS_MEANINGS[status_code]}），稍后重试。",
            retryable=True,
            **evidence,
        )
    if status_code == "20000003":
        # 静音素材：纯音乐、无人声视频都会命中，实测很常见。落进下面的兜底分支
        # 会被写成「火山拒绝了本次转写」，把用户引去查火山账号 —— 与本次失败无关。
        return VolcengineASRError(
            "这段素材里没有检测到人声（火山状态码 20000003）：可能是纯音乐、静音，"
            "或这个视频本来就没有口播。换一段有人声口播的素材即可；"
            "如果该视频确实没有语音，提取不到文案属正常。"
            f"（火山原话：{detail}）",
            **evidence,
        )
    if status_code == "45000030" or "not granted" in lowered:
        return VolcengineASRError(
            "火山拒绝本次转写（状态码 45000030）：请求的资源 ID 未被授予。"
            "先核对 VOLCENGINE_SPEECH_RESOURCE_ID 是否与账号已开通的服务一致，"
            f"而不是急着认为服务没开通（控制台「快速接入」示例用的是 volc.seedasr.auc；服务端原话：{detail}）",
            **evidence,
        )
    if status_code.startswith("450"):
        return VolcengineASRError(
            f"火山拒绝本次转写（状态码 {status_code}）：{VOLCENGINE_STATUS_MEANINGS.get(status_code) or detail}",
            **evidence,
        )
    if status_code in {"401", "403"} or any(
        token in lowered for token in ("api key", "apikey", "unauthorized", "鉴权", "密钥")
    ):
        return VolcengineASRError(f"火山鉴权失败（状态码 {status_code or 'HTTP 403'}）：{detail}", **evidence)
    if not isinstance(exc, urllib.error.HTTPError) and not status_code:
        # 连接被重置、DNS 失败一类：没有状态码，值得再试一次。
        return VolcengineASRError(f"网络请求失败：{detail}", retryable=True, **evidence)
    return VolcengineASRError(
        f"火山拒绝了本次转写（状态码 {status_code or '未知'}）：{detail}", **evidence
    )


def run_selected_local_asr(
    workspace_root: str | Path,
    *,
    video_url: str,
    provider: str,
    platform: str = "",
    bvid: str = "",
    aid: str = "",
    cid: str = "",
    max_attempts: int = VOLCENGINE_ASR_ATTEMPTS,
) -> dict[str, str]:
    """Run the selected local ASR provider, retrying only recoverable failures.

    Network hiccups and Volcengine-side errors are retried up to
    ``max_attempts`` (default: one attempt plus two retries).  Auth, unauthorised
    resource and parameter errors fail immediately, because retrying them
    cannot change the outcome and only burns quota.
    """
    if provider != "volcengine":
        raise RuntimeError("asr_provider_invalid:volcengine_required")
    normalized_url = normalize_url(video_url)
    if not normalized_url:
        raise RuntimeError("asr_video_url_missing")
    if platform == "小红书" and is_fragmented_xiaohongshu_url(normalized_url):
        # 分片地址火山必拒（45000151），而且签名绑在自己路径上、改不出 .mp4（实测 403）。
        # 重试 3 次只会白烧额度，直接给出可执行的失败原因。
        raise RuntimeError(
            "视频直链是 .m4s 分片，火山会拒（45000151 音频格式不正确）；"
            "请重开该笔记页取 .mp4 直链（<video> 的 currentSrc）后再试"
        )
    if platform == "抖音":
        rejection = douyin_asr_url_rejection(normalized_url)
        if rejection:
            raise RuntimeError(rejection)
    if platform == "B站":
        normalized_url = fetch_bilibili_audio_url(bvid, aid, cid)
    config = load_local_config(Path(workspace_root).expanduser().resolve() / "本地配置.txt")
    configured_key = config.get("VOLCENGINE_SPEECH_API_KEY", "").strip()
    if not configured_key:
        # 2026-11-12：内置试用通道整条删掉了，转写只能用用户自己的火山 Key。
        raise RuntimeError(
            "没填火山引擎语音识别 Key，转写做不了。\n"
            "  在「本地配置.txt」里填一行 VOLCENGINE_SPEECH_API_KEY=<你的 Key> 再跑；\n"
            "  不填也可以，跳过转写这一步，其它分析照常。"
        )
    api_key = configured_key
    endpoint = (config.get("VOLCENGINE_SPEECH_API_URL") or VOLCENGINE_ASR_URL).strip()
    resource_id = str(
        config.get("VOLCENGINE_SPEECH_RESOURCE_ID") or VOLCENGINE_DEFAULT_RESOURCE_ID
    ).strip()
    app_id = str(config.get("VOLCENGINE_SPEECH_APP_ID") or "").strip()

    attempts = max(1, int(max_attempts))
    if platform == "抖音":
        # 创作者中心 work_list 给的 cold 域名直链火山能拉取（2026-11-04 实测）；
        # 公开页那组 web 域名会被判 45000006。先试 URL，失败再本机下载直传。
        try:
            text = submit_volcengine_asr(
                api_key,
                normalized_url,
                endpoint,
                resource_id=resource_id,
                app_id=app_id,
            )
        except VolcengineASRError as exc:
            if exc.status_code not in {"45000006", "45000002", "45000151"}:
                raise
            text = submit_volcengine_asr_data(
                api_key,
                download_media_bytes(normalized_url, platform),
                endpoint,
                resource_id=resource_id,
                app_id=app_id,
            )
        return {
            "provider": provider,
            "model": config.get("VOLCENGINE_SPEECH_MODEL", "bigmodel"),
            "video_url": normalized_url,
            "resource_id": resource_id,
            "attempts": 1,
            "text": text,
        }
    last_error: VolcengineASRError | None = None
    for attempt in range(1, attempts + 1):
        try:
            text = submit_volcengine_asr(
                api_key,
                normalized_url,
                endpoint,
                resource_id=resource_id,
                app_id=app_id,
            )
        except VolcengineASRError as exc:
            exc.attempts = attempt
            last_error = exc
            if exc.status_code in {"45000006", "45000002", "45000151"}:
                # URL 拉取失败时，同一份文件本机下载后直传，不再整条失败。
                text = submit_volcengine_asr_data(
                    api_key,
                    download_media_bytes(normalized_url, platform),
                    endpoint,
                    resource_id=resource_id,
                    app_id=app_id,
                )
                return {
                    "provider": provider,
                    "model": config.get("VOLCENGINE_SPEECH_MODEL", "bigmodel"),
                    "video_url": normalized_url,
                    "resource_id": resource_id,
                    "attempts": attempt,
                    "text": text,
                }
            if not exc.retryable or attempt >= attempts:
                raise
        else:
            if text:
                return {
                    "provider": provider,
                    "model": config.get("VOLCENGINE_SPEECH_MODEL", "bigmodel"),
                    "video_url": normalized_url,
                    "resource_id": resource_id,
                    "attempts": attempt,
                    "text": text,
                }
            last_error = VolcengineASRError("火山 ASR 返回为空", retryable=True, attempts=attempt)
            if attempt >= attempts:
                raise last_error
        if attempt < attempts:
            time.sleep(VOLCENGINE_ASR_RETRY_DELAY_SECONDS)
    raise last_error or VolcengineASRError("火山 ASR 执行失败")


def _macos_physical_node() -> int | None:
    """macOS：直接问真网卡 en0 的 MAC，拿不到返回 None。

    `uuid.getnode()` 取的是「系统里第一块网络接口」，而 Apple 芯片的 Mac 上
    `anpi0/anpi1/...` 这类内部口排在真网卡 en0 前面；换一套 Python 环境就可能落到
    另一块接口，同一台电脑因此算出两个机器码（2026-10-08 实测）。只问 en0 可以
    避开这个漂移；任何异常都吞掉，交给下面的回退。
    """
    try:
        out = subprocess.run(
            ["/sbin/ifconfig", "en0"],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=2,
        ).stdout.decode("utf-8", "replace")
    except Exception:
        return None
    for line in out.splitlines():
        parts = line.split()
        if len(parts) >= 2 and parts[0].lower() == "ether":
            try:
                return int(parts[1].replace(":", ""), 16)
            except ValueError:
                return None
    return None


def _run_capture(command: list[str]) -> str:
    """跑一条只读命令、返回 stdout 文本；任何异常都吞掉、返回空串。"""
    try:
        return subprocess.run(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=2,
        ).stdout.decode("utf-8", "replace")
    except Exception:
        return ""


def _platform_machine_seed() -> str:
    """按平台找一个**稳定的**系统级标识；找不到返回空串。

    只在「真网卡 MAC 也拿不到」时才会走到这里。下面三样都比 `uuid.getnode()` 的
    随机值靠谱，而且不随运行变化：

    - macOS：`ioreg` 的 `IOPlatformUUID`（每台机器一个，重装系统才会变）
    - Linux：`/etc/machine-id`（systemd 生成）或 `/var/lib/dbus/machine-id`
    - Windows：主板 UUID（`wmic` 在新版 Windows 已弃用，改用 PowerShell 的 CIM 接口）
    """
    system = platform.system()
    if system == "Darwin":
        text = _run_capture(["/usr/sbin/ioreg", "-rd1", "-c", "IOPlatformExpertDevice"])
        for line in text.splitlines():
            if "IOPlatformUUID" in line and "=" in line:
                # 形如：  "IOPlatformUUID" = "9C8A3F2E-1B4D-4E59-...."
                value = line.split("=", 1)[1].strip().strip('"').strip()
                if value:
                    return value.lower()
        return ""
    if system == "Windows":
        text = _run_capture([
            "powershell", "-NoProfile", "-Command",
            "(Get-CimInstance Win32_ComputerSystemProduct).UUID",
        ])
        value = text.strip().splitlines()[0].strip() if text.strip() else ""
        # 全 F / 全 0 是「取不到」的占位值，不能当标识用。
        if value and value.upper() not in (
            "FFFFFFFF-FFFF-FFFF-FFFF-FFFFFFFFFFFF",
            "00000000-0000-0000-0000-000000000000",
        ):
            return value.lower()
        return ""
    for candidate in ("/etc/machine-id", "/var/lib/dbus/machine-id"):
        try:
            value = Path(candidate).read_text(encoding="utf-8").strip()
        except Exception:
            continue
        if value:
            return value.lower()
    return ""


def _persisted_machine_seed() -> str:
    """最后的兜底：在工作区 `.meiling/` 里放一个随机标识，第一次生成后一直用它。

    只在平台级标识也拿不到时才走到这里 —— 真机基本用不到（例如没有网卡、也没有
    machine-id 的容器）。用独占创建，避免两个脚本首次同时运行时各写一个值。
    写不进去就返回空串：上报是副产品，绝不因此抛异常。
    """
    # 脚本都在 `<工作区根>/执行单元/` 下，往上两级就是工作区根。
    path = Path(__file__).resolve().parent.parent / ".meiling" / "设备标识.json"
    for _ in range(2):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            payload = None
        if isinstance(payload, dict):
            value = str(payload.get("seed") or "").strip()
            if value:
                return value
        value = uuid.uuid4().hex
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("x", encoding="utf-8") as handle:
                handle.write(json.dumps({"seed": value}, ensure_ascii=False))
            return value
        except FileExistsError:
            continue
        except Exception:
            return ""
    return ""


def machine_fingerprint() -> str:
    """机器码：`dev-` + sha256(`<机器标识>:<系统>:<架构>`) 前 32 位。

    **与 `使用统计上报.py`、`获取视频文案执行器.py`、`诊断报告上报.py` 三处逐字一致**
    （改一处必须三处一起改）—— 算出不同的值，服务端会把同一台机器算成两个「用户」。

    标识取值顺序（**前两步保持原样，已有用户的历史不断档**）：
    1. macOS 真网卡 en0 的 MAC，其它系统用 `uuid.getnode()`；
    2. 拿不到真 MAC（`uuid.getnode()` 返回带多播位的随机值）时，改问平台级标识；
    3. 平台级标识也拿不到，才落到工作区里的持久标识。
    仍然什么都取不到时返回空串：宁可空，也**不**返回每次运行都变的假号。
    """
    node = _macos_physical_node() if platform.system() == "Darwin" else None
    if node is None:
        node = uuid.getnode()
    if (node >> 40) & 0x01:
        # 拿不到真 MAC：先找平台级标识，再落到工作区里的持久标识。
        seed = _platform_machine_seed() or _persisted_machine_seed()
        if not seed:
            return ""
    else:
        seed = f"{node:012x}"
    raw = f"{seed}:{platform.system()}:{platform.machine()}"
    return "dev-" + hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]


def _volcengine_headers(
    api_key: str,
    endpoint: str,
    *,
    resource_id: str,
    app_id: str,
    request_id: str,
) -> dict[str, str]:
    headers = {
        "Content-Type": "application/json",
        # 必须显式设 UA。Python 的默认 UA 是 `Python-urllib/3.x`，
        # 有些站点（比如带 Cloudflare 的）有一条按浏览器特征的拦截规则会命中它
        # （403 `error code: 1010`，实测大写 P 的 `Python-urllib/*` 被拦、
        # `Mozilla/5.0` 放行）。本仓库其它联网模块同样写 Mozilla/5.0。
        "User-Agent": "Mozilla/5.0",
        "X-Api-Key": api_key,
        "X-Api-Resource-Id": resource_id,
        "X-Api-Request-Id": request_id,
        "X-Api-Sequence": "-1",
    }
    if app_id:
        headers["X-Api-App-Key"] = app_id
    return headers


def _post_volcengine_asr(
    api_key: str,
    endpoint: str,
    audio_payload: dict[str, str],
    *,
    resource_id: str,
    app_id: str,
    request_id: str,
) -> str:
    headers = _volcengine_headers(
        api_key,
        endpoint,
        resource_id=resource_id,
        app_id=app_id,
        request_id=request_id,
    )
    request = urllib.request.Request(
        endpoint,
        data=json.dumps({
            "user": {"uid": request_id},
            "audio": audio_payload,
            "request": {"model_name": "bigmodel"},
        }).encode("utf-8"),
        headers=headers,
        method="POST",
    )
    payload: dict[str, object] = {}
    status = ""
    log_id = ""
    try:
        with urllib.request.urlopen(request, timeout=180) as response:
            raw = response.read().decode("utf-8", errors="replace")
            decoded = json.loads(raw) if raw else {}
            payload = decoded if isinstance(decoded, dict) else {}
            status = str(response.headers.get("X-Api-Status-Code") or "").strip()
            log_id = str(response.headers.get("X-Tt-Logid") or "").strip()
    except Exception as exc:
        error_payload: dict[str, object] = {}
        if isinstance(exc, urllib.error.HTTPError):
            try:
                raw_error = exc.read().decode("utf-8", errors="replace")
                decoded_error = json.loads(raw_error) if raw_error else {}
                if isinstance(decoded_error, dict):
                    error_payload = decoded_error
            except Exception:
                pass
            status = str(exc.headers.get("X-Api-Status-Code") or exc.code or "").strip()
            log_id = str(exc.headers.get("X-Tt-Logid") or "").strip()
        raise classify_volcengine_failure(
            exc,
            status=status,
            payload=error_payload,
            log_id=log_id,
        ) from exc
    if status and status != "20000000":
        raise classify_volcengine_failure(
            RuntimeError("provider_status"),
            status=status,
            payload=payload,
            log_id=log_id,
        )
    result = payload.get("result") if isinstance(payload.get("result"), Mapping) else {}
    text = "\n".join(
        str(item.get("text") or "").strip()
        for item in result.get("utterances", [])
        if isinstance(item, Mapping) and str(item.get("text") or "").strip()
    ).strip()
    final_text = text or str(result.get("text") or payload.get("text") or "").strip()
    if not final_text:
        raise RuntimeError("火山 ASR 返回为空")
    return final_text


def submit_volcengine_asr(
    api_key: str,
    video_url: str,
    endpoint: str,
    *,
    resource_id: str = VOLCENGINE_DEFAULT_RESOURCE_ID,
    app_id: str = "",
) -> str:
    """Run one URL transcription with the local Volcano Speech key.

    The App ID has to travel with the API key.  Without ``X-Api-App-Key`` the
    service answers 45000030 "requested resource not granted" even when the
    console shows the service as activated, so a missing App ID looks exactly
    like a missing subscription.
    """
    request_id = hashlib.sha256(video_url.encode("utf-8")).hexdigest()[:32]
    return _post_volcengine_asr(
        api_key,
        endpoint,
        {"url": video_url, "format": "mp4"},
        resource_id=resource_id,
        app_id=app_id,
        request_id=request_id,
    )


def submit_volcengine_asr_data(
    api_key: str,
    audio_bytes: bytes,
    endpoint: str,
    *,
    resource_id: str = VOLCENGINE_DEFAULT_RESOURCE_ID,
    app_id: str = "",
) -> str:
    """把本机下载的音视频以 base64 直传火山。

    2026-11-04 实测：火山的 URL 拉取功能本身正常（同一账号拉 douyinpic 图片
    会正常返回 45000151 格式错），但抖音视频 CDN（`douyinvod.com`）的签名地址
    现在会被服务端判定为「拉不到」，返回 `45000006`；换任何 play_addr 或公开
    mp3 样本都不行。本机下载同一份文件后 base64 直传可以正常转写，因此抖音
    视频文案走这条不依赖火山访问第三方 CDN 的路径。
    """
    encoded = base64.b64encode(audio_bytes).decode("ascii")
    return _post_volcengine_asr(
        api_key,
        endpoint,
        {"data": encoded},
        resource_id=resource_id,
        app_id=app_id,
        request_id=uuid.uuid4().hex,
    )


def probe_media_url(video_url: str) -> str:
    parsed = urllib.parse.urlsplit(str(video_url or "").strip())
    if parsed.scheme in {"http", "https"} and parsed.hostname:
        return "视频 URL 格式有效"
    return "视频 URL 格式无效"


def run_dashscope_asr_with_retry(
    api_key: str,
    model: str,
    video_url: str,
    language: str,
    *,
    retry_count: int,
    retry_delay_seconds: float,
) -> str:
    last_error: Exception | None = None
    for attempt in range(1, max(1, retry_count) + 1):
        try:
            task_id = submit_dashscope_asr(api_key, model, video_url, language)
            result_json = poll_dashscope_asr(api_key, task_id)
            transcript_text = extract_asr_text(result_json)
            if transcript_text:
                return transcript_text
            last_error = RuntimeError("ASR 返回为空")
        except Exception as exc:  # noqa: BLE001
            code = permission_error_code(exc, "network")
            if code:
                raise RuntimeError(code) from exc
            last_error = exc
        if attempt < max(1, retry_count):
            time.sleep(max(0.0, retry_delay_seconds))
    raise RuntimeError(str(last_error) if last_error else "ASR 执行失败")


def load_batch_asr_result(
    result_path: str,
    *,
    content_id: str,
    workspace_root: Path,
    expected_run_id: str = "",
    expected_video_url: str = "",
    expected_url_hash: str = "",
    expected_idempotency_key: str = "",
) -> tuple[str, str]:
    """Read a persisted one-item transcribe_video_batch result without exposing tickets.

    ``expected_idempotency_key`` 必须由生成该批次的调用方传入：不同链路（我方内容 /
    对标内容）的键命名空间不同，本执行器无法凭内容自身推导出对方的键。

    返回值保持既有的 ``(正文, 错误)`` 二元组不变；回执要标注的取数来源另由
    ``read_asr_source_label`` 单独读取，不污染这个已被多处依赖的契约。
    """
    path = Path(result_path).expanduser()
    if not path.is_absolute():
        path = workspace_root / path
    try:
        path = path.resolve()
        relative = path.relative_to(workspace_root.resolve())
    except ValueError:
        return "", "ASR批次结果路径越界"
    if not relative.as_posix().startswith(".meiling/staging/"):
        return "", "ASR批次结果必须位于本轮暂存目录"
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        return "", f"ASR批次结果无法读取：{exc}"
    try:
        normalized = validate_batch_asr_payload(
            payload,
            content_id=content_id,
            expected_run_id=expected_run_id,
            expected_video_url=expected_video_url,
            expected_url_hash=expected_url_hash,
            expected_idempotency_key=expected_idempotency_key,
        )
    except RuntimeError as exc:
        return "", str(exc)
    item = normalized["items"][0]
    if item["status"] == "failed":
        return "", str(item.get("error") or item.get("reason") or item.get("error_code") or "ASR 执行失败")
    return str(item["text"]), ""


def read_asr_source_label(result_path: str, *, workspace_root: Path) -> str:
    """读出这条 ASR 结果文件声明的真实来源，仅供回执标注，不参与业务校验。

    托管侧与本地回退侧写出的结果文件同名同形，靠结构分不出来；差别只在回退方
    额外声明了 ``local_fallback`` / ``provider``。读不到就返回空串，由调用方决定
    兜底文案——绝不硬编码成某一侧，否则本地回退会被记成托管调用。
    """
    path = Path(result_path or "").expanduser()
    if not path.is_absolute():
        path = workspace_root / path
    try:
        payload = json.loads(path.resolve().read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return ""
    if not isinstance(payload, Mapping):
        return ""
    return asr_source_label(payload)


def asr_source_label(normalized: Mapping[str, object]) -> str:
    """把「这一笔 ASR 究竟由谁完成」翻译成回执文案，不得硬编码。

    托管侧与本地回退侧写出的结果文件同名同形，差别只在回退方额外声明了
    ``local_fallback`` / ``provider``。回执必须如实反映来源，否则本地回退会被
    记成托管调用，审计和计费都对不上账。
    """
    if normalized.get("local_fallback") is True:
        provider = str(normalized.get("provider") or "").strip()
        return f"本地火山 ASR 直连回退（{provider}）" if provider else "本地火山 ASR 直连回退"
    return "媒灵托管火山 URL ASR"


ASR_SENSITIVE_FIELDS = frozenset({
    "ticket", "asr_ticket", "token", "access_token", "api_key", "apikey", "key",
})


def asr_idempotency_key(run_id: str, content_id: str, video_url: str) -> str:
    url_hash = video_url_hash(video_url)
    return hashlib.sha256(
        f"own-content:{str(run_id).strip()}:{str(content_id).strip()}:{url_hash}".encode("utf-8")
    ).hexdigest()


def contains_asr_sensitive_fields(value: object) -> bool:
    if isinstance(value, dict):
        if any(str(key).strip().casefold() in ASR_SENSITIVE_FIELDS for key in value):
            return True
        return any(contains_asr_sensitive_fields(child) for child in value.values())
    if isinstance(value, list):
        return any(contains_asr_sensitive_fields(child) for child in value)
    return False


def normalize_asr_envelope(envelope: object) -> dict[str, object]:
    """Extract the service payload from one raw MCP envelope without accepting credentials."""
    candidate = envelope
    if isinstance(envelope, dict) and isinstance(envelope.get("structuredContent"), dict):
        candidate = envelope["structuredContent"]
    elif isinstance(envelope, dict) and isinstance(envelope.get("content"), list):
        texts = [
            item.get("text")
            for item in envelope["content"]
            if isinstance(item, dict) and isinstance(item.get("text"), str)
        ]
        if len(texts) != 1:
            raise RuntimeError("ASR原始回执格式无效")
        try:
            candidate = json.loads(texts[0])
        except json.JSONDecodeError as exc:
            raise RuntimeError("ASR原始回执格式无效") from exc
    if not isinstance(candidate, dict) or "structuredContent" in candidate or "content" in candidate:
        raise RuntimeError("ASR批次结果格式无效")
    if contains_asr_sensitive_fields(candidate):
        raise RuntimeError("ASR批次结果包含敏感凭证字段")
    return dict(candidate)


def validate_batch_asr_payload(
    payload: object,
    *,
    content_id: str,
    expected_run_id: str,
    expected_video_url: str = "",
    expected_url_hash: str = "",
    expected_idempotency_key: str = "",
) -> dict[str, object]:
    """Validate the sole normalized result contract shared by ASR write-back and recovery."""
    if not isinstance(payload, dict):
        raise RuntimeError("ASR批次结果格式无效")
    if contains_asr_sensitive_fields(payload):
        raise RuntimeError("ASR批次结果包含敏感凭证字段")
    run_id = str(expected_run_id or "").strip()
    if not run_id or str(payload.get("run_id") or "").strip() != run_id:
        raise RuntimeError("ASR批次结果运行标识不一致")
    expected_content_id = str(content_id or "").strip()
    if not expected_content_id:
        raise RuntimeError("ASR批次结果内容身份缺失")
    expected_hash = str(expected_url_hash or "").strip().lower()
    expected_url = str(expected_video_url or "").strip()
    if expected_url and not expected_hash:
        expected_hash = video_url_hash(expected_url)
    if not expected_hash:
        raise RuntimeError("ASR批次结果视频身份缺失")
    expected_key = str(expected_idempotency_key or "").strip()
    if not expected_key:
        expected_key = asr_idempotency_key(run_id, expected_content_id, expected_url)
    if str(payload.get("idempotency_key") or "").strip() != expected_key:
        raise RuntimeError("ASR批次结果幂等键不一致")
    items = payload.get("items")
    if not isinstance(items, list) or len(items) != 1 or not isinstance(items[0], dict):
        raise RuntimeError("ASR批次结果必须包含单个内容项")
    item = dict(items[0])
    if str(item.get("content_id") or "").strip() != expected_content_id:
        raise RuntimeError("ASR批次结果内容身份不一致")
    if str(item.get("url_hash") or "").strip().lower() != expected_hash:
        raise RuntimeError("ASR批次结果视频身份不一致")
    status = str(item.get("status") or "").strip().lower()
    if status not in {"success", "failed"}:
        raise RuntimeError("ASR批次结果状态无效")
    transcript = str(item.get("text") or item.get("transcript") or "").strip()
    if status == "success" and not transcript:
        raise RuntimeError("ASR 返回为空")
    normalized: dict[str, object] = {
        "run_id": run_id,
        "content_id": expected_content_id,
        "idempotency_key": expected_key,
        "items": [{
            "content_id": expected_content_id,
            "url_hash": expected_hash,
            "status": status,
            **({"text": transcript} if status == "success" else {
                "error": str(item.get("error") or item.get("reason") or item.get("error_code") or "ASR 执行失败")
            }),
        }],
    }
    return normalized


def emit_result(
    args: argparse.Namespace,
    output_path: Path,
    legacy_status: str,
    **details: object,
) -> None:
    payload = {
        "状态": legacy_status,
        "content_id": args.content_id,
        "output_path": str(output_path),
        **details,
    }
    result_output = str(getattr(args, "result_output_json", "") or "").strip()
    if result_output:
        result_path = Path(result_output).expanduser().resolve()
        atomic_write_bytes(result_path, (json.dumps(payload, ensure_ascii=False, indent=2) + "\n").encode("utf-8"))
    print(json.dumps(payload, ensure_ascii=False))


def _main(args: argparse.Namespace | None = None) -> int:
    args = args or parse_args()
    output_path = Path(args.output_path).resolve()
    try:
        args.result_output_json = validate_result_output(
            getattr(args, "result_output_json", ""),
            Path(args.workspace_root).expanduser().resolve(),
            str(getattr(args, "run_id", "") or "").strip(),
        )
    except RuntimeError as exc:
        print(json.dumps({"状态": "失败", "content_id": args.content_id, "失败原因": str(exc)}, ensure_ascii=False))
        return 1
    output_path.parent.mkdir(parents=True, exist_ok=True)
    refresh_meta_path = Path(args.refresh_meta_path).resolve() if args.refresh_meta_path else None
    current_video_url = (args.video_url or "").strip()

    if args.media_type != "视频":
        try:
            body_text, body_status = load_body_evidence(args.body_source_json)
        except RuntimeError as exc:
            emit_result(args, output_path, "失败", 失败原因=str(exc))
            return 1
        if body_status == "确认无正文":
            atomic_write_text(output_path, CONFIRMED_EMPTY_BODY_MARKER)
            emit_result(
                args,
                output_path,
                "成功",
                输出文件=output_path.name,
                输出路径=str(output_path),
                正文来源="原内容确认无文字正文",
                正文获取状态=body_status,
            )
            return 0
        if not body_text:
            emit_result(args, output_path, "失败", 失败原因="页面真实正文为空且未确认原内容无正文")
            return 1
        atomic_write_text(output_path, body_text)
        emit_result(
            args,
            output_path,
            "成功",
            输出文件=output_path.name,
            输出路径=str(output_path),
            正文来源="页面真实正文",
            正文获取状态="已获取",
            source="formal_transcript",
            status="success",
            text_path=str(output_path),
            evidence={"content_id": args.content_id, "run_id": getattr(args, "run_id", "")},
        )
        return 0
    if args.dry_run:
        emit_result(
            args,
            output_path,
            "成功",
            输出文件=output_path.name,
            输出路径=str(output_path),
        )
        return 0

    # A caption/body acquired in the current Chrome/API run is stronger than
    # ASR.  Consume it first and leave ASR entirely untouched on success.
    caption_fact: dict[str, object]
    raw_detail_path = str(getattr(args, "raw_detail_json", "") or "").strip()
    raw_detail_sha256 = str(getattr(args, "raw_detail_sha256", "") or "").strip()
    native_content_id = str(getattr(args, "native_content_id", "") or "").strip()
    evidence_path = str(getattr(args, "platform_caption_evidence_json", "") or "").strip()
    if args.platform == "小红书":
        evidence_path = evidence_path or str(output_path.parent / "platform-caption-evidence.json")
        existing_evidence = _staging_path(
            Path(args.workspace_root).resolve(), evidence_path,
            run_id=str(getattr(args, "run_id", "") or ""),
        )
        if existing_evidence.is_file():
            caption_fact = load_platform_caption_evidence(
                str(existing_evidence),
                content_id=str(args.content_id or "").strip(),
                run_id=str(getattr(args, "run_id", "") or "").strip(),
                video_url=current_video_url,
            )
            try:
                caption_fact = validate_caption_evidence_raw_hash(
                    caption_fact,
                    workspace_root=Path(args.workspace_root).resolve(),
                    run_id=str(getattr(args, "run_id", "") or ""),
                )
            except (OSError, RuntimeError) as exc:
                caption_fact = {"source": "platform_caption", "status": "failed", "failure_reason": f"平台字幕原始详情不可用：{exc}"}
        else:
            binding, binding_error = load_xiaohongshu_caption_binding(
                str(getattr(args, "platform_caption_binding_json", "") or "").strip(),
                workspace_root=Path(args.workspace_root).resolve(),
                content_id=str(args.content_id or "").strip(),
                run_id=str(getattr(args, "run_id", "") or "").strip(),
                video_url=current_video_url,
            )
            if binding:
                raw_detail_path = binding["raw_detail_json"]
                raw_detail_sha256 = binding["raw_detail_sha256"]
                native_content_id = binding["native_content_id"]
            caption_fact = materialize_xiaohongshu_platform_caption(
                workspace_root=Path(args.workspace_root).resolve(),
                raw_detail_json=raw_detail_path,
                evidence_json=evidence_path,
                content_id=str(args.content_id or "").strip(),
                run_id=str(getattr(args, "run_id", "") or "").strip(),
                video_url=current_video_url,
                native_content_id=native_content_id,
                expected_raw_sha256=raw_detail_sha256,
            )
            if binding_error:
                caption_fact.update({
                    "status": "failed",
                    "fields": {"字幕状态": "failed", "失败原因": binding_error},
                    "failure_reason": binding_error,
                })
                write_json_atomically(existing_evidence, caption_fact)
    else:
        # 非小红书平台：先在 --platform-caption-evidence-json 指向的证据文件里找，
        # 读不到再回落基础数据 JSON。B站 的字幕由 process 阶段物化成本证据、
        # 并不在基础数据里；不先读它就必然判「字幕不可用」，然后一路走到 ASR。
        caption_fact = None
        if evidence_path:
            existing_evidence = _staging_path(
                Path(args.workspace_root).resolve(), evidence_path,
                run_id=str(getattr(args, "run_id", "") or ""),
            )
            if existing_evidence.is_file():
                caption_fact = load_platform_caption_evidence(
                    str(existing_evidence),
                    content_id=str(args.content_id or "").strip(),
                    run_id=str(getattr(args, "run_id", "") or "").strip(),
                    video_url=current_video_url,
                )
                try:
                    caption_fact = validate_caption_evidence_raw_hash(
                        caption_fact,
                        workspace_root=Path(args.workspace_root).resolve(),
                        run_id=str(getattr(args, "run_id", "") or ""),
                    )
                except (OSError, RuntimeError) as exc:
                    caption_fact = {"source": "platform_caption", "status": "failed", "failure_reason": f"平台字幕原始详情不可用：{exc}"}
        if caption_fact is None:
            caption_fact = load_platform_caption_evidence(
                args.body_source_json,
                content_id=str(args.content_id or "").strip(),
                run_id=str(getattr(args, "run_id", "") or "").strip(),
                video_url=current_video_url,
            )
    if caption_fact.get("status") == "success" and str(caption_fact.get("text") or "").strip():
        atomic_write_text(output_path, str(caption_fact["text"]).strip())
        emit_result(
            args, output_path, "成功", 输出文件=output_path.name, 输出路径=str(output_path),
            正文来源="平台真实字幕", 正文获取状态="已获取", source="platform_caption",
            status="success", text_path=str(output_path), evidence=caption_fact.get("evidence", {}),
        )
        return 0

    # Publish review receives a persisted one-item batch response from the
    # unified URL ASR service. Consume it directly so no ticket is passed to
    # or emitted by this execution unit.
    batch_result_path = str(getattr(args, "asr_result_json", "") or "").strip()
    if batch_result_path:
        transcript_text, batch_error = load_batch_asr_result(
            batch_result_path,
            content_id=str(args.content_id or "").strip(),
            workspace_root=Path(args.workspace_root).resolve(),
            expected_run_id=str(getattr(args, "run_id", "") or "").strip(),
            expected_video_url=current_video_url,
            expected_idempotency_key=str(getattr(args, "asr_idempotency_key", "") or "").strip(),
        )
        if not transcript_text:
            used_final, failure_reason = emit_confirmed_final_fallback(
                args, output_path, trigger=f"ASR 批次结果不可用：{batch_error}"
            )
            if used_final:
                return 0
            emit_result(args, output_path, "失败", 失败原因=failure_reason, source="asr", status="failed", evidence={"content_id": args.content_id, "run_id": getattr(args, "run_id", ""), "url_hash": video_url_hash(current_video_url)}, failure_reason=failure_reason)
            return 1
        atomic_write_text(output_path, transcript_text)
        # 来源必须来自结果文件本身：它可能是托管写回的，也可能是本地火山回退补的，
        # 两者同形，只有结果文件自己知道。读不到时按托管口径兜底。
        asr_source = read_asr_source_label(
            batch_result_path, workspace_root=Path(args.workspace_root).resolve()
        ) or "媒灵托管火山 URL ASR"
        emit_result(
            args,
            output_path,
            "成功",
            输出文件=output_path.name,
            输出路径=str(output_path),
            ASR来源=asr_source,
            正文获取状态="已获取",
            source="asr",
            status="success",
            text_path=str(output_path),
            evidence={"content_id": args.content_id, "run_id": getattr(args, "run_id", ""), "url_hash": video_url_hash(current_video_url)},
        )
        return 0

    env_values = load_env(Path(args.workspace_root) / "本地配置.txt")
    justone_token = env_values.get("JUSTONEAPI_TOKEN", "")
    justone_base_url = env_values.get("JUSTONEAPI_BASE_URL", "https://api.justoneapi.com")
    bilibili_sessdata = env_values.get("BILIBILI_SESSDATA", "")

    transcript_text = ""
    primary_error = ""
    media_probe = probe_media_url(current_video_url) if current_video_url else ""
    refresh_meta: dict[str, object] = {
        "content_id": args.content_id,
        "platform": args.platform,
        "note_url": args.note_url,
        "original_video_url": current_video_url,
        "active_video_url": current_video_url,
        "refreshed_video_url": "",
        "refresh_source": "",
        "refresh_attempted": False,
        "refresh_succeeded": False,
        "refresh_failure_reason": "",
    }

    if args.platform == "B站":
        subtitle_errors: list[str] = []
        bvid, aid, cid = resolve_bilibili_ids(args)
        if not cid or not (bvid or aid):
            subtitle_errors.append("B站字幕获取失败: 缺少完整的 B 站视频标识")
        else:
            try:
                transcript_text = fetch_bilibili_caption_from_public_api(
                    bvid,
                    aid,
                    cid,
                    bilibili_sessdata,
                )
            except Exception as exc:
                code = permission_error_code(exc, "network")
                if code:
                    raise RuntimeError(code) from exc
                subtitle_errors.append(f"B站公开字幕分支失败: {exc}")
            if not transcript_text and justone_token:
                try:
                    transcript_text = fetch_bilibili_caption_from_justoneapi(
                        justone_token, justone_base_url, bvid, aid, cid
                    )
                except Exception as exc:
                    code = permission_error_code(exc, "network")
                    if code:
                        raise RuntimeError(code) from exc
                    subtitle_errors.append(f"JustOneAPI 字幕分支失败: {exc}")
        if subtitle_errors:
            primary_error = "；".join(subtitle_errors)
        if transcript_text:
            # 与小红书对齐：把 B站 字幕落成本轮可消费的平台字幕证据，供下游
            # （我方内容 / 对标链路的证据选择器）识别来源，而不是只把正文写出去。
            write_json_atomically(
                Path(evidence_path or (output_path.parent / "platform-caption-evidence.json")),
                {
                    "schema_version": 1,
                    "source": "platform_caption",
                    "status": "success",
                    "content_id": str(args.content_id or "").strip(),
                    "run_id": str(getattr(args, "run_id", "") or "").strip(),
                    "url_hash": video_url_hash(current_video_url),
                    "fields": {"字幕状态": "success", "字幕文本": transcript_text},
                    "failure_reason": "",
                },
            )

    if not transcript_text:
        if not current_video_url:
            reason = primary_error or "B站字幕获取失败"
            reason += "；视频下载链接不可用，无法执行 ASR"
            used_final, failure_reason = emit_confirmed_final_fallback(args, output_path, trigger=reason)
            if used_final:
                return 0
            emit_result(args, output_path, "失败", 失败原因=failure_reason, source="unavailable", status="unavailable", evidence={"content_id": args.content_id, "run_id": getattr(args, "run_id", ""), "url_hash": video_url_hash(current_video_url)}, failure_reason=failure_reason)
            return 1
        try:
            asr_payload = run_selected_local_asr(
                Path(args.workspace_root).resolve(),
                video_url=current_video_url,
                provider="volcengine",
                platform=args.platform,
                bvid=args.bvid,
                aid=args.aid,
                cid=args.cid,
            )
            transcript_text = asr_payload["text"]
            refresh_meta["asr_success"] = {
                "resource_id": str(asr_payload.get("resource_id") or ""),
                "attempts": int(asr_payload.get("attempts") or 1),
            }
        except Exception as exc:
            failure_reason = primary_error + "；" if primary_error else ""
            failure_reason += f"本地火山 ASR 执行失败：{exc}"
            if media_probe:
                failure_reason += f"；{media_probe}"
            # 服务端原始信封单独落盘：失败原因可以读，事后也能复核。
            if isinstance(exc, VolcengineASRError):
                refresh_meta["asr_failure"] = exc.as_evidence()
            write_refresh_meta(refresh_meta_path, refresh_meta)
            used_final, final_failure_reason = emit_confirmed_final_fallback(
                args, output_path, trigger=failure_reason
            )
            if used_final:
                return 0
            emit_result(
                args, output_path, "失败", 失败原因=final_failure_reason, source="asr",
                status="failed", evidence={"content_id": args.content_id, "run_id": getattr(args, "run_id", ""), "url_hash": video_url_hash(current_video_url)},
                failure_reason=final_failure_reason,
            )
            return 1

    atomic_write_text(output_path, transcript_text.strip())
    write_refresh_meta(refresh_meta_path, refresh_meta)
    emit_result(
        args,
        output_path,
        "成功",
        输出文件=output_path.name,
        输出路径=str(output_path),
        刷新后视频链接=refresh_meta.get("refreshed_video_url", ""),
        source="platform_caption" if args.platform == "B站" and not primary_error else "asr",
        status="success",
        text_path=str(output_path),
        evidence={"content_id": args.content_id, "run_id": getattr(args, "run_id", ""), "url_hash": video_url_hash(current_video_url)},
    )
    return 0


def main() -> int:
    args = parse_args()
    try:
        return _main(args)
    except Exception as exc:  # noqa: BLE001
        error_code = permission_error_code(exc, "filesystem")
        if not error_code:
            raise
        emit_result(args, Path(args.output_path).resolve(), "失败", error_code=error_code, 失败原因=str(exc))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
