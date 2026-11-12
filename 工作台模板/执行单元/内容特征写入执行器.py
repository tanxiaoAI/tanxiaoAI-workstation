#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from datetime import datetime
from pathlib import Path, PurePosixPath
from typing import Mapping, Sequence

from csv安全写入 import (
    CsvTransactionError,
    read_csv_table_with_snapshot,
    write_csv_tables_atomically,
)
from 内容特征词典 import ContentFeatureDictionaryError, load_feature_catalog


class ContentFeatureWriteError(RuntimeError):
    """Raised when content feature evidence cannot be committed safely."""
MEDIA_FORMS = {"视频", "图文", "文章", "其他"}
INSTANCE_PATH = Path("沉淀结论/内容特征实例.csv")
# 自 2026-09-27 起：候选文档直接拆两路落表 —— 第 1/2 节进实例表，第 3 节进观察表。
# `内容画像与特征卡.md` 退役，不再渲染、不再回写两张库的卡路径列。
OBSERVATION_PATH = Path("沉淀结论/内容特征观察表.csv")
BENCHMARK_LIBRARY_PATH = Path("对标资料/对标内容库.csv")
OWN_LIBRARY_PATH = Path("我方资料/我方内容库.csv")
OWN_ACCOUNT_LIBRARY_PATH = Path("我方资料/我方账号库.csv")

INSTANCE_REQUIRED_FIELDS = {
    "特征实例ID", "样本键", "样本来源", "内容ID", "平台", "账号ID", "标题",
    "媒介形式", "分析版本", "当前有效",
    "特征ID", "所属层级", "特征小类", "标准标签",
    "实际采用状态", "实例证据", "证据路径", "证据位置",
    "创建时间", "备注",
}
# 观察表列顺序即表头顺序（`8.2 新表定稿`）。
OBSERVATION_FIELDS = (
    "观察ID", "样本来源", "样本键", "内容ID", "平台", "账号ID", "标题",
    "特征小类", "标准标签", "观察说明", "证据部位", "当前有效", "分析版本",
    "创建时间", "备注",
)
OBSERVATION_REQUIRED_FIELDS = set(OBSERVATION_FIELDS)
# 观察表的「特征小类」必须取自现有 6 个小类：它能唯一推出所属层级
# （内容类别 → 策略层，其余 → 动作层），所以表里不另存层级。
OBSERVATION_TYPE_CATEGORIES = (
    "内容类别", "标题", "开头", "内容结构", "表达风格", "互动设计",
)
ID_PATTERNS = {
    "特征实例": re.compile(r"^特征实例(?P<number>\d{3,})$"),
    "内容特征观察": re.compile(r"^内容特征观察(?P<number>\d{3,})$"),
}
ALLOWED_SOURCES = {"对标内容", "我方内容"}
PUBLICATION_ROOT = "发布记录"


def _require_string(value: object, label: str, *, allow_blank: bool = False) -> str:
    if not isinstance(value, str):
        raise ContentFeatureWriteError(f"invalid_value:{label}_must_be_string")
    if value != value.strip():
        raise ContentFeatureWriteError(f"invalid_value:{label}_outer_whitespace")
    if not allow_blank and not value:
        raise ContentFeatureWriteError(f"invalid_value:{label}_blank")
    return value


def _read_table(
    root: Path,
    relative_path: Path,
    required_fields: set[str],
) -> tuple[Path, list[str], list[dict[str, str]], bytes]:
    path = root / relative_path
    if not path.is_file():
        raise ContentFeatureWriteError(f"required_table_missing:{relative_path.as_posix()}")
    try:
        fields, rows, snapshot = read_csv_table_with_snapshot(path)
    except CsvTransactionError as exc:
        raise ContentFeatureWriteError(str(exc)) from exc
    missing = sorted(required_fields - set(fields))
    if missing:
        raise ContentFeatureWriteError(
            f"required_table_missing_fields:{relative_path.as_posix()}:{missing}"
        )
    return path, fields, rows, snapshot


