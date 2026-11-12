#!/usr/bin/env python3
"""契约一致性检查：比对流程文档的声明 与 本地执行器的真实实现。

定位：开发/交付自检工具，不属于业务执行器，不被任何流程文档调用。

检查口径（按项目约定）：
  流程文档只负责「调用哪个执行器、传什么参数、拿到回执怎么继续」；
  执行单元的内部实现（内部错误码、内部字段、内部校验逻辑）不属于流程文档的职责。
因此：
  - 「文档说了、代码里没有」      → 报为问题（会误导 AI 或让调用失败）
  - 「代码有、文档没提」          → 只统计，不算问题
  - 命令里出现文档没写的参数       → 报为提示（执行器支持即可）

用法：
  python3 执行单元/契约一致性检查.py --workspace-root <工作区绝对路径>
  python3 执行单元/契约一致性检查.py --workspace-root <ws> --json <输出.json>
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

# ---------------------------------------------------------------- 提取正则

# 执行单元/xxx.py 的引用
EXEC_REF = re.compile(r"执行单元/([^\s\"'`（）)，,、。；;：:]+\.py)")

# 命令行参数
OPT = re.compile(r"(?<![\w-])(--[A-Za-z][A-Za-z0-9_-]*)")

# 反引号里的 token
CODE_SPAN = re.compile(r"`([^`\n]{2,90})`")

# 本地文件引用
FILE_REF = re.compile(
    r"(?:分析框架|提示词|执行单元|执行流程|skills|工作台)/"
    r"[^\s\"'`（）)，,、。；;：:]+\.(?:md|py|json|csv|mjs|js|html)"
)

# 源码里的错误码：raise Xxx(f"码") / "error": "码" / {"code": "码"} / "reason": f"码"
RAISE_ERR = re.compile(r"raise\s+\w*(?:Error|Exception)\s*\(\s*f?[\"']([^\"'\n]{2,90})[\"']")
DICT_ERR = re.compile(r"[\"'](?:error|error_code|code|reason|failure_reason)[\"']\s*:\s*f?[\"']([^\"'\n]{2,90})[\"']")
STATUS_LIT = re.compile(r"[\"']status[\"']\s*:\s*f?[\"']([^\"'\n]{1,40})[\"']")
STATUS_EQ = re.compile(r"status\s*==\s*[\"']([^\"'\n]{1,40})[\"']")

# 不是错误码的常见词（避免噪音）
STOPWORDS = {
    # 工具/命令/动作
    "prepare", "save", "start", "transition", "fail", "resume", "commit", "build",
    "verify", "update-positioning", "reopen-final", "decide", "inspect",
    # 泛指的状态名与字段名（文档正常会用到，且未必以字符串形式出现在代码里）
    "ok", "true", "false", "status", "message", "error", "reason", "code",
    "run_id", "content_id", "topic_id", "state_file", "workspace_root",
    "context_file", "action_contract", "sha256", "json", "csv", "md", "py",
    "stdout", "stderr", "stdin", "utf-8",
}


def _read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except Exception:
        return ""


HELP_OPT = re.compile(r"^\s+(--[A-Za-z][A-Za-z0-9_-]*)", re.M)
# 有些参数被 argparse.SUPPRESS 隐藏，--help 看不到，必须回源码取字面量
OPT_LITERAL = re.compile(r"[\"'](--[A-Za-z][A-Za-z0-9_-]*)[\"']")


def _cli_opts(py: Path) -> tuple[list[str], bool]:
    """取参数真值 = 实读 --help ∪ 源码里的 --字面量。

    只用 --help 会漏掉 SUPPRESS 隐藏的参数；只用源码正则又会漏掉运行时动态拼的。
    两路取并集最稳。没有 argparse 的库模块两者都为空，标记为非 CLI。
    """
    literals = set(OPT_LITERAL.findall(_read(py)))
    from_help: set[str] = set()
    try:
        proc = subprocess.run(
            [sys.executable, str(py), "--help"],
            capture_output=True, text=True, timeout=20,
        )
        from_help = set(HELP_OPT.findall((proc.stdout or "") + (proc.stderr or "")))
    except Exception:
        pass
    merged = sorted(literals | from_help)
    return merged, bool(merged)


# 执行器之间的调用关系（用于算透传）
DEP_PATTERNS = (
    re.compile(r"_load_sibling\(\s*[\"']([^\"']+\.py)[\"']"),
    re.compile(r"^from\s+([^\s.]+)\s+import", re.M),
    re.compile(r"^import\s+([^\s.#,]+)", re.M),
)


def _deps(text: str, unit_names: set[str]) -> set[str]:
    found: set[str] = set()
    for pattern in DEP_PATTERNS:
        for hit in pattern.findall(text):
            name = hit if hit.endswith(".py") else hit + ".py"
            if name in unit_names:
                found.add(name)
    return found


def collect_executors(root: Path) -> dict[str, dict]:
    """扫描 执行单元/*.py，收集每个执行器的：参数、错误码、状态取值、依赖。"""
    executors: dict[str, dict] = {}
    unit_dir = root / "执行单元"
    if not unit_dir.is_dir():
        return executors
    py_files = sorted(unit_dir.glob("*.py"))
    unit_names = {p.name for p in py_files}
    for py in py_files:
        text = _read(py)
        opts, is_cli = _cli_opts(py)
        codes = set()
        for regex in (RAISE_ERR, DICT_ERR):
            for hit in regex.findall(text):
                codes.add(hit.split("{")[0].split(":")[0].strip())
        statuses = set(STATUS_LIT.findall(text)) | set(STATUS_EQ.findall(text))
        executors[py.name] = {
            "path": str(py.relative_to(root)),
            "opts": sorted(set(opts)),
            "is_cli": is_cli,
            "codes": sorted(c for c in codes if c),
            "statuses": sorted(s for s in statuses if s),
            "deps": sorted(_deps(text, unit_names) - {py.name}),
        }
    return executors


def collect_doc_commands(root: Path, executors: dict[str, dict]) -> list[dict]:
    """扫描流程文档与技能文档，提取「执行器命令 + 参数」，核对参数是否真实存在。"""
    findings: list[dict] = []
    docs = list((root / "执行流程").glob("*.md"))
    docs += list((root / "skills").glob("*/SKILL.md"))
    docs += list((root / "执行单元").glob("*.md"))
    for doc in docs:
        text = _read(doc)
        lines = text.splitlines()
        for idx, line in enumerate(lines):
            for match in EXEC_REF.finditer(line):
                script = match.group(1)
                # 收集续行
                block = [line]
                j = idx
                while j < len(lines) - 1 and lines[j].rstrip().endswith("\\"):
                    j += 1
                    block.append(lines[j])
                joined = "\n".join(block)
                opts = [o for o in OPT.findall(joined) if o not in ("--workspace-root",)]
                info = executors.get(script)
                unknown: list[str] = []
                if info and info.get("is_cli"):
                    real = set(info["opts"]) | {"--workspace-root"}
                    unknown = sorted({o for o in opts if o not in real})
                findings.append({
                    "doc": str(doc.relative_to(root)),
                    "line": idx + 1,
                    "script": script,
                    "script_known": bool(info),
                    "opts": sorted(set(opts)),
                    "unknown_opts": unknown,
                })
    return findings


