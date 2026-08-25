# Dota 2 自治 BP Intelligence System 计划

## Approach

本项目面向职业与半职业 Dota 2 战队，构建一套以真实比赛为反馈、能够持续学习的 Captain's Mode BP 决策系统。系统复用 OpenDota、STRATZ 和现有开源 Draft 工具提供的数据与基础交互，不重复建设英雄数据库、通用 Counter Picker 或静态胜率站点。

系统的核心价值是：自动识别双方选手绝活与战队体系、模拟对手回应、给出有证据的 Pick/Ban 建议，并在新比赛结束后根据真实胜负继续训练和验证。

## Product Positioning

### Target users

- 职业、半职业和青训战队
- 队长、教练与数据分析师
- 需要准备 BO3/BO5 的赛事选手

### Core promise

在指定我方战队、对手、Patch、阵营和系列赛状态后，系统自动生成：

- 双方五名选手的有效英雄池与绝活英雄
- 对手的一阶段 Pick/Ban 习惯和常见回应
- 我方需要保护、抢选、交换或放出的英雄
- 当前 BP 每一步的候选动作、依据、风险和后续分支
- 赛后自动复盘和新一轮模型学习结果

### Differentiation

本项目不是面向普通玩家的通用选人助手，也不是单纯预测完整阵容胜率。主要差异为：

- 战队对战上下文，而非全局 Meta 排名
- 选手绝活、战队体系与对手针对性 Ban 的联合建模
- 完整 Captain's Mode 与 BO3/BO5 系列赛上下文
- Policy、Value、Opponent Policy 和自博弈结合
- 新比赛到来后的自动训练、验证、晋级和回滚
- 教练可通过 Markdown DraftBook 注入战队私有知识
- 每项建议提供比赛样本、Patch、选手和规则来源

## Scope

### In

- 自动同步公开职业比赛、完整 BP、选手、战队、Patch 和胜负
- 构建选手英雄池、绝活分、战队体系和对手 BP 画像
- 支持完整 Captain's Mode Draft 状态和系列赛上下文
- 构建 Policy、Value 与对手行为模型
- 支持历史盲测、离线强化学习与 Draft 自博弈
- 自动处理 Patch 更新、数据衰减和模型冷启动
- 提供教练可配置的 Markdown DraftBook
- 提供模型版本、数据版本、审计、灰度和自动回滚
- 提供本地或私有部署的教练工作台

### Out

- 第一阶段不做游戏进程注入或读内存
- 第一阶段不依赖实时 Overlay 或自动操作客户端
- 不重新建设通用英雄资料、物品资料和公开战绩网站
- 不将 LLM 作为 Pick/Ban 决策的唯一来源
- 不用未执行的假想 BP 直接冒充真实胜负训练样本
- 不让模型无约束修改生产代码并自行部署

## System Overview

```text
OpenDota / STRATZ / team replays
                |
                v
      ingestion + normalization
                |
                v
 match / roster / patch / draft store
                |
        +-------+--------+
        |                |
        v                v
 player/team profiles   DraftBook compiler
        |                |
        +-------+--------+
                v
  Policy + Value + Opponent Policy
                |
                v
      search / self-play engine
                |
                v
       coach BP decision cards
                |
                v
       actual draft and result
                |
                v
 walk-forward evaluation + retraining
                |
                v
 champion/challenger promotion or rollback
```

## Data Acquisition

### Primary sources

- OpenDota `/proMatches`：获取职业比赛索引
- OpenDota `/matches/{match_id}`：获取完整 `picks_bans`、选手、英雄、Patch、阵营和胜负
- OpenDota `/live`：发现进行中的可观战比赛和当前比赛状态
- STRATZ GraphQL：补充位置、分段、选手、对线和更细粒度统计
- `dotaconstants`：英雄名称、ID 和静态元数据
- 战队提供的比赛 ID 或 replay：补充训练赛和私有数据

### Acquisition policy

- 首次运行回填指定时间范围内的职业比赛
- 后续按 Match ID 增量同步，不重复下载已完成数据
- 所有原始响应保留来源、抓取时间和 Schema 版本
- API 暂时不可用时使用本地缓存继续提供 BP 服务
- 私有训练赛与公开比赛分库保存，并执行访问控制

