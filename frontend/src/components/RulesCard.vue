<script setup lang="ts">
const rules = [
  ["1 · 积累", "每交易日投 200 元买 159941（≈1 手）", "docs/09"],
  ["2 · 入场闸门", "溢价 >5% 暂停，攒钱；回落 ≤5% 连本带额补回", "docs/07"],
  ["3 · 持仓风控", "满仓持有或波动率目标 25%（影子验证中）", "docs/07/08"],
  ["4 · 验证纪律", "新规则：回测 → 影子盘 ≥60 日 → 达标才替换", "docs/10"],
];
</script>

<template>
  <div class="card">
    <table>
      <thead>
        <tr><th>层</th><th style="text-align: left">规则</th><th>证据</th></tr>
      </thead>
      <tbody>
        <tr v-for="r in rules" :key="r[0]">
          <td>{{ r[0] }}</td>
          <td style="text-align: left">{{ r[1] }}</td>
          <td class="muted">{{ r[2] }}</td>
        </tr>
      </tbody>
    </table>
  </div>
</template>
