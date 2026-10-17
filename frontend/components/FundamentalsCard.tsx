"use client";

import { useState, useEffect } from "react";
import type {
  Fundamentals,
  FinvizInsiderData,
  CongressTradesData,
  AlphaSpreadIntrinsicData,
} from "@/lib/api";
import { fetchFundamentalsExtras } from "@/lib/api";

interface Row {
  label: string;
  value: string | number;
}

interface Props {
  f: Fundamentals;
  ticker?: string;
  currentPrice?: number;
  finvizInsider?: FinvizInsiderData | null;
  congressTrades?: CongressTradesData | null;
  alphaspreadIntrinsic?: AlphaSpreadIntrinsicData | null;
}

export default function FundamentalsCard({
  f,
  ticker,
  currentPrice,
  finvizInsider: initialFinviz,
  congressTrades: initialCongress,
  alphaspreadIntrinsic: initialAlpha,
}: Props) {
  const [activeTab, setActiveTab] = useState<"multiples" | "alphaspread" | "insider" | "congress">("multiples");
  const [finviz, setFinviz] = useState<FinvizInsiderData | null | undefined>(initialFinviz);
  const [congress, setCongress] = useState<CongressTradesData | null | undefined>(initialCongress);
  const [alpha, setAlpha] = useState<AlphaSpreadIntrinsicData | null | undefined>(initialAlpha);
  const [loadingExtras, setLoadingExtras] = useState<boolean>(false);

  useEffect(() => {
    if (initialFinviz !== undefined) setFinviz(initialFinviz);
    if (initialCongress !== undefined) setCongress(initialCongress);
    if (initialAlpha !== undefined) setAlpha(initialAlpha);
  }, [initialFinviz, initialCongress, initialAlpha]);

  useEffect(() => {
    if (!ticker) return;
    if (finviz === undefined || congress === undefined || alpha === undefined) {
      setLoadingExtras(true);
      fetchFundamentalsExtras(ticker)
        .then((res) => {
          if (res) {
            if (finviz === undefined) setFinviz(res.finviz_insider ?? null);
            if (congress === undefined) setCongress(res.congress_trades ?? null);
            if (alpha === undefined) setAlpha(res.alphaspread_intrinsic ?? null);
          }
        })
        .catch(() => {})
        .finally(() => setLoadingExtras(false));
    }
  }, [ticker, finviz, congress, alpha]);

  const rows: Row[] = [
    { label: "Market Cap", value: f.market_cap },
    { label: "P/E (TTM)", value: f.pe_ratio },
    { label: "Forward P/E", value: f.forward_pe },
    { label: "EPS", value: f.eps },
    { label: "Profit Margin", value: `${f.profit_margin}%` },
    { label: "Dividend Yield", value: `${f.dividend_yield}%` },
    { label: "52W High", value: `$${f["52w_high"]}` },
    { label: "52W Low", value: `$${f["52w_low"]}` },
    { label: "Beta", value: f.beta },
    { label: "Avg Volume", value: typeof f.avg_volume === "number" ? f.avg_volume.toLocaleString() : f.avg_volume },
  ];

  const sc = alpha?.scenarios;
  const bear = sc?.bear;
  const base = sc?.base;
  const bull = sc?.bull;

  const w90_insider = finviz?.last_90_days;
  const w90_congress = congress?.last_90_days;

  return (
    <div className="card space-y-3.5">
      {/* Header */}
      <div className="flex flex-wrap items-center justify-between gap-2 border-b border-border/40 pb-2.5">
        <div>
          <h3 className="font-semibold text-white text-sm sm:text-base">{f.name || ticker}</h3>
          <p className="text-muted text-xs">
            {f.sector} {f.industry ? `· ${f.industry}` : ""}
          </p>
        </div>
        {ticker && (
          <span className="font-mono text-xs font-bold px-2 py-0.5 rounded bg-surface border border-border text-slate-300">
            {ticker}
          </span>
        )}
      </div>

      {/* Navigation Sub-Tabs */}
      <div className="flex items-center gap-1.5 overflow-x-auto text-[11px] font-semibold border-b border-border/40 pb-2">
        <button
          onClick={() => setActiveTab("multiples")}
          className={`px-2.5 py-1 rounded transition-all whitespace-nowrap ${
            activeTab === "multiples"
              ? "bg-[#4d9fff]/20 text-[#4d9fff] border border-[#4d9fff]/40 font-bold"
              : "text-muted hover:text-white hover:bg-surface/60"
          }`}
        >
          📊 Multiples
        </button>
        <button
          onClick={() => setActiveTab("alphaspread")}
          className={`px-2.5 py-1 rounded transition-all whitespace-nowrap flex items-center gap-1 ${
            activeTab === "alphaspread"
              ? "bg-amber-500/20 text-amber-300 border border-amber-500/40 font-bold"
              : "text-muted hover:text-white hover:bg-surface/60"
          }`}
        >
          <span>🎯 AlphaSpread DCF</span>
          {base?.intrinsic_value != null && (
            <span className="text-[10px] font-mono px-1 rounded bg-black/40">
              ${base.intrinsic_value.toFixed(0)}
            </span>
          )}
        </button>
        <button
          onClick={() => setActiveTab("insider")}
          className={`px-2.5 py-1 rounded transition-all whitespace-nowrap flex items-center gap-1 ${
            activeTab === "insider"
              ? "bg-emerald-500/20 text-emerald-300 border border-emerald-500/40 font-bold"
              : "text-muted hover:text-white hover:bg-surface/60"
          }`}
        >
          <span>💼 Insider (Finviz)</span>
          {w90_insider && (
            <span
              className={`text-[9px] px-1 rounded font-bold ${
                w90_insider.net_signal.includes("BUY")
                  ? "bg-emerald-500/30 text-emerald-300"
                  : w90_insider.net_signal.includes("SELL")
                  ? "bg-rose-500/30 text-rose-300"
                  : "bg-surface text-muted"
              }`}
            >
              90d {w90_insider.net_signal.includes("BUY") ? "BUY" : w90_insider.net_signal.includes("SELL") ? "SELL" : "—"}
            </span>
          )}
        </button>
        <button
          onClick={() => setActiveTab("congress")}
          className={`px-2.5 py-1 rounded transition-all whitespace-nowrap flex items-center gap-1 ${
            activeTab === "congress"
              ? "bg-indigo-500/20 text-indigo-300 border border-indigo-500/40 font-bold"
              : "text-muted hover:text-white hover:bg-surface/60"
          }`}
        >
          <span>🏛️ Congress Trades</span>
          {w90_congress && (
            <span
              className={`text-[9px] px-1 rounded font-bold ${
                w90_congress.purchases > w90_congress.sales
                  ? "bg-emerald-500/30 text-emerald-300"
                  : w90_congress.sales > w90_congress.purchases
                  ? "bg-rose-500/30 text-rose-300"
                  : "bg-surface text-muted"
              }`}
            >
              +{w90_congress.purchases}/-{w90_congress.sales}
            </span>
          )}
        </button>
      </div>

      {/* Tab 1: Financial Multiples */}
      {activeTab === "multiples" && (
        <div className="space-y-3">
          <div className="grid grid-cols-2 gap-x-4 gap-y-2">
            {rows.map(({ label, value }) => (
              <div key={label} className="flex justify-between text-xs border-b border-border/40 pb-1">
                <span className="text-muted">{label}</span>
                <span className="font-mono text-white font-medium">{value ?? "N/A"}</span>
              </div>
            ))}
          </div>
          {f.description && (
            <p className="mt-2 text-[11px] text-muted leading-relaxed line-clamp-3">{f.description}</p>
          )}
        </div>
      )}

      {/* Tab 2: AlphaSpread DCF Intrinsic Values (Bear / Base / Bull) */}
      {activeTab === "alphaspread" && (
        <div className="space-y-3">
          {loadingExtras && !alpha && (
            <div className="text-xs text-muted py-4 text-center animate-pulse">
              Loading AlphaSpread DCF models…
            </div>
          )}

          {alpha && !alpha.error ? (
            <>
              {/* Overall Read Banner */}
              <div className="flex flex-wrap items-center justify-between gap-2 p-2.5 rounded-lg bg-surface/60 border border-border/60 text-xs">
                <div className="flex items-center gap-1.5">
                  <span className="text-amber-400 font-bold">AlphaSpread Read:</span>
                  <span
                    className={`font-semibold px-2 py-0.5 rounded text-[11px] ${
                      alpha.overall_read?.includes("UNDERVALUED")
                        ? "bg-emerald-500/20 text-emerald-300 border border-emerald-500/40"
                        : alpha.overall_read?.includes("OVERVALUED")
                        ? "bg-rose-500/20 text-rose-300 border border-rose-500/40"
                        : "bg-yellow-500/20 text-yellow-300 border border-yellow-500/40"
                    }`}
                  >
                    {alpha.overall_read || "Mixed DCF Posture"}
                  </span>
                </div>
                {alpha.intrinsic_value_range && (
                  <div className="text-[11px] font-mono text-slate-300">
                    DCF Range: <span className="font-bold text-amber-200">{alpha.intrinsic_value_range}</span>
                  </div>
                )}
              </div>

              {/* 3 Scenario Cards: Bear, Base, Bull */}
              <div className="grid grid-cols-1 sm:grid-cols-3 gap-2.5">
                {/* Bear Case */}
                <div className="bg-black/40 border border-rose-500/30 rounded-lg p-2.5 space-y-1.5">
                  <div className="flex items-center justify-between text-xs">
                    <span className="font-bold text-rose-400 flex items-center gap-1">
                      <span>🐻</span> Bear Case
                    </span>
                    <span className="text-[10px] uppercase font-bold text-rose-400/90">
                      {bear?.verdict || "Overvalued"}
                    </span>
                  </div>
                  <div className="text-base font-mono font-bold text-white">
                    {bear?.intrinsic_value != null ? `$${bear.intrinsic_value.toFixed(2)}` : "N/A"}
                  </div>
                  {bear?.upside_vs_price_pct != null && (
                    <div
                      className={`text-[11px] font-mono font-semibold ${
                        bear.upside_vs_price_pct >= 0 ? "text-emerald-400" : "text-rose-400"
                      }`}
                    >
                      {bear.upside_vs_price_pct >= 0 ? "+" : ""}
                      {bear.upside_vs_price_pct}% vs px
                    </div>
                  )}
                  {bear?.assumptions && (
                    <div className="pt-1 border-t border-border/40 text-[10px] text-muted space-y-0.5">
                      <div className="flex justify-between">
                        <span>Rev Growth:</span>
                        <span className="font-mono text-slate-300">{bear.assumptions.revenue_growth_pct}%</span>
                      </div>
                      <div className="flex justify-between">
                        <span>Target Margin:</span>
                        <span className="font-mono text-slate-300">{bear.assumptions.net_margin_end_pct}%</span>
                      </div>
                      <div className="flex justify-between">
                        <span>Discount:</span>
                        <span className="font-mono text-slate-300">{bear.assumptions.discount_rate_pct}%</span>
                      </div>
                    </div>
                  )}
                </div>

                {/* Base Case */}
                <div className="bg-black/50 border border-amber-500/40 rounded-lg p-2.5 space-y-1.5 shadow-sm shadow-amber-500/5">
                  <div className="flex items-center justify-between text-xs">
                    <span className="font-bold text-amber-300 flex items-center gap-1">
                      <span>⚖️</span> Base Case
                    </span>
                    <span className="text-[10px] uppercase font-bold text-amber-300/90">
                      {base?.verdict || "Consensus"}
                    </span>
                  </div>
                  <div className="text-base font-mono font-bold text-amber-200">
                    {base?.intrinsic_value != null ? `$${base.intrinsic_value.toFixed(2)}` : "N/A"}
                  </div>
                  {base?.upside_vs_price_pct != null && (
                    <div
                      className={`text-[11px] font-mono font-semibold ${
                        base.upside_vs_price_pct >= 0 ? "text-emerald-400" : "text-rose-400"
                      }`}
                    >
                      {base.upside_vs_price_pct >= 0 ? "+" : ""}
                      {base.upside_vs_price_pct}% vs px
                    </div>
                  )}
                  {base?.assumptions && (
                    <div className="pt-1 border-t border-border/40 text-[10px] text-muted space-y-0.5">
                      <div className="flex justify-between">
                        <span>Rev Growth:</span>
                        <span className="font-mono text-slate-300">{base.assumptions.revenue_growth_pct}%</span>
                      </div>
                      <div className="flex justify-between">
                        <span>Target Margin:</span>
                        <span className="font-mono text-slate-300">{base.assumptions.net_margin_end_pct}%</span>
                      </div>
                      <div className="flex justify-between">
                        <span>Discount:</span>
                        <span className="font-mono text-slate-300">{base.assumptions.discount_rate_pct}%</span>
                      </div>
                    </div>
                  )}
                </div>

                {/* Bull Case */}
                <div className="bg-black/40 border border-emerald-500/30 rounded-lg p-2.5 space-y-1.5">
                  <div className="flex items-center justify-between text-xs">
                    <span className="font-bold text-emerald-400 flex items-center gap-1">
                      <span>🐂</span> Bull Case
                    </span>
                    <span className="text-[10px] uppercase font-bold text-emerald-400/90">
                      {bull?.verdict || "Undervalued"}
                    </span>
                  </div>
                  <div className="text-base font-mono font-bold text-white">
                    {bull?.intrinsic_value != null ? `$${bull.intrinsic_value.toFixed(2)}` : "N/A"}
                  </div>
                  {bull?.upside_vs_price_pct != null && (
                    <div
                      className={`text-[11px] font-mono font-semibold ${
                        bull.upside_vs_price_pct >= 0 ? "text-emerald-400" : "text-rose-400"
                      }`}
                    >
                      {bull.upside_vs_price_pct >= 0 ? "+" : ""}
                      {bull.upside_vs_price_pct}% vs px
                    </div>
                  )}
                  {bull?.assumptions && (
                    <div className="pt-1 border-t border-border/40 text-[10px] text-muted space-y-0.5">
                      <div className="flex justify-between">
                        <span>Rev Growth:</span>
                        <span className="font-mono text-slate-300">{bull.assumptions.revenue_growth_pct}%</span>
                      </div>
                      <div className="flex justify-between">
                        <span>Target Margin:</span>
                        <span className="font-mono text-slate-300">{bull.assumptions.net_margin_end_pct}%</span>
                      </div>
                      <div className="flex justify-between">
                        <span>Discount:</span>
                        <span className="font-mono text-slate-300">{bull.assumptions.discount_rate_pct}%</span>
                      </div>
                    </div>
                  )}
                </div>
              </div>

              {alpha.page_url && (
                <div className="text-right">
                  <a
                    href={alpha.page_url}
                    target="_blank"
                    rel="noreferrer"
                    className="text-[10px] font-mono text-amber-400/80 hover:underline"
                  >
                    View model details on AlphaSpread.com ↗
                  </a>
                </div>
              )}
            </>
          ) : (
            <div className="text-xs text-muted py-3 text-center">
              {alpha?.error || "AlphaSpread intrinsic valuation not available for this ticker."}
            </div>
          )}
        </div>
      )}

      {/* Tab 3: Finviz Corporate Insider Activity */}
      {activeTab === "insider" && (
        <div className="space-y-3">
          {loadingExtras && !finviz && (
            <div className="text-xs text-muted py-4 text-center animate-pulse">
              Loading Finviz Form 4 filings…
            </div>
          )}

          {finviz && !finviz.error ? (
            <>
              {/* 90D Sentiment Summary */}
              {w90_insider && (
                <div className="flex flex-wrap items-center justify-between gap-2 p-2.5 rounded-lg bg-surface/60 border border-border/60 text-xs">
                  <div className="flex items-center gap-1.5">
                    <span className="text-muted font-bold">90D Net:</span>
                    <span
                      className={`font-semibold px-2 py-0.5 rounded text-[11px] ${
                        w90_insider.net_signal.includes("BUY")
                          ? "bg-emerald-500/20 text-emerald-300 border border-emerald-500/40"
                          : w90_insider.net_signal.includes("SELL")
                          ? "bg-rose-500/20 text-rose-300 border border-rose-500/40"
                          : "bg-surface text-muted"
                      }`}
                    >
                      {w90_insider.net_signal} ({w90_insider.net_value})
                    </span>
                  </div>
                  <div className="flex items-center gap-2 text-[11px] font-mono text-muted">
                    <span className="text-emerald-400 font-bold">+{w90_insider.open_market_buys} Buys ({w90_insider.buy_value})</span>
                    <span>·</span>
                    <span className="text-rose-400 font-bold">-{w90_insider.open_market_sells} Sells ({w90_insider.sell_value})</span>
                  </div>
                </div>
              )}

              {/* Transactions Table */}
              <div className="max-h-56 overflow-y-auto space-y-1 pr-1">
                {(finviz.recent_transactions || []).length > 0 ? (
                  finviz.recent_transactions!.map((tx, idx) => (
                    <div
                      key={idx}
                      className="flex items-center justify-between text-[11px] p-2 rounded bg-black/30 border border-border/30 hover:border-border/60 transition-colors"
                    >
                      <div className="min-w-0 pr-2">
                        <div className="font-semibold text-slate-200 truncate">{tx.insider}</div>
                        <div className="text-[10px] text-muted truncate">
                          {tx.relationship} · {tx.date}
                        </div>
                      </div>
                      <div className="text-right shrink-0 font-mono">
                        <div
                          className={`font-bold ${
                            tx.transaction.toLowerCase().includes("buy") || tx.transaction.toLowerCase().includes("purchase")
                              ? "text-emerald-400"
                              : tx.transaction.toLowerCase().includes("sale") || tx.transaction.toLowerCase().includes("sell")
                              ? "text-rose-400"
                              : "text-slate-300"
                          }`}
                        >
                          {tx.transaction}
                        </div>
                        <div className="text-[10px] text-muted">
                          {tx.shares ? `${tx.shares.toLocaleString()} sh` : ""}
                          {tx.value && tx.value !== "$0" ? ` · ${tx.value}` : ""}
                        </div>
                      </div>
                    </div>
                  ))
                ) : (
                  <div className="text-xs text-muted text-center py-2">No recent Form 4 insider transactions recorded.</div>
                )}
              </div>

              {finviz.page_url && (
                <div className="text-right">
                  <a
                    href={finviz.page_url}
                    target="_blank"
                    rel="noreferrer"
                    className="text-[10px] font-mono text-accent hover:underline"
                  >
                    View all filings on Finviz.com ↗
                  </a>
                </div>
              )}
            </>
          ) : (
            <div className="text-xs text-muted py-3 text-center">
              {finviz?.error || "Finviz insider filings not available for this ticker."}
            </div>
          )}
        </div>
      )}

      {/* Tab 4: Congress / Politician Trades */}
      {activeTab === "congress" && (
        <div className="space-y-3">
          {loadingExtras && !congress && (
            <div className="text-xs text-muted py-4 text-center animate-pulse">
              Loading Congressional STOCK Act disclosures…
            </div>
          )}

          {congress && !congress.error ? (
            <>
              {/* 90D Congress Sentiment */}
              {w90_congress && (
                <div className="flex flex-wrap items-center justify-between gap-2 p-2.5 rounded-lg bg-surface/60 border border-border/60 text-xs">
                  <div className="flex items-center gap-1.5">
                    <span className="text-muted font-bold">Congress 90D:</span>
                    <span
                      className={`font-semibold px-2 py-0.5 rounded text-[11px] ${
                        w90_congress.net_signal.includes("BUY")
                          ? "bg-emerald-500/20 text-emerald-300 border border-emerald-500/40"
                          : w90_congress.net_signal.includes("SELL")
                          ? "bg-rose-500/20 text-rose-300 border border-rose-500/40"
                          : "bg-surface text-muted"
                      }`}
                    >
                      {w90_congress.net_signal}
                    </span>
                  </div>
                  <div className="text-[11px] font-mono text-muted">
                    <span className="text-emerald-400 font-bold">+{w90_congress.purchases} Buys</span>
                    {" · "}
                    <span className="text-rose-400 font-bold">-{w90_congress.sales} Sales</span>
                  </div>
                </div>
              )}

              {/* Congress Trades List */}
              <div className="max-h-56 overflow-y-auto space-y-1 pr-1">
                {(congress.recent_trades || []).length > 0 ? (
                  congress.recent_trades!.map((ct, idx) => (
                    <div
                      key={idx}
                      className="flex items-center justify-between text-[11px] p-2 rounded bg-black/30 border border-border/30 hover:border-border/60 transition-colors"
                    >
                      <div className="min-w-0 pr-2">
                        <div className="font-semibold text-slate-200 truncate flex items-center gap-1.5">
                          <span>{ct.politician}</span>
                          {ct.party && (
                            <span
                              className={`text-[9px] px-1 rounded font-bold ${
                                ct.party === "D"
                                  ? "bg-blue-500/20 text-blue-300"
                                  : ct.party === "R"
                                  ? "bg-red-500/20 text-red-300"
                                  : "bg-slate-700 text-slate-300"
                              }`}
                            >
                              {ct.chamber || "Congress"} / {ct.party}
                            </span>
                          )}
                        </div>
                        <div className="text-[10px] text-muted">
                          Traded: {ct.traded}
                        </div>
                      </div>
                      <div className="text-right shrink-0 font-mono">
                        <span
                          className={`font-bold px-1.5 py-0.5 rounded text-[10px] ${
                            ct.transaction.toLowerCase().includes("purchase")
                              ? "bg-emerald-500/20 text-emerald-300 border border-emerald-500/40"
                              : "bg-rose-500/20 text-rose-300 border border-rose-500/40"
                          }`}
                        >
                          {ct.transaction}
                        </span>
                        {ct.amount_range && (
                          <div className="text-[10px] text-muted mt-0.5">{ct.amount_range}</div>
                        )}
                      </div>
                    </div>
                  ))
                ) : (
                  <div className="text-xs text-muted text-center py-2">
                    {congress.note || "No Congressional stock trades reported for this ticker."}
                  </div>
                )}
              </div>

              {congress.page_url && (
                <div className="text-right">
                  <a
                    href={congress.page_url}
                    target="_blank"
                    rel="noreferrer"
                    className="text-[10px] font-mono text-indigo-400/80 hover:underline"
                  >
                    View on Quiver Quantitative ↗
                  </a>
                </div>
              )}
            </>
          ) : (
            <div className="text-xs text-muted py-3 text-center">
              {congress?.error || "Congress trading disclosures not available for this ticker."}
            </div>
          )}
        </div>
      )}
    </div>
  );
}
