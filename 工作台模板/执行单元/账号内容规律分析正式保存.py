#!/usr/bin/env python3
"""账号内容规律分析 · 正式保存执行器

两条账号详细分析流程（对标 / 我方）在 `2.6 生成账号内容规律分析` 用：
把模型写好的候选稿校验后**原子写入账号目录**。

- 对标：`<账号目录>/账号内容规律分析.md`（固定名，重跑直接覆盖）
- 我方：`<账号目录>/<YYYY年M月D日>账号内容规律分析.md`（带日期，同日覆盖）

校验三条与落盘都交给 `内容规律报告写入执行器.save_report`（单点实现），
本执行器只负责「找到账号目录、定出正式文件名」这一段账号相关的逻辑。
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime
from pathlib import Path

from csv安全写入 import (
    CsvTransactionError,
    read_csv_table_with_snapshot,
    serialize_csv,
    write_csv_tables_atomically,
)
from 内容规律报告写入执行器 import (
    REPORT_KIND_BENCHMARK,
    REPORT_KIND_OWN,
    ContentPatternReportError,
    save_report,
)


class AccountPatternFinalizeError(RuntimeError):
    """Raised when the account-level content-pattern report cannot be finalized."""


ACCOUNT_LIBRARY_PATHS = {
    "对标内容": Path("对标资料/对标账号库.csv"),
    "我方内容": Path("我方资料/我方账号库.csv"),
}
FORMAL_FILE_NAME = "账号内容规律分析.md"
# 对标账号库要记下这份分析写到哪（跟「概要分析路径」「详细分析路径」一个套路）。
# 我方账号库不加这一列——我方那份带日期，靠目录扫描找最新，不靠库里的列。
LIBRARY_PATH_COLUMN = "内容规律分析路径"


def _read_account(root: Path, source: str, account_id: str, platform: str) -> dict[str, str]:
    library_rel = ACCOUNT_LIBRARY_PATHS.get(source)
    if library_rel is None:
        raise AccountPatternFinalizeError(f"scope_source_invalid:{source}")
    library_path = root / library_rel
    if not library_path.is_file():
        raise AccountPatternFinalizeError(f"account_library_missing:{library_rel.as_posix()}")
    _, rows, _ = read_csv_table_with_snapshot(library_path)
    matches = [
        row
        for row in rows
        if str(row.get("账号ID") or "").strip() == account_id
        and (not platform or str(row.get("平台") or "").strip() == platform)
    ]
    if not matches:
        raise AccountPatternFinalizeError("account_not_found")
    if len(matches) > 1:
        raise AccountPatternFinalizeError(f"account_not_unique:{len(matches)}")
    return {str(key): str(value or "").strip() for key, value in matches[0].items()}


def _formal_relative_path(
    account: dict[str, str], source: str, timestamp: datetime
) -> Path:
    list_rel = str(account.get("内容列表路径") or "").strip()
    if not list_rel:
        raise AccountPatternFinalizeError("account_content_list_path_missing")
    account_dir = Path(list_rel).parent
    stem = FORMAL_FILE_NAME.removesuffix(".md")
    name = f"{stem}.md"
    if str(source) == "我方内容":
        # 同日覆盖：每天只保留一份，同一天重跑直接覆盖当天文件。
        name = f"{timestamp.year}年{timestamp.month}月{timestamp.day}日{name}"
    return account_dir / name


def _write_library_path(
    root: Path, source: str, account_id: str, platform: str, formal_rel: Path
) -> str:
    """把正式路径回写进对标账号库。我方账号库不加这一列（带日期，靠目录扫描）。"""
    library_rel = ACCOUNT_LIBRARY_PATHS[source]
    if source != "对标内容":
        return "skipped_own_library"
    library_path = root / library_rel
    fields, rows, original = read_csv_table_with_snapshot(library_path)
    if LIBRARY_PATH_COLUMN not in fields:
        raise AccountPatternFinalizeError(
            f"account_library_field_missing:{LIBRARY_PATH_COLUMN}"
        )
    matched = 0
    for row in rows:
        if str(row.get("账号ID") or "").strip() != account_id:
            continue
        if platform and str(row.get("平台") or "").strip() != platform:
            continue
        row[LIBRARY_PATH_COLUMN] = formal_rel.as_posix()
        matched += 1
    if matched != 1:
        raise AccountPatternFinalizeError(f"account_match_count:{matched}")
    try:
        write_csv_tables_atomically(
            [(library_path, fields, rows)],
            expected_originals={library_path: original},
        )
    except CsvTransactionError as exc:
        raise AccountPatternFinalizeError(f"account_library_write_failed:{exc}") from exc
    return "succeeded"


def finalize_account_pattern(
    workspace_root: str | Path,
    *,
    source: str,
    account_id: str,
    platform: str = "",
    candidate_file: str | Path,
    preparation_result_file: str | Path,
    now: datetime | None = None,
) -> dict[str, object]:
    root = Path(workspace_root).expanduser().resolve()
    if not root.is_dir():
        raise AccountPatternFinalizeError(f"workspace_root_invalid:{root}")
    normalized_id = str(account_id or "").strip()
    if not normalized_id:
        raise AccountPatternFinalizeError("account_id_missing")
    source = str(source or "").strip()
    platform = str(platform or "").strip()
    account = _read_account(root, source, account_id=normalized_id, platform=platform)
    formal_rel = _formal_relative_path(account, source, now or datetime.now().astimezone())
    # 我方报告多「与上期相比」一节、第 2 节要求完整打法表；对标报告维持三节。
    report_kind = REPORT_KIND_OWN if source == "我方内容" else REPORT_KIND_BENCHMARK
    try:
        result = save_report(
            root,
            candidate_file,
            preparation_result_file,
            report_path=formal_rel,
            report_kind=report_kind,
        )
    except ContentPatternReportError as exc:
        raise AccountPatternFinalizeError(str(exc)) from exc
    # 正式文档直接覆盖同名文件，并把路径写回对标账号库。
    library_status = _write_library_path(root, source, normalized_id, platform, Path(str(result["report_path"])))
    return {
        "ok": True,
        "status": "committed",
        "source": source,
        "account_id": normalized_id,
        "platform": platform,
        "formal_path": result["report_path"],
        "formal_sha256": result["sha256"],
        "account_library_write_status": library_status,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="校验并正式保存账号内容规律分析")
    parser.add_argument("--workspace-root", type=Path, required=True)
    parser.add_argument("--source", choices=("对标内容", "我方内容"), required=True)
    parser.add_argument("--account-id", required=True)
    parser.add_argument("--platform", default="")
    parser.add_argument("--candidate-file", required=True, type=Path)
    parser.add_argument("--preparation-result-file", required=True, type=Path)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        result = finalize_account_pattern(
            args.workspace_root,
            source=args.source,
            account_id=args.account_id,
            platform=args.platform,
            candidate_file=args.candidate_file,
            preparation_result_file=args.preparation_result_file,
        )
    except (OSError, UnicodeError, AccountPatternFinalizeError) as exc:
        print(json.dumps({"ok": False, "status": "failed", "error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
