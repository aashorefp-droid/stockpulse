"use client";

import { useEffect, useState } from "react";
import Link from "next/link";

const API_BASE = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

interface HoldingItem {
  id?: number;
  ticker: string;
  shares: number;
  avg_cost: number;
  current_price?: number;
  cost_basis?: number;
  market_value?: number;
  pnl_dollars?: number;
  pnl_pct?: number;
  notes?: string;
}

interface PortfolioSummary {
  total_positions: number;
  total_invested: number;
  total_market_value: number;
  total_pnl_dollars: number;
  total_pnl_pct: number;
}

interface StockAnalysisResult {
  ticker: string;
  price: number;
  shares: number;
  avg_cost: number;
  cost_basis: number;
  market_value: number;
  pnl_dollars: number;
  pnl_pct: number;
  verdict: string;
  confidence: string;
  score: number;
  sv_verdict?: string | null;
  sv_signal?: string | null;
  sv_fair_value?: string | null;
  sv_trust?: number | null;
  sv_pros?: string[];
  sv_cons?: string[];
  entry_grade: string;
  entry_label: string;
  entry_status: string;
  expected_wr: number;
  expected_avg: number;
  weekly_bias: string;
  daily_bias: string;
  h4_bias: string;
  ma_bias: string;
  mtf_signal: string;
  mtf_action: string;
  mtf_rank: number;
  entry: number;
  stop_loss?: number;
  target1?: number;
  target2?: number;
  t1_days?: number;
  risk_pct?: number;
  rr_t1?: number;
  rr_t2?: number;
  vol_trend: string;
  vol_ratio: number;
  fundamental: string;
  pe_ratio?: number;
  forward_pe?: number;
  peg_ratio?: number;
  rev_growth?: number;
  eps_growth?: number;
  profit_margin?: number;
  roe?: number;
  debt_to_equity?: number;
  beta?: number;
  dividend_yield?: number;
  short_pct?: number;
  target_upside?: number;
  analyst_target?: number;
  sector: string;
  news: string;
  flags: string[];
  wk_hi: number;
  wk_lo: number;
  wk_pos: number;
  weekly_zone: string;
  daily_zone: string;
  nearest_fib: string;
  fib_compression: boolean;
  in_golden_zone: boolean;
  fib_levels: Record<string, number>;
  covered_call: string;
  alpaca_options: string;
}

const FIB_COLS = [
  "E 261.8%", "E 200.0%", "E 161.8%", "E 141.4%", "E 127.2%",
  "R 0.0%", "R 23.6%", "R 38.2%", "R 50.0%", "R 61.8%", "R 78.6%", "R 100.0%",
  "N -23.6%", "N -38.2%", "N -50.0%", "N -61.8%", "N -100.0%",
];

const GOLDEN_COLS = new Set(["R 38.2%", "R 50.0%", "R 61.8%"]);

