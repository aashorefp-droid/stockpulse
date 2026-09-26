"use client";
import React, { useEffect, useState, useMemo } from "react";

const API_BASE = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

interface GrowthRow {
  Ticker: string;
  Price: string;
  "Market Cap": string;
  Sector?: string;
  "Rev Growth%": string;
  "EPS QoQ%": string;
  "Analyst Target": string;
  "Upside%": string;
  "4W Mom%": string;
  "Vol Ratio": string;
  "Weekly Zone": string;
  "Wk Pos%": string;
  Score: number;
  _score_raw?: number;
}

interface GrowthScanResponse {
  status: string;
  as_of_date: string;
  min_score: number;
  top_n: number;
  by_sector: Record<string, GrowthRow[]>;
  top_picks: GrowthRow[];
  total_scanned: number;
}

export default function GrowthScannerPage() {
  const [availableSectors, setAvailableSectors] = useState<Record<string, string[]>>({});
  const [selectedSectors, setSelectedSectors] = useState<string[]>([]);
  const [asOfDate, setAsOfDate] = useState(() => new Date().toISOString().split("T")[0]);
  const [minScore, setMinScore] = useState<number>(40);
  const [topN, setTopN] = useState<number>(10);
  const [loading, setLoading] = useState(false);
  const [scanResult, setScanResult] = useState<GrowthScanResponse | null>(null);

  useEffect(() => {
    fetch(`${API_BASE}/api/growth/sectors`)
      .then((res) => res.json())
      .then((data) => {
        if (data.sectors) {
          setAvailableSectors(data.sectors);
          setSelectedSectors(Object.keys(data.sectors));
        }
      })
      .catch(() => {});
  }, []);

  const handleToggleSector = (sec: string) => {
    setSelectedSectors((prev) =>
      prev.includes(sec) ? prev.filter((s) => s !== sec) : [...prev, sec]
    );
  };

  const handleSelectAllSectors = () => {
    setSelectedSectors(Object.keys(availableSectors));
  };

  const handleClearAllSectors = () => {
    setSelectedSectors([]);
  };

  const handleScan = async () => {
    if (selectedSectors.length === 0) {
      alert("Please select at least one sector to scan.");
      return;
    }
    setLoading(true);
    try {
      const res = await fetch(`${API_BASE}/api/growth/scan`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          sectors: selectedSectors,
          as_of_date: asOfDate,
          min_score: minScore,
          top_n: topN,
        }),
      });
      if (res.ok) {
        const data = await res.json();
        setScanResult(data);
      } else {
        alert("Growth scan failed. Please check backend server logs.");
      }
    } catch (e: any) {
      alert(`Network error: ${e.message}`);
    } finally {
      setLoading(false);
    }
  };

  const getScoreStyle = (s: number) => {
    if (s >= 70) return "bg-bull/20 text-bull border border-bull/30";
    if (s >= 50) return "bg-amber-500/20 text-amber-300 border border-amber-500/30";
    return "bg-bear/20 text-bear border border-bear/30";
  };

  const getZoneStyle = (z: string) => {
    if (z === "HIGH") return "bg-bear/20 text-bear border border-bear/30";
    if (z === "LOW") return "bg-bull/20 text-bull border border-bull/30";
    if (z === "MID") return "bg-amber-500/20 text-amber-300 border border-amber-500/30";
    return "text-muted";
  };

  const getUpsideColor = (upStr: string) => {
    const num = parseFloat(upStr.replace("%", "").replace("+", "").trim());
    if (isNaN(num)) return "text-muted";
    if (num >= 20) return "text-bull font-bold";
    if (num >= 5) return "text-teal-300 font-semibold";
    if (num < 0) return "text-bear font-semibold";
    return "text-white";
  };

  const handleDownloadCSV = () => {
    if (!scanResult || scanResult.top_picks.length === 0) return;
    const headers = [
      "Ticker", "Price", "Market Cap", "Score", "Rev Growth%",
      "EPS QoQ%", "Analyst Target", "Upside%", "4W Mom%", "Vol Ratio", "Weekly Zone", "Wk Pos%"
    ];
    const rows = scanResult.top_picks.map((r) => [
      r.Ticker, r.Price, r["Market Cap"], r.Score, r["Rev Growth%"],
      r["EPS QoQ%"], r["Analyst Target"], r["Upside%"], r["4W Mom%"], r["Vol Ratio"], r["Weekly Zone"], r["Wk Pos%"]
    ]);
    const csvContent = [headers.join(","), ...rows.map((e) => e.join(","))].join("\n");
    const blob = new Blob([csvContent], { type: "text/csv;charset=utf-8;" });
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.setAttribute("href", url);
    link.setAttribute("download", `growth_scan_${asOfDate}.csv`);
    document.body.appendChild(link);
    link.click();
    document.body.removeChild(link);
  };

  return (
    <div className="max-w-screen-2xl mx-auto px-4 py-8 space-y-6">
      {/* ── HEADER BANNER (lines 7619-7630) ─────────────────────────── */}
      <div className="bg-gradient-to-r from-card to-surface border border-border rounded-xl p-5 shadow-sm">
        <div className="flex items-center gap-4">
          <div className="text-3xl">🚀</div>
          <div>
            <h1 className="text-xl font-bold text-white tracking-tight">Future Growth Scanner</h1>
            <p className="text-muted text-xs mt-0.5">
              Scans high-potential growth sectors: AI/ML, Quantum, Space, Biotech, Defense, Clean Energy, Longevity by fundamental acceleration and analyst targets.
            </p>
          </div>
        </div>
      </div>

      {/* ── CONTROLS (lines 7631-7647) ──────────────────────────────── */}
      <div className="bg-card border border-border rounded-xl p-5 space-y-4 shadow-sm">
        <div className="space-y-2">
          <div className="flex items-center justify-between">
            <label className="text-xs font-bold text-white uppercase tracking-wider">
              Sectors to scan ({selectedSectors.length}/{Object.keys(availableSectors).length})
            </label>
            <div className="flex gap-3 text-xs">
              <button onClick={handleSelectAllSectors} className="text-accent hover:underline">
                Select All
              </button>
              <span className="text-muted">·</span>
              <button onClick={handleClearAllSectors} className="text-muted hover:text-white">
                Clear All
              </button>
            </div>
          </div>

          <div className="flex flex-wrap gap-2 pt-1">
            {Object.keys(availableSectors).map((sec) => {
              const active = selectedSectors.includes(sec);
              return (
                <button
                  key={sec}
                  onClick={() => handleToggleSector(sec)}
                  className={`px-3 py-1.5 rounded-lg text-xs font-semibold transition-all border ${
                    active
                      ? "bg-accent/20 border-accent text-white shadow-sm"
                      : "bg-surface border-border text-muted hover:text-white hover:border-border/80"
                  }`}
                >
                  {sec} ({availableSectors[sec]?.length || 0})
                </button>
              );
            })}
          </div>
        </div>

        <div className="grid grid-cols-1 sm:grid-cols-2 md:grid-cols-4 gap-4 items-end pt-3 border-t border-border">
          <div>
            <label className="text-xs text-muted block mb-1">As Of Date (backdating supported)</label>
            <input
              type="date"
              value={asOfDate}
              onChange={(e) => setAsOfDate(e.target.value)}
              className="w-full bg-surface border border-border rounded-lg px-3 py-1.5 text-xs text-white font-mono focus:outline-none focus:border-accent"
            />
          </div>

          <div>
            <label className="text-xs text-muted block mb-1">Min Score: {minScore}</label>
            <input
              type="range"
              min={0}
              max={100}
              value={minScore}
              onChange={(e) => setMinScore(parseInt(e.target.value))}
              className="w-full accent-accent"
            />
          </div>

          <div>
            <label className="text-xs text-muted block mb-1">Top N per sector (0 = all)</label>
            <input
              type="number"
              min={0}
              max={50}
              value={topN}
              onChange={(e) => setTopN(parseInt(e.target.value) || 0)}
              className="w-full bg-surface border border-border rounded-lg px-3 py-1.5 text-xs text-white font-mono focus:outline-none focus:border-accent"
            />
          </div>

          <button
            onClick={handleScan}
            disabled={loading}
            className="w-full bg-gradient-to-r from-accent to-blue-600 hover:from-accent/90 hover:to-blue-600/90 text-white text-xs font-bold px-6 py-2.5 rounded-lg shadow-md transition-all flex items-center justify-center gap-2 disabled:opacity-50"
          >
            {loading ? (
              <>
                <span className="animate-spin">🔄</span> Screening Tickers...
              </>
            ) : (
              <>🔍 SCAN GROWTH SECTORS</>
            )}
          </button>
        </div>
      </div>

      {/* ── UN-SCANNED STATE (lines 7648-7660) ───────────────────────── */}
      {!scanResult && !loading && (
        <div className="space-y-6">
          <div className="text-center py-8">
            <div className="text-5xl opacity-20 mb-2">🚀</div>
            <div className="text-muted text-sm">
              Select sectors and click <span className="text-accent font-bold">🔍 SCAN GROWTH SECTORS</span>
            </div>
          </div>

          <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
            {Object.entries(availableSectors).map(([sec, tks]) => (
              <div key={sec} className="bg-card border border-border rounded-lg p-3 space-y-1 text-xs">
                <div className="font-bold text-white flex items-center justify-between">
                  <span>{sec}</span>
                  <span className="text-muted text-[11px] font-mono">{tks.length} constituents</span>
                </div>
                <div className="text-accent font-mono text-[11px] truncate">
                  {tks.join(", ")}
                </div>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* ── SCANNED STATE: PER SECTOR & TOP PICKS ────────────────────── */}
      {scanResult && (
        <div className="space-y-8">
          {/* ── 🏆 TOP GROWTH PICKS ACROSS ALL SECTORS (lines 7711-7725) ── */}
          {scanResult.top_picks.length > 0 && (
            <div className="bg-card border border-border rounded-xl p-5 space-y-4 shadow-sm">
              <div className="flex items-center justify-between border-b border-border pb-3">
                <h2 className="text-base font-bold text-white flex items-center gap-2">
                  🏆 Top Growth Picks Across All Sectors ({scanResult.top_picks.length})
                </h2>
                <button
                  onClick={handleDownloadCSV}
                  className="text-xs font-semibold text-accent hover:underline flex items-center gap-1"
                >
                  📥 Download Top Picks CSV
                </button>
              </div>

              <div className="overflow-x-auto border border-border rounded-lg">
                <table className="w-full text-left text-xs whitespace-nowrap">
                  <thead className="bg-surface text-muted text-[10px] uppercase border-b border-border">
                    <tr>
                      <th className="px-3 py-2.5">Rank</th>
                      <th className="px-3 py-2.5">Ticker</th>
                      <th className="px-3 py-2.5">Price</th>
                      <th className="px-3 py-2.5">Market Cap</th>
                      <th className="px-3 py-2.5">Score</th>
                      <th className="px-3 py-2.5">Rev Growth</th>
                      <th className="px-3 py-2.5">EPS QoQ</th>
                      <th className="px-3 py-2.5">Analyst Target</th>
                      <th className="px-3 py-2.5">Upside</th>
                      <th className="px-3 py-2.5">4W Mom</th>
                      <th className="px-3 py-2.5">Vol Ratio</th>
                      <th className="px-3 py-2.5">Weekly Zone</th>
                      <th className="px-3 py-2.5">Wk Pos</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-border">
                    {scanResult.top_picks.map((r, i) => (
                      <tr key={r.Ticker} className="hover:bg-surface/50">
                        <td className="px-3 py-2.5 font-mono text-muted text-xs">#{i + 1}</td>
                        <td className="px-3 py-2.5 font-mono font-bold text-accent">{r.Ticker}</td>
                        <td className="px-3 py-2.5 font-mono font-bold text-white">{r.Price}</td>
                        <td className="px-3 py-2.5 font-mono text-muted">{r["Market Cap"]}</td>
                        <td className="px-3 py-2.5">
                          <span className={`px-2 py-0.5 rounded text-[10px] font-bold font-mono ${getScoreStyle(r.Score)}`}>
                            {r.Score}
                          </span>
                        </td>
                        <td className="px-3 py-2.5 font-mono text-white">{r["Rev Growth%"]}</td>
                        <td className="px-3 py-2.5 font-mono text-muted">{r["EPS QoQ%"]}</td>
                        <td className="px-3 py-2.5 font-mono text-muted">{r["Analyst Target"]}</td>
                        <td className={`px-3 py-2.5 font-mono ${getUpsideColor(r["Upside%"])}`}>
                          {r["Upside%"]}
                        </td>
                        <td className="px-3 py-2.5 font-mono text-muted">{r["4W Mom%"]}</td>
                        <td className="px-3 py-2.5 font-mono text-muted">{r["Vol Ratio"]}</td>
                        <td className="px-3 py-2.5">
                          <span className={`px-2 py-0.5 rounded text-[10px] font-bold ${getZoneStyle(r["Weekly Zone"])}`}>
                            {r["Weekly Zone"]}
                          </span>
                        </td>
                        <td className="px-3 py-2.5 font-mono text-white">{r["Wk Pos%"]}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          )}

          {/* ── PER-SECTOR BREAKDOWNS (lines 7666-7708) ──────────────── */}
          <div className="space-y-6">
            {Object.entries(scanResult.by_sector).map(([secName, rows]) => (
              <div key={secName} className="bg-card border border-border rounded-xl p-5 space-y-3 shadow-sm">
                <div className="flex items-center justify-between border-b border-border pb-2">
                  <h3 className="text-sm font-bold text-white flex items-center gap-2">
                    <span>{secName}</span>
                    <span className="text-xs text-muted font-normal">
                      ({rows.length} qualifying setup{rows.length !== 1 ? "s" : ""})
                    </span>
                  </h3>
                </div>

                {rows.length === 0 ? (
                  <div className="text-xs text-muted py-4 text-center">
                    No tickers met the minimum score of {scanResult.min_score} in this sector.
                  </div>
                ) : (
                  <div className="overflow-x-auto border border-border rounded-lg">
                    <table className="w-full text-left text-xs whitespace-nowrap">
                      <thead className="bg-surface text-muted text-[10px] uppercase border-b border-border">
                        <tr>
                          <th className="px-3 py-2.5">Ticker</th>
                          <th className="px-3 py-2.5">Price</th>
                          <th className="px-3 py-2.5">Market Cap</th>
                          <th className="px-3 py-2.5">Score</th>
                          <th className="px-3 py-2.5">Rev Growth</th>
                          <th className="px-3 py-2.5">EPS QoQ</th>
                          <th className="px-3 py-2.5">Analyst Target</th>
                          <th className="px-3 py-2.5">Upside</th>
                          <th className="px-3 py-2.5">4W Mom</th>
                          <th className="px-3 py-2.5">Vol Ratio</th>
                          <th className="px-3 py-2.5">Weekly Zone</th>
                          <th className="px-3 py-2.5">Wk Pos</th>
                        </tr>
                      </thead>
                      <tbody className="divide-y divide-border">
                        {rows.map((r) => (
                          <tr key={r.Ticker} className="hover:bg-surface/50">
                            <td className="px-3 py-2.5 font-mono font-bold text-accent">{r.Ticker}</td>
                            <td className="px-3 py-2.5 font-mono font-bold text-white">{r.Price}</td>
                            <td className="px-3 py-2.5 font-mono text-muted">{r["Market Cap"]}</td>
                            <td className="px-3 py-2.5">
                              <span className={`px-2 py-0.5 rounded text-[10px] font-bold font-mono ${getScoreStyle(r.Score)}`}>
                                {r.Score}
                              </span>
                            </td>
                            <td className="px-3 py-2.5 font-mono text-white">{r["Rev Growth%"]}</td>
                            <td className="px-3 py-2.5 font-mono text-muted">{r["EPS QoQ%"]}</td>
                            <td className="px-3 py-2.5 font-mono text-muted">{r["Analyst Target"]}</td>
                            <td className={`px-3 py-2.5 font-mono ${getUpsideColor(r["Upside%"])}`}>
                              {r["Upside%"]}
                            </td>
                            <td className="px-3 py-2.5 font-mono text-muted">{r["4W Mom%"]}</td>
                            <td className="px-3 py-2.5 font-mono text-muted">{r["Vol Ratio"]}</td>
                            <td className="px-3 py-2.5">
                              <span className={`px-2 py-0.5 rounded text-[10px] font-bold ${getZoneStyle(r["Weekly Zone"])}`}>
                                {r["Weekly Zone"]}
                              </span>
                            </td>
                            <td className="px-3 py-2.5 font-mono text-white">{r["Wk Pos%"]}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                )}
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