# 判断一个 token 是否「长得像错误码」，而不是字段名/参数名/流程概念
_ERROR_SUFFIX = (
    "_invalid", "_failed", "_error", "_denied", "_mismatch", "_missing",
    "_required", "_not_found", "_unavailable", "_conflict", "_changed",
    "_rejected", "_expired", "_collision", "_mismatch",
)
_ERROR_PREFIX = ("needs_", "stale_", "concurrent_", "unexpected_", "invalid_", "already_")
_FIELD_SUFFIX = (
    "_path", "_file", "_dir", "_url", "_json", "_id", "_key", "_name",
    "_sha256", "_hash", "_index", "_count", "_mode", "_type", "_form",
    "_text", "_time", "_link", "_list", "_note", "_set", "_map", "_at",
)


def _looks_like_error_code(token: str) -> bool:
    if len(token) < 7 or not re.fullmatch(r"[a-z][a-z0-9_]*", token):
        return False
    if token.endswith(_FIELD_SUFFIX):
        return False
    if token.endswith(_ERROR_SUFFIX) or token.startswith(_ERROR_PREFIX):
        return True
    # 三个及以上单词拼的 snake_case 复合码（如 post_commit_verification_failed 的简化形态）
    return token.count("_") >= 2


# 外部 MCP 工具名，不是本项目的错误码
MCP_TOOLS = {
    "get_network_request", "list_network_requests", "read_mcp_resource",
    "list_mcp_resources", "evaluate_script", "take_snapshot", "take_screenshot",
    "navigate_page", "click_element", "fill_form", "wait_for", "new_page",
    "list_pages", "select_page", "upload_file", "handle_dialog",
}
# 流程自身定义的枚举（由 AI 判断、不落代码），不是错误码
FLOW_ONLY_ENUMS = {
    "resolved_by_ai", "needs_maintainer", "skill_recommended", "external_issue",
    "self_diagnosis_inspiration", "benchmark_inspiration", "needs_topic_input",
}


