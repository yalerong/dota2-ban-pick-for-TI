# Phase 1–2 可执行任务清单

> 状态（2026-08-26）：P1-01～P1-09 已合并（PR#1）；P2-01～P2-10 已实现，P2-11 Streamlit 未做。盲测基线见 `docs/baseline.md`。

对应 `PLAN.md` 的 Phase 1（数据基础）和 Phase 2（Scouting MVP）。目标是**两周到三周**内拿到第一份有证据的对手 BP 画像报告，其余 Phase 一律不动。

## MVP 定义（做到这里就算 Phase 2 完成）

输入：我方战队 ID、对手战队 ID、Patch、时间范围。
输出：一份 Markdown 赛前报告，包含

1. 双方十名选手在当前 Patch 的有效英雄池 + 绝活分
2. 对手一阶段 Ban/Pick 习惯（前 N 手的频率、对我方的针对性 Ban）
3. 我方需要保护 / 抢选 / 放出的英雄清单
4. 每条建议附比赛 ID 列表，可以点开 OpenDota 核对

不含：Policy/Value 模型、自博弈、DraftBook、实时接入。

## 前置决策（开工前定，给了默认值）

| 决策 | 默认值 | 说明 |
|---|---|---|
| 目标战队 / 对手 | 任选两支近一年打过 ≥3 次 Tier-1 的队 | 验证用，不需要真实合作方 |
| 历史范围 | 最近两个大版本 | 免费档 2000 次/天，两版本约 3–6k 场，两三天回填完 |
| 数据源 | 仅 OpenDota + dotaconstants | STRATZ 需要 token 且字段不同，推到 Phase 3 之后 |
| 技术栈 | Python 3.11 + SQLite + pandas | 和 factor-analysis 同栈，可以直接借数据版本戳和 gate 的写法 |
| 部署 | 本地 CLI | Streamlit 看板放 Phase 2 最后一项，可砍 |

## 阶段 1：数据基础

### P1-01 项目骨架
- 产出：`pyproject.toml`、`src/bp/`、`data/raw/`、`data/db/`、`.env.example`、`.gitignore`
- 验收：`python -m bp --help` 能跑；`.env` 与 `data/` 被 gitignore 挡住
- 依赖：无　估时：0.5 天

### P1-02 OpenDota 客户端
- 产出：`bp/opendota.py`：限速（60/分、2000/天）、指数退避、失败重试、`api_key` 可选
- 原始响应落盘 `data/raw/<endpoint>/<id>.json`，附 `source / fetched_at / schema_version`
- 验收：断网时读缓存不报错；同一 ID 二次请求不发网络
- 依赖：P1-01　估时：1 天

### P1-03 职业比赛索引回填 + 增量
- 产出：`bp sync index --since <date>`
- `/proMatches` 按 `less_than_match_id` 翻页，写 `match_index` 表（match_id, start_time, leagueid, radiant/dire team_id, radiant_win）
- 增量模式：从本地最大 match_id 向前翻，遇到已存在即停
- 验收：重复跑不重复写；日志打印新增条数
- 依赖：P1-02　估时：0.5 天

### P1-04 比赛详情拉取
- 产出：`bp sync matches`，对 `match_index` 中未拉取的 ID 拉 `/matches/{id}`
- 只保留需要的字段：`picks_bans`、`players[].account_id/hero_id/player_slot`、`radiant_team_id/dire_team_id`、`patch`、`radiant_win`、`leagueid`、`start_time`、`duration`、`draft_timings`（可缺）
- 验收：拉取进度可续跑；缺 `picks_bans` 的比赛被标记而不是丢弃
- 依赖：P1-03　估时：1 天（含跑数时间）

### P1-05 静态元数据
- 产出：`heroes` 表（来自 dotaconstants `heroes.json`）、`patches` 表（`patch.json`，含发布时间）
- 验收：任意 hero_id 可映射到英文名和中文名（中文名后补一份手工 CSV）
- 依赖：P1-01　估时：0.5 天

