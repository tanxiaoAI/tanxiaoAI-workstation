"use strict";

/* 独立问卷页的挂载入口：题库、交互和解析都在 survey-core.js 里。
   带 ?edit=1 进来时（从账号方向的「修改问卷」点进来），先读回
   我方资料/调查问卷.md，把上次提交的答案回填到表单。 */

(async function () {
  const root = document.querySelector("#survey-root");
  if (!root || !window.MeilingSurvey) return;

  // 独立问卷页也算「打开问卷」。首页内嵌的那条在 app.js 里打卡，两边互不影响：
  // 服务端按机器码去重，同一台机器只会算一台。
  fetch("/api/onboarding-survey/open", { cache: "no-store" }).catch(() => {});

  const options = {
    container: root,
    variant: "page",
    storageKey: "meiling:first-survey:v2",
    endpoint: "/api/onboarding-survey",
  };

  if (new URLSearchParams(location.search).get("edit") === "1") {
    // 从账号方向进来改：返回链接也回到账号方向，不再回工作台首页
    const brand = document.querySelector(".survey-brand");
    if (brand) brand.href = "index.html#media-plan";
    try {
      const response = await fetch(`/api/file?path=${encodeURIComponent(window.MeilingSurvey.answersPath)}`, { cache: "no-store" });
      if (response.ok) {
        const file = await response.json();
        options.initial = window.MeilingSurvey.parse(file.content);
        options.editing = true;
      }
    } catch (_error) {
      // 读不到档案就按首次填写处理，不影响继续填
    }
  }

  window.MeilingSurvey.mount(options);
})();
