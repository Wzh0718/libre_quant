<script setup lang="ts">
import { onMounted, ref, watch } from "vue";
import {
  fetchLive, fetchToday, fmtPct, fmtYuan,
  type LiveData, type TodayCard,
} from "../api";
import { selectedCode } from "../store";

const data = ref<TodayCard | null>(null);
const live = ref<LiveData | null>(null);
const liveErr = ref<string | null>(null);
const error = ref<string | null>(null);
const loading = ref(true);

async function loadLive() {
  liveErr.value = null;
  try {
    live.value = await fetchLive(selectedCode.value);
  } catch (e) {
    liveErr.value = e instanceof Error ? e.message : String(e);
  }
}

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
  void loadLive();
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

    <h2>盘中实时（时效性：收盘前可判定）</h2>
    <div class="card">
      <div v-if="live && live.price" style="display:flex;gap:16px;align-items:center;flex-wrap:wrap">
        <div>
          <span class="big num">{{ fmtPct(live.premium) }}</span>
          <span class="muted"> 实时溢价</span>
        </div>
        <span v-if="live.gate === 'pause'" class="badge badge-pause">暂停买入</span>
        <span v-else class="badge badge-buy">正常买入</span>
        <span class="muted">
          现价 <span class="num">{{ live.price.toFixed(3) }}</span> ·
          最近净值 <span class="num">{{ live.nav_used?.toFixed(4) }}</span>（{{ live.nav_day }}）·
          {{ live.ts }}
        </span>
      </div>
      <div v-else class="muted">
        {{ liveErr ? `实时行情暂不可用（${liveErr}）` : "非交易时段或暂无实时报价" }}
        <button class="badge badge-hold" style="cursor:pointer;margin-left:8px" @click="loadLive">重试</button>
      </div>
      <div class="muted" style="margin-top:8px;font-size:12px">
        {{ live?.note ?? "盘中实时（收盘前参考；日终以入库收盘价为准）" }}
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