### P1-06 Draft 格式表（按 Patch）
- 产出：`draft_formats` 表：patch → 动作序列（每步 是否 pick / 先后手 / 阶段）
- **不要手写**：从 P1-04 数据里按 patch 统计 `picks_bans` 的长度和 `order→(is_pick, team)` 序列的众数，自动推断；与众数不符的比赛标记 `draft_format_anomaly`
- 验收：每个 patch 只有一个主格式；异常比例 < 5%；打印每个 patch 的格式供人工过目
- 依赖：P1-04　估时：1 天
- 备注：这是 Phase 2 状态机的唯一依据，也是跨版本回放不出错的关键

### P1-07 规范化存储
- 产出 SQLite 表：
  - `matches`（match_id, patch, league, start_time, radiant_team, dire_team, radiant_win, duration, quality_flags）
  - `draft_events`（match_id, order, team_side, is_pick, hero_id, phase）
  - `players`（account_id, 当前公开名）
  - `teams`（team_id, 当前名, 历史名列表）
  - `roster_snapshots`（match_id, team_id, account_id, player_slot, 推断位置）
- 位置推断：MVP 只用"该账号在该战队近 30 场的 player_slot 众数"，不用 lane_role
- 验收：外键完整；一场比赛正好 10 条 roster 行；转会前后同一 account_id 在不同 team_id 下各有记录
- 依赖：P1-04、P1-05、P1-06　估时：1.5 天

### P1-08 数据质量检查
- 产出：`bp check`，输出每项检查的通过/失败计数并写 `quality_flags`
- 检查项：BP 动作数与 `draft_formats` 一致 / 英雄 ID 全部可映射 / 战队 ID 非空 / 匿名账号（account_id 为空）/ 重复 match_id / 时长 < 10 分钟 / 缺 `draft_timings`
- 验收：带任一硬失败标记的比赛在后续聚合中默认排除，且可以用 `--include-flagged` 打开
- 依赖：P1-07　估时：1 天

### P1-09 可回放数据集 + 版本戳
- 产出：`bp export --as-of <date>`，导出 `as_of` 之前的全部表为一个不可变快照（parquet 或独立 sqlite），文件名带 `data_version = sha(表内容 + 最大 match_id + 导出时间)`
- 验收：同一 `as_of` 两次导出 hash 一致；`as_of` 之后开始的比赛一条都不进快照
- 依赖：P1-08　估时：1 天
- 备注：这一条直接照搬 factor-analysis 的 data_version 戳思路，后面所有盲测都钉这个快照

**阶段 1 合计约 8 个工作日；里程碑：`bp check` 全绿 + 一份带版本戳的快照。**

## 阶段 2：Scouting MVP

### P2-01 选手 × 英雄 × Patch 画像
- 产出：`player_hero_patch_stats`（account_id, hero_id, patch, position, games, wins, last_played, 时间衰减后的 games_w / wins_w）
- 平滑：胜率用 Beta 先验（先验 = 该英雄当版本全体职业胜率，强度 10 场）
- 衰减：半衰期 90 天，Patch 边界额外 ×0.7
- 验收：抽 3 名知名选手，绝活英雄肉眼可信；样本 < 3 的组合胜率不会出现 100%/0%
- 依赖：P1-09　估时：1 天

### P2-02 绝活分基线
- 产出：`signature_score` 按 PLAN 六个分项实现，**每个分项都是可解释的统计量**，权重先手工定，写进 `config/scoring.yaml`
- 分项 MVP 口径：
  - pick_frequency：衰减后选用率
  - relative_win_lift：选手该英雄胜率 − 选手整体胜率（平滑后）
  - targeted_ban_pressure：见 P2-03
  - recent_usage：最近 30 天是否用过
  - role_adjusted_performance：MVP 先置 1，留接口
  - tournament_readiness：MVP 先置 1，留接口
- 验收：输出 Top-10 绝活列表 + 每项分值；改 yaml 权重不用改代码
- 依赖：P2-01　估时：1 天

### P2-03 针对性 Ban 压力
- 产出：对每 (team_id, hero_id, patch)：对手在一阶段 Ban 该英雄的次数 / 面对该队的总场次；以及对 (account_id, hero_id) 的归属（该英雄在该队谁用）
- 验收：能回答"对手打我们时前 3 手 Ban 最多的是什么"；能反过来回答"我们打对手时通常 Ban 了什么"
- 依赖：P1-07、P2-01　估时：1 天

