"""
Real Gamma Exposure (GEX) for SPY using Alpaca options snapshots + Black-Scholes gamma.

Convention used here (SpotGamma-style for index ETFs):
  - Dealers are net LONG calls (retail sells covered calls / vol) → call OI contributes POSITIVE dealer gamma
  - Dealers are net SHORT puts (retail buys puts as hedges)        → put  OI contributes NEGATIVE dealer gamma
  - Net dealer GEX = Σ(call γ × OI) - Σ(put γ × OI), scaled by S² × 100 × 0.01 = S² dollars per 1% spot move

Positive GEX → dealers dampen moves   (Long Gamma regime)
Negative GEX → dealers amplify moves  (Short Gamma regime)
"""
import logging
import math
import time
from datetime import date, datetime, timedelta
from typing import Optional
import requests

from backend.config import ALPACA_API_KEY, ALPACA_API_SECRET, ALPACA_DATA_BASE
from backend.db import gex_store

logger = logging.getLogger(__name__)


_cache: dict = {}            # {symbol: {"data": ..., "ts": ...}}
_CACHE_TTL = 300             # SPY — matches macro cache (5 min)
_SECTOR_CACHE_TTL = 1800     # sectors — 30 min (Alpaca/yf chains are heavy)

_STRUCTURAL_THRESHOLD = 10  # consecutive same-sign days = regime, not a dip


def _record_and_streak(net_gex: float, symbol: str = "SPY") -> dict:
    """Persist today's GEX sign, return the signed consecutive-day streak.

    SPY uses the original gex_history table (unchanged); any other symbol
    uses the per-symbol gex_history_sym table.
    """
    sign = 1 if net_gex > 0 else (-1 if net_gex < 0 else 0)
    try:
        if symbol == "SPY":
            gex_store.record_sign(sign)
            hist = gex_store.load_history()
        else:
            gex_store.record_sign_sym(symbol, sign)
            hist = gex_store.load_history_sym(symbol)
    except Exception as e:
        # DB unreachable — degrade gracefully, just report today. Loud on
        # purpose: this is exactly what pins the streak at ±1 forever
        # (e.g. DATABASE_URL unset → ephemeral SQLite wiped on each deploy,
        # or Neon connection failing). Don't let it fail silently.
        logger.warning(
            "[gex] %s history store unreachable (%s) — streak degraded to "
            "single day; check DATABASE_URL / Neon. err=%s",
            symbol, gex_store.backend_name(), str(e)[:160],
        )
        hist = {date.today().isoformat(): sign}

    streak = 0
    last_sign = None
    for d in sorted(hist.keys(), reverse=True):
        s = hist[d]
        if s == 0:
            break
        if last_sign is None:
            last_sign = s
            streak = 1
        elif s == last_sign:
            streak += 1
        else:
            break
    signed_streak = (last_sign or 0) * streak
    try:
        store = gex_store.backend_name()
    except Exception:
        store = "memory"
    return {
        "streak": signed_streak,
        "structural": abs(signed_streak) >= _STRUCTURAL_THRESHOLD,
        "regime_kind": (
            "Bull structure" if signed_streak >= _STRUCTURAL_THRESHOLD else
            "Bear structure" if signed_streak <= -_STRUCTURAL_THRESHOLD else
            "Transitional"
        ),
        "store": store,
        "as_of": max(hist.keys()) if hist else None,
        "history_days": len(hist),
    }


def _alpaca_get(endpoint: str, params: dict) -> dict:
    headers = {"APCA-API-KEY-ID": ALPACA_API_KEY, "APCA-API-SECRET-KEY": ALPACA_API_SECRET}
    url = ALPACA_DATA_BASE + endpoint
    for attempt in range(3):
        try:
            r = requests.get(url, params=params, headers=headers, timeout=15)
            if r.status_code == 429:
                time.sleep(5); continue
            r.raise_for_status()
            return r.json()
        except Exception:
            if attempt == 2:
                return {}
            time.sleep(2)
    return {}


