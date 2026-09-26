"use client";
import React, { useEffect, useState, useCallback } from "react";
import { fmtNum, fmtPrice } from "@/lib/format";

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

interface CloseModalState {
  isOpen: boolean;
  symbol: string;
  tradeId?: number;
  shares: number;
  avgEntry: number;
  currentPrice: number;
  side: string;
  orderType: "oco" | "stop" | "limit" | "market";
  limitPrice: string;
  stopPrice: string;
  qtyToClose: string;
  timeInForce: "gtc" | "day";
  source: "broker" | "trade";
  initialStop?: number | null;
  initialT1?: number | null;
  initialT2?: number | null;
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
  const [syncingOrders, setSyncingOrders] = useState(false);
  const [activeTab, setActiveTab] = useState<"open" | "all" | "daily" | "broker_positions" | "broker_orders" | "config">("open");
  const [closingId, setClosingId] = useState<number | null>(null);
  const [closingSymbol, setClosingSymbol] = useState<string | null>(null);
  const [closeModal, setCloseModal] = useState<CloseModalState | null>(null);
  const [submittingClose, setSubmittingClose] = useState(false);
  const [cancelingOrderId, setCancelingOrderId] = useState<string | null>(null);
  const [cancelingAllOrders, setCancelingAllOrders] = useState(false);

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

  const handleSyncOrders = async () => {
    setSyncingOrders(true);
    try {
      const res = await fetch(`${API_BASE}/api/paper/sync-orders?mode=${mode}`, { method: "POST" });
      const data = await res.json();
      if (res.ok && data.status === "ok") {
        const count = data.count ?? 0;
        const already = data.already_protected || [];
        if (count > 0) {
          const listStr = data.attached?.map((a: any) => a.ticker).join(", ");
          alert(`✅ Successfully attached GTC exit orders for ${count} position(s): ${listStr}`);
        } else if (already.length > 0) {
          alert(`🛡️ All ${already.length} open position(s) (${already.join(", ")}) already have active exit orders sitting on Alpaca's order book.`);
        } else if (data.total_positions === 0) {
          alert(`ℹ️ No open positions found in your Alpaca (${mode}) account.`);
        } else {
          alert(data.message || "All open positions are already protected.");
        }
        await loadData(false);
      } else {
        const errorMsg = data.detail || data.error || "Unknown error";
        alert(`⚠️ Alpaca Sync Failed:\n${errorMsg}\n\nPlease check your ${mode === "paper" ? "ALPACA_PAPER_API_KEY" : "ALPACA_API_KEY"} in .env.`);
      }
    } catch (e: any) {
      alert(`Error: ${e.message}`);
    } finally {
      setSyncingOrders(false);
    }
  };

  const openCloseModalForBroker = (pos: any) => {
    const curPx = parseFloat(pos.current_price || pos.avg_entry_price || 0);
    const avgEntry = parseFloat(pos.avg_entry_price || 0);
    const sharesCount = Math.abs(parseInt(pos.qty || 0));
    const isShort = pos.side?.toLowerCase() === "short";

    const initialStop = pos.stop_price ? parseFloat(pos.stop_price) : null;
    const initialT1 = pos.t1_price ? parseFloat(pos.t1_price) : null;
    const initialT2 = pos.t2_price ? parseFloat(pos.t2_price) : null;

    const basePx = curPx > 0 ? curPx : avgEntry;
    let defaultStopPx = initialStop || (isShort ? basePx * 1.02 : basePx * 0.98);
    let defaultLimitPx = initialT1 || (isShort ? basePx * 0.98 : basePx * 1.02);

    setCloseModal({
      isOpen: true,
      symbol: pos.symbol,
      shares: sharesCount,
      avgEntry: avgEntry,
      currentPrice: curPx,
      side: isShort ? "SHORT" : "LONG",
      orderType: "oco",
      limitPrice: defaultLimitPx > 0 ? defaultLimitPx.toFixed(2) : "",
      stopPrice: defaultStopPx > 0 ? defaultStopPx.toFixed(2) : "",
      qtyToClose: sharesCount.toString(),
      timeInForce: "gtc",
      source: "broker",
      initialStop,
      initialT1,
      initialT2,
    });
  };

  const openCloseModalForTrade = (t: PaperTrade) => {
    const isShort = t.direction === "SHORT";
    const initialStop = t.stop_price ? Number(t.stop_price) : null;
    const initialT1 = t.t1_price ? Number(t.t1_price) : null;
    const initialT2 = t.t2_price ? Number(t.t2_price) : null;

    let defaultStopPx = initialStop || (isShort ? t.entry_price * 1.02 : t.entry_price * 0.98);
    let defaultLimitPx = initialT1 || (isShort ? t.entry_price * 0.98 : t.entry_price * 1.02);

    setCloseModal({
      isOpen: true,
      symbol: t.ticker,
      tradeId: t.id,
      shares: t.shares,
      avgEntry: t.entry_price,
      currentPrice: t.entry_price,
      side: t.direction,
      orderType: "oco",
      limitPrice: defaultLimitPx > 0 ? defaultLimitPx.toFixed(2) : "",
      stopPrice: defaultStopPx > 0 ? defaultStopPx.toFixed(2) : "",
      qtyToClose: t.shares.toString(),
      timeInForce: "gtc",
      source: "trade",
      initialStop,
      initialT1,
      initialT2,
    });
  };

