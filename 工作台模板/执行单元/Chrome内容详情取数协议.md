# Chrome 内容详情取数协议

本文件是供 AI 执行 Chrome 单条内容详情取数的协议，不是可直接运行的 Python 或 JavaScript 程序。

本协议只处理一条已经确定的内容详情，适用于：

- 对标内容数据获取流程的一条内容；
- 对标账号或我方账号详细数据获取流程中的首条内容探索。

批量范围、后续方法复用、正式 CSV、封面、ASR 和分析交接由调用方流程负责。

## 1. 输入

调用方必须先选择一种已预分配的输出模式，并提供：

```text
target_url = 已确认的单条内容链接
canonical_contract = 调用方刚刚查询到的当前 Chrome 精简输出合同
```

**单条 candidate 模式**（对标内容、我方内容流程）额外提供：

```text
candidate_output_path = 本轮 staging 中预先分配的单条 candidate 输出路径
```

**账号批处理 item 模式**（对标账号、我方账号详细数据流程）额外提供 Chrome 批次计划中当前目标的：

```text
batch_result_path = 本轮唯一 Chrome 整批结果 JSON 路径
batch_item_index = 当前目标在计划中的固定顺序编号
expected_content_identity = 计划中的预期平台原生内容 ID
```

调用方需要保存小红书原始详情或平台字幕证据时，再提供：

```text
raw_detail_output_path = 本轮 staging 中预先分配的 Network 原始响应路径
```

`target_url` 是本协议的唯一页面读取输入。平台原生内容 ID 优先从该 URL 解析；如果当前平台不能从 URL 得到稳定 ID，由本协议按该平台的稳定标识规则确认。该协议只负责校验并读取 `target_url` 对应的页面，不负责决定页面属于哪个已选内容库条目；上游已经冻结的 `content_id` 由调用方负责绑定。不得从标题或正文猜测或重新选择内容 ID。

所有输出路径必须位于当前 run 的 staging 目录内，由调用方或执行器预先分配；AI 不自行猜测、改写或创建替代路径。单条模式的 `candidate_output_path` 只指定本条 candidate 的落盘位置；账号批处理模式的 `batch_result_path` 只指定唯一整批结果容器，AI 只能基于该容器模板更新已经定位的 `batch_item_index`，不能新增、删除、重排或改写其他条目。`canonical_contract.canonical_output.template` 只用于约束当前输出字段，`required_when_success` 用于快速确认成功结果的必填字段；字段类型、读取边界和历史兼容规则由本协议及执行器内部维护，不得从旧流程、源码或兼容别名自行拼接字段。

## 2. 执行顺序

### 2.1 校验目标页面

1. 使用当前 Chrome 会话打开 `target_url`。
2. 读取地址栏真实 URL，解析当前页面的平台原生内容 ID。
3. 当前 ID 与 `target_url` 解析出的目标 ID 不一致时，重新导航到 `target_url` 并再次校验。
4. 仍无法确认同一条内容时停止，不能从标题、正文、推荐卡片或其他页面内容猜测身份。

经验提示（非契约，以本轮探索为准）：存在“共创”内容时，详情页显示的作者可能不是本条记录的归属账号；`账号ID` 按流程规定的记录归属填写，身份校验仍以内容标识为准。

### 2.2 读取页面结构化状态

1. 使用 `evaluate_script` 只读取当前目标内容对应的页面内嵌状态。
2. 只投影 canonical contract 允许的单值字段：平台、平台原生内容 ID、账号 ID、账号名、标题、封面图原链接、视频下载链接、媒介形式、互动数据、发布时间、对外页面链接和真实正文文本。
3. 禁止返回完整 `window`、完整 Vue/React 状态树、响应式根对象、`outerHTML`、`document.scripts`、`mediaV2` 或其他无关原始对象。
4. 返回值必须是紧凑 JSON。页面状态原文不得写入 candidate。

B 站页面结构化状态只负责确认 `bvid / aid / cid`；B 站视频 URL 不要求从页面内嵌状态读取。

**B 站单条详情的互动字段**（2026-09-14 实测）：取当前页面 `__INITIAL_STATE__.videoData.stat`——「点赞数」= `stat.like`、「收藏数」= `stat.favorite`、「分享数」= `stat.share`、「评论数」= `stat.reply`，发布时间取 `videoData.pubdate`（Unix 秒）。`stat.view` 是播放量，**不是**「点赞数」，不得写入该列。页面拿不到 `like` 时该格写 `未知`。该列口径必须与内容列表阶段一致（同为真实点赞数），同一列不得混两种口径。

