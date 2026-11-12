#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List

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
    build_batch_report_path,
    build_benchmark_content_dir_name,
    build_content_dir_name,
    build_content_process_dir,
    resolve_existing_content_dir as resolve_safe_existing_content_dir,
    sanitize_title,
    UnsafeContentDirectoryError,
)
from 账号阶段状态 import write_json_atomically
from 详细数据阶段合同 import DetailDataContractError, validate_identity

CONTENT_DIR_PATH_FIELDS = [
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
        "内容目录路径",  # 路径只填空、不覆盖（与另两处执行器口径一致）
        "对外页面链接",
        "内容链接",
        "笔记链接",
    }
)
CONTENT_LIBRARY_OWNED_FIELDS = CONTENT_LIBRARY_FILL_ONLY_FIELDS | frozenset(
    {"核心互动数", "数据水平", "发布时间区间", "备注"}
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="批量计算账号内容的核心互动数、数据水平、发布时间区间。")
    parser.add_argument("--account-id", required=True, help="账号ID")
    parser.add_argument("--content-list", required=True, help="账号内容列表 CSV 路径")
    parser.add_argument("--content-library", required=True, help="内容库 CSV 路径")
    parser.add_argument("--content-ids", required=True, help="本轮待处理内容ID，逗号分隔")
    parser.add_argument(
        "--workspace-root",
        default=".",
        help="工作区根目录，默认使用调用方当前工作目录",
    )
    parser.add_argument("--task-id", default="", help="我方账号公开调度任务编号")
    parser.add_argument("--flow-type", default="", choices=("", "benchmark_account", "self_account"))
    parser.add_argument("--run-id", default="", help="当前一次具体执行的唯一标识")
    parser.add_argument("--dry-run", action="store_true", help="只校验清单，不实际回写")
    return parser.parse_args()


def build_row_index(rows: List[Dict[str, str]], key: str) -> Dict[str, Dict[str, str]]:
    index: Dict[str, Dict[str, str]] = {}
    for row in rows:
        value = (row.get(key) or "").strip()
        if value:
            index[value] = row
    return index


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


def build_default_report_path(content_list_path: Path) -> Path:
    return build_batch_report_path(content_list_path, "本轮衍生字段计算结果.json")


def build_task_report_path(workspace_root: Path, task_id: str, run_id: str, *, flow_type: str) -> Path:
    safe_id = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}")
    if not safe_id.fullmatch(run_id):
        raise ValueError("run-id 必须是安全的单段标识")
    if flow_type == "benchmark_account":
        flow_root = "benchmark-account-diagnose"
    elif flow_type == "self_account":
        flow_root = "self-account-diagnose"
    else:
        raise ValueError("详细数据任务身份无效")
    stage_dir = workspace_root.resolve() / ".meiling/staging" / flow_root / run_id / "detail-data"
    if stage_dir.exists() and stage_dir.resolve() != stage_dir:
        raise ValueError("当前任务暂存目录不能经过符号链接")
    return stage_dir / "衍生字段报告.json"


def normalize_int(value: Any) -> int | None:
    text = str(value or "").strip()
    if not text:
        return None
    digits = re.sub(r"[^\d-]", "", text)
    if digits in {"", "-"}:
        return None
    return int(digits)


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


def compute_data_level(platform: str, core_interactions: int) -> str:
    """按平台的绝对阈值给「数据水平」（大爆款／小爆款／优秀／正常／差）。

    ⚠️ **同一份逻辑在 `对标内容数据建档执行器.py` 里还有一份**（那边写成阈值表，
    阈值完全相同）。**改阈值时两处必须一起改。**

    没有合并成一份的原因：两边对「平台不在支持的三种里」的反应不同（这边
    `raise ValueError`，那边 `KeyError`），合并会改变异常类型，对调用方是行为变更。
    """
    if platform == "小红书":
        if core_interactions >= 10000:
            return "大爆款"
        if core_interactions >= 3000:
            return "小爆款"
        if core_interactions >= 1000:
            return "优秀"
        if core_interactions >= 100:
            return "正常"
        return "差"
    if platform == "抖音":
        if core_interactions >= 30000:
            return "大爆款"
        if core_interactions >= 10000:
            return "小爆款"
        if core_interactions >= 3000:
            return "优秀"
        if core_interactions >= 500:
            return "正常"
        return "差"
    if platform == "B站":
        if core_interactions >= 5000:
            return "大爆款"
        if core_interactions >= 2000:
            return "小爆款"
        if core_interactions >= 800:
            return "优秀"
        if core_interactions >= 100:
            return "正常"
        return "差"
    raise ValueError(f"当前平台暂不支持计算数据水平: {platform}")