def _load_catalog(root: Path) -> dict[str, dict[str, object]]:
    try:
        return load_feature_catalog(root)
    except ContentFeatureDictionaryError as exc:
        raise ContentFeatureWriteError(f"feature_catalog_invalid:{exc}") from exc
def _validate_identity(value: str, label: str, *, allow_blank: bool = False) -> str:
    text = _require_string(value, label, allow_blank=allow_blank)
    if "|" in text:
        raise ContentFeatureWriteError(f"identity_contains_separator:{label}")
    return text


def _sample_key(content_id: str) -> str:
    return content_id


def _validate_unique_ids(rows: Sequence[Mapping[str, str]], field: str, prefix: str) -> list[int]:
    seen: set[str] = set()
    numbers: list[int] = []
    for row in rows:
        value = str(row.get(field, "")).strip()
        if not value:
            continue
        if value in seen:
            raise ContentFeatureWriteError(f"duplicate_existing_id:{field}:{value}")
        seen.add(value)
        match = ID_PATTERNS[prefix].fullmatch(value)
        if match is None:
            raise ContentFeatureWriteError(f"invalid_existing_id:{field}:{value}")
        numbers.append(int(match.group("number")))
    return numbers


def _next_id(rows: Sequence[Mapping[str, str]], field: str, prefix: str) -> str:
    return f"{prefix}{max(_validate_unique_ids(rows, field, prefix), default=0) + 1:03d}"


def _blank_row(fields: Sequence[str]) -> dict[str, str]:
    return {field: "" for field in fields}


def _now_text(now: str | None) -> str:
    return now or datetime.now().astimezone().isoformat(timespec="seconds")


def _section(markdown: str, heading: str) -> str:
    match = re.search(rf"(?m)^##\s+{re.escape(heading)}\s*$", markdown)
    if match is None:
        raise ContentFeatureWriteError(f"feature_candidate_section_missing:{heading}")
    following = re.search(r"(?m)^##\s+", markdown[match.end():])
    end = match.end() + following.start() if following else len(markdown)
    return markdown[match.end():end]
def _publication_identity_from_review_path(review_path: str) -> tuple[str, str]:
    """从我方「发布后复盘路径」解析本次发布的平台与账号。

    我方内容在发布前拿不到平台，特征实例的平台和账号只能在发布后确定。
    发布时间记录目录名按 `<平台>-<账号ID>-<平台原生内容ID>` 生成，
    三段之间用短横线连接。这里从右往左切两刀，所以平台名里即使带短横线也不会切错。

    卡退役前这段读的是卡里那一行 `- 发布后复盘路径：`；现在卡不再生成，
    改由调用方直接把自己手里那份复盘路径传进来（同值，少一次渲染往返）。
    """
    review_path = str(review_path or "").strip().replace("\\", "/")
    if not review_path:
        raise ContentFeatureWriteError("own_publication_review_path_missing")
    parts = PurePosixPath(review_path).parts
    if len(parts) < 3 or PUBLICATION_ROOT not in parts[:-1]:
        raise ContentFeatureWriteError("own_publication_review_path_invalid")
    directory_name = parts[-2]
    identity = directory_name.rsplit("-", 2)
    if len(identity) != 3 or not identity[0] or not identity[1]:
        raise ContentFeatureWriteError(
            f"own_publication_identity_unresolved:{directory_name}"
        )
    return identity[0], identity[1]


def _table(section: str, expected_header: list[str]) -> list[list[str]]:
    rows: list[list[str]] = []
    header_seen = False
    for raw_line in section.splitlines():
        line = raw_line.strip()
        if not line.startswith("|"):
            continue
        cells = [cell.strip().strip("`") for cell in line.strip("|").split("|")]
        if cells == expected_header:
            if header_seen:
                raise ContentFeatureWriteError(f"feature_candidate_table_duplicate:{expected_header[0]}")
            header_seen = True
            continue
        if all(re.fullmatch(r":?-{3,}:?", cell.replace(" ", "")) for cell in cells):
            continue
        if header_seen:
            if len(cells) != len(expected_header):
                raise ContentFeatureWriteError(f"feature_candidate_table_columns_invalid:{expected_header[0]}")
            rows.append(cells)
    if not header_seen:
        raise ContentFeatureWriteError(f"feature_candidate_table_missing:{expected_header[0]}")
    return rows