### Data quality controls

- 检查完整 BP 是否包含预期 Pick/Ban 数量
- 检查选手、战队和英雄 ID 映射
- 保存每场比赛当时的 roster snapshot，避免转会污染
- 标记缺失 `draft_timings`、选手匿名或角色推断失败的比赛
- 对重复比赛、重赛和异常短局进行隔离

## Core Data Model

最低需要维护以下实体：

- `matches`：比赛、联赛、Patch、阵营、胜负和时间
- `draft_events`：每一步 Pick/Ban、队伍、英雄和顺序
- `players`：稳定账号 ID 与公开身份
- `teams`：战队 ID 与历史名称
- `roster_snapshots`：每场比赛当时的五人名单和位置
- `player_hero_patch_stats`：选手、英雄、位置和 Patch 画像
- `team_hero_patch_stats`：战队英雄、体系和 BP 阶段画像
- `opponent_response_edges`：特定 Draft 状态下的对手回应
- `model_decisions`：系统当时的候选动作和锁定预测
- `match_rewards`：真实结果及调整后的训练奖励
- `model_versions`：训练数据、特征、指标和部署状态
- `draftbook_snapshots`：每场比赛实际使用的教练配置

## Signature Hero and Team System Detection

### Player signature score

```text
signature_score =
  pick_frequency
  relative_win_lift
  targeted_ban_pressure
  recent_usage
  role_adjusted_performance
  tournament_readiness
```

所有分项必须使用样本平滑、时间衰减和 Patch 权重。绝活分表示选手掌握程度，不直接等于必须 Ban。

### Current threat score

```text
threat_score =
  signature_score
  * current_patch_viability
  * team_system_dependency
  * current_draft_fit
  * denial_value
```

### Required classifications

- 个人绝活：选手长期高频且表现高于个人基线
- 战队体系英雄：能够启动或连接固定阵容结构
- 当前版本英雄：近期 Patch 强势，但不一定属于绝活
- 摇摆英雄：能够隐藏位置或延迟阵容暴露
- 必 Ban 英雄：绝活、版本强度和当前 BP 风险共同达到阈值

## Learning System

### Models

- `Policy`：给定当前 BP 状态，选择我方下一手 Pick/Ban
- `Value`：评估当前状态或完整 Draft 的预期结果
- `Opponent Policy`：预测指定对手下一手最可能的回应
- `Draft World Model`：估计动作对后续英雄池、位置和阵容结构的影响

### Historical blind replay

对历史比赛按时间排序，处理第 N 场时只能使用前 N-1 场信息：

1. 重建比赛开始前可见的战队、选手、Patch 和系列赛状态。
2. 在每个 BP 节点先让系统生成并锁定候选动作。
3. 再揭示职业队实际动作和最终比赛结果。
4. 保存系统动作、实际动作、置信度和最终奖励。
5. 更新 Policy、Value 和 Opponent Policy。

### Experience classes

- `realized`：系统建议被实际执行，并有真实比赛结果；最高权重
- `observed`：职业队实际执行的动作和真实结果；高权重
- `counterfactual`：系统建议但未执行；只能由保守 Value 模型估计
- `self_play`：双方模型模拟的完整 BP；用于探索，不能替代真实比赛

### Reward

真实胜负是主要奖励，但需要降低纯执行差异带来的噪声：

```text
reward =
  actual_match_result
  - pre_draft_team_strength_expectation
  + small_auxiliary_draft_execution_signals
```

辅助信号只能低权重使用，例如对线结果、阵容预期窗口是否实现。不得用大量赛中数据让模型把选手操作错误全部归因于 BP。

### Self-play

1. 我方 Policy 选择动作。
2. 指定对手的 Opponent Policy 进行回应。
3. 双方完成完整 Captain's Mode Draft。
4. Value 模型评估叶节点。
5. Beam Search 或 MCTS 保留更高价值且符合英雄池约束的分支。
6. 新真实比赛到来后校准自博弈产生的价值判断。

## Patch-aware Weighting

### Experience weight

