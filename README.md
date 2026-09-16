# libre_quant

个人量化研究。投资宇宙：**515880 通信ETF（国泰）+ 513500 标普500 / 513100 纳指100（QDII ETF）**。
515880 是目前唯一完成深度研究的标的；宇宙扩展计划见 [`docs/06-multi-asset-plan.md`](docs/06-multi-asset-plan.md)。

## 这个项目在做什么

ETF 是加权篮子。**看 ETF 自己的 K 线是在看结果，看成分股才是看原因。**
而最精确的成分股视图来自每日 **PCF 申购赎回清单** —— 它给的是**每只股票的实际持股数量**，
不是指数公司公布的权重百分比。

```
ETF (515880)
  └─ 每日 PCF 申赎清单 → 成分股 + 每只股数（真实持仓）
       └─ 外接真实收盘价 → 精确权重
            └─ 归因 / 调仓追踪 / 资金流
```

本仓库当前完成的是**数据层**与**一次假设的实证检验**。

## 已验证的结论

### ✅ PCF 通路可用，历史完整

```
https://m.gtfund.com/cochin/etf/download/{fund_code}/{yyyymmdd}
```

- 覆盖 **2019-12 至今所有交易日**，404 = 非交易日
- 两代格式（GBK legacy / UTF-8 XML）均已支持
- 精度与东财季报交叉验证：前十大合计 **71.51% vs 71.77%**，差 0.26pp

### ✅ 发现并处理了「现金替代占位腿」陷阱

`RecordNumber=50` 但**实际只持有 43 只**。其余 7 只是"必须现金替代"占位腿，
基金并未持有 —— **不剔除会让权重算错**。

### ❌ 隔夜美股映射：实证否决

曾假设美股（COHR / LITE / NVDA）是这个标的最强的领先指标。检验结果：

| | r |
|---|---:|
| 与**开盘缺口**的相关性 | **0.40** |
| 与**日内收益**的相关性 | **0.02** |
| 与 1/3/5/10/20 日收益 | **≈ 0** |

**美股信息强传导到开盘，但开盘后完全定价，零 alpha。**

推论：连业务直接对标、时间明确领先的数据都无预测力，
**「用公开信息预测这个标的的日线收益」这条路基本是关的。**

详见 [`docs/03-methodology.md`](docs/03-methodology.md)。

## 快速开始

```bash
export UV_CACHE_DIR=/tmp/uv-cache   # 沙箱内 ~/.cache 只读
uv sync

# PCF 可得性验收（需联网）
uv run python scripts/p0_spike.py

# PCF + 真实价格 → 精确权重（需联网）
uv run python scripts/p0_weights.py

# 隔夜美股领先性检验（需联网）
uv run python scripts/us_lead_test.py

# PCF 解析器离线自测
uv run pytest -q

# 全标的采集冒烟（不连数据库）
uv run python scripts/ingest.py --dry-run

# 入库（首次/手动一轮）
uv run python scripts/serve.py --once

# 常驻采集服务（容器/Komodo 形态，内嵌 APScheduler 替代 cron）
uv run python scripts/serve.py --catchup

# 可视化看板（FastAPI + Vue3/ECharts，生产形态单端口）
# 六页：今日决策（推理链+盘中实时）/ 深度分析（溢价分桶+场外因素分解）/
#       复盘模拟（逐日流水账）/ 历史复盘（策略对比+定投）/ 影子盘 /
#       我的盘（选标的+选方案开盘｜实际盘录入成交｜未来 3 天预案）
# 顶栏「标的检索」：输入任意基金/股票代码 → 自动拉历史入库 → 全站可跑盘
uv run python scripts/api.py --port 8321            # http://localhost:8321

# 前端开发（Vite 热更新，/api 代理到 8321）
cd frontend && pnpm install && pnpm dev

# 静态 HTML 兜底（无框架单文件；serve.py 每日也会重建到 web/）
uv run python scripts/dashboard.py
```

配置（PostgreSQL / Tavily 等）用 pydantic-settings 统一管理：

```bash
cp .env.example .env   # 然后填写；.env 已进 .gitignore
uv run python -m libre_quant.data.news "光模块 800G 最新进展"   # 需 TAVILY_TOKEN
```

## 项目结构

