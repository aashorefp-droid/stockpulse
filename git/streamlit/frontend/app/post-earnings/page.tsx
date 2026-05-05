"use client";
import { useState, useEffect } from "react";
import Link from "next/link";
import { downloadCsv } from "@/lib/csv";

const API = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

function todayStr() {
  return new Date().toISOString().split("T")[0];
}

interface TradeRow {
  ticker: string;
  date?: string;
  direction?: string;
  pre_drift?: number;
  entry?: number;
  exit?: number;
  gap_pct?: number;
  day_pct?: number;
  pnl_pct?: number;
  win?: boolean;
  eps_surprise?: number;
  beat?: boolean;
  vol_ratio?: number;
  reason?: string;
  error?: string;
}

interface BatchResponse {
  date: string;
  results: TradeRow[];
}

function fmtPct(v?: number | null) {
  if (v == null) return "—";
  return (v > 0 ? "+" : "") + v.toFixed(1) + "%";
}

function fmt(v?: number | null, d = 2) {
  if (v == null) return "—";
  return v.toFixed(d);
}

function pctColor(v?: number | null) {
  if (v == null) return "text-[#8b949e]";
  return v > 0 ? "text-[#00e5a0]" : v < 0 ? "text-[#ff4d4f]" : "text-[#8b949e]";
}

