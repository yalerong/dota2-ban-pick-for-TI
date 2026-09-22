# Dota 2 禁选侦察工具

[![Release](https://img.shields.io/github/v/release/yalerong/dota2-ban-pick-for-TI)](https://github.com/yalerong/dota2-ban-pick-for-TI/releases)
[![CI](https://github.com/yalerong/dota2-ban-pick-for-TI/actions/workflows/ci.yml/badge.svg)](https://github.com/yalerong/dota2-ban-pick-for-TI/actions/workflows/ci.yml)
[![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-blue.svg)](https://www.python.org/downloads/)

面向职业和半职业 Dota 2 战队、以证据为基础的队长模式侦察工具。本工具可构建 OpenDota 数据集，分析战队和选手特点，根据可追溯的比赛证据推荐选人和禁用英雄，并提供终端及浏览器两种 BP 面板。

**当前版本：v0.2.1——混合侦察 MVP。** 本版本继续以本地统计推荐器为权威数据源，同时新增可选的 AI 战术解读功能，用于分析本地推荐器生成的候选英雄。

## 功能特性

- 增量同步 OpenDota 数据，并支持可恢复的原始数据缓存。
- 规范化的 SQLite 数据、质量检查以及不可变的时间点快照。
- 分析选手英雄池、绝活英雄得分、针对性禁用压力、战队 BP 习惯、英雄配合与克制关系以及对手应对方式。
- 提供有比赛证据支持的选人/禁用候选英雄和 Markdown 对局报告。
- 提供交互式终端和浏览器 BP 面板。
- 提供可选的 OpenAI 兼容战术解读；输出经过校验且只能从本地候选集中选择，并支持自动回退。
- 提供基于双方十名英雄的阵容胜率模型和冻结快照评估命令。

> [!IMPORTANT]
> 推荐结果仅用于辅助决策，不是自动 BP 指令。在冻结盲测中，禁用推荐优于版本热门度基线，但选人推荐尚未达到这一水平。采用候选英雄前，请先检查引用的比赛、当前阵容名单和版本环境。

## 环境要求

- Python 3.11 或更高版本。
- 可访问互联网，以同步 OpenDota 和 dotaconstants 数据。
- 可选的 `OPENDOTA_API_KEY`，用于提高实际可用的请求额度。未配置密钥时，将受到 OpenDota 免费额度限制。

## 安装 v0.2.1

安装 GitHub Release 中附带的 wheel 包：

```bash
python -m pip install https://github.com/yalerong/dota2-ban-pick-for-TI/releases/download/v0.2.1/dota2_bp-0.2.1-py3-none-any.whl
bp --help
```

如需从源码运行：

```bash
git clone https://github.com/yalerong/dota2-ban-pick-for-TI.git
cd dota2-ban-pick-for-TI
python -m pip install -e .
```

API 密钥并非必需。如果有密钥，请在同步前于 Shell 中设置：

```bash
# macOS / Linux
export OPENDOTA_API_KEY="your-key"

# Windows PowerShell
$env:OPENDOTA_API_KEY="your-key"
```

## 快速开始

```bash
bp constants
bp update --since 2026-03-24 --limit 1500
bp teams --q spirit
bp report --us "Team Spirit" --them "Team Liquid"
bp h5 --matchup "Team Spirit|Team Liquid" --serve
```

首次同步可能需要较长时间，因为需要在 OpenDota 请求额度内下载比赛详情。后续运行均为增量同步，并支持中断后继续。

## 可选的 AI 战术分析

AI 层不会生成统计候选列表。浏览器会先显示本地候选英雄，再请求一份经过校验的推荐结果、理由、风险和备选方案；所有结果只能从该候选列表中选择。即使服务商响应缓慢、不可用或返回无效结果，也不会延迟或替代本地推荐。

每位用户都需要在运行时提供自己的 OpenAI 兼容服务商凭据。切勿将真实 API Key 写入仓库、README、命令历史记录或任何用于分发的文件。请在用户本地 Shell 环境中设置凭据，并使用 `--ai` 启动本地服务器：

```bash
# macOS / Linux
export AI_API_KEY="your-key"
export AI_MODEL="your-model"
export AI_BASE_URL="https://api.openai.com/v1"  # 可选；这是默认值

# Windows PowerShell
$env:AI_API_KEY="your-key"
$env:AI_MODEL="your-model"
$env:AI_BASE_URL="https://api.openai.com/v1"   # 可选

bp h5 --matchup "Team Spirit|Team Liquid" --serve --ai
```

也可以使用 `OPENAI_API_KEY` 和 `OPENAI_BASE_URL`。模型和基础 URL 可通过 `--ai-model` 和 `--ai-base-url` 覆盖。上面的 `your-key` 等值仅为占位符。每个安装实例都使用自己的密钥；仓库和发布包中均不包含服务商凭据。API Key 只保留在本地 Python 进程中，绝不会嵌入页面；选秀状态、策略文本、本地候选英雄及其证据将发送给配置的服务商。

为保障安全，AI 模式只允许监听回环地址（`127.0.0.1`、`localhost` 或 `::1`），且远程服务商必须使用 HTTPS。同一台计算机上运行的模型网关仍可使用普通 HTTP。每次运行都会在浏览器 URL 片段中生成一个不可预测的令牌；只有携带该令牌的请求才能调用付费 AI 端点。该令牌只在本地短期有效，不会发送给服务商。如果需要通过局域网 Wi-Fi 在手机上访问浏览器面板，请不要使用 `--ai`。

## 开发

```bash
python -m pip install -e ".[dev]"
python -m ruff check src scripts tests
python -m pytest -q
```

设计说明见 [`PLAN.md`](PLAN.md)；最初的第一至第二阶段任务拆分见 [`docs/TASKS-phase1-2.md`](docs/TASKS-phase1-2.md)。

## 数据处理流程

```bash
bp constants                          # 从 dotaconstants 获取英雄和版本数据
bp sync index --since 2026-03-24      # 回填 /proMatches，之后执行增量同步
bp sync matches --limit 1500          # 获取 /matches/{id}；支持恢复，并在达到每日额度时停止
bp normalize                          # -> matches / draft_events / players / teams / roster_snapshots
bp formats                            # 根据数据推断各版本的队长模式操作顺序
bp check                              # 生成质量标记；硬性标记会设置 matches.excluded=1
bp export --as-of 2026-08-01          # 生成不可变快照 data/snapshots/snapshot_<date>_<hash>.sqlite
bp status
bp update --since 2026-03-24 --limit 1500   # 一次完成 index+details+normalize+formats+check
```

无需安装也可运行 `python -m bp ...`（设置 `PYTHONPATH=src`）。

## 侦察（第二阶段）

```bash
bp teams --q spirit                                   # 解析战队 ID 和名称
bp report --us "Team Spirit" --them "Team Liquid"     # -> reports/<us>-vs-<them>-<patch>.md
bp draft  --us "Team Spirit" --them "Team Liquid" --first them   # 交互式面板；支持英雄名称、undo、state、quit
bp h5 --matchup "Team Spirit|Team Liquid" --serve      # 由权威 Python 推荐器驱动的浏览器面板
bp --db data/snapshots/snapshot_20260801_<hash>.sqlite report --us ... --them ...   # 基于冻结快照进行时间点分析
bp blindtest --snapshot data/snapshots/snapshot_20260801_<hash>.sqlite --patch 7.41 --test-matches docs/baseline-test-matches.json --out docs/baseline.md
bp lineup --match 8960991322 --swap "Juggernaut=Kez"           # P（天辉获胜 | 十名英雄），仅使用该场比赛之前的数据训练
bp lineup --radiant "Axe,Invoker,Rubick,Hoodwink,Kez" --dire "Bane,Lifestealer,Pangolier,Mirana,Dark Seer"
bp lineup-eval --snapshot data/snapshots/snapshot_20260801_<hash>.sqlite --patch 7.41 --out docs/lineup-baseline.md
```

`bp h5 --serve` 会打开一个本地浏览器页面，并由同一进程提供 `POST /api/recommend`，因此面板使用的是 `recommend.py`，而不是简化的离线 JavaScript 评分。对手的操作始终需要手动输入；只有轮到我方操作时才会显示候选英雄。添加 `--host 0.0.0.0` 后，可在同一 Wi-Fi 下通过手机访问该页面。普通的 `bp h5` 仍会生成单个离线 HTML 文件，并回退到较轻量的页面内评分。

候选英雄仅作为**参考资料**：在冻结盲测中，禁用推荐优于版本热门度基线，但选人推荐并未胜出（见 `docs/baseline.md`）。因此，请结合阵容名单和比赛 ID 解读推荐结果，而不要将其直接视为 BP 指令。

所有分数都是可解释统计数据的线性组合，权重定义在 `config/scoring.yaml` 中。另外两个组件在通过 `docs/baseline.md` 所述的开发集/冻结集验证流程前默认关闭（权重为 0）：`pick.position_fit`（该英雄是否属于尚未选定英雄的阵容选手的英雄池）和 `ban.their_next_pick`（对手在当前 BP 前缀之后选择该英雄的占比）。无需修改 YAML 即可试用：

```bash
bp blindtest --snapshot ... --test-matches docs/development-test-matches.json --weight pick.position_fit=1.0 --weight ban.their_next_pick=1.0
```

高分段天梯比赛（`scripts/pull_public.py` -> `data/db/public.sqlite`）可以作为密集先验数据加入克制/配合关系表：传入 `--public data/db/public.sqlite`，并设置 `context.public_weight` / `lineup.public_weight`（一场天梯比赛相对于一场职业比赛的权重；0 表示关闭，也可通过 `--weight` 设置）。150 万行数据的统计过程已向量化并带有缓存，因此只需数秒即可完成一次计算。

`bp report` / `bp draft` / `bp lineup` / `bp h5` 会将数据帧、画像和拟合后的阵容模型缓存在 `data/cache/` 中。缓存键由数据库文件、筛选条件、权重和源文件时间戳组成，因此比赛日命令可以立即打开；使用 `--no-cache`（或 `BP_NO_CACHE=1`）可重新构建缓存。

BP 上下文项（`context.py`）包括：经过 Beta 平滑的克制矩阵（英雄与对手已选英雄）、配合矩阵（与我方已选英雄）以及根据 dotaconstants 标签计算的职责缺口填补。`bp blindtest --no-context` 可执行消融实验。

`bp lineup` 是独立的“BP 结束后哪方更占优”指标（即游戏内 0:00 时显示的预测）：只使用十名英雄，不考虑选手或战队。英雄/配合/克制表采用经过 Beta 收缩的残差对数几率（从未出现过的组合严格为 0），按比赛求和后，再由使用折外聚合数据训练的逻辑回归堆叠器进行校准。`docs/lineup-baseline.md` 记录了相对于常数基线和纯英雄基线的对数损失/Brier 分数以及可靠性表；目前仅使用职业比赛数据时，英雄组合项尚未带来提升（见该文档），因此在信任克制关系之前，需要先加入高分段公开比赛数据。

报告中的每个数值最多附带 5 个比赛 ID，以便在 OpenDota 上核验。

## 项目结构

```text
src/bp/
  opendota.py      客户端：限流、重试、原始缓存（data/raw/<endpoint>/<id>.json，含 fetched_at/schema_version）
  sync.py          索引回填/增量同步和比赛详情获取
  constants.py     dotaconstants 英雄/版本数据
  normalize.py     原始载荷 -> 数据表；phase = 连续相同 is_pick 的操作；position_est = 队内 GPM 排名
  draft_formats.py 各版本操作顺序 = 相对 (is_pick, team) 特征的众数
  quality.py       硬性/软性标记（见 HARD/SOFT）
  export.py        时间点快照和 data_version
  db.py            数据库架构
  profiles.py      P2-01..05：选手 × 英雄统计（Beta 平滑、时间衰减）、绝活得分、禁用压力、
                   战队英雄/阶段习惯、双英雄配合、对手应对关系；位置 = lane_role + GPM
  recommend.py     P2-07：带证据和预测应对的选人/禁用候选评分
  context.py       输入 recommend 的克制/配合/阵容缺口项（未见组合 == 0）
  report.py        P2-09：Markdown 侦察报告
  draft_state.py   P2-06：队长模式状态机（由赛制参数驱动）
  web.py           本地 H5 服务器：权威推荐 API、请求校验、无额外依赖
  blindtest.py     P2-10：在快照 as_of 之后重放真实 BP，比较 Top-k 命中率与版本热门度基线
  lineup.py        P（天辉获胜 | 十名英雄）：收缩残差表和校准逻辑回归堆叠器
  public.py        天梯比赛 -> 向量化英雄组合计数（供上下文/阵容表使用的密集先验）
  cache.py         data/cache 下的 pickle 缓存（数据帧/画像/阵容模型），由代码和数据时间戳失效
scripts/backfill.py  OpenDota 多日回填程序（第一轮及之后每 --full-every 轮执行完整索引遍历）
scripts/pull_public.py  高分段 /publicMatches 拉取程序，带可恢复游标
```

CI（`.github/workflows/ci.yml`）会在每次推送和拉取请求时运行 `ruff check` 和 `pytest`。

## 注意事项

- BP 格式绝不手工编写；任何不符合对应版本众数的比赛都会标记为 `draft_format_anomaly` 并排除。
- `position_est` 是基于 GPM 排名的启发式估计（1 表示 GPM 最高）。用于区分核心和辅助位置已经足够，但不要依赖它区分三号位和四号位。
- 快照只包含 `start_time < as_of` 的比赛；`players.last_seen` / `teams.last_seen` 会在快照内重新计算，确保 `as_of` 之后的信息不会泄漏。
