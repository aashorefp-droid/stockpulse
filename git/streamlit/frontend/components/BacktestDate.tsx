"use client";
import { useRouter, useSearchParams } from "next/navigation";

export default function BacktestDate({ ticker, asOf }: { ticker: string; asOf?: string }) {
  const router = useRouter();
  const searchParams = useSearchParams();

  function setDate(date: string) {
    const params = new URLSearchParams(searchParams.toString());
    if (date) {
      params.set("as_of", date);
    } else {
      params.delete("as_of");
    }
    const qs = params.toString();
    router.push(`/stock/${ticker}${qs ? "?" + qs : ""}`);
  }

  return (
    <div className="flex items-center gap-2">
      <label className="text-[10px] text-muted uppercase tracking-wider">Backtest</label>
      <input
        type="date"
        value={asOf ?? ""}
        max={new Date().toISOString().split("T")[0]}
        onChange={(e) => setDate(e.target.value)}
        className="bg-card border border-border rounded px-2 py-1 text-xs font-mono text-white focus:outline-none focus:border-accent"
      />
      {asOf && (
        <button
          onClick={() => setDate("")}
          className="text-[10px] text-muted hover:text-red transition-colors"
          title="Clear and return to live data"
        >
          ✕
        </button>
      )}
    </div>
  );
}
