<script setup lang="ts">
import { onMounted, ref, watch } from "vue";
import {
  deleteStrategy, fetchStrategies, type StrategyRow,
} from "../api";
import { selectedCode } from "../store";

const items = ref<StrategyRow[]>([]);
const loading = ref(true);
const err = ref<string | null>(null);
const msg = ref<string | null>(null);

async function load() {
  loading.value = true;
  err.value = null;
  try {
    items.value = (await fetchStrategies(selectedCode.value)).items;
  } catch (e) {
    err.value = e instanceof Error ? e.message : String(e);
  } finally {
    loading.value = false;
  }
}

async function remove(s: StrategyRow) {
  if (!confirm(`删除版本 #${s.id}「${s.name}」？已落盘的作战方案不受影响。`)) return;
  err.value = null;
  try {
    await deleteStrategy(s.id);
    msg.value = `已删除 #${s.id}`;
    await load();
  } catch (e) {
    err.value = e instanceof Error ? e.message : String(e);
  }
}

onMounted(load);
watch(selectedCode, load);
</script>

<template>
  <h2>策略库 · {{ selectedCode }}</h2>
  <p class="muted" style="margin-top:-6px">
    每个版本是一组作战参数的快照（含调参血缘）。在「复盘」页调参跑出好结果后存进来；
    版本本身不会被后续修改覆盖 —— 改参数请存新版本。
  </p>
  <div v-if="loading" class="state-block">加载中…</div>
  <div v-else-if="err" class="state-block" role="alert">出错了：{{ err }}
    <button class="badge badge-hold" style="cursor:pointer" @click="load">重试</button></div>
  <div v-else class="card" style="overflow-x:auto">
    <div v-if="msg" class="muted" style="margin-bottom:8px">✅ {{ msg }}</div>
    <table v-if="items.length">
      <thead><tr>
        <th>#</th><th>名称</th><th>参数</th><th>备注</th><th>基于</th><th>创建时间</th><th>操作</th>
      </tr></thead>
      <tbody>
        <tr v-for="s in items" :key="s.id">
          <td class="num">{{ s.id }}</td>
          <td><strong>{{ s.name }}</strong></td>
          <td class="muted" style="font-size:12px">{{ s.params_label }}</td>
          <td class="muted" style="font-size:12px">{{ s.note || "—" }}</td>
          <td class="num">{{ s.parent_id ? `#${s.parent_id}` : "—" }}</td>
          <td class="num muted" style="font-size:12px">{{ s.created_at.slice(0, 16) }}</td>
          <td style="white-space:nowrap">
            <router-link :to="`/battle?strategy_id=${s.id}`">
              <button class="badge badge-buy" style="cursor:pointer">作战方案</button>
            </router-link>
            <button class="badge badge-hold" style="cursor:pointer;color:var(--red)"
                    @click="remove(s)">删除</button>
          </td>
        </tr>
      </tbody>
    </table>
    <div v-else class="muted">
      这个标的还没有策略版本 —— 去<router-link to="/review">复盘页</router-link>调参并存下第一版。
    </div>
  </div>
</template>
