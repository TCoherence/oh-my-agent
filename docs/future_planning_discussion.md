# Oh My Agent — 未来发展讨论

> 基于你提出的两个核心观点和项目现有 todo/roadmap 的综合分析

---

## 🎯 你的两个论点

### 1. CLI Agent 优先 → 去掉 API Agent

**完全同意。** 这是一个很好的架构简化方向。

当前项目同时维护了两条路径：

```
BaseAgent
  ├── BaseCLIAgent  →  claude, gemini (有完整 agentic loop)
  └── BaseAPIAgent  →  anthropic, openai (只是 SDK call, 无 tool use)
```

两者的根本不兼容在于：

| 维度 | CLI Agent | API Agent |
|------|-----------|-----------|
| Context Engineering | CLI 自己管理（AGENT.md, skills, tool use） | 需要自己搭建全部 |
| Tool Use | 内置（Bash, Read, Edit, Grep...） | 需要自己定义 function schema |
| Skill 系统 | 原生支持（SKILL.md auto-discovery） | 无法使用 |
| Memory 集成 | 可以通过 prompt 注入 history | 需要自己管 messages array |
| 迭代成本 | 零 — CLI 升级即可 | 需要跟进 API 变更 + 自研 |

**建议行动：**
- 标记 `agents/api/` 为 **deprecated**，暂时保留代码但不再投入维护
- 从 `config.yaml.example` 和 `README.md` 中降低 API agent 的存在感
- `todo.md` 中移除与 API agent 相关的 streaming SDK 等条目
- 未来如果需要 "轻量级" 回答（比如简单问答不需要 agentic loop），可以考虑一个 `SimpleChatAgent`，但那是 **后话**

---

### 2. Skill 同步 — 双向 sync 的挑战

当前流程是**单向的**：

```
skills/ (canonical source)
  └─ SkillSync.sync() ──→ .gemini/skills/ (symlink)
                        ──→ .claude/skills/  (symlink)
```

如果我们希望 oh-my-agent **自己迭代 skill**（比如通过 CLI agent 创建新 skill），那流程会变成：

```
CLI Agent 创建 skill
  └─ 写入 .gemini/skills/new_skill/ (CLI 的原生位置)
     └─ ❌ 不会自动出现在 skills/ (canonical source)
        └─ ❌ 也不会 sync 给其他 CLI agent
```

**需要补全的能力是「反向同步」：**

```
方案 A: Watch + Reverse Copy
  └─ 用 watchdog 或轮询 .gemini/skills/ 和 .claude/skills/
  └─ 检测到新的非 symlink 目录 → 复制回 skills/
  └─ 然后触发 SkillSync.sync() 同步给所有 CLI

方案 B: Agent 指令约束
  └─ 在 AGENT.md 中指示 agent：创建 skill 时直接写到 skills/ 目录
  └─ 然后手动或自动触发 sync()
  └─ 更简单，但依赖 agent 遵守指令

方案 C: Webhook / Post-run Hook
  └─ 每次 CLI agent run 完成后，检查 .gemini/skills/ 有无新目录
  └─ 如果有，自动 reverse sync
  └─ 最实际的方案，可以集成在 GatewayManager.handle_message() 里
```

**推荐方案 B + C 结合**：在 `AGENT.md` 中指示 agent 写到 `skills/`，同时在每次 agent response 后做一次 diff 检查作为 safety net。

---

## 💡 基于现有 Roadmap 的想法

### 优先级重排（考虑去掉 API agent 后）

