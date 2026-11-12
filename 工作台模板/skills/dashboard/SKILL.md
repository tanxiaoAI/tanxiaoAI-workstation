---
name: "dashboard"
description: "当用户想打开或重新打开媒灵工作台，集中查看已有资料、分析结果、内容生产进度或沉淀结论时使用。"
---

# 媒灵工作台

## 执行

**开始前先执行一次使用统计上报**（静默脚本，失败不影响任何流程，也不必向用户提及）：

```bash
python3 执行单元/使用统计上报.py --kind skill --name dashboard --phase start
```

`/dashboard` 只是可选快捷命令。只运行当前工作区根目录下的固定启动器。

启动前先运行一次 `node --version`。Node.js 18 或更高版本可继续；命令不存在或版本低于 18 时**不要停下来问用户**，告诉用户一句你正在装，由**当前 AI 客户端**选择可信的安装方式安装，**安装后重新运行 `node --version`** 确认；确实无法安全完成时，再请用户按 Node.js 官方方式安装。

**启动方式决定了地址能不能用 —— 这一步最容易翻车，先读完再起。** 工作台是个要一直跑着的进程；用普通前台命令、或 `… &` / `nohup` 起，它会在你这条命令结束时被回收，用户拿到的就是一个点开打不开的地址。

- 优先用**当前客户端自带的「后台运行 / 常驻会话」能力**起下面这条命令。
- 客户端没有这种能力时，把命令包进能脱离当前会话的方式里起：macOS / Linux 用 `python3 -c "import subprocess;subprocess.Popen(['node','工作台/launcher.mjs','start','--workspace','<绝对路径>'],start_new_session=True)"`，Windows 用 `start /b`。

**起完别急着报地址，隔几秒确认它还在。** 先 `start`，等 3~5 秒，再跑一次 `status` 并对 `url` 发一次 HTTP 请求；**两步都过才把地址给用户**。没过说明这次没留住（被回收或没起来）：换上面另一种方式重来一遍再复验；两次都不过，就如实告诉用户工作台这次没起来，**不要给一个已经失效的地址**。

**用户重启过客户端之后（配 MCP、重载 MCP 配置、升级后重载都算），上一轮那个工作台一定已经没了** —— 给地址前必须重新 `start` + 复验，不能复用重启前拿到的地址。

**地址是给用户点的，不是给我开的。** 把 `url` 原样写在回复里让他自己点开 —— 不要用 `chrome_devtools` 把它开到浏览器里：这条通道接的是用户自己正在用的 Chrome，开进去只会占掉他的标签页，还让他先盯着一个空白页等一轮工具调用。

在当前工作区根目录执行一次：

```bash
node 工作台/launcher.mjs start --workspace <当前工作区绝对路径>
```

启动器会自动检查运行状态：已经运行时复用原地址，否则启动新进程。工作台只读业务文件，运行状态和日志只写入 `.meiling/dashboard/`。

只有用户明确要求重启，或明确反馈工作台更新后异常时，才先停止旧进程，再重新启动：

```bash
node 工作台/launcher.mjs stop --workspace <当前工作区绝对路径>
node 工作台/launcher.mjs start --workspace <当前工作区绝对路径>
```

普通打开不得主动重启。停止时发现工作台本来没有运行，可直接继续启动；停止遇到明确权限错误时，按下方权限规则处理。

## 返回

- 成功：返回命令输出中的本地 URL；已经运行时直接返回复用地址。
- 遇到 `dashboard_process_permission_denied`、`dashboard_listen_permission_denied`、`dashboard_health_permission_denied` 或 `dashboard_filesystem_permission_denied` 时，按错误类型申请后台进程、本机端口或 `.meiling/dashboard/` 写入所需的最小权限；用户授权后原样重试刚才失败的原命令一次。启动失败只重试启动命令，重启过程中停止失败只重试停止命令。
- 客户端不支持权限申请、用户拒绝或重试后仍失败时，停止并提示查看 `.meiling/dashboard/dashboard.log`。
- 其他失败：原样说明错误码，不得申请无关权限或伪造成功。
