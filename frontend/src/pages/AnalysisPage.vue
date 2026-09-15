<script setup lang="ts">
import { onMounted, ref, watch } from "vue";
import { fetchAnalysis, fmtPct, type AnalysisData } from "../api";
import MultiLineChart from "../components/MultiLineChart.vue";
import { selectedCode } from "../store";
import { CHART_COLORS } from "../useChart";

const data = ref<AnalysisData | null>(null);
const error = ref<string | null>(null);
const loading = ref(true);

async function load() {
  loading.value = true;
  error.value = null;
  try {
    data.value = await fetchAnalysis(selectedCode.value);
  } catch (e) {
    error.value = e instanceof Error ? e.message : String(e);
  } finally {
    loading.value = false;
  }
}
onMounted(load);
watch(selectedCode, load);
</script>

<template>
  <div v-if="loading" class="state-block" role="status" aria-busy="true">加载中…</div>
  <div v-else-if="error" class="state-block" role="alert">
    加载失败：{{ error }} <button class="badge badge-hold" style="cursor:pointer" @click="load">重试</button>
  </div>
  <template v-else-if="data">
    <h2>{{ data.name }}（{{ data.code }}）溢价 · 全历史</h2>
    <div class="card">
      <MultiLineChart
        :days="data.prem_days"
        :series="[{ name: '溢价', values: data.prem_series, color: CHART_COLORS.blue }]"
        :percent="true"
        :mark-line-y="[{ y: 0.05, label: '危险线 +5%', color: CHART_COLORS.red }]"
        aria-label="溢价全历史走势" />
    </div>

    <h2>分布统计（分析随图携带）</h2>
    <div class="grid cards">
      <div class="card"><div class="muted">中位数 p50</div>
        <div class="big num">{{ fmtPct(data.analytics.dist.p50) }}</div></div>
      <div class="card"><div class="muted">p95 / max</div>
        <div class="big num" style="font-size:22px">
          {{ fmtPct(data.analytics.dist.p95) }} / {{ fmtPct(data.analytics.dist.max) }}</div></div>
      <div class="card"><div class="muted">>2% 天数占比</div>
        <div class="big num">{{ fmtPct(data.analytics.gt2, 1, false) }}</div></div>
      <div class="card"><div class="muted">>5% 天数占比</div>
        <div class="big num">{{ fmtPct(data.analytics.gt5, 1, false) }}</div></div>
    </div>

    <h2>溢价分桶 → 前向收益（闸门的实证依据）</h2>
    <div class="card">
      <table>
        <thead><tr><th>溢价桶</th><th>天数</th><th>前向1日均</th><th>前向5日均</th><th>解读</th></tr></thead>
        <tbody>
          <tr v-for="b in data.analytics.buckets" :key="b.label"
              :style="b.is_danger ? 'color: var(--red)' : ''">
            <td>{{ b.label }}</td>
            <td class="num">{{ b.n }}</td>
            <td class="num">{{ fmtPct(b.fwd1) }}</td>
            <td class="num">{{ fmtPct(b.fwd5) }}</td>
            <td class="muted">{{ b.is_danger ? "唯一前向转负的桶 → 禁买线" : "前向为正" }}</td>
          </tr>
        </tbody>
      </table>
    </div>

    <h2>价格 vs 净值（近两年，归一=100）</h2>
    <div class="card">
      <MultiLineChart
        :days="data.win_days"
        :series="[
          { name: '价格', values: data.px_norm, color: CHART_COLORS.blue },
          { name: '净值', values: data.nav_norm, color: CHART_COLORS.muted },
        ]"
        aria-label="价格净值对比" />
    </div>
  </template>
</template>
