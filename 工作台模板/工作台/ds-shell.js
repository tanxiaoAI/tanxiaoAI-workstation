/**
 * DS 版工作台的顶栏外壳。
 *
 * 这份代码属于**工作台**（`工作台模板/工作台/`），不属于 Harness 插件。
 * 插件只负责把它和 `ds-shell.css` 注入进来，所以改导航、改那几个按钮
 * 都不用重装插件——重新装一份工作台就够了。
 *
 * 三种情况，按这个顺序判：
 *   1. 地址里带 `embed=1`（抽屉里的详情页）→ 不出导航，把侧栏收掉，正文占满
 *   2. 没有 `data-dsb-host`（浏览器直接打开）→ 什么都不做，保持原来的左侧栏
 *   3. 其余（DS 版主界面，插件打了 host 标记）→ 把侧栏改成顶部一条
 */
(function () {
  var root = document.documentElement;

  // 抽屉自己带 `embed=1`（app.js 打开 content.html / topic.html 时加的），只有它才是
  // 「只要正文」的那一种。**不能**用 `window.top !== window.self` 判断 —— DS 版的主界面
  // 本身也跑在插件的 iframe 里，那样会把主界面的导航一起吃掉（2026-11-12 踩过：
  // 装完看到的是没有导航的工作台）。
  if (/(?:^\?|&)embed=1(?:&|$)/.test(location.search || '')) {
    root.setAttribute('data-dsb-embed', '1');
    return;
  }
  if (!root.hasAttribute('data-dsb-host')) return;

  root.setAttribute('data-dsb-layout', 'top');

  /* ---------------------------- 顶部导航里的「更新」 ---------------------------- */
  var API_BASE = '/selfmedia-ds/api';
  var updateBusy = false;

  function mk(tag, cls, text) {
    var node = document.createElement(tag);
    if (cls) node.className = cls;
    if (text != null) node.textContent = text;
    return node;
  }

  function closeUpdate() {
    var mask = document.getElementById('dsb-update-mask');
    if (mask) mask.parentNode.removeChild(mask);
  }

  function paint(nodes) {
    var body = document.getElementById('dsb-update-body');
    if (!body) return;
    body.textContent = '';
    for (var i = 0; i < nodes.length; i += 1) body.appendChild(nodes[i]);
  }

  function metaRow(label, value) {
    var line = mk('div', 'dsb-up-row');
    line.appendChild(mk('span', null, label));
    line.appendChild(mk('span', null, value));
    return line;
  }

  function runUpdate(button) {
    if (updateBusy) return;
    updateBusy = true;
    button.disabled = true;
    paint([mk('p', null, '正在下载并替换文件，中间别关这个页面，大概半分钟。')]);
    fetch(API_BASE + '/update/apply', { method: 'POST' })
      .then(function (response) { return response.json(); })
      .then(function (data) {
        updateBusy = false;
        if (!data.ok) {
          var nodes = [mk('p', 'dsb-up-err', data.message || '更新没成功')];
          if (data.output) nodes.push(mk('pre', 'dsb-up-pre', data.output));
          paint(nodes);
          return;
        }
        paint([mk('p', null, '更新完成，正在重新加载…')]);
        setTimeout(function () { window.location.reload(); }, 1200);
      })
      .catch(function (error) {
        updateBusy = false;
        paint([mk('p', 'dsb-up-err', String(error && error.message ? error.message : error))]);
      });
  }

  function checkUpdate() {
    paint([mk('p', null, '正在检查…')]);
    fetch(API_BASE + '/update/check', { headers: { accept: 'application/json' } })
      .then(function (response) { return response.json(); })
      .then(function (data) {
        if (!data.ok) throw new Error(data.message || '检查失败');
        var nodes = [
          metaRow('当前版本', data.currentVersion),
          metaRow('线上版本', data.latestVersion || '未知'),
        ];
        if (data.hasUpdate) {
          var button = mk('button', 'dsb-up-btn', '更新到 ' + data.latestVersion);
          button.type = 'button';
          button.addEventListener('click', function () { runUpdate(button); });
          nodes.push(button);
          nodes.push(mk('p', 'dsb-up-hint', '只替换工具层，你的资料和本机配置不动，更新前会自动备份。'));
        } else {
          nodes.push(mk('p', 'dsb-up-hint', '已经是最新版本。'));
        }
        paint(nodes);
      })
      .catch(function (error) {
        paint([mk('p', 'dsb-up-err', String(error && error.message ? error.message : error))]);
      });
  }

  function openUpdate() {
    if (document.getElementById('dsb-update-mask')) {
      closeUpdate();
      return;
    }
    var mask = mk('div', 'dsb-up-mask');
    mask.id = 'dsb-update-mask';
    var panel = mk('div', 'dsb-up-panel');
    var head = mk('div', 'dsb-up-head');
    head.appendChild(mk('b', null, '工作台更新'));
    var close = mk('button', 'dsb-up-x', '×');
    close.type = 'button';
    close.setAttribute('aria-label', '关闭');
    close.addEventListener('click', closeUpdate);
    head.appendChild(close);
    var body = mk('div', 'dsb-up-body');
    body.id = 'dsb-update-body';
    panel.appendChild(head);
    panel.appendChild(body);
    mask.appendChild(panel);
    mask.addEventListener('click', function (event) {
      if (event.target === mask) closeUpdate();
    });
    document.body.appendChild(mask);
    checkUpdate();
  }

  function injectUpdateButton() {
    var foot = document.querySelector('.sidebar-foot');
    if (!foot || foot.querySelector('[data-dsb-update]')) return;
    var button = document.createElement('button');
    button.type = 'button';
    button.className = 'foot-item';
    button.setAttribute('data-dsb-update', '');
    button.innerHTML =
      '<span class="foot-icon" aria-hidden="true"><svg viewBox="0 0 24 24">' +
      '<path d="M20 11a8 8 0 1 0 2 5.2"/><path d="M20 4v7h-7"/></svg></span>' +
      '<span class="foot-label">更新</span>';
    button.addEventListener('click', openUpdate);
    foot.appendChild(button);
  }

  /* ------------------ 导航：九个视图按组收成三个下拉 ------------------
     真源的 HTML 不动：把 .primary-nav 里的 .nav-item 按原来的三组
     （账号管理 / 内容洞察 / 内容创作）重新排成「触发项 + 面板」。
     每条菜单项都保留 data-view / data-nav-key 和 href，
     所以 app.js 那条委托的点击（navigate）和详情页的跳转都照旧。 */
  var NAV_GROUPS = [
    ['账号管理', ['media-plan', 'own-accounts']],
    ['内容洞察', ['benchmark-accounts-v2', 'benchmark-contents', 'patterns']],
    ['内容创作', ['topic-search', 'topics', 'creation-board']],
  ];

  var ICON = {
    caret:
      '<svg class="trigger-caret" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="m6 9.5 6 6 6-6"/></svg>',
    folder:
      '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M3 7.5A1.5 1.5 0 0 1 4.5 6h4.2l1.9 2H19a1.5 1.5 0 0 1 1.5 1.5v8A1.5 1.5 0 0 1 19 19H4.5A1.5 1.5 0 0 1 3 17.5v-10Z"/></svg>',
    plus:
      '<svg viewBox="0 0 24 24"><path d="M12 5.5v13M5.5 12h13"/></svg>',
    check:
      '<svg class="ws-check" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><path d="m5 12.5 4.5 4.5L19 7"/></svg>',
  };

  function rebuildNav() {
    var nav = document.querySelector('.primary-nav');
    if (!nav || nav.getAttribute('data-dsb-nav') === 'dropdown') return;
    var nodes = [].slice.call(nav.querySelectorAll('.nav-item'));
    if (!nodes.length) return;

    var info = {};
    nodes.forEach(function (node) {
      var key = node.getAttribute('data-view') || node.getAttribute('data-nav-key') || '';
      var label = node.querySelector('.nav-label');
      var icon = node.querySelector('.nav-icon');
      info[key] = {
        label: (label || node).textContent.trim(),
        icon: icon ? icon.innerHTML : '',
        href: node.getAttribute('href') || '',
        tag: node.tagName.toLowerCase(),
      };
    });

    var active = nav.querySelector('.nav-item.is-active');
    var current =
      (active && (active.getAttribute('data-view') || active.getAttribute('data-nav-key'))) ||
      document.documentElement.getAttribute('data-nav') ||
      'home';

    // 详情页的导航是 <a href>，首页是 <button data-view>，保持一致，别把跳转弄丢
    function makeItem(key, cls) {
      var meta = info[key];
      if (!meta) return null;
      var node = document.createElement(meta.tag === 'a' ? 'a' : 'button');
      if (meta.tag === 'a') {
        node.setAttribute('href', meta.href);
        node.setAttribute('data-nav-key', key);
      } else {
        node.type = 'button';
        node.setAttribute('data-view', key);
      }
      node.className = cls + (key === current ? ' is-active' : '');
      node.innerHTML =
        '<span class="nav-icon" aria-hidden="true">' + meta.icon + '</span>' +
        '<span>' + meta.label + '</span>';
      return node;
    }

    var frag = document.createDocumentFragment();

    var home = makeItem('home', 'nav-item nav-home');
    if (home) frag.appendChild(home);

    NAV_GROUPS.forEach(function (group) {
      var name = group[0];
      var keys = group[1].filter(function (k) { return info[k]; });
      if (!keys.length) return;

      var wrap = document.createElement('div');
      wrap.className = 'nav-menu';

      var trigger = document.createElement('button');
      trigger.type = 'button';
      trigger.className = 'nav-trigger';
      trigger.setAttribute('aria-haspopup', 'true');
      trigger.innerHTML = '<span>' + name + '</span>' + ICON.caret;
      wrap.appendChild(trigger);

      var menu = document.createElement('div');
      menu.className = 'menu';
      menu.setAttribute('role', 'menu');
      keys.forEach(function (key) {
        var item = makeItem(key, 'menu-item');
        if (item) menu.appendChild(item);
      });
      wrap.appendChild(menu);

      // 点完菜单项就让面板收起（悬停本来离开就收，这里补点击那一下）
      menu.addEventListener('click', function () {
        wrap.classList.remove('is-open');
      });
      trigger.addEventListener('click', function (event) {
        event.stopPropagation();
        var open = wrap.classList.contains('is-open');
        nav.querySelectorAll('.nav-menu.is-open').forEach(function (x) {
          x.classList.remove('is-open');
        });
        if (!open) wrap.classList.add('is-open');
      });

      frag.appendChild(wrap);
    });

    nav.textContent = '';
    nav.appendChild(frag);
    nav.setAttribute('data-dsb-nav', 'dropdown');
  }

  /* ------------------ 右侧：设置 / 技能 / 更新 ------------------
     窄屏会把文字收掉只留图标，所以先把文字补成 title / aria-label。 */
  function labelFootItems() {
    var items = document.querySelectorAll('.sidebar-foot .foot-item');
    for (var i = 0; i < items.length; i += 1) {
      var item = items[i];
      if (item.getAttribute('aria-label')) continue;
      var text = (item.textContent || '').trim();
      if (!text) continue;
      item.setAttribute('aria-label', text);
      item.title = text;
    }
  }

  /* ------------------ 工作区下拉：已识别的工作区 + 新增 ------------------ */
  function workspaceMenu() {
    var menu = document.getElementById('dsb-ws-menu');
    if (menu) return menu;
    var foot = document.querySelector('.sidebar-foot');
    if (!foot) return null;

    menu = document.createElement('div');
    menu.className = 'ws-menu';
    menu.id = 'dsb-ws-menu';
    menu.innerHTML =
      '<button type="button" class="dsb-nav-ws">' + ICON.folder + '<b></b>' + ICON.caret + '</button>' +
      '<div class="ws-panel"></div>';
    foot.appendChild(menu);

    var trigger = menu.querySelector('.dsb-nav-ws');
    trigger.addEventListener('click', function (event) {
      event.stopPropagation();
      var open = menu.classList.contains('is-open');
      if (!open) paintWorkspaceMenu(true);
      menu.classList.toggle('is-open', !open);
    });

    document.addEventListener('click', function (event) {
      if (!menu.contains(event.target)) menu.classList.remove('is-open');
    });

    return menu;
  }

  function wsRow(item, currentRoot) {
    var isCurrent = item.path === currentRoot;
    var btn = document.createElement('button');
    btn.type = 'button';
    btn.className = 'ws-row' + (isCurrent ? ' is-current' : '');
    btn.setAttribute('title', item.path);
    btn.innerHTML =
      '<span class="ws-ico">' + ICON.folder + '</span>' +
      '<span class="ws-text"><b class="ws-name">' + item.name + '</b>' +
      '<i class="ws-path">' + item.path + '</i></span>' +
      (isCurrent ? ICON.check : '');
    btn.addEventListener('click', function () {
      menu_(false);
      if (isCurrent) return;
      try {
        window.parent.postMessage(
          { source: 'selfmedia-ds', type: 'switch-workspace', path: item.path },
          '*',
        );
      } catch (e) {}
    });
    return btn;
  }

  function menu_(keep) {
    var menu = document.getElementById('dsb-ws-menu');
    if (menu && !keep) menu.classList.remove('is-open');
  }

  function addRow() {
    var btn = document.createElement('button');
    btn.type = 'button';
    btn.className = 'ws-row ws-add';
    btn.innerHTML =
      '<span class="ws-ico">' + ICON.plus + '</span>' +
      '<span class="ws-text"><b class="ws-name">新增工作区</b>' +
      '<i class="ws-path">选一个文件夹…</i></span>';
    btn.addEventListener('click', function () {
      menu_(false);
      try {
        window.parent.postMessage({ source: 'selfmedia-ds', type: 'switch-workspace' }, '*');
      } catch (e) {}
    });
    return btn;
  }

  function paintWorkspaceMenu(force) {
    var menu = workspaceMenu();
    if (!menu) return;
    if (!force && menu.getAttribute('data-ws-ready') === '1') return;

    Promise.all([
      fetch(API_BASE + '/workspaces', { headers: { accept: 'application/json' } }).then(function (r) {
        return r.json();
      }).catch(function () { return { items: [] }; }),
      fetch(API_BASE + '/workspace', { headers: { accept: 'application/json' } }).then(function (r) {
        return r.json();
      }).catch(function () { return {}; }),
    ]).then(function (results) {
      var items = (results[0] && results[0].items) || [];
      var state = results[1] || {};
      var current = String(state.workspaceRoot || '');
      var parts = current.split('/').filter(Boolean);
      var name = parts.length ? parts[parts.length - 1] : '没选工作区';

      var label = menu.querySelector('.dsb-nav-ws b');
      if (label) label.textContent = name;
      var trigger = menu.querySelector('.dsb-nav-ws');
      if (trigger) {
        trigger.title = current
          ? '当前工作区：' + current + '（点一下换一个）'
          : '还没指向工作区';
      }

      var panel = menu.querySelector('.ws-panel');
      if (!panel) return;
      panel.textContent = '';
      var list = items.slice(0, 6);
      if (!list.length && current) list = [{ name: name, path: current }];
      list.forEach(function (item) {
        panel.appendChild(wsRow(item, current));
      });
      var sep = document.createElement('div');
      sep.className = 'ws-sep';
      panel.appendChild(sep);
      panel.appendChild(addRow());

      menu.setAttribute('data-ws-ready', '1');
    });
  }

  /**
   * 点面板外面、按 Esc，都要收起来。
   * 光靠 is-open 自己不会消失——一直停在那儿会挡住下面的内容。
   */
  function closeAllMenus(blurTrigger) {
    document.querySelectorAll('.primary-nav .nav-menu.is-open').forEach(function (node) {
      node.classList.remove('is-open');
    });
    // 焦点还留在触发按钮上时，:focus-within 会让面板继续显示，得一起撤掉。
    if (!blurTrigger) return
    var active = document.activeElement
    if (active && active.closest && active.closest('.primary-nav .nav-menu')) active.blur()
  }

  function startMenuDismiss() {
    if (document.documentElement.getAttribute('data-dsb-menu-dismiss') === '1') return
    document.documentElement.setAttribute('data-dsb-menu-dismiss', '1')

    document.addEventListener(
      'click',
      function (event) {
        var target = event.target
        if (target && target.closest && target.closest('.nav-menu')) return
        closeAllMenus(true)
      },
      true,
    )
    document.addEventListener('keydown', function (event) {
      if (event.key === 'Escape') closeAllMenus(false)
    })
  }

  function decorateNav() {
    rebuildNav();
    startMenuDismiss();
    injectUpdateButton();
    labelFootItems();
    workspaceMenu();
    paintWorkspaceMenu(false);
  }

  function startNavDecor() {
    decorateNav();
    // 切视图时侧栏可能被重绘，隔一会儿补一次；都是幂等的。
    setInterval(decorateNav, 1500);
  }
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', startNavDecor, { once: true });
  } else {
    startNavDecor();
  }
})();
