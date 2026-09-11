"use client";
import { useState, useEffect, useCallback } from "react";
import { downloadCsv } from "@/lib/csv";

const API = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

interface Prediction {
  direction?: string;
  confidence?: string;
  score?: number;
  squeeze?: boolean;
  error?: string;
}

interface ScheduleRow {
  ticker: string;
  earnings_date: string | null;
  day_of_week: string | null;
  timing: "BMO" | "AMC" | "Unknown";
  is_today: boolean;
  in_week: boolean;
  prediction?: Prediction;
}

interface WeekData {
  week_start: string;
  week_end: string;
  schedule: ScheduleRow[];
}

interface TodayRow {
  ticker: string;
  date: string;
  eps_estimate: number | null;
  eps_actual: number | null;
  surprise_pct: number | null;
  eps_beat: number | null;
  pre_notified: number;
  post_notified: number;
  pre_drift: number | null;
  gap_pct: number | null;
  day_pct: number | null;
  direction: string | null;
  pnl_pct: number | null;
  reason: string | null;
}

function pctColor(v?: number | null) {
  if (v == null) return "text-slate-400";
  return v > 0 ? "text-green-400" : v < 0 ? "text-red-400" : "text-slate-400";
}

function fmtPct(v?: number | null) {
  if (v == null) return "—";
  return (v > 0 ? "+" : "") + v.toFixed(1) + "%";
}

function PredictionBadge({ p }: { p?: Prediction }) {
  if (!p || p.error) return <span className="text-slate-600 text-xs">—</span>;
  const dir = p.direction ?? "NEUTRAL";
  const cls =
    dir.includes("BULL") ? "bg-green-900/60 text-green-300" :
    dir.includes("BEAR") ? "bg-red-900/60 text-red-400" :
    "bg-slate-800 text-slate-400";
  const short =
    dir === "BULLISH"      ? "BULL ↑↑" :
    dir === "LEAN BULLISH" ? "BULL ↑"  :
    dir === "BEARISH"      ? "BEAR ↓↓" :
    dir === "LEAN BEARISH" ? "BEAR ↓"  : "NEUT";
  return (
    <span className="inline-flex items-center gap-1">
      <span className={`text-[10px] font-bold px-1.5 py-0.5 rounded ${cls}`}>
        {short}
      </span>
      <span className="text-[10px] text-slate-500">
        {p.score != null ? (p.score > 0 ? `+${p.score}` : p.score) : ""}
      </span>
      {p.squeeze && <span className="text-orange-400 text-[10px]">🔥</span>}
    </span>
  );
}

function TimingBadge({ timing }: { timing: string }) {
  const cls =
    timing === "BMO" ? "bg-blue-900/60 text-blue-300" :
    timing === "AMC" ? "bg-purple-900/60 text-purple-300" :
    "bg-slate-800 text-slate-400";
  return <span className={`text-xs px-2 py-0.5 rounded font-semibold ${cls}`}>{timing}</span>;
}

function AlertBadge({ sent }: { sent: boolean }) {
  return sent
    ? <span className="text-green-400 text-xs font-semibold">✓ Sent</span>
    : <span className="text-slate-500 text-xs">—</span>;
}

const CACHE_KEY_WEEK  = "tracker_week_cache";
const CACHE_KEY_TODAY = "tracker_today_cache";
const CACHE_KEY_TS    = "tracker_cache_ts";

