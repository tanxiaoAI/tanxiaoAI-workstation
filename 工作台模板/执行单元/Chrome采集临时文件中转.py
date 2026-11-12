#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict


MANIFEST_VERSION = 2
INBOX_DIRNAME = "chrome-inbox"
# 兼容仍读取旧常量名的调用方；该常量现在只用于标识工作区中转目录。
TEMP_PREFIX = INBOX_DIRNAME
STAGE1_FILENAME = "阶段一结果.json"
SCREENSHOT_FILENAME = "主页长截图.png"
RAW_LIST_FILENAME = "账号内容原始列表.json"
SCREENSHOT_RESULT_FILENAME = "主页长截图结果.json"
CONTENT_STATUSES = {"成功", "部分成功", "失败"}
REQUIRED_ITEM_FIELDS = {
    "平台原生内容ID",
    "平台",
    "账号ID",
    "账号名",
    "标题",
    "封面图原链接",
    "媒介形式",
    "公开状态",
    "点赞数",
    "发布时间",
    "对外页面链接",
    "基础数据获取方式",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="在工作区内的 Chrome 中转目录和账号诊断暂存目录之间中转采集文件。")
    subparsers = parser.add_subparsers(dest="command", required=True)

    prepare = subparsers.add_parser("prepare", help="创建工作区内 Chrome MCP 可写的中转目录和中转清单")
    prepare.add_argument("--staging-dir", required=True, help="本轮账号诊断暂存目录")

    finalize = subparsers.add_parser("finalize", help="校验临时产物并写入本轮暂存目录")
    finalize.add_argument("--manifest-json", required=True, help="prepare 返回的中转清单 JSON")
    finalize.add_argument(
        "--screenshot-error",
        default="",
        help="take_screenshot 失败时的简短原始错误；不得传入作品列表或其他大段数据",
    )
    return parser.parse_args()


