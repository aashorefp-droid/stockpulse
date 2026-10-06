"use client";

import { useState } from "react";

interface Props {
  levels: Record<string, number>;
  nearestFib: string;
  currentPrice: number;
  weeklyLevels?: Record<string, number>;
  weeklyNearestFib?: string;
  weekHigh?: number | null;
  weekLow?: number | null;
  weekRangeLabel?: string | null;
  hi52?: number | null;
  lo52?: number | null;
}

export function getFibDescription(label: string, mode: "52w" | "week" = "52w"): string {
  if (mode === "week") {
    switch (label) {
      case "R 0.0%":
        return "Week High (PWH)";
      case "R 23.6%":
        return "Shallow Retrace";
      case "R 38.2%":
        return "Key Retrace";
      case "R 50.0%":
        return "Week Midpoint";
      case "R 61.8%":
        return "Golden Ratio";
      case "R 78.6%":
        return "Deep Retrace";
      case "R 100.0%":
        return "Week Low (PWL)";
      case "E 127.2%":
        return "Bullish Ext 1";
      case "E 141.4%":
        return "Bullish Ext 2";
      case "E 161.8%":
        return "Golden Ext";
      case "E 200.0%":
        return "2x Expansion";
      case "E 261.8%":
        return "Max Expansion";
      case "N -23.6%":
        return "Breakdown Ext 1";
      case "N -38.2%":
        return "Breakdown Ext 2";
      case "N -50.0%":
        return "Breakdown Midpoint";
      case "N -61.8%":
        return "Golden Breakdown";
      case "N -100.0%":
        return "100% Breakdown";
      default:
        if (label.startsWith("E ")) return "Expansion Target";
        if (label.startsWith("N -")) return "Breakdown Target";
        return "Retracement Level";
    }
  }

  // mode === "52w"
  switch (label) {
    case "R 0.0%":
      return "52W High";
    case "R 23.6%":
      return "Shallow Retrace";
    case "R 38.2%":
      return "Key Retrace";
    case "R 50.0%":
      return "52W Midpoint";
    case "R 61.8%":
      return "Golden Ratio";
    case "R 78.6%":
      return "Deep Retrace";
    case "R 100.0%":
      return "52W Low";
    case "E 127.2%":
      return "Breakout Ext 1";
    case "E 141.4%":
      return "Breakout Ext 2";
    case "E 161.8%":
      return "Golden Ext";
    case "E 200.0%":
      return "2x Expansion";
    case "E 261.8%":
      return "Max Ext";
    case "N -23.6%":
      return "Breakdown Ext 1";
    case "N -38.2%":
      return "Breakdown Ext 2";
    case "N -50.0%":
      return "Breakdown Midpoint";
    case "N -61.8%":
      return "Golden Breakdown";
    case "N -100.0%":
      return "100% Breakdown Ext";
    default:
      if (label.startsWith("E ")) return "Expansion Target";
      if (label.startsWith("N -")) return "Breakdown Target";
      return "Retracement Level";
  }
}

