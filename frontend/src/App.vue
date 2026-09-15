<script setup lang="ts">
import { onMounted, ref, watch } from "vue";
import { useRoute, useRouter } from "vue-router";
import { ingestCode, resolveCode, type ResolveInfo } from "./api";
import { assets, refreshAssets, selectedCode } from "./store";

const route = useRoute();
const router = useRouter();

// URL ?code= ↔ 全局标的 双向同步
watch(() => route.query.code, (q) => {
  if (typeof q === "string" && q) selectedCode.value = q;
}, { immediate: true });
watch(selectedCode, (c) => {
  if (route.query.code !== c)
    router.replace({ query: { ...route.query, code: c } });
});

const NAV = [
  { to: "/today", label: "今日决策" },
  { to: "/analysis", label: "深度分析" },
  { to: "/replay", label: "复盘模拟" },
  { to: "/review", label: "历史复盘" },
  { to: "/shadow", label: "影子盘" },
  { to: "/accounts", label: "我的盘" },
];

// ---- 标的检索（输入基金/股票代码 → 拉历史 → 跑盘）
const query = ref("");
const busy = ref(false);
const info = ref<ResolveInfo | null>(null);
const msg = ref<string | null>(null);
const err = ref<string | null>(null);

async function search() {
  const code = query.value.trim();
  if (!code) return;
  busy.value = true;
  msg.value = null;
  err.value = null;
  info.value = null;
  try {
    const r = await resolveCode(code);
    info.value = r;
    if (!r.ok) {
      err.value = `未取到数据：${r.notes.join("；") || "未知原因"}`;
      return;
    }
    if (!r.in_db || r.has_nav === false) {
      msg.value = `正在拉取 ${r.name} 的历史数据…`;
      const got = await ingestCode(code);
      msg.value = `已入库：价格 ${got.bars} 根${got.navs ? ` · 净值 ${got.navs} 条` : ""}` +
        (got.prem_rows ? ` · 溢价 ${got.prem_rows} 行` : "");
    } else {
      msg.value = `已在库中：${r.name}`;
    }
    await refreshAssets();
    selectedCode.value = r.code;
  } catch (e) {
    err.value = e instanceof Error ? e.message : String(e);
  } finally {
    busy.value = false;
  }
}

onMounted(async () => {
  await refreshAssets();
  if (!assets.value.some(a => a.code === selectedCode.value))
    selectedCode.value = assets.value[0]?.code ?? "159941";
});
</script>

<template>
  <main>
    <header style="display:flex;gap:12px;align-items:center;flex-wrap:wrap;
                   padding-bottom:12px;border-bottom:1px solid var(--border)">
      <h1 style="margin-right:auto">libre_quant</h1>
      <nav style="display:flex;gap:4px" aria-label="页面">
        <router-link v-for="n in NAV" :key="n.to" :to="n.to" custom
                     v-slot="{ navigate, isActive }">
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
          <option v-for="a in assets" :key="a.code" :value="a.code">
            {{ a.name }}（{{ a.code }}）
          </option>
        </select>
      </label>
    </header>

    <div class="card" style="margin-top:12px">
      <div style="display:flex;gap:8px;align-items:center;flex-wrap:wrap">
        <strong>标的检索</strong>
        <span class="muted">输入基金 / 股票代码，自动拉取历史并跑盘</span>
        <input v-model="query" list="known-codes" placeholder="如 159941 / 600519 / 270042 / QQQ"
               style="flex:1;min-width:220px;background:var(--bg);color:var(--text);
                      border:1px solid var(--border);border-radius:6px;padding:6px 10px"
               @keyup.enter="search" />
        <datalist id="known-codes">
          <option v-for="a in assets" :key="a.code" :value="a.code">{{ a.name }}</option>
        </datalist>
        <button class="badge badge-buy" style="cursor:pointer" :disabled="busy"
                @click="search">{{ busy ? "检索中…" : "检索/入库" }}</button>
      </div>
      <div v-if="msg" class="muted" style="margin-top:8px">✅ {{ msg }}</div>
      <div v-if="err" style="margin-top:8px;color:var(--red)">⚠️ {{ err }}</div>
      <div v-if="info" class="muted" style="margin-top:8px;font-size:12px">
        {{ info.name }} · 类别 {{ info.kind }} · 市场 {{ info.market }} ·
        {{ info.has_price ? `行情 ${info.first_price} ~ ${info.last_price}` : "无场内行情" }}
        {{ info.has_nav ? ` · 净值 ${info.first_nav} ~ ${info.last_nav}` : "" }}
        {{ info.notes.length ? " · " + info.notes.join("；") : "" }}
      </div>
    </div>

    <router-view />

    <p class="muted" style="margin-top:24px;font-size:12px">
      口径：溢价=不复权收盘 ÷ 严格早于当日的第 lag 个净值 − 1（写入时配对，无前视）；
      回测成本 0.05%/边；定投佣金=万0.5/最低0.1元。证据：docs/00 策略总纲。
    </p>
  </main>
</template>
