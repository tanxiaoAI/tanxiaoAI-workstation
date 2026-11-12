#!/usr/bin/env node

import crypto from "node:crypto";
import { closeSync, openSync, realpathSync } from "node:fs";
import fs from "node:fs/promises";
import path from "node:path";
import { spawn } from "node:child_process";
import { fileURLToPath } from "node:url";

import { createDashboardServer } from "./server.mjs";

const CURRENT_FILE = fileURLToPath(import.meta.url);
const WORKBENCH_ROOT = path.dirname(CURRENT_FILE);
const MAX_LOG_BYTES = 2 * 1024 * 1024;

function createError(code, message = code) {
  const error = new Error(message);
  error.code = code;
  return error;
}

// 后台服务是长期进程，**不能继承调用方（AI 客户端）的环境**。
//
// 背景：客户端（WorkBuddy / Claude Code / Trae / …）会给它拉起的命令注入「会话级」
// 沙箱 / 文件代理 / broker 环境变量。子进程一旦继承，文件读写会被劫持到该会话的
// 代理上；会话结束或代理回收后，后续读取全部 ECONNREFUSED —— 表现为
// 「进程在、端口在、页面打不开」，用户点工作台链接打不开。
//
// 为什么不按客户端枚举「要剥掉的变量名」：那是个无底洞。名字会随客户端改版变
// （2026-10-11 实测就撞上一次：旧名 `SANDBOX_CENTER_*` 早已不是现名，现在实际是
// `CODEBUDDY_SANDBOX_BROKER_*`、`TOOLHOST_*`、`TRAE_SANDBOX_*` …），漏一个就等于没防；
// 而且这套东西要交付给客户，我们根本不知道客户会用哪个客户端。
//
// 所以改用**白名单**：只把子进程真正需要的几个变量给它，其余一概不带。这样对任何
// 客户端、任何未来改版都成立，也不会把客户端的令牌 / 代理 / 沙箱配置泄进后台进程。
// ⚠️ 接入新客户端**不需要改这里**；只有工作台再次出现「进程在、端口在、页面打不开」，
// 才需要回来看这段。
const CHILD_ENV_ALLOWLIST = [
  // POSIX（macOS / Linux）
  "PATH",
  "HOME",
  "USER",
  "LOGNAME",
  "SHELL",
  "TMPDIR",
  "TMP",
  "TEMP",
  "LANG",
  "LC_ALL",
  "LC_CTYPE",
  "TZ",
  // Windows
  "SystemRoot",
  "windir",
  "ComSpec",
  "PATHEXT",
  "USERPROFILE",
  "APPDATA",
  "LOCALAPPDATA",
  "PROGRAMDATA",
  "NUMBER_OF_PROCESSORS",
  "PROCESSOR_ARCHITECTURE",
  "OS",
];

function detachedChildEnv(source = process.env) {
  const env = {};
  for (const key of CHILD_ENV_ALLOWLIST) {
    const value = source[key];
    if (typeof value === "string") env[key] = value;
  }
  return env;
}

// 路径身份归一：同一目录通过符号链接路径（如 macOS 的 /tmp -> /private/tmp）
// 访问时必须得到同一个结果，否则会被当成两个工作区，或让入口判断静默失效。
function canonicalPath(target) {
  const resolved = path.resolve(target);
  try {
    return realpathSync(resolved);
  } catch {
    return resolved;
  }
}

function isPermissionError(error) {
  const visited = new Set();
  let current = error;
  while (current && !visited.has(current)) {
    visited.add(current);
    if (current.code === "EPERM" || current.code === "EACCES") return true;
    const message = String(current.message || current).toLowerCase();
    if (
      message.includes("eperm") ||
      message.includes("eacces") ||
      message.includes("operation not permitted") ||
      message.includes("permission denied")
    ) {
      return true;
    }
    current = current.cause;
  }
  return false;
}

function processStartError(error) {
  return isPermissionError(error)
    ? createError("dashboard_process_permission_denied")
    : createError("dashboard_process_start_failed");
}