```text
experience_weight =
  time_decay
  * patch_similarity
  * hero_change_impact
  * role_environment_similarity
  * roster_similarity
  * sample_confidence
```

### Information-specific decay

- 选手英雄熟练度：慢衰减，允许跨多个小版本迁移
- 战队整体风格：较慢衰减
- 战队具体 BP 顺序：中等衰减
- 当前 Meta 强度：快速衰减
- 对线关系：相关英雄、物品、地图或经济变化后重算
- 阵容体系：任一关键组件发生重大变化后降权

### Patch bootstrap mode

新版本发布后系统自动：

1. 冻结旧版本模型作为先验。
2. 生成英雄、物品、地图、经济和通用机制的 Change Vector。
3. 对受影响英雄、位置和体系单独降权。
4. 保留长期选手熟练度和战队风格。
5. 优先同步当前版本职业赛和高分局数据。
6. 随有效样本增加逐渐替换旧版本先验。
7. 仅通过当前版本盲测决定新模型是否晋级。

## Coach DraftBook

Markdown 是教练可编辑的知识入口。机器配置放在 YAML Front Matter，正文用于解释和战术背景。

### Directory layout

```text
draftbook/
├─ global/
│  ├─ principles.md
│  └─ scoring.md
├─ teams/<our-team>/
│  ├─ team.md
│  ├─ roster/<account-id>.md
│  ├─ strategies/*.md
│  ├─ patches/<patch>.md
│  └─ private-notes.md
├─ opponents/<team-id>/
│  ├─ profile.md
│  ├─ threats.md
│  ├─ responses.md
│  └─ generated/*.md
└─ series/<series-id>/
   ├─ plan.md
   ├─ game-1.md
   └─ generated/bp-report.md
```

### Ownership rules

- 教练文件由教练维护，系统永不覆盖
- 系统生成内容只写入 `generated/`
- 每份配置包含 Schema 版本、适用 Patch、有效期和优先级
- 所有配置编译成统一 JSON 后再进入推荐引擎
- 每场比赛保存不可变 DraftBook Snapshot，保证复盘可重现

### Merge priority

```text
current series
> current opponent
> current patch
> temporary player readiness
> team defaults
> model recommendation
> global defaults
```

硬约束可以排除英雄；软偏好只能改变排序。过期配置自动降权并产生警告，不静默删除。

## Recommendation Output

每个决策节点至少输出三个候选动作：

- 推荐 Pick/Ban
- 预计对手回应
- 对我方后续英雄池和位置的影响
- 推荐依据和关联比赛样本
- 风险、置信度和替代路线
- 来源：模型、比赛数据或具体 DraftBook 规则

示例：

```text
First phase: Ban Chen

Evidence:
- opponent position 5 used Chen 12 times on the current patch, 8 wins
- Chen was first-phase banned against this roster in 9 recent games
- Chen connects their Beastmaster summon-push system
- our current roster has limited tournament-ready summon responses

If released:
- prioritize Doom
- preserve Clockwerk for position 4
- avoid exposing a second greedy core
```

## Autonomous Operations

- 定时同步新比赛并更新画像
- 在新 Patch、累计新比赛或漂移超阈值时自动训练 Challenger
- 自动执行时间盲测、当前 Patch 验证和对手留出验证
- Challenger 只有在关键指标改善且无回归时才灰度上线
- 在线指标恶化、数据异常或校准失效时自动回滚
- 所有模型、数据集、DraftBook Snapshot 和推荐均可审计
- LLM 只将结构化证据转化为说明，不生成未经验证的核心分数

## Evaluation

### Offline metrics

- 下一手 Pick/Ban Top-1、Top-3、Top-5 命中率
- Opponent Policy 的 MRR、Log Loss 和概率校准
- 绝活英雄识别的 Precision、Recall 和稳定性
- Value 模型的 Brier Score、AUC 和时间校准
- 当前 Patch、未知对手和新 roster 的独立表现
- 系统建议相对真实 Draft 的估计 regret

### Product metrics

- 赛前报告自动生成成功率
- 新比赛进入系统到画像更新的延迟
- 教练接受、修改和拒绝推荐的比例
- 单个 BP 决策的响应时间
- 建议所引用证据的完整率
- 自动训练、晋级和回滚的成功率

