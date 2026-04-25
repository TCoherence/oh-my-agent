---
name: jensen-huang-speech-research
description: Research and analyze Jensen Huang's public speeches, keynotes, dedicated interviews, guest podcast appearances, fireside chats, and investor events across a user-specified time window (default past 1 month; supports past-2w / past-1m / past-3m / past-6m / past-1y / past-3y / bootstrap / custom date range). Produces a structured Chinese-language report covering AI development vision, Nvidia strategy, product announcements, and recurring themes. Use when the user asks about Jensen Huang's speeches, Nvidia CEO remarks, GTC keynotes, podcast guest appearances, or wants a rolling / historical compilation of his public statements.
metadata:
  timeout_seconds: 1500
  max_turns: 60
---

# Jensen Huang Speech Research

Research Jensen Huang (黄仁勋) 在用户指定时间窗口内的公开演讲、主题演讲、深度访谈、做客播客和投资者活动，生成结构化的中文研究报告。默认做滚动窗口（过去一个月），也支持用户显式指定 bootstrap / past-3y 做完整历史回顾。

## When to use

- 用户想了解 Jensen Huang 关于 AI 发展的观点和公开发言
- 用户需要 Nvidia CEO 公开发言的系统性整理
- 用户对 GTC / CES / Computex / SIGGRAPH 等大会主题演讲感兴趣
- 用户提到 "黄仁勋演讲"、"Jensen Huang speech"、"GTC keynote"、"Jensen 做客 XX 播客"、"Jensen 最近说了啥" 等关键词
- 用户要做定期 rolling review（过去两周 / 过去一个月 / 过去一季度）
- 用户第一次使用，想做 bootstrap（过去 3 年全量）

## Step 0: Parse time window (REQUIRED, do first)

从用户 prompt 中识别时间窗口，映射到下列 token 之一：

| Token | 含义 | 窗口模式 |
|-------|------|---------|
| `past-2w` | 最近 14 天 | short |
| `past-1m` | 最近 30 天 | short |
| `past-3m` | 最近 90 天 | short |
| `past-6m` | 最近 180 天 | long |
| `past-1y` | 最近 365 天 | long |
| `past-3y` / `bootstrap` | 最近 3 年 (2023-01 至今) | long |
| `custom` | 用户指定起止日期 (`YYYY-MM to YYYY-MM` 或 `YYYY-MM-DD to YYYY-MM-DD`) | <90d short, 否则 long |

**中文 / 英文自然语言映射示例**：
- "过去两周" / "最近 2 周" / "past 2 weeks" → `past-2w`
- "过去一个月" / "最近一个月" / "past month" → `past-1m`
- "过去三年" / "从头" / "历史" / "全量" / "bootstrap" → `past-3y`
- "从 2025 年 1 月开始" / "2024-01 到现在" → `custom`

**用户没明说时间窗口时，默认用 `past-1m`。** 不要擅自假定是 bootstrap — 只有用户显式说「从头 / 全量 / 过去三年」才用 `past-3y`。

解析完成后，在报告头部显式声明窗口决策，便于用户纠正。

### 窗口模式 → 工作预算与报告骨架

| 模式 | turn 预算 | 报告骨架 |
|------|----------|---------|
| **short** (≤ 90 天) | ≤ 25 turns | ① 窗口概览 ② 本窗口重点事件 ③ 核心引述 ④ 相对上期的增量观察 ⑤ 即将到来的日程 |
| **long** (> 90 天) | ≤ 60 turns | ① 执行摘要 ② 演讲时间线 ③ 主题深度分析 ④ 经典引述集 ⑤ 观点演进追踪 ⑥ 关键发现 |

## Coverage — 需要覆盖的事件类别

在当前窗口内按类别穷举，**不要只盯 flagship keynote**：

1. **Flagship keynotes**（旗舰主题演讲）
   - GTC（常规 3 月 + GTC DC / GTC Taipei / GTC Paris / GTC Washington 等区域场）
   - CES（1 月）
   - Computex（5-6 月）
   - SIGGRAPH（7-8 月）
2. **Earnings calls**（Nvidia 季度财报电话会上的开场 + Q&A 发言）
3. **Analyst Day / Investor Day**（独立于 earnings，常附在 GTC 周）
4. **Dedicated media interviews**（独立长访谈）
   - 英文：CNBC, Bloomberg TV, WSJ, FT, The Verge, Wired, Fortune, Nikkei Asia, Yahoo Finance
   - 中文：央视财经、第一财经、钛媒体、台媒（TVBS、风传媒、数位时代）
5. **Guest podcast appearances**（做客他人播客）
   - 重点目标：Acquired, BG2 Pod (Gurley + Gerstner), Stratechery, All-In, Lex Fridman, No Priors, Hard Fork, Patrick Collison / Stripe Sessions, Possible (Reid Hoffman), In Good Company (Nicolai Tangen)
