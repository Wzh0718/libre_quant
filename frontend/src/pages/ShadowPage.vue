<script setup lang="ts">
import { onMounted, ref } from "vue";
import { fetchDashboard, type DashboardData } from "../api";
import ShadowPanel from "../components/ShadowPanel.vue";

const data = ref<DashboardData | null>(null);
const error = ref<string | null>(null);
const loading = ref(true);

async function load() {
  loading.value = true;
  error.value = null;
  try {
    data.value = await fetchDashboard();
  } catch (e) {
    error.value = e instanceof Error ? e.message : String(e);
  } finally {
    loading.value = false;
  }
}
onMounted(load);
</script>

<template>
  <div v-if="loading" class="state-block" role="status" aria-busy="true">加载中…</div>
  <div v-else-if="error" class="state-block" role="alert">
    加载失败：{{ error }} <button class="badge badge-hold" style="cursor:pointer" @click="load">重试</button>
  </div>
  <template v-else-if="data">
    <h2>影子盘 · champion–challenger（docs/10）</h2>
    <ShadowPanel :shadow="data.shadow" />
    <h2>策略总纲（docs/00）</h2>
    <div class="card">
      <table>
        <thead><tr><th>层</th><th style="text-align:left">规则</th><th>证据</th></tr></thead>
        <tbody>
          <tr><td>1 · 积累</td><td style="text-align:left">每交易日投 200 元买 159941（≈1 手）</td><td class="muted">docs/09</td></tr>
          <tr><td>2 · 入场闸门</td><td style="text-align:left">溢价 &gt;5% 暂停，攒钱；回落 ≤5% 连本带额补回</td><td class="muted">docs/07</td></tr>
          <tr><td>3 · 持仓风控</td><td style="text-align:left">满仓持有或波动率目标 25%（影子验证中）</td><td class="muted">docs/07/08</td></tr>
          <tr><td>4 · 验证纪律</td><td style="text-align:left">新规则：回测 → 影子盘 ≥60 日 → 达标才替换</td><td class="muted">docs/10</td></tr>
        </tbody>
      </table>
    </div>
  </template>
</template>
