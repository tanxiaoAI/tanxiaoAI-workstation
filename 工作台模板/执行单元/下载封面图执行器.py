#!/usr/bin/env python3
from __future__ import annotations

import argparse
import io
import json
import os
import re
import sys
import tempfile
import urllib.request
from datetime import datetime, timezone
from time import monotonic_ns
from pathlib import Path

try:
    from PIL import Image, UnidentifiedImageError
except ImportError:  # pragma: no cover - depends on the deployment environment
    Image = None
    UnidentifiedImageError = OSError

import 证书信任  # noqa: F401  进程级统一 HTTPS 证书信任
from 权限错误 import permission_error_code


def timestamp() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def finish_timing(started_at: str, started_ns: int, **events: str) -> dict[str, object]:
    finished_at = timestamp()
    result = {
        "started_at": started_at,
        "finished_at": finished_at,
        "duration_ms": max(0, round((monotonic_ns() - started_ns) / 1_000_000)),
        **events,
    }
    if "body_read_complete_at" in result:
        result["body_read_completed_at"] = result["body_read_complete_at"]
    if "write_complete_at" in result:
        result["write_completed_at"] = result["write_complete_at"]
    return result


PLATFORM_REFERERS = {
    "小红书": "https://www.xiaohongshu.com/",
    "抖音": "https://www.douyin.com/",
    "B站": "https://www.bilibili.com/",
}

_HEIF_FORMAT_PATTERN = re.compile(r"format(%2F|/)(heif|heic)", re.IGNORECASE)


def prefer_decodable_format(url: str) -> str:
    """把 HEIF/HEIC 的图片处理参数换成 JPEG。

    Pillow 不带 HEIF 解码器，而小红书视频封面默认带 ``format/heif``，下载
    下来无法解析、只能报「响应不是可解析的图片」。换成 jpg 后同一 CDN 会
    返回可解析的 JPEG，且不影响签名。其它格式一律原样保留。
    """
    text = str(url or "")
    if not text:
        return text
    return _HEIF_FORMAT_PATTERN.sub(lambda match: f"format{match.group(1)}jpg", text)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="下载单条内容的封面图。")
    parser.add_argument("--content-id", required=True)
    parser.add_argument("--title", required=True)
    parser.add_argument("--platform", required=True, choices=tuple(PLATFORM_REFERERS))
    parser.add_argument("--cover-url", required=True)
    parser.add_argument("--output-path", required=True)
    parser.add_argument("--result-output-json", default="", help="封面结果回执的原子输出路径")
    parser.add_argument("--workspace-root", default=".")
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args()


def fail(
    reason: str,
    error_code: str | None = None,
    *,
    content_id: str = "",
    output_path: Path | None = None,
    timing: dict[str, object] | None = None,
    result_output_json: str = "",
) -> int:
    payload = {"状态": "失败", "失败原因": reason}
    if content_id:
        payload["content_id"] = content_id
    if output_path is not None:
        payload["output_path"] = str(output_path)
    if error_code:
        payload["error_code"] = error_code
    if timing:
        payload["timing"] = timing
    emit_result(payload, result_output_json)
    return 1


def atomic_write_bytes(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".stage", dir=str(path.parent))
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def emit_result(payload: dict[str, object], result_output_json: str = "") -> None:
    result_path = str(result_output_json or "").strip()
    if result_path:
        atomic_write_bytes(Path(result_path).expanduser().resolve(), (json.dumps(payload, ensure_ascii=False, indent=2) + "\n").encode("utf-8"))
    print(json.dumps(payload, ensure_ascii=False))


def validate_result_output(raw_path: str, workspace_root: Path) -> str:
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
        raise RuntimeError("封面结果回执路径越界") from exc
    if not relative.as_posix().startswith(".meiling/staging/"):
        raise RuntimeError("封面结果回执必须位于暂存目录")
    return str(path)


def request_headers(platform: str) -> dict[str, str]:
    return {
        "User-Agent": "Mozilla/5.0",
        "Accept": "image/avif,image/webp,image/apng,image/*,*/*;q=0.8",
        "Referer": PLATFORM_REFERERS[platform],
    }


