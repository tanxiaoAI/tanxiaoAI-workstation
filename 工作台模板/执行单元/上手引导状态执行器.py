#!/usr/bin/env python3
"""上手引导 · 状态执行器

给「上手引导流程」用的**跨流程进度账本**。账本固定 6 步
（起工作台 → 账号方向 → 我方账号诊断 → 对标内容分析 → 选题分析 → 内容生产）。

⚠️ 引导**不会走完这 6 步**：它到「判断②：这次的内容想怎么来」就结束，后面的步骤按判断的答案
交给对应流程自己走 —— 收口时把还没落定的步骤**一律记 skip**，让 6 步全部落定（不然 `finished`
永远不为真，用户隔天回来说「继续」会被账本拽回引导中途）。这个执行器只负责**记住走到哪了**，不跑业务。

## 状态文件

`<当前工作区根目录>/.meiling/引导状态.json`

放在工作区里是刻意的：引导从「工作区已经解压出来」那一刻才开始记，
在那之前（拉 install.md、下载解压）属于**统计段**，服务端按匿名事件记，不进这个文件。

## 两个判断不占步骤

- `has_account`（有自己的账号吗）→ 决定 `diagnose` 是 `done` 还是 `skipped`
- `production_mode`（直接生产 / 有参考生产）→ 决定 `benchmark-content` 是 `done` 还是 `skipped`

用 `--action decide` 记，`status` 会把「这一步该不该做」直接算出来给流程看。

## 用法

    python3 执行单元/上手引导状态执行器.py --workspace-root <绝对路径> --action init
    python3 执行单元/上手引导状态执行器.py --workspace-root <绝对路径> --action status
    python3 执行单元/上手引导状态执行器.py --workspace-root <绝对路径> --action begin --step plan
    python3 执行单元/上手引导状态执行器.py --workspace-root <绝对路径> --action done  --step plan --note "已成稿"
    python3 执行单元/上手引导状态执行器.py --workspace-root <绝对路径> --action skip  --step diagnose --reason "用户没有自己的账号"
    python3 执行单元/上手引导状态执行器.py --workspace-root <绝对路径> --action decide --key has_account --value 有

stdout 永远是**一个 JSON 对象**（机器读），人类看的反馈由流程文档决定怎么组织。
出错时写 stderr 并返回非 0；不吞异常。
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from datetime import datetime
from pathlib import Path

SCHEMA = "meiling-guide/1"
STATE_REL = Path(".meiling") / "引导状态.json"
INSTALLATION_REL = Path(".meiling") / "installation.json"
QUESTIONNAIRE_REL = Path("我方资料") / "调查问卷.md"
PLAN_REL = Path("我方资料") / "我方自媒体规划.md"
HISTORY_LIMIT = 200

# 引导的 6 步。顺序即推进顺序；`skill` 是这一步要调的公开 Skill。
# ⚠️ 没有「准备环境」这一步：Python / Node / 技能目录适配 / Chrome / MCP 配置全部在装包阶段
#    （`install.md` 第 0~4 步）做完，用户重启客户端之后才进本流程。账本也是那时候建的，
#    这里第一步 `dashboard` 只是把工作台起起来。
STEPS: tuple[tuple[str, str, str], ...] = (
    ("dashboard", "起工作台", "dashboard"),
    ("plan", "账号方向", "media-strategy"),
    ("diagnose", "我方账号诊断", "self-account-diagnose"),
    ("benchmark-content", "对标内容分析", "benchmark-content-diagnose"),
    ("topic", "选题分析", "topic-evaluate"),
    ("create", "内容生产", "create-content"),
)
STEP_IDS = tuple(s[0] for s in STEPS)
STEP_NAMES = {s[0]: s[1] for s in STEPS}
STEP_SKILLS = {s[0]: s[2] for s in STEPS}

# 判断的合法取值。None（还没问）= 该步「待定」，流程不该往下跳。
DECISIONS: dict[str, tuple[str, ...]] = {
    "has_account": ("有", "没有"),
    "production_mode": ("有参考", "无参考"),
}

STATUSES = ("pending", "in_progress", "done", "skipped")

# 某一步该不该做：判断没落定 → unknown（流程必须先问用户）
GATES: dict[str, tuple[str, tuple[object, ...]]] = {
    "diagnose": ("has_account", ("有",)),
    "benchmark-content": ("production_mode", ("有参考",)),
}


class GuideStateError(RuntimeError):
    """状态文件不可用、参数非法或推进不合法。"""


def _now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _write_json_atomic(path: Path, payload: dict) -> None:
    """原子替换写，避免中途失败留下半个 JSON。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=".引导状态.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def _workspace_version(root: Path) -> str:
    inst = root / INSTALLATION_REL
    if not inst.is_file():
        return ""
    try:
        data = json.loads(inst.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return ""
    value = data.get("version")
    return value if isinstance(value, str) else ""


def _blank_state(root: Path) -> dict:
    now = _now()
    return {
        "schema": SCHEMA,
        "created_at": now,
        "updated_at": now,
        "workspace_version": _workspace_version(root),
        "decisions": {key: None for key in DECISIONS},
        "steps": [
            {
                "id": sid,
                "name": STEP_NAMES[sid],
                "skill": STEP_SKILLS[sid],
                "status": "pending",
                "started_at": None,
                "finished_at": None,
                "note": "",
            }
            for sid in STEP_IDS
        ],
        "history": [],
    }


def _load(root: Path) -> dict:
    path = root / STATE_REL
    if not path.is_file():
        raise GuideStateError(
            f"状态文件还不存在：{STATE_REL.as_posix()}（先跑 --action init）"
        )
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise GuideStateError(f"状态文件读不出来或不是合法 JSON：{exc}") from exc
    if not isinstance(data, dict) or data.get("schema") != SCHEMA:
        raise GuideStateError(f"状态文件不是本执行器的格式（schema 应为 {SCHEMA}）")
    return _normalize(data)


def _normalize(data: dict) -> dict:
    """把状态文件补全成完整形状：缺的步骤补进来、非法状态回落到 pending。"""
    by_id = {}
    raw_steps = data.get("steps")
    if isinstance(raw_steps, list):
        for item in raw_steps:
            if isinstance(item, dict) and item.get("id") in STEP_IDS:
                by_id[item["id"]] = item
    steps = []
    for sid in STEP_IDS:
        item = by_id.get(sid) or {}
        status = item.get("status")
        steps.append(
            {
                "id": sid,
                "name": STEP_NAMES[sid],
                "skill": STEP_SKILLS[sid],
                "status": status if status in STATUSES else "pending",
                "started_at": item.get("started_at") or None,
                "finished_at": item.get("finished_at") or None,
                "note": item.get("note") if isinstance(item.get("note"), str) else "",
            }
        )
    data["steps"] = steps
    decisions = data.get("decisions")
    if not isinstance(decisions, dict):
        decisions = {}
    data["decisions"] = {
        key: (decisions.get(key) if decisions.get(key) in allowed else None)
        for key, allowed in DECISIONS.items()
    }
    if not isinstance(data.get("history"), list):
        data["history"] = []
    return data


def _row(state: dict, step_id: str) -> dict:
    for item in state["steps"]:
        if item["id"] == step_id:
            return item
    raise GuideStateError(f"未知步骤：{step_id}")


def _gate_of(state: dict, step_id: str) -> str:
    """这一步该不该做：todo / skip / unknown。"""
    gate = GATES.get(step_id)
    if gate is None:
        return "todo"
    key, allowed = gate
    value = state["decisions"].get(key)
    if value is None:
        return "unknown"
    return "todo" if value in allowed else "skip"


def _current(state: dict) -> str:
    """第一个还没落定的步骤；全部落定返回空串。"""
    for item in state["steps"]:
        if item["status"] in ("pending", "in_progress"):
            return item["id"]
    return ""


def _artifact_ready(root: Path, relative: Path) -> bool:
    """只用本机文件状态区分问卷已提交和方向已保存，不读取业务正文写入账本。"""
    path = root / relative
    try:
        return path.is_file() and bool(path.read_text(encoding="utf-8").strip())
    except (OSError, UnicodeError):
        return False


def _advance(state: dict, step_id: str, status: str, note: str) -> None:
    row = _row(state, step_id)
    now = _now()
    row["status"] = "done" if status == "done" else "skipped"
    row["finished_at"] = now
    if note:
        row["note"] = note
    state["updated_at"] = now
    entry = {"at": now, "action": status, "step": step_id}
    if note:
        entry["note"] = note
    state["history"].append(entry)
    del state["history"][:-HISTORY_LIMIT]


def _summary(state: dict, root: Path | None = None) -> dict:
    done = [i["id"] for i in state["steps"] if i["status"] == "done"]
    skipped = [i["id"] for i in state["steps"] if i["status"] == "skipped"]
    current = _current(state)
    finished = not current
    artifacts = {
        "questionnaire_submitted": _artifact_ready(root, QUESTIONNAIRE_REL) if root else False,
        "direction_saved": _artifact_ready(root, PLAN_REL) if root else False,
    }
    return {
        "current": current,
        "current_name": STEP_NAMES.get(current, ""),
        "current_skill": STEP_SKILLS.get(current, ""),
        "gate": _gate_of(state, current) if current else "",
        "finished": finished,
        "done_steps": done,
        "skipped_steps": skipped,
        "next_if_done": _next_after(state, current) if current else "",
        **artifacts,
    }


def _next_after(state: dict, step_id: str) -> str:
    if not step_id:
        return ""
    index = STEP_IDS.index(step_id)
    for sid in STEP_IDS[index + 1 :]:
        if _gate_of(state, sid) != "skip":
            return sid
    return ""


def _emit(payload: dict) -> int:
    sys.stdout.write(json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
    return 0


def _parse(args: argparse.Namespace) -> int:
    root = Path(args.workspace_root).expanduser().resolve()
    state_file = root / STATE_REL

    if args.action == "init":
        if state_file.is_file():
            state = _load(root)
            return _emit({"ok": True, "created": False, **_summary(state, root), "steps": state["steps"], "decisions": state["decisions"]})
        state = _blank_state(root)
        _write_json_atomic(state_file, state)
        return _emit({"ok": True, "created": True, **_summary(state, root), "steps": state["steps"], "decisions": state["decisions"]})

    if args.action == "reset":
        if not args.confirm:
            raise GuideStateError("reset 会丢掉整条引导进度，必须显式加 --confirm")
        state = _blank_state(root)
        _write_json_atomic(state_file, state)
        return _emit({"ok": True, "created": True, **_summary(state, root), "steps": state["steps"], "decisions": state["decisions"]})

    state = _load(root)

    if args.action == "status":
        return _emit(
            {
                "ok": True,
                **_summary(state, root),
                "created_at": state.get("created_at", ""),
                "updated_at": state.get("updated_at", ""),
                "workspace_version": state.get("workspace_version", ""),
                "steps": state["steps"],
                "decisions": state["decisions"],
            }
        )

    if args.action == "decide":
        key = args.key
        if key not in DECISIONS:
            raise GuideStateError(f"未知判断项：{key}（可选：{'、'.join(DECISIONS)}）")
        if args.value not in DECISIONS[key]:
            raise GuideStateError(
                f"{key} 的取值只能是 {' / '.join(DECISIONS[key])}，收到：{args.value}"
            )
        state["decisions"][key] = args.value
        state["updated_at"] = _now()
        state["history"].append({"at": state["updated_at"], "action": "decide", "key": key, "value": args.value})
        del state["history"][:-HISTORY_LIMIT]
        _write_json_atomic(state_file, state)
        return _emit({"ok": True, **_summary(state, root), "steps": state["steps"], "decisions": state["decisions"]})

    step_id = args.step
    if step_id not in STEP_IDS:
        raise GuideStateError(f"未知步骤：{step_id}（可选：{'、'.join(STEP_IDS)}）")
    row = _row(state, step_id)

    if args.action == "begin":
        if row["status"] in ("done", "skipped"):
            raise GuideStateError(
                f"「{row['name']}」已经是 {row['status']}，要重跑先 --action reset"
            )
        row["status"] = "in_progress"
        row["started_at"] = _now()
        state["updated_at"] = row["started_at"]
        state["history"].append({"at": row["started_at"], "action": "begin", "step": step_id})
        del state["history"][:-HISTORY_LIMIT]
        payload = {"ok": True, **_summary(state, root), "steps": state["steps"], "decisions": state["decisions"]}
        _write_json_atomic(state_file, state)
        return _emit(payload)

    if args.action in ("done", "skip"):
        if args.action == "skip":
            gate = _gate_of(state, step_id)
            if gate == "todo" and step_id in GATES:
                # 判断说「要做」，却被 skip：只提示，不阻断 —— 用户可能中途改主意。
                pass
            if not (args.reason or "").strip():
                raise GuideStateError("skip 必须写 --reason，说明为什么跳过")
            _advance(state, step_id, "skipped", args.reason.strip())
        else:
            _advance(state, step_id, "done", (args.note or "").strip())
        _write_json_atomic(state_file, state)
        return _emit({"ok": True, **_summary(state, root), "steps": state["steps"], "decisions": state["decisions"]})

    raise GuideStateError(f"未知动作：{args.action}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="上手引导进度账本（只记状态，不跑业务）")
    parser.add_argument("--workspace-root", required=True, help="当前工作区根目录绝对路径")
    parser.add_argument(
        "--action",
        required=True,
        choices=("init", "status", "begin", "done", "skip", "decide", "reset"),
    )
    parser.add_argument("--step", default="", help=f"步骤 id，可选：{'、'.join(STEP_IDS)}")
    parser.add_argument("--key", default="", help=f"判断项，可选：{'、'.join(DECISIONS)}")
    parser.add_argument("--value", default="", help="判断取值")
    parser.add_argument("--note", default="", help="完成备注（可选）")
    parser.add_argument("--reason", default="", help="跳过原因（skip 必填）")
    parser.add_argument("--confirm", action="store_true", help="reset 必须带这个标志")
    args = parser.parse_args(argv)

    try:
        return _parse(args)
    except GuideStateError as exc:
        print(f"引导状态错误：{exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
