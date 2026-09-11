"use client";
import { useState } from "react";
import { useRouter } from "next/navigation";
import Link from "next/link";

export default function Navbar() {
  const [q, setQ] = useState("");
  const router = useRouter();

  const search = () => {
    const t = q.trim().toUpperCase();
    if (t) { router.push(`/stock/${t}`); setQ(""); }
  };

  return (
    <nav className="border-b border-border bg-card sticky top-0 z-50">
      <div className="max-w-screen-2xl mx-auto px-4 h-14 flex items-center gap-6">
        <Link href="/" className="text-accent font-bold text-xl tracking-tight shrink-0">
          StockPulse
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
            className="bg-surface border border-border rounded-lg px-3 py-1.5 text-sm text-white placeholder-muted focus:outline-none focus:border-accent w-40"
            placeholder="Search ticker…"
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
