<script setup lang="ts">
import { computed, onMounted, ref, watch } from "vue";
import {
  fetchAccounts, fetchWorkbench, fmtPct, fmtYuan, saveSimpleParams,
  type AccountRow, type WorkbenchData,
} from "../api";
import MultiLineChart from "../components/MultiLineChart.vue";
import { selectedCode } from "../store";
import { CHART_COLORS } from "../useChart";

const data = ref<WorkbenchData | null>(null);
const accounts = ref<AccountRow[]>([]);
const accountId = ref<number | null>(null);
const loading = ref(true);
const err = ref<string | null>(null);
const msg = ref<string | null>(null);

const form = ref({
  daily: 200, premium_max: 5, dip_drop: -5, dip_mult: 2,
  rise_gain: 10, sell_pct: 20,
});

function fillForm(d: WorkbenchData) {
  form.value = {
    daily: d.params.daily ?? 0,
    premium_max: (d.params.premium_max ?? 0.05) * 100,
    dip_drop: (d.params.dip_drop ?? -0.05) * 100,
    dip_mult: d.params.dip_mult ?? 0,
    rise_gain: (d.params.rise_gain ?? 0.10) * 100,
    sell_pct: (d.params.sell_pct ?? 0) * 100,
  };
}

async function load() {
  loading.value = true;
  err.value = null;
  try {
    accounts.value = (await fetchAccounts()).items;
    if (accountId.value == null && accounts.value.length)
      accountId.value = accounts.value[0].id;
    data.value = await fetchWorkbench(selectedCode.value, accountId.value);
    if (!data.value.plan_configured) fillForm(data.value);
    else fillForm(data.value);
  } catch (e) {
    err.value = e instanceof Error ? e.message : String(e);
  } finally {
    loading.value = false;
  }
}

async function save() {
  msg.value = null;
  err.value = null;
  try {
    await saveSimpleParams(selectedCode.value, {
      daily: form.value.daily,
      premium_max: form.value.premium_max / 100,
      dip_drop: form.value.dip_drop / 100,
      dip_mult: form.value.dip_mult,
      rise_gain: form.value.rise_gain / 100,
      sell_pct: form.value.sell_pct / 100,
    });
    msg.value = "已保存，下面按新参数重算";
    await load();
  } catch (e) {
    err.value = e instanceof Error ? e.message : String(e);
  }
}

const realAccounts = computed(() => accounts.value.filter(a => a.kind === "real"));
const histSeries = computed(() => data.value?.history
  ? [{ name: "这套参数的历史账户价值", values: data.value.history.curve,
       color: CHART_COLORS.green }]
  : []);

onMounted(load);
watch([selectedCode, accountId], load);
</script>

