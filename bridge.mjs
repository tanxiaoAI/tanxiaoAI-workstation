/**
 * 工作台桥接：把工作区里那一份 `工作台/server.mjs` 起成实例，再把它整套
 * 搬到宿主 webServer 的 `/selfmedia-ds/app/` 前缀下面。
 *
 * 为什么不在插件里重写界面：UI 和功能要跟开源版一模一样。工作区里本来就有
 * 完整的那一套（server.mjs + app.js + 所有样式），这里只做两件事：
 *   1. 起实例（复用它的 createDashboardServer，端口让系统分配）
 *   2. 反向代理 + 给 HTML 注入一段 base 前缀脚本
 * 工作台以后怎么改，这里都不用跟着改。
 */
import fs from 'node:fs/promises'
import http from 'node:http'
import path from 'node:path'
import { pathToFileURL } from 'node:url'

/** 挂载点。iframe 的地址就是 `${APP_BASE}/`。 */
export const APP_BASE = '/selfmedia-ds/app'
/** 注入脚本的地址（不走反代，单独一条精确路由）。 */
export const BRIDGE_SCRIPT = '/selfmedia-ds/bridge.js'
/** 页面里调用宿主动作用的回传口（iframe → 插件 host）。 */
export const ACTION_PATH = '/selfmedia-ds/action'
/** 工作台自己的顶栏脚本 / 样式（内容来自工作台模板，插件只转发）。 */
export const SHELL_SCRIPT = '/selfmedia-ds/shell.js'
export const SHELL_STYLE = '/selfmedia-ds/shell.css'
/** 把页面同源的 `/api/` 请求改到前缀下，再被反代剥回去。 */
export const INJECT_MARK = 'data-selfmedia-bridge'

const HOP_BY_HOP = new Set([
  'connection',
  'keep-alive',
  'proxy-authenticate',
  'proxy-authorization',
  'te',
  'trailer',
  'transfer-encoding',
  'upgrade',
])

function isInside(root, target) {
  const abs = path.resolve(root)
  const candidate = path.resolve(target)
  return candidate === abs || candidate.startsWith(abs + path.sep)
}

/* ------------------------------------------------------------ 起实例 */

/** DS 版工作台的身份证。开源版装出来的目录没有这个文件。 */
export const EDITION_MARK = path.join('工作台', '.ds-edition')
export const EDITION_ID = 'selfmedia-ds'

/**
 * 这个目录能不能给 DS 版用。三种结果，调用方要分别给一句人话：
 *   - ok                DS 版的工作台
 *   - missing_entry     什么工作台都没有（空目录，可以装一份）
 *   - other_product     有工作台，但不是 DS 版那套（开源版装出来的）
 */
export async function probeWorkspace(workspaceRoot) {
  // 还没选工作区。不往下拼路径，否则会拿相对路径去 stat，错得莫名其妙。
  if (!String(workspaceRoot || '').trim()) return { ok: false, code: 'no_workspace' }
  const entry = path.join(workspaceRoot, '工作台', 'server.mjs')
  try {
    const stat = await fs.stat(entry)
    if (!stat.isFile()) return { ok: false, code: 'not_a_file', entry }
  } catch {
    return { ok: false, code: 'missing_entry', entry }
  }
  const mark = path.join(workspaceRoot, EDITION_MARK)
  let id = ''
  try {
    id = (await fs.readFile(mark, 'utf8')).trim()
  } catch {
    id = ''
  }
  if (id !== EDITION_ID) return { ok: false, code: 'other_product', entry, mark }
  return { ok: true, entry }
}

/**
 * 起一个工作台实例。端口交给系统分配（0），只监听回环。
 * 宿主的 webServer 已经是唯一入口，这个端口只给本进程反代用，不对外。
 */
export async function startDashboard(workspaceRoot) {
  const probe = await probeWorkspace(workspaceRoot)
  if (!probe.ok) {
    const message =
      probe.code === 'no_workspace'
        ? '还没选工作区'
        : probe.code === 'other_product'
          ? `这个目录里装的是开源版的工作台，DS 版用自己的那份：${workspaceRoot}`
          : probe.code === 'missing_entry'
            ? `这个工作区里没有「工作台/server.mjs」：${workspaceRoot}`
            : `「工作台/server.mjs」不是文件：${probe.entry}`
    const error = new Error(message)
    error.code = probe.code
    throw error
  }

  // 带上时间戳：更新会覆盖 server.mjs，不带的话 import 缓存还是旧代码。
  const mod = await import(`${pathToFileURL(probe.entry).href}?t=${Date.now()}`)
  if (typeof mod.createDashboardServer !== 'function') {
    throw new Error('「工作台/server.mjs」没有导出 createDashboardServer，版本可能太旧')
  }

  const dashboard = mod.createDashboardServer({
    workspaceRoot,
    // 跟 launcher.mjs 保持一致：静态资源目录就是「工作台」本身，不是它的子目录。
    publicDir: path.join(workspaceRoot, '工作台'),
    host: '127.0.0.1',
    port: 0,
  })
  await dashboard.start()

  const address = new URL(dashboard.url)
  const port = Number(address.port)
  if (!Number.isFinite(port) || port <= 0) throw new Error('工作台没有拿到端口')

  return {
    workspaceRoot,
    port,
    dashboard,
    async close() {
      try {
        await dashboard.close()
      } catch {
        /* 关的时候出错不影响宿主退出 */
      }
    },
  }
}