export default function TrackerPage() {
  const [input, setInput]       = useState("");
  const [week, setWeek]         = useState<WeekData | null>(() => {
    try { const s = localStorage.getItem(CACHE_KEY_WEEK); return s ? JSON.parse(s) : null; } catch { return null; }
  });
  const [today, setToday]       = useState<TodayRow[]>(() => {
    try { const s = localStorage.getItem(CACHE_KEY_TODAY); return s ? JSON.parse(s) : []; } catch { return []; }
  });
  const [loading, setLoading]   = useState(false);
  const [refreshing, setRefreshing] = useState(false);
  const [adding, setAdding]     = useState(false);
  const [error, setError]       = useState("");
  const [lastUpdated, setLastUpdated] = useState<string>(() => {
    try { return localStorage.getItem(CACHE_KEY_TS) ?? ""; } catch { return ""; }
  });

  const loadWeek = useCallback(async (silent = false) => {
    if (silent) setRefreshing(true); else setLoading(true);
    try {
      const [wRes, tRes] = await Promise.all([
        fetch(`${API}/api/tracker/week`),
        fetch(`${API}/api/tracker/today`),
      ]);
      if (wRes.ok) {
        const data = await wRes.json();
        setWeek(data);
        try { localStorage.setItem(CACHE_KEY_WEEK, JSON.stringify(data)); } catch {}
      }
      if (tRes.ok) {
        const data = (await tRes.json()).tickers ?? [];
        setToday(data);
        try { localStorage.setItem(CACHE_KEY_TODAY, JSON.stringify(data)); } catch {}
      }
      const ts = new Date().toLocaleTimeString();
      setLastUpdated(ts);
      try { localStorage.setItem(CACHE_KEY_TS, ts); } catch {}
    } catch {
      if (!silent) setError("Failed to load tracker data");
    } finally {
      if (silent) setRefreshing(false); else setLoading(false);
    }
  }, []);

  useEffect(() => {
    // If we have cached data, do a silent background refresh; otherwise show loading
    const hasCached = !!localStorage.getItem(CACHE_KEY_WEEK);
    loadWeek(!hasCached ? false : true);
  }, [loadWeek]);

  async function addTickers() {
    const tickers = input.split(",").map(t => t.trim().toUpperCase()).filter(Boolean);
    if (!tickers.length) return;
    setAdding(true);
    setError("");
    try {
      const res = await fetch(`${API}/api/tracker/watchlist`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ tickers }),
      });
      if (!res.ok) throw new Error("Add failed");
      setInput("");
      await loadWeek();
    } catch {
      setError("Failed to add tickers");
    } finally {
      setAdding(false);
    }
  }

  async function deleteTicker(ticker: string) {
    await fetch(`${API}/api/tracker/watchlist/${ticker}`, { method: "DELETE" });
    await loadWeek();
  }

  async function deleteAll() {
    if (!confirm("Remove all tickers from watchlist?")) return;
    await fetch(`${API}/api/tracker/watchlist`, { method: "DELETE" });
    await loadWeek();
  }

  // Map today's DB rows by ticker for quick lookup
  const todayMap = Object.fromEntries(today.map(r => [r.ticker, r]));

  const weekRows  = week?.schedule ?? [];
  const inWeek    = weekRows.filter(r => r.in_week);
  const outOfWeek = weekRows.filter(r => !r.in_week);

  return (
    <div className="max-w-5xl mx-auto px-4 py-8 space-y-6">
      <div className="flex items-center justify-between flex-wrap gap-3">
        <h1 className="text-xl font-bold">Earnings Watchlist</h1>
        <div className="flex gap-2">
          {lastUpdated && (
            <span className="text-[10px] text-slate-600 self-center">updated {lastUpdated}</span>
          )}
          <button
            onClick={() => loadWeek(false)}
            disabled={loading || refreshing}
            className="text-xs border border-slate-700 px-3 py-1.5 rounded hover:bg-slate-800 transition-colors disabled:opacity-50"
          >
            {loading || refreshing ? "⟳ Refreshing…" : "⟳ Refresh"}
          </button>
          {weekRows.length > 0 && (
            <button onClick={deleteAll} className="text-xs text-red-400 hover:text-red-300 border border-red-900/50 px-3 py-1.5 rounded hover:bg-red-950/40 transition-colors">
              Delete All
            </button>
          )}
        </div>
      </div>

      {/* Add tickers */}
      <div className="card space-y-2">
        <label className="text-xs text-muted uppercase tracking-wider">Add Tickers (comma-separated)</label>
        <div className="flex gap-2">
          <input
            className="input flex-1 font-mono text-sm"
            placeholder="AAPL, MSFT, NVDA, GOOGL"
            value={input}
            onChange={e => setInput(e.target.value)}
            onKeyDown={e => e.key === "Enter" && addTickers()}
          />
          <button className="btn-primary px-5" onClick={addTickers} disabled={adding || !input.trim()}>
            {adding ? "Adding…" : "Add"}
          </button>
        </div>
        {error && <p className="text-red-400 text-xs">{error}</p>}
      </div>

      {loading && !week && <p className="text-muted text-sm">Loading schedule…</p>}

      {/* Week schedule table */}
      {!loading && week && (
        <div className="card space-y-3">
          <div className="flex items-center justify-between">
            <h2 className="font-semibold text-sm text-muted uppercase tracking-wider">
              Week of {week.week_start} – {week.week_end}
            </h2>
            <div className="flex items-center gap-2">
              {refreshing && <span className="text-[10px] text-slate-500 animate-pulse">updating…</span>}
              <span className="text-xs text-muted">{inWeek.length} reporting this week</span>
              {weekRows.length > 0 && (
                <button
                  onClick={() => downloadCsv(
                    `earnings_tracker_${week.week_start}.csv`,
                    ["Ticker","Date","Day","When","Direction","Confidence","EPS Est","EPS Actual","Surprise%","Day%","Pre Alert","Post Alert"],
                    weekRows.map(row => {
                      const db = todayMap[row.ticker];
                      return [row.ticker, row.earnings_date, row.day_of_week, row.timing, row.prediction?.direction, row.prediction?.confidence, db?.eps_estimate, db?.eps_actual, db?.surprise_pct, db?.day_pct, db?.pre_notified ? "Y" : "", db?.post_notified ? "Y" : ""];
                    })
                  )}
                  className="px-3 py-1 text-xs rounded-lg border border-border text-muted hover:text-white hover:border-white/20 transition-colors"
                >
                  ⬇ CSV
                </button>
              )}
            </div>
          </div>

          {weekRows.length === 0 ? (
            <p className="text-muted text-sm">No tickers in watchlist. Add some above.</p>
          ) : (
            <div className="overflow-x-auto">
              <table className="w-full text-xs whitespace-nowrap">
                <thead>
                  <tr className="text-muted border-b border-slate-700">
                    <th className="text-left py-2 pr-3">Ticker</th>
                    <th className="text-left pr-3">Date</th>
                    <th className="text-left pr-3">Day</th>
                    <th className="text-left pr-3">When</th>
                    <th className="text-left pr-3">Prediction</th>
                    <th className="text-right pr-3">EPS Est</th>
                    <th className="text-right pr-3">EPS Act</th>
                    <th className="text-right pr-3">Surp%</th>
                    <th className="text-right pr-3">Day%</th>
                    <th className="text-center pr-3">Pre Alert</th>
                    <th className="text-center pr-3">Post Alert</th>
                    <th className="text-center">Del</th>
                  </tr>
                </thead>
                <tbody>
                  {/* This-week rows first */}
                  {inWeek.map(row => {
                    const db = todayMap[row.ticker];
                    return (
                      <tr
                        key={row.ticker}
                        className={`border-b border-slate-800 hover:bg-slate-800/30 ${
                          row.is_today ? "bg-yellow-950/20 border-l-2 border-l-yellow-500" : ""
                        }`}
                      >
                        <td className="py-2 pr-3 font-semibold">
                          <a href={`/earnings?ticker=${row.ticker}`} className="hover:text-accent">
                            {row.ticker}
                          </a>
                          {row.is_today && <span className="ml-1.5 text-yellow-400 text-[10px] font-bold">TODAY</span>}
                        </td>
                        <td className="pr-3 text-slate-300">{row.earnings_date ?? "—"}</td>
                        <td className="pr-3 text-slate-400">{row.day_of_week ?? "—"}</td>
                        <td className="pr-3"><TimingBadge timing={row.timing} /></td>
                        <td className="pr-3"><PredictionBadge p={row.prediction} /></td>
                        <td className="text-right pr-3 text-slate-400">
                          {db?.eps_estimate != null ? `$${db.eps_estimate.toFixed(2)}` : "—"}
                        </td>
                        <td className="text-right pr-3">
                          {db?.eps_actual != null ? `$${db.eps_actual.toFixed(2)}` : "—"}
                        </td>
                        <td className={`text-right pr-3 ${pctColor(db?.surprise_pct)}`}>
                          {fmtPct(db?.surprise_pct)}
                        </td>
                        <td className={`text-right pr-3 font-semibold ${pctColor(db?.day_pct)}`}>
                          {fmtPct(db?.day_pct)}
                        </td>
                        <td className="text-center pr-3"><AlertBadge sent={!!db?.pre_notified} /></td>
                        <td className="text-center pr-3"><AlertBadge sent={!!db?.post_notified} /></td>
                        <td className="text-center">
                          <button onClick={() => deleteTicker(row.ticker)} className="text-slate-600 hover:text-red-400 transition-colors px-1">✕</button>
                        </td>
                      </tr>
                    );
                  })}

                  {/* Divider if there are out-of-week tickers */}
                  {inWeek.length > 0 && outOfWeek.length > 0 && (
                    <tr>
                      <td colSpan={11} className="py-1.5 text-[10px] text-muted uppercase tracking-wider pl-0.5 border-b border-slate-700">
                        Not this week
                      </td>
                    </tr>
                  )}

                  {/* Out-of-week rows */}
                  {outOfWeek.map(row => (
                    <tr key={row.ticker} className="border-b border-slate-800/50 opacity-50 hover:opacity-80 hover:bg-slate-800/20">
                      <td className="py-1.5 pr-3 font-semibold">
                        <a href={`/earnings?ticker=${row.ticker}`} className="hover:text-accent">{row.ticker}</a>
                      </td>
                      <td className="pr-3 text-slate-500">{row.earnings_date ?? "No date found"}</td>
                      <td className="pr-3 text-slate-600">{row.day_of_week ?? "—"}</td>
                      <td className="pr-3"><TimingBadge timing={row.timing} /></td>
                      <td className="pr-3"><PredictionBadge p={row.prediction} /></td>
                      <td colSpan={5} />
                      <td className="text-center">
                        <button onClick={() => deleteTicker(row.ticker)} className="text-slate-600 hover:text-red-400 transition-colors px-1">✕</button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>
      )}

      {/* Today's alert detail */}
      {today.length > 0 && (
        <div className="card space-y-3">
          <h2 className="font-semibold text-sm text-muted uppercase tracking-wider">
            Today's Alerts — {today[0]?.date}
          </h2>
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
            {today.map(r => (
              <div key={r.ticker} className={`rounded-lg border p-3 space-y-1 text-xs ${
                r.post_notified ? "border-green-800/40 bg-green-950/20" :
                r.pre_notified  ? "border-yellow-800/40 bg-yellow-950/20" :
                "border-slate-700 bg-slate-800/30"
              }`}>
                <div className="flex items-center justify-between">
                  <a href={`/earnings?ticker=${r.ticker}`} className="font-bold text-sm hover:text-accent">{r.ticker}</a>
                  <div className="flex gap-2">
                    <span className={`px-1.5 py-0.5 rounded text-[10px] font-bold ${r.pre_notified ? "bg-yellow-900/60 text-yellow-300" : "bg-slate-800 text-slate-500"}`}>
                      PRE {r.pre_notified ? "✓" : "—"}
                    </span>
                    <span className={`px-1.5 py-0.5 rounded text-[10px] font-bold ${r.post_notified ? "bg-green-900/60 text-green-300" : "bg-slate-800 text-slate-500"}`}>
                      POST {r.post_notified ? "✓" : "—"}
                    </span>
                  </div>
                </div>
                <div className="flex gap-4 text-muted">
                  {r.eps_estimate != null && <span>Est: ${r.eps_estimate.toFixed(2)}</span>}
                  {r.eps_actual   != null && <span>Act: ${r.eps_actual.toFixed(2)}</span>}
                  {r.surprise_pct != null && (
                    <span className={pctColor(r.surprise_pct)}>Surp: {fmtPct(r.surprise_pct)}</span>
                  )}
                </div>
                {(r.day_pct != null || r.pnl_pct != null) && (
                  <div className="flex gap-4">
                    {r.day_pct != null && <span className={pctColor(r.day_pct)}>Day: {fmtPct(r.day_pct)}</span>}
                    {r.direction && <span className={r.direction === "LONG" ? "text-green-400" : "text-red-400"}>{r.direction}</span>}
                    {r.pnl_pct != null && <span className={pctColor(r.pnl_pct)}>PnL: {fmtPct(r.pnl_pct)}</span>}
                  </div>
                )}
                {r.reason && <div className="text-slate-500 italic">{r.reason}</div>}
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
