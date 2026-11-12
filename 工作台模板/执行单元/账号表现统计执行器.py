#!/usr/bin/env python3
"""账号表现统计执行器

为《账号详细分析》的「账号表现」准备同一轮程序统计。只读账号库和内容列表，
不读模型分析结论，不重算特征标签。输出一份 JSON，供提示词引用、并让页面与
正式报告使用同一组数字。

统计口径：
- 互动数 = 内容列表的「核心互动数」；缺失时不参与统计，不按 0 计。
- 分档 = 账号内部相对排名，调用 `账号衍生字段计算批处理.compute_account_rank`。
- 发布时间只说明发布分布，不用于推断最佳发布时间或增长趋势。
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Iterable, Mapping

if str(Path(__file__).resolve().parent) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parent))

from csv安全写入 import read_csv_table_with_snapshot
from 发布时间解析 import normalize_publish_time_text
from 账号衍生字段计算批处理 import compute_account_rank, compute_time_slot


class AccountPerformanceError(RuntimeError):
    """Raised when program statistics cannot be produced safely."""


ACCOUNT_LIBRARY_PATH = Path("对标资料/对标账号库.csv")
RANK_ORDER = ("高", "中", "低", "样本不足")


def _resolve_inside(root: Path, value: str | Path, label: str) -> Path:
    raw = Path(str(value or "").strip()).expanduser()
    if not str(value or "").strip():
        raise AccountPerformanceError(f"{label}_missing")
    target = (raw if raw.is_absolute() else root / raw).resolve()
    try:
        target.relative_to(root)
    except ValueError as exc:
        raise AccountPerformanceError(f"{label}_outside_workspace") from exc
    return target


def _atomic_write_json(path: Path, payload: Mapping[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    data = (json.dumps(payload, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=str(path.parent))
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)
    if path.read_bytes() != data:
        raise AccountPerformanceError("statistics_post_write_mismatch")


def _read_account(root: Path, account_id: str, platform: str) -> dict[str, str]:
    library_path = root / ACCOUNT_LIBRARY_PATH
    if not library_path.is_file():
        raise AccountPerformanceError(f"account_library_missing:{ACCOUNT_LIBRARY_PATH.as_posix()}")
    _, rows, _ = read_csv_table_with_snapshot(library_path)
    matches = [
        row
        for row in rows
        if str(row.get("账号ID") or "").strip() == account_id
        and (not platform or str(row.get("平台") or "").strip() == platform)
    ]
    if len(matches) != 1:
        raise AccountPerformanceError(f"account_match_count:expected=1:actual={len(matches)}")
    return {str(key): str(value or "").strip() for key, value in matches[0].items()}


def _to_int(value: object) -> int | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return int(float(text))
    except ValueError:
        return None


def _median(values: list[int]) -> float:
    if not values:
        return 0.0
    size = len(values)
    if size % 2:
        return float(values[size // 2])
    return (values[size // 2 - 1] + values[size // 2]) / 2


def _parse_published(value: str) -> datetime | None:
    normalized = normalize_publish_time_text(value)
    if not normalized:
        return None
    try:
        return datetime.strptime(normalized, "%Y/%m/%d %H:%M")
    except ValueError:
        return None


def _published_intervals(published: list[datetime]) -> dict[str, object]:
    ordered = sorted(published)
    if len(ordered) < 2:
        return {"可用作品数": len(ordered), "平均间隔天数": None, "中位间隔天数": None}
    gaps = [
        (ordered[index] - ordered[index - 1]).total_seconds() / 86400
        for index in range(1, len(ordered))
    ]
    gaps.sort()
    return {
        "可用作品数": len(ordered),
        "平均间隔天数": round(sum(gaps) / len(gaps), 1),
        "中位间隔天数": round(_median([int(gap * 100) for gap in gaps]) / 100, 1),
    }


def compute_account_performance(
    workspace_root: str | Path,
    *,
    account_id: str,
    platform: str = "",
    now: str | None = None,
) -> dict[str, object]:
    root = Path(workspace_root).expanduser().resolve()
    if not root.is_dir():
        raise AccountPerformanceError(f"workspace_root_invalid:{root}")
    normalized_id = str(account_id or "").strip()
    if not normalized_id:
        raise AccountPerformanceError("account_id_missing")
    normalized_platform = str(platform or "").strip()
    account = _read_account(root, normalized_id, normalized_platform)
    list_rel = str(account.get("内容列表路径") or "").strip()
    if not list_rel:
        raise AccountPerformanceError("account_content_list_path_missing")
    list_path = _resolve_inside(root, list_rel, "content_list_path")
    _, content_rows, _ = read_csv_table_with_snapshot(list_path)

    interactions: list[int] = []
    ranks: dict[str, int] = {key: 0 for key in RANK_ORDER}
    published: list[datetime] = []
    time_slots: dict[str, int] = {}
    for row in content_rows:
        core = _to_int(row.get("核心互动数"))
        if core is not None:
            interactions.append(core)
        published_at = _parse_published(row.get("发布时间", ""))
        if published_at is not None:
            published.append(published_at)
            slot = compute_time_slot(published_at.strftime("%Y/%m/%d %H:%M"))
            if slot:
                time_slots[slot] = time_slots.get(slot, 0) + 1

    if interactions:
        for value in interactions:
            rank = compute_account_rank(interactions, value)
            ranks[rank] = ranks.get(rank, 0) + 1
        ordered = sorted(interactions, reverse=True)
        top3_count = min(3, len(ordered))
        top3_share = round(sum(ordered[:top3_count]) / sum(ordered), 4) if sum(ordered) else None
        engagement = {
            "样本数": len(interactions),
            "中位数": _median(sorted(interactions)),
            "平均值": round(sum(interactions) / len(interactions), 1),
            "最高": ordered[0],
            "最低": ordered[-1],
            "前3条占比": top3_share,
        }
    else:
        engagement = {
            "样本数": 0,
            "中位数": None,
            "平均值": None,
            "最高": None,
            "最低": None,
            "前3条占比": None,
        }

    earliest = min(published).strftime("%Y/%m/%d %H:%M") if published else None
    latest = max(published).strftime("%Y/%m/%d %H:%M") if published else None
    publish = {
        "可用作品数": len(published),
        "最早": earliest,
        "最晚": latest,
        **_published_intervals(published),
        "时段分布": dict(sorted(time_slots.items())),
    }

    if not interactions:
        warning = "当前没有可用的互动数，暂不能判断作品表现是否稳定"
    elif len(interactions) < 10:
        warning = f"本次只有 {len(interactions)} 条作品有互动数，样本较少，表现判断只作线索"
    else:
        warning = ""

    statistics = {
        "统计时间": now or datetime.now().astimezone().isoformat(timespec="seconds"),
        "账号": {
            "账号ID": normalized_id,
            "账号名": account.get("账号名", ""),
            "平台": normalized_platform or account.get("平台", ""),
        },
        "作品总数": len(content_rows),
        "互动": engagement,
        "账号内分档": {
            "高档": ranks.get("高", 0),
            "中档": ranks.get("中", 0),
            "低档": ranks.get("低", 0),
            "样本不足": ranks.get("样本不足", 0),
            "分母": len(interactions),
            "样本口径": "全部有互动数的已采集作品",
            "说明": (
                "按账号内部互动数相对位置分档，不是与其他账号比；"
                "内容规律分析的分档分母只含已完成特征识别的作品，两者数字可能不同"
            ),
        },
        "发布": publish,
        "趋势提示": {
            "是否可判断": False,
            "说明": "当前为不同发布时间作品的累计互动，不能据此确认近期增长或下降",
        },
        "限制说明": warning,
        "证据": [
            {
                "ID": "S1",
                "来源类型": "程序统计",
                "来源定位": list_rel,
                "内容摘要": (
                    f"按内容列表统计 {len(content_rows)} 条作品的互动中位数、分档与发布分布"
                ),
                "适用限制": "只覆盖已采集作品，不代表账号全部历史作品",
            }
        ],
    }
    return statistics


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="准备账号表现的同一轮程序统计")
    parser.add_argument("--workspace-root", type=Path, required=True)
    parser.add_argument("--account-id", required=True)
    parser.add_argument("--platform", default="")
    parser.add_argument("--output-file", type=Path, required=True)
    parser.add_argument("--now", default=None, help="固定统计时间（测试用）")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        statistics = compute_account_performance(
            args.workspace_root,
            account_id=args.account_id,
            platform=args.platform,
            now=args.now,
        )
        target = _resolve_inside(
            Path(args.workspace_root).expanduser().resolve(),
            args.output_file,
            "output_file",
        )
        payload = {"ok": True, "status": "ready", **statistics}
        _atomic_write_json(target, payload)
        print(
            json.dumps(
                {
                    "ok": True,
                    "status": "ready",
                    "output_path": target.relative_to(
                        Path(args.workspace_root).expanduser().resolve()
                    ).as_posix(),
                    "作品总数": statistics["作品总数"],
                    "互动样本数": statistics["互动"]["样本数"],
                },
                ensure_ascii=False,
            )
        )
        return 0
    except (OSError, UnicodeError, ValueError, AccountPerformanceError) as exc:
        print(
            json.dumps({"ok": False, "status": "failed", "error": str(exc)}, ensure_ascii=False),
            file=sys.stderr,
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
