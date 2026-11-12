#!/usr/bin/env python3
"""内容特征落表执行器（四条链路共用的入口）。

从一张「内容特征判断候选」直接落两张表：第 1/2 节写 `沉淀结论/内容特征实例.csv`，
第 3 节「待补充标签」写 `沉淀结论/内容特征观察表.csv`。

> 2026-09-27 起不再渲染 `内容画像与特征卡.md`（卡已退役）：候选本身就是结构化的，
> 渲染成卡再由写入器解析回来是一次纯往返。也不再回写两张库的卡路径列。

两种用法（都不依赖内容分析、不依赖复盘，也不依赖任何 run 状态机）：

- **单条落表**（默认，不给 `--action`）：`--content-id` + `--candidate-path`。
  四条链路（对标账号 / 我方账号 / 对标内容 / 我方内容）落一条内容都用这个；
- **批量落表**（`--action plan|commit`）：按批次上下文的锁定清单分批走，
  只给两条账号链路的详细数据获取流程用。

输入是 `内容特征判断候选` 三节（`1. 核心标准特征` / `2. 候补标准特征` /
`3. 待补充标签`），由 AI 按 `提示词/内容特征提取提示词.md` 生成。

复用说明：候选的校验与两表写入全部复用 `内容特征写入执行器.apply_feature_candidate`，
本执行器只负责「定位内容、校验身份、逐条调用、汇总回执」。
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path, PurePosixPath

from csv安全写入 import read_csv_table_with_snapshot

from 内容特征写入执行器 import (
    ContentFeatureWriteError,
    PUBLICATION_ROOT,
    _evidence_from_row,
    _own_account_lookup,
    _publication_identity_from_review_path,
    apply_feature_candidate,
)
from 账号后续详细处理批处理 import resolve_batch_context


# 卡退役后不再有 `内容画像与特征卡.md`；候选后缀保留原名（流程文档与存量
# 暂存目录都在用它，改名属另一轮）。
CANDIDATE_SUFFIX = "-特征候选.md"
INSTANCE_PATH = Path("沉淀结论/内容特征实例.csv")
TRANSCRIPT_FIELDS = ("视频文案路径",)
BENCHMARK_LIBRARY = Path("对标资料/对标内容库.csv")
OWN_LIBRARY = Path("我方资料/我方内容库.csv")
BENCHMARK_ACCOUNT_LIBRARY = Path("对标资料/对标账号库.csv")
OWN_ACCOUNT_LIBRARY = Path("我方资料/我方账号库.csv")
FLOW_ROOT = {
    "benchmark_account": "benchmark-account-diagnose",
    "self_account": "self-account-diagnose",
}


class AccountFeatureBuildError(RuntimeError):
    pass


def _library_path(source: str) -> Path:
    return BENCHMARK_LIBRARY if source == "对标内容" else OWN_LIBRARY


def _count_reasons(rows: list[dict[str, str]]) -> dict[str, int]:
    """把 `skipped` 明细按 `reason` 计数，便于在回报里一眼看出被跳过的主因。"""
    counts: dict[str, int] = {}
    for item in rows:
        reason = str(item.get("reason") or "").strip() or "unknown"
        counts[reason] = counts.get(reason, 0) + 1
    return counts
def _resolve_under(root: Path, raw: object, *, required_prefix: str = "") -> Path:
    text = str(raw or "").strip()
    if not text:
        raise AccountFeatureBuildError("relative_path_missing")
    if "\\" in text:
        raise AccountFeatureBuildError(f"path_must_use_forward_slashes:{text}")
    pure = PurePosixPath(text)
    if pure.is_absolute() or ".." in pure.parts:
        raise AccountFeatureBuildError(f"path_must_be_relative:{text}")
    resolved = root.joinpath(*pure.parts).resolve()
    try:
        resolved.relative_to(root)
    except ValueError as exc:
        raise AccountFeatureBuildError(f"path_outside_workspace:{text}") from exc
    if required_prefix:
        try:
            resolved.relative_to((root / required_prefix).resolve())
        except ValueError as exc:
            raise AccountFeatureBuildError(
                f"path_outside_expected_root:{text}:{required_prefix}"
            ) from exc
    return resolved
# 解析发布记录目录名时挂的探针叶子：`_publication_identity_from_review_path` 只认
# 「倒数第二段」是发布记录目录名，所以必须给一个叶子段，否则会切到「发布记录」本身。
_PUBLICATION_PROBE_LEAF = "_"


def _publication_review_path(root: Path, row: dict[str, str]) -> str:
    """找本轮已发布的 `发布记录/<平台>-<账号ID>-<原生ID>/`，返回可交给
    `_publication_identity_from_review_path` 解析的相对路径。

    内容生产链路新做、尚未被「账号刷新」登记进账号内容列表的内容，只在发布记录
    里留下平台与账号身份。目录不存在、名字不合形态时一律返回空串，不抛错。
    """
    content_dir_text = str(row.get("内容目录路径") or "").strip()
    if not content_dir_text:
        return ""
    try:
        pub_root = _resolve_under(root, f"{content_dir_text}/{PUBLICATION_ROOT}")
    except AccountFeatureBuildError:
        return ""
    if not pub_root.is_dir():
        return ""
    for candidate in sorted(p for p in pub_root.iterdir() if p.is_dir()):
        probe = f"{candidate.relative_to(root).as_posix()}/{_PUBLICATION_PROBE_LEAF}"
        try:
            _publication_identity_from_review_path(probe)
        except ContentFeatureWriteError:
            continue
        return probe
    return ""


def _resolved_identity(root: Path, row: dict[str, str]) -> tuple[dict[str, str], str]:
    """解析我方内容的平台／账号身份，返回 `(identity, review_path)`。

    两级，顺序不能颠倒：

    1. 账号侧反查（`_own_account_lookup`）：按内容目录路径在「我方账号库 → 该账号
       的内容列表」里找。被账号刷新链路登记过的内容走这一档；
    2. 发布记录目录名兜底：第 1 档失败时，用内容目录下
       `发布记录/<平台>-<账号ID>-<原生ID>/` 的目录名解析。内容生产链路新做、发布后
       直接走复盘的内容只有这一档可用（发布后复盘按设计不回写账号内容列表）。

    两档都拿不到时返回空 identity／空串，由调用方决定 skip 还是 raise。
    第 2 档同时回传 review_path，让写入侧 `apply_feature_candidate` 复用同一身份。
    """
    identity = _own_account_lookup(root, row)
    if identity.get("platform") and identity.get("account_id"):
        return identity, ""
    review_path = _publication_review_path(root, row)
    if not review_path:
        return identity, ""
    try:
        platform, account_id = _publication_identity_from_review_path(review_path)
    except ContentFeatureWriteError:
        return identity, ""
    resolved = dict(identity)
    resolved["platform"] = str(resolved.get("platform") or "").strip() or platform
    resolved["account_id"] = str(resolved.get("account_id") or "").strip() or account_id
    return resolved, review_path
def build_features(
    workspace_root: Path | str,
    *,
    source: str,
    content_id: str,
    candidate_path: Path | str,
    validate_only: bool = False,
    now: str | None = None,
) -> dict[str, object]:
    """把一条内容的「内容特征判断候选」落成两张表（特征实例 + 待补充标签观察）。

    不再渲染 `内容画像与特征卡.md`：候选本身就是结构化的，直接交给
    `apply_feature_candidate` 落表；候选校验也在它里面做，这里不重复实现一遍。
    """
    root = Path(workspace_root).expanduser().resolve()
    if not root.is_dir():
        raise AccountFeatureBuildError(f"workspace_root_invalid:{root}")
    library_rel = _library_path(source)
    library = root / library_rel
    if not library.is_file():
        raise AccountFeatureBuildError(f"required_table_missing:{library_rel.as_posix()}")
    _, rows, _ = read_csv_table_with_snapshot(library)
    matches = [
        row for row in rows if str(row.get("内容ID") or "").strip() == content_id
    ]
    if len(matches) != 1:
        raise AccountFeatureBuildError(f"content_match_count:{len(matches)}")
    review_path = ""
    if source == "我方内容":
        # 我方内容库没有平台/账号列：两级解析（账号侧反查 → 发布记录目录名兜底），
        # 见 `_resolved_identity`。两档都取不到才失败：实例行缺平台/账号会让汇总时
        # 把不同账号串在一起。
        identity, review_path = _resolved_identity(root, matches[0])
        if not (identity.get("platform") and identity.get("account_id")):
            raise AccountFeatureBuildError("account_identity_unresolved")
    candidate = _resolve_under(root, candidate_path)
    if not candidate.is_file():
        raise AccountFeatureBuildError(f"candidate_missing:{candidate}")
    candidate_rel = candidate.relative_to(root).as_posix()
    written = apply_feature_candidate(
        root,
        source=source,
        content_id=content_id,
        candidate_path=candidate_rel,
        review_path=review_path,
        validate_only=validate_only,
        now=now,
    )
    return {
        "ok": True,
        "status": str(written.get("status") or ""),
        "source": source,
        "content_id": content_id,
        "feature_candidate_path": candidate_rel,
        "core_feature_count": written.get("feature_count"),
        "observation_count": written.get("observation_count"),
        "analysis_version": written.get("analysis_version"),
        "instance_write": {
            "status": written.get("status"),
            "feature_count": written.get("feature_count"),
            "analysis_version": written.get("analysis_version"),
        },
        "observation_write": {
            "observation_count": written.get("observation_count"),
            "observation_labels": written.get("observation_labels"),
        },
    }


def _featured_content_ids(root: Path, source: str) -> set[str]:
    """已经落过特征实例的内容（当前有效=是）——分批循环靠它判断「这条做完了」。"""
    path = root / INSTANCE_PATH
    if not path.is_file():
        return set()
    _, rows, _ = read_csv_table_with_snapshot(path)
    return {
        str(row.get("样本键") or "").strip()
        for row in rows
        if str(row.get("样本来源") or "").strip() == source
        and str(row.get("当前有效") or "").strip() == "是"
    }


def _candidate_dir_rel(run_id: str, flow_type: str) -> str:
    flow_root = FLOW_ROOT.get(flow_type, "benchmark-account-diagnose")
    return f".meiling/staging/{flow_root}/{run_id}/特征候选"


def plan_cards(
    workspace_root: Path | str,
    *,
    source: str,
    run_id: str,
    limit: int = 0,
) -> dict[str, object]:
    """按 `2.2` 锁定的本轮内容ID清单，算出「待落表」与「跳过」两类内容。

    范围只认批次上下文里的 `selected_content_ids`（与 2.3～2.7 同源），
    不读、不猜别的清单。跳过项连同原因一起返回，免得 AI 逐条去试。

    `limit > 0` 时只返回**最前面 limit 条**待产内容（分批循环用）：游标就是
    「已落表的条数」——产完这批，下一批自然变成新的最前 N 条，不需要额外状态。
    此时 `pending_count` 是**本批条数**，`pending_total` 才是待落表总数。
    """
    root = Path(workspace_root).expanduser().resolve()
    if not root.is_dir():
        raise AccountFeatureBuildError(f"workspace_root_invalid:{root}")
    try:
        context = resolve_batch_context(root, run_id)
    except RuntimeError as exc:
        raise AccountFeatureBuildError(f"batch_context_unavailable:{exc}") from exc
    flow_type = str(context.get("flow_type") or "").strip()
    scope = context.get("scope") if isinstance(context.get("scope"), dict) else {}
    raw_scope = context.get("content_ids") or scope.get("selected_content_ids") or []
    selected = [str(item).strip() for item in raw_scope if str(item).strip()]
    if not selected:
        raise AccountFeatureBuildError(f"locked_scope_empty:{run_id}")

    library_rel = _library_path(source)
    library = root / library_rel
    if not library.is_file():
        raise AccountFeatureBuildError(f"required_table_missing:{library_rel.as_posix()}")
    _, rows, _ = read_csv_table_with_snapshot(library)
    by_id = {str(row.get("内容ID") or "").strip(): row for row in rows}
    featured = _featured_content_ids(root, source)

    pending: list[dict[str, str]] = []
    skipped: list[dict[str, str]] = []
    for content_id in selected:
        row = by_id.get(content_id)
        if row is None:
            skipped.append({"content_id": content_id, "reason": "not_in_library"})
            continue
        if content_id in featured:
            skipped.append({"content_id": content_id, "reason": "already_has_features"})
            continue
        evidence = _evidence_from_row(row)
        if not evidence:
            skipped.append({"content_id": content_id, "reason": "no_transcript"})
            continue
        if not (root / PurePosixPath(evidence)).is_file():
            skipped.append({"content_id": content_id, "reason": "transcript_file_missing"})
            continue
        if source == "我方内容":
            # 我方内容库没有平台/账号列：两级解析（账号侧反查 → 发布记录目录名兜底），
            # 见 `_resolved_identity`。内容目录路径由详细数据阶段回填；两档都拿不到
            # 的内容到 commit 阶段必然失败，不如在这里就跳过并给出原因。
            identity, _ = _resolved_identity(root, row)
            if not (identity.get("platform") and identity.get("account_id")):
                skipped.append(
                    {"content_id": content_id, "reason": "account_identity_unresolved"}
                )
                continue
        pending.append({
            "content_id": content_id,
            "title": str(row.get("标题") or "").strip(),
            "evidence_path": evidence,
        })

    pending_total = len(pending)
    batch_limit = int(limit or 0)
    if batch_limit > 0:
        pending = pending[:batch_limit]

    return {
        "ok": True,
        "status": "planned",
        "run_id": run_id,
        "source": source,
        "flow_type": flow_type,
        "locked_count": len(selected),
        "pending_count": len(pending),
        "pending_total": pending_total,
        "remaining_count": pending_total - len(pending),
        "batch_limit": batch_limit,
        "skipped_count": len(skipped),
        # 按原因汇总，供流程的固定展示文案直接说明「哪几条没落成特征、为什么」；
        # 只有 `skipped` 明细数组时，这类提示容易被淹没。
        "skipped_reasons": _count_reasons(skipped),
        "candidate_dir": _candidate_dir_rel(run_id, flow_type),
        "candidate_naming": f"<内容ID>{CANDIDATE_SUFFIX}",
        "pending": pending,
        "skipped": skipped,
    }


def commit_cards(
    workspace_root: Path | str,
    *,
    source: str,
    run_id: str,
    candidates_dir: Path | str | None = None,
    now: str | None = None,
    limit: int = 0,
) -> dict[str, object]:
    """整批落表：读候选目录，逐条校验并写两张表，返回批次回执。

    单条失败只记为该条失败，继续下一条，不中断整批。候选按
    `<内容ID>-特征候选.md` 命名放在同一个目录里。

    `limit > 0` 时只处理**本批 limit 条**（与 plan 用同一个 limit 才能对上）；
    已落表的条目在下一轮自动变成 `already_has_features` 跳过，所以分批可续、幂等。
    """
    root = Path(workspace_root).expanduser().resolve()
    plan = plan_cards(root, source=source, run_id=run_id, limit=limit)
    dir_rel = str(candidates_dir or plan["candidate_dir"]).strip()
    directory = root / PurePosixPath(dir_rel)
    if not directory.is_dir():
        raise AccountFeatureBuildError(f"candidate_dir_missing:{dir_rel}")

    items: list[dict[str, object]] = []
    committed = 0
    for entry in plan["pending"]:
        content_id = str(entry["content_id"])
        candidate_rel = f"{dir_rel}/{content_id}{CANDIDATE_SUFFIX}"
        if not (root / PurePosixPath(candidate_rel)).is_file():
            items.append({
                "content_id": content_id,
                "status": "failed",
                "error": f"candidate_missing:{candidate_rel}",
            })
            continue
        try:
            result = build_features(
                root,
                source=source,
                content_id=content_id,
                candidate_path=candidate_rel,
                now=now,
            )
        except (
            AccountFeatureBuildError,
            ContentFeatureWriteError,
            OSError,
            ValueError,
        ) as exc:
            items.append({
                "content_id": content_id,
                "status": "failed",
                "error": str(exc),
            })
            continue
        committed += 1
        items.append({
            "content_id": content_id,
            "status": "committed",
            "feature_candidate_path": result.get("feature_candidate_path"),
            "core_feature_count": result.get("core_feature_count"),
            "observation_count": result.get("observation_count"),
            "analysis_version": result.get("analysis_version"),
            "instance_status": (result.get("instance_write") or {}).get("status"),
        })

    failed = len(items) - committed
    return {
        "ok": True,
        "status": "committed" if failed == 0 else "partial",
        "run_id": run_id,
        "source": source,
        "candidate_dir": dir_rel,
        "batch": {
            "locked": plan["locked_count"],
            "pending": plan["pending_count"],
            "pending_total": plan["pending_total"],
            "batch_limit": plan["batch_limit"],
            "skipped": plan["skipped_count"],
            "committed": committed,
            "failed": failed,
        },
        "remaining_count": max(0, int(plan["pending_total"]) - committed),
        "items": items,
        "skipped": plan["skipped"],
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="内容特征落表：候选第 1、2 节写特征实例表，第 3 节写观察表"
    )
    parser.add_argument("--workspace-root", required=True, type=Path, help="当前工作区根目录")
    parser.add_argument(
        "--source",
        required=True,
        choices=("对标内容", "我方内容"),
        help="这条内容属于哪一侧（决定读哪个内容库与渲染口径）",
    )
    parser.add_argument(
        "--action",
        default="",
        choices=("plan", "commit"),
        help="批次动作：plan 按锁定清单只返回待落表清单；commit 读候选目录整批落表；都不给则按单条落表",
    )
    parser.add_argument("--run-id", default="", help="本轮运行标识（批次动作必填，用于解析锁定清单）")
    parser.add_argument(
        "--candidates-dir",
        default="",
        help="整批候选所在目录（工作区内相对路径）；缺省用 plan 返回的 candidate_dir",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=0,
        help="分批条数：只处理最前面 N 条待落表内容（0 = 不分批，一次全部）；plan 与 commit 必须传同一个值",
    )
    parser.add_argument("--content-id", default="", help="内容 ID（单条落表时必填）")
    parser.add_argument(
        "--candidate-path",
        default="",
        help="内容特征判断候选 Markdown 的路径（工作区内相对路径，单条落表时必填）",
    )
    parser.add_argument(
        "--validate-only",
        action="store_true",
        help="只校验，不写任何文件（仅单条落表可用）",
    )
    args = parser.parse_args()
    try:
        if args.action:
            if not args.run_id.strip():
                raise AccountFeatureBuildError("run_id_missing")
            if args.action == "plan":
                result = plan_cards(
                    args.workspace_root,
                    source=args.source,
                    run_id=args.run_id.strip(),
                    limit=args.limit,
                )
            else:
                result = commit_cards(
                    args.workspace_root,
                    source=args.source,
                    run_id=args.run_id.strip(),
                    candidates_dir=args.candidates_dir.strip() or None,
                    limit=args.limit,
                )
        else:
            if not args.content_id.strip() or not args.candidate_path.strip():
                raise AccountFeatureBuildError("content_id_and_candidate_path_required")
            result = build_features(
                args.workspace_root,
                source=args.source,
                content_id=args.content_id.strip(),
                candidate_path=args.candidate_path.strip(),
                validate_only=args.validate_only,
            )
    except (
        AccountFeatureBuildError,
        ContentFeatureWriteError,
        OSError,
        ValueError,
    ) as exc:
        print(
            json.dumps(
                {"ok": False, "status": "failed", "error": str(exc)},
                ensure_ascii=False,
            )
        )
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
