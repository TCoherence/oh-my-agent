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

| 维度                | CLI Agent                                  | API Agent                    |
| ------------------- | ------------------------------------------ | ---------------------------- |
| Context Engineering | CLI 自己管理（AGENT.md, skills, tool use） | 需要自己搭建全部             |
| Tool Use            | 内置（Bash, Read, Edit, Grep...）          | 需要自己定义 function schema |
| Skill 系统          | 原生支持（SKILL.md auto-discovery）        | 无法使用                     |
| Memory 集成         | 可以通过 prompt 注入 history               | 需要自己管 messages array    |
| 迭代成本            | 零 — CLI 升级即可                          | 需要跟进 API 变更 + 自研     |

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

| 原有条目                                                                  | 建议                                                       |
| ------------------------------------------------------------------------- | ---------------------------------------------------------- |
| Streaming responses（需要 `--output-format stream-json` + streaming SDK） | **简化** — 只需关注 CLI 的 stream-json，去掉 SDK streaming |
| Codex CLI agent                                                           | **保留** — 自然是 CLI 路线的延伸                           |
| Slash commands (`/agent claude`)                                          | **保留** — agent 切换在纯 CLI 架构下更有意义               |
| Cross-session memory                                                      | **升级优先级** — 这是 oh-my-agent 自我迭代的基础设施       |
| SQLite → PostgreSQL                                                       | **降低优先级** — 单机 CLI agent 暂时不需要                 |

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

| 对比         | Claude CLI                       | Gemini CLI             | Codex CLI                   |
| ------------ | -------------------------------- | ---------------------- | --------------------------- |
| 非交互模式   | `claude -p "<prompt>"`           | `gemini -p "<prompt>"` | `codex exec "<prompt>"`     |
| 自动批准     | `--dangerously-skip-permissions` | `--yolo`               | `--full-auto`               |
| 内置 Sandbox | 仅交互模式 (`/sandbox`)          | `--sandbox`            | `--sandbox workspace-write` |
| 静默模式     | 默认                             | 默认                   | `-q`                        |

`--full-auto` = `--ask-for-approval on-request` + `--sandbox workspace-write`，是 oh-my-agent headless 场景的理想组合。

### Sandbox / 隔离环境

三个 CLI 都支持某种形式的 sandbox：

| 特性          | Claude CLI                                  | Gemini CLI                        | Codex CLI                     |
| ------------- | ------------------------------------------- | --------------------------------- | ----------------------------- |
| 机制          | Apple Seatbelt (macOS) / bubblewrap (Linux) | Seatbelt (macOS) / Docker (Linux) | OS-level                      |
| 文件限制      | cwd 内读写                                  | project dir 内写入                | cwd 内写入                    |
| 网络隔离      | Proxy + 白名单域名                          | 可配置                            | 默认禁止                      |
| Headless 可用 | ❌ 仅交互模式，CLI flag 待开发               | ✅ `--sandbox`                     | ✅ `--sandbox workspace-write` |
| Docker 选项   | Docker Sandbox (microVM)                    | Container-based                   | 无                            |

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

---

## 🔒 Sandbox 隔离策略讨论（2025-02-26 补充）

### 核心担忧

两个关键风险：

1. **CLI agent 在 dev repo 内操作** — 当前 `BaseCLIAgent.run()` 没有设置 `cwd`，subprocess 继承 oh-my-agent 进程的工作目录（即开发 repo）。agent 能看到源码、`config.yaml`（含 token）、`AGENT.md`，甚至可能直接修改这些文件。
2. **Skill 脚本逃逸** — 即使 CLI sandbox 限制了文件写入范围，skill 脚本可能通过网络、环境变量、子进程等方式突破限制。

### 当前代码的三个缺口

#### 缺口 1: 没有 workspace 隔离

```python
# BaseCLIAgent.run() — 当前实现
proc = await asyncio.create_subprocess_exec(
    *cmd,
    # ❌ 没有 cwd= 参数，继承父进程工作目录
    stdout=asyncio.subprocess.PIPE,
    stderr=asyncio.subprocess.PIPE,
    env=self._build_env(),
)
```

CLI agent 的 sandbox 都是基于 cwd 的：
- Codex `--sandbox workspace-write` → 限制写入 cwd 内
- Claude Seatbelt → 限制读写 cwd 内
- Gemini `--sandbox` → 限制写入 project dir 内

如果 cwd = dev repo，sandbox 反而「保护」了 agent 对 dev repo 的访问权。

#### 缺口 2: 环境变量直通

```python
# BaseCLIAgent._build_env() — 当前实现
def _build_env(self) -> dict[str, str]:
    env = os.environ.copy()  # ❌ 全量复制，包含所有 token 和 secret
    env.pop("CLAUDECODE", None)
    return env
```

所有环境变量（包括 `DISCORD_BOT_TOKEN`、`ANTHROPIC_API_KEY` 等）都传给了子进程。Skill 脚本可以通过 `echo $DISCORD_BOT_TOKEN` 直接获取。

#### 缺口 3: Skill symlink 指向 dev repo

```python
# SkillSync.sync() — 当前实现
link.symlink_to(source)  # source = dev_repo/skills/xxx
```

Skill 以 symlink 形式指向 dev repo 的 `skills/` 目录。如果 agent 的 cwd 是 workspace，但 skill 是到 dev repo 的 symlink，agent 仍然可以通过 symlink 间接访问 dev repo。

### Skill 逃逸风险矩阵

