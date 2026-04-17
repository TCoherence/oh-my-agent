# Report schema — YouTube Podcast Digest

本 skill 仅有一种 report 形态：`weekly_digest`。产出两份文件（Markdown 人读 + JSON 机读），并附带每集一份独立 `_episodes/*.md` 作为溯源中间产物。

## 存储路径

```
~/.oh-my-agent/reports/youtube-podcast-digest/weekly/<ISO-week>/
├── report.md          # 人读周报
├── report.json        # 机读 sidecar
└── _episodes/
    └── <group>__<video_id>.md   # 每集 TL;DR（溯源）
```

`<ISO-week>` 形如 `2026-W16`（用 `date.isocalendar()` 派生）。

## JSON sidecar schema

所有字段都是必需的（空列表用 `[]`、空字符串用 `""`），除非标注 `optional`。

```json
{
  "version": 1,
  "mode": "weekly_digest",
  "iso_week": "2026-W16",
  "period_start": "2026-04-13",
  "period_end": "2026-04-19",
  "generated_at": "2026-04-19T22:30:00+08:00",
  "report_timezone": "Asia/Shanghai",
  "channel_count": 11,
  "episode_count": 0,
  "transcript_coverage": 0,
  "episodes": [
    {
      "name": "Dwarkesh Patel",
      "group": "ai",
      "handle": "@DwarkeshPatel",
      "channel_url": "https://www.youtube.com/@DwarkeshPatel",
      "video_title": "...",
      "video_url": "https://www.youtube.com/watch?v=...",
      "video_id": "...",
      "published_at": "2026-04-15T15:00:00+00:00",
      "duration_seconds": 8040,
      "language": "en",
      "transcript_available": true,
      "tldr": "<200–300 字中文 TL;DR，转写级证据>",
      "evidence": "youtube_subtitles"
    }
  ],
  "cross_cut_themes": [
    {
      "title": "主题标题",
      "summary": "1–2 句中文总结",
      "evidence_video_urls": ["https://youtube.com/watch?v=..."]
    }
  ],
  "coverage_notes": {
    "silent_channels": ["频道名1", "频道名2"],
    "transcript_failures": [
      {"name": "xxx", "video_url": "...", "reason": "no_captions|yt_dlp_failed|..."}
    ]
  }
}
```

### 字段约束

- `mode`：固定 `"weekly_digest"`
- `iso_week`：`YYYY-Www`，与 `period_start` 的 ISO 周一致
- `period_start` / `period_end`：该 ISO 周的周一与周日（按 `Asia/Shanghai` 或 `OMA_REPORT_TIMEZONE`）
- `channel_count`：`channels.yaml` 中所有频道总数
- `episode_count`：`episodes[]` 长度
- `transcript_coverage`：`episodes[]` 中 `transcript_available=true` 的比例（0.0–1.0，小数）
- `evidence`：每集必选一个——`"youtube_subtitles"` 或 `"youtube_metadata"`
- `transcript_available=false` 时，`tldr` 基于 description 产出 80–120 字概要，`evidence="youtube_metadata"`
- `tldr` 字段：字数按来源决定
  - `youtube_subtitles`：200–300 字中文
  - `youtube_metadata`：80–120 字中文

## Markdown 结构（最终 `report.md`）

```markdown
# YouTube Podcast 周报 · 2026-W16 (Apr 13–19)

> 覆盖 11 个订阅频道 · 本周共 N 条新集 · 字幕覆盖 M/N

## 🤖 AI 访谈 (Dwarkesh / No Priors / Latent Space)

### [Dwarkesh Patel — Ep title](https://youtube.com/watch?v=...)
- 2h14m · 发布 Apr 15 · 字幕：英文自动
- **TL;DR**：<200–300 字中文 TL;DR>

### [No Priors — Ep title](url)
- ...

## 💰 VC 机构 / 访谈 (a16z / Sequoia / 20VC)

### ...

## 📈 公开市场 (Bg2Pod / All-In)

### ...

## 🌏 中文科技 (xiaojunpodcast / 硅谷101)

### ...

## 🏛️ 公司深潜 (Acquired)

### ...

## 本周跨集主题观察

- **主题 1**（来源：Dwarkesh、Bg2Pod）：1–2 句跨集提炼
- **主题 2**（来源：Acquired）：1–2 句跨集提炼

## Coverage Notes

- 本周未发布：a16z、Sequoia Capital
- 字幕获取失败：（如有，列明频道 + 集名 + 原因）
```

### 章节规则

- 固定 5 个 group 小节（按 `ai → vc → public_markets → china_tech → deep_dive` 顺序）
- 每个 group 内按 `published_at` 倒序
- 若某 group 本周无新集，依然保留标题，正文写 `本周暂无新集。`
- `本周跨集主题观察`：3–5 条，基于本周所有 TL;DR 而非原字幕
- `Coverage Notes`：永远存在，至少列出本周"未发布"的频道

## 单集 `_episodes/<slug>.md` 结构

`slug` = `<group>__<video_id>`（避免跨 group 同 id 碰撞）。

```markdown
# [Dwarkesh Patel — Ep title](https://www.youtube.com/watch?v=abc123)

- Group: ai
- Channel: @DwarkeshPatel
- Published: 2026-04-15
- Duration: 2h14m
- Language: en (auto)
- Evidence: youtube_subtitles

## TL;DR

<200–300 字中文，主论点 + 关键论据 + 可操作观察>
```

这些文件由 `scripts/report_store.py write-episode` 原子写入，汇总阶段被读回拼成 `report.md`。不要手工删，保留作为溯源资产。
