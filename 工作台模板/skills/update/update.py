#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""媒灵-自媒体AI工作台 —— 工作区更新脚本

把工作区的「工具层」升级到最新发布版；用户的资料一律不动。

两种用法：
    python3 skills/update/update.py --check              # 只检查，不下载、不覆盖
    python3 skills/update/update.py --apply              # 实际更新（覆盖前自动备份）

可选参数：
    --manifest-url <url>   指定更新清单地址（一般不传，默认用内置地址）。
                           也可以传本地文件路径，用于离线 / 人工升级。
    --force                版本号相同也强制重装（修复被改坏的工具层）

设计要点：
    1. 只有一条线。2026-11-12 起产品完全开源 —— 不再分免费版 / 完整版，
       没有升级码，也不做设备登记。原来那套「按升级码选通道 + 一码绑 N 台」
       的逻辑整段删掉了，所有人读同一份清单、下同一个包。
    2. 增量覆盖。只覆盖包内有的文件；该删的按清单的 removed_files 删。
       用户自己放进受管目录的文件不在 removed_files 里，因此不会被误删。
    3. 多个下载地址。packages 是数组，按顺序试第一个能用的。
       镜像地址写在清单里而不是代码里 —— 换镜像不用重发客户端。
    4. 覆盖前把原工具层完整备份，失败可回退。