function parseArguments(argv) {
  const command = String(argv[0] || "start").trim().toLowerCase();
  if (!new Set(["start", "status", "stop", "serve"]).has(command)) {
    throw createError("invalid_dashboard_command");
  }
  const options = { command };
  for (let index = 1; index < argv.length; index += 1) {
    const flag = argv[index];
    if (!flag.startsWith("--")) throw createError("invalid_dashboard_argument");
    const value = argv[index + 1];
    if (!value || value.startsWith("--")) throw createError(`missing_value:${flag}`);
    index += 1;
    if (flag === "--workspace") options.workspace = value;
    else if (flag === "--runtime-file") options.runtimeFile = value;
    else if (flag === "--runtime-id") options.runtimeId = value;
    else if (flag === "--port") options.port = Number(value);
    else if (flag === "--reconcile-ms") options.reconcileIntervalMs = Number(value);
    else throw createError(`unknown_dashboard_argument:${flag}`);
  }
  return options;
}

async function ensureWorkspace(workspace) {
  if (!workspace) throw createError("workspace_required");
  const resolved = path.resolve(workspace);
  const stat = await fs.lstat(resolved).catch((error) => {
    if (error?.code === "ENOENT") throw createError("workspace_not_found");
    throw error;
  });
  if (!stat.isDirectory() || stat.isSymbolicLink()) throw createError("workspace_must_be_directory");
  return canonicalPath(resolved);
}

function runtimePaths(workspaceRoot, explicitRuntimeFile = "") {
  const runtimeDir = path.join(workspaceRoot, ".meiling", "dashboard");
  return {
    runtimeDir,
    runtimeFile: explicitRuntimeFile ? path.resolve(explicitRuntimeFile) : path.join(runtimeDir, "runtime.json"),
    startupFile: path.join(runtimeDir, "startup.json"),
    logFile: path.join(runtimeDir, "dashboard.log"),
    previousLogFile: path.join(runtimeDir, "dashboard.previous.log"),
  };
}

async function rotateLogIfNeeded(logFile, previousLogFile) {
  const stat = await fs.stat(logFile).catch((error) => {
    if (error?.code === "ENOENT") return null;
    throw error;
  });
  if (!stat || stat.size <= MAX_LOG_BYTES) return;
  await fs.rm(previousLogFile, { force: true });
  await fs.rename(logFile, previousLogFile);
}

async function readJson(filePath) {
  try {
    return JSON.parse(await fs.readFile(filePath, "utf8"));
  } catch (error) {
    if (error?.code === "ENOENT" || error instanceof SyntaxError) return null;
    throw error;
  }
}

async function writeJsonAtomically(filePath, value) {
  await fs.mkdir(path.dirname(filePath), { recursive: true });
  const temporaryPath = path.join(
    path.dirname(filePath),
    `.runtime-${process.pid}-${crypto.randomBytes(4).toString("hex")}.tmp`,
  );
  try {
    await fs.writeFile(temporaryPath, `${JSON.stringify(value, null, 2)}\n`, "utf8");
    await fs.rename(temporaryPath, filePath);
  } finally {
    await fs.rm(temporaryPath, { force: true }).catch(() => {});
  }
}

async function fetchHealth(url) {
  if (!/^http:\/\/127\.0\.0\.1:\d+$/.test(String(url || ""))) return null;
  try {
    const response = await fetch(`${url}/api/health`, { signal: AbortSignal.timeout(1_000) });
    if (!response.ok) return null;
    return await response.json();
  } catch (error) {
    const code = error?.code || error?.cause?.code;
    if (code === "EPERM" || code === "EACCES") {
      throw createError("dashboard_health_permission_denied");
    }
    return null;
  }
}

function startupErrorCode(error) {
  if (error?.code === "EPERM" || error?.code === "EACCES") {
    return "dashboard_listen_permission_denied";
  }
  if (error?.code === "EADDRINUSE") return "dashboard_port_in_use";
  return "dashboard_server_start_failed";
}

async function readStartupFailure(startupFile, runtimeId) {
  const state = await readJson(startupFile);
  if (state?.runtime_id !== runtimeId || typeof state.error !== "string") return null;
  return state.error;
}

