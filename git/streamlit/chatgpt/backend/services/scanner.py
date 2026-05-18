"""
Single-stock scan logic — reuses the full scoring pipeline.
Designed to be called in parallel from the scanner router.
"""
import requests
import yfinance as yf
import pandas as pd
from datetime import date, timedelta
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Iterator, Optional

from backend.services.analysis import (
    full_score_pipeline, get_entry_grade, calc_trade_levels,
    compute_weekly_bias, compute_daily_bias, compute_4h_bias,
    mtf_signal_action, calc_atr, calc_weekly_atr, compute_seasonality,
    get_fundamentals, compute_btd, compute_w30, _col,
)
from backend.services.market_data import get_daily_bars_alpaca, get_five_min_bars_alpaca
from backend.services.options import get_options_strategy, get_options_bias
from backend.config import ALPACA_API_KEY, ALPACA_API_SECRET

# ── Watchlists ────────────────────────────────────────────────────────────────

WATCHLISTS: dict[str, list[str]] = {
    "default": [
        "AAPL", "MSFT", "GOOGL", "AMZN", "NVDA", "META", "TSLA", "AMD", "NFLX", "CRM",
        "ORCL", "ADBE", "INTC", "PYPL", "SQ", "SHOP", "COIN", "UBER", "ABNB", "SNOW",
        "BA", "CAT", "GS", "JPM", "V", "MA", "DIS", "NKE", "SBUX", "MCD",
        "XOM", "CVX", "PFE", "JNJ", "UNH", "MRNA", "LLY", "ABBV", "BMY", "MRK",
        "SPY", "QQQ", "DIA", "XLF", "XLE", "XLK", "ARKK", "SOXX", "SMH", "MRVL",
    ],
    "tech": [
        "AAPL", "MSFT", "NVDA", "GOOGL", "META", "AMD", "TSLA", "ORCL", "ADBE", "CRM",
        "INTC", "QCOM", "TXN", "MU", "AMAT", "LRCX", "KLAC", "MRVL", "AVGO", "ARM",
        "PLTR", "SNOW", "DDOG", "ZS", "CRWD", "NET", "MDB", "SMCI", "DELL", "HPE",
    ],
    "mega_cap": [
        "AAPL", "MSFT", "NVDA", "GOOGL", "AMZN", "META", "TSLA", "AVGO", "LLY", "JPM",
        "V", "UNH", "XOM", "MA", "JNJ", "PG", "HD", "COST", "MRK", "ABBV",
    ],
    "etfs": [
        "XLB", "XLC", "XLE", "XLF", "XLI", "XLK", "XLP", "XLRE", "XLU", "XLV",
        "XLY", "VAW", "VOX", "VDE", "VFH", "VIS", "VGT", "VDC", "VNQ", "VPU",
        "VHT", "VCR", "RTM", "IXE", "KBE", "ITA", "IYW", "KXI", "REZ", "IDU",
        "IXJ", "RXI", "GLD", "SLV", "DBMF", "HFGM", "CTA", "PPI", "UUP", "TLT",
        "JPST", "BIL", "SHY", "SCHD", "VXUS", "VTI", "IEMG", "SPY", "QQQ", "USMV",
        "SPLV", "GLDM", "TIP", "VTIP", "SCHP", "RINF",
    ],
    "momentum": [
        "NVDA", "MRVL", "AVGO", "ARM", "PLTR", "CRWD", "DDOG", "NET", "SMCI",
        "TSLA", "META", "AMZN", "GOOGL", "MSFT", "AMD", "SNOW", "ZS", "SHOP", "COIN", "UBER",
    ],
    # Fallback only — overridden at runtime by get_short_squeeze_tickers()
    "short_squeeze": [
        "TSLA", "COIN", "RIVN", "LCID", "NKLA", "BBAI", "SOUN", "MSTR", "IONQ",
        "SMCI", "BYND", "SPCE", "RIDE", "WKHS", "FFIE", "MULN", "ASTS", "RKLB",
        "ACHR", "JOBY", "LILM", "CLOV", "WISH", "SKLZ", "DKNG", "PLBY", "CVNA",
        "GME", "AMC", "BBBY", "KOSS", "EXPR",
    ],
}


ETF_SECTORS: dict[str, str] = {
    "XLB": "Materials", "VAW": "Materials", "RTM": "Materials",
    "XLC": "Communication", "VOX": "Communication",
    "XLE": "Energy", "VDE": "Energy", "IXE": "Energy",
    "XLF": "Financials", "VFH": "Financials", "KBE": "Banks",
    "XLI": "Industrials", "VIS": "Industrials", "ITA": "Aerospace",
    "XLK": "Technology", "VGT": "Technology", "IYW": "Technology", "QQQ": "Nasdaq 100",
    "XLP": "Staples", "VDC": "Staples", "KXI": "Staples",
    "XLRE": "Real Estate", "VNQ": "Real Estate", "REZ": "Real Estate",
    "XLU": "Utilities", "VPU": "Utilities", "IDU": "Utilities",
    "XLV": "Health Care", "VHT": "Health Care", "IXJ": "Health Care",
    "XLY": "Discretionary", "VCR": "Discretionary", "RXI": "Discretionary",
    "GLD": "Gold", "GLDM": "Gold", "SLV": "Silver",
    "DBMF": "Managed Futures", "CTA": "Managed Futures", "HFGM": "Alternatives",
    "PPI": "Inflation", "RINF": "Inflation",
    "UUP": "US Dollar",
    "TLT": "Treasury", "JPST": "Short Bond", "BIL": "T-Bills", "SHY": "Treasury",
    "TIP": "TIPS", "VTIP": "Short TIPS", "SCHP": "TIPS",
    "SCHD": "Dividend", "VXUS": "International", "IEMG": "Emerging Markets",
    "VTI": "US Total Market", "SPY": "S&P 500", "USMV": "Low Volatility", "SPLV": "Low Volatility",
}


def _sector_for_ticker(ticker: str, info: Optional[dict] = None) -> str:
    symbol = (ticker or "").upper()
    if symbol in ETF_SECTORS:
        return ETF_SECTORS[symbol]
    info = info or {}
    sector = info.get("sector") or info.get("category") or info.get("industry")
    if sector:
        return str(sector)
    if info.get("quoteType") == "ETF":
        return "ETF"
    return "Unknown"


# ── Live short-squeeze fetcher ─────────────────────────────────────────────────

def get_telegram_watchlist() -> list[str]:
    """
    Tickers from the ThinkOrSwim scan email (TOS → Gmail), refreshed daily by
    the scheduler ~7:15 PM CST and on-demand via the Hard-pull endpoint.
    (Name kept for wiring stability; source is now Gmail, not Telegram.)
    Falls back to the default watchlist if the store is empty.
    """
    try:
        from backend.services.gmail_watchlist import fetch_today_watchlist
        tickers = fetch_today_watchlist()
        if tickers:
            return tickers
    except Exception as e:
        print(f"⚠️ TOS/Gmail watchlist fetch failed: {e}")
    return WATCHLISTS["default"]


