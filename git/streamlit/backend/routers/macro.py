"""
GET /api/macro/snapshot
Returns macro market data + risk score + CNN Fear & Greed. Cached server-side for 5 minutes.
"""
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo
from fastapi import APIRouter
import yfinance as yf

from backend.services.gex import compute_spy_gex
from backend.services.analysis import compute_btd

router = APIRouter(prefix="/api/macro", tags=["macro"])

MACRO_INSTRUMENTS = [
    ("SPY",     "S&P 500",    "index"),
    ("QQQ",     "Nasdaq",     "index"),
    ("DIA",     "Dow Jones",  "index"),
    ("IWM",     "Russell 2K", "index"),
    ("^VIX",    "VIX",        "fear"),
    ("GLD",     "Gold",       "commodity"),
    ("SLV",     "Silver",     "commodity"),
    ("USO",     "Oil",        "commodity"),
    ("TLT",     "Bonds 20Y",  "bonds"),
    ("BTC-USD", "Bitcoin",    "crypto"),
    ("XLB",     "Materials",      "sector"),
    ("XLC",     "Comm",           "sector"),
    ("XLE",     "Energy",         "sector"),
    ("XLF",     "Financials",     "sector"),
    ("XLI",     "Industrials",    "sector"),
    ("XLK",     "Tech",           "sector"),
    ("XLP",     "Staples",        "sector"),
    ("XLRE",    "Real Estate",    "sector"),
    ("XLU",     "Utilities",      "sector"),
    ("XLV",     "Health",         "sector"),
    ("XLY",     "Discretionary",  "sector"),
]

_cache: dict = {"data": None, "ts": 0.0}
_CACHE_TTL = 300  # 5 minutes

ECON_EVENTS = [
    # BLS high-impact inflation/jobs releases; FOMC dates from Federal Reserve 2026 calendar.
    {"date": "2026-05-12", "title": "CPI", "time": "08:30 AM ET", "impact": "High", "note": "Consumer Price Index for Apr 2026", "priority": 100},
    {"date": "2026-06-10", "title": "CPI", "time": "08:30 AM ET", "impact": "High", "note": "Consumer Price Index for May 2026", "priority": 100},
    {"date": "2026-07-14", "title": "CPI", "time": "08:30 AM ET", "impact": "High", "note": "Consumer Price Index for Jun 2026", "priority": 100},
    {"date": "2026-08-12", "title": "CPI", "time": "08:30 AM ET", "impact": "High", "note": "Consumer Price Index for Jul 2026", "priority": 100},
    {"date": "2026-09-11", "title": "CPI", "time": "08:30 AM ET", "impact": "High", "note": "Consumer Price Index for Aug 2026", "priority": 100},
    {"date": "2026-10-14", "title": "CPI", "time": "08:30 AM ET", "impact": "High", "note": "Consumer Price Index for Sep 2026", "priority": 100},
    {"date": "2026-11-10", "title": "CPI", "time": "08:30 AM ET", "impact": "High", "note": "Consumer Price Index for Oct 2026", "priority": 100},
    {"date": "2026-12-10", "title": "CPI", "time": "08:30 AM ET", "impact": "High", "note": "Consumer Price Index for Nov 2026", "priority": 100},
    {"date": "2026-05-13", "title": "PPI", "time": "08:30 AM ET", "impact": "High", "note": "Producer Price Index for Apr 2026", "priority": 80},
    {"date": "2026-06-11", "title": "PPI", "time": "08:30 AM ET", "impact": "High", "note": "Producer Price Index for May 2026", "priority": 80},
    {"date": "2026-07-15", "title": "PPI", "time": "08:30 AM ET", "impact": "High", "note": "Producer Price Index for Jun 2026", "priority": 80},
    {"date": "2026-08-13", "title": "PPI", "time": "08:30 AM ET", "impact": "High", "note": "Producer Price Index for Jul 2026", "priority": 80},
    {"date": "2026-09-10", "title": "PPI", "time": "08:30 AM ET", "impact": "High", "note": "Producer Price Index for Aug 2026", "priority": 80},
    {"date": "2026-10-15", "title": "PPI", "time": "08:30 AM ET", "impact": "High", "note": "Producer Price Index for Sep 2026", "priority": 80},
    {"date": "2026-11-13", "title": "PPI", "time": "08:30 AM ET", "impact": "High", "note": "Producer Price Index for Oct 2026", "priority": 80},
    {"date": "2026-12-15", "title": "PPI", "time": "08:30 AM ET", "impact": "High", "note": "Producer Price Index for Nov 2026", "priority": 80},
    {"date": "2026-06-05", "title": "Jobs", "time": "08:30 AM ET", "impact": "High", "note": "Employment Situation for May 2026", "priority": 90},
    {"date": "2026-07-02", "title": "Jobs", "time": "08:30 AM ET", "impact": "High", "note": "Employment Situation for Jun 2026", "priority": 90},
    {"date": "2026-08-07", "title": "Jobs", "time": "08:30 AM ET", "impact": "High", "note": "Employment Situation for Jul 2026", "priority": 90},
    {"date": "2026-09-04", "title": "Jobs", "time": "08:30 AM ET", "impact": "High", "note": "Employment Situation for Aug 2026", "priority": 90},
    {"date": "2026-10-02", "title": "Jobs", "time": "08:30 AM ET", "impact": "High", "note": "Employment Situation for Sep 2026", "priority": 90},
    {"date": "2026-11-06", "title": "Jobs", "time": "08:30 AM ET", "impact": "High", "note": "Employment Situation for Oct 2026", "priority": 90},
    {"date": "2026-12-04", "title": "Jobs", "time": "08:30 AM ET", "impact": "High", "note": "Employment Situation for Nov 2026", "priority": 90},
    {"date": "2026-06-17", "title": "FOMC", "time": "02:00 PM ET", "impact": "High", "note": "Fed statement / rate decision", "priority": 95},
    {"date": "2026-07-29", "title": "FOMC", "time": "02:00 PM ET", "impact": "High", "note": "Fed statement / rate decision", "priority": 95},
    {"date": "2026-09-16", "title": "FOMC", "time": "02:00 PM ET", "impact": "High", "note": "Fed statement / rate decision", "priority": 95},
    {"date": "2026-10-28", "title": "FOMC", "time": "02:00 PM ET", "impact": "High", "note": "Fed statement / rate decision", "priority": 95},
    {"date": "2026-12-09", "title": "FOMC", "time": "02:00 PM ET", "impact": "High", "note": "Fed statement / rate decision", "priority": 95},
]


