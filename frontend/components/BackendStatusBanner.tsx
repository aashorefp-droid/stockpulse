"use client";

import { useState, useEffect, useCallback } from "react";

const API_BASE = process.env.NEXT_PUBLIC_API_URL || "https://stockpulse-pkpj.onrender.com";
const CHECK_INTERVAL_MS = 45000; // Auto-verify in background every 45s

interface HealthDetails {
  status?: string;
  scheduler?: boolean;
  polling_active?: boolean;
  raw?: any;
}

export default function BackendStatusBanner() {
  const [status, setStatus] = useState<"checking" | "online" | "offline">("checking");
  const [latency, setLatency] = useState<number | null>(null);
  const [lastChecked, setLastChecked] = useState<string | null>(null);
  const [details, setDetails] = useState<HealthDetails | null>(null);
  const [errorMsg, setErrorMsg] = useState<string | null>(null);
  const [isMinimized, setIsMinimized] = useState<boolean>(false);
  const [showDiagnostics, setShowDiagnostics] = useState<boolean>(false);
  const [isPinging, setIsPinging] = useState<boolean>(false);

  const verifyBackend = useCallback(async () => {
    setIsPinging(true);
    const start = performance.now();
    const controller = new AbortController();
    const timeoutId = setTimeout(() => controller.abort(), 12000);

    try {
      const res = await fetch(`${API_BASE}/health`, {
        method: "GET",
        signal: controller.signal,
        cache: "no-store",
      });
      clearTimeout(timeoutId);
      const elapsed = Math.round(performance.now() - start);

      if (res.ok) {
        const json = await res.json().catch(() => ({}));
        setStatus("online");
        setLatency(elapsed);
        setDetails(json);
        setErrorMsg(null);
        setLastChecked(new Date().toLocaleTimeString());
      } else {
        setStatus("offline");
        setErrorMsg(`HTTP ${res.status} ${res.statusText}`);
        setLatency(elapsed);
        setLastChecked(new Date().toLocaleTimeString());
      }
    } catch (err: any) {
      clearTimeout(timeoutId);
      const elapsed = Math.round(performance.now() - start);
      setStatus("offline");
      setLatency(elapsed);
      setLastChecked(new Date().toLocaleTimeString());
      if (err.name === "AbortError") {
        setErrorMsg("Request timed out (Render instance may be waking from cold sleep).");
      } else {
        setErrorMsg(err.message || "Network / CORS connection failed");
      }
    } finally {
      setIsPinging(false);
    }
  }, []);

  useEffect(() => {
    verifyBackend();
    const timer = setInterval(verifyBackend, CHECK_INTERVAL_MS);
    return () => clearInterval(timer);
  }, [verifyBackend]);

  // Collapsed Pill View
  if (isMinimized) {
    return (
      <div className="bg-[#0b0e14] border-b border-border/60 px-4 py-1.5 flex items-center justify-between text-xs font-mono">
        <div className="flex items-center gap-2">
          {status === "online" && (
            <>
              <span className="relative flex h-2 w-2">
                <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-green opacity-75"></span>
                <span className="relative inline-flex rounded-full h-2 w-2 bg-green"></span>
              </span>
              <span className="text-white font-semibold">Backend Live:</span>
              <span className="text-muted">Render ({latency}ms)</span>
              <span className="text-green/80 text-[11px] font-semibold bg-green/10 border border-green/30 px-1.5 py-0.2 rounded">
                Handshake OK
              </span>
            </>
          )}
          {status === "checking" && (
            <>
              <span className="h-2 w-2 rounded-full bg-yellow animate-pulse"></span>
              <span className="text-yellow">Verifying Render Backend...</span>
            </>
          )}
          {status === "offline" && (
            <>
              <span className="h-2 w-2 rounded-full bg-red animate-ping"></span>
              <span className="text-red font-semibold">Backend Offline / Waking Up</span>
              <span className="text-muted text-[11px]">({errorMsg})</span>
            </>
          )}
        </div>
        <div className="flex items-center gap-2">
          <button
            onClick={verifyBackend}
            disabled={isPinging}
            className="text-[11px] px-2 py-0.5 rounded bg-surface border border-border hover:border-accent text-slate-300 hover:text-white transition-colors"
          >
            {isPinging ? "Pinging..." : "⚡ Ping"}
          </button>
          <button
            onClick={() => setIsMinimized(false)}
            className="text-[11px] text-accent hover:underline ml-1"
          >
            Expand Details ▼
          </button>
        </div>
      </div>
    );
  }

  // Full Expanded Banner View
  return (
    <div
      className={`border-b transition-all duration-300 font-mono text-xs ${
        status === "online"
          ? "bg-[#0b1319] border-emerald-900/40 text-emerald-300"
          : status === "checking"
          ? "bg-[#16120b] border-amber-900/40 text-amber-300"
          : "bg-[#180b0e] border-rose-900/40 text-rose-300"
      }`}
    >
      <div className="max-w-screen-2xl mx-auto px-4 py-2 flex flex-wrap items-center justify-between gap-3">
        {/* Left: Status indicator & description */}
        <div className="flex items-center gap-3 flex-wrap">
          {status === "online" && (
            <>
              <span className="flex items-center gap-1.5 bg-emerald-500/10 border border-emerald-500/30 text-emerald-400 font-bold px-2 py-0.5 rounded text-[11px]">
                <span className="relative flex h-2 w-2">
                  <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-emerald-400 opacity-75"></span>
                  <span className="relative inline-flex rounded-full h-2 w-2 bg-emerald-400"></span>
                </span>
                BACKEND VERIFIED
              </span>
              <span className="text-white text-xs">
                Connected to <strong className="text-emerald-400 font-semibold">{API_BASE}</strong>
              </span>
              {latency !== null && (
                <span className="bg-surface/80 border border-border/80 px-2 py-0.5 rounded text-[11px] text-slate-300">
                  ⚡ Latency: <strong className="text-emerald-400">{latency}ms</strong>
                </span>
              )}
              {lastChecked && (
                <span className="text-[11px] text-muted hidden sm:inline">
                  Verified at {lastChecked}
                </span>
              )}
            </>
          )}

          {status === "checking" && (
            <>
              <span className="flex items-center gap-1.5 bg-amber-500/10 border border-amber-500/30 text-amber-400 font-bold px-2 py-0.5 rounded text-[11px]">
                <span className="h-2 w-2 rounded-full bg-amber-400 animate-pulse"></span>
                CHECKING HANDSHAKE...
              </span>
              <span className="text-slate-300 text-xs">
                Pinging Render backend at <span className="font-semibold text-white">{API_BASE}</span>
              </span>
            </>
          )}

          {status === "offline" && (
            <>
              <span className="flex items-center gap-1.5 bg-rose-500/10 border border-rose-500/30 text-rose-400 font-bold px-2 py-0.5 rounded text-[11px]">
                <span className="h-2 w-2 rounded-full bg-rose-500 animate-ping"></span>
                BACKEND UNREACHABLE
              </span>
              <span className="text-slate-300 text-xs">
                Could not connect to <span className="font-semibold text-white">{API_BASE}</span>
                {errorMsg && <span className="text-rose-400 ml-1.5">({errorMsg})</span>}
              </span>
              <span className="text-[11px] text-muted hidden md:inline">
                (Render free tier sleeps after inactivity and takes ~30s to wake up)
              </span>
            </>
          )}
        </div>

        {/* Right: Actions */}
        <div className="flex items-center gap-2 ml-auto">
          <button
            onClick={verifyBackend}
            disabled={isPinging}
            className="flex items-center gap-1.5 bg-surface border border-border hover:border-accent text-slate-200 hover:text-white px-2.5 py-1 rounded transition-colors text-[11px] font-semibold disabled:opacity-50"
            title="Send an immediate health ping to Render backend"
          >
            {isPinging ? (
              <>
                <div className="w-3 h-3 border-2 border-accent border-t-transparent rounded-full animate-spin"></div>
                Pinging...
              </>
            ) : (
              <>
                <span>🔄</span> Test Ping
              </>
            )}
          </button>

          <button
            onClick={() => setShowDiagnostics((prev) => !prev)}
            className="text-[11px] text-muted hover:text-white border border-border/60 px-2 py-1 rounded bg-surface/40 hover:bg-surface transition-colors"
          >
            {showDiagnostics ? "Hide Diagnostics ▲" : "Diagnostics ▼"}
          </button>

          <button
            onClick={() => setIsMinimized(true)}
            className="text-[11px] text-muted hover:text-white px-1.5 py-1 rounded hover:bg-surface transition-colors"
            title="Minimize banner to compact mode"
          >
            ✕
          </button>
        </div>
      </div>

      {/* Diagnostics Drawer */}
      {showDiagnostics && (
        <div className="bg-[#070a0f] border-t border-border/50 px-4 py-3 text-[11px] text-slate-300">
          <div className="max-w-screen-2xl mx-auto grid grid-cols-1 md:grid-cols-4 gap-4">
            <div>
              <div className="text-muted uppercase text-[10px] font-semibold mb-0.5">Target Backend</div>
              <div className="text-white font-mono break-all">{API_BASE}</div>
            </div>
            <div>
              <div className="text-muted uppercase text-[10px] font-semibold mb-0.5">CORS & Handshake</div>
              <div className="text-emerald-400 font-semibold">
                {status === "online" ? "✓ Authorized (regex: *.vercel.app)" : "Pending check"}
              </div>
            </div>
            <div>
              <div className="text-muted uppercase text-[10px] font-semibold mb-0.5">Round-Trip Latency</div>
              <div className="text-white font-mono">{latency ? `${latency} ms` : "—"}</div>
            </div>
            <div>
              <div className="text-muted uppercase text-[10px] font-semibold mb-0.5">Scheduler & State</div>
              <div className="text-white font-mono">
                {details?.status === "ok" ? "Running · Healthy" : "Offline / Unchecked"}
              </div>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
