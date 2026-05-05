"use client";
import { useEffect, useState, useCallback } from "react";

const API_BASE = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";
const REFRESH_MS = 5 * 60 * 1000;

interface MacroItem {
  ticker:   string;
  label:    string;
  category: string;
  price:    number;
  chg_1d:   number;
  chg_5d:   number;
  chg_20d:  number;
}

interface MacroData {
  items: MacroItem[];
  risk: { score: number; label: string; notes: string[] };
}

const KEY_TICKERS = ["SPY", "QQQ", "^VIX", "GLD", "TLT"];

export default function MarketRisk() {
  const [data, setData]           = useState<MacroData | null>(null);
  const [updatedAt, setUpdatedAt] = useState<Date | null>(null);
  const [loading, setLoading]     = useState(true);
  const [error, setError]         = useState<string | null>(null);

  const refresh = useCallback(async () => {
    try {
      const res = await fetch(`${API_BASE}/api/macro/snapshot`);
      if (!res.ok) { setError(`API error ${res.status}`); return; }
      setData(await res.json());
      setUpdatedAt(new Date());
      setError(null);
    } catch (e: any) {
      setError(e?.message || "Network error");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    refresh();
    const id = setInterval(refresh, REFRESH_MS);
    return () => clearInterval(id);
  }, [refresh]);

  if (loading) {
    return (
      <div className="border-b border-border bg-card/40 px-4 py-2 text-xs text-muted animate-pulse">
        Loading market data…
      </div>
    );
  }

  if (error) return (
    <div className="border-b border-border bg-card/40 px-4 py-2 text-xs text-red/70">
      Market data unavailable — {error}
    </div>
  );

  if (!data) return null;

  const { risk, items } = data;

  const riskStyles =
    risk.score === 0 ? "text-green  border-green/40  bg-green/5"  :
    risk.score <= 2  ? "text-yellow border-yellow/40 bg-yellow/5" :
                       "text-red    border-red/40    bg-red/5";

  const riskDot =
    risk.score === 0 ? "bg-green"  :
    risk.score <= 2  ? "bg-yellow" :
                       "bg-red";

  const keyItems = KEY_TICKERS
    .map(t => items.find(i => i.ticker === t))
    .filter(Boolean) as MacroItem[];

  return (
    <div className="border-b border-border bg-card/40 px-4 py-2">
      <div className="max-w-screen-2xl mx-auto flex flex-wrap items-center gap-x-4 gap-y-1">

        {/* Risk badge */}
        <div className={`flex items-center gap-1.5 px-2.5 py-1 rounded border text-[11px] font-bold tracking-wide ${riskStyles}`}>
          <span className={`w-1.5 h-1.5 rounded-full ${riskDot}`} />
          MARKET RISK: {risk.label}
        </div>

        {/* Risk notes — hidden on mobile */}
        {risk.notes.length > 0 && (
          <div className="hidden md:flex items-center gap-1 text-[11px] text-muted">
            {risk.notes.map((n, i) => (
              <span key={i}>
                {i > 0 && <span className="mx-1 opacity-40">·</span>}
                {n}
              </span>
            ))}
          </div>
        )}

        {/* Key tickers — pushed to the right */}
        <div className="flex items-center gap-4 ml-auto">
          {keyItems.map(item => {
            const pos  = item.chg_1d > 0;
            const neg  = item.chg_1d < 0;
            const col  = pos ? "text-green" : neg ? "text-red" : "text-muted";
            const sign = pos ? "+" : "";
            return (
              <div key={item.ticker} className="flex flex-col items-end leading-tight">
                <span className="text-[9px] text-muted uppercase tracking-wider">{item.label}</span>
                <span className="text-[12px] font-mono font-semibold text-white">
                  {item.ticker === "^VIX"
                    ? item.price.toFixed(2)
                    : `$${item.price.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`}
                </span>
                <span className={`text-[10px] font-mono ${col}`}>
                  {sign}{item.chg_1d.toFixed(2)}%
                </span>
              </div>
            );
          })}
        </div>

        {/* Last updated */}
        {updatedAt && (
          <div className="hidden lg:block text-[10px] text-muted/60 tabular-nums">
            {updatedAt.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}
          </div>
        )}
      </div>
    </div>
  );
}
