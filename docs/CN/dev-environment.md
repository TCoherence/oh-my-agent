# 开发环境 / 用第二个 bot 做测试

本文介绍如何在 worktree 内运行一个隔离的 dev bot，避免影响生产 bot。

---

## 1. 为什么需要 dev bot？

生产 bot 通常跑在 Docker 里（host 上 bind 到 `~/oh-my-agent-docker-mount/`），里面是真实用户数据：`memory.db`、自动化任务、产物报告、登录凭证。把实验性改动 — 新 hook、agent 逻辑、skill — 直接打到生产，会有损坏数据的风险，回滚也很麻烦。

dev bot 通过三条隔离规则解决这个问题：

1. **独立的 Discord bot token。** 一个 token 只能维持一个 gateway WebSocket 连接；prod 和 dev 用同一个 token 会互相踢线（[discord.py:1026](../../src/oh_my_agent/gateway/discord.py:1026)）。
2. **独立的 runtime root。** dev 的运行时状态都在 `~/.oh-my-agent-dev/` 下，prod 在 `~/.oh-my-agent/`（在容器内对应 host 的 `~/oh-my-agent-docker-mount/.oh-my-agent/`）。两条路径完全不交叉。
3. **独立的 workspace。** dev 的 agent workspace 在 `~/.oh-my-agent-dev/agent-workspace/`，所以 skill 同步和 `_attachments/` 清理不会影响到 prod。

---

## 2. 配置（4 步）

### 2.1 创建第二个 Discord bot

这个 bot 必须是一个**完全独立的 Application**，不只是新发一个邀请链接。

**a) New Application + 拿 token**

打开 [Discord Developer Portal](https://discord.com/developers/applications)：**New Application** → 起名（比如 `oh-my-agent-dev`）→ Bot 用户会自动建好。在 **Bot** 页面：

- 点 **Reset Token** → token 只显示一次，**马上复制**（错过就再 Reset 一次）。这就是 `DISCORD_DEV_BOT_TOKEN`。
- 往下滑到 **Privileged Gateway Intents**，打开：
  - ✅ **MESSAGE CONTENT INTENT** —— 必须开。不开的话 bot 能上线、看着正常，但收到的消息内容是空的，永远不回复。代码里 [discord.py:1028](../../src/oh_my_agent/gateway/discord.py:1028) 设置了 `intents.message_content = True`，依赖这个开关。
  - `PRESENCE INTENT` 和 `SERVER MEMBERS INTENT` 不需要开。

**b) 生成带正确 scope + permission 的邀请链接**

**OAuth2 → OAuth2 URL Generator**：

- **Scopes**：`bot` + `applications.commands`（漏第二个会让 slash command 注册悄悄失败）。
- **Bot Permissions**（跟 prod 对齐）：
  - View Channels、Send Messages、Send Messages in Threads、Create Public Threads、Read Message History、Add Reactions、Attach Files、Embed Links、Use Slash Commands。

复制下方 **Generated URL**，浏览器打开 → 选一个**测试服务器**授权（或现有服务器里专门的测试 channel —— 但**不要**选 prod 也在的 channel，否则两个 bot 会同时回复同一条消息）。

**c) 拿 channel id**

Discord 客户端 → **设置 → Advanced** → 打开 **Developer Mode**。右键测试 channel → **Copy Channel ID**。这就是 `DISCORD_DEV_CHANNEL_ID`。

### 2.2 创建 dev config

在 worktree 根目录：

```bash
cp config.dev.yaml.example config.dev.yaml
```

`config.dev.yaml` 已加入 gitignore。模板里引用了两个环境变量：`DISCORD_DEV_BOT_TOKEN` 和 `DISCORD_DEV_CHANNEL_ID`。把它们写入 worktree 根目录的 `.env`（同样 gitignore）：

```bash
cat <<EOF >> .env
DISCORD_DEV_BOT_TOKEN=你的-dev-bot-token
DISCORD_DEV_CHANNEL_ID=你的-dev-channel-id
EOF
```

`.env` 必须放在 config 文件旁边 — `load_config()` 会先从 config 文件所在目录加载 `.env`，再 fallback 到 cwd（[config.py:33-35](../../src/oh_my_agent/config.py:33)）。

### 2.3 在 worktree 里建独立 venv

这一步**不能省**。`pip install -e .` 把 editable 目标记成执行命令时所在的目录 —— 主仓库的 `.venv` 里 `oh_my_agent` 解析到的是主仓库的 `src/`，**不是 worktree 的 `src/`**。如果用主 venv 跑 dev bot，加载的是 main 分支的代码，你在 worktree 里做的改动**完全不会生效**，dev bot 也就失去了意义。