def get_short_squeeze_tickers(min_short_pct: float = 10.0, limit: int = 40) -> list[str]:
    """
    Pull US stocks sorted by short % of float (desc) from Yahoo Finance screener.
    Filters: short float > min_short_pct%, US exchange, market cap > $50M.
    Falls back to hardcoded list on any error.
    """
    # Strategy 1: Yahoo Finance custom screener API
    try:
        url = "https://query1.finance.yahoo.com/v1/finance/screener"
        headers = {"User-Agent": "Mozilla/5.0"}
        body = {
            "offset": 0,
            "size": limit,
            "sortField": "percentofsharesfloatshort",
            "sortType": "DESC",
            "quoteType": "EQUITY",
            "query": {
                "operator": "AND",
                "operands": [
                    {"operator": "GT", "operands": ["percentofsharesfloatshort", min_short_pct / 100]},
                    {"operator": "EQ", "operands": ["region", "us"]},
                    {"operator": "GT", "operands": ["intradaymarketcap", 50_000_000]},
                ],
            },
            "userId": "",
            "userIdType": "guid",
        }
        r = requests.post(url, json=body, headers=headers, timeout=15)
        r.raise_for_status()
        quotes = r.json()["finance"]["result"][0]["quotes"]
        tickers = [q["symbol"] for q in quotes if "." not in q.get("symbol", "")]
        if tickers:
            return tickers[:limit]
    except Exception:
        pass

    # Strategy 2: Yahoo Finance predefined "most shorted" screener
    try:
        url = "https://query1.finance.yahoo.com/v1/finance/screener/predefined/saved"
        params = {"formatted": "false", "scrIds": "most_shorted_stocks", "count": limit}
        headers = {"User-Agent": "Mozilla/5.0"}
        r = requests.get(url, params=params, headers=headers, timeout=10)
        r.raise_for_status()
        quotes = r.json()["finance"]["result"][0]["quotes"]
        tickers = [q["symbol"] for q in quotes if "." not in q.get("symbol", "")]
        if tickers:
            return tickers[:limit]
    except Exception:
        pass

    # Fallback: hardcoded list
    return WATCHLISTS["short_squeeze"]


def _round2(v) -> float:
    try:
        return round(float(v), 2)
    except Exception:
        return 0.0


def _money_short(v) -> Optional[str]:
    try:
        n = float(v)
    except Exception:
        return None
    sign = "-" if n < 0 else ""
    n = abs(n)
    if n >= 1e12:
        return f"{sign}${n / 1e12:.2f}T"
    if n >= 1e9:
        return f"{sign}${n / 1e9:.2f}B"
    if n >= 1e6:
        return f"{sign}${n / 1e6:.2f}M"
    if n >= 1e3:
        return f"{sign}${n / 1e3:.1f}K"
    return f"{sign}${n:.0f}"


def _bar_date(daily_df: pd.DataFrame) -> date:
    try:
        idx = daily_df.index[-1]
        if hasattr(idx, "date"):
            return idx.date()
        return date.fromisoformat(str(idx)[:10])
    except Exception:
        return date.today()


def _next_trading_day(d: date) -> date:
    nxt = d + timedelta(days=1)
    while nxt.weekday() >= 5:
        nxt += timedelta(days=1)
    return nxt


def _idx_date(i) -> date:
    return i.date() if hasattr(i, "date") else date.fromisoformat(str(i)[:10])


def _forward_bars(ticker: str, scan_date: date, n_days: int) -> Optional[pd.DataFrame]:
    """
    Up to n_days daily bars STRICTLY AFTER scan_date — skips weekends and
    holidays via real bars (a Fri/Sat scan rolls forward to Monday+).
    Columns lowercased, ascending. Backtest-only; returns None if no forward
    bar exists yet (window not closed).
    """
    win_start = scan_date + timedelta(days=1)
    # calendar cushion: ~1.6× trading days + a week for weekends/holidays
    win_end   = scan_date + timedelta(days=int(n_days * 1.6) + 7)
    df = None
    try:
        df = get_daily_bars_alpaca(ticker, str(win_start), str(win_end),
                                   ALPACA_API_KEY, ALPACA_API_SECRET)
    except Exception:
        df = None
    if df is None or df.empty:
        try:
            h = yf.Ticker(ticker).history(
                start=str(win_start), end=str(win_end + timedelta(days=1)),
                interval="1d")
            if not h.empty:
                h.columns = [c.lower() for c in h.columns]
                df = h
        except Exception:
            df = None
    if df is None or df.empty:
        return None
    try:
        df = df.copy()
        df.index = pd.to_datetime(df.index)
        df = df.sort_index()
        fwd = df[[_idx_date(i) > scan_date for i in df.index]].head(n_days)
        return fwd if not fwd.empty else None
    except Exception:
        return None


def _simulate_swing(fwd: Optional[pd.DataFrame], direction: str,
                    entry, stop, t1) -> dict:
    """
    Walk forward bars: did target1 print before stop_loss, in the trade's
    direction (LONG unless verdict bearish)? Same-bar ambiguity resolves
    stop-first (conservative). Unresolved at window end → OPEN, marked to
    market on the last close. r_mult is realized R (risk = |entry-stop|).
    """
    out: dict = {"outcome": None, "r_mult": None, "bars": None}
    try:
        if (fwd is None or fwd.empty or entry is None
                or stop is None or t1 is None):
            return out
        risk = abs(float(entry) - float(stop))
        if risk <= 0:
            return out
        entry = float(entry); stop = float(stop); t1 = float(t1)
        hc = _col(fwd, "high"); lc = _col(fwd, "low"); cc = _col(fwd, "close")
        long_ = direction != "SHORT"
        for n, (_, bar) in enumerate(fwd.iterrows(), start=1):
            hi = float(bar[hc]); lo = float(bar[lc])
            hit_stop = (lo <= stop) if long_ else (hi >= stop)
            hit_t1   = (hi >= t1)   if long_ else (lo <= t1)
            if hit_stop:                       # stop-first on ambiguous bars
                return {"outcome": "LOSS", "r_mult": -1.0, "bars": n}
            if hit_t1:
                return {"outcome": "WIN",
                        "r_mult": round(abs(t1 - entry) / risk, 2), "bars": n}
        last_close = float(fwd.iloc[-1][cc])
        m2m = (last_close - entry) if long_ else (entry - last_close)
        return {"outcome": "OPEN", "r_mult": round(m2m / risk, 2),
                "bars": int(len(fwd))}
    except Exception:
        return out


def _lre_takeaway(verdict: str, label: str, status: str, direction: str) -> str:
    """Compact read of verdict + LRE quality/status for the scanner cell."""
    is_lean = verdict in ("LEAN BULLISH", "LEAN BEARISH")
    is_weak = label == "Weak"
    is_decent = label == "Decent"
    is_strong = label in ("GOOD", "STRONG", "PRIME")
    side = "long" if direction == "LONG" else "short"

    if status == "INVALIDATED":
        return f"{side.title()} invalidated"

    if is_weak:
        base = "Weak short; bounce risk" if direction == "SHORT" else "Weak long; fade risk"
    elif is_decent:
        base = f"Decent {side}; confirm"
    elif is_strong and is_lean:
        base = f"Lean {side}; watch"
    elif is_strong:
        base = f"Active {side} setup"
    else:
        base = f"{side.title()} setup"

    if status == "ACTIVE":
        return base
    if status == "DISCOUNT":
        return f"{base}; better price"
    if status == "STALE":
        return f"{base}; extended"
    return base


def _verdict_flip_info(daily_df: pd.DataFrame, current_verdict: str, lookback: int = 90) -> dict:
    """Find the latest historical date where verdict changed into current verdict."""
    if daily_df is None or len(daily_df) < 80 or not current_verdict:
        return {}

    start_idx = max(60, len(daily_df) - lookback)
    history: list[tuple[str, str]] = []
    for end_idx in range(start_idx, len(daily_df) + 1):
        try:
            scored = full_score_pipeline(daily_df.iloc[:end_idx])
            verdict = scored.get("verdict")
            if not verdict:
                continue
            idx = daily_df.index[end_idx - 1]
            dt = pd.to_datetime(idx).date().isoformat()
            history.append((dt, verdict))
        except Exception:
            continue

    if len(history) < 2:
        return {}

    current_date = history[-1][0]
    prev_verdict = None
    flip_date = None
    for i in range(len(history) - 1, 0, -1):
        if history[i][1] != history[i - 1][1]:
            flip_date = history[i][0]
            prev_verdict = history[i - 1][1]
            break

    if not flip_date or history[-1][1] != current_verdict:
        return {}

    try:
        days_since = (date.fromisoformat(current_date) - date.fromisoformat(flip_date)).days
    except Exception:
        days_since = None

    return {
        "verdict_flip_date": flip_date,
        "verdict_flip_from": prev_verdict,
        "verdict_flip_days": days_since,
        "verdict_flip_text": f"Flip {flip_date[5:]} from {prev_verdict}",
    }


