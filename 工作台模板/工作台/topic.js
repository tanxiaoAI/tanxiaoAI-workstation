"use strict";

const query = new URLSearchParams(location.search);
const topicId = query.get("id") || "";
const requestedReturnTo = query.get("return_to") || "";

// 详情页的 tab 归属是固定的，进页面就点亮，别等快照回来才亮
document.querySelectorAll(".primary-nav .nav-item").forEach((item) => {
  item.classList.toggle("is-active", item.dataset.navKey === "topics");
});

const elements = {
  back: document.querySelector("#back-to-topics"),
  breadcrumb: document.querySelector("#topic-breadcrumb"),
  reload: document.querySelector("#reload-topic"),
  view: document.querySelector("#topic-view"),
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

function tagTone(value) {
  const text = String(value || "");
  if (/不建议|失败|异常|阻断|废弃|取消|无效|不适配|不通过|高风险/.test(text)) return "tone-red";
  if (/风险|低适配|低质量|不匹配|问题|待优化|需修正|受限|较差/.test(text)) return "tone-pink";
  if (/暂缓|生产中|待生产|待发布|处理中|进行中|排期|草稿/.test(text)) return "tone-orange";
  if (/待|未填写|待补齐|缺失|暂无|普通|一般|未知|低优先/.test(text)) return "tone-yellow";
  if (/建议做|优秀|高适配|大爆款|已完成|已通过|已验证|可用|高优先/.test(text)) return "tone-deep-green";
  if (/已发布|正常|小爆款|中适配|已复盘|已建档|已分析|已启用|稳定/.test(text)) return "tone-light-green";
  return "tone-neutral";
}

function tag(value) {
  const text = value || "未填写";
  return `<span class="content-tag ${tagTone(text)}">${escapeHtml(text)}</span>`;
}

function contentUrl(id) {
  return `/content?kind=own&id=${encodeURIComponent(id)}&return_to=${encodeURIComponent(`topic?id=${topicId}`)}`;
}

function referenceAnalysisUrl(key) {
  return `/content?kind=benchmark&id=${encodeURIComponent(key)}&return_to=${encodeURIComponent(`topic?id=${topicId}`)}`;
}

function emptyState(title, detail) {
  return `<div class="content-empty"><strong>${escapeHtml(title)}</strong><p>${escapeHtml(detail)}</p><a href="index.html#topics">返回选题分析</a></div>`;
}

function safeReturnPath(value) {
  const candidate = String(value || "").trim();
  if (!candidate || candidate.startsWith("/") || candidate.startsWith("\\") || /^[a-z][a-z\d+.-]*:/i.test(candidate)) return "";
  try {
    const target = new URL(candidate, `${location.origin}/`);
    if (target.origin !== location.origin) return "";
    const path = target.pathname.replace(/^\//, "");
    if (path === "index.html" && (!target.hash || target.hash === "#topics")) return `${path}${target.hash}`;
  } catch (_error) {
    // Invalid return values use the topic list fallback.
  }
  return "";
}

function returnToTopics() {
  location.assign(safeReturnPath(requestedReturnTo) || "index.html#topics");
}

async function fetchSnapshot(force = false) {
  const response = await fetch(force ? "/api/refresh" : "/api/snapshot", { cache: "no-store" });
  if (!response.ok) throw new Error(`snapshot_read_failed:${response.status}`);
  return response.json();
}

async function fetchDocument(path) {
  const response = await fetch(`/api/file?path=${encodeURIComponent(path)}`, { cache: "no-store" });
  if (!response.ok) throw new Error(`document_read_failed:${response.status}`);
  return response.json();
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

function renderTopic(topic, analysisContent) {
  markNavActive("topics");
  const relatedContents = topic.contents || [];
  const referenceContentHtml = topic.reference_content_key
    ? `<a class="topic-related-link" href="${referenceAnalysisUrl(topic.reference_content_key)}" data-open-content="benchmark" data-content-id="${escapeAttribute(topic.reference_content_key)}">查看参考内容分析</a>`
    : '<p class="topic-related-empty">无</p>';
  const relatedContentHtml = relatedContents.length
    ? `<ul class="topic-related-list">${relatedContents.map((content) => `<li><a class="topic-related-link" href="${contentUrl(content.id)}" data-open-content="own" data-content-id="${escapeAttribute(content.id)}">${escapeHtml(content.title || content.id)}</a></li>`).join("")}</ul>`
    : '<p class="topic-related-empty">无</p>';
  const analysisBody = topic.analysis_path
    ? `<div class="markdown-body">${analysisContent.replace(/^\s*<h1[^>]*>[\s\S]*?<\/h1>\s*/, "")}</div>`
    : emptyState("尚未做选题分析", "当前选题没有可读取的正式选题分析。");
  /* 正文只有一份正式选题分析，用与「内容详情」一致的形态：
     顶部一行资料路径（超长省略号，title 里给全路径），下面是正文。 */
  const analysisPanel = `<article class="content-document">
    <header><span class="document-path" title="${escapeAttribute(topic.analysis_path || "")}">${escapeHtml(topic.analysis_path || "当前资料尚未建立可用路径")}</span></header>
    ${analysisBody}
  </article>`;

  const crumbTitle = String(topic.name || topic.id || "选题详情");
  elements.breadcrumb.textContent = crumbTitle.length > 34 ? `${crumbTitle.slice(0, 34)}…` : crumbTitle;
  elements.breadcrumb.title = crumbTitle;
  document.title = `${topic.name}｜媒灵选题详情`;
  elements.view.innerHTML = `<section class="content-summary topic-summary">
    <div class="summary-copy">
      <h1>${escapeHtml(topic.name)}</h1>
      <div class="tag-row">${tag(topic.recommendation)}${tag(topic.status)}</div>
      <dl class="summary-facts"><div><dt>选题编号</dt><dd>${escapeHtml(topic.id)}</dd></div><div><dt>更新时间</dt><dd>${formatDate(topic.updated_at)}</dd></div></dl>
    </div>
    <aside class="summary-media topic-summary-relations" aria-label="选题关联资料">
      <section class="topic-summary-relation"><header><span>参考内容分析</span><b>${topic.reference_content_key ? 1 : 0}</b></header>${referenceContentHtml}</section>
      <section class="topic-summary-relation"><header><span>关联内容</span><b>${relatedContents.length}</b></header>${relatedContentHtml}</section>
    </aside>
  </section>
  <div class="topic-content-layout">${analysisPanel}</div>`;
}

async function loadTopic({ force = false } = {}) {
  if (!topicId) {
    elements.view.innerHTML = emptyState("选题地址无效", "请从选题分析中的选题标题进入详情。");
    return;
  }
  elements.reload.disabled = true;
  elements.view.innerHTML = '<div class="content-loading"><span></span><p>正在读取选题详情</p></div>';
  try {
    const snapshot = await fetchSnapshot(force);
    syncWorkspaceVersion(snapshot);
    const topic = snapshot.workflows?.production?.topics?.find((item) => item.id === topicId);
    if (!topic) {
      elements.view.innerHTML = emptyState("未找到该选题", "这条选题可能已被移动或删除，请返回选题分析刷新后重试。");
      return;
    }
    let analysisContent = "";
    if (topic.analysis_path) {
      try {
        const document = await fetchDocument(topic.analysis_path);
        analysisContent = globalThis.MeilingMarkdown.renderMarkdown(document.content, { documentPath: document.path || topic.analysis_path });
      } catch (error) {
        analysisContent = `<p class="topic-empty-copy">正式选题分析读取失败：${escapeHtml(error.message)}</p>`;
      }
    }
    renderTopic(topic, analysisContent);
  } catch (error) {
    elements.view.innerHTML = emptyState("选题详情暂时不可用", error.message);
  } finally {
    elements.reload.disabled = false;
  }
}

elements.back.addEventListener("click", returnToTopics);
elements.reload.addEventListener("click", () => loadTopic({ force: true }));
loadTopic();