6. **Fireside chats / summits**（峰会炉边对谈）
   - Goldman Communacopia, Milken Global, Sun Valley, DealBook Summit, Vanity Fair New Establishment, WEF Davos, Code Conference, Wired Summit
7. **University & commencement**
   - Stanford, Caltech（他做过毕业典礼演讲）、National Taiwan University (NTU)、Oregon State University（母校）
8. **Industry events / panels**（AI Summit, AI Everywhere, Snowflake Summit, Databricks, Dell Tech World, HP Amplify 等）
9. **Congressional / regulatory appearances**（如听证会、政府对话，若窗口内发生）
10. **Live media events / live Q&A**（Vanity Fair live、WIRED25、CNBC live special）

**覆盖原则**：
- 窗口越短（past-2w / past-1m） → coverage 越「**全**」（尽量穷举所有出场）
- 窗口越长（past-1y / past-3y） → coverage 越「**代表性**」（每类挑最有分量的 1-3 个）

## Required workflow

### Step 1: List prior reports for incremental reuse

```bash
REPORT_DIR="$HOME/.oh-my-agent/reports/jensen-huang-speech-research"
mkdir -p "$REPORT_DIR"
ls -t "$REPORT_DIR"/*.md 2>/dev/null | head -5
```

读取最近 1-2 个 prior report 的**头部**（执行摘要 / 窗口概览 + 时间线），作为「已覆盖内容」参考。目的：
- 知道哪些事件已经深度写过，新报告不必重述，只需引用
- 知道 prior 窗口的结束日期，作为「相对上期增量观察」的起点

**不要复制旧内容作为新报告。** 引用时用路径 + 一句话概括。

### Step 2: Event discovery — broad search

针对窗口 `[start, end]`，按类别执行搜索。动态填充年份 / 月份：

```
# 1. Flagship events（仅窗口覆盖到的年份）
"Jensen Huang GTC keynote <year_in_window>"
"Jensen Huang CES <year_in_window>"
"Jensen Huang Computex <year_in_window>"
"Jensen Huang SIGGRAPH <year_in_window>"

# 2. Guest podcast appearances（对窗口内做 site-restricted 搜索）
site:youtube.com "Jensen Huang" interview
site:open.spotify.com "Jensen Huang"
"Jensen Huang" podcast guest
site:acquired.fm Jensen Huang
site:stratechery.com Jensen Huang
"Jensen Huang" on "BG2" OR "All-In" OR "No Priors" OR "Lex Fridman"

# 3. Dedicated media interviews
site:cnbc.com "Jensen Huang" interview
site:bloomberg.com "Jensen Huang"
site:ft.com "Jensen Huang"
site:wsj.com "Jensen Huang"
"Jensen Huang" 采访 OR 专访

# 4. Earnings + investor events
"Nvidia earnings call" <quarter_in_window>
"Nvidia investor day" OR "GTC Financial Analyst"

# 5. Fireside / summits / campus / 其他
"Jensen Huang" fireside OR summit OR "Q&A"
"Jensen Huang" commencement OR "university talk"
"Jensen Huang" congressional OR testimony  # 仅窗口可能覆盖时
```

**所有查询结果必须自己核对日期**，确保落在当前窗口 `[start, end]` 内。搜索引擎经常带出窗口外的旧内容。

### Step 3: Deep-dive on selected events

- **short mode**：几乎所有入选事件都用 WebFetch 取详情（transcript / 官方稿 / 报道）
- **long mode**：按重要度挑 5-8 个做 WebFetch，其余只保留时间线条目

每个 deep-dive 事件必须提取：日期 / 场合 / 核心主题 / 重要引述（英文原文 + 中文翻译）/ 产品或技术细节 / 现场反响与后续影响。

### Step 4: Diff vs prior report

对比 Step 1 读到的 prior report（若存在）：
- 标出**新出现**的事件 / 引述 / 概念
- 标出**延续强化**的论述
- 标出**被淡化 / 不再提**的论述

short mode 必须显式落到「相对上期的增量观察」章节；long mode 落到「观点演进追踪」章节。

### Step 5: Compile & persist

**文件命名**（必须编码窗口类型 + 起止日期，避免覆盖）：

| 窗口 token | 文件名 |
|-----------|-------|
| `past-2w` / `past-1m` / `past-3m` | `rolling-<token>-<end_date>.md`（如 `rolling-past-2w-2026-04-19.md`） |
| `past-6m` / `past-1y` | `retrospective-<token>-<end_date>.md` |
| `past-3y` / `bootstrap` | `bootstrap-<start>-to-<end>.md`（如 `bootstrap-2023-01-to-2026-04.md`） |
| `custom` | `custom-<start>-to-<end>.md` |

