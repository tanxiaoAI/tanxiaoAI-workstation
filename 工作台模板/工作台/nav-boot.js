"use strict";

/* 侧栏 tab 的初始高亮：放在 <head> 里同步执行。
   高亮由 <html data-nav="..."> 配合 styles.css 的属性规则直接画出来，
   所以首帧（以及跨页视图过渡的 pagereveal 快照）就是对的，
   不会出现「切页后 tab 先空一下、再跳出来」的闪烁。
   工作台切视图、详情页纠正归属时，各页面脚本会同步改这个属性。 */
(function () {
  const root = document.documentElement;
  const hashView = location.hash.replace(/^#/, "").split("?")[0];
  const kind = new URLSearchParams(location.search).get("kind") || "";
  const page = root.dataset.page || "";
  let key = hashView || root.dataset.nav || "";

  if (!key) {
    if (page === "workbench") key = "home";
    else if (/account-v2(\.html)?$/.test(location.pathname)) {
      key = kind === "own" ? "own-accounts" : "benchmark-accounts-v2";
    } else if (kind === "benchmark") {
      key = "benchmark-contents";
    }
  }
  if (!key) return;

  root.dataset.nav = key;

  // 脚本若被放到 body 里（老页面），nav 已解析时直接补一次 class，双保险
  const apply = () => {
    document.querySelectorAll(".primary-nav .nav-item").forEach((item) => {
      const view = item.dataset.view || "";
      const navKey = item.dataset.navKey || "";
      item.classList.toggle("is-active", view === key || navKey === key);
    });
  };
  if (document.querySelector(".primary-nav")) apply();
  else document.addEventListener("DOMContentLoaded", apply, { once: true });
})();
