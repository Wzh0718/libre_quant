<script setup lang="ts">
import type { Hero } from "../api";
import { fmtPct, fmtYuan } from "../api";

defineProps<{ hero: Hero }>();
</script>

<template>
  <div class="card">
    <div class="muted">
      {{ hero.name }}（{{ hero.code }}）· {{ hero.day }} 收盘
      <span class="num">{{ hero.close.toFixed(3) }}</span>
    </div>
    <div style="margin: 8px 0">
      <span class="big num">{{ fmtPct(hero.premium) }}</span>
      <span class="muted"> 当前溢价</span>
    </div>
    <div style="display: flex; gap: 8px; flex-wrap: wrap; align-items: center">
      <span v-if="hero.gate === 'pause'" class="badge badge-pause">暂停买入</span>
      <span v-else class="badge badge-buy">正常买入</span>
      <span v-if="hero.ma5.above" class="badge badge-buy">站上 5 月线</span>
      <span v-else class="badge badge-hold">跌破 5 月线</span>
    </div>
    <div class="muted" style="margin-top: 8px">
      今日计划 {{ fmtYuan(hero.planned) }} 元 · 待投现金 {{ fmtYuan(hero.pending) }} 元 ·
      60日波动 {{ fmtPct(hero.vol60, 1, false) }} → 25%目标仓位
      {{ fmtPct(hero.target_pos, 0, false) }}
    </div>
  </div>
</template>
