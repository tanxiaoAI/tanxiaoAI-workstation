# 自媒体工作台 DS 版

DeepSeek Harness（DSH）插件的包根目录。**这个目录本身就是要安装的 bundle**，不要往里面塞用户资料数据。

## 这个目录是什么

DS 版是**一个独立的产品**：壳 + 它自己的一套工作台，都在这个目录里。

- `index.js` / `client.js` / `bridge.mjs` — 壳：反向代理、顶栏、工作区切换、首次进入
- `工作台模板/` — DS 版自带的那套工作台（工作台页面 + `skills/` + `执行流程/` +
  `执行单元/` + `提示词/` + `分析框架/`），约 5.3MB
  - `工作台/ds-shell.js` + `ds-shell.css` — **顶栏和导航在这里**，不在插件里
  - `工作台/.ds-edition` — 工作区的身份证，`selfmedia-ds`
- `工作台版本.txt` — 模板对应的版本号，装完写进工作区 `.meiling/installation.json`

装到用户选的目录时，`installWorkspace()` 先把模板整份铺进去再启动工作台；用户目录里
只有数据（`我方资料/`、`对标资料/`）和他的本地配置，工具层跟着模板走。
线上清单下载那条路还在，只作为模板缺失时的兜底。

**和 `../自媒体工作台开源版/` 是两套代码，互不影响。** 改 DS 版的界面或流程就改
`工作台模板/`，开源版一个字不动；反过来也一样。代价是同一个 bug 要修两处——这是
刻意选的，两边不共用一个发行包。

⚠️ 在**这个目录里**对模板的 `.js` 跑 `node --check` 会报「xxx has already been
declared」——根目录的 `package.json` 是 `type: module`，子目录里的浏览器脚本被当成
ESM 解析了。那是假报错，拷到别处再 check 就正常。

## 壳和界面怎么分（2026-11-12 重构）

**壳越薄越好**：它跟着 Harness 走（宿主协议变了才动），不该天天改。
界面跟着内容走，天天改。所以两边分开：

| 在哪 | 管什么 |
| --- | --- |
| `bridge.mjs` / `index.js` / `client.js`（壳） | 反向代理、前缀改写、会话桥接、选工作区、装工作台 |
| `工作台模板/工作台/ds-shell.js` + `ds-shell.css` | 顶栏、导航、工作区胶囊、更新按钮 |

壳只干四件事：

1. `GET/POST /selfmedia-ds/app/**` → 反代到工作台进程
2. 给每个 HTML 注入 `bridge.js`：打 `data-dsb-host`、把 `/api/*` 改到前缀下、
   把 `ds-shell.css` / `ds-shell.js` 挂上去
3. `POST /api/run` → 在选中工作区里新开对话
4. 选工作区（要开系统对话框，只有宿主能做）

`/selfmedia-ds/shell.js` 和 `/selfmedia-ds/shell.css` 两条路由**只是把模板里的文件吐出去**，
要改顶栏就改 `工作台模板/工作台/ds-shell.*`，不用碰插件。

### ds-shell 自己判断三种情况

- **抽屉里的详情页**（iframe）：只设 `data-dsb-embed`，把 `.sidebar` 收掉、正文占满，
  不出第二条导航（抽屉自己有「返回上一页」）
- **浏览器直接打开**（没有 `data-dsb-host`）：什么都不做，保持工作台原来的左侧栏
- **DS 版主页面**：设 `data-dsb-layout="top"`，把侧栏改成顶部一条

⚠️ `ds-shell.css` 里每条都带 `html[data-dsb-layout="top"]` 前缀 + `!important`，
覆盖的是工作台自己的侧栏样式。CSS 走 `<link>`（同源，`style-src 'self'` 允许），
不是 `<style>` 标签——标签会被 CSP 直接丢弃。

## DS 版工作区有身份证

`工作台/.ds-edition`，内容一行 `selfmedia-ds`。放工作台目录里，跟着代码走：
万一被开源版的更新覆盖，标识一起没了，DS 版下次就拒绝打开——正好挡住两边混用。

`probeWorkspace()` 三种结果：

- `ok`：DS 版的工作台，正常起
- `missing_entry`：什么工作台都没有，走「装一份」
- `other_product`：有工作台但不是 DS 版那套（开源版装的），**拒绝**，让人换个目录

## 首次进入 / 装工作台

- **首次进入**（`FirstRunStage`）：只有「先选一个工作区」+ 一句说明 + 一个大按钮，
  居中。手动填路径不在界面上，只有选文件夹的能力不可用时才退化成输入框。
