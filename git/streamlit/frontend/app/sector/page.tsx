"use client";
import React, { useEffect, useState } from "react";

const API_BASE = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

interface SectorOverviewItem {
  ticker: string;
  etf: string;
  name: string;
  emoji: string;
  stocks: string[];
  price: number;
  change_1d: number;
  change_1w: number;
  change_1m: number;
  momentum: number;
}

interface FibRow {
  sector: string;
  etf: string;
  emoji: string;
  as_of: string;
  close: number;
  weekly_hi: number;
  weekly_lo: number;
  weekly_range: number;
  weekly_pos_pct: number;
  weekly_zone: string;
  weekly_zone_desc: string;
  weekly_zone_clr: string;
  fund_bias: string;
  news_sentiment: string;
  conclusion: string;
  fib_levels: Record<string, number | null>;
}

interface GoldenZoneItem {
  sector: string;
  etf: string;
  emoji: string;
  price: number;
  r_38: number;
  r_50: number | null;
  r_61: number;
  weekly_zone: string;
}

interface StockSetup {
  ticker: string;
  price: number;
  score: number;
  verdict: string;
  confidence: string;
  stop_loss: number;
  target1: number;
  t1_days: number;
  valuation: string;
  valuation_color: string;
  market_cap: string;
}

interface ScanResponse {
  status: string;
  as_of_date: string;
  fib_report: {
    all: FibRow[];
    by_zone: {
      LOW: FibRow[];
      MID: FibRow[];
      HIGH: FibRow[];
    };
    golden_zone: GoldenZoneItem[];
    column_names: string[];
  };
  performance: {
    hot_sectors: SectorOverviewItem[];
    cold_sectors: SectorOverviewItem[];
    all_sectors: SectorOverviewItem[];
  };
  stocks_analysis: {
    bullish: StockSetup[];
    bearish: StockSetup[];
    total_scanned: number;
  };
}

