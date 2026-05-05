"use client";
import { useState } from "react";
import { useRouter } from "next/navigation";

export default function Home() {
  const [ticker, setTicker] = useState("");
  const router = useRouter();

  const go = () => {
    const t = ticker.trim().toUpperCase();
    if (t) router.push(`/stock/${t}`);
  };

  return (
    <div className="flex flex-col items-center justify-center min-h-[70vh] gap-8">
      <div className="text-center">
        <h1 className="text-5xl font-bold text-accent mb-3">StockPulse</h1>
        <p className="text-muted text-lg">Multi-timeframe analysis · Earnings backtest · Options bias</p>
      </div>

      <div className="flex gap-3 w-full max-w-md">
        <input
          className="flex-1 bg-card border border-border rounded-lg px-4 py-3 text-white placeholder-muted focus:outline-none focus:border-accent text-lg uppercase"
          placeholder="Enter ticker (e.g. AAPL)"
          value={ticker}
          onChange={(e) => setTicker(e.target.value.toUpperCase())}
          onKeyDown={(e) => e.key === "Enter" && go()}
          autoFocus
        />
        <button
          onClick={go}
          className="bg-accent text-black font-semibold px-6 py-3 rounded-lg hover:bg-accent/80 transition-colors"
        >
          Analyze
        </button>
      </div>

      <div className="grid grid-cols-3 gap-4 w-full max-w-lg mt-4">
        {["AAPL","TSLA","NVDA","SPY","MSFT","AMZN"].map((t) => (
          <button
            key={t}
            onClick={() => router.push(`/stock/${t}`)}
            className="card hover:border-accent transition-colors text-accent font-mono font-semibold text-center py-2"
          >
            {t}
          </button>
        ))}
      </div>
    </div>
  );
}
