"use client";
import { useEffect, useState, useCallback } from "react";

const API_BASE = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";
const REFRESH_MS = 5 * 60 * 1000;

interface MacroItem {
  ticker:   string;
  label:    string;
  category: string;
  price:    number;
  chg_1d:   number;
  chg_5d:   number;
  chg_20d:  number;
}

interface FearGreed {
  score:  number | null;
  rating: string;
}

interface EconomicEvent {
  day:    string;
  date:   string;
  title:  string;
  time?:  string;
  impact: string;
  note?:  string;
}

interface GexData {
  available:        boolean;
  spot?:            number;
  net_gex?:         number;
  call_gex?:        number;
  put_gex?:         number;
  regime?:          string;
  action?:          string;
  contracts_used?:  number;
  source?:          string;
  streak?:          number;
  structural?:      boolean;
  regime_kind?:     string;
  store?:           string;
  reason?:          string;
}

interface SectorGex {
  symbol:       string;
  name:         string;
  available?:   boolean;
  spot?:        number | null;
  net_gex?:     number | null;
  regime?:      string | null;     // "Long Gamma" | "Short Gamma" | "Near Flip"
  streak?:      number | null;
  structural?:  boolean | null;
  reason?:      string;
}

interface Btd {
  btd_state:        string;
  btd_zone?:        string | null;
  btd_reason?:      string | null;
  btd_size?:        string | null;
  ema20?:           number | null;
  ema50?:           number | null;
  ema200?:          number | null;
  ema50_slope_pct?: number | null;
  regime_ok?:       boolean;
  regime_source?:   string;
  gex_regime?:      string | null;
  gex_net?:         number | null;
  gex_streak?:      number | null;
  vix?:             number;
}

interface MacroData {
  items:       MacroItem[];
  fear_greed:  FearGreed;
  risk:        { score: number; label: string; notes: string[] };
  economic_events?: EconomicEvent[];
  econ_refresh_date?: string;
  gex?:        GexData;
  btd?:        Btd;
}

const KEY_TICKERS = ["SPY", "QQQ", "^VIX", "GLD", "TLT"];
const CNN_FNG_URL = "https://production.dataviz.cnn.io/index/fearandgreed/graphdata";

function sectorTone(chg1d: number) {
  if (chg1d >= 0.25) return { label: "Green", dot: "bg-green", text: "text-green", border: "border-green/25", bg: "bg-green/5" };
  if (chg1d <= -0.25) return { label: "Red", dot: "bg-red", text: "text-red", border: "border-red/25", bg: "bg-red/5" };
  return { label: "Yellow", dot: "bg-yellow", text: "text-yellow", border: "border-yellow/25", bg: "bg-yellow/5" };
}

function eventTone(event: EconomicEvent) {
  if (event.impact === "High") return "border-yellow/35 bg-yellow/10 text-yellow";
  return "border-border bg-card/60 text-muted";
}

function gammaRegime(vix?: MacroItem, spy?: MacroItem):
  { label: string; action: string; tone: string; reason: string } | null {
  if (!vix || !spy) return null;
  const v   = vix.price;
  const v1d = vix.chg_1d;
  const s1d = spy.chg_1d;
  const s5d = spy.chg_5d;

  // Short gamma: trends amplify — VIX expanding, SPY weak
  if (v > 22 || s5d < -3 || (v1d > 8 && s1d < -0.5)) {
    return {
      label: "Short Gamma",
      action: "Trends extend — ride strength, fade weakness with care",
      tone: "border-red/40 bg-red/10 text-red",
      reason: `VIX ${v.toFixed(1)} (${v1d >= 0 ? "+" : ""}${v1d.toFixed(1)}% 1d) · SPY 5d ${s5d >= 0 ? "+" : ""}${s5d.toFixed(1)}%`,
    };
  }
  // Long gamma: calm, mean-reverting — VIX low, SPY steady
  if (v <= 17 && s5d >= -1 && Math.abs(s1d) < 0.7) {
    return {
      label: "Long Gamma",
      action: "Range bound — sell rallies, buy dips",
      tone: "border-green/40 bg-green/10 text-green",
      reason: `VIX ${v.toFixed(1)} calm · SPY 1d ${s1d >= 0 ? "+" : ""}${s1d.toFixed(1)}% · 5d ${s5d >= 0 ? "+" : ""}${s5d.toFixed(1)}%`,
    };
  }
  // Mixed / near gamma flip
  return {
    label: "Mixed Gamma",
    action: "Near gamma flip — reduce conviction on intraday levels",
    tone: "border-yellow/35 bg-yellow/10 text-yellow",
    reason: `VIX ${v.toFixed(1)} · SPY 1d ${s1d >= 0 ? "+" : ""}${s1d.toFixed(1)}% · 5d ${s5d >= 0 ? "+" : ""}${s5d.toFixed(1)}%`,
  };
}

