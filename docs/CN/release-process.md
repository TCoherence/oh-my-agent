# 发版流程

这是切 Oh My Agent 新版本时的 playbook。故意写得很短——本项目是单维护者规模，目标是一份能兜底 regression 的清单，而不是一套企业级 release gate。

## 版本号

遵循 [Semantic Versioning](https://semver.org/)：

- **MAJOR**——contract 级破坏性变更（config schema、automation YAML schema、删除 slash command、runtime 状态机改版）。
- **MINOR**——用户可见的新增能力（新技能、新 slash command、新子系统）。
- **PATCH**——bug fix、文档修正、内部重构不改 surface。

版本号单一来源：[`src/oh_my_agent/_version.py`](../../src/oh_my_agent/_version.py)。git tag 用 `vX.Y.Z`。

## 发版节奏

- 没有固定节奏——只要有用户可见的变更值得发，就发。
- 修了一个影响当前发布线的 bug → 切 patch。
- Unreleased 段堆了 3+ 项，或者一个大 feature 到位 → 切 minor。
- Major 是规划性工作，有专门的 issue 和 plan 文档（参考 [v1.0-plan.md](v1.0-plan.md) 的模样）。

## 发版前 checklist

按顺序跑。**一条都不要跳**——每条都有至少一次实打实救过。

### 1. 代码在 main，工作树干净

```bash
git status             # 干净，在 main
git pull               # 最新
git log --oneline -10  # 扫一眼最近 commit——有没有奇怪的？
```

### 2. 测试在干净 venv 里全绿

```bash
source .venv/bin/activate
pytest                 # 全量套件——全绿
pytest -q              # 扫 warning，有值得修的就先修
```

不能有本该跑的 skip。不能有新的 warning 把真信号淹没。

### 3. 真机 `/doctor` 全绿

在你的 staging Discord 启动 bot，跑 `/doctor`，每个段都看过一遍：

- **Scheduler health**——无 stale job；`reload_last_progress_at` 是最近几分钟的值。
- **Runtime health**——无卡住的 task；没有早于你预期的 HITL prompt。
- **Memory**——条目数合理；最近一小时没有 `parse_failure` 刷屏。
- **Auth**——每个 provider 都是 `ok`（或有意的 `cleared`）。

有任何段红，先修再打 tag。

### 4. 手动 automation 冒烟

用 `/automation_run` 跑一个 bundled automation，确认：

- 进 `DRAFT`（或 `auto_approve: true` 时直接 `RUNNING`）。
- 完成后 Discord 有 summary 卡片。
- `/automation_status` 显示 `last_run_at` / `next_run_at` 已刷新。

### 5. Restart 能恢复

```bash
# 把 bot kill 掉（SIGINT 或 Ctrl-C），等 ~5s，重启。
# 确认 Discord 里：
#   - 任何 WAITING_USER_INPUT / WAITING_MERGE 的 task 还在等。
#   - 任何 DRAFT 的 task 还在 DRAFT。
#   - 调度器接着跑没丢。
#   - `/doctor` Scheduler health 无 stale job。
```

### 6. Changelog 是最新的

`CHANGELOG.md` 有一个 `## Unreleased` 段，里面每条用户可见的变更都在。每条末尾应该有 PR 引用 `(#123)` 或短 SHA `(abc1234)`。

### 7. 文档和代码一致

顺手 grep 几个版本相关的落后字样：

```bash
grep -rn 'v0\.[0-9]\|^504 tests' README.md CLAUDE.md docs/EN docs/CN
```

提到老版本号的地方要么仍然准确（比如 `upgrade-guide.md` 里的迁移说明合法引用 `v0.8.x`），要么就得更新。

## 切版本

### 第 1 步——bump 版本号

编辑 [`src/oh_my_agent/_version.py`](../../src/oh_my_agent/_version.py)：

```python
__version__ = "X.Y.Z"
```

### 第 2 步——关掉 changelog 段

在 `CHANGELOG.md`：

- 把 `## Unreleased` 重命名为 `## vX.Y.Z - YYYY-MM-DD`。
- 在文件最顶上补一个空的 `## Unreleased` 块。
- 有破坏性变更时，写一个 **Breaking** 子段醒目标出。

### 第 3 步——commit + tag

```bash
git add src/oh_my_agent/_version.py CHANGELOG.md
git commit -m "chore(release): cut vX.Y.Z"
git tag -a vX.Y.Z -m "vX.Y.Z"
git push origin main vX.Y.Z
```

### 第 4 步——建 GitHub release

```bash
gh release create vX.Y.Z \
  --title "vX.Y.Z" \
  --notes-file <(awk '/## vX.Y.Z/,/## v/' CHANGELOG.md | head -n -1)
```

或者用 GitHub UI 手动粘 changelog 段。

## 发版后

- 在你用的渠道宣布一下。写事实就好：改了什么、破坏了什么（如果有）、怎么升级。
- 盯 `/doctor` 和 `service.log` 一两天。Patch 发版代价很低——发现 regression 当天就再切一个 `X.Y.Z+1`。
- 部署到用的实例上。如果有 schema 迁移，重启后确认 log 里有迁移记录。

## Hotfix 流程

已发布版本上需要紧急修（数据损坏、安全、bot 挂）时：

1. 从 release tag 拉分支：`git checkout -b hotfix/vX.Y.Z+1 vX.Y.Z`。
2. 带 regression 测试落修复。
3. bump `_version.py` 到 `X.Y.Z+1`，在 changelog 顶上加 `## vX.Y.Z+1 - DATE` 块。
4. tag、push、release——同上。
5. 把 hotfix 分支 merge 回 `main`（或者 main 已飘远就 cherry-pick）。

## 禁止操作

- **禁止**在脏工作树上发版。
- **禁止**跑失败的测试切发版"反正 CI 会接"——tag 上没有 CI gate。
- **禁止**跳过 `/doctor` 检查。它存在就是为了抓单元测试抓不到的东西。
- **禁止**给已发布的版本号重新 tag。发错了就再切一个 patch。
