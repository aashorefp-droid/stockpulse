"use client";

import React, { useState, useEffect, useRef, useCallback, Suspense } from "react";
import { useSearchParams, useRouter } from "next/navigation";
import Link from "next/link";

const API_BASE = process.env.NEXT_PUBLIC_API_URL || "https://stockpulse-pkpj.onrender.com";

interface ChartBar {
  time: number;
  open: number;
  high: number;
  low: number;
  close: number;
}

interface VolumeBar {
  time: number;
  value: number;
  color: string;
}

interface ContractData {
  symbol: string;
  description: string;
  expiry: string;
  dte: number;
  strike: number;
  option_type: "CALL" | "PUT";
  last_price: number;
  bid: number;
  ask: number;
  iv: number;
  delta: number;
  gamma: number;
  theta: number;
  volume: number;
  open_interest: number;
  bars: {
    time: number;
    value: number;
    open: number;
    high: number;
    low: number;
    close: number;
  }[];
}

interface MarketSession {
  status: "PRE_MARKET" | "REGULAR_HOURS" | "AFTER_HOURS" | "WEEKEND_CLOSED" | "OVERNIGHT_CLOSED" | "HISTORICAL_SESSION";
  label: string;
  current_time_et: string;
  current_time_ct: string;
  regular_open_et: string;
  regular_open_ct: string;
  regular_close_et: string;
  regular_close_ct: string;
  validation_ct: string;
  extended_hours: boolean;
  is_historical?: boolean;
  target_date?: string | null;
}

interface BacktestResult {
  timeframe?: string;
  structure_tag?: string;
  bars_evaluated?: number;
  status?: string;
  result_badge?: string;
  t1_hit?: boolean;
  t1_hit_at?: string | null;
  t2_hit?: boolean;
  t2_hit_at?: string | null;
  stop_hit?: boolean;
  stop_hit_at?: string | null;
  runner_breakeven_hit?: boolean;
  post_entry_high?: number;
  post_entry_low?: number;
  end_price?: number;
  max_favorable_excursion?: string;
  max_adverse_excursion?: string;
  realized_pnl_per_share?: number;
  realized_r_multiple?: string;
  summary?: string;
  one_hour?: BacktestResult;
  full_day?: BacktestResult;
  session_window?: "1h" | "full";
}

interface LiveChartPayload {
  ticker: string;
  yf_symbol: string;
  timeframe: string;
  current_price: number;
  timestamp: number;
  target_date?: string | null;
  is_historical?: boolean;
  available_dates?: string[];
  backtest?: BacktestResult | null;
  levels: {
    pdh: number | null;
    pdl: number | null;
    pd_mid: number | null;
    atr: number | null;
    entry: number | null;
    stop_loss: number | null;
    target_1: number | null;
    target_2: number | null;
    reward_risk: string | null;
    action: string | null;
    valid_outcome: string | null;
    focus_badge: string | null;
    focus_rule: string | null;
    four_hour_pattern?: string | null;
  };
  bars: ChartBar[];
  volume_bars: VolumeBar[];
  contract: ContractData;
  call_contract?: ContractData;
  put_contract?: ContractData;
  option_mode?: string;
  available_expiries: string[];
  available_strikes: number[];
  session?: MarketSession;
}

type OptionTypeMode = "CALL" | "PUT" | "BOTH";

const TIMEFRAMES = [
  { key: "1m", label: "1m" },
  { key: "5m", label: "5m" },
  { key: "15m", label: "15m" },
  { key: "30m", label: "30m" },
  { key: "1h", label: "1h" },
  { key: "4h", label: "4h" },
  { key: "1d", label: "1D" },
];

const QUICK_TICKERS = ["SPY", "SPXW", "QQQ", "SOXX", "UNH", "AAPL", "NVDA", "TSLA"];

// Module-level cached import for Lightweight Charts
let lwcPromise: Promise<any> | null = null;
function getLwc() {
  if (!lwcPromise) {
    lwcPromise = import("lightweight-charts");
  }
  return lwcPromise;
}

interface PatternMarker {
  time: number;
  position: "aboveBar" | "belowBar";
  color: string;
  shape: "arrowUp" | "arrowDown" | "circle";
  text: string;
  size?: number;
}

// Compute Exponential Moving Average (EMA) for any candle field (high, low, close)
function computeEma(
  bars: ChartBar[],
  period: number = 20,
  source: "high" | "low" | "close" = "close"
): { time: number; value: number }[] {
  if (!bars || bars.length === 0) return [];
  const k = 2 / (period + 1);
  const result: { time: number; value: number }[] = [];
  let ema = bars[0][source];
  result.push({ time: bars[0].time, value: parseFloat(ema.toFixed(2)) });
  for (let i = 1; i < bars.length; i++) {
    ema = bars[i][source] * k + ema * (1 - k);
    result.push({ time: bars[i].time, value: parseFloat(ema.toFixed(2)) });
  }
  return result;
}

// Candlestick pattern detection for Inside Bar and Engulfing Bar (Bullish & Bearish)
function computePatternMarkers(bars: ChartBar[]): PatternMarker[] {
  if (!bars || bars.length < 2) return [];
  const markers: PatternMarker[] = [];

  for (let i = 1; i < bars.length; i++) {
    const prev = bars[i - 1];
    const curr = bars[i];

    // 1. Engulfing Bar Detection:
    // Bullish Engulfing: previous bar was bearish, current bar is bullish, current real body engulfs previous real body
    const isBullishEngulfing =
      prev.close < prev.open &&
      curr.close > curr.open &&
      curr.open <= prev.close &&
      curr.close >= prev.open;

    // Bearish Engulfing: previous bar was bullish, current bar is bearish, current real body engulfs previous real body
    const isBearishEngulfing =
      prev.close > prev.open &&
      curr.close < curr.open &&
      curr.open >= prev.close &&
      curr.close <= prev.open;

    // 2. Inside Bar Detection: Current candle's range is completely inside previous candle's range
    const isInsideBar = curr.high <= prev.high && curr.low >= prev.low;

    if (isBullishEngulfing) {
      markers.push({
        time: curr.time,
        position: "belowBar",
        color: "#10b981",
        shape: "arrowUp",
        text: "Bull Engulf",
        size: 1,
      });
    } else if (isBearishEngulfing) {
      markers.push({
        time: curr.time,
        position: "aboveBar",
        color: "#ef4444",
        shape: "arrowDown",
        text: "Bear Engulf",
        size: 1,
      });
    } else if (isInsideBar) {
      const isBullish = curr.close >= curr.open;
      markers.push({
        time: curr.time,
        position: isBullish ? "belowBar" : "aboveBar",
        color: isBullish ? "#34d399" : "#f87171",
        shape: "circle",
        text: isBullish ? "IB 🟢" : "IB 🔴",
        size: 1,
      });
    }
  }

  return markers.sort((a, b) => a.time - b.time);
}

// Single bar pattern inspection helper for hover crosshair and active bar status
function getBarPatternInfo(curr: ChartBar, prev?: ChartBar): {
  bias: "Bullish" | "Bearish" | "Neutral";
  change: number;
  pctChange: number;
  pattern: string | null;
} {
  const change = curr.close - curr.open;
  const pctChange = curr.open > 0 ? (change / curr.open) * 100 : 0;
  const bias = change > 0 ? "Bullish" : change < 0 ? "Bearish" : "Neutral";

  let pattern: string | null = null;
  if (prev) {
    const isBullishEngulfing =
      prev.close < prev.open &&
      curr.close > curr.open &&
      curr.open <= prev.close &&
      curr.close >= prev.open;

    const isBearishEngulfing =
      prev.close > prev.open &&
      curr.close < curr.open &&
      curr.open >= prev.close &&
      curr.close <= prev.open;

    const isInsideBar = curr.high <= prev.high && curr.low >= prev.low;

    if (isBullishEngulfing) {
      pattern = "Bullish Engulfing 🟢";
    } else if (isBearishEngulfing) {
      pattern = "Bearish Engulfing 🔴";
    } else if (isInsideBar) {
      pattern = curr.close >= curr.open ? "Inside Bar (Bullish 🟢)" : "Inside Bar (Bearish 🔴)";
    }
  }

  return { bias, change, pctChange, pattern };
}

