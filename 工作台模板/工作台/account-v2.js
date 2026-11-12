"use strict";

const query = new URLSearchParams(location.search);
const accountKind = query.get("kind") || "";
const accountId = query.get("id") || "";
const ACCOUNT_TAB_IDS = new Set(["charts", "analysis", "pattern", "works"]);

// 一进页面就先点亮当前 tab，不等快照回来再亮 —— 否则切页时侧栏会空一下再跳出来
const OWN_NAV_KEY = accountKind === "own" ? "own-accounts" : "benchmark-accounts-v2";
document.documentElement.dataset.nav = OWN_NAV_KEY;
document.querySelectorAll(".nav-item[data-nav-key]").forEach((item) => {
  item.classList.toggle("is-active", item.dataset.navKey === OWN_NAV_KEY);
});

const state = {
  model: null,
  accounts: [],
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
  workspaceVersion: document.querySelector("#workspace-version"),
  back: document.querySelector("#back-to-previous"),
  breadcrumb: document.querySelector("#account-breadcrumb"),
  toast: document.querySelector("#toast"),
  view: document.querySelector("#account-view"),
};

// 我的账号页与对标账号共用返回条，卡片右上角不再放切换/刷新工具
document.body.classList.toggle("is-own-account", accountKind === "own");

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

