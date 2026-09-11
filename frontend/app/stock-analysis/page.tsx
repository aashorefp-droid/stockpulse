"use client";
import { useState, useRef, useEffect } from "react";
import Link from "next/link";
import DailyTradeChart from "@/components/DailyTradeChart";

const API_BASE = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

const FIB_COLUMNS = [
  "E 261.8%", "E 200.0%", "E 161.8%", "E 141.4%", "E 127.2%",
  "R 0.0%", "R 23.6%", "R 38.2%", "R 50.0%", "R 61.8%", "R 78.6%", "R 100.0%",
  "N -23.6%", "N -38.2%", "N -50.0%", "N -61.8%", "N -100.0%"
];

const GOLDEN_COLS = new Set(["R 38.2%", "R 50.0%", "R 61.8%"]);

export default function StockAnalysisPage() {
  const [tickersInput, setTickersInput] = useState("AAPL,MSFT,NVDA,GOOGL,AMZN,META,TSLA,AMD,PLTR,AVGO");
  const [selectedTicker, setSelectedTicker] = useState<string>("AAPL");
  const [earningsDays, setEarningsDays] = useState(7);
  const [asOfDate, setAsOfDate] = useState("");
  const [scanning, setScanning] = useState(false);
  const [vixData, setVixData] = useState<any>(null);
  const [fibRows, setFibRows] = useState<any[]>([]);
  const [scanResults, setScanResults] = useState<any[]>([]);
  const [activeTab, setActiveTab] = useState<"exceptional" | "exceptional_bear" | "rank1" | "rank2" | "rank3" | "all">("rank1");
  const [tgTickers, setTgTickers] = useState<string[]>([]);
  const [tgPulling, setTgPulling] = useState(false);
  const esRef = useRef<EventSource | null>(null);
  const chartRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    fetch(`${API_BASE}/api/analysis/tab2/vix-scenario`)
      .then((r) => r.json())
      .then((d) => setVixData(d))
      .catch(() => {});
  }, []);

  const handlePullTelegram = async () => {
    setTgPulling(true);
    try {
      const res = await fetch(`${API_BASE}/api/telegram/watchlist?force=true`);
      const data = await res.json();
      if (data.ok && data.tickers && data.tickers.length > 0) {
        setTgTickers(data.tickers);
        const currentList = tickersInput.split(",").map((t) => t.trim().toUpperCase()).filter(Boolean);
        const combined = Array.from(new Set([...data.tickers, ...currentList]));
        setTickersInput(combined.join(","));
      }
    } catch (e) {
      console.error(e);
    } finally {
      setTgPulling(false);
    }
  };

  const startScan = async () => {
    setScanning(true);
    setScanResults([]);
    setFibRows([]);

    const tickers = tickersInput.split(",").map((t) => t.trim().toUpperCase()).filter(Boolean);
    if (!tickers.length) {
      setScanning(false);
      return;
    }

    // 1. Fetch VIX Scenario
    fetch(`${API_BASE}/api/analysis/tab2/vix-scenario${asOfDate ? `?as_of=${asOfDate}` : ""}`)
      .then((r) => r.json())
      .then((d) => setVixData(d))
      .catch(() => {});

    // 2. Fetch Fib Scenario Report
    fetch(`${API_BASE}/api/analysis/tab2/fib-report?tickers=${encodeURIComponent(tickers.join(","))}${asOfDate ? `&as_of=${asOfDate}` : ""}`)
      .then((r) => r.json())
      .then((d) => setFibRows(d.items || []))
      .catch(() => {});

    // 3. Stream MTF Strategy Scanner
    if (esRef.current) esRef.current.close();
    const sseUrl = `${API_BASE}/api/scanner/stream?tickers=${encodeURIComponent(tickers.join(","))}${asOfDate ? `&as_of=${asOfDate}` : ""}`;
    const es = new EventSource(sseUrl);
    esRef.current = es;

    es.onmessage = (event) => {
      try {
        const msg = JSON.parse(event.data);
        if (msg.done) {
          setScanning(false);
          es.close();
        } else if (msg.ticker) {
          setScanResults((prev) => {
            const exists = prev.some((r) => r.ticker === msg.ticker);
            return exists ? prev : [...prev, msg];
          });
        }
      } catch {
        // ignore
      }
    };

    es.onerror = () => {
      setScanning(false);
      es.close();
    };
  };

  const highEarnZone = fibRows.filter((r) => r.earn_zone === "HIGH");
  const midEarnZone = fibRows.filter((r) => r.earn_zone === "MID");
  const lowEarnZone = fibRows.filter((r) => r.earn_zone === "LOW");

  const goldenZoneRows = fibRows.filter((r) => {
    const r38 = r.fib_all?.["R 38.2%"] ?? r.r_382;
    const r61 = r.fib_all?.["R 61.8%"] ?? r.r_618;
    if (r38 && r61 && r.close) {
      const minVal = Math.min(r38, r61);
      const maxVal = Math.max(r38, r61);
      return r.close >= minVal && r.close <= maxVal;
    }
    return false;
  });

  const actionable = scanResults.filter((r) => r.entry_status === "ENTER");
  const exceptionalList = scanResults.filter(
    (r) => r.confidence === "HIGH" && (Math.abs(r.score ?? 0) >= 4) && (r.expected_wr ?? 0) > 80 && r.mtf_signal === "A+ Long"
  );
  const exceptionalBearList = scanResults.filter(
    (r) => r.confidence === "HIGH" && ((r.score ?? 0) <= -4) && (r.expected_wr ?? 0) > 80 && r.mtf_signal === "A+ Short"
  );
  const rank1List = scanResults.filter((r) => r.mtf_rank === 1);
  const rank2List = scanResults.filter((r) => r.mtf_rank === 2);
  const rank3List = scanResults.filter((r) => (r.mtf_rank ?? 0) >= 3);

  const getFilteredRows = () => {
    switch (activeTab) {
      case "exceptional": return exceptionalList;
      case "exceptional_bear": return exceptionalBearList;
      case "rank1": return rank1List;
      case "rank2": return rank2List;
      case "rank3": return rank3List;
      default: return scanResults;
    }
  };

  const currentTabRows = getFilteredRows();

  const bullishCount = scanResults.filter((r) => r.verdict === "BULLISH").length;
  const bearishCount = scanResults.filter((r) => r.verdict === "BEARISH").length;
  const highConfCount = scanResults.filter((r) => r.confidence === "HIGH").length;
  const gradeS = actionable.filter((r) => r.entry_grade === "S").length;
  const gradeA = actionable.filter((r) => r.entry_grade === "A").length;
  const gradeB = actionable.filter((r) => ["B", "B-"].includes(r.entry_grade)).length;
  const gradeC = actionable.filter((r) => r.entry_grade === "C").length;

  const downloadCsv = (rows: any[], filename: string) => {
    if (!rows.length) return;
    const keys = Object.keys(rows[0]).filter((k) => typeof rows[0][k] !== "object");
    const header = keys.join(",");
    const lines = rows.map((r) => keys.map((k) => `"${r[k] ?? ""}"`).join(","));
    const csvContent = "data:text/csv;charset=utf-8," + [header, ...lines].join("\n");


    const encodedUri = encodeURI(csvContent);
    const link = document.createElement("a");
    link.setAttribute("href", encodedUri);
    link.setAttribute("download", filename);
    document.body.appendChild(link);
    link.click();
    document.body.removeChild(link);
  };

  return (
    <div className="max-w-screen-2xl mx-auto px-4 py-8 text-slate-200 font-sans">
      {/* HEADER BANNER */}
      <div className="bg-gradient-to-r from-[#0a0b14] to-[#131625] border border-[#1a1d2e] rounded-xl p-6 mb-6">
        <div className="flex items-center gap-4">
          <div className="text-3xl">🔬</div>
          <div>
            <h1 className="text-xl font-bold text-[#e8ecff]">Stock Analysis</h1>
            <p className="text-xs text-[#6b7099] mt-1">
              Technical + Fundamental analysis. Scan a watchlist for upcoming earnings, get verdicts, valuations, growth & risk flags, and export CSV.
            </p>
          </div>
        </div>
      </div>

      {/* CONTROLS & WATCHLIST INPUT */}
      <div className="bg-[#0d0f17] border border-[#1a1d2e] rounded-xl p-5 mb-8">
        {tgTickers.length > 0 && (
          <div className="bg-[#1a1d2e] border-l-4 border-[#4d9fff] px-3 py-2 rounded mb-4 text-xs flex items-center justify-between">
            <div>
              📱 <b className="text-[#4d9fff]">{tgTickers.length} Telegram tickers</b> prepended to watchlist: {tgTickers.slice(0, 10).join(", ")}
            </div>
            <span className="text-[#6b7099]">Ready</span>
          </div>
        )}

        <div className="grid grid-cols-1 md:grid-cols-12 gap-4 items-end">
          <div className="md:col-span-6">
            <div className="flex justify-between items-center mb-1">
              <label className="text-xs font-semibold text-[#6b7099] uppercase tracking-wider">
                Tickers to Scan (comma-separated)
              </label>
              <button
                type="button"
                onClick={handlePullTelegram}
                disabled={tgPulling}
                className="text-xs bg-[#1a1d2e] hover:bg-[#252a42] text-[#4d9fff] px-2.5 py-1 rounded border border-[#4d9fff]/30 transition-all"
              >
                {tgPulling ? "📱 Pulling..." : "📱 Pull Telegram"}
              </button>
            </div>
            <textarea
              className="w-full bg-[#131625] border border-[#1a1d2e] rounded-lg px-3 py-2 text-white font-mono text-sm focus:outline-none focus:border-[#4d9fff] resize-none h-20"
              value={tickersInput}
              onChange={(e) => setTickersInput(e.target.value.toUpperCase())}
            />
          </div>

          <div className="md:col-span-2">
            <label className="block text-xs font-semibold text-[#6b7099] mb-1 uppercase tracking-wider">
              Earnings in Next (days)
            </label>
            <input
              type="number"
              min={1}
              max={30}
              value={earningsDays}
              onChange={(e) => setEarningsDays(Number(e.target.value))}
              className="w-full bg-[#131625] border border-[#1a1d2e] rounded-lg px-3 py-2 text-white font-mono text-sm h-11 focus:outline-none focus:border-[#4d9fff]"
            />
          </div>

          <div className="md:col-span-2">
            <label className="block text-xs font-semibold text-[#6b7099] mb-1 uppercase tracking-wider">
              As Of Date (Backtest)
            </label>
            <input
              type="date"
              className="w-full bg-[#131625] border border-[#1a1d2e] rounded-lg px-3 py-2 text-white font-mono text-sm h-11 focus:outline-none focus:border-[#4d9fff]"
              value={asOfDate}
              onChange={(e) => setAsOfDate(e.target.value)}
            />
          </div>

          <div className="md:col-span-2">
            <button
              onClick={startScan}
              disabled={scanning}
              className="w-full bg-[#4d9fff] hover:bg-[#3b82f6] text-black font-bold text-sm h-11 rounded-lg transition-all flex items-center justify-center gap-2 shadow-lg shadow-[#4d9fff]/20"
            >
              {scanning ? (
                <>
                  <div className="w-4 h-4 border-2 border-black border-t-transparent rounded-full animate-spin"></div>
                  <span>Scanning...</span>
                </>
              ) : (
                <>
                  <span>🔬</span>
                  <span>SCAN</span>
                </>
              )}
            </button>
          </div>
        </div>
      </div>

      {/* TRADINGVIEW DAILY CHART & BEST ENTRY/EXIT POINTS */}
      <div ref={chartRef} className="scroll-mt-20 mb-8">
        <div className="flex flex-wrap items-center justify-between gap-2 mb-3">
          <div className="flex items-center gap-2">
            <span className="text-xl">🎯</span>
            <h2 className="text-base font-bold text-[#e8ecff]">TradingView Daily Chart — Best Entry & Exit Points</h2>
          </div>
          <span className="text-xs text-[#6b7099] font-mono">
            Interactive Daily Candlesticks · Best Entry Level · Protective Stop · Targets 1 & 2
          </span>
        </div>
        <DailyTradeChart
          ticker={selectedTicker}
          onTickerChange={setSelectedTicker}
          availableTickers={Array.from(new Set([
            ...tickersInput.split(",").map((t) => t.trim().toUpperCase()).filter(Boolean),
            ...scanResults.map((r) => r.ticker),
          ]))}
        />
      </div>

      {/* 1. VIX FIB SCENARIO CARD & TABLE */}
      {vixData && (
        <div className="bg-gradient-to-r from-[#0d0f17] to-[#131625] border border-[#1a1d2e] rounded-xl p-5 mb-8 shadow-sm">
          <div className="flex flex-wrap items-center justify-between gap-3 mb-4">
            <div className="flex items-center gap-2.5">
              <span className="text-2xl">📉</span>
              <h2 className="text-base font-bold text-[#e8ecff]">VIX Fib Scenario</h2>
              <span className={`px-2.5 py-0.5 rounded text-xs font-bold ${
                vixData.zone === "HIGH" ? "bg-[#3d0a1a] text-[#ff4d6a] border border-[#ff4d6a]/40" :
                vixData.zone === "LOW" ? "bg-[#0a3d1f] text-[#00e5a0] border border-[#00e5a0]/40" :
                "bg-[#3d3a0a] text-[#f5c842] border border-[#f5c842]/40"
              }`}>
                {vixData.zone === "HIGH" ? "🔴 HIGH Zone" : vixData.zone === "LOW" ? "🟢 LOW Zone" : "🟡 MID Zone"}
              </span>
              <span className="text-xs text-[#6b7099] font-mono">
                — Close <b className="text-white">${vixData.close?.toFixed(2)}</b> | 10W Range: ${vixData.lo10?.toFixed(2)}–${vixData.hi10?.toFixed(2)} | Pos: {vixData.pos?.toFixed(0)}%
              </span>
            </div>
            <div className="text-xs text-[#6b7099] font-mono italic">
              {vixData.conclusion}
            </div>
          </div>

          <div className="overflow-x-auto rounded-lg border border-[#1a1d2e]">
            <table className="w-full text-left text-xs font-mono">
              <thead className="bg-[#0a0b14] text-[#6b7099] uppercase border-b border-[#1a1d2e]">
                <tr>
                  <th className="px-3 py-2">Ticker</th>
                  <th className="px-3 py-2">Close</th>
                  <th className="px-3 py-2">Zone</th>
                  {FIB_COLUMNS.map((col) => (
                    <th
                      key={col}
                      className={`px-3 py-2 text-center whitespace-nowrap ${
                        GOLDEN_COLS.has(col) ? "text-[#d4a017] bg-[#1a1500] font-bold border-b-2 border-[#d4a017]": ""
                      }`}
                    >
                      {col}
                    </th>
                  ))}
                  <th className="px-3 py-2">Conclusion</th>
                </tr>
              </thead>
              <tbody className="bg-[#0d0f17]">
                <tr className="border-b border-[#1a1d2e]/50 hover:bg-[#131625]">
                  <td className="px-3 py-2 font-bold text-[#4d9fff]">^VIX</td>
                  <td className="px-3 py-2 text-white font-semibold">${vixData.close?.toFixed(2)}</td>
                  <td className="px-3 py-2">
                    <span className={`px-2 py-0.5 rounded text-[10px] font-bold ${
                      vixData.zone === "HIGH" ? "bg-[#3d0a1a] text-[#ff4d6a]" :
                      vixData.zone === "LOW" ? "bg-[#0a3d1f] text-[#00e5a0]" :
                      "bg-[#3d3a0a] text-[#f5c842]"
                    }`}>
                      {vixData.zone}
                    </span>
                  </td>
                  {FIB_COLUMNS.map((col) => (
                    <td
                      key={col}
                      className={`px-3 py-2 text-center whitespace-nowrap ${
                        GOLDEN_COLS.has(col)
                          ? "bg-[#1a1500] text-[#d4a017] font-semibold"
                          : "text-[#e8ecff]"
                      }`}
                    >
                      {vixData.fib?.[col] !== undefined ? `$${Number(vixData.fib[col]).toFixed(2)}` : "—"}
                    </td>
                  ))}
                  <td className="px-3 py-2 text-[#6b7099] whitespace-nowrap">{vixData.conclusion}</td>
                </tr>
              </tbody>
            </table>
          </div>
        </div>
      )}

      {/* 2. FIB SCENARIO REPORT (WEEKLY ZONE) */}
      {fibRows.length > 0 && (
        <div className="bg-[#0d0f17] border border-[#1a1d2e] rounded-xl p-5 mb-8 shadow-sm">
          <div className="flex items-center justify-between mb-4 border-b border-[#1a1d2e] pb-3">
            <h2 className="text-base font-bold text-[#e8ecff] flex items-center gap-2">
              <span>📊</span>
              <span>Fib Scenario Report (Weekly Zone)</span>
            </h2>
            <button
              onClick={() => downloadCsv(fibRows, `fib_scenario_report_${new Date().toISOString().slice(0, 10)}.csv`)}
              className="text-xs bg-[#1a1d2e] hover:bg-[#252a42] text-[#4d9fff] px-3 py-1.5 rounded border border-[#1a1d2e] font-semibold flex items-center gap-1.5"
            >
              <span>📥</span>
              <span>Download Fib CSV</span>
            </button>
          </div>

          {highEarnZone.length > 0 && (
            <div className="mb-6">
              <div className="bg-[#3d0a1a]/30 border-l-4 border-[#ff4d6a] px-3 py-1.5 rounded mb-2 text-xs flex items-center gap-2">
                <span>🔴</span>
                <b className="text-[#ff4d6a]">HIGH Earn Zone</b>
                <span className="text-[#6b7099]">({highEarnZone.length} tickers)</span>
              </div>
              {renderFibTable(highEarnZone)}
            </div>
          )}

          {midEarnZone.length > 0 && (
            <div className="mb-6">
              <div className="bg-[#3d3a0a]/30 border-l-4 border-[#f5c842] px-3 py-1.5 rounded mb-2 text-xs flex items-center gap-2">
                <span>🟡</span>
                <b className="text-[#f5c842]">MID Earn Zone</b>
                <span className="text-[#6b7099]">({midEarnZone.length} tickers)</span>
              </div>
              {renderFibTable(midEarnZone)}
            </div>
          )}

          {lowEarnZone.length > 0 && (
            <div className="mb-6">
              <div className="bg-[#0a3d1f]/30 border-l-4 border-[#00e5a0] px-3 py-1.5 rounded mb-2 text-xs flex items-center gap-2">
                <span>🟢</span>
                <b className="text-[#00e5a0]">LOW Earn Zone</b>
                <span className="text-[#6b7099]">({lowEarnZone.length} tickers)</span>
              </div>
              {renderFibTable(lowEarnZone)}
            </div>
          )}

          {/* 🥇 GOLDEN ZONE SUMMARY */}
          {goldenZoneRows.length > 0 && (
            <div className="mt-8 bg-gradient-to-r from-[#1a1500] to-[#1f1800] border border-[#d4a017]/40 rounded-xl p-5">
              <div className="flex items-center gap-2.5 mb-3">
                <span className="text-xl">🥇</span>
                <h3 className="text-sm font-bold text-[#d4a017]">Golden Zone</h3>
                <span className="text-xs text-[#8b7d3c]">
                  ({goldenZoneRows.length} tickers) — Price within R 38.2% – R 61.8% retracement
                </span>
              </div>
              <div className="overflow-x-auto rounded-lg border border-[#d4a017]/30">
                <table className="w-full text-left text-xs font-mono">
                  <thead className="bg-[#1a1500] text-[#d4a017] uppercase border-b border-[#d4a017]/30">
                    <tr>
                      <th className="px-4 py-2.5">Ticker</th>
                      <th className="px-4 py-2.5">Price</th>
                      <th className="px-4 py-2.5">R 38.2%</th>
                      <th className="px-4 py-2.5">R 50.0%</th>
                      <th className="px-4 py-2.5">R 61.8%</th>
                      <th className="px-4 py-2.5">Earn Zone</th>
                      <th className="px-4 py-2.5">Weekly Zone</th>
                      <th className="px-4 py-2.5">Inst/Retail Control</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-[#d4a017]/20 bg-[#0d0f17]">
                    {goldenZoneRows.map((r) => (
                      <tr key={r.ticker} className="hover:bg-[#1a1500]/40">
                        <td className="px-4 py-2.5 font-bold text-[#4d9fff]">
                          <Link href={`/stock/${r.ticker}`} className="hover:underline">{r.ticker}</Link>
                        </td>
                        <td className="px-4 py-2.5 text-white font-bold">${r.close?.toFixed(2)}</td>
                        <td className="px-4 py-2.5 text-[#d4a017] font-semibold">${r.r_382?.toFixed(2) ?? r.fib_all?.["R 38.2%"]}</td>
                        <td className="px-4 py-2.5 text-[#d4a017] font-semibold">${r.r_500?.toFixed(2) ?? r.fib_all?.["R 50.0%"]}</td>
                        <td className="px-4 py-2.5 text-[#d4a017] font-semibold">${r.r_618?.toFixed(2) ?? r.fib_all?.["R 61.8%"]}</td>
                        <td className="px-4 py-2.5">
                          <span className={`px-2 py-0.5 rounded text-[10px] font-bold ${
                            r.earn_zone === "HIGH" ? "bg-[#3d0a1a] text-[#ff4d6a]" :
                            r.earn_zone === "LOW" ? "bg-[#0a3d1f] text-[#00e5a0]" :
                            "bg-[#3d3a0a] text-[#f5c842]"
                          }`}>
                            {r.earn_zone}
                          </span>
                        </td>
                        <td className="px-4 py-2.5">
                          <span className={`px-2 py-0.5 rounded text-[10px] font-bold ${
                            r.weekly_zone === "HIGH" ? "bg-[#3d0a1a] text-[#ff4d6a]" :
                            r.weekly_zone === "LOW" ? "bg-[#0a3d1f] text-[#00e5a0]" :
                            "bg-[#3d3a0a] text-[#f5c842]"
                          }`}>
                            {r.weekly_zone}
                          </span>
                        </td>
                        <td className="px-4 py-2.5 text-xs text-[#e8ecff]">{r.inst_control}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          )}
        </div>
      )}

      {/* 3. MULTI-TIMEFRAME STRATEGY SCAN SECTION */}
      {scanResults.length > 0 && (
        <div className="bg-[#0d0f17] border border-[#1a1d2e] rounded-xl p-5 shadow-sm">
          <div className="mb-4">
            <h2 className="text-base font-bold text-[#e8ecff] flex items-center gap-2">
              <span>🔬</span>
              <span>Stock Analysis — Multi-Timeframe Strategy Scan</span>
            </h2>
          </div>

          <div className="bg-[#131625] border border-[#1a1d2e] rounded-lg p-3.5 mb-5 text-xs space-y-1.5">
            <div className="text-[#6b7099]">
              Scanned <b className="text-[#e8ecff]">{scanResults.length}</b> tickers · <b className="text-[#00e5a0]">{bullishCount}</b> bullish · <b className="text-[#ff4d6a]">{bearishCount}</b> bearish · <b className="text-[#4d9fff]">{highConfCount}</b> high confidence · <b className="text-[#e8ecff]">{actionable.length}</b> actionable / {scanResults.length} total
            </div>
            <div className="text-[#6b7099]">
              Entry grades (actionable): <b className="text-[#00e5a0]">S: {gradeS}</b> · <b className="text-[#00e5a0]">A: {gradeA}</b> · <b className="text-[#4d9fff]">B: {gradeB}</b> · <b className="text-[#f0c040]">C: {gradeC}</b>
            </div>
          </div>

          <div className="flex gap-2 mb-4 overflow-x-auto pb-2 border-b border-[#1a1d2e]">
            {[
              { id: "exceptional", label: `⭐ Exceptional (${exceptionalList.length})` },
              { id: "exceptional_bear", label: `💀 Exceptional Bear (${exceptionalBearList.length})` },
              { id: "rank1", label: `🎯 Rank 1 — Full Align (${rank1List.length})` },
              { id: "rank2", label: `✅ Rank 2 — Two Aligned (${rank2List.length})` },
              { id: "rank3", label: `📋 Rank 3+ — Rest (${rank3List.length})` },
              { id: "all", label: `📊 All Tickers (${scanResults.length})` },
            ].map((t) => (
              <button
                key={t.id}
                onClick={() => setActiveTab(t.id as any)}
                className={`px-3.5 py-2 rounded-lg text-xs font-semibold whitespace-nowrap transition-all ${
                  activeTab === t.id
                    ? "bg-[#4d9fff] text-black font-bold shadow-md shadow-[#4d9fff]/20"
                    : "bg-[#131625] text-[#6b7099] hover:text-white border border-[#1a1d2e]"
                }`}
              >
                {t.label}
              </button>
            ))}
          </div>

          {currentTabRows.length === 0 ? (
            <div className="py-12 text-center text-xs text-[#6b7099]">
              No setups found for this rank category.
            </div>
          ) : (
            <div className="space-y-3">
              <div className="flex justify-end">
                <button
                  onClick={() => downloadCsv(currentTabRows, `scan_${activeTab}_${new Date().toISOString().slice(0, 10)}.csv`)}
                  className="text-xs bg-[#1a1d2e] hover:bg-[#252a42] text-[#4d9fff] px-3 py-1.5 rounded border border-[#1a1d2e] font-semibold flex items-center gap-1.5"
                >
                  <span>📥</span>
                  <span>Download {activeTab} CSV</span>
                </button>
              </div>

              <div className="overflow-x-auto rounded-lg border border-[#1a1d2e]">
                <table className="w-full text-left text-xs font-mono">
                  <thead className="bg-[#0a0b14] text-[#6b7099] uppercase border-b border-[#1a1d2e]">
                    <tr>
                      <th className="px-3 py-2.5">Chart</th>
                      <th className="px-3 py-2.5">Ticker</th>
                      <th className="px-3 py-2.5">Price</th>
                      <th className="px-3 py-2.5 text-center">Funda</th>
                      <th className="px-3 py-2.5 text-center">Status</th>
                      <th className="px-3 py-2.5 text-center">Grade</th>
                      <th className="px-3 py-2.5">Entry</th>
                      <th className="px-3 py-2.5">Stop</th>
                      <th className="px-3 py-2.5">Target 1</th>
                      <th className="px-3 py-2.5">Target 2</th>
                      <th className="px-3 py-2.5">T1 (td)</th>
                      <th className="px-3 py-2.5">Alpaca Options</th>
                      <th className="px-3 py-2.5 text-center">Weekly</th>
                      <th className="px-3 py-2.5 text-center">Daily</th>
                      <th className="px-3 py-2.5 text-center">4H</th>
                      <th className="px-3 py-2.5 text-center">MA Bias</th>
                      <th className="px-3 py-2.5">Signal</th>
                      <th className="px-3 py-2.5">CPR Type</th>
                      <th className="px-3 py-2.5">Sector</th>
                      <th className="px-3 py-2.5">Verdict</th>
                      <th className="px-3 py-2.5 text-center">Confidence</th>
                      <th className="px-3 py-2.5 text-center">Score</th>
                      <th className="px-3 py-2.5">Day Candle</th>
                      <th className="px-3 py-2.5 text-center">Vol Trend</th>
                      <th className="px-3 py-2.5 text-center">Vol Ratio</th>
                      <th className="px-3 py-2.5">POC</th>
                      <th className="px-3 py-2.5">VAL</th>
                      <th className="px-3 py-2.5">VAH</th>
                      <th className="px-3 py-2.5">Risk%</th>
                      <th className="px-3 py-2.5">RR(T1)</th>
                      <th className="px-3 py-2.5">P/E</th>
                      <th className="px-3 py-2.5">EPS Growth</th>
                      <th className="px-3 py-2.5">Margin</th>
                      <th className="px-3 py-2.5">1Y Target</th>
                      <th className="px-3 py-2.5">Upside</th>
                      <th className="px-3 py-2.5 text-center">News</th>
                      <th className="px-3 py-2.5 text-center">Exp WR%</th>
                      <th className="px-3 py-2.5">Signals</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-[#1a1d2e] bg-[#0d0f17]">
                    {currentTabRows.map((r) => {
                      const isBull = r.verdict === "BULLISH";
                      const isBear = r.verdict === "BEARISH";
                      return (
                        <tr key={r.ticker} className="hover:bg-[#131625] transition-colors">
                          <td className="px-3 py-2 whitespace-nowrap">
                            <button
                              type="button"
                              onClick={() => {
                                setSelectedTicker(r.ticker);
                                chartRef.current?.scrollIntoView({ behavior: "smooth" });
                              }}
                              className="text-[#4d9fff] hover:text-white bg-[#1a2d3d] hover:bg-[#25394d] border border-[#4d9fff]/30 px-2 py-0.5 rounded text-[11px] font-semibold flex items-center gap-1 transition-all"
                              title="Inspect TradingView Daily Chart & Entry/Exit points"
                            >
                              <span>📈</span>
                              <span>Daily Chart</span>
                            </button>
                          </td>
                          <td className="px-3 py-2 font-bold text-[#4d9fff] whitespace-nowrap">
                            <button
                              type="button"
                              onClick={() => {
                                setSelectedTicker(r.ticker);
                                chartRef.current?.scrollIntoView({ behavior: "smooth" });
                              }}
                              className="hover:underline text-left font-bold text-[#4d9fff]"
                              title="Click to view daily entry/exit points"
                            >
                              {r.ticker}
                            </button>
                          </td>
                          <td className="px-3 py-2 text-white font-bold whitespace-nowrap">
                            ${r.price?.toFixed(2) ?? "—"}
                          </td>
                          <td className="px-3 py-2 text-center whitespace-nowrap">
                            <span className={`px-2 py-0.5 rounded text-[10px] font-bold ${
                              r.fundamental === "S" || r.fundamental === "Strong" ? "bg-[#0a3d1f] text-[#00e5a0]" :
                              r.fundamental === "W" || r.fundamental === "Weak" ? "bg-[#3d0a1a] text-[#ff4d6a]" :
                              "bg-[#1a1d2e] text-[#6b7099]"
                            }`}>
                              {r.fundamental?.charAt(0) ?? "N"}
                            </span>
                          </td>
                          <td className="px-3 py-2 text-center whitespace-nowrap">
                            <span className={`px-2 py-0.5 rounded text-[10px] font-bold ${
                              r.entry_status === "ENTER" ? "bg-[#0a3d1f] text-[#00e5a0]" :
                              r.entry_status === "HOLD" ? "bg-[#3d3a0a] text-[#f5c842]" :
                              "bg-[#1a1d2e] text-[#6b7099]"
                            }`}>
                              {r.entry_status === "ENTER" ? "E" : r.entry_status === "HOLD" ? "H" : "S"}
                            </span>
                          </td>
                          <td className="px-3 py-2 text-center whitespace-nowrap">
                            <span className={`px-2 py-0.5 rounded text-[10px] font-bold ${
                              ["S", "A"].includes(r.entry_grade) ? "bg-[#0a3d1f] text-[#00e5a0]" :
                              ["B", "B-"].includes(r.entry_grade) ? "bg-[#1a2d3d] text-[#4d9fff]" :
                              r.entry_grade === "C" ? "bg-[#3d3a0a] text-[#f5c842]" :
                              "bg-[#1a1d2e] text-[#6b7099]"
                            }`}>
                              {r.entry_grade ?? "—"}
                            </span>
                          </td>
                          <td className="px-3 py-2 text-[#e8ecff] whitespace-nowrap">
                            {r.entry != null ? `$${Number(r.entry).toFixed(2)}` : "—"}
                          </td>
                          <td className="px-3 py-2 text-[#ff4d6a] whitespace-nowrap">
                            {r.stop_loss != null ? `$${Number(r.stop_loss).toFixed(2)}` : "—"}
                          </td>
                          <td className="px-3 py-2 text-[#00e5a0] whitespace-nowrap font-semibold">
                            {r.target1 != null ? `$${Number(r.target1).toFixed(2)}` : "—"}
                          </td>
                          <td className="px-3 py-2 text-[#00e5a0] whitespace-nowrap font-semibold">
                            {r.target2 != null ? `$${Number(r.target2).toFixed(2)}` : "—"}
                          </td>
                          <td className="px-3 py-2 text-[#6b7099] whitespace-nowrap">{r.t1_days ?? "—"}</td>

                          <td className="px-3 py-2 text-xs text-[#a78bfa] max-w-xs truncate">
                            {r.alpaca_options ?? "—"}
                          </td>
                          <td className="px-3 py-2 text-center whitespace-nowrap">
                            <span className={`px-2 py-0.5 rounded text-[10px] font-bold ${
                              r.weekly_bias?.toUpperCase().includes("BULL") ? "bg-[#0a3d1f] text-[#00e5a0]" :
                              r.weekly_bias?.toUpperCase().includes("BEAR") ? "bg-[#3d0a1a] text-[#ff4d6a]" :
                              "bg-[#1a1d2e] text-[#6b7099]"
                            }`}>
                              {r.weekly_bias?.toUpperCase().includes("BULL") ? "Bull" : r.weekly_bias?.toUpperCase().includes("BEAR") ? "Bear" : "—"}
                            </span>
                          </td>
                          <td className="px-3 py-2 text-center whitespace-nowrap">
                            <span className={`px-2 py-0.5 rounded text-[10px] font-bold ${
                              r.daily_bias?.toUpperCase().includes("BULL") ? "bg-[#0a3d1f] text-[#00e5a0]" :
                              r.daily_bias?.toUpperCase().includes("BEAR") ? "bg-[#3d0a1a] text-[#ff4d6a]" :
                              "bg-[#1a1d2e] text-[#6b7099]"
                            }`}>
                              {r.daily_bias?.toUpperCase().includes("BULL") ? "Bull" : r.daily_bias?.toUpperCase().includes("BEAR") ? "Bear" : "—"}
                            </span>
                          </td>
                          <td className="px-3 py-2 text-center whitespace-nowrap">
                            <span className={`px-2 py-0.5 rounded text-[10px] font-bold ${
                              r.h4_bias?.toUpperCase().includes("BULL") ? "bg-[#0a3d1f] text-[#00e5a0]" :
                              r.h4_bias?.toUpperCase().includes("BEAR") ? "bg-[#3d0a1a] text-[#ff4d6a]" :
                              "bg-[#1a1d2e] text-[#6b7099]"
                            }`}>
                              {r.h4_bias?.toUpperCase().includes("BULL") ? "Bull" : r.h4_bias?.toUpperCase().includes("BEAR") ? "Bear" : "—"}
                            </span>
                          </td>
                          <td className="px-3 py-2 text-center whitespace-nowrap">
                            <span className={`px-2 py-0.5 rounded text-[10px] font-bold ${
                              r.ma_bias?.toUpperCase().includes("BULL") ? "bg-[#0a3d1f] text-[#00e5a0]" :
                              r.ma_bias?.toUpperCase().includes("BEAR") ? "bg-[#3d0a1a] text-[#ff4d6a]" :
                              "bg-[#1a1d2e] text-[#6b7099]"
                            }`}>
                              {r.ma_bias?.toUpperCase().includes("BULL") ? "Bull" : r.ma_bias?.toUpperCase().includes("BEAR") ? "Bear" : "—"}
                            </span>
                          </td>
                          <td className="px-3 py-2 font-bold whitespace-nowrap">
                            <span className={`px-2 py-0.5 rounded text-[11px] ${
                              r.mtf_signal?.includes("A+ Long") ? "bg-[#0a3d1f] text-[#00e5a0] font-bold" :
                              r.mtf_signal?.includes("A+ Short") ? "bg-[#3d0a1a] text-[#ff4d6a] font-bold" :
                              "text-[#a78bfa]"
                            }`}>
                              {r.mtf_signal ?? "—"}
                            </span>
                          </td>
                          <td className="px-3 py-2 text-[#6b7099] whitespace-nowrap">{r.cpr_type ?? "—"}</td>
                          <td className="px-3 py-2 text-[#6b7099] whitespace-nowrap">{r.sector ?? "—"}</td>
                          <td className="px-3 py-2 font-bold whitespace-nowrap">
                            <span className={isBull ? "text-[#00e5a0]" : isBear ? "text-[#ff4d6a]" : "text-[#f5c842]"}>
                              {r.verdict ?? "—"}
                            </span>
                            {r.sv_verdict && (
                              <span
                                className={`ml-2 px-1.5 py-0.5 rounded text-[10px] font-extrabold uppercase border ${
                                  r.sv_verdict === "GROWTH" ? "bg-[#8b5cf6]/20 text-[#c4b5fd] border-[#8b5cf6]/40" :
                                  r.sv_verdict === "VALUE" ? "bg-[#0284c7]/20 text-[#7dd3fc] border-[#0284c7]/40" :
                                  r.sv_verdict === "AVOID" ? "bg-[#ef4444]/20 text-[#fca5a5] border-[#ef4444]/40" :
                                  "bg-[#d97706]/20 text-[#fcd34d] border-[#d97706]/40"
                                }`}
                                title={r.sv_signal ? `StockVerdicts: ${r.sv_signal}` : `StockVerdicts: ${r.sv_verdict}`}
                              >
                                {r.sv_verdict}
                              </span>
                            )}
                          </td>
                          <td className="px-3 py-2 text-center whitespace-nowrap">
                            <span className={`px-2 py-0.5 rounded text-[10px] font-bold ${
                              r.confidence === "HIGH" ? "bg-[#1a2d3d] text-[#4d9fff]" : "text-[#6b7099]"
                            }`}>
                              {r.confidence ?? "—"}
                            </span>
                          </td>
                          <td className="px-3 py-2 text-center font-bold whitespace-nowrap">
                            <span className={r.score > 0 ? "text-[#00e5a0]" : r.score < 0 ? "text-[#ff4d6a]" : "text-[#6b7099]"}>
                              {r.score ?? 0}
                            </span>
                          </td>
                          <td className="px-3 py-2 text-[#6b7099] whitespace-nowrap">{r.day_candle ?? "—"}</td>
                          <td className="px-3 py-2 text-center whitespace-nowrap">
                            <span className={r.vol_trend === "ACCUMULATING" ? "text-[#00e5a0] font-bold" : r.vol_trend === "DISTRIBUTING" ? "text-[#ff4d6a] font-bold" : "text-[#6b7099]"}>
                              {r.vol_trend ?? "—"}
                            </span>
                          </td>
                          <td className="px-3 py-2 text-center text-[#e8ecff] whitespace-nowrap">
                            {r.vol_ratio ? `${r.vol_ratio}x` : "—"}
                          </td>
                          <td className="px-3 py-2 text-[#6b7099] whitespace-nowrap">{r.poc != null ? `$${Number(r.poc).toFixed(2)}` : "—"}</td>
                          <td className="px-3 py-2 text-[#6b7099] whitespace-nowrap">{r.val != null ? `$${Number(r.val).toFixed(2)}` : "—"}</td>
                          <td className="px-3 py-2 text-[#6b7099] whitespace-nowrap">{r.vah != null ? `$${Number(r.vah).toFixed(2)}` : "—"}</td>
                          <td className="px-3 py-2 text-[#6b7099] whitespace-nowrap">{r.risk_pct ? `${Number(r.risk_pct).toFixed(1)}%` : "—"}</td>
                          <td className="px-3 py-2 text-[#e8ecff] whitespace-nowrap">{r.rr_t1 ? `${Number(r.rr_t1).toFixed(2)}x` : "—"}</td>
                          <td className="px-3 py-2 text-[#6b7099] whitespace-nowrap">{r.pe_ratio ?? "—"}</td>
                          <td className="px-3 py-2 text-[#6b7099] whitespace-nowrap">{r.earnings_growth ? `${(Number(r.earnings_growth) * 100).toFixed(1)}%` : "—"}</td>
                          <td className="px-3 py-2 text-[#6b7099] whitespace-nowrap">{r.profit_margin ? `${(Number(r.profit_margin) * 100).toFixed(1)}%` : "—"}</td>
                          <td className="px-3 py-2 text-[#e8ecff] whitespace-nowrap">
                            {r.analyst_target != null
                              ? (typeof r.analyst_target === "number" ? `$${r.analyst_target.toFixed(2)}` : `$${r.analyst_target}`)
                              : (r.target_1y != null ? `$${r.target_1y}` : "—")}
                          </td>

                          <td className="px-3 py-2 text-center whitespace-nowrap">
                            {r.target_upside ? (
                              <span className={r.target_upside > 0 ? "text-[#00e5a0]" : "text-[#ff4d6a]"}>
                                {r.target_upside > 0 ? `+${r.target_upside.toFixed(1)}%` : `${r.target_upside.toFixed(1)}%`}
                              </span>
                            ) : "—"}
                          </td>
                          <td className="px-3 py-2 text-center whitespace-nowrap">
                            <span className={`px-2 py-0.5 rounded text-[10px] font-bold ${
                              r.news?.includes("Good") || r.news?.includes("POSITIVE") ? "bg-[#0a3d1f] text-[#00e5a0]" :
                              r.news?.includes("Bad") || r.news?.includes("NEGATIVE") ? "bg-[#3d0a1a] text-[#ff4d6a]" :
                              "text-[#6b7099]"
                            }`}>
                              {r.news ?? "No"}
                            </span>
                          </td>
                          <td className="px-3 py-2 text-center font-bold text-white whitespace-nowrap">
                            {r.expected_wr ? `${r.expected_wr}%` : "—"}
                          </td>
                          <td className="px-3 py-2 text-[#6b7099] text-[10px] max-w-xs truncate">
                            {Array.isArray(r.flags) ? r.flags.join(", ") : r.flags ?? "—"}
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
      )}
    </div>
  );
}

function renderFibTable(rows: any[]) {
  return (
    <div className="overflow-x-auto rounded-lg border border-[#1a1d2e] mb-2">
      <table className="w-full text-left text-xs font-mono">
        <thead className="bg-[#0a0b14] text-[#6b7099] uppercase border-b border-[#1a1d2e]">
          <tr>
            <th className="px-3 py-2">Ticker</th>
            <th className="px-3 py-2">Close</th>
            <th className="px-3 py-2">Earn Zone</th>
            <th className="px-3 py-2">Weekly Zone</th>
            <th className="px-3 py-2">Inst/Retail Control</th>
            {FIB_COLUMNS.map((col) => (
              <th
                key={col}
                className={`px-3 py-2 text-center whitespace-nowrap ${
                  GOLDEN_COLS.has(col) ? "text-[#d4a017] bg-[#1a1500] font-bold border-b-2 border-[#d4a017]": ""
                }`}
              >
                {col}
              </th>
            ))}
            <th className="px-3 py-2">Earnings Bias</th>
            <th className="px-3 py-2">News Sentiment</th>
            <th className="px-3 py-2">Conclusion</th>
          </tr>
        </thead>
        <tbody className="divide-y divide-[#1a1d2e] bg-[#0d0f17]">
          {rows.map((r) => {
            const livePrice = r.close;
            let closestCol = "";
            let minDiff = Infinity;
            if (livePrice && r.fib_all) {
              for (const col of FIB_COLUMNS) {
                const val = r.fib_all[col];
                if (val !== undefined && val !== null) {
                  const diff = Math.abs(val - livePrice);
                  if (diff < minDiff) {
                    minDiff = diff;
                    closestCol = col;
                  }
                }
              }
            }

            return (
              <tr key={r.ticker} className="hover:bg-[#131625]">
                <td className="px-3 py-2 font-bold text-[#4d9fff]">
                  <Link href={`/stock/${r.ticker}`} className="hover:underline">{r.ticker}</Link>
                </td>
                <td className="px-3 py-2 text-white font-bold">${r.close?.toFixed(2)}</td>
                <td className="px-3 py-2">
                  <span className={`px-2 py-0.5 rounded text-[10px] font-bold ${
                    r.earn_zone === "HIGH" ? "bg-[#3d0a1a] text-[#ff4d6a]" :
                    r.earn_zone === "LOW" ? "bg-[#0a3d1f] text-[#00e5a0]" :
                    "bg-[#3d3a0a] text-[#f5c842]"
                  }`}>
                    {r.earn_zone}
                  </span>
                </td>
                <td className="px-3 py-2">
                  <span className={`px-2 py-0.5 rounded text-[10px] font-bold ${
                    r.weekly_zone === "HIGH" ? "bg-[#3d0a1a] text-[#ff4d6a]" :
                    r.weekly_zone === "LOW" ? "bg-[#0a3d1f] text-[#00e5a0]" :
                    "bg-[#3d3a0a] text-[#f5c842]"
                  }`}>
                    {r.weekly_zone}
                  </span>
                </td>
                <td className="px-3 py-2 text-xs text-[#e8ecff] whitespace-nowrap">{r.inst_control}</td>
                {FIB_COLUMNS.map((col) => {
                  const val = r.fib_all?.[col];
                  const isClosest = col === closestCol;
                  const isGolden = GOLDEN_COLS.has(col);
                  return (
                    <td
                      key={col}
                      className={`px-3 py-2 text-center whitespace-nowrap ${
                        isClosest
                          ? "bg-[#0a1f3a] text-white font-black border-2 border-[#4d9fff]"
                          : isGolden
                          ? "bg-[#1a1500] text-[#d4a017] font-semibold"
                          : "text-[#e8ecff]"
                      }`}
                    >
                      {val !== undefined ? `$${Number(val).toFixed(2)}` : "—"}
                    </td>
                  );
                })}
                <td className="px-3 py-2 whitespace-nowrap">
                  <span className={
                    r.earnings_bias?.includes("BULLISH") ? "text-[#00e5a0] font-bold" :
                    r.earnings_bias?.includes("BEARISH") ? "text-[#ff4d6a] font-bold" :
                    "text-[#f5c842]"
                  }>
                    {r.earnings_bias ?? "—"}
                  </span>
                </td>
                <td className="px-3 py-2 whitespace-nowrap">
                  <span className={
                    r.news_sentiment?.includes("POSITIVE") ? "text-[#00e5a0] font-bold" :
                    r.news_sentiment?.includes("NEGATIVE") ? "text-[#ff4d6a] font-bold" :
                    "text-[#f5c842]"
                  }>
                    {r.news_sentiment ?? "—"}
                  </span>
                </td>
                <td className="px-3 py-2 text-xs text-[#6b7099] whitespace-nowrap">{r.conclusion}</td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
