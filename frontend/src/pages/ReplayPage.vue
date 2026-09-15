<script setup lang="ts">
import { computed, onMounted, ref, watch } from "vue";
import {
  fetchReplay, fmtPct, fmtYuan,
  type JournalRow, type ReplayData,
} from "../api";
import MultiLineChart, { type LineSeries } from "../components/MultiLineChart.vue";
import { selectedCode } from "../store";
import { CHART_COLORS } from "../useChart";

const data = ref<ReplayData | null>(null);
const error = ref<string | null>(null);
const loading = ref(true);
const fill = ref("close");
const arm = ref("闸门日投");

async function load() {
  loading.value = true;
  error.value = null;
  try {
    data.value = await fetchReplay(selectedCode.value, fill.value);
  } catch (e) {
    error.value = e instanceof Error ? e.message : String(e);
  } finally {
    loading.value = false;
  }
}
onMounted(load);
watch([selectedCode, fill], load);

const COLORS: Record<string, string> = {
  "朴素日投": CHART_COLORS.muted,
  "闸门日投": CHART_COLORS.green,
  "闸门+5月线": CHART_COLORS.blue,
  "月度定投": CHART_COLORS.red,
};

const curves = computed<LineSeries[]>(() => {
  if (!data.value) return [];
  return Object.entries(data.value.arms).map(([name, a]) => ({
    name, values: a.curve, color: COLORS[name],
  }));
});

const journal = computed<JournalRow[]>(() => {
  const a = data.value?.arms[arm.value];
  return a ? [...a.journal].reverse() : [];
});

const gateArm = computed(() => data.value?.arms["闸门日投"]?.summary);
const naiveArm = computed(() => data.value?.arms["朴素日投"]?.summary);
</script>

<template>
  <div v-if="loading" class="state-block" role="status" aria-busy="true">重放中…</div>
  <div v-else-if="error" class="state-block" role="alert">
    加载失败：{{ error }} <button class="badge badge-hold" style="cursor:pointer" @click="load">重试</button>
  </div>
  <template v-else-if="data">
    <h2>逐日定投复盘 · {{ data.name }}（{{ data.code }}）· {{ data.span[0] }} ~ {{ data.span[1] }}</h2>
    <p class="muted">
      每天按当日已知信息独立决策、记录流水账；暂停的钱进"待投"，条件恢复当天连本带额补投。
      各变体投入同等月度资金（日投 200 ≈ 月投 4000）。
    </p>

    <div class="grid cards">
      <div class="card">
        <div class="muted">闸门 vs 朴素（买入均价溢价）</div>
        <div class="big num" style="font-size:22px">
          {{ fmtPct(gateArm?.avg_buy_premium ?? null) }} vs {{ fmtPct(naiveArm?.avg_buy_premium ?? null) }}
        </div>
        <div class="muted">闸门把买入溢价压低
          {{ fmtPct((naiveArm?.avg_buy_premium ?? 0) - (gateArm?.avg_buy_premium ?? 0), 2, false) }}</div>
      </div>
      <div class="card">
        <div class="muted">期末差异（闸门 − 朴素）</div>
        <div class="big num" style="font-size:22px">
          {{ fmtYuan((gateArm?.value ?? 0) - (naiveArm?.value ?? 0)) }} 元
        </div>
        <div class="muted">XIRR {{ fmtPct(gateArm?.xirr ?? null) }} vs {{ fmtPct(naiveArm?.xirr ?? null) }}</div>
      </div>
      <div class="card">
        <div class="muted">闸门暂停天数</div>
        <div class="big num">{{ gateArm?.pauses ?? 0 }}</div>
        <div class="muted">待投现金 {{ fmtYuan(gateArm?.pending ?? 0) }} 元</div>
      </div>
      <div class="card">
        <label class="muted">成交价模型
          <select v-model="fill" style="background:var(--surface);color:var(--text);
                  border:1px solid var(--border);border-radius:6px;padding:4px 8px;margin-left:8px">
            <option value="close">收盘</option>
            <option value="open">开盘</option>
            <option value="mid">日内中枢</option>
          </select>
        </label>
        <div class="muted" style="margin-top:8px">日内时点对 XIRR 的影响 &lt;0.02pp</div>
      </div>
    </div>

    <h2>变体对比</h2>
    <div class="card">
      <table>
        <thead>
          <tr><th>变体</th><th>投入</th><th>期末(市值+待投)</th><th>XIRR</th>
            <th>回撤</th><th>买入</th><th>暂停</th><th>买均溢价</th><th>费用</th></tr>
        </thead>
        <tbody>
          <tr v-for="(a, name) in data.arms" :key="name">
            <td>{{ name }}</td>
            <td class="num">{{ fmtYuan(a.summary.invested) }}</td>
            <td class="num">{{ fmtYuan(a.summary.value) }}</td>
            <td class="num">{{ fmtPct(a.summary.xirr) }}</td>
            <td class="num">{{ fmtPct(a.summary.max_dd, 1, false) }}</td>
            <td class="num">{{ a.summary.buys }}</td>
            <td class="num">{{ a.summary.pauses }}</td>
            <td class="num">{{ fmtPct(a.summary.avg_buy_premium) }}</td>
            <td class="num">{{ fmtYuan(a.summary.fees) }}</td>
          </tr>
        </tbody>
      </table>
    </div>

    <h2>账户净值曲线（四变体）</h2>
    <div class="card">
      <MultiLineChart :days="data.curve_days" :series="curves" aria-label="定投变体净值曲线" />
    </div>

    <h2>每日流水账（近 60 个交易日）</h2>
    <div class="card">
      <div style="margin-bottom:8px">
        <label class="muted">变体
          <select v-model="arm" style="background:var(--surface);color:var(--text);
                  border:1px solid var(--border);border-radius:6px;padding:4px 8px;margin-left:8px">
            <option v-for="(_, name) in data.arms" :key="name" :value="name">{{ name }}</option>
          </select>
        </label>
      </div>
      <table>
        <thead>
          <tr><th>日期</th><th>溢价</th><th>判定</th><th>动作</th>
            <th>本次买入</th><th>待投现金</th><th>累计投入</th><th>账户市值</th></tr>
        </thead>
        <tbody>
          <tr v-for="j in journal" :key="j.day">
            <td class="num">{{ j.day }}</td>
            <td class="num">{{ fmtPct(j.premium) }}</td>
            <td>
              <span v-if="j.gate === 'pause'" class="badge badge-pause">暂停</span>
              <span v-else class="badge badge-buy">买入</span>
            </td>
            <td>{{ j.action }}</td>
            <td class="num">{{ j.bought ? fmtYuan(j.bought) : "—" }}</td>
            <td class="num">{{ fmtYuan(j.pending) }}</td>
            <td class="num">{{ fmtYuan(j.invested) }}</td>
            <td class="num">{{ fmtYuan(j.value) }}</td>
          </tr>
        </tbody>
      </table>
    </div>
  </template>
</template>