### Promotion gates

新模型必须同时满足：

- 当前 Patch 时间盲测优于线上 Champion
- 概率校准没有显著下降
- 未知对手和 roster 变化下没有明显回归
- 所有数据泄漏、Schema、完整性和性能检查通过
- 达到最低真实比赛样本量

## Security and Competitive Integrity

- 默认使用赛前公开数据、赛后比赛数据和战队自有数据
- 不读取游戏进程内存，不修改客户端
- 私有 DraftBook、训练赛和模型必须支持本地或私有部署
- 角色至少区分 coach、player、analyst 和 read-only viewer
- 记录配置修改、模型上线和报告访问审计日志
- 产品化实时功能前单独审查赛事规则和第三方辅助边界

## Implementation Phases

### Phase 1: Data foundation

- 建立职业比赛增量同步和本地缓存
- 规范化比赛、Draft、战队、选手、roster 和 Patch
- 构建数据质量检查和可重放数据集

### Phase 2: Scouting MVP

- 生成选手英雄池、绝活分和战队体系画像
- 生成对手一阶段 BP 和回应报告
- 支持手动 Captain's Mode Draft Board
- 输出有证据的候选 Pick/Ban

### Phase 3: Coach DraftBook

- 实现 Markdown + YAML Schema
- 实现优先级合并、热加载、冲突检查和 Snapshot
- 实现教练工作台和配置审计

### Phase 4: Learning loop

- 实现历史 Walk-Forward Replay
- 训练基础 Policy、Value 和 Opponent Policy
- 保存系统预测并在赛后自动结算奖励
- 建立 Champion/Challenger 模型注册与晋级流程

### Phase 5: Self-play and patch adaptation

- 实现 Draft 自博弈和分支搜索
- 实现 Patch Change Vector 和经验迁移权重
- 实现新 Patch Bootstrap Mode
- 建立自动漂移检测、灰度和回滚

### Phase 6: Team deployment

- 接入私有训练赛和教练反馈
- 提供本地或私有云部署
- 验证实际 BO3/BO5 工作流和决策时延
- 根据真实使用记录校准模型和交互

## Action Items

- [ ] 定义 OpenDota、STRATZ 和私有 replay 的数据契约与使用边界。
- [ ] 建立 `matches`、`draft_events`、`roster_snapshots` 和 Patch 规范化存储。
- [ ] 实现职业比赛历史回填、增量同步、缓存和数据质量检查。
- [ ] 实现选手绝活、战队体系、摇摆价值和针对性 Ban 的可解释基线评分。
- [ ] 建立完整 Captain's Mode 状态机和手动 Draft Board。
- [ ] 实现 DraftBook 目录、YAML Schema、编译、合并和 Snapshot。
- [ ] 实现按时间隔离的 Historical Blind Replay 和预测锁定机制。
- [ ] 训练并验证基础 Policy、Value 和 Opponent Policy。
- [ ] 实现 Patch 权重、Change Vector、冷启动和 Champion/Challenger 自动晋级。
- [ ] 使用真实系列赛完成端到端赛前、BP、赛后复盘和自动学习验证。

## Validation

- 使用至少一个完整职业赛事进行按时间顺序的离线回放，确认不存在未来数据泄漏。
- 随机抽查比赛详情与本地 BP 事件顺序、战队、选手、英雄和胜负一致。
- 验证转会前后 roster snapshot 不会互相污染。
- 验证新 Patch 到来后受影响英雄降权、未受影响熟练度保留。
- 验证未执行的 Counterfactual 动作不会被错误赋予真实胜负奖励。
- 验证教练硬约束始终覆盖模型建议，过期规则会产生明确警告。
- 验证模型晋级失败时线上 Champion 保持不变，退化时能够自动回滚。

## Remaining Decisions

- 第一支用于验证的目标战队、对手和历史时间范围。
- 第一版部署形态：纯本地桌面、战队内网 Web，或私有云。
- 战队是否能够提供训练赛 Match ID、Replay 和教练历史 BP 文档。