/* ------------------------------------------------------------ 反向代理 */

function upstreamHeaders(req, port) {
  const headers = {}
  for (const [name, value] of Object.entries(req.headers)) {
    if (HOP_BY_HOP.has(name.toLowerCase())) continue
    if (name.toLowerCase() === 'host') continue
    if (value !== undefined) headers[name] = value
  }
  headers.host = `127.0.0.1:${port}`
  return headers
}

/** 只有这两类响应需要整段读出来改（HTML 注入）。其余一律流式透传。 */
function needsInjection(contentType) {
  return typeof contentType === 'string' && /text\/html/i.test(contentType)
}

function injectIntoHtml(html) {
  if (html.includes(INJECT_MARK)) return html
  const tag = `<script ${INJECT_MARK} src="${BRIDGE_SCRIPT}"></script>`
  const head = html.match(/<head[^>]*>/i)
  if (head) {
    const at = head.index + head[0].length
    return html.slice(0, at) + tag + html.slice(at)
  }
  const doc = html.match(/<html[^>]*>/i)
  if (doc) {
    const at = doc.index + doc[0].length
    return html.slice(0, at) + tag + html.slice(at)
  }
  return tag + html
}

/**
 * 把 `stripPrefix` 之后的路径转给上游。
 * SSE（/api/events）走的就是这条，不能攒、不能加 content-length。
 */
export function proxyRequest(req, res, { port, stripPrefix }) {
  const url = new URL(req.url ?? '/', 'http://127.0.0.1')
  let rest = url.pathname.slice(stripPrefix.length)
  if (rest === '') rest = '/'
  if (!rest.startsWith('/')) rest = `/${rest}`
  const target = `${rest}${url.search}`

  const upstream = http.request(
    {
      host: '127.0.0.1',
      port,
      method: req.method,
      path: target,
      headers: upstreamHeaders(req, port),
    },
    (upstreamRes) => {
      const contentType = upstreamRes.headers['content-type']
      const status = upstreamRes.statusCode ?? 502

      if (!needsInjection(contentType)) {
        const headers = { ...upstreamRes.headers }
        res.writeHead(status, headers)
        upstreamRes.pipe(res)
        upstreamRes.on('error', () => res.destroy())
        return
      }

      const chunks = []
      upstreamRes.on('data', (chunk) => chunks.push(chunk))
      upstreamRes.on('error', () => {
        if (!res.headersSent) res.writeHead(502, { 'cache-control': 'no-store' })
        res.end()
      })
      upstreamRes.on('end', () => {
        const html = injectIntoHtml(Buffer.concat(chunks).toString('utf8'))
        const body = Buffer.from(html, 'utf8')
        const headers = { ...upstreamRes.headers }
        for (const name of Object.keys(headers)) {
          if (HOP_BY_HOP.has(name.toLowerCase())) delete headers[name]
        }
        // 工作台的页面头写的是 style-src 'self'，内联样式会被直接丢弃。
        // 这个 iframe 里装的全是我们自己发的内容，放开这一项不影响别处。
        if (headers['content-security-policy']) {
          headers['content-security-policy'] = String(headers['content-security-policy']).replace(
            /style-src [^;]*/,
            "style-src 'self' 'unsafe-inline'",
          )
        }
        delete headers['content-length']
        headers['content-length'] = String(body.length)
        headers['cache-control'] = 'no-store'
        res.writeHead(status, headers)
        res.end(body)
      })
    },
  )

  upstream.on('error', (error) => {
    if (res.headersSent) {
      res.destroy()
      return
    }
    const text = JSON.stringify({ ok: false, error: 'upstream_failed', message: String(error?.message ?? error) })
    res.writeHead(502, {
      'content-type': 'application/json; charset=utf-8',
      'cache-control': 'no-store',
      'content-length': Buffer.byteLength(text),
    })
    res.end(text)
  })

  // GET/HEAD 没有 body，pipe 完自然会 end
  req.pipe(upstream)
  req.on('aborted', () => upstream.destroy())
}