### 2.2a 小红书互动数据兼容规则

小红书详情页优先从当前页面内嵌状态按目标 `note_id` 精确定位互动对象：

```text
window.__INITIAL_STATE__.note.noteDetailMap[<note_id>].note.interactInfo
```

兼容读取顺序固定如下，命中第一条与当前目标身份一致的路径后停止：

1. `note.noteDetailMap[<note_id>].note.interactInfo`（当前 Chrome 页面结构）
2. 当前目标 note 对象上的 `interactInfo`（旧版页面结构）
3. 当前目标 note 对象上的 `interactionInfo`、`interact_info` 或 `interaction_info`（API/历史结构）

作者账号 ID 同样按来源兼容：优先读取 `note.user.userId`，再兼容 `user_id`、`userid`、`id`。互动对象中的字段按以下别名读取：

| 输出字段 | 当前页面字段 | 兼容字段 |
| --- | --- | --- |
| 点赞数 | `likedCount` | `liked_count`、`like_count` |
| 收藏数 | `collectedCount` | `collected_count`、`collect_count` |
| 分享数 | `shareCount` | `share_count` |
| 评论数 | `commentCount` | `comment_count` |

所有路径和字段均区分大小写。兼容查找只能发生在已通过 `note_id` 身份校验的当前 note 对象内；不得对整个状态树做无边界键名搜索，也不得把其他笔记、列表摘要或推荐内容中的数字当作当前内容互动数据。实际命中的来源和路径应写入过程记录，candidate 仍只写规范化后的互动字段。

### 2.2b 平台页面行为提示（经验，非契约）

以下提示来自实际取数时的观察，用来判断页面数据的可信度。平台页面结构可能变化，这些提示不是校验条件，也不替代本协议其他条款；以本轮探索结果为准。

- 页面上的互动数在数量较大时会被缩写，属于展示值，不能当精确值使用。
- 视频页与图文页的页面结构不同，同一字段的位置和文本形态都可能不一样，不能把一种页面的读法直接套到另一种。
- 页面内嵌状态、页面可见展示值、详情接口响应三者可信度不同，需要精确值时优先靠前的来源。

### 2.3 小红书视频链接

确认目标 note 身份后，按以下顺序取第一个合法的 `http://` 或 `https://` 地址：

1. **先要完整文件，不要分片**：递归遍历 `note.video.media.stream` 下的编码档位和数组元素，只取**以 `.mp4` 结尾**的 `masterUrl` / `master_url`（例如 `stream.EF4[0].masterUrl`）。同一个 stream 里往往同时有 `.m4s` 和 `.mp4` 两种地址，**跳过 `.m4s`**。
2. stream 里只有 `.m4s`（或没有合法地址）时，**读页面 `<video>` 元素的 `currentSrc`，为空再读 `src`** —— 页面实际在播的就是完整 mp4，实测形如 `…/110/258/xxxx_258.mp4?sign=…`。
3. 仍拿不到时，再读 `note.video.masterUrl`、`note.video.master_url`、`note.video.video_url` 和 `note.video.consumer.origin_video_key`，同样优先 `.mp4`。
4. `blob:`、空值、非 HTTP(S) 值、`mediaV2` 内的视频地址以及 MP4/M3U8 等媒体请求地址都不能作为视频下载链接。
5. **写入 `视频下载链接` 的地址不得以 `.m4s` 结尾**（2026-11-01 实测：`.m4s` 是分片，火山 ASR 会直接拒，返回 `45000151 音频格式不正确`）。整条视频确实只有 `.m4s` 时，允许写入但必须在 `备注` 写明「视频直链只有 m4s 分片，ASR 可能失败」，不得把它当成正常直链。

经验提示（非契约，以本轮探索为准）：页面上的视频播放地址可能是流式地址；`.m4s` 的签名绑在它自己的路径上，**不能靠改扩展名或改档位目录拼出 `.mp4`**（实测改完一律 403），只能重开页面重新取。

### 2.3a B 站 ASR 音频流地址

确认当前页面已取得目标 `bvid / aid / cid` 后，才允许为 B 站视频补取临时流地址：