def _low_risk_entry(price: float, verdict: str, confidence: str, mtf_rank: int,
                    vol_profile: dict, vol_trend: str, trade: dict) -> dict:
    """Score low-risk entries for directional setups."""
    bullish = verdict in ("BULLISH", "LEAN BULLISH")
    bearish = verdict in ("BEARISH", "LEAN BEARISH")
    atr = float(trade.get("atr") or 0)
    if not price or atr <= 0 or (not bullish and not bearish):
        return {}

    score = 0
    reasons: list[str] = []

    if vol_profile:
        val = float(vol_profile.get("val") or 0)
        vah = float(vol_profile.get("vah") or 0)
        vol_bias = vol_profile.get("vol_bias")
        near_val = val > 0 and (val - atr * 0.5) <= price <= (val + atr * 0.5)
        near_vah = vah > 0 and (vah - atr * 0.5) <= price <= (vah + atr * 0.5)
        if bullish and near_val:
            score += 1
            reasons.append("near VAL")
        elif bearish and near_vah:
            score += 1
            reasons.append("near VAH")
        if bullish and vol_bias == "BULLISH":
            score += 1
            reasons.append("VA bias bullish")
        elif bearish and vol_bias == "BEARISH":
            score += 1
            reasons.append("VA bias bearish")

    if mtf_rank == 1:
        score += 1
        reasons.append("MTF aligned")
    if bullish and vol_trend == "ACCUMULATING":
        score += 1
        reasons.append("accumulating")
    elif bearish and vol_trend == "DISTRIBUTING":
        score += 1
        reasons.append("distributing")
    risk_pct = trade.get("risk_pct")
    if risk_pct is not None and 0 < float(risk_pct) < 3:
        score += 1
        reasons.append("tight stop <3%")
    if confidence == "HIGH":
        score += 1
        reasons.append("high confidence")

    score = min(score, 5)
    if score <= 0:
        return {}

    if score >= 5:
        label = "PRIME"
    elif score >= 4:
        label = "STRONG"
    elif score >= 3:
        label = "GOOD"
    elif score >= 2:
        label = "Decent"
    else:
        label = "Weak"

    if bullish:
        entry = price
        val = float((vol_profile or {}).get("val") or 0)
        if val > 0 and val < price:
            entry = val
        stop = entry - atr
        direction = "LONG"
    else:
        entry = price
        vah = float((vol_profile or {}).get("vah") or 0)
        if vah > price:
            entry = vah
        stop = entry + atr
        direction = "SHORT"

    lre_risk = abs(entry - stop) / entry * 100 if entry > 0 else 0
    near = abs(price - entry) / entry <= 0.05 if entry > 0 else False
    if bullish:
        status = "ACTIVE" if near else ("INVALIDATED" if price < stop else ("DISCOUNT" if price < entry else "STALE"))
    else:
        status = "ACTIVE" if near else ("INVALIDATED" if price > stop else ("DISCOUNT" if price > entry else "STALE"))

    return {
        "lre_score": score,
        "lre_label": label,
        "lre_direction": direction,
        "lre_reason": " | ".join(reasons),
        "lre_entry": _round2(entry),
        "lre_stop": _round2(stop),
        "lre_risk_pct": _round2(lre_risk),
        "lre_status": status,
        "lre_takeaway": _lre_takeaway(verdict, label, status, direction),
    }


# ── Single ticker scan ────────────────────────────────────────────────────────

def _num(v) -> Optional[float]:
    try:
        if v in (None, "N/A"):
            return None
        return float(v)
    except Exception:
        return None


def _valuation_target_pe(growth: Optional[float], margin_pct: Optional[float], debt_to_equity: Optional[float]) -> float:
    multiple = 18.0
    if growth is not None:
        if growth >= 0.20:
            multiple = 28.0
        elif growth >= 0.10:
            multiple = 24.0
        elif growth >= 0.05:
            multiple = 21.0
        elif growth < 0:
            multiple = 12.0
    if margin_pct is not None:
        if margin_pct >= 25:
            multiple += 2.0
        elif margin_pct < 0:
            multiple -= 4.0
        elif margin_pct < 5:
            multiple -= 2.0
    if debt_to_equity is not None:
        if debt_to_equity <= 50:
            multiple += 1.0
        elif debt_to_equity >= 250:
            multiple -= 3.0
        elif debt_to_equity >= 150:
            multiple -= 1.5
    return max(8.0, min(35.0, multiple))


def _valuation_target_ps(growth: Optional[float], margin_pct: Optional[float]) -> float:
    multiple = 3.0
    if growth is not None:
        if growth >= 0.30:
            multiple = 10.0
        elif growth >= 0.20:
            multiple = 7.0
        elif growth >= 0.10:
            multiple = 5.0
        elif growth < 0:
            multiple = 1.5
    if margin_pct is not None:
        if margin_pct >= 25:
            multiple += 2.0
        elif margin_pct < 0:
            multiple -= 1.5
        elif margin_pct < 5:
            multiple -= 0.5
    return max(0.5, min(15.0, multiple))


def _valuation_target_pb(growth: Optional[float], margin_pct: Optional[float]) -> float:
    multiple = 2.5
    if margin_pct is not None:
        if margin_pct >= 25:
            multiple += 2.0
        elif margin_pct >= 15:
            multiple += 1.0
        elif margin_pct < 0:
            multiple -= 1.0
    if growth is not None:
        if growth >= 0.20:
            multiple += 1.0
        elif growth < 0:
            multiple -= 0.5
    return max(0.8, min(8.0, multiple))


