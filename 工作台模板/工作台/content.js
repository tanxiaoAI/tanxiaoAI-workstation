"use strict";

const query = new URLSearchParams(location.search);
const contentKind = query.get("kind") || "";
const contentId = query.get("id") || "";
const requestedReturnTo = query.get("return_to") || "";

const state = {
  toastTimer: null,
};

const elements = {
  back: document.querySelector("#back-to-previous"),
  breadcrumb: document.querySelector("#content-breadcrumb"),
  reload: document.querySelector("#reload-content"),
  toast: document.querySelector("#toast"),
  view: document.querySelector("#content-view"),
};

function showToast(message) {
  clearTimeout(state.toastTimer);
  elements.toast.textContent = message;
  elements.toast.classList.remove("is-hidden");
  state.toastTimer = setTimeout(() => elements.toast.classList.add("is-hidden"), 2400);
}

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

function formatDate(value) {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value ? escapeHtml(value) : "—";
  return new Intl.DateTimeFormat("zh-CN", {
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  }).format(date);
}

function formatWordCount(value) {
  const count = Number(value);
  return Number.isFinite(count) && count > 0 ? `${formatNumber(count)} 字` : "未提供";
}

function visibleTextLength(value) {
  const content = String(value || "")
    .replace(/\r\n?/g, "\n")
    .replace(/^---\n[\s\S]*?\n---\n?/, "")
    .replace(/```[\s\S]*?```/g, "")
    .replace(/^\s{0,3}#{1,6}\s+.*$/gm, "")
    .replace(/^\s*[-*+]\s+/gm, "")
    .replace(/[\p{P}\p{S}]/gu, "")
    .replace(/\s+/g, "");
  return [...content].length;
}

function tagTone(value) {
  const text = String(value || "");
  if (/不建议|失败|异常|阻断|废弃|取消|无效|不适配|不通过|高风险/.test(text)) return "tone-red";
  if (/风险|低适配|低质量|不匹配|问题|待优化|需修正|受限|较差/.test(text)) return "tone-pink";
  if (/暂缓|生产中|待生产|待发布|处理中|进行中|排期|草稿/.test(text)) return "tone-orange";
  if (/待|未填写|待补齐|缺失|暂无|普通|一般|未知|低优先/.test(text)) return "tone-yellow";
  if (/建议做|优秀|高适配|大爆款|已完成|已通过|已验证|可用|高优先/.test(text)) return "tone-deep-green";
  if (/已发布|正常|小爆款|中适配|已复盘|可调用|已建档|已分析|已启用|稳定/.test(text)) return "tone-light-green";
  return "tone-neutral";
}

function tag(value) {
  const text = value || "未填写";
  return `<span class="content-tag ${tagTone(text)}">${escapeHtml(text)}</span>`;
}

function dataLevelTone(value) {
  const level = String(value || "");
  if (/大爆款/.test(level)) return "tone-deep-green";
  if (/小爆款/.test(level)) return "tone-light-green";
  if (/优秀/.test(level)) return "tone-orange";
  if (/正常|普通|一般|中等/.test(level)) return "tone-yellow";
  if (/差|低|不佳|失败/.test(level)) return "tone-red";
  return "tone-gray";
}

function dataLevelTag(value) {
  const level = value || "未填写";
  return `<span class="content-tag ${dataLevelTone(level)}">${escapeHtml(level)}</span>`;
}

function platformKey(value) {
  const platform = String(value || "");
  if (/抖音|douyin|tiktok/i.test(platform)) return "douyin";
  if (/小红书|xhs|rednote/i.test(platform)) return "xiaohongshu";
  if (/B站|哔哩哔哩|bilibili/i.test(platform)) return "bilibili";
  if (/视频号|微信|wechat/i.test(platform)) return "wechat";
  return "other";
}

// 小红书 / 抖音 / B 站用官方 logo 位图（工作台同目录，演示版见 demo-init.js 的
// platformIconBase 覆盖）；视频号和未知平台仍用内联 SVG。
const PLATFORM_ICON_FILES = {
  xiaohongshu: "platform-xiaohongshu.png",
  douyin: "platform-douyin.png",
  bilibili: "platform-bilibili.png",
};

function platformIconBase() {
  return "";
}

function platformIcon(key) {
  const file = PLATFORM_ICON_FILES[key];
  if (file) return `<img class="platform-icon platform-icon-${key}" src="${platformIconBase()}${file}" alt="" aria-hidden="true">`;
  if (key === "wechat") return '<svg class="platform-icon platform-icon-wechat" aria-hidden="true" viewBox="0 0 24 24"><path d="M20 11a6.8 6.8 0 0 1-7.2 6.7 8 8 0 0 1-3.1-.6L5 18.8l1.2-3.2A6.2 6.2 0 0 1 5 12a6.8 6.8 0 0 1 7.2-6.7A6.8 6.8 0 0 1 20 11Z"/><circle cx="10" cy="11" r=".8"/><circle cx="15" cy="11" r=".8"/></svg>';
  return '<svg class="platform-icon platform-icon-other" aria-hidden="true" viewBox="0 0 24 24"><circle cx="12" cy="12" r="8"/><path d="M4 12h16M12 4a13 13 0 0 1 0 16M12 4a13 13 0 0 0 0 16"/></svg>';
}

function platformPill(value) {
  const label = value || "未填写";
  const key = platformKey(label);
  return `<span class="content-tag platform-pill platform-${key}">${platformIcon(key)}<span>${escapeHtml(label)}</span></span>`;
}

function rawUrl(path) {
  return `/api/raw?path=${encodeURIComponent(path)}`;
}

function renderMarkdown(input, options = {}) {
  return globalThis.MeilingMarkdown.renderMarkdown(input, options);
}

function emptyState(title, detail) {
  return `<section class="content-empty"><strong>${escapeHtml(title)}</strong><p>${escapeHtml(detail)}</p><a href="index.html">返回工作台</a></section>`;
}

function documentButton(document, activePath) {
  if (!document?.path) return "";
  const isActive = document.path === activePath;
  return `<button class="source-item ${isActive ? "is-active" : ""}" type="button" data-document-path="${escapeAttribute(document.path)}" data-document-title="${escapeAttribute(document.title)}"><span>${escapeHtml(document.title)}</span>${document.meta ? `<small>${escapeHtml(document.meta)}</small>` : ""}</button>`;
}

function documentTab(document, activePath) {
  if (!document?.path) return "";
  const isActive = document.path === activePath;
  return `<button class="benchmark-tab ${isActive ? "is-active" : ""}" type="button" role="tab" aria-selected="${isActive}" data-document-path="${escapeAttribute(document.path)}" data-document-title="${escapeAttribute(document.title)}">${escapeHtml(document.title)}</button>`;
}

function safeExternalLink(value, label) {
  try {
    const url = new URL(value);
    if (!/^(https?):$/.test(url.protocol)) return "";
    return `<a class="secondary-link" href="${escapeAttribute(url.href)}">${escapeHtml(label)}</a>`;
  } catch (_error) {
    return "";
  }
}

function normalizedDocument(path, title, meta = "") {
  return path ? { path, title, meta } : null;
}

function coverPlaceholder() {
  return `<div class="content-cover content-cover-placeholder" role="img" aria-label="未提供封面"><svg aria-hidden="true" viewBox="0 0 64 64"><rect x="10" y="12" width="44" height="40" rx="6"/><circle cx="24" cy="26" r="4"/><path d="m15 46 12-12 8 8 6-6 8 10"/></svg></div>`;
}

function getContent(snapshot) {
  if (contentKind === "benchmark") {
    const item = snapshot.workflows.insight.benchmark_contents.find((content) => content.key === contentId);
    if (!item) return null;
    const documents = [
      // 「内容分析」这一栏始终占位：这份文档还没生成时，保留 tab、由内容区给出说明，
      // 而不是让整个 tab 消失（2026-09-29）。**只对「独立建档」的内容这样处理** ——
      // 来自对标账号的内容是账号链路采集来的，本来就不产这份分析，不要给它补 tab。
      // 用文件名本身当占位值：真实路径一定是完整相对路径，不会等于裸文件名。
      normalizedDocument(
        item.analysis_path || (item.source === "standalone" ? "内容分析.md" : ""),
        "内容分析",
      ),
      normalizedDocument(item.transcript_path, "视频文案", formatWordCount(item.transcript_word_count)),
      normalizedDocument(item.comments_path, "评论"),
    ].filter(Boolean);
    return {
      kind: "benchmark",
      label: "对标内容分析",
      item,
      primaryDocument: documents[0] || null,
      documents,
    };
  }
  if (contentKind === "own") {
    // 同一个内容 ID 既可能出现在账号采集里，也可能走过内容生产。
    // 从脚本创作列表进来时以生产记录为准；其他入口（我的账号）仍看采集详情。
    const preferProduction = /creation-board/.test(requestedReturnTo);
    const collected = preferProduction
      ? null
      : accountCollectedItems(snapshot).find((content) => content.id === contentId && content.has_detail);
    const collectedItem = collected
      ? {
        ...collected,
      }
      : null;
    if (collectedItem) {
      return {
        kind: "own",
        collected: true,
        label: "我方账号采集内容",
        item: collectedItem,
        primaryDocument: normalizedDocument(collectedItem.transcript_path, "视频文案", formatWordCount(collectedItem.transcript_word_count)),
        documents: [
          normalizedDocument(collectedItem.transcript_path, "视频文案", formatWordCount(collectedItem.transcript_word_count)),
        ].filter(Boolean),
      };
    }
    const item = snapshot.workflows.production.own_contents.find((content) => content.id === contentId);
    if (!item) return null;
    return {
      kind: "own",
      label: "我方内容生产",
      item,
      snapshot,
      primaryDocument: normalizedDocument(item.production_record_path, "内容生产记录"),
      documents: [
        normalizedDocument(item.production_record_path, "内容生产记录"),
        normalizedDocument(item.transcript_path, "视频文案", formatWordCount(item.transcript_word_count)),
      ].filter(Boolean),
    };
  }
  return null;
}

function accountCollectedItems(snapshot) {
  const ownedAccounts = snapshot.workflows?.self?.own_accounts || [];
  const collected = [];
  for (const account of ownedAccounts) {
    for (const content of account.account_contents || []) collected.push(content);
  }
  return collected;
}

/* 左侧栏导航高亮（详情页共用） */
function markNavActive(key) {
  document.documentElement.dataset.nav = key;
  document.querySelectorAll(".primary-nav .nav-item").forEach((item) => {
    item.classList.toggle("is-active", item.dataset.navKey === key);
  });
}

/* 侧栏版本号：和首页写同一行，切页时品牌区不发生变化 */
function syncWorkspaceVersion(snapshot) {
  const element = document.querySelector("#workspace-version");
  const raw = snapshot?.workspace?.version;
  if (!element || !raw) return;
  const text = String(raw);
  const version = /^v/i.test(text) ? text : `v${text}`;
  element.textContent = version;
}

/* 顶栏面包屑：上级名（列表页）＋ 本条名字（照稿子 .crumb；稿子把标题截到 28 字） */
const CRUMB_GROUP = {
  "对标内容分析": "对标内容",
  "我方账号采集内容": "我的账号",
  "我方内容生产": "脚本创作",
};
const CRUMB_LINKS = {
  "对标内容": "index.html#benchmark-contents",
  "我的账号": "index.html#own-accounts",
  "脚本创作": "index.html#creation-board",
};
function applyCrumb(label, title, displayTitle) {
  const group = document.querySelector("#content-crumb-group");
  const groupText = CRUMB_GROUP[label] || label || "内容详情";
  if (group) {
    group.textContent = groupText;
    group.href = CRUMB_LINKS[groupText] || "index.html#benchmark-contents";
  }
  const crumbTitle = String(displayTitle || title || label || "");
  elements.breadcrumb.textContent = crumbTitle.length > 34 ? `${crumbTitle.slice(0, 34)}…` : crumbTitle;
  elements.breadcrumb.title = crumbTitle;
}

function renderView(model, activeDocument, documentHtml) {
  const { item, kind, label, documents, primaryDocument } = model;
  if (kind === "own" && !model.collected) {
    renderOwnView(model);
    return;
  }
  const isBenchmark = kind === "benchmark" || model.collected === true;
  markNavActive(isBenchmark ? "benchmark-contents" : "own-accounts");
  const cover = item.cover_path
    ? `<img class="content-cover" src="${rawUrl(item.cover_path)}" alt="${escapeAttribute(item.title)}封面">`
    : coverPlaceholder();
  const statusTags = isBenchmark
    ? `${dataLevelTag(item.data_level)}${tag(item.analysis_state)}`
    : `${tag(item.status)}${item.data_level ? dataLevelTag(item.data_level) : ""}`;
  const metrics = [
    ["点赞", formatNumber(item.metrics.likes)],
    ["收藏", formatNumber(item.metrics.favorites)],
    ["评论", formatNumber(item.metrics.comments)],
  ];
  if (item.metrics.shares !== null && item.metrics.shares !== undefined) metrics.push(["分享", formatNumber(item.metrics.shares)]);
  const externalAction = safeExternalLink(item.external_url, "打开原网页");
  const currentDocument = activeDocument || primaryDocument;
  const sourceList = documents.length
    ? documents.map((document) => documentButton(document, currentDocument?.path)).join("")
    : '<p class="source-empty">该内容暂未沉淀可读取的正式文档。</p>';
  const documentTabs = documents.map((document) => documentTab(document, currentDocument?.path)).join("");

  applyCrumb(label, item.title);
  document.title = `${item.title}｜媒灵内容档案`;
  const facts = [
    { name: "账号", value: item.account_name || "待补齐" },
    { name: "平台", value: platformPill(item.platform), isHtml: true },
    { name: "媒介形式", value: item.media_format || "待补齐" },
    { name: "发布时间", value: item.published_at || "待补齐" },
    ...metrics.map(([name, value]) => ({ name, value })),
  ];
  elements.view.innerHTML = `<section class="content-summary">
    <div class="summary-copy">
      <h1>${escapeHtml(item.title)}</h1>
      <div class="tag-row">${statusTags}</div>
      <dl class="summary-facts">${facts.map(({ name, value, isHtml }) => `<div><dt>${escapeHtml(name)}</dt><dd>${isHtml ? value : escapeHtml(value)}</dd></div>`).join("")}</dl>
    </div>
    <div class="summary-media">${cover}${externalAction ? `<div class="summary-actions">${externalAction}</div>` : ""}</div>
  </section>
  ${isBenchmark ? `<div class="benchmark-layout"><div class="benchmark-tabs" role="tablist" aria-label="正式资料">${documentTabs}</div><article class="content-document benchmark-document"><header><span class="document-path" title="${escapeAttribute(currentDocument?.path || "")}">${escapeHtml(currentDocument?.path || "当前资料尚未建立可用路径")}</span></header><div id="content-document-body" class="markdown-body">${documentHtml}</div></article></div>` : `<div class="content-layout"><aside class="source-panel"><div class="source-panel-head"><span>FORMAL SOURCE</span><strong>正式资料</strong></div>${sourceList}</aside><article class="content-document"><header><span class="document-path" title="${escapeAttribute(currentDocument?.path || "")}">${escapeHtml(currentDocument?.path || "当前资料尚未建立可用路径")}</span></header><div id="content-document-body" class="markdown-body">${documentHtml}</div></article></div>`}
  </div>`;
}

/* ==================================================================
   脚本创作 · 详情页
   —— 版式：2026-10-27 tab 版设计稿；内容定位按 2026-10-31 重设计
   —— tab：内容定位 / 内容初稿 / 内容审查（有报告才出现）/ 内容终稿
   —— 数据源：内容生产记录 / 过程记录/内容审查/审查报告-vN.md
   ================================================================== */

const SD_STAGE_NAMES = {
  定位: ["内容定位"],
  初稿: ["内容初稿"],
  终稿: ["内容终稿", "终稿"],
};

const SD_ICON = {
  pin: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 20.5s6-5.2 6-9.4A6 6 0 0 0 6 11.1c0 4.2 6 9.4 6 9.4z"/><circle cx="12" cy="10.8" r="2.2"/></svg>',
  doc: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M6 3.5h8l3.5 3.5v13.5H6z"/><path d="M14 3.5V7.5h3.5M9 12h6M9 15.5h4"/></svg>',
  shield: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 3.5 19.5 6v6c0 4.2-3.2 7-7.5 8.5C7.7 19 4.5 16.2 4.5 12V6z"/><path d="m8.8 12 2.2 2.2 4-4.4"/></svg>',
  flag: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M6 3.5v17"/><path d="M6 4.5h11l-2 4 2 4H6z"/></svg>',
  down: '<svg class="sd-car" viewBox="0 0 24 24" aria-hidden="true"><path d="m6 9.5 6 6 6-6"/></svg>',
  check: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="m4.5 12.5 5 5 10-11"/></svg>',
  warn: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 4.2 21 20H3z"/><path d="M12 10v4.6"/><circle cx="12" cy="17.2" r=".9"/></svg>',
  copy: '<svg viewBox="0 0 24 24" aria-hidden="true"><rect x="8.5" y="8.5" width="11" height="11" rx="2.4"/><path d="M15.5 8.5v-2a2 2 0 0 0-2-2h-7a2 2 0 0 0-2 2v7a2 2 0 0 0 2 2h2"/></svg>',
  edit: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M4.5 19.5h4l10-10-4-4-10 10z"/><path d="m14.5 5.5 4 4"/></svg>',
  x: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="m6.5 6.5 11 11M17.5 6.5l-11 11"/></svg>',
};

const SD_FIELDS = [
  ["theme", "主题", "这条内容主要讲什么"],
  ["audience", "目标用户", "主要写给谁"],
  ["pain", "核心痛点", "他们现在卡在哪里"],
  ["benefit", "收益承诺", "看完能得到什么"],
  ["proposition", "核心命题", "最后希望他相信什么"],
  ["angle", "切口", "从哪个具体角度开始讲"],
];

const SD_TABS = [
  { key: "position", name: "内容定位" },
  { key: "draft", name: "内容初稿" },
  { key: "review", name: "内容审查" },
  { key: "final", name: "内容终稿" },
];

const sdViewState = { tab: "", position: null, draft: null, review: null, final: null };

function sdSource(value) {
  return String(value || "").replace(/\r\n?/g, "\n");
}

function sdCleanCell(value) {
  return String(value ?? "")
    .replace(/<br\s*\/?>/gi, " ")
    .replace(/<[^>]*>/g, "")
    .replace(/&nbsp;/g, " ")
    .replace(/\*\*/g, "")
    .replace(/`/g, "")
    .replace(/\s+/g, " ")
    .trim();
}

function sdSplitHeadings(source, level) {
  const text = sdSource(source);
  const pattern = new RegExp(`^${"#".repeat(level)}\\s+(.+?)\\s*$`, "gm");
  const matches = [...text.matchAll(pattern)];
  return matches.map((match, index) => ({
    title: sdCleanCell(match[1]),
    body: text.slice(match.index + match[0].length, matches[index + 1]?.index ?? text.length).trim(),
  }));
}

function sdSplitSubHeadings(source) {
  const text = sdSource(source);
  const matches = [...text.matchAll(/^#{3,4}\s+(.+?)\s*$/gm)];
  return matches
    .map((match, index) => ({
      title: sdCleanCell(match[1]),
      body: text.slice(match.index + match[0].length, matches[index + 1]?.index ?? text.length).trim(),
    }))
    .filter((block) => !/^v[1-9]\d*$/.test(block.title));
}

function sdSection(sections, names) {
  return sections.find((section) => names.includes(section.title)) || null;
}

function sdVersionList(body) {
  const text = sdSource(body).trim();
  if (!text) return [];
  const matches = [...text.matchAll(/^###\s+v([1-9]\d*)\s*$/gm)];
  if (!matches.length) return [{ version: 1, body: text }];
  return matches.map((match, index) => ({
    version: Number(match[1]),
    body: text.slice(match.index + match[0].length, matches[index + 1]?.index ?? text.length).trim(),
  }));
}

function sdCleanTitle(value) {
  return sdCleanCell(value).replace(/^[一二三四五六七八九十百]+、\s*/, "").trim();
}

function sdTimeOf(value) {
  const match = String(value || "").match(/[（(]\s*(\d{1,2}:\d{2}\s*[–\-—~]\s*\d{1,2}:\d{2})\s*[）)]/);
  return match ? match[1].replace(/\s+/g, "") : "";
}

function sdParseRows(body) {
  const rows = [];
  for (const line of sdSource(body).split("\n")) {
    const trimmed = line.trim();
    if (!trimmed.startsWith("|")) continue;
    const inner = trimmed.endsWith("|") ? trimmed.slice(1, -1) : trimmed.slice(1);
    const cells = inner.split("|").map(sdCleanCell);
    if (cells.length < 2) continue;
    const label = cells[0];
    const value = cells.slice(1).find(Boolean) || "";
    if (!label || /^[-: ]+$/.test(label)) continue;
    if (/^(字段|原文|描述|项目)$/.test(label) && /^(当前结论|描述|问题|规律应用|说明)$/.test(value)) continue;
    rows.push({ label, value, cells });
  }
  return rows;
}

function sdField(rows, prefix) {
  return rows.find((row) => row.label.startsWith(prefix))?.value || "";
}

function sdTableRows(body) {
  const rows = [];
  for (const line of sdSource(body).split("\n")) {
    const trimmed = line.trim();
    if (!trimmed.startsWith("|")) continue;
    const inner = trimmed.endsWith("|") ? trimmed.slice(1, -1) : trimmed.slice(1);
    const cells = inner.split("|").map(sdCleanCell);
    if (!cells.length || cells.every((cell) => /^[-: ]*$/.test(cell))) continue;
    rows.push(cells);
  }
  return rows;
}

function sdLineValue(body, label) {
  for (const line of sdSource(body).split("\n")) {
    const match = line.match(/^\s*([^：:]+)[：:]\s*(.*)$/);
    if (match && match[1].trim() === label) return sdCleanCell(match[2]);
  }
  return "";
}

function sdMetaValue(body, label) {
  for (const line of sdSource(body).split("\n")) {
    const match = line.match(/^\s*[-*]\s*([^：:]+)[：:]\s*(.*)$/);
    if (match && match[1].trim() === label) return sdCleanCell(match[2]);
  }
  return "";
}

function sdPositionModel(body) {
  const rows = sdParseRows(body);
  const model = {
    theme: sdField(rows, "主题"),
    audience: sdField(rows, "目标用户"),
    pain: sdField(rows, "核心痛点"),
    benefit: sdField(rows, "收益承诺"),
    proposition: sdField(rows, "核心命题"),
    angle: sdField(rows, "切口"),
    media: sdField(rows, "媒介形式"),
    contentType: sdField(rows, "内容类别"),
    primaryGoal: sdField(rows, "主目标侧重"),
  };
  model.filled = [model.theme, model.audience, model.pain, model.benefit, model.proposition, model.angle].filter(Boolean).length;
  return model;
}

function sdFrameworkInfo(body) {
  const blocks = sdSplitSubHeadings(body);
  const overviewBlock = blocks.find((block) => block.title.includes("框架总览"));
  const overview = (overviewBlock ? sdField(sdParseRows(overviewBlock.body), "推进主线") : "") || sdLineValue(body, "推进主线");
  const segments = blocks
    .map((block) => {
      const title = sdCleanTitle(block.title);
      if (!title || /内容框架|完整初稿|框架总览|推进主线/.test(title)) return null;
      const rows = sdParseRows(block.body);
      const candidate = rows.length
        ? rows.map((row) => row.value).find((value) => value && value.length <= 90) || ""
        : sdCleanCell(block.body.split("\n").find((line) => line.trim() && !line.trim().startsWith("|")) || "");
      return {
        name: title.replace(/[（(][^）)]*[）)]/g, "").trim(),
        time: sdTimeOf(block.title),
        line: candidate.length <= 90 ? candidate : "",
      };
    })
    .filter(Boolean);
  return { overview, segments };
}

function sdDraftModel(body) {
  const source = sdSource(body);
  if (!source.trim()) return null;
  const title = sdLineValue(source, "标题");
  const lines = source.split("\n");
  /* 正文起点：优先认裸行「完整初稿」（格式规定的标记行）。
     模型漏写裸行时退一步认「### 二、完整初稿」这类标题行兜底，
     并把兜底起点后面混着的「标题：/完整初稿」几行剔掉。 */
  const bareAt = lines.findIndex((line) => /^完整初稿\s*$/.test(line.trim()));
  const start = bareAt >= 0 ? bareAt
    : lines.findIndex((line) => /^(?:#{1,6}\s*)?(?:[一二三四五六七八九十\d]{1,3}\s*[、．.]\s*)?完整初稿\s*$/.test(line.trim()));
  let content = start >= 0 ? lines.slice(start + 1).join("\n").trim() : "";
  if (bareAt < 0 && start >= 0 && content) {
    const body = content.split("\n");
    let i = 0;
    while (i < body.length && (!body[i].trim() || /^标题\s*[：:]/.test(body[i].trim()) || body[i].trim() === "完整初稿")) i += 1;
    content = body.slice(i).join("\n").trim();
  }
  if (!content && !/内容框架/.test(source)) {
    content = lines.filter((line) => !/^标题[：:]/.test(line.trim())).join("\n").trim();
  }
  const frame = sdFrameworkInfo(source);
  const markers = [...content.matchAll(/^【([^】]+)】\s*$/gm)];
  const segments = markers.length
    ? markers.map((match, index) => {
      const name = match[1].trim();
      const keys = name.split(/[｜|]/).map((part) => part.trim()).filter(Boolean);
      const info = frame.segments.find((segment) => keys.some((key) => segment.name === key || segment.name.includes(key) || key.includes(segment.name)));
      return {
        name,
        time: info?.time || "",
        line: info?.line || "",
        text: content.slice(match.index + match[0].length, markers[index + 1]?.index ?? content.length).trim(),
      };
    }).filter((segment) => segment.text)
    : content ? [{ name: "完整初稿", time: "", line: "", text: content }] : [];
  if (!title && !segments.length) return null;
  /* rawBody：完整初稿以下的原始正文（含【分段名】标记）。
     手动编辑要用它，用 segments 拼回去会丢分段标记。 */
  return { title, overview: frame.overview, segments, rawBody: content };
}

function sdFinalModel(body) {
  const source = sdSource(body);
  if (!source.trim()) return null;
  const lines = source.split("\n");
  /* 同 sdDraftModel：裸行优先，漏写时用标题行兜底并剔掉开头混入的标签行。 */
  const bareAt = lines.findIndex((line) => /^完整终稿\s*$/.test(line.trim()));
  const start = bareAt >= 0 ? bareAt
    : lines.findIndex((line) => /^(?:#{1,6}\s*)?(?:[一二三四五六七八九十\d]{1,3}\s*[、．.]\s*)?完整终稿\s*$/.test(line.trim()));
  let raw = start >= 0
    ? lines.slice(start + 1)
    : lines.filter((line) => !/^(最终内容标题|最终标题|标题)[：:]/.test(line.trim()));
  if (bareAt < 0 && start >= 0) {
    let i = 0;
    while (i < raw.length && (!raw[i].trim() || /^(最终内容标题|最终标题|标题)\s*[：:]/.test(raw[i].trim()) || raw[i].trim() === "完整终稿")) i += 1;
    raw = raw.slice(i);
  }
  const text = raw.join("\n").trim();
  const title = sdLineValue(source, "最终内容标题") || sdLineValue(source, "最终标题") || sdLineValue(source, "标题");
  if (!text && !title) return null;
  return { title, text, rawBody: text };
}

function sdPickVersion(list, key) {
  if (!Array.isArray(list) || !list.length) return null;
  const picked = sdViewState[key];
  return list.find((item) => item.version === picked) || list[list.length - 1];
}

/* 这条内容的来源＝三种生产方式之一（和脚本创作卡片同一口径）：
   直接生产没有关联对象；选题分析后生产带关联选题；对标内容分析后生产带关联对标内容。 */
function ownContentSource(item, snapshot) {
  const key = String(item?.reference_content_key || "").trim();
  if (key) {
    const benchmark = (snapshot?.workflows?.insight?.benchmark_contents || [])
      .find((content) => content.key === key);
    // ⚠️ 对标内容详情页认的是 key（平台ID::内容ID），不是内容ID
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

/* 来源行里的标题是可点的：对标内容在 iframe 里换一篇详情，选题交给父窗口开抽屉 */
function sdSourceLinkHtml(source) {
  if (!source) return "";
  const title = escapeHtml(source.title);
  if (source.kind === "benchmark" && source.target) {
    const params = new URLSearchParams({
      kind: "benchmark",
      id: source.target,
      return_to: "index.html#creation-board",
      embed: "1",
    });
    return `<a class="sd-pos-source-link" href="content.html?${params.toString()}" title="打开这条对标内容">${title}</a>`;
  }
  if (source.kind === "topic" && source.target) {
    return `<a class="sd-pos-source-link" href="index.html#topics" data-sd-topic="${escapeAttribute(source.target)}" title="打开这个选题">${title}</a>`;
  }
  return `<b>${title}</b>`;
}

function buildOwnProduction(model) {
  const item = model.item || {};
  const sections = sdSplitHeadings(model.productionRecordContent || "", 2);

  const positioningVersions = sdVersionList(sdSection(sections, SD_STAGE_NAMES.定位)?.body || "")
    .map((version) => ({ version: version.version, ...sdPositionModel(version.body) }));
  const draftVersions = sdVersionList(sdSection(sections, SD_STAGE_NAMES.初稿)?.body || "")
    .map((version) => {
      const draftModel = sdDraftModel(version.body);
      return draftModel ? { version: version.version, ...draftModel } : null;
    })
    .filter(Boolean);
  const finalVersions = sdVersionList(sdSection(sections, SD_STAGE_NAMES.终稿)?.body || "")
    .map((version) => {
      const finalModel = sdFinalModel(version.body);
      return finalModel ? { version: version.version, ...finalModel } : null;
    })
    .filter(Boolean);
  const positioning = positioningVersions[positioningVersions.length - 1] || null;
  const draft = draftVersions[draftVersions.length - 1] || null;
  const final = finalVersions[finalVersions.length - 1] || null;
  const positioningDone = Boolean(positioning?.filled);
  const draftDone = Boolean(draft);
  const finalDone = Boolean(final);

  return {
    title: final?.title || item.title || "未命名内容",
    media: (item.media_format && item.media_format !== "未填写" ? item.media_format : positioning?.media) || "",
    source: ownContentSource(item, model.snapshot),
    positioningVersions,
    draftVersions,
    finalVersions,
    /* 审查报告按轮次存成 审查报告-vN.md，工作台要能回看每一轮，
       所以带的是全部版本，最新的排在最后。 */
    reviewVersions: Array.isArray(item.review_versions) && item.review_versions.length
      ? item.review_versions
      : (item.review_report ? [item.review_report] : []),
    positioningDone,
    draftDone,
    finalDone,
  };
}

function sdEmptyBox(title, detail) {
  return `<div class="sd-emptybox"><svg viewBox="0 0 24 24" aria-hidden="true"><rect x="4" y="3.5" width="16" height="17" rx="4"/><path d="M8 9h8M8 13h5"/></svg><b>${escapeHtml(title)}</b><p>${escapeHtml(detail)}</p></div>`;
}

/* 版本号一直露出来：只有一版就是静态的 v1 标签，有多版才给下拉切。
   之前只有 versions.length > 1 才显示，看着就像「版本号被删了」。 */
function sdVersionControl(versions, versionKey) {
  const list = Array.isArray(versions) ? versions : [];
  const current = sdPickVersion(list, versionKey);
  if (!current) return "";
  if (list.length < 2) return `<span class="sd-ver-tag">v${current.version}</span>`;
  return `<div class="sd-ver" data-sd-ver-wrap>
      <button class="sd-ver-btn" type="button" data-sd-ver-open>v${current.version}${SD_ICON.down}</button>
      <div class="sd-ver-pop">${list.slice().reverse().map((item) => `<button class="sd-ver-op${item.version === current.version ? " is-on" : ""}" type="button" data-sd-ver-key="${versionKey}" data-sd-ver="${item.version}">v${item.version}</button>`).join("")}</div>
    </div>`;
}

function sdHeadHtml({ icon, title, versions, versionKey, actions = "" }) {
  const switcher = sdVersionControl(versions, versionKey);
  return `<div class="sd-shead">
    <div class="sd-shl"><span class="sd-sicon">${icon}</span><div class="sd-stitle">${escapeHtml(title)}</div>${switcher}</div>
    ${actions ? `<div class="sd-shr">${actions}</div>` : ""}
  </div>`;
}

/* 内容定位 · 2026-10-31 重设计：
   页头（标题 + 三个规格）→ 主题主卡 → 目标用户到切口五项明细。 */

function sdRenderPosition(production) {
  const version = sdPickVersion(production.positioningVersions, "position");
  if (!version?.filled) {
    return `<div class="sd-panel">
      ${sdHeadHtml({ icon: SD_ICON.pin, title: "内容定位", versions: production.positioningVersions, versionKey: "position" })}
      <section class="sd-card">${sdEmptyBox("还没有内容定位", "把主题、目标用户、核心痛点、收益承诺、核心命题和切口定下来，才会往下写。")}</section>
    </div>`;
  }
  const specs = [
    ["媒介形式", version.media],
    ["内容类别", version.contentType],
    ["主目标侧重", version.primaryGoal],
  ];
  const rest = SD_FIELDS.filter(([key]) => key !== "theme");
  const no = (value) => String(value).padStart(2, "0");
  /* 定位这一版已经存下来了，下一步就是确认它去生成初稿。
     初稿已经有了就不再给这个入口 —— 那一步的下一步是看初稿，不是再确认一次定位。 */
  const cta = production.draftDone
    ? ""
    : `<button class="sd-btn is-primary sd-pos-cta" type="button" data-sd-confirm="position">+ 确认定位并生成初稿</button>`;
  return `<div class="sd-panel sd-pos">
    <header class="sd-pos-head">
      <div class="sd-pos-headrow">
        <div class="sd-pos-headmain">
          <div class="sd-pos-titlerow">
            <h1 class="sd-pos-title">${escapeHtml(production.title || version.theme || "内容定位")}</h1>
            ${sdVersionControl(production.positioningVersions, "position")}
          </div>
          ${production.source ? `<p class="sd-pos-source"><span>${production.source.label}</span>${sdSourceLinkHtml(production.source)}</p>` : ""}
        </div>
        ${cta}
      </div>
      <dl class="sd-pos-chips">${specs.map(([label, value]) => `<div class="sd-pos-chip"><dt>${label}</dt><dd${value ? "" : ' class="is-empty"'}>${escapeHtml(value || "未填写")}</dd></div>`).join("")}</dl>
    </header>
    <section class="sd-pos-hero">
      <div class="sd-pos-fhead">
        <span class="sd-pos-no">01</span>
        <h2 class="sd-pos-name">主题</h2>
        <span class="sd-pos-hint">这条内容主要讲什么</span>
      </div>
      <p class="sd-pos-body${version.theme ? "" : " is-empty"}">${escapeHtml(version.theme || "未填写")}</p>
    </section>
    <dl class="sd-pos-list">${rest.map(([key, label, hint], index) => `<div class="sd-pos-row${key === "angle" ? " is-accent" : ""}">
      <dt>
        <span class="sd-pos-namerow"><span class="sd-pos-no">${no(index + 2)}</span><span class="sd-pos-name">${label}</span></span>
        <span class="sd-pos-hint">${hint}</span>
      </dt>
      <dd><p class="sd-pos-body${version[key] ? "" : " is-empty"}">${escapeHtml(version[key] || "未填写")}</p></dd>
    </div>`).join("")}</dl>
  </div>`;
}

/* ---- 手动编辑初稿 / 终稿 ----
   编辑区里放的是原始正文（含【分段名】标记），不是解析出来的分段。
   保存时由服务端按「## 章节 → ### vN → 完整初稿以下」精确定位替换，
   只动这一版，前面的内容定位、框架表和别的版本一个字节都不改。 */
const sdEditState = {
  section: "", version: 0, title: "", body: "",
  saving: false, error: "",
  /* 切 tab 时把改动暂存在这里：dirty 为真、且是同一节同一版，再点编辑就接着改，
     不会被打回服务端读到的原文 */
  dirty: false, dirtySection: "",
};

const SD_SAVE_ERROR = {
  content_not_found: "找不到这条内容，可能已被移动",
  content_record_missing: "这条内容还没有内容生产记录",
  content_stage_missing: "记录里没有「内容初稿」或「内容终稿」这一节",
  content_marker_missing: "记录里找不到「完整初稿 / 完整终稿」这一行，结构可能被改过",
  content_version_missing: "找不到要改的那一版",
  content_table_missing: "找不到 我方资料/我方内容库.csv",
  content_table_empty: "我方内容库.csv 只有表头，没有内容行",
  content_table_broken: "我方内容库.csv 缺少「内容ID」列",
  content_row_missing: "内容库里找不到这条内容",
  invalid_payload: "提交的内容不完整",
};

function sdEditModel(section) {
  const production = window.contentArchiveModel?.productionData;
  if (!production || !section) return null;
  const list = section === "final" ? production.finalVersions : production.draftVersions;
  const version = sdPickVersion(list, section);
  if (!version) return null;
  return { production, version, list };
}

function sdOpenEditor(section) {
  const model = sdEditModel(section);
  const shell = window.contentArchiveModel;
  if (!model || !shell) return;
  const resume = sdEditState.dirty
    && sdEditState.dirtySection === section
    && sdEditState.version === model.version.version;
  sdEditState.section = section;
  sdEditState.version = model.version.version;
  if (!resume) {
    sdEditState.title = model.version.title || "";
    sdEditState.body = model.version.rawBody || "";
  }
  sdEditState.error = "";
  sdEditState.saving = false;
  renderOwnView(shell);
}

function sdCloseEditor() {
  sdEditState.section = "";
  sdEditState.error = "";
  sdEditState.saving = false;
  sdEditState.dirty = false;
  sdEditState.dirtySection = "";
  const shell = window.contentArchiveModel;
  if (shell) renderOwnView(shell);
}

function sdRenderEditor({ section, icon, label, versionKey }) {
  const model = sdEditModel(section);
  if (!model) return "";
  return `<div class="sd-panel">
    ${sdHeadHtml({ icon, title: `${label} · 编辑中`, versions: model.list, versionKey })}
    <section class="sd-card">
      <div class="sd-edit">
        <div class="sd-edit-row">
          <span class="sd-edit-k">标题</span>
          <input class="sd-edit-in" type="text" data-edit="title" value="${escapeAttribute(sdEditState.title)}" placeholder="内容标题">
        </div>
        <div class="sd-edit-row is-block">
          <span class="sd-edit-k">正文</span>
          <textarea class="sd-edit-area" data-edit="body" spellcheck="false">${escapeHtml(sdEditState.body)}</textarea>
          <span class="sd-edit-note">【分段名】这些标记留着，它是分段依据；删了整篇会被当成一段。</span>
        </div>
        ${sdEditState.error ? `<p class="sd-edit-err">${escapeHtml(sdEditState.error)}</p>` : ""}
        <div class="sd-edit-foot">
          <span class="sd-edit-hint">保存后直接写回 内容生产记录.md，只改这一版</span>
          <button class="sd-btn" type="button" data-sd-edit="cancel">取消</button>
          <button class="sd-btn is-primary" type="button" data-sd-edit="save"${sdEditState.saving ? " disabled" : ""}>${sdEditState.saving ? "保存中…" : "保存"}</button>
        </div>
      </div>
    </section>
  </div>`;
}

/* 把输入框里的值读回 state。切 tab、保存失败重渲都要先走这一步，
   否则用户刚打的字会跟着 DOM 一起没。 */
function sdCaptureEditor() {
  const pick = (name, fallback) => {
    const node = document.querySelector(`[data-edit="${name}"]`);
    return node ? node.value : fallback;
  };
  sdEditState.title = pick("title", sdEditState.title);
  sdEditState.body = pick("body", sdEditState.body);
  sdEditState.dirty = true;
  sdEditState.dirtySection = sdEditState.section;
}

async function sdSaveEditor() {
  const section = sdEditState.section;
  const model = sdEditModel(section);
  if (!section || !model) return;
  sdCaptureEditor();
  const title = sdEditState.title;
  const body = sdEditState.body;
  if (!body.trim()) {
    sdEditState.error = "正文不能空";
    renderOwnView(window.contentArchiveModel);
    return;
  }

  sdEditState.saving = true;
  sdEditState.error = "";
  renderOwnView(window.contentArchiveModel);
  try {
    const response = await fetch("/api/content/script", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ id: contentId, section, version: model.version.version, title, body }),
    });
    const data = await response.json().catch(() => ({}));
    if (!response.ok || !data.ok) throw new Error(SD_SAVE_ERROR[data.error] || data.error || "服务端没有返回原因");
    sdEditState.section = "";
    sdEditState.saving = false;
    sdEditState.dirty = false;
    sdEditState.dirtySection = "";
    await loadContent({ force: true });
    showToast("已保存");
  } catch (error) {
    sdEditState.saving = false;
    sdEditState.error = `没保存成功：${error.message}`;
    renderOwnView(window.contentArchiveModel);
  }
}

/* 复制一段文本。抽屉是 iframe，剪贴板权限可能被拦，要留一条选中兜底。 */
function sdCopyText(text, okMessage) {
  const fallback = () => {
    const sink = document.createElement("textarea");
    sink.value = text;
    sink.setAttribute("readonly", "");
    sink.className = "sd-copy-sink";
    document.body.appendChild(sink);
    sink.select();
    try {
      if (document.execCommand("copy")) showToast(okMessage);
      else showToast("复制失败，请手动选中后再复制");
    } catch (_error) {
      showToast("复制失败，请手动选中后再复制");
    } finally {
      sink.remove();
    }
  };
  if (navigator.clipboard?.writeText) {
    navigator.clipboard.writeText(text).then(() => showToast(okMessage)).catch(fallback);
  } else {
    fallback();
  }
}

/* 初稿上的 AI 审查：只出指令，用户复制去对话框跑审查流程 */
function sdAuditPrompt(production, section) {
  const stage = section === "final" ? "内容终稿" : "内容初稿";
  return [
    `用 create-content 对这条内容的${stage}做一次内容审查。`,
    "",
    `内容：${production.title || "（未命名）"}`,
    `内容ID：${contentId}`,
    "",
    "按内容审查流程跑一遍：只出报告，不改稿、不生成新版本。",
  ].join("\n");
}

/* 只出指令的动作统一走这个弹窗：先让人看清要 AI 做什么，再决定复制。
   指令来自 sdAuditPrompt，弹窗里显示的就是要粘进对话框的原文。 */
const sdPromptDialogState = { text: "" };

function sdOpenPromptDialog({ title, copy, text }) {
  /* 装在工作台抽屉里时，交给父窗口弹：iframe 里的 dialog 只能盖住抽屉那一块，
     页面居中的效果出不来（2026-11-02 用户指出）。 */
  if (document.documentElement.classList.contains("is-embed")) {
    window.parent.postMessage({ type: "meiling:prompt-dialog", payload: { title, copy, text } }, location.origin);
    return;
  }
  const dialog = document.querySelector("#sd-prompt-dialog");
  if (!dialog) return;
  sdPromptDialogState.text = text;
  const titleNode = dialog.querySelector("#sd-dlg-title");
  const copyNode = dialog.querySelector("#sd-dlg-copy");
  const textNode = dialog.querySelector("#sd-dlg-text");
  if (titleNode) titleNode.textContent = title;
  if (copyNode) copyNode.textContent = copy;
  if (textNode) textNode.textContent = text;
  if (!dialog.open) {
    dialog.showModal();
    /* showModal 会自动聚焦第一个可聚焦元素（右上角 ×），一进来就是一圈焦点环；
       把焦点收回到弹窗本身，键盘照样能 Tab 到按钮。 */
    dialog.setAttribute("tabindex", "-1");
    dialog.focus();
  }
}

function sdClosePromptDialog() {
  const dialog = document.querySelector("#sd-prompt-dialog");
  if (dialog?.open) dialog.close();
}

/* 要真写数据的动作统一走确认弹窗：点按钮先问一句，用户点「确认」才继续，
   点「取消」或关掉弹窗什么都不做。装在工作台抽屉里时同样交给父窗口弹。 */
const sdConfirmState = { resolve: null };

function sdAskConfirm({ title, copy, confirmText = "确认" }) {
  if (document.documentElement.classList.contains("is-embed")) {
    return new Promise((resolve) => {
      sdConfirmState.resolve = resolve;
      window.parent.postMessage({ type: "meiling:confirm-dialog", payload: { title, copy, confirmText } }, location.origin);
    });
  }
  const dialog = document.querySelector("#sd-confirm-dialog");
  if (!dialog) return Promise.resolve(false);
  const titleNode = dialog.querySelector("#sd-cfm-title");
  const copyNode = dialog.querySelector("#sd-cfm-copy");
  const okNode = dialog.querySelector("#sd-cfm-ok");
  if (titleNode) titleNode.textContent = title;
  if (copyNode) copyNode.textContent = copy;
  if (okNode) okNode.textContent = confirmText;
  if (!dialog.open) {
    dialog.showModal();
    dialog.setAttribute("tabindex", "-1");
    dialog.focus();
  }
  return new Promise((resolve) => {
    sdConfirmState.resolve = resolve;
  });
}

function sdCloseConfirm(confirmed) {
  const dialog = document.querySelector("#sd-confirm-dialog");
  const resolve = sdConfirmState.resolve;
  sdConfirmState.resolve = null;
  if (dialog?.open) dialog.close();
  if (resolve) resolve(confirmed);
}

window.addEventListener("message", (event) => {
  if (event.origin !== location.origin) return;
  if (event.data?.type !== "meiling:confirm-dialog-result") return;
  const resolve = sdConfirmState.resolve;
  sdConfirmState.resolve = null;
  if (resolve) resolve(Boolean(event.data.confirmed));
});

document.querySelector("#sd-confirm-dialog")?.addEventListener("cancel", (event) => {
  event.preventDefault();
  sdCloseConfirm(false);
});

/* 定位页的「+ 确认定位并生成初稿」：也只出指令，不写任何数据。
   定位版本号写进指令，让 AI 确认的是屏幕上这一版，不会重新生成一份新的定位。 */
function sdConfirmPrompt(production) {
  const version = sdPickVersion(production.positioningVersions, "position");
  const lines = [
    "用 create-content 继续这条内容：内容定位就按当前这一版确认，接着生成内容初稿。",
    "",
    `内容：${production.title || "（未命名）"}`,
    `内容ID：${contentId}`,
  ];
  if (version) lines.push(`定位版本：v${version.version}`);
  lines.push(
    "",
    "按流程走：",
    "1. 内容定位流程 2.5 提交 confirm，不重新生成定位、不改定位字段。",
    "2. 内容初稿流程 2.2 按已确认的定位生成框架和完整初稿，保存成新版本后再展示给我。",
  );
  return lines.join("\n");
}

function sdTextLength(text) {
  return String(text || "").replace(/\s+/g, "").length;
}

function sdRenderDraft(production) {
  const version = sdPickVersion(production.draftVersions, "draft");
  if (!version) {
    return `<div class="sd-panel">
      ${sdHeadHtml({ icon: SD_ICON.doc, title: "内容初稿" })}
      <section class="sd-card">${sdEmptyBox("还没有内容初稿", "内容定位确认后，才会生成内容框架和完整初稿。")}</section>
    </div>`;
  }
  if (sdEditState.section === "draft") {
    return sdRenderEditor({ section: "draft", icon: SD_ICON.doc, label: "内容初稿", versionKey: "draft" });
  }
  const chars = version.segments.reduce((total, segment) => total + sdTextLength(segment.text), 0);
  const actions = `<button class="sd-btn" type="button" data-sd-edit="open" data-sd-target="draft">${SD_ICON.edit}编辑</button><button class="sd-btn is-dark" type="button" data-sd-audit="draft">${SD_ICON.shield}AI 审查</button>`;
  return `<div class="sd-panel">
    ${sdHeadHtml({ icon: SD_ICON.doc, title: "内容初稿", versions: production.draftVersions, versionKey: "draft", actions })}
    <section class="sd-card">
      <div class="sd-draft">
        <div class="sd-title-row">
          <div><div class="sd-k">标题</div><div class="sd-tf-main">${escapeHtml(version.title || "未命名")}</div></div>
        </div>
        ${version.overview ? `<div class="sd-overview"><b>推进主线</b>${escapeHtml(version.overview)}</div>` : ""}
        <div class="sd-hline"></div>
        ${version.segments.map((segment) => `<div class="sd-seg">
          <div class="sd-seg-head"><span class="sd-seg-tag">${segment.time ? `<span class="sd-seg-time">${escapeHtml(segment.time)}</span>` : ""}<span class="sd-seg-name">${escapeHtml(segment.name)}</span></span></div>
          ${segment.line ? `<div class="sd-seg-line">${escapeHtml(segment.line)}</div>` : ""}
          <div class="sd-seg-text">${escapeHtml(segment.text)}</div>
        </div>`).join("")}
        <div class="sd-dfoot"><span>${version.segments.length} 段 · 约 ${chars} 字</span></div>
      </div>
    </section>
  </div>`;
}

/* 平台依据一格写成「《政策名》 链接 —— 要点」，这里拆回三段，
   政策名和链接要单独渲染（链接可点），要点做小字说明。 */
function sdParseBasis(text) {
  const raw = String(text || "").trim();
  if (!raw) return null;
  const policy = raw.match(/《([^》]+)》/)?.[1] || "";
  const link = raw.match(/https?:\/\/[^\s，。；;）)】]+/)?.[0] || "";
  const point = raw
    .replace(/《[^》]+》/, "")
    .replace(link, "")
    .replace(/^[\s·—\-–；;，,：:]+/, "")
    .replace(/^要点[：:]\s*/, "")
    .trim();
  if (!policy && !link && !point) return null;
  return { policy, link, point };
}

/* 平台规范审查：老报告是「涉及原文 / 政策名称 / 官方链接 / 政策要点 / 风险说明」，
   新报告跟通用审查同一套「位置 / 原文 / 问题 / 政策依据 / 可怎么处理」。
   两种都认，统一成 { where, src, issue, basis, fix }，页面上就是同一张表。 */
function sdReviewPlatforms(content) {
  const headings = [...content.matchAll(/^##\s+(.+?)\s*$/gm)];
  const start = headings.findIndex((match) => /平台规范审查/.test(match[1]));
  if (start < 0) return { platforms: [], sample: false };
  const section = content.slice(headings[start].index + headings[start][0].length, headings[start + 1]?.index ?? content.length);
  const subs = [...section.matchAll(/^###\s+(.+?)\s*$/gm)];
  const platforms = subs.map((match, index) => {
    const body = section.slice(match.index + match[0].length, subs[index + 1]?.index ?? section.length);
    const table = sdTableRows(body);
    /* 看表头判断是哪一代格式：老的是「涉及原文 / 政策名称 / 官方链接 / 政策要点 / 风险说明」，
       新的是「位置 / 原文 / 问题 / 政策依据 / 可怎么处理」。老报告也要能正常打开。 */
    const header = table.find((cells) => /^(位置|涉及原文)$/.test(cells[0] || "")) || [];
    const legacy = /涉及原文|政策名称|风险说明/.test(header.join("|"));
    return {
      name: sdCleanCell(match[1]).replace(/^平台[：:]\s*/, ""),
      status: sdMetaValue(body, "检索状态"),
      at: sdMetaValue(body, "检索时间"),
      rows: table
        .filter((cells) => cells !== header && cells.length >= 4 && !/^(涉及原文|位置)$/.test(cells[0]))
        .map((cells) => (legacy
          ? { where: "", src: cells[0], issue: cells[4] || "", fix: "", basis: sdParseBasis(`《${cells[1]}》 ${cells[2]} —— ${cells[3]}`) }
          : { where: cells[0], src: cells[1], issue: cells[2], fix: cells[4] || "", basis: sdParseBasis(cells[3]) })),
    };
  }).filter((platform) => platform.name && platform.rows.length);
  return { platforms, sample: /模拟示例|示例数据|模拟|示例/.test(section) };
}

function sdLink(url, fallback) {
  try {
    const target = new URL(String(url || "").trim());
    if (!/^https?:$/.test(target.protocol)) return escapeHtml(fallback || url || "");
    return `<a href="${escapeAttribute(target.href)}" target="_blank" rel="noreferrer noopener">${escapeHtml(fallback || target.hostname.replace(/^www\./, ""))}</a>`;
  } catch (_error) {
    return escapeHtml(fallback || url || "");
  }
}

function sdReviewModel(report) {
  const content = report?.content || "";
  const headings = [...content.matchAll(/^##\s+(.+?)\s*$/gm)];
  const start = headings.findIndex((match) => /通用审查/.test(match[1]));
  const body = start >= 0
    ? content.slice(headings[start].index + headings[start][0].length, headings[start + 1]?.index ?? content.length)
    : content;
  const rows = sdTableRows(body)
    .filter((cells) => cells.length >= 4 && cells[0] !== "位置")
    .map((cells) => ({ where: cells[0], src: cells[1], issue: cells[2], fix: cells[3] }));
  const platformReview = sdReviewPlatforms(content);
  return {
    conclusion: sdMetaValue(content, "审查结论"),
    summary: sdMetaValue(content, "一句话判断"),
    rows,
    platforms: platformReview.platforms,
    platformSample: platformReview.sample,
  };
}

/* ---- 内容审查的采纳状态 ----
   存在浏览器本地：key 带内容 ID 和报告版本，切版本各记各的，
   报告更新一版也不会沿用旧决定。 */
const REVIEW_KEY_PREFIX = "meiling:review:";

function sdReviewKey(production, version) {
  return `${REVIEW_KEY_PREFIX}${contentId}:${version || 0}`;
}

function sdReadReviewState(production, version) {
  try {
    const raw = JSON.parse(localStorage.getItem(sdReviewKey(production, version)) || "{}");
    return raw && typeof raw === "object" ? raw : {};
  } catch (_error) {
    return {};
  }
}

function sdWriteReviewState(production, version, state) {
  try {
    localStorage.setItem(sdReviewKey(production, version), JSON.stringify(state));
  } catch (_error) {
    /* 隐私模式下写不进去，界面仍可操作，只是刷新后丢失 */
  }
}

/* 通用审查和平台规范审查的行摆在同一张待办清单里：
   通用用 g-序号，平台用 p-平台序号-行序号，采纳状态互不干扰。 */
function sdReviewItems(review) {
  const items = review.rows.map((row, index) => ({ id: `g-${index}`, platform: "", row }));
  review.platforms.forEach((platform, pIndex) => {
    platform.rows.forEach((row, rIndex) => {
      items.push({ id: `p-${pIndex}-${rIndex}`, platform: platform.name, row });
    });
  });
  return items;
}

/* 生成给对话框用的提示词：只带被采纳的那几条，原文和改法都抄进去，
   让 AI 不用回头翻报告就能改。平台那几条多带一行政策依据。 */
function sdReviewPrompt(production, review, version, state) {
  const adopted = sdReviewItems(review).filter((item) => state[item.id] === "adopted");
  const head = [
    "按下面这份内容审查意见修改内容初稿。",
    "",
    `内容：${production.title || "（未命名）"}`,
    `审查报告：v${version}`,
    `采纳意见：${adopted.length} 条`,
    "",
    "要求：",
    "1. 只改下面列出的这几条，没提到的段落和表达不要动。",
    "2. 保留原来的分段时间轴和顺序。",
    "3. 平台规范那几条按写明的依据调整表达，不新增事实、案例或数据。",
    "4. 改完输出完整初稿，不要只给修改片段。",
    "",
  ].join("\n");
  const body = adopted.map((item, index) => {
    const basis = item.row.basis;
    return [
      `${index + 1}. ${item.platform ? `平台：${item.platform} · ` : ""}位置：${item.row.where || "—"}`,
      item.row.src ? `   原文：${item.row.src}` : "",
      `   问题：${item.row.issue || "—"}`,
      basis ? `   依据：${[basis.policy ? `《${basis.policy}》` : "", basis.link, basis.point ? `要点：${basis.point}` : ""].filter(Boolean).join(" ")}` : "",
      item.row.fix ? `   怎么改：${item.row.fix}` : "",
    ].filter(Boolean).join("\n");
  }).join("\n\n");
  return `${head}\n${body}`;
}

/* 「通过审查确认终稿」：用户确认后，把当前这一版初稿原样写进终稿，不经过 AI。 */
async function sdCopyDraftToFinal(version) {
  if (!window.contentArchiveModel?.productionData) return;
  try {
    const response = await fetch("/api/content/confirm-final", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ id: contentId, version }),
    });
    const data = await response.json().catch(() => ({}));
    if (!response.ok || !data.ok) throw new Error(SD_SAVE_ERROR[data.error] || data.error || "服务端没有返回原因");
    sdViewState.tab = "final";
    history.replaceState(null, "", "#final");
    await loadContent({ force: true });
    showToast("终稿已生成，内容状态已改为已完成");
  } catch (error) {
    showToast(`没生成终稿：${error.message}`);
  }
}

/* 平台依据挂在「问题」单元格下面一行小字，表格本身和通用审查同一套列宽 */
function sdBasisHtml(basis) {
  if (!basis) return "";
  const parts = [
    basis.policy ? `《${escapeHtml(basis.policy)}》` : "",
    basis.link ? sdLink(basis.link) : "",
    basis.point ? `要点：${escapeHtml(basis.point)}` : "",
  ].filter(Boolean);
  if (!parts.length) return "";
  return `<div class="sd-rev-basis">${parts.join('<span class="sd-rev-dot">·</span>')}</div>`;
}

function sdRenderReview(production) {
  const versions = production.reviewVersions || [];
  const current = sdPickVersion(versions, "review");
  if (!current?.content) {
    return `<div class="sd-panel">
      ${sdHeadHtml({ icon: SD_ICON.shield, title: "内容审查", versions, versionKey: "review" })}
      <section class="sd-card">${sdEmptyBox("还没有内容审查", "内容初稿确认后跑一次审查，问题和改法会列在这里。")}</section>
    </div>`;
  }
  const version = current.version || 0;
  const review = sdReviewModel(current);
  const items = sdReviewItems(review);
  const state = sdReadReviewState(production, version);
  const adopted = items.filter((item) => state[item.id] === "adopted").length;
  const bad = review.rows.length > 0
    || /发现\s*[1-9]/.test(review.conclusion)
    || (/问题/.test(review.conclusion) && !/未发现|无问题/.test(review.conclusion));
  const title = review.conclusion || (bad ? `发现 ${review.rows.length} 个问题` : "审查已通过");

  /* 页面上的动作有两个：把采纳的几条拼成指令交给对话框去改初稿；
     或者审查通过、不按建议调整，直接确认当前初稿进终稿（推进动作，始终在）。
     没勾选时 AI 调整照样在，点了提示先勾，不做成灰掉按不动的样子。 */
  const adjustBtn = items.length
    ? `<button class="sd-btn" type="button" data-sd-adjust="review"${adopted ? "" : ' data-sd-need="1"'}>${SD_ICON.edit}AI 调整初稿</button>`
    : "";
  const actions = `${adjustBtn}<button class="sd-btn is-dark" type="button" data-sd-confirm-final>${SD_ICON.flag}通过审查确认终稿</button>`;

  const rowActions = (id) => {
    const currentState = state[id];
    /* 定下来的状态只报状态 + 撤销：撤销等于回到未处理，再从「采纳 / 忽略」重选，不在同一行挤第二个决定 */
    if (currentState === "adopted") {
      return `<span class="sd-rev-done">${SD_ICON.check}已采纳</span><button class="sd-rev-link" type="button" data-rev="undo" data-row="${id}">撤销</button>`;
    }
    if (currentState === "skipped") {
      return `<span class="sd-rev-muted">${SD_ICON.x}已忽略</span><button class="sd-rev-link" type="button" data-rev="undo" data-row="${id}">撤销</button>`;
    }
    return `<button class="sd-rev-btn is-on" type="button" data-rev="adopt" data-row="${id}">${SD_ICON.check}采纳</button><button class="sd-rev-btn" type="button" data-rev="skip" data-row="${id}">${SD_ICON.x}忽略</button>`;
  };

  /* 通用审查和平台规范审查共用一个行模板，保证列、宽度、采纳按钮完全一致 */
  const rowHtml = (row, id) => `<tr>
    <td class="sd-c-loc">${escapeHtml(row.where || "—")}</td>
    <td class="sd-c-src">${escapeHtml(row.src)}</td>
    <td class="sd-c-issue">${escapeHtml(row.issue)}${sdBasisHtml(row.basis)}</td>
    <td class="sd-c-fix">${escapeHtml(row.fix || "—")}</td>
    <td class="sd-c-act">${rowActions(id)}</td>
  </tr>`;

  const reviewTable = (rows) => `<div class="sd-table-wrap"><table class="sd-table sd-table-rev">
    <thead><tr><th class="sd-c-loc">位置</th><th class="sd-c-src">原文</th><th>问题</th><th class="sd-c-fix">可怎么处理</th><th class="sd-c-act">采纳</th></tr></thead>
    <tbody>${rows.join("")}</tbody>
  </table></div>`;

  const platformCards = review.platforms.map((platform, pIndex) => `<div class="sd-plat">
      <div class="sd-plat-h">
        <span class="sd-plat-n">${escapeHtml(platform.name)}</span>
        ${platform.status ? `<span class="sd-chip${/已完成/.test(platform.status) ? " is-ok" : ""}">${escapeHtml(platform.status)}</span>` : ""}
        ${platform.at ? `<span class="sd-plat-at">检索时间 ${escapeHtml(platform.at)}</span>` : ""}
      </div>
      ${reviewTable(platform.rows.map((row, rIndex) => rowHtml(row, `p-${pIndex}-${rIndex}`)))}
    </div>`).join("");

  return `<div class="sd-panel">
    ${sdHeadHtml({ icon: SD_ICON.shield, title: "内容审查", versions, versionKey: "review", actions })}
    <section class="sd-card">
      <div class="sd-verdict${bad ? "" : " is-ok"}">
        <span class="sd-vico">${bad ? SD_ICON.warn : SD_ICON.check}</span>
        <div><div class="sd-vt">${escapeHtml(title)}</div>${review.summary ? `<div class="sd-vd">${escapeHtml(review.summary)}</div>` : ""}</div>
      </div>
      ${review.rows.length
        ? reviewTable(review.rows.map((row, index) => rowHtml(row, `g-${index}`)))
        : sdEmptyBox("这份报告没有列出具体问题", review.summary || "审查没有发现需要修改的地方。")}
    </section>
    ${review.platforms.length ? `<section class="sd-card sd-plat-card">
      <div class="sd-plat-head">
        <span class="sd-plat-t">平台规范审查</span>
        ${review.platformSample ? '<span class="sd-chip">示例数据 · 未做真实检索</span>' : ""}
      </div>
      ${platformCards}
    </section>` : ""}
  </div>`;
}


function sdRenderFinal(production) {
  const version = sdPickVersion(production.finalVersions, "final");
  if (!version) {
    return `<div class="sd-panel">
      ${sdHeadHtml({ icon: SD_ICON.flag, title: "内容终稿" })}
      <section class="sd-card">${sdEmptyBox("还没有内容终稿", "内容初稿确认后，才会生成最终版本。")}</section>
    </div>`;
  }
  const chars = sdTextLength(version.text);
  const seconds = Math.round(chars / 4.5);
  const duration = `${Math.floor(seconds / 60)} 分 ${String(seconds % 60).padStart(2, "0")} 秒`;
  const meta = [production.media && production.media !== "未填写" ? production.media : "内容", `口播约 ${duration}`, `正文 ${chars} 字`].join(" · ");
  if (sdEditState.section === "final") {
    return sdRenderEditor({ section: "final", icon: SD_ICON.flag, label: "内容终稿", versionKey: "final" });
  }
  const actions = [
    version.text ? `<button class="sd-btn" type="button" data-sd-copy>${SD_ICON.copy}复制全文</button>` : "",
    `<button class="sd-btn" type="button" data-sd-edit="open" data-sd-target="final">${SD_ICON.edit}编辑</button>`,
  ].join("");
  return `<div class="sd-panel">
    ${sdHeadHtml({ icon: SD_ICON.flag, title: "内容终稿", versions: production.finalVersions, versionKey: "final", actions })}
    <section class="sd-card">
      <div class="sd-final-pad">
        <div class="sd-k">标题</div>
        <div class="sd-ftitle">${escapeHtml(version.title || "未命名")}</div>
        <div class="sd-hline sp"></div>
        <div class="sd-ftext">${escapeHtml(version.text || "")}</div>
        <div class="sd-fmeta">${escapeHtml(meta)}</div>
      </div>
    </section>
  </div>`;
}

/* 四个 tab 常显：以前「内容审查」要等有报告才出现，导致生产中的内容
   一眼看不出后面还有审查这一步。没报告时走空态说明，不给假结论。 */
function sdVisibleTabs() {
  return SD_TABS;
}

function sdTabDone(production, key) {
  if (key === "position") return production.positioningDone;
  if (key === "draft") return production.draftDone;
  if (key === "review") return Boolean(production.reviewVersions?.length);
  return production.finalDone;
}

function sdResolveTab(production) {
  const tabs = sdVisibleTabs(production);
  const hash = String(location.hash || "").replace(/^#/, "");
  if (tabs.some((tab) => tab.key === hash)) return hash;
  if (tabs.some((tab) => tab.key === sdViewState.tab)) return sdViewState.tab;
  return "position";
}

function renderOwnView(model, production = model.productionData) {
  markNavActive("creation-board");
  const { label } = model;
  if (!production) {
    elements.view.innerHTML = '<div class="content-loading"><span></span><p>正在读取内容生产记录</p></div>';
    return;
  }
  applyCrumb(label, production.title);
  document.title = `${production.title}｜媒灵内容档案`;

  const tabs = sdVisibleTabs(production);
  const active = sdResolveTab(production);
  const tabsHtml = `<div class="sd-tabsbar"><div class="sd-tabs" role="tablist" aria-label="内容生产环节">${tabs.map((tab) => `<button class="sd-tab${tab.key === active ? " is-on" : ""}" type="button" role="tab" aria-selected="${tab.key === active}" data-sd-tab="${tab.key}"><i class="sd-dot${sdTabDone(production, tab.key) ? " is-done" : ""}"></i><b>${escapeHtml(tab.name)}</b></button>`).join("")}</div></div>`;
  const panel = active === "draft"
    ? sdRenderDraft(production)
    : active === "review"
      ? sdRenderReview(production)
      : active === "final"
        ? sdRenderFinal(production)
        : sdRenderPosition(production);
  elements.view.innerHTML = `<div class="sd-page">${tabsHtml}${panel}</div>`;
}

/* ==================================================================
   对标内容 · 详情页
   —— 版式：照 2026-10-27 设计稿（概要 + 01 学习结论 + 02 内容拆解 + 03 观众反馈 + 资料抽屉）
   —— 数据源：内容分析.md（兼容新旧三种结构）/ 视频文案.md / 评论.md
   ================================================================== */

const BD_HEART = '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 20.3s-6.8-4.4-6.8-9.1A3.9 3.9 0 0 1 12 8.4a3.9 3.9 0 0 1 6.8 2.8c0 4.7-6.8 9.1-6.8 9.1z"/></svg>';
const BD_ARROW = '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M5 12h11M12.5 6.5 18 12l-5.5 5.5"/></svg>';
const BD_CLOSE = '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M6 6l12 12M18 6L6 18"/></svg>';

function bdText(value) {
  return String(value ?? "")
    .replace(/<br\s*\/?>/gi, " ")
    .replace(/<[^>]*>/g, "")
    .replace(/&nbsp;/g, " ")
    .replace(/&amp;/g, "&")
    .replace(/&lt;/g, "<")
    .replace(/&gt;/g, ">")
    .replace(/\*\*/g, "")
    .replace(/`/g, "")
    .replace(/^\s*>\s?/gm, "")
    .replace(/\s+/g, " ")
    .trim();
}

function bdTitle(value) {
  const cleaned = String(value || "")
    .replace(/\r?\n/g, " ")
    .replace(/(^|\s)#[^\s#]+/g, "$1")
    .replace(/\s+/g, " ")
    .trim();
  return cleaned || String(value || "").trim() || "未命名内容";
}

function bdSections(markdown) {
  const text = String(markdown || "").replace(/\r\n?/g, "\n");
  const matches = [...text.matchAll(/^##\s+(.+?)\s*$/gm)];
  return matches.map((match, index) => ({
    title: bdText(match[1]),
    body: text.slice(match.index + match[0].length, matches[index + 1]?.index ?? text.length).trim(),
  }));
}

function bdSubSections(body) {
  const text = String(body || "");
  const matches = [...text.matchAll(/^###\s+(.+?)\s*$/gm)];
  return matches.map((match, index) => ({
    title: bdText(match[1]),
    body: text.slice(match.index + match[0].length, matches[index + 1]?.index ?? text.length).trim(),
  }));
}

function bdFindSection(sections, ...keywords) {
  return sections.find((section) => keywords.some((keyword) => section.title.includes(keyword))) || null;
}

function bdFindBlock(sections, keyword) {
  for (const section of sections) {
    if (section.title.includes(keyword)) return section;
    const found = bdSubSections(section.body).find((block) => block.title.includes(keyword));
    if (found) return found;
  }
  return null;
}

function bdTable(body) {
  const lines = String(body || "").split("\n").map((line) => line.trim()).filter((line) => line.startsWith("|"));
  if (lines.length < 2) return null;
  const rows = lines.map((line) => {
    const inner = line.endsWith("|") ? line.slice(1, -1) : line.slice(1);
    return inner.split("|").map((cell) => cell.trim());
  });
  const headers = rows[0].map(bdText);
  const dataRows = rows.slice(1).filter((row) => !row.every((cell) => /^[-: ]+$/.test(cell)));
  return { headers, rows: dataRows };
}

function bdBullets(body) {
  return String(body || "")
    .split("\n")
    .map((line) => line.trim())
    .filter((line) => /^[-*]\s+/.test(line))
    .map((line) => bdText(line.replace(/^[-*]\s+/, "")))
    .filter(Boolean);
}

function bdBulletValue(body, label) {
  for (const line of String(body || "").split("\n")) {
    const match = line.trim().match(/^[-*]\s*([^：:]+)[：:]\s*(.*)$/);
    if (match && match[1].trim() === label) return bdText(match[2]);
  }
  return "";
}

/* 第 2 节：最值得学什么（总述 / 为什么 / 可以直接用 / 别照搬） */
function bdStudyBlock(body) {
  const out = { lead: "", why: [], plus: [], minus: [] };
  let mode = "";
  for (const raw of String(body || "").split("\n")) {
    const line = raw.trim();
    if (!line) continue;
    const bold = line.match(/^\*\*(.+?)\*\*$/);
    if (bold) {
      const label = bdText(bold[1]);
      if (!out.lead && !mode) {
        out.lead = label;
        mode = "why";
        continue;
      }
      if (/可以直接用/.test(label)) {
        mode = "plus";
        continue;
      }
      if (/别照搬|不能照搬|不能忽略/.test(label)) {
        mode = "minus";
        continue;
      }
      if (/优先借鉴/.test(label)) {
        const rest = label.replace(/^优先借鉴[：:]\s*/, "");
        if (rest) out.why.push(rest);
        mode = "why";
        continue;
      }
      if (mode === "why") out.why.push(label);
      continue;
    }
    const bullet = line.match(/^[-*]\s+(.+)$/);
    if (mode === "plus" && bullet) out.plus.push(bdText(bullet[1]));
    else if (mode === "minus" && bullet) out.minus.push(bdText(bullet[1]));
    else if (mode === "why") out.why.push(bdText(line));
    else if (!out.lead) out.lead = bdText(line);
  }
  return out;
}

/* 借鉴判断表：维度 / 结论 / 可以借鉴 / 不能照搬 */
function bdJudgeRows(body) {
  const table = bdTable(body);
  if (!table) return [];
  const heads = table.headers.map((head) => head.replace(/\s/g, ""));
  const pick = (keys, fallback) => {
    const index = heads.findIndex((head) => keys.some((key) => head.includes(key)));
    return index >= 0 ? index : fallback;
  };
  const conclusionIndex = pick(["结论"], 1);
  const canIndex = pick(["可以借鉴", "可借鉴"], 2);
  const boundaryIndex = pick(["不能照搬", "边界"], -1);
  return table.rows
    .map((row) => ({
      dim: bdText(row[0]),
      conclusion: bdText(row[conclusionIndex]),
      can: bdText(row[canIndex]),
      boundary: boundaryIndex >= 0 ? bdText(row[boundaryIndex]) : "",
    }))
    .filter((row) => row.dim && row.dim !== "维度");
}

function bdParseLearning(sections) {
  const learning = { lead: "", why: [], plus: [], minus: [] };
  const study = bdFindSection(sections, "最值得学");
  if (study) {
    const block = bdStudyBlock(study.body);
    learning.lead = block.lead;
    learning.why = block.why;
    learning.plus = block.plus;
    learning.minus = block.minus;
  }

  const judge = bdFindSection(sections, "借鉴判断");
  if (judge) {
    const rows = bdJudgeRows(judge.body);
    if (!learning.plus.length) {
      learning.plus = rows
        .filter((row) => /^值得借鉴/.test(row.conclusion) && row.can)
        .map((row) => `${row.dim}：${row.can}`);
    }
    if (!learning.minus.length) {
      const minus = [];
      rows.forEach((row) => {
        if (/不值得借鉴/.test(row.conclusion) && row.can) minus.push(`${row.dim}：${row.can}`);
        if (row.boundary) minus.push(`${row.dim}：${row.boundary}`);
      });
      const boundaryBlock = bdSubSections(judge.body).find((block) => block.title.includes("不能忽略的边界"));
      if (boundaryBlock) bdBullets(boundaryBlock.body).forEach((item) => minus.push(item));
      if (minus.length) learning.minus = minus;
    }
  }

  /* 旧结构兜底：内核层核心命题 + 正向校验 */
  if (!learning.lead) {
    const inner = bdFindBlock(sections, "内核层");
    if (inner) learning.lead = bdBulletValue(inner.body, "核心命题");
  }
  if (!learning.why.length) {
    const check = bdFindSection(sections, "正向校验");
    if (check) learning.why = check.body.split("\n").map(bdText).filter(Boolean);
  }
  // 「适用条件与不能照搬」只保留四维判断，数据缺失、样本不足这类边界说明不再展示。
  const dimensionPattern = /^(选题|切口|结构|表达)\s*[：:]/;
  learning.minus = [...new Set(learning.minus)]
    .map((item) => String(item || "").trim())
    .filter((item) => dimensionPattern.test(item));
  return learning;
}

const BD_POSITION_ORDER = ["主题", "目标用户", "核心痛点", "收益承诺", "核心命题", "切口"];

function bdParsePositioning(sections) {
  const rows = [];
  const section = bdFindSection(sections, "内容定位");
  if (section) {
    const table = bdTable(section.body);
    if (table) {
      table.rows.forEach((row) => {
        const label = bdText(row[0]).replace(/（[^）]*）/g, "").replace(/\([^)]*\)/g, "").trim();
        const value = bdText(row[1]);
        if (label && value && label !== "字段") rows.push({ label, value });
      });
    }
  }
  if (!rows.length) {
    const inner = bdFindBlock(sections, "内核层");
    if (inner) {
      [["主题", "主题"], ["目标用户", "目标用户"], ["核心痛点", "核心痛点"], ["收益承诺", "收益承诺"], ["核心命题", "核心命题"], ["切口", "切口"]]
        .forEach(([label, key]) => {
          const value = bdBulletValue(inner.body, key);
          if (value) rows.push({ label, value });
        });
    }
  }
  if (!rows.length) return rows;
  const byLabel = new Map(rows.map((row) => [row.label, row]));
  const ordered = BD_POSITION_ORDER.map((label) => byLabel.get(label)).filter(Boolean);
  rows.forEach((row) => {
    if (!BD_POSITION_ORDER.includes(row.label)) ordered.push(row);
  });
  return ordered;
}

function bdLegacyFrameworkRow(cells) {
  const moduleCell = String(cells[0] || "");
  const what = bdText((moduleCell.match(/<small>([\s\S]*?)<\/small>/i) || [])[1] || "");
  const module = bdText(moduleCell.replace(/<small>[\s\S]*?<\/small>/gi, "")).replace(/｜/g, " · ");
  const raw = String(cells[1] || "");
  const design = (raw.match(/做法[：:]\s*([\s\S]*?)(?:[；;]\s*证据[：:]|$)/) || [])[1] || raw;
  const link = (raw.match(/衔接[：:]\s*([\s\S]*)$/) || [])[1] || "";
  return { module, what, design: bdText(design), link: bdText(link) };
}

function bdParseFramework(sections) {
  const section = bdFindSection(sections, "内容框架");
  if (section) {
    const mainline = bdText((section.body.match(/推进主线[：:]\s*(.+)$/m) || [])[1] || "");
    const table = bdTable(section.body);
    if (table && table.rows.length) {
      const fourColumn = table.headers.length >= 4;
      const rows = fourColumn
        ? table.rows.map((row) => ({
          module: bdText(row[0]),
          what: bdText(row[1]),
          design: bdText(row[2]),
          link: bdText(row[3]),
        }))
        : table.rows.map(bdLegacyFrameworkRow);
      if (rows.some((row) => row.module)) return { mainline, rows };
    }
  }
  const action = bdFindBlock(sections, "动作层");
  if (action) {
    const note = bdBulletValue(action.body, "内容结构") || bdBulletValue(action.body, "结构");
    if (note) return { mainline: "", rows: [], note };
  }
  return null;
}

function bdParseFeedback(sections) {
  const section = bdFindSection(sections, "观众反馈", "评论主要在说什么", "评论");
  if (!section) return { present: false, note: "", topics: [] };
  const noteLine = section.body.split("\n").map((line) => line.trim()).find((line) => /^样本[：:]/.test(line));
  const topics = [];
  const table = bdTable(section.body);
  if (table) {
    const heads = table.headers.map((head) => head.replace(/\s/g, ""));
    const quoteIndex = heads.findIndex((head) => head.includes("代表原话"));
    table.rows.forEach((row) => {
      const name = bdText(row[0]);
      const count = bdText(row[1]);
      let what = bdText(row[2]);
      let quote = quoteIndex >= 0 ? bdText(row[quoteIndex]) : "";
      if (!quote) {
        const match = what.match(/代表原话[：:]\s*[「“"]([\s\S]*?)[」”"]\s*$/);
        if (match) {
          quote = match[1].trim();
          what = what.replace(match[0], "").trim();
        }
      }
      if (name && name !== "方向") topics.push({ name, count, what, quote });
    });
  }
  return { present: true, note: noteLine ? bdText(noteLine) : "", topics };
}

function bdParseComments(markdown) {
  const text = String(markdown || "");
  const totalMatch = text.match(/一共\s*([\d,]+)\s*条一级评论/);
  const total = totalMatch ? Number(totalMatch[1].replace(/,/g, "")) : 0;
  const table = bdTable(text);
  const rows = [];
  if (table) {
    table.rows.forEach((row) => {
      if (row.length < 4) return;
      const author = bdText(row[1]);
      const content = bdText(row[2]);
      const likes = Number(bdText(row[3]).replace(/[^\d]/g, "")) || 0;
      if (!author && !content) return;
      rows.push({ author, text: content, likes, time: bdText(row[5] || "") });
    });
  }
  return { total: total || rows.length, rows };
}

function bdParseTranscript(markdown) {
  return String(markdown || "")
    .replace(/\r\n?/g, "\n")
    .split("\n")
    .map((line) => line.trim())
    .filter((line) => line && !/^#/.test(line) && !/^<!--/.test(line))
    .map(bdText)
    .filter(Boolean);
}

/* 视频文案按内容框架的模块顺序切成段：每段只保留「进入 / 第 N 段｜功能 / 结尾」这个框架标签，
   标签下面直接接原文，展示方式和「内容初稿」的分段一致，不再附带框架里的整段说明。
   原文段落数多于框架行时按行数均分，第一行从第一段开始、最后一行收在最后一段；
   没有框架时不加标签，仍按原文展示。 */
function bdTranscriptSegments(framework, transcript) {
  const texts = (transcript || []).filter(Boolean);
  const rows = (framework?.rows || []).filter((row) => row.module);
  if (!texts.length) return [];
  if (!rows.length) return texts.map((text) => ({ name: "", text }));
  const segments = [];
  let cursor = 0;
  rows.forEach((row, index) => {
    if (cursor >= texts.length) return;
    const remainingRows = rows.length - index;
    const remainingTexts = texts.length - cursor;
    const take = index === rows.length - 1
      ? remainingTexts
      : Math.max(1, Math.round(remainingTexts / remainingRows));
    const lines = texts.slice(cursor, cursor + take);
    cursor += take;
    segments.push({
      name: row.module,
      text: lines.join("\n"),
    });
  });
  return segments;
}

function bdTranscriptHtml(segments) {
  return segments.map((segment) => `<div class="sd-seg">
    ${segment.name ? `<div class="sd-seg-head"><span class="sd-seg-tag"><span class="sd-seg-name">${escapeHtml(segment.name)}</span></span></div>` : ""}
    <div class="sd-seg-text">${escapeHtml(segment.text)}</div>
  </div>`).join("");
}

function bdExternalAction(item) {
  try {
    const url = new URL(item.external_url);
    if (!/^https?:$/.test(url.protocol)) return "";
    return `<a class="bd-link-btn" href="${escapeAttribute(url.href)}" target="_blank" rel="noreferrer">打开原网页</a>`;
  } catch (_error) {
    return "";
  }
}

function bdSummaryHtml(item) {
  const statuses = [item.data_level, item.analysis_state].filter((value) => value && value !== "未填写");
  const metrics = [
    ["点赞", formatNumber(item.metrics?.likes)],
    ["收藏", formatNumber(item.metrics?.favorites)],
    ["评论", formatNumber(item.metrics?.comments)],
  ];
  if (item.metrics?.shares !== null && item.metrics?.shares !== undefined) metrics.push(["分享", formatNumber(item.metrics.shares)]);
  // 头部是一张 4 列两排的表：上排「谁发的」（账号 / 平台 / 形式 / 时间），下排「表现如何」（4 个互动数）。
  // 两排共用同一套列宽和单元格结构，中间一根分隔线；不要再把下排改成挤在左边的行内串。
  const facts = [
    { name: "账号", value: escapeHtml(item.account_name || "待补齐") },
    { name: "平台", value: platformPill(item.platform), isHtml: true },
    { name: "媒介形式", value: escapeHtml(item.media_format || "待补齐") },
    { name: "发布时间", value: escapeHtml(item.published_at || "待补齐") },
  ];
  const cover = item.cover_path
    ? `<img class="bd-cover" src="${rawUrl(item.cover_path)}" alt="${escapeAttribute(item.title)}封面">`
    : `<div class="bd-cover bd-cover-placeholder" role="img" aria-label="未提供封面"><svg viewBox="0 0 24 24" aria-hidden="true"><rect x="3.5" y="4.5" width="17" height="15" rx="2.5"/><circle cx="9" cy="10" r="1.6"/><path d="m4.5 17 4.5-4.5 3 3 2.5-2.5 5 4.5"/></svg></div>`;
  const external = bdExternalAction(item);
  return `<section class="bd-summary">
    <div class="bd-summary-main">
      <h1>${escapeHtml(bdTitle(item.title))}</h1>
      ${statuses.length ? `<div class="bd-status-row">${statuses.map((value) => `<span class="bd-status">${escapeHtml(value)}</span>`).join("")}</div>` : ""}
      <dl class="bd-info">${facts.map(({ name, value, isHtml }) => `<div><dt>${escapeHtml(name)}</dt><dd>${isHtml ? value : value}</dd></div>`).join("")}</dl>
      <dl class="bd-stats">${metrics.map(([name, value]) => `<div><dt>${escapeHtml(name)}</dt><dd>${escapeHtml(value)}</dd></div>`).join("")}</dl>
    </div>
    <div class="bd-summary-side">${cover}${external}</div>
  </section>`;
}

function bdLearningHtml(learning) {
  const plus = learning.plus.filter(Boolean);
  const minus = learning.minus.filter(Boolean);
  const why = learning.why.filter(Boolean);
  if (!learning.lead && !plus.length && !why.length && !minus.length) return "";
  const meta = [plus.length ? `${plus.length} 条可直接用` : "", minus.length ? `${minus.length} 条不能照搬` : ""].filter(Boolean).join(" · ");
  let blockIndex = 0;
  const blockClass = () => (blockIndex++ === 0 ? "bd-m-block first" : "bd-m-block");
  return `<section class="bd-module bd-module-accent">
    <div class="bd-m-head">
      <span class="bd-m-num">01</span>
      <div class="bd-m-titles"><h2>学习结论</h2><p>我能学什么</p></div>
      ${meta ? `<span class="bd-m-meta">${escapeHtml(meta)}</span>` : ""}
    </div>
    ${learning.lead || plus.length ? `<div class="${blockClass()}">
      <p class="bd-kicker">最值得借鉴的做法</p>
      ${learning.lead ? `<p class="bd-lead">${escapeHtml(learning.lead)}</p>` : ""}
      ${plus.length ? `<ul class="bd-plus">${plus.map((text, index) => `<li><span class="n">${index + 1}</span><span>${escapeHtml(text)}</span></li>`).join("")}</ul>` : ""}
    </div>` : ""}
    ${why.length ? `<div class="${blockClass()}">
      <p class="bd-kicker">为什么值得借鉴</p>
      <p class="bd-lead-sub">${why.map(escapeHtml).join("<br>")}</p>
    </div>` : ""}
    ${minus.length ? `<div class="${blockClass()}">
      <p class="bd-kicker">适用条件与不能照搬</p>
      <ul class="bd-minus">${minus.map((text) => `<li><span class="n">—</span><span>${escapeHtml(text)}</span></li>`).join("")}</ul>
    </div>` : ""}
  </section>`;
}

function bdContentHtml(positioning, transcriptSegments, item) {
  const hasPositioning = positioning.length > 0;
  const hasTranscript = transcriptSegments.length > 0;
  if (!hasPositioning && !hasTranscript) return "";

  const wordCount = Number(item.transcript_word_count) > 0
    ? item.transcript_word_count
    : visibleTextLength(transcriptSegments.map((segment) => segment.text).join("\n"));
  const excerpt = transcriptSegments.slice(0, 4);
  const docPanel = `<div class="bd-col">
    <p class="bd-kicker">视频文案 · 拆解依据</p>
    <div class="bd-doc">
      <div class="bd-doc-body"><div class="bd-doc-scroll">${excerpt.length ? bdTranscriptHtml(excerpt) : "<p>还没有取到视频文案。</p>"}</div></div>
      <div class="bd-doc-foot">
        <span class="bd-doc-meta">${wordCount ? `原文摘录 · 约 ${escapeHtml(formatNumber(wordCount))} 字` : "原文摘录"}</span>
        ${hasTranscript ? `<button class="bd-ghost-btn" type="button" data-bd-drawer="transcript">查看完整文案${BD_ARROW}</button>` : ""}
      </div>
    </div>
  </div>`;

  const specPanel = `<div class="bd-col">
    <p class="bd-kicker">内容定位 · 对谁说 / 讲什么 / 提供什么价值</p>
    <div class="bd-spec">${positioning.map((row) => `<div class="bd-spec-cell"><span>${escapeHtml(row.label)}</span><b>${escapeHtml(row.value)}</b></div>`).join("")}</div>
  </div>`;

  const splitClass = hasPositioning && hasTranscript ? "bd-split2" : "bd-split2 single";
  const splitBlock = `<div class="bd-m-block first"><div class="${splitClass}">${hasPositioning ? specPanel : ""}${hasTranscript ? docPanel : ""}</div></div>`;

  return `<section class="bd-module">
    <div class="bd-m-head">
      <span class="bd-m-num">02</span>
      <div class="bd-m-titles"><h2>内容拆解</h2><p>这条内容是怎么做的</p></div>
      <span class="bd-m-meta">定位 · 原文</span>
    </div>
    ${splitBlock}
  </section>`;
}

function bdCommentHtml(comment, maxLikes) {
  const initial = [...String(comment.author || "?")][0] || "?";
  const hot = comment.likes > 0 && comment.likes === maxLikes;
  return `<div class="bd-cmt">
    <span class="bd-av">${escapeHtml(initial)}</span>
    <div>
      <div class="bd-who"><b>${escapeHtml(comment.author || "匿名")}</b><span class="bd-like${hot ? " hot" : ""}">${BD_HEART}${escapeHtml(formatNumber(comment.likes))}</span></div>
      <p>${escapeHtml(comment.text)}</p>
    </div>
  </div>`;
}

function bdFeedbackHtml(feedback, comments) {
  const topics = feedback.topics || [];
  const rows = comments.rows || [];
  const top = [...rows].sort((left, right) => right.likes - left.likes).slice(0, 6);
  const present = Boolean(feedback.present);
  if (!present && !topics.length && !top.length) return "";
  const maxLikes = top.length ? Math.max(...top.map((comment) => comment.likes)) : 0;
  const meta = [
    comments.total ? `${comments.total} 条评论` : "",
    topics.length ? `${topics.length} 个话题` : "",
  ].filter(Boolean).join(" · ");
  const head = `<div class="bd-m-head">
      <span class="bd-m-num">03</span>
      <div class="bd-m-titles"><h2>观众反馈</h2><p>观众接住了什么，还在关心什么</p></div>
      ${meta ? `<span class="bd-m-meta">${escapeHtml(meta)}</span>` : ""}
    </div>`;

  if (!topics.length && !top.length) {
    return `<section class="bd-module">
    ${head}
    <div class="bd-m-block first"><p class="bd-note-line">${escapeHtml(feedback.note || "本次没有可用的评论样本，无法归纳观众反馈。")}</p></div>
  </section>`;
  }

  const topicsPanel = `<div class="bd-col">
    <p class="bd-kicker">评论集中讨论的话题</p>
    ${feedback.note ? `<p class="bd-note-line">${escapeHtml(feedback.note)}</p>` : ""}
    <div class="bd-topics">${topics.map((topic) => `<div class="bd-topic">
      <div class="bd-topic-h"><span class="lbl">方向：</span><b>${escapeHtml(topic.name)}</b>${topic.count ? `<span class="cnt">${escapeHtml(topic.count)}</span>` : ""}</div>
      ${topic.what ? `<p><span class="lbl">评论在说什么：</span>${escapeHtml(topic.what)}</p>` : ""}
      ${topic.quote ? `<q>「${escapeHtml(topic.quote)}」</q>` : ""}
    </div>`).join("")}</div>
  </div>`;

  const commentsPanel = `<div class="bd-col">
    <div class="bd-kicker-row">
      <p class="bd-kicker">评论内容 · 按赞数排</p>
      ${rows.length ? `<button class="bd-ghost-btn" type="button" data-bd-drawer="comments">查看全部${BD_ARROW}</button>` : ""}
    </div>
    ${top.length ? `<div class="bd-cmt-list">${top.map((comment) => bdCommentHtml(comment, maxLikes)).join("")}</div>` : `<p class="bd-note-line">还没有抓到评论原文。</p>`}
  </div>`;

  const splitClass = topics.length && top.length ? "bd-split3" : "bd-split3 single";
  return `<section class="bd-module">
    ${head}
    <div class="bd-m-block first"><div class="${splitClass}">${topics.length ? topicsPanel : ""}${top.length ? commentsPanel : ""}</div></div>
  </section>`;
}

function bdDrawerHtml() {
  return `<div class="bd-mask" id="bd-mask"></div>
  <aside class="bd-drawer" id="bd-drawer" aria-hidden="true" aria-label="资料抽屉">
    <div class="bd-drawer-head">
      <div><div class="bd-d-eyebrow">SOURCE</div><h3 id="bd-drawer-title"></h3><p id="bd-drawer-path"></p></div>
      <button class="bd-drawer-close" id="bd-close" type="button" aria-label="关闭">${BD_CLOSE}</button>
    </div>
    <div class="bd-drawer-body" id="bd-drawer-body"></div>
  </aside>`;
}

function bdRenderPage(item, data) {
  return `<div class="bd-page">
    ${bdSummaryHtml(item)}
    ${bdLearningHtml(data.learning)}
    ${bdContentHtml(data.positioning, data.transcriptSegments, item)}
    ${bdFeedbackHtml(data.feedback, data.comments)}
    ${bdDrawerHtml()}
  </div>`;
}

function openBdDrawer(kind) {
  const data = window.benchmarkViewData;
  const drawer = document.querySelector("#bd-drawer");
  const mask = document.querySelector("#bd-mask");
  if (!data || !drawer || !mask) return;
  const title = document.querySelector("#bd-drawer-title");
  const path = document.querySelector("#bd-drawer-path");
  const body = document.querySelector("#bd-drawer-body");
  if (kind === "transcript") {
    title.textContent = "视频文案";
    path.textContent = data.paths.transcript;
    body.innerHTML = data.transcriptSegments.length
      ? bdTranscriptHtml(data.transcriptSegments)
      : `<p class="bd-dpara">还没有取到视频文案。</p>`;
  } else {
    title.textContent = "评论";
    path.textContent = data.paths.comments;
    const rows = data.comments.rows || [];
    const note = `共 ${data.comments.total || rows.length} 条一级评论${rows.length ? `，下表为已获取的 ${rows.length} 条，按点赞数排` : ""}。`;
    body.innerHTML = rows.length
      ? `<p class="bd-drawer-note">${escapeHtml(note)}</p><table class="bd-dtable"><thead><tr><th>作者</th><th>评论内容</th><th style="text-align:right">赞</th><th style="text-align:right">时间</th></tr></thead><tbody>${rows.map((row) => `<tr><td class="n">${escapeHtml(row.author || "匿名")}</td><td>${escapeHtml(row.text)}</td><td class="like">${escapeHtml(formatNumber(row.likes))}</td><td class="like">${escapeHtml(row.time || "—")}</td></tr>`).join("")}</tbody></table>`
      : `<p class="bd-drawer-note">还没有抓到评论原文。</p>`;
  }
  mask.classList.add("on");
  drawer.classList.add("on");
  drawer.setAttribute("aria-hidden", "false");
  body.scrollTop = 0;
  document.body.style.overflow = "hidden";
}

function closeBdDrawer() {
  const drawer = document.querySelector("#bd-drawer");
  const mask = document.querySelector("#bd-mask");
  if (!drawer || !mask) return;
  mask.classList.remove("on");
  drawer.classList.remove("on");
  drawer.setAttribute("aria-hidden", "true");
  document.body.style.overflow = "";
}

async function loadBenchmarkDetail(model) {
  const item = model.item;
  markNavActive("benchmark-contents");
  applyCrumb("对标内容分析", item.title, bdTitle(item.title));
  document.title = `${bdTitle(item.title)}｜媒灵内容档案`;
  elements.view.innerHTML = `<div class="bd-page">${bdSummaryHtml(item)}<div class="content-loading"><span></span><p>正在装配分析档案</p></div></div>`;

  const [analysisFile, transcriptFile, commentsFile] = await Promise.all([
    fetchOptionalDocument(item.analysis_path || ""),
    fetchOptionalDocument(item.transcript_path || ""),
    fetchOptionalDocument(item.comments_path || ""),
  ]);

  if (!analysisFile?.content) {
    window.benchmarkViewData = null;
    elements.view.innerHTML = `<div class="bd-page">${bdSummaryHtml(item)}${emptyState("还没有内容分析", "这条内容还没有生成内容分析文档，先跑一次对标内容分析。")}</div>`;
    return;
  }

  const sections = bdSections(analysisFile.content);
  const framework = bdParseFramework(sections);
  const transcript = transcriptFile?.content ? bdParseTranscript(transcriptFile.content) : [];
  const data = {
    learning: bdParseLearning(sections),
    positioning: bdParsePositioning(sections),
    framework,
    feedback: bdParseFeedback(sections),
    transcript,
    transcriptSegments: bdTranscriptSegments(framework, transcript),
    comments: commentsFile?.content ? bdParseComments(commentsFile.content) : { total: 0, rows: [] },
    paths: {
      analysis: analysisFile.path || item.analysis_path || "",
      transcript: transcriptFile?.path || item.transcript_path || "",
      comments: commentsFile?.path || item.comments_path || "",
    },
  };
  const structured = Boolean(
    data.learning.lead || data.learning.plus.length || data.learning.why.length || data.learning.minus.length
    || data.positioning.length || data.framework || data.feedback.topics.length,
  );

  if (!structured) {
    /* 结构识别不了时退回完整文档，保证任何一份分析都能读到 */
    window.benchmarkViewData = null;
    renderView(model, { path: data.paths.analysis, title: "内容分析" }, renderMarkdown(analysisFile.content, { documentPath: data.paths.analysis }));
    return;
  }

  window.benchmarkViewData = data;
  elements.view.innerHTML = bdRenderPage(item, data);
}

function fallbackReturnPath() {
  if (contentKind === "own") {
    return window.contentArchiveModel?.collected ? "index.html#own-accounts" : "index.html#creation-board";
  }
  return "index.html#benchmark-contents";
}

function safeReturnPath(value) {
  const candidate = String(value || "").trim();
  if (!candidate || candidate.startsWith("/") || candidate.startsWith("\\") || /^[a-z][a-z\d+.-]*:/i.test(candidate)) return "";
  try {
    const target = new URL(candidate, `${location.origin}/`);
    if (target.origin !== location.origin) return "";
    const path = target.pathname.replace(/^\//, "");
    if (path === "account-v2" || path === "account-v2.html") {
      const kind = target.searchParams.get("kind");
      const id = target.searchParams.get("id");
      const tab = target.searchParams.get("tab");
      if (!id || !["benchmark", "own"].includes(kind) || (tab && !["charts", "analysis", "works"].includes(tab))) return "";
      return `${path}?kind=${encodeURIComponent(kind)}&id=${encodeURIComponent(id)}${tab ? `&tab=${encodeURIComponent(tab)}` : ""}`;
    }
    if (path === "index.html" && (!target.hash || /^#(?:benchmark-contents|publications|own-accounts|benchmark-accounts-v2|topics|creation-board)$/.test(target.hash))) {
      return `${path}${target.hash}`;
    }
    if ((path === "topic" || path === "topic.html") && target.searchParams.get("id")) {
      return `${path}?id=${encodeURIComponent(target.searchParams.get("id"))}`;
    }
  } catch (_error) {
    // Invalid return data deliberately falls back to the matching workbench list.
  }
  return "";
}

function returnToPreviousPage() {
  const returnPath = safeReturnPath(requestedReturnTo);
  location.assign(returnPath || fallbackReturnPath());
}

async function fetchDocument(path) {
  const response = await fetch(`/api/file?path=${encodeURIComponent(path)}`, { cache: "no-store" });
  if (!response.ok) throw new Error(`document_read_failed:${response.status}`);
  return response.json();
}

async function hydrateTranscriptWordCount(model) {
  if (!model?.item?.transcript_path || Number(model.item.transcript_word_count) > 0) return;
  try {
    const file = await fetchDocument(model.item.transcript_path);
    model.item.transcript_word_count = visibleTextLength(file.content);
    const transcript = model.documents.find((document) => document.path === model.item.transcript_path);
    if (transcript) transcript.meta = formatWordCount(model.item.transcript_word_count);
  } catch (_error) {
    // A missing transcript should not stop the archive from opening.
  }
}

async function loadDocument(model, documentReference) {
  const currentDocument = documentReference || model.primaryDocument;
  // 「内容分析」只是占位（见 getContent 里的说明）：这份文档还没生成时，在 tab 栏下方给一句说明，
  // 不让 tab 消失、也不去读一个不存在的文件。
  // 说明**不放进文档框里** —— 文档不存在时那一栏空着即可，别留一个空框把提示框住。
  if (currentDocument?.path === "内容分析.md") {
    renderView(model, currentDocument, "");
    elements.view.querySelector("article.benchmark-document")?.remove();
    elements.view.querySelector(".benchmark-layout")?.insertAdjacentHTML(
      "afterend",
      emptyState("还没有内容分析", "这条内容还没有生成内容分析文档。"),
    );
    return;
  }
  if (!currentDocument?.path) {
    renderView(model, null, emptyState("还没有正式内容文档", "可以继续在工作台中补齐分析或生产记录。"));
    return;
  }
  renderView(model, currentDocument, '<div class="inline-loading"><span></span><p>正在读取正式真源</p></div>');
  try {
    const file = await fetchDocument(currentDocument.path);
    renderView(model, currentDocument, renderMarkdown(file.content, { documentPath: file.path || currentDocument.path }));
  } catch (error) {
    renderView(model, currentDocument, emptyState("正式文档读取失败", error.message));
  }
}

async function fetchOptionalDocument(path) {
  if (!path) return null;
  try {
    return await fetchDocument(path);
  } catch (_error) {
    return null;
  }
}

async function loadOwnProduction(model) {
  renderOwnView(model);
  try {
    if (model.productionRecordContent === undefined) {
      if (!model.primaryDocument?.path) throw new Error("内容生产记录尚未建立");
      const file = await fetchDocument(model.primaryDocument.path);
      model.productionRecordContent = file.content;
    }
    model.productionData = buildOwnProduction(model);
    renderOwnView(model);
  } catch (error) {
    elements.view.innerHTML = emptyState("内容生产记录读取失败", error.message);
  }
}

async function loadContent({ force = false } = {}) {
  const hasPreviousContent = Boolean(window.contentArchiveModel);
  elements.reload.disabled = true;
  if (!hasPreviousContent) {
    elements.view.innerHTML = '<div class="content-loading"><span></span><p>正在装配统一内容档案</p></div>';
  }
  try {
    if (location.protocol === "file:") {
      elements.view.innerHTML = emptyState(
        "内容档案需要通过媒灵工作台打开",
        "content.html 是可编辑模板，不携带具体内容。请启动本地服务后，从工作台的内容标题进入。",
      );
      return;
    }
    if (!contentId || !["benchmark", "own"].includes(contentKind)) {
      elements.view.innerHTML = emptyState("内容地址无效", "请从媒灵工作台的内容列表进入。 ");
      return;
    }
    const response = await fetch(force ? "/api/refresh" : "/api/snapshot", { cache: "no-store" });
    if (!response.ok) throw new Error(`snapshot_read_failed:${response.status}`);
    const snapshot = await response.json();
    syncWorkspaceVersion(snapshot);
    const model = getContent(snapshot);
    if (!model) {
      elements.view.innerHTML = emptyState("未找到该内容", "这条记录可能已被移动或删除，请返回工作台刷新后重试。 ");
      return;
    }
    window.contentArchiveModel = model;
    if (model.kind === "own" && !model.collected) {
      await loadOwnProduction(model);
    } else if (model.kind === "benchmark") {
      await loadBenchmarkDetail(model);
    } else {
      await hydrateTranscriptWordCount(model);
      await loadDocument(model, model.primaryDocument);
    }
  } catch (error) {
    if (hasPreviousContent) {
      showToast("资料刷新失败，当前仍显示上一次结果");
      return;
    }
    elements.view.innerHTML = emptyState("内容档案暂时不可用", error.message);
  } finally {
    elements.reload.disabled = false;
  }
}

document.addEventListener("click", (event) => {
  if (!event.target.closest("[data-sd-ver-wrap]")) {
    document.querySelectorAll("[data-sd-ver-wrap].is-open").forEach((node) => node.classList.remove("is-open"));
  }
  // 来源行里的「选题」：iframe 里没有选题详情页，装在工作台抽屉里时交给父窗口开抽屉
  const sourceTopic = event.target.closest("[data-sd-topic]");
  if (sourceTopic) {
    event.preventDefault();
    if (document.documentElement.classList.contains("is-embed")) {
      window.parent.postMessage({ type: "meiling:open-topic-drawer", topicId: sourceTopic.dataset.sdTopic }, location.origin);
      return;
    }
    location.assign("index.html#topics");
    return;
  }
  if (event.target.closest("#back-to-previous")) {
    // 装在工作台抽屉里时，这里是「关掉抽屉回列表」，不是让 iframe 自己跳页
    // （那样会把整个工作台嵌进抽屉里）。
    if (document.documentElement.classList.contains("is-embed")) {
      window.parent.postMessage({ type: "meiling:close-content-drawer" }, location.origin);
      return;
    }
    returnToPreviousPage();
    return;
  }
  const editBtn = event.target.closest("[data-sd-edit]");
  if (editBtn) {
    const kind = editBtn.dataset.sdEdit;
    if (kind === "open") sdOpenEditor(editBtn.dataset.sdTarget);
    else if (kind === "cancel") sdCloseEditor();
    else if (kind === "save") sdSaveEditor();
    return;
  }
  const auditBtn = event.target.closest("[data-sd-audit]");
  if (auditBtn) {
    const production = window.contentArchiveModel?.productionData;
    if (!production) return;
    const section = auditBtn.dataset.sdAudit;
    sdOpenPromptDialog({
      title: "AI 审查",
      copy: "会按内容审查流程检查这份稿件：只出报告，不改稿、不生成新版本。点「确认」就送进对话框，跑完回来在「内容审查」里看结果。",
      text: sdAuditPrompt(production, section),
    });
    return;
  }
  const confirmDialogAction = event.target.closest("[data-sd-cfm]");
  if (confirmDialogAction) {
    sdCloseConfirm(confirmDialogAction.dataset.sdCfm === "ok");
    return;
  }
  if (event.target.id === "sd-confirm-dialog") {
    sdCloseConfirm(false);
    return;
  }
  const dialogAction = event.target.closest("[data-sd-dlg]");
  if (dialogAction) {
    const kind = dialogAction.dataset.sdDlg;
    if (kind === "copy") {
      sdCopyText(sdPromptDialogState.text, "审查指令已复制，粘到对话框执行");
    }
    sdClosePromptDialog();
    return;
  }
  if (event.target.id === "sd-prompt-dialog") {
    sdClosePromptDialog();
    return;
  }
  const confirmBtn = event.target.closest("[data-sd-confirm]");
  if (confirmBtn) {
    const production = window.contentArchiveModel?.productionData;
    if (!production) return;
    sdOpenPromptDialog({
      title: "确认定位并生成初稿",
      copy: "点「确认」就送进对话框，AI 会按这一版定位写初稿，不会重新生成定位。",
      text: sdConfirmPrompt(production),
    });
    return;
  }
  const adjustBtn = event.target.closest("[data-sd-adjust]");
  if (adjustBtn) {
    const production = window.contentArchiveModel?.productionData;
    const current = production ? sdPickVersion(production.reviewVersions || [], "review") : null;
    if (!current?.content) return;
    const version = current.version || 0;
    const review = sdReviewModel(current);
    const state = sdReadReviewState(production, version);
    const adopted = sdReviewItems(review).filter((item) => state[item.id] === "adopted").length;
    if (!adopted) {
      showToast("先在表里勾选要采纳的几条");
      return;
    }
    sdOpenPromptDialog({
      title: "AI 调整初稿",
      copy: `已采纳 ${adopted} 条。点「确认」就送进对话框，AI 只改这几条，改完写成新的初稿版本。`,
      text: sdReviewPrompt(production, review, version, state),
    });
    return;
  }
  const confirmFinalBtn = event.target.closest("[data-sd-confirm-final]");
  if (confirmFinalBtn) {
    const production = window.contentArchiveModel?.productionData;
    if (!production) return;
    const draft = sdPickVersion(production.draftVersions || [], "draft");
    if (!draft) {
      showToast("还没有内容初稿");
      return;
    }
    const hasFinal = Boolean(sdPickVersion(production.finalVersions || [], "final"));
    const copy = [
      `会把当前初稿 v${draft.version}${draft.title ? `「${draft.title}」` : ""}原样复制成内容终稿，审查建议不应用。`,
      hasFinal ? "现有终稿会被这一版覆盖。" : "",
    ].filter(Boolean).join("");
    sdAskConfirm({ title: "通过审查确认终稿", copy, confirmText: "确认" }).then((confirmed) => {
      if (confirmed) sdCopyDraftToFinal(draft.version);
    });
    return;
  }
  const revBtn = event.target.closest("[data-rev]");
  if (revBtn) {
    const model = window.contentArchiveModel;
    const production = model?.productionData;
    if (!production) return;
    const current = sdPickVersion(production.reviewVersions || [], "review");
    if (!current?.content) return;
    const version = current.version || 0;
    const kind = revBtn.dataset.rev;
    const state = sdReadReviewState(production, version);
    const rowId = revBtn.dataset.row;
    if (!rowId) return;
    if (kind === "undo") delete state[rowId];
    else state[rowId] = kind === "adopt" ? "adopted" : "skipped";
    sdWriteReviewState(production, version, state);
    renderOwnView(model);
    return;
  }
  const bdTrigger = event.target.closest("[data-bd-drawer]");
  if (bdTrigger) {
    openBdDrawer(bdTrigger.dataset.bdDrawer);
    return;
  }
  if (event.target.closest("#bd-close") || event.target.id === "bd-mask") {
    closeBdDrawer();
    return;
  }
  const button = event.target.closest("[data-document-path]");
  if (button && (window.contentArchiveModel?.kind === "benchmark" || window.contentArchiveModel?.collected)) {
    loadDocument(window.contentArchiveModel, {
      path: button.dataset.documentPath,
      title: button.dataset.documentTitle || "正式资料",
    });
    return;
  }
  const sdTab = event.target.closest("[data-sd-tab]");
  if (sdTab) {
    /* 正在编辑时切走：先把输入收进 state，再退出编辑态，
       免得改了一半的字直接没了 */
    if (sdEditState.section) {
      sdCaptureEditor();
      sdEditState.section = "";
    }
    sdViewState.tab = sdTab.dataset.sdTab;
    history.replaceState(null, "", `#${sdViewState.tab}`);
    if (window.contentArchiveModel?.productionData) renderOwnView(window.contentArchiveModel);
    return;
  }
  const sdVerOpen = event.target.closest("[data-sd-ver-open]");
  if (sdVerOpen) {
    const wrap = sdVerOpen.closest("[data-sd-ver-wrap]");
    const willOpen = wrap && !wrap.classList.contains("is-open");
    document.querySelectorAll("[data-sd-ver-wrap]").forEach((node) => node.classList.remove("is-open"));
    if (willOpen) wrap.classList.add("is-open");
    return;
  }
  const sdVer = event.target.closest("[data-sd-ver]");
  if (sdVer) {
    const key = sdVer.dataset.sdVerKey;
    const version = Number(sdVer.dataset.sdVer);
    if (["position", "draft", "review", "final"].includes(key) && Number.isInteger(version)) {
      sdViewState[key] = version;
      if (window.contentArchiveModel?.productionData) renderOwnView(window.contentArchiveModel);
    }
    return;
  }
  const sdCopy = event.target.closest("[data-sd-copy]");
  if (sdCopy) {
    const production = window.contentArchiveModel?.productionData;
    const version = production ? sdPickVersion(production.finalVersions, "final") : null;
    if (version?.text) sdCopyText(version.text, "全文已复制");
    return;
  }
});

window.addEventListener("hashchange", () => {
  sdViewState.tab = "";
  if (window.contentArchiveModel?.productionData) renderOwnView(window.contentArchiveModel);
});

document.addEventListener("keydown", (event) => {
  if (event.key === "Escape") closeBdDrawer();
});

elements.reload.addEventListener("click", async () => {
  const scrollTop = window.scrollY;
  await loadContent({ force: true });
  window.scrollTo({ top: scrollTop });
});
loadContent();
