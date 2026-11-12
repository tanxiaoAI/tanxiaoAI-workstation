"use strict";

/* 旧账号详情页已由 account-v2 取代：从旧地址进入时直接跳到新版，避免旧页面再次出现。 */
if (location.protocol !== "file:" && /\/account(\.html)?$/.test(location.pathname)) {
  location.replace(`/account-v2${location.search}${location.hash}`);
}

const query = new URLSearchParams(location.search);
const accountKind = query.get("kind") || "";
const accountId = query.get("id") || "";
const ACCOUNT_TAB_IDS = new Set(["charts", "analysis", "pattern", "works"]);

const state = {
  model: null,
  toastTimer: null,
  activeTab: ACCOUNT_TAB_IDS.has(query.get("tab")) ? query.get("tab") : "charts",
  notesOpen: false,
  trendMetric: "total",
  works: {
    query: "",
    platform: "",
    dataLevel: "",
    state: "",
    sort: "published-desc",
  },
};

const elements = {
  back: document.querySelector("#back-to-previous"),
  breadcrumb: document.querySelector("#account-breadcrumb"),
  closeTranscript: document.querySelector("#close-transcript"),
  reload: document.querySelector("#reload-account"),
  toast: document.querySelector("#toast"),
  transcriptBody: document.querySelector("#transcript-body"),
  transcriptKicker: document.querySelector("#transcript-kicker"),
  transcriptPanel: document.querySelector("#transcript-panel"),
  transcriptTitle: document.querySelector("#transcript-title"),
  view: document.querySelector("#account-view"),
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

function numericValue(value) {
  if (value === null || value === undefined || String(value).trim() === "") return null;
  const number = Number(value);
  return Number.isFinite(number) ? number : null;
}

function dateTimestamp(value) {
  const normalized = String(value || "")
    .trim()
    .replace(/[年/.]/g, "-")
    .replace(/月/g, "-")
    .replace(/日/g, "");
  const timestamp = Date.parse(normalized);
  return Number.isFinite(timestamp) ? timestamp : null;
}

function formatPublishedAt(value) {
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

function statusTone(value) {
  const text = String(value || "");
  if (/不建议|失败|异常|阻断|废弃|取消|无效|不适配|不通过|高风险/.test(text)) return "tone-red";
  if (/风险|低适配|低质量|不匹配|问题|待优化|需修正|受限|较差/.test(text)) return "tone-pink";
  if (/暂缓|生产中|待生产|待发布|处理中|进行中|排期|草稿/.test(text)) return "tone-orange";
  if (/待|未填写|待补齐|缺失|暂无|普通|一般|未知|低优先/.test(text)) return "tone-yellow";
  if (/建议做|优秀|高适配|大爆款|已完成|已通过|已验证|可用|高优先/.test(text)) return "tone-deep-green";
  if (/已发布|正常|小爆款|中适配|已复盘|可调用|已建档|已分析|已启用|稳定/.test(text)) return "tone-light-green";
  return "tone-neutral";
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
  const label = value || "未填写";
  return `<span class="account-status-tag ${dataLevelTone(label)}">${escapeHtml(label)}</span>`;
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

function platformTag(value) {
  const label = value || "未填写";
  const key = platformKey(label);
  return `<span class="account-platform platform-${key}">${platformIcon(key)}<span>${escapeHtml(label)}</span></span>`;
}

function platformPill(value) {
  const label = value || "未填写";
  const key = platformKey(label);
  return `<span class="status-pill platform-pill platform-${key}">${platformIcon(key)}<span>${escapeHtml(label)}</span></span>`;
}

function listStatusPill(value) {
  const label = value || "未填写";
  return `<span class="status-pill ${statusTone(label)}">${escapeHtml(label)}</span>`;
}

function listDataLevelPill(value) {
  const label = value || "未填写";
  return `<span class="status-pill ${dataLevelTone(label)}">${escapeHtml(label)}</span>`;
}

/* 抖音侧的性别存的是数字（1=男 / 2=女）；空值和 0 视为未知，整条不显示。
   口径与稿子 gender_info() 一致；列表卡那边见 app.js 的 genderLabel()。 */
function genderLabel(value) {
  const raw = String(value === null || value === undefined ? "" : value).trim();
  if (!raw || raw === "0") return "";
  if (raw === "1") return "男";
  if (raw === "2") return "女";
  return raw;
}

function genderTag(value) {
  const label = genderLabel(value);
  if (!label) return "";
  if (/男/.test(label)) return '<span class="account-status-tag account-gender gender-male"><svg aria-hidden="true" viewBox="0 0 24 24"><circle cx="9" cy="15" r="5"/><path d="M12.5 11.5 19 5m-5 0h5v5"/></svg>男</span>';
  if (/女/.test(label)) return '<span class="account-status-tag account-gender gender-female"><svg aria-hidden="true" viewBox="0 0 24 24"><circle cx="12" cy="9" r="5"/><path d="M12 14v7m-3-3h6"/></svg>女</span>';
  return `<span class="account-status-tag tone-gray">${escapeHtml(label)}</span>`;
}

function rawUrl(path) {
  return `/api/raw?path=${encodeURIComponent(path)}`;
}

function contentUrl(kind, id) {
  // Preserve the archive context so content can return to this account's works tab.
  const returnTo = `account?kind=${encodeURIComponent(accountKind)}&id=${encodeURIComponent(accountId)}&tab=works`;
  return `content?kind=${encodeURIComponent(kind)}&id=${encodeURIComponent(id)}&return_to=${encodeURIComponent(returnTo)}`;
}

function safeExternalLink(value, label) {
  try {
    const url = new URL(value);
    if (!/^(https?):$/.test(url.protocol)) return "";
    return `<a class="account-home-link" href="${escapeAttribute(url.href)}" target="_blank" rel="noopener noreferrer"><svg aria-hidden="true" viewBox="0 0 24 24"><path d="M14 4h6v6M20 4l-9 9"/><path d="M19 13v5a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2V7a2 2 0 0 1 2-2h5"/></svg>${escapeHtml(label)}</a>`;
  } catch (_error) {
    return "";
  }
}

function accountAvatar(account, className = "account-avatar") {
  if (account.avatar_path) return `<img class="${className}" src="${rawUrl(account.avatar_path)}" alt="${escapeAttribute(account.name)}头像">`;
  return `<span class="${className} account-avatar-default" aria-label="${escapeAttribute(account.name)}默认头像"><svg aria-hidden="true" viewBox="0 0 24 24"><circle cx="12" cy="8" r="3.5"/><path d="M5.5 20c.9-4 3.1-6 6.5-6s5.6 2 6.5 6"/></svg></span>`;
}

function engagementMetric(icon, label, value) {
  const paths = {
    like: '<path d="M20.8 4.8a5.2 5.2 0 0 0-7.4 0L12 6.2l-1.4-1.4a5.2 5.2 0 0 0-7.4 7.4L12 21l8.8-8.8a5.2 5.2 0 0 0 0-7.4Z"/>',
    favorite: '<path d="M6 3h12v18l-6-3.8L6 21Z"/>',
    comment: '<path d="M20 11.5a7.5 7.5 0 0 1-8 7.5 8.5 8.5 0 0 1-3.7-.8L4 20l1.2-3.5A7.1 7.1 0 0 1 4 12a7.5 7.5 0 0 1 8-7.5 7.5 7.5 0 0 1 8 7Z"/>',
    share: '<path d="m3 12 18-9-6.5 18-3-7Z"/><path d="m11.5 14 3-3"/>',
  };
  const display = formatNumber(value);
  return `<span class="engagement-item" title="${escapeAttribute(`${label} ${display}`)}"><svg aria-hidden="true" viewBox="0 0 24 24">${paths[icon]}</svg><span>${display}</span></span>`;
}

function contentEngagement(metrics = {}) {
  return `<div class="content-engagement" aria-label="作品互动数据">${[
    engagementMetric("like", "点赞", metrics.likes),
    engagementMetric("favorite", "收藏", metrics.favorites),
    engagementMetric("comment", "评论", metrics.comments),
    engagementMetric("share", "分享", metrics.shares),
  ].join("")}</div>`;
}

function renderMarkdown(input, options = {}) {
  return globalThis.MeilingMarkdown.renderMarkdown(input, options);
}

function emptyState(title, detail) {
  return `<section class="content-empty"><strong>${escapeHtml(title)}</strong><p>${escapeHtml(detail)}</p><a href="index.html">返回工作台</a></section>`;
}

function matchesAccount(content, account) {
  if (account.id && content.account_id === account.id) return true;
  return content.account_name === account.name && (!account.platform || !content.platform || content.platform === account.platform);
}

function getAccountModel(snapshot) {
  const isBenchmark = accountKind === "benchmark";
  const accounts = isBenchmark
    ? snapshot.workflows.insight.benchmark_accounts
    : snapshot.workflows.self.own_accounts;
  const account = accounts.find((item) => item.id === accountId);
  if (!account) return null;
  // 两边同口径：作品明细一律以账号内容列表（account_contents）为唯一来源，
  // 账号抓到多少就展示多少；对标账号的作品按「有没有视频文案」决定能否点开，
  // 我方账号仍由 has_detail 控制置灰。
  // 只有历史工作区缺 account_contents 时，才回落到对标内容库。
  const contents = account.account_contents?.length
    ? account.account_contents
    : (isBenchmark ? snapshot.workflows.insight.benchmark_contents.filter((content) => matchesAccount(content, account)) : []);
  return {
    kind: accountKind,
    label: isBenchmark ? "对标账号档案" : "我方账号档案",
    content_count: contents.length,
    account,
    contents,
    works_contents: contents,
    analysis: {
      overview: account.documents?.overview || null,
      detail: account.documents?.detail || null,
      prior: account.documents?.prior || null,
      pattern: account.documents?.pattern || null,
    },
    analysisContent: {},
  };
}

function engagementTotal(content) {
  const metrics = content.metrics || {};
  const explicit = numericValue(metrics.interactions);
  if (explicit !== null) return explicit;
  return [metrics.likes, metrics.favorites, metrics.comments, metrics.shares]
    .map(numericValue)
    .filter((value) => value !== null)
    .reduce((sum, value) => sum + value, 0);
}

function contentState(content, kind) {
  if (kind === "benchmark") return content.analysis_state;
  if (content.analysis_state) return content.analysis_state;
  return content.status;
}

const TREND_METRICS = Object.freeze([
  { id: "total", label: "总计" },
  { id: "likes", label: "点赞" },
  { id: "comments", label: "评论" },
  { id: "favorites", label: "收藏" },
  { id: "shares", label: "转发" },
]);

function metricValue(content, metric) {
  if (metric === "total") return engagementTotal(content);
  return numericValue(content.metrics?.[metric]) ?? 0;
}

function averageMetric(contents, metric) {
  const values = contents
    .map((content) => numericValue(content.metrics?.[metric]))
    .filter((value) => value !== null);
  if (!values.length) return { value: null, samples: 0 };
  return {
    value: Math.round(values.reduce((sum, value) => sum + value, 0) / values.length),
    samples: values.length,
  };
}

/* ---------- 账号详情：照稿子补的图标与卡片（作品明细 / 图表分析） ---------- */

const KPI_ICON = {
  heart: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 20.2 4.9 13a4.6 4.6 0 0 1 6.5-6.5l.6.6.6-.6A4.6 4.6 0 0 1 19.1 13z"/></svg>',
  bookmark: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M6.4 3.4h11.2v17.2L12 17.2l-5.6 3.4z"/></svg>',
  message: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M20.4 11.6A7.6 7.6 0 0 1 12 19.2a8.6 8.6 0 0 1-3.8-.8L4 20.4l1.3-3.6A7.4 7.4 0 0 1 4 11.6 7.6 7.6 0 0 1 12 4a7.6 7.6 0 0 1 8.4 7.6z"/></svg>',
  share: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 3v11"/><path d="m7.6 7.4 4.4-4.4 4.4 4.4"/><path d="M5 14v4.6A1.4 1.4 0 0 0 6.4 20h11.2A1.4 1.4 0 0 0 19 18.6V14"/></svg>',
};

const HEAD_ICON = {
  file: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M6 3h7l5 5v13H6z"/><path d="M13 3v5h5"/></svg>',
  chart: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M4 19.4h16"/><path d="m5.6 15.6 4.2-5 3.4 2.8 4.8-6.4"/></svg>',
  trend: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M3.8 16.6 9.2 11l3.6 3.2L20.2 6.8"/><path d="M20.2 12.2V6.8h-5.4"/></svg>',
  bars: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M5.6 20V11M12 20V4.6M18.4 20v-6"/></svg>',
  grid: '<svg viewBox="0 0 24 24" aria-hidden="true"><rect x="4" y="4" width="7" height="7" rx="1.6"/><rect x="13" y="4" width="7" height="7" rx="1.6"/><rect x="4" y="13" width="7" height="7" rx="1.6"/><rect x="13" y="13" width="7" height="7" rx="1.6"/></svg>',
};

const MEDIA_ICON = {
  video: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M8.5 5.5v13l10-6.5z"/></svg>',
  article: '<svg viewBox="0 0 24 24" aria-hidden="true"><rect x="3.5" y="4.5" width="17" height="15" rx="2.5"/><path d="m6.5 15.5 3.5-3.5 3 3 2.5-2.5 3 3"/><circle cx="9" cy="9.5" r="1.2"/></svg>',
  other: '<svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="12" cy="12" r="8.5"/></svg>',
};

/* 趋势图配色（照稿子 METRIC_META） */
const TREND_COLORS = {
  total: ["#111316", "#2F5D8A"],
  likes: ["#A83C31", "#A83C31"],
  comments: ["#2F5D8A", "#2F5D8A"],
  favorites: ["#B45309", "#B45309"],
  shares: ["#111316", "#1F5C4D"],
};

/* 发布时段配色（照稿子 TIME_GROUPS） */
const TIME_GROUPS = [
  ["凌晨 00–06", "#AEB4BB", 0, 6],
  ["上午 06–12", "#7E858E", 6, 12],
  ["下午 12–18", "#3A4046", 12, 18],
  ["晚间 18–24", "#111316", 18, 24],
];

function periodColor(hour) {
  const hit = TIME_GROUPS.find((item) => item[2] <= hour && hour < item[3]);
  return hit ? hit[1] : "#111316";
}

function mediaPill(media) {
  const text = String(media || "").trim();
  if (!text || text === "未填写") return '<span class="media-pill is-other">—</span>';
  const kind = /视频|短视频|口播|直播/.test(text) ? "video" : /图文|文章|笔记/.test(text) ? "article" : "other";
  return `<span class="media-pill is-${kind}">${MEDIA_ICON[kind]}${escapeHtml(text)}</span>`;
}

function rankPill(index) {
  return `<span class="rank-pill">#${String(index).padStart(2, "0")}</span>`;
}

function shortDate(value) {
  const text = String(value || "").trim();
  if (!text) return "—";
  const stamp = dateTimestamp(text);
  if (stamp === null) return text.slice(0, 10);
  const date = new Date(stamp);
  const pad = (number) => String(number).padStart(2, "0");
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}`;
}

function headIcon(name) {
  return `<span class="head-icon" aria-hidden="true">${HEAD_ICON[name] || ""}</span>`;
}

function sectionHead(icon, title, sub, side = "") {
  return `<header class="account-section-head">${headIcon(icon)}<div><h2>${escapeHtml(title)}</h2><p>${escapeHtml(sub)}</p></div>${side ? `<div class="section-head-side">${side}</div>` : ""}</header>`;
}

/* 作品卡：与列表页同一套 .works-grid / .work-card 口径（左图右文、封面 3:4）。
   对标账号采到的内容只有视频文案，点开在右侧抽屉里读原文，不进对标内容分析页；
   我方账号仍按原口径跳内容档案。 */
function accountWorkCard(item, index, model) {
  const isBenchmark = model.kind === "benchmark";
  const canOpen = isBenchmark ? Boolean(item.transcript_path) : Boolean(item.has_detail);
  const targetId = isBenchmark ? (item.key || item.id) : item.id;
  const cover = item.cover_path
    ? `<img src="${rawUrl(item.cover_path)}" alt="" loading="lazy">`
    : `<span class="work-fallback">${platformIcon(platformKey(item.platform))}</span>`;
  const visibility = (!isBenchmark && item.visibility)
    ? `<span class="status-pill tone-gray">${escapeHtml(item.visibility)}</span>`
    : "";
  const linkText = isBenchmark
    ? (canOpen ? "查看视频文案 →" : "未采集视频文案")
    : (canOpen ? "查看详情 →" : "未采集详情");
  const inner = `<span class="work-cover">${cover}</span>
    <div class="work-body">
      <div class="work-head">${rankPill(index)}${mediaPill(item.media_format)}${visibility}<span class="work-date">${escapeHtml(shortDate(item.published_at))}</span></div>
      <div class="work-title">${escapeHtml(item.title || "—")}</div>
      <div class="work-author">${platformPill(item.platform)}<span class="work-author-name">${escapeHtml(item.account_name || "—")}</span></div>
      <div class="work-foot">${contentEngagement(item.metrics)}<span class="work-link${canOpen ? "" : " is-muted"}">${escapeHtml(linkText)}</span></div>
    </div>`;
  if (canOpen && isBenchmark) {
    return `<div class="work-card is-link" role="link" tabindex="0" title="查看视频文案" data-work-transcript="${escapeAttribute(item.key || item.id)}">${inner}</div>`;
  }
  if (canOpen) {
    return `<a class="work-card is-link" href="${contentUrl(model.kind, targetId)}" title="打开详情">${inner}</a>`;
  }
  return `<div class="work-card">${inner}</div>`;
}

/* 视频文案抽屉：只放在对标账号的作品明细里，打开时现读正式文案文件。 */
function openTranscriptDrawer(content) {
  elements.transcriptKicker.textContent = [content.account_name, content.platform].filter(Boolean).join(" · ") || "视频文案";
  elements.transcriptTitle.textContent = content.title || "视频文案";
  elements.transcriptBody.innerHTML = '<div class="content-loading"><span></span><p>正在读取视频文案</p></div>';
  if (!elements.transcriptPanel.open) elements.transcriptPanel.showModal();
}

async function showWorkTranscript(key) {
  const content = (state.model?.contents || []).find((item) => (item.key || item.id) === key);
  if (!content?.transcript_path) return;
  openTranscriptDrawer(content);
  try {
    const file = await fetchDocument(content.transcript_path);
    elements.transcriptBody.innerHTML = `<article class="markdown-body">${renderMarkdown(file.content, { documentPath: file.path || content.transcript_path })}</article>`;
  } catch (error) {
    elements.transcriptBody.innerHTML = `<p class="muted">视频文案读取失败：${escapeHtml(error.message)}</p>`;
  }
}

function renderTrend(contents, metric) {
  const metricMeta = TREND_METRICS.find((item) => item.id === metric) || TREND_METRICS[0];
  const points = contents
    .map((content) => ({
      timestamp: dateTimestamp(content.published_at),
      label: content.published_at || "未填写",
      value: metricValue(content, metricMeta.id),
    }))
    .filter((point) => point.timestamp !== null)
    .sort((left, right) => left.timestamp - right.timestamp)
    .slice(-16);
  if (points.length < 2) return '<div class="account-empty-inline">至少需要两条带发布时间的作品，才能绘制互动趋势。</div>';
  const width = 920;
  const height = 260;
  const padding = { top: 22, right: 28, bottom: 40, left: 54 };
  const maxValue = Math.max(...points.map((point) => point.value), 1);
  const innerWidth = width - padding.left - padding.right;
  const innerHeight = height - padding.top - padding.bottom;
  const coordinates = points.map((point, index) => ({
    ...point,
    x: padding.left + index / Math.max(points.length - 1, 1) * innerWidth,
    y: padding.top + (1 - point.value / maxValue) * innerHeight,
  }));
  const line = coordinates.map((point, index) => `${index === 0 ? "M" : "L"}${point.x.toFixed(1)},${point.y.toFixed(1)}`).join(" ");
  const area = `${line} L${coordinates.at(-1).x.toFixed(1)},${height - padding.bottom} L${coordinates[0].x.toFixed(1)},${height - padding.bottom} Z`;
  const ticks = [0, .5, 1].map((position) => ({
    y: padding.top + (1 - position) * innerHeight,
    value: Math.round(maxValue * position),
  }));
  const labels = coordinates.filter((_, index) => index === 0 || index === coordinates.length - 1 || index % Math.ceil(coordinates.length / 4) === 0);
  return `<svg class="trend-chart" data-metric="${metricMeta.id}" viewBox="0 0 ${width} ${height}" role="img" aria-label="${escapeAttribute(`${metricMeta.label}趋势`)}">
    <defs>${Object.entries(TREND_COLORS).map(([key, pair]) => `<linearGradient id="account-trend-fill-${key}" x1="0" x2="0" y1="0" y2="1"><stop offset="0" stop-color="${pair[0]}" stop-opacity=".28"/><stop offset="1" stop-color="${pair[1]}" stop-opacity="0"/></linearGradient>`).join("")}</defs>
    ${ticks.map((tick) => `<line class="trend-grid" x1="${padding.left}" x2="${width - padding.right}" y1="${tick.y}" y2="${tick.y}"/><text class="trend-axis" x="${padding.left - 10}" y="${tick.y + 4}" text-anchor="end">${formatNumber(tick.value)}</text>`).join("")}
    <path class="trend-area" d="${area}"/><path class="trend-line" d="${line}"/>
    ${coordinates.map((point) => `<circle class="trend-point" cx="${point.x}" cy="${point.y}" r="3.5"><title>${escapeHtml(`${point.label}：${formatNumber(point.value)} ${metricMeta.label}`)}</title></circle>`).join("")}
    ${labels.map((point) => `<text class="trend-axis" x="${point.x}" y="${height - 14}" text-anchor="middle">${escapeHtml(formatShortDate(point.label))}</text>`).join("")}
  </svg>`;
}

function formatShortDate(value) {
  const timestamp = dateTimestamp(value);
  if (timestamp === null) return "—";
  return new Intl.DateTimeFormat("zh-CN", { month: "numeric", day: "numeric" }).format(timestamp);
}

function renderPublishingRhythm(contents) {
  const counts = new Array(24).fill(0);
  let total = 0;
  for (const content of contents) {
    const timestamp = dateTimestamp(content.published_at);
    if (timestamp === null) continue;
    counts[new Date(timestamp).getHours()] += 1;
    total += 1;
  }
  if (!total) return '<div class="account-empty-inline">作品主表还没有可用的发布时间，暂时无法统计发布规律。</div>';
  const maximum = Math.max(...counts, 1);
  const bars = counts.map((count, hour) => {
    const isPeak = count === maximum && count > 0;
    return `<div class="bar-col" title="${String(hour).padStart(2, "0")}:00 发布 ${count} 条">`
      + `<span class="bar-count${isPeak ? " is-peak" : ""}">${count || ""}</span>`
      + `<span class="bar${isPeak ? " is-peak" : ""}${count ? "" : " is-empty"}" data-height="${Math.max(4, Math.round(count / maximum * 132))}"${count ? ` data-bar="${periodColor(hour)}"` : ""}></span></div>`;
  }).join("");
  const labels = counts.map((_, hour) => `<span class="bar-label">${hour % 3 === 0 ? String(hour).padStart(2, "0") : ""}</span>`).join("");
  const legend = TIME_GROUPS.map(([name, color]) => `<span class="legend-item"><i data-dot="${color}"></i>${escapeHtml(name)}</span>`).join("");
  return `<div class="rhythm-body"><div class="bars">${bars}</div><div class="bar-labels">${labels}</div><div class="legend">${legend}</div></div>`;
}


/* ---------- 互动数据分布 ----------
   口径与「账号内容规律分析」报告的三档**完全一致**（同一套判定）：
     互动数＝核心互动数＝点赞＋收藏＋评论（不含转发）；三项缺任一项的内容不参与；
     高＝互动数 ≥ 账号中位数 × 2 且排在全账号前 30%；
     低＝互动数 ≤ 账号中位数 ÷ 1.5 且排在全账号后 30%；其余为中档；
     取到互动数的内容少于 5 条时不分档（样本不足），只按互动数排序列出。
   与 `执行单元/账号衍生字段计算批处理.compute_account_rank` 同源，改动要同步。 */
const DISTRIBUTION_MIN_SAMPLES = 5;
const DISTRIBUTION_HEAD_SHARE = 0.3;
const DISTRIBUTION_TIERS = [
  ["high", "高档"],
  ["middle", "中档"],
  ["low", "低档"],
];

function distributionInteraction(content) {
  const metrics = content.metrics || {};
  const likes = numericValue(metrics.likes);
  const comments = numericValue(metrics.comments);
  const favorites = numericValue(metrics.favorites);
  if (likes === null || comments === null || favorites === null) return null;
  return likes + comments + favorites;
}

/* 三条线（真实互动数）。values 必须是升序；样本不足返回 null。 */
function distributionScale(values) {
  const size = values.length;
  if (size < DISTRIBUTION_MIN_SAMPLES) return null;
  // 「2 × 中位数」用整数表达：偶数条是两个中间值之和，奇数条是中间值 × 2。
  const doubledMedian = values[(size - 1) >> 1] + values[size >> 1];
  const cut = Math.max(1, Math.ceil(size * DISTRIBUTION_HEAD_SHARE));
  const descending = [...values].reverse();
  const lowCeilingTripled = Math.min(doubledMedian, 3 * descending[size - cut]);
  return {
    median: doubledMedian / 2,
    highFloor: Math.max(doubledMedian, descending[cut - 1]),
    lowCeilingTripled,
    lowCeiling: lowCeilingTripled / 3,
  };
}

function distributionTier(scale, value) {
  if (!scale) return "insufficient";
  if (scale.highFloor > 0 && value >= scale.highFloor) return "high";
  if (scale.lowCeilingTripled > 0 && value * 3 <= scale.lowCeilingTripled) return "low";
  return "middle";
}

/* 对数轴的整十次幂刻度：1000 → 1000，10000 → 1万 */
function formatAxisTick(value) {
  if (value >= 100000000) return `${value / 100000000}亿`;
  if (value >= 10000) return `${value / 10000}万`;
  return String(value);
}

function renderDistribution(contents) {
  const samples = contents
    .map((content) => ({ content, value: distributionInteraction(content) }))
    .filter((item) => item.value !== null)
    .sort((left, right) => right.value - left.value);
  if (!samples.length) {
    return '<div class="account-empty-inline">这批内容还没有可用的互动数据（点赞、收藏、评论三项齐全才算），暂时画不出互动数据分布。</div>';
  }
  const values = samples.map((item) => item.value).sort((left, right) => left - right);
  const scale = distributionScale(values);
  // 对数刻度：下端取到相邻的整十次幂，上端只比最大值高一点（不硬取下一个整十次幂，
  // 否则最大值离得远时右半张会空着）。刻度本身仍只画落在域内的整十次幂。
  const peak = Math.max(values.at(-1), 1);
  const domainMin = Math.max(1, Math.pow(10, Math.floor(Math.log10(Math.max(values[0], 1)))));
  const domainMax = Math.max(peak * 1.15, domainMin * 2);
  const span = Math.log10(domainMax) - Math.log10(domainMin);
  const position = (value) => {
    if (span <= 0) return 0;
    const ratio = (Math.log10(Math.max(value, 1)) - Math.log10(domainMin)) / span;
    return Math.min(100, Math.max(0, ratio * 100));
  };
  const ticks = [];
  for (
    let exponent = Math.floor(Math.log10(domainMin) + 1e-9);
    exponent <= Math.floor(Math.log10(domainMax) + 1e-9);
    exponent += 1
  ) {
    ticks.push(Math.pow(10, exponent));
  }
  const guides = scale
    ? [["low", "低档线", scale.lowCeiling], ["median", "中位数", scale.median], ["high", "高档线", scale.highFloor]]
      .sort((left, right) => left[2] - right[2])
    : [];
  // 三条线的标签各占一行（低档线 / 中位数 / 高档线），行由 class 决定，挨得多近都不会叠。
  const guideLabels = guides
    .map(([key, label, value]) => `<span class="distribution-guide-label is-${key}" data-pos="${position(value).toFixed(2)}">${label} ${formatNumber(value)}</span>`)
    .join("");
  const guideLines = guides
    .map(([key, , value]) => `<span class="distribution-guide is-${key}" data-pos="${position(value).toFixed(2)}"></span>`)
    .join("");
  const rows = samples.map((item) => {
    const tier = distributionTier(scale, item.value);
    const title = item.content.title || "未命名内容";
    const left = position(item.value).toFixed(2);
    return `<div class="distribution-row"><span class="distribution-label" title="${escapeAttribute(title)}">${escapeHtml(title)}</span><span class="distribution-slot"><i class="distribution-bar" data-pos="${left}"></i><i class="distribution-dot is-${tier}" data-pos="${left}" title="${escapeAttribute(`${title}：${formatNumber(item.value)} 互动`)}"></i><b class="distribution-value" data-pos="${left}">${formatNumber(item.value)}</b></span></div>`;
  }).join("");
  const axis = ticks
    .map((value) => `<span class="distribution-tick" data-pos="${position(value).toFixed(2)}">${formatAxisTick(value)}</span>`)
    .join("");
  const legend = DISTRIBUTION_TIERS
    .map(([id, label]) => `<span class="distribution-legend-item"><i class="is-${id}"></i>${label}</span>`)
    .join("");
  const note = scale
    ? "按账号内相对排名分档：高＝互动数 ≥ 中位数 × 2 且排前 30%，低＝≤ 中位数 ÷ 1.5 且排后 30%"
    : `取到互动数的内容不足 ${DISTRIBUTION_MIN_SAMPLES} 条，暂不分档`;
  return `<div class="distribution-body">
    <div class="distribution-legend">${legend}<span class="distribution-legend-note">${note}</span></div>
    <div class="distribution-chart">
      <div class="distribution-guide-labels"><span class="distribution-guide-lane">${guideLabels}</span></div>
      <div class="distribution-frame">
        <div class="distribution-guides"><span class="distribution-guides-lane">${guideLines}</span></div>
        <div class="distribution-rows">${rows}</div>
      </div>
      <div class="distribution-axis"><span class="distribution-axis-lane">${axis}</span></div>
    </div>
  </div>`;
}

function renderCharts(model) {
  const { contents } = model;
  const kpis = [
    ["heart", "#A83C31", "#F6F7F8", "单篇平均点赞", "likes"],
    ["bookmark", "#B45309", "#F6F7F8", "单篇平均收藏", "favorites"],
    ["message", "#2F5D8A", "#F4F6F7", "单篇平均评论", "comments"],
    ["share", "#111316", "#EDEFF1", "单篇平均转发", "shares"],
  ].map(([mark, tone, tint, label, metric]) => ({ mark, tone, tint, label, ...averageMetric(contents, metric) }));
  const selectedTrend = TREND_METRICS.find((item) => item.id === state.trendMetric) || TREND_METRICS[0];
  const trendRows = Math.min(contents.filter((content) => dateTimestamp(content.published_at) !== null).length, 16);
  const hourCounts = new Array(24).fill(0);
  for (const content of contents) {
    const timestamp = dateTimestamp(content.published_at);
    if (timestamp !== null) hourCounts[new Date(timestamp).getHours()] += 1;
  }
  const peakCount = Math.max(...hourCounts, 0);
  const peakHour = peakCount > 0 ? hourCounts.indexOf(peakCount) : null;
  const peakChip = peakHour === null ? "" : `<span class="section-head-action is-dark">高峰 ${String(peakHour).padStart(2, "0")}:00 · ${formatNumber(peakCount)} 条</span>`;
  const distSamples = contents.filter((content) => distributionInteraction(content) !== null).length;
  const distChip = `<span class="section-head-action">${formatNumber(distSamples)} 条有互动数据</span>`;
  const seg = `<div class="trend-segmented" role="group" aria-label="互动趋势指标">${TREND_METRICS.map((item) => `<button class="trend-segment ${item.id === selectedTrend.id ? "is-active" : ""}" type="button" data-trend-metric="${item.id}">${item.label}</button>`).join("")}</div>`;
  return `<section class="account-section-card">${sectionHead("chart", "数据概览", "按已录入互动数据的内容计算", `<span class="section-head-action">${formatNumber(contents.length)} 条采集作品</span>`)}
    <div class="analysis-kpis">${kpis.map((item) => `<div class="kpi"><span class="kpi-icon" data-tone="${item.tone}" data-tint="${item.tint}">${KPI_ICON[item.mark]}</span><div class="kpi-copy"><span>${escapeHtml(item.label)}</span><strong>${item.value === null ? "—" : formatNumber(item.value)}</strong></div></div>`).join("")}</div>
  </section>
  <section class="account-section-card">${sectionHead("trend", "互动趋势", `横轴为发布时间，取最近 ${trendRows} 条`, seg)}
    <div class="trend-body">${renderTrend(contents, selectedTrend.id)}</div>
  </section>
  <section class="account-section-card">${sectionHead("bars", "发布规律", "按已采集作品的发布时间统计，柱高为条数、颜色为时段", peakChip)}
    ${renderPublishingRhythm(contents)}
  </section>
  <section class="account-section-card">${sectionHead("bars", "互动数据分布", "横轴为互动数（对数刻度），竖线为账号内中位数与高低档线", distChip)}
    ${renderDistribution(contents)}
  </section>`;
}


function analysisDocument(document, content, fallback) {
  if (!document?.path) {
    return `<section class="analysis-document"><header><div><h2>${escapeHtml(fallback)}</h2><small>尚未建立正式资料</small></div></header><div class="account-empty-inline">当前账号还没有${escapeHtml(fallback)}，建立对应正式文档后会自动显示在这里。</div></section>`;
  }
  const body = content === undefined
    ? '<div class="inline-loading"><span></span><p>正在读取正式资料</p></div>'
    : `<article class="markdown-body">${renderMarkdown(content, { documentPath: document.path })}</article>`;
  return `<section class="analysis-document"><header><p class="analysis-document-path"><span>资料路径</span><code>${escapeHtml(document.path)}</code></p></header>${body}</section>`;
}

function renderAnalysis(model) {
  const preferred = model.analysis.detail?.path ? "detail" : "overview";
  const document = model.analysis[preferred];
  // 取词按账号类型：我方链路只有「账号详细诊断」一份文档，没有「概要分析」这一环；
  // 对标账号才有概要 / 详细两份。（2026-09-29 修正，原先两侧共用一句会给我方显示「概要分析」）
  const fallback = model.kind === "own"
    ? "账号详细诊断"
    : preferred === "detail" ? "详细分析" : "概要分析";
  // 账号内容规律分析已独立成 tab，这里只放详细分析（或概要分析）。
  return `<div class="document-stack">${analysisDocument(document, model.analysisContent[preferred], fallback)}</div>`;
}

function renderPattern(model) {
  const document = model.analysis.pattern;
  if (!document?.path) {
    // 这份分析有门槛：账号要有可用特征才会生成，没有就说明原因，不留空文档位。
    return `<section class="analysis-document"><header><div><h2>内容规律分析</h2><small>尚未建立正式资料</small></div></header><div class="account-empty-inline">这个账号还没有生成内容规律分析。账号详细分析（或自我账号详细诊断）跑到最后一步、且该账号有可用特征时，才会生成这份分析。</div></section>`;
  }
  return `<div class="document-stack">${analysisDocument(document, model.analysisContent.pattern, "内容规律分析")}</div>`;
}

function uniqueValues(items, key) {
  return [...new Set(items.map((item) => String(item[key] || "").trim()).filter(Boolean))]
    .sort((left, right) => left.localeCompare(right, "zh-CN"));
}

function selectOptions(values, selected, allLabel) {
  return [`<option value="">${escapeHtml(allLabel)}</option>`, ...values.map((value) => `<option value="${escapeAttribute(value)}"${value === selected ? " selected" : ""}>${escapeHtml(value)}</option>`)].join("");
}

function filteredWorks(model) {
  const contentList = model.works_contents || model.contents;
  const filters = state.works;
  const queryText = filters.query.trim().toLocaleLowerCase("zh-CN");
  return contentList.filter((content) => {
    const stateValue = contentState(content, model.kind);
    const searchable = [content.id, content.title, content.platform, content.media_format, content.data_level, stateValue].join(" ").toLocaleLowerCase("zh-CN");
    return (!queryText || searchable.includes(queryText))
      && (!filters.platform || content.platform === filters.platform)
      && (!filters.dataLevel || content.data_level === filters.dataLevel)
      && (!filters.state || stateValue === filters.state);
  }).slice().sort((left, right) => {
    const leftTime = dateTimestamp(left.published_at);
    const rightTime = dateTimestamp(right.published_at);
    if (leftTime === null && rightTime === null) return left.title.localeCompare(right.title, "zh-CN");
    if (leftTime === null) return 1;
    if (rightTime === null) return -1;
    return filters.sort === "published-asc" ? leftTime - rightTime : rightTime - leftTime;
  });
}

function renderWorks(model) {
  const contentList = model.works_contents || model.contents;
  const items = filteredWorks(model);
  const filters = state.works;
  const stateOptions = uniqueValues(contentList.map((content) => ({ state: contentState(content, model.kind) })), "state");
  const list = items.length
    ? `<div class="works-grid">${items.map((content, index) => accountWorkCard(content, index + 1, model)).join("")}</div>`
    : '<div class="account-empty-inline">没有符合当前筛选条件的作品。</div>';
  const worksHint = model.kind === "benchmark"
    ? "展示账号采集到的全部内容；点开可读视频文案，未采集文案的内容不可点"
    : "展示账号诊断采集的全部内容；未采集详情的内容也会列出";
  return `<section class="account-section-card">${sectionHead("grid", "作品明细", worksHint, `<span class="section-head-action">共 ${formatNumber(contentList.length)} 条</span>`)}
    <div class="data-toolbar data-toolbar-benchmark"><div class="toolbar-filter-group"><input type="search" data-works-filter="query" value="${escapeAttribute(filters.query)}" placeholder="筛选标题或账号"><select data-works-filter="platform" aria-label="按平台筛选">${selectOptions(uniqueValues(contentList, "platform"), filters.platform, "全部平台")}</select><select data-works-filter="dataLevel" aria-label="按数据水平筛选">${selectOptions(uniqueValues(contentList, "data_level"), filters.dataLevel, "全部数据水平")}</select><select data-works-filter="state" aria-label="按状态筛选">${selectOptions(stateOptions, filters.state, "全部状态")}</select><select data-works-filter="sort" aria-label="排序方式"><option value="published-desc"${filters.sort === "published-desc" ? " selected" : ""}>发布时间：最新在前</option><option value="published-asc"${filters.sort === "published-asc" ? " selected" : ""}>发布时间：最早在前</option></select></div><span class="muted">${formatNumber(items.length)} 条</span></div>${list}</section>`;
}


function isMeaningfulStatus(value) {
  return Boolean(String(value || "").trim()) && !/待补齐|未填写|未知|暂无/.test(String(value));
}

function renderAccountNotes(model) {
  if (!state.notesOpen) return "";
  const { account, kind } = model;
  const prior = model.analysis.prior;
  const manualNotes = account.notes
    ? `<article class="account-note-prose">${renderMarkdown(account.notes)}</article>`
    : '<p class="account-note-empty-copy">账号主表尚未填写人工备注。</p>';
  const priorNotes = kind === "benchmark"
    ? (!prior?.path
      ? '<p class="account-note-empty-copy">尚未建立人工先验说明。</p>'
      : model.analysisContent.prior === undefined
        ? '<div class="inline-loading"><span></span><p>正在读取人工先验说明</p></div>'
        : `<article class="account-note-prose">${renderMarkdown(model.analysisContent.prior, { documentPath: prior.path })}</article>`)
    : "";
  const images = account.images?.length
    ? `<div class="account-note-images">${account.images.map((path, index) => `<a href="${rawUrl(path)}" target="_blank" rel="noopener noreferrer"><img src="${rawUrl(path)}" alt="${escapeAttribute(`${account.name}资料图 ${index + 1}`)}" loading="lazy"></a>`).join("")}</div>`
    : "";
  const noteSections = [`<section><h3>人工备注</h3>${manualNotes}</section>`];
  if (kind === "benchmark") noteSections.push(`<section><h3>人工先验说明</h3>${priorNotes}</section>`);
  const subtitle = kind === "benchmark" ? "人工补充与先验资料，不参与图表计算。" : "人工补充与资料图片，不参与图表计算。";
  return `<section class="account-note-inline" id="account-notes"><header><div><h2>账号备注</h2><p>${subtitle}</p></div><button class="account-note-close" type="button" data-account-action="toggle-notes">收起</button></header><div class="account-note-grid${kind === "own" ? " account-note-grid--single" : ""}">${noteSections.join("")}</div>${images ? `<section class="account-note-visuals"><h3>图片说明</h3>${images}</section>` : ""}</section>`;
}

/* hero 右侧四个统计块的图标（描边风格，颜色由 .stat-* 的 --tone 决定） */
const HERO_ICON = {
  users: '<svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="12" cy="8" r="3.5"/><path d="M5 20c.9-4 3.2-6 7-6s6.1 2 7 6"/></svg>',
  heart: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M20.8 4.8a5.2 5.2 0 0 0-7.4 0L12 6.2l-1.4-1.4a5.2 5.2 0 0 0-7.4 7.4L12 21l8.8-8.8a5.2 5.2 0 0 0 0-7.4Z"/></svg>',
  layers: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="m12 3 8.5 4.5L12 12 3.5 7.5Z"/><path d="m4 12 8 4.3 8-4.3M4 16.4l8 4.3 8-4.3"/></svg>',
  check: '<svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="12" cy="12" r="9"/><path d="m8 12 3 3 5-6"/></svg>',
  /* 适配 / 判断用的图标（稿子 ICONS 的 target / star） */
  target: '<svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="12" cy="12" r="8"/><circle cx="12" cy="12" r="3.4"/></svg>',
  star: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 3.2l2.1 4.9 5.2.6-3.9 3.6 1 5.2L12 14.9l-4.4 2.6 1-5.2-3.9-3.6 5.2-.6z"/></svg>',
};

/* hero 标签行里的「适配 / 判断」药丸：照稿子 hero_tags —— 各带图标与分色，
   顺序是 适配 在前、判断 在后；空值 / 未填写不渲染。 */
function heroJudgement(mark, icon, value) {
  if (!isMeaningfulStatus(value)) return "";
  return `<span class="account-status-tag hero-${mark}">${HERO_ICON[icon]}<span>${escapeHtml(String(value).trim())}</span></span>`;
}

function renderSummary(model) {
  const { account, kind, contents } = model;
  let secondaryMetric;
  let collectMetric;
  if (kind === "benchmark") {
    secondaryMetric = ["已分析作品", account.analyzed_content_count ?? contents.filter((item) => item.analysis_state === "已完成").length];
    collectMetric = ["已采集作品", contents.length];
  } else {
    const works = model.works_contents || contents;
    const publicCount = works.filter((item) => item.visibility === "公开" || item.visibility === "public").length;
    collectMetric = ["收录内容", works.length];
    secondaryMetric = ["已发布", account.published_count ?? publicCount];
  }
  /* 顺序照稿子 hero_tags：先「适配」，再「质量判断」 */
  const judgements = kind === "benchmark"
    ? [heroJudgement("fit", "target", account.fit), heroJudgement("quality", "star", account.quality)].join("")
    : "";
  const tags = (account.tags || []).map((tag) => `<span class="account-status-tag tone-neutral">${escapeHtml(tag)}</span>`).join("");
  // 动作沿用原来的两个：查看备注 / 打开主页
  const actions = `<button class="account-note-toggle" type="button" data-account-action="toggle-notes">${state.notesOpen ? "收起备注" : "查看备注"}</button>${safeExternalLink(account.homepage, "打开主页")}`;
  const stats = [
    ["followers", "users", "粉丝数", formatNumber(account.followers)],
    ["likes", "heart", "获赞数", formatNumber(account.likes)],
    ["collect", "layers", collectMetric[0], formatNumber(collectMetric[1])],
    ["analyzed", "check", secondaryMetric[0], formatNumber(secondaryMetric[1])],
  ].map(([mark, icon, label, value]) => `<div class="account-hero-stat stat-${mark}"><span class="account-hero-stat-icon">${HERO_ICON[icon]}</span><div class="account-hero-stat-copy"><span>${escapeHtml(label)}</span><strong>${escapeHtml(value)}</strong></div></div>`).join("");
  return `<section class="account-hero">
    <div class="account-hero-glow"></div>
    <svg class="account-hero-rings" viewBox="0 0 460 460" aria-hidden="true"><circle cx="230" cy="230" r="74"/><circle cx="230" cy="230" r="130"/><circle cx="230" cy="230" r="186"/><circle cx="230" cy="230" r="230"/></svg>
    <div class="account-hero-id">
      ${accountAvatar(account, "account-hero-avatar")}
      <div class="account-hero-copy">
        <p class="account-hero-eyebrow">${kind === "benchmark" ? "BENCHMARK ACCOUNT" : "OWN ACCOUNT"}</p>
        <h1>${escapeHtml(account.name)}</h1>
        <p class="account-hero-intro">${escapeHtml(account.intro || "当前账号尚未填写简介。").replaceAll("\n", "<br>")}</p>
        <div class="account-hero-tags">${platformTag(account.platform)}${genderTag(account.gender)}${judgements}${tags}</div>
        <div class="account-hero-actions">${actions}</div>
      </div>
    </div>
    <div class="account-hero-stats">${stats}</div>
  </section>${renderAccountNotes(model)}`;
}


/* 左侧栏导航高亮（详情页共用） */
function markNavActive(key) {
  document.querySelectorAll(".primary-nav .nav-item").forEach((item) => {
    item.classList.toggle("is-active", item.dataset.navKey === key);
  });
}

/* ⛔ 工作台 CSP 是 style-src 'self'（无 unsafe-inline）—— HTML 里的行内 style 会被丢掉。
   所以带变量的配色 / 柱高一律先写 data-*，渲染完再用 CSSOM 赋上（CSSOM 不受 CSP 限制）。 */
function applyAccountSkin(root) {
  const scope = root || document;
  scope.querySelectorAll(".kpi-icon[data-tone]").forEach((el) => {
    el.style.setProperty("--tone", el.dataset.tone);
    el.style.setProperty("--tint", el.dataset.tint);
  });
  scope.querySelectorAll(".bars .bar[data-height]").forEach((el) => {
    el.style.height = `${el.dataset.height}px`;
    if (el.dataset.bar) el.style.setProperty("--bar", el.dataset.bar);
  });
  scope.querySelectorAll(".legend-item i[data-dot]").forEach((el) => el.style.setProperty("--dot", el.dataset.dot));
  scope.querySelectorAll(".distribution-chart [data-pos]").forEach((el) => el.style.setProperty("--pos", el.dataset.pos));
  const chart = scope.querySelector(".trend-chart[data-metric]");
  if (chart) {
    const pair = TREND_COLORS[chart.dataset.metric] || TREND_COLORS.total;
    chart.querySelectorAll(".trend-line").forEach((el) => { el.style.stroke = pair[0]; });
    chart.querySelectorAll(".trend-point").forEach((el) => { el.style.fill = pair[0]; });
    chart.querySelectorAll(".trend-area").forEach((el) => { el.style.fill = `url(#account-trend-fill-${chart.dataset.metric})`; });
  }
}

function renderAccount() {
  const model = state.model;
  if (!model) return;
  markNavActive(model.label === "对标账号档案" ? "benchmark-accounts-v2" : "own-accounts");

  // 四个分区用 tab 切分，不铺成一页（作品明细条数多，铺开会把页面拉得很长）
  const tabs = [
    { id: "charts", label: "图表分析", body: renderCharts(model) },
    {
      id: "analysis",
      label: model.kind === "own" ? "账号诊断" : "账号分析",
      body: renderAnalysis(model),
    },
    { id: "pattern", label: "内容规律分析", body: renderPattern(model) },
    { id: "works", label: "作品明细", body: renderWorks(model) },
  ];
  const activeId = tabs.some((tab) => tab.id === state.activeTab) ? state.activeTab : "charts";
  state.activeTab = activeId;
  const active = tabs.find((tab) => tab.id === activeId);
  const nav = tabs
    .map((tab) => {
      const on = tab.id === activeId;
      return `<button class="account-tab${on ? " is-active" : ""}" type="button" data-account-tab="${tab.id}" aria-selected="${on}">${escapeHtml(tab.label)}</button>`;
    })
    .join("");

  const accountGroup = document.querySelector("#account-crumb-group");
  const groupText = { "对标账号档案": "对标账号", "我方账号档案": "我的账号" }[model.label] || model.label;
  if (accountGroup) {
    accountGroup.textContent = groupText;
    accountGroup.href = groupText === "我的账号" ? "index.html#own-accounts" : "index.html#benchmark-accounts-v2";
  }
  const crumbTitle = String(model.account?.name || model.label || "");
  elements.breadcrumb.textContent = crumbTitle.length > 34 ? `${crumbTitle.slice(0, 34)}…` : crumbTitle;
  elements.breadcrumb.title = crumbTitle;
  document.title = `${model.account.name}｜媒灵账号档案`;
  elements.view.innerHTML = `${renderSummary(model)}<nav class="account-tabs" aria-label="账号档案分区">${nav}</nav><div class="account-tab-panel">${active.body}</div>`;
  applyAccountSkin(elements.view);
}

function fallbackListUrl() {
  return accountKind === "own" ? "index.html#own-accounts" : "index.html#benchmark-accounts-v2";
}

/**
 * 只接受「工作台内的相对路径」形式的 return_to，其余一律判非法（与 content.js 同口径）。
 * 以 / 开头或带协议的绝对地址会被拒绝，避免被塞外链。
 */
function safeReturnPath(value) {
  const candidate = String(value || "").trim();
  if (!candidate || candidate.startsWith("/") || candidate.startsWith("\\") || /^[a-z][a-z\d+.-]*:/i.test(candidate)) return "";
  try {
    const target = new URL(candidate, `${location.origin}/`);
    if (target.origin !== location.origin) return "";
    const path = target.pathname.replace(/^\//, "");
    if (path === "index.html") {
      if (!target.hash) return path;
      return /^#(?:own-accounts|benchmark-accounts-v2|benchmark-contents|topics|creation-board|publications)$/.test(target.hash)
        ? `${path}${target.hash}`
        : "";
    }
    if (path === "topic" || path === "topic.html") {
      const id = target.searchParams.get("id");
      if (id) return `${path}?id=${encodeURIComponent(id)}`;
    }
    if (path === "content" || path === "content.html") {
      const kind = target.searchParams.get("kind");
      const id = target.searchParams.get("id");
      if (id && ["benchmark", "own"].includes(kind)) {
        return `${path}?kind=${encodeURIComponent(kind)}&id=${encodeURIComponent(id)}`;
      }
    }
  } catch (_error) {
    // 非法 return 值一律回退到对应的列表
  }
  return "";
}

function returnToPreviousPage() {
  // 优先回到**来源视图**（列表页 / 首页在链接里带过来的 return_to）；
  // 来源缺失或非法时，才退回对应的列表。
  const requestedReturnTo = new URLSearchParams(location.search).get("return_to") || "";
  location.assign(safeReturnPath(requestedReturnTo) || fallbackListUrl());
}

async function fetchDocument(path) {
  const response = await fetch(`/api/file?path=${encodeURIComponent(path)}`, { cache: "no-store" });
  if (!response.ok) throw new Error(`document_read_failed:${response.status}`);
  return response.json();
}

async function hydrateAnalysis(model) {
  const preferredKey = model.analysis.detail?.path ? "detail" : "overview";
  const entries = [[preferredKey, model.analysis[preferredKey]]];
  if (model.analysis.pattern?.path) entries.push(["pattern", model.analysis.pattern]);
  if (state.notesOpen) entries.push(["prior", model.analysis.prior]);
  const pending = entries.filter(([key, document]) => document?.path && model.analysisContent[key] === undefined);
  await Promise.all(pending.map(async ([key, document]) => {
    try {
      const file = await fetchDocument(document.path);
      model.analysisContent[key] = file.content;
    } catch (_error) {
      model.analysisContent[key] = "# 资料读取失败\n\n当前正式资料暂时无法读取，请返回工作台刷新后重试。";
    }
  }));
}

async function loadAccount({ force = false } = {}) {
  const hasPreviousAccount = Boolean(state.model);
  elements.reload.disabled = true;
  if (!hasPreviousAccount) {
    elements.view.innerHTML = '<div class="content-loading"><span></span><p>正在装配统一账号档案</p></div>';
  }
  try {
    if (location.protocol === "file:") {
      elements.view.innerHTML = emptyState("账号档案需要通过媒灵工作台打开", "account.html 是可编辑模板，不携带具体账号数据。请启动本地服务后，从账号列表进入。");
      return;
    }
    if (!accountId || !["benchmark", "own"].includes(accountKind)) {
      elements.view.innerHTML = emptyState("账号地址无效", "请从媒灵工作台的账号列表进入。");
      return;
    }
    const response = await fetch(force ? "/api/refresh" : "/api/snapshot", { cache: "no-store" });
    if (!response.ok) throw new Error(`snapshot_read_failed:${response.status}`);
    const snapshot = await response.json();
    const model = getAccountModel(snapshot);
    if (!model) {
      elements.view.innerHTML = emptyState("未找到该账号", "该记录可能已被移动或删除，请返回账号列表刷新后重试。");
      return;
    }
    state.model = model;
    renderAccount();
    await hydrateAnalysis(model);
    renderAccount();
  } catch (error) {
    if (hasPreviousAccount) {
      showToast("资料刷新失败，当前仍显示上一次结果");
      return;
    }
    elements.view.innerHTML = emptyState("账号档案暂时不可用", error.message);
  } finally {
    elements.reload.disabled = false;
  }
}

function restoreWorksInput(target) {
  if (target !== "query") return;
  requestAnimationFrame(() => {
    const input = elements.view.querySelector('[data-works-filter="query"]');
    if (!input) return;
    input.focus();
    input.setSelectionRange(input.value.length, input.value.length);
  });
}

document.addEventListener("click", async (event) => {
  if (event.target.closest("#back-to-previous")) {
    returnToPreviousPage();
    return;
  }
  if (!state.model) return;
  const transcriptCard = event.target.closest("[data-work-transcript]");
  if (transcriptCard) {
    await showWorkTranscript(transcriptCard.dataset.workTranscript);
    return;
  }
  const trendButton = event.target.closest("[data-trend-metric]");
  if (trendButton) {
    state.trendMetric = trendButton.dataset.trendMetric;
    renderAccount();
    return;
  }
  const accountAction = event.target.closest("[data-account-action]");
  if (accountAction?.dataset.accountAction === "toggle-notes") {
    state.notesOpen = !state.notesOpen;
    renderAccount();
    if (state.notesOpen) {
      await hydrateAnalysis(state.model);
      renderAccount();
    }
    return;
  }
  const tab = event.target.closest("[data-account-tab]");
  if (!tab) return;
  if (!ACCOUNT_TAB_IDS.has(tab.dataset.accountTab)) return;
  state.activeTab = tab.dataset.accountTab;
  renderAccount();
});

document.addEventListener("input", (event) => {
  const input = event.target.closest("[data-works-filter]");
  if (!input || input.dataset.worksFilter !== "query" || !state.model) return;
  state.works.query = input.value;
  renderAccount();
  restoreWorksInput("query");
});

document.addEventListener("change", (event) => {
  const input = event.target.closest("[data-works-filter]");
  if (!input || !state.model) return;
  state.works[input.dataset.worksFilter] = input.value;
  renderAccount();
});

document.addEventListener("keydown", (event) => {
  if (event.key !== "Enter" && event.key !== " ") return;
  const card = event.target.closest?.("[data-work-transcript]");
  if (!card) return;
  event.preventDefault();
  showWorkTranscript(card.dataset.workTranscript);
});

if (elements.closeTranscript) {
  elements.closeTranscript.addEventListener("click", () => elements.transcriptPanel.close());
}
if (elements.transcriptPanel) {
  elements.transcriptPanel.addEventListener("click", (event) => {
    if (event.target === elements.transcriptPanel) elements.transcriptPanel.close();
  });
}

elements.reload.addEventListener("click", async () => {
  const scrollTop = window.scrollY;
  await loadAccount({ force: true });
  window.scrollTo({ top: scrollTop });
});
loadAccount();