```bash
REPORT_FILE="$REPORT_DIR/<naming_from_table>.md"
```

最终 Markdown 写入 `$REPORT_FILE`，然后按 **Final answer format**（见下方 "Output rules" 之后的章节）输出最终回复。这是必须的 —— Discord 用户只能看到你的 final assistant message。

## Report structure

### 报告头部（所有模式必填）

```
- 报告类型: rolling / retrospective / bootstrap / custom
- 时间窗口: <start_date> → <end_date>（token: <past-1m | past-3y | ...>）
- 生成时间: <YYYY-MM-DD HH:MM>
- 引用的 prior report: <path or "无">
```

### Short mode（窗口 ≤ 90 天）

1. **窗口概览**：3-5 句话概括本窗口 Jensen 的公开出席频次、主线议题、最显眼的一条发言。
2. **本窗口重点事件**：按时间顺序列出所有入选事件。每条 → 日期 / 场合 / 核心主题 / 重要度 ★-★★★ / 2-3 条关键引述 / 产品或技术细节。
3. **核心引述**：精选 5-10 条，附中文翻译 + 出处链接。
4. **相对上期的增量观察**（依赖 Step 4）：新概念 / 被强化的主题 / 被淡化或消失的论述。
5. **即将到来的日程**：窗口结束后 30-60 天内已 announced 的 Jensen 公开日程。

### Long mode（窗口 > 90 天）

1. **执行摘要**：3-5 句话概括本窗口核心主线与里程碑。
2. **演讲时间线**：按时间顺序列出所有入选发言。每条 → 日期 / 场合 / 核心主题 / 重要度 ★-★★★。
3. **主题深度分析**
   - 3.1 AI 发展愿景（AGI 路径、AI factory、Agentic / Physical AI）
   - 3.2 Nvidia 战略与定位（Accelerated computing、full-stack、sovereign AI）
   - 3.3 产品与技术路线（Hopper → Blackwell → Rubin、NVLink/NVSwitch、CUDA、DGX/HGX/MGX、Omniverse）
   - 3.4 行业洞察（数据中心 TAM、云厂商关系、汽车/医疗/机器人垂直）
4. **经典引述集**：10-15 条原文引述 + 中文翻译 + 出处。
5. **观点演进追踪**：关键论述在窗口内的演变（哪些被验证、哪些被强化、哪些概念首次出现）。
6. **关键发现与总结**：5-8 条核心发现 + 对 Nvidia 未来方向的推断。

## Output rules

- 默认使用中文撰写报告
- 英文引述保留原文，附中文翻译
- 技术术语保留英文（CUDA, NVLink, Blackwell, Rubin, NVL72 等）
- 所有事实性陈述必须标注信息来源（URL 或 publication）
- 不编造未经搜索验证的演讲内容；若某类事件在窗口内未搜到，显式写「窗口内未发现 X 类事件」
- 时间线 / 事件列表严格按时间顺序
- 报告长度目标：
  - short mode: 2000-4000 字
  - long mode (past-6m / past-1y): 5000-8000 字
  - bootstrap / past-3y: 6000-10000 字

## Final answer format

报告本身（最长 10000 字）直接 paste 到 Discord 体验差 —— 那是 5+ 条消息的"文件 viewer"流。所以这个 skill 走 **结构化摘要 + 文件路径** 模式（不是全文 paste）。但"摘要"绝不等于状态注释 —— 摘要本身必须是用户来这次想看的实质内容。

**回复中必须包含：**

1. **窗口结论（3–5 句）**：本窗口 Jensen 的主线议题、最显眼的 1 个观察点、相对上期的核心变化（rolling/retrospective）或核心论述总览（bootstrap）。
2. **核心引述精选（3–5 条）**：原文 + 中文翻译 + 出处。从报告 §核心引述 / §经典引述集 里挑最强的几条直接 paste。
3. **关键事件时间线（5–10 条压缩版）**：日期 + 场合 + 一句话主题 + 重要度。完整版在文件里。
4. **观点演进 / 增量观察（2–4 条）**：哪些被验证、哪些被强化、哪些首次出现。
5. **存储路径**：`$REPORT_FILE` 的绝对路径。

❌ 不要用 "Done."、"Report saved."、"报告已写入文件"收尾 —— 那是状态注释，不是 answer。
❌ 不要只回 storage path —— 用户在 Discord 打不开文件。
❌ 不要把窗口结论写成 1 句"Jensen 这个窗口讲了 AI"这种空话 —— 摘要要给用户判断"是否要打开 file 读全文"的信息密度。
✅ 摘要本身要让一个不打开文件的用户也能拿到核心结论；文件用于 deep dive。