def _evidence_from_row(row: dict[str, str]) -> str:
    """实例行的「证据路径」＝这条证据的原文出处：视频文案。

    取不到就**留空** —— 不校验文件是否存在、也不做非空校验（图文内容没有视频文案是
    正常情况，视频也可能转写失败）。因此这里刻意不走 `_validate_relative_file`。
    """
    for field in ("视频文案路径",):
        value = str(row.get(field) or "").strip()
        if value:
            return value
    return ""


def _account_view_lookup(
    root: Path,
    row: dict[str, str],
    *,
    account_library: Path,
) -> dict[str, str]:
    """在指定账号库的各「账号内容列表」里，按内容目录路径反查账号侧的字段。

    返回 `platform` / `account_id` / `data_group`。
    查不到就返回空 dict，由调用方按缺省处理（不抛错）。

    注：这里**不再**顺手算「账号内档次」——该列已从实例表删除，改由报告阶段
    按账号内容列表现算（口径单点仍在 `compute_account_rank`）。
    """
    content_dir = str(row.get("内容目录路径") or "").strip()
    if not content_dir:
        return {}
    if not (root / account_library).is_file():
        return {}
    try:
        _, _, accounts, _ = _read_table(
            root,
            account_library,
            {"账号ID", "平台", "内容列表路径"},
        )
    except ContentFeatureWriteError:
        return {}
    for account in accounts:
        list_text = str(account.get("内容列表路径") or "").strip()
        if not list_text:
            continue
        try:
            _, _, rows, _ = _read_table(
                root,
                PurePosixPath(list_text),
                {"内容ID", "内容目录路径"},
            )
        except ContentFeatureWriteError:
            continue
        for candidate in rows:
            if str(candidate.get("内容目录路径") or "").strip() != content_dir:
                continue
            return {
                "platform": str(
                    candidate.get("平台") or account.get("平台") or ""
                ).strip(),
                "account_id": str(
                    candidate.get("账号ID") or account.get("账号ID") or ""
                ).strip(),
                "data_group": str(candidate.get("数据水平") or "").strip(),
            }
    return {}


def _own_account_lookup(root: Path, row: dict[str, str]) -> dict[str, str]:
    """我方侧：平台／账号／数据水平都要从账号侧取。

    我方内容库以**内容**为主体，这几列都没有；历史已发布内容又没有
    「发布后复盘路径」，所以平台与账号也只能从账号侧取。
    """
    return _account_view_lookup(root, row, account_library=OWN_ACCOUNT_LIBRARY_PATH)
