<script setup lang="ts">
import type { AssetGate } from "../api";
import { fmtPct } from "../api";

defineProps<{ assets: AssetGate[] }>();
</script>

<template>
  <div class="card">
    <table>
      <thead>
        <tr><th>标的</th><th>溢价</th><th>判定</th></tr>
      </thead>
      <tbody>
        <tr v-for="a in assets" :key="a.code">
          <td>{{ a.name }}（{{ a.code }}）</td>
          <td class="num">{{ fmtPct(a.premium) }}</td>
          <td>
            <span v-if="a.gate === 'pause'" class="badge badge-pause">暂停买入</span>
            <span v-else class="badge badge-buy">正常买入</span>
          </td>
        </tr>
      </tbody>
    </table>
  </div>
</template>