从 [todo.md](file:///Users/yanghanzhi/repos/oh-my-agent/docs/todo.md) 来看，去掉 API agent 后，一些条目可以简化或移除：

| 原有条目 | 建议 |
|----------|------|
| Streaming responses（需要 `--output-format stream-json` + streaming SDK） | **简化** — 只需关注 CLI 的 stream-json，去掉 SDK streaming |
| Codex CLI agent | **保留** — 自然是 CLI 路线的延伸 |
| Slash commands (`/agent claude`) | **保留** — agent 切换在纯 CLI 架构下更有意义 |
| Cross-session memory | **升级优先级** — 这是 oh-my-agent 自我迭代的基础设施 |
| SQLite → PostgreSQL | **降低优先级** — 单机 CLI agent 暂时不需要 |

### 新增建议条目

#### 1. **SkillSync 双向同步** (v0.4.0)
上面已经分析了。实现 reverse sync 是 self-evolving agent 的前置条件。

#### 2. **Agent 自我迭代框架** (v0.5.0)
让 oh-my-agent 能够：
- 接收用户指令 → 创建/修改 skill → 自动 sync
- Skill 版本管理（简单的 git commit 或 changelog）
- Skill 测试机制（创建 skill 后自动验证）

#### 3. **CLI Agent Session 管理优化**
当前 CLI agent 是 stateless 的（每次 subprocess），history 通过 prompt flattening 传入。
考虑：
- `claude --resume <session_id>` 的可行性 — 避免每次都把完整 history 塞进 prompt
- 对于长对话，prompt flattening 会导致 token 膨胀
- 这与 `HistoryCompressor` 形成互补：compressor 压缩旧 history，session resume 避免重发 history

#### 4. **多 CLI Agent 协作**
既然是纯 CLI 架构，可以考虑更有意思的模式：
- **专家路由**：不再是简单的 fallback，而是根据任务类型选择 agent（代码 → Claude，搜索 → Gemini）
- **Review 模式**：一个 agent 写代码，另一个 review
- 这需要更丰富的 `AgentRegistry` 逻辑

#### 5. **Memory 抽离准备**
你提到 memory 和 skill 应该独立于 repo。虽然现在不急，但可以为此做准备：
- `MemoryStore` 的 ABC 已经设计得不错，换 backend 很容易
- 可以加一个 `memory.export()` / `memory.import()` 接口，方便未来迁移
- Skill 目录如果用 git submodule 或独立 repo，SkillSync 需要适配

---

## 🔧 CLI Agent 能力讨论（2025-02-26 补充）

### CLI Agent 能不能改文件？

**可以，而且当前架构已经支持。**

- **Claude CLI**: 内置 `Edit` 工具（改现有文件）和 `Write` 工具（创建新文件）。当前 config 的 `allowed_tools: [Bash, Read, Edit, Glob, Grep]` 已经包含了 `Edit`。如果要创建新文件，加上 `Write` 即可。另外 `Bash` 工具本身也能通过 shell 命令操作文件。
- **Gemini CLI**: `--yolo` 模式下没有工具限制，通过 shell 命令可以做任何文件操作。
- **Codex CLI**: 在 `--sandbox workspace-write` 模式下可以读写 cwd 内的所有文件。

**结论**：文件编辑不是能力问题，而是 **范围控制** 问题 — sandbox 和 `allowedTools` 控制的是 agent 能碰哪些文件、能做哪些操作，而非能不能改文件。

### Codex CLI 集成

Codex CLI 是 OpenAI 的本地 coding agent，和 Claude CLI、Gemini CLI 定位一致。关键区别：

| 对比 | Claude CLI | Gemini CLI | Codex CLI |
|------|-----------|-----------|-----------|
| 非交互模式 | `claude -p "<prompt>"` | `gemini -p "<prompt>"` | `codex exec "<prompt>"` |
| 自动批准 | `--dangerously-skip-permissions` | `--yolo` | `--full-auto` |
| 内置 Sandbox | 仅交互模式 (`/sandbox`) | `--sandbox` | `--sandbox workspace-write` |
| 静默模式 | 默认 | 默认 | `-q` |

`--full-auto` = `--ask-for-approval on-request` + `--sandbox workspace-write`，是 oh-my-agent headless 场景的理想组合。

### Sandbox / 隔离环境

三个 CLI 都支持某种形式的 sandbox：

| 特性 | Claude CLI | Gemini CLI | Codex CLI |
|------|-----------|-----------|-----------|
| 机制 | Apple Seatbelt (macOS) / bubblewrap (Linux) | Seatbelt (macOS) / Docker (Linux) | OS-level |
| 文件限制 | cwd 内读写 | project dir 内写入 | cwd 内写入 |
| 网络隔离 | Proxy + 白名单域名 | 可配置 | 默认禁止 |
| Headless 可用 | ❌ 仅交互模式，CLI flag 待开发 | ✅ `--sandbox` | ✅ `--sandbox workspace-write` |
| Docker 选项 | Docker Sandbox (microVM) | Container-based | 无 |

**推荐策略**：
1. Codex → `--full-auto`（自带 sandbox）
2. Gemini → 加 `--sandbox` flag
3. Claude → 当前用 `--allowedTools` 守护，等待 `--sandbox` CLI flag
4. 长期 → 所有 CLI agent 跑在 Docker 容器内，defense-in-depth

---

## 📋 建议的版本规划

> 完整的依赖关系图（Mermaid DAG）见 [todo.md](todo.md)。

```
v0.4.0 — CLI-First Cleanup + Skill Sync
  ├─ Deprecate API agent layer        (独立，无依赖)
  ├─ Add Write to Claude tools        (独立，config 改动)
  ├─ Add Codex CLI agent              (独立，无依赖)
  ├─ Enable CLI sandbox modes         (⬅ Codex CLI agent)
  ├─ SkillSync reverse sync (B+C)     (⬅ ✅ Skill System v0.3)
  ├─ Streaming responses (CLI only)   (独立，无依赖)
  ├─ Slash commands                   (独立，但 /search 需要 v0.5 memory)
  └─ Update README                    (⬅ Deprecate API + Add Codex)

v0.5.0 — Self-Evolution
  ├─ Agent-driven skill creation      (⬅ Reverse sync + Write tool)
  ├─ Skill testing / validation       (⬅ Skill creation)
  ├─ CLI session resume               (⬅ ✅ History Compression v0.3)
  ├─ Cross-session memory search      (⬅ ✅ Memory v0.3 + Slash commands)
  └─ Memory export/import API         (⬅ ✅ Memory v0.3)

v0.6.0 — Multi-Agent Intelligence
  ├─ Smart agent routing              (⬅ ✅ Agent Registry + Codex CLI)
  ├─ Agent collaboration              (⬅ Smart routing)
  ├─ Agent selection via @mention     (⬅ Smart routing + Slash /agent)
  └─ Platform adapters                (独立，无 agent 依赖)
```

### 关键发现

**三条关键路径**：

1. **Self-Evolution 路径** — Skill System → Reverse Sync → Skill Creation → Skill Testing。这是最长的链，v0.4 的 reverse sync 和 Write tool 是 v0.5 self-evolution 的硬性前置。
2. **Multi-Agent 路径** — Codex CLI → Smart Routing → Collaboration / @mention。v0.4 加 Codex 是 v0.6 multi-agent 的前置（至少 3 个 agent 才有 routing 的意义）。
3. **Memory 路径** — Memory (✅) → Cross-Session Search ← Slash Commands。这条路径比较短，Slash commands 和 memory 都已经有基础，主要是 wiring。

**可以立即并行做的**（无任何依赖，v0.4 的第一批工作）：
1. Deprecate API agents
2. Add Codex CLI agent
3. Add `Write` to Claude tools（一行 config）
4. Streaming responses
5. Slash commands
6. CLI session resume
7. Memory export/import

---

## 🤔 一个值得辩论的问题

> **API agent 是否应该完全移除，还是保留为 "lightweight fallback"？**

有一个实际场景：当所有 CLI agent 都挂了（比如 API quota 用完导致 CLI 也失败），一个不需要 tool use 的 API agent 可以作为最后的兜底，至少回复用户"我现在无法处理复杂请求"。

但这可能 over-engineering 了 — 一个简单的硬编码 fallback message 就够了，不需要走 API agent。

**结论：去掉 API agent 是正确的方向。** 保持架构简洁比保留一个几乎不会用到的 fallback 更重要。