def _valuation_estimate(f: dict) -> dict:
    """Simple current valuation read from snapshot fundamentals, not historical."""
    score = 0
    reasons: list[str] = []

    price = _num(f.get("price"))
    target = _num(f.get("target_mean_price"))
    target_upside = None
    if price and target:
        target_upside = (target - price) / price * 100
        reasons.append(f"Analyst target {target_upside:+.0f}%")
        if target_upside >= 20:
            score += 2
        elif target_upside >= 8:
            score += 1
        elif target_upside <= -20:
            score -= 2
        elif target_upside <= -8:
            score -= 1

    forward_pe = _num(f.get("forward_pe"))
    pe = _num(f.get("pe_ratio"))
    pe_used = forward_pe or pe
    if pe_used:
        pe_name = "Fwd P/E" if forward_pe else "P/E"
        reasons.append(f"{pe_name} {pe_used:.1f}")
        if pe_used <= 15:
            score += 1
        elif pe_used >= 60:
            score -= 2
        elif pe_used >= 35:
            score -= 1

    growth = _num(f.get("earnings_growth"))
    if growth is not None:
        growth_pct = growth * 100
        reasons.append(f"Growth {growth_pct:+.0f}%")
        if growth >= 0.20:
            score += 1
        elif growth < -0.05:
            score -= 1

    margin = _num(f.get("profit_margin"))
    if margin is not None:
        reasons.append(f"Margin {margin:.0f}%")
        if margin >= 20:
            score += 1
        elif margin < 0:
            score -= 2
        elif margin < 5:
            score -= 1

    cashflow = _num(f.get("free_cashflow"))
    if cashflow is None:
        cashflow = _num(f.get("operating_cashflow"))
    if cashflow is not None:
        reasons.append(f"Cash flow {_money_short(cashflow)}")
        score += 1 if cashflow > 0 else -1

    debt_to_equity = _num(f.get("debt_to_equity"))
    if debt_to_equity is not None:
        reasons.append(f"D/E {debt_to_equity:.0f}%")
        if debt_to_equity <= 50:
            score += 1
        elif debt_to_equity >= 250:
            score -= 2
        elif debt_to_equity >= 150:
            score -= 1

    price_to_book = _num(f.get("price_to_book"))
    if price_to_book:
        reasons.append(f"P/B {price_to_book:.1f}")
        if price_to_book <= 3:
            score += 1
        elif price_to_book >= 10:
            score -= 1

    if score >= 4:
        label = "Undervalued"
    elif score >= 2:
        label = "Attractive"
    elif score >= -1:
        label = "Fair Value"
    elif score >= -3:
        label = "Expensive"
    else:
        label = "Overvalued"

    def _sane(value: Optional[float]) -> bool:
        if value is None or not price:
            return False
        return abs(value - price) / price <= 1.0  # within ±100% of current price

    pe_fair_value = None
    pe_upside_pct = None
    pe_source = None
    if price and pe_used and pe_used >= 8.0:
        implied_eps = price / pe_used
        fair_pe = _valuation_target_pe(growth, margin, debt_to_equity)
        candidate = implied_eps * fair_pe
        if candidate > 0 and _sane(candidate):
            pe_fair_value = candidate
            pe_source = f"P/E fair value ({fair_pe:.0f}x earnings)"

    if pe_fair_value is None and price:
        ps_ratio = _num(f.get("price_to_sales"))
        if ps_ratio and ps_ratio > 0:
            sales_per_share = price / ps_ratio
            fair_ps = _valuation_target_ps(growth, margin)
            candidate = sales_per_share * fair_ps
            if candidate > 0 and _sane(candidate):
                pe_fair_value = candidate
                pe_source = f"P/S fair value ({fair_ps:.1f}x sales)"

    if pe_fair_value is None and price:
        pb_ratio = _num(f.get("price_to_book"))
        if pb_ratio and pb_ratio > 0:
            book_per_share = price / pb_ratio
            fair_pb = _valuation_target_pb(growth, margin)
            candidate = book_per_share * fair_pb
            if candidate > 0 and _sane(candidate):
                pe_fair_value = candidate
                pe_source = f"P/B fair value ({fair_pb:.1f}x book)"

    if pe_fair_value is None and price:
        implied_pct = max(-0.30, min(0.30, score * 0.06))
        pe_fair_value = price * (1 + implied_pct)
        pe_source = f"Score-implied fair value (score {score:+d})"

    if pe_fair_value and price:
        pe_upside_pct = (pe_fair_value - price) / price * 100

    analyst_fair_value = target if (target and target > 0) else None
    analyst_upside_pct = None
    if analyst_fair_value and price:
        analyst_upside_pct = (analyst_fair_value - price) / price * 100

    candidates: list[tuple[float, float, str]] = []
    if analyst_fair_value:
        candidates.append((analyst_fair_value, 0.60, "Analyst target"))
    if pe_fair_value:
        candidates.append((pe_fair_value, 0.40, pe_source or "P/E fair value"))
    if not candidates and price:
        implied_pct = max(-0.30, min(0.30, score * 0.06))
        candidates.append((price * (1 + implied_pct), 1.0, "Score-implied fair value"))

    fair_value = None
    valuation_upside_pct = None
    valuation_source = None
    if candidates:
        total_weight = sum(weight for _, weight, _ in candidates)
        fair_value = sum(value * weight for value, weight, _ in candidates) / total_weight if total_weight else None
        valuation_source = " + ".join(source for _, _, source in candidates[:2])
        if fair_value and price:
            valuation_upside_pct = (fair_value - price) / price * 100

    cyclical_peak_risk = False
    cyclical_peak_reason = None
    trailing_pe_raw = _num(f.get("pe_ratio"))
    forward_pe_raw = _num(f.get("forward_pe"))
    triggers: list[str] = []
    if trailing_pe_raw is not None and 0 < trailing_pe_raw < 10:
        triggers.append(f"Trail P/E {trailing_pe_raw:.0f}x (possible peak EPS)")
    if (
        trailing_pe_raw is not None
        and forward_pe_raw is not None
        and trailing_pe_raw > 0
        and forward_pe_raw > trailing_pe_raw * 1.5
    ):
        triggers.append(
            f"Fwd P/E {forward_pe_raw:.0f}x >> Trail {trailing_pe_raw:.0f}x (EPS drop expected)"
        )
    if analyst_upside_pct is not None and analyst_upside_pct <= -15:
        triggers.append(f"Analyst target {analyst_upside_pct:+.0f}% (Street skeptical)")
    if triggers:
        cyclical_peak_risk = True
        cyclical_peak_reason = " | ".join(triggers)

    long_runway = False
    long_runway_reason = None
    if (
        not cyclical_peak_risk
        and growth is not None and growth >= 0.05
        and margin is not None and margin >= 10
        and cashflow is not None and cashflow > 0
        and (debt_to_equity is None or debt_to_equity <= 150)
        and pe_used is not None and 10 <= pe_used <= 60
        and score >= 0
        and (analyst_upside_pct is None or analyst_upside_pct >= -10)
    ):
        long_runway = True
        long_runway_bits = [
            f"Growth {growth * 100:+.0f}%",
            f"Margin {margin:.0f}%",
            "Positive FCF",
            f"Score {score:+d}",
        ]
        long_runway_reason = "Durable fundamentals — " + " | ".join(long_runway_bits)

    multi_bagger = False
    multi_bagger_reason = None
    revenue_growth = _num(f.get("revenue_growth"))
    total_cash = _num(f.get("total_cash"))
    market_cap_raw = _num(f.get("market_cap_raw"))
    ps_ratio = _num(f.get("price_to_sales"))
    if (
        not cyclical_peak_risk
        and not long_runway
        and revenue_growth is not None and revenue_growth >= 0.25
        and (market_cap_raw is None or market_cap_raw <= 20e9)
        and (debt_to_equity is None or debt_to_equity <= 100)
        and (total_cash is None or total_cash > 0)
        and (ps_ratio is None or 0 < ps_ratio <= 25)
    ):
        multi_bagger = True
        bits = [f"Rev growth {revenue_growth * 100:+.0f}%"]
        if market_cap_raw is not None:
            bits.append(
                f"Cap ${market_cap_raw/1e9:.1f}B" if market_cap_raw >= 1e9
                else f"Cap ${market_cap_raw/1e6:.0f}M"
            )
        if ps_ratio is not None:
            bits.append(f"P/S {ps_ratio:.1f}x")
        if total_cash is not None and total_cash > 0:
            bits.append(
                f"Cash ${total_cash/1e9:.1f}B" if total_cash >= 1e9
                else f"Cash ${total_cash/1e6:.0f}M"
            )
        multi_bagger_reason = "Speculative growth — " + " | ".join(bits)

    reason = " | ".join(reasons[:7]) if reasons else "Insufficient valuation fundamentals"
    return {
        "valuation_label": label,
        "valuation_score": score,
        "valuation_reason": reason,
        "valuation_fair_value": _round2(fair_value) if fair_value is not None else None,
        "valuation_upside_pct": _round2(valuation_upside_pct) if valuation_upside_pct is not None else None,
        "valuation_source": valuation_source,
        "valuation_pe_fair_value": _round2(pe_fair_value) if pe_fair_value is not None else None,
        "valuation_pe_upside_pct": _round2(pe_upside_pct) if pe_upside_pct is not None else None,
        "valuation_pe_source": pe_source,
        "valuation_analyst_fair_value": _round2(analyst_fair_value) if analyst_fair_value is not None else None,
        "valuation_analyst_upside_pct": _round2(analyst_upside_pct) if analyst_upside_pct is not None else None,
        "cyclical_peak_risk": cyclical_peak_risk,
        "cyclical_peak_reason": cyclical_peak_reason,
        "long_runway": long_runway,
        "long_runway_reason": long_runway_reason,
        "multi_bagger": multi_bagger,
        "multi_bagger_reason": multi_bagger_reason,
    }