0. **先确保视频已进入播放状态**（2026-09-14 实测）：页面刚导航完成时播放器尚未加载流，此时 Network 里没有任何 `.m4s` 请求。先触发一次播放（如 `video.play()`）并稍等，再取快照。
1. 仅调用一次 `list_network_requests`，并以该次返回的请求列表作为唯一快照。**注意**：`resourceTypes` 传 `media` 可能筛不到结果，`.m4s` 实测落在 `xhr` / `fetch` 里，筛不到时改用这两类。
2. 只筛选 `GET`、当前 `cid` 对应的音频资源路径、`.m4s` 音频流请求，优先选择 `codecs` 为 `mp4a` 的音频轨道。B 站音频轨的路径形如 `<cid>-<档位>-302xx.m4s`（`30216` / `30232` / `30280`，`30280` 为常见音频档），视频轨是另一个档位（2026-09-14 实测为 `<cid>-1-30112.m4s`），不要取错。
3. 读取完整请求 URL，保留 `?` 后的全部参数；必须包含 `deadline`、`upsig` 或 `sign` 等签名/过期参数。等价且更干净的做法：从当前页面的 `performance.getEntriesByType('resource')` 里按 `<cid>` + `-302xx.m4s` 过滤取完整 URL，可避免 `get_network_request` 内联输出带出 Cookie。
4. 将该地址写入现有字段 `视频下载链接`，不新增 B 站专用字段，不把 `.m4s` 改成 `.mp4`。
5. 不从旧表格、历史暂存目录或模型文本复制、拼接 URL。该地址只作为本轮 ASR 的临时输入。

### 2.3b B 站字幕地址

每次获取 B 站字幕时，都必须在当前页面会话中重新请求播放器字幕接口，并立即使用该次响应返回的 `subtitle_url`：

1. 使用当前目标的 `bvid / aid / cid` 请求 `x/player/v2`。
2. 从本次响应的 `data.subtitle.subtitles` 读取字幕条目，并选取目标语言字幕。
3. 立即请求本次返回的 `subtitle_url`，读取完整 `body`；`//` 开头的地址补 `https:` 后再请求。
4. `subtitle_url` 带有短时效 `auth_key`，不得复用上一次运行、旧页面快照或历史暂存目录中的字幕地址。
5. 字幕请求失败时重新执行第 1-3 步获取一次新地址；仍失败才报告字幕获取失败，不把旧地址继续交给后续步骤。
6. **取到地址不等于取到正确字幕**（2026-09-14 实测案例）：同一次会话内两次请求 `x/player/v2`，返回的 `subtitle_url` 分别指向与本视频无关的字幕（时长 791s / 309s，而本视频为 440s）。拿到字幕后必须先做两项身份核对，任一不符就重新执行第 1-3 步，而不是直接采用：
   - `subtitle_url` 的路径里应内嵌当前 `aid` 与 `cid`；
   - 字幕时间轴的终点**不得超过视频时长**。注意是单向判据：字幕只覆盖有人声的片段，**可以明显短于视频时长**（2026-09-14 实测一份正确字幕终点 55.72s，而同视频为 88s——尾部是无声画面，属正常）。反过来，终点明显超过视频时长（如 791s / 309s 对 440s）说明取错了。不要用"接近视频时长"当判据，那会把正确字幕误判为错。
   两次都对不上时，按「平台字幕不可用」处理并写明真实原因，回退到 ASR，不得采用错配字幕。

### 2.3c 抖音视频下载链接

抖音详情页的视频源不能直接从 `video` 元素的 `src` 取（实测为空或 `blob:`）。页面用 MSE 播放时 `currentSrc` 恒为 `blob:`，播放元素和媒体请求里都没有可转写地址：媒体流里那类 `media-video-hvc1` 是 H.265 分路，火山由服务端拉流，会报 `45000006` / `45000151`。

**抖音的可用地址在详情接口里**（2026-11-04 实测）：打开公开视频页后，页面会自己发起 `aweme/v1/web/aweme/detail/`（host 可能是 `www.douyin.com`，也可能是 `www-hj.douyin.com`），响应 JSON 的 `aweme_detail.video` 带 H.264 的 mp4 直链。按以下顺序取：

