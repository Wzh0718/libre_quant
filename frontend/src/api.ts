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
  planned: number;
  pending: number;
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

async function getJson<T>(url: string): Promise<T> {
  const r = await fetch(url);
  if (!r.ok) throw new Error(`HTTP ${r.status}`);
  return r.json();
}

export const fetchToday = (code: string) =>
  getJson<TodayCard>(`/api/today?code=${code}`);
export const fetchAnalysis = (code: string) =>
  getJson<AnalysisData>(`/api/analysis?code=${code}`);
export const fetchReview = (code: string) =>
  getJson<ReviewData>(`/api/review?code=${code}`);

export const fmtPct = (x: number | null, digits = 2, sign = true): string =>
  x == null ? "n/a" : sign ? `${(x * 100).toFixed(digits).replace(/^([^-+])/, "+$1")}%` : `${(x * 100).toFixed(digits)}%`;

export const fmtYuan = (x: number): string =>
  Math.round(x).toLocaleString("zh-CN");