def _looks_like_dynamic_concat(token: str, code_text: str) -> bool:
    """文档里的码可能是代码用 f-string 拼出来的（如 f"{label}_heading_contract_invalid"）。

    做法：把 token 去掉开头 1~2 个词，看剩下的后缀是否在代码里出现。
    """
    parts = token.split("_")
    for cut in (1, 2):
        if len(parts) - cut >= 2:
            suffix = "_".join(parts[cut:])
            if len(suffix) >= 12 and suffix in code_text:
                return True
    return False


def collect_doc_tokens(root: Path, executors: dict[str, dict]) -> tuple[list[dict], list[dict]]:
    """文档反引号里形如错误码的名字，若全仓代码都找不到，报为可疑。

    返回 (确认的漂移候选, 需人工判断的)。
    只挑「长得像错误码」的（有 _invalid/_failed/needs_ 等特征），
    字段名、参数名、入口概念不在此列——它们本就未必出现在代码里。
    """
    all_code_text = ""
    for info in executors.values():
        all_code_text += _read(root / info["path"]) + "\n"
    for pattern in ("工作台/*.mjs", "工作台/*.js"):
        for js in root.glob(pattern):
            all_code_text += _read(js) + "\n"

    docs = list((root / "执行流程").glob("*.md"))
    docs += list((root / "skills").glob("*/SKILL.md"))
    docs += list((root / "执行单元").glob("*.md"))
    docs += list((root / "分析框架").glob("*.md"))
    docs += list((root / "提示词").glob("*.md"))

    confirmed: dict[str, list[str]] = {}
    review: dict[str, tuple[str, list[str]]] = {}
    for doc in docs:
        rel = str(doc.relative_to(root))
        for span in CODE_SPAN.findall(_read(doc)):
            span = span.strip()
            if "/" in span or re.search(r"\.(py|md|json|csv|js|mjs|html)\b", span):
                continue
            token = span.split(":")[0].split("(")[0].strip()
            if token in STOPWORDS or not _looks_like_error_code(token):
                continue
            if token in all_code_text:
                continue
            if token in MCP_TOOLS:
                continue
            if token in FLOW_ONLY_ENUMS:
                review.setdefault(token, ("流程自定义枚举（AI 判断用，不落代码）", []))[1].append(rel)
                continue
            if _looks_like_dynamic_concat(token, all_code_text):
                review.setdefault(token, ("代码用 f-string 拼接，字面量搜不到", []))[1].append(rel)
                continue
            confirmed.setdefault(token, []).append(rel)

    confirmed_list = [{"token": t, "docs": sorted(set(d))} for t, d in sorted(confirmed.items())]
    review_list = [
        {"token": t, "reason": reason, "docs": sorted(set(docs))}
        for t, (reason, docs) in sorted(review.items())
    ]
    return confirmed_list, review_list


# 到处都会返回的通用状态值，不参与归属核对（否则满屏误报）
COMMON_STATUS = {
    "completed", "blocked", "failed", "ready", "success", "unavailable",
    "committed", "pending", "partial", "written", "prepared", "checked",
    "collected", "planned", "verified", "matched", "initialized", "confirmed",
    "reused", "skipped", "unchanged", "successfully",
}

