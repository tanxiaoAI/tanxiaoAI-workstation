# Chrome 搜索选题取数协议

本文件供 AI 使用当前 Chrome 会话完成搜索选题取数，不是可直接运行的程序。只读取公开页面，不发布内容，不构造绕开平台页面的评论裸请求。

## 1. 输入

- 已冻结的 `搜索计划.json`；
- 本轮指定数据源 `chrome_devtools`；
- 调用方分配的 `Chrome搜索原始结果.json` 精确输出路径；
- 后续选中内容的 `评论原始/<平台原生内容ID>.json` 精确输出路径。

## 2. 搜索结果

逐个执行搜索计划中的 `query_id + platform + keyword`。

0. 打开搜索页后先判断真实登录态：页面出现“登录后查看搜索结果”、扫码登录或跳转登录页时，判为未登录。已登录直接读取；未登录时保持页面打开，让用户在自己正在用的 Chrome 里登录，并每 5 秒重新判断一次，最多 60 轮（约 5 分钟）。登录成功后自动继续，不要求用户回复；超时则停止并记录真实原因，不得把登录墙当成零结果。
1. 三个平台各走各的入口：
   - 小红书：`https://www.xiaohongshu.com/search_result?keyword=<URL编码后的关键词>`
   - 抖音：**不要直接开 `https://www.douyin.com/search/<关键词>`**，这条路由会被平台拦成 502。改为打开 `https://www.douyin.com/jingxuan`，在搜索框里输入关键词回车，页面会跳到 `/jingxuan/search/<关键词>`，结果才正常返回。
   - B站：`https://search.bilibili.com/all?keyword=<URL编码后的关键词>`
2. 只取一轮结果（第一页），不无限滚动、不翻页。

### 2.1 小红书：读页面卡片，写成统一合同

小红书的结果卡片在 `window.__INITIAL_STATE__.search.feeds` 里，每项就是一个 noteCard（`id`、`displayTitle`、`user.nickname`、`interactInfo.likedCount` 等）。按下面合同写入 `Chrome搜索原始结果.json`：

```json
{
  "contents": [
    {
      "query_id": "Q001",
      "platform": "小红书",
      "native_id": "真实note_id",
      "title": "真实标题",
      "body": "实际读取的正文，没有则为空",
      "author_name": "作者昵称",
      "author_id": "作者ID，没有则为空",
      "publish_time": "页面实际时间",
      "url": "真实内容链接",
      "metrics": {
        "likes": "点赞数",
        "collects": "收藏数",
        "comments": "评论数",
        "shares": "分享数",
        "views": "播放或阅读数，没有则为空"
      }
    }
  ]
}
```

`query_id` 必须来自冻结计划，不得新增关键词、改换平台或从旧运行复制结果。

**小红书的 `url` 必须带上 xsec_token，不能只写 `/explore/<note_id>`。** 实测（无 Cookie 直接请求）：

- 不带 token：服务端 302 到 `/404/...`，提示「当前笔记暂时无法浏览」——别人点开就是「页面不见了」。
- 带 token：302 到登录页并带 `redirectPath`，登录后直接落到这篇笔记。
- 小红书网页版本就不给未登录的人看笔记，任何链接都一样；带 token 是能做到的上限。

所以取数时必须顺手把卡片上的 `xsecToken`（或页面链接里的 `xsec_token`）一起写进 `url`，形如：

```
https://www.xiaohongshu.com/explore/<note_id>?xsec_token=<xsecToken>&xsec_source=pc_search
```

token 是搜索页当场下发的短时票，过期就失效。链接只在当轮有效，不能让下一轮复用旧链接。

### 2.2 抖音 / B站：抓平台自己的搜索接口响应，别从页面抄字段

这两个平台的页面卡片是 JS 渲染的 div，DOM 里没有作品 ID、也没有可读的卡片文字结构，手工抄字段必错。正确做法是抓页面自己发出的搜索接口响应：

- 抖音：`/aweme/v1/web/general/search/single/`（响应 `data[].aweme_info`）
- B站：`/x/web-interface/wbi/search/type` 或页面自身等价搜索接口（响应 `data.result[]`，每条带 `bvid`）

步骤：

1. 按上面的入口打开搜索结果页，等卡片渲染出来。
2. `list_network_requests` 找当次搜索接口请求，用 `get_network_request` 把响应原文写到本轮 `raw/<平台>-<query_id>.json`（`responseFilePath` 参数；禁止用 `output_path`，禁止从工具展示文本里抠 JSON）。
3. 交给执行器解析，不再手工整理：

```bash
python3 "执行单元/搜索选题数据执行器.py" --workspace-root "<根>" --action normalize \
  --plan-file "<搜索计划.json>" --raw-input "<raw/抖音-Q001.json>" \
  --query-id "Q001" --platform "抖音"
```

执行器会从响应里取 `aweme_id`/`bvid`、标题、作者、互动数、发布时间，剥掉 B站标题里的 `<em>` 高亮标签，并把 `canonical_url` 拼成标准作品链接。

抖音一个关键词通常分页返回（offset 0 / 10 / 20），**每组只要第一页**；第一页请求没抓到时就取 offset 最小的那一页，并在停止原因里写清本轮实际取的是第几页。

4. 三个平台的结果合并时，执行器按 `平台:原生ID` 去重；同一内容被多个关键词命中会合并 `query_ids`。

## 3. 评论补充

只对流程执行者根据搜索结果明确选中的内容补评论：

1. 打开该内容真实链接并确认原生 ID 一致。
2. **先触发评论请求，再读响应。** 抖音作品页、B 站视频页、小红书笔记页打开后都不会自动请求评论接口，必须点开评论区并滚动到需要的位置；抖音、B 站也可以在当前页会话里直接请求评论接口，小红书只能靠页面自身发出。请求列表里没有评论请求时，先补一次触发，**不得直接记成「未采集」或「平台未返回」**（2026-11-01 实测：抖音作品页评论请求为 0，直接请求 `/aweme/v1/web/comment/list/` 立刻拿到 `total = 101`）。
3. 读取页面自身（或当前会话直接请求）的评论接口原始响应，各平台对应的接口：
   - 小红书：`/api/sns/web/v2/comment/page`，需要下一页时滚动评论区，最多沿用现有一级评论三页口径。
   - 抖音：`/aweme/v1/web/comment/list/`（响应 `comments[]`，`count` 固定 50，游标每次前进 50）。
   - B站：`/x/v2/reply/wbi/main`（响应 `replies[]`，`rpid` 是评论 ID）。
4. 原始响应写入本轮 `评论原始/<平台原生内容ID>.json`，不把完整 Cookie、请求头或页面状态树写入文件。
   抖音、B站的作品页链接如果打不开（提示笔记或视频不存在），改用搜索页里那条卡片的真实链接重新进入一次；仍打不开就按单条失败记录真实原因，不要让整轮停在这里。
5. 评论数为 0 时记录真实状态，不伪造空响应；读取失败时保留真实原因，继续其他内容。
6. 只取一级评论，不额外请求完整楼中楼；B站不展开楼中楼（`replies` 里的子回复不采）。

随后调用 `搜索选题数据执行器.py --action enrich`，由执行器统一解析评论、去重并写入 `选题证据.json`。
