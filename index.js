/**
 * 自媒体AI工作台 DS 版 — Host 半部
 *
 * DS 版和开源版是**两个独立产品**，各有各的一套工作台：
 * `工作台模板/` 这一份只属于 DS 版，界面和流程改这里，开源版那边不动。
 * 插件负责把这份模板装进用户选的目录，再挂到宿主 webServer 的前缀下：
 *
 *   GET/POST  /selfmedia-ds/app/**      反向代理到进程内的工作台实例
 *   GET       /selfmedia-ds/bridge.js   注入到 iframe 页面里的前缀适配脚本
 *   GET/POST  /selfmedia-ds/api/workspace  当前读哪个工作区
 *   POST      /selfmedia-ds/api/run     把一条指令排进工作区会话
 *   GET       /selfmedia-ds/api/activity  那个会话在不在跑
 *   GET       /selfmedia-ds/api/state   给界面看的状态（起没起来、错在哪）
 *
 * 用户目录里只有数据（我方资料 / 对标资料）和他的本地配置，工具层每次都用模板铺。
 */
import fs from 'node:fs/promises'
import os from 'node:os'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { spawn } from 'node:child_process'
import { createHash } from 'node:crypto'

import {
  APP_BASE,
  BRIDGE_SCRIPT,
  BRIDGE_SOURCE,
  SHELL_SCRIPT,
  SHELL_STYLE,
  proxyRequest,
  probeWorkspace,
  startDashboard,
} from './bridge.mjs'

export const inject = ['webServer']

const ROUTE_PREFIX = '/selfmedia-ds/api'
const CONFIG_PATH = path.join(
  (process.env.DSH_HOME || '').trim() || path.join(os.homedir(), '.dsh'),
  'selfmedia-ds.config.json',
)

/** 诊断用：插件自己的一行行日志。宿主没有可看的地方，出问题就靠它。 */
const LOG_PATH = '/tmp/ds-selfmedia.log'
function log(...args) {
  const line = `[${new Date().toISOString()}] ${args
    .map((item) => (typeof item === 'string' ? item : JSON.stringify(item)))
    .join(' ')}\n`
  void fs.appendFile(LOG_PATH, line).catch(() => {})
}

/* ------------------------------------------------------------- 配置 */

async function readConfig() {
  try {
    const text = await fs.readFile(CONFIG_PATH, 'utf8')
    const value = JSON.parse(text)
    return value && typeof value === 'object' ? value : {}
  } catch {
    return {}
  }
}

async function writeConfig(patch) {
  const current = await readConfig()
  const next = { ...current, ...patch }
  await fs.mkdir(path.dirname(CONFIG_PATH), { recursive: true })
  await fs.writeFile(CONFIG_PATH, JSON.stringify(next, null, 2), 'utf8')
  return next
}

/**
 * 配置 > 环境变量。都没有就返回空字符串——**不猜路径**。
 * 猜默认目录会在别人机器上指到一个不存在的地方，报错还看不懂。
 */
async function resolveWorkspaceRoot() {
  const config = await readConfig()
  const fromConfig = String(config.workspaceRoot || '').trim()
  if (fromConfig) return fromConfig
  const fromEnv = String(process.env.SELFMEDIA_WORKSPACE_ROOT || '').trim()
  if (fromEnv) return fromEnv
  return ''
}

/* --------------------------------------------------------- 找工作区 */

const SKIP_DIRS = new Set(['node_modules', '.git', 'Library', 'Applications', 'System'])

async function listChildDirs(dir) {
  try {
    const entries = await fs.readdir(dir, { withFileTypes: true })
    return entries
      .filter((entry) => entry.isDirectory() && !entry.name.startsWith('.') && !SKIP_DIRS.has(entry.name))
      .slice(0, 200)
      .map((entry) => path.join(dir, entry.name))
  } catch {
    return []
  }
}

/** probe 的结果翻成一句人话，别把 code 直接丢给用户。 */
function explainProbe(probe, dir) {
  if (probe.code === 'other_product') {
    return `这个文件夹里装的是开源版的工作台，DS 版用自己的那份。换一个空目录，或者先删掉里面的工作台：${dir}`
  }
  if (probe.code === 'no_workspace') return '还没选工作区'
  if (probe.code === 'not_a_file') return `工作台入口不是一个文件：${probe.entry ?? dir}`
  return `这个文件夹里还没有工作台：${dir}`
}

/** 判定标准：目录里有工作台，而且那是 DS 版那套（带标识）。开源版的目录不算。 */
async function isWorkspace(dir) {
  return (await probeWorkspace(dir)).ok
}

let discoverCache = { at: 0, items: [] }
const DISCOVER_TTL_MS = 20_000

/**
 * 扫常见的几个位置（桌面 / 文稿 / 下载）往下两层，找出装了工作台的目录。
 * 不扫整个磁盘，也不进 node_modules 这类目录，一轮几十毫秒。
 */
