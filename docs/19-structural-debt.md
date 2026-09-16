# 19 · 结构性欠债清偿计划：估值内核收敛 / scripts 下沉库层 / 回测引擎统一

> 出处：37 提交全量评审（Mnemon 83815832）定下的 S1–S3 结构债，Critical 批次已修复提交（`7ec0ced`，108 测试全绿）。
> 本文是执行计划 + 权威口径表。执行期间新增/变更的口径决定只改本文，不另开文档。

## 一、现状盘点（2026-09-16 实测，HEAD 7ec0ced）

### S2 · 库层反向依赖脚本层

- `src/libre_quant` 里有 **14 处 `from scripts.* import`**，分布在 6 个库模块：
  - `api.py` ×6（dashboard.build_data/_vol60、monthly_ma ×3、qdii_pricing.US_PROXY、ingest.ingest_one/resolve_one）
  - `review.py` ×3（backtest、monthly_ma、dca.simulate）
  - `replay.py` / `policy.py` / `accounts.py` 各 ×1（全部是 dca.xirr）
  - `jobs.py` ×2（ingest、shadow.run_daily）
- **6 个库模块**（api/review/replay/workbench/policy/accounts）靠 `sys.path.insert(PROJECT_ROOT)` 补丁维持可导入。
- 后果：uvicorn 从任意 cwd 起服务、或库被打包安装时直接崩；`pip install .` 形态不可能。

### S1 · 估值无单一内核

同一段"成交/现金流 → 份额·市值·盈亏·XIRR"的逻辑有 **7 条汇总路径**：

| # | 位置 | 口径 |
|---|---|---|
| 1 | `accounts.value_trades` | units/invested/fees/value/pnl/avg_cost/xirr（最完整） |
| 2 | `api.py` 实盘持仓 inline（~L892-906） | units/invested/avg_cost/value/profit，**无 fees、卖出净额处理与 #1 不同** |
| 3 | `workbench.run_history` 尾部 | invested/value/profit/max_dd，**不计佣金、闸门日资金蒸发** |
| 4 | `dca.simulate` 返回 dict | invested/value/xirr/dd/fees |
| 5 | `replay._summary` | 同上 + pending/pauses |
| 6 | `policy.summarize` | 同 #5（三胞胎） |
| 7 | `dashboard._arm_summary` | DB hist 元组 → 又一套 |

另有：**`max_dd` 5 份内联拷贝**（dca/backtest.metrics/replay/policy/workbench）、**波动率 4 个口径**（backtest.realized_vol 简单收益·总体方差·含当日；accounts.realized_vol 对数收益·样本方差；policy.rolling_vol 无前视版；dashboard._vol60 又一份）、**年化常数两套**（scripts 侧 252，库层 244）、**周日历两套**（`dca.is_first_of_week` 长假 gap≥5 也算新周 vs `review._first_of_week` 严格 isoweekday 回绕——长假周两入口结果不同）。

### S3 · 引擎碎片化

- **仓位策略引擎 5+ 份**：`backtest.run`、`monthly_ma.run_positions`、`review._positions_daily`、`multi_asset_review.simulate_type`、`attribution.simulate`，且 `backtest.main` 分年度段（~L262-283）在**同一文件内第三遍**重写循环。
- **定投/现金流引擎 5 份**：`dca.simulate`、`replay._run_arm`、`policy.run_policy`、`workbench.run_history`、`accounts.derive_paper_trades`，外加网格族 `simulate_ladder` / `simulate_hybrid` 自成一套。
- 口径漂移已让 **CLI 与 API 同参数结论对不上**（workbench 不计佣金、闸门日资金蒸发；周日历两套）。
- 顺带的静默 bug：`fraction_for` 对 ladder 无分支 → ladder 模拟盘**静默退化成朴素日投**。

## 二、目标架构

依赖方向唯一化：**`scripts/*`（CLI 薄壳）→ `src/libre_quant`（库）**；库内禁止 import scripts、禁止 sys.path 补丁。

