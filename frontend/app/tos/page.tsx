"use client";
import React, { useEffect, useState, useMemo } from "react";

const API_BASE = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

interface TosSummary {
  scanned: number;
  filtered: number;
  bullish: number;
  bearish: number;
  mixed: number;
  a_plus: number;
  a: number;
  bb_squeeze: number;
  golden_cross: number;
}

interface TosRow {
  ticker: string;
  price: number;
  combined_bias: string;
  signal_grade: string;
  signal_score: number;
  w_bias: string;
  d_bias: string;
  d_rsi: number;
  d_rsi_label: string;
  w_rsi: number;
  d_macd_label: string;
  d_macd_cross_up: boolean;
  d_macd_cross_down: boolean;
  price_vs_ma20: string;
  price_vs_ma50: string;
  price_vs_ma200: string;
  golden_cross: boolean;
  death_cross: boolean;
  bb_bandwidth: number;
  bb_pct_b: number;
  bb_squeeze: boolean;
  bb_position: string;
  bb_upper?: number;
  bb_mid?: number;
  bb_lower?: number;
  ma20?: number;
  ma50?: number;
  ma200?: number;
  w_ema_trend: string;
  d_ema_trend: string;
  w_fib_zone: string;
  d_fib_zone: string;
  d_fib_position: number;
  w_conviction: string;
  d_conviction: string;
  d_vol_trend: string;
  d_vol_ratio: number;
  w_weinstein_score: number;
  d_weinstein_score: number;
  w_weinstein_phase: string;
  d_weinstein_phase: string;
  d_momentum: string;
  d_roc: number;
  d_volatility_label: string;
  d_volatility?: number;
  d_bull_div: boolean;
  d_bear_div: boolean;
  d_bull_fvg_count?: number;
  d_bear_fvg_count?: number;
  w_details?: Record<string, any>;
  d_details?: Record<string, any>;
}