def _resolve_content_inputs(
    root: Path,
    *,
    source: str,
    content_id: str,
    platform: str,
    account_id: str = "",
    review_path: str = "",
) -> dict[str, str]:
    """按内容身份从内容库／账号侧凑出写实例行与观察行要用的字段。

    卡退役后不再有「内容画像与特征卡路径」可读，所以对标侧只认**内容库**里的
    平台／账号／标题／数据水平／媒介形式；我方侧仍走「账号库 → 内容列表」反查，
    查不到时用调用方传进来的 `review_path`（发布后复盘链路）补平台与账号。
    """
    if source == "对标内容":
        _, _, rows, _ = _read_table(
            root,
            BENCHMARK_LIBRARY_PATH,
            {"内容ID", "平台", "账号ID", "标题", "数据水平", "媒介形式"},
        )
        matches = [
            row for row in rows
            if row.get("内容ID", "").strip() == content_id
        ]
        if len(matches) != 1:
            raise ContentFeatureWriteError(f"content_match_count:{len(matches)}")
        row = matches[0]
        if platform and row.get("平台", "").strip() != platform:
            raise ContentFeatureWriteError("content_platform_mismatch")
        return {
            "source": source,
            "content_id": content_id,
            "platform": row.get("平台", "").strip(),
            "account_id": row.get("账号ID", "").strip(),
            "title": row.get("标题", "").strip(),
            "data_group": row.get("数据水平", "").strip(),
            "media_form": row.get("媒介形式", "").strip(),
            # 「证据路径」＝原文出处，与内容分析解耦（见 _evidence_from_row）。
            "evidence_path": _evidence_from_row(row),
        }

    _, _, rows, _ = _read_table(
        root,
        OWN_LIBRARY_PATH,
        {
            "内容ID", "标题", "媒介形式", "内容目录路径",
        },
    )
    matches = [
        row for row in rows
        if row.get("内容ID", "").strip() == content_id
    ]
    if len(matches) != 1:
        raise ContentFeatureWriteError(f"content_match_count:{len(matches)}")
    row = matches[0]
    content_dir_text = row.get("内容目录路径", "").strip()
    if not content_dir_text:
        raise ContentFeatureWriteError("own_content_directory_missing")
    # 平台／账号／数据水平不在内容库（以内容为主体），到「我方账号库 → 该账号的
    # 内容列表」按内容目录路径反查。查不到时用 review_path 的目录名兜底
    # （发布后复盘链路手里就有这份路径，不必再从卡里读）。
    account_view = _own_account_lookup(root, row)
    resolved_platform = account_view.get("platform", "")
    resolved_account_id = account_view.get("account_id", "")
    if (not resolved_platform or not resolved_account_id) and review_path:
        from_review_platform, from_review_account = _publication_identity_from_review_path(
            review_path
        )
        resolved_platform = resolved_platform or from_review_platform
        resolved_account_id = resolved_account_id or from_review_account
    if account_id and resolved_account_id and account_id != resolved_account_id:
        raise ContentFeatureWriteError(
            f"own_content_account_mismatch:{account_id}:{resolved_account_id}"
        )
    return {
        "source": source,
        "content_id": content_id,
        "platform": resolved_platform,
        "account_id": resolved_account_id,
        "title": row.get("标题", "").strip(),
        "data_group": account_view.get("data_group", ""),
        "media_form": row.get("媒介形式", "").strip(),
        # 「证据路径」＝原文出处；我方内容库同样有「视频文案路径」列。
        "evidence_path": _evidence_from_row(row),
    }


CANDIDATE_TITLE = "内容特征判断候选"
# 候选文档的节号（1./2./3.）与旧卡（2./3./4.）**不同**，解析时别弄反。
CORE_SECTION = "1. 核心标准特征"
CANDIDATE_SECTION = "2. 候补标准特征"
OBSERVATION_SECTION = "3. 待补充标签"
FEATURE_HEADER = ["特征ID", "实例证据", "证据部位"]
CANDIDATE_FEATURE_HEADER = FEATURE_HEADER + ["候补理由"]
OBSERVATION_HEADER = ["特征小类", "标准标签", "观察说明", "证据部位"]
MAX_CANDIDATE_FEATURES = 4


def analysis_version_from_feature_set(triples: list[tuple[str, str, str]]) -> str:
    """公开入口：把 (特征ID, 证据, 部位) 三元组集合算成分析版本。

    单条链路的 2.4.2 与写入器自己都要算这个值，**必须同一份实现**——不然
    状态机拿 2.4.2 存的版本去比写入器回执的版本会对不上。
    """
    normalized = sorted([str(a), str(b), str(c)] for a, b, c in triples)
    payload = json.dumps(normalized, ensure_ascii=False, separators=(",", ":"))
    return f"sha256:{hashlib.sha256(payload.encode('utf-8')).hexdigest()[:12]}"


