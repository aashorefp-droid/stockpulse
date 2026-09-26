/**
 * frontend/lib/format.ts
 * Defensive formatting utilities to prevent unhandled TypeError: ...toFixed is not a function
 * or runtime null/undefined crashes in Next.js React components.
 */

export function fmtNum(val: any, decimals: number = 2, fallback: string = "—"): string {
  if (val == null || val === "" || val === "N/A" || val === "—") return fallback;
  const num = typeof val === "number" ? val : parseFloat(String(val).replace(/[$,%]/g, ""));
  if (isNaN(num) || !isFinite(num)) return fallback;
  return num.toFixed(decimals);
}

export function fmtPrice(val: any, decimals: number = 2, fallback: string = "—"): string {
  const s = fmtNum(val, decimals, fallback);
  return s === fallback ? fallback : `$${s}`;
}

export function fmtPct(val: any, decimals: number = 1, fallback: string = "—"): string {
  const s = fmtNum(val, decimals, fallback);
  return s === fallback ? fallback : `${s}%`;
}
