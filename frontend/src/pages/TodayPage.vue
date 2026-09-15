<script setup lang="ts">
import { onMounted, ref, watch } from "vue";
import {
  fetchLevels, fetchLive, fetchMyPlan, fetchToday, fmtPct, fmtYuan,
  previewPlan, saveMyPlan,
  type LevelsData, type LiveData, type MyPlan, type PreviewResult,
  type TodayCard,
} from "../api";
import { selectedCode } from "../store";

const data = ref<TodayCard | null>(null);
const live = ref<LiveData | null>(null);
const levels = ref<LevelsData | null>(null);
// 我的定投参数（用户输入，系统不发明金额）
const plan = ref<MyPlan>({ configured: false });
const planForm = ref({ daily: 0, gate: 5 });
const planMsg = ref<string | null>(null);
const planErr = ref<string | null>(null);
const preview = ref<PreviewResult | null>(null);
const previewErr = ref<string | null>(null);
const liveErr = ref<string | null>(null);
const error = ref<string | null>(null);
const loading = ref(true);

async function loadLive() {
  liveErr.value = null;
  try {
    live.value = await fetchLive(selectedCode.value);
  } catch (e) {
    liveErr.value = e instanceof Error ? e.message : String(e);
  }
}

async function load() {
  loading.value = true;
  error.value = null;
  try {
    data.value = await fetchToday(selectedCode.value);
    levels.value = await fetchLevels(selectedCode.value).catch(() => null);
    plan.value = await fetchMyPlan();
    if (plan.value.configured) {
      planForm.value = { daily: plan.value.daily ?? 0,
                         gate: (plan.value.gate ?? 0.05) * 100 };
    }
  } catch (e) {
    error.value = e instanceof Error ? e.message : String(e);
  } finally {
    loading.value = false;
  }
  void loadLive();
}
async function runPreview() {
  previewErr.value = null;
  preview.value = null;
  try {
    preview.value = await previewPlan({
      code: selectedCode.value,
      daily: planForm.value.daily,
      gate: planForm.value.gate / 100,
    });
  } catch (e) {
    previewErr.value = e instanceof Error ? e.message : String(e);
  }
}

async function savePlan() {
  planErr.value = null;
  planMsg.value = null;
  try {
    await saveMyPlan({ daily: planForm.value.daily,
                       code: selectedCode.value,
                       gate: planForm.value.gate / 100 });
    planMsg.value = "已保存";
    await load();
  } catch (e) {
    planErr.value = e instanceof Error ? e.message : String(e);
  }
}

onMounted(load);
watch(selectedCode, load);
</script>