def _analysis_version_of(
    features: list[dict[str, str]],
    *,
    explicit: str,
) -> str:
    """版本号 = **特征集合**的规范化 sha256（不掺观察表）。

    旧版是「卡文件自身的 sha」。卡退役后不能再用文件哈希：候选由模型生成，
    重跑同样内容若有措辞微调，哈希就会变，实例表会凭空多出一轮。

    所以改成对 (特征ID, 实例证据, 证据部位) 三元组排序后序列化再取哈希 ——
    **特征集合没变，版本就不变**（这比旧口径更强）。候补特征一并计入，因为它
    们同样落成实例行（传入的 `features` 已经含核心 + 候补）。

    ⚠️ 观察表**不进**这个指纹：否则「只改了观察说明」也会把实例行的版本键改掉，
    实例表就会重复登记。观察表自己的幂等靠 (来源, 样本键, 分析版本, 标准标签)。
    """
    if explicit:
        return explicit
    return analysis_version_from_feature_set(
        [
            (str(item["特征ID"]), str(item["实例证据"]), str(item["证据位置"]))
            for item in features
        ]
    )


def _parse_candidate(
    root: Path,
    sample: dict[str, str],
    *,
    candidate_path: Path,
    analysis_version: str,
) -> tuple[list[dict[str, str]], list[dict[str, str]], str]:
    """把「内容特征判断候选」拆成两路：特征实例行 + 待补充标签观察行。"""
    markdown = candidate_path.read_text(encoding="utf-8-sig")
    if len(re.findall(rf"(?m)^#\s+{CANDIDATE_TITLE}\s*$", markdown)) != 1:
        raise ContentFeatureWriteError("feature_candidate_title_invalid")

    rows = _table(_section(markdown, CORE_SECTION), FEATURE_HEADER)
    candidate_rows = _table(_section(markdown, CANDIDATE_SECTION), CANDIDATE_FEATURE_HEADER)
    if len(candidate_rows) > MAX_CANDIDATE_FEATURES:
        raise ContentFeatureWriteError(
            f"too_many_candidate_features:maximum={MAX_CANDIDATE_FEATURES}:"
            f"actual={len(candidate_rows)}"
        )
    observation_rows = _table(_section(markdown, OBSERVATION_SECTION), OBSERVATION_HEADER)

    catalog = _load_catalog(root)
    features: list[dict[str, str]] = []
    seen: set[str] = set()
    type_counts: dict[str, int] = {}

    def _append_row(index: int, row: list[str], *, kind: str) -> None:
        feature_id, evidence, location = row[:3]
        definition = catalog.get(feature_id)
        if definition is None:
            raise ContentFeatureWriteError(f"unknown_feature_id:{feature_id}")
        if feature_id in seen:
            raise ContentFeatureWriteError(f"duplicate_feature_id:{feature_id}")
        seen.add(feature_id)
        layer = str(definition["layer"])
        feature_type = str(definition["feature_type"])
        if kind == "核心":
            type_counts[feature_type] = type_counts.get(feature_type, 0) + 1
            if type_counts[feature_type] > 1:
                raise ContentFeatureWriteError(
                    f"feature_type_count_exceeded:{feature_type}:actual={type_counts[feature_type]}"
                )
        features.append(
            {
                "特征ID": feature_id,
                "所属层级": layer,
                "特征小类": feature_type,
                "标准标签": str(definition["label"]),
                "实际采用状态": "已采用",
                "实例证据": _require_string(evidence, f"{kind}[{index}].evidence"),
                # 原文出处；取不到就是空串（不校验存在性）。
                "证据路径": sample.get("evidence_path", ""),
                "证据位置": _require_string(location, f"{kind}[{index}].location"),
            }
        )

    for index, row in enumerate(rows):
        _append_row(index, row, kind="核心")
    # 候补实例与主特征实例同等写入，不加角色列。
    for index, row in enumerate(candidate_rows):
        _append_row(index, row, kind="候补")

    observations: list[dict[str, str]] = []
    for index, row in enumerate(observation_rows):
        feature_category, label, description, location = row[:4]
        feature_category = _require_string(
            feature_category, f"observation[{index}].feature_category"
        )
        if feature_category not in OBSERVATION_TYPE_CATEGORIES:
            raise ContentFeatureWriteError(
                f"observation_feature_category_invalid:{feature_category}"
            )
        observations.append(
            {
                "特征小类": feature_category,
                # 这些标签词典里还没有，所以观察表**没有特征ID列**。
                "标准标签": _require_string(label, f"observation[{index}].label"),
                "观察说明": _require_string(
                    description, f"observation[{index}].description"
                ),
                "证据部位": _require_string(location, f"observation[{index}].location"),
            }
        )

    resolved_version = _analysis_version_of(features, explicit=analysis_version)
    return features, observations, resolved_version


