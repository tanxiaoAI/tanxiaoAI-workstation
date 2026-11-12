#!/usr/bin/env python3
"""使用统计上报（静默副产品）。

用法：

    # skill 开头
    python3 执行单元/使用统计上报.py --kind skill --name dashboard --phase start

    # 流程开头 / 结尾
    python3 执行单元/使用统计上报.py --kind flow --name 内容定位流程 --phase start
    python3 执行单元/使用统计上报.py --kind flow --name 内容定位流程 --phase end --status ok

    # 环境检查之后（三个配置状态，覆盖式更新）
    python3 执行单元/使用统计上报.py --kind check --chrome 可用 --api 未配置 --asr 未配置

    # 引导里每一步做完 / 跳过（新增：--kind step）
    python3 执行单元/使用统计上报.py --kind step --name 上手引导流程 --step plan --status ok

    # 拿到用户给的主页链接（公开地址，由调用方按需传）
    python3 执行单元/使用统计上报.py --kind step --name 上手引导流程 --step homepage --homepage "https://..."

⚠️ 这个脚本的**第一要求是"没有存在感"**，改它之前先读完下面四条：

1. **永远 `exit 0`，永远不往 stdout / stderr 写任何东西。**
   它一旦报错，AI 会停下来"修"它，把用户正在做的事打断 —— 那比丢一条统计严重得多。
   所以这里不用 argparse（未知参数会 exit 2 并打印），全部手动取值并吞掉异常。
2. **超时 3 秒、失败重试一次，再失败就算了。** 上报是副产品，绝不能拖慢主流程。
   （2026-10-11：2 秒实测太贴 —— 端点正常响应约 1.9 秒，网络稍有波动就 timeout
    丢数据；放宽到 3 秒。2026-10-11 晚：单次失败就永久丢一条（实测引导漏斗缺了一层），
    加**一次**重试兜网络抖动；重试仍失败就放弃，不无限重试。单次超时上限由
    `tests/contracts/test_usage_report_contract.py` 的「不拖慢主流程」约束卡住，
    不要再往上加；重试次数也由该测试守着。）
3. **只读 `.meiling/installation.json`、`.meiling/客户端信息.json`、机器码，
   以及「本地配置.txt」里的更新凭据**；不碰用户的任何业务文件，也不上传路径、文件名或账号数据。
   唯一会传出去的用户内容是 **`--homepage` 由调用方传进来的主页链接**（用户主动给的公开地址，
   调用方负责只在真拿到时才传）。它用来回答「这个用户在做什么账号」，其余业务信息一律不碰。
   （**本脚本不传业务正文**。账号方向正文和调查问卷只保存在用户本机，不由这条统计通道上传。）
   每次都捎上操作系统（脚本自己测）与 AI 客户端（读 `客户端信息.json`，由引导 2.0 那步写入），
   服务端靠它们看「用户都跑在什么环境上」。
4. **必须显式设 `User-Agent`。** Python 默认 UA 会被 tanxiao.com.cn 的 Cloudflare
   按浏览器特征拦掉（403 `error code: 1010`，2026-10-01 已踩过）。

机器码算法必须与 `获取视频文案执行器.py` 的 `machine_fingerprint()` **逐字一致** ——
两边算出不同的值，服务端就会把同一台机器算成两个"用户"。
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

# 内置的上报端点：不占「本地配置.txt」字段，用户不需要配任何东西。
USAGE_REPORT_ENDPOINT = "https://tanxiao.com.cn/api/report_usage"
USAGE_REPORT_TIMEOUT_SECONDS = 3.0
# 首次 + 一次重试：只为兜网络抖动，不是「发到成功为止」。两次都失败就放弃
# （最坏 6 秒，且只在失败路径上；正常一次成功还是 ~2 秒）。
USAGE_REPORT_ATTEMPTS = 2
# 与其它联网模块一致；换成 Python 默认 UA 会被 Cloudflare 403 拦掉。
USER_AGENT = "Mozilla/5.0"
PAYLOAD_SCHEMA = 1

WORKSPACE_ROOT = Path(__file__).resolve().parent.parent
INSTALLATION_FILE = WORKSPACE_ROOT / ".meiling" / "installation.json"
LOCAL_LOG_FILE = WORKSPACE_ROOT / ".meiling" / "usage-report.log"
LOCAL_LOG_MAX_BYTES = 32 * 1024


# AI 客户端标识由引导 2.0 那一步写入（脚本猜不出自己在哪个客户端里跑）。
# 路径相对 WORKSPACE_ROOT 拼，方便测试整体替换。
CLIENT_INFO_REL = Path(".meiling") / "客户端信息.json"

# 操作系统与 AI 客户端的取值枚举 —— 与 `server/usage-stats.js` 的白名单**必须一致**，
# 改一处要两处一起改，否则服务端会把值当脏数据丢掉。
OS_BY_PLATFORM = {"Darwin": "macos", "Windows": "windows", "Linux": "linux"}
OS_VALUES = ("macos", "windows", "linux", "other")
CLIENT_VALUES = ("workbuddy", "claude-code", "codex", "cursor", "trae", "other")

USAGE = """用法：
  python3 执行单元/使用统计上报.py --kind skill --name <skill 名> --phase start
  python3 执行单元/使用统计上报.py --kind flow --name <流程名> --phase end --status ok
  python3 执行单元/使用统计上报.py --kind check --chrome <可用|不可用> --api <已配置|未配置> --asr <已配置|未配置>
  python3 执行单元/使用统计上报.py --kind step --name <流程名> --step <步骤 id> --status <ok|skipped>
  python3 执行单元/使用统计上报.py --kind step --name 上手引导流程 --step has-account --status ok --value <有|没有>

  --kind    skill | flow | check | step，必填
  --name    skill 名或流程名（kind 为 check 时不用）
  --phase   start | end，默认 start
  --status  ok | blocked | skipped，流程收尾或单步跳过时填
  --step    步骤 id（kind 为 step 时填，如 plan / diagnose）
  --value   判断题的答案（step 为 has-account / production-mode 时填，其余不用）
  --homepage  用户给的主页链接（公开地址；只在真拿到时传，拿不到就不传）
  --chrome  环境检查结果：可用 | 不可用
  --api     环境检查结果：已配置 | 未配置
  --asr     环境检查结果：已配置 | 未配置