async function discoverWorkspaces() {
  if (Date.now() - discoverCache.at < DISCOVER_TTL_MS) return discoverCache.items

  const home = os.homedir()
  const roots = [path.join(home, 'Desktop'), path.join(home, 'Documents'), path.join(home, 'Downloads')]
  const seen = new Set()
  const items = []

  const consider = async (dir) => {
    const resolved = path.resolve(dir)
    if (seen.has(resolved)) return
    seen.add(resolved)
    if (!(await isWorkspace(resolved))) return
    items.push({
      path: resolved,
      name: path.basename(resolved),
      parent: path.basename(path.dirname(resolved)),
    })
  }

  for (const root of roots) {
    for (const first of await listChildDirs(root)) {
      await consider(first)
      for (const second of await listChildDirs(first)) await consider(second)
    }
  }

  // 当前配置的那个即使扫不到（比如在工作区外），也要出现在列表里。
  const current = await resolveWorkspaceRoot()
  if (current && !seen.has(path.resolve(current))) {
    items.unshift({
      path: path.resolve(current),
      name: path.basename(path.resolve(current)),
      parent: path.basename(path.dirname(path.resolve(current))),
    })
  }

  discoverCache = { at: Date.now(), items }
  return items
}

/* --------------------------------------------------- 顶栏资源（属于工作台） */

/**
 * 顶栏脚本/样式读哪儿：**工作区里有就用工作区那份**，没有才退回插件自带模板。
 *
 * 这样「改导航、改那几个按钮」只发工作台就行 —— 用户点一次「更新」，
 * 工作区里的 `工作台/ds-shell.*` 被换掉，下次打开页面就是新的，不用重装插件。
 * 插件里那份只是初始种子，别让它盖掉用户已经更新的版本。
 */
async function readShellAsset(workspaceRoot, file) {
  if (workspaceRoot) {
    try {
      return await fs.readFile(path.join(workspaceRoot, '工作台', file))
    } catch {
      /* 工作区里没有，往下走 */
    }
  }
  try {
    return await fs.readFile(path.join(TEMPLATE_DIR, '工作台', file))
  } catch {
    return null
  }
}

/** 工作区里缺了顶栏文件（被删、或被别的更新冲掉）就补回来；已有的绝不动。 */
async function seedShellAssets(workspaceRoot) {
  for (const file of ['ds-shell.js', 'ds-shell.css', '.ds-edition']) {
    const target = path.join(workspaceRoot, '工作台', file)
    if (await pathExists(target)) continue
    try {
      await fs.copyFile(path.join(TEMPLATE_DIR, '工作台', file), target)
      log('补回工作台文件', file)
    } catch {
      /* 模板里没有这个文件就算了，不自作聪明 */
    }
  }
}

/* --------------------------------------------------------- 工作台实例 */

let runtime = null // { workspaceRoot, port, dashboard, close }
let starting = null
let lastError = null

/**
 * 每个会话「是不是在跑」。
 *
 * 不能从 `sessionController.list()` 里取 —— 列表行只有 `blank` / `lastPromptAt`，
 * 根本没有 running（2026-11-12 踩过：拿那个字段判断，永远 false，
 * 「跑完自动回工作台」就一直不触发）。宿主会在 `api-session/status` 上推真实状态。
 */
const sessionRunning = new Map()

/**
 * 每个会话「跑完去哪个页面」。
 *
 * 走文件，不走对话正文：AI 在回答末尾写 `[[goto:...]]` 会被渲染成正文，
 * 用户看到一行裸露的标记（2026-11-12 踩过）。所以改成让 AI 把这行写进
 * `<工作区>/.meiling/dashboard/goto.json`，宿主在这一轮跑完时读走并删掉。
 *
 * 取值两种：
 *   "view=topics"                         → 首页切视图（写 data-view 的值）
 *   "content.html?kind=own&id=xxx"        → 直接去详情页
 */
const sessionGoto = new Map()

/** 兼容旧姿势：万一 AI 还是把标记写进了正文，至少还能跳一次。 */
const GOTO_RE = /\[\[goto:([^\]\n]{1,300})\]\]/g

const GOTO_RELPATH = path.join('.meiling', 'dashboard', 'goto.json')

/** 超过这个时间的文件当残留（插件崩过一轮没人收）。 */
const GOTO_MAX_AGE = 30 * 60 * 1000

/**
 * 读走本轮的定位文件：读到就删，一份文件只活一轮。
 *
 * 不做「这轮开始之后写过才算」这种判断——宿主一轮里会多次推
 * running true/false（工具调用之间有间隙），拿开始时间去卡 mtime
 * 会把 AI 开头写的那份当成上一轮残留删掉。
 */
async function takeGotoSpec() {
  let root = ''
  try {
    root = await resolveWorkspaceRoot()
  } catch {
    return null
  }
  if (!root) return null
  const file = path.join(root, GOTO_RELPATH)
  try {
    const stat = await fs.stat(file)
    if (Date.now() - stat.mtimeMs > GOTO_MAX_AGE) {
      await fs.rm(file, { force: true })
      return null
    }
    const raw = (await fs.readFile(file, 'utf8')).trim()
    if (!raw) return null
    let spec = raw
    if (raw.startsWith('{')) {
      try {
        spec = String(JSON.parse(raw)?.spec ?? '')
      } catch {
        return null
      }
    }
    spec = String(spec || '')
      .replace(/^\[\[goto:/, '')
      .replace(/\]\]$/, '')
      .trim()
    await fs.rm(file, { force: true })
    return spec || null
  } catch {
    return null
  }
}

