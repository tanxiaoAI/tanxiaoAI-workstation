"use strict";

/* 内容详情抽屉：独立页（账号详情 / 选题分析）include 之后，
   带 data-open-content="own|benchmark" + data-content-id 的卡片点了在右侧开，
   里面装的是 content.html 的 embed 模式——和工作台 #content-drawer 同一份详情，不重画。

   ⚠️ benchmark 认的是 key（平台ID::内容ID），own 认内容ID；传错会「未找到该内容」。
   ⚠️ 链接本身的 href 保留着：脚本没跑起来时照旧跳页，不会变成点不动的死卡。 */
(function () {
  // 和 content.html 里 css/js 的 ?v= 同步跳，否则抽屉里会拿到旧样式
  const ASSET_VERSION = "20261102-review1";
  let drawer = null;

  function ensureDrawer() {
    if (drawer) return drawer;
    drawer = document.createElement("dialog");
    drawer.className = "content-drawer";
    drawer.id = "content-drawer";
    drawer.innerHTML = '<iframe title="内容详情"></iframe>';
    drawer.addEventListener("click", (event) => {
      if (event.target === drawer) closeDrawer();
    });
    drawer.addEventListener("cancel", (event) => {
      event.preventDefault();
      closeDrawer();
    });
    document.body.appendChild(drawer);
    return drawer;
  }

  function contentPageUrl(kind, id) {
    const params = new URLSearchParams({ kind, id, v: ASSET_VERSION, embed: "1" });
    return `content.html?${params.toString()}`;
  }

  function openDrawer(kind, id) {
    const node = ensureDrawer();
    node.querySelector("iframe").src = contentPageUrl(kind, id);
    if (!node.open) node.showModal();
    requestAnimationFrame(() => node.classList.add("is-open"));
  }

  function closeDrawer() {
    if (!drawer || !drawer.open) return;
    drawer.classList.remove("is-open");
    window.setTimeout(() => {
      if (drawer.classList.contains("is-open")) return;
      drawer.close();
      drawer.querySelector("iframe").removeAttribute("src");
    }, 280);
  }

  document.addEventListener("click", (event) => {
    const trigger = event.target.closest("[data-open-content]");
    if (!trigger) return;
    const id = trigger.dataset.contentId;
    if (!id) return;
    event.preventDefault();
    openDrawer(trigger.dataset.openContent || "own", id);
  });

  // 抽屉里的「返回上一页」在内嵌模式下不发跳转，改成让父窗口关掉抽屉
  window.addEventListener("message", (event) => {
    if (event.origin !== location.origin) return;
    if (event.data?.type === "meiling:close-content-drawer") closeDrawer();
  });
})();
