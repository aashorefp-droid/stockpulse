"use client";
import { useState } from "react";

const API_BASE = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

export default function NewsPage() {
  const [ticker, setTicker] = useState("NVDA");
  const [data, setData] = useState<any>(null);
  const [loading, setLoading] = useState(false);

  const fetchNews = async () => {
    if (!ticker.trim()) return;
    setLoading(true);
    try {
      const res = await fetch(`${API_BASE}/api/news/ticker/${ticker.trim().toUpperCase()}`);
      const json = await res.json();
      setData(json);
    } catch (err) {
      console.error(err);
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="max-w-4xl mx-auto px-4 py-8">
      <h1 className="text-2xl font-bold text-white flex items-center gap-2 mb-2">
        ?? News Sentiment & Finviz Scraping
      </h1>
      <p className="text-muted text-sm mb-6">
        Batch-scrape Finviz headlines, compute sentiment polarity, and track emerging catalysts.
      </p>

      <div className="flex gap-2 mb-6">
        <input
          className="bg-surface border border-border rounded-lg px-4 py-2 text-white font-mono uppercase w-48 text-sm focus:outline-none focus:border-accent"
          placeholder="Ticker (e.g. NVDA)"
          value={ticker}
          onChange={(e) => setTicker(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && fetchNews()}
        />
        <button
          onClick={fetchNews}
          className="bg-accent text-black font-semibold px-4 py-2 rounded-lg text-sm hover:bg-accent/80 transition-colors"
        >
          {loading ? "Scraping?" : "Analyze Sentiment"}
        </button>
      </div>

      {data && (
        <div className="space-y-6">
          <div className="bg-card border border-border rounded-xl p-6 flex items-center justify-between">
            <div>
              <span className="font-mono font-bold text-2xl text-accent">{data.ticker}</span>
              <div className="text-sm text-muted mt-1">{data.count} recent headlines parsed</div>
            </div>
            <div className="text-right">
              <span className={`text-xl font-bold font-mono ${
                data.label === "BULLISH" ? "text-bull" : data.label === "BEARISH" ? "text-bear" : "text-muted"
              }`}>
                {data.label}
              </span>
              <div className="text-xs text-muted mt-1 font-mono">Score: {data.score}</div>
            </div>
          </div>

          <div className="bg-card border border-border rounded-xl p-6">
            <h2 className="text-base font-semibold text-white mb-4 border-b border-border pb-2">
              Recent Headlines
            </h2>
            <div className="space-y-3">
              {data.headlines && data.headlines.length > 0 ? (
                data.headlines.map((h: any, i: number) => (
                  <div key={i} className="py-2 border-b border-border/40 last:border-0 flex justify-between gap-4 text-sm">
                    <span className="text-white hover:text-accent transition-colors">{h.title || h}</span>
                    {h.time && <span className="text-muted text-xs whitespace-nowrap font-mono">{h.time}</span>}
                  </div>
                ))
              ) : (
                <div className="text-muted text-sm py-4">No recent headlines found.</div>
              )}
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