def _economic_events():
    today = datetime.now(ZoneInfo("America/New_York")).date()
    out = []
    for label, d in (("Today", today), ("Tomorrow", today + timedelta(days=1))):
        matches = [e for e in ECON_EVENTS if e["date"] == d.isoformat()]
        if matches:
            event = sorted(matches, key=lambda e: e["priority"], reverse=True)[0]
            out.append({k: v for k, v in event.items() if k != "priority"} | {"day": label})
        else:
            out.append({
                "day": label,
                "date": d.isoformat(),
                "title": "No major scheduled",
                "time": "",
                "impact": "Low",
                "note": "No CPI, PPI, Employment Situation, or FOMC event scheduled.",
            })
    return out



@router.get("/gex")
def gex_detail():
    """Raw GEX + persistence health check. Use to confirm Postgres is wired."""
    try:
        from backend.db import gex_store
        gex = compute_spy_gex()
        history = gex_store.load_history()
        return {
            "gex": gex,
            "store_backend": gex_store.backend_name(),
            "history_days": len(history),
            "history": dict(sorted(history.items(), reverse=True)),
        }
    except Exception as e:
        return {"error": str(e)}


@router.get("/sector-gex")
def sector_gex():
    """
    Per-sector gamma (XLK/XLF/XLE). Long Gamma = dealers dampen → stable/strong;
    Short Gamma = dealers amplify → volatile/weak. Cached ~30 min server-side
    (heavy: one option chain per sector).
    """
    try:
        from backend.services.gex import compute_sector_gex
        return compute_sector_gex()
    except Exception as e:
        return {"sectors": [], "error": str(e)[:120]}