def png_payload(image_bytes: bytes) -> tuple[bytes, str]:
    """Decode the response and re-encode it so the .png contract is truthful."""
    if Image is None:
        raise ValueError("封面图转码依赖 Pillow 未安装")
    try:
        with Image.open(io.BytesIO(image_bytes)) as image:
            source_format = str(image.format or "").upper()
            image.load()
            if "transparency" in image.info or image.mode in {"RGBA", "LA"}:
                converted = image.convert("RGBA")
            else:
                converted = image.convert("RGB")
            output = io.BytesIO()
            converted.save(output, format="PNG")
    except (OSError, UnidentifiedImageError) as exc:
        raise ValueError("封面图响应不是可解析的图片") from exc
    return output.getvalue(), source_format


def main() -> int:
    args = parse_args()
    started_ns = monotonic_ns()
    started_at = timestamp()
    output_path = Path(args.output_path).resolve()
    try:
        args.result_output_json = validate_result_output(args.result_output_json, Path(args.workspace_root).resolve())
    except RuntimeError as exc:
        return fail(str(exc), content_id=args.content_id, output_path=output_path)
    try:
        output_path.parent.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        code = permission_error_code(exc, "filesystem")
        if code:
            return fail("封面图目录无写入权限", code, content_id=args.content_id, output_path=output_path, result_output_json=args.result_output_json,
                        timing=finish_timing(started_at, started_ns, request_started_at=started_at))
        raise

    if not args.cover_url.strip():
        return fail("封面图原链接为空", content_id=args.content_id, output_path=output_path, result_output_json=args.result_output_json,
                    timing=finish_timing(started_at, started_ns, request_started_at=started_at))

    args.cover_url = prefer_decodable_format(args.cover_url)

    if args.dry_run:
        payload = {
                    "状态": "成功",
                    "content_id": args.content_id,
                    "output_path": str(output_path),
                    "输出文件": output_path.name,
                    "封面图路径": str(output_path),
                    "timing": finish_timing(started_at, started_ns, request_started_at=started_at,
                                             response_received_at=started_at, body_read_complete_at=started_at,
                                             write_complete_at=started_at),
                }
        emit_result(payload, args.result_output_json)
        return 0

    request = urllib.request.Request(
        args.cover_url,
        headers=request_headers(args.platform),
    )
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            response_received_at = timestamp()
            image_bytes = response.read()
            body_read_complete_at = timestamp()
    except Exception as exc:
        code = permission_error_code(exc, "network")
        return fail(
            f"封面图下载失败: {exc}", code,
            content_id=args.content_id, output_path=output_path,
            result_output_json=args.result_output_json,
            timing=finish_timing(started_at, started_ns, request_started_at=started_at),
        )

    try:
        image_bytes, source_format = png_payload(image_bytes)
        atomic_write_bytes(output_path, image_bytes)
    except ValueError as exc:
        return fail(
            str(exc), content_id=args.content_id, output_path=output_path,
            result_output_json=args.result_output_json,
            timing=finish_timing(started_at, started_ns,
                                 request_started_at=started_at,
                                 response_received_at=response_received_at,
                                 body_read_complete_at=body_read_complete_at),
        )
    except OSError as exc:
        code = permission_error_code(exc, "filesystem")
        return fail(
            f"封面图写入失败: {exc}", code,
            content_id=args.content_id, output_path=output_path,
            result_output_json=args.result_output_json,
            timing=finish_timing(started_at, started_ns, request_started_at=started_at,
                                 response_received_at=response_received_at,
                                 body_read_complete_at=body_read_complete_at),
        )

    if not output_path.exists() or output_path.stat().st_size == 0:
        return fail("封面图下载后文件为空", content_id=args.content_id, output_path=output_path, result_output_json=args.result_output_json,
                    timing=finish_timing(started_at, started_ns, request_started_at=started_at,
                                         response_received_at=response_received_at,
                                         body_read_complete_at=body_read_complete_at,
                                         write_complete_at=timestamp()))

    payload = {
                "状态": "成功",
                "content_id": args.content_id,
                "output_path": str(output_path),
                "输出文件": output_path.name,
                "封面图路径": str(output_path),
                "source_format": source_format,
                "output_format": "PNG",
                "timing": finish_timing(started_at, started_ns, request_started_at=started_at,
                                         response_received_at=response_received_at,
                                         body_read_complete_at=body_read_complete_at,
                                         write_complete_at=timestamp()),
            }
    emit_result(payload, args.result_output_json)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
