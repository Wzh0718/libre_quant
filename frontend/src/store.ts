import { ref } from "vue";
import { fetchAssets, type AssetItem } from "./api";

/** 首选清单（库未就绪/请求失败时的兜底） */
export const DEFAULT_ASSETS = [
  { code: "159941", name: "纳指ETF广发（主战场）" },
  { code: "513100", name: "纳指ETF" },
  { code: "513500", name: "标普500ETF" },
  { code: "515880", name: "通信ETF" },
];

/** 库中已有数据的标的（动态：用户检索入库后自动出现在这里） */
export const assets = ref<{ code: string; name: string }[]>(DEFAULT_ASSETS);

export async function refreshAssets() {
  try {
    const { items } = await fetchAssets();
    if (items.length) {
      assets.value = items.map((i: AssetItem) => ({
        code: i.code,
        name: i.name + (i.has_premium ? "" : i.has_price ? "" : "（场外净值）"),
      }));
    }
  } catch {
    /* 忽略：保留兜底清单 */
  }
}

/** @deprecated 兼容旧引用 */
export const ASSETS = DEFAULT_ASSETS;

/** 全局选中的标的（与 URL ?code= 双向同步，同步逻辑在 App.vue） */
export const selectedCode = ref<string>("159941");