export default function FibTable({
  levels,
  nearestFib,
  currentPrice,
  weeklyLevels,
  weeklyNearestFib,
  weekHigh,
  weekLow,
  weekRangeLabel,
  hi52,
  lo52,
}: Props) {
  const hasWeekly = Boolean(weeklyLevels && Object.keys(weeklyLevels).length > 0);
  const [activeTab, setActiveTab] = useState<"both" | "52w" | "week">(hasWeekly ? "both" : "52w");

  const entries52w = Object.entries(levels || {}).sort((a, b) => b[1] - a[1]);
  const entriesWeek = Object.entries(weeklyLevels || {}).sort((a, b) => b[1] - a[1]);

  const resolvedHi52 = hi52 ?? levels?.["R 0.0%"];
  const resolvedLo52 = lo52 ?? levels?.["R 100.0%"];
  const resolvedWkHi = weekHigh ?? weeklyLevels?.["R 0.0%"];
  const resolvedWkLo = weekLow ?? weeklyLevels?.["R 100.0%"];

  const renderTableRows = (
    entries: [string, number][],
    nearest: string | undefined,
    mode: "52w" | "week"
  ) => {
    return (
      <div className="space-y-1 max-h-72 overflow-y-auto pr-1">
        {entries.map(([label, val]) => {
          const isNearest = label === nearest;
          const above = val > currentPrice;
          const desc = getFibDescription(label, mode);
          const isAnchor = label === "R 0.0%" || label === "R 100.0%";
          const isGolden = label === "R 50.0%" || label === "R 61.8%";
          const distPct = currentPrice > 0 ? ((val - currentPrice) / currentPrice) * 100 : 0;
          const distLabel = distPct === 0 ? "0.0%" : distPct > 0 ? `+${distPct.toFixed(1)}%` : `${distPct.toFixed(1)}%`;

          return (
            <div
              key={label}
              className={`flex justify-between items-center px-2 py-1.5 rounded text-xs font-mono transition-all ${
                isNearest
                  ? "bg-accent/15 border border-accent/40 text-accent font-semibold shadow-sm"
                  : above
                  ? "text-red/90 hover:bg-white/[0.02]"
                  : "text-green/90 hover:bg-white/[0.02]"
              }`}
            >
              <div className="flex items-center gap-1.5 flex-wrap">
                <span className="font-bold">{label}</span>
                <span
                  className={`text-[10px] font-sans px-1.5 py-0.2 rounded border uppercase tracking-wider ${
                    isAnchor
                      ? "bg-purple-500/20 text-purple-300 border-purple-500/30 font-bold"
                      : isGolden
                      ? "bg-amber-500/20 text-amber-300 border-amber-500/30 font-semibold"
                      : "bg-surface/80 text-slate-400 border-border/40"
                  }`}
                >
                  {desc}
                </span>
                {isNearest && (
                  <span className="text-[9px] font-sans font-extrabold px-1.5 py-0.2 rounded bg-accent text-black uppercase tracking-wider">
                    Nearest
                  </span>
                )}
              </div>
              <div className="flex items-center gap-2">
                <span className="text-[10px] font-sans text-slate-400">{distLabel}</span>
                <span className="font-bold font-mono">${val.toFixed(2)}</span>
              </div>
            </div>
          );
        })}
      </div>
    );
  };

  return (
    <div className="card">
      {/* Header and View Selector */}
      <div className="flex flex-wrap items-center justify-between gap-2 mb-3 pb-2.5 border-b border-border/40">
        <div>
          <h3 className="text-sm font-semibold text-white flex items-center gap-1.5">
            <span>📐</span>
            <span>Fibonacci Levels</span>
          </h3>
          <p className="text-[11px] text-muted">
            {activeTab === "52w"
              ? "Key retracements & extensions based on 52-Week High & Low"
              : activeTab === "week"
              ? "Weekly swings based on Prior Week High (PWH) & Low (PWL)"
              : "Dual timeframe view: 52-Week Macro Range & Prior Week Swing"}
          </p>
        </div>

        {hasWeekly && (
          <div className="flex items-center gap-1 bg-[#131622] p-0.5 rounded-lg border border-border/60">
            <button
              type="button"
              onClick={() => setActiveTab("both")}
              className={`text-xs px-2.5 py-1 rounded font-medium transition-colors ${
                activeTab === "both"
                  ? "bg-accent text-black font-bold shadow-sm"
                  : "text-slate-400 hover:text-white"
              }`}
            >
              Both (Split)
            </button>
            <button
              type="button"
              onClick={() => setActiveTab("52w")}
              className={`text-xs px-2.5 py-1 rounded font-medium transition-colors ${
                activeTab === "52w"
                  ? "bg-accent text-black font-bold shadow-sm"
                  : "text-slate-400 hover:text-white"
              }`}
            >
              52W Range
            </button>
            <button
              type="button"
              onClick={() => setActiveTab("week")}
              className={`text-xs px-2.5 py-1 rounded font-medium transition-colors ${
                activeTab === "week"
                  ? "bg-accent text-black font-bold shadow-sm"
                  : "text-slate-400 hover:text-white"
              }`}
            >
              Week High &amp; Low
            </button>
          </div>
        )}
      </div>

      {/* ── BOTH (SPLIT VIEW) ────────────────────────────────────────── */}
      {activeTab === "both" && hasWeekly && (
        <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
          {/* 52W Column */}
          <div className="space-y-2 bg-[#0a0c14]/50 p-2.5 rounded-lg border border-border/30">
            <div className="flex items-center justify-between pb-1 border-b border-border/20">
              <div>
                <span className="text-xs font-bold text-purple-300">52W Range</span>
                <span className="text-[10px] text-slate-400 block">Based on 52-Week High &amp; Low</span>
              </div>
              {resolvedHi52 != null && resolvedLo52 != null && (
                <span className="text-[11px] font-mono text-purple-200 bg-purple-500/10 px-2 py-0.5 rounded border border-purple-500/20">
                  ${resolvedLo52.toFixed(2)} – ${resolvedHi52.toFixed(2)}
                </span>
              )}
            </div>
            {renderTableRows(entries52w, nearestFib, "52w")}
          </div>

          {/* Week Range Column */}
          <div className="space-y-2 bg-[#0a0c14]/50 p-2.5 rounded-lg border border-border/30">
            <div className="flex items-center justify-between pb-1 border-b border-border/20">
              <div>
                <span className="text-xs font-bold text-sky-300">Week High &amp; Low</span>
                <span className="text-[10px] text-slate-400 block">
                  Based on PWH &amp; PWL {weekRangeLabel ? `(${weekRangeLabel})` : ""}
                </span>
              </div>
              {resolvedWkHi != null && resolvedWkLo != null && (
                <span className="text-[11px] font-mono text-sky-200 bg-sky-500/10 px-2 py-0.5 rounded border border-sky-500/20">
                  ${resolvedWkLo.toFixed(2)} – ${resolvedWkHi.toFixed(2)}
                </span>
              )}
            </div>
            {renderTableRows(entriesWeek, weeklyNearestFib, "week")}
          </div>
        </div>
      )}

      {/* ── 52W SINGLE VIEW ─────────────────────────────────────────── */}
      {activeTab === "52w" && (
        <div className="space-y-2">
          <div className="flex items-center justify-between pb-1">
            <span className="text-[11px] text-slate-400">
              Anchor: 52-Week Range (0% High – 100% Low)
            </span>
            {resolvedHi52 != null && resolvedLo52 != null && (
              <span className="text-[11px] font-mono text-purple-200 bg-purple-500/10 px-2 py-0.5 rounded border border-purple-500/20">
                ${resolvedLo52.toFixed(2)} – ${resolvedHi52.toFixed(2)}
              </span>
            )}
          </div>
          {renderTableRows(entries52w, nearestFib, "52w")}
        </div>
      )}

      {/* ── WEEK SINGLE VIEW ────────────────────────────────────────── */}
      {activeTab === "week" && hasWeekly && (
        <div className="space-y-2">
          <div className="flex items-center justify-between pb-1">
            <span className="text-[11px] text-slate-400">
              Anchor: Previous Week High (PWH) &amp; Low (PWL) {weekRangeLabel ? `· ${weekRangeLabel}` : ""}
            </span>
            {resolvedWkHi != null && resolvedWkLo != null && (
              <span className="text-[11px] font-mono text-sky-200 bg-sky-500/10 px-2 py-0.5 rounded border border-sky-500/20">
                ${resolvedWkLo.toFixed(2)} – ${resolvedWkHi.toFixed(2)}
              </span>
            )}
          </div>
          {renderTableRows(entriesWeek, weeklyNearestFib, "week")}
        </div>
      )}
    </div>
  );
}
