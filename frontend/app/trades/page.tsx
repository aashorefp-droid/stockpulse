"use client";
import { useEffect, useState } from "react";

const API_BASE = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

export default function TradesPage() {
  const [tab, setTab] = useState<"open" | "closed">("open");
  const [openTrades, setOpenTrades] = useState<any[]>([]);
  const [closedTrades, setClosedTrades] = useState<any[]>([]);
  const [winRate, setWinRate] = useState<number>(0);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    Promise.all([
      fetch(`${API_BASE}/api/trades/open`).then((r) => r.json()),
      fetch(`${API_BASE}/api/trades/closed`).then((r) => r.json()),
    ])
      .then(([oData, cData]) => {
        setOpenTrades(oData.items || []);
        setClosedTrades(cData.items || []);
        setWinRate(cData.win_rate || 0);
        setLoading(false);
      })
      .catch(() => setLoading(false));
  }, []);

  return (
    <div className="max-w-screen-2xl mx-auto px-4 py-8">
      <div className="flex items-center justify-between mb-6">
        <div>
          <h1 className="text-2xl font-bold text-white flex items-center gap-2">
            ?? Trade Tracker & History
          </h1>
          <p className="text-muted text-sm mt-1">
            Track active swing & intraday trades, monitor stop-losses, and review closed outcomes.
          </p>
        </div>
        <div className="bg-card border border-border rounded-xl px-4 py-2 flex gap-6 text-sm font-mono">
          <div>
            <span className="text-muted text-xs block">Active Trades</span>
            <span className="text-white font-bold text-base">{openTrades.length}</span>
          </div>
          <div>
            <span className="text-muted text-xs block">Historical Win Rate</span>
            <span className="text-bull font-bold text-base">{winRate}%</span>
          </div>
        </div>
      </div>

      <div className="flex gap-2 mb-4">
        <button
          onClick={() => setTab("open")}
          className={`px-4 py-1.5 rounded-lg text-sm font-semibold transition-colors ${
            tab === "open" ? "bg-accent text-black" : "bg-card text-muted hover:text-white"
          }`}
        >
          Open Positions ({openTrades.length})
        </button>
        <button
          onClick={() => setTab("closed")}
          className={`px-4 py-1.5 rounded-lg text-sm font-semibold transition-colors ${
            tab === "closed" ? "bg-accent text-black" : "bg-card text-muted hover:text-white"
          }`}
        >
          Closed Ledger ({closedTrades.length})
        </button>
      </div>

      {loading ? (
        <div className="text-center py-20 text-muted">Loading trade journal?</div>
      ) : (
        <div className="bg-card border border-border rounded-xl overflow-hidden">
          <table className="w-full text-left text-sm">
            <thead className="bg-surface text-muted text-xs uppercase border-b border-border">
              <tr>
                <th className="px-4 py-3">Date</th>
                <th className="px-4 py-3">Ticker</th>
                <th className="px-4 py-3">Direction</th>
                <th className="px-4 py-3">Entry</th>
                <th className="px-4 py-3">Stop Loss</th>
                <th className="px-4 py-3">Target 1</th>
                <th className="px-4 py-3">Status / Outcome</th>
                {tab === "closed" && <th className="px-4 py-3">P&L ($)</th>}
              </tr>
            </thead>
            <tbody className="divide-y divide-border">
              {(tab === "open" ? openTrades : closedTrades).map((t) => (
                <tr key={t.id} className="hover:bg-surface/50">
                  <td className="px-4 py-3 font-mono text-xs">{t.entry_date}</td>
                  <td className="px-4 py-3 font-mono font-bold text-accent">{t.ticker}</td>
                  <td className="px-4 py-3 font-semibold text-xs">{t.direction}</td>
                  <td className="px-4 py-3 font-mono">${t.entry_price?.toFixed(2)}</td>
                  <td className="px-4 py-3 font-mono text-bear">${t.stop_loss?.toFixed(2) ?? "?"}</td>
                  <td className="px-4 py-3 font-mono text-bull">${t.target1?.toFixed(2) ?? "?"}</td>
                  <td className="px-4 py-3">
                    <span className={`px-2 py-0.5 rounded text-xs font-bold ${
                      t.outcome === "WIN" ? "bg-bull/20 text-bull" : t.outcome === "LOSS" ? "bg-bear/20 text-bear" : "bg-surface text-muted"
                    }`}>
                      {t.outcome || t.status}
                    </span>
                  </td>
                  {tab === "closed" && (
                    <td className={`px-4 py-3 font-mono font-bold ${(t.pnl_dollars ?? 0) >= 0 ? "text-bull" : "text-bear"}`}>
                      {(t.pnl_dollars ?? 0) >= 0 ? "+" : ""}${t.pnl_dollars?.toFixed(2) ?? "0.00"}
                    </td>
                  )}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