def _prepare_observations(
    observation_fields: list[str],
    observation_rows: list[dict[str, str]],
    observations: list[dict[str, str]],
    *,
    source: str,
    stable_key: str,
    content_id: str,
    platform: str,
    account_id: str,
    title: str,
    analysis_version: str,
    timestamp: str,
) -> list[dict[str, str]]:
    """观察表的写入准备：机制与实例表一致（旧行置否 → 写新行 → 保留历史）。

    幂等键 = (样本来源, 样本键, 分析版本, 标准标签)。同一篇的旧观察先整体置
    `当前有效=否`，再按新观察逐条写 `是` —— 这样标签被删掉时不会留下幽灵行。
    """
    prepared = [dict(row) for row in observation_rows]
    existing: dict[tuple[str, str, str, str], int] = {}
    for index, row in enumerate(prepared):
        key = (
            row.get("样本来源", ""),
            row.get("样本键", ""),
            row.get("分析版本", ""),
            row.get("标准标签", ""),
        )
        if all(key):
            if key in existing:
                raise ContentFeatureWriteError(f"duplicate_existing_observation:{key}")
            existing[key] = index
        if row.get("样本键") == stable_key and row.get("样本来源") == source:
            row["当前有效"] = "否"
    for observation in observations:
        key = (source, stable_key, analysis_version, observation["标准标签"])
        if key in existing:
            observation_row = prepared[existing[key]]
        else:
            observation_row = _blank_row(observation_fields)
            observation_row["观察ID"] = _next_id(
                prepared, "观察ID", "内容特征观察"
            )
            prepared.append(observation_row)
        observation_row.update(
            {
                "样本来源": source,
                "样本键": stable_key,
                "内容ID": content_id,
                "平台": platform,
                "账号ID": account_id,
                "标题": title,
                "当前有效": "是",
                "分析版本": analysis_version,
                "创建时间": observation_row.get("创建时间") or timestamp,
                "备注": "",
                **observation,
            }
        )
    return prepared