export default function HoldingsPage() {
  // Portfolio state
  const [holdings, setHoldings] = useState<HoldingItem[]>([]);
  const [summary, setSummary] = useState<PortfolioSummary | null>(null);
  const [loadingHoldings, setLoadingHoldings] = useState(true);
  const [manageExpanded, setManageExpanded] = useState(false);

  // New holding inputs
  const [newTicker, setNewTicker] = useState("");
  const [newShares, setNewShares] = useState(10);
  const [newCost, setNewCost] = useState(100.0);

  // Report controls
  const [tickersInput, setTickersInput] = useState("");
  const [asOfDate, setAsOfDate] = useState(() => new Date().toISOString().split("T")[0]);
  const [scanning, setScanning] = useState(false);
  const [reportData, setReportData] = useState<any>(null);
  const [activeSaTab, setActiveSaTab] = useState<"exceptional" | "exceptional_bear" | "rank1" | "rank2" | "rank3" | "all">("all");

  const loadHoldings = async () => {
    try {
      const res = await fetch(`${API_BASE}/api/holdings`);
      if (res.ok) {
        const data = await res.json();
        setHoldings(data.items || []);
        setSummary(data.summary || null);
        const tks = (data.items || []).map((h: any) => h.ticker).join(", ");
        if (!tickersInput) {
          setTickersInput(tks);
        }
      }
    } catch (e) {
      console.error(e);
    } finally {
      setLoadingHoldings(false);
    }
  };

  useEffect(() => {
    loadHoldings();
  }, []);

  const handleAddHolding = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!newTicker.trim()) return;
    try {
      await fetch(`${API_BASE}/api/holdings`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          ticker: newTicker.trim().toUpperCase(),
          shares: Number(newShares),
          avg_cost: Number(newCost),
        }),
      });
      setNewTicker("");
      loadHoldings();
    } catch (e) {
      console.error(e);
    }
  };

  const handleDeleteHolding = async (ticker: string) => {
    try {
      await fetch(`${API_BASE}/api/holdings/${ticker}`, { method: "DELETE" });
      loadHoldings();
    } catch (e) {
      console.error(e);
    }
  };

  const handleClearAll = async () => {
    if (!confirm("Are you sure you want to clear all holdings?")) return;
    try {
      await fetch(`${API_BASE}/api/holdings/clear`, { method: "POST" });
      loadHoldings();
    } catch (e) {
      console.error(e);
    }
  };

  const runReport = async () => {
    setScanning(true);
    try {
      const res = await fetch(`${API_BASE}/api/holdings/report`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          tickers: tickersInput.trim() || undefined,
          as_of: asOfDate || undefined,
        }),
      });
      if (res.ok) {
        const data = await res.json();
        setReportData(data);
      }
    } catch (e) {
      console.error(e);
    } finally {
      setScanning(false);
    }
  };

  const downloadCsv = (filename: string, rows: any[], headers?: string[]) => {
    if (!rows || rows.length === 0) return;
    const keys = headers || Object.keys(rows[0]);
    const csvContent = [
      keys.join(","),
      ...rows.map((row) =>
        keys
          .map((k) => {
            const v = row[k] === null || row[k] === undefined ? "" : String(row[k]);
            return `"${v.replace(/"/g, '""')}"`;
          })
          .join(",")
      ),
    ].join("\n");
    const blob = new Blob([csvContent], { type: "text/csv;charset=utf-8;" });
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.setAttribute("href", url);
    link.setAttribute("download", filename);
    document.body.appendChild(link);
    link.click();
    document.body.removeChild(link);
  };

  // Helper formatting functions
  const fmtCurrency = (v?: number) =>
    v !== undefined && v !== null ? `$${v.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}` : "—";

  const fmtPnl = (v?: number, isPct = false) => {
    if (v === undefined || v === null) return "—";
    const sign = v > 0 ? "+" : "";
    const color = v > 0 ? "text-bull" : v < 0 ? "text-bear" : "text-muted";
    return (
      <span className={`${color} font-mono font-bold`}>
        {sign}
        {isPct ? `${v.toFixed(2)}%` : `$${v.toFixed(2)}`}
      </span>
    );
  };

  const getZoneBadge = (zone: string) => {
    if (zone === "HIGH") return <span className="px-2 py-0.5 rounded text-xs font-bold bg-[#3d0a1a] text-bear border border-bear/30">🔴 HIGH</span>;
    if (zone === "LOW") return <span className="px-2 py-0.5 rounded text-xs font-bold bg-[#0a3d1f] text-bull border border-bull/30">🟢 LOW</span>;
    return <span className="px-2 py-0.5 rounded text-xs font-bold bg-[#3d3a0a] text-yellow-400 border border-yellow-500/30">🟡 MID</span>;
  };

  const getBiasBadge = (val?: string) => {
    const v = (val || "").toUpperCase();
    if (v.includes("BULL") || v === "POSITIVE") {
      return <span className="px-1.5 py-0.5 rounded text-[11px] font-bold bg-[#0a3d1f] text-bull">Bull</span>;
    }
    if (v.includes("BEAR") || v === "NEGATIVE") {
      return <span className="px-1.5 py-0.5 rounded text-[11px] font-bold bg-[#3d0a1a] text-bear">Bear</span>;
    }
    return <span className="px-1.5 py-0.5 rounded text-[11px] font-bold bg-surface text-muted">—</span>;
  };

  const getSignalBadge = (sig?: string) => {
    const s = sig || "—";
    if (s.includes("A+ Long") || s.includes("Strong Long")) {
      return <span className="px-2 py-0.5 rounded text-xs font-bold bg-[#0a3d1f] text-bull">{s}</span>;
    }
    if (s.includes("A+ Short") || s.includes("Strong Short")) {
      return <span className="px-2 py-0.5 rounded text-xs font-bold bg-[#3d0a1a] text-bear">{s}</span>;
    }
    return <span className="text-xs text-muted font-mono">{s}</span>;
  };

  const subtabCounts = {
    exceptional: reportData?.subtabs?.exceptional?.length || 0,
    exceptional_bear: reportData?.subtabs?.exceptional_bear?.length || 0,
    rank1: reportData?.subtabs?.rank1?.length || 0,
    rank2: reportData?.subtabs?.rank2?.length || 0,
    rank3: reportData?.subtabs?.rank3?.length || 0,
    all: reportData?.subtabs?.all?.length || 0,
  };

  const currentSaRows: StockAnalysisResult[] = reportData?.subtabs?.[activeSaTab] || [];

  return (
    <div className="max-w-screen-2xl mx-auto px-4 py-8 space-y-8">
      {/* ── 1. Page Header Banner ── */}
      <div className="rounded-xl border border-border bg-gradient-to-r from-[#0a0b14] via-[#131625] to-[#0a0b14] p-6 shadow-xl flex flex-wrap items-center justify-between gap-4">
        <div className="flex items-center gap-4">
          <div className="text-3xl p-3 bg-accent/10 border border-accent/20 rounded-xl">💼</div>
          <div>
            <h1 className="text-2xl font-bold text-white tracking-tight flex items-center gap-3">
              My Holdings
              <span className="text-xs font-normal text-muted bg-surface/80 border border-border px-2.5 py-1 rounded-full">
                Portfolio & Fib Zones
              </span>
            </h1>
            <p className="text-xs text-muted mt-1">
              Enter tickers, run the report → Weekly zones, Fib levels, covered-call strategy for each position.
            </p>
          </div>
        </div>

        {/* Portfolio Overview Stat Cards */}
        {summary && (
          <div className="flex flex-wrap gap-3">
            <div className="bg-card border border-border rounded-lg px-3.5 py-2">
              <div className="text-[10px] text-muted uppercase tracking-wider">Total Invested</div>
              <div className="text-sm font-bold font-mono text-white">{fmtCurrency(summary.total_invested)}</div>
            </div>
            <div className="bg-card border border-border rounded-lg px-3.5 py-2">
              <div className="text-[10px] text-muted uppercase tracking-wider">Market Value</div>
              <div className="text-sm font-bold font-mono text-white">{fmtCurrency(summary.total_market_value)}</div>
            </div>
            <div className="bg-card border border-border rounded-lg px-3.5 py-2">
              <div className="text-[10px] text-muted uppercase tracking-wider">Unrealized P&L</div>
              <div className="text-sm">{fmtPnl(summary.total_pnl_dollars)}</div>
            </div>
            <div className="bg-card border border-border rounded-lg px-3.5 py-2">
              <div className="text-[10px] text-muted uppercase tracking-wider">Total Return</div>
              <div className="text-sm">{fmtPnl(summary.total_pnl_pct, true)}</div>
            </div>
          </div>
        )}
      </div>

      {/* ── 2. Manage My Holdings (Persistent Storage) ── */}
      <div className="bg-card border border-border rounded-xl overflow-hidden shadow-sm">
        <button
          onClick={() => setManageExpanded(!manageExpanded)}
          className="w-full flex items-center justify-between p-4 text-left hover:bg-surface/50 transition-colors border-b border-border/50"
        >
          <div className="flex items-center gap-2">
            <span className="text-sm font-bold text-white">📝 Manage My Holdings (persistent)</span>
            <span className="text-xs text-muted">({holdings.length} positions)</span>
          </div>
          <span className="text-xs text-accent font-semibold">{manageExpanded ? "▲ Collapse" : "▼ Expand / Edit"}</span>
        </button>

        {manageExpanded && (
          <div className="p-4 space-y-6">
            <p className="text-xs text-muted">
              Add or edit your holdings below. Changes are saved to disk (`stockpulse_trades.db` + `my_holdings.csv`) and persist across sessions.
            </p>

            {/* Add New Holding Form */}
            <form onSubmit={handleAddHolding} className="flex flex-wrap gap-4 items-end bg-surface/60 border border-border rounded-lg p-3">
              <div>
                <label className="block text-[11px] font-medium text-muted mb-1">Ticker</label>
                <input
                  className="bg-card border border-border rounded px-3 py-1.5 text-white font-mono uppercase w-28 text-sm focus:outline-none focus:border-accent"
                  placeholder="AAPL"
                  value={newTicker}
                  onChange={(e) => setNewTicker(e.target.value)}
                />
              </div>
              <div>
                <label className="block text-[11px] font-medium text-muted mb-1">Shares</label>
                <input
                  type="number"
                  step="any"
                  className="bg-card border border-border rounded px-3 py-1.5 text-white font-mono w-28 text-sm focus:outline-none focus:border-accent"
                  value={newShares}
                  onChange={(e) => setNewShares(parseFloat(e.target.value) || 0)}
                />
              </div>
              <div>
                <label className="block text-[11px] font-medium text-muted mb-1">Avg Cost ($)</label>
                <input
                  type="number"
                  step="0.01"
                  className="bg-card border border-border rounded px-3 py-1.5 text-white font-mono w-32 text-sm focus:outline-none focus:border-accent"
                  value={newCost}
                  onChange={(e) => setNewCost(parseFloat(e.target.value) || 0)}
                />
              </div>
              <button
                type="submit"
                className="bg-accent text-black text-xs font-bold px-4 py-2 rounded hover:bg-accent/80 transition-colors"
              >
                ➕ Add Position
              </button>
            </form>

            {/* Holdings Table */}
            <div className="overflow-x-auto border border-border rounded-lg">
              <table className="w-full text-left text-xs">
                <thead className="bg-surface text-muted border-b border-border uppercase text-[10px]">
                  <tr>
                    <th className="px-4 py-2.5">Ticker</th>
                    <th className="px-4 py-2.5">Shares</th>
                    <th className="px-4 py-2.5">Avg Cost</th>
                    <th className="px-4 py-2.5">Price</th>
                    <th className="px-4 py-2.5">Cost Basis</th>
                    <th className="px-4 py-2.5">Market Value</th>
                    <th className="px-4 py-2.5">P&L ($)</th>
                    <th className="px-4 py-2.5">P&L (%)</th>
                    <th className="px-4 py-2.5 text-right">Action</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-border">
                  {holdings.length === 0 ? (
                    <tr>
                      <td colSpan={9} className="text-center py-6 text-muted">
                        No holdings saved yet. Add tickers above.
                      </td>
                    </tr>
                  ) : (
                    holdings.map((h) => (
                      <tr key={h.ticker} className="hover:bg-surface/30">
                        <td className="px-4 py-2.5 font-mono font-bold text-accent">
                          <Link href={`/stock/${h.ticker}`} className="hover:underline">
                            {h.ticker}
                          </Link>
                        </td>
                        <td className="px-4 py-2.5 font-mono">{h.shares}</td>
                        <td className="px-4 py-2.5 font-mono">${h.avg_cost.toFixed(2)}</td>
                        <td className="px-4 py-2.5 font-mono text-white">${h.current_price?.toFixed(2) || "—"}</td>
                        <td className="px-4 py-2.5 font-mono">${h.cost_basis?.toFixed(2) || "—"}</td>
                        <td className="px-4 py-2.5 font-mono text-white font-bold">${h.market_value?.toFixed(2) || "—"}</td>
                        <td className="px-4 py-2.5 font-mono">{fmtPnl(h.pnl_dollars)}</td>
                        <td className="px-4 py-2.5 font-mono">{fmtPnl(h.pnl_pct, true)}</td>
                        <td className="px-4 py-2.5 text-right">
                          <button
                            onClick={() => handleDeleteHolding(h.ticker)}
                            className="text-bear text-xs hover:underline"
                          >
                            🗑️ Delete
                          </button>
                        </td>
                      </tr>
                    ))
                  )}
                </tbody>
              </table>
            </div>

            {holdings.length > 0 && (
              <div className="flex justify-end">
                <button
                  onClick={handleClearAll}
                  className="text-xs text-bear hover:bg-bear/10 border border-bear/20 px-3 py-1.5 rounded transition-colors"
                >
                  🗑️ Clear All Holdings
                </button>
              </div>
            )}
          </div>
        )}
      </div>

      {/* ── 3. Controls & Report Trigger Bar ── */}
      <div className="bg-card border border-border rounded-xl p-5 shadow-sm space-y-4">
        <div className="grid grid-cols-1 md:grid-cols-4 gap-4">
          <div className="md:col-span-3">
            <label className="block text-xs font-semibold text-muted uppercase tracking-wider mb-1.5">
              Tickers (comma-separated) — auto-filled from saved holdings
            </label>
            <input
              className="w-full bg-surface border border-border rounded-lg px-3 py-2 text-white font-mono text-sm placeholder-muted focus:outline-none focus:border-accent"
              placeholder="e.g. PLUG, AAPL, NVDA, TSLA"
              value={tickersInput}
              onChange={(e) => setTickersInput(e.target.value.toUpperCase())}
            />
          </div>
          <div>
            <label className="block text-xs font-semibold text-muted uppercase tracking-wider mb-1.5">
              As-Of Date
            </label>
            <input
              type="date"
              className="w-full bg-surface border border-border rounded-lg px-3 py-2 text-white text-sm focus:outline-none focus:border-accent"
              value={asOfDate}
              onChange={(e) => setAsOfDate(e.target.value)}
            />
          </div>
        </div>

        <button
          onClick={runReport}
          disabled={scanning}
          className="w-full py-3 bg-accent text-black font-bold text-sm rounded-lg hover:bg-accent/80 transition-all flex items-center justify-center gap-2 shadow-lg disabled:opacity-50"
        >
          {scanning ? (
            <>
              <span className="animate-spin text-lg">⏳</span>
              <span>Scanning Holdings & Computing Fib Zones...</span>
            </>
          ) : (
            <>
              <span>🔍</span>
              <span>RUN HOLDINGS REPORT</span>
            </>
          )}
        </button>
      </div>

      {/* ── REPORT CONTENT ── */}
      {reportData && (
        <div className="space-y-8 animate-in fade-in duration-300">
          {/* Summary counters bar */}
          <div className="bg-[#0d0f17] border border-border rounded-lg p-3.5 flex flex-wrap items-center justify-between text-xs">
            <div className="flex flex-wrap gap-4 items-center">
              <span className="text-muted">
                Scanned <b className="text-white">{reportData.summary?.total_scanned || 0}</b> holdings
              </span>
              <span>·</span>
              <span className="text-bull font-semibold">
                <b>{reportData.summary?.bullish || 0}</b> bullish
              </span>
              <span>·</span>
              <span className="text-bear font-semibold">
                <b>{reportData.summary?.bearish || 0}</b> bearish
              </span>
              <span>·</span>
              <span className="text-[#4d9fff] font-semibold">
                <b>{reportData.summary?.high_conf || 0}</b> high confidence
              </span>
              <span>·</span>
              <span className="text-white font-semibold">
                <b>{reportData.summary?.actionable || 0}</b> actionable / {reportData.summary?.total_scanned || 0} total
              </span>
            </div>
            <button
              onClick={() => downloadCsv(`holdings_scan_all_${asOfDate}.csv`, reportData.results)}
              className="text-xs bg-surface border border-border hover:bg-surface/80 text-white font-semibold px-3 py-1 rounded transition-colors"
            >
              📥 Download All CSV
            </button>
          </div>

          {/* ── 4. Stock Analysis — Holdings Dense Table ── */}
          <div className="bg-card border border-border rounded-xl p-5 space-y-4">
            <div className="flex flex-wrap items-center justify-between gap-2">
              <div>
                <h2 className="text-lg font-bold text-white flex items-center gap-2">
                  <span>🔬</span> Stock Analysis — Holdings
                </h2>
                <p className="text-xs text-muted">
                  Full technical + fundamental scan enriched with your portfolio cost basis and P&L.
                </p>
              </div>

              {/* Sub-tabs */}
              <div className="flex flex-wrap gap-1 bg-surface p-1 rounded-lg border border-border text-xs">
                <button
                  onClick={() => setActiveSaTab("exceptional")}
                  className={`px-3 py-1 rounded font-semibold transition-colors ${
                    activeSaTab === "exceptional" ? "bg-accent text-black" : "text-muted hover:text-white"
                  }`}
                >
                  ⭐ Exceptional ({subtabCounts.exceptional})
                </button>
                <button
                  onClick={() => setActiveSaTab("exceptional_bear")}
                  className={`px-3 py-1 rounded font-semibold transition-colors ${
                    activeSaTab === "exceptional_bear" ? "bg-bear text-white" : "text-muted hover:text-white"
                  }`}
                >
                  💀 Exceptional Bear ({subtabCounts.exceptional_bear})
                </button>
                <button
                  onClick={() => setActiveSaTab("rank1")}
                  className={`px-3 py-1 rounded font-semibold transition-colors ${
                    activeSaTab === "rank1" ? "bg-bull text-black" : "text-muted hover:text-white"
                  }`}
                >
                  🎯 Rank 1 ({subtabCounts.rank1})
                </button>
                <button
                  onClick={() => setActiveSaTab("rank2")}
                  className={`px-3 py-1 rounded font-semibold transition-colors ${
                    activeSaTab === "rank2" ? "bg-[#4d9fff] text-white" : "text-muted hover:text-white"
                  }`}
                >
                  ✅ Rank 2 ({subtabCounts.rank2})
                </button>
                <button
                  onClick={() => setActiveSaTab("rank3")}
                  className={`px-3 py-1 rounded font-semibold transition-colors ${
                    activeSaTab === "rank3" ? "bg-yellow-400 text-black" : "text-muted hover:text-white"
                  }`}
                >
                  📋 Rank 3+ ({subtabCounts.rank3})
                </button>
                <button
                  onClick={() => setActiveSaTab("all")}
                  className={`px-3 py-1 rounded font-semibold transition-colors ${
                    activeSaTab === "all" ? "bg-card text-white border border-border" : "text-muted hover:text-white"
                  }`}
                >
                  📊 All ({subtabCounts.all})
                </button>
              </div>
            </div>

            {/* Table */}
            <div className="overflow-x-auto border border-border rounded-lg max-h-[600px]">
              <table className="w-full text-left text-xs whitespace-nowrap">
                <thead className="bg-surface text-muted sticky top-0 z-10 border-b border-border uppercase text-[10px]">
                  <tr>
                    <th className="px-3 py-2.5">Chart</th>
                    <th className="px-3 py-2.5">Ticker</th>
                    <th className="px-3 py-2.5">Shares</th>
                    <th className="px-3 py-2.5">Avg Cost</th>
                    <th className="px-3 py-2.5">Price</th>
                    <th className="px-3 py-2.5">Mkt Value</th>
                    <th className="px-3 py-2.5">P&L ($)</th>
                    <th className="px-3 py-2.5">P&L (%)</th>
                    <th className="px-3 py-2.5">Status</th>
                    <th className="px-3 py-2.5">Grade</th>
                    <th className="px-3 py-2.5">Signal</th>
                    <th className="px-3 py-2.5">Action</th>
                    <th className="px-3 py-2.5">Weekly</th>
                    <th className="px-3 py-2.5">Daily</th>
                    <th className="px-3 py-2.5">4H</th>
                    <th className="px-3 py-2.5">MA Bias</th>
                    <th className="px-3 py-2.5">Funda</th>
                    <th className="px-3 py-2.5">SV Verdict</th>
                    <th className="px-3 py-2.5">SV Fair</th>
                    <th className="px-3 py-2.5">SV Signal</th>
                    <th className="px-3 py-2.5">Stop</th>
                    <th className="px-3 py-2.5">Target 1</th>
                    <th className="px-3 py-2.5">T1 (td)</th>
                    <th className="px-3 py-2.5">Risk%</th>
                    <th className="px-3 py-2.5">Best RR</th>
                    <th className="px-3 py-2.5">Vol Ratio</th>
                    <th className="px-3 py-2.5">Vol Trend</th>
                    <th className="px-3 py-2.5">P/E</th>
                    <th className="px-3 py-2.5">Rev Growth</th>
                    <th className="px-3 py-2.5">Margin</th>
                    <th className="px-3 py-2.5">ROE</th>
                    <th className="px-3 py-2.5">News</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-border">
                  {currentSaRows.length === 0 ? (
                    <tr>
                      <td colSpan={32} className="text-center py-8 text-muted">
                        No holdings found in this category.
                      </td>
                    </tr>
                  ) : (
                    currentSaRows.map((r) => (
                      <tr key={r.ticker} className="hover:bg-surface/40">
                        <td className="px-3 py-2">
                          <a
                            href={`https://finviz.com/quote.ashx?t=${r.ticker}&p=d`}
                            target="_blank"
                            rel="noreferrer"
                            className="text-accent hover:underline font-bold"
                          >
                            📈
                          </a>
                        </td>
                        <td className="px-3 py-2 font-mono font-bold text-white">
                          <Link href={`/stock/${r.ticker}`} className="hover:text-accent">
                            {r.ticker}
                          </Link>
                        </td>
                        <td className="px-3 py-2 font-mono">{r.shares || "—"}</td>
                        <td className="px-3 py-2 font-mono">{r.avg_cost ? `$${r.avg_cost.toFixed(2)}` : "—"}</td>
                        <td className="px-3 py-2 font-mono text-white font-bold">${r.price.toFixed(2)}</td>
                        <td className="px-3 py-2 font-mono">{r.market_value ? `$${r.market_value.toLocaleString()}` : "—"}</td>
                        <td className="px-3 py-2 font-mono">{r.shares > 0 ? fmtPnl(r.pnl_dollars) : "—"}</td>
                        <td className="px-3 py-2 font-mono">{r.shares > 0 ? fmtPnl(r.pnl_pct, true) : "—"}</td>
                        <td className="px-3 py-2">
                          <span
                            className={`px-1.5 py-0.5 rounded font-bold text-[10px] ${
                              r.entry_status === "ENTER"
                                ? "bg-bull text-black"
                                : r.entry_status === "HOLD"
                                ? "bg-yellow-400 text-black"
                                : "bg-surface text-muted"
                            }`}
                          >
                            {r.entry_status}
                          </span>
                        </td>
                        <td className="px-3 py-2 font-mono font-bold text-accent">{r.entry_grade}</td>
                        <td className="px-3 py-2">{getSignalBadge(r.mtf_signal)}</td>
                        <td className="px-3 py-2 text-muted">{r.mtf_action}</td>
                        <td className="px-3 py-2">{getBiasBadge(r.weekly_bias)}</td>
                        <td className="px-3 py-2">{getBiasBadge(r.daily_bias)}</td>
                        <td className="px-3 py-2">{getBiasBadge(r.h4_bias)}</td>
                        <td className="px-3 py-2">{getBiasBadge(r.ma_bias)}</td>
                        <td className="px-3 py-2">
                          <span
                            className={`px-1.5 py-0.5 rounded font-bold text-[10px] ${
                              r.fundamental === "Strong"
                                ? "bg-[#0a3d1f] text-bull"
                                : r.fundamental === "Weak"
                                ? "bg-[#3d0a1a] text-bear"
                                : "bg-surface text-muted"
                            }`}
                          >
                            {r.fundamental}
                          </span>
                        </td>
                        <td className="px-3 py-2">
                          {r.sv_verdict ? (
                            <span
                              className={`px-1.5 py-0.5 rounded font-bold text-[10px] uppercase ${
                                r.sv_verdict.toUpperCase() === "GROWTH"
                                  ? "bg-purple-950/80 text-purple-300 border border-purple-800/60"
                                  : r.sv_verdict.toUpperCase() === "VALUE"
                                  ? "bg-cyan-950/80 text-cyan-300 border border-cyan-800/60"
                                  : r.sv_verdict.toUpperCase() === "AVOID"
                                  ? "bg-red-950/80 text-red-300 border border-red-800/60"
                                  : "bg-amber-950/80 text-amber-300 border border-amber-800/60"
                              }`}
                            >
                              {r.sv_verdict}
                            </span>
                          ) : (
                            <span className="text-muted text-[11px]">—</span>
                          )}
                        </td>
                        <td className="px-3 py-2 font-mono text-white">
                          {r.sv_fair_value || "—"}
                        </td>
                        <td className="px-3 py-2 text-[11px] text-muted max-w-[140px] truncate" title={r.sv_signal || ""}>
                          {r.sv_signal || "—"}
                        </td>
                        <td className="px-3 py-2 font-mono text-bear">${r.stop_loss?.toFixed(2) || "—"}</td>
                        <td className="px-3 py-2 font-mono text-bull">${r.target1?.toFixed(2) || "—"}</td>
                        <td className="px-3 py-2 font-mono text-muted">{r.t1_days ? `~${r.t1_days} td` : "—"}</td>
                        <td className="px-3 py-2 font-mono text-muted">{r.risk_pct ? `${r.risk_pct.toFixed(1)}%` : "—"}</td>
                        <td className="px-3 py-2 font-mono text-white">{r.rr_t1 ? `${r.rr_t1.toFixed(1)}x` : "—"}</td>
                        <td className="px-3 py-2 font-mono">{r.vol_ratio?.toFixed(1)}x</td>
                        <td className="px-3 py-2 text-muted text-[11px]">{r.vol_trend}</td>
                        <td className="px-3 py-2 font-mono text-muted">{r.pe_ratio ? r.pe_ratio.toFixed(1) : "—"}</td>
                        <td className="px-3 py-2 font-mono text-muted">
                          {r.rev_growth !== undefined ? `${(r.rev_growth * 100).toFixed(1)}%` : "—"}
                        </td>
                        <td className="px-3 py-2 font-mono text-muted">
                          {r.profit_margin !== undefined ? `${(r.profit_margin * 100).toFixed(1)}%` : "—"}
                        </td>
                        <td className="px-3 py-2 font-mono text-muted">
                          {r.roe !== undefined ? `${(r.roe * 100).toFixed(1)}%` : "—"}
                        </td>
                        <td className="px-3 py-2 text-[11px]">
                          <span
                            className={`px-1.5 py-0.5 rounded ${
                              r.news === "Good"
                                ? "bg-[#0a3d1f] text-bull font-bold"
                                : r.news === "Bad"
                                ? "bg-[#3d0a1a] text-bear font-bold"
                                : "text-muted"
                            }`}
                          >
                            {r.news}
                          </span>
                        </td>
                      </tr>
                    ))
                  )}
                </tbody>
              </table>
            </div>
          </div>

          {/* ── 5. Covered Call Report ── */}
          {reportData.covered_calls && reportData.covered_calls.length > 0 && (
            <div className="bg-card border border-border rounded-xl p-5 space-y-4">
              <div className="flex items-center justify-between">
                <div>
                  <h2 className="text-lg font-bold text-white flex items-center gap-2">
                    <span>📞</span> Covered Call Report
                  </h2>
                  <p className="text-xs text-muted">Covered call strategies and Alpaca options for your holdings.</p>
                </div>
                <button
                  onClick={() => downloadCsv(`covered_call_report_${asOfDate}.csv`, reportData.covered_calls)}
                  className="text-xs bg-surface border border-border hover:bg-surface/80 text-white font-semibold px-3 py-1 rounded transition-colors"
                >
                  📥 Download Covered Call CSV
                </button>
              </div>

              <div className="overflow-x-auto border border-border rounded-lg">
                <table className="w-full text-left text-xs">
                  <thead className="bg-surface text-muted uppercase text-[10px] border-b border-border">
                    <tr>
                      <th className="px-4 py-2.5 w-24">Ticker</th>
                      <th className="px-4 py-2.5 w-28">Price</th>
                      <th className="px-4 py-2.5 w-32">Signal</th>
                      <th className="px-4 py-2.5">Covered Call Recommendation</th>
                      <th className="px-4 py-2.5">Alpaca Options Chain</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-border">
                    {reportData.covered_calls.map((cc: any) => (
                      <tr key={cc.ticker} className="hover:bg-surface/30">
                        <td className="px-4 py-3 font-mono font-bold text-accent">
                          <Link href={`/stock/${cc.ticker}`} className="hover:underline">
                            {cc.ticker}
                          </Link>
                        </td>
                        <td className="px-4 py-3 font-mono text-white">${cc.price?.toFixed(2)}</td>
                        <td className="px-4 py-3">{getSignalBadge(cc.signal)}</td>
                        <td className="px-4 py-3 text-white font-mono text-[11px]">{cc.covered_call}</td>
                        <td className="px-4 py-3 text-muted font-mono text-[11px]">{cc.alpaca_options || "N/A"}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          )}

          {/* ── 6. Holdings Fib Zone Report & Golden Zone ── */}
          <div className="bg-card border border-border rounded-xl p-5 space-y-6">
            <div className="flex flex-wrap items-center justify-between gap-2">
              <div>
                <h2 className="text-lg font-bold text-white flex items-center gap-2">
                  <span>💼</span> Holdings Fib & Covered Call Report
                </h2>
                <p className="text-xs text-muted">
                  {reportData.results?.length} tickers · 🔵 Blue border = nearest fib to current price · Gold column = Golden Zone
                </p>
              </div>
              <button
                onClick={() => downloadCsv(`holdings_fib_report_${asOfDate}.csv`, reportData.results)}
                className="text-xs bg-surface border border-border hover:bg-surface/80 text-white font-semibold px-3 py-1 rounded transition-colors"
              >
                📥 Download Fib Report CSV
              </button>
            </div>

            {/* 🥇 Golden Zone Banner Card */}
            {reportData.golden_zone && reportData.golden_zone.length > 0 && (
              <div className="rounded-xl border border-yellow-500/40 bg-gradient-to-r from-[#1a1500] to-[#1f1800] p-4">
                <div className="flex items-center gap-3 mb-3">
                  <span className="text-xl">🥇</span>
                  <div className="text-sm font-bold text-yellow-400">Golden Zone Pullback Holdings</div>
                  <span className="text-xs text-yellow-500/80">
                    ({reportData.golden_zone.length} tickers inside R 38.2% – R 61.8% high-probability retracement)
                  </span>
                </div>
                <div className="overflow-x-auto">
                  <table className="w-full text-left text-xs">
                    <thead className="text-[10px] text-yellow-500/80 uppercase border-b border-yellow-500/20">
                      <tr>
                        <th className="px-3 py-1.5">Ticker</th>
                        <th className="px-3 py-1.5">Price</th>
                        <th className="px-3 py-1.5">R 38.2%</th>
                        <th className="px-3 py-1.5">R 50.0%</th>
                        <th className="px-3 py-1.5">R 61.8%</th>
                        <th className="px-3 py-1.5">Weekly Zone</th>
                      </tr>
                    </thead>
                    <tbody className="divide-y divide-yellow-500/10">
                      {reportData.golden_zone.map((gz: any) => (
                        <tr key={gz.ticker}>
                          <td className="px-3 py-2 font-mono font-bold text-white">{gz.ticker}</td>
                          <td className="px-3 py-2 font-mono text-yellow-400 font-bold">${gz.price?.toFixed(2)}</td>
                          <td className="px-3 py-2 font-mono text-yellow-300">${gz.r_38_2?.toFixed(2)}</td>
                          <td className="px-3 py-2 font-mono text-yellow-300 font-bold">${gz.r_50_0?.toFixed(2)}</td>
                          <td className="px-3 py-2 font-mono text-yellow-300">${gz.r_61_8?.toFixed(2)}</td>
                          <td className="px-3 py-2">{getZoneBadge(gz.weekly_zone)}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </div>
            )}

            {/* Split by Weekly Zone (HIGH, MID, LOW) */}
            {[
              { key: "high", title: "HIGH Weekly Zone", color: "text-bear", border: "border-bear/40", icon: "🔴", list: reportData.fib_zones?.high },
              { key: "mid", title: "MID Weekly Zone", color: "text-yellow-400", border: "border-yellow-500/40", icon: "🟡", list: reportData.fib_zones?.mid },
              { key: "low", title: "LOW Weekly Zone", color: "text-bull", border: "border-bull/40", icon: "🟢", list: reportData.fib_zones?.low },
            ].map(
              (group) =>
                group.list &&
                group.list.length > 0 && (
                  <div key={group.key} className="space-y-2">
                    <div className={`border-l-4 ${group.border} pl-3 py-1 flex items-center gap-2`}>
                      <span className="text-sm">{group.icon}</span>
                      <span className={`text-xs font-bold uppercase tracking-wider ${group.color}`}>{group.title}</span>
                      <span className="text-xs text-muted">({group.list.length} tickers)</span>
                    </div>

                    <div className="overflow-x-auto border border-border rounded-lg max-h-[400px]">
                      <table className="w-full text-left text-xs whitespace-nowrap">
                        <thead className="bg-surface text-muted uppercase text-[10px] sticky top-0 z-10 border-b border-border">
                          <tr>
                            <th className="px-3 py-2">Ticker</th>
                            <th className="px-3 py-2">Price</th>
                            <th className="px-3 py-2">Wk Hi</th>
                            <th className="px-3 py-2">Wk Lo</th>
                            <th className="px-3 py-2">Wk Pos%</th>
                            <th className="px-3 py-2">Weekly Zone</th>
                            <th className="px-3 py-2">Daily Zone</th>
                            <th className="px-3 py-2">Nearest Fib</th>
                            <th className="px-3 py-2">Compression</th>
                            {FIB_COLS.map((col) => (
                              <th
                                key={col}
                                className={`px-2 py-2 font-mono ${
                                  GOLDEN_COLS.has(col) ? "bg-[#1a1500] text-yellow-400 border-b-2 border-yellow-500" : ""
                                }`}
                              >
                                {col}
                              </th>
                            ))}
                            <th className="px-3 py-2">Covered Call</th>
                          </tr>
                        </thead>
                        <tbody className="divide-y divide-border">
                          {group.list.map((r: StockAnalysisResult) => (
                            <tr key={r.ticker} className="hover:bg-surface/30">
                              <td className="px-3 py-2 font-mono font-bold text-accent">
                                <Link href={`/stock/${r.ticker}`} className="hover:underline">
                                  {r.ticker}
                                </Link>
                              </td>
                              <td className="px-3 py-2 font-mono font-bold text-white">${r.price?.toFixed(2)}</td>
                              <td className="px-3 py-2 font-mono">${r.wk_hi?.toFixed(2)}</td>
                              <td className="px-3 py-2 font-mono">${r.wk_lo?.toFixed(2)}</td>
                              <td className="px-3 py-2 font-mono">{r.wk_pos?.toFixed(1)}%</td>
                              <td className="px-3 py-2">{getZoneBadge(r.weekly_zone)}</td>
                              <td className="px-3 py-2">{getZoneBadge(r.daily_zone)}</td>
                              <td className="px-3 py-2 font-mono text-accent font-bold">{r.nearest_fib}</td>
                              <td className="px-3 py-2 font-bold font-mono">
                                {r.fib_compression ? <span className="text-bear">Y</span> : <span className="text-muted">N</span>}
                              </td>
                              {FIB_COLS.map((col) => {
                                const fibVal = r.fib_levels?.[col];
                                const isNearest = r.nearest_fib?.startsWith(col);
                                const isGolden = GOLDEN_COLS.has(col);
                                return (
                                  <td
                                    key={col}
                                    className={`px-2 py-2 font-mono text-[11px] ${
                                      isNearest
                                        ? "bg-[#0a1f3a] text-white font-bold border-2 border-blue-500"
                                        : isGolden
                                        ? "bg-[#1a1500]/50 text-yellow-400/90 font-semibold"
                                        : "text-muted"
                                    }`}
                                  >
                                    {fibVal !== undefined ? `$${fibVal.toFixed(2)}` : "—"}
                                  </td>
                                );
                              })}
                              <td className="px-3 py-2 font-mono text-[11px] text-white max-w-xs truncate">{r.covered_call}</td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </div>
                  </div>
                )
            )}
          </div>

          {/* ── 7. Fundamentals Signal Checklist Matrix ── */}
          {reportData.matrix?.columns && reportData.matrix.columns.length > 0 && (
            <div className="bg-card border border-border rounded-xl p-5 space-y-4">
              <div className="flex items-center justify-between">
                <div>
                  <h2 className="text-lg font-bold text-white flex items-center gap-2">
                    <span>📊</span> Fundamentals Report
                  </h2>
                  <p className="text-xs text-muted">Signal matrix — each column is a fundamental flag, ✅ if ticker has it.</p>
                </div>
                <button
                  onClick={() => downloadCsv(`fundamentals_matrix_${asOfDate}.csv`, reportData.results)}
                  className="text-xs bg-surface border border-border hover:bg-surface/80 text-white font-semibold px-3 py-1 rounded transition-colors"
                >
                  📥 Download Matrix CSV
                </button>
              </div>

              <div className="overflow-x-auto border border-border rounded-lg">
                <table className="w-full text-left text-xs whitespace-nowrap">
                  <thead className="bg-surface text-muted uppercase text-[10px] border-b border-border">
                    <tr>
                      <th className="px-4 py-2.5">Ticker</th>
                      {reportData.matrix.columns.map((col: string) => (
                        <th key={col} className="px-3 py-2.5 text-center">
                          {col}
                        </th>
                      ))}
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-border">
                    {reportData.matrix.rows.map((row: any) => (
                      <tr key={row.ticker} className="hover:bg-surface/30">
                        <td className="px-4 py-2 font-mono font-bold text-accent">
                          <Link href={`/stock/${row.ticker}`} className="hover:underline">
                            {row.ticker}
                          </Link>
                        </td>
                        {reportData.matrix.columns.map((col: string) => {
                          const hasIt = row.has_flags?.[col];
                          return (
                            <td key={col} className="px-3 py-2 text-center font-mono">
                              {hasIt ? (
                                <span className="inline-block px-1.5 py-0.5 rounded text-xs bg-[#0a3d1f] text-bull font-bold">
                                  ✅
                                </span>
                              ) : (
                                <span className="text-muted/20">—</span>
                              )}
                            </td>
                          );
                        })}
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