export default function TosScannerPage() {
  const [watchlists, setWatchlists] = useState<Record<string, string[]>>({});
  const [selectedPreset, setSelectedPreset] = useState<string>("Mag 7");
  const [tickersInput, setTickersInput] = useState<string>("NVDA, TSLA, AAPL, MSFT, AMZN, GOOGL, META");
  const [loading, setLoading] = useState(false);
  const [showFilters, setShowFilters] = useState(false);

  // Filter state
  const [fBias, setFBias] = useState("ANY");
  const [fAligned, setFAligned] = useState(false);
  const [fGrade, setFGrade] = useState("Any");
  const [fMinScore, setFMinScore] = useState(0);
  const [fRsiMin, setFRsiMin] = useState(0);
  const [fRsiMax, setFRsiMax] = useState(100);
  const [fRsiOs, setFRsiOs] = useState(false);
  const [fRsiOb, setFRsiOb] = useState(false);
  const [fMacdCross, setFMacdCross] = useState("ANY");
  const [fMacdDir, setFMacdDir] = useState("ANY");
  const [fMa20, setFMa20] = useState("ANY");
  const [fMa50, setFMa50] = useState("ANY");
  const [fMa200, setFMa200] = useState("ANY");
  const [fGolden, setFGolden] = useState(false);
  const [fDeath, setFDeath] = useState(false);
  const [fBbPos, setFBbPos] = useState("ANY");
  const [fBbSq, setFBbSq] = useState(false);
  const [fWeinPhase, setFWeinPhase] = useState("Any");
  const [fWeinMin, setFWeinMin] = useState(0);
  const [fVolTrend, setFVolTrend] = useState("ANY");
  const [fFibZone, setFFibZone] = useState("ANY");
  const [fBullDiv, setFBullDiv] = useState(false);
  const [fBearDiv, setFBearDiv] = useState(false);

  // Scan outputs
  const [summary, setSummary] = useState<TosSummary | null>(null);
  const [items, setItems] = useState<TosRow[]>([]);
  const [activeTab, setActiveTab] = useState<"aligned" | "bull" | "bear" | "grade" | "squeeze" | "all">("all");
  const [selectedTickerDetail, setSelectedTickerDetail] = useState<string>("");

  useEffect(() => {
    fetch(`${API_BASE}/api/tos/watchlists`)
      .then((res) => res.json())
      .then((data) => {
        if (data.watchlists) {
          setWatchlists(data.watchlists);
        }
      })
      .catch(() => {});
  }, []);

  const handlePresetChange = (preset: string) => {
    setSelectedPreset(preset);
    if (preset !== "Custom" && watchlists[preset]) {
      setTickersInput(watchlists[preset].join(", "));
    }
  };

  const buildFiltersPayload = () => {
    return {
      bias_direction: fBias !== "ANY" ? fBias : undefined,
      require_aligned: fAligned || undefined,
      min_grade: fGrade !== "Any" ? fGrade : undefined,
      min_score: fMinScore > 0 ? fMinScore : undefined,
      rsi_min: fRsiMin > 0 ? fRsiMin : undefined,
      rsi_max: fRsiMax < 100 ? fRsiMax : undefined,
      require_rsi_oversold: fRsiOs || undefined,
      require_rsi_overbought: fRsiOb || undefined,
      macd_cross: fMacdCross !== "ANY" ? fMacdCross : undefined,
      macd_direction: fMacdDir !== "ANY" ? fMacdDir : undefined,
      price_vs_ma20: fMa20 !== "ANY" ? fMa20 : undefined,
      price_vs_ma50: fMa50 !== "ANY" ? fMa50 : undefined,
      price_vs_ma200: fMa200 !== "ANY" ? fMa200 : undefined,
      require_golden_cross: fGolden || undefined,
      require_death_cross: fDeath || undefined,
      bb_position: fBbPos !== "ANY" ? fBbPos : undefined,
      require_bb_squeeze: fBbSq || undefined,
      weinstein_phase: fWeinPhase !== "Any" ? fWeinPhase : undefined,
      min_weinstein: fWeinMin > 0 ? fWeinMin : undefined,
      vol_trend: fVolTrend !== "ANY" ? fVolTrend : undefined,
      fib_zone: fFibZone !== "ANY" ? fFibZone : undefined,
      require_bull_div: fBullDiv || undefined,
      require_bear_div: fBearDiv || undefined,
    };
  };

  const handleRunScan = async () => {
    setLoading(true);
    const t_list = tickersInput.split(",").map((t) => t.trim().toUpperCase()).filter(Boolean);
    try {
      const res = await fetch(`${API_BASE}/api/tos/scan`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          tickers: t_list,
          filters: buildFiltersPayload(),
        }),
      });
      if (res.ok) {
        const data = await res.json();
        setSummary(data.summary);
        setItems(data.items || []);
        if (data.items && data.items.length > 0) {
          setSelectedTickerDetail(data.items[0].ticker);
        }
      } else {
        alert("TOS scan failed. Check server logs.");
      }
    } catch (e: any) {
      alert(`Network error: ${e.message}`);
    } finally {
      setLoading(false);
    }
  };

  const filteredItems = useMemo(() => {
    if (activeTab === "aligned") {
      return items.filter((r) => r.combined_bias?.includes("ALIGNED"));
    }
    if (activeTab === "bull") {
      return items.filter((r) => r.combined_bias === "BULLISH ALIGNED");
    }
    if (activeTab === "bear") {
      return items.filter((r) => r.combined_bias === "BEARISH ALIGNED");
    }
    if (activeTab === "grade") {
      return items.filter((r) => r.signal_grade === "A+" || r.signal_grade === "A");
    }
    if (activeTab === "squeeze") {
      return items.filter((r) => r.bb_squeeze);
    }
    return items;
  }, [items, activeTab]);

  const activeDetailRow = useMemo(() => {
    return items.find((r) => r.ticker === selectedTickerDetail) || items[0] || null;
  }, [items, selectedTickerDetail]);

  const handleDownloadCSV = () => {
    if (items.length === 0) return;
    const headers = [
      "Ticker", "Price", "Bias (W+D)", "Grade", "Score",
      "W Bias", "D Bias", "D RSI", "MACD", "vs MA20", "vs MA50", "vs MA200",
      "Golden Cross", "BB Squeeze", "Vol Trend", "D Weinstein"
    ];
    const rows = items.map((r) => [
      r.ticker, r.price, r.combined_bias, r.signal_grade, r.signal_score,
      r.w_bias, r.d_bias, r.d_rsi, r.d_macd_label, r.price_vs_ma20, r.price_vs_ma50, r.price_vs_ma200,
      r.golden_cross, r.bb_squeeze, r.d_vol_trend, r.d_weinstein_score
    ]);
    const csvContent = [headers.join(","), ...rows.map((e) => e.join(","))].join("\n");
    const blob = new Blob([csvContent], { type: "text/csv;charset=utf-8;" });
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.setAttribute("href", url);
    link.setAttribute("download", `tos_scan_${new Date().toISOString().split("T")[0]}.csv`);
    document.body.appendChild(link);
    link.click();
    document.body.removeChild(link);
  };

  return (
    <div className="max-w-screen-2xl mx-auto px-4 py-8 space-y-6">
      {/* ── HEADER BANNER (matching stock_pulse.py lines 18426-18434) ── */}
      <div className="bg-gradient-to-r from-card to-surface border border-border rounded-xl p-5 shadow-sm">
        <h1 className="text-2xl font-bold text-white tracking-tight flex items-center gap-2">
          📡 TOS Scanner
        </h1>
        <p className="text-muted text-xs mt-1">
          Multi-condition scanner — Fibonacci · AGIG ZigZag · Weinstein · RSI · MACD · Bollinger · MA · Volume · FVG
        </p>
      </div>

      {/* ── INPUT PANEL (lines 18449-18467) ─────────────────────────── */}
      <div className="bg-card border border-border rounded-xl p-4 space-y-3">
        <div className="grid grid-cols-1 sm:grid-cols-2 md:grid-cols-5 gap-3 items-end">
          <div className="md:col-span-1">
            <label className="text-xs text-muted block mb-1">Watchlist Preset</label>
            <select
              value={selectedPreset}
              onChange={(e) => handlePresetChange(e.target.value)}
              className="w-full bg-surface border border-border rounded-lg px-3 py-1.5 text-xs text-white font-mono focus:outline-none focus:border-accent"
            >
              <option value="Custom">Custom</option>
              {Object.keys(watchlists).map((w) => (
                <option key={w} value={w}>
                  {w} ({watchlists[w].length})
                </option>
              ))}
            </select>
          </div>

          <div className="md:col-span-3">
            <label className="text-xs text-muted block mb-1">
              Ticker(s) — comma-separated (or edit after selecting preset)
            </label>
            <input
              type="text"
              value={tickersInput}
              onChange={(e) => setTickersInput(e.target.value)}
              placeholder="AAPL, NVDA, TSLA, MSFT..."
              className="w-full bg-surface border border-border rounded-lg px-3 py-1.5 text-xs text-white font-mono focus:outline-none focus:border-accent uppercase"
            />
          </div>

          <div className="md:col-span-1 flex gap-2">
            <button
              onClick={handleRunScan}
              disabled={loading}
              className="flex-1 bg-accent hover:bg-accent/90 text-white text-xs font-bold px-4 py-2 rounded-lg shadow transition-all flex items-center justify-center gap-1.5 disabled:opacity-50"
            >
              {loading ? (
                <>
                  <span className="animate-spin">🔄</span> Scanning...
                </>
              ) : (
                <>🔍 Run Scan</>
              )}
            </button>
            <button
              onClick={() => setShowFilters(!showFilters)}
              className="bg-surface hover:bg-surface/80 border border-border text-white text-xs px-2.5 py-2 rounded-lg"
              title="Toggle Filter Conditions"
            >
              🎛️
            </button>
          </div>
        </div>

        {/* ── EXPANDABLE SCAN CONDITIONS (lines 18471-18510) ────────── */}
        {showFilters && (
          <div className="bg-surface/40 border border-border rounded-lg p-4 mt-3 space-y-4">
            <div className="flex items-center justify-between border-b border-border pb-2">
              <span className="text-xs font-bold text-white uppercase tracking-wider">
                🎛️ Scan Conditions (Filter Results)
              </span>
              <button
                onClick={() => {
                  setFBias("ANY");
                  setFAligned(false);
                  setFGrade("Any");
                  setFMinScore(0);
                  setFRsiMin(0);
                  setFRsiMax(100);
                  setFRsiOs(false);
                  setFRsiOb(false);
                  setFMacdCross("ANY");
                  setFMacdDir("ANY");
                  setFMa20("ANY");
                  setFMa50("ANY");
                  setFMa200("ANY");
                  setFGolden(false);
                  setFDeath(false);
                  setFBbPos("ANY");
                  setFBbSq(false);
                  setFWeinPhase("Any");
                  setFWeinMin(0);
                  setFVolTrend("ANY");
                  setFFibZone("ANY");
                  setFBullDiv(false);
                  setFBearDiv(false);
                }}
                className="text-[11px] text-accent hover:underline"
              >
                Reset Filters
              </button>
            </div>

            <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4 text-xs">
              {/* Col 1: Bias & Grade */}
              <div className="space-y-2">
                <div className="font-bold text-white">Bias &amp; Grade</div>
                <div>
                  <label className="text-[11px] text-muted block">Direction</label>
                  <select
                    value={fBias}
                    onChange={(e) => setFBias(e.target.value)}
                    className="w-full bg-card border border-border rounded px-2 py-1 text-white font-mono text-xs"
                  >
                    <option value="ANY">ANY</option>
                    <option value="BULLISH">BULLISH</option>
                    <option value="BEARISH">BEARISH</option>
                  </select>
                </div>
                <label className="flex items-center gap-1.5 cursor-pointer text-muted pt-1">
                  <input
                    type="checkbox"
                    checked={fAligned}
                    onChange={(e) => setFAligned(e.target.checked)}
                    className="rounded border-border"
                  />
                  Require W+D Aligned
                </label>
                <div>
                  <label className="text-[11px] text-muted block">Min Grade</label>
                  <select
                    value={fGrade}
                    onChange={(e) => setFGrade(e.target.value)}
                    className="w-full bg-card border border-border rounded px-2 py-1 text-white font-mono text-xs"
                  >
                    <option value="Any">Any</option>
                    <option value="A+">A+</option>
                    <option value="A">A</option>
                    <option value="B">B</option>
                    <option value="C">C</option>
                  </select>
                </div>
                <div>
                  <label className="text-[11px] text-muted block">Min Signal Score: {fMinScore}</label>
                  <input
                    type="range"
                    min={0}
                    max={12}
                    value={fMinScore}
                    onChange={(e) => setFMinScore(parseInt(e.target.value))}
                    className="w-full accent-accent"
                  />
                </div>
              </div>

              {/* Col 2: RSI & MACD */}
              <div className="space-y-2">
                <div className="font-bold text-white">RSI &amp; MACD</div>
                <div className="grid grid-cols-2 gap-2">
                  <div>
                    <label className="text-[10px] text-muted block">RSI Min</label>
                    <input
                      type="number"
                      value={fRsiMin}
                      onChange={(e) => setFRsiMin(parseInt(e.target.value) || 0)}
                      className="w-full bg-card border border-border rounded px-2 py-1 text-white font-mono text-xs"
                    />
                  </div>
                  <div>
                    <label className="text-[10px] text-muted block">RSI Max</label>
                    <input
                      type="number"
                      value={fRsiMax}
                      onChange={(e) => setFRsiMax(parseInt(e.target.value) || 100)}
                      className="w-full bg-card border border-border rounded px-2 py-1 text-white font-mono text-xs"
                    />
                  </div>
                </div>
                <div className="flex gap-3 text-[11px] text-muted">
                  <label className="flex items-center gap-1 cursor-pointer">
                    <input
                      type="checkbox"
                      checked={fRsiOs}
                      onChange={(e) => setFRsiOs(e.target.checked)}
                    />
                    Oversold (&lt;30)
                  </label>
                  <label className="flex items-center gap-1 cursor-pointer">
                    <input
                      type="checkbox"
                      checked={fRsiOb}
                      onChange={(e) => setFRsiOb(e.target.checked)}
                    />
                    Overbought (&gt;70)
                  </label>
                </div>
                <div>
                  <label className="text-[11px] text-muted block">MACD Cross</label>
                  <select
                    value={fMacdCross}
                    onChange={(e) => setFMacdCross(e.target.value)}
                    className="w-full bg-card border border-border rounded px-2 py-1 text-white font-mono text-xs"
                  >
                    <option value="ANY">ANY</option>
                    <option value="UP">UP</option>
                    <option value="DOWN">DOWN</option>
                  </select>
                </div>
                <div>
                  <label className="text-[11px] text-muted block">MACD Direction</label>
                  <select
                    value={fMacdDir}
                    onChange={(e) => setFMacdDir(e.target.value)}
                    className="w-full bg-card border border-border rounded px-2 py-1 text-white font-mono text-xs"
                  >
                    <option value="ANY">ANY</option>
                    <option value="BULLISH">BULLISH</option>
                    <option value="BEARISH">BEARISH</option>
                  </select>
                </div>
              </div>

              {/* Col 3: Moving Averages & BB */}
              <div className="space-y-2">
                <div className="font-bold text-white">Moving Averages &amp; BB</div>
                <div className="grid grid-cols-3 gap-1">
                  <div>
                    <label className="text-[10px] text-muted block">vs MA20</label>
                    <select
                      value={fMa20}
                      onChange={(e) => setFMa20(e.target.value)}
                      className="w-full bg-card border border-border rounded px-1 py-1 text-white font-mono text-[10px]"
                    >
                      <option value="ANY">ANY</option>
                      <option value="ABOVE">ABOVE</option>
                      <option value="BELOW">BELOW</option>
                    </select>
                  </div>
                  <div>
                    <label className="text-[10px] text-muted block">vs MA50</label>
                    <select
                      value={fMa50}
                      onChange={(e) => setFMa50(e.target.value)}
                      className="w-full bg-card border border-border rounded px-1 py-1 text-white font-mono text-[10px]"
                    >
                      <option value="ANY">ANY</option>
                      <option value="ABOVE">ABOVE</option>
                      <option value="BELOW">BELOW</option>
                    </select>
                  </div>
                  <div>
                    <label className="text-[10px] text-muted block">vs MA200</label>
                    <select
                      value={fMa200}
                      onChange={(e) => setFMa200(e.target.value)}
                      className="w-full bg-card border border-border rounded px-1 py-1 text-white font-mono text-[10px]"
                    >
                      <option value="ANY">ANY</option>
                      <option value="ABOVE">ABOVE</option>
                      <option value="BELOW">BELOW</option>
                    </select>
                  </div>
                </div>
                <div className="flex gap-3 text-[11px] text-muted">
                  <label className="flex items-center gap-1 cursor-pointer">
                    <input
                      type="checkbox"
                      checked={fGolden}
                      onChange={(e) => setFGolden(e.target.checked)}
                    />
                    Golden Cross
                  </label>
                  <label className="flex items-center gap-1 cursor-pointer">
                    <input
                      type="checkbox"
                      checked={fDeath}
                      onChange={(e) => setFDeath(e.target.checked)}
                    />
                    Death Cross
                  </label>
                </div>
                <div className="grid grid-cols-2 gap-2 pt-1">
                  <div>
                    <label className="text-[10px] text-muted block">BB Position</label>
                    <select
                      value={fBbPos}
                      onChange={(e) => setFBbPos(e.target.value)}
                      className="w-full bg-card border border-border rounded px-1 py-1 text-white font-mono text-[10px]"
                    >
                      <option value="ANY">ANY</option>
                      <option value="ABOVE_UPPER">ABOVE_UPPER</option>
                      <option value="BELOW_LOWER">BELOW_LOWER</option>
                      <option value="INSIDE">INSIDE</option>
                    </select>
                  </div>
                  <label className="flex items-center gap-1 cursor-pointer text-[11px] text-muted mt-3">
                    <input
                      type="checkbox"
                      checked={fBbSq}
                      onChange={(e) => setFBbSq(e.target.checked)}
                    />
                    BB Squeeze
                  </label>
                </div>
              </div>

              {/* Col 4: Weinstein & Patterns */}
              <div className="space-y-2">
                <div className="font-bold text-white">Weinstein &amp; Patterns</div>
                <div>
                  <label className="text-[11px] text-muted block">Weinstein Phase</label>
                  <select
                    value={fWeinPhase}
                    onChange={(e) => setFWeinPhase(e.target.value)}
                    className="w-full bg-card border border-border rounded px-2 py-1 text-white font-mono text-xs"
                  >
                    <option value="Any">Any</option>
                    <option value="STAGE 2">STAGE 2 (Bullish)</option>
                    <option value="STAGE 4">STAGE 4 (Bearish)</option>
                    <option value="STAGE 1">STAGE 1 (Base)</option>
                    <option value="STAGE 3">STAGE 3 (Topping)</option>
                  </select>
                </div>
                <div>
                  <label className="text-[11px] text-muted block">Volume Trend</label>
                  <select
                    value={fVolTrend}
                    onChange={(e) => setFVolTrend(e.target.value)}
                    className="w-full bg-card border border-border rounded px-2 py-1 text-white font-mono text-xs"
                  >
                    <option value="ANY">ANY</option>
                    <option value="ACCUMULATING">ACCUMULATING</option>
                    <option value="DISTRIBUTING">DISTRIBUTING</option>
                    <option value="FLAT">FLAT</option>
                  </select>
                </div>
                <div>
                  <label className="text-[11px] text-muted block">Fib Zone</label>
                  <select
                    value={fFibZone}
                    onChange={(e) => setFFibZone(e.target.value)}
                    className="w-full bg-card border border-border rounded px-2 py-1 text-white font-mono text-xs"
                  >
                    <option value="ANY">ANY</option>
                    <option value="BUY ZONE">BUY ZONE</option>
                    <option value="SELL ZONE">SELL ZONE</option>
                    <option value="DEEP DISCOUNT">DEEP DISCOUNT</option>
                    <option value="EXTENDED">EXTENDED</option>
                    <option value="NEUTRAL">NEUTRAL</option>
                  </select>
                </div>
                <div className="flex gap-3 text-[11px] text-muted pt-1">
                  <label className="flex items-center gap-1 cursor-pointer">
                    <input
                      type="checkbox"
                      checked={fBullDiv}
                      onChange={(e) => setFBullDiv(e.target.checked)}
                    />
                    Bull Div
                  </label>
                  <label className="flex items-center gap-1 cursor-pointer">
                    <input
                      type="checkbox"
                      checked={fBearDiv}
                      onChange={(e) => setFBearDiv(e.target.checked)}
                    />
                    Bear Div
                  </label>
                </div>
              </div>
            </div>
          </div>
        )}
      </div>

      {/* ── SUMMARY STATS BANNER (lines 18584-18610) ────────────────── */}
      {summary && (
        <div className="bg-card border border-border rounded-xl p-4 flex flex-wrap gap-4 text-xs items-center shadow-sm">
          <span className="text-muted">
            Scanned <b className="text-white">{summary.scanned}</b> · Showing{" "}
            <b className="text-white">{summary.filtered}</b> after filters
          </span>
          <span className="text-bull font-bold">🟢 Bullish: {summary.bullish}</span>
          <span className="text-bear font-bold">🔴 Bearish: {summary.bearish}</span>
          <span className="text-amber-400 font-semibold">Mixed: {summary.mixed}</span>
          <span className="text-bull font-bold">A+: {summary.a_plus}</span>
          <span className="text-accent font-bold">A: {summary.a}</span>
          <span className="text-amber-300 font-semibold">⚡ BB Squeeze: {summary.bb_squeeze}</span>
          <span className="text-accent font-semibold">Golden Cross: {summary.golden_cross}</span>
        </div>
      )}

      {/* ── FILTERED SUB-TABS (lines 18738-18760) ───────────────────── */}
      {items.length > 0 && (
        <div className="space-y-4">
          <div className="flex items-center justify-between border-b border-border overflow-x-auto pb-px">
            <div className="flex items-center gap-2">
              {[
                { id: "aligned", label: "🎯 Aligned", count: items.filter((r) => r.combined_bias?.includes("ALIGNED")).length },
                { id: "bull", label: "🟢 Bull", count: summary?.bullish || 0 },
                { id: "bear", label: "🔴 Bear", count: summary?.bearish || 0 },
                { id: "grade", label: "⭐ A / A+", count: (summary?.a_plus || 0) + (summary?.a || 0) },
                { id: "squeeze", label: "⚡ BB Squeeze", count: summary?.bb_squeeze || 0 },
                { id: "all", label: "📊 All", count: items.length },
              ].map((tab) => (
                <button
                  key={tab.id}
                  onClick={() => setActiveTab(tab.id as any)}
                  className={`px-4 py-2 text-xs font-semibold rounded-t-lg transition-colors border-b-2 flex items-center gap-1.5 whitespace-nowrap ${
                    activeTab === tab.id
                      ? "border-accent text-white bg-card"
                      : "border-transparent text-muted hover:text-white"
                  }`}
                >
                  <span>{tab.label}</span>
                  <span className="bg-surface px-1.5 py-0.2 rounded-full text-[10px] text-muted">
                    {tab.count}
                  </span>
                </button>
              ))}
            </div>

            <button
              onClick={handleDownloadCSV}
              className="text-xs font-semibold text-accent hover:underline px-3 py-1 flex items-center gap-1"
            >
              📥 Download CSV
            </button>
          </div>

          {/* ── RESULTS TABLE ── */}
          <div className="bg-card border border-border rounded-xl overflow-hidden shadow-sm">
            <div className="overflow-x-auto">
              <table className="w-full text-left text-xs whitespace-nowrap">
                <thead className="bg-surface text-muted text-[10px] uppercase border-b border-border">
                  <tr>
                    <th className="px-3 py-2.5">Ticker</th>
                    <th className="px-3 py-2.5">Price</th>
                    <th className="px-3 py-2.5">Bias (W+D)</th>
                    <th className="px-3 py-2.5">Grade</th>
                    <th className="px-3 py-2.5">Score</th>
                    <th className="px-3 py-2.5">W Bias</th>
                    <th className="px-3 py-2.5">D Bias</th>
                    <th className="px-3 py-2.5">D RSI</th>
                    <th className="px-3 py-2.5">MACD</th>
                    <th className="px-3 py-2.5">vs MA20</th>
                    <th className="px-3 py-2.5">vs MA50</th>
                    <th className="px-3 py-2.5">vs MA200</th>
                    <th className="px-3 py-2.5">Golden✓</th>
                    <th className="px-3 py-2.5">BB Sq</th>
                    <th className="px-3 py-2.5">Vol Trend</th>
                    <th className="px-3 py-2.5">D Wein</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-border">
                  {filteredItems.length === 0 ? (
                    <tr>
                      <td colSpan={16} className="text-center py-12 text-muted">
                        No setups match the current sub-tab criteria.
                      </td>
                    </tr>
                  ) : (
                    filteredItems.map((r) => {
                      const isBull = r.combined_bias?.includes("BULLISH");
                      const isBear = r.combined_bias?.includes("BEARISH");

                      return (
                        <tr
                          key={r.ticker}
                          onClick={() => setSelectedTickerDetail(r.ticker)}
                          className={`cursor-pointer transition-colors ${
                            selectedTickerDetail === r.ticker ? "bg-accent/10 border-l-2 border-accent" : "hover:bg-surface/50"
                          }`}
                        >
                          <td className="px-3 py-2.5 font-mono font-bold text-accent">{r.ticker}</td>
                          <td className="px-3 py-2.5 font-mono text-white">${r.price?.toFixed(2)}</td>
                          <td className="px-3 py-2.5">
                            <span className={`px-2 py-0.5 rounded text-[10px] font-bold ${
                              isBull
                                ? "bg-bull/20 text-bull border border-bull/30"
                                : isBear
                                ? "bg-bear/20 text-bear border border-bear/30"
                                : "bg-amber-500/20 text-amber-300"
                            }`}>
                              {r.combined_bias}
                            </span>
                          </td>
                          <td className="px-3 py-2.5">
                            <span className={`px-1.5 py-0.2 rounded font-bold font-mono text-[10px] ${
                              r.signal_grade === "A+" || r.signal_grade === "A" ? "text-bull bg-bull/10" : "text-muted bg-surface"
                            }`}>
                              {r.signal_grade}
                            </span>
                          </td>
                          <td className="px-3 py-2.5 font-mono font-bold text-white">{r.signal_score}</td>
                          <td className="px-3 py-2.5 font-mono text-[11px] text-muted">{r.w_bias}</td>
                          <td className="px-3 py-2.5 font-mono text-[11px] text-muted">{r.d_bias}</td>
                          <td className="px-3 py-2.5 font-mono font-semibold">
                            <span className={r.d_rsi <= 30 ? "text-bull" : r.d_rsi >= 70 ? "text-bear" : "text-white"}>
                              {r.d_rsi?.toFixed(1)}
                            </span>
                          </td>
                          <td className="px-3 py-2.5 text-muted">{r.d_macd_label}</td>
                          <td className="px-3 py-2.5 font-mono text-[10px] text-muted">{r.price_vs_ma20}</td>
                          <td className="px-3 py-2.5 font-mono text-[10px] text-muted">{r.price_vs_ma50}</td>
                          <td className="px-3 py-2.5 font-mono text-[10px] text-muted">{r.price_vs_ma200}</td>
                          <td className="px-3 py-2.5 font-mono">{r.golden_cross ? "✅" : "—"}</td>
                          <td className="px-3 py-2.5 font-mono">{r.bb_squeeze ? "⚡" : "—"}</td>
                          <td className="px-3 py-2.5 text-muted">{r.d_vol_trend}</td>
                          <td className="px-3 py-2.5 font-mono font-bold text-white">{r.d_weinstein_score}/6</td>
                        </tr>
                      );
                    })
                  )}
                </tbody>
              </table>
            </div>
          </div>

          {/* ── DETAILED TICKER VIEW EXPANDER (lines 18767-18850) ────── */}
          {activeDetailRow && (
            <div className="bg-card border border-border rounded-xl p-5 space-y-4 shadow-sm">
              <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-3 border-b border-border pb-3">
                <div className="flex items-center gap-3">
                  <span className="text-xl font-bold font-mono text-white">{activeDetailRow.ticker}</span>
                  <span className="text-sm font-mono text-muted">${activeDetailRow.price?.toFixed(2)}</span>
                  <span className="px-2.5 py-0.5 rounded text-xs font-bold bg-bull/20 text-bull border border-bull/30">
                    Grade {activeDetailRow.signal_grade} · Score {activeDetailRow.signal_score}
                  </span>
                  <span className="text-xs font-semibold text-accent">{activeDetailRow.combined_bias}</span>
                </div>

                <div className="flex items-center gap-2">
                  <label className="text-xs text-muted">Select Ticker:</label>
                  <select
                    value={selectedTickerDetail}
                    onChange={(e) => setSelectedTickerDetail(e.target.value)}
                    className="bg-surface border border-border rounded-lg px-3 py-1 text-xs text-white font-mono focus:outline-none focus:border-accent"
                  >
                    {items.map((r) => (
                      <option key={r.ticker} value={r.ticker}>
                        {r.ticker} ({r.signal_grade})
                      </option>
                    ))}
                  </select>
                </div>
              </div>

              {/* 3 Columns breakdown */}
              <div className="grid grid-cols-1 md:grid-cols-3 gap-6 text-xs">
                {/* Col 1: Weekly */}
                <div className="space-y-2 bg-surface/30 p-4 rounded-lg border border-border/60">
                  <h3 className="font-bold text-white flex items-center gap-1.5 pb-1 border-b border-border">
                    📅 Weekly Timeframe
                  </h3>
                  <div className="text-muted">Bias: <b className="text-white">{activeDetailRow.w_bias}</b></div>
                  <div className="text-muted">RSI: <b className="text-white">{activeDetailRow.w_rsi?.toFixed(1)}</b></div>
                  <div className="text-muted">EMA Trend: <b className="text-white">{activeDetailRow.w_ema_trend}</b></div>
                  <div className="text-muted">Fib Zone: <b className="text-white">{activeDetailRow.w_fib_zone}</b></div>
                  <div className="text-muted">Conviction: <b className="text-white">{activeDetailRow.w_conviction}</b></div>
                  <div className="text-muted">
                    Weinstein: <b className="text-white">{activeDetailRow.w_weinstein_score}/6</b> ({activeDetailRow.w_weinstein_phase})
                  </div>
                </div>

                {/* Col 2: Daily */}
                <div className="space-y-2 bg-surface/30 p-4 rounded-lg border border-border/60">
                  <h3 className="font-bold text-white flex items-center gap-1.5 pb-1 border-b border-border">
                    📈 Daily Timeframe
                  </h3>
                  <div className="text-muted">Bias: <b className="text-white">{activeDetailRow.d_bias}</b></div>
                  <div className="text-muted">RSI: <b className="text-white">{activeDetailRow.d_rsi?.toFixed(1)}</b> ({activeDetailRow.d_rsi_label})</div>
                  <div className="text-muted">MACD: <b className="text-white">{activeDetailRow.d_macd_label}</b></div>
                  <div className="text-muted">EMA Trend: <b className="text-white">{activeDetailRow.d_ema_trend}</b></div>
                  <div className="text-muted">Fib Zone: <b className="text-white">{activeDetailRow.d_fib_zone} ({activeDetailRow.d_fib_position}%)</b></div>
                  <div className="text-muted">Volume: <b className="text-white">{activeDetailRow.d_vol_trend} ({activeDetailRow.d_vol_ratio}x)</b></div>
                  <div className="text-muted">
                    Weinstein: <b className="text-white">{activeDetailRow.d_weinstein_score}/6</b> ({activeDetailRow.d_weinstein_phase})
                  </div>
                  {(activeDetailRow.d_bull_div || activeDetailRow.d_bear_div) && (
                    <div className="text-[11px] pt-1">
                      {activeDetailRow.d_bull_div && <span className="text-bull mr-2">🟢 Bull Divergence</span>}
                      {activeDetailRow.d_bear_div && <span className="text-bear">🔴 Bear Divergence</span>}
                    </div>
                  )}
                </div>

                {/* Col 3: Technicals */}
                <div className="space-y-2 bg-surface/30 p-4 rounded-lg border border-border/60">
                  <h3 className="font-bold text-white flex items-center gap-1.5 pb-1 border-b border-border">
                    📐 Technicals &amp; Bands
                  </h3>
                  <div className="text-muted">vs MA20: <b className="text-white">{activeDetailRow.price_vs_ma20}</b></div>
                  <div className="text-muted">vs MA50: <b className="text-white">{activeDetailRow.price_vs_ma50}</b></div>
                  <div className="text-muted">vs MA200: <b className="text-white">{activeDetailRow.price_vs_ma200}</b></div>
                  {activeDetailRow.golden_cross && <div className="text-bull font-semibold">✅ Golden Cross Active</div>}
                  {activeDetailRow.death_cross && <div className="text-bear font-semibold">❌ Death Cross Active</div>}
                  <div className="pt-2 border-t border-border/60">
                    <div className="text-muted">BB Width: <b className="text-white">{activeDetailRow.bb_bandwidth?.toFixed(1)}%</b></div>
                    <div className="text-muted">BB %B: <b className="text-white">{activeDetailRow.bb_pct_b?.toFixed(2)}</b></div>
                    <div className="text-muted">BB Position: <b className="text-white">{activeDetailRow.bb_position}</b></div>
                    {activeDetailRow.bb_squeeze && (
                      <div className="text-amber-400 font-bold mt-1">⚡ Bollinger Squeeze Active — breakout imminent</div>
                    )}
                  </div>
                </div>
              </div>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