# 判定「错误码归属」时向前回溯的行数。文档常在同一个段落里先写上游判据、再写真正
# 产出该码的正式化执行器；只认「最近一个执行器」会把正确归属误判成安错。
OWNERSHIP_WINDOW_LINES = 12


def _dependency_closure(executors: dict[str, dict]) -> dict[str, set[str]]:
    """算执行器的依赖闭包：A 调用了 B，那 B 抛的码 A 也会透传出来。

    这一步很关键。只看「谁 raise」会误判，例如
    账号概要分析正式化执行器 import 了 verify_account_evidence_sources，
    它在正式写入路径上就会把 source_hash_mismatch 透传出来。
    """
    direct = {name: set(info.get("deps", ())) for name, info in executors.items()}
    closure: dict[str, set[str]] = {}
    for name in executors:
        seen: set[str] = set()
        stack = list(direct.get(name, ()))
        while stack:
            cur = stack.pop()
            if cur in seen or cur not in executors:
                continue
            seen.add(cur)
            stack.extend(direct.get(cur, ()))
        closure[name] = seen | {name}
    return closure


def check_code_ownership(root: Path, executors: dict[str, dict]) -> list[dict]:
    """核对文档里的错误码，是否可能由它上文最近提到的那个执行器透传出来。

    这是本次问题的真正形态：码名字真实存在，但被安在了错误的动作上。
    近似判据：文档通常先写用哪个执行器、再列它可能返回的码；
    归属按「自身 + 依赖闭包」算，避免把透传出来的码误判成安错。
    """
    owner: dict[str, set[str]] = {}
    for name, info in executors.items():
        for code in info["codes"]:
            owner.setdefault(code, set()).add(name)
    if not owner:
        return []
    closure = _dependency_closure(executors)
    findings: list[dict] = []
    docs = list((root / "执行流程").glob("*.md"))
    docs += list((root / "skills").glob("*/SKILL.md"))
    docs += list((root / "执行单元").glob("*.md"))
    for doc in docs:
        rel = str(doc.relative_to(root))
        recent: list[tuple[int, str]] = []
        for idx, line in enumerate(_read(doc).splitlines(), start=1):
            # 同一行可能同时提到多个执行器（例如先写单点判据、再写真正产出该码的正式化
            # 执行器），必须把这一行的全部引用都记下来；只取第一个会漏掉后半句的归属。
            for name in EXEC_REF.findall(line):
                recent.append((idx, name))
            # 归属按「最近 OWNERSHIP_WINDOW_LINES 行内提到过的执行器」算，而不是只看最近的那一个：
            # 文档常在同一个段落里先写上游判据、再写真正产出该码的正式化执行器，只取最近一个
            # 会把正确归属误判成「安错了执行器」（2026-09-14 实测 3 条残留告警全是这个原因）。
            window = [name for ln, name in recent if idx - ln <= OWNERSHIP_WINDOW_LINES]
            if not window:
                continue
            for span in CODE_SPAN.findall(line):
                token = span.split(":")[0].split("(")[0].strip()
                if token in COMMON_STATUS or token not in owner:
                    continue
                if any(closure.get(name, {name}) & owner[token] for name in window):
                    continue
                findings.append({
                    "doc": rel,
                    "line": idx,
                    "code": token,
                    "context_executors": sorted(set(window)),
                    "actual_owners": sorted(owner[token]),
                })
    return findings


def collect_code_only(root: Path, executors: dict[str, dict]) -> dict:
    """代码里有、文档没提的错误码/状态：只统计数量，不算问题。"""
    docs_text = ""
    for pat in ("执行流程/*.md", "skills/*/SKILL.md", "执行单元/*.md"):
        for doc in root.glob(pat):
            docs_text += _read(doc) + "\n"
    missing_codes = {}
    for name, info in executors.items():
        absent = [c for c in info["codes"] if c and c not in docs_text and len(c) > 4]
        if absent:
            missing_codes[name] = len(absent)
    return missing_codes


