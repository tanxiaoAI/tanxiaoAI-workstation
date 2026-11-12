/* ==================================================================
   我的方向 · 规划正文渲染

   输入：我方资料/我方自媒体规划.md 的正文（Markdown 字符串）
   输出：一段 HTML 字符串，交给 .workspace-main 直接插入

   为什么要单独一个渲染器：
     规划正文是**固定结构**的（见 提示词/自媒体规划成稿提示词.md 第 3 节）——
     方向总览 + 四个一级栏目，栏目下面要么是「### 字段名」卡片，
     要么是「### 名称：xxx + #### 四个字段」的表格行。
     直接丢给 markdown 渲染只会得到一堵文字墙，所以按结构拆成卡片。

   两条硬约束：
     1. 不写死栏目数量、字段数量、名称或顺序 —— 都从文档标题里读，
        多一个用户群体、多一个交付项会自然多出一张卡 / 一行。
     2. 只有两处靠关键词判断，且都对应提示词里写死的模板：
        · 标题含「群体」→ 人群卡（不再展开成表格行）
        · 标题含「不做」→ 排除项卡片
        模板改了要一起改（见下方 GROUP_HINT / EXCLUDE_HINT）。

   只做字符串拼接，不碰 DOM —— 方便用 node 直接跑一遍看输出。
   ================================================================== */
