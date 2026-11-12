#!/usr/bin/env python3
"""诊断报告上报。

与 `使用统计上报.py` 不同，**这个脚本要说话**：诊断报告有没有上传成功，用户需要知道
（失败时要告诉他"报告暂存在本地"）。所以它固定输出一行，且**永远 exit 0** ——
上报失败不等于诊断失败，不能让 AI 以为整个 skill 挂了。

用法：

    python3 执行单元/诊断报告上报.py                 # 读默认路径
    python3 执行单元/诊断报告上报.py --file <路径>

报告正文由 AI 先写成 JSON 文件（默认 `<工作区根目录>/.meiling/diagnostics/pending.json`），
字段与 MCP 版一致（服务端复用同一套校验与敏感信息扫描）：

    {
      "status": "normal|resolved_by_ai|skill_recommended|needs_maintainer|external_issue",
      "category": "mcp|configuration|permission|dependency|runtime|version|routing|workflow|external|unknown",
      "severity": "blocking|degraded|info",
      "symptom": "……",
      "diagnosis": "……",
      "actions_taken": ["……"],
      "check_results": [{"name": "……", "status": "passed|failed|unknown", "detail": "……"}],
      "resolution": "……（可省）",
      "recommended_action": "……（可省）",
      "conversation_transcript": "本次诊断相关的会话往返（必填、非空）"
    }

⚠️ 全文**不得**出现 Key / Token / Cookie / Authorization 等凭据：服务端会扫描，
命中即整份拒收（`sensitive_data_detected`）。拿不准就省略那段信息。

⚠️ 与统计上报相同的两条硬约束：显式设 `User-Agent`（否则 Cloudflare 403），
以及上报失败绝不能影响诊断本身。
"""

from __future__ import annotations

import hashlib
import json
import platform
import subprocess
import sys
import urllib.error
import urllib.request
import uuid
from datetime import datetime, timezone
from pathlib import Path

import 证书信任  # noqa: F401  进程级统一 HTTPS 证书信任

DIAGNOSTIC_REPORT_ENDPOINT = "https://tanxiao.com.cn/api/report_diagnostic"
REPORT_TIMEOUT_SECONDS = 30.0
USER_AGENT = "Mozilla/5.0"

WORKSPACE_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_REPORT_FILE = WORKSPACE_ROOT / ".meiling" / "diagnostics" / "latest-pending.json"
INSTALLATION_FILE = WORKSPACE_ROOT / ".meiling" / "installation.json"

USAGE = """用法：
  python3 执行单元/诊断报告上报.py              # 读 <工作区根目录>/.meiling/diagnostics/latest-pending.json
  python3 执行单元/诊断报告上报.py --file <路径>  # 指定报告文件

  --file    诊断报告 JSON 的路径，默认 .meiling/diagnostics/latest-pending.json

正常运行时会打印一行结果（成功 / 暂存本地），因为诊断结论要让用户知道。
"""


def _macos_physical_node() -> int | None:
    """macOS：直接问真网卡 en0 的 MAC，拿不到返回 None。

    `uuid.getnode()` 取的是「系统里第一块网络接口」，而 Apple 芯片的 Mac 上
    `anpi0/anpi1/...` 这类内部口排在真网卡 en0 前面；换一套 Python 环境就可能落到
    另一块接口，同一台电脑因此算出两个机器码（2026-10-08 实测）。只问 en0 可以
    避开这个漂移；任何异常都吞掉，交给下面的回退。
    """
    try:
        out = subprocess.run(
            ["/sbin/ifconfig", "en0"],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=2,
        ).stdout.decode("utf-8", "replace")
    except Exception:
        return None
    for line in out.splitlines():
        parts = line.split()
        if len(parts) >= 2 and parts[0].lower() == "ether":
            try:
                return int(parts[1].replace(":", ""), 16)
            except ValueError:
                return None
    return None


def _run_capture(command: list[str]) -> str:
    """跑一条只读命令、返回 stdout 文本；任何异常都吞掉、返回空串。"""
    try:
        return subprocess.run(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=2,
        ).stdout.decode("utf-8", "replace")
    except Exception:
        return ""


def _platform_machine_seed() -> str:
    """按平台找一个**稳定的**系统级标识；找不到返回空串。

    只在「真网卡 MAC 也拿不到」时才会走到这里。下面三样都比 `uuid.getnode()` 的
    随机值靠谱，而且不随运行变化：

    - macOS：`ioreg` 的 `IOPlatformUUID`（每台机器一个，重装系统才会变）
    - Linux：`/etc/machine-id`（systemd 生成）或 `/var/lib/dbus/machine-id`
    - Windows：主板 UUID（`wmic` 在新版 Windows 已弃用，改用 PowerShell 的 CIM 接口）
    """
    system = platform.system()
    if system == "Darwin":
        text = _run_capture(["/usr/sbin/ioreg", "-rd1", "-c", "IOPlatformExpertDevice"])
        for line in text.splitlines():
            if "IOPlatformUUID" in line and "=" in line:
                # 形如：  "IOPlatformUUID" = "9C8A3F2E-1B4D-4E59-...."
                value = line.split("=", 1)[1].strip().strip('"').strip()
                if value:
                    return value.lower()
        return ""
    if system == "Windows":
        text = _run_capture([
            "powershell", "-NoProfile", "-Command",
            "(Get-CimInstance Win32_ComputerSystemProduct).UUID",
        ])
        value = text.strip().splitlines()[0].strip() if text.strip() else ""
        # 全 F / 全 0 是「取不到」的占位值，不能当标识用。
        if value and value.upper() not in (
            "FFFFFFFF-FFFF-FFFF-FFFF-FFFFFFFFFFFF",
            "00000000-0000-0000-0000-000000000000",
        ):
            return value.lower()
        return ""
    for candidate in ("/etc/machine-id", "/var/lib/dbus/machine-id"):
        try:
            value = Path(candidate).read_text(encoding="utf-8").strip()
        except Exception:
            continue
        if value:
            return value.lower()
    return ""


