# 运维指南

本文档涵盖 oh-my-agent 的安装、日常运维、问题诊断和升级流程。

---

## 1. 安装

### 1.1 本地安装（venv）

```bash
# 克隆仓库
git clone https://github.com/TCoherence/oh-my-agent.git
cd oh-my-agent

# 创建虚拟环境
python3 -m venv .venv
source .venv/bin/activate

# 以可编辑模式安装
pip install -e .

# 准备配置
cp config.yaml.example config.yaml
# 编辑 config.yaml — 填入 DISCORD_BOT_TOKEN、DISCORD_CHANNEL_ID 等

# （可选）创建 .env 存放密钥
echo 'DISCORD_BOT_TOKEN=你的token' >> .env
echo 'DISCORD_CHANNEL_ID=你的频道ID' >> .env

# 启动
oh-my-agent
```

**前置条件**：Python ≥ 3.11，且 config 中引用的 CLI agent（`claude`、`gemini`、`codex`）需要已安装且在 `PATH` 中。

### 1.2 Docker / Compose

```bash
# 构建并启动
docker compose up -d

# 查看日志
docker compose logs -f

# 停止
docker compose down
```

仓库根目录的 `compose.yaml`：
- 基于 `Dockerfile` 构建镜像
- 将仓库挂载到 `/repo`（容器启动时做 editable install）
- 通过命名卷 `oma-runtime` 持久化运行状态
- 从宿主机 `.env` 转发环境变量

**首次运行检查清单**：
1. `cp config.yaml.example config.yaml`，填入 token
2. 创建 `.env`，写入 `DISCORD_BOT_TOKEN`、`DISCORD_CHANNEL_ID` 和所需 API key
3. `docker compose up -d`

#### 使用 shell 脚本（替代方案）

`scripts/` 目录提供了更细粒度的 Docker 控制：

| 脚本 | 用途 |
|------|------|
| `docker-build.sh` | 构建镜像 |
| `docker-start.sh` | 后台启动，自动重启 |
| `docker-run.sh` | 一次性运行（调试用） |
| `docker-logs.sh` | 跟踪容器日志 |
| `docker-stop.sh` | 停止并移除容器 |
| `docker-status.sh` | 查看容器状态 |

### 1.3 配置校验

启动前可以先校验配置：

```bash
oh-my-agent --validate-config
# 或指定配置文件路径：
oh-my-agent --config /path/to/config.yaml --validate-config
```

退出码 0 = 有效，退出码 1 = 存在错误。警告会打印但不阻止启动。

---

## 2. 重启流程

### 本地

```bash
# Ctrl-C 停止，然后：
oh-my-agent
```

### Docker Compose

```bash
docker compose restart
# 或完整的 down/up：
docker compose down && docker compose up -d
```

收到 SIGTERM 时 bot 会优雅关闭正在运行的任务。中断的任务转为 FAILED，可以重试。

---

## 3. 问题诊断

### 3.1 服务日志

位置：`~/.oh-my-agent/runtime/logs/service.log`（Docker 中在 `oma-runtime` 卷内）。

日志格式（结构化 key=value）：
```
2026-04-10T10:20:11.123Z level=INFO logger=oh_my_agent.gateway.manager msg=agent running
```

按天轮转，保留 `service_retention_days` 天（默认 7 天）。

### 3.2 Discord 命令

| 命令 | 用途 |
|------|------|
| `/task_status <id>` | 查看任务状态、步骤信息、耗时 |
| `/task_list` | 列出所有任务及状态 |
| `/task_logs <id>` | 查看任务最近日志事件 |
| `/task_changes <id>` | 查看任务修改的文件 |
| `/memories` | 列出自适应记忆（可选分类过滤） |
| `/search <query>` | 全文搜索对话历史 |

### 3.3 任务日志

每个运行时任务的事件记录在 `runtime_events` SQLite 表中。使用 `/task_logs <task_id>` 查看最新事件，或直接查询数据库：

```bash
sqlite3 ~/.oh-my-agent/runtime/runtime.db \
  "SELECT timestamp, event_type, payload FROM runtime_events WHERE task_id='<id>' ORDER BY timestamp DESC LIMIT 20;"
```

---

## 4. 自动化

