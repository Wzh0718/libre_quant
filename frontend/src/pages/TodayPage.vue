<script setup lang="ts">
import { onMounted, ref, watch } from "vue";
import { fetchToday, fmtPct, fmtYuan, type TodayCard } from "../api";
import { selectedCode } from "../store";

const data = ref<TodayCard | null>(null);
const error = ref<string | null>(null);
const loading = ref(true);

async function load() {
  loading.value = true;
  error.value = null;
  try {
    data.value = await fetchToday(selectedCode.value);
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
    <h2>今日决策 · {{ data.name }}（{{ data.code }}）· {{ data.day }}</h2>
    <div class="card">
      <div style="display:flex;gap:16px;align-items:center;flex-wrap:wrap">
        <div>
          <span class="big num">{{ fmtPct(data.premium) }}</span>
          <span class="muted"> 当前溢价</span>
        </div>
        <span v-if="data.gate === 'pause'" class="badge badge-pause">暂停买入</span>
        <span v-else class="badge badge-buy">正常买入 {{ fmtYuan(data.planned) }} 元</span>
        <span class="muted">待投现金 {{ fmtYuan(data.pending) }} 元</span>
      </div>
    </div>

    <h2>决策推理链（每天投递的思路）</h2>
    <div class="card">
      <ol style="padding-left: 20px">
        <li v-for="(r, i) in data.reasoning" :key="i" style="margin: 8px 0">{{ r }}</li>
      </ol>
    </div>

    <h2>近期信号轨迹</h2>
    <div class="card">
      <table v-if="data.trail.length">
        <thead><tr><th>日期</th><th>溢价</th><th>判定</th><th>计划</th></tr></thead>
        <tbody>
          <tr v-for="t in [...data.trail].reverse()" :key="t.day">
            <td class="num">{{ t.day }}</td>
            <td class="num">{{ fmtPct(t.premium) }}</td>
            <td>
              <span v-if="t.gate === 'pause'" class="badge badge-pause">暂停</span>
              <span v-else class="badge badge-buy">买入</span>
            </td>
            <td class="num">{{ fmtYuan(t.planned) }} 元</td>
          </tr>
        </tbody>
      </table>
      <div v-else class="muted">该标的未纳入影子盘（仅 159941 有逐日信号轨迹）</div>
    </div>
  </template>
</template>
