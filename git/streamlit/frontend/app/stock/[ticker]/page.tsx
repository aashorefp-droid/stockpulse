import { fetchAnalysis } from "@/lib/api";
import { BiasBadge } from "@/components/BiasCard";
import SignalBanner from "@/components/SignalBanner";
import FibTable from "@/components/FibTable";
import FundamentalsCard from "@/components/FundamentalsCard";
import SRCard from "@/components/SRCard";
import DualChart from "@/components/DualChart";
import ScoreCard from "@/components/ScoreCard";
import TradeCard from "@/components/TradeCard";
import VolumeProfileCard from "@/components/VolumeProfileCard";
import OptionsCard from "@/components/OptionsCard";

export const revalidate = 60;

export default async function StockPage({
  params,
}: {
  params: { ticker: string };
}) {
  const ticker = params.ticker.toUpperCase();

  let data: any;
  try {
    data = await fetchAnalysis(ticker);
  } catch (e: any) {
    return (
      <div className="flex flex-col items-center justify-center min-h-[60vh] gap-4">
        <p className="text-red text-lg font-semibold">Failed to load {ticker}</p>
        <p className="text-muted text-sm">{e.message}</p>
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
    chart_data = [],
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
  } = data;

  return (
    <div className="space-y-6">

      {/* ── Header ───────────────────────────────────────────────────────────── */}
      <div className="flex flex-wrap items-end gap-4">
        <div>
          <h1 className="text-4xl font-bold text-white">{ticker}</h1>
          <p className="text-muted text-sm mt-0.5">{fundamentals.name} · {fundamentals.sector}</p>
        </div>
        <div className="ml-auto text-right">
          <div className="text-3xl font-mono font-bold text-white">${current_price.toFixed(2)}</div>
          <div className="text-xs text-muted mt-0.5">Last close</div>
        </div>
      </div>

      {/* ── Signal Banner ─────────────────────────────────────────────────────── */}
      <SignalBanner signal={signal} direction={direction} />

      {/* ── Chart ─────────────────────────────────────────────────────────────── */}
      <DualChart ticker={ticker} />

      {/* ── Score + Trade + Volume Profile ────────────────────────────────────── */}
      <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
        <ScoreCard
          verdict={verdict} confidence={confidence}
          score={score} signals={signal_names} grade={entry_grade}
        />
        <TradeCard trade={trade} verdict={verdict} optionsStrategy={options?.strategy ?? null} />
        {volume_profile
          ? <VolumeProfileCard vp={volume_profile} currentPrice={current_price} />
          : <div className="card flex items-center justify-center text-muted text-sm">Volume profile unavailable</div>
        }
      </div>

      {/* ── MTF Bias + WTD + Technical Signals ───────────────────────────────── */}
      <div className="grid grid-cols-1 md:grid-cols-3 gap-4">

        {/* MTF Bias */}
        <div className="card space-y-3">
          <h3 className="text-sm font-semibold text-muted">Multi-Timeframe Bias</h3>
          {[
            { label: "Weekly", value: bias.weekly },
            { label: "Daily",  value: bias.daily },
            { label: "4H",     value: bias.h4 },
          ].map(({ label, value }) => (
            <div key={label} className="flex justify-between items-center">
              <span className="text-sm text-muted">{label}</span>
              <BiasBadge bias={value} />
            </div>
          ))}
          {bias.multiframe && (
            <>
              <hr className="border-border" />
              <div className="flex justify-between items-center">
                <span className="text-xs text-muted">
                  {bias.multiframe.tf_short} vs {bias.multiframe.tf_long}
                </span>
                <BiasBadge bias={bias.multiframe.alignment} />
              </div>
              <p className="text-xs text-muted">{bias.multiframe.conclusion}</p>
            </>
          )}
        </div>

        {/* WTD + Key Stats */}
        <div className="card space-y-3">
          <h3 className="text-sm font-semibold text-muted">Week-to-Date Metrics</h3>
          {[
            { label: "Weekly Fib Zone", value: weekly_fib_rsi.weekly_fib, color: "text-accent" },
            { label: "4H RSI",          value: String(weekly_fib_rsi.rsi_4h),
              color: Number(weekly_fib_rsi.rsi_4h) >= 70 ? "text-red" :
                     Number(weekly_fib_rsi.rsi_4h) <= 30 ? "text-green" : "text-white" },
            { label: "Nearest Fib",     value: nearest_fib, color: "text-yellow" },
          ].map(({ label, value, color }) => (
            <div key={label} className="flex justify-between text-sm">
              <span className="text-muted">{label}</span>
              <span className={`font-mono ${color}`}>{value}</span>
            </div>
          ))}
          <hr className="border-border" />
          <h3 className="text-sm font-semibold text-muted">Key Stats</h3>
          {[
            { label: "Market Cap", value: fundamentals.market_cap },
            { label: "P/E (TTM)",  value: String(fundamentals.pe_ratio) },
            { label: "Beta",       value: String(fundamentals.beta) },
          ].map(({ label, value }) => (
            <div key={label} className="flex justify-between text-sm">
              <span className="text-muted">{label}</span>
              <span className="font-mono text-white">{value ?? "N/A"}</span>
            </div>
          ))}
        </div>

        {/* Technical Signals */}
        <div className="card space-y-3">
          <h3 className="text-sm font-semibold text-muted">Technical Signals</h3>
          {strategy_signals && !strategy_signals.error && (
            <>
              {[
                { label: "MA10 vs MA30", value: strategy_signals.ma10_below_ma30 ? "MA10 Below" : "MA10 Above",
                  color: strategy_signals.ma10_below_ma30 ? "text-red" : "text-green" },
                { label: "Trend",        value: strategy_signals.is_uptrend ? "Uptrend" :
                                                strategy_signals.is_downtrend ? "Downtrend" : "Sideways",
                  color: strategy_signals.is_uptrend ? "text-green" :
                         strategy_signals.is_downtrend ? "text-red" : "text-muted" },
                { label: "Breakout Score", value: `${strategy_signals.breakout_score}/3`,
                  color: strategy_signals.breakout_score >= 3 ? "text-green" :
                         strategy_signals.breakout_score >= 2 ? "text-yellow" : "text-red" },
                { label: "Price Position", value: `${strategy_signals.price_position}% of 52W range`,
                  color: "text-white" },
                { label: "Dist from 52W High", value: `${strategy_signals.dist_from_high}%`,
                  color: strategy_signals.dist_from_high < 5 ? "text-red" :
                         strategy_signals.dist_from_high < 15 ? "text-yellow" : "text-green" },
              ].map(({ label, value, color }) => (
                <div key={label} className="flex justify-between text-xs">
                  <span className="text-muted">{label}</span>
                  <span className={`font-mono font-semibold ${color}`}>{value}</span>
                </div>
              ))}
              {strategy_signals.fvg_details && (
                <div className={`text-xs px-2 py-1 rounded border font-mono ${
                  strategy_signals.fvg_details.type === "BULLISH"
                    ? "bg-green/5 text-green border-green/20"
                    : "bg-red/5 text-red border-red/20"
                }`}>
                  {strategy_signals.fvg_details.type} FVG ${strategy_signals.fvg_details.bottom}–${strategy_signals.fvg_details.top}
                  {" "}({strategy_signals.fvg_details.size_pct}%)
                </div>
              )}
            </>
          )}
        </div>
      </div>

      {/* ── Fib + S/R + Fundamentals ──────────────────────────────────────────── */}
      <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
        <FibTable levels={fib_levels} nearestFib={nearest_fib} currentPrice={current_price} />
        <SRCard support={support_resistance.support} resistance={support_resistance.resistance} currentPrice={current_price} />
        <FundamentalsCard f={fundamentals} />
      </div>

      {/* ── Options ───────────────────────────────────────────────────────────── */}
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