def write_json_atomic(path: Path, payload: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    temporary_path = Path(temporary_name)
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as file:
            json.dump(payload, file, ensure_ascii=False, indent=2)
            file.write("\n")
            file.flush()
            os.fsync(file.fileno())
        os.replace(temporary_path, path)
    except Exception:
        temporary_path.unlink(missing_ok=True)
        raise


def copy_file_atomic(source: Path, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary_name = tempfile.mkstemp(prefix=f".{target.name}.", suffix=".tmp", dir=target.parent)
    os.close(handle)
    temporary_path = Path(temporary_name)
    try:
        shutil.copy2(source, temporary_path)
        os.replace(temporary_path, target)
    except Exception:
        temporary_path.unlink(missing_ok=True)
        raise


def is_within(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def prepare_handoff(staging_dir_value: str) -> Dict[str, Any]:
    staging_dir = Path(staging_dir_value).expanduser().resolve()
    # 本步骤会在传入目录下再建一层 INBOX_DIRNAME。如果调用方已经把 chrome-inbox
    # 当成暂存目录传进来，会得到 chrome-inbox/chrome-inbox 的重复嵌套，之后所有
    # 中转文件都落在错位路径上（且命令本身会"成功"，调用方看不出来）。
    # 正确用法是传本轮暂存目录本身，见 执行流程/账号基础数据获取流程.md。
    if staging_dir.name == INBOX_DIRNAME:
        raise ValueError(f"staging_dir_must_be_run_dir:{staging_dir}")
    staging_dir.mkdir(parents=True, exist_ok=True)
    # Chrome MCP 只允许写入当前工作区；中转目录必须跟随本轮任务保存在工作区内。
    temporary_dir = staging_dir / INBOX_DIRNAME
    temporary_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = temporary_dir / "中转清单.json"
    manifest = {
        "version": MANIFEST_VERSION,
        "chrome_inbox_dir": str(temporary_dir),
        # 保留兼容字段名，但它现在也必须指向工作区内的 chrome-inbox。
        "temporary_dir": str(temporary_dir),
        "stage1_json": str(temporary_dir / STAGE1_FILENAME),
        "screenshot_png": str(temporary_dir / SCREENSHOT_FILENAME),
        "network_response": str(temporary_dir / "网络响应.network-response"),
        "staging_dir": str(staging_dir),
        "raw_list_json": str(staging_dir / RAW_LIST_FILENAME),
        "screenshot_result_json": str(staging_dir / SCREENSHOT_RESULT_FILENAME),
        "staging_screenshot_png": str(staging_dir / SCREENSHOT_FILENAME),
    }
    write_json_atomic(manifest_path, manifest)
    return {"ok": True, "manifest_json": str(manifest_path), **manifest}


def load_manifest(manifest_value: str) -> Dict[str, Any]:
    manifest_path = Path(manifest_value).expanduser().resolve()
    if not manifest_path.is_file():
        raise ValueError("中转清单不存在")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if not isinstance(manifest, dict) or manifest.get("version") != MANIFEST_VERSION:
        raise ValueError("中转清单格式或版本无效")

    temporary_dir = Path(
        str(manifest.get("chrome_inbox_dir") or manifest.get("temporary_dir") or "")
    ).expanduser().resolve()
    if temporary_dir != manifest_path.parent or temporary_dir.name != INBOX_DIRNAME:
        raise ValueError("中转清单记录的 Chrome 中转目录无效")

    staging_dir = Path(str(manifest.get("staging_dir") or "")).expanduser().resolve()
    if not is_within(temporary_dir, staging_dir) or temporary_dir.parent != staging_dir:
        raise ValueError("Chrome 中转目录必须位于本轮暂存目录内")
    expected_paths = {
        "stage1_json": temporary_dir / STAGE1_FILENAME,
        "screenshot_png": temporary_dir / SCREENSHOT_FILENAME,
        "network_response": temporary_dir / "网络响应.network-response",
        "raw_list_json": staging_dir / RAW_LIST_FILENAME,
        "screenshot_result_json": staging_dir / SCREENSHOT_RESULT_FILENAME,
        "staging_screenshot_png": staging_dir / SCREENSHOT_FILENAME,
    }
    for key, expected in expected_paths.items():
        recorded = Path(str(manifest.get(key) or "")).expanduser()
        if recorded.absolute() != expected.absolute():
            raise ValueError(f"中转清单路径不匹配: {key}")
    return manifest


def validate_stage1_payload(payload: Any) -> Dict[str, Any]:
    if not isinstance(payload, dict):
        raise ValueError("阶段一结果必须是 JSON 对象")
    if payload.get("基础数据获取方式") != "chrome_devtools":
        raise ValueError("阶段一结果的基础数据获取方式必须是 chrome_devtools")
    status = payload.get("内容列表获取状态")
    if status not in CONTENT_STATUSES:
        raise ValueError("阶段一结果缺少有效的内容列表获取状态")
    items = payload.get("账号内容原始列表")
    if not isinstance(items, list):
        raise ValueError("阶段一结果的账号内容原始列表必须是数组")
    for index, item in enumerate(items):
        if not isinstance(item, dict):
            raise ValueError(f"第 {index + 1} 条作品不是 JSON 对象")
        missing = sorted(REQUIRED_ITEM_FIELDS - set(item))
        if missing:
            raise ValueError(f"第 {index + 1} 条作品缺少字段: {', '.join(missing)}")
    return payload


def is_valid_png(path: Path) -> bool:
    if not path.is_file() or path.is_symlink():
        return False
    with path.open("rb") as file:
        return file.read(8) == b"\x89PNG\r\n\x1a\n"


def finalize_handoff(manifest_value: str, screenshot_error: str = "") -> Dict[str, Any]:
    manifest = load_manifest(manifest_value)
    stage1_path = Path(manifest["stage1_json"])
    if not stage1_path.is_file() or stage1_path.is_symlink():
        raise ValueError("Chrome MCP 没有生成有效的阶段一结果 JSON")
    payload = validate_stage1_payload(json.loads(stage1_path.read_text(encoding="utf-8")))

    screenshot_source = Path(manifest["screenshot_png"])
    screenshot_target = Path(manifest["staging_screenshot_png"])
    screenshot_result_path = Path(manifest["screenshot_result_json"])
    content_failed = payload["内容列表获取状态"] == "失败"
    screenshot_ok = not content_failed and is_valid_png(screenshot_source)
    if content_failed:
        screenshot_status = "跳过"
        screenshot_path_value = ""
        screenshot_result_path_value = ""
        screenshot_failure = ""
    elif screenshot_ok:
        copy_file_atomic(screenshot_source, screenshot_target)
        screenshot_status = "成功"
        screenshot_path_value = str(screenshot_target)
        screenshot_result_path_value = str(screenshot_result_path)
        screenshot_failure = ""
    else:
        screenshot_status = "失败"
        screenshot_path_value = ""
        screenshot_result_path_value = str(screenshot_result_path)
        screenshot_failure = screenshot_error.strip() or "take_screenshot 未生成有效的 PNG 文件"

    if not content_failed:
        screenshot_result = {
            "status": "success" if screenshot_ok else "failed",
            "fullPage": True,
            "format": "png",
            "path": screenshot_path_value,
            "error": screenshot_failure,
        }
        write_json_atomic(screenshot_result_path, screenshot_result)

    payload.update(
        {
            "主页长截图状态": screenshot_status,
            "主页长截图路径": screenshot_path_value,
            "主页长截图结果JSON路径": screenshot_result_path_value,
            "主页长截图失败原因": screenshot_failure,
        }
    )
    # 阶段一结果已在上方完成 schema 校验；中转只写入 canonical 结果和文件事实，
    # 不再追加第二份处理打点，也不重复跑整套 schema 校验。
    payload.pop("处理打点", None)
    raw_list_path = Path(manifest["raw_list_json"])
    write_json_atomic(raw_list_path, payload)
    return {
        "ok": True,
        "raw_list_json": str(raw_list_path),
        "content_count": len(payload["账号内容原始列表"]),
        "content_list_status": payload["内容列表获取状态"],
        "screenshot_status": screenshot_status,
        "screenshot_png": screenshot_path_value,
        "screenshot_result_json": screenshot_result_path_value,
    }


def main() -> int:
    args = parse_args()
    try:
        if args.command == "prepare":
            result = prepare_handoff(args.staging_dir)
        else:
            result = finalize_handoff(args.manifest_json, args.screenshot_error)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except (OSError, ValueError, json.JSONDecodeError) as error:
        print(json.dumps({"ok": False, "error": str(error)}, ensure_ascii=False, indent=2))
        return 1


if __name__ == "__main__":
    sys.exit(main())
