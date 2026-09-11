# libre_quant

个人量化研究。当前载体：**515880 通信ETF（国泰基金）**。

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
```

Tavily 检索需要 token，放 `.env`：

```bash
echo 'TAVILY_TOKEN=<your-token>' > .env   # 已进 .gitignore
uv run python -m libre_quant.data.news "光模块 800G 最新进展"
```

## 项目结构

```
libre_quant/
├── docs/
│   ├── 01-conversation-log.md   研究过程与决策记录
│   ├── 02-data-sources.md       数据源技术档案（全部实测）
│   └── 03-methodology.md        因子验证方法论
├── scripts/
│   ├── p0_spike.py              PCF 可得性验收
│   ├── p0_weights.py            PCF + 价格 → 精确权重
│   └── us_lead_test.py          gap/intra 分离检验（可复用）
├── src/libre_quant/data/
│   ├── pcf.py                   PCF 抓取 + 双格式解析
│   ├── quotes.py                A 股行情（腾讯）
│   ├── us.py                    美股行情（腾讯 + 新浪）
│   └── news.py                  Tavily 检索（MCP）
└── tests/fixtures/              离线 PCF 样本（两代格式）
```

## 数据源一览

| 用途 | 源 | 说明 |
|---|---|---|
| **PCF 持仓明细** | `m.gtfund.com/cochin/etf/download/...` | 核心数据，含每只股数 |
| A 股行情 | `qt.gtimg.cn`（批量）/ `web.ifzq.gtimg.cn`（历史） | 一次请求多只，避免限流 |
| 美股行情 | `qt.gtimg.cn`（批量）/ 新浪 `getDailyK`（1999 起） | — |
| 新闻检索 | Tavily MCP | ⚠️ 无历史快照，**无法回测** |

细节与坑见 [`docs/02-data-sources.md`](docs/02-data-sources.md)。

## 待办

1. **成分股日间调仓 diff** —— PCF 独有信息，最可能真有 edge
2. **候选因子批量过筛** —— 宽度、分化度、份额变化、溢价率、相对强度
3. **记录/归因系统** —— 调策略的前提设施
4. **Tavily 每日归档** —— 从今天起攒可回测数据

## 边界声明

- 本项目是**个人研究**，非投资建议
- 光模块/CPO 是**事件驱动 + 主题投资**，日内 ±8% 常见；
  适合**趋势跟踪 + 波动率定仓**，不适合精细多因子择时
- 单标的、~1500 根日线样本，**精调参数必然过拟合**
