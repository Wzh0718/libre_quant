<script setup lang="ts">
import { onMounted, ref } from "vue";
import { fetchDashboard, type DashboardData } from "./api";
import SignalHero from "./components/SignalHero.vue";
import GateTable from "./components/GateTable.vue";
import RulesCard from "./components/RulesCard.vue";
import PremiumChart from "./components/PremiumChart.vue";
import PxNavChart from "./components/PxNavChart.vue";
import ShadowPanel from "./components/ShadowPanel.vue";

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
  <main>
    <h1>
      libre_quant 看板
      <span v-if="data" class="muted" style="font-size: 13px">
        数据截至 {{ data.data_asof }} · 生成 {{ data.generated }}
      </span>
    </h1>

    <div v-if="loading" class="state-block" role="status" aria-busy="true">加载中…</div>

    <div v-else-if="error" class="state-block" role="alert">
      <p>看板数据加载失败：{{ error }}</p>
      <p><button class="badge badge-hold" style="cursor: pointer" @click="load">重试</button></p>
    </div>

    <template v-else-if="data">
      <h2>今日信号</h2>
      <SignalHero :hero="data.hero" />

      <h2>全标的闸门</h2>
      <GateTable :assets="data.assets" />

      <h2>策略总纲（docs/00）</h2>
      <RulesCard />

      <h2>{{ data.hero.code }} 溢价 · 近一年</h2>
      <div class="card"><PremiumChart :days="data.prem_days" :series="data.prem_series" /></div>

      <h2>{{ data.hero.code }} 价格 vs 净值（归一=100）</h2>
      <div class="card"><PxNavChart :days="data.prem_days" :px="data.px_norm" :nav="data.nav_norm" /></div>

      <h2>影子盘（champion–challenger，docs/10）</h2>
      <ShadowPanel :shadow="data.shadow" />

      <p class="muted" style="margin-top: 24px; font-size: 12px">
        口径：溢价=不复权收盘 ÷ 严格早于当日的第 lag 个净值 − 1（写入时配对，无前视）；
        成交价=收盘代理；佣金=万0.5/最低0.1元。证据：docs/00 策略总纲。
      </p>
    </template>
  </main>
</template>
