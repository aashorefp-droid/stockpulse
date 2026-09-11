"use client";
import React, { useState, useEffect, useRef, useCallback } from "react";

const API_BASE = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

interface PlanRow {
  Ticker: string;
  "Generated Time"?: string;
  "Generated At"?: string;
  Grade: string;
  "Entry Signal": string;
  "Exp WR%": string;
  Direction: string;
  Option: string;
  Verdict: string;
  Confidence: string;
  "Best Setup": string;
  Close: number;
  ATR: number;
  "Intra Stop": number;
  "Intra T1": number;
  "Intra T2": number;
  "RR(T1)": string;
  "RR(T2)": string;
  "Best RR": string;
  "Risk $": number;
  "T1 Reward $": number;
  "T2 Reward $": number;
  "ATM Strike": number;
  Aggressive: string;
  Moderate: string;
  Conservative: string;
  "0DTE Exp": string;
  "2-3DTE Exp": string;
  "Weekly Zone": string;
  "Daily Zone": string;
  "Zone Conclusion": string;
  "CPR TC": string;
  "CPR P": string;
  "CPR BC": string;
  "CPR Type": string;
  "CPR Width%": string;
  "CPR Position": string;
  "CPR Interpretation": string;
  "10m Bias": string;
  "30m Bias": string;
  "4H Bias": string;
  "Bias Align": string;
  "Options Strategy": string;
  "If opens near entry": string;
  "If opens between entry & stop": string;
  "If opens past stop": string;
  "If big gap": string;
}

interface CheckOpenRow {
  Ticker: string;
  "Generated Time"?: string;
  "Generated At"?: string;
  Direction: string;
  Option: string;
  "Prev Close": number;
  Open: number;
  Current: number;
  "Move $": number;
  "Move %": number;
  "Live Status"?: string;
  "Live Move $"?: number;
  "Live Move %"?: number;
  "Last Updated"?: string;
  "Locked At"?: string;
  "Scenario ID": string;
  Scenario: string;
  "Scenario Color": string;
  "Is Flipped": boolean;
  Stop: number;
  T1: number;
  T2: number;
  ATM: number;
  "Active RR": string;
  "Action Notes": string;
  "Exp 0DTE": string;
  "Exp 2-3DTE": string;
  "Weekly Zone": string;
  "Daily Zone": string;
  "Zone Conclusion": string;
}

const SCENARIO_GROUPS = [
  {
    id: "past_stop",
    title: "❌ OPENS PAST STOP",
    shortLabel: "Past Stop (Reversal)",
    icon: "❌",
    subtitle: "Invalidated original setup. Direction flipped to reverse (Call ↔ Put) with new stops and targets.",
    alertBg: "bg-rose-950/30 border-rose-500/40",
    badgeBg: "bg-rose-500/20 text-rose-400",
    headerColor: "text-rose-400",
  },
  {
    id: "near_entry",
    title: "✅ OPENS NEAR ENTRY",
    shortLabel: "Near Entry (Priority)",
    icon: "✅",
    subtitle: "Ideal execution scenario. Follow original trade direction with standard brackets.",
    alertBg: "bg-emerald-950/30 border-emerald-500/40",
    badgeBg: "bg-emerald-500/20 text-emerald-400",
    headerColor: "text-emerald-400",
  },
  {
    id: "between",
    title: "⚡ OPENS BETWEEN ENTRY & STOP",
    shortLabel: "Between Entry & Stop",
    icon: "⚡",
    subtitle: "Pullback scenario. Tighter stop needed or wait for confirmation back towards entry.",
    alertBg: "bg-blue-950/30 border-blue-500/40",
    badgeBg: "bg-blue-500/20 text-blue-400",
    headerColor: "text-blue-400",
  },
  {
    id: "big_gap",
    title: "⚠️ BIG GAP",
    shortLabel: "Big Gap",
    icon: "⚠️",
    subtitle: "Excessive gap beyond 0.5% buffer. Caution on chasing at market open.",
    alertBg: "bg-amber-950/30 border-amber-500/40",
    badgeBg: "bg-amber-500/20 text-amber-300",
    headerColor: "text-amber-300",
  },
];

const DEFAULT_TICKERS = "SPY, QQQ, AAPL, NVDA, TSLA, MSFT, AMZN, META, AMD, GOOGL";

function getCSTDate(): Date {
  const now = new Date();
  const utc = now.getTime() + now.getTimezoneOffset() * 60000;
  return new Date(utc - 5 * 3600000);
}