```
src/libre_quant/
├── metrics.py     # 指标纯函数：xirr / max_dd / fee / realized_vol 家族 /
│                  #   年化常数（TRADING_DAYS_CN=244 / TRADING_DAYS=252，命名显式）
├── ledger.py      # 估值内核：Trade 事件流 → 状态折叠(units,cash,invested,fees)
│                  #   → 估值(value,pnl,avg_cost) → 汇总(flows→xirr, curve→max_dd)
├── backtest.py    # 仓位策略引擎：run(closes, signal) 与 run_positions(days,closes,pos)
│                  #   同一循环两入口 + Metrics/equity_curve/信号族/分年聚合
├── dca.py         # 现金流定投引擎：统一 simulate(plan_amount, allow, fill, fee,
│                  #   cash_policy 回调)；ladder/hybrid 的档位触发器也表达为回调
├── timing.py      # 日历与月线信号：month_series/monthly_sig/daily_positions/
│                  #   block_entries + 唯一 first_of_week / first_of_month
├── ingest.py      # 采集编排：resolve_one / ingest_one / collect_prices
├── overview.py    # 看板数据组装：build_data / _vol60（原 scripts/dashboard.py）
└── (universe.py   # +US_PROXY；shadow.py +run_daily/report；dashboard.py 仍是纯渲染)
```

职责边界一句话：**引擎只产事件与曲线，内核管记账与估值，指标层管 XIRR/回撤**。scripts 里只剩 `main()`（参数解析、打印、I/O 编排）。

## 三、权威口径表（执行期的唯一真值）

| 口径 | 决定 | 影响 |
|---|---|---|
| 资金投放佣金 | **所有引擎一律计费** `max(amount×rate, min)` | workbench 数字变化（bug 修复性质） |
| 闸门/不买日资金 | **一律进 pending/cash，不蒸发** | workbench 数字变化 |
| XIRR 现金流 | 一律"计划投入日"（含暂停日），期末 value 含现金 | 与 replay/policy 现状一致 |
| 年化交易日 | **D1 已定（2026-09-16）：双常量并存**——`TRADING_DAYS_CN=244`（A 股库层）与 `TRADING_DAYS=252`（通用/美股 scripts 侧），命名显式、按标的域选用，不强制归一 | 现有数字全部不变 |
| 每周定投"一周之首" | **D2 已定（2026-09-16）：gap≥5 版**——长假后首个交易日视为新周补投，保各策略投入等额可比；`review._first_of_week` 删除 | 长假周看板定投复盘数字微变 |
| ladder 模拟盘退化 | **D3 已定（2026-09-16）：修复**——`derive_paper_trades` 对 ladder 走 `simulate_ladder`，名实相符 | 已建 ladder 账户推演结果变化 |
| docs/04-18 历史数字 | **不回改**，本文附录记录换算说明 | — |

## 四、任务分解

> **进度（2026-09-16）**：Phase 0 ✅（690d6ba）· Phase 1 ✅（c879712）· Phase 2 ✅（34534d6）·
> Phase 3 进行中：T3.0/T3.1 ✅（3b7ec8b），T3.2–T3.5 待做 · Phase 4 待做。
> 测试基线：136 passed + 2 xfailed（workbench 蒸发、ladder 退化——T3.3/T3.4 的翻转信号）。

### Phase 0 · 基线与守卫（先于一切改动）✅

- **T0.1 引擎行为基线测试**：5 份仓位引擎 + 5 份定投引擎各跑固定合成序列，把**当前**输出固化为 golden（已知漂移逐条注释）。AC：`tests/test_engine_baseline.py` 全绿，故意改任一引擎必红。规模 M。
- **T0.2 账目守恒测试族**：每个定投引擎断言不变量——`invested == Σ计划投入`、`value == units×px + cash`、`units ≥ 0`、`Σjournal金额 == invested`。workbench 现状会 fail → 以 `xfail(strict)` 标记，Phase 3 翻转。AC：守恒测试就位。规模 M。
- **T0.3 架构守卫** `tests/test_architecture.py`：扫 `src/**/*.py`，断言无 `from scripts`、无 `sys.path.insert`。当前 6 模块违规 → 先以清单形式 xfail，Phase 1 末转硬断言。规模 S。