def _norm_iv(value) -> float:
    try:
        iv = float(value or 0)
    except Exception:
        return 0.0
    if iv <= 0:
        return 0.0
    return iv / 100 if iv > 3 else iv


def _bs_gamma(spot: float, strike: float, iv: float, T: float, r: float = 0.05) -> float:
    if spot <= 0 or strike <= 0 or iv <= 0 or T <= 0:
        return 0.0
    sqrt_T = math.sqrt(T)
    d1 = (math.log(spot / strike) + (r + 0.5 * iv * iv) * T) / (iv * sqrt_T)
    pdf = math.exp(-0.5 * d1 * d1) / math.sqrt(2 * math.pi)
    return pdf / (spot * iv * sqrt_T)


def _fetch_spy_chain_cboe(spot_hint: float = 0) -> tuple[list[dict], float]:
    """
    CBOE free delayed-quotes endpoint. Returns (contracts, spot).
    Each contract carries CBOE's own gamma (no Black-Scholes needed) → gamma_known=True.
    """
    url = "https://cdn.cboe.com/api/global/delayed_quotes/options/SPY.json"
    try:
        r = requests.get(url, timeout=15, headers={"User-Agent": "Mozilla/5.0"})
        r.raise_for_status()
        payload = r.json()
    except Exception:
        return [], 0.0

    data = payload.get("data", {})
    spot = float(data.get("current_price") or data.get("close") or spot_hint or 0)
    options = data.get("options", [])
    if not options:
        return [], spot

    today   = date.today()
    exp_min = (today + timedelta(days=2))
    exp_max = (today + timedelta(days=45))
    s_lo    = spot * 0.85 if spot else 0
    s_hi    = spot * 1.15 if spot else float("inf")

    contracts: list[dict] = []
    for o in options:
        sym = o.get("option", "")
        after = sym.replace("SPY", "", 1)
        if len(after) < 15:
            continue
        try:
            exp_dt  = datetime.strptime(after[:6], "%y%m%d").date()
            cp_flag = after[6]
            strike  = int(after[7:15]) / 1000
        except Exception:
            continue
        if not (exp_min <= exp_dt <= exp_max):
            continue
        if not (s_lo <= strike <= s_hi):
            continue
        oi    = int(o.get("open_interest", 0) or 0)
        gamma = float(o.get("gamma", 0) or 0)
        if oi <= 0 or gamma <= 0:
            continue
        contracts.append({
            "strike":   strike,
            "exp":      exp_dt.isoformat(),
            "is_call":  cp_flag == "C",
            "gamma":    gamma,          # CBOE-computed
            "oi":       oi,
            "gamma_known": True,
        })
    return contracts, spot


def _fetch_chain(symbol: str, spot: float) -> list[dict]:
    today    = date.today()
    exp_min  = (today + timedelta(days=2)).isoformat()
    exp_max  = (today + timedelta(days=45)).isoformat()
    s_lo     = spot * 0.85
    s_hi     = spot * 1.15

    all_snaps: dict = {}
    page_token = None
    for _ in range(6):
        params: dict = {"limit": 1000, "feed": "indicative"}
        if page_token:
            params["page_token"] = page_token
        data = _alpaca_get(f"/v1beta1/options/snapshots/{symbol}", params)
        all_snaps.update(data.get("snapshots", {}))
        page_token = data.get("next_page_token")
        if not page_token:
            break

    contracts: list[dict] = []
    for sym, snap in all_snaps.items():
        after = sym.replace(symbol, "", 1)
        if len(after) < 15:
            continue
        try:
            exp_str  = after[:6]
            cp_flag  = after[6]
            strike   = int(after[7:15]) / 1000
            exp_date = f"20{exp_str[:2]}-{exp_str[2:4]}-{exp_str[4:6]}"
        except Exception:
            continue
        if not (exp_min <= exp_date <= exp_max):
            continue
        if not (s_lo <= strike <= s_hi):
            continue
        iv = _norm_iv(snap.get("impliedVolatility") or snap.get("implied_volatility") or snap.get("iv"))
        oi = int(snap.get("openInterest", 0) or 0)
        if iv <= 0 or oi <= 0:
            continue
        contracts.append({
            "strike":  strike,
            "exp":     exp_date,
            "is_call": cp_flag == "C",
            "iv":      iv,
            "oi":      oi,
        })
    return contracts


