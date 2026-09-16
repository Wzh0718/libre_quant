<script setup lang="ts">
import { onMounted, ref } from "vue";
import {
  addTrade, createAccount, deleteAccount, fetchAccounts, fetchAttribution,
  fetchOutlook, fetchPlans, fmtPct, fmtYuan,
  type AccountRow, type AttributionData, type OutlookData, type PlanItem,
} from "../api";
import { assets, refreshAssets } from "../store";

// ---- 开盘表单
const plans = ref<PlanItem[]>([]);
const form = ref({
  code: "159941", plan: "gate", kind: "paper", name: "",
  start_day: "", daily: undefined as number | undefined,
  gate: 5,
});
const busy = ref(false);
const err = ref<string | null>(null);
const msg = ref<string | null>(null);

// ---- 实际盘录入
const tradeForm = ref({ aid: 0, day: "", price: 0, qty: 100 });

// ---- 预案
const outlook = ref<OutlookData | null>(null);
const outlookFor = ref<number | null>(null);

// ---- 当日红绿归因
const attrib = ref<AttributionData | null>(null);
const attribFor = ref<number | null>(null);

const rows = ref<AccountRow[]>([]);

async function load() {
  try {
    const [p, a] = await Promise.all([fetchPlans(), fetchAccounts()]);
    plans.value = p.items;
    rows.value = a.items;
  } catch (e) {
    err.value = e instanceof Error ? e.message : String(e);
  }
}

async function open() {
  busy.value = true;
  err.value = null;
  msg.value = null;
  try {
    const r = await createAccount({
      code: form.value.code.trim(),
      plan: form.value.plan,
      kind: form.value.kind,
      name: form.value.name || undefined,
      start_day: form.value.start_day || undefined,
      daily: form.value.daily,
      gate: form.value.gate / 100,
    });
    msg.value = `已开盘 #${r.id}（${form.value.kind === "paper" ? "模拟盘" : "实际盘"}）`;
    await load();
    if (form.value.kind === "real") {
      tradeForm.value.aid = r.id;
      tradeForm.value.day = new Date().toISOString().slice(0, 10);
    }
  } catch (e) {
    err.value = e instanceof Error ? e.message : String(e);
  } finally {
    busy.value = false;
  }
}

async function submitTrade() {
  err.value = null;
  try {
    await addTrade(tradeForm.value.aid, {
      day: tradeForm.value.day,
      price: tradeForm.value.price,
      qty: tradeForm.value.qty,
    });
    msg.value = "成交已录入";
    await load();
  } catch (e) {
    err.value = e instanceof Error ? e.message : String(e);
  }
}

async function showOutlook(aid: number) {
  outlookFor.value = aid;
  outlook.value = null;
  try {
    outlook.value = await fetchOutlook(aid, 3);
  } catch (e) {
    err.value = e instanceof Error ? e.message : String(e);
  }
}

async function showAttrib(aid: number) {
  attribFor.value = attribFor.value === aid ? null : aid;
  attrib.value = null;
  if (attribFor.value === null) return;
  try {
    attrib.value = await fetchAttribution(aid, 30);
  } catch (e) {
    err.value = e instanceof Error ? e.message : String(e);
  }
}

const signedYuan = (v: number | null | undefined) =>
  v == null ? "—" : `${v >= 0 ? "+" : ""}${fmtYuan(v)}`;

async function remove(aid: number) {
  await deleteAccount(aid);
  if (outlookFor.value === aid) { outlook.value = null; outlookFor.value = null; }
  if (attribFor.value === aid) { attrib.value = null; attribFor.value = null; }
  await load();
}

onMounted(async () => { await refreshAssets(); await load(); });
</script>

