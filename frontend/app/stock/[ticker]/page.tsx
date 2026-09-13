"use client";

import { useState, useEffect, useCallback } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import Link from "next/link";
import { fetchAnalysis } from "@/lib/api";
import { BiasBadge } from "@/components/BiasCard";
import { fmtNum, fmtPrice } from "@/lib/format";
import SignalBanner from "@/components/SignalBanner";
import FibTable from "@/components/FibTable";
import FundamentalsCard from "@/components/FundamentalsCard";
import SRCard from "@/components/SRCard";
import DualChart from "@/components/DualChart";
import DailyTradeChart from "@/components/DailyTradeChart";
import ScoreCard from "@/components/ScoreCard";
import TradeCard from "@/components/TradeCard";
import VolumeProfileCard from "@/components/VolumeProfileCard";
import OptionsCard from "@/components/OptionsCard";
import StockVerdictCard from "@/components/StockVerdictCard";

interface StockPageProps {
  params: { ticker: string };
}

export default function StockPage({ params }: StockPageProps) {
  const ticker = params.ticker.toUpperCase();
  const router = useRouter();
  const searchParams = useSearchParams();

  const urlAsOf = searchParams.get("as_of") || searchParams.get("date") || "";

  const [asOfDate, setAsOfDate] = useState<string>(urlAsOf);
  const [inputDate, setInputDate] = useState<string>(urlAsOf);
  const [data, setData] = useState<any>(null);
  const [loading, setLoading] = useState<boolean>(true);
  const [error, setError] = useState<string | null>(null);
  const [chartView, setChartView] = useState<"daily_plan" | "dual_chart">("daily_plan");

  const loadData = useCallback(async (dateToFetch: string) => {
    setLoading(true);
    setError(null);
    try {
      const res = await fetchAnalysis(ticker, dateToFetch || undefined);
      setData(res);
    } catch (e: any) {
      setError(e.message || `Failed to fetch analysis for ${ticker}`);
    } finally {
      setLoading(false);
    }
  }, [ticker]);

  useEffect(() => {
    loadData(asOfDate);
  }, [asOfDate, loadData]);

  const handleApplyDate = (targetDate: string) => {
    setAsOfDate(targetDate);
    setInputDate(targetDate);
    if (targetDate) {
      router.push(`/stock/${ticker}?as_of=${targetDate}`, { scroll: false });
    } else {
      router.push(`/stock/${ticker}`, { scroll: false });
    }
  };

  const handlePreset = (daysAgo: number) => {
    const d = new Date();
    d.setDate(d.getDate() - daysAgo);
    // Format YYYY-MM-DD
    const iso = d.toISOString().split("T")[0];
    handleApplyDate(iso);
  };

  const handleResetLive = () => {
    handleApplyDate("");
  };

  if (loading && !data) {
    return (
      <div className="flex flex-col items-center justify-center min-h-[65vh] gap-4">
        <div className="w-10 h-10 border-4 border-[#4d9fff] border-t-transparent rounded-full animate-spin"></div>
        <p className="text-slate-300 font-mono text-sm">
          {asOfDate ? `Running backdated analysis for ${ticker} as of ${asOfDate}...` : `Loading live analysis for ${ticker}...`}
        </p>
      </div>
    );
  }

  if (error && !data) {
    return (
      <div className="flex flex-col items-center justify-center min-h-[60vh] gap-4">
        <p className="text-red-400 text-lg font-semibold">Failed to load {ticker}</p>
        <p className="text-slate-400 text-sm">{error}</p>
        <div className="flex gap-3">
          {asOfDate && (
            <button
              onClick={handleResetLive}
              className="bg-[#4d9fff] text-black font-bold px-4 py-2 rounded-lg text-sm"
            >
              Reset to Live (Today)
            </button>
          )}
          <button
            onClick={() => loadData(asOfDate)}
            className="bg-[#1a1d2e] text-white px-4 py-2 rounded-lg text-sm border border-slate-700"
          >
            Retry
          </button>
        </div>
      </div>
    );
  }

  const {
    bias = {},
    signal = { rank: 5, signal: "N/A", action: "N/A", key: "N/N/N" },
    fib_levels = {},
    nearest_fib = "N/A",
    support_resistance = { support: [], resistance: [] },
    weekly_fib_rsi = { weekly_fib: "N/A", rsi_4h: "N/A" },
    fundamentals = {},
    current_price = 0,
    verdict = "NEUTRAL",
    confidence = "N/A",
    score = 0,
    direction = "LONG",
    signal_names = [],
    entry_grade = { entry_grade: "D", entry_label: "N/A", expected_wr: 0, expected_avg: 0, grade_color: "#8b949e" },
    trade = { entry: 0, stop_loss: null, target1: null, target2: null, risk_pct: null, rr_t1: null, rr_t2: null, t1_days: null, t2_days: null, atr: 0 },
    volume_profile = null,
    strategy_signals = {},
    options = null,
    stock_verdict = null,
    is_backtest = false,
    as_of = null,
    backtest_outcome = null,
  } = data || {};

  const isWin = backtest_outcome?.is_win;
  const outcomeText = backtest_outcome?.outcome || "ACTIVE";

  return (
    <div className="space-y-6 max-w-screen-2xl mx-auto px-2 sm:px-4 py-4">

      {/* ── TIME MACHINE & BACKTEST CONTROL BAR ───────────────────────────── */}
      <div className={`p-4 rounded-xl border transition-all ${
        is_backtest
          ? "bg-gradient-to-r from-[#1c1917] via-[#1f1d13] to-[#0f172a] border-amber-500/50 shadow-lg shadow-amber-500/10"
          : "bg-[#0d0f17] border-[#1a1d2e]"
      }`}>
        <div className="flex flex-wrap items-center justify-between gap-4">
          <div className="flex items-center gap-3">
            <span className="text-2xl">{is_backtest ? "🕰️" : "⚡"}</span>
            <div>
              <div className="flex items-center gap-2">
                <span className="text-sm font-bold text-white uppercase tracking-wider">
                  {is_backtest ? "Backtest Time Machine Mode" : "Analysis Mode"}
                </span>
                {is_backtest ? (
                  <span className="px-2 py-0.5 rounded text-[10px] font-extrabold bg-amber-500 text-black uppercase">
                    Backdated to {as_of}
                  </span>
                ) : (
                  <span className="px-2 py-0.5 rounded text-[10px] font-bold bg-emerald-500/20 text-emerald-400 border border-emerald-500/30 uppercase">
                    🟢 Live Quotes (Today)
                  </span>
                )}
              </div>
              <p className="text-xs text-slate-400 mt-0.5">
                {is_backtest
                  ? "All Fibs, CPRe, EMAs, Signals & Trade Levels calculated strictly using past data available on this date."
                  : "Pick a past date to backtest all signals, projected trade levels, and verify what actually happened next."}
              </p>
            </div>
          </div>

          <div className="flex flex-wrap items-center gap-2">
            <div className="flex items-center gap-1 bg-[#131625] border border-[#252a42] rounded-lg px-2.5 py-1">
              <span className="text-xs text-slate-400 font-mono">Date:</span>
              <input
                type="date"
                value={inputDate}
                onChange={(e) => setInputDate(e.target.value)}
                className="bg-transparent text-white text-xs font-mono focus:outline-none cursor-pointer"
              />
            </div>

            <button
              onClick={() => handleApplyDate(inputDate)}
              disabled={!inputDate || inputDate === asOfDate || loading}
              className="bg-[#4d9fff] hover:bg-[#3b82f6] disabled:opacity-40 text-black font-bold text-xs px-3.5 py-2 rounded-lg transition-all flex items-center gap-1.5"
            >
              <span>🔬</span>
              <span>Run Backtest</span>
            </button>

            {is_backtest && (
              <button
                onClick={handleResetLive}
                disabled={loading}
                className="bg-emerald-500 hover:bg-emerald-400 text-black font-bold text-xs px-3 py-2 rounded-lg transition-all flex items-center gap-1"
              >
                <span>⚡</span>
                <span>Reset to Live</span>
              </button>
            )}
          </div>
        </div>

        {/* Quick presets row */}
        <div className="flex flex-wrap items-center gap-2 mt-3 pt-3 border-t border-slate-800/80 text-xs">
          <span className="text-slate-400 font-mono text-[11px] uppercase tracking-wider">Quick Presets:</span>
          {[
            { label: "1W Ago", days: 7 },
            { label: "1M Ago", days: 30 },
            { label: "3M Ago", days: 90 },
            { label: "6M Ago", days: 180 },
            { label: "1Y Ago", days: 365 },
          ].map((p) => (
            <button
              key={p.label}
              onClick={() => handlePreset(p.days)}
              disabled={loading}
              className="px-2.5 py-1 rounded bg-[#131625] hover:bg-[#202538] border border-slate-800 text-slate-300 font-mono text-[11px] transition-colors"
            >
              {p.label}
            </button>
          ))}
          {loading && (
            <span className="text-xs text-[#4d9fff] font-mono ml-auto animate-pulse flex items-center gap-1.5">
              <span className="inline-block w-2 h-2 rounded-full bg-[#4d9fff]"></span>
              Computing backdated matrix...
            </span>
          )}
        </div>
      </div>

      {/* ── BACKTEST OUTCOME RESULT BANNER (IF IN BACKTEST MODE) ─────────── */}
      {is_backtest && backtest_outcome && (
        <div className="bg-gradient-to-r from-[#0d1527] via-[#101e38] to-[#0d1527] border-2 border-[#4d9fff]/60 rounded-xl p-5 shadow-xl">
          <div className="flex flex-wrap items-center justify-between gap-4 border-b border-slate-700/60 pb-3 mb-4">
            <div className="flex items-center gap-3">
              <span className="text-3xl">
                {outcomeText.includes("TARGET 2") ? "🏆" : outcomeText.includes("TARGET 1") ? "🎯" : outcomeText.includes("STOP") ? "🛑" : "⚡"}
              </span>
              <div>
                <div className="flex items-center gap-2.5">
                  <h2 className="text-lg font-black text-white tracking-wide">
                    BACKTEST OUTCOME VERIFICATION
                  </h2>
                  <span className={`px-2.5 py-0.5 rounded text-xs font-black tracking-wider uppercase border ${
                    outcomeText.includes("TARGET 2")
                      ? "bg-emerald-500/20 text-emerald-300 border-emerald-500/50"
                      : outcomeText.includes("TARGET 1")
                      ? "bg-green-500/20 text-green-300 border-green-500/50"
                      : outcomeText.includes("STOP")
                      ? "bg-red-500/20 text-red-300 border-red-500/50"
                      : "bg-blue-500/20 text-blue-300 border-blue-500/50"
                  }`}>
                    {outcomeText}
                  </span>
                </div>
                <p className="text-xs text-slate-300 mt-0.5">
                  Projected trade entered at <b className="text-white">{fmtPrice(trade?.entry)}</b> on <b>{as_of}</b> following a <b>{verdict}</b> signal.
                </p>
              </div>
            </div>

            <div className="text-right">
              <div className={`text-2xl font-mono font-black ${
                (backtest_outcome.outcome_pnl_pct ?? 0) >= 0 ? "text-emerald-400" : "text-red-400"
              }`}>
                {(backtest_outcome.outcome_pnl_pct ?? 0) >= 0 ? "+" : ""}
                {backtest_outcome.outcome_pnl_pct}%
              </div>
              <div className="text-[11px] text-slate-400 font-mono">
                {backtest_outcome.outcome_days} trading days to outcome
              </div>
            </div>
          </div>

          <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-6 gap-3 text-xs font-mono">
            <div className="bg-[#090d1a] p-3 rounded-lg border border-slate-800">
              <span className="text-slate-400 block text-[10px] uppercase">Projected Entry</span>
              <span className="text-white font-bold text-sm">{fmtPrice(trade?.entry)}</span>
            </div>
            <div className="bg-[#090d1a] p-3 rounded-lg border border-slate-800">
              <span className="text-slate-400 block text-[10px] uppercase">Protective Stop</span>
              <span className="text-red-400 font-bold text-sm">{fmtPrice(trade?.stop_loss)}</span>
            </div>
            <div className="bg-[#090d1a] p-3 rounded-lg border border-slate-800">
              <span className="text-slate-400 block text-[10px] uppercase">Target 1</span>
              <span className="text-emerald-400 font-bold text-sm">{fmtPrice(trade?.target1)}</span>
            </div>
            <div className="bg-[#090d1a] p-3 rounded-lg border border-slate-800">
              <span className="text-slate-400 block text-[10px] uppercase">Outcome Date</span>
              <span className="text-yellow-400 font-bold text-sm">{backtest_outcome.outcome_date || "Still Open"}</span>
            </div>
            <div className="bg-[#090d1a] p-3 rounded-lg border border-slate-800">
              <span className="text-slate-400 block text-[10px] uppercase">Max Peak Run (MFE)</span>
              <span className="text-emerald-300 font-bold text-sm">+{backtest_outcome.mfe_pct}%</span>
            </div>
            <div className="bg-[#090d1a] p-3 rounded-lg border border-slate-800">
              <span className="text-slate-400 block text-[10px] uppercase">Max Drawdown (MAE)</span>
              <span className="text-red-300 font-bold text-sm">{backtest_outcome.mae_pct}%</span>
            </div>
          </div>
        </div>
      )}

      {/* ── HEADER & TICKER PRICE INFO ──────────────────────────────────── */}
      <div className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <div className="flex items-center gap-3">
            <h1 className="text-4xl font-extrabold text-white tracking-tight">{ticker}</h1>
            <span className={`px-2.5 py-1 rounded text-xs font-black uppercase ${
              verdict.includes("BULL") ? "bg-emerald-500/20 text-emerald-400 border border-emerald-500/30" :
              verdict.includes("BEAR") ? "bg-red-500/20 text-red-400 border border-red-500/30" :
              "bg-amber-500/20 text-amber-400 border border-amber-500/30"
            }`}>
              {verdict}
            </span>
            {entry_grade && (
              <span
                className="px-2 py-1 rounded text-xs font-black uppercase border"
                style={{ backgroundColor: `${entry_grade.grade_color}20`, borderColor: `${entry_grade.grade_color}50`, color: entry_grade.grade_color }}
              >
                Grade {entry_grade.entry_grade} · {entry_grade.entry_label}
              </span>
            )}
          </div>
          <p className="text-slate-400 text-sm mt-1">
            {fundamentals.name ? `${fundamentals.name} · ` : ""}{fundamentals.sector || "Equities"}
          </p>
        </div>

        <div className="text-right">
          <div className="text-3xl font-mono font-black text-white">
            {fmtPrice(current_price)}
          </div>
          <div className="text-xs text-slate-400 mt-0.5">
            {is_backtest ? `Closing price as of ${as_of}` : "Last close"}
          </div>
        </div>
      </div>

      {/* ── SIGNAL BANNER ────────────────────────────────────────────────── */}
      <SignalBanner signal={signal} direction={direction} />

      {/* ── STOCK VERDICT CARD ───────────────────────────────────────────── */}
      <StockVerdictCard ticker={ticker} verdict={stock_verdict} currentPrice={current_price} />

      {/* ── CHART VIEW CONTROLS & COMPONENT ──────────────────────────────── */}
      <div className="space-y-3">
        <div className="flex items-center justify-between border-b border-slate-800 pb-2">
          <div className="flex gap-2">
            <button
              onClick={() => setChartView("daily_plan")}
              className={`px-3 py-1.5 rounded-lg text-xs font-bold transition-all flex items-center gap-1.5 ${
                chartView === "daily_plan"
                  ? "bg-[#4d9fff] text-black shadow-md shadow-[#4d9fff]/20"
                  : "bg-[#131625] text-slate-400 hover:text-white border border-slate-800"
              }`}
            >
              <span>📈</span>
              <span>Daily Candlesticks & Entry/Exit Plan</span>
            </button>
            <button
              onClick={() => setChartView("dual_chart")}
              className={`px-3 py-1.5 rounded-lg text-xs font-bold transition-all flex items-center gap-1.5 ${
                chartView === "dual_chart"
                  ? "bg-[#4d9fff] text-black shadow-md shadow-[#4d9fff]/20"
                  : "bg-[#131625] text-slate-400 hover:text-white border border-slate-800"
              }`}
            >
              <span>📊</span>
              <span>Dual Chart (Intraday + 30W Curl)</span>
            </button>
          </div>
          <span className="text-xs text-slate-400 font-mono hidden sm:inline">
            {chartView === "daily_plan" ? "Interactive Entry, Stop & Targets with Candlesticks" : "30-Week MA Stage & Volume Profile"}
          </span>
        </div>

        {chartView === "daily_plan" ? (
          <DailyTradeChart ticker={ticker} initialAsOfDate={asOfDate} />
        ) : (
          <DualChart ticker={ticker} />
        )}
      </div>

      {/* ── SCORE + TRADE + VOLUME PROFILE ───────────────────────────────── */}
      <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
        <ScoreCard
          verdict={verdict}
          confidence={confidence}
          score={score}
          signals={signal_names}
          grade={entry_grade}
        />
        <TradeCard
          trade={trade}
          verdict={verdict}
          optionsStrategy={options?.strategy ?? null}
        />
        {volume_profile ? (
          <VolumeProfileCard vp={volume_profile} currentPrice={current_price} />
        ) : (
          <div className="bg-[#0d0f17] border border-[#1a1d2e] rounded-xl p-5 flex items-center justify-center text-slate-500 text-sm">
            Volume profile calculated from price bars
          </div>
        )}
      </div>

      {/* ── MTF BIAS + WTD + TECHNICAL SIGNALS ───────────────────────────── */}
      <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
        {/* MTF Bias */}
        <div className="bg-[#0d0f17] border border-[#1a1d2e] rounded-xl p-5 space-y-3">
          <h3 className="text-sm font-bold text-slate-300">Multi-Timeframe Bias</h3>
          {[
            { label: "Weekly", value: bias.weekly },
            { label: "Daily", value: bias.daily },
            { label: "4H", value: bias.h4 },
          ].map(({ label, value }) => (
            <div key={label} className="flex justify-between items-center">
              <span className="text-sm text-slate-400">{label}</span>
              <BiasBadge bias={value} />
            </div>
          ))}
          {bias.multiframe && (
            <>
              <hr className="border-slate-800" />
              <div className="flex justify-between items-center">
                <span className="text-xs text-slate-400">
                  {bias.multiframe.tf_short} vs {bias.multiframe.tf_long}
                </span>
                <BiasBadge bias={bias.multiframe.alignment} />
              </div>
              <p className="text-xs text-slate-400">{bias.multiframe.conclusion}</p>
            </>
          )}
        </div>

        {/* WTD + Key Stats */}
        <div className="bg-[#0d0f17] border border-[#1a1d2e] rounded-xl p-5 space-y-3">
          <h3 className="text-sm font-bold text-slate-300">Week-to-Date Metrics</h3>
          {[
            { label: "Weekly Fib Zone", value: weekly_fib_rsi.weekly_fib, color: "text-[#4d9fff]" },
            {
              label: "4H RSI",
              value: String(weekly_fib_rsi.rsi_4h),
              color: Number(weekly_fib_rsi.rsi_4h) >= 70 ? "text-red-400" :
                     Number(weekly_fib_rsi.rsi_4h) <= 30 ? "text-emerald-400" : "text-white"
            },
            { label: "Nearest Fib", value: nearest_fib, color: "text-amber-400" },
          ].map(({ label, value, color }) => (
            <div key={label} className="flex justify-between text-sm">
              <span className="text-slate-400">{label}</span>
              <span className={`font-mono font-semibold ${color}`}>{value}</span>
            </div>
          ))}
          <hr className="border-slate-800" />
          <h3 className="text-sm font-bold text-slate-300">Key Stats</h3>
          {[
            { label: "Market Cap", value: fundamentals.market_cap },
            { label: "P/E (TTM)", value: String(fundamentals.pe_ratio) },
            { label: "Beta", value: String(fundamentals.beta) },
          ].map(({ label, value }) => (
            <div key={label} className="flex justify-between text-sm">
              <span className="text-slate-400">{label}</span>
              <span className="font-mono text-white">{value ?? "N/A"}</span>
            </div>
          ))}
        </div>

        {/* Technical Signals */}
        <div className="bg-[#0d0f17] border border-[#1a1d2e] rounded-xl p-5 space-y-3">
          <h3 className="text-sm font-bold text-slate-300">Technical Signals</h3>
          {strategy_signals && !strategy_signals.error && (
            <>
              {[
                {
                  label: "MA10 vs MA30",
                  value: strategy_signals.ma10_below_ma30 ? "MA10 Below" : "MA10 Above",
                  color: strategy_signals.ma10_below_ma30 ? "text-red-400" : "text-emerald-400"
                },
                {
                  label: "Trend",
                  value: strategy_signals.is_uptrend ? "Uptrend" :
                         strategy_signals.is_downtrend ? "Downtrend" : "Sideways",
                  color: strategy_signals.is_uptrend ? "text-emerald-400" :
                         strategy_signals.is_downtrend ? "text-red-400" : "text-slate-400"
                },
                {
                  label: "Breakout Score",
                  value: `${strategy_signals.breakout_score}/3`,
                  color: strategy_signals.breakout_score >= 3 ? "text-emerald-400" :
                         strategy_signals.breakout_score >= 2 ? "text-amber-400" : "text-red-400"
                },
                {
                  label: "Price Position",
                  value: `${strategy_signals.price_position}% of 52W range`,
                  color: "text-white"
                },
                {
                  label: "Dist from 52W High",
                  value: `${strategy_signals.dist_from_high}%`,
                  color: strategy_signals.dist_from_high < 5 ? "text-red-400" :
                         strategy_signals.dist_from_high < 15 ? "text-amber-400" : "text-emerald-400"
                },
              ].map(({ label, value, color }) => (
                <div key={label} className="flex justify-between text-xs">
                  <span className="text-slate-400">{label}</span>
                  <span className={`font-mono font-semibold ${color}`}>{value}</span>
                </div>
              ))}
              {strategy_signals.fvg_details && (
                <div className={`text-xs px-2 py-1 rounded border font-mono ${
                  strategy_signals.fvg_details.type === "BULLISH"
                    ? "bg-emerald-500/10 text-emerald-400 border-emerald-500/30"
                    : "bg-red-500/10 text-red-400 border-red-500/30"
                }`}>
                  {strategy_signals.fvg_details.type} FVG ${strategy_signals.fvg_details.bottom}–${strategy_signals.fvg_details.top}
                  {" "}({strategy_signals.fvg_details.size_pct}%)
                </div>
              )}
            </>
          )}
        </div>
      </div>

      {/* ── FIB + S/R + FUNDAMENTALS ─────────────────────────────────────── */}
      <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
        <FibTable levels={fib_levels} nearestFib={nearest_fib} currentPrice={current_price} />
        <SRCard support={support_resistance.support} resistance={support_resistance.resistance} currentPrice={current_price} />
        <FundamentalsCard f={fundamentals} />
      </div>

      {/* ── OPTIONS ──────────────────────────────────────────────────────── */}
      {options && (
        <OptionsCard
          bias={options.bias ?? {}}
          strategy={options.strategy ?? null}
          isExceptional={options.is_exceptional ?? false}
        />
      )}

    </div>
  );
}