/**
 * 跑完回工作台这条链路出过太多次「到底卡在哪」的问题，所以把它写进
 * `~/.dsh/selfmedia-ds.log`。日志超过 512KB 就清空重来，不留垃圾。
 */
let activityLogReady = false
async function noteActivity(info) {
  try {
    const file = path.join(os.homedir(), '.dsh', 'selfmedia-ds.log')
    if (!activityLogReady) {
      activityLogReady = true
      const stat = await fs.stat(file).catch(() => null)
      if (stat && stat.size > 512 * 1024) await fs.writeFile(file, '')
    }
    await fs.appendFile(file, `${new Date().toISOString()} ${JSON.stringify(info)}\n`)
  } catch {
    /* 日志写不进去不影响功能 */
  }
}

/** 下一轮开跑前把上一轮的残留清掉（正常流程里读走时就删了）。 */
async function clearGotoFile() {
  try {
    const root = await resolveWorkspaceRoot()
    if (!root) return
    await fs.rm(path.join(root, GOTO_RELPATH), { force: true })
  } catch {
    /* 清不掉就算了，takeGotoSpec 还有时效兜底 */
  }
}

async function bootRuntime(workspaceRoot) {
  lastError = null
  if (workspaceRoot) await seedShellAssets(workspaceRoot)
  const next = await startDashboard(workspaceRoot)
  if (runtime) await runtime.close()
  runtime = next
  log('工作台已启动', workspaceRoot, 'port=' + next.port)
  return runtime
}

/** 并发调用只会起一次。失败的原因记下来给界面看，下一次调用重试。 */
async function ensureRuntime() {
  if (runtime) return runtime
  if (starting) return starting
  starting = (async () => {
    try {
      const workspaceRoot = await resolveWorkspaceRoot()
      return await bootRuntime(workspaceRoot)
    } catch (error) {
      const code = String(error?.code ?? 'start_failed')
      // 这几种不是故障，是"还没到那一步"：界面按 /workspace 的 ready 走首次进入页，
      // 别在这里塞一条红字，否则第一次打开看到的是「工作台没能启动」。
      if (code === 'no_workspace' || code === 'missing_entry' || code === 'other_product') {
        log('还没有可用的工作区', code)
        lastError = null
      } else {
        log('工作台启动失败', String(error?.message ?? error))
        lastError = {
          message: String(error?.message ?? error),
          code,
          at: Date.now(),
        }
      }
      throw error
    } finally {
      starting = null
    }
  })()
  return starting
}

async function restartRuntime(workspaceRoot) {
  if (starting) {
    try {
      await starting
    } catch {
      /* 上一轮起失败不影响这一轮 */
    }
  }
  lastError = null
  return bootRuntime(workspaceRoot)
}

/* --------------------------------------------------------- 装一份工作台 */

async function pathExists(target) {
  try {
    await fs.access(target)
    return true
  } catch {
    return false
  }
}

/**
 * 往一个空目录里铺一份工作台。
 *
 * 用的就是线上那份免费版包（跟更新器同一个清单、同一份 sha256），
 * 所以「装出来的东西」和「以后更新到的东西」是同一套，不会两套形态。
 */
/**
 * DS 版自带的那一套工作台（工作台/ + skills/ + 执行流程/ + 执行单元/ + 提示词/ …）。
 * 和开源版是两个产品，各自改各自的：这里的文件只属于 DS 版。
 */
const HERE = path.dirname(fileURLToPath(import.meta.url))
const TEMPLATE_DIR = path.join(HERE, '工作台模板')

/** 模板对应的版本号（放在插件根上，别混进用户工作区），装完写进 .meiling/installation.json。 */
async function readTemplateVersion() {
  try {
    const text = await fs.readFile(path.join(HERE, '工作台版本.txt'), 'utf8')
    return text.trim() || 'v0.0.0'
  } catch {
    return 'v0.0.0'
  }
}