1. 打开该内容的公开视频页（`https://www.douyin.com/video/<原生内容ID>`）。
2. 只调一次 `list_network_requests`，筛 `GET`、路径含 `/aweme/v1/web/aweme/detail/`、查询参数 `aweme_id` 精确等于当前原生内容 ID 的那一条（同一 ID 可能有多次请求，取任意一条 200 的即可）。
3. 对该请求调一次 `get_network_request`，把响应写入调用方给的 `raw_detail_output_path`；只从该文件读 JSON。
4. 校验 `aweme_detail.aweme_id` 等于当前原生内容 ID，再取 `aweme_detail.video.play_addr_h264.url_list`；没有时取 `play_addr.url_list`。选第一条带 `mime_type=video_mp4` 的地址。
5. **不能用** `play_addr_265`、`bit_rate` 里的 H.265 档、路径含 `hvc1` / `h265` / `hevc` / `media-video-hvc` 的媒体流，也不能用 `blob:`、`.m4s`、`.m3u8`。
6. 拿到候选后先在本机做一次 `Range: bytes=0-1023` 的 GET 预检：HTTP 200/206、`Content-Type` 含 `video/mp4`、响应体非空。不通过就换 `url_list` 的下一条，不要把坏地址交给 ASR，也不要在同一条坏地址上反复重试。
7. 该地址带短时效签名（`dy_q`、`l` 等参数），只能作为本轮 ASR 的临时输入，不得写入表格后长期复用，也不得从旧暂存目录或历史运行拷贝。

详情接口取不到（请求不存在、响应非 200、`aweme_id` 不匹配）时，才退回读该 `video` 元素的 `currentSrc`、为空再读 `src`（`blob:` 直接丢弃）；仍没有就如实记录「视频缺少视频下载链接」，不要从媒体流里拼地址。

抖音的 `SSR_RENDER_DATA` / `_ROUTER_DATA` 里没有视频地址（2026-11-04 实测只有用户信息和封面图），不要在那里找。标题、互动数、发布时间仍可直接从详情区 DOM 读取（`detail-video-info`、`detail-video-publish-time` 等），作者账号 ID 出现在页面 HTML 的 `MS4wLjABAAAA…` 形式字符串中。

自账号有 `2.5` 保存的 `创作者中心数据/作品列表原始响应.json` 时，优先用其中的 `play_addr`（创作者中心 cold 域名的直链火山能拉取）；本节的公开页取链只作兜底（同一视频的公开页 web 域名现在会被火山判 `45000006`）。

### 2.4 一次 Network 探索

仅在页面状态缺少必需字段，或调用方明确需要原始详情/平台字幕证据时执行：

1. 最多调用一次 `list_network_requests`，把该次返回的请求列表作为唯一快照。
2. 根据平台、请求方法、请求路径和请求参数筛选与当前内容 ID 精确匹配的详情请求。
3. 只对命中的详情请求调用一次 `get_network_request`，并将响应写入调用方提供的 `raw_detail_output_path`。禁止使用 `output_path`、临时猜路径，或从 MCP 内联文本复制、拼接 JSON。
4. 只从该原始响应文件读取 JSON，并将该文件保留在本轮 staging；原始平台字段不直接追加到 candidate。
5. 请求未命中、响应文件缺失/为空/无法解析或身份不匹配时，记录真实的 `raw_detail_unavailable` 原因；不得翻页、改变 `pageSize`、扩大扫描范围或再次查询 Network。
6. 普通平台仍排除媒体请求；B 站仅允许为当前 `cid` 捕获一次匹配的音频 `.m4s` 请求，该地址只作为本轮 ASR 临时输入。列表、评论、回复、搜索、推荐、埋点、图片、视频 `.m4s`、MP4、M3U8 和其他不匹配的媒体流都不是候选。

小红书当前 MVP 的详情请求识别规则是 `POST /api/sns/web/v1/feed`，请求或响应中的原生内容 ID 必须精确等于当前目标 `note_id`。常见响应包装优先解析 `.data.items[].note_card`；该路径不能套用于其他平台。

但小红书笔记详情页在页面结构化状态 `noteDetailMap[<note_id>]` 已经带全所需字段时**不会**发出该请求。这时不要为了凑出一份原始响应去反复扫描 Network：按 `raw_detail_unavailable:页面结构化状态已覆盖` 记录真实原因并继续，所需字段直接取自页面结构化状态。只有该请求真实存在时才抓取并落盘。

### 2.5 一次 DOM 降级

Network 仍不足时，最多执行一次 DOM 降级：

1. 只定位当前页面可见的目标字段，并投影为预定义单值字段。
2. 不循环尝试选择器、脚本、页面层级或其他请求。
3. 仍缺字段时停止取数并写明真实缺失字段，不补值、不猜值。

## 3. 输出

### 3.1 单条 candidate 或账号批处理 item

输出模式由调用方在输入中固定，不能混用。

