"use client";
import { useState, useEffect, useRef } from "react";
import DailyTradeChart from "./DailyTradeChart";

declare global { interface Window { TradingView: any } }

const API_BASE = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

type Timeframe = "W" | "D";
type Tab = "tradingview_daily" | "tradingview_curl" | "finviz" | "tradingview_embed";

function FinvizChart({ ticker, timeframe }: { ticker: string; timeframe: Timeframe }) {
  const p = timeframe === "W" ? "w" : "d";
  return (
    <div className="w-full bg-black flex items-center justify-center" style={{ height: 540 }}>
      <img
        src={`https://charts2.finviz.com/chart.ashx?t=${ticker}&ty=c&ta=1&p=${p}`}
        alt={`${ticker} Finviz chart (${timeframe === "W" ? "Weekly" : "Daily"})`}
        className="max-w-full max-h-full object-contain"
        style={{ height: 540, width: "100%" }}
      />
    </div>
  );
}

function TVWeeklyCurlChart({ ticker }: { ticker: string }) {
  const containerRef = useRef<HTMLDivElement>(null);
  const [loading, setLoading] = useState(true);
  const [curlInfo, setCurlInfo] = useState<{
    last_sma30: number;
    is_curling_up: boolean;
    markersCount: number;
    slope: number;
    current_price: number;
    trailing_stop: number | null;
    swing_low_8w: number | null;
  } | null>(null);

  useEffect(() => {
    let chart: any;
    let isMounted = true;
    setLoading(true);

    fetch(`${API_BASE}/api/analysis/chart-weekly/${ticker}`)
      .then((r) => r.json())
      .then(async (data) => {
        if (!isMounted || !containerRef.current) return;
        if (!data.bars || data.bars.length === 0) {
          setLoading(false);
          return;
        }

        setCurlInfo({
          last_sma30: data.last_sma30,
          is_curling_up: data.is_curling_up,
          markersCount: (data.markers || []).length,
          slope: data.slope || 0,
          current_price: data.current_price,
          trailing_stop: data.trailing_stop,
          swing_low_8w: data.swing_low_8w,
        });

        const { createChart, CrosshairMode, LineStyle } = await import("lightweight-charts");
        containerRef.current.innerHTML = "";

        chart = createChart(containerRef.current, {
          width: containerRef.current.clientWidth,
          height: 540,
          layout: { background: { color: "#0d1117" }, textColor: "#8b949e" },
          grid: {
            vertLines: { color: "rgba(255,255,255,0.03)" },
            horzLines: { color: "rgba(255,255,255,0.03)" },
          },
          crosshair: { mode: CrosshairMode.Normal },
          rightPriceScale: {
            borderColor: "#30363d",
            scaleMargins: {
              top: 0.08,
              bottom: 0.22, // Reserve bottom 22% for volume
            },
          },
          timeScale: { borderColor: "#30363d", timeVisible: false },
        });

        // 1. Weekly Volume Histogram (Bottom Pane)
        const volumeSeries = chart.addHistogramSeries({
          color: "#26a69a",
          priceFormat: { type: "volume" },
          priceScaleId: "volume",
        });

        chart.priceScale("volume").applyOptions({
          scaleMargins: {
            top: 0.78, // Take bottom 22%
            bottom: 0,
          },
        });

        if (data.volume_bars && data.volume_bars.length > 0) {
          volumeSeries.setData(data.volume_bars);
        }

        // 2. Weekly Candlesticks
        const candleSeries = chart.addCandlestickSeries({
          upColor: "#00e5a0",
          downColor: "#ff4d4f",
          borderUpColor: "#00e5a0",
          borderDownColor: "#ff4d4f",
          wickUpColor: "#00e5a0",
          wickDownColor: "#ff4d4f",
        });
        candleSeries.setData(data.bars);

        // 3. 30-Week Simple Moving Average (amber line)
        if (data.sma30 && data.sma30.length > 0) {
          const smaSeries = chart.addLineSeries({
            color: "#f59e0b",
            lineWidth: 2,
            title: "30W SMA",
            priceLineVisible: false,
            lastValueVisible: true,
          });
          smaSeries.setData(data.sma30);
        }

        // 4. Trailing Stop Level (Dashed Red Line at 5% below 30W SMA or swing low)
        if (data.trailing_stop && data.bars.length > 20) {
          const stopLine = chart.addLineSeries({
            color: "rgba(239, 68, 68, 0.75)",
            lineWidth: 1,
            lineStyle: LineStyle.Dashed,
            title: "Trailing Stop",
            priceLineVisible: false,
            lastValueVisible: true,
          });
          const recentBars = data.bars.slice(-20);
          stopLine.setData(recentBars.map((b: any) => ({ time: b.time, value: data.trailing_stop })));
        }

        // 5. ── Arrows plotted directly on the bar where 30W MA curls up ──
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

        setLoading(false);
      })
      .catch((e) => {
        console.error(e);
        if (isMounted) setLoading(false);
      });

    return () => {
      isMounted = false;
      chart?.remove();
    };
  }, [ticker]);

  return (
    <div className="relative w-full bg-[#0d1117]" style={{ minHeight: 540 }}>
      {curlInfo && (
        <div className="absolute top-3 left-3 z-10 bg-[#161b22]/95 backdrop-blur border border-border/80 rounded-lg px-3 py-1.5 text-xs flex flex-wrap items-center gap-3 shadow-lg">
          <span className="font-mono text-[#f59e0b] font-bold flex items-center gap-1">
            <span className="w-2 h-0.5 bg-[#f59e0b] inline-block"></span>
            <span>30W SMA: ${curlInfo.last_sma30?.toFixed(2) ?? "—"}</span>
          </span>
          <span className="text-muted">·</span>
          <span className={curlInfo.is_curling_up ? "text-bull font-bold" : "text-muted"}>
            Slope: {curlInfo.slope > 0 ? `+${curlInfo.slope.toFixed(2)} (Curling Up ↗)` : `${curlInfo.slope.toFixed(2)} (Flat/Down)`}
          </span>
          {curlInfo.trailing_stop && (
            <>
              <span className="text-muted">·</span>
              <span className="font-mono text-[#fca5a5] flex items-center gap-1" title="Trailing Stop (5% below 30W SMA)">
                <span className="w-2 h-0.5 bg-[#ef4444] inline-block border-b border-dashed border-red-500"></span>
                <span>Stop: ${curlInfo.trailing_stop.toFixed(2)}</span>
              </span>
            </>
          )}
          <span className="text-muted">·</span>
          <span className="text-accent font-semibold flex items-center gap-1">
            <span>⬆</span>
            <span>{curlInfo.markersCount} Curl-Up Breakouts (Volume Surge)</span>
          </span>
        </div>
      )}
      {loading && (
        <div className="w-full flex items-center justify-center text-muted text-xs animate-pulse" style={{ height: 540 }}>
          Calculating 30-Week SMA, volume profiles, and detecting curl-up inflection points...
        </div>
      )}
      <div ref={containerRef} style={{ height: 540 }} />
    </div>
  );
}

