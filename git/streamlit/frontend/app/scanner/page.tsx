"use client";
import { useState, useRef, useEffect } from "react";
import Link from "next/link";
import { downloadCsv } from "@/lib/csv";

const API_BASE = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

const WATCHLISTS = [
  { key: "default",       label: "Default 50",      count: 50 },
  { key: "tech",          label: "Tech 30",          count: 30 },
  { key: "mega_cap",      label: "Mega Cap 20",      count: 20 },
  { key: "momentum",      label: "Momentum 20",      count: 20 },
  { key: "etfs",          label: "ETFs 20",          count: 20 },
  { key: "short_squeeze", label: "🔥 Short Squeeze", count: 40 },
  { key: "custom",        label: "Custom",           count: 0  },
];

type Filter = "all" | "rank1" | "exceptional" | "high_short";

interface OptLeg {
  action:     string;
  type:       string;
  strike:     number;
  exp:        string;
  bid:        number;
  ask:        number;
  mid:        number;
  spread_pct?: number | null;
}

interface ScanResult {
  ticker:        string;
  price?:        number;
  verdict?:      string;
  confidence?:   string;
  score?:        number;
  direction?:    string;
  entry_grade?:  string;
  entry_label?:  string;
  grade_color?:  string;
  expected_wr?:  number;
  mtf_rank?:     number;
  mtf_signal?:   string;
  mtf_action?:   string;
  mtf_key?:      string;
  weekly_bias?:  string;
  daily_bias?:   string;
  vol_trend?:    string;
  vol_surge?:    boolean;
  breakout_score?: number;
  dist_from_high?: number;
  entry?:        number;
  stop_loss?:    number;
  target1?:      number;
  risk_pct?:     number;
  rr_t1?:        number;
  atr?:          number;
  short_pct?:    number | null;
  opt_strategy?:  string | null;
  opt_summary?:   string | null;
  opt_debit?:     number | null;
  opt_profit?:    number | null;
  opt_source?:    string | null;
  opt_quote_ts?:  string | null;
  opt_legs?:      OptLeg[] | null;
  opt_width?:     number | null;
  opt_exp_short?: string | null;
  opt_exp_long?:  string | null;
  opt_alt?:       string | null;
  error?:         string | null;
  done?:         boolean;
  total?:        number;
}

const verdictColor: Record<string, string> = {
  "BULLISH":      "text-green",
  "LEAN BULLISH": "text-green/70",
  "BEARISH":      "text-red",
  "LEAN BEARISH": "text-red/70",
  "NEUTRAL":      "text-muted",
};

const biasColor: Record<string, string> = {
  BULLISH: "text-green", BEARISH: "text-red", NEUTRAL: "text-muted",
};

const gradeColor: Record<string, string> = {
  S: "bg-green/20 text-green border-green/30",
  A: "bg-green/10 text-green border-green/20",
  B: "bg-accent/10 text-accent border-accent/20",
  "B-": "bg-accent/5 text-accent border-accent/10",
  C: "bg-yellow/10 text-yellow border-yellow/20",
  D: "bg-red/5 text-muted border-border",
};

function Badge({ text, color }: { text: string; color: string }) {
  return <span className={`text-[10px] font-mono font-semibold px-1.5 py-0.5 rounded border ${color}`}>{text}</span>;
}

function prevTradingDay(dateStr: string): { date: string; note: string | null } {
  const [y, m, d] = dateStr.split("-").map(Number);
  const jsDay = new Date(Date.UTC(y, m - 1, d)).getUTCDay(); // 0=Sun, 6=Sat
  if (jsDay === 6) {
    const fri = new Date(Date.UTC(y, m - 1, d - 1));
    return { date: fri.toISOString().split("T")[0], note: `Sat ${dateStr} → using Fri close` };
  }
  if (jsDay === 0) {
    const fri = new Date(Date.UTC(y, m - 1, d - 2));
    return { date: fri.toISOString().split("T")[0], note: `Sun ${dateStr} → using Fri close` };
  }
  return { date: dateStr, note: null };
}