function opexInfo(today: Date): { tomorrow: boolean; kind: string; tone: string } | null {
  const tmr = new Date(today);
  tmr.setDate(tmr.getDate() + 1);
  if (tmr.getDay() !== 5) return null;
  const dom = tmr.getDate();
  const month = tmr.getMonth();
  const isMonthly = dom >= 15 && dom <= 21;
  const isQuarterly = isMonthly && [2, 5, 8, 11].includes(month);
  if (isQuarterly) return { tomorrow: true, kind: "Quarterly OPEX", tone: "border-red/40 bg-red/10 text-red" };
  if (isMonthly)   return { tomorrow: true, kind: "Monthly OPEX",   tone: "border-orange-400/40 bg-orange-400/10 text-orange-300" };
  return { tomorrow: true, kind: "Weekly OPEX", tone: "border-yellow/35 bg-yellow/10 text-yellow" };
}

export default function MarketRisk() {
  const [data, setData]           = useState<MacroData | null>(null);
  const [updatedAt, setUpdatedAt] = useState<Date | null>(null);
  const [loading, setLoading]     = useState(true);
  const [error, setError]         = useState<string | null>(null);
  const [sectors, setSectors]     = useState<SectorGex[]>([]);

  const refresh = useCallback(async () => {
    try {
      const [macroRes, fngRes, secRes] = await Promise.allSettled([
        fetch(`${API_BASE}/api/macro/snapshot`),
        fetch(CNN_FNG_URL),
        fetch(`${API_BASE}/api/macro/sector-gex`),
      ]);

      if (macroRes.status === "rejected" || !macroRes.value.ok) {
        setError(`API error`);
        return;
      }

      const macro: MacroData = await macroRes.value.json();

      if (fngRes.status === "fulfilled" && fngRes.value.ok) {
        const fng = await fngRes.value.json();
        const fg  = fng?.fear_and_greed ?? {};
        macro.fear_greed = {
          score:  fg.score != null ? Math.round(parseFloat(fg.score)) : null,
          rating: (fg.rating ?? "Unknown").replace(/_/g, " ")
            .replace(/\b\w/g, (c: string) => c.toUpperCase()),
        };
      }

      if (secRes.status === "fulfilled" && secRes.value.ok) {
        try {
          const sj = await secRes.value.json();
          setSectors(Array.isArray(sj?.sectors) ? sj.sectors : []);
        } catch { /* keep prior sectors */ }
      }

      setData(macro);
      setUpdatedAt(new Date());
      setError(null);
    } catch (e: any) {
      setError(e?.message || "Network error");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    refresh();
    const id = setInterval(refresh, REFRESH_MS);
    return () => clearInterval(id);
  }, [refresh]);

  if (loading) {
    return (
      <div className="border-b border-border bg-card/40 px-4 py-2 text-xs text-muted animate-pulse">
        Loading market data…
      </div>
    );
  }

  if (error) return (
    <div className="border-b border-border bg-card/40 px-4 py-2 text-xs text-red/70">
      Market data unavailable — {error}
    </div>
  );

  if (!data) return null;

  const { risk, items, fear_greed, economic_events } = data;

  const fgScore  = fear_greed?.score;
  const fgColor  =
    fgScore == null   ? "text-muted"  :
    fgScore <= 25     ? "text-red"    :
    fgScore <= 40     ? "text-yellow" :
    fgScore >= 75     ? "text-green"  :
    fgScore >= 60     ? "text-green/70" :
                        "text-muted";
  const fgMeter  = fgScore != null ? Math.round(fgScore) : null;

  const riskStyles =
    risk.score === 0 ? "text-green  border-green/40  bg-green/5"  :
    risk.score <= 2  ? "text-yellow border-yellow/40 bg-yellow/5" :
                       "text-red    border-red/40    bg-red/5";

  const riskDot =
    risk.score === 0 ? "bg-green"  :
    risk.score <= 2  ? "bg-yellow" :
                       "bg-red";

  const keyItems = KEY_TICKERS
    .map(t => items.find(i => i.ticker === t))
    .filter(Boolean) as MacroItem[];
  const sectorItems = items.filter(i => i.category === "sector");

  return (
    <div className="border-b border-border bg-card/40 px-4 py-2">
      <div className="max-w-screen-2xl mx-auto flex flex-wrap items-center gap-x-4 gap-y-1">

        {/* Risk badge */}
        <div className={`flex items-center gap-1.5 px-2.5 py-1 rounded border text-[11px] font-bold tracking-wide ${riskStyles}`}>
          <span className={`w-1.5 h-1.5 rounded-full ${riskDot}`} />
          MARKET RISK: {risk.label}
        </div>

        {(() => {
          const opex = opexInfo(new Date());
          if (!opex) return null;
          return (
            <div
              className={`inline-flex items-center gap-1 rounded border px-1.5 py-0.5 text-[10px] font-semibold ${opex.tone}`}
              title={`${opex.kind} tomorrow — expect elevated volatility into the close`}
            >
              <span>⚡</span>
              <span>{opex.kind} TMRW</span>
            </div>
          );
        })()}

        {(() => {
          const gex = data.gex;
          if (gex && gex.available && gex.regime) {
            const tone =
              gex.regime === "Long Gamma"  ? "border-green/40 bg-green/10 text-green" :
              gex.regime === "Short Gamma" ? "border-red/40 bg-red/10 text-red"       :
                                             "border-yellow/35 bg-yellow/10 text-yellow";
            const fmtB = (v?: number) => v == null ? "—" : `${(v / 1e9).toFixed(2)}B`;
            return (
              <div
                className={`inline-flex items-center gap-1 rounded border px-1.5 py-0.5 text-[10px] font-semibold ${tone}`}
                title={
                  `${gex.action}\n` +
                  `Net dealer GEX: $${fmtB(gex.net_gex)} per 1% SPY move\n` +
                  `Calls: $${fmtB(gex.call_gex)} · Puts: $${fmtB(gex.put_gex)}\n` +
                  `Spot: $${gex.spot?.toFixed(2)} · ${gex.contracts_used} contracts (${gex.source ?? "data"} + Black-Scholes)`
                }
              >
                <span>γ</span>
                <span>{gex.regime}</span>
                <span className="font-normal opacity-80">· {gex.action?.split(" — ")[0]}</span>
                {gex.streak != null && gex.streak !== 0 && (
                  <span
                    className={`ml-1 rounded px-1 font-mono ${
                      gex.structural
                        ? (gex.streak > 0 ? "bg-green/25 text-green" : "bg-red/25 text-red")
                        : "bg-border/40 text-muted"
                    }`}
                    title={
                      `${gex.regime_kind}: ${Math.abs(gex.streak)} consecutive ` +
                      `${gex.streak > 0 ? "positive" : "negative"}-GEX day(s)\n` +
                      (gex.structural
                        ? "≥10 days — structural regime, not a dip/blip"
                        : "<10 days — transitional, treat as noise") +
                      `\nStore: ${gex.store ?? "?"}`
                    }
                  >
                    {gex.streak > 0 ? "+" : ""}{gex.streak}d
                    {gex.structural && (gex.streak > 0 ? " 🐂" : " 🐻")}
                  </span>
                )}
              </div>
            );
          }
          // Fallback to heuristic if Alpaca GEX unavailable
          const vix = items.find(i => i.ticker === "^VIX");
          const spy = items.find(i => i.ticker === "SPY");
          const g   = gammaRegime(vix, spy);
          if (!g) return null;
          return (
            <div
              className={`inline-flex items-center gap-1 rounded border px-1.5 py-0.5 text-[10px] font-semibold ${g.tone}`}
              title={`${g.action}\n${g.reason}\n(Heuristic — Alpaca GEX unavailable${gex?.reason ? ": " + gex.reason : ""})`}
            >
              <span>γ</span>
              <span>{g.label}</span>
              <span className="font-normal opacity-80">· {g.action.split(" — ")[0]}</span>
            </div>
          );
        })()}

        {sectors.length > 0 && (
          <div
            className="inline-flex items-center gap-1 text-[10px]"
            title="Sector gamma — Long Gamma = dealers dampen moves (stable/strong); Short Gamma = dealers amplify (volatile/weak). XLK/XLF/XLE, ~30 min cache."
          >
            <span className="font-semibold text-muted">Sectors γ:</span>
            {sectors.map(s => {
              const reg  = s.regime || (s.available === false ? "n/a" : "");
              const tone =
                reg === "Long Gamma"  ? "border-green/40 bg-green/10 text-green"     :
                reg === "Short Gamma" ? "border-red/40 bg-red/10 text-red"           :
                reg === "Near Flip"   ? "border-yellow/35 bg-yellow/10 text-yellow"  :
                                        "border-border/40 bg-card text-muted";
              const tag = reg === "Long Gamma" ? "L" : reg === "Short Gamma" ? "S"
                        : reg === "Near Flip" ? "~" : "·";
              const stk = s.streak ?? 0;
              return (
                <span
                  key={s.symbol}
                  className={`inline-flex items-center gap-0.5 rounded border px-1 py-0.5 font-semibold ${tone}`}
                  title={
                    s.available === false
                      ? `${s.name} (${s.symbol}): unavailable${s.reason ? " — " + s.reason : ""}`
                      : `${s.name} (${s.symbol}) — ${reg}\n` +
                        `Net dealer GEX: ${s.net_gex == null ? "—" : (s.net_gex / 1e9).toFixed(2) + "B"} per 1% move\n` +
                        `Streak: ${stk > 0 ? "+" : ""}${stk}d${s.structural ? " (structural ≥10d)" : ""}`
                  }
                >
                  {s.symbol}<span>{tag}</span>
                  {stk !== 0 && (
                    <span className={`font-mono ${s.structural ? "" : "opacity-70"}`}>
                      {stk > 0 ? "+" : ""}{stk}d
                    </span>
                  )}
                </span>
              );
            })}
          </div>
        )}

        {(() => {
          const btd = data.btd;
          if (!btd || !btd.btd_state) return null;
          const s = btd.btd_state;
          const tone =
            s === "TRIGGER"    ? "border-green/40 bg-green/10 text-green"   :
            s === "ARMED"      ? "border-accent/40 bg-accent/10 text-accent" :
            s === "ARMED-DEEP" ? "border-yellow/35 bg-yellow/10 text-yellow" :
            s === "DISARMED"   ? "border-red/40 bg-red/10 text-red"          :
                                 "border-border bg-card/60 text-muted";
          if (s === "N/A") return null;
          const tip = [
            btd.btd_reason,
            btd.ema20 != null && `SPY EMA 20/50/200: ${btd.ema20} / ${btd.ema50} / ${btd.ema200}`,
            btd.ema50_slope_pct != null && `50EMA slope ${btd.ema50_slope_pct > 0 ? "+" : ""}${btd.ema50_slope_pct}%`,
            btd.regime_source && `Gate: ${btd.regime_source}`,
          ].filter(Boolean).join("\n");
          return (
            <div
              className={`inline-flex items-center gap-1 rounded border px-1.5 py-0.5 text-[10px] font-semibold ${tone}`}
              title={tip || undefined}
            >
              <span>BTD</span>
              <span>{s}</span>
              {btd.btd_zone && (
                <span className="font-normal opacity-80">· {btd.btd_zone}</span>
              )}
              {btd.btd_size && (
                <span className="font-normal opacity-60">· {btd.btd_size}</span>
              )}
            </div>
          );
        })()}

        {/* Risk notes — hidden on mobile */}
        {economic_events && economic_events.length > 0 && (
          <div className="flex items-center gap-1.5 overflow-x-auto whitespace-nowrap text-[10px]">
            <span className="font-semibold uppercase tracking-wide text-muted">Econ</span>
            {economic_events.slice(0, 2).map(event => (
              <span
                key={`${event.day}-${event.date}`}
                className={`inline-flex items-center gap-1 rounded border px-1.5 py-0.5 ${eventTone(event)}`}
                title={`${event.day} ${event.date}: ${event.note ?? event.title}${event.time ? ` at ${event.time}` : ""}. Daily refresh.`}
              >
                <span className={`h-1.5 w-1.5 rounded-full ${event.impact === "High" ? "bg-yellow" : "bg-muted"}`} />
                <span className="font-semibold">{event.day}</span>
                <span>{event.title === "No major scheduled" ? "No major" : event.title}</span>
                {event.time && <span className="text-muted">{event.time.replace(" AM ET", "a").replace(" PM ET", "p")}</span>}
              </span>
            ))}
          </div>
        )}

        {risk.notes.length > 0 && (
          <div className="hidden md:flex items-center gap-1 text-[11px] text-muted">
            {risk.notes.map((n, i) => (
              <span key={i}>
                {i > 0 && <span className="mx-1 opacity-40">·</span>}
                {n}
              </span>
            ))}
          </div>
        )}

        {sectorItems.length > 0 && (
          <div className="w-full overflow-x-auto">
            <div className="flex items-center gap-1.5 whitespace-nowrap text-[10px]">
              <span className="mr-1 font-semibold uppercase tracking-wide text-muted">Sector 1D</span>
              {sectorItems.map(item => {
                const tone = sectorTone(item.chg_1d);
                const sign = item.chg_1d > 0 ? "+" : "";
                return (
                  <span
                    key={item.ticker}
                    className={`inline-flex items-center gap-1 rounded border px-1.5 py-0.5 ${tone.border} ${tone.bg}`}
                    title={`${item.label} (${item.ticker}) ${sign}${item.chg_1d.toFixed(2)}% 1D`}
                  >
                    <span className={`h-1.5 w-1.5 rounded-full ${tone.dot}`} />
                    <span className="text-muted">{item.label}</span>
                    <span className={`font-semibold ${tone.text}`}>
                      {sign}{item.chg_1d.toFixed(1)}%
                    </span>
                  </span>
                );
              })}
            </div>
          </div>
        )}

        {/* CNN Fear & Greed */}
        {fgMeter != null && (
          <div className="flex items-center gap-2 px-2.5 py-1 rounded border border-border bg-card/60">
            <div className="flex flex-col leading-tight">
              <span className="text-[9px] text-muted uppercase tracking-wider">CNN F&G</span>
              <span className={`text-[13px] font-mono font-bold ${fgColor}`}>{fgMeter}</span>
            </div>
            <div className="flex flex-col leading-tight">
              <span className={`text-[10px] font-semibold ${fgColor}`}>{fear_greed.rating}</span>
              <div className="w-16 h-1.5 bg-border rounded-full overflow-hidden mt-0.5">
                <div
                  className={`h-full rounded-full transition-all ${
                    fgMeter <= 25 ? "bg-red" : fgMeter <= 40 ? "bg-yellow" : fgMeter >= 60 ? "bg-green" : "bg-muted"
                  }`}
                  style={{ width: `${fgMeter}%` }}
                />
              </div>
            </div>
          </div>
        )}

        {/* Key tickers — pushed to the right */}
        <div className="flex items-center gap-4 ml-auto">
          {keyItems.map(item => {
            const pos  = item.chg_1d > 0;
            const neg  = item.chg_1d < 0;
            const col  = pos ? "text-green" : neg ? "text-red" : "text-muted";
            const sign = pos ? "+" : "";
            return (
              <div key={item.ticker} className="flex flex-col items-end leading-tight">
                <span className="text-[9px] text-muted uppercase tracking-wider">{item.label}</span>
                <span className="text-[12px] font-mono font-semibold text-white">
                  {item.ticker === "^VIX"
                    ? item.price.toFixed(2)
                    : `$${item.price.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`}
                </span>
                <span className={`text-[10px] font-mono ${col}`}>
                  {sign}{item.chg_1d.toFixed(2)}%
                </span>
              </div>
            );
          })}
        </div>

        {/* Last updated */}
        {updatedAt && (
          <div className="flex flex-col items-end leading-tight">
            <span className="text-[9px] text-muted uppercase tracking-wider">Updated</span>
            <span className="text-[11px] text-white font-mono tabular-nums">
              {updatedAt.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}
            </span>
            <span className="text-[9px] text-muted/60">refresh in 5min</span>
          </div>
        )}
      </div>
    </div>
  );
}
