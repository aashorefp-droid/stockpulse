"""
GET /api/macro/snapshot
Returns macro market data + risk score. Cached server-side for 5 minutes.
"""
import time
from fastapi import APIRouter
import yfinance as yf

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
]

_cache: dict = {"data": None, "ts": 0.0}
_CACHE_TTL = 300  # 5 minutes


@router.get("/snapshot")
def macro_snapshot():
    now = time.time()
    if _cache["data"] and now - _cache["ts"] < _CACHE_TTL:
        return _cache["data"]

    tickers = [t for t, *_ in MACRO_INSTRUMENTS]
    try:
        raw  = yf.download(tickers, period="30d", interval="1d",
                           auto_adjust=True, progress=False, threads=True)
        close = raw["Close"] if "Close" in raw.columns else raw
    except Exception:
        return {"items": [], "risk": {"score": 0, "label": "UNKNOWN", "notes": []}}

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

    result = {
        "items": items,
        "risk": {"score": risk_score, "label": risk_label, "notes": risk_notes},
    }
    _cache["data"] = result
    _cache["ts"]   = now
    return result