def _spot(symbol: str) -> Optional[float]:
    data = _alpaca_get(f"/v2/stocks/{symbol}/snapshot", {})
    quote = data.get("latestTrade") or data.get("latest_trade") or {}
    price = quote.get("p") or quote.get("price")
    if price:
        try:
            return float(price)
        except Exception:
            pass
    try:
        import yfinance as yf
        hist = yf.Ticker(symbol).history(period="1d")
        if not hist.empty:
            return float(hist["Close"].iloc[-1])
    except Exception:
        pass
    return None


def _fetch_chain_yfinance(symbol: str, spot: float) -> list[dict]:
    try:
        import yfinance as yf
    except Exception:
        return []
    try:
        tk = yf.Ticker(symbol)
        expirations = tk.options
    except Exception:
        return []
    if not expirations:
        return []

    today    = date.today()
    exp_min  = (today + timedelta(days=2)).isoformat()
    exp_max  = (today + timedelta(days=45)).isoformat()
    s_lo     = spot * 0.85
    s_hi     = spot * 1.15
    target_exps = [e for e in expirations if exp_min <= e <= exp_max]
    # Sample expirations to keep latency manageable
    if len(target_exps) > 6:
        step = max(1, len(target_exps) // 6)
        target_exps = target_exps[::step][:6]

    contracts: list[dict] = []
    for exp in target_exps:
        try:
            chain = tk.option_chain(exp)
        except Exception:
            continue
        for df, is_call in [(chain.calls, True), (chain.puts, False)]:
            for _, row in df.iterrows():
                try:
                    strike = float(row.get("strike", 0))
                    iv     = _norm_iv(row.get("impliedVolatility"))
                    oi     = int(row.get("openInterest", 0) or 0)
                except Exception:
                    continue
                if iv <= 0 or oi <= 0:
                    continue
                if not (s_lo <= strike <= s_hi):
                    continue
                contracts.append({
                    "strike":  strike,
                    "exp":     exp,
                    "is_call": is_call,
                    "iv":      iv,
                    "oi":      oi,
                })
    return contracts


def compute_gex(symbol: str, use_cboe: bool = False) -> dict:
    """
    Net dealer gamma exposure for `symbol`. CBOE (free, gamma-known) is only
    available for SPY → use_cboe=True there; sectors fall back to
    Alpaca → yfinance option chains with Black-Scholes-approximated gamma.
    """
    symbol = symbol.upper()
    now = time.time()
    ttl = _CACHE_TTL if symbol == "SPY" else _SECTOR_CACHE_TTL
    c = _cache.get(symbol)
    if c and c.get("data") and now - c["ts"] < ttl:
        return c["data"]

    contracts: list[dict] = []
    spot: Optional[float] = None
    source = ""
    if use_cboe:
        source = "cboe"
        contracts, spot = _fetch_spy_chain_cboe()
    if not contracts:
        spot = _spot(symbol)
        if not spot:
            return {"available": False, "reason": "spot price unavailable"}
        source = "alpaca"
        contracts = _fetch_chain(symbol, spot)
        if not contracts:
            source = "yfinance"
            contracts = _fetch_chain_yfinance(symbol, spot)
    if not contracts or not spot:
        return {"available": False,
                "reason": "options chain empty (cboe + alpaca + yfinance)"}

    today = date.today()
    call_gex = 0.0
    put_gex  = 0.0
    for c in contracts:
        if c.get("gamma_known"):
            gamma = c["gamma"]
        else:
            exp_dt = datetime.strptime(c["exp"], "%Y-%m-%d").date()
            days   = max((exp_dt - today).days, 1)
            T      = days / 365.0
            gamma  = _bs_gamma(spot, c["strike"], c["iv"], T)
        # Dollar gamma per 1% move = gamma × OI × 100 × spot² × 0.01
        dollar_g = gamma * c["oi"] * 100 * spot * spot * 0.01
        if c["is_call"]:
            call_gex += dollar_g
        else:
            put_gex  += dollar_g

    # SpotGamma convention: net = calls - puts (dealers long calls, short puts)
    net_gex = call_gex - put_gex

    if net_gex > 0:
        regime = "Long Gamma"
        action = "Range bound — sell rallies, buy dips"
    elif net_gex < 0:
        regime = "Short Gamma"
        action = "Trends extend — ride strength, fade weakness with care"
    else:
        regime = "Near Flip"
        action = "Reduce intraday conviction"

    streak_info = _record_and_streak(net_gex, symbol)

    result = {
        "available": True,
        "symbol":        symbol,
        "spot":          round(spot, 2),
        "net_gex":       round(net_gex, 0),
        "call_gex":      round(call_gex, 0),
        "put_gex":       round(put_gex, 0),
        "regime":        regime,
        "action":        action,
        "contracts_used": len(contracts),
        "source":        source,
        "streak":        streak_info["streak"],
        "structural":    streak_info["structural"],
        "regime_kind":   streak_info["regime_kind"],
        "store":         streak_info["store"],
        "as_of":         streak_info["as_of"],
        "history_days":  streak_info["history_days"],
    }
    _cache[symbol] = {"data": result, "ts": now}
    return result


def compute_spy_gex() -> dict:
    """Net dealer gamma exposure for SPY (CBOE-first). Back-compat wrapper."""
    return compute_gex("SPY", use_cboe=True)


# ── Sector gamma — strong (Long Gamma) vs weak (Short Gamma) sectors ──────────
SECTOR_GEX_SYMBOLS: list[tuple[str, str]] = [
    ("XLK",  "Tech"),
    ("XLF",  "Financials"),
    ("XLE",  "Energy"),
    ("XLY",  "Discretionary"),
    ("XLV",  "Health"),
    ("XLI",  "Industrials"),
    ("XLC",  "Comm"),
    ("XLP",  "Staples"),
    ("XLU",  "Utilities"),
    ("XLB",  "Materials"),
    ("XLRE", "Real Estate"),
]


def _sector_one(item: tuple[str, str]) -> dict:
    sym, name = item
    try:
        g = compute_gex(sym, use_cboe=False)
    except Exception as e:
        g = {"available": False, "reason": str(e)[:80]}
    if g.get("available"):
        return {
            "symbol":      sym,
            "name":        name,
            "spot":        g.get("spot"),
            "net_gex":     g.get("net_gex"),
            "regime":      g.get("regime"),       # Long / Short / Near Flip
            "streak":      g.get("streak"),
            "structural":  g.get("structural"),
            "regime_kind": g.get("regime_kind"),
            "source":      g.get("source"),
            "as_of":       g.get("as_of"),
        }
    return {
        "symbol": sym, "name": name,
        "available": False, "reason": g.get("reason", "unavailable"),
    }


def compute_sector_gex() -> dict:
    """GEX per tracked sector ETF. Best-effort — a failed sector is skipped.

    Sectors are independent (each is its own option chain), so they run
    concurrently — serial was ~11× slower on a cold cache. Worker count is
    capped at 6 to stay under Alpaca's rate limit; ex.map preserves the
    SECTOR_GEX_SYMBOLS order.
    """
    from concurrent.futures import ThreadPoolExecutor

    with ThreadPoolExecutor(max_workers=6) as ex:
        sectors = list(ex.map(_sector_one, SECTOR_GEX_SYMBOLS))
    return {"sectors": sectors}