function validRuntimeState(state, workspaceRoot) {
  return Boolean(
    state &&
      Number.isSafeInteger(state.pid) &&
      state.pid > 0 &&
      typeof state.url === "string" &&
      typeof state.runtime_id === "string" &&
      state.runtime_id &&
      path.resolve(String(state.workspace_root || "")) === workspaceRoot,
  );
}

async function inspectRuntime(workspaceRoot, runtimeFile) {
  const state = await readJson(runtimeFile);
  if (!validRuntimeState(state, workspaceRoot)) {
    return { running: false, stale: Boolean(state), state };
  }
  const health = await fetchHealth(state.url);
  const running = Boolean(
    health?.ok === true &&
      health.service === "meiling-local-dashboard" &&
      health.pid === state.pid &&
      health.runtime_id === state.runtime_id,
  );
  return { running, stale: !running, state, health };
}

function publicState(inspected, extra = {}) {
  if (!inspected.running) {
    return { ok: true, running: false, stale: inspected.stale, ...extra };
  }
  return {
    ok: true,
    running: true,
    stale: false,
    pid: inspected.state.pid,
    url: inspected.state.url,
    started_at: inspected.state.started_at,
    revision: inspected.health?.revision ?? null,
    workspace: inspected.health?.workspace ?? null,
    ...extra,
  };
}

async function waitForStarted(workspaceRoot, runtimeFile, startupFile, runtimeId, timeoutMs = 15_000) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    const inspected = await inspectRuntime(workspaceRoot, runtimeFile);
    if (inspected.running && inspected.state.runtime_id === runtimeId) return inspected;
    const startupFailure = await readStartupFailure(startupFile, runtimeId);
    if (startupFailure) throw createError(startupFailure);
    await new Promise((resolve) => setTimeout(resolve, 100));
  }
  throw createError("dashboard_start_timeout");
}

async function waitForStopped(state, timeoutMs = 5_000) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    if (!(await fetchHealth(state.url))) return true;
    await new Promise((resolve) => setTimeout(resolve, 100));
  }
  return false;
}

async function startCommand(options, workspaceRoot, paths, dependencies = {}) {
  const spawnImpl = dependencies.spawnImpl || spawn;
  const openLog = dependencies.openLog || openSync;
  const closeLog = dependencies.closeLog || closeSync;
  const current = await inspectRuntime(workspaceRoot, paths.runtimeFile);
  if (current.running) return publicState(current, { reused: true });
  if (current.stale) await fs.rm(paths.runtimeFile, { force: true });

  await fs.mkdir(paths.runtimeDir, { recursive: true });
  await fs.rm(paths.startupFile, { force: true });
  await rotateLogIfNeeded(paths.logFile, paths.previousLogFile);
  const runtimeId = crypto.randomBytes(18).toString("hex");
  const childArguments = [
    CURRENT_FILE,
    "serve",
    "--workspace",
    workspaceRoot,
    "--runtime-file",
    paths.runtimeFile,
    "--runtime-id",
    runtimeId,
  ];
  if (Number.isSafeInteger(options.port) && options.port >= 0) {
    childArguments.push("--port", String(options.port));
  }
  if (Number.isSafeInteger(options.reconcileIntervalMs) && options.reconcileIntervalMs >= 250) {
    childArguments.push("--reconcile-ms", String(options.reconcileIntervalMs));
  }

  let logFd;
  try {
    logFd = openLog(paths.logFile, "a");
  } catch (error) {
    if (isPermissionError(error)) throw createError("dashboard_filesystem_permission_denied");
    throw error;
  }
  let child;
  try {
    try {
      child = spawnImpl(process.execPath, childArguments, {
        cwd: workspaceRoot,
        detached: true,
        windowsHide: true,
        env: detachedChildEnv(),
        stdio: ["ignore", logFd, logFd],
      });
    } catch (error) {
      throw processStartError(error);
    }
    await new Promise((resolve, reject) => {
      child.once("spawn", resolve);
      child.once("error", (error) => reject(processStartError(error)));
    });
    child.unref();
  } finally {
    closeLog(logFd);
  }

  const started = await waitForStarted(workspaceRoot, paths.runtimeFile, paths.startupFile, runtimeId);
  await fs.rm(paths.startupFile, { force: true });
  return publicState(started, { reused: false });
}