**Checkpoint 0**：108 + ~20 新测试全绿；未动任何生产代码。

### Phase 1 · S2 叶子下沉（src 自治，无行为变化）

- **T1.1 `metrics.py`**：迁 `xirr`/`max_dd`/`fee` + 年化常量；`test_dca_xirr` 改指库；dca/replay/policy/accounts 四处 import 翻转。S。
- **T1.2 `timing.py`**：迁月线纯函数（month_series/monthly_sig/daily_positions/block_entries）+ 周月历函数（两个 first_of_week 并存、docstring 标注 D2 待定）。M。
- **T1.3 `ingest.py`**：迁 resolve_one/ingest_one/collect_prices；api/jobs/scripts-ingest 翻转 import。M。
- **T1.4 杂项**：US_PROXY→universe.py；shadow.run_daily/report→shadow.py；build_data/_vol60→overview.py；api/review/jobs/dashboard 翻转。M。
- **T1.5 收口**：删 6 个库模块的 sys.path 补丁；T0.3 转硬断言；`grep -r "from scripts" src/` 为空。S。

**Checkpoint 1**：全绿；`uv run python -c "import libre_quant.api"` 在任意 cwd 可用（含 `/tmp` 下验证）；Docker 形态冒烟。

### Phase 2 · S1 估值内核（ledger.py）

- **T2.1 内核**：以 `value_trades` 为基础泛化为 ledger（状态折叠 + 估值 + 汇总三层），原函数保留为薄包装（API 兼容）。AC：新增 `tests/test_ledger.py`（守恒 + 边界：空流/只有卖出/as_of 截断）。M。
- **T2.2 迁移消费方**：api 实盘 inline（~L892-906）→ 转 Trade 流走 ledger，**顺带修实盘"今日盈亏"把当日新存本金算成收益**（contrib 只在 paper 分支，~L729）；workbench/run_history 尾部、dashboard._arm_summary 改调 ledger。M。
- **T2.3 三胞胎合一**：`replay._summary`/`policy.summarize`/`dca.simulate` 汇总段 → `ledger.summarize`。S-M。

**Checkpoint 2**：交叉一致性测试——同一合成 Trade 流经 3 个入口（value_trades / api 路径 / ledger）得到**逐位相等**的 value/pnl/xirr。

### Phase 3 · S3 引擎统一（风险最高，小步走）

- **T3.0 口径拍板落地** ✅（3b7ec8b）：D2 落地——`review._first_of_week` 与
  scripts/dca 旧闭包删除，全站 `timing.first_of_week`（gap≥5 权威口径）。
- **T3.1 仓位引擎** ✅（3b7ec8b）：`backtest.positions_of / daily_returns /
  run_positions / yearly_from_daily` 单实现；review._positions_daily 删除；
  qdii_pricing.run_blocked（adjust 钩子）/ attribution.simulate（复用仓位生成）/
  multi_asset_review / backtest.main 分年度内联全部改调库；
  顺带修 backtest.main 分年度 i=0 负下标回绕 bug。
- **T3.2 定投引擎** ✅（598fc4b）：`dca.run_cashflow` 统一现金流引擎
  （deposit/spendable/fill/mark/sell 回调；spendable 收 `(t,d,cash,sold)`，
  policy 按现金定比例、workbench 卖出日不追买都表达得出来）；
  dca.simulate / replay._run_arm / policy.run_policy 全部改为配置表达，
  golden 逐位不变。
- **T3.3 workbench 接入 + 修 bug** ✅（598fc4b）：run_history 改走引擎，
  计佣金（新增 fees 字段，api 传用户费率）、闸门资金进 cash；
  invested 6800→8000，与 dca 家族四引擎数字完全一致（S3 漂移归零，
  before/after 见附录 A）；卖出日不追买（旧 elif 链语义保留）。
