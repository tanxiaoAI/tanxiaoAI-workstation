from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Iterable, Mapping

from csv安全写入 import read_csv_table_with_snapshot


ACCOUNT_LIBRARY = Path("对标资料/对标账号库.csv")


class AccountContentListSyncError(RuntimeError):
    pass


@dataclass
class AccountContentListSync:
    status: str
    path: Path | None = None
    fields: list[str] | None = None
    rows: list[dict[str, str]] | None = None
    snapshot: bytes | None = None


def _resolve_under(root: Path, raw_path: str) -> Path:
    text = str(raw_path or "").strip().replace("\\", "/")
    if not text:
        raise AccountContentListSyncError("account_content_list_path_missing")
    logical = PurePosixPath(text)
    candidate = Path(text).expanduser()
    resolved = candidate.resolve() if candidate.is_absolute() else root.joinpath(*logical.parts).resolve()
    try:
        resolved.relative_to(root.resolve())
    except ValueError as exc:
        raise AccountContentListSyncError("account_content_list_path_outside_workspace") from exc
    return resolved


def prepare_account_content_list_sync(
    root: Path,
    content_row: Mapping[str, str],
    *,
    required_sync_fields: Iterable[str] = (),
    # 默认对标账号库；我方侧传 我方资料/我方账号库.csv（两库同构：都有
    # 平台 / 账号ID / 内容列表路径 三列）。模块名保留原样以免牵动既有 import。
    account_library: Path = ACCOUNT_LIBRARY,
) -> AccountContentListSync:
    content_id = str(content_row.get("内容ID") or "").strip()
    platform = str(content_row.get("平台") or "").strip()
    account_id = str(content_row.get("账号ID") or "").strip()
    if not content_id:
        raise AccountContentListSyncError("content_id_missing_for_account_sync")
    if not platform or not account_id:
        return AccountContentListSync(status="not_applicable")

    account_path = root / account_library
    if not account_path.is_file():
        raise AccountContentListSyncError(f"account_library_missing:{account_path}")
    account_fields, account_rows, _ = read_csv_table_with_snapshot(account_path)
    missing_account_fields = sorted({"平台", "账号ID", "内容列表路径"} - set(account_fields))
    if missing_account_fields:
        raise AccountContentListSyncError(
            f"account_library_missing_fields:{missing_account_fields}"
        )
    matches = [
        row
        for row in account_rows
        if row.get("平台", "").strip() == platform
        and row.get("账号ID", "").strip() == account_id
    ]
    if not matches:
        return AccountContentListSync(status="not_applicable")
    if len(matches) != 1:
        raise AccountContentListSyncError(f"account_match_count_for_sync:{len(matches)}")

    list_path = _resolve_under(root, matches[0].get("内容列表路径", ""))
    if not list_path.is_file():
        raise AccountContentListSyncError(f"account_content_list_missing:{list_path}")
    fields, rows, snapshot = read_csv_table_with_snapshot(list_path)
    required_fields = {"内容ID", "平台", "账号ID", *required_sync_fields}
    missing_fields = sorted(required_fields - set(fields))
    if missing_fields:
        raise AccountContentListSyncError(
            f"account_content_list_missing_fields:{missing_fields}"
        )

    targets = [row for row in rows if row.get("内容ID", "").strip() == content_id]
    if len(targets) > 1:
        raise AccountContentListSyncError(f"account_content_id_duplicate:{content_id}")
    if targets:
        target = targets[0]
        for field, expected in (("平台", platform), ("账号ID", account_id)):
            current = target.get(field, "").strip()
            if current and current != expected:
                raise AccountContentListSyncError(
                    f"account_content_identity_mismatch:{content_id}:{field}"
                )
    else:
        target = {field: "" for field in fields}
        rows.append(target)

    for field in fields:
        value = str(content_row.get(field) or "")
        if value.strip():
            target[field] = value
    target["内容ID"] = content_id
    target["平台"] = platform
    target["账号ID"] = account_id
    return AccountContentListSync(
        status="synced",
        path=list_path,
        fields=fields,
        rows=rows,
        snapshot=snapshot,
    )


def verify_account_content_list_sync(
    sync: AccountContentListSync,
    expected_row: Mapping[str, str],
    *,
    required_sync_fields: Iterable[str] = (),
) -> None:
    if sync.status == "not_applicable":
        return
    if sync.path is None:
        raise AccountContentListSyncError("account_content_list_sync_path_missing")
    _, rows, _ = read_csv_table_with_snapshot(sync.path)
    content_id = str(expected_row.get("内容ID") or "").strip()
    matches = [row for row in rows if row.get("内容ID", "").strip() == content_id]
    if len(matches) != 1:
        raise AccountContentListSyncError(
            f"account_content_list_post_commit_match_count:{len(matches)}"
        )
    actual = matches[0]
    fields_to_check = {"内容ID", "平台", "账号ID", *required_sync_fields}
    for field in fields_to_check:
        if actual.get(field, "").strip() != str(expected_row.get(field) or "").strip():
            raise AccountContentListSyncError(
                f"account_content_list_post_commit_mismatch:{content_id}:{field}"
            )
