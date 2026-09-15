export interface Hero {
  code: string;
  name: string;
  day: string;
  close: number;
  premium: number | null;
  gate: "buy" | "pause";
  planned: number;
  pending: number;
  ma5: { above: boolean };
  vol60: number | null;
  target_pos: number | null;
}

export interface AssetGate {
  code: string;
  name: string;
  premium: number | null;
  gate: "buy" | "pause";
}

export interface ArmSummary {
  invested: number;
  pending: number;
  fees: number;
  value: number;
}

export interface Shadow {
  days: number;
  first: string;
  gate: ArmSummary;
  naive: ArmSummary;
  checklist: [boolean, string][];
  curve_days: string[];
  curves: { gate: number[]; naive: number[] };
}

export interface DashboardData {
  generated: string;
  data_asof: string;
  hero: Hero;
  assets: AssetGate[];
  prem_days: string[];
  prem_series: number[];
  px_norm: number[];
  nav_norm: number[];
  shadow: Shadow;
}

export async function fetchDashboard(): Promise<DashboardData> {
  const r = await fetch("/api/dashboard");
  if (!r.ok) throw new Error(`HTTP ${r.status}`);
  return r.json();
}

// ---------------------------------------------------------------- 新分页 API

export interface TrailRow {
  day: string;
  premium: number | null;
  gate: "buy" | "pause";
  planned: number;
}

export interface TodayCard {
  code: string;
  name: string;
  day: string;
  close: number;
  premium: number | null;
  gate: "buy" | "pause";
  planned: number | null;
  pending: number;
  gate_threshold?: number | null;
  plan_configured?: boolean;
  ma5_above: boolean;
  vol60: number | null;
  target_pos: number | null;
  reasoning: string[];
  trail: TrailRow[];
}

export interface Bucket {
  label: string;
  n: number;
  fwd1: number | null;
  fwd5: number | null;
  is_danger: boolean;
}

export interface AnalysisData {
  code: string;
  name: string;
  prem_days: string[];
  prem_series: number[];
  analytics: {
    dist: Record<string, number | null>;
    gt2: number | null;
    gt5: number | null;
    buckets: Bucket[];
  };
  win_days: string[];
  px_norm: number[];
  nav_norm: number[];
}

export interface StrategyResult {
  total: number;
  cagr: number;
  max_dd: number;
  sharpe: number;
  exposure: number;
  trades: number;
  equity: number[];
  yearly: Record<string, number>;
}

export interface DcaRow {
  name: string;
  invested: number;
  value: number;
  xirr: number;
  dd: number;
  buys: number;
  fees: number;
  multiple: number;
}

export interface ReviewData {
  code: string;
  name: string;
  span: [string, string];
  days: string[];
  strategies: Record<string, StrategyResult>;
  dca: DcaRow[];
}

/** 把 FastAPI 的错误体转成可读文字（detail 可能是字符串或校验错误数组）。 */
function errText(body: unknown, status: number): string {
  const d = (body as { detail?: unknown })?.detail;
  if (typeof d === "string") return d;
  if (Array.isArray(d)) {
    return d.map((e) => {
      const x = e as { loc?: unknown[]; msg?: string };
      const where = Array.isArray(x.loc) ? x.loc.join(".") : "";
      return `${where ? where + ": " : ""}${x.msg ?? JSON.stringify(e)}`;
    }).join("；");
  }
  return `HTTP ${status}`;
}

async function getJson<T>(url: string): Promise<T> {
  const r = await fetch(url);
  if (!r.ok) {
    const body = await r.json().catch(() => null);
    throw new Error(errText(body, r.status));
  }
  return r.json();
}

export const fetchToday = (code: string) =>
  getJson<TodayCard>(`/api/today?code=${code}`);
export const fetchAnalysis = (code: string) =>
  getJson<AnalysisData>(`/api/analysis?code=${code}`);
export const fetchReview = (code: string) =>
  getJson<ReviewData>(`/api/review?code=${code}`);

// ---------------------------------------------------------------- 场外/复盘/实时

export interface DecompPeriod {
  price_total?: number;
  nav_total: number;
  nav_total_naive: number;
  underlying_total?: number;
  fx_total?: number;
  resid_total?: number;
  premium_effect?: number;
  span: [string, string];
}

export interface DecompData {
  code: string;
  name: string;
  n: number;
  skipped_events: number;
  event_days?: string[];
  underlying_code?: string | null;
  fx_code?: string | null;
  note?: string;
  period?: DecompPeriod;
  days?: string[];
  contrib_ann?: Record<string, number>;
  corr?: Record<string, number>;
  var_share?: Record<string, number>;
  cum?: Record<string, number[]>;
  resid_var_share?: number;
}