/* -------------------------------------------------- 注入到页面的那段脚本 */

/**
 * 把工作台那条左侧导航改到顶部。
 *
 * 只覆盖样式，不动真源的 HTML：`.sidebar` 原本是 `position:fixed` + 靠
 * `--sidebar-width` 给主区留左边距，这里把那个变量打成 0，再把侧栏改成
 * 一条**浮在页面上的圆角条**（inset 12px、radius 16、半透明白 + 模糊、
 * 两层投影），这也是 `后台UI参考两屏-20261109.html` 那套的底子 ——
 * 面是浮起来的、控件是有描边有微投影的圆形/胶囊，而不是一条平铺的白带子。
 *
 * 条子从左到右：品牌 → 九个视图的胶囊组（底下一层浅灰，选中的是深色胶囊）
 * → 设置 / 技能 / 更新（三个描边圆按钮，文字收掉只留 title）→ 当前工作区胶囊。
 *
 * 顶上占 10 + 60 = 70px：`.main-column`（index.html）和 `.content-main`
 * （account / content / topic 三个详情页）都按 84px 让位，`.topbar` 的吸顶位置
 * 也一起跟着走。窄屏按 1359 / 1059 / 1019 / 979 / 939 收，断点是照实测宽度定的：
 * 九个视图加工作区顶得很满，900 宽刚好放下（595/595），再窄就只能横向滚。
 */
/**
 * iframe 里的页面地址是 `dsh-app://app/selfmedia-ds/app/`，
 * 但工作台的前端是照着「自己就在站点根目录」写的：fetch 用 `/api/...`。
 * 这段脚本把这几处改成带前缀，其余一个字不碰。
 */
/**
 * 注入到工作台页面里的**壳脚本**。只干三件事，界面一概不管：
 *   1. 打上 `data-dsb-host` —— 工作台靠这个知道自己跑在 DS 版里
 *   2. 把工作台自己的 `/api/*` 请求改到前缀下（反代再剥回去）
 *   3. 把工作台的顶栏资源（`ds-shell.css` / `ds-shell.js`）挂上来
 *
 * 顶栏、导航、工作区胶囊、更新按钮都在 `工作台模板/工作台/ds-shell.*` 里，
 * 属于工作台那一套代码——改它们不用动这个插件。
 */