- **T3.4 ladder 修复** ✅（598fc4b，D3）：derive_paper_trades 对 ladder
  走 simulate_ladder 真网格；api 模拟盘现金口径补卖出净额回笼。
- **T3.5 CLI↔API 对账报告** ✅：见附录 A/B（合成场景五入口逐位一致已由
  `test_dca_family_golden` 固化；真实数据 CLI↔API 由"同一引擎函数"结构性保证）。

**Checkpoint 3** ✅：golden 全部更新为新口径，138 passed + 0 xfailed
（workbench 蒸发 / ladder 退化两个翻转信号清零）。

### Phase 4 · 清理与固化

- **T4.1** ✅：dca.py "+10.32%" 过期输出已删（c879712）；strategy_lab 硬编码
  基准数改为从数据打印、内联回撤换 ledger（本批）；README 项目结构段重写（本批）。
- **T4.2** ✅：迁移映射表（附录 D）、年化常数说明（附录 C）、对账报告（附录 A/B）。
- **T4.3（可选加购）**：`/api/analysis` 无净值 500、`ingest.run()` 单标的失败
  隔离——评审遗留 Required，独立小修，未纳入本轮（已在评审文档登记）。

## 五、风险与缓解

| 风险 | 影响 | 缓解 |
|---|---|---|
| 口径统一改变用户可见数字（策略台 XIRR/回撤） | 高 | 集中在 T3.3 一次提交 + before/after 表；这恰是欠债的本息 |
| 历史 docs（252 年化）与在线值不一致 | 中 | 不回改历史 docs，附录统一解释 |
| 引擎统一引入静默算错 | 高 | Phase 0 守恒/golden 先行；每任务小步；parity 逐入口对账 |
| scripts CLI 输出格式被 docs 引用 | 中 | 只动实现不动 stdout 格式 |
| 阶段穿插导致中途不可交付 | 中 | 每个 Checkpoint 都是可提交、可部署状态 |

## 六、验收定义（全部完成的标准）

1. `src/libre_quant` 零 `from scripts`、零 `sys.path` 补丁（test_architecture 硬断言）。
2. 估值路径唯一：任何入口的 units/value/pnl/xirr 都出自 ledger（交叉一致性测试在库）。
3. 仓位引擎 1 份、定投引擎 1 份；known 漂移清单清零或逐条有解释。
4. 全测试绿（预计 130+），Docker 形态冒烟通过，CLI 输出格式未变。
5. 本文 §三 口径表、迁移映射、对账报告齐备。

## 附录 A · 策略台（workbench）口径修正 before/after

场景：40 交易日合成序列（锯齿+缓跌），daily 200，溢价闸门暂停 6 天，
佣金 万0.5 / 最低 0.1 元（`tests/test_engine_baseline.py` 固化）。

| 指标 | 旧（蒸发口径） | 新（统一口径） | 差异原因 |
|---|---:|---:|---|
| invested | 6,800.00 | **8,000.00** | 旧版 6 个闸门日每天 200 元既不算投入也不进现金（凭空消失） |
| value | 4,975.85 | **5,757.09** | 消失的钱 + 恢复日连本带额补投的份额回来了 |
| profit | −1,824.15 | **−2,242.91** | 投入口径修正（亏损额变大是因为投入原本就被少算） |
| max_dd | 38.67% | **36.87%** | 回撤不再由"资金蒸发"放大 |
| units | 563.52 | **651.99** | 补投产生真实份额 |
| fees | （无此字段） | **3.40** | 旧版全程零佣金；新版所有买卖一律计费（口径表 #1） |

修正后与同场景的 dca.simulate / replay._run_arm / policy.run_policy /
paper value_trades **逐位一致**——策略台（/api/workbench）、复盘页
（/api/replay、scripts/dca.py）、政策台（strategy_lab）从此同一答案。

## 附录 B · 跨入口 parity 对账（S3 验收）

同一合成场景五入口数字（`test_dca_family_golden` + `test_dca_review…` 固化）：