export default function ScannerPage() {
  const [watchlist,    setWatchlist]    = useState("default");
  const [customInput,  setCustomInput]  = useState("");
  const [scanning,     setScanning]     = useState(false);
  const [results,      setResults]      = useState<ScanResult[]>([]);
  const [progress,     setProgress]     = useState({ done: 0, total: 0 });
  const [filter,       setFilter]       = useState<Filter>("all");
  const [sortBy,       setSortBy]       = useState<"score" | "grade" | "rr">("score");
  const [optModal,     setOptModal]     = useState<{ r: ScanResult } | null>(null);
  const [copied,       setCopied]       = useState(false);
  const [mode,         setMode]         = useState<"live" | "backtest">("live");
  const [backtestDate, setBacktestDate] = useState("");
  const [activeBacktestDate, setActiveBacktestDate] = useState<string | null>(null);
  const esRef = useRef<EventSource | null>(null);

  useEffect(() => {
    if (!optModal) return;
    function onKey(e: KeyboardEvent) { if (e.key === "Escape") setOptModal(null); }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [optModal]);

  function copyText(text: string) {
    navigator.clipboard.writeText(text).catch(() => {});
    setCopied(true);
    setTimeout(() => setCopied(false), 1500);
  }

  function startScan() {
    if (esRef.current) esRef.current.close();
    setResults([]);

    let url: string;
    let total: number;

    if (watchlist === "custom") {
      const tickers = customInput.split(",").map(t => t.trim().toUpperCase()).filter(Boolean);
      if (!tickers.length) return;
      total = tickers.length;
      url = `${API_BASE}/api/scanner/stream?tickers=${encodeURIComponent(tickers.join(","))}`;
    } else {
      total = WATCHLISTS.find(w => w.key === watchlist)?.count ?? 50;
      url = `${API_BASE}/api/scanner/stream?watchlist=${watchlist}`;
    }

    if (mode === "backtest" && backtestDate) {
      url += `&as_of=${backtestDate}`;
      setActiveBacktestDate(backtestDate);
    } else {
      setActiveBacktestDate(null);
    }

    setProgress({ done: 0, total });
    setScanning(true);

    const es = new EventSource(url);
    esRef.current = es;

    es.onmessage = (e) => {
      const data: ScanResult = JSON.parse(e.data);
      if (data.done) {
        setScanning(false);
        setProgress(p => ({ ...p, done: data.total ?? p.done }));
        es.close();
        return;
      }
      setResults(prev => [...prev, data]);
      setProgress(p => ({ ...p, done: p.done + 1 }));
    };

    es.onerror = () => {
      setScanning(false);
      es.close();
    };
  }

  function stopScan() {
    esRef.current?.close();
    setScanning(false);
  }

  const gradeRank: Record<string, number> = { S: 0, A: 1, B: 2, "B-": 3, C: 4, D: 5 };

  const filtered = results
    .filter(r => !r.error && r.verdict)
    .filter(r => {
      if (filter === "rank1")     return r.mtf_rank === 1;
      if (filter === "high_short") return (r.short_pct ?? 0) >= 10;
      // Matches original: score≥4 + HIGH conf + grade A/S + rank1 + ACCUMULATING vol
      if (filter === "exceptional")
        return ["S", "A"].includes(r.entry_grade ?? "")
          && r.mtf_rank === 1
          && r.vol_trend === "ACCUMULATING";
      return true;
    })
    .sort((a, b) => {
      if (sortBy === "score")  return Math.abs(b.score ?? 0) - Math.abs(a.score ?? 0);
      if (sortBy === "grade")  return (gradeRank[a.entry_grade ?? "D"] ?? 5) - (gradeRank[b.entry_grade ?? "D"] ?? 5);
      if (sortBy === "rr")     return (b.rr_t1 ?? 0) - (a.rr_t1 ?? 0);
      return 0;
    });

  const errors  = results.filter(r => r.error);
  const pct     = progress.total > 0 ? Math.round((progress.done / progress.total) * 100) : 0;

  return (
    <div className="space-y-6">

      {/* ── Header ── */}
      <div className="flex flex-wrap items-end gap-4">
        <div>
          <h1 className="text-3xl font-bold text-white">Scanner</h1>
          <p className="text-muted text-sm mt-0.5">Multi-stock scoring · Rank 1 signals · Exceptional setups</p>
        </div>
      </div>

      {/* ── Controls ── */}
      <div className="card space-y-4">
        <div className="flex flex-wrap gap-2 items-center">
          <span className="text-sm text-muted mr-1">Watchlist:</span>
          {WATCHLISTS.map(w => (
            <button key={w.key} onClick={() => setWatchlist(w.key)}
              className={`px-3 py-1.5 text-xs rounded-lg font-semibold border transition-colors ${
                watchlist === w.key
                  ? "bg-accent text-black border-accent"
                  : "border-border text-muted hover:text-white hover:border-white/20"
              }`}>
              {w.label}
            </button>
          ))}
        </div>

        {watchlist === "custom" && (
          <div className="flex gap-2 items-center">
            <input
              type="text"
              value={customInput}
              onChange={e => setCustomInput(e.target.value)}
              onKeyDown={e => e.key === "Enter" && !scanning && startScan()}
              placeholder="AAPL, NVDA, TSLA, MSFT ..."
              className="flex-1 max-w-lg bg-surface border border-border rounded-lg px-3 py-2 text-sm text-white placeholder-muted focus:outline-none focus:border-accent font-mono"
            />
            {customInput && (
              <span className="text-xs text-muted">
                {customInput.split(",").filter(t => t.trim()).length} ticker{customInput.split(",").filter(t => t.trim()).length !== 1 ? "s" : ""}
              </span>
            )}
          </div>
        )}

        {/* ── Mode toggle ── */}
        <div className="flex flex-wrap gap-3 items-center">
          <span className="text-sm text-muted">Mode:</span>
          <div className="flex rounded-lg border border-border overflow-hidden">
            <button
              onClick={() => setMode("live")}
              className={`px-4 py-1.5 text-xs font-semibold transition-colors ${
                mode === "live" ? "bg-accent text-black" : "text-muted hover:text-white bg-transparent"
              }`}
            >
              ▶ Live
            </button>
            <button
              onClick={() => setMode("backtest")}
              className={`px-4 py-1.5 text-xs font-semibold transition-colors border-l border-border ${
                mode === "backtest" ? "bg-accent text-black" : "text-muted hover:text-white bg-transparent"
              }`}
            >
              ⏪ Backtest
            </button>
          </div>
          {mode === "backtest" && (
            <div className="flex flex-wrap gap-2 items-center">
              <span className="text-xs text-muted">As of date:</span>
              <input
                type="date"
                value={backtestDate}
                max={new Date(Date.now() - 86400000).toISOString().split("T")[0]}
                onChange={e => setBacktestDate(e.target.value)}
                className="bg-surface border border-border rounded-lg px-3 py-1.5 text-sm text-white focus:outline-none focus:border-accent font-mono"
              />
              {!backtestDate && (
                <span className="text-xs text-yellow">Pick a date to backtest</span>
              )}
              {backtestDate && prevTradingDay(backtestDate).note && (
                <span className="text-xs text-yellow">{prevTradingDay(backtestDate).note}</span>
              )}
            </div>
          )}
        </div>

        <div className="flex gap-3 items-center">
          <button
            onClick={scanning ? stopScan : startScan}
            disabled={!scanning && mode === "backtest" && !backtestDate}
            className={`px-6 py-2 rounded-lg font-semibold text-sm transition-colors ${
              scanning
                ? "bg-red/20 text-red border border-red/30 hover:bg-red/30"
                : mode === "backtest" && !backtestDate
                  ? "bg-surface text-muted border border-border cursor-not-allowed"
                  : "bg-accent text-black hover:bg-accent/80"
            }`}>
            {scanning ? "⏹ Stop" : mode === "backtest" ? "⏪ Backtest" : "▶ Scan"}
          </button>

          {(scanning || results.length > 0) && (
            <div className="flex-1 max-w-xs">
              <div className="flex justify-between text-xs text-muted mb-1">
                <span>{progress.done} / {progress.total} scanned</span>
                <span>{pct}%</span>
              </div>
              <div className="h-1.5 bg-surface rounded-full overflow-hidden">
                <div className="h-full bg-accent transition-all duration-300 rounded-full"
                     style={{ width: `${pct}%` }} />
              </div>
            </div>
          )}

          {results.length > 0 && !scanning && (
            <span className="text-xs text-muted">
              {filtered.length} shown · {errors.length} errors
            </span>
          )}
        </div>
      </div>

      {/* ── Backtest banner ── */}
      {activeBacktestDate && results.length > 0 && (() => {
        const { date: effectiveDate, note } = prevTradingDay(activeBacktestDate);
        return (
          <div className="flex items-center gap-2 px-4 py-2 rounded-lg bg-accent/10 border border-accent/20 text-sm">
            <span className="text-accent font-semibold">⏪ Backtest</span>
            <span className="text-muted">Showing scanner results as of</span>
            <span className="font-mono text-white">{effectiveDate}</span>
            {note && <span className="text-xs text-yellow ml-1">({note})</span>}
          </div>
        );
      })()}

      {/* ── Filter + Sort ── */}
      {results.length > 0 && (
        <div className="flex flex-wrap gap-4 items-center">
          <div className="flex gap-1 bg-card border border-border rounded-lg p-1">
            {(["all", "rank1", "exceptional", "high_short"] as Filter[]).map(f => (
              <button key={f} onClick={() => setFilter(f)}
                className={`px-3 py-1 text-xs rounded-md font-semibold transition-colors ${
                  filter === f ? "bg-accent text-black" : "text-muted hover:text-white"
                }`}>
                {f === "all"        ? `All (${results.filter(r => !r.error).length})`
                : f === "rank1"     ? `Rank 1 (${results.filter(r => r.mtf_rank === 1).length})`
                : f === "high_short"? `🔥 High Short (${results.filter(r => (r.short_pct ?? 0) >= 10).length})`
                : `Exceptional (${results.filter(r => ["S","A"].includes(r.entry_grade ?? "") && r.mtf_rank === 1 && r.vol_trend === "ACCUMULATING").length})`}
              </button>
            ))}
          </div>

          <div className="flex items-center gap-2 text-xs text-muted">
            <span>Sort:</span>
            {(["score", "grade", "rr"] as const).map(s => (
              <button key={s} onClick={() => setSortBy(s)}
                className={`px-2 py-1 rounded transition-colors ${
                  sortBy === s ? "text-white font-semibold" : "hover:text-white"
                }`}>
                {s === "score" ? "Score" : s === "grade" ? "Grade" : "R/R"}
              </button>
            ))}
          </div>

          <button
            onClick={() => downloadCsv(
              `scanner_${activeBacktestDate ?? "live"}.csv`,
              ["Ticker","Price","Verdict","Score","Grade","Rank","Weekly","Daily","Entry","Stop","T1","Risk%","R/R","Short%","Options Strategy"],
              filtered.map(r => [r.ticker, r.price, r.verdict, r.score, r.entry_grade, `R${r.mtf_rank}`, r.weekly_bias, r.daily_bias, r.entry, r.stop_loss, r.target1, r.risk_pct, r.rr_t1, r.short_pct, r.opt_strategy])
            )}
            className="ml-auto px-3 py-1.5 text-xs rounded-lg border border-border text-muted hover:text-white hover:border-white/20 transition-colors"
          >
            ⬇ CSV
          </button>
        </div>
      )}

      {/* ── Results Table ── */}
      {filtered.length > 0 && (
        <div className="card p-0 overflow-hidden">
          <div className="overflow-x-auto">
            <table className="text-sm" style={{ borderCollapse: "collapse", width: "max-content", minWidth: "100%" }}>
              <thead>
                <tr className="border-b border-border text-muted text-xs">
                  {/* sticky ticker column */}
                  <th className="text-left pl-4 pr-3 py-3 whitespace-nowrap sticky left-0 bg-card z-10">Ticker</th>
                  <th className="text-right px-3 py-3 whitespace-nowrap">Price</th>
                  <th className="text-center px-3 py-3 whitespace-nowrap">Verdict</th>
                  <th className="text-center px-3 py-3 whitespace-nowrap">Score</th>
                  <th className="text-center px-3 py-3 whitespace-nowrap">Grade</th>
                  <th className="text-center px-3 py-3 whitespace-nowrap">Rank</th>
                  <th className="text-center px-3 py-3 whitespace-nowrap">W/D</th>
                  <th className="text-right px-3 py-3 whitespace-nowrap">Entry</th>
                  <th className="text-right px-3 py-3 whitespace-nowrap">Stop</th>
                  <th className="text-right px-3 py-3 whitespace-nowrap">T1</th>
                  <th className="text-right px-3 py-3 whitespace-nowrap">Risk%</th>
                  <th className="text-right px-3 py-3 whitespace-nowrap">R/R</th>
                  <th className="text-right px-3 py-3 whitespace-nowrap">Short%</th>
                  <th className="text-left px-3 py-3 whitespace-nowrap">Options Play</th>
                </tr>
              </thead>
              <tbody>
                {filtered.map((r) => {
                  const optEmoji = r.opt_strategy?.includes("Bull") ? "📈"
                                 : r.opt_strategy?.includes("Bear") ? "📉"
                                 : r.opt_strategy?.includes("Butterfly") ? "🦋"
                                 : r.opt_strategy?.includes("Straddle") ? "🦋"
                                 : r.opt_strategy ? "📊" : null;
                  const optShort = r.opt_strategy
                    ?.replace("Bull Call Spread", "BCS")
                     .replace("Bear Put Spread",  "BPS")
                     .replace("Iron Butterfly",   "Iron Fly")
                     .replace("Long Call",        "Long C")
                     .replace("Long Put",         "Long P");
                  return (
                    <tr key={r.ticker}
                      className="border-b border-border/40 hover:bg-surface/50 transition-colors">
                      <td className="pl-4 pr-3 py-2.5 whitespace-nowrap sticky left-0 bg-card">
                        <Link href={`/stock/${r.ticker}`}
                          className="font-bold text-white hover:text-accent transition-colors">
                          {r.ticker}
                        </Link>
                        {r.vol_surge && <span className="ml-1 text-[10px] text-yellow">⚡</span>}
                      </td>
                      <td className="px-3 py-2.5 text-right font-mono text-white whitespace-nowrap">
                        ${r.price?.toFixed(2)}
                      </td>
                      <td className="px-3 py-2.5 text-center whitespace-nowrap">
                        <span className={`text-xs font-semibold ${verdictColor[r.verdict ?? ""] ?? "text-muted"}`}>
                          {r.verdict}
                        </span>
                      </td>
                      <td className="px-3 py-2.5 text-center whitespace-nowrap">
                        <span className={`font-mono font-bold text-sm ${
                          (r.score ?? 0) > 0 ? "text-green" : (r.score ?? 0) < 0 ? "text-red" : "text-muted"
                        }`}>
                          {(r.score ?? 0) > 0 ? `+${r.score}` : r.score}
                        </span>
                      </td>
                      <td className="px-3 py-2.5 text-center whitespace-nowrap">
                        <span className={`text-xs font-bold px-2 py-0.5 rounded border ${gradeColor[r.entry_grade ?? "D"] ?? gradeColor.D}`}>
                          {r.entry_grade}
                        </span>
                      </td>
                      <td className="px-3 py-2.5 text-center whitespace-nowrap">
                        <span className={`font-mono text-xs ${
                          r.mtf_rank === 1 ? "text-green font-bold" :
                          r.mtf_rank === 2 ? "text-accent" :
                          r.mtf_rank === 3 ? "text-yellow" : "text-muted"
                        }`}>R{r.mtf_rank}</span>
                      </td>
                      <td className="px-3 py-2.5 text-center font-mono text-xs text-muted whitespace-nowrap">
                        {r.weekly_bias?.[0]}/{r.daily_bias?.[0]}
                      </td>
                      <td className="px-3 py-2.5 text-right font-mono text-accent text-xs whitespace-nowrap">
                        {r.entry ? `$${r.entry}` : "—"}
                      </td>
                      <td className="px-3 py-2.5 text-right font-mono text-red text-xs whitespace-nowrap">
                        {r.stop_loss ? `$${r.stop_loss}` : "—"}
                      </td>
                      <td className="px-3 py-2.5 text-right font-mono text-green text-xs whitespace-nowrap">
                        {r.target1 ? `$${r.target1}` : "—"}
                      </td>
                      <td className="px-3 py-2.5 text-right font-mono text-xs text-muted whitespace-nowrap">
                        {r.risk_pct ? `${r.risk_pct}%` : "—"}
                      </td>
                      <td className="px-3 py-2.5 text-right font-mono text-xs text-accent whitespace-nowrap">
                        {r.rr_t1 ? `${r.rr_t1}R` : "—"}
                      </td>
                      <td className="px-3 py-2.5 text-right font-mono text-xs whitespace-nowrap">
                        {r.short_pct != null
                          ? <span className={r.short_pct >= 20 ? "text-red font-bold" : r.short_pct >= 10 ? "text-yellow" : "text-muted"}>
                              {r.short_pct}%
                            </span>
                          : <span className="text-muted">—</span>}
                      </td>
                      <td
                        className="px-3 py-2.5 text-left whitespace-nowrap"
                        onDoubleClick={() => r.opt_summary && setOptModal({ r })}
                        title="Double-click to view & copy"
                      >
                        {optEmoji && optShort ? (
                          <span className="flex items-center gap-1 cursor-pointer select-none group">
                            <span className={`text-[9px] font-bold px-1 py-0.5 rounded border ${
                              r.opt_source === "alpaca"
                                ? "bg-accent/10 text-accent border-accent/30"
                                : "bg-muted/10 text-muted border-border"
                            }`}>
                              {r.opt_source === "alpaca" ? "A" : "Y"}
                            </span>
                            <span className="text-xs font-mono">
                              {optEmoji}{" "}
                              <span className={
                                r.direction === "LONG"  ? "text-green" :
                                r.direction === "SHORT" ? "text-red"   : "text-accent"
                              }>{optShort}</span>
                              {r.opt_debit != null && (
                                <span className="text-muted ml-1">${r.opt_debit}</span>
                              )}
                              {r.opt_profit != null && (
                                <span className="text-green ml-1">→${r.opt_profit}</span>
                              )}
                            </span>
                            <span className="text-muted/40 text-[10px] opacity-0 group-hover:opacity-100 transition-opacity">⤢</span>
                          </span>
                        ) : (
                          <span className="text-muted text-xs">—</span>
                        )}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {/* ── Scanning skeleton ── */}
      {scanning && filtered.length === 0 && (
        <div className="card flex flex-col items-center justify-center py-16 gap-3">
          <div className="w-8 h-8 border-2 border-accent border-t-transparent rounded-full animate-spin" />
          <p className="text-muted text-sm">Scanning {progress.total} stocks…</p>
        </div>
      )}

      {/* ── Empty state ── */}
      {!scanning && results.length > 0 && filtered.length === 0 && (
        <div className="card flex items-center justify-center py-12">
          <p className="text-muted text-sm">No stocks match the current filter.</p>
        </div>
      )}

      {/* ── Options summary modal ── */}
      {optModal && (
        <div
          className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-sm"
          onClick={() => setOptModal(null)}
        >
          <div
            className="bg-card border border-border rounded-xl shadow-2xl p-5 w-full max-w-lg mx-4 space-y-3"
            onClick={e => e.stopPropagation()}
          >
            <div className="flex items-center justify-between">
              <span className="text-sm font-semibold text-white">{optModal.ticker} — Options Play</span>
              <button onClick={() => setOptModal(null)} className="text-muted hover:text-white text-lg leading-none">×</button>
            </div>
            {optModal.quoteTs && (
              <p className="text-[11px] text-muted font-mono">
                Quote: {(() => {
                  try {
                    const d = new Date(optModal.quoteTs);
                    return d.toLocaleString("en-US", {
                      timeZone: "America/New_York",
                      month: "short", day: "numeric",
                      hour: "2-digit", minute: "2-digit",
                      hour12: true,
                    }) + " ET";
                  } catch { return optModal.quoteTs; }
                })()}
              </p>
            )}
            <textarea
              readOnly
              autoFocus
              onFocus={e => e.target.select()}
              value={optModal.summary}
              rows={4}
              className="w-full bg-surface border border-border rounded-lg p-3 text-sm font-mono text-white resize-none focus:outline-none focus:border-accent"
            />
            <div className="flex justify-end gap-2">
              <button
                onClick={() => copyText(optModal.summary)}
                className="px-4 py-1.5 text-xs font-semibold rounded-lg bg-accent text-black hover:bg-accent/80 transition-colors"
              >
                {copied ? "✓ Copied" : "Copy"}
              </button>
              <button
                onClick={() => setOptModal(null)}
                className="px-4 py-1.5 text-xs font-semibold rounded-lg border border-border text-muted hover:text-white transition-colors"
              >
                Close
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
