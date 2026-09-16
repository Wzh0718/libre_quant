<script setup lang="ts">
import { computed, onMounted, ref, watch } from "vue";
import { useRoute } from "vue-router";
import {
  fetchAccounts, fetchBattle, fetchStrategies, fmtPct, fmtYuan,
  saveBattlePlan, type AccountRow, type BattleData, type StrategyRow,
} from "../api";
import MultiLineChart from "../components/MultiLineChart.vue";
import { selectedCode } from "../store";
import { CHART_COLORS } from "../useChart";

const route = useRoute();
const data = ref<BattleData | null>(null);
const accounts = ref<AccountRow[]>([]);
const strategies = ref<StrategyRow[]>([]);
const accountId = ref<number | null>(null);
const strategyId = ref<number | null>(null);
const loading = ref(true);
const err = ref<string | null>(null);
const msg = ref<string | null>(null);
const saving = ref(false);

const realAccounts = computed(() => accounts.value.filter(a => a.kind === "real"));

async function load() {
  loading.value = true;
  err.value = null;
  try {
    const [accs, strats] = await Promise.all([
      fetchAccounts(), fetchStrategies(selectedCode.value)]);
    accounts.value = accs.items;
    strategies.value = strats.items;
    if (accountId.value == null) {
      const sameCode = realAccounts.value.find(a => a.code === selectedCode.value);
      accountId.value = sameCode?.id ?? realAccounts.value[0]?.id ?? null;
    }
    // URL ?strategy_id= 优先（策略库页跳转过来时）
    const q = Number(route.query.strategy_id);
    if (strategyId.value == null && Number.isInteger(q) && q > 0)
      strategyId.value = q;
    data.value = await fetchBattle(selectedCode.value, {
      accountId: accountId.value, strategyId: strategyId.value });
  } catch (e) {
    data.value = null;
    err.value = e instanceof Error ? e.message : String(e);
  } finally {
    loading.value = false;
  }
}

async function save() {
  if (accountId.value == null || !data.value) return;
  saving.value = true;
  msg.value = null;
  try {
    const r = await saveBattlePlan({
      code: selectedCode.value, accountId: accountId.value,
      strategyId: strategyId.value,
      params: strategyId.value == null ? data.value.params : undefined });
    msg.value = `已落盘为方案 #${r.plan_id}（截至 ${r.as_of}）—— 一周后到「我的盘」看计划 vs 实际`;
  } catch (e) {
    err.value = e instanceof Error ? e.message : String(e);
  } finally {
    saving.value = false;
  }
}

const histSeries = computed(() => data.value?.history
  ? [{ name: "这套规则的历史账户价值", values: data.value.history.curve,
       color: CHART_COLORS.green }]
  : []);

const strategyName = computed(() =>
  strategies.value.find(s => s.id === strategyId.value)?.name
  ?? (strategyId.value == null ? "当前默认参数" : `#${strategyId.value}`));

onMounted(load);
watch(selectedCode, () => { strategyId.value = null; load(); });
watch([accountId, strategyId], load);
</script>