export async function installWorkspace(targetRoot) {
  const root = path.resolve(targetRoot)
  const existing = await probeWorkspace(root)
  if (existing.ok) {
    return { ok: true, alreadyInstalled: true, workspaceRoot: root }
  }
  if (existing.code === 'other_product') {
    throw new Error('这个文件夹里是开源版的工作台，DS 版用自己的那份，换一个空目录')
  }

  await fs.mkdir(root, { recursive: true })

  // 先用插件自带的那一份。DS 版有自己的工作台，不跟开源版共用一个发行包。
  if (await pathExists(path.join(TEMPLATE_DIR, '工作台', 'server.mjs'))) {
    await fs.cp(TEMPLATE_DIR, root, { recursive: true, force: true })
    const version = await readTemplateVersion()
    await fs.mkdir(path.join(root, '.meiling'), { recursive: true })
    await fs.writeFile(
      path.join(root, '.meiling', 'installation.json'),
      JSON.stringify({ current_version: version, updated_at: new Date().toISOString() }, null, 2),
      'utf8',
    )
    log('工作台已安装（DS 自带）', root, version)
    return { ok: true, workspaceRoot: root, version }
  }

  // 插件包里没带模板（理论上不会发生），退回线上下载。
  const manifestResponse = await fetch(MANIFEST_URL, {
    headers: { accept: 'application/json', 'user-agent': 'meiling-ds/1.0' },
  })
  if (!manifestResponse.ok) throw new Error(`拿不到更新清单：HTTP ${manifestResponse.status}`)
  const manifest = await manifestResponse.json()
  const pkg = Array.isArray(manifest.packages) ? manifest.packages[0] : null
  if (!pkg?.url) throw new Error('更新清单里没有可下载的包')

  const packageResponse = await fetch(pkg.url, {
    headers: { 'user-agent': 'meiling-ds/1.0' },
  })
  if (!packageResponse.ok) throw new Error(`下载失败：HTTP ${packageResponse.status}`)
  const bytes = Buffer.from(await packageResponse.arrayBuffer())

  if (pkg.sha256) {
    const digest = createHash('sha256').update(bytes).digest('hex')
    if (digest !== String(pkg.sha256)) {
      throw new Error('下载的包校验不过，可能网络抖动，重试一次')
    }
  }

  const stamp = `${Date.now()}-${Math.random().toString(36).slice(2, 7)}`
  const zipPath = path.join(os.tmpdir(), `meiling-install-${stamp}.zip`)
  const extractDir = path.join(os.tmpdir(), `meiling-install-${stamp}`)
  await fs.writeFile(zipPath, bytes)
  await fs.mkdir(extractDir, { recursive: true })

  try {
    const python = await resolvePython(root)
    if (!python) throw new Error('找不到 Python，解压要用它。装好 Python 3 再试。')
    const unzip = await runCommand(python, ['-m', 'zipfile', '-e', zipPath, extractDir], root, 3 * 60 * 1000)
    if (!unzip.ok) throw new Error(`解压失败：${unzip.error || unzip.stderr.trim() || '未知原因'}`)

    // 包里通常套一层「自媒体AI工作台/」，剥掉，直接铺到用户选的目录里。
    const entries = await fs.readdir(extractDir, { withFileTypes: true })
    const dirs = entries.filter((entry) => entry.isDirectory())
    const files = entries.filter((entry) => entry.isFile())
    const source =
      dirs.length === 1 && files.length === 0
        ? path.join(extractDir, dirs[0].name)
        : extractDir

    await fs.cp(source, root, { recursive: true, force: true })

    if (!(await pathExists(path.join(root, '工作台', 'server.mjs')))) {
      throw new Error('包已经解开，但里面没找到「工作台/server.mjs」，这个包不对')
    }
  } finally {
    await fs.rm(zipPath, { force: true }).catch(() => {})
    await fs.rm(extractDir, { recursive: true, force: true }).catch(() => {})
  }

  log('工作台已安装', root, String(manifest.version || ''))
  return { ok: true, workspaceRoot: root, version: String(manifest.version || '') }
}

/* --------------------------------------------------------------- 更新 */

/** 跟 skills/update/update.py 的 MANIFEST_URL 保持一致：只有一条线。 */
const MANIFEST_URL = 'https://update.tanxiao.com.cn/update/update-manifest.json'

async function readInstallation(root) {
  try {
    const text = await fs.readFile(path.join(root, '.meiling', 'installation.json'), 'utf8')
    const value = JSON.parse(text)
    return value && typeof value === 'object' ? value : {}
  } catch {
    return {}
  }
}

async function checkUpdate() {
  const root = await resolveWorkspaceRoot()
  const installation = await readInstallation(root)
  const currentVersion = String(installation.current_version || '').trim()

  const response = await fetch(MANIFEST_URL, {
    headers: { accept: 'application/json', 'user-agent': 'meiling-ds/1.0' },
  })
  if (!response.ok) throw new Error(`更新服务器返回 ${response.status}`)
  const manifest = await response.json()
  const latestVersion = String(manifest.version || '').trim()

  return {
    ok: true,
    currentVersion: currentVersion || '未记录',
    latestVersion,
    hasUpdate: Boolean(latestVersion) && latestVersion !== currentVersion,
    checkedAt: Date.now(),
  }
}

/** 跑一个子进程，按行回吐输出；超时或找不到命令都要给一句人话。 */
function runCommand(command, args, cwd, timeoutMs) {
  return new Promise((resolve) => {
    const child = spawn(command, args, { cwd, env: process.env })
    let stdout = ''
    let stderr = ''
    let finished = false

    const timer = setTimeout(() => {
      if (finished) return
      finished = true
      try {
        child.kill('SIGKILL')
      } catch {
        /* 已经退了 */
      }
      resolve({ ok: false, code: -1, stdout, stderr, error: '太久没结束，已经停下了' })
    }, timeoutMs)

    child.stdout.on('data', (chunk) => {
      stdout += chunk.toString()
    })
    child.stderr.on('data', (chunk) => {
      stderr += chunk.toString()
    })
    child.on('error', (error) => {
      if (finished) return
      finished = true
      clearTimeout(timer)
      const missing = error?.code === 'ENOENT'
      resolve({
        ok: false,
        code: -1,
        stdout,
        stderr,
        error: missing ? `找不到命令：${command}` : String(error?.message ?? error),
      })
    })
    child.on('close', (code) => {
      if (finished) return
      finished = true
      clearTimeout(timer)
      resolve({ ok: code === 0, code, stdout, stderr })
    })
  })
}