def _fundamental_signals(ticker: str, fundamentals: Optional[dict] = None) -> str:
    """Return compact labels for the scanner Fundamental column."""
    f = fundamentals if fundamentals is not None else (get_fundamentals(ticker) or {})
    labels: list[str] = []

    earnings_growth = f.get("earnings_growth")
    if earnings_growth is not None:
        if earnings_growth > 0.25:
            labels.append("📈 Strong Earnings Growth")
        elif earnings_growth < -0.10:
            labels.append("⚠ Earnings Declining")

    profit_margin = f.get("profit_margin")
    if profit_margin is not None:
        if profit_margin > 20:
            labels.append("💰 High Margins")
        elif profit_margin < 0:
            labels.append("⚠ Unprofitable")

    dividend_yield = f.get("dividend_yield")
    if dividend_yield is not None and dividend_yield > 3:
        labels.append("💵 Good Dividend")

    short_pct = f.get("short_pct_float")
    if short_pct is not None and short_pct >= 10:
        labels.append("🔥 High Short Interest")

    debt_to_equity = f.get("debt_to_equity")
    total_debt = f.get("total_debt")
    try:
        if debt_to_equity is not None:
            dte = float(debt_to_equity)
            if dte >= 150:
                labels.append(f"High Debt D/E {dte:.0f}%")
            elif dte <= 50:
                labels.append(f"Low Debt D/E {dte:.0f}%")
            else:
                labels.append(f"Debt D/E {dte:.0f}%")
        else:
            debt_text = _money_short(total_debt)
            if debt_text:
                labels.append(f"Debt {debt_text}")
    except Exception:
        debt_text = _money_short(total_debt)
        if debt_text:
            labels.append(f"Debt {debt_text}")

    cashflow = f.get("free_cashflow")
    cashflow_label = "Cash Flow"
    if cashflow is None:
        cashflow = f.get("operating_cashflow")
        cashflow_label = "Op Cash Flow"
    cashflow_text = _money_short(cashflow)
    if cashflow_text:
        try:
            labels.append(
                f"{'Positive' if float(cashflow) >= 0 else 'Negative'} {cashflow_label} {cashflow_text}"
            )
        except Exception:
            labels.append(f"{cashflow_label} {cashflow_text}")

    week52_high = f.get("52w_high")
    week52_low = f.get("52w_low")
    try:
        price = float(f.get("price") or 0)
        hi = float(week52_high) if week52_high not in (None, "N/A") else 0
        lo = float(week52_low) if week52_low not in (None, "N/A") else 0
        if price > 0 and hi > lo:
            pos = (price - lo) / (hi - lo) * 100
            if pos > 90:
                labels.append("🏁 Near 52W High")
            elif pos < 15:
                labels.append("🧊 Near 52W Low")
    except Exception:
        pass

    if not labels:
        market_cap = f.get("market_cap")
        pe_ratio = f.get("pe_ratio")
        profit_margin = f.get("profit_margin")
        dividend_yield = f.get("dividend_yield")
        short_pct = f.get("short_pct_float")
        if market_cap and market_cap != "N/A":
            labels.append(f"🏢 MCap {market_cap}")
        if pe_ratio and pe_ratio != "N/A":
            labels.append(f"📊 P/E {pe_ratio}")
        if profit_margin is not None:
            labels.append(f"💰 Margin {profit_margin}%")
        if dividend_yield is not None and dividend_yield > 0:
            labels.append(f"💵 Div {dividend_yield}%")
        if short_pct is not None:
            labels.append(f"🔥 Short {short_pct}%")

    return " | ".join(labels) if labels else "Unavailable"


