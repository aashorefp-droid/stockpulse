"use client";
import React, { useEffect, useState, useMemo } from "react";

const API_BASE = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

interface ChartPoint {
  date: string;
  price: number;
  sma_50: number | null;
  sma_200: number | null;
  ratio_50: number;
  ratio_200: number;
  risk_score: number;
}

interface BubblePeriod {
  start: string;
  end: string;
  days: number;
}

interface BubbleResult {
  ticker: string;
  price: number;
  sma_50: number;
  sma_200: number;
  overval_50: number;
  overval_200: number;
  ret_20d: number;
  ret_50d: number;
  volatility: number;
  risk_score: number;
  max_risk_score: number;
  max_ret_20d: number;
  max_overval: number;
  risk_label: "HIGH" | "MEDIUM" | "ELEVATED" | "LOW";
  trend: "UPTREND" | "DOWNTREND" | "RECOVERING" | "WEAKENING";
  bubble_periods: number;
  bubble_period_details?: BubblePeriod[];
  chart_data?: ChartPoint[];
}

export default function BubbleScannerPage() {
  const [tickerInput, setTickerInput] = useState("NVDA, TSLA, AAPL, BTC-USD");
  const [period, setPeriod] = useState("3y");
  const [asOfDate, setAsOfDate] = useState(() => new Date().toISOString().split("T")[0]);
  const [loading, setLoading] = useState(false);
  const [results, setResults] = useState<BubbleResult[]>([]);
  const [selectedTicker, setSelectedTicker] = useState<string>("");

  const handleScan = async (tickersToScan?: string) => {
    setLoading(true);
    const raw = (tickersToScan || tickerInput).split(",").map((t) => t.trim().toUpperCase()).filter(Boolean);
    try {
      const res = await fetch(`${API_BASE}/api/bubble/scan`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          tickers: raw,
          period: period,
          as_of_date: asOfDate,
        }),
      });
      if (res.ok) {
        const data = await res.json();
        setResults(data.items || []);
        if (data.items && data.items.length > 0) {
          setSelectedTicker(data.items[0].ticker);
        }
      } else {
        alert("Bubble scan failed. Please check server logs.");
      }
    } catch (e: any) {
      alert(`Network error: ${e.message}`);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    handleScan("NVDA, TSLA, AAPL, BTC-USD");
  }, []);

  const activeResult = useMemo(() => {
    return results.find((r) => r.ticker === selectedTicker) || results[0] || null;
  }, [results, selectedTicker]);

  const handleDownloadCSV = () => {
    if (results.length === 0) return;
    const headers = [
      "Ticker", "Price", "Trend", "Risk", "Risk Score",
      "vs SMA50", "vs SMA200", "20D Ret", "50D Ret", "Volatility",
      "Bubble Periods", "Peak Overval", "Peak 20D Ret"
    ];
    const rows = results.map((r) => [
      r.ticker, r.price, r.trend, r.risk_label, r.risk_score,
      `${r.overval_50}%`, `${r.overval_200}%`, `${r.ret_20d}%`, `${r.ret_50d}%`, `${r.volatility}%`,
      r.bubble_periods, `${r.max_overval}%`, `${r.max_ret_20d}%`
    ]);
    const csvContent = [headers.join(","), ...rows.map((e) => e.join(","))].join("\n");
    const blob = new Blob([csvContent], { type: "text/csv;charset=utf-8;" });
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.setAttribute("href", url);
    link.setAttribute("download", `bubble_scan_${asOfDate}.csv`);
    document.body.appendChild(link);
    link.click();
    document.body.removeChild(link);
  };

  return (
    <div className="max-w-screen-2xl mx-auto px-4 py-8 space-y-6">
      {/* ── HEADER BANNER ───────────────────────────────────────────── */}
      <div className="bg-gradient-to-r from-card to-surface border border-border rounded-xl p-5 shadow-sm">
        <h1 className="text-2xl font-bold text-white tracking-tight flex items-center gap-2">
          🫧 Bubble Detection &amp; Risk Analysis
        </h1>
        <p className="text-muted text-xs mt-1">
          Detects bubble-like price behavior: overvaluation vs moving averages, rapid returns, elevated volatility, and composite risk score.
        </p>
      </div>

      {/* ── CONTROL BAR ─────────────────────────────────────────────── */}
      <div className="bg-card border border-border rounded-xl p-4 flex flex-col md:flex-row items-center justify-between gap-4">
        <div className="grid grid-cols-1 sm:grid-cols-3 gap-3 w-full md:w-auto flex-1">
          <div>
            <label className="text-xs text-muted block mb-1">Ticker(s) — comma-separated</label>
            <input
              type="text"
              value={tickerInput}
              onChange={(e) => setTickerInput(e.target.value)}
              placeholder="NVDA, TSLA, AAPL, BTC-USD"
              className="w-full bg-surface border border-border rounded-lg px-3 py-1.5 text-xs text-white font-mono focus:outline-none focus:border-accent uppercase"
            />
          </div>

          <div>
            <label className="text-xs text-muted block mb-1">Period</label>
            <select
              value={period}
              onChange={(e) => setPeriod(e.target.value)}
              className="w-full bg-surface border border-border rounded-lg px-3 py-1.5 text-xs text-white font-mono focus:outline-none focus:border-accent"
            >
              <option value="1y">1y</option>
              <option value="2y">2y</option>
              <option value="3y">3y</option>
              <option value="5y">5y</option>
            </select>
          </div>

          <div>
            <label className="text-xs text-muted block mb-1">As-Of Date (backdate)</label>
            <input
              type="date"
              value={asOfDate}
              onChange={(e) => setAsOfDate(e.target.value)}
              className="w-full bg-surface border border-border rounded-lg px-3 py-1.5 text-xs text-white font-mono focus:outline-none focus:border-accent"
            />
          </div>
        </div>

        <button
          onClick={() => handleScan()}
          disabled={loading}
          className="w-full md:w-auto bg-accent hover:bg-accent/90 text-white text-xs font-bold px-6 py-2.5 rounded-lg shadow transition-all flex items-center justify-center gap-2 disabled:opacity-50"
        >
          {loading ? (
            <>
              <span className="animate-spin">🔄</span> Scanning...
            </>
          ) : (
            <>🔍 Scan</>
          )}
        </button>
      </div>

      {/* ── SUMMARY TABLE (lines 18913-18957) ───────────────────────── */}
      {results.length > 0 && (
        <div className="bg-card border border-border rounded-xl overflow-hidden shadow-sm">
          <div className="p-4 border-b border-border flex items-center justify-between">
            <h2 className="text-sm font-bold text-white uppercase tracking-wider">
              📊 Multi-Ticker Bubble Assessment ({results.length})
            </h2>
            <button
              onClick={handleDownloadCSV}
              className="text-xs font-semibold text-accent hover:underline flex items-center gap-1"
            >
              📥 Download Summary CSV
            </button>
          </div>

          <div className="overflow-x-auto">
            <table className="w-full text-left text-xs whitespace-nowrap">
              <thead className="bg-surface text-muted text-[10px] uppercase border-b border-border">
                <tr>
                  <th className="px-4 py-3">Ticker</th>
                  <th className="px-4 py-3">Price</th>
                  <th className="px-4 py-3">Trend</th>
                  <th className="px-4 py-3">Risk Level</th>
                  <th className="px-4 py-3">Risk Score</th>
                  <th className="px-4 py-3">vs SMA50</th>
                  <th className="px-4 py-3">vs SMA200</th>
                  <th className="px-4 py-3">20D Ret</th>
                  <th className="px-4 py-3">50D Ret</th>
                  <th className="px-4 py-3">Volatility</th>
                  <th className="px-4 py-3">Bubble Periods</th>
                  <th className="px-4 py-3">Peak Overval</th>
                  <th className="px-4 py-3">Peak 20D Ret</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-border">
                {results.map((r) => {
                  const isHigh = r.risk_label === "HIGH";
                  const isMed = r.risk_label === "MEDIUM";
                  const isElev = r.risk_label === "ELEVATED";
                  const isUptrend = r.trend === "UPTREND";
                  const isDowntrend = r.trend === "DOWNTREND";

                  return (
                    <tr
                      key={r.ticker}
                      onClick={() => setSelectedTicker(r.ticker)}
                      className={`cursor-pointer transition-colors ${
                        selectedTicker === r.ticker ? "bg-accent/10 border-l-2 border-accent" : "hover:bg-surface/50"
                      }`}
                    >
                      <td className="px-4 py-3 font-mono font-bold text-accent">{r.ticker}</td>
                      <td className="px-4 py-3 font-mono font-bold text-white">${r.price.toFixed(2)}</td>
                      <td className="px-4 py-3">
                        <span className={`px-2 py-0.5 rounded text-[10px] font-bold ${
                          isUptrend
                            ? "bg-bull/20 text-bull border border-bull/30"
                            : isDowntrend
                            ? "bg-bear/20 text-bear border border-bear/30"
                            : "bg-surface text-muted"
                        }`}>
                          {r.trend}
                        </span>
                      </td>
                      <td className="px-4 py-3">
                        <span className={`px-2 py-0.5 rounded text-[10px] font-bold ${
                          isHigh
                            ? "bg-bear/20 text-bear border border-bear/30"
                            : isMed
                            ? "bg-amber-500/20 text-amber-300 border border-amber-500/30"
                            : isElev
                            ? "text-amber-400"
                            : "text-bull"
                        }`}>
                          {r.risk_label}
                        </span>
                      </td>
                      <td className="px-4 py-3 font-mono font-bold text-white">{r.risk_score}</td>
                      <td className={`px-4 py-3 font-mono ${r.overval_50 >= 0 ? "text-bull" : "text-bear"}`}>
                        {r.overval_50 >= 0 ? "+" : ""}{r.overval_50}%
                      </td>
                      <td className={`px-4 py-3 font-mono ${r.overval_200 >= 0 ? "text-bull" : "text-bear"}`}>
                        {r.overval_200 >= 0 ? "+" : ""}{r.overval_200}%
                      </td>
                      <td className={`px-4 py-3 font-mono ${r.ret_20d >= 0 ? "text-bull" : "text-bear"}`}>
                        {r.ret_20d >= 0 ? "+" : ""}{r.ret_20d}%
                      </td>
                      <td className={`px-4 py-3 font-mono ${r.ret_50d >= 0 ? "text-bull" : "text-bear"}`}>
                        {r.ret_50d >= 0 ? "+" : ""}{r.ret_50d}%
                      </td>
                      <td className="px-4 py-3 font-mono text-muted">{r.volatility}%</td>
                      <td className="px-4 py-3 font-mono font-bold text-white">{r.bubble_periods}</td>
                      <td className="px-4 py-3 font-mono text-muted">{r.max_overval}%</td>
                      <td className="px-4 py-3 font-mono text-muted">{r.max_ret_20d}%</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {/* ── DETAIL & CHART VIEW (lines 18960-19069) ─────────────────── */}
      {activeResult && (
        <div className="bg-card border border-border rounded-xl p-5 space-y-6 shadow-sm">
          <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4 border-b border-border pb-4">
            <div className="flex items-center gap-3">
              <span className="text-xl font-bold font-mono text-white">{activeResult.ticker}</span>
              <span className="text-sm font-mono text-muted">${activeResult.price.toFixed(2)}</span>
              <span className={`px-2 py-0.5 rounded text-xs font-bold ${
                activeResult.risk_label === "HIGH"
                  ? "bg-bear/20 text-bear border border-bear/30"
                  : activeResult.risk_label === "MEDIUM"
                  ? "bg-amber-500/20 text-amber-300 border border-amber-500/30"
                  : "bg-bull/20 text-bull border border-bull/30"
              }`}>
                Current Risk: {activeResult.risk_label}
              </span>
            </div>

            <div className="flex items-center gap-2">
              <label className="text-xs text-muted">Select ticker for chart:</label>
              <select
                value={selectedTicker}
                onChange={(e) => setSelectedTicker(e.target.value)}
                className="bg-surface border border-border rounded-lg px-3 py-1 text-xs text-white font-mono focus:outline-none focus:border-accent"
              >
                {results.map((r) => (
                  <option key={r.ticker} value={r.ticker}>
                    {r.ticker} ({r.risk_label})
                  </option>
                ))}
              </select>
            </div>
          </div>

          {/* Current Risk assessment banner (line 19058) */}
          <div className="bg-surface/50 border border-border rounded-lg p-4 text-xs font-mono flex flex-wrap gap-4 items-center justify-between">
            <div>
              <span className="text-muted">Risk Score:</span>{" "}
              <b className="text-white">{activeResult.risk_score}</b>
            </div>
            <div>
              <span className="text-muted">vs SMA50:</span>{" "}
              <b className={activeResult.overval_50 >= 0 ? "text-bull" : "text-bear"}>
                {activeResult.overval_50 >= 0 ? "+" : ""}{activeResult.overval_50}%
              </b>
            </div>
            <div>
              <span className="text-muted">vs SMA200:</span>{" "}
              <b className={activeResult.overval_200 >= 0 ? "text-bull" : "text-bear"}>
                {activeResult.overval_200 >= 0 ? "+" : ""}{activeResult.overval_200}%
              </b>
            </div>
            <div>
              <span className="text-muted">20D Return:</span>{" "}
              <b className={activeResult.ret_20d >= 0 ? "text-bull" : "text-bear"}>
                {activeResult.ret_20d >= 0 ? "+" : ""}{activeResult.ret_20d}%
              </b>
            </div>
            <div>
              <span className="text-muted">Volatility:</span>{" "}
              <b className="text-white">{activeResult.volatility}%</b>
            </div>
          </div>

          {/* ── 3-PANEL INTERACTIVE VISUAL CHARTS ── */}
          {activeResult.chart_data && activeResult.chart_data.length > 0 && (
            <div className="space-y-4">
              {/* Panel 1: Price + SMA50 + SMA200 */}
              <div className="bg-surface/40 border border-border rounded-lg p-4 space-y-2">
                <div className="flex justify-between items-center text-xs font-semibold">
                  <span className="text-white">{activeResult.ticker}: Price with Moving Averages</span>
                  <div className="flex items-center gap-3 text-[11px]">
                    <span className="text-accent">● Price</span>
                    <span className="text-amber-400">● SMA 50</span>
                    <span className="text-bull">● SMA 200</span>
                  </div>
                </div>
                <div className="h-44 w-full flex items-end gap-0.5 pt-2">
                  {(() => {
                    const data = activeResult.chart_data;
                    const maxP = Math.max(...data.map((d) => Math.max(d.price, d.sma_50 || 0, d.sma_200 || 0)));
                    const minP = Math.min(...data.map((d) => Math.min(d.price, d.sma_50 || d.price, d.sma_200 || d.price)));
                    const range = maxP - minP || 1;
                    return data.map((d, i) => {
                      const h = ((d.price - minP) / range) * 100;
                      return (
                        <div
                          key={i}
                          title={`${d.date}: Price $${d.price}, SMA50 $${d.sma_50}, SMA200 $${d.sma_200}`}
                          className="flex-1 bg-accent/60 hover:bg-accent rounded-t-sm transition-all"
                          style={{ height: `${Math.max(4, h)}%` }}
                        />
                      );
                    });
                  })()}
                </div>
              </div>

              {/* Panel 2: Price / SMA Ratios */}
              <div className="bg-surface/40 border border-border rounded-lg p-4 space-y-2">
                <div className="flex justify-between items-center text-xs font-semibold">
                  <span className="text-white">{activeResult.ticker}: Price / SMA50 Ratio</span>
                  <span className="text-bear text-[11px] font-mono">Overvaluation Line (1.30x)</span>
                </div>
                <div className="h-28 w-full flex items-end gap-0.5 pt-2 border-b border-bear/40 relative">
                  {(() => {
                    const data = activeResult.chart_data;
                    const maxR = Math.max(1.5, ...data.map((d) => d.ratio_50));
                    const minR = Math.min(0.7, ...data.map((d) => d.ratio_50));
                    const range = maxR - minR || 1;
                    return data.map((d, i) => {
                      const h = ((d.ratio_50 - minR) / range) * 100;
                      const isOver = d.ratio_50 >= 1.3;
                      return (
                        <div
                          key={i}
                          title={`${d.date}: Ratio ${d.ratio_50}x`}
                          className={`flex-1 rounded-t-sm transition-all ${
                            isOver ? "bg-bear" : "bg-blue-400/50"
                          }`}
                          style={{ height: `${Math.max(4, h)}%` }}
                        />
                      );
                    });
                  })()}
                </div>
              </div>

              {/* Panel 3: Risk Score Area */}
              <div className="bg-surface/40 border border-border rounded-lg p-4 space-y-2">
                <div className="flex justify-between items-center text-xs font-semibold">
                  <span className="text-white">{activeResult.ticker}: Composite Bubble Risk Score</span>
                  <div className="flex items-center gap-3 text-[11px] font-mono">
                    <span className="text-bear">High Risk (80)</span>
                    <span className="text-amber-400">Medium Risk (50)</span>
                  </div>
                </div>
                <div className="h-28 w-full flex items-end gap-0.5 pt-2">
                  {(() => {
                    const data = activeResult.chart_data;
                    const maxS = Math.max(100, ...data.map((d) => d.risk_score));
                    return data.map((d, i) => {
                      const h = (Math.max(0, d.risk_score) / maxS) * 100;
                      const isHigh = d.risk_score >= 80;
                      const isMed = d.risk_score >= 50;
                      return (
                        <div
                          key={i}
                          title={`${d.date}: Score ${d.risk_score}`}
                          className={`flex-1 rounded-t-sm transition-all ${
                            isHigh ? "bg-bear" : isMed ? "bg-amber-400" : "bg-bull/40"
                          }`}
                          style={{ height: `${Math.max(3, h)}%` }}
                        />
                      );
                    });
                  })()}
                </div>
              </div>
            </div>
          )}

          {/* Bubble Period Details (lines 19046-19053) */}
          <div className="border-t border-border pt-4">
            <h3 className="text-xs font-bold text-white uppercase tracking-wider mb-2">
              Bubble Periods Detected: {activeResult.bubble_period_details?.length || 0}
            </h3>
            {activeResult.bubble_period_details && activeResult.bubble_period_details.length > 0 ? (
              <div className="grid grid-cols-1 sm:grid-cols-2 md:grid-cols-3 gap-2">
                {activeResult.bubble_period_details.map((bp, i) => (
                  <div key={i} className="bg-surface border border-border rounded-lg p-2.5 text-xs font-mono">
                    <span className="text-bear font-bold">Period {i + 1}:</span> {bp.start} to {bp.end}{" "}
                    <span className="text-muted">({bp.days} days)</span>
                  </div>
                ))}
              </div>
            ) : (
              <div className="text-xs text-muted">No historical bubble periods detected for this ticker.</div>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
