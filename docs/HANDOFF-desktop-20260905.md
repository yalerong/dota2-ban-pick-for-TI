# dota2-ban-pick-for-TI 交接（给桌面机，2026-09-05）

写给有数据的那台机器（原交接文档里的 `C:\Users\z1628\Desktop\dota bp`）。这次改动是在另一台没有数据的机器上做的，
只跑了单元测试，**没有在真实数据上跑过任何一次盲测或 lineup-eval**。所以这份文档的核心是：合并 PR 后，你在桌面机上
按第 3 节把实验跑一遍，再决定要不要打开新开关。

## 1. 发生了什么

整仓 review 后做了一轮"实用性"优化，全部在 PR#12：
https://github.com/yalerong/dota2-ban-pick-for-TI/pull/12 （分支 `review/practical-optimizations`，CI 已过，57 个测试全绿）

| 类别 | 改动 | 对你的影响 |
|---|---|---|
| 速度 | `assign_positions` 向量化（7000 场 4.8s → 0.05s）；`candidates()` 改 dict 查表；`build_profile` 只算一次 `team_draft`；先验记忆化 | 每条命令启动更快，盲测少约 1 分钟纯查表开销 |
| 赛日秒开 | 新增 `src/bp/cache.py`：report / draft / lineup / h5 把 Frames、profile、拟合好的 lineup 模型缓存到 `data/cache/` | 同一数据第二次运行秒开；`--no-cache` 或 `BP_NO_CACHE=1` 强制重算 |
| 新评分项 | `pick.position_fit`、`ban.their_next_pick`，**默认权重 0** | 生产结果不变，需要你按协议验证 |
| 天梯数据 | 新增 `src/bp/public.py`；`--public data\db\public.sqlite` 把 pull_public 拉的高分段局并入克制/协同表和 lineup 表；`context.public_weight` / `lineup.public_weight` **默认 0** | 同上，需要验证 |
| 实验开关 | `blindtest` / `lineup` / `lineup-eval` 支持 `--weight SECTION.KEY=VALUE`（可重复） | 不用改 yaml 就能做实验 |
| 运维 | `backfill.py` 只在第 1 轮和每 7 轮做全量索引（`--full-every`）；日志脱敏 api_key；`.env` 支持带引号；`pull_public.cmd` 不再硬编码 `--patch 7.41 --target 1500000`；CI（ruff + pytest） | 见第 4 节的注意事项 |
| 说明 | 报告、draft board、README 加了"仅供参考"：Pick 推荐尚未跑赢 meta 基线 | 无 |

为什么新东西都默认关闭：`docs/baseline.md` 规定改生产评分必须先过独立开发集（`docs/development-test-matches.json`），
再到冻结验收集（`docs/baseline-test-matches.json`）做一次晋级判断。这道门只有桌面机能跑。

## 2. 拿到代码

```powershell
cd "C:\Users\z1628\Desktop\dota bp"
git fetch origin
# 方式 A：先在分支上跑实验，满意再合并 PR
git checkout review/practical-optimizations
# 方式 B：直接在 GitHub 合并 PR#12，然后
git checkout main; git pull

pip install -e .[dev]          # 新增了 numpy 显式依赖和 ruff
$env:PYTHONPATH="src"; $env:PYTHONIOENCODING="utf-8"
python -m pytest -q            # 应为 57 passed
python -m bp status            # 确认数据库、pending、剩余额度都正常
```

`data/` 结构不变，第一次运行会多出 `data/cache/`。

## 3. 需要你跑的实验（按顺序，每条都可重复执行）

