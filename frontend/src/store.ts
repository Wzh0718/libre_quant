import { ref } from "vue";

export const ASSETS = [
  { code: "159941", name: "纳指ETF广发（主战场）" },
  { code: "513100", name: "纳指ETF" },
  { code: "513500", name: "标普500ETF" },
  { code: "515880", name: "通信ETF" },
];

/** 全局选中的标的（与 URL ?code= 双向同步，同步逻辑在 App.vue） */
export const selectedCode = ref<string>("159941");