export interface ReplaySummary {
  invested: number;
  value: number;
  fees: number;
  pending: number;
  buys: number;
  pauses: number;
  xirr: number | null;
  max_dd: number;
  avg_buy_premium: number | null;
}

export interface JournalRow {
  day: string;
  premium: number | null;
  gate: "buy" | "pause";
  action: string;
  planned: number;
  bought: number;
  pending: number;
  invested: number;
  value: number;
}

export interface ReplayArm {
  summary: ReplaySummary;
  curve: number[];
  journal: JournalRow[];
}

export interface ReplayData {
  code: string;
  name: string;
  span: [string, string];
  fill: string;
  curve_days: string[];
  arms: Record<string, ReplayArm>;
}

export interface LiveData {
  code: string;
  name: string;
  ts: string;
  price: number | null;
  nav_used: number | null;
  nav_day: string | null;
  premium: number | null;
  gate: "buy" | "pause";
  note: string;
}

export const fetchDecomp = (code: string) =>
  getJson<DecompData>(`/api/decomp?code=${code}`);
export const fetchReplay = (code: string, fill = "close") =>
  getJson<ReplayData>(`/api/replay?code=${code}&fill=${fill}`);
export const fetchLive = (code: string) =>
  getJson<LiveData>(`/api/live?code=${code}`);

export const fmtPct = (x: number | null, digits = 2, sign = true): string =>
  x == null ? "n/a" : sign ? `${(x * 100).toFixed(digits).replace(/^([^-+])/, "+$1")}%` : `${(x * 100).toFixed(digits)}%`;

export const fmtYuan = (x: number): string =>
  Math.round(x).toLocaleString("zh-CN");


// ---------------------------------------------------------------- 标的检索

export interface ResolveInfo {
  code: string;
  name: string;
  kind: string;
  market: string;
  has_price: boolean;
  has_nav: boolean;
  first_price: string | null;
  last_price: string | null;
  first_nav: string | null;
  last_nav: string | null;
  notes: string[];
  ok: boolean;
  in_db: boolean;
}

export interface IngestResult {
  code: string;
  name: string;
  kind: string;
  bars: number;
  navs: number;
  price_span: string;
  nav_span: string;
  prem_rows: number;
  prem_latest: number | null;
}

export interface AssetItem {
  code: string;
  name: string;
  has_price: boolean;
  has_nav: boolean;
  has_premium: boolean;
  span: [string, string];
  bars: number;
  in_universe: boolean;
}

export const resolveCode = (code: string) =>
  getJson<ResolveInfo>(`/api/resolve?code=${encodeURIComponent(code)}`);

export async function ingestCode(code: string): Promise<IngestResult> {
  const r = await fetch(`/api/ingest?code=${encodeURIComponent(code)}`, {
    method: "POST",
  });
  if (!r.ok) {
    const body = await r.json().catch(() => null);
    throw new Error(errText(body, r.status));
  }
  return r.json();
}

export const fetchAssets = () =>
  getJson<{ items: AssetItem[] }>("/api/assets");

// ---------------------------------------------------------------- 我的盘

export interface PlanItem {
  plan: string;
  name: string;
  desc: string;
  defaults: { daily?: number; gate?: number };
}

export interface AccountRow {
  id: number;
  name: string;
  code: string;
  kind: "paper" | "real";
  plan: string;
  plan_label: string;
  params: { daily?: number; gate?: number };
  start_day: string;
  empty?: boolean;
  units?: number;
  invested?: number;
  fees?: number;
  value?: number;
  pnl?: number;
  pnl_pct?: number | null;
  avg_cost?: number | null;
  last_price?: number | null;
  last_day?: string;
  xirr?: number | null;
  trades?: number;
  holdings?: number;
  cash?: number;
  prev_value?: number;
  day_pnl?: number;
  day_pnl_pct?: number | null;
}

export interface OutlookRow {
  day: string;
  weekday: string;
  action: string;
  condition: string;
  amount: number;
  pending_if_pause: number;
  value_low: number | null;
  value_high: number | null;
}

export interface OutlookData {
  account: { id: number; name: string; kind: string; code: string;
             plan: string; start_day: string };
  valuation: Record<string, number | string | null>;
  pending_cash: number;
  code: string;
  plan: string;
  plan_label: string;
  as_of: string;
  current_premium: number | null;
  premium_stat: { bucket: string; n: number; fwd1: number | null;
                  fwd5: number | null } | null;
  vol_ann: number | null;
  sigma_day: number | null;
  rows: OutlookRow[];
  disclaimer: string;
}

export const fetchPlans = () => getJson<{ items: PlanItem[] }>("/api/plans");
export const fetchAccounts = () =>
  getJson<{ items: AccountRow[] }>("/api/accounts");

