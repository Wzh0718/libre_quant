<script setup lang="ts">
import { toRefs } from "vue";
import { baseGrid, CHART_COLORS, useChart } from "../useChart";

const props = defineProps<{ days: string[]; series: number[] }>();
const { days, series } = toRefs(props);

const el = useChart(() => ({
  ...baseGrid(),
  xAxis: { ...baseGrid().xAxis, data: days.value },
  yAxis: {
    ...baseGrid().yAxis,
    axisLabel: {
      ...(baseGrid().yAxis as object),
      formatter: (v: number) => `${(v * 100).toFixed(0)}%`,
      color: CHART_COLORS.muted, fontSize: 10,
    },
  },
  series: [{
    name: "溢价",
    type: "line",
    data: series.value,
    showSymbol: false,
    lineStyle: { color: CHART_COLORS.blue, width: 1.5 },
    itemStyle: { color: CHART_COLORS.blue },
    markLine: {
      symbol: "none",
      data: [
        { yAxis: 0.05, label: { formatter: "危险线 +5%", color: CHART_COLORS.red }, lineStyle: { color: CHART_COLORS.red, type: "dashed" } },
        { yAxis: 0, label: { show: false }, lineStyle: { color: CHART_COLORS.border } },
      ],
    },
  }],
}));
</script>

<template>
  <div ref="el" class="chart" role="img" aria-label="溢价走势图" />
</template>
