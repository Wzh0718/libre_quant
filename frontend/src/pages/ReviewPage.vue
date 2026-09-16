<script setup lang="ts">
import { computed, onMounted, ref, watch } from "vue";
import {
  compareStrategies, createStrategy, fetchStrategies, fmtPct, fmtYuan,
  runReview, type ReviewRunResult, type StrategyRow,
} from "../api";
import MultiLineChart from "../components/MultiLineChart.vue";
import { selectedCode } from "../store";
import { CHART_COLORS } from "../useChart";

const form = ref({
  daily: 200, premium_max: 5, dip_drop: -5, dip_mult: 2,
  rise_gain: 10, sell_pct: 20, vol_target: 0,
});
const result = ref<ReviewRunResult | null>(null);
const strategies = ref<StrategyRow[]>([]);
const compareItems = ref<StrategyRow[]>([]);
const picked = ref<number[]>([]);
const running = ref(false);
const err = ref<string | null>(null);
const msg = ref<string | null>(null);
const saveName = ref("");
const saveNote = ref("");
const basedOn = ref<number | null>(null);

function paramsPayload() {
  return {
    daily: form.value.daily || null,
    premium_max: form.value.premium_max > 0 ? form.value.premium_max / 100 : null,
    dip_drop: form.value.dip_drop ? form.value.dip_drop / 100 : null,
    dip_mult: form.value.dip_mult || null,
    rise_gain: form.value.rise_gain > 0 ? form.value.rise_gain / 100 : null,
    sell_pct: form.value.sell_pct > 0 ? form.value.sell_pct / 100 : null,
    vol_target: form.value.vol_target > 0 ? form.value.vol_target / 100 : null,
  };
}

async function run() {
  running.value = true;
  err.value = null;
  msg.value = null;
  try {
    result.value = await runReview(selectedCode.value, paramsPayload());
  } catch (e) {
    err.value = e instanceof Error ? e.message : String(e);
  } finally {
    running.value = false;
  }
}

async function saveVersion() {
  if (!saveName.value.trim()) { err.value = "给这个版本起个名字"; return; }
  err.value = null;
  try {
    const r = await createStrategy({
      code: selectedCode.value, name: saveName.value.trim(),
      params: paramsPayload(), note: saveNote.value, parentId: basedOn.value });
    msg.value = `已存为版本 #${r.id}，可到「作战台」或「策略库」用它`;
    saveName.value = "";
    saveNote.value = "";
    await loadStrategies();
  } catch (e) {
    err.value = e instanceof Error ? e.message : String(e);
  }
}

function loadVersion(s: StrategyRow) {
  form.value = {
    daily: s.params.daily ?? 0,
    premium_max: (s.params.premium_max ?? 0) * 100,
    dip_drop: (s.params.dip_drop ?? 0) * 100,
    dip_mult: s.params.dip_mult ?? 0,
    rise_gain: (s.params.rise_gain ?? 0) * 100,
    sell_pct: (s.params.sell_pct ?? 0) * 100,
    vol_target: (s.params.vol_target ?? 0) * 100,
  };
  basedOn.value = s.id;
  msg.value = `已把 #${s.id} ${s.name} 的参数填进表单，可直接改或跑一遍`;
}

async function loadStrategies() {
  strategies.value = (await fetchStrategies(selectedCode.value)).items;
}

async function doCompare() {
  err.value = null;
  try {
    compareItems.value = (await compareStrategies(picked.value)).items;
  } catch (e) {
    err.value = e instanceof Error ? e.message : String(e);
  }
}

const resultSeries = computed(() => result.value
  ? [{ name: "当前表单参数", values: result.value.curve, color: CHART_COLORS.green }]
  : []);

const compareSeries = computed(() =>
  compareItems.value.filter(s => s.backtest).map((s, i) => ({
    name: `#${s.id} ${s.name}`,
    values: s.backtest!.curve,
    color: [CHART_COLORS.green, CHART_COLORS.blue, CHART_COLORS.amber,
            CHART_COLORS.red][i % 4],
  })));

const compareDays = computed(() =>
  compareItems.value.find(s => s.backtest)?.backtest?.curve_days ?? []);

onMounted(loadStrategies);
watch(selectedCode, () => {
  result.value = null;
  compareItems.value = [];
  picked.value = [];
  basedOn.value = null;
  loadStrategies();
});
</script>