- **单条 candidate 模式**：把单条取数结果按 `canonical_contract` 写入 `candidate_output_path`。candidate 只能包含该 contract 允许的字段，不得添加平台原始对象、调试字段或自定义 `field_sources` 字段。
- **账号批处理 item 模式**：把单条取数结果按 Chrome 整批结果 schema 写入 `batch_result_path` 中 `batch_item_index` 对应的既有 item。item 的 `request_url` 必须填写当前地址栏真实 URL，`observed_content_identity` 必须从当前加载页面提取，不能复制 `expected_content_identity`；失败 item 必须保留真实 `failure_reason`，缺失字段仅写入该原因文本，不新增 schema 外字段。
- 身份比对按平台统一书写形式进行：B站 只比 `bvid`，两侧都会归一到**大写**后再比，因此 `observed_content_identity` 写成页面原样的大小写或直接写大写都可以，只有大小写不同不判为不一致；除大小写外的任何差异仍然判为身份不一致。归一规则只影响比对，写入 item 的值保持你从页面提取到的原始字符串。

成功结果至少应包含对应 contract 要求的身份和基础字段；缺失的可选字段保留为空。失败结果仍写入其预先分配的位置，并包含：

```text
状态或 status = 失败
失败原因或 failure_reason = 真实失败原因
```

候选或批量 item 必须通过 `JSON文件写入执行器.py` 原子写入。写入成功的最低条件是收到路径匹配的回执：

```text
ok = true
status = written
output_path = candidate_output_path 或 batch_result_path
```

经验提示（非契约）：写入器的输入 JSON 也必须位于本轮 staging 目录内；跨目录暂存请改用标准输入（不传 `--input-path`）。

#### 单条 candidate 固定写入合同

本合同只适用于本协议的**单条 candidate 模式**；账号批处理 item 仍按调用方提供的整批结果容器和 schema 写入，不套用本节命令。

- `document-type`：固定为 `temporary-workflow-input`；
- 输入方式：固定将完整 candidate JSON 原样传入标准输入；
- `schema-path`：本模式不传；
- `output-path`：固定使用调用方提供的 `candidate_output_path`；
- candidate 必须先按 `canonical_contract` 组织完成，再调用写入器；
- 不得为确认上述参数读取 `JSON文件写入执行器.py` 源码、查询其他 `document-type`，或自行推导路径规则。

执行命令：

```bash
python3 "<工作区根目录>/执行单元/JSON文件写入执行器.py" \
  --workspace-root "<工作区根目录>" \
  --document-type temporary-workflow-input \
  --output-path "<candidate_output_path>"
```

只有收到以下回执，才算单条 candidate 写入成功：

```text
ok = true
status = written
output_path = candidate_output_path
```

写入失败、回执路径不匹配，或账号批处理模式中改变了其他 item、顶层 schema、条目数量或顺序时，不能报告 Chrome 取数成功；调用方不得自行生成、校验或读取替代输出。

### 3.2 原始 Network 文件

只有实际执行 `get_network_request` 并成功写入时，才生成 `raw_detail_output_path` 对应的原始 JSON 文件。它是平台原始证据，不是 candidate，不作为基础字段直接消费。

如果本轮没有执行或没有成功保存 Network 详情，不得创建空文件或伪造原始详情路径；后续流程必须按 `raw_detail_unavailable` 处理。

### 3.3 正文边界

candidate 中的 `正文文本` 只能填写作者发布页真实可见的正文。标题、搜索摘要、`desc`、`chapter_abstract`、平台字幕和 ASR 结果不能冒充作者正文。

平台字幕和 ASR 属于后续正文证据流程，按各自执行单元单独保存和消费，不扩展 Chrome candidate 的字段边界。

## 4. 调用方承接

对标内容流程直接将本条流程的 `candidate_output_path` 指向 `内容基础结构化数据-Chrome.json`。收到路径匹配的 `written` 回执后，流程将该路径记为基础数据结果路径并继续自己的校验和提交，不再次读取 candidate。

账号详细流程必须先执行 `prepare-chrome`，并只读取其返回的批次计划、整批结果模板和 schema。首个**成功完成完整探索**的内容使用本协议的账号批处理 item 模式；失败的首条目标照实写入失败 item，不得把它误当作已冻结的方法。当前 AI/Chrome 会话在任一目标成功后冻结本次成功的方法（数据来源、字段路径、Network 请求识别规则和 JSON 路径）；后续目标只复用该方法。复用失败、身份不匹配或结构校验失败时，允许该目标按本协议再执行一次有界探索。

协议只定义单条页面取数及当前 item 的写入边界；账号流程仍独占批次计划、整批结果容器、条目顺序、`commit-chrome` 身份校验和正式 CSV 提交。协议不会直接写正式 CSV，也不能替代 `commit-chrome` 或 `collect-details`。