```
libre_quant/
├── docs/
│   ├── 00-strategy.md           策略总纲（一页纸，全部规则的权威摘要）
│   ├── 01~18                    研究过程：方法论/回测/归因/QDII/定投/
│   │                            影子盘/分解复盘/策略优化/标的检索/
│   │                            我的盘/价格触发/溢价趋势/信号扫描/价格驱动
│   └── 19-structural-debt.md    结构性欠债清偿（估值内核/引擎统一/口径表）
├── scripts/                      # 研究与运维 CLI（薄壳：参数解析+打印；
│   │                            # 引擎与指标全部在 src/libre_quant）
│   ├── p0_spike.py              PCF 可得性验收
│   ├── p0_weights.py            PCF + 价格 → 精确权重
│   ├── us_lead_test.py          gap/intra 分离检验（可复用）
│   ├── backtest.py              策略回测（--etf 515880/513500/513100）
│   ├── attribution.py           精确收益归因（对数空间精确分解）
│   ├── multi_asset_review.py    Phase 2a 三标的趋势复检
│   ├── qdii_pricing.py          Phase 2b/2c QDII 定价与溢价研究
│   ├── monthly_ma.py            5月线择时复检（--n 可调）
│   ├── dca.py                   定投复检（频率/溢价暂停/佣金敏感性）
│   ├── shadow.py                影子盘：每日步进 + 晋升检查单（--report）
│   ├── signal_scan.py           信号扫描：候选变量对前向收益的预测力对比
│   ├── strategy_lab.py          投放/持仓管理变体实验台
│   ├── dashboard.py             静态看板兜底（数据组装在库层 overview）
│   ├── api.py                   FastAPI + 看板托管入口（--with-scheduler 单容器形态）
│   ├── ingest.py                数据采集入库（--dry-run 冒烟 / --init-db）
│   └── serve.py                 常驻采集服务（APScheduler 容器形态）
└── src/libre_quant/              # 库层（可 pip install；scripts 单向依赖它）
    ├── config.py                pydantic-settings 集中配置（.env）
    ├── universe.py              投资宇宙注册表 + QDII→美股代理映射
    ├── store.py                 PostgreSQL 存储层 + 溢价写入时配对
    ├── metrics.py               指标原语：xirr / max_dd / fee / 年化常数
    ├── ledger.py                估值内核：记账/估值/XIRR 出口/回撤（唯一实现）
    ├── backtest.py              仓位回测引擎 + 策略信号族 + Metrics
    ├── dca.py                   定投统一引擎 run_cashflow + 研究口径 simulate
    ├── timing.py                日历（周/月之首，gap≥5 口径）+ 月线信号族
    ├── ingest.py                采集编排（ingest_one/resolve_one）
    ├── overview.py              看板数据组装（API 与静态看板共用）
    ├── review.py / replay.py / policy.py / workbench.py / accounts.py
    │                            分析层：策略复盘/逐日重放/政策投放/策略台/我的盘
    ├── shadow.py                影子盘纯逻辑 + 每日步进/报告
    ├── decomp.py                场外因素分解（标的/汇率/费用/溢价）
    └── data/
        ├── pcf.py               PCF 抓取 + 双格式解析
        ├── quotes.py            A 股行情（腾讯，分页全历史）
        ├── nav.py               基金净值（东财，全历史，+08:00 固定时区）
        ├── us.py / macro.py / news.py / discover.py
        │                        美股行情 / 宏观日线 / Tavily 检索 / 表外标的探测
└── tests/                       离线自测：解析器/配置/引擎 golden 基线/
                                 账目守恒/架构守卫（src 禁反向依赖 scripts）
```

## 数据源一览

| 用途 | 源 | 说明 |
|---|---|---|
| **PCF 持仓明细** | `m.gtfund.com/cochin/etf/download/...` | 核心数据，含每只股数 |
| A 股行情 | `qt.gtimg.cn`（批量）/ `web.ifzq.gtimg.cn`（历史） | 一次请求多只，避免限流 |
| **基金净值** | `fund.eastmoney.com/pingzhongdata/{code}.js` | 三 ETF 全历史；溢价 = 价格÷最近已公布净值 |
| 美股行情 | `qt.gtimg.cn`（批量）/ 新浪 `getDailyK`（2001 起） | SPY/QQQ 代理标普/纳指 |
| 新闻检索 | Tavily MCP | ⚠️ 无历史快照，**无法回测** |

细节与坑见 [`docs/02-data-sources.md`](docs/02-data-sources.md)。

## 待办

→ 已按多标的宇宙重排至 [`docs/06-multi-asset-plan.md`](docs/06-multi-asset-plan.md)
（Phase 0 架构去单标的化 → 数据资产 → 策略复检 → 组合层 → walk-forward）。

## 边界声明

- 本项目是**个人研究**，非投资建议
- 光模块/CPO 是**事件驱动 + 主题投资**，日内 ±8% 常见；
  适合**趋势跟踪 + 波动率定仓**，不适合精细多因子择时
- 单标的、~1500 根日线样本，**精调参数必然过拟合**
