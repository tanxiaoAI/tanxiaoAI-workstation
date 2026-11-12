/**
 * 自媒体AI工作台 DS 版 — 界面半部
 *
 * 一整页 iframe，加载的就是工作区里那份工作台本体。
 * 这里负责三件事：
 *   1. 头一回打开时，让用户选个文件夹；里面没有工作台就当场装一份
 *   2. 把 iframe 铺满。当前工作区显示在页面内那条顶部导航的最右边
 *      （「选文件夹」也挪到那儿了），这里不再单独占一行
 *   3. 接管「复制指令」—— 页面里复制出来的指令直接排进左边那个会话；
 *      导航上点工作区 → 这里开系统选文件夹的对话框
 */
window.__ModuleLoader__.load({
  id: 'selfmedia-ds',
  factory(require) {
    const React = require('react')
    const h = React.createElement

    const API = '/selfmedia-ds/api'
    const APP = '/selfmedia-ds/app/'
    /** 前端半部的版本号，跟 package.json 对齐；排障时靠它分清新旧前端。 */
    const CLIENT_VERSION = '0.3.15'

    const FONT = '-apple-system, BlinkMacSystemFont, "PingFang SC", sans-serif'
    const INK = 'var(--dsh-text-primary, #111316)'
    const MUTED = '#5c6270'
    const FAINT = '#8a909c'
    const DANGER = '#b3261e'

    let pluginCtx = null

    /* --------------------------------------------------------- 小工具 */

    function openConversation() {
      try {
        pluginCtx?.get('layout')?.selectPanel('conversation')
      } catch {
        /* 布局服务不可用时静默 */
      }
    }

    /**
     * 把刚开出来的会话切到前台。
     * `uiWorkspace.openSession` 是宿主侧栏点条目时用的同一个方法，
     * 消息已经排进去了，这里只负责让用户看见它在说话。
     */
    function revealSession(sessionId, onDone) {
      openConversation()
      if (!sessionId) return
      // 记下来：这一轮跑完要把面板切回工作台（见 onSessionSnapshot）
      waitSession = String(sessionId)
      try {
        const ui = pluginCtx?.get('uiWorkspace')
        if (ui && typeof ui.openSession === 'function') {
          Promise.resolve(ui.openSession(sessionId)).catch(() => {})
        }
      } catch {
        /* 拿不到就只停在会话面板，不打扰用户 */
      }
      watchRunThenReturn(sessionId, onDone)
    }

    /** 当前等着跑完的那个会话（从工作台点进去才设）。 */
    let waitSession = ''

    let returnTimer = null

    function stopReturnWatch() {
      if (returnTimer) {
        clearInterval(returnTimer)
        returnTimer = null
      }
    }

    /**
     * 订阅宿主的会话列表 store（`ctx.sessions.list`）—— 侧栏那些绿点就是这份数据。
     *
     * 社区插件的做法（dsh-session-notify / attention-plugins）：浏览器半部直接
     * `list.getSnapshot()` 读 `byId[id].running`，`running true→false` 就是一轮跑完。
     * **不要走 HTTP 去问插件自己的接口**，那条路在宿主那层一直被挡（2026-11-12 查了一晚）。
     */
    let prevRunning = new Map()
    let statusPrimed = false

    function onSessionSnapshot() {
      const sessions = (() => {
        try {
          return pluginCtx?.sessions ?? pluginCtx?.get?.('sessions')
        } catch {
          return null
        }
      })()
      const list = sessions?.list
      if (!list || typeof list.getSnapshot !== 'function') return
      const snap = list.getSnapshot()
      const byId = snap?.byId
      if (!byId || typeof byId !== 'object') return
      for (const id of Object.keys(byId)) {
        const row = byId[id]
        if (!row || typeof row.running !== 'boolean') continue
        const was = prevRunning.get(id)
        prevRunning.set(id, row.running)
        if (!statusPrimed) continue
        // 只认「从工作台点进去的那一轮」：别的会话跑完不抢用户的屏幕。
        if (was === true && row.running === false && waitSession === String(id)) {
          finishRun(id)
        }
      }
      statusPrimed = true
    }

    /** 这一轮跑完了：切回工作台，目标页由 iframe 自己去取。 */
    function finishRun(sessionId) {
      const spec = pendingGoto.get(String(sessionId)) || null
      pendingGoto.delete(String(sessionId))
      waitSession = ''
      stopReturnWatch()
      try {
        pluginCtx?.get('layout')?.selectPanel('selfmedia-ds')
      } catch {
        /* 布局服务不可用时静默 */
      }
      // 面板刚回来，iframe 可能还没挂好：发几次，谁先到算谁。
      if (spec) {
        const send = () => {
          try {
            frameRefHolder?.current?.contentWindow?.postMessage({ type: 'dsb-goto', spec }, '*')
          } catch {
            /* 下一发再试 */
          }
        }
        send()
        setTimeout(send, 400)
        setTimeout(send, 1200)
      }
    }

    /** 已经取到的定位目标，key = sessionId。 */
    const pendingGoto = new Map()

    /** 工作台 iframe 的 ref（组件挂载时塞进来，模块级函数要用）。 */
    let frameRefHolder = null

    /**
     * 送完指令后，盯着「这一轮该看哪一页」。
     *
     * 从**工作台自己的后端**要（`/selfmedia-ds/app/api/ds-goto`）：这个前缀就是
     * 页面本身加载用的那条反代，一定通。AI 在这一轮最后一步把目标写进工作区的
     * goto.json，这里取到就记下来；真正的「切回工作台」由会话状态那条线负责。
     */
    function watchRunThenReturn(sessionId, onDone) {
      stopReturnWatch()
      const started = Date.now()

      returnTimer = setInterval(async () => {
        if (Date.now() - started > 45 * 60 * 1000) {
          stopReturnWatch()
          return
        }
        try {
          const response = await fetch(`${APP}api/ds-goto`, {
            headers: { accept: 'application/json' },
          })
          if (!response.ok) return
          const data = await response.json()
          if (!data || !data.spec) return
          stopReturnWatch()
          if (typeof onDone === 'function') onDone(String(data.spec))
        } catch {
          /* 工作台还没起来就下一轮再试 */
        }
      }, 2500)
    }

    async function readJson(response) {
      const data = await response.json().catch(() => ({}))
      if (!response.ok || data.ok === false) {
        throw new Error(data.message ?? `HTTP ${response.status}`)
      }
      return data
    }

    async function sendToSession(text) {
      return readJson(
        await fetch(`${API}/run`, {
          method: 'POST',
          headers: { 'content-type': 'application/json' },
          body: JSON.stringify({ text }),
        }),
      )
    }

    /* --------------------------------------------------------- 共用件 */

    const inputStyle = {
      width: '100%',
      boxSizing: 'border-box',
      padding: '10px 12px',
      fontSize: 13,
      borderRadius: 10,
      border: '1px solid var(--dsh-border-default, #d9dce3)',
      background: '#fff',
      color: 'inherit',
      outline: 'none',
      fontFamily: 'inherit',
    }

    function Primary({ label, onClick, busy }) {
      return h(
        'button',
        {
          type: 'button',
          disabled: busy,
          onClick,
          style: {
            border: 0,
            borderRadius: 10,
            padding: '11px 22px',
            font: `600 13.5px/1.2 ${FONT}`,
            color: '#fff',
            background: '#111316',
            cursor: busy ? 'default' : 'pointer',
            opacity: busy ? 0.6 : 1,
            fontFamily: 'inherit',
          },
        },
        label,
      )
    }

    function Ghost({ label, onClick, busy }) {
      return h(
        'button',
        {
          type: 'button',
          disabled: busy,
          onClick,
          style: {
            border: '1px solid var(--dsh-border-default, #d9dce3)',
            borderRadius: 10,
            padding: '10px 18px',
            font: `500 13px/1.2 ${FONT}`,
            color: INK,
            background: '#fff',
            cursor: busy ? 'default' : 'pointer',
            fontFamily: 'inherit',
          },
        },
        label,
      )
    }

    function Linkish({ label, onClick }) {
      return h(
        'button',
        {
          type: 'button',
          onClick,
          style: {
            border: 0,
            background: 'transparent',
            padding: 0,
            font: `12.5px/1.4 ${FONT}`,
            color: FAINT,
            cursor: 'pointer',
            fontFamily: 'inherit',
            textDecoration: 'underline',
            textUnderlineOffset: 3,
          },
        },
        label,
      )
    }

    function Stage({ children }) {
      return h(
        'div',
        {
          style: {
            height: '100%',
            overflowY: 'auto',
            padding: '64px 36px 40px',
            font: `14px/1.65 ${FONT}`,
            color: INK,
          },
        },
        h('div', { style: { maxWidth: 540, margin: '0 auto' } }, children),
      )
    }

    function Title({ children }) {
      return h('h1', { style: { fontSize: 21, lineHeight: 1.4, margin: '0 0 12px' } }, children)
    }

    function Lede({ children }) {
      return h('p', { style: { margin: '0 0 24px', color: MUTED, fontSize: 13.5 } }, children)
    }

    function Hint({ children }) {
      return h('p', { style: { margin: '26px 0 0', fontSize: 12.5, color: FAINT } }, children)
    }

    function ErrorLine({ children }) {
      if (!children) return null
      return h(
        'p',
        { style: { margin: '14px 0 0', color: DANGER, fontSize: 12.5, wordBreak: 'break-all' } },
        children,
      )
    }

    /* --------------------------------------------------- ① 首次进入 */

    /**
     * 还没指向工作区时那一屏。设计稿只留三样：标题、一句话、主按钮。
     * 手动填路径不在界面上了，但选文件夹的能力万一不可用（没有系统选择器），
     * 会在这儿退化成输入框——否则就卡死了，哪儿也去不了。
     */
    function FirstRunStage({ onPick, onManual, busy, error }) {
      const [manual, setManual] = React.useState(false)
      const [value, setValue] = React.useState('')
      const needManual = manual || /目录选择器|picker/i.test(String(error || ''))

      return h(
        'div',
        {
          style: {
            height: '100%',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            padding: '48px 40px',
            fontFamily: FONT,
            color: INK,
            background: '#fff',
          },
        },
        h(
          'div',
          { style: { width: '100%', maxWidth: 620, marginTop: '-6vh', textAlign: 'center' } },
          h(
            'h1',
            {
              style: {
                margin: 0,
                fontSize: 36,
                fontWeight: 600,
                letterSpacing: '-1px',
                lineHeight: 1.28,
              },
            },
            '先选一个工作区',
          ),
          h(
            'p',
            {
              style: {
                margin: '16px auto 0',
                maxWidth: 470,
                color: MUTED,
                fontSize: 15.5,
                lineHeight: 1.85,
              },
            },
            '工作台的分析结果、内容和账号资料都写在这个文件夹里，换电脑时把它一起带走就还在。',
          ),
          h(
            'div',
            { style: { marginTop: 38 } },
            h(
              'button',
              {
                type: 'button',
                disabled: busy,
                onClick: onPick,
                style: {
                  height: 60,
                  minWidth: 240,
                  padding: '0 48px',
                  border: 0,
                  borderRadius: 14,
                  background: '#111316',
                  color: '#fff',
                  fontFamily: 'inherit',
                  fontSize: 18,
                  fontWeight: 600,
                  lineHeight: 1,
                  letterSpacing: '.01em',
                  cursor: busy ? 'default' : 'pointer',
                  opacity: busy ? 0.7 : 1,
                  boxShadow: '0 14px 30px -16px rgba(17,19,22,.8)',
                },
              },
              busy ? '打开中…' : '选择文件夹',
            ),
          ),
          error && !needManual ? h(ErrorLine, null, error) : null,
          needManual
            ? h(
                'div',
                { style: { marginTop: 26, textAlign: 'left' } },
                h('input', {
                  value,
                  onChange: (event) => setValue(event.target.value),
                  placeholder: '/Users/你的用户名/Desktop/我的自媒体AI工作台',
                  spellCheck: false,
                  style: { ...inputStyle, height: 42, borderRadius: 10 },
                }),
                h(
                  'div',
                  { style: { marginTop: 10, display: 'flex', gap: 10 } },
                  h(Ghost, {
                    label: '用这个目录',
                    onClick: () => onManual(value.trim()),
                    busy,
                  }),
                ),
                error ? h(ErrorLine, null, error) : null,
              )
            : null,
        ),
      )
    }

    /* ------------------------------------------- ② 这个文件夹里没有工作台 */

    /**
     * 选到的文件夹里没有工作台时用弹窗说，不换页——底下那一屏还在。
     * 只有四块：标题、一句话、路径、两个按钮。
     */
    function InstallModal({ picked, installing, error, onInstall, onRepick, onClose }) {
      const entryNote = picked?.empty
        ? '空目录'
        : `里面有 ${picked?.entryCount ?? 0} 个文件，不是工作台`

      return h(
        'div',
        {
          style: {
            position: 'fixed',
            inset: 0,
            zIndex: 200,
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            padding: 24,
            background: 'rgba(14,16,19,.3)',
            backdropFilter: 'blur(3px)',
            WebkitBackdropFilter: 'blur(3px)',
            fontFamily: FONT,
            color: INK,
          },
          onClick: (event) => {
            if (event.target === event.currentTarget && !installing) onClose()
          },
        },
        h(
          'div',
          {
            style: {
              width: 520,
              maxWidth: '100%',
              background: '#fff',
              borderRadius: 18,
              boxShadow: '0 40px 90px -34px rgba(14,16,19,.6), 0 0 0 1px rgba(17,19,22,.06)',
              overflow: 'hidden',
            },
          },
          h(
            'div',
            { style: { display: 'flex', alignItems: 'flex-start', gap: 12, padding: '24px 24px 0' } },
            h(
              'h3',
              {
                style: {
                  margin: 0,
                  fontSize: 17,
                  fontWeight: 600,
                  letterSpacing: '-.3px',
                  lineHeight: 1.4,
                },
              },
              '这个文件夹里还没有工作台',
            ),
            h(
              'button',
              {
                type: 'button',
                'aria-label': '关闭',
                disabled: installing,
                onClick: onClose,
                style: {
                  marginLeft: 'auto',
                  width: 28,
                  height: 28,
                  flex: '0 0 28px',
                  display: 'grid',
                  placeItems: 'center',
                  border: 0,
                  borderRadius: 8,
                  background: 'transparent',
                  color: '#A8AEBE',
                  cursor: installing ? 'default' : 'pointer',
                  font: '16px/1 ' + FONT,
                },
              },
              '×',
            ),
          ),
          h(
            'div',
            { style: { padding: '10px 24px 0' } },
            h(
              'p',
              { style: { margin: 0, fontSize: 13.5, lineHeight: 1.8, color: MUTED } },
              '装的是程序和流程，约 10 MB，不含任何账号数据。装完打开是空的，跟着引导填一份问卷、绑一个账号，就有内容了。',
            ),
            h(
              'div',
              {
                style: {
                  marginTop: 16,
                  padding: '13px 15px',
                  borderRadius: 12,
                  background: '#F7F8FA',
                  boxShadow: 'inset 0 0 0 1px #EDEFF1',
                },
              },
              h('div', { style: { fontSize: 11.5, color: FAINT } }, '已选的文件夹'),
              h(
                'div',
                {
                  style: {
                    marginTop: 5,
                    fontFamily: 'ui-monospace, SFMono-Regular, Menlo, monospace',
                    fontSize: 12.5,
                    lineHeight: 1.5,
                    color: '#31373D',
                    wordBreak: 'break-all',
                  },
                },
                picked?.path || '',
              ),
              h(
                'div',
                {
                  style: {
                    marginTop: 10,
                    paddingTop: 10,
                    borderTop: '1px dashed #E2E6EA',
                    fontSize: 12,
                    color: FAINT,
                  },
                },
                entryNote + ' · 原有文件不会动',
              ),
            ),
            error ? h(ErrorLine, null, error) : null,
          ),
          h(
            'div',
            { style: { display: 'flex', alignItems: 'center', gap: 10, padding: '22px 24px 24px' } },
            h(
              'button',
              {
                type: 'button',
                disabled: installing,
                onClick: onInstall,
                style: {
                  height: 38,
                  padding: '0 18px',
                  border: 0,
                  borderRadius: 10,
                  background: '#111316',
                  color: '#fff',
                  fontFamily: 'inherit',
                  fontSize: 14,
                  fontWeight: 600,
                  lineHeight: 1,
                  cursor: installing ? 'default' : 'pointer',
                  opacity: installing ? 0.7 : 1,
                },
              },
              installing ? '正在装…' : '在这里装一份',
            ),
            h(
              'button',
              {
                type: 'button',
                disabled: installing,
                onClick: onRepick,
                style: {
                  height: 38,
                  padding: '0 16px',
                  border: 0,
                  borderRadius: 10,
                  background: '#fff',
                  boxShadow: 'inset 0 0 0 1px #E2E6EA',
                  color: '#31373D',
                  fontFamily: 'inherit',
                  fontSize: 13.5,
                  fontWeight: 500,
                  lineHeight: 1,
                  cursor: installing ? 'default' : 'pointer',
                },
              },
              '换一个文件夹',
            ),
          ),
        ),
      )
    }

    /* --------------------------------------------------------- 主页面 */

    function WorkbenchPage() {
      const [state, setState] = React.useState({ status: 'loading' })
      const [phase, setPhase] = React.useState('auto') // auto | pick | confirm | installing
      const [picked, setPicked] = React.useState(null)
      const [busy, setBusy] = React.useState(false)
      const [error, setError] = React.useState('')
      const [installing, setInstalling] = React.useState(false)
      const [reloadKey, setReloadKey] = React.useState(0)
      const [note, setNote] = React.useState('')

      const flash = React.useCallback((text, ms = 2600) => {
        setNote(String(text || ''))
        setTimeout(() => setNote(''), ms)
      }, [])

      // 页面里的工作区胶囊发消息过来时要调 pickFolder，但它定义在下面，
      // 走 ref 转一道，免得消息监听为了拿到最新引用反复重挂。
      const pickRef = React.useRef(null)
      const pickingRef = React.useRef(false)
      // 跑完跳回工作台时要给 iframe 发一条跳转指令，所以得拿着它
      const frameRef = React.useRef(null)
      frameRefHolder = frameRef

      const load = React.useCallback(() => {
        setState({ status: 'loading' })
        fetch(`${API}/workspace`, { headers: { accept: 'application/json' } })
          .then((response) => response.json())
          .then((data) => setState({ status: 'ready', data }))
          .catch((err) => setState({ status: 'error', message: String(err?.message ?? err) }))
      }, [])

      React.useEffect(() => {
        load()
      }, [load])

      // 在别处换了工作区，切回来要看到新的那一份。
      React.useEffect(() => {
        const onFocus = () => load()
        window.addEventListener('focus', onFocus)
        return () => window.removeEventListener('focus', onFocus)
      }, [load])

      /** 工作台页面里复制出来的指令 → 直接排进会话。复制本身照旧有效。 */
      React.useEffect(() => {
        const onMessage = (event) => {
          const data = event.data
          if (!data || data.source !== 'selfmedia-ds') return
          // 页面里那条导航上的工作区胶囊：换目录要开系统对话框，只能在这边开
          if (data.type === 'switch-workspace') {
            pickRef.current?.(data.path ? String(data.path) : '')
            return
          }
          if (data.type !== 'clipboard') return
          const text = String(data.text || '')
          if (text.length < 8) return
          sendToSession(text)
            .then((result) => {
              const sid = String(result?.sessionId || '')
              revealSession(result?.sessionId, (spec) => {
                if (!sid) return
                // 取到目标页先记着。跑完那条线可能还没到（还在跑），也可能已经到过
                // （跑完才写完文件）—— 两种情况都在这里兜一下。
                pendingGoto.set(sid, spec || '')
                if (waitSession !== sid) finishRun(sid)
              })
              flash('已送进对话', 2200)
            })
            .catch((err) => flash(`没送进去：${String(err?.message ?? err)}`, 4000))
        }
        window.addEventListener('message', onMessage)
        return () => {
          window.removeEventListener('message', onMessage)
          stopReturnWatch()
        }
      }, [flash])

      /** 目录里已经有工作台：写进配置，直接进。 */
      const adopt = React.useCallback(
        (workspaceRoot) => {
          if (!workspaceRoot) {
            setError('先填一个目录')
            return
          }
          setBusy(true)
          setError('')
          fetch(`${API}/workspace`, {
            method: 'POST',
            headers: { 'content-type': 'application/json' },
            body: JSON.stringify({ workspaceRoot }),
          })
            .then(readJson)
            .then(() => {
              setPhase('auto')
              setPicked(null)
              setReloadKey((value) => value + 1)
              load()
            })
            .catch((err) => setError(String(err?.message ?? err)))
            .finally(() => setBusy(false))
        },
        [load],
      )

      /** 选完目录先探一下：有工作台就进，没有就问要不要装。 */
      const inspect = React.useCallback(
        async (target, fromNav) => {
          if (!target) {
            setError('先选一个文件夹')
            return
          }
          setBusy(true)
          setError('')
          try {
            const data = await readJson(
              await fetch(`${API}/probe?path=${encodeURIComponent(target)}`),
            )
            if (data.hasWorkbench) {
              adopt(target)
              return
            }
            setPicked({ path: data.path, empty: data.empty, entryCount: data.entryCount })
            setPhase('modal')
          } catch (err) {
            const message = String(err?.message ?? err)
            setError(message)
            // 从导航换目录时这一步没有就地提示（页面还停在原来那一份），用浮条说一声
            if (fromNav) flash(message, 4000)
          } finally {
            setBusy(false)
          }
        },
        [adopt, flash],
      )

      const pickFolder = React.useCallback(async (explicit) => {
        if (pickingRef.current) return
        pickingRef.current = true
        setBusy(true)
        setError('')
        try {
          let target = String(explicit || '')
          if (!target) {
            const data = await readJson(await fetch(`${API}/pick-folder`, { method: 'POST' }))
            if (data.cancelled) return
            target = String(data.workspaceRoot || '')
          }
          await inspect(target, true)
        } catch (err) {
          const message = String(err?.message ?? err)
          setError(message)
          flash(message, 4000)
        } finally {
          pickingRef.current = false
          setBusy(false)
        }
      }, [inspect, flash])

      React.useEffect(() => {
        pickRef.current = pickFolder
      }, [pickFolder])

      const install = React.useCallback(async () => {
        if (!picked?.path || installing) return
        setError('')
        setInstalling(true)
        try {
          await readJson(
            await fetch(`${API}/install`, {
              method: 'POST',
              headers: { 'content-type': 'application/json' },
              body: JSON.stringify({ workspaceRoot: picked.path }),
            }),
          )
          setInstalling(false)
          setPhase('auto')
          setPicked(null)
          setReloadKey((value) => value + 1)
          load()
        } catch (err) {
          setInstalling(false)
          setError(String(err?.message ?? err))
        }
      }, [picked, installing, load])

      const root = state.status === 'ready' ? state.data : null
      const ready = Boolean(root?.ready)
      const startError = root?.error?.message || ''

      /* ---------- 主区显示什么 ---------- */
      const centered = {
        height: '100%',
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
        color: FAINT,
        fontSize: 13,
        fontFamily: FONT,
      }

      let body
      if (state.status === 'loading') {
        body = h('div', { style: centered }, '正在打开工作台…')
      } else if (ready) {
        body = h('iframe', {
          key: `${root.workspaceRoot}-${reloadKey}`,
          ref: frameRef,
          src: APP,
          title: '自媒体AI工作台',
          allow: 'clipboard-read; clipboard-write',
          style: { width: '100%', height: '100%', border: 0, display: 'block', background: '#fff' },
        })
      } else if (startError) {
        // 目录里有工作台、但服务没起来：这不是「没装」，别弹安装框
        body = h(
          'div',
          { style: { ...centered, padding: '48px 40px' } },
          h(
            'div',
            { style: { maxWidth: 520, textAlign: 'center' } },
            h(
              'h1',
              { style: { margin: 0, fontSize: 24, fontWeight: 600, letterSpacing: '-.6px' } },
              '工作台没能启动',
            ),
            h(
              'p',
              {
                style: {
                  margin: '12px 0 24px',
                  color: MUTED,
                  fontSize: 13.5,
                  lineHeight: 1.8,
                  wordBreak: 'break-all',
                },
              },
              startError,
            ),
            h(Primary, { label: '重试', onClick: load, busy: false }),
          ),
        )
      } else {
        body = h(FirstRunStage, {
          onPick: pickFolder,
          onManual: inspect,
          busy,
          error: state.status === 'error' ? state.message : error,
        })
      }

      return h(
        'div',
        {
          className: 'dsb-app',
          style: {
            height: '100%',
            minHeight: 0,
            display: 'flex',
            flexDirection: 'column',
            background: 'var(--dsh-surface-primary, #fff)',
          },
        },
        h('div', { style: { flex: '1 1 auto', minHeight: 0, position: 'relative' } }, body),
        phase === 'modal' && picked
          ? h(InstallModal, {
              picked,
              installing,
              error,
              onInstall: install,
              onRepick: () => pickFolder(),
              onClose: () => {
                if (installing) return
                setPhase('auto')
                setPicked(null)
                setError('')
              },
            })
          : null,
        note
          ? h(
              'div',
              {
                style: {
                  position: 'fixed',
                  bottom: 22,
                  left: '50%',
                  transform: 'translateX(-50%)',
                  background: '#111316',
                  color: '#fff',
                  fontSize: 12.5,
                  padding: '9px 16px',
                  borderRadius: 10,
                  zIndex: 60,
                  pointerEvents: 'none',
                  fontFamily: FONT,
                },
              },
              note,
            )
          : null,
      )
    }

    /* ------------------------------------------------- 插件页里的卡片 */

    function WorkspaceCard(props) {
      const isSummary = props.view === 'summary'
      const [state, setState] = React.useState({ status: 'loading' })
      const [draft, setDraft] = React.useState('')
      const [busy, setBusy] = React.useState(false)
      const [error, setError] = React.useState('')

      const load = React.useCallback(() => {
        fetch(`${API}/workspace`, { headers: { accept: 'application/json' } })
          .then((response) => response.json())
          .then((data) => {
            setState({ status: 'ready', data })
            setDraft((current) => current || String(data.workspaceRoot || ''))
          })
          .catch((err) => setState({ status: 'error', message: String(err?.message ?? err) }))
      }, [])

      React.useEffect(() => {
        load()
      }, [load])

      const save = () => {
        if (!draft.trim()) {
          setError('先填一个目录')
          return
        }
        setBusy(true)
        setError('')
        fetch(`${API}/workspace`, {
          method: 'POST',
          headers: { 'content-type': 'application/json' },
          body: JSON.stringify({ workspaceRoot: draft.trim() }),
        })
          .then(readJson)
          .then(load)
          .catch((err) => setError(String(err?.message ?? err)))
          .finally(() => setBusy(false))
      }

      const data = state.status === 'ready' ? state.data : null
      const short = data ? String(data.workspaceRoot || '').split('/').filter(Boolean).pop() : ''

      if (isSummary) {
        if (state.status === 'loading') return '正在读取'
        if (state.status === 'error') return '读不到工作区设置'
        return data?.ready ? `当前读：${short}` : '还没指向工作区'
      }

      return h(
        'div',
        { style: { display: 'flex', flexDirection: 'column', gap: 10, font: `13px/1.6 ${FONT}` } },
        h(
          'p',
          { style: { margin: 0, color: MUTED, fontSize: 12.5 } },
          '这个插件读的是某个自媒体工作区里已经装好的工作台。所有分析结果、内容、资料都留在这个目录里，插件自己不存数据。平时点导航栏最右边那个工作区就能换，这里只是备用入口。',
        ),
        h('input', {
          value: draft,
          onChange: (event) => {
            setDraft(event.target.value)
            setError('')
          },
          spellCheck: false,
          placeholder: '/Users/你的用户名/Desktop/我的自媒体AI工作台',
          style: { ...inputStyle, padding: '8px 10px', fontSize: 12.5, borderRadius: 8 },
        }),
        h(
          'div',
          { style: { display: 'flex', alignItems: 'center', gap: 10 } },
          h(
            'button',
            {
              type: 'button',
              disabled: busy,
              onClick: save,
              style: {
                border: 0,
                borderRadius: 8,
                padding: '7px 14px',
                fontSize: 12.5,
                fontWeight: 600,
                color: '#fff',
                background: '#111316',
                cursor: busy ? 'default' : 'pointer',
                opacity: busy ? 0.6 : 1,
                fontFamily: 'inherit',
              },
            },
            busy ? '正在切换…' : '用这个目录',
          ),
          h(
            'span',
            { style: { fontSize: 12, color: FAINT } },
            data?.running ? `工作台在跑（端口 ${data.port}）` : '工作台没在跑',
          ),
        ),
        error ? h('span', { style: { color: DANGER, fontSize: 12 } }, error) : null,
      )
    }

    /* ------------------------------------------------------- 侧栏图标 */

    function WorkbenchIcon({ size, active }) {
      return h(
        'svg',
        {
          viewBox: '0 0 24 24',
          width: size,
          height: size,
          fill: 'none',
          stroke: 'currentColor',
          strokeWidth: 1.6,
          strokeLinecap: 'round',
          strokeLinejoin: 'round',
          'aria-hidden': true,
          style: { display: 'block', opacity: active ? 1 : 0.72 },
        },
        h('rect', { x: 3, y: 4, width: 18, height: 16, rx: 2.5 }),
        h('path', { d: 'M3 9h18M9 9v11' }),
      )
    }

    return {
      // 只注入 slots。`sessions` **不**进 inject：社区插件用的是硬依赖
      // （`exports.inject = ['sessions', 'slots']`），但它同时警告「服务缺席会让
      // 整个客户端 fiber 永久 park」—— 那样工作台面板会整个消失。这里改用官方
      // 给的可选读取口 `ctx.get('sessions')`，拿不到就退化成只靠定位文件那条，
      // 至少不会把面板搞没。
      inject: ['slots'],
      apply(ctx) {
        pluginCtx = ctx

        // 会话状态：跟侧栏绿点用同一份数据（`ctx.sessions.list` 的 store）。
        // 这条不经过任何 HTTP 接口，是社区插件和官方侧栏共用的做法。
        let wired = false
        let reason = ''
        try {
          const sessions =
            (typeof ctx.get === 'function' ? ctx.get('sessions') : null) ?? ctx.sessions ?? null
          const list = sessions?.list
          if (!sessions) reason = 'no-sessions'
          else if (!list) reason = 'no-list'
          else if (typeof list.subscribe !== 'function') reason = 'no-subscribe'
          else {
            onSessionSnapshot()
            const dispose = list.subscribe(() => onSessionSnapshot())
            wired = true
            reason = 'ok'
            ctx.effect?.(() => () => {
              try {
                if (typeof dispose === 'function') dispose()
              } catch {
                /* 卸载时订阅已经没了就算了 */
              }
            })
          }
        } catch (e) {
          reason = 'throw:' + String(e && e.message ? e.message : e)
        }

        // 版本探针：落在工作区的 .meiling/dashboard/ds-client.json 里。
        // 排障时靠它判断「用户跑的是新前端还是旧前端」「会话状态那条接通没有」。
        try {
          fetch(
            `${APP}api/ds-client?v=${encodeURIComponent(CLIENT_VERSION)}&s=${wired ? 1 : 0}&r=${encodeURIComponent(reason)}`,
            { headers: { accept: 'application/json' } },
          ).catch(() => {})
        } catch {
          /* 探针失败不影响功能 */
        }

        // 兜底：宿主转发的 api-session/status。命名空间要用 ctx.get('remote.session')，
        // 直接 ctx.remote.session 可能是 undefined。
        if (!wired) {
          try {
            const remoteSession =
              (typeof ctx.get === 'function' ? ctx.get('remote.session') : null) ??
              ctx.remote?.session ??
              null
            const on = remoteSession?.$on ?? remoteSession?.on
            if (typeof on === 'function') {
              on.call(remoteSession, 'api-session/status', (sessionId, running) => {
                if (running) {
                  prevRunning.set(String(sessionId), true)
                  return
                }
                const was = prevRunning.get(String(sessionId))
                prevRunning.set(String(sessionId), false)
                if (was === true && waitSession === String(sessionId)) finishRun(sessionId)
              })
            }
          } catch {
            /* 两条都拿不到就只能靠页面自己跳 */
          }
        }

        // 工作台页面取到目标页后会发这条：它自己已经切好视图了，这里只把面板切回去。
        window.addEventListener('message', (event) => {
          const data = event.data
          if (!data || data.source !== 'selfmedia-ds' || data.type !== 'goto-ready') return
          waitSession = ''
          stopReturnWatch()
          try {
            pluginCtx?.get('layout')?.selectPanel('selfmedia-ds')
          } catch {
            /* 布局服务不可用时静默 */
          }
        })

        ctx.slots.inject('main', () =>
          ctx.slots.register({ name: 'main', key: 'selfmedia-ds' }, (props) =>
            h(WorkbenchPage, props),
          ),
        )

        ctx.slots.inject('sidebar.panellist', () =>
          ctx.slots.register(
            { name: 'sidebar.panellist', id: 'selfmedia-ds', label: '自媒体AI工作台', order: 21 },
            WorkbenchIcon,
          ),
        )

        ctx.slots.inject('plugins.item', () =>
          ctx.slots.register(
            { name: 'plugins.item', id: 'selfmedia-ds', order: 30, label: () => '自媒体AI工作台' },
            WorkspaceCard,
          ),
        )
      },
    }
  },
})
