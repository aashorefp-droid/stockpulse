"use client";

import { useState, useEffect, useRef } from "react";
import { getFibDescription } from "./FibTable";

declare global {
  interface Window {
    TradingView: any;
  }
}

const API_BASE = process.env.NEXT_PUBLIC_API_URL || "https://stockpulse-pkpj.onrender.com";

interface DailyTradeChartProps {
  ticker: string;
  onTickerChange?: (ticker: string) => void;
  availableTickers?: string[];
  initialAsOfDate?: string;
  asOfDate?: string;
  volumeProfile?: any;
  entryGrade?: any;
  verdict?: string;
  finalJudgement?: any;
}

export default function DailyTradeChart({
  ticker,
  onTickerChange,
  availableTickers = [],
  initialAsOfDate = "",
  asOfDate: propAsOfDate = "",
  volumeProfile: propVolumeProfile,
  entryGrade: propEntryGrade,
  verdict: propVerdict,
  finalJudgement: propFinalJudgement,
}: DailyTradeChartProps) {
  const containerRef = useRef<HTMLDivElement>(null);
  const sma20ValRef = useRef<HTMLSpanElement>(null);
  const sma50ValRef = useRef<HTMLSpanElement>(null);
  const sma200ValRef = useRef<HTMLSpanElement>(null);
  const [loading, setLoading] = useState(true);
  const [data, setData] = useState<any>(null);
  const [viewMode, setViewMode] = useState<"levels" | "embed" | "finviz">("levels");
  const [showSma, setShowSma] = useState(true);
  const [showFuture, setShowFuture] = useState(false);
  const [showRetestLine, setShowRetestLine] = useState(true);
  const [entryMode, setEntryMode] = useState<"trigger" | "latest_low" | "avg_low" | "retest">("trigger");
  const [selectedRedDay, setSelectedRedDay] = useState<any>(null);
  const [showAllRedDayLines, setShowAllRedDayLines] = useState(true);

  // Backtest state
  const effectiveInitialDate = propAsOfDate || initialAsOfDate || "";
  const [backtestMode, setBacktestMode] = useState(Boolean(effectiveInitialDate));
  const [asOfDate, setAsOfDate] = useState(effectiveInitialDate);
  const [showHistoryModal, setShowHistoryModal] = useState(false);
  const [historyLoading, setHistoryLoading] = useState(false);
  const [historyData, setHistoryData] = useState<any>(null);
  const [historyDays, setHistoryDays] = useState(365);

  useEffect(() => {
    const nextDate = propAsOfDate || initialAsOfDate || "";
    setAsOfDate(nextDate);
    setBacktestMode(Boolean(nextDate));
    setShowFuture(false);
    setSelectedRedDay(null);
  }, [propAsOfDate, initialAsOfDate]);

  // Fetch daily chart data with calculated trade levels (and backtest outcome if asOfDate is active)
  useEffect(() => {
    let isMounted = true;
    setLoading(true);
    setSelectedRedDay(null);

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

      const isBacktest = Boolean(data.is_backtest || (backtestMode && asOfDate));
      const activeBars = (isBacktest && !showFuture && data.hist_bars && data.hist_bars.length > 0)
        ? data.hist_bars
        : data.bars;
      const activeVolume = (isBacktest && !showFuture && data.hist_volume_bars && data.hist_volume_bars.length > 0)
        ? data.hist_volume_bars
        : data.volume_bars;
      const activeSma20 = (isBacktest && !showFuture && data.hist_sma20 && data.hist_sma20.length > 0)
        ? data.hist_sma20
        : data.sma20;
      const activeSma50 = (isBacktest && !showFuture && data.hist_sma50 && data.hist_sma50.length > 0)
        ? data.hist_sma50
        : data.sma50;
      const activeSma200 = (isBacktest && !showFuture && data.hist_sma200 && data.hist_sma200.length > 0)
        ? data.hist_sma200
        : data.sma200;

      // 1. Volume Series
      const volumeSeries = chart.addHistogramSeries({
        color: "#26a69a",
        priceFormat: { type: "volume" },
        priceScaleId: "volume",
      });
      chart.priceScale("volume").applyOptions({
        scaleMargins: { top: 0.8, bottom: 0 },
      });
      if (activeVolume && activeVolume.length > 0) {
        volumeSeries.setData(activeVolume);
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
      candleSeries.setData(activeBars);

      // 3. SMA Overlays (20, 50, 200)
      let sma20Series: any = null;
      let sma50Series: any = null;
      let sma200Series: any = null;

      if (showSma) {
        if (activeSma20 && activeSma20.length > 0) {
          sma20Series = chart.addLineSeries({
            color: "#60a5fa",
            lineWidth: 1,
            title: "20 SMA",
            priceLineVisible: false,
            lastValueVisible: false,
          });
          sma20Series.setData(activeSma20);
        }
        if (activeSma50 && activeSma50.length > 0) {
          sma50Series = chart.addLineSeries({
            color: "#f59e0b",
            lineWidth: 1.5,
            title: "50 SMA",
            priceLineVisible: false,
            lastValueVisible: false,
          });
          sma50Series.setData(activeSma50);
        }
        if (activeSma200 && activeSma200.length > 0) {
          sma200Series = chart.addLineSeries({
            color: "#a855f7",
            lineWidth: 2,
            title: "200 SMA",
            priceLineVisible: false,
            lastValueVisible: false,
          });
          sma200Series.setData(activeSma200);
        }
      }

      // 4. Horizontal Trade Price Lines
      const { entry, stop_loss, target1, target2 } = data.levels || {};
      
      // If backtest mode with future bars shown, draw lines from entry date onwards
      let lineBars = activeBars.slice(-40);
      if (isBacktest && showFuture && (data.as_of || asOfDate)) {
        const targetDate = data.as_of || asOfDate;
        const asOfIdx = activeBars.findIndex((b: any) => b.time >= targetDate);
        if (asOfIdx >= 0) {
          lineBars = activeBars.slice(Math.max(0, asOfIdx - 5));
        }
      }

      // Best Entry Line (Solid Emerald)
      if (entry && lineBars.length > 0) {
        const entryLine = chart.addLineSeries({
          color: "#00e5a0",
          lineWidth: 2,
          lineStyle: LineStyle.Solid,
          title: `ENTRY $${entry}`,
          priceLineVisible: true,
          lastValueVisible: true,
        });
        entryLine.setData(lineBars.map((b: any) => ({ time: b.time, value: entry })));
      }

      // Stop Loss Line (Dashed Red)
      if (stop_loss && lineBars.length > 0) {
        const stopLine = chart.addLineSeries({
          color: "#ff4d6a",
          lineWidth: 2,
          lineStyle: LineStyle.Dashed,
          title: `STOP $${stop_loss}`,
          priceLineVisible: true,
          lastValueVisible: true,
        });
        stopLine.setData(lineBars.map((b: any) => ({ time: b.time, value: stop_loss })));
      }

      // Target 1 Exit Line (Dashed Green)
      if (target1 && lineBars.length > 0) {
        const t1Line = chart.addLineSeries({
          color: "#34d399",
          lineWidth: 2,
          lineStyle: LineStyle.Dashed,
          title: `TARGET 1 $${target1}`,
          priceLineVisible: true,
          lastValueVisible: true,
        });
        t1Line.setData(lineBars.map((b: any) => ({ time: b.time, value: target1 })));
      }

      // Target 2 Exit Line (Dashed Cyan)
      if (target2 && lineBars.length > 0) {
        const t2Line = chart.addLineSeries({
          color: "#38bdf8",
          lineWidth: 2,
          lineStyle: LineStyle.Dashed,
          title: `TARGET 2 $${target2}`,
          priceLineVisible: true,
          lastValueVisible: true,
        });
        t2Line.setData(lineBars.map((b: any) => ({ time: b.time, value: target2 })));
      }

      // Retest Entry Line(s) (Latest Low, AVG Low, PWL & Prior Week Red Day Lows)
      const pwRedDays = data.levels?.pw_red_day_lows || [];
      const latestLowVal = data.levels?.pw_latest_low;
      const avgLowVal = data.levels?.pw_avg_low;
      const pwlVal = data.levels?.retest_entry;
      const primaryRetestLevel = entryMode === "latest_low"
        ? (latestLowVal ?? pwlVal)
        : entryMode === "avg_low"
        ? (avgLowVal ?? pwlVal)
        : (selectedRedDay?.low ?? pwlVal);

      if (showRetestLine && lineBars.length > 0) {
        // Draw all other prior week red day lows as dashed coral support lines
        if (showAllRedDayLines && pwRedDays.length > 0) {
          pwRedDays.forEach((rd: any) => {
            if (rd.low && Math.abs(rd.low - (primaryRetestLevel || 0)) > 0.01 && (!latestLowVal || Math.abs(rd.low - latestLowVal) > 0.01)) {
              const rdLine = chart.addLineSeries({
                color: "rgba(255, 77, 106, 0.45)",
                lineWidth: 1,
                lineStyle: LineStyle.Dashed,
                title: `RED LOW $${rd.low} (${rd.date_label})`,
                priceLineVisible: false,
                lastValueVisible: true,
              });
              rdLine.setData(lineBars.map((b: any) => ({ time: b.time, value: rd.low })));
            }
          });
        }

        // Draw Latest Low line as dashed gold reference if not primary
        if (latestLowVal && entryMode !== "latest_low" && Math.abs(latestLowVal - (primaryRetestLevel || 0)) > 0.01) {
          const latLine = chart.addLineSeries({
            color: "rgba(245, 200, 66, 0.65)",
            lineWidth: 1,
            lineStyle: LineStyle.Dashed,
            title: `LATEST LOW $${latestLowVal} (${data.levels?.pw_latest_date ?? "Prior Wk"}${data.levels?.pw_latest_day ? ` ${data.levels?.pw_latest_day}` : ""})`,
            priceLineVisible: false,
            lastValueVisible: true,
          });
          latLine.setData(lineBars.map((b: any) => ({ time: b.time, value: latestLowVal })));
        }

        // Draw AVG Low line as dashed purple reference if not primary
        if (avgLowVal && entryMode !== "avg_low" && Math.abs(avgLowVal - (primaryRetestLevel || 0)) > 0.01) {
          const avgLine = chart.addLineSeries({
            color: "rgba(168, 85, 247, 0.65)",
            lineWidth: 1,
            lineStyle: LineStyle.Dashed,
            title: `AVG LOW $${avgLowVal}`,
            priceLineVisible: false,
            lastValueVisible: true,
          });
          avgLine.setData(lineBars.map((b: any) => ({ time: b.time, value: avgLowVal })));
        }

        // Draw PWL line if entryMode is latest_low or avg_low (so trader sees all key prior week levels)
        if ((entryMode === "latest_low" || entryMode === "avg_low") && pwlVal && Math.abs(pwlVal - (primaryRetestLevel || 0)) > 0.01) {
          const pwlLine = chart.addLineSeries({
            color: "rgba(56, 189, 248, 0.65)",
            lineWidth: 1,
            lineStyle: LineStyle.Dotted,
            title: `PWL $${pwlVal}`,
            priceLineVisible: false,
            lastValueVisible: true,
          });
          pwlLine.setData(lineBars.map((b: any) => ({ time: b.time, value: pwlVal })));
        }

        // Draw primary active retest level
        if (primaryRetestLevel) {
          const isLatest = entryMode === "latest_low";
          const isAvg = entryMode === "avg_low";
          const isPwl = selectedRedDay ? selectedRedDay.is_pwl : (!isLatest && !isAvg);
          const lineColor = isLatest ? "#f5c842" : isAvg ? "#a855f7" : "#38bdf8";
          const lineTitle = isLatest
            ? `LATEST LOW $${primaryRetestLevel} (${data.levels?.pw_latest_date ?? "Prior Wk"}${data.levels?.pw_latest_day ? ` ${data.levels?.pw_latest_day}` : ""})`
            : isAvg
            ? `AVG LOW $${primaryRetestLevel}`
            : `${isPwl ? 'PWL' : 'RED LOW'} $${primaryRetestLevel}${selectedRedDay ? ` (${selectedRedDay.date_label})` : ''}`;
          const retestLine = chart.addLineSeries({
            color: lineColor,
            lineWidth: 2,
            lineStyle: (isLatest || isAvg) ? LineStyle.Solid : LineStyle.Dotted,
            title: lineTitle,
            priceLineVisible: true,
            lastValueVisible: true,
          });
          retestLine.setData(lineBars.map((b: any) => ({ time: b.time, value: primaryRetestLevel })));
        }
      }

      // 5. Markers (Entry trigger + Exit outcome marker)
      if (data.markers && data.markers.length > 0) {
        const activeMarkers = (isBacktest && !showFuture)
          ? data.markers.filter((m: any) => m.time <= (data.as_of_date || data.as_of || asOfDate))
          : data.markers;
        candleSeries.setMarkers(activeMarkers);
      }

      // Update real-time SMA values in legend on crosshair hover
      const lastSma20 = data.sma20 && data.sma20.length > 0 ? data.sma20[data.sma20.length - 1]?.value : null;
      const lastSma50 = data.sma50 && data.sma50.length > 0 ? data.sma50[data.sma50.length - 1]?.value : null;
      const lastSma200 = data.sma200 && data.sma200.length > 0 ? data.sma200[data.sma200.length - 1]?.value : null;

      chart.subscribeCrosshairMove((param: any) => {
        if (!param || !param.time || !param.seriesData) {
          if (sma20ValRef.current) sma20ValRef.current.textContent = lastSma20 != null ? `$${lastSma20.toFixed(2)}` : "—";
          if (sma50ValRef.current) sma50ValRef.current.textContent = lastSma50 != null ? `$${lastSma50.toFixed(2)}` : "—";
          if (sma200ValRef.current) sma200ValRef.current.textContent = lastSma200 != null ? `$${lastSma200.toFixed(2)}` : "—";
          return;
        }

        const v20 = sma20Series ? (param.seriesData.get(sma20Series) as any)?.value : undefined;
        const v50 = sma50Series ? (param.seriesData.get(sma50Series) as any)?.value : undefined;
        const v200 = sma200Series ? (param.seriesData.get(sma200Series) as any)?.value : undefined;

        if (sma20ValRef.current) {
          sma20ValRef.current.textContent = v20 != null ? `$${v20.toFixed(2)}` : (lastSma20 != null ? `$${lastSma20.toFixed(2)}` : "—");
        }
        if (sma50ValRef.current) {
          sma50ValRef.current.textContent = v50 != null ? `$${v50.toFixed(2)}` : (lastSma50 != null ? `$${lastSma50.toFixed(2)}` : "—");
        }
        if (sma200ValRef.current) {
          sma200ValRef.current.textContent = v200 != null ? `$${v200.toFixed(2)}` : (lastSma200 != null ? `$${lastSma200.toFixed(2)}` : "—");
        }
      });

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
  }, [viewMode, data, showSma, showFuture, showRetestLine, selectedRedDay, showAllRedDayLines, entryMode]);

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

  const pwRedDays: any[] = levels.pw_red_day_lows || [];
  const selectedRedDayLow = selectedRedDay?.low;

  let activeRetestEntry = levels.entry;
  let activeRetestZoneMin = levels.entry_zone_min;
  let activeRetestZoneMax = levels.entry_zone_max;
  let activeRetestLabel = "Breakout Trigger Entry";
  let activeRetestDiffPct = 0;
  let activeRetestRiskPct = levels.risk_pct;
  let activeRetestRrT1 = levels.rr_t1;
  let activeRetestRrT2 = levels.rr_t2;
  let activeRetestT1Gain = levels.target1_gain_pct;
  let activeRetestT2Gain = levels.target2_gain_pct;

  if (entryMode === "latest_low" && levels.pw_latest_low != null) {
    activeRetestEntry = levels.pw_latest_low;
    activeRetestZoneMin = levels.pw_latest_zone_min;
    activeRetestZoneMax = levels.pw_latest_zone_max;
    activeRetestLabel = `Last Week Latest Low (${levels.pw_latest_date ?? "Prior Wk"}${levels.pw_latest_day ? ` ${levels.pw_latest_day}` : ""})`;
    activeRetestDiffPct = levels.pw_latest_diff_pct ?? 0;
    activeRetestRiskPct = levels.pw_latest_risk_pct;
    activeRetestRrT1 = levels.pw_latest_rr_t1;
    activeRetestRrT2 = levels.pw_latest_rr_t2;
    activeRetestT1Gain = levels.pw_latest_t1_gain;
    activeRetestT2Gain = levels.pw_latest_t2_gain;
  } else if (entryMode === "avg_low" && levels.pw_avg_low != null) {
    activeRetestEntry = levels.pw_avg_low;
    activeRetestZoneMin = levels.pw_avg_zone_min;
    activeRetestZoneMax = levels.pw_avg_zone_max;
    activeRetestLabel = `Average of Lows (PWL $${levels.retest_entry?.toFixed(2)} + Latest $${levels.pw_latest_low?.toFixed(2)})`;
    activeRetestDiffPct = levels.pw_avg_diff_pct ?? 0;
    activeRetestRiskPct = levels.pw_avg_risk_pct;
    activeRetestRrT1 = levels.pw_avg_rr_t1;
    activeRetestRrT2 = levels.pw_avg_rr_t2;
    activeRetestT1Gain = levels.pw_avg_t1_gain;
    activeRetestT2Gain = levels.pw_avg_t2_gain;
  } else if (entryMode === "retest") {
    activeRetestEntry = selectedRedDayLow != null ? selectedRedDayLow : levels.retest_entry;
    activeRetestZoneMin = selectedRedDay?.zone_min != null ? selectedRedDay.zone_min : levels.retest_zone_min;
    activeRetestZoneMax = selectedRedDay?.zone_max != null ? selectedRedDay.zone_max : levels.retest_zone_max;
    activeRetestLabel = selectedRedDay
      ? `${selectedRedDay.date_label} (${selectedRedDay.day_name}) Red Day Low`
      : levels.retest_label;
    activeRetestDiffPct = selectedRedDay ? selectedRedDay.diff_pct : (levels.retest_diff_pct ?? 0);
    activeRetestRiskPct = levels.retest_risk_pct;
    activeRetestRrT1 = levels.retest_rr_t1;
    activeRetestRrT2 = levels.retest_rr_t2;
    activeRetestT1Gain = levels.retest_t1_gain;
    activeRetestT2Gain = levels.retest_t2_gain;
  }

  if (activeRetestEntry && levels.stop_loss && levels.target1 && levels.target2 && entryMode !== "trigger") {
    const atrBuffer = (levels.atr || 1) * 0.4;
    const rRisk = Math.max(activeRetestEntry - levels.stop_loss, atrBuffer, activeRetestEntry * 0.015);
    activeRetestRiskPct = Math.round((rRisk / activeRetestEntry) * 1000) / 10;
    activeRetestRrT1 = rRisk > 0 ? Math.round(((levels.target1 - activeRetestEntry) / rRisk) * 10) / 10 : 2.5;
    activeRetestRrT2 = rRisk > 0 ? Math.round(((levels.target2 - activeRetestEntry) / rRisk) * 10) / 10 : 3.5;
    activeRetestT1Gain = Math.round(((levels.target1 - activeRetestEntry) / activeRetestEntry) * 1000) / 10;
    activeRetestT2Gain = Math.round(((levels.target2 - activeRetestEntry) / activeRetestEntry) * 1000) / 10;
  }

  const latestSma20 = data?.sma20 && data.sma20.length > 0 ? data.sma20[data.sma20.length - 1]?.value : null;
  const latestSma50 = data?.sma50 && data.sma50.length > 0 ? data.sma50[data.sma50.length - 1]?.value : null;
  const latestSma200 = data?.sma200 && data.sma200.length > 0 ? data.sma200[data.sma200.length - 1]?.value : null;

  // Synthesize or retrieve final judgement based on entry alert + volume profile decision
  const finalJudgement = data?.final_judgement ?? propFinalJudgement ?? (() => {
    const vp = data?.vol_profile ?? propVolumeProfile;
    const v = data?.verdict ?? propVerdict;
    const eg = data?.entry_grade ?? propEntryGrade;
    const cp = data?.current_price;
    const tr = data?.levels;
    if (!vp && !eg) return null;

    const direction = (v || (isBull ? "LONG" : "SHORT") || "LONG").toUpperCase().includes("BEAR") ? "SHORT" : "LONG";
    const gradeLetter = eg?.entry_grade || "C";
    const gradeLabel = eg?.entry_label || "Neutral";
    const entryPrice = tr?.entry || cp || 0;
    const vah = vp?.vah;
    const val = vp?.val;
    const poc = vp?.poc;
    const volTrend = vp?.vol_trend || "FLAT";
    const volSurge = Boolean(vp?.vol_surge);
    const volRatio = vp?.vol_ratio || 1.0;
    const aboveVah = cp && vah ? cp > vah : false;
    const belowVal = cp && val ? cp < val : false;
    const insideVa = !aboveVah && !belowVal;

    if (direction === "LONG") {
      if (aboveVah && (volTrend === "ACCUMULATING" || volSurge)) {
        return {
          verdict_title: "HIGH CONVICTION BUY · INSTITUTIONALLY CONFIRMED",
          badge: "STRONG BUY",
          color: "emerald",
          confluence_score: 95,
          summary: `Entry alert (${gradeLetter} Grade) aligns with institutional volume accumulation above fair value (${vah ? `$${vah.toFixed(2)} VAH` : "VAH"}). Volume confirms breakout momentum.`,
          entry_call: `${gradeLetter} Grade (${gradeLabel}) at $${entryPrice.toFixed(2)}`,
          vp_call: `Above VAH ($${vah?.toFixed(2)}) · ${volTrend} (${volRatio.toFixed(1)}x)`,
        };
      } else if (insideVa || (!belowVal && val && cp >= val)) {
        return {
          verdict_title: "VALUE AREA ACCUMULATION BUY",
          badge: "DIP BUY SUPPORT",
          color: "cyan",
          confluence_score: 85,
          summary: `Price holding institutional Value Area support (${val ? `$${val.toFixed(2)}` : ""}${vah ? `–$${vah.toFixed(2)}` : ""}). High R/R dip accumulation with defined stop below VAL.`,
          entry_call: `${gradeLetter} Grade (${gradeLabel}) at $${entryPrice.toFixed(2)}`,
          vp_call: `Inside Value Area · POC $${poc?.toFixed(2)}`,
        };
      } else if (belowVal) {
        return {
          verdict_title: "VOLUME DIVERGENCE · CAUTION ON LONGS",
          badge: "CAUTION TRAP",
          color: "amber",
          confluence_score: 45,
          summary: `Bullish entry alert conflict: Price is trading below Value Area Low (${val ? `$${val.toFixed(2)}` : ""}) with distribution volume. High probability of false breakout. Wait for reclaim of VAL or reduce size.`,
          entry_call: `${gradeLetter} Grade (${gradeLabel}) at $${entryPrice.toFixed(2)}`,
          vp_call: `Below VAL ($${val?.toFixed(2)}) · ${volTrend} (${volRatio.toFixed(1)}x)`,
        };
      } else {
        return {
          verdict_title: "MODERATE BULLISH BIAS · MONITOR VOLUME",
          badge: "SPECULATIVE BUY",
          color: "emerald",
          confluence_score: 70,
          summary: `Bullish price signal with steady volume profile. Maintain strict risk management at stop loss.`,
          entry_call: `${gradeLetter} Grade at $${entryPrice.toFixed(2)}`,
          vp_call: `${vp?.detail || "Normal volume profile"}`,
        };
      }
    } else {
      if (belowVal && (volTrend === "ACCUMULATING" || volSurge)) {
        return {
          verdict_title: "INSTITUTIONAL BREAKDOWN CONFIRMED",
          badge: "CONFIRMED SHORT",
          color: "rose",
          confluence_score: 95,
          summary: `Decisive breakdown below Value Area Low (${val ? `$${val.toFixed(2)}` : ""}) confirmed by expanding volume surge. Institutional liquidation favors aggressive short continuation.`,
          entry_call: `${gradeLetter} Grade (${gradeLabel}) Short at $${entryPrice.toFixed(2)}`,
          vp_call: `Below VAL ($${val?.toFixed(2)}) · ${volTrend} Surge (${volRatio.toFixed(1)}x)`,
        };
      } else if (insideVa) {
        return {
          verdict_title: "BEARISH FADE · SITTING NEAR POC SUPPORT",
          badge: "CAUTION SHORT",
          color: "amber",
          confluence_score: 60,
          summary: `Short signal active, but price is sitting near heavy Point of Control liquidity (${poc ? `$${poc.toFixed(2)}` : ""}). Expect choppy support; wait for breakdown below VAL.`,
          entry_call: `${gradeLetter} Grade Short at $${entryPrice.toFixed(2)}`,
          vp_call: `Inside Value Area · Near POC $${poc?.toFixed(2)}`,
        };
      } else {
        return {
          verdict_title: "BEARISH BIAS · VOLUME RESISTANCE",
          badge: "LEAN SHORT",
          color: "rose",
          confluence_score: 75,
          summary: `Bearish trajectory with institutional resistance at ${vah ? `$${vah.toFixed(2)} VAH` : "VAH"}.`,
          entry_call: `${gradeLetter} Grade Short at $${entryPrice.toFixed(2)}`,
          vp_call: `${vp?.detail || "Normal volume profile"}`,
        };
      }
    }
  })();

  const fibLevels = data?.fib_levels || {};
  const nearestFib = data?.nearest_fib;
  const weeklyFibLevels = data?.weekly_fib_levels || data?.levels?.weekly_fib_levels || {};
  const weeklyNearestFib = data?.weekly_nearest_fib || data?.levels?.weekly_nearest_fib;
  const currentP = data?.current_price || levels.entry || 0;
  let resolvedFibCall = finalJudgement?.fib_call;
  if (!resolvedFibCall && nearestFib && fibLevels[nearestFib] != null) {
    const fVal = fibLevels[nearestFib];
    const desc = getFibDescription(nearestFib, "52w");
    const distPct = currentP > 0 ? Math.round(((fVal - currentP) / currentP) * 1000) / 10 : 0;
    const role = Math.abs(distPct) < 0.5 ? "At Level" : fVal > currentP ? "Resistance" : "Support";
    resolvedFibCall = `${nearestFib} (${desc}) $${fVal.toFixed(2)} · ${role}`;
  }

  let resolvedWeeklyFibCall = "";
  if (weeklyNearestFib && weeklyFibLevels[weeklyNearestFib] != null) {
    const fVal = weeklyFibLevels[weeklyNearestFib];
    const desc = getFibDescription(weeklyNearestFib, "week");
    const distPct = currentP > 0 ? Math.round(((fVal - currentP) / currentP) * 1000) / 10 : 0;
    const role = Math.abs(distPct) < 0.5 ? "At Level" : fVal > currentP ? "Resistance" : "Support";
    resolvedWeeklyFibCall = `${weeklyNearestFib} (${desc}) $${fVal.toFixed(2)} · ${role}`;
  }

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
            {(data?.as_of || (backtestMode && asOfDate)) && (
              <span className="text-[11px] font-mono font-bold text-[#f5c842] bg-[#3d3a0a] px-2 py-0.5 rounded border border-[#f5c842]/40 flex items-center gap-1">
                <span>⏪</span>
                <span>Backtest: {data?.as_of || asOfDate}</span>
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
                setShowFuture(false);
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

          {/* Outcome toggle when backtesting */}
          {(data?.is_backtest || (backtestMode && asOfDate)) && (
            <button
              onClick={() => setShowFuture(!showFuture)}
              className={`px-3 py-1 rounded text-xs font-semibold border flex items-center gap-1.5 transition-all ${
                showFuture
                  ? "bg-purple-500/25 border-purple-400 text-purple-200 shadow-md shadow-purple-500/20"
                  : "bg-[#1a2d3d] border-[#4d9fff]/40 text-[#4d9fff] hover:text-white"
              }`}
              title={showFuture ? "Cut off chart at the backtest date (no future hindsight)" : "Show subsequent bars to see the trade outcome"}
            >
              <span>{showFuture ? "⏪ Cutoff at As-Of" : `⏩ Show Outcome (+${data?.subsequent_bars_count || (data?.bars?.length ? data.bars.length - (data?.hist_bars?.length || 0) : 0)}d)`}</span>
            </button>
          )}

          {/* Full Backtest Report Button */}
          <button
            onClick={() => runHistoryBacktest(historyDays)}
            className="px-3 py-1 rounded text-xs font-semibold bg-[#1a2d3d] hover:bg-[#25394d] text-[#4d9fff] border border-[#4d9fff]/40 flex items-center gap-1.5 transition-all"
          >
            <span>📊</span>
            <span>1-Yr Backtest Report</span>
          </button>

          {viewMode === "levels" && (
            <>
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

              {levels.retest_entry != null && (
                <button
                  type="button"
                  onClick={() => setShowRetestLine(!showRetestLine)}
                  className={`px-2.5 py-1 rounded text-xs font-semibold border transition-all flex items-center gap-1.5 ${
                    showRetestLine
                      ? "bg-[#0c2838] text-[#38bdf8] border-[#38bdf8]/40 shadow-sm"
                      : "bg-[#131625] text-[#6b7099] border-[#1a1d2e]"
                  }`}
                  title="Toggle Previous Week Low (PWL) Support Line"
                >
                  <span>🔄</span>
                  <span>PWL ${levels.retest_entry?.toFixed(2)}</span>
                </button>
              )}
            </>
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
            <button
              onClick={() => setShowFuture(!showFuture)}
              className={`px-2.5 py-1 rounded text-[11px] font-semibold border transition-all ${
                showFuture
                  ? "bg-purple-500/25 border-purple-400 text-purple-200 shadow-sm"
                  : "bg-surface border-border text-muted hover:text-white"
              }`}
              title={showFuture ? "Cut off chart at the backtest date (no future hindsight)" : "Show subsequent bars to see the trade outcome"}
            >
              {showFuture ? "⏪ Cutoff at As-Of" : `⏩ Show Outcome (+${bt.subsequent_bars_count}d)`}
            </button>
          </div>
        </div>
      )}

      {/* ── Final Judgement Confluence Banner (Entry Alert + Volume Profile) ── */}
      {finalJudgement && finalJudgement.verdict_title && (
        <div className={`px-4 py-3 border-b border-[#1a1d2e] ${
          finalJudgement.color === "emerald"
            ? "bg-gradient-to-r from-emerald-950/60 via-[#0d2219] to-[#0a0d16] border-emerald-500/40"
            : finalJudgement.color === "cyan"
            ? "bg-gradient-to-r from-cyan-950/60 via-[#0d1e28] to-[#0a0d16] border-cyan-500/40"
            : finalJudgement.color === "amber"
            ? "bg-gradient-to-r from-amber-950/60 via-[#241c0e] to-[#0a0d16] border-amber-500/40"
            : finalJudgement.color === "rose"
            ? "bg-gradient-to-r from-rose-950/60 via-[#260e15] to-[#0a0d16] border-rose-500/40"
            : "bg-[#10131f] border-slate-700/40"
        }`}>
          <div className="flex flex-wrap items-center justify-between gap-2 mb-2">
            <div className="flex items-center gap-2">
              <span className="text-lg">⚡</span>
              <div className="flex flex-wrap items-center gap-2">
                <span className="text-xs font-black tracking-wider uppercase text-white font-mono">
                  FINAL JUDGEMENT:
                </span>
                <span className={`text-sm font-black font-mono tracking-wide ${
                  finalJudgement.color === "emerald" ? "text-emerald-400" :
                  finalJudgement.color === "cyan" ? "text-cyan-400" :
                  finalJudgement.color === "amber" ? "text-amber-400" :
                  finalJudgement.color === "rose" ? "text-rose-400" : "text-slate-300"
                }`}>
                  {finalJudgement.verdict_title}
                </span>
              </div>
            </div>

            <div className="flex items-center gap-2">
              <span className={`text-[11px] font-mono font-bold px-2.5 py-0.5 rounded border uppercase ${
                finalJudgement.color === "emerald" ? "bg-emerald-500/20 text-emerald-300 border-emerald-500/50 shadow-sm" :
                finalJudgement.color === "cyan" ? "bg-cyan-500/20 text-cyan-300 border-cyan-500/50 shadow-sm" :
                finalJudgement.color === "amber" ? "bg-amber-500/20 text-amber-300 border-amber-500/50 shadow-sm" :
                finalJudgement.color === "rose" ? "bg-rose-500/20 text-rose-300 border-rose-500/50 shadow-sm" :
                "bg-slate-500/20 text-slate-300 border-slate-500/50"
              }`}>
                {finalJudgement.badge}
              </span>
              {finalJudgement.confluence_score && (
                <span className="text-xs font-mono font-bold text-yellow bg-black/40 px-2.5 py-0.5 rounded border border-border/50">
                  {finalJudgement.confluence_score}% Confluence
                </span>
              )}
            </div>
          </div>

          {/* Confluence Readout (Entry Alert + Volume Profile + 52W Fibonacci + Week Fibonacci) */}
          <div className={`grid grid-cols-1 ${resolvedFibCall && resolvedWeeklyFibCall ? "sm:grid-cols-2 lg:grid-cols-4" : (resolvedFibCall || resolvedWeeklyFibCall) ? "sm:grid-cols-2 lg:grid-cols-3" : "sm:grid-cols-2"} gap-2 text-xs font-mono mt-2`}>
            <div className="bg-black/50 p-2.5 rounded-lg border border-border/40 flex flex-col justify-start">
              <div className="text-[10px] text-[#8e95bf] font-bold uppercase tracking-wider flex items-center gap-1.5 mb-1">
                <span>🔔</span>
                <span>Entry Alert</span>
              </div>
              <div className="text-white font-semibold text-xs leading-snug break-words">
                {finalJudgement.entry_call || "Evaluating..."}
              </div>
            </div>

            <div className="bg-black/50 p-2.5 rounded-lg border border-border/40 flex flex-col justify-start">
              <div className="text-[10px] text-[#8e95bf] font-bold uppercase tracking-wider flex items-center gap-1.5 mb-1">
                <span>📊</span>
                <span>Volume Profile</span>
              </div>
              <div className="text-[#4d9fff] font-semibold text-xs leading-snug break-words">
                {finalJudgement.vp_call || "Calculating..."}
              </div>
            </div>

            {resolvedFibCall && (
              <div className="bg-black/50 p-2.5 rounded-lg border border-border/40 flex flex-col justify-start">
                <div className="text-[10px] text-[#8e95bf] font-bold uppercase tracking-wider flex items-center gap-1.5 mb-1">
                  <span>📐</span>
                  <span>52W Fibonacci</span>
                </div>
                <div className="text-[#c084fc] font-semibold text-xs leading-snug break-words">
                  {resolvedFibCall}
                </div>
              </div>
            )}

            {resolvedWeeklyFibCall && (
              <div className="bg-black/50 p-2.5 rounded-lg border border-border/40 flex flex-col justify-start">
                <div className="text-[10px] text-[#8e95bf] font-bold uppercase tracking-wider flex items-center gap-1.5 mb-1">
                  <span>📏</span>
                  <span>Week Fibonacci</span>
                </div>
                <div className="text-[#38bdf8] font-semibold text-xs leading-snug break-words">
                  {resolvedWeeklyFibCall}
                </div>
              </div>
            )}
          </div>

          <p className="text-xs text-slate-300 mt-2 leading-relaxed font-sans">
            {finalJudgement.summary}
          </p>
        </div>
      )}

      {/* ── Key Trade Levels Summary Cards ──────────────────────────── */}
      {levels.entry != null && (
        <div className="grid grid-cols-2 md:grid-cols-4 gap-3 p-4 bg-[#0a0b14] border-b border-[#1a1d2e]">
          {/* Best Entry Point Card */}
          <div className="bg-[#0f1d18] border border-[#00e5a0]/30 rounded-xl p-3 shadow-sm flex flex-col justify-between">
            <div>
              <div className="flex items-center justify-between text-xs text-[#6b7099] mb-1 font-semibold uppercase tracking-wider">
                <span>{backtestMode ? "Simulated Entry" : "Best Entry Point"}</span>
                {levels.retest_entry != null || levels.pw_latest_low != null || levels.pw_avg_low != null ? (
                  <div className="flex items-center gap-1 bg-[#0a0b14] p-0.5 rounded border border-[#1a1d2e]">
                    <button
                      type="button"
                      onClick={() => { setEntryMode("trigger"); setSelectedRedDay(null); }}
                      className={`px-1.5 py-0.5 rounded text-[10px] font-bold transition-all ${
                        entryMode === "trigger"
                          ? "bg-[#00e5a0] text-black shadow-sm font-extrabold"
                          : "text-[#6b7099] hover:text-white"
                      }`}
                      title="Trigger entry at current breakout price"
                    >
                      Trigger
                    </button>
                    {levels.pw_latest_low != null && (
                      <button
                        type="button"
                        onClick={() => { setEntryMode("latest_low"); setSelectedRedDay(null); }}
                        className={`px-1.5 py-0.5 rounded text-[10px] font-bold transition-all ${
                          entryMode === "latest_low"
                            ? "bg-[#f5c842] text-black shadow-sm font-extrabold"
                            : "text-[#6b7099] hover:text-[#f5c842]"
                        }`}
                        title={`Retest Last Week Latest Day Low: $${levels.pw_latest_low?.toFixed(2)} (${levels.pw_latest_date} ${levels.pw_latest_day})`}
                      >
                        ⚡ Latest Low
                      </button>
                    )}
                    {levels.pw_avg_low != null && (
                      <button
                        type="button"
                        onClick={() => { setEntryMode("avg_low"); setSelectedRedDay(null); }}
                        className={`px-1.5 py-0.5 rounded text-[10px] font-bold transition-all ${
                          entryMode === "avg_low"
                            ? "bg-[#a855f7] text-white shadow-sm font-extrabold"
                            : "text-[#6b7099] hover:text-[#a855f7]"
                        }`}
                        title={`Retest Balanced Average Low: $${levels.pw_avg_low?.toFixed(2)} (Midpoint of PWL & Latest Low)`}
                      >
                        ⚖️ AVG Low
                      </button>
                    )}
                    {levels.retest_entry != null && (
                      <button
                        type="button"
                        onClick={() => { setEntryMode("retest"); setSelectedRedDay(null); }}
                        className={`px-1.5 py-0.5 rounded text-[10px] font-bold transition-all ${
                          entryMode === "retest" && !selectedRedDay
                            ? "bg-[#38bdf8] text-black shadow-sm font-extrabold"
                            : "text-[#6b7099] hover:text-[#38bdf8]"
                        }`}
                        title="Retest dip entry at Previous Week Low (PWL)"
                      >
                        ★ PWL Low
                      </button>
                    )}
                  </div>
                ) : (
                  <span className="text-[#00e5a0]">🟢 TRIGGER</span>
                )}
              </div>

              <div className="flex items-baseline justify-between">
                <div>
                  <div className={`text-2xl font-mono font-extrabold ${
                    entryMode === "latest_low" ? "text-[#f5c842]" :
                    entryMode === "avg_low" ? "text-[#a855f7]" :
                    entryMode === "retest" ? "text-[#38bdf8]" : "text-[#00e5a0]"
                  }`}>
                    ${(entryMode !== "trigger" && activeRetestEntry != null ? activeRetestEntry : levels.entry)?.toFixed(2)}
                  </div>
                  <div className="text-[11px] font-mono mt-0.5">
                    {entryMode === "latest_low" && activeRetestZoneMin != null ? (
                      <span className="text-[#f5c842]/90 font-medium">
                        Latest Low Zone: ${activeRetestZoneMin?.toFixed(2)} – ${activeRetestZoneMax?.toFixed(2)}
                      </span>
                    ) : entryMode === "avg_low" && activeRetestZoneMin != null ? (
                      <span className="text-[#a855f7]/90 font-medium">
                        AVG Low Zone: ${activeRetestZoneMin?.toFixed(2)} – ${activeRetestZoneMax?.toFixed(2)}
                      </span>
                    ) : entryMode === "retest" && activeRetestZoneMin != null ? (
                      <span className="text-[#38bdf8]/90 font-medium">
                        {selectedRedDay ? `${selectedRedDay.date_label} Zone` : "PWL Zone"}: ${activeRetestZoneMin?.toFixed(2)} – ${activeRetestZoneMax?.toFixed(2)}
                      </span>
                    ) : (
                      <span className="text-[#00e5a0]/80">
                        Zone: ${levels.entry_zone_min?.toFixed(2)} – ${levels.entry_zone_max?.toFixed(2)}
                      </span>
                    )}
                    {levels.last_breakout_date && (
                      <div className="text-[10px] text-[#8e95bf] mt-0.5 flex items-center gap-1 font-mono">
                        <span>⚡ Breakout:</span>
                        <span className={levels.last_breakout_days_ago === 0 ? "text-[#00e5a0] font-bold" : "text-[#4d9fff]"}>
                          {levels.last_breakout_days_ago === 0 ? "Today" : `${levels.last_breakout_days_ago}d ago`} ({levels.last_breakout_date})
                        </span>
                      </div>
                    )}
                  </div>
                </div>

                <div className="text-right">
                  {entryMode === "latest_low" ? (
                    <span className="text-[10px] font-bold px-1.5 py-0.5 rounded border border-[#f5c842]/40 bg-[#f5c842]/20 text-[#f5c842] uppercase tracking-wider inline-flex items-center gap-0.5">
                      ⚡ Latest Low
                    </span>
                  ) : entryMode === "avg_low" ? (
                    <span className="text-[10px] font-bold px-1.5 py-0.5 rounded border border-[#a855f7]/40 bg-[#a855f7]/20 text-[#a855f7] uppercase tracking-wider inline-flex items-center gap-0.5">
                      ⚖️ AVG Low
                    </span>
                  ) : entryMode === "retest" ? (
                    <span className="text-[10px] font-bold px-1.5 py-0.5 rounded border border-[#38bdf8]/40 bg-[#38bdf8]/20 text-[#38bdf8] uppercase tracking-wider inline-flex items-center gap-0.5">
                      ★ {selectedRedDay ? "Red Low" : "PWL Low"}
                    </span>
                  ) : (
                    <span className="text-[10px] font-bold px-1.5 py-0.5 rounded border border-[#00e5a0]/40 bg-[#00e5a0]/20 text-[#00e5a0] uppercase tracking-wider inline-flex items-center gap-0.5">
                      🟢 Trigger
                    </span>
                  )}
                  <div className="text-[11px] font-mono font-bold mt-1 text-slate-400">
                    {entryMode === "trigger"
                      ? (levels.pw_latest_low ? `Lat: $${levels.pw_latest_low?.toFixed(2)}` : (levels.retest_entry ? `PWL: $${levels.retest_entry?.toFixed(2)}` : ""))
                      : `Trig: $${levels.entry?.toFixed(2)}`}
                  </div>
                </div>
              </div>
            </div>

            <div className="mt-2.5 pt-2 border-t border-[#1a2d26] text-[11px] font-mono flex flex-wrap items-center justify-between gap-1">
              <span className="text-slate-300">
                {entryMode === "latest_low" ? (
                  <span className="text-[#f5c842] font-bold">
                    ⚡ {activeRetestLabel} Active
                  </span>
                ) : entryMode === "avg_low" ? (
                  <span className="text-[#a855f7] font-bold">
                    ⚖️ {activeRetestLabel} Active
                  </span>
                ) : entryMode === "retest" ? (
                  <span className="text-[#38bdf8] font-bold">
                    ★ {selectedRedDay ? `${selectedRedDay.date_label} (${selectedRedDay.day_name}) Red Low Active` : "Prev Week Low Active"}
                  </span>
                ) : (
                  <span>
                    PWL: <b className="text-[#38bdf8] font-mono">${levels.retest_entry?.toFixed(2)}</b>
                    {levels.pw_latest_low != null && (
                      <span className="ml-1.5 text-slate-400">· Latest: <b className="text-[#f5c842] font-mono">${levels.pw_latest_low?.toFixed(2)}</b></span>
                    )}
                    {levels.pw_avg_low != null && (
                      <span className="ml-1.5 text-slate-400">· AVG: <b className="text-[#a855f7] font-mono">${levels.pw_avg_low?.toFixed(2)}</b></span>
                    )}
                  </span>
                )}
              </span>
              <span className={`text-[10px] font-semibold font-sans ${entryMode === "latest_low" ? "text-[#f5c842]" : entryMode === "avg_low" ? "text-[#a855f7]" : entryMode === "retest" ? "text-[#38bdf8]" : "text-slate-400"}`}>
                {activeRetestDiffPct != null && activeRetestDiffPct !== 0 && `(${activeRetestDiffPct > 0 ? "+" : ""}${activeRetestDiffPct}%)`}
              </span>
            </div>
          </div>

          {/* Protective Stop Loss */}
          <div className="bg-[#1f0f14] border border-[#ff4d6a]/30 rounded-xl p-3 shadow-sm flex flex-col justify-between">
            <div>
              <div className="flex items-center justify-between text-xs text-[#6b7099] mb-1 font-semibold uppercase tracking-wider">
                <span>Stop Loss</span>
                <span className="text-[#ff4d6a]">🔴 RISK</span>
              </div>
              <div className="text-2xl font-mono font-extrabold text-[#ff4d6a]">
                ${levels.stop_loss?.toFixed(2)}
              </div>
              <div className="text-[11px] text-[#ff4d6a]/80 mt-1 font-mono">
                Risk: -{(entryMode !== "trigger" && activeRetestRiskPct != null ? activeRetestRiskPct : levels.risk_pct)?.toFixed(1)}% ({isBull ? "Below" : "Above"} ATR buffer)
              </div>
            </div>
            {entryMode !== "trigger" && activeRetestRiskPct != null && (
              <div className="mt-2.5 pt-2 border-t border-[#2d1a20] text-[10px] text-slate-400 font-sans">
                Tighter risk from {entryMode === "latest_low" ? "Latest Low" : entryMode === "avg_low" ? "AVG Low" : (selectedRedDay ? `${selectedRedDay.date_label} Red Low` : "PWL")} <span className="text-[#38bdf8] font-mono font-bold">${activeRetestEntry?.toFixed(2)}</span> entry
              </div>
            )}
          </div>

          {/* Target 1 Exit Point */}
          <div className="bg-[#0f1d1f] border border-[#34d399]/30 rounded-xl p-3 shadow-sm flex flex-col justify-between">
            <div>
              <div className="flex items-center justify-between text-xs text-[#6b7099] mb-1 font-semibold uppercase tracking-wider">
                <span>Target 1 (Primary Exit)</span>
                <span className="text-[#34d399]">🎯 {(entryMode !== "trigger" && activeRetestRrT1 != null ? activeRetestRrT1 : levels.rr_t1)?.toFixed(1)}R</span>
              </div>
              <div className="text-2xl font-mono font-extrabold text-[#34d399]">
                ${levels.target1?.toFixed(2)}
              </div>
              <div className="text-[11px] text-[#34d399]/80 mt-1 font-mono">
                +{(entryMode !== "trigger" && activeRetestT1Gain != null ? activeRetestT1Gain : levels.target1_gain_pct)?.toFixed(1)}% gain · ~{levels.t1_days ?? 5} trading days
              </div>
            </div>
            {entryMode !== "trigger" && activeRetestRrT1 != null && (
              <div className="mt-2.5 pt-2 border-t border-[#1a2d24] text-[10px] text-emerald-400 font-sans flex items-center justify-between">
                <span>Expanded {entryMode === "latest_low" ? "Latest Low" : entryMode === "avg_low" ? "AVG Low" : (selectedRedDay ? "Red Low" : "PWL")} R/R:</span>
                <span className="font-mono font-bold">1:{activeRetestRrT1?.toFixed(1)}</span>
              </div>
            )}
          </div>

          {/* Target 2 Exit Point */}
          <div className="bg-[#0f172a] border border-[#38bdf8]/30 rounded-xl p-3 shadow-sm flex flex-col justify-between">
            <div>
              <div className="flex items-center justify-between text-xs text-[#6b7099] mb-1 font-semibold uppercase tracking-wider">
                <span>Target 2 (Runner Exit)</span>
                <span className="text-[#38bdf8]">🚀 {(entryMode !== "trigger" && activeRetestRrT2 != null ? activeRetestRrT2 : levels.rr_t2)?.toFixed(1)}R</span>
              </div>
              <div className="text-2xl font-mono font-extrabold text-[#38bdf8]">
                ${levels.target2?.toFixed(2)}
              </div>
              <div className="text-[11px] text-[#38bdf8]/80 mt-1 font-mono">
                +{(entryMode !== "trigger" && activeRetestT2Gain != null ? activeRetestT2Gain : levels.target2_gain_pct)?.toFixed(1)}% gain · ~{levels.t2_days ?? 10} trading days
              </div>
            </div>
            {entryMode !== "trigger" && activeRetestRrT2 != null && (
              <div className="mt-2.5 pt-2 border-t border-[#152538] text-[10px] text-sky-400 font-sans flex items-center justify-between">
                <span>Expanded {entryMode === "latest_low" ? "Latest Low" : entryMode === "avg_low" ? "AVG Low" : (selectedRedDay ? "Red Low" : "PWL")} R/R:</span>
                <span className="font-mono font-bold">1:{activeRetestRrT2?.toFixed(1)}</span>
              </div>
            )}
          </div>
        </div>
      )}

      {/* ── Prior Week Lows Shelf (Latest Low, PWL & Red Days) ──────────── */}
      {(levels.pw_latest_low != null || levels.pw_avg_low != null || levels.retest_entry != null || (pwRedDays && pwRedDays.length > 0)) && (
        <div className="bg-[#09111e] border-b border-[#1a2d3d] px-4 py-2.5 flex flex-wrap items-center justify-between gap-3 text-xs">
          <div className="flex flex-wrap items-center gap-2.5">
            <span className="text-[#38bdf8] font-bold uppercase tracking-wider flex items-center gap-1.5 text-xs">
              <span>📅</span>
              <span>Prior Week Lows{levels.pw_range_label ? ` (${levels.pw_range_label})` : ""}:</span>
            </span>
            <div className="flex flex-wrap items-center gap-2">
              {/* 1. Last Week Latest Day Low Pill */}
              {levels.pw_latest_low != null && (
                <button
                  type="button"
                  onClick={() => {
                    setEntryMode("latest_low");
                    setSelectedRedDay(null);
                  }}
                  className={`px-2.5 py-1 rounded text-xs font-mono transition-all flex items-center gap-1.5 border ${
                    entryMode === "latest_low"
                      ? "bg-[#f5c842] text-black font-extrabold border-[#f5c842] shadow-md shadow-[#f5c842]/30 ring-1 ring-[#f5c842]"
                      : "bg-[#1f1a0e] text-[#f5c842] hover:text-white border-[#4d3e14] hover:border-[#f5c842]/60"
                  }`}
                  title={`Set entry to Last Week Latest Day Low: $${levels.pw_latest_low?.toFixed(2)} (${levels.pw_latest_date} ${levels.pw_latest_day})`}
                >
                  <span>⚡</span>
                  <span className="font-semibold">Latest Low ({levels.pw_latest_date ?? "Prior Wk"}{levels.pw_latest_day ? ` ${levels.pw_latest_day}` : ""}):</span>
                  <span className="font-bold">${levels.pw_latest_low?.toFixed(2)}</span>
                  {levels.pw_latest_diff_pct != null && (
                    <span className={`text-[10px] ${entryMode === "latest_low" ? "text-slate-900 font-bold" : "text-slate-400"}`}>
                      ({levels.pw_latest_diff_pct > 0 ? "+" : ""}{levels.pw_latest_diff_pct}%)
                    </span>
                  )}
                  <span className={`px-1 py-0.2 rounded text-[9px] font-extrabold uppercase ${entryMode === "latest_low" ? "bg-black text-[#f5c842]" : "bg-[#f5c842]/20 text-[#f5c842]"}`}>
                    LATEST
                  </span>
                </button>
              )}

              {/* 2. Balanced Average of Lows (PWL + Latest Midpoint) Pill */}
              {levels.pw_avg_low != null && (
                <button
                  type="button"
                  onClick={() => {
                    setEntryMode("avg_low");
                    setSelectedRedDay(null);
                  }}
                  className={`px-2.5 py-1 rounded text-xs font-mono transition-all flex items-center gap-1.5 border ${
                    entryMode === "avg_low"
                      ? "bg-[#a855f7] text-white font-extrabold border-[#a855f7] shadow-md shadow-[#a855f7]/30 ring-1 ring-[#a855f7]"
                      : "bg-[#181126] text-[#a855f7] hover:text-white border-[#3c1e5a] hover:border-[#a855f7]/60"
                  }`}
                  title={`Set entry to Balanced Average of Lows (PWL + Latest Midpoint): $${levels.pw_avg_low?.toFixed(2)}`}
                >
                  <span>⚖️</span>
                  <span className="font-semibold">AVG Low:</span>
                  <span className="font-bold">${levels.pw_avg_low?.toFixed(2)}</span>
                  {levels.pw_avg_diff_pct != null && (
                    <span className={`text-[10px] ${entryMode === "avg_low" ? "text-purple-100 font-bold" : "text-slate-400"}`}>
                      ({levels.pw_avg_diff_pct > 0 ? "+" : ""}{levels.pw_avg_diff_pct}%)
                    </span>
                  )}
                  <span className={`px-1 py-0.2 rounded text-[9px] font-extrabold uppercase ${entryMode === "avg_low" ? "bg-white text-purple-900" : "bg-[#a855f7]/20 text-[#a855f7]"}`}>
                    AVG
                  </span>
                </button>
              )}

              {/* 2. Absolute Lowest Low (PWL) Pill */}
              {levels.retest_entry != null && (
                <button
                  type="button"
                  onClick={() => {
                    setEntryMode("retest");
                    setSelectedRedDay(null);
                  }}
                  className={`px-2.5 py-1 rounded text-xs font-mono transition-all flex items-center gap-1.5 border ${
                    entryMode === "retest" && !selectedRedDay
                      ? "bg-[#38bdf8] text-black font-extrabold border-[#38bdf8] shadow-md shadow-[#38bdf8]/30 ring-1 ring-[#38bdf8]"
                      : "bg-[#131d2e] text-[#38bdf8] hover:text-white border-[#1e344d] hover:border-[#38bdf8]/60"
                  }`}
                  title={`Set entry to Absolute Lowest Low of Prior Week (PWL): $${levels.retest_entry?.toFixed(2)}`}
                >
                  <span>★</span>
                  <span className="font-semibold">PWL (Lowest):</span>
                  <span className="font-bold">${levels.retest_entry?.toFixed(2)}</span>
                  {levels.retest_diff_pct != null && (
                    <span className={`text-[10px] ${entryMode === "retest" && !selectedRedDay ? "text-slate-900 font-bold" : "text-slate-400"}`}>
                      ({levels.retest_diff_pct > 0 ? "+" : ""}{levels.retest_diff_pct}%)
                    </span>
                  )}
                  <span className={`px-1 py-0.2 rounded text-[9px] font-extrabold uppercase ${entryMode === "retest" && !selectedRedDay ? "bg-black text-[#38bdf8]" : "bg-[#38bdf8]/30 text-[#38bdf8]"}`}>
                    PWL
                  </span>
                </button>
              )}

              {/* 3. Individual Prior Week Red Day Lows */}
              {pwRedDays.map((rd: any) => {
                const isSelected = entryMode === "retest" && selectedRedDay && selectedRedDay.date === rd.date;
                return (
                  <button
                    key={rd.date}
                    type="button"
                    onClick={() => {
                      setSelectedRedDay(rd);
                      setEntryMode("retest");
                    }}
                    className={`px-2.5 py-1 rounded text-xs font-mono transition-all flex items-center gap-1.5 border ${
                      isSelected
                        ? "bg-[#ff4d6a] text-white font-extrabold border-[#ff4d6a] shadow-md shadow-[#ff4d6a]/30 ring-1 ring-[#ff4d6a]"
                        : "bg-[#1a1215] text-slate-200 hover:text-white border-[#381a20] hover:border-[#ff4d6a]/60"
                    }`}
                    title={`Click to set entry to ${rd.date_label} (${rd.day_name}) Red Day Low: $${rd.low?.toFixed(2)}`}
                  >
                    <span className={isSelected ? "text-white" : "text-[#ff4d6a]"}>🔴</span>
                    <span className="font-semibold">{rd.date_label} ({rd.day_name}):</span>
                    <span className="font-bold">${rd.low?.toFixed(2)}</span>
                    <span className={`text-[10px] ${isSelected ? "text-slate-100 font-bold" : "text-slate-400"}`}>
                      ({rd.diff_pct > 0 ? "+" : ""}{rd.diff_pct}%)
                    </span>
                    {rd.is_pwl && (
                      <span className={`px-1 py-0.2 rounded text-[9px] font-extrabold uppercase ${isSelected ? "bg-white text-black" : "bg-[#38bdf8]/30 text-[#38bdf8]"}`}>
                        PWL
                      </span>
                    )}
                  </button>
                );
              })}
            </div>
          </div>

          <div className="flex items-center gap-3 text-[11px]">
            <label className="flex items-center gap-1.5 cursor-pointer text-slate-300 hover:text-white select-none">
              <input
                type="checkbox"
                checked={showAllRedDayLines}
                onChange={(e) => setShowAllRedDayLines(e.target.checked)}
                className="rounded border-[#1a2d3d] bg-black text-[#38bdf8] focus:ring-0 w-3.5 h-3.5"
              />
              <span>Show all on chart</span>
            </label>
            {(selectedRedDay || entryMode !== "trigger") && (
              <button
                type="button"
                onClick={() => {
                  setSelectedRedDay(null);
                  setEntryMode("trigger");
                }}
                className="text-[#6b7099] hover:text-[#00e5a0] underline"
              >
                Reset to Trigger
              </button>
            )}
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
              {showRetestLine && (
                <>
                  {levels.pw_latest_low != null && (
                    <>
                      <span className="text-[#6b7099]">·</span>
                      <span className="flex items-center gap-1.5 text-[#f5c842]">
                        <span className="w-2.5 h-0.5 bg-[#f5c842] rounded border-b border-dashed"></span>
                        <span>Latest Low ({levels.pw_latest_date ?? "Prior Wk"}{levels.pw_latest_day ? ` ${levels.pw_latest_day}` : ""}): ${levels.pw_latest_low?.toFixed(2)}</span>
                      </span>
                    </>
                  )}
                  {levels.pw_avg_low != null && (
                    <>
                      <span className="text-[#6b7099]">·</span>
                      <span className="flex items-center gap-1.5 text-[#a855f7]">
                        <span className="w-2.5 h-0.5 bg-[#a855f7] rounded border-b border-dashed"></span>
                        <span>AVG Low: ${levels.pw_avg_low?.toFixed(2)}</span>
                      </span>
                    </>
                  )}
                  {levels.retest_entry != null && (
                    <>
                      <span className="text-[#6b7099]">·</span>
                      <span className="flex items-center gap-1.5 text-[#38bdf8]">
                        <span className="w-2.5 h-0.5 bg-[#38bdf8] rounded border-b border-dotted"></span>
                        <span>{selectedRedDay ? (selectedRedDay.is_pwl ? "PWL" : `Red Low (${selectedRedDay.date_label})`) : "PWL"}: ${(selectedRedDay?.low ?? levels.retest_entry)?.toFixed(2)}</span>
                      </span>
                    </>
                  )}
                </>
              )}
              {showSma && (
                <>
                  <span className="text-[#6b7099]">·</span>
                  <span className="flex items-center gap-1.5 text-[#60a5fa]">
                    <span className="w-2.5 h-0.5 bg-[#60a5fa] rounded"></span>
                    <span>20 SMA: <span ref={sma20ValRef}>{latestSma20 != null ? `$${latestSma20.toFixed(2)}` : "—"}</span></span>
                  </span>
                  <span className="flex items-center gap-1.5 text-[#f59e0b]">
                    <span className="w-2.5 h-0.5 bg-[#f59e0b] rounded"></span>
                    <span>50 SMA: <span ref={sma50ValRef}>{latestSma50 != null ? `$${latestSma50.toFixed(2)}` : "—"}</span></span>
                  </span>
                  <span className="flex items-center gap-1.5 text-[#a855f7]">
                    <span className="w-2.5 h-0.5 bg-[#a855f7] rounded"></span>
                    <span>200 SMA: <span ref={sma200ValRef}>{latestSma200 != null ? `$${latestSma200.toFixed(2)}` : "—"}</span></span>
                  </span>
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
            {entryMode === "latest_low" ? (
              <b className="text-[#f5c842] block mb-1">
                1. Last Week Latest Low Entry ({levels.pw_latest_date ?? "Prior Wk"}{levels.pw_latest_day ? ` ${levels.pw_latest_day}` : ""})
              </b>
            ) : entryMode === "avg_low" ? (
              <b className="text-[#a855f7] block mb-1">
                1. Balanced Average Low Entry (PWL + Latest Midpoint)
              </b>
            ) : entryMode === "retest" ? (
              <b className="text-[#38bdf8] block mb-1">
                1. {selectedRedDay ? `${selectedRedDay.date_label} Red Day Low` : "Prev Week Low (PWL)"} Entry
              </b>
            ) : (
              <b className="text-[#00e5a0] block mb-1">
                1. Breakout Trigger Entry
              </b>
            )}
            {entryMode === "latest_low" && activeRetestEntry != null ? (
              <>
                Buy shallow pullback near Last Week Latest Low ({levels.pw_latest_date ?? "Prior Wk"}{levels.pw_latest_day ? ` ${levels.pw_latest_day}` : ""}) <span className="font-mono text-[#f5c842] font-bold">${activeRetestEntry?.toFixed(2)}</span> (Zone: <span className="font-mono text-white">${activeRetestZoneMin?.toFixed(2)} – ${activeRetestZoneMax?.toFixed(2)}</span>).
                Provides earlier retest support entry with tighter risk (-{activeRetestRiskPct?.toFixed(1)}%) vs breakout trigger.
              </>
            ) : entryMode === "avg_low" && activeRetestEntry != null ? (
              <>
                Buy balanced pullback near Average of Lows <span className="font-mono text-[#a855f7] font-bold">${activeRetestEntry?.toFixed(2)}</span> (Zone: <span className="font-mono text-white">${activeRetestZoneMin?.toFixed(2)} – ${activeRetestZoneMax?.toFixed(2)}</span>).
                Provides optimal midpoint between aggressive shallow entry and deep PWL with tighter risk (-{activeRetestRiskPct?.toFixed(1)}%).
              </>
            ) : entryMode === "retest" && activeRetestEntry != null ? (
              <>
                Buy retest dip near {selectedRedDay ? `${selectedRedDay.date_label} (${selectedRedDay.day_name}) Red Day Low` : "PWL"} <span className="font-mono text-[#38bdf8] font-bold">${activeRetestEntry?.toFixed(2)}</span> (Zone: <span className="font-mono text-white">${activeRetestZoneMin?.toFixed(2)} – ${activeRetestZoneMax?.toFixed(2)}</span>).
                Provides tighter risk (-{activeRetestRiskPct?.toFixed(1)}%) vs breakout trigger.
              </>
            ) : (
              <>
                Buy in the zone <span className="font-mono text-white">${levels.entry_zone_min?.toFixed(2)} – ${levels.entry_zone_max?.toFixed(2)}</span>.
                Enter on intraday pullbacks or market open if holding above moving averages.
              </>
            )}

            {/* Last Breakout Information */}
            <div className="mt-3 pt-2.5 border-t border-[#1a1d2e] flex flex-col gap-1.5 text-[11px] font-mono">
              <div className="flex items-center justify-between text-[#8b949e]">
                <span className="flex items-center gap-1 font-semibold text-[#8e95bf]">
                  <span>⚡</span> Last Breakout:
                </span>
                {levels.last_breakout_date ? (
                  <span className={`font-bold px-1.5 py-0.5 rounded text-[10px] ${
                    levels.last_breakout_days_ago === 0
                      ? "bg-[#00e5a0]/15 text-[#00e5a0] border border-[#00e5a0]/30"
                      : levels.last_breakout_days_ago <= 3
                      ? "bg-[#38bdf8]/15 text-[#38bdf8] border border-[#38bdf8]/30"
                      : "bg-[#1f2438] text-slate-300 border border-border/40"
                  }`}>
                    {levels.last_breakout_days_ago === 0
                      ? `Today (${levels.last_breakout_date_label || levels.last_breakout_date})`
                      : levels.last_breakout_days_ago === 1
                      ? `Yesterday (${levels.last_breakout_date_label || levels.last_breakout_date})`
                      : `${levels.last_breakout_days_ago} sessions ago (${levels.last_breakout_date_label || levels.last_breakout_date})`}
                  </span>
                ) : (
                  <span className="text-[#6b7099] italic">Consolidating below resistance</span>
                )}
              </div>
              {levels.last_breakout_date && (
                <div className="text-[10px] text-[#8b949e] flex flex-wrap items-center justify-between gap-1">
                  <span>
                    Pivot: <b className="text-white">${levels.last_breakout_level?.toFixed(2)}</b>{" "}
                    <span className="text-[#6b7099]">({levels.last_breakout_type || "20D High"})</span>
                  </span>
                  <span>
                    Gain: <b className="text-[#00e5a0]">+{levels.last_breakout_gain_pct}%</b>
                    {levels.last_breakout_vol_ratio != null && (
                      <> · <b className="text-[#f5c842]">{levels.last_breakout_vol_ratio}x Vol</b></>
                    )}
                  </span>
                </div>
              )}
            </div>
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