/** 依次试几个可能的解释器，返回第一个能跑起来的。 */
async function resolvePython(cwd) {
  const candidates = process.platform === 'win32' ? ['python', 'py', 'python3'] : ['python3', 'python']
  for (const candidate of candidates) {
    const probe = await runCommand(candidate, ['--version'], cwd, 8000)
    if (probe.ok) return candidate
  }
  return null
}

async function applyUpdate() {
  const root = await resolveWorkspaceRoot()
  const script = path.join(root, 'skills', 'update', 'update.py')
  try {
    await fs.access(script)
  } catch {
    throw new Error('这个工作区里没有 skills/update/update.py，没法自动更新')
  }

  const python = await resolvePython(root)
  if (!python) throw new Error('找不到 Python，更新要用它。装好 Python 3 再试。')

  const result = await runCommand(python, [script, '--apply'], root, 10 * 60 * 1000)
  const output = `${result.stdout}${result.stderr}`.trim()

  if (!result.ok) {
    return {
      ok: false,
      message: result.error || '更新没成功，下面是脚本的输出',
      output,
    }
  }

  // 工具层文件换了，内存里跑的还是旧代码，得重起一次。
  await restartRuntime(root)
  log('更新后已重启工作台')
  return { ok: true, output, restarted: true }
}

/* --------------------------------------------------------- 送进会话 */

function sameDirectory(left, right) {
  if (!left || !right) return false
  return path.resolve(left) === path.resolve(right)
}

/**
 * 找「目录＝工作区」的会话。会话是按 cwd 执行命令的，工作台的流程文件
 * 都是相对 cwd 的路径，发给别的目录的会话，AI 读不到 执行流程/ 和 执行单元/。
 */
/**
 * 找「目录＝工作区」的会话，只读。
 * 会话是按 cwd 执行命令的，工作台的流程文件都是相对 cwd 的路径，
 * 发给别的目录的会话，AI 读不到 执行流程/ 和 执行单元/。
 */
async function findWorkspaceSession(sessionController, signal, workspaceRoot, preferSessionId) {
  const value = await sessionController.list({}, signal)
  const rows = (value?.items ?? []).filter(
    (row) => row && row.origin !== 'subagent' && sameDirectory(row.cwd, workspaceRoot),
  )
  // 前端点名要哪个会话就查哪个。不这么做的话，「刚建出来还没说话」的会话不在
  // 非空列表里，会退回到另一个空会话，前端一看 id 不对就立刻收手，永远不跳。
  if (preferSessionId) {
    const exact = rows.find((row) => String(row.sessionId) === String(preferSessionId))
    if (exact) return { row: exact, sessionId: exact.sessionId }
  }
  const nonBlank = rows.filter((row) => !row.blank)
  nonBlank.sort((a, b) => (b.updatedAt ?? 0) - (a.updatedAt ?? 0))
  const candidate = nonBlank[0] ?? rows.find((row) => row.blank) ?? null
  return candidate ? { row: candidate, sessionId: candidate.sessionId } : { row: null, sessionId: null }
}

/**
 * 把工作区目录登记给宿主，拿回它的 workspaceId。
 *
 * 这一步不能省。宿主的侧栏是按「工作区注册表」分组的，会话只带 cwd 建出来
 * 是没有归属的——它会掉进「默认工作区 / 未分组」，看着就像没选工作区。
 * `create` 是幂等的：这个目录登记过就直接返回原来那条，不会重复。
 */
async function ensureWorkspaceId(ctx, workspaceRoot) {
  const controller = ctx.get('workspaceController')
  if (!controller || typeof controller.create !== 'function') return null
  try {
    const result = await controller.create({ path: workspaceRoot })
    return result?.workspace?.workspaceId ?? null
  } catch {
    return null
  }
}

async function sendToSession(ctx, text) {
  const sessionController = ctx.get('sessionController')
  if (!sessionController) throw new Error('会话服务不可用')
  const signal = new AbortController().signal
  const workspaceRoot = await resolveWorkspaceRoot()
  if (!workspaceRoot) throw new Error('还没选工作区，先在工作台里选一个文件夹')

  // 先登记工作区，再在它里面开对话。
  const workspaceId = await ensureWorkspaceId(ctx, workspaceRoot)

  // 点一条指令 = 在当前工作区里新开一个对话。不复用旧会话：
  // 指令是各自独立的任务，混在同一个对话里既难看也难翻。
  const created = await sessionController.create(
    // 有 workspaceId 就必须走它：`create` 不接受 workspaceId 和 cwd 同时给。
    workspaceId ? { workspaceId } : { cwd: workspaceRoot },
  )
  const sessionId = created?.sessionId
  if (!sessionId) throw new Error('没能在这个工作区里创建会话')

  const resolved = await sessionController.resolveAgent(sessionId)
  if (resolved && !resolved.agent) {
    throw new Error(`会话不可用：${String(resolved.error?.code ?? 'unknown')}`)
  }

  // 新开一轮：清掉上一轮的定位目标和残留文件，免得 AI 这轮没写就跳回老页面。
  // 必须等它删完再发指令，否则可能把 AI 刚写的那份一起删掉。
  sessionGoto.delete(String(sessionId))
  await clearGotoFile()

  await sessionController.prompt(
    {
      requestId: `smd-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`,
      sessionId,
      mode: 'queue',
      content: [{ type: 'text', text }],
      clientTimeZone: 'Asia/Shanghai',
    },
    signal,
  )
  void noteActivity({ kind: 'run', sessionId: String(sessionId), workspaceId })

  return {
    accepted: true,
    sessionId,
    workspaceId,
    cwd: workspaceRoot,
  }
}

