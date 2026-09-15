import * as echarts from "echarts";
import { onBeforeUnmount, onMounted, ref, watch } from "vue";

/** 挂载一个 ECharts 实例到 div，跟随容器尺寸变化；option 为响应式 getter。 */
export function useChart(option: () => echarts.EChartsOption) {
  const el = ref<HTMLDivElement | null>(null);
  let chart: echarts.ECharts | null = null;
  let ro: ResizeObserver | null = null;

  onMounted(() => {
    if (!el.value) return;
    chart = echarts.init(el.value);
    chart.setOption(option());
    ro = new ResizeObserver(() => chart?.resize());
    ro.observe(el.value);
  });
  onBeforeUnmount(() => {
    ro?.disconnect();
    chart?.dispose();
  });
  watch(option, (o) => chart?.setOption(o, true), { deep: true });
  return el;
}

export const CHART_COLORS = {
  blue: "#58a6ff",
  green: "#3fb950",
  red: "#f85149",
  muted: "#8b949e",
  border: "#30363d",
  text: "#e6edf3",
};

export function baseGrid(): echarts.EChartsOption {
  return {
    backgroundColor: "transparent",
    grid: { left: 48, right: 16, top: 24, bottom: 24 },
    tooltip: { trigger: "axis" },
    xAxis: {
      type: "category",
      axisLine: { lineStyle: { color: CHART_COLORS.border } },
      axisLabel: { color: CHART_COLORS.muted, fontSize: 10 },
    },
    yAxis: {
      type: "value",
      axisLabel: { color: CHART_COLORS.muted, fontSize: 10 },
      splitLine: { lineStyle: { color: "rgba(48,54,61,.5)" } },
    },
  };
}
