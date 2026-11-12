"use strict";

const state = {
  snapshot: null,
  currentView: "home",
  eventSource: null,
  eventsPaused: false,
  eventsDisabled: false,
  listQuery: "",
  skillCategory: "all",
  benchmarkAccountFilters: {
    platform: "",
    fit: "",
    sort: "recorded-desc",
  },
  topicFilters: {
    judgment: "",
    production: "",
    sort: "recorded-desc",
  },
  contentStage: "",
  patternTab: "report",
  patternDictCategory: "all",
  patternLevel: "all",
  detailRevision: 0,
  toastTimer: null,
  // 问卷改完回来时提示「账号方向没跟着变」；只在本轮会话里显示，刷新资料重建页面也不丢
  surveyNotice: false,
  // 刚保存问卷的时间：快照还没刷回来前，用它判断「规划旧于问卷」
  surveyUpdatedAt: 0,
  // 用户点过「暂不调整 / 暂不处理」的那组状态；状态变了才重新提醒
  baselineNoticeDismissedKey: "",
  // 首页问卷刚提交：快照还没刷回来之前也按「已填」处理，别让用户重复填
  homeSurveyDone: false,
};

const VIEW_META = {
  home: ["TODAY", "工作台", "从真实资料汇总当前进度"],
  "benchmark-accounts-v2": ["INSIGHT", "对标账号", "从账号进入内容样本与分析"],
  "benchmark-contents": ["INSIGHT", "对标内容", "查看样本数据、原文与正式分析"],
  patterns: ["INSIGHT", "爆款规律", "规律分析与规律词典"],
  "media-plan": ["PLAN", "账号方向", "结合自身条件、用户需求和市场竞争，明确服务谁、做什么内容，以及是否和如何变现。"],
  "own-accounts": ["SELF", "我的账号", "账号数据、素材与正式发布"],
  "topic-search": ["PRODUCTION", "搜索选题", "按账号方向搜平台，从真实评论里挑出 9 条可以拍的选题"],
  topics: ["PRODUCTION", "选题分析", "按账号方向和对标规律挑选选题，并给出能不能做的判断"],
  "creation-board": ["SCRIPT", "脚本创作", "查看全部脚本与当前阶段"],
  publications: ["PUBLISH", "发布记录", "按平台、账号和原生内容区分的正式发布"],
  skills: ["SKILLS", "技能", "当前可直接调用的公开能力，覆盖从规划到发布后再回到规律的完整链路"],
  settings: ["SETTINGS", "设置", "接口配置与本机偏好"],
};

const elements = {
  closeDetail: document.querySelector("#close-detail"),
  closeImagePreview: document.querySelector("#close-image-preview"),
  detailBody: document.querySelector("#detail-body"),
  detailKicker: document.querySelector("#detail-kicker"),
  detailPanel: document.querySelector("#detail-panel"),
  detailTitle: document.querySelector("#detail-title"),
  diagnosticNotice: document.querySelector("#diagnostic-notice"),
  menuButton: document.querySelector("#menu-button"),
  imagePreviewImage: document.querySelector("#image-preview-image"),
  imagePreviewPanel: document.querySelector("#image-preview-panel"),
  imagePreviewTitle: document.querySelector("#image-preview-title"),
  pageKicker: document.querySelector("#page-kicker"),
  pageSubtitle: document.querySelector("#page-subtitle"),
  pageTitle: document.querySelector("#page-title"),
  primaryNav: document.querySelector("#primary-nav"),
  reloadSnapshot: document.querySelector("#reload-snapshot"),
  sidebarScrim: document.querySelector("#sidebar-scrim"),
  snapshotTime: document.querySelector("#snapshot-time"),
  toast: document.querySelector("#toast"),
  promptCopy: document.querySelector("#tpl-prompt-copy"),
  promptError: document.querySelector("#tpl-prompt-error"),
  ideaError: document.querySelector("#tpl-idea-error"),
  drawerError: document.querySelector("#tpl-drawer-error"),
  confirmError: document.querySelector("#tpl-confirm-error"),
  promptDialog: document.querySelector("#topic-prompt-dialog"),
  promptFieldIdea: document.querySelector("#tpl-field-idea"),
  promptFieldRef: document.querySelector("#tpl-field-ref"),
  promptForm: document.querySelector("#tpl-prompt-form"),
  promptIdea: document.querySelector("#tpl-prompt-idea"),
  promptIdeaLabel: document.querySelector("#tpl-idea-label"),
  promptIntro: document.querySelector("#tpl-prompt-intro"),
  promptExtra: document.querySelector("#tpl-prompt-extra"),
  promptRef: document.querySelector("#tpl-prompt-ref"),
  promptResult: document.querySelector("#tpl-prompt-result"),
  promptText: document.querySelector("#tpl-prompt-text"),
  promptTitle: document.querySelector("#tpl-prompt-title"),
  promptCancel: document.querySelector("#tpl-prompt-cancel"),
  confirmTitle: document.querySelector("#tpl-confirm-title"),
  refreshDialog: document.querySelector("#pattern-refresh-dialog"),
  refreshBody: document.querySelector("#pattern-refresh-body"),
  refreshResult: document.querySelector("#pattern-refresh-result"),
  refreshText: document.querySelector("#pattern-refresh-text"),
  refreshError: document.querySelector("#pattern-refresh-error"),
  refreshCopy: document.querySelector("#pattern-refresh-copy"),
  refreshCancel: document.querySelector("#pattern-refresh-cancel"),
  dictDialog: document.querySelector("#dictionary-add-dialog"),
  dictResult: document.querySelector("#dict-add-result"),
  dictText: document.querySelector("#dict-add-text"),
  dictCopy: document.querySelector("#dict-add-copy"),
  dictCancel: document.querySelector("#dict-add-cancel"),
  topicConfirm: document.querySelector("#topic-delete-confirm"),
  topicConfirmCancel: document.querySelector("#tpl-confirm-cancel"),
  topicConfirmCopy: document.querySelector("#tpl-confirm-copy"),
  topicConfirmList: document.querySelector("#tpl-confirm-list"),
  topicConfirmOk: document.querySelector("#tpl-confirm-ok"),
  topicDrawer: document.querySelector("#topic-drawer"),
  contentDrawer: document.querySelector("#content-drawer"),
  contentDrawerBody: document.querySelector("#cdr-body"),
  contentDrawerFoot: document.querySelector("#cdr-foot"),
  topicDrawerBody: document.querySelector("#tpl-dr-body"),
  topicDrawerFoot: document.querySelector("#tpl-dr-foot"),
  topicDrawerSub: document.querySelector("#tpl-dr-sub"),
  topicDrawerTitle: document.querySelector("#tpl-dr-title"),
  closeTopicDrawer: document.querySelector("#close-topic-drawer"),
  recommendTopicDrawer: document.querySelector("#recommend-topic-drawer"),
  recommendTopicDrawerTitle: document.querySelector("#rtdr-title"),
  recommendTopicDrawerSub: document.querySelector("#rtdr-sub"),
  recommendTopicDrawerBody: document.querySelector("#rtdr-body"),
  recommendTopicDrawerFoot: document.querySelector("#rtdr-foot"),
  recommendTopicDrawerClose: document.querySelector("#close-recommend-topic-drawer"),
  searchTopicDrawer: document.querySelector("#topic-search-drawer"),
  searchTopicDrawerTitle: document.querySelector("#tsdr-title"),
  searchTopicDrawerSub: document.querySelector("#tsdr-sub"),
  searchTopicDrawerBody: document.querySelector("#tsdr-body"),
  searchTopicDrawerFoot: document.querySelector("#tsdr-foot"),
  closeSearchTopicDrawer: document.querySelector("#close-topic-search-drawer"),
  workspaceMain: document.querySelector("#workspace-main"),
  workspaceVersion: document.querySelector("#workspace-version"),
};

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#39;");
}

function escapeAttribute(value) {
  return escapeHtml(String(value ?? ""));
}

function formatNumber(value) {
  if (value === null || value === undefined || value === "") return "—";
  const number = Number(value);
  if (!Number.isFinite(number)) return escapeHtml(value);
  if (Math.abs(number) >= 10000) return `${(number / 10000).toFixed(number >= 100000 ? 0 : 1)}万`;
  return new Intl.NumberFormat("zh-CN").format(number);
}

function formatVersion(value) {
  // 工作区版本号两种形态都有：开源版 installation.json 存 "v1.0.1"，MCP 客户端存 "1.6.1"。
  // 这里统一补前缀，避免出现 "vv1.0.1"。
  const text = String(value ?? "").trim();
  if (!text) return "未知";
  return /^v/i.test(text) ? text : `v${text}`;
}

/* 侧栏 / 设置页共用：只显示版本号。
   2026-11-12 起产品完全开源，不再分免费版 / 完整版，所以不拼类型后缀。 */
function workspaceVersionText(workspace) {
  return formatVersion(workspace?.version);
}

function formatDate(value, includeTime = false) {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value ? escapeHtml(value) : "—";
  return new Intl.DateTimeFormat("zh-CN", {
    month: "2-digit",
    day: "2-digit",
    ...(includeTime ? { hour: "2-digit", minute: "2-digit" } : {}),
  }).format(date);
}

function dateTimestamp(value) {
  const raw = String(value || "").trim();
  if (!raw) return null;
  // 先按原样解析（ISO 与 `YYYY-MM-DD` 都吃），失败再按「2026年10月1日」这类中文写法归一化 ——
  // 别一上来就替换 `.`，ISO 的毫秒 `.000Z` 会被改坏、解析成 NaN。
  const direct = Date.parse(raw);
  if (Number.isFinite(direct)) return direct;
  const normalized = raw.replace(/[年/.]/g, "-").replace(/月/g, "-").replace(/日/g, "");
  const timestamp = Date.parse(normalized);
  return Number.isFinite(timestamp) ? timestamp : null;
}

function statusClass(status) {
  const value = String(status || "");
  if (/不建议|失败|异常|阻断|废弃|取消|无效|不适配|不通过|高风险/.test(value)) return "tone-red";
  if (/风险|低适配|低质量|不匹配|问题|待优化|需修正|受限|较差/.test(value)) return "tone-pink";
  if (/暂缓|生产中|待生产|待发布|处理中|进行中|排期|草稿/.test(value)) return "tone-orange";
  if (/待|未填写|待补齐|缺失|暂无|普通|一般|未知|低优先/.test(value)) return "tone-yellow";
  if (/建议做|优秀|高适配|大爆款|已完成|已通过|已验证|可用|高优先/.test(value)) return "tone-deep-green";
  if (/已发布|正常|小爆款|中适配|已复盘|可调用|已建档|已分析|已启用|稳定/.test(value)) return "tone-light-green";
  return "tone-neutral";
}

function statusPill(status) {
  const value = status || "待补齐";
  return `<span class="status-pill ${statusClass(value)}">${escapeHtml(value)}</span>`;
}

function dataLevelClass(value) {
  const level = String(value || "");
  if (/大爆款/.test(level)) return "tone-deep-green";
  if (/小爆款/.test(level)) return "tone-light-green";
  if (/优秀/.test(level)) return "tone-orange";
  if (/正常|普通|一般|中等/.test(level)) return "tone-yellow";
  if (/差|低|不佳|失败/.test(level)) return "tone-red";
  if (/未填写|待补齐|缺失|暂无|未知/.test(level)) return "tone-gray";
  return "tone-gray";
}

function dataLevelPill(value) {
  const level = value || "未填写";
  return `<span class="status-pill ${dataLevelClass(level)}">${escapeHtml(level)}</span>`;
}

function platformKey(value) {
  const platform = String(value || "");
  if (/抖音|douyin|tiktok/i.test(platform)) return "douyin";
  if (/小红书|xhs|rednote/i.test(platform)) return "xiaohongshu";
  if (/B站|哔哩哔哩|bilibili/i.test(platform)) return "bilibili";
  if (/视频号|微信|wechat/i.test(platform)) return "wechat";
  return "other";
}

// 平台图标：小红书 / 抖音 / B 站直接用官方 logo 位图，文件放在工作台同目录。
// 演示版由 demo-init.js 覆盖 platformIconBase()，把路径改挂到 /demo-assets/。
// 视频号和未知平台没有现成 logo，仍用内联 SVG 简化形状。
const PLATFORM_ICON_FILES = {
  xiaohongshu: "platform-xiaohongshu.png",
  douyin: "platform-douyin.png",
  bilibili: "platform-bilibili.png",
};

function platformIconBase() {
  return "";
}

const PLATFORM_ART = {
  wechat: '<path d="M20 11a6.8 6.8 0 0 1-7.2 6.7 8 8 0 0 1-3.1-.6L5 18.8l1.2-3.2A6.2 6.2 0 0 1 5 12a6.8 6.8 0 0 1 7.2-6.7A6.8 6.8 0 0 1 20 11Z" fill="#111316"/><circle cx="10" cy="11" r=".8" fill="#FFFFFF"/><circle cx="15" cy="11" r=".8" fill="#FFFFFF"/>',
  other: '<circle cx="12" cy="12" r="8" fill="none" stroke="currentColor" stroke-width="1.8"/><path d="M4 12h16M12 4a13 13 0 0 1 0 16M12 4a13 13 0 0 0 0 16" fill="none" stroke="currentColor" stroke-width="1.8"/>',
};

function platformIcon(key) {
  const file = PLATFORM_ICON_FILES[key];
  if (file) {
    return `<img class="platform-icon platform-icon-${key}" src="${platformIconBase()}${file}" alt="" aria-hidden="true">`;
  }
  const art = PLATFORM_ART[key] || PLATFORM_ART.other;
  return `<svg class="platform-icon platform-icon-${key}" aria-hidden="true" viewBox="0 0 24 24">${art}</svg>`;
}

function platformPill(value) {
  const label = value || "未填写";
  const key = platformKey(label);
  return `<span class="status-pill platform-pill platform-${key}">${platformIcon(key)}<span>${escapeHtml(label)}</span></span>`;
}

function platformPills(value) {
  const platforms = String(value || "未填写").split(/[,，、/|]/).map((item) => item.trim()).filter(Boolean);
  return `<span class="platform-pill-list">${(platforms.length ? platforms : ["未填写"]).map(platformPill).join("")}</span>`;
}

function genderKey(value) {
  const gender = String(value || "").trim();
  if (/男/.test(gender)) return "male";
  if (/女/.test(gender)) return "female";
  return "unspecified";
}

function genderIcon(key) {
  if (key === "male") return '<svg class="gender-icon" aria-hidden="true" viewBox="0 0 24 24"><circle cx="9" cy="15" r="5"/><path d="M12.5 11.5 19 5m-5 0h5v5"/></svg>';
  if (key === "female") return '<svg class="gender-icon" aria-hidden="true" viewBox="0 0 24 24"><circle cx="12" cy="9" r="5"/><path d="M12 14v7m-3-3h6"/></svg>';
  return '<svg class="gender-icon" aria-hidden="true" viewBox="0 0 24 24"><circle cx="12" cy="12" r="8"/><path d="M12 8v5m0 3h.01"/></svg>';
}

function genderPill(value) {
  /* 空值（含数字 0 映射后的空串）整条不显示 —— 与稿子 gender_chip() 的 `if not info: return ""` 一致 */
  if (!String(value === null || value === undefined ? "" : value).trim()) return "";
  const label = value || "未填写";
  const key = genderKey(label);
  return `<span class="status-pill gender-pill gender-${key}">${genderIcon(key)}<span>${escapeHtml(label)}</span></span>`;
}

function emptyState(title, detail = "当前真源中没有符合条件的正式记录") {
  return `<div class="empty-state"><div><strong>${escapeHtml(title)}</strong><p>${escapeHtml(detail)}</p></div></div>`;
}

function rawFileUrl(path) {
  return `/api/raw?path=${encodeURIComponent(path)}`;
}

function contentUrl(kind, id, returnTo = "") {
  // 同 accountUrl：DS 版在带前缀的 iframe 里跑，站内跳转一律用相对路径。
  const base = `content?kind=${encodeURIComponent(kind)}&id=${encodeURIComponent(id)}`;
  return returnTo ? `${base}&return_to=${encodeURIComponent(returnTo)}` : base;
}

function topicUrl(id, returnTo = "") {
  const base = `topic?id=${encodeURIComponent(id)}`;
  return returnTo ? `${base}&return_to=${encodeURIComponent(returnTo)}` : base;
}

function accountUrl(kind, id, returnTo = "", base = "account") {
  // ⚠️ 这里**不能**用 `/account-v2?...` 这种站内绝对路径。
  // DS 版是在插件的 iframe 里跑的，页面地址带 `/selfmedia-ds/app/` 前缀；
  // 绝对路径会被发到宿主根路径上，宿主没有这个页面 → 点进去就是一片白。
  // 注入脚本只会补 `<a href>` 和 `/api/*` 的前缀，`location.href =` 它是补不了的，
  // 所以只能在这里写成相对路径（相对当前页面所在目录）：
  //   直连 http://127.0.0.1:<port>/index.html  → /account-v2?...
  //   插件 iframe /selfmedia-ds/app/index.html → /selfmedia-ds/app/account-v2?...
  // 两种都对。（2026-11-12 踩了一晚上）
  const url = `${base}?kind=${encodeURIComponent(kind)}&id=${encodeURIComponent(id)}`;
  return returnTo ? `${url}&return_to=${encodeURIComponent(returnTo)}` : url;
}

function fileButton(path, label, primary = false) {
  if (!path) return "";
  return `<button class="${primary ? "command-button" : "secondary-button"}" type="button" data-action="open-file" data-path="${escapeAttribute(path)}">${escapeHtml(label)}</button>`;
}

function contentButton(label, {
  kind = "",
  fallbackAction,
  id,
  className = "row-button",
  returnTo = "",
} = {}) {
  const action = kind
    ? `data-action="open-content" data-kind="${escapeAttribute(kind)}" data-id="${escapeAttribute(id)}"`
    : `data-action="${escapeAttribute(fallbackAction)}" data-id="${escapeAttribute(id)}"`;
  // return_to 单独拼：两条分支都要带，否则降级按钮回不到来源视图。
  return `<button class="${className}" type="button" ${action}${returnTo ? ` data-return-to="${escapeAttribute(returnTo)}"` : ""}>${escapeHtml(label)}</button>`;
}

function externalButton(url, label = "打开原页面") {
  if (!url) return "";
  return `<button class="secondary-button" type="button" data-action="open-external" data-url="${escapeAttribute(url)}">${escapeHtml(label)}</button>`;
}

function renderTags(tags = []) {
  if (!tags.length) return "";
  return `<div class="tag-list">${tags.map((tag) => `<span class="tag">${escapeHtml(tag)}</span>`).join("")}</div>`;
}

function renderMarkdown(input, options = {}) {
  return globalThis.MeilingMarkdown.renderMarkdown(input, options);
}

function renderAccountNoteMarkdown(input) {
  // The card itself supplies the document title, so omit a duplicated leading H1.
  const content = String(input || "").replace(/^\s*#\s+[^\n]+\n?/, "");
  return renderMarkdown(content);
}

function filterItems(items, fields) {
  const query = state.listQuery.trim().toLocaleLowerCase("zh-CN");
  if (!query) return items;
  return items.filter((item) => fields.map((field) => item[field] || "").join(" ").toLocaleLowerCase("zh-CN").includes(query));
}

function uniqueValues(items, field) {
  return [...new Set(items.map((item) => String(item[field] || "").trim()).filter(Boolean))]
    .sort((left, right) => left.localeCompare(right, "zh-CN"));
}

function selectOptions(values, selected, allLabel) {
  return [`<option value="">${escapeHtml(allLabel)}</option>`, ...values.map((value) =>
    `<option value="${escapeAttribute(value)}"${value === selected ? " selected" : ""}>${escapeHtml(value)}</option>`,
  )].join("");
}

function toolbar(placeholder, count, extraClass = "") {
  return `<div class="data-toolbar${extraClass ? ` ${extraClass}` : ""}"><input type="search" data-list-filter value="${escapeAttribute(state.listQuery)}" placeholder="${escapeAttribute(placeholder)}"><span class="muted">${count} 条</span></div>`;
}

function formatAccountRecordedAt(value) {
  const timestamp = dateTimestamp(value);
  if (timestamp === null) return value ? escapeHtml(value) : "—";
  return new Intl.DateTimeFormat("zh-CN", {
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  }).format(timestamp);
}

function benchmarkAccountToolbar(items, count) {
  const filters = state.benchmarkAccountFilters;
  return `<div class="data-toolbar data-toolbar-benchmark"><div class="toolbar-filter-group"><input type="search" data-list-filter value="${escapeAttribute(state.listQuery)}" placeholder="筛选账号、简介或标签"><select data-benchmark-account-filter="platform" aria-label="按平台筛选">${selectOptions(uniqueValues(items, "platform"), filters.platform, "全部平台")}</select><select data-benchmark-account-filter="fit" aria-label="按适配判断筛选">${selectOptions(uniqueValues(items, "fit"), filters.fit, "全部适配判断")}</select><select data-benchmark-account-filter="sort" aria-label="排序方式"><option value="recorded-desc"${filters.sort === "recorded-desc" ? " selected" : ""}>录入时间：最新在前</option><option value="recorded-asc"${filters.sort === "recorded-asc" ? " selected" : ""}>录入时间：最早在前</option><option value="followers-desc"${filters.sort === "followers-desc" ? " selected" : ""}>粉丝数：从多到少</option><option value="followers-asc"${filters.sort === "followers-asc" ? " selected" : ""}>粉丝数：从少到多</option><option value="likes-desc"${filters.sort === "likes-desc" ? " selected" : ""}>获赞数：从多到少</option><option value="likes-asc"${filters.sort === "likes-asc" ? " selected" : ""}>获赞数：从少到多</option></select></div><span class="muted">${count} 条</span></div>`;
}

function compareOptionalNumbers(left, right, direction) {
  const leftValue = Number(left);
  const rightValue = Number(right);
  const leftValid = Number.isFinite(leftValue);
  const rightValid = Number.isFinite(rightValue);
  if (!leftValid && !rightValid) return 0;
  if (!leftValid) return 1;
  if (!rightValid) return -1;
  return direction === "asc" ? leftValue - rightValue : rightValue - leftValue;
}

function filterBenchmarkAccounts(items) {
  const filters = state.benchmarkAccountFilters;
  const filtered = filterItems(items, ["name", "platform", "intro", "fit", "search_text"])
    .filter((item) => !filters.platform || item.platform === filters.platform)
    .filter((item) => !filters.fit || item.fit === filters.fit);
  return filtered.slice().sort((left, right) => {
    if (filters.sort.startsWith("followers")) return compareOptionalNumbers(left.followers, right.followers, filters.sort.endsWith("asc") ? "asc" : "desc");
    if (filters.sort.startsWith("likes")) return compareOptionalNumbers(left.likes, right.likes, filters.sort.endsWith("asc") ? "asc" : "desc");
    const leftTime = dateTimestamp(left.recorded_at);
    const rightTime = dateTimestamp(right.recorded_at);
    if (leftTime !== null && rightTime !== null) return filters.sort === "recorded-asc" ? leftTime - rightTime : rightTime - leftTime;
    if (leftTime !== null) return -1;
    if (rightTime !== null) return 1;
    return (right.record_order || 0) - (left.record_order || 0);
  });
}

/* 选题在卡片上的状态是「互斥」的，只有一个：
   已经生产过的显示「已生产」——不再分它当初判的是建议做还是暂缓；
   没生产的显示选题结论；结论也空的就是还没分析过。 */
function topicTone(recommendation) {
  const value = String(recommendation || "").trim();
  if (value === "建议做") return "go";
  if (value === "暂缓") return "hold";
  if (value === "不建议做") return "no";
  return "todo";
}

/* 「已生产」只看一件事：这条选题名下有没有内容记录。
   以前还要看阶段推到「内容框架」之后才算，结果「生产中」的内容挂在选题上、
   选题卡片却还显示「建议做」，看着能再开一条，实际 create-content 不会新建。 */
function enteredProduction(item) {
  if (String(item.status || "").trim() === "已生产") return true;
  return (item.contents || []).length > 0;
}

function topicState(item) {
  if (enteredProduction(item)) return "made";
  const tone = topicTone(item.recommendation);
  return tone === "todo" ? "raw" : tone;
}

const TOPIC_STATES = [
  ["all", "全部"],
  ["go", "建议做"],
  ["hold", "暂缓"],
  ["no", "不建议做"],
  ["made", "已生产"],
  ["raw", "未分析"],
];

const TOPIC_STATE_LABEL = {
  go: "建议做",
  hold: "暂缓",
  no: "不建议做",
  made: "已生产",
  raw: "未分析",
};

/* 分段按互斥状态走：数量为 0 的档位不占位置 */
function topicToolbar(all, count) {
  const active = state.topicFilters.judgment || "all";
  const counts = { all: all.length, go: 0, hold: 0, no: 0, made: 0, raw: 0 };
  all.forEach((item) => { counts[topicState(item)] += 1; });
  const seg = TOPIC_STATES
    .filter(([key]) => key === "all" || counts[key] > 0)
    .map(([key, label]) => `<button type="button" role="tab" aria-selected="${active === key}" class="${active === key ? "on" : ""}" data-action="filter-topic" data-value="${key}">${label}<em>${counts[key]}</em></button>`)
    .join("");
  return `<nav class="tpl-seg" role="tablist" aria-label="按状态筛选">${seg}</nav>`;
}

function filterTopics(items) {
  const filters = state.topicFilters;
  const active = filters.judgment || "all";
  const filtered = filterItems(items, ["name", "theme", "search_text"])
    .filter((item) => active === "all" || topicState(item) === active)
    .filter((item) => filters.production === "produced" ? item.has_created_content : true)
    .filter((item) => filters.production === "unproduced" ? !item.has_created_content : true);
  /* 已经生产过的选题只起记录作用，排到列表最后，不占待决策的位置 */
  return filtered.slice().sort((left, right) => {
    const leftMade = topicState(left) === "made" ? 1 : 0;
    const rightMade = topicState(right) === "made" ? 1 : 0;
    if (leftMade !== rightMade) return leftMade - rightMade;
    const leftTime = dateTimestamp(left.updated_at);
    const rightTime = dateTimestamp(right.updated_at);
    if (leftTime === null && rightTime === null) return left.name.localeCompare(right.name, "zh-CN");
    if (leftTime === null) return 1;
    if (rightTime === null) return -1;
    return filters.sort === "recorded-asc" ? leftTime - rightTime : rightTime - leftTime;
  });
}

function filterBenchmarkContents(items) {
  return items.slice().sort((left, right) => {
    const leftTime = dateTimestamp(left.analysis_at);
    const rightTime = dateTimestamp(right.analysis_at);
    if (leftTime === null && rightTime === null) return left.title.localeCompare(right.title, "zh-CN");
    if (leftTime === null) return 1;
    if (rightTime === null) return -1;
    return rightTime - leftTime;
  });
}


/* ================================================================
   列表页卡片网格（设计稿口径：列表不用表格）
   只负责把既有字段排成卡片；跳转 / 筛选 / 动作按钮沿用原有实现。
   ================================================================ */

function cardGrid(cards, emptyTitle, gridClass) {
  if (!cards.length) return emptyState(emptyTitle);
  return `<div class="${gridClass}">${cards.join("")}</div>`;
}

/* 抖音侧的性别存的是数字（1=男 / 2=女 / 0=未知），别把数字原样甩给用户 */
function genderLabel(value) {
  const raw = String(value || "").trim();
  if (raw === "1") return "男";
  if (raw === "2") return "女";
  if (raw === "0") return "";
  return raw;
}

function fillPills(values) {
  return values.filter((value) => value && !value.includes("未填写")).join("");
}

function accountCard(item, {
  detailBase = "account-v2",
  listView = "benchmark-accounts-v2",
  kind = "benchmark",
  subMetrics = [
    ["获赞", "likes"],
    ["收录", "content_count"],
    ["已分析", "analyzed_content_count"],
  ],
  updatedAt = item.recorded_at,
} = {}) {
  /* 账号「数据卡」：粉丝数是主视觉，其余三项做副指标；对标账号与我的账号共用这一套版式。 */
  const url = accountUrl(kind, item.id, `index.html#${listView}`, detailBase);
  const tags = [platformPill(item.platform), genderPill(genderLabel(item.gender))];
  // 副指标固定三项，缺数据就用「—」占位，避免每张卡宽度不一致
  const sub = subMetrics
    .map(([label, field]) => {
      const value = item[field];
      const missing = value === null || value === undefined || value === "";
      return `<span class="bm-sub-item"><b>${missing ? "—" : formatNumber(value)}</b><i>${escapeHtml(label)}</i></span>`;
    })
    .join("");
  const dateLine = updatedAt ? formatAccountRecordedAt(updatedAt) : "";
  // 只有我的账号卡片带「重新诊断」，对标账号没有诊断流程
  const REDO_ICON = '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M20 12a8 8 0 1 1-2.6-5.9"/><path d="M20 4.5V9h-4.5"/></svg>';
  const redo = kind === "own"
    ? `<span class="acct-redo" data-action="redo-own-diagnosis" data-id="${escapeAttribute(item.id)}">${REDO_ICON}重新诊断</span>`
    : kind === "benchmark"
      ? `<span class="acct-redo" data-action="reanalyze-benchmark-account" data-id="${escapeAttribute(item.id)}">${REDO_ICON}重新分析</span>`
      : "";

  return `<a class="acct-card is-link bm-card" href="${url}" draggable="false" title="打开「${escapeAttribute(item.name)}」的账号档案">
    <div class="bm-hero">
      <span class="acct-avatar">${accountAvatar(item, "acct-avatar-img")}</span>
      <div class="bm-id">
        <span class="acct-name">${escapeHtml(item.name)}</span>
        <div class="acct-pills">${fillPills(tags)}</div>
      </div>
      ${redo}
    </div>
    <p class="bm-bio">${escapeHtml(item.intro || "—")}</p>
    <div class="bm-metrics">
      <div class="bm-main">
        <b>${formatNumber(item.followers)}</b>
        <i>粉丝</i>
      </div>
      <div class="bm-sub">${sub}</div>
    </div>
    ${dateLine ? `<div class="bm-foot">
      <span class="bm-stamp">
        <svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="12" cy="12" r="8.6"/><path d="M12 7.5V12l3.1 1.9"/></svg>
        更新时间
      </span>
      <span class="bm-date">${escapeHtml(dateLine)}</span>
    </div>` : ""}
  </a>`;
}

/* ================================================================
   对标账号的卡片顺序（拖拽重排后存在本地）
   ================================================================ */

const BENCH_ORDER_KEY = "meiling:benchmark-order";

function readBenchmarkOrder() {
  try {
    const list = JSON.parse(localStorage.getItem(BENCH_ORDER_KEY) || "[]");
    return Array.isArray(list) ? list.filter((id) => typeof id === "string" && id) : [];
  } catch (_error) {
    return [];
  }
}

function writeBenchmarkOrder(ids) {
  try {
    localStorage.setItem(BENCH_ORDER_KEY, JSON.stringify(ids));
  } catch (_error) {
    /* 隐私模式写不进去，忽略即可，不影响浏览 */
  }
}

/* 保存过顺序的按它排；没保存过的保持原来的相对顺序垫在后面 */
function applyBenchmarkOrder(items) {
  const saved = readBenchmarkOrder();
  if (!saved.length) return items;
  const rank = new Map(saved.map((id, index) => [id, index]));
  return items
    .map((item, index) => ({ item, index }))
    .sort((left, right) => {
      const a = rank.has(left.item.id) ? rank.get(left.item.id) : Number.MAX_SAFE_INTEGER;
      const b = rank.has(right.item.id) ? rank.get(right.item.id) : Number.MAX_SAFE_INTEGER;
      return a === b ? left.index - right.index : a - b;
    })
    .map((entry) => entry.item);
}

function accountIdFromHref(href) {
  const match = String(href || "").match(/[?&]id=([^&]+)/);
  return match ? decodeURIComponent(match[1]) : "";
}


/* ================================================================
   列表页外壳（照设计稿）：一张白卡 = 卡头（图标 + 标题 + 副标题 + 右侧）+ 卡体（工具栏 + 卡片）
   卡头用的就是页面标题，所以这些视图把页面级 .page-heading 收掉，免得同一句话出现两次。
   ================================================================ */

const VIEW_ICON = {
  skills: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="m12 3 1.5 4.5L18 9l-4.5 1.5L12 15l-1.5-4.5L6 9l4.5-1.5z"/><path d="m19 15 .7 2.3L22 18l-2.3.7L19 21l-.7-2.3L16 18l2.3-.7z"/></svg>',
  "benchmark-accounts-v2": '<svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="9" cy="8" r="3"/><path d="M3.5 20c.7-3.4 2.6-5 5.5-5s4.8 1.6 5.5 5"/><path d="M16 8h5M18.5 5.5v5"/></svg>',
  "benchmark-contents": '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M6 3h9l3 3v15H6z"/><path d="M15 3v4h4M9 12h6M9 16h4"/></svg>',
  "own-accounts": '<svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="12" cy="8" r="3.5"/><path d="M5 21c.9-4 3.2-6 7-6s6.1 2 7 6"/></svg>',
  "topic-search": '<svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="10.8" cy="10.8" r="6.6"/><path d="m15.8 15.8 4.4 4.4"/></svg>',
  topics: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M9 18h6M10 22h4M8 14a6 6 0 1 1 8 0c-1 1-1 2-1 3H9c0-1 0-2-1-3z"/></svg>',
  "creation-board": '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="m4 17 10-10 3 3L7 20H4z"/><path d="m13 8 3 3M15 5l1-1 3 3-1 1"/></svg>',
  publications: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="m3 11 18-8-6 18-4-7z"/><path d="m11 14 4-4"/></svg>',
  settings: '<svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="12" cy="12" r="3.2"/><path d="M19.4 15a1.6 1.6 0 0 0 .3 1.8l.1.1a2 2 0 1 1-2.8 2.8l-.1-.1a1.6 1.6 0 0 0-1.8-.3 1.6 1.6 0 0 0-1 1.5V21a2 2 0 1 1-4 0v-.1A1.6 1.6 0 0 0 7 19.4a1.6 1.6 0 0 0-1.8.3l-.1.1a2 2 0 1 1-2.8-2.8l.1-.1a1.6 1.6 0 0 0 .3-1.8 1.6 1.6 0 0 0-1.5-1H1a2 2 0 1 1 0-4h.1A1.6 1.6 0 0 0 4.6 7a1.6 1.6 0 0 0-.3-1.8l-.1-.1a2 2 0 1 1 2.8-2.8l.1.1a1.6 1.6 0 0 0 1.8.3H9a1.6 1.6 0 0 0 1-1.5V1a2 2 0 1 1 4 0v.1a1.6 1.6 0 0 0 1 1.5 1.6 1.6 0 0 0 1.8-.3l.1-.1a2 2 0 1 1 2.8 2.8l-.1.1a1.6 1.6 0 0 0-.3 1.8V9a1.6 1.6 0 0 0 1.5 1H21a2 2 0 1 1 0 4h-.1a1.6 1.6 0 0 0-1.5 1z"/></svg>',
};

/* 卡头右侧的「更新于 …」，口径与页面级 #snapshot-time 一致 */
function updatedChip() {
  const stamp = state.snapshot?.generated_at;
  return `<span class="list-chip">更新于 ${stamp ? formatDate(stamp, true) : "等待同步"}</span>`;
}

function listHead(side = "") {
  const meta = VIEW_META[state.currentView] || VIEW_META.home;
  return { icon: VIEW_ICON[state.currentView] || "", title: meta[1], sub: meta[2], side };
}

function listShell({ icon = "", title = "", sub = "", side = "", body = "" }) {
  return `<section class="list-card">
    <header class="list-card-head">
      <span class="list-card-icon">${icon}</span>
      <div class="list-card-copy"><h2>${escapeHtml(title)}</h2><p>${escapeHtml(sub)}</p></div>
      ${side ? `<div class="list-card-side">${side}</div>` : ""}
    </header>
    <div class="list-card-body">${body}</div>
  </section>`;
}

/* ---------- 作品卡（列表页 / 首页 / 发布记录共用） ---------- */

const MEDIA_ICON = {
  video: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M8.5 5.5v13l10-6.5z"/></svg>',
  article: '<svg viewBox="0 0 24 24" aria-hidden="true"><rect x="3.5" y="4.5" width="17" height="15" rx="2.5"/><path d="m6.5 15.5 3.5-3.5 3 3 2.5-2.5 3 3"/><circle cx="9" cy="9.5" r="1.2"/></svg>',
  other: '<svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="12" cy="12" r="8.5"/></svg>',
};

const CARD_ICON = {
  clock: '<svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="12" cy="12" r="8.5"/><path d="M12 7.5V12l3 2"/></svg>',
  hash: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M9.5 4 7.5 20M16.5 4l-2 16M4.5 9h15M4 15h15"/></svg>',
};

/* 灰胶囊（当前阶段这类纯标签，不参与语义配色） */
function plainPill(value) {
  return `<span class="status-pill tone-gray">${escapeHtml(String(value || "—"))}</span>`;
}

function rankPill(index) {
  return `<span class="rank-pill">#${String(index).padStart(2, "0")}</span>`;
}

function mediaPill(media) {
  const text = String(media || "").trim();
  if (!text || text === "未填写") return '<span class="media-pill is-other">—</span>';
  const kind = /视频|短视频|口播|直播/.test(text) ? "video" : /图文|文章|笔记/.test(text) ? "article" : "other";
  return `<span class="media-pill is-${kind}">${MEDIA_ICON[kind]}${escapeHtml(text)}</span>`;
}

/* 卡头行的短日期：2026-09-18（工作区里的发布时间多是「2026/09/18 17:21」这种文本） */
function shortDate(value) {
  const text = String(value || "").trim();
  if (!text) return "—";
  const stamp = dateTimestamp(text);
  if (stamp === null) return text.slice(0, 10);
  const date = new Date(stamp);
  const pad = (number) => String(number).padStart(2, "0");
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}`;
}

/* 作品卡：左封面 + 右文案 —— 编号·媒介·状态 → 标题 → 平台·账号 → 互动数 + 查看详情。
   **整张卡就是详情入口**：open={href} 走链接，open={action,attrs} 走既有 data-action。 */
function workCardHtml(item, index, open = {}, opts = {}) {
  const platform = platformKey(item.platform);
  const canOpen = Boolean(open.href || open.action);
  const cover = item.cover_path
    ? `<img src="${rawFileUrl(item.cover_path)}" alt="" loading="lazy">`
    : `<span class="work-fallback">${platformIcon(platform)}</span>`;
  const metrics = opts.metrics || item.metrics || {};
  const inner = `<span class="work-cover">${cover}</span>
    <div class="work-body">
      <div class="work-head">${rankPill(index)}${mediaPill(item.media_format)}${opts.extraTags || ""}<span class="work-date">${escapeHtml(shortDate(item.published_at))}</span></div>
      <div class="work-title">${escapeHtml(item.title || "—")}</div>
      <div class="work-author">${platformPill(item.platform)}<span class="work-author-name">${escapeHtml(item.account_name || "—")}</span></div>
      <div class="work-foot">${contentEngagement(metrics)}${canOpen ? '<span class="work-link">查看详情 →</span>' : ""}</div>
    </div>`;
  if (open.href) {
    return `<a class="work-card is-link" href="${open.href}" title="打开详情" data-platform="${platform}">${inner}</a>`;
  }
  if (open.action) {
    return `<div class="work-card is-link" role="link" tabindex="0" title="打开详情" data-platform="${platform}" data-action="${escapeAttribute(open.action)}"${open.attrs ? " " + open.attrs : ""}>${inner}</div>`;
  }
  return `<div class="work-card" data-platform="${platform}">${inner}</div>`;
}

/* 对标内容卡：照「对标账号·新 → 账号详情页 → 代表作品」的口径——
   封面在左（左上序号、右上网民视频/图文），右边标题、平台+作者、底部互动数据。
   整张卡是按钮，点了在右侧开抽屉（内容和原来的详情页一模一样），不再跳 content.html。 */
function benchmarkContentCard(item, index) {
  const cover = item.cover_path
    ? `<img src="${rawFileUrl(item.cover_path)}" alt="" loading="lazy">`
    : `<span class="work-fallback">${platformIcon(platformKey(item.platform))}</span>`;
  const media = /图文|文章|笔记/.test(String(item.media_format || "")) ? "图文" : "视频";
  return `<button class="bm-card" type="button" data-action="open-benchmark-content-drawer" data-key="${escapeAttribute(item.key)}" title="打开内容详情">
    <span class="bm-card-cover">${cover}<b class="bm-card-rank">${String(index).padStart(2, "0")}</b><span class="bm-card-media">${media}</span></span>
    <div class="bm-card-body">
      <p class="bm-card-title">${escapeHtml(item.title || "—")}</p>
      <div class="bm-card-author">${platformPill(item.platform)}<span>${escapeHtml(item.account_name || "—")}</span></div>
      <div class="bm-card-stats">${contentEngagement(item.metrics)}</div>
    </div>
  </button>`;
}

/* 发布记录：发布的是我方内容，详情入口走 own 内容页（沿用原有 contentUrl） */
function publicationCard(item, index) {
  return workCardHtml(item, index, { href: contentUrl("own", item.content_id, "index.html#publications") }, { extraTags: statusPill(item.status) });
}

/* 参考内容一行：内容 / 选题主表里存的都是对标内容的 key（`账号ID::内容ID`），
   统一在这里解析成「账号名 + 标题」；找不到就显示未关联。 */
function referenceCellHtml(item) {
  const refKey = String(item.reference_content_key || "").trim();
  const refId = refKey ? refKey.split("::").pop() : "";
  const ref = refId ? (state.snapshot.workflows.insight.benchmark_contents || []).find((entry) => entry.id === refId) : null;
  return ref
    ? `<span class="tc-id">${escapeHtml(ref.account_name || "—")}</span><span class="tc-name" title="${escapeAttribute(ref.title || "")}">${escapeHtml(ref.title || refId)}</span>`
    : `<span class="tc-empty">未关联参考内容</span>`;
}

/* 选题卡：顶行只有「序号 + 一个互斥状态标签」，下面标题、核心判断，最后一行更新时间。
   整张卡是按钮，点了在右侧开抽屉，不再跳 topic.html。 */
function topicCard(item) {
  const state = topicState(item);
  const date = String(item.updated_at || "").slice(5, 10);
  const core = String(item.reason || "").trim();
  return `<button type="button" class="tpl-card" data-tone="${state}" data-action="open-topic-drawer" data-topic-id="${escapeAttribute(item.id)}">
    <span class="tpl-top">
      <span class="tpl-num">${escapeHtml(item.id || "未编号")}</span>
      <span class="tpl-badge ${state}">${escapeHtml(TOPIC_STATE_LABEL[state])}</span>
      <span class="tpl-date">${escapeHtml(date)}</span>
    </span>
    <span class="tpl-title">${escapeHtml(item.name || "—")}</span>
    <span class="tpl-core">${core ? escapeHtml(core) : "还没有核心判断"}</span>
  </button>`;
}


/* ==================== 提示词弹窗 ====================
   这个弹窗不做任何写操作：只把用户填的东西拼成一段可以直接粘进对话的指令，
   真正的分析 / 生产还是由 AI 按 skills 里的流程跑。 */
let promptState = { kind: "", topic: null, mode: "direct", bench: null, mediaForm: "", goal: "" };

/* ---------- 做内容前的两个设置：形式 / 目标 ----------
   口径跟「内容定位流程」2.1 一致：媒介形式归一到 视频/图文/文章/其他，
   内容目标在 流量/涨粉/交易 里选一个。工作台只放前三项形式 —— 选「其他」的人
   在对话里说一声更省事，不必为它多占一格。
   这两个值会写进指令，AI 建档时直接带上，内容定位阶段就不用再问一遍。 */
const PRODUCTION_MEDIA_FORMS = ["视频", "图文", "文章"];
const PRODUCTION_GOALS = ["流量", "涨粉", "交易"];
const PRODUCTION_DEFAULTS = { mediaForm: "视频", goal: "流量" };

function productionChipsHtml(options, current, action) {
  return options
    .map((name) => `<button class="tsa-channel${name === current ? " is-on" : ""}" type="button" role="radio" aria-checked="${name === current}" data-${action}="${escapeAttribute(name)}">${escapeHtml(name)}</button>`)
    .join("");
}

/* 两个弹窗各画一次；选中的值存在 promptState / recommendTopicActionContext 上 */
function paintProductionSettings(box, action, current) {
  if (!box) return;
  box.innerHTML = productionChipsHtml(box.dataset.options === "goal" ? PRODUCTION_GOALS : PRODUCTION_MEDIA_FORMS, current, action);
}


/* ---------- 内联提示：错误贴在出问题的地方，不用 toast ---------- */
function setInlineError(node, message = "") {
  if (!node) return;
  node.textContent = message;
  node.hidden = !message;
}

function setFieldError(field, errorNode, message = "") {
  /* is-error 是旧两步弹窗的钩子（.tpl-field.is-error），is-err 是单页弹窗的（.dd-field.is-err）。
     两套都要打，否则新弹窗只出红字、输入框不描红。 */
  if (field) {
    const on = Boolean(message);
    field.classList.toggle("is-error", on);
    field.classList.toggle("is-err", on);
  }
  setInlineError(errorNode, message);
}

function clearPromptErrors() {
  setFieldError(elements.promptFieldIdea, elements.ideaError, "");
  setFieldError(elements.promptFieldRef, null, "");
  setInlineError(elements.promptError, "");
}


const PROMPT_PRESETS = {

  new: {
    title: "新增选题",
    ideaRequired: true,
    idea: true,
    ref: true,
    checkRef: true,
    build: ({ idea, ref }) => {
      const lines = ["用 topic-evaluate 帮我分析一个新选题。", `我的想法：${idea.trim()}`];
      if (ref.trim()) lines.push(`参考链接：${ref.trim()}`);
      return lines.join("\n");
    },
  },
  /* 脚本创作页的「新增脚本」：三种生产方式共用这一个弹窗，靠 tab 切。
     ① 直接生产 = 有想法就能写；② 根据选题 = 用已分析好的选题结论；
     ③ 根据对标 = 拆一条对标内容照着写。三档最后都接同一套内容生产流程。 */
  script: {
    title: "新增脚本",
    tabs: true,
    settings: true,
    ideaRequired: true,
    idea: true,
    ref: true,
    checkRef: true,
    placeholder: "比如：想讲「为什么先理清业务系统再上AI」，给中小企业老板看",
    confirm: "复制提示词",
    build: ({ idea, ref, extra }) => {
      const mode = promptState.mode || "direct";
      const tail = String(extra || "").trim();
      /* 三条入口都不该停在「等你确认定位」——定位出来直接往下走 */
      const go = "定位生成后不用等我确认，直接出内容框架和完整初稿。";

      // ② 根据选题生产：分析已经做完，带选题身份直接进 create-content
      if (mode === "topic") {
        const topic = promptState.topic;
        if (!topic) {
          return ["用 create-content 开始做内容，入口是「选题分析后生产」。", "", "选题：（还没选）"].join("\n");
        }
        const lines = [
          "用 create-content 开始做内容，入口是「选题分析后生产」。",
          "按这条选题已有的分析结论写，不要重新分析、也不要另起一个选题。",
          "",
          `选题：${topic.id} ${topic.name}`,
        ];
        if (topic.analysis_path) lines.push(`选题分析记录：${topic.analysis_path}`);
        if (tail) lines.push("", `这次的补充要求：${tail}`, "补充只调细节，主题和切口仍以选题分析为准。");
        lines.push(go);
        return lines.join("\n");
      }

      // ③ 根据对标生产：列表里挑的带正式分析路径；自己粘链接的让 AI 先拆完再做
      if (mode === "benchmark") {
        const bench = promptState.bench;
        const lines = ["用 create-content 开始做内容，入口是「对标内容分析后生产」。"];
        if (bench) {
          lines.push("先按这条对标内容的「借鉴判断」拆清楚它为什么成立，再写我自己的。");
          lines.push("", `参考内容：${bench.title || bench.id}`);
          lines.push(`对标内容 ID：${bench.key}`);
          if (bench.analysis_path) lines.push(`参考内容分析：${bench.analysis_path}`);
        } else if (String(ref || "").trim()) {
          lines.push("", `参考链接：${String(ref).trim()}`);
          lines.push("这条对标内容还没有正式分析，先按对标内容分析流程拆完，再进入内容生产。");
        } else {
          lines.push("", "参考内容：（还没选）");
          return lines.join("\n");
        }
        if (tail) lines.push("", `这次的补充要求：${tail}`, "补充只调细节，不改变参考的是哪条对标内容。");
        lines.push(go);
        return lines.join("\n");
      }

      // ① 直接生产：没有选题也没有对标参考，想法就是全部输入
      return [
        "用 create-content 开始做内容，入口是「独立内容生产」。",
        "",
        `这条内容的想法：${String(idea || "").trim()}`,
        "",
        "这个想法就是全部输入，不用再做选题分析。直接按它进入内容定位和初稿；如果和账号方向冲突，以账号方向为准并说明调整。",
        go,
      ].join("\n");
    },
  },
  reanalyze: {
    title: "重新分析",
    intro: "说明这次想调整什么，生成一段指令，点「确认」交给 AI 重跑一遍选题分析。",
    idea: true,
    ref: true,
    checkRef: true,
    ideaLabel: "这次想调整什么<em>可选</em>",
    confirm: "生成提示词",
    build: ({ idea, ref, topic }) => {
      const lines = [
        `用 topic-evaluate 重新分析「${topic.id} ${topic.name}」。`,
        "",
        "这次要调整的：",
        idea.trim() || "（先看现有分析有没有问题，该改哪里你来判断）",
      ];
      if (ref.trim()) lines.push("", `新增的参考内容：${ref.trim()}`);
      if (topic.analysis_path) lines.push("", `先读这条已有的分析记录：${topic.analysis_path}`);
      lines.push("", "按已有选题重新分析并直接保存更新，不要新建，也不用再问我是否沿用老题、是否定稿。");
      return lines.join("\n");
    },
  },
  produce: {
    title: "开始生产",
    intro: "生成一段指令，点「确认」，让 AI 用这条选题开始做内容。",
    idea: false,
    ref: false,
    settings: true,
    confirm: "复制提示词",
    build: ({ topic }) => {
      const lines = [
        "用 create-content 开始做内容，入口是「选题分析后生产」。",
        "",
        `选题：${topic.id} ${topic.name}`,
      ];
      if (topic.analysis_path) lines.push(`选题分析记录：${topic.analysis_path}`);
      return lines.join("\n");
    },
  },
};

function promptValues() {
  return {
    idea: elements.promptIdea.value,
    ref: elements.promptRef.value,
    extra: elements.promptExtra ? elements.promptExtra.value : "",
    topic: promptState.topic,
  };
}

/* 「新增对标内容」的那句固定说明，参考链接也用它，别再各写一份 */
const CONTENT_HINT = "抖音分享出来的那一整段话里有链接，会自动挑出来，不用自己删。";

/* 选题的参考链接和「新增对标内容」用同一套识别（同一个 inspectContentUrl），
   只把提示语换成选题语境：要的是「那一条作品」，不是账号主页。 */
function inspectTopicRef(raw) {
  const result = inspectContentUrl(raw);
  if (result.state === "ok") return result;
  const wording = {
    account: "这是账号主页，不是某一条作品；参考链接要填具体那一条",
    feed: "这是首页或推荐页，不是某一条作品；打开那条内容，点「分享 → 复制链接」",
  };
  return { ...result, message: wording[result.state] || result.message };
}

/* 必填判断：新增脚本三种方式各有各的必填——直接生产要想法，
   根据选题要选中一条选题，根据对标要挑一条或粘一条链接；其他用途只有「新增选题」要求写想法。 */
function promptIncomplete(preset, values) {
  if (!Boolean(preset.ideaRequired)) return false;
  if (preset.tabs) {
    const mode = promptState.mode || "direct";
    if (mode === "topic") return !promptState.topic;
    if (mode === "benchmark") return !promptState.bench && !String(values.ref || "").trim();
  }
  return !String(values.idea || "").trim();
}

/* 一个地方算完：指令内容 + 复制键能不能点。没有「生成」这一步 */
/* 把「形式 / 目标」两行接到指令尾巴上；只有带 settings 的预设（开始生产）才追加 */
function withProductionSettings(text) {
  const preset = PROMPT_PRESETS[promptState.kind];
  if (!preset?.settings) return text;
  const mediaForm = promptState.mediaForm || PRODUCTION_DEFAULTS.mediaForm;
  const goal = promptState.goal || PRODUCTION_DEFAULTS.goal;
  return [
    text,
    "",
    `媒介形式：${mediaForm}`,
    `内容目标：${goal}`,
    `这两项已经定好，建档时一起带上（--media-form ${mediaForm} / --content-task ${goal}），内容定位阶段不用再问我。`,
  ].join("\n");
}

function syncPrompt() {
  const preset = PROMPT_PRESETS[promptState.kind];
  if (!preset) return;
  const values = promptValues();
  /* 参考链接和「新增对标内容」一个机制：整段分享文案也能粘；
     校验看原文，写进指令的只放挑出来的那一条链接 */
  const rawRef = values.ref;
  values.ref = extractFirstUrl(rawRef);
  const missing = promptIncomplete(preset, values);
  /* 必填还没填时，指令里给个占位，别让那一行空着 */
  if (missing) values.idea = "（还没填）";
  /* 形式 / 目标：工作台已经选好，就把值写进指令，让 AI 建档时直接带上。
     不写的话内容定位阶段还要再问一遍，白问一次。 */
  const text = withProductionSettings(preset.build(values));
  elements.promptText.textContent = text;
  elements.promptDialog.dataset.prompt = text;

  /* 参考链接只有填了才校验；填错和没填想法一样，都不让复制 */
  const check = preset.checkRef ? inspectTopicRef(rawRef) : null;
  const refBad = Boolean(check) && check.state !== "empty" && check.state !== "ok";
  elements.promptCopy.disabled = missing || refBad;
  /* 复制键为什么是灰的：就写在这一句里。填好了这句自动消失，不复述别的状态 */
  const need = document.querySelector("#tpl-prompt-need");
  if (need) {
    /* 新增脚本按当前这一档说要什么；其他用途（新增选题）还是那一句 */
    need.textContent = missing
      ? (preset.tabs ? SCRIPT_MODE_NEEDS[promptState.mode || "direct"] || "" : "写一句想法就能复制")
      : "";
  }
  if (!missing) setFieldError(elements.promptFieldIdea, elements.ideaError, "");

  const refError = document.querySelector("#tpl-ref-error");
  const refHint = document.querySelector("#tpl-ref-hint");
  if (refError) {
    refError.hidden = !refBad;
    refError.textContent = refBad ? check.message : "";
  }
  if (refHint) {
    /* 没有 checkRef 的预设（重新分析等）整块不显示，别留一个空的行高 */
    if (!check) {
      refHint.hidden = true;
      refHint.classList.remove("is-ok");
    } else {
      refHint.hidden = refBad;
      refHint.classList.toggle("is-ok", check.state === "ok");
      refHint.textContent = check.state === "ok"
        ? (check.short
            ? `已识别：${check.platform}短链，取数时会自动展开成完整地址`
            : `已识别：是${check.platform}的作品链接`)
        : check.state === "empty"
          ? CONTENT_HINT
          : "";
    }
  }
  setFieldError(elements.promptFieldRef, refError, refBad ? check.message : "");
  updatePromptFade();
}

/* 输入区还能不能往下滚：能就在下沿压一道白色渐隐，滚到底自动撤掉。
   没有它，折在底边的内容看着像被裁坏了。 */
function updatePromptFade() {
  const fade = document.querySelector("#tpl-prompt-fade");
  const body = document.querySelector("#topic-prompt-dialog .dd-body");
  if (!fade || !body) return;
  fade.classList.toggle("is-on", body.scrollHeight - body.scrollTop - body.clientHeight > 4);
}

/* ---------- 新增脚本 · 三种生产方式 ----------
   ① 直接生产：想法就是全部输入
   ② 根据选题生产：列出「已分析、还没生产过」的选题，建议做 / 不建议做只当标签，不参与筛选
   ③ 根据对标生产：从分析完成的对标内容里挑一条，或粘一条链接让它先拆
   三句说明写在 tab 卡片上（index.html），这里只留「还差什么才能复制」那一句。 */
const SCRIPT_MODE_NEEDS = {
  direct: "写一句想法就能复制",
  topic: "选一条选题就能复制",
  benchmark: "挑一条对标内容，或粘一条链接",
};

function setScriptNote(kind, text, on = false) {
  const el = document.querySelector(kind === "topic" ? "#tpl-topic-note" : "#tpl-bench-note");
  if (!el) return;
  el.textContent = text;
  el.classList.toggle("is-on", on);
}

function scriptModeOptions() {
  // 「已分析」认两个信号：有正式分析记录路径，或选题状态已经走到「已完成分析」。
  // 只看 analysis_path 会漏掉那些分析记录没落在标准路径上的选题。
  const analyzed = (state.snapshot?.workflows?.production?.topics || [])
    .filter((topic) => topic.analysis_path || topic.status === "已完成分析");
  const topics = analyzed
    .filter((topic) => !topic.has_created_content)
    .sort((left, right) => String(right.updated_at || "").localeCompare(String(left.updated_at || "")));
  const producedTopics = analyzed.filter((topic) => topic.has_created_content);
  const benchmarks = (state.snapshot?.workflows?.insight?.benchmark_contents || [])
    .filter((item) => item.analysis_state === "已完成")
    .sort((left, right) => String(right.published_at || "").localeCompare(String(left.published_at || "")));
  return { topics, producedTopics, benchmarks };
}

function recommendationClass(value) {
  const text = String(value || "").trim();
  if (text === "建议做") return "is-ok";
  if (text === "不建议做") return "is-no";
  return "is-mid";
}

function renderScriptTopicList(keyword = "") {
  const list = document.querySelector("#tpl-topic-list");
  if (!list) return;
  const { topics, producedTopics } = scriptModeOptions();
  const kw = String(keyword || "").trim();
  const shown = kw
    ? topics.filter((topic) => `${topic.id} ${topic.name} ${topic.theme || ""}`.includes(kw))
    : topics;
  const picked = promptState.topic;
  // 右上是事实：能选几条，或者已经选了哪条
  setScriptNote("topic", picked ? `已选 ${picked.id}` : topics.length ? `可选 ${topics.length} 条` : "", Boolean(picked));
  if (!shown.length) {
    // 空的时候要说清为什么空：是搜不到、还是已经分析过的都开过内容了
    const names = producedTopics.slice(0, 3).map((topic) => topic.id).join("、");
    const hint = topics.length
      ? "没有匹配的选题"
      : producedTopics.length
        ? `已经分析过的选题都开过内容了（${names}${producedTopics.length > 3 ? " 等" : ""}）。一条选题只对应一条内容，不能重复新建。`
        : "还没有分析过的选题，先去「选题分析」跑一条。";
    list.innerHTML = `<p class="dd-pick-empty">${hint}</p>`;
    return;
  }
  list.innerHTML = shown.map((topic) => {
    const on = promptState.topic?.id === topic.id;
    return `<button class="dd-pick${on ? " is-on" : ""}" type="button" role="option" aria-selected="${on}" data-action="pick-script-topic" data-topic-id="${escapeAttribute(topic.id)}">
      <span class="dd-pick-tag ${recommendationClass(topic.recommendation)}">${escapeHtml(topic.recommendation || "待判断")}</span>
      <span class="dd-pick-num">${escapeHtml(topic.id)}</span>
      <span class="dd-pick-title">${escapeHtml(topic.name)}</span>
    </button>`;
  }).join("");
}

function renderScriptBenchList(keyword = "") {
  const list = document.querySelector("#tpl-bench-list");
  if (!list) return;
  const { benchmarks } = scriptModeOptions();
  const kw = String(keyword || "").trim();
  const shown = kw
    ? benchmarks.filter((item) => `${item.title || ""} ${item.account_name || ""}`.includes(kw))
    : benchmarks;
  const picked = promptState.bench;
  setScriptNote(
    "bench",
    picked ? `已选 ${picked.account_name || "这条"}` : benchmarks.length ? `可选 ${benchmarks.length} 条` : "",
    Boolean(picked),
  );
  if (!shown.length) {
    list.innerHTML = `<p class="dd-pick-empty">${benchmarks.length ? "没有匹配的对标内容" : "还没有分析完成的对标内容"}</p>`;
    return;
  }
  list.innerHTML = shown.map((item) => {
    const on = promptState.bench?.key === item.key;
    return `<button class="dd-pick${on ? " is-on" : ""}" type="button" role="option" aria-selected="${on}" data-action="pick-script-bench" data-key="${escapeAttribute(item.key)}">
      <span class="dd-pick-plat">${escapeHtml(item.platform || "来源")}</span>
      <span class="dd-pick-title">${escapeHtml(item.title || "未命名内容")}</span>
      <span class="dd-pick-meta">${escapeHtml(item.account_name || "")}</span>
    </button>`;
  }).join("");
}

function applyScriptMode(mode, options = {}) {
  const next = ["direct", "topic", "benchmark"].includes(mode) ? mode : "direct";
  promptState.mode = next;
  document.querySelectorAll("#tpl-script-tabs [data-mode]").forEach((button) => {
    const on = button.dataset.mode === next;
    button.classList.toggle("is-on", on);
    button.setAttribute("aria-selected", String(on));
  });
  const paneTopic = document.querySelector("#tpl-pane-topic");
  const paneBench = document.querySelector("#tpl-pane-benchmark");
  if (paneTopic) paneTopic.hidden = next !== "topic";
  if (paneBench) paneBench.hidden = next !== "benchmark";
  // 想法只给「直接生产」，参考链接只给「根据对标生产」，补充想法只给后两个
  elements.promptFieldIdea.hidden = next !== "direct";
  elements.promptFieldRef.hidden = next !== "benchmark";
  const extraField = document.querySelector("#tpl-field-extra");
  if (extraField) extraField.hidden = next === "direct";
  // 「或者」只有对标这一档才有：上面是列表，下面是链接，二选一
  const refOr = document.querySelector("#tpl-ref-or");
  if (refOr) refOr.hidden = next !== "benchmark";
  if (elements.promptExtra) {
    elements.promptExtra.placeholder = next === "benchmark"
      ? "不写就完全按对标分析的结论来。"
      : "不写就完全按选题分析的结论来。";
  }
  // 三个 tab 卡片自己带一句「什么时候用它」，弹窗里不再另起一行说明
  elements.promptIntro.hidden = true;
  // 列表一直铺在面板里：切回来时按当前的选择状态重画一遍
  if (next === "topic") renderScriptTopicList(document.querySelector("#tpl-topic-search")?.value || "");
  if (next === "benchmark") renderScriptBenchList(document.querySelector("#tpl-bench-search")?.value || "");
  // 切到要挑东西的两档，光标直接落在搜索框，不用再点一次（键盘切 tab 时不动焦点）
  if (next !== "direct" && options.focus !== false && elements.promptDialog.open) {
    document.querySelector(next === "topic" ? "#tpl-topic-search" : "#tpl-bench-search")?.focus();
  }
  syncPrompt();
}

function pickScriptTopic(topicId) {
  const { topics } = scriptModeOptions();
  // 再点一次就是取消：选错了不用去别处找重置
  promptState.topic = promptState.topic?.id === topicId
    ? null
    : topics.find((topic) => topic.id === topicId) || null;
  renderScriptTopicList(document.querySelector("#tpl-topic-search")?.value || "");
  syncPrompt();
}

function pickScriptBench(key) {
  const { benchmarks } = scriptModeOptions();
  if (promptState.bench?.key === key) {
    promptState.bench = null;
  } else {
    promptState.bench = benchmarks.find((item) => item.key === key) || null;
    // 二选一：挑了列表里的就把链接清掉，免得指令里用的是另一条
    if (elements.promptRef) elements.promptRef.value = "";
  }
  renderScriptBenchList(document.querySelector("#tpl-bench-search")?.value || "");
  syncPrompt();
}

function openPromptDialog(kind, topic = null, options = {}) {
  const preset = PROMPT_PRESETS[kind];
  if (!preset) return;
  promptState = {
    kind,
    topic,
    mode: "direct",
    bench: null,
    mediaForm: PRODUCTION_DEFAULTS.mediaForm,
    goal: PRODUCTION_DEFAULTS.goal,
  };
  elements.promptTitle.textContent = preset.title;
  elements.promptIntro.textContent = preset.intro || "";
  elements.promptIntro.hidden = !preset.intro;
  elements.promptIdeaLabel.innerHTML = preset.ideaLabel || "想法";
  elements.promptIdea.placeholder = preset.placeholder || "想讲什么、从哪个角度切、为什么现在想做";
  elements.promptIdea.value = "";
  elements.promptRef.value = "";
  elements.promptForm.hidden = !(preset.idea || preset.ref);
  elements.promptFieldIdea.hidden = !preset.idea;
  elements.promptFieldRef.hidden = !preset.ref;
  const refErrorEl = document.querySelector("#tpl-ref-error");
  const refHintEl = document.querySelector("#tpl-ref-hint");
  if (refErrorEl) { refErrorEl.hidden = true; refErrorEl.textContent = ""; }
  if (refHintEl) {
    refHintEl.hidden = !preset.checkRef;
    refHintEl.classList.remove("is-ok");
    if (preset.checkRef) refHintEl.textContent = CONTENT_HINT;
  }
  clearPromptErrors();
  /* 「或者」这一行只属于新增脚本的对标档；别的预设进来先收起来（对标档由 applyScriptMode 放出来） */
  const refOrEl = document.querySelector("#tpl-ref-or");
  if (refOrEl) refOrEl.hidden = true;

  // 只有「新增脚本」有三种生产方式；别的用途把 tab 和两块选择面板一起收起来。
  // hideModes：从「用该选题进行内容创作」进来时就是直接生产，不给选，也别摆三张卡。
  const tabs = document.querySelector("#tpl-script-tabs");
  if (tabs) tabs.hidden = !preset.tabs || Boolean(options.hideModes);
  const paneTopic = document.querySelector("#tpl-pane-topic");
  const paneBench = document.querySelector("#tpl-pane-benchmark");
  if (paneTopic) paneTopic.hidden = true;
  if (paneBench) paneBench.hidden = true;
  const topicSearch = document.querySelector("#tpl-topic-search");
  const benchSearch = document.querySelector("#tpl-bench-search");
  if (topicSearch) topicSearch.value = "";
  if (benchSearch) benchSearch.value = "";
  if (elements.promptExtra) elements.promptExtra.value = "";
  const extraField = document.querySelector("#tpl-field-extra");
  if (extraField) extraField.hidden = true;

  // 形式 / 目标：带 settings 的预设（开始生产 / 新增脚本）才出现；每次打开都回到默认（视频 + 流量）
  const settingsBox = document.querySelector("#tpl-prod-settings");
  if (settingsBox) {
    settingsBox.hidden = !preset.settings;
    if (preset.settings) {
      paintProductionSettings(document.querySelector("#tpl-media-forms"), "media-form", promptState.mediaForm);
      paintProductionSettings(document.querySelector("#tpl-goals"), "goal", promptState.goal);
    }
  }
  if (preset.tabs) applyScriptMode("direct");
  else syncPrompt();

  elements.promptDialog.showModal();
  requestAnimationFrame(updatePromptFade);
  if (preset.idea) elements.promptIdea.focus();
  else {
    /* 没有要填的（开始生产），把焦点收回到弹窗，别停在右上角 × 上 */
    elements.promptDialog.setAttribute("tabindex", "-1");
    elements.promptDialog.focus();
  }
}

/* 写剪贴板。
   先用剪贴板 API —— 它是异步的、要求安全上下文，而且在 iframe 里要页面给 clipboard-write
   权限、窗口失焦时会直接 reject。这些情况在客户端内嵌预览里都会遇到，所以失败后一律再用
   execCommand 试一次：那条路只要还在用户点击的手势里就能成。两条都不成才返回 false。 */
async function writeClipboard(text) {
  if (!text) return false;
  if (navigator.clipboard?.writeText) {
    try {
      await navigator.clipboard.writeText(text);
      return true;
    } catch (_error) {
      /* 落到下面的兜底 */
    }
  }
  const sink = document.createElement("textarea");
  sink.value = text;
  sink.setAttribute("readonly", "");
  sink.style.cssText = "position:fixed;top:-1000px;left:0;width:1px;height:1px;opacity:0;";
  document.body.appendChild(sink);
  try {
    sink.select();
    return document.execCommand("copy");
  } catch (_error) {
    return false;
  } finally {
    sink.remove();
  }
}

async function copyPromptText() {
  const text = elements.promptText.textContent || "";
  if (!text) return;
  const original = elements.promptCopy.innerHTML;
  const ok = await writeClipboard(text);
  if (ok) {
    setInlineError(elements.promptError, "");
    elements.promptCopy.innerHTML = '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M5 12.5 9.5 17 19 7.5"/></svg>已复制';
    elements.promptCopy.disabled = true;
    window.setTimeout(() => {
      elements.promptCopy.innerHTML = original;
      elements.promptCopy.disabled = false;
    }, 1800);
    return;
  }
  /* 两条路都没成：把文字选中，让用户自己按 Cmd+C，并且明说没复制上 */
  const range = document.createRange();
  range.selectNodeContents(elements.promptText);
  const selection = window.getSelection();
  selection.removeAllRanges();
  selection.addRange(range);
  setInlineError(elements.promptError, "没能自动复制，已经帮你选中了，按 Cmd+C 就行");
}

/* ==================== 选题抽屉 ====================
   抽屉内容全部来自选题分析记录（我方资料/选题分析/*.md）和选题库主表，
   不额外生成原文里没有的结论。记录读不到时只展示主表字段。 */

/* 选题定义的六项，括注是分析记录里固定带的问法，不是这里现编的 */
const TOPIC_DEF_FIELDS = [
  ["主题", "这个题主要讲什么", "theme"],
  ["目标用户", "主要写给谁", "audience"],
  ["核心痛点", "他们现在卡在哪里", "pain_point"],
  ["收益承诺", "看完能得到什么", "benefit_promise"],
  ["核心命题", "最后希望用户相信什么", "core_proposition"],
  ["切口", "从哪个具体角度开始讲", "angle"],
];

const TOPIC_ANALYSIS_CACHE = new Map();
let topicDrawerId = "";

/* 只取「判断理由」下缩进两格的子项，和「参考内容借鉴」的表体 */
function parseTopicAnalysis(markdown) {
  const reasons = [];
  const borrow = [];
  let section = "";
  String(markdown || "").split(/\r?\n/).forEach((raw) => {
    const line = raw.trim();
    if (line.startsWith("## ")) {
      section = line.slice(3).trim();
      return;
    }
    if (section === "选题结论" && /^\s{2,}-\s+/.test(raw)) {
      reasons.push(line.replace(/^-\s+/, ""));
      return;
    }
    if (section === "参考内容借鉴" && line.startsWith("|")) {
      const cells = line.replace(/^\|/, "").replace(/\|$/, "").split("|").map((cell) => cell.trim());
      if (cells.length >= 3 && cells[0] !== "维度" && !/^[-:\s]+$/.test(cells[0])) {
        borrow.push({ dimension: cells[0], verdict: cells[1], content: cells[2] });
      }
    }
  });
  return { reasons, borrow };
}

async function readTopicAnalysis(path) {
  if (!path) return null;
  if (TOPIC_ANALYSIS_CACHE.has(path)) return TOPIC_ANALYSIS_CACHE.get(path);
  let parsed = null;
  try {
    const response = await fetch(`/api/file?path=${encodeURIComponent(path)}`, { cache: "no-store" });
    if (response.ok) {
      const payload = await response.json();
      parsed = parseTopicAnalysis(payload.content || "");
    }
  } catch (_error) {
    parsed = null;
  }
  TOPIC_ANALYSIS_CACHE.set(path, parsed);
  return parsed;
}

function currentTopic(id) {
  const topics = state.snapshot?.workflows?.production?.topics || [];
  return topics.find((item) => item.id === id) || null;
}

/* 参考内容一行字：说明 > 链接（压成「平台 · 内容ID」）> 只有分析文件时的兜底 */
function topicReferenceText(topic) {
  if (String(topic.reference_description || "").trim()) return String(topic.reference_description).trim();
  const url = String(topic.reference_url || "").trim();
  if (url) {
    const platform = /douyin/.test(url) ? "抖音" : /xiaohongshu|xhslink/.test(url) ? "小红书" : /bilibili|b23/.test(url) ? "B站" : "";
    const idMatch = url.match(/video\/(\d+)/) || url.match(/\/(\d{10,})/);
    const text = [platform, idMatch ? idMatch[1] : ""].filter(Boolean).join(" · ");
    if (text) return text;
    return url;
  }
  if (String(topic.reference_analysis_path || "").trim()) return "已有内容分析";
  return "";
}

function topicDrawerHint(tone, status) {
  if (String(status || "").trim() === "已生产") return "这条已经进过脚本创作";
  if (tone === "go") return "判断是「建议做」，还没生产";
  if (tone === "hold") return "判断是「暂缓」，还没到能生产的程度";
  if (tone === "no") return "判断是「不建议做」";
  return "还没有判断，先补一次选题分析";
}

function renderTopicDrawer(topic, analysis) {
  const state = topicState(topic);
  const reasons = analysis?.reasons || [];
  const borrow = analysis?.borrow || [];

  /* 参考内容卡：登记过对标内容分析时，整卡可点，点了在右侧开这条对标内容的抽屉 */
  const refKey = String(topic.reference_content_key || "").trim();
  const refId = refKey ? refKey.split("::").pop() : "";
  const refText = topicReferenceText(topic);
  const refBody = `<span class="tpl-ref-cover"><svg viewBox="0 0 24 24" aria-hidden="true"><rect x="3.4" y="6.4" width="13" height="13" rx="2.4"/><path d="M7.6 3.6h10a2 2 0 0 1 2 2v10"/></svg></span>
      <span class="tpl-ref-copy"><b>参考内容分析</b><span>${refText ? escapeHtml(refText) : "无"}</span></span>
      ${refId ? '<svg class="tpl-ref-arrow" viewBox="0 0 24 24" aria-hidden="true"><path d="M9 6l6 6-6 6"/></svg>' : ""}`;
  const refCard = refKey
    ? `<button class="tpl-ref-card" type="button" data-action="open-benchmark-content-drawer" data-key="${escapeAttribute(refKey)}" title="打开这条对标内容">${refBody}</button>`
    : `<div class="tpl-ref-card">${refBody}</div>`;

  /* 关联内容卡：有生产内容时同样可点，点了开这条内容的抽屉 */
  const contentText = topic.content_id
    ? [topic.content_id, topic.content_name].filter(Boolean).join(" · ")
    : "";
  const contentBody = `<span class="tpl-ref-cover"><svg viewBox="0 0 24 24" aria-hidden="true"><path d="M6 3.5h8l4 4v13H6z"/><path d="M14 3.5v4.5h4.5"/></svg></span>
      <span class="tpl-ref-copy"><b>关联内容</b><span>${contentText ? escapeHtml(contentText) : "无"}</span></span>
      ${topic.content_id ? '<svg class="tpl-ref-arrow" viewBox="0 0 24 24" aria-hidden="true"><path d="M9 6l6 6-6 6"/></svg>' : ""}`;
  const contentCard = topic.content_id
    ? `<button class="tpl-ref-card" type="button" data-action="open-content-drawer" data-content-id="${escapeAttribute(topic.content_id)}" title="打开这条内容">${contentBody}</button>`
    : `<div class="tpl-ref-card">${contentBody}</div>`;

  const topHtml = `<div class="tpl-topres">${refCard}${contentCard}</div>`;

  const whyHtml = reasons.length
    ? `<div class="tpl-why">
        <div class="tpl-why-h">判断理由<em>${reasons.length} 条</em></div>
        <ol class="tpl-why-list">${reasons.map((text, index) => `<li><span>${String(index + 1).padStart(2, "0")}</span><p>${escapeHtml(text)}</p></li>`).join("")}</ol>
      </div>`
    : "";

  /* 01 块只放一个互斥状态标签，和列表卡完全一致 */
  const verdictHtml = `<section class="tpl-blk">
      <div class="tpl-blk-h"><span class="no">01</span><b>选题结论</b></div>
      <div class="tpl-verdict">
        <span class="tpl-badge ${state}">${escapeHtml(TOPIC_STATE_LABEL[state])}</span>
        <div class="vc">
          <b>核心判断</b>
          <p>${escapeHtml(String(topic.reason || "").trim() || "还没有核心判断")}</p>
        </div>
      </div>
      ${whyHtml}
    </section>`;

  const defRows = TOPIC_DEF_FIELDS.map(([name, note, key], index) => {
    const value = String(topic[key] || "").trim();
    return `<div class="tpl-def-row">
      <div class="tpl-def-key"><b><i>${index + 1}</i>${name}</b><span>${note}</span></div>
      <div class="tpl-def-val${value ? "" : " is-empty"}">${value ? escapeHtml(value) : "还没填"}</div>
    </div>`;
  }).join("");
  const defHtml = `<section class="tpl-blk">
      <div class="tpl-blk-h"><span class="no">02</span><b>选题定义</b><em>${TOPIC_DEF_FIELDS.length} 项</em></div>
      <div class="tpl-defs">${defRows}</div>
    </section>`;

  const borrowHtml = borrow.length
    ? `<section class="tpl-blk">
        <div class="tpl-blk-h"><span class="no">03</span><b>参考内容借鉴</b><em>${borrow.length} 个维度</em></div>
        <div class="tpl-borrow">${borrow.map((row) => `<div class="tpl-borrow-row">
          <div class="tpl-borrow-k"><b>${escapeHtml(row.dimension)}</b><span class="tpl-btag">${escapeHtml(row.verdict)}</span></div>
          <p>${escapeHtml(row.content)}</p>
        </div>`).join("")}</div>
      </section>`
    : "";

  elements.topicDrawerBody.innerHTML = topHtml + verdictHtml + defHtml + borrowHtml;

  /* 已经生产过的选题只作记录：不给任何操作按钮 */
  const buttons = [];
  if (state !== "made" && topic.id) {
    const id = escapeAttribute(topic.id);
    buttons.push(`<button class="tpl-primary" type="button" data-action="produce-topic" data-topic-id="${id}"><svg class="is-solid" viewBox="0 0 24 24" aria-hidden="true"><path d="M8.5 5.5v13l10-6.5z"/></svg>开始生产</button>`);
    buttons.push(`<button class="tpl-danger-button" type="button" data-action="delete-topic" data-topic-id="${id}"><svg viewBox="0 0 24 24" aria-hidden="true"><path d="M4 7h16M9 7V5h6v2M6.5 7l.8 12.2A1.8 1.8 0 0 0 9.1 21h5.8a1.8 1.8 0 0 0 1.8-1.8L17.5 7"/></svg>删除</button>`);
  }
  elements.topicDrawerFoot.innerHTML = buttons.join("");
}
function openTopicDrawer(id) {
  const topic = currentTopic(id);
  if (!topic) return;
  topicDrawerId = id;
  const updated = String(topic.updated_at || "").slice(0, 16);
  elements.topicDrawerTitle.innerHTML = `<span class="tpl-num">${escapeHtml(topic.id || "未编号")}</span><span>${escapeHtml(topic.name || "—")}</span>`;
  elements.topicDrawerSub.textContent = updated ? `最新更新时间：${updated}` : "";
  elements.topicDrawerBody.innerHTML = '<div class="tpl-drawer-note">正在读取选题分析…</div>';
  elements.topicDrawerFoot.innerHTML = "";
  setInlineError(elements.drawerError, "");
  if (!elements.topicDrawer.open) elements.topicDrawer.showModal();
  requestAnimationFrame(() => elements.topicDrawer.classList.add("is-open"));

  if (!topic.analysis_path) {
    renderTopicDrawer(topic, null);
    return;
  }
  readTopicAnalysis(topic.analysis_path).then((analysis) => {
    if (topicDrawerId !== id) return;
    renderTopicDrawer(topic, analysis);
  });
}

/* ---------- 删除选题 ---------- */
let pendingDeleteId = "";

function openDeleteConfirm(id) {
  const topic = currentTopic(id);
  if (!topic) return;
  pendingDeleteId = id;
  pendingContentDeleteId = "";
  pendingDictDeleteId = "";
  elements.confirmTitle.textContent = "删除这条选题？";
  elements.topicConfirmCopy.textContent = `「${topic.name || id}」会从选题分析里移除，这条记录对应的文件会被移到回收目录。`;
  const lines = [
    `列表行：${topic.id}`,
    topic.analysis_path ? `分析记录：${topic.analysis_path}` : "",
    "删除前的选题分析整表备份也会留在 我方资料/.trash/ 里",
  ].filter(Boolean);
  elements.topicConfirmList.innerHTML = lines.map((line) => `<li>${escapeHtml(line)}</li>`).join("");
  setInlineError(elements.confirmError, "");
  elements.topicConfirm.showModal();
}

async function confirmDeleteTopic() {
  const id = pendingDeleteId;
  if (!id) return;
  elements.topicConfirmOk.disabled = true;
  elements.topicConfirmOk.textContent = "正在删除…";
  try {
    const response = await fetch("/api/topic/delete", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ id }),
    });
    const payload = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(payload.error || `delete_failed_${response.status}`);
    pendingDeleteId = "";
    elements.topicConfirm.close();
    closeTopicDrawer();
    await loadSnapshot({ force: true });
  } catch (error) {
    setInlineError(elements.confirmError, `删除没成功：${error.message || "服务端没有返回原因"}。文件和表格都没动，可以重试。`);
  } finally {
    elements.topicConfirmOk.disabled = false;
    elements.topicConfirmOk.textContent = "确认删除";
  }
}

function closeTopicDrawer() {
  const dialog = elements.topicDrawer;
  if (!dialog || !dialog.open) return;
  topicDrawerId = "";
  dialog.classList.remove("is-open");
  window.setTimeout(() => {
    if (!dialog.classList.contains("is-open")) dialog.close();
  }, 300);
}

/* ---------- 删除脚本内容 ---------- */
let pendingContentDeleteId = "";

function openContentDeleteConfirm(id) {
  const content = contentRecord("own", id);
  if (!content) return;
  pendingContentDeleteId = id;
  pendingDeleteId = "";
  pendingDictDeleteId = "";
  elements.confirmTitle.textContent = "删除这条内容？";
  elements.topicConfirmCopy.textContent = `「${content.title || id}」会从脚本创作里移除，内容目录和生产记录会被移到回收目录。`;
  const lines = [
    `列表行：${content.id}`,
    content.directory_path ? `内容目录：${content.directory_path}` : "",
    content.production_record_path ? `生产记录：${content.production_record_path}` : "",
    content.topic_id ? `关联选题 ${content.topic_id} 会回到「已完成分析」` : "",
    "删除前的内容库整表备份也会留在 我方资料/.trash/ 里",
  ].filter(Boolean);
  elements.topicConfirmList.innerHTML = lines.map((line) => `<li>${escapeHtml(line)}</li>`).join("");
  setInlineError(elements.confirmError, "");
  elements.topicConfirm.showModal();
}

async function confirmDeleteContent() {
  const id = pendingContentDeleteId;
  if (!id) return;
  elements.topicConfirmOk.disabled = true;
  elements.topicConfirmOk.textContent = "正在删除…";
  try {
    const response = await fetch("/api/content/delete", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ id }),
    });
    const payload = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(payload.error || `delete_failed_${response.status}`);
    pendingContentDeleteId = "";
    elements.topicConfirm.close();
    closeContentDrawer();
    await loadSnapshot({ force: true });
  } catch (error) {
    setInlineError(elements.confirmError, `删除没成功：${error.message || "服务端没有返回原因"}。文件和表格都没动，可以重试。`);
  } finally {
    elements.topicConfirmOk.disabled = false;
    elements.topicConfirmOk.textContent = "确认删除";
  }
}

/* ==================== 内容详情抽屉 ====================
   抽屉里就是内容详情页本身（content.html 的 embed 模式），不做删减、不重画：
   我方内容的「内容定位 / 内容初稿 / 平台审查 / 内容终稿」、对标内容的
   「概要 / 学习结论 / 内容拆解 / 观众反馈」、版本切换、空态全都原样，
   只是把整页塞进 900 宽的抽屉、去掉侧栏和顶栏。
   两种内容共用一个抽屉，靠 kind 区分：own 认内容ID，benchmark 认 key。 */

let contentDrawerRef = null;

function contentRecord(kind, id) {
  if (kind === "benchmark") {
    // ⚠️ 对标内容认的是 key（平台ID::内容ID），不是内容ID
    const items = state.snapshot?.workflows?.insight?.benchmark_contents || [];
    return items.find((item) => item.key === id) || null;
  }
  const items = state.snapshot?.workflows?.production?.own_contents || [];
  return items.find((item) => item.id === id) || null;
}

// 抽屉里的 iframe 地址带版本号：否则改完样式，浏览器可能还拿上一次的 content.html / css。
const CONTENT_ASSET_VERSION = "20261102-review1";

function contentPageUrl(kind, id, returnTo, embed) {
  const params = new URLSearchParams({ kind, id, v: CONTENT_ASSET_VERSION });
  if (returnTo) params.set("return_to", returnTo);
  if (embed) params.set("embed", "1");
  return `content.html?${params.toString()}`;
}

function renderContentDrawer(ref, item) {
  // 抽屉里只有内容详情页本身：顶部那行返回/刷新、环节 tab、正文全是详情页自己的，
  // 抽屉不额外加标题，只在右下角给「我方的脚本内容」留一个删除按钮。
  const returnTo = ref.kind === "benchmark" ? "index.html#benchmark-contents" : "index.html#creation-board";
  const title = item.title || item.name || ref.id;
  elements.contentDrawerBody.innerHTML = `<iframe class="tpl-dr-frame" src="${escapeAttribute(contentPageUrl(ref.kind, ref.id, returnTo, true))}" title="${escapeAttribute(title)} 的内容详情"></iframe>`;
  elements.contentDrawerFoot.innerHTML = ref.kind === "own"
    ? `<button class="tpl-danger-button" type="button" data-action="delete-content" data-content-id="${escapeAttribute(ref.id)}"><svg viewBox="0 0 24 24" aria-hidden="true"><path d="M4 7h16M9 7V5h6v2M6.5 7l.8 12.2A1.8 1.8 0 0 0 9.1 21h5.8a1.8 1.8 0 0 0 1.8-1.8L17.5 7"/></svg>删除</button>`
    : "";
}

function openContentDrawer(kind, id) {
  const item = contentRecord(kind, id);
  if (!item) return;
  contentDrawerRef = { kind, id };
  renderContentDrawer(contentDrawerRef, item);
  if (!elements.contentDrawer.open) elements.contentDrawer.showModal();
  requestAnimationFrame(() => elements.contentDrawer.classList.add("is-open"));
}

function closeContentDrawer() {
  const dialog = elements.contentDrawer;
  if (!dialog || !dialog.open) return;
  contentDrawerRef = null;
  dialog.classList.remove("is-open");
  window.setTimeout(() => {
    if (!dialog.classList.contains("is-open")) {
      dialog.close();
      // 关掉后清空 iframe 和底栏，下次打开重新拉一次数据，避免停在旧版本
      elements.contentDrawerBody.innerHTML = "";
      elements.contentDrawerFoot.innerHTML = "";
    }
  }, 300);
}

/* 内容生产卡：顶行＝内容编号 + 生产状态 + 日期，下面标题和核心主题，底部标来源。
   来源就是这条内容的三种生产方式：
   - 直接生产：没有关联对象，不标（保持原样）
   - 经过选题分析后生产：标出关联的选题
   - 经过对标内容分析后生产：标出关联的对标内容
   两个都有的极少，优先标对标内容（参考内容分析路径是内容记录里更硬的那条）。 */
function productionSource(item) {
  const key = String(item?.reference_content_key || "").trim();
  if (key) {
    const benchmark = (state.snapshot?.workflows?.insight?.benchmark_contents || [])
      .find((content) => content.key === key);
    // ⚠️ 对标内容详情页认的是 key（平台ID::内容ID），不是内容ID —— 传 id 会「未找到该内容」
    return {
      kind: "benchmark",
      label: "对标内容",
      title: benchmark?.title || "已删除的对标内容",
      target: key,
    };
  }
  if (String(item?.topic_id || "").trim()) {
    return { kind: "topic", label: "选题", title: item.topic_name || item.topic_id, target: item.topic_id };
  }
  return null;
}

/* 来源那一行：标签 + 可点的标题。点选题、点对标内容都是开对应抽屉，不跳页。
   ⚠️ 卡片本身是 <button>，这里只能用带 data-action 的 <b>，靠全局代理的 closest 命中。 */
function productionSourceHtml(source) {
  if (!source) return "";
  const title = escapeHtml(source.title);
  const body = source.kind === "benchmark" && source.target
    ? `<b role="link" tabindex="0" data-action="open-benchmark-content-drawer" data-key="${escapeAttribute(source.target)}" title="打开这条对标内容">${title}</b>`
    : source.kind === "topic" && source.target
      ? `<b role="link" tabindex="0" data-action="open-topic-drawer" data-topic-id="${escapeAttribute(source.target)}" title="打开这个选题">${title}</b>`
      : `<b>${title}</b>`;
  return `<p class="tpl-source"><span>${source.label}</span>${body}</p>`;
}

function productionCard(item) {
  const status = String(item.status || "").trim() || "待创作";
  const tone = status === "已完成" ? "done" : status === "生产中" ? "run" : "todo";
  const date = String(item.produced_at || "").slice(5, 10);
  const core = String(item.core || "").trim();
  return `<button type="button" class="tpl-card" data-tone="${tone}" data-action="open-content-drawer" data-content-id="${escapeAttribute(item.id)}" title="打开内容详情">
    <div class="tpl-top">
      <span class="tpl-num">${escapeHtml(item.id || "未编号")}</span>
      <span class="tpl-badge ${tone}">${escapeHtml(status)}</span>
      <span class="tpl-date">${escapeHtml(date)}</span>
    </div>
    <h3 class="tpl-title">${escapeHtml(item.title || "—")}</h3>
    <p class="tpl-core">${escapeHtml(core || "还没有内容定位")}</p>
    ${productionSourceHtml(productionSource(item))}
  </button>`;
}


function table(headers, rows, emptyTitle, tableClass = "") {
  if (!rows.length) return emptyState(emptyTitle);
  const classes = ["data-table", tableClass].filter(Boolean).join(" ");
  return `<div class="table-wrap"><table class="${classes}"><thead><tr>${headers.map((header) => `<th>${escapeHtml(header)}</th>`).join("")}</tr></thead><tbody>${rows.join("")}</tbody></table></div>`;
}

function engagementMetric(icon, label, value) {
  const displayValue = formatNumber(value);
  const paths = {
    like: '<path d="M20.8 4.8a5.2 5.2 0 0 0-7.4 0L12 6.2l-1.4-1.4a5.2 5.2 0 0 0-7.4 7.4L12 21l8.8-8.8a5.2 5.2 0 0 0 0-7.4Z"/>',
    favorite: '<path d="M6 3h12v18l-6-3.8L6 21Z"/>',
    comment: '<path d="M20 11.5a7.5 7.5 0 0 1-8 7.5 8.5 8.5 0 0 1-3.7-.8L4 20l1.2-3.5A7.1 7.1 0 0 1 4 12a7.5 7.5 0 0 1 8-7.5 7.5 7.5 0 0 1 8 7Z"/>',
    share: '<path d="m3 12 18-9-6.5 18-3-7Z"/><path d="m11.5 14 3-3"/>',
  };
  return `<span class="engagement-item" title="${label} ${displayValue}"><svg aria-hidden="true" viewBox="0 0 24 24">${paths[icon]}</svg><span>${displayValue}</span></span>`;
}

function contentEngagement(metrics = {}) {
  return `<div class="content-engagement" aria-label="内容互动数据">${[
    engagementMetric("like", "点赞", metrics.likes),
    engagementMetric("favorite", "收藏", metrics.favorites),
    engagementMetric("comment", "评论", metrics.comments),
    engagementMetric("share", "分享", metrics.shares),
  ].join("")}</div>`;
}

/* ==================================================================
   新手引导：3 步，全部按「有没有真的做过」判，不写死。
   1 安装准备（装上了就算完成）
   2 填写资料（我方资料/调查问卷.md 有答案，只有题目骨架不算）
   3 诊断一次账号（账号目录里有一份账号详细诊断）
   —— 「推荐一次选题」「生成一次脚本」两步已删（2026-11-02）：
      这两步在首页只是「有个入口能点」，做完也说明不了什么。
   —— 「生成账号方向」这一步已删（2026-11-01）：账号方向不再单独占一步，
      职责合并到第一次诊断里（绑定并诊断那条指令先绑号跑诊断，诊断完再按问卷 + 这份诊断把四栏方向写出来）。
   —— 原来的「拆解一次爆款」第 6 步已删（2026-10-31）：爆款拆解属于进阶动作，
      新手第一轮不做它也能把内容跑出来。
   ================================================================== */
const ONBOARDING_STEPS = [
  // target = 点这一步滚到哪、亮哪个入口；没有 target 的步骤直接执行 action
  { id: "install", label: "安装准备", action: "", cta: "", target: "" },
  // 「填写资料」：首页就是问卷时滚到问卷，别的页面才开抽屉（见 ONBOARDING_ACTIONS.survey）
  { id: "survey", label: "填写资料", action: "focus-home-survey", cta: "填写资料", target: "" },
  // 诊断这一步就是「绑定并诊断我的账号」：先绑号诊断，诊断完再写方向
  { id: "diagnose", label: "诊断一次账号", action: "first-diagnose-account", cta: "诊断一次账号", target: "#accCard" },
];

/* 第一次诊断做完了没有：账号目录里有一份详细诊断，或 AI 引导账本记了 done。
   首页「推荐选题」这块要不要给入口，看的就是这个。 */
function homeDiagnosed() {
  const self = state.snapshot?.workflows?.self || {};
  if ((self.own_accounts || []).some((account) => account.documents?.detail)) return true;
  return (self.guide?.steps || {}).diagnose === "done";
}

function onboardingFlags() {
  const workflows = state.snapshot?.workflows || {};
  const self = workflows.self || {};
  /* AI 那边的引导账本（.meiling/引导状态.json）。两个来源取并集：
     账本说 done 的算做完（AI 刚记完，刷新就能看到）；文件里能看到真做过的也算
     （用户没走 AI、直接在工作台做完的情况）。没有账本时整段自动失效。 */
  const guideSteps = self.guide?.steps || {};
  /* 只认 done，skipped 不算：skipped 是「这一步这次不做」（比如没账号跳过诊断），
     不是做过了，打勾会骗人。 */
  const done = (id) => guideSteps[id] === "done";
  return [
    true,
    Boolean(self.onboarding?.survey_filled),
    homeDiagnosed() || done("diagnose"),
  ];
}

/* 前进一步时的动效：dot 弹一下 + 勾画出来 + 进度线往右长。
   上一次的状态记在卡片自己身上（dataset），整卡重画时新节点没有记录、不重播。 */
function playOnboardingAdvance(card, flags) {
  const previous = card.dataset.onbFlags ? card.dataset.onbFlags.split("").map((v) => v === "1") : null;
  card.dataset.onbFlags = flags.map((v) => (v ? "1" : "0")).join("");
  if (!previous || previous.length !== flags.length) return;

  let advanced = false;
  card.querySelectorAll("[data-onb-step]").forEach((node) => {
    const index = ONBOARDING_STEPS.findIndex((step) => step.id === node.dataset.onbStep);
    if (index < 0 || !flags[index] || previous[index]) return;
    advanced = true;
    node.classList.remove("step-pop");
    void node.offsetWidth;
    node.classList.add("step-pop");
    window.setTimeout(() => node.classList.remove("step-pop"), 900);
  });
  if (!advanced) return;

  // 卡片本身也给一下：一道浅绿从左边掠过，眼睛跟得住「刚完成了一步」
  card.classList.remove("onb-advance");
  void card.offsetWidth;
  card.classList.add("onb-advance");
  window.setTimeout(() => card.classList.remove("onb-advance"), 1400);
}

function renderOnboarding(root) {
  const card = root.querySelector("#onb");
  if (!card) return;
  const flags = onboardingFlags();
  const total = ONBOARDING_STEPS.length;
  const doneCount = flags.filter(Boolean).length;

  // 进度线只画到「第一个没完成的前一格」：中间有跳着做完的，线不跟着拐
  let streak = 0;
  while (streak < total && flags[streak]) streak += 1;

  card.querySelectorAll("[data-onb-step]").forEach((node) => {
    const index = ONBOARDING_STEPS.findIndex((step) => step.id === node.dataset.onbStep);
    if (index < 0) return;
    node.classList.toggle("done", Boolean(flags[index]));
    node.classList.toggle("now", index === streak);
  });

  playOnboardingAdvance(card, flags);

  const counter = card.querySelector("[data-onb-count]");
  if (counter) counter.textContent = `已完成 ${doneCount} / ${total}`;

  // 全部走完就收起：留着也没事可做了。
  // 【临时停用 · 2026-10-31】现在要看全部完成时的样子，先把自动收起关掉；
  // 恢复时把下面这 5 行取消注释即可。
  // if (doneCount === total) {
  //   card.classList.add("closing");
  //   window.setTimeout(() => card.remove(), 380);
  //   return;
  // }

  // CSP 是 style-src 'self'，行内 style 属性会被丢掉，这里走 CSSOM 赋值
  const stepper = card.querySelector(".stepper");
  if (stepper) stepper.style.setProperty("--onb-done", String(Math.max(streak, 1)));

  const next = ONBOARDING_STEPS[streak];
  const cta = card.querySelector("[data-onb-cta]");
  if (cta) {
    if (next) {
      cta.dataset.action = next.action || "";
      cta.innerHTML = `继续：${next.cta || next.label}<svg viewBox="0 0 24 24"><path d="M5 12h13M12.5 6.5 18.5 12l-6 5.5"/></svg>`;
    } else {
      // 没有下一步了：按钮变成真的关闭，不留假按钮
      cta.dataset.action = "close-onboarding";
      cta.innerHTML = `全部完成，收起引导<svg viewBox="0 0 24 24"><path d="M5 12h13M12.5 6.5 18.5 12l-6 5.5"/></svg>`;
    }
  }
}

/* 点引导步骤：滚到能完成这一步的地方，把那个入口亮一下。
   首页没有对应位置的（空态没填资料、没账号方向），退化成直接执行这一步的动作。 */
/* 「填写资料」这一步：首页就是问卷时滚到那张问卷，别另外再弹一份一样的；
   首页没有问卷（已填过 / 不在首页）时退回抽屉里改。 */
function focusHomeSurvey() {
  const box = document.querySelector("#home-survey");
  if (box) {
    box.scrollIntoView({ behavior: "smooth", block: "start" });
    return;
  }
  openSurveyDrawer();
}

/* 「生成一次脚本」这一步：滚到首页那块「脚本创作」区块，不是导航栏的脚本创作页。
   用户可能已经切到别的页，先切回首页；切页是异步的，那块还没画出来就按帧重试几次。 */
function focusHomeScripts() {
  const spot = () => {
    if (!document.querySelector("#sec-script")) return false;
    spotOnboardingTarget("#sec-script");
    return true;
  };
  if (spot()) return;
  if (state.currentView !== "home") navigate("home");
  let tries = 0;
  const tick = () => {
    if (spot() || ++tries > 60) return;
    window.requestAnimationFrame(tick);
  };
  window.requestAnimationFrame(tick);
}

const ONBOARDING_ACTIONS = {
  survey: () => focusHomeSurvey(),
  diagnose: () => openAccountDialog({ mode: "first" }),
};

function spotOnboardingTarget(selector) {
  const node = document.querySelector(selector);
  // 入口被收起（比如没诊断前没有「推荐选题」按钮）时不算找到，交回给 action 走弹窗
  if (!node || !node.getClientRects().length) return false;
  node.scrollIntoView({ behavior: "smooth", block: "center" });
  // 连点同一步时动画要能重播：先摘掉类、强制一次重排，再加回去
  node.classList.remove("onb-spot");
  void node.offsetWidth;
  node.classList.add("onb-spot");
  window.setTimeout(() => node.classList.remove("onb-spot"), 4200);
  return true;
}

function openOnboardingStep(stepId) {
  const step = ONBOARDING_STEPS.find((item) => item.id === stepId);
  if (!step) return;
  if (step.target && spotOnboardingTarget(step.target)) return;
  ONBOARDING_ACTIONS[step.id]?.();
}



/* ==================================================================
   首页（工作台）
   —— 三种状态，模板都放在 index.html 里（不额外请求资源，避免旧服务进程的
      白名单缺失导致 404）：
      1 还没填调查问卷 → 整页就是那张问卷
      2 问卷填了、还没绑账号 → 空态，但骨架和已诊断态一致（标题、字段都在）
      3 有账号 → 已诊断态
   ================================================================== */

let homeSurveyMount = null;
/* 从别的页面走回首页时排一次进场；资料自动刷新不排，否则隔一会儿闪一次 */
let homeEnterQueued = false;

function destroyHomeSurvey() {
  if (!homeSurveyMount) return;
  try {
    homeSurveyMount.destroy?.();
  } catch (_error) {
    // 容器已经被整页替换掉时 destroy 找不到节点，忽略即可
  }
  homeSurveyMount = null;
}

function homeStateId() {
  const self = state.snapshot?.workflows?.self || {};
  const accounts = Array.isArray(self.own_accounts) ? self.own_accounts : [];
  // 刚在本页交过问卷：快照还没刷回来也先算「已填」，不能让人再填一遍
  const surveyFilled = Boolean(self.onboarding?.survey_filled) || state.homeSurveyDone;
  if (!surveyFilled) return "survey";
  return accounts.length ? "diagnosed" : "empty";
}

/* 问卷真的摆出来了 → 漏斗的「打开问卷」这一层。只打一次卡，失败无声。
   真正的去重在服务端（按机器码），这里只是别让同一次停留反复请求。 */
let surveyOpenedReported = false;
function reportSurveyOpened() {
  if (surveyOpenedReported) return;
  surveyOpenedReported = true;
  fetch("/api/onboarding-survey/open", { cache: "no-store" }).catch(() => {});
}

function mountHomeSurvey() {
  const box = document.querySelector("#home-survey");
  if (!box || !window.MeilingSurvey) return;
  reportSurveyOpened();
  homeSurveyMount = window.MeilingSurvey.mount({
    container: box,
    variant: "embed",
    storageKey: "meiling:first-survey:v2",
    endpoint: "/api/onboarding-survey",
    onSubmitted: async () => {
      state.homeSurveyDone = true;
      // 先转场再拉资料：等接口回来才换页，这几百毫秒里页面像卡住了。
      // ⚠️ 别在这里先 destroyHomeSurvey()：转场要把问卷整块复制一份飞出去，
      //    提前 destroy 会把克隆源清空，飞出去的只剩一个空壳。
      await playSurveyToHomeFx();
      const refreshed = await loadSnapshot({ quiet: true, force: true });
      if (!refreshed) refreshHomeParts();
    },
  });
}

/* 把首页画成某一态。返回是否画成功（模板缺失时画一张错误提示）。 */
function paintHome(target) {
  const body = document.getElementById(`home-v3-${target}`)?.innerHTML || "";
  if (!body) {
    elements.workspaceMain.innerHTML = emptyState("工作台暂时不可用", "首页模板未加载，请刷新页面后重试。");
    return false;
  }
  elements.workspaceMain.innerHTML = `<div class="home-v3">${body}</div>`;
  wireHomeV3(elements.workspaceMain);
  if (target === "survey") mountHomeSurvey();
  return true;
}

/* 同一态里的重画：只更新会变的那几块，整页 DOM 不动。
   资料自动刷新（snapshot.updated）走这条路——不然每隔一会儿整页闪一次，
   已经展开的焦点、正在播的进场动效都会被冲掉。 */
function refreshHomeParts() {
  const root = elements.workspaceMain;
  if (!root.querySelector("[data-home-state]")) return;
  renderOnboarding(root);
  renderHomeGates(root);
  renderHomeRec(root);
  renderHomeScripts(root);
  renderHomeDirection(root);
  renderHomeBreakdown(root);
  renderHomeAccount(root);
}

/* 工作台进场：整块从下往上摊开，各块依次错开。
   只加一个类，位移和延迟都写在 CSS 里（CSP 下不写行内样式）。 */
function playHomeEnter(root) {
  const wrap = root.querySelector(".home-v3");
  if (!wrap) return;
  wrap.classList.remove("home-enter");
  void wrap.offsetWidth;
  wrap.classList.add("home-enter");
  window.setTimeout(() => wrap.classList.remove("home-enter"), 1400);
}

/* 问卷交完 → 工作台：问卷整页缩进「账号方向」，再让工作台从下往上摊开。
   做法：把整页内容复制一份盖成固定浮层（真页面先藏起来），量出新页面里
   #dirPane 的位置，让浮层缩小飞过去；飞的同时把新页面换上，落地时首页播一次进场。 */
function playSurveyToHomeFx() {
  const shell = elements.workspaceMain;
  const inner = shell.querySelector(".home-v3 > main > .main-inner");
  const reduceMotion = window.matchMedia?.("(prefers-reduced-motion: reduce)")?.matches;

  // 提交按钮在页面最底下：先跳回顶部再起动画，
  // 不然浮层是从屏幕中间飞走的，看着像「只剩半页缩进去了」
  window.scrollTo({ top: 0, left: 0, behavior: "auto" });

  if (!inner || reduceMotion) {
    const target = homeStateId();
    destroyHomeSurvey();
    paintHome(target);
    return Promise.resolve();
  }

  // 整页（不是只有问卷块）复制一份飞出去：这样上面的引导条和标题不会先「跳掉」
  const from = inner.getBoundingClientRect();
  const clone = inner.cloneNode(true);
  // 复制品不能带 id：这几百毫秒里页面上会同时存在两份 #onb / #home-survey
  clone.querySelectorAll("[id]").forEach((node) => node.removeAttribute("id"));
  // 提交成功的绿条只在真页面上有意义，别跟着飞
  clone.querySelectorAll(".survey-status").forEach((node) => node.remove());

  const ghost = document.createElement("div");
  ghost.className = "home-v3 home-fx-ghost";
  ghost.appendChild(clone);
  // 位置走 CSSOM（行内 style 属性会被 CSP 丢掉）
  ghost.style.setProperty("--fx-x", `${Math.round(from.left)}px`);
  ghost.style.setProperty("--fx-y", `${Math.round(from.top)}px`);
  ghost.style.setProperty("--fx-w", `${Math.round(from.width)}px`);
  document.body.appendChild(ghost);

  shell.classList.add("home-fx-out");
  const target = homeStateId();
  destroyHomeSurvey();
  const painted = paintHome(target);

  // 飞去哪：账号方向那三节，量不到就退回账号卡
  const landNode = shell.querySelector("#dirPane, #accCard");
  const land = landNode?.getBoundingClientRect();
  const centerX = from.left + from.width / 2;
  const centerY = from.top + from.height / 2;
  const toX = land ? land.left + land.width / 2 : centerX;
  const toY = land ? land.top + land.height / 2 : centerY - 120;
  const scale = land ? Math.min(0.26, Math.max(0.12, land.width / from.width)) : 0.18;

  requestAnimationFrame(() => {
    ghost.style.transition = "transform .46s cubic-bezier(.4,0,.25,1), opacity .34s ease .12s";
    ghost.style.transform = `translate(${Math.round(toX - centerX)}px, ${Math.round(toY - centerY)}px) scale(${scale})`;
    ghost.style.opacity = "0";
  });

  // 浮层落到账号方向的位置时换页：先让工作台浮上来，再拆掉浮层
  window.setTimeout(() => {
    shell.classList.remove("home-fx-out");
    if (painted) playHomeEnter(shell);
    shell.querySelector("#dirPane")?.classList.add("dir-land");
    window.setTimeout(() => shell.querySelector("#dirPane")?.classList.remove("dir-land"), 1600);
  }, 250);

  return new Promise((resolve) => {
    window.setTimeout(() => {
      ghost.remove();
      resolve();
    }, 620);
  });
}

function renderHome() {
  const target = homeStateId();
  const shell = elements.workspaceMain;
  const current = shell.querySelector(".home-v3 > main");
  const currentState = current?.dataset.homeState;

  // 问卷正在填：资料一刷新就重画会把已填内容和焦点冲掉，同一状态直接不重画
  if (target === "survey" && currentState === "survey") return;
  if (target !== "survey") destroyHomeSurvey();

  // 已经是这一态：只刷新会变的内容，不重画整页
  if (currentState === target) {
    refreshHomeParts();
    return;
  }

  paintHome(target);
  if (homeEnterQueued) {
    homeEnterQueued = false;
    playHomeEnter(shell);
  }
}

/* 设计稿里的交互：关闭新手引导、打开账号切换浮层 */
/* 首页「关键问题 / 行动建议」：一次只显示一条，右上角 ‹ › 切换
   模板里每组用 data-dx-group 包住，条目用 data-dx-item，
   计数器 data-dx-count、按钮 data-dx-prev / data-dx-next；只有一条时按钮自动置灰。 */
function wireDxSwitchers(root) {
  root.querySelectorAll("[data-dx-group]").forEach((group) => {
    const items = Array.from(group.querySelectorAll("[data-dx-item]"));
    if (items.length < 2) return;
    const counter = group.querySelector("[data-dx-count]");
    const headNode = group.querySelector("[data-dx-head]");
    const dirNode = group.querySelector("[data-dx-dir-tag]");
    const prev = group.querySelector("[data-dx-prev]");
    const next = group.querySelector("[data-dx-next]");
    let index = 0;
    const pad = (value) => String(value).padStart(2, "0");
    const paint = () => {
      items.forEach((item, i) => { item.hidden = i !== index; });
      if (counter) counter.textContent = `${pad(index + 1)} / ${pad(items.length)}`;
      // 只换标题里冒号后面那一截，「关键问题：」「行动建议：」保持不动
      const title = items[index]?.dataset.dxTitle;
      if (headNode && title) headNode.textContent = title;
      // 标题右边的标签（行动建议的「方向」）：跟着条目换，没有就隐藏
      if (dirNode) {
        const dir = items[index]?.dataset.dxDir || "";
        dirNode.textContent = dir;
        dirNode.hidden = !dir;
      }
    };
    prev?.addEventListener("click", () => {
      index = (index - 1 + items.length) % items.length;
      paint();
    });
    next?.addEventListener("click", () => {
      index = (index + 1) % items.length;
      paint();
    });
    paint();
  });
}

/* 引导整条链走完没有：5 步全绿才算完。 */
function onboardingAllDone() {
  return onboardingFlags().every(Boolean);
}

/* 没做完第一次诊断：推荐选题不给入口（板块和框架留着，只收按钮）。
   点引导里的「推荐一次选题」同理——没有落点就直接打开指令弹窗。 */
function renderHomeGates(root) {
  const diagnosed = homeDiagnosed();
  root.querySelector("#sec-recommended-topics")?.classList.toggle("is-gated", !diagnosed);
}

/* ==================================================================
   空态骨架（2026-11-03）
   第一次进来时所有固定字段照常露出来，只把值留空：数字写「—」，
   整段文字换成一条浅灰条，状态写进虚线胶囊。
   区块里不再各写一句「先做完第一次诊断」——那句只在顶部引导条说一次。
   ================================================================== */
function phBar(size = "full", extra = "") {
  const cls = size === "full" ? "" : ` is-${size}`;
  return `<i class="ph-bar${cls}${extra}"></i>`;
}

function phField(label, bars) {
  return `<div class="ph-field"><span>${escapeHtml(label)}</span>${bars}</div>`;
}

function phChip(text) {
  return `<span class="ph-chip">${escapeHtml(text)}</span>`;
}

/* 关键问题空态：综合诊断 / 问题标题 / 具体表现 / 实际影响 */
function homePhProblemHtml() {
  return `<div class="dx-item is-on">
    <div class="ph-box is-plain">${phBar("long")}${phBar("short")}</div>
    ${phBar("mid", " is-title")}
    ${phField("具体表现", phBar("long"))}
    ${phField("实际影响", phBar("mid"))}
  </div>`;
}

/* 行动建议空态：建议标题 / 方向标签 / 对应问题 / 具体怎么做 / 验证指标 */
function homePhActionHtml() {
  return `<div class="dx-act-item is-ph">
    <div class="ph-title-row">${phBar("mid", " is-title")}${phChip("方向")}</div>
    ${phField("对应问题", phBar("long"))}
    ${phField("具体怎么做", `${phBar("long")}${phBar("mid")}${phBar("short")}`)}
    ${phField("验证指标", phBar("mid"))}
  </div>`;
}

/* 推荐选题空态：9 个位，序号 + 主线标签位 + 主题 + 切口 */
function homePhTopicList(count = 9) {
  return Array.from({ length: count }, (_, index) => `<div class="ph-card">
    <div class="ph-card-top"><span class="ph-num">${String(index + 1).padStart(2, "0")}</span>${phChip("主线")}</div>
    ${phBar("mid", " is-title")}
    ${phBar("long")}
  </div>`).join("");
}

/* 脚本创作空态：6 个位，编号 + 待创作 + 标题 + 内容定位 */
function homePhScriptList(count = 6) {
  return Array.from({ length: count }, () => `<div class="ph-card">
    <div class="ph-card-top"><span class="ph-num">编号 —</span>${phChip("待创作")}</div>
    ${phBar("mid", " is-title")}
    ${phBar("long")}
  </div>`).join("");
}

/* 诊断态里「这块确实还没有内容」的提示条：浅灰一块、居中一句话。
   不摆骨架——骨架的意思是「正在加载」，诊断都跑完了还摆 9 个空位，用户会以为在转圈。
   空态模板（还没绑定账号）不走这里：那里骨架旁边挂着「待生成」标记，能说清是在等它生成。 */
function homeEmptyNote(text) {
  return `<div class="sec-empty"><p>${escapeHtml(text)}</p></div>`;
}

function isDiagnosedHome(root) {
  return root.querySelector('[data-home-state="diagnosed"]') !== null;
}

/* 首页「推荐选题」：按账号方向直接生成的 9 条，一条一张卡。
   点卡片开详情抽屉，不跳页；不显示分数和平台来源，因为这一轮没有取数。 */
function renderHomeRec(root) {
  const list = root.querySelector("[data-rec-list]");
  if (!list) return;
  const data = recommendedTopicsPayload();
  const topics = Array.isArray(data?.topics) ? data.topics : [];
  if (!topics.length) {
    list.innerHTML = isDiagnosedHome(root)
      ? homeEmptyNote("这个账号还没有推荐选题，点上面「推荐一批选题」生成。")
      : homePhTopicList(9);
    return;
  }

  list.innerHTML = topics
    .slice(0, 9)
    .map((topic) => recommendTopicCard(topic))
    .join("");
}

/* 首页「脚本创作」：和「脚本创作」模块共用同一张卡（productionCard）、同一个抽屉
   （#content-drawer），点卡片看到的就是模块页里那份内容详情。
   生产中排在前面，已完成收在后面；首页只放最近几条，全量去模块页看。 */
const HOME_SCRIPT_LIMIT = 6;

function renderHomeScripts(root) {
  const list = root.querySelector("[data-scripts-list]");
  if (!list) return;
  // 口径和 renderContentProduction 一致：有内容生产记录的才算进过脚本创作。
  const all = [...(state.snapshot?.workflows?.production?.own_contents || [])]
    .filter((item) => item.production_record_path)
    .reverse();
  if (!all.length) {
    list.innerHTML = isDiagnosedHome(root)
      ? homeEmptyNote("这个账号还没有脚本创作记录，做过内容后这里会出现。")
      : homePhScriptList(6);
    return;
  }
  const creating = all.filter((item) => productionStateKey(item) !== "done");
  const done = all.filter((item) => productionStateKey(item) === "done");
  list.innerHTML = [...creating, ...done]
    .slice(0, HOME_SCRIPT_LIMIT)
    .map((item) => productionCard(item))
    .join("");
}

/* 首页「账号方向」：三块正文来自 我方资料/我方当前概要.md（快照里的 workflows.self.direction）。
   没有这份文件、或这三节都空时，整段不显示，卡片退回原来的两栏。 */
/* 悬停看全文：卡片正文只放得下 4 行，停上去在卡片旁边弹一个跟它对位的浮层。
   浮层挂在 body 下（卡片父级 overflow:hidden，挂里面会被裁掉），样式在 theme-aurora.css 的 .dir-tip。 */
let directionTip = null;
function directionTooltip() {
  if (directionTip) return directionTip;
  directionTip = document.createElement("div");
  directionTip.className = "dir-tip";
  directionTip.setAttribute("role", "tooltip");
  document.body.appendChild(directionTip);
  // 滚动时位置就对不上了，直接收起来
  window.addEventListener("scroll", hideDirectionTip, true);
  return directionTip;
}

function hideDirectionTip() {
  directionTip?.classList.remove("on");
}

function showDirectionTip(card) {
  const text = card.querySelector("p")?.textContent || "";
  if (!text) return;
  const tip = directionTooltip();
  tip.textContent = text;
  tip.classList.add("on");

  const box = card.getBoundingClientRect();
  const size = tip.getBoundingClientRect();
  const gap = 10;
  // 优先放卡片右边；右边放不下就翻到左边
  let left = box.right + gap;
  let flipped = false;
  if (left + size.width > window.innerWidth - 12) {
    left = box.left - gap - size.width;
    flipped = true;
  }
  if (left < 12) {
    left = 12;
    flipped = false;
  }
  tip.classList.toggle("left", flipped);
  let top = box.top + (box.height - size.height) / 2;
  if (top + size.height > window.innerHeight - 12) top = window.innerHeight - 12 - size.height;
  if (top < 12) top = 12;
  tip.style.left = `${Math.round(left)}px`;
  tip.style.top = `${Math.round(top)}px`;
}

/* 问卷答案 → 「账号方向」三节的草稿（2026-11-01）。
   方向正文还没生成时，先把用户自己填的答案摆上去——三节全写「待生成」帮不上忙。
   只做摆放，不做归纳：译文就是问卷里他选的那几句原话。
   映射：用户与需求 ← 关注的人 + 看完能得到；产品与变现 ← 真实积累 + 怎么赚钱；
        账号与内容 ← 内容领域 + 内容形式 + 先要达到的目标。 */
const SURVEY_DIRECTION_MAP = {
  user: [["q3", "关注的人"], ["q4", "看完能得到"]],
  biz: [["q2", "真实积累"], ["q6", "怎么赚钱"]],
  media: [["q1", "内容领域"], ["q7", "内容形式"], ["q5", "先要达到"]],
};

/* 一条答案拍平成一句：选项用「、」连，填的补充跟在后面；「还没想清楚」不算内容 */
function surveyAnswerText(entry) {
  if (!entry) return "";
  const values = (Array.isArray(entry.values) ? entry.values : [])
    .map((value) => String(value || "").trim())
    .filter((value) => value && value !== "还没想清楚");
  const extra = String(entry.extra || "").trim();
  if (extra) values.push(extra);
  return values.join("、");
}

function surveyDirectionDraft() {
  const answers = state.snapshot?.workflows?.self?.onboarding?.survey_answers || {};
  const draft = {};
  Object.entries(SURVEY_DIRECTION_MAP).forEach(([key, rows]) => {
    const parts = rows
      .map(([id, label]) => {
        const text = surveyAnswerText(answers[id]);
        return text ? `${label}：${text}` : "";
      })
      .filter(Boolean);
    if (parts.length) draft[key] = `${parts.join("；")}。`;
  });
  return Object.keys(draft).length ? draft : null;
}

/* ==================================================================
   首页「账号方向」（2026-11-02 按设计稿）
   数据全部来自 workflows.self.direction_plan —— 源头是 我方资料/我方自媒体规划.md：
     positioning  账号定位那一句
     legs[]       自身条件 / 目标客户 / 变现方式 三条事实
     lines[]      内容主线三栏（问题判断 / 真实过程 / 方法复用）
     open[]       还没定的事
   哪个分区没数据就整块不显示，不拿别的段落凑。
   ================================================================== */

const DIR_LEG_ICONS = {
  blue: '<path d="M12 3.4 20.6 7.9 12 12.4 3.4 7.9z"/><path d="m3.4 12.3 8.6 4.5 8.6-4.5"/><path d="m3.4 16.5 8.6 4.5 8.6-4.5"/>',
  plum: '<path d="M3.6 8.6h3.2l1.6-2.2h7.2l1.6 2.2h3.2v10.2H3.6z"/><circle cx="12" cy="13.4" r="3.3"/>',
  mint: '<path d="M4.4 6.6h2.4M4.4 12h2.4M4.4 17.4h2.4"/><path d="M10.2 6.6h9.4M10.2 12h9.4M10.2 17.4h9.4"/>',
};
/* 方向总览卡里四个格子：图标 + 配色。
   第 0 格「账号定位」写死在 index.html，JS 只渲染后面三条，配色顺着往后排 */
const DIR_CELL_STYLES = [
  { hue: "blue", icon: '<circle cx="12" cy="12" r="8.2"/><circle cx="12" cy="12" r="3.2"/><path d="M12 2.9v2.7M12 18.4v2.7M2.9 12h2.7M18.4 12h2.7"/>' },
  { hue: "mint", icon: '<circle cx="12" cy="8" r="3.6"/><path d="M5 20c.9-4 3.3-6 7-6s6.1 2 7 6"/>' },
  { hue: "plum", icon: '<circle cx="9" cy="8.6" r="3.2"/><path d="M3.4 19.4c.7-3.4 2.6-5 5.6-5s4.9 1.6 5.6 5"/><path d="M16.2 6.2a3 3 0 0 1 0 5.6"/><path d="M18 14.8c2 .6 3.1 2.1 3.6 4.6"/>' },
  { hue: "biz", icon: '<path d="M12 3.4v17.2"/><path d="M16.6 7.2H10a2.9 2.9 0 0 0 0 5.8h4a2.9 2.9 0 0 1 0 5.8H6.4"/>' },
];

function renderHomeDirection(root) {
  const card = root.querySelector("#dirCard");
  if (!card) return;
  const plan = state.snapshot?.workflows?.self?.direction_plan;

  const statement = card.querySelector("[data-dir-positioning]");
  if (statement) {
    const text = String(plan?.positioning || "").trim();
    // 空态留两条占位条，不写「还没有写账号定位」这种一句话空态
    statement.innerHTML = text
      ? escapeHtml(text)
      : `${phBar("long")}${phBar("mid")}`;
    statement.classList.toggle("is-wait", !text);
  }

  /* 方向总览有两种写法：
     ① 字段化（自身条件/目标客户/账号定位/内容主线/变现方式 + 待验证列表）——
        按字段排成「三条事实」，整段不再重复；
     ② 一整段散文 —— 就照原文当一段方向说明放。 */
  const overviewNode = card.querySelector("[data-dir-overview]");
  if (overviewNode) {
    const text = String(plan?.overview || "").trim();
    if (text) {
      overviewNode.textContent = text;
      overviewNode.classList.remove("is-wait");
      overviewNode.hidden = false;
    } else {
      overviewNode.hidden = true;
    }
  }

  const legsNode = card.querySelector("[data-dir-legs]");
  if (legsNode) {
    const legs = Array.isArray(plan?.legs) ? plan.legs.filter((item) => item.v) : [];
    legsNode.innerHTML = legs
      .map((item, index) => {
        const cell = DIR_CELL_STYLES[(index + 1) % DIR_CELL_STYLES.length];
        return `<div class="dir-cell" data-hue="${cell.hue}">
        <span class="dir-cell-ico" aria-hidden="true"><svg viewBox="0 0 24 24">${cell.icon}</svg></span>
        <h4>${escapeHtml(item.k)}</h4>
        <p>${escapeHtml(item.v)}</p>
      </div>`;
      })
      .join("");
    legsNode.hidden = !legs.length;
  }

  const linesNode = card.querySelector("[data-dir-lines]");
  const linesPart = card.querySelector("[data-dir-lines-part]");
  if (linesNode && linesPart) {
    const lines = Array.isArray(plan?.lines) ? plan.lines : [];
    linesNode.innerHTML = lines
      .map((item) => `<article class="dir-line" data-hue="${escapeAttribute(item.hue || "blue")}">
        <header class="dir-line-hd">
          <h4>${escapeHtml(item.title)}</h4>
        </header>
        <p>${escapeHtml(item.text)}</p>
      </article>`)
      .join("");
    if (!lines.length) {
      // 空态：两列表头照常显示，行里留空 —— 让人知道这里以后是三行主线
      linesNode.innerHTML = Array.from({ length: 3 }, () => `<div class="dir-line is-ph">
        ${phBar()}${phBar()}
      </div>`).join("");
    }
    linesPart.hidden = false;
  }

  const openNode = card.querySelector("[data-dir-open]");
  const openList = card.querySelector("[data-dir-open-list]");
  if (openNode && openList) {
    const items = Array.isArray(plan?.open) ? plan.open : [];
    // 一条待验证都没有时也把这三行摆出来：字段先露，值留空
    openList.innerHTML = items.length
      ? items.map((item) => `<li>${escapeHtml(item)}</li>`).join("")
      : Array.from({ length: 3 }, () => `<li class="is-ph">${phBar("mid")}</li>`).join("");
    openNode.hidden = false;
  }
}

/* ==================================================================
   首页「内容拆解」（2026-11-02）
   数据：沉淀结论/内容特征实例.csv 里 样本来源=我方内容、当前有效=是 的行，
   按「特征小类」分成 6 个环节，每个环节列实际用了哪些做法、各占几条。
   哪个环节没数据就写一句占位，不拿别的环节凑。
   ================================================================== */
const CD_DIMENSIONS = [
  { key: "内容类别", tone: "cat", icon: '<rect x="3.6" y="3.6" width="7" height="7" rx="2"/><rect x="13.4" y="3.6" width="7" height="7" rx="2"/><rect x="3.6" y="13.4" width="7" height="7" rx="2"/><rect x="13.4" y="13.4" width="7" height="7" rx="2"/>' },
  { key: "标题", tone: "title", icon: '<path d="M5 6.2h14M12 6.2v12.6"/>' },
  { key: "开头", tone: "open", icon: '<path d="M5 4.6v14.8M9.4 6.8h9.2M9.4 12h6.6M9.4 17.2h9.2"/>' },
  { key: "内容结构", tone: "struct", icon: '<path d="M4 6.4h16M4 12h16M4 17.6h10"/>' },
  { key: "表达风格", tone: "voice", icon: '<path d="M4 5.4h16v10.2H9.4L4 19.4z"/>' },
  { key: "互动设计", tone: "interact", icon: '<path d="M4 9h10.5M12 6l3 3-3 3"/><path d="M20 15H9.5M12 12l-3 3 3 3"/>' },
];

function renderHomeBreakdown(root) {
  const grid = root.querySelector("[data-cd-grid]");
  if (!grid) return;
  fillBreakdownTechnique(root);
  const rows = state.snapshot?.workflows?.patterns?.instances?.rows || [];
  const own = rows.filter((row) => row.source === "我方内容" && row.active === "是" && row.category);
  if (!own.length) {
    // 空态也把 6 个维度摆出来，只把做法和占比留空
    grid.classList.add("is-ph");
    grid.innerHTML = CD_DIMENSIONS.map(({ key, icon }) => `<article class="cd-card is-ph">
      <header class="cd-hd"><span class="cd-ico" aria-hidden="true"><svg viewBox="0 0 24 24">${icon}</svg></span><h3>${escapeHtml(key)}</h3></header>
      ${phBar("mid")}<i class="ph-track"></i>${phBar("short")}<i class="ph-track"></i>
    </article>`).join("");
    return;
  }
  grid.classList.remove("is-ph");

  const grouped = new Map();
  own.forEach((row) => {
    if (!grouped.has(row.category)) grouped.set(row.category, new Map());
    const bucket = grouped.get(row.category);
    bucket.set(row.label, (bucket.get(row.label) || 0) + 1);
  });

  grid.innerHTML = CD_DIMENSIONS.map(({ key, tone, icon }) => {
    const bucket = grouped.get(key);
    const items = bucket ? [...bucket.entries()].sort((a, b) => b[1] - a[1]) : [];
    const total = items.reduce((sum, [, count]) => sum + count, 0);
    const body = items.length
      ? items
          .map(([label, count], index) => {
            const pct = Math.round((count / total) * 100);
            // tone 区分环节，rank 让同一环节里的多条按占比从深到浅，不用看数字也能读出主次
            return `<div class="cd-row">
        <div class="cd-row-top"><span class="cd-label">${escapeHtml(label)}</span><span class="cd-num">${count} · ${pct}%</span></div>
        <div class="cd-bar"><i data-pct="${pct}" data-tone="${tone}" data-rank="${Math.min(index, 3)}"></i></div>
      </div>`;
          })
          .join("")
      : `<p class="cd-empty">这一环节还没拆到。</p>`;
    return `<article class="cd-card">
      <header class="cd-hd"><span class="cd-ico" aria-hidden="true"><svg viewBox="0 0 24 24">${icon}</svg></span><h3>${key}</h3></header>
      ${body}
    </article>`;
  }).join("");

  // 进度条宽度：CSP 挡行内 style，渲染完用 CSSOM 赋
  grid.querySelectorAll(".cd-bar i").forEach((bar) => {
    bar.style.width = `${Number(bar.dataset.pct) || 0}%`;
  });
}

/* 旧版索引不提供 content_technique 时才走这条路：直接读规律分析原文自己抽。 */
const CD_TECH_CACHE = new Map();

function cdTechniqueSentence(markdown) {
  const lines = String(markdown || "").split("\n");
  const start = lines.findIndex((line) => /^#{2,4}\s*我的打法小结/.test(line.trim()));
  if (start < 0) return "";
  for (let i = start + 1; i < lines.length; i += 1) {
    const line = lines[i].trim();
    if (!line) continue;
    if (/^#/.test(line)) break;          // 到下一节了
    if (/^\|/.test(line)) continue;      // 表格行当不成句子
    return line.replace(/\*\*/g, "").trim();
  }
  return "";
}

async function readCdTechnique(path) {
  if (!path) return "";
  if (CD_TECH_CACHE.has(path)) return CD_TECH_CACHE.get(path);
  let sentence = "";
  try {
    const response = await fetch(`/api/file?path=${encodeURIComponent(path)}`, { cache: "no-store" });
    if (response.ok) {
      const payload = await response.json();
      sentence = cdTechniqueSentence(payload.content || "");
    }
  } catch (_error) {
    sentence = "";
  }
  CD_TECH_CACHE.set(path, sentence);
  return sentence;
}

/* 「我的账号内容技巧」：账号级的一句话打法，取自「账号内容规律分析.md」的
   「### 我的打法小结」下面第一段。
   索引里已经算好（own_accounts[].content_technique），这里同步就填上 ——
   之前是前端自己异步读文件，快照没就绪时取不到账号、或者取数回来时节点
   已经被重画换掉了，就会出现「一会儿有一会儿没有」。 */
function fillBreakdownTechnique(root) {
  const node = root.querySelector("[data-cd-tech]");
  if (!node) return;
  const accounts = homeAccounts();
  const current = currentHomeAccount();
  const textOf = (account) => String(account?.content_technique || "").trim();
  // 下面六组环节是跨账号统计的，这句话也跟着取「做过规律分析的那个账号」：
  // 只认当前账号的话，切到一个没跑过分析的账号，这一段就凭空消失。
  const account = textOf(current) ? current : accounts.find((item) => textOf(item)) || current;
  const ready = textOf(account);
  if (ready) {
    node.textContent = ready;
    node.hidden = false;
    return;
  }
  // 只有索引没给这个字段（旧版索引）才退回读文件；读回来先确认节点还挂在文档上
  readCdTechnique(account?.documents?.pattern?.path || "").then((sentence) => {
    if (!node.isConnected) return;
    node.textContent = sentence;
    node.hidden = !sentence;
  });
}

/* ==================================================================
   首页「账号洞察」（2026-11-02 按设计稿）
   数据来自所选账号最新那那份详细诊断（workflows.self.own_accounts[].diagnosis）：
     综合诊断一句 + 主矛盾标签 / 关键问题若干条 / 行动建议若干条
   关键问题和行动建议都做成一次一条、右上角翻页。
   ================================================================== */

/* 结论里高亮诊断给的重点短语：先转义再按短语切，顺序不能反（否则高亮标签本身会被转义） */
function highlightKeyPhrases(text, phrases, exclude) {
  let html = escapeHtml(String(text || ""));
  const skip = String(exclude || "").trim();
  for (const phrase of Array.isArray(phrases) ? phrases : []) {
    const raw = String(phrase || "").trim();
    if (!raw || raw === skip) continue;
    const safe = escapeHtml(raw);
    if (safe) html = html.split(safe).join(`<b class="v2-kw">${safe}</b>`);
  }
  return html;
}

const DX_PAD = (value) => String(value).padStart(2, "0");

/* 关键问题一条：标题 + 问题类型标签 + 具体表现/实际影响 */
function insightProblemHtml(item, index) {
  /* dt/dd 直接挂在 dl 上：字段名一列、内容一列，两条的冒号位对齐（同一个 grid 算列宽） */
  return `<div class="dx-item${index === 0 ? " is-on" : ""}">
    <div class="dx-item-head">
      <h4>${escapeHtml(item.title || "未命名问题")}</h4>
    </div>
    <dl class="dx-fields">
      ${item.show ? `<dt>具体表现</dt><dd>${escapeHtml(item.show)}</dd>` : ""}
      ${item.impact ? `<dt>实际影响</dt><dd>${escapeHtml(item.impact)}</dd>` : ""}
    </dl>
  </div>`;
}

/* 行动建议一条：建议标题 + 方向标签，下面 对应问题 / 具体怎么做 / 验证指标 */
function insightActionHtml(item) {
  const how = Array.isArray(item.how) ? item.how.filter(Boolean) : [];
  const howHtml = how.length
    ? `<ol>${how.map((line) => `<li>${escapeHtml(line)}</li>`).join("")}</ol>`
    : escapeHtml(item.howText || "");
  return `<div class="dx-act-item">
    <div class="dx-act-title">
      <h4>${escapeHtml(item.title || "未命名动作")}</h4>
      ${item.direction ? `<span class="v2o-tag is-info">${escapeHtml(item.direction)}</span>` : ""}
    </div>
    <dl class="dx-fields">
      ${item.problems ? `<dt>对应问题</dt><dd>${escapeHtml(item.problems)}</dd>` : ""}
      ${howHtml ? `<dt>具体怎么做</dt><dd>${howHtml}</dd>` : ""}
      ${item.verify ? `<dt>验证指标</dt><dd>${escapeHtml(item.verify)}</dd>` : ""}
    </dl>
  </div>`;
}

function renderHomeInsight(root, account) {
  const data = account?.diagnosis || null;
  const problems = Array.isArray(data?.problems) ? data.problems.filter((item) => item.title) : [];
  /* 行动建议只认「账号改进行动清单」：那里面才有动作标题、对应问题、具体怎么做、验证指标。
     诊断里的「目标与实际」是按维度给的建议（标题是「目标用户」这种维度名），
     拿来当行动建议是冒充，宁可不显示。 */
  const list = account?.action_list?.items;
  const actions = Array.isArray(list) ? list : [];

  // 综合诊断：结论里把诊断给的重点短语高亮掉（跟「我的账号」同一套 v2-kw 黄底）
  const verdictNode = root.querySelector("[data-dx-verdict]");
  if (verdictNode) {
    const text = String(data?.verdict || "").trim();
    if (text) {
      verdictNode.innerHTML = highlightKeyPhrases(text, data?.keywords, data?.mainTag);
      verdictNode.classList.remove("is-wait");
    } else {
      verdictNode.textContent = "还没有诊断记录。做完第一次诊断，这里会给出账号的综合诊断：问题出在哪个环节、依据是什么。";
      verdictNode.classList.add("is-wait");
    }
  }
  const tagNode = root.querySelector("[data-dx-tag]");
  if (tagNode) {
    tagNode.textContent = data?.mainTag || "";
    tagNode.hidden = !data?.mainTag;
  }

  // 关键问题：一次一条，右上角翻页
  const problemCard = root.querySelector("[data-dx-problems]");
  if (problemCard) {
    const itemsNode = problemCard.querySelector("[data-dx-items]");
    if (itemsNode) {
      itemsNode.innerHTML = problems.length
        ? problems.map(insightProblemHtml).join("")
        : homePhProblemHtml();
    }
    paintInsetPager(problemCard, "dx", problems.length, 0);
  }

  // 行动建议：一次一条
  const actionCard = root.querySelector("[data-dx-action]");
  if (actionCard) {
    const itemsNode = actionCard.querySelector("[data-dx-act-items]");
    const paintAction = (index) => {
      if (!itemsNode) return;
      const item = actions[index];
      itemsNode.innerHTML = item
        ? insightActionHtml(item)
        : homePhActionHtml();
    };
    paintAction(0);
    paintInsetPager(actionCard, "dx-act", actions.length, 0, paintAction);
  }
}

/* 同一张卡里的「‹ 01 / 03 ›」翻页：一条都没有就整块藏起来 */
function paintInsetPager(card, prefix, total, index, onChange) {
  const pager = card.querySelector(`[data-${prefix}-pager]`);
  if (!pager) return;
  /* 只有一条（01/01）时也留着角标，只是两侧箭头置灰 —— 直接隐藏会让人不知道这块能翻。
     一条都没有就不摆角标：01/01 会让人以为有一页内容可翻，其实下面只是空态说明。 */
  pager.hidden = total <= 0;
  if (total <= 0) return;
  pager.classList.toggle("is-single", total <= 1);
  if (pager.dataset.bound !== "1") {
    pager.dataset.bound = "1";
    pager.dataset.index = "0";
    const step = (delta) => {
      const count = Number(pager.dataset.total || total) || 1;
      if (count <= 1) return;
      const next = (Number(pager.dataset.index || 0) + delta + count) % count;
      pager.dataset.index = String(next);
      card.dispatchEvent(new CustomEvent("dx-pager", { detail: { prefix, index: next } }));
    };
    pager.querySelector(`[data-${prefix}-prev]`)?.addEventListener("click", () => step(-1));
    pager.querySelector(`[data-${prefix}-next]`)?.addEventListener("click", () => step(1));
  }
  pager.dataset.total = String(total);
  pager.dataset.index = String(index);
  const pos = pager.querySelector(`[data-${prefix}-pos]`);
  const totalNode = pager.querySelector(`[data-${prefix}-total]`);
  if (pos) pos.textContent = DX_PAD(index + 1);
  if (totalNode) totalNode.textContent = DX_PAD(total);
  card.dataset.prefix = prefix;
  if (!card.dataset.pagerBound) {
    card.dataset.pagerBound = "1";
    card.addEventListener("dx-pager", (event) => {
      const { prefix: which, index: next } = event.detail;
      const node = card.querySelector(`[data-${which}-pos]`);
      if (node) node.textContent = DX_PAD(next + 1);
      if (which === "dx") {
        // 每条自带标题，翻页只切换哪条显示
        card.querySelectorAll(".dx-item").forEach((item, i) => item.classList.toggle("is-on", i === next));
      } else if (card.__repaintAction) {
        card.__repaintAction(next);
      }
    });
  }
  if (onChange) card.__repaintAction = onChange;
}

/* 首页「账号现状」：昵称 / 粉丝 / 获赞 / 标签 / 上次诊断，全部取快照里的
   workflows.self.own_accounts（源头是 我方资料/我方账号库.csv + 账号目录里的诊断文档）。
   一个账号都没有时不画这张卡，renderHome 会换成「还没绑定」的空态模板。 */
const HOME_ACCOUNT_STORAGE_KEY = "meiling.homeAccount";

function homeAccounts() {
  return state.snapshot?.workflows?.self?.own_accounts || [];
}

function homeAccountKey(account) {
  return `${account.platform || ""}::${account.id || ""}`;
}

/* 首页当前选中的那个账号（右上角胶囊切换的就是它）。
   账号洞察的「查看详细诊断」按它跳对应平台的诊断页，不写死小红书或抖音。 */
function currentHomeAccount() {
  const items = homeAccounts();
  if (!items.length) return null;
  let saved = "";
  try {
    saved = localStorage.getItem(HOME_ACCOUNT_STORAGE_KEY) || "";
  } catch (error) {
    saved = "";
  }
  return items.find((account) => homeAccountKey(account) === saved) || items[0];
}

function homeAccountTheme(platform) {
  const key = platformKey(platform);
  if (key === "douyin") return "douyin";
  if (key === "bilibili") return "bili";
  return "xhs";
}

/* 粉丝/获赞按千分位显示；没填就写「—」，不拿 0 顶 */
function homeAccountCount(value) {
  const number = Number(value);
  if (value === null || value === undefined || value === "" || !Number.isFinite(number)) return "—";
  return new Intl.NumberFormat("zh-CN").format(number);
}

/* 「上次诊断」= 账号详细诊断.md 的修改时间；没有这份文档就写「—」 */
function homeAccountDiagStamp(account) {
  const at = account.documents?.detail?.modified_at;
  const date = new Date(at || "");
  if (Number.isNaN(date.getTime())) return "—";
  const pad = (number) => String(number).padStart(2, "0");
  return `${pad(date.getMonth() + 1)}-${pad(date.getDate())} ${pad(date.getHours())}:${pad(date.getMinutes())}`;
}

/* 卡片只放得下 4 个标签（flex-wrap 会换行，再多就把卡片撑高），多的不展示 */
function homeAccountTags(tags) {
  const list = (Array.isArray(tags) ? tags : []).filter(Boolean).slice(0, 4);
  return list
    .map((tag, index) => index === 0
      ? `<span class="tag"><svg viewBox="0 0 24 24"><circle cx="10" cy="14" r="4.6"/><path d="M13.8 10.2 19 5M15.2 5H19v3.8"/></svg>${escapeHtml(tag)}</span>`
      : `<span class="tag">${escapeHtml(tag)}</span>`)
    .join("");
}

function homeAccountMenuItem(account, active) {
  const file = PLATFORM_ICON_FILES[platformKey(account.platform)];
  const logo = file
    ? `<img class="am-logo" src="${platformIconBase()}${file}" alt="" />`
    : `<span class="am-logo am-plus">${escapeHtml(String(account.platform || "?").slice(0, 1))}</span>`;
  const fans = homeAccountCount(account.followers);
  const meta = `${account.platform || "未填写"} · ${fans === "—" ? "粉丝未填" : `${fans} 粉`}`;
  return `<button class="am-item${active ? " on" : ""}" type="button" data-account-key="${escapeAttribute(homeAccountKey(account))}">
    ${logo}
    <span class="am-copy"><b>${escapeHtml(account.name || "未命名账号")}</b><span>${escapeHtml(meta)}</span></span>
    <svg class="am-chk" viewBox="0 0 24 24"><path d="m5 12.5 4.5 4.5L19 7"/></svg>
  </button>`;
}

function paintHomeAccount(root, account, items) {
  const card = root.querySelector("#accCard");
  if (card) card.dataset.theme = homeAccountTheme(account.platform);

  // 奖牌位放账号头像：这一格代表的是「哪个账号」，不是「哪个平台」。
  // 头像缺失（账号库没登记头像路径）时才退回平台 logo。
  const key = platformKey(account.platform);
  const file = PLATFORM_ICON_FILES[key];
  const avatar = String(account.avatar_path || "").trim();
  const logo = root.querySelector("#medalLogo");
  if (logo) {
    const src = avatar ? rawFileUrl(avatar) : (file ? `${platformIconBase()}${file}` : "");
    logo.hidden = !src;
    logo.src = src;
    logo.dataset.kind = avatar ? "avatar" : "platform";
    if (avatar) logo.alt = `${account.name || "账号"}头像`;
    else logo.alt = "";
  }

  const name = root.querySelector("#medalName");
  if (name) name.textContent = account.name || "未命名账号";
  const fans = root.querySelector("#medalFans");
  if (fans) fans.textContent = homeAccountCount(account.followers);
  const likes = root.querySelector("#medalLikes");
  if (likes) likes.textContent = homeAccountCount(account.likes);
  const likesLabel = root.querySelector("#likesLabel");
  if (likesLabel) likesLabel.textContent = key === "xiaohongshu" ? "获赞与收藏" : "获赞";
  const tags = root.querySelector("#tagList");
  if (tags) tags.innerHTML = homeAccountTags(account.tags);
  const diag = root.querySelector("#diagTime");
  if (diag) diag.textContent = homeAccountDiagStamp(account);

  // 最近数据按当前选中的账号重画（切账号时一起换）
  void renderHomeRecent(root, account);
  // 账号洞察也跟着切：每个账号有自己那份诊断
  renderHomeInsight(root, account);
  // 右上角的账号胶囊
  paintAccountPicker(root, account, items);

  const list = root.querySelector("#accMenuList");
  if (list) {
    const activeKey = homeAccountKey(account);
    list.innerHTML = items
      .map((item) => homeAccountMenuItem(item, homeAccountKey(item) === activeKey))
      .join("");
  }
}

/* 页面右上角「当前运营账号」胶囊：头像 + 昵称 + 平台·粉丝，点开是账号列表。
   和账号卡里那个浮层是同一份数据（我方账号库.csv），两个入口指向同一件事。 */
function paintAccountPicker(root, account, items) {
  const face = root.querySelector("#apFace");
  const key = platformKey(account.platform);
  const file = PLATFORM_ICON_FILES[key];
  if (face) {
    face.hidden = !file;
    face.src = file ? `${platformIconBase()}${file}` : "";
  }

  const name = root.querySelector("[data-acct-name]");
  if (name) name.textContent = account.name || "未命名账号";

  const meta = root.querySelector("[data-acct-meta]");
  if (meta) {
    const fans = homeAccountCount(account.followers);
    const platform = account.platform || "未填写平台";
    meta.textContent = fans === "—" ? platform : `${platform} · ${fans} 粉`;
    meta.removeAttribute("title");
  }

  const list = root.querySelector("#apList");
  if (!list) return;
  const activeKey = homeAccountKey(account);
  list.innerHTML = items
    .map((item) => {
      const itemFile = PLATFORM_ICON_FILES[platformKey(item.platform)];
      const logo = itemFile
        ? `<img class="ap-logo" src="${platformIconBase()}${itemFile}" alt="" />`
        : `<span class="ap-logo ap-plus">${escapeHtml(String(item.platform || "?").slice(0, 1))}</span>`;
      const fans = homeAccountCount(item.followers);
      const sub = fans === "—" ? (item.platform || "平台未填") : `${item.platform} · ${fans} 粉`;
      return `<button class="ap-item${homeAccountKey(item) === activeKey ? " is-on" : ""}" type="button" data-account-key="${escapeAttribute(homeAccountKey(item))}">
        ${logo}
        <span class="ap-who"><b>${escapeHtml(item.name || "未命名账号")}</b><span class="ap-meta">${escapeHtml(sub)}</span></span>
        <svg class="ap-chk" viewBox="0 0 24 24" aria-hidden="true"><path d="m5 12.5 4.5 4.5L19 7"/></svg>
      </button>`;
    })
    .join("");
}

/* ==================================================================
   首页「最近数据」：只认创作者中心「账号概览」，没采到就显示未采集。
   2026-11-01 之前这块是设计稿里写死的示例数字（141 / 6.2% / 10-17 至 10-23），
   看着像真数据，其实和账号档案页、诊断用的数据都不一致 —— 一律删掉改真数据。
   ================================================================== */
const HOME_RECENT_METRICS = {
  xiaohongshu: ["曝光数", "封面点击率", "平均观看时长", "视频完播率"],
  douyin: ["5秒完播率", "2秒跳出率", "封面点击率", "平均播放时长"],
  bilibili: [],
};
const HOME_RECENT_RADAR = {
  xiaohongshu: ["观看数", "涨粉数", "主页访客数", "发布数", "互动数"],
  douyin: ["播放量", "完播率", "粉丝净增", "作品数", "互动率"],
  bilibili: [],
};
const HOME_RECENT_LABELS = ["l-top", "l-right", "l-br", "l-bl", "l-left"];
const homeRecentCache = new Map();

function homeRecentMetric(label, raw) {
  const text = String(raw ?? "").trim();
  if (!text) return { label, value: "—", unit: "", delta: "未采集", tone: "out" };
  const deltaHit = /环比\s*[+-]\s*[\d.]+%/.exec(text);
  const main = text.replace(/（[^）]*）/g, "").trim();
  const match = /^([+\-]?[\d.,]+)\s*(.*)$/.exec(main);
  return {
    label,
    value: match ? match[1] : main || "—",
    unit: match ? match[2] : "",
    delta: deltaHit ? deltaHit[0] : "",
    tone: deltaHit ? (deltaHit[0].includes("-") ? "down" : "up") : "out",
  };
}

function homeRecentNumber(raw) {
  const hit = /^([+\-]?[\d.]+)/.exec(String(raw ?? "").replace(/,/g, "").trim());
  return hit ? Number(hit[1]) : null;
}

function homeRecentRadarDim(name, raw) {
  const text = String(raw ?? "").trim();
  if (!text) return { key: name, value: "—", pct: null, number: null };
  const value = text.replace(/（[^）]*）/g, "").trim() || "—";
  const over = /超过\s*(\d+)\s*%\s*同类/.exec(text);
  const under = /低于\s*(\d+)\s*%\s*同类/.exec(text);
  let pct = null;
  // 排名文案照抄平台原话：平台写「低于 99%」就别翻成「超过 1%」——
  // 后者读起来像好消息，实际是垫底。pct 只用来画雷达半径。
  let rank = "";
  if (over) {
    pct = Number(over[1]);
    rank = `超过 ${over[1]}% 同类`;
  } else if (under) {
    pct = Math.max(0, 100 - Number(under[1]));
    rank = `低于 ${under[1]}% 同类`;
  }
  const number = homeRecentNumber(value);
  if (pct === null && number === 0) pct = 0;
  return { key: name, value, pct, rank, number };
}

async function readHomeRecentOverview(path) {
  if (!path) return null;
  if (homeRecentCache.has(path)) return homeRecentCache.get(path);
  try {
    const response = await fetch(`/api/file?path=${encodeURIComponent(path)}`, { cache: "no-store" });
    if (!response.ok) throw new Error(String(response.status));
    const file = await response.json();
    const parsed = JSON.parse(String(file.content || ""));
    homeRecentCache.set(path, parsed);
    return parsed;
  } catch (_error) {
    return null;
  }
}

function homeRecentRadarSvg(dims) {
  const CX = 220;
  const CY = 175;
  const R = 120;
  const point = (index, radius) => {
    const angle = (-90 + index * 72) * Math.PI / 180;
    return [CX + radius * Math.cos(angle), CY + radius * Math.sin(angle)];
  };
  const ring = (ratio) => dims
    .map((_, index) => point(index, R * ratio).map((n) => n.toFixed(1)).join(","))
    .join(" ");
  const spokes = dims
    .map((_, index) => {
      const [x, y] = point(index, R);
      return `<line class="rline" x1="${CX}" y1="${CY}" x2="${x.toFixed(1)}" y2="${y.toFixed(1)}"/>`;
    })
    .join("");
  const plotted = dims.map((item, index) => point(index, R * Math.sqrt(Math.min(Math.max(item.plot, 0), 100) / 100)));
  const polygon = plotted.map((pair) => pair.map((n) => n.toFixed(1)).join(",")).join(" ");
  const dots = plotted
    .map((pair) => `<circle class="rdot" cx="${pair[0].toFixed(1)}" cy="${pair[1].toFixed(1)}" r="3.8"/>`)
    .join("");
  return `<svg class="radar-svg" viewBox="0 0 440 350" aria-hidden="true">
    <polygon class="rg-outer" points="${ring(1)}"/>
    <polygon class="rg" points="${ring(0.75)}"/>
    <polygon class="rg" points="${ring(0.5)}"/>
    <polygon class="rg" points="${ring(0.25)}"/>
    ${spokes}
    <polygon class="rdata" points="${polygon}"/>
    ${dots}
    <circle class="rdot0" cx="${CX}" cy="${CY}" r="2.6"/>
  </svg>`;
}

function homeRecentRadarLabels(dims) {
  return dims
    .map((item, index) => {
      const missing = item.value === "—" ? " miss" : "";
      const sub = item.pct === null ? (missing ? "未采集" : "") : (item.rank || `超过 ${item.pct}% 同类`);
      return `<div class="rlab ${HOME_RECENT_LABELS[index]}${missing}"><b>${escapeHtml(item.key)} <i>${escapeHtml(item.value)}</i></b><span>${escapeHtml(sub)}</span></div>`;
    })
    .join("");
}

async function renderHomeRecent(root, account) {
  const periodNode = root.querySelector("[data-recent-period]");
  const metricsNode = root.querySelector("[data-recent-metrics]");
  const radarNode = root.querySelector("[data-recent-radar]");
  if (!metricsNode || !radarNode) return;

  const key = platformKey(account?.platform);
  const metricNames = HOME_RECENT_METRICS[key] || [];
  const radarNames = HOME_RECENT_RADAR[key] || [];
  const overview = await readHomeRecentOverview(account?.creator_center?.overview?.path);
  const metrics = overview?.指标 && typeof overview.指标 === "object" ? overview.指标 : {};
  const status = String(overview?.状态 || "").trim();

  if (periodNode) {
    const period = String(metrics["统计周期"] || "").trim();
    periodNode.textContent = period ? `统计周期 ${period}` : "统计周期未采集";
  }

  metricsNode.innerHTML = metricNames
    .map((name) => {
      const item = homeRecentMetric(name, metrics[name]);
      // 涨红跌绿：环比 >=0 走 .up（红），<0 走 .down（绿），箭头跟着方向换。
      const arrow = item.tone === "down"
        ? '<svg viewBox="0 0 12 12"><path d="M6 2v8M2.6 6.6 6 10l3.4-3.4"/></svg>'
        : item.tone === "up"
          ? '<svg viewBox="0 0 12 12"><path d="M6 10V2M2.6 5.4 6 2l3.4 3.4"/></svg>'
          : "";
      const delta = item.delta
        ? `<span class="rm-d ${item.tone}">${arrow}${escapeHtml(item.delta)}</span>`
        : "";
      const extra = item.unit ? `<em>${escapeHtml(item.unit)}</em>` : "";
      return `<div class="rm${item.value === "—" ? " miss" : ""}">
        <span class="rm-k">${escapeHtml(item.label)}</span>
        <div class="rm-v">${escapeHtml(item.value)}${extra}</div>
        ${delta}
      </div>`;
    })
    .join("");

  const dimsBase = radarNames.map((name) => homeRecentRadarDim(name, metrics[name]));
  const ready = dimsBase.length === 5 && dimsBase.every((item) => item.pct !== null);
  const maxNumber = Math.max(...dimsBase.map((item) => Math.max(item.number || 0, 0)), 0);
  const dims = dimsBase.map((item) => ({
    ...item,
    plot: ready
      ? Math.min(Math.max(item.pct, 0), 100)
      : (maxNumber > 0 ? Math.round((Math.max(item.number || 0, 0) / maxNumber) * 100) : 0),
  }));
  const drawable = dims.length === 5 && (ready || maxNumber > 0);

  if (!drawable) {
    // 「一条都没采到」和「采到了但画不出来」是两件事：前者要引导用户去跑诊断，
    // 后者是平台确实没给数（例如观看数不足 100 不给同类排名）。
    const nothingCollected = !overview
      || status !== "成功"
      || dimsBase.every((item) => item.value === "—");
    const reason = nothingCollected
      ? "创作者中心的近 7 天数据还没采到，跑一次账号诊断后会自动补上。"
      : "五项指标当前没有可绘制的数值，暂时画不出雷达图。";
    radarNode.innerHTML = `<p class="radar-empty">${escapeHtml(reason)}</p>`;
    return;
  }
  radarNode.innerHTML = `${homeRecentRadarSvg(dims)}${homeRecentRadarLabels(dims)}`;
}

function renderHomeAccount(root) {
  const items = homeAccounts();
  if (!items.length) return;
  let saved = "";
  try {
    saved = localStorage.getItem(HOME_ACCOUNT_STORAGE_KEY) || "";
  } catch (error) {
    saved = "";
  }
  const selected = items.find((account) => homeAccountKey(account) === saved) || items[0];
  paintHomeAccount(root, selected, items);
}

/* 右上角账号胶囊的事件：点条目切账号、点「添加账号」开弹窗、点外面收起。
   切账号后重画由 selectHomeAccount → paintHomeAccount 负责。 */
let accountPickerOutsideBound = false;

function wireAccountPicker(root) {
  const picker = root.querySelector("#acctPicker");
  if (!picker || picker.dataset.bound === "1") return;
  picker.dataset.bound = "1";
  picker.addEventListener("click", (event) => {
    const item = event.target.closest(".ap-item");
    if (!item) return;
    event.stopPropagation();
    picker.removeAttribute("open");
    if (item.hasAttribute("data-add-account")) {
      openAccountDialog();
      return;
    }
    if (item.dataset.accountKey) selectHomeAccount(item.dataset.accountKey);
  });
  if (accountPickerOutsideBound) return;
  accountPickerOutsideBound = true;
  document.addEventListener("click", (event) => {
    const node = document.querySelector("#acctPicker[open]");
    if (node && !node.contains(event.target)) node.removeAttribute("open");
  });
}

function selectHomeAccount(key) {
  try {
    localStorage.setItem(HOME_ACCOUNT_STORAGE_KEY, key);
  } catch (error) {
    /* 隐私模式下 localStorage 会被拒，这次切换照样生效，只是记不住 */
  }
  const items = homeAccounts();
  const account = items.find((item) => homeAccountKey(item) === key);
  const root = elements.workspaceMain;
  if (account && root) paintHomeAccount(root, account, items);
}

function wireHomeV3(root) {
  // 引导步骤：点一下滚到对应位置并高亮入口（键盘 Enter / 空格同样可用）
  root.querySelectorAll("[data-onb-step]").forEach((node) => {
    const step = ONBOARDING_STEPS.find((item) => item.id === node.dataset.onbStep);
    // 「安装准备」没有可去的地方：不做成按钮，也不进 Tab 顺序
    if (step && !step.target && !ONBOARDING_ACTIONS[step.id]) return;
    node.setAttribute("role", "button");
    node.setAttribute("tabindex", "0");
    node.addEventListener("click", () => openOnboardingStep(node.dataset.onbStep));
    node.addEventListener("keydown", (event) => {
      if (event.key !== "Enter" && event.key !== " ") return;
      event.preventDefault();
      openOnboardingStep(node.dataset.onbStep);
    });
  });

  wireDxSwitchers(root);
  renderOnboarding(root);
  renderHomeGates(root);
  renderHomeRec(root);
  renderHomeScripts(root);
  renderHomeDirection(root);
  // 首屏就要画「我的账号内容技巧」：只在刷新时画的话，首次打开这张卡是空的
  renderHomeBreakdown(root);
  renderHomeAccount(root);
  wireAccountPicker(root);

  const menu = root.querySelector("#accMenu");
  const trigger = root.querySelector("#switchBtn");
  if (!menu || !trigger) return;

  trigger.addEventListener("click", (event) => {
    event.stopPropagation();
    const rect = trigger.getBoundingClientRect();
    const base = menu.parentElement.getBoundingClientRect();
    menu.style.top = `${rect.bottom - base.top + 8}px`;
    menu.classList.toggle("open");
  });

  /* 账号条目由 renderHomeAccount 按账号库生成，这里用委托，
     条目重绘后不用重新绑定；「+ 添加账号」走新增账号弹窗。 */
  menu.addEventListener("click", (event) => {
    const item = event.target.closest(".am-item");
    if (!item) return;
    event.stopPropagation();
    menu.classList.remove("open");
    if (item.hasAttribute("data-add-account")) {
      openAccountDialog();
      return;
    }
    if (item.dataset.accountKey) selectHomeAccount(item.dataset.accountKey);
  });

  document.addEventListener("click", () => menu.classList.remove("open"));
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape") menu.classList.remove("open");
  });
}

// 新版列表：详情页指向 account-v2。
function renderBenchmarkAccountsV2() {
  const all = state.snapshot.workflows.insight.benchmark_accounts;
  const items = applyBenchmarkOrder(filterBenchmarkAccounts(all));
  const side = `<div class="head-tools">
    <span class="head-count"><b>${items.length}</b><i>个账号</i></span>
    <button class="head-reload" type="button" data-action="refresh-snapshot" aria-label="刷新资料" title="刷新资料">
      <svg aria-hidden="true" viewBox="0 0 24 24"><path d="M20 11a8 8 0 1 0 2 5.2"/><path d="M20 4v7h-7"/></svg>
    </button>
  </div>`;
  elements.workspaceMain.innerHTML = listShell({
    ...listHead(side),
    body: cardGrid(items.map((item) => accountCard(item, { detailBase: "account-v2", listView: "benchmark-accounts-v2" })), "还没有符合当前筛选条件的对标账号", "acct-grid"),
  });
}


function renderBenchmarkContents() {
  const all = state.snapshot.workflows.insight.benchmark_contents;
  const items = filterBenchmarkContents(all);
  // 只列已进入正式对标分析的内容；账号采集、尚未单独分析的内容留在账号详情页的「代表作品」里。
  const analyzed = items.filter((item) => item.source !== "account" || item.analysis_state === "已完成");
  // 头部副标题照设计稿：账号数 + 平台 + 同步说明
  const accountCount = new Set(analyzed.map((item) => item.account_name).filter(Boolean)).size;
  const platforms = [...new Set(analyzed.map((item) => item.platform).filter(Boolean))]
    .sort((left, right) => {
      const order = ["抖音", "小红书", "B站", "视频号"];
      const leftIndex = order.indexOf(left);
      const rightIndex = order.indexOf(right);
      return (leftIndex < 0 ? 99 : leftIndex) - (rightIndex < 0 ? 99 : rightIndex);
    });
  const sub = `${accountCount} 个对标账号 · ${platforms.length ? platforms.join(" / ") : "暂无平台"} · 数据每日同步`;
  const side = `<div class="head-tools">
    <span class="head-count"><b>${analyzed.length}</b><i>条</i></span>
    <button class="head-reload" type="button" data-action="refresh-snapshot" aria-label="刷新资料" title="刷新资料">
      <svg aria-hidden="true" viewBox="0 0 24 24"><path d="M20 11a8 8 0 1 0 2 5.2"/><path d="M20 4v7h-7"/></svg>
    </button>
  </div>`;
  elements.workspaceMain.innerHTML = listShell({
    ...listHead(side),
    title: "对标内容",
    sub,
    body: cardGrid(analyzed.map((item, index) => benchmarkContentCard(item, index + 1)), "还没有对标内容", "bm-content-grid"),
  });
}



async function renderDocumentView(document, emptyTitle) {
  if (!document?.path) {
    elements.workspaceMain.innerHTML = emptyState(emptyTitle, "当前工作区还没有对应正式文档");
    return;
  }
  const viewAtStart = state.currentView;
  elements.workspaceMain.innerHTML = '<div class="loading-state"><span></span><p>正在读取正式文档</p></div>';
  try {
    const file = await fetchFile(document.path);
    if (state.currentView !== viewAtStart) return;
    elements.workspaceMain.innerHTML = `<div class="document-shell"><article class="document-preview"><header class="document-source-path"><code title="${escapeAttribute(file.path || document.path)}">${escapeHtml(file.path || document.path)}</code></header><div class="document-preview-body markdown-body">${renderMarkdown(file.content, { documentPath: file.path || document.path })}</div></article></div>`;
  } catch (error) {
    elements.workspaceMain.innerHTML = emptyState("文档读取失败", error.message);
  }
}

/* ---------------- 账号方向页 · 调查问卷 ----------------

   问卷是基础档案，答案是 我方资料/调查问卷.md；账号方向是基于它和实际运营
   形成的当前策略。两者有关联，但不互相自动覆盖：页面顶部并排两块，
   左边方向总览（planning.js 渲染），右边调查问卷摘要；
   填写和修改都在右侧抽屉里完成，保存只更新档案，不动账号方向。 */

/* 右栏把 7 道题和答案全摊开：标签用短名，值就是用户勾的答案 */
const BASELINE_FIELDS = [
  ["q1", "内容领域"],
  ["q2", "持续积累"],
  ["q3", "想服务的人"],
  ["q4", "用户价值"],
  ["q5", "当前目标"],
  ["q6", "变现方式"],
  ["q7", "内容形式"],
];

const BASELINE_ICON = '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M8 4h8a2 2 0 0 1 2 2v14H6V6a2 2 0 0 1 2-2z"/><path d="M9 4V2.8h6V4"/><path d="M9 9h6M9 13h6M9 17h3"/></svg>';

const BASELINE_ARROW = '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M5 12h13M12.5 6l6 6-6 6"/></svg>';

/* 选「其他」时补的说明并进答案；「还没想清楚」和未回答统一显示「待定」 */
function baselineAnswer(parsed, id) {
  const values = parsed.answers[id] || [];
  if (!values.length || (values.length === 1 && values[0] === "还没想清楚")) {
    return { text: "待定", tbd: true };
  }
  const extra = String(parsed.extras[id] || "").trim();
  return { text: values.join("、") + (extra ? `（${extra}）` : ""), tbd: false };
}

/* 卡头直接复用规划卡片的卡头（.plan-card-h），保证和自我认知那一排长得一样 */
function baselineHeadHtml(action = "") {
  return `<div class="plan-card-h">
    <span class="plan-ico">${BASELINE_ICON}</span>
    <b class="plan-card-t">调查问卷</b>
    ${action}
  </div>`;
}

function baselineCardHtml(parsed) {
  const items = BASELINE_FIELDS.map(([id, label], index) => {
    const answer = baselineAnswer(parsed, id);
    // 第一题（做哪个领域）铺满整行，剩下 6 项两两成对，正好三排
    const wide = index === 0;
    return `<div class="baseline-item${wide ? " is-wide" : ""}"><span>${label}</span><b${answer.tbd ? ' class="is-tbd"' : ""}>${escapeHtml(answer.text)}</b></div>`;
  }).join("");
  return `<section class="baseline-card plan-card plan-t1">
    ${baselineHeadHtml(`<button class="baseline-edit" type="button" data-action="open-survey-drawer">修改问卷${BASELINE_ARROW}</button>`)}
    <div class="baseline-grid">${items}</div>
  </section>`;
}

/* 没填过时用同样的 7 格骨架，填完后位置和高度都不跳 */
function baselineEmptyCardHtml() {
  const items = BASELINE_FIELDS.map(([, label], index) => {
    const wide = index === 0;
    return `<div class="baseline-item${wide ? " is-wide" : ""}"><span>${label}</span><b class="is-empty">待填写</b></div>`;
  }).join("");
  return `<section class="baseline-card plan-card plan-t1 is-empty">
    ${baselineHeadHtml(`<button class="baseline-edit" type="button" data-action="open-survey-drawer">填写调查问卷${BASELINE_ARROW}</button>`)}
    <div class="baseline-grid">${items}</div>
  </section>`;
}

/* 档案不存在、读不到或一题都没答，都按「还没填写」处理 */
async function loadBaselineSurvey() {
  try {
    const file = await fetchFile(window.MeilingSurvey?.answersPath || "我方资料/调查问卷.md");
    const parsed = window.MeilingSurvey ? window.MeilingSurvey.parse(file.content) : null;
    const filled = Boolean(parsed) && Object.values(parsed.answers).some((values) => values.length);
    return filled ? parsed : null;
  } catch (_error) {
    return null;
  }
}

async function baselineSurveyCard() {
  const parsed = await loadBaselineSurvey();
  return parsed ? baselineCardHtml(parsed) : baselineEmptyCardHtml();
}

const DIAGNOSABLE_PLATFORM_KEYS = new Set(["xiaohongshu", "douyin"]);

function diagnosableHomeAccounts() {
  return homeAccounts().filter((account) => DIAGNOSABLE_PLATFORM_KEYS.has(platformKey(account.platform)));
}

function directionPlanDocument() {
  return state.snapshot?.workflows?.self?.media_plan || null;
}

function directionPlanAt() {
  return dateTimestamp(directionPlanDocument()?.modified_at);
}

function surveyAt() {
  if (typeof state.surveyUpdatedAt === "number" && Number.isFinite(state.surveyUpdatedAt) && state.surveyUpdatedAt > 0) {
    return state.surveyUpdatedAt;
  }
  const onboarding = state.snapshot?.workflows?.self?.onboarding || {};
  return dateTimestamp(onboarding.survey_modified_at);
}

function directionAccountNames(accounts) {
  const names = accounts.map((account) => `${account.platform}「${account.name || "未命名账号"}」`);
  if (names.length > 3) return `${names.slice(0, 3).join("、")} 等账号`;
  if (names.length === 2) return `${names[0]}和${names[1]}`;
  return names.join("、");
}

/* 账号诊断旧于当前规划：只算「已经诊断过、但诊断没跟上新方向」的账号。
   从来没诊断过的账号不在这里提醒，交给「我的账号」的首次诊断入口。 */
function staleDiagnosisAccounts() {
  const planAt = directionPlanAt();
  if (!planAt) return [];
  return diagnosableHomeAccounts().filter((account) => {
    const diagnosisAt = dateTimestamp(account.documents?.detail?.modified_at);
    return Boolean(diagnosisAt) && diagnosisAt < planAt;
  });
}

/* 提示条只有三种真实状态：方向旧于问卷 / 方向已更新但部分诊断没重跑 / 都没有。
   没有规划时不在这里提醒，页面本身有空态。 */
function baselineNoticeInfo() {
  const self = state.snapshot?.workflows?.self || {};
  if (!self.onboarding?.survey_filled) return null;
  const plan = directionPlanDocument();
  if (!plan?.path) return null;

  const planAt = directionPlanAt();
  const surveyTime = surveyAt();
  const directionStale = planAt && surveyTime ? planAt < surveyTime : Boolean(state.surveyNotice);
  if (directionStale) {
    return {
      kind: "direction",
      text: "调查问卷已更新，当前账号方向没有随之改变。",
      accounts: diagnosableHomeAccounts(),
    };
  }

  const stale = staleDiagnosisAccounts();
  if (stale.length) {
    return {
      kind: "diagnosis",
      text: `账号方向已更新，${directionAccountNames(stale)}的诊断还没重跑。`,
      stale,
    };
  }
  return null;
}

function baselineNoticeKey(info) {
  const planAt = directionPlanAt() || 0;
  const surveyTime = surveyAt() || 0;
  const accountKeys = (info.kind === "diagnosis" ? info.stale : info.accounts)
    .map((account) => homeAccountKey(account));
  return [info.kind, planAt, surveyTime, accountKeys.join(",")].join("|");
}

function dismissBaselineNotice(button) {
  const info = baselineNoticeInfo();
  if (info) state.baselineNoticeDismissedKey = baselineNoticeKey(info);
  button?.closest(".baseline-notice")?.remove();
}

function baselineNoticeHtml() {
  const info = baselineNoticeInfo();
  if (!info) return "";
  const key = baselineNoticeKey(info);
  if (state.baselineNoticeDismissedKey === key) return "";

  const isDirection = info.kind === "direction";
  const action = isDirection ? "direction-diagnosis" : "rediagnose-stale-accounts";
  const label = isDirection
    ? (info.accounts.length ? "更新方向并诊断" : "更新方向")
    : "补跑未完成的账号";
  return `<div class="baseline-notice" data-notice-key="${escapeAttribute(key)}">
    <span>${escapeHtml(info.text)}</span>
    <button class="baseline-notice-act" type="button" data-action="${action}">${label}</button>
    <button class="baseline-notice-close" type="button" data-action="dismiss-baseline-notice">${isDirection ? "暂不调整" : "暂不处理"}</button>
  </div>`;
}

/* 方向总览和调查问卷并排：卡片挪进 .plan-top；旧结构文档没有总览，就单独放页首 */
function mountBaselineCard(html) {
  const template = document.createElement("template");
  template.innerHTML = html.trim();
  const card = template.content.firstElementChild;
  if (!card) return;
  const planDoc = elements.workspaceMain.querySelector(".plan-doc");
  const summary = planDoc?.querySelector(".plan-summary");
  if (!summary) {
    elements.workspaceMain.insertBefore(card, elements.workspaceMain.firstChild);
    return;
  }
  const top = document.createElement("div");
  top.className = "plan-top";
  summary.replaceWith(top);
  top.append(summary, card);
}

/* ---------- 调查问卷抽屉 ---------- */

let surveyDrawer = null;
let surveyMount = null;

function ensureSurveyDrawer() {
  if (surveyDrawer) return surveyDrawer;
  const dialog = document.createElement("dialog");
  dialog.className = "topic-drawer survey-drawer";
  dialog.id = "survey-drawer";
  dialog.innerHTML = `<div class="tpl-dr-shell">
    <div class="tpl-dr-head">
      <div>
        <h2>调查问卷</h2>
        <p>改完保存只更新这份基础档案，账号方向不会自动跟着变。</p>
      </div>
      <button class="dr-close" type="button" data-survey-close aria-label="关闭调查问卷" title="关闭"><svg viewBox="0 0 16 16" aria-hidden="true"><path d="M3 3l10 10M13 3L3 13"/></svg></button>
    </div>
    <div class="tpl-dr-body" id="survey-drawer-body"></div>
  </div>`;
  dialog.addEventListener("click", (event) => {
    if (event.target.closest("[data-survey-close]") || event.target === dialog) closeSurveyDrawer();
  });
  dialog.addEventListener("close", () => {
    dialog.classList.remove("is-open");
    surveyMount?.destroy?.();
    surveyMount = null;
  });
  document.body.append(dialog);
  surveyDrawer = dialog;
  return dialog;
}

async function openSurveyDrawer() {
  const dialog = ensureSurveyDrawer();
  const body = dialog.querySelector("#survey-drawer-body");
  body.innerHTML = '<div class="loading-state"><span></span><p>正在读取调查问卷</p></div>';
  if (!dialog.open) dialog.showModal();
  requestAnimationFrame(() => dialog.classList.add("is-open"));

  const parsed = await loadBaselineSurvey();
  if (!dialog.open) return;
  surveyMount?.destroy?.();
  body.innerHTML = "";
  surveyMount = window.MeilingSurvey?.mount({
    container: body,
    variant: "embed",
    storageKey: "meiling:first-survey:v2",
    endpoint: "/api/onboarding-survey",
    initial: parsed,
    editing: Boolean(parsed),
    onSubmitted: () => {
      // 改过档案才提示「方向没变」；首次填写只是把档案补上
      if (parsed) {
        state.surveyNotice = true;
        state.surveyUpdatedAt = Date.now();
        state.baselineNoticeDismissedKey = "";
      }
      closeSurveyDrawer();
      renderCurrentView();
      // 填写期间自动刷新是暂停的（见 scheduleSnapshotRefresh），这里补一次；
      // 等抽屉滑出动画结束再拉，免得动画中途整页换 DOM
      window.setTimeout(() => loadSnapshot({ quiet: true, force: true }), 360);
    },
  });
}

function closeSurveyDrawer() {
  const dialog = surveyDrawer;
  if (!dialog?.open) return;
  dialog.classList.remove("is-open");
  // 等滑出动画结束再真正关闭；中途又打开就让它留着
  window.setTimeout(() => {
    if (!dialog.classList.contains("is-open")) dialog.close();
  }, 320);
}

async function renderPlanningView() {
  const viewAtStart = state.currentView;
  const planDocument = state.snapshot.workflows.self.media_plan;
  const baseline = await baselineSurveyCard();
  if (state.currentView !== viewAtStart) return;
  const notice = baselineNoticeHtml();
  if (!planDocument?.path) {
    elements.workspaceMain.innerHTML = `${notice}${baseline}${emptyState("还没有账号方向", "填完调查问卷后，回到对话窗口说一声，会按问卷整理出一版账号方向")}`;
    return;
  }
  elements.workspaceMain.innerHTML = '<div class="loading-state"><span></span><p>正在读取账号方向</p></div>';
  try {
    const file = await fetchFile(planDocument.path);
    if (state.currentView !== viewAtStart) return;
    const content = String(file.content || "");
    const documentPath = file.path || planDocument.path;
    // 新结构的规划正文（带「## 方向总览」）交给 planning.js 拆成卡片；
    // 旧文档或结构不完整时退回纯 markdown —— 不硬套模板，免得整篇乱掉。
    const asCards = Boolean(window.MeilingPlanning && window.MeilingPlanning.supports(content));
    if (!asCards) {
      elements.workspaceMain.innerHTML = `${notice}${baseline}<div class="document-shell"><article class="document-preview"><header class="document-source-path"><code title="${escapeAttribute(documentPath)}">${escapeHtml(documentPath)}</code></header><div class="document-preview-body markdown-body">${renderMarkdown(content, { documentPath })}</div></article></div>`;
      return;
    }
    elements.workspaceMain.innerHTML = `${notice}${window.MeilingPlanning.render(content)}`;
    window.MeilingPlanning.mount?.(elements.workspaceMain);
    mountBaselineCard(baseline);
  } catch (error) {
    elements.workspaceMain.innerHTML = `${notice}${baseline}${emptyState("规划读取失败", error.message)}`;
  }
}

/* 我的账号：与对标账号同一套列表版式，点卡片进账号详情 */
const OWN_CARD_METRICS = [
  ["获赞", "likes"],
  ["收录", "content_count"],
  ["已发布", "published_count"],
];

function renderOwnAccounts() {
  const accounts = state.snapshot.workflows.self.own_accounts;
  const side = `<div class="head-tools">
    <span class="head-count"><b>${accounts.length}</b><i>个账号</i></span>
    <button class="head-reload" type="button" data-action="refresh-snapshot" aria-label="刷新资料" title="刷新资料">
      <svg aria-hidden="true" viewBox="0 0 24 24"><path d="M20 11a8 8 0 1 0 2 5.2"/><path d="M20 4v7h-7"/></svg>
    </button>
  </div>`;
  elements.workspaceMain.innerHTML = listShell({
    ...listHead(side),
    body: cardGrid(
      accounts.map((item) => accountCard(item, {
        detailBase: "account-v2",
        listView: "own-accounts",
        kind: "own",
        subMetrics: OWN_CARD_METRICS,
        // 我的账号没有 recorded_at，就用诊断文档的修改时间，保证卡片底部一样有更新时间
        updatedAt: item.documents?.detail?.modified_at
          || item.documents?.overview?.modified_at
          || item.documents?.pattern?.modified_at
          || state.snapshot?.generated_at,
      })),
      "还没有我方账号",
      "acct-grid"
    ),
  });
}


/* 页面标题由工作台顶部的 page-head 统一负责，这里只出一条分段栏。
   分段用「对标账号·新」的黑胶囊语言，不要 listShell 那套旧模板。 */
/* ==================== 搜索选题 ====================
   数据来自快照里的 workflows.production.topic_search：
   只投影最近一轮 我方资料/搜索选题/<run_id>/选题推荐.json 的正式结果。
   页面只做两件真实动作：复制 topic-evaluate 指令、复制本轮结果文件路径。 */

function topicSearchPayload() {
  return state.snapshot?.workflows?.production?.topic_search || null;
}

/* 平台指标可能是数字，也可能已经是「86.3万」这种文本；两种都原样显示。 */
function compactMetric(value) {
  const text = String(value ?? "").trim();
  if (!text) return "—";
  if (/[^\d.,]/u.test(text)) return text;
  const number = Number(text.replaceAll(",", ""));
  if (!Number.isFinite(number)) return text;
  if (number >= 10000) {
    const wan = Math.round((number / 10000) * 10) / 10;
    return `${String(wan).replace(/\.0$/, "")}万`;
  }
  return String(number);
}

function topicSearchTime(value) {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return String(value || "—");
  const pad = (number) => String(number).padStart(2, "0");
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())} ${pad(date.getHours())}:${pad(date.getMinutes())}`;
}

function topicSearchPlatformClass(platform) {
  if (platform === "小红书") return "is-xhs";
  if (platform === "抖音") return "is-dy";
  return "is-other";
}

/* 列表里平台按固定顺序列，避免同一套数据在不同题里前后颠倒 */
const TOPIC_SEARCH_PLATFORM_ORDER = ["抖音", "小红书", "B站", "视频号"];

function topicSearchOrderedPlatforms(platforms) {
  return [...new Set(platforms.filter(Boolean))].sort((left, right) => {
    const leftIndex = TOPIC_SEARCH_PLATFORM_ORDER.indexOf(left);
    const rightIndex = TOPIC_SEARCH_PLATFORM_ORDER.indexOf(right);
    if (leftIndex >= 0 && rightIndex >= 0) return leftIndex - rightIndex;
    if (leftIndex >= 0) return -1;
    if (rightIndex >= 0) return 1;
    return left.localeCompare(right, "zh-CN");
  });
}

function topicSearchSourceMeta(source) {
  const parts = [];
  if (source.author) parts.push(source.author);
  if (source.likes) parts.push(`赞 ${compactMetric(source.likes)}`);
  if (source.collects) parts.push(`藏 ${compactMetric(source.collects)}`);
  if (source.comments) parts.push(`评 ${compactMetric(source.comments)}`);
  if (source.fetched !== undefined) parts.push(`抓到 ${Number(source.fetched) || 0} 条评论`);
  return parts.join(" · ");
}

/* 小红书原笔记链接没有 xsec_token 时会跳到 404；
   没有 token 的旧数据改为打开站内搜索，至少能按标题找回那条笔记。 */
function topicSearchSourceUrl(source) {
  const url = String(source.url || "").trim();
  if (source.platform !== "小红书" || /[?&]xsec_token=/.test(url)) return url;
  const keyword = String(source.title || "")
    .replace(/#[^\s#]+/g, " ")
    .replace(/\s+/g, " ")
    .trim()
    .slice(0, 80);
  if (!keyword) return url;
  return `https://www.xiaohongshu.com/search_result?keyword=${encodeURIComponent(keyword)}`;
}

function topicSearchSourceRow(source) {
  const label = escapeAttribute(source.title || "");
  const url = topicSearchSourceUrl(source);
  const title = url
    ? `<button class="ts-source-title" type="button" data-action="open-external" data-url="${escapeAttribute(url)}" title="${label}">${escapeHtml(source.title || "未命名来源")}</button>`
    : `<span class="ts-source-title" title="${label}">${escapeHtml(source.title || "未命名来源")}</span>`;
  return `<li class="ts-source">
    ${title}
    <p class="ts-source-meta"><span class="ts-plat ${topicSearchPlatformClass(source.platform)}">${escapeHtml(source.platform || "来源")}</span>${escapeHtml(topicSearchSourceMeta(source) || "本轮没有记录指标")}</p>
  </li>`;
}

function topicSearchCommentRow(comment) {
  const meta = `${comment.author || "匿名用户"} · 赞 ${Number(comment.likes) || 0} · 回复 ${Number(comment.replies) || 0}`;
  return `<li class="ts-quote"><p class="ts-quote-text">${escapeHtml(comment.text || "（空评论）")}</p><p class="ts-quote-meta">${escapeHtml(meta)}</p></li>`;
}

/* ---------- 推荐选题卡片 ----------
   一行 3 个，沿用选题库（选题分析）的 .tpl-grid / .tpl-card 骨架：
   顶行 序号 + 分数，中间标题，底下平台/来源/评论一行，最后是代表内容合起来的赞和藏。 */
function topicSearchCardMeta(topic) {
  const sources = Array.isArray(topic.sources) ? topic.sources : [];
  const comments = Array.isArray(topic.comments) ? topic.comments : [];
  const platforms = topicSearchOrderedPlatforms(sources.map((source) => source.platform));
  return [
    platforms.length ? platforms.join(" / ") : "未记录平台",
    `${sources.length} 条代表内容`,
    `${comments.length} 条评论`,
  ].join(" · ");
}

/* 「推荐」爆炸贴：首页前三条才挂。12 尖星（24 个顶点，外 49 / 内 36）当底，
   贴纸压在卡片右下角，旋转一点才有中奖贴纸那股劲儿。 */
const RECOMMEND_STAR_POINTS = "50.0,1.0 59.3,15.2 74.5,7.6 75.5,24.5 92.4,25.5 84.8,40.7 99.0,50.0 84.8,59.3 92.4,74.5 75.5,75.5 74.5,92.4 59.3,84.8 50.0,99.0 40.7,84.8 25.5,92.4 24.5,75.5 7.6,74.5 15.2,59.3 1.0,50.0 15.2,40.7 7.6,25.5 24.5,24.5 25.5,7.6 40.7,15.2";

function topicSearchRecommendBadge() {
  return `<span class="ts-recommend" aria-hidden="true">
      <svg viewBox="0 0 100 100"><polygon points="${RECOMMEND_STAR_POINTS}"/></svg>
      <b>推荐</b>
    </span>`;
}

/* 推荐选题卡片：模块页和首页共用同一张，两处的序号 / 分数 / 标题 / 平台条数 / 累计行完全一致。
   两处点卡片都是开同一个详情抽屉。featured 只在首页传，给前三条挂「推荐」贴纸。 */
function topicSearchCard(topic, runId, featured = false) {
  return `<button type="button" class="tpl-card ts-tcard" data-action="open-search-topic" data-run="${escapeAttribute(runId || "")}" data-rank="${escapeAttribute(topic.rank)}">
    <span class="tpl-top">
      <span class="tpl-num">${String(topic.rank).padStart(2, "0")}</span>
      <span class="ts-pick" data-action="topic-search-produce" data-run="${escapeAttribute(runId || "")}" data-rank="${escapeAttribute(topic.rank)}" title="拿这条选题的爆款内容当参考，创作自己的脚本"><svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 5v14M5 12h14"/></svg>参考爆款内容创作</span>
      <span class="ts-tcard-score">${Number(topic.total_score) || 0}<em>分</em></span>
    </span>
    <span class="tpl-title">${escapeHtml(topic.title || "未命名选题")}</span>
    <span class="ts-tcard-meta">${escapeHtml(topicSearchCardMeta(topic))}</span>
    <span class="ts-tcard-stats">
      <span class="ts-tcard-cum">累计</span>
      <span class="ts-tcard-stat"><em>点赞</em>${escapeHtml(compactMetric(topic.likes_total))}</span>
      <span class="ts-tcard-stat"><em>收藏</em>${escapeHtml(compactMetric(topic.collects_total))}</span>
      <span class="ts-tcard-stat"><em>评论</em>${escapeHtml(compactMetric(topic.comments_total))}</span>
    </span>${featured ? topicSearchRecommendBadge() : ""}
  </button>`;
}
function renderTopicSearch() {
  const data = topicSearchPayload();
  if (!data || !Array.isArray(data.topics) || !data.topics.length) {
    elements.snapshotTime.textContent = "还没有结果";
    elements.workspaceMain.innerHTML = `<section class="ts-page"><div class="ts-empty">
      <strong>还没有搜索选题结果</strong>
      <p>点右上角「+ 搜索选题」生成指令，点「确认」跑完一轮，这里会展示 9 条推荐选题和对应的来源内容、评论证据。</p>
    </div></section>`;
    return;
  }

  const topics = data.topics;
  const generatedAt = topicSearchTime(data.generated_at);
  elements.snapshotTime.textContent = `更新于 ${generatedAt}`;

  /* 历史推荐＝更早轮次的结果。同一轮重跑最容易和本轮撞题，
     本轮已有的标题不再在历史里重复出现。 */
  const currentTitles = new Set(topics.map((topic) => String(topic.title || "").trim()));
  const historyRuns = (Array.isArray(data.history) ? data.history : [])
    .map((run) => ({
      run_id: run.run_id,
      generated_at: run.generated_at,
      topics: (Array.isArray(run.topics) ? run.topics : [])
        .filter((topic) => !currentTitles.has(String(topic.title || "").trim())),
    }))
    .filter((run) => run.topics.length);

  const currentCards = topics.map((topic) => topicSearchCard(topic, data.run_id)).join("");
  const historyBlocks = historyRuns.map((run) => `<p class="ts-run-label">${escapeHtml(topicSearchTime(run.generated_at))} 那轮 · ${run.topics.length} 条</p>
      <div class="ts-grid is-history">${run.topics.map((topic) => topicSearchCard(topic, run.run_id)).join("")}</div>`).join("");

  elements.workspaceMain.innerHTML = `<section class="ts-page">
    <section class="ts-group">
      <div class="ts-group-head"><h2>本次推荐</h2></div>
      <div class="ts-grid">${currentCards}</div>
    </section>

    <section class="ts-group">
      <div class="ts-group-head"><h2>历史推荐</h2></div>
      ${historyBlocks || '<p class="ts-empty-line">还没有历史推荐。再跑一轮搜索选题，这一轮的结果会自动收进这里。</p>'}
    </section>

    ${data.json_path ? `<p class="ts-run-path">本轮结果：${escapeHtml(data.json_path)}</p>` : ""}
  </section>`;
}

/* ---------- 选题详情抽屉 ----------
   和选题分析共用一套 .topic-drawer 外壳（右侧滑出、900 宽），只换里面的内容。 */
let searchTopicKey = "";

function topicSearchRunById(runId) {
  const data = topicSearchPayload();
  if (!data) return null;
  if (!runId || runId === data.run_id) return data;
  return (Array.isArray(data.history) ? data.history : []).find((run) => run.run_id === runId) || null;
}

/* 抽屉头部那条评分：左边综合评分，右边五个维度各 5 格 + 分数。
   维度和权重跟协调器一致（账号匹配 / 需求强度 / 参考内容 / 差异化 / 可生产）。 */
const TOPIC_SEARCH_DIMENSIONS = [
  ["账号匹配", "account_fit"],
  ["需求强度", "demand"],
  ["参考内容", "reference"],
  ["差异化", "differentiation"],
  ["可生产", "production"],
];

function topicSearchScoreBar(topic) {
  const scores = topic.scores || {};
  const dims = TOPIC_SEARCH_DIMENSIONS.map(([label, key]) => {
    const value = Math.max(0, Math.min(5, Number(scores[key]) || 0));
    const squares = Array.from({ length: 5 }, (_, index) => (index < value ? '<i class="on"></i>' : "<i></i>")).join("");
    return `<div class="ts-dim"><span class="ts-dim-k">${label}</span><span class="ts-dim-v">${squares}<em>${value}</em></span></div>`;
  }).join("");
  return `<div class="ts-scorebar-total"><b>${Number(topic.total_score) || 0}</b><span>综合评分</span></div>
    <div class="ts-scorebar-dims">${dims}</div>`;
}

function searchTopicDetailHtml(topic) {
  const sources = Array.isArray(topic.sources) ? topic.sources : [];
  const comments = Array.isArray(topic.comments) ? topic.comments : [];
  // 01 选题判断：和选题分析的「选题定义」同一套 key/value 行
  const defRows = [
    ["推荐角度", topic.angle],
    ["目标用户", topic.audience],
    ["用户问题", topic.pain],
    ["账号匹配", topic.fit],
  ].map(([name, value], index) => `<div class="tpl-def-row">
      <div class="tpl-def-key"><b><i>${index + 1}</i>${name}</b></div>
      <div class="tpl-def-val${value ? "" : " is-empty"}">${value ? escapeHtml(value) : "还没填"}</div>
    </div>`).join("");
  const sourceRows = sources.length
    ? sources.map(topicSearchSourceRow).join("")
    : '<li class="ts-empty-row">本轮没有记录代表内容</li>';
  const commentRows = comments.length
    ? comments.map(topicSearchCommentRow).join("")
    : '<li class="ts-empty-row">本轮没有记录代表性评论</li>';

  return `<section class="tpl-blk">
      <div class="tpl-blk-h"><span class="no">01</span><b>选题判断</b><em>4 项</em></div>
      <div class="tpl-defs">${defRows}</div>
    </section>
    <section class="tpl-blk">
      <div class="tpl-blk-h"><span class="no">02</span><b>代表内容</b><em>${sources.length} 条</em></div>
      <ul class="ts-sources">${sourceRows}</ul>
    </section>
    <section class="tpl-blk">
      <div class="tpl-blk-h"><span class="no">03</span><b>代表性评论</b><em>${comments.length} 条</em></div>
      <ul class="ts-quotes">${commentRows}</ul>
    </section>`;
}

function openSearchTopicDrawer(runId, rank) {
  const run = topicSearchRunById(runId);
  const topic = (run?.topics || []).find((item) => Number(item.rank) === Number(rank));
  const dialog = elements.searchTopicDrawer;
  if (!topic || !dialog) return;
  searchTopicKey = `${run.run_id}:${topic.rank}`;
  elements.searchTopicDrawerTitle.innerHTML =
    `<span class="tpl-num">${String(topic.rank).padStart(2, "0")}</span><span>${escapeHtml(topic.title || "未命名选题")}</span>`;
  elements.searchTopicDrawerSub.innerHTML = topicSearchScoreBar(topic);
  elements.searchTopicDrawerBody.innerHTML = searchTopicDetailHtml(topic);
  elements.searchTopicDrawerBody.scrollTop = 0;
  // 底栏只有一个动作：拿这条选题的爆款当参考去创作。撑满整条底栏，不并第二个入口。
  const rankAttr = escapeAttribute(topic.rank);
  const runAttr = escapeAttribute(run.run_id);
  elements.searchTopicDrawerFoot.innerHTML = `<button class="tpl-primary" type="button" data-action="topic-search-produce" data-rank="${rankAttr}" data-run="${runAttr}"><svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 5v14M5 12h14"/></svg>参考爆款内容创作</button>`;
  if (!dialog.open) dialog.showModal();
  requestAnimationFrame(() => dialog.classList.add("is-open"));
}

function closeSearchTopicDrawer() {
  const dialog = elements.searchTopicDrawer;
  if (!dialog || !dialog.open) return;
  searchTopicKey = "";
  dialog.classList.remove("is-open");
  window.setTimeout(() => {
    if (!dialog.classList.contains("is-open")) dialog.close();
  }, 300);
}

/* 参考链接取这条选题的代表内容。选题分析的「参考内容分支」只处理一条链接，
   给多条反而要它自己挑；弹窗里让用户自己选一条，默认第一条。
   链接统一成流程认得的规范地址，不把分享页 / 搜索结果页丢过去。 */
function topicSearchSourceUrl(source) {
  if (!source) return "";
  const raw = String(source.url || "").trim();
  if (!raw) return "";
  const nativeId = String(source.content_id || "").split(":").pop();

  // 抖音分享页带一长串跟踪参数，粘进指令会糊满整块；换成流程自己用的规范地址
  const douyin = raw.match(/^https?:\/\/(?:www\.)?iesdouyin\.com\/share\/(?:video|note|slide)\/(\d+)/);
  if (douyin) return `https://www.douyin.com/video/${douyin[1]}`;

  // 小红书没有 xsec_token 的旧链接打不开，退回按 note id 的规范地址（和取数口径一致）
  if (source.platform === "小红书" && nativeId && !/[?&]xsec_token=/.test(raw)) {
    return `https://www.xiaohongshu.com/explore/${nativeId}`;
  }
  return raw;
}

/* 能当参考链接的来源：有 url 的那些。顺序沿用搜索结果里的顺序。 */
function topicSearchReferenceSources(topic) {
  const sources = Array.isArray(topic?.sources) ? topic.sources : [];
  return sources.filter((source) => String(source.url || "").trim());
}

function topicSearchReferenceUrl(topic) {
  return topicSearchSourceUrl(topicSearchReferenceSources(topic)[0]);
}

/* 搜索选题支持的三个渠道；弹窗里默认全勾，指令按勾选结果写死本轮范围 */
const TOPIC_SEARCH_CHANNELS = ["抖音", "小红书", "B站"];

function topicSearchSearchPrompt(channels) {
  // 只有「没传渠道」才回退成全选；用户把三个都取消是一个明确的选择，不能替他补回来
  const source = Array.isArray(channels) ? channels : TOPIC_SEARCH_CHANNELS;
  const picked = source.filter((name) => TOPIC_SEARCH_CHANNELS.includes(name));
  const list = TOPIC_SEARCH_CHANNELS.filter((name) => picked.includes(name));
  const head = skillPromptText("topic-search");
  if (!list.length) {
    return `${head}\n本轮渠道：我还没勾渠道。先问我这一轮搜哪几个平台，不要自己默认挑一个。`;
  }
  if (list.length === TOPIC_SEARCH_CHANNELS.length) {
    return `${head}\n本轮渠道：${TOPIC_SEARCH_CHANNELS.join("、")}（三个都搜）。`;
  }
  return `${head}\n本轮渠道：${list.join("、")}（${list.length === 1 ? "只搜这个" : "只搜这几个"}，不要搜其他平台）。`;
}

const TOPIC_SEARCH_ACTION_PRESETS = {
  /* 页头「搜索选题」：不需要选中哪一条，进来就是一轮新搜索的指令。
     和「使用技能」同一个写法——流程本身在 skill 里，指令只负责把技能叫起来，
     外加一句本轮搜哪几个渠道（用户在弹窗里勾的那几个）。 */
  search: {
    title: "搜索选题",
    build: (topic, source, idea, channels) => topicSearchSearchPrompt(channels),
  },
  /* 参考爆款内容创作：把这条选题的代表爆款当参考，走「对标直达生产」，六项定位逐项继承对标分析。
     「想怎么改」不参与继承，只作为「用户明确要改的那一处」写进指令；留空就是纯继承。 */
  produce: {
    title: "参考爆款内容创作",
    needsSettings: true,
    build: (topic, source, idea) => {
      const ref = source ? topicSearchSourceUrl(source) : topicSearchReferenceUrl(topic);
      const lines = ["用 create-content 开始做内容，入口是「对标内容分析后生产」。", ""];
      if (ref) lines.push(`参考链接：${ref}`);
      lines.push("这条对标内容还没有正式分析，先按对标内容分析流程拆完，再进入内容生产。");
      const text = String(idea ?? "").trim();
      if (text) {
        lines.push("", "定位按这条对标的分析逐项继承，只有下面这一处按我的意思调整（不要就删掉）：", text);
      } else {
        lines.push("", "定位完全按这条对标的分析逐项继承，不要改动。");
      }
      /* 对标直达生产的定位没有新判断要用户拍板，别停在「等你确认」 */
      lines.push("定位生成后不用等我确认，直接出内容框架和完整初稿。");
      const mediaForm = topicSearchActionContext?.mediaForm || PRODUCTION_DEFAULTS.mediaForm;
      const goal = topicSearchActionContext?.goal || PRODUCTION_DEFAULTS.goal;
      lines.push(
        "",
        `媒介形式：${mediaForm}`,
        `内容目标：${goal}`,
        `这两项已经定好，建档时一起带上（--media-form ${mediaForm} / --content-task ${goal}），内容定位阶段不用再问我。`
      );
      return lines.join("\n");
    },
  },
};

/* 弹窗当前选中的是哪条代表内容：切换链接时用它重算指令 */
let topicSearchActionContext = null;

function topicSearchRefPickerHtml(topic, selectedIndex) {
  const sources = topicSearchReferenceSources(topic);
  if (!sources.length) return "";
  const items = sources.map((source, index) => `<button class="tsa-ref-item${index === selectedIndex ? " is-on" : ""}" type="button" data-tsa-ref="${index}">
      <span class="tsa-ref-plat">${escapeHtml(source.platform || "来源")}</span>
      <span class="tsa-ref-title">${escapeHtml(source.title || "未命名来源")}</span>
      <span class="tsa-ref-meta">${escapeHtml(topicSearchSourceMeta(source) || "")}</span>
    </button>`).join("");
  return `<div class="tsa-ref-head"><b>参考哪条爆款</b><em>这条选题有 ${sources.length} 条代表内容，挑一条</em></div>
    <div class="tsa-ref-list">${items}</div>`;
}

/* 指令文本永远按当前选中的那条链接 + 当前想法重算，改哪样都立刻跟着变 */
function paintTopicSearchAction(dialog) {
  const context = topicSearchActionContext;
  if (!context) return;
  const source = context.sources[context.index] || null;
  const text = context.preset.build(context.topic, source, context.idea, context.channels);
  dialog.dataset.prompt = text;
  dialog.querySelector("#tsa-text").textContent = text;
}

/* 想法是唯一可改的一行：默认填这条选题的「标题—角度，写给谁」，用户可改可清空。
   清空后生产指令就是纯继承对标定位，不再带任何调整。 */
function syncTopicSearchIdea(value) {
  const dialog = document.querySelector("#topic-search-action-dialog");
  const context = topicSearchActionContext;
  if (!dialog || !context) return;
  context.idea = String(value ?? "");
  paintTopicSearchAction(dialog);
}

/* 渠道多选：勾几个就只搜几个，指令里同步写死范围。
   一个都没勾时不替用户默认，指令里明确让 AI 回头问一句。 */
function topicSearchChannelChips(selected) {
  return TOPIC_SEARCH_CHANNELS.map((name) => {
    const on = selected.includes(name);
    return `<button class="tsa-channel${on ? " is-on" : ""}" type="button" role="checkbox" aria-checked="${on}" data-tsa-channel="${name}">${name}</button>`;
  }).join("");
}

function toggleTopicSearchChannel(name) {
  const dialog = document.querySelector("#topic-search-action-dialog");
  const context = topicSearchActionContext;
  if (!dialog || !context || !TOPIC_SEARCH_CHANNELS.includes(name)) return;
  const chosen = new Set(context.channels);
  if (chosen.has(name)) chosen.delete(name); else chosen.add(name);
  context.channels = TOPIC_SEARCH_CHANNELS.filter((item) => chosen.has(item));
  dialog.querySelectorAll("[data-tsa-channel]").forEach((node) => {
    const on = context.channels.includes(node.dataset.tsaChannel);
    node.classList.toggle("is-on", on);
    node.setAttribute("aria-checked", String(on));
  });
  paintTopicSearchAction(dialog);
}

function selectTopicSearchReference(index) {
  const dialog = document.querySelector("#topic-search-action-dialog");
  const context = topicSearchActionContext;
  if (!dialog || !context) return;
  if (!Number.isInteger(index) || index < 0 || index >= context.sources.length) return;
  context.index = index;
  dialog.querySelectorAll("[data-tsa-ref]").forEach((node) => {
    node.classList.toggle("is-on", Number(node.dataset.tsaRef) === index);
  });
  paintTopicSearchAction(dialog);
}

/* 三个动作共用同一个弹窗外壳：标题 + 一行上下文 + 现成指令。
   topic 为空时就是页头那轮「新一轮搜索选题」。
   有 topic 时，指令上方多一块「参考链接」，从这条选题的代表内容里挑一条。 */
function showTopicSearchActionDialog(preset, topic) {
  const dialog = document.querySelector("#topic-search-action-dialog");
  if (!preset || !dialog) return;
  dialog.querySelector("#tsa-title").textContent = preset.title;
  if (topic) {
    dialog.querySelector("#tsa-topic").innerHTML =
      `<b>${escapeHtml(String(topic.rank).padStart(2, "0") + " " + (topic.title || "未命名选题"))}</b><span>${escapeHtml(topic.angle || "")}</span>`;
  } else {
    const data = topicSearchPayload();
    const last = data
      ? `上一轮 ${topicSearchTime(data.generated_at)} · ${(data.topics || []).length} 条`
      : "还没有跑过搜索选题";
    dialog.querySelector("#tsa-topic").innerHTML =
      `<b>新一轮搜索选题</b><span>${escapeHtml(last)}</span>`;
  }

  const sources = topic ? topicSearchReferenceSources(topic) : [];
  topicSearchActionContext = {
    preset,
    topic,
    sources,
    index: 0,
    // 新一轮搜索默认三个渠道全勾；有具体选题时不用渠道，留空数组
    channels: topic ? [] : [...TOPIC_SEARCH_CHANNELS],
    // 「想怎么改」默认留空：空格才叫纯参考，需要改的人自己写一句
    idea: "",
    // 形式 / 目标：只有「参考爆款内容创作」用得上，每次打开都回默认（视频 + 流量）
    mediaForm: PRODUCTION_DEFAULTS.mediaForm,
    goal: PRODUCTION_DEFAULTS.goal,
  };
  /* 形式 / 目标：新一轮搜索只是搜平台，不做内容，不显示；参考爆款才需要 */
  const settingsField = dialog.querySelector("#tsa-prod-settings");
  if (settingsField) {
    settingsField.hidden = !preset.needsSettings;
    if (preset.needsSettings) {
      paintProductionSettings(dialog.querySelector("#tsa-media-forms"), "media-form", topicSearchActionContext.mediaForm);
      paintProductionSettings(dialog.querySelector("#tsa-goals"), "goal", topicSearchActionContext.goal);
    }
  }
  /* 渠道多选只在「新一轮搜索」出现：搜哪几个平台是这一轮的事，
     已经在做某条具体选题时不再问一遍 */
  const channelField = dialog.querySelector("#tsa-channels-field");
  const channelBox = dialog.querySelector("#tsa-channels");
  if (channelField && channelBox) {
    const showChannels = !topic;
    channelField.hidden = !showChannels;
    channelBox.innerHTML = showChannels ? topicSearchChannelChips(topicSearchActionContext.channels) : "";
  }
  const picker = dialog.querySelector("#tsa-ref");
  if (picker) {
    picker.innerHTML = sources.length ? topicSearchRefPickerHtml(topic, 0) : "";
    picker.hidden = !sources.length;
  }

  /* 补一句只对「有具体选题」的动作开放；页头那轮新搜索不需要它 */
  const ideaField = dialog.querySelector("#tsa-idea-field");
  const ideaInput = dialog.querySelector("#tsa-idea");
  if (ideaField && ideaInput) {
    ideaField.hidden = !topic;
    ideaInput.value = topicSearchActionContext.idea;
  }

  paintTopicSearchAction(dialog);

  if (!dialog.open) dialog.showModal();
  dialog.setAttribute("tabindex", "-1");
  dialog.focus();
}

function openTopicSearchActionDialog(kind, rank, runId = "") {
  const preset = TOPIC_SEARCH_ACTION_PRESETS[kind];
  const run = topicSearchRunById(runId);
  const topic = run?.topics?.find((item) => Number(item.rank) === Number(rank));
  if (!preset || !topic) return;
  showTopicSearchActionDialog(preset, topic);
}

function openTopicSearchStartDialog() {
  showTopicSearchActionDialog(TOPIC_SEARCH_ACTION_PRESETS.search, null);
}

function renderTopics() {
  const all = state.snapshot.workflows.production.topics;
  const items = filterTopics(all);
  /* 已生产的两条只作记录，单独排到后面，和待决策的隔开 */
  const active = items.filter((item) => topicState(item) !== "made");
  const archived = items.filter((item) => topicState(item) === "made");
  const cells = active.map((item) => topicCard(item));
  if (active.length && archived.length) {
    cells.push('<div class="tpl-split">已生产 · 仅作记录</div>');
  } else if (!active.length && archived.length) {
    cells.push('<div class="tpl-split">没有待决策的选题 · 以下都已进入脚本创作</div>');
  }
  cells.push(...archived.map((item) => topicCard(item)));
  const gridClass = active.length ? "tpl-grid" : "tpl-grid is-archive";
  elements.workspaceMain.innerHTML = `<div class="tpl-bar">${topicToolbar(all, items.length)}</div>
  ${cells.length ? `<div class="${gridClass}">${cells.join("")}</div>` : emptyState("没有符合当前筛选条件的选题")}`;
}


/* ==================== 推荐选题 ====================
   数据来自快照里的 workflows.production.recommended_topics：
   只读 我方资料/推荐选题/推荐选题结果.json（由 执行单元/保存推荐选题.py 写入，同目录还有一份同名 .md 给人看）。
   这一轮不搜索平台、不取数，所以卡片和抽屉都不显示分数、来源和评论。 */

function recommendedTopicsPayload() {
  return state.snapshot?.workflows?.production?.recommended_topics || null;
}

function recommendedTopicsTime(value) {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return String(value || "—");
  const pad = (number) => String(number).padStart(2, "0");
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())} ${pad(date.getHours())}:${pad(date.getMinutes())}`;
}

/* 内容主线三种，颜色跟首页「账号方向」那三栏同源（见 workspace-index.mjs 的 hues 顺序：
   问题判断=blue、真实过程=plum、方法复用=mint），两个页面看到的是同一套配色。 */
const MAINLINE_TONES = { "问题判断": "blue", "真实过程": "plum", "方法复用": "mint" };

function mainlineTone(mainline) {
  return MAINLINE_TONES[String(mainline || "").trim()] || "mint";
}

/* 首页卡片：序号 + 内容主线，标题，角度，最底下是目标人群。
   目标（流量 / 涨粉 / 交易）不上卡面，创作者挑选题看的是讲什么、从哪切。 */
function recommendTopicCard(topic) {
  return `<button type="button" class="tpl-card rt-card" data-action="open-recommend-topic" data-rank="${escapeAttribute(topic.rank)}">
    <span class="tpl-top">
      <span class="tpl-num">${String(topic.rank).padStart(2, "0")}</span>
      ${topic.mainline ? `<span class="rt-tag is-${mainlineTone(topic.mainline)}">${escapeHtml(topic.mainline)}</span>` : ""}
    </span>
    <span class="tpl-title">${escapeHtml(topic.theme || topic.title || "未命名选题")}</span>
    <span class="rt-angle">${escapeHtml(topic.angle || "")}</span>
    ${topic.audience ? `<span class="rt-card-meta">${escapeHtml(topic.audience)}</span>` : ""}
  </button>`;
}

/* ---------- 选题详情抽屉 ---------- */
let recommendTopicRank = 0;

function recommendTopicByRank(rank) {
  return (recommendedTopicsPayload()?.topics || []).find((topic) => Number(topic.rank) === Number(rank)) || null;
}

/* 字段口径和「选题分析」的六个核心要素一致：目标用户、核心痛点、收益承诺、核心命题、切口，
   后面不管走不走选题分析，两边说的是同一套东西。
   标题直接就是主题、内容主线放在标题下面，所以这两个不再单独占字段。
   剩下的按「先给结论、再给依据、最后给怎么拍」排：
   核心命题 → 引文；目标用户/核心痛点/收益承诺/切口 → 2×2；选题出处 → 底注；内容提纲 → 步骤。
   配色：核心命题和提纲跟着内容主线的色走，四块卡片每个字段固定一个色，方便扫读。 */
function recommendTopicDetailHtml(topic) {
  const tone = mainlineTone(topic.mainline);
  const cell = (name, value, cellTone) => `<section class="rt-cell" data-tone="${cellTone}">
      <div class="rt-lab">${name}</div>
      <p${value ? "" : ' class="is-empty"'}>${value ? escapeHtml(value) : "还没填"}</p>
    </section>`;
  const outline = Array.isArray(topic.outline) ? topic.outline : [];
  const steps = outline.length
    ? `<ol class="rt-steps">${outline.map((item) => `<li><span aria-hidden="true"></span><p>${escapeHtml(item)}</p></li>`).join("")}</ol>`
    : '<p class="rt-steps-empty">这条选题没有写内容提纲</p>';
  /* 「选题出处」是判断这条有没有根据的那一段：没有就整段不出现，不留「还没填」 */
  const source = topic.basis
    ? `<section class="rt-src">
      <div class="rt-lab">选题出处</div>
      <p>${escapeHtml(topic.basis)}</p>
    </section>`
    : "";

  return `<section class="rt-lead">
      <div class="rt-lab">核心命题</div>
      <p${topic.proposition ? "" : ' class="is-empty"'}>${topic.proposition ? escapeHtml(topic.proposition) : "还没填"}</p>
    </section>
    <div class="rt-grid">
      ${cell("目标用户", topic.audience, "user")}
      ${cell("核心痛点", topic.pain, "pain")}
      ${cell("收益承诺", topic.promise, "promise")}
      ${cell("切口", topic.angle, "angle")}
    </div>
    ${source}
    <section class="rt-block">
      <div class="rt-lab">内容提纲${outline.length ? `<i>${outline.length} 点</i>` : ""}</div>
      ${steps}
    </section>`;
}

function openRecommendTopicDrawer(rank) {
  const topic = recommendTopicByRank(rank);
  const dialog = elements.recommendTopicDrawer;
  if (!topic || !dialog) return;
  recommendTopicRank = Number(topic.rank) || 0;
  /* 标题就是主题；内容主线放在标题下面那行 */
  elements.recommendTopicDrawerTitle.innerHTML =
    `<span class="tpl-num">${String(topic.rank).padStart(2, "0")}</span><span>${escapeHtml(topic.theme || topic.title || "未命名选题")}</span>`;
  elements.recommendTopicDrawerSub.innerHTML = topic.mainline
    ? `<span class="rt-tag is-${mainlineTone(topic.mainline)}"><em>内容主线</em>${escapeHtml(topic.mainline)}</span>`
    : "";
  elements.recommendTopicDrawerBody.innerHTML = recommendTopicDetailHtml(topic);
  elements.recommendTopicDrawerBody.scrollTop = 0;
  elements.recommendTopicDrawerFoot.innerHTML = `<button class="tpl-primary" type="button" data-action="recommend-topic-produce" data-rank="${escapeAttribute(topic.rank)}"><svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 5v14M5 12h14"/></svg>用该选题进行内容创作</button>`;
  if (!dialog.open) dialog.showModal();
  /* 抽屉开着的时候底下的页面不跟着滚 */
  document.body.classList.add("is-drawer-open");
  requestAnimationFrame(() => dialog.classList.add("is-open"));
}

function closeRecommendTopicDrawer() {
  const dialog = elements.recommendTopicDrawer;
  if (!dialog || !dialog.open) return;
  dialog.classList.remove("is-open");
  window.setTimeout(() => {
    if (!dialog.classList.contains("is-open")) {
      dialog.close();
      document.body.classList.remove("is-drawer-open");
    }
  }, 300);
}

/* ---------- 指令弹窗：推荐选题 / 用该选题进行内容创作 ---------- */
let recommendTopicActionContext = null;

function recommendTopicsPrompt() {
  /* 只发这一句。读规划还是问卷、要不要搜平台，都是 skill 里写死的流程，
     写进指令等于把 skill 的正文抄一遍，skill 一改这两边就对不上。 */
  return skillPromptText("recommend-topics");
}

const RECOMMEND_TOPIC_ACTION_PRESETS = {
  generate: {
    title: "推荐选题",
    build: () => recommendTopicsPrompt(),
  },
};

function paintRecommendTopicAction(dialog) {
  const context = recommendTopicActionContext;
  if (!context) return;
  const text = context.preset.build(context.topic);
  dialog.dataset.prompt = text;
  dialog.querySelector("#rta-text").textContent = text;
}

function showRecommendTopicActionDialog(preset, topic) {
  const dialog = document.querySelector("#recommend-topic-action-dialog");
  if (!preset || !dialog) return;
  dialog.querySelector("#rta-title").textContent = preset.title;
  if (topic) {
    dialog.querySelector("#rta-topic").innerHTML =
      `<b>${escapeHtml(String(topic.rank).padStart(2, "0") + " " + (topic.theme || topic.title || "未命名选题"))}</b><span>${escapeHtml(topic.angle || "")}</span>`;
  } else {
    const data = recommendedTopicsPayload();
    const last = data?.topics?.length
      ? `上一轮 ${recommendedTopicsTime(data.generated_at)} · ${data.topics.length} 条`
      : "还没有生成过推荐选题";
    dialog.querySelector("#rta-topic").innerHTML =
      `<b>按账号方向直接推荐</b><span>${escapeHtml(last)}</span>`;
  }

  recommendTopicActionContext = { preset, topic };
  paintRecommendTopicAction(dialog);
  if (!dialog.open) dialog.showModal();
  dialog.setAttribute("tabindex", "-1");
  dialog.focus();
}

function openRecommendTopicsDialog() {
  showRecommendTopicActionDialog(RECOMMEND_TOPIC_ACTION_PRESETS.generate, null);
}

/* 「用该选题进行内容创作」就是「新增脚本 · 直接生产」，只是把这条选题填进「想法」。
   两条路最后都落到 create-content 的独立内容生产，没必要让用户在两个弹窗之间分辨该用哪个。
   想法里把这条选题已经定好的判断都带上——只给标题，AI 等于从零重做一遍选题分析。 */
function openRecommendTopicProduceDialog(rank) {
  const topic = recommendTopicByRank(rank);
  openPromptDialog("script", null, { hideModes: true });
  if (!topic || !elements.promptIdea) return;
  const lines = [String(topic.title || topic.theme || "").trim()];
  [
    ["核心痛点", topic.pain],
    ["核心命题", topic.proposition],
    ["切口", topic.angle],
  ].forEach(([label, value]) => {
    const text = String(value || "").trim();
    if (text) lines.push("", `${label}：${text}`);
  });
  elements.promptIdea.value = lines.join("\n").trim();
  syncPrompt();
}

/* 脚本创作只认三个状态：全部 / 生产中 / 已完成。
   阶段（内容定位、终稿优化这些）是流程内部的推进，放到抽屉里看，不做成筛选维度。 */
const CONTENT_STATES = [["all", "全部"], ["run", "生产中"], ["done", "已完成"]];

function productionStateKey(item) {
  return String(item.status || "").trim() === "已完成" ? "done" : "run";
}

function productionSegments(all) {
  const counts = { all: all.length, run: 0, done: 0 };
  all.forEach((item) => { counts[productionStateKey(item)] += 1; });
  const active = state.contentStage || "all";
  const buttons = CONTENT_STATES
    .map(([key, label]) => `<button type="button" role="tab" aria-selected="${active === key}" class="${active === key ? "on" : ""}" data-action="filter-content-stage" data-value="${key}">${label}<em>${counts[key]}</em></button>`)
    .join("");
  return `<nav class="tpl-seg" role="tablist" aria-label="按生产状态筛选">${buttons}</nav>`;
}

function renderContentProduction() {
  // 主表按登记顺序追加，最新内容在文件末尾；这里反序显示，让最近登记的排在最前。
  // 只收真正进过内容生产的内容：判断依据是有没有内容生产记录。
  // 账号诊断/账号刷新时采集回来的历史已发布内容也在我方内容主表里，但它们没走过生产，不属于脚本创作。
  const all = [...state.snapshot.workflows.production.own_contents]
    .filter((item) => item.production_record_path)
    .reverse();
  // 生产中的排在前面，做完的收在下面（照设计稿「生产中在上，已完成收在下面」）
  const active = state.contentStage || "all";
  const items = active === "all" ? all : all.filter((item) => productionStateKey(item) === active);
  const creating = items.filter((item) => productionStateKey(item) !== "done");
  const done = items.filter((item) => productionStateKey(item) === "done");
  const cells = creating.map((item) => productionCard(item));
  if (creating.length && done.length) cells.push('<div class="tpl-split">已完成</div>');
  cells.push(...done.map((item) => productionCard(item)));
  elements.pageSubtitle.textContent = `共 ${all.length} 条走过生产流程 · 按当前阶段筛选`;
  elements.workspaceMain.innerHTML = `<div class="tpl-bar">${productionSegments(all)}</div>`
    + (cells.length
      ? `<div class="tpl-grid">${cells.join("")}</div>`
      : emptyState(all.length ? "这个阶段还没有内容" : "还没有脚本创作记录"));
}


function renderPublications() {
  const all = state.snapshot.workflows.production.publications || [];
  const items = filterItems(all, ["title", "account_name", "platform", "data_level", "search_text"]);
  elements.workspaceMain.innerHTML = listShell({
    ...listHead(updatedChip()),
    body: toolbar("筛选发布内容", items.length) + cardGrid(items.map((item, index) => publicationCard(item, index + 1)), "还没有发布记录", "works-grid"),
  });
}

// 爆款规律页：把沉淀结论里的正式资产集中看一眼。资产在索引层投影成 workflows.patterns。
// 两个子页面：规律分析（原规律知识库）、规律词典（原特征词典）。
/* ============ 爆款规律：规律分析 / 规律词典 ============
   两个分区共用一条工具条 + 一条二级分段控件；卡片、抬头、标签的样式在 theme-2.css 第 16 节。 */

const PATTERN_TABS = [
  ["report", "规律分析"],
  ["dictionary", "规律词典"],
];

const PATTERN_TAB_HINTS = {
  dictionary: "还没有正式特征。完成一次对标内容分析后会写入规律词典。",
};

// 规律词典的六个分类与作用环节顺序，和设计稿一致。
const PATTERN_CATEGORY_ORDER = ["内容类别", "标题", "开头", "内容结构", "表达风格", "互动设计"];
const PATTERN_STAGE_LABELS = ["吸引", "持续", "信任", "获得", "沉淀"];

// 四个证据层级，越往下证据越弱。
const PATTERN_LEVELS = [
  ["all", "全部"],
  ["self", "我方效果验证过的"],
  ["multi", "多账号对标验证过的"],
  ["single", "单账号里成立的"],
  ["repeat", "出现过、但判不出效果的"],
];

const PATTERN_ICON = {
  refresh: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M20 12a8 8 0 1 1-2.6-5.9"/><path d="M20 4.5V9h-4.5"/></svg>',
  plus: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 5v14M5 12h14"/></svg>',
};

// ⛔ 工作台 CSP 是 style-src 'self'，HTML 里的行内 style 会被丢掉：
// 条的宽度先写 data-w，插进 DOM 之后再用 CSSOM 赋上（CSSOM 不受 CSP 限制）。
function paintPatterns(html) {
  elements.workspaceMain.innerHTML = html;
  elements.workspaceMain.querySelectorAll(".pt-bar s[data-w]").forEach((bar) => {
    bar.style.width = `${Math.min(Number(bar.dataset.w) || 0, 100)}%`;
  });
}

function patternTabTotal(tab) {
  const patterns = state.snapshot.workflows.patterns;
  if (!patterns || tab === "report") return null;
  return patterns[tab]?.total ?? 0;
}

function patternToolbar() {
  const active = state.patternTab || "report";
  const tabs = PATTERN_TABS.map(([id, label]) => {
    const total = patternTabTotal(id);
    const count = total === null ? "" : `<em>${total}</em>`;
    return `<button type="button" role="tab" data-action="select-pattern-tab" data-section="${id}" aria-selected="${active === id}" class="${active === id ? "on" : ""}">${escapeHtml(label)}${count}</button>`;
  }).join("");
  return `<div class="pt-toolbar"><div class="pt-seg" role="tablist" aria-label="爆款规律分区">${tabs}</div></div>`;
}

/* ---------- 规律分析：把 Markdown 报告解析成卡片 ---------- */

function patternTableCells(line) {
  return String(line || "").trim().replace(/^\|/, "").replace(/\|$/, "").split("|").map((cell) => cell.trim());
}

function patternReportSections(md) {
  const sections = {};
  for (const part of String(md || "").split(/\n##\s+/).slice(1)) {
    const lines = part.split("\n");
    const title = (lines.shift() || "").trim();
    const tableLines = lines.filter((line) => line.trim().startsWith("|"));
    // 章节可能暂时没有表格（比如我方验证还是 0 条），也要留标题，前端才能显示空态。
    const head = tableLines.length ? patternTableCells(tableLines[0]) : [];
    const rows = tableLines.length >= 3
      ? tableLines.slice(2).map(patternTableCells).filter((cells) => cells.length === head.length)
      : [];
    sections[title] = { head, rows };
  }
  return sections;
}

function patternReportSection(sections, prefix) {
  for (const [title, section] of Object.entries(sections)) {
    if (title.startsWith(prefix)) return section;
  }
  return null;
}

function patternAccountShort(name) {
  const raw = String(name || "").trim();
  const dash = raw.indexOf("-");
  const short = dash >= 0 ? raw.slice(dash + 1).trim() : raw;
  // 只用在「本轮说明」那种一句话里，卡片上用的是一直完整名字。
  return /\s/.test(short) ? short.split(/\s+/)[0] : short;
}

function patternLevelSeg(counts) {
  const active = state.patternLevel || "all";
  const buttons = PATTERN_LEVELS.map(([key, label]) => {
    const count = key === "all" ? counts.total : (counts[key] ?? 0);
    return `<button type="button" role="tab" data-action="select-pattern-level" data-level="${key}" aria-selected="${active === key}" class="${active === key ? "on" : ""}">${escapeHtml(label)}<em>${count}</em></button>`;
  }).join("");
  return `<div class="pt-sub"><div class="pt-seg pt-seg-sub" role="tablist" aria-label="证据层级">${buttons}</div></div>`;
}

// 区块标题：标题 + 一句说明（右边）+ 条数（最右），三者同一行。
function patternSectionHead(title, hint, countText) {
  return `<div class="pt-sec-head"><h2>${escapeHtml(title)}</h2>${hint ? `<p>${escapeHtml(hint)}</p>` : ""}<span>${escapeHtml(countText)}</span></div>`;
}

function patternTypeTag(type) {
  const value = String(type || "").trim();
  return value ? `<span class="pt-chip">${escapeHtml(value)}</span>` : "";
}

// 档位分布一格里每个账号一行，用 <br> 分隔；解析成账号对象，方便排序和按账号取案例。
function patternTierEntries(text) {
  return String(text || "").split(/<br\s*\/?>/i).map((line) => line.trim()).filter(Boolean).map((line) => {
    const match = line.match(/^(.+?)：(.+)$/);
    if (!match) return null;
    const account = match[1].trim();
    const rest = match[2];
    const dash = account.indexOf("-");
    const platform = dash >= 0 ? account.slice(0, dash) : "";
    const fullName = (dash >= 0 ? account.slice(dash + 1) : account).trim();
    /* 证据栏有两种写法：
       ① 带基数与百分比「高档 12／20（60%）」——按档内占比显示；
       ② 只写篇数「高档 3／低档 3」「高档 5／中档 10／低档 2」——这是合并执行器
          现在实际写出的格式，没有基数，算不出档内占比，就只显示篇数，
          条按三档里的最大值等比画，不硬造百分比。 */
    const cells = [["高档", "hi"], ["中档", "mid"], ["低档", "lo"]].map(([level, tone]) => {
      const withPct = rest.match(new RegExp(`${level}\\s*(\\d+)\\s*／\\s*(\\d+)（(\\d+)%）`));
      if (withPct) return { level, tone, pct: Number(withPct[3]), count: Number(withPct[1]), base: Number(withPct[2]) };
      const plain = rest.match(new RegExp(`${level}\\s*(\\d+)`));
      if (plain) return { level, tone, pct: null, count: Number(plain[1]), base: null };
      return { level, tone, pct: null, count: null, base: null };
    });
    const maxCount = Math.max(...cells.map((cell) => cell.count || 0), 0);
    cells.forEach((cell) => {
      cell.width = cell.pct !== null
        ? Math.min(cell.pct, 100)
        : (maxCount ? Math.round(((cell.count || 0) / maxCount) * 100) : 0);
    });
    const high = cells.find((cell) => cell.tone === "hi");
    return { name: fullName, fullName, platform, cells, high: high?.pct ?? (high?.count || 0) };
  }).filter(Boolean);
}

// 一个账号：名字 + 平台两行，右边三行（档位 / 百分比或篇数 / 根数 / 一根条）。
function patternAccountBlock(entry) {
  const rows = entry.cells.map((cell) => {
    const value = cell.pct !== null
      ? `${cell.pct}%`
      : (cell.count === null ? "—" : String(cell.count));
    const note = cell.pct !== null ? `${cell.count}/${cell.base}` : (cell.count === null ? "" : "篇");
    return `<div class="pt-ar">
      <span class="pt-ak">${cell.level}</span>
      <b class="pt-av">${value}</b>
      <em>${note}</em>
      <i class="pt-bar ${cell.tone}"><s data-w="${cell.width}"></s></i>
    </div>`;
  }).join("");
  return `<div class="pt-acct">
    <div class="pt-ah"><b title="${escapeAttribute(entry.fullName)}">${escapeHtml(entry.fullName)}</b>${entry.platform ? `<span>${escapeHtml(entry.platform)}</span>` : ""}</div>
    <div>${rows}</div>
  </div>`;
}

function patternCaseItems(text) {
  const items = [];
  const pattern = /\[《(.+?)》\]\(([^)]+)\)/g;
  let match;
  while ((match = pattern.exec(String(text || "")))) items.push({ title: match[1], href: match[2] });
  return items;
}

function patternCaseLink(item) {
  const short = item.title.length <= 24 ? item.title : `${item.title.slice(0, 24)}…`;
  return `<a href="${escapeAttribute(item.href)}" title="${escapeAttribute(item.title)}">《${escapeHtml(short)}》</a>`;
}

function patternRuleCard(row, options = {}) {
  const name = row[0];
  const type = row[1];
  const definition = row[2];
  const distribution = row[3];
  const caseItems = patternCaseItems(row[row.length - 1]);
  let body = "";
  let links = [];
  if (options.repeat) {
    body = `<div class="pt-rep"><span>三档分布</span><p>${escapeHtml(distribution || "")}</p></div>`;
    links = caseItems.map(patternCaseLink);
  } else {
    // 后端不限制账号数，卡片里只展示高档占比最高的三个账号，案例按账号一一对应取。
    const entries = patternTierEntries(distribution);
    const picked = entries.slice().sort((a, b) => b.high - a.high).slice(0, 3);
    links = picked.map((entry) => caseItems[entries.indexOf(entry)]).filter(Boolean).map(patternCaseLink);
    body = picked.map(patternAccountBlock).join("");
  }
  const caseBox = links.length
    ? `<div class="pt-case"><span class="pt-k">案例</span><div class="pt-l">${links.join("")}</div></div>`
    : "";
  return `<article class="pt-card">
    <div class="pt-card-top">
      <div class="pt-card-title"><h3>${escapeHtml(name)}</h3>${patternTypeTag(type)}</div>
      <p class="pt-card-def">${escapeHtml(definition)}</p>
    </div>
    <div class="pt-card-body">${body}${caseBox}</div>
  </article>`;
}

function patternRuleSection(title, hint, rows, options = {}) {
  const body = rows.length
    ? `<div class="pt-cards">${rows.map((row) => patternRuleCard(row, options)).join("")}</div>`
    : `<div class="pt-empty-line">${escapeHtml(options.empty || "这一档暂时没有条目。")}</div>`;
  return `<section class="pt-sec">${patternSectionHead(title, hint, `${rows.length} 条`)}${body}</section>`;
}

function patternReportIntro(md) {
  const part = String(md || "").split("## 1. 本轮说明")[1]?.split("## 2.")[0] || "";
  const values = {};
  const samples = [];
  for (const line of part.split("\n")) {
    if (!line.trim().startsWith("|")) continue;
    const cells = patternTableCells(line);
    if (cells.length < 2 || cells[0] === "项" || /^[-:\s]+$/.test(cells[0])) continue;
    const [key, value] = cells;
    if (["生成时间", "覆盖账号数", "纳入内容数", "做法总数"].includes(key)) values[key] = value;
    else if (/^\d+$/.test(value)) samples.push([key, Number(value)]);
  }
  samples.sort((a, b) => b[1] - a[1]);
  const when = String(values["生成时间"] || "").slice(0, 16).replace("T", " ");
  const picks = samples.filter(([, count]) => count > 0).slice(0, 3)
    .map(([name, count]) => `${patternAccountShort(name)} ${count} 篇`);
  return {
    when,
    stats: {
      accounts: values["覆盖账号数"] || "",
      contents: values["纳入内容数"] || "",
      rules: values["做法总数"] || "",
    },
    html: `<b>本轮说明</b>　生成于 ${escapeHtml(when || "未知时间")} ｜ 覆盖 ${escapeHtml(values["覆盖账号数"] || "?")} 个账号 ｜ ${escapeHtml(values["纳入内容数"] || "?")} 篇纳入内容 ｜ ${escapeHtml(values["做法总数"] || "?")} 条做法。${picks.length ? `样本：${escapeHtml(picks.join("、"))}。` : ""}判定依据是平台公开的点赞、收藏、评论，不等于赚钱多少。`,
  };
}

// 工作区当前的数据量，用来和上次规律分析对比。
function patternWorkspaceStats() {
  const workflows = state.snapshot?.workflows || {};
  const benchmarkAccounts = workflows.insight?.benchmark_accounts || [];
  const benchmarkContents = workflows.insight?.benchmark_contents || [];
  const ownAccounts = workflows.self?.own_accounts || [];
  const ownContents = workflows.production?.own_contents || [];
  /* 词典条数直接从 内容特征词典.csv 读，不用等重新分析 */
  const dict = workflows.patterns?.dictionary;
  const dictCount = Number(dict?.total ?? dict?.rows?.length ?? 0) || 0;
  return {
    benchmarkAccounts: benchmarkAccounts.length,
    benchmarkAccountsWithContents: benchmarkAccounts.filter((item) => Number(item.content_count) > 0).length,
    benchmarkContents: benchmarkContents.length,
    ownAccounts: ownAccounts.length,
    ownContents: ownContents.length,
    dictCount,
  };
}

function patternReportView(md, path) {
  const sections = patternReportSections(md);
  const self = patternReportSection(sections, "2. 我方")?.rows || [];
  const multi = patternReportSection(sections, "3. 多账号")?.rows || [];
  const single = patternReportSection(sections, "4. 单账号")?.rows || [];
  const repeat = patternReportSection(sections, "5. 出现过")?.rows || [];
  const intro = patternReportIntro(md);
  const level = state.patternLevel || "all";
  const show = (key) => level === "all" || level === key;
  const counts = {
    total: multi.length + single.length + repeat.length,
    self: self.length,
    multi: multi.length,
    single: single.length,
    repeat: repeat.length,
  };
  const selfBody = self.length
    ? `<div class="pt-cards">${self.map((row) => patternRuleCard(row)).join("")}</div>`
    : `<div class="pt-empty-line">暂时没有：我方内容还没攒够能分档的样本。</div>`;
  return `${patternToolbar()}
    ${patternLevelSeg(counts)}
    <p class="pt-legend">本次分析纳入了 ${escapeHtml(intro.stats.accounts || "?")} 个账号、${escapeHtml(intro.stats.contents || "?")} 个内容。</p>
    ${show("self") ? `<section class="pt-sec">${patternSectionHead("我方效果验证过的", "我方账号自己的数据里验证成立，1 个账号成立就进这一层", `${self.length} 条`)}${selfBody}</section>` : ""}
    ${show("multi") ? patternRuleSection("多账号对标验证过的", "至少两个账号判出同一方向，没有账号给出相反结论", multi) : ""}
    ${show("single") ? patternRuleSection("单账号里成立的", "只有一个账号判出方向，当线索，不当规律", single) : ""}
    ${show("repeat") ? patternRuleSection("出现过、但判不出效果的", "用过这个做法，但按互动数分档后看不出稳定关系；只列全工作区出现 3 篇以上的做法", repeat, { repeat: true }) : ""}
    <div class="pt-notice"><b>判定口径</b>　每个账号的内容按核心互动数分成高、中、低三档，同一做法在高档与低档的篇数相差 2 篇以上、且高档更多，才算「数据好的内容更常用」。依据是平台公开的点赞、收藏、评论，<b>不等于赚多少钱</b>；不同赛道的账号混在一起看时，结论只能当线索。</div>
    <p class="pt-foot">来源：${escapeHtml(path)}${intro.when ? ` · 生成于 ${escapeHtml(intro.when)}` : ""}</p>`;
}

/* ---------- 规律词典：特征卡片 ---------- */

function patternDictionaryGroups(items) {
  const groups = new Map(PATTERN_CATEGORY_ORDER.map((category) => [category, []]));
  for (const item of items) {
    const category = String(item.category || "").trim() || "未分类";
    if (!groups.has(category)) groups.set(category, []);
    groups.get(category).push(item);
  }
  return groups;
}

function patternStageChain(item) {
  const active = new Set(String(item.stages || "").split("·").filter(Boolean));
  if (!PATTERN_STAGE_LABELS.some((label) => active.has(label))) return "";
  return `<div class="pt-chain">${PATTERN_STAGE_LABELS.map((label, index) => `${index ? '<span class="pt-lk"></span>' : ""}<span class="pt-st${active.has(label) ? " on" : ""}"><i></i><b>${label}</b></span>`).join("")}</div>`;
}

function patternDictionaryCard(item) {
  const status = String(item.status || "").trim() || "未填写";
  const off = status === "停用";
  const chain = patternStageChain(item);
  return `<article class="pt-dcard${off ? " off" : ""}" data-dict-id="${escapeAttribute(item.id)}" title="${escapeAttribute(item.id)}">
    <div class="pt-dhead">
      <h3>${escapeHtml(item.label)}</h3>
      <div class="pt-dstatus">
        <button class="pt-state${off ? " off" : ""}" type="button" data-action="toggle-dict-menu" data-id="${escapeAttribute(item.id)}" aria-haspopup="true" aria-expanded="false"><span>${off ? "已停用" : "启用"}</span><svg viewBox="0 0 12 12" aria-hidden="true"><path d="M3 4.5 6 7.5 9 4.5"/></svg></button>
        <div class="pt-menu" hidden>
          <button type="button" data-action="set-dict-status" data-id="${escapeAttribute(item.id)}" data-next="${off ? "启用" : "停用"}">${off ? "启用" : "停用"}</button>
          <button class="pt-menu-danger" type="button" data-action="delete-dict" data-id="${escapeAttribute(item.id)}" data-label="${escapeAttribute(item.label || "")}">删除</button>
        </div>
      </div>
    </div>
    <p class="pt-ddef">${escapeHtml(item.definition)}</p>
    ${chain}
  </article>`;
}

function patternCategorySeg(groups, total) {
  const active = state.patternDictCategory || "all";
  const button = (key, label, count) =>
    `<button type="button" role="tab" data-action="select-pattern-dict-category" data-category="${escapeAttribute(key)}" aria-selected="${active === key}" class="${active === key ? "on" : ""}">${escapeHtml(label)}<em>${count}</em></button>`;
  const buttons = [button("all", "全部", total)];
  for (const [category, items] of groups) buttons.push(button(category, category, items.length));
  return `<div class="pt-sub"><div class="pt-seg pt-seg-sub" role="tablist" aria-label="特征分类">${buttons.join("")}</div></div>`;
}

function patternDictionaryView(asset) {
  if (!asset) return `${patternToolbar()}${emptyState("没有规律词典", "当前工作区读取不到沉淀结论/内容特征词典.csv")}`;
  const items = asset.rows || [];
  if (!items.length) return `${patternToolbar()}${emptyState("规律词典还没有内容", PATTERN_TAB_HINTS.dictionary)}`;
  const groups = patternDictionaryGroups(items);
  const active = state.patternDictCategory || "all";
  const visible = active === "all" ? [...groups.entries()] : [[active, groups.get(active) || []]];
  const sections = visible.map(([category, rows]) => {
    const body = rows.length
      ? `<div class="pt-cards pt-cards-3">${rows.map(patternDictionaryCard).join("")}</div>`
      : `<div class="pt-empty-line">这个分类还没有特征。</div>`;
    return `<section class="pt-sec">${patternSectionHead(category, "", `${rows.length} 个`)}${body}</section>`;
  }).join("");
  return `${patternToolbar()}${patternCategorySeg(groups, items.length)}<p class="pt-legend">作用环节　吸引 → 持续 → 信任 → 获得 → 沉淀。绿点是它实际起作用的那一段。</p><div class="pt-dict">${sections}</div>`;
}

/* ---------- 刷新规律：对比上次分析与当前数据量，再给可复制提示词 ---------- */

async function copyPlainText(text, button, errorNode, textNode) {
  if (!text) return;
  const original = button.innerHTML;
  const ok = await writeClipboard(text);
  if (ok) {
    setInlineError(errorNode, "");
    button.innerHTML = '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M5 12.5 9.5 17 19 7.5"/></svg>已复制';
    button.disabled = true;
    window.setTimeout(() => {
      button.innerHTML = original;
      button.disabled = false;
    }, 1800);
    return;
  }
  setInlineError(errorNode, "没能自动复制，已经选中文字了，按 Cmd+C 就行");
  if (!textNode) return;
  const range = document.createRange();
  range.selectNodeContents(textNode);
  const selection = window.getSelection();
  selection.removeAllRanges();
  selection.addRange(range);
}

async function openPatternRefresh() {
  const patterns = state.snapshot?.workflows?.patterns;
  if (!patterns?.report?.path) {
    showToast("还没有规律分析报告，先跑一次规律分析");
    return;
  }
  elements.refreshBody.innerHTML = '<div class="loading-state"><span></span><p>正在读取上次分析</p></div>';
  elements.refreshResult.hidden = true;
  elements.refreshCopy.disabled = true;
  setInlineError(elements.refreshError, "");
  elements.refreshDialog.showModal();
  /* showModal 会自动聚焦第一个可聚焦元素（右上角 ×），一进来就是一圈焦点环；
     这个弹窗没有输入框，把焦点收回到弹窗本身，Tab 才进到按钮 */
  elements.refreshDialog.setAttribute("tabindex", "-1");
  elements.refreshDialog.focus();

  let md = "";
  try {
    md = (await fetchFile(patterns.report.path)).content || "";
  } catch (_error) {
    md = "";
  }
  const intro = patternReportIntro(md);
  const last = intro.stats;
  const now = patternWorkspaceStats();
  const rows = [
    ["对标账号", last.accounts ? `${last.accounts} 个` : "—", `${now.benchmarkAccounts} 个`],
    ["纳入分析的内容", last.contents ? `${last.contents} 篇` : "—", `${now.benchmarkContents} 篇`],
    ["我的账号", "—", `${now.ownAccounts} 个`],
    ["我的内容", "—", `${now.ownContents} 篇`],
    ["词典条数", last.rules ? `${last.rules} 条` : "—", `${now.dictCount} 条`],
  ];
  const table = `<table class="pv-refresh-table"><thead><tr><th>数据</th><th>上次分析</th><th>现在工作区</th></tr></thead><tbody>${rows
    .map(([name, before, after]) => `<tr><td>${escapeHtml(name)}</td><td>${escapeHtml(before)}</td><td>${escapeHtml(after)}</td></tr>`)
    .join("")}</tbody></table>`;
  elements.refreshBody.innerHTML = `<p class="pv-refresh-when">上次生成：${escapeHtml(intro.when || "未知时间")}</p>${table}`;

  const prompt = "用 pattern-library 重新跑一遍规律分析，按 skill 的完整流程执行";
  elements.refreshText.textContent = prompt;
  elements.refreshResult.hidden = false;
  elements.refreshCopy.disabled = false;
}

/* ---------- 规律词典：启用 / 停用 / 删除 / 新增 ---------- */

async function postDictionary(payload) {
  const response = await fetch("/api/dictionary/update", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(data.error || `dictionary_update_failed_${response.status}`);
  return data;
}

async function setDictionaryStatus(id, next) {
  const action = next === "启用" ? "enable" : "disable";
  try {
    await postDictionary({ id, action });
    await loadSnapshot({ force: true });
  } catch (error) {
    showToast(`操作没成功：${error.message || "服务端没有返回原因"}`);
  }
}

let pendingDictDeleteId = "";

function openDictionaryDelete(id, label) {
  pendingDictDeleteId = id;
  pendingDeleteId = "";
  pendingContentDeleteId = "";
  elements.confirmTitle.textContent = "删除这个特征？";
  elements.topicConfirmCopy.textContent = `「${label || id}」会从规律词典里删掉；删除前会先备份整份 内容特征词典.csv。`;
  elements.topicConfirmList.innerHTML = [`特征ID：${id}`, "备份留在 我方资料/.trash/ 里"]
    .map((line) => `<li>${escapeHtml(line)}</li>`)
    .join("");
  setInlineError(elements.confirmError, "");
  elements.topicConfirm.showModal();
}

async function confirmDeleteDictionary() {
  const id = pendingDictDeleteId;
  if (!id) return;
  elements.topicConfirmOk.disabled = true;
  elements.topicConfirmOk.textContent = "正在删除…";
  try {
    await postDictionary({ id, action: "delete" });
    pendingDictDeleteId = "";
    elements.topicConfirm.close();
    await loadSnapshot({ force: true });
  } catch (error) {
    setInlineError(elements.confirmError, `删除没成功：${error.message || "服务端没有返回原因"}。表格没动，可以重试。`);
  } finally {
    elements.topicConfirmOk.disabled = false;
    elements.topicConfirmOk.textContent = "确认删除";
  }
}

/* 新增词典只剩一条路：让 AI 自己筛。
   内容特征入库流程 2.1 是扫 沉淀结论/内容特征观察表.csv、找词典还没覆盖的做法，
   不需要用户填任何东西；要改某一条已有特征，在词典卡片上直接改。 */
function dictionaryAiPrompt() {
  return "用 content-patterns 更新内容特征：先给我候选和建议，我确认后再写词典。";
}

function openDictionaryAdd() {
  elements.dictText.textContent = dictionaryAiPrompt();
  elements.dictCopy.disabled = false;
  elements.dictCopy.hidden = false;
  elements.dictDialog.showModal();
  /* 没有输入框，焦点收回到弹窗本身，否则会停在右上角 × 上 */
  elements.dictDialog.setAttribute("tabindex", "-1");
  elements.dictDialog.focus();
}

// 「规律分析」还没有正式内容时的空态：文件缺失、或文件里只有出厂的占位正文，两种都走这里。
function patternReportEmptyState() {
  return emptyState("还没有规律分析", "沉淀结论/内容规律知识库.md 还没有正式内容，点右上角「更新规律」后会生成");
}

// 交付包里预置的是占位正文（只有一级标题 + 一句「当前还没有生成」，没有任何二级章节）。
// 占位不算正式内容：否则占位会被当报告原样渲染，用户看不到「还没有生成」的提示。
function hasPatternReportSections(content) {
  return /^##[ \t]/m.test(String(content || ""));
}

async function renderPatterns() {
  const patterns = state.snapshot.workflows.patterns;
  if (!patterns) {
    elements.workspaceMain.innerHTML = emptyState("爆款规律还没有可用数据", "当前工作区读取不到沉淀结论里的正式资产");
    return;
  }
  const tab = state.patternTab || "report";
  if (tab === "dictionary") {
    paintPatterns(patternDictionaryView(patterns.dictionary));
    return;
  }
  if (!patterns.report?.path) {
    elements.workspaceMain.innerHTML = `${patternToolbar()}${patternReportEmptyState()}`;
    return;
  }
  const viewAtStart = state.currentView;
  elements.workspaceMain.innerHTML = `${patternToolbar()}<div class="loading-state"><span></span><p>正在读取规律分析</p></div>`;
  try {
    const file = await fetchFile(patterns.report.path);
    if (state.currentView !== viewAtStart || state.patternTab !== "report") return;
    if (!hasPatternReportSections(file.content)) {
      elements.workspaceMain.innerHTML = `${patternToolbar()}${patternReportEmptyState()}`;
      return;
    }
    state.patternReport = { md: file.content, path: file.path || patterns.report.path };
    paintPatterns(patternReportView(file.content, file.path || patterns.report.path));
  } catch (error) {
    elements.workspaceMain.innerHTML = `${patternToolbar()}${emptyState("规律分析读取失败", error.message)}`;
  }
}

const SKILL_ICONS = {
  dashboard: '<rect x="3.6" y="3.6" width="7" height="7" rx="2.2"/><rect x="13.4" y="3.6" width="7" height="7" rx="2.2"/><rect x="3.6" y="13.4" width="7" height="7" rx="2.2"/><rect x="13.4" y="13.4" width="7" height="7" rx="2.2"/>',
  quickstart: '<circle cx="12" cy="12" r="8.2"/><path d="M10.3 8.9 15.4 12l-5.1 3.1z"/>',
  diagnose: '<circle cx="11" cy="11" r="6.6"/><path d="m15.9 15.9 4.1 4.1M7.9 11h2l1-2.2 1.6 4.4 1-2.2h1.7"/>',
  update: '<path d="M20 12a8 8 0 1 1-2.6-5.9M20 4.4v4.4h-4.4"/>',
  "media-strategy": '<circle cx="12" cy="12" r="8.2"/><path d="M15.7 8.3 13.4 14.1 8.3 15.7 10.6 9.9z"/>',
  "self-account-diagnose": '<circle cx="10" cy="8.6" r="3.3"/><path d="M4 20c.8-3.5 2.9-5.3 6-5.3 1.1 0 2.1.2 2.9.7"/><path d="m14.6 17.2 2 2 4-4.4"/>',
  "benchmark-account-diagnose": '<circle cx="8.8" cy="9" r="3.1"/><path d="M2.6 19.4c.8-3.2 2.8-4.9 6.2-4.9s5.4 1.7 6.2 4.9"/><circle cx="17.2" cy="9.6" r="2.6"/><path d="M16.4 14.7c2.5.3 4.1 1.7 4.7 4.3"/>',
  "benchmark-content-diagnose": '<path d="M8.4 3.6h7.2l3.4 3.4v9.4H8.4zM15.6 3.6v3.4H19M5.6 7.6v12.8h9"/>',
  "topic-evaluate": '<path d="M9.6 18.4h4.8M10.6 21h2.8M8.2 14.4a5.5 5.5 0 1 1 7.6 0c-.8.9-.9 1.6-.9 2.1H9.1c0-.5-.1-1.2-.9-2.1z"/>',
  "create-content": '<path d="M4.4 17.4 14.2 7.6l2.8 2.8-9.8 9.8H4.4zM13 8.8l2.8 2.8M16.6 4.4l3 3-2 2-3-3z"/>',
  "content-review": '<path d="m4.6 6.4 1.7 1.7 3-3.3M4.6 12.4l1.7 1.7 3-3.3M4.6 18.4l1.7 1.7 3-3.3M12.6 6.8h7M12.6 12.8h7M12.6 18.8h4.8"/>',
  "content-patterns": '<path d="M12 3.6 20.4 8 12 12.4 3.6 8zm-8.4 8.8 8.4 4.4 8.4-4.4m-16.8 4.2 8.4 4.4 8.4-4.4"/>',
  "pattern-library": '<path d="M4.4 5.2a1.8 1.8 0 0 1 1.8-1.8h13v16.4h-13a1.8 1.8 0 0 0-1.8 1.8zM8.8 7.6h6.6M8.8 11.2h4.6"/>',
  "dbs-hook": '<rect x="3.4" y="5.2" width="17.2" height="13.6" rx="3.2"/><path d="M10.4 9.4 15.2 12l-4.8 2.6z"/>',
  "dbs-xhs-title": '<path d="M20.4 12.4 12.4 20.4a2 2 0 0 1-2.8 0L3.6 14.4V3.6h10.8l6 6a2 2 0 0 1 0 2.8z"/><circle cx="8.4" cy="8.4" r="1.5"/>',
};

function skillIcon(name) {
  return `<svg width="22" height="22" viewBox="0 0 24 24" fill="none" aria-hidden="true">${SKILL_ICONS[name] || SKILL_ICONS.dashboard}</svg>`;
}

function skillCard(item) {
  const category = item.category || "扩展能力";
  /* 卡片本身不再开详情弹窗：唯一入口是右上角「使用技能」。
     分类挪到标题下面，图标和「标题 + 分类」整块同高，上下沿对齐。 */
  return `<article class="skill-card" data-category="${escapeAttribute(category)}">
    <div class="skill-card-head">
      <span class="skill-icon">${skillIcon(item.name)}</span>
      <div class="skill-card-title">
        <span class="skill-name">${escapeHtml(item.display_name || item.name)}</span>
        <span class="skill-category-chip">${escapeHtml(category)}</span>
      </div>
      <button class="skill-use" type="button" data-action="use-skill" data-id="${escapeAttribute(item.name)}">使用技能</button>
    </div>
    <p class="skill-desc">${escapeHtml(item.description || "暂无说明")}</p>
    <footer class="skill-card-foot"><span class="skill-slug">/${escapeHtml(item.name)}</span></footer>
  </article>`;
}

function renderSkills() {
  const all = state.snapshot.workflows.skills || [];
  const catalog = state.snapshot.workflows.skill_catalog;
  const categoryOrder = [];
  const categoryCounts = new Map();
  const ensureCategory = (label) => {
    const category = label || "扩展能力";
    if (!categoryCounts.has(category)) {
      categoryCounts.set(category, 0);
      categoryOrder.push(category);
    }
    return category;
  };
  // 分类顺序仍由《Skill 分类说明》决定，未收录的能力排在最后。
  for (const category of catalog?.categories || []) ensureCategory(category.label);
  for (const item of all) {
    const category = ensureCategory(item.category);
    categoryCounts.set(category, categoryCounts.get(category) + 1);
  }
  const categories = categoryOrder.filter((category) => categoryCounts.get(category) > 0);
  const activeCategory = categories.includes(state.skillCategory) ? state.skillCategory : "all";
  state.skillCategory = activeCategory;
  const queried = filterItems(all, ["name", "display_name", "description", "category"]);
  const items = activeCategory === "all" ? queried : queried.filter((item) => (item.category || "扩展能力") === activeCategory);
  const countLabel = items.length === all.length
    ? `${all.length} 个技能 · ${categories.length} 个分类`
    : `${items.length} 个技能${activeCategory === "all" ? "" : ` · ${activeCategory}`}`;
  const chips = [
    `<button class="skill-chip" type="button" data-action="filter-skill-category" data-value="all" aria-pressed="${activeCategory === "all"}">全部<span>${all.length}</span></button>`,
    ...categories.map((category) => `<button class="skill-chip" type="button" data-action="filter-skill-category" data-value="${escapeAttribute(category)}" aria-pressed="${activeCategory === category}">${escapeHtml(category)}<span>${categoryCounts.get(category)}</span></button>`),
  ].join("");
  const emptyTitle = all.length ? "没有匹配的技能" : "没有可公开调用的 Skill";
  const emptyDescription = all.length ? "换个关键词，或者点「全部」看完整列表" : "在 skills/ 目录建立 SKILL.md 后会在这里展示";
  elements.workspaceMain.innerHTML = `<section class="skills-page">
    <header class="skills-head"><p class="skills-kicker">SKILLS</p><div class="skills-head-row"><h1>技能</h1><p>当前可直接调用的公开能力，覆盖从规划到发布后再回到规律的完整链路</p><span class="skills-count">${escapeHtml(countLabel)}</span></div></header>
    <div class="skills-toolbar"><div class="skills-chips" role="group" aria-label="按分类筛选">${chips}</div><label class="skill-search"><svg width="15" height="15" viewBox="0 0 24 24" fill="none" aria-hidden="true"><circle cx="10.8" cy="10.8" r="6.6"/><path d="m15.8 15.8 4.4 4.4"/></svg><input type="search" data-list-filter value="${escapeAttribute(state.listQuery)}" placeholder="搜索技能名" autocomplete="off" aria-label="搜索技能"></label></div>
    <div class="skills-grid">${items.map(skillCard).join("")}${items.length ? "" : `<div class="skills-empty"><strong>${emptyTitle}</strong><span>${emptyDescription}</span></div>`}</div>
    <footer class="skills-foot"><i aria-hidden="true"></i><span>点卡片右上角「使用技能」，在弹窗里点「确认」就送进对话框；分类与定位取自 skills/Skill分类说明.md。</span></footer>
  </section>`;
}

function syncNavActive(view) {
  // CSS 的首帧高亮看的是 <html data-nav>，切视图时必须同步，否则会残留上一项的胶囊
  document.documentElement.dataset.nav = view;
  document.querySelectorAll(".primary-nav [data-view], .sidebar-foot [data-view]")
    .forEach((button) => button.classList.toggle("is-active", button.dataset.view === view));
}

async function renderCurrentView() {
  if (!state.snapshot?.workflows) return;
  // 离开首页就把内嵌问卷卸掉，别让它的键盘监听跟着整页换内容一直挂着
  if (state.currentView !== "home") destroyHomeSurvey();
  const meta = VIEW_META[state.currentView] || VIEW_META.home;
  elements.pageKicker.textContent = meta[0];
  elements.pageTitle.textContent = meta[1];
  elements.pageSubtitle.textContent = meta[2];
  // 页头右上统一显示快照更新时间
  elements.snapshotTime.textContent = `更新于 ${formatDate(state.snapshot.generated_at, true)}`;
  // 首页自带「今日横幅」，页面级标题区在这里是重复信息，直接收掉。
  document.querySelector(".page-heading")?.classList.toggle("is-hidden", state.currentView === "home");
  syncNavActive(state.currentView);

  const renderers = {
    home: renderHome,
    "benchmark-accounts-v2": renderBenchmarkAccountsV2,
    "benchmark-contents": renderBenchmarkContents,
    patterns: renderPatterns,
    "media-plan": renderPlanningView,
    "own-accounts": renderOwnAccounts,
    "topic-search": renderTopicSearch,
    topics: renderTopics,
    "creation-board": renderContentProduction,
    publications: renderPublications,
    skills: renderSkills,
    settings: renderSettings,
  };
  await renderers[state.currentView]?.();
  unifyPageHeading();
}

// 页头统一：把内容区顶部的「计数 + 刷新」并到页头右侧。
// 有 5 个视图原来各画了一套标题（黑图标块 + 大标题 + 副标题），跟别的页面对不上；
// 现在一律只用共享页头，内容里那套图标与标题由 CSS 收掉，只剩这两个操作。
function unifyPageHeading() {
  const row = document.querySelector(".page-heading-row");
  if (!row) return;
  row.querySelectorAll("[data-heading-tools]").forEach((el) => el.remove());
  const main = elements.workspaceMain;
  if (!main) return;
  const tools = main.querySelector(".list-card-side") || main.querySelector(".skills-count");
  if (tools) {
    tools.setAttribute("data-heading-tools", "");
    // 数量统一成一句纯文字：2个账号 / 5个账号 / 34条内容
    const count = tools.querySelector(".head-count");
    const num = count?.querySelector("b");
    const unit = count?.querySelector("i");
    if (num && unit) {
      const noun = unit.textContent.trim() === "条" ? "条内容" : unit.textContent.trim();
      count.textContent = num.textContent.trim() + noun;
    }
    row.appendChild(tools);
  }
  const head = main.querySelector(".list-card-head");
  if (head) head.style.display = "none";
}

function updateCounts() {
  const workflows = state.snapshot.workflows;
  const counts = {
    "benchmark-accounts-v2": workflows.insight.benchmark_accounts.length,
    "benchmark-contents": workflows.insight.benchmark_contents.length,
    patterns: workflows.patterns?.dictionary?.total || 0,
    "own-accounts": workflows.self.own_accounts.length,
    topics: workflows.production.topics.length,
    production: workflows.production.own_contents.length,
    publications: workflows.production.publications.length,
    skills: workflows.skills.length,
  };
  document.querySelectorAll("[data-count]").forEach((node) => {
    node.textContent = String(counts[node.dataset.count] ?? 0);
  });
}

function renderDiagnostics() {
  const diagnostics = state.snapshot.diagnostics || [];
  if (!diagnostics.length) {
    elements.diagnosticNotice.classList.add("is-hidden");
    return;
  }
  elements.diagnosticNotice.textContent = `有 ${diagnostics.length} 项资料未能完整读取，其他数据不受影响。`;
  elements.diagnosticNotice.classList.remove("is-hidden");
}

async function fetchFile(path) {
  const response = await fetch(`/api/file?path=${encodeURIComponent(path)}`, { cache: "no-store" });
  if (!response.ok) throw new Error(`file_read_failed:${response.status}`);
  return response.json();
}

async function openDocument(path, title = "") {
  openDetail("DOCUMENT", title || path, '<div class="loading-state"><span></span><p>正在读取文档</p></div>');
  try {
    const file = await fetchFile(path);
    elements.detailTitle.textContent = file.name;
    if (file.extension === "md" || file.extension === "txt") {
      elements.detailBody.innerHTML = `<article class="markdown-body">${renderMarkdown(file.content, { documentPath: file.path || path })}</article>`;
    } else {
      elements.detailBody.innerHTML = `<pre>${escapeHtml(file.content)}</pre>`;
    }
  } catch (error) {
    elements.detailBody.innerHTML = emptyState("读取失败", error.message);
  }
}

function openDetail(kicker, title, body, { centered = false, variant = "" } = {}) {
  state.detailRevision += 1;
  elements.detailPanel.classList.toggle("is-centered", centered);
  elements.detailPanel.classList.toggle("is-account-notes", variant === "account-notes");
  elements.detailKicker.textContent = kicker;
  elements.detailTitle.textContent = title;
  elements.detailBody.innerHTML = body;
  if (!elements.detailPanel.open) elements.detailPanel.showModal();
}

function showImagePreview(path, label) {
  if (!path) return;
  elements.imagePreviewTitle.textContent = label || "图片预览";
  elements.imagePreviewImage.src = rawFileUrl(path);
  elements.imagePreviewImage.alt = label || "图片预览";
  if (!elements.imagePreviewPanel.open) elements.imagePreviewPanel.showModal();
}

function accountAvatar(item, className = "account-avatar") {
  if (item.avatar_path) return `<img class="${className}" src="${rawFileUrl(item.avatar_path)}" alt="${escapeAttribute(item.name)}头像" loading="lazy">`;
  return `<span class="${className} account-avatar-default" aria-label="${escapeAttribute(item.name)}默认头像"><svg aria-hidden="true" viewBox="0 0 24 24"><circle cx="12" cy="8" r="3.5"/><path d="M5.5 20c.9-4 3.1-6 6.5-6s5.6 2 6.5 6"/></svg></span>`;
}

function accountListIdentity(item, kind, returnTo = "") {
  return `<div class="account-cell">${accountAvatar(item)}<div class="account-copy"><a class="row-button" href="${accountUrl(kind, item.id, returnTo)}">${escapeHtml(item.name)}</a><div class="cell-meta">${escapeHtml(item.id)}</div></div></div>`;
}

function imageGallery(images = [], name) {
  if (!images.length) return accountNoteEmpty("暂无图片说明", "补入主页、商品或承接截图后，会在这里集中查看。");
  return `<div class="image-gallery">${images.map((image, index) => `<button type="button" data-action="preview-image" data-path="${escapeAttribute(image)}" data-label="${escapeAttribute(`${name}资料图 ${index + 1}`)}"><img src="${rawFileUrl(image)}" alt="${escapeAttribute(name)}资料图 ${index + 1}"></button>`).join("")}</div>`;
}

function accountNoteIcon(kind) {
  if (kind === "remark") return '<svg aria-hidden="true" viewBox="0 0 24 24"><path d="M5 4h14v12H9l-4 4z"/><path d="M9 8h6m-6 4h4"/></svg>';
  if (kind === "prior") return '<svg aria-hidden="true" viewBox="0 0 24 24"><path d="M6 3h9l3 3v15H6z"/><path d="M15 3v4h4M9 12h6M9 16h5"/></svg>';
  return '<svg aria-hidden="true" viewBox="0 0 24 24"><rect x="4" y="5" width="16" height="14" rx="2"/><circle cx="9" cy="10" r="1.5"/><path d="m6 17 4-4 3 3 2-2 3 3"/></svg>';
}

function accountNoteEmpty(title, detail) {
  return `<div class="account-note-empty"><span aria-hidden="true">—</span><div><strong>${escapeHtml(title)}</strong><p>${escapeHtml(detail)}</p></div></div>`;
}

function accountNoteSection({ kind, title, meta, content, empty = false }) {
  return `<section class="account-note-card account-note-card--${escapeAttribute(kind)}${empty ? " is-empty" : ""}"><header><span class="account-note-icon">${accountNoteIcon(kind)}</span><div><h3>${escapeHtml(title)}</h3><small>${escapeHtml(meta)}</small></div></header><div class="account-note-card-body">${content}</div></section>`;
}

async function showAccountNotes(kind, id) {
  const accounts = kind === "benchmark"
    ? state.snapshot.workflows.insight.benchmark_accounts
    : state.snapshot.workflows.self.own_accounts;
  const item = accounts.find((account) => account.id === id);
  if (!item) return;
  const notes = item.notes
    ? `<article class="notes-copy note-prose">${renderAccountNoteMarkdown(item.notes)}</article>`
    : accountNoteEmpty("暂无人工备注", "可在账号主表的“备注”字段补充当前判断。");
  const priorPath = item.documents?.prior?.path || "";
  const prior = priorPath
    ? '<div class="inline-note-loading" data-prior-content><span></span><p>正在读取人工先验说明</p></div>'
    : accountNoteEmpty("暂无人工先验说明", "建立人工先验说明.md 后，系统会自动在此展示正文。");
  const body = `<div class="account-note-stack">${accountNoteSection({ kind: "remark", title: "人工备注", meta: "账号主表补充", content: notes, empty: !item.notes })}${accountNoteSection({ kind: "prior", title: "人工先验说明", meta: priorPath ? "正式资料" : "尚未建档", content: prior, empty: !priorPath })}${accountNoteSection({ kind: "images", title: "图片说明", meta: item.images.length ? `${item.images.length} 项资料` : "尚未建档", content: imageGallery(item.images, item.name), empty: !item.images.length })}</div>`;
  openDetail("账号备注", `${item.name} · 备注资料`, body, { centered: true, variant: "account-notes" });
  const revision = state.detailRevision;
  if (!priorPath) return;
  try {
    const file = await fetchFile(priorPath);
    if (revision !== state.detailRevision) return;
    const target = elements.detailBody.querySelector("[data-prior-content]");
    if (target) target.outerHTML = `<article class="notes-copy note-prose prior-copy">${renderAccountNoteMarkdown(file.content)}</article>`;
  } catch (error) {
    if (revision !== state.detailRevision) return;
    const target = elements.detailBody.querySelector("[data-prior-content]");
    if (target) target.outerHTML = `<p class="muted">人工先验说明读取失败：${escapeHtml(error.message)}</p>`;
  }
}

function navigate(view) {
  if (!VIEW_META[view]) view = "home";
  const movedToHome = view === "home" && state.currentView !== "home";
  state.currentView = view;
  if (movedToHome) homeEnterQueued = true;
  // 主题层（theme-aurora.css）靠 body[data-view] 限定作用范围，切视图必须同步
  document.body.dataset.view = view;
  state.listQuery = "";
  location.hash = view === "home" ? "" : `#${view}`;
  document.body.classList.remove("nav-open");
  renderCurrentView();
  // 首页那几块的「查看更多」是从半路点进来的，切页必须把视口带回顶部
  window.scrollTo({ top: 0 });
  requestAnimationFrame(() => window.scrollTo({ top: 0 }));
}

function showToast(message) {
  clearTimeout(state.toastTimer);
  elements.toast.textContent = message;
  elements.toast.classList.remove("is-hidden");
  state.toastTimer = setTimeout(() => elements.toast.classList.add("is-hidden"), 2400);
}

function openExternal(value) {
  try {
    const url = new URL(value);
    if (!["http:", "https:"].includes(url.protocol)) throw new Error("invalid_protocol");
    window.open(url.href, "_blank", "noopener,noreferrer");
  } catch (_error) {
    showToast("链接格式无效");
  }
}

async function loadSnapshot({ quiet = false, force = false } = {}) {
  if (!quiet) elements.reloadSnapshot.disabled = true;
  try {
    const response = await fetch(force ? "/api/refresh" : "/api/snapshot", { cache: "no-store" });
    if (!response.ok) throw new Error(`snapshot_failed:${response.status}`);
    const snapshot = await response.json();
    if (!snapshot?.workflows) throw new Error("snapshot_schema_unsupported");
    state.snapshot = snapshot;
    elements.workspaceVersion.textContent = workspaceVersionText(snapshot.workspace);
    elements.snapshotTime.textContent = `更新于 ${formatDate(snapshot.generated_at, true)}`;
    updateCounts();
    renderDiagnostics();
    await renderCurrentView();
    return true;
  } catch (error) {
    if (state.snapshot) {
      showToast("资料刷新失败，当前仍显示上一次结果");
      return false;
    }
    const detail = location.protocol === "file:" ? "请通过 /dashboard 打开工作台" : error.message;
    elements.workspaceMain.innerHTML = emptyState("无法读取本地资料", detail);
  } finally {
    elements.reloadSnapshot.disabled = false;
  }
}

/* 自动刷新要先去抖，问卷在填的时候干脆不刷。
   服务端在工作区文件变动时推 snapshot.updated，AI 跑流程时会连着写文件，事件很密；
   每来一条就整页重建，用户点一下正好撞上重建，看到的就是「卡一下」。
   填问卷时直接跳过：那会儿根本不需要看后台数据，重建只会把光标和勾选弄没。
   交卷后的刷新不靠这里 —— 首页问卷在 mountHomeSurvey 的 onSubmitted 里自己拉，
   抽屉在提交后重画当前视图。 */
let snapshotRefreshTimer = null;

function surveyEditing() {
  if (document.querySelector("#home-survey")) return true;
  return Boolean(surveyDrawer?.open);
}

function scheduleSnapshotRefresh(delay = 450) {
  if (snapshotRefreshTimer) {
    window.clearTimeout(snapshotRefreshTimer);
    snapshotRefreshTimer = null;
  }
  // 正在问卷里：这次更新直接丢掉，等问卷关了之后的下一次事件再说
  if (surveyEditing()) return;
  snapshotRefreshTimer = window.setTimeout(function () {
    snapshotRefreshTimer = null;
    if (surveyEditing()) return;
    loadSnapshot({ quiet: true });
  }, delay);
}

function connectEvents() {
  state.eventSource?.close();
  state.eventsPaused = false;
  const source = new EventSource("/api/events");
  state.eventSource = source;
  source.addEventListener("ready", async (event) => {
    let serverRevision = null;
    try {
      serverRevision = Number(JSON.parse(event.data).revision);
    } catch (_error) {
      // A malformed reconnect notice should trigger a safe full reload.
    }
    if (!state.snapshot || !Number.isSafeInteger(serverRevision) || serverRevision !== state.snapshot.revision) {
      await loadSnapshot({ quiet: true });
    }
  });
  source.addEventListener("snapshot.updated", () => {
    scheduleSnapshotRefresh();
  });
  source.addEventListener("snapshot.failed", () => {
    showToast("资料自动刷新失败，当前仍显示上一次结果");
  });
  // 服务端连接位已满（同时开着的工作台标签页太多）：不再重连，
  // 退成「右上角刷新按钮手动刷新」，把浏览器的连接位留给页面本身。
  source.addEventListener("busy", () => {
    source.close();
    state.eventSource = null;
    state.eventsDisabled = true;
  });
  source.onerror = () => {
    // 页面进后台时是我们自己断的，不当作断线提示。
    if (state.eventsPaused) return;
  };
}

/* 事件流是长期连接，而浏览器对同一个地址最多只让连 6 条。
   页面进后台就主动断开、把连接位让出来；回到前台再连，
   重连时 ready 会比对版本并自动补拉最新资料。 */
function bindEventChannelLifecycle() {
  document.addEventListener("visibilitychange", () => {
    if (document.hidden) {
      if (!state.eventSource) return;
      state.eventsPaused = true;
      state.eventSource.close();
      state.eventSource = null;
      return;
    }
    if (!state.eventSource && !state.eventsDisabled) connectEvents();
  });
  window.addEventListener("pagehide", () => {
    state.eventsPaused = true;
    state.eventSource?.close();
    state.eventSource = null;
  });
  // 后台打开的标签页先不占连接，等切到前台再连。
  if (!document.hidden) connectEvents();
}

document.addEventListener("click", (event) => {
  // 只认侧边栏里的导航项：body 上也有 data-view（主题层用），
  // 不加限定的话页面内任何点击都会被 closest 命中 body，误判成切页。
  const navButton = event.target.closest(".primary-nav [data-view], .sidebar-foot [data-view]");
  if (navButton) {
    navigate(navButton.dataset.view);
    return;
  }
  const targetButton = event.target.closest("[data-view-target]");
  if (targetButton) {
    navigate(targetButton.dataset.viewTarget);
    return;
  }
  const action = event.target.closest("[data-action]");
  // 形式 / 目标胶囊（新增脚本 / 选题生产弹窗里那两组），选中即重算指令
  const productionChip = event.target.closest("[data-media-form], [data-goal]");
  if (productionChip) {
    event.preventDefault();
    const box = productionChip.parentElement;
    const isGoal = Boolean(productionChip.dataset.goal);
    const value = isGoal ? productionChip.dataset.goal : productionChip.dataset.mediaForm;
    paintProductionSettings(box, isGoal ? "goal" : "media-form", value);
    if (box.id.startsWith("tsa-")) {
      if (topicSearchActionContext) {
        topicSearchActionContext[isGoal ? "goal" : "mediaForm"] = value;
        paintTopicSearchAction(document.querySelector("#topic-search-action-dialog"));
      }
    } else {
      promptState[isGoal ? "goal" : "mediaForm"] = value;
      syncPrompt();
    }
    return;
  }
  if (!action) return;
  if (action.dataset.action === "open-file") openDocument(action.dataset.path);
  else if (action.dataset.action === "open-raw") window.open(rawFileUrl(action.dataset.path), "_blank", "noopener,noreferrer");
  else if (action.dataset.action === "preview-image") showImagePreview(action.dataset.path, action.dataset.label);
  else if (action.dataset.action === "open-content") location.assign(contentUrl(action.dataset.kind, action.dataset.id, action.dataset.returnTo));
  // 内容按钮在没有 kind 时走降级动作（见 contentButton）—— 之前这两个动作**没有处理分支**，
  // 点了什么都不发生。这里补上，kind 由动作名反推。
  else if (action.dataset.action === "open-own-content") location.assign(contentUrl("own", action.dataset.id, action.dataset.returnTo));
  else if (action.dataset.action === "open-external") openExternal(action.dataset.url);
  else if (action.dataset.action === "use-skill") openSkillDialog(action.dataset.id);
  else if (action.dataset.action === "start-topic-search") openTopicSearchStartDialog();
  else if (action.dataset.action === "open-search-topic") openSearchTopicDrawer(action.dataset.run || "", Number(action.dataset.rank));
  else if (action.dataset.action === "topic-search-produce") openTopicSearchActionDialog("produce", Number(action.dataset.rank), action.dataset.run || "");
  else if (action.dataset.action === "open-topic-drawer") openTopicDrawer(action.dataset.topicId);
  else if (action.dataset.action === "open-content-drawer") openContentDrawer("own", action.dataset.contentId);
  else if (action.dataset.action === "delete-content") openContentDeleteConfirm(action.dataset.contentId);
  else if (action.dataset.action === "open-benchmark-content-drawer") openContentDrawer("benchmark", action.dataset.key);
  else if (action.dataset.action === "delete-topic") openDeleteConfirm(action.dataset.topicId);
  else if (action.dataset.action === "discuss-direction") openDiscussDialog();
  else if (action.dataset.action === "direction-diagnosis") openDirectionDiagnosisDialog({ mode: "direction" });
  else if (action.dataset.action === "rediagnose-stale-accounts") {
    openDirectionDiagnosisDialog({ mode: "diagnose", preselect: staleDiagnosisAccounts() });
  }
  else if (action.dataset.action === "open-survey-drawer") openSurveyDrawer();
  else if (action.dataset.action === "focus-home-survey") focusHomeSurvey();
  else if (action.dataset.action === "focus-home-scripts") focusHomeScripts();
  else if (action.dataset.action === "close-onboarding") {
    // 全部完成后底栏那颗按钮：和右上角 × 一个动作
    const card = action.closest("#onb");
    card?.classList.add("closing");
    window.setTimeout(() => card?.remove(), 380);
  }
  else if (action.dataset.action === "dismiss-baseline-notice") dismissBaselineNotice(action);
  else if (action.dataset.action === "add-own-account") openAccountDialog();
  /* 账号洞察 → 当前账号的详细诊断：进账号详情页，停在页面最上方，
     不再锚到「账号洞察」那一段（从首页看的就是那一段，跳过去等于原地不动） */
  else if (action.dataset.action === "open-own-diagnosis") {
    const account = currentHomeAccount();
    if (account) location.href = accountUrl("own", account.id, "", "account-v2");
  }
  /* 脚本创作页头的「新增脚本」：三种生产方式（直接生产 / 根据选题 / 根据对标） */
  else if (action.dataset.action === "new-script") openPromptDialog("script");
  else if (action.dataset.action === "switch-script-mode") applyScriptMode(action.dataset.mode);
  else if (action.dataset.action === "pick-script-topic") pickScriptTopic(action.dataset.topicId);
  else if (action.dataset.action === "pick-script-bench") pickScriptBench(action.dataset.key);
  else if (action.dataset.action === "first-diagnose-account") openAccountDialog({ mode: "first" });
  else if (action.dataset.action === "redo-own-diagnosis") {
    // 按钮在整卡链接里，先拦住冒泡，避免点按钮时把卡片跳转也带走
    event.preventDefault();
    const account = (state.snapshot?.workflows?.self?.own_accounts || [])
      .find((item) => item.id === action.dataset.id);
    if (account) openRedoDialog(account, "own");
  }
  else if (action.dataset.action === "add-benchmark-account") openBenchmarkDialog();
  else if (action.dataset.action === "recommend-topics") openRecommendTopicsDialog();
  else if (action.dataset.action === "reanalyze-benchmark-account") {
    event.preventDefault();
    const account = (state.snapshot?.workflows?.insight?.benchmark_accounts || [])
      .find((item) => item.id === action.dataset.id);
    if (account) openRedoDialog(account, "benchmark");
  }
  else if (action.dataset.action === "add-benchmark-content") openContentDialog();
  else if (action.dataset.action === "open-recommend-topic") openRecommendTopicDrawer(Number(action.dataset.rank));
  else if (action.dataset.action === "recommend-topic-produce") openRecommendTopicProduceDialog(Number(action.dataset.rank));
  else if (action.dataset.action === "new-topic") openPromptDialog("new");
  else if (action.dataset.action === "reanalyze-topic") openPromptDialog("reanalyze", currentTopic(action.dataset.topicId));
  else if (action.dataset.action === "produce-topic") openPromptDialog("produce", currentTopic(action.dataset.topicId));
  else if (action.dataset.action === "filter-skill-category") {
    state.skillCategory = action.dataset.value || "all";
    renderSkills();
  }
  else if (action.dataset.action === "filter-topic") {
    state.topicFilters.judgment = action.dataset.value === "all" ? "" : action.dataset.value;
    renderTopics();
  }
  else if (action.dataset.action === "filter-content-stage") {
    state.contentStage = action.dataset.value === "all" ? "" : action.dataset.value;
    renderCurrentView();
  }
  else if (action.dataset.action === "select-pattern-tab") {
    state.patternTab = action.dataset.section;
    state.patternLevel = "all";
    renderCurrentView();
  }
  else if (action.dataset.action === "select-pattern-level") {
    state.patternLevel = action.dataset.level || "all";
    if (state.patternReport) paintPatterns(patternReportView(state.patternReport.md, state.patternReport.path));
    else renderCurrentView();
  }
  else if (action.dataset.action === "select-pattern-dict-category") {
    state.patternDictCategory = action.dataset.category || "all";
    renderCurrentView();
  }
  else if (action.dataset.action === "open-pattern-refresh") openPatternRefresh();
  else if (action.dataset.action === "open-dict-add") openDictionaryAdd();
  else if (action.dataset.action === "toggle-dict-menu") {
    const menu = action.closest(".pt-dstatus")?.querySelector(".pt-menu");
    if (!menu) return;
    document.querySelectorAll(".pt-menu").forEach((item) => {
      if (item !== menu) item.hidden = true;
    });
    menu.hidden = !menu.hidden;
    action.setAttribute("aria-expanded", String(!menu.hidden));
  }
  else if (action.dataset.action === "set-dict-status") setDictionaryStatus(action.dataset.id, action.dataset.next);
  else if (action.dataset.action === "delete-dict") openDictionaryDelete(action.dataset.id, action.dataset.label);
  else if (action.dataset.action === "show-account-notes") showAccountNotes(action.dataset.kind, action.dataset.id);
});

// 点卡片状态菜单以外的地方收起菜单
document.addEventListener("click", (event) => {
  if (event.target.closest(".pt-dstatus")) return;
  document.querySelectorAll(".pt-menu").forEach((menu) => {
    menu.hidden = true;
  });
});

document.addEventListener("input", (event) => {
  if (event.target.matches("[data-list-filter]")) {
    state.listQuery = event.target.value;
    const selectionStart = event.target.selectionStart;
    renderCurrentView().then(() => {
      const input = document.querySelector("[data-list-filter]");
      input?.focus();
      input?.setSelectionRange(selectionStart, selectionStart);
    });
  }
});

document.addEventListener("change", (event) => {
  if (event.target.matches("[data-benchmark-account-filter]")) {
    state.benchmarkAccountFilters[event.target.dataset.benchmarkAccountFilter] = event.target.value;
    renderCurrentView();
    return;
  }
  if (event.target.matches("[data-topic-filter]")) {
    state.topicFilters[event.target.dataset.topicFilter] = event.target.value;
    renderCurrentView();
  }
});

elements.reloadSnapshot.addEventListener("click", () => loadSnapshot({ force: true }));
elements.menuButton.addEventListener("click", () => document.body.classList.add("nav-open"));
elements.sidebarScrim.addEventListener("click", () => document.body.classList.remove("nav-open"));
elements.closeDetail.addEventListener("click", () => elements.detailPanel.close());
elements.closeImagePreview.addEventListener("click", () => elements.imagePreviewPanel.close());
elements.closeTopicDrawer.addEventListener("click", closeTopicDrawer);
elements.topicConfirmCancel.addEventListener("click", () => {
  pendingDeleteId = "";
  pendingContentDeleteId = "";
  pendingDictDeleteId = "";
  elements.topicConfirm.close();
});
elements.topicConfirmOk.addEventListener("click", () => {
  if (pendingContentDeleteId) confirmDeleteContent();
  else if (pendingDictDeleteId) confirmDeleteDictionary();
  else confirmDeleteTopic();
});
elements.promptCancel.addEventListener("click", () => elements.promptDialog.close());
elements.promptIdea.addEventListener("input", () => {
  if (elements.promptIdea.value.trim()) setFieldError(elements.promptFieldIdea, elements.ideaError, "");
});
elements.promptCopy.addEventListener("click", copyPromptText);
document.querySelector('[data-tpl="close"]')?.addEventListener("click", () => elements.promptDialog.close());
elements.promptIdea.addEventListener("input", () => {
  syncPrompt();
  if (elements.promptIdea.value.trim()) {
    elements.promptFieldIdea.classList.remove("is-err");
    setInlineError(elements.ideaError, "");
  }
});
elements.promptRef.addEventListener("input", () => {
  syncPrompt();
});
// 新增脚本：列表一直铺在面板里，搜索框边打边筛
document.querySelector("#tpl-topic-search")?.addEventListener("input", (event) => renderScriptTopicList(event.target.value));
document.querySelector("#tpl-bench-search")?.addEventListener("input", (event) => renderScriptBenchList(event.target.value));
// tab 栏的键盘行为：左右切、Home/End 跳到两头（role=tablist 本来就该有）
document.querySelector("#tpl-script-tabs")?.addEventListener("keydown", (event) => {
  const step = { ArrowLeft: -1, ArrowRight: 1 }[event.key];
  const tabs = [...document.querySelectorAll("#tpl-script-tabs [data-mode]")];
  const at = tabs.indexOf(document.activeElement);
  let next = null;
  if (step && at >= 0) next = (at + step + tabs.length) % tabs.length;
  else if (event.key === "Home") next = 0;
  else if (event.key === "End") next = tabs.length - 1;
  if (next === null) return;
  event.preventDefault();
  applyScriptMode(tabs[next].dataset.mode, { focus: false });
  tabs[next].focus();
});
elements.promptExtra?.addEventListener("input", () => syncPrompt());
// 输入区滚动 / 窗口变化时，重新判断下沿那道渐隐要不要显示
document.querySelector("#topic-prompt-dialog .dd-body")?.addEventListener("scroll", updatePromptFade, { passive: true });
window.addEventListener("resize", updatePromptFade);
elements.refreshCancel.addEventListener("click", () => elements.refreshDialog.close());
document.querySelector('[data-rf="close"]')?.addEventListener("click", () => elements.refreshDialog.close());
document.querySelector('[data-dc="close"]')?.addEventListener("click", () => elements.dictDialog.close());
elements.refreshCopy.addEventListener("click", () => {
  copyPlainText(elements.refreshText.textContent || "", elements.refreshCopy, elements.refreshError, elements.refreshText);
});
elements.dictCancel.addEventListener("click", () => elements.dictDialog.close());
elements.dictCopy.addEventListener("click", () => {
  copyPlainText(elements.dictText.textContent || "", elements.dictCopy, null, elements.dictText);
});
// 点遮罩（dialog 自身）关闭；Esc 走 cancel 事件，先播完退场动画再真正 close
elements.topicDrawer.addEventListener("click", (event) => {
  if (event.target === elements.topicDrawer) closeTopicDrawer();
});
elements.topicDrawer.addEventListener("cancel", (event) => {
  event.preventDefault();
  closeTopicDrawer();
});
elements.closeTopicDrawer?.addEventListener("click", () => closeTopicDrawer());
// 推荐选题详情抽屉：同一套开关逻辑
elements.recommendTopicDrawer?.addEventListener("click", (event) => {
  if (event.target === elements.recommendTopicDrawer) closeRecommendTopicDrawer();
});
elements.recommendTopicDrawer?.addEventListener("cancel", (event) => {
  event.preventDefault();
  closeRecommendTopicDrawer();
});
elements.recommendTopicDrawerClose?.addEventListener("click", () => closeRecommendTopicDrawer());
// 搜索选题详情抽屉：同一套开关逻辑
elements.searchTopicDrawer?.addEventListener("click", (event) => {
  if (event.target === elements.searchTopicDrawer) closeSearchTopicDrawer();
});
elements.searchTopicDrawer?.addEventListener("cancel", (event) => {
  event.preventDefault();
  closeSearchTopicDrawer();
});
elements.closeSearchTopicDrawer?.addEventListener("click", () => closeSearchTopicDrawer());
/* 不管从哪条路关掉（按钮 / ESC / 点遮罩），底下的滚动锁定都要解开 */
elements.recommendTopicDrawer?.addEventListener("close", () => {
  document.body.classList.remove("is-drawer-open");
});
document.querySelector("#close-content-drawer")?.addEventListener("click", () => closeContentDrawer());
elements.contentDrawer.addEventListener("click", (event) => {
  if (event.target === elements.contentDrawer) closeContentDrawer();
});
elements.contentDrawer.addEventListener("cancel", (event) => {
  event.preventDefault();
  closeContentDrawer();
});

/* 抽屉里的页面要出指令时，弹窗开在这一层（居中、盖住整页）。
   iframe 里 showModal 只能盖住抽屉那一块，用户要的是页面居中的那种。 */
function openEmbedPromptDialog({ title = "", copy = "", text = "" } = {}) {
  const dialog = document.querySelector("#embed-prompt-dialog");
  if (!dialog) return;
  dialog.dataset.prompt = String(text || "");
  const titleNode = dialog.querySelector("#embed-prompt-title");
  const copyNode = dialog.querySelector("#embed-prompt-copy");
  const textNode = dialog.querySelector("#embed-prompt-text");
  if (titleNode) titleNode.textContent = title || "指令";
  if (copyNode) {
    copyNode.textContent = copy || "";
    copyNode.hidden = !copy;
  }
  if (textNode) textNode.textContent = text || "";
  if (!dialog.open) dialog.showModal();
}

document.querySelector("#embed-prompt-dialog")?.addEventListener("click", (event) => {
  const dialog = event.target.closest("#embed-prompt-dialog");
  const action = event.target.closest("[data-ep]");
  if (!action) {
    if (event.target === dialog) dialog.close();
    return;
  }
  if (action.dataset.ep === "close") {
    dialog.close();
    return;
  }
  if (action.dataset.ep === "copy") copyDialogPrompt(dialog, action);
});

/* 抽屉里的页面要确认一次写数据的动作（如「通过审查确认终稿」）：
   弹窗开在父窗口这一层，用户点确认或取消后把结果回传给 iframe，由它自己执行。 */
let embedConfirmSource = null;

function openEmbedConfirmDialog({ title = "", copy = "", confirmText = "确认" } = {}, source = null) {
  const dialog = document.querySelector("#embed-confirm-dialog");
  if (!dialog) return;
  embedConfirmSource = source;
  const titleNode = dialog.querySelector("#embed-confirm-title");
  const copyNode = dialog.querySelector("#embed-confirm-copy");
  const okNode = dialog.querySelector("#embed-confirm-ok");
  if (titleNode) titleNode.textContent = title || "确认";
  if (copyNode) copyNode.textContent = copy || "";
  if (okNode) okNode.textContent = confirmText || "确认";
  if (!dialog.open) dialog.showModal();
}

function closeEmbedConfirmDialog(confirmed) {
  const dialog = document.querySelector("#embed-confirm-dialog");
  if (dialog?.open) dialog.close();
  const source = embedConfirmSource;
  embedConfirmSource = null;
  source?.postMessage({ type: "meiling:confirm-dialog-result", confirmed }, location.origin);
}

document.querySelector("#embed-confirm-dialog")?.addEventListener("click", (event) => {
  const action = event.target.closest("[data-ec]");
  if (!action) {
    if (event.target.id === "embed-confirm-dialog") closeEmbedConfirmDialog(false);
    return;
  }
  closeEmbedConfirmDialog(action.dataset.ec === "ok");
});

document.querySelector("#embed-confirm-dialog")?.addEventListener("cancel", (event) => {
  event.preventDefault();
  closeEmbedConfirmDialog(false);
});

// 抽屉里的详情页点「返回上一页」时，让它通知关抽屉，而不是在 iframe 里跳去列表页
window.addEventListener("message", (event) => {
  if (event.origin !== location.origin) return;
  if (event.data?.type === "meiling:close-content-drawer") closeContentDrawer();
  // 抽屉里点「来源 · 选题」：iframe 没法开选题抽屉，交给父窗口开
  if (event.data?.type === "meiling:open-topic-drawer") {
    closeContentDrawer();
    openTopicDrawer(String(event.data.topicId || ""));
  }
  // 抽屉里的页面要出指令弹窗：交给父窗口，才能页面居中
  if (event.data?.type === "meiling:prompt-dialog") {
    openEmbedPromptDialog(event.data.payload || {});
  }
  // 抽屉里的页面要确认一次写数据的动作：同样交给父窗口，结果再回传给 iframe
  if (event.data?.type === "meiling:confirm-dialog") {
    openEmbedConfirmDialog(event.data.payload || {}, event.source);
  }
});
elements.detailPanel.addEventListener("click", (event) => {
  if (event.target === elements.detailPanel) elements.detailPanel.close();
});
elements.imagePreviewPanel.addEventListener("click", (event) => {
  if (event.target === elements.imagePreviewPanel) elements.imagePreviewPanel.close();
});

window.addEventListener("hashchange", () => {
  const view = location.hash.slice(1) || "home";
  if (view !== state.currentView) navigate(view);
});

state.currentView = VIEW_META[location.hash.slice(1)] ? location.hash.slice(1) : "home";
document.body.dataset.view = state.currentView;
// 先点亮当前 tab，再等快照；否则会先空着、资料回来才亮，切页看起来就是闪一下
syncNavActive(state.currentView);
loadSnapshot();
bindEventChannelLifecycle();
initCardReorder();

// 卡头里的刷新按钮复用顶栏那颗，逻辑只有一份
document.addEventListener("click", (event) => {
  const trigger = event.target.closest('[data-action="refresh-snapshot"]');
  if (!trigger) return;
  event.preventDefault();
  elements.reloadSnapshot.click();
});

// 设置页：把输入框里的值保存到 本地配置.txt
document.addEventListener("click", async (event) => {
  const trigger = event.target.closest('[data-action="save-local-config"]');
  if (!trigger) return;
  event.preventDefault();

  const field = trigger.dataset.field;
  if (!field) return;
  const input = document.querySelector(`[data-config-field="${field}"]`);
  const value = (input?.dataset.realValue ?? "").trim();

  trigger.disabled = true;
  try {
    await updateLocalConfig(field, value);
    showToast(value ? "已保存" : "已清空并保存");
    if (state.currentView === "settings") await renderSettings();
  } catch (_error) {
    showToast("保存失败，请确认工作区里的 本地配置.txt 可以写入");
  } finally {
    trigger.disabled = false;
  }
});

// 设置页：取数方式单选，点一下即保存
document.addEventListener("click", async (event) => {
  const trigger = event.target.closest('[data-action="save-config-value"]');
  if (!trigger) return;
  event.preventDefault();
  const field = trigger.dataset.field;
  const value = trigger.dataset.value || "";
  if (!field || trigger.disabled) return;
  trigger.disabled = true;
  try {
    await updateLocalConfig(field, value);
    showToast("已保存");
    if (state.currentView === "settings") await renderSettings();
  } catch (_error) {
    showToast("保存失败，请确认工作区里的 本地配置.txt 可以写入");
  } finally {
    trigger.disabled = false;
  }
});

// 设置页：密钥默认中间打码，点眼睛或点输入框才展开
document.addEventListener("click", (event) => {
  const eye = event.target.closest('[data-action="toggle-config-visibility"]');
  if (eye) {
    event.preventDefault();
    const input = document.querySelector(`[data-config-field="${eye.dataset.field}"]`);
    if (input) setConfigVisibility(input, input.dataset.masked === "1");
    return;
  }

  const input = event.target.closest("[data-config-field]");
  if (input && input.dataset.masked === "1") {
    setConfigVisibility(input, true);
    input.focus();
  }
});

document.addEventListener("input", (event) => {
  const input = event.target.closest?.("[data-config-field]");
  if (!input || input.dataset.masked === "1") return;
  input.dataset.realValue = input.value;
});


/* ================================================================
   卡片拖拽重排（对标账号）
   · 拖起来时原位置留一块占位，其余卡片用 FLIP 平滑让位
   · 松手吸附回网格，顺序写进 localStorage，下次进来还是这个排法
   ================================================================ */

/* ================================================================
   设置：本机接口配置
   · 直接读写工作区根目录的 本地配置.txt（Python 执行器读的是同一份）
   · 密钥只存在本机，不回传到浏览器以外的任何地方
   ================================================================ */

const DATA_SOURCE_OPTIONS = [
  {
    value: "",
    label: "自动",
    meta: "两种都配了走本机 Chrome",
  },
  {
    value: "chrome_devtools",
    label: "本机 Chrome",
    meta: "免费，失败即停",
  },
  {
    value: "justoneapi",
    label: "JustOneAPI",
    meta: "按接口计费",
  },
];

const LOCAL_CONFIG_SECTIONS = [
  {
    field: "volcengine_speech_api_key",
    title: "火山引擎语音转写",
    inputLabel: "火山引擎语音转写 API Key",
    placeholder: "填入火山引擎语音服务的 API Key",
    hint: "把视频、录音转成文字稿。",
    configuredHint: "已填自己的火山 Key，视频转写走你自己的账号。",
    unconfiguredHint: "还没填火山 Key，视频转写这一步做不了。在「本地配置.txt」里填 VOLCENGINE_SPEECH_API_KEY 就能用。",
    guide: "https://tanxiao.com.cn/docs/asr/",
    guideLabel: "查看配置指南",
  },
  {
    field: "justoneapi_token",
    title: "JustOneAPI",
    inputLabel: "JustOneAPI Token",
    placeholder: "填入 JustOneAPI Token",
    hint: "批量抓取抖音、小红书、B 站的公开账号和内容数据。没配置时只能用本机 Chrome 取数。",
    guide: "https://tanxiao.com.cn/docs/data-api/",
    guideLabel: "查看配置指南",
  },
];

async function fetchLocalConfig() {
  const response = await fetch("/api/local-config", { cache: "no-store" });
  if (!response.ok) throw new Error(`local_config_failed:${response.status}`);
  return response.json();
}

async function updateLocalConfig(field, value) {
  const response = await fetch("/api/local-config", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ [field]: value }),
  });
  if (!response.ok) throw new Error(`local_config_failed:${response.status}`);
  return response.json();
}

function maskConfigValue(value) {
  if (!value) return "";
  if (value.length <= 10) return "****";
  return `${value.slice(0, 4)}****${value.slice(-4)}`;
}

function setConfigVisibility(input, reveal) {
  const button = document.querySelector(`[data-action="toggle-config-visibility"][data-field="${input.dataset.configField}"]`);
  const realValue = input.dataset.realValue || "";
  input.dataset.masked = reveal ? "0" : "1";
  input.readOnly = !reveal && Boolean(realValue);
  input.value = reveal ? realValue : maskConfigValue(realValue);
  const label = button?.querySelector("span");
  if (label) label.textContent = reveal ? "隐藏" : "显示";
  if (button) {
    button.setAttribute("aria-pressed", String(reveal));
    button.setAttribute("aria-label", `${reveal ? "隐藏" : "显示"}${input.dataset.configLabel || "密钥"}`);
  }
}

function localConfigItem(section, value) {
  const masked = maskConfigValue(value);
  const hasValue = Boolean(value);
  const hint = hasValue ? (section.configuredHint || section.hint) : (section.unconfiguredHint || section.hint);
  return `<div class="settings-item">
    <div class="settings-item-label">
      <span>${escapeHtml(section.title)}</span>
      <span class="settings-tag${hasValue ? "" : " is-off"}">${hasValue ? "已配置" : "未配置"}</span>
    </div>
    <div class="settings-item-ctrl">
      <input class="settings-input settings-input-secret" id="config-field-${section.field}" type="text" data-config-field="${section.field}"
        data-real-value="${escapeAttribute(value || "")}" data-config-label="${escapeAttribute(section.inputLabel)}"
        data-masked="${hasValue ? "1" : "0"}" value="${escapeAttribute(masked)}"
        placeholder="${escapeAttribute(section.placeholder)}" autocomplete="off" spellcheck="false"
        ${hasValue ? "readonly" : ""}>
      <button class="settings-btn settings-btn-icon" type="button" data-action="toggle-config-visibility" data-field="${section.field}"
        aria-pressed="false" aria-label="显示${escapeAttribute(section.inputLabel)}">
        <svg aria-hidden="true" viewBox="0 0 24 24"><path d="M2.5 12s3.5-6.5 9.5-6.5S21.5 12 21.5 12 18 18.5 12 18.5 2.5 12 2.5 12Z"/><circle cx="12" cy="12" r="3.2"/><path class="settings-slash" d="m4 4 16 16"/></svg>
      </button>
      <button class="settings-btn settings-btn-primary" type="button" data-action="save-local-config" data-field="${section.field}">保存</button>
    </div>
    <p class="settings-item-hint">${escapeHtml(hint)}</p>
    <a class="settings-item-link" href="${escapeAttribute(section.guide)}" target="_blank" rel="noreferrer">${escapeHtml(section.guideLabel)}</a>
  </div>`;
}

const ACCOUNT_SCOPE_GROUPS = [
  {
    title: "对标账号",
    description: "设置一次，之后每次分析对标账号都按这里执行，不中途询问范围。",
    fields: [
      {
        field: "benchmark_account_base_limit",
        title: "基础内容获取上限",
        placeholder: "200",
        min: 1,
        max: 2000,
        hint: "获取账号内容列表时最多保留多少条，默认 200 条。达到上限会停止继续翻页或滚动，并在结果里注明可能未抓全。",
      },
      {
        field: "benchmark_account_detail_limit",
        title: "详细分析内容范围",
        placeholder: "20",
        min: 1,
        max: 500,
        hint: "详细数据和分析默认使用最近多少条内容，默认 20 条。用户当次明确要求其他范围时，以当次要求为准。",
      },
    ],
  },
  {
    title: "我的账号",
    description: "设置一次，之后每次诊断我的账号都按这里执行，不中途询问范围。",
    fields: [
      {
        field: "self_account_base_limit",
        title: "基础内容获取上限",
        placeholder: "200",
        min: 1,
        max: 2000,
        hint: "获取我的账号内容列表时最多保留多少条，默认 200 条。达到上限会停止继续翻页或滚动，并在结果里注明可能未抓全。",
      },
      {
        field: "self_account_detail_limit",
        title: "详细分析内容范围",
        placeholder: "20",
        min: 1,
        max: 500,
        hint: "我的账号详细数据和分析默认使用最近多少条内容，默认 20 条。用户当次明确要求其他范围时，以当次要求为准。",
      },
    ],
  },
];

function accountScopeItem(item, values) {
  const value = values[item.field] || item.placeholder;
  return `<div class="settings-item">
    <div class="settings-item-label">${escapeHtml(item.title)}</div>
    <div class="settings-item-ctrl">
      <input class="settings-input settings-input-num" id="config-field-${item.field}" type="number" inputmode="numeric"
        min="${item.min}" max="${item.max}" step="1"
        data-config-field="${item.field}" data-real-value="${escapeAttribute(value)}"
        value="${escapeAttribute(value)}" placeholder="${escapeAttribute(item.placeholder)}"
        aria-label="${escapeAttribute(item.title)}" autocomplete="off">
      <span class="settings-unit">条</span>
      <button class="settings-btn settings-btn-primary" type="button" data-action="save-local-config" data-field="${item.field}">保存</button>
    </div>
    <p class="settings-item-hint">${escapeHtml(item.hint)}</p>
  </div>`;
}

function accountScopePanel(values) {
  const groups = ACCOUNT_SCOPE_GROUPS.map((group) => `<div class="settings-group">
    <div class="settings-group-head">
      <h3>${escapeHtml(group.title)}</h3>
      <p>${escapeHtml(group.description)}</p>
    </div>
    ${group.fields.map((item) => accountScopeItem(item, values)).join("")}
  </div>`).join("");
  return `<section class="settings-panel">
    <div class="settings-panel-head">
      <h2>分析范围</h2>
      <p>设置一次，之后每次分析账号都按这里执行，不再中途询问范围。</p>
    </div>
    ${groups}
  </section>`;
}

function dataSourcePanel(value, justoneapiConfigured) {
  const options = DATA_SOURCE_OPTIONS.map((option) => {
    const disabled = option.value === "justoneapi" && !justoneapiConfigured;
    const active = (value || "") === option.value;
    return `<button class="settings-radio-row${active ? " is-on" : ""}" type="button" role="radio"
      aria-checked="${active}" data-action="save-config-value" data-field="data_source_preference"
      data-value="${escapeAttribute(option.value)}" ${disabled ? "disabled" : ""}>
      <span class="settings-radio-dot" aria-hidden="true"></span>
      <span class="settings-radio-name">${escapeHtml(option.label)}</span>
      <span class="settings-radio-meta">${escapeHtml(disabled ? "没填 Token 时不可选" : option.meta)}</span>
    </button>`;
  }).join("");
  const note = `选中的来源失败时会直接停下，不会自动换源。${justoneapiConfigured ? "" : "没填 JustOneAPI Token 时只能选「自动」或「本机 Chrome」。"}`;
  return `<section class="settings-panel">
    <div class="settings-panel-head">
      <h2>取数方式</h2>
      <p>设置一次，后面的对标分析和账号取数都按这里执行，不再每次询问。</p>
    </div>
    <div class="settings-panel-body">
      <div class="settings-radios" role="radiogroup" aria-label="取数方式">${options}</div>
      <p class="settings-panel-note">${escapeHtml(note)}</p>
    </div>
  </section>`;
}

function secretsPanel(values, readonly) {
  return `<section class="settings-panel">
    <div class="settings-panel-head">
      <h2>接口密钥与凭据</h2>
      <p>这些密钥只写进本机的「本地配置.txt」，不会上传到任何地方。留空就是不用这个能力。</p>
    </div>
    ${LOCAL_CONFIG_SECTIONS.map((section) => localConfigItem(section, values[section.field])).join("")}
  </section>`;
}

async function renderSettings() {
  const workspace = state.snapshot?.workspace || {};
  let values = {};
  let readonly = {};
  let notice = "";
  try {
    const config = await fetchLocalConfig();
    values = config.values || {};
    readonly = config.readonly || {};
  } catch (_error) {
    notice = "读取不到本机配置，请刷新页面或重启工作台后再试。";
  }

  elements.workspaceMain.innerHTML = `<div class="settings-page">
    <header class="settings-page-head">
      <p class="settings-kicker">SETTINGS</p>
      <div class="settings-head-row">
        <h1>设置</h1>
        <p class="settings-head-note">密钥与取数只存在这台电脑，不上传</p>
      </div>
    </header>
    <div class="settings-panels">
      ${notice ? `<p class="settings-notice">${escapeHtml(notice)}</p>` : ""}
      ${dataSourcePanel(values.data_source_preference, Boolean(values.justoneapi_token))}
      ${accountScopePanel(values)}
      ${secretsPanel(values, readonly)}
      <p class="settings-page-meta">
        <span><span class="k">版本</span><span class="v is-mono">${escapeHtml(workspaceVersionText(workspace))}</span></span>
        <span class="sep" aria-hidden="true"></span>
        <span><span class="k">工作区</span><span class="v">${escapeHtml(workspace.name || "—")}</span></span>
        <span class="sep" aria-hidden="true"></span>
        <span><span class="k">配置文件</span><span class="v">本地配置.txt</span></span>
      </p>
    </div>
  </div>`;
}


function initCardReorder() {
  const host = elements.workspaceMain;
  if (!host || host.dataset.reorder === "1") return;
  host.dataset.reorder = "1";

  let drag = null;
  let suppressUntil = 0;

  host.addEventListener("pointerdown", (event) => {
    if (event.button !== 0 || event.isPrimary === false) return;
    // 拖拽排序只给对标账号用；我的账号列表沿用同一套卡片，但不参与这个排序
    if (state.currentView !== "benchmark-accounts-v2") return;
    const card = event.target.closest(".bm-card");
    if (!card) return;
    const grid = card.closest(".acct-grid");
    if (!grid) return;
    drag = {
      card,
      grid,
      pointerId: event.pointerId,
      startX: event.clientX,
      startY: event.clientY,
      offsetX: 0,
      offsetY: 0,
      active: false,
      placeholder: null,
    };
  });

  window.addEventListener("pointermove", (event) => {
    if (!drag || drag.pointerId !== event.pointerId) return;
    if (!drag.active) {
      if (Math.hypot(event.clientX - drag.startX, event.clientY - drag.startY) < 6) return;
      beginDrag(event);
    }
    if (!drag || !drag.active) return;
    drag.card.style.left = `${event.clientX - drag.offsetX}px`;
    drag.card.style.top = `${event.clientY - drag.offsetY}px`;
    const hit = document.elementFromPoint(event.clientX, event.clientY);
    const target = hit && hit.closest ? hit.closest(".bm-card") : null;
    if (target && target !== drag.card && drag.grid.contains(target)) {
      movePlaceholder(target, event.clientX, event.clientY);
    }
  });

  window.addEventListener("pointerup", finishDrag);
  window.addEventListener("pointercancel", finishDrag);

  // 拖完松手会补一次 click，这里吃掉，免得误打开详情
  host.addEventListener("click", (event) => {
    if (Date.now() >= suppressUntil) return;
    event.preventDefault();
    event.stopPropagation();
  }, true);

  function beginDrag(event) {
    const { card, grid } = drag;
    const rect = card.getBoundingClientRect();
    drag.active = true;
    drag.offsetX = event.clientX - rect.left;
    drag.offsetY = event.clientY - rect.top;

    const placeholder = document.createElement("div");
    placeholder.className = "bm-placeholder";
    placeholder.style.width = `${rect.width}px`;
    placeholder.style.height = `${rect.height}px`;
    grid.insertBefore(placeholder, card);
    drag.placeholder = placeholder;

    Object.assign(card.style, {
      position: "fixed",
      left: `${rect.left}px`,
      top: `${rect.top}px`,
      width: `${rect.width}px`,
      height: `${rect.height}px`,
      margin: "0",
      zIndex: "60",
    });
    card.classList.add("is-dragging");
    document.body.classList.add("is-reordering");
  }

  function snapshot() {
    const map = new Map();
    drag.grid.querySelectorAll(".bm-card, .bm-placeholder").forEach((el) => {
      map.set(el, el.getBoundingClientRect());
    });
    return map;
  }

  /* 按「指针相对目标卡片中心」判断插到前面还是后面：
     同一行看左右半边，不同行看上下半边。只看 placeholder 位置会单向卡死。 */
  function movePlaceholder(target, pointerX, pointerY) {
    if (!drag.placeholder || target === drag.placeholder) return;

    const tRect = target.getBoundingClientRect();
    const cx = tRect.left + tRect.width / 2;
    const cy = tRect.top + tRect.height / 2;
    const sameRow = Math.abs(pointerY - cy) <= tRect.height * 0.5;
    const after = sameRow ? pointerX > cx : pointerY > cy;

    const reference = after ? target.nextSibling : target;
    if (reference === drag.placeholder) return;

    const before = snapshot();
    drag.grid.insertBefore(drag.placeholder, reference);

    drag.grid.querySelectorAll(".bm-card, .bm-placeholder").forEach((el) => {
      if (el === drag.card || el === drag.placeholder) return;
      const previous = before.get(el);
      if (!previous) return;
      const current = el.getBoundingClientRect();
      const dx = previous.left - current.left;
      const dy = previous.top - current.top;
      if (!dx && !dy) return;
      el.style.transition = "none";
      el.style.transform = `translate(${dx}px, ${dy}px)`;
      requestAnimationFrame(() => {
        el.style.transition = "transform .26s cubic-bezier(.2,.8,.2,1)";
        el.style.transform = "";
        window.setTimeout(() => { el.style.transition = ""; }, 300);
      });
    });
  }

  function finishDrag() {
    if (!drag) return;
    if (!drag.active) { drag = null; return; }
    const { card, placeholder, grid } = drag;
    const target = placeholder.getBoundingClientRect();
    drag = null;
    suppressUntil = Date.now() + 260;
    document.body.classList.remove("is-reordering");

    card.style.transition = "left .2s cubic-bezier(.2,.8,.2,1), top .2s cubic-bezier(.2,.8,.2,1)";
    card.style.left = `${target.left}px`;
    card.style.top = `${target.top}px`;

    window.setTimeout(() => {
      card.classList.remove("is-dragging");
      ["position", "left", "top", "width", "height", "margin", "zIndex", "transition"].forEach((key) => {
        card.style[key] = "";
      });
      if (placeholder.parentNode) placeholder.replaceWith(card);
      const ids = [...grid.querySelectorAll(".bm-card")]
        .map((el) => accountIdFromHref(el.getAttribute("href")))
        .filter(Boolean);
      writeBenchmarkOrder(ids);
    }, 200);
  }
}

/* ==================== 和AI讨论方向 · 单页版 ====================
   和上面 openPromptDialog 那套两步弹窗分开实现：
   - 选路 → 提示 → 想法，全在同一个弹窗里；
   - 指令一直显示，路线或想法一改就跟着变，没有「生成指令」这一步；
   - 选「让 AI 引导我」时不出现提示条和想法框，指令直接是引导版；
   - 底栏固定「取消 / 确认」。
   这个弹窗不写任何数据，只拼一段能粘进对话框的指令。 */
const discussState = { route: "self", text: "" };

function discussEls() {
  const dialog = document.querySelector("#discuss-dialog");
  if (!dialog) return null;
  return {
    dialog,
    body: dialog.querySelector(".dd-body"),
    routeInputs: [...dialog.querySelectorAll('input[name="dd-route"]')],
    field: dialog.querySelector("#dd-field"),
    idea: dialog.querySelector("#dd-idea"),
    text: dialog.querySelector("#dd-text"),
    copy: dialog.querySelector("#dd-copy"),
  };
}

function discussPromptText(route, idea) {
  if (route === "ai") {
    return [
      "用 media-strategy 引导我确定账号方向。",
      "",
      "我现在还没想法。请根据我的现状引导我讨论，一次只问我一个问题，从最影响方向的那个开始。",
    ].join("\n");
  }
  return [
    "用 media-strategy 讨论我的账号方向。",
    "",
    "我这次想聊的是：",
    idea || "（还没填）",
  ].join("\n");
}

/* 一个地方算完所有状态：哪块显示、指令内容、复制键能不能点 */
function syncDiscuss(els) {
  const isAI = discussState.route === "ai";
  els.field.hidden = isAI;

  const idea = els.idea.value.trim();
  discussState.text = discussPromptText(discussState.route, idea);
  els.text.textContent = discussState.text;
  els.dialog.dataset.prompt = discussState.text;
  /* 让 AI 引导时不用填任何东西，随时能复制；自己写想法时，写之前复制没意义 */
  els.copy.disabled = !isAI && !idea;
}

function openDiscussDialog() {
  const els = discussEls();
  if (!els) return;

  discussState.route = "self";
  discussState.text = "";
  els.routeInputs.forEach((input) => { input.checked = input.value === "self"; });
  els.idea.value = "";
  syncDiscuss(els);

  if (!els.dialog.open) els.dialog.showModal();
  els.idea.focus();
}

(function wireDiscussDialog() {
  const els = discussEls();
  if (!els) return;

  els.dialog.addEventListener("click", (event) => {
    if (event.target === els.dialog) { els.dialog.close(); return; }
    if (event.target.closest('[data-dd="close"], [data-dd="cancel"]')) { els.dialog.close(); return; }
    const copy = event.target.closest('[data-dd="copy"]');
    if (copy) copyDialogPrompt(els.dialog, copy);
  });

  els.dialog.addEventListener("change", (event) => {
    if (event.target.name !== "dd-route") return;
    discussState.route = event.target.value === "ai" ? "ai" : "self";
    syncDiscuss(els);
  });

  /* 边写边同步：打一个字指令就跟着变 */
  els.idea.addEventListener("input", () => syncDiscuss(els));
})();

/* ==================== 新增账号 / 重新诊断 · 单页版 ====================
   和「和AI讨论方向」共用 .prompt-sheet 外壳。
   两个弹窗都没有分步：填完直接出指令，或者在没输入的情况下直接给现成指令。
   底栏谁当主按钮看有没有输入：
   - 有输入 → 生成指令（主），复制放产物区；
   - 没输入 → 直接就是确认（主）。 */
/* 两种用法共用一个弹窗：
   - new   新增账号（侧栏 / 页头那几处）
   - first 第一次绑号：先绑号诊断，诊断跑完再按问卷 + 这份诊断一起把账号方向写出来
     （问卷提交不会触发账号分析；这里也不重跑 media-strategy 的对话流程，
      只按 questionnaire_diagnosis_direct 这个入口直接成稿 + 保存）
   提示区只留四步：那两句「要接哪个浏览器」的说明实测没人看，只占位置。
   两种用法的用户动作完全一样（选平台 → 确认 → 在 AI 开的窗口登录 → 回来说一声），
   所以四步共用一套，只有标题按模式换。 */
const ACCOUNT_STEPS = [
  "选择创作者中心",
  "点「确认」送进对话",
  "在AI打开的浏览器窗口登录",
  "登录后告诉AI",
];

const ACCOUNT_DIALOG_COPY = {
  new: { title: "新增账号" },
  first: { title: "绑定并诊断我的账号" },
};

function accountPromptText(platform, creator, mode) {
  if (mode !== "first") {
    const lines = [
      "用 self-account-diagnose 新增一个我的账号",
      `平台：${platform || "（还没选）"}`,
      `创作者中心链接：${creator || "（还没选平台）"}`,
    ];
    if (!platform || !creator) lines.push("", "（先选小红书或抖音，这段才会补全）");
    return lines.join("\n");
  }
  return [
    "1. 绑定并诊断这个账号",
    "用 self-account-diagnose 新增一个我的账号，并跑完第一次诊断",
    `平台：${platform || "（还没选）"}`,
    `创作者中心链接：${creator || "（还没选平台）"}`,
    "跑完告诉我本次正式诊断的文件路径，就是 我方资料/我方账号库.csv 里这个账号的账号目录下、今天那份「账号详细诊断.md」，不要编路径。",
    "",
    "2. 按问卷和这份诊断直出一版账号方向",
    "入口：questionnaire_diagnosis_direct（问卷 + 诊断直出）",
    "读 我方资料/调查问卷.md 和第 1 步那份正式诊断，按 提示词/自媒体规划成稿提示词.md 生成 我方资料/我方自媒体规划.md，再用 执行单元/自媒体规划正式保存.py 一起保存 我方自媒体规划.md 和 我方当前概要.md",
    "不中断直接完成，问卷和诊断都没说的地方写「未定」，不推测。",
    "",
    "3. 推荐一批选题",
    "用 recommend-topics 推荐一批选题",
  ].join("\n");
}

/* 选择或改动平台后，指令和复制键状态一起跟上 */
function syncAccount(dialog) {
  const platform = dialog.dataset.platform || "";
  const creator = dialog.dataset.creator || "";
  const mode = dialog.dataset.mode === "first" ? "first" : "new";
  dialog.dataset.prompt = platform ? accountPromptText(platform, creator, mode) : "";
  dialog.querySelector("#ad-text").textContent = accountPromptText(platform, creator, mode);
  dialog.querySelector("#ad-copy").disabled = !platform;
}

/* 标题和步骤条按 mode 换一套，弹窗骨架不动 */
function paintAccountDialogCopy(dialog, mode) {
  const key = mode === "first" ? "first" : "new";
  const copy = ACCOUNT_DIALOG_COPY[key];
  dialog.dataset.mode = key;
  dialog.querySelector("#ad-title").textContent = copy.title;
  dialog.querySelector("#ad-steps").innerHTML = ACCOUNT_STEPS.map(
    (label, index) =>
      `<span><b class="dd-num">${String(index + 1).padStart(2, "0")}</b>${escapeHtml(label)}</span>`,
  ).join("");
}

function openAccountDialog({ mode } = {}) {
  const dialog = document.querySelector("#account-dialog");
  if (!dialog) return;

  /* 一个账号都没有时，「添加账号」和「绑定并诊断我的账号」是同一件事：
     都要先绑号、跑第一次诊断，再把账号方向写出来。有账号之后新增才只绑号。 */
  const resolved = mode || (homeAccounts().length ? "new" : "first");
  paintAccountDialogCopy(dialog, resolved);
  dialog.dataset.platform = "";
  dialog.dataset.creator = "";
  dialog.querySelectorAll('[data-ad="pick"]').forEach((button) => {
    button.classList.remove("is-on");
    button.setAttribute("aria-checked", "false");
  });
  syncAccount(dialog);

  if (!dialog.open) dialog.showModal();
}

/* ==================== 使用技能 · 单页版 ====================
   和「新增账号」共用 .prompt-sheet 外壳，但没有输入：
   技能名由卡片定死，指令只换名字，格式统一。 */
function skillPromptText(name) {
  return `启动 ${name} 这个技能的流程。`;
}

function openSkillDialog(id) {
  const dialog = document.querySelector("#skill-dialog");
  const item = state.snapshot?.workflows?.skills?.find((skill) => skill.name === id);
  if (!dialog || !item) return;

  const meta = [`/${item.name}`, item.category || "扩展能力"].join(" · ");
  dialog.querySelector("#sk-skill").innerHTML =
    `<b>${escapeHtml(item.display_name || item.name)}</b><span>${escapeHtml(meta)}</span>`;

  const text = skillPromptText(item.name);
  dialog.dataset.prompt = text;
  dialog.querySelector("#sk-text").textContent = text;

  if (!dialog.open) dialog.showModal();
  dialog.setAttribute("tabindex", "-1");
  dialog.focus();
}

function redoPromptText(account, kind) {
  const isBenchmark = kind === "benchmark";
  return [
    isBenchmark
      ? `用 benchmark-account-diagnose 重新分析我的对标账号「${account.name}」。`
      : `用 self-account-diagnose 重新诊断我的账号「${account.name}」。`,
    `平台：${account.platform || ""}`,
    `账号ID：${account.id || ""}`,
    isBenchmark
      ? "这个账号已经建档。按现有流程读取这个账号的最新数据，并重新生成分析"
      : "这个账号已经建档。按现有流程读取这个账号的最新数据，并重新生成诊断",
  ].join("\n");
}

function openRedoDialog(account, kind = "own") {
  const dialog = document.querySelector("#redo-dialog");
  if (!dialog || !account) return;

  /* 同一个弹窗服务两种重跑：我的账号=重新诊断，对标账号=重新分析 */
  dialog.querySelector("#rd-title").textContent = kind === "benchmark" ? "重新分析" : "重新诊断";

  const meta = [account.platform, account.followers != null ? `${formatNumber(account.followers)} 粉丝` : ""]
    .filter(Boolean)
    .join(" · ");
  dialog.querySelector("#rd-acct").innerHTML =
    `<b>${escapeHtml(account.name || "未命名")}</b><span>${escapeHtml(meta)}</span>`;

  const text = redoPromptText(account, kind);
  dialog.dataset.prompt = text;
  dialog.querySelector("#rd-text").textContent = text;

  if (!dialog.open) dialog.showModal();
  dialog.setAttribute("tabindex", "-1");
  dialog.focus();
}

/* ==================== 问卷修改后 · 统一账号选择 ====================
   一个弹窗处理 0 / 1 / 多个账号：
   - 不选账号：只更新方向
   - 选 1 个：方向 + 该账号诊断
   - 选多个：方向更新一次 + 依次诊断多个账号
   账号诊断旧于规划时，从提示条进入「补跑未完成的账号」，只补诊断。 */

const directionDialogState = { mode: "direction", selected: new Set() };

function directionSupportedAccount(account) {
  return DIAGNOSABLE_PLATFORM_KEYS.has(platformKey(account.platform));
}

function directionSelectedAccounts() {
  return homeAccounts().filter(
    (account) => directionSupportedAccount(account) && directionDialogState.selected.has(homeAccountKey(account)),
  );
}

function directionHasDiagnosis(account) {
  return Boolean(account.documents?.detail?.modified_at);
}

function directionAccountLine(account) {
  return `- ${account.platform}「${account.name || "未命名账号"}」账号ID：${account.id}`;
}

function directionReferenceLine(accounts) {
  const withDiagnosis = accounts.filter(directionHasDiagnosis);
  if (!withDiagnosis.length) return "";
  return `参考诊断：${withDiagnosis
    .map((account) => `${account.platform}「${account.name || "未命名账号"}」${formatDate(account.documents.detail.modified_at)}`)
    .join("、")}`;
}

function buildDirectionCommand(mode, accounts) {
  if (mode === "diagnose") {
    if (!accounts.length) return "";
    return [
      accounts.length > 1
        ? "用 self-account-diagnose 依次重新诊断以下账号"
        : "用 self-account-diagnose 重新诊断以下账号",
      "判断基准：最新《我方自媒体规划》（方向已按新问卷更新）",
      ...accounts.map(directionAccountLine),
      "",
      ...(accounts.length > 1
        ? ["逐个跑完，不要并行；某个失败就继续下一个，最后汇总哪个没完成。"]
        : []),
      "不讨论、不追问；方向已经更新，不要再改方向。",
    ].join("\n");
  }

  if (!accounts.length) {
    return [
      "用 questionnaire_direct 按修改后的《调查问卷》直出一版新方向",
      "输入：我方资料/调查问卷.md + 旧版 我方自媒体规划.md",
      "规则：新问卷与旧方向冲突时以新问卷为准；旧方向只补问卷没覆盖的细节；问卷没覆盖的地方写「未定」",
      "保存：我方自媒体规划.md + 我方当前概要.md",
      "不讨论、不追问。",
    ].join("\n");
  }

  const hasAnyDiagnosis = accounts.some(directionHasDiagnosis);
  const directionRule = "规则：诊断只补事实，不写成长期目标；账号差异分账号写清；新问卷与旧方向冲突时以新问卷为准，旧方向只补问卷没覆盖的细节，未覆盖写「未定」";

  if (!hasAnyDiagnosis) {
    return [
      accounts.length > 1
        ? "1. 用 self-account-diagnose 依次诊断以下账号"
        : "1. 用 self-account-diagnose 诊断以下账号",
      "判断基准：本次修改后的《调查问卷》，不要用旧的《我方自媒体规划》",
      ...accounts.map(directionAccountLine),
      "",
      ...(accounts.length > 1
        ? ["逐个跑完，不要并行；某个失败就继续下一个，最后汇总哪个没完成。", ""]
        : []),
      "2. 用 questionnaire_diagnosis_direct 直出并保存新方向",
      "输入：新问卷 + 旧方向 + 第 1 步各账号的正式诊断",
      directionRule,
      "保存：我方自媒体规划.md + 我方当前概要.md",
      "",
      "3. 不讨论、不追问，两步直接跑完。",
    ].join("\n");
  }

  const step2 = accounts.length > 1
    ? "2. 依次用 self-account-diagnose 重新诊断以下账号"
    : "2. 用 self-account-diagnose 重新诊断以下账号";
  const tail = accounts.length > 1
    ? ["3. 逐个跑完，不要并行；某个失败就继续下一个，最后汇总哪个没完成。", "4. 不讨论、不追问。"]
    : ["3. 不讨论、不追问。"];
  return [
    "1. 用 questionnaire_diagnosis_direct 直出并保存新方向",
    directionReferenceLine(accounts),
    directionRule,
    "保存：我方自媒体规划.md + 我方当前概要.md",
    "",
    step2,
    "判断基准：刚保存的新方向",
    ...accounts.map(directionAccountLine),
    "",
    ...tail,
  ].join("\n");
}

function directionFlowItems(mode, accounts) {
  if (mode === "diagnose") {
    if (!accounts.length) return [];
    return [{ title: `补跑 ${accounts.length} 个账号的诊断`, text: "只补诊断，不重写方向；逐个跑完，失败就继续下一个。" }];
  }
  if (!accounts.length) {
    return [{ title: "只更新方向", text: "按新问卷 + 旧方向直出，不跑诊断。" }];
  }
  if (!accounts.some(directionHasDiagnosis)) {
    return [
      { title: `先诊断 ${accounts.length} 个账号`, text: "判断基准用修改后的《调查问卷》，不用旧方向。" },
      { title: "再直出新方向", text: "用新问卷 + 旧方向 + 刚生成诊断，保存规划与概要。" },
    ];
  }
  return [
    { title: "先更新方向", text: "用新问卷 + 旧方向 + 已有诊断直出；冲突时以新问卷为准。" },
    { title: `再依次诊断 ${accounts.length} 个账号`, text: "用刚保存的新方向做基准，逐个跑完；失败就继续下一个。" },
  ];
}

function renderDirectionAccountRow(account) {
  const supported = directionSupportedAccount(account);
  const selected = directionDialogState.selected.has(homeAccountKey(account));
  let stateLabel = "";
  if (!supported) {
    stateLabel = '<span class="dd-account-badge">暂不支持诊断</span>';
  } else if (directionDialogState.mode === "diagnose") {
    stateLabel = '<span class="dd-account-state is-warn">诊断未重跑</span>';
  } else if (directionHasDiagnosis(account)) {
    stateLabel = `<span class="dd-account-state">上次诊断 ${escapeHtml(formatDate(account.documents.detail.modified_at))}</span>`;
  } else {
    stateLabel = '<span class="dd-account-state is-warn">还没有正式诊断</span>';
  }
  const meta = [account.platform || "未填写", account.followers != null ? `${formatNumber(account.followers)} 粉` : ""]
    .filter(Boolean)
    .join(" · ");
  return `<button class="dd-account${selected ? " is-on" : ""}${supported ? "" : " is-off"}" type="button"
      data-dd-dir-account="${escapeAttribute(homeAccountKey(account))}"
      ${supported ? `aria-pressed="${selected}"` : "disabled"}>
    <span class="dd-check">${selected ? '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="m5 12.5 4.5 4.5L19 7"/></svg>' : ""}</span>
    ${platformIcon(platformKey(account.platform))}
    <span class="dd-account-copy"><b>${escapeHtml(account.name || "未命名账号")}</b><span>${escapeHtml(meta)}</span></span>
    ${stateLabel}
  </button>`;
}

function renderDirectionDialog() {
  const dialog = document.querySelector("#dd-direction-dialog");
  if (!dialog) return;
  const accounts = homeAccounts();
  const supported = accounts.filter(directionSupportedAccount);
  const selected = directionSelectedAccounts();
  const mode = directionDialogState.mode;

  const title = dialog.querySelector("#dd-dir-title");
  const hint = dialog.querySelector("#dd-dir-hint");
  const pickAll = dialog.querySelector("#dd-dir-pick-all");
  const list = dialog.querySelector("#dd-dir-accounts");
  const note = dialog.querySelector("#dd-dir-note");
  const flow = dialog.querySelector("#dd-dir-flow");
  const text = dialog.querySelector("#dd-dir-text");
  const need = dialog.querySelector("#dd-dir-need");
  const copy = dialog.querySelector("#dd-dir-copy");

  title.textContent = mode === "diagnose"
    ? "重新诊断未完成的账号"
    : (selected.length ? "更新方向并诊断" : "更新方向");
  hint.textContent = mode === "diagnose"
    ? "已默认勾选诊断未重跑的账号；可以取消，也可以补选其他账号。只支持小红书和抖音。"
    : "可多选；不选就只更新方向。默认勾选首页当前账号。只支持小红书和抖音。";

  pickAll.hidden = !supported.length;
  const allSelected = supported.length > 0
    && supported.every((account) => directionDialogState.selected.has(homeAccountKey(account)));
  pickAll.textContent = allSelected ? "清空" : "全选";

  list.innerHTML = accounts.length
    ? accounts.map(renderDirectionAccountRow).join("")
    : '<div class="dd-empty-account"><b>还没有可诊断的账号</b><span>现在只能先按新问卷更新方向；绑定账号后再诊断。</span></div>';

  if (mode === "diagnose") {
    note.textContent = "只补诊断，不重写方向。";
  } else if (!selected.length) {
    note.textContent = "没有选择账号，所以只按新问卷更新方向，不跑诊断。";
  } else {
    note.textContent = "方向更新会参考所有勾选账号已有的最新诊断；没诊断的账号只补诊断，不回头再改方向。";
  }

  const items = directionFlowItems(mode, selected);
  flow.hidden = !items.length;
  flow.innerHTML = items.map((item, index) =>
    `<div class="dd-flow-item"><b>${String(index + 1).padStart(2, "0")}</b><div><strong>${escapeHtml(item.title)}</strong><span>${escapeHtml(item.text)}</span></div></div>`,
  ).join("");

  const command = buildDirectionCommand(mode, selected);
  dialog.dataset.prompt = command;
  text.textContent = command || "至少选择一个账号，才能补跑诊断。";
  copy.disabled = !command;

  if (mode === "diagnose") {
    need.textContent = selected.length
      ? `只补诊断，不重写方向；依次诊断 ${selected.length} 个账号。`
      : "至少选一个账号才能补跑诊断。";
  } else if (!selected.length) {
    need.textContent = "一个账号都没选，只更新方向。";
  } else if (!selected.some(directionHasDiagnosis)) {
    need.textContent = `将先诊断 ${selected.length} 个账号，再更新方向。`;
  } else {
    need.textContent = `更新方向后，依次诊断 ${selected.length} 个账号；每个约 10-20 分钟。`;
  }
}

function currentDiagnosableAccount() {
  const supported = diagnosableHomeAccounts();
  const current = currentHomeAccount();
  if (current && supported.some((account) => homeAccountKey(account) === homeAccountKey(current))) return current;
  return supported[0] || null;
}

function openDirectionDiagnosisDialog({ mode = "direction", preselect = null } = {}) {
  const dialog = document.querySelector("#dd-direction-dialog");
  if (!dialog) return;
  directionDialogState.mode = mode === "diagnose" ? "diagnose" : "direction";
  if (directionDialogState.mode === "diagnose") {
    const stale = (preselect || staleDiagnosisAccounts()).filter(directionSupportedAccount);
    if (!stale.length) return;
    directionDialogState.selected = new Set(stale.map(homeAccountKey));
  } else {
    const current = currentDiagnosableAccount();
    directionDialogState.selected = new Set(current ? [homeAccountKey(current)] : []);
  }
  renderDirectionDialog();
  if (!dialog.open) dialog.showModal();
  dialog.setAttribute("tabindex", "-1");
  dialog.focus();
}

function toggleDirectionAccount(key) {
  const account = homeAccounts().find((item) => homeAccountKey(item) === key);
  if (!account || !directionSupportedAccount(account)) return;
  if (directionDialogState.selected.has(key)) directionDialogState.selected.delete(key);
  else directionDialogState.selected.add(key);
  renderDirectionDialog();
}

function toggleAllDirectionAccounts() {
  const supported = diagnosableHomeAccounts();
  const allSelected = supported.every((account) => directionDialogState.selected.has(homeAccountKey(account)));
  directionDialogState.selected.clear();
  if (!allSelected) supported.forEach((account) => directionDialogState.selected.add(homeAccountKey(account)));
  renderDirectionDialog();
}

(function wireDirectionDialog() {
  const dialog = document.querySelector("#dd-direction-dialog");
  if (!dialog) return;
  dialog.addEventListener("click", (event) => {
    if (event.target === dialog) { dialog.close(); return; }
    const action = event.target.closest("[data-dd-dir]");
    if (action) {
      const kind = action.dataset.ddDir;
      if (kind === "close" || kind === "cancel") { dialog.close(); return; }
      if (kind === "copy") { copyDialogPrompt(dialog, action); return; }
      if (kind === "toggle-all") { toggleAllDirectionAccounts(); return; }
    }
    const row = event.target.closest("[data-dd-dir-account]");
    if (row && !row.disabled) toggleDirectionAccount(row.dataset.ddDirAccount);
  });
})();

async function copyDialogPrompt(dialog, button) {
  const text = dialog?.dataset.prompt || "";
  if (!text) return;
  const original = button.innerHTML;
  const ok = await writeClipboard(text);
  if (!ok) {
    /* 复制确实没成：把指令选中并明说一声，别让用户以为已经复制好了 */
    const block = dialog.querySelector(".dd-text");
    if (block) {
      const range = document.createRange();
      range.selectNodeContents(block);
      const selection = window.getSelection();
      selection.removeAllRanges();
      selection.addRange(range);
    }
    showToast("没能自动复制，指令已选中，按 Cmd+C 复制");
    return;
  }
  button.innerHTML = '已复制';
  button.disabled = true;
  window.setTimeout(() => {
    button.innerHTML = original;
    button.disabled = false;
  }, 1600);
}

(function wireSinglePageDialogs() {
  const accountDialog = document.querySelector("#account-dialog");
  const redoDialog = document.querySelector("#redo-dialog");
  const skillDialog = document.querySelector("#skill-dialog");
  const recommendTopicActionDialog = document.querySelector("#recommend-topic-action-dialog");
  const topicSearchActionDialog = document.querySelector("#topic-search-action-dialog");

  if (accountDialog) {
    accountDialog.addEventListener("click", (event) => {
      if (event.target === accountDialog) { accountDialog.close(); return; }
      const hit = event.target.closest("[data-ad]");
      if (!hit) return;
      const role = hit.dataset.ad;
      if (role === "close" || role === "cancel") accountDialog.close();
      else if (role === "pick") {
        accountDialog.querySelectorAll('[data-ad="pick"]').forEach((button) => {
          const on = button === hit;
          button.classList.toggle("is-on", on);
          button.setAttribute("aria-checked", on ? "true" : "false");
        });
        accountDialog.dataset.platform = hit.dataset.platform || "";
        accountDialog.dataset.creator = hit.dataset.creator || "";
        syncAccount(accountDialog);
      } else if (role === "copy") copyDialogPrompt(accountDialog, hit);
    });
  }

  if (redoDialog) {
    redoDialog.addEventListener("click", (event) => {
      if (event.target === redoDialog) { redoDialog.close(); return; }
      const hit = event.target.closest("[data-rd]");
      if (!hit) return;
      const role = hit.dataset.rd;
      if (role === "close" || role === "cancel") redoDialog.close();
      else if (role === "copy") copyDialogPrompt(redoDialog, hit);
    });
  }

  if (skillDialog) {
    skillDialog.addEventListener("click", (event) => {
      if (event.target === skillDialog) { skillDialog.close(); return; }
      const hit = event.target.closest("[data-sk]");
      if (!hit) return;
      const role = hit.dataset.sk;
      if (role === "close" || role === "cancel") skillDialog.close();
      else if (role === "copy") copyDialogPrompt(skillDialog, hit);
    });
  }

  if (recommendTopicActionDialog) {
    recommendTopicActionDialog.addEventListener("click", (event) => {
      if (event.target === recommendTopicActionDialog) { recommendTopicActionDialog.close(); return; }
      const hit = event.target.closest("[data-rta]");
      if (!hit) return;
      const role = hit.dataset.rta;
      if (role === "close" || role === "cancel") recommendTopicActionDialog.close();
      else if (role === "copy") copyDialogPrompt(recommendTopicActionDialog, hit);
    });
  }

  if (topicSearchActionDialog) {
    topicSearchActionDialog.addEventListener("click", (event) => {
      if (event.target === topicSearchActionDialog) { topicSearchActionDialog.close(); return; }
      const refHit = event.target.closest("[data-tsa-ref]");
      if (refHit) {
        selectTopicSearchReference(Number(refHit.dataset.tsaRef));
        return;
      }
      const channelHit = event.target.closest("[data-tsa-channel]");
      if (channelHit) {
        toggleTopicSearchChannel(channelHit.dataset.tsaChannel);
        return;
      }
      const hit = event.target.closest("[data-tsa]");
      if (!hit) return;
      const role = hit.dataset.tsa;
      if (role === "close" || role === "cancel") topicSearchActionDialog.close();
      else if (role === "copy") copyDialogPrompt(topicSearchActionDialog, hit);
    });
    /* 想法一改，下面的指令立刻重算；清空就是纯继承对标定位 */
    const tsaIdeaInput = topicSearchActionDialog.querySelector("#tsa-idea");
    if (tsaIdeaInput) {
      tsaIdeaInput.addEventListener("input", () => syncTopicSearchIdea(tsaIdeaInput.value));
    }
  }
})();

/* ==================== 新增对标账号 · 单页版 ====================
   和「新增账号」同壳，字段换成一个主页链接。
   指令常显，链接一改就跟着变，没有「生成指令」这一步；
   底栏固定「取消 / 确认」，链接没通过识别时复制置灰。

   为什么要识别：用户最常干的事是复制某条视频/笔记的地址，而不是账号主页。
   这种情况直接放过去，AI 那边要么跑错对象，要么当场反问，白跑一轮。 */
function benchmarkPromptText(home) {
  return [
    "用 benchmark-account-diagnose 帮我新增一个对标账号，并跑完整个分析。",
    "",
    "账号主页链接：" + (home || "（还没填）"),
    "",
    "按工作台设置里的取数方式、基础内容上限和详细内容范围，直接跑完基础数据、详细数据、详细分析、内容规律分析和行动建议，不要再逐轮问我；只有账号无法唯一确定时再问我。",
  ].join("\n");
}

/* 只认「能确定是账号主页」的写法，别的都拦下来并说清是哪一种不对。
   返回值 state: empty / ok / note（作品链接）/ creator（创作者后台）/ short（短链）/ bad */
function inspectHomeUrl(raw) {
  const value = String(raw || "").trim();
  if (!value) return { state: "empty" };

  if (!/^https?:\/\/\S+$/i.test(value)) {
    return { state: "bad", message: "链接要完整，以 https:// 开头，中间不能有空格" };
  }

  let url;
  try { url = new URL(value); } catch (_error) {
    return { state: "bad", message: "这段不是有效链接，检查一下有没有粘全" };
  }

  const host = url.hostname.replace(/^www\./i, "").toLowerCase();
  const path = url.pathname.replace(/\/+$/, "");

  /* 抖音/小红书的账号也能被分享成短链，所以短链放行；
     真正指向什么由 AI 打开后确认，界面这里只标一句「会在打开后确认」。 */
  if (host === "v.douyin.com") return { state: "ok", platform: "抖音", short: true };
  if (host === "xhslink.com" || host === "xhslink.cn") return { state: "ok", platform: "小红书", short: true };
  if (host === "b23.tv") {
    return { state: "bad", message: "B站的 b23.tv 是视频短链；账号主页是 https://space.bilibili.com/数字ID" };
  }

  if (host === "creator.xiaohongshu.com") {
    return { state: "creator", message: "这是创作者中心后台，不是账号主页" };
  }
  if (host === "creator.douyin.com") {
    return { state: "creator", message: "这是抖音创作者中心后台，不是账号主页" };
  }

  if (host === "xiaohongshu.com" || host.endsWith(".xiaohongshu.com")) {
    if (/^\/user\/profile\/[A-Za-z0-9]+/.test(path)) {
      if (/\/note\//.test(path)) {
        return { state: "note", message: "这是某一条笔记的链接，要填账号主页：点作者头像进去那一页" };
      }
      return { state: "ok", platform: "小红书" };
    }
    if (/^\/(explore|discovery\/item)\//.test(path)) {
      return { state: "note", message: "这是某一条笔记的链接，要填账号主页：点作者头像进去那一页" };
    }
    return { state: "bad", message: "这不像账号主页；主页地址形如 https://www.xiaohongshu.com/user/profile/…" };
  }

  if (host === "douyin.com" || host.endsWith(".douyin.com")) {
    if (/^\/user\/[A-Za-z0-9_-]+/.test(path)) return { state: "ok", platform: "抖音" };
    if (/^\/(video|note|share\/video|share\/note)\//.test(path)) {
      return { state: "note", message: "这是某一条视频的链接，要填账号主页：点作者头像进去那一页" };
    }
    return { state: "bad", message: "这不像账号主页；主页地址形如 https://www.douyin.com/user/…" };
  }

  if (host === "iesdouyin.com" || host.endsWith(".iesdouyin.com")) {
    return { state: "bad", message: "这是抖音的分享页，不是账号主页；在浏览器里打开后复制地址栏那条" };
  }

  if (host === "space.bilibili.com") {
    if (/^\/\d+$/.test(path)) return { state: "ok", platform: "B站" };
    return { state: "bad", message: "这不像B站账号主页；主页地址形如 https://space.bilibili.com/3493130031" };
  }
  if (host === "bilibili.com" || host.endsWith(".bilibili.com")) {
    if (/^\/space\/\d+$/.test(path)) return { state: "ok", platform: "B站" };
    if (/^\/video\/(BV[0-9A-Za-z]+|av\d+)/i.test(path)) {
      return { state: "note", message: "这是B站的某一条视频，要填账号主页：点作者头像进去那一页" };
    }
    return { state: "bad", message: "这不像B站账号主页；主页地址形如 https://space.bilibili.com/3493130031" };
  }

  return { state: "bad", message: "只支持抖音、B站、小红书的账号主页" };
}

const HOME_HINT = "打开账号主页，复制地址栏里那条链接；不是某一条作品。";

/* 输入一改：指令跟着变、复制键跟着亮或灰、错误说明跟着出现或消失 */
function syncBenchmark(dialog) {
  const input = dialog.querySelector("#bm-home");
  const home = input.value.trim();
  const result = inspectHomeUrl(home);

  const text = benchmarkPromptText(home);
  dialog.dataset.prompt = text;
  dialog.querySelector("#bm-text").textContent = text;
  dialog.querySelector("#bm-copy").disabled = result.state !== "ok";

  const field = dialog.querySelector("#bm-field");
  const err = dialog.querySelector("#bm-err");
  const hint = dialog.querySelector("#bm-hint");

  const wrong = result.state !== "empty" && result.state !== "ok";
  field.classList.toggle("is-err", wrong);
  err.hidden = !wrong;
  err.textContent = wrong ? result.message : "";
  /* 错的时候只留一行红字，别让灰字和红字叠着说两件事 */
  hint.hidden = wrong;
  hint.textContent = result.state !== "ok"
    ? HOME_HINT
    : result.short
      ? "已识别：" + result.platform + "短链。打开后要是账号主页才算数，不是的话换完整地址"
      : "已识别：是" + result.platform + "的账号主页";
  hint.classList.toggle("is-ok", result.state === "ok");
}

function openBenchmarkDialog() {
  const dialog = document.querySelector("#benchmark-dialog");
  if (!dialog) return;

  dialog.querySelector("#bm-home").value = "";
  syncBenchmark(dialog);

  if (!dialog.open) dialog.showModal();
  dialog.querySelector("#bm-home").focus();
}

(function wireBenchmarkDialog() {
  const dialog = document.querySelector("#benchmark-dialog");
  if (!dialog) return;

  dialog.addEventListener("click", (event) => {
    if (event.target === dialog) { dialog.close(); return; }
    const hit = event.target.closest("[data-bm]");
    if (!hit) return;
    const role = hit.dataset.bm;
    if (role === "close" || role === "cancel") dialog.close();
    else if (role === "copy") copyDialogPrompt(dialog, hit);
  });

  dialog.querySelector("#bm-home").addEventListener("input", () => syncBenchmark(dialog));
})();


/* ==================== 新增对标内容 · 单页版 ====================
   和另外三个同壳。区别在于这个字段允许整段粘：
   抖音 App 里「分享 → 复制链接」给的是「一堆文案 + 短链 + 复制此链接…」的一整段话，
   所以先抽第一条 URL，再判断这条 URL 是不是单条作品。
   判据对齐 执行单元/抖音内容链接解析.py 与 skills/diagnose/references/known-issues.json：
   - v.douyin.com 短链是合法输入，由 AI 打开后跳转解析，前端不拦；
   - douyin.com/?recommend=1 是推荐首页、/user/… 是账号主页，都要拦；
   - B站 b23.tv、小红书 xhslink.com 同理放行。 */
function contentPromptText(url) {
  return [
    "用 benchmark-content-diagnose 帮我新增一条对标内容并完成分析。",
    "",
    "内容链接：" + (url || "（还没填）"),
  ].join("\n");
}

/* 从整段分享文案里挑第一条链接；没有就返回空串 */
function extractFirstUrl(text) {
  const match = String(text || "").match(/https?:\/\/[^\s，。；、！？"'<>（）()【】]+/i);
  return match ? match[0].replace(/[.,;:!?]+$/, "") : "";
}

function inspectContentUrl(raw) {
  const text = String(raw || "").trim();
  if (!text) return { state: "empty" };

  const url = extractFirstUrl(text);
  if (!url) {
    return { state: "bad", message: "这段里没找到链接。把「分享 → 复制链接」那一整段粘进来就行" };
  }

  let u;
  try { u = new URL(url); } catch (_error) {
    return { state: "bad", message: "链接不完整，检查一下有没有粘全" };
  }
  const host = u.hostname.replace(/^www\./i, "").toLowerCase();
  const path = u.pathname.replace(/\/+$/, "");
  const idInQuery = ["modal_id", "vid", "aweme_id", "item_id"]
    .some((key) => /^\d{15,24}$/.test(u.searchParams.get(key) || ""));

  if (host === "v.douyin.com") return { state: "ok", platform: "抖音", short: true };
  if (host === "douyin.com" || host.endsWith(".douyin.com")) {
    /* /user/… 即使带 modal_id 也是账号主页，别当成视频 */
    if (/^\/user\//.test(path)) {
      return { state: "account", message: "这是账号主页，不是某一条作品。要加账号请用「新增对标账号」" };
    }
    if (/^\/(video|note|slide)\/\d{10,}/.test(path)) return { state: "ok", platform: "抖音" };
    if (idInQuery) return { state: "ok", platform: "抖音" };
    return { state: "feed", message: "这是抖音首页或推荐页，不是某一条作品。打开那条视频，点「分享 → 复制链接」" };
  }
  if (host === "iesdouyin.com" || host.endsWith(".iesdouyin.com")) {
    if (/^\/share\/(video|note|slide)\//.test(path)) return { state: "ok", platform: "抖音" };
    return { state: "feed", message: "这是抖音的分享页，不是单条作品。用「分享 → 复制链接」那一整段" };
  }

  if (host === "b23.tv") return { state: "ok", platform: "B站", short: true };
  if (host === "space.bilibili.com") {
    return { state: "account", message: "这是B站账号主页，不是某一条视频。要加账号请用「新增对标账号」" };
  }
  if (host === "bilibili.com" || host.endsWith(".bilibili.com")) {
    if (/^\/video\/(BV[0-9A-Za-z]+|av\d+)/i.test(path)) return { state: "ok", platform: "B站" };
    if (/^\/space\/\d+$/.test(path)) {
      return { state: "account", message: "这是B站账号主页，不是某一条视频。要加账号请用「新增对标账号」" };
    }
    return { state: "bad", message: "这不像B站视频页；视频地址形如 https://www.bilibili.com/video/BV…" };
  }

  if (host === "xhslink.com" || host === "xhslink.cn") return { state: "ok", platform: "小红书", short: true };
  if (host === "xiaohongshu.com" || host.endsWith(".xiaohongshu.com")) {
    if (/^\/(explore|discovery\/item)\/[A-Za-z0-9]+/.test(path)) return { state: "ok", platform: "小红书" };
    if (/^\/user\/profile\//.test(path)) {
      return { state: "account", message: "这是小红书账号主页，不是某一条笔记。要加账号请用「新增对标账号」" };
    }
    return { state: "bad", message: "这不像小红书的笔记页；笔记地址形如 https://www.xiaohongshu.com/explore/…" };
  }

  return { state: "bad", message: "只支持抖音、B站、小红书的作品链接" };
}

function syncContent(dialog) {
  const input = dialog.querySelector("#ct-link");
  const result = inspectContentUrl(input.value);
  const url = extractFirstUrl(input.value);

  const text = contentPromptText(url);
  dialog.dataset.prompt = text;
  dialog.querySelector("#ct-text").textContent = text;
  dialog.querySelector("#ct-copy").disabled = result.state !== "ok";

  const wrong = result.state !== "empty" && result.state !== "ok";
  dialog.querySelector("#ct-field").classList.toggle("is-err", wrong);
  const err = dialog.querySelector("#ct-err");
  err.hidden = !wrong;
  err.textContent = wrong ? result.message : "";

  const hint = dialog.querySelector("#ct-hint");
  hint.hidden = wrong;
  hint.textContent = result.state === "ok"
    ? (result.short
        ? "已识别：" + result.platform + "短链，取数时会自动展开成完整地址"
        : "已识别：是" + result.platform + "的作品链接")
    : CONTENT_HINT;
  hint.classList.toggle("is-ok", result.state === "ok");
}

function openContentDialog() {
  const dialog = document.querySelector("#content-dialog");
  if (!dialog) return;

  dialog.querySelector("#ct-link").value = "";
  syncContent(dialog);

  if (!dialog.open) dialog.showModal();
  dialog.querySelector("#ct-link").focus();
}

(function wireContentDialog() {
  const dialog = document.querySelector("#content-dialog");
  if (!dialog) return;

  dialog.addEventListener("click", (event) => {
    if (event.target === dialog) { dialog.close(); return; }
    const hit = event.target.closest("[data-ct]");
    if (!hit) return;
    const role = hit.dataset.ct;
    if (role === "close" || role === "cancel") dialog.close();
    else if (role === "copy") copyDialogPrompt(dialog, hit);
  });

  dialog.querySelector("#ct-link").addEventListener("input", () => syncContent(dialog));
})();

