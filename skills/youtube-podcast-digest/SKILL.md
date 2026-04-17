---
name: youtube-podcast-digest
description: Produce a Chinese-first weekly digest of subscribed YouTube podcast channels (VC / AI / public markets / Chinese tech / deep-dive company studies). Fetches the last 7 days of new episodes, generates a 200–300-char Chinese TL;DR per episode from full captions (via yt-dlp), persists per-episode files plus an aggregated weekly report under ~/.oh-my-agent/reports/youtube-podcast-digest/weekly/<ISO-week>/. Use when the user asks for a YouTube podcast weekly, a roll-up of new VC/AI podcast episodes, or a subscribed-channel digest. Unlike youtube-video-summary (single-link ad-hoc) and market-briefing (Xiaoyuzhou Chinese feeds), this skill is the YouTube subscription pool.
metadata:
  timeout_seconds: 1800
---

# YouTube Podcast Digest

订阅池 YouTube 播客周报。一次 `weekly_digest` mode；每集生成字幕级中文 TL;DR，然后按 group 汇总。

## When to use

- 用户要本周 YouTube 订阅频道周报
- 用户问"最近 a16z / Dwarkesh / Acquired 等有什么新集"并期望一份整合报告
- 需要固定版面的 YouTube 播客跨频道对比

不适用的场景：
- **单个 YouTube 链接的临时总结** → 用 `youtube-video-summary` skill
- **中文小宇宙 podcast 的每日简报** → 用 `market-briefing` skill（finance/ai daily 自带播客段）
- **B 站视频** → 用 `bilibili-video-summary` skill

## 订阅池

订阅列表在 [references/channels.yaml](references/channels.yaml)，按 5 个 group 组织：

- `ai`：Dwarkesh Patel、No Priors、Latent Space
- `vc`：a16z、Sequoia Capital、20VC
- `public_markets`：Bg2Pod、All-In
- `china_tech`：xiaojunpodcast、硅谷101
- `deep_dive`：Acquired

添加/删除频道：直接编辑 `channels.yaml`，新增项的 `channel_id` 留 `null`；首次运行 `channel_fetch.py` 会自动解析 `@handle` 并写回。

## Required workflow

1. **确认报告窗口与 label**：
   - 默认：当前本地日历周（由 `OMA_REPORT_TIMEZONE` / `TZ` 或系统时区推导），label `YYYY-Www`（例 `2026-W16`），窗口 **7 天**。
   - 用户显式指定 ISO 周 → 用用户给的 `YYYY-Www`，窗口仍 7 天。
   - 用户要求补看更长窗口（例如"看过去 30 天"、"catch up last month"、"补一下上个月没看的"）→ 进入 **catch-up 模式**：
     - label 改为 `catchup-<N>d-<end-date>`，例如 30 天补看报告当日是 2026-04-16 → `catchup-30d-2026-04-16`
     - step 3 的 `--since-days` 改成用户指定的 N
     - step 5 的 Markdown 标题用 `# YouTube Podcast 补看 · <start-date> → <end-date> (<N> 天)` 代替"周报"版式；JSON 的 `iso_week` 字段存上面的 `catchup-<N>d-<end-date>` label
     - 其他步骤（逐集 TL;DR、persist、_episodes/ 结构）完全保持不变

2. **加载历史上下文**（可选但推荐）：
   ```bash
   ./.venv/bin/python skills/youtube-podcast-digest/scripts/report_store.py context --weeks 4
   ```
   读取最近 4 周已存报告的 JSON，作为"跨集主题观察"的参考底。

3. **抓新集**：
   ```bash
   ./.venv/bin/python skills/youtube-podcast-digest/scripts/channel_fetch.py --since-days <N>
   ```
   `N` 按 step 1 决策（默认周报 `7`；catch-up 模式用用户指定的天数）。返回 JSON 数组，每条含 `name / group / video_title / video_url / video_id / published_at / description_snippet` 等字段。若返回空数组，直接写"该窗口订阅频道暂无新集"并跳到 step 6。

