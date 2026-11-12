"use strict";

/* ================================================================
   调查问卷（可复用模块）
   · 固定 7 题题库；不读取账号资料、不调用 AI、不使用外部 API
   · 独立页 survey.html 承担首次填写与后续修改；账号方向页只显示摘要和入口
   · 修改时由 survey.js 读回 我方资料/调查问卷.md，用 parse() 还原成选项再回填
   ================================================================ */

(function () {
  const MAX_MULTI = 3;

  const QUESTIONS = [
    {
      id: "q1",
      title: "你希望账号主要围绕哪个领域做内容？",
      type: "single",
      options: [
        "知识与教育",
        "职场与职业发展",
        "商业与创业",
        "AI 与软件工具",
        "科技数码",
        "商品测评与消费建议",
        "美食",
        "美妆与穿搭",
        "运动与健康",
        "母婴与育儿",
        "家居与生活",
        "情感与人际关系",
        "旅行与户外",
        "本地生活",
        "娱乐与文化",
        "其他",
        "还没想清楚",
      ],
      extra: {
        otherValue: "其他",
        hideFor: ["还没想清楚"],
        optionalText: "可以补充更细的方向，例如：AI 自媒体工具、餐饮店经营。",
        requiredText: "请填写你的领域，例如：法律科普、农业种植。",
      },
    },
    {
      id: "q2",
      title: "你有哪些可以持续用于内容的真实积累？",
      type: "multi",
      max: MAX_MULTI,
      options: [
        "专业知识或职业资质",
        "工作经验或实际操作经验",
        "真实案例、作品或数据",
        "自己的产品或服务",
        "可以持续展示的工作、经营或生活过程",
        "已有用户、客户或社群反馈",
        "个人经历或兴趣积累",
        "其他",
        "暂时没有明确积累，准备从真实实践和记录开始",
      ],
      extra: {
        otherValue: "其他",
        hideFor: ["暂时没有明确积累，准备从真实实践和记录开始"],
        optionalText: "可以举一个具体例子，例如：做了五年财务、经营一家餐饮店、开发了一款 AI 工具。",
        requiredText: "请简单说明你的积累。",
      },
    },
    {
      id: "q3",
      title: "你最希望哪类人持续关注你？",
      type: "single",
      options: [
        "在校学生",
        "刚进入职场的人",
        "已有工作经验的从业者",
        "管理者或团队负责人",
        "自由职业者或个人创作者",
        "创业者或个体商家",
        "企业业务人员",
        "家长或家庭照顾者",
        "某个领域的兴趣爱好者",
        "有具体购买或消费需求的人",
        "其他人群",
        "还没想清楚",
      ],
      extra: {
        otherValue: "其他人群",
        hideFor: ["还没想清楚"],
        optionalText: "可以说得更具体，例如：刚开始带团队的主管、已经使用 AI 的知识类创作者。",
        requiredText: "请简单描述这类人。",
      },
    },
    {
      id: "q4",
      title: "你希望大家看完你的内容，能得到什么？",
      type: "multi",
      max: MAX_MULTI,
      options: [
        "解决问题：解决工作或生活中遇到的具体困难",
        "学到方法：学会一种知识、技能或可以照着做的方法",
        "获得信息和建议：了解新信息，帮助比较、选择和判断",
        "找到工具和资源：发现好用的工具、资料或产品",
        "收获快乐和放松：觉得有趣、开心，缓解压力",
        "获得共鸣和陪伴：感到被理解、被鼓励，或有人一起成长",
        "其他",
        "还没想清楚",
      ],
      extra: {
        otherValue: "其他",
        hideFor: ["还没想清楚"],
        optionalText: "可以补充一个具体例子：你最想帮大家解决什么，或带来什么？",
        requiredText: "请简单说明你希望带来的价值。",
      },
    },
    {
      id: "q5",
      title: "你现在做这个账号，最想先达到什么目标？",
      type: "single",
      options: [
        "获取流量：让更多人看到我的内容",
        "吸引粉丝：让对这类内容感兴趣的人关注我",
        "建立信任：让大家更了解我、相信我的专业或产品",
        "带来生意：获得咨询、订单、到店或合作机会",
        "找到方向：看看自己适合做什么、大家愿意看什么",
        "还没想清楚",
      ],
    },
    {
      id: "q6",
      title: "你想通过这个账号怎么赚钱？",
      type: "multi",
      max: MAX_MULTI,
      options: [
        "卖自己的产品或商品",
        "提供咨询、服务或定制",
        "卖课程或知识产品",
        "给线下门店带来顾客",
        "接广告、品牌推广或商务合作",
        "暂时不考虑赚钱",
        "想赚钱，但还没确定方式",
        "其他方式",
      ],
      extra: {
        otherValue: "其他方式",
        hideFor: ["暂时不考虑赚钱", "想赚钱，但还没确定方式"],
        optionalText: "具体卖什么，或提供什么服务？简单写一句就行。",
        requiredText: "请简单说明你想通过什么方式赚钱。",
      },
    },
    {
      id: "q7",
      title: "你接下来愿意持续采用哪些内容形式？",
      type: "multi",
      max: MAX_MULTI,
      options: [
        "真人出镜口播",
        "屏幕录制或操作演示",
        "实拍工作、经营或生活过程",
        "图文内容",
        "素材画面配旁白",
        "剧情、对话或角色演绎",
        "直播或长内容拆条",
        "其他形式",
        "还没想清楚",
      ],
      extra: {
        otherValue: "其他形式",
        hideFor: ["还没想清楚"],
        optionalText: null,
        requiredText: "请简单说明你想采用的内容形式。",
      },
    },
  ];

  const SURVEY_ANSWERS_PATH = "我方资料/调查问卷.md";

  const maxOf = (question) => question.max || MAX_MULTI;

  function escapeHtml(value) {
    return String(value ?? "")
      .replaceAll("&", "&amp;")
      .replaceAll("<", "&lt;")
      .replaceAll(">", "&gt;")
      .replaceAll('"', "&quot;")
      .replaceAll("'", "&#39;");
  }

  /* 把服务端写出的 我方资料/调查问卷.md 读回成选项。
     格式由 server.mjs 的 buildSurveyMarkdown 固定生成：
     每题一个 `## N. 题目`，下面是 `- 选项` / `- 补充：xxx`；
     第 8 节是自由补充，末尾的 `>` 是文件说明。 */
  function parse(markdown) {
    const rawAnswers = {};
    const rawExtras = {};
    let supplement = "";
    let currentId = null;
    let inSupplement = false;

    for (const line of String(markdown || "").replace(/\r\n?/g, "\n").split("\n")) {
      const heading = line.match(/^##\s+(\d+)\.\s*(.*)$/);
      if (heading) {
        const index = Number(heading[1]) - 1;
        const question = QUESTIONS[index];
        currentId = question ? question.id : null;
        inSupplement = !question;
        if (currentId) rawAnswers[currentId] = [];
        continue;
      }
      if (inSupplement) {
        const text = line.trim();
        if (!text) continue;
        if (text.startsWith(">")) {
          inSupplement = false;
          continue;
        }
        supplement = supplement ? `${supplement}\n${text}` : text;
        continue;
      }
      if (!currentId) continue;
      const bullet = line.match(/^\s*[-*+]\s+(.*)$/);
      if (!bullet) continue;
      const value = bullet[1].trim();
      const extra = value.match(/^补充：\s*(.*)$/);
      if (extra) {
        rawExtras[currentId] = extra[1].trim();
        continue;
      }
      rawAnswers[currentId].push(value);
    }

    const answers = {};
    const extras = {};
    for (const question of QUESTIONS) {
      let values = (rawAnswers[question.id] || []).filter((value) => question.options.includes(value));
      if (question.type === "single") values = values.slice(0, 1);
      if (question.type === "multi") values = values.slice(0, maxOf(question));
      answers[question.id] = values;
      extras[question.id] = rawExtras[question.id] || "";
    }
    return {
      answers,
      extras,
      supplement: supplement === "（未填写）" ? "" : supplement.slice(0, 200),
    };
  }

  function mount(options = {}) {
    const container = options.container;
    if (!container) return null;
    const variant = options.variant === "embed" ? "embed" : "page";
    const editing = Boolean(options.editing);
    const idleLabel = editing ? "保存修改" : "完成问卷";
    const storageKey = options.storageKey || "meiling:first-survey:v2";
    const endpoint = options.endpoint || "/api/onboarding-survey";
    const uid = `survey-${Math.random().toString(36).slice(2, 8)}`;
    const state = { answers: {}, extras: {}, supplement: "", submitted: false };
    /* 打开抽屉那一刻的答案。改没改、改完又改回去，都拿它比 */
    let pristine = "";
    let submitting = false;

    container.classList.add("survey-app", `survey-app--${variant}`);
    container.innerHTML = `${variant === "page" ? `
      <section class="survey-hero">
        <h1>${editing ? "修改调查问卷" : "开始前，先说说你想怎么做这个账号"}</h1>
        <p>${editing
          ? "下面是上次提交的答案，改完点「保存修改」。保存后回账号方向页，可以按新问卷更新方向，并重新诊断账号。"
          : "这些选择，决定AI之后如何分析你的账号、给你找什么选题、生产什么样的内容，还没想清楚的地方，可以选择“还没想清楚”，之后还能修改"}</p>
      </section>` : ""}
      <div class="survey-progress" role="status" aria-live="polite">
        <div class="survey-progress-copy">
          <span class="survey-progress-k">已完成</span>
          <b data-progress-done>0</b><span class="survey-progress-total">/ 7 题</span>
        </div>
        <div class="survey-segments" data-progress-segments></div>
      </div>
      <div class="survey-status is-hidden" data-status role="alert"></div>
      <form class="survey-form" novalidate>
        <section class="survey-sheet">
          <div data-questions></div>
          <div class="survey-supplement">
            <h2>还有什么需要让我们知道？<span class="survey-tag is-qa">问答</span><span class="survey-supplement-note">选填</span></h2>
            <p>可补充具体服务的人群、细分方向、产品或服务名称，以及明确不愿做的内容。</p>
            <textarea rows="4" maxlength="200" placeholder="可以补充具体服务的人群、细分方向、产品或服务名称，以及明确不愿做的内容。没有可以留空。"></textarea>
            <div class="survey-textarea-meta"><span data-supplement-count>0 / 200</span></div>
          </div>
        </section>
        <div class="survey-submit">
          <button class="survey-primary" type="submit"${editing ? " disabled" : ""}><span data-submit-label>${idleLabel}</span></button>
          <p>${editing ? "保存后会覆盖这份基础档案，之后可以在账号方向页更新方向和重新诊断。" : "根据这些选择整理基础规划，之后随时可以改。"}</p>
        </div>
      </form>
      <div class="survey-modal is-hidden" data-modal role="dialog" aria-modal="true" aria-labelledby="${uid}-modal-title">
        <div class="survey-modal-card">
          <span class="survey-modal-icon" aria-hidden="true"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2"><path d="m5 12.5 4.5 4.5L19 7.5" stroke-linecap="round" stroke-linejoin="round"/></svg></span>
          <h2 id="${uid}-modal-title">${editing ? "问卷已更新" : "问卷已提交"}</h2>
          <p>${editing
            ? "调查问卷已保存。回账号方向页，可以按新问卷更新方向，并重新诊断账号。"
            : "回到对话窗口说一声，我会按这些选择整理你的基础规划。之后想改，随时回来修改。"}</p>
          <button class="survey-primary" type="button" data-modal-close>${editing ? "关闭" : "知道了"}</button>
          <a class="survey-modal-link" href="index.html#media-plan">去账号方向页</a>
        </div>
      </div>`;

    const el = {
      form: container.querySelector(".survey-form"),
      questions: container.querySelector("[data-questions]"),
      progressDone: container.querySelector("[data-progress-done]"),
      segments: container.querySelector("[data-progress-segments]"),
      status: container.querySelector("[data-status]"),
      supplement: container.querySelector(".survey-supplement textarea"),
      supplementCount: container.querySelector("[data-supplement-count]"),
      submit: container.querySelector(".survey-primary"),
      submitLabel: container.querySelector("[data-submit-label]"),
      modal: container.querySelector("[data-modal]"),
      modalClose: container.querySelector("[data-modal-close]"),
    };

    function questionNumber(question) {
      return QUESTIONS.findIndex((item) => item.id === question.id) + 1;
    }

    function isExclusive(question, option) {
      return Boolean(question.extra && (question.extra.hideFor || []).includes(option));
    }

    function renderOption(question, option, index) {
      const selected = state.answers[question.id] || [];
      const checked = selected.includes(option);
      const atMax = question.type === "multi" && selected.length >= maxOf(question);
      const type = question.type === "multi" ? "checkbox" : "radio";
      const inputId = `${uid}-${question.id}-${index}`;
      const exclusive = isExclusive(question, option);
      // 多选题选满上限时，未选中的选项禁用；「还没想清楚」这类边界选项始终可选。
      const disabled = question.type === "multi" && !checked && atMax && !exclusive;
      const classes = [
        "survey-option",
        question.type === "multi" ? "is-multi" : "",
        checked ? "is-selected" : "",
        exclusive ? "is-exclusive" : "",
        disabled ? "is-disabled" : "",
      ].filter(Boolean).join(" ");
      return `<label class="${classes}" for="${inputId}">
        <input id="${inputId}" type="${type}" name="${question.id}" value="${escapeHtml(option)}"${checked ? " checked" : ""}${disabled ? " disabled" : ""}>
        <span class="survey-option-mark" aria-hidden="true"></span>
        <span class="survey-option-text">${escapeHtml(option)}</span>
      </label>`;
    }

    function renderExtra(question) {
      if (!question.extra) return "";
      return `<div class="survey-extra is-hidden" data-extra="${question.id}">
        <label class="survey-extra-label" for="${uid}-${question.id}-extra">补充说明（选填）</label>
        <input id="${uid}-${question.id}-extra" type="text" maxlength="120" data-extra-input="${question.id}" value="${escapeHtml(state.extras[question.id] || "")}" placeholder="">
      </div>`;
    }

    function countText(question) {
      const selected = state.answers[question.id] || [];
      const max = maxOf(question);
      if (!selected.length) return `最多 ${max} 项`;
      return `已选 ${selected.length} / ${max}${selected.length >= max ? " · 已选满" : ""}`;
    }

    function renderQuestion(question) {
      const number = questionNumber(question);
      const meta = question.type === "multi"
        ? `<span class="survey-tag is-multi">多选</span><span class="survey-q-count" data-survey-count="${question.id}">${countText(question)}</span>`
        : `<span class="survey-tag is-single">单选</span>`;
      const options = question.options.map((option, index) => renderOption(question, option, index)).join("");
      return `<article class="survey-question" data-question="${question.id}" id="${uid}-${question.id}">
        <header class="survey-question-head">
          <span class="survey-q-index" aria-hidden="true">Q${number}</span>
          <div class="survey-q-copy">
            <h3 class="survey-q-title">${escapeHtml(question.title)}${meta}</h3>
          </div>
        </header>
        <div class="survey-options">${options}</div>
        ${renderExtra(question)}
      </article>`;
    }

    function renderAll() {
      el.questions.innerHTML = QUESTIONS.map((question) => renderQuestion(question)).join("");
    }

    function syncQuestion(question) {
      const node = container.querySelector(`[data-question="${question.id}"]`);
      if (!node) return;
      const selected = state.answers[question.id] || [];
      const atMax = question.type === "multi" && selected.length >= maxOf(question);
      node.querySelectorAll(".survey-options input").forEach((input) => {
        const checked = selected.includes(input.value);
        input.checked = checked;
        input.disabled = question.type === "multi" && !checked && atMax && !isExclusive(question, input.value);
        const label = input.closest(".survey-option");
        label?.classList.toggle("is-selected", checked);
        label?.classList.toggle("is-disabled", input.disabled);
      });
      const count = node.querySelector("[data-survey-count]");
      if (count) count.textContent = countText(question);
      node.classList.remove("is-missing");
      syncExtra(question);
    }

    function syncExtra(question) {
      if (!question.extra) return;
      const node = container.querySelector(`[data-extra="${question.id}"]`);
      if (!node) return;
      const selected = state.answers[question.id] || [];
      const otherSelected = selected.includes(question.extra.otherValue);
      const concreteSelected = selected.some((value) => value !== question.extra.otherValue && !(question.extra.hideFor || []).includes(value));
      const show = otherSelected || (Boolean(question.extra.optionalText) && concreteSelected);
      node.classList.toggle("is-hidden", !show);
      node.classList.toggle("is-required", otherSelected);
      const label = node.querySelector(".survey-extra-label");
      const input = node.querySelector("input");
      if (label) label.textContent = otherSelected ? "补充说明（必填）" : "补充说明（选填）";
      if (input) input.placeholder = otherSelected ? question.extra.requiredText : (question.extra.optionalText || "");
    }

    function isComplete(question) {
      const selected = state.answers[question.id] || [];
      if (!selected.length) return false;
      if (question.extra && selected.includes(question.extra.otherValue) && !(state.extras[question.id] || "").trim()) return false;
      return true;
    }

    function updateProgress() {
      const done = QUESTIONS.filter(isComplete).length;
      if (el.segments.children.length !== QUESTIONS.length) {
        el.segments.innerHTML = QUESTIONS.map(() => "<i></i>").join("");
      }
      el.progressDone.textContent = String(done);
      [...el.segments.children].forEach((segment, index) => {
        segment.classList.toggle("is-on", isComplete(QUESTIONS[index]));
      });
    }

    /* 当前答案的指纹。多选比较前先排序 —— 取消再勾回同一项，顺序可能变，
       但那不算改动。 */
    function snapshot() {
      return JSON.stringify({
        answers: QUESTIONS.map((question) => [question.id, [...(state.answers[question.id] || [])].sort()]),
        extras: QUESTIONS.map((question) => [question.id, (state.extras[question.id] || "").trim()]),
        supplement: String(el.supplement?.value || "").trim(),
      });
    }

    /* 只有「修改问卷」才需要这个：一次没动就点不了保存。
       首次填写不给锁 —— 缺题时要靠点提交把缺哪几题喊出来。 */
    function syncDirty() {
      if (!editing || submitting) return;
      const dirty = snapshot() !== pristine;
      el.submit.disabled = !dirty;
      el.submit.classList.toggle("is-idle", !dirty);
    }

    function saveLocal() {
      try {
        localStorage.setItem(storageKey, JSON.stringify({ answers: state.answers, extras: state.extras, supplement: state.supplement }));
      } catch (_error) {
        // 浏览器禁用存储时只影响「关掉页面后能不能接着填」，不影响提交
      }
    }

    function restoreLocal() {
      let saved = null;
      try {
        saved = JSON.parse(localStorage.getItem(storageKey) || "null");
      } catch (_error) {
        saved = null;
      }
      if (!saved || typeof saved !== "object") return;
      const rawAnswers = saved.answers && typeof saved.answers === "object" ? saved.answers : {};
      const rawExtras = saved.extras && typeof saved.extras === "object" ? saved.extras : {};
      for (const question of QUESTIONS) {
        const raw = rawAnswers[question.id];
        let values = Array.isArray(raw) ? raw.filter((value) => typeof value === "string") : [];
        values = values.filter((value) => question.options.includes(value));
        if (question.type === "single") values = values.slice(0, 1);
        if (question.type === "multi") values = values.slice(0, maxOf(question));
        state.answers[question.id] = values;
        state.extras[question.id] = typeof rawExtras[question.id] === "string" ? rawExtras[question.id].slice(0, 120) : "";
      }
      state.supplement = typeof saved.supplement === "string" ? saved.supplement.slice(0, 200) : "";
    }

    /* 修改问卷时，以 我方资料/调查问卷.md 为准回填；本地草稿只用于首次填写时续填。
       返回 true 表示这份档案已经有答案，不用再看本地草稿。 */
    function applyInitial(initial) {
      if (!initial || typeof initial !== "object") return false;
      let filled = false;
      for (const question of QUESTIONS) {
        const raw = initial.answers?.[question.id];
        let values = Array.isArray(raw)
          ? raw.filter((value) => typeof value === "string" && question.options.includes(value))
          : [];
        if (question.type === "single") values = values.slice(0, 1);
        if (question.type === "multi") values = values.slice(0, maxOf(question));
        state.answers[question.id] = values;
        const extra = initial.extras?.[question.id];
        state.extras[question.id] = typeof extra === "string" ? extra.slice(0, 120) : "";
        if (values.length) filled = true;
      }
      state.supplement = typeof initial.supplement === "string" ? initial.supplement.slice(0, 200) : "";
      return filled;
    }

    function showStatus(kind, text) {
      el.status.className = `survey-status is-${kind}`;
      el.status.textContent = text;
    }

    function hideStatus() {
      el.status.className = "survey-status is-hidden";
      el.status.textContent = "";
    }

    function openModal() {
      el.modal.classList.remove("is-hidden");
      el.modalClose.focus();
    }

    function closeModal() {
      el.modal.classList.add("is-hidden");
    }

    function onModalKeydown(event) {
      if (event.key === "Escape" && !el.modal.classList.contains("is-hidden")) closeModal();
    }

    el.modalClose.addEventListener("click", closeModal);
    el.modal.addEventListener("click", (event) => {
      if (event.target === el.modal) closeModal();
    });
    document.addEventListener("keydown", onModalKeydown);

    function resetSubmitted() {
      if (!state.submitted) return;
      state.submitted = false;
      el.submitLabel.textContent = idleLabel;
      hideStatus();
    }

    function applyChange(question, input) {
      resetSubmitted();
      let values = [...(state.answers[question.id] || [])];
      if (question.type === "single") {
        values = input.checked ? [input.value] : [];
      } else if (input.checked) {
        if (!values.includes(input.value)) values.push(input.value);
        if (values.length > maxOf(question)) values = values.slice(0, maxOf(question));
      } else {
        values = values.filter((value) => value !== input.value);
      }
      state.answers[question.id] = values;
      syncQuestion(question);
      updateProgress();
      saveLocal();
      syncDirty();
    }

    el.form.addEventListener("change", (event) => {
      const input = event.target.closest('.survey-options input[name^="q"]');
      if (!input) return;
      const question = QUESTIONS.find((item) => item.id === input.name);
      if (!question) return;
      applyChange(question, input);
    });

    el.form.addEventListener("input", (event) => {
      const input = event.target.closest("[data-extra-input]");
      if (!input) return;
      const id = input.dataset.extraInput;
      state.extras[id] = input.value.slice(0, 120);
      const node = container.querySelector(`[data-question="${id}"]`);
      if ((input.value || "").trim()) node?.classList.remove("is-missing");
      updateProgress();
      saveLocal();
      resetSubmitted();
      syncDirty();
    });

    el.supplement.addEventListener("input", () => {
      state.supplement = el.supplement.value;
      el.supplementCount.textContent = `${state.supplement.length} / 200`;
      saveLocal();
      resetSubmitted();
      syncDirty();
    });

    el.form.addEventListener("submit", async (event) => {
      event.preventDefault();
      const missing = QUESTIONS.filter((question) => (state.answers[question.id] || []).length === 0);
      const missingExtra = QUESTIONS.filter((question) => {
        if (!question.extra) return false;
        const selected = state.answers[question.id] || [];
        return selected.includes(question.extra.otherValue) && !(state.extras[question.id] || "").trim();
      });
      if (missing.length || missingExtra.length) {
        [...missing, ...missingExtra].forEach((question) => container.querySelector(`[data-question="${question.id}"]`)?.classList.add("is-missing"));
        const parts = [];
        if (missing.length) parts.push(`还有 ${missing.length} 题没有完成：${missing.map((question) => `Q${questionNumber(question)}`).join("、")}`);
        if (missingExtra.length) parts.push(`${missingExtra.map((question) => `Q${questionNumber(question)}`).join("、")} 选择「${missingExtra[0].extra.otherValue}」后需要填写具体内容`);
        showStatus("error", `${parts.join("；")}。其他已填内容已经保留，补完再提交。`);
        const first = missing[0] || missingExtra[0];
        container.querySelector(`[data-question="${first.id}"]`)?.scrollIntoView({ behavior: "smooth", block: "center" });
        return;
      }

      const payload = {
        submitted_at: new Date().toISOString(),
        answers: QUESTIONS.map((question) => ({
          id: question.id,
          question: question.title,
          values: state.answers[question.id] || [],
          extra: (state.extras[question.id] || "").trim(),
        })),
        supplement: el.supplement.value.trim(),
      };

      el.submit.disabled = true;
      el.submit.classList.remove("is-idle");
      el.submitLabel.textContent = "正在提交…";
      submitting = true;
      try {
        const response = await fetch(endpoint, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(payload),
        });
        const data = await response.json().catch(() => null);
        if (!response.ok || !data?.ok) throw new Error(data?.error || `HTTP ${response.status}`);
        state.submitted = true;
        pristine = snapshot();
        showStatus("success", editing
          ? "调查问卷已更新。回账号方向页，可以按新问卷更新方向并重新诊断账号。"
          : "问卷已提交。回到对话窗口说一声，我会按这些选择整理你的基础规划。");
        el.submitLabel.textContent = editing ? "再次保存" : "重新提交";
        // 嵌在账号方向抽屉里时，由调用方收尾（关抽屉、刷新卡片），不再弹自己的成功弹窗
        if (typeof options.onSubmitted === "function") {
          options.onSubmitted(payload);
          return;
        }
        openModal();
      } catch (error) {
        showStatus("error", `提交失败：${error.message}。答案还留在这页，可以直接重试。`);
        el.submitLabel.textContent = idleLabel;
      } finally {
        submitting = false;
        el.submit.disabled = false;
        el.submit.classList.remove("is-idle");
        syncDirty();
      }
    });

    if (!applyInitial(options.initial) && !editing) restoreLocal();
    renderAll();
    el.supplement.value = state.supplement;
    el.supplementCount.textContent = `${state.supplement.length} / 200`;
    for (const question of QUESTIONS) syncExtra(question);
    updateProgress();
    pristine = snapshot();
    syncDirty();

    return {
      destroy() {
        document.removeEventListener("keydown", onModalKeydown);
        container.classList.remove("survey-app", `survey-app--${variant}`);
        container.innerHTML = "";
      },
    };
  }

  window.MeilingSurvey = { mount, parse, answersPath: SURVEY_ANSWERS_PATH };
})();