ACCOUNT_RANK_MIN_SAMPLES = 5
ACCOUNT_RANK_INSUFFICIENT = "样本不足"
ACCOUNT_RANK_HIGH = "高"
ACCOUNT_RANK_MIDDLE = "中"
ACCOUNT_RANK_LOW = "低"
# 高门槛＝互动数达到账号中位数的 2 倍；低门槛＝互动数只有中位数的 1.5 分之一及以下。
# 「÷ 1.5」＝「× 2 ÷ 3」，比较时两边同乘 3 就回到整数，避免浮点误差。
ACCOUNT_RANK_LOW_SCALE = 3
# 护栏：头尾各最多占 30%（3/10，取整向上，保证 n 小时至少给 1 个名额）。
# 为什么需要它：长尾账号的中位数会被大量低互动内容压低，2 倍门槛跟着变低，
# 不加护栏会让四成内容都算「头部」（2026-09-26 用真实账号实测）。
ACCOUNT_RANK_MAX_SHARE_TENTHS = 3


def compute_account_rank(core_values: Iterable[int], core: int) -> str:
    """按「账号内相对位置」给档次：高／中／低。

    两条门槛同时用，**取更严的那个**：

    - 高：互动数 ≥ 账号中位数 × 2，**且**排在全账号前 30%；
    - 低：互动数 ≤ 账号中位数 ÷ 1.5，**且**排在全账号后 30%；
    - 两条都不满足＝中档。

    倍数那半条负责「数据不够突出的账号分不出头尾」：全账号互动数都很平时，
    没人够得着 2 倍中位数，头部自然是空的。上限那半条是护栏，防止长尾账号
    头部膨胀。少任何一条都会出问题，别只留一条。

    只在**已取到互动数**的内容里排名（没跑过详细数据的内容不参与）；公开与
    私密一视同仁，用户自己决定要拿多少条内容进来跑。
    样本少于 `ACCOUNT_RANK_MIN_SAMPLES` 条时返回「样本不足」—— 条数太少时
    「前 30%」可能只对应 1 条，拿去做统计没有意义。

    跨账号不按绝对值比，而是**各账号排各自的**：这样大号的前 30% 和小号的
    前 30% 能放在一起看，不受粉丝量影响。
    """
    values = sorted(int(value) for value in core_values)
    size = len(values)
    if size < ACCOUNT_RANK_MIN_SAMPLES:
        return ACCOUNT_RANK_INSUFFICIENT
    # 2 × 中位数。偶数条是两个中间值之和，奇数条是中间值 × 2 —— 结果一定是整数，
    # 于是「中位数 × 2」和「中位数 × 2 ÷ 3」都能用整数比较表达，不引入浮点误差。
    doubled_median = values[(size - 1) // 2] + values[size // 2]
    # ceil(size * 0.3)，避免引入 math
    cut = max(1, (size * ACCOUNT_RANK_MAX_SHARE_TENTHS + 9) // 10)
    descending = list(reversed(values))
    high_floor = max(doubled_median, descending[cut - 1])
    # 低门槛乘 3 之后与「中位数 × 2」同尺度，所以两边都乘 3 再比。
    low_ceiling_tripled = min(
        doubled_median,
        ACCOUNT_RANK_LOW_SCALE * descending[size - cut],
    )
    current = int(core)
    # 门槛必须大于 0 才算数：全账号互动数都是 0 时中位数也是 0，门槛会退化成 0，
    # 那样任何一个值都能「达标」，整账号会被误判成头部。
    if high_floor > 0 and current >= high_floor:
        return ACCOUNT_RANK_HIGH
    if (
        low_ceiling_tripled > 0
        and ACCOUNT_RANK_LOW_SCALE * current <= low_ceiling_tripled
    ):
        return ACCOUNT_RANK_LOW
    return ACCOUNT_RANK_MIDDLE


def compute_time_slot(published_at: str) -> str:
    text = str(published_at or "").strip()
    if not text:
        return ""
    candidates = [text, text.replace("/", "-"), text.replace("Z", "+00:00")]
    candidates.append(candidates[1].replace("Z", "+00:00"))
    parsed: datetime | None = None
    for candidate in candidates:
        candidate = re.sub(r"([+-]\d{2})(\d{2})$", r"\1:\2", candidate)
        try:
            parsed = datetime.fromisoformat(candidate)
            break
        except ValueError:
            continue
    if parsed is None:
        for pattern in (
            "%Y/%m/%d %H:%M:%S", "%Y/%m/%d %H:%M",
            "%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M",
            "%Y年%m月%d日 %H时%M分%S秒", "%Y年%m月%d日 %H时%M分",
            "%Y年%m月%d日 %H:%M:%S", "%Y年%m月%d日 %H:%M",
        ):
            try:
                parsed = datetime.strptime(text, pattern)
                break
            except ValueError:
                continue
    if parsed is None:
        raise ValueError("发布时间格式不合法")
    end_hour = (parsed.hour + 1) % 24
    return f"{parsed.hour:02d}:00-{end_hour:02d}:00"


def sync_content_library_row(
    library_rows: List[Dict[str, str]],
    library_index: Dict[str, Dict[str, str]],
    content_row: Dict[str, str],
    library_fieldnames: List[str],
    *,
    clear_fields: Iterable[str] = (),
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
    sync_owned_fields(
        library_row,
        content_row,
        library_fieldnames,
        owned_fields=CONTENT_LIBRARY_OWNED_FIELDS,
        field_aliases=CONTENT_LIBRARY_FIELD_ALIASES,
        fill_only_fields=CONTENT_LIBRARY_FILL_ONLY_FIELDS,
        clear_fields=clear_fields,
    )
    if "当前状态" in library_fieldnames:
        if (library_row.get("当前状态") or "").strip() != "已复盘":
            library_row["当前状态"] = "已发布"
        if not (library_row.get("当前状态") or "").strip():
            raise ValueError("我方内容写入后当前状态不得为空")


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


def main() -> int:
    try:
        return _main()
    except UnsafeContentDirectoryError as exc:
        print(json.dumps({"ok": False, "error_code": "unsafe_content_path", "error": str(exc)}, ensure_ascii=False))
        return 1


def _main() -> int:
    args = parse_args()
    workspace_root = Path(args.workspace_root).resolve()
    def output_ref(path: Path) -> str:
        try:
            return path.resolve().relative_to(workspace_root).as_posix()
        except ValueError as exc:
            raise ValueError("衍生字段产物路径必须位于工作区内") from exc
    def resolve_input(raw: str, label: str) -> Path:
        candidate = Path(str(raw or "").strip()).expanduser()
        resolved = (candidate if candidate.is_absolute() else workspace_root / candidate).resolve()
        try:
            resolved.relative_to(workspace_root)
        except ValueError as exc:
            raise ValueError(f"{label}必须是工作区内相对路径") from exc
        return resolved
    try:
        content_list_path = resolve_input(args.content_list, "内容列表")
        content_library_path = resolve_input(args.content_library, "内容库")
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    task_id = str(args.task_id or "").strip()
    try:
        flow_type, task_id, run_id = validate_identity(
            flow_type=str(args.flow_type or ""),
            task_id=task_id,
            run_id=str(args.run_id or ""),
            standalone=not task_id and not str(args.flow_type or "").strip(),
        )
    except DetailDataContractError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    if flow_type not in {"benchmark_account", "self_account"}:
        print("衍生字段批处理必须显式提供 flow_type", file=sys.stderr)
        return 1
    if not content_list_path.exists():
        print(f"内容列表不存在: {content_list_path}")
        return 1
    if not content_library_path.exists():
        print(f"内容库不存在: {content_library_path}")
        return 1

    requested_ids = normalize_requested_ids(args.content_ids)
    if not requested_ids:
        print("未提供有效的内容ID清单")
        return 1

    content_fieldnames, content_rows, content_snapshot = read_csv_table_with_snapshot(content_list_path)
    content_index = build_row_index(content_rows, "内容ID")
    try:
        validate_requested_account(requested_ids, content_index, args.account_id)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    library_fieldnames, library_rows, library_snapshot = read_csv_table_with_snapshot(content_library_path)
    library_index = build_content_library_index(library_rows, workspace_root)
    if task_id:
        try:
            content_list_path.relative_to(workspace_root)
            content_library_path.relative_to(workspace_root)
            report_path = build_task_report_path(workspace_root, task_id, run_id, flow_type=flow_type).resolve()
        except ValueError as exc:
            print(str(exc), file=sys.stderr)
            return 1
    else:
        if flow_type == "benchmark_account":
            report_path = build_task_report_path(workspace_root, "", run_id, flow_type=flow_type).resolve()
        else:
            report_path = build_default_report_path(content_list_path)

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
                    "content_dir": output_ref(content_dir),
                    "process_dir": output_ref(process_dir),
                    "result_json": output_ref(process_dir / "衍生字段计算结果.json"),
                    "requested_inputs": {
                        "点赞数": row.get("点赞数", ""),
                        "收藏数": row.get("收藏数", ""),
                        "评论数": row.get("评论数", ""),
                        "发布时间": row.get("发布时间", ""),
                    },
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
                        "report_json": output_ref(report_path),
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

    report_path.parent.mkdir(parents=True, exist_ok=True)

    summary: Dict[str, Any] = {
        "flow_type": flow_type,
        "task_id": task_id,
        "run_id": run_id,
        "account_id": args.account_id,
        "requested_count": len(requested_ids),
        "processed_count": 0,
        "core_interaction_success_count": 0,
        "data_level_success_count": 0,
        "time_slot_success_count": 0,
        "derived_success_content_ids": [],
        "failed_items": [],
        "items": [],
        "report_json": output_ref(report_path),
    }

    for content_id in requested_ids:
        row = content_index.get(content_id)
        if row is None:
            summary["failed_items"].append({"content_id": content_id, "title": "", "failures": ["内容ID不在内容列表中"]})
            continue
        title = (row.get("标题") or "").strip()
        library_row = lookup_content_library_row(library_index, row)
        content_root = resolve_content_root(workspace_root, content_library_path)
        existing_content_dir = resolve_existing_content_dir(workspace_root, content_root, row, library_row)
        if existing_content_dir is not None:
            content_dir = existing_content_dir
        else:
            content_dir = content_root / build_content_dir_name(content_library_path, row, title)
        process_dir = build_content_process_dir(content_list_path, content_id, title)
        ensure_dir(content_dir)
        ensure_dir(process_dir)
        failures: List[str] = []

        likes = normalize_int(row.get("点赞数"))
        collects = normalize_int(row.get("收藏数"))
        comments = normalize_int(row.get("评论数"))
        if likes is None or collects is None or comments is None:
            failures.append("点赞数/收藏数/评论数不完整，无法计算核心互动数")
        else:
            row["核心互动数"] = str(likes + collects + comments)
        if (row.get("核心互动数") or "").strip():
            try:
                row["数据水平"] = compute_data_level((row.get("平台") or "").strip(), int(row["核心互动数"]))
            except Exception as exc:  # noqa: BLE001
                failures.append(str(exc))
        else:
            failures.append("核心互动数缺失，无法计算数据水平")
        try:
            row["发布时间区间"] = compute_time_slot(row.get("发布时间", ""))
        except Exception:  # noqa: BLE001
            failures.append("发布时间格式不合法，无法计算发布时间区间")

        base_remark = strip_remark_prefixes(row.get("备注", ""), ["衍生字段计算:"])
        if failures:
            row["备注"] = merge_remark(base_remark, "衍生字段计算: " + "，".join(dict.fromkeys(failures)))
        else:
            row["备注"] = base_remark
        if "我方资料" not in content_library_path.parts:
            sync_content_library_row(library_rows, library_index, row, library_fieldnames)

        result = {
            "flow_type": flow_type,
            "task_id": task_id,
            "run_id": run_id,
            "account_id": args.account_id,
            "content_id": content_id,
            "title": title,
            "status": "成功" if not failures else "部分成功" if (row.get("核心互动数") or "").strip() else "失败",
            "核心互动数": row.get("核心互动数", ""),
            "数据水平": row.get("数据水平", ""),
            "发布时间区间": row.get("发布时间区间", ""),
            "failures": list(dict.fromkeys(failures)),
            "content_dir": output_ref(content_dir),
            "process_dir": output_ref(process_dir),
            "result_json": output_ref(process_dir / "衍生字段计算结果.json"),
        }
        summary["items"].append(result)
        summary["processed_count"] += 1
        if (row.get("核心互动数") or "").strip():
            summary["core_interaction_success_count"] += 1
        if (row.get("数据水平") or "").strip():
            summary["data_level_success_count"] += 1
        if (row.get("发布时间区间") or "").strip():
            summary["time_slot_success_count"] += 1
        if result["status"] != "失败":
            summary["derived_success_content_ids"].append(content_id)
        if failures:
            summary["failed_items"].append({"content_id": content_id, "title": title, "failures": result["failures"]})

    write_csv_tables_atomically(
        [
            (content_list_path, content_fieldnames, content_rows),
            (content_library_path, library_fieldnames, library_rows),
        ],
        expected_originals={
            content_list_path: content_snapshot,
            content_library_path: library_snapshot,
        },
    )
    for item in summary["items"]:
        write_json_atomically(workspace_root / str(item["result_json"]), item)

    write_json_atomically(report_path, summary)
    stdout_summary = {
        "flow_type": flow_type,
        "task_id": task_id,
        "run_id": run_id,
        "account_id": args.account_id,
        "requested_count": summary["requested_count"],
        "processed_count": summary["processed_count"],
        "core_interaction_success_count": summary["core_interaction_success_count"],
        "data_level_success_count": summary["data_level_success_count"],
        "time_slot_success_count": summary["time_slot_success_count"],
        "derived_success_content_ids": summary["derived_success_content_ids"],
        "failed_count": len(summary["failed_items"]),
        "report_json": output_ref(report_path),
    }
    print(json.dumps(stdout_summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