正常运行时不打印任何东西；只有显式传 --help 才输出这段用法。
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


def read_installation() -> str:
    """返回当前版本号；读不到返回空串，不影响上报。

    2026-11-12：分版取消，installation.json 里不再有 edition，这里也就不读了。
    """
    try:
        payload = json.loads(INSTALLATION_FILE.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            return ""
        return str(payload.get("current_version") or "").strip()
    except Exception:
        return ""



def current_os() -> str:
    """操作系统：脚本自己测，比 AI 写的准；认不出的平台归 other。"""
    return OS_BY_PLATFORM.get(platform.system(), "other")


def client_name() -> str:
    """AI 客户端标识：读 `.meiling/客户端信息.json` 的 `client`。

    这个文件由引导 2.0 那一步写入 —— 只有 AI 自己知道它跑在哪个客户端里，脚本猜不出来。
    文件不在、不是合法 JSON、值不在枚举里，一律返回空串，**绝不抛异常**。
    """
    try:
        payload = json.loads((WORKSPACE_ROOT / CLIENT_INFO_REL).read_text(encoding="utf-8"))
    except Exception:
        return ""
    if not isinstance(payload, dict):
        return ""
    value = str(payload.get("client") or "").strip().lower()
    return value if value in CLIENT_VALUES else ""


def client_raw() -> str:
    """AI 客户端的原始显示名：文件里写什么就是什么（只限长、不校验）。

    兜底用：遇到枚举里没有的客户端（例如 Zed），`client` 只能写 `other`，
    原始名留在这里 —— 后台仍看得出到底是什么，也是以后决定要不要扩枚举的依据。
    """
    try:
        payload = json.loads((WORKSPACE_ROOT / CLIENT_INFO_REL).read_text(encoding="utf-8"))
    except Exception:
        return ""
    if not isinstance(payload, dict):
        return ""
    return str(payload.get("client_raw") or "").strip()[:24]


def homepage_value() -> str:
    """用户给的主页链接：只收 `http(s)` 开头的，其它一律丢掉（限长 200）。

    这是唯一会传出去的用户业务内容 —— 由调用方在真拿到链接时用 `--homepage` 传进来。
    """
    raw = _arg("--homepage").strip()
    if not raw.lower().startswith(("http://", "https://")):
        return ""
    return raw[:200]


def _arg(name: str, default: str = "") -> str:
    """手动取值：未知参数、缺值都不抛异常、不打印（见模块开头第 1 条）。"""
    try:
        index = sys.argv.index(name)
    except ValueError:
        return default
    if index + 1 >= len(sys.argv):
        return default
    value = str(sys.argv[index + 1]).strip()
    return value or default


def build_payload() -> dict:
    version = read_installation()
    kind = _arg("--kind")
    payload: dict = {
        "schema": PAYLOAD_SCHEMA,
        "machineId": machine_fingerprint(),
        "kind": kind,
        "name": _arg("--name"),
        "phase": _arg("--phase", "start"),
        "status": _arg("--status"),
        "version": version,
        # 环境：操作系统脚本自己测；客户端读引导 2.0 写下的那份文件（读不到就是空）。
        "os": current_os(),
        "client": client_name(),
        "clientRaw": client_raw(),
        # 只有调用方传了主页链接才有值，其余情况是空串。
        "homepage": homepage_value(),
        "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    if kind == "step":
        # 引导里每一步一条：step 记是哪个步骤，status 记做完（ok）还是跳过（skipped）。
        payload["step"] = _arg("--step")[:32]
        # 判断题选了什么（`有`/`没有`、`有参考`/`无参考`）—— 只有判断那两步会传，其余是空串。
        # 没这个字段后台就只能靠「后面走了哪个流程」猜，会猜错。
        payload["value"] = _arg("--value")[:32]
    if kind == "check":
        # 三项配置状态：可用/不可用、已配置/未配置。服务端按机器码覆盖式更新。
        payload["config"] = {
            "chrome": _arg("--chrome", "未检查"),
            "api": _arg("--api", "未检查"),
            "asr": _arg("--asr", "未检查"),
        }
    return payload


def _log_failure(reason: str) -> None:
    """失败时在本地留一行痕，方便事后查"为什么没有数据"。

    纯尽力而为：写不进去就算了，绝不因此抛异常。
    """
    try:
        if LOCAL_LOG_FILE.exists() and LOCAL_LOG_FILE.stat().st_size > LOCAL_LOG_MAX_BYTES:
            return
        LOCAL_LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
        with LOCAL_LOG_FILE.open("a", encoding="utf-8") as handle:
            handle.write(f"{stamp}\t{reason}\n")
    except Exception:
        pass


def report(payload: dict) -> None:
    request = urllib.request.Request(
        USAGE_REPORT_ENDPOINT,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json", "User-Agent": USER_AGENT},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=USAGE_REPORT_TIMEOUT_SECONDS) as response:
        response.read()


def _attempt_report(payload: dict) -> str:
    """发一次；成功返回空串，失败返回原因串。异常一律转成字符串，绝不外抛。"""
    try:
        report(payload)
        return ""
    except urllib.error.HTTPError as error:
        return f"http {error.code}"
    except Exception as error:  # noqa: BLE001 —— 静默是第一要求
        return type(error).__name__


def main() -> int:
    # 唯一的"会说话"的分支：显式要 help 才打印。其余情况一律静默（见模块开头第 1 条）。
    if "--help" in sys.argv or "-h" in sys.argv:
        print(USAGE)
        return 0
    try:
        payload = build_payload()
    except Exception as error:  # noqa: BLE001 —— 静默是第一要求
        _log_failure(f"build/{type(error).__name__}")
        return 0
    if not payload.get("kind"):
        return 0
    # 首次 + 一次重试，只为兜网络抖动；两次都失败才在本机记一行，然后放弃。
    reason = ""
    for _ in range(USAGE_REPORT_ATTEMPTS):
        reason = _attempt_report(payload)
        if not reason:
            return 0
    _log_failure(reason)
    return 0


if __name__ == "__main__":
    sys.exit(main())
