"use client";
import React, { useEffect, useState, useCallback } from "react";

const API_BASE = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

interface AlpacaAccount {
  status: string;
  mode: string;
  equity: number;
  buying_power: number;
  cash: number;
  last_equity: number;
  day_pl: number;
  currency: string;
  account_status: string;
  account_number: string;
  pattern_day_trader?: boolean;
  trading_blocked?: boolean;
  error?: string;
}

interface OverallStats {
  total_trades: number;
  closed: number;
  open_count: number;
  wins: number;
  losses: number;
  win_rate: number;
  total_pnl: number;
  avg_pnl_pct: number;
  best_trade: number;
  worst_trade: number;
}

interface PaperTrade {
  id: number;
  trade_date: string;
  ticker: string;
  direction: string;
  entry_price: number;
  stop_price: number;
  t1_price: number;
  t2_price: number;
  exit_price?: number;
  shares: number;
  status: string;
  outcome?: string;
  pnl_dollars?: number;
  pnl_pct?: number;
  scenario?: string;
  confidence?: string;
  entry_time?: string;
  exit_time?: string;
  exit_reason?: string;
  alpaca_order_id?: string;
}

interface DailySummary {
  trade_date: string;
  total_trades: number;
  wins: number;
  losses: number;
  open_trades: number;
  total_pnl: number;
  avg_pnl_pct: number;
}

