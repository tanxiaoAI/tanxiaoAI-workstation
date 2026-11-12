import { spawn } from "node:child_process";
import { createReadStream, existsSync, watch as watchFs } from "node:fs";
import fs from "node:fs/promises";
import http from "node:http";
import path from "node:path";
import { fileURLToPath } from "node:url";

import {
  BROWSE_SECTIONS,
  WorkspaceIndex,
  resolveAllowedWorkspacePath,
} from "./workspace-index.mjs";

const RUNTIME_DIR = path.dirname(fileURLToPath(import.meta.url));
const DEFAULT_PUBLIC_DIR = path.join(RUNTIME_DIR, "public");
const LOOPBACK_HOSTS = new Set(["127.0.0.1", "::1", "localhost"]);
const TEXT_EXTENSIONS = new Set([".md", ".csv", ".json", ".txt"]);
const RAW_CONTENT_TYPES = new Map([
  [".png", "image/png"],
  [".jpg", "image/jpeg"],
  [".jpeg", "image/jpeg"],
  [".webp", "image/webp"],
  [".gif", "image/gif"],
  [".pdf", "application/pdf"],
  [".mp4", "video/mp4"],
  [".mov", "video/quicktime"],
  [".m4v", "video/x-m4v"],
  [".mp3", "audio/mpeg"],
  [".m4a", "audio/mp4"],
  [".wav", "audio/wav"],
]);
const PUBLIC_ASSETS = new Map([
  ["/", ["index.html", "text/html; charset=utf-8"]],
  ["/index.html", ["index.html", "text/html; charset=utf-8"]],
  // 调查问卷：只从上手引导里给链接，不进左侧导航。
  ["/survey", ["survey.html", "text/html; charset=utf-8"]],
  ["/survey.html", ["survey.html", "text/html; charset=utf-8"]],
  ["/survey.js", ["survey.js", "text/javascript; charset=utf-8"]],
  ["/survey.css", ["survey.css", "text/css; charset=utf-8"]],
  ["/survey-core.js", ["survey-core.js", "text/javascript; charset=utf-8"]],
  ["/survey-core.css", ["survey-core.css", "text/css; charset=utf-8"]],
  ["/app.js", ["app.js", "text/javascript; charset=utf-8"]],
  ["/planning.js", ["planning.js", "text/javascript; charset=utf-8"]],
  ["/styles.css", ["styles.css", "text/css; charset=utf-8"]],
  ["/theme-aurora.css", ["theme-aurora.css", "text/css; charset=utf-8"]],
  ["/theme-2.css", ["theme-2.css", "text/css; charset=utf-8"]],
  ["/tokens.css", ["tokens.css", "text/css; charset=utf-8"]],
  ["/content", ["content.html", "text/html; charset=utf-8"]],
  ["/content.html", ["content.html", "text/html; charset=utf-8"]],
  ["/content.js", ["content.js", "text/javascript; charset=utf-8"]],
  ["/content-embed.js", ["content-embed.js", "text/javascript; charset=utf-8"]],
  ["/content.css", ["content.css", "text/css; charset=utf-8"]],
  // 独立页（账号详情 / 选题分析）共用的内容详情抽屉；漏登记 → 这两个页面的卡片点了还是跳页
  ["/content-drawer.js", ["content-drawer.js", "text/javascript; charset=utf-8"]],
  ["/content-drawer.css", ["content-drawer.css", "text/css; charset=utf-8"]],
  ["/topic", ["topic.html", "text/html; charset=utf-8"]],
  ["/topic.html", ["topic.html", "text/html; charset=utf-8"]],
  ["/topic.js", ["topic.js", "text/javascript; charset=utf-8"]],
  ["/topic.css", ["topic.css", "text/css; charset=utf-8"]],
  ["/topic-library.css", ["topic-library.css", "text/css; charset=utf-8"]],
  ["/topic-search.css", ["topic-search.css", "text/css; charset=utf-8"]],
  ["/account", ["account-v2.html", "text/html; charset=utf-8"]],
  ["/account.html", ["account-v2.html", "text/html; charset=utf-8"]],
  ["/account.js", ["account.js", "text/javascript; charset=utf-8"]],
  ["/account.css", ["account.css", "text/css; charset=utf-8"]],
  ["/account-v2", ["account-v2.html", "text/html; charset=utf-8"]],
  ["/account-v2.html", ["account-v2.html", "text/html; charset=utf-8"]],
  ["/account-v2.js", ["account-v2.js", "text/javascript; charset=utf-8"]],
  ["/nav-boot.js", ["nav-boot.js", "text/javascript; charset=utf-8"]],
  ["/wechat-qr.png", ["wechat-qr.png", "image/png"]],
  ["/modal-creation-board.webp", ["modal-creation-board.webp", "image/webp"]],
  ["/markdown.js", ["markdown.js", "text/javascript; charset=utf-8"]],
  ["/markdown.css", ["markdown.css", "text/css; charset=utf-8"]],
  ["/LOGO.png", ["LOGO.png", "image/png"]],
  ["/logo-mark.png", ["logo-mark.png", "image/png"]],
  // 平台图标：必须与 app.js / account.js / content.js 里的 PLATFORM_ICON_FILES 一一对应。
  // 漏登记 → 请求落到末尾的 404 分支 → 页面上抖音/小红书/B 站图标变成裂图。
  ["/platform-douyin.png", ["platform-douyin.png", "image/png"]],
  ["/platform-xiaohongshu.png", ["platform-xiaohongshu.png", "image/png"]],
  ["/platform-bilibili.png", ["platform-bilibili.png", "image/png"]],
]);
const MAX_API_TEXT_BYTES = 2 * 1024 * 1024;
// 设置页只改这几项；其余字段（更新凭据等）原样留在文件里。
const LOCAL_CONFIG_FILE = "本地配置.txt";
const LOCAL_CONFIG_FIELDS = Object.freeze({
  data_source_preference: "DATA_SOURCE_PREFERENCE",
  benchmark_account_base_limit: "BENCHMARK_ACCOUNT_BASE_LIMIT",
  benchmark_account_detail_limit: "BENCHMARK_ACCOUNT_DETAIL_LIMIT",
  self_account_base_limit: "SELF_ACCOUNT_BASE_LIMIT",
  self_account_detail_limit: "SELF_ACCOUNT_DETAIL_LIMIT",
  volcengine_speech_api_key: "VOLCENGINE_SPEECH_API_KEY",
  justoneapi_token: "JUSTONEAPI_TOKEN",
});
// 取数方式首选：空 = 自动（有 JustOneAPI 也默认本机 Chrome），或明确指定其一。
const DATA_SOURCE_PREFERENCE_VALUES = new Set(["", "chrome_devtools", "justoneapi"]);
// 数量类配置：设置页和本地配置.txt 共用，留空时仍按默认值执行。
const LOCAL_CONFIG_NUMBER_LIMITS = Object.freeze({
  benchmark_account_base_limit: [1, 2000],
  benchmark_account_detail_limit: [1, 500],
  self_account_base_limit: [1, 2000],
  self_account_detail_limit: [1, 500],
});
const LOCAL_CONFIG_DEFAULTS = Object.freeze({
  benchmark_account_base_limit: "200",
  benchmark_account_detail_limit: "20",
  self_account_base_limit: "200",
  self_account_detail_limit: "20",
});

function createError(code, statusCode, message = code) {
  const error = new Error(message);
  error.code = code;
  error.statusCode = statusCode;
  return error;
}

function baseHeaders(contentType) {
  return {
    "Cache-Control": "no-store",
    "Content-Type": contentType,
    "Cross-Origin-Opener-Policy": "same-origin",
    "Referrer-Policy": "no-referrer",
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
  };
}

function sendJson(response, statusCode, payload) {
  const body = `${JSON.stringify(payload)}\n`;
  response.writeHead(statusCode, {
    ...baseHeaders("application/json; charset=utf-8"),
    "Content-Length": Buffer.byteLength(body),
    "Content-Security-Policy": "default-src 'none'; frame-ancestors 'none'",
  });
  response.end(body);
}

/* ---------- 本地配置.txt 读写 ----------
   格式就是 Python 执行器在读的 `KEY=值`，注释和空行原样保留。 */

const parseLocalConfig = (text) => {
  const values = {};
  for (const line of text.split(/\r?\n/)) {
    const match = /^\s*([A-Za-z0-9_]+)\s*=(.*)$/.exec(line);
    if (!match) continue;
    values[match[1]] = match[2].trim();
  }
  return values;
};

const normalizeConfigValue = (value) => {
  if (typeof value !== "string") return null;
  const trimmed = value.trim();
  if (trimmed.length > 256) return null;
  if (/[\u0000-\u001f\u007f]/.test(trimmed)) return null;
  return trimmed;
};

