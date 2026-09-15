<script setup lang="ts">
import { toRefs } from "vue";
import type { Shadow } from "../api";
import { fmtYuan } from "../api";
import { baseGrid, CHART_COLORS, useChart } from "../useChart";

const props = defineProps<{ shadow: Shadow }>();
const { shadow } = toRefs(props);

const el = useChart(() => ({
  ...baseGrid(),
  legend: { data: ["闸门臂", "朴素臂"], textStyle: { color: CHART_COLORS.muted }, top: 0 },
  xAxis: { ...baseGrid().xAxis, data: shadow.value.curve_days },
  series: [
    { name: "闸门臂", type: "line", data: shadow.value.curves.gate, showSymbol: false,
      lineStyle: { color: CHART_COLORS.green, width: 1.5 }, itemStyle: { color: CHART_COLORS.green } },
    { name: "朴素臂", type: "line", data: shadow.value.curves.naive, showSymbol: false,
      lineStyle: { color: CHART_COLORS.muted, width: 1.5 }, itemStyle: { color: CHART_COLORS.muted } },
  ],
}));
</script>

<template>
  <div class="grid cards">
    <div class="card">
      <div class="muted">影子盘运行</div>
      <div class="big num">{{ shadow.days }}<span style="font-size: 14px"> 天</span></div>
      <div class="muted">起跑 {{ shadow.first }}</div>
    </div>
    <div class="card">
      <div class="muted">闸门臂（gate）</div>
      <div class="num">市值+现金 {{ fmtYuan(shadow.gate.value) }} 元</div>
      <div class="muted">
        投入 {{ fmtYuan(shadow.gate.invested) }} · 待投 {{ fmtYuan(shadow.gate.pending) }} ·
        费用 {{ fmtYuan(shadow.gate.fees) }}
      </div>
    </div>
    <div class="card">
      <div class="muted">朴素臂（naive）</div>
      <div class="num">市值+现金 {{ fmtYuan(shadow.naive.value) }} 元</div>
      <div class="muted">投入 {{ fmtYuan(shadow.naive.invested) }} · 费用 {{ fmtYuan(shadow.naive.fees) }}</div>
    </div>
    <div class="card">
      <div class="muted">晋升检查单</div>
      <ul>
        <li v-for="[ok, label] in shadow.checklist" :key="label">
          {{ ok ? "✅" : "⬜" }} {{ label }}
        </li>
      </ul>
    </div>
  </div>
  <div class="card" style="margin-top: 12px">
    <div v-if="shadow.days >= 2" ref="el" class="chart" role="img" aria-label="影子盘两臂净值曲线" />
    <div v-else class="muted">影子曲线需 ≥2 天数据（当前 {{ shadow.days }} 天）</div>
  </div>
</template>