export default function PaperTradingPage() {
  const [mode, setMode] = useState<"paper" | "live">("paper");
  const [account, setAccount] = useState<AlpacaAccount | null>(null);
  const [stats, setStats] = useState<OverallStats | null>(null);
  const [trades, setTrades] = useState<PaperTrade[]>([]);
  const [openTrades, setOpenTrades] = useState<PaperTrade[]>([]);
  const [daily, setDaily] = useState<DailySummary[]>([]);
  const [alpacaPositions, setAlpacaPositions] = useState<any[]>([]);
  const [alpacaOrders, setAlpacaOrders] = useState<any[]>([]);
  const [config, setConfig] = useState<any>(null);

  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [activeTab, setActiveTab] = useState<"open" | "all" | "daily" | "broker_positions" | "broker_orders" | "config">("open");
  const [closingId, setClosingId] = useState<number | null>(null);

  const loadData = useCallback(async (showSpinner = true) => {
    if (showSpinner) setRefreshing(true);
    try {
      // 1. Account info
      const acctRes = await fetch(`${API_BASE}/api/paper/account?mode=${mode}`);
      if (acctRes.ok) {
        const acctData = await acctRes.json();
        setAccount(acctData);
      }

      // 2. Broker positions & orders
      const [posRes, ordRes] = await Promise.all([
        fetch(`${API_BASE}/api/paper/alpaca-positions?mode=${mode}`),
        fetch(`${API_BASE}/api/paper/alpaca-orders?mode=${mode}&status=all&limit=20`),
      ]);
      if (posRes.ok) {
        const posData = await posRes.json();
        setAlpacaPositions(posData.items || []);
      }
      if (ordRes.ok) {
        const ordData = await ordRes.json();
        setAlpacaOrders(ordData.items || []);
      }

      // 3. Stats, Trades, Positions, Daily, Config
      const [statsRes, tradesRes, openRes, dailyRes, cfgRes] = await Promise.all([
        fetch(`${API_BASE}/api/paper/stats`),
        fetch(`${API_BASE}/api/paper/trades`),
        fetch(`${API_BASE}/api/paper/positions`),
        fetch(`${API_BASE}/api/paper/daily`),
        fetch(`${API_BASE}/api/paper/config`),
      ]);

      if (statsRes.ok) {
        const d = await statsRes.json();
        setStats(d.stats);
      }
      if (tradesRes.ok) {
        const d = await tradesRes.json();
        setTrades(d.items || []);
      }
      if (openRes.ok) {
        const d = await openRes.json();
        setOpenTrades(d.items || []);
      }
      if (dailyRes.ok) {
        const d = await dailyRes.json();
        setDaily(d.items || []);
      }
      if (cfgRes.ok) {
        const d = await cfgRes.json();
        setConfig(d);
      }
    } catch (err) {
      console.error("Error loading paper trading data:", err);
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  }, [mode]);

  useEffect(() => {
    loadData();
  }, [loadData]);

  const handleForceClose = async (trade: PaperTrade) => {
    const exitPxStr = window.prompt(
      `Force close trade #${trade.id} (${trade.ticker} ${trade.direction})?\nEnter exit price:`,
      trade.entry_price.toString()
    );
    if (!exitPxStr) return;
    const exitPrice = parseFloat(exitPxStr);
    if (isNaN(exitPrice)) {
      alert("Invalid price number");
      return;
    }

    setClosingId(trade.id);
    try {
      const res = await fetch(`${API_BASE}/api/paper/close`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          trade_id: trade.id,
          exit_price: exitPrice,
          reason: "MANUAL_UI_EXIT",
        }),
      });
      if (res.ok) {
        await loadData(false);
      } else {
        const err = await res.json();
        alert(`Failed to close: ${err.detail || "Unknown error"}`);
      }
    } catch (e: any) {
      alert(`Network error: ${e.message}`);
    } finally {
      setClosingId(null);
    }
  };

  const handleClearAll = async () => {
    if (!window.confirm("Are you sure you want to clear all simulated paper trades? This cannot be undone.")) return;
    try {
      const res = await fetch(`${API_BASE}/api/paper/clear`, { method: "POST" });
      if (res.ok) {
        await loadData(false);
      } else {
        alert("Failed to clear paper trades");
      }
    } catch (e: any) {
      alert(`Error: ${e.message}`);
    }
  };

  return (
    <div className="max-w-screen-2xl mx-auto px-4 py-8 space-y-6">
      {/* ── HEADER ──────────────────────────────────────────────────── */}
      <div className="flex flex-col md:flex-row md:items-center justify-between gap-4 pb-4 border-b border-border">
        <div>
          <div className="flex items-center gap-3">
            <h1 className="text-2xl font-bold text-white tracking-tight flex items-center gap-2">
              🏦 Alpaca Paper Trading Engine
            </h1>
            <span className={`text-[11px] font-bold px-2 py-0.5 rounded uppercase tracking-wider ${
              account?.account_status === "ACTIVE" ? "bg-bull/20 text-bull border border-bull/30" : "bg-bear/20 text-bear border border-bear/30"
            }`}>
              {account?.account_status || "DISCONNECTED"}
            </span>
          </div>
          <p className="text-muted text-sm mt-1">
            Simulated paper execution with automated bracket stops, profit targets, and live Alpaca broker sync.
          </p>
        </div>

        {/* Action buttons & mode switcher */}
        <div className="flex items-center gap-3">
          {/* Mode Switcher */}
          <div className="bg-surface border border-border rounded-lg p-1 flex items-center text-xs font-semibold">
            <button
              onClick={() => setMode("paper")}
              className={`px-3 py-1 rounded transition-colors ${
                mode === "paper" ? "bg-accent text-white shadow-sm" : "text-muted hover:text-white"
              }`}
            >
              📄 Paper API
            </button>
            <button
              onClick={() => setMode("live")}
              className={`px-3 py-1 rounded transition-colors ${
                mode === "live" ? "bg-bull text-background font-bold shadow-sm" : "text-muted hover:text-white"
              }`}
            >
              🟢 Live Broker API
            </button>
          </div>

          <button
            onClick={() => loadData(true)}
            disabled={refreshing}
            className="bg-surface hover:bg-surface/80 border border-border text-white text-xs font-semibold px-3 py-1.5 rounded-lg flex items-center gap-1.5 transition-colors disabled:opacity-50"
          >
            <span className={refreshing ? "animate-spin" : ""}>🔄</span> Refresh
          </button>

          <button
            onClick={handleClearAll}
            className="bg-bear/10 hover:bg-bear/20 text-bear border border-bear/30 text-xs font-semibold px-3 py-1.5 rounded-lg transition-colors"
          >
            🗑️ Clear Trades
          </button>
        </div>
      </div>

      {/* ── ALPACA BROKER ACCOUNT BALANCES ──────────────────────────── */}
      <div className="bg-card border border-border rounded-xl p-5 shadow-sm space-y-4">
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-2">
            <span className="text-base font-bold text-white">
              {mode === "paper" ? "🏦 Alpaca Paper Account Balances" : "🟢 Alpaca Live Brokerage Account Balances"}
            </span>
            {account?.account_number && (
              <span className="text-xs font-mono text-muted bg-surface px-2 py-0.5 rounded border border-border">
                Acct #{account.account_number}
              </span>
            )}
          </div>
          <span className="text-xs text-muted">
            API Target: <code className="text-white font-mono">{mode === "paper" ? "https://paper-api.alpaca.markets" : "https://api.alpaca.markets"}</code>
          </span>
        </div>

        {account?.error && (
          <div className="bg-bear/10 border border-bear/30 rounded-lg p-3 text-xs text-bear space-y-1">
            <div className="font-bold flex items-center gap-1.5">
              <span>⚠️ Alpaca Account Error:</span> {account.error}
            </div>
            <p className="text-muted">
              {mode === "paper"
                ? "The paper trading credentials in your .env file (ALPACA_PAPER_API_KEY) may need to be regenerated at alpaca.markets. You can also switch to 'Live Broker API' above to view your active Alpaca account."
                : "Live Alpaca API key returned unauthorized. Please check ALPACA_API_KEY and ALPACA_API_SECRET in your .env file."}
            </p>
          </div>
        )}

        <div className="grid grid-cols-2 sm:grid-cols-4 gap-4">
          <div className="bg-surface/50 border border-border/60 rounded-lg p-4">
            <div className="text-xs font-semibold text-muted uppercase tracking-wider">Equity</div>
            <div className="text-2xl font-bold font-mono text-white mt-1">
              ${account ? account.equity.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 }) : "0.00"}
            </div>
            <div className="text-[11px] text-muted mt-1">Total portfolio value</div>
          </div>

          <div className="bg-surface/50 border border-border/60 rounded-lg p-4">
            <div className="text-xs font-semibold text-muted uppercase tracking-wider">Buying Power</div>
            <div className="text-2xl font-bold font-mono text-accent mt-1">
              ${account ? account.buying_power.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 }) : "0.00"}
            </div>
            <div className="text-[11px] text-muted mt-1">Available trading margin</div>
          </div>

          <div className="bg-surface/50 border border-border/60 rounded-lg p-4">
            <div className="text-xs font-semibold text-muted uppercase tracking-wider">Day P&L</div>
            <div className={`text-2xl font-bold font-mono mt-1 ${
              (account?.day_pl || 0) >= 0 ? "text-bull" : "text-bear"
            }`}>
              {(account?.day_pl || 0) >= 0 ? "+" : ""}${account ? account.day_pl.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 }) : "0.00"}
            </div>
            <div className="text-[11px] text-muted mt-1">Today's unrealized shift</div>
          </div>

          <div className="bg-surface/50 border border-border/60 rounded-lg p-4">
            <div className="text-xs font-semibold text-muted uppercase tracking-wider">Cash</div>
            <div className="text-2xl font-bold font-mono text-white mt-1">
              ${account ? account.cash.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 }) : "0.00"}
            </div>
            <div className="text-[11px] text-muted mt-1">Settled currency balance</div>
          </div>
        </div>
      </div>

      {/* ── SIMULATED ENGINE OVERALL PERFORMANCE ────────────────────── */}
      <div className="bg-card border border-border rounded-xl p-5 shadow-sm space-y-3">
        <div className="text-sm font-bold text-white uppercase tracking-wider flex items-center justify-between">
          <span>📊 Paper Trading Engine Performance (SQLite)</span>
          <span className="text-xs font-normal text-muted">Tracking automated rules from paper_config.py</span>
        </div>

        <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-6 gap-3">
          <div className="bg-surface/40 border border-border/50 rounded-lg p-3">
            <div className="text-[11px] text-muted font-medium">Total P&L</div>
            <div className={`text-lg font-bold font-mono mt-0.5 ${
              (stats?.total_pnl || 0) >= 0 ? "text-bull" : "text-bear"
            }`}>
              {(stats?.total_pnl || 0) >= 0 ? "+" : ""}${stats ? stats.total_pnl.toFixed(2) : "0.00"}
            </div>
          </div>

          <div className="bg-surface/40 border border-border/50 rounded-lg p-3">
            <div className="text-[11px] text-muted font-medium">Win Rate</div>
            <div className="text-lg font-bold font-mono text-white mt-0.5">
              {stats ? stats.win_rate.toFixed(1) : "0.0"}%
            </div>
            <div className="text-[10px] text-muted">
              {stats?.wins || 0}W / {stats?.losses || 0}L
            </div>
          </div>

          <div className="bg-surface/40 border border-border/50 rounded-lg p-3">
            <div className="text-[11px] text-muted font-medium">Total Trades</div>
            <div className="text-lg font-bold font-mono text-white mt-0.5">
              {stats?.total_trades || 0}
            </div>
            <div className="text-[10px] text-accent font-semibold">
              {stats?.open_count || 0} open
            </div>
          </div>

          <div className="bg-surface/40 border border-border/50 rounded-lg p-3">
            <div className="text-[11px] text-muted font-medium">Best Trade</div>
            <div className="text-lg font-bold font-mono text-bull mt-0.5">
              {stats?.best_trade ? `+$${stats.best_trade.toFixed(2)}` : "—"}
            </div>
          </div>

          <div className="bg-surface/40 border border-border/50 rounded-lg p-3">
            <div className="text-[11px] text-muted font-medium">Worst Trade</div>
            <div className="text-lg font-bold font-mono text-bear mt-0.5">
              {stats?.worst_trade ? `$${stats.worst_trade.toFixed(2)}` : "—"}
            </div>
          </div>

          <div className="bg-surface/40 border border-border/50 rounded-lg p-3">
            <div className="text-[11px] text-muted font-medium">Avg P&L %</div>
            <div className={`text-lg font-bold font-mono mt-0.5 ${
              (stats?.avg_pnl_pct || 0) >= 0 ? "text-bull" : "text-bear"
            }`}>
              {(stats?.avg_pnl_pct || 0) >= 0 ? "+" : ""}{stats ? stats.avg_pnl_pct.toFixed(2) : "0.00"}%
            </div>
          </div>
        </div>
      </div>

      {/* ── SUB-TABS NAVIGATION ─────────────────────────────────────── */}
      <div className="flex items-center gap-2 border-b border-border overflow-x-auto pb-px">
        <button
          onClick={() => setActiveTab("open")}
          className={`px-4 py-2 text-xs font-semibold rounded-t-lg transition-colors border-b-2 flex items-center gap-1.5 ${
            activeTab === "open"
              ? "border-accent text-white bg-card"
              : "border-transparent text-muted hover:text-white"
          }`}
        >
          <span>🟢 Open Trades</span>
          <span className="bg-accent/20 text-accent px-1.5 py-0.2 rounded-full text-[10px]">
            {openTrades.length}
          </span>
        </button>

        <button
          onClick={() => setActiveTab("all")}
          className={`px-4 py-2 text-xs font-semibold rounded-t-lg transition-colors border-b-2 flex items-center gap-1.5 ${
            activeTab === "all"
              ? "border-accent text-white bg-card"
              : "border-transparent text-muted hover:text-white"
          }`}
        >
          <span>📋 Trade Log</span>
          <span className="bg-surface text-muted px-1.5 py-0.2 rounded-full text-[10px]">
            {trades.length}
          </span>
        </button>

        <button
          onClick={() => setActiveTab("daily")}
          className={`px-4 py-2 text-xs font-semibold rounded-t-lg transition-colors border-b-2 flex items-center gap-1.5 ${
            activeTab === "daily"
              ? "border-accent text-white bg-card"
              : "border-transparent text-muted hover:text-white"
          }`}
        >
          <span>📊 Daily Summary</span>
        </button>

        <button
          onClick={() => setActiveTab("broker_positions")}
          className={`px-4 py-2 text-xs font-semibold rounded-t-lg transition-colors border-b-2 flex items-center gap-1.5 ${
            activeTab === "broker_positions"
              ? "border-accent text-white bg-card"
              : "border-transparent text-muted hover:text-white"
          }`}
        >
          <span>🏦 Alpaca Open Positions</span>
          <span className="bg-surface text-muted px-1.5 py-0.2 rounded-full text-[10px]">
            {alpacaPositions.length}
          </span>
        </button>

        <button
          onClick={() => setActiveTab("broker_orders")}
          className={`px-4 py-2 text-xs font-semibold rounded-t-lg transition-colors border-b-2 flex items-center gap-1.5 ${
            activeTab === "broker_orders"
              ? "border-accent text-white bg-card"
              : "border-transparent text-muted hover:text-white"
          }`}
        >
          <span>📜 Alpaca Recent Orders</span>
          <span className="bg-surface text-muted px-1.5 py-0.2 rounded-full text-[10px]">
            {alpacaOrders.length}
          </span>
        </button>

        <button
          onClick={() => setActiveTab("config")}
          className={`px-4 py-2 text-xs font-semibold rounded-t-lg transition-colors border-b-2 flex items-center gap-1.5 ${
            activeTab === "config"
              ? "border-accent text-white bg-card"
              : "border-transparent text-muted hover:text-white"
          }`}
        >
          <span>⚙️ Config Settings</span>
        </button>
      </div>

      {/* ── TAB CONTENT ─────────────────────────────────────────────── */}
      {loading ? (
        <div className="text-center py-20 text-muted">Loading paper trading details...</div>
      ) : (
        <div className="space-y-4">
          {/* 1. OPEN TRADES */}
          {activeTab === "open" && (
            <div className="bg-card border border-border rounded-xl overflow-hidden">
              <div className="overflow-x-auto">
                <table className="w-full text-left text-sm">
                  <thead className="bg-surface text-muted text-xs uppercase border-b border-border">
                    <tr>
                      <th className="px-4 py-3">ID</th>
                      <th className="px-4 py-3">Date</th>
                      <th className="px-4 py-3">Ticker</th>
                      <th className="px-4 py-3">Direction</th>
                      <th className="px-4 py-3">Entry $</th>
                      <th className="px-4 py-3">Stop Loss</th>
                      <th className="px-4 py-3">Target 1</th>
                      <th className="px-4 py-3">Target 2</th>
                      <th className="px-4 py-3">Shares</th>
                      <th className="px-4 py-3">Scenario</th>
                      <th className="px-4 py-3 text-right">Actions</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-border">
                    {openTrades.length === 0 ? (
                      <tr>
                        <td colSpan={11} className="text-center py-12 text-muted">
                          No open simulated paper trades. Route trades from the Intraday Planning tab or replay runner.
                        </td>
                      </tr>
                    ) : (
                      openTrades.map((t) => (
                        <tr key={t.id} className="hover:bg-surface/50">
                          <td className="px-4 py-3 font-mono text-muted text-xs">#{t.id}</td>
                          <td className="px-4 py-3 font-mono text-xs">{t.trade_date}</td>
                          <td className="px-4 py-3 font-mono font-bold text-accent">{t.ticker}</td>
                          <td className="px-4 py-3">
                            <span className={`px-2 py-0.5 rounded text-xs font-bold ${
                              t.direction === "LONG" || t.direction === "BULLISH"
                                ? "bg-bull/20 text-bull border border-bull/30"
                                : "bg-bear/20 text-bear border border-bear/30"
                            }`}>
                              {t.direction}
                            </span>
                          </td>
                          <td className="px-4 py-3 font-mono text-white">${t.entry_price.toFixed(2)}</td>
                          <td className="px-4 py-3 font-mono text-bear">${t.stop_price.toFixed(2)}</td>
                          <td className="px-4 py-3 font-mono text-bull">${t.t1_price.toFixed(2)}</td>
                          <td className="px-4 py-3 font-mono text-bull">${t.t2_price.toFixed(2)}</td>
                          <td className="px-4 py-3 font-mono text-white">{t.shares}</td>
                          <td className="px-4 py-3 text-xs text-muted">{t.scenario || "—"}</td>
                          <td className="px-4 py-3 text-right">
                            <button
                              onClick={() => handleForceClose(t)}
                              disabled={closingId === t.id}
                              className="bg-bear/10 hover:bg-bear/20 text-bear border border-bear/30 text-xs font-semibold px-2.5 py-1 rounded transition-colors disabled:opacity-50"
                            >
                              {closingId === t.id ? "Closing..." : "⚡ Force Close"}
                            </button>
                          </td>
                        </tr>
                      ))
                    )}
                  </tbody>
                </table>
              </div>
            </div>
          )}

          {/* 2. ALL TRADES / TRADE LOG */}
          {activeTab === "all" && (
            <div className="bg-card border border-border rounded-xl overflow-hidden">
              <div className="overflow-x-auto">
                <table className="w-full text-left text-sm">
                  <thead className="bg-surface text-muted text-xs uppercase border-b border-border">
                    <tr>
                      <th className="px-4 py-3">ID</th>
                      <th className="px-4 py-3">Date</th>
                      <th className="px-4 py-3">Ticker</th>
                      <th className="px-4 py-3">Direction</th>
                      <th className="px-4 py-3">Entry $</th>
                      <th className="px-4 py-3">Exit $</th>
                      <th className="px-4 py-3">Shares</th>
                      <th className="px-4 py-3">P&L ($)</th>
                      <th className="px-4 py-3">P&L (%)</th>
                      <th className="px-4 py-3">Outcome</th>
                      <th className="px-4 py-3">Exit Reason</th>
                      <th className="px-4 py-3">Alpaca ID</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-border">
                    {trades.length === 0 ? (
                      <tr>
                        <td colSpan={12} className="text-center py-12 text-muted">
                          No paper trade history recorded yet.
                        </td>
                      </tr>
                    ) : (
                      trades.map((t) => {
                        const isWin = t.outcome === "WIN";
                        const isLoss = t.outcome === "LOSS";
                        return (
                          <tr key={t.id} className="hover:bg-surface/50">
                            <td className="px-4 py-3 font-mono text-muted text-xs">#{t.id}</td>
                            <td className="px-4 py-3 font-mono text-xs">{t.trade_date}</td>
                            <td className="px-4 py-3 font-mono font-bold text-accent">{t.ticker}</td>
                            <td className="px-4 py-3">
                              <span className={`px-2 py-0.5 rounded text-xs font-bold ${
                                t.direction === "LONG" || t.direction === "BULLISH"
                                  ? "bg-bull/20 text-bull"
                                  : "bg-bear/20 text-bear"
                              }`}>
                                {t.direction}
                              </span>
                            </td>
                            <td className="px-4 py-3 font-mono text-white">${t.entry_price.toFixed(2)}</td>
                            <td className="px-4 py-3 font-mono text-white">
                              {t.exit_price ? `$${t.exit_price.toFixed(2)}` : "—"}
                            </td>
                            <td className="px-4 py-3 font-mono text-white">{t.shares}</td>
                            <td className="px-4 py-3 font-mono font-bold">
                              {t.pnl_dollars !== null && t.pnl_dollars !== undefined ? (
                                <span className={t.pnl_dollars >= 0 ? "text-bull" : "text-bear"}>
                                  {t.pnl_dollars >= 0 ? "+" : ""}${t.pnl_dollars.toFixed(2)}
                                </span>
                              ) : (
                                "—"
                              )}
                            </td>
                            <td className="px-4 py-3 font-mono">
                              {t.pnl_pct !== null && t.pnl_pct !== undefined ? (
                                <span className={t.pnl_pct >= 0 ? "text-bull" : "text-bear"}>
                                  {t.pnl_pct >= 0 ? "+" : ""}{t.pnl_pct.toFixed(2)}%
                                </span>
                              ) : (
                                "—"
                              )}
                            </td>
                            <td className="px-4 py-3">
                              <span className={`px-2 py-0.5 rounded text-xs font-bold ${
                                isWin ? "bg-bull/20 text-bull" : isLoss ? "bg-bear/20 text-bear" : "bg-surface text-muted"
                              }`}>
                                {isWin ? "🟢 WIN" : isLoss ? "🔴 LOSS" : t.status}
                              </span>
                            </td>
                            <td className="px-4 py-3 text-xs text-muted font-mono">{t.exit_reason || "—"}</td>
                            <td className="px-4 py-3 text-xs text-muted font-mono truncate max-w-[120px]">
                              {t.alpaca_order_id ? t.alpaca_order_id.slice(0, 8) + "..." : "—"}
                            </td>
                          </tr>
                        );
                      })
                    )}
                  </tbody>
                </table>
              </div>
            </div>
          )}

          {/* 3. DAILY SUMMARY */}
          {activeTab === "daily" && (
            <div className="bg-card border border-border rounded-xl overflow-hidden">
              <div className="overflow-x-auto">
                <table className="w-full text-left text-sm">
                  <thead className="bg-surface text-muted text-xs uppercase border-b border-border">
                    <tr>
                      <th className="px-4 py-3">Date</th>
                      <th className="px-4 py-3">Total Trades</th>
                      <th className="px-4 py-3">Wins</th>
                      <th className="px-4 py-3">Losses</th>
                      <th className="px-4 py-3">Win %</th>
                      <th className="px-4 py-3">Open</th>
                      <th className="px-4 py-3">Total P&L ($)</th>
                      <th className="px-4 py-3">Avg P&L (%)</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-border">
                    {daily.length === 0 ? (
                      <tr>
                        <td colSpan={8} className="text-center py-12 text-muted">
                          No daily aggregated summary data yet.
                        </td>
                      </tr>
                    ) : (
                      daily.map((d) => {
                        const closed = d.wins + d.losses;
                        const winRate = closed > 0 ? (d.wins / closed) * 100 : 0;
                        return (
                          <tr key={d.trade_date} className="hover:bg-surface/50">
                            <td className="px-4 py-3 font-mono font-semibold text-white">{d.trade_date}</td>
                            <td className="px-4 py-3 font-mono">{d.total_trades}</td>
                            <td className="px-4 py-3 font-mono text-bull">{d.wins}</td>
                            <td className="px-4 py-3 font-mono text-bear">{d.losses}</td>
                            <td className="px-4 py-3 font-mono text-white">{winRate.toFixed(1)}%</td>
                            <td className="px-4 py-3 font-mono text-accent">{d.open_trades}</td>
                            <td className={`px-4 py-3 font-mono font-bold ${d.total_pnl >= 0 ? "text-bull" : "text-bear"}`}>
                              {d.total_pnl >= 0 ? "+" : ""}${d.total_pnl.toFixed(2)}
                            </td>
                            <td className={`px-4 py-3 font-mono ${d.avg_pnl_pct >= 0 ? "text-bull" : "text-bear"}`}>
                              {d.avg_pnl_pct >= 0 ? "+" : ""}{d.avg_pnl_pct ? d.avg_pnl_pct.toFixed(2) : "0.00"}%
                            </td>
                          </tr>
                        );
                      })
                    )}
                  </tbody>
                </table>
              </div>
            </div>
          )}

          {/* 4. ALPACA BROKER OPEN POSITIONS */}
          {activeTab === "broker_positions" && (
            <div className="bg-card border border-border rounded-xl overflow-hidden">
              <div className="overflow-x-auto">
                <table className="w-full text-left text-sm">
                  <thead className="bg-surface text-muted text-xs uppercase border-b border-border">
                    <tr>
                      <th className="px-4 py-3">Symbol</th>
                      <th className="px-4 py-3">Side</th>
                      <th className="px-4 py-3">Qty</th>
                      <th className="px-4 py-3">Avg Entry</th>
                      <th className="px-4 py-3">Current Price</th>
                      <th className="px-4 py-3">Unrealized P&L</th>
                      <th className="px-4 py-3">P&L %</th>
                      <th className="px-4 py-3">Market Value</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-border">
                    {alpacaPositions.length === 0 ? (
                      <tr>
                        <td colSpan={8} className="text-center py-12 text-muted">
                          No open positions found on the Alpaca broker account.
                        </td>
                      </tr>
                    ) : (
                      alpacaPositions.map((p, idx) => {
                        const pl = parseFloat(p.unrealized_pl || 0);
                        const plpc = parseFloat(p.unrealized_plpc || 0) * 100;
                        return (
                          <tr key={idx} className="hover:bg-surface/50">
                            <td className="px-4 py-3 font-mono font-bold text-accent">{p.symbol}</td>
                            <td className="px-4 py-3">
                              <span className={`px-2 py-0.5 rounded text-xs font-bold ${
                                p.side?.toLowerCase() === "long" ? "bg-bull/20 text-bull" : "bg-bear/20 text-bear"
                              }`}>
                                {p.side?.toUpperCase()}
                              </span>
                            </td>
                            <td className="px-4 py-3 font-mono text-white">{p.qty}</td>
                            <td className="px-4 py-3 font-mono text-white">${parseFloat(p.avg_entry_price || 0).toFixed(2)}</td>
                            <td className="px-4 py-3 font-mono text-white">${parseFloat(p.current_price || 0).toFixed(2)}</td>
                            <td className={`px-4 py-3 font-mono font-bold ${pl >= 0 ? "text-bull" : "text-bear"}`}>
                              {pl >= 0 ? "+" : ""}${pl.toFixed(2)}
                            </td>
                            <td className={`px-4 py-3 font-mono font-bold ${plpc >= 0 ? "text-bull" : "text-bear"}`}>
                              {plpc >= 0 ? "+" : ""}{plpc.toFixed(2)}%
                            </td>
                            <td className="px-4 py-3 font-mono text-white">${parseFloat(p.market_value || 0).toFixed(2)}</td>
                          </tr>
                        );
                      })
                    )}
                  </tbody>
                </table>
              </div>
            </div>
          )}

          {/* 5. ALPACA BROKER RECENT ORDERS */}
          {activeTab === "broker_orders" && (
            <div className="bg-card border border-border rounded-xl overflow-hidden">
              <div className="overflow-x-auto">
                <table className="w-full text-left text-sm">
                  <thead className="bg-surface text-muted text-xs uppercase border-b border-border">
                    <tr>
                      <th className="px-4 py-3">Status</th>
                      <th className="px-4 py-3">Symbol</th>
                      <th className="px-4 py-3">Side</th>
                      <th className="px-4 py-3">Qty</th>
                      <th className="px-4 py-3">Filled</th>
                      <th className="px-4 py-3">Type</th>
                      <th className="px-4 py-3">Status Name</th>
                      <th className="px-4 py-3">Submitted At</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-border">
                    {alpacaOrders.length === 0 ? (
                      <tr>
                        <td colSpan={8} className="text-center py-12 text-muted">
                          No recent broker orders found.
                        </td>
                      </tr>
                    ) : (
                      alpacaOrders.map((o, idx) => {
                        const st = (o.status || "").toUpperCase();
                        const isFilled = st === "FILLED";
                        const isPending = ["NEW", "ACCEPTED", "PARTIALLY_FILLED"].includes(st);
                        return (
                          <tr key={idx} className="hover:bg-surface/50">
                            <td className="px-4 py-3 text-base">
                              {isFilled ? "✅" : isPending ? "⏳" : "❌"}
                            </td>
                            <td className="px-4 py-3 font-mono font-bold text-accent">{o.symbol}</td>
                            <td className="px-4 py-3">
                              <span className={`px-2 py-0.5 rounded text-xs font-bold ${
                                o.side?.toLowerCase() === "buy" ? "bg-bull/20 text-bull" : "bg-bear/20 text-bear"
                              }`}>
                                {o.side?.toUpperCase()}
                              </span>
                            </td>
                            <td className="px-4 py-3 font-mono text-white">{o.qty}</td>
                            <td className="px-4 py-3 font-mono text-white">{o.filled_qty || 0}</td>
                            <td className="px-4 py-3 text-xs text-muted uppercase">{o.type}</td>
                            <td className="px-4 py-3">
                              <span className={`px-2 py-0.5 rounded text-xs font-semibold ${
                                isFilled ? "text-bull bg-bull/10" : isPending ? "text-accent bg-accent/10" : "text-bear bg-bear/10"
                              }`}>
                                {st}
                              </span>
                            </td>
                            <td className="px-4 py-3 font-mono text-xs text-muted">
                              {o.submitted_at ? new Date(o.submitted_at).toLocaleString() : "—"}
                            </td>
                          </tr>
                        );
                      })
                    )}
                  </tbody>
                </table>
              </div>
            </div>
          )}

          {/* 6. CONFIG SETTINGS */}
          {activeTab === "config" && config && (
            <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
              <div className="bg-card border border-border rounded-xl p-5 space-y-3">
                <h3 className="text-sm font-bold text-white uppercase tracking-wider">
                  📥 Entry Rules & Sizing (paper_config.py)
                </h3>
                <div className="divide-y divide-border/60 text-xs">
                  <div className="py-2 flex justify-between">
                    <span className="text-muted">Position Size ($)</span>
                    <span className="font-mono font-bold text-white">${config.position_size}</span>
                  </div>
                  <div className="py-2 flex justify-between">
                    <span className="text-muted">Min Confidence</span>
                    <span className="font-mono text-white">{config.min_confidence || "ANY"}</span>
                  </div>
                  <div className="py-2 flex justify-between">
                    <span className="text-muted">Min RR (T1)</span>
                    <span className="font-mono text-white">{config.min_rr_t1 || "—"}</span>
                  </div>
                  <div className="py-2 flex justify-between">
                    <span className="text-muted">Min Best RR</span>
                    <span className="font-mono text-white">{config.min_best_rr || "—"}</span>
                  </div>
                  <div className="py-2 flex justify-between">
                    <span className="text-muted">Pullback Entry Allowed</span>
                    <span className="font-mono text-white">{config.entry_on_pullback ? "YES" : "NO"}</span>
                  </div>
                  <div className="py-2 flex justify-between">
                    <span className="text-muted">Pullback Max Dist %</span>
                    <span className="font-mono text-white">{config.pullback_max_dist_pct}%</span>
                  </div>
                </div>
              </div>

              <div className="bg-card border border-border rounded-xl p-5 space-y-3">
                <h3 className="text-sm font-bold text-white uppercase tracking-wider">
                  📤 Exit Conditions & Broker Settings
                </h3>
                <div className="divide-y divide-border/60 text-xs">
                  <div className="py-2 flex justify-between">
                    <span className="text-muted">Exit on Stop Loss</span>
                    <span className="font-mono text-bear font-bold">{config.exit_on_stop ? "YES" : "NO"}</span>
                  </div>
                  <div className="py-2 flex justify-between">
                    <span className="text-muted">Exit on Target 1</span>
                    <span className="font-mono text-white">{config.exit_on_t1 ? "YES" : "NO"}</span>
                  </div>
                  <div className="py-2 flex justify-between">
                    <span className="text-muted">Exit on Target 2</span>
                    <span className="font-mono text-white">{config.exit_on_t2 ? "YES" : "NO"}</span>
                  </div>
                  <div className="py-2 flex justify-between">
                    <span className="text-muted">Partial Exit at T1</span>
                    <span className="font-mono text-white">{config.partial_exit_at_t1 ? "YES" : "NO"}</span>
                  </div>
                  <div className="py-2 flex justify-between">
                    <span className="text-muted">Trail Stop After T1</span>
                    <span className="font-mono text-white">{config.trail_stop_after_t1 ? "YES" : "NO"}</span>
                  </div>
                  <div className="py-2 flex justify-between">
                    <span className="text-muted">Session End Exit (CST)</span>
                    <span className="font-mono text-accent">{config.exit_at_session_end ? config.session_end_time_cst : "NO"}</span>
                  </div>
                  <div className="py-2 flex justify-between">
                    <span className="text-muted">Submit to Alpaca</span>
                    <span className="font-mono text-white font-bold">{config.submit_to_alpaca ? "YES" : "NO"}</span>
                  </div>
                  <div className="py-2 flex justify-between">
                    <span className="text-muted">Order Type / TIF</span>
                    <span className="font-mono text-white uppercase">{config.order_type} / {config.time_in_force}</span>
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