/* --------------------------------------------------------------- 路由 */

function sendJson(res, status, body) {
  const text = JSON.stringify(body)
  res.writeHead(status, {
    'content-type': 'application/json; charset=utf-8',
    'cache-control': 'no-store',
    'content-length': Buffer.byteLength(text),
  })
  res.end(text)
}

async function readBody(req) {
  const chunks = []
  let size = 0
  for await (const chunk of req) {
    size += chunk.length
    if (size > 256 * 1024) throw new Error('请求体过大')
    chunks.push(chunk)
  }
  if (chunks.length === 0) return {}
  const raw = Buffer.concat(chunks).toString('utf8')
  return raw.trim() ? JSON.parse(raw) : {}
}

function jsonRoute(handler) {
  return async (req, res) => {
    try {
      sendJson(res, 200, await handler(req))
    } catch (error) {
      sendJson(res, 500, {
        ok: false,
        error: 'ds_route_failed',
        message: String(error?.message ?? error),
      })
    }
  }
}

function failurePage(message) {
  return `<!doctype html><meta charset="utf-8">
<body style="font:14px/1.7 -apple-system,'PingFang SC',sans-serif;padding:40px;color:#111">
<h2 style="margin:0 0 12px;font-size:18px">工作台没起来</h2>
<p style="margin:0 0 10px;color:#b3261e;word-break:break-all">${message}</p>
<p style="margin:0;color:#666">在 DS 工作台的设置里，把工作区指到装好工作台的那个目录。</p>
</body>`
}