| 逃逸方式          | 例子                       |       Codex       |         Claude         |      Gemini       |
| ----------------- | -------------------------- | :---------------: | :--------------------: | :---------------: |
| 网络请求          | `curl` exfiltrate 数据     |    ✅ 默认禁网     |        ❌ 无限制        |     ❌ 无限制      |
| 读 sandbox 外文件 | `cat ~/.ssh/id_rsa`        |  ✅ sandbox 限制   |  ⚠️ Seatbelt 部分限制   | ❌ `--yolo` 无限制 |
| 环境变量泄露      | `echo $DISCORD_BOT_TOKEN`  |         ❌         |           ❌            |         ❌         |
| 子进程逃逸        | skill 里 `exec` 另一个进程 |  ✅ sandbox 继承   | ⚠️ 取决于 Seatbelt 粒度 |         ❌         |
| 篡改 AGENT.md     | 改指令让 agent 做别的事    | ✅ workspace-write |  ⚠️ 如果在 cwd 内可写   |         ❌         |

> ✅ = CLI sandbox 能防住，⚠️ = 部分防护，❌ = 无法防御

**结论**：环境变量泄露是所有 CLI agent 的共同弱点，仅靠 CLI sandbox 无法解决。

### 分层防御方案

```
Layer 4: Docker 容器隔离（进程级隔离，长期目标）
Layer 3: Skill 权限声明（permissions manifest，v0.5+）
Layer 2: CLI-native sandbox（--full-auto, --sandbox, --allowedTools）← 已有
Layer 1: 环境变量净化（白名单化 _build_env）
Layer 0: Workspace 目录隔离（cwd 设为专属目录）
```

#### Layer 0: Workspace 隔离（v0.4.x）

Config 中添加 `workspace` 字段，agent spawn 时设 `cwd=workspace_path`：

```yaml
# config.yaml
workspace: ~/oh-my-agent-workspace   # 所有 agent 的专属工作目录

agents:
  claude:
    type: cli
    # workspace 也可以 per-agent 覆盖
```

```python
# BaseCLIAgent.run() 改动
proc = await asyncio.create_subprocess_exec(
    *cmd,
    cwd=self._workspace,   # ← 新增：指向专属 workspace
    stdout=asyncio.subprocess.PIPE,
    stderr=asyncio.subprocess.PIPE,
    env=self._build_env(),
)
```

Workspace 目录在启动时自动创建，包含：
- Agent 需要的 `AGENT.md`（拷贝，不是 symlink）
- Skill 文件（拷贝到 workspace 的 `.skills/` 子目录）
- Agent 产生的文件（代码、输出等）

#### Layer 1: 环境变量净化（v0.4.x）

`_build_env()` 改为白名单模式：

```python
def _build_env(self) -> dict[str, str]:
    SAFE_KEYS = {"PATH", "HOME", "USER", "LANG", "LC_ALL", "TERM", "SHELL",
                 "TMPDIR", "XDG_CONFIG_HOME", "XDG_DATA_HOME"}
    env = {k: v for k, v in os.environ.items() if k in SAFE_KEYS}
    # CLI agent 可能需要自己的 API key（例如 Codex 需要 OPENAI_API_KEY）
    # 这些通过 config 显式声明，而不是继承全局环境
    for key in self._passthrough_env_keys:
        if key in os.environ:
            env[key] = os.environ[key]
    return env
```

```yaml
# config.yaml 显式声明需要传递的环境变量
agents:
  codex:
    type: cli
    env_passthrough: [OPENAI_API_KEY]   # 只有这些 env vars 会传给子进程
  claude:
    type: cli
    env_passthrough: [ANTHROPIC_API_KEY]
```

#### Layer 2: CLI-native sandbox（已有，微调）

保持现有策略，不变：
- Codex: `--full-auto`（sandbox + 禁网）
- Gemini: 加 `--sandbox` flag
- Claude: `--allowedTools` 守护，等待 `--sandbox` CLI flag

#### Layer 3: Skill 权限声明（v0.5+）

在 `SKILL.md` 的 YAML frontmatter 中声明权限：

```yaml
---
name: weather
description: Get weather information
permissions:
  network: true          # 需要访问网络
  filesystem: read-only  # 只读文件系统
  env_vars: []           # 不需要任何环境变量
---
```

oh-my-agent 在 invoke 前检查权限声明，不匹配时拒绝执行。这是一个 **声明式** 的 capability，不是强制执行——真正的强制执行依赖 Layer 0-2 和 Layer 4。

#### Layer 4: Docker 隔离（长期，backlog）

所有 CLI agent + workspace 运行在 Docker 容器内：
- 进程级隔离，不依赖 CLI 自身的 sandbox 实现
- 网络、文件系统、环境变量全部由容器控制
- 是唯一真正意义上的 defense-in-depth

### 推荐实施节奏

| 阶段                 | 内容                                 | 代码量  | 风险解决                  |
| -------------------- | ------------------------------------ | ------- | ------------------------- |
| **v0.4.x（立即做）** | Layer 0 workspace + Layer 1 env 净化 | ~50 行  | 解决 80% 的担忧           |
| **v0.5+**            | Layer 3 skill permissions            | ~100 行 | 声明式 capability control |
| **Backlog**          | Layer 4 Docker                       | 中等    | 完整的进程隔离            |

v0.4.x 的三个改动互相独立，可以分别 PR：
1. `workspace` config + `BaseCLIAgent` cwd — 不影响现有行为（默认 workspace = cwd）
2. `_build_env()` 白名单 — 可能需要测试确保 CLI agent 正常运行
3. SkillSync 拷贝到 workspace — 需要区分 dev 模式（symlink）和 production 模式（copy）

