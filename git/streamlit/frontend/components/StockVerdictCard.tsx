"use client";

import { useEffect, useState } from "react";
import type { StockVerdict } from "@/lib/api";
import { fetchStockVerdict } from "@/lib/api";

interface Props {
  ticker: string;
  verdict?: StockVerdict | null;
  currentPrice?: number;
}

export default function StockVerdictCard({ ticker, verdict: initialVerdict, currentPrice }: Props) {
  const [verdict, setVerdict] = useState<StockVerdict | null | undefined>(initialVerdict);
  const [loading, setLoading] = useState<boolean>(!initialVerdict);

  useEffect(() => {
    if (initialVerdict) {
      setVerdict(initialVerdict);
      setLoading(false);
      return;
    }

    let isMounted = true;
    setLoading(true);

    fetchStockVerdict(ticker)
      .then((data) => {
        if (isMounted) {
          setVerdict(data);
          setLoading(false);
        }
      })
      .catch(() => {
        if (isMounted) {
          setVerdict(null);
          setLoading(false);
        }
      });

    return () => {
      isMounted = false;
    };
  }, [ticker, initialVerdict]);

  if (loading) {
    return (
      <div className="card border border-border/80 bg-gradient-to-b from-[#101a2e] to-[#0a0f1d] p-4 rounded-xl shadow-lg animate-pulse">
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-2">
            <span className="text-sm font-bold text-white">Stock Verdict</span>
            <span className="text-xs text-muted">Loading valuation model from stockverdicts.com…</span>
          </div>
          <div className="h-6 w-24 bg-surface rounded-full"></div>
        </div>
      </div>
    );
  }

  if (!verdict || verdict.error || !verdict.verdict) {
    return (
      <div className="card border border-border/60 bg-surface/30 p-3.5 rounded-xl flex items-center justify-between text-xs">
        <div className="flex items-center gap-2.5">
          <span className="text-base">⚖️</span>
          <div>
            <span className="font-semibold text-white">Stock Verdict:</span>{" "}
            <span className="text-muted">
              {verdict?.error
                ? `Verdict for ${ticker}: ${verdict.error}`
                : `No fundamental model coverage for ${ticker} (ETFs, indexes, and unrated stocks are not tracked on stockverdicts.com).`}
            </span>
          </div>
        </div>
        <a
          href={`https://stockverdicts.com/stock/${ticker}`}
          target="_blank"
          rel="noreferrer"
          className="text-accent hover:underline shrink-0 ml-4 font-mono text-[11px]"
        >
          Check on stockverdicts.com ↗
        </a>
      </div>
    );
  }

  const v = verdict.verdict?.toUpperCase() || "NEUTRAL";
  const sig = verdict.signal || "";
  const levels = verdict.levels || {};
  const meters = verdict.meters || {};
  const pros = verdict.pros || [];
  const cons = verdict.cons || [];
  const eq = verdict.earnings_quality || [];

  // Parse numeric values for price ladder
  const parsePx = (val?: string) => {
    if (!val) return null;
    const n = parseFloat(val.replace(/[$,]/g, ""));
    return isNaN(n) ? null : n;
  };

  const heavyPx = parsePx(levels.Heavy);
  const buyPx = parsePx(levels.Buy);
  const fairPx = parsePx(levels.Fair);
  const exitPx = parsePx(levels.Exit);
  const curPx = currentPrice || parsePx(levels.Fair) || 0;

  // Determine ladder pointer percentage
  let ladderPct = 50;
  if (heavyPx && exitPx && exitPx > heavyPx) {
    const minVal = heavyPx * 0.85;
    const maxVal = exitPx * 1.25;
    const range = maxVal - minVal;
    if (range > 0) {
      ladderPct = Math.max(5, Math.min(95, ((curPx - minVal) / range) * 100));
    }
  }

  // Badge styling
  const getBadgeStyle = (val: string) => {
    if (val === "GROWTH") return "bg-[#8b5cf6]/20 text-[#c4b5fd] border-[#8b5cf6]/50";
    if (val === "VALUE") return "bg-[#0284c7]/20 text-[#7dd3fc] border-[#0284c7]/50";
    if (val === "AVOID") return "bg-[#ef4444]/20 text-[#fca5a5] border-[#ef4444]/50";
    if (val === "PEAK") return "bg-[#d97706]/20 text-[#fcd34d] border-[#d97706]/50";
    return "bg-surface text-muted border-border";
  };

  const getMeterColor = (score: number) => {
    if (score >= 70) return "bg-[#059669]";
    if (score >= 45) return "bg-[#d97706]";
    return "bg-[#ef4444]";
  };

  return (
    <div className="card space-y-4 border border-border/80 bg-gradient-to-b from-[#101a2e] to-[#0a0f1d] shadow-lg">
      {/* ── Top Header ── */}
      <div className="flex flex-wrap items-start justify-between gap-3 border-b border-border/40 pb-3">
        <div>
          <div className="flex items-center gap-2">
            <span className="text-base font-bold text-white tracking-tight">Stock Verdict</span>
            <a
              href={verdict.source_url || `https://stockverdicts.com/stock/${verdict.ticker}`}
              target="_blank"
              rel="noreferrer"
              className="text-[10px] text-accent hover:underline flex items-center gap-0.5"
            >
              <span>via stockverdicts.com</span>
              <span>↗</span>
            </a>
          </div>
          {sig && (
            <div className="text-xs font-semibold text-muted mt-0.5 flex items-center gap-1.5">
              <span>Valuation Status:</span>
              <span className={v === "AVOID" ? "text-bear" : v === "GROWTH" || v === "VALUE" ? "text-bull" : "text-white"}>
                {sig}
              </span>
            </div>
          )}
        </div>

        <div className="flex items-center gap-2">
          {verdict.verdict && (
            <span className={`px-3 py-1 rounded-full text-xs font-extrabold uppercase tracking-wider border ${getBadgeStyle(v)}`}>
              {v}
            </span>
          )}
          {verdict.trust_score !== null && verdict.trust_score !== undefined && (
            <span className="px-2.5 py-1 rounded-full text-xs font-bold bg-[#16213a] text-accent border border-accent/20" title="Trust Score">
              🛡️ {verdict.trust_score}/100 {verdict.trust_label || ""}
            </span>
          )}
        </div>
      </div>

      {/* ── Price Ladder Visualization ── */}
      {heavyPx !== null && exitPx !== null && (
        <div className="space-y-1.5 py-1">
          <div className="flex justify-between text-[11px] font-semibold text-muted">
            <span>Valuation Target Ladder</span>
            {currentPrice && (
              <span className="font-mono text-white">
                Current: <b className="text-accent">${currentPrice.toFixed(2)}</b>
              </span>
            )}
          </div>

          <div className="relative pt-4 pb-2">
            {/* Pointer pin */}
            <div
              className="absolute top-0 transform -translate-x-1/2 flex flex-col items-center transition-all duration-300"
              style={{ left: `${ladderPct}%` }}
            >
              <span className="text-[10px] font-bold font-mono bg-[#1e2b45] text-white px-1.5 py-0.5 rounded shadow">
                ${curPx.toFixed(2)}
              </span>
              <span className="text-xs text-accent leading-none -mt-1">▼</span>
            </div>

            {/* Track zones */}
            <div className="h-3 w-full rounded-full bg-[#16213a] flex overflow-hidden border border-border/60">
              <div className="h-full w-1/4 bg-[#059669]/80" title="Heavy Buy Zone" />
              <div className="h-full w-1/4 bg-[#d97706]/80" title="Buy Zone" />
              <div className="h-full w-1/4 bg-[#334155]/90" title="Fair Value Zone" />
              <div className="h-full w-1/4 bg-[#ef4444]/80" title="Exit / Overvalued Zone" />
            </div>

            {/* Labels under track */}
            <div className="grid grid-cols-4 text-center mt-1.5 text-[10px] font-mono">
              <div className="text-left">
                <span className="text-muted block text-[9px] uppercase font-sans">Heavy</span>
                <span className="text-[#059669] font-bold">{levels.Heavy || "—"}</span>
              </div>
              <div>
                <span className="text-muted block text-[9px] uppercase font-sans">Buy</span>
                <span className="text-[#d97706] font-bold">{levels.Buy || "—"}</span>
              </div>
              <div>
                <span className="text-muted block text-[9px] uppercase font-sans">Fair Value</span>
                <span className="text-white font-bold">{levels.Fair || "—"}</span>
              </div>
              <div className="text-right">
                <span className="text-muted block text-[9px] uppercase font-sans">Exit</span>
                <span className="text-[#ef4444] font-bold">{levels.Exit || "—"}</span>
              </div>
            </div>
          </div>
        </div>
      )}

      {/* ── 4 Factor Meters Grid ── */}
      {Object.keys(meters).length > 0 && (
        <div className="grid grid-cols-2 md:grid-cols-4 gap-3 pt-1">
          {(["VALUE", "GROWTH", "QUALITY", "TECHNICAL"] as const).map((key) => {
            const val = meters[key] ?? 50;
            return (
              <div key={key} className="bg-[#0d1526] border border-border/50 rounded-lg p-2.5">
                <div className="flex justify-between text-[11px] font-semibold text-muted mb-1">
                  <span>{key}</span>
                  <span className="font-mono text-white font-bold">{val}</span>
                </div>
                <div className="h-1.5 w-full bg-[#16213a] rounded-full overflow-hidden">
                  <div
                    className={`h-full rounded-full ${getMeterColor(val)}`}
                    style={{ width: `${Math.min(100, Math.max(0, val))}%` }}
                  />
                </div>
              </div>
            );
          })}
        </div>
      )}

      {/* ── Pros & Cons Box ── */}
      {(pros.length > 0 || cons.length > 0) && (
        <div className="grid grid-cols-1 md:grid-cols-2 gap-3 pt-1">
          {pros.length > 0 && (
            <div className="bg-[#0d1526] border border-[#059669]/40 rounded-lg p-3 space-y-1.5">
              <div className="text-[11px] font-bold text-[#6ee7b9] uppercase tracking-wider flex items-center gap-1.5">
                <span>✓</span>
                <span>KEY STRENGTHS (PROS)</span>
              </div>
              <ul className="space-y-1 text-xs text-slate-300">
                {pros.map((p, i) => (
                  <li key={i} className="flex items-start gap-1.5 leading-relaxed">
                    <span className="text-[#059669] font-bold text-xs mt-0.5">•</span>
                    <span>{p}</span>
                  </li>
                ))}
              </ul>
            </div>
          )}

          {cons.length > 0 && (
            <div className="bg-[#0d1526] border border-[#ef4444]/40 rounded-lg p-3 space-y-1.5">
              <div className="text-[11px] font-bold text-[#fca5a5] uppercase tracking-wider flex items-center gap-1.5">
                <span>⚠</span>
                <span>KEY RISKS (CONS)</span>
              </div>
              <ul className="space-y-1 text-xs text-slate-300">
                {cons.map((c, i) => (
                  <li key={i} className="flex items-start gap-1.5 leading-relaxed">
                    <span className="text-[#ef4444] font-bold text-xs mt-0.5">•</span>
                    <span>{c}</span>
                  </li>
                ))}
              </ul>
            </div>
          )}
        </div>
      )}

      {/* ── Earnings Quality / Red Flags Check ── */}
      {eq.length > 0 && (
        <div className="bg-[#0d1526] border border-border/60 rounded-lg p-2.5 text-xs text-slate-300 flex items-start gap-2">
          <span className="text-accent font-bold">🔍</span>
          <div className="space-y-0.5">
            <span className="text-[10px] font-bold text-muted uppercase tracking-wider block">
              Earnings Quality & Accounting Integrity
            </span>
            {eq.map((line, i) => (
              <p key={i} className="leading-snug">{line}</p>
            ))}
          </div>
        </div>
      )}

      {/* ── Sector Peers ── */}
      {verdict.peers && verdict.peers.length > 0 && (
        <div className="flex flex-wrap items-center gap-2 pt-1 border-t border-border/30 text-xs">
          <span className="text-muted text-[11px] font-semibold">Sector Peers:</span>
          {verdict.peers.map((peer) => (
            <a
              key={peer.ticker}
              href={`/stock/${peer.ticker}`}
              className="px-2 py-1 rounded bg-[#0d1526] border border-border/60 hover:border-accent text-white flex items-center gap-1.5 transition-colors"
            >
              <span className="font-mono font-bold text-accent">{peer.ticker}</span>
              {peer.verdict && (
                <span className={`text-[9px] px-1 py-0.2 rounded font-extrabold ${getBadgeStyle(peer.verdict)}`}>
                  {peer.verdict}
                </span>
              )}
              {peer.score && <span className="text-[10px] text-muted">{peer.score}</span>}
            </a>
          ))}
        </div>
      )}
    </div>
  );
}