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

        <div className="flex gap-4 text-sm text-muted hidden sm:flex">
          <Link href="/" className="hover:text-white transition-colors">Dashboard</Link>
          <Link href="/scanner" className="hover:text-white transition-colors">Scanner</Link>
          <Link href="/earnings" className="hover:text-white transition-colors">Earnings</Link>
          <Link href="/post-earnings" className="hover:text-white transition-colors">Post-Earnings</Link>
          <Link href="/tracker" className="hover:text-white transition-colors">Tracker</Link>
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