async function statusCommand(workspaceRoot, paths) {
  return publicState(await inspectRuntime(workspaceRoot, paths.runtimeFile));
}

async function stopCommand(workspaceRoot, paths) {
  const inspected = await inspectRuntime(workspaceRoot, paths.runtimeFile);
  if (!inspected.running) {
    if (inspected.stale) await fs.rm(paths.runtimeFile, { force: true });
    return publicState({ running: false, stale: false }, { stopped: false });
  }
  try {
    process.kill(inspected.state.pid, "SIGTERM");
  } catch (error) {
    if (isPermissionError(error)) throw createError("dashboard_process_permission_denied");
    throw error;
  }
  const stopped = await waitForStopped(inspected.state);
  if (!stopped) throw createError("dashboard_stop_timeout");
  await fs.rm(paths.runtimeFile, { force: true });
  return { ok: true, running: false, stale: false, stopped: true };
}

async function serveCommand(options, workspaceRoot, paths) {
  if (!options.runtimeId) throw createError("runtime_id_required");
  const dashboard = createDashboardServer({
    workspaceRoot,
    publicDir: WORKBENCH_ROOT,
    port: Number.isSafeInteger(options.port) ? options.port : 0,
    runtimeId: options.runtimeId,
    reconcileIntervalMs:
      Number.isSafeInteger(options.reconcileIntervalMs) && options.reconcileIntervalMs >= 250
        ? options.reconcileIntervalMs
        : 30_000,
  });
  try {
    await dashboard.start();
  } catch (error) {
    await writeJsonAtomically(paths.startupFile, {
      runtime_id: options.runtimeId,
      error: startupErrorCode(error),
    });
    throw createError(startupErrorCode(error));
  }
  const state = {
    schema_version: "1",
    pid: process.pid,
    url: dashboard.url,
    workspace_root: workspaceRoot,
    runtime_id: options.runtimeId,
    started_at: new Date().toISOString(),
  };
  await writeJsonAtomically(paths.runtimeFile, state);
  await fs.rm(paths.startupFile, { force: true });

  let stopping = false;
  const stop = async () => {
    if (stopping) return;
    stopping = true;
    await dashboard.close().catch(() => {});
    const current = await readJson(paths.runtimeFile);
    if (current?.runtime_id === options.runtimeId && current?.pid === process.pid) {
      await fs.rm(paths.runtimeFile, { force: true });
    }
  };
  process.once("SIGTERM", () => stop().finally(() => process.exit(0)));
  process.once("SIGINT", () => stop().finally(() => process.exit(0)));
  process.once("uncaughtException", (error) => {
    process.stderr.write(`${error.stack || error.message}\n`);
    stop().finally(() => process.exit(1));
  });
  process.once("unhandledRejection", (error) => {
    process.stderr.write(`${error?.stack || error}\n`);
    stop().finally(() => process.exit(1));
  });
  await new Promise(() => {});
}

export async function runDashboardLauncher(argv = process.argv.slice(2), dependencies = {}) {
  const options = parseArguments(argv);
  const workspaceRoot = await ensureWorkspace(options.workspace);
  const paths = runtimePaths(workspaceRoot, options.runtimeFile);
  if (options.command === "start") return startCommand(options, workspaceRoot, paths, dependencies);
  if (options.command === "status") return statusCommand(workspaceRoot, paths);
  if (options.command === "stop") return stopCommand(workspaceRoot, paths);
  return serveCommand(options, workspaceRoot, paths);
}

if (canonicalPath(process.argv[1] || "") === canonicalPath(CURRENT_FILE)) {
  runDashboardLauncher()
    .then((result) => {
      if (result) process.stdout.write(`${JSON.stringify(result)}\n`);
    })
    .catch((error) => {
      process.stderr.write(`${JSON.stringify({ ok: false, error: error.code || "dashboard_failed" })}\n`);
      process.exit(1);
    });
}
