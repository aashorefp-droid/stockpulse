export interface OtmContract {
  type:         string;
  volume:       number;
  oi:           number;
  otm_pct:      number;
  vol_oi_ratio: number;
  unusual:      boolean;
}

export function interpretOtmFlow(contracts: OtmContract[] | undefined | null): string {
  if (!contracts || contracts.length === 0) return "";

  const calls = contracts.filter(c => c.type === "CALL");
  const puts  = contracts.filter(c => c.type === "PUT");

  const callVol = calls.reduce((s, c) => s + c.volume, 0);
  const putVol  = puts.reduce((s, c) => s + c.volume, 0);
  const cpRatio = putVol > 0 ? callVol / putVol : (callVol > 0 ? 99 : 0);

  const avgVolOi = contracts.reduce((s, c) => s + c.vol_oi_ratio, 0) / contracts.length;
  const avgOtm   = contracts.reduce((s, c) => s + c.otm_pct, 0) / contracts.length;

  const tone =
    cpRatio >= 3    ? "🟢 Strong bullish"  :
    cpRatio >= 1.5  ? "🟢 Bullish skew"    :
    cpRatio <= 0.33 ? "🔴 Strong bearish"  :
    cpRatio <= 0.67 ? "🔴 Bearish skew"    :
                      "🟡 Mixed";

  const conviction =
    avgVolOi >= 5 ? "fresh sweeping"     :
    avgVolOi >= 2 ? "fresh positioning"  :
                    "active";

  const strike =
    avgOtm >= 20 ? "deep-OTM lottery"   :
    avgOtm >= 5  ? "OTM directional"    :
                   "near-ATM";

  const cpDisplay = cpRatio >= 99 ? "calls only" : `C/P ${cpRatio.toFixed(1)}:1`;

  return `${tone} — ${conviction}, ${strike} (${cpDisplay}, avg Vol/OI ${avgVolOi.toFixed(1)}×)`;
}
