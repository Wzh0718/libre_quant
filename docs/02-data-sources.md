# 数据源技术档案

> 全部结论均为 **2026-09-11 在本机实测**，非文档推断。失效时请重新探测。

## 目录
- [1. PCF 申赎清单（核心）](#1-pcf-申赎清单核心)
- [2. A 股行情](#2-a-股行情腾讯)
- [3. 美股行情](#3-美股行情)
- [4. Tavily 检索](#4-tavily-检索mcp)
- [5. 已验证不可用的源](#5-已验证不可用的源)
- [6. 网络环境注意事项](#6-网络环境注意事项)

---

## 1. PCF 申赎清单（核心）

### 端点

```
https://m.gtfund.com/cochin/etf/download/{fund_code}/{yyyymmdd}
```

- **404 = 非交易日**，不是错误。不要当异常处理。
- 覆盖 **2019-12 至今所有交易日**（515880 成立于 2019-08-16）
- 无需登录、无签名、无加密参数

### 反爬：CDN cookie 挑战

裸请求会陷入 302 循环，响应头特征：

```
Ws-Action: cc
Set-Cookie: C3VK=<...>; Max-Age=300
Location: <原 URL>
```

**解法**：用 `requests.Session`（自带 cookie jar）即可自动通过。
curl 需要 `-c/-b` cookie 文件。

### 两代格式

| 时期 | 格式 | 编码 | 标记 |
|---|---|---|---|
| ~2025-11 及以前 | INI 头 + `TAGTAG` + 竖线分隔 | **GBK** | `[ETF]` |
| 2025-12 起 | XML | UTF-8 | `<SSEPortfolioCompositionFile>` |

实测边界：`2025-11-17` = legacy，`2025-12-01` = xml。

**legacy 正文行格式**：

```
000063|中兴通讯|    3000|3|0.10000|  120750.000|
      代码    名称    数量  flag  溢价率   现金替代金额
```

### XML 字段

```xml
<SSEPortfolioCompositionFile>
  <FundInstrumentID>515880</FundInstrumentID>
  <CreationRedemptionUnit>2000000</CreationRedemptionUnit>   <!-- 最小申赎单位 -->
  <TradingDay>20260910</TradingDay>
  <PreTradingDay>20260909</PreTradingDay>
  <NAVperCU>1353042.45</NAVperCU>                            <!-- 一篮子净值 -->
  <NAV>0.6765</NAV>
  <EstimatedCashComponent>1889.45</EstimatedCashComponent>
  <MaxCashRatio>0.5</MaxCashRatio>
  <RecordNumber>50</RecordNumber>
  <ComponentList>
    <Component>
      <InstrumentID>000063</InstrumentID>
      <InstrumentName>中兴通讯</InstrumentName>
      <Quantity>1900</Quantity>                              <!-- ★ 持仓股数 -->
      <SubstitutionFlag>1</SubstitutionFlag>
      <CreationPremiumRate>...</CreationPremiumRate>
      <RedemptionDiscountRate>...</RedemptionDiscountRate>
      <SubstitutionCashAmount>...</SubstitutionCashAmount>
      <UnderlyingSecurityID>102</UnderlyingSecurityID>        <!-- 101=沪 102=深 -->
    </Component>
    ...
  </ComponentList>
</SSEPortfolioCompositionFile>
```

### ⚠️ 陷阱一：现金替代占位腿

**`RecordNumber` ≠ 实际持仓数。**

515880 @2026-09-10：`RecordNumber=50`，**实际持有 43 只**。

| 分组 | 数量 | 含义 |
|---|---|---|
| `flag=1` + 有数量 + **无** cash | 16（全沪市） | 禁止现金替代，实物交付 |
| `flag=1` + 有数量 + 有 cash | 27（全深市） | 可以现金替代 |
| **`flag=2` + 数量=0** | 7 | **基金未持有，必须剔除** |

**不剔除会让权重算错。** `libre_quant.data.pcf` 已用 `Component.is_held` 处理。

### 陷阱二：`SubstitutionCashAmount` 只在深市票上出现

沪市票（实物交付）不给这个字段 → `implied_price` 为 `None`。
**所以权重计算必须外接真实价格**，不能只靠隐含价，否则会静默丢掉 37% 的腿。

### 权重算法

```
weight_i = Quantity_i × Price_i / NAVperCU
```

分母用 `NAVperCU`（最准）。权重合计应略小于 100%（余量是现金）。

---

## 2. A 股行情（腾讯）

### 批量实时（推荐）

```
https://qt.gtimg.cn/q=sh600487,sz300502,sh601138,...
```

- **一次请求多只** —— 43 只 = 1 次请求，**从根上避免限流**
- GBK 编码，`~` 分隔
- 字段：`市场~名称~代码~现价~昨收~今开~成交量~...`

### 历史 K 线

```
https://web.ifzq.gtimg.cn/appstock/app/fqkline/get?param=sh600487,day,{start},{end},640,qfq
```

返回 JSON，每根：`[日期, 开, 收, 高, 低, 量]`

### 代码 → 符号映射

| 前缀 | 市场 |
|---|---|
| `6`、`5` | `sh`（6=股票，**5=沪市 ETF**） |
| `0`、`3`、`1` | `sz`（0/3=股票，**1=深市 ETF**） |
| `4`、`8` | `bj`（北交所） |

⚠️ 容易漏掉 `5xxxxx` —— 515880 会因此报错。

---

## 3. 美股行情

### 实时批量（腾讯）

```
https://qt.gtimg.cn/q=usNVDA,usAVGO,usMRVL,usCOHR,usLITE,usAAOI,usTSM
```

- 代码格式 `us` + 大写代码
- 需 `Referer: https://gu.qq.com/`
- 字段：`市场~名称~代码~现价~昨收~今开~...~时间~涨跌额~涨跌幅`
- ⚠️ 指数（SOX）**不支持**

### 实时（新浪）

```
https://hq.sinajs.cn/list=gb_nvda
https://hq.sinajs.cn/list=gb_$sox      ← 指数用 $ 前缀
```

需 `Referer: https://finance.sina.com.cn/`，GBK 编码。

### 完整历史（新浪）★ 主力

```
https://stock.finance.sina.com.cn/usstock/api/json_v2.php/US_MinKService.getDailyK?symbol=nvda
```

返回 JSON 数组：`{"d":"1999-01-22","o":...,"h":...,"l":...,"c":...,"v":...,"a":...}`

实测覆盖：

| 代码 | 起始 | 响应大小 |
|---|---|---|
| nvda | 1999-01-22 | 546 KB |
| tsm | 2001-01-02 | 547 KB |
| mrvl | 2000-06-30 | 498 KB |
| lite | 2015-08-04 | 266 KB |
| aaoi | 2013-09-27 | 300 KB |
| avgo | 2016-02-01 | 269 KB |
| cohr | 2023-02-23 | 87 KB |

### 时间对齐（做隔夜映射的前提）

```
美股交易日 D 收盘 16:00 EDT  ==  北京时间 D+1 凌晨 04:00/05:00
A 股     D+1 开盘 09:30
```

**映射关系**：`US_ret[D] → A_ret[D+1]`，美股信息在 A 股开盘前已完全可知，**无前视偏差**。

实现：对 A 股交易日 T，取**严格早于 T 的最近一个美股交易日**。

---

## 4. Tavily 检索（MCP）

### 端点与传输

```
POST https://tavily.ivanli.cc/mcp
Authorization: Bearer <token>
Content-Type: application/json
Accept: application/json, text/event-stream
```

**MCP Streamable HTTP** 传输。响应是 `text/event-stream`。

启动时返回 `mcp-session-id` 响应头，后续请求需带上。

会话流程：`initialize` → `notifications/initialized` → `tools/call`。

### 工具

`tavily_search` / `tavily_extract` / `tavily_crawl` / `tavily_map` / `tavily_research`
（服务器：`tavily-mcp v0.2.0`）

### ⚠️ 两个实现坑

1. **编码**：响应头**不声明 charset**，`requests` 会退化成 latin-1，中文全乱码。
   必须 `resp.content.decode("utf-8")`。
2. **SSE 多行 data**：检索结果长时服务端会把 data 跨多行切分，
   需按 `\n` 拼接后再 `json.loads`。

### 硬限制

**检索是"当下"的，拿不到历史新闻快照 → 无法回测。**
只能从今天起每天落盘，积累 3~6 个月后才能检验。

### DSH 集成

DSH 的 `web-search-deepseek` 插件**调不通这个端点** ——
它期望 Anthropic 兼容的 Messages API（拼 `/messages`），协议不匹配。

正确做法是加一个 MCP 客户端（`dsh-mcp-client` 支持 `streamable-http`）：

```yaml
- insert:
    - id: mcp-tavily
      name: '@deepseek-ai/dsh-mcp-client'
      config:
        serverName: tavily
        transport: streamable-http
        url: https://tavily.ivanli.cc/mcp
        headers:
          Authorization: !!js '`Bearer ${process.env.TAVILY_TOKEN}`'
```

---

## 5. 已验证不可用的源

| 源 | 结果 | 原因 |
|---|---|---|
| `query1.finance.yahoo.com/v8/finance/chart` | ❌ 403 | 需 crumb/cookie |
| `stooq.com/q/d/l/` | ❌ | JS 挑战 |
| `push2his.eastmoney.com` | ⚠️ 限流 | ~130 次批量请求后连接重置，冷却 >25s 未恢复 |
| AKShare 的 PCF 接口 | ❌ 不存在 | 只有季报前十大 + ETF 份额，**无每日持仓明细** |

---

## 6. 网络环境注意事项

- 本机 HTTP 代理 `127.0.0.1:7890` 对**行情域名不稳定**（`RemoteDisconnected`）
- 所有行情客户端应设 **`session.trust_env = False`**（禁用代理）
- `m.gtfund.com`、`qt.gtimg.cn`、`hq.sinajs.cn` 直连稳定
- 沙箱内 `~/.cache` 只读 → 跑 uv 需 `UV_CACHE_DIR=/tmp/uv-cache`