function TVEmbedChart({ ticker, timeframe }: { ticker: string; timeframe: Timeframe }) {
  const containerId = `tv_${ticker}_${timeframe}`;
  const scriptRef   = useRef<HTMLScriptElement | null>(null);

  useEffect(() => {
    function init() {
      if (!window.TradingView) return;
      const el = document.getElementById(containerId);
      if (!el) return;
      el.innerHTML = "";

      new window.TradingView.widget({
        container_id:        containerId,
        autosize:            true,
        symbol:              ticker,
        interval:            timeframe,
        timezone:            "America/New_York",
        theme:               "dark",
        style:               "1",
        locale:              "en",
        toolbar_bg:          "#161b22",
        backgroundColor:     "#0d1117",
        gridColor:           "rgba(255,255,255,0.04)",
        enable_publishing:   false,
        hide_top_toolbar:    false,
        allow_symbol_change: false,
        save_image:          true,
        studies: [
          "Volume@tv-basicstudies",
          "MASimple@tv-basicstudies",
        ],
        studies_overrides: {
          "moving average.length": 30,
          "moving average.color": "#f59e0b",
          "moving average.linewidth": 2,
        },
      });
    }

    if (window.TradingView) {
      init();
    } else {
      const script    = document.createElement("script");
      script.src      = "https://s3.tradingview.com/tv.js";
      script.async    = true;
      script.onload   = init;
      document.head.appendChild(script);
      scriptRef.current = script;
    }

    return () => {
      const el = document.getElementById(containerId);
      if (el) el.innerHTML = "";
    };
  }, [ticker, containerId, timeframe]);

  return <div id={containerId} style={{ height: 540 }} />;
}

