/**
 * 把工作台的真样式拼成插件能用的 page.css。
 *
 * 为什么要 scope：普通 CSS 是全局的，直接塞进 Harness 会污染宿主 UI。
 * 所以给每条选择器加 `.dsb-app` 前缀；:root / html / body 映射到 .dsb-app 本身，
 * 这样 tokens.css 里的变量在插件根上照样成立。
 *
 * 用法：node build-page-css.mjs   （在 自媒体AI工作台DS版/ 下跑）
 */
import fs from 'node:fs'
import path from 'node:path'

const SRC = process.argv[2] || '../自媒体AI工作台开源版/工作台'
const OUT = path.resolve('page.css')

// 与 index.html 的引入顺序一致（survey / contact / topic-* 与对标内容页无关，略过）
const FILES = ['markdown.css', 'styles.css', 'theme-aurora.css', 'topic-library.css', 'theme-2.css', 'tokens.css']

function stripComments(css) {
  return css.replace(/\/\*[\s\S]*?\*\//g, '')
}

function splitSelector(selector) {
  return selector
    .split(',')
    .map((part) => part.trim())
    .filter(Boolean)
}

function scopeSelector(selector) {
  const s = selector.trim()
  if (!s) return null
  if (s === ':root' || s === 'html' || s === 'body') return '.dsb-app'
  if (/^html\s+body$/.test(s)) return '.dsb-app'
  if (s.startsWith(':root')) return '.dsb-app' + s.slice(5)
  if (/^body[\[.:#]/.test(s)) return '.dsb-app' + s.slice(4)
  if (/^html[\[.:#]/.test(s)) return '.dsb-app' + s.slice(4)
  if (s.startsWith('html ')) return '.dsb-app ' + s.slice(5)
  if (s.startsWith('body ')) return '.dsb-app ' + s.slice(5)
  if (s.startsWith('*')) return '.dsb-app' + (s === '*' ? '' : s.slice(1))
  return '.dsb-app ' + s
}

/** 逐块扫描：普通规则加前缀，@media/@supports 递归，@keyframes/@font-face 原样保留。 */
function scope(css) {
  let out = ''
  let i = 0
  let depth = 0
  let preludeStart = 0
  let blockStart = -1
  const stack = []

  const flushRule = (prelude, body) => {
    const head = prelude.trim()
    if (!head) return ''
    if (/^@(media|supports|layer|container)\b/.test(head)) {
      return `${head}{${scope(body)}}`
    }
    if (/^@(keyframes|-webkit-keyframes|font-face|charset|import|page)\b/.test(head)) {
      return `${head}{${body}}`
    }
    const selectors = splitSelector(head).map(scopeSelector).filter(Boolean)
    if (!selectors.length) return ''
    return `${selectors.join(',')}{${body}}`
  }

  while (i < css.length) {
    const ch = css[i]
    if (ch === '{') {
      if (depth === 0) {
        blockStart = i
        preludeStart = i
      }
      depth += 1
      stack.push(i)
    } else if (ch === '}') {
      depth -= 1
      const open = stack.pop()
      if (depth === 0 && open !== undefined) {
        // 找到这一块的 prelude 起点（上一个 } 或 0 之后）
        let p = open
        while (p > 0 && /[\s]/.test(css[p - 1])) p -= 1
        let q = p
        while (q > 0 && css[q - 1] !== '}' && css[q - 1] !== ';') q -= 1
        const prelude = css.slice(q, p)
        const body = css.slice(open + 1, i)
        out += flushRule(prelude, body) + '\n'
      }
    } else if (ch === ';' && depth === 0) {
      const stmt = css.slice(preludeStart, i + 1).trim()
      if (/^@(import|charset)/.test(stmt)) out += stmt + '\n'
    }
    i += 1
  }
  return out
}

let combined = ''
for (const file of FILES) {
  const full = path.join(SRC, file)
  if (!fs.existsSync(full)) {
    console.warn('缺少', file)
    continue
  }
  combined += `\n` + stripComments(fs.readFileSync(full, 'utf8'))
}

// 上面拼进去的分段注释已经剥掉了，这里再兜一层：解析器不认注释，
// 注释里带 { } 会把 prelude 切歪。
combined = stripComments(combined)

const scoped = scope(combined)
fs.writeFileSync(
  OUT,
  `/* 由 build-page-css.mjs 从工作台真样式生成，勿手改。源：${FILES.join(' → ')} */\n` + scoped,
)
console.log('写出', OUT, (scoped.length / 1024).toFixed(0) + 'KB')
