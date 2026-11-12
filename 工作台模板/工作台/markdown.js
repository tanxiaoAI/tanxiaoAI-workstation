/* ==================================================================
   媒灵工作台 · Markdown 渲染器 v1.2 · 2026-10-05

   落地位置：工作台/markdown.js（替换同名文件）
   配套样式：工作台/markdown.css

   相对 v1.0 的五点增强（都是「只增不删」：条件不满足时输出与原来完全一致）：
     1. H2 序号徽标   —— 标题带「1. 」前缀时取该数字，否则用本章节序号，
                        输出 <h2><span class="md-badge">01</span><span>标题</span></h2>。
     2. 口径句提示块  —— 段落以「**分档规则**：…」这类写法开头、且标签落在
                        白名单里时，输出成
                        <div class="md-tip"><span class="md-tip-label">标签</span><p>正文</p></div>。
                        白名单＝分档规则 / 读表规则 / 口径说明 / 一句话概括 /
                        该学什么 / 数据边界 / 证据范围。
                        ⚠️ 白名单是刻意的：工作台渲染的不只是分析报告，还有流程文档、
                        提示词、SKILL、内容分析（那里 `**标题文本**：…` 是字段行）。
                        写成「任何 `**X**：…` 都转」的话，这些文档里会一半段落变绿、
                        一半不变，比全都不转还难看。收窄后误伤面为零。
     3. 表格结论胶囊  —— 表头最后一列叫「结论」时，该列单元格按文案套
                        md-pill-green / md-pill-red / md-pill-gray。
     4. 列表缩进层级  —— 原来所有缩进层级都被拍平成同级；现在按缩进宽度
                        还原 1~3 级（2~3 空格算一级，4~6 空格算二级）。
     5. 字段二级说明  —— 表格单元格里的 <small> 原样放行，配合 markdown.css
                        渲染成字段名下方的小字说明（内容框架的「字段」列用它写
                        「这段讲什么 / 本段具体讲什么…」这两级）。

   覆盖范围：账号详细分析 / 账号内容规律分析 / 概要分析 / 复盘记录 /
            内容生产各阶段文档 / 选题分析 / SKILL 详情 / 详情弹窗，
            全部由这五个增强一起受益。
   ================================================================== */