"""

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request
import zipfile
from datetime import datetime
from pathlib import Path

WORKSPACE_ROOT = Path(__file__).resolve().parents[2]

USER_AGENT = "meiling-open-source-update/2.0"

# ── 更新通道 ────────────────────────────────────────────────────────────────
# 只有一条线：所有人拿同一份清单、同一个包。
# 2026-11-12 起产品完全开源 —— 不再分免费版 / 完整版，没有升级码，
# 也不再按设备登记。原来那套双通道 + 升级码 + 设备绑定的逻辑整段删掉了。
# 两个旧地址（/update/free/、/update/full/）不再被这个脚本读取；
# 已经装出去的老客户端还认它们，所以服务端那边保留着没动。
MANIFEST_URL = "https://update.tanxiao.com.cn/update/update-manifest.json"

MANIFEST_SCHEMA = "meiling-open-source-update-v2"

STATE_REL = ".meiling/installation.json"
BACKUP_REL = ".meiling/backup"
DASHBOARD_RUNTIME_REL = ".meiling/dashboard/runtime.json"
CONFIG_REL = "本地配置.txt"

# 脚本级最后防线：无论清单怎么写，这些路径一律不覆盖、不删除。
PROTECTED_ROOTS = [
    "对标资料",
    "我方资料",
    "沉淀结论",
    ".meiling",
]
PROTECTED_FILES = [
    "本地配置.txt",
    "本地配置.env",
]

# ── 客户端技能目录的「重新适配」（2026-10-09）───────────────────────────────
# 交付包里**不带**任何 `.xxx/skills`：客户端读哪个目录只有客户端自己知道，发布侧猜不出来。
# 装机时由使用者按当前客户端现场适配一次（见 `执行流程/上手引导流程.md` 的 `2.1`），
# 更新时由这里把**本来就存在**的那几处刷成新的 `skills/`。
# ⚠️ 只刷新已存在的，**不凭空创建** —— 否则会把客户端根本读不到的目录建出来。
CLIENT_SKILL_DIRS = (
    ".workbuddy",
    ".claude",
    ".codex",
    ".trae",
    ".cursor",
    ".windsurf",
    ".gemini",
)
CLIENT_SKILL_SUFFIX = "skills"


def die(message):
    print("失败：" + message, file=sys.stderr)
    sys.exit(1)


def human_bytes(n):
    n = float(n or 0)
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return "{:.1f} {}".format(n, unit)
        n /= 1024
    return "{:.1f} GB".format(n)


def remove_path(path):
    if path.is_symlink() or path.is_file():
        path.unlink()
    elif path.is_dir():
        shutil.rmtree(path)


def sha256_file(path):
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


# ── 本地状态 ────────────────────────────────────────────────────────────────

def read_local_version():
    """读当前版本号；读不到返回 None。

    老工作区的 installation.json 里还留着 "edition" 字段（free / full），
    这里直接忽略 —— 分版已经取消，留着不影响。
    """
    path = WORKSPACE_ROOT / STATE_REL
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    if not isinstance(data, dict):
        return None
    return str(data.get("current_version") or "").strip() or None


# ── 清单 ────────────────────────────────────────────────────────────────────

def read_raw(url, timeout=60):
    if url.startswith("file://"):
        return Path(urllib.request.url2pathname(url[7:])).read_bytes()
    if "://" not in url:
        return Path(url).read_bytes()
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.read()


def explain_manifest_fetch_failure(url, error):
    """把「取清单失败」翻译成人话。"""
    code = getattr(error, "code", None)
    if code == 404:
        return (
            "更新清单在这个地址上不存在（HTTP 404）。\n"
            "  - 地址：{}\n"
            "  - 可能是地址已经变更，请到发布页确认最新地址。".format(url)
        )
    if code in (401, 403):
        return (
            "服务端拒绝了这次请求（HTTP {}）。\n"
            "  - 地址：{}\n"
            "  - 如果这个地址需要凭据，请联系发布方。".format(code, url)
        )
    return "取不到更新清单（{}）：{}".format(url, error)


def load_manifest(url):
    try:
        raw = read_raw(url)
    except Exception as error:
        die(explain_manifest_fetch_failure(url, error))
    try:
        manifest = json.loads(raw.decode("utf-8"))
    except Exception as error:
        die("更新清单不是合法 JSON：{}".format(error))
    if not isinstance(manifest, dict):
        die("更新清单结构不对。")
    return manifest


def validate_manifest(manifest):
    if manifest.get("schema") != MANIFEST_SCHEMA:
        die(
            "更新清单格式不认识（schema={}，本脚本只认 {}）。".format(
                manifest.get("schema"), MANIFEST_SCHEMA
            )
        )

    version = str(manifest.get("version") or "").strip()
    if not version:
        die("更新清单没写版本号。")

    packages = manifest.get("packages")
    if not isinstance(packages, list) or not packages:
        die("更新清单没有列出任何下载地址。")
    for item in packages:
        if not isinstance(item, dict) or not str(item.get("url") or "").strip():
            die("更新清单里有下载地址为空。")
        if not str(item.get("sha256") or "").strip():
            die("更新清单缺少包的校验值。")

    roots = [str(item).strip().strip("/") for item in (manifest.get("managed_roots") or [])]
    files = [str(item).strip() for item in (manifest.get("managed_files") or [])]
    removed = [str(item).strip() for item in (manifest.get("removed_files") or [])]
    if not roots and not files:
        die("更新清单没有声明任何受管路径。")

    for relative in roots + files + removed:
        parts = relative.replace("\\", "/").split("/")
        if any(part in ("", ".", "..") for part in parts):
            die("更新清单里有不安全的路径，已拒绝执行：{}".format(relative))
        for protected in PROTECTED_ROOTS + PROTECTED_FILES:
            if relative == protected or relative.startswith(protected + "/"):
                die("更新清单列出了受保护的路径，已拒绝执行：{}".format(relative))

    return version, packages, roots, files, removed


# ── 下载 ────────────────────────────────────────────────────────────────────

def fetch_to(url, destination, timeout=300):
    if url.startswith("file://"):
        shutil.copyfile(urllib.request.url2pathname(url[7:]), destination)
        return
    if "://" not in url:
        shutil.copyfile(url, destination)
        return
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        with open(destination, "wb") as handle:
            while True:
                chunk = response.read(1 << 16)
                if not chunk:
                    break
                handle.write(chunk)


def download_package(packages, destination):
    """按清单顺序逐个地址试，第一个下载且校验通过的就用。"""
    problems = []
    for item in packages:
        url = str(item.get("url") or "").strip()
        expected = str(item.get("sha256") or "").strip().lower()
        label = str(item.get("label") or "").strip() or "未命名地址"
        print("尝试下载（{}）：{}".format(label, url))
        try:
            fetch_to(url, destination)
        except Exception as error:
            print("  这个地址不通：{}".format(error))
            problems.append("{}：{}".format(label, error))
            continue
        actual = sha256_file(destination)
        if expected and actual != expected:
            print("  这个地址拿到的包校验不过，换下一个。")
            problems.append("{}：校验不过".format(label))
            continue
        size = destination.stat().st_size
        print("下载完成：{}".format(human_bytes(size)))
        return
    die(
        "清单里的下载地址全都不通：\n  - " + "\n  - ".join(problems)
        if problems
        else "清单里的下载地址全都不通。"
    )


def find_payload_root(extract_dir):
    """包内顶层如果是个单目录，就把它当成真正的根。"""
    items = [item for item in extract_dir.iterdir()]
    directories = [item for item in items if item.is_dir()]
    if len(items) == 1 and len(directories) == 1:
        return directories[0]
    return extract_dir


# ── 备份 ────────────────────────────────────────────────────────────────────

def backup_targets(backup_dir, roots, files, local_version):
    backup_dir.mkdir(parents=True, exist_ok=True)
    for relative in roots:
        source = WORKSPACE_ROOT / relative
        if not source.exists():
            continue
        destination = backup_dir / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        if source.is_dir():
            shutil.copytree(source, destination, symlinks=False)
        else:
            shutil.copy2(source, destination)
    for relative in files:
        source = WORKSPACE_ROOT / relative
        if not source.exists():
            continue
        destination = backup_dir / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)
    info = {
        "backed_up_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "version_before": local_version,
        "workspace_root": str(WORKSPACE_ROOT),
    }
    (backup_dir / "backup-info.json").write_text(
        json.dumps(info, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


# ── 应用 ────────────────────────────────────────────────────────────────────

def write_one(source, destination):
    """内容一样就跳过；返回 True 表示真的写了。"""
    if destination.is_file() and sha256_file(source) == sha256_file(destination):
        return False
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, destination)
    return True


def apply_update(payload_root, roots, files, removed_files):
    """增量覆盖：只写包内有的文件，只删 removed_files 里列的路径。

    注意这里**不做整目录删除** —— 用户自己放进受管目录的文件，
    既不在包里、也不在 removed_files 里，因此会原样保留。
    """
    report = []

    for relative in files:
        source = payload_root / relative
        if source.is_file():
            verb = "写入" if write_one(source, WORKSPACE_ROOT / relative) else "未变"
            report.append("{}：{}".format(verb, relative))
        else:
            report.append("跳过（新版包内没有）：{}".format(relative))

    for relative in roots:
        source_root = payload_root / relative
        if not source_root.is_dir():
            report.append("跳过（新版包内没有）：{}/".format(relative))
            continue
        written = 0
        unchanged = 0
        for path in sorted(source_root.rglob("*")):
            if not path.is_file():
                continue
            if write_one(path, WORKSPACE_ROOT / path.relative_to(payload_root)):
                written += 1
            else:
                unchanged += 1
        report.append("已同步：{}/（更新 {} 个，未变 {} 个）".format(relative, written, unchanged))

    for relative in removed_files:
        target = WORKSPACE_ROOT / relative
        if target.exists():
            remove_path(target)
            report.append("删除（新版已移除）：{}".format(relative))

    # 把「因为上面这次删除才变空」的目录也清掉 —— 否则改名/退役的旧目录会留下一个空壳。
    # 只动**确实是空的**目录：用户自己放了东西的目录不会被删。
    pruned = set()
    for relative in removed_files:
        parent = (WORKSPACE_ROOT / relative).parent
        while parent != WORKSPACE_ROOT and parent.is_dir():
            if parent.name in PROTECTED_ROOTS:
                break
            try:
                if any(parent.iterdir()):
                    break
            except OSError:
                break
            try:
                parent.rmdir()
            except OSError:
                break
            pruned.add(str(parent.relative_to(WORKSPACE_ROOT)))
            parent = parent.parent
    for relative in sorted(pruned, reverse=True):
        report.append("删除空目录：{}/".format(relative))

    if not removed_files:
        report.append("没有需要删除的文件（清单未列出 removed_files）。")
    return report


def write_state(version):
    path = WORKSPACE_ROOT / STATE_REL
    path.parent.mkdir(parents=True, exist_ok=True)
    data = {}
    if path.exists():
        try:
            existing = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(existing, dict):
                data = existing
        except Exception:
            data = {}
    data["current_version"] = version
    # 分版已经取消：老文件里可能还留着 "edition"，这里主动抹掉，免得下游误读。
    data.pop("edition", None)
    data["updated_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    path.write_text(
        json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


def restart_dashboard():
    """升级完原地重启正在运行的工作台服务进程，返回给用户看的一行话；没在运行返回 None。

    为什么必须重启（2026-11-03 实测）：工具层文件覆盖完、installation.json 写成新形态，
    但工作台服务进程还握着升级前的投影，页面继续显示旧版本 / 旧形态。
    用户不会自己重启工作台，所以这一步必须脚本做掉：
    读 runtime.json 拿端口，launcher stop → start，地址不变。
    """
    runtime_path = WORKSPACE_ROOT / DASHBOARD_RUNTIME_REL
    try:
        runtime = json.loads(runtime_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(runtime, dict):
        return None
    if str(runtime.get("workspace_root") or "") != str(WORKSPACE_ROOT):
        return None
    pid = runtime.get("pid")
    if not isinstance(pid, int):
        return None
    try:
        os.kill(pid, 0)
    except OSError:
        return None

    launcher = WORKSPACE_ROOT / "工作台" / "launcher.mjs"
    if not launcher.is_file():
        return None
    url = str(runtime.get("url") or "")
    port = url.rsplit(":", 1)[-1]
    if not port.isdigit():
        return None

    manual = (
        "工作台自动重启没成功。如果工作台正开着，请手动重启一次，"
        "否则页面还是旧版本：\n"
        "  node 工作台/launcher.mjs stop --workspace .\n"
        "  node 工作台/launcher.mjs start --workspace . --port {}".format(port)
    )
    try:
        stop = subprocess.run(
            ["node", str(launcher), "stop", "--workspace", str(WORKSPACE_ROOT)],
            capture_output=True,
            text=True,
            timeout=120,
        )
        if stop.returncode != 0:
            return manual
        start = subprocess.run(
            [
                "node",
                str(launcher),
                "start",
                "--workspace",
                str(WORKSPACE_ROOT),
                "--port",
                port,
            ],
            capture_output=True,
            text=True,
            timeout=120,
        )
    except (OSError, subprocess.TimeoutExpired):
        return manual
    if start.returncode != 0:
        return manual
    return "工作台进程已重启（地址不变：{}），浏览器里刷新页面即可看到新版本。".format(
        url or "http://127.0.0.1:{}".format(port)
    )


def find_client_skill_mirrors():
    """工作区里**已经存在**的客户端技能目录（相对路径）。没装的不返回，也不建。"""
    found = []
    for name in CLIENT_SKILL_DIRS:
        if (WORKSPACE_ROOT / name / CLIENT_SKILL_SUFFIX).is_dir():
            found.append("{}/{}".format(name, CLIENT_SKILL_SUFFIX))
    return found


def readapt_client_skill_mirrors(relative_roots):
    """把 `skills/` 整目录刷进那几处已经存在的客户端技能目录，返回报告行。

    整目录替换（先删后拷）：`skills/` 里退役的技能不能在镜像里留下残骸。
    """
    source_root = WORKSPACE_ROOT / "skills"
    if not source_root.is_dir() or not relative_roots:
        return []
    report = []
    for relative in relative_roots:
        destination = WORKSPACE_ROOT / relative
        if destination.exists():
            remove_path(destination)
        shutil.copytree(
            source_root,
            destination,
            ignore=shutil.ignore_patterns(".DS_Store", "__pycache__"),
        )
        count = sum(1 for item in destination.rglob("*") if item.is_file())
        report.append("已重新适配：{}/（{} 个文件）".format(relative, count))
    return report


def describe_scope(roots, files):
    print("更新会覆盖（工具层）：")
    for relative in roots:
        print("  - {}/".format(relative))
    for relative in files:
        print("  - {}".format(relative))
    print("")
    print("更新不会动（你的资料和本地配置）：")
    for relative in PROTECTED_ROOTS + PROTECTED_FILES:
        print("  - {}".format(relative))
    print("")
    print("另外：你自己放进上面那些受管目录里的文件不会被删 ——")
    print("      更新只写新版包里有的文件，只删清单明确列出要移除的那几个。")


# ── 命令 ────────────────────────────────────────────────────────────────────

def cmd_check(args):
    local_version = read_local_version()
    url = args.manifest_url or MANIFEST_URL
    manifest = load_manifest(url)
    version, _packages, roots, files, _removed = validate_manifest(manifest)

    print("工作区：{}".format(WORKSPACE_ROOT))
    print("当前版本：{}".format(local_version or "未记录"))
    print("线上版本：{}".format(version))
    print("清单来源：{}".format(url))
    created = str(manifest.get("created_at") or "").strip()
    if created:
        print("发布时间：{}".format(created))
    print("")

    if local_version == version and not args.force:
        print("结论：已经是最新版本，无需更新。")
        return 0

    if local_version and local_version != version:
        print("结论：有新版本 {}（本地 {}）。".format(version, local_version))
    else:
        print("结论：可以更新到 {}。".format(version))
    print("")
    describe_scope(roots, files)
    print("")
    print("确认后执行：python3 skills/update/update.py --apply")
    return 0


def cmd_apply(args):
    local_version = read_local_version()
    url = args.manifest_url or MANIFEST_URL
    manifest = load_manifest(url)
    version, packages, roots, files, removed = validate_manifest(manifest)

    if local_version == version and not args.force:
        print("已经是最新版本 {}，无需更新。".format(version))
        return 0

    print("工作区：{}".format(WORKSPACE_ROOT))
    print("当前版本：{}".format(local_version or "未记录"))
    print("目标版本：{}".format(version))
    print("清单来源：{}".format(url))
    print("")

    with tempfile.TemporaryDirectory(prefix="meiling-update-") as temp_dir:
        temp_path = Path(temp_dir)
        archive = temp_path / "package.zip"
        download_package(packages, archive)

        actual = sha256_file(archive)
        expected = str(packages[0].get("sha256") or "").strip().lower()
        if actual != expected:
            die(
                "下载的包校验不过（期望 {}…，实际 {}…），已中止，工作区没有被改动。".format(
                    expected[:12], actual[:12]
                )
            )
        print("包校验通过。")

        extract_dir = temp_path / "extract"
        extract_dir.mkdir()
        with zipfile.ZipFile(archive) as bundle:
            bundle.extractall(extract_dir)

        payload_root = find_payload_root(extract_dir)
        print("包内顶层：{}".format(payload_root.name))

        # 技能镜像要先记账再动手：这次更新会删掉旧版本交付的镜像文件（在 removed 里），
        # 得先知道原来有哪几处，最后再用新的 skills/ 刷回去。
        client_mirrors = find_client_skill_mirrors()

        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        backup_dir = WORKSPACE_ROOT / BACKUP_REL / ("update-" + stamp)
        backup_targets(backup_dir, roots + client_mirrors, files, local_version)
        print("已备份当前工具层到：{}".format(backup_dir.relative_to(WORKSPACE_ROOT)))
        print("")

        print("开始覆盖：")
        for line in apply_update(payload_root, roots, files, removed):
            print("  - {}".format(line))

        for line in readapt_client_skill_mirrors(client_mirrors):
            print("  - {}".format(line))

        write_state(version)

    print("")
    restart_note = restart_dashboard()
    print("更新完成：{} → {}".format(local_version or "未记录", version))
    print("备份位置：{}（确认没问题后可以自行删除）".format(BACKUP_REL + "/update-" + stamp))
    if restart_note:
        print(restart_note)
    return 0


def main():
    parser = argparse.ArgumentParser(
        description="媒灵-自媒体AI工作台 —— 工作区更新"
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--check", action="store_true", help="只检查版本，不下载、不覆盖")
    group.add_argument("--apply", action="store_true", help="实际更新，覆盖前自动备份")
    parser.add_argument(
        "--manifest-url", default="", help="更新清单地址（留空则用内置地址）"
    )
    parser.add_argument("--force", action="store_true", help="版本相同也强制重装")
    args = parser.parse_args()

    if args.check:
        return cmd_check(args)
    return cmd_apply(args)


if __name__ == "__main__":
    sys.exit(main())