def _cpr_fields(daily_df: pd.DataFrame, current_price: float) -> dict:
    """Compute Central Pivot Range fields expected by the scanner UI."""
    if daily_df is None or len(daily_df) < 2 or current_price <= 0:
        return {}

    hi_col = _col(daily_df, "high")
    lo_col = _col(daily_df, "low")
    close_col = _col(daily_df, "close")
    open_col = _col(daily_df, "open")

    prev = daily_df.iloc[-2]
    prev_high = float(prev[hi_col])
    prev_low = float(prev[lo_col])
    prev_close = float(prev[close_col])
    if prev_high <= 0 or prev_low <= 0:
        return {}

    p = (prev_high + prev_low + prev_close) / 3
    bc_raw = (prev_high + prev_low) / 2
    tc_raw = 2 * p - bc_raw
    top = max(tc_raw, bc_raw)
    bot = min(tc_raw, bc_raw)
    width_pct = ((top - bot) / p * 100) if p > 0 else 0

    cpr_type = "Normal"
    if width_pct < 0.15:
        cpr_type = "Narrow"
    elif width_pct > 0.5:
        cpr_type = "Wide"

    if current_price > top:
        position = "Above"
    elif current_price < bot:
        position = "Below"
    else:
        position = "Inside"

    interp_map = {
        ("Narrow", "Above"): f"Trending day up - price above TC (${top:.2f})",
        ("Narrow", "Inside"): f"Trending day - inside CPR (${bot:.2f}-${top:.2f})",
        ("Narrow", "Below"): f"Trending day down - price below BC (${bot:.2f})",
        ("Wide", "Above"): f"Range day - above TC (${top:.2f}), may pull back toward TC/P (${top:.2f}/${p:.2f})",
        ("Wide", "Inside"): f"Range day - chop between TC and BC",
        ("Wide", "Below"): f"Range day - below BC (${bot:.2f}), may bounce toward BC/P (${bot:.2f}/${p:.2f})",
        ("Normal", "Above"): f"Bullish bias - above TC (${top:.2f})",
        ("Normal", "Inside"): f"Neutral - inside CPR (${bot:.2f}-${top:.2f})",
        ("Normal", "Below"): f"Bearish bias - below BC (${bot:.2f})",
    }

    atr = calc_atr(daily_df)
    day_open = float(daily_df[open_col].iloc[-1]) if open_col in daily_df.columns else current_price
    cpr_day_result = "Inside CPR; wait"
    cpr_day_entry = cpr_day_stop = cpr_day_t1 = None
    # REVERT_DAY_TRIGGER_V2: additive trigger text. Entry/T1 are anchored to
    # the day's open (Stop stays at the pivot P); current_price is no longer used.
    open_context = (
        f"Open above TC (${top:.2f})" if day_open > top else
        f"Open below BC (${bot:.2f})" if day_open < bot else
        f"Open inside CPR (${bot:.2f}-${top:.2f})"
    )
    cpr_day_trigger_text = f"Break TC ${top:.2f} / BC ${bot:.2f}"
    cpr_day_invalidation_text = "Failed break back inside CPR"
    cpr_day_target_text = f"Breakout side + ${atr * 0.5:.2f}" if atr > 0 else "Next CPR edge"
    cpr_day_ref = f"{open_context}; wait for TC/BC break."
    if position == "Above":
        cpr_day_result = "Trend up" if cpr_type == "Narrow" else (
            "Above CPR; pullback risk" if cpr_type == "Wide" else "Bullish above TC"
        )
        cpr_day_entry = day_open
        cpr_day_stop = p
        cpr_day_t1 = day_open + atr if atr > 0 else day_open + (top - bot)
        if cpr_day_t1 <= day_open and atr > 0:
            cpr_day_t1 = day_open + atr * 0.5
        cpr_day_trigger_text = (
            f"Hold > TC ${top:.2f}" if day_open >= top else f"Reclaim TC ${top:.2f}"
        )
        cpr_day_invalidation_text = f"Back < P ${p:.2f}"
        cpr_day_target_text = f"${cpr_day_t1:.2f}" if cpr_day_t1 is not None else "Open + ATR"
        cpr_day_ref = f"{open_context}; long only while holding above TC/P."
    elif position == "Below":
        cpr_day_result = "Trend down" if cpr_type == "Narrow" else (
            "Below CPR; bounce risk" if cpr_type == "Wide" else "Bearish below BC"
        )
        cpr_day_entry = day_open
        cpr_day_stop = p
        cpr_day_t1 = day_open - atr if atr > 0 else day_open - (top - bot)
        if cpr_day_t1 >= day_open and atr > 0:
            cpr_day_t1 = day_open - atr * 0.5
        cpr_day_trigger_text = (
            f"Hold < BC ${bot:.2f}" if day_open <= bot else f"Lose BC ${bot:.2f}"
        )
        cpr_day_invalidation_text = f"Back > P ${p:.2f}"
        cpr_day_target_text = f"${cpr_day_t1:.2f}" if cpr_day_t1 is not None else "Open - ATR"
        cpr_day_ref = f"{open_context}; short only while staying below BC/P."

    cur = daily_df.iloc[-1]
    cur_high = float(cur[hi_col])
    cur_low = float(cur[lo_col])
    cur_close = float(cur[close_col])
    cur_range = max(cur_high - cur_low, 0.0)
    close_pos = (cur_close - cur_low) / cur_range if cur_range > 0 else 0.5
    atr_for_next = atr if atr > 0 else cur_range
    range_atr = cur_range / atr_for_next if atr_for_next > 0 else 0
    atr_pct = atr_for_next / cur_close * 100 if cur_close > 0 and atr_for_next > 0 else 0

    next_p = (cur_high + cur_low + cur_close) / 3
    next_bc_raw = (cur_high + cur_low) / 2
    next_tc_raw = 2 * next_p - next_bc_raw
    next_top = max(next_tc_raw, next_bc_raw)
    next_bot = min(next_tc_raw, next_bc_raw)
    next_width_pct = ((next_top - next_bot) / next_p * 100) if next_p > 0 else 0
    next_cpr_type = "Normal"
    if next_width_pct < 0.15:
        next_cpr_type = "Narrow"
    elif next_width_pct > 0.5:
        next_cpr_type = "Wide"

    scan_date = _bar_date(daily_df)
    next_day_date = _next_trading_day(scan_date)
    up_trigger = cur_high
    down_trigger = cur_low
    up_target = cur_high + atr_for_next * 0.5
    down_target = cur_low - atr_for_next * 0.5

    if range_atr >= 1.25 and close_pos >= 0.70:
        next_day_outcome = "Trending Bullish"
        next_day_bias = "Extended Bullish"
        next_day_target = up_target
        setup = f"Close near high after {range_atr:.1f} ATR day"
        action = "needs open/hold above high for continuation; losing pivot favors pullback."
    elif range_atr >= 1.25 and close_pos <= 0.30:
        next_day_outcome = "Trending Bearish"
        next_day_bias = "Extended Bearish"
        next_day_target = down_target
        setup = f"Close near low after {range_atr:.1f} ATR day"
        action = "needs open/hold below low for continuation; reclaiming pivot favors bounce."
    elif close_pos >= 0.65 and cur_close >= next_p:
        next_day_outcome = "Bullish"
        next_day_bias = "Bullish Watch"
        next_day_target = up_target
        setup = f"Close strong ({close_pos * 100:.0f}% of range)"
        action = "open above high favors continuation; inside open uses pivot as support test."
    elif close_pos <= 0.35 and cur_close <= next_p:
        next_day_outcome = "Bearish"
        next_day_bias = "Bearish Watch"
        next_day_target = down_target
        setup = f"Close weak ({close_pos * 100:.0f}% of range)"
        action = "open below low favors breakdown; inside open uses pivot as resistance test."
    elif next_cpr_type == "Wide":
        next_day_outcome = "Range"
        next_day_bias = "Range Watch"
        next_day_target = next_p
        setup = "Wide next CPR"
        action = "expect chop or mean reversion unless price clears prior high/low."
    else:
        next_day_outcome = "Neutral"
        next_day_bias = "Neutral Watch"
        next_day_target = next_p
        setup = "Close mid-range"
        action = "inside open is balanced; wait for prior high/low break."

    next_day_ref = f"P {next_p:.2f} / ATR {atr_for_next:.2f}"
    next_day_summary = (
        f"{next_day_date.isoformat()}: {next_day_outcome}. {setup}. ATR ${atr_for_next:.2f} ({atr_pct:.1f}%). "
        f"Open > H ${up_trigger:.2f} points to ${up_target:.2f}; "
        f"open < L ${down_trigger:.2f} points to ${down_target:.2f}; {action}"
    )
    next_day_prediction = next_day_summary

    out = {
        "cpr_type": cpr_type,
        "cpr_tc": _round2(top),
        "cpr_bc": _round2(bot),
        "cpr_p": _round2(p),
        "cpr_position": position,
        "cpr_interpretation": interp_map.get((cpr_type, position), "-"),
        "cpr_day_result": cpr_day_result,
        "cpr_day_entry": _round2(cpr_day_entry) if cpr_day_entry is not None else None,
        "cpr_day_stop": _round2(cpr_day_stop) if cpr_day_stop is not None else None,
        "cpr_day_t1": _round2(cpr_day_t1) if cpr_day_t1 is not None else None,
        "cpr_day_trigger_text": cpr_day_trigger_text,
        "cpr_day_invalidation_text": cpr_day_invalidation_text,
        "cpr_day_target_text": cpr_day_target_text,
        "cpr_day_ref": cpr_day_ref,
        "next_day_date": next_day_date.isoformat(),
        "next_day_outcome": next_day_outcome,
        "next_day_bias": next_day_bias,
        "next_day_summary": next_day_summary,
        "next_day_prediction": next_day_prediction,
        "next_day_open": None,
        "next_day_ref": next_day_ref,
        "next_day_target": _round2(next_day_target) if next_day_target is not None else None,
        "next_day_atr": _round2(atr_for_next),
        "next_day_atr_pct": _round2(atr_pct),
        "next_day_trigger_up": _round2(up_trigger),
        "next_day_trigger_down": _round2(down_trigger),
        "next_day_pivot": _round2(next_p),
        "prev_day_high": _round2(prev_high),
        "prev_day_low": _round2(prev_low),
    }
    if atr > 0:
        out.update({
            "exp_move_up": _round2(current_price + atr),
            "exp_move_down": _round2(current_price - atr),
            "exp_move_pct": _round2(atr / current_price * 100),
            "day_open": _round2(day_open),
            "exp_move_open_up": _round2(day_open + atr),
            "exp_move_open_dn": _round2(day_open - atr),
            "exp_move_open_pct": _round2(atr / day_open * 100) if day_open > 0 else 0,
        })
    return out


def _day_volume_confirm_text(position: str, vol_trend: str | None, vol_surge: bool, vol_ratio: float | None = None) -> str:
    trend = (vol_trend or "").upper()
    ratio_text = f" ({vol_ratio:.1f}x avg)" if isinstance(vol_ratio, (int, float)) and vol_ratio > 0 else ""
    if position == "Above":
        if trend == "ACCUMULATING" and vol_surge:
            return f"Confirmed: accumulating + volume surge{ratio_text}"
        if trend == "ACCUMULATING":
            return f"Supportive: accumulating volume{ratio_text}"
        if trend == "DISTRIBUTING":
            return f"Caution: distribution against long{ratio_text}"
        if vol_surge:
            return f"Watch: volume surge; confirm direction{ratio_text}"
        return "Needs volume: no clear accumulation"
    if position == "Below":
        if trend == "DISTRIBUTING" and vol_surge:
            return f"Confirmed: distributing + volume surge{ratio_text}"
        if trend == "DISTRIBUTING":
            return f"Supportive: distributing volume{ratio_text}"
        if trend == "ACCUMULATING":
            return f"Caution: accumulation against short{ratio_text}"
        if vol_surge:
            return f"Watch: volume surge; confirm direction{ratio_text}"
        return "Needs volume: no clear distribution"
    if vol_surge:
        return f"Watch: volume surge; wait for CPR break{ratio_text}"
    return "Needs volume on CPR break"