<template>
  <h2>开盘（选标的 → 选方案 → 设定投逻辑）</h2>
  <div class="card">
    <div class="grid" style="grid-template-columns:repeat(auto-fit,minmax(180px,1fr))">
      <label class="muted">标的（股票/ETF 代码）
        <input v-model="form.code" list="known-codes-2"
               style="width:100%;background:var(--bg);color:var(--text);
                      border:1px solid var(--border);border-radius:6px;padding:6px" />
        <datalist id="known-codes-2">
          <option v-for="a in assets" :key="a.code" :value="a.code">{{ a.name }}</option>
        </datalist>
      </label>
      <label class="muted">方案
        <select v-model="form.plan" style="width:100%;background:var(--bg);color:var(--text);
                border:1px solid var(--border);border-radius:6px;padding:6px">
          <option v-for="p in plans" :key="p.plan" :value="p.plan">{{ p.name }}</option>
        </select>
      </label>
      <label class="muted">类型
        <select v-model="form.kind" style="width:100%;background:var(--bg);color:var(--text);
                border:1px solid var(--border);border-radius:6px;padding:6px">
          <option value="paper">模拟盘</option>
          <option value="real">实际盘</option>
        </select>
      </label>
      <label class="muted">每日金额（元，你填）
        <input v-model.number="form.daily" type="number" step="50" placeholder="例如 200"
               style="width:100%;background:var(--bg);color:var(--text);
                      border:1px solid var(--border);border-radius:6px;padding:6px" />
      </label>
      <label class="muted">溢价闸门 %
        <input v-model.number="form.gate" type="number" step="0.5"
               style="width:100%;background:var(--bg);color:var(--text);
                      border:1px solid var(--border);border-radius:6px;padding:6px" />
      </label>
      <label class="muted">起始日（留空=今天）
        <input v-model="form.start_day" type="date"
               style="width:100%;background:var(--bg);color:var(--text);
                      border:1px solid var(--border);border-radius:6px;padding:6px" />
      </label>
    </div>
    <div class="muted" style="margin-top:8px;font-size:12px">
      {{ plans.find(p => p.plan === form.plan)?.desc ?? "" }} · 只支持场内标的（有交易所行情）
    </div>
    <div style="margin-top:12px">
      <button class="badge badge-buy" style="cursor:pointer" :disabled="busy" @click="open">
        {{ busy ? "处理中…" : "开盘" }}
      </button>
      <span v-if="msg" class="muted" style="margin-left:12px">✅ {{ msg }}</span>
      <span v-if="err" style="margin-left:12px;color:var(--red)">⚠️ {{ err }}</span>
    </div>
  </div>

  <h2>盘口列表</h2>
  <div v-for="a in rows" :key="a.id" class="card" style="margin-bottom:12px">
    <div style="display:flex;gap:12px;align-items:center;flex-wrap:wrap">
      <strong>#{{ a.id }} {{ a.name }}</strong>
      <span class="badge" :class="a.kind === 'paper' ? 'badge-hold' : 'badge-buy'">
        {{ a.kind === "paper" ? "模拟盘" : "实际盘" }}
      </span>
      <span class="muted">{{ a.code }} · {{ a.plan_label }} · 起 {{ a.start_day }} ·
        日投 {{ fmtYuan(a.params.daily ?? 0) }} 元
        {{ a.params.gate != null ? ` · 闸门 ${fmtPct(a.params.gate, 1, false)}` : "" }}</span>
    </div>
    <div v-if="!a.empty" class="grid cards" style="margin-top:12px">
      <div>
        <div class="muted">累计涨跌幅（相对投入）</div>
        <div class="big num"
             :style="(a.pnl_pct ?? 0) >= 0 ? 'color:var(--green)' : 'color:var(--red)'">
          {{ fmtPct(a.pnl_pct ?? null) }}
        </div>
        <div class="num" :style="(a.pnl ?? 0) >= 0 ? 'color:var(--green)' : 'color:var(--red)'">
          {{ (a.pnl ?? 0) >= 0 ? "+" : "" }}{{ fmtYuan(a.pnl ?? 0) }} 元
        </div>
      </div>
      <div>
        <div class="muted">今日涨跌</div>
        <div class="big num"
             :style="(a.day_pnl ?? 0) >= 0 ? 'color:var(--green)' : 'color:var(--red)'">
          {{ a.day_pnl_pct != null ? fmtPct(a.day_pnl_pct) : "—" }}
        </div>
        <div class="num" :style="(a.day_pnl ?? 0) >= 0 ? 'color:var(--green)' : 'color:var(--red)'">
          {{ (a.day_pnl ?? 0) >= 0 ? "+" : "" }}{{ fmtYuan(a.day_pnl ?? 0) }} 元
        </div>
      </div>
      <div><div class="muted">市值</div>
        <div class="num" style="font-size:18px">{{ fmtYuan(a.value ?? 0) }} 元</div>
        <div class="muted num" style="font-size:12px">
          持仓 {{ fmtYuan(a.holdings ?? 0) }} + 现金 {{ fmtYuan(a.cash ?? 0) }} ·
          投入 {{ fmtYuan(a.invested ?? 0) }}</div></div>
      <div><div class="muted">份额 / 均价 / XIRR</div>
        <div class="num">{{ (a.units ?? 0).toFixed(1) }} 份 · 均价
          {{ a.avg_cost ? a.avg_cost.toFixed(4) : "—" }}</div>
        <div class="muted num" style="font-size:12px">
          XIRR {{ fmtPct(a.xirr ?? null) }} · {{ a.trades ?? 0 }} 笔成交</div></div>
    </div>
    <div style="margin-top:12px;display:flex;gap:8px">
      <button class="badge badge-hold" style="cursor:pointer" @click="showOutlook(a.id)">
        未来 3 天预案
      </button>
      <button
        class="badge"
        :class="(a.day_pnl ?? 0) >= 0 ? 'badge-buy' : 'badge-pause'"
        style="cursor:pointer" @click="showAttrib(a.id)">
        {{ attribFor === a.id ? "收起归因" : "为什么红/绿" }}
      </button>
      <button class="badge badge-pause" style="cursor:pointer" @click="remove(a.id)">删除</button>
    </div>

    <div v-if="attribFor === a.id && attrib" style="margin-top:12px">
      <template v-if="attrib.latest">
        <div class="muted" style="font-size:12px">
          {{ attrib.latest.day }}：今日盈亏
          <span :style="(attrib.latest.day_pnl ?? 0) >= 0
                        ? 'color:var(--green)' : 'color:var(--red)'">
            {{ signedYuan(attrib.latest.day_pnl) }} 元
            （{{ fmtPct(attrib.latest.day_pnl_pct) }}）
          </span>
          = 市场波动 {{ signedYuan(attrib.latest.market) }}
          <template v-if="attrib.latest.us_overnight != null">
            （美股隔夜 {{ signedYuan(attrib.latest.us_overnight) }}
            <template v-if="attrib.latest.fx != null">
              · 汇率 {{ signedYuan(attrib.latest.fx) }}</template>
            · 溢价/残差 {{ signedYuan(attrib.latest.premium_resid) }}）
          </template>
          − 费用 {{ signedYuan(attrib.latest.fees) }}
        </div>
        <table style="margin-top:8px">
          <thead><tr>
            <th>日期</th><th>收盘价</th><th>当日盈亏</th>
            <th v-if="attrib.factors.us_proxy">美股隔夜</th>
            <th v-if="attrib.factors.has_fx">汇率</th>
            <th v-if="attrib.factors.us_proxy">溢价/残差</th>
            <th>费用</th>
          </tr></thead>
          <tbody>
            <tr v-for="r in attrib.rows.slice().reverse().slice(0, 10)"
                :key="r.day"
                :style="(r.day_pnl ?? 0) >= 0 ? '' : 'color:var(--red)'">
              <td class="num">{{ r.day }}</td>
              <td class="num">{{ r.px_prev.toFixed(3) }} → {{ r.px_today.toFixed(3) }}</td>
              <td class="num">{{ signedYuan(r.day_pnl) }}</td>
              <td v-if="attrib.factors.us_proxy" class="num">
                {{ signedYuan(r.us_overnight) }}</td>
              <td v-if="attrib.factors.has_fx" class="num">{{ signedYuan(r.fx) }}</td>
              <td v-if="attrib.factors.us_proxy" class="num">
                {{ signedYuan(r.premium_resid) }}</td>
              <td class="num">{{ signedYuan(r.fees) }}</td>
            </tr>
          </tbody>
        </table>
        <div class="muted" style="font-size:12px;margin-top:6px">
          近 {{ attrib.rows.length }} 个交易日合计：盈亏
          {{ signedYuan(attrib.totals.day_pnl) }} 元 · 市场波动
          {{ signedYuan(attrib.totals.market) }}
          <template v-if="attrib.factors.us_proxy">
            · 美股 {{ signedYuan(attrib.totals.us_overnight) }}
            · 汇率 {{ signedYuan(attrib.totals.fx) }}
            · 溢价/残差 {{ signedYuan(attrib.totals.premium_resid) }}
          </template>
          · 费用 {{ signedYuan(attrib.totals.fees) }}<br>
          {{ attrib.note }}
        </div>
      </template>
      <div v-else class="muted">暂无可归因的交易日（前一日无持仓）。</div>
    </div>

    <div v-if="outlookFor === a.id && outlook" style="margin-top:12px">
      <div class="muted" style="font-size:12px">
        截至 {{ outlook.as_of }} · 当前溢价 {{ fmtPct(outlook.current_premium) }} ·
        年化波动 {{ fmtPct(outlook.vol_ann, 1, false) }}
        <template v-if="outlook.premium_stat">
          · 该溢价档（{{ outlook.premium_stat.bucket }}，{{ outlook.premium_stat.n }} 天）
          历史前向 1 日 {{ fmtPct(outlook.premium_stat.fwd1) }} / 5 日
          {{ fmtPct(outlook.premium_stat.fwd5) }}
        </template>
      </div>
      <table style="margin-top:8px">
        <thead><tr><th>日期</th><th>动作</th><th>计划金额</th><th>触发条件</th>
          <th>市值区间（±1σ）</th></tr></thead>
        <tbody>
          <tr v-for="r in outlook.rows" :key="r.day">
            <td class="num">{{ r.day }}（{{ r.weekday }}）</td>
            <td>
              <span v-if="r.action === '买入'" class="badge badge-buy">买入</span>
              <span v-else class="badge badge-pause">暂停</span>
            </td>
            <td class="num">{{ r.amount ? fmtYuan(r.amount) : "—" }}</td>
            <td class="muted" style="text-align:left">{{ r.condition }}</td>
            <td class="num">
              {{ r.value_low != null ? `${fmtYuan(r.value_low)} ~ ${fmtYuan(r.value_high ?? 0)}` : "—" }}
            </td>
          </tr>
        </tbody>
      </table>
      <div class="muted" style="font-size:12px;margin-top:6px">{{ outlook.disclaimer }}</div>
    </div>
  </div>

  <h2>实际盘录入成交</h2>
  <div class="card">
    <div class="grid" style="grid-template-columns:repeat(auto-fit,minmax(160px,1fr))">
      <label class="muted">账户
        <select v-model.number="tradeForm.aid" style="width:100%;background:var(--bg);
                color:var(--text);border:1px solid var(--border);border-radius:6px;padding:6px">
          <option v-for="a in rows.filter(x => x.kind === 'real')" :key="a.id" :value="a.id">
            #{{ a.id }} {{ a.name }}
          </option>
        </select>
      </label>
      <label class="muted">买入日期
        <input v-model="tradeForm.day" type="date" style="width:100%;background:var(--bg);
               color:var(--text);border:1px solid var(--border);border-radius:6px;padding:6px" />
      </label>
      <label class="muted">成交价
        <input v-model.number="tradeForm.price" type="number" step="0.001"
               style="width:100%;background:var(--bg);color:var(--text);
                      border:1px solid var(--border);border-radius:6px;padding:6px" />
      </label>
      <label class="muted">数量（份）
        <input v-model.number="tradeForm.qty" type="number" step="100"
               style="width:100%;background:var(--bg);color:var(--text);
                      border:1px solid var(--border);border-radius:6px;padding:6px" />
      </label>
    </div>
    <div style="margin-top:12px">
      <button class="badge badge-buy" style="cursor:pointer"
              :disabled="!tradeForm.aid || !tradeForm.day || !tradeForm.price"
              @click="submitTrade">录入</button>
      <span class="muted" style="margin-left:12px;font-size:12px">
        实际盘只记录你的真实成交，系统按库内价格继续核算市值/盈亏/XIRR
      </span>
    </div>
  </div>
</template>