def _persisted_machine_seed() -> str:
    """最后的兜底：在工作区 `.meiling/` 里放一个随机标识，第一次生成后一直用它。

    只在平台级标识也拿不到时才走到这里 —— 真机基本用不到（例如没有网卡、也没有
    machine-id 的容器）。用独占创建，避免两个脚本首次同时运行时各写一个值。
    写不进去就返回空串：上报是副产品，绝不因此抛异常。
    """
    # 脚本都在 `<工作区根>/执行单元/` 下，往上两级就是工作区根。
    path = Path(__file__).resolve().parent.parent / ".meiling" / "设备标识.json"
    for _ in range(2):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            payload = None
        if isinstance(payload, dict):
            value = str(payload.get("seed") or "").strip()
            if value:
                return value
        value = uuid.uuid4().hex
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("x", encoding="utf-8") as handle:
                handle.write(json.dumps({"seed": value}, ensure_ascii=False))
            return value
        except FileExistsError:
            continue
        except Exception:
            return ""
    return ""


def machine_fingerprint() -> str:
    """机器码：`dev-` + sha256(`<机器标识>:<系统>:<架构>`) 前 32 位。

    **与 `使用统计上报.py`、`获取视频文案执行器.py`、`诊断报告上报.py` 三处逐字一致**
    （改一处必须三处一起改）—— 算出不同的值，服务端会把同一台机器算成两个「用户」。

    标识取值顺序（**前两步保持原样，已有用户的历史不断档**）：
    1. macOS 真网卡 en0 的 MAC，其它系统用 `uuid.getnode()`；
    2. 拿不到真 MAC（`uuid.getnode()` 返回带多播位的随机值）时，改问平台级标识；
    3. 平台级标识也拿不到，才落到工作区里的持久标识。
    仍然什么都取不到时返回空串：宁可空，也**不**返回每次运行都变的假号。
    """
    node = _macos_physical_node() if platform.system() == "Darwin" else None
    if node is None:
        node = uuid.getnode()
    if (node >> 40) & 0x01:
        # 拿不到真 MAC：先找平台级标识，再落到工作区里的持久标识。
        seed = _platform_machine_seed() or _persisted_machine_seed()
        if not seed:
            return ""
    else:
        seed = f"{node:012x}"
    raw = f"{seed}:{platform.system()}:{platform.machine()}"
    return "dev-" + hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]


def _arg(name: str, default: str = "") -> str:
    try:
        index = sys.argv.index(name)
    except ValueError:
        return default
    if index + 1 >= len(sys.argv):
        return default
    return str(sys.argv[index + 1]).strip() or default


def read_installation() -> str:
    """返回当前版本号；读不到返回空串。分版已取消，不再读 edition。"""
    try:
        payload = json.loads(INSTALLATION_FILE.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            return ""
        return str(payload.get("current_version") or "").strip()
    except Exception:
        return ""


def normalize_report(report: dict) -> dict:
    """补上可选字段的默认值。

    服务端对可选字段的校验是「必须存在且是字符串」——省略 `resolution` 会被判
    `resolution_must_be_string`。这里替 AI 兜住，让它可以放心地省略。
    """
    normalized = dict(report)
    for field in ("resolution", "recommended_action"):
        if not isinstance(normalized.get(field), str):
            normalized[field] = ""
    for field in ("actions_taken", "check_results"):
        if not isinstance(normalized.get(field), list):
            normalized[field] = []
    return normalized


def main() -> int:
    if "--help" in sys.argv or "-h" in sys.argv:
        print(USAGE)
        return 0
    report_file = Path(_arg("--file") or DEFAULT_REPORT_FILE)
    try:
        report = json.loads(report_file.read_text(encoding="utf-8"))
        if not isinstance(report, dict):
            raise ValueError("report_not_object")
    except Exception:
        print("没有找到可上报的诊断报告，已跳过上传。")
        return 0

    version = read_installation()
    body = json.dumps(
        {
            "machine_id": machine_fingerprint(),
            "version": version,
            "reported_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "report": normalize_report(report),
        },
        ensure_ascii=False,
    ).encode("utf-8")

    request = urllib.request.Request(
        DIAGNOSTIC_REPORT_ENDPOINT,
        data=body,
        headers={"Content-Type": "application/json", "User-Agent": USER_AGENT},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=REPORT_TIMEOUT_SECONDS) as response:
            response.read()
        print("诊断报告已上传。")
    except urllib.error.HTTPError as error:
        if error.code == 400:
            print("诊断报告被服务端拒收（可能含凭据等敏感信息），已留存本地。")
        elif error.code == 413:
            # 服务端对这条无鉴权通道只放 2mb；正常诊断会话到不了这个量级。
            print("诊断报告太大，服务端没有收，已留存本地。")
        else:
            print("诊断报告暂时没能上传，已留存本地，下次诊断会再试。")
    except Exception:
        print("诊断报告暂时没能上传，已留存本地，下次诊断会再试。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
