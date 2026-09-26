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

type Filter = "all" | "strength" | "emerging" | "weakness" | "fresh_curl" | "curl_vol" | "rank1" | "exceptional" | "good_rr" | "high_short";

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
  stage2_curl_surge?: boolean;
  is_30w_curl?: boolean;
  is_fresh_stage2?: boolean;
  stage2_status?: string;
  dist_from_sma30?: number | null;
  weeks_curling?: number;
  sma30?: number | null;
  sma30_slope?: number;
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

export interface RankedItem {
  ticker: string;
  sector: string;
  price: number | null;
  verdict: string;
  btd: string;
  btd_zone: string;
  dist_from_high?: number;
  sma30_slope?: number;
  valuation_upside: number;
  swing_entry: number | null;
  swing_stop: number | null;
  swing_t1: number | null;
  swing_reward_pct: number;
  swing_risk_pct?: number;
  swing_rr: number;
  tightness_rating?: string;
  setup_status?: string;
  category?: "STRENGTH" | "EMERGING" | "WEAKNESS";
  category_badge?: string;
  category_title?: string;
  category_tagline?: string;
  score: number;
}

export interface BestPickResponse {
  best_picks?: {
    strength: RankedItem | null;
    emerging: RankedItem | null;
    weakness: RankedItem | null;
  };
  best_pick: RankedItem | null;
  ranked: RankedItem[];
  total_scanned: number;
  strict_passed_count: number;
  is_strict: boolean;
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
  const [sortBy,       setSortBy]       = useState<"score" | "grade" | "rr" | "stage30w">("score");
  const [optModal,     setOptModal]     = useState<{ r: ScanResult } | null>(null);
  const [copied,       setCopied]       = useState(false);
  const [mode,         setMode]         = useState<"live" | "backtest">("live");
  const [backtestDate, setBacktestDate] = useState("");
  const [activeBacktestDate, setActiveBacktestDate] = useState<string | null>(null);
  const [bestPicks,    setBestPicks]    = useState<BestPickResponse | null>(null);
  const [activePickMode, setActivePickMode] = useState<"strength" | "emerging" | "weakness">("emerging");
  const [rankingLoading, setRankingLoading] = useState(false);
  const [showRankedTable, setShowRankedTable] = useState(true);
  const fileInputRef = useRef<HTMLInputElement | null>(null);
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