def _write_analysis(
    root: Path,
    sample: dict[str, str],
    features: list[dict[str, str]],
    observations: list[dict[str, str]],
    analysis_version: str,
    *,
    validate_only: bool,
    now: str | None,
) -> dict[str, object]:
    source = _validate_identity(sample["source"], "source")
    if source not in ALLOWED_SOURCES:
        raise ContentFeatureWriteError(f"invalid_sample_source:{source}")
    content_id = _validate_identity(sample["content_id"], "content_id")
    platform = _validate_identity(sample["platform"], "platform", allow_blank=True)
    account_id = _validate_identity(sample["account_id"], "account_id", allow_blank=True)
    title = _require_string(sample["title"], "title")
    media_form = _require_string(sample["media_form"], "media_form", allow_blank=True)
    if sample["media_form"] and sample["media_form"] not in MEDIA_FORMS:
        raise ContentFeatureWriteError("media_form_invalid")
    stable_key = _sample_key(content_id)
    feature_ids = sorted(feature["特征ID"] for feature in features)
    observation_labels = [observation["标准标签"] for observation in observations]

    instance_path, instance_fields, instance_rows, instance_snapshot = _read_table(
        root, INSTANCE_PATH, INSTANCE_REQUIRED_FIELDS
    )
    observation_path, observation_fields, observation_rows, observation_snapshot = _read_table(
        root, OBSERVATION_PATH, OBSERVATION_REQUIRED_FIELDS
    )

    timestamp = _now_text(now)

    prepared_instances = [dict(row) for row in instance_rows]
    # 身份要带样本来源：对标内容和我方内容各自编号，只按「样本键 + 分析版本 +
    # 特征ID」判同一行会把两条互不相干的内容混成一条，也会把对方置为无效。
    existing: dict[tuple[str, str, str, str], int] = {}
    for index, row in enumerate(prepared_instances):
        key = (
            row.get("样本来源", ""),
            row.get("样本键", ""),
            row.get("分析版本", ""),
            row.get("特征ID", ""),
        )
        if all(key):
            if key in existing:
                raise ContentFeatureWriteError(f"duplicate_existing_feature_instance:{key}")
            existing[key] = index
        if row.get("样本键") == stable_key and row.get("样本来源") == source:
            row["当前有效"] = "否"
    for feature in features:
        key = (source, stable_key, analysis_version, feature["特征ID"])
        if key in existing:
            instance_row = prepared_instances[existing[key]]
        else:
            instance_row = _blank_row(instance_fields)
            instance_row["特征实例ID"] = _next_id(
                prepared_instances, "特征实例ID", "特征实例"
            )
            prepared_instances.append(instance_row)
        instance_row.update(
            {
                "样本键": stable_key,
                "样本来源": source,
                "内容ID": content_id,
                "平台": platform,
                "账号ID": account_id,
                "标题": title,
                "媒介形式": media_form,
                "分析版本": analysis_version,
                "当前有效": "是",
                "创建时间": instance_row.get("创建时间") or timestamp,
                "备注": "",
                **feature,
            }
        )

    prepared_observations = _prepare_observations(
        observation_fields,
        observation_rows,
        observations,
        source=source,
        stable_key=stable_key,
        content_id=content_id,
        platform=platform,
        account_id=account_id,
        title=title,
        analysis_version=analysis_version,
        timestamp=timestamp,
    )

    if validate_only:
        return {
            "status": "planned",
            "kind": "analysis",
            "sample_key": stable_key,
            "feature_count": len(features),
            "feature_ids": feature_ids,
            "observation_count": len(observations),
            "observation_labels": observation_labels,
            "analysis_version": analysis_version,
            "main_index_write_status": "not_started",
        }
    try:
        write_csv_tables_atomically(
            [
                (instance_path, instance_fields, prepared_instances),
                (observation_path, observation_fields, prepared_observations),
            ],
            expected_originals={
                instance_path: instance_snapshot,
                observation_path: observation_snapshot,
            },
        )
    except CsvTransactionError as exc:
        raise ContentFeatureWriteError(f"csv_transaction_failed:{exc}") from exc

    _, _, committed_instances, _ = _read_table(root, INSTANCE_PATH, INSTANCE_REQUIRED_FIELDS)
    active_instances = [
        row for row in committed_instances
        if row.get("样本键") == stable_key and row.get("当前有效") == "是"
    ]
    if len(active_instances) != len(features):
        raise ContentFeatureWriteError("post_write_active_record_mismatch")
    _, _, committed_observations, _ = _read_table(
        root, OBSERVATION_PATH, OBSERVATION_REQUIRED_FIELDS
    )
    active_observations = [
        row for row in committed_observations
        if row.get("样本键") == stable_key and row.get("当前有效") == "是"
    ]
    if len(active_observations) != len(observations):
        raise ContentFeatureWriteError("post_write_active_observation_mismatch")
    return {
        "status": "committed",
        "kind": "analysis",
        "sample_key": stable_key,
        "feature_count": len(features),
        "feature_ids": feature_ids,
        "observation_count": len(observations),
        "observation_labels": observation_labels,
        "analysis_version": analysis_version,
        "main_index_write_status": "succeeded",
    }


