import crypto from "node:crypto";
import fs from "node:fs/promises";
import path from "node:path";

export const BROWSE_SECTIONS = Object.freeze([
  { id: "own", label: "我方资料", root: "我方资料" },
  { id: "benchmark", label: "对标资料", root: "对标资料" },
  { id: "insights", label: "沉淀结论", root: "沉淀结论" },
  { id: "skills", label: "Skills", root: "skills" },
]);

const BROWSE_ROOTS = new Set(BROWSE_SECTIONS.map((item) => item.root));
const TEXT_EXTENSIONS = new Set([".md", ".csv", ".json", ".txt"]);
const IMAGE_EXTENSIONS = new Set([".png", ".jpg", ".jpeg", ".webp", ".gif"]);
const MEDIA_EXTENSIONS = new Set([".mp4", ".mov", ".m4v", ".mp3", ".m4a", ".wav", ".pdf"]);
const DEFAULT_MAX_TEXT_BYTES = 512 * 1024;
const DEFAULT_MAX_FILES = 20_000;
const WORKSPACE_DATA_ROOTS = Object.freeze(["我方资料", "对标资料", "沉淀结论", "skills"]);
const AI_ONLY_FILE_NAMES = new Set([
  ".keep",
  "我方当前概要.md",
  "推荐选题结果.json",
  "内容基础结构化数据.json",
  "内容分析.html",
  "内容生产记录.html",
]);
const PROCESS_DIRECTORY_NAMES = new Set(["过程记录"]);
const PROCESS_FILE_PATTERNS = Object.freeze([
  /内容分析初判/,
  /内容分析修正/,
  /修正对比/,
  /候选状态/,
  /确认.*状态/,
  /推荐包/,
  /推荐上下文/,
  /模板调试记录/,
  /网页原型/,
  /验证记录/,
]);

const MASTER_TABLES = Object.freeze({
  own_accounts: {
    label: "我方账号",
    path: "我方资料/我方账号库.csv",
  },
  own_contents: {
    label: "我方内容",
    path: "我方资料/我方内容库.csv",
    statusColumn: "当前状态",
  },
  topics: {
    label: "选题",
    path: "我方资料/选题库.csv",
    statusColumn: "当前状态",
  },
  benchmark_accounts: {
    label: "对标账号",
    path: "对标资料/对标账号库.csv",
  },
  benchmark_contents: {
    label: "对标内容",
    path: "对标资料/对标内容库.csv",
  },
});
const SKILL_CATALOG_PATH = "skills/Skill分类说明.md";
const FALLBACK_SKILL_CATEGORY = Object.freeze({
  id: "extensions",
  label: "扩展能力",
  description: "工作区新增的公开 Skill；可在分类说明中为它指定位置。",
});
// 规律分析页的正式资产。这里**没有**「内容特征规律库」和「内容特征观察表」：
// 前者已退役（唯一读者是内容生产里被开关关掉的推荐器），后者是待入库的观察记录、不是正式规律。
const PATTERN_ASSETS = Object.freeze({
  report: "沉淀结论/内容规律知识库.md",
  dictionary: "沉淀结论/内容特征词典.csv",
  instances: "沉淀结论/内容特征实例.csv",
});
const PATTERN_STAGE_COLUMNS = Object.freeze([
  ["吸引作用", "吸引"],
  ["持续作用", "持续"],
  ["信任作用", "信任"],
  ["获得作用", "获得"],
  ["沉淀作用", "沉淀"],
]);
// 推荐选题：固定一份正式结果，由 执行单元/保存推荐选题.py 原子写入（同目录还有一份同内容的 .md 给人看）。
const RECOMMENDED_TOPICS_PATH = "我方资料/推荐选题/推荐选题结果.json";
// 搜索选题：只认执行流程里的正式落盘结果（我方资料/搜索选题/<run_id>/搜索选题结果.json）。
const TOPIC_SEARCH_RESULT_PATTERN = /^我方资料\/搜索选题\/[^/]+\/搜索选题结果\.json$/;
const TOPIC_SEARCH_DATA_SOURCE_LABELS = Object.freeze({
  justoneapi: "JustOneAPI",
  chrome_devtools: "本机 Chrome",
});
// 一次投影最近 N 轮：第 1 轮是「本次推荐」，其余进「历史推荐」。
// 每轮 JSON 都在 100KB 量级，轮次多了没必要全读。
const TOPIC_SEARCH_RUN_LIMIT = 4;

function metricTotal(values) {
  return values.reduce((sum, value) => {
    const text = String(value ?? "").trim().replaceAll(",", "");
    const match = text.match(/^(\d+(?:\.\d+)?)\s*(万|w|W)?$/);
    if (!match) return sum;
    const base = Number(match[1]);
    if (!Number.isFinite(base)) return sum;
    return sum + Math.round(match[2] ? base * 10000 : base);
  }, 0);
}

function createError(code, message = code) {
  const error = new Error(message);
  error.code = code;
  return error;
}

function normalizeLineEndings(value) {
  return String(value || "").replace(/\r\n?/g, "\n");
}

function stripBom(value) {
  return String(value || "").replace(/^\uFEFF+/, "");
}

function toPosix(relativePath) {
  return relativePath.split(path.sep).join("/");
}

function jsonFingerprint(value) {
  return crypto.createHash("sha256").update(JSON.stringify(value), "utf8").digest("hex");
}

function contentFingerprint(value) {
  return crypto.createHash("sha256").update(value).digest("hex");
}

function fileKind(extension) {
  if (TEXT_EXTENSIONS.has(extension)) return "text";
  if (IMAGE_EXTENSIONS.has(extension)) return "image";
  if (MEDIA_EXTENSIONS.has(extension)) return "media";
  return "file";
}