<template>
  <div v-if="loading" class="state-block" role="status" aria-busy="true">计算中…</div>
  <div v-else-if="err" class="state-block" role="alert">
    出错了：{{ err }} <button class="badge badge-hold" style="cursor:pointer" @click="load">重试</button>
  </div>
  <template v-else-if="data && !data.empty">
    <h2>今天该做什么 · {{ data.name }}（{{ data.code }}）</h2>
    <div class="grid cards">
      <div class="card" style="grid-column: span 2">
        <div class="muted">动作</div>
        <div style="display:flex;align-items:baseline;gap:12px;flex-wrap:wrap">
          <span class="big num"
                :style="data.action.action === '买入' ? 'color:var(--green)'
                        : data.action.action === '卖出' ? 'color:var(--amber)'
                        : 'color:var(--red)'">{{ data.action.action }}</span>
          <span v-if="data.action.shares" class="num" style="font-size:20px">
            {{ data.action.shares.toFixed(0) }} 份
          </span>
          <span v-if="data.action.amount" class="num muted">
            ≈ {{ fmtYuan(data.action.amount) }} 元</span>
        </div>
        <ul style="margin:8px 0 0 0;padding-left:18px">
          <li v-for="(r, i) in data.action.reasons" :key="i"
              class="muted" style="font-size:13px">{{ r }}</li>
        </ul>
      </div>
      <div class="card">
        <div class="muted">你的持仓（实际盘）</div>
        <template v-if="data.hold.units">
          <div class="num" style="font-size:18px">{{ data.hold.units.toFixed(0) }} 份</div>
          <div class="muted num" style="font-size:12px">
            成本 {{ data.hold.avg_cost?.toFixed(3) }} · 市值 {{ fmtYuan(data.hold.value ?? 0) }} ·
            <span :style="(data.hold.profit ?? 0) >= 0 ? 'color:var(--green)' : 'color:var(--red)'">
              {{ fmtPct(data.hold.profit_pct ?? null) }}</span>
          </div>
        </template>
        <div v-else class="muted">还没录入成交——去「我的盘」建实际盘并录入</div>
      </div>
    </div>

    <h2>你的策略参数（全部你填）</h2>
    <div class="card">
      <div class="grid" style="grid-template-columns:repeat(auto-fit,minmax(190px,1fr))">
        <label class="muted">每天买入（元）
          <input v-model.number="form.daily" type="number" step="50"
                 style="width:100%;background:var(--bg);color:var(--text);
                        border:1px solid var(--border);border-radius:6px;padding:6px" />
        </label>
        <label class="muted">溢价超过（%）就不买
          <input v-model.number="form.premium_max" type="number" step="0.5"
                 style="width:100%;background:var(--bg);color:var(--text);
                        border:1px solid var(--border);border-radius:6px;padding:6px" />
        </label>
        <label class="muted">跌超过（%）就多买
          <input v-model.number="form.dip_drop" type="number" step="1"
                 style="width:100%;background:var(--bg);color:var(--text);
                        border:1px solid var(--border);border-radius:6px;padding:6px" />
        </label>
        <label class="muted">多买几倍
          <input v-model.number="form.dip_mult" type="number" step="0.5" min="0"
                 style="width:100%;background:var(--bg);color:var(--text);
                        border:1px solid var(--border);border-radius:6px;padding:6px" />
        </label>
        <label class="muted">涨超过（%）就卖出
          <input v-model.number="form.rise_gain" type="number" step="1"
                 style="width:100%;background:var(--bg);color:var(--text);
                        border:1px solid var(--border);border-radius:6px;padding:6px" />
        </label>
        <label class="muted">卖出持仓的（%）
          <input v-model.number="form.sell_pct" type="number" step="5" min="0" max="100"
                 style="width:100%;background:var(--bg);color:var(--text);
                        border:1px solid var(--border);border-radius:6px;padding:6px" />
        </label>
      </div>
      <div class="muted" style="margin-top:8px;font-size:12px">
        规则都按<b>这只 ETF 的场内价格</b>判断：跌/涨看近 7 天；卖出按<b>你的真实持仓份数</b>算。
        填 0 或负数表示不启用该条。
      </div>
      <div style="margin-top:12px;display:flex;gap:12px;align-items:center">
        <button class="badge badge-buy" style="cursor:pointer" @click="save">
          保存并重算</button>
        <span v-if="msg" class="muted">✅ {{ msg }}</span>
        <label class="muted" style="margin-left:auto">用哪个盘算持仓
          <select v-model.number="accountId" style="background:var(--surface);color:var(--text);
                  border:1px solid var(--border);border-radius:6px;padding:4px 8px;margin-left:6px">
            <option :value="null">不关联</option>
            <option v-for="a in realAccounts" :key="a.id" :value="a.id">
              #{{ a.id }} {{ a.name }}</option>
          </select>
        </label>
      </div>
    </div>

    <h2>价格明细</h2>
    <div class="card">
      <div style="display:flex;gap:24px;flex-wrap:wrap">
        <div><div class="muted">今天</div>
          <div class="num" style="font-size:20px">{{ data.price.today.toFixed(3) }}</div>
          <div class="muted num" style="font-size:12px">{{ data.price.today_day }}</div></div>
        <div v-if="data.price.yesterday"><div class="muted">昨天</div>
          <div class="num" style="font-size:20px">{{ data.price.yesterday.toFixed(3) }}</div>
          <div class="num" style="font-size:12px"
               :style="(data.price.change_1d ?? 0) >= 0 ? 'color:var(--green)' : 'color:var(--red)'">
            {{ fmtPct(data.price.change_1d ?? null) }}</div></div>
        <div><div class="muted">近 7 天</div>
          <div class="num" style="font-size:20px"
               :style="(data.price.change_7d ?? 0) >= 0 ? 'color:var(--green)' : 'color:var(--red)'">
            {{ fmtPct(data.price.change_7d) }}</div></div>
        <div><div class="muted">近 14 天</div>
          <div class="num" style="font-size:20px"
               :style="(data.price.change_14d ?? 0) >= 0 ? 'color:var(--green)' : 'color:var(--red)'">
            {{ fmtPct(data.price.change_14d) }}</div></div>
      </div>
      <table style="margin-top:12px">
        <thead><tr><th>近 14 天日期</th><th>收盘</th><th>当天涨跌</th></tr></thead>
        <tbody>
          <tr v-for="r in (data.price.path_14 ?? []).slice().reverse()" :key="r.day">
            <td class="num">{{ r.day }}</td>
            <td class="num">{{ r.close.toFixed(3) }}</td>
            <td class="num"
                :style="(r.change ?? 0) >= 0 ? 'color:var(--green)' : 'color:var(--red)'">
              {{ fmtPct(r.change) }}</td>
          </tr>
        </tbody>
      </table>
    </div>

    <h2>这套参数跑历史</h2>
    <template v-if="data.history">
      <div class="grid cards">
        <div class="card"><div class="muted">总共投入</div>
          <div class="big num">{{ fmtYuan(data.history.invested) }}</div></div>
        <div class="card"><div class="muted">现在值</div>
          <div class="big num">{{ fmtYuan(data.history.value) }}</div></div>
        <div class="card"><div class="muted">赚了</div>
          <div class="big num"
               :style="(data.history.profit_pct ?? 0) >= 0 ? 'color:var(--green)' : 'color:var(--red)'">
            {{ fmtPct(data.history.profit_pct) }}</div>
          <div class="muted num" style="font-size:12px">
            {{ fmtYuan(data.history.profit) }} 元</div></div>
        <div class="card"><div class="muted">最差时亏</div>
          <div class="big num" style="color:var(--red)">
            {{ fmtPct(data.history.max_dd, 1, false) }}</div></div>
        <div class="card"><div class="muted">买入 / 卖出 / 不买</div>
          <div class="num">{{ data.history.buys }} / {{ data.history.sells }} /
            {{ data.history.skips }} 次</div></div>
      </div>
      <div class="card" style="margin-top:12px">
        <MultiLineChart :days="data.history.curve_days" :series="histSeries"
                        aria-label="这套参数的历史账户价值" />
      </div>
    </template>
    <div v-else class="card muted">填了「每天买入」并保存后，这里会显示这套参数的历史结果。</div>
  </template>
  <div v-else class="state-block">{{ data?.note }}</div>
</template>