(function installMeilingMarkdown(root, factory) {
  const api = factory();
  if (typeof module === "object" && module.exports) module.exports = api;
  root.MeilingMarkdown = api;
})(globalThis, function createMeilingMarkdown() {
  const WORKSPACE_ROOTS = new Set(["我方资料", "对标资料", "沉淀结论", "skills"]);
  const IMAGE_EXTENSIONS = new Set(["png", "jpg", "jpeg", "webp", "gif"]);

  // H2 序号徽标：识别「1. 」「2、」这类前缀
  const HEADING_NUMBER = /^(\d+)[.、]\s*(.+)$/;

  function escapeHtml(value) {
    return String(value ?? "")
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#039;");
  }

  function escapeAttribute(value) {
    return escapeHtml(value).replace(/`/g, "&#096;");
  }

  function destinationValue(value) {
    const trimmed = String(value || "").trim();
    if (trimmed.startsWith("<")) {
      const end = trimmed.indexOf(">");
      return end > 1 ? trimmed.slice(1, end).trim() : "";
    }
    return trimmed.split(/\s+["']/)[0].trim();
  }

  function safeExternalUrl(value) {
    try {
      const url = new URL(destinationValue(value));
      return /^(?:https?):$/.test(url.protocol) ? url.href : "";
    } catch (_error) {
      return "";
    }
  }

  // 表格单元格里没法换行，所以流程文档用 <ul><li>…</li></ul> 表达无序列表，
  // 内容框架的「字段」列用 <small> 写字段名下方的二级说明。
  // 只放行 ul/ol/li/small 四个标签本身，属性一律丢弃，其余 HTML 仍然按纯文本转义。
  const ALLOWED_TAG_PATTERN = /^<(\/?)(ul|ol|li|small)\b[^>]*>$/i;

  function sanitizeAllowedTag(value) {
    const match = ALLOWED_TAG_PATTERN.exec(String(value || "").trim());
    return match ? `<${match[1]}${match[2].toLowerCase()}>` : "";
  }

  function normalizeWorkspaceImagePath(value, documentPath) {
    const source = destinationValue(value);
    if (!source || source.includes("\\") || source.includes("\0") || source.startsWith("/")) return "";
    if (/^[a-z][a-z\d+.-]*:/i.test(source) || source.includes("?") || source.includes("#")) return "";

    const sourceParts = source.split("/");
    const documentParts = String(documentPath || "").split("/").filter(Boolean);
    const parts = WORKSPACE_ROOTS.has(sourceParts[0]) ? [] : documentParts.slice(0, -1);
    for (const part of sourceParts) {
      if (!part || part === ".") continue;
      if (part === "..") {
        if (parts.length <= 1) return "";
        parts.pop();
        continue;
      }
      parts.push(part);
    }
    if (!WORKSPACE_ROOTS.has(parts[0])) return "";
    const extension = parts.at(-1)?.split(".").pop()?.toLowerCase() || "";
    return IMAGE_EXTENSIONS.has(extension) ? parts.join("/") : "";
  }

  function renderInline(value, options = {}) {
    const source = String(value || "");
    const pattern = /`([^`\n]+)`|\*\*([^*\n]+)\*\*|!\[([^\]\n]*)\]\(([^)\n]+)\)|\[([^\]\n]+)\]\(([^)\n]+)\)|(<\/?(?:ul|ol|li|small)\b[^>]*>)|<br\s*\/?>/gi;
    let output = "";
    let cursor = 0;
    for (const match of source.matchAll(pattern)) {
      output += escapeHtml(source.slice(cursor, match.index));
      if (match[1] !== undefined) {
        output += `<code>${escapeHtml(match[1])}</code>`;
      } else if (match[2] !== undefined) {
        output += `<strong>${escapeHtml(match[2])}</strong>`;
      } else if (match[3] !== undefined) {
        const workspacePath = normalizeWorkspaceImagePath(match[4], options.documentPath);
        output += workspacePath
          ? `<img class="markdown-image" src="/api/raw?path=${encodeURIComponent(workspacePath)}" alt="${escapeAttribute(match[3])}" loading="lazy">`
          : escapeHtml(match[3] || "图片");
      } else if (match[5] !== undefined) {
        const url = safeExternalUrl(match[6]);
        output += url
          ? `<a href="${escapeAttribute(url)}" target="_blank" rel="noopener noreferrer">${escapeHtml(match[5])}</a>`
          : escapeHtml(match[5]);
      } else if (match[7] !== undefined) {
        output += sanitizeAllowedTag(match[7]);
      } else {
        output += "<br>";
      }
      cursor = match.index + match[0].length;
    }
    return output + escapeHtml(source.slice(cursor));
  }

  // 结论胶囊：只认这三类固定文案
  function pillClass(text) {
    if (text.includes("数据好")) return "md-pill md-pill-green";
    if (text.includes("数据差")) return "md-pill md-pill-red";
    if (text.includes("区分不出好坏") || text.includes("三档差不多") || text.includes("判不出方向")) return "md-pill md-pill-gray";
    return "";
  }

  // 段落转提示块：**只认这份白名单里的标签**。
  // ⚠️ 不能写成「任何 `**X**：…` 都转」——工作台里 markdown 渲染的不只是分析报告，
  //    还有流程文档、提示词、SKILL、内容分析（那里 `**标题文本**：…` 是字段行）。
  //    放开写会让这些文档里半数段落变成绿色提示块、半数不是，比全都不转还难看。
  //    这 7 个标签是分析报告里专门用来写「口径句」的，误伤面为零。
  const TIP_LABELS = ["分档规则", "读表规则", "口径说明", "一句话概括", "该学什么", "数据边界", "证据范围"];

  function tipOf(line) {
    const stripped = String(line || "").trim();
    const bold = stripped.match(/^\*\*(.+?)\*\*[：:]\s*(.*)$/);
    const plain = bold ? null : stripped.match(/^([^：:\n]{1,10})[：:]\s*(.*)$/);
    const label = bold ? bold[1] : (plain ? plain[1] : "");
    const rest = bold ? bold[2] : (plain ? plain[2] : "");
    if (!TIP_LABELS.includes(label) || rest.length < 8) return null;
    return [label, rest];
  }

  // 缩进宽度 → 层级（2~3 空格一级，4~6 空格二级，再深封顶三级）
  function indentDepth(spaces) {
    const width = String(spaces || "").replace(/\t/g, "    ").length;
    if (width <= 0) return 0;
    if (width <= 3) return 1;
    if (width <= 6) return 2;
    return 3;
  }

  function renderMarkdown(input, options = {}) {
    const lines = String(input || "").replace(/\r\n?/g, "\n").split("\n");
    const output = [];
    let codeOpen = false;
    let codeLines = [];
    let headingIndex = 0;
    // 列表栈：每层记 key（ul / ol / tasks）与缩进层级，用来还原嵌套
    const listStack = [];

    const closeListTag = (item) => {
      output.push(item.key === "ol" ? "</ol>" : "</ul>");
    };
    const closeListsTo = (depth) => {
      while (listStack.length && listStack[listStack.length - 1].depth >= depth) {
        closeListTag(listStack.pop());
      }
    };
    const closeList = () => {
      while (listStack.length) closeListTag(listStack.pop());
    };
    const openList = (key, depth) => {
      const top = listStack[listStack.length - 1];
      if (top && top.depth === depth && top.key === key) return;
      closeListsTo(depth);
      if (key === "ol") {
        output.push("<ol>");
      } else {
        const classes = [];
        if (listStack.length > 0) classes.push("markdown-nested");
        if (key === "tasks") classes.push("markdown-task-list");
        output.push(classes.length ? `<ul class="${classes.join(" ")}">` : "<ul>");
      }
      listStack.push({ key, depth });
    };

    const tableCells = (line) => line.trim().replace(/^\||\|$/g, "").split("|").map((cell) => cell.trim());
    const tableDivider = (line) => {
      const cells = tableCells(line);
      return cells.length > 0 && cells.every((cell) => /^:?-{3,}:?$/.test(cell));
    };

    for (let index = 0; index < lines.length; index += 1) {
      const line = lines[index];
      if (line.startsWith("```")) {
        closeList();
        if (codeOpen) {
          output.push(`<pre><code>${escapeHtml(codeLines.join("\n"))}</code></pre>`);
          codeLines = [];
        }
        codeOpen = !codeOpen;
        continue;
      }
      if (codeOpen) {
        codeLines.push(line);
        continue;
      }

      const heading = line.match(/^(#{1,6})\s+(.+)$/);
      if (heading) {
        closeList();
        const level = Math.min(4, heading[1].length);
        if (level === 2) {
          headingIndex += 1;
          const numbered = heading[2].match(HEADING_NUMBER);
          const badge = numbered ? String(Number(numbered[1])).padStart(2, "0") : String(headingIndex).padStart(2, "0");
          const title = numbered ? numbered[2] : heading[2];
          output.push(`<h2><span class="md-badge">${badge}</span><span>${renderInline(title, options)}</span></h2>`);
        } else {
          output.push(`<h${level}>${renderInline(heading[2], options)}</h${level}>`);
        }
        continue;
      }
      const task = line.match(/^(\s*)[-*+]\s+\[([ xX])\]\s+(.+)$/);
      if (task) {
        openList("tasks", indentDepth(task[1]));
        const checked = task[2].toLowerCase() === "x" ? " checked" : "";
        output.push(`<li><input type="checkbox" disabled${checked}><span>${renderInline(task[3], options)}</span></li>`);
        continue;
      }
      const unordered = line.match(/^(\s*)[-*+]\s+(.+)$/);
      if (unordered) {
        openList("ul", indentDepth(unordered[1]));
        output.push(`<li>${renderInline(unordered[2], options)}</li>`);
        continue;
      }
      const ordered = line.match(/^(\s*)\d+[.)]\s+(.+)$/);
      if (ordered) {
        openList("ol", indentDepth(ordered[1]));
        output.push(`<li>${renderInline(ordered[2], options)}</li>`);
        continue;
      }

      closeList();
      if (line.trim() === "" || /^\s*---+\s*$/.test(line)) continue;
      if (line.startsWith(">")) {
        output.push(`<blockquote>${renderInline(line.replace(/^>\s?/, ""), options)}</blockquote>`);
        continue;
      }
      if (line.trim().startsWith("|") && tableDivider(lines[index + 1] || "")) {
        const header = tableCells(line);
        const rows = [];
        index += 2;
        while (index < lines.length && lines[index].trim().startsWith("|")) {
          rows.push(tableCells(lines[index]));
          index += 1;
        }
        index -= 1;
        const conclusionColumn = header.length > 0 && header[header.length - 1] === "结论";
        const cells = (row, tag) => header.map((_, column) => {
          const raw = row[column] || "";
          if (conclusionColumn && column === header.length - 1) {
            const cls = pillClass(raw);
            if (cls) return `<${tag}><span class="${cls}">${renderInline(raw, options)}</span></${tag}>`;
          }
          return `<${tag}>${renderInline(raw, options)}</${tag}>`;
        }).join("");
        output.push(`<div class="markdown-table-wrap"><table class="markdown-table"><thead><tr>${cells(header, "th")}</tr></thead><tbody>${rows.map((row) => `<tr>${cells(row, "td")}</tr>`).join("")}</tbody></table></div>`);
        continue;
      }
      const tip = tipOf(line);
      if (tip) {
        output.push(`<div class="md-tip"><span class="md-tip-label">${renderInline(tip[0], options)}</span><p>${renderInline(tip[1], options)}</p></div>`);
        continue;
      }
      const inline = renderInline(line, options);
      output.push(/^<(?:ul|ol|li)\b/i.test(inline) ? inline : `<p>${inline}</p>`);
    }

    closeList();
    if (codeOpen) output.push(`<pre><code>${escapeHtml(codeLines.join("\n"))}</code></pre>`);
    return output.join("");
  }

  return { renderMarkdown };
});