<template>
  <h2>复盘：调参数 → 跑历史 → 存版本</h2>

  <div class="card">
    <div class="grid" style="grid-template-columns:repeat(auto-fit,minmax(170px,1fr))">
      <label class="muted">每天买入（元）
        <input v-model.number="form.daily" type="number" step="50" class="pinput" />
      </label>
      <label class="muted">溢价超过（%）就停
        <input v-model.number="form.premium_max" type="number" step="0.5" class="pinput" />
      </label>
      <label class="muted">跌超过（%）多买
        <input v-model.number="form.dip_drop" type="number" step="1" class="pinput" />
      </label>
      <label class="muted">多买几倍
        <input v-model.number="form.dip_mult" type="number" step="0.5" min="0" class="pinput" />
      </label>
      <label class="muted">涨超过（%）卖出
        <input v-model.number="form.rise_gain" type="number" step="1" class="pinput" />
      </label>
      <label class="muted">卖出持仓（%）
        <input v-model.number="form.sell_pct" type="number" step="5" min="0" max="100" class="pinput" />
      </label>
      <label class="muted">波动目标（%，0=不定仓）
        <input v-model.number="form.vol_target" type="number" step="1" min="0" class="pinput" />
      </label>
    </div>
    <div class="muted" style="margin-top:8px;font-size:12px">
      跌/涨按近 7 天（前复权）判断；溢价闸门只对 QDII 生效；填 0 = 不启用该条。
      <span v-if="basedOn" style="color:var(--amber)">当前基于版本 #{{ basedOn }} 修改</span>
    </div>
    <div style="margin-top:12px;display:flex;gap:12px;align-items:center;flex-wrap:wrap">
      <button class="badge badge-buy" style="cursor:pointer" :disabled="running" @click="run">
        {{ running ? "计算中…" : "跑一遍历史" }}</button>
      <template v-if="result">
        <input v-model="saveName" placeholder="版本名，如：收紧多买档"
               style="background:var(--bg);color:var(--text);border:1px solid var(--border);
                      border-radius:6px;padding:6px 10px;min-width:180px" />
        <input v-model="saveNote" placeholder="备注（为什么这么改）"
               style="flex:1;min-width:200px;background:var(--bg);color:var(--text);
                      border:1px solid var(--border);border-radius:6px;padding:6px 10px" />
        <button class="badge badge-hold" style="cursor:pointer" @click="saveVersion">
          存为策略版本</button>
      </template>
      <span v-if="msg" class="muted">✅ {{ msg }}</span>
    </div>
    <div v-if="err" style="margin-top:8px;color:var(--red)">⚠️ {{ err }}</div>
  </div>

  <template v-if="result">
    <div class="grid cards" style="margin-top:12px">
      <div class="card"><div class="muted">总共投入</div>
        <div class="big num">{{ fmtYuan(result.invested) }}</div></div>
      <div class="card"><div class="muted">现在值</div>
        <div class="big num">{{ fmtYuan(result.value) }}</div></div>
      <div class="card"><div class="muted">赚了</div>
        <div class="big num"
             :style="(result.profit_pct ?? 0) >= 0 ? 'color:var(--green)' : 'color:var(--red)'">
          {{ fmtPct(result.profit_pct) }}</div></div>
      <div class="card"><div class="muted">最差时亏</div>
        <div class="big num" style="color:var(--red)">
          {{ fmtPct(result.max_dd, 1, false) }}</div></div>
      <div class="card"><div class="muted">买入 / 卖出 / 不买</div>
        <div class="num">{{ result.buys }} / {{ result.sells }} / {{ result.skips }} 次</div></div>
    </div>
    <div class="card" style="margin-top:12px">
      <MultiLineChart :days="result.curve_days" :series="resultSeries"
                      aria-label="当前参数的历史账户价值" />
    </div>
  </template>

  <h2 style="margin-top:20px">策略版本（{{ strategies.length }}）</h2>
  <div class="card" style="overflow-x:auto">
    <table v-if="strategies.length">
      <thead><tr>
        <th></th><th>#</th><th>名称</th><th>参数</th><th>基于</th><th>创建时间</th><th>操作</th>
      </tr></thead>
      <tbody>
        <tr v-for="s in strategies" :key="s.id">
          <td><input type="checkbox" :value="s.id" v-model="picked" /></td>
          <td class="num">{{ s.id }}</td>
          <td>{{ s.name }}<div v-if="s.note" class="muted" style="font-size:12px">{{ s.note }}</div></td>
          <td class="muted" style="font-size:12px">{{ s.params_label }}</td>
          <td class="num">{{ s.parent_id ? `#${s.parent_id}` : "—" }}</td>
          <td class="num muted" style="font-size:12px">{{ s.created_at.slice(0, 16) }}</td>
          <td style="white-space:nowrap">
            <button class="badge badge-hold" style="cursor:pointer" @click="loadVersion(s)">填进表单</button>
            <router-link :to="`/battle?strategy_id=${s.id}`">
              <button class="badge badge-buy" style="cursor:pointer">生成作战方案</button>
            </router-link>
          </td>
        </tr>
      </tbody>
    </table>
    <div v-else class="muted">还没有版本 —— 上面调参跑一遍，满意后存下来。</div>
    <div style="margin-top:10px">
      <button class="badge badge-hold" style="cursor:pointer"
              :disabled="picked.length < 2" @click="doCompare">
        对比勾选的版本（{{ picked.length }}）</button>
    </div>
  </div>

  <template v-if="compareItems.length">
    <h2>版本对比（同标的同口径）</h2>
    <div class="card" style="overflow-x:auto">
      <table>
        <thead><tr>
          <th>版本</th><th>投入</th><th>现值</th><th>收益</th><th>最大回撤</th><th>买/卖/不买</th>
        </tr></thead>
        <tbody>
          <tr v-for="s in compareItems" :key="s.id">
            <td>#{{ s.id }} {{ s.name }}</td>
            <template v-if="s.backtest">
              <td class="num">{{ fmtYuan(s.backtest.invested) }}</td>
              <td class="num">{{ fmtYuan(s.backtest.value) }}</td>
              <td class="num"
                  :style="(s.backtest.profit_pct ?? 0) >= 0 ? 'color:var(--green)' : 'color:var(--red)'">
                {{ fmtPct(s.backtest.profit_pct) }}</td>
              <td class="num" style="color:var(--red)">
                {{ fmtPct(s.backtest.max_dd, 1, false) }}</td>
              <td class="num">{{ s.backtest.buys }} / {{ s.backtest.sells }} /
                {{ s.backtest.skips }}</td>
            </template>
            <td v-else colspan="5" class="muted">无数据</td>
          </tr>
        </tbody>
      </table>
      <div style="margin-top:12px" v-if="compareSeries.length">
        <MultiLineChart :days="compareDays" :series="compareSeries"
                        aria-label="版本对比曲线" />
      </div>
    </div>
  </template>
</template>

<style scoped>
.pinput {
  width: 100%;
  background: var(--bg);
  color: var(--text);
  border: 1px solid var(--border);
  border-radius: 6px;
  padding: 6px;
}
</style>