  const handleSubmitClose = async () => {
    if (!closeModal) return;
    const { symbol, tradeId, shares, orderType, limitPrice, stopPrice, qtyToClose, timeInForce, source } = closeModal;
    const parsedQty = parseInt(qtyToClose);
    if (isNaN(parsedQty) || parsedQty <= 0) {
      alert("Please enter a valid share quantity to close.");
      return;
    }
    if (parsedQty > shares) {
      alert(`Cannot close more than open position size (${shares} shares).`);
      return;
    }

    let parsedLimitPx: number | null = null;
    if (orderType === "limit" || orderType === "oco") {
      parsedLimitPx = parseFloat(limitPrice);
      if (isNaN(parsedLimitPx) || parsedLimitPx <= 0) {
        alert("Please enter a valid positive target/limit price.");
        return;
      }
    }

    let parsedStopPx: number | null = null;
    if (orderType === "stop" || orderType === "oco") {
      parsedStopPx = parseFloat(stopPrice);
      if (isNaN(parsedStopPx) || parsedStopPx <= 0) {
        alert("Please enter a valid positive stop loss price.");
        return;
      }
    }

    setSubmittingClose(true);
    try {
      if (source === "broker") {
        const res = await fetch(`${API_BASE}/api/paper/alpaca-positions/close`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            symbol,
            mode,
            order_type: orderType,
            limit_price: parsedLimitPx,
            stop_price: parsedStopPx,
            qty: parsedQty,
            time_in_force: timeInForce,
          }),
        });
        const data = await res.json();
        if (res.ok && data.status === "ok") {
          alert(`✅ ${data.message || "Order submitted successfully!"}`);
          setCloseModal(null);
          await loadData(false);
        } else {
          alert(`⚠️ Failed to submit order: ${data.detail || data.error || "Unknown error"}`);
        }
      } else {
        let exitPx = closeModal.currentPrice || closeModal.avgEntry;
        let reason = "MANUAL_UI_EXIT";
        if (orderType === "market") {
          reason = "MANUAL_MARKET_EXIT";
        } else if (orderType === "limit" && parsedLimitPx) {
          exitPx = parsedLimitPx;
          reason = `MANUAL_LIMIT_${parsedLimitPx}`;
        } else if (orderType === "stop" && parsedStopPx) {
          exitPx = parsedStopPx;
          reason = `MANUAL_STOP_${parsedStopPx}`;
        } else if (orderType === "oco" && parsedLimitPx) {
          exitPx = parsedLimitPx;
          reason = `MANUAL_OCO_TARGET_${parsedLimitPx}_STOP_${parsedStopPx}`;
        }

        const res = await fetch(`${API_BASE}/api/paper/close`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            trade_id: tradeId,
            exit_price: exitPx,
            reason,
          }),
        });
        if (res.ok) {
          alert(`✅ Trade #${tradeId} (${symbol}) exit recorded at $${exitPx.toFixed(2)} (${reason})!`);
          setCloseModal(null);
          await loadData(false);
        } else {
          const err = await res.json();
          alert(`⚠️ Failed to close trade: ${err.detail || "Unknown error"}`);
        }
      }
    } catch (e: any) {
      alert(`Network error: ${e.message}`);
    } finally {
      setSubmittingClose(false);
    }
  };

  const handleCancelOrder = async (orderId: string, symbol: string) => {
    if (!window.confirm(`Cancel order for ${symbol}?`)) return;
    setCancelingOrderId(orderId);
    try {
      const res = await fetch(`${API_BASE}/api/paper/alpaca-orders/cancel`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ order_id: orderId, mode }),
      });
      const data = await res.json();
      if (res.ok && data.status === "ok") {
        await loadData(false);
      } else {
        alert(`Failed to cancel order: ${data.detail || data.error || "Unknown error"}`);
      }
    } catch (e: any) {
      alert(`Network error: ${e.message}`);
    } finally {
      setCancelingOrderId(null);
    }
  };

  const handleCancelAllOrders = async () => {
    if (!window.confirm(`Are you sure you want to cancel ALL open orders on your Alpaca (${mode}) account?`)) return;
    setCancelingAllOrders(true);
    try {
      const res = await fetch(`${API_BASE}/api/paper/alpaca-orders/cancel-all?mode=${mode}`, {
        method: "POST",
      });
      const data = await res.json();
      if (res.ok && data.status === "ok") {
        alert("✅ All open orders cancelled successfully.");
        await loadData(false);
      } else {
        alert(`Failed to cancel orders: ${data.detail || data.error || "Unknown error"}`);
      }
    } catch (e: any) {
      alert(`Network error: ${e.message}`);
    } finally {
      setCancelingAllOrders(false);
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
            onClick={handleSyncOrders}
            disabled={syncingOrders}
            className="bg-accent/15 hover:bg-accent/25 text-accent border border-accent/40 text-xs font-semibold px-3 py-1.5 rounded-lg flex items-center gap-1.5 transition-colors disabled:opacity-50"
            title="Re-attach Good 'Til Cancelled (GTC) Stop Loss & Take Profit orders to Alpaca open positions whose daily orders expired"
          >
            <span className={syncingOrders ? "animate-spin" : ""}>🛡️</span> Re-attach GTC Orders
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
              ${fmtNum(account?.equity, 2, "0.00")}
            </div>
            <div className="text-[11px] text-muted mt-1">Total portfolio value</div>
          </div>

          <div className="bg-surface/50 border border-border/60 rounded-lg p-4">
            <div className="text-xs font-semibold text-muted uppercase tracking-wider">Buying Power</div>
            <div className="text-2xl font-bold font-mono text-accent mt-1">
              ${fmtNum(account?.buying_power, 2, "0.00")}
            </div>
            <div className="text-[11px] text-muted mt-1">Available trading margin</div>
          </div>

          <div className="bg-surface/50 border border-border/60 rounded-lg p-4">
            <div className="text-xs font-semibold text-muted uppercase tracking-wider">Day P&L</div>
            <div className={`text-2xl font-bold font-mono mt-1 ${
              (Number(account?.day_pl) || 0) >= 0 ? "text-bull" : "text-bear"
            }`}>
              {(Number(account?.day_pl) || 0) >= 0 ? "+" : ""}${fmtNum(account?.day_pl, 2, "0.00")}
            </div>
            <div className="text-[11px] text-muted mt-1">Today's unrealized shift</div>
          </div>

          <div className="bg-surface/50 border border-border/60 rounded-lg p-4">
            <div className="text-xs font-semibold text-muted uppercase tracking-wider">Cash</div>
            <div className="text-2xl font-bold font-mono text-white mt-1">
              ${fmtNum(account?.cash, 2, "0.00")}
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
              (Number(stats?.total_pnl) || 0) >= 0 ? "text-bull" : "text-bear"
            }`}>
              {(Number(stats?.total_pnl) || 0) >= 0 ? "+" : ""}${fmtNum(stats?.total_pnl, 2, "0.00")}
            </div>
          </div>

          <div className="bg-surface/40 border border-border/50 rounded-lg p-3">
            <div className="text-[11px] text-muted font-medium">Win Rate</div>
            <div className="text-lg font-bold font-mono text-white mt-0.5">
              {fmtNum(stats?.win_rate, 1, "0.0")}%
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
              {stats?.best_trade != null ? `+$${fmtNum(stats.best_trade, 2)}` : "—"}
            </div>
          </div>

          <div className="bg-surface/40 border border-border/50 rounded-lg p-3">
            <div className="text-[11px] text-muted font-medium">Worst Trade</div>
            <div className="text-lg font-bold font-mono text-bear mt-0.5">
              {stats?.worst_trade != null ? `$${fmtNum(stats.worst_trade, 2)}` : "—"}
            </div>
          </div>

          <div className="bg-surface/40 border border-border/50 rounded-lg p-3">
            <div className="text-[11px] text-muted font-medium">Avg P&L %</div>
            <div className={`text-lg font-bold font-mono mt-0.5 ${
              (Number(stats?.avg_pnl_pct) || 0) >= 0 ? "text-bull" : "text-bear"
            }`}>
              {(Number(stats?.avg_pnl_pct) || 0) >= 0 ? "+" : ""}{fmtNum(stats?.avg_pnl_pct, 2, "0.00")}%
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
                          <td className="px-4 py-3 font-mono text-white">{fmtPrice(t.entry_price)}</td>
                          <td className="px-4 py-3 font-mono text-bear">{fmtPrice(t.stop_price)}</td>
                          <td className="px-4 py-3 font-mono text-bull">{fmtPrice(t.t1_price)}</td>
                          <td className="px-4 py-3 font-mono text-bull">{fmtPrice(t.t2_price)}</td>
                          <td className="px-4 py-3 font-mono text-white">{t.shares}</td>
                          <td className="px-4 py-3 text-xs text-muted">{t.scenario || "—"}</td>
                          <td className="px-4 py-3 text-right">
                            <button
                              onClick={() => openCloseModalForTrade(t)}
                              className="bg-bear/10 hover:bg-bear/20 text-bear border border-bear/30 text-xs font-semibold px-2.5 py-1 rounded transition-colors inline-flex items-center gap-1"
                              title={`Close or set limit exit price for #${t.id} (${t.ticker})`}
                            >
                              <span>⚡</span> Close / Exit...
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
                            <td className="px-4 py-3 font-mono text-white">{fmtPrice(t.entry_price)}</td>
                            <td className="px-4 py-3 font-mono text-white">
                              {fmtPrice(t.exit_price)}
                            </td>
                            <td className="px-4 py-3 font-mono text-white">{t.shares}</td>
                            <td className="px-4 py-3 font-mono font-bold">
                              {t.pnl_dollars !== null && t.pnl_dollars !== undefined ? (
                                <span className={Number(t.pnl_dollars) >= 0 ? "text-bull" : "text-bear"}>
                                  {Number(t.pnl_dollars) >= 0 ? "+" : ""}${fmtNum(t.pnl_dollars, 2)}
                                </span>
                              ) : (
                                "—"
                              )}
                            </td>
                            <td className="px-4 py-3 font-mono">
                              {t.pnl_pct !== null && t.pnl_pct !== undefined ? (
                                <span className={Number(t.pnl_pct) >= 0 ? "text-bull" : "text-bear"}>
                                  {Number(t.pnl_pct) >= 0 ? "+" : ""}{fmtNum(t.pnl_pct, 2)}%
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
                            <td className="px-4 py-3 font-mono text-white">{fmtNum(winRate, 1, "0.0")}%</td>
                            <td className="px-4 py-3 font-mono text-accent">{d.open_trades}</td>
                            <td className={`px-4 py-3 font-mono font-bold ${Number(d.total_pnl) >= 0 ? "text-bull" : "text-bear"}`}>
                              {Number(d.total_pnl) >= 0 ? "+" : ""}${fmtNum(d.total_pnl, 2, "0.00")}
                            </td>
                            <td className={`px-4 py-3 font-mono ${Number(d.avg_pnl_pct) >= 0 ? "text-bull" : "text-bear"}`}>
                              {Number(d.avg_pnl_pct) >= 0 ? "+" : ""}{fmtNum(d.avg_pnl_pct, 2, "0.00")}%
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
            <div className="space-y-4">
              <div className="bg-surface/50 border border-border/80 rounded-xl p-3 px-4 flex flex-col sm:flex-row sm:items-center justify-between gap-2 text-xs">
                <div className="flex items-center gap-2 text-muted">
                  <span className="text-accent text-sm">ℹ️</span>
                  <span>
                    Open positions use Good &apos;Til Cancelled (<code className="text-accent font-mono">gtc</code>) exit orders to persist across overnight sessions. If prior DAY orders expired at market close, click <strong className="text-white">🛡️ Re-attach GTC Orders</strong>.
                  </span>
                </div>
                <button
                  onClick={handleSyncOrders}
                  disabled={syncingOrders}
                  className="bg-accent/15 hover:bg-accent/25 text-accent border border-accent/40 text-xs font-semibold px-2.5 py-1 rounded-md self-start sm:self-auto transition-colors disabled:opacity-50 whitespace-nowrap"
                >
                  {syncingOrders ? "Re-attaching..." : "🛡️ Re-attach GTC Exit Orders"}
                </button>
              </div>

              <div className="bg-card border border-border rounded-xl overflow-hidden">
                <div className="overflow-x-auto">
                  <table className="w-full text-left text-sm">
                    <thead className="bg-surface text-muted text-xs uppercase border-b border-border">
                      <tr>
                        <th className="px-4 py-3">Symbol</th>
                        <th className="px-4 py-3">Side</th>
                        <th className="px-4 py-3">Qty</th>
                        <th className="px-4 py-3">Avg Entry</th>
                        <th className="px-4 py-3">Current</th>
                        <th className="px-4 py-3">Stop Loss</th>
                        <th className="px-4 py-3">Target 1</th>
                        <th className="px-4 py-3">Target 2</th>
                        <th className="px-4 py-3">Exit Orders</th>
                        <th className="px-4 py-3">Unrealized P&L</th>
                        <th className="px-4 py-3">P&L %</th>
                        <th className="px-4 py-3">Market Value</th>
                        <th className="px-4 py-3 text-right">Action</th>
                      </tr>
                    </thead>
                    <tbody className="divide-y divide-border">
                      {alpacaPositions.length === 0 ? (
                        <tr>
                          <td colSpan={13} className="text-center py-12 text-muted">
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
                              <td className="px-4 py-3 font-mono text-white">{fmtPrice(p.avg_entry_price)}</td>
                              <td className="px-4 py-3 font-mono text-white">{fmtPrice(p.current_price)}</td>
                              <td className="px-4 py-3 font-mono text-bear font-semibold">
                                {p.stop_price ? fmtPrice(p.stop_price) : "—"}
                              </td>
                              <td className="px-4 py-3 font-mono text-bull font-semibold">
                                {p.t1_price ? fmtPrice(p.t1_price) : "—"}
                              </td>
                              <td className="px-4 py-3 font-mono text-bull">
                                {p.t2_price ? fmtPrice(p.t2_price) : "—"}
                              </td>
                              <td className="px-4 py-3">
                                {p.has_active_orders ? (
                                  <span className="px-2 py-0.5 rounded text-[11px] font-semibold bg-bull/20 text-bull border border-bull/30 flex items-center gap-1 w-fit" title={`${p.active_exit_orders?.length || 1} exit order(s) active on Alpaca`}>
                                    <span>🛡️</span> GTC Active
                                  </span>
                                ) : (
                                  <button
                                    onClick={handleSyncOrders}
                                    disabled={syncingOrders}
                                    className="px-2 py-0.5 rounded text-[11px] font-semibold bg-bear/20 text-bear border border-bear/30 hover:bg-bear/30 transition-colors flex items-center gap-1"
                                    title="Click to re-attach GTC Stop Loss & Take Profit order"
                                  >
                                    <span>⚠️</span> Unhedged &middot; Sync
                                  </button>
                                )}
                              </td>
                              <td className={`px-4 py-3 font-mono font-bold ${pl >= 0 ? "text-bull" : "text-bear"}`}>
                                {pl >= 0 ? "+" : ""}${fmtNum(pl, 2)}
                              </td>
                              <td className={`px-4 py-3 font-mono font-bold ${plpc >= 0 ? "text-bull" : "text-bear"}`}>
                                {plpc >= 0 ? "+" : ""}{fmtNum(plpc, 2)}%
                              </td>
                              <td className="px-4 py-3 font-mono text-white">{fmtPrice(p.market_value)}</td>
                              <td className="px-4 py-3 text-right">
                                <button
                                  onClick={() => openCloseModalForBroker(p)}
                                  className="bg-bear/10 hover:bg-bear/20 text-bear border border-bear/30 text-xs font-semibold px-2.5 py-1 rounded transition-colors inline-flex items-center gap-1"
                                  title={`Close or set limit exit price for ${p.symbol}`}
                                >
                                  <span>⚡</span> Close / Exit...
                                </button>
                              </td>
                            </tr>
                          );
                        })
                      )}
                    </tbody>
                  </table>
                </div>
              </div>
            </div>
          )}

          {/* 5. ALPACA BROKER RECENT ORDERS */}
          {activeTab === "broker_orders" && (
            <div className="space-y-4">
              <div className="bg-surface/50 border border-border/80 rounded-xl p-3 px-4 flex flex-col sm:flex-row sm:items-center justify-between gap-2 text-xs">
                <div className="flex items-center gap-3">
                  <span className="text-accent text-sm">📜</span>
                  <span className="text-white font-semibold">
                    Orders on {mode === "live" ? "Live Alpaca Account" : "Alpaca Paper Account"}
                  </span>
                  <span className="text-muted text-[11px]">
                    ({alpacaOrders.filter((o) => ["new", "accepted", "partially_filled", "held", "pending_new"].includes((o.status || "").toLowerCase())).length} open/pending)
                  </span>
                </div>
                <div className="flex items-center gap-2">
                  <button
                    onClick={() => loadData(true)}
                    disabled={refreshing}
                    className="bg-surface hover:bg-surface/80 border border-border text-white text-xs font-semibold px-2.5 py-1 rounded-md transition-colors"
                  >
                    🔄 Refresh Orders
                  </button>
                  <button
                    onClick={handleCancelAllOrders}
                    disabled={
                      cancelingAllOrders ||
                      alpacaOrders.filter((o) =>
                        ["new", "accepted", "partially_filled", "held", "pending_new"].includes(
                          (o.status || "").toLowerCase()
                        )
                      ).length === 0
                    }
                    className="bg-bear/15 hover:bg-bear/25 text-bear border border-bear/40 text-xs font-semibold px-2.5 py-1 rounded-md transition-colors disabled:opacity-40"
                    title="Cancel all open/pending orders on Alpaca"
                  >
                    {cancelingAllOrders ? "Cancelling..." : "🚫 Cancel All Open Orders"}
                  </button>
                </div>
              </div>

              <div className="bg-card border border-border rounded-xl overflow-hidden">
                <div className="overflow-x-auto">
                  <table className="w-full text-left text-sm">
                    <thead className="bg-surface text-muted text-xs uppercase border-b border-border">
                      <tr>
                        <th className="px-4 py-3">Status</th>
                        <th className="px-4 py-3">Symbol</th>
                        <th className="px-4 py-3">Side</th>
                        <th className="px-4 py-3">Qty / Filled</th>
                        <th className="px-4 py-3">Type</th>
                        <th className="px-4 py-3">Limit / Target</th>
                        <th className="px-4 py-3">Stop Loss</th>
                        <th className="px-4 py-3">TIF</th>
                        <th className="px-4 py-3">Submitted At</th>
                        <th className="px-4 py-3 text-right">Action</th>
                      </tr>
                    </thead>
                    <tbody className="divide-y divide-border">
                      {alpacaOrders.length === 0 ? (
                        <tr>
                          <td colSpan={10} className="text-center py-12 text-muted">
                            No recent broker orders found.
                          </td>
                        </tr>
                      ) : (
                        alpacaOrders.map((o, idx) => {
                          const st = (o.status || "").toUpperCase();
                          const isFilled = st === "FILLED";
                          const isPending = [
                            "NEW",
                            "ACCEPTED",
                            "PARTIALLY_FILLED",
                            "HELD",
                            "PENDING_NEW",
                            "ACCEPTED_FOR_BIDDING",
                          ].includes(st);
                          const isBuy = o.side?.toLowerCase() === "buy";
                          return (
                            <tr key={idx} className="hover:bg-surface/50">
                              <td className="px-4 py-3">
                                <span
                                  className={`px-2 py-0.5 rounded text-xs font-semibold inline-flex items-center gap-1 ${
                                    isFilled
                                      ? "text-bull bg-bull/10 border border-bull/20"
                                      : isPending
                                      ? "text-accent bg-accent/10 border border-accent/20"
                                      : "text-bear bg-bear/10 border border-bear/20"
                                  }`}
                                >
                                  <span>{isFilled ? "✅" : isPending ? "⏳" : "❌"}</span>
                                  <span>{st}</span>
                                </span>
                              </td>
                              <td className="px-4 py-3 font-mono font-bold text-accent">{o.symbol}</td>
                              <td className="px-4 py-3">
                                <span
                                  className={`px-2 py-0.5 rounded text-xs font-bold ${
                                    isBuy ? "bg-bull/20 text-bull" : "bg-bear/20 text-bear"
                                  }`}
                                >
                                  {o.side?.toUpperCase()}
                                </span>
                              </td>
                              <td className="px-4 py-3 font-mono text-white">
                                {o.filled_qty || 0} / {o.qty}
                              </td>
                              <td className="px-4 py-3 text-xs font-mono text-muted uppercase">
                                {o.type} {o.order_class && o.order_class !== "simple" ? `(${o.order_class})` : ""}
                              </td>
                              <td className="px-4 py-3 font-mono text-bull font-semibold">
                                {o.limit_price ? `$${fmtPrice(o.limit_price)}` : "—"}
                              </td>
                              <td className="px-4 py-3 font-mono text-bear font-semibold">
                                {o.stop_price ? `$${fmtPrice(o.stop_price)}` : "—"}
                              </td>
                              <td className="px-4 py-3 font-mono text-xs text-white uppercase">
                                {o.time_in_force || "—"}
                              </td>
                              <td className="px-4 py-3 font-mono text-xs text-muted">
                                {o.submitted_at ? new Date(o.submitted_at).toLocaleString() : "—"}
                              </td>
                              <td className="px-4 py-3 text-right">
                                {isPending ? (
                                  <button
                                    onClick={() => handleCancelOrder(o.id, o.symbol)}
                                    disabled={cancelingOrderId === o.id}
                                    className="bg-bear/15 hover:bg-bear/25 text-bear border border-bear/40 text-xs font-semibold px-2.5 py-1 rounded transition-colors disabled:opacity-50 inline-flex items-center gap-1"
                                    title={`Cancel order #${o.id} for ${o.symbol}`}
                                  >
                                    {cancelingOrderId === o.id ? "Cancelling..." : "❌ Cancel"}
                                  </button>
                                ) : (
                                  <span className="text-muted text-xs">—</span>
                                )}
                              </td>
                            </tr>
                          );
                        })
                      )}
                    </tbody>
                  </table>
                </div>
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

      {/* ── CLOSE POSITION / ORDER MODAL ─────────────────────────────── */}
      {closeModal && closeModal.isOpen && (
        <div className="fixed inset-0 z-50 bg-black/80 backdrop-blur-sm flex items-center justify-center p-4 overflow-y-auto">
          <div className="bg-card border border-border rounded-2xl w-full max-w-xl shadow-2xl overflow-hidden animate-in fade-in zoom-in-95 duration-150 my-8">
            {/* Modal Header */}
            <div className="px-6 py-4 border-b border-border flex items-center justify-between bg-surface/40">
              <div className="flex items-center gap-3">
                <div className="w-9 h-9 rounded-xl bg-accent/15 border border-accent/30 flex items-center justify-center text-lg">
                  {closeModal.orderType === "oco" ? "🛡️" : closeModal.orderType === "stop" ? "🛑" : closeModal.orderType === "limit" ? "🎯" : "⚡"}
                </div>
                <div>
                  <h2 className="text-base font-bold text-white flex items-center gap-2">
                    Exit &amp; Protect &middot; <span className="text-accent font-mono">{closeModal.symbol}</span>
                  </h2>
                  <p className="text-xs text-muted">
                    {closeModal.source === "broker" ? "Alpaca Broker Position" : "Simulated Paper Trade"} &bull; {mode === "live" ? "Live Broker" : "Paper Trading"}
                  </p>
                </div>
              </div>
              <button
                onClick={() => setCloseModal(null)}
                disabled={submittingClose}
                className="text-muted hover:text-white text-lg font-bold p-1.5 rounded-lg hover:bg-surface transition-colors"
              >
                ✕
              </button>
            </div>

            {/* Modal Body */}
            <div className="p-6 space-y-5 max-h-[80vh] overflow-y-auto">
              {/* Position Info Pill */}
              <div className="grid grid-cols-2 sm:grid-cols-4 gap-2 bg-surface/60 border border-border/80 rounded-xl p-3 text-center">
                <div>
                  <div className="text-[10px] text-muted uppercase font-semibold">Side</div>
                  <div className={`text-xs font-bold font-mono mt-0.5 ${closeModal.side === "LONG" ? "text-bull" : "text-bear"}`}>
                    {closeModal.side}
                  </div>
                </div>
                <div>
                  <div className="text-[10px] text-muted uppercase font-semibold">Open Shares</div>
                  <div className="text-xs font-bold font-mono text-white mt-0.5">{closeModal.shares}</div>
                </div>
                <div>
                  <div className="text-[10px] text-muted uppercase font-semibold">Avg Entry</div>
                  <div className="text-xs font-bold font-mono text-white mt-0.5">${fmtPrice(closeModal.avgEntry)}</div>
                </div>
                <div>
                  <div className="text-[10px] text-muted uppercase font-semibold">Current Price</div>
                  <div className="text-xs font-bold font-mono text-accent mt-0.5">
                    ${fmtPrice(closeModal.currentPrice || closeModal.avgEntry)}
                  </div>
                </div>
              </div>

              {/* Order Type Selection: 4 distinct choices */}
              <div>
                <label className="text-xs font-semibold text-muted uppercase tracking-wider block mb-2">
                  Select Exit / Protection Strategy
                </label>
                <div className="grid grid-cols-2 sm:grid-cols-4 gap-2">
                  {/* 1. Bracket (OCO) */}
                  <button
                    type="button"
                    onClick={() => setCloseModal({ ...closeModal, orderType: "oco" })}
                    className={`p-2.5 rounded-xl border text-left transition-all ${
                      closeModal.orderType === "oco"
                        ? "border-accent bg-accent/15 text-white ring-1 ring-accent"
                        : "border-border bg-surface/40 text-muted hover:text-white hover:bg-surface"
                    }`}
                  >
                    <div className="font-bold text-xs flex items-center gap-1">
                      <span>🛡️</span> Bracket
                    </div>
                    <div className="text-[10px] text-muted mt-1 leading-tight">
                      Target + Stop Loss (OCO)
                    </div>
                  </button>

                  {/* 2. Stop Loss */}
                  <button
                    type="button"
                    onClick={() => setCloseModal({ ...closeModal, orderType: "stop" })}
                    className={`p-2.5 rounded-xl border text-left transition-all ${
                      closeModal.orderType === "stop"
                        ? "border-bear bg-bear/15 text-white ring-1 ring-bear"
                        : "border-border bg-surface/40 text-muted hover:text-white hover:bg-surface"
                    }`}
                  >
                    <div className="font-bold text-xs flex items-center gap-1 text-bear">
                      <span>🛑</span> Stop Loss
                    </div>
                    <div className="text-[10px] text-muted mt-1 leading-tight">
                      Downside exit protection
                    </div>
                  </button>

                  {/* 3. Take Profit (Limit) */}
                  <button
                    type="button"
                    onClick={() => setCloseModal({ ...closeModal, orderType: "limit" })}
                    className={`p-2.5 rounded-xl border text-left transition-all ${
                      closeModal.orderType === "limit"
                        ? "border-bull bg-bull/15 text-white ring-1 ring-bull"
                        : "border-border bg-surface/40 text-muted hover:text-white hover:bg-surface"
                    }`}
                  >
                    <div className="font-bold text-xs flex items-center gap-1 text-bull">
                      <span>🎯</span> Take Profit
                    </div>
                    <div className="text-[10px] text-muted mt-1 leading-tight">
                      Limit exit at target price
                    </div>
                  </button>

                  {/* 4. Market Order */}
                  <button
                    type="button"
                    onClick={() => setCloseModal({ ...closeModal, orderType: "market" })}
                    className={`p-2.5 rounded-xl border text-left transition-all ${
                      closeModal.orderType === "market"
                        ? "border-amber-500 bg-amber-500/15 text-white ring-1 ring-amber-500"
                        : "border-border bg-surface/40 text-muted hover:text-white hover:bg-surface"
                    }`}
                  >
                    <div className="font-bold text-xs flex items-center gap-1 text-amber-400">
                      <span>⚡</span> Market Close
                    </div>
                    <div className="text-[10px] text-muted mt-1 leading-tight">
                      Immediate liquidation
                    </div>
                  </button>
                </div>
              </div>

              {/* Price Inputs depending on orderType */}
              {(() => {
                const isShort = closeModal.side === "SHORT";
                const basePx = closeModal.currentPrice || closeModal.avgEntry || 100;

                const targetMultipliers = isShort
                  ? [
                      { label: "-1%", factor: -0.01 },
                      { label: "-2%", factor: -0.02 },
                      { label: "-3%", factor: -0.03 },
                      { label: "-5%", factor: -0.05 },
                    ]
                  : [
                      { label: "+1%", factor: 0.01 },
                      { label: "+2%", factor: 0.02 },
                      { label: "+3%", factor: 0.03 },
                      { label: "+5%", factor: 0.05 },
                    ];

                const stopMultipliers = isShort
                  ? [
                      { label: "+1%", factor: 0.01 },
                      { label: "+2%", factor: 0.02 },
                      { label: "+3%", factor: 0.03 },
                      { label: "+5%", factor: 0.05 },
                    ]
                  : [
                      { label: "-1%", factor: -0.01 },
                      { label: "-2%", factor: -0.02 },
                      { label: "-3%", factor: -0.03 },
                      { label: "-5%", factor: -0.05 },
                    ];

                return (
                  <div className="space-y-4">
                    {/* Take Profit / Limit Input (shown for oco and limit) */}
                    {(closeModal.orderType === "oco" || closeModal.orderType === "limit") && (
                      <div className="space-y-2 bg-bull/5 border border-bull/20 rounded-xl p-3.5">
                        <div className="flex items-center justify-between">
                          <label className="text-xs font-bold text-bull flex items-center gap-1.5">
                            <span>🎯 Take Profit / Target Price ($)</span>
                          </label>
                          <span className="text-[11px] text-muted">
                            Entry: ${fmtPrice(closeModal.avgEntry)}
                          </span>
                        </div>

                        <div className="relative">
                          <span className="absolute left-3 top-1/2 -translate-y-1/2 text-muted font-mono text-sm">$</span>
                          <input
                            type="number"
                            step="0.01"
                            min="0.01"
                            value={closeModal.limitPrice}
                            onChange={(e) => setCloseModal({ ...closeModal, limitPrice: e.target.value })}
                            placeholder="0.00"
                            className="w-full bg-surface border border-bull/30 rounded-lg pl-7 pr-4 py-2 text-white font-mono text-base font-bold focus:outline-none focus:border-bull"
                          />
                        </div>

                        {/* Target quick pills */}
                        <div className="flex items-center gap-1.5 pt-1 overflow-x-auto">
                          <span className="text-[10px] text-muted uppercase font-semibold mr-0.5">Presets:</span>
                          {closeModal.initialT1 && (
                            <button
                              type="button"
                              onClick={() => setCloseModal({ ...closeModal, limitPrice: Number(closeModal.initialT1).toFixed(2) })}
                              className="bg-bull/10 hover:bg-bull/20 border border-bull/30 text-[10px] font-mono px-2 py-0.5 rounded text-bull transition-colors whitespace-nowrap"
                            >
                              T1: ${fmtPrice(closeModal.initialT1)}
                            </button>
                          )}
                          {closeModal.initialT2 && (
                            <button
                              type="button"
                              onClick={() => setCloseModal({ ...closeModal, limitPrice: Number(closeModal.initialT2).toFixed(2) })}
                              className="bg-bull/10 hover:bg-bull/20 border border-bull/30 text-[10px] font-mono px-2 py-0.5 rounded text-bull transition-colors whitespace-nowrap"
                            >
                              T2: ${fmtPrice(closeModal.initialT2)}
                            </button>
                          )}
                          {targetMultipliers.map((adj) => {
                            const calculated = (basePx * (1 + adj.factor)).toFixed(2);
                            return (
                              <button
                                key={adj.label}
                                type="button"
                                onClick={() => setCloseModal({ ...closeModal, limitPrice: calculated })}
                                className="bg-surface hover:bg-surface/80 border border-border text-[10px] font-mono px-2 py-0.5 rounded text-muted hover:text-white transition-colors whitespace-nowrap"
                              >
                                {adj.label}
                              </button>
                            );
                          })}
                        </div>
                      </div>
                    )}

                    {/* Stop Loss Input (shown for oco and stop) */}
                    {(closeModal.orderType === "oco" || closeModal.orderType === "stop") && (
                      <div className="space-y-2 bg-bear/5 border border-bear/20 rounded-xl p-3.5">
                        <div className="flex items-center justify-between">
                          <label className="text-xs font-bold text-bear flex items-center gap-1.5">
                            <span>🛑 Stop Loss Price ($)</span>
                          </label>
                          <span className="text-[11px] text-muted">
                            Protects downside capital
                          </span>
                        </div>

                        <div className="relative">
                          <span className="absolute left-3 top-1/2 -translate-y-1/2 text-muted font-mono text-sm">$</span>
                          <input
                            type="number"
                            step="0.01"
                            min="0.01"
                            value={closeModal.stopPrice}
                            onChange={(e) => setCloseModal({ ...closeModal, stopPrice: e.target.value })}
                            placeholder="0.00"
                            className="w-full bg-surface border border-bear/30 rounded-lg pl-7 pr-4 py-2 text-white font-mono text-base font-bold focus:outline-none focus:border-bear"
                          />
                        </div>

                        {/* Stop Loss quick pills */}
                        <div className="flex items-center gap-1.5 pt-1 overflow-x-auto">
                          <span className="text-[10px] text-muted uppercase font-semibold mr-0.5">Presets:</span>
                          {closeModal.initialStop && (
                            <button
                              type="button"
                              onClick={() => setCloseModal({ ...closeModal, stopPrice: Number(closeModal.initialStop).toFixed(2) })}
                              className="bg-bear/10 hover:bg-bear/20 border border-bear/30 text-[10px] font-mono px-2 py-0.5 rounded text-bear transition-colors whitespace-nowrap"
                            >
                              Initial Stop: ${fmtPrice(closeModal.initialStop)}
                            </button>
                          )}
                          <button
                            type="button"
                            onClick={() => setCloseModal({ ...closeModal, stopPrice: closeModal.avgEntry.toFixed(2) })}
                            className="bg-surface hover:bg-surface/80 border border-border text-[10px] font-mono px-2 py-0.5 rounded text-accent hover:text-white transition-colors whitespace-nowrap"
                          >
                            BE: ${fmtPrice(closeModal.avgEntry)}
                          </button>
                          {stopMultipliers.map((adj) => {
                            const calculated = (basePx * (1 + adj.factor)).toFixed(2);
                            return (
                              <button
                                key={adj.label}
                                type="button"
                                onClick={() => setCloseModal({ ...closeModal, stopPrice: calculated })}
                                className="bg-surface hover:bg-surface/80 border border-border text-[10px] font-mono px-2 py-0.5 rounded text-muted hover:text-white transition-colors whitespace-nowrap"
                              >
                                {adj.label}
                              </button>
                            );
                          })}
                        </div>
                      </div>
                    )}

                    {/* Market Order Explainer */}
                    {closeModal.orderType === "market" && (
                      <div className="bg-amber-500/10 border border-amber-500/30 rounded-xl p-3.5 text-xs text-amber-200/90 leading-relaxed flex items-start gap-2.5">
                        <span className="text-base">⚡</span>
                        <div>
                          <div className="font-bold text-amber-300">Immediate Liquidation Notice</div>
                          Submitting this will execute a market sell order directly against current broker bid liquidity at the best available price.
                        </div>
                      </div>
                    )}

                    {/* Time in Force Toggle (shown when not market order) */}
                    {closeModal.orderType !== "market" && (
                      <div className="flex items-center justify-between pt-1 text-xs">
                        <span className="text-muted">Time In Force (TIF):</span>
                        <div className="flex items-center gap-2">
                          <button
                            type="button"
                            onClick={() => setCloseModal({ ...closeModal, timeInForce: "gtc" })}
                            className={`px-2.5 py-1 rounded text-xs font-semibold border transition-colors ${
                              closeModal.timeInForce === "gtc"
                                ? "bg-accent text-white border-accent"
                                : "bg-surface text-muted border-border hover:text-white"
                            }`}
                          >
                            🛡️ GTC (Good &apos;Til Cancelled)
                          </button>
                          <button
                            type="button"
                            onClick={() => setCloseModal({ ...closeModal, timeInForce: "day" })}
                            className={`px-2.5 py-1 rounded text-xs font-semibold border transition-colors ${
                              closeModal.timeInForce === "day"
                                ? "bg-accent text-white border-accent"
                                : "bg-surface text-muted border-border hover:text-white"
                            }`}
                          >
                            DAY
                          </button>
                        </div>
                      </div>
                    )}
                  </div>
                );
              })()}

              {/* Shares to Close Input */}
              <div className="space-y-1.5">
                <div className="flex items-center justify-between">
                  <label className="text-xs font-semibold text-muted uppercase tracking-wider">
                    Quantity to Close
                  </label>
                  <div className="flex items-center gap-1.5">
                    {[
                      { label: "25%", pct: 0.25 },
                      { label: "50%", pct: 0.5 },
                      { label: "100% (All)", pct: 1.0 },
                    ].map((btn) => (
                      <button
                        key={btn.label}
                        type="button"
                        onClick={() => {
                          const q = Math.max(1, Math.round(closeModal.shares * btn.pct));
                          setCloseModal({ ...closeModal, qtyToClose: q.toString() });
                        }}
                        className="bg-surface hover:bg-surface/80 border border-border text-[10px] font-semibold px-2 py-0.5 rounded text-muted hover:text-white transition-colors"
                      >
                        {btn.label}
                      </button>
                    ))}
                  </div>
                </div>
                <input
                  type="number"
                  min="1"
                  max={closeModal.shares}
                  value={closeModal.qtyToClose}
                  onChange={(e) => setCloseModal({ ...closeModal, qtyToClose: e.target.value })}
                  className="w-full bg-surface border border-border rounded-lg px-3 py-2 text-white font-mono text-sm focus:outline-none focus:border-accent"
                />
              </div>

              {/* Estimated Proceeds & P&L Calculation */}
              {(() => {
                const q = parseInt(closeModal.qtyToClose) || 0;
                const cost = q * closeModal.avgEntry;
                const isShort = closeModal.side === "SHORT";

                const calcMetrics = (price: number) => {
                  const proceeds = q * price;
                  const pl = isShort ? (closeModal.avgEntry - price) * q : (price - closeModal.avgEntry) * q;
                  const plPct = cost > 0 ? (pl / cost) * 100 : 0;
                  return { proceeds, pl, plPct };
                };

                if (closeModal.orderType === "oco") {
                  const targetPx = parseFloat(closeModal.limitPrice) || 0;
                  const stopPx = parseFloat(closeModal.stopPrice) || 0;
                  const tMetrics = calcMetrics(targetPx);
                  const sMetrics = calcMetrics(stopPx);

                  return (
                    <div className="grid grid-cols-2 gap-2 text-xs">
                      <div className="bg-bull/10 border border-bull/30 rounded-xl p-3">
                        <div className="text-[11px] text-bull font-semibold flex items-center gap-1">
                          <span>🎯 Target Fill</span>
                        </div>
                        <div className="font-mono font-bold text-white text-sm mt-0.5">
                          ${fmtPrice(tMetrics.proceeds)}
                        </div>
                        <div className={`font-mono text-xs mt-0.5 ${tMetrics.pl >= 0 ? "text-bull" : "text-bear"}`}>
                          {tMetrics.pl >= 0 ? "+" : ""}${fmtNum(tMetrics.pl, 2)} ({tMetrics.pl >= 0 ? "+" : ""}{fmtNum(tMetrics.plPct, 2)}%)
                        </div>
                      </div>

                      <div className="bg-bear/10 border border-bear/30 rounded-xl p-3">
                        <div className="text-[11px] text-bear font-semibold flex items-center gap-1">
                          <span>🛑 Stop Out</span>
                        </div>
                        <div className="font-mono font-bold text-white text-sm mt-0.5">
                          ${fmtPrice(sMetrics.proceeds)}
                        </div>
                        <div className={`font-mono text-xs mt-0.5 ${sMetrics.pl >= 0 ? "text-bull" : "text-bear"}`}>
                          {sMetrics.pl >= 0 ? "+" : ""}${fmtNum(sMetrics.pl, 2)} ({sMetrics.pl >= 0 ? "+" : ""}{fmtNum(sMetrics.plPct, 2)}%)
                        </div>
                      </div>
                    </div>
                  );
                } else if (closeModal.orderType === "stop") {
                  const stopPx = parseFloat(closeModal.stopPrice) || 0;
                  const sMetrics = calcMetrics(stopPx);
                  return (
                    <div className="bg-bear/10 border border-bear/30 rounded-xl p-3 text-xs flex items-center justify-between">
                      <div>
                        <div className="text-muted text-[11px]">Protected Value (at Stop)</div>
                        <div className="font-mono font-bold text-white text-sm mt-0.5">
                          ${fmtPrice(sMetrics.proceeds)}
                        </div>
                      </div>
                      <div className="text-right">
                        <div className="text-muted text-[11px]">P&amp;L at Stop</div>
                        <div className={`font-mono font-bold text-sm mt-0.5 ${sMetrics.pl >= 0 ? "text-bull" : "text-bear"}`}>
                          {sMetrics.pl >= 0 ? "+" : ""}${fmtNum(sMetrics.pl, 2)} ({sMetrics.pl >= 0 ? "+" : ""}{fmtNum(sMetrics.plPct, 2)}%)
                        </div>
                      </div>
                    </div>
                  );
                } else {
                  const exitPx =
                    closeModal.orderType === "limit"
                      ? parseFloat(closeModal.limitPrice) || 0
                      : closeModal.currentPrice || closeModal.avgEntry;
                  const m = calcMetrics(exitPx);
                  return (
                    <div className="bg-surface/40 border border-border/60 rounded-xl p-3 text-xs flex items-center justify-between">
                      <div>
                        <div className="text-muted text-[11px]">Estimated Proceeds</div>
                        <div className="font-mono font-bold text-white text-sm mt-0.5">
                          ${fmtPrice(m.proceeds)}
                        </div>
                      </div>
                      <div className="text-right">
                        <div className="text-muted text-[11px]">Estimated P&amp;L</div>
                        <div className={`font-mono font-bold text-sm mt-0.5 ${m.pl >= 0 ? "text-bull" : "text-bear"}`}>
                          {m.pl >= 0 ? "+" : ""}${fmtNum(m.pl, 2)} ({m.pl >= 0 ? "+" : ""}{fmtNum(m.plPct, 2)}%)
                        </div>
                      </div>
                    </div>
                  );
                }
              })()}
            </div>

            {/* Modal Footer */}
            <div className="px-6 py-4 border-t border-border bg-surface/30 flex items-center justify-end gap-3">
              <button
                type="button"
                onClick={() => setCloseModal(null)}
                disabled={submittingClose}
                className="bg-surface hover:bg-surface/80 border border-border text-white text-xs font-semibold px-4 py-2.5 rounded-lg transition-colors disabled:opacity-50"
              >
                Cancel
              </button>

              <button
                type="button"
                onClick={handleSubmitClose}
                disabled={submittingClose}
                className={`text-xs font-bold px-4 py-2.5 rounded-lg flex items-center gap-2 transition-colors disabled:opacity-50 ${
                  closeModal.orderType === "oco"
                    ? "bg-accent hover:bg-accent/90 text-white shadow-lg shadow-accent/25"
                    : closeModal.orderType === "stop"
                    ? "bg-bear hover:bg-bear/90 text-white shadow-lg shadow-bear/25"
                    : closeModal.orderType === "limit"
                    ? "bg-bull hover:bg-bull/90 text-white shadow-lg shadow-bull/25"
                    : "bg-amber-600 hover:bg-amber-500 text-white shadow-lg shadow-amber-600/25"
                }`}
              >
                {submittingClose ? (
                  <>
                    <span className="animate-spin">🔄</span> Submitting...
                  </>
                ) : closeModal.orderType === "oco" ? (
                  <>🛡️ Submit Bracket Exit (Target &amp; Stop)</>
                ) : closeModal.orderType === "stop" ? (
                  <>🛑 Submit Stop Loss Order</>
                ) : closeModal.orderType === "limit" ? (
                  <>🎯 Submit Limit Exit Order</>
                ) : (
                  <>⚡ Confirm Market Liquidation</>
                )}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
