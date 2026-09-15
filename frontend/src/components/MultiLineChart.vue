<script setup lang="ts">
import { toRefs } from "vue";
import { baseGrid, CHART_COLORS, useChart } from "../useChart";

export interface LineSeries {
  name: string;
  values: number[];
  color?: string;
}

const props = withDefaults(defineProps<{
  days: string[];
  series: LineSeries[];
  percent?: boolean;
  markLineY?: { y: number; label: string; color?: string }[];
  ariaLabel?: string;
}>(), { percent: false, markLineY: () => [], ariaLabel: "趋势图" });

const { days, series, percent, markLineY } = toRefs(props);
const PALETTE = [CHART_COLORS.blue, CHART_COLORS.muted, CHART_COLORS.green, CHART_COLORS.red, CHART_COLORS.text];

const el = useChart(() => ({
  ...baseGrid(),
  legend: series.value.length > 1
    ? { textStyle: { color: CHART_COLORS.muted }, top: 0 } : undefined,
  xAxis: { ...baseGrid().xAxis, data: days.value },
  yAxis: {
    ...baseGrid().yAxis,
    axisLabel: {
      color: CHART_COLORS.muted, fontSize: 10,
      formatter: (v: number) => percent.value ? `${(v * 100).toFixed(0)}%` : `${v}`,
    },
  },
  series: series.value.map((s, i) => ({
    name: s.name,
    type: "line",
    data: s.values,
    showSymbol: false,
    lineStyle: { color: s.color ?? PALETTE[i % PALETTE.length], width: 1.5 },
    itemStyle: { color: s.color ?? PALETTE[i % PALETTE.length] },
    markLine: i === 0 && markLineY.value.length ? {
      symbol: "none",
      data: markLineY.value.map(m => ({
        yAxis: m.y,
        label: { formatter: m.label, color: m.color ?? CHART_COLORS.red },
        lineStyle: { color: m.color ?? CHART_COLORS.red, type: "dashed" },
      })),
    } : undefined,
  })),
}));
</script>

<template>
  <div ref="el" class="chart" role="img" :aria-label="ariaLabel" />
</template>