def _opening_15m_volume_signal(ticker: str, target_date: date) -> dict:
    start = target_date - timedelta(days=35)
    df = pd.DataFrame()
    try:
        df = get_five_min_bars_alpaca(ticker, str(start), str(target_date), ALPACA_API_KEY, ALPACA_API_SECRET)
    except Exception:
        pass
    if df is None or df.empty:
        try:
            hist = yf.Ticker(ticker).history(
                start=str(start),
                end=str(target_date + timedelta(days=1)),
                interval="5m",
                prepost=False,
            )
            if not hist.empty:
                hist.columns = [c.lower() for c in hist.columns]
                if hist.index.tz is None:
                    hist.index = hist.index.tz_localize("America/New_York")
                else:
                    hist.index = hist.index.tz_convert("America/New_York")
                df = hist[[c for c in ["open", "high", "low", "close", "volume"] if c in hist.columns]]
        except Exception:
            df = pd.DataFrame()
    if df is None or df.empty or "volume" not in df.columns:
        return {
            "cpr_day_15m_volume_text": "15m pending: waiting for opening bars",
            "cpr_day_15m_volume_ratio": None,
            "cpr_day_15m_volume_surge": False,
        }

    idx = pd.to_datetime(df.index)
    if idx.tz is None:
        idx = idx.tz_localize("America/New_York")
    else:
        idx = idx.tz_convert("America/New_York")
    work = df.copy()
    work.index = idx
    mask = (
        (work.index.time >= pd.Timestamp("09:30").time()) &
        (work.index.time < pd.Timestamp("09:45").time())
    )
    opening = work.loc[mask]
    if opening.empty:
        text = "15m pending: waiting for opening bars"
        return {"cpr_day_15m_volume_text": text, "cpr_day_15m_volume_ratio": None, "cpr_day_15m_volume_surge": False}

    grouped = opening.groupby(opening.index.date)["volume"].agg(["sum", "count"])
    if target_date not in grouped.index:
        text = "15m pending: waiting for opening bars"
        return {"cpr_day_15m_volume_text": text, "cpr_day_15m_volume_ratio": None, "cpr_day_15m_volume_surge": False}

    target = grouped.loc[target_date]
    count = int(target["count"])
    if count < 3:
        text = f"15m pending: {count}/3 bars"
        return {"cpr_day_15m_volume_text": text, "cpr_day_15m_volume_ratio": None, "cpr_day_15m_volume_surge": False}

    prior = grouped[(grouped.index < target_date) & (grouped["count"] >= 3) & (grouped["sum"] > 0)]["sum"].tail(10)
    if prior.empty:
        text = "15m baseline pending"
        return {"cpr_day_15m_volume_text": text, "cpr_day_15m_volume_ratio": None, "cpr_day_15m_volume_surge": False}

    avg = float(prior.mean())
    ratio = float(target["sum"]) / avg if avg > 0 else 0.0
    ratio = round(ratio, 2)
    if ratio >= 1.5:
        text = f"15m Surge {ratio:.1f}x avg"
    elif ratio >= 1.1:
        text = f"15m Active {ratio:.1f}x avg"
    elif ratio >= 0.8:
        text = f"15m Normal {ratio:.1f}x avg"
    else:
        text = f"15m Light {ratio:.1f}x avg"
    return {
        "cpr_day_15m_volume_text": text,
        "cpr_day_15m_volume_ratio": ratio,
        "cpr_day_15m_volume_surge": ratio >= 1.5,
    }


_NEXT_EARN_CACHE: dict = {}   # {ticker: (date_cached, value)} — refreshed daily


_SEASONALITY_CACHE: dict = {}   # {(ticker, "YYYY-MM"): dict} — refreshed monthly


def _seasonality_cached(ticker: str) -> dict:
    """Current-month seasonality, cached per ticker per calendar month."""
    today = date.today()
    key = (ticker, f"{today.year}-{today.month:02d}")
    hit = _SEASONALITY_CACHE.get(key)
    if hit is not None:
        return hit
    try:
        val = compute_seasonality(ticker) or {"available": False}
    except Exception:
        val = {"available": False}
    _SEASONALITY_CACHE[key] = val
    return val


def _next_earnings_cached(ticker: str) -> Optional[str]:
    """Next earnings date (YYYY-MM-DD), cached once per calendar day."""
    today = date.today()
    hit = _NEXT_EARN_CACHE.get(ticker)
    if hit and hit[0] == today:
        return hit[1]
    val = None
    try:
        from backend.services.earnings import get_next_earnings_date
        val = get_next_earnings_date(ticker)
    except Exception:
        val = None
    _NEXT_EARN_CACHE[ticker] = (today, val)
    return val