- **这个文件夹里还没有工作台**（`InstallModal`）：弹窗，不换页。标题 + 一句话 +
  路径块 + 主/次按钮。装的过程中按钮变「正在装…」，不关弹窗。
- 目录里有工作台但服务没起来，是另一回事：走「工作台没能启动 + 重试」，别弹安装框。

## 点指令 → 在工作区里新开对话（2026-11-12 修）

工作台里点一条指令，走 `postMessage` → `client.js` → `POST /run` → `sendToSession`。

**会话归属必须靠 `workspaceId`，不能只给 `cwd`。** 宿主的侧栏是按工作区注册表
（`~/.dsh/storages/workspace.json`）分组的，`sessionController.create({ cwd })`
建出来的会话没有归属，会掉进「默认工作区 / 未分组」——看着就像没选工作区
（2026-11-12 用户实测踩到）。正确顺序：

1. `ctx.get('workspaceController').create({ path: workspaceRoot })`
   → `{ workspace: { workspaceId } }`，**幂等**，登记过就返回原来那条
2. `ctx.get('sessionController').create({ workspaceId })`
   → 会话 cwd 自动取 workspace.path，并 `attachSession` 进该工作区

两条硬约束：

- `session.create` 的 `workspaceId` 和 `cwd` **不能同时给**，会抛
  `gateway/bad-request`。所以只有一个分支，`workspaceId` 拿不到时才退化成 cwd。
- **每次点指令都新建一个对话**，不复用旧会话。指令是各自独立的任务，混在一个
  对话里又难翻又难看。（`blank` 在第一次 prompt 后就变 false，本来也复用不了。）

新会话的 id 回给前端，`client.js` 调 `ctx.get('uiWorkspace').openSession(sessionId)`
切过去——就是侧栏点条目时的同一个方法。拿不到这个服务就只停在会话面板，不报错。



## 布局铁律

根容器 `.dsb-app` 用 `height:100%` + 普通流，**绝对不能 `position:absolute; inset:0`**——
宿主那个位置不是定位父级，absolute 会一路顶到视口，把左边会话栏整个盖住（2026-11-07 踩过）。

## 装法（在 Harness 里执行一次）

对 Harness 说一句：

> 装 `/Users/tanxiao/Desktop/AI自媒体系统桌面版源文件/自媒体工作台DS版` 这个 bundle

它内部走 `plugin_manager` → `install_bundle`，把这个目录链进 profile。装完 Cmd+R 刷新，侧栏出现「DS 工作台」。

改完文件后重装一次才生效：先 `remove_bundle @local/selfmedia-ds`，再 `install_bundle`，然后**完全退出 Harness 再打开**（客户端代码换了版本，光刷新可能还是旧的）。

## 读哪个工作区

默认读：

```
/Users/tanxiao/Desktop/AI自媒体系统桌面版源文件/自媒体工作台开源版
```

换工作区不用改代码，起 Harness 前设环境变量即可：

```bash
SELFMEDIA_WORKSPACE_ROOT=/path/to/workspace
```

## 四条路由

| 路由 | 做什么 |
|---|---|
| `GET /selfmedia-ds/api/list` | 读内容库 + 逐条判断有没有 `内容分析.md` |
| `GET /selfmedia-ds/api/report?id=` | 读某条的正式报告（限 512KB，路径必须落在工作区内） |
| `POST /selfmedia-ds/api/run` | `{mode:'id',id}` 或 `{mode:'url',url}` → 排进工作区会话 |
| `GET /selfmedia-ds/api/activity` | 这个工作区的会话在不在跑 |

## 会话为什么要挂在工作区上

两件不同的事，容易混：

- **`cwd` 决定 AI 能不能干活**：`执行流程/`、`执行提示词/`、`执行单元/` 全是相对 `cwd`
  的路径。发给一个 `cwd` 不是工作区的会话，AI 读不到流程文件，就会报文件不存在、
  或者凭记忆编流程。
- **`workspaceId` 决定它出现在侧栏哪儿**：只带 `cwd` 建的会话不会进任何分组，
  会掉到「默认工作区 / 未分组」。

所以 `sendToSession` 是「先登记工作区 → 再用 `workspaceId` 开会话 → 再 prompt」，
细节见前面「点指令 → 在工作区里新开对话」。`cwd` 不在那条链上单独传，它是
`workspace.path` 的结果。

`findWorkspaceSession` 只服务于 `/activity`（看这个工作区的会话在不在跑），只读。

## 边界

- 只读工作区文件，不写任何东西（唯一的写动作是往会话里排一条 prompt）
- 不接管授权、计费、更新——那些还在 `server/index.js`
- 自用版：没有多工作区、没有免费/付费闸门、没有上架索引
