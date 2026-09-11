"use client";

import { useState, useEffect, useRef } from "react";

declare global {
  interface Window {
    TradingView: any;
  }
}

const API_BASE = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

interface DailyTradeChartProps {
  ticker: string;
  onTickerChange?: (ticker: string) => void;
  availableTickers?: string[];
  initialAsOfDate?: string;
}

export default function DailyTradeChart({
  ticker,
  onTickerChange,
  availableTickers = [],
  initialAsOfDate = "",
}: DailyTradeChartProps) {
  const containerRef = useRef<HTMLDivElement>(null);
  const [loading, setLoading] = useState(true);
  const [data, setData] = useState<any>(null);
  const [viewMode, setViewMode] = useState<"levels" | "embed" | "finviz">("levels");
  const [showSma, setShowSma] = useState(true);

  // Backtest state
  const [backtestMode, setBacktestMode] = useState(Boolean(initialAsOfDate));
  const [asOfDate, setAsOfDate] = useState(initialAsOfDate);
  const [showHistoryModal, setShowHistoryModal] = useState(false);
  const [historyLoading, setHistoryLoading] = useState(false);
  const [historyData, setHistoryData] = useState<any>(null);
  const [historyDays, setHistoryDays] = useState(365);

  // Fetch daily chart data with calculated trade levels (and backtest outcome if asOfDate is active)
  useEffect(() => {
    let isMounted = true;
    setLoading(true);

    let url = `${API_BASE}/api/analysis/chart-daily/${ticker}`;
    if (backtestMode && asOfDate) {
      url += `?as_of=${asOfDate}`;
    }

    fetch(url)
      .then((res) => res.json())
      .then((json) => {
        if (!isMounted) return;
        setData(json);
        setLoading(false);
      })
      .catch((err) => {
        console.error("Failed to load daily chart data:", err);
        if (isMounted) setLoading(false);
      });

    return () => {
      isMounted = false;
    };
  }, [ticker, backtestMode, asOfDate]);

  // Render Lightweight Charts when in "levels" mode
  useEffect(() => {
    if (viewMode !== "levels" || !data || !data.bars || data.bars.length === 0) return;
    if (!containerRef.current) return;

    let chart: any;
    let isMounted = true;

    async function initChart() {
      const { createChart, CrosshairMode, LineStyle } = await import("lightweight-charts");
      if (!isMounted || !containerRef.current) return;

      containerRef.current.innerHTML = "";

      chart = createChart(containerRef.current, {
        width: containerRef.current.clientWidth,
        height: 520,
        layout: { background: { color: "#0a0b14" }, textColor: "#8b949e" },
        grid: {
          vertLines: { color: "rgba(255,255,255,0.03)" },
          horzLines: { color: "rgba(255,255,255,0.03)" },
        },
        crosshair: { mode: CrosshairMode.Normal },
        rightPriceScale: {
          borderColor: "#202438",
          scaleMargins: {
            top: 0.08,
            bottom: 0.22, // Reserve bottom 22% for volume
          },
        },
        timeScale: { borderColor: "#202438", timeVisible: false },
      });

      // 1. Volume Series
      const volumeSeries = chart.addHistogramSeries({
        color: "#26a69a",
        priceFormat: { type: "volume" },
        priceScaleId: "volume",
      });
      chart.priceScale("volume").applyOptions({
        scaleMargins: { top: 0.8, bottom: 0 },
      });
      if (data.volume_bars && data.volume_bars.length > 0) {
        volumeSeries.setData(data.volume_bars);
      }

      // 2. Candlestick Series
      const candleSeries = chart.addCandlestickSeries({
        upColor: "#00e5a0",
        downColor: "#ff4d6a",
        borderUpColor: "#00e5a0",
        borderDownColor: "#ff4d6a",
        wickUpColor: "#00e5a0",
        wickDownColor: "#ff4d6a",
      });
      candleSeries.setData(data.bars);

      // 3. SMA Overlays (20, 50, 200)
      if (showSma) {
        if (data.sma20 && data.sma20.length > 0) {
          const sma20Series = chart.addLineSeries({
            color: "#60a5fa",
            lineWidth: 1,
            title: "20 SMA",
            priceLineVisible: false,
            lastValueVisible: false,
          });
          sma20Series.setData(data.sma20);
        }
        if (data.sma50 && data.sma50.length > 0) {
          const sma50Series = chart.addLineSeries({
            color: "#f59e0b",
            lineWidth: 1.5,
            title: "50 SMA",
            priceLineVisible: false,
            lastValueVisible: false,
          });
          sma50Series.setData(data.sma50);
        }
        if (data.sma200 && data.sma200.length > 0) {
          const sma200Series = chart.addLineSeries({
            color: "#a855f7",
            lineWidth: 2,
            title: "200 SMA",
            priceLineVisible: false,
            lastValueVisible: false,
          });
          sma200Series.setData(data.sma200);
        }
      }

      // 4. Horizontal Trade Price Lines
      const { entry, stop_loss, target1, target2 } = data.levels || {};
      
      // If backtest mode, draw lines from entry date onwards
      let activeBars = data.bars.slice(-40);
      if (data.as_of) {
        const asOfIdx = data.bars.findIndex((b: any) => b.time >= data.as_of);
        if (asOfIdx >= 0) {
          activeBars = data.bars.slice(Math.max(0, asOfIdx - 5));
        }
      }

      // Best Entry Line (Solid Emerald)
      if (entry && activeBars.length > 0) {
        const entryLine = chart.addLineSeries({
          color: "#00e5a0",
          lineWidth: 2,
          lineStyle: LineStyle.Solid,
          title: `ENTRY $${entry}`,
          priceLineVisible: true,
          lastValueVisible: true,
        });
        entryLine.setData(activeBars.map((b: any) => ({ time: b.time, value: entry })));
      }

      // Stop Loss Line (Dashed Red)
      if (stop_loss && activeBars.length > 0) {
        const stopLine = chart.addLineSeries({
          color: "#ff4d6a",
          lineWidth: 2,
          lineStyle: LineStyle.Dashed,
          title: `STOP $${stop_loss}`,
          priceLineVisible: true,
          lastValueVisible: true,
        });
        stopLine.setData(activeBars.map((b: any) => ({ time: b.time, value: stop_loss })));
      }

      // Target 1 Exit Line (Dashed Green)
      if (target1 && activeBars.length > 0) {
        const t1Line = chart.addLineSeries({
          color: "#34d399",
          lineWidth: 2,
          lineStyle: LineStyle.Dashed,
          title: `TARGET 1 $${target1}`,
          priceLineVisible: true,
          lastValueVisible: true,
        });
        t1Line.setData(activeBars.map((b: any) => ({ time: b.time, value: target1 })));
      }

      // Target 2 Exit Line (Dashed Cyan)
      if (target2 && activeBars.length > 0) {
        const t2Line = chart.addLineSeries({
          color: "#38bdf8",
          lineWidth: 2,
          lineStyle: LineStyle.Dashed,
          title: `TARGET 2 $${target2}`,
          priceLineVisible: true,
          lastValueVisible: true,
        });
        t2Line.setData(activeBars.map((b: any) => ({ time: b.time, value: target2 })));
      }

      // 5. Markers (Entry trigger + Exit outcome marker)
      if (data.markers && data.markers.length > 0) {
        candleSeries.setMarkers(data.markers);
      }

      chart.timeScale().fitContent();

      const handleResize = () => {
        if (chart && containerRef.current) {
          chart.applyOptions({ width: containerRef.current.clientWidth });
        }
      };
      window.addEventListener("resize", handleResize);
    }

    initChart();

    return () => {
      isMounted = false;
      chart?.remove();
    };
  }, [viewMode, data, showSma]);

  // Render TradingView Embed widget when selected
  useEffect(() => {
    if (viewMode !== "embed") return;
    const containerId = `tv_daily_embed_${ticker}`;

    function initTV() {
      if (!window.TradingView) return;
      const el = document.getElementById(containerId);
      if (!el) return;
      el.innerHTML = "";

      new window.TradingView.widget({
        container_id: containerId,
        autosize: true,
        symbol: ticker,
        interval: "D",
        timezone: "America/New_York",
        theme: "dark",
        style: "1",
        locale: "en",
        toolbar_bg: "#131625",
        backgroundColor: "#0a0b14",
        gridColor: "rgba(255,255,255,0.04)",
        enable_publishing: false,
        hide_top_toolbar: false,
        allow_symbol_change: true,
        save_image: true,
        studies: ["Volume@tv-basicstudies", "MASimple@tv-basicstudies"],
      });
    }

    if (window.TradingView) {
      initTV();
    } else {
      const script = document.createElement("script");
      script.src = "https://s3.tradingview.com/tv.js";
      script.async = true;
      script.onload = initTV;
      document.head.appendChild(script);
    }

    return () => {
      const el = document.getElementById(containerId);
      if (el) el.innerHTML = "";
    };
  }, [viewMode, ticker]);

  function setPresetDaysAgo(daysAgo: number) {
    const d = new Date();
    d.setDate(d.getDate() - daysAgo);
    setAsOfDate(d.toISOString().slice(0, 10));
    setBacktestMode(true);
  }

  function runHistoryBacktest(days: number = 365) {
    setHistoryLoading(true);
    setShowHistoryModal(true);
    setHistoryDays(days);
    fetch(`${API_BASE}/api/analysis/backtest-history/${ticker}?days=${days}`)
      .then((res) => res.json())
      .then((json) => {
        setHistoryData(json);
        setHistoryLoading(false);
      })
      .catch((err) => {
        console.error("Backtest history error:", err);
        setHistoryLoading(false);
      });
  }

  const levels = data?.levels || {};
  const isBull = data?.direction === "LONG";
  const bt = data?.backtest;

  return (
    <div className="bg-[#0d0f17] border border-[#1a1d2e] rounded-xl overflow-hidden shadow-lg mb-8">
      {/* ── Top Control Bar ─────────────────────────────────────────── */}
      <div className="p-4 border-b border-[#1a1d2e] flex flex-wrap items-center justify-between gap-4 bg-gradient-to-r from-[#0d0f17] to-[#131625]">
        <div className="flex flex-wrap items-center gap-3">
          <div className="flex items-center gap-2">
            <span className="text-xl">📈</span>
            <span className="text-lg font-bold text-white tracking-wide">{ticker}</span>
            <span className="text-sm font-mono text-[#e8ecff] font-semibold">
              ${data?.current_price?.toFixed(2) ?? "—"}
            </span>
            {data?.as_of && (
              <span className="text-[11px] font-mono text-[#f5c842] bg-[#3d3a0a] px-2 py-0.5 rounded border border-[#f5c842]/40">
                Backtest: {data.as_of}
              </span>
            )}
          </div>

          <span
            className={`px-2.5 py-0.5 rounded text-xs font-bold border ${
              isBull
                ? "bg-[#0a3d1f] text-[#00e5a0] border-[#00e5a0]/30"
                : "bg-[#3d0a1a] text-[#ff4d6a] border-[#ff4d6a]/30"
            }`}
          >
            {data?.verdict ?? "NEUTRAL"} · {isBull ? "LONG" : "SHORT"}
          </span>

          {availableTickers.length > 0 && onTickerChange && (
            <div className="flex items-center gap-1.5 ml-2">
              <span className="text-xs text-[#6b7099]">Switch:</span>
              <div className="flex flex-wrap gap-1 max-w-md overflow-x-auto py-1">
                {availableTickers.slice(0, 8).map((t) => (
                  <button
                    key={t}
                    onClick={() => onTickerChange(t)}
                    className={`px-2 py-0.5 rounded text-xs font-mono font-semibold transition-all ${
                      ticker === t
                        ? "bg-[#4d9fff] text-black font-bold"
                        : "bg-[#131625] text-[#6b7099] hover:text-white border border-[#1a1d2e]"
                    }`}
                  >
                    {t}
                  </button>
                ))}
              </div>
            </div>
          )}
        </div>

        {/* View mode and Backtest actions */}
        <div className="flex flex-wrap items-center gap-2">
          {/* Backtest Toggle Button */}
          <button
            onClick={() => {
              if (backtestMode) {
                setBacktestMode(false);
                setAsOfDate("");
              } else {
                setBacktestMode(true);
                if (!asOfDate) setPresetDaysAgo(30);
              }
            }}
            className={`px-3 py-1 rounded text-xs font-semibold border flex items-center gap-1.5 transition-all ${
              backtestMode
                ? "bg-[#f5c842] text-black border-[#f5c842] font-bold shadow-md shadow-[#f5c842]/20"
                : "bg-[#131625] text-[#e8ecff] hover:text-[#f5c842] border-[#1a1d2e]"
            }`}
          >
            <span>⏪</span>
            <span>{backtestMode ? "Exit Backtest" : "Backtest Mode"}</span>
          </button>

          {/* Full Backtest Report Button */}
          <button
            onClick={() => runHistoryBacktest(historyDays)}
            className="px-3 py-1 rounded text-xs font-semibold bg-[#1a2d3d] hover:bg-[#25394d] text-[#4d9fff] border border-[#4d9fff]/40 flex items-center gap-1.5 transition-all"
          >
            <span>📊</span>
            <span>1-Yr Backtest Report</span>
          </button>

          {viewMode === "levels" && (
            <button
              onClick={() => setShowSma(!showSma)}
              className={`px-2.5 py-1 rounded text-xs font-semibold border transition-all ${
                showSma
                  ? "bg-[#1a2d3d] text-[#4d9fff] border-[#4d9fff]/40"
                  : "bg-[#131625] text-[#6b7099] border-[#1a1d2e]"
              }`}
            >
              MAs (20/50/200)
            </button>
          )}

          <div className="flex items-center bg-[#131625] p-1 rounded-lg border border-[#1a1d2e]">
            <button
              onClick={() => setViewMode("levels")}
              className={`px-3 py-1 rounded text-xs font-semibold transition-all ${
                viewMode === "levels"
                  ? "bg-[#4d9fff] text-black font-bold shadow"
                  : "text-[#6b7099] hover:text-white"
              }`}
            >
              🎯 Entry / Exit Levels
            </button>
            <button
              onClick={() => setViewMode("embed")}
              className={`px-3 py-1 rounded text-xs font-semibold transition-all ${
                viewMode === "embed"
                  ? "bg-[#4d9fff] text-black font-bold shadow"
                  : "text-[#6b7099] hover:text-white"
              }`}
            >
              TradingView Widget
            </button>
            <button
              onClick={() => setViewMode("finviz")}
              className={`px-3 py-1 rounded text-xs font-semibold transition-all ${
                viewMode === "finviz"
                  ? "bg-[#4d9fff] text-black font-bold shadow"
                  : "text-[#6b7099] hover:text-white"
              }`}
            >
              Finviz Daily
            </button>
          </div>
        </div>
      </div>

      {/* ── Backtest Date Picker Bar ───────────────────────────────── */}
      {backtestMode && (
        <div className="bg-[#131625] px-4 py-2.5 border-b border-[#1a1d2e] flex flex-wrap items-center justify-between gap-3 text-xs">
          <div className="flex flex-wrap items-center gap-3">
            <span className="font-semibold text-[#f5c842] flex items-center gap-1">
              <span>⏪</span>
              <span>Backtest Entry Date:</span>
            </span>
            <input
              type="date"
              value={asOfDate}
              onChange={(e) => {
                setAsOfDate(e.target.value);
                setBacktestMode(true);
              }}
              className="bg-[#0a0b14] border border-[#1a1d2e] rounded px-2.5 py-1 text-white font-mono text-xs focus:outline-none focus:border-[#f5c842]"
            />

            {/* Quick Presets */}
            <div className="flex items-center gap-1.5 ml-2">
              <span className="text-[#6b7099]">Presets:</span>
              {[
                { label: "1W", days: 7 },
                { label: "2W", days: 14 },
                { label: "1M", days: 30 },
                { label: "2M", days: 60 },
                { label: "3M", days: 90 },
                { label: "6M", days: 180 },
              ].map((p) => (
                <button
                  key={p.label}
                  onClick={() => setPresetDaysAgo(p.days)}
                  className="px-2 py-0.5 rounded bg-[#1a1d2e] hover:bg-[#252a42] text-[#e8ecff] text-[11px] font-mono transition-colors"
                >
                  {p.label}
                </button>
              ))}
            </div>
          </div>

          <button
            onClick={() => {
              setBacktestMode(false);
              setAsOfDate("");
            }}
            className="text-[11px] text-[#6b7099] hover:text-white underline"
          >
            Reset to Live View
          </button>
        </div>
      )}

      {/* ── Backtest Outcome Result Banner ──────────────────────────── */}
      {bt && (
        <div
          className={`px-4 py-3 border-b flex flex-wrap items-center justify-between gap-3 text-xs ${
            bt.is_win
              ? "bg-[#0a3d1f]/40 border-[#00e5a0]/40 text-[#00e5a0]"
              : bt.outcome === "STOPPED OUT"
              ? "bg-[#3d0a1a]/40 border-[#ff4d6a]/40 text-[#ff4d6a]"
              : "bg-[#1a2d3d]/40 border-[#4d9fff]/40 text-[#4d9fff]"
          }`}
        >
          <div className="flex items-center gap-2.5">
            <span className="text-xl">
              {bt.is_win ? "🏆" : bt.outcome === "STOPPED OUT" ? "🛑" : "⏳"}
            </span>
            <div>
              <div className="font-bold text-sm tracking-wide">
                BACKTEST OUTCOME: {bt.outcome} ({bt.outcome_pnl_pct >= 0 ? `+${bt.outcome_pnl_pct}` : bt.outcome_pnl_pct}%)
              </div>
              <div className="text-[11px] opacity-80 mt-0.5">
                Entered on <b className="font-mono">{bt.as_of}</b> · Exit reached on{" "}
                <b className="font-mono">{bt.outcome_date ?? "Ongoing"}</b> in{" "}
                <b className="font-mono">{bt.outcome_days} trading days</b>.
              </div>
            </div>
          </div>

          <div className="flex items-center gap-4 font-mono text-xs">
            <span>
              Max Run-up: <b className="text-[#00e5a0]">+{bt.mfe_pct}%</b>
            </span>
            <span>
              Max Drawdown: <b className="text-[#ff4d6a]">{bt.mae_pct}%</b>
            </span>
            <span>
              Evaluated Across: <b>{bt.subsequent_bars_count} bars</b>
            </span>
          </div>
        </div>
      )}

      {/* ── Key Trade Levels Summary Cards ──────────────────────────── */}
      {levels.entry != null && (
        <div className="grid grid-cols-2 md:grid-cols-4 gap-3 p-4 bg-[#0a0b14] border-b border-[#1a1d2e]">
          {/* Best Entry Point */}
          <div className="bg-[#0f1d18] border border-[#00e5a0]/30 rounded-xl p-3 shadow-sm">
            <div className="flex items-center justify-between text-xs text-[#6b7099] mb-1 font-semibold uppercase tracking-wider">
              <span>{backtestMode ? "Simulated Entry" : "Best Entry Point"}</span>
              <span className="text-[#00e5a0]">🟢 TRIGGER</span>
            </div>
            <div className="text-2xl font-mono font-extrabold text-[#00e5a0]">
              ${levels.entry?.toFixed(2)}
            </div>
            <div className="text-[11px] text-[#00e5a0]/80 mt-1 font-mono">
              Zone: ${levels.entry_zone_min?.toFixed(2)} – ${levels.entry_zone_max?.toFixed(2)}
            </div>
          </div>

          {/* Protective Stop Loss */}
          <div className="bg-[#1f0f14] border border-[#ff4d6a]/30 rounded-xl p-3 shadow-sm">
            <div className="flex items-center justify-between text-xs text-[#6b7099] mb-1 font-semibold uppercase tracking-wider">
              <span>Stop Loss</span>
              <span className="text-[#ff4d6a]">🔴 RISK</span>
            </div>
            <div className="text-2xl font-mono font-extrabold text-[#ff4d6a]">
              ${levels.stop_loss?.toFixed(2)}
            </div>
            <div className="text-[11px] text-[#ff4d6a]/80 mt-1 font-mono">
              Risk: -{levels.risk_pct?.toFixed(1)}% ({isBull ? "Below" : "Above"} ATR buffer)
            </div>
          </div>

          {/* Target 1 Exit Point */}
          <div className="bg-[#0f1d1f] border border-[#34d399]/30 rounded-xl p-3 shadow-sm">
            <div className="flex items-center justify-between text-xs text-[#6b7099] mb-1 font-semibold uppercase tracking-wider">
              <span>Target 1 (Primary Exit)</span>
              <span className="text-[#34d399]">🎯 {levels.rr_t1?.toFixed(1)}R</span>
            </div>
            <div className="text-2xl font-mono font-extrabold text-[#34d399]">
              ${levels.target1?.toFixed(2)}
            </div>
            <div className="text-[11px] text-[#34d399]/80 mt-1 font-mono">
              +{levels.target1_gain_pct?.toFixed(1)}% gain · ~{levels.t1_days ?? 5} trading days
            </div>
          </div>

          {/* Target 2 Exit Point */}
          <div className="bg-[#0f172a] border border-[#38bdf8]/30 rounded-xl p-3 shadow-sm">
            <div className="flex items-center justify-between text-xs text-[#6b7099] mb-1 font-semibold uppercase tracking-wider">
              <span>Target 2 (Runner Exit)</span>
              <span className="text-[#38bdf8]">🚀 {levels.rr_t2?.toFixed(1)}R</span>
            </div>
            <div className="text-2xl font-mono font-extrabold text-[#38bdf8]">
              ${levels.target2?.toFixed(2)}
            </div>
            <div className="text-[11px] text-[#38bdf8]/80 mt-1 font-mono">
              +{levels.target2_gain_pct?.toFixed(1)}% gain · ~{levels.t2_days ?? 10} trading days
            </div>
          </div>
        </div>
      )}

      {/* ── Chart Container ─────────────────────────────────────────── */}
      <div className="relative min-h-[520px] bg-[#0a0b14]">
        {loading && (
          <div className="absolute inset-0 z-20 flex flex-col items-center justify-center bg-[#0a0b14]/80 text-[#6b7099] text-xs gap-2">
            <div className="w-5 h-5 border-2 border-[#4d9fff] border-t-transparent rounded-full animate-spin"></div>
            <span>Loading daily candlestick data and calculating entry/exit targets...</span>
          </div>
        )}

        {viewMode === "levels" && (
          <div>
            {/* Chart legend overlay */}
            <div className="absolute top-3 left-3 z-10 bg-[#0d0f17]/90 backdrop-blur border border-[#1a1d2e] rounded-lg px-3 py-1.5 text-[11px] flex flex-wrap items-center gap-3 font-mono shadow-md">
              <span className="flex items-center gap-1.5 text-[#00e5a0]">
                <span className="w-2.5 h-0.5 bg-[#00e5a0] rounded"></span>
                <span>Best Entry: ${levels.entry?.toFixed(2) ?? "—"}</span>
              </span>
              <span className="text-[#6b7099]">·</span>
              <span className="flex items-center gap-1.5 text-[#ff4d6a]">
                <span className="w-2.5 h-0.5 bg-[#ff4d6a] rounded border-b border-dashed"></span>
                <span>Stop: ${levels.stop_loss?.toFixed(2) ?? "—"}</span>
              </span>
              <span className="text-[#6b7099]">·</span>
              <span className="flex items-center gap-1.5 text-[#34d399]">
                <span className="w-2.5 h-0.5 bg-[#34d399] rounded border-b border-dashed"></span>
                <span>Target 1: ${levels.target1?.toFixed(2) ?? "—"}</span>
              </span>
              <span className="text-[#6b7099]">·</span>
              <span className="flex items-center gap-1.5 text-[#38bdf8]">
                <span className="w-2.5 h-0.5 bg-[#38bdf8] rounded border-b border-dashed"></span>
                <span>Target 2: ${levels.target2?.toFixed(2) ?? "—"}</span>
              </span>
              {showSma && (
                <>
                  <span className="text-[#6b7099]">·</span>
                  <span className="text-[#60a5fa]">20 SMA</span>
                  <span className="text-[#f59e0b]">50 SMA</span>
                  <span className="text-[#a855f7]">200 SMA</span>
                </>
              )}
            </div>

            <div ref={containerRef} style={{ height: 520 }} />
          </div>
        )}

        {viewMode === "embed" && (
          <div id={`tv_daily_embed_${ticker}`} style={{ height: 520 }} />
        )}

        {viewMode === "finviz" && (
          <div className="w-full bg-black flex items-center justify-center" style={{ height: 520 }}>
            <img
              src={`https://charts2.finviz.com/chart.ashx?t=${ticker}&ty=c&ta=1&p=d`}
              alt={`${ticker} Finviz Daily`}
              className="max-w-full max-h-full object-contain"
              style={{ height: 520, width: "100%" }}
            />
          </div>
        )}
      </div>

      {/* ── Trade Execution Strategy Guide ─────────────────────────── */}
      <div className="p-4 bg-[#0d0f17] border-t border-[#1a1d2e] text-xs">
        <div className="flex items-center gap-2 mb-2">
          <span className="text-sm">📋</span>
          <span className="font-bold text-[#e8ecff] uppercase tracking-wider">
            Trade Execution Plan for {ticker} {backtestMode ? "(Backtest Mode)" : ""}
          </span>
        </div>
        <div className="grid grid-cols-1 md:grid-cols-3 gap-4 text-[#8b949e]">
          <div className="bg-[#131625] p-3 rounded-lg border border-[#1a1d2e]">
            <b className="text-[#00e5a0] block mb-1">1. Entry Execution</b>
            Buy in the zone <span className="font-mono text-white">${levels.entry_zone_min?.toFixed(2)} – ${levels.entry_zone_max?.toFixed(2)}</span>.
            Enter on intraday pullbacks or market open if holding above moving averages.
          </div>
          <div className="bg-[#131625] p-3 rounded-lg border border-[#1a1d2e]">
            <b className="text-[#34d399] block mb-1">2. Target 1 Exit (Scale Out 50%)</b>
            Take 50% profit at <span className="font-mono text-white">${levels.target1?.toFixed(2)}</span> (+{levels.target1_gain_pct?.toFixed(1)}%).
            Immediately move your stop loss on remaining shares to breakeven (${levels.entry?.toFixed(2)}).
          </div>
          <div className="bg-[#131625] p-3 rounded-lg border border-[#1a1d2e]">
            <b className="text-[#38bdf8] block mb-1">3. Target 2 Runner Exit (Scale Out 50%)</b>
            Exit the remainder at <span className="font-mono text-white">${levels.target2?.toFixed(2)}</span> (+{levels.target2_gain_pct?.toFixed(1)}%) or trail
            the position using the 20-day SMA until a daily close below it.
          </div>
        </div>
      </div>

      {/* ── HISTORICAL BACKTEST MODAL / DRAWER ─────────────────────── */}
      {showHistoryModal && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/80 backdrop-blur-sm p-4">
          <div className="bg-[#0d0f17] border border-[#1a1d2e] rounded-xl max-w-4xl w-full max-h-[90vh] overflow-y-auto p-6 shadow-2xl space-y-6">
            <div className="flex items-center justify-between border-b border-[#1a1d2e] pb-4">
              <div>
                <h3 className="text-lg font-bold text-white flex items-center gap-2">
                  <span>📊</span>
                  <span>{ticker} — Historical Walk-Forward Backtest</span>
                </h3>
                <p className="text-xs text-[#6b7099] mt-0.5">
                  Simulates trade signals, stop losses, and target exits over the past {historyDays} days
                </p>
              </div>
              <button
                onClick={() => setShowHistoryModal(false)}
                className="text-[#6b7099] hover:text-white text-lg font-bold px-2 py-1"
              >
                ✕
              </button>
            </div>

            {/* Timeframe selector */}
            <div className="flex items-center gap-2 text-xs">
              <span className="text-[#6b7099]">Lookback:</span>
              {[90, 180, 365, 730].map((d) => (
                <button
                  key={d}
                  onClick={() => runHistoryBacktest(d)}
                  className={`px-3 py-1 rounded font-semibold transition-all ${
                    historyDays === d
                      ? "bg-[#4d9fff] text-black font-bold"
                      : "bg-[#131625] text-[#6b7099] hover:text-white border border-[#1a1d2e]"
                  }`}
                >
                  {d === 365 ? "1 Year" : d === 730 ? "2 Years" : `${d} Days`}
                </button>
              ))}
            </div>

            {historyLoading ? (
              <div className="py-16 flex flex-col items-center justify-center text-xs text-[#6b7099] gap-3">
                <div className="w-6 h-6 border-2 border-[#4d9fff] border-t-transparent rounded-full animate-spin"></div>
                <span>Simulating walk-forward trades and calculating performance...</span>
              </div>
            ) : historyData ? (
              <div className="space-y-6">
                {/* Metric cards */}
                <div className="grid grid-cols-2 md:grid-cols-5 gap-3">
                  <div className="bg-[#131625] border border-[#1a1d2e] rounded-lg p-3">
                    <div className="text-[10px] text-[#6b7099] uppercase font-semibold">Win Rate</div>
                    <div className="text-2xl font-bold font-mono text-[#00e5a0] mt-1">
                      {historyData.win_rate_pct}%
                    </div>
                    <div className="text-[10px] text-[#6b7099]">
                      {historyData.wins}W / {historyData.losses}L ({historyData.total_trades} total)
                    </div>
                  </div>

                  <div className="bg-[#131625] border border-[#1a1d2e] rounded-lg p-3">
                    <div className="text-[10px] text-[#6b7099] uppercase font-semibold">Profit Factor</div>
                    <div className="text-2xl font-bold font-mono text-[#38bdf8] mt-1">
                      {historyData.profit_factor}
                    </div>
                    <div className="text-[10px] text-[#6b7099]">Gross profit / Gross loss</div>
                  </div>

                  <div className="bg-[#131625] border border-[#1a1d2e] rounded-lg p-3">
                    <div className="text-[10px] text-[#6b7099] uppercase font-semibold">Total PnL</div>
                    <div
                      className={`text-2xl font-bold font-mono mt-1 ${
                        historyData.total_pnl_pct >= 0 ? "text-[#00e5a0]" : "text-[#ff4d6a]"
                      }`}
                    >
                      {historyData.total_pnl_pct >= 0 ? `+${historyData.total_pnl_pct}` : historyData.total_pnl_pct}%
                    </div>
                    <div className="text-[10px] text-[#6b7099]">Sum of all trades</div>
                  </div>

                  <div className="bg-[#131625] border border-[#1a1d2e] rounded-lg p-3">
                    <div className="text-[10px] text-[#6b7099] uppercase font-semibold">Avg Win</div>
                    <div className="text-2xl font-bold font-mono text-[#00e5a0] mt-1">
                      +{historyData.avg_win_pct}%
                    </div>
                    <div className="text-[10px] text-[#6b7099]">Per winning trade</div>
                  </div>

                  <div className="bg-[#131625] border border-[#1a1d2e] rounded-lg p-3">
                    <div className="text-[10px] text-[#6b7099] uppercase font-semibold">Avg Loss</div>
                    <div className="text-2xl font-bold font-mono text-[#ff4d6a] mt-1">
                      {historyData.avg_loss_pct}%
                    </div>
                    <div className="text-[10px] text-[#6b7099]">Per stopped trade</div>
                  </div>
                </div>

                {/* Trade Log Table */}
                <div className="overflow-x-auto rounded-lg border border-[#1a1d2e]">
                  <table className="w-full text-left text-xs font-mono">
                    <thead className="bg-[#0a0b14] text-[#6b7099] uppercase border-b border-[#1a1d2e]">
                      <tr>
                        <th className="px-3 py-2">Entry Date</th>
                        <th className="px-3 py-2">Exit Date</th>
                        <th className="px-3 py-2">Direction</th>
                        <th className="px-3 py-2">Entry Px</th>
                        <th className="px-3 py-2">Stop Loss</th>
                        <th className="px-3 py-2">Target 1</th>
                        <th className="px-3 py-2">Outcome</th>
                        <th className="px-3 py-2 text-right">PnL %</th>
                        <th className="px-3 py-2 text-right">Days Held</th>
                      </tr>
                    </thead>
                    <tbody className="divide-y divide-[#1a1d2e] bg-[#0d0f17]">
                      {historyData.trades?.map((t: any, idx: number) => (
                        <tr key={idx} className="hover:bg-[#131625] transition-colors">
                          <td className="px-3 py-2 text-white">{t.entry_date}</td>
                          <td className="px-3 py-2 text-[#e8ecff]">{t.exit_date ?? "Active"}</td>
                          <td className="px-3 py-2">
                            <span
                              className={`px-1.5 py-0.5 rounded text-[10px] font-bold ${
                                t.direction === "LONG"
                                  ? "bg-[#0a3d1f] text-[#00e5a0]"
                                  : "bg-[#3d0a1a] text-[#ff4d6a]"
                              }`}
                            >
                              {t.direction}
                            </span>
                          </td>
                          <td className="px-3 py-2 text-white">${t.entry_price}</td>
                          <td className="px-3 py-2 text-[#ff4d6a]">${t.stop_loss}</td>
                          <td className="px-3 py-2 text-[#00e5a0]">${t.target1}</td>
                          <td className="px-3 py-2">
                            <span
                              className={`px-2 py-0.5 rounded text-[10px] font-bold ${
                                t.is_win
                                  ? "bg-[#0a3d1f] text-[#00e5a0]"
                                  : "bg-[#3d0a1a] text-[#ff4d6a]"
                              }`}
                            >
                              {t.outcome}
                            </span>
                          </td>
                          <td
                            className={`px-3 py-2 text-right font-bold ${
                              t.pnl_pct >= 0 ? "text-[#00e5a0]" : "text-[#ff4d6a]"
                            }`}
                          >
                            {t.pnl_pct >= 0 ? `+${t.pnl_pct}` : t.pnl_pct}%
                          </td>
                          <td className="px-3 py-2 text-right text-[#6b7099]">{t.days_held}d</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </div>
            ) : null}
          </div>
        </div>
      )}
    </div>
  );
}