4. **逐集生成 TL;DR**（对 step 3 的每条新集独立执行，**不批处理**）：
   - 抓字幕：
     ```bash
     ./.venv/bin/python skills/youtube-video-summary/scripts/extract_youtube.py --url "<video_url>"
     ```
     （脚本永远返回全量字幕，无截断入口。）
   - 根据返回 `status` 字段分支：
     - `transcript_backed` → 基于 `transcript` 字段全量内容产出 **200–300 字中文 TL;DR**，强调主论点 + 关键论据 + 可操作观察
     - `metadata_only` → 基于 `description` 产出 **80–120 字中文概要**，TL;DR 前加斜体小字 `_基于 metadata，无字幕_`
     - `error` → 跳过 TL;DR 生成，只记录标题 / 链接 / 时长 / 失败原因
   - 立即把该集 TL;DR 写到单独文件（见 [references/report_schema.md](references/report_schema.md) 里 `_episodes/<slug>.md` 格式）：
     ```bash
     ./.venv/bin/python skills/youtube-podcast-digest/scripts/report_store.py write-episode \
         --week <ISO-week> --slug <group>__<video_id> --md-stdin < /tmp/episode.md
     ```
   - **关键**：一集处理完再处理下一集。不要把多集字幕同时放在 context 里，那会导致长字幕累积。

5. **最终汇总**：所有单集 `_episodes/*.md` 就绪后，读回它们（总字数 ≈ 集数 × 300，可控），按 [report_schema.md](references/report_schema.md) 的 Markdown 结构组装最终 `report.md` + `report.json`：
   - 5 个 group 小节固定顺序
   - 每 group 按 `published_at` 倒序
   - 空 group 保留标题 + "本周暂无新集。"
   - 追加"本周跨集主题观察"（3–5 条，基于 TL;DR 不是原字幕）
   - 追加 "Coverage Notes"（至少列出本周未发布的频道）

6. **持久化**：
   ```bash
   ./.venv/bin/python skills/youtube-podcast-digest/scripts/report_store.py persist \
       --week <ISO-week> --md-path /tmp/report.md --json-path /tmp/report.json
   ```
   存到 `~/.oh-my-agent/reports/youtube-podcast-digest/weekly/<ISO-week>/report.md|report.json`。

7. **最终回复**：直接贴 Markdown 正文 + 存储路径。无需二次总结。

## Source rules

严格遵守 [references/source_policy.md](references/source_policy.md)：

- 只有两类 evidence：`youtube_subtitles` / `youtube_metadata`
- 不允许外部 web 搜索、评论区、其他 podcast 平台
- 字幕级 TL;DR 必须基于全量转写（脚本已改为永远全量返回）
- 观点必须能映射回某集字幕或 description

## 跨集主题观察规则

- 仅基于当期 `_episodes/*.md`，不引入未包含的视频
- 3–5 条足够，不要凑数
- 每条必须指明来源频道或视频 URL
- 单一来源的主题也能成立，但要明确写"仅 X 集提及"
- 历史周报（从 `report_store.py context` 拿到的）只用来判断主题是否"延续"，不用来填内容

## Episode slug

`_episodes/*.md` 的 slug 格式统一为 `<group>__<video_id>`，例如：

- `ai__-TjtKCFVuCo`
- `vc__abc123xyz9`
- `china_tech__aBcDeFgHiJk`

**避免碰撞**：不同 group 可能有同一个视频（交叉上传）；加 group 前缀保证唯一。

## Failure modes

- `channel_fetch.py` 返回空且 stderr 有 handle 解析失败 → 不要自动重试，直接在 `Coverage Notes` 里记"handle 解析失败"，让用户手动修 yaml
- `extract_youtube.py` 单集失败 → 按 `metadata_only` 降级，若连 metadata 也拿不到，记录到 `coverage_notes.transcript_failures`
- 单集 TL;DR 生成异常（字幕极短 / 语言错配）→ 在该集 TL;DR 里说明具体原因，不要掩盖
- 全部 11 频道本周都无新集 → 报告照样写，标题保留，正文写"本周订阅频道暂无新集"，Coverage Notes 列出全部 11 频道

## Density rule

- 不要写"本期内容丰富"这种空话
- TL;DR 必须包含至少一个可操作观察（新观点 / 新论据 / 新预测）；没有就写"本集为访谈预告，无新论证"
- "跨集主题观察"每条必须是**跨集或跨来源**的判断，不允许只用一集就成一条

## Report structure 参考

完整 Markdown + JSON 契约见 [references/report_schema.md](references/report_schema.md)。每次起草前先读一遍。