export default function SectorScanPage() {
  const [overviewItems, setOverviewItems] = useState<SectorOverviewItem[]>([]);
  const [overviewLoading, setOverviewLoading] = useState(true);

  const [asOfDate, setAsOfDate] = useState(() => {
    return new Date().toISOString().split("T")[0];
  });
  const [isScanning, setIsScanning] = useState(false);
  const [scanResult, setScanResult] = useState<ScanResponse | null>(null);
  const [selectedZoneFilter, setSelectedZoneFilter] = useState<"ALL" | "LOW" | "MID" | "HIGH">("ALL");

  useEffect(() => {
    fetch(`${API_BASE}/api/sector/overview`)
      .then((res) => res.json())
      .then((data) => {
        setOverviewItems(data.items || []);
        setOverviewLoading(false);
      })
      .catch(() => setOverviewLoading(false));
  }, []);

  const handleScan = async () => {
    setIsScanning(true);
    try {
      const res = await fetch(`${API_BASE}/api/sector/scan`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ as_of_date: asOfDate }),
      });
      if (res.ok) {
        const data = await res.json();
        setScanResult(data);
      } else {
        alert("Failed to run sector scan. Check server logs.");
      }
    } catch (e: any) {
      alert(`Network error: ${e.message}`);
    } finally {
      setIsScanning(false);
    }
  };

  return (
    <div className="max-w-screen-2xl mx-auto px-4 py-8 space-y-6">
      {/* ── HEADER BANNER (matching stock_pulse.py lines 6960-6967) ── */}
      <div className="bg-gradient-to-r from-card to-surface border border-border rounded-xl p-5 shadow-sm">
        <div className="flex items-center gap-4">
          <div className="text-3xl">🔥</div>
          <div>
            <h1 className="text-xl font-bold text-white tracking-tight">Sector Scan</h1>
            <p className="text-muted text-xs mt-0.5">
              Identify hot &amp; cold sectors. Scans all 11 S&amp;P sectors for momentum, then finds top stocks within each.
            </p>
          </div>
        </div>
      </div>

      {/* ── CONTROLS: AS-OF DATE & SCAN BUTTON (lines 6970-6975) ────── */}
      <div className="bg-card border border-border rounded-xl p-4 flex flex-col md:flex-row items-center justify-between gap-4">
        <div className="flex items-center gap-3 w-full md:w-auto">
          <label className="text-xs font-semibold text-muted whitespace-nowrap">
            Fib Zones — As Of Date (backdating supported):
          </label>
          <input
            type="date"
            value={asOfDate}
            onChange={(e) => setAsOfDate(e.target.value)}
            className="bg-surface border border-border rounded-lg px-3 py-1.5 text-xs text-white font-mono focus:outline-none focus:border-accent"
          />
        </div>

        <button
          onClick={handleScan}
          disabled={isScanning}
          className="w-full md:w-auto bg-gradient-to-r from-accent to-blue-600 hover:from-accent/90 hover:to-blue-600/90 text-white text-xs font-bold px-8 py-2.5 rounded-lg shadow-md transition-all flex items-center justify-center gap-2 disabled:opacity-60"
        >
          {isScanning ? (
            <>
              <span className="animate-spin">🔄</span> Analyzing Sectors &amp; Top Stocks...
            </>
          ) : (
            <>🔥 SCAN ALL SECTORS</>
          )}
        </button>
      </div>

      {/* ── UN-SCANNED STATE: MINI SECTOR GRID (lines 6977-6999) ─────── */}
      {!scanResult && !isScanning && (
        <div className="space-y-6">
          <div className="text-center py-8">
            <div className="text-5xl opacity-20 mb-2">🔥</div>
            <div className="text-muted text-sm">
              Click <span className="text-accent font-bold">🔥 SCAN ALL SECTORS</span> to analyze sector momentum
            </div>
          </div>

          <div className="grid grid-cols-2 sm:grid-cols-3 md:grid-cols-4 lg:grid-cols-6 gap-3">
            {overviewLoading ? (
              <div className="col-span-full text-center py-10 text-muted text-xs">Loading sector grid...</div>
            ) : (
              overviewItems.map((s) => (
                <div
                  key={s.ticker}
                  className="bg-card border border-border hover:border-accent/40 rounded-lg p-3 text-center transition-all shadow-sm"
                >
                  <div className="text-2xl mb-1">{s.emoji}</div>
                  <div className="text-xs font-bold text-white truncate">{s.name}</div>
                  <div className="text-[11px] font-mono text-muted mt-0.5">{s.ticker}</div>
                  <div className="text-xs font-mono font-semibold text-white mt-1.5">${s.price}</div>
                  <div className={`text-[10px] font-mono font-bold mt-0.5 ${
                    s.change_1d >= 0 ? "text-bull" : "text-bear"
                  }`}>
                    {s.change_1d >= 0 ? "+" : ""}{s.change_1d}% (1D)
                  </div>
                </div>
              ))
            )}
          </div>
        </div>
      )}

      {/* ── SCANNED STATE: FULL DASHBOARD (lines 7001-7159 & 13000-13200) */}
      {scanResult && (
        <div className="space-y-8">
          {/* ══════════════════════════════════════════════════════════════
              PART 1: FIB SCENARIO REPORT FOR SECTORS (WEEKLY ONLY)
             ══════════════════════════════════════════════════════════════ */}
          <div className="bg-card border border-border rounded-xl p-5 space-y-4 shadow-sm">
            <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-3 border-b border-border pb-3">
              <div>
                <h2 className="text-base font-bold text-white flex items-center gap-2">
                  📐 Fib Scenario Report (Weekly Only)
                </h2>
                <p className="text-xs text-muted mt-0.5">
                  10-week swing high/low ranges, weekly zone territory, and Fibonacci extensions/retracements.
                </p>
              </div>

              {/* Zone Filter Buttons */}
              <div className="flex items-center gap-1.5 bg-surface p-1 rounded-lg border border-border text-xs font-semibold">
                {(["ALL", "LOW", "MID", "HIGH"] as const).map((z) => (
                  <button
                    key={z}
                    onClick={() => setSelectedZoneFilter(z)}
                    className={`px-3 py-1 rounded transition-colors ${
                      selectedZoneFilter === z
                        ? "bg-accent text-white shadow-sm"
                        : "text-muted hover:text-white"
                    }`}
                  >
                    {z === "ALL" ? "All Zones" : `${z} Zone`}
                  </button>
                ))}
              </div>
            </div>

            {/* ── 🥇 GOLDEN ZONE BANNER (lines 7122-7158) ─────────────── */}
            {scanResult.fib_report.golden_zone.length > 0 && (
              <div className="bg-gradient-to-r from-amber-950/40 via-surface to-amber-950/30 border border-amber-500/30 rounded-lg p-4 space-y-3">
                <div className="flex items-center gap-2">
                  <span className="text-xl">🥇</span>
                  <span className="font-bold text-amber-400 text-sm">Golden Zone</span>
                  <span className="text-xs text-amber-300/70">
                    ({scanResult.fib_report.golden_zone.length} sectors) — Price within R 38.2% – R 61.8% retracement
                  </span>
                </div>

                <div className="overflow-x-auto">
                  <table className="w-full text-left text-xs">
                    <thead className="text-muted border-b border-border">
                      <tr>
                        <th className="px-3 py-1.5">Sector</th>
                        <th className="px-3 py-1.5">ETF</th>
                        <th className="px-3 py-1.5">Price</th>
                        <th className="px-3 py-1.5 text-amber-400">R 38.2%</th>
                        <th className="px-3 py-1.5 text-amber-400">R 50.0%</th>
                        <th className="px-3 py-1.5 text-amber-400">R 61.8%</th>
                        <th className="px-3 py-1.5">Weekly Zone</th>
                      </tr>
                    </thead>
                    <tbody className="divide-y divide-border/50">
                      {scanResult.fib_report.golden_zone.map((gz) => (
                        <tr key={gz.etf} className="hover:bg-surface/50">
                          <td className="px-3 py-2 font-semibold text-white flex items-center gap-1.5">
                            <span>{gz.emoji}</span> {gz.sector}
                          </td>
                          <td className="px-3 py-2 font-mono font-bold text-accent">{gz.etf}</td>
                          <td className="px-3 py-2 font-mono font-bold text-white">${gz.price}</td>
                          <td className="px-3 py-2 font-mono font-bold text-amber-300">${gz.r_38}</td>
                          <td className="px-3 py-2 font-mono font-bold text-amber-300">${gz.r_50 || "—"}</td>
                          <td className="px-3 py-2 font-mono font-bold text-amber-300">${gz.r_61}</td>
                          <td className="px-3 py-2">
                            <span className={`px-2 py-0.5 rounded text-[10px] font-bold ${
                              gz.weekly_zone === "LOW"
                                ? "bg-bull/20 text-bull"
                                : gz.weekly_zone === "HIGH"
                                ? "bg-bear/20 text-bear"
                                : "bg-amber-500/20 text-amber-300"
                            }`}>
                              {gz.weekly_zone}
                            </span>
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </div>
            )}

            {/* ── ZONE TABLES (LOW, MID, HIGH) ───────────────────────── */}
            {(["LOW", "MID", "HIGH"] as const).map((zone) => {
              if (selectedZoneFilter !== "ALL" && selectedZoneFilter !== zone) return null;
              const rows = scanResult.fib_report.by_zone[zone] || [];
              if (rows.length === 0) return null;

              return (
                <div key={zone} className="space-y-2 pt-2">
                  <div className="flex items-center gap-2">
                    <span className={`text-xs font-bold px-2.5 py-0.5 rounded uppercase tracking-wider ${
                      zone === "LOW"
                        ? "bg-bull/20 text-bull border border-bull/30"
                        : zone === "HIGH"
                        ? "bg-bear/20 text-bear border border-bear/30"
                        : "bg-amber-500/20 text-amber-300 border border-amber-500/30"
                    }`}>
                      {zone} Weekly Zone
                    </span>
                    <span className="text-xs text-muted">
                      {zone === "LOW" && "Near Weekly Lo — Retrace territory"}
                      {zone === "MID" && "Mid Weekly Range — Balanced"}
                      {zone === "HIGH" && "Near Weekly Hi — Extension territory"}
                    </span>
                  </div>

                  <div className="overflow-x-auto border border-border rounded-lg">
                    <table className="w-full text-left text-xs whitespace-nowrap">
                      <thead className="bg-surface text-muted uppercase text-[10px] border-b border-border">
                        <tr>
                          <th className="px-3 py-2.5">Sector</th>
                          <th className="px-3 py-2.5">ETF</th>
                          <th className="px-3 py-2.5">Close</th>
                          <th className="px-3 py-2.5">Weekly Hi</th>
                          <th className="px-3 py-2.5">Weekly Lo</th>
                          <th className="px-3 py-2.5">Range</th>
                          <th className="px-3 py-2.5">Pos %</th>
                          <th className="px-3 py-2.5">Zone</th>
                          <th className="px-3 py-2.5">Fund Bias</th>
                          <th className="px-3 py-2.5">News</th>
                          <th className="px-3 py-2.5 text-amber-400 bg-amber-950/20">R 38.2%</th>
                          <th className="px-3 py-2.5 text-amber-400 bg-amber-950/20">R 50.0%</th>
                          <th className="px-3 py-2.5 text-amber-400 bg-amber-950/20">R 61.8%</th>
                          <th className="px-3 py-2.5">R 78.6%</th>
                          <th className="px-3 py-2.5">E 127.2%</th>
                          <th className="px-3 py-2.5">E 161.8%</th>
                        </tr>
                      </thead>
                      <tbody className="divide-y divide-border">
                        {rows.map((r) => (
                          <tr key={r.etf} className="hover:bg-surface/40">
                            <td className="px-3 py-2.5 font-semibold text-white flex items-center gap-1.5">
                              <span>{r.emoji}</span> {r.sector}
                            </td>
                            <td className="px-3 py-2.5 font-mono font-bold text-accent">{r.etf}</td>
                            <td className="px-3 py-2.5 font-mono font-bold text-white">${r.close}</td>
                            <td className="px-3 py-2.5 font-mono text-muted">${r.weekly_hi}</td>
                            <td className="px-3 py-2.5 font-mono text-muted">${r.weekly_lo}</td>
                            <td className="px-3 py-2.5 font-mono text-white">${r.weekly_range}</td>
                            <td className="px-3 py-2.5 font-mono font-bold text-white">{r.weekly_pos_pct}%</td>
                            <td className="px-3 py-2.5">
                              <span className={`px-2 py-0.5 rounded text-[10px] font-bold ${
                                r.weekly_zone === "LOW"
                                  ? "bg-bull/20 text-bull"
                                  : r.weekly_zone === "HIGH"
                                  ? "bg-bear/20 text-bear"
                                  : "bg-amber-500/20 text-amber-300"
                              }`}>
                                {r.weekly_zone}
                              </span>
                            </td>
                            <td className="px-3 py-2.5 text-muted">{r.fund_bias}</td>
                            <td className="px-3 py-2.5 text-muted">{r.news_sentiment}</td>
                            <td className="px-3 py-2.5 font-mono font-bold text-amber-300 bg-amber-950/10">
                              {r.fib_levels["R 38.2%"] ? `$${r.fib_levels["R 38.2%"]}` : "—"}
                            </td>
                            <td className="px-3 py-2.5 font-mono font-bold text-amber-300 bg-amber-950/10">
                              {r.fib_levels["R 50.0%"] ? `$${r.fib_levels["R 50.0%"]}` : "—"}
                            </td>
                            <td className="px-3 py-2.5 font-mono font-bold text-amber-300 bg-amber-950/10">
                              {r.fib_levels["R 61.8%"] ? `$${r.fib_levels["R 61.8%"]}` : "—"}
                            </td>
                            <td className="px-3 py-2.5 font-mono text-muted">
                              {r.fib_levels["R 78.6%"] ? `$${r.fib_levels["R 78.6%"]}` : "—"}
                            </td>
                            <td className="px-3 py-2.5 font-mono text-muted">
                              {r.fib_levels["E 127.2%"] ? `$${r.fib_levels["E 127.2%"]}` : "—"}
                            </td>
                            <td className="px-3 py-2.5 font-mono text-muted">
                              {r.fib_levels["E 161.8%"] ? `$${r.fib_levels["E 161.8%"]}` : "—"}
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                </div>
              );
            })}
          </div>

          {/* ══════════════════════════════════════════════════════════════
              PART 2: SECTOR PERFORMANCE (HOT VS COLD) (lines 13008-13085)
             ══════════════════════════════════════════════════════════════ */}
          <div className="space-y-4">
            <h2 className="text-base font-bold text-white flex items-center gap-2">
              🔥 Sector Performance Analysis
            </h2>

            <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
              {/* ── HOT SECTORS ── */}
              <div className="bg-card border border-border rounded-xl p-5 space-y-3">
                <div className="bg-bull/10 border border-bull/30 rounded-lg p-3">
                  <div className="text-sm font-bold text-bull">🔥 HOT SECTORS</div>
                  <div className="text-[11px] text-muted mt-0.5">Positive momentum (buy strength)</div>
                </div>

                <div className="space-y-2.5">
                  {scanResult.performance.hot_sectors.length === 0 ? (
                    <div className="text-xs text-muted text-center py-6">No hot sectors currently identified.</div>
                  ) : (
                    scanResult.performance.hot_sectors.map((data, idx) => (
                      <div
                        key={data.etf}
                        className="bg-surface/50 border border-border hover:border-bull/40 rounded-lg p-3 transition-colors"
                      >
                        <div className="flex justify-between items-center">
                          <span className="text-sm font-bold text-white flex items-center gap-1.5">
                            <span className="text-xs font-mono text-muted">#{idx + 1}</span>
                            <span>{data.emoji}</span>
                            <span>{data.name}</span>
                          </span>
                          <span className="text-xs font-mono font-bold text-bull">
                            +{data.momentum.toFixed(1)}
                          </span>
                        </div>
                        <div className="text-xs text-muted mt-1 font-mono">
                          <span className="text-accent font-bold">{data.etf}</span> ·{" "}
                          <span className={data.change_1d >= 0 ? "text-bull" : "text-bear"}>
                            1D: {data.change_1d >= 0 ? "+" : ""}{data.change_1d}%
                          </span>{" "}
                          ·{" "}
                          <span className={data.change_1w >= 0 ? "text-bull" : "text-bear"}>
                            1W: {data.change_1w >= 0 ? "+" : ""}{data.change_1w}%
                          </span>{" "}
                          ·{" "}
                          <span className={data.change_1m >= 0 ? "text-bull" : "text-bear"}>
                            1M: {data.change_1m >= 0 ? "+" : ""}{data.change_1m}%
                          </span>
                        </div>
                        <div className="text-[11px] text-accent mt-1 truncate">
                          Top: {data.stocks?.slice(0, 6).join(", ")}
                        </div>
                      </div>
                    ))
                  )}
                </div>
              </div>

              {/* ── COLD SECTORS ── */}
              <div className="bg-card border border-border rounded-xl p-5 space-y-3">
                <div className="bg-bear/10 border border-bear/30 rounded-lg p-3">
                  <div className="text-sm font-bold text-bear">❄️ COLD SECTORS</div>
                  <div className="text-[11px] text-muted mt-0.5">Negative momentum (avoid or short)</div>
                </div>

                <div className="space-y-2.5">
                  {scanResult.performance.cold_sectors.length === 0 ? (
                    <div className="text-xs text-muted text-center py-6">No cold sectors currently identified.</div>
                  ) : (
                    scanResult.performance.cold_sectors.map((data, idx) => (
                      <div
                        key={data.etf}
                        className="bg-surface/50 border border-border hover:border-bear/40 rounded-lg p-3 transition-colors"
                      >
                        <div className="flex justify-between items-center">
                          <span className="text-sm font-bold text-white flex items-center gap-1.5">
                            <span className="text-xs font-mono text-muted">#{idx + 1}</span>
                            <span>{data.emoji}</span>
                            <span>{data.name}</span>
                          </span>
                          <span className="text-xs font-mono font-bold text-bear">
                            {data.momentum.toFixed(1)}
                          </span>
                        </div>
                        <div className="text-xs text-muted mt-1 font-mono">
                          <span className="text-accent font-bold">{data.etf}</span> ·{" "}
                          <span className={data.change_1d >= 0 ? "text-bull" : "text-bear"}>
                            1D: {data.change_1d >= 0 ? "+" : ""}{data.change_1d}%
                          </span>{" "}
                          ·{" "}
                          <span className={data.change_1w >= 0 ? "text-bull" : "text-bear"}>
                            1W: {data.change_1w >= 0 ? "+" : ""}{data.change_1w}%
                          </span>{" "}
                          ·{" "}
                          <span className={data.change_1m >= 0 ? "text-bull" : "text-bear"}>
                            1M: {data.change_1m >= 0 ? "+" : ""}{data.change_1m}%
                          </span>
                        </div>
                        <div className="text-[11px] text-accent mt-1 truncate">
                          Top: {data.stocks?.slice(0, 6).join(", ")}
                        </div>
                      </div>
                    ))
                  )}
                </div>
              </div>
            </div>
          </div>

          {/* ══════════════════════════════════════════════════════════════
              PART 3: BEST STOCKS IN HOT SECTORS (lines 13088-13200)
             ══════════════════════════════════════════════════════════════ */}
          <div className="bg-card border border-border rounded-xl p-5 space-y-4 shadow-sm">
            <div className="border-b border-border pb-3">
              <h2 className="text-base font-bold text-white flex items-center gap-2">
                🎯 Best Stocks in Hot Sectors
              </h2>
              <p className="text-xs text-muted mt-0.5">
                Top quantitative momentum setups filtered from leading hot sector constituents.
              </p>
            </div>

            <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
              {/* Bullish Setups */}
              <div className="space-y-2.5">
                <div className="bg-bull/10 border border-bull/30 rounded-lg p-2.5">
                  <div className="text-xs font-bold text-bull">🚀 BULLISH in Hot Sectors</div>
                </div>

                {scanResult.stocks_analysis.bullish.length === 0 ? (
                  <div className="text-xs text-muted text-center py-6">No bullish setups currently in hot sectors.</div>
                ) : (
                  scanResult.stocks_analysis.bullish.map((r, idx) => (
                    <div
                      key={r.ticker}
                      className="bg-surface/50 border border-border rounded-lg p-3 hover:border-bull/40 transition-colors"
                    >
                      <div className="flex justify-between items-center">
                        <span className="text-sm font-bold text-white">
                          #{idx + 1} <span className="font-mono text-accent">{r.ticker}</span>
                        </span>
                        <span className="text-xs font-mono font-bold text-bull">+{r.score}</span>
                      </div>
                      <div className="text-xs text-muted mt-1 font-mono">
                        <span className="text-white font-bold">${r.price}</span> · Stop:{" "}
                        <span className="text-bear">${r.stop_loss}</span> · T1:{" "}
                        <span className="text-bull">${r.target1}</span>{" "}
                        <span className="text-accent text-[11px]">~{r.t1_days}td</span>
                      </div>
                      <div className="text-[11px] text-muted mt-1 flex items-center justify-between">
                        <span style={{ color: r.valuation_color }}>{r.valuation}</span>
                        <span>{r.market_cap}</span>
                      </div>
                    </div>
                  ))
                )}
              </div>

              {/* Bearish Setups */}
              <div className="space-y-2.5">
                <div className="bg-bear/10 border border-bear/30 rounded-lg p-2.5">
                  <div className="text-xs font-bold text-bear">📉 BEARISH in Hot Sectors</div>
                </div>

                {scanResult.stocks_analysis.bearish.length === 0 ? (
                  <div className="text-xs text-muted text-center py-6">No bearish setups currently in hot sectors.</div>
                ) : (
                  scanResult.stocks_analysis.bearish.map((r, idx) => (
                    <div
                      key={r.ticker}
                      className="bg-surface/50 border border-border rounded-lg p-3 hover:border-bear/40 transition-colors"
                    >
                      <div className="flex justify-between items-center">
                        <span className="text-sm font-bold text-white">
                          #{idx + 1} <span className="font-mono text-accent">{r.ticker}</span>
                        </span>
                        <span className="text-xs font-mono font-bold text-bear">{r.score}</span>
                      </div>
                      <div className="text-xs text-muted mt-1 font-mono">
                        <span className="text-white font-bold">${r.price}</span> · Stop:{" "}
                        <span className="text-bear">${r.stop_loss}</span> · T1:{" "}
                        <span className="text-bull">${r.target1}</span>{" "}
                        <span className="text-accent text-[11px]">~{r.t1_days}td</span>
                      </div>
                      <div className="text-[11px] text-muted mt-1 flex items-center justify-between">
                        <span style={{ color: r.valuation_color }}>{r.valuation}</span>
                        <span>{r.market_cap}</span>
                      </div>
                    </div>
                  ))
                )}
              </div>
            </div>

            <div className="text-[11px] text-muted text-center pt-2 border-t border-border/60">
              Scanned {scanResult.stocks_analysis.total_scanned} stocks from top 3 hot sectors
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
