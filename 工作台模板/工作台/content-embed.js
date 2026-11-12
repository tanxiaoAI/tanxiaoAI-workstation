"use strict";

/* 内容详情的内嵌模式：index.html 的脚本创作抽屉用 iframe 打开 content.html?embed=1，
   这个脚本在 <head> 里同步执行，给 <html> 挂 is-embed，content.css 据此去掉侧栏和顶栏。
   放在 head 是为了首帧就对，不然会先闪一下整页布局再收窄。

   ⚠️ 不能写成内联 <script>：工作台的 CSP 是 script-src 'self'，内联脚本会被静默拦掉。 */
if (new URLSearchParams(location.search).get("embed") === "1") {
  document.documentElement.classList.add("is-embed");
}