const updateLocalConfigText = (text, updates) => {
  const newline = text.includes("\r\n") ? "\r\n" : "\n";
  const lines = text ? text.split(/\r?\n/) : [];
  const seen = new Set();
  const nextLines = lines.map((line) => {
    const match = /^(\s*)([A-Za-z0-9_]+)(\s*)=/.exec(line);
    if (!match) return line;
    const [, indent, key, spacing] = match;
    if (!(key in updates)) return line;
    seen.add(key);
    return `${indent}${key}${spacing}=${updates[key]}`;
  });
  const appended = Object.entries(updates)
    .filter(([key]) => !seen.has(key))
    .map(([key, value]) => `${key}=${value}`);
  if (!appended.length) return nextLines.join(newline);

  const baseLines = text
    ? nextLines
    : ["# 本地私密配置", "# 只在等号后填写密钥，不要把真实密钥发到 AI 对话或公开位置。", ""];
  if (baseLines[baseLines.length - 1] !== "") baseLines.push("");
  baseLines.push(...appended);
  baseLines.push("");
  return baseLines.join(newline);
};

function clientSnapshot(snapshot) {
  if (!snapshot) return null;
  const {
    files: _files,
    recent_files: _recentFiles,
    sections: _sections,
    skills: _skills,
    fingerprint: _fingerprint,
    ...clientData
  } = snapshot;
  return clientData;
}

/**
 * DS 版专用：AI 在这一轮最后一步会把「跑完该看哪一页」写进
 * `.meiling/dashboard/goto.json`，页面每隔几秒来取一次，取走即删。
 *
 * 走工作台自己的后端，不走插件那个 `/selfmedia-ds/api/*`：页面和插件之间
 * 隔着宿主的路由与鉴权，实测调不通（2026-11-12 踩了一整晚）。
 */
async function takeDsGoto(workspaceRoot) {
  const file = path.join(workspaceRoot, ".meiling", "dashboard", "goto.json");
  try {
    const stat = await fs.stat(file);
    if (Date.now() - stat.mtimeMs > 30 * 60 * 1000) {
      await fs.rm(file, { force: true });
      return { ok: true, spec: null };
    }
    const raw = (await fs.readFile(file, "utf8")).trim();
    await fs.rm(file, { force: true });
    if (!raw) return { ok: true, spec: null };
    let spec = raw;
    if (raw.startsWith("{")) {
      try {
        spec = String(JSON.parse(raw)?.spec ?? "");
      } catch {
        spec = "";
      }
    }
    spec = spec.replace(/^\[\[goto:/, "").replace(/\]\]$/, "").trim();
    return { ok: true, spec: spec || null };
  } catch {
    return { ok: true, spec: null };
  }
}

/**
 * 记一下「页面最近一次来取定位」的时间，用来判断页面到底有没有在轮询。
 * 只覆盖写一个几百字节的小文件，不会长大。
 */
async function noteGotoPoll(workspaceRoot, spec) {
  try {
    const file = path.join(workspaceRoot, ".meiling", "dashboard", "goto-poll.json");
    await fs.writeFile(file, JSON.stringify({ at: new Date().toISOString(), spec: spec || null }));
  } catch {
    /* 记不上就算了 */
  }
}

function publicContentSecurityPolicy() {
  return [
    "default-src 'self'",
    "base-uri 'none'",
    "connect-src 'self'",
    "font-src 'self' data:",
    "form-action 'none'",
    "frame-ancestors 'self'",
    "img-src 'self' data: blob:",
    "object-src 'none'",
    "script-src 'self'",
    "style-src 'self'",
  ].join("; ");
}

function mapPathError(error) {
  if (error?.statusCode) return error;
  if (error?.code === "invalid_relative_path") return createError(error.code, 400);
  if (
    error?.code === "path_not_browsable" ||
    error?.code === "path_not_visible" ||
    error?.code === "symlink_forbidden"
  ) {
    return createError(error.code, 403);
  }
  if (error?.code === "path_not_found" || error?.code === "ENOENT") {
    return createError("path_not_found", 404);
  }
  return error;
}

async function listDirectories(root) {
  const result = [];
  const visit = async (current) => {
    result.push(current);
    let entries;
    try {
      entries = await fs.readdir(current, { withFileTypes: true });
    } catch (error) {
      if (error?.code === "ENOENT") return;
      throw error;
    }
    for (const entry of entries) {
      if (entry.isDirectory() && !entry.isSymbolicLink()) await visit(path.join(current, entry.name));
    }
  };
  await visit(root);
  return result;
}