  async function runRanking(itemsToRank?: ScanResult[]) {
    const list = itemsToRank || results;
    if (!list || list.length === 0) return;
    setRankingLoading(true);
    try {
      const res = await fetch(`${API_BASE}/api/scanner/rank`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ items: list }),
      });
      if (res.ok) {
        const data = await res.json();
        setBestPicks(data);
      }
    } catch (err) {
      console.error("Failed to rank scan results:", err);
    } finally {
      setRankingLoading(false);
    }
  }

  async function handleCsvUpload(e: React.ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0];
    if (!file) return;
    setRankingLoading(true);
    try {
      const formData = new FormData();
      formData.append("file", file);
      const res = await fetch(`${API_BASE}/api/scanner/rank-csv`, {
        method: "POST",
        body: formData,
      });
      if (res.ok) {
        const data = await res.json();
        setBestPicks(data);
      }
    } catch (err) {
      console.error("Failed to rank uploaded CSV:", err);
    } finally {
      setRankingLoading(false);
      if (fileInputRef.current) fileInputRef.current.value = "";
    }
  }

  function startScan() {
    if (esRef.current) esRef.current.close();
    setResults([]);
    setBestPicks(null);

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
      url += `${url.includes("?") ? "&" : "?"}as_of=${backtestDate}`;
      setActiveBacktestDate(backtestDate);
    } else {
      setActiveBacktestDate(null);
    }

    setProgress({ done: 0, total });
    setScanning(true);

    const accumulated: ScanResult[] = [];
    const es = new EventSource(url);
    esRef.current = es;

    es.onmessage = (e) => {
      const data: ScanResult = JSON.parse(e.data);
      if (data.done) {
        setScanning(false);
        es.close();
        if (accumulated.length > 0) {
          runRanking(accumulated);
        }
        return;
      }
      accumulated.push(data);
      setResults(prev => [...prev, data]);
      setProgress(p => ({ ...p, done: p.done + 1 }));
    };

    es.onerror = () => {
      setScanning(false);
      es.close();
      if (accumulated.length > 0) {
        runRanking(accumulated);
      }
    };
  }

  function stopScan() {
    esRef.current?.close();
    setScanning(false);
    if (results.length > 0) {
      runRanking(results);
    }
  }

  const gradeRank: Record<string, number> = { S: 0, A: 1, B: 2, "B-": 3, C: 4, D: 5 };

  const filtered = results
    .filter(r => !r.error && r.verdict)
    .filter(r => {
      if (filter === "strength") {
        const dist = r.dist_from_high ?? 999;
        const slope = r.sma30_slope ?? 0;
        const verdict = (r.verdict ?? "").toUpperCase();
        return dist <= 12.0 && slope > 0 && verdict.includes("BULLISH");
      }
      if (filter === "emerging") {
        const dist = r.dist_from_high ?? 999;
        const slope = r.sma30_slope ?? 0;
        const risk = r.risk_pct ?? 999;
        const verdict = (r.verdict ?? "").toUpperCase();
        return dist <= 15.0 && slope > 0 && risk <= 8.5 && verdict.includes("BULLISH");
      }
      if (filter === "weakness") {
        const dist = r.dist_from_high ?? 0;
        const rr = r.rr_t1 ?? 0;
        return (rr >= 1.8 && dist >= 3.0);
      }
      if (filter === "fresh_curl")  return Boolean(r.is_fresh_stage2 || r.stage2_status === "FRESH");
      if (filter === "curl_vol")    return Boolean(r.is_30w_curl);
      if (filter === "rank1")       return r.mtf_rank === 1;
      if (filter === "good_rr")     return (r.rr_t1 ?? 0) >= 1.5;
      if (filter === "high_short")  return (r.short_pct ?? 0) >= 10;
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
      if (sortBy === "stage30w") {
        const stageRank: Record<string, number> = { FRESH: 1, ADVANCING: 2, EXTENDED: 3, NONE: 4 };
        const rankA = stageRank[a.stage2_status ?? "NONE"] ?? 4;
        const rankB = stageRank[b.stage2_status ?? "NONE"] ?? 4;
        if (rankA !== rankB) return rankA - rankB;
        // within same stage, sort by closest distance to 30W SMA (tightest to pivot first)
        return (Math.abs(a.dist_from_sma30 ?? 999)) - (Math.abs(b.dist_from_sma30 ?? 999));
      }
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

        <div className="flex flex-wrap gap-3 items-center">
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

          {results.length > 0 && !scanning && (
            <button
              onClick={() => runRanking()}
              disabled={rankingLoading}
              className="px-4 py-2 rounded-lg font-semibold text-xs border border-yellow/40 bg-yellow/10 text-yellow hover:bg-yellow/20 transition-colors flex items-center gap-1.5 shadow-sm"
              title="Run Swing Trade ranking algorithm to find best pick of the day"
            >
              <span>🏆</span>
              <span>{rankingLoading ? "Ranking..." : "Rank Best Pick"}</span>
            </button>
          )}

          <label className="cursor-pointer px-3 py-2 rounded-lg text-xs border border-border text-muted hover:text-white hover:border-white/20 transition-colors flex items-center gap-1.5">
            <span>📁</span>
            <span>Upload CSV to Rank</span>
            <input
              ref={fileInputRef}
              type="file"
              accept=".csv"
              onChange={handleCsvUpload}
              className="hidden"
            />
          </label>

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

      {/* ── 🏆 Best Swing Trade Pick of the Day Showcase ── */}
      {(bestPicks?.best_pick || rankingLoading) && (
        <div className="rounded-xl border border-yellow/40 bg-gradient-to-br from-[#1a1607] via-[#0d0f17] to-[#121625] p-5 shadow-2xl relative overflow-hidden">
          {rankingLoading && (
            <div className="absolute inset-0 bg-[#0a0b14]/80 backdrop-blur-sm z-20 flex items-center justify-center gap-3 text-sm text-yellow">
              <div className="w-5 h-5 border-2 border-yellow border-t-transparent rounded-full animate-spin"></div>
              <span>Evaluating Trend Gates, BTD Timing, Risk/Reward & Scoring Best Swing Trade...</span>
            </div>
          )}

          {bestPicks && (() => {
            const currentPick = (bestPicks.best_picks ? bestPicks.best_picks[activePickMode] : null) || bestPicks.best_pick;
            if (!currentPick) return null;
            return (
              <div className="space-y-4">
                {/* Header Bar */}
                <div className="flex flex-wrap items-center justify-between gap-3 border-b border-border/40 pb-3">
                  <div className="flex items-center gap-2.5">
                    <span className="text-2xl">
                      {activePickMode === "strength" ? "⚡" : activePickMode === "emerging" ? "🚀" : "🛡️"}
                    </span>
                    <div>
                      <h2 className="text-base font-bold text-white flex items-center gap-2">
                        <span>{currentPick.category_title || "BEST SWING TRADE OF THE DAY"}</span>
                        <span className="text-[11px] font-mono px-2 py-0.5 rounded-full bg-yellow/20 text-yellow border border-yellow/40 font-bold">
                          ★ TOP {currentPick.category || "PICK"}
                        </span>
                      </h2>
                      <p className="text-xs text-muted">
                        {currentPick.category_tagline || "Highest conviction setup tailored for this strategy"}
                      </p>
                    </div>
                  </div>

                  <div className="flex flex-wrap items-center gap-2">
                    <button
                      onClick={() => setShowRankedTable(!showRankedTable)}
                      className="px-3 py-1 text-xs rounded-lg border border-border text-muted hover:text-white hover:border-white/20 transition-colors"
                    >
                      {showRankedTable ? "Hide Top Candidates Table" : `Show All Ranked (${bestPicks.ranked.length})`}
                    </button>
                    <button
                      onClick={() => downloadCsv(
                        `best_swing_picks_${activeBacktestDate ?? "live"}.csv`,
                        ["Rank","Ticker","Sector","Price","Score","Style","Setup Status","Base Tightness","% Off High","Risk %","30W Slope%","Verdict","BTD","BTD Zone","Valuation Upside%","Swing Entry","Swing Stop","Swing T1","Swing Reward%","Swing R/R"],
                        bestPicks.ranked.map((r, i) => [
                          i + 1, r.ticker, r.sector, r.price, r.score,
                          r.category_badge ?? r.category ?? "",
                          r.setup_status ?? "", r.tightness_rating ?? "",
                          r.dist_from_high !== undefined ? `-${r.dist_from_high}%` : "",
                          r.swing_risk_pct !== undefined ? `${r.swing_risk_pct}%` : "",
                          r.sma30_slope !== undefined ? `+${r.sma30_slope}%` : "",
                          r.verdict, r.btd, r.btd_zone,
                          r.valuation_upside, r.swing_entry, r.swing_stop, r.swing_t1,
                          r.swing_reward_pct, r.swing_rr
                        ])
                      )}
                      className="px-3 py-1 text-xs rounded-lg border border-yellow/40 bg-yellow/10 text-yellow hover:bg-yellow/20 transition-colors font-semibold"
                    >
                      ⬇ Export Best Picks CSV
                    </button>
                  </div>
                </div>

                {/* 3 Strategy Pick Selector Tabs */}
                <div className="grid grid-cols-1 md:grid-cols-3 gap-3">
                  {/* Mode 1: Strength */}
                  <button
                    onClick={() => setActivePickMode("strength")}
                    className={`p-3 rounded-xl border text-left transition-all relative overflow-hidden ${
                      activePickMode === "strength"
                        ? "bg-gradient-to-br from-cyan-950/70 via-[#0d1222] to-[#0a0d16] border-cyan-400 shadow-[0_0_20px_rgba(6,182,212,0.25)] ring-1 ring-cyan-400/50"
                        : "bg-[#0d101a] border-border/60 hover:border-cyan-500/40 text-muted"
                    }`}
                  >
                    <div className="flex items-center justify-between mb-1">
                      <span className="text-[11px] font-mono font-bold px-2 py-0.5 rounded bg-cyan-500/20 text-cyan-300 border border-cyan-500/30">
                        ⚡ BUY THE STRENGTH
                      </span>
                      {bestPicks.best_picks?.strength && (
                        <span className="text-xs font-mono font-bold text-yellow">
                          {bestPicks.best_picks.strength.score} pts
                        </span>
                      )}
                    </div>
                    {bestPicks.best_picks?.strength ? (
                      <div className="flex items-baseline justify-between mt-1.5">
                        <span className="text-xl font-extrabold text-white font-mono">
                          {bestPicks.best_picks.strength.ticker}
                        </span>
                        <span className="text-xs font-mono text-cyan-300">
                          -{bestPicks.best_picks.strength.dist_from_high}% off High
                        </span>
                      </div>
                    ) : (
                      <span className="text-xs text-muted">No candidate</span>
                    )}
                    <div className="text-[11px] text-muted mt-1 truncate">52W High Momentum Leader</div>
                  </button>

                  {/* Mode 2: Emerging */}
                  <button
                    onClick={() => setActivePickMode("emerging")}
                    className={`p-3 rounded-xl border text-left transition-all relative overflow-hidden ${
                      activePickMode === "emerging"
                        ? "bg-gradient-to-br from-emerald-950/70 via-[#0d1814] to-[#0a0d16] border-emerald-400 shadow-[0_0_20px_rgba(16,185,129,0.25)] ring-1 ring-emerald-400/50"
                        : "bg-[#0d101a] border-border/60 hover:border-emerald-500/40 text-muted"
                    }`}
                  >
                    <div className="flex items-center justify-between mb-1">
                      <span className="text-[11px] font-mono font-bold px-2 py-0.5 rounded bg-emerald-500/20 text-emerald-300 border border-emerald-500/30">
                        🚀 BUY THE EMERGING
                      </span>
                      {bestPicks.best_picks?.emerging && (
                        <span className="text-xs font-mono font-bold text-yellow">
                          {bestPicks.best_picks.emerging.score} pts
                        </span>
                      )}
                    </div>
                    {bestPicks.best_picks?.emerging ? (
                      <div className="flex items-baseline justify-between mt-1.5">
                        <span className="text-xl font-extrabold text-white font-mono">
                          {bestPicks.best_picks.emerging.ticker}
                        </span>
                        <span className="text-xs font-mono text-emerald-300">
                          {bestPicks.best_picks.emerging.swing_risk_pct}% Risk Base
                        </span>
                      </div>
                    ) : (
                      <span className="text-xs text-muted">No candidate</span>
                    )}
                    <div className="text-[11px] text-muted mt-1 truncate">Fresh Breakout from Tight Base</div>
                  </button>

                  {/* Mode 3: Weakness */}
                  <button
                    onClick={() => setActivePickMode("weakness")}
                    className={`p-3 rounded-xl border text-left transition-all relative overflow-hidden ${
                      activePickMode === "weakness"
                        ? "bg-gradient-to-br from-amber-950/70 via-[#18140d] to-[#0a0d16] border-amber-400 shadow-[0_0_20px_rgba(245,158,11,0.25)] ring-1 ring-amber-400/50"
                        : "bg-[#0d101a] border-border/60 hover:border-amber-500/40 text-muted"
                    }`}
                  >
                    <div className="flex items-center justify-between mb-1">
                      <span className="text-[11px] font-mono font-bold px-2 py-0.5 rounded bg-amber-500/20 text-amber-300 border border-amber-500/30">
                        🛡️ BUY THE WEAKNESS
                      </span>
                      {bestPicks.best_picks?.weakness && (
                        <span className="text-xs font-mono font-bold text-yellow">
                          {bestPicks.best_picks.weakness.score} pts
                        </span>
                      )}
                    </div>
                    {bestPicks.best_picks?.weakness ? (
                      <div className="flex items-baseline justify-between mt-1.5">
                        <span className="text-xl font-extrabold text-white font-mono">
                          {bestPicks.best_picks.weakness.ticker}
                        </span>
                        <span className="text-xs font-mono text-amber-300">
                          {bestPicks.best_picks.weakness.swing_rr}x R/R Support
                        </span>
                      </div>
                    ) : (
                      <span className="text-xs text-muted">No candidate</span>
                    )}
                    <div className="text-[11px] text-muted mt-1 truncate">High R/R Dip at Key Support</div>
                  </button>
                </div>

                {/* Spotlight Content for currentPick */}
                <div className="grid grid-cols-1 lg:grid-cols-3 gap-4 items-center">
                  {/* Winner Card */}
                  <div className="lg:col-span-1 bg-[#0a0d16] border border-[#232840] rounded-xl p-4 flex flex-col justify-between h-full">
                    <div>
                      <div className="flex items-center justify-between">
                        <span className="text-xs font-semibold text-muted tracking-wider uppercase">
                          {currentPick.sector}
                        </span>
                        <span className="text-xs font-mono font-bold px-2 py-0.5 rounded bg-green/20 text-green border border-green/30">
                          {currentPick.verdict}
                        </span>
                      </div>

                      <div className="flex items-baseline gap-3 my-2">
                        <Link
                          href={`/stock/${currentPick.ticker}`}
                          className="text-4xl font-extrabold text-white hover:text-accent transition-colors font-mono tracking-tight"
                        >
                          {currentPick.ticker}
                        </Link>
                        <span className="text-2xl font-mono text-[#e8ecff] font-bold">
                          ${currentPick.price?.toFixed(2)}
                        </span>
                      </div>

                      <div className="flex flex-wrap gap-1.5 mt-2">
                        {currentPick.category_badge && (
                          <span className={`text-[11px] font-mono font-bold px-2 py-0.5 rounded border ${
                            currentPick.category === "STRENGTH"
                              ? "bg-cyan-500/20 text-cyan-300 border-cyan-500/30"
                              : currentPick.category === "EMERGING"
                              ? "bg-emerald-500/20 text-emerald-300 border-emerald-500/30"
                              : "bg-amber-500/20 text-amber-300 border-amber-500/30"
                          }`}>
                            {currentPick.category_badge}
                          </span>
                        )}
                        {currentPick.setup_status && (
                          <span className="text-[11px] font-mono font-bold px-2 py-0.5 rounded border bg-white/10 text-white/90 border-white/20">
                            {currentPick.setup_status}
                          </span>
                        )}
                        <span className={`text-[11px] font-mono font-bold px-2 py-0.5 rounded border ${
                          currentPick.btd === "TRIGGER"
                            ? "bg-green/20 text-green border-green/30"
                            : "bg-yellow/20 text-yellow border-yellow/30"
                        }`}>
                          BTD: {currentPick.btd}
                        </span>
                        {currentPick.dist_from_high !== undefined && (
                          <span className="text-[11px] font-mono font-bold px-2 py-0.5 rounded border bg-cyan-500/20 text-cyan-300 border-cyan-500/30">
                            ⚡ -{currentPick.dist_from_high.toFixed(1)}% off High
                          </span>
                        )}
                        {currentPick.swing_risk_pct !== undefined && (
                          <span className="text-[11px] font-mono font-semibold px-2 py-0.5 rounded border bg-rose-500/20 text-rose-300 border-rose-500/30">
                            🎯 Risk {currentPick.swing_risk_pct.toFixed(1)}%
                          </span>
                        )}
                        {currentPick.sma30_slope !== undefined && currentPick.sma30_slope > 0 && (
                          <span className="text-[11px] font-mono font-semibold px-2 py-0.5 rounded border border-accent/30 bg-accent/10 text-accent">
                            📈 30W Slope +{currentPick.sma30_slope.toFixed(1)}%
                          </span>
                        )}
                      </div>
                    </div>

                    <div className="mt-4 pt-3 border-t border-border/30 flex items-center justify-between">
                      <div>
                        <div className="text-[11px] text-muted uppercase tracking-wider font-semibold">Algorithm Score</div>
                        <div className="text-3xl font-mono font-extrabold text-yellow">
                          {currentPick.score} <span className="text-xs text-muted font-normal">/ 100</span>
                        </div>
                      </div>
                      <Link
                        href={`/stock/${currentPick.ticker}`}
                        className="px-4 py-2 rounded-lg bg-accent text-black font-bold text-xs hover:bg-accent/80 transition-colors flex items-center gap-1"
                      >
                        <span>📈 Open Live Chart</span>
                      </Link>
                    </div>
                  </div>

                  {/* Trade Levels & Execution Blueprint */}
                  <div className="lg:col-span-2 grid grid-cols-2 sm:grid-cols-3 gap-2.5">
                    <div className="bg-[#0f1d18] border border-[#00e5a0]/30 rounded-xl p-3">
                      <span className="text-[10px] text-muted uppercase font-semibold">🎯 Swing Entry</span>
                      <div className="text-xl font-mono font-extrabold text-[#00e5a0] mt-0.5">
                        ${currentPick.swing_entry?.toFixed(2) ?? "—"}
                      </div>
                      <span className="text-[10px] text-[#00e5a0]/80">Primary buy trigger</span>
                    </div>

                    <div className="bg-[#1e1014] border border-[#ff4d6a]/30 rounded-xl p-3">
                      <span className="text-[10px] text-muted uppercase font-semibold">🛑 Swing Stop</span>
                      <div className="text-xl font-mono font-extrabold text-[#ff4d6a] mt-0.5">
                        ${currentPick.swing_stop?.toFixed(2) ?? "—"}
                      </div>
                      <span className="text-[10px] text-[#ff4d6a]/80">Invalidation point</span>
                    </div>

                    <div className="bg-[#0f1924] border border-[#38bdf8]/30 rounded-xl p-3">
                      <span className="text-[10px] text-muted uppercase font-semibold">🏁 Target 1 (T1)</span>
                      <div className="text-xl font-mono font-extrabold text-[#38bdf8] mt-0.5">
                        ${currentPick.swing_t1?.toFixed(2) ?? "—"}
                      </div>
                      <span className="text-[10px] text-[#38bdf8]/80">First profit target</span>
                    </div>

                    <div className="bg-[#121626] border border-border/60 rounded-xl p-3">
                      <span className="text-[10px] text-muted uppercase font-semibold">📈 Swing Reward</span>
                      <div className="text-xl font-mono font-extrabold text-green mt-0.5">
                        +{currentPick.swing_reward_pct?.toFixed(1)}%
                      </div>
                      <span className="text-[10px] text-muted">Gain potential to T1</span>
                    </div>

                    <div className="bg-[#121626] border border-border/60 rounded-xl p-3">
                      <span className="text-[10px] text-muted uppercase font-semibold">⚖️ Risk / Reward</span>
                      <div className="text-xl font-mono font-extrabold text-accent mt-0.5">
                        {currentPick.swing_rr?.toFixed(2)}x
                      </div>
                      <span className="text-[10px] text-muted">Target R/R ratio</span>
                    </div>

                    <div className="bg-[#121626] border border-purple-500/30 rounded-xl p-3">
                      <span className="text-[10px] text-muted uppercase font-semibold">📐 Strategy Focus</span>
                      <div className="text-xl font-mono font-extrabold text-purple-400 mt-0.5">
                        {activePickMode === "strength"
                          ? `-${currentPick.dist_from_high?.toFixed(1)}% Off High`
                          : activePickMode === "emerging"
                          ? `${currentPick.swing_risk_pct?.toFixed(1)}% Risk Base`
                          : `+${currentPick.valuation_upside?.toFixed(1)}% Upside`}
                      </div>
                      <span className="text-[10px] text-purple-300/80">
                        {activePickMode === "strength"
                          ? "52-Week High Proximity"
                          : activePickMode === "emerging"
                          ? (currentPick.tightness_rating ?? "Compact Stop Base")
                          : "Fair Value Dip Discount"}
                      </span>
                    </div>
                  </div>
                </div>

                {/* Top Ranked Candidates Table (Collapsible) */}
                {showRankedTable && bestPicks.ranked.length > 1 && (
                  <div className="mt-4 pt-3 border-t border-border/40">
                    <div className="text-xs font-semibold text-muted mb-2 flex items-center justify-between">
                      <span>TOP RANKED CANDIDATES ({bestPicks.ranked.length})</span>
                      <span className="text-[11px] text-muted">Sorted by Composite Score (0–100)</span>
                    </div>

                    <div className="overflow-x-auto rounded-lg border border-border/60">
                      <table className="w-full text-xs font-mono" style={{ borderCollapse: "collapse" }}>
                        <thead>
                          <tr className="border-b border-border bg-[#0d0f17] text-muted text-[11px]">
                            <th className="text-center py-2 px-2.5">#</th>
                            <th className="text-left py-2 px-3">Ticker</th>
                            <th className="text-center py-2 px-2">Score</th>
                            <th className="text-left py-2 px-2.5">Style</th>
                            <th className="text-left py-2 px-2.5">Setup</th>
                            <th className="text-left py-2 px-2.5">Base Tightness</th>
                            <th className="text-right py-2 px-3">Price</th>
                            <th className="text-right py-2 px-2.5">Off High</th>
                            <th className="text-right py-2 px-2.5">Risk%</th>
                            <th className="text-right py-2 px-2.5">30W Slope</th>
                            <th className="text-center py-2 px-2.5">Verdict</th>
                            <th className="text-center py-2 px-2.5">BTD</th>
                            <th className="text-left py-2 px-3">BTD Zone</th>
                            <th className="text-right py-2 px-3">Upside%</th>
                            <th className="text-right py-2 px-3">Entry</th>
                            <th className="text-right py-2 px-3">Stop</th>
                            <th className="text-right py-2 px-3">T1</th>
                            <th className="text-right py-2 px-3">Reward%</th>
                            <th className="text-right py-2 px-3">R/R</th>
                            <th className="text-center py-2 px-2.5">Action</th>
                          </tr>
                        </thead>
                        <tbody>
                          {bestPicks.ranked.map((item, idx) => (
                            <tr
                              key={item.ticker}
                              className={`border-b border-border/30 hover:bg-white/5 transition-colors ${
                                idx === 0 ? "bg-yellow/5 font-bold" : ""
                              }`}
                            >
                              <td className="text-center py-2 px-2.5 text-muted">{idx + 1}</td>
                              <td className="text-left py-2 px-3">
                                <Link href={`/stock/${item.ticker}`} className="text-white hover:text-accent font-bold">
                                  {item.ticker}
                                </Link>
                                {idx === 0 && <span className="ml-1 text-yellow">★</span>}
                              </td>
                              <td className="text-center py-2 px-2 font-bold text-yellow">{item.score}</td>
                              <td className="text-left py-2 px-2.5">
                                <span className={`text-[10px] font-mono px-1.5 py-0.5 rounded border whitespace-nowrap ${
                                  item.category === "STRENGTH"
                                    ? "bg-cyan-500/20 text-cyan-300 border-cyan-500/30"
                                    : item.category === "EMERGING"
                                    ? "bg-emerald-500/20 text-emerald-300 border-emerald-500/30"
                                    : "bg-amber-500/20 text-amber-300 border-amber-500/30"
                                }`}>
                                  {item.category_badge ?? item.category ?? "Setup"}
                                </span>
                              </td>
                              <td className="text-left py-2 px-2.5">
                                <span className="text-[10px] font-mono px-1.5 py-0.5 rounded bg-white/10 text-white/90 border border-white/20 whitespace-nowrap">
                                  {item.setup_status ?? "Emerging"}
                                </span>
                              </td>
                              <td className="text-left py-2 px-2.5">
                                <span className="text-[10px] font-mono px-1.5 py-0.5 rounded bg-purple-500/20 text-purple-300 border border-purple-500/30 whitespace-nowrap">
                                  {item.tightness_rating ?? "Constructive"}
                                </span>
                              </td>
                              <td className="text-right py-2 px-3 text-white">${item.price?.toFixed(2)}</td>
                              <td className="text-right py-2 px-2.5 text-cyan-400 font-semibold">
                                {item.dist_from_high !== undefined ? `-${item.dist_from_high.toFixed(1)}%` : "—"}
                              </td>
                              <td className="text-right py-2 px-2.5 text-rose-300 font-semibold">
                                {item.swing_risk_pct !== undefined ? `${item.swing_risk_pct.toFixed(1)}%` : "—"}
                              </td>
                              <td className="text-right py-2 px-2.5 text-accent font-semibold">
                                {item.sma30_slope !== undefined ? `+${item.sma30_slope.toFixed(1)}%` : "—"}
                              </td>
                              <td className="text-center py-2 px-2.5">
                                <span className={item.verdict === "BULLISH" ? "text-green" : "text-green/70"}>
                                  {item.verdict}
                                </span>
                              </td>
                              <td className="text-center py-2 px-2.5">
                                <span className={`px-1.5 py-0.5 rounded text-[10px] ${
                                  item.btd === "TRIGGER" ? "bg-green/20 text-green" : "bg-yellow/20 text-yellow"
                                }`}>
                                  {item.btd}
                                </span>
                              </td>
                              <td className="text-left py-2 px-3 text-muted">{item.btd_zone}</td>
                              <td className="text-right py-2 px-3 text-[#c084fc]">+{item.valuation_upside}%</td>
                              <td className="text-right py-2 px-3 text-green">${item.swing_entry?.toFixed(2)}</td>
                              <td className="text-right py-2 px-3 text-red">${item.swing_stop?.toFixed(2)}</td>
                              <td className="text-right py-2 px-3 text-[#38bdf8]">${item.swing_t1?.toFixed(2)}</td>
                              <td className="text-right py-2 px-3 text-green">+{item.swing_reward_pct}%</td>
                              <td className="text-right py-2 px-3 text-accent">{item.swing_rr}x</td>
                              <td className="text-center py-2 px-2.5">
                                <Link href={`/stock/${item.ticker}`} className="text-accent hover:underline text-[11px]">
                                  Chart →
                                </Link>
                              </td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </div>
                  </div>
                )}
              </div>
            );
          })()}
        </div>
      )}

      {/* ── Filter + Sort ── */}
      {results.length > 0 && (
        <div className="flex flex-wrap gap-4 items-center">
          <div className="flex flex-wrap gap-1 bg-card border border-border rounded-lg p-1">
            {(["all", "strength", "emerging", "weakness", "fresh_curl", "curl_vol", "rank1", "exceptional", "good_rr", "high_short"] as Filter[]).map(f => (
              <button key={f} onClick={() => {
                setFilter(f);
                if (f === "good_rr") setSortBy("rr");
              }}
                className={`px-3 py-1 text-xs rounded-md font-semibold transition-colors ${
                  filter === f ? "bg-accent text-black" : "text-muted hover:text-white"
                }`}>
                {f === "all"        ? `All (${results.filter(r => !r.error).length})`
                : f === "strength"  ? `⚡ Strength (${results.filter(r => (r.dist_from_high ?? 999) <= 12.0 && (r.sma30_slope ?? 0) > 0 && (r.verdict ?? "").toUpperCase().includes("BULLISH")).length})`
                : f === "emerging"  ? `🚀 Emerging (${results.filter(r => (r.dist_from_high ?? 999) <= 15.0 && (r.sma30_slope ?? 0) > 0 && (r.risk_pct ?? 999) <= 8.5 && (r.verdict ?? "").toUpperCase().includes("BULLISH")).length})`
                : f === "weakness"  ? `🛡️ Weakness (${results.filter(r => (r.rr_t1 ?? 0) >= 1.8 && (r.dist_from_high ?? 0) >= 3.0).length})`
                : f === "fresh_curl"? `🎉 Fresh Breakout (${results.filter(r => r.is_fresh_stage2 || r.stage2_status === "FRESH").length})`
                : f === "curl_vol"  ? `⚡ 30W Advancing (${results.filter(r => r.is_30w_curl).length})`
                : f === "rank1"     ? `Rank 1 (${results.filter(r => r.mtf_rank === 1).length})`
                : f === "good_rr"   ? `🎯 Good R/R ≥1.5× (${results.filter(r => (r.rr_t1 ?? 0) >= 1.5).length})`
                : f === "high_short"? `🔥 High Short (${results.filter(r => (r.short_pct ?? 0) >= 10).length})`
                : `Exceptional (${results.filter(r => ["S","A"].includes(r.entry_grade ?? "") && r.mtf_rank === 1 && r.vol_trend === "ACCUMULATING").length})`}
              </button>
            ))}
          </div>

          <div className="flex items-center gap-2 text-xs text-muted">
            <span>Sort:</span>
            {(["score", "grade", "rr", "stage30w"] as const).map(s => (
              <button key={s} onClick={() => setSortBy(s)}
                className={`px-2 py-1 rounded transition-colors ${
                  sortBy === s ? "bg-accent/20 text-accent font-semibold border border-accent/30" : "hover:text-white"
                }`}>
                {s === "score" ? "Score" : s === "grade" ? "Grade" : s === "rr" ? "R/R" : "⚡ 30W Stage"}
              </button>
            ))}
          </div>

          <button
            onClick={() => {
              downloadCsv(
                `scanner_${activeBacktestDate ?? "live"}.csv`,
                [
                  "Ticker", "Sector", "Price", "Verdict", "BTD", "BTD Zone",
                  "30wk MA Slope%", "Valuation Upside%", "Swing Entry", "Swing Stop",
                  "Swing T1", "Swing Reward%", "Swing Risk%", "Swing R/R",
                  "Long Term % From Entry", "Fundamental", "Next Day Summary",
                  "Grade", "Rank", "Short%", "Options Strategy"
                ],
                filtered.map(r => {
                  const btd = (["S", "A"].includes(r.entry_grade ?? "") || r.entry_status === "ENTER")
                    ? "TRIGGER"
                    : (r.is_fresh_stage2 || (r.dist_from_sma30 != null && r.dist_from_sma30 >= -2 && r.dist_from_sma30 <= 8.5))
                    ? "ARMED"
                    : (r.dist_from_sma30 != null && r.dist_from_sma30 < -2)
                    ? "ARMED-DEEP"
                    : "EXTENDED";

                  const btdZone = btd === "TRIGGER"
                    ? "Reclaimed 20 EMA / Breakout"
                    : btd === "ARMED"
                    ? "Dip 20-50 EMA"
                    : btd === "ARMED-DEEP"
                    ? "Deep Dip 50-200 EMA"
                    : "Support Zone";

                  const entry = r.entry ?? r.price ?? 0;
                  const t1 = r.target1 ?? entry;
                  const rewardPct = entry > 0 ? (((t1 - entry) / entry) * 100).toFixed(1) : "0.0";
                  const funda = (r.profit_margin != null && r.profit_margin < 0) || (r.pe_ratio != null && r.pe_ratio < 0)
                    ? "Unprofitable"
                    : (r.earnings_growth != null && r.earnings_growth < 0)
                    ? "Declining"
                    : "Profitable / Sound";

                  const summary = (r.breakout_score ?? 0) >= 7 || r.vol_surge
                    ? "Strong Bullish Close (90%+ Range)"
                    : r.daily_bias === "BULLISH"
                    ? "Bullish"
                    : "Neutral";

                  return [
                    r.ticker, r.sector ?? "N/A", r.price, r.verdict, btd, btdZone,
                    `${r.sma30_slope ?? 0}%`, `${r.target_upside ?? 0}%`,
                    r.entry, r.stop_loss, r.target1, `${rewardPct}%`, `${r.risk_pct ?? 0}%`,
                    r.rr_t1 ?? 1.0, `${r.dist_from_sma30 ?? 0}%`, funda, summary,
                    r.entry_grade, `R${r.mtf_rank}`, r.short_pct, r.opt_strategy
                  ];
                })
              );
            }}
            className="ml-auto px-3 py-1.5 text-xs rounded-lg border border-border text-muted hover:text-white hover:border-white/20 transition-colors flex items-center gap-1.5"
            title="Download full scanner dataset with swing trade ranking fields"
          >
            <span>⬇</span>
            <span>Export Full CSV</span>
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
                  <th
                    onClick={() => setSortBy(sortBy === "stage30w" ? "score" : "stage30w")}
                    className={`text-center px-3 py-3 whitespace-nowrap cursor-pointer transition-colors select-none ${
                      sortBy === "stage30w" ? "text-accent font-bold" : "hover:text-white"
                    }`}
                    title="Click to sort by 30W Stage (Fresh entries first)"
                  >
                    30W Stage {sortBy === "stage30w" ? "▼" : ""}
                  </th>
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
                        {r.stage2_status === "FRESH" ? (
                          <span className="inline-flex items-center gap-1 text-[11px] font-semibold px-2 py-0.5 rounded border bg-green/15 text-green border-green/30" title="Party just started: fresh 30W curl <= 8 weeks and price near 30W SMA">
                            🎉 Fresh {r.dist_from_sma30 != null ? `${r.dist_from_sma30 > 0 ? `+${r.dist_from_sma30}` : r.dist_from_sma30}%` : ""}
                            {r.weeks_curling ? ` (${r.weeks_curling}w)` : ""}
                          </span>
                        ) : r.stage2_status === "EXTENDED" ? (
                          <span className="inline-flex items-center gap-1 text-[11px] font-semibold px-2 py-0.5 rounded border bg-yellow/10 text-yellow border-yellow/20" title="Overextended: late stage entry >15% above 30W SMA or running for months">
                            ⚠️ Ext {r.dist_from_sma30 != null ? `+${r.dist_from_sma30}%` : ""}
                            {r.weeks_curling ? ` (${r.weeks_curling}w)` : ""}
                          </span>
                        ) : r.stage2_status === "ADVANCING" ? (
                          <span className="inline-flex items-center gap-1 text-[11px] font-semibold px-2 py-0.5 rounded border bg-accent/10 text-accent border-accent/20" title="Advancing Stage 2">
                            🚀 Adv {r.dist_from_sma30 != null ? `+${r.dist_from_sma30}%` : ""}
                            {r.weeks_curling ? ` (${r.weeks_curling}w)` : ""}
                          </span>
                        ) : (
                          <span className="text-xs text-muted font-mono">—</span>
                        )}
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
              <span className="text-sm font-semibold text-white">{optModal.r.ticker} — Options Play</span>
              <button onClick={() => setOptModal(null)} className="text-muted hover:text-white text-lg leading-none">×</button>
            </div>
            {optModal.r.opt_quote_ts && (
              <p className="text-[11px] text-muted font-mono">
                Quote: {(() => {
                  try {
                    const d = new Date(optModal.r.opt_quote_ts!);
                    return d.toLocaleString("en-US", {
                      timeZone: "America/New_York",
                      month: "short", day: "numeric",
                      hour: "2-digit", minute: "2-digit",
                      hour12: true,
                    }) + " ET";
                  } catch { return optModal.r.opt_quote_ts; }
                })()}
              </p>
            )}
            <textarea
              readOnly
              autoFocus
              onFocus={e => e.target.select()}
              value={optModal.r.opt_summary ?? ""}
              rows={4}
              className="w-full bg-surface border border-border rounded-lg p-3 text-sm font-mono text-white resize-none focus:outline-none focus:border-accent"
            />
            <div className="flex justify-end gap-2">
              <button
                onClick={() => copyText(optModal.r.opt_summary ?? "")}
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