/** @param {import('cordis').Context} ctx */
export function apply(ctx) {
  // 启动时就试着起一次，用户打开页面时不用再等；失败不阻塞宿主启动。
  void ensureRuntime().catch(() => {})

  /* ------------------------------------------------ 会话状态（推送） */
  //
  // ⚠️ 这里只能听 `agent/status`。`api-session/status` 是宿主**推给前端**的
  // remote 事件（宿主内部拿 `agent/status` 转出去的那条），插件在服务端
  // `ctx.on('api-session/status')` 收不到 —— 2026-11-12 踩了一整轮：
  // 订阅永远不触发，running 永远 false，前端「见过 true 才认 false」的
  // 判断永远不成立，于是「跑完自动回工作台」一次都没跳。
  function noteStatus(rawId, nowRunning) {
    const key = String(rawId ?? '')
    if (!key) return
    const before = sessionRunning.get(key)
    sessionRunning.set(key, nowRunning)
    void noteActivity({ kind: 'status', id: key, running: nowRunning, before: before ?? null })
    // 跑完的那一刻去收本轮的定位文件；还没见过 true 的不算「跑完」。
    if (nowRunning || before !== true) return
    void takeGotoSpec().then((spec) => {
      if (spec) sessionGoto.set(key, spec)
    })
  }

  ctx.on('agent/status', (payload) => {
    const agent = payload?.agent
    noteStatus(agent?.id ?? agent?.session?.id, payload?.status === 'running')
  })

  // 老宿主 / 别的入口万一真把这条推到了本地，也认。
  ctx.on('api-session/status', (sessionId, running) => {
    noteStatus(sessionId, Boolean(running))
  })

  // 每条 assistant 消息看一眼有没有 goto 标记；同一轮里以最后一条为准。
  ctx.on('session/event', (session, event) => {
    if (!event || event.type !== 'assistant/message') return
    const id = String(session?.id ?? session?.sessionId ?? '')
    if (!id) return
    const content = event.data?.message?.content
    if (!Array.isArray(content)) return
    const text = content
      .filter((part) => part && part.type === 'text' && typeof part.text === 'string')
      .map((part) => part.text)
      .join('\n')
    if (!text.includes('[[goto:')) return
    let spec = null
    const re = new RegExp(GOTO_RE.source, 'g')
    let match = re.exec(text)
    while (match) {
      spec = match[1].trim()
      match = re.exec(text)
    }
    if (spec) sessionGoto.set(id, spec)
  })

  /* ---------------------------------------------------- 工作台整站 */
  ctx.effect(() =>
    ctx.webServer.register({
      kind: 'prefix',
      path: APP_BASE,
      handler: async (req, res) => {
        const url = new URL(req.url ?? '/', 'http://127.0.0.1')
        // 少一个斜杠，相对路径就会解析到上一层，后面全都 404。
        if (url.pathname === APP_BASE) {
          res.writeHead(302, { location: `${APP_BASE}/${url.search}` })
          res.end()
          return
        }
        try {
          const instance = await ensureRuntime()
          proxyRequest(req, res, { port: instance.port, stripPrefix: APP_BASE })
        } catch (error) {
          const text = failurePage(String(error?.message ?? error))
          res.writeHead(503, {
            'content-type': 'text/html; charset=utf-8',
            'cache-control': 'no-store',
            'content-length': Buffer.byteLength(text),
          })
          res.end(text)
        }
      },
    }),
  )

  /* -------------------------------------------------- 前缀适配脚本 */
  ctx.effect(() =>
    ctx.webServer.register({
      kind: 'exact',
      path: BRIDGE_SCRIPT,
      handler: async (req, res) => {
        const buf = Buffer.from(BRIDGE_SOURCE, 'utf8')
        res.writeHead(200, {
          'content-type': 'text/javascript; charset=utf-8',
          'cache-control': 'no-cache',
          'content-length': buf.length,
        })
        res.end(buf)
      },
    }),
  )

  /* ---------------------------------------------- 工作台自己的顶栏资源
     内容在工作台模板里（`工作台/ds-shell.*`），这里只把文件吐出去。
     改顶栏不用动插件，换一份工作台就生效。 */
  const shellAssets = [
    { path: SHELL_SCRIPT, file: 'ds-shell.js', type: 'text/javascript; charset=utf-8' },
    { path: SHELL_STYLE, file: 'ds-shell.css', type: 'text/css; charset=utf-8' },
  ]
  for (const asset of shellAssets) {
    ctx.effect(() =>
      ctx.webServer.register({
        kind: 'exact',
        path: asset.path,
        handler: async (req, res) => {
          const buf = await readShellAsset(await resolveWorkspaceRoot(), asset.file)
          if (!buf) {
            // 工作台还没装/被换掉了：给个空响应，页面上就是没有顶栏，别报错
            res.writeHead(204, { 'cache-control': 'no-store' })
            res.end()
            return
          }
          res.writeHead(200, {
            'content-type': asset.type,
            'cache-control': 'no-cache',
            'content-length': buf.length,
          })
          res.end(buf)
        },
      }),
    )
  }

  /* --------------------------------------------------------- 工作区 */
  ctx.effect(() =>
    ctx.webServer.register({
      kind: 'exact',
      path: `${ROUTE_PREFIX}/workspaces`,
      handler: jsonRoute(async () => ({ ok: true, items: await discoverWorkspaces() })),
    }),
  )

  ctx.effect(() =>
    ctx.webServer.register({
      kind: 'exact',
      path: `${ROUTE_PREFIX}/workspace`,
      handler: async (req, res) => {
        try {
          if (req.method === 'POST') {
            const body = await readBody(req)
            const next = String(body?.workspaceRoot || '').trim()
            if (!next) throw new Error('请给一个目录路径')
            const probe = await probeWorkspace(next)
            if (!probe.ok) throw new Error(explainProbe(probe, next))
            await writeConfig({ workspaceRoot: next })
            await restartRuntime(next)
            sendJson(res, 200, { ok: true, workspaceRoot: next, port: runtime?.port ?? null })
            return
          }
          const workspaceRoot = await resolveWorkspaceRoot()
          const probe = await probeWorkspace(workspaceRoot)
          sendJson(res, 200, {
            ok: true,
            workspaceRoot,
            configPath: CONFIG_PATH,
            ready: probe.ok,
            running: Boolean(runtime),
            port: runtime?.port ?? null,
            error: lastError,
          })
        } catch (error) {
          sendJson(res, 500, { ok: false, message: String(error?.message ?? error) })
        }
      },
    }),
  )

  /* --------------------------------------------------- 系统选文件夹 */
  ctx.effect(() =>
    ctx.webServer.register({
      kind: 'exact',
      path: `${ROUTE_PREFIX}/pick-folder`,
      handler: jsonRoute(async () => {
        const picker = ctx.get('directoryPicker')
        if (!picker || typeof picker.capability !== 'function') {
          throw new Error('这个环境没有目录选择器，直接填路径吧')
        }
        const capability = picker.capability()
        if (capability.kind !== 'native') {
          throw new Error('当前是应用内浏览模式，直接填路径吧')
        }
        const picked = await capability.pick(new AbortController().signal)
        if (!picked) return { ok: false, cancelled: true }
        return { ok: true, workspaceRoot: String(picked) }
      }),
    }),
  )

  /* --------------------------------------------- 页面自检（诊断用） */
  ctx.effect(() =>
    ctx.webServer.register({
      kind: 'exact',
      path: `${ROUTE_PREFIX}/bridge-log`,
      handler: async (req, res) => {
        try {
          const body = await readBody(req)
          await fs.writeFile(
            '/tmp/ds-bridge-log.json',
            JSON.stringify({ ...body, receivedAt: new Date().toISOString() }, null, 2),
            'utf8',
          )
        } catch {
          /* 诊断写不进去不影响页面 */
        }
        sendJson(res, 200, { ok: true })
      },
    }),
  )

  /* --------------------------------------------------- 探一个目录 */
  ctx.effect(() =>
    ctx.webServer.register({
      kind: 'exact',
      path: `${ROUTE_PREFIX}/probe`,
      handler: jsonRoute(async (req) => {
        const url = new URL(req.url ?? '/', 'http://127.0.0.1')
        const target = String(url.searchParams.get('path') || '').trim()
        if (!target) throw new Error('缺 path')
        if (!path.isAbsolute(target)) throw new Error('请给绝对路径')
        const abs = path.resolve(target)
        const exists = await pathExists(abs)
        let entryCount = 0
        if (exists) {
          try {
            entryCount = (await fs.readdir(abs)).length
          } catch {
            entryCount = -1
          }
        }
        const hasWorkbench = await pathExists(path.join(abs, '工作台', 'server.mjs'))
        return {
          ok: true,
          path: abs,
          exists,
          empty: !exists || entryCount === 0,
          entryCount,
          hasWorkbench,
        }
      }),
    }),
  )

  /* ------------------------------------------------- 装一份工作台 */
  ctx.effect(() =>
    ctx.webServer.register({
      kind: 'exact',
      path: `${ROUTE_PREFIX}/install`,
      handler: jsonRoute(async (req) => {
        if (req.method !== 'POST') throw new Error('POST required')
        const body = await readBody(req)
        const target = String(body?.workspaceRoot || '').trim()
        if (!target) throw new Error('先选一个文件夹')
        if (!path.isAbsolute(target)) throw new Error('请给绝对路径')

        const result = await installWorkspace(target)
        await writeConfig({ workspaceRoot: result.workspaceRoot })
        await restartRuntime(result.workspaceRoot)
        return {
          ok: true,
          workspaceRoot: result.workspaceRoot,
          version: result.version ?? null,
          alreadyInstalled: Boolean(result.alreadyInstalled),
          running: Boolean(runtime),
        }
      }),
    }),
  )

  /* ---------------------------------------------------- 检查/安装更新 */
  ctx.effect(() =>
    ctx.webServer.register({
      kind: 'exact',
      path: `${ROUTE_PREFIX}/update/check`,
      handler: jsonRoute(async () => checkUpdate()),
    }),
  )

  ctx.effect(() =>
    ctx.webServer.register({
      kind: 'exact',
      path: `${ROUTE_PREFIX}/update/apply`,
      handler: jsonRoute(async (req) => {
        if (req.method !== 'POST') throw new Error('POST required')
        return applyUpdate()
      }),
    }),
  )

  /* -------------------------------------------------- 送指令进会话 */
  ctx.effect(() =>
    ctx.webServer.register({
      kind: 'exact',
      path: `${ROUTE_PREFIX}/run`,
      handler: jsonRoute(async (req) => {
        if (req.method !== 'POST') throw new Error('POST required')
        const body = await readBody(req)
        const text = String(body?.text || '').trim()
        if (!text) throw new Error('指令是空的')
        return sendToSession(ctx, text)
      }),
    }),
  )

  /* ------------------------------------------------------- 会话状态 */
  ctx.effect(() =>
    ctx.webServer.register({
      kind: 'exact',
      path: `${ROUTE_PREFIX}/activity`,
      handler: jsonRoute(async (req) => {
        // ⚠️ 会话 id 走 body，不走 query。宿主的 exact 路由是拿「不带 ?query 的路径」
        // 去匹配的，`/activity?sessionId=xxx` 会直接 404，压根进不到这里
        // （2026-11-12 踩到：日志里一条 activity 都没有，前端却每 2.5 秒在轮询）。
        let wanted = ''
        if (req.method === 'POST') {
          try {
            const body = await readBody(req)
            wanted = String(body?.sessionId || '')
          } catch {
            wanted = ''
          }
        }
        // 前端点名要哪个会话，就原样当成回答里的 sessionId，找不到也不能换人 ——
        // 换人前端会当成「会话变了」立刻收手，那就永远不跳了。
        let sessionId = wanted
        let running = false
        let goto = null
        let error = ''
        try {
          const sessionController = ctx.get('sessionController')
          if (sessionController) {
            const signal = new AbortController().signal
            const workspaceRoot = await resolveWorkspaceRoot()
            const target = await findWorkspaceSession(sessionController, signal, workspaceRoot, wanted)
            if (target.sessionId) sessionId = String(target.sessionId)
            const pushed = sessionRunning.get(String(sessionId))
            running = pushed === undefined ? Boolean(target.row?.running) : pushed
            goto = sessionGoto.get(String(sessionId)) ?? null
            // 这个接口只在前端「等这一轮跑完」的那段轮询里被调用，所以在这里读定位文件
            // 是安全的：文件出现就说明 AI 已经把这轮做完了，直接交给前端去跳。
            if (!running && !goto) {
              goto = await takeGotoSpec()
              if (goto) sessionGoto.set(String(sessionId), goto)
            }
          }
        } catch (e) {
          // 出错也要正常回一句话，否则前端拿到 500 就当成「会话变了」收手。
          error = String(e?.message ?? e)
        }
        void noteActivity({ wanted, sessionId, running, goto, error })
        return {
          available: true,
          sessionId,
          running,
          goto,
          mode: sessionId ? 'ready' : 'will-create',
          now: Date.now(),
          ...(error ? { error } : {}),
        }
      }),
    }),
  )

  /* ------------------------------------------------------- 插件状态 */
  ctx.effect(() =>
    ctx.webServer.register({
      kind: 'exact',
      path: `${ROUTE_PREFIX}/state`,
      handler: jsonRoute(async () => ({
        ok: true,
        running: Boolean(runtime),
        port: runtime?.port ?? null,
        error: lastError,
        appBase: APP_BASE,
      })),
    }),
  )

  // 宿主退出时把工作台实例关掉，别留一个没人管的端口。
  ctx.effect(() => () => {
    const instance = runtime
    runtime = null
    if (instance) void instance.close()
  })
}