<template>
  <div v-if="loading" class="state-block" role="status" aria-busy="true">加载中…</div>
  <div v-else-if="error" class="state-block" role="alert">
    加载失败：{{ error }} <button class="badge badge-hold" style="cursor:pointer" @click="load">重试</button>
  </div>
  <template v-else-if="data">
    <h2>我的定投参数（你自己填，系统不替你决定）</h2>
    <div class="card">
      <div style="display:flex;gap:12px;align-items:flex-end;flex-wrap:wrap">
        <label class="muted">每日金额（元）
          <input v-model.number="planForm.daily" type="number" min="0" step="50"
                 placeholder="例如 200"
                 style="display:block;width:140px;background:var(--bg);color:var(--text);
                        border:1px solid var(--border);border-radius:6px;padding:6px" />
        </label>
        <label class="muted">溢价闸门（%）
          <input v-model.number="planForm.gate" type="number" min="0" max="99" step="0.5"
                 style="display:block;width:120px;background:var(--bg);color:var(--text);
                        border:1px solid var(--border);border-radius:6px;padding:6px" />
          <span style="display:block;max-width:260px;font-size:11px;line-height:1.45;margin-top:4px">
            场内价高于净值的百分比。超过此值当天不买、钱攒着，回落当次补投。
            建议 5%（唯一有三标的实证支持的阈值）。
          </span>
        </label>
        <button class="badge badge-buy" style="cursor:pointer" @click="savePlan">保存</button>
        <button class="badge badge-hold" style="cursor:pointer" @click="runPreview">试算（近3年）</button>
        <span v-if="planMsg" class="muted">✅ {{ planMsg }}</span>
        <span v-if="planErr" style="color:var(--red)">⚠️ {{ planErr }}</span>
      </div>
      <div v-if="preview" class="muted" style="margin-top:10px;font-size:12px">
        <b>试算</b>（{{ preview.start }} 起，只读不改配置）：
        投入 <span class="num">{{ fmtYuan(preview.invested) }}</span> 元 →
        市值 <span class="num">{{ fmtYuan(preview.value) }}</span> 元 ·
        涨跌 <span class="num"
          :style="(preview.pnl_pct ?? 0) >= 0 ? 'color:var(--green)' : 'color:var(--red)'">
          {{ fmtPct(preview.pnl_pct) }}</span> ·
        XIRR {{ fmtPct(preview.xirr) }} ·
        暂停 {{ preview.pauses }}/{{ preview.planned_days }} 天 ·
        买入均价溢价 {{ fmtPct(preview.avg_buy_premium) }} ·
        待投现金 {{ fmtYuan(preview.cash) }} 元
      </div>
      <div v-if="previewErr" style="margin-top:8px;color:var(--red)">⚠️ {{ previewErr }}</div>
      <div v-if="data" class="muted" style="margin-top:8px;font-size:12px">
        当前溢价 <b :style="(data.premium ?? 0) > (planForm.gate / 100)
          ? 'color:var(--red)' : 'color:var(--green)'">{{ fmtPct(data.premium) }}</b>
        · 按你填的 {{ planForm.gate }}% 阈值 →
        {{ (data.premium ?? 0) > planForm.gate / 100 ? "今日暂停买入" : "今日可买入" }}
        <span style="opacity:.75">
          （溢价=场内价÷净值−1；多付的部分会在溢价回归时亏掉）</span>
      </div>
      <div class="muted" style="margin-top:8px;font-size:12px">
        <template v-if="!plan.configured">
          ⚠️ 你还没设置参数 —— 下面的决策卡只陈述溢价状态，<b>不会替你决定投多少</b>。
        </template>
        <template v-else>
          当前：每交易日 <span class="num">{{ planForm.daily }}</span> 元 ·
          闸门 <span class="num">{{ planForm.gate }}%</span> ·
          主目标 <span class="num">{{ plan.code }}</span>
        </template>
      </div>
    </div>

    <h2>今日决策 · {{ data.name }}（{{ data.code }}）· {{ data.day }}</h2>
    <div class="card">
      <div style="display:flex;gap:16px;align-items:center;flex-wrap:wrap">
        <div>
          <span class="big num">{{ fmtPct(data.premium) }}</span>
          <span class="muted"> 当前溢价</span>
        </div>
        <span v-if="data.gate === 'pause'" class="badge badge-pause">暂停买入</span>
        <span v-else class="badge badge-buy">正常买入 {{ fmtYuan(data.planned) }} 元</span>
        <span class="muted">待投现金 {{ fmtYuan(data.pending) }} 元</span>
      </div>
    </div>

    <h2>盘中实时（时效性：收盘前可判定）</h2>
    <div class="card">
      <div v-if="live && live.price" style="display:flex;gap:16px;align-items:center;flex-wrap:wrap">
        <div>
          <span class="big num">{{ fmtPct(live.premium) }}</span>
          <span class="muted"> 实时溢价</span>
        </div>
        <span v-if="live.gate === 'pause'" class="badge badge-pause">暂停买入</span>
        <span v-else class="badge badge-buy">正常买入</span>
        <span class="muted">
          现价 <span class="num">{{ live.price.toFixed(3) }}</span> ·
          最近净值 <span class="num">{{ live.nav_used?.toFixed(4) }}</span>（{{ live.nav_day }}）·
          {{ live.ts }}
        </span>
      </div>
      <div v-else class="muted">
        {{ liveErr ? `实时行情暂不可用（${liveErr}）` : "非交易时段或暂无实时报价" }}
        <button class="badge badge-hold" style="cursor:pointer;margin-left:8px" @click="loadLive">重试</button>
      </div>
      <div class="muted" style="margin-top:8px;font-size:12px">
        {{ live?.note ?? "盘中实时（收盘前参考；日终以入库收盘价为准）" }}
      </div>
    </div>

    <h2>价格参考位（数据算出，非预测）</h2>
    <div class="card">
      <template v-if="levels && !levels.empty">
        <div class="muted" style="font-size:12px">
          最新价 <span class="num">{{ levels.last.toFixed(3) }}</span> ·
          年化波动 {{ fmtPct(levels.vol_ann, 1, false) }}
          <template v-if="levels.premium_now != null">
            · 当前溢价 {{ fmtPct(levels.premium_now) }}
            <template v-if="levels.price_if_premium_2pct">
              → 溢价回到 2% 的等价价
              <span class="num">{{ levels.price_if_premium_2pct.toFixed(3) }}</span>
              （{{ fmtPct(levels.price_if_premium_2pct / levels.last - 1) }}，无需美股下跌）
            </template>
          </template>
        </div>
        <div class="grid cards" style="margin-top:12px">
          <div><div class="muted">±1σ 区间（1 / 3 / 5 日）</div>
            <div class="num" v-for="(b, h) in levels.bands" :key="h">
              {{ h }}日：{{ b[0].toFixed(3) }} ~ {{ b[1].toFixed(3) }}</div></div>
          <div><div class="muted">均线（MA20 / 60 / 120）</div>
            <div class="num" v-for="(v, n) in levels.ma" :key="n">
              MA{{ n }}：{{ v != null ? v.toFixed(3) : "—" }}</div></div>
          <div><div class="muted">近一年回撤触及（从滚动高点）</div>
            <div class="num" v-for="(d, k) in levels.drawdown" :key="k">
              {{ k }}：{{ d.days }} 天（{{ fmtPct(d.share, 0, false) }}）→ {{ d.price.toFixed(3) }}</div></div>
        </div>
        <div class="muted" style="margin-top:12px;font-size:12px">
          阶梯档位（相对最新价）：
          买入
          <span v-for="b in levels.ladder.buy" :key="b.offset" class="num">
            {{ fmtPct(b.offset, 0) }}→{{ b.price.toFixed(3) }} </span>
          · 卖出
          <span v-for="sv in levels.ladder.sell" :key="sv.offset" class="num">
            {{ fmtPct(sv.offset, 0) }}→{{ sv.price.toFixed(3) }} </span>
        </div>
        <div style="margin-top:8px;font-size:12px;color:var(--amber)">
          ⚠️ 实测提醒：把买卖做成价格触发（阶梯/网格/回撤加码）在本标的上
          **全部跑输纯定投**（docs/15：纯阶梯 XIRR +2.21%，加码混合盘 18.6~19.7%，
          纯定投 19.77%），每降 1.9pp 回撤要付 1.14pp 年化。价位适合当"贵不贵"的刻度，
          不适合当挂单指令。
        </div>
      </template>
      <div v-else class="muted">{{ levels?.note ?? "暂无价位数据" }}</div>
    </div>

    <h2>决策推理链（每天投递的思路）</h2>
    <div class="card">
      <ol style="padding-left: 20px">
        <li v-for="(r, i) in data.reasoning" :key="i" style="margin: 8px 0">{{ r }}</li>
      </ol>
    </div>

    <h2>近期信号轨迹</h2>
    <div class="card">
      <table v-if="data.trail.length">
        <thead><tr><th>日期</th><th>溢价</th><th>判定</th><th>计划</th></tr></thead>
        <tbody>
          <tr v-for="t in [...data.trail].reverse()" :key="t.day">
            <td class="num">{{ t.day }}</td>
            <td class="num">{{ fmtPct(t.premium) }}</td>
            <td>
              <span v-if="t.gate === 'pause'" class="badge badge-pause">暂停</span>
              <span v-else class="badge badge-buy">买入</span>
            </td>
            <td class="num">{{ fmtYuan(t.planned) }} 元</td>
          </tr>
        </tbody>
      </table>
      <div v-else class="muted">该标的未纳入影子盘（仅 159941 有逐日信号轨迹）</div>
    </div>
  </template>
</template>