def main() -> int:
    parser = argparse.ArgumentParser(description="契约一致性检查")
    parser.add_argument("--workspace-root", required=True)
    parser.add_argument("--json", default="")
    args = parser.parse_args()

    root = Path(args.workspace_root).expanduser().resolve()
    if not root.is_dir():
        print(f"工作区不存在: {root}", file=sys.stderr)
        return 2

    executors = collect_executors(root)
    commands = collect_doc_commands(root, executors)
    tokens, review_tokens = collect_doc_tokens(root, executors)
    ownership = check_code_ownership(root, executors)
    code_only = collect_code_only(root, executors)

    # 文件引用核对
    missing_files: dict[str, set] = {}
    for pat in ("执行流程/*.md", "skills/*/SKILL.md"):
        for doc in root.glob(pat):
            for hit in FILE_REF.findall(_read(doc)):
                if not (root / hit).exists():
                    missing_files.setdefault(hit, set()).add(doc.name)

    print("=" * 72)
    print("契约一致性检查")
    print("=" * 72)
    print(f"执行器：{len(executors)} 个　文档命令：{len(commands)} 条\n")

    print("-" * 72)
    print("① 文档命令里出现、但执行器没有的参数（会直接导致调用失败）")
    print("-" * 72)
    bad = [c for c in commands if c["unknown_opts"]]
    unknown_script = [c for c in commands if not c["script_known"]]
    if bad:
        for c in bad:
            print(f"  {c['doc']}:{c['line']}  → {c['script']}")
            print(f"      文档写了但执行器没有：{', '.join(c['unknown_opts'])}")
    else:
        print("  ✅ 无")
    if unknown_script:
        print("\n  文档引用了不存在的执行器脚本：")
        for c in unknown_script:
            print(f"    {c['doc']}:{c['line']} → {c['script']}")

    print()
    print("-" * 72)
    print("② 文档引用的本地文件是否存在")
    print("-" * 72)
    if missing_files:
        for ref, srcs in sorted(missing_files.items()):
            print(f"  ❌ {ref}   ← {', '.join(sorted(srcs))}")
    else:
        print("  ✅ 全部存在")

    print()
    print("-" * 72)
    print("③ 文档提到、但全仓代码找不到的名字（疑似文档与代码漂移）")
    print("-" * 72)
    if tokens:
        for item in tokens:
            print(f"  ❌ {item['token']}")
            for src in item["docs"]:
                print(f"      ← {src}")
    else:
        print("  ✅ 无需要处理的")
    if review_tokens:
        print("\n  以下需人工判断（多半正常）：")
        for item in review_tokens:
            print(f"      {item['token']}  [{item['reason']}] ← {', '.join(item['docs'])}")

    print()
    print("-" * 72)
    print("④ 错误码归属可疑：文档把它安在了不是它产出的执行器上")
    print("-" * 72)
    if ownership:
        shown = 0
        for f in ownership:
            shown += 1
            if shown > 25:
                print(f"  … 另有 {len(ownership) - 25} 条，见 JSON")
                break
            print(f"  ⚠ {f['doc']}:{f['line']}  文档上文执行器={', '.join(f['context_executors'])}")
            print(f"      码 {f['code']}  实际由：{', '.join(f['actual_owners'])}")
    else:
        print("  ✅ 无")

    print()
    print("-" * 72)
    print("⑤ 代码里有、文档没提的错误码数量（按项目约定：不算问题，仅供参考）")
    print("-" * 72)
    if code_only:
        total = sum(code_only.values())
        print(f"  共 {len(code_only)} 个执行器、{total} 个错误码未在文档中出现")
        for name, count in sorted(code_only.items(), key=lambda kv: -kv[1])[:10]:
            print(f"      {name}: {count}")
    else:
        print("  无")

    if args.json:
        payload = {
            "executors": {k: {"opts": v["opts"], "is_cli": v["is_cli"],
                              "codes": v["codes"], "statuses": v["statuses"]}
                          for k, v in executors.items()},
            "commands_with_unknown_opts": bad,
            "unknown_scripts": unknown_script,
            "missing_files": {k: sorted(v) for k, v in missing_files.items()},
            "suspicious_tokens": tokens,
            "tokens_need_review": review_tokens,
            "code_ownership_suspects": ownership,
            "code_only_counts": code_only,
        }
        Path(args.json).write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\n已写出 JSON：{args.json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