@router.get("/snapshot")
def macro_snapshot():
    now = time.time()
    if _cache["data"] and now - _cache["ts"] < _CACHE_TTL:
        return _cache["data"]

    tickers = [t for t, *_ in MACRO_INSTRUMENTS]

    # These three calls are independent network I/O. Run them concurrently
    # so cold-cache latency ≈ the slowest one, not their sum.
    def _macro_dl():
        raw = yf.download(tickers, period="30d", interval="1d",
                          auto_adjust=True, progress=False, threads=True)
        return raw["Close"] if "Close" in raw.columns else raw

    def _spy_1y():
        s = yf.Ticker("SPY").history(period="1y", interval="1d", auto_adjust=True)
        if not s.empty:
            s.columns = [c.lower() for c in s.columns]
        return s

    with ThreadPoolExecutor(max_workers=3) as ex:
        f_close = ex.submit(_macro_dl)
        f_gex   = ex.submit(compute_spy_gex)
        f_spy1y = ex.submit(_spy_1y)
        try:
            close = f_close.result()
        except Exception:
            return {"items": [], "risk": {"score": 0, "label": "UNKNOWN", "notes": []},
                    "btd": {"btd_state": "N/A", "btd_reason": "macro fetch failed"}}
        try:
            gex = f_gex.result()
        except Exception:
            gex = {"available": False, "reason": "GEX computation failed"}
        try:
            spy1y = f_spy1y.result()
        except Exception:
            spy1y = None

    items = []
    for ticker, label, category in MACRO_INSTRUMENTS:
        try:
            if ticker not in close.columns:
                continue
            s = close[ticker].dropna()
            if len(s) < 2:
                continue
            price   = float(s.iloc[-1])
            chg_1d  = (price - float(s.iloc[-2]))  / float(s.iloc[-2])  * 100 if len(s) >= 2  else 0.0
            chg_5d  = (price - float(s.iloc[-6]))  / float(s.iloc[-6])  * 100 if len(s) >= 6  else chg_1d
            chg_20d = (price - float(s.iloc[-21])) / float(s.iloc[-21]) * 100 if len(s) >= 21 else chg_5d
            items.append({
                "ticker":   ticker,
                "label":    label,
                "category": category,
                "price":    round(price, 2),
                "chg_1d":   round(chg_1d,  2),
                "chg_5d":   round(chg_5d,  2),
                "chg_20d":  round(chg_20d, 2),
            })
        except Exception:
            continue

    risk_score = 0
    risk_notes: list[str] = []
    spx = next((m for m in items if m["ticker"] == "SPY"),     None)
    vix = next((m for m in items if m["ticker"] == "^VIX"),    None)
    tlt = next((m for m in items if m["ticker"] == "TLT"),     None)
    gld = next((m for m in items if m["ticker"] == "GLD"),     None)

    if vix:
        if vix["price"] > 25:
            risk_score += 2
            risk_notes.append(f"VIX {vix['price']:.0f} — elevated fear")
        elif vix["price"] > 18:
            risk_score += 1
            risk_notes.append(f"VIX {vix['price']:.0f} — mild caution")
        else:
            risk_notes.append(f"VIX {vix['price']:.0f} — calm")
    if spx and spx["chg_5d"] < -2:
        risk_score += 1
        risk_notes.append(f"SPY 5d: {spx['chg_5d']:+.1f}% — market under pressure")
    if tlt and tlt["chg_5d"] > 1:
        risk_score += 1
        risk_notes.append("Bonds rallying — flight to safety")
    if gld and gld["chg_5d"] > 2:
        risk_score += 1
        risk_notes.append(f"Gold 5d: {gld['chg_5d']:+.1f}% — safe haven demand")

    risk_label = "LOW RISK" if risk_score == 0 else ("MODERATE RISK" if risk_score <= 2 else "HIGH RISK")

    # gex was resolved above (computed in parallel with the macro download).

    # ── Market-level Buy-The-Dip (SPY EMA structure + real gamma gate) ──────
    # Regime gate uses real dealer GEX when available:
    #   Long Gamma  → dips mean-revert → BTD armed (if VIX not risk-off)
    #   Short Gamma / Near Flip → trends extend → BTD disarmed
    # Falls back to the VIX risk score when GEX is unavailable.
    gex_regime = (gex or {}).get("regime") if isinstance(gex, dict) else None
    if gex_regime == "Long Gamma":
        regime_ok     = risk_score <= 2
        regime_source = "GEX: Long Gamma + VIX"
    elif gex_regime in ("Short Gamma", "Near Flip"):
        regime_ok     = False
        regime_source = f"GEX: {gex_regime} — BTD off"
    else:
        regime_ok     = risk_score <= 2
        regime_source = "VIX proxy (GEX unavailable)"

    btd: dict = {"btd_state": "N/A", "btd_reason": "SPY history unavailable"}
    try:
        if spy1y is not None and not spy1y.empty:
            btd = compute_btd(spy1y, regime_ok=regime_ok)
    except Exception:
        pass
    btd["regime_ok"]     = regime_ok
    btd["regime_source"] = regime_source
    if isinstance(gex, dict) and gex.get("available"):
        btd["gex_regime"] = gex_regime
        btd["gex_net"]    = gex.get("net_gex")
        btd["gex_streak"] = gex.get("streak")
    if vix:
        btd["vix"] = vix["price"]

    result = {
        "items": items,
        "risk":  {"score": risk_score, "label": risk_label, "notes": risk_notes},
        "economic_events": _economic_events(),
        "econ_refresh_date": datetime.now(ZoneInfo("America/New_York")).date().isoformat(),
        "gex": gex,
        "btd": btd,
    }
    _cache["data"] = result
    _cache["ts"]   = now
    return result