export default function PlanPage() {
  const [tickersRaw, setTickersRaw] = useState(DEFAULT_TICKERS);
  const [planDate, setPlanDate] = useState(() => new Date().toISOString().split("T")[0]);
  const [loading, setLoading] = useState(false);
  const [checkLoading, setCheckLoading] = useState(false);
  const [selectedScenarioTab, setSelectedScenarioTab] = useState<string>("all");
  const [paperMessage, setPaperMessage] = useState<string | null>(null);

  // Paper Trading Tracking state
  const [paperTickers, setPaperTickers] = useState<Set<string>>(new Set());

  // Scheduler & Auto-Refresh state
  const [autoRefreshEnabled, setAutoRefreshEnabled] = useState(true);
  const [cstTimeStr, setCstTimeStr] = useState<string>("");
  const [countdown, setCountdown] = useState<number>(300);
  const [lastRefreshed, setLastRefreshed] = useState<string>("—");
  const [milestonesDone, setMilestonesDone] = useState<{
    open800: boolean;
    plan815: boolean;
    open820: boolean;
    open830: boolean;
  }>({
    open800: false,
    plan815: false,
    open820: false,
    open830: false,
  });

  // Lock States
  const [isLocked830, setIsLocked830] = useState<boolean>(false);
  const [lockedAt830, setLockedAt830] = useState<string | null>(null);
  const [isLocked820, setIsLocked820] = useState<boolean>(false);
  const [lockedAt820, setLockedAt820] = useState<string | null>(null);
  const [locked820Data, setLocked820Data] = useState<any>(null);
  const [refreshingLocked, setRefreshingLocked] = useState<boolean>(false);
  const [activeViewMode, setActiveViewMode] = useState<"830_locked" | "820_locked" | "plan">("830_locked");

  const [planData, setPlanData] = useState<{
    plan_date: string;
    plan_for: string;
    plan_label: string;
    generated_at?: string;
    generated_time?: string;
    summary: { total: number; long: number; short: number; best: number };
    rows: PlanRow[];
  } | null>(null);

  const [openCheckData, setOpenCheckData] = useState<{
    check_date: string;
    checked_at?: string;
    checked_time?: string;
    successful: number;
    failed: number;
    counts?: { past_stop: number; near_entry: number; between: number; big_gap: number };
    grouped?: Record<string, CheckOpenRow[]>;
    rows: CheckOpenRow[];
  } | null>(null);

  // Position Calculator State
  const [showCalculator, setShowCalculator] = useState(false);
  const [calcAccountSize, setCalcAccountSize] = useState(25000);
  const [calcRiskPct, setCalcRiskPct] = useState(1.0);
  const [calcEntry, setCalcEntry] = useState(150.0);
  const [calcStop, setCalcStop] = useState(145.0);
  const [calcT1, setCalcT1] = useState(160.0);
  const [calcT2, setCalcT2] = useState(170.0);
  const [calcResult, setCalcResult] = useState<any>(null);

  const planDataRef = useRef(planData);
  planDataRef.current = planData;

  const openCheckDataRef = useRef(openCheckData);
  openCheckDataRef.current = openCheckData;

  const isLocked830Ref = useRef(isLocked830);
  isLocked830Ref.current = isLocked830;

  // Query open paper positions
  const fetchPaperPositions = useCallback(async () => {
    try {
      const res = await fetch(`${API_BASE}/api/paper/positions`);
      if (res.ok) {
        const data = await res.json();
        const tickers = new Set<string>((data.items || []).map((t: any) => t.ticker.toUpperCase()));
        setPaperTickers(tickers);
      }
    } catch (e) {
      console.error("Error fetching paper positions:", e);
    }
  }, []);

  useEffect(() => {
    fetchPaperPositions();
  }, [fetchPaperPositions]);

  // Push individual ticker to paper trading
  const handlePushToPaper = async (row: CheckOpenRow) => {
    try {
      const res = await fetch(`${API_BASE}/api/paper/trade`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          ticker: row.Ticker,
          direction: row.Direction,
          entry_price: row.Open || row.Current || row["Prev Close"],
          stop_price: row.Stop,
          t1_price: row.T1,
          t2_price: row.T2,
          scenario: row.Scenario,
          confidence: "HIGH",
          trade_date: planDate,
        }),
      });
      if (res.ok) {
        setPaperTickers((prev) => new Set(prev).add(row.Ticker.toUpperCase()));
        setPaperMessage(`✅ ${row.Ticker} pushed to Paper Trading! (Stop $${row.Stop} · T1 $${row.T1})`);
        setTimeout(() => setPaperMessage(null), 5000);
      } else {
        const err = await res.json();
        setPaperMessage(`⚠️ Could not push ${row.Ticker}: ${err.detail || "Error"}`);
        setTimeout(() => setPaperMessage(null), 5000);
      }
    } catch (err: any) {
      setPaperMessage(`⚠️ Error pushing ${row.Ticker}: ${err.message}`);
      setTimeout(() => setPaperMessage(null), 5000);
    }
  };

  // Push all eligible tickers
  const handlePushAllEligible = async () => {
    const activeData = activeViewMode === "820_locked" && locked820Data ? locked820Data : openCheckData;
    if (!activeData || !activeData.rows.length) return;
    const eligible = activeData.rows.filter(
      (r: CheckOpenRow) =>
        (r["Scenario ID"] === "near_entry" || r["Scenario ID"] === "past_stop") &&
        !paperTickers.has(r.Ticker.toUpperCase())
    );

    if (!eligible.length) {
      setPaperMessage("ℹ️ No new eligible tickers to push to Paper Trading.");
      setTimeout(() => setPaperMessage(null), 4000);
      return;
    }

    let count = 0;
    for (const r of eligible) {
      try {
        const res = await fetch(`${API_BASE}/api/paper/trade`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            ticker: r.Ticker,
            direction: r.Direction,
            entry_price: r.Open || r.Current || r["Prev Close"],
            stop_price: r.Stop,
            t1_price: r.T1,
            t2_price: r.T2,
            scenario: r.Scenario,
            confidence: "HIGH",
            trade_date: planDate,
          }),
        });
        if (res.ok) {
          count++;
          setPaperTickers((prev) => new Set(prev).add(r.Ticker.toUpperCase()));
        }
      } catch (err) {
        console.error(`Error pushing ${r.Ticker}:`, err);
      }
    }
    setPaperMessage(`✅ Successfully pushed ${count} eligible ticker(s) to Paper Trading!`);
    setTimeout(() => setPaperMessage(null), 5000);
  };

  // Generate Plan (Pre-market)
  const handleGeneratePlan = useCallback(async (isSilent = false) => {
    if (!isSilent) setLoading(true);
    try {
      const tickerList = tickersRaw
        .split(",")
        .map((t) => t.trim().toUpperCase())
        .filter((t) => t.length > 0);
      const res = await fetch(`${API_BASE}/api/plan/generate`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ tickers: tickerList, plan_date: planDate }),
      });
      const data = await res.json();
      const cst = getCSTDate();
      const fallbackTime = cst.toLocaleTimeString("en-US", { hour: "2-digit", minute: "2-digit", second: "2-digit" });
      if (data && data.rows) {
        const genTime = data.generated_time || fallbackTime;
        data.rows = data.rows.map((r: any) => ({
          ...r,
          "Generated Time": r["Generated Time"] || genTime,
        }));
      }
      setPlanData(data);
      setLastRefreshed(fallbackTime + " CST");
      return data;
    } catch (err) {
      console.error("Error generating plan:", err);
      return null;
    } finally {
      if (!isSilent) setLoading(false);
    }
  }, [tickersRaw, planDate]);

  // Check Open Prices
  const handleCheckOpen = useCallback(async (isSilent = false) => {
    let currentRows = planDataRef.current?.rows || [];
    if (!currentRows.length) {
      const genData = await handleGeneratePlan(isSilent);
      currentRows = genData?.rows || [];
    }

    if (!currentRows.length) return null;

    if (!isSilent) setCheckLoading(true);
    try {
      const res = await fetch(`${API_BASE}/api/plan/check-open`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ plan_rows: currentRows, check_date: planDate }),
      });
      const data = await res.json();
      const cst = getCSTDate();
      const fallbackTime = cst.toLocaleTimeString("en-US", { hour: "2-digit", minute: "2-digit", second: "2-digit" });
      if (data && data.rows) {
        const genTime = data.checked_time || fallbackTime;
        data.rows = data.rows.map((r: any) => ({
          ...r,
          "Generated Time": r["Generated Time"] || genTime,
          "Live Status": r["Live Status"] || "⚡ IN PLAY",
        }));
      }
      setOpenCheckData(data);
      setLastRefreshed(fallbackTime + " CST");
      fetchPaperPositions();
      return data;
    } catch (err) {
      console.error("Error checking open prices:", err);
      return null;
    } finally {
      if (!isSilent) setCheckLoading(false);
    }
  }, [handleGeneratePlan, planDate, fetchPaperPositions]);

  // 🔒 Lock 8:30 AM Market Open Snapshot
  const handleLock830 = useCallback((overrideData?: any) => {
    const dataToLock = overrideData || openCheckDataRef.current;
    if (!dataToLock || !dataToLock.rows || !dataToLock.rows.length) return;

    const cst = getCSTDate();
    const timeStr = cst.toLocaleTimeString("en-US", { hour: "2-digit", minute: "2-digit", second: "2-digit" }) + " CST";

    const lockedRows = dataToLock.rows.map((r: CheckOpenRow) => ({
      ...r,
      "Locked At": r["Locked At"] || timeStr,
      "Live Status": r["Live Status"] || "⚡ IN PLAY",
    }));

    const grouped = {
      past_stop: lockedRows.filter((r: any) => r["Scenario ID"] === "past_stop"),
      near_entry: lockedRows.filter((r: any) => r["Scenario ID"] === "near_entry"),
      between: lockedRows.filter((r: any) => r["Scenario ID"] === "between"),
      big_gap: lockedRows.filter((r: any) => r["Scenario ID"] === "big_gap"),
    };

    setOpenCheckData({
      ...dataToLock,
      rows: lockedRows,
      grouped,
    });
    setIsLocked830(true);
    setLockedAt830(timeStr);
    setActiveViewMode("830_locked");
    setPaperMessage("🔒 8:30 AM Market Open Locked! Tickers are anchored into their scenario tables (Past Stop, Near Entry, etc.). Live prices will refresh dynamically.");
    setTimeout(() => setPaperMessage(null), 6000);
  }, []);

  // ⚡ Lock 8:20 AM Pre-Market Snapshot
  const handleLock820 = useCallback((overrideData?: any) => {
    const dataToLock = overrideData || openCheckDataRef.current;
    if (!dataToLock || !dataToLock.rows || !dataToLock.rows.length) return;

    const cst = getCSTDate();
    const timeStr = cst.toLocaleTimeString("en-US", { hour: "2-digit", minute: "2-digit", second: "2-digit" }) + " CST";

    setIsLocked820(true);
    setLockedAt820(timeStr);
    setLocked820Data({
      ...dataToLock,
      locked_at: timeStr,
    });
    setPaperMessage("⚡ 8:20 AM Pre-Market Snapshot Locked!");
    setTimeout(() => setPaperMessage(null), 5000);
  }, []);

  // 🔓 Unlock 8:30 AM Playbook
  const handleUnlock830 = () => {
    setIsLocked830(false);
    setLockedAt830(null);
    setPaperMessage("🔓 8:30 AM Lock released. Scenario distribution will re-evaluate on next check.");
    setTimeout(() => setPaperMessage(null), 4000);
  };

  // 🔄 Refresh live quotes for existing locked rows without altering table grouping
  const handleRefreshLockedPrices = useCallback(async () => {
    const currentData = openCheckDataRef.current;
    if (!currentData || !currentData.rows || !currentData.rows.length) return;
    setRefreshingLocked(true);
    try {
      const res = await fetch(`${API_BASE}/api/plan/refresh-locked`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ locked_rows: currentData.rows }),
      });
      if (res.ok) {
        const data = await res.json();
        setOpenCheckData((prev: any) => ({
          ...prev,
          rows: data.rows,
          grouped: data.grouped,
          counts: data.counts,
          checked_time: data.checked_time,
        }));
        const cst = getCSTDate();
        setLastRefreshed(cst.toLocaleTimeString("en-US", { hour: "2-digit", minute: "2-digit", second: "2-digit" }) + " CST");
      }
    } catch (err) {
      console.error("Error refreshing locked prices:", err);
    } finally {
      setRefreshingLocked(false);
    }
  }, []);

  // Clock & Scheduler effect
  useEffect(() => {
    const updateTime = () => {
      const cst = getCSTDate();
      const timeFormatted = cst.toLocaleTimeString("en-US", {
        hour: "2-digit",
        minute: "2-digit",
        second: "2-digit",
      });
      setCstTimeStr(`${timeFormatted} CST`);

      const h = cst.getHours();
      const m = cst.getMinutes();
      const s = cst.getSeconds();
      const weekday = cst.getDay();
      const isWeekday = weekday >= 1 && weekday <= 5;

      const todayStr = new Date().toISOString().split("T")[0];
      if (isWeekday && planDate === todayStr) {
        if (h === 8 && m === 0 && s <= 2 && !milestonesDone.open800) {
          setMilestonesDone((prev) => ({ ...prev, open800: true }));
          handleCheckOpen(true);
        }
        if (h === 8 && m === 15 && s <= 2 && !milestonesDone.plan815) {
          setMilestonesDone((prev) => ({ ...prev, plan815: true }));
          handleGeneratePlan(true);
        }
        if (h === 8 && m === 20 && s <= 2 && !milestonesDone.open820) {
          setMilestonesDone((prev) => ({ ...prev, open820: true }));
          handleCheckOpen(true).then((data) => {
            if (data) handleLock820(data);
          });
        }
        if (h === 8 && m === 30 && s <= 2 && !milestonesDone.open830) {
          setMilestonesDone((prev) => ({ ...prev, open830: true }));
          handleCheckOpen(true).then((data) => {
            if (data) handleLock830(data);
          });
        }
      }
    };

    updateTime();
    const clockInterval = setInterval(updateTime, 1000);
    return () => clearInterval(clockInterval);
  }, [planDate, milestonesDone, handleCheckOpen, handleGeneratePlan, handleLock820, handleLock830]);

  // 5-minute recurring refresh countdown
  useEffect(() => {
    if (!autoRefreshEnabled) return;
    const interval = setInterval(() => {
      setCountdown((prev) => {
        if (prev <= 1) {
          if (isLocked830Ref.current) {
            handleRefreshLockedPrices();
          } else {
            handleCheckOpen(true);
          }
          return 300;
        }
        return prev - 1;
      });
    }, 1000);
    return () => clearInterval(interval);
  }, [autoRefreshEnabled, handleRefreshLockedPrices, handleCheckOpen]);

  // Position sizing calculation
  const calculatePosition = async () => {
    try {
      const res = await fetch(`${API_BASE}/api/plan/calculate`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          account_size: calcAccountSize,
          risk_pct: calcRiskPct,
          entry_price: calcEntry,
          stop_loss: calcStop,
          target1: calcT1,
          target2: calcT2 || null,
        }),
      });
      const data = await res.json();
      setCalcResult(data);
    } catch (err) {
      console.error("Error calculating position:", err);
    }
  };

  // Download CSV export
  const downloadCSV = () => {
    if (!planData || !planData.rows.length) return;
    const headers = Object.keys(planData.rows[0]).filter((k) => !k.startsWith("_"));
    const csvContent =
      "data:text/csv;charset=utf-8," +
      [
        headers.join(","),
        ...planData.rows.map((row: any) =>
          headers
            .map((h) => {
              const val = row[h];
              if (typeof val === "string" && val.includes(",")) {
                return `"${val.replace(/"/g, '""')}"`;
              }
              return val;
            })
            .join(",")
        ),
      ].join("\n");

    const encodedUri = encodeURI(csvContent);
    const link = document.createElement("a");
    link.setAttribute("href", encodedUri);
    link.setAttribute("download", `intraday_plan_${planData.plan_for}.csv`);
    document.body.appendChild(link);
    link.click();
    document.body.removeChild(link);
  };

  const countdownFormatted = `${Math.floor(countdown / 60)}:${(countdown % 60).toString().padStart(2, "0")}`;

  // Tickers actively shown in scenario analysis
  const currentScenarioData = activeViewMode === "820_locked" && locked820Data ? locked820Data : openCheckData;

  return (
    <div className="max-w-7xl mx-auto px-4 py-8 space-y-6">
      {/* Toast Notification */}
      {paperMessage && (
        <div className="bg-emerald-950/90 border border-emerald-500/50 text-emerald-200 text-xs px-4 py-3 rounded-lg shadow-xl flex items-center justify-between animate-in fade-in duration-200 sticky top-4 z-50">
          <span className="font-mono">{paperMessage}</span>
          <button onClick={() => setPaperMessage(null)} className="text-emerald-400 hover:text-white ml-4 font-bold">
            ✕
          </button>
        </div>
      )}

      {/* Top Banner with Real-Time Scheduler Status */}
      <div className="bg-gradient-to-br from-[#0a0b14] to-[#131625] border border-[#1a1d2e] rounded-xl p-6 space-y-4">
        <div className="flex flex-wrap items-start justify-between gap-4">
          <div className="flex items-start gap-4">
            <div className="text-3xl">🗓️</div>
            <div>
              <h1 className="text-xl font-bold text-white tracking-wide">Intraday Planning</h1>
              <p className="text-sm text-muted mt-1 leading-relaxed">
                Run after market close — generates a trade plan for tomorrow based on today's signals. Shows direction,
                entry, stop, targets, options strikes, CPR, and execution scenarios.
                <br />
                Eligible trades can be pushed straight into <a href="/paper" className="text-accent underline hover:text-accent/80">📄 Paper Trading</a> with automatic bracket stops & targets.
              </p>
            </div>
          </div>
          {/* Real-Time CST Clock & Refresh Indicator */}
          <div className="flex flex-col items-end gap-1 font-mono text-xs">
            <div className="flex items-center gap-2 bg-[#121629] border border-[#1e2540] px-3 py-1.5 rounded-lg text-white">
              <span className="w-2 h-2 rounded-full bg-emerald-400 animate-pulse"></span>
              <span className="font-bold">{cstTimeStr || "CST"}</span>
            </div>
            <span className="text-[11px] text-muted">
              Last refresh: <strong className="text-white">{lastRefreshed}</strong>
            </span>
          </div>
        </div>

        {/* Schedule Milestones & Lock Status Bar */}
        <div className="bg-[#0b0e1a] border border-[#1b2238] rounded-lg p-3 flex flex-wrap items-center justify-between gap-3 text-xs">
          <div className="flex flex-wrap items-center gap-2">
            <span className="text-muted font-bold flex items-center gap-1.5 mr-1">
              <span>⏰</span> Milestones:
            </span>
            <span
              className={`px-2 py-0.5 rounded font-mono text-[11px] border ${
                milestonesDone.open800
                  ? "bg-emerald-500/20 text-emerald-400 border-emerald-500/30"
                  : "bg-[#141828] text-muted border-[#202740]"
              }`}
            >
              ☀️ 8:00 AM Open Check
            </span>
            <span
              className={`px-2 py-0.5 rounded font-mono text-[11px] border ${
                milestonesDone.plan815
                  ? "bg-emerald-500/20 text-emerald-400 border-emerald-500/30"
                  : "bg-[#141828] text-muted border-[#202740]"
              }`}
            >
              🗓️ 8:15 AM Auto-Plan
            </span>
            <span
              className={`px-2 py-0.5 rounded font-mono text-[11px] border flex items-center gap-1 ${
                isLocked820
                  ? "bg-emerald-500/20 text-emerald-400 border-emerald-500/40 font-bold"
                  : milestonesDone.open820
                  ? "bg-blue-500/20 text-blue-400 border-blue-500/30"
                  : "bg-[#141828] text-muted border-[#202740]"
              }`}
            >
              <span>{isLocked820 ? "🔒" : "⚡"}</span>
              <span>8:20 AM Pre-Market {isLocked820 ? `(Locked ${lockedAt820})` : ""}</span>
            </span>
            <span
              className={`px-2 py-0.5 rounded font-mono text-[11px] border flex items-center gap-1 ${
                isLocked830
                  ? "bg-emerald-500/20 text-emerald-400 border-emerald-500/50 font-bold shadow-sm shadow-emerald-500/10"
                  : milestonesDone.open830
                  ? "bg-blue-500/20 text-blue-400 border-blue-500/30"
                  : "bg-[#141828] text-muted border-[#202740]"
              }`}
            >
              <span>{isLocked830 ? "🔒" : "🔔"}</span>
              <span>8:30 AM Market Open {isLocked830 ? `(Locked ${lockedAt830})` : ""}</span>
            </span>
          </div>

          <div className="flex items-center gap-3">
            <span className="text-muted font-mono text-[11px]">
              🔄 5-min Auto-Sync:{" "}
              <strong className={autoRefreshEnabled ? "text-emerald-400" : "text-muted"}>
                {autoRefreshEnabled ? `Next in ${countdownFormatted}` : "Paused"}
              </strong>
            </span>
            <button
              onClick={() => setAutoRefreshEnabled(!autoRefreshEnabled)}
              className={`px-2 py-1 rounded text-[11px] font-bold border transition-colors ${
                autoRefreshEnabled
                  ? "bg-emerald-500/20 text-emerald-400 border-emerald-500/30 hover:bg-emerald-500/30"
                  : "bg-[#181d30] text-muted border-[#252c48] hover:text-white"
              }`}
            >
              {autoRefreshEnabled ? "Auto-Sync ON" : "Auto-Sync OFF"}
            </button>
            <button
              onClick={() => {
                if (isLocked830) {
                  handleRefreshLockedPrices();
                } else {
                  handleCheckOpen(false);
                }
                setCountdown(300);
              }}
              disabled={refreshingLocked || checkLoading}
              className="px-2.5 py-1 rounded text-[11px] font-bold bg-[#1e2540] text-amber-400 hover:bg-[#283256] border border-amber-500/30 transition-colors flex items-center gap-1"
            >
              <span>{refreshingLocked ? "⏳" : "⚡"}</span> {refreshingLocked ? "Refreshing..." : "Refresh Now"}
            </button>
          </div>
        </div>
      </div>

      {/* Input Controls */}
      <div className="bg-[#0e1017] border border-[#1a1d2e] rounded-xl p-5 space-y-4">
        <div className="grid grid-cols-1 md:grid-cols-4 gap-4 items-end">
          <div className="md:col-span-3">
            <label className="block text-xs font-semibold text-muted uppercase tracking-wider mb-2">
              Tickers (comma-separated)
            </label>
            <input
              type="text"
              value={tickersRaw}
              onChange={(e) => setTickersRaw(e.target.value)}
              placeholder="SPY, QQQ, AAPL, NVDA, TSLA..."
              className="w-full bg-[#131722] border border-[#232738] rounded-lg px-4 py-2.5 text-sm font-mono text-white focus:outline-none focus:border-accent"
            />
          </div>
          <div>
            <label className="block text-xs font-semibold text-muted uppercase tracking-wider mb-2">
              Plan Date (Close Of)
            </label>
            <input
              type="date"
              value={planDate}
              onChange={(e) => setPlanDate(e.target.value)}
              className="w-full bg-[#131722] border border-[#232738] rounded-lg px-3 py-2.5 text-sm font-mono text-white focus:outline-none focus:border-accent"
            />
          </div>
        </div>

        <div className="flex flex-wrap items-center gap-3 pt-2 border-t border-[#1a1d2e]">
          <button
            onClick={() => handleGeneratePlan(false)}
            disabled={loading}
            className="flex-1 min-w-[180px] bg-accent text-black font-bold py-2.5 px-4 rounded-lg hover:bg-accent/90 transition-all flex items-center justify-center gap-2 shadow-lg shadow-accent/10 text-xs"
          >
            {loading ? "⏳ Generating Plan..." : "🗓️ GENERATE PLAN"}
          </button>
          <button
            onClick={() => handleCheckOpen(false)}
            disabled={checkLoading}
            className="flex-1 min-w-[180px] bg-amber-500 hover:bg-amber-400 text-black font-bold py-2.5 px-4 rounded-lg transition-all flex items-center justify-center gap-2 shadow-lg shadow-amber-500/10 text-xs"
          >
            {checkLoading ? "⏳ Checking Open..." : "☀️ CHECK OPEN PRICES"}
          </button>

          {/* 🔒 8:30 AM Lock Action Button */}
          {openCheckData && (
            <button
              onClick={() => {
                if (isLocked830) {
                  handleUnlock830();
                } else {
                  handleLock830();
                }
              }}
              className={`py-2.5 px-4 rounded-lg font-bold text-xs transition-all flex items-center gap-2 ${
                isLocked830
                  ? "bg-rose-950/40 text-rose-300 border border-rose-500/40 hover:bg-rose-900/60"
                  : "bg-emerald-600 hover:bg-emerald-500 text-white shadow-lg shadow-emerald-600/20"
              }`}
            >
              <span>{isLocked830 ? "🔓" : "🔒"}</span>
              {isLocked830 ? "Unlock 8:30 AM Playbook" : "🔒 LOCK 8:30 AM OPEN"}
            </button>
          )}

          {/* ⚡ 8:20 AM Lock Action Button */}
          {openCheckData && (
            <button
              onClick={() => handleLock820()}
              className={`py-2.5 px-4 rounded-lg font-bold text-xs transition-all flex items-center gap-1.5 border ${
                isLocked820
                  ? "bg-blue-950/40 text-blue-300 border-blue-500/40"
                  : "bg-[#141828] text-muted hover:text-white border-[#202740]"
              }`}
            >
              <span>⚡</span> {isLocked820 ? "8:20 AM Locked ✓" : "Lock 8:20 AM Snapshot"}
            </button>
          )}

          {/* 🔄 Live Refresh for Locked Tables */}
          {isLocked830 && (
            <button
              onClick={handleRefreshLockedPrices}
              disabled={refreshingLocked}
              className="py-2.5 px-4 rounded-lg font-bold text-xs bg-[#151c33] hover:bg-[#1f2a4d] text-emerald-400 border border-emerald-500/30 transition-all flex items-center gap-1.5"
            >
              <span className={refreshingLocked ? "animate-spin" : ""}>🔄</span>
              {refreshingLocked ? "Refreshing Quotes..." : "Refresh Live Prices"}
            </button>
          )}

          <button
            onClick={() => setShowCalculator(!showCalculator)}
            className="bg-[#1a1f33] hover:bg-[#232a45] text-[#93a4db] font-semibold py-2.5 px-3 rounded-lg border border-[#2a3254] transition-all flex items-center gap-1.5 text-xs"
          >
            🧮 {showCalculator ? "Hide Calculator" : "Position Sizing"}
          </button>
          {planData && planData.rows.length > 0 && (
            <button
              onClick={downloadCSV}
              className="bg-[#121624] hover:bg-[#1a2034] text-emerald-400 font-semibold py-2.5 px-3 rounded-lg border border-emerald-500/30 transition-all flex items-center gap-1.5 text-xs"
            >
              📥 CSV
            </button>
          )}
        </div>
      </div>

      {/* Position Sizing Calculator */}
      {showCalculator && (
        <div className="bg-[#0b0d14] border border-[#232a45] rounded-xl p-5 space-y-4 animate-in fade-in duration-200">
          <div className="flex justify-between items-center border-b border-[#1c2238] pb-3">
            <h3 className="text-sm font-bold text-white flex items-center gap-2">
              <span>🧮</span> Account Risk & Position Sizing Calculator
            </h3>
            <span className="text-xs text-muted font-mono">Calculates shares based on max equity risk</span>
          </div>

          <div className="grid grid-cols-2 sm:grid-cols-3 md:grid-cols-6 gap-3">
            <div>
              <label className="text-[11px] text-muted block mb-1">Account Equity ($)</label>
              <input
                type="number"
                value={calcAccountSize}
                onChange={(e) => setCalcAccountSize(parseFloat(e.target.value) || 0)}
                className="w-full bg-[#131722] border border-[#232738] rounded px-2.5 py-1.5 text-xs text-white font-mono"
              />
            </div>
            <div>
              <label className="text-[11px] text-muted block mb-1">Risk %</label>
              <input
                type="number"
                step="0.1"
                value={calcRiskPct}
                onChange={(e) => setCalcRiskPct(parseFloat(e.target.value) || 0)}
                className="w-full bg-[#131722] border border-[#232738] rounded px-2.5 py-1.5 text-xs text-white font-mono"
              />
            </div>
            <div>
              <label className="text-[11px] text-muted block mb-1">Entry ($)</label>
              <input
                type="number"
                step="0.01"
                value={calcEntry}
                onChange={(e) => setCalcEntry(parseFloat(e.target.value) || 0)}
                className="w-full bg-[#131722] border border-[#232738] rounded px-2.5 py-1.5 text-xs text-white font-mono"
              />
            </div>
            <div>
              <label className="text-[11px] text-muted block mb-1">Stop Loss ($)</label>
              <input
                type="number"
                step="0.01"
                value={calcStop}
                onChange={(e) => setCalcStop(parseFloat(e.target.value) || 0)}
                className="w-full bg-[#131722] border border-[#232738] rounded px-2.5 py-1.5 text-xs text-rose-400 font-mono"
              />
            </div>
            <div>
              <label className="text-[11px] text-muted block mb-1">Target 1 ($)</label>
              <input
                type="number"
                step="0.01"
                value={calcT1}
                onChange={(e) => setCalcT1(parseFloat(e.target.value) || 0)}
                className="w-full bg-[#131722] border border-[#232738] rounded px-2.5 py-1.5 text-xs text-emerald-400 font-mono"
              />
            </div>
            <div>
              <label className="text-[11px] text-muted block mb-1">Target 2 ($)</label>
              <input
                type="number"
                step="0.01"
                value={calcT2}
                onChange={(e) => setCalcT2(parseFloat(e.target.value) || 0)}
                className="w-full bg-[#131722] border border-[#232738] rounded px-2.5 py-1.5 text-xs text-emerald-400 font-mono"
              />
            </div>
          </div>

          <div className="flex items-center gap-4 pt-2">
            <button
              onClick={calculatePosition}
              className="bg-accent/90 hover:bg-accent text-black font-semibold text-xs py-2 px-4 rounded transition-colors"
            >
              Calculate Shares
            </button>
            {calcResult && (
              <div className="flex flex-wrap items-center gap-4 text-xs font-mono">
                <span className="text-white">
                  Shares: <strong className="text-accent">{calcResult.shares}</strong>
                </span>
                <span className="text-white">
                  Cost: <strong>${calcResult.total_cost}</strong>
                </span>
                <span className="text-rose-400">
                  Risk: <strong>${calcResult.risk_dollars}</strong>
                </span>
                <span className="text-emerald-400">
                  T1 R:R: <strong>{calcResult.rr_t1}:1</strong> (+${calcResult.profit_t1})
                </span>
                {calcResult.rr_t2 && (
                  <span className="text-cyan-400">
                    T2 R:R: <strong>{calcResult.rr_t2}:1</strong> (+${calcResult.profit_t2})
                  </span>
                )}
              </div>
            )}
          </div>
        </div>
      )}

      {/* ── VIEW SWITCHER TABS ───────────────────────────────────────── */}
      {(openCheckData || planData) && (
        <div className="flex flex-wrap items-center gap-3 border-b border-[#1c2238] pb-3">
          <button
            onClick={() => setActiveViewMode("830_locked")}
            className={`px-4 py-2 rounded-lg text-xs font-bold transition-all flex items-center gap-2 border ${
              activeViewMode === "830_locked"
                ? "bg-emerald-500/20 text-emerald-400 border-emerald-500/40 shadow-md shadow-emerald-500/10"
                : "bg-[#101422] text-muted hover:text-white border-[#1c2238]"
            }`}
          >
            <span>{isLocked830 ? "🔒" : "🎯"}</span>
            <span>8:30 AM Playbook</span>
            {isLocked830 && (
              <span className="bg-emerald-500/30 text-emerald-300 text-[10px] px-1.5 py-0.5 rounded font-mono">
                LOCKED
              </span>
            )}
          </button>

          {isLocked820 && (
            <button
              onClick={() => setActiveViewMode("820_locked")}
              className={`px-4 py-2 rounded-lg text-xs font-bold transition-all flex items-center gap-2 border ${
                activeViewMode === "820_locked"
                  ? "bg-blue-500/20 text-blue-400 border-blue-500/40 shadow-md shadow-blue-500/10"
                  : "bg-[#101422] text-muted hover:text-white border-[#1c2238]"
              }`}
            >
              <span>⚡</span>
              <span>8:20 AM Pre-Market Lock</span>
              <span className="bg-blue-500/30 text-blue-300 text-[10px] px-1.5 py-0.5 rounded font-mono">
                FROZEN
              </span>
            </button>
          )}

          {planData && (
            <button
              onClick={() => setActiveViewMode("plan")}
              className={`px-4 py-2 rounded-lg text-xs font-bold transition-all flex items-center gap-2 border ${
                activeViewMode === "plan"
                  ? "bg-white text-black border-white shadow-md shadow-white/10"
                  : "bg-[#101422] text-muted hover:text-white border-[#1c2238]"
              }`}
            >
              <span>📋</span>
              <span>Master Daily Plan ({planData.rows.length})</span>
            </button>
          )}
        </div>
      )}

      {/* ── SCENARIO PLAYBOOK (8:30 AM LOCKED OR 8:20 AM SNAPSHOT) ────── */}
      {currentScenarioData && activeViewMode !== "plan" && (
        <div className="bg-[#0b0e17] border border-amber-500/30 rounded-xl p-5 space-y-6 animate-in fade-in duration-300">
          {/* Header */}
          <div className="flex flex-wrap justify-between items-center gap-3 border-b border-[#1f2538] pb-4">
            <div className="flex items-center gap-3">
              <span className="text-2xl">{isLocked830 ? "🔒" : "☀️"}</span>
              <div>
                <div className="flex items-center gap-2 flex-wrap">
                  <h2 className="text-lg font-bold text-white">
                    {activeViewMode === "820_locked"
                      ? "⚡ 8:20 AM Pre-Market Locked Snapshot"
                      : isLocked830
                      ? "🔒 8:30 AM Market Open Playbook (Locked Tables)"
                      : "Morning Open Scenario Analysis"}
                  </h2>
                  {isLocked830 && activeViewMode === "830_locked" && (
                    <span className="px-2.5 py-0.5 rounded text-[11px] font-mono font-bold bg-emerald-500/20 text-emerald-400 border border-emerald-500/30 flex items-center gap-1">
                      <span>🔒</span> LOCKED AT {lockedAt830}
                    </span>
                  )}
                  {activeViewMode === "820_locked" && lockedAt820 && (
                    <span className="px-2.5 py-0.5 rounded text-[11px] font-mono font-bold bg-blue-500/20 text-blue-400 border border-blue-500/30 flex items-center gap-1">
                      <span>⚡</span> CAPTURED AT {lockedAt820}
                    </span>
                  )}
                </div>
                <p className="text-xs text-muted mt-1">
                  {isLocked830 && activeViewMode === "830_locked"
                    ? "Tickers are permanently anchored to their 8:30 AM opening scenarios (OPENS PAST STOP, OPENS NEAR ENTRY, etc.). Live quotes refresh dynamically without shifting tickers across tables."
                    : "Tickers evaluated against 8:30 AM open prices and assigned into execution scenarios."}
                </p>
              </div>
            </div>

            <div className="flex items-center gap-2 flex-wrap">
              {isLocked830 && activeViewMode === "830_locked" && (
                <button
                  onClick={handleRefreshLockedPrices}
                  disabled={refreshingLocked}
                  className="bg-[#14192b] hover:bg-emerald-600 text-emerald-400 hover:text-white font-bold text-xs py-1.5 px-3 rounded-lg border border-emerald-500/30 transition-all flex items-center gap-1.5 shadow-sm"
                >
                  <span className={refreshingLocked ? "animate-spin" : ""}>🔄</span>
                  <span>{refreshingLocked ? "Refreshing Quotes..." : "Refresh Live Prices"}</span>
                </button>
              )}
              <button
                onClick={handlePushAllEligible}
                className="bg-emerald-600 hover:bg-emerald-500 text-white font-bold text-xs py-1.5 px-3 rounded-lg border border-emerald-400/30 transition-all flex items-center gap-1.5 shadow-md shadow-emerald-600/20"
              >
                <span>📄</span> Push All Eligible to Paper Trading
              </button>
              <div className="text-xs font-mono px-3 py-1.5 rounded-lg bg-[#141828] text-white border border-[#202742]">
                ✅ {currentScenarioData.successful} analyzed · ❌ {currentScenarioData.failed} failed
              </div>
            </div>
          </div>

          {/* Scenario Tab Filter Pills */}
          <div className="flex flex-wrap items-center gap-2 border-b border-[#171c2b] pb-3">
            <button
              onClick={() => setSelectedScenarioTab("all")}
              className={`px-3 py-1.5 rounded-lg text-xs font-bold transition-all flex items-center gap-1.5 ${
                selectedScenarioTab === "all"
                  ? "bg-white text-black shadow-md shadow-white/10"
                  : "bg-[#131724] text-muted hover:text-white border border-[#1e2438]"
              }`}
            >
              <span>📊</span> All Scenarios ({currentScenarioData.rows.length})
            </button>
            {SCENARIO_GROUPS.map((group) => {
              const groupRows = currentScenarioData.rows.filter((r: any) => r["Scenario ID"] === group.id);
              const count = groupRows.length;
              const isActive = selectedScenarioTab === group.id;
              return (
                <button
                  key={group.id}
                  onClick={() => setSelectedScenarioTab(group.id)}
                  className={`px-3 py-1.5 rounded-lg text-xs font-bold transition-all flex items-center gap-1.5 border ${
                    isActive
                      ? `${group.badgeBg} border-current shadow-md`
                      : "bg-[#131724] text-muted hover:text-white border-[#1e2438]"
                  }`}
                >
                  <span>{group.icon}</span> {group.shortLabel} ({count})
                </button>
              );
            })}
          </div>

          {/* Render Scenario Tables */}
          <div className="space-y-6">
            {SCENARIO_GROUPS.map((group) => {
              const groupRows = currentScenarioData.rows.filter((r: any) => r["Scenario ID"] === group.id);
              if (selectedScenarioTab !== "all" && selectedScenarioTab !== group.id) {
                return null;
              }
              if (groupRows.length === 0 && selectedScenarioTab === "all") {
                return null;
              }

              return (
                <div key={group.id} className="space-y-3">
                  {/* Group Header & Strategy Rule Banner */}
                  <div className={`p-3.5 rounded-xl border ${group.alertBg} space-y-1`}>
                    <div className="flex flex-wrap items-center justify-between gap-2">
                      <div className="flex items-center gap-2">
                        <span className="text-lg">{group.icon}</span>
                        <h3 className={`text-sm font-black tracking-wide ${group.headerColor}`}>
                          {group.title} {isLocked830 ? "(LOCKED)" : ""}
                        </h3>
                        <span className="text-xs px-2 py-0.5 rounded font-mono font-bold bg-[#141826] text-white">
                          {groupRows.length} ticker{groupRows.length === 1 ? "" : "s"}
                        </span>
                      </div>
                      {group.id === "past_stop" && (
                        <span className="text-[11px] font-bold px-2 py-0.5 rounded bg-rose-500/20 text-rose-400 border border-rose-500/30 flex items-center gap-1">
                          <span>🔄</span> DIRECTION REVERSAL ACTIVE · FLIPPED TO {groupRows[0]?.Option || "REVERSE"}
                        </span>
                      )}
                    </div>
                    <p className="text-xs text-white/80 leading-relaxed font-sans">{group.subtitle}</p>
                  </div>

                  {/* Empty state for single filtered tab */}
                  {groupRows.length === 0 && (
                    <div className="text-center py-5 text-xs text-muted font-mono bg-[#0d101d] rounded-lg border border-[#1b2038]">
                      No tickers opened under {group.title}.
                    </div>
                  )}

                  {/* Pure Table Format */}
                  {groupRows.length > 0 && (
                    <div className="bg-[#0d101d] border border-[#1b2038] rounded-xl overflow-hidden shadow-sm">
                      <div className="overflow-x-auto">
                        <table className="w-full text-left border-collapse text-xs font-mono">
                          <thead>
                            <tr className="bg-[#141828] text-muted border-b border-[#1f253d]">
                              <th className="py-2.5 px-3">Ticker</th>
                              <th className="py-2.5 px-2">Generated</th>
                              <th className="py-2.5 px-2">8:30 Open {isLocked830 ? "🔒" : ""}</th>
                              <th className="py-2.5 px-2">Live Current</th>
                              <th className="py-2.5 px-2">Live Move</th>
                              <th className="py-2.5 px-2">Status</th>
                              <th className="py-2.5 px-2">Direction</th>
                              <th className="py-2.5 px-2">Option</th>
                              <th className="py-2.5 px-2">Stop</th>
                              <th className="py-2.5 px-2">T1</th>
                              <th className="py-2.5 px-2">T2</th>
                              <th className="py-2.5 px-2">ATM</th>
                              <th className="py-2.5 px-2">Active RR</th>
                              <th className="py-2.5 px-2 text-center">Paper Trading</th>
                              <th className="py-2.5 px-3">Action Instruction</th>
                            </tr>
                          </thead>
                          <tbody className="divide-y divide-[#181d30]">
                            {groupRows.map((r: any, i: number) => {
                              const isLong = r.Direction === "LONG";
                              const inPaper = paperTickers.has(r.Ticker.toUpperCase());
                              const liveMove = r["Live Move $"] != null ? r["Live Move $"] : r["Move $"];
                              const liveMovePct = r["Live Move %"] != null ? r["Live Move %"] : r["Move %"];
                              const liveStatus = r["Live Status"] || "⚡ IN PLAY";

                              return (
                                <tr key={i} className="hover:bg-[#14192b] transition-colors">
                                  <td className="py-2.5 px-3 font-bold text-white flex items-center gap-1.5">
                                    {r.Option === "CALL" ? "📞" : "📉"}
                                    <span>{r.Ticker}</span>
                                    {r["Is Flipped"] && (
                                      <span className="text-[10px] text-rose-400 font-bold" title="Direction Reversal Active">
                                        🔄
                                      </span>
                                    )}
                                  </td>
                                  <td className="py-2.5 px-2 font-mono text-[11px] text-amber-300 whitespace-nowrap">
                                    {r["Generated Time"] || currentScenarioData.checked_time || "—"}
                                  </td>
                                  <td className="py-2.5 px-2 text-amber-300 font-bold font-mono">
                                    ${r.Open} {isLocked830 ? <span className="text-[10px] text-emerald-400">🔒</span> : null}
                                  </td>
                                  <td className="py-2.5 px-2 text-white font-bold font-mono">
                                    ${r.Current}
                                  </td>
                                  <td
                                    className={`py-2.5 px-2 font-bold font-mono ${
                                      liveMove >= 0 ? "text-emerald-400" : "text-rose-400"
                                    }`}
                                  >
                                    {liveMove >= 0 ? "+" : ""}${liveMove} ({liveMovePct}%)
                                  </td>
                                  <td className="py-2.5 px-2">
                                    <span
                                      className={`px-1.5 py-0.5 rounded text-[10px] font-bold whitespace-nowrap ${
                                        liveStatus.includes("T2")
                                          ? "bg-cyan-500/20 text-cyan-300 border border-cyan-500/30"
                                          : liveStatus.includes("T1")
                                          ? "bg-emerald-500/20 text-emerald-300 border border-emerald-500/30"
                                          : liveStatus.includes("STOPPED")
                                          ? "bg-rose-500/20 text-rose-400 border border-rose-500/30"
                                          : liveStatus.includes("PROFIT")
                                          ? "bg-emerald-950 text-emerald-400"
                                          : "bg-[#171c2e] text-blue-300"
                                      }`}
                                    >
                                      {liveStatus}
                                    </span>
                                  </td>
                                  <td className="py-2.5 px-2">
                                    <span
                                      className={`px-2 py-0.5 rounded text-[10px] font-bold ${
                                        isLong ? "bg-emerald-500/20 text-emerald-400" : "bg-rose-500/20 text-rose-400"
                                      }`}
                                    >
                                      {r.Direction}
                                    </span>
                                  </td>
                                  <td className="py-2.5 px-2">
                                    <span
                                      className={`px-2 py-0.5 rounded text-[10px] font-bold ${
                                        r.Option === "CALL"
                                          ? "bg-blue-500/20 text-blue-400"
                                          : "bg-orange-500/20 text-orange-400"
                                      }`}
                                    >
                                      {r.Option}
                                    </span>
                                  </td>
                                  <td className="py-2.5 px-2 text-rose-400 font-bold">${r.Stop}</td>
                                  <td className="py-2.5 px-2 text-emerald-400 font-bold">${r.T1}</td>
                                  <td className="py-2.5 px-2 text-cyan-400 font-bold">${r.T2}</td>
                                  <td className="py-2.5 px-2 text-white">${r.ATM}</td>
                                  <td className="py-2.5 px-2 text-amber-300 font-bold">{r["Active RR"]}</td>
                                  <td className="py-2.5 px-2 text-center">
                                    {inPaper ? (
                                      <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded text-[10px] font-bold bg-emerald-500/20 text-emerald-400 border border-emerald-500/30">
                                        ✅ In Paper
                                      </span>
                                    ) : (
                                      <button
                                        onClick={() => handlePushToPaper(r)}
                                        className="inline-flex items-center gap-1 px-2 py-1 rounded text-[10px] font-bold bg-[#1d233a] hover:bg-emerald-600 text-white border border-[#2b3558] hover:border-emerald-500 transition-colors shadow-sm"
                                        title="Push trade into Paper Trading engine"
                                      >
                                        📄 Paper Trade
                                      </button>
                                    )}
                                  </td>
                                  <td className="py-2.5 px-3 text-[#d1d7f0] text-[11px] leading-relaxed">
                                    {r["Action Notes"]}
                                  </td>
                                </tr>
                              );
                            })}
                          </tbody>
                        </table>
                      </div>
                    </div>
                  )}
                </div>
              );
            })}
          </div>
        </div>
      )}

      {/* ── MASTER PLAN RESULTS TABLE (SHOWN ON 'plan' TAB OR IF NO OPEN CHECK YET) ── */}
      {planData && (activeViewMode === "plan" || !currentScenarioData) && (
        <div className="space-y-6 animate-in fade-in duration-200">
          {/* Summary Metrics Bar */}
          <div className="grid grid-cols-2 sm:grid-cols-4 gap-4">
            <div className="bg-[#0e111a] border border-[#1c2033] rounded-xl p-4 text-center">
              <span className="text-xs text-muted block mb-1">Total Signals</span>
              <span className="text-2xl font-bold font-mono text-white">{planData.summary.total}</span>
            </div>
            <div className="bg-[#0e111a] border border-emerald-500/20 rounded-xl p-4 text-center">
              <span className="text-xs text-emerald-400 block mb-1">LONG (Calls)</span>
              <span className="text-2xl font-bold font-mono text-emerald-400">{planData.summary.long}</span>
            </div>
            <div className="bg-[#0e111a] border border-rose-500/20 rounded-xl p-4 text-center">
              <span className="text-xs text-rose-400 block mb-1">SHORT (Puts)</span>
              <span className="text-2xl font-bold font-mono text-rose-400">{planData.summary.short}</span>
            </div>
            <div className="bg-[#0e111a] border border-amber-500/20 rounded-xl p-4 text-center">
              <span className="text-xs text-amber-400 block mb-1">Best Setups ⭐</span>
              <span className="text-2xl font-bold font-mono text-amber-400">{planData.summary.best}</span>
            </div>
          </div>

          {/* ⭐ Best Setups highlight banner */}
          {planData.summary.best > 0 && (
            <div className="bg-emerald-950/20 border border-emerald-500/30 rounded-xl p-4 space-y-2">
              <div className="text-xs font-bold text-emerald-400 uppercase tracking-wider flex items-center gap-1.5">
                <span>⭐</span> BEST SETUPS (High R:R & Narrow CPR)
              </div>
              <div className="space-y-1">
                {planData.rows
                  .filter((r) => r["Best Setup"] === "Y")
                  .map((r, i) => (
                    <div key={i} className="text-xs text-[#e8ecff] flex flex-wrap items-center gap-2">
                      <strong className="text-white font-mono">{r.Ticker}</strong> —
                      <span className={r.Direction === "LONG" ? "text-emerald-400 font-bold" : "text-rose-400 font-bold"}>
                        {r.Option}
                      </span>{" "}
                      · ATM ${r["ATM Strike"]} · Stop ${r["Intra Stop"]} · T1 ${r["Intra T1"]} · T2 ${r["Intra T2"]} ·
                      Best RR: <strong className="text-amber-400">{r["Best RR"]}</strong>
                      {r["Generated Time"] && (
                        <span className="text-amber-300/80 font-mono text-[10px] ml-1">
                          ({r["Generated Time"]})
                        </span>
                      )}
                    </div>
                  ))}
              </div>
            </div>
          )}

          {/* 📋 All Symbols Summary Table */}
          <div className="bg-[#0e111a] border border-[#1c2033] rounded-xl overflow-hidden shadow-sm">
            <div className="p-4 border-b border-[#1c2033] flex flex-wrap justify-between items-center gap-2">
              <h2 className="text-base font-bold text-white flex items-center gap-2">
                <span>📋</span> All Symbols Intraday Plan
              </h2>
              <div className="flex items-center gap-3">
                {(planData.generated_time || planData.generated_at) && (
                  <span className="text-xs text-amber-300 font-mono bg-[#141828] border border-amber-500/20 px-2.5 py-1 rounded-md flex items-center gap-1.5">
                    <span>⏱️</span> Generated: <strong>{planData.generated_time || planData.generated_at}</strong>
                  </span>
                )}
                <span className="text-xs text-muted font-mono">Plan For: {planData.plan_label}</span>
              </div>
            </div>
            <div className="overflow-x-auto">
              <table className="w-full text-left border-collapse text-xs font-mono">
                <thead>
                  <tr className="bg-[#141824] text-muted border-b border-[#1f2438]">
                    <th className="py-3 px-3">Ticker</th>
                    <th className="py-3 px-2">Generated</th>
                    <th className="py-3 px-2">Direction</th>
                    <th className="py-3 px-2">Option</th>
                    <th className="py-3 px-2">Grade</th>
                    <th className="py-3 px-2">Close</th>
                    <th className="py-3 px-2">Stop</th>
                    <th className="py-3 px-2">T1</th>
                    <th className="py-3 px-2">T2</th>
                    <th className="py-3 px-2">Best RR</th>
                    <th className="py-3 px-2">ATM</th>
                    <th className="py-3 px-2">CPR</th>
                    <th className="py-3 px-2">Weekly</th>
                    <th className="py-3 px-2">Daily</th>
                    <th className="py-3 px-2">10m</th>
                    <th className="py-3 px-2">30m</th>
                    <th className="py-3 px-2">4H</th>
                    <th className="py-3 px-2">0DTE</th>
                    <th className="py-3 px-2">2-3DTE</th>
                    <th className="py-3 px-3">Options Strategy</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-[#171c2b]">
                  {planData.rows.map((row, idx) => {
                    const isLong = row.Direction === "LONG";
                    return (
                      <tr key={idx} className="hover:bg-[#141826] transition-colors">
                        <td className="py-2.5 px-3 font-bold text-white flex items-center gap-1">
                          {row.Ticker}
                          {row["Best Setup"] === "Y" && <span className="text-amber-400 text-[10px]">⭐</span>}
                        </td>
                        <td className="py-2.5 px-2 font-mono text-[11px] text-amber-300 whitespace-nowrap">
                          {row["Generated Time"] || planData.generated_time || "—"}
                        </td>
                        <td className="py-2.5 px-2">
                          <span
                            className={`px-1.5 py-0.5 rounded text-[10px] font-bold ${
                              isLong ? "bg-emerald-500/20 text-emerald-400" : "bg-rose-500/20 text-rose-400"
                            }`}
                          >
                            {row.Direction}
                          </span>
                        </td>
                        <td className="py-2.5 px-2">
                          <span
                            className={`px-1.5 py-0.5 rounded text-[10px] font-bold ${
                              row.Option === "CALL" ? "bg-blue-500/20 text-blue-400" : "bg-orange-500/20 text-orange-400"
                            }`}
                          >
                            {row.Option}
                          </span>
                        </td>
                        <td className="py-2.5 px-2 font-bold text-muted">{row.Grade}</td>
                        <td className="py-2.5 px-2 text-white">${row.Close}</td>
                        <td className="py-2.5 px-2 text-rose-400 font-bold">${row["Intra Stop"]}</td>
                        <td className="py-2.5 px-2 text-emerald-400 font-bold">${row["Intra T1"]}</td>
                        <td className="py-2.5 px-2 text-cyan-400 font-bold">${row["Intra T2"]}</td>
                        <td className="py-2.5 px-2 text-amber-400 font-bold">{row["Best RR"]}</td>
                        <td className="py-2.5 px-2 text-white font-bold">${row["ATM Strike"]}</td>
                        <td className="py-2.5 px-2">
                          <span
                            className={`px-1.5 py-0.5 rounded text-[10px] ${
                              row["CPR Type"] === "Narrow"
                                ? "bg-amber-500/20 text-amber-300 font-bold"
                                : row["CPR Type"] === "Wide"
                                ? "bg-orange-500/20 text-orange-400"
                                : "bg-blue-500/20 text-blue-400"
                            }`}
                          >
                            {row["CPR Type"]}
                          </span>
                        </td>
                        <td className="py-2.5 px-2 text-muted">{row["Weekly Zone"]}</td>
                        <td className="py-2.5 px-2 text-muted">{row["Daily Zone"]}</td>
                        <td className="py-2.5 px-2">
                          <span
                            className={`px-1 py-0.5 rounded text-[10px] ${
                              row["10m Bias"] === "BULLISH"
                                ? "bg-emerald-950 text-emerald-400 font-bold"
                                : row["10m Bias"] === "BEARISH"
                                ? "bg-rose-950 text-rose-400 font-bold"
                                : "text-muted"
                            }`}
                          >
                            {row["10m Bias"]}
                          </span>
                        </td>
                        <td className="py-2.5 px-2">
                          <span
                            className={`px-1 py-0.5 rounded text-[10px] ${
                              row["30m Bias"] === "BULLISH"
                                ? "bg-emerald-950 text-emerald-400 font-bold"
                                : row["30m Bias"] === "BEARISH"
                                ? "bg-rose-950 text-rose-400 font-bold"
                                : "text-muted"
                            }`}
                          >
                            {row["30m Bias"]}
                          </span>
                        </td>
                        <td className="py-2.5 px-2">
                          <span
                            className={`px-1 py-0.5 rounded text-[10px] ${
                              row["4H Bias"] === "BULLISH"
                                ? "bg-emerald-950 text-emerald-400 font-bold"
                                : row["4H Bias"] === "BEARISH"
                                ? "bg-rose-950 text-rose-400 font-bold"
                                : "text-muted"
                            }`}
                          >
                            {row["4H Bias"]}
                          </span>
                        </td>
                        <td className="py-2.5 px-2 text-muted">{row["0DTE Exp"]}</td>
                        <td className="py-2.5 px-2 text-muted">{row["2-3DTE Exp"]}</td>
                        <td className="py-2.5 px-3 text-[#d1d7f0] text-[11px] max-w-sm truncate" title={row["Options Strategy"]}>
                          {row["Options Strategy"]}
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          </div>
        </div>
      )}

      {/* Initial Empty State */}
      {!planData && !openCheckData && !loading && (
        <div className="text-center py-20 bg-[#0c0e17] border border-[#181d2e] rounded-xl">
          <div className="text-5xl mb-3 opacity-30">🗓️</div>
          <div className="text-muted text-sm max-w-md mx-auto">
            Enter your tickers above, select the plan date, and click{" "}
            <strong className="text-accent">🗓️ GENERATE PLAN</strong> to construct the full intraday playbook with
            stops, targets, and execution scenarios.
          </div>
        </div>
      )}
    </div>
  );
}