function LiveChartInner() {
  const searchParams = useSearchParams();
  const router = useRouter();

  const initialTicker = (searchParams.get("ticker") || "SPY").toUpperCase();
  const initialTf = searchParams.get("tf") || searchParams.get("timeframe") || "5m";
  const initialStrike = searchParams.get("strike") ? parseFloat(searchParams.get("strike")!) : null;
  const initialType = searchParams.get("type") ? (searchParams.get("type")!.toUpperCase() as OptionTypeMode) : null;
  const initialDte = searchParams.get("dte") !== null ? parseInt(searchParams.get("dte")!) : 0;
  const initialDate = searchParams.get("date") || searchParams.get("target_date") || "";

  // State
  const [ticker, setTicker] = useState(initialTicker);
  const [customTicker, setCustomTicker] = useState("");
  const [timeframe, setTimeframe] = useState(initialTf);
  const [strike, setStrike] = useState<number | null>(initialStrike);
  const [optionType, setOptionType] = useState<OptionTypeMode | null>(initialType);
  const [dte, setDte] = useState<number>(initialDte);
  const [customExpiry, setCustomExpiry] = useState<string>("");

  // Backdated Historical & Bar Replay State
  const [targetDate, setTargetDate] = useState<string>(initialDate);
  const [replayMode, setReplayMode] = useState<boolean>(false);
  const [replayIndex, setReplayIndex] = useState<number>(0);
  const [isPlaying, setIsPlaying] = useState<boolean>(false);
  const [replaySpeed, setReplaySpeed] = useState<number>(1);
  const [backtestWindow, setBacktestWindow] = useState<"1h" | "full">("1h");

  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [data, setData] = useState<LiveChartPayload | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [lastUpdated, setLastUpdated] = useState<string>("");
  const [autoRefresh, setAutoRefresh] = useState(!initialDate);

  // Polling Interval State (Default 10s optimal for buttery smooth 60 FPS)
  const [pollIntervalMs, setPollIntervalMs] = useState<number>(10000);

  // Timezone & Extended Hours Options
  const [tzMode, setTzMode] = useState<"CT" | "ET">("CT");
  const [extendedHours, setExtendedHours] = useState<boolean>(true);

  // View Options
  const [showContractOverlap, setShowContractOverlap] = useState(true);
  const [showVolume, setShowVolume] = useState(true);
  const [showPdhPdl, setShowPdhPdl] = useState(true);
  const [showSl, setShowSl] = useState(true);
  const [showEntryTarget, setShowEntryTarget] = useState(true);
  const [showEmaBand, setShowEmaBand] = useState<boolean>(false);
  const [showPatterns, setShowPatterns] = useState<boolean>(false);
  const [chartMode, setChartMode] = useState<"overlay" | "dual">("overlay");

  // Chart DOM refs
  const chartContainerRef = useRef<HTMLDivElement>(null);
  const subChartContainerRef = useRef<HTMLDivElement>(null);
  const chartInstanceRef = useRef<any>(null);
  const subChartInstanceRef = useRef<any>(null);
  const candleSeriesRef = useRef<any>(null);
  const volumeSeriesRef = useRef<any>(null);
  const contractSeriesRef = useRef<any>(null);
  const callSeriesRef = useRef<any>(null);
  const putSeriesRef = useRef<any>(null);
  const emaHighSeriesRef = useRef<any>(null);
  const emaCloseSeriesRef = useRef<any>(null);
  const emaLowSeriesRef = useRef<any>(null);
  const latestBarsRef = useRef<ChartBar[]>([]);
  const priceLinesRef = useRef<any[]>([]);

  // Performance optimization refs
  const lastLevelsKeyRef = useRef<string>("");
  const lastDatasetIdRef = useRef<string>("");
  const lastBarTimestampRef = useRef<number>(0);
  const isPageVisibleRef = useRef<boolean>(true);

  // Hover Info
  const [hoverInfo, setHoverInfo] = useState<{
    time: string;
    underlyingClose: number;
    underlyingOpen?: number;
    underlyingHigh?: number;
    underlyingLow?: number;
    contractValue?: number;
    callValue?: number;
    putValue?: number;
    emaHigh?: number;
    emaClose?: number;
    emaLow?: number;
    candleBias?: "Bullish" | "Bearish" | "Neutral";
    candleChangePct?: number;
    pattern?: string | null;
  } | null>(null);

  // Derived effective option mode
  const effectiveMode: OptionTypeMode = optionType || (data?.option_mode as OptionTypeMode) || (data?.contract?.option_type as OptionTypeMode) || "CALL";

  // Derived active backtest outcome (1-Hour Session vs Full-Day Session)
  const activeBacktest: BacktestResult | undefined = data?.backtest
    ? (backtestWindow === "1h" && data.backtest.one_hour
        ? data.backtest.one_hour
        : backtestWindow === "full" && data.backtest.full_day
        ? data.backtest.full_day
        : data.backtest)
    : undefined;

  const formatBarTime = (ts: number, tz: "CT" | "ET" = tzMode): string => {
    const d = new Date(ts * 1000);
    return d.toLocaleTimeString("en-US", {
      timeZone: tz === "ET" ? "America/New_York" : "America/Chicago",
      hour: "2-digit",
      minute: "2-digit",
      hour12: true,
    });
  };

  const getBarIndexByTimeET = (bars: ChartBar[], targetHourET: number, targetMinET: number): number => {
    for (let i = 0; i < bars.length; i++) {
      const d = new Date(bars[i].time * 1000);
      const etHour = parseInt(d.toLocaleTimeString("en-US", { timeZone: "America/New_York", hour12: false, hour: "numeric" }), 10);
      const etMin = parseInt(d.toLocaleTimeString("en-US", { timeZone: "America/New_York", minute: "numeric" }), 10);
      if (etHour > targetHourET || (etHour === targetHourET && etMin >= targetMinET)) {
        return i;
      }
    }
    return 0;
  };

  const switchTicker = (newTicker: string) => {
    const t = newTicker.trim().toUpperCase();
    if (!t) return;
    setTicker(t);
    setStrike(null);
    setOptionType(null);
    setCustomExpiry("");
    chartInstanceRef.current?.remove();
    chartInstanceRef.current = null;
    candleSeriesRef.current = null;
    volumeSeriesRef.current = null;
    contractSeriesRef.current = null;
    callSeriesRef.current = null;
    putSeriesRef.current = null;
    emaHighSeriesRef.current = null;
    emaCloseSeriesRef.current = null;
    emaLowSeriesRef.current = null;
    priceLinesRef.current = [];
    const dateParam = targetDate ? `&date=${targetDate}` : "";
    router.replace(`/chart?ticker=${t}&tf=${timeframe}${dateParam}`);
  };

  const switchTimeframe = (newTf: string) => {
    if (newTf === timeframe) return;
    setTimeframe(newTf);
    chartInstanceRef.current?.remove();
    chartInstanceRef.current = null;
    candleSeriesRef.current = null;
    volumeSeriesRef.current = null;
    contractSeriesRef.current = null;
    callSeriesRef.current = null;
    putSeriesRef.current = null;
    emaHighSeriesRef.current = null;
    emaCloseSeriesRef.current = null;
    emaLowSeriesRef.current = null;
    priceLinesRef.current = [];
    const dateParam = targetDate ? `&date=${targetDate}` : "";
    router.replace(`/chart?ticker=${ticker}&tf=${newTf}${dateParam}`);
  };

  const switchDate = (newDate: string) => {
    const d = newDate.trim();
    setTargetDate(d);
    setIsPlaying(false);
    if (d) {
      setAutoRefresh(false);
    } else {
      setAutoRefresh(true);
    }
    setStrike(null);
    setOptionType(null);
    setCustomExpiry("");
    setDte(0);
    chartInstanceRef.current?.remove();
    chartInstanceRef.current = null;
    candleSeriesRef.current = null;
    volumeSeriesRef.current = null;
    contractSeriesRef.current = null;
    callSeriesRef.current = null;
    putSeriesRef.current = null;
    emaHighSeriesRef.current = null;
    emaCloseSeriesRef.current = null;
    emaLowSeriesRef.current = null;
    priceLinesRef.current = [];
    const dateParam = d ? `&date=${d}` : "";
    router.replace(`/chart?ticker=${ticker}&tf=${timeframe}${dateParam}`);
  };

  const toggleReplay = () => {
    if (!replayMode) {
      setReplayMode(true);
      setAutoRefresh(false);
      setIsPlaying(false);
      if (data?.bars?.length) {
        const valIdx = getBarIndexByTimeET(data.bars, 9, 35);
        setReplayIndex(valIdx > 0 ? valIdx : 0);
      }
    } else {
      setReplayMode(false);
      setIsPlaying(false);
      if (!targetDate) setAutoRefresh(true);
    }
  };

  const switchTz = (newTz: "CT" | "ET") => {
    if (newTz === tzMode) return;
    setTzMode(newTz);
    chartInstanceRef.current?.remove();
    chartInstanceRef.current = null;
    candleSeriesRef.current = null;
    volumeSeriesRef.current = null;
    contractSeriesRef.current = null;
    callSeriesRef.current = null;
    putSeriesRef.current = null;
    emaHighSeriesRef.current = null;
    emaCloseSeriesRef.current = null;
    emaLowSeriesRef.current = null;
    priceLinesRef.current = [];
  };

  // Synchronize state when URL params change
  useEffect(() => {
    const t = searchParams.get("ticker");
    if (t && t.toUpperCase() !== ticker) {
      setTicker(t.toUpperCase());
      setStrike(null);
      setOptionType(null);
      setCustomExpiry("");
      chartInstanceRef.current?.remove();
      chartInstanceRef.current = null;
    }
    const tf = searchParams.get("tf") || searchParams.get("timeframe");
    if (tf && tf !== timeframe) {
      setTimeframe(tf);
      chartInstanceRef.current?.remove();
      chartInstanceRef.current = null;
    }
    const d = searchParams.get("date") || searchParams.get("target_date") || "";
    if (d !== targetDate) {
      setTargetDate(d);
      setStrike(null);
      setOptionType(null);
      setCustomExpiry("");
      setDte(0);
      if (d) setAutoRefresh(false);
      chartInstanceRef.current?.remove();
      chartInstanceRef.current = null;
    }
  }, [searchParams]);

  // Fetch payload
  const fetchData = useCallback(
    async (isBackground = false) => {
      if (!isBackground) setLoading(true);
      else setRefreshing(true);
      setError(null);

      try {
        let url = `${API_BASE}/api/analysis/live-chart/${ticker}?timeframe=${timeframe}&extended_hours=${extendedHours}`;
        if (targetDate) url += `&target_date=${targetDate}`;
        if (strike !== null) url += `&strike=${strike}`;
        if (optionType !== null) url += `&option_type=${optionType}`;
        if (customExpiry) url += `&custom_expiry=${customExpiry}`;
        else if (dte !== null) url += `&dte=${dte}`;

        const res = await fetch(url);
        if (!res.ok) throw new Error(`HTTP ${res.status}: Failed to fetch chart data`);
        const json: LiveChartPayload = await res.json();

        setData(json);
        const now = new Date();
        setLastUpdated(
          now.toLocaleTimeString("en-US", {
            hour: "2-digit",
            minute: "2-digit",
            second: "2-digit",
            hour12: true,
          })
        );
      } catch (err: any) {
        console.error("Failed to load live chart data:", err);
        setError(err.message || "Failed to load live chart data");
      } finally {
        setLoading(false);
        setRefreshing(false);
      }
    },
    [ticker, timeframe, strike, optionType, dte, customExpiry, extendedHours, targetDate]
  );

  // Initial fetch and dependency fetch
  useEffect(() => {
    fetchData(false);
  }, [fetchData]);

  // Page Visibility API: pause background polling when tab is inactive, resume immediately upon return
  useEffect(() => {
    const handleVisibilityChange = () => {
      const isVisible = document.visibilityState === "visible";
      isPageVisibleRef.current = isVisible;
      if (isVisible && autoRefresh && !replayMode && !targetDate) {
        // Instant fresh update when user switches back to this tab
        fetchData(true);
      }
    };
    document.addEventListener("visibilitychange", handleVisibilityChange);
    return () => document.removeEventListener("visibilitychange", handleVisibilityChange);
  }, [autoRefresh, replayMode, targetDate, fetchData]);

  // Live auto-refresh polling (disabled in Replay Mode, on Backdated dates, or when tab is hidden)
  useEffect(() => {
    if (!autoRefresh || replayMode || targetDate) return;
    const interval = setInterval(() => {
      if (isPageVisibleRef.current) {
        fetchData(true);
      }
    }, pollIntervalMs);
    return () => clearInterval(interval);
  }, [autoRefresh, replayMode, targetDate, pollIntervalMs, fetchData]);

  // Strike adjustment helpers
  const handleStrikeStep = (direction: "up" | "down") => {
    if (!data?.available_strikes || data.available_strikes.length === 0) return;
    const currentStrike = data.contract.strike;
    const sorted = data.available_strikes;
    const idx = sorted.indexOf(currentStrike);

    if (direction === "up") {
      if (idx !== -1 && idx < sorted.length - 1) {
        setStrike(sorted[idx + 1]);
      } else if (idx === -1) {
        const higher = sorted.filter((s) => s > currentStrike);
        if (higher.length > 0) setStrike(higher[0]);
      }
    } else {
      if (idx > 0) {
        setStrike(sorted[idx - 1]);
      } else if (idx === -1) {
        const lower = sorted.filter((s) => s < currentStrike);
        if (lower.length > 0) setStrike(lower[lower.length - 1]);
      }
    }
  };

  // Render & Update Lightweight Charts
  useEffect(() => {
    if (!data || !chartContainerRef.current) return;

    let chart: any = chartInstanceRef.current;
    let isNewChart = false;

    async function setupChart() {
      const { createChart, CrosshairMode, LineStyle } = await getLwc();
      if (!chartContainerRef.current || !data) return;

      if (!chart) {
        isNewChart = true;
        chartContainerRef.current.innerHTML = "";

        chart = createChart(chartContainerRef.current, {
          width: chartContainerRef.current.clientWidth,
          height: chartMode === "dual" ? 420 : 540,
          layout: {
            background: { color: "#090d16" },
            textColor: "#8b949e",
            fontSize: 12,
          },
          grid: {
            vertLines: { color: "rgba(255, 255, 255, 0.04)" },
            horzLines: { color: "rgba(255, 255, 255, 0.04)" },
          },
          crosshair: {
            mode: CrosshairMode.Normal,
            vertLine: { color: "#4f5b66", width: 1, style: LineStyle.Dashed },
            horzLine: { color: "#4f5b66", width: 1, style: LineStyle.Dashed },
          },
          rightPriceScale: {
            borderColor: "#1e293b",
            scaleMargins: {
              top: 0.08,
              bottom: showVolume ? 0.22 : 0.08,
            },
          },
          leftPriceScale: {
            visible: showContractOverlap && chartMode === "overlay",
            borderColor: "#eab308",
            scaleMargins: {
              top: 0.1,
              bottom: 0.25,
            },
          },
          localization: {
            dateFormat: "yyyy-MM-dd",
            timeFormatter: (ts: number) => {
              const d = new Date(ts * 1000);
              const et = d.toLocaleTimeString("en-US", {
                timeZone: "America/New_York",
                hour: "2-digit",
                minute: "2-digit",
                hour12: true,
              });
              const ct = d.toLocaleTimeString("en-US", {
                timeZone: "America/Chicago",
                hour: "2-digit",
                minute: "2-digit",
                hour12: true,
              });
              return tzMode === "ET" ? `${et} ET (${ct} CT)` : `${ct} CT (${et} ET)`;
            },
          },
          timeScale: {
            borderColor: "#1e293b",
            timeVisible: true,
            secondsVisible: false,
            tickMarkFormatter: (time: any) => {
              const ts = typeof time === "number" ? time : time?.timestamp;
              if (!ts) return null;
              const d = new Date(ts * 1000);
              return d.toLocaleTimeString("en-US", {
                timeZone: tzMode === "ET" ? "America/New_York" : "America/Chicago",
                hour: "2-digit",
                minute: "2-digit",
                hour12: false,
              });
            },
          },
        });

        chartInstanceRef.current = chart;

        // Volume Series
        if (showVolume) {
          const vSeries = chart.addHistogramSeries({
            priceFormat: { type: "volume" },
            priceScaleId: "volume",
          });
          chart.priceScale("volume").applyOptions({
            scaleMargins: { top: 0.82, bottom: 0 },
          });
          volumeSeriesRef.current = vSeries;
        }

        // Underlying Candlestick Series
        const cSeries = chart.addCandlestickSeries({
          upColor: "#10b981",
          downColor: "#ef4444",
          borderUpColor: "#10b981",
          borderDownColor: "#ef4444",
          wickUpColor: "#10b981",
          wickDownColor: "#ef4444",
          priceFormat: {
            type: "price",
            precision: 2,
            minMove: 0.01,
          },
        });
        candleSeriesRef.current = cSeries;

        // Crosshair listener for tooltip
        chart.subscribeCrosshairMove((param: any) => {
          if (!param || !param.time || !param.seriesPrices) {
            setHoverInfo(null);
            return;
          }
          const cPrice = param.seriesPrices.get(candleSeriesRef.current);
          const cp = callSeriesRef.current ? param.seriesPrices.get(callSeriesRef.current) : undefined;
          const pp = putSeriesRef.current ? param.seriesPrices.get(putSeriesRef.current) : undefined;
          const op = contractSeriesRef.current ? param.seriesPrices.get(contractSeriesRef.current) : undefined;
          const eh = emaHighSeriesRef.current ? param.seriesPrices.get(emaHighSeriesRef.current) : undefined;
          const ec = emaCloseSeriesRef.current ? param.seriesPrices.get(emaCloseSeriesRef.current) : undefined;
          const el = emaLowSeriesRef.current ? param.seriesPrices.get(emaLowSeriesRef.current) : undefined;

          const dateObj = new Date(param.time * 1000);
          const dateStr = dateObj.toLocaleDateString("en-US", {
            month: "short",
            day: "numeric",
            timeZone: tzMode === "ET" ? "America/New_York" : "America/Chicago",
          });
          const etStr = dateObj.toLocaleTimeString("en-US", {
            timeZone: "America/New_York",
            hour: "2-digit",
            minute: "2-digit",
            hour12: true,
          });
          const ctStr = dateObj.toLocaleTimeString("en-US", {
            timeZone: "America/Chicago",
            hour: "2-digit",
            minute: "2-digit",
            hour12: true,
          });

          // Identify candle direction and patterns
          const currentBars = latestBarsRef.current;
          const barIdx = currentBars.findIndex((b) => b.time === param.time);
          let pattern: string | null = null;
          let candleBias: "Bullish" | "Bearish" | "Neutral" = "Neutral";
          let candleChangePct: number | undefined = undefined;

          if (barIdx >= 0) {
            const currBar = currentBars[barIdx];
            const prevBar = barIdx > 0 ? currentBars[barIdx - 1] : undefined;
            const info = getBarPatternInfo(currBar, prevBar);
            pattern = info.pattern;
            candleBias = info.bias;
            candleChangePct = info.pctChange;
          } else if (typeof cPrice === "object" && cPrice?.open !== undefined && cPrice?.close !== undefined) {
            const chg = cPrice.close - cPrice.open;
            candleBias = chg > 0 ? "Bullish" : chg < 0 ? "Bearish" : "Neutral";
            candleChangePct = cPrice.open > 0 ? (chg / cPrice.open) * 100 : 0;
          }

          setHoverInfo({
            time: `${dateStr} · ${tzMode === "ET" ? `${etStr} ET (${ctStr} CT)` : `${ctStr} CT (${etStr} ET)`}`,
            underlyingClose: typeof cPrice === "object" ? cPrice?.close : cPrice,
            underlyingOpen: typeof cPrice === "object" ? cPrice?.open : undefined,
            underlyingHigh: typeof cPrice === "object" ? cPrice?.high : undefined,
            underlyingLow: typeof cPrice === "object" ? cPrice?.low : undefined,
            contractValue: typeof op === "number" ? op : op?.close,
            callValue: typeof cp === "number" ? cp : cp?.close,
            putValue: typeof pp === "number" ? pp : pp?.close,
            emaHigh: typeof eh === "number" ? eh : undefined,
            emaClose: typeof ec === "number" ? ec : undefined,
            emaLow: typeof el === "number" ? el : undefined,
            candleBias,
            candleChangePct,
            pattern,
          });
        });

        // Resize observer
        const ro = new ResizeObserver(() => {
          if (chartContainerRef.current) {
            chart.applyOptions({ width: chartContainerRef.current.clientWidth });
          }
        });
        ro.observe(chartContainerRef.current);
      }

      // Ensure left price scale visibility & border match option mode
      chart.applyOptions({
        leftPriceScale: {
          visible: showContractOverlap && chartMode === "overlay",
          borderColor: effectiveMode === "BOTH" ? "#06b6d4" : effectiveMode === "PUT" ? "#f43f5e" : "#eab308",
        },
      });

      // Synchronize contract series (single vs dual)
      if (showContractOverlap && chartMode === "overlay") {
        if (effectiveMode === "BOTH") {
          // In BOTH mode, remove single contract series if present
          if (contractSeriesRef.current) {
            try { chart.removeSeries(contractSeriesRef.current); } catch {}
            contractSeriesRef.current = null;
          }
          // Create CALL series if needed
          if (!callSeriesRef.current) {
            callSeriesRef.current = chart.addAreaSeries({
              priceScaleId: "left",
              topColor: "rgba(16, 185, 129, 0.35)",
              bottomColor: "rgba(16, 185, 129, 0.02)",
              lineColor: "#10b981",
              lineWidth: 2,
              priceFormat: {
                type: "price",
                precision: 2,
                minMove: 0.01,
              },
              title: "CALL",
            });
          }
          // Create PUT series if needed
          if (!putSeriesRef.current) {
            putSeriesRef.current = chart.addAreaSeries({
              priceScaleId: "left",
              topColor: "rgba(244, 63, 94, 0.35)",
              bottomColor: "rgba(244, 63, 94, 0.02)",
              lineColor: "#f43f5e",
              lineWidth: 2,
              priceFormat: {
                type: "price",
                precision: 2,
                minMove: 0.01,
              },
              title: "PUT",
            });
          }
        } else {
          // Single Mode (CALL or PUT)
          if (callSeriesRef.current) {
            try { chart.removeSeries(callSeriesRef.current); } catch {}
            callSeriesRef.current = null;
          }
          if (putSeriesRef.current) {
            try { chart.removeSeries(putSeriesRef.current); } catch {}
            putSeriesRef.current = null;
          }
          if (!contractSeriesRef.current) {
            contractSeriesRef.current = chart.addAreaSeries({
              priceScaleId: "left",
              topColor: effectiveMode === "PUT" ? "rgba(244, 63, 94, 0.35)" : "rgba(234, 179, 8, 0.35)",
              bottomColor: effectiveMode === "PUT" ? "rgba(244, 63, 94, 0.02)" : "rgba(234, 179, 8, 0.02)",
              lineColor: effectiveMode === "PUT" ? "#f43f5e" : "#eab308",
              lineWidth: 2,
              priceFormat: {
                type: "price",
                precision: 2,
                minMove: 0.01,
              },
              title: effectiveMode,
            });
          } else {
            contractSeriesRef.current.applyOptions({
              topColor: effectiveMode === "PUT" ? "rgba(244, 63, 94, 0.35)" : "rgba(234, 179, 8, 0.35)",
              bottomColor: effectiveMode === "PUT" ? "rgba(244, 63, 94, 0.02)" : "rgba(234, 179, 8, 0.02)",
              lineColor: effectiveMode === "PUT" ? "#f43f5e" : "#eab308",
              title: effectiveMode,
            });
          }
        }
      } else {
        if (contractSeriesRef.current) {
          try { chart.removeSeries(contractSeriesRef.current); } catch {}
          contractSeriesRef.current = null;
        }
        if (callSeriesRef.current) {
          try { chart.removeSeries(callSeriesRef.current); } catch {}
          callSeriesRef.current = null;
        }
        if (putSeriesRef.current) {
          try { chart.removeSeries(putSeriesRef.current); } catch {}
          putSeriesRef.current = null;
        }
      }

      // Synchronize 20 Period EMA Band Series (High, Close, Low) on right scale
      if (showEmaBand) {
        if (!emaHighSeriesRef.current) {
          emaHighSeriesRef.current = chart.addLineSeries({
            priceScaleId: "right",
            color: "#38bdf8", // Sky blue
            lineWidth: 1,
            lineStyle: LineStyle.Dashed,
            priceFormat: { type: "price", precision: 2, minMove: 0.01 },
            title: "EMA 20 High",
            crosshairMarkerVisible: true,
          });
        }
        if (!emaCloseSeriesRef.current) {
          emaCloseSeriesRef.current = chart.addLineSeries({
            priceScaleId: "right",
            color: "#fbbf24", // Amber gold
            lineWidth: 2,
            lineStyle: LineStyle.Solid,
            priceFormat: { type: "price", precision: 2, minMove: 0.01 },
            title: "EMA 20 Close",
            crosshairMarkerVisible: true,
          });
        }
        if (!emaLowSeriesRef.current) {
          emaLowSeriesRef.current = chart.addLineSeries({
            priceScaleId: "right",
            color: "#f43f5e", // Rose pink
            lineWidth: 1,
            lineStyle: LineStyle.Dashed,
            priceFormat: { type: "price", precision: 2, minMove: 0.01 },
            title: "EMA 20 Low",
            crosshairMarkerVisible: true,
          });
        }
      } else {
        if (emaHighSeriesRef.current) {
          try { chart.removeSeries(emaHighSeriesRef.current); } catch {}
          emaHighSeriesRef.current = null;
        }
        if (emaCloseSeriesRef.current) {
          try { chart.removeSeries(emaCloseSeriesRef.current); } catch {}
          emaCloseSeriesRef.current = null;
        }
        if (emaLowSeriesRef.current) {
          try { chart.removeSeries(emaLowSeriesRef.current); } catch {}
          emaLowSeriesRef.current = null;
        }
      }

      // Determine if this is a new dataset or an incremental live tick
      const currentDatasetKey = `${data?.ticker || ""}_${data?.timeframe || ""}_${data?.target_date || ""}_${data?.session?.extended_hours}_${chartMode}_${replayMode}_${effectiveMode}_${data?.contract?.strike}`;
      const isDatasetChange = isNewChart || currentDatasetKey !== lastDatasetIdRef.current;

      // Populate or update data
      if (isDatasetChange || replayMode) {
        lastDatasetIdRef.current = currentDatasetKey;
        if (candleSeriesRef.current && data?.bars) {
          const barsToSet = replayMode ? data.bars.slice(0, Math.min(replayIndex + 1, data.bars.length)) : data.bars;
          candleSeriesRef.current.setData(barsToSet);
          if (showPatterns) {
            candleSeriesRef.current.setMarkers(computePatternMarkers(barsToSet));
          } else {
            candleSeriesRef.current.setMarkers([]);
          }
          if (showEmaBand && barsToSet.length > 0) {
            emaHighSeriesRef.current?.setData(computeEma(barsToSet, 20, "high"));
            emaCloseSeriesRef.current?.setData(computeEma(barsToSet, 20, "close"));
            emaLowSeriesRef.current?.setData(computeEma(barsToSet, 20, "low"));
          }
          latestBarsRef.current = barsToSet;
          if (barsToSet.length > 0) {
            lastBarTimestampRef.current = barsToSet[barsToSet.length - 1].time;
          }
        }
        if (volumeSeriesRef.current && data?.volume_bars && showVolume) {
          const volToSet = replayMode ? data.volume_bars.slice(0, Math.min(replayIndex + 1, data.volume_bars.length)) : data.volume_bars;
          volumeSeriesRef.current.setData(volToSet);
        }
        if (showContractOverlap && chartMode === "overlay") {
          if (effectiveMode === "BOTH") {
            if (callSeriesRef.current && data?.call_contract?.bars) {
              const callToSet = replayMode ? data.call_contract.bars.slice(0, Math.min(replayIndex + 1, data.call_contract.bars.length)) : data.call_contract.bars;
              callSeriesRef.current.setData(callToSet);
            }
            if (putSeriesRef.current && data?.put_contract?.bars) {
              const putToSet = replayMode ? data.put_contract.bars.slice(0, Math.min(replayIndex + 1, data.put_contract.bars.length)) : data.put_contract.bars;
              putSeriesRef.current.setData(putToSet);
            }
          } else {
            if (contractSeriesRef.current && data?.contract?.bars) {
              const optToSet = replayMode ? data.contract.bars.slice(0, Math.min(replayIndex + 1, data.contract.bars.length)) : data.contract.bars;
              contractSeriesRef.current.setData(optToSet);
            }
          }
        }
      } else {
        // High-performance incremental live update (O(1) Series.update)
        if (candleSeriesRef.current && data?.bars?.length) {
          const latestBar = data.bars[data.bars.length - 1];
          const prevTime = lastBarTimestampRef.current;
          if (prevTime > 0 && latestBar.time >= prevTime) {
            candleSeriesRef.current.update(latestBar);
            if (showPatterns) {
              candleSeriesRef.current.setMarkers(computePatternMarkers(data.bars));
            }
            if (showEmaBand) {
              emaHighSeriesRef.current?.setData(computeEma(data.bars, 20, "high"));
              emaCloseSeriesRef.current?.setData(computeEma(data.bars, 20, "close"));
              emaLowSeriesRef.current?.setData(computeEma(data.bars, 20, "low"));
            }
            latestBarsRef.current = data.bars;
            if (volumeSeriesRef.current && data.volume_bars?.length && showVolume) {
              volumeSeriesRef.current.update(data.volume_bars[data.volume_bars.length - 1]);
            }
            if (showContractOverlap && chartMode === "overlay") {
              if (effectiveMode === "BOTH") {
                if (callSeriesRef.current && data.call_contract?.bars?.length) {
                  callSeriesRef.current.update(data.call_contract.bars[data.call_contract.bars.length - 1]);
                }
                if (putSeriesRef.current && data.put_contract?.bars?.length) {
                  putSeriesRef.current.update(data.put_contract.bars[data.put_contract.bars.length - 1]);
                }
              } else {
                if (contractSeriesRef.current && data.contract?.bars?.length) {
                  contractSeriesRef.current.update(data.contract.bars[data.contract.bars.length - 1]);
                }
              }
            }
          } else {
            candleSeriesRef.current.setData(data.bars);
            if (showPatterns) {
              candleSeriesRef.current.setMarkers(computePatternMarkers(data.bars));
            } else {
              candleSeriesRef.current.setMarkers([]);
            }
            if (showEmaBand) {
              emaHighSeriesRef.current?.setData(computeEma(data.bars, 20, "high"));
              emaCloseSeriesRef.current?.setData(computeEma(data.bars, 20, "close"));
              emaLowSeriesRef.current?.setData(computeEma(data.bars, 20, "low"));
            }
            latestBarsRef.current = data.bars;
            if (volumeSeriesRef.current && data.volume_bars && showVolume) {
              volumeSeriesRef.current.setData(data.volume_bars);
            }
            if (showContractOverlap && chartMode === "overlay") {
              if (effectiveMode === "BOTH") {
                if (callSeriesRef.current && data.call_contract?.bars) {
                  callSeriesRef.current.setData(data.call_contract.bars);
                }
                if (putSeriesRef.current && data.put_contract?.bars) {
                  putSeriesRef.current.setData(data.put_contract.bars);
                }
              } else {
                if (contractSeriesRef.current && data.contract?.bars) {
                  contractSeriesRef.current.setData(data.contract.bars);
                }
              }
            }
          }
          lastBarTimestampRef.current = latestBar.time;
        }
      }

      // Memoized Key Levels & SL on the underlying chart
      const currentLevelsKey = `${data?.levels?.pdh}_${data?.levels?.pdl}_${data?.levels?.pd_mid}_${data?.levels?.entry}_${data?.levels?.stop_loss}_${data?.levels?.target_1}_${data?.levels?.target_2}_${showPdhPdl}_${showSl}_${showEntryTarget}`;

      if (isNewChart || currentLevelsKey !== lastLevelsKeyRef.current) {
        lastLevelsKeyRef.current = currentLevelsKey;

        // Clean existing price lines
        priceLinesRef.current.forEach((pl) => {
          try {
            candleSeriesRef.current?.removePriceLine(pl);
          } catch {}
        });
        priceLinesRef.current = [];

        // Pre-Draw Key Levels & SL on the underlying chart
        if (candleSeriesRef.current && data?.levels) {
          const { pdh, pdl, pd_mid, entry, stop_loss, target_1, target_2 } = data.levels;

          // 1. PDH (Prior Day High) - Red Dashed
          if (showPdhPdl && pdh) {
            const line = candleSeriesRef.current.createPriceLine({
              price: pdh,
              color: "#f87171",
              lineWidth: 2,
              lineStyle: LineStyle.Dashed,
              axisLabelVisible: true,
              title: `PDH $${pdh.toFixed(2)}`,
            });
            priceLinesRef.current.push(line);
          }

          // 2. PDL (Prior Day Low) - Emerald Dashed
          if (showPdhPdl && pdl) {
            const line = candleSeriesRef.current.createPriceLine({
              price: pdl,
              color: "#34d399",
              lineWidth: 2,
              lineStyle: LineStyle.Dashed,
              axisLabelVisible: true,
              title: `PDL $${pdl.toFixed(2)}`,
            });
            priceLinesRef.current.push(line);
          }

          // 3. STOP LOSS (SL) - Crimson Solid
          if (showSl && stop_loss) {
            const line = candleSeriesRef.current.createPriceLine({
              price: stop_loss,
              color: "#ef4444",
              lineWidth: 2,
              lineStyle: LineStyle.Solid,
              axisLabelVisible: true,
              title: `🛑 SL $${stop_loss.toFixed(2)}`,
            });
            priceLinesRef.current.push(line);
          }

          // 4. Midpoint - Slate Dotted
          if (showPdhPdl && pd_mid) {
            const line = candleSeriesRef.current.createPriceLine({
              price: pd_mid,
              color: "#94a3b8",
              lineWidth: 1,
              lineStyle: LineStyle.Dotted,
              axisLabelVisible: true,
              title: `MID $${pd_mid.toFixed(2)}`,
            });
            priceLinesRef.current.push(line);
          }

          // 5. Entry Trigger - Amber Dashed
          if (showEntryTarget && entry) {
            const line = candleSeriesRef.current.createPriceLine({
              price: entry,
              color: "#fbbf24",
              lineWidth: 2,
              lineStyle: LineStyle.Dashed,
              axisLabelVisible: true,
              title: `ENTRY $${entry.toFixed(2)}`,
            });
            priceLinesRef.current.push(line);
          }

          // 6. Target 1 - Cyan Dotted
          if (showEntryTarget && target_1) {
            const line = candleSeriesRef.current.createPriceLine({
              price: target_1,
              color: "#38bdf8",
              lineWidth: 1,
              lineStyle: LineStyle.Dotted,
              axisLabelVisible: true,
              title: `🎯 T1 $${target_1.toFixed(2)}`,
            });
            priceLinesRef.current.push(line);
          }

          // 7. Target 2 - Indigo Dotted
          if (showEntryTarget && target_2) {
            const line = candleSeriesRef.current.createPriceLine({
              price: target_2,
              color: "#818cf8",
              lineWidth: 1,
              lineStyle: LineStyle.Dotted,
              axisLabelVisible: true,
              title: `🎯 T2 $${target_2.toFixed(2)}`,
            });
            priceLinesRef.current.push(line);
          }
        }
      }

      if (isNewChart) {
        chart.timeScale().fitContent();
      }
    }

    setupChart();

    return () => {
      // Keep chart instance alive across smooth polling ticks unless component unmounts
    };
  }, [data, showContractOverlap, showVolume, showPdhPdl, showSl, showEntryTarget, showEmaBand, showPatterns, chartMode, replayMode, effectiveMode]);

  // Replay bar slice updater: fast in-memory slicing without chart rebuild
  useEffect(() => {
    if (!replayMode || !data?.bars?.length) return;
    const clamped = Math.min(Math.max(0, replayIndex), data.bars.length - 1);
    const slicedBars = data.bars.slice(0, clamped + 1);
    const slicedVolume = data.volume_bars ? data.volume_bars.slice(0, clamped + 1) : [];
    const slicedContract = data.contract?.bars ? data.contract.bars.slice(0, clamped + 1) : [];
    const slicedCall = data.call_contract?.bars ? data.call_contract.bars.slice(0, clamped + 1) : [];
    const slicedPut = data.put_contract?.bars ? data.put_contract.bars.slice(0, clamped + 1) : [];

    if (candleSeriesRef.current && slicedBars.length > 0) {
      candleSeriesRef.current.setData(slicedBars);
      if (showPatterns) {
        candleSeriesRef.current.setMarkers(computePatternMarkers(slicedBars));
      } else {
        candleSeriesRef.current.setMarkers([]);
      }
    }
    if (showEmaBand && slicedBars.length > 0) {
      emaHighSeriesRef.current?.setData(computeEma(slicedBars, 20, "high"));
      emaCloseSeriesRef.current?.setData(computeEma(slicedBars, 20, "close"));
      emaLowSeriesRef.current?.setData(computeEma(slicedBars, 20, "low"));
    }
    latestBarsRef.current = slicedBars;
    if (volumeSeriesRef.current && slicedVolume.length > 0 && showVolume) {
      volumeSeriesRef.current.setData(slicedVolume);
    }
    if (showContractOverlap && chartMode === "overlay") {
      if (effectiveMode === "BOTH") {
        if (callSeriesRef.current && slicedCall.length > 0) {
          callSeriesRef.current.setData(slicedCall);
        }
        if (putSeriesRef.current && slicedPut.length > 0) {
          putSeriesRef.current.setData(slicedPut);
        }
      } else {
        if (contractSeriesRef.current && slicedContract.length > 0) {
          contractSeriesRef.current.setData(slicedContract);
        }
      }
    }
  }, [replayMode, replayIndex, data, showVolume, showContractOverlap, showEmaBand, showPatterns, chartMode, effectiveMode]);

  // When replayMode turns off, restore full series
  useEffect(() => {
    if (!replayMode && data?.bars?.length) {
      candleSeriesRef.current?.setData(data.bars);
      if (showPatterns) {
        candleSeriesRef.current?.setMarkers(computePatternMarkers(data.bars));
      } else {
        candleSeriesRef.current?.setMarkers([]);
      }
      if (showEmaBand) {
        emaHighSeriesRef.current?.setData(computeEma(data.bars, 20, "high"));
        emaCloseSeriesRef.current?.setData(computeEma(data.bars, 20, "close"));
        emaLowSeriesRef.current?.setData(computeEma(data.bars, 20, "low"));
      }
      latestBarsRef.current = data.bars;
      if (volumeSeriesRef.current && data.volume_bars && showVolume) {
        volumeSeriesRef.current.setData(data.volume_bars);
      }
      if (showContractOverlap && chartMode === "overlay") {
        if (effectiveMode === "BOTH") {
          if (callSeriesRef.current && data.call_contract?.bars) {
            callSeriesRef.current.setData(data.call_contract.bars);
          }
          if (putSeriesRef.current && data.put_contract?.bars) {
            putSeriesRef.current.setData(data.put_contract.bars);
          }
        } else {
          if (contractSeriesRef.current && data.contract?.bars) {
            contractSeriesRef.current.setData(data.contract.bars);
          }
        }
      }
    }
  }, [replayMode, data, showVolume, showContractOverlap, showEmaBand, showPatterns, chartMode, effectiveMode]);

  // Replay playback timer loop
  useEffect(() => {
    if (!replayMode || !isPlaying || !data?.bars?.length) return;
    const speedIntervals: Record<number, number> = {
      0.5: 1400,
      1: 800,
      2: 450,
      3: 280,
      5: 150,
      10: 75,
    };
    const delay = speedIntervals[replaySpeed] || 800;

    const timer = setInterval(() => {
      setReplayIndex((prev) => {
        if (prev >= data.bars.length - 1) {
          setIsPlaying(false);
          return prev;
        }
        return prev + 1;
      });
    }, delay);

    return () => clearInterval(timer);
  }, [replayMode, isPlaying, replaySpeed, data?.bars?.length]);

  // Pro keyboard shortcuts (Space = Play/Pause, Left/Right arrow = Step, R = Toggle Replay)
  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      if (["INPUT", "TEXTAREA", "SELECT"].includes((e.target as HTMLElement)?.tagName)) {
        return;
      }
      if (e.key === " " && replayMode) {
        e.preventDefault();
        setIsPlaying((p) => !p);
      } else if (e.key === "ArrowRight" && replayMode) {
        e.preventDefault();
        setIsPlaying(false);
        setReplayIndex((p) => Math.min(p + 1, (data?.bars?.length || 1) - 1));
      } else if (e.key === "ArrowLeft" && replayMode) {
        e.preventDefault();
        setIsPlaying(false);
        setReplayIndex((p) => Math.max(0, p - 1));
      } else if (e.key.toLowerCase() === "r" && !e.ctrlKey && !e.metaKey) {
        e.preventDefault();
        toggleReplay();
      }
    };
    window.addEventListener("keydown", handleKeyDown);
    return () => window.removeEventListener("keydown", handleKeyDown);
  }, [replayMode, data?.bars?.length]);

  // Clean up chart on full unmount
  useEffect(() => {
    return () => {
      chartInstanceRef.current?.remove();
      chartInstanceRef.current = null;
    };
  }, []);

  return (
    <div className="w-full min-h-screen bg-[#080b13] text-slate-200 pb-12 font-sans">
      {/* Top Bar / Header */}
      <div className="border-b border-slate-800 bg-[#0d121f] px-3 sm:px-6 py-2.5">
        <div className="flex flex-wrap items-center justify-between gap-3">
          {/* Brand & Ticker Switcher */}
          <div className="flex flex-wrap items-center gap-3">
            <div className="flex items-center gap-2">
              <span className="text-xl">📈</span>
              <h1 className="text-base sm:text-lg font-bold text-white tracking-tight">
                Live 5m Chart
              </h1>
            </div>

            {/* Quick Ticker Pills */}
            <div className="flex items-center gap-1 bg-[#121829] border border-slate-800 rounded-lg p-0.5">
              {QUICK_TICKERS.map((t) => (
                <button
                  key={t}
                  type="button"
                  onClick={() => switchTicker(t)}
                  className={`px-2 py-0.5 rounded text-xs font-mono font-bold transition-colors ${
                    ticker === t
                      ? "bg-cyan-500 text-black shadow-sm"
                      : "text-slate-400 hover:text-white hover:bg-slate-800"
                  }`}
                >
                  {t}
                </button>
              ))}
            </div>

            {/* Custom Ticker Search */}
            <form
              onSubmit={(e) => {
                e.preventDefault();
                if (customTicker.trim()) {
                  switchTicker(customTicker.trim());
                  setCustomTicker("");
                }
              }}
              className="flex items-center gap-1"
            >
              <input
                type="text"
                placeholder="Ticker…"
                value={customTicker}
                onChange={(e) => setCustomTicker(e.target.value)}
                className="bg-[#121829] border border-slate-800 rounded px-2 py-0.5 text-xs font-mono text-white placeholder-slate-500 w-20 focus:outline-none focus:border-cyan-500 uppercase"
              />
              <button
                type="submit"
                className="px-2 py-0.5 bg-slate-800 hover:bg-slate-700 text-slate-300 rounded text-xs font-mono"
              >
                Go
              </button>
            </form>
          </div>

          {/* Timeframe & Live Polling Indicator */}
          <div className="flex flex-wrap items-center gap-3">
            {/* Timeframe Selector Pills */}
            <div className="flex items-center bg-[#121829] border border-slate-800 rounded-lg p-0.5">
              {TIMEFRAMES.map((tf) => (
                <button
                  key={tf.key}
                  type="button"
                  onClick={() => switchTimeframe(tf.key)}
                  className={`px-2.5 py-1 rounded text-xs font-mono font-semibold transition-all ${
                    timeframe === tf.key
                      ? "bg-emerald-500 text-black font-bold shadow-sm"
                      : "text-slate-400 hover:text-white hover:bg-slate-800"
                  }`}
                >
                  {tf.label}
                </button>
              ))}
            </div>

            {/* Live Indicator & Polling Speed Selector */}
            <div className="flex items-center gap-1.5 bg-[#121829] border border-slate-800 px-2 py-1 rounded-lg text-xs font-mono">
              <span
                className={`w-2 h-2 rounded-full ${
                  autoRefresh ? "bg-emerald-400 animate-pulse" : "bg-slate-500"
                }`}
              />
              <span className={autoRefresh ? "text-emerald-300 font-bold" : "text-slate-400"}>
                {autoRefresh ? `LIVE ${pollIntervalMs / 1000}s` : "PAUSED"}
              </span>

              {/* Polling Speed Pills */}
              <div className="flex items-center gap-0.5 bg-[#182035] rounded p-0.5 ml-0.5" title="Live update polling interval">
                {[
                  { ms: 5000, label: "5s", title: "⚡ 5s: Ultra-fast polling" },
                  { ms: 10000, label: "10s", title: "⏱️ 10s: Optimal 60 FPS smooth performance (Recommended)" },
                  { ms: 30000, label: "30s", title: "🐢 30s: Eco mode (low network & CPU)" },
                ].map((opt) => (
                  <button
                    key={opt.ms}
                    type="button"
                    onClick={() => setPollIntervalMs(opt.ms)}
                    title={opt.title}
                    className={`px-1.5 py-0.5 rounded text-[10px] font-bold transition ${
                      pollIntervalMs === opt.ms
                        ? "bg-cyan-500 text-black shadow-sm"
                        : "text-slate-400 hover:text-white"
                    }`}
                  >
                    {opt.label}
                  </button>
                ))}
              </div>

              {lastUpdated && <span className="text-slate-400 text-[10px]">· {lastUpdated}</span>}
              <button
                type="button"
                onClick={() => fetchData(true)}
                disabled={refreshing}
                title="Manual Refresh"
                className={`text-slate-400 hover:text-white ml-1 ${refreshing ? "animate-spin" : ""}`}
              >
                🔄
              </button>
              <button
                type="button"
                onClick={() => setAutoRefresh(!autoRefresh)}
                className="text-[10px] text-slate-400 hover:text-cyan-300 underline ml-1"
              >
                {autoRefresh ? "Pause" : "Resume"}
              </button>
            </div>

            {/* Timezone Switcher */}
            <div className="flex items-center bg-[#121829] border border-slate-800 rounded-lg p-0.5 text-xs font-mono" title="Timezone format on chart axis & crosshair">
              <button
                type="button"
                onClick={() => switchTz("CT")}
                className={`px-2 py-0.5 rounded transition ${
                  tzMode === "CT" ? "bg-cyan-500 text-black font-bold shadow-sm" : "text-slate-400 hover:text-white"
                }`}
                title="Central Time (StockPulse Schedule: 8:30 AM Open, 8:35 AM Validation)"
              >
                CT (8:30 Open)
              </button>
              <button
                type="button"
                onClick={() => switchTz("ET")}
                className={`px-2 py-0.5 rounded transition ${
                  tzMode === "ET" ? "bg-cyan-500 text-black font-bold shadow-sm" : "text-slate-400 hover:text-white"
                }`}
                title="Eastern Time (Market Time: 9:30 AM Open, 4:00 PM Close)"
              >
                ET (9:30 Open)
              </button>
            </div>

            {/* Replay Mode Toggle Button */}
            <button
              type="button"
              onClick={toggleReplay}
              className={`px-2.5 py-1 rounded-lg text-xs font-mono font-bold transition flex items-center gap-1.5 shadow-sm ${
                replayMode
                  ? "bg-amber-500 text-black shadow-amber-500/20"
                  : "bg-[#121829] border border-slate-700/80 text-amber-300 hover:bg-amber-500/10 hover:text-white"
              }`}
              title="Bar Replay: Step through historical or today's session candle by candle (Key: R)"
            >
              <span>{replayMode ? "⏸ Replay Active" : "🎬 Replay"}</span>
            </button>

            {/* Back to AI Analyst Chat */}
            <Link
              href="/chat"
              className="px-2.5 py-1 rounded-lg bg-cyan-500/15 border border-cyan-500/40 text-cyan-300 hover:bg-cyan-500/25 text-xs font-mono font-semibold transition flex items-center gap-1"
            >
              <span>🤖</span>
              <span>AI Analyst Pulse</span>
            </Link>
          </div>
        </div>
      </div>

      {/* Historical Session & Date Selector Ribbon */}
      <div className="bg-[#0b101c] border-b border-slate-800/80 px-3 sm:px-6 py-2 flex flex-wrap items-center justify-between gap-3 text-xs font-mono">
        <div className="flex flex-wrap items-center gap-2">
          <span className="text-slate-400 font-bold flex items-center gap-1">
            <span>📅</span>
            <span>Session Date:</span>
          </span>

          {/* Today / Live Button */}
          <button
            type="button"
            onClick={() => switchDate("")}
            className={`px-2.5 py-1 rounded text-xs font-semibold transition ${
              !targetDate
                ? "bg-emerald-500 text-black font-bold shadow-sm"
                : "bg-[#141a2c] text-slate-400 hover:text-white hover:bg-slate-800"
            }`}
          >
            🔴 Live (Today)
          </button>

          {/* Quick Recent Trading Days (from available_dates) */}
          {data?.available_dates &&
            data.available_dates
              .filter((d) => {
                const todayStr = new Date().toISOString().split("T")[0];
                return d !== todayStr;
              })
              .slice(0, 4)
              .map((d) => {
                const parts = d.split("-");
                const dateObj = new Date(parseInt(parts[0], 10), parseInt(parts[1], 10) - 1, parseInt(parts[2], 10));
                const label = dateObj.toLocaleDateString("en-US", { month: "short", day: "numeric", weekday: "short" });
                return (
                  <button
                    key={d}
                    type="button"
                    onClick={() => switchDate(d)}
                    className={`px-2.5 py-1 rounded text-xs font-semibold transition ${
                      targetDate === d
                        ? "bg-cyan-500 text-black font-bold shadow-sm"
                        : "bg-[#141a2c] text-slate-400 hover:text-white hover:bg-slate-800"
                    }`}
                  >
                    {label}
                  </button>
                );
              })}

          {/* HTML Date Picker Input */}
          <div className="flex items-center gap-1.5 bg-[#141a2c] border border-slate-800 rounded px-2 py-0.5">
            <span className="text-slate-500 text-[11px]">Pick Date:</span>
            <input
              type="date"
              value={targetDate}
              onChange={(e) => switchDate(e.target.value)}
              max={new Date().toISOString().split("T")[0]}
              className="bg-transparent text-white text-xs font-mono focus:outline-none cursor-pointer"
            />
          </div>

          {targetDate && (
            <button
              type="button"
              onClick={() => switchDate("")}
              className="text-[11px] text-cyan-400 hover:text-cyan-300 underline ml-1"
              title="Clear date filter and return to live session"
            >
              Reset to Live
            </button>
          )}
        </div>

        {/* Backtest Quick Status Badge if historical session */}
        {data?.backtest && (
          <div className="flex flex-wrap items-center gap-2">
            {/* Interactive 1-Hour vs Full-Day Toggle */}
            <div className="flex items-center bg-[#141a2c] border border-slate-700/80 rounded-lg p-0.5 text-xs font-mono">
              <button
                type="button"
                onClick={() => setBacktestWindow("1h")}
                className={`px-2 py-0.5 rounded text-[11px] font-bold transition flex items-center gap-1 ${
                  backtestWindow === "1h"
                    ? "bg-amber-400 text-black shadow-sm"
                    : "text-slate-400 hover:text-white hover:bg-slate-800"
                }`}
                title="1-Hour Session Backtest: Evaluates performance across the first 60 minutes after entry (8:35 AM – 9:35 AM CT)"
              >
                <span>⏱️</span>
                <span>1-Hour</span>
              </button>
              <button
                type="button"
                onClick={() => setBacktestWindow("full")}
                className={`px-2 py-0.5 rounded text-[11px] font-bold transition flex items-center gap-1 ${
                  backtestWindow === "full"
                    ? "bg-cyan-500 text-black shadow-sm"
                    : "text-slate-400 hover:text-white hover:bg-slate-800"
                }`}
                title="Full-Day Session Backtest: Evaluates performance through the entire market session to close"
              >
                <span>📅</span>
                <span>Full Day</span>
              </button>
            </div>

            <span
              className={`px-2.5 py-0.5 rounded text-[11px] font-bold border transition ${
                activeBacktest?.status?.includes("WIN")
                  ? "bg-emerald-500/20 text-emerald-300 border-emerald-500/40"
                  : activeBacktest?.status?.includes("STOP")
                  ? "bg-red-500/20 text-red-300 border-red-500/40"
                  : "bg-slate-800 text-slate-300 border-slate-700"
              }`}
              title={activeBacktest?.summary || activeBacktest?.timeframe}
            >
              {backtestWindow === "1h" ? "⏱️ 1H: " : "📅 Full: "}
              {activeBacktest?.status} {activeBacktest?.realized_r_multiple && `(${activeBacktest.realized_r_multiple})`}
            </span>
            {!replayMode && (
              <button
                type="button"
                onClick={toggleReplay}
                className="px-2 py-0.5 rounded bg-amber-500/20 border border-amber-500/40 text-amber-300 hover:bg-amber-500/30 font-bold text-[11px] flex items-center gap-1"
              >
                <span>▶ Replay Setup</span>
              </button>
            )}
          </div>
        )}
      </div>

      {/* Main Content Area */}
      <div className="px-3 sm:px-6 py-3 space-y-3">
        {/* Replay Control Toolbar (Interactive Bar Replay) */}
        {replayMode && data?.bars && data.bars.length > 0 && (
          <div className="bg-[#0b101c] border-2 border-amber-500/60 rounded-xl p-3.5 space-y-3 shadow-xl shadow-amber-500/5">
            <div className="flex flex-wrap items-center justify-between gap-3">
              {/* Title & Active Candle Info */}
              <div className="flex flex-wrap items-center gap-3">
                <div className="flex items-center gap-1.5">
                  <span className="w-2.5 h-2.5 rounded-full bg-amber-400 animate-ping" />
                  <span className="font-bold text-amber-400 uppercase tracking-wider text-xs">
                    🎬 BAR REPLAY ({timeframe})
                  </span>
                </div>

                <span className="text-slate-500">|</span>

                {/* Bar Counter & Current Time */}
                <div className="flex items-center gap-2 text-xs font-mono">
                  <span className="text-white font-bold bg-[#141b2e] px-2 py-0.5 rounded border border-slate-800">
                    Bar {replayIndex + 1} / {data.bars.length}
                  </span>
                  {data.bars[replayIndex] && (
                    <span className="text-cyan-300 font-semibold">
                      {formatBarTime(data.bars[replayIndex].time, "CT")} CT ·{" "}
                      {formatBarTime(data.bars[replayIndex].time, "ET")} ET
                    </span>
                  )}
                </div>

                {/* Replay Candle Price */}
                {data.bars[replayIndex] && (
                  <div className="flex items-center gap-1.5 text-xs font-mono">
                    <span className="text-slate-400">Close:</span>
                    <span className="text-emerald-400 font-bold text-sm">
                      ${data.bars[replayIndex].close.toFixed(2)}
                    </span>
                  </div>
                )}

                {/* Replay Option Price(s) */}
                {effectiveMode === "BOTH" ? (
                  <div className="flex items-center gap-2.5 text-xs font-mono">
                    {data.call_contract?.bars?.[replayIndex] && (
                      <div className="flex items-center gap-1">
                        <span className="text-emerald-400 font-semibold">🟢 Call:</span>
                        <span className="text-emerald-300 font-bold">
                          ${data.call_contract.bars[replayIndex].close.toFixed(2)}
                        </span>
                      </div>
                    )}
                    {data.put_contract?.bars?.[replayIndex] && (
                      <div className="flex items-center gap-1">
                        <span className="text-rose-400 font-semibold">🔴 Put:</span>
                        <span className="text-rose-300 font-bold">
                          ${data.put_contract.bars[replayIndex].close.toFixed(2)}
                        </span>
                      </div>
                    )}
                    {data.call_contract?.bars?.[replayIndex] && data.put_contract?.bars?.[replayIndex] && (
                      <div className="flex items-center gap-1">
                        <span className="text-amber-400 font-semibold">⚡ Straddle:</span>
                        <span className="text-amber-300 font-bold">
                          ${(data.call_contract.bars[replayIndex].close + data.put_contract.bars[replayIndex].close).toFixed(2)}
                        </span>
                      </div>
                    )}
                  </div>
                ) : (
                  data.contract?.bars?.[replayIndex] && (
                    <div className="flex items-center gap-1.5 text-xs font-mono">
                      <span className="text-slate-400">Option:</span>
                      <span className="text-amber-400 font-bold text-sm">
                        ${data.contract.bars[replayIndex].close.toFixed(2)}
                      </span>
                    </div>
                  )
                )}
              </div>

              {/* Exit Replay Button */}
              <div className="flex items-center gap-2">
                <button
                  type="button"
                  onClick={toggleReplay}
                  className="px-2.5 py-1 rounded bg-slate-800 hover:bg-slate-700 text-slate-300 hover:text-white text-xs font-mono flex items-center gap-1"
                  title="Exit Replay and return to full chart (Key: R)"
                >
                  <span>✕</span>
                  <span>Exit Replay</span>
                </button>
              </div>
            </div>

            {/* Controls Row: Play/Pause, Step Back/Fwd, Jump Buttons, Speed */}
            <div className="flex flex-wrap items-center justify-between gap-3 pt-1 border-t border-slate-800/80">
              {/* Playback Step Controls */}
              <div className="flex items-center gap-1.5">
                {/* Jump to Beginning */}
                <button
                  type="button"
                  onClick={() => {
                    setIsPlaying(false);
                    setReplayIndex(0);
                  }}
                  className="px-2.5 py-1 rounded bg-[#141a2c] hover:bg-slate-700 text-slate-300 text-xs font-mono font-bold"
                  title="Jump to first bar"
                >
                  ⏮ Start
                </button>

                {/* Step Back 1 Bar */}
                <button
                  type="button"
                  onClick={() => {
                    setIsPlaying(false);
                    setReplayIndex((p) => Math.max(0, p - 1));
                  }}
                  className="px-2.5 py-1 rounded bg-[#141a2c] hover:bg-slate-700 text-slate-200 text-xs font-mono font-bold"
                  title="Step back 1 bar (Left Arrow)"
                >
                  ◄ Step
                </button>

                {/* Play / Pause Toggle */}
                <button
                  type="button"
                  onClick={() => setIsPlaying(!isPlaying)}
                  className={`px-4 py-1 rounded text-xs font-mono font-bold flex items-center gap-1.5 transition ${
                    isPlaying
                      ? "bg-amber-400 text-black shadow-md shadow-amber-400/20"
                      : "bg-emerald-500 text-black hover:bg-emerald-400 shadow-md shadow-emerald-500/20"
                  }`}
                  title="Play / Pause (Spacebar)"
                >
                  <span>{isPlaying ? "⏸ Pause" : "▶ Play"}</span>
                </button>

                {/* Step Forward 1 Bar */}
                <button
                  type="button"
                  onClick={() => {
                    setIsPlaying(false);
                    setReplayIndex((p) => Math.min(p + 1, data.bars.length - 1));
                  }}
                  className="px-2.5 py-1 rounded bg-[#141a2c] hover:bg-slate-700 text-slate-200 text-xs font-mono font-bold"
                  title="Step forward 1 bar (Right Arrow)"
                >
                  Step ►
                </button>

                {/* Jump to End / Latest */}
                <button
                  type="button"
                  onClick={() => {
                    setIsPlaying(false);
                    setReplayIndex(data.bars.length - 1);
                  }}
                  className="px-2.5 py-1 rounded bg-[#141a2c] hover:bg-slate-700 text-slate-300 text-xs font-mono font-bold"
                  title="Jump to latest bar"
                >
                  ⏭ End
                </button>
              </div>

              {/* StockPulse Setup Fast Jumps (8:30 AM Open & 8:35 AM Validation Candle) */}
              <div className="flex items-center gap-1.5 bg-[#141a2c] border border-slate-800 rounded-lg p-0.5 text-xs font-mono">
                <button
                  type="button"
                  onClick={() => {
                    setIsPlaying(false);
                    const openIdx = getBarIndexByTimeET(data.bars, 9, 30);
                    setReplayIndex(openIdx);
                  }}
                  className="px-2 py-0.5 rounded text-[11px] text-slate-300 hover:text-white hover:bg-slate-800"
                  title="Jump directly to Market Open (8:30 AM CT / 9:30 AM ET)"
                >
                  🔔 8:30 Open
                </button>
                <button
                  type="button"
                  onClick={() => {
                    setIsPlaying(false);
                    const valIdx = getBarIndexByTimeET(data.bars, 9, 35);
                    setReplayIndex(valIdx);
                  }}
                  className="px-2 py-0.5 rounded text-[11px] bg-cyan-500/20 border border-cyan-500/40 text-cyan-300 font-bold hover:bg-cyan-500/30"
                  title="Jump to 8:35 AM CT Validation Candle (where signal is generated)"
                >
                  ⚡ 8:35 Validation
                </button>
                <button
                  type="button"
                  onClick={() => {
                    setIsPlaying(false);
                    const h1Idx = getBarIndexByTimeET(data.bars, 10, 35);
                    setReplayIndex(h1Idx);
                  }}
                  className="px-2 py-0.5 rounded text-[11px] bg-amber-500/20 border border-amber-500/40 text-amber-300 font-bold hover:bg-amber-500/30"
                  title="Jump to 9:35 AM CT / 10:35 AM ET (1-Hour Session Mark)"
                >
                  ⏱️ 9:35 (1H)
                </button>
              </div>

              {/* Playback Speed Selector */}
              <div className="flex items-center gap-1 bg-[#141a2c] border border-slate-800 rounded-lg p-0.5 text-xs font-mono">
                {[0.5, 1, 2, 3, 5, 10].map((s) => (
                  <button
                    key={s}
                    type="button"
                    onClick={() => setReplaySpeed(s)}
                    className={`px-2 py-0.5 rounded text-[11px] font-semibold transition ${
                      replaySpeed === s
                        ? "bg-amber-500 text-black font-bold"
                        : "text-slate-400 hover:text-white hover:bg-slate-800"
                    }`}
                  >
                    {s}x
                  </button>
                ))}
              </div>
            </div>

            {/* Timeline Scrubber Range Slider */}
            <div className="space-y-1">
              <input
                type="range"
                min={0}
                max={data.bars.length - 1}
                value={replayIndex}
                onChange={(e) => {
                  setIsPlaying(false);
                  setReplayIndex(parseInt(e.target.value, 10));
                }}
                className="w-full accent-amber-400 bg-slate-800 h-2 rounded-lg cursor-pointer"
              />
              <div className="flex justify-between text-[10px] font-mono text-slate-500">
                <span>Session Start</span>
                <span>🔔 8:30 AM CT Open</span>
                <span>⚡ 8:35 AM Validation</span>
                <span>Session End</span>
              </div>
            </div>
          </div>
        )}

        {/* Session & Timezone Info Banner */}
        {data?.session && (
          <div className={`rounded-xl px-3.5 py-2 flex flex-col sm:flex-row sm:items-center justify-between gap-2 text-xs font-mono border shadow-sm ${
            replayMode
              ? "bg-amber-500/15 border-amber-500/40 text-amber-200"
              : data.session.status === "HISTORICAL_SESSION"
              ? "bg-cyan-500/10 border-cyan-500/30 text-cyan-200"
              : data.session.status === "PRE_MARKET"
              ? "bg-amber-500/10 border-amber-500/30 text-amber-200"
              : data.session.status === "REGULAR_HOURS"
              ? "bg-emerald-500/10 border-emerald-500/30 text-emerald-200"
              : "bg-slate-800/40 border-slate-700/60 text-slate-300"
          }`}>
            <div className="flex flex-wrap items-center gap-2">
              <span className="font-bold flex items-center gap-1.5 uppercase">
                <span className="text-sm">
                  {replayMode ? "🎬" : data.session.status === "HISTORICAL_SESSION" ? "📅" : data.session.status === "PRE_MARKET" ? "⏱️" : data.session.status === "REGULAR_HOURS" ? "🟢" : "🔒"}
                </span>
                <span>
                  {replayMode
                    ? `Replay Active · Bar ${replayIndex + 1} of ${data.bars.length}`
                    : data.session.label}
                </span>
              </span>
              <span>·</span>
              <span>
                {replayMode && data.bars[replayIndex]
                  ? `Candle Time: ${formatBarTime(data.bars[replayIndex].time, "CT")} CT (${formatBarTime(data.bars[replayIndex].time, "ET")} ET)`
                  : `Current Time: ${data.session.current_time_ct} (${data.session.current_time_et})`}
              </span>
              <span>·</span>
              <span>Regular Open: <b>{data.session.regular_open_ct} ({data.session.regular_open_et})</b></span>
              <span>·</span>
              <span>5m Validation: <b>{data.session.validation_ct}</b></span>
            </div>

            <div className="flex items-center gap-3 text-[11px]">
              <label className="flex items-center gap-1.5 cursor-pointer text-slate-300 hover:text-white">
                <input
                  type="checkbox"
                  checked={extendedHours}
                  onChange={(e) => {
                    setExtendedHours(e.target.checked);
                    chartInstanceRef.current?.remove();
                    chartInstanceRef.current = null;
                  }}
                  className="accent-amber-500 rounded"
                />
                <span>🌙 Extended Hours (Pre-Market)</span>
              </label>
            </div>
          </div>
        )}

        {/* Key Levels Strip & 4H Narrowed Focus Banner */}
        {data && (
          <div className="bg-[#0e1424] border border-slate-800/80 rounded-xl p-3 space-y-2.5 shadow-lg">
            {/* Top row: Underlying Price, PDH, PDL, SL, MID, Entry, Targets */}
            <div className="flex flex-wrap items-center justify-between gap-2.5">
              <div className="flex flex-wrap items-center gap-3">
                <div className="flex items-baseline gap-1.5">
                  <span className="text-lg sm:text-xl font-bold font-mono text-white">
                    {data.ticker}
                  </span>
                  <span className="text-xl sm:text-2xl font-black font-mono text-emerald-400">
                    ${(replayMode && data.bars[replayIndex] ? data.bars[replayIndex].close : data.current_price)?.toFixed(2)}
                  </span>
                </div>

                {/* Pre-Drawn Key Levels Badges */}
                <div className="flex flex-wrap items-center gap-1.5 text-xs font-mono">
                  {/* PDH */}
                  <span className="px-2 py-0.5 rounded bg-red-500/15 border border-red-500/40 text-red-300 font-bold">
                    🔴 PDH: ${data.levels.pdh ? data.levels.pdh.toFixed(2) : "—"}
                  </span>
                  {/* PDL */}
                  <span className="px-2 py-0.5 rounded bg-emerald-500/15 border border-emerald-500/40 text-emerald-300 font-bold">
                    🟢 PDL: ${data.levels.pdl ? data.levels.pdl.toFixed(2) : "—"}
                  </span>
                  {/* STOP LOSS (SL) - Highly Prominent */}
                  <span className="px-2.5 py-0.5 rounded bg-red-600/30 border border-red-500 text-red-200 font-bold shadow-sm animate-pulse">
                    🛑 SL: ${data.levels.stop_loss ? data.levels.stop_loss.toFixed(2) : "—"}
                  </span>
                  {/* Midpoint */}
                  <span className="px-2 py-0.5 rounded bg-slate-800 border border-slate-700 text-slate-300">
                    ⚪ MID: ${data.levels.pd_mid ? data.levels.pd_mid.toFixed(2) : "—"}
                  </span>
                  {/* Entry */}
                  {data.levels.entry && (
                    <span className="px-2 py-0.5 rounded bg-amber-500/15 border border-amber-500/40 text-amber-300 font-bold">
                      🟡 ENTRY: ${data.levels.entry.toFixed(2)}
                    </span>
                  )}
                  {/* Targets */}
                  {data.levels.target_1 && (
                    <span className="px-2 py-0.5 rounded bg-cyan-500/15 border border-cyan-500/40 text-cyan-300 font-bold">
                      🎯 T1: ${data.levels.target_1.toFixed(2)}
                      {data.levels.target_2 && ` · T2: $${data.levels.target_2.toFixed(2)}`}
                    </span>
                  )}

                  {/* Active / Current Candle Bias & Pattern */}
                  {(() => {
                    const activeBars = replayMode && data?.bars ? data.bars.slice(0, Math.min(replayIndex + 1, data.bars.length)) : data?.bars;
                    if (!activeBars || activeBars.length === 0) return null;
                    const curr = activeBars[activeBars.length - 1];
                    const prev = activeBars.length > 1 ? activeBars[activeBars.length - 2] : undefined;
                    const { bias, change, pctChange, pattern } = getBarPatternInfo(curr, prev);
                    return (
                      <div className="flex items-center gap-1.5 text-xs font-mono">
                        <span className={`px-2 py-0.5 rounded font-bold border ${
                          bias === "Bullish"
                            ? "bg-emerald-500/15 border-emerald-500/40 text-emerald-300"
                            : bias === "Bearish"
                            ? "bg-rose-500/15 border-rose-500/40 text-rose-300"
                            : "bg-slate-800 border-slate-700 text-slate-300"
                        }`}>
                          {bias === "Bullish" ? "🟢 Bullish" : bias === "Bearish" ? "🔴 Bearish" : "⚪ Neutral"} ({change >= 0 ? "+" : ""}{pctChange.toFixed(2)}%)
                        </span>
                        {pattern && (
                          <span className="px-2 py-0.5 rounded bg-amber-500/20 border border-amber-500/40 text-amber-300 font-bold animate-pulse">
                            ⚡ {pattern}
                          </span>
                        )}
                      </div>
                    );
                  })()}
                </div>
              </div>

              {/* Levels & Indicators Visibility Controls */}
              <div className="flex flex-wrap items-center gap-2 text-xs font-mono text-slate-400">
                <label className="flex items-center gap-1 cursor-pointer hover:text-white">
                  <input
                    type="checkbox"
                    checked={showPdhPdl}
                    onChange={(e) => setShowPdhPdl(e.target.checked)}
                    className="accent-cyan-500 rounded"
                  />
                  <span>PDH/PDL</span>
                </label>
                <label className="flex items-center gap-1 cursor-pointer hover:text-white">
                  <input
                    type="checkbox"
                    checked={showSl}
                    onChange={(e) => setShowSl(e.target.checked)}
                    className="accent-red-500 rounded"
                  />
                  <span>SL</span>
                </label>
                <label className="flex items-center gap-1 cursor-pointer hover:text-white">
                  <input
                    type="checkbox"
                    checked={showEntryTarget}
                    onChange={(e) => setShowEntryTarget(e.target.checked)}
                    className="accent-amber-500 rounded"
                  />
                  <span>Entry/Targets</span>
                </label>
                <label className="flex items-center gap-1 cursor-pointer hover:text-white">
                  <input
                    type="checkbox"
                    checked={showVolume}
                    onChange={(e) => setShowVolume(e.target.checked)}
                    className="accent-emerald-500 rounded"
                  />
                  <span>Volume</span>
                </label>

                {/* Master Quick Toggle to Hide or Show Indicators */}
                <button
                  type="button"
                  onClick={() => {
                    const anyActive = showEmaBand || showPatterns;
                    if (anyActive) {
                      setShowEmaBand(false);
                      setShowPatterns(false);
                    } else {
                      setShowEmaBand(true);
                      setShowPatterns(true);
                    }
                  }}
                  className={`px-2 py-0.5 rounded text-[11px] font-mono font-bold flex items-center gap-1 transition-all ${
                    showEmaBand || showPatterns
                      ? "bg-amber-500/20 text-amber-300 border border-amber-500/40 hover:bg-amber-500/30"
                      : "bg-slate-800 text-slate-400 border border-slate-700 hover:text-white hover:bg-slate-700"
                  }`}
                  title={showEmaBand || showPatterns ? "Hide 20 EMA and Candlestick Pattern Indicators" : "Show 20 EMA and Candlestick Pattern Indicators"}
                >
                  <span>{showEmaBand || showPatterns ? "👁️ Hide Indicators" : "👁️ Show Indicators"}</span>
                </button>

                <label className="flex items-center gap-1.5 cursor-pointer hover:text-white" title="20 EMA of High, Close, and Low">
                  <input
                    type="checkbox"
                    checked={showEmaBand}
                    onChange={(e) => setShowEmaBand(e.target.checked)}
                    className="accent-amber-400 rounded"
                  />
                  <span className="flex items-center gap-1">
                    <span className="w-1.5 h-1.5 rounded-full bg-sky-400" title="20 EMA High (Cyan)" />
                    <span className="w-1.5 h-1.5 rounded-full bg-amber-400" title="20 EMA Close (Gold)" />
                    <span className="w-1.5 h-1.5 rounded-full bg-rose-400" title="20 EMA Low (Rose)" />
                    <span>20 EMA</span>
                  </span>
                </label>
                <label className="flex items-center gap-1 cursor-pointer hover:text-white" title="Inside Bars (IB) and Engulfing Bars">
                  <input
                    type="checkbox"
                    checked={showPatterns}
                    onChange={(e) => setShowPatterns(e.target.checked)}
                    className="accent-emerald-400 rounded"
                  />
                  <span>Patterns</span>
                </label>
              </div>
            </div>

            {/* 4H Structure & Narrowed Focus Alert Banner */}
            {data.levels.focus_badge && (
              <div className="bg-[#12192e] border border-cyan-500/30 rounded-lg px-3 py-2 flex flex-col sm:flex-row sm:items-center justify-between gap-2 text-xs font-mono">
                <div className="flex flex-wrap items-center gap-2">
                  <span className="text-cyan-300 font-bold uppercase">🎯 4H Narrowed Focus:</span>
                  <span className="px-2 py-0.5 rounded bg-cyan-500/20 text-cyan-200 border border-cyan-500/40 font-bold">
                    {data.levels.focus_badge}
                  </span>
                  {data.levels.four_hour_pattern && (
                    <span className="px-2 py-0.5 rounded bg-amber-500/20 border border-amber-500/40 text-amber-300 font-bold animate-pulse">
                      ⚡ 4H: {data.levels.four_hour_pattern}
                    </span>
                  )}
                  {data.levels.valid_outcome && (
                    <span className="text-slate-300 text-[11px]">
                      · Outcome: <b>{data.levels.valid_outcome}</b>
                    </span>
                  )}
                </div>
                {data.levels.focus_rule && (
                  <span className="text-slate-400 text-[11px] italic">
                    {data.levels.focus_rule}
                  </span>
                )}
              </div>
            )}
          </div>
        )}

        {/* Contract Controls & Overlap Bar (Default 0 DTE, Adjustable) */}
        {data?.contract && (() => {
          const callPrice = data.call_contract
            ? (replayMode && data.call_contract.bars?.[replayIndex] ? data.call_contract.bars[replayIndex].close : data.call_contract.last_price)
            : undefined;
          const putPrice = data.put_contract
            ? (replayMode && data.put_contract.bars?.[replayIndex] ? data.put_contract.bars[replayIndex].close : data.put_contract.last_price)
            : undefined;
          const straddlePrice = (callPrice !== undefined && putPrice !== undefined)
            ? (callPrice + putPrice)
            : undefined;
          const singleContractPrice = (replayMode && data.contract.bars?.[replayIndex] ? data.contract.bars[replayIndex].close : data.contract.last_price);

          return (
          <div className="bg-[#0f1422] border border-amber-500/30 rounded-xl p-3 space-y-2.5 shadow-md">
            <div className="flex flex-wrap items-center justify-between gap-3">
              {/* Contract Identity & Live Quotes */}
              {effectiveMode === "BOTH" ? (
                <div className="flex flex-wrap items-center gap-3">
                  <div className="flex items-center gap-2">
                    <span className="px-2 py-0.5 rounded text-xs font-bold font-mono bg-gradient-to-r from-emerald-500/20 via-cyan-500/20 to-rose-500/20 text-cyan-300 border border-cyan-500/40">
                      ⚡ DUAL OVERLAY ({data.contract.dte} DTE)
                    </span>
                    <span className="font-bold text-sm sm:text-base font-mono text-white">
                      ${data.contract.strike?.toFixed(2)} Straddle
                    </span>
                  </div>

                  {/* Dual Quotes */}
                  <div className="flex flex-wrap items-center gap-2.5 text-xs font-mono">
                    {data.call_contract && (
                      <div className="flex items-center gap-1.5 bg-emerald-950/40 border border-emerald-500/30 px-2 py-0.5 rounded">
                        <span className="text-emerald-400 font-bold">🟢 CALL:</span>
                        <span className="text-sm font-black text-emerald-300">
                          ${callPrice?.toFixed(2)}
                        </span>
                        <span className="text-slate-400 text-[10px]">
                          (${data.call_contract.bid?.toFixed(2)} / ${data.call_contract.ask?.toFixed(2)})
                        </span>
                      </div>
                    )}

                    {data.put_contract && (
                      <div className="flex items-center gap-1.5 bg-rose-950/40 border border-rose-500/30 px-2 py-0.5 rounded">
                        <span className="text-rose-400 font-bold">🔴 PUT:</span>
                        <span className="text-sm font-black text-rose-300">
                          ${putPrice?.toFixed(2)}
                        </span>
                        <span className="text-slate-400 text-[10px]">
                          (${data.put_contract.bid?.toFixed(2)} / ${data.put_contract.ask?.toFixed(2)})
                        </span>
                      </div>
                    )}

                    {straddlePrice !== undefined && (
                      <div className="flex items-center gap-1.5 bg-amber-950/40 border border-amber-500/30 px-2 py-0.5 rounded">
                        <span className="text-amber-400 font-bold">⚡ STRADDLE:</span>
                        <span className="text-sm font-black text-amber-300">
                          ${straddlePrice.toFixed(2)}
                        </span>
                      </div>
                    )}
                  </div>
                </div>
              ) : (
                <div className="flex flex-wrap items-center gap-3">
                  <div className="flex items-center gap-2">
                    <span className="px-2 py-0.5 rounded text-xs font-bold font-mono bg-amber-500/20 text-amber-300 border border-amber-500/40">
                      🟡 CONTRACT OVERLAP ({data.contract.dte} DTE)
                    </span>
                    <span className="font-bold text-sm sm:text-base font-mono text-white">
                      {data.contract.description}
                    </span>
                  </div>

                  {/* Contract Price & Bid/Ask */}
                  <div className="flex items-center gap-2 text-xs font-mono">
                    <span className="text-base font-black text-amber-400">
                      ${singleContractPrice?.toFixed(2)}
                    </span>
                    <span className="text-slate-400 text-[11px]">
                      Bid: <b>${data.contract.bid?.toFixed(2)}</b> / Ask:{" "}
                      <b>${data.contract.ask?.toFixed(2)}</b>
                    </span>
                  </div>
                </div>
              )}

              {/* Interactive Contract Adjusters */}
              <div className="flex flex-wrap items-center gap-2.5 text-xs font-mono">
                {/* DTE Selector (Default 0 DTE) */}
                <div className="flex items-center gap-1 bg-[#151c30] border border-slate-700/80 rounded-lg p-0.5">
                  {[0, 1, 2, 3, 7].map((d) => (
                    <button
                      key={d}
                      type="button"
                      onClick={() => {
                        setDte(d);
                        setCustomExpiry("");
                      }}
                      className={`px-2 py-0.5 rounded text-[11px] font-semibold transition-colors ${
                        dte === d && !customExpiry
                          ? "bg-amber-500 text-black font-bold shadow-sm"
                          : "text-slate-400 hover:text-white hover:bg-slate-800"
                      }`}
                    >
                      {d === 0 ? "0 DTE" : `${d} DTE`}
                    </button>
                  ))}
                  {/* Expiry Dropdown */}
                  {data.available_expiries && data.available_expiries.length > 0 && (
                    <select
                      value={customExpiry || data.contract.expiry}
                      onChange={(e) => {
                        setCustomExpiry(e.target.value);
                      }}
                      className="bg-transparent text-amber-300 font-bold text-[11px] font-mono px-1 py-0.5 focus:outline-none cursor-pointer"
                    >
                      {data.available_expiries.map((exp) => {
                        const sessionDate = targetDate || new Date().toISOString().split("T")[0];
                        const diffTime = new Date(`${exp}T12:00:00Z`).getTime() - new Date(`${sessionDate}T12:00:00Z`).getTime();
                        const diffDays = Math.max(0, Math.round(diffTime / 86400000));
                        return (
                          <option key={exp} value={exp} className="bg-[#121626] text-white">
                            {exp} ({diffDays} DTE)
                          </option>
                        );
                      })}
                    </select>
                  )}
                </div>

                {/* Type Selector (CALL vs PUT vs BOTH) */}
                <div className="flex items-center bg-[#151c30] border border-slate-700/80 rounded-lg p-0.5">
                  <button
                    type="button"
                    onClick={() => setOptionType("CALL")}
                    className={`px-2.5 py-0.5 rounded text-[11px] font-bold transition-colors ${
                      effectiveMode === "CALL"
                        ? "bg-emerald-500 text-black shadow-sm"
                        : "text-slate-400 hover:text-emerald-300"
                    }`}
                  >
                    CALL
                  </button>
                  <button
                    type="button"
                    onClick={() => setOptionType("PUT")}
                    className={`px-2.5 py-0.5 rounded text-[11px] font-bold transition-colors ${
                      effectiveMode === "PUT"
                        ? "bg-rose-500 text-white shadow-sm"
                        : "text-slate-400 hover:text-rose-300"
                    }`}
                  >
                    PUT
                  </button>
                  <button
                    type="button"
                    onClick={() => setOptionType("BOTH")}
                    className={`px-2.5 py-0.5 rounded text-[11px] font-bold transition-colors flex items-center gap-1 ${
                      effectiveMode === "BOTH"
                        ? "bg-gradient-to-r from-emerald-500 to-rose-500 text-white shadow-sm font-black"
                        : "text-slate-400 hover:text-cyan-300"
                    }`}
                    title="Draw both CALL and PUT contracts simultaneously on left price scale"
                  >
                    <span>⚡</span>
                    <span>BOTH</span>
                  </button>
                </div>

                {/* Strike Stepper & Dropdown */}
                <div className="flex items-center gap-1 bg-[#151c30] border border-slate-700/80 rounded-lg p-0.5">
                  <button
                    type="button"
                    onClick={() => handleStrikeStep("down")}
                    title="Step Down Strike"
                    className="px-2 py-0.5 text-slate-300 hover:text-white hover:bg-slate-800 rounded font-bold"
                  >
                    -
                  </button>
                  {data.available_strikes && data.available_strikes.length > 0 ? (
                    <select
                      value={data.contract.strike}
                      onChange={(e) => setStrike(parseFloat(e.target.value))}
                      className="bg-transparent text-amber-300 font-bold text-xs font-mono px-1 py-0.5 focus:outline-none cursor-pointer"
                    >
                      {data.available_strikes.map((s) => (
                        <option key={s} value={s} className="bg-[#121626] text-white">
                          ${s.toFixed(2)}
                        </option>
                      ))}
                    </select>
                  ) : (
                    <span className="text-amber-300 font-bold px-1.5">
                      ${data.contract.strike?.toFixed(2)}
                    </span>
                  )}
                  <button
                    type="button"
                    onClick={() => handleStrikeStep("up")}
                    title="Step Up Strike"
                    className="px-2 py-0.5 text-slate-300 hover:text-white hover:bg-slate-800 rounded font-bold"
                  >
                    +
                  </button>
                </div>

                {/* Overlap Toggle */}
                <label className="flex items-center gap-1.5 cursor-pointer text-slate-300 hover:text-white ml-1">
                  <input
                    type="checkbox"
                    checked={showContractOverlap}
                    onChange={(e) => setShowContractOverlap(e.target.checked)}
                    className="accent-amber-500 rounded"
                  />
                  <span>Show Overlap (Left Scale)</span>
                </label>
              </div>
            </div>

            {/* Contract Greeks & Vol/OI Strip */}
            {effectiveMode === "BOTH" && data.call_contract && data.put_contract ? (
              <div className="grid grid-cols-1 md:grid-cols-2 gap-2 text-[11px] font-mono text-slate-400 border-t border-amber-500/15 pt-2">
                <div className="flex flex-wrap items-center gap-3 bg-emerald-950/20 px-2.5 py-1 rounded border border-emerald-500/20">
                  <span className="text-emerald-400 font-bold">🟢 CALL:</span>
                  <span>Delta: <b className="text-white">{data.call_contract.delta}</b></span>
                  <span>Theta: <b className="text-rose-400">{data.call_contract.theta}</b></span>
                  <span>IV: <b className="text-amber-300">{data.call_contract.iv}%</b></span>
                  <span>Vol: <b className="text-white">{data.call_contract.volume?.toLocaleString()}</b></span>
                  <span>OI: <b className="text-white">{data.call_contract.open_interest?.toLocaleString()}</b></span>
                </div>
                <div className="flex flex-wrap items-center gap-3 bg-rose-950/20 px-2.5 py-1 rounded border border-rose-500/20">
                  <span className="text-rose-400 font-bold">🔴 PUT:</span>
                  <span>Delta: <b className="text-white">{data.put_contract.delta}</b></span>
                  <span>Theta: <b className="text-rose-400">{data.put_contract.theta}</b></span>
                  <span>IV: <b className="text-amber-300">{data.put_contract.iv}%</b></span>
                  <span>Vol: <b className="text-white">{data.put_contract.volume?.toLocaleString()}</b></span>
                  <span>OI: <b className="text-white">{data.put_contract.open_interest?.toLocaleString()}</b></span>
                </div>
              </div>
            ) : (
              <div className="flex flex-wrap items-center gap-4 text-[11px] font-mono text-slate-400 border-t border-amber-500/15 pt-2">
                <span>Delta: <b className="text-white">{data.contract.delta}</b></span>
                <span>Gamma: <b className="text-white">{data.contract.gamma}</b></span>
                <span>Theta: <b className="text-red-400">{data.contract.theta}</b></span>
                <span>IV: <b className="text-amber-300">{data.contract.iv}%</b></span>
                <span>Vol: <b className="text-white">{data.contract.volume?.toLocaleString()}</b></span>
                <span>OI: <b className="text-white">{data.contract.open_interest?.toLocaleString()}</b></span>
                <span className="text-slate-500 text-[10px] ml-auto">
                  Black-Scholes synthesized & calibrated to live quote
                </span>
              </div>
            )}
          </div>
          );
        })()}

        {/* Hover Crosshair Info Bar */}
        {hoverInfo && (
          <div className="bg-[#121829] border border-cyan-500/30 rounded-lg px-3 py-1.5 text-xs font-mono flex flex-wrap items-center gap-x-4 gap-y-1">
            <span className="text-slate-400">
              Time: <b className="text-white">{hoverInfo.time}</b>
            </span>
            <span className="text-slate-400">
              {data?.ticker} Close:{" "}
              <b className="text-emerald-400">
                ${hoverInfo.underlyingClose !== undefined ? hoverInfo.underlyingClose.toFixed(2) : "—"}
              </b>
            </span>

            {/* Candle Direction & % Change */}
            {hoverInfo.candleBias && (
              <span className={`px-2 py-0.5 rounded text-[11px] font-bold border ${
                hoverInfo.candleBias === "Bullish"
                  ? "bg-emerald-500/20 border-emerald-500/40 text-emerald-300"
                  : hoverInfo.candleBias === "Bearish"
                  ? "bg-rose-500/20 border-rose-500/40 text-rose-300"
                  : "bg-slate-800 border-slate-700 text-slate-300"
              }`}>
                {hoverInfo.candleBias === "Bullish" ? "🟢 Bullish" : hoverInfo.candleBias === "Bearish" ? "🔴 Bearish" : "⚪ Neutral"}
                {hoverInfo.candleChangePct !== undefined && ` (${hoverInfo.candleChangePct >= 0 ? "+" : ""}${hoverInfo.candleChangePct.toFixed(2)}%)`}
              </span>
            )}

            {/* Pattern Badge */}
            {hoverInfo.pattern && (
              <span className="px-2 py-0.5 rounded text-[11px] font-bold bg-amber-500/20 border border-amber-500/40 text-amber-300 animate-pulse">
                ⚡ {hoverInfo.pattern}
              </span>
            )}

            {/* 20 Period EMA Band Values (High, Close, Low) */}
            {showEmaBand && (hoverInfo.emaHigh !== undefined || hoverInfo.emaClose !== undefined || hoverInfo.emaLow !== undefined) && (
              <div className="flex items-center gap-2.5 text-[11px] border-l border-slate-700/80 pl-3">
                {hoverInfo.emaHigh !== undefined && (
                  <span className="text-slate-400">
                    EMA 20 H: <b className="text-sky-400">${hoverInfo.emaHigh.toFixed(2)}</b>
                  </span>
                )}
                {hoverInfo.emaClose !== undefined && (
                  <span className="text-slate-400">
                    EMA 20 C: <b className="text-amber-400 font-bold">${hoverInfo.emaClose.toFixed(2)}</b>
                  </span>
                )}
                {hoverInfo.emaLow !== undefined && (
                  <span className="text-slate-400">
                    EMA 20 L: <b className="text-rose-400">${hoverInfo.emaLow.toFixed(2)}</b>
                  </span>
                )}
              </div>
            )}

            {/* Option Overlay Values */}
            {hoverInfo.contractValue !== undefined && (
              <span className="text-slate-400">
                Option Premium:{" "}
                <b className="text-amber-400">${hoverInfo.contractValue.toFixed(2)}</b>
              </span>
            )}
            {hoverInfo.callValue !== undefined && (
              <span className="text-slate-400">
                🟢 Call:{" "}
                <b className="text-emerald-400">${hoverInfo.callValue.toFixed(2)}</b>
              </span>
            )}
            {hoverInfo.putValue !== undefined && (
              <span className="text-slate-400">
                🔴 Put:{" "}
                <b className="text-rose-400">${hoverInfo.putValue.toFixed(2)}</b>
              </span>
            )}
            {hoverInfo.callValue !== undefined && hoverInfo.putValue !== undefined && (
              <span className="text-slate-400">
                ⚡ Straddle:{" "}
                <b className="text-amber-300">${(hoverInfo.callValue + hoverInfo.putValue).toFixed(2)}</b>
              </span>
            )}
          </div>
        )}

        {/* Chart Canvas */}
        <div className="relative bg-[#090d16] border border-slate-800 rounded-xl overflow-hidden shadow-2xl">
          {loading && (
            <div className="absolute inset-0 bg-black/60 backdrop-blur-sm z-20 flex items-center justify-center">
              <div className="flex items-center gap-2 text-cyan-400 font-mono text-sm">
                <span className="animate-spin text-lg">🔄</span>
                <span>Loading Live 5m Candlesticks & Contract Overlap...</span>
              </div>
            </div>
          )}

          {error && (
            <div className="p-8 text-center text-red-400 font-mono text-sm">
              <p>⚠️ {error}</p>
              <button
                type="button"
                onClick={() => fetchData(false)}
                className="mt-3 px-3 py-1 bg-red-500/20 border border-red-500/40 rounded text-xs text-white"
              >
                Retry
              </button>
            </div>
          )}

          {/* Lightweight Chart container */}
          <div ref={chartContainerRef} className="w-full" style={{ height: 540 }} />
        </div>

        {/* Chart Legend & Shortcuts Guide */}
        <div className="flex flex-wrap items-center justify-between gap-3 text-[11px] font-mono text-slate-400 px-1">
          <div className="flex flex-wrap items-center gap-3">
            <span className="flex items-center gap-1">
              <span className="w-2.5 h-0.5 bg-red-400 inline-block" />
              <span>PDH</span>
            </span>
            <span className="flex items-center gap-1">
              <span className="w-2.5 h-0.5 bg-emerald-400 inline-block" />
              <span>PDL</span>
            </span>
            <span className="flex items-center gap-1">
              <span className="w-2.5 h-0.5 bg-red-600 inline-block" />
              <span>🛑 SL</span>
            </span>
            <span className="flex items-center gap-1">
              <span className="w-2.5 h-0.5 bg-amber-400 inline-block" />
              <span>Entry</span>
            </span>

            {/* 20 EMA Band Legend */}
            {showEmaBand && (
              <>
                <span className="flex items-center gap-1">
                  <span className="w-2.5 h-0.5 border-b border-dashed border-sky-400 inline-block" />
                  <span className="text-sky-300">EMA 20 High</span>
                </span>
                <span className="flex items-center gap-1">
                  <span className="w-2.5 h-0.5 bg-amber-400 inline-block" />
                  <span className="text-amber-300 font-bold">EMA 20 Close</span>
                </span>
                <span className="flex items-center gap-1">
                  <span className="w-2.5 h-0.5 border-b border-dashed border-rose-400 inline-block" />
                  <span className="text-rose-300">EMA 20 Low</span>
                </span>
              </>
            )}

            {/* Pattern Legend */}
            {showPatterns && (
              <>
                <span className="flex items-center gap-1 text-emerald-400">
                  <span>▲ Bull Engulf</span>
                </span>
                <span className="flex items-center gap-1 text-rose-400">
                  <span>▼ Bear Engulf</span>
                </span>
                <span className="flex items-center gap-1 text-slate-300">
                  <span>● Inside Bar (IB 🟢/🔴)</span>
                </span>
              </>
            )}

            {effectiveMode === "BOTH" ? (
              <>
                <span className="flex items-center gap-1">
                  <span className="w-2.5 h-0.5 bg-emerald-400 inline-block" />
                  <span>🟢 Call (Left Scale)</span>
                </span>
                <span className="flex items-center gap-1">
                  <span className="w-2.5 h-0.5 bg-rose-500 inline-block" />
                  <span>🔴 Put (Left Scale)</span>
                </span>
              </>
            ) : (
              <span className="flex items-center gap-1">
                <span className="w-2.5 h-0.5 bg-yellow-500 inline-block" />
                <span>Option (Left Scale)</span>
              </span>
            )}
          </div>
          <div>
            <span>Scroll to zoom · Drag to pan · Live 5s</span>
          </div>
        </div>
      </div>
    </div>
  );
}

export default function LiveChartPage() {
  return (
    <Suspense
      fallback={
        <div className="w-full min-h-screen bg-[#080b13] flex items-center justify-center text-cyan-400 font-mono text-sm">
          <span className="animate-spin text-lg mr-2">🔄</span> Loading Live Chart...
        </div>
      }
    >
      <LiveChartInner />
    </Suspense>
  );
}
