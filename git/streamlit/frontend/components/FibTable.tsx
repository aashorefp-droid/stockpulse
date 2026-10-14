"use client";

import { useState } from "react";
import type { EarningsFibData, EarningsPredictionData } from "@/lib/api";

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
  earningsFib?: EarningsFibData | null;
  earningsPrediction?: EarningsPredictionData | null;
}

export function getFibDescription(label: string, mode: "52w" | "week" | "earnings" = "52w"): string {
  if (mode === "earnings") {
    switch (label) {
      case "R 0.0%":
        return "Last Earnings High";
      case "R 23.6%":
        return "Shallow Retrace";
      case "R 38.2%":
        return "Key Retrace";
      case "R 50.0%":
        return "Earnings Midpoint";
      case "R 61.8%":
        return "Golden Ratio";
      case "R 78.6%":
        return "Deep Retrace";
      case "R 100.0%":
        return "Last Earnings Low";
      case "E 127.2%":
        return "Post-Earnings Ext 1";
      case "E 141.4%":
        return "Post-Earnings Ext 2";
      case "E 161.8%":
        return "Golden Earnings Ext";
      case "E 200.0%":
        return "2x Earnings Ext";
      case "E 261.8%":
        return "Max Earnings Ext";
      case "N -23.6%":
        return "Earnings Breakdown 1";
      case "N -38.2%":
        return "Earnings Breakdown 2";
      case "N -50.0%":
        return "Breakdown Midpoint";
      case "N -61.8%":
        return "Golden Breakdown";
      case "N -100.0%":
        return "100% Breakdown";
      default:
        if (label.startsWith("E ")) return "Earnings Expansion";
        if (label.startsWith("N -")) return "Earnings Breakdown";
        return "Earnings Retrace";
    }
  }

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
  earningsFib,
  earningsPrediction,
}: Props) {
  const hasWeekly = Boolean(weeklyLevels && Object.keys(weeklyLevels).length > 0);
  const hasEarnings = Boolean(earningsFib && earningsFib.has_earnings && earningsFib.fib_levels);
  const [activeTab, setActiveTab] = useState<"both" | "52w" | "week" | "earnings">(hasWeekly ? "both" : "52w");
  const [showConfluencePreview, setShowConfluencePreview] = useState(false);

  const entries52w = Object.entries(levels || {}).sort((a, b) => b[1] - a[1]);
  const entriesWeek = Object.entries(weeklyLevels || {}).sort((a, b) => b[1] - a[1]);
  const entriesEarnings = Object.entries(earningsFib?.fib_levels || {}).sort((a, b) => b[1] - a[1]);

  const resolvedHi52 = hi52 ?? levels?.["R 0.0%"];
  const resolvedLo52 = lo52 ?? levels?.["R 100.0%"];
  const resolvedWkHi = weekHigh ?? weeklyLevels?.["R 0.0%"];
  const resolvedWkLo = weekLow ?? weeklyLevels?.["R 100.0%"];

  const renderTableRows = (
    entries: [string, number][],
    nearest: string | undefined,
    mode: "52w" | "week" | "earnings"
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
                      ? mode === "earnings"
                        ? "bg-amber-500/20 text-amber-300 border-amber-500/40 font-bold"
                        : "bg-purple-500/20 text-purple-300 border-purple-500/30 font-bold"
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
      {/* ── 52W / WEEK / EARNINGS FIBS OUTCOME PREDICTION & HELPFUL DATA BANNER ── */}
      {earningsPrediction && (
        <div className="mb-4 rounded-xl bg-gradient-to-br from-[#121626] via-[#0d101d] to-[#080a13] border border-indigo-500/20 p-3.5 shadow-sm">
          {/* Top Row: Title, Prediction Badge, Market Cap & Schedule */}
          <div className="flex flex-wrap items-center justify-between gap-2.5 pb-2.5 border-b border-border/30">
            <div className="flex items-center gap-2.5">
              <span className="text-xl">🔮</span>
              <div>
                <div className="flex items-center gap-2 flex-wrap">
                  <span className="text-xs font-bold text-white uppercase tracking-wider">
                    Earnings Outcome Prediction
                  </span>
                  {/* Status Badge */}
                  {earningsPrediction.is_tomorrow ? (
                    <span
                      className={`text-[11px] font-bold px-2.5 py-0.5 rounded-full border shadow-sm uppercase tracking-wider flex items-center gap-1.5 ${
                        earningsPrediction.prediction_color === "emerald"
                          ? "bg-emerald-500/20 text-emerald-300 border-emerald-500/50"
                          : earningsPrediction.prediction_color === "teal"
                          ? "bg-teal-500/20 text-teal-300 border-teal-500/50"
                          : earningsPrediction.prediction_color === "rose"
                          ? "bg-rose-500/20 text-rose-300 border-rose-500/50"
                          : "bg-amber-500/20 text-amber-300 border-amber-500/50"
                      }`}
                    >
                      <span className="w-2 h-2 rounded-full bg-emerald-400 animate-ping"></span>
                      <span>PREDICTION: {earningsPrediction.prediction}</span>
                    </span>
                  ) : (
                    <span className="text-[11px] font-bold px-2.5 py-0.5 rounded-full bg-slate-800/80 text-slate-300 border border-slate-700 uppercase tracking-wider flex items-center gap-1.5">
                      <span className="w-1.5 h-1.5 rounded-full bg-slate-400"></span>
                      <span>EARNINGS: N/A</span>
                      <span className="text-[10px] text-slate-400 font-normal lowercase">(not tomorrow)</span>
                    </span>
                  )}
                </div>
                <div className="text-[11px] text-slate-300 font-sans mt-0.5">
                  {earningsPrediction.prediction_note}
                </div>
              </div>
            </div>

            {/* Market Cap & Next Earnings Info */}
            <div className="flex items-center gap-2.5 font-mono text-xs ml-auto">
              {earningsPrediction.market_cap?.formatted && earningsPrediction.market_cap.formatted !== "N/A" && (
                <div className="text-right bg-black/40 px-2.5 py-1 rounded border border-border/40">
                  <span className="text-[10px] text-slate-400 block font-sans">Market Cap</span>
                  <span className="font-bold text-sky-300">{earningsPrediction.market_cap.formatted}</span>
                  <span className="text-[9px] text-slate-400 block font-sans">
                    {earningsPrediction.market_cap.category}
                  </span>
                </div>
              )}
              {earningsPrediction.helpful_earnings_data?.next_earnings_date_label && (
                <div className="text-right bg-black/40 px-2.5 py-1 rounded border border-border/40">
                  <span className="text-[10px] text-slate-400 block font-sans">Next Earnings</span>
                  <span className="font-bold text-amber-200">
                    {earningsPrediction.helpful_earnings_data.next_earnings_date_label}
                  </span>
                  <span className="text-[9px] text-amber-400/90 block font-sans">
                    {earningsPrediction.helpful_earnings_data.status_label}
                  </span>
                </div>
              )}
            </div>
          </div>

          {/* Helpful Earnings Data Grid */}
          <div className="grid grid-cols-2 sm:grid-cols-4 gap-2 text-xs font-mono mt-2.5">
            <div className="bg-black/30 p-2 rounded border border-border/30">
              <span className="text-[10px] text-slate-400 block font-sans">Next Consensus Est.</span>
              <span className="font-bold text-white">
                EPS: {earningsPrediction.helpful_earnings_data?.next_eps_estimate != null ? `$${earningsPrediction.helpful_earnings_data.next_eps_estimate}` : "N/A"}
              </span>
              <span className="text-[10px] text-slate-400 block font-sans mt-0.5">
                Rev: {earningsPrediction.helpful_earnings_data?.next_revenue_estimate || "N/A"}
              </span>
            </div>

            <div className="bg-black/30 p-2 rounded border border-border/30">
              <span className="text-[10px] text-slate-400 block font-sans">Valuation Multiples</span>
              <span className="font-bold text-white">
                P/E: {earningsPrediction.helpful_earnings_data?.pe_ratio != null ? earningsPrediction.helpful_earnings_data.pe_ratio : "N/A"}
              </span>
              <span className="text-[10px] text-slate-400 block font-sans mt-0.5">
                Fwd P/E: {earningsPrediction.helpful_earnings_data?.forward_pe != null ? earningsPrediction.helpful_earnings_data.forward_pe : "N/A"}
              </span>
            </div>

            <div className="bg-black/30 p-2 rounded border border-border/30">
              <span className="text-[10px] text-slate-400 block font-sans">Last Report Beat/Miss</span>
              <span
                className={`font-bold ${
                  earningsPrediction.helpful_earnings_data?.last_earnings_beat_status === "BEAT"
                    ? "text-emerald-400"
                    : earningsPrediction.helpful_earnings_data?.last_earnings_beat_status === "MISS"
                    ? "text-rose-400"
                    : "text-slate-300"
                }`}
              >
                {earningsPrediction.helpful_earnings_data?.last_earnings_eps_actual != null ? `$${earningsPrediction.helpful_earnings_data.last_earnings_eps_actual}` : "N/A"}{" "}
                <span className="text-[10px]">
                  ({earningsPrediction.helpful_earnings_data?.last_earnings_surprise_pct != null ? `${earningsPrediction.helpful_earnings_data.last_earnings_surprise_pct >= 0 ? "+" : ""}${earningsPrediction.helpful_earnings_data.last_earnings_surprise_pct}%` : ""})
                </span>
              </span>
              <span className="text-[10px] text-slate-400 block font-sans mt-0.5">
                vs ${earningsPrediction.helpful_earnings_data?.last_earnings_eps_estimate ?? "N/A"} est
              </span>
            </div>

            <div className="bg-black/30 p-2 rounded border border-border/30">
              <span className="text-[10px] text-slate-400 block font-sans">Track Record & Move</span>
              <span className="font-bold text-indigo-300">
                {earningsPrediction.helpful_earnings_data?.historical_beat_rate_pct != null ? `${earningsPrediction.helpful_earnings_data.historical_beat_rate_pct}% Beats` : "N/A"}
              </span>
              <span className="text-[10px] text-amber-300/90 block font-sans mt-0.5">
                Options Move: {earningsPrediction.helpful_earnings_data?.expected_move_pct != null ? `±${earningsPrediction.helpful_earnings_data.expected_move_pct}%` : "N/A"}
              </span>
            </div>
          </div>

          {/* Active Prediction Details OR Collapsible Confluence Preview */}
          {(earningsPrediction.is_tomorrow || showConfluencePreview) && earningsPrediction.fib_confluence && (
            <div className="mt-3 pt-3 border-t border-border/30 space-y-2">
              <div className="flex items-center justify-between text-xs">
                <span className="font-bold text-white font-sans flex items-center gap-1.5">
                  <span>📐</span>
                  <span>52W · Week · Earnings Fibs Confluence Model</span>
                </span>
                <span className="font-mono text-sky-300 bg-sky-500/10 px-2 py-0.5 rounded border border-sky-500/20">
                  Confluence Score: {earningsPrediction.fib_confluence.total_confluence_score > 0 ? "+" : ""}{earningsPrediction.fib_confluence.total_confluence_score}/100 ({earningsPrediction.fib_confluence.outcome_bias})
                </span>
              </div>

              <div className="grid grid-cols-1 sm:grid-cols-3 gap-2 text-xs">
                {/* 52W Stance */}
                <div className="bg-purple-950/20 border border-purple-500/30 p-2 rounded">
                  <div className="flex items-center justify-between text-[10px] text-purple-300 font-bold uppercase">
                    <span>52W Macro Fib</span>
                    <span>{earningsPrediction.fib_confluence.fib_52w.score > 0 ? "+" : ""}{earningsPrediction.fib_confluence.fib_52w.score} pts</span>
                  </div>
                  <div className="font-semibold text-purple-200 mt-0.5">
                    {earningsPrediction.fib_confluence.fib_52w.stance}
                  </div>
                  <div className="text-[10px] text-slate-300 mt-0.5 font-sans">
                    {earningsPrediction.fib_confluence.fib_52w.description}
                  </div>
                </div>

                {/* Weekly Stance */}
                <div className="bg-blue-950/20 border border-blue-500/30 p-2 rounded">
                  <div className="flex items-center justify-between text-[10px] text-blue-300 font-bold uppercase">
                    <span>Weekly Swing Fib</span>
                    <span>{earningsPrediction.fib_confluence.fib_week.score > 0 ? "+" : ""}{earningsPrediction.fib_confluence.fib_week.score} pts</span>
                  </div>
                  <div className="font-semibold text-blue-200 mt-0.5">
                    {earningsPrediction.fib_confluence.fib_week.stance}
                  </div>
                  <div className="text-[10px] text-slate-300 mt-0.5 font-sans">
                    {earningsPrediction.fib_confluence.fib_week.description}
                  </div>
                </div>

                {/* Earnings Reaction Stance */}
                <div className="bg-amber-950/20 border border-amber-500/30 p-2 rounded">
                  <div className="flex items-center justify-between text-[10px] text-amber-300 font-bold uppercase">
                    <span>Last Earnings Fib</span>
                    <span>{earningsPrediction.fib_confluence.fib_earnings.score > 0 ? "+" : ""}{earningsPrediction.fib_confluence.fib_earnings.score} pts</span>
                  </div>
                  <div className="font-semibold text-amber-200 mt-0.5">
                    {earningsPrediction.fib_confluence.fib_earnings.stance}
                  </div>
                  <div className="text-[10px] text-slate-300 mt-0.5 font-sans">
                    {earningsPrediction.fib_confluence.fib_earnings.description}
                  </div>
                </div>
              </div>

              {/* Rationale & Target Levels */}
              <div className="bg-black/40 p-2.5 rounded border border-border/40 text-xs space-y-1.5">
                <p className="text-slate-300 font-sans leading-relaxed">
                  <span className="font-bold text-white">Confluence Thesis: </span>
                  {earningsPrediction.fib_confluence.rationale}
                </p>
                <div className="flex flex-wrap gap-4 pt-1 font-mono text-[11px]">
                  <div className="flex items-center gap-1.5">
                    <span className="text-slate-400">Predicted Upside Target:</span>
                    <span className="font-bold text-emerald-300">
                      {earningsPrediction.fib_confluence.upside_target_label}
                    </span>
                    <span className="text-slate-400">
                      (+{earningsPrediction.fib_confluence.upside_target_pct}%)
                    </span>
                  </div>
                  <div className="flex items-center gap-1.5">
                    <span className="text-slate-400">Downside Support Floor:</span>
                    <span className="font-bold text-rose-300">
                      {earningsPrediction.fib_confluence.downside_floor_label}
                    </span>
                    <span className="text-slate-400">
                      ({earningsPrediction.fib_confluence.downside_floor_pct}%)
                    </span>
                  </div>
                </div>
              </div>
            </div>
          )}

          {/* Toggle for non-tomorrow prospective preview */}
          {!earningsPrediction.is_tomorrow && (
            <div className="mt-2.5 pt-2 border-t border-border/20 flex justify-between items-center text-[11px]">
              <span className="text-slate-400">
                Want to see prospective Fibonacci alignment ahead of time?
              </span>
              <button
                type="button"
                onClick={() => setShowConfluencePreview(!showConfluencePreview)}
                className="text-sky-400 hover:text-sky-300 font-semibold underline underline-offset-2 transition-colors"
              >
                {showConfluencePreview ? "Hide Fib Confluence Model ▲" : "Preview 52W/Week/Earning Fibs Model ▼"}
              </button>
            </div>
          )}
        </div>
      )}

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
              : activeTab === "earnings"
              ? "Price action and extensions relative to Last Earnings High & Low"
              : "Multi-timeframe view: 52-Week Macro, Prior Week Swing, and Last Earnings"}
          </p>
        </div>

        <div className="flex items-center gap-1 bg-[#131622] p-0.5 rounded-lg border border-border/60 flex-wrap">
          <button
            type="button"
            onClick={() => setActiveTab("both")}
            className={`text-xs px-2.5 py-1 rounded font-medium transition-colors ${
              activeTab === "both"
                ? "bg-accent text-black font-bold shadow-sm"
                : "text-slate-400 hover:text-white"
            }`}
          >
            All (Split)
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
          {hasWeekly && (
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
          )}
          {hasEarnings && (
            <button
              type="button"
              onClick={() => setActiveTab("earnings")}
              className={`text-xs px-2.5 py-1 rounded font-medium transition-colors flex items-center gap-1 ${
                activeTab === "earnings"
                  ? "bg-amber-400 text-black font-bold shadow-sm"
                  : "text-amber-300/80 hover:text-amber-200"
              }`}
            >
              <span>📊</span>
              <span>Last Earnings</span>
            </button>
          )}
        </div>
      </div>

      {/* ── WHERE WE ARE NOW BANNER (When viewing Last Earnings or All Split) ── */}
      {hasEarnings && earningsFib && (activeTab === "earnings" || activeTab === "both") && (
        <div className="mb-3 p-3 rounded-lg bg-gradient-to-r from-[#121626] to-[#0c0e1a] border border-amber-500/20 shadow-sm">
          <div className="flex flex-wrap items-center justify-between gap-2 pb-2 border-b border-border/30">
            <div className="flex items-center gap-2">
              <span className="text-base">📍</span>
              <div>
                <div className="text-xs font-bold text-white flex items-center gap-2">
                  <span>WHERE WE ARE NOW · LAST EARNINGS</span>
                  <span
                    className={`text-[10px] font-sans px-2 py-0.5 rounded font-bold border uppercase tracking-wider ${
                      earningsFib.status_color === "emerald"
                        ? "bg-emerald-500/20 text-emerald-300 border-emerald-500/40"
                        : earningsFib.status_color === "red"
                        ? "bg-red-500/20 text-red-300 border-red-500/40"
                        : "bg-amber-500/20 text-amber-300 border-amber-500/40"
                    }`}
                  >
                    {earningsFib.status_badge || "EVALUATING"}
                  </span>
                </div>
                <div className="text-[11px] text-slate-300 font-sans mt-0.5">
                  {earningsFib.status_label}
                </div>
              </div>
            </div>

            <div className="text-right font-mono text-xs">
              <span className="text-slate-400 text-[10px] block">Reaction Date:</span>
              <span className="font-semibold text-amber-200">
                {earningsFib.reaction_date_label || earningsFib.earnings_date} ({earningsFib.days_since_earnings}d ago)
              </span>
            </div>
          </div>

          <div className="grid grid-cols-2 sm:grid-cols-4 gap-2 text-xs font-mono mt-2.5">
            <div className="bg-black/40 p-2 rounded border border-border/30">
              <span className="text-[10px] text-slate-400 block font-sans">Last Earnings High</span>
              <span className="font-bold text-emerald-300">${earningsFib.earnings_high?.toFixed(2)}</span>
              <span className="text-[10px] text-slate-400 ml-1">
                ({earningsFib.dist_from_high_pct != null && earningsFib.dist_from_high_pct >= 0 ? "+" : ""}
                {earningsFib.dist_from_high_pct?.toFixed(1)}%)
              </span>
            </div>

            <div className="bg-black/40 p-2 rounded border border-border/30">
              <span className="text-[10px] text-slate-400 block font-sans">Last Earnings Low</span>
              <span className="font-bold text-rose-300">${earningsFib.earnings_low?.toFixed(2)}</span>
              <span className="text-[10px] text-slate-400 ml-1">
                ({earningsFib.dist_from_low_pct != null && earningsFib.dist_from_low_pct >= 0 ? "+" : ""}
                {earningsFib.dist_from_low_pct?.toFixed(1)}%)
              </span>
            </div>

            <div className="bg-black/40 p-2 rounded border border-border/30">
              <span className="text-[10px] text-slate-400 block font-sans">Earnings Midpoint (50%)</span>
              <span className="font-bold text-amber-300">${earningsFib.earnings_midpoint?.toFixed(2)}</span>
              <span className="text-[10px] text-slate-400 block font-sans mt-0.5">
                Close: ${earningsFib.earnings_close?.toFixed(2)}
              </span>
            </div>

            <div className="bg-black/40 p-2 rounded border border-border/30">
              <span className="text-[10px] text-slate-400 block font-sans">Return Since Earnings</span>
              <span
                className={`font-bold ${
                  (earningsFib.return_since_earnings_pct ?? 0) >= 0 ? "text-emerald-400" : "text-rose-400"
                }`}
              >
                {(earningsFib.return_since_earnings_pct ?? 0) >= 0 ? "+" : ""}
                {earningsFib.return_since_earnings_pct?.toFixed(1)}%
              </span>
              <span className="text-[10px] text-slate-400 block font-sans mt-0.5">
                Nearest: {earningsFib.nearest_fib}
              </span>
            </div>
          </div>
        </div>
      )}

      {/* ── ALL (SPLIT VIEW) ────────────────────────────────────────── */}
      {activeTab === "both" && (
        <div className={`grid grid-cols-1 ${hasEarnings ? "md:grid-cols-2 lg:grid-cols-3" : "md:grid-cols-2"} gap-3`}>
          {/* 52W Column */}
          <div className="space-y-2 bg-[#0a0c14]/50 p-2.5 rounded-lg border border-border/30">
            <div className="flex items-center justify-between pb-1 border-b border-border/20">
              <div>
                <span className="text-xs font-bold text-purple-300">52W Range</span>
                <span className="text-[10px] text-slate-400 block">52-Week High &amp; Low</span>
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
          {hasWeekly && (
            <div className="space-y-2 bg-[#0a0c14]/50 p-2.5 rounded-lg border border-border/30">
              <div className="flex items-center justify-between pb-1 border-b border-border/20">
                <div>
                  <span className="text-xs font-bold text-sky-300">Week High &amp; Low</span>
                  <span className="text-[10px] text-slate-400 block">
                    PWH &amp; PWL {weekRangeLabel ? `(${weekRangeLabel})` : ""}
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
          )}

          {/* Last Earnings Column */}
          {hasEarnings && (
            <div className="space-y-2 bg-[#0a0c14]/50 p-2.5 rounded-lg border border-amber-500/30">
              <div className="flex items-center justify-between pb-1 border-b border-border/20">
                <div>
                  <span className="text-xs font-bold text-amber-300">Last Earnings</span>
                  <span className="text-[10px] text-slate-400 block">
                    E-High &amp; E-Low ({earningsFib?.reaction_date_label || earningsFib?.earnings_date})
                  </span>
                </div>
                {earningsFib?.earnings_high != null && earningsFib?.earnings_low != null && (
                  <span className="text-[11px] font-mono text-amber-200 bg-amber-500/10 px-2 py-0.5 rounded border border-amber-500/20">
                    ${earningsFib.earnings_low.toFixed(2)} – ${earningsFib.earnings_high.toFixed(2)}
                  </span>
                )}
              </div>
              {renderTableRows(entriesEarnings, earningsFib?.nearest_fib, "earnings")}
            </div>
          )}
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

      {/* ── LAST EARNINGS SINGLE VIEW ────────────────────────────────── */}
      {activeTab === "earnings" && hasEarnings && (
        <div className="space-y-2">
          <div className="flex items-center justify-between pb-1">
            <span className="text-[11px] text-slate-400">
              Anchor: Last Earnings Reaction Session ({earningsFib?.reaction_date_label || earningsFib?.earnings_date})
            </span>
            {earningsFib?.earnings_high != null && earningsFib?.earnings_low != null && (
              <span className="text-[11px] font-mono text-amber-200 bg-amber-500/10 px-2 py-0.5 rounded border border-amber-500/20">
                ${earningsFib.earnings_low.toFixed(2)} – ${earningsFib.earnings_high.toFixed(2)}
              </span>
            )}
          </div>
          {renderTableRows(entriesEarnings, earningsFib?.nearest_fib, "earnings")}
        </div>
      )}
    </div>
  );
}