export default function PostEarningsPage() {
  const [date, setDate] = useState(todayStr());
  const [tickersInput, setTickersInput] = useState("");
  const [loading, setLoading] = useState(false);
  const [data, setData] = useState<BatchResponse | null>(null);
  const [error, setError] = useState("");

  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    const t = params.get("tickers");
    if (t) setTickersInput(t.toUpperCase());
    const d = params.get("date");
    if (d) setDate(d);
  }, []);

  async function analyze() {
    setLoading(true);
    setError("");
    setData(null);
    try {
      const params = new URLSearchParams({ date });
      const trimmed = tickersInput.trim();
      if (trimmed) params.set("tickers", trimmed);
      const res = await fetch(`${API}/api/earnings/post-analysis?${params}`);
      if (!res.ok) {
        const j = await res.json();
        throw new Error(j.detail || "Request failed");
      }
      const j: BatchResponse = await res.json();
      setData(j);
      if (j.results.length === 0)
        setError(
          trimmed
            ? "No earnings data found for those tickers on this date."
            : "No earnings reporters found in our watchlist. Try entering tickers manually."
        );
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : "Unknown error");
    } finally {
      setLoading(false);
    }
  }

  const rows = data?.results ?? [];
  const goodRows = rows.filter((r) => !r.error);
  const badRows  = rows.filter((r) =>  r.error);

  return (
    <div className="max-w-6xl mx-auto px-4 py-6 space-y-6">
      <div>
        <h1 className="text-2xl font-bold text-[#58a6ff]">Post-Earnings Analysis</h1>
        <p className="text-[#8b949e] text-sm mt-1">
          Earnings-day backtest trade for each reporter — pre-drift, entry/exit, EPS surprise, and reason.
        </p>
      </div>

      {/* Controls */}
      <div className="bg-[#161b22] border border-[#30363d] rounded-xl p-4">
        <div className="flex flex-wrap gap-4 items-end">
          <div>
            <label className="block text-xs text-[#8b949e] mb-1.5 uppercase tracking-wider">
              Earnings Date
            </label>
            <input
              type="date"
              className="bg-[#0d1117] border border-[#30363d] rounded-lg px-3 py-2 text-white text-sm focus:outline-none focus:border-[#58a6ff]"
              value={date}
              onChange={(e) => setDate(e.target.value)}
              onKeyDown={(e) => e.key === "Enter" && analyze()}
            />
          </div>

          <div className="flex-1 min-w-56">
            <label className="block text-xs text-[#8b949e] mb-1.5 uppercase tracking-wider">
              Tickers — optional, comma-separated
            </label>
            <input
              className="w-full bg-[#0d1117] border border-[#30363d] rounded-lg px-3 py-2 text-white text-sm uppercase placeholder-[#8b949e] focus:outline-none focus:border-[#58a6ff]"
              placeholder="e.g. AAPL, TSLA, NVDA  (blank = auto-detect top 5)"
              value={tickersInput}
              onChange={(e) => setTickersInput(e.target.value.toUpperCase())}
              onKeyDown={(e) => e.key === "Enter" && analyze()}
            />
          </div>

          <button
            className="bg-[#58a6ff] text-black font-semibold px-8 py-2 rounded-lg hover:bg-[#58a6ff]/80 transition-colors disabled:opacity-50 h-[38px]"
            onClick={analyze}
            disabled={loading}
          >
            {loading ? "Analyzing…" : "Analyze"}
          </button>
        </div>

        {loading && (
          <p className="text-xs text-[#8b949e] animate-pulse mt-3">
            {tickersInput.trim()
              ? "Running backtest per ticker…"
              : "Scanning earnings calendar then running backtests… (15–20 s)"}
          </p>
        )}
      </div>

      {error && <p className="text-[#ff4d4f] text-sm">{error}</p>}

      {/* Results table */}
      {goodRows.length > 0 && (
        <div className="bg-[#161b22] border border-[#30363d] rounded-xl overflow-hidden">
          <div className="px-4 py-3 border-b border-[#30363d] flex items-center justify-between">
            <span className="text-xs text-[#8b949e] uppercase tracking-wider">
              {goodRows.length} trade{goodRows.length !== 1 ? "s" : ""} — {data!.date}
            </span>
            <button
              onClick={() => downloadCsv(
                `post_earnings_${data!.date}.csv`,
                ["Ticker","Date","Direction","Pre Drift%","Entry","Exit","Gap%","Day%","PnL%","EPS Surprise","Vol×","Reason"],
                goodRows.map(r => [r.ticker, r.date, r.direction, r.pre_drift, r.entry, r.exit, r.gap_pct, r.day_pct, r.pnl_pct, r.eps_surprise, r.vol_ratio, r.reason])
              )}
              className="px-3 py-1 text-xs rounded-lg border border-[#30363d] text-[#8b949e] hover:text-white hover:border-white/20 transition-colors"
            >
              ⬇ CSV
            </button>
          </div>
          <div className="overflow-x-auto">
            <table className="w-full text-xs whitespace-nowrap">
              <thead>
                <tr className="text-[#8b949e] border-b border-[#30363d]">
                  <th className="text-left px-4 py-2">Ticker</th>
                  <th className="text-left px-3 py-2">Date</th>
                  <th className="text-right px-3 py-2">Dir</th>
                  <th className="text-right px-3 py-2">Pre Drift</th>
                  <th className="text-right px-3 py-2">Entry</th>
                  <th className="text-right px-3 py-2">Exit</th>
                  <th className="text-right px-3 py-2">Day%</th>
                  <th className="text-right px-3 py-2">PnL%</th>
                  <th className="text-right px-3 py-2">EPS Surp</th>
                  <th className="text-right px-3 py-2">Vol×</th>
                  <th className="text-left px-3 py-2">Reason</th>
                </tr>
              </thead>
              <tbody>
                {goodRows.map((r, i) => (
                  <tr
                    key={i}
                    className={`border-b border-[#30363d] hover:bg-slate-800/30 ${
                      r.win ? "bg-green-950/20" : "bg-red-950/10"
                    }`}
                  >
                    <td className="px-4 py-2 font-bold">
                      <Link href={`/earnings?ticker=${r.ticker}`} className="text-[#58a6ff] hover:underline">
                        {r.ticker}
                      </Link>
                    </td>
                    <td className="px-3 py-2 text-[#8b949e]">{r.date}</td>
                    <td className={`px-3 py-2 text-right font-semibold ${
                      r.direction === "LONG" ? "text-[#00e5a0]" : "text-[#ff4d4f]"
                    }`}>
                      {r.direction ?? "—"}
                    </td>
                    <td className={`px-3 py-2 text-right ${pctColor(r.pre_drift)}`}>
                      {fmtPct(r.pre_drift)}
                    </td>
                    <td className="px-3 py-2 text-right text-[#8b949e]">${fmt(r.entry)}</td>
                    <td className="px-3 py-2 text-right text-[#8b949e]">${fmt(r.exit)}</td>
                    <td className={`px-3 py-2 text-right ${pctColor(r.day_pct)}`}>
                      {fmtPct(r.day_pct)}
                    </td>
                    <td className={`px-3 py-2 text-right font-bold ${r.win ? "text-[#00e5a0]" : "text-[#ff4d4f]"}`}>
                      {fmtPct(r.pnl_pct)}
                    </td>
                    <td className={`px-3 py-2 text-right ${pctColor(r.eps_surprise)}`}>
                      {r.eps_surprise != null ? fmtPct(r.eps_surprise) : "—"}
                    </td>
                    <td className="px-3 py-2 text-right text-[#8b949e]">
                      {r.vol_ratio != null ? r.vol_ratio.toFixed(2) + "×" : "—"}
                    </td>
                    <td className={`px-3 py-2 italic ${r.win ? "text-[#00e5a0]/70" : "text-orange-400"}`}>
                      {r.reason ?? ""}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {/* Errors */}
      {badRows.length > 0 && (
        <div className="text-xs text-[#8b949e] space-y-1">
          {badRows.map((r, i) => (
            <div key={i}>
              <span className="text-white">{r.ticker}</span>: {r.error}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