export default function DualChart({ ticker }: { ticker: string }) {
  const [tab, setTab] = useState<Tab>("tradingview_curl"); // Default to 30W SMA Curl chart
  const [fvTimeframe, setFvTimeframe] = useState<Timeframe>("D"); // Finviz default Daily
  const [tvTimeframe, setTvTimeframe] = useState<Timeframe>("W"); // Embed default Weekly

  return (
    <div className="card p-0 overflow-hidden">
      {/* Tab bar */}
      <div className="flex flex-wrap items-center justify-between px-3 pt-3 pb-0 border-b border-border gap-2">
        <div className="flex items-center gap-1">
          {([
            { key: "tradingview_daily", label: "TradingView (Daily Entry & Exit)", hint: "Daily candles with calculated Best Entry point, Stop Loss, Target 1 & 2" },
            { key: "tradingview_curl",  label: "TradingView (30W Curl Arrows)", hint: "Weekly chart with 30-Week SMA, Volume, Trailing Stop, and green curl-up arrows" },
            { key: "finviz",            label: `Finviz (${fvTimeframe === "D" ? "Daily" : "Weekly"})`, hint: "Auto patterns · S/R levels · Technical overlays" },
            { key: "tradingview_embed", label: `TradingView Embed (${tvTimeframe === "W" ? "Weekly" : "Daily"})`, hint: "Full TradingView iframe widget with drawing tools" },
          ] as { key: Tab; label: string; hint: string }[]).map(t => (
            <button
              key={t.key}
              onClick={() => setTab(t.key)}
              title={t.hint}
              className={`px-3 py-2 text-xs font-semibold rounded-t-md transition-colors border-b-2 -mb-px ${
                tab === t.key
                  ? "text-white border-accent"
                  : "text-muted border-transparent hover:text-white"
              }`}
            >
              {t.label}
            </button>
          ))}
        </div>

        {/* Dynamic Controls based on tab */}
        {tab === "finviz" && (
          <div className="flex items-center gap-1 bg-surface p-1 rounded-lg border border-border text-xs mb-1.5">
            <span className="text-[10px] text-muted uppercase font-mono px-1">Finviz:</span>
            <button
              onClick={() => setFvTimeframe("D")}
              className={`px-2 py-0.5 rounded font-semibold text-[11px] ${fvTimeframe === "D" ? "bg-accent text-black font-bold" : "text-muted hover:text-white"}`}
            >
              ☀️ Daily
            </button>
            <button
              onClick={() => setFvTimeframe("W")}
              className={`px-2 py-0.5 rounded font-semibold text-[11px] ${fvTimeframe === "W" ? "bg-accent text-black font-bold" : "text-muted hover:text-white"}`}
            >
              📅 Weekly
            </button>
          </div>
        )}

        {tab === "tradingview_embed" && (
          <div className="flex items-center gap-1 bg-surface p-1 rounded-lg border border-border text-xs mb-1.5">
            <span className="text-[10px] text-muted uppercase font-mono px-1">TradingView:</span>
            <button
              onClick={() => setTvTimeframe("W")}
              className={`px-2 py-0.5 rounded font-semibold text-[11px] ${tvTimeframe === "W" ? "bg-accent text-black font-bold" : "text-muted hover:text-white"}`}
            >
              📅 Weekly (30-SMA)
            </button>
            <button
              onClick={() => setTvTimeframe("D")}
              className={`px-2 py-0.5 rounded font-semibold text-[11px] ${tvTimeframe === "D" ? "bg-accent text-black font-bold" : "text-muted hover:text-white"}`}
            >
              ☀️ Daily
            </button>
          </div>
        )}

        {tab === "tradingview_curl" && (
          <div className="flex items-center gap-3 text-[11px] text-muted mb-1.5 mr-1">
            <span className="flex items-center gap-1">
              <span className="w-2.5 h-0.5 bg-[#f59e0b] rounded"></span>
              <span className="font-mono text-[#f59e0b] font-semibold">30W SMA</span>
            </span>
            <span className="flex items-center gap-1">
              <span className="w-2.5 h-0.5 bg-[#ef4444] rounded border-b border-dashed"></span>
              <span className="font-mono text-[#fca5a5]">Trailing Stop</span>
            </span>
            <span className="text-bull font-bold flex items-center gap-0.5">
              <span>⬆</span>
              <span>Volume Confirmed Curl</span>
            </span>
          </div>
        )}
      </div>

      {/* Chart area */}
      <div>
        {tab === "tradingview_daily" && <DailyTradeChart ticker={ticker} />}
        {tab === "tradingview_curl" && <TVWeeklyCurlChart ticker={ticker} />}
        {tab === "finviz" && <FinvizChart ticker={ticker} timeframe={fvTimeframe} />}
        {tab === "tradingview_embed" && <TVEmbedChart ticker={ticker} timeframe={tvTimeframe} />}
      </div>
    </div>
  );
}