export const BRIDGE_SOURCE = `(function(){
  var BASE = ${JSON.stringify(APP_BASE)};
  var ACTION = ${JSON.stringify(ACTION_PATH)};
  window.__SELFMEDIA_DS_BRIDGE__ = { base: BASE, action: ACTION };

  document.documentElement.setAttribute('data-dsb-host', '1');

  /** 让工作台把自己的顶栏装上来。两个资源都由插件转发，内容在模板里。 */
  function injectShellAssets() {
    var head = document.head;
    if (!head) return;
    if (!document.querySelector('link[data-selfmedia-shell]')) {
      var link = document.createElement('link');
      link.setAttribute('data-selfmedia-shell', '');
      link.rel = 'stylesheet';
      link.href = ${JSON.stringify(SHELL_STYLE)};
      head.appendChild(link);
    }
    if (!document.querySelector('script[data-selfmedia-shell]')) {
      var script = document.createElement('script');
      script.setAttribute('data-selfmedia-shell', '');
      script.src = ${JSON.stringify(SHELL_SCRIPT)};
      head.appendChild(script);
    }
  }
  injectShellAssets();

  /* ---------------------------- 前缀改写 ---------------------------- */
  function rewrite(input) {
    if (typeof input !== 'string') return input;
    if (input.charCodeAt(0) !== 47) return input;      // 只处理站内绝对路径
    if (input.indexOf(BASE + '/') === 0) return input; // 已经带前缀，别叠
    // 只改工作台自己的 /api/*。插件自己的 /selfmedia-ds/api/* 是宿主路径，
    // 加了前缀就 404（2026-11-12 踩过：更新按钮点下去没反应）。
    if (input.indexOf('/api/') !== 0) return input;
    return BASE + input;
  }

  // fetch
  var rawFetch = window.fetch;
  if (typeof rawFetch === 'function') {
    window.fetch = function (input, init) {
      if (typeof input === 'string') return rawFetch.call(this, rewrite(input), init);
      if (input && typeof input === 'object' && typeof input.url === 'string' && typeof Request !== 'undefined' && input instanceof Request) {
        var next = rewrite(input.url);
        if (next !== input.url) {
          return rawFetch.call(this, new Request(next, input), init);
        }
      }
      return rawFetch.call(this, input, init);
    };
  }

  // EventSource（工作台的实时刷新靠它）
  var RawEventSource = window.EventSource;
  if (typeof RawEventSource === 'function') {
    var PatchedEventSource = function (url, config) {
      return new RawEventSource(rewrite(url), config);
    };
    PatchedEventSource.prototype = RawEventSource.prototype;
    PatchedEventSource.CONNECTING = 0;
    PatchedEventSource.OPEN = 1;
    PatchedEventSource.CLOSED = 2;
    window.EventSource = PatchedEventSource;
  }

  // XMLHttpRequest
  var rawOpen = window.XMLHttpRequest && window.XMLHttpRequest.prototype.open;
  if (typeof rawOpen === 'function') {
    window.XMLHttpRequest.prototype.open = function (method, url) {
      var args = Array.prototype.slice.call(arguments);
      if (args.length > 1) args[1] = rewrite(args[1]);
      return rawOpen.apply(this, args);
    };
  }

  // 图片、视频、下载链接不是 fetch 发的，是浏览器直接按属性去要资源。
  // 工作台里封面和头像是 img 标签指向 /api/raw?path=...，不补这一步就全 404。
  var ATTRS = ['src', 'href', 'poster'];
  function fixOne(el) {
    if (!el || !el.getAttribute) return;
    for (var i = 0; i < ATTRS.length; i += 1) {
      var name = ATTRS[i];
      var value = el.getAttribute(name);
      if (!value) continue;
      if (value.charCodeAt(0) !== 47) continue;            // 只看站内绝对路径
      if (value.indexOf(BASE + '/') === 0) continue;       // 已经带前缀
      el.setAttribute(name, BASE + value);
    }
  }

  var SELECTOR = 'img[src],video[src],video[poster],source[src],audio[src],a[href]';
  function fixWithin(root) {
    if (!root || root.nodeType !== 1) return;
    if (root.matches && root.matches(SELECTOR)) fixOne(root);
    if (!root.querySelectorAll) return;
    var list = root.querySelectorAll(SELECTOR);
    for (var i = 0; i < list.length; i += 1) fixOne(list[i]);
  }

  function startFixing() {
    fixWithin(document.documentElement);
    var observer = new MutationObserver(function (records) {
      for (var i = 0; i < records.length; i += 1) {
        var record = records[i];
        if (record.type === 'attributes') {
          fixOne(record.target);
          continue;
        }
        var added = record.addedNodes;
        for (var j = 0; j < added.length; j += 1) fixWithin(added[j]);
      }
    });
    observer.observe(document.documentElement, {
      childList: true,
      subtree: true,
      attributes: true,
      attributeFilter: ATTRS,
    });
  }

  if (document.documentElement) startFixing();
  else document.addEventListener('DOMContentLoaded', startFixing, { once: true });

  // 页面里「复制指令」这类动作：剪贴板照写，同时把指令回传给宿主，
  // 由宿主直接排进左边那个工作区会话，省掉「复制 → 切窗口 → 粘贴」。
  function reportCopy(text) {
    try {
      if (!text || typeof text !== 'string') return;
      if (text.length < 8) return;
      window.parent.postMessage(
        { source: 'selfmedia-ds', type: 'clipboard', text: text },
        '*',
      );
    } catch (e) {}
  }

  if (navigator.clipboard && typeof navigator.clipboard.writeText === 'function') {
    var rawWrite = navigator.clipboard.writeText.bind(navigator.clipboard);
    navigator.clipboard.writeText = function (text) {
      var result = rawWrite(text);
      reportCopy(text);
      return result;
    };
  }

  // 没有剪贴板 API 时，工作台会走 execCommand 兜底，这里补一手。
  var rawExec = document.execCommand && document.execCommand.bind(document);
  if (typeof rawExec === 'function') {
    document.execCommand = function (command) {
      var value = arguments.length > 2 ? arguments[2] : null;
      var result = rawExec.apply(document, arguments);
      if (String(command).toLowerCase() === 'copy') {
        try {
          var active = document.activeElement;
          if (active && typeof active.value === 'string') reportCopy(active.value);
          else reportCopy(String(window.getSelection() || ''));
        } catch (e) {}
      }
      return result;
    };
  }

  // 页面里「在新窗口打开原文件」之类的动作，在桌面壳里会被拦；
  // 改成在当前 frame 里导航，至少不会点了没反应。
  var rawWinOpen = window.open;
  window.open = function (url) {
    if (typeof url === 'string' && url.charAt(0) === '/') {
      window.location.href = rewrite(url);
      return null;
    }
    if (typeof rawWinOpen === 'function') return rawWinOpen.apply(window, arguments);
    return null;
  };
})();`;