export async function createAccount(q: {
  name?: string; code: string; plan: string; kind: string;
  start_day?: string; daily?: number; gate?: number;
}): Promise<{ id: number }> {
  const p = new URLSearchParams();
  p.set("code", q.code);
  p.set("plan", q.plan);
  p.set("kind", q.kind);
  if (q.name) p.set("name", q.name);
  if (q.start_day) p.set("start_day", q.start_day);
  if (q.daily != null) p.set("daily", String(q.daily));
  if (q.gate != null) p.set("gate", String(q.gate));
  const r = await fetch(`/api/accounts?${p}`, { method: "POST" });
  if (!r.ok) {
    const body = await r.json().catch(() => null);
    throw new Error(errText(body, r.status));
  }
  return r.json();
}

export async function addTrade(aid: number, q: {
  day: string; price: number; qty: number; action?: string; note?: string;
}): Promise<void> {
  const p = new URLSearchParams({
    day: q.day, price: String(q.price), qty: String(q.qty),
    action: q.action ?? "buy", note: q.note ?? "",
  });
  const r = await fetch(`/api/accounts/${aid}/trade?${p}`, { method: "POST" });
  if (!r.ok) {
    const body = await r.json().catch(() => null);
    throw new Error(errText(body, r.status));
  }
}

export async function deleteAccount(aid: number): Promise<void> {
  await fetch(`/api/accounts/${aid}`, { method: "DELETE" });
}

export const fetchOutlook = (aid: number, n = 3) =>
  getJson<OutlookData>(`/api/accounts/${aid}/outlook?n=${n}`);

// ---------------------------------------------------------------- 价格参考位

export interface LevelItem { offset: number; price: number; mult?: number; frac?: number }

export interface LevelsData {
  code: string;
  name: string;
  as_of: string;
  last: number;
  vol_ann: number | null;
  sigma_day: number | null;
  bands: Record<string, [number, number]>;
  ma: Record<string, number | null>;
  drawdown: Record<string, { days: number; share: number | null; price: number }>;
  premium_now: number | null;
  price_if_premium_2pct: number | null;
  ladder: { buy: LevelItem[]; sell: LevelItem[] };
  empty?: boolean;
  note?: string;
}

export const fetchLevels = (code: string) =>
  getJson<LevelsData>(`/api/levels?code=${code}`);

// ---------------------------------------------------------------- 我的定投参数

export interface MyPlan {
  configured: boolean;
  code?: string;
  daily?: number;
  gate?: number;
  trend_gate?: number;
  dip_threshold?: number;
  dip_mult?: number;
}

export const fetchMyPlan = () => getJson<MyPlan>("/api/my-plan");

export async function saveMyPlan(q: {
  daily: number; code: string; gate: number; trend_gate?: number;
  dip_threshold?: number; dip_mult?: number;
}): Promise<void> {
  const p = new URLSearchParams({
    daily: String(q.daily), code: q.code, gate: String(q.gate),
    trend_gate: String(q.trend_gate ?? 0),
    dip_threshold: String(q.dip_threshold ?? 0),
    dip_mult: String(q.dip_mult ?? 0),
  });
  const r = await fetch(`/api/my-plan?${p}`, { method: "PUT" });
  if (!r.ok) {
    const body = await r.json().catch(() => null);
    throw new Error(errText(body, r.status));
  }
}

// ---------------------------------------------------------------- 参数试算（只读）

export interface PreviewResult {
  code: string;
  start: string;
  daily: number;
  gate: number;
  invested: number;
  value: number;
  pnl: number;
  pnl_pct: number | null;
  xirr: number | null;
  cash: number;
  buys: number;
  planned_days: number;
  pauses: number;
  avg_buy_premium: number | null;
  note: string;
}

export const previewPlan = (q: {
  code: string; daily: number; gate: number; years?: number;
}) => {
  const p = new URLSearchParams({
    code: q.code, daily: String(q.daily), gate: String(q.gate),
    years: String(q.years ?? 3),
  });
  return getJson<PreviewResult>(`/api/my-plan/preview?${p}`);
};

// ---------------------------------------------------------------- 溢价趋势

export interface TrendRow {
  window: string;
  back: number;
  premium_then: number | null;
  premium_change: number | null;
  price_then: number | null;
  price_change: number | null;
}

export interface PremiumTrend {
  code: string;
  name: string;
  as_of: string;
  premium_now: number | null;
  rows: TrendRow[];
  stat: { bucket: string; n: number; fwd5: number } | null;
  note: string;
  empty?: boolean;
}

export const fetchPremiumTrend = (code: string) =>
  getJson<PremiumTrend>(`/api/premium-trend?code=${code}`);