### P2-04 战队画像 + 一阶段 BP 习惯
- 产出：`team_hero_patch_stats`（战队 × 英雄 × 阶段：pick 率、ban 率、先/后手拆分、胜率）；一阶段 Pick/Ban 前 N 手的频率分布
- 附"体系英雄"简化判定：与该队其他高频英雄共现率显著高于基线的英雄组合（成对共现即可）
- 验收：能输出"对手先手时一阶段最常 Ban 的 5 个英雄 + 最常首选的 5 个英雄"
- 依赖：P1-07　估时：1 天

### P2-05 对手回应边（简化版）
- 产出：`opponent_response_edges`（team_id, patch, 我方已选/已 Ban 前缀 hash, 对手下一手 hero_id, 次数）
- MVP 只做前缀长度 1–3 手，超过就退化到 P2-04 的无条件频率
- 验收：给定前缀能返回带样本数的候选回应列表；样本数 < 3 时明确标"证据不足"
- 依赖：P1-07　估时：1 天

### P2-06 Captain's Mode 状态机
- 产出：`bp/draft_state.py`：按 `draft_formats` 参数化；支持 apply(action)、undo、当前轮到谁、剩余动作、合法英雄集合
- 验收：把 P1-07 里任意一场真实比赛的 `draft_events` 逐条 apply 不报错，且终态与 OpenDota 一致；对每个 patch 各跑 50 场
- 依赖：P1-06　估时：1 天

### P2-07 候选 Pick/Ban 输出
- 产出：给定状态机当前状态 + 双方战队，输出 ≥3 个候选动作，每个带：类型 / 英雄 / 依据（绝活分、Ban 压力、战队习惯、回应边）/ 样本 match_id 列表 / 置信度（样本量映射）
- 打分 MVP：线性加权，权重进 `config/scoring.yaml`；不做搜索
- 验收：每条候选点开 match_id 能在 OpenDota 上验证；无样本时不给出"看起来自信"的建议
- 依赖：P2-02～P2-06　估时：1.5 天

### P2-08 手动 Draft Board（CLI）
- 产出：`bp draft --us <id> --them <id> --patch <p> --first-pick us|them`，交互式逐手输入动作，每步打印 P2-07 候选
- 验收：一场完整 BP 从头走到尾；`undo` 可用
- 依赖：P2-06、P2-07　估时：1 天

### P2-09 赛前报告生成
- 产出：`bp report --us --them --patch` → `reports/<us>-vs-<them>-<patch>.md`，按 MVP 定义四节输出，附 `data_version`
- 验收：报告能独立阅读；所有数字可追溯到 match_id
- 依赖：P2-02～P2-05　估时：1 天

### P2-10 端到端盲测
- 产出：选一个已结束的赛事（如最近一次 Major），用 `--as-of` 赛事开始前一天的快照生成报告，再逐场用 P2-08 回放真实 BP，记录候选 Top-3 对实际动作的命中率
- 验收：整条链路无未来数据泄漏（快照里没有赛事期间的比赛）；命中率数字写进 `docs/baseline.md` 作为后续所有模型的对照基线
- 依赖：全部　估时：1 天

### P2-11（可砍）Streamlit 看板
- 产出：把 P2-08、P2-09 套一层 Streamlit
- 依赖：P2-08、P2-09　估时：1 天

**阶段 2 合计约 10 个工作日；里程碑：`docs/baseline.md` 里有 Top-3 命中率数字。**

## 明确不做

- STRATZ、replay 解析、任何私有数据
- Policy / Value / Opponent Policy 模型训练
- 自博弈、MCTS、Beam Search
- DraftBook 目录与合并逻辑
- 实时 `/live` 接入、Overlay
- 角色权限、审计日志

## 顺序与并行

```
P1-01 → P1-02 → P1-03 → P1-04 → P1-06 → P1-07 → P1-08 → P1-09
             └→ P1-05 ─────────────┘
P1-09 → P2-01 → P2-02 ─┐
P1-07 → P2-03, P2-04, P2-05 ─┼→ P2-07 → P2-08 → P2-10
P1-06 → P2-06 ─────────┘     └→ P2-09 ─┘
```

P1-04 跑数的两三天里可以并行做 P1-05、P1-06 的推断脚本和 P2-06 状态机。