<template>
  <div v-if="loading" class="state-block" role="status" aria-busy="true">计算中…</div>
  <div v-else-if="err" class="state-block" role="alert">
    <p>出错了：{{ err }}</p>
    <p class="muted" style="font-size:13px">
      还没设参数？先去<router-link to="/review">复盘页</router-link>填参数跑历史，
      或在<router-link to="/strategies">策略库</router-link>选一个版本。
      <button class="badge badge-hold" style="cursor:pointer" @click="load">重试</button>
    </p>
  </div>
  <template v-else-if="data">
    <!-- 抬头：标的 / 数据截至 / 用哪个盘算持仓 / 用哪版策略 -->
    <div class="card" style="display:flex;gap:16px;align-items:center;flex-wrap:wrap;
                             margin-bottom:12px">
      <strong>{{ data.name }}（{{ data.code }}）</strong>
      <span class="muted">数据截至 {{ data.as_of }}</span>
      <label class="muted" style="margin-left:auto">持仓来自
        <select v-model.number="accountId" style="background:var(--surface);color:var(--text);
                border:1px solid var(--border);border-radius:6px;padding:4px 8px;margin-left:6px">
          <option :value="null">不关联实际盘</option>
          <option v-for="a in realAccounts" :key="a.id" :value="a.id">
            #{{ a.id }} {{ a.name }}</option>
        </select>
      </label>
      <label class="muted">策略版本
        <select v-model.number="strategyId" style="background:var(--surface);color:var(--text);
                border:1px solid var(--border);border-radius:6px;padding:4px 8px;margin-left:6px">
          <option :value="null">默认参数（我的计划）</option>
          <option v-for="s in strategies" :key="s.id" :value="s.id">
            #{{ s.id }} {{ s.name }}</option>
        </select>
      </label>
      <button class="badge badge-buy" style="cursor:pointer"
              :disabled="saving || accountId == null" @click="save">
        {{ saving ? "落盘中…" : "落盘为本周方案" }}</button>
    </div>
    <div v-if="msg" class="card muted" style="margin-bottom:12px">✅ {{ msg }}</div>

    <!-- 态势（事实，不是预测） -->
    <h2>当前态势</h2>
    <div class="grid cards">
      <div class="card"><div class="muted">现价（{{ data.situation.day }}）</div>
        <div class="big num">{{ data.situation.close.toFixed(3) }}</div></div>
      <div class="card"><div class="muted">近 7 天 / 近 14 天</div>
        <div class="num" style="font-size:18px"
             :style="(data.situation.change_7d ?? 0) >= 0 ? 'color:var(--green)' : 'color:var(--red)'">
          {{ fmtPct(data.situation.change_7d ?? null) }}</div>
        <div class="muted num" style="font-size:13px"
             :style="(data.situation.change_14d ?? 0) >= 0 ? 'color:var(--green)' : 'color:var(--red)'">
          {{ fmtPct(data.situation.change_14d ?? null) }}</div></div>
      <div class="card" v-if="data.situation.ma['20']">
        <div class="muted">均线距离（20 / 60 / 120）</div>
        <div class="num" style="font-size:14px;line-height:1.7">
          <span v-for="w in ['20', '60', '120']" :key="w" style="margin-right:10px">
            MA{{ w }}
            <b :style="(data.situation.ma[w]?.dist ?? 0) >= 0 ? 'color:var(--green)' : 'color:var(--red)'">
              {{ fmtPct(data.situation.ma[w]?.dist ?? null, 1) }}</b>
          </span>
        </div></div>
      <div class="card"><div class="muted">年化波动 / 距年内高点</div>
        <div class="num" style="font-size:18px">
          {{ data.situation.vol_ann ? (data.situation.vol_ann * 100).toFixed(0) + "%" : "—" }}</div>
        <div class="muted num" style="font-size:13px;color:var(--red)">
          {{ fmtPct(data.situation.drawdown_from_high ?? null, 1, false) }}</div></div>
      <div class="card" v-if="data.situation.premium != null">
        <div class="muted">当前溢价</div>
        <div class="num" style="font-size:18px">{{ fmtPct(data.situation.premium) }}</div></div>
      <div class="card" v-if="data.position.units > 0">
        <div class="muted">你的持仓</div>
        <div class="num" style="font-size:18px">{{ data.position.units.toFixed(0) }} 份</div>
        <div class="muted num" style="font-size:13px">
          成本 {{ data.position.avg_cost?.toFixed(3) ?? "—" }}</div></div>
    </div>

    <!-- 规则说明 -->
    <h2>本周规则 <span class="muted" style="font-size:13px;font-weight:normal">
      （{{ strategyName }}：{{ data.params_label }}）</span></h2>
    <div class="card">
      <ol style="margin:0;padding-left:20px">
        <li v-for="(r, i) in data.rules" :key="i" style="margin:4px 0">{{ r }}</li>
      </ol>
      <div class="muted" style="margin-top:8px;font-size:12px">{{ data.disclaimer }}</div>
    </div>

    <!-- 未来一周条件动作单 -->
    <h2>未来 {{ data.horizon }} 个交易日 · 条件动作单</h2>
    <div class="card" style="overflow-x:auto">
      <table>
        <thead>
          <tr>
            <th>日期</th>
            <th v-if="data.rows.some(r => r.base)">基准买入</th>
            <th v-if="data.rows.some(r => r.dip)">多买触发（收盘 ≤）</th>
            <th v-if="data.rows.some(r => r.sell)">卖出触发（收盘 ≥）</th>
            <th>±1σ 参考区间</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="r in data.rows" :key="r.day">
            <td class="num">{{ r.day }} 周{{ r.weekday }}</td>
            <td v-if="data.rows.some(x => x.base)" class="num">
              <template v-if="r.base">
                买 {{ r.base.amount.toFixed(0) }} 元
                <span class="muted">≈ {{ r.base.shares_est.toFixed(0) }} 份</span>
              </template>
              <span v-else class="muted">—</span>
            </td>
            <td v-if="data.rows.some(x => x.dip)" class="num">
              <template v-if="r.dip">
                <b style="color:var(--green)">{{ r.dip.trigger.toFixed(3) }}</b>
                → 买 {{ r.dip.amount.toFixed(0) }} 元
                <span class="muted">≈ {{ r.dip.shares_est.toFixed(0) }} 份</span>
              </template>
              <span v-else class="muted">—</span>
            </td>
            <td v-if="data.rows.some(x => x.sell)" class="num">
              <template v-if="r.sell">
                <b style="color:var(--red)">{{ r.sell.trigger.toFixed(3) }}</b>
                → 卖 {{ (r.sell.pct * 100).toFixed(0) }}%
                <span v-if="r.sell.qty" class="muted">
                  ≈ {{ r.sell.qty.toFixed(0) }} 份 / {{ r.sell.amount_est?.toFixed(0) }} 元</span>
              </template>
              <span v-else class="muted">—</span>
            </td>
            <td class="num muted">
              {{ r.band ? `${r.band[0].toFixed(3)} ~ ${r.band[1].toFixed(3)}` : "—" }}
            </td>
          </tr>
        </tbody>
      </table>
      <div class="muted" style="margin-top:8px;font-size:12px">
        卖出数量按你当前持仓 {{ data.position.units.toFixed(0) }} 份算；
        触发价由「7 日前收盘价 × 档位」精确算出，与回测同一判定口径。
        溢价闸门在开盘前看当日溢价，超过阈值当天什么都不做。
      </div>
    </div>

    <!-- 同参数历史表现 -->
    <h2>这套规则跑全程历史</h2>
    <template v-if="data.history">
      <div class="grid cards">
        <div class="card"><div class="muted">总共投入</div>
          <div class="big num">{{ fmtYuan(data.history.invested) }}</div></div>
        <div class="card"><div class="muted">现在值</div>
          <div class="big num">{{ fmtYuan(data.history.value) }}</div></div>
        <div class="card"><div class="muted">赚了</div>
          <div class="big num"
               :style="(data.history.profit_pct ?? 0) >= 0 ? 'color:var(--green)' : 'color:var(--red)'">
            {{ fmtPct(data.history.profit_pct) }}</div>
          <div class="muted num" style="font-size:12px">{{ fmtYuan(data.history.profit) }} 元</div></div>
        <div class="card"><div class="muted">最差时亏</div>
          <div class="big num" style="color:var(--red)">
            {{ fmtPct(data.history.max_dd, 1, false) }}</div></div>
        <div class="card"><div class="muted">买入 / 卖出 / 不买</div>
          <div class="num">{{ data.history.buys }} / {{ data.history.sells }} /
            {{ data.history.skips }} 次</div></div>
      </div>
      <div class="card" style="margin-top:12px">
        <MultiLineChart :days="data.history.curve_days" :series="histSeries"
                        aria-label="这套规则的历史账户价值" />
      </div>
      <p class="muted" style="font-size:13px">
        想改参数？去<router-link to="/review">复盘页</router-link>调参重跑，
        满意后存成新版本，再回到这里用它生成方案。
      </p>
    </template>
    <div v-else class="card muted">设了「每天买入」后，这里显示这套规则的历史结果。</div>
  </template>
</template>
