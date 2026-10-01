"use client";

interface VolumeProfile {
  poc:       number;
  vah:       number;
  val:       number;
  vol_bias:  string;
  vol_trend: string;
  vol_ratio: number;
  vol_surge: boolean;
  detail:    string;
  hi_52?:    number;
  lo_52?:    number;
  dist_hi_52?: number;
  dist_lo_52?: number;
}

const trendColor: Record<string, string> = {
  ACCUMULATING: "text-green",
  DISTRIBUTING: "text-red",
  FLAT:         "text-muted",
};

const biasColor: Record<string, string> = {
  BULLISH: "badge-bullish",
  BEARISH: "badge-bearish",
  NEUTRAL: "badge-neutral",
};

interface VolumeProfileCardProps {
  vp: VolumeProfile;
  currentPrice: number;
  hi52?: number | null;
  lo52?: number | null;
  distFromHigh?: number | null;
  distFromLow?: number | null;
  finalJudgement?: any;
}

export default function VolumeProfileCard({
  vp,
  currentPrice,
  hi52,
  lo52,
  distFromHigh,
  distFromLow,
  finalJudgement,
}: VolumeProfileCardProps) {
  const aboveVah = currentPrice > vp.vah;
  const belowVal = currentPrice < vp.val;
  const inVA     = !aboveVah && !belowVal;

  const resolvedHi52 = (typeof vp.hi_52 === "number" && !isNaN(vp.hi_52))
    ? vp.hi_52
    : (typeof hi52 === "number" && !isNaN(hi52)) ? hi52 : undefined;

  const resolvedLo52 = (typeof vp.lo_52 === "number" && !isNaN(vp.lo_52))
    ? vp.lo_52
    : (typeof lo52 === "number" && !isNaN(lo52)) ? lo52 : undefined;

  const resolvedDistHi = (typeof vp.dist_hi_52 === "number" && !isNaN(vp.dist_hi_52))
    ? vp.dist_hi_52
    : (typeof distFromHigh === "number" && !isNaN(distFromHigh))
    ? distFromHigh
    : (resolvedHi52 && currentPrice > 0)
    ? Math.round(((resolvedHi52 - currentPrice) / currentPrice) * 1000) / 10
    : undefined;

  const resolvedDistLo = (typeof vp.dist_lo_52 === "number" && !isNaN(vp.dist_lo_52))
    ? vp.dist_lo_52
    : (typeof distFromLow === "number" && !isNaN(distFromLow))
    ? distFromLow
    : (resolvedLo52 && resolvedLo52 > 0)
    ? Math.round(((currentPrice - resolvedLo52) / resolvedLo52) * 1000) / 10
    : undefined;

  return (
    <div className="card">
      {/* Header */}
      <div className="flex items-center justify-between mb-3">
        <h3 className="text-sm font-semibold text-muted">Volume Profile</h3>
        <div className="flex gap-2 items-center">
          <span className={biasColor[vp.vol_bias] ?? "badge-neutral"}>{vp.vol_bias}</span>
          {vp.vol_surge && (
            <span className="text-xs px-1.5 py-0.5 rounded bg-yellow/10 text-yellow border border-yellow/20 font-mono font-semibold">
              ⚡ SURGE {vp.vol_ratio}x
            </span>
          )}
        </div>
      </div>

      {/* Visual VA bar */}
      <div className="relative h-24 bg-surface rounded-lg border border-border overflow-hidden mb-3">
        {/* Value Area block */}
        <div className="absolute inset-x-0 flex flex-col justify-center h-full px-3">
          <div className="text-xs flex justify-between mb-1 items-center">
            <span className="font-mono text-muted">VAH ${vp.vah?.toFixed(2) ?? vp.vah}</span>
            <div className="flex items-center gap-1.5 font-mono">
              <span className={`text-[11px] font-semibold ${trendColor[vp.vol_trend] ?? "text-muted"}`}>
                {vp.vol_trend}
              </span>
              <span className="text-muted/40">•</span>
              {aboveVah && <span className="text-[11px] text-green font-semibold">▲ Above VA</span>}
              {belowVal  && <span className="text-[11px] text-red   font-semibold">▼ Below VA</span>}
              {inVA      && <span className="text-[11px] text-accent font-semibold">◈ Inside VA</span>}
            </div>
          </div>
          <div className="h-10 bg-accent/10 border border-accent/20 rounded flex items-center justify-center relative">
            <span className="text-xs text-accent font-mono font-bold">POC ${vp.poc?.toFixed(2) ?? vp.poc}</span>
            {/* Current price marker */}
            {inVA && (
              <div
                className="absolute right-2.5 w-2.5 h-2.5 rounded-full bg-white border-2 border-accent shadow-sm"
                title={`Current Price: $${currentPrice.toFixed(2)}`}
              />
            )}
          </div>
          <div className="text-xs text-muted mt-1 font-mono">VAL ${vp.val?.toFixed(2) ?? vp.val}</div>
        </div>
      </div>

      {/* Stats */}
      <div className="grid grid-cols-3 gap-2 text-center text-xs mb-3">
        <div className="bg-surface/50 p-1.5 rounded border border-border/40">
          <div className="font-mono text-red font-bold">${vp.vah?.toFixed(2) ?? vp.vah}</div>
          <div className="text-[11px] text-muted">VAH</div>
        </div>
        <div className="bg-surface/50 p-1.5 rounded border border-border/40">
          <div className="font-mono text-accent font-bold">${vp.poc?.toFixed(2) ?? vp.poc}</div>
          <div className="text-[11px] text-muted">POC</div>
        </div>
        <div className="bg-surface/50 p-1.5 rounded border border-border/40">
          <div className="font-mono text-green font-bold">${vp.val?.toFixed(2) ?? vp.val}</div>
          <div className="text-[11px] text-muted">VAL</div>
        </div>
      </div>

      {/* 52-Week High & 52-Week Low Distance Section */}
      {(resolvedHi52 != null || resolvedLo52 != null) && (
        <div className="pt-3 border-t border-border/60 space-y-2">
          <div className="flex items-center justify-between text-xs">
            <span className="text-muted font-medium flex items-center gap-1.5">
              <span>📅</span>
              <span className="font-semibold text-slate-300">52-Week Range & Distance</span>
            </span>
            {resolvedHi52 && resolvedLo52 && resolvedHi52 > resolvedLo52 && (
              <span className="text-[11px] font-mono text-muted">
                {Math.round(Math.max(0, Math.min(100, ((currentPrice - resolvedLo52) / (resolvedHi52 - resolvedLo52)) * 100)))}% of 52W range
              </span>
            )}
          </div>

          {/* Mini 52W Position Range Bar */}
          {resolvedHi52 && resolvedLo52 && resolvedHi52 > resolvedLo52 && (
            <div className="relative w-full h-2 bg-[#0a0d16] rounded-full overflow-hidden border border-border/50 my-1">
              <div
                className="h-full bg-gradient-to-r from-red-500/70 via-accent/70 to-emerald-500/80 rounded-full"
                style={{ width: "100%" }}
              />
              <div
                className="absolute top-0 bottom-0 w-2 bg-white shadow-[0_0_8px_rgba(255,255,255,1)] rounded-full -ml-1 border border-black/40"
                style={{
                  left: `${Math.max(2, Math.min(98, ((currentPrice - resolvedLo52) / (resolvedHi52 - resolvedLo52)) * 100))}%`,
                }}
                title={`Price: $${currentPrice.toFixed(2)}`}
              />
            </div>
          )}

          {/* 52W High & 52W Low Stat Boxes */}
          <div className="grid grid-cols-2 gap-2 text-xs font-mono">
            {/* 52W Low Box */}
            <div className="bg-[#0a0d16] border border-border/50 rounded-lg p-2.5 flex flex-col justify-between">
              <div className="flex items-center justify-between text-[11px]">
                <span className="text-muted">52W Low</span>
                {resolvedDistLo !== undefined && (
                  <span className="text-emerald-400 font-bold">
                    +{resolvedDistLo.toFixed(1)}%
                  </span>
                )}
              </div>
              <div className="text-base font-bold text-white mt-1">
                ${resolvedLo52 ? resolvedLo52.toFixed(2) : "—"}
              </div>
              <span className="text-[10px] text-emerald-300/80 mt-0.5">above 52w low</span>
            </div>

            {/* 52W High Box */}
            <div className="bg-[#0a0d16] border border-border/50 rounded-lg p-2.5 flex flex-col justify-between">
              <div className="flex items-center justify-between text-[11px]">
                <span className="text-muted">52W High</span>
                {resolvedDistHi !== undefined && (
                  <span className={`font-bold ${resolvedDistHi <= 5 ? "text-amber-400" : resolvedDistHi <= 15 ? "text-cyan-400" : "text-muted"}`}>
                    -{resolvedDistHi.toFixed(1)}%
                  </span>
                )}
              </div>
              <div className="text-base font-bold text-white mt-1">
                ${resolvedHi52 ? resolvedHi52.toFixed(2) : "—"}
              </div>
              <span className="text-[10px] text-muted mt-0.5">off 52w high</span>
            </div>
          </div>
        </div>
      )}

      {/* Final Judgement (Entry Alert + Volume Profile) if provided */}
      {finalJudgement && finalJudgement.verdict_title && (
        <div className="mt-3 pt-3 border-t border-border/60">
          <div className="text-[11px] font-semibold text-muted uppercase tracking-wider mb-1 flex items-center justify-between">
            <span>⚡ Final Judgement</span>
            <span className={`text-[10px] font-mono px-1.5 py-0.5 rounded border ${
              finalJudgement.color === "emerald" ? "bg-emerald-500/20 text-emerald-300 border-emerald-500/40" :
              finalJudgement.color === "cyan" ? "bg-cyan-500/20 text-cyan-300 border-cyan-500/40" :
              finalJudgement.color === "amber" ? "bg-amber-500/20 text-amber-300 border-amber-500/40" :
              finalJudgement.color === "rose" ? "bg-red-500/20 text-red-300 border-red-500/40" :
              "bg-slate-500/20 text-slate-300 border-slate-500/40"
            }`}>
              {finalJudgement.badge}
            </span>
          </div>
          <p className="text-xs text-slate-300 leading-snug">
            {finalJudgement.summary}
          </p>
        </div>
      )}
    </div>
  );
}