/* 面向用户字段里的链接：支持 [标题](链接) 和裸 URL，其余文本照常转义 */
function renderInlineLinks(value) {
  const text = String(value ?? "");
  const pattern = /\[([^\]]+)\]\((https?:\/\/[^\s)]+)\)|(https?:\/\/[^\s<>"）)。，；：、]+)/g;
  let result = "";
  let cursor = 0;
  let match;
  while ((match = pattern.exec(text)) !== null) {
    result += escapeHtml(text.slice(cursor, match.index));
    const label = match[1] || match[3];
    const url = match[2] || match[3];
    result += `<a href="${escapeAttribute(url)}" target="_blank" rel="noopener noreferrer">${escapeHtml(label)}</a>`;
    cursor = match.index + match[0].length;
  }
  return result + escapeHtml(text.slice(cursor));
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
  const returnTo = `account-v2?kind=${encodeURIComponent(accountKind)}&id=${encodeURIComponent(accountId)}&tab=works`;
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
  // 账号抓到多少就展示多少；未采集详情的行由 has_detail 控制置灰不可点。
  // 只有历史工作区缺 account_contents 时，才回落到对标内容库。
  const contents = account.account_contents?.length
    ? account.account_contents
    : (isBenchmark ? snapshot.workflows.insight.benchmark_contents.filter((content) => matchesAccount(content, account)) : []);
  // 公开面：非公开内容不取数、不进作品列表，详情页与抽屉都只看公开内容。
  const publicContents = contents.filter((content) => /公开|public/i.test(String(content.visibility || "")));
  // 正式文档已归一到无后缀文件名，所有账号只读这一份新版数据。
  const documentSet = account.documents || {};
  return {
    kind: accountKind,
    label: isBenchmark ? "对标账号档案" : "我方账号档案",
    content_count: contents.length,
    account,
    contents,
    works_contents: isBenchmark ? contents : publicContents,
    // 行动建议里的参考做法要落到站内那条作品卡，对标内容库单独带一份索引
    benchmark_contents: Array.isArray(snapshot.workflows?.insight?.benchmark_contents)
      ? snapshot.workflows.insight.benchmark_contents
      : [],
    creatorCenter: account.creator_center || null,
    analysis: {
      overview: account.documents?.overview || null,
      detail: documentSet.detail || null,
      prior: account.documents?.prior || null,
      pattern: documentSet.pattern || null,
      advice: documentSet.advice || null,
      // 行动建议要拿我方规划做对照，这份是工作区级的正式文档
      plan: snapshot.workflows?.self?.media_plan || null,
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

/* 发布时段配色（照稿子 TIME_GROUPS，四段用可区分的蓝 / 橙 / 绿） */
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

/* 作品卡：与列表页同一套 .works-grid / .work-card 口径（左图右文、封面 3:4） */
function accountWorkCard(item, index, model) {
  const isBenchmark = model.kind === "benchmark";
  const canOpen = Boolean(item.has_detail);
  const targetId = isBenchmark ? (item.key || item.id) : item.id;
  const cover = item.cover_path
    ? `<img src="${rawUrl(item.cover_path)}" alt="" loading="lazy">`
    : `<span class="work-fallback">${platformIcon(platformKey(item.platform))}</span>`;
  const visibility = (!isBenchmark && item.visibility)
    ? `<span class="status-pill tone-gray">${escapeHtml(item.visibility)}</span>`
    : "";
  const inner = `<span class="work-cover">${cover}</span>
    <div class="work-body">
      <div class="work-head">${rankPill(index)}${mediaPill(item.media_format)}${visibility}<span class="work-date">${escapeHtml(shortDate(item.published_at))}</span></div>
      <div class="work-title">${escapeHtml(item.title || "—")}</div>
      <div class="work-author">${platformPill(item.platform)}<span class="work-author-name">${escapeHtml(item.account_name || "—")}</span></div>
      <div class="work-foot">${contentEngagement(item.metrics)}${canOpen ? '<span class="work-link">查看详情 →</span>' : '<span class="work-link is-muted">未采集详情</span>'}</div>
    </div>`;
  if (canOpen) {
    // 带了 data-open-content 就在右侧开内容抽屉（content-drawer.js 接管），href 留着兜底
    return `<a class="work-card is-link" href="${contentUrl(model.kind, targetId)}" title="打开详情" data-open-content="${escapeAttribute(model.kind)}" data-content-id="${escapeAttribute(targetId)}">${inner}</a>`;
  }
  return `<div class="work-card">${inner}</div>`;
}

function renderTrend(contents, metric, options = {}) {
  const compact = Boolean(options.compact);
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
  const width = compact ? 420 : 920;
  const height = compact ? 210 : 260;
  const padding = compact
    ? { top: 18, right: 14, bottom: 32, left: 42 }
    : { top: 22, right: 28, bottom: 40, left: 54 };
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
  const labelTarget = compact ? 2 : 4;
  const labelStep = Math.max(1, Math.ceil((coordinates.length - 1) / labelTarget));
  const labels = coordinates.filter((_, index) => index === 0 || index === coordinates.length - 1 || index % labelStep === 0);
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

function renderPublishingRhythm(contents, options = {}) {
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
  const legend = TIME_GROUPS.map(([name, color]) => {
    const label = options.compact ? name.split(/\s+/)[0] : name;
    return `<span class="legend-item" title="${escapeAttribute(name)}"><i data-dot="${color}"></i>${escapeHtml(label)}</span>`;
  }).join("");
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

/* 互动趋势的评价：前半段 vs 后半段。
   方向以中位数为准（这个号的互动是头部极重的分布，均值会被一两条爆款带跑），
   均值单独报一句；两边方向相反时明确说清楚是被爆款拉偏的。 */
function trendEvaluation(contents) {
  const points = contents
    .map((item) => ({ item, ts: dateTimestamp(item.published_at), value: engagementTotal(item) }))
    .filter((point) => point.ts !== null)
    .sort((left, right) => left.ts - right.ts);
  if (points.length < 4) return "";

  const half = Math.floor(points.length / 2);
  const early = points.slice(0, half).map((point) => point.value);
  const late = points.slice(-half).map((point) => point.value);
  const mean = (list) => Math.round(list.reduce((sum, value) => sum + value, 0) / list.length);
  const median = (list) => {
    const sorted = [...list].sort((left, right) => left - right);
    const mid = Math.floor(sorted.length / 2);
    return sorted.length % 2 ? sorted[mid] : Math.round((sorted[mid - 1] + sorted[mid]) / 2);
  };
  const pct = (from, to) => (from > 0 ? Math.round(((to - from) / from) * 100) : null);
  const signed = (value) => (value === null ? "—" : `${value > 0 ? "+" : ""}${value}%`);

  const earlyMed = median(early);
  const lateMed = median(late);
  const medChange = pct(earlyMed, lateMed);
  const meanChange = pct(mean(early), mean(late));

  const direction = medChange === null
    ? "前后两段无法比较"
    : medChange > 15 ? `后半段比前半段高 ${medChange}%，热度在往上走`
    : medChange < -15 ? `后半段比前半段低 ${Math.abs(medChange)}%，热度在往下走`
    : `前后两段差 ${signed(medChange)}，基本持平`;

  const meanClash = medChange !== null && meanChange !== null
    && Math.sign(medChange) !== Math.sign(meanChange) && Math.abs(medChange) > 10 && Math.abs(meanChange) > 10;

  const peak = points.reduce((best, point) => (point.value > best.value ? point : best), points[0]);
  const allMedian = median(points.map((point) => point.value));
  const allMean = mean(points.map((point) => point.value));

  return `<div class="v2-trend-eval">
    <p class="v2-trend-lead"><b>趋势判断</b>${escapeHtml(direction)}（按中位数看，避开爆款干扰）</p>
    <p class="v2-trend-sub">前半段 ${early.length} 条：中位 ${formatNumber(earlyMed)}、均值 ${formatNumber(mean(early))} ｜ 后半段 ${late.length} 条：中位 ${formatNumber(lateMed)}、均值 ${formatNumber(mean(late))}</p>
    ${meanClash ? `<p class="v2-trend-warn">注意：按均值算是 ${signed(meanChange)}、按中位数算是 ${signed(medChange)}，方向相反——均值被少数几条爆款带偏，账号常态要看中位数。</p>` : ""}
    <ul class="v2-trend-facts">
      <li>最高一条：${escapeHtml(shortDate(new Date(peak.ts)))}《${escapeHtml(shortQuote(peak.item.title || "未命名", 22))}》${formatNumber(peak.value)}</li>
      <li>全账号中位数 ${formatNumber(allMedian)}，均值 ${formatNumber(allMean)}${allMean > allMedian * 1.5 ? "（头部极重，均值不代表单条水平）" : ""}</li>
      <li>口径：点赞＋收藏＋评论，不含分享</li>
    </ul>
  </div>`;
}

/* 发布规律结论：要么没有规律，要么集中在某个时段 */
function rhythmEvaluation(contents) {
  const hours = contents
    .map((content) => dateTimestamp(content.published_at))
    .filter((timestamp) => timestamp !== null)
    .map((timestamp) => new Date(timestamp).getHours());
  if (hours.length < 5) return "";

  const counts = new Array(24).fill(0);
  for (const hour of hours) counts[hour] += 1;

  let bestStart = 0;
  let bestSum = -1;
  for (let start = 0; start < 24; start += 1) {
    const sum = counts[start] + counts[(start + 1) % 24] + counts[(start + 2) % 24];
    if (sum > bestSum) {
      bestSum = sum;
      bestStart = start;
    }
  }
  const share = Math.round((bestSum / hours.length) * 100);
  const pad = (value) => String(value).padStart(2, "0");
  const range = `${pad(bestStart)}–${pad((bestStart + 3) % 24)} 点`;
  const peak = counts.indexOf(Math.max(...counts));

  // 一天 24 小时均匀分布时，任意 3 小时只占 12.5%，所以按这个基线判断「算不算集中」
  const lead = share >= 45
    ? `有规律：集中在 ${range}，这 3 小时发了 ${bestSum} 条，占 ${share}%`
    : share >= 25
      ? `规律一般：相对集中在 ${range}，这 3 小时 ${bestSum} 条，占 ${share}%，其余散在全天`
      : `没有明显规律：最密的 3 小时（${range}）也只有 ${bestSum} 条、占 ${share}%，接近全天均匀`;

  return `${lead}。单点最多的是 ${pad(peak)}:00（${counts[peak]} 条）。`;
}

/* 互动分布：v2 版横向条形（对数刻度，和原图同一套档位口径） */
function renderDistributionV2(contents) {
  const samples = contents
    .map((content) => ({ content, value: distributionInteraction(content) }))
    .filter((item) => item.value !== null)
    .sort((left, right) => right.value - left.value);
  if (!samples.length) return v2Missing("这批内容还没有可用的互动数据（点赞、收藏、评论三项齐全才算）。");

  const values = samples.map((item) => item.value).sort((left, right) => left - right);
  const scale = distributionScale(values);
  const max = Math.max(...values, 1);
  const position = (value) => Math.min(100, Math.max(2, (Math.log10(Math.max(value, 1)) / Math.log10(max)) * 100));

  const counts = { high: 0, middle: 0, low: 0, insufficient: 0 };
  samples.forEach((item) => { counts[distributionTier(scale, item.value)] += 1; });

  const legend = DISTRIBUTION_TIERS
    .map(([id, label]) => `<span class="v2-dist-tier is-${id}">${label} ${counts[id] || 0}</span>`)
    .join("");
  const marks = scale
    ? `<span class="v2-dist-mark">低档线 ${formatNumber(scale.lowCeiling)}</span><span class="v2-dist-mark">中位数 ${formatNumber(scale.median)}</span><span class="v2-dist-mark is-high">高档线 ${formatNumber(scale.highFloor)}</span>`
    : "";

  const rows = samples.map((item) => {
    const tier = distributionTier(scale, item.value);
    return `<li>
      <span class="v2-dist-name" title="${escapeAttribute(item.content.title || "")}">${escapeHtml(shortQuote(item.content.title || "未命名", 26))}</span>
      <span class="v2-dist-track"><i class="is-${tier}" data-w="${position(item.value).toFixed(1)}"></i></span>
      <b>${formatNumber(item.value)}</b>
    </li>`;
  }).join("");

  return `<div class="v2-dist-chart">
    <div class="v2-dist-head">${legend}${marks ? `<span class="v2-dist-marks">${marks}</span>` : ""}<span class="v2-dist-scale">对数刻度 · 每条一行</span></div>
    <ul class="v2-dist-rows">${rows}</ul>
  </div>`;
}

function distributionEvaluation(contents) {
  const samples = contents
    .map((content) => distributionInteraction(content))
    .filter((value) => value !== null)
    .sort((left, right) => left - right);
  if (samples.length < 4) return "";

  const scale = distributionScale(samples);
  const total = samples.reduce((sum, value) => sum + value, 0) || 1;
  const top3 = [...samples].reverse().slice(0, 3).reduce((sum, value) => sum + value, 0);
  const median = samples[(samples.length - 1) >> 1];
  const min = samples[0];
  const max = samples[samples.length - 1];
  const ratio = min > 0 ? Math.round(max / min) : 0;
  const mean = Math.round(total / samples.length);
  const share = Math.round((top3 / total) * 100);
  if (!scale) {
    return `${samples.length} 条有完整互动数据，不足 ${DISTRIBUTION_MIN_SAMPLES} 条暂不分档；最高 ${formatNumber(max)}、最低 ${formatNumber(min)}，相差 ${ratio} 倍。`;
  }

  const counts = { high: 0, middle: 0, low: 0, insufficient: 0 };
  samples.forEach((value) => { counts[distributionTier(scale, value)] += 1; });

  return `${samples.length} 条里高档 ${counts.high}、中档 ${counts.middle}、低档 ${counts.low}；中位数 ${formatNumber(median)}，最高 ${formatNumber(max)}、最低 ${formatNumber(min)}，相差 ${ratio} 倍。前 3 条就占了全部互动的 ${share}%，均值 ${formatNumber(mean)} 被这少数几条拉高——判断这个号看中位数和档位分布，不看均值。`;
}

/* 图表解读：固定按「互动趋势：」「发布规律：」「互动分布：」三行展示 */
const CHART_NOTE_LABELS = ["互动趋势", "发布规律", "互动分布"];

function chartNoteLines(value) {
  const raw = String(value || "").replace(/[*_`]/g, "").trim();
  if (!raw) return [];
  const marker = new RegExp(`(?=(?:${CHART_NOTE_LABELS.join("|")})：)`);
  return raw
    .split(/\r?\n/)
    .flatMap((line) => line.trim().replace(/^[-*•]\s*/, "").split(marker))
    .map((line) => line.trim().replace(/[；;]\s*$/, ""))
    .filter(Boolean);
}

function chartNoteMap(value) {
  const notes = {};
  chartNoteLines(value).forEach((line) => {
    const hit = /^(互动趋势|发布规律|互动分布)[：:]\s*(.+)$/.exec(line);
    if (hit) notes[hit[1]] = hit[2].trim();
  });
  return notes;
}

function perfTag(kind, label) {
  const icons = {
    trend: '<svg viewBox="0 0 24 24"><path d="m4 16 5-5 3 3 7-8"/><path d="M14 6h4v4"/></svg>',
    rhythm: '<svg viewBox="0 0 24 24"><circle cx="12" cy="12" r="8"/><path d="M12 8v4l3 2"/></svg>',
    dist: '<svg viewBox="0 0 24 24"><path d="M4 7h9M4 12h16M4 17h6"/></svg>',
  };
  return `<span class="perf-tag is-${kind}"><span class="perf-tag-ico" aria-hidden="true">${icons[kind]}</span><b>${escapeHtml(label)}</b></span>`;
}

function chartNoteFallback(contents) {
  return [
    ["互动趋势", trendBrief(contents)],
    ["发布规律", rhythmEvaluation(contents)],
    ["互动分布", distributionEvaluation(contents)],
  ].filter(([, text]) => text).map(([label, text]) => `${label}：${text}`).join("\n");
}

/* 互动趋势：放在完整分析最下面，带指标切换 */
function renderTrendBlock(model) {
  const contents = model.contents;
  const selected = TREND_METRICS.find((item) => item.id === state.trendMetric) || TREND_METRICS[0];
  const seg = `<div class="trend-segmented" role="group" aria-label="互动趋势指标">${TREND_METRICS.map((item) => `<button class="trend-segment ${item.id === selected.id ? "is-active" : ""}" type="button" data-trend-metric="${item.id}">${item.label}</button>`).join("")}</div>`;
  return `<section class="v2-sub">
    <header class="v2-trend-head"><h3>互动趋势</h3>${seg}</header>
    <div class="trend-body">${renderTrend(contents, selected.id)}</div>
    ${trendEvaluation(contents)}
  </section>`;
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
  void seg;
  // 发布规律和互动分布已经各自独立成节（发布规律在本抽屉最后、互动分布在内容拆解的二级页），
  // 这里只留数据概览，避免同一个抽屉里出现两张一样的图
  return `<section class="account-section-card">${sectionHead("chart", "数据概览", "按已录入互动数据的内容计算", `<span class="section-head-action">${formatNumber(contents.length)} 条采集作品</span>`)}
    <div class="analysis-kpis">${kpis.map((item) => `<div class="kpi"><span class="kpi-icon" data-tone="${item.tone}" data-tint="${item.tint}">${KPI_ICON[item.mark]}</span><div class="kpi-copy"><span>${escapeHtml(item.label)}</span><strong>${item.value === null ? "—" : formatNumber(item.value)}</strong></div></div>`).join("")}</div>
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
  // 抽屉里不再放筛选条：卡片和首页「代表作品」用同一个组件、同一套样式
  const list = contentList.length
    ? `<div class="v2-wcards is-drawer">${contentList.map((content, index) => miniWorkCard(content, index + 1, model)).join("")}</div>`
    : '<div class="account-empty-inline">还没有采集到作品。</div>';
  return `<section class="account-section-card is-plain-works">${list}</section>`;
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
  chart: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M5 19V11M12 19V6M19 19v-5"/></svg>',
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
  /* 高适配 / 值得研究两枚标签按他的要求去掉了 */
  const tags = (account.tags || []).map((tag) => `<span class="account-status-tag tone-neutral">${escapeHtml(tag)}</span>`).join("");
  // 只留打开主页（黑色药丸），挂在头像下方；备注入口去掉
  const homeLink = safeExternalLink(account.homepage, "打开主页");
  /* 代表作品：取公开面，未公开内容不进页面 */
  const publicWorks = model.works_contents || model.contents;
  const miniWorks = representativeWorks(model);
  const worksBlock = miniWorks.length
    ? `<div class="account-hero-works">
        <div class="account-hero-works-head">
          <h3>代表作品</h3>
          <span class="account-hero-works-count">共 ${publicWorks.length} 条</span>
          <button class="v2-more" type="button" data-account-action="open-works">查看所有作品<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M5 12h13M12.5 6.5 18.5 12l-6 5.5"/></svg></button>
        </div>
        <div class="v2-wcards is-two">${miniWorks.map((item, index) => miniWorkCard(item, index + 1, model)).join("")}</div>
      </div>`
    : "";
  /* 更新时间：对标账号取 recorded_at；我的账号没有这个字段，
     退到创作者中心采集时间，再退到诊断文件更新时间，和之前的取值口径一致 */
  const updatedRaw = account.recorded_at
    || (kind === "own" ? ownCreatorData(model)?.capturedAt || model.analysis.detail?.modified_at : "");
  const updatedAt = updatedRaw
    ? `<span class="account-hero-stamp"><svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="12" cy="12" r="8.6"/><path d="M12 7.5V12l3.1 1.9"/></svg><em>更新时间</em>${escapeHtml(formatPublishedAt(updatedRaw))}</span>`
    : "";
  /* 单片平均互动：口径＝点赞＋收藏＋评论（engagementTotal 里的 interactions 就是这个和），
     和内容规律分析分三档用的「互动数」同源，不加分享 */
  const perWorkEngagement = contents.length
    ? Math.round(contents.reduce((sum, item) => sum + engagementTotal(item), 0) / contents.length)
    : 0;
  const stats = [
    ["followers", "users", "粉丝数", formatNumber(account.followers)],
    ["likes", "heart", "获赞数", formatNumber(account.likes)],
    ["collect", "chart", "单篇平均互动", formatNumber(perWorkEngagement)],
    ["analyzed", "check", secondaryMetric[0], formatNumber(secondaryMetric[1])],
  ].map(([mark, icon, label, value]) => `<div class="account-hero-stat stat-${mark}"><span class="account-hero-stat-icon">${HERO_ICON[icon]}</span><div class="account-hero-stat-copy"><span>${escapeHtml(label)}</span><strong>${escapeHtml(value)}</strong></div></div>`).join("");
  return `<section class="account-hero">
    <div class="account-hero-glow"></div>
    <svg class="account-hero-rings" viewBox="0 0 460 460" aria-hidden="true"><circle cx="230" cy="230" r="74"/><circle cx="230" cy="230" r="130"/><circle cx="230" cy="230" r="186"/><circle cx="230" cy="230" r="230"/></svg>
    <div class="account-hero-id">
      <div class="account-hero-avatar-wrap">
        ${accountAvatar(account, "account-hero-avatar")}
        ${homeLink}
      </div>
      <div class="account-hero-copy">
        <p class="account-hero-eyebrow">${kind === "benchmark" ? "BENCHMARK ACCOUNT" : "OWN ACCOUNT"}</p>
        <h1>${escapeHtml(account.name)}</h1>
        <p class="account-hero-intro">${escapeHtml(account.intro || "当前账号尚未填写简介。").replaceAll("\n", "<br>")}</p>
        <div class="account-hero-tags">${platformTag(account.platform)}${genderTag(account.gender)}${tags}</div>
      </div>
    </div>
    <div class="account-hero-side">
      ${updatedAt}
      <div class="account-hero-stats">${stats}</div>
    </div>
    ${worksBlock}
  </section>`;
}

/* 左侧栏导航高亮（详情页共用） */
function markNavActive(key) {
  document.documentElement.dataset.nav = key;
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

/* ================================================================
   v2 布局：四个模块，结论在首页，长文进二级
   1 账号概览 / 2 账号洞察 / 3 内容拆解 / 4 行动建议
   内容全部来自现有正式文档（详细分析、内容规律分析），不新造结论；
   解析不到的地方显示「待补」，不编。
   ================================================================ */

function splitMarkdownSections(md) {
  const blocks = [];
  let current = { level: 0, title: "", lines: [] };
  for (const line of String(md || "").split("\n")) {
    const hit = /^(#{1,3})\s+(.+?)\s*$/.exec(line.trim());
    if (hit) {
      blocks.push(current);
      current = { level: hit[1].length, title: hit[2].trim(), lines: [] };
    } else {
      current.lines.push(line);
    }
  }
  blocks.push(current);
  return blocks;
}

function sectionByTitle(blocks, title) {
  return blocks.find((block) => block.title === title)
    || blocks.find((block) => block.title && block.title.includes(title))
    || null;
}

function sectionText(block) {
  return block ? block.lines.join("\n").trim() : "";
}

function markdownKeyValues(text) {
  const out = {};
  for (const line of String(text || "").split("\n")) {
    const hit = /^\s*[-*]\s*`([^`]+)`\s*[：:]\s*(.+)$/.exec(line);
    if (hit) out[hit[1].trim()] = hit[2].trim();
  }
  return out;
}

function firstPlainParagraph(text) {
  for (const chunk of String(text || "").split(/\n{2,}/)) {
    const value = chunk.trim();
    if (!value) continue;
    if (/^([-*|`#>]|\d+\.)/.test(value)) continue;
    return value.replace(/\s*\n\s*/g, "");
  }
  return "";
}

function firstBulletText(text) {
  for (const line of String(text || "").split("\n")) {
    const value = line.trim();
    if (!value || !/^[-*]/.test(value)) continue;
    return value.replace(/^[-*]\s*/, "").replace(/`/g, "").trim();
  }
  return "";
}

function markdownTableRows(text) {
  const lines = String(text || "").split("\n").filter((line) => line.trim().startsWith("|"));
  if (lines.length < 3) return [];
  const cells = (line) => line.trim().replace(/^\|/, "").replace(/\|$/, "").split("|").map((cell) => cell.trim());
  return lines.slice(2).map(cells).filter((row) => row.some(Boolean));
}

function tierCounts(text) {
  const hit = /高档\s*(\d+)\s*[、,，]\s*中档\s*(\d+)\s*[、,，]\s*低档\s*(\d+)/.exec(String(text || ""));
  if (!hit) return null;
  return { high: Number(hit[1]), mid: Number(hit[2]), low: Number(hit[3]) };
}

function shortQuote(text, limit = 90) {
  const value = String(text || "").trim();
  if (value.length <= limit) return value;
  return `${value.slice(0, limit)}…`;
}

/* 模块头图标：统一 24 网格 + 描边（fill / stroke-width 在 CSS 里给，和侧栏导航同口径） */
const V2_HEAD_ICONS = {
  // 账号洞察：看清这个人，不是「查看」，所以用眼睛而不是人像（人像已被侧栏/面包屑占用）
  insight: `<path d="M2.5 12S6 6.2 12 6.2 21.5 12 21.5 12 18 17.8 12 17.8 2.5 12 2.5 12z"/><circle cx="12" cy="12" r="2.8"/>`,
  // 内容拆解：一层层拆开看结构
  breakdown: `<path d="m12 2.8 8.6 4.3-8.6 4.3-8.6-4.3z"/><path d="m3.4 11.4 8.6 4.3 8.6-4.3"/><path d="m3.4 15.9 8.6 4.3 8.6-4.3"/>`,
  // 行动建议：瞄准下一步要验证的东西（不用箭头，避免看着像可点的按钮）
  action: `<circle cx="12" cy="12" r="8.4"/><circle cx="12" cy="12" r="4"/><circle cx="12" cy="12" r="1"/>`,
  // 账号概览：横向柱状＝后台数据摘要
  metrics: `<path d="M4 20V5M4 20h16"/><path d="M8.5 20v-5.2M13 20v-9.4M17.5 20v-3.4"/>`,
};

function v2Head(icon, title, sub, action = "") {
  const glyph = V2_HEAD_ICONS[icon] || V2_HEAD_ICONS.insight;
  return `<header class="v2-head"><span class="v2-icon" aria-hidden="true"><svg viewBox="0 0 24 24">${glyph}</svg></span><div class="v2-head-copy"><h2>${escapeHtml(title)}</h2><p>${escapeHtml(sub)}</p></div>${action ? `<div class="v2-head-action">${action}</div>` : ""}</header>`;
}

function v2Loading(label) {
  return `<div class="v2-empty"><p>${escapeHtml(label)}</p></div>`;
}

function v2Missing(label) {
  return `<div class="v2-empty"><p>${escapeHtml(label)}</p></div>`;
}

/* ---------- 01 账号概览 ---------- */

/* 内容拆解的二级页：和详情页同一套口径，只是把整份规律报告摊开看。
   顺序＝总结 / 按环节看 / 靠什么起作用 / 账号通常使用 / 内容类别 / 三档对比 / 分档规则。 */
function verdictShort(text) {
  const value = String(text || "").trim();
  if (/数据好/.test(value)) return "数据好";
  if (/数据差/.test(value)) return "数据差";
  if (/差不多|区分不出/.test(value)) return "区分不出";
  return value || "—";
}

function learnReportParts(model) {
  const pattern = String(model.analysisContent.pattern || "");
  const pBlocks = pattern ? splitMarkdownSections(pattern) : [];
  const total = scopeNumber(sectionText(sectionByTitle(pBlocks, "分析范围")), "纳入内容数") || model.contents.length;
  const ruleCount = scopeNumber(sectionText(sectionByTitle(pBlocks, "分析范围")), "规律数量");
  const stages = loopStages(pBlocks);
  const category = stages.find((row) => row.stage === "内容类别") || null;
  const mechs = loopMechanisms(pBlocks);
  const facts = tierFacts(pBlocks);
  const usual = usualRows(pBlocks);
  const tierList = normalizeTierRows(pBlocks);
  // 小结正文里那对「」就是一句话打法；文档写得再长，这一块也只放这一句
  const summary = firstPlainParagraph(sectionText(sectionByTitle(pBlocks, "小结")));
  const quote = /「([^」]+)」/.exec(summary);
  const playbook = quote ? quote[1] : summary.replace(/^概括来说[，,]\s*/, "");

  const sub = [
    model.account.name || "",
    `${total} 条内容`,
    ruleCount ? `${ruleCount} 条规律` : "",
  ].filter(Boolean).join(" · ");

  const summaryBlock = playbook ? `<section class="v2-sub is-summary">
      <h3>总结</h3>
      <p class="learn3-sum">这个账号的打法是「<em>${escapeHtml(playbook)}</em>」</p>
    </section>` : "";

  // 抽屉是完整版：六个环节的做法一条不省，详情页那边只摘前三条
  const stageBlock = stages.length ? `<section class="v2-sub">
      <h3>按环节看：他每一步用什么形式</h3>
      <div class="learn3-stages">
        ${stages.map((row) => `<div class="learn3-stage">
          <header><b>${escapeHtml(row.stage)}</b><span>${escapeHtml(row.habit)}</span></header>
          <ul class="learn3-dist">${row.dist.map((item, index) => `<li${index === 0 ? " is-top" : ""}>
            <span class="n">${escapeHtml(item.label)}</span>
            <span class="track"><i data-w="${item.percent}"></i></span>
            <b>${item.count}</b><em>${item.percent}%</em>
          </li>`).join("")}</ul>
        </div>`).join("")}
      </div>
    </section>` : "";

  // 先讲这一层为什么起作用，再讲他具体怎么做的
  const mechBlock = mechs.length ? `<section class="v2-sub">
      <h3>靠什么起作用</h3>
      <div class="learn3-mech">
        ${mechs.map((row) => `<article class="learn3-mech-card">
          <header><h4>${escapeHtml(row.name)}</h4><span>${escapeHtml(row.definition)}</span></header>
          ${row.reading ? `<p class="learn3-mech-read"><i>为什么起作用</i>${escapeHtml(row.reading)}</p>` : ""}
          <p class="learn3-mech-fact"><i>他具体怎么做</i>${escapeHtml(row.facts)}</p>
        </article>`).join("")}
      </div>
    </section>` : "";

  const usualBlock = usual.length ? `<section class="v2-sub">
      <h3>账号通常使用</h3>
      <p class="learn3-lead">覆盖八成以上内容的做法，只说明这是他的习惯，不判断好坏。</p>
      <div class="learn3-table is-usually">
        <div class="learn3-tr is-head"><span>做法</span><span>覆盖</span></div>
        ${usual.map((row) => `<div class="learn3-tr">
          <span class="learn3-td-name"><b>${escapeHtml(row.tag)}</b><i>${escapeHtml(row.type)}</i><p>${escapeHtml(row.definition)}</p></span>
          <b>${row.all.count} 条 · ${row.all.percent}%</b>
        </div>`).join("")}
      </div>
    </section>` : "";

  // 内容类别：用表格，不重复详情页那张排行条
  const definitionOf = (tag) => (tierList.find((row) => row.tag === tag) || {}).definition || "";
  const categoryBlock = category && category.dist.length ? `<section class="v2-sub">
      <h3>内容类别</h3>
      <p class="learn3-lead">${total} 条内容分别是什么类型</p>
      <div class="learn3-table is-category">
        <div class="learn3-tr is-head"><span>内容类别</span><span>条数</span><span>占比</span></div>
        ${category.dist.map((item) => `<div class="learn3-tr">
          <span class="learn3-td-name"><b>${escapeHtml(item.label)}</b>${definitionOf(item.label) ? `<p>${escapeHtml(definitionOf(item.label))}</p>` : ""}</span>
          <b>${item.count}</b><b>${item.percent}%</b>
        </div>`).join("")}
      </div>
    </section>` : "";

  const tierTable = (rows) => `<div class="learn3-table">
      <div class="learn3-tr is-head"><span>做法</span><span>高档</span><span>中档</span><span>低档</span><span>结论</span></div>
      ${rows.map((row) => `<div class="learn3-tr">
        <span class="learn3-td-name"><b>${escapeHtml(row.tag)}</b><i>${escapeHtml(row.type)}</i><p>${escapeHtml(row.definition)}</p></span>
        <b>${row.high.percent}%</b><b>${row.mid.percent}%</b><b>${row.low.percent}%</b>
        <span class="learn3-chip is-${verdictGroup(row.verdict)}">${escapeHtml(verdictShort(row.verdict))}</span>
      </div>`).join("")}
    </div>`;

  const tierBlocks = TIER_GROUPS.map((group) => {
    const rows = tierList.filter((row) => verdictGroup(row.verdict) === group.key);
    const tip = tierTip(pBlocks, group.sections);
    // 只有第一组带整段口径，避免三张表都重复一遍「怎么分的档」
    const lead = group.key === "good" && facts.rules.length
      ? `<p class="learn3-lead">${escapeHtml(facts.rules[0])}</p>${facts.weak ? `<p class="v2-note">这轮样本不足，还没法分高低档。</p>` : ""}`
      : "";
    if (!rows.length) {
      // 「数据好／数据差的内容更常用」是报告的固定两组，本轮没有做法时保留标题和一句说明；
      // 其余分组为空时只留标题：空表头会看起来像表格坏了。
      return group.key === "good" || group.key === "bad"
        ? `<section class="v2-sub">
            <h3>${group.title}</h3>
            <p class="learn3-tip">本轮没有判定为这一档的做法。</p>
          </section>`
        : "";
    }
    return `<section class="v2-sub">
        <h3>${group.title}</h3>
        ${lead}
        ${tierTable(rows)}
        ${tip ? `<p class="learn3-tip">${escapeHtml(tip)}</p>` : ""}
      </section>`;
  }).join("");

  const rulesSection = facts.rules.length > 1 ? `<section class="v2-sub">
      <h3>分档和读表规则</h3>
      ${facts.rules.slice(1).map((line) => `<p class="learn3-lead">${escapeHtml(line)}</p>`).join("")}
    </section>` : "";

  // data 给「我的账号」内联排版用：那边不用现成的块，自己重排
  return {
    sub, summaryBlock, stageBlock, mechBlock, usualBlock, categoryBlock, tierBlocks, rulesSection,
    data: { total, ruleCount, playbook, stages, mechs, usual, tierList, facts },
  };
}

function learnDrawer(model) {
  const parts = learnReportParts(model);
  return `<div class="v2-drawer" id="v2-learn-drawer" aria-hidden="true">
    <div class="v2-drawer-mask" data-account-action="close-learn"></div>
    <aside class="v2-drawer-panel" role="dialog" aria-modal="true" aria-label="内容拆解完整分析">
      <header class="v2-drawer-head">
        <div>
          <p class="v2-drawer-kicker">内容拆解</p>
          <h2>完整分析</h2>
          <p class="v2-drawer-sub">${escapeHtml(parts.sub)}</p>
        </div>
        <button class="v2-drawer-close" type="button" data-account-action="close-learn" aria-label="关闭">
          <svg viewBox="0 0 24 24" aria-hidden="true"><path d="m6 6 12 12M18 6 6 18"/></svg>
        </button>
      </header>
      <div class="v2-drawer-body"><div class="v2-drawer-sections">
        ${parts.summaryBlock}
        ${parts.stageBlock}
        ${parts.mechBlock}
        ${parts.usualBlock}
        ${parts.categoryBlock}
        ${parts.tierBlocks}
        ${parts.rulesSection}
      </div></div>
    </aside>
  </div>`;
}



/* 采集作品卡：封面在左（左上序号、右上视频/图文），右边标题、平台+作者、数据。
   对标与我方统一：点开都在抽屉里读文案，不再跳内容档案页。 */
function miniWorkCard(item, index, model) {
  const cover = item.cover_path
    ? `<img src="${rawUrl(item.cover_path)}" alt="" loading="lazy">`
    : `<span class="work-fallback">${platformIcon(platformKey(item.platform))}</span>`;
  const media = /图文|文章|笔记/.test(String(item.media_format || "")) ? "图文" : "视频";
  const rank = index === null || index === undefined
    ? ""
    : `<b class="v2-wcard-rank">${String(index).padStart(2, "0")}</b>`;
  const inner = `<span class="v2-wcard-cover">${cover}${rank}<span class="v2-wcard-media">${media}</span></span>
    <div class="v2-wcard-body">
      <p class="v2-wcard-title">${escapeHtml(item.title || "—")}</p>
      <div class="v2-wcard-meta">
        <div class="v2-wcard-author">${platformPill(item.platform)}<span>${escapeHtml(item.account_name || "—")}</span></div>
      </div>
      <div class="v2-wcard-stats">${contentEngagement(item.metrics)}</div>
    </div>`;
  // 没采到文案的也保留入口：点开抽屉说明「还没有采集」，避免死点。
  return `<div class="v2-wcard is-clickable" role="link" tabindex="0" title="查看文案" data-account-action="open-transcript" data-transcript-key="${escapeAttribute(item.key || item.id)}">${inner}</div>`;
}

/* 采集作品抽屉：点顶部那个箭头看全部作品与数据 */
function worksDrawer(model) {
  return `<div class="v2-drawer" id="v2-works" aria-hidden="true">
    <div class="v2-drawer-mask" data-account-action="close-works"></div>
    <aside class="v2-drawer-panel" role="dialog" aria-modal="true" aria-label="采集作品">
      <header class="v2-drawer-head">
        <div>
          <p class="v2-drawer-kicker">账号档案</p>
          <h2>采集作品</h2>
          <p class="v2-drawer-sub">${escapeHtml(model.account.name || "")} · 共 ${model.contents.length} 条</p>
        </div>
        <button class="v2-drawer-close" type="button" data-account-action="close-works" aria-label="关闭采集作品">
          <svg viewBox="0 0 24 24" aria-hidden="true"><path d="m6 6 12 12M18 6 6 18"/></svg>
        </button>
      </header>
      <div class="v2-drawer-body"><div class="v2-drawer-sections">${renderWorks(model)}</div></div>
    </aside>
  </div>`;
}

/* 视频文案抽屉：账号采集到的作品只展示原文，不跳内容档案页 */
function transcriptDrawer() {
  return `<div class="v2-drawer" id="v2-transcript" aria-hidden="true">
    <div class="v2-drawer-mask" data-account-action="close-transcript"></div>
    <aside class="v2-drawer-panel" role="dialog" aria-modal="true" aria-label="视频文案">
      <header class="v2-drawer-head">
        <div>
          <p class="v2-drawer-kicker">视频文案</p>
          <h2 id="v2-transcript-title">视频文案</h2>
          <p class="v2-drawer-sub" id="v2-transcript-sub"></p>
        </div>
        <button class="v2-drawer-close" type="button" data-account-action="close-transcript" aria-label="关闭视频文案">
          <svg viewBox="0 0 24 24" aria-hidden="true"><path d="m6 6 12 12M18 6 6 18"/></svg>
        </button>
      </header>
      <div class="v2-drawer-body"><div class="v2-drawer-sections" id="v2-transcript-body"></div></div>
    </aside>
  </div>`;
}

function renderV2Overview(model) {
  return `<section class="v2-block is-plain" id="v2-overview">
    ${renderSummary(model)}
    ${worksDrawer(model)}
    ${transcriptDrawer()}
  </section>`;
}

/* ---------- 02 账号洞察 ---------- */

/* 详细分析的层级是「层＝###、节＝####」，比通用切分多一层；
   这里单独切一份，不改 splitMarkdownSections 的既有行为 */
function splitDetailSections(md) {
  const blocks = [];
  let current = { level: 0, title: "", lines: [] };
  for (const line of String(md || "").split("\n")) {
    const hit = /^(#{1,4})\s+(.+?)\s*$/.exec(line.trim());
    if (hit) {
      blocks.push(current);
      current = { level: hit[1].length, title: hit[2].trim(), lines: [] };
    } else {
      current.lines.push(line);
    }
  }
  blocks.push(current);
  return blocks;
}

/* 适配结论里的档位词：文档写「清晰。身份、场景…」，只留前面那个档位词 */
function v2FacetLevel(label, value) {
  const text = String(value || "").trim();
  const head = text.split(/[。，,；;（(]/)[0].trim();
  if (!head) return text;
  const stripped = label === "支撑度"
    ? head.replace(/^支撑/, "").trim()
    : head.replace(/^清晰(?!度)/, "").trim();
  return stripped || head;
}

/* 卡片头：图标 + 大标题（不带副标题） */
const V2_FACET_ICONS = {
  pos: '<circle cx="12" cy="12" r="6.6"/><path d="M12 2.4v3.2M12 18.4v3.2M2.4 12h3.2M18.4 12h3.2"/><circle cx="12" cy="12" r="1.5" fill="currentColor" stroke="none"/>',
  route: '<circle cx="5.6" cy="18" r="2.4"/><circle cx="18.4" cy="6" r="2.4"/><path d="M8 18h4.2a3 3 0 0 0 3-3V9a3 3 0 0 1 3-3h.2"/>',
  persona: '<circle cx="12" cy="8" r="3.6"/><path d="M4.5 20c0-4 3.4-6.4 7.5-6.4S19.5 16 19.5 20"/>',
  product: '<path d="M12 3l9 4.6-9 4.6L3 7.6 12 3z"/><path d="M3 12.2l9 4.6 9-4.6"/><path d="M3 16.8l9 4.6 9-4.6"/>',
  value: '<path d="M12 3l2.3 5.7L20 11l-5.7 2.3L12 19l-2.3-5.7L4 11l5.7-2.3L12 3z"/>',
  tone: '<rect x="3" y="4" width="18" height="16" rx="4.5"/><circle cx="9" cy="10" r="1.5" fill="currentColor" stroke="none"/><circle cx="15" cy="10" r="1.5" fill="currentColor" stroke="none"/><path d="M9 15c1.9 1.3 4.1 1.3 6 0"/>',
  diff: '<path d="M12 3l7 3.4v6c0 4.4-3 8-7 9.2-4-1.2-7-4.8-7-9.2v-6L12 3z"/><path d="M8.8 12.2l2.2 2.2 4.2-4.6"/>',
};

function v2FacetCard(cls, icon, title, body) {
  return `<article class="v2-facet ${cls}">
    <header class="v2-facet-head">
      <span class="v2-facet-ico" aria-hidden="true"><svg viewBox="0 0 24 24">${V2_FACET_ICONS[icon] || ""}</svg></span>
      <h3>${escapeHtml(title)}</h3>
    </header>
    ${body}
  </article>`;
}

/* 账号定位右侧的点缀：同心圆「定位」图形，纯装饰 */
const V2_POS_FIG = `<svg viewBox="0 0 240 132" fill="none">
  <defs>
    <radialGradient id="v2posGlow" cx="0.5" cy="0.5" r="0.5">
      <stop offset="0%" stop-color="#EDEFF1" stop-opacity=".85"/>
      <stop offset="100%" stop-color="#EDEFF1" stop-opacity="0"/>
    </radialGradient>
  </defs>
  <ellipse cx="120" cy="66" rx="116" ry="64" fill="url(#v2posGlow)"/>
  <circle cx="120" cy="66" r="57" fill="#FAFBFB"/>
  <circle cx="120" cy="66" r="57" stroke="#E2E5E8" stroke-width="1.4"/>
  <circle cx="120" cy="66" r="41" stroke="#E2E5E8" stroke-width="1.4" stroke-dasharray="4 6"/>
  <circle cx="120" cy="66" r="25" stroke="#E2E5E8" stroke-width="1.5"/>
  <circle cx="120" cy="66" r="11" stroke="#AEB4BB" stroke-width="1.8"/>
  <circle cx="120" cy="66" r="4.6" fill="#AEB4BB"/>
  <path d="M120 2v16M120 114v16M52 66H70M170 66h18" stroke="#E2E5E8" stroke-width="1.8" stroke-linecap="round"/>
  <circle cx="177" cy="66" r="4.4" fill="#AEB4BB"/>
  <circle cx="120" cy="90" r="3.4" fill="#AEB4BB"/>
  <circle cx="81" cy="43" r="3" fill="#AEB4BB"/>
  <circle cx="153" cy="106" r="2.4" fill="#AEB4BB"/>
</svg>`;

/* 用户画像的配图：三枚叠起来的人像圆徽，纯装饰，不承载数据 */
const V2_PERSONA_FIG = `<svg viewBox="0 0 96 96" fill="none">
  <defs>
    <linearGradient id="v2p1" x1="0" y1="0" x2="1" y2="1"><stop offset="0%" stop-color="#AEB4BB"/><stop offset="100%" stop-color="#7E858E"/></linearGradient>
    <linearGradient id="v2p2" x1="0" y1="0" x2="1" y2="1"><stop offset="0%" stop-color="#E2E5E8"/><stop offset="100%" stop-color="#AEB4BB"/></linearGradient>
    <linearGradient id="v2p3" x1="0" y1="0" x2="1" y2="1"><stop offset="0%" stop-color="#AEB4BB"/><stop offset="100%" stop-color="#7E858E"/></linearGradient>
    <clipPath id="v2pc1"><circle cx="36" cy="40" r="18.5"/></clipPath>
    <clipPath id="v2pc2"><circle cx="67" cy="31" r="13.5"/></clipPath>
    <clipPath id="v2pc3"><circle cx="60" cy="65" r="20.5"/></clipPath>
  </defs>
  <rect width="96" height="96" rx="26" fill="#F6F7F8"/>
  <g clip-path="url(#v2pc1)">
    <circle cx="36" cy="40" r="18.5" fill="url(#v2p1)"/>
    <circle cx="36" cy="34.6" r="7" fill="#fff" opacity=".95"/>
    <path d="M19.4 62c0-10.2 7.4-18.4 16.6-18.4S52.6 51.8 52.6 62z" fill="#fff" opacity=".95"/>
  </g>
  <g clip-path="url(#v2pc2)">
    <circle cx="67" cy="31" r="13.5" fill="url(#v2p2)"/>
    <circle cx="67" cy="27" r="5.1" fill="#fff" opacity=".95"/>
    <path d="M55 47.2c0-7.3 5.4-13.2 12-13.2s12 5.9 12 13.2z" fill="#fff" opacity=".95"/>
  </g>
  <g clip-path="url(#v2pc3)">
    <circle cx="60" cy="65" r="20.5" fill="url(#v2p3)"/>
    <circle cx="60" cy="58.8" r="7.8" fill="#fff" opacity=".95"/>
    <path d="M41.5 89.6c0-11.6 8.3-21 18.5-21s18.5 9.4 18.5 21z" fill="#fff" opacity=".95"/>
  </g>
  <circle cx="36" cy="40" r="18.5" stroke="#fff" stroke-width="3.4"/>
  <circle cx="67" cy="31" r="13.5" stroke="#fff" stroke-width="3.4"/>
  <circle cx="60" cy="65" r="20.5" stroke="#fff" stroke-width="3.4"/>
</svg>`;

function v2CleanLead(text) {
  return String(text || "")
    .replace(/^(一句话概括|一句话总结|一句话|概括)[:：]\s*/, "")
    .replace(/^这一层(描述|讲|说)[^：:]{0,16}[:：]\s*/, "")
    .trim();
}

function v2FacetNote(text) {
  return `<p class="v2-facet-note">${escapeHtml(text)}</p>`;
}

/* 按分隔符切句，括号里的分隔符不算（「免费层（dbskill 工具、公开推文与 Skill）」要当成一个整体） */
function v2SplitTop(text, seps) {
  const out = [];
  let buf = "";
  let depth = 0;
  for (const ch of String(text || "")) {
    if (ch === "（" || ch === "(") depth += 1;
    else if (ch === "）" || ch === ")") depth = Math.max(0, depth - 1);
    if (depth === 0 && seps.includes(ch)) {
      out.push(buf);
      buf = "";
    } else {
      buf += ch;
    }
  }
  if (buf.trim()) out.push(buf);
  return out.map((item) => item.trim()).filter(Boolean);
}

/* 「低价知识库（信息型）→ 答疑社群（陪伴型）→ …」→ [{name, sub}]
   文档里用 → 也用过「与/，」，所以按箭头优先、其次按并列词切；
   只认文档真写出来的层级，切不出来就返回空，不拿别的东西凑 */
function v2ChainNodes(value) {
  const raw = String(value || "").trim();
  if (!raw || /^(信息不足|无证据|无$|暂无|待补)/.test(raw)) return [];
  let body = raw.split(/——|--/).pop();
  body = body.split("。")[0];
  let parts = v2SplitTop(body, "→");
  if (parts.length < 2) parts = v2SplitTop(body, "，、与和");
  if (parts.length < 2) return [];
  return parts.map((seg) => {
    const text = seg.replace(/^[①②③④⑤⑥\d.、\s]+/, "").replace(/^(一|两|三)层(是|为)?/, "").trim();
    const hit = /^(.+?)\s*[（(]([^）)]*)[）)]/.exec(text);
    const name = (hit ? hit[1] : text).replace(/[，。；:：]$/, "").trim();
    const sub = hit ? hit[2].trim() : "";
    return name ? { name, sub } : null;
  }).filter(Boolean).slice(0, 4);
}

/* 层级里的价格：只认「299 元」这种写在文案里的数字，没有就返回空 */
function v2NodePrice(node) {
  const text = `${node.name}${node.sub}`;
  if (/免费|公开|白送/.test(text)) return "免费";
  const hit = /(\d[\d,.]*)\s*(元|块)/.exec(text);
  return hit ? `${hit[1]} 元` : "";
}

/* 「三层——围观者（点赞收藏为主）、动手者（…）、付费者（…）」→ [{name, note}]
   有 ①② 按序号切，没有就先按分号、再不按顿号/与和切；括号里的分隔符不算 */
function v2PersonaLayers(value) {
  const raw = String(value || "").trim();
  if (!raw || /^(信息不足|无证据|暂无|待补)/.test(raw)) return [];
  let parts;
  if (/[①②③④⑤]/.test(raw)) {
    parts = [...raw.matchAll(/[①②③④⑤]\s*([^①②③④⑤]+)/g)].map((m) => m[1]);
  } else {
    const body = raw.split(/——|--/).pop().split("。")[0];
    parts = v2SplitTop(body, "；;");
    if (parts.length < 2) parts = v2SplitTop(body, "、与和，");
    if (parts.length < 2) parts = v2SplitTop(body, "／/｜|");
  }
  return parts.map((part) => {
    const text = part.replace(/^[①②③④⑤\d.、\s]+/, "").replace(/^(一|两|三)层(是|为)?/, "").replace(/[，。；]$/, "").trim();
    const hit = /^(.+?)\s*[（(]([^）)]*)[）)]/.exec(text);
    let name = (hit ? hit[1] : text).trim();
    let note = hit ? hit[2].replace(/[，。；]$/, "").trim() : "";
    // 没写括号、又长成一句话的，按第一个逗号/句号拆成「名字 + 说明」，不整句塞进标签
    if (!hit && name.length > 22) {
      const cut = name.search(/[，,。]/);
      if (cut > 4) {
        note = name.slice(cut + 1).trim();
        name = name.slice(0, cut).trim();
      }
    }
    return name ? { name, note } : null;
  }).filter(Boolean).slice(0, 4);
}

function analysisDrawer(model, body, options = {}) {
  const kicker = options.kicker || "账号洞察";
  const title = options.title || "完整分析";
  const sub = options.sub || `${escapeHtml(model.account.name || "")} · 判断依据、成立逻辑与明细`;
  return `<div class="v2-drawer" id="v2-analysis" aria-hidden="true">
    <div class="v2-drawer-mask" data-account-action="close-analysis"></div>
    <aside class="v2-drawer-panel" role="dialog" aria-modal="true" aria-label="${escapeAttribute(title)}">
      <header class="v2-drawer-head">
        <div>
          <p class="v2-drawer-kicker">${escapeHtml(kicker)}</p>
          <h2>${escapeHtml(title)}</h2>
          <p class="v2-drawer-sub">${sub}</p>
        </div>
        <button class="v2-drawer-close" type="button" data-account-action="close-analysis" aria-label="关闭${escapeAttribute(title)}">
          <svg viewBox="0 0 24 24" aria-hidden="true"><path d="m6 6 12 12M18 6 6 18"/></svg>
        </button>
      </header>
      <div class="v2-drawer-body">${body}</div>
    </aside>
  </div>`;
}

/* ==========================================================================
   账号洞察 · 完整分析（字段拆分版）
   页面数据区负责结构化字段，正文负责细分分析与边界；两边同一套结论。
   ========================================================================== */
const ANA_ICONS = {
  summary: '<svg viewBox="0 0 24 24"><path d="M12 3.2l1.7 4.9 4.9 1.7-4.9 1.7L12 16.4l-1.7-4.9L5.4 9.8l4.9-1.7z"/><path d="M18.4 15.6l.7 2 2 .7-2 .7-.7 2-.7-2-2-.7 2-.7z"/></svg>',
  performance: '<svg viewBox="0 0 24 24"><path d="M4 20V5M4 20h16"/><path d="M8.5 20v-5.5M13 20v-9M17.5 20v-3.5"/></svg>',
  persona: '<svg viewBox="0 0 24 24"><circle cx="12" cy="8" r="3.4"/><path d="M5 21c.9-4 3.2-6 7-6s6.1 2 7 6"/></svg>',
  products: '<svg viewBox="0 0 24 24"><path d="M12 3 21 8l-9 5-9-5z"/><path d="M3 12.6l9 5 9-5"/><path d="M3 17.2l9 5 9-5"/></svg>',
  value: '<svg viewBox="0 0 24 24"><path d="M12 3.2l6.6 8.8-6.6 8.8-6.6-8.8z"/><path d="M12 8.2l3 3.8-3 3.8-3-3.8z"/></svg>',
  style: '<svg viewBox="0 0 24 24"><circle cx="12" cy="12" r="8.6"/><path d="M8.4 14.2c1 1.2 2.2 1.8 3.6 1.8s2.6-.6 3.6-1.8"/><path d="M9.2 9.8h.01M14.8 9.8h.01"/></svg>',
  diff: '<svg viewBox="0 0 24 24"><circle cx="7.4" cy="7.4" r="3.4"/><circle cx="16.6" cy="16.6" r="3.4"/><path d="M10.8 7.4h4.2a2.2 2.2 0 0 1 2.2 2.2v3.8"/><path d="m14.8 11.6 2.4 2.4 2.4-2.4"/></svg>',
  logic: '<svg viewBox="0 0 24 24"><path d="M10.4 13.6a4 4 0 0 0 5.7 0l2.6-2.6a4 4 0 0 0-5.7-5.7l-1.3 1.3"/><path d="M13.6 10.4a4 4 0 0 0-5.7 0l-2.6 2.6a4 4 0 0 0 5.7 5.7l1.3-1.3"/></svg>',
  sources: '<svg viewBox="0 0 24 24"><path d="M6 3h9l3 3v15H6z"/><path d="M15 3v4h4M9 12h6M9 16h4"/></svg>',
};

const ANA_FIELDS = Object.freeze([
  { id: "summary", no: "01", label: "综合结论", tab: "综合结论", hint: "一句话说清他在做什么、靠什么站住" },
  { id: "performance", no: "02", label: "账号表现", tab: "账号表现", hint: "只看已采集作品，能确认到哪一步" },
  { id: "persona", no: "03", label: "用户画像", tab: "用户画像", hint: "谁在看、谁在付钱，两者差在哪" },
  { id: "products", no: "04", label: "产品服务体系", tab: "产品服务", hint: "免费给什么，靠什么收费" },
  { id: "value", no: "05", label: "价值主张", tab: "价值主张", hint: "他主张帮用户拿到什么结果" },
  { id: "style", no: "06", label: "人设风格", tab: "人设风格", hint: "他以什么身份、什么姿态出现" },
  { id: "diff", no: "07", label: "差异化", tab: "差异化", hint: "和同类内容相比，哪一点不同" },
  { id: "logic", no: "08", label: "成立逻辑", tab: "成立逻辑", hint: "这些要素如何互相支撑，何时失效" },
]);

function normalizeAnalysisHeading(value) {
  return String(value || "")
    .replace(/^#+\s*/, "")
    .replace(/^\s*\d+\s*[.、)．]\s*/, "")
    .replace(/\s+/g, "")
    .trim();
}

/* 把正文按 ## / ### 拆开：页面按字段渲染，不再把整篇 markdown 塞进一个块 */
function parseAnalysisDocument(content) {
  const text = String(content || "")
    .replace(/<!--\s*account-insight-summary:start\s*-->[\s\S]*?<!--\s*account-insight-summary:end\s*-->/g, "")
    .replace(/^#\s+.+\n/, "");
  const sections = new Map();
  text.split(/^##\s+/m).slice(1).forEach((part) => {
    const lines = part.split("\n");
    const title = normalizeAnalysisHeading(lines.shift() || "");
    if (!title) return;
    const body = lines.join("\n").trim();
    const sub = new Map();
    body.split(/^###\s+/m).slice(1).forEach((chunk) => {
      const subLines = chunk.split("\n");
      const key = normalizeAnalysisHeading(subLines.shift() || "");
      if (key) sub.set(key, subLines.join("\n").trim());
    });
    sections.set(title, { title, body, sub });
  });
  return sections;
}

function analysisSection(sections, aliases) {
  for (const alias of aliases) {
    const hit = sections.get(normalizeAnalysisHeading(alias));
    if (hit) return hit;
  }
  return null;
}

function analysisSub(section, name) {
  return section?.sub?.get(normalizeAnalysisHeading(name)) || "";
}

function analysisBullets(body) {
  const out = {};
  String(body || "").split("\n").forEach((line) => {
    const hit = /^\s*[-*]\s*(?:\*\*)?([^：:]{2,24}?)(?:\*\*)?[：:]\s*(.+)$/.exec(line);
    if (hit) out[normalizeAnalysisHeading(hit[1])] = anaStripInline(hit[2]);
  });
  return out;
}

function anaStripInline(value) {
  return String(value || "")
    .replace(/^\s*#{1,6}\s*/, "")
    .replace(/[*_`]/g, "")
    .replace(/\s+/g, " ")
    .trim();
}

function anaFirstParagraph(value) {
  return anaStripInline(String(value || "").split(/\n\s*\n/)[0] || "");
}

function anaProse(text, documentPath) {
  const body = String(text || "").trim();
  if (!body) return "";
  return `<div class="ana-prose markdown-body">${renderMarkdown(body, { documentPath: documentPath || "" })}</div>`;
}

function anaNote(label, text) {
  const body = anaStripInline(text);
  if (!body) return "";
  return `<div class="ana-note"><b>${escapeHtml(label)}</b><span>${escapeHtml(body)}</span></div>`;
}

function anaLead(text, phrases) {
  const value = anaStripInline(text);
  if (!value) return "";
  return `<p class="ana-lead">${highlightPhrases(value, phrases || [], "ana-kw")}</p>`;
}

function anaDetail(section, documentPath, fallback) {
  const text = String(analysisSub(section, "细分分析") || fallback || "").replace(/^[\u3000 \t]+/gm, "").trim();
  if (!text) return "";
  return `<div class="ana-detail"><p class="ana-label">细分分析</p>${anaProse(text, documentPath)}</div>`;
}

function anaBoundary() {
  // 依据与边界只留在正式文档里做追溯，不在页面展示。
  return "";
}

function anaCollectIds(items) {
  const out = [];
  (Array.isArray(items) ? items : []).forEach((item) => {
    if (!item) return;
    const ids = item["证据ID"];
    if (Array.isArray(ids)) out.push(...ids);
    else if (ids) out.push(ids);
  });
  return out;
}

function anaReadableTime(value) {
  const text = anaStripInline(value);
  const hit = /^(\d{4}-\d{2}-\d{2})T(\d{2}:\d{2})/.exec(text);
  return hit ? `${hit[1]} ${hit[2]}` : text;
}

function anaEvidence() {
  // 证据编号只在页面数据区内部使用，不展示给读者。
  return "";
}

function anaSourceBadge(type) {
  const text = String(type || "").trim();
  const tone = { 公开资料: "is-public", 作者自述: "is-author", 人工补充: "is-manual", 程序统计: "is-stat" }[text] || "";
  return `<span class="ana-src-badge ${tone}">${escapeHtml(text || "—")}</span>`;
}

function anaEvidenceTable(index) {
  const rows = (Array.isArray(index) ? index : []).filter((item) => item && item["ID"]);
  if (!rows.length) return '<p class="ana-muted">这份分析没有附证据索引。</p>';
  return `<div class="ana-table-wrap"><table class="ana-table">
    <thead><tr><th>编号</th><th>来源类型</th><th>来源定位</th><th>支撑内容</th><th>适用限制</th></tr></thead>
    <tbody>${rows.map((item) => `<tr>
      <td><b>${escapeHtml(item["ID"])}</b></td>
      <td>${anaSourceBadge(item["来源类型"])}</td>
      <td><code>${escapeHtml(item["来源定位"] || "—")}</code></td>
      <td>${escapeHtml(item["内容摘要"] || "—")}</td>
      <td>${escapeHtml(item["适用限制"] || "—")}</td>
    </tr>`).join("")}</tbody>
  </table></div>`;
}

function anaLikesStats(contents) {
  const values = (Array.isArray(contents) ? contents : [])
    .map((item) => numericValue(item.metrics?.likes))
    .filter((value) => value !== null)
    .sort((left, right) => left - right);
  if (!values.length) return { median: null, mean: null, max: null, samples: 0 };
  const middle = values.length >> 1;
  return {
    median: values.length % 2 ? values[middle] : Math.round((values[middle - 1] + values[middle]) / 2),
    mean: Math.round(values.reduce((sum, value) => sum + value, 0) / values.length),
    max: values[values.length - 1],
    samples: values.length,
  };
}

function anaCard(meta, body) {
  return `<article class="ana-card is-${meta.id}" id="ana-field-${meta.id}" data-field="${meta.id}">
    <header class="ana-head">
      <span class="ana-ico" aria-hidden="true">${ANA_ICONS[meta.id] || ""}</span>
      <div class="ana-titles"><h3>${escapeHtml(meta.label)}</h3><p>${escapeHtml(meta.hint)}</p></div>
      <span class="ana-no">${meta.no}</span>
    </header>
    ${body}
  </article>`;
}

function anaFieldSummary(model, insight, sections) {
  const summary = insight["综合结论"] || {};
  const doc = analysisSection(sections, ["综合结论"]);
  const text = summary["文本"] || doc?.body || "";
  const lead = anaLead(text, summary["重点短语"]) || '<p class="ana-muted">分析文档还没写出综合结论。</p>';
  return `${lead}${anaEvidence(summary["证据ID"], insight["证据索引"], "来自《账号详细分析》")}`;
}

function trendBrief(contents) {
  const points = (Array.isArray(contents) ? contents : [])
    .map((item) => ({ item, ts: dateTimestamp(item.published_at), value: engagementTotal(item) }))
    .filter((point) => point.ts !== null)
    .sort((left, right) => left.ts - right.ts);
  if (points.length < 4) return "作品还太少，暂时看不出前后变化。";
  const half = Math.floor(points.length / 2);
  const median = (list) => {
    const sorted = [...list].sort((left, right) => left - right);
    const mid = Math.floor(sorted.length / 2);
    return sorted.length % 2 ? sorted[mid] : Math.round((sorted[mid - 1] + sorted[mid]) / 2);
  };
  const early = median(points.slice(0, half).map((point) => point.value));
  const late = median(points.slice(-half).map((point) => point.value));
  const change = early > 0 ? Math.round(((late - early) / early) * 100) : null;
  const direction = change === null
    ? "前后两段暂时无法比较"
    : change > 15 ? `最近一半作品比前一半互动高约 ${change}%，热度在往上走`
    : change < -15 ? `最近一半作品比前一半互动低约 ${Math.abs(change)}%，热度在往下走`
    : "前后两段互动基本持平";
  const peak = points.reduce((best, point) => (point.value > best.value ? point : best), points[0]);
  return `${direction}；最高一条是《${shortQuote(peak.item.title || "未命名", 22)}》，互动 ${formatNumber(peak.value)}。`;
}

function anaFieldPerformance(model, insight, sections) {
  const performance = insight["账号表现"] || {};
  const doc = analysisSection(sections, ["账号表现"]);
  const bullets = analysisBullets(doc?.body || "");
  const contents = Array.isArray(model.contents) ? model.contents : [];
  const chartNote = performance["图表解读"] || bullets["图表解读"] || chartNoteFallback(contents);
  const notes = chartNoteMap(chartNote);

  const buttons = TREND_METRICS.map((item, index) => `<button class="ana-seg-btn${index === 0 ? " is-active" : ""}" type="button" role="tab" aria-selected="${index === 0}" data-ana-metric="${item.id}">${escapeHtml(item.label)}</button>`).join("");
  const panels = TREND_METRICS.map((item, index) => `<div class="ana-trend-panel${index === 0 ? " is-active" : ""}" data-ana-panel="${item.id}">${renderTrend(contents, item.id, { compact: true })}</div>`).join("");

  return `<div class="ana-perf-grid">
      <div class="ana-sub">
        <div class="ana-sub-head">${perfTag("trend", "互动趋势")}<span>按发布时间 · 取最近 16 条有发布时间的作品</span></div>
        ${notes["互动趋势"] ? `<p class="perf-desc">${escapeHtml(notes["互动趋势"])}</p>` : ""}
        <div class="ana-seg" role="tablist" aria-label="互动趋势指标">${buttons}</div>
        <div class="ana-trends">${panels}</div>
      </div>
      <div class="ana-sub">
        <div class="ana-sub-head">${perfTag("rhythm", "发布规律")}<span>按发布时间的小时分布</span></div>
        ${notes["发布规律"] ? `<p class="perf-desc">${escapeHtml(notes["发布规律"])}</p>` : ""}
        <div class="ana-rhythm">${renderPublishingRhythm(contents, { compact: true })}</div>
      </div>
    </div>
    <div class="ana-sub">
      <div class="ana-sub-head">${perfTag("dist", "互动分布")}<span>横轴为互动数（对数刻度），竖线为账号内中位数与高低档线</span></div>
      ${notes["互动分布"] ? `<p class="perf-desc">${escapeHtml(notes["互动分布"])}</p>` : ""}
      <div class="ana-dist">${renderDistribution(contents)}</div>
    </div>`;
}

function anaFieldPersona(model, insight, sections, documentPath) {
  const user = insight["用户画像"] || {};
  const doc = analysisSection(sections, ["用户画像", "1. 用户画像"]);
  const tags = (Array.isArray(user["标签"]) ? user["标签"] : []).map((tag) => {
    const text = typeof tag === "string" ? tag : tag?.["文本"];
    if (!text) return "";
    const category = typeof tag === "string" ? "" : tag?.["类别"];
    return `<span class="ana-tag">${category ? `<i>${escapeHtml(category)}</i>` : ""}${escapeHtml(text)}</span>`;
  }).filter(Boolean).join("");
  const needs = (Array.isArray(user["需求"]) ? user["需求"] : []).map((need) => {
    const text = typeof need === "string" ? need : need?.["文本"];
    return text ? `<li>${escapeHtml(text)}</li>` : "";
  }).filter(Boolean).join("");

  const ids = [...anaCollectIds(user["标签"]), ...anaCollectIds(user["需求"])];
  return `${anaLead(user["核心描述"]) || '<p class="ana-muted">分析里还没写出目标用户这一段。</p>'}
    ${tags ? `<div class="ana-tags">${tags}</div>` : ""}
    ${needs ? `<div class="ana-needs"><p class="ana-label">核心需求</p><ul class="ana-need-list">${needs}</ul></div>` : ""}
    ${anaDetail(doc, documentPath)}
    ${anaBoundary(doc, user["限制说明"])}
    ${anaEvidence(ids, insight["证据索引"], "来自《账号详细分析》")}`;
}

function anaFieldProducts(model, insight, sections, documentPath) {
  const products = insight["产品服务体系"] || {};
  const doc = analysisSection(sections, ["产品服务体系", "2. 产品服务体系"]);
  const rows = (Array.isArray(products["产品"]) ? products["产品"] : []).map((item) => {
    const name = item?.["名称"] || "未命名产品";
    const type = item?.["类型"] || "";
    const purpose = item?.["用途"] || "";
    const price = priceText(item?.["价格"]);
    const tone = price === "免费" ? "is-free" : /未获取|未公开标价|口径冲突/.test(price) ? "is-quote" : "is-paid";
    return `<div class="ana-product">
      <span class="ana-product-name"><b>${escapeHtml(name)}</b>${type || purpose ? `<i>${escapeHtml([type, purpose].filter(Boolean).join(" · "))}</i>` : ""}</span>
      <span class="ana-price ${tone}">${escapeHtml(price)}</span>
    </div>`;
  }).join("");

  return `${anaLead(products["核心描述"]) || '<p class="ana-muted">报告里没有可展示的产品或服务。</p>'}
    ${rows ? `<div class="ana-products">${rows}</div>` : ""}
    ${anaDetail(doc, documentPath)}
    ${anaBoundary(doc, products["限制说明"])}
    ${anaEvidence(anaCollectIds(products["产品"]), insight["证据索引"], "来自《账号详细分析》")}`;
}

function anaFieldValue(model, insight, sections, documentPath) {
  const value = insight["价值主张"] || {};
  const doc = analysisSection(sections, ["价值主张", "3. 价值主张"]);
  const text = anaStripInline(value["核心描述"]);
  const quote = text ? `<p class="ana-quote">${highlightPhrases(text, value["重点短语"] || [], "ana-kw")}</p>` : '<p class="ana-muted">分析里还没写出价值主张。</p>';
  return `${quote}
    ${anaDetail(doc, documentPath)}
    ${anaBoundary(doc, value["限制说明"])}
    ${anaEvidence(value["证据ID"], insight["证据索引"], "来自《账号详细分析》")}`;
}

function anaFieldStyle(model, insight, sections, documentPath) {
  const persona = insight["人设风格"] || {};
  const doc = analysisSection(sections, ["人设风格", "4. 人设风格"]);
  const groups = [
    ["身份", persona["身份标签"]],
    ["角色", persona["角色标签"]],
    ["表达", persona["表达标签"]],
  ].map(([label, list]) => {
    const chips = (Array.isArray(list) ? list : []).map((item) => {
      const text = typeof item === "string" ? item : item?.["文本"];
      return text ? `<span class="ana-mini">${escapeHtml(text)}</span>` : "";
    }).filter(Boolean).join("");
    return chips ? `<div class="ana-fact"><dt>${escapeHtml(label)}</dt><dd>${chips}</dd></div>` : "";
  }).filter(Boolean).join("");
  const tones = (Array.isArray(persona["表达倾向"]) ? persona["表达倾向"] : []).map((item) => {
    if (!item?.["倾向"]) return "";
    return `<div class="ana-tone"><span>${escapeHtml(item["维度"] || "倾向")}</span><b>${escapeHtml(item["倾向"])}</b><em>${escapeHtml(anaStripInline(item["说明"] || ""))}</em></div>`;
  }).filter(Boolean).join("");
  const ids = [...anaCollectIds(persona["身份标签"]), ...anaCollectIds(persona["角色标签"]), ...anaCollectIds(persona["表达标签"]), ...anaCollectIds(persona["表达倾向"])];

  return `${anaLead(persona["核心描述"]) || '<p class="ana-muted">分析里还没写出人设这一段。</p>'}
    ${groups ? `<dl class="ana-facts">${groups}</dl>` : ""}
    ${tones ? `<div class="ana-tones">${tones}</div>` : ""}
    ${anaDetail(doc, documentPath)}
    ${anaBoundary(doc, persona["限制说明"])}
    ${anaEvidence(ids, insight["证据索引"], "来自《账号详细分析》")}`;
}

function anaFieldDiff(model, insight, sections, documentPath) {
  const difference = insight["差异化"] || {};
  const doc = analysisSection(sections, ["差异化", "5. 差异化"]);
  const features = (Array.isArray(difference["特点"]) ? difference["特点"] : []).map((item, index) => {
    const title = item?.["本账号特点"];
    if (!title) return "";
    const compare = [item["比较对象"], item["区别说明"]].filter(Boolean).join(" · ");
    return `<div class="ana-diff"><span class="n">${index + 1}</span><b>${escapeHtml(title)}</b>${compare ? `<span>${escapeHtml(compare)}</span>` : ""}</div>`;
  }).filter(Boolean).join("");
  return `${anaLead(difference["核心描述"]) || '<p class="ana-muted">分析里还没写出差异化这一段。</p>'}
    ${features ? `<div class="ana-diffs">${features}</div>` : ""}
    ${difference["对比状态"] ? `<span class="ana-status">${escapeHtml(difference["对比状态"])}</span>` : ""}
    ${anaDetail(doc, documentPath)}
    ${anaBoundary(doc, difference["限制说明"])}
    ${anaEvidence(anaCollectIds(difference["特点"]), insight["证据索引"], "来自《账号详细分析》")}`;
}

function anaFieldLogic(model, insight, sections, documentPath) {
  const doc = analysisSection(sections, ["成立逻辑", "6. 成立逻辑"]);
  if (!doc) return '<p class="ana-muted">这份分析没有单独的「成立逻辑」章节。</p>';
  const core = analysisSub(doc, "核心描述") || anaFirstParagraph(doc.body);
  const detail = analysisSub(doc, "细分分析");
  return `${anaLead(core) || '<p class="ana-muted">成立逻辑还没写出核心判断。</p>'}
    ${detail ? `<div class="ana-detail"><p class="ana-label">细分分析</p>${anaProse(detail, documentPath)}</div>` : ""}`;
}

function anaFieldSources(model, insight, sections, documentPath) {
  const doc = analysisSection(sections, ["资料范围与来源", "资料与来源", "资料说明"]);
  const perfDoc = analysisSection(sections, ["账号表现"]);
  const bullets = analysisBullets(perfDoc?.body || "");
  const scope = [
    ["采集范围", bullets["样本范围"]],
    ["统计口径", bullets["统计依据"]],
  ].filter(([, value]) => value).map(([label, value]) => `<div><dt>${escapeHtml(label)}</dt><dd>${escapeHtml(value)}</dd></div>`).join("");

  return `${scope ? `<dl class="ana-scope">${scope}</dl>` : ""}
    ${doc?.body ? anaProse(doc.body, documentPath) : ""}
    ${insight["资料说明"] ? anaNote("资料说明", insight["资料说明"]) : ""}
    ${anaEvidenceTable(insight["证据索引"])}`;
}

function renderInsightAnalysisBody(model, insight, source) {
  const sections = parseAnalysisDocument(source);
  const documentPath = model.analysis?.detail?.path || "";
  const bodies = {
    summary: () => anaFieldSummary(model, insight, sections),
    performance: () => anaFieldPerformance(model, insight, sections),
    persona: () => anaFieldPersona(model, insight, sections, documentPath),
    products: () => anaFieldProducts(model, insight, sections, documentPath),
    value: () => anaFieldValue(model, insight, sections, documentPath),
    style: () => anaFieldStyle(model, insight, sections, documentPath),
    diff: () => anaFieldDiff(model, insight, sections, documentPath),
    logic: () => anaFieldLogic(model, insight, sections, documentPath),
    sources: () => anaFieldSources(model, insight, sections, documentPath),
  };
  const tabs = ANA_FIELDS.map((meta) => `<a class="ana-tab is-${meta.id}" href="#ana-field-${meta.id}" data-field="${meta.id}"><span class="n">${meta.no}</span>${escapeHtml(meta.tab)}</a>`).join("");
  const cards = ANA_FIELDS.map((meta) => anaCard(meta, bodies[meta.id]())).join("");
  return `<div class="ana-body">
    <nav class="ana-tabs" aria-label="完整分析字段索引">${tabs}</nav>
    <div class="ana-fields">${cards}</div>
  </div>`;
}


function parseInsightSummary(text) {
  const block = /<!--\s*account-insight-summary:start\s*-->([\s\S]*?)<!--\s*account-insight-summary:end\s*-->/.exec(String(text || ""));
  if (!block) return null;
  const fence = /```json\s*([\s\S]*?)```/.exec(block[1]);
  if (!fence) return null;
  try {
    const data = JSON.parse(fence[1]);
    return data && data["版本"] === "account-insight-v1" ? data : null;
  } catch (_error) {
    return null;
  }
}

function insightData(model) {
  return parseInsightSummary(model.analysisContent.detail || model.analysisContent.overview || "");
}

function parseSelfDiagnosisSummary(text) {
  const block = /<!--\s*self-account-diagnosis-summary:start\s*-->([\s\S]*?)<!--\s*self-account-diagnosis-summary:end\s*-->/.exec(String(text || ""));
  if (!block) return null;
  const fence = /```json\s*([\s\S]*?)```/.exec(block[1]);
  if (!fence) return null;
  try {
    const data = JSON.parse(fence[1]);
    if (!data) return null;
    if (data["版本"] === "self-account-diagnosis-v2") return data;
    if (data["版本"] !== "self-account-diagnosis-v1") return null;
    /* 旧版诊断（v1）：没有“依据/具体表现/本轮依据与边界”，按原文或“未记录”兜底。 */
    return {
      ...data,
      "目标与实际": (Array.isArray(data["目标与实际"]) ? data["目标与实际"] : []).map((item) => ({
        ...item,
        "依据": item["依据"] || "",
      })),
      "关键问题": (Array.isArray(data["关键问题"]) ? data["关键问题"] : []).map((item) => ({
        ...item,
        "具体表现": item["具体表现"] || "",
      })),
      "本轮依据与边界": data["本轮依据与边界"] || null,
    };
  } catch (_error) {
    return null;
  }
}

function ownInsightData(model) {
  return parseSelfDiagnosisSummary(model.analysisContent.detail || "");
}

function parseActionListSummary(text) {
  const block = /<!--\s*account-action-list:start\s*-->([\s\S]*?)<!--\s*account-action-list:end\s*-->/.exec(String(text || ""));
  if (!block) return null;
  const fence = /```json\s*([\s\S]*?)```/.exec(block[1]);
  if (!fence) return null;
  try {
    const data = JSON.parse(fence[1]);
    return data && data["版本"] === "account-action-list-v1" ? data : null;
  } catch (_error) {
    return null;
  }
}

function parseAdviceSummary(text) {
  const block = /<!--\s*action-advice-summary:start\s*-->([\s\S]*?)<!--\s*action-advice-summary:end\s*-->/.exec(String(text || ""));
  if (!block) return null;
  const fence = /```json\s*([\s\S]*?)```/.exec(block[1]);
  if (!fence) return null;
  try {
    const data = JSON.parse(fence[1]);
    if (!data) return null;
    return ["action-advice-v1", "action-advice-v2"].includes(data["版本"]) ? data : null;
  } catch (_error) {
    return null;
  }
}

/* v2 直接读「具体怎么做」；v1 兼容时合并三段动作说明，不再展示类型与依据 */
function adviceHowText(card) {
  const how = card["具体怎么做"];
  if (typeof how === "string" && how.trim()) return how.trim();
  return ["尝试动作", "借鉴结构", "我方调整"]
    .map((key) => (typeof card[key] === "string" ? card[key].trim() : ""))
    .filter(Boolean)
    .join(" ");
}

function adviceRefHtml(model, work, byId) {
  const title = work?.["标题"] || work?.["内容ID"] || "参考作品";
  const id = work?.["内容ID"];
  const target = byId.get(id);
  // 和「代表作品」同一套内容卡：点开抽屉读视频文案
  if (target) return `<div class="v2-wcards is-advice">${miniWorkCard(target, 1, model)}</div>`;
  return id
    ? `<a class="act2-ref is-good" href="${contentUrl(model.kind, id)}"><span class="act2-ref-body"><span class="act2-ref-t">${escapeHtml(shortQuote(title, 34))}</span></span></a>`
    : `<span class="act2-ref is-good"><span class="act2-ref-body"><span class="act2-ref-t">${escapeHtml(shortQuote(title, 34))}</span></span></span>`;
}

/* 一张卡：标题 → 一条参考作品 → 具体怎么做 → 发布后看什么 */
function renderAdviceCard(model, card, index, byId) {
  const works = Array.isArray(card["参考作品"]) ? card["参考作品"] : [];
  const signals = (Array.isArray(card["观察信号"]) ? card["观察信号"] : [])
    .map((line) => `<li>${escapeHtml(line)}</li>`).join("");
  return `<section class="act2-card is-act">
    <div class="act2-head">
      <span class="act2-no">${String(index + 1).padStart(2, "0")}</span>
      <h2>${escapeHtml(card["标题"] || "下一步尝试")}</h2>
    </div>
    ${works[0] ? `<div class="act2-refs is-lead"><div class="act2-refs-h">参考作品</div>${adviceRefHtml(model, works[0], byId)}</div>` : ""}
    <dl class="act2-field"><dt>具体怎么做</dt><dd>${escapeHtml(adviceHowText(card) || "—")}</dd></dl>
    <dl class="act2-field"><dt>发布后看什么</dt><dd>${signals ? `<ul class="act2-signals">${signals}</ul>` : "—"}</dd></dl>
  </section>`;
}

function adviceAdaptationCard(adaptation, reasons) {
  return `<section class="act2-card is-full">
    <div class="act2-head">
      <div class="act2-ico"><svg viewBox="0 0 24 24"><circle cx="12" cy="12" r="8.4"/><path d="M8.2 12.4l2.6 2.6 5-5.2"/></svg></div>
      <h2>适配结论</h2>
    </div>
    <div class="act2-verdict">
      <p>${escapeHtml(adaptation["总体判断"] || "")}</p>
    </div>
    ${reasons.length ? `<ul class="act2-reasons">${reasons.map((line) => `<li>${escapeHtml(line)}</li>`).join("")}</ul>` : ""}
    ${adaptation["适用边界"] ? `<p class="act2-boundary">${escapeHtml(adaptation["适用边界"])}</p>` : ""}
  </section>`;
}

function renderV2ActionFromAdvice(model, advice, head) {
  const adaptation = advice["适配结论"] || {};
  const reasons = Array.isArray(adaptation["理由"]) ? adaptation["理由"] : [];
  const byId = new Map((model.contents || []).map((item) => [item.id, item]));
  const cards = (Array.isArray(advice["行动卡片"]) ? advice["行动卡片"] : [])
    .map((card, index) => renderAdviceCard(model, card, index, byId)).join("");

  return `<section class="v2-block is-plain" id="v2-action">
    <div class="learn2-app">
      ${head}
      <div class="act2-grid">
        ${adviceAdaptationCard(adaptation, reasons)}
        ${cards || v2Missing("行动建议里还没有具体行动卡片。")}
      </div>
      <div class="learn2-foot">
        <svg viewBox="0 0 24 24"><circle cx="12" cy="12" r="9"/><path d="M12 11v5.5"/><circle cx="12" cy="7.8" r=".9" fill="currentColor" stroke="none"/></svg>
        适配结论和行动卡片来自《行动建议》；参考作品来自对标账号已采集作品。
      </div>
    </div>
  </section>`;
}

function priceText(price) {
  const node = price && typeof price === "object" ? price : {};
  const status = String(node["状态"] || "未获取");
  if (status === "免费") return "免费";
  if (status === "明确标价") {
    const amount = node["金额"];
    const unit = [node["币种"], node["计价单位"]].filter(Boolean).join(" / ");
    return `${numericValue(amount) !== null ? formatNumber(amount) : amount}${unit ? ` ${unit}` : ""}`;
  }
  return status;
}

function highlightPhrases(text, phrases, className = "v2-kw") {
  let html = escapeHtml(String(text || ""));
  for (const phrase of Array.isArray(phrases) ? phrases : []) {
    const safe = escapeHtml(String(phrase || ""));
    if (safe) html = html.split(safe).join(`<b class="${className}">${safe}</b>`);
  }
  return html;
}

function medianEngagement(contents) {
  const values = (contents || [])
    .map((item) => numericValue(item.metrics?.interactions) ?? engagementTotal(item))
    .filter((value) => value !== null)
    .sort((left, right) => left - right);
  if (!values.length) return null;
  const middle = Math.floor(values.length / 2);
  return values.length % 2 ? values[middle] : Math.round((values[middle - 1] + values[middle]) / 2);
}

function renderV2WorthFromSummary(model, insight) {
  const source = model.analysisContent.detail || model.analysisContent.overview || "";
  const worthHead = v2Head("insight", "账号洞察", "他是谁、做什么、服务谁，以及值不值得研究。", `<button class="v2-more" type="button" data-account-action="open-analysis">查看完整分析<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M5 12h13M12.5 6.5 18.5 12l-6 5.5"/></svg></button>`);
  const summary = insight["综合结论"] || {};
  const performance = insight["账号表现"] || {};
  const user = insight["用户画像"] || {};
  const products = insight["产品服务体系"] || {};

  const posCard = v2FacetCard("is-pos", "pos", "综合结论",
    `<div class="v2-pos-body">
      <p class="v2-facet-judge is-lead">${highlightPhrases(summary["文本"] || "分析文档还没写出综合结论。", summary["重点短语"])}</p>
      <span class="v2-pos-fig" aria-hidden="true">${V2_POS_FIG}</span>
    </div>`);

  const chartNote = performance["图表解读"] || chartNoteFallback(model.contents);
  const notes = chartNoteMap(chartNote);
  const perfBody = `<div class="v2-perf">
      <div class="v2-perf-charts">
        <div class="v2-perf-chart">
          ${perfTag("trend", "互动趋势")}
          ${notes["互动趋势"] ? `<p class="v2-perf-desc">${escapeHtml(notes["互动趋势"])}</p>` : ""}
          ${renderTrend(model.contents, TREND_METRICS[0].id, { compact: true })}
        </div>
        <div class="v2-perf-chart">
          ${perfTag("rhythm", "发布规律")}
          ${notes["发布规律"] ? `<p class="v2-perf-desc">${escapeHtml(notes["发布规律"])}</p>` : ""}
          ${renderPublishingRhythm(model.contents, { compact: true })}
        </div>
        <div class="v2-perf-chart is-wide">
          ${perfTag("dist", "互动分布")}
          ${notes["互动分布"] ? `<p class="v2-perf-desc">${escapeHtml(notes["互动分布"])}</p>` : ""}
          ${renderDistribution(model.contents)}
        </div>
      </div>
    </div>`;
  const perfCard = v2FacetCard("is-perf", "chart", "账号表现", perfBody);

  const tags = (Array.isArray(user["标签"]) ? user["标签"] : []).map((tag) => {
    const text = typeof tag === "string" ? tag : tag?.["文本"];
    if (!text) return "";
    const category = typeof tag === "string" ? "" : tag?.["类别"];
    return `<span>${category ? `<i>${escapeHtml(category)}</i>` : ""}${escapeHtml(text)}</span>`;
  }).filter(Boolean).join("");
  const needs = (Array.isArray(user["需求"]) ? user["需求"] : []).map((need) => {
    const text = typeof need === "string" ? need : need?.["文本"];
    return text ? `<li>${escapeHtml(text)}</li>` : "";
  }).filter(Boolean).join("");
  const personaBody = `<div class="v2-persona-top">
      <span class="v2-persona-fig" aria-hidden="true">${V2_PERSONA_FIG}</span>
      ${user["核心描述"] ? `<p class="v2-facet-judge">${escapeHtml(user["核心描述"])}</p>` : v2FacetNote("分析里还没写出目标用户这一段。")}
    </div>
    ${tags ? `<div class="v2-tags2">${tags}</div>` : ""}
    ${needs ? `<div class="v2-layers"><p class="v2-layers-cap">核心需求</p><ul>${needs}</ul></div>` : ""}`;
  const personaCard = v2FacetCard("is-persona", "persona", "用户画像", personaBody);

  const productRows = (Array.isArray(products["产品"]) ? products["产品"] : []).map((item) => {
    const name = item?.["名称"] || "未命名产品";
    const type = item?.["类型"] || "";
    const price = priceText(item?.["价格"]);
    return `<div class="v2-prow">
      <span class="v2-pname"><b>${escapeHtml(name)}</b>${type ? `<i>${escapeHtml(type)}</i>` : ""}</span>
      <span class="v2-price${/未获取|未公开标价|口径冲突/.test(price) ? " is-empty" : ""}">${escapeHtml(price)}</span>
    </div>`;
  }).join("");
  const productCard = v2FacetCard("is-product", "product", "产品服务体系",
    productRows ? `<div class="v2-plist">${productRows}</div>` : v2FacetNote("报告里没有可展示的产品或服务。"));

  const drawerBody = renderInsightAnalysisBody(model, insight, source);

  return `<section class="v2-block" id="v2-worth">
    ${worthHead}
    <div class="v2-facets">
      ${posCard}
      ${perfCard}
      ${personaCard}
      ${productCard}
    </div>
    ${analysisDrawer(model, drawerBody)}
  </section>`;
}

function ownJudgeTone(value) {
  const text = String(value || "").trim();
  if (/不一致|偏位|仍存在|出现新问题|变差|仍然差|有好有坏/.test(text)) return "is-warn";
  if (/一致|已改善|有改善|保持|变好/.test(text)) return "is-ok";
  return "is-muted";
}

function stageVerdictClass(value) {
  const text = String(value || "").trim();
  if (text === "变好" || text === "有改善") return "is-ok";
  if (text === "变差" || text === "仍然差" || text === "有好有坏") return "is-warn";
  return "is-neutral";
}

function ownBadge(value) {
  const text = String(value || "").trim() || "证据不足";
  return `<span class="own-badge ${ownJudgeTone(text)}">${escapeHtml(text)}</span>`;
}

function renderOwnWorth(model, insight) {
  const source = model.analysisContent.detail || "";
  const head = v2Head(
    "insight",
    "账号洞察",
    "现在最主要的问题是什么、差距在哪、有没有在变好。",
    `<button class="v2-more" type="button" data-account-action="open-analysis">查看完整诊断<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M5 12h13M12.5 6.5 18.5 12l-6 5.5"/></svg></button>`,
  );
  const summary = insight["综合诊断"] || {};
  const posCard = v2FacetCard("is-pos", "pos", "综合诊断",
    `<div class="v2-pos-body">
      <p class="v2-facet-judge is-lead">${highlightPhrases(summary["文本"] || "诊断还没写出综合判断。", summary["重点短语"])}</p>
      <span class="v2-pos-fig" aria-hidden="true">${V2_POS_FIG}</span>
    </div>
    ${summary["问题类型"] ? `<div class="v2-tags2"><span><i>问题类型</i>${escapeHtml(summary["问题类型"])}</span></div>` : ""}`);

  const issues = Array.isArray(insight["关键问题"]) ? insight["关键问题"] : [];
  const issueBody = issues.length
    ? `<div class="own-actions">${issues.map((item, index) => `<div class="own-action">
        <div class="own-action-head"><b>${index + 1}. ${escapeHtml(item["标题"] || "")}</b>${ownBadge(item["类型"] || "")}</div>
        ${item["具体表现"] ? `<p><span>具体表现</span>${escapeHtml(item["具体表现"])}</p>` : ""}
        ${item["影响"] ? `<p><span>实际影响</span>${escapeHtml(item["影响"])}</p>` : ""}
        ${item["依据"] ? `<p><span>判断依据</span>${escapeHtml(item["依据"])}</p>` : ""}
      </div>`).join("")}</div>`
    : v2FacetNote("当前未发现需要优先处理的关键问题，按现有方向继续发布并观察。");
  const issueCard = v2FacetCard("is-diff", "diff", "关键问题", issueBody);

  const targets = Array.isArray(insight["目标与实际"]) ? insight["目标与实际"] : [];
  const targetRows = targets.map((item) => `
    <div class="own-action">
      <div class="own-action-head"><b>${escapeHtml(item["维度"] || "")}</b>${ownBadge(item["结论"])}</div>
      ${item["原因"] ? `<p><span>原因</span>${escapeHtml(item["原因"])}</p>` : ""}
      ${item["建议"] ? `<p><span>建议</span>${escapeHtml(item["建议"])}</p>` : ""}
    </div>`).join("");
  const targetCard = v2FacetCard("is-persona is-own-wide", "persona", "目标与实际",
    targetRows ? `<div class="own-actions">${targetRows}</div>` : v2FacetNote("诊断里还没有写出目标与实际。"));

  const stage = insight["这段时间的变化"] || insight["阶段变化"] || {};
  const stageState = stage["总体"] || stage["状态"] || "首次诊断";
  const stageChanges = Array.isArray(stage["变化"]) ? stage["变化"] : [];
  const stageLegacy = !stageChanges.length && !String(stage["一句话总结"] || "").trim();
  const stageList = (label, items) => (Array.isArray(items) && items.length
    ? `<div class="v2-layers"><p class="v2-layers-cap">${label}</p><ul>${items.map((text) => `<li>${escapeHtml(text)}</li>`).join("")}</ul></div>`
    : "");
  const legacyBody = `<div class="v2-tags2"><span><i>总体</i>${escapeHtml(stageState)}</span></div>
    ${stageList("已改善", stage["已改善"])}
    ${stageList("仍存在", stage["仍存在"])}
    ${stageList("新增问题", stage["新增问题"])}
    ${stage["上次行动结果"] ? `<p class="v2-facet-note">${escapeHtml(stage["上次行动结果"])}</p>` : ""}`;
  const changeBlocks = stageChanges.map((item, index) => {
    const head = `<p class="v2-layers-cap">变化 ${index + 1} · ${escapeHtml(item["方面"] || "")}${item["结论"] ? ` <span class="v2-stage-verdict ${stageVerdictClass(item["结论"])}">${escapeHtml(item["结论"])}</span>` : ""}</p>`;
    if (item["文案"]) {
      return `<div class="v2-layers">${head}<ul><li>${escapeHtml(item["文案"])}</li></ul></div>`;
    }
    return `<div class="v2-layers">
      ${head}
      <ul><li>
        <b>${escapeHtml(item["哪里变了"] || "")}</b>
        ${item["证据"] ? `<span>证据：${escapeHtml(item["证据"])}</span>` : ""}
        ${item["说明什么"] ? `<span>说明什么：${escapeHtml(item["说明什么"])}</span>` : ""}
      </li></ul>
    </div>`;
  }).join("");
  const stageBody = stageLegacy
    ? legacyBody
    : `<div class="v2-tags2"><span><i>总体</i>${escapeHtml(stageState)}</span></div>
      ${stage["一句话总结"] ? `<p class="v2-facet-judge">${escapeHtml(stage["一句话总结"])}</p>` : ""}
      ${changeBlocks}`;
  const stageCard = v2FacetCard("is-stage is-own-wide", "value", "这段时间的变化", stageBody);

  const boundary = insight["本轮依据与边界"];
  const boundaryFields = [
    ["目标账号", "目标账号"],
    ["判断基准", "判断基准"],
    ["已获取内容", "已获取内容"],
    ["公开与私密范围", "公开与私密范围"],
    ["正文级证据", "正文级证据"],
    ["仅基础数据", "仅基础数据"],
    ["创作者中心数据", "创作者中心数据"],
    ["评论文本", "评论文本"],
    ["历史对照", "历史对照"],
    ["其他信息边界", "其他信息边界"],
  ];
  const boundaryRows = boundary && typeof boundary === "object"
    ? boundaryFields
        .filter(([key]) => String(boundary[key] || "").trim())
        .map(([key, label]) => `<p><span>${label}</span>${escapeHtml(boundary[key])}</p>`)
        .join("")
    : "";
  const boundaryBody = boundaryRows
    ? `<details class="own-basis"><summary>展开查看本轮证据范围</summary><div class="own-basis-body">${boundaryRows}</div></details>`
    : v2FacetNote("旧版诊断没有记录本轮依据与边界。");
  const boundaryCard = v2FacetCard("is-value", "route", "本轮依据与边界", boundaryBody);

  const drawerBody = `<div class="v2-drawer-sections">
      <section class="v2-sub"><h3>完整诊断</h3><div class="markdown-body">${renderMarkdown(source, { documentPath: model.analysis.detail?.path || "" })}</div></section>
    </div>`;

  return `<section class="v2-block" id="v2-worth">
    ${head}
    <div class="v2-facets">
      ${posCard}
      ${stageCard}
      ${targetCard}
      ${issueCard}
      ${boundaryCard}
    </div>
    ${analysisDrawer(model, drawerBody)}
  </section>`;
}

function renderV2Worth(model) {
  const source = model.analysisContent.detail ?? model.analysisContent.overview;
  const worthHead = v2Head("insight", "账号洞察", "他是谁、做什么、服务谁，以及值不值得研究。", `<button class="v2-more" type="button" data-account-action="open-analysis">查看完整分析<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M5 12h13M12.5 6.5 18.5 12l-6 5.5"/></svg></button>`);
  if (source === undefined) return v2Loading("正在读取账号分析");
  if (!source) {
    return `<section class="v2-block" id="v2-worth">${worthHead}${v2Missing("这个账号还没有账号分析文档，无法给出判断。")}</section>`;
  }

  if (model.kind === "own") {
    const own = ownInsightData(model);
    if (own) return renderOwnWorth(model, own);
  }

  const insight = insightData(model);
  if (insight) return renderV2WorthFromSummary(model, insight);

  const blocks = splitDetailSections(source);
  const modeLayer = sectionByTitle(blocks, "模式层");
  // 账号定位：模式层开头那句话，照原文，只去掉「一句话概括：」这类行文前缀
  const summary = v2CleanLead(firstPlainParagraph(sectionText(modeLayer)) || firstPlainParagraph(source));
  const kvOf = (title) => markdownKeyValues(sectionText(sectionByTitle(blocks, title)));

  const productKv = kvOf("2. 产品服务体系");
  const chain = v2ChainNodes(productKv["产品层级"]);
  const personaKv = kvOf("2. 目标用户画像");
  const layers = v2PersonaLayers(personaKv["用户分层"]);
  const toneKv = kvOf("4. 人设风格");
  const diffKv = kvOf("5. 差异化来源");
  const valueJudge = kvOf("3. 价值主张")["判断"] || "";

  // 01 综合结论
  const posCard = v2FacetCard("is-pos", "pos", "综合结论",
    `<div class="v2-pos-body">
      <p class="v2-facet-judge is-lead">${escapeHtml(summary || "分析文档还没写出「他做什么」这一段。")}</p>
      <span class="v2-pos-fig" aria-hidden="true">${V2_POS_FIG}</span>
    </div>`);

  // 03 用户画像：一句判断 + 分层标签
  const personaBody = `<div class="v2-persona-top">
      <span class="v2-persona-fig" aria-hidden="true">${V2_PERSONA_FIG}</span>
      ${personaKv["判断"] ? `<p class="v2-facet-judge">${escapeHtml(personaKv["判断"])}</p>` : v2FacetNote("分析里还没写出目标用户这一段。")}
    </div>
    ${layers.length ? (layers.every((item) => item.name.length <= 8)
      ? `<div class="v2-tags2">${layers.map((item) => `<span>${escapeHtml(item.name)}</span>`).join("")}</div>`
      : `<div class="v2-layers">
      <p class="v2-layers-cap">用户分层</p>
      <ul>${layers.map((item) => `<li><b>${escapeHtml(item.name)}</b>${item.note ? `<span>${escapeHtml(item.note)}</span>` : ""}</li>`).join("")}</ul>
    </div>`) : ""}`;
  const personaCard = v2FacetCard("is-persona", "persona", "用户画像", personaBody);

  // 04 产品服务体系：层级名 + 具体产品 + 价格（没有就是未标价）
  const rows = chain.map((node) => {
    const price = v2NodePrice(node);
    return `<div class="v2-prow">
      <span class="v2-pname"><b>${escapeHtml(node.name)}</b>${node.sub ? `<i>${escapeHtml(node.sub)}</i>` : ""}</span>
      ${price ? `<span class="v2-price">${price}</span>` : `<span class="v2-price is-empty">未标价</span>`}
    </div>`;
  }).join("");
  const gradientNote = String(productKv["价格梯度"] || "").replace(/[`*]/g, "").split("。")[0].trim();
  const productFoot = chain.length && /未标价/.test(rows) && gradientNote
    ? `<p class="v2-price-note">价格梯度 · ${escapeHtml(gradientNote)}</p>` : "";
  const productCard = v2FacetCard("is-product", "product", "产品服务体系",
    chain.length ? `<div class="v2-plist">${rows}</div>${productFoot}` : v2FacetNote("分析里没写出「产品层级」，列不出产品。"));

  // 05 价值主张：只留一句话
  const valueCard = v2FacetCard("is-value", "value", "价值主张", `
    ${valueJudge ? `<p class="v2-value-quote">${escapeHtml(valueJudge)}</p>` : v2FacetNote("分析里还没写出价值主张。")}
    ${valueJudge ? `<div class="v2-value-fig" aria-hidden="true">
      <svg viewBox="0 0 196 150" fill="none">
        <defs>
          <linearGradient id="v2spark" x1="0" y1="0" x2="1" y2="1">
            <stop offset="0%" stop-color="#E2E5E8"/>
            <stop offset="100%" stop-color="#E2E5E8"/>
          </linearGradient>
          <radialGradient id="v2sparkGlow" cx="0.5" cy="0.5" r="0.5">
            <stop offset="0%" stop-color="#E2E5E8" stop-opacity=".6"/>
            <stop offset="100%" stop-color="#E2E5E8" stop-opacity="0"/>
          </radialGradient>
        </defs>
        <circle cx="100" cy="74" r="62" fill="url(#v2sparkGlow)"/>
        <path d="M98 12l9 21.4L128.6 42 107 51l-9 21.4L89 51 67.4 42 89 33.4 98 12z" fill="url(#v2spark)"/>
        <path d="M152 74l5.4 12.8L170 92l-12.6 5.2L152 110l-5.4-12.8L134 92l12.6-5.2L152 74z" fill="url(#v2spark)" opacity=".55"/>
        <path d="M48 92l3.8 9.2L61 105l-9.2 3.8L48 118l-3.8-9.2L35 105l9.2-3.8L48 92z" fill="url(#v2spark)" opacity=".4"/>
        <circle cx="172" cy="40" r="2" fill="#E2E5E8" opacity=".8"/>
        <circle cx="28" cy="58" r="1.8" fill="#E2E5E8" opacity=".9"/>
      </svg>
    </div>` : ""}`);

  // 06 人设风格：一句判断 + 姿态 / 形成方式
  const toneFacts = [
    ["沟通姿态", toneKv["沟通姿态"]],
    ["人设形成", toneKv["人设形成方式"]],
  ].filter(([, v]) => v).map(([k, v]) => `<div class="v2-fact"><dt>${escapeHtml(k)}</dt><dd>${escapeHtml(v)}</dd></div>`).join("");
  // 刻度尺是固定装饰位（四档位置写死在 CSS 里），只有判断和沟通姿态是文档里的字
  const SPECS = [
    ["姿态", "俯视讲师", "同行演示", "is-a"],
    ["语气", "绕弯", "直给", "is-b"],
    ["情绪", "卖焦虑", "不卖", "is-c"],
    ["距离", "远", "近", "is-d"],
  ].map(([k, l, r, cls]) => `<div class="v2-spec ${cls}"><span class="v2-sk">${k}</span><span class="v2-se is-l">${l}</span><div class="v2-sbar"><i></i></div><span class="v2-se">${r}</span></div>`).join("");
  const toneCard = v2FacetCard("is-tone", "tone", "人设风格",
    `${toneKv["判断"] ? `<p class="v2-facet-judge">${escapeHtml(toneKv["判断"])}</p>` : v2FacetNote("分析里还没写出人设这一段。")}
     <div class="v2-specs">${SPECS}</div>
     ${toneKv["沟通姿态"] ? `<p class="v2-posture"><i>沟通姿态</i>${escapeHtml(toneKv["沟通姿态"])}</p>` : ""}`);

  // 差异类型只取主句（文档常写成「A 为主，B 为辅；不是…」），药丸放不下整段
  const diffType = String(diffKv["差异类型"] || "").replace(/[。；]$/, "").split("；")[0].trim();

  // 07 差异化：一句判断 + 差异类型
  const diffCard = v2FacetCard("is-diff", "diff", "差异化",
    `${diffKv["判断"] ? `<p class="v2-facet-judge">${escapeHtml(diffKv["判断"])}</p>` : v2FacetNote("分析里还没写出差异化这一段。")}
     <div class="v2-cmp">
       <div class="v2-cmp-row"><span class="v2-cmp-k">同类账号的普遍做法</span><div class="v2-cmp-track"><i class="is-wide"></i></div><span class="v2-cmp-tag is-gray">同类多数</span></div>
       <div class="v2-cmp-row"><span class="v2-cmp-k">这个账号的差异来源</span><div class="v2-cmp-track"><i class="is-narrow"></i></div><span class="v2-cmp-tag is-lime">差异点</span></div>
     </div>
     ${diffType ? `<div class="v2-diff-type"><span>差异类型</span><b>${escapeHtml(diffType)}</b></div>` : ""}`);

  const detailSections = [
    ["判断依据", ["1. 账号定位", "2. 目标用户画像", "3. 价值主张", "4. 人设风格", "5. 差异化来源"]],
    ["成立逻辑与依赖", ["6. 成立逻辑", "7. 关键依赖", "8. 待确认点"]],
    ["用户与商业", ["1. 目标付费用户", "2. 产品服务体系", "3. 转化路径", "4. 商业模式摘要"]],
    ["内容侧依据", ["1. 核心议题", "4. 用户吸引机制", "5. 信任建立机制"]],
  ].map(([label, titles]) => {
    const md = titles.map((title) => {
      const block = sectionByTitle(blocks, title);
      return block ? `#### ${block.title}\n\n${sectionText(block)}` : "";
    }).filter(Boolean).join("\n\n");
    if (!md) return "";
    return `<section class="v2-sub"><h3>${escapeHtml(label)}</h3><div class="markdown-body">${renderMarkdown(md, { documentPath: model.analysis.detail?.path || "" })}</div></section>`;
  }).join("");

  const drawerBody = `<div class="v2-drawer-sections">
      ${detailSections || v2Missing("分析文档缺少这几节内容。")}
      <section class="v2-sub"><h3>账号表现</h3>${renderCharts(model)}</section>
      ${renderTrendBlock(model)}
      <section class="v2-sub">
        <h3>发布规律</h3>
        <div class="rhythm-wrap">${renderPublishingRhythm(model.contents)}</div>
        ${rhythmEvaluation(model.contents) ? `<p class="v2-note">${escapeHtml(rhythmEvaluation(model.contents))}</p>` : ""}
      </section>
    </div>`;

  return `<section class="v2-block" id="v2-worth">
    ${worthHead}
    <p class="v2-legacy-note">这份账号分析是旧结构，页面按旧字段展示；重新生成后会使用新版页面数据区。</p>
    <div class="v2-facets">
      ${posCard}
      ${personaCard}
      ${productCard}
      ${valueCard}
      ${toneCard}
      ${diffCard}
    </div>
    ${analysisDrawer(model, drawerBody)}
  </section>`;
}

/* ---------- 03 内容拆解 ---------- */

/* 「教程型 18（62.1%）、复盘型 5（17.2%）」→ [{label,count,percent}] */
function parsePercentList(text) {
  return String(text || "")
    .split(/[、；;]/)
    .map((chunk) => {
      const hit = /([^\d（(]+?)\s*(\d+)\s*[（(]\s*([\d.]+)\s*%/.exec(chunk);
      if (!hit) return null;
      return { label: hit[1].trim(), count: Number(hit[2]), percent: Number(hit[3]) };
    })
    .filter(Boolean);
}

/* ---------- 新版规律报告（账号内容规律分析.md）取数 ----------
   固定三节：分析范围 / 内容打法（按环节看、按机制看、小结）/ 表现比较（三档）
   取不到的项留空或显示空态，不自己编。 */

/* 「- 纳入内容数：18」→ 18 */
function scopeNumber(text, label) {
  const hit = new RegExp(`${label}\\s*[：:]\\s*(\\d+)`).exec(String(text || ""));
  return hit ? Number(hit[1]) : 0;
}

/* 环节表：环节 / 他习惯怎么做 / 具体分布 */
function loopStages(pBlocks) {
  return markdownTableRows(sectionText(sectionByTitle(pBlocks, "按环节看")))
    .map((cells) => ({ stage: cells[0] || "", habit: cells[1] || "", dist: parsePercentList(cells[2] || "") }))
    .filter((row) => row.stage);
}

/* 机制表：机制 / 定义 / 观察到的事实 / 作用解读 */
function loopMechanisms(pBlocks) {
  return markdownTableRows(sectionText(sectionByTitle(pBlocks, "按机制看")))
    .map((cells) => ({ name: cells[0] || "", definition: cells[1] || "", facts: cells[2] || "", reading: cells[3] || "" }))
    .filter((row) => row.name);
}

/* 「9（50%）」→ {count, percent}；只有条数没有百分比时 percent 记 0 */
function countPercent(text) {
  const hit = /(\d+)\s*[（(]\s*([\d.]+)\s*%/.exec(String(text || ""));
  if (hit) return { count: Number(hit[1]), percent: Number(hit[2]) };
  const num = /(\d+)/.exec(String(text || ""));
  return { count: num ? Number(num[1]) : 0, percent: 0 };
}

/* 三档表：标签 / 标签类型 / 定义 / 高档 / 中档 / 低档 / 结论 */
function tierRows(pBlocks, title) {
  return markdownTableRows(sectionText(sectionByTitle(pBlocks, title)))
    .map((cells) => ({
      tag: cells[0] || "",
      type: cells[1] || "",
      definition: cells[2] || "",
      high: countPercent(cells[3]),
      mid: countPercent(cells[4]),
      low: countPercent(cells[5]),
      verdict: cells[6] || "",
    }))
    .filter((row) => row.tag);
}

/* 「账号通常使用」表：标签 / 标签类型 / 定义 / 全部（18条） */
function usualRows(pBlocks) {
  return markdownTableRows(sectionText(sectionByTitle(pBlocks, "账号通常使用")))
    .map((cells) => ({ tag: cells[0] || "", type: cells[1] || "", definition: cells[2] || "", all: countPercent(cells[3]) }))
    .filter((row) => row.tag);
}

/* 结论句 → 分组：good 数据好 / bad 数据差 / flat 区分不出 */
function verdictGroup(text) {
  if (/数据好/.test(String(text || ""))) return "good";
  if (/数据差/.test(String(text || ""))) return "bad";
  return "flat";
}

/* 三档明细：内容类别那一节 + 「数据好/差」两张表，按标签去重后合成一份。
   新版报告的分组标题有「先看：选哪类内容」「数据好的内容更常用」「数据差的内容更常用」「三档差不多」。 */
function collectTierRows(pBlocks) {
  const list = [];
  for (const row of [
    ...tierRows(pBlocks, "先看"),
    ...tierRows(pBlocks, "数据好的内容更常用"),
    ...tierRows(pBlocks, "数据差的内容更常用"),
    ...tierRows(pBlocks, "区分不出好坏"),
    ...tierRows(pBlocks, "三档差不多"),
  ]) {
    if (!list.some((item) => item.tag === row.tag)) list.push(row);
  }
  return list;
}

/* 每组多少条：先看正文里的「高档 5 条、中档 10 条、低档 3 条」，没有就读表头「高档（5条）」 */
function tierSizes(pBlocks) {
  const facts = tierFacts(pBlocks);
  if (facts.tiers) return facts.tiers;
  const header = String(sectionText(sectionByTitle(pBlocks, "先看")))
    .split("\n")
    .find((line) => line.trim().startsWith("|")) || "";
  const grab = (label) => {
    const hit = new RegExp(`${label}\\s*[（(]\\s*(\\d+)\\s*条`).exec(header);
    return hit ? Number(hit[1]) : 0;
  };
  return { high: grab("高档"), mid: grab("中档"), low: grab("低档") };
}

/* 报告有的版本只给条数、不给百分比，按组内条数自己换算成使用率 */
function tierRate(tier, groupSize) {
  if (tier.percent || !groupSize) return tier;
  return { count: tier.count, percent: Math.round((tier.count / groupSize) * 100) };
}

function normalizeTierRows(pBlocks) {
  const sizes = tierSizes(pBlocks);
  return collectTierRows(pBlocks).map((row) => ({
    ...row,
    high: tierRate(row.high, sizes.high),
    mid: tierRate(row.mid, sizes.mid),
    low: tierRate(row.low, sizes.low),
  }));
}

/* 每组表格下面那句「该学什么」；同一组在不同版本里可能叫不同小节名 */
function tierTip(pBlocks, titles) {
  for (const title of [].concat(titles)) {
    const hit = /\*\*该学什么\*\*[：:]\s*(.+)/.exec(sectionText(sectionByTitle(pBlocks, title)));
    if (hit) return hit[1].trim();
  }
  return "";
}

/* 表现比较正文：分了哪三档、样本够不够、分档和读表规则 */
function tierFacts(pBlocks) {
  const text = sectionText(sectionByTitle(pBlocks, "表现比较"));
  const hit = /高档\s*(\d+)\s*条\s*[、，,/／]\s*中档\s*(\d+)\s*条\s*[、，,/／]\s*低档\s*(\d+)\s*条/.exec(text);
  return {
    tiers: hit ? { high: Number(hit[1]), mid: Number(hit[2]), low: Number(hit[3]) } : null,
    weak: /样本不足/.test(text),
    rules: String(text || "")
      .split(/\n{2,}/)
      .map((block) => block.trim())
      .filter((block) => block && !block.startsWith("|")),
  };
}

/* 一条「名字 + 占比条 + 百分比」 */
function v2RankList(items, limit = 4) {
  const list = items.slice(0, limit);
  if (!list.length) return "";
  return `<ul class="v2-rank">${list.map((item) => `<li>
      <span class="v2-rank-label">${escapeHtml(item.label)}</span>
      <span class="v2-rank-bar"><i data-w="${item.percent}"></i></span>
      <b>${item.percent}%</b>
    </li>`).join("")}</ul>`;
}

/* 一条堆叠条 + 图例（用来看"内容类别"这种混合构成） */
function v2MixBar(items) {
  if (!items.length) return "";
  const segs = items.map((item) => `<span class="v2-dist-seg" data-w="${item.percent}"></span>`).join("");
  const legend = items.map((item) => `<span class="v2-dist-key"><b>${escapeHtml(item.label)}</b><i>${item.percent}%</i></span>`).join("");
  return `<div class="v2-dist"><div class="v2-dist-bar" aria-hidden="true">${segs}</div><div class="v2-dist-legend">${legend}</div></div>`;
}

/* 标签药丸（不带档位圆点，用于机制做法这种纯计数） */
function v2TagList(items, limit = 4) {
  const list = items.slice(0, limit);
  if (!list.length) return "";
  return `<div class="v2-tagline">${list.map((item) => `<span class="v2-pill">${escapeHtml(item.label)}<b>${item.count}</b></span>`).join("")}</div>`;
}

function v2Field(label, value) {
  if (!value) return "";
  const head = label ? `<i>${escapeHtml(label)}</i>` : "";
  return `<p class="v2-field">${head}${escapeHtml(value)}</p>`;
}

function v2Sublabel(text) {
  return text ? `<p class="v2-sublabel">${escapeHtml(text)}</p>` : "";
}

/* 内容拆解的行：左边固定环节名，右边放这一环节的料，不用四张高度不一的卡 */
function v2LearnRow(title, hint, body, cols = 2) {
  if (!body.trim()) return "";
  return `<section class="v2-learn-row">
    <header><h3>${escapeHtml(title)}</h3><p>${escapeHtml(hint)}</p></header>
    <div class="v2-learn-body is-${cols}">${body}</div>
  </section>`;
}

/* 一句话打法：把「」里的做法用自己的底色标出来 */
function highlightQuote(text) {
  return escapeHtml(text).replace(/「([^」]+)」/g, "「<em>$1</em>」");
}

function learn2Row(label, iconPath, body, extraClass = "") {
  if (!String(body || "").trim()) return "";
  return `<section class="learn2-card">
    <div class="learn2-row-label">
      <div class="learn2-ico"><svg viewBox="0 0 24 24">${iconPath}</svg></div>
      <h2>${escapeHtml(label)}</h2>
    </div>
    <div class="learn2-body ${extraClass}">${body}</div>
  </section>`;
}

/* 内容拆解模块头。空态没有「完整分析」可看，就不放那个按钮——点了没反应等于假按钮 */
function learnHead(withAction = true) {
  return `<header class="learn2-head">
    <div class="learn2-head-ico"><svg viewBox="0 0 24 24" fill="none" stroke="#fff" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"><path d="M6 3.5h8l4 4v13H6z"/><path d="M14 3.5v4h4"/><path d="M9 11.5h6M9 15h6M9 18.5h4"/></svg></div>
    <div><h1>内容拆解</h1><p>他怎么讲、怎么选题、怎么表达，哪些做法有数据支持。</p></div>
    ${withAction ? `<div class="learn2-head-actions"><button class="v2-more" type="button" data-account-action="open-learn">查看完整分析<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M5 12h13M12.5 6.5 18.5 12l-6 5.5"/></svg></button></div>` : ""}
  </header>`;
}

/* 三档结论的三个分组，顺序＝先看好的、再看差的、最后是分不出的。
   sections 是报告里可能出现的小节名（旧版把这组叫「三档差不多」）。 */
const TIER_GROUPS = [
  { key: "good", title: "数据好的内容更常用", sections: ["数据好的内容更常用"] },
  { key: "bad", title: "数据差的内容更常用", sections: ["数据差的内容更常用"] },
  { key: "flat", title: "区分不出好坏", sections: ["区分不出好坏", "三档差不多"] },
];

/* 六个环节各配一个线条图标，和详情页其它卡同一套画法 */
const STAGE_ICONS = {
  内容类别: '<path d="m12 3 8 4-8 4-8-4 8-4z"/><path d="m4 12 8 4 8-4"/><path d="m4 16.6 8 4 8-4"/>',
  标题: '<path d="M5 6.4V4.8h14v1.6"/><path d="M12 4.8v14.6"/><path d="M9.2 19.4h5.6"/>',
  开头: '<rect x="3.4" y="5" width="17.2" height="14" rx="3.2"/><path d="M10.4 9.4 15 12l-4.6 2.6z"/>',
  内容结构: '<path d="M4 6.4h16M4 12h10.5M4 17.6h13"/>',
  表达风格: '<path d="M4.2 5.4h15.6v9.4H9.4L5.2 18v-3.2H4.2z"/>',
  互动设计: '<path d="M4 12h16"/><path d="m7.6 8.2-3.8 3.8 3.8 3.8"/><path d="m16.4 8.2 3.8 3.8-3.8 3.8"/>',
};

/* 一个环节里的做法分布：名称 / 占比条 / 「条数 · 百分比」 */
function stageDist(dist) {
  if (!dist.length) return "";
  return `<ul class="learn2-stages">${dist.map((item, index) => `<li${index === 0 ? ' class="is-top"' : ""}>
      <span class="n">${escapeHtml(item.label)}</span>
      <span class="track"><i data-w="${item.percent}"></i></span>
      <span class="v">${item.count} · ${item.percent}%</span>
    </li>`).join("")}</ul>`;
}

/* 详情页每张环节卡只摘前三条做法，其余留给抽屉里的完整版 */
function stageCard(row) {
  const icon = STAGE_ICONS[row.stage] || STAGE_ICONS["内容结构"];
  const top = row.dist.slice(0, 3);
  const rest = row.dist.length - top.length;
  const body = `<p class="learn2-habit">${escapeHtml(row.habit)}</p>${stageDist(top)}${rest > 0 ? `<p class="learn2-rest">另有 ${rest} 种做法，见完整分析</p>` : ""}`;
  return learn2Row(row.stage, icon, body, "learn2-stage");
}

function renderOwnLearn(model) {
  const pattern = model.analysisContent.pattern;
  if (pattern === undefined) return v2Loading("正在读取内容规律分析");
  if (!pattern) {
    return `<section class="v2-block is-plain" id="v2-learn"><div class="learn2-app">${learnHead(false)}${v2Missing("这个账号还没有内容规律分析，暂时拆不了内容。")}</div></section>`;
  }
  return `<section class="v2-block is-plain" id="v2-learn">
    <div class="learn2-app">
      ${learnHead()}
      <div class="markdown-body own-report-body">${renderMarkdown(pattern, { documentPath: model.analysis.pattern?.path || "" })}</div>
    </div>
    ${learnDrawer(model)}
  </section>`;
}

function renderV2Learn(model) {
  const pattern = model.analysisContent.pattern;
  if (pattern === undefined) return v2Loading("正在读取内容规律分析");
  if (!pattern) {
    return `<section class="v2-block is-plain" id="v2-learn"><div class="learn2-app">${learnHead(false)}${v2Missing("这个账号还没有内容规律分析，暂时拆不了内容。")}</div></section>`;
  }

  const pBlocks = splitMarkdownSections(pattern);
  // 条数口径用报告自己写的「纳入内容数」，不是账号采集到的全部内容数
  const total = scopeNumber(sectionText(sectionByTitle(pBlocks, "分析范围")), "纳入内容数") || model.contents.length;
  const stages = loopStages(pBlocks);

  // 小结的整段偏长，正文里那对「」就是一句话打法，优先只取它
  const summaryLine = firstPlainParagraph(sectionText(sectionByTitle(pBlocks, "小结")));
  const summaryQuote = /「([^」]+)」/.exec(summaryLine);
  const summary = summaryQuote ? summaryQuote[1] : summaryLine;

  const sumPart = summary
    ? `<div class="learn2-sum"><p class="learn2-eyebrow">${total} 条内容 · 一句话打法</p>
        <p>${summaryQuote ? `他的打法是「<em>${escapeHtml(summary)}</em>」` : highlightQuote(summary)}</p>
      </div>`
    : "";

  const stageCards = stages.map(stageCard).join("");

  return `<section class="v2-block is-plain" id="v2-learn">
    <div class="learn2-app">
    ${learnHead()}
    <div class="learn2-grid">
      ${sumPart}
      ${stageCards || v2Missing("这份规律分析里没有「按环节看」，暂时拆不了打法。")}
    </div>
    <div class="learn2-foot">
      <svg viewBox="0 0 24 24"><circle cx="12" cy="12" r="9"/><path d="M12 11v5.5"/><circle cx="12" cy="7.8" r=".9" fill="currentColor" stroke="none"/></svg>
      数据来自 ${total} 条内容的特征识别；一个环节记一条主要做法，个别内容记了两条，所以条数会略多于内容数。完整的三档对比在「查看完整分析」里。
    </div>
    </div>
    ${learnDrawer(model)}
  </section>`;
}

/* ---------- 适配结论（归属账号洞察的二级页） ---------- */

/* 五个维度：右边那句是判断口径，左边是他/我两边的事实 */
const ADAPT_DIMENSIONS = [
  { key: "用户匹配", ask: "他的受众和我的目标用户是否重叠", benchmark: ["2. 目标用户画像"], own: ["2. 用户与需求"] },
  { key: "问题匹配", ask: "他解决的问题是否也是我的用户问题", benchmark: ["2. 目标问题"], own: ["2. 用户与需求"] },
  { key: "内容打法匹配", ask: "他的内容做法是否适合我的表达形式和能力", benchmark: ["1. 核心议题"], own: ["4. 账号与内容"] },
  { key: "成立条件", ask: "我是否具备做成类似账号的必要条件", benchmark: ["7. 关键依赖"], own: ["1. 自身条件"] },
  { key: "商业模式匹配", ask: "他的产品、交付和成交方式是否适合我", benchmark: ["4. 商业模式摘要"], own: ["3. 产品与变现"] },
];

/* 概要分析的字段不带反引号（`- 适配依据：…`），这里兼容两种写法 */
function looseKeyValues(text) {
  const out = {};
  for (const line of String(text || "").split("\n")) {
    const hit = /^\s*[-*]\s*`?([^`：:\n]{1,20})`?\s*[：:]\s*(.+)$/.exec(line);
    if (hit) out[hit[1].trim()] = hit[2].trim();
  }
  return out;
}

function v2AdaptSection(model) {
  const detail = model.analysisContent.detail ?? "";
  const overview = model.analysisContent.overview ?? "";
  const plan = model.analysisContent.plan;
  if (!detail || plan === undefined) return "";
  if (!plan) {
    return `<section class="v2-sub"><h3>适配结论</h3>${v2Missing("工作区还没有「我方自媒体规划」，没法和这个账号做方向适配对照。")}</section>`;
  }

  const dBlocks = splitMarkdownSections(detail);
  const planBlocks = splitMarkdownSections(plan);
  const judgeKv = looseKeyValues(sectionText(sectionByTitle(dBlocks, "适配判断")));
  const detailJudge = (title) => markdownKeyValues(sectionText(sectionByTitle(dBlocks, title)))["判断"] || "";

  // 总体判断：优先用详细分析里的「总体判断」，没有就用概要分析的适配依据
  const overviewKv = looseKeyValues(sectionText(sectionByTitle(splitMarkdownSections(overview), "研究价值")));
  const overall = judgeKv["总体判断"] || overviewKv["适配依据"] || "";

  const hasVerdict = ADAPT_DIMENSIONS.some((dimension) => judgeKv[dimension.key]);
  const rows = ADAPT_DIMENSIONS.map((dimension) => {
    const benchmark = detailJudge(dimension.benchmark[0]);
    const own = planLead(planBlocks, ...dimension.own);
    const verdict = judgeKv[dimension.key] || "";
    if (!benchmark && !own) return "";
    return `<div class="v2-adapt-row">
      <div class="v2-adapt-name"><b>${escapeHtml(dimension.key)}</b><span>${escapeHtml(dimension.ask)}</span></div>
      <p class="v2-adapt-side"><i>对标</i>${escapeHtml(shortQuote(benchmark, 150) || "—")}</p>
      <p class="v2-adapt-side is-own"><i>我方</i>${escapeHtml(shortQuote(own, 150) || "—")}</p>
      ${verdict ? `<span class="v2-adapt-verdict">${escapeHtml(v2FacetLevel(dimension.key, verdict))}</span>` : ""}
    </div>`;
  }).filter(Boolean);

  if (!rows.length) return "";

  const chips = [model.account.fit, model.account.quality].filter(Boolean)
    .map((text) => `<span class="v2-chip-tone">${escapeHtml(text)}</span>`).join("");

  return `<section class="v2-sub">
    <header class="v2-adapt-head"><h3>适配结论</h3>${chips ? `<div class="v2-adapt-chips">${chips}</div>` : ""}</header>
    <div class="v2-adapt-overall">
      <span class="v2-adapt-overall-label">总体判断</span>
      <p>${escapeHtml(overall || "还没有总体适配判断：需要分析流程给出一句结论，不用分项平均。")}</p>
    </div>
    <div class="v2-adapt${hasVerdict ? " has-verdict" : ""}">${rows.join("")}</div>
  </section>`;
}

/* 在某一节正文里按 `#### 小标题` 再切一刀（splitMarkdownSections 只切到三级） */
function subBlockText(text, title) {
  const source = String(text || "");
  const marker = `#### ${title}`;
  const start = source.indexOf(marker);
  if (start < 0) return "";
  const rest = source.slice(start + marker.length);
  const next = rest.search(/\n#{2,4}\s/);
  return (next < 0 ? rest : rest.slice(0, next)).trim();
}

/* 我方规划的取文工具：章节 → 段落 / 首条 */
function planPart(blocks, title, sub) {
  const block = sectionByTitle(blocks, title);
  if (!block) return "";
  const text = sectionText(block);
  return sub ? subBlockText(text, sub) : text;
}

function planLead(blocks, title, sub) {
  const text = planPart(blocks, title, sub);
  // 有的小节整段是项目符号，firstPlainParagraph 会返回空，退回取第一条
  return firstPlainParagraph(text) || firstBulletText(text);
}

function bulletLines(text, limit = 5) {
  return String(text || "")
    .split("\n")
    .map((line) => line.trim())
    .filter((line) => /^[-*]\s+/.test(line))
    .map((line) => line.replace(/^[-*]\s+/, "").replace(/`/g, "").trim())
    .slice(0, limit);
}

/* 从四栏账号方向的边界和验证依据中取红线，逐条判这张对标做法能不能直接照搬 */
function planRedlines(blocks) {
  const current = [
    planPart(blocks, "1. 自身条件", "详细依据与边界"),
    planPart(blocks, "3. 产品与变现", "详细依据与边界"),
    planPart(blocks, "4. 账号与内容", "详细依据与边界"),
  ].join("\n");
  return bulletLines(current, 20);
}

const REDLINE_HINTS = [
  [/课|培训|方法论|训练营/, "不卖方法论、不卖课"],
  [/社群|群|私域/, "不混他人社群"],
  [/投流|投放|付费流量/, "没有验证转化之前不投流"],
  [/涨粉|获客|成交/, "不承诺涨粉、获客、成交"],
  [/定制|代运营|排障/, "不接受无限定制"],
];

function redlineFor(text, redlines) {
  const hit = REDLINE_HINTS.find(([pattern]) => pattern.test(String(text || "")));
  if (!hit) return null;
  return redlines.find((line) => line.includes(hit[1])) || hit[1];
}

/* ---------- 学习启发（挂在内容拆解底部） ---------- */

/* 总体适配判断：详细分析里的「总体判断」优先，没有再退回概要分析的「适配依据」 */
function v2OverallJudgement(model) {
  const detail = model.analysisContent.detail ?? "";
  const overview = model.analysisContent.overview ?? "";
  const judgeKv = looseKeyValues(sectionText(sectionByTitle(splitMarkdownSections(detail), "适配判断")));
  const overviewKv = looseKeyValues(sectionText(sectionByTitle(splitMarkdownSections(overview), "研究价值")));
  return judgeKv["总体判断"] || overviewKv["适配依据"] || "";
}

/* 可直接照做的清单：新版取「值得优先研究」，旧版退回「可以照做的」 */
function learningRows(pBlocks) {
  const rows = markdownTableRows(sectionText(sectionByTitle(pBlocks, "值得优先研究")));
  return rows.length ? rows : markdownTableRows(sectionText(sectionByTitle(pBlocks, "可以照做的")));
}

function borrowBlock(model) {
  const pattern = model.analysisContent.pattern;
  const plan = model.analysisContent.plan ?? "";
  if (!pattern) return v2Missing("规律分析里还没有可参考的学习启发。");

  const pBlocks = splitMarkdownSections(pattern);
  const planBlocks = splitMarkdownSections(plan);
  const redlines = planRedlines(planBlocks);
  const rows = learningRows(pBlocks).slice(0, 4);
  if (!rows.length) return v2Missing("规律分析里还没有可参考的学习启发。");

  return `<div class="v2-borrow">
    <div class="v2-borrow-head"><span>对标做法</span><span>能不能直接照搬</span><span>我方规划依据</span></div>
    ${rows.map((row) => {
      const [tag, type, , note] = row;
      const redline = redlineFor(`${tag}${note}`, redlines);
      const planDoc = type === "互动设计" ? "规划 4. 账号与内容" : "规划 4. 账号与内容";
      return `<div class="v2-borrow-row">
        <div><b>${escapeHtml(tag || "做法")}</b><span>${escapeHtml(type || "")}</span></div>
        <p><span class="v2-verdict-chip ${redline ? "is-block" : "is-ok"}">${redline ? "不照搬" : "可照搬"}</span></p>
        <p>${redline ? `红线：${escapeHtml(shortQuote(redline, 40))}` : escapeHtml(planDoc)}</p>
      </div>`;
    }).join("")}
    <p class="v2-borrow-foot">这里只判「能不能照搬」：命中我方账号方向中明确的边界就不照搬，其余给可照搬。具体怎么改成我方口径，要结合规划逐条改写。</p>
  </div>`;
}

/* ---------- 04 行动建议 ---------- */

/* 代表作品：一条高表现 + 一条常规/近期，避免两张都是爆款。
   我的账号只取公开面（works_contents），对标账号两者同源。 */
function representativeWorks(model) {
  const works = [...(model.works_contents || model.contents)].filter((item) => item.id);
  if (!works.length) return [];
  const byLikes = [...works].sort(
    (left, right) => (numericValue(right.metrics?.likes) || 0) - (numericValue(left.metrics?.likes) || 0)
  );
  const top = byLikes[0];
  const rest = works.filter((item) => item.id !== top.id);
  const recent = [...rest].sort(
    (left, right) => dateTimestamp(right.published_at) - dateTimestamp(left.published_at)
  )[0];
  return [top, recent].filter(Boolean);
}

/* 行动建议（照 1:1 稿子）：适配结论一张通栏卡 + 三张行动卡，每张带参考作品 */
function actionRefWork(model, work, tone, label) {
  if (!work) return "";
  const cover = work.cover_path
    ? `<img src="${rawUrl(work.cover_path)}" alt="" loading="lazy">`
    : `<span class="work-fallback">${platformIcon(platformKey(work.platform))}</span>`;
  const likes = numericValue(work.metrics?.likes);
  const title = work.title || "未命名";
  // 对标内容认 key（平台ID::内容ID），传 id 会「未找到该内容」
  const contentId = model.kind === "benchmark" ? (work.key || work.id) : work.id;
  const href = contentId ? contentUrl(model.kind, contentId) : "";
  const body = `<span class="act2-ref-cover">${cover}</span>
    <span class="act2-ref-body">
      <span class="act2-ref-tag is-${tone}"><i></i>${escapeHtml(label)}</span>
      <span class="act2-ref-t">${escapeHtml(shortQuote(title, 34))}</span>
      <span class="act2-ref-n"><b>${likes === null ? "—" : formatNumber(likes)}</b> 赞</span>
    </span>`;
  return href
    ? `<a class="act2-ref is-${tone}" href="${href}" data-open-content="${escapeAttribute(model.kind)}" data-content-id="${escapeAttribute(contentId)}">${body}</a>`
    : `<span class="act2-ref is-${tone}">${body}</span>`;
}

function actionCard(model, row, index, checks, works) {
  const [tag, , definition, note] = row;
  const used = /(\d+)\s*条(?:内容)?用过/.exec(note || "");
  const high = /高档\s*(\d+)/.exec(note || "");
  const low = /低档\s*(\d+)/.exec(note || "");
  const action = /可以直接照做[：:]\s*([\s\S]+)$/.exec(note || "");
  const how = action ? action[1].trim() : definition || "";
  const check = checks[index % Math.max(checks.length, 1)] || "发布后看同样的口径：点开率、完播率、评论里自取资料的人数。";
  const sigParts = [
    used ? `${used[1]} 条用过` : "",
    high ? `<b class="is-hi">高档 ${high[1]}</b>` : "",
    low ? `<b class="is-lo">低档 ${low[1]}</b>` : "",
  ].filter(Boolean);

  return `<section class="act2-card is-act">
    <div class="act2-head">
      <span class="act2-no">${String(index + 1).padStart(2, "0")}</span>
      <h2>${escapeHtml(tag || "可借鉴做法")}</h2>
    </div>
    ${sigParts.length ? `<p class="act2-sig">${sigParts.join('<span>·</span>')}</p>` : ""}
    ${works[0] ? `<div class="act2-refs is-lead">
      <div class="act2-refs-h">参考作品</div>
      ${actionRefWork(model, works[0], "good", "参考作品")}
    </div>` : ""}
    <dl class="act2-field"><dt>具体怎么做</dt><dd>${escapeHtml(how)}</dd></dl>
    <dl class="act2-field"><dt>发布后看什么</dt><dd>${escapeHtml(check)}</dd></dl>
  </section>`;
}

function renderOwnActionList(model) {
  const head = `<header class="learn2-head">
    <div class="learn2-head-ico"><svg viewBox="0 0 24 24" fill="none" stroke="#fff" stroke-width="2.1" stroke-linecap="round" stroke-linejoin="round"><path d="M5 12.5l4.2 4.2L19 7"/></svg></div>
    <div><h1>行动建议</h1><p>现在先做什么、具体怎么做、做完看什么。</p></div>
  </header>`;
  const advice = model.analysisContent.advice;
  if (advice === undefined) {
    return `<section class="v2-block is-plain" id="v2-action"><div class="learn2-app">${head}${v2Loading("正在读取行动清单")}</div></section>`;
  }
  if (!advice) {
    return `<section class="v2-block is-plain" id="v2-action"><div class="learn2-app">${head}${v2Missing("这个账号还没有生成改进行动清单。")}</div></section>`;
  }
  const data = parseActionListSummary(advice);
  if (!data) {
    return `<section class="v2-block is-plain" id="v2-action"><div class="learn2-app">${head}<div class="markdown-body">${renderMarkdown(advice, { documentPath: model.analysis.advice?.path || "" })}</div></div></section>`;
  }
  const actions = Array.isArray(data["行动"]) ? data["行动"] : [];
  const cards = actions.map((item, index) => {
    const steps = Array.isArray(item["具体怎么做"]) ? item["具体怎么做"] : [];
    return `<section class="act2-card">
      <div class="act2-head">
        <div class="act2-ico"><svg viewBox="0 0 24 24"><circle cx="12" cy="12" r="8.4"/><path d="M8.2 12.4l2.6 2.6 5-5.2"/></svg></div>
        <h2>${escapeHtml(item["动作"] || `动作 ${index + 1}`)}</h2>
      </div>
      ${item["方向"] ? `<div class="act2-dir"><span>${escapeHtml(item["方向"])}</span></div>` : ""}
      <div class="act2-verdict"><p>${escapeHtml(item["对应问题"] || "")}</p></div>
      ${steps.length ? `<ul class="act2-reasons">${steps.map((step) => `<li>${renderInlineLinks(step)}</li>`).join("")}</ul>` : ""}
      <dl class="act2-overlaps">
        <div class="act2-ov"><dt>为什么优先</dt><dd>${escapeHtml(item["优先原因"] || "—")}</dd></div>
        <div class="act2-ov"><dt>参考做法</dt><dd>${renderInlineLinks(item["参考做法"] || "无")}</dd></div>
        <div class="act2-ov"><dt>验证指标</dt><dd>${escapeHtml(item["验证指标"] || "—")}</dd></div>
      </dl>
    </section>`;
  }).join("");
  return `<section class="v2-block is-plain" id="v2-action">
    <div class="learn2-app">
      ${head}
      <div class="own-action-grid">${cards || v2Missing("当前没有需要优先执行的动作，按现有方向稳定发布并观察即可。")}</div>
      <div class="learn2-foot">
        <svg viewBox="0 0 24 24"><circle cx="12" cy="12" r="9"/><path d="M12 11v5.5"/><circle cx="12" cy="7.8" r=".9" fill="currentColor" stroke="none"/></svg>
        动作来自《账号改进行动清单》；按当前重要程度排序，最多 3 条。
      </div>
    </div>
  </section>`;
}

function renderV2Action(model) {
  const pattern = model.analysisContent.pattern;
  const detail = model.analysisContent.detail ?? "";
  const plan = model.analysisContent.plan ?? "";
  const head = `<header class="learn2-head">
    <div class="learn2-head-ico"><svg viewBox="0 0 24 24" fill="none" stroke="#fff" stroke-width="2.1" stroke-linecap="round" stroke-linejoin="round"><path d="M5 12.5l4.2 4.2L19 7"/></svg></div>
    <div><h1>行动建议</h1><p>哪些可以借鉴、需要什么条件、下一步测试什么。</p></div>
  </header>`;

  const advice = parseAdviceSummary(model.analysisContent.advice || "");
  if (advice) return renderV2ActionFromAdvice(model, advice, head);

  if (!pattern) {
    return `<section class="v2-block is-plain" id="v2-action"><div class="learn2-app">${head}${v2Missing("规律分析还没生成，暂时给不出行动建议。")}</div></section>`;
  }

  const pBlocks = splitMarkdownSections(pattern);
  const dBlocks = splitMarkdownSections(detail);
  const planBlocks = splitMarkdownSections(plan);
  const overall = v2OverallJudgement(model);
  const judge = (title) => markdownKeyValues(sectionText(sectionByTitle(dBlocks, title)))["判断"] || "";

  const overlaps = [
    ["受众", judge("2. 目标用户画像")],
    ["卖点", judge("3. 价值主张")],
    ["承接", judge("2. 产品服务体系") || judge("4. 商业模式摘要")],
  ].filter(([, text]) => text)
    .map(([label, text]) => `<div class="act2-ov"><dt>${label}</dt><dd>${escapeHtml(shortQuote(text, 52))}</dd></div>`)
    .join("");

  // 验收口径：优先使用产品与变现、账号与内容中的验证边界，没有就用通用口径
  const verifyText = planPart(planBlocks, "3. 产品与变现", "详细依据与边界") || planPart(planBlocks, "4. 账号与内容", "详细依据与边界");
  const checks = bulletLines(verifyText, 6).filter((line) => /跑通|反馈|购买|需求|使用|复购|咨询/.test(line)).slice(0, 3);

  const byLikes = [...model.contents]
    .filter((item) => item.id)
    .sort((left, right) => (numericValue(right.metrics?.likes) || 0) - (numericValue(left.metrics?.likes) || 0));
  const pickRefs = (index) => {
    if (byLikes.length < 3) return [];
    const high = byLikes[index % byLikes.length];
    const low = byLikes[byLikes.length - 1 - (index % byLikes.length)];
    return high && low && high !== low ? [high, low] : [];
  };

  const rows = learningRows(pBlocks).slice(0, 3);
  const cards = rows.map((row, index) => actionCard(model, row, index, checks, pickRefs(index))).join("");

  return `<section class="v2-block is-plain" id="v2-action">
    <div class="learn2-app">
      ${head}
      <div class="act2-grid">
        <section class="act2-card is-full">
          <div class="act2-head">
            <div class="act2-ico"><svg viewBox="0 0 24 24"><circle cx="12" cy="12" r="8.4"/><path d="M8.2 12.4l2.6 2.6 5-5.2"/></svg></div>
            <h2>适配结论</h2>
          </div>
          <div class="act2-verdict">
            ${model.account.fit ? `<span class="act2-fit">${escapeHtml(model.account.fit)}</span>` : ""}
            <p>${escapeHtml(overall || "还没有总体适配判断，需要分析流程给出一句结论。")}</p>
          </div>
          ${overlaps ? `<dl class="act2-overlaps">${overlaps}</dl>` : ""}
        </section>
        ${cards || v2Missing("规律分析里还没有可借鉴的做法。")}
      </div>
      <div class="learn2-foot">
        <svg viewBox="0 0 24 24"><circle cx="12" cy="12" r="9"/><path d="M12 11v5.5"/><circle cx="12" cy="7.8" r=".9" fill="currentColor" stroke="none"/></svg>
        适配结论取自概要分析的适配依据；动作来自内容规律分析「可以照做」的做法，参考作品取同账号最高赞与最低赞各一条。
      </div>
    </div>
  </section>`;
}


/* 设计稿的环节图标：内容类别 / 标题 / 开头 / 内容结构 / 表达风格 / 互动设计 */
const OWN_LEARN_ICONS = {
  内容类别: '<path d="m12 3 8 4-8 4-8-4 8-4z"/><path d="m4 12 8 4 8-4"/><path d="m4 16.6 8 4 8-4"/>',
  标题: '<path d="M5 6.4V4.8h14v1.6"/><path d="M12 4.8v14.6"/><path d="M9.2 19.4h5.6"/>',
  开头: '<rect x="3.4" y="5" width="17.2" height="14" rx="3.2"/><path d="M10.4 9.4 15 12l-4.6 2.6z"/>',
  内容结构: '<path d="M4 6.4h16M4 12h10.5M4 17.6h13"/>',
  表达风格: '<path d="M4.2 5.4h15.6v9.4H9.4L5.2 18v-3.2H4.2z"/>',
  互动设计: '<path d="M4 12h16"/><path d="m7.6 8.2-3.8 3.8 3.8 3.8"/><path d="m16.4 8.2 3.8 3.8-3.8 3.8"/>',
};

const OWN_XHS_METRICS = ["曝光数", "封面点击率", "平均观看时长", "视频完播率"];
const OWN_DY_METRICS = ["5秒完播率", "2秒跳出率", "封面点击率", "平均播放时长"];
const OWN_XHS_RADAR = ["观看数", "涨粉数", "主页访客数", "发布数", "互动数"];
const OWN_DY_RADAR = ["播放量", "完播率", "粉丝净增", "作品数", "互动率"];

/* 我的账号与对标账号共用同一套模块头 v2Head（黑底方图标 + 20/600 标题 + 14 灰说明 + 右上黑药丸），
   不再另起一套「01 / 02」编号头，避免两个详情页长得像两个产品。 */

function ownTimestampText(value) {
  const raw = String(value || "").trim();
  if (!raw) return "—";
  const clean = raw.replace(/\s*[+-]\d{4}$/, "").replace(" ", "T");
  const timestamp = Date.parse(clean);
  if (!Number.isFinite(timestamp)) return escapeHtml(raw.replace(/\s*[+-]\d{4}$/, ""));
  const date = new Date(timestamp);
  const pad = (number) => String(number).padStart(2, "0");
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())} ${pad(date.getHours())}:${pad(date.getMinutes())}`;
}


function ownDiagnosisDate(model) {
  // 文件名里带日期（2026年10月28日账号详细诊断.md），优先用它；
  // 文档标题是「自我账号详细诊断」，不带日期，最后才退回文件修改时间。
  const fileName = String(model.analysis.detail?.path || "").split("/").pop() || "";
  const title = String(model.analysis.detail?.title || "");
  const hit = /(\d{4})年(\d{1,2})月(\d{1,2})日/.exec(fileName) || /(\d{4})年(\d{1,2})月(\d{1,2})日/.exec(title);
  if (hit) return `${hit[1]}-${hit[2].padStart(2, "0")}-${hit[3].padStart(2, "0")}`;
  const docDate = model.analysis.detail?.modified_at ? ownTimestampText(model.analysis.detail.modified_at) : "";
  return docDate ? docDate.slice(0, 10) : "";
}

function ownTagText(value) {
  return String(value || "").replace(/（[^）]*）/g, "").replace(/[。；;]+$/, "").trim();
}

function ownJudgeClass(value) {
  if (/未完成|没做完|没完成/.test(String(value || ""))) return "is-warn";
  const tone = ownJudgeTone(value);
  if (tone === "is-warn") return "is-warn";
  if (tone === "is-ok") return "is-ok";
  return "is-neutral";
}

function ownIssueClass(value) {
  const text = String(value || "");
  if (/执行/.test(text)) return "is-warn";
  if (/证据|样本|数据/.test(text)) return "is-info";
  return "is-neutral";
}



// 创作者中心账号概览：只读「账号概览.json」，没有采集的账号返回 null，不补 0。
function ownCreatorData(model) {
  const raw = model.analysisContent.creatorOverview;
  if (typeof raw !== "string" || !raw.trim().startsWith("{")) return null;
  let data = null;
  try {
    data = JSON.parse(raw);
  } catch (_error) {
    return null;
  }
  if (!data || typeof data !== "object") return null;
  const key = platformKey(data["平台"] || model.account.platform);
  const metrics = data["指标"] && typeof data["指标"] === "object" ? data["指标"] : {};
  const supported = key === "xiaohongshu" || key === "douyin";
  const metricNames = key === "douyin" ? OWN_DY_METRICS : key === "xiaohongshu" ? OWN_XHS_METRICS : [];
  const radarNames = key === "douyin" ? OWN_DY_RADAR : key === "xiaohongshu" ? OWN_XHS_RADAR : [];
  const rows = metricNames.map((name) => ownCreatorMetric(name, metrics[name]));
  const radarBase = radarNames.map((name) => ownRadarDim(name, metrics[name]));
  // 同类排名有就用排名画；没有就按本账号五项数值的相对比例画，不因为缺排名放弃雷达。
  const percentileReady = radarBase.length === 5 && radarBase.every((item) => item.pct !== null);
  const positiveMax = Math.max(...radarBase.map((item) => Math.max(item.number || 0, 0)), 0);
  const radar = radarBase.map((item) => ({
    ...item,
    plot: percentileReady
      ? Math.min(Math.max(item.pct, 0), 100)
      : (positiveMax > 0 ? Math.round((Math.max(item.number || 0, 0) / positiveMax) * 100) : 0),
  }));
  return {
    platform: String(data["平台"] || model.account.platform || ""),
    platformKey: key,
    supported,
    status: String(data["状态"] || "未获取"),
    source: String(data["数据来源"] || ""),
    capturedAt: String(data["采集时间"] || ""),
    period: String(metrics["统计周期"] || ""),
    metrics: rows,
    radar,
    radarMode: percentileReady ? "percentile" : "relative",
    hasRadar: radar.length === 5 && (percentileReady || positiveMax > 0),
    note: String(data["备注"] || ""),
    raw: metrics,
  };
}

function ownCreatorMetric(label, raw) {
  const text = String(raw ?? "").trim();
  if (!text) return { label, value: "—", unit: "", delta: "", tone: "missing", missing: true };
  const deltaHit = /环比\s*[+-]\s*[\d.]+%/.exec(text);
  const delta = deltaHit ? deltaHit[0] : "";
  const tone = delta ? (delta.includes("-") ? "down" : "up") : "out";
  const main = text.replace(/（[^）]*）/g, "").trim();
  const match = /^([+\-]?[\d.,]+)\s*(.*)$/.exec(main);
  return {
    label,
    value: match ? match[1] : main || "—",
    unit: match ? match[2] : "",
    delta,
    tone,
    missing: false,
  };
}

function ownRadarNumber(value) {
  const text = String(value || "").replace(/,/g, "");
  const hit = /^([+\-]?[\d.]+)/.exec(text);
  return hit ? Number(hit[1]) : null;
}

function ownRadarDim(name, raw) {
  const text = String(raw ?? "").trim();
  if (!text) return { key: name, value: "—", pct: null, number: null };
  const value = text.replace(/（[^）]*）/g, "").trim() || "—";
  const over = /超过\s*(\d+)\s*%\s*同类/.exec(text);
  const under = /低于\s*(\d+)\s*%\s*同类/.exec(text);
  let pct = null;
  if (over) pct = Number(over[1]);
  else if (under) pct = Math.max(0, 100 - Number(under[1]));
  const number = ownRadarNumber(value);
  if (pct === null && number === 0) pct = 0;
  return { key: name, value, pct, number };
}

function ownRadarBlock(creator) {
  if (!creator || !creator.hasRadar) {
    const reason = creator && creator.supported
      ? "五项指标当前没有可绘制的数值，暂时画不出雷达图。"
      : "该渠道暂不支持创作者中心数据。";
    return `<p class="own-radar-empty">${escapeHtml(reason)}</p>`;
  }
  const dims = creator.radar;
  const CX = 300;
  const CY = 172;
  const R = 118;
  const at = (index, radius) => {
    const angle = (-90 + index * 72) * Math.PI / 180;
    return [CX + radius * Math.cos(angle), CY + radius * Math.sin(angle)];
  };
  const pointsOf = (ratio) => dims.map((_, index) => at(index, R * ratio).map((n) => n.toFixed(1)).join(",")).join(" ");
  const rings = [0.25, 0.5, 0.75, 1].map((ratio) => `<polygon class="own-radar-ring${ratio === 0.5 ? " is-mid" : ""}" points="${pointsOf(ratio)}"/>`).join("");
  const spokes = dims.map((_, index) => {
    const [x, y] = at(index, R);
    return `<line class="own-radar-line" x1="${CX}" y1="${CY}" x2="${x.toFixed(1)}" y2="${y.toFixed(1)}"/>`;
  }).join("");
  const points = dims.map((item, index) => at(index, R * Math.sqrt(Math.min(Math.max(item.plot, 0), 100) / 100)));
  const poly = `<polygon class="own-radar-data" points="${points.map((point) => point.map((n) => n.toFixed(1)).join(",")).join(" ")}"/>`;
  const dots = points.map((point) => `<circle class="own-radar-dot" cx="${point[0].toFixed(1)}" cy="${point[1].toFixed(1)}" r="4"/>`).join("");
  const positions = ["rl-t", "rl-r", "rl-br", "rl-bl", "rl-l"];
  const labels = dims.map((item, index) => {
    const author = creator.platformKey === "douyin";
    let sub = "";
    if (creator.radarMode === "relative") sub = "";
    else if (item.pct === null) sub = "同类排名未采集";
    else if (author) sub = item.pct >= 50 ? `超过 ${item.pct}% 同类作者` : `低于 ${100 - item.pct}% 同类作者`;
    else sub = `超过 ${item.pct}% 同类`;
    return `<div class="own-radar-lab ${positions[index]}"><b>${escapeHtml(item.key)} <i>${escapeHtml(item.value)}</i></b><span>${escapeHtml(sub)}</span></div>`;
  }).join("");
  const note = creator.radarMode === "relative"
    ? `<p class="own-radar-note">同类排名未采集；雷达按本账号五项数值的相对比例绘制，只反映结构，不代表同类水平。</p>`
    : "";
  return `<div class="own-radar">
    <svg class="own-radar-svg" viewBox="0 0 600 340" aria-hidden="true">${rings}${spokes}${poly}${dots}</svg>
    ${labels}
  </div>${note}`;
}

function ownCreatorCard(model) {
  const creator = ownCreatorData(model);
  const head = v2Head(
    "metrics",
    "创作者中心数据",
    creator && creator.period ? `统计周期 ${creator.period} · 近 7 天指标与同类排名` : "近 7 天指标与同类排名",
  );
  // 文件已索引但正文还在读取：先占位，避免先显示「没有采集到」再跳成图表
  if (!creator && model.creatorCenter?.overview?.path && model.analysisContent.creatorOverview === undefined) {
    return `<section class="v2-block" id="v2-creator">${head}<div class="v2-empty"><p>正在读取创作者中心账号概览…</p></div></section>`;
  }
  if (!creator) {
    const key = platformKey(model.account.platform);
    const unsupported = key !== "xiaohongshu" && key !== "douyin";
    const message = unsupported
      ? `${model.account.platform || "该渠道"} 暂不支持创作者中心数据，账号概览只展示账号主表与公开作品。`
      : "本轮没有采集到创作者中心账号概览。重新执行一次账号数据获取后，这里会显示近 7 天指标与同类排名。";
    return `<section class="v2-block" id="v2-creator">${head}<div class="v2-empty"><p>${escapeHtml(message)}</p></div></section>`;
  }
  const metrics = creator.metrics.map((item) => {
    const delta = item.delta
      ? `<span class="own-rm-d ${item.tone}">${item.tone === "down" ? '<svg viewBox="0 0 12 12"><path d="M6 10V2M2.6 5.4 6 2l3.4 3.4"/></svg>' : '<svg viewBox="0 0 12 12"><path d="M6 2v8M2.6 6.6 6 10l3.4-3.4"/></svg>'}${escapeHtml(item.delta)}</span>`
      : `<span class="own-rm-d is-out">${item.missing ? "未采集" : "平台未提供环比"}</span>`;
    return `<div class="own-rm"><span class="own-rm-k">${escapeHtml(item.label)}</span>
      <div class="own-rm-v${item.missing ? " is-na" : ""}">${item.missing ? "—" : escapeHtml(item.value) + (item.unit ? `<em>${escapeHtml(item.unit)}</em>` : "")}</div>
      ${delta}</div>`;
  }).join("");
  const source = [
    creator.source ? `来源：${creator.platform}创作者中心「${creator.source}」` : "",
    creator.capturedAt ? `采集于 ${ownTimestampText(creator.capturedAt)}` : "",
  ].filter(Boolean).join("，");
  return `<section class="v2-block" id="v2-creator">${head}
    <div class="v2-facets"><article class="v2-facet v2o-12">
      <div class="v2o-creator">
        <div class="v2o-radar">${ownRadarBlock(creator)}</div>
        <div class="own-rm-grid v2o-metrics">${metrics}</div>
      </div>
      ${source || creator.note ? `<p class="own-note">${escapeHtml([source, creator.note].filter(Boolean).join("。"))}</p>` : ""}
    </article></div>
  </section>`;
}

/* 01 账号概览：与「对标账号」共用同一个 hero（renderSummary），代表作品就挂在 hero 里；
   创作者中心数据是「我的账号」独有的后台数据，单独成一块放在 hero 下面。 */
function ownOverviewSection(model) {
  return `<section class="v2-block is-plain" id="v2-overview">
    ${renderSummary(model)}
    ${worksDrawer(model)}
    ${transcriptDrawer()}
  </section>`;
}

/* ---------- 02 账号洞察 ---------- */

function ownLegacyBoundary(source) {
  const blocks = splitDetailSections(source);
  const text = sectionText(sectionByTitle(blocks, "本轮依据与边界"));
  const out = {};
  for (const line of text.split("\n")) {
    const hit = /^\s*[-*]\s*([^：:]+)[：:]\s*(.+)$/.exec(line);
    if (hit) out[hit[1].trim()] = hit[2].trim();
  }
  return out;
}

function ownLegacyInsight(source) {
  const blocks = splitDetailSections(source);
  const conclusion = (firstPlainParagraph(sectionText(sectionByTitle(blocks, "当前总判断")))
    || firstPlainParagraph(source)).replace(/\*\*/g, "");
  // 旧诊断是一句叙述，问题类型就夹在里面（「主矛盾属于**执行不足**——不是内容方向错了」）。
  // 截到第一个标点/破折号为止，否则整句会被当成标签塞进模块头、撑破卡片。
  const typeHit = /主矛盾属于\s*\**\s*([^，。；:：—\-*\n]+)/.exec(conclusion);
  const issues = blocks
    .filter((block) => /^问题\s*\d+\s*[：:]/.test(block.title))
    .map((block) => {
      const fields = {};
      for (const line of block.lines) {
        const hit = /^\s*[-*]\s*([^：:]+)[：:]\s*(.+)$/.exec(line);
        if (hit) fields[hit[1].trim()] = hit[2].trim();
      }
      const no = (/\d+/.exec(block.title) || [])[0] || "";
      return {
        no,
        "标题": block.title.replace(/^问题\s*\d+\s*[：:]\s*/, ""),
        "类型": ownTagText(fields["问题类型"]),
        "具体表现": fields["具体表现"] || "",
        "影响": fields["主要影响"] || fields["影响"] || "",
        "依据": fields["判断依据"] || fields["依据"] || "",
      };
    });
  return {
    legacy: true,
    conclusion,
    keywords: [],
    problemType: typeHit ? typeHit[1].trim() : "",
    goals: [],
    stage: null,
    issues,
    boundary: ownLegacyBoundary(source),
  };
}

function ownInsightData(model) {
  const summary = parseSelfDiagnosisSummary(model.analysisContent.detail || "");
  if (!summary) return ownLegacyInsight(model.analysisContent.detail || "");
  return {
    legacy: false,
    conclusion: summary["综合诊断"]?.["文本"] || "",
    keywords: Array.isArray(summary["综合诊断"]?.["重点短语"]) ? summary["综合诊断"]["重点短语"] : [],
    problemType: summary["综合诊断"]?.["问题类型"] || "",
    goals: Array.isArray(summary["目标与实际"]) ? summary["目标与实际"] : [],
    stage: summary["这段时间的变化"] || summary["阶段变化"] || null,
    issues: Array.isArray(summary["关键问题"]) ? summary["关键问题"] : [],
    boundary: summary["本轮依据与边界"] || null,
  };
}

/* 洞察里的每张卡：与对标账号同一套 v2-facet（32 方图标 + 20/600 标题），
   一个子结论一张卡，不再在一张大卡里再套「①②③」编号小标题。 */
function ownFacet(cls, icon, title, body, side = "", sideClass = "v2o-fhead-side") {
  return `<article class="v2-facet ${cls}">
    <header class="v2-facet-head">
      <span class="v2-facet-ico" aria-hidden="true"><svg viewBox="0 0 24 24">${V2_FACET_ICONS[icon] || ""}</svg></span>
      <h3>${escapeHtml(title)}</h3>
      ${side ? `<span class="${sideClass}">${side}</span>` : ""}
    </header>
    ${body}
  </article>`;
}

function ownConclusionBlock(insight) {
  if (!insight.conclusion) return "";
  // 问题类型紧贴「综合诊断」标题（不是卡片另一头），它是这个标题的注解。
  // 重点短语不再抽成标签串：它们本来就是结论里的词，就在正文里高亮，让人看到它出现在哪句话里。
  const type = insight.problemType
    ? `<span class="v2o-tag ${ownIssueClass(insight.problemType)}">${escapeHtml(ownTagText(insight.problemType))}</span>`
    : "";
  const keywords = insight.keywords.filter((keyword) => keyword && keyword !== insight.problemType);
  return ownFacet("is-pos", "pos", "综合诊断",
    `<div class="v2-pos-body">
      <div class="v2o-pos-copy">
        <p class="v2-facet-judge is-lead">${highlightPhrases(insight.conclusion, keywords, "v2-kw")}</p>
      </div>
      <span class="v2-pos-fig" aria-hidden="true">${V2_POS_FIG}</span>
    </div>`, type, "v2o-fhead-side is-near");
}

function ownGoalsBlock(insight) {
  const goals = insight.goals.filter((item) => item && (item["维度"] || item["结论"]));
  if (!goals.length) return "";
  // 原因和建议都带引子标签：原因讲事实，建议讲该做什么。
  // 建议是这一屏唯一要照着执行的话，给它一块浅底，不再和原因一样是灰字小字。
  const rows = goals.map((item) => `<article class="v2o-row">
    <div class="v2o-row-head">
      <b>${escapeHtml(item["维度"] || "")}</b>
      <span class="v2o-tag ${ownJudgeClass(item["结论"])}">${escapeHtml(item["结论"] || "—")}</span>
    </div>
    ${item["原因"] ? `<p class="v2o-why"><i>原因</i><span>${escapeHtml(item["原因"])}</span></p>` : ""}
    ${item["建议"] ? `<p class="v2o-do"><i>建议</i><span>${escapeHtml(item["建议"])}</span></p>` : ""}
  </article>`).join("");
  return ownFacet("v2o-12", "value", "目标与实际", `<div class="v2o-rows">${rows}</div>`);
}

function ownStageBlock(insight) {
  const stage = insight.stage;
  if (!stage || typeof stage !== "object") return "";
  const state = stage["总体"] || stage["状态"] || "";
  const summary = stage["一句话总结"] || "";
  const changes = Array.isArray(stage["变化"]) ? stage["变化"] : [];
  const legacyLists = ["已改善", "仍存在", "新增问题"];
  const hasLegacy = legacyLists.some((key) => Array.isArray(stage[key]) && stage[key].length);
  const lastResult = stage["上次行动结果"] || "";
  if (!state && !summary && !changes.length && !hasLegacy && !lastResult) return "";

  const stateTag = state
    ? `<span class="v2o-tag ${ownJudgeClass(state)}">${escapeHtml(state)}</span>`
    : "";
  const changeRows = changes.map((item) => {
    const verdict = item["结论"]
      ? `<span class="v2o-tag ${stageVerdictClass(item["结论"])}">${escapeHtml(item["结论"])}</span>`
      : "";
    if (item["文案"]) {
      return `<article class="v2o-row">
      <div class="v2o-row-head"><b>${escapeHtml(item["方面"] || "")}</b>${verdict}</div>
      <p>${escapeHtml(item["文案"])}</p>
    </article>`;
    }
    return `<article class="v2o-row">
      <div class="v2o-row-head"><b>${escapeHtml(item["方面"] || "")}</b>${item["哪里变了"] ? `<span class="v2o-row-note">${escapeHtml(item["哪里变了"])}</span>` : ""}</div>
      ${item["证据"] ? `<p>${escapeHtml(item["证据"])}</p>` : ""}
      ${item["说明什么"] ? `<p class="v2o-tip"><i>说明什么</i>${escapeHtml(item["说明什么"])}</p>` : ""}
    </article>`;
  }).join("");
  const legacyCards = hasLegacy
    ? `<div class="v2o-legacy">
        ${legacyLists.map((key) => {
          const items = Array.isArray(stage[key]) ? stage[key] : [];
          if (!items.length) return "";
          return `<div class="v2o-legacy-col"><p class="v2o-cap">${key}</p><ul>${items.map((text) => `<li>${escapeHtml(text)}</li>`).join("")}</ul></div>`;
        }).join("")}
      </div>`
    : "";
  const body = `${summary ? `<p class="v2o-sum">${escapeHtml(summary)}</p>` : ""}
    ${changeRows ? `<div class="v2o-rows">${changeRows}</div>` : ""}
    ${legacyCards}
    ${lastResult ? `<p class="v2o-tip"><i>上次行动结果</i>${escapeHtml(lastResult)}</p>` : ""}`;
  return ownFacet("v2o-12", "tone", "这段时间的变化", body, stateTag, "v2o-fhead-side is-near");
}

function ownIssuesBlock(insight) {
  const issues = insight.issues.filter((item) => item && item["标题"]);
  if (!issues.length) return "";
  // 一条问题一张竖卡：编号 + 标题 + 类型在顶上，具体表现/影响整宽往下排。
  // 类型标签紧贴标题（跟着标题文字走，标题换行它也跟在后面），不推到卡片另一头。
  const cards = issues.map((item, index) => `<article class="v2o-issue-card">
    <div class="v2o-issue-head">
      <span class="v2o-issue-no">${escapeHtml(item["no"] || String(index + 1).padStart(2, "0"))}</span>
      <div class="v2o-issue-title"><b>${escapeHtml(item["标题"])}</b>${
        item["类型"] ? `<span class="v2o-tag ${ownIssueClass(item["类型"])}">${escapeHtml(ownTagText(item["类型"]))}</span>` : ""
      }</div>
    </div>
    <dl class="v2o-def">
      ${item["具体表现"] ? `<dt>具体表现</dt><dd>${escapeHtml(item["具体表现"])}</dd>` : ""}
      ${item["影响"] ? `<dt>实际影响</dt><dd>${escapeHtml(item["影响"])}</dd>` : ""}
    </dl>
  </article>`).join("");
  return ownFacet("v2o-12", "diff", "关键问题", `<div class="v2o-issue-list">${cards}</div>`);
}


function ownInsightSection(model) {
  const source = model.analysisContent.detail;
  const date = ownDiagnosisDate(model);
  const sub = date
    ? `${date} 诊断 · 现在最主要的问题是什么、差距在哪、有没有在变好。`
    : "现在最主要的问题是什么、差距在哪、有没有在变好。";
  const head = v2Head("insight", "账号洞察", sub);
  if (source === undefined) {
    return `<section class="v2-block" id="v2-worth">${head}<div class="v2-empty"><p>正在读取账号详细诊断…</p></div></section>`;
  }
  if (!source) {
    return `<section class="v2-block" id="v2-worth">${head}<div class="v2-empty"><p>这个账号还没有账号详细诊断，暂时无法给出账号洞察。</p></div></section>`;
  }
  const insight = ownInsightData(model);
  const blocks = [
    ownConclusionBlock(insight),
    ownStageBlock(insight),
    ownGoalsBlock(insight),
    ownIssuesBlock(insight),
  ].filter(Boolean);
  const legacyNote = insight.legacy
    ? `<p class="v2o-meta">这份诊断是旧结构，页面只展示能对应上的内容；重新生成诊断后会补全目标与实际、阶段变化与依据边界。</p>`
    : "";
  return `<section class="v2-block" id="v2-worth">${head}
    ${blocks.length ? `<div class="v2-facets">${blocks.join("")}</div>` : `<div class="v2-empty"><p>这份诊断还没有可展示的结论。</p></div>`}
    ${legacyNote}
  </section>`;
}


/* ---------- 03 行动建议 ---------- */

/* 参考做法在页面上的口径：只认站内已有的作品。
   原文形如 `[《标题》](链接) 的做法：……` 或 `《标题》（未公开）的做法：……`，
   标题和链接只用来定位作品，界面不再跳平台外链。 */
function parseActionReference(value) {
  const text = String(value ?? "").trim();
  if (!text || text === "无" || text === "—") return null;
  const linked = /\[([^\]]+)\]\(\s*(https?:\/\/[^\s)]+)\s*\)/.exec(text);
  const quoted = /《([^》]+)》/.exec(text);
  const title = String(linked ? linked[1] : quoted ? quoted[1] : "").replace(/[《》]/g, "").trim();
  const note = text
    .replace(linked ? linked[0] : quoted ? quoted[0] : "", "")
    .replace(/^[的\s，,、：:]+/, "")
    .trim();
  return { title, url: linked ? linked[2] : "", note };
}

/* 作品定位：先按链接里的作品 ID，再按标题。对标内容库优先，其次我方内容。
   两处都找不到就返回 null——参考做法卡留空，不用外链兜底。 */
function findActionReferenceWork(model, reference) {
  if (!reference) return null;
  const pool = [
    ...(Array.isArray(model.benchmark_contents) ? model.benchmark_contents : []),
    ...(Array.isArray(model.contents) ? model.contents : []),
  ].filter((item) => item && (item.external_url || item.title));
  const tokens = String(reference.url || "").match(/[0-9A-Za-z]{12,}/g) || [];
  if (tokens.length) {
    const hit = pool.find((item) => tokens.some((token) => String(item.external_url || "").includes(token)));
    if (hit) return hit;
  }
  const normalize = (text) => String(text || "")
    .replace(/[《》\s"'“”‘’?？!！,，.。:：;；、·—\-_()（）\[\]【】]/g, "")
    .toLowerCase();
  const target = normalize(reference.title);
  if (target.length < 6) return null;
  return pool.find((item) => {
    const own = normalize(item.title);
    return own.length >= 6 && (own === target || own.includes(target) || target.includes(own));
  }) || null;
}

/* 参考做法：一条站内作品卡 + 一句做法说明；作品找不到就只留说明 */
function renderActionReference(model, value) {
  const reference = parseActionReference(value);
  if (!reference) return "";
  const work = findActionReferenceWork(model, reference);
  if (!work && !reference.note) return "";
  return `<section class="v2o-act-sec">
    <h4>参考做法</h4>
    ${work ? `<div class="v2-wcards is-advice v2o-act-ref">${miniWorkCard(work, null, model)}</div>` : ""}
    ${reference.note ? `<p class="v2o-act-ref-note">${escapeHtml(reference.note)}</p>` : ""}
  </section>`;
}

function ownActionSection(model) {
  const advice = model.analysisContent.advice;
  const data = parseActionListSummary(advice || "");
  const actions = data && Array.isArray(data["行动"]) ? data["行动"] : [];
  const head = v2Head("action", "行动建议", "现在先做什么、具体怎么做、做完看什么。");
  if (advice === undefined) {
    return `<section class="v2-block" id="v2-action">${head}<div class="v2-empty"><p>正在读取账号改进行动清单…</p></div></section>`;
  }
  if (!advice) {
    return `<section class="v2-block" id="v2-action">${head}<div class="v2-empty"><p>这个账号还没有改进行动清单。</p></div></section>`;
  }
  if (!actions.length) {
    return `<section class="v2-block" id="v2-action">${head}<div class="v2-facets"><article class="v2-facet v2o-12"><div class="markdown-body">${renderMarkdown(advice, { documentPath: model.analysis.advice?.path || "" })}</div></article></div></section>`;
  }
  const cards = actions.map((item, index) => {
    const steps = Array.isArray(item["具体怎么做"]) ? item["具体怎么做"] : [];
    const meta = [
      ["完成标准", item["完成标准"]],
      ["验证指标", item["验证指标"]],
    ].filter(([, value]) => String(value || "").trim());
    return `<article class="v2-facet v2o-12 v2o-act">
      <header class="v2o-act-top">
        <span class="v2o-act-ico" aria-hidden="true"><svg viewBox="0 0 24 24">${V2_HEAD_ICONS.action}</svg></span>
        <div class="v2o-act-title">
          <h3>${escapeHtml(item["动作"] || `动作 ${index + 1}`)}</h3>
        </div>
        <span class="v2o-issue-no">${String(index + 1).padStart(2, "0")}</span>
      </header>
      ${item["方向"] ? `<section class="v2o-act-sec">
        <h4>方向</h4>
        <p>${escapeHtml(item["方向"])}</p>
      </section>` : ""}
      ${item["对应问题"] ? `<section class="v2o-act-sec">
        <h4>对应问题</h4>
        <p>${escapeHtml(item["对应问题"])}</p>
      </section>` : ""}
      ${steps.length ? `<section class="v2o-act-sec">
        <h4>具体怎么做</h4>
        <ol class="v2o-steps">${steps.map((step) => `<li>${renderInlineLinks(step)}</li>`).join("")}</ol>
      </section>` : ""}
      ${renderActionReference(model, item["参考做法"])}
      ${meta.map(([label, value]) => `<section class="v2o-act-sec">
        <h4>${label}</h4>
        <p>${renderInlineLinks(value)}</p>
      </section>`).join("")}
    </article>`;
  }).join("");
  return `<section class="v2-block" id="v2-action">${head}
    <div class="v2-facets">${cards}</div>
  </section>`;
}

/* ---------- 04 内容拆解 ---------- */

function ownPlaybook(pBlocks) {
  const modern = firstPlainParagraph(sectionText(sectionByTitle(pBlocks, "小结")));
  if (modern) {
    const quote = /「([^」]+)」/.exec(modern);
    return quote ? quote[1] : modern.replace(/^概括来说[，,]\s*/, "");
  }
  const legacy = sectionText(sectionByTitle(pBlocks, "打法画像"));
  const hit = /一句话概括[^：:]*[：:]\s*([^。\n]+)[。]?/.exec(legacy);
  return hit ? hit[1].trim() : "";
}


/* 内容拆解：
   一段一句话打法 → 六个环节（卡）→ 五层机制（行）→ 账号底色（行）→ 和表现的关系（表）。
   组的层级靠 12px 灰组标签区分，不再每一小块都顶一个 16px 标题。
   内容全部来自《账号内容规律分析》，不新造结论。 */
/* 内容拆解的分组标题：按环节看 / 靠什么起作用 / 账号通常使用 /
   数据好的内容更常用 / 数据差的内容更常用 / 区分不出好坏，六个同一层级，不带说明文字 */
function learnGroup(title, body) {
  return `<div class="v2o-group"><p class="v2o-group-title">${escapeHtml(title)}</p>${body}</div>`;
}

function ownLearnSection(model) {
  const pattern = model.analysisContent.pattern;
  const parts = learnReportParts(model);
  const { total, ruleCount, playbook, stages, mechs, usual, tierList, facts } = parts.data;
  const scope = [total ? `${total} 条内容` : "", ruleCount ? `${ruleCount} 条规律` : ""].filter(Boolean).join(" · ");
  const sub = `他怎么讲、怎么选题、怎么表达，哪些做法有数据支持。${scope ? ` ${scope}。` : ""}`;
  const head = v2Head("breakdown", "内容拆解", sub);
  if (pattern === undefined) {
    return `<section class="v2-block" id="v2-learn">${head}<div class="v2-empty"><p>正在读取账号内容规律分析…</p></div></section>`;
  }
  if (!pattern) {
    return `<section class="v2-block" id="v2-learn">${head}<div class="v2-empty"><p>这个账号还没有内容规律分析，暂时拆不了内容。</p></div></section>`;
  }

  // 总纲：一句话打法。稳定底色单独成一组卡片（见下），不挤在这张浅底卡里。
  const summary = playbook ? `<div class="v2o-summary">
    <div class="v2o-sum-row">
      <span class="v2o-sum-cap">一句话打法</span>
      <p class="v2o-sum-text">${escapeHtml(playbook)}</p>
    </div>
  </div>` : "";

  // 每个环节的做法全列，不再截前三条
  const stageCards = stages.map((stage) => {
    const bars = stage.dist.map((item, index) => `<li>
      <span class="v2o-bar-top">
        <span class="v2o-bar-name" title="${escapeAttribute(item.label)}">${escapeHtml(item.label)}</span>
        <span class="v2o-bar-val">${item.count} · ${item.percent}%</span>
      </span>
      <span class="v2o-bar-track"><i class="${index ? "" : "is-top"}" data-w="${Math.min(item.percent, 100)}"></i></span>
    </li>`).join("");
    return `<article class="v2-facet v2o-4">
      <header class="v2-facet-head">
        <span class="v2-facet-ico" aria-hidden="true"><svg viewBox="0 0 24 24">${OWN_LEARN_ICONS[stage.stage] || OWN_LEARN_ICONS["内容结构"]}</svg></span>
        <h3>${escapeHtml(stage.stage)}</h3>
      </header>
      <p class="v2o-habit">${escapeHtml(stage.habit)}</p>
      ${bars ? `<ul class="v2o-bars">${bars}</ul>` : ""}
    </article>`;
  }).join("");

  // 靠什么起作用：一层一张卡，和「完整分析」里那套机制卡同一口径
  const mechCards = mechs.map((row) => `<article class="v2o-mech-card">
      <div class="v2o-mech-card-h"><b>${escapeHtml(row.name)}</b><span>${escapeHtml(row.definition)}</span></div>
      ${row.reading ? `<p class="v2o-mech-card-read"><i>为什么起作用</i>${escapeHtml(row.reading)}</p>` : ""}
      <p class="v2o-mech-card-fact"><i>他具体怎么做</i>${escapeHtml(row.facts || "—")}</p>
    </article>`).join("");

  // 账号通常使用：一张卡，卡内「做法 / 覆盖」两列，和完整分析里同一套表
  const usualBlock = usual.length ? learnGroup("账号通常使用", `<div class="v2o-card"><div class="learn3-table is-usually">
        <div class="learn3-tr is-head"><span>做法</span><span>覆盖</span></div>
        ${usual.map((row) => `<div class="learn3-tr">
          <span class="learn3-td-name"><b>${escapeHtml(row.tag)}</b><i>${escapeHtml(row.type)}</i><p>${escapeHtml(row.definition)}</p></span>
          <b>${row.all.count} 条 · ${row.all.percent}%</b>
        </div>`).join("")}
      </div></div>`) : "";

  // 三档：一组一张卡，卡内是「做法 / 高档 / 中档 / 低档 / 结论」的同一张表
  const tierGroupCards = (group) => {
    const rows = tierList.filter((row) => verdictGroup(row.verdict) === group.key);
    if (!rows.length) {
      return group.key === "good" || group.key === "bad"
        ? learnGroup(group.title, `<p class="v2o-group-empty">本轮没有判定为这一档的做法。</p>`)
        : "";
    }
    return learnGroup(group.title, `<div class="v2o-card"><div class="learn3-table">
        <div class="learn3-tr is-head"><span>做法</span><span>高档</span><span>中档</span><span>低档</span><span>结论</span></div>
        ${rows.map((row) => `<div class="learn3-tr">
          <span class="learn3-td-name"><b>${escapeHtml(row.tag)}</b><i>${escapeHtml(row.type)}</i><p>${escapeHtml(row.definition)}</p></span>
          <b>${row.high.percent}%</b><b>${row.mid.percent}%</b><b>${row.low.percent}%</b>
          <span class="learn3-chip is-${verdictGroup(row.verdict)}">${escapeHtml(verdictShort(row.verdict))}</span>
        </div>`).join("")}
      </div></div>`);
  };

  return `<section class="v2-block" id="v2-learn">${head}
    ${summary}
    ${stageCards ? learnGroup("按环节看", `<div class="v2-facets">${stageCards}</div>`) : ""}
    ${mechCards ? learnGroup("靠什么起作用", `<div class="v2o-cards">${mechCards}</div>`) : ""}
    ${usualBlock}
    ${tierList.length ? TIER_GROUPS.map(tierGroupCards).join("") : ""}
  </section>`;
}



function applyOwnSkin(root) {
  root.querySelectorAll(".own-page [data-w]").forEach((el) => {
    el.style.width = `${el.dataset.w}%`;
  });
}

function renderOwnPage(model) {
  return `<div class="v2-page own-page">
    ${ownOverviewSection(model)}
    ${ownCreatorCard(model)}
    ${ownInsightSection(model)}
    ${ownActionSection(model)}
    ${ownLearnSection(model)}
  </div>`;
}

function applyV2Skin(root) {
  root.querySelectorAll(".v2-tier-seg[data-w], .v2-dist-seg[data-w], .v2-rank-bar i[data-w], .v2-dist-track i[data-w], .learn2-bar-track i[data-w], .learn2-cats-bar span[data-w], .learn2-cat-track i[data-w], .learn2-cmp-track i[data-w], .learn2-stages .track i[data-w], .learn3-dist .track i[data-w]").forEach((el) => {
    el.style.width = `${el.dataset.w}%`;
  });
  // 发布规律的柱子高度和配色走 CSS 变量（CSP 不许行内 style）
  root.querySelectorAll(".bar[data-height]").forEach((el) => {
    el.style.setProperty("--h", el.dataset.height);
  });
  root.querySelectorAll(".bar[data-bar]").forEach((el) => {
    el.style.setProperty("--dot", el.dataset.bar);
  });
  root.querySelectorAll(".legend-item i[data-dot], .learn2-legend i[data-dot]").forEach((el) => {
    el.style.setProperty("--dot", el.dataset.dot);
  });
}

function renderV2(model) {
  // 我的账号走设计稿的新四段结构；对标账号维持原顺序与组件。
  if (model.kind === "own") return renderOwnPage(model);
  return `<div class="v2-page">
    ${renderV2Overview(model)}
    ${renderV2Worth(model)}
    ${renderV2Learn(model)}
    ${renderV2Action(model)}
  </div>`;
}

function renderAccount() {
  const model = state.model;
  if (!model) return;
  markNavActive(model.label === "对标账号档案" ? "benchmark-accounts-v2" : "own-accounts");

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
  elements.view.innerHTML = renderV2(model);
  applyAccountSkin(elements.view);
  applyV2Skin(elements.view);
  applyOwnSkin(elements.view);
  /* 从首页「查看详细诊断」进来时 URL 带 #v2-worth，资料渲染完再滚到「账号洞察」 */
  focusHashTarget();
}

let hashFocusDone = false;

function focusHashTarget() {
  if (hashFocusDone) return;
  const id = String(location.hash || "").replace(/^#/, "");
  if (!id) return;
  hashFocusDone = true;
  /* 账号页是分批渲染的，每次重画都会换掉 DOM，所以每 200ms 重新取一次节点；
     落到位置就停，最多 8 秒，不跟用户抢滚动。 */
  const started = Date.now();
  const timer = window.setInterval(() => {
    const node = document.getElementById(id);
    if (node) node.scrollIntoView({ block: "start", behavior: "auto" });
    const landed = node && Math.abs(node.getBoundingClientRect().top) <= 4;
    if (landed || Date.now() - started > 8000) window.clearInterval(timer);
  }, 200);
}

function fallbackListUrl() {
  // 我的账号回自己的列表页；对标账号回新版列表
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
  // 详细分析和概要分析都读：适配依据只写在概要里
  const entries = [["detail", model.analysis.detail], ["overview", model.analysis.overview]];
  if (model.analysis.pattern?.path) entries.push(["pattern", model.analysis.pattern]);
  if (model.analysis.advice?.path) entries.push(["advice", model.analysis.advice]);
  if (model.analysis.plan?.path) entries.push(["plan", model.analysis.plan]);
  if (state.notesOpen) entries.push(["prior", model.analysis.prior]);
  // 我的账号：创作者中心账号概览 / 内容分析（JSON）也按需读取，没有采集的账号不占位
  if (model.kind === "own" && model.creatorCenter?.overview?.path) entries.push(["creatorOverview", model.creatorCenter.overview]);
  if (model.kind === "own" && model.creatorCenter?.contents?.path) entries.push(["creatorContents", model.creatorCenter.contents]);
  // 没有这份文档的账号要显式标成 null，否则视图会一直停在「正在读取」
  for (const key of ["detail", "overview", "pattern", "advice", "prior", "plan"]) {
    if (model.analysisContent[key] === undefined && !model.analysis?.[key]?.path) {
      model.analysisContent[key] = null;
    }
  }
  if (model.analysisContent.creatorOverview === undefined && !model.creatorCenter?.overview?.path) {
    model.analysisContent.creatorOverview = null;
  }
  if (model.analysisContent.creatorContents === undefined && !model.creatorCenter?.contents?.path) {
    model.analysisContent.creatorContents = null;
  }
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

/* 侧边栏：版本号和当前项高亮都跟列表页一致，切页时 tab 栏不动 */
function syncSidebar(snapshot) {
  const version = elements.workspaceVersion;
  const raw = snapshot?.workspace?.version;
  if (version && raw) {
    const text = String(raw);
    const formatted = /^v/i.test(text) ? text : `v${text}`;
    version.textContent = formatted;
  }
  const activeKey = accountKind === "own" ? "own-accounts" : "benchmark-accounts-v2";
  document.querySelectorAll(".nav-item[data-nav-key]").forEach((item) => {
    item.classList.toggle("is-active", item.dataset.navKey === activeKey);
  });
}

async function loadAccount({ force = false } = {}) {
  const hasPreviousAccount = Boolean(state.model);
  if (!hasPreviousAccount) {
    elements.view.innerHTML = '<div class="content-loading"><span></span><p>正在装配统一账号档案</p></div>';
  }
  try {
    if (location.protocol === "file:") {
      elements.view.innerHTML = emptyState("账号档案需要通过媒灵工作台打开", "account-v2.html 是可编辑模板，不携带具体账号数据。请启动本地服务后，从账号列表进入。");
      return;
    }
    if (!accountId || !["benchmark", "own"].includes(accountKind)) {
      elements.view.innerHTML = emptyState("账号地址无效", "请从媒灵工作台的账号列表进入。");
      return;
    }
    const response = await fetch(force ? "/api/refresh" : "/api/snapshot", { cache: "no-store" });
    if (!response.ok) throw new Error(`snapshot_read_failed:${response.status}`);
    const snapshot = await response.json();
    state.accounts = accountKind === "own" ? (snapshot?.workflows?.self?.own_accounts || []) : [];
    syncSidebar(snapshot);
    const model = getAccountModel(snapshot);
    if (!model) {
      elements.view.innerHTML = emptyState("未找到该账号", accountKind === "own" ? "该账号可能已被移动或删除，请回到工作台刷新后重试。" : "该记录可能已被移动或删除，请返回账号列表刷新后重试。");
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

  const trendButton = event.target.closest("[data-trend-metric]");
  if (trendButton) {
    const reopen = document.querySelector("#v2-analysis")?.classList.contains("is-open");
    state.trendMetric = trendButton.dataset.trendMetric;
    renderAccount();
    if (reopen) openDrawer("#v2-analysis");
    return;
  }
  const accountAction = event.target.closest("[data-account-action]");
  if (accountAction?.dataset.accountAction === "open-transcript") {
    await openTranscript(accountAction.dataset.transcriptKey);
    return;
  }
  const drawerAction = { "open-analysis": "#v2-analysis", "open-works": "#v2-works", "open-learn": "#v2-learn-drawer", "open-action-full": "#v2-action-full" }[accountAction?.dataset.accountAction];
  if (drawerAction) {
    openDrawer(drawerAction);
    return;
  }
  const closeAction = { "close-analysis": "#v2-analysis", "close-works": "#v2-works", "close-learn": "#v2-learn-drawer", "close-transcript": "#v2-transcript", "close-action-full": "#v2-action-full" }[accountAction?.dataset.accountAction];
  if (closeAction) {
    closeDrawer(closeAction);
    return;
  }
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

/* 完整分析抽屉：顶部字段 tab 与滚动联动，切换指标不重渲染整页 */
function anaDrawerBody() {
  return document.querySelector("#v2-analysis .v2-drawer-body");
}

function setAnaTab(field) {
  const body = anaDrawerBody();
  if (!body) return;
  const bar = body.querySelector(".ana-tabs");
  body.querySelectorAll(".ana-tab").forEach((tab) => {
    const on = tab.dataset.field === field;
    tab.classList.toggle("is-active", on);
    if (on && bar && bar.scrollWidth > bar.clientWidth) {
      const left = tab.offsetLeft;
      const right = left + tab.offsetWidth;
      if (left < bar.scrollLeft) bar.scrollLeft = left - 12;
      else if (right > bar.scrollLeft + bar.clientWidth) bar.scrollLeft = right - bar.clientWidth + 12;
    }
  });
}

function updateAnaTabs() {
  const body = anaDrawerBody();
  if (!body) return;
  const bar = body.querySelector(".ana-tabs");
  const cards = [...body.querySelectorAll(".ana-card")];
  if (!bar || !cards.length) return;
  const mark = bar.getBoundingClientRect().bottom + 18;
  let active = cards[0];
  cards.forEach((card) => {
    if (card.getBoundingClientRect().top <= mark) active = card;
  });
  if (body.scrollHeight - body.clientHeight > 8 && body.scrollTop + body.clientHeight >= body.scrollHeight - 4) {
    active = cards[cards.length - 1];
  }
  setAnaTab(active.dataset.field);
}

document.addEventListener("click", (event) => {
  const tab = event.target.closest("#v2-analysis .ana-tab");
  if (tab) {
    event.preventDefault();
    const body = anaDrawerBody();
    const card = body?.querySelector(`#ana-field-${tab.dataset.field}`);
    if (!card) return;
    const bar = body.querySelector(".ana-tabs");
    const top = card.getBoundingClientRect().top - body.getBoundingClientRect().top + body.scrollTop - (bar?.offsetHeight || 0) - 12;
    // 跨度很大时直接跳过去，避免长距离滚动动画拖太久
    body.scrollTo({ top, behavior: Math.abs(top - body.scrollTop) > 1400 ? "auto" : "smooth" });
    setAnaTab(tab.dataset.field);
    return;
  }
  const metric = event.target.closest("#v2-analysis [data-ana-metric]");
  if (metric) {
    const card = metric.closest(".ana-card");
    if (!card) return;
    card.querySelectorAll("[data-ana-metric]").forEach((button) => {
      const on = button === metric;
      button.classList.toggle("is-active", on);
      button.setAttribute("aria-selected", String(on));
    });
    card.querySelectorAll("[data-ana-panel]").forEach((panel) => {
      panel.classList.toggle("is-active", panel.dataset.anaPanel === metric.dataset.anaMetric);
    });
  }
});

document.addEventListener("scroll", (event) => {
  const target = event.target;
  if (target instanceof Element && target.closest("#v2-analysis .v2-drawer-body")) updateAnaTabs();
}, true);

/* 视频文案抽屉：点作品卡时现读正式文案文件 */
async function openTranscript(key) {
  // 行动建议的参考作品来自对标内容库，不在本账号的作品列表里，两处都找
  const content = (state.model?.contents || []).find((item) => (item.key || item.id) === key)
    || (state.model?.benchmark_contents || []).find((item) => (item.key || item.id) === key);
  if (!content) return;
  const title = document.querySelector("#v2-transcript-title");
  const sub = document.querySelector("#v2-transcript-sub");
  const body = document.querySelector("#v2-transcript-body");
  if (!title || !sub || !body) return;
  title.textContent = content.title || "视频文案";
  sub.textContent = [content.account_name, content.platform].filter(Boolean).join(" · ");
  openDrawer("#v2-transcript");
  if (!content.transcript_path) {
    body.innerHTML = '<div class="account-empty-inline">这条内容还没有采集视频文案。运行一次账号详细数据获取后，这里会显示原文。</div>';
    return;
  }
  body.innerHTML = '<div class="content-loading"><span></span><p>正在读取视频文案</p></div>';
  try {
    const file = await fetchDocument(content.transcript_path);
    body.innerHTML = `<article class="markdown-body">${renderMarkdown(file.content, { documentPath: file.path || content.transcript_path })}</article>`;
  } catch (error) {
    body.innerHTML = `<p class="muted">视频文案读取失败：${escapeHtml(error.message)}</p>`;
  }
}

/* 抽屉：只切类名，不重渲染整页，动画才不被打断 */
function openDrawer(selector) {
  const drawer = document.querySelector(selector);
  if (!drawer) return;
  drawer.classList.add("is-open");
  drawer.setAttribute("aria-hidden", "false");
  document.body.classList.add("has-v2-drawer");
  if (selector === "#v2-analysis") {
    const body = drawer.querySelector(".v2-drawer-body");
    if (body) body.scrollTop = 0;
    requestAnimationFrame(updateAnaTabs);
  }
}

function closeDrawer(selector) {
  const drawer = document.querySelector(selector);
  if (drawer) {
    drawer.classList.remove("is-open");
    drawer.setAttribute("aria-hidden", "true");
  }
  if (!document.querySelector(".v2-drawer.is-open")) document.body.classList.remove("has-v2-drawer");
}

document.addEventListener("keydown", (event) => {
  if (event.key === "Enter" || event.key === " ") {
    const card = event.target.closest?.('[data-account-action="open-transcript"]');
    if (card) {
      event.preventDefault();
      openTranscript(card.dataset.transcriptKey);
      return;
    }
  }
  if (event.key !== "Escape") return;
  document.querySelectorAll(".v2-drawer.is-open").forEach((drawer) => {
    drawer.classList.remove("is-open");
    drawer.setAttribute("aria-hidden", "true");
  });
  document.body.classList.remove("has-v2-drawer");
});

loadAccount();