def scan_single(ticker: str, as_of: Optional[str] = None) -> dict:
    try:
        if as_of:
            end = date.fromisoformat(as_of)
            # Roll back to Friday if weekend
            if end.weekday() == 5:   # Saturday
                end = end - timedelta(days=1)
            elif end.weekday() == 6: # Sunday
                end = end - timedelta(days=2)
        else:
            end = date.today()
        start = end - timedelta(days=400)

        daily_df = None
        try:
            daily_df = get_daily_bars_alpaca(ticker, str(start), str(end), ALPACA_API_KEY, ALPACA_API_SECRET)
        except Exception:
            pass

        if daily_df is None or daily_df.empty:
            hist = yf.Ticker(ticker).history(start=str(start), end=str(end + timedelta(days=1)), interval="1d")
            if not hist.empty:
                hist.columns = [c.lower() for c in hist.columns]
                daily_df = hist

        if daily_df is None or len(daily_df) < 30:
            return {"ticker": ticker, "error": "Insufficient data", "score": 0}

        scored      = full_score_pipeline(daily_df)
        verdict     = scored.get("verdict", "NEUTRAL")
        confidence  = scored.get("confidence", "N/A")
        score       = scored.get("score", 0)
        direction   = "SHORT" if verdict in ("BEARISH", "LEAN BEARISH") else "LONG"

        close_col   = _col(daily_df, "close")
        price       = round(float(daily_df[close_col].iloc[-1]), 2)

        grade       = get_entry_grade(score, confidence)
        weekly      = compute_weekly_bias(daily_df)
        daily_b     = compute_daily_bias(daily_df)
        signal      = mtf_signal_action(weekly, daily_b, daily_b)
        trade       = calc_trade_levels(daily_df, verdict, price)

        # Buy-The-Dip EMA structure (per-ticker — own structure only;
        # market-level regime gate lives in the Market Risk bar).
        btd = compute_btd(daily_df, regime_ok=True)

        # Weinstein 30-week MA curl (long-term Stage 1→2 turn).
        w30 = compute_w30(daily_df)

        # Weekly ATR (volatility on weekly bars).
        wkatr = calc_weekly_atr(daily_df)

        # Recent-headline sentiment (Finviz, cached 10 min). Best-effort —
        # never let a news fetch break a scan.
        news_label, news_good, news_bad = "No", 0, 0
        news_headlines: list = []
        try:
            from backend.services.news_sentiment import get_news_details
            _nd = get_news_details(ticker)
            news_label = _nd.get("label", "No")
            news_good  = _nd.get("good_score", 0)
            news_bad   = _nd.get("bad_score", 0)
            news_headlines = [
                {"h": (x.get("headline") or "")[:140],
                 "s": x.get("sentiment", "Neutral"),
                 "src": x.get("source", ""),
                 "t": (f'{x.get("date","")} {x.get("time","")}').strip()}
                for x in (_nd.get("headlines") or [])[:12]
            ]
        except Exception:
            pass

        # Next scheduled earnings date (cached daily — best-effort).
        next_earnings = _next_earnings_cached(ticker)
        # Seasonality is intentionally NOT computed here — it's fetched
        # on-demand via GET /api/scanner/seasonality (kept off the scan
        # hot path; see _seasonality_cached).

        vol_profile     = scored.get("vol_profile") or {}
        strategy_sig    = scored.get("strategy_signals") or {}
        flip_info       = _verdict_flip_info(daily_df, verdict)
        lre             = _low_risk_entry(
            price=price,
            verdict=verdict,
            confidence=confidence,
            mtf_rank=signal["rank"],
            vol_profile=vol_profile,
            vol_trend=vol_profile.get("vol_trend", "N/A"),
            trade=trade,
        )
        fundamentals = get_fundamentals(ticker) or {}
        fundamentals.setdefault("price", price)
        fundamental_signals = _fundamental_signals(ticker, fundamentals)
        valuation = _valuation_estimate(fundamentals)
        cpr = _cpr_fields(daily_df, price)
        cpr["cpr_day_volume_text"] = _day_volume_confirm_text(
            cpr.get("cpr_position", ""),
            vol_profile.get("vol_trend", "N/A"),
            bool(vol_profile.get("vol_surge", False)),
            vol_profile.get("vol_ratio"),
        )
        cpr.update(_opening_15m_volume_signal(ticker, end))

        # Short interest (best-effort)
        short_pct = None
        sector = _sector_for_ticker(ticker)
        try:
            info = yf.Ticker(ticker).info
            v = info.get("shortPercentOfFloat")
            if v is not None:
                short_pct = round(float(v) * 100, 1)
            sector = _sector_for_ticker(ticker, info)
        except Exception:
            pass

        # Options strategy (best-effort — yfinance fallback, no Alpaca keys needed)
        opt_strategy = opt_summary = opt_debit = opt_profit = opt_source = opt_quote_ts = None
        opt_legs = opt_width = opt_exp_short = opt_exp_long = opt_alt = None
        opt_liquid: list = []
        try:
            strat = get_options_strategy(ticker, price, direction, ALPACA_API_KEY, ALPACA_API_SECRET)
            if strat and strat.get("summary"):
                opt_strategy  = strat.get("strategy")
                opt_summary   = strat.get("summary")
                opt_debit     = strat.get("net_debit")
                opt_profit    = strat.get("max_profit")
                opt_source    = strat.get("source")
                opt_quote_ts  = strat.get("quote_ts")
                opt_legs      = strat.get("legs")
                opt_width     = strat.get("width")
                opt_exp_short = strat.get("exp_short")
                opt_exp_long  = strat.get("exp_long")
                opt_alt       = strat.get("alt")
        except Exception:
            pass

        # OTM liquid options (best-effort)
        try:
            bias = get_options_bias(ticker)
            opt_liquid = bias.get("otm_liquid", [])[:5]
        except Exception:
            pass

        # Backtest only: one forward-bar fetch powers both metrics.
        # Next-day close stays long-only (UI labels it so); the swing-plan
        # sim is direction-aware by verdict (long unless bearish).
        bt_scan_date = bt_next_date = bt_next_close = bt_next_chg_pct = None
        bt_next_positive = None
        bt_swing_outcome = bt_swing_r = bt_swing_bars = None
        if as_of:
            scan_d  = _bar_date(daily_df)
            horizon = max(3, min(int(trade.get("t1_days") or 10), 10))
            fwd = _forward_bars(ticker, scan_d, horizon)
            if fwd is not None and not fwd.empty:
                bt_scan_date = scan_d.isoformat()
                ccf = _col(fwd, "close")
                nd_close = round(float(fwd.iloc[0][ccf]), 2)
                if price > 0:
                    bt_next_date     = _idx_date(fwd.index[0]).isoformat()
                    bt_next_close    = nd_close
                    bt_next_chg_pct  = round((nd_close - price) / price * 100, 2)
                    bt_next_positive = bool(nd_close > price)
                sim = _simulate_swing(
                    fwd, direction,
                    trade.get("entry"), trade.get("stop_loss"),
                    trade.get("target1"),
                )
                bt_swing_outcome = sim["outcome"]
                bt_swing_r       = sim["r_mult"]
                bt_swing_bars    = sim["bars"]

        return {
            "ticker":       ticker,
            "sector":       sector,
            "price":        price,
            "verdict":      verdict,
            **flip_info,
            "confidence":   confidence,
            "score":        score,
            "direction":    direction,
            "entry_grade":  grade["entry_grade"],
            "entry_label":  grade["entry_label"],
            "grade_color":  grade["grade_color"],
            "expected_wr":  grade["expected_wr"],
            "mtf_rank":     signal["rank"],
            "mtf_signal":   signal["signal"],
            "mtf_action":   signal["action"],
            "mtf_key":      signal["key"],
            "weekly_bias":  weekly,
            "daily_bias":   daily_b,
            "vol_trend":    vol_profile.get("vol_trend", "N/A"),
            "vol_surge":    vol_profile.get("vol_surge", False),
            "breakout_score": strategy_sig.get("breakout_score", 0),
            "dist_from_high": strategy_sig.get("dist_from_high", None),
            "short_pct":    short_pct,
            "entry":        trade.get("entry"),
            "stop_loss":    trade.get("stop_loss"),
            "target1":      trade.get("target1"),
            "risk_pct":     trade.get("risk_pct"),
            "rr_t1":        trade.get("rr_t1"),
            "atr":          trade.get("atr"),
            "swing_invalidation":      trade.get("swing_invalidation"),
            "swing_invalidation_text": trade.get("swing_invalidation_text"),
            "opt_strategy":  opt_strategy,
            "opt_summary":   opt_summary,
            "opt_debit":     opt_debit,
            "opt_profit":    opt_profit,
            "opt_source":    opt_source,
            "opt_quote_ts":  opt_quote_ts,
            "opt_legs":      opt_legs,
            "opt_width":     opt_width,
            "opt_exp_short": opt_exp_short,
            "opt_exp_long":  opt_exp_long,
            "opt_alt":       opt_alt,
            "opt_liquid":    opt_liquid,
            "btd_state":     btd["btd_state"],
            "btd_zone":      btd["btd_zone"],
            "btd_reason":    btd["btd_reason"],
            "btd_size":      btd["btd_size"],
            "ema20":         btd["ema20"],
            "ema50":         btd["ema50"],
            "ema200":        btd["ema200"],
            "ema50_slope_pct": btd["ema50_slope_pct"],
            "w30ma":         w30["w30ma"],
            "w30ma_curl":    w30["w30ma_curl"],
            "w30ma_slope_pct": w30["w30ma_slope_pct"],
            "w30ma_reason":  w30["w30ma_reason"],
            "wk_atr":        wkatr["wk_atr"],
            "wk_atr_pct":    wkatr["wk_atr_pct"],
            "news":          news_label,
            "news_good":     news_good,
            "news_bad":      news_bad,
            "news_headlines": news_headlines,
            "next_earnings": next_earnings,
            "bt_scan_date":     bt_scan_date,
            "bt_next_date":     bt_next_date,
            "bt_next_close":    bt_next_close,
            "bt_next_chg_pct":  bt_next_chg_pct,
            "bt_next_positive": bt_next_positive,
            "bt_swing_outcome": bt_swing_outcome,
            "bt_swing_r":       bt_swing_r,
            "bt_swing_bars":    bt_swing_bars,
            "signals":       fundamental_signals,
            **valuation,
            **cpr,
            **lre,
            "error":         None,
        }
    except Exception as e:
        return {"ticker": ticker, "error": str(e)[:120], "score": 0}


# ── Parallel scan yielding results as they complete ───────────────────────────

def scan_watchlist_stream(tickers: list[str], max_workers: int = 12) -> Iterator[dict]:
    """Yields scan results one-by-one as each ticker finishes."""
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = {pool.submit(scan_single, t): t for t in tickers}
        for fut in as_completed(futures):
            yield fut.result()
