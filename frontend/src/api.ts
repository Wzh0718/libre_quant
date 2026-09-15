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

export const fmtPct = (x: number | null, digits = 2, sign = true): string =>
  x == null ? "n/a" : sign ? `${(x * 100).toFixed(digits).replace(/^([^-+])/, "+$1")}%` : `${(x * 100).toFixed(digits)}%`;

export const fmtYuan = (x: number): string =>
  Math.round(x).toLocaleString("zh-CN");