(function () {
  "use strict";

  /* ---------------- 基础工具 ---------------- */

  const ESCAPE = { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" };
  const esc = (value) => String(value == null ? "" : value).replace(/[&<>"]/g, (ch) => ESCAPE[ch]);

  /* 行内只认 **粗体** 和 `代码`；先转义再加标签 */
  function inline(text) {
    return esc(text)
      .replace(/\*\*(.+?)\*\*/g, "<b>$1</b>")
      .replace(/`([^`]+)`/g, "<code>$1</code>");
  }

  const GROUP_HINT = /群体/;
  const EXCLUDE_HINT = /不做|避免|禁忌/;

  /* ---------------- 图标（16px 线性，描边颜色走 CSS 的 currentColor） ---------------- */

  const ICON_PATH = {
    eye: '<path d="M2.6 12S6 5.6 12 5.6 21.4 12 21.4 12 18 18.4 12 18.4 2.6 12 2.6 12z"/><circle cx="12" cy="12" r="3.2"/>',
    clock: '<circle cx="12" cy="12" r="8.4"/><path d="M12 7.4V12l3.2 2"/>',
    users: '<circle cx="9.2" cy="8.4" r="3.3"/><path d="M3.4 19.6c.8-3.5 3-5.3 5.8-5.3s5 1.8 5.8 5.3"/><path d="M15.8 6.2a3.1 3.1 0 0 1 0 5.8M17.4 14.4c1.8.7 2.9 2.4 3.2 4.8"/>',
    person: '<circle cx="12" cy="8.6" r="3.5"/><path d="M5 20c.9-3.7 3.2-5.5 7-5.5s6.1 1.8 7 5.5"/>',
    cube: '<path d="M12 3.2 4 7.6v8.8L12 20.8l8-4.4V7.6z"/><path d="M4 7.6 12 12l8-4.4M12 12v8.8"/>',
    lock: '<rect x="4.8" y="10.4" width="14.4" height="9.8" rx="2.6"/><path d="M8.4 10.4V7.8a3.6 3.6 0 0 1 7.2 0v2.6"/>',
    target: '<circle cx="12" cy="12" r="8"/><circle cx="12" cy="12" r="3.4"/>',
    pin: '<circle cx="12" cy="12" r="8.2"/><path d="m15.4 8.6-2.2 4.6-4.6 2.2 2.2-4.6z"/>',
    mic: '<rect x="9" y="3.2" width="6" height="10.4" rx="3"/><path d="M5.6 11.2a6.4 6.4 0 0 0 12.8 0M12 17.6v3.2M9.4 20.8h5.2"/>',
    wallet: '<path d="M4 8.6h16v10.2H4z"/><path d="M4 8.6 6.8 4.4h10.4L20 8.6"/><circle cx="12" cy="13.6" r="2.2"/>',
    layers: '<path d="M12 3.4 3.6 7.7 12 12l8.4-4.3z"/><path d="m3.6 12 8.4 4.3 8.4-4.3M3.6 16.3 12 20.6l8.4-4.3"/>',
    dot: '<circle cx="12" cy="12" r="3"/>',
  };

  /* 顺序有讲究：越具体的往前放 */
  const ICON_RULES = [
    [/总览|概览|整体/, "eye"],
    [/尚未|未定|待定|待确认|待验证|验证/, "clock"],
    [/群体|用户|人群|客户/, "users"],
    [/资源|现有|手里|资产/, "cube"],
    [/约束|限制|边界|风险|缺口/, "lock"],
    [/任务|目标|目的/, "target"],
    [/定位/, "pin"],
    [/风格|表达|语气|人设/, "mic"],
    [/变现|收入|定价|交付|产品|服务/, "wallet"],
    [/主线|内容|选题/, "layers"],
    [/自我|认知|我是谁|个人/, "person"],
  ];

  function iconFor(title) {
    const text = String(title || "");
    const hit = ICON_RULES.find((rule) => rule[0].test(text));
    const key = hit ? hit[1] : "dot";
    return `<svg viewBox="0 0 24 24" aria-hidden="true">${ICON_PATH[key]}</svg>`;
  }

  /* 卡片点缀色：1 墨绿 / 2 靛青 / 3 琥珀，按出现顺序轮换 */
  const TONES = ["plan-t1", "plan-t2", "plan-t3"];

  /* ---------------- Markdown → 块 → 标题树 ---------------- */

  function parseBlocks(markdown) {
    const lines = String(markdown || "").replace(/\r\n?/g, "\n").split("\n");
    const blocks = [];
    let paragraph = [];
    let list = null;
    let inFence = false;

    const flushParagraph = () => {
      if (paragraph.length) {
        blocks.push({ type: "p", text: paragraph.join(" ").trim() });
        paragraph = [];
      }
    };
    const flushList = () => {
      if (list && list.length) blocks.push({ type: "ul", items: list });
      list = null;
    };

    for (const raw of lines) {
      const line = raw.replace(/\s+$/, "");
      if (/^\s*```/.test(line)) {
        inFence = !inFence;
        continue;
      }
      if (inFence) continue;
      if (!line.trim()) {
        flushParagraph();
        flushList();
        continue;
      }
      const heading = line.match(/^(#{1,6})\s+(.*)$/);
      if (heading) {
        flushParagraph();
        flushList();
        blocks.push({ type: "h", level: heading[1].length, text: heading[2].trim() });
        continue;
      }
      const bullet = line.match(/^\s*[-*+]\s+(.*)$/);
      if (bullet) {
        flushParagraph();
        if (!list) list = [];
        list.push(bullet[1].trim());
        continue;
      }
      /* 列表项的续行 */
      if (list) {
        list[list.length - 1] += " " + line.trim();
        continue;
      }
      paragraph.push(line.trim());
    }
    flushParagraph();
    flushList();
    return blocks;
  }

  function nest(blocks) {
    const root = { type: "root", level: 0, children: [] };
    const stack = [root];
    for (const block of blocks) {
      if (block.type === "h") {
        const node = { type: "h", level: block.level, text: block.text, children: [] };
        while (stack.length > 1 && stack[stack.length - 1].level >= block.level) stack.pop();
        stack[stack.length - 1].children.push(node);
        stack.push(node);
      } else {
        stack[stack.length - 1].children.push(block);
      }
    }
    return root;
  }

  const headingsOf = (node) => (node.children || []).filter((child) => child.type === "h");
  const bodyOf = (node) => (node.children || []).filter((child) => child.type !== "h");
  const plainTitle = (text) => String(text || "").replace(/^\d+\s*[.、]\s*/, "").trim();
  /* 「具体交付：免费公开版」→ { prefix: "具体交付", name: "免费公开版" } */
  function splitName(text) {
    const raw = plainTitle(text);
    const at = raw.search(/[：:]/);
    if (at < 0) return { prefix: raw, name: raw };
    return { prefix: raw.slice(0, at).trim(), name: raw.slice(at + 1).trim() };
  }

  /* ---------------- 渲染：正文块 ---------------- */

  function bodyHtml(nodes) {
    return nodes
      .map((node) => {
        if (node.type === "p") return `<p>${inline(node.text)}</p>`;
        if (node.type === "ul") {
          return `<ul>${node.items.map((item) => `<li>${inline(item)}</li>`).join("")}</ul>`;
        }
        return "";
      })
      .join("");
  }

  /* ---------------- 渲染：卡头 ---------------- */

  function cardHead(title, extra, sub) {
    const tail = sub ? `<span class="plan-card-sub">：${inline(sub)}</span>` : "";
    return `<div class="plan-card-h"><span class="plan-ico">${iconFor(title)}</span><b class="plan-card-t">${esc(title)}</b>${tail}${extra || ""}</div>`;
  }

  /* ---------------- 渲染：方向总览 ---------------- */

  /* 「**自身条件**：一句话」。老文件的总览是整段散文，一行都匹配不上时照旧排版。 */
  const OVERVIEW_ROW = /^\*\*([^*]{1,12})\*\*[ \t]*[：:][ \t]*([\s\S]+)$/;

  /* 一段里挤了好几条字段（没有空行分隔）也拆开，按「**字段**：」的位置切 */
  function overviewRows(text) {
    return String(text)
      .split(/(?=\*\*[^*\n]{1,12}\*\*[ \t]*[：:])/)
      .map((part) => part.trim())
      .filter(Boolean)
      .map((part) => {
        const hit = part.match(OVERVIEW_ROW);
        return hit
          ? { type: "row", label: hit[1].trim(), text: hit[2].trim() }
          : { type: "note", text: part };
      });
  }

  function renderSummary(node) {
    const body = bodyOf(node);
    const paragraphs = body.filter((item) => item.type === "p");
    const lists = body.filter((item) => item.type === "ul");
    const pending = lists.length ? lists[0].items : [];

    const rows = paragraphs.flatMap((item) => overviewRows(item.text));
    const overview = rows.some((item) => item.type === "row")
      ? `<div class="plan-ov">${rows
          .map((item) =>
            item.type === "row"
              ? `<div class="plan-ov-row"><span class="plan-ov-t">${esc(item.label)}</span><p>${inline(item.text)}</p></div>`
              : `<p class="plan-ov-note">${inline(item.text)}</p>`
          )
          .join("")}</div>`
      : `<div class="plan-prose">${paragraphs.map((item) => `<p>${inline(item.text)}</p>`).join("")}</div>`;

    /* 需要验证的清单留在总览卡里，排在五条字段下面，不再单独切一栏 */
    const verify = pending.length
      ? `<div class="plan-ov-verify">
          <span class="plan-ov-t">需要验证</span>
          <div class="plan-chips">${pending.map((item) => `<em>${inline(item)}</em>`).join("")}</div>
        </div>`
      : "";

    return `<section class="plan-summary is-solo">
      <div class="plan-summary-main ${TONES[0]}">
        ${cardHead("方向总览")}
        ${overview}
        ${verify}
      </div>
    </section>`;
  }

  /* ---------------- 渲染：一个一级栏目 ---------------- */

  function renderSection(node, index) {
    const title = plainTitle(node.text);
    const h3s = headingsOf(node);
    const out = [];
    let cursor = 0;
    let cardIndex = 0;

    const kindOf = (h3) => {
      const fields = headingsOf(h3);
      if (fields.length) return GROUP_HINT.test(h3.text) ? "group" : "row";
      if (EXCLUDE_HINT.test(h3.text)) return "exclude";
      return "card";
    };

    while (cursor < h3s.length) {
      const kind = kindOf(h3s[cursor]);

      if (kind === "card") {
        const group = [];
        while (cursor < h3s.length && kindOf(h3s[cursor]) === "card") group.push(h3s[cursor++]);
        out.push(renderCards(group, cardIndex));
        cardIndex += group.length;
        continue;
      }
      if (kind === "row") {
        const group = [];
        while (cursor < h3s.length && kindOf(h3s[cursor]) === "row") group.push(h3s[cursor++]);
        out.push(renderTable(group));
        continue;
      }
      if (kind === "group") {
        out.push(renderGroupCard(h3s[cursor++]));
        continue;
      }
      out.push(renderExclude(h3s[cursor++]));
    }

    const head = `<div class="plan-sec-head"><h2>${esc(title)}</h2><span class="plan-sec-rule"></span></div>`;
    return `<section class="plan-sec" id="plan-sec-${index + 1}">${head}${out.join("")}</section>`;
  }

  /* 连续出现的无字段 `###` → 并排卡片；只有一个时占满整行（正文分两栏） */
  function renderCards(group, offset) {
    const wide = group.length === 1;
    const cards = group.map((node, i) => {
      /* 同一排卡片统一一种点缀色：三色轮换会让三张卡看起来一样重，分不出主次 */
      const tone = TONES[0];
      const title = plainTitle(node.text);
      const blocks = bodyOf(node);
      const paragraphs = blocks.filter((block) => block.type === "p");
      /* 宽卡里正好两段、且是「怎么变现」这类字段时，拆成「做什么 / 不做」两栏；
         判据不成立就照常整段排版，免得给不相干的两段硬套标签。 */
      const split = wide && paragraphs.length === 2 && /变现|收入|赚钱|盈利/.test(title);
      const body = split
        ? `<div class="plan-split">
            <section class="plan-split-i is-do"><span class="plan-split-t">做什么</span><p>${inline(paragraphs[0].text)}</p></section>
            <section class="plan-split-i is-dont"><span class="plan-split-t">不做</span><p>${inline(paragraphs[1].text)}</p></section>
          </div>`
        : `<div class="plan-prose">${bodyHtml(blocks)}</div>`;
      return `<article class="plan-card${wide ? " is-wide" : ""} ${tone}">
        ${cardHead(title)}
        ${body}
      </article>`;
    });
    const count = Math.min(group.length, 3);
    return `<div class="plan-grid${wide ? " is-single" : ""}" data-cols="${count}">${cards.join("")}</div>`;
  }

  /* 「用户群体：xxx」+ 六个字段 */
  function renderGroupCard(node) {
    const { prefix, name } = splitName(node.text);
    const fields = headingsOf(node);
    const cells = fields
      .map((field) => {
        const body = bodyHtml(bodyOf(field));
        return `<div class="plan-field"><span class="plan-field-t">${esc(plainTitle(field.text))}</span>${body}</div>`;
      })
      .join("");
    return `<article class="plan-group ${TONES[0]}">
      ${cardHead(prefix, "", name && name !== prefix ? name : "")}
      <div class="plan-fields">${cells}</div>
    </article>`;
  }

  /* 连续出现的「xxx：名称」+ 子字段 → 一张表 */
  function renderTable(group) {
    const labels = [];
    const rows = group.map((node) => {
      const { prefix, name } = splitName(node.text);
      const cells = {};
      for (const field of headingsOf(node)) {
        const label = plainTitle(field.text);
        if (!labels.includes(label)) labels.push(label);
        cells[label] = bodyHtml(bodyOf(field));
      }
      /* 子字段之后还挂着段落时，当作这张表的备注，不丢内容 */
      const notes = bodyOf(node).filter((item) => item.type === "p").map((item) => inline(item.text));
      return { prefix, name, cells, notes };
    });

    const tableTitle = rows[0].prefix;
    /* 列宽交给 CSS 的 flex（首列固定 186px，其余等分）。
       ⚠️ 不要用行内 style 写 grid-template-columns：工作台 CSP 是 style-src 'self'，
       行内样式会被整条丢掉，表格会塌成一列。 */
    const head = `<div class="plan-row is-head">
      <div class="plan-name">${esc(tableTitle)}</div>
      ${labels.map((label) => `<div class="plan-cell">${esc(label)}</div>`).join("")}
    </div>`;
    const body = rows
      .map(
        (row) => `<div class="plan-row">
        <div class="plan-name"><b>${esc(row.name)}</b></div>
        ${labels.map((label) => `<div class="plan-cell">${row.cells[label] || ""}</div>`).join("")}
      </div>`
      )
      .join("");
    const notes = rows
      .flatMap((row) => row.notes)
      .map((note) => `<p class="plan-note">${note}</p>`)
      .join("");

    return `<div class="plan-table-block">
      <div class="plan-table-t">${esc(tableTitle)}</div>
      <div class="plan-table-wrap"><div class="plan-table">${head}${body}</div></div>
      ${notes}
    </div>`;
  }

  /* 「哪些内容不做」→ 排除项卡片 */
  function renderExclude(node) {
    const items = [];
    for (const child of bodyOf(node)) {
      if (child.type === "ul") items.push(...child.items);
      else if (child.type === "p") items.push(child.text);
    }
    if (!items.length) return "";
    return `<div class="plan-table-block">
      <div class="plan-table-t">${esc(plainTitle(node.text))}</div>
      <div class="plan-excl">${items
        .map((item) => `<div class="plan-excl-i"><span>不做</span><b>${inline(item)}</b></div>`)
        .join("")}</div>
    </div>`;
  }

  /* ---------------- 卡片高度：量溢出、挂「展开」 ---------------- */

  const mounted = [];

  const CHEVRON = '<svg viewBox="0 0 12 12" aria-hidden="true"><path d="M2 4.5 6 8.5l4-4" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"/></svg>';

  function bindMore(card) {
    if (card.querySelector(".plan-more")) return;
    const fade = document.createElement("span");
    fade.className = "plan-fade";
    const foot = document.createElement("div");
    foot.className = "plan-foot";
    const button = document.createElement("button");
    button.type = "button";
    button.className = "plan-more";
    button.innerHTML = `<span>展开</span>${CHEVRON}`;
    button.addEventListener("click", () => {
      const open = card.classList.toggle("is-open");
      button.querySelector("span").textContent = open ? "收起" : "展开";
    });
    foot.append(button);
    card.append(fade, foot);
  }

  /* 卡片高度写死在 CSS 里；这里只判断「内容有没有超出」，
     超出才挂渐隐和「展开」，没超出就让下半截空着。
     底部的白色操作栏会盖住一截，判定时要把它减掉。 */
  function relayout(root) {
    const cards = Array.from(root.querySelectorAll(".plan-card:not(.is-wide)"));
    if (!cards.length) return;
    const openCards = new Set(cards.filter((card) => card.classList.contains("is-open")));
    cards.forEach((card) => card.classList.remove("is-open"));
    const measured = cards.map((card) => {
      card.classList.add("is-measuring");
      const natural = card.scrollHeight;
      card.classList.remove("is-measuring");
      const foot = card.querySelector(".plan-foot");
      const room = card.clientHeight - (foot ? foot.offsetHeight : 0);
      return { card, over: natural - room > 4 };
    });
    measured.forEach(({ card, over }) => {
      card.classList.toggle("has-more", over);
      const reopen = over && openCards.has(card);
      card.classList.toggle("is-open", reopen);
      const label = card.querySelector(".plan-more span");
      if (label) label.textContent = reopen ? "收起" : "展开";
    });
  }

  /* 渲染完插进 DOM 之后调用一次；窗口变宽变窄要重新量 */
  function mount(root) {
    if (!root) return;
    root.querySelectorAll(".plan-card:not(.is-wide)").forEach(bindMore);
    relayout(root);
    if (!mounted.includes(root)) mounted.push(root);
    if (!mount.bound) {
      mount.bound = true;
      let timer = 0;
      window.addEventListener("resize", () => {
        window.clearTimeout(timer);
        timer = window.setTimeout(() => mounted.forEach(relayout), 180);
      });
    }
  }

  /* ---------------- 对外 ---------------- */

  /* 只有新结构（提示词第 3 节）才走卡片版式；旧文档原样交给 markdown 渲染 */
  function supports(markdown) {
    return /^##\s*方向总览\s*$/m.test(String(markdown || ""));
  }

  function render(markdown) {
    const tree = nest(parseBlocks(markdown));
    /* 顶层标题一般是 `# 我方账号方向`，正文栏目是它的下一级。
       这里做一次下探：只有一层顶层标题、且它带子标题时，用子标题当栏目。 */
    let sections = headingsOf(tree);
    if (sections.length === 1 && headingsOf(sections[0]).length) {
      sections = headingsOf(sections[0]);
    }
    const parts = [];
    let sectionIndex = 0;
    for (const node of sections) {
      if (/^方向总览$/.test(plainTitle(node.text))) {
        parts.push(renderSummary(node));
        continue;
      }
      parts.push(renderSection(node, sectionIndex++));
    }
    return `<div class="plan-doc">${parts.join("")}</div>`;
  }

  window.MeilingPlanning = { supports, render, mount, parseBlocks, nest };
})();