在 worktree 根目录：

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
```

确认 editable 目标已经指向 worktree：

```bash
cat .venv/lib/python*/site-packages/__editable__.oh_my_agent-*.pth
# 期望：<worktree 路径>/src   （不是 /Users/.../oh-my-agent/src）
```

CLI agents（`claude`、`gemini`、`codex`）在 `PATH` 上，跟 prod 共用，不需要重装。

### 2.4 校验后启动

应用级 config 校验（不连 Discord）：

```bash
./.venv/bin/oh-my-agent --config config.dev.yaml --validate-config
```

通过后启动：

```bash
./.venv/bin/oh-my-agent --config config.dev.yaml
```

显式用 `./.venv/bin/...` 路径，确保命中**本 worktree** 的 venv（加载的是本 worktree 的 `src/`），不是全局安装或别的 worktree 的 `oh-my-agent`。启动 log 里应该看到所有 runtime 路径都指向 `~/.oh-my-agent-dev/...`。

---

## 3. 隔离不变量

| 维度 | Dev | Prod |
|---|---|---|
| Runtime root | `~/.oh-my-agent-dev/` | 容器内 `~/.oh-my-agent/` = host 上 `~/oh-my-agent-docker-mount/.oh-my-agent/` |
| Memory DB | `~/.oh-my-agent-dev/runtime/memory.db` | 容器内 `~/.oh-my-agent/runtime/memory.db` |
| Workspace | `~/.oh-my-agent-dev/agent-workspace/` | 容器内 `~/.oh-my-agent/agent-workspace/` |
| Discord token | dev bot token | prod bot token |
| Discord channel | 测试 channel | 生产 channel |
| Automations | 模板默认**关** | 通常打开 |

dev 的 `memory.db` 起手是空的。如果想把 prod 的 memory 复制到 dev：

```bash
mkdir -p ~/.oh-my-agent-dev
cp -r ~/oh-my-agent-docker-mount/.oh-my-agent/memory ~/.oh-my-agent-dev/memory
```

只在你确实需要 dev 反映 prod 累积的记忆状态时才这么做 — 多数测试场景下空白记忆更干净。

---

## 4. 注意事项

- **同一个 dev bot token 只能跑一个进程。** Discord gateway 限制每个 token 一个活跃连接。如果想在两个 worktree 并行跑 dev bot，需要再创建一个 Discord bot 并用独立 token。
- **dev 模板默认 `automations.enabled: false`。** 只在专门测试调度器时才改成 `true`，否则定时任务会触发并把测试 channel 刷屏。
- **Validator 不会检查环境变量是否被替换成功。** `--validate-config` 解析 YAML 并校验 schema，但**不会**警告 `DISCORD_DEV_BOT_TOKEN` 缺失；bot 启动时才会失败。所以一定要先把 `.env` 准备好。
- **不要把 `workspace:` 改回 prod 的路径。** 第 1 节第三条隔离规则依赖模板默认值 `workspace: ~/.oh-my-agent-dev/agent-workspace`。如果你改成 `~/.oh-my-agent/agent-workspace`（或 docker bind 路径），dev 的 skill 同步和 `_attachments/` 清理会跟 prod 抢同一份文件 —— 整个隔离就废了。
- **Runtime 任务会自动 commit 到当前 branch。** 模板默认 `runtime.merge_gate.auto_commit: true`（跟 prod 一致），自治任务会把改动 commit 到你 checkout 的 branch 上。**不要**在你即将开 PR 的 branch 上跑自治 runtime 任务，runtime 的 commit 会污染你的 PR 历史。
- **确认 prod 没受影响。** dev 启动后跑 `docker compose ps` 检查 prod 容器仍 healthy。隔离做对了 prod 不会受影响，但确认一下放心。

---

## 5. 验证步骤

1. 在 dev Discord channel 发 `hi` — dev bot 应该正常回复。（如果进程是 online 但完全不回复，多半是忘了开 MESSAGE CONTENT INTENT，回 2.1a 检查。）
2. 跑 `ls ~/.oh-my-agent-dev/` — 应该看到 `runtime/`、`agent-workspace/`、`memory/`、`reports/` 目录被自动创建。（`automations/` 默认关，不会出现，正常。）
3. 在 prod channel 发消息 — prod bot 应该照常工作。

---

## 6. 清理

完全删除 dev 环境：

```bash
rm -rf ~/.oh-my-agent-dev/
rm config.dev.yaml .env
```

Discord 上的 dev bot token 和 channel 可以保留以备下次使用，也可以在 Developer Portal 撤销。