export function createDashboardServer({
  workspaceRoot,
  publicDir = DEFAULT_PUBLIC_DIR,
  host = "127.0.0.1",
  port = 0,
  runtimeId = "",
  debounceMs = 300,
  reconcileIntervalMs = 30_000,
  index = null,
} = {}) {
  const resolvedWorkspaceRoot = path.resolve(String(workspaceRoot || ""));
  const resolvedPublicDir = path.resolve(String(publicDir || DEFAULT_PUBLIC_DIR));
  const indexer = index || new WorkspaceIndex({ workspaceRoot: resolvedWorkspaceRoot });
  const sseClients = new Set();
  // 浏览器对同一个地址最多只让连 6 条（HTTP/1.1），而事件流是长期连接：
  // 开够 6 个工作台标签页就会把连接池占满，之后连页面本身都请求不到，直接白屏。
  // 这里主动设上限，超出的页面收一条 busy 后改用「手动刷新」，不再抢连接。
  const MAX_SSE_CLIENTS = 4;
  const watchers = new Set();
  let server = null;
  let snapshot = null;
  let revision = 0;
  let refreshPromise = null;
  let debounceTimer = null;
  let reconcileTimer = null;
  let heartbeatTimer = null;
  let startedAt = "";
  let closing = false;

  // 引导漏斗埋点：问卷那两层只能由工作台代报 —— 前端算不出机器码，
  // 机器码算法在 `执行单元/使用统计上报.py` 里（必须和客户端其它上报逐字一致，
  // 换一份实现服务端就会把同一台机器算成两个人）。
  //
  // 三条硬要求：
  //   1. 静默 —— 失败当作没发生，绝不能连累问卷提交或页面加载；
  //   2. 不阻塞响应 —— `detached` + `unref()`，起个进程就走；
  //   3. 一个进程内每层只报一次 —— 工作台会被反复打开，重复报只会把事件流撑大
  //      （漏斗按机器码去重，数值本来不受影响）。
  const reportedUsageSteps = new Set();
  const reportUsageStep = (step) => {
    if (!step || reportedUsageSteps.has(step)) return;
    reportedUsageSteps.add(step);
    const script = path.join(resolvedWorkspaceRoot, "执行单元", "使用统计上报.py");
    // 老版本工作区里没有这个脚本（或者用户删了）—— 直接跳过，不要报错。
    if (!existsSync(script)) return;
    const args = [
      script,
      "--kind", "step",
      "--name", "上手引导流程",
      "--step", step,
      "--status", "ok",
    ];
    // Windows 上一般没有 `python3`，是 `python` / `py`；其它平台反过来。
    // 按顺序试，第一个真的起来的就停手（ENOENT 会触发 error 事件，换下一个）。
    const candidates = process.platform === "win32" ? ["python", "py"] : ["python3", "python"];
    let index = 0;
    const attempt = () => {
      const bin = candidates[index++];
      if (!bin) return;
      try {
        const child = spawn(bin, args, {
          cwd: resolvedWorkspaceRoot,
          detached: true,
          stdio: "ignore",
        });
        child.on("error", attempt);
        child.on("spawn", () => child.unref());
      } catch (_error) {
        // 起不来就算了，上报是副产品。
      }
    };
    attempt();
  };

  const broadcast = (eventName, payload) => {
    const message = `event: ${eventName}\ndata: ${JSON.stringify(payload)}\n\n`;
    for (const response of sseClients) {
      try {
        response.write(message);
      } catch (_error) {
        sseClients.delete(response);
      }
    }
  };

  const closeWatchers = () => {
    for (const watcher of watchers) watcher.close();
    watchers.clear();
  };

  const scheduleRefresh = () => {
    if (closing) return;
    clearTimeout(debounceTimer);
    debounceTimer = setTimeout(() => {
      refreshInBackground("watch");
    }, debounceMs);
    debounceTimer.unref?.();
  };

  const installWatchers = async () => {
    closeWatchers();
    for (const section of BROWSE_SECTIONS) {
      const root = path.join(resolvedWorkspaceRoot, section.root);
      try {
        const recursiveWatcher = watchFs(root, { recursive: true }, scheduleRefresh);
        recursiveWatcher.on("error", scheduleRefresh);
        watchers.add(recursiveWatcher);
        continue;
      } catch (error) {
        if (error?.code === "ENOENT") continue;
        if (error?.code !== "ERR_FEATURE_UNAVAILABLE_ON_PLATFORM") throw error;
      }

      for (const directory of await listDirectories(root)) {
        try {
          const watcher = watchFs(directory, scheduleRefresh);
          watcher.on("error", scheduleRefresh);
          watchers.add(watcher);
        } catch (error) {
          if (error?.code !== "ENOENT") throw error;
        }
      }
    }
  };

  const refresh = async (source = "manual") => {
    if (refreshPromise) return refreshPromise;
    refreshPromise = (async () => {
      const next = await indexer.build();
      if (!snapshot || next.fingerprint !== snapshot.fingerprint) {
        revision += 1;
        snapshot = { ...next, revision };
        if (revision > 1) broadcast("snapshot.updated", { revision, source });
      }
      return snapshot;
    })().finally(() => {
      refreshPromise = null;
    });
    return refreshPromise;
  };

  const refreshInBackground = (source) => {
    refresh(source).catch((error) => {
      broadcast("snapshot.failed", {
        source,
        error: error?.code || "dashboard_refresh_failed",
      });
    });
  };

  const servePublicAsset = async (pathname, response) => {
    const asset = PUBLIC_ASSETS.get(pathname);
    if (!asset) return false;
    const [fileName, contentType] = asset;
    let content;
    try {
      content = await fs.readFile(path.join(resolvedPublicDir, fileName));
    } catch (error) {
      if (error?.code === "ENOENT") throw createError("public_asset_missing", 500);
      throw error;
    }
    response.writeHead(200, {
      ...baseHeaders(contentType),
      "Cache-Control": "no-store",
      "Content-Length": content.length,
      "Content-Security-Policy": publicContentSecurityPolicy(),
    });
    response.end(content);
    return true;
  };

  const serveTextFile = async (url, response) => {
    const relativePath = url.searchParams.get("path") || "";
    let absolutePath;
    try {
      absolutePath = await resolveAllowedWorkspacePath(resolvedWorkspaceRoot, relativePath);
    } catch (error) {
      throw mapPathError(error);
    }
    const extension = path.extname(absolutePath).toLowerCase();
    if (!TEXT_EXTENSIONS.has(extension)) throw createError("text_file_type_forbidden", 403);
    const stat = await fs.lstat(absolutePath);
    if (!stat.isFile() || stat.isSymbolicLink()) throw createError("file_not_readable", 403);
    if (stat.size > MAX_API_TEXT_BYTES) throw createError("text_file_too_large", 413);
    const content = await fs.readFile(absolutePath, "utf8");
    sendJson(response, 200, {
      path: relativePath,
      name: path.basename(absolutePath),
      extension: extension.slice(1),
      size: stat.size,
      modified_at: stat.mtime.toISOString(),
      content: content.charCodeAt(0) === 0xfeff ? content.slice(1) : content,
    });
  };

  const serveRawFile = async (url, response) => {
    const relativePath = url.searchParams.get("path") || "";
    let absolutePath;
    try {
      absolutePath = await resolveAllowedWorkspacePath(resolvedWorkspaceRoot, relativePath);
    } catch (error) {
      throw mapPathError(error);
    }
    const extension = path.extname(absolutePath).toLowerCase();
    const contentType = RAW_CONTENT_TYPES.get(extension);
    if (!contentType) throw createError("raw_file_type_forbidden", 403);
    const stat = await fs.lstat(absolutePath);
    if (!stat.isFile() || stat.isSymbolicLink()) throw createError("file_not_readable", 403);
    const headers = {
      ...baseHeaders(contentType),
      "Content-Length": stat.size,
      "Content-Disposition": "inline",
      "Accept-Ranges": "none",
    };
    headers["Content-Security-Policy"] = "default-src 'none'; frame-ancestors 'none'; sandbox";
    response.writeHead(200, headers);
    createReadStream(absolutePath).pipe(response);
  };

  /* ================================================================
     调查问卷落盘
     · 只接受固定 7 题的答案，写入 我方资料/调查问卷.md
     · 服务只监听回环地址，写盘位置固定在工作区内
     ================================================================ */

  const SURVEY_ANSWER_IDS = ["q1", "q2", "q3", "q4", "q5", "q6", "q7"];
  const SURVEY_TARGET = "我方资料/调查问卷.md";

  const readRequestBody = async (request, limitBytes) => {
    const chunks = [];
    let size = 0;
    for await (const chunk of request) {
      size += chunk.length;
      if (size > limitBytes) throw createError("payload_too_large", 413);
      chunks.push(chunk);
    }
    return Buffer.concat(chunks).toString("utf8");
  };

  /* ================================================================
     设置页 · 本机接口配置
     · 读写工作区根目录的 本地配置.txt（Python 执行器读的就是它）
     · 返回值只带「是否已配置」和打码预览，不回传明文密钥
     ================================================================ */

  const localConfigPath = path.join(resolvedWorkspaceRoot, LOCAL_CONFIG_FILE);

  const readLocalConfigText = async () => {
    try {
      return await fs.readFile(localConfigPath, "utf8");
    } catch (error) {
      if (error?.code === "ENOENT") return "";
      throw error;
    }
  };

  const maskConfigValue = (value) => {
    if (!value) return "";
    if (value.length <= 10) return "****";
    return `${value.slice(0, 4)}****${value.slice(-4)}`;
  };

  const describeLocalConfig = (text) => {
    const values = parseLocalConfig(text);
    return {
      ok: true,
      file: LOCAL_CONFIG_FILE,
      values: Object.fromEntries(
        Object.entries(LOCAL_CONFIG_FIELDS).map(([field, envKey]) => [
          field,
          values[envKey] || LOCAL_CONFIG_DEFAULTS[field] || "",
        ]),
      ),
      readonly: {},
    };
  };

  const saveLocalConfig = async (request, response) => {
    const contentType = String(request.headers["content-type"] || "").toLowerCase();
    if (!contentType.startsWith("application/json")) throw createError("unsupported_media_type", 415);
    let payload;
    try {
      payload = JSON.parse(await readRequestBody(request, 8 * 1024));
    } catch (error) {
      if (error?.statusCode) throw error;
      throw createError("invalid_json", 400);
    }
    if (!payload || typeof payload !== "object" || Array.isArray(payload)) {
      throw createError("invalid_payload", 400);
    }

    const updates = {};
    for (const [field, envKey] of Object.entries(LOCAL_CONFIG_FIELDS)) {
      if (!(field in payload)) continue;
      const value = normalizeConfigValue(payload[field]);
      if (value === null) throw createError("invalid_payload", 400);
      if (field === "data_source_preference" && !DATA_SOURCE_PREFERENCE_VALUES.has(value)) {
        throw createError("invalid_payload", 400);
      }
      const numberLimit = LOCAL_CONFIG_NUMBER_LIMITS[field];
      if (numberLimit) {
        const effective = value === "" ? LOCAL_CONFIG_DEFAULTS[field] : value;
        if (!/^\d+$/.test(effective)) throw createError("invalid_payload", 400);
        const number = Number(effective);
        if (number < numberLimit[0] || number > numberLimit[1]) {
          throw createError("invalid_payload", 400);
        }
        updates[envKey] = String(number);
        continue;
      }
      updates[envKey] = value;
    }
    if (!Object.keys(updates).length) throw createError("invalid_payload", 400);

    const nextText = updateLocalConfigText(await readLocalConfigText(), updates);
    const temporaryPath = `${localConfigPath}.${process.pid}.tmp`;
    await fs.writeFile(temporaryPath, nextText, "utf8");
    await fs.rename(temporaryPath, localConfigPath);
    sendJson(response, 200, describeLocalConfig(nextText));
  };

  const isSurveyPayloadValid = (payload) => {
    if (!payload || typeof payload !== "object" || Array.isArray(payload)) return false;
    const answers = payload.answers;
    if (!Array.isArray(answers) || answers.length !== SURVEY_ANSWER_IDS.length) return false;
    const seen = new Set();
    for (const answer of answers) {
      if (!answer || typeof answer !== "object" || Array.isArray(answer)) return false;
      if (!SURVEY_ANSWER_IDS.includes(answer.id) || seen.has(answer.id)) return false;
      seen.add(answer.id);
      if (typeof answer.question !== "string" || !answer.question.trim()) return false;
      if ([...answer.question].length > 120) return false;
      if (!Array.isArray(answer.values) || answer.values.length === 0 || answer.values.length > 5) return false;
      if (answer.values.some((value) => typeof value !== "string" || !value.trim() || [...value].length > 160)) return false;
      if (answer.extra !== undefined) {
        if (typeof answer.extra !== "string") return false;
        if ([...answer.extra].length > 200) return false;
      }
    }
    if (payload.supplement !== undefined) {
      if (typeof payload.supplement !== "string") return false;
      if ([...payload.supplement].length > 200) return false;
    }
    return true;
  };

  const singleLine = (value) => String(value ?? "").replace(/[\r\n]+/g, " ").trim();

  const buildSurveyMarkdown = (payload) => {
    const submittedAt = new Date().toLocaleString("zh-CN", { hour12: false });
    const lines = ["# 调查问卷", "", `提交时间：${submittedAt}`];
    const first = payload.answers.find((answer) => answer.id === "q1");
    const field = first?.values?.[0];
    if (field) lines.push(`内容领域：${singleLine(field === "其他" && first.extra ? first.extra : field)}`);
    lines.push("");
    payload.answers.forEach((answer, index) => {
      lines.push(`## ${index + 1}. ${singleLine(answer.question)}`, "");
      for (const value of answer.values) lines.push(`- ${singleLine(value)}`);
      if (answer.extra && String(answer.extra).trim()) lines.push(`- 补充：${singleLine(answer.extra)}`);
      lines.push("");
    });
    lines.push("## 8. 还有什么需要让我们知道？", "");
    const supplement = String(payload.supplement || "").trim();
    lines.push(supplement || "（未填写）", "");
    lines.push("> 本文件由调查问卷页面生成；用户重新提交问卷时会覆盖这里的内容。", "");
    return lines.join("\n");
  };

  const saveOnboardingSurvey = async (request, response) => {
    const contentType = String(request.headers["content-type"] || "").toLowerCase();
    if (!contentType.startsWith("application/json")) throw createError("unsupported_media_type", 415);
    let payload;
    try {
      payload = JSON.parse(await readRequestBody(request, 64 * 1024));
    } catch (error) {
      if (error?.statusCode) throw error;
      throw createError("invalid_json", 400);
    }
    if (!isSurveyPayloadValid(payload)) throw createError("invalid_payload", 400);
    const targetPath = path.join(resolvedWorkspaceRoot, SURVEY_TARGET);
    await fs.mkdir(path.dirname(targetPath), { recursive: true });
    const temporaryPath = `${targetPath}.${process.pid}.tmp`;
    await fs.writeFile(temporaryPath, buildSurveyMarkdown(payload), "utf8");
    await fs.rename(temporaryPath, targetPath);
    refreshInBackground("survey");
    // 落盘成功才算「完成问卷」—— 这里报在 sendJson 之前，响应本身不受影响。
    reportUsageStep("survey-submit");
    sendJson(response, 200, {
      ok: true,
      path: SURVEY_TARGET,
      submitted_at: payload.submitted_at || new Date().toISOString(),
    });
  };

  /* ---------- 选题删除 ----------
     只做两件事：从选题库 CSV 里摘掉这一行、把它的选题分析记录移到 .trash。
     删之前先备份整份 CSV；不接受任意路径，只认快照里登记过的 analysis_path。 */
  const CONTENT_LIBRARY_TABLE = "我方资料/我方内容库.csv";
  const TOPICS_TABLE = "我方资料/选题库.csv";
  const TOPICS_ANALYSIS_DIR = "我方资料/选题分析/";
  const TRASH_DIR = "我方资料/.trash";
  const DICTIONARY_TABLE = "沉淀结论/内容特征词典.csv";

  /* 逐字符切行：字段里可能有被引号包住的换行，不能直接 split("\n") */
  const splitCsvRows = (text) => {
    const rows = [];
    let current = "";
    let inQuote = false;
    for (let index = 0; index < text.length; index += 1) {
      const char = text[index];
      if (char === '"') {
        if (inQuote && text[index + 1] === '"') {
          current += '""';
          index += 1;
          continue;
        }
        inQuote = !inQuote;
        current += char;
        continue;
      }
      if (char === "\n" && !inQuote) {
        rows.push(current);
        current = "";
        continue;
      }
      if (char === "\r") continue;
      current += char;
    }
    if (current) rows.push(current);
    return rows;
  };

  const parseCsvRow = (row) => {
    const cells = [];
    let current = "";
    let inQuote = false;
    for (let index = 0; index < row.length; index += 1) {
      const char = row[index];
      if (char === '"') {
        if (inQuote && row[index + 1] === '"') {
          current += '"';
          index += 1;
          continue;
        }
        inQuote = !inQuote;
        continue;
      }
      if (char === "," && !inQuote) {
        cells.push(current);
        current = "";
        continue;
      }
      current += char;
    }
    cells.push(current);
    return cells;
  };

  const deleteTopic = async (request, response) => {
    const contentType = String(request.headers["content-type"] || "").toLowerCase();
    if (!contentType.startsWith("application/json")) throw createError("unsupported_media_type", 415);
    let payload;
    try {
      payload = JSON.parse(await readRequestBody(request, 16 * 1024));
    } catch (error) {
      if (error?.statusCode) throw error;
      throw createError("invalid_json", 400);
    }
    const topicId = String(payload?.id || "").trim();
    if (!topicId) throw createError("invalid_payload", 400);

    const current = snapshot || (await refresh("topic-delete"));
    const topic = (current?.workflows?.production?.topics || []).find((item) => item.id === topicId);
    if (!topic) throw createError("topic_not_found", 404);

    const csvPath = path.join(resolvedWorkspaceRoot, TOPICS_TABLE);
    const raw = await fs.readFile(csvPath, "utf8");
    const bom = raw.charCodeAt(0) === 0xfeff ? "\uFEFF" : "";
    const rows = splitCsvRows(bom ? raw.slice(1) : raw);
    if (rows.length < 2) throw createError("topic_table_empty", 500);

    const header = parseCsvRow(rows[0]);
    const idIndex = header.indexOf("选题ID");
    if (idIndex < 0) throw createError("topic_table_broken", 500);

    const kept = rows.filter((row, index) => {
      if (index === 0) return true;
      return String(parseCsvRow(row)[idIndex] || "").trim() !== topicId;
    });
    if (kept.length === rows.length) throw createError("topic_row_missing", 404);

    // 先留一份原表，再写回
    const stamp = new Date().toISOString().replace(/[:.]/g, "-");
    const backupDir = path.join(resolvedWorkspaceRoot, TRASH_DIR);
    await fs.mkdir(backupDir, { recursive: true });
    const backupPath = path.join(backupDir, `选题库.csv.${stamp}.bak`);
    await fs.writeFile(backupPath, raw, "utf8");

    const temporaryPath = `${csvPath}.${process.pid}.tmp`;
    await fs.writeFile(temporaryPath, bom + kept.join("\n") + "\n", "utf8");
    await fs.rename(temporaryPath, csvPath);

    // 分析记录移到回收目录，不做物理删除
    let movedAnalysis = "";
    const analysisPath = String(topic.analysis_path || "").trim();
    if (analysisPath.startsWith(TOPICS_ANALYSIS_DIR)) {
      const from = path.join(resolvedWorkspaceRoot, analysisPath);
      const to = path.join(backupDir, `${stamp}-${path.basename(analysisPath)}`);
      try {
        await fs.rename(from, to);
        movedAnalysis = path.relative(resolvedWorkspaceRoot, to);
      } catch (_error) {
        movedAnalysis = "";
      }
    }

    refreshInBackground("topic-delete");
    sendJson(response, 200, {
      ok: true,
      id: topicId,
      removed_analysis: movedAnalysis,
      backup: path.relative(resolvedWorkspaceRoot, backupPath),
    });
  };

  /* ---------- 脚本内容删除 ----------
     照选题删除的口径：先从内容库 CSV 摘掉这一行，再把内容目录整体移到 .trash。
     删之前先备份整份内容库；关联选题能恢复成「已完成分析」，让它可以重新生产。 */
  const deleteContent = async (request, response) => {
    const contentType = String(request.headers["content-type"] || "").toLowerCase();
    if (!contentType.startsWith("application/json")) throw createError("unsupported_media_type", 415);
    let payload;
    try {
      payload = JSON.parse(await readRequestBody(request, 16 * 1024));
    } catch (error) {
      if (error?.statusCode) throw error;
      throw createError("invalid_json", 400);
    }
    const contentId = String(payload?.id || "").trim();
    if (!contentId) throw createError("invalid_payload", 400);

    const current = snapshot || (await refresh("content-delete"));
    const content = (current?.workflows?.production?.own_contents || []).find((item) => item.id === contentId);
    if (!content) throw createError("content_not_found", 404);

    const stamp = new Date().toISOString().replace(/[:.]/g, "-");
    const backupDir = path.join(resolvedWorkspaceRoot, TRASH_DIR);
    await fs.mkdir(backupDir, { recursive: true });

    const csvPath = path.join(resolvedWorkspaceRoot, CONTENT_LIBRARY_TABLE);
    const raw = await fs.readFile(csvPath, "utf8");
    const bom = raw.charCodeAt(0) === 0xfeff ? "\uFEFF" : "";
    const rows = splitCsvRows(bom ? raw.slice(1) : raw);
    if (rows.length < 2) throw createError("content_table_empty", 500);

    const header = parseCsvRow(rows[0]);
    const idIndex = header.indexOf("内容ID");
    if (idIndex < 0) throw createError("content_table_broken", 500);

    const kept = rows.filter((row, index) => {
      if (index === 0) return true;
      return String(parseCsvRow(row)[idIndex] || "").trim() !== contentId;
    });
    if (kept.length === rows.length) throw createError("content_row_missing", 404);

    const backupPath = path.join(backupDir, `我方内容库.csv.${stamp}.bak`);
    await fs.writeFile(backupPath, raw, "utf8");

    const temporaryPath = `${csvPath}.${process.pid}.tmp`;
    await fs.writeFile(temporaryPath, bom + kept.join("\n") + "\n", "utf8");
    await fs.rename(temporaryPath, csvPath);

    // 内容目录整体移到回收目录，不做物理删除
    let movedDirectory = "";
    const directory = String(content.directory_path || "").trim();
    if (directory.startsWith("我方资料/我方内容/")) {
      const from = path.join(resolvedWorkspaceRoot, directory);
      const to = path.join(backupDir, `${stamp}-${path.basename(directory)}`);
      try {
        await fs.rename(from, to);
        movedDirectory = path.relative(resolvedWorkspaceRoot, to);
      } catch (_error) {
        movedDirectory = "";
      }
    }

    // 关联选题回到可重新生产的状态
    let resetTopic = "";
    const topicId = String(content.topic_id || "").trim();
    if (topicId) {
      const topicPath = path.join(resolvedWorkspaceRoot, TOPICS_TABLE);
      try {
        const topicRaw = await fs.readFile(topicPath, "utf8");
        const topicBom = topicRaw.charCodeAt(0) === 0xfeff ? "\uFEFF" : "";
        const topicRows = splitCsvRows(topicBom ? topicRaw.slice(1) : topicRaw);
        const topicHeader = parseCsvRow(topicRows[0] || "");
        const topicIdAt = topicHeader.indexOf("选题ID");
        const topicRowIndex = topicIdAt >= 0
          ? topicRows.findIndex((row, index) => index > 0 && String(parseCsvRow(row)[topicIdAt] || "").trim() === topicId)
          : -1;
        if (topicRowIndex >= 0) {
          const topicCells = parseCsvRow(topicRows[topicRowIndex]);
          const statusAt = topicHeader.indexOf("当前状态");
          if (statusAt >= 0) topicCells[statusAt] = "已完成分析";
          const recordAt = topicHeader.indexOf("内容生产记录路径");
          if (recordAt >= 0) topicCells[recordAt] = "";
          topicRows[topicRowIndex] = topicCells.map(csvCell).join(",");
          const topicBackupPath = path.join(backupDir, `选题库.csv.${stamp}.bak`);
          await fs.writeFile(topicBackupPath, topicRaw, "utf8");
          const topicTemporaryPath = `${topicPath}.${process.pid}.tmp`;
          await fs.writeFile(topicTemporaryPath, topicBom + topicRows.join("\n") + "\n", "utf8");
          await fs.rename(topicTemporaryPath, topicPath);
          resetTopic = topicId;
        }
      } catch (_error) {
        resetTopic = "";
      }
    }

    refreshInBackground("content-delete");
    sendJson(response, 200, {
      ok: true,
      id: contentId,
      removed_directory: movedDirectory,
      reset_topic: resetTopic,
      backup: path.relative(resolvedWorkspaceRoot, backupPath),
    });
  };

  /* ---------- 内容生产记录：手动改写初稿 / 终稿 ----------
     只改「内容初稿」「内容终稿」两节里某一版的标题和正文，
     其余结构（内容定位、框架表、其它版本）一个字节都不动。
     写前先整份备份到 .meiling/trash/。 */
  const CONTENT_STAGE = {
    draft: { heading: "内容初稿", marker: "完整初稿", titles: ["标题"] },
    final: { heading: "内容终稿", marker: "完整终稿", titles: ["最终内容标题", "最终标题", "标题"] },
  };

  const labelIndex = (lines, labels, limit) => {
    for (let i = 0; i < limit; i += 1) {
      for (const label of labels) {
        const match = lines[i].match(/^\s*([^：:]+)[：:]\s*(.*)$/);
        if (match && match[1].trim() === label) return i;
      }
    }
    return -1;
  };

  /* 纯函数：把一份内容生产记录里的某一版初稿/终稿替换掉，返回新文本。
     stage 定位 → 版本定位 → 标题行替换/补齐 → 正文整段替换。 */
  const rewriteContentStage = (markdown, payload) => {
    const stage = CONTENT_STAGE[payload.section];
    const text = String(markdown || "").replace(/\r\n?/g, "\n");

    const headPattern = new RegExp(`^##\\s+${stage.heading}\\s*$`, "m");
    const headMatch = headPattern.exec(text);
    if (!headMatch) throw createError("content_stage_missing", 422);

    const stageBodyStart = headMatch.index + headMatch[0].length;
    const rest = text.slice(stageBodyStart);
    const nextHeading = rest.search(/^##\s+/m);
    const stageBodyEnd = nextHeading >= 0 ? stageBodyStart + nextHeading : text.length;

    const stageBody = text.slice(stageBodyStart, stageBodyEnd);
    const versions = [...stageBody.matchAll(/^###\s+v([1-9]\d*)\s*$/gm)];

    let versionStart = 0;
    let versionEnd = stageBody.length;
    if (versions.length) {
      const wanted = payload.version
        ? versions.findIndex((item) => Number(item[1]) === Number(payload.version))
        : versions.length - 1;
      if (wanted < 0) throw createError("content_version_missing", 422);
      versionStart = versions[wanted].index;
      versionEnd = versions[wanted + 1]?.index ?? stageBody.length;
    }

    const versionText = stageBody.slice(versionStart, versionEnd);
    const lines = versionText.split("\n");
    /* 标记行优先认裸行；模型漏写时退一步认「### 二、完整初稿」这类标题行。
       两处定位（markerIndex / nextMarkerIndex）必须用同一个找法，
       否则兜底命中标题行时 nextMarkerIndex 会找不到、正文被整段吞掉。 */
    const barePattern = new RegExp(`^${stage.marker}\\s*$`);
    const markerPattern = new RegExp(
      `^(?:#{1,6}\\s*)?(?:[一二三四五六七八九十\\d]{1,3}\\s*[、．.]\\s*)?${stage.marker}\\s*$`,
    );
    const findMarker = (list) => {
      const bare = list.findIndex((line) => barePattern.test(line.trim()));
      return bare >= 0 ? bare : list.findIndex((line) => markerPattern.test(line.trim()));
    };
    const markerIndex = findMarker(lines);
    if (markerIndex < 0) throw createError("content_marker_missing", 422);

    /* 标题：有就改，没有就补在 marker 前一条空行隔开 */
    const setLabel = (labels, value) => {
      if (typeof value !== "string") return;
      const clean = value.replace(/\s*\n\s*/g, " ").trim();
      const at = labelIndex(lines, labels, markerIndex);
      if (at >= 0) {
        lines[at] = `${labels[0]}：${clean}`;
        return;
      }
      lines.splice(markerIndex, 0, `${labels[0]}：${clean}`, "");
    };

    setLabel(stage.titles, payload.title);

    const nextMarkerIndex = findMarker(lines);
    const bodyLines = String(payload.body || "").replace(/\r\n?/g, "\n").replace(/^\n+|\n+$/g, "").split("\n");
    const merged = [...lines.slice(0, nextMarkerIndex + 1), "", ...bodyLines];

    /* 版本原尾巴（通常是两个换行）必须留着，否则下一节的 ## 会被粘到最后一行后面 */
    const versionTail = (versionText.match(/\n*$/) || [""])[0] || "\n\n";
    const nextStageBody = stageBody.slice(0, versionStart)
      + merged.join("\n").replace(/\n+$/, "")
      + versionTail
      + stageBody.slice(versionEnd);

    return text.slice(0, stageBodyStart) + nextStageBody + text.slice(stageBodyEnd);
  };

  const updateContentScript = async (request, response) => {
    const contentType = String(request.headers["content-type"] || "").toLowerCase();
    if (!contentType.startsWith("application/json")) throw createError("unsupported_media_type", 415);
    let payload;
    try {
      payload = JSON.parse(await readRequestBody(request, 512 * 1024));
    } catch (error) {
      if (error?.statusCode) throw error;
      throw createError("invalid_json", 400);
    }

    const contentId = String(payload?.id || "").trim();
    const section = String(payload?.section || "").trim();
    if (!contentId) throw createError("invalid_payload", 400);
    if (!CONTENT_STAGE[section]) throw createError("invalid_payload", 400);
    if (typeof payload.body !== "string" || !payload.body.trim()) throw createError("invalid_payload", 400);

    const current = snapshot || (await refresh("content-edit"));
    const content = (current?.workflows?.production?.own_contents || []).find((item) => item.id === contentId);
    if (!content) throw createError("content_not_found", 404);
    const recordPath = String(content.production_record_path || "").trim();
    if (!recordPath) throw createError("content_record_missing", 404);

    const absolutePath = path.resolve(resolvedWorkspaceRoot, recordPath);
    if (!absolutePath.startsWith(resolvedWorkspaceRoot + path.sep)) throw createError("invalid_path", 400);

    const raw = await fs.readFile(absolutePath, "utf8");
    const nextText = rewriteContentStage(raw, { ...payload, section });

    const stamp = new Date().toISOString().replace(/[:.]/g, "-");
    const backupDir = path.join(resolvedWorkspaceRoot, TRASH_DIR);
    await fs.mkdir(backupDir, { recursive: true });
    const backupPath = path.join(backupDir, `${stamp}-${path.basename(absolutePath)}`);
    await fs.writeFile(backupPath, raw, "utf8");

    const temporaryPath = `${absolutePath}.${process.pid}.tmp`;
    await fs.writeFile(temporaryPath, nextText, "utf8");
    await fs.rename(temporaryPath, absolutePath);

    refreshInBackground("content-edit");
    sendJson(response, 200, {
      ok: true,
      id: contentId,
      section,
      version: payload.version || null,
      backup: path.relative(resolvedWorkspaceRoot, backupPath),
    });
  };

  /* ---------- 内容生产记录：把当前初稿复制成终稿 ----------
     用户点「通过审查确认终稿」并确认后走这里：不按审查建议调整，
     把当前这一版初稿的标题和正文原样写成唯一的终稿。
     旧终稿整节覆盖；写前先整份备份到 .meiling/trash/。 */
  const contentStageRange = (text, heading) => {
    const head = new RegExp(`^##\\s+${heading}\\s*$`, "m").exec(text);
    if (!head) return null;
    const bodyStart = head.index + head[0].length;
    const next = text.slice(bodyStart).search(/^##\s+/m);
    return { start: head.index, bodyStart, end: next >= 0 ? bodyStart + next : text.length };
  };

  const draftToFinalText = (markdown, payload) => {
    const text = String(markdown || "").replace(/\r\n?/g, "\n");
    const draft = contentStageRange(text, "内容初稿");
    if (!draft) throw createError("content_stage_missing", 422);

    const draftBody = text.slice(draft.bodyStart, draft.end);
    const versions = [...draftBody.matchAll(/^###\s+v([1-9]\d*)\s*$/gm)];
    let versionStart = 0;
    let versionEnd = draftBody.length;
    if (versions.length) {
      const wanted = payload.version
        ? versions.findIndex((item) => Number(item[1]) === Number(payload.version))
        : versions.length - 1;
      if (wanted < 0) throw createError("content_version_missing", 422);
      versionStart = versions[wanted].index;
      versionEnd = versions[wanted + 1]?.index ?? draftBody.length;
    }

    const lines = draftBody.slice(versionStart, versionEnd).split("\n");
    const barePattern = /^完整初稿\s*$/;
    const markerPattern = /^(?:#{1,6}\s*)?(?:[一二三四五六七八九十\d]{1,3}\s*[、．.]\s*)?完整初稿\s*$/;
    const markerIndex = (() => {
      const bare = lines.findIndex((line) => barePattern.test(line.trim()));
      return bare >= 0 ? bare : lines.findIndex((line) => markerPattern.test(line.trim()));
    })();
    if (markerIndex < 0) throw createError("content_marker_missing", 422);

    const labelValue = (labels) => {
      for (let i = 0; i < markerIndex; i += 1) {
        const match = lines[i].match(/^\s*([^：:]+)[：:]\s*(.*)$/);
        if (match && labels.includes(match[1].trim())) return match[2].replace(/\s+/g, " ").trim();
      }
      return "";
    };
    const title = labelValue(["标题"]) || String(payload.title || "").trim() || "未命名";
    const body = lines.slice(markerIndex + 1).join("\n").replace(/\n+$/, "").trim();
    if (!body) throw createError("content_marker_missing", 422);

    const final = contentStageRange(text, "内容终稿");
    const finalVersions = final
      ? [...text.slice(final.bodyStart, final.end).matchAll(/^###\s+v([1-9]\d*)\s*$/gm)]
      : [];
    const recordFinalVersion = finalVersions.length ? Number(finalVersions[finalVersions.length - 1][1]) : 0;
    const finalVersion = Math.max(recordFinalVersion, Number(payload.finalVersion) || 0, 1);

    const finalBlock = [
      "## 内容终稿",
      "",
      `### v${finalVersion}`,
      "",
      `最终内容标题：${title}`,
      "",
      "完整终稿",
      "",
      body,
      "",
    ].join("\n");

    if (!final) return { text: `${text.replace(/\s+$/, "")}\n\n${finalBlock}`, title, version: finalVersion };
    const tail = text.slice(final.end);
    return {
      text: `${text.slice(0, final.start)}${finalBlock}${tail ? `\n${tail}` : ""}`,
      title,
      version: finalVersion,
    };
  };

  const confirmContentFinal = async (request, response) => {
    const contentType = String(request.headers["content-type"] || "").toLowerCase();
    if (!contentType.startsWith("application/json")) throw createError("unsupported_media_type", 415);
    let payload;
    try {
      payload = JSON.parse(await readRequestBody(request, 512 * 1024));
    } catch (error) {
      if (error?.statusCode) throw error;
      throw createError("invalid_json", 400);
    }

    const contentId = String(payload?.id || "").trim();
    if (!contentId) throw createError("invalid_payload", 400);

    const current = snapshot || (await refresh("content-final"));
    const content = (current?.workflows?.production?.own_contents || []).find((item) => item.id === contentId);
    if (!content) throw createError("content_not_found", 404);
    const recordPath = String(content.production_record_path || "").trim();
    if (!recordPath) throw createError("content_record_missing", 404);

    const absolutePath = path.resolve(resolvedWorkspaceRoot, recordPath);
    if (!absolutePath.startsWith(resolvedWorkspaceRoot + path.sep)) throw createError("invalid_path", 400);

    const raw = await fs.readFile(absolutePath, "utf8");

    /* 阶段状态先读出来：终稿版本要和状态里的版本对齐，避免记录写 v1、状态还停在 v3。 */
    const statePath = path.join(path.dirname(absolutePath), "过程记录", "当前阶段状态.json");
    let stateRaw = "";
    let stateBase = null;
    try {
      stateRaw = await fs.readFile(statePath, "utf8");
      stateBase = JSON.parse(stateRaw.replace(/^\uFEFF/, ""));
    } catch (_error) {
      stateRaw = "";
      stateBase = null;
    }
    const stateFinalVersion = Number(stateBase?.["阶段"]?.["内容终稿"]?.["版本"] || 0);
    const written = draftToFinalText(raw, { ...payload, title: content.title, finalVersion: stateFinalVersion });

    /* 卡片上的「已完成」只认 我方内容库.csv 的「当前状态」列。
       所以收尾时要把标题、状态一起写回去，有选题的顺带更新选题库。 */
    const contentLibraryPath = path.join(resolvedWorkspaceRoot, CONTENT_LIBRARY_TABLE);
    const contentRaw = await fs.readFile(contentLibraryPath, "utf8").catch(() => {
      throw createError("content_table_missing", 500);
    });
    const contentBom = contentRaw.charCodeAt(0) === 0xfeff ? "\uFEFF" : "";
    const contentRows = splitCsvRows(contentBom ? contentRaw.slice(1) : contentRaw);
    if (contentRows.length < 2) throw createError("content_table_empty", 500);
    const contentHeader = parseCsvRow(contentRows[0]);
    const contentIdIndex = contentHeader.indexOf("内容ID");
    if (contentIdIndex < 0) throw createError("content_table_broken", 500);
    const contentRowIndex = contentRows.findIndex(
      (row, index) => index > 0 && String(parseCsvRow(row)[contentIdIndex] || "").trim() === contentId,
    );
    if (contentRowIndex < 0) throw createError("content_row_missing", 404);

    const setCell = (header, cells, label, value) => {
      const at = header.indexOf(label);
      if (at >= 0) cells[at] = String(value ?? "");
    };
    const contentCells = parseCsvRow(contentRows[contentRowIndex]);
    setCell(contentHeader, contentCells, "标题", written.title);
    setCell(contentHeader, contentCells, "当前状态", "已完成");
    setCell(contentHeader, contentCells, "内容目录路径", path.posix.dirname(recordPath));
    setCell(contentHeader, contentCells, "内容生产记录路径", recordPath);
    contentRows[contentRowIndex] = contentCells.map(csvCell).join(",");
    const nextContentRaw = contentBom + contentRows.join("\n") + "\n";

    const topicIdIndex = contentHeader.indexOf("选题ID");
    const topicId = topicIdIndex >= 0 ? String(contentCells[topicIdIndex] || "").trim() : "";
    let topicPath = "";
    let topicRaw = "";
    let nextTopicRaw = "";
    if (topicId) {
      topicPath = path.join(resolvedWorkspaceRoot, TOPICS_TABLE);
      try {
        topicRaw = await fs.readFile(topicPath, "utf8");
        const topicBom = topicRaw.charCodeAt(0) === 0xfeff ? "\uFEFF" : "";
        const topicRows = splitCsvRows(topicBom ? topicRaw.slice(1) : topicRaw);
        const topicHeader = parseCsvRow(topicRows[0] || "");
        const topicIdAt = topicHeader.indexOf("选题ID");
        const topicRowIndex = topicIdAt >= 0
          ? topicRows.findIndex((row, index) => index > 0 && String(parseCsvRow(row)[topicIdAt] || "").trim() === topicId)
          : -1;
        if (topicRowIndex >= 0) {
          const topicCells = parseCsvRow(topicRows[topicRowIndex]);
          setCell(topicHeader, topicCells, "当前状态", "已生产");
          setCell(topicHeader, topicCells, "内容生产记录路径", recordPath);
          topicRows[topicRowIndex] = topicCells.map(csvCell).join(",");
          nextTopicRaw = topicBom + topicRows.join("\n") + "\n";
        }
      } catch (_error) {
        /* 选题库读不到不影响终稿落地，内容库状态才是列表依据 */
        topicRaw = "";
        nextTopicRaw = "";
      }
    }

    /* 阶段状态：只有上游已确认、没有失效记录时才同步收尾。
       其它情况宁可不动，避免写出一个自相矛盾的状态。 */
    let nextStateRaw = "";
    if (stateBase) {
      const stages = stateBase["阶段"];
      const positioning = stages?.["内容定位"];
      const draftStage = stages?.["内容初稿"];
      const finalStage = stages?.["内容终稿"];
      const draftVersion = Number(draftStage?.["版本"] || 0);
      const draftStatus = String(draftStage?.["状态"] || "");
      const finalStatus = String(finalStage?.["状态"] || "");
      const canComplete = String(stateBase["内容ID"] || "").trim() === contentId
        && !stateBase["失效记录"]?.["是否存在"]
        && String(stages?.["建档与恢复"]?.["状态"] || "") === "已确认"
        && String(positioning?.["状态"] || "") === "已确认"
        && draftVersion > 0
        && ["进行中", "等待用户确认", "已确认"].includes(draftStatus)
        && ["未开始", "进行中", "等待用户确认", "已确认"].includes(finalStatus);
      if (canComplete) {
        draftStage["状态"] = "已确认";
        draftStage["依赖版本"] = { ...(draftStage["依赖版本"] || {}), 内容定位: Number(positioning["版本"] || 0) };
        finalStage["版本"] = written.version;
        finalStage["状态"] = "已确认";
        finalStage["依赖版本"] = { ...(finalStage["依赖版本"] || {}), 内容初稿: draftVersion };
        stateBase["当前阶段"] = "内容终稿";
        stateBase["流程状态"] = "已完成";
        stateBase["当前是否完成"] = true;
        stateBase["是否需要用户确认"] = false;
        stateBase["下一步推荐"] = "";
        stateBase["允许的下一步"] = [];
        stateBase["阻断原因"] = "";
        stateBase["机器数据"] = { ...(stateBase["机器数据"] || {}), 待确认问题: "" };
        nextStateRaw = `${JSON.stringify(stateBase, null, 2)}\n`;
      }
    }

    const stamp = new Date().toISOString().replace(/[:.]/g, "-");
    const backupDir = path.join(resolvedWorkspaceRoot, TRASH_DIR);
    await fs.mkdir(backupDir, { recursive: true });
    const backupPath = path.join(backupDir, `${stamp}-${path.basename(absolutePath)}`);
    await fs.writeFile(backupPath, raw, "utf8");
    await fs.writeFile(path.join(backupDir, `${stamp}-我方内容库.csv`), contentRaw, "utf8");
    if (nextStateRaw) await fs.writeFile(path.join(backupDir, `${stamp}-当前阶段状态.json`), stateRaw, "utf8");
    if (nextTopicRaw) await fs.writeFile(path.join(backupDir, `${stamp}-选题库.csv`), topicRaw, "utf8");

    const writeText = async (targetPath, text) => {
      const temporaryPath = `${targetPath}.${process.pid}.tmp`;
      await fs.writeFile(temporaryPath, text, "utf8");
      await fs.rename(temporaryPath, targetPath);
    };
    try {
      await writeText(absolutePath, written.text);
      await writeText(contentLibraryPath, nextContentRaw);
      if (nextStateRaw) await writeText(statePath, nextStateRaw);
      if (nextTopicRaw) await writeText(topicPath, nextTopicRaw);
    } catch (error) {
      await fs.writeFile(absolutePath, raw, "utf8").catch(() => {});
      await fs.writeFile(contentLibraryPath, contentRaw, "utf8").catch(() => {});
      if (stateRaw) await fs.writeFile(statePath, stateRaw, "utf8").catch(() => {});
      if (topicRaw) await fs.writeFile(topicPath, topicRaw, "utf8").catch(() => {});
      throw error;
    }

    refreshInBackground("content-final");
    sendJson(response, 200, {
      ok: true,
      id: contentId,
      version: payload.version || null,
      final_version: written.version,
      status: "已完成",
      state_updated: Boolean(nextStateRaw),
      backup: path.relative(resolvedWorkspaceRoot, backupPath),
    });
  };

  /* ---------- 规律词典：启用 / 停用 / 删除 / 新增 ----------
     只动 沉淀结论/内容特征词典.csv，写前先备份整表。
     特征不物理删除也能用「状态」停用，但用户明确要删时走删除 + 备份。 */
  const DICTIONARY_CATEGORIES = {
    内容类别: { level: "策略层", prefix: "STR-CAT" },
    标题: { level: "动作层", prefix: "ACT-TITLE" },
    开头: { level: "动作层", prefix: "ACT-OPEN" },
    内容结构: { level: "动作层", prefix: "ACT-STRUCT" },
    表达风格: { level: "动作层", prefix: "ACT-STYLE" },
    互动设计: { level: "动作层", prefix: "ACT-INTERACT" },
  };
  const DICTIONARY_STAGES = ["吸引", "持续", "信任", "获得", "沉淀"];

  const csvCell = (value) => {
    const text = String(value ?? "");
    return /[",\n\r]/.test(text) ? `"${text.replace(/"/g, '""')}"` : text;
  };

  const updateDictionary = async (request, response) => {
    const contentType = String(request.headers["content-type"] || "").toLowerCase();
    if (!contentType.startsWith("application/json")) throw createError("unsupported_media_type", 415);
    let payload;
    try {
      payload = JSON.parse(await readRequestBody(request, 64 * 1024));
    } catch (error) {
      if (error?.statusCode) throw error;
      throw createError("invalid_json", 400);
    }
    const action = String(payload?.action || "").trim();
    if (!["enable", "disable", "delete", "add"].includes(action)) throw createError("invalid_action", 400);

    const csvPath = path.join(resolvedWorkspaceRoot, DICTIONARY_TABLE);
    const raw = await fs.readFile(csvPath, "utf8");
    const bom = raw.charCodeAt(0) === 0xfeff ? "\uFEFF" : "";
    const rows = splitCsvRows(bom ? raw.slice(1) : raw);
    if (rows.length < 2) throw createError("dictionary_table_empty", 500);

    const header = parseCsvRow(rows[0]);
    const idIndex = header.indexOf("特征ID");
    const statusIndex = header.indexOf("状态");
    if (idIndex < 0 || statusIndex < 0) throw createError("dictionary_table_broken", 500);

    const nextRows = rows.slice(1);
    let id = String(payload?.id || "").trim();

    if (action === "add") {
      const category = String(payload?.category || "").trim();
      const meta = DICTIONARY_CATEGORIES[category];
      if (!meta) throw createError("invalid_category", 400);
      const label = String(payload?.label || "").trim();
      if (!label) throw createError("invalid_payload", 400);

      // 同一个前缀下取最大编号 +1，保证 ID 连续且不撞已有词条。
      let max = 0;
      const idPattern = new RegExp(`^${meta.prefix}-(\\d+)$`);
      for (const row of nextRows) {
        const found = String(parseCsvRow(row)[idIndex] || "").trim().match(idPattern);
        if (found) max = Math.max(max, Number(found[1]));
      }
      const newId = `${meta.prefix}-${String(max + 1).padStart(3, "0")}`;

      const cells = header.map(() => "");
      const put = (column, value) => {
        const index = header.indexOf(column);
        if (index >= 0) cells[index] = value;
      };
      put("特征ID", newId);
      put("所属层级", meta.level);
      put("特征小类", category);
      put("标准标签", label);
      put("定义", String(payload?.definition || "").trim());
      put("适用边界", String(payload?.scope || "").trim());
      put("排除边界", String(payload?.exclude || "").trim());
      const stages = Array.isArray(payload?.stages) ? payload.stages.map((item) => String(item).trim()) : [];
      DICTIONARY_STAGES.forEach((stage) => put(`${stage}作用`, stages.includes(stage) ? "是" : "否"));
      put("状态", "启用");
      put("版本", "v1");
      nextRows.push(cells.map(csvCell).join(","));
      id = newId;
    } else {
      if (!id) throw createError("invalid_payload", 400);
      const rowIndex = nextRows.findIndex((row) => String(parseCsvRow(row)[idIndex] || "").trim() === id);
      if (rowIndex < 0) throw createError("dictionary_row_missing", 404);
      if (action === "delete") {
        nextRows.splice(rowIndex, 1);
      } else {
        const cells = parseCsvRow(nextRows[rowIndex]);
        cells[statusIndex] = action === "enable" ? "启用" : "停用";
        nextRows[rowIndex] = cells.map(csvCell).join(",");
      }
    }

    // 先留一份原表，再写回
    const stamp = new Date().toISOString().replace(/[:.]/g, "-");
    const backupDir = path.join(resolvedWorkspaceRoot, TRASH_DIR);
    await fs.mkdir(backupDir, { recursive: true });
    const backupPath = path.join(backupDir, `内容特征词典.csv.${stamp}.bak`);
    await fs.writeFile(backupPath, raw, "utf8");

    const temporaryPath = `${csvPath}.${process.pid}.tmp`;
    await fs.writeFile(temporaryPath, bom + [rows[0], ...nextRows].join("\n") + "\n", "utf8");
    await fs.rename(temporaryPath, csvPath);

    refreshInBackground("dictionary-update");
    sendJson(response, 200, {
      ok: true,
      action,
      id,
      backup: path.relative(resolvedWorkspaceRoot, backupPath),
    });
  };

  const handleRequest = async (request, response) => {
    const url = new URL(request.url || "/", "http://127.0.0.1");

    if (request.method === "POST") {
      if (url.pathname === "/api/onboarding-survey") {
        await saveOnboardingSurvey(request, response);
        return;
      }
      if (url.pathname === "/api/topic/delete") {
        await deleteTopic(request, response);
        return;
      }
      if (url.pathname === "/api/content/delete") {
        await deleteContent(request, response);
        return;
      }
      if (url.pathname === "/api/dictionary/update") {
        await updateDictionary(request, response);
        return;
      }
      if (url.pathname === "/api/local-config") {
        await saveLocalConfig(request, response);
        return;
      }
      if (url.pathname === "/api/content/script") {
        await updateContentScript(request, response);
        return;
      }
      if (url.pathname === "/api/content/confirm-final") {
        await confirmContentFinal(request, response);
        return;
      }
      sendJson(response, 404, { ok: false, error: "not_found" });
      return;
    }
    if (request.method !== "GET") {
      sendJson(response, 405, { ok: false, error: "method_not_allowed" });
      return;
    }

    // 问卷页真的渲染出来了 → 漏斗的「打开问卷」这一层。
    // 只用作打卡，不读不写任何业务文件；前端在进入问卷态时打一次。
    if (url.pathname === "/api/onboarding-survey/open") {
      reportUsageStep("survey-open");
      sendJson(response, 200, { ok: true });
      return;
    }
    if (url.pathname === "/api/health") {
      sendJson(response, 200, {
        ok: true,
        service: "meiling-local-dashboard",
        pid: process.pid,
        runtime_id: runtimeId,
        started_at: startedAt,
        revision,
        workspace: snapshot?.workspace || { name: path.basename(resolvedWorkspaceRoot), version: "未知" },
      });
      return;
    }
    if (url.pathname === "/api/ds-client") {
      // 前端半部的版本探针：插件换版之后，光看日志分不清用户跑的是新前端还是旧前端，
      // 这里让页面把版本号报一次，落在 .meiling/dashboard/ds-client.json 里。
      try {
        await fs.mkdir(path.join(resolvedWorkspaceRoot, ".meiling", "dashboard"), { recursive: true });
        await fs.writeFile(
          path.join(resolvedWorkspaceRoot, ".meiling", "dashboard", "ds-client.json"),
          JSON.stringify({ v: url.searchParams.get("v") || "", at: new Date().toISOString() }),
        );
      } catch {
        /* 记不上不影响功能 */
      }
      sendJson(response, 200, { ok: true });
      return;
    }
    if (url.pathname === "/api/ds-goto") {
      const result = await takeDsGoto(resolvedWorkspaceRoot);
      void noteGotoPoll(resolvedWorkspaceRoot, result.spec);
      sendJson(response, 200, result);
      return;
    }
    if (url.pathname === "/api/snapshot") {
      sendJson(response, 200, clientSnapshot(snapshot));
      return;
    }
    if (url.pathname === "/api/refresh") {
      sendJson(response, 200, clientSnapshot(await refresh("manual")));
      return;
    }
    if (url.pathname === "/api/local-config") {
      sendJson(response, 200, describeLocalConfig(await readLocalConfigText()));
      return;
    }
    if (url.pathname === "/api/file") {
      await serveTextFile(url, response);
      return;
    }
    if (url.pathname === "/api/raw") {
      await serveRawFile(url, response);
      return;
    }
    if (url.pathname === "/api/events") {
      const eventHeaders = {
        ...baseHeaders("text/event-stream; charset=utf-8"),
        Connection: "keep-alive",
        "Content-Security-Policy": "default-src 'none'; frame-ancestors 'none'",
      };
      if (sseClients.size >= MAX_SSE_CLIENTS) {
        response.writeHead(200, eventHeaders);
        response.write(`event: busy\ndata: ${JSON.stringify({ limit: MAX_SSE_CLIENTS })}\n\n`);
        response.end();
        return;
      }
      response.writeHead(200, eventHeaders);
      response.write(`event: ready\ndata: ${JSON.stringify({ revision })}\n\n`);
      sseClients.add(response);
      request.on("close", () => sseClients.delete(response));
      return;
    }
    if (await servePublicAsset(url.pathname, response)) return;
    sendJson(response, 404, { ok: false, error: "not_found" });
  };

  return {
    get url() {
      const address = server?.address();
      return address && typeof address === "object" ? `http://127.0.0.1:${address.port}` : "";
    },
    get revision() {
      return revision;
    },
    get snapshot() {
      return snapshot;
    },
    async start() {
      if (!LOOPBACK_HOSTS.has(host)) throw createError("loopback_host_required", 400);
      if (server) return this;
      await refresh("startup");
      startedAt = new Date().toISOString();
      server = http.createServer((request, response) => {
        handleRequest(request, response).catch((error) => {
          if (response.headersSent) {
            response.destroy(error);
            return;
          }
          const mapped = mapPathError(error);
          sendJson(response, mapped.statusCode || 500, {
            ok: false,
            error: mapped.code || "internal_error",
          });
        });
      });
      server.on("clientError", (_error, socket) => socket.end("HTTP/1.1 400 Bad Request\r\n\r\n"));
      await new Promise((resolve, reject) => {
        server.once("error", reject);
        server.listen(port, host, () => {
          server.off("error", reject);
          resolve();
        });
      });
      await installWatchers();
      reconcileTimer = setInterval(() => refreshInBackground("reconcile"), reconcileIntervalMs);
      reconcileTimer.unref?.();
      heartbeatTimer = setInterval(() => {
        for (const response of sseClients) response.write(": heartbeat\n\n");
      }, 15_000);
      heartbeatTimer.unref?.();
      return this;
    },
    refresh,
    async close() {
      if (closing) return;
      closing = true;
      clearTimeout(debounceTimer);
      clearInterval(reconcileTimer);
      clearInterval(heartbeatTimer);
      closeWatchers();
      for (const response of sseClients) response.end();
      sseClients.clear();
      if (server) {
        const currentServer = server;
        server = null;
        currentServer.closeAllConnections?.();
        await new Promise((resolve) => currentServer.close(resolve));
      }
    },
  };
}