先确认当前 snapshot 的 hash（`data\snapshots\` 下最新的 `snapshot_20260801_<hash>.sqlite`；交接文档记录 Run 3 是 `58e7a04b5d620dcd`）。
下面用 `$S` 代替。

```powershell
$S = "data\snapshots\snapshot_20260801_58e7a04b5d620dcd.sqlite"
$DEV = "docs\development-test-matches.json"
$FROZEN = "docs\baseline-test-matches.json"
```

### 3.1 基线对齐（先确认新代码没有改变旧结果）

```powershell
python -m bp blindtest --snapshot $S --patch 7.41 --test-matches $FROZEN --out docs\baseline-run4-check.md
```

**预期**：和 `docs/baseline-run2-on.md` / Run 3 记录的冻结集数字一致（Top-3 约 21.5%，Ban 约 23%，Pick 约 19%）。
速度改动不应改变任何数字；如果有差异，先停下来，把两份 md 的差异贴出来，不要继续。

### 3.2 位置感知 Pick（开发集）

```powershell
python -m bp blindtest --snapshot $S --patch 7.41 --test-matches $DEV --out docs\dev-posfit-off.md
python -m bp blindtest --snapshot $S --patch 7.41 --test-matches $DEV --weight pick.position_fit=1.0 --out docs\dev-posfit-1.md
python -m bp blindtest --snapshot $S --patch 7.41 --test-matches $DEV --weight pick.position_fit=2.0 --out docs\dev-posfit-2.md
```

看 **picks** 行和 `phase3_pick` / `phase4_pick`（末阶段）的 Top-3。这项只作用于 Pick，Ban 行应完全不变。
判定：开发集 picks Top-3 比 off 高 ≥ 1pt 且末阶段不降，才拿到冻结集跑一次：

```powershell
python -m bp blindtest --snapshot $S --patch 7.41 --test-matches $FROZEN --weight pick.position_fit=1.0 --out docs\baseline-run4-posfit.md
```

晋级规则照旧：冻结集 overall Top-3 必须超过 Run 2 的 21.7%。通过才把 `config/scoring.yaml` 和 `src/bp/scoring.yaml`
（两份必须一致，有测试卡着）里的 `pick.position_fit` 改成对应值。

### 3.3 Ban 用对手下一手 pick（开发集）

```powershell
python -m bp blindtest --snapshot $S --patch 7.41 --test-matches $DEV --weight ban.their_next_pick=1.0 --out docs\dev-nextpick-1.md
python -m bp blindtest --snapshot $S --patch 7.41 --test-matches $DEV --weight ban.their_next_pick=2.0 --out docs\dev-nextpick-2.md
```

看 **bans** 行。这项只作用于 Ban。判定和晋级方式同 3.2。两项都有效的话再跑一次两个 `--weight` 同时开的组合。

### 3.4 天梯数据接入 lineup 模型

前提：`python scripts\pull_public.py --status` 里 `ranked_all_pick` 有足够行数（几十万以上才有意义），且 `by_patch` 里 7.41 占主体。

```powershell
python -m bp lineup-eval --snapshot $S --patch 7.41 --out docs\lineup-run4-off.md
python -m bp lineup-eval --snapshot $S --patch 7.41 --public data\db\public.sqlite --weight lineup.public_weight=0.1 --out docs\lineup-run4-pub01.md
python -m bp lineup-eval --snapshot $S --patch 7.41 --public data\db\public.sqlite --weight lineup.public_weight=0.3 --out docs\lineup-run4-pub03.md
python -m bp lineup-eval --snapshot $S --patch 7.41 --public data\db\public.sqlite --weight lineup.public_weight=1.0 --out docs\lineup-run4-pub1.md
```

第一次带 `--public` 会解析全部天梯行并缓存（150 万行约几秒到几十秒），之后秒开。
看 `model` 行的 log-loss / Brier 和可靠性表。当前记录是 log-loss 0.6903（常数 0.6928），说明模型几乎没信号；
天梯数据的意义就是看 `synergy` / `counter` 系数和 log-loss 能不能明显动起来。
晋级规则见 `docs/baseline.md` 末尾：同一测试集 log-loss 更低，且 n ≥ 30 的可靠性行偏差 ≤ 5pt。
用冻结集再确认一次：加 `--test-matches $FROZEN`。

### 3.5 天梯数据接入 BP 推荐的克制/协同项（可选，最后做）

```powershell
python -m bp blindtest --snapshot $S --patch 7.41 --test-matches $DEV --public data\db\public.sqlite --weight context.public_weight=0.2 --out docs\dev-ctxpub-02.md
```

Run 2 / Run 3 已经证明职业局的 context 项对 Pick 是负贡献；换成稠密的天梯先验后再看 picks 行是否转正。

### 3.6 记录

每次把 snapshot hash、test-set hash（md 头部有）、`--weight` 参数和结果追加到 `docs/baseline.md`，
**不要**按冻结集结果调权重。

## 4. 注意事项

- **缓存**：`data/cache/` 的 key 含数据库文件戳、过滤条件、权重和源码戳，改代码、重导快照、换 `--weight` 都会自动失效。
  怀疑结果不对时先加 `--no-cache` 跑一遍对比，或直接删掉 `data/cache/`。盲测和 lineup-eval 本身不走缓存，只有 `--public` 的计数走。
- **`pull_public.cmd` 用法变了**：以前双击就自带 `--patch 7.41 --target 1500000`，现在必须自己传：
  `scripts\pull_public.cmd --patch 7.41 --target 1500000`。已经在跑的进程不受影响（`data\pull_public.pid`）。
- **`backfill.py`**：默认每 7 轮做一次全量索引，其余轮增量，每天省约 150 次调用。如果发现有晚解析的老比赛没进索引，
  可以 `--full-every 1` 恢复旧行为。
- **`.env`**：现在 `OPENDOTA_API_KEY="xxx"` 带引号也能读；日志里 key 会显示成 `api_key=***`。
- **两份 scoring.yaml**（`config/` 和 `src/bp/`）必须一模一样，有测试卡着，改权重要改两处。
- 盲测用的 `--weight` 只影响那一次运行，不会写回 yaml。
- 新评分项的证据行长这样，方便核对：`in the pool of <选手名> (still without a hero)`、`they picked <英雄> next Nx from a similar draft position`。

## 5. 跑完之后告诉我什么

1. 3.1 是否与旧数字一致。
2. 3.2 / 3.3 开发集三行（overall / bans / picks）的 Top-3 对比表，以及末阶段 Pick。
3. 3.4 四个权重下的 log-loss、`counter` / `synergy` 系数、天梯行数。
4. 哪些开关你决定打开，我据此改 yaml 默认值并更新 README / 交接.md。
