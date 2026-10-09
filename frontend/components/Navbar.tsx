"use client";
import { useState, useEffect } from "react";
import { useRouter } from "next/navigation";
import Link from "next/link";

export default function Navbar() {
  const [q, setQ] = useState("");
  const [timeStr, setTimeStr] = useState<string>("");
  const [marketOpen, setMarketOpen] = useState<boolean>(false);
  const router = useRouter();

  useEffect(() => {
    const updateTime = () => {
      const now = new Date();
      const datePart = now.toLocaleDateString("en-US", {
        timeZone: "America/Chicago",
        weekday: "short",
        month: "short",
        day: "numeric",
      });
      const timePart = now.toLocaleTimeString("en-US", {
        timeZone: "America/Chicago",
        hour: "numeric",
        minute: "2-digit",
        second: "2-digit",
        hour12: true,
      });
      setTimeStr(`${datePart} · ${timePart} CT`);

      // Determine US market hours: Mon-Fri, 8:30 AM - 3:00 PM CT
      try {
        const cstStr = now.toLocaleString("en-US", { timeZone: "America/Chicago" });
        const cstDate = new Date(cstStr);
        const day = cstDate.getDay();
        const isWeekday = day >= 1 && day <= 5;
        const mins = cstDate.getHours() * 60 + cstDate.getMinutes();
        setMarketOpen(isWeekday && mins >= 510 && mins < 900);
      } catch {
        setMarketOpen(false);
      }
    };

    updateTime();
    const interval = setInterval(updateTime, 1000);
    return () => clearInterval(interval);
  }, []);

  const search = () => {
    const raw = q.trim();
    if (!raw) return;
    const parts = raw.split(/[\s,]+/);
    const t = parts[0].toUpperCase();
    const dateMatch = parts[1] && /^\d{4}-\d{2}-\d{2}$/.test(parts[1]) ? parts[1] : "";
    if (t) {
      if (dateMatch) {
        router.push(`/stock/${t}?as_of=${dateMatch}`);
      } else {
        router.push(`/stock/${t}`);
      }
      setQ("");
    }
  };

  return (
    <nav className="border-b border-border bg-card sticky top-0 z-50">
      <div className="max-w-screen-2xl mx-auto px-4 h-14 flex items-center gap-6">
        <Link href="/" className="text-accent font-bold tracking-tight shrink-0 flex flex-col justify-center leading-tight py-0.5 group">
          <span className="text-lg lg:text-xl font-bold group-hover:text-accent/80 transition-colors">
            StockPulse
          </span>
          {timeStr && (
            <span className="text-[10px] text-muted font-mono font-normal tracking-normal flex items-center gap-1 mt-0.5 whitespace-nowrap">
              <span
                className={`w-1.5 h-1.5 rounded-full ${marketOpen ? "bg-emerald-400 animate-pulse" : "bg-muted/50"}`}
                title={marketOpen ? "Market Open (US Equities)" : "Market Closed"}
              />
              {timeStr}
            </span>
          )}
        </Link>

        <div className="flex gap-4 text-xs lg:text-sm text-muted hidden md:flex items-center overflow-x-auto">
          <Link href="/" className="hover:text-white transition-colors whitespace-nowrap">Dashboard</Link>
          <Link href="/stock-analysis" className="text-accent font-semibold hover:text-white transition-colors whitespace-nowrap">🔬 Stock Analysis</Link>
          <Link href="/stock/SPY" className="hover:text-white transition-colors whitespace-nowrap">Chart & Deep Dive</Link>
          <Link href="/scanner" className="hover:text-white transition-colors whitespace-nowrap">Strategy Scanner</Link>
          <Link href="/sector" className="hover:text-white transition-colors whitespace-nowrap">Sectors</Link>
          <Link href="/bubble" className="hover:text-white transition-colors whitespace-nowrap">Bubble</Link>
          <Link href="/tos" className="hover:text-white transition-colors whitespace-nowrap">TOS Scan</Link>
          <Link href="/growth" className="hover:text-white transition-colors whitespace-nowrap">Growth</Link>
          <Link href="/plan" className="hover:text-white transition-colors whitespace-nowrap">Trade Plan</Link>
          <Link href="/paper" className="hover:text-white transition-colors whitespace-nowrap">Paper Trading</Link>
          <Link href="/holdings" className="hover:text-white transition-colors whitespace-nowrap">Holdings</Link>
          <Link href="/trades" className="hover:text-white transition-colors whitespace-nowrap">Trades</Link>
          <Link href="/news" className="hover:text-white transition-colors whitespace-nowrap">News</Link>
          <Link href="/earnings" className="hover:text-white transition-colors whitespace-nowrap">Earnings</Link>
        </div>

        <div className="ml-auto flex gap-2">
          <input
            className="bg-surface border border-border rounded-lg px-3 py-1.5 text-sm text-white placeholder-muted focus:outline-none focus:border-accent w-48 lg:w-64"
            placeholder="Search ticker (e.g. AAPL 2024-05-15)…"
            value={q}
            onChange={(e) => setQ(e.target.value.toUpperCase())}
            onKeyDown={(e) => e.key === "Enter" && search()}
          />
          <button
            onClick={search}
            className="bg-accent text-black text-sm font-semibold px-3 py-1.5 rounded-lg hover:bg-accent/80"
          >
            Go
          </button>
        </div>
      </div>
    </nav>
  );
}
