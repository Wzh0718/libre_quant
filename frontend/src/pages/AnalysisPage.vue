<script setup lang="ts">
import { onMounted, ref, watch } from "vue";
import {
  fetchAnalysis, fetchDecomp, fmtPct,
  type AnalysisData, type DecompData,
} from "../api";
import MultiLineChart from "../components/MultiLineChart.vue";
import { selectedCode } from "../store";
import { CHART_COLORS } from "../useChart";

const data = ref<AnalysisData | null>(null);
const decomp = ref<DecompData | null>(null);
const error = ref<string | null>(null);
const loading = ref(true);

async function load() {
  loading.value = true;
  error.value = null;
  try {
    const [a, d] = await Promise.all([
      fetchAnalysis(selectedCode.value),
      fetchDecomp(selectedCode.value),
    ]);
    data.value = a;
    decomp.value = d;
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

    <h2>收益归因：标的 / 汇率 / 费用 / 溢价（docs/11）
      <span class="muted" style="font-size:12px;font-weight:400">
        —— 仅解释收益来源，不参与买卖决策</span></h2>
    <template v-if="decomp && decomp.period">
      <div class="card">
        <div class="muted">
          区间 {{ decomp.period.span[0] }} ~ {{ decomp.period.span[1] }}（{{ decomp.n }} 个净值日，
          剔除份额折算 {{ decomp.skipped_events }} 天）
        </div>
        <table style="margin-top:8px">
          <thead><tr><th>来源</th><th>区间收益</th><th>说明</th></tr></thead>
          <tbody>
            <tr><td>场内价格（前复权）</td>
              <td class="num">{{ fmtPct(decomp.period.price_total ?? null, 1) }}</td>
              <td class="muted">你实际拿到的（份额口径）</td></tr>
            <tr><td>净值（CNY）</td>
              <td class="num">{{ fmtPct(decomp.period.nav_total, 1) }}</td>
              <td class="muted">基金资产的真实增值</td></tr>
            <tr v-if="decomp.period.underlying_total != null">
              <td>├─ 标的 {{ decomp.underlying_code?.toUpperCase() }}</td>
              <td class="num">{{ fmtPct(decomp.period.underlying_total, 1) }}</td>
              <td class="muted">美股底层涨跌（美元计）</td></tr>
            <tr v-if="decomp.period.fx_total != null">
              <td>├─ 汇率 USDCNH</td>
              <td class="num">{{ fmtPct(decomp.period.fx_total, 1) }}</td>
              <td class="muted">人民币升贬的额外损益（尾部保险，非收益引擎）</td></tr>
            <tr v-if="decomp.period.resid_total != null">
              <td>└─ 费用/跟踪残差</td>
              <td class="num">{{ fmtPct(decomp.period.resid_total, 1) }}</td>
              <td class="muted">管理费+托管+跟踪误差的长期拖累</td></tr>
            <tr><td>溢价效应（价格 ÷ 净值）</td>
              <td class="num">{{ fmtPct(decomp.period.premium_effect ?? null, 1) }}</td>
              <td class="muted">高溢价买入 = 白送；低溢价/折价买入 = 白赚</td></tr>
          </tbody>
        </table>
      </div>
      <div class="grid cards" style="margin-top:12px">
        <div class="card"><div class="muted">标的解释的波动占比</div>
          <div class="big num">{{ fmtPct(decomp.var_share?.underlying ?? null, 1, false) }}</div>
          <div class="muted">相关 {{ (decomp.corr?.underlying ?? 0).toFixed(3) }}</div></div>
        <div class="card"><div class="muted">汇率解释的波动占比</div>
          <div class="big num">{{ fmtPct(decomp.var_share?.fx ?? null, 1, false) }}</div>
          <div class="muted">相关 {{ (decomp.corr?.fx ?? 0).toFixed(3) }}（近零/负 = 不驱动波动）</div></div>
        <div class="card"><div class="muted">残差（费用/跟踪）</div>
          <div class="big num">{{ fmtPct(decomp.resid_var_share ?? null, 1, false) }}</div>
          <div class="muted">无法被场外因子解释的部分</div></div>
      </div>
    </template>
    <div v-else class="card muted">
      {{ decomp?.note ?? "该标的无场外因子分解（境内 ETF 无汇率暴露）" }}
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
