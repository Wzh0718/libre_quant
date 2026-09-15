<script setup lang="ts">
import { watch } from "vue";
import { useRoute, useRouter } from "vue-router";
import { ASSETS, selectedCode } from "./store";

const route = useRoute();
const router = useRouter();

// URL ?code= ↔ 全局标的 双向同步
watch(() => route.query.code, (q) => {
  if (typeof q === "string" && ASSETS.some(a => a.code === q))
    selectedCode.value = q;
}, { immediate: true });
watch(selectedCode, (c) => {
  if (route.query.code !== c)
    router.replace({ query: { ...route.query, code: c } });
});

const NAV = [
  { to: "/today", label: "今日决策" },
  { to: "/analysis", label: "深度分析" },
  { to: "/review", label: "历史复盘" },
  { to: "/shadow", label: "影子盘" },
];
</script>

<template>
  <main>
    <header style="display:flex;gap:16px;align-items:center;flex-wrap:wrap;
                   padding-bottom:12px;border-bottom:1px solid var(--border)">
      <h1 style="margin-right:auto">libre_quant</h1>
      <nav style="display:flex;gap:4px" aria-label="页面">
        <router-link v-for="n in NAV" :key="n.to" :to="n.to" custom v-slot="{ navigate, isActive }">
          <button class="badge" :class="isActive ? 'badge-buy' : 'badge-hold'"
                  style="cursor:pointer;background:transparent" @click="navigate">
            {{ n.label }}
          </button>
        </router-link>
      </nav>
      <label class="muted" style="display:flex;gap:8px;align-items:center">
        标的
        <select v-model="selectedCode" aria-label="选择标的"
                style="background:var(--surface);color:var(--text);
                       border:1px solid var(--border);border-radius:6px;padding:4px 8px">
          <option v-for="a in ASSETS" :key="a.code" :value="a.code">{{ a.name }}</option>
        </select>
      </label>
    </header>
    <router-view />
    <p class="muted" style="margin-top:24px;font-size:12px">
      口径：溢价=不复权收盘 ÷ 严格早于当日的第 lag 个净值 − 1（写入时配对，无前视）；
      回测成本 0.05%/边；定投佣金=万0.5/最低0.1元。证据：docs/00 策略总纲。
    </p>
  </main>
</template>
