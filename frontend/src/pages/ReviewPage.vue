<script setup lang="ts">
import { computed, onMounted, ref, watch } from "vue";
import { fetchReview, fmtPct, fmtYuan, type ReviewData } from "../api";
import MultiLineChart, { type LineSeries } from "../components/MultiLineChart.vue";
import { selectedCode } from "../store";
import { CHART_COLORS } from "../useChart";

const data = ref<ReviewData | null>(null);
const error = ref<string | null>(null);
const loading = ref(true);

async function load() {
  loading.value = true;
  error.value = null;
  try {
    data.value = await fetchReview(selectedCode.value);
  } catch (e) {
    error.value = e instanceof Error ? e.message : String(e);
  } finally {
    loading.value = false;
  }
}
onMounted(load);
watch(selectedCode, load);

const COLORS: Record<string, string> = {
  "买入持有": CHART_COLORS.muted,
  "MA60趋势": CHART_COLORS.blue,
  "MA5月线": CHART_COLORS.red,
  "波动率目标25%": CHART_COLORS.green,
};

const curveSeries = computed<LineSeries[]>(() => {
  if (!data.value) return [];
  return Object.entries(data.value.strategies).map(([name, s]) => ({
    name, values: s.equity, color: COLORS[name],
  }));
});

const years = computed(() => {
  if (!data.value) return [];
  const all = new Set<string>();
  for (const s of Object.values(data.value.strategies))
    Object.keys(s.yearly).forEach(y => all.add(y));
  return [...all].sort();
});
</script>

<template>
  <div v-if="loading" class="state-block" role="status" aria-busy="true">加载中…</div>
  <div v-else-if="error" class="state-block" role="alert">
    加载失败：{{ error }} <button class="badge badge-hold" style="cursor:pointer" @click="load">重试</button>
  </div>
  <template v-else-if="data">
    <h2>策略对比 · {{ data.name }}（{{ data.code }}）· {{ data.span[0] }} ~ {{ data.span[1] }}</h2>
    <div class="card">
      <table>
        <thead>
          <tr><th>策略</th><th>总收益</th><th>年化</th><th>回撤</th><th>夏普</th><th>仓位</th><th>调仓</th></tr>
        </thead>
        <tbody>
          <tr v-for="(s, name) in data.strategies" :key="name">
            <td>{{ name }}</td>
            <td class="num">{{ fmtPct(s.total, 1) }}</td>
            <td class="num">{{ fmtPct(s.cagr, 1) }}</td>
            <td class="num">{{ fmtPct(s.max_dd, 1, false) }}</td>
            <td class="num">{{ s.sharpe.toFixed(2) }}</td>
            <td class="num">{{ fmtPct(s.exposure, 0, false) }}</td>
            <td class="num">{{ s.trades }}</td>
          </tr>
        </tbody>
      </table>
    </div>

    <h2>净值曲线（从数据库实时重算，口径与 docs/04/08 一致）</h2>
    <div class="card">
      <MultiLineChart :days="data.days" :series="curveSeries" aria-label="策略净值曲线" />
    </div>

    <h2>分年收益</h2>
    <div class="card">
      <table>
        <thead>
          <tr><th>年份</th><th v-for="(s, name) in data.strategies" :key="name">{{ name }}</th></tr>
        </thead>
        <tbody>
          <tr v-for="y in years" :key="y">
            <td class="num">{{ y }}</td>
            <td v-for="(s, name) in data.strategies" :key="name" class="num">
              {{ s.yearly[y] != null ? fmtPct(s.yearly[y], 1) : "—" }}
            </td>
          </tr>
        </tbody>
      </table>
    </div>

    <h2>定投变体（每日 200 元，你的佣金口径）</h2>
    <div class="card">
      <table>
        <thead>
          <tr><th>变体</th><th>总投入</th><th>期末市值</th><th>倍数</th><th>XIRR</th><th>回撤</th><th>费用</th></tr>
        </thead>
        <tbody>
          <tr v-for="r in data.dca" :key="r.name">
            <td>{{ r.name }}</td>
            <td class="num">{{ fmtYuan(r.invested) }}</td>
            <td class="num">{{ fmtYuan(r.value) }}</td>
            <td class="num">{{ r.multiple.toFixed(2) }}</td>
            <td class="num">{{ fmtPct(r.xirr) }}</td>
            <td class="num">{{ fmtPct(r.dd, 1, false) }}</td>
            <td class="num">{{ fmtYuan(r.fees) }}</td>
          </tr>
        </tbody>
      </table>
    </div>
  </template>
</template>