### 4.1 错过任务策略

调度器对错过的任务采用 **跳过（skip）** 策略。如果 bot 离线期间有任务本应运行，不会补跑，而是在下一个调度周期正常执行。

### 4.2 自动化状态

自动化运行历史记录在 `runtime.db` 的 `automation_state` 表中。可用 `/task_list` 或直接查询数据库来检查上次运行时间和错误。

---

## 5. 备份

### 5.1 需要备份的内容

| 路径 | 内容 | 优先级 |
|------|------|--------|
| `config.yaml` + `.env` | 配置和密钥 | 关键 |
| `~/.oh-my-agent/runtime/memory.db` | 对话历史 | 高 |
| `~/.oh-my-agent/runtime/runtime.db` | 任务状态、事件、决策 | 高 |
| `~/.oh-my-agent/runtime/skills.db` | Skill 遥测和反馈 | 中 |
| `~/.oh-my-agent/memory/` | 自适应记忆（daily + curated YAML） | 中 |
| `skills/` | 自定义 skill | 中 |
| `~/.oh-my-agent/runtime/logs/` | 服务日志 | 低（会轮转） |

### 5.2 备份步骤

```bash
# 先停止 bot 以确保 SQLite 一致性
# 然后复制运行时目录：
cp -r ~/.oh-my-agent/runtime/ ~/backup/oh-my-agent-runtime-$(date +%Y%m%d)/
cp config.yaml ~/backup/
cp -r skills/ ~/backup/skills/
```

Docker 环境下，运行时状态在 `oma-runtime` 卷中：
```bash
docker compose down
docker run --rm -v oma-runtime:/data -v $(pwd)/backup:/backup alpine \
  cp -r /data /backup/oma-runtime-$(date +%Y%m%d)
```

### 5.3 恢复

```bash
# 停止 bot
# 用备份替换运行时文件：
cp -r ~/backup/oh-my-agent-runtime-YYYYMMDD/* ~/.oh-my-agent/runtime/
# 重启
oh-my-agent
```

---

## 6. 升级流程

### 6.1 升级前检查清单

1. **备份** 所有运行时状态（参见 §5）
2. **检查当前 schema 版本**：
   ```bash
   sqlite3 ~/.oh-my-agent/runtime/runtime.db \
     "SELECT version FROM schema_version;"
   ```
3. **阅读** 目标版本的发布说明

### 6.2 执行升级

```bash
# 拉取最新代码
git pull origin main

# 重新安装
pip install -e .

# 校验配置（新版本可能新增必填字段）
oh-my-agent --validate-config

# 启动
oh-my-agent
```

Docker 环境：
```bash
docker compose down
git pull origin main
docker compose build
docker compose up -d
```

Schema 迁移在启动时自动运行。bot 启动时会记录当前 schema 版本：
```
level=INFO logger=oh_my_agent.memory.store msg=Schema version: 1
```

### 6.3 回滚

如果升级出现问题：

1. 停止 bot
2. 恢复备份（§5.3）
3. 切换到之前的版本：`git checkout v0.7.3`（或你之前使用的 tag）
4. 重新安装：`pip install -e .`
5. 重启

> **注意**：已执行前向迁移的数据库无法在旧版本代码中使用。回滚时必须从备份恢复。

### 6.4 版本兼容矩阵

| 从 | 到 | 迁移方式 | 说明 |
|----|----|----------|------|
| 0.7.x | 0.8.x | 自动 | Schema v1；config 新增 `logging` 区块（可选） |
| < 0.5.2 | ≥ 0.5.2 | 自动 | 旧 `.workspace/` 迁移到 `~/.oh-my-agent/` |
| 单体 DB | 分拆 DB | 自动 | `memory.db` 拆分为 `memory.db` + `runtime.db` + `skills.db` |

### 6.5 迁移内容说明

- **SQLite schema**：版本记录在 `schema_version` 表中；启动时执行前向迁移
- **配置格式**：新增可选区块由 `_apply_v052_defaults()` 补充；已有配置保持有效
- **运行时路径**：旧 `.workspace/` 自动迁移到 `~/.oh-my-agent/` 目录树
- **Memory 存储**：单体 `memory.db` 自动拆分为 conversation / runtime / skills 三个数据库
