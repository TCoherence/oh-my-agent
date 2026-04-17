# Source policy — YouTube Podcast Digest

本 skill 的信息源只有两类，必须在每集 TL;DR 的 `evidence` 字段中显式标注。

## `youtube_subtitles`（主路径）

- 用 [`skills/youtube-video-summary/scripts/extract_youtube.py`](../../youtube-video-summary/scripts/extract_youtube.py) 抓取
- 脚本现在永远返回**全量字幕**（相关的 `--max-transcript-chars` 已移除）
- 支持 `en` 与 `zh` 自动/人工字幕
- TL;DR 基于完整转写产出：200–300 字中文，主论点 + 关键论据 + 可操作观察
- 绝不允许基于部分字幕就下跨段结论——若字幕异常短（< 500 字），按 `youtube_metadata` 处理并在 `coverage_notes` 里标注

## `youtube_metadata`（降级路径）

当视频字幕不可用（直播回放、仅音轨、频道禁用、yt-dlp 报错）时才允许走这条路：

- 仅基于 YouTube description + title
- TL;DR 80–120 字中文
- **必须**在 Markdown 的 TL;DR 前加一段斜体小字 `_基于 metadata，无字幕_`
- JSON 里 `transcript_available=false`，`evidence="youtube_metadata"`

## 禁止的源

- 外部网络搜索（跨站综述、新闻报道）：**不允许**。本 skill 只依赖 YouTube 本身的字幕/描述。若某集确实值得深挖周边报道，走 `youtube-video-summary` 或 `market-briefing` skill，不要在本报告里混入。
- 频道主页 tab 文案、评论区：**不允许**。仅 per-video 的字幕与 description。
- 其他 podcast 平台（Spotify、Apple Podcasts）的 shownotes：**不允许**，源头不可控。

## 推断与猜测的边界

- 所有观点必须能映射回某集字幕或 description
- 不要引用"你可能知道"的背景，除非该背景是 TL;DR 理解的关键前提（且 ≤ 10 字）
- 嘉宾身份不确定时，宁可写"嘉宾（姓名未在字幕中出现）"也不要猜错名字
- "本周跨集主题观察"章节只能基于当期 `_episodes/*.md` 的内容，不允许引入未包含的视频或外部事件

## 交叉验证

同一事件若在多集出现（例如同一模型发布被 Bg2Pod 和 Latent Space 都讨论），`cross_cut_themes` 条目可列出两个视频 URL 作为 `evidence_video_urls`。单一来源的主题也可以成为一条观察，但要明确写"仅 X 集提及"。