def apply_feature_candidate(
    workspace_root: Path | str,
    *,
    source: str,
    content_id: str,
    candidate_path: Path | str,
    platform: str = "",
    account_id: str = "",
    review_path: str = "",
    run_id: str = "",
    analysis_version: str = "",
    validate_only: bool = False,
    now: str | None = None,
) -> dict[str, object]:
    """把一份「内容特征判断候选」拆两路落表：特征实例 + 待补充标签观察。

    卡退役前的入口是 `apply_feature_card`（读正式卡）。现在候选本身就是结构化
    的，渲染成卡再解析回来纯属白走一趟，所以直接吃候选。

    输入是**候选路径**，不是卡路径 —— 调用方（单条链路 / 账号批量链路 / 发布后
    复盘链路）都已经持有它。
    """
    root = Path(workspace_root).expanduser().resolve()
    if not root.is_dir():
        raise ContentFeatureWriteError(f"workspace_root_invalid:{root}")
    if source not in ALLOWED_SOURCES:
        raise ContentFeatureWriteError(f"invalid_sample_source:{source}")
    candidate = Path(candidate_path).expanduser()
    if candidate.is_absolute():
        candidate = candidate.resolve()
    else:
        candidate = (root / candidate).resolve()
    try:
        candidate.relative_to(root)
    except ValueError as exc:
        raise ContentFeatureWriteError("feature_candidate_path_outside_workspace") from exc
    if not candidate.is_file():
        raise ContentFeatureWriteError("feature_candidate_path_missing")

    sample = _resolve_content_inputs(
        root,
        source=source,
        content_id=content_id,
        platform=platform,
        account_id=account_id,
        review_path=review_path,
    )
    if sample["source"] == "我方内容":
        # 我方实例必须带平台和账号：汇总按「平台 + 账号 ID + 媒介形式」
        # 分组，缺了这两项同一账号的多篇内容会和其他账号混在一起。
        # 账号批量链路走「账号内容列表」反查；发布后复盘链路查不到时用
        # review_path 的目录名兜底（_resolve_content_inputs 已处理）。
        if platform and sample["platform"] and platform != sample["platform"]:
            raise ContentFeatureWriteError(
                f"own_content_platform_mismatch:{platform}:{sample['platform']}"
            )
        if platform and not sample["platform"]:
            sample["platform"] = platform
    features, observations, resolved_version = _parse_candidate(
        root,
        sample,
        candidate_path=candidate,
        analysis_version=analysis_version,
    )
    result = _write_analysis(
        root,
        sample,
        features,
        observations,
        resolved_version,
        validate_only=validate_only,
        now=now,
    )
    result["feature_candidate_path"] = candidate.relative_to(root).as_posix()
    if run_id:
        result["run_id"] = str(run_id)
    return result


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="从内容特征判断候选写入特征实例与内容特征观察"
    )
    parser.add_argument("--workspace-root", type=Path, required=True, help="当前工作区根目录")
    parser.add_argument(
        "--source", required=True, choices=sorted(ALLOWED_SOURCES), help="样本来源"
    )
    parser.add_argument("--content-id", required=True, help="本条内容的内容 ID")
    parser.add_argument("--candidate-file", type=Path, required=True, help="内容特征判断候选")
    parser.add_argument("--platform", default="", help="可选的平台一致性校验")
    parser.add_argument("--account-id", default="", help="可选的账号一致性校验")
    parser.add_argument("--review-path", default="", help="我方内容的发布后复盘路径（取平台/账号用）")
    parser.add_argument("--analysis-version", default="", help="上游已明确的分析版本")
    parser.add_argument("--run-id", default="", help="本地 MVP 运行标识，仅用于结果关联")
    parser.add_argument("--validate-only", action="store_true", help="只校验，不写正式文件")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        root = args.workspace_root.expanduser().resolve()
        result = apply_feature_candidate(
            root,
            source=args.source,
            content_id=args.content_id,
            candidate_path=args.candidate_file,
            platform=args.platform,
            account_id=args.account_id,
            review_path=args.review_path,
            run_id=args.run_id,
            analysis_version=args.analysis_version,
            validate_only=args.validate_only,
        )
    except (ContentFeatureWriteError, OSError, UnicodeError) as exc:
        print(json.dumps({"status": "failed", "error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