| 入口 | invested | value | fees | buys/pauses |
|---|---:|---:|---:|---|
| scripts/dca.py（CLI） | 8,000.0 | 5,757.0867 | 3.4 | 34 / 6 |
| /api/replay（replay._run_arm） | 8,000.0 | 5,757.0867 | 3.4 | 34 / 6 |
| policy.run_policy | 8,000.0 | 5,757.0867 | 3.4 | 34 / 6 |
| 模拟盘 value_trades | 8,000.0 | 5,757.0867 | 3.4 | 34 / 6 |
| /api/workbench（run_history，修正后） | 8,000.0 | 5,757.0867 | 3.4 | 34 / 6 |

真实数据的 CLI↔API 一致性不再依赖对账，而是**结构性保证**：Phase 1 下沉后
CLI 与 API 调用同一个 `libre_quant` 引擎函数，全仓无第二份实现；
周日历唯一（D2：`timing.first_of_week`），长假周不再分歧。
仓位策略引擎同理：`backtest.run` / `run_positions` / qdii 禁买 / attribution
共享同一仓位与收益循环（`test_run_positions_is_the_single_position_loop`）。

已知且有意保留的差异：`strategy_lab.run_position_managed`（波动率控仓 +
止盈武装语义独特）暂保留独立实现，指标取自 metrics/ledger，待后续需要时
再迁移到 run_cashflow。

## 附录 C · 年化常数（D1：双常量并存）

| 常量 | 值 | 用途 | 影响 |
|---|---:|---|---|
| `metrics.TRADING_DAYS` | 252 | 回测指标年化（backtest.Metrics 的 cagr/sharpe） | docs/04~18 历史数字按此计算，保持不变 |
| `metrics.TRADING_DAYS_CN` | 244 | A 股波动率年化（accounts/policy/review/workbench 的 vol 与 σ_day） | 库层现状口径，保持不变 |

不强制归一（拍板记录）：两常数作用于不同指标，等比缩放不改任何排序结论；
强行统一会让历史 docs 数字与在线看板出现无意义漂移。

## 附录 D · 迁移映射表（旧 → 新）

| 旧位置 | 新位置 | 备注 |
|---|---|---|
| `scripts/dca.py: xirr/max_dd/fee` | `libre_quant/metrics.py` | scripts 侧兼容再出口 |
| `scripts/dca.py: simulate` | `libre_quant/dca.py: simulate` | T3.2 起内部走 run_cashflow |
| `scripts/backtest.py: 引擎/信号/Metrics` | `libre_quant/backtest.py` | CLI 只留 STRATS/parser/main |
| `scripts/monthly_ma.py: 月线信号族` | `libre_quant/timing.py` | |
| `scripts/monthly_ma.py: run_positions` | `libre_quant/backtest.py: run_positions` | timing 兼容再出口；返回 (Metrics, 净值曲线) |
| `scripts/ingest.py: collect/resolve/ingest_one/run` | `libre_quant/ingest.py` | jobs/api 直调库 |
| `scripts/dashboard.py: build_data/_vol60` | `libre_quant/overview.py` | |
| `scripts/shadow.py: run_daily/report/PROMO_*` | `libre_quant/shadow.py` | |
| `scripts/qdii_pricing.py: US_PROXY` | `libre_quant/universe.py` | |
| `review._positions_daily` | （删除） | `backtest.daily_returns` |
| `review._first_of_week/_first_of_month` | `timing.first_of_week/first_of_month` | D2 权威口径 |
| `accounts.value_trades`（实现） | `libre_quant/ledger.valuation_summary` | 原签名薄包装保留 |
| `replay._summary` / `policy.summarize` 的 xirr/dd | `ledger.xirr_or_none` / `ledger.drawdown` | |
| `backtest.main` 分年度内联（第三遍循环） | `backtest.yearly_from_daily` | 顺带修 i=0 负下标回绕 |
| api 实盘持仓 inline | `accounts.value_trades`（ledger） | 补 fees；修当日本金算盈亏 |
