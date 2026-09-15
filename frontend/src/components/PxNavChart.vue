<script setup lang="ts">
import { toRefs } from "vue";
import { baseGrid, CHART_COLORS, useChart } from "../useChart";

const props = defineProps<{ days: string[]; px: number[]; nav: number[] }>();
const { days, px, nav } = toRefs(props);

const el = useChart(() => ({
  ...baseGrid(),
  legend: { data: ["价格", "净值"], textStyle: { color: CHART_COLORS.muted }, top: 0 },
  xAxis: { ...baseGrid().xAxis, data: days.value },
  series: [
    { name: "价格", type: "line", data: px.value, showSymbol: false,
      lineStyle: { color: CHART_COLORS.blue, width: 1.5 }, itemStyle: { color: CHART_COLORS.blue } },
    { name: "净值", type: "line", data: nav.value, showSymbol: false,
      lineStyle: { color: CHART_COLORS.muted, width: 1.5 }, itemStyle: { color: CHART_COLORS.muted } },
  ],
}));
</script>

<template>
  <div ref="el" class="chart" role="img" aria-label="价格净值对比图" />
</template>