export function isUserVisibleRelativePath(relativePath) {
  const value = toPosix(String(relativePath || "")).replace(/^\.\//, "");
  const segments = value.split("/").filter(Boolean);
  if (segments.length === 0 || segments.some((segment) => segment.startsWith("."))) return false;
  if (segments.some((segment) => PROCESS_DIRECTORY_NAMES.has(segment))) return false;
  const name = segments.at(-1);
  if (AI_ONLY_FILE_NAMES.has(name)) return false;
  return !PROCESS_FILE_PATTERNS.some((pattern) => pattern.test(name));
}

function titleFromText(content, fallback) {
  const heading = normalizeLineEndings(content).match(/^#\s+(.+?)\s*$/m);
  return heading ? heading[1].replace(/[*_`]/g, "").trim() || fallback : fallback;
}

function compactSearchText(value, maxLength = 1_200) {
  return normalizeLineEndings(value)
    .replace(/^---\n[\s\S]*?\n---\n?/, "")
    .replace(/```[\s\S]*?```/g, " ")
    .replace(/[#>*_`|\[\]()]/g, " ")
    .replace(/\s+/g, " ")
    .trim()
    .slice(0, maxLength);
}

function visibleTextLength(value) {
  // Count the reader-facing transcript text instead of Markdown structure and whitespace.
  const content = normalizeLineEndings(value)
    .replace(/^---\n[\s\S]*?\n---\n?/, "")
    .replace(/```[\s\S]*?```/g, "")
    .replace(/^\s{0,3}#{1,6}\s+.*$/gm, "")
    .replace(/^\s*[-*+]\s+/gm, "")
    .replace(/[\p{P}\p{S}]/gu, "")
    .replace(/\s+/g, "");
  return [...content].length;
}

function parseFrontMatter(content) {
  const normalized = normalizeLineEndings(content);
  const match = normalized.match(/^---\n([\s\S]*?)\n---(?:\n|$)/);
  if (!match) return {};
  const result = {};
  for (const line of match[1].split("\n")) {
    const field = line.match(/^([A-Za-z0-9_-]+)\s*:\s*(.*?)\s*$/);
    if (!field) continue;
    result[field[1]] = field[2].replace(/^(["'])([\s\S]*)\1$/, "$2").trim();
  }
  return result;
}

function isInternalSkill(content) {
  return /不面向用户|内部入口|不要向用户推荐|仅供内部/.test(content);
}

function stripMarkdownCell(value) {
  return String(value || "")
    .replace(/`([^`]+)`/g, "$1")
    .replace(/\*\*([^*]+)\*\*/g, "$1")
    .trim();
}

function parseSkillCatalog(content) {
  const categories = [];
  const byName = new Map();
  let current = null;
  for (const line of normalizeLineEndings(content).split("\n")) {
    const heading = line.match(/^##\s+(.+?)\s*$/);
    if (heading) {
      current = {
        id: `category-${categories.length + 1}`,
        label: stripMarkdownCell(heading[1]),
        description: "",
      };
      categories.push(current);
      continue;
    }
    if (!current || !line.trim()) continue;
    if (!line.trim().startsWith("|")) {
      if (!current.description && !line.startsWith("#")) current.description = stripMarkdownCell(line);
      continue;
    }
    const cells = line.trim().replace(/^\||\|$/g, "").split("|").map(stripMarkdownCell);
    const skillName = cells[0]?.match(/^[a-z0-9][a-z0-9-]*$/i)?.[0];
    if (!skillName || /^(skill|技能|名称)$/i.test(skillName)) continue;
    byName.set(skillName, {
      category: current.label,
      category_id: current.id,
      category_description: current.description || "按当前工作流组织的可调用能力。",
      display_name: cells[1] || "",
      description: cells[2] || "",
    });
  }
  return { categories, byName };
}

function diagnostic(code, relativePath, message) {
  return { code, path: relativePath, message };
}

export function normalizeWorkspaceRelativePath(relativePath) {
  const value = String(relativePath || "").trim();
  if (
    !value ||
    value.includes("\\") ||
    value.includes("\0") ||
    value.startsWith("/") ||
    path.posix.isAbsolute(value)
  ) {
    throw createError("invalid_relative_path");
  }
  const segments = value.split("/");
  if (segments.some((segment) => !segment || segment === "." || segment === "..")) {
    throw createError("invalid_relative_path");
  }
  return segments.join("/");
}

export async function resolveAllowedWorkspacePath(workspaceRoot, relativePath) {
  const normalized = normalizeWorkspaceRelativePath(relativePath);
  const segments = normalized.split("/");
  if (!BROWSE_ROOTS.has(segments[0])) {
    throw createError("path_not_browsable");
  }
  if (!isUserVisibleRelativePath(normalized)) throw createError("path_not_visible");

  const root = path.resolve(String(workspaceRoot || ""));
  let current = root;
  for (const segment of segments) {
    current = path.join(current, segment);
    let stat;
    try {
      stat = await fs.lstat(current);
    } catch (error) {
      if (error?.code === "ENOENT") throw createError("path_not_found");
      throw error;
    }
    if (stat.isSymbolicLink()) throw createError("symlink_forbidden");
  }

  const relative = path.relative(root, current);
  if (!relative || relative.startsWith("..") || path.isAbsolute(relative)) {
    throw createError("invalid_relative_path");
  }
  return current;
}

export function parseCsv(input) {
  const text = stripBom(normalizeLineEndings(input));
  const rows = [];
  let row = [];
  let field = "";
  let quoted = false;

  for (let index = 0; index < text.length; index += 1) {
    const character = text[index];
    if (quoted) {
      if (character === '"') {
        if (text[index + 1] === '"') {
          field += '"';
          index += 1;
        } else {
          quoted = false;
        }
      } else {
        field += character;
      }
      continue;
    }

    if (character === '"') {
      if (field) throw createError("csv_invalid_quote");
      quoted = true;
    } else if (character === ",") {
      row.push(field);
      field = "";
    } else if (character === "\n") {
      row.push(field);
      rows.push(row);
      row = [];
      field = "";
    } else {
      field += character;
    }
  }

  if (quoted) throw createError("csv_unclosed_quote");
  if (field || row.length > 0) {
    row.push(field);
    rows.push(row);
  }
  while (rows.length > 0 && rows.at(-1).every((cell) => cell === "")) rows.pop();
  return rows;
}

function csvRecords(content) {
  const rows = parseCsv(content);
  if (rows.length === 0) throw createError("csv_missing_header");
  const headers = rows[0].map((header) => header.trim());
  if (headers.some((header) => !header)) throw createError("csv_invalid_header");
  return rows.slice(1)
    .filter((row) => row.some((cell) => String(cell || "").trim() !== ""))
    .map((row) => Object.fromEntries(headers.map((header, index) => [header, String(row[index] || "").trim()])));
}

function normalizeStoredPath(value) {
  let normalized = String(value || "").trim().replace(/\\/g, "/");
  if (!normalized) return "";
  normalized = normalized.replace(/^file:\/\//i, "");
  for (const root of WORKSPACE_DATA_ROOTS) {
    if (normalized === root || normalized.startsWith(`${root}/`)) break;
    const marker = `/${root}/`;
    const markerIndex = normalized.indexOf(marker);
    if (markerIndex >= 0) {
      normalized = normalized.slice(markerIndex + 1);
      break;
    }
  }
  normalized = normalized.replace(/^\.\//, "");
  try {
    const safe = normalizeWorkspaceRelativePath(normalized);
    return BROWSE_ROOTS.has(safe.split("/")[0]) && isUserVisibleRelativePath(safe) ? safe : "";
  } catch (_error) {
    return "";
  }
}

function directoryFromRecord(record, fields) {
  for (const field of fields) {
    const storedPath = normalizeStoredPath(record[field]);
    if (!storedPath) continue;
    return path.posix.extname(storedPath) ? path.posix.dirname(storedPath) : storedPath;
  }
  return "";
}

function documentReference(relativePath, fileByPath, fallbackTitle = "") {
  const normalized = normalizeStoredPath(relativePath);
  const file = fileByPath.get(normalized);
  if (!file) return null;
  return {
    path: file.path,
    title: file.title || fallbackTitle || file.name,
    view: file.view,
    modified_at: file.modified_at,
  };
}

function firstDocument(candidates, fileByPath, fallbackTitle = "") {
  for (const candidate of candidates) {
    const document = documentReference(candidate, fileByPath, fallbackTitle);
    if (document) return document;
  }
  return null;
}

function childFiles(directory, files, predicate = () => true) {
  const normalized = normalizeStoredPath(directory);
  if (!normalized) return [];
  const prefix = `${normalized}/`;
  return files.filter((file) => file.path.startsWith(prefix) && predicate(file));
}

function latestDirectDocument(directory, files, predicate = () => true, fallbackTitle = "") {
  const normalized = normalizeStoredPath(directory);
  if (!normalized) return null;
  const file = files
    .filter((item) => path.posix.dirname(item.path) === normalized && predicate(item))
    .sort((left, right) => right.modified_ms - left.modified_ms || right.path.localeCompare(left.path, "zh-CN"))[0];
  if (!file) return null;
  return {
    path: file.path,
    title: fallbackTitle || file.title || file.name,
    view: file.view,
    modified_at: file.modified_at,
  };
}

// 创作者中心按「采集批次目录」落盘（创作者中心数据/20261028-175443/账号概览.json），
// 这里按修改时间取最新一份，不依赖批次目录名排序。
function latestNestedDocument(directory, files, fileName, fallbackTitle = "") {
  const normalized = normalizeStoredPath(directory);
  if (!normalized) return null;
  const file = files
    .filter((item) => item.path.startsWith(`${normalized}/`) && (item.name === fileName || item.path.endsWith(`/${fileName}`)))
    .sort((left, right) => right.modified_ms - left.modified_ms || right.path.localeCompare(left.path, "zh-CN"))[0];
  if (!file) return null;
  return {
    path: file.path,
    title: fallbackTitle || file.title || file.name,
    view: file.view,
    modified_at: file.modified_at,
  };
}

// 「最近动态」用的时间口径：一律取落盘文件的本地修改时间，不取平台发布时间。
// 先按原样解析（ISO 与 `YYYY-MM-DD` 都吃），失败再按「2026年10月1日」这类中文写法归一化 ——
// 注意别一上来就替换 `.`，ISO 的毫秒 `.000Z` 会被改坏、解析成 NaN。
function activityTimestamp(value) {
  const raw = String(value || "").trim();
  if (!raw) return null;
  const direct = Date.parse(raw);
  if (Number.isFinite(direct)) return direct;
  const normalized = raw.replace(/[年/.]/g, "-").replace(/月/g, "-").replace(/日/g, "");
  const timestamp = Date.parse(normalized);
  return Number.isFinite(timestamp) ? timestamp : null;
}

// 建档时间用实体目录里「最早」的那个文件：头像、图片说明这些是建档时落的第一批，
// 后续阶段的文档（概要分析、详细分析…）都晚于它，不会把「入库」顶成最新动态。
function oldestFile(files) {
  return files.reduce((best, file) => (best === null || file.modified_ms < best.modified_ms ? file : best), null);
}

// 一条链路 × 一个实体只出一条动态：取时间最新的那个阶段。
function latestActivity(candidates) {
  let best = null;
  let bestStamp = -1;
  for (const candidate of candidates) {
    if (!candidate || !candidate.at) continue;
    const stamp = activityTimestamp(candidate.at);
    if (stamp === null || stamp <= bestStamp) continue;
    best = candidate;
    bestStamp = stamp;
  }
  return best;
}

// 出厂空壳（只有一级标题）不算正式内容，避免首页把「预置文件」当成用户动作。
function hasLevelTwoHeading(content) {
  return /^##[ \t]/m.test(String(content || ""));
}

// 调查问卷：题目是 `## 1. …`，答案是一行一个 `- 选项`。
// 只预置了题目、一条答案都没有的骨架不算填过 —— 新手引导要按这个判。
function surveyFilled(content) {
  const text = String(content || "");
  return /^##[ \t]*\d+[.、]/m.test(text) && /^-[ \t]+\S/m.test(text);
}

// 问卷答案按题号拆成 { q1: { title, values[], extra } }。
// 首页「账号方向」还没生成正文时，先拿它垫一版（放的是用户自己选的原话，不是结论）。
// 格式由 server.mjs 的 buildSurveyMarkdown 固定生成，`- 补充：xxx` 单列成 extra。
function readSurveyAnswers(content) {
  const answers = {};
  let current = null;
  for (const line of String(content || "").replace(/\r\n?/g, "\n").split("\n")) {
    const heading = line.match(/^##[ \t]+(\d+)[.、][ \t]*(.*)$/);
    if (heading) {
      current = { title: heading[2].trim(), values: [] };
      answers[`q${heading[1]}`] = current;
      continue;
    }
    if (!current) continue;
    const item = line.match(/^-[ \t]+(.+)$/);
    if (!item) continue;
    const text = item[1].trim();
    if (text.startsWith("补充：")) current.extra = text.slice(3).trim();
    else current.values.push(text);
  }
  return answers;
}

// 过程记录是跑流程的中间产物，不算用户可见的落盘，时间口径里要排除。
function isProcessFile(file) {
  return file.path.includes("/过程记录/");
}

function numericValue(value) {
  const raw = String(value ?? "").trim();
  if (!raw) return null;
  const normalized = raw.replace(/[,，\s]/g, "");
  const number = Number(normalized);
  return Number.isFinite(number) ? number : null;
}

function displayStatus(value, fallback = "待补齐") {
  return String(value || "").trim() || fallback;
}

function productionStatus(value, hasProductionRecord = false) {
  const status = String(value || "").trim();
  if (["已完成", "待发布", "已发布", "已复盘"].includes(status)) return "已完成";
  if (status === "生产中") return status;
  return hasProductionRecord ? "生产中" : "待创作";
}

function publicationRecordData(content) {
  const fields = {};
  const metrics = {};
  for (const line of normalizeLineEndings(content).split("\n")) {
    const field = line.match(/^\s*-\s*([^：:]+)[：:]\s*(.*?)\s*$/);
    if (field) fields[field[1].trim()] = field[2].trim();
    const cells = line.trim().match(/^\|\s*([^|]+?)\s*\|\s*([^|]+?)\s*\|$/);
    if (!cells || /^(?:指标|类型|[-: ]+)$/i.test(cells[1].trim())) continue;
    metrics[cells[1].trim()] = cells[2].trim();
  }
  return { fields, metrics };
}

// 内容生产记录里「内容定位 → 呈现方案」这两张表就是脚本创作卡要的三个规格：
// 媒介形式 / 内容类别 / 主目标侧重。记录会按版本追加，取最后一次出现的值。
function productionRecordData(content) {
  const fields = {};
  for (const line of normalizeLineEndings(content).split("\n")) {
    const cells = line.trim().match(/^\|\s*([^|]+?)\s*\|\s*([^|]+?)\s*\|$/);
    if (!cells || /^(?:字段|[-: ]+)$/.test(cells[1].trim())) continue;
    fields[cells[1].trim()] = cells[2].trim();
  }
  const pick = (label) => {
    const key = Object.keys(fields).find((item) => item.startsWith(label));
    return key ? fields[key] : "";
  };
  return {
    theme: pick("主题"),
    media_format: pick("媒介形式"),
    content_type: pick("内容类别"),
    primary_goal: pick("主目标侧重"),
  };
}

function publicationRecordTimestamp(fileName) {
  const match = String(fileName || "").match(/^(\d{4})年(\d{1,2})月(\d{1,2})日复盘记录\.md$/);
  if (!match) return 0;
  return Date.UTC(Number(match[1]), Number(match[2]) - 1, Number(match[3]));
}

function entitySearchText(...values) {
  return values.flat(Infinity).filter(Boolean).join(" ").replace(/\s+/g, " ").trim();
}

// 规律分析页只给人看最核心的字段：这几张正式资产里其余列不进快照。
function patternStages(record) {
  return PATTERN_STAGE_COLUMNS
    .filter(([column]) => String(record[column] || "").trim() === "是")
    .map(([, stage]) => stage)
    .join("·");
}

function patternAsset(relativePath, fileByPath, records, mapRow) {
  const file = fileByPath.get(relativePath) || null;
  return {
    path: relativePath,
    file: file ? { path: file.path, view: file.view, modified_at: file.modified_at } : null,
    total: records.length,
    rows: records.map(mapRow),
  };
}

function csvSummary(content, statusColumn) {
  const rows = parseCsv(content);
  if (rows.length === 0) throw createError("csv_missing_header");
  const headers = rows[0].map((header) => header.trim());
  if (headers.some((header) => !header)) throw createError("csv_invalid_header");
  const records = rows.slice(1).filter((row) => row.some((cell) => cell.trim() !== ""));
  const result = { available: true, count: records.length, statuses: {} };
  if (!statusColumn) return result;

  const statusIndex = headers.indexOf(statusColumn);
  if (statusIndex === -1) throw createError("csv_missing_status_column");
  for (const record of records) {
    const status = String(record[statusIndex] || "未填写").trim() || "未填写";
    result.statuses[status] = (result.statuses[status] || 0) + 1;
  }
  return result;
}

/* 首页「账号方向」卡片：只认 我方当前概要.md 里这三节。
   标题按「名字」或「名字概要」匹配，文件里写 ## 用户与需求概要 或 ## 用户与需求 都能取到。 */
/* 我方当前概要.md 的四个小节（2026-11-02 补「自身条件」）。
   顺序就是首页账号方向的排列顺序；首页会按 key 换成更口语的标题，
   这里保持 md 里的原词，账号方向页跟着用。 */
const OWN_DIRECTION_SECTIONS = Object.freeze([
  { key: "self", label: "自身条件", heading: "自身条件" },
  { key: "user", label: "用户与需求", heading: "用户与需求" },
  { key: "biz", label: "产品与变现", heading: "产品与变现" },
  { key: "media", label: "账号与内容", heading: "账号与内容" },
]);

function parseOwnDirection(text) {
  const blocks = new Map();
  let current = null;
  for (const line of String(text || "").split(/\r?\n/)) {
    const heading = /^##\s+(.+?)\s*$/.exec(line);
    if (heading) {
      current = heading[1];
      blocks.set(current, []);
      continue;
    }
    if (current) blocks.get(current).push(line);
  }

  const headings = [...blocks.keys()];
  const items = [];
  for (const section of OWN_DIRECTION_SECTIONS) {
    const name = headings.find((key) => key === section.heading || key === `${section.heading}概要`);
    const body = name ? blocks.get(name).join("\n").replace(/\s*\n+\s*/g, " ").trim() : "";
    if (body) items.push({ key: section.key, label: section.label, text: body });
  }
  return items.length ? { path: "我方资料/我方当前概要.md", items } : null;
}

/* ==================================================================
   首页「账号方向」卡（2026-11-02 改口径）
   ------------------------------------------------------------------
   数据源从 我方当前概要.md 换成 我方自媒体规划.md —— 规划那份是结构化的：
     ## 方向总览          → 自身条件 / 目标客户 / 变现方式 三个短句 + 待验证那串
     ## 4. 账号与内容
        ### 账号定位       → 定位那一句话
        ### 内容主线：X     → 每条主线的「目标问题与价值」
   首页三个分区（定位 / 内容主线 / 需要验证）都从这一份出，不再拼接两个文件。
   取不到的分区就留空，让前端整块不显示，不拿别的段凑句子。
   ================================================================== */

const MD_CLEAN = (value) => String(value || "").replace(/\*\*/g, "").replace(/\s+/g, " ").trim();

/* 按标题层级切开：返回 [{level,title,text}] ，text 是本节到下一个标题之前的原文 */
function mdSections(md) {
  const lines = String(md || "").replace(/\r\n?/g, "\n").split("\n");
  const out = [];
  let current = null;
  let fence = false;
  for (const line of lines) {
    if (/^```/.test(line)) fence = !fence;
    if (!fence) {
      const heading = /^(#{1,4})\s+(.+?)\s*$/.exec(line);
      if (heading) {
        current = { level: heading[1].length, title: heading[2], body: [] };
        out.push(current);
        continue;
      }
    }
    if (current) current.body.push(line);
  }
  return out.map((section) => ({ ...section, text: section.body.join("\n").trim() }));
}

/* 第一个非空段落（去掉列表符号和引用符号） */
function mdFirstParagraph(text) {
  const blocks = String(text || "")
    .split(/\n\s*\n/)
    .map((block) => MD_CLEAN(block.replace(/^[-*>\s]+/gm, "")))
    .filter(Boolean);
  return blocks[0] || "";
}

const mdSentences = (text) => MD_CLEAN(text).split(/。/).map((part) => part.trim()).filter(Boolean);

/* 「方向总览」有两种写法，都要认：
   ① 字段化（2026-11 起的新格式）：
        **自身条件**：产品经理，运营小红书「谈笑AI」…
        **目标客户**：用 AI 做知识、经验或方法型内容…
        **账号定位**：用 AI 把内容生产做成系统的人。
        **内容主线**：问题判断、真实过程、方法复用。
        **变现方式**：免费版引流，完整版、安装服务和定制收费。
        - 完整版价格与使用授权        ← 这几条列表 = 还没定的事
        - 用户真实付费意愿
   ② 一整段散文（旧格式）：整段当方向说明显示。
   ---------------------------------------------------------------- */
function parseOverviewBlock(text) {
  const fields = [];
  const open = [];
  for (const raw of String(text || "").split("\n")) {
    const line = raw.replace(/\s+$/, "").trim();
    if (!line) continue;
    const field = /^\*\*(.+?)\*\*\s*[：:]\s*(.+)$/.exec(line);
    if (field) {
      fields.push({ k: field[1].trim(), v: field[2].trim() });
      continue;
    }
    const item = /^[-*]\s+(.+)$/.exec(line);
    if (item) open.push(item[1].trim());
  }
  return { fields, open };
}

function parseMediaPlanDirection(text) {
  const source = String(text || "");
  if (!source.trim()) return null;
  const sections = mdSections(source);
  const findSection = (pattern) => sections.find((section) => pattern.test(section.title));

  const overviewSection = findSection(/^方向总览$/);
  const overviewText = String(overviewSection?.text || "");
  const { fields, open: openList } = parseOverviewBlock(overviewText);
  const fieldMap = new Map(fields.map((item) => [item.k, item.v]));
  const byFields = fields.length > 0;

  /* 账号定位：字段里有就用字段；没有就取「### 账号定位」首段冒号前 */
  let positioning = fieldMap.get("账号定位") || "";
  if (!positioning) {
    const positionSection = findSection(/^账号定位$/);
    if (positionSection) {
      positioning = mdFirstParagraph(positionSection.text);
      const colon = positioning.search(/[：:]/);
      if (colon > 0) positioning = positioning.slice(0, colon);
      positioning = positioning.replace(/^一个/, "").replace(/^我是/, "");
    }
  }
  positioning = MD_CLEAN(positioning);

  /* 三条事实只在字段化格式下产出。
     旧格式（一整段散文）拆成三条会和整段说明重复，那种情况只显示整段。 */
  const legs = ["自身条件", "目标客户", "变现方式"]
    .map((key) => (fieldMap.get(key) ? { k: key, v: fieldMap.get(key) } : null))
    .filter(Boolean);

  /* 内容主线：三种颜色按顺序配，前端用 data-hue 出配色。
     只展示「讲什么」；「主要交付」不在首页露出。 */
  const hues = ["blue", "plum", "mint"];
  const lines = [];
  sections
    .filter((section) => section.level === 3 && /^内容主线[：:]/.test(section.title))
    .forEach((section) => {
      const title = section.title.replace(/^内容主线[：:]\s*/, "").trim();
      const index = sections.indexOf(section);
      let target = "";
      for (let i = index + 1; i < sections.length; i += 1) {
        const next = sections[i];
        if (next.level <= 3) break;
        if (/讲什么|目标问题与价值/.test(next.title)) {
          target = mdFirstParagraph(next.text);
          continue;
        }
      }
      const brief = target.split(/。/)[0].trim();
      if (!title || !brief) return;
      lines.push({ title, text: brief, hue: hues[lines.length % hues.length] });
    });

  /* 需要验证：只认总览里那几条列表（字段化格式）。
     旧格式的待验证事项已经写在总览那段话里，再拆一遍就是同一句话说两遍。 */
  const open = byFields ? openList.filter((item) => item.length <= 24) : [];

  /* 整段说明只在旧格式下用；字段化格式已经有五个字段，再放整段就重复了 */
  const overview = byFields ? "" : MD_CLEAN(overviewText);

  if (!positioning && !legs.length && !lines.length) return null;
  return { path: "我方资料/我方自媒体规划.md", format: byFields ? "fields" : "prose", overview, positioning, legs, lines, open };
}

/* ==================================================================
   账号改进行动清单（<日期>账号改进行动清单.md）
   ------------------------------------------------------------------
   开头有一段 <!-- account-action-list:start --> 的 JSON，字段：
     方向 / 对应问题 / 动作 / 具体怎么做[] / 优先原因 / 参考做法 / 验证指标
   首页「行动建议」就用这一份 —— 诊断里的「目标与实际」只是按维度说的建议，
   没有验证指标，也说不清「对应哪个问题、具体怎么做」。
   ================================================================== */
/* 账号内容规律分析里「我的打法小结」下面第一段就是账号级的一句话打法。
   跳过空行和表格行，遇到下一个标题就停；没这一节就返回空串，不拿别的段凑。
   放索引里算，前端拿到的就是现成的字符串，不用再异步读文件。 */
function patternSummarySentence(markdown) {
  const lines = String(markdown || "").split(/\r?\n/);
  const start = lines.findIndex((line) => /^#{2,4}\s*我的打法小结/.test(line.trim()));
  if (start < 0) return "";
  for (let index = start + 1; index < lines.length; index += 1) {
    const line = lines[index].trim();
    if (!line) continue;
    if (/^#/.test(line)) break;
    if (/^\|/.test(line)) continue;
    return line.replace(/\*\*/g, "").trim();
  }
  return "";
}

function parseActionList(text) {
  const source = String(text || "");
  const hit = /<!--\s*account-action-list:start\s*-->\s*```json\s*([\s\S]*?)```/.exec(source);
  if (!hit) return null;
  let data = null;
  try {
    data = JSON.parse(hit[1]);
  } catch (_error) {
    return null;
  }
  const rows = Array.isArray(data?.行动) ? data.行动 : [];
  const items = rows.map((row, index) => ({
    no: index + 1,
    id: MD_CLEAN(row["ID"] || ""),
    direction: MD_CLEAN(row["方向"] || ""),
    problems: MD_CLEAN(row["对应问题"] || ""),
    title: MD_CLEAN(row["动作"] || ""),
    how: Array.isArray(row["具体怎么做"])
      ? row["具体怎么做"].map((line) => MD_CLEAN(line)).filter(Boolean)
      : (MD_CLEAN(row["具体怎么做"] || "") ? [MD_CLEAN(row["具体怎么做"])] : []),
    why: MD_CLEAN(row["优先原因"] || ""),
    reference: MD_CLEAN(row["参考做法"] || ""),
    verify: MD_CLEAN(row["验证指标"] || ""),
  })).filter((item) => item.title);
  if (!items.length) return null;
  return { path: "", items };
}

/* ==================================================================
   首页「账号洞察」：读账号目录里最新那份详细诊断
   ------------------------------------------------------------------
   诊断文件里带一段 <!-- self-account-diagnosis-summary:start --> 的 JSON，
   机器可读；JSON 外面还有正文节。两种版式都要认：
     v2（2026-10-28 起）：综合诊断 / 目标与实际（带「建议」）/ 关键问题，没有单独的动作节
     v1：另外有「## 7. 优先调整动作」
   行动建议优先取 v1 的动作节，没有就退回 v2「目标与实际」里的「建议」。
   ================================================================== */

function mdSectionRange(md, startPattern) {
  const lines = String(md || "").replace(/\r\n?/g, "\n").split("\n");
  let start = -1;
  for (let i = 0; i < lines.length; i += 1) {
    if (start < 0) {
      if (/^##\s/.test(lines[i]) && startPattern.test(lines[i])) start = i + 1;
      continue;
    }
    if (/^##\s/.test(lines[i])) return lines.slice(start, i).join("\n").trim();
  }
  return start < 0 ? "" : lines.slice(start).join("\n").trim();
}

/* 「### 问题 1：标题」这种分组，组内是「- 字段：值」 */
function mdGroups(text) {
  const items = [];
  let current = null;
  for (const raw of String(text || "").split("\n")) {
    const heading = /^###\s+(.+?)\s*$/.exec(raw);
    if (heading) {
      // 「问题 1：xxx」「动作 2：xxx」「2.1 目标用户」「3.1 变化 1：xxx」都要剥成纯标题
      const title = heading[1]
        .replace(/^(问题|动作|变化)\s*\d+[：:]\s*/, "")
        .replace(/^\d+(\.\d+)*\s*/, "")
        .replace(/^(问题|动作|变化)\s*\d+[：:]\s*/, "")
        .trim();
      current = { title, body: [] };
      items.push(current);
      continue;
    }
    if (current) current.body.push(raw);
  }
  return items.map((item) => ({ title: item.title, text: item.body.join("\n") }));
}

function mdField(text, name) {
  const pattern = new RegExp(`^\\s*-\\s*${name}[：:]\\s*([\\s\\S]*?)(?=\\n\\s*-\\s|\\n\\n|$)`, "m");
  const hit = pattern.exec(String(text || ""));
  return hit ? MD_CLEAN(hit[1]) : "";
}

function parseDiagnosisDocument(text) {
  const source = String(text || "");
  if (!source.trim()) return null;

  /* 优先认 JSON 摘要块 */
  let summary = null;
  const jsonHit = /<!--\s*self-account-diagnosis-summary:start\s*-->\s*```json\s*([\s\S]*?)```/.exec(source);
  if (jsonHit) {
    try {
      summary = JSON.parse(jsonHit[1]);
    } catch (_error) {
      summary = null;
    }
  }

  /* 综合诊断那句：JSON 里优先；没有 JSON 的老诊断(v1)从「综合诊断」或「当前总判断」节取 */
  const verdictText = MD_CLEAN(
    summary?.综合诊断?.文本
    || mdFirstParagraph(mdSectionRange(source, /综合诊断/))
    || mdFirstParagraph(mdSectionRange(source, /当前总判断/))
    || "",
  );
  /* 问题类型：JSON 里有就用；没有就从「当前总判断」里那句「主矛盾属于 X」抠。
     ⚠️ 别写成 /…/.exec(…) [1] 的链式写法：exec 返回 null 时整段会抛
     TypeError，而这个函数在构建快照时跑，一抛就是整个工作台起不来。 */
  let mainTag = MD_CLEAN(summary?.综合诊断?.问题类型 || "");
  if (!mainTag) {
    const verdictSection = MD_CLEAN(mdSectionRange(source, /当前总判断/));
    // 只取到标点/破折号之前：「执行不足——不是内容方向错了」要的是「执行不足」
    const tagHit = /主矛盾(?:属于|是)?\s*\**([^*，。；—－\n]{2,8})/.exec(verdictSection);
    if (tagHit) mainTag = tagHit[1].trim();
  }

  /* 关键问题 */
  let problems = [];
  if (Array.isArray(summary?.关键问题) && summary.关键问题.length) {
    problems = summary.关键问题.map((item, index) => ({
      no: index + 1,
      title: MD_CLEAN(item.标题),
      type: MD_CLEAN(item.类型),
      show: MD_CLEAN(item.具体表现),
      impact: MD_CLEAN(item.影响),
    }));
  } else {
    /* 字段名两版不同：v2 是「具体表现 / 实际影响」，v1 是「主要影响 / 判断依据」 */
    problems = mdGroups(mdSectionRange(source, /关键.*问题|最关键的问题/)).map((item, index) => ({
      no: index + 1,
      title: item.title,
      type: mdField(item.text, "问题类型"),
      show: mdField(item.text, "具体表现") || mdField(item.text, "主要影响"),
      impact: mdField(item.text, "实际影响") || mdField(item.text, "判断依据"),
    }));
  }
  problems = problems.filter((item) => item.title);

  /* 行动建议：v1 的动作节优先，没有就用 v2「目标与实际」里的建议 */
  const actionGroups = mdGroups(mdSectionRange(source, /优先调整动作/));
  let actions = actionGroups.map((item, index) => ({
    no: index + 1,
    title: item.title,
    related: mdField(item.text, "对应问题"),
    how: mdField(item.text, "具体调整"),
    why: mdField(item.text, "为什么优先"),
  }));
  if (!actions.length) {
    const pairs = mdGroups(mdSectionRange(source, /目标与实际/)).map((item, index) => ({
      no: index + 1,
      title: item.title,
      related: mdField(item.text, "结论"),
      how: mdField(item.text, "建议"),
      why: mdField(item.text, "原因"),
    }));
    actions = pairs.filter((item) => item.how);
  }
  actions = actions.filter((item) => item.title);

  if (!verdictText && !problems.length && !actions.length) return null;
  /* 重点短语：诊断 JSON 里给的那几个词，前端在结论里高亮它们（跟「我的账号」同一套） */
  const keywords = Array.isArray(summary?.综合诊断?.重点短语)
    ? summary.综合诊断.重点短语.map((item) => MD_CLEAN(item)).filter(Boolean)
    : [];
  return { verdict: verdictText, mainTag, keywords, problems, actions };
}

export class WorkspaceIndex {
  constructor({
    workspaceRoot,
    now = () => new Date(),
    readFile = fs.readFile,
    maxTextBytes = DEFAULT_MAX_TEXT_BYTES,
    maxFiles = DEFAULT_MAX_FILES,
  } = {}) {
    this.workspaceRoot = path.resolve(String(workspaceRoot || ""));
    this.now = now;
    this.readFile = readFile;
    this.maxTextBytes = maxTextBytes;
    this.maxFiles = maxFiles;
    this.cache = new Map();
  }

  async _readCachedText(relativePath, absolutePath, stat) {
    const cacheKey = `${stat.size}:${stat.mtimeMs}`;
    const cached = this.cache.get(relativePath);
    if (cached?.cacheKey === cacheKey) return cached;
    if (stat.size > this.maxTextBytes) {
      const entry = { cacheKey, content: "", tooLarge: true };
      this.cache.set(relativePath, entry);
      return entry;
    }
    const raw = await this.readFile(absolutePath);
    const rawBuffer = Buffer.isBuffer(raw) ? raw : Buffer.from(String(raw), "utf8");
    const entry = {
      cacheKey,
      content: stripBom(rawBuffer.toString("utf8")),
      sha256: contentFingerprint(rawBuffer),
      modified_at: stat.mtime.toISOString(),
      tooLarge: false,
    };
    this.cache.set(relativePath, entry);
    return entry;
  }

  async _readInternalJson(relativePath) {
    const absolutePath = path.join(this.workspaceRoot, ...relativePath.split("/"));
    try {
      const stat = await fs.lstat(absolutePath);
      if (!stat.isFile() || stat.isSymbolicLink() || stat.size > this.maxTextBytes) return null;
      const entry = await this._readCachedText(`@internal:${relativePath}`, absolutePath, stat);
      return JSON.parse(entry.content);
    } catch (error) {
      if (error?.code === "ENOENT" || error instanceof SyntaxError) return null;
      throw error;
    }
  }

  /* 新手引导账本（`.meiling/引导状态.json`）：AI 那边的进度表，工作台只读不写。
     写账本是 `执行单元/上手引导状态执行器.py` 的事 —— 两边都写会把进度搞乱。
     读不到、schema 不对就返回 null，界面自动退回按「文件在不在」判断。 */
  async _readGuideState() {
    const data = await this._readInternalJson(".meiling/引导状态.json");
    if (!data || data.schema !== "meiling-guide/1" || !Array.isArray(data.steps)) return null;
    const steps = {};
    for (const item of data.steps) {
      if (item && typeof item.id === "string") {
        steps[item.id] = typeof item.status === "string" ? item.status : "pending";
      }
    }
    const decisions = data.decisions && typeof data.decisions === "object" ? data.decisions : {};
    return {
      steps,
      decisions: {
        has_account: decisions.has_account ?? null,
        production_mode: decisions.production_mode ?? null,
      },
      updated_at: typeof data.updated_at === "string" ? data.updated_at : "",
    };
  }

  /* 首页「账号方向」：读 我方资料/我方当前概要.md 的三节正文。
     这个文件在 AI_ONLY_FILE_NAMES 里（不进文件浏览列表），所以按内部文件读，不走 files 索引。 */
  async _readOwnDirection() {
    const relativePath = "我方资料/我方当前概要.md";
    const absolutePath = path.join(this.workspaceRoot, ...relativePath.split("/"));
    try {
      const stat = await fs.lstat(absolutePath);
      if (!stat.isFile() || stat.isSymbolicLink() || stat.size > this.maxTextBytes) return null;
      const entry = await this._readCachedText(`@internal:${relativePath}`, absolutePath, stat);
      return parseOwnDirection(entry.content);
    } catch (error) {
      // 没这份文件就是没做新手问卷，卡片整段不显示；读取异常也不该拖垮整个快照
      return null;
    }
  }

  /* 首页「账号方向」：读我方自媒体规划.md（2026-11-02 起的数据源） */
  async _readMediaPlanDirection() {
    const relativePath = "我方资料/我方自媒体规划.md";
    const absolutePath = path.join(this.workspaceRoot, ...relativePath.split("/"));
    try {
      const stat = await fs.lstat(absolutePath);
      if (!stat.isFile() || stat.isSymbolicLink() || stat.size > this.maxTextBytes) return null;
      const entry = await this._readCachedText(`@internal:${relativePath}`, absolutePath, stat);
      return parseMediaPlanDirection(entry.content);
    } catch (error) {
      // 没做规划就整块不显示，读取异常也不该拖垮整个快照
      return null;
    }
  }

  /* 审查报告按轮次存成 审查报告-vN.md。工作台要能在标题旁边切版本回看，
     所以投影全部版本（升序，最新的在最后），不再只留最新一版。 */
  async _readReviewVersions(directory) {
    if (!directory) return [];
    const relativeDir = `${directory}/过程记录/内容审查`;
    const absoluteDir = path.join(this.workspaceRoot, ...relativeDir.split("/"));
    let entries;
    try {
      entries = await fs.readdir(absoluteDir, { withFileTypes: true });
    } catch (error) {
      if (error?.code === "ENOENT") return [];
      throw error;
    }
    const candidates = entries
      .filter((entry) => entry.isFile() && !entry.name.startsWith("."))
      .map((entry) => {
        const match = entry.name.match(/^审查报告-v([1-9]\d*)\.md$/);
        return match ? { name: entry.name, version: Number(match[1]) } : null;
      })
      .filter(Boolean)
      .sort((left, right) => left.version - right.version);
    const versions = [];
    for (const candidate of candidates) {
      const relativePath = `${relativeDir}/${candidate.name}`;
      const absolutePath = path.join(absoluteDir, candidate.name);
      const stat = await fs.lstat(absolutePath);
      if (!stat.isFile() || stat.isSymbolicLink() || stat.size > this.maxTextBytes) continue;
      const cached = await this._readCachedText(`@internal:${relativePath}`, absolutePath, stat);
      versions.push({
        version: candidate.version,
        modified_at: stat.mtime.toISOString(),
        content: cached.content,
      });
    }
    return versions;
  }

  /* 推荐选题：保存器写两份，这份 json 是工作台的正式结果（同目录另有 .md 给人看）。
     json 在 AI_ONLY_FILE_NAMES 里，不进文件浏览列表，所以按内部文件读。 */
  async _buildRecommendedTopics() {
    const data = await this._readInternalJson(RECOMMENDED_TOPICS_PATH);
    if (!data || data.schema !== "meiling-recommended-topics/1" || !Array.isArray(data.topics)) return null;
    const topics = data.topics
      .map((topic, index) => {
        if (!topic || typeof topic !== "object") return null;
        const title = String(topic.title || "").trim();
        if (!title) return null;
        return {
          rank: Number(topic.rank) || index + 1,
          title,
          // 六要素和「选题分析」同一套口径
          theme: String(topic.theme || "").trim(),
          audience: String(topic.audience || "").trim(),
          pain: String(topic.pain || "").trim(),
          promise: String(topic.promise || "").trim(),
          proposition: String(topic.proposition || "").trim(),
          angle: String(topic.angle || "").trim(),
          mainline: String(topic.mainline || "").trim(),
          goal: String(topic.goal || "").trim(),
          // 出处：这条选题用了哪份诊断、哪条已发内容或哪篇文案；没写就当从账号方向推的
          basis: String(topic.basis || "").trim(),
          outline: Array.isArray(topic.outline)
            ? topic.outline.map((item) => String(item || "").trim()).filter(Boolean)
            : [],
        };
      })
      .filter(Boolean)
      .sort((left, right) => left.rank - right.rank);
    if (!topics.length) return null;
    return {
      generated_at: String(data.generated_at || ""),
      plan_path: String(data.plan_path || ""),
      topics,
    };
  }

  /* 搜索选题：投影最近几轮已保存的正式结果。
     第 1 轮（最新）是「本次推荐」，页面给完整详情；其余轮次进「历史推荐」。
     来源内容和评论证据按题目里记录的 ID 关联，不额外拼数据，也不回退旧轮次。 */
  _buildTopicSearch(files, diagnostics) {
    // 轮次目录名里带时间戳（run-YYYYMMDD-HHMMSS-xxx），按名字倒序就是最新的在前。
    // 不用文件 mtime：旧轮次被补写（比如回填评分）后 mtime 会变新，会把历史轮次误当成本轮。
    const candidates = files
      .filter((file) => TOPIC_SEARCH_RESULT_PATTERN.test(file.path))
      .sort((left, right) => right.path.localeCompare(left.path, "en") || right.modified_ms - left.modified_ms);
    if (!candidates.length) return null;
    const runs = candidates
      .slice(0, TOPIC_SEARCH_RUN_LIMIT)
      .map((file) => this._projectTopicSearchRun(file, files, diagnostics))
      .filter(Boolean);
    if (!runs.length) return null;
    const [latest, ...history] = runs;
    // 历史轮次只需要卡片和抽屉用得上的字段，不重复带文件路径和查询计划
    return {
      ...latest,
      history: history.map((run) => ({
        run_id: run.run_id,
        generated_at: run.generated_at,
        stats: run.stats,
        platform_counts: run.platform_counts,
        topics: run.topics,
      })),
    };
  }

  _projectTopicSearchRun(latest, files, diagnostics) {
    const cached = this.cache.get(latest.path);
    if (!cached || cached.tooLarge) {
      diagnostics.push(diagnostic("topic_search_read_failed", latest.path, "搜索选题结果文件过大或读取失败"));
      return null;
    }
    let data;
    try {
      data = JSON.parse(cached.content);
    } catch (error) {
      diagnostics.push(diagnostic("topic_search_parse_failed", latest.path, error.message));
      return null;
    }

    const rawSources = Array.isArray(data.source_contents) ? data.source_contents : [];
    const sourceById = new Map();
    const commentById = new Map();
    const platformCounts = new Map();
    let commentTotal = 0;

    rawSources.forEach((source) => {
      if (!source || typeof source !== "object") return;
      const contentId = String(source.content_id || "").trim();
      if (contentId) sourceById.set(contentId, source);
      const platform = String(source.platform || "").trim();
      if (platform) platformCounts.set(platform, (platformCounts.get(platform) || 0) + 1);
      const comments = Array.isArray(source["评论"]) ? source["评论"] : [];
      commentTotal += comments.length;
      comments.forEach((comment) => {
        if (!comment || typeof comment !== "object") return;
        const commentId = String(comment["评论ID"] || "").trim();
        if (!commentId || commentById.has(commentId)) return;
        commentById.set(commentId, {
          id: commentId,
          text: String(comment["正文"] || "").trim(),
          author: String(comment["作者昵称"] || "").trim(),
          likes: Number(comment["点赞数"] || 0) || 0,
          replies: Number(comment["回复数"] || 0) || 0,
        });
      });
    });

    const projectSource = (source) => ({
      content_id: String(source.content_id || "").trim(),
      platform: String(source.platform || "").trim(),
      title: String(source.title || "").trim(),
      author: String(source.author_name || "").trim(),
      likes: String(source.metrics?.likes ?? "").trim(),
      collects: String(source.metrics?.collects ?? "").trim(),
      comments: String(source.metrics?.comments ?? "").trim(),
      fetched: Number(source["评论条数"] || 0) || 0,
      url: String(source.url || "").trim(),
    });

    const queries = (Array.isArray(data.queries) ? data.queries : []).map((query) => ({
      id: String(query.query_id || "").trim(),
      keyword: String(query.keyword || "").trim(),
      platforms: Array.isArray(query.platforms) ? query.platforms.map((item) => String(item).trim()).filter(Boolean) : [],
      rationale: String(query.rationale || "").trim(),
    }));

    const topics = (Array.isArray(data.topics) ? data.topics : []).map((topic, index) => {
      const sourceIds = Array.isArray(topic.reference_content_ids) ? topic.reference_content_ids : [];
      const commentIds = Array.isArray(topic.evidence_comment_ids) ? topic.evidence_comment_ids : [];
      const evidenceComments = commentIds
        .map((id) => commentById.get(String(id)))
        .filter(Boolean);
      // 页面最多展示 10 条代表性评论：先放 AI 选中的证据评论，再按点赞从本题来源里补足
      const seenCommentIds = new Set(evidenceComments.map((comment) => comment.id));
      const extraComments = [];
      sourceIds.forEach((sourceId) => {
        const source = sourceById.get(String(sourceId));
        const comments = Array.isArray(source?.["评论"]) ? source["评论"] : [];
        comments.forEach((raw) => {
          const commentId = String(raw?.["评论ID"] || "");
          const comment = commentById.get(commentId);
          if (!commentId || !comment || seenCommentIds.has(commentId) || !comment.text) return;
          seenCommentIds.add(commentId);
          extraComments.push(comment);
        });
      });
      extraComments.sort((left, right) => (Number(right.likes) || 0) - (Number(left.likes) || 0));
      const sources = sourceIds
        .map((id) => sourceById.get(String(id)))
        .filter(Boolean)
        .map(projectSource);
      return {
        rank: Number(topic.rank) || index + 1,
        title: String(topic.title || "").trim(),
        angle: String(topic.angle || "").trim(),
        audience: String(topic.audience || "").trim(),
        pain: String(topic.pain || "").trim(),
        fit: String(topic.account_fit_reason || "").trim(),
        evidence: String(topic.evidence_type || "").trim(),
        total_score: Number(topic.total_score) || 0,
        scores: {
          account_fit: Number(topic.score_account_fit) || 0,
          demand: Number(topic.score_demand) || 0,
          reference: Number(topic.score_reference) || 0,
          differentiation: Number(topic.score_differentiation) || 0,
          production: Number(topic.score_production) || 0,
        },
        sources,
        // 卡片底部那句「累计」：代表内容合起来的赞 / 藏 / 评
        likes_total: metricTotal(sources.map((item) => item.likes)),
        collects_total: metricTotal(sources.map((item) => item.collects)),
        comments_total: metricTotal(sources.map((item) => item.comments)),
        comments: [...evidenceComments, ...extraComments].slice(0, 10),
      };
    });

    const runId = String(data.run_id || latest.path.split("/")[1] || "").trim();
    const dataSource = String(data.data_source || "").trim();
    const dataSourceKey = dataSource.toLowerCase();
    const mdRelativePath = latest.path.replace(/搜索选题结果\.json$/, "搜索选题结果.md");
    const mdFile = files.find((file) => file.path === mdRelativePath);

    return {
      run_id: runId,
      generated_at: String(data.generated_at || latest.modified_at || ""),
      data_source: dataSource,
      data_source_label: TOPIC_SEARCH_DATA_SOURCE_LABELS[dataSourceKey] || dataSource,
      queries,
      topics,
      stats: {
        queries: queries.length,
        topics: topics.length,
        sources: rawSources.length,
        comments: commentTotal,
      },
      platform_counts: [...platformCounts.entries()]
        .sort((left, right) => right[1] - left[1] || left[0].localeCompare(right[0], "zh-CN")),
      json_path: latest.path,
      json_abs_path: path.join(this.workspaceRoot, ...latest.path.split("/")),
      md_path: mdFile ? mdRelativePath : "",
      md_abs_path: mdFile ? path.join(this.workspaceRoot, ...mdRelativePath.split("/")) : "",
    };
  }

  async _scanDirectory(section, diagnostics) {
    const sectionRoot = path.join(this.workspaceRoot, section.root);
    const files = [];

    const visit = async (absoluteDir, relativeDir) => {
      let entries;
      try {
        entries = await fs.readdir(absoluteDir, { withFileTypes: true });
      } catch (error) {
        if (error?.code === "ENOENT" && relativeDir === section.root) return;
        diagnostics.push(diagnostic("directory_read_failed", relativeDir, error.message));
        return;
      }
      entries.sort((left, right) => left.name.localeCompare(right.name, "zh-CN"));

      for (const entry of entries) {
        const relativePath = `${relativeDir}/${entry.name}`;
        if (!isUserVisibleRelativePath(relativePath)) continue;
        const absolutePath = path.join(absoluteDir, entry.name);
        if (entry.isSymbolicLink()) {
          diagnostics.push(diagnostic("symlink_skipped", relativePath, "符号链接不进入工作台索引"));
          continue;
        }
        if (entry.isDirectory()) {
          await visit(absolutePath, relativePath);
          continue;
        }
        if (!entry.isFile()) continue;
        if (files.length >= this.maxFiles) {
          diagnostics.push(diagnostic("file_limit_reached", section.root, `最多索引 ${this.maxFiles} 个文件`));
          return;
        }

        let stat;
        try {
          stat = await fs.lstat(absolutePath);
        } catch (error) {
          diagnostics.push(diagnostic("file_stat_failed", relativePath, error.message));
          continue;
        }
        if (!stat.isFile() || stat.isSymbolicLink()) continue;

        const extension = path.extname(entry.name).toLowerCase();
        const kind = fileKind(extension);
        const fallbackTitle = path.basename(entry.name, extension) || entry.name;
        let title = fallbackTitle;
        let searchText = `${fallbackTitle} ${relativePath}`;
        let tooLarge = false;

        if (kind === "text") {
          try {
            const cached = await this._readCachedText(relativePath, absolutePath, stat);
            tooLarge = cached.tooLarge;
            if (!cached.tooLarge) {
              title = extension === ".md" ? titleFromText(cached.content, fallbackTitle) : fallbackTitle;
              searchText = `${title} ${relativePath} ${compactSearchText(cached.content)}`.trim();
            }
          } catch (error) {
            diagnostics.push(diagnostic("text_read_failed", relativePath, error.message));
          }
        }

        files.push({
          path: relativePath,
          name: entry.name,
          title,
          section: section.id,
          section_label: section.label,
          extension: extension.slice(1),
          size: stat.size,
          modified_at: stat.mtime.toISOString(),
          modified_ms: stat.mtimeMs,
          view: kind,
          too_large: tooLarge,
          search_text: searchText,
        });
      }
    };

    await visit(sectionRoot, section.root);
    return files;
  }

  _recordsFor(relativePath, diagnostics) {
    const cached = this.cache.get(relativePath);
    if (!cached || cached.tooLarge) return [];
    try {
      return csvRecords(cached.content);
    } catch (error) {
      diagnostics.push(diagnostic("csv_entity_parse_failed", relativePath, error.message));
      return [];
    }
  }

  async _buildWorkflows(files, skills, diagnostics) {
    const fileByPath = new Map(files.map((file) => [file.path, file]));
    const benchmarkAccountRows = this._recordsFor(MASTER_TABLES.benchmark_accounts.path, diagnostics);
    const benchmarkContentRows = this._recordsFor(MASTER_TABLES.benchmark_contents.path, diagnostics);
    // 对标内容库里的行有两种来源：从对标账号链路采集登记的，和单条内容直接建档的。
    // 判定只认账号ID——账号名会随作者改名漂移（同一个账号ID 可能挂过多个名字），按名字匹配会漏。
    const benchmarkAccountIds = new Set(
      benchmarkAccountRows.map((record) => String(record["账号ID"] || "").trim()).filter(Boolean),
    );
    const ownAccountRows = this._recordsFor(MASTER_TABLES.own_accounts.path, diagnostics);
    const ownContentRows = this._recordsFor(MASTER_TABLES.own_contents.path, diagnostics);
    const topicRows = this._recordsFor(MASTER_TABLES.topics.path, diagnostics);
    const dictionaryRows = this._recordsFor(PATTERN_ASSETS.dictionary, diagnostics);
    const instanceRows = this._recordsFor(PATTERN_ASSETS.instances, diagnostics);

    // 首页「最近动态」的事件源。凡是「有正式落盘」的链路都往这里写一条：
    // 对标账号 / 对标内容 / 我方账号 / 内容生产 / 选题分析 / 内容特征 / 规律知识库 / 账号方向。
    // 时间用落盘文件的本地 mtime；只有过程记录、出厂空壳、系统类 Skill（dashboard/diagnose/update）不进。
    const activity = [];
    const recordActivity = (at, text, meta = "") => {
      if (!at || !text) return;
      activity.push({ at, text, meta });
    };

    const benchmarkContents = benchmarkContentRows.map((record, recordIndex) => {
      const fromAccount = benchmarkAccountIds.has(String(record["账号ID"] || "").trim());
      const directory = directoryFromRecord(record, [
        "内容目录路径",
        "内容分析路径",
        "视频文案路径",
      ]);
      const analysis = firstDocument([
        record["内容分析路径"],
        directory && `${directory}/内容分析.md`,
      ], fileByPath, "内容分析");
      const cover = firstDocument([record["封面图路径"], directory && `${directory}/封面图.png`], fileByPath, "封面图");
      const transcript = firstDocument([record["视频文案路径"], directory && `${directory}/视频文案.md`], fileByPath, "视频文案");
      const comments = firstDocument([record["评论路径"], directory && `${directory}/评论.md`], fileByPath, "评论");
      const item = {
        key: `${record["账号ID"] || record["账号名"] || "unknown"}::${record["内容ID"] || recordIndex}`,
        id: record["内容ID"] || "未编号",
        account_id: record["账号ID"] || "",
        account_name: record["账号名"] || "未知账号",
        title: record["标题"] || "未命名内容",
        platform: record["平台"] || "未填写",
        media_format: record["媒介形式"] || "未填写",
        published_at: record["发布时间"] || "",
        data_level: record["数据水平"] || "未填写",
        external_url: record["对外页面链接"] || "",
        metrics: {
          likes: numericValue(record["点赞数"]),
          favorites: numericValue(record["收藏数"]),
          shares: numericValue(record["分享数"]),
          comments: numericValue(record["评论数"]),
          interactions: numericValue(record["核心互动数"]),
        },
        directory_path: directory,
        cover_path: cover?.path || "",
        transcript_path: transcript?.path || "",
        transcript_word_count: transcript?.path ? visibleTextLength(this.cache.get(transcript.path)?.content || "") : 0,
        comments_path: comments?.path || "",
        analysis_path: analysis?.path || "",
        analysis_at: analysis?.modified_at || "",
        analysis_state: analysis ? "已完成" : directory ? "待分析" : "待建档",
        source: fromAccount ? "account" : "standalone",
        source_label: fromAccount ? "来自对标账号" : "独立建档",
      };
      const contentStage = latestActivity([
        { at: analysis?.modified_at, text: `完成对标内容分析「${item.title}」` },
        { at: oldestFile(directory ? childFiles(directory, files, (file) => !isProcessFile(file)) : [])?.modified_at, text: `入库对标内容「${item.title}」` },
      ]);
      if (contentStage) recordActivity(contentStage.at, contentStage.text, item.account_name);
      return {
        ...item,
        search_text: entitySearchText(item.id, item.account_name, item.title, item.platform, item.data_level, item.source_label),
      };
    });

    const benchmarkAccounts = benchmarkAccountRows.map((record, recordIndex) => {
      const related = benchmarkContents.filter((content) =>
        (record["账号ID"] && content.account_id === record["账号ID"]) ||
        (
          content.account_name === record["账号名"] &&
          (!record["平台"] || !content.platform || content.platform === record["平台"])
        ),
      );
      const accountDirectory = directoryFromRecord(record, [
        "头像路径",
        "概要分析路径",
        "详细分析路径",
        "内容规律分析路径",
        "人工先验说明路径",
        "内容列表路径",
      ]) || path.posix.dirname(normalizeStoredPath(record["图片说明路径"]) || "_");
      const documents = {
        overview: firstDocument([record["概要分析路径"], accountDirectory && `${accountDirectory}/概要分析.md`], fileByPath, "概要分析"),
        detail: firstDocument([record["详细分析路径"], accountDirectory && `${accountDirectory}/详细分析.md`], fileByPath, "详细分析"),
        prior: firstDocument([record["人工先验说明路径"], accountDirectory && `${accountDirectory}/人工先验说明.md`], fileByPath, "人工先验说明"),
        // 账号内容规律分析：对标是固定文件名；库里有路径列时优先用列。
        pattern: firstDocument([record["内容规律分析路径"], accountDirectory && `${accountDirectory}/账号内容规律分析.md`], fileByPath, "账号内容规律分析"),
        // 行动建议固定文件名，不新增账号库列；规划变化后可单独重跑覆盖。
        advice: firstDocument([accountDirectory && `${accountDirectory}/行动建议.md`], fileByPath, "行动建议"),
        contents: firstDocument([record["内容列表路径"], accountDirectory && `${accountDirectory}/内容列表.csv`], fileByPath, "内容列表"),
      };
      // 作品明细与我方账号保持同一口径：展示账号内容列表里的全部内容。
      // 已建档进对标内容库的（related 命中）可点进详情，其余置灰不可点。
      let accountContents = [];
      if (documents.contents?.path) {
        const cachedContentList = this.cache.get(normalizeStoredPath(documents.contents.path) || documents.contents.path);
        accountContents = csvRecords(cachedContentList?.content ?? "").map((entry, entryIndex) => {
          const contentDirectory = normalizeStoredPath(entry["内容目录路径"]);
          const contentKey = `${record["账号ID"] || record["账号名"] || "unknown"}::${entry["内容ID"] || entryIndex}`;
          const tracked = related.find((content) => content.key === contentKey)
            || related.find((content) => entry["内容ID"] && content.id === entry["内容ID"])
            || null;
          const transcript = firstDocument([entry["视频文案路径"], contentDirectory && `${contentDirectory}/视频文案.md`], fileByPath, "视频文案");
          return {
            key: contentKey,
            id: entry["内容ID"] || "未编号",
            account_id: record["账号ID"] || "",
            account_name: record["账号名"] || "未知账号",
            title: entry["标题"] || "未命名内容",
            platform: record["平台"] || "未填写",
            media_format: entry["媒介形式"] || "未填写",
            published_at: entry["发布时间"] || "",
            data_level: entry["数据水平"] || "未填写",
            external_url: entry["对外页面链接"] || "",
            visibility: entry["公开状态"] || "",
            has_detail: Boolean(tracked),
            metrics: {
              likes: numericValue(entry["点赞数"]),
              favorites: numericValue(entry["收藏数"]),
              shares: numericValue(entry["分享数"]),
              comments: numericValue(entry["评论数"]),
              interactions: numericValue(entry["核心互动数"]),
            },
            directory_path: contentDirectory,
            cover_path: firstDocument([entry["封面图路径"], contentDirectory && `${contentDirectory}/封面图.png`], fileByPath)?.path || "",
            transcript_path: transcript?.path || "",
            transcript_word_count: transcript?.path ? visibleTextLength(this.cache.get(transcript.path)?.content || "") : 0,
            analysis_path: tracked?.analysis_path || "",
            analysis_state: tracked ? tracked.analysis_state : "待采集详情",
          };
        });
      }
      // 历史工作区或未抓到内容列表的账号：回落到对标内容库里已建档的内容，
      // 避免账号档案显示为空。
      if (!accountContents.length) {
        accountContents = related.map((content) => ({ ...content, has_detail: true }));
      }
      const imageDirectory = normalizeStoredPath(record["图片说明路径"]);
      const avatar = firstDocument([
        record["头像路径"],
        accountDirectory && `${accountDirectory}/头像.png`,
        accountDirectory && `${accountDirectory}/头像.jpg`,
        accountDirectory && `${accountDirectory}/头像.jpeg`,
        accountDirectory && `${accountDirectory}/头像.webp`,
        // Older workspaces kept the avatar in the image-reference directory.
        imageDirectory && `${imageDirectory}/头像.png`,
        imageDirectory && `${imageDirectory}/头像.jpg`,
        imageDirectory && `${imageDirectory}/头像.jpeg`,
        imageDirectory && `${imageDirectory}/头像.webp`,
      ], fileByPath, "账号头像");
      const images = childFiles(imageDirectory, files, (file) => file.view === "image")
        .filter((file) => file.path !== avatar?.path)
        .slice(0, 8)
        .map((file) => file.path);
      const item = {
        id: record["账号ID"] || "未编号",
        name: record["账号名"] || "未命名账号",
        platform: record["平台"] || "未填写",
        intro: record["简介"] || "",
        gender: record["性别"] || "未填写",
        followers: numericValue(record["粉丝数"]),
        likes: numericValue(record["获赞数"]),
        // 标签在账号库里用顿号连写（"33岁、浙江杭州、产品经理"），逗号/分号/斜杠也认
        tags: String(record["标签"] || "").split(/[,，、;；/|]/).map((item) => item.trim()).filter(Boolean),
        homepage: record["主页链接"] || "",
        quality: displayStatus(record["质量判断"]),
        fit: displayStatus(record["适配判断"]),
        recorded_at: record["录入时间"] || record["建档时间"] || record["创建时间"] || "",
        record_order: recordIndex,
        notes: record["备注"] || "",
        documents,
        avatar_path: avatar?.path || "",
        images,
        content_count: accountContents.length,
        analyzed_content_count: accountContents.filter((content) => content.analysis_state === "已完成").length,
        account_contents: accountContents,
      };
      const accountStage = latestActivity([
        { at: documents.pattern?.modified_at, text: `完成对标账号「${item.name}」的内容规律分析` },
        { at: documents.detail?.modified_at, text: `完成对标账号「${item.name}」的详细分析` },
        { at: documents.contents?.modified_at, text: `采集对标账号「${item.name}」的作品明细` },
        { at: documents.overview?.modified_at, text: `完成对标账号「${item.name}」的概要分析` },
        {
          at: oldestFile(accountDirectory ? childFiles(accountDirectory, files, (file) => !isProcessFile(file)) : [])?.modified_at,
          text: `入库对标账号「${item.name}」`,
        },
      ]);
      if (accountStage) recordActivity(accountStage.at, accountStage.text, item.platform);
      return { ...item, search_text: entitySearchText(item.id, item.name, item.platform, item.intro, item.tags) };
    });

    const ownContents = [];
    for (const record of ownContentRows) {
      const directory = directoryFromRecord(record, ["内容目录路径", "内容生产记录路径", "封面图路径", "视频文案路径"]);
      const productionRecord = firstDocument([
        record["内容生产记录路径"],
        directory && `${directory}/内容生产记录.md`,
      ], fileByPath, "内容生产记录");
      const transcript = firstDocument([record["视频文案路径"], directory && `${directory}/视频文案.md`], fileByPath, "视频文案");
      const cachedProduction = productionRecord
        ? this.cache.get(normalizeStoredPath(productionRecord.path) || productionRecord.path)
        : null;
      const productionData = cachedProduction && !cachedProduction.tooLarge
        ? productionRecordData(cachedProduction.content)
        : {};
      const state = directory
        ? await this._readInternalJson(`${directory}/过程记录/当前阶段状态.json`)
        : null;
      const effectiveStatus = productionStatus(record["当前状态"], Boolean(productionRecord));
      // 参考内容：内容主表的「参考内容分析路径」→ 命中同一条对标内容（与选题卡同一口径）
      const referenceAnalysis = firstDocument([record["参考内容分析路径"]], fileByPath, "参考内容分析");
      const referenceContent = benchmarkContents.find((content) => content.analysis_path === referenceAnalysis?.path);
      const reviewVersions = await this._readReviewVersions(directory);
      const item = {
        id: record["内容ID"] || "未编号",
        topic_id: record["选题ID"] || "",
        topic_name: record["选题名称"] || "",
        title: record["标题"] || "未命名内容",
        core: productionData.theme || "",
        media_format: record["媒介形式"] || productionData.media_format || "未填写",
        content_type: productionData.content_type || "",
        primary_goal: productionData.primary_goal || "",
        produced_at: productionRecord?.modified_at || "",
        status: effectiveStatus,
        stored_status: displayStatus(record["当前状态"], "未填写"),
        workflow_stage: String(state?.["当前阶段"] || "").trim(),
        workflow_status: String(state?.["流程状态"] || "").trim(),
        directory_path: directory,
        production_record_path: productionRecord?.path || "",
        reference_analysis_path: referenceAnalysis?.path || "",
        reference_content_key: referenceContent?.key || "",
        cover_path: firstDocument([record["封面图路径"], directory && `${directory}/封面图.png`], fileByPath)?.path || "",
        transcript_path: transcript?.path || "",
        transcript_word_count: transcript?.path ? visibleTextLength(this.cache.get(transcript.path)?.content || "") : 0,
        review_versions: reviewVersions,
        review_report: reviewVersions[reviewVersions.length - 1] || null,
      };
      // 内容生产：落盘是内容生产记录；五阶段是同一篇内容的连续推进，只记一条。
      if (productionRecord) recordActivity(productionRecord.modified_at, `完成内容生产「${item.title}」`, item.media_format);
      ownContents.push({ ...item, search_text: entitySearchText(item.id, item.topic_name, item.title, item.status, item.workflow_stage, item.content_type, item.primary_goal) });
    }

    const publications = [];
    for (const content of ownContents) {
      const prefix = content.directory_path ? `${content.directory_path}/发布记录/` : "";
      const recordsByDirectory = new Map();
      if (prefix) {
        for (const file of files) {
          if (!file.path.startsWith(prefix) || !/\d{4}年\d{1,2}月\d{1,2}日复盘记录\.md$/.test(file.name)) continue;
          const relative = file.path.slice(prefix.length).split("/");
          if (relative.length !== 2) continue;
          const current = recordsByDirectory.get(relative[0]);
          if (!current || publicationRecordTimestamp(file.name) > publicationRecordTimestamp(current.name)) {
            recordsByDirectory.set(relative[0], file);
          }
        }
      }
      for (const [directoryName, recordFile] of recordsByDirectory) {
        const cached = this.cache.get(recordFile.path);
        if (!cached || cached.tooLarge) continue;
        const parsed = publicationRecordData(cached.content);
        const publicationDirectory = path.posix.dirname(recordFile.path);
        const platform = parsed.fields["平台"] || "未填写";
        const accountId = parsed.fields["账号ID"] || "";
        const nativeId = parsed.fields["平台原生内容ID"] || "";
        const stableKey = [platform, accountId, nativeId].every(Boolean)
          ? `${platform}|${accountId}|${nativeId}`
          : directoryName;
        const metric = (name) => numericValue(parsed.metrics[name] ?? parsed.fields[name]);
        const likes = metric("点赞数");
        const comments = metric("评论数");
        const favorites = metric("收藏数");
        const shares = metric("分享数");
        const explicitInteractions = metric("核心互动数");
        const knownMetrics = [likes, comments, favorites, shares].filter((value) => value !== null);
        const item = {
          id: stableKey,
          content_id: content.id,
          title: parsed.fields["内容标题"] || content.title,
          account_id: accountId,
          account_name: parsed.fields["账号名"] || "未知账号",
          platform,
          platform_native_content_id: nativeId,
          media_format: parsed.fields["媒介形式"] || content.media_format,
          published_at: parsed.fields["发布时间"] || "",
          analysis_time: parsed.fields["分析时间"] || recordFile.name.replace(/复盘记录\.md$/, ""),
          external_url: parsed.fields["对外页面链接"] || "",
          data_level: parsed.metrics["数据水平"] || parsed.fields["数据水平"] || "未填写",
          status: "已发布",
          record_path: recordFile.path,
          directory_path: publicationDirectory,
          cover_path: firstDocument([`${publicationDirectory}/封面图.png`], fileByPath)?.path || "",
          transcript_path: firstDocument([`${publicationDirectory}/发布正文.md`], fileByPath)?.path || "",
          metrics: {
            likes,
            comments,
            favorites,
            shares,
            interactions: explicitInteractions ?? (knownMetrics.length ? knownMetrics.reduce((sum, value) => sum + value, 0) : null),
          },
        };
        publications.push({ ...item, search_text: entitySearchText(item.title, item.account_name, item.platform, item.status, item.analysis_time) });
      }
    }

    // Legacy workspaces stored one publication directly on the content row.
    for (const [index, record] of ownContentRows.entries()) {
      if (!/^(?:已发布|已复盘)$/.test(record["当前状态"] || "")) continue;
      const content = ownContents[index];
      if (!content || publications.some((item) => item.content_id === content.id)) continue;
      const review = firstDocument([record["复盘总结路径"], content.directory_path && `${content.directory_path}/复盘总结.md`], fileByPath, "复盘总结");
      const item = {
        id: `legacy:${content.id}`,
        content_id: content.id,
        title: content.title,
        account_id: record["账号ID"] || "",
        account_name: record["账号名"] || "未知账号",
        platform: record["平台"] || "未填写",
        platform_native_content_id: "",
        media_format: content.media_format,
        published_at: record["发布时间"] || "",
        analysis_time: record["发布数据分析时间"] || "",
        external_url: record["对外页面链接"] || "",
        data_level: record["数据水平"] || "未填写",
        status: "已发布",
        record_path: review?.path || "",
        directory_path: content.directory_path,
        cover_path: content.cover_path,
        transcript_path: content.transcript_path,
        metrics: {
          likes: numericValue(record["点赞数"]),
          comments: numericValue(record["评论数"]),
          favorites: numericValue(record["收藏数"]),
          shares: numericValue(record["分享数"]),
          interactions: numericValue(record["核心互动数"]),
        },
      };
      publications.push({ ...item, search_text: entitySearchText(item.title, item.account_name, item.platform, item.status) });
    }

    // 发布记录：落盘是发布记录目录下的复盘记录（取数阶段写），工作台只读取数据部分。
    for (const publication of publications) {
      const recordFile = publication.record_path ? fileByPath.get(publication.record_path) : null;
      if (!recordFile) continue;
      recordActivity(
        recordFile.modified_at,
        `完成「${publication.title}」的发布数据获取`,
        publication.platform,
      );
    }

    const topics = topicRows.map((record) => {
      const linkedContents = ownContents.filter((content) =>
        (record["选题ID"] && content.topic_id === record["选题ID"]) ||
        (normalizeStoredPath(record["内容生产记录路径"]) && content.production_record_path === normalizeStoredPath(record["内容生产记录路径"])),
      );
      const linkedContent = linkedContents[0] || null;
      const analysis = firstDocument([record["选题分析记录路径"]], fileByPath, "选题分析");
      const referenceAnalysis = firstDocument([record["参考内容分析路径"]], fileByPath, "参考内容分析");
      const referenceContent = benchmarkContents.find((content) => content.analysis_path === referenceAnalysis?.path);
      const storedProductionPath = normalizeStoredPath(record["内容生产记录路径"]);
      const item = {
        id: record["选题ID"] || "未编号",
        name: record["选题名称"] || "未命名选题",
        source: record["选题来源"] || "未填写",
        status: displayStatus(record["当前状态"]),
        recommendation: record["是否建议做"] || "待判断",
        reason: record["判断理由"] || "",
        theme: record["主题"] || "",
        audience: record["目标用户"] || "",
        pain_point: record["核心痛点"] || "",
        benefit_promise: record["收益承诺"] || "",
        core_proposition: record["核心命题"] || "",
        angle: record["切口"] || "",
        analysis_path: analysis?.path || "",
        reference_analysis_path: referenceAnalysis?.path || "",
        reference_content_key: referenceContent?.key || "",
        recorded_at: record["灵感记录时间"] || record["记录时间"] || "",
        updated_at: record["更新时间"] || record["灵感记录时间"] || record["记录时间"] || "",
        reference_url: record["参考内容链接"] || "",
        reference_description: record["参考内容说明"] || "",
        production_path: linkedContent?.production_record_path || storedProductionPath,
        content_id: linkedContent?.id || "",
        content_name: linkedContent?.title || "",
        contents: linkedContents,
        content_ids: linkedContents.map((content) => content.id),
        content_count: linkedContents.length,
        has_created_content: linkedContents.length > 0 || Boolean(storedProductionPath),
      };
      if (analysis) recordActivity(analysis.modified_at, `完成选题分析「${item.name}」`, item.source);
      return { ...item, search_text: entitySearchText(item.id, item.name, item.source, item.status, item.theme, item.audience) };
    });

    const ownAccounts = ownAccountRows.map((record) => {
      const imageDirectory = normalizeStoredPath(record["图片说明路径"]);
      const accountDirectory = directoryFromRecord(record, [
        "头像路径",
        "概要分析路径",
        "详细分析路径",
        "人工先验说明路径",
        "内容列表路径",
        "图片说明路径",
        "过程记录路径",
      ]);
      const latestDiagnosis = latestDirectDocument(
        accountDirectory,
        files,
        // 只认「账号详细诊断」这一种：账号目录里还有账号内容规律分析，放宽会把那份当诊断。
        (file) => file.view === "text" && /账号详细诊断\.md$/i.test(file.path),
        "账号详细诊断",
      );
      const latestPattern = latestDirectDocument(
        accountDirectory,
        files,
        (file) => file.view === "text" && /账号内容规律分析\.md$/i.test(file.path),
        "账号内容规律分析",
      );
      const latestActionList = latestDirectDocument(
        accountDirectory,
        files,
        (file) => file.view === "text" && /账号改进行动清单\.md$/i.test(file.path),
        "账号改进行动清单",
      );
      let accountContents = [];
      const contentListFile = firstDocument([
        record["内容列表路径"],
        accountDirectory && `${accountDirectory}/内容列表.csv`,
      ], fileByPath, "内容列表");
      if (contentListFile?.path) {
        const cached = this.cache.get(normalizeStoredPath(contentListFile.path) || contentListFile.path);
        const contentRecords = csvRecords(cached?.content ?? "");
        accountContents = contentRecords.map((entry, entryIndex) => {
          const contentDirectory = normalizeStoredPath(entry["内容目录路径"]);
          const transcript = firstDocument([entry["视频文案路径"], contentDirectory && `${contentDirectory}/视频文案.md`], fileByPath, "视频文案");
          const hasDetail = Boolean(transcript?.path || entry["内容目录路径"]);
          return {
            key: `${record["账号ID"] || record["账号名"] || "unknown"}::${entry["内容ID"] || entryIndex}`,
            id: entry["内容ID"] || "未编号",
            account_id: record["账号ID"] || "",
            account_name: record["账号名"] || "未知账号",
            title: entry["标题"] || "未命名内容",
            platform: record["平台"] || "未填写",
            media_format: entry["媒介形式"] || "未填写",
            published_at: entry["发布时间"] || "",
            data_level: entry["数据水平"] || "未填写",
            external_url: entry["对外页面链接"] || "",
            visibility: entry["公开状态"] || "未知",
            has_detail: hasDetail,
            metrics: {
              likes: numericValue(entry["点赞数"]),
              favorites: numericValue(entry["收藏数"]),
              shares: numericValue(entry["分享数"]),
              comments: numericValue(entry["评论数"]),
              interactions: numericValue(entry["核心互动数"]),
            },
            directory_path: contentDirectory,
            cover_path: firstDocument([entry["封面图路径"], contentDirectory && `${contentDirectory}/封面图.png`], fileByPath)?.path || "",
            transcript_path: transcript?.path || "",
            transcript_word_count: transcript?.path ? visibleTextLength(this.cache.get(transcript.path)?.content || "") : 0,
            analysis_path: "",
            analysis_state: hasDetail ? "已完成" : "待采集详情",
          };
        });
      }
      const avatar = firstDocument([
        record["头像路径"],
        accountDirectory && `${accountDirectory}/头像.png`,
        accountDirectory && `${accountDirectory}/头像.jpg`,
        accountDirectory && `${accountDirectory}/头像.jpeg`,
        accountDirectory && `${accountDirectory}/头像.webp`,
        // Keep legacy account folders readable during the schema migration.
        imageDirectory && `${imageDirectory}/头像.png`,
        imageDirectory && `${imageDirectory}/头像.jpg`,
        imageDirectory && `${imageDirectory}/头像.jpeg`,
        imageDirectory && `${imageDirectory}/头像.webp`,
      ], fileByPath, "账号头像");
      // 创作者中心数据：账号概览与内容分析分别落在同一采集批次目录下，
      // 前端按需读取；没有采集过的账号为 null，不补 0。
      const creatorCenterDirectory = accountDirectory && `${accountDirectory}/创作者中心数据`;
      const creatorOverview = latestNestedDocument(creatorCenterDirectory, files, "账号概览.json", "创作者中心账号概览");
      const creatorContents = latestNestedDocument(creatorCenterDirectory, files, "内容分析.json", "创作者中心内容分析");
      const item = {
        id: record["账号ID"] || "未编号",
        name: record["账号名"] || "未命名账号",
        platform: record["平台"] || "未填写",
        intro: record["简介"] || "",
        gender: record["性别"] || "未填写",
        followers: numericValue(record["粉丝数"]),
        likes: numericValue(record["获赞数"]),
        // 标签在账号库里用顿号连写（"33岁、浙江杭州、产品经理"），逗号/分号/斜杠也认
        tags: String(record["标签"] || "").split(/[,，、;；/|]/).map((item) => item.trim()).filter(Boolean),
        homepage: record["主页链接"] || "",
        notes: record["备注"] || "",
        documents: {
          overview: firstDocument([
            record["概要分析路径"],
            accountDirectory && `${accountDirectory}/概要分析.md`,
          ], fileByPath, "概要分析"),
          detail: firstDocument([
            latestDiagnosis?.path,
            record["详细分析路径"],
            accountDirectory && `${accountDirectory}/详细分析.md`,
          ], fileByPath, latestDiagnosis?.title || "详细分析"),
          prior: firstDocument([
            record["人工先验说明路径"],
            accountDirectory && `${accountDirectory}/人工先验说明.md`,
          ], fileByPath, "人工先验说明"),
          // 我方那份带日期，按「最新一份」找；同日覆盖，不会有两条。
          pattern: firstDocument([
            latestPattern?.path,
            accountDirectory && `${accountDirectory}/账号内容规律分析.md`,
          ], fileByPath, latestPattern?.title || "账号内容规律分析"),
          advice: firstDocument([
            latestActionList?.path,
            accountDirectory && `${accountDirectory}/行动建议.md`,
          ], fileByPath, latestActionList?.title || "账号改进行动清单"),
        },
        creator_center: {
          overview: creatorOverview,
          contents: creatorContents,
        },
        avatar_path: avatar?.path || "",
        images: childFiles(imageDirectory, files, (file) => file.view === "image")
          .filter((file) => file.path !== avatar?.path)
          .slice(0, 8)
          .map((file) => file.path),
        content_count: accountContents.length,
        published_count: accountContents.filter((content) => content.visibility === "公开").length,
        account_contents: accountContents,
      };
      /* 首页账号洞察：这份账号最新那份详细诊断的结构化版本。
         诊断正文已经在缓存里就顺手解析，没有就留 null（前端退回「待诊断」）。 */
      const diagnosisEntry = item.documents.detail?.path
        ? this.cache.get(normalizeStoredPath(item.documents.detail.path) || item.documents.detail.path)
        : null;
      item.diagnosis = parseDiagnosisDocument(diagnosisEntry?.content || "");
      item.diagnosis_at = item.documents.detail?.modified_at || "";
      /* 行动建议用「账号改进行动清单」，不是诊断里的按维度建议 */
      const adviceEntry = item.documents.advice?.path
        ? this.cache.get(normalizeStoredPath(item.documents.advice.path) || item.documents.advice.path)
        : null;
      item.action_list = parseActionList(adviceEntry?.content || "");
      item.action_list_at = item.documents.advice?.modified_at || "";
      /* 首页「我的账号内容技巧」：规律分析里那句账号级打法。
         索引里一次算好，前端同步渲染 —— 前端自己异步读文件会时有时无。 */
      const patternEntry = item.documents.pattern?.path
        ? this.cache.get(normalizeStoredPath(item.documents.pattern.path) || item.documents.pattern.path)
        : null;
      item.content_technique = patternSummarySentence(patternEntry?.content || "");
      const ownStage = latestActivity([
        { at: item.documents.pattern?.modified_at, text: `完成账号内容规律分析「${item.name}」` },
        { at: item.documents.detail?.modified_at, text: `完成账号详细诊断「${item.name}」` },
        { at: contentListFile?.modified_at, text: `采集账号「${item.name}」的作品明细` },
        { at: item.documents.overview?.modified_at, text: `完成账号概要分析「${item.name}」` },
        {
          at: oldestFile(accountDirectory ? childFiles(accountDirectory, files, (file) => !isProcessFile(file)) : [])?.modified_at,
          text: `入库我的账号「${item.name}」`,
        },
      ]);
      if (ownStage) recordActivity(ownStage.at, ownStage.text, item.platform);
      return { ...item, search_text: entitySearchText(item.id, item.name, item.platform, item.intro, item.tags) };
    });

    // 内容特征入库 / 内容规律知识库 / 账号方向：都以「正式文件 + 有实质内容」为准，
    // 出厂预置的空表空壳不产生动态。
    const instanceFile = fileByPath.get(PATTERN_ASSETS.instances);
    if (instanceFile && instanceRows.length) {
      recordActivity(instanceFile.modified_at, "更新内容特征库", `${instanceRows.length} 条特征实例`);
    }
    const reportFile = fileByPath.get(PATTERN_ASSETS.report);
    if (reportFile && hasLevelTwoHeading(this.cache.get(PATTERN_ASSETS.report)?.content)) {
      recordActivity(reportFile.modified_at, "更新内容规律知识库");
    }
    const mediaPlanPath = "我方资料/我方自媒体规划.md";
    const mediaPlanFile = fileByPath.get(mediaPlanPath);
    if (mediaPlanFile && hasLevelTwoHeading(this.cache.get(mediaPlanPath)?.content)) {
      recordActivity(mediaPlanFile.modified_at, "更新账号方向");
    }

    // 创作看板只算真正进过内容生产的内容（有内容生产记录）；
    // 账号诊断采集回来的历史已发布内容不算创作中的内容。
    const productionContents = ownContents.filter((content) => content.production_record_path);
    const creating = productionContents.filter((content) => content.status === "生产中");
    const completed = productionContents.filter((content) => content.status === "已完成");
    const linkedTopicIds = new Set(ownContents.map((content) => content.topic_id).filter(Boolean));
    const ideaTopics = topics.filter((topic) => !linkedTopicIds.has(topic.id) && !topic.has_created_content);
    const boardItem = (kind, item) => ({
      kind,
      id: item.id,
      title: kind === "topic" ? item.name : item.title,
      status: item.status,
      stage: item.workflow_stage || "",
      topic_id: kind === "topic" ? item.id : item.topic_id,
      content_id: kind === "content" ? item.id : item.content_id,
    });
    const creationBoard = {
      columns: [
        { id: "ideas", label: "待创作", items: ideaTopics.map((item) => boardItem("topic", item)) },
        { id: "creating", label: "创作中", items: creating.map((item) => boardItem("content", item)) },
        { id: "completed", label: "已完成", items: completed.map((item) => boardItem("content", item)) },
      ],
    };

    const publicSkills = skills.filter((skill) => skill.visibility === "public");

    return {
      overview: {
        metrics: {
          benchmark_accounts: benchmarkAccounts.length,
          benchmark_contents: benchmarkContents.length,
          analyzed_benchmark_contents: benchmarkContents.filter((content) => content.analysis_state === "已完成").length,
          account_benchmark_contents: benchmarkContents.filter((content) => content.source === "account").length,
          standalone_benchmark_contents: benchmarkContents.filter((content) => content.source === "standalone").length,
          topics: topics.length,
          creating_contents: creating.length,
          completed_contents: completed.length,
          published_contents: publications.length,
        },
        alerts: [
          { id: "creating", label: "内容正在创作", count: creating.length, target: "creation-board" },
        ],
      },
      insight: { benchmark_accounts: benchmarkAccounts, benchmark_contents: benchmarkContents },
      patterns: {
        report: documentReference(PATTERN_ASSETS.report, fileByPath, "内容规律知识库"),
        dictionary: patternAsset(PATTERN_ASSETS.dictionary, fileByPath, dictionaryRows, (record) => {
          const item = {
            id: record["特征ID"] || "",
            level: record["所属层级"] || "",
            category: record["特征小类"] || "",
            label: record["标准标签"] || "未命名特征",
            definition: record["定义"] || "",
            stages: patternStages(record),
            // 特征删不掉、只能停用，所以「状态」是用户判断自己认可哪些做法的唯一依据。
            status: record["状态"] || "",
          };
          return {
            ...item,
            search_text: entitySearchText(item.id, item.label, item.level, item.category, item.definition),
          };
        }),
        instances: patternAsset(PATTERN_ASSETS.instances, fileByPath, instanceRows, (record) => {
          const item = {
            id: record["特征实例ID"] || "",
            title: record["标题"] || record["内容ID"] || "未命名内容",
            source: record["样本来源"] || "",
            platform: record["平台"] || "",
            label: record["标准标签"] || "未命名特征",
            active: record["当前有效"] || "",
            adopted: record["实际采用状态"] || "",
            // 首页「内容拆解」按 特征小类 分组统计我方内容，需要这三列
            category: record["特征小类"] || "",
            content_id: record["内容ID"] || "",
            account_id: record["账号ID"] || "",
          };
          return { ...item, search_text: entitySearchText(item.id, item.title, item.label, item.source, item.platform) };
        }),
      },
      self: {
        media_plan: documentReference(mediaPlanPath, fileByPath, "我的方向"),
        direction: await this._readOwnDirection(),
        // 首页账号方向卡（定位 / 内容主线 / 需要验证）—— 来源 我方自媒体规划.md
        direction_plan: await this._readMediaPlanDirection(),
        own_accounts: ownAccounts,
        // 新手引导前两步只看「有没有真的生成过」，不看文件在不在（出厂都预置了空壳）。
        onboarding: {
          survey_filled: surveyFilled(this.cache.get("我方资料/调查问卷.md")?.content),
          // 问卷答案原文：首页「账号方向」没有正式正文时先摆上去
          survey_answers: readSurveyAnswers(this.cache.get("我方资料/调查问卷.md")?.content),
          survey_modified_at: this.cache.get("我方资料/调查问卷.md")?.modified_at || "",
          media_plan_ready: hasLevelTwoHeading(this.cache.get(mediaPlanPath)?.content),
        },
        // AI 那边的引导进度（哪一步真的做完了）。没有账本时为 null，界面退回上面的文件判断。
        guide: await this._readGuideState(),
      },
      production: {
        topics,
        own_contents: ownContents,
        creation_board: creationBoard,
        publications,
        recommended_topics: await this._buildRecommendedTopics(),
        topic_search: this._buildTopicSearch(files, diagnostics),
      },
      // 首页「最近动态」：时间最新的排前面，渲染条数由工作台决定。
      activity: activity.sort((left, right) => (activityTimestamp(right.at) || 0) - (activityTimestamp(left.at) || 0)),
      skills: publicSkills,
    };
  }

  async build() {
    const diagnostics = [];
    const sectionFiles = [];
    for (const section of BROWSE_SECTIONS) {
      const files = await this._scanDirectory(section, diagnostics);
      sectionFiles.push({ ...section, files });
    }
    const files = sectionFiles.flatMap((section) => section.files);
    const livePaths = new Set(files.map((file) => file.path));
    for (const cacheKey of this.cache.keys()) {
      if (!cacheKey.startsWith("@internal:") && !livePaths.has(cacheKey)) this.cache.delete(cacheKey);
    }

    const summary = {};
    for (const [key, config] of Object.entries(MASTER_TABLES)) {
      const file = files.find((item) => item.path === config.path);
      if (!file) {
        summary[key] = { label: config.label, available: false, count: null, statuses: {} };
        diagnostics.push(diagnostic("master_table_missing", config.path, `${config.label}主表不存在`));
        continue;
      }
      try {
        const cached = this.cache.get(config.path);
        if (!cached || cached.tooLarge) throw createError("csv_too_large");
        summary[key] = { label: config.label, ...csvSummary(cached.content, config.statusColumn) };
      } catch (error) {
        summary[key] = { label: config.label, available: false, count: null, statuses: {} };
        diagnostics.push(diagnostic("csv_parse_failed", config.path, error.message));
      }
    }

    const skills = [];
    for (const file of files.filter((item) => item.path.startsWith("skills/") && item.name === "SKILL.md")) {
      const cached = this.cache.get(file.path);
      if (!cached || cached.tooLarge) continue;
      const metadata = parseFrontMatter(cached.content);
      const name = metadata.name || file.path.split("/")[1] || "unknown";
      skills.push({
        name,
        display_name: titleFromText(cached.content, name),
        description: metadata.description || "暂无说明",
        path: file.path,
        visibility: isInternalSkill(cached.content) ? "internal" : "public",
      });
    }
    const skillCatalogFile = files.find((file) => file.path === SKILL_CATALOG_PATH);
    const skillCatalog = skillCatalogFile && this.cache.get(SKILL_CATALOG_PATH) && !this.cache.get(SKILL_CATALOG_PATH).tooLarge
      ? parseSkillCatalog(this.cache.get(SKILL_CATALOG_PATH).content)
      : { categories: [], byName: new Map() };
    const categorizedSkills = skills.map((skill) => {
      const catalogEntry = skillCatalog.byName.get(skill.name);
      return {
        ...skill,
        display_name: catalogEntry?.display_name || skill.display_name,
        description: catalogEntry?.description || skill.description,
        category: catalogEntry?.category || FALLBACK_SKILL_CATEGORY.label,
        category_id: catalogEntry?.category_id || FALLBACK_SKILL_CATEGORY.id,
        category_description: catalogEntry?.category_description || FALLBACK_SKILL_CATEGORY.description,
      };
    }).sort((left, right) => {
      const categoryOrder = left.category_id.localeCompare(right.category_id, "en");
      return categoryOrder || left.name.localeCompare(right.name, "en");
    });
    summary.skills = {
      label: "Skills",
      available: true,
      count: categorizedSkills.length,
      statuses: {
        "公开入口": categorizedSkills.filter((item) => item.visibility === "public").length,
        "内部阶段": categorizedSkills.filter((item) => item.visibility === "internal").length,
      },
    };

    const workflows = await this._buildWorkflows(files, categorizedSkills, diagnostics);
    workflows.skill_catalog = {
      path: skillCatalogFile?.path || "",
      title: skillCatalogFile?.title || "Skill 分类说明",
      categories: skillCatalog.categories,
    };

    const packageInfo = (await this._readInternalJson(".meiling/installation.json")) || {};
    const workspace = {
      name: String(packageInfo.customer_workspace_name || path.basename(this.workspaceRoot)),
      version: String(packageInfo.current_version || "未知"),
      // 分版已取消（2026-11-12），installation.json 里不再有 edition，这里也就不再往外带。
    };
    const sections = sectionFiles.map((section) => ({
      id: section.id,
      label: section.label,
      root: section.root,
      file_count: section.files.length,
    }));
    const dataFiles = files.filter((file) => file.section !== "skills");
    const recentFiles = [...(dataFiles.length > 0 ? dataFiles : files)]
      .sort((left, right) => right.modified_ms - left.modified_ms || left.path.localeCompare(right.path, "zh-CN"))
      .slice(0, 12)
      .map(({ modified_ms: _modifiedMs, ...file }) => file);
    const publicFiles = files.map(({ modified_ms: _modifiedMs, ...file }) => file);
    const fingerprint = jsonFingerprint({
      files: files.map((file) => [file.path, file.size, file.modified_ms]),
      summary,
      skills: categorizedSkills,
      workflows,
      diagnostics,
    });

    return {
      schema_version: "3",
      generated_at: this.now().toISOString(),
      fingerprint,
      workspace,
      summary,
      sections,
      files: publicFiles,
      skills: categorizedSkills,
      workflows,
      recent_files: recentFiles,
      diagnostics,
    };
  }
}
