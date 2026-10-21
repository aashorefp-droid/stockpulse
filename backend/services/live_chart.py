"""
Live Chart Service (backend/services/live_chart.py)
Provides real-time intraday candlestick data, pre-drawn key levels (PDH, PDL, SL, Midpoint, Targets),
and synchronized option contract pricing (default 0 DTE, adjustable strike/type/DTE)
for interactive Lightweight Charts rendering and live streaming.
"""
import time
import math
import logging
from datetime import datetime, date, timedelta
from typing import Optional, Dict, Any, List
import pytz
import yfinance as yf
import pandas as pd

logger = logging.getLogger("live_chart")
ET = pytz.timezone("America/New_York")
CST = pytz.timezone("America/Chicago")

# ── Pure-Python Black-Scholes & Greeks (No scipy dependency) ─────────────────

def _norm_cdf(x: float) -> float:
    """Standard normal cumulative distribution function using math.erf."""
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))

def _norm_pdf(x: float) -> float:
    """Standard normal probability density function."""
    return (1.0 / math.sqrt(2.0 * math.pi)) * math.exp(-0.5 * x * x)

def _black_scholes_price(S: float, K: float, T: float, r: float, sigma: float, option_type: str = "call") -> float:
    """
    Computes Black-Scholes option price.
    S: Underlying price
    K: Strike price
    T: Time to expiration in years
    r: Risk-free rate (e.g. 0.045)
    sigma: Implied volatility (e.g. 0.15)
    """
    if S <= 0 or K <= 0:
        return 0.01
    is_call = option_type.lower() == "call"
    if T <= 0.00001:
        intrinsic = (S - K) if is_call else (K - S)
        return max(0.01, intrinsic)
    if sigma <= 0.0001:
        sigma = 0.0001

    try:
        d1 = (math.log(S / K) + (r + 0.5 * sigma ** 2) * T) / (sigma * math.sqrt(T))
        d2 = d1 - sigma * math.sqrt(T)
        if is_call:
            price = S * _norm_cdf(d1) - K * math.exp(-r * T) * _norm_cdf(d2)
        else:
            price = K * math.exp(-r * T) * _norm_cdf(-d2) - S * _norm_cdf(-d1)
        return max(0.01, price)
    except Exception:
        intrinsic = (S - K) if is_call else (K - S)
        return max(0.01, intrinsic)

def _calc_greeks(S: float, K: float, T: float, r: float, sigma: float, option_type: str = "call") -> Dict[str, float]:
    """Computes Delta, Gamma, Theta for the contract."""
    is_call = option_type.lower() == "call"
    if S <= 0 or K <= 0 or T <= 0.00001 or sigma <= 0.0001:
        delta = 1.0 if (is_call and S > K) else (-1.0 if (not is_call and S < K) else 0.0)
        return {"delta": delta, "gamma": 0.0, "theta": 0.0}

    try:
        sqrt_t = math.sqrt(T)
        d1 = (math.log(S / K) + (r + 0.5 * sigma ** 2) * T) / (sigma * sqrt_t)
        d2 = d1 - sigma * sqrt_t
        pdf_d1 = _norm_pdf(d1)

        delta = _norm_cdf(d1) if is_call else (_norm_cdf(d1) - 1.0)
        gamma = pdf_d1 / (S * sigma * sqrt_t)

        # 1-day theta
        theta_ann = - (S * pdf_d1 * sigma) / (2.0 * sqrt_t)
        if is_call:
            theta_ann -= r * K * math.exp(-r * T) * _norm_cdf(d2)
        else:
            theta_ann += r * K * math.exp(-r * T) * _norm_cdf(-d2)
        theta_day = theta_ann / 365.0

        return {
            "delta": round(delta, 3),
            "gamma": round(gamma, 4),
            "theta": round(theta_day, 3),
        }
    except Exception:
        return {"delta": 0.5 if is_call else -0.5, "gamma": 0.0, "theta": 0.0}


# ── Multi-Tier In-Memory Caching (Extreme High Performance for Live & Historical) ──
_CACHE = {}
_CACHE_TTL = 3.5  # Cache full live chart response for 3.5s to prevent concurrent poll thrashing

_V4_CACHE = {}
_V4_CACHE_TTL = 300.0  # Daily key levels & bias don't change every 5 seconds (5m live, 30m historical)

_DATES_CACHE = {}
_DATES_CACHE_TTL = 300.0  # Available trading days cached for 5 minutes

_OPTIONS_CACHE = {}
_OPTIONS_CACHE_TTL = 30.0  # Option chains cached for 30 seconds

def _log_cache_debug(msg: str):
    logger.debug(f"[live_chart] {msg}")

def _get_cache(key: str) -> Optional[Any]:
    entry = _CACHE.get(key)
    if entry and (time.time() - entry["ts"] < _CACHE_TTL):
        return entry["data"]
    return None

def _set_cache(key: str, data: Any):
    _CACHE[key] = {"ts": time.time(), "data": data}

def _get_v4_data_cached(ticker: str, target_date_clean: Optional[str], is_historical: bool) -> Dict[str, Any]:
    key = f"{ticker}_{target_date_clean}"
    ttl = 1800.0 if is_historical else _V4_CACHE_TTL
    entry = _V4_CACHE.get(key)
    if entry and (time.time() - entry["ts"] < ttl):
        return entry["data"]
    try:
        from day_trading.v4 import get_valid_outcome_and_trade_idea
        v4_data = get_valid_outcome_and_trade_idea(ticker, target_date=target_date_clean)
        _V4_CACHE[key] = {"ts": time.time(), "data": v4_data}
        return v4_data
    except Exception as exc:
        logger.warning(f"[live_chart] Failed to get v4 outcome for {ticker}: {exc}")
        return entry["data"] if entry else {}

def _get_available_dates_cached(tk: yf.Ticker, yf_sym: str) -> List[str]:
    entry = _DATES_CACHE.get(yf_sym)
    if entry and (time.time() - entry["ts"] < _DATES_CACHE_TTL):
        return entry["data"]
    dates = []
    try:
        daily_hist = tk.history(period="60d", interval="1d")
        if not daily_hist.empty:
            for idx in reversed(daily_hist.index):
                d_str = pd.Timestamp(idx).tz_convert("America/New_York").strftime("%Y-%m-%d") if pd.Timestamp(idx).tzinfo else pd.Timestamp(idx).strftime("%Y-%m-%d")
                if d_str not in dates:
                    dates.append(d_str)
        _DATES_CACHE[yf_sym] = {"ts": time.time(), "data": dates}
    except Exception as exc:
        logger.debug(f"[live_chart] Failed to fetch available dates for {yf_sym}: {exc}")
        return entry["data"] if entry else []
    return dates

def _get_option_chain_cached(tk: yf.Ticker, yf_sym: str, expiry: str, opt_type: str) -> tuple[pd.DataFrame, List[float]]:
    key = f"{yf_sym}_{expiry}_{opt_type}"
    entry = _OPTIONS_CACHE.get(key)
    if entry and (time.time() - entry["ts"] < _OPTIONS_CACHE_TTL):
        return entry["chain_df"], entry["available_strikes"]
    try:
        chain = tk.option_chain(expiry)
        chain_df = chain.calls if opt_type == "CALL" else chain.puts
        available_strikes = []
        if not chain_df.empty:
            available_strikes = sorted([float(s) for s in chain_df["strike"].dropna().unique()])
        _OPTIONS_CACHE[key] = {"ts": time.time(), "chain_df": chain_df, "available_strikes": available_strikes}
        return chain_df, available_strikes
    except Exception as exc:
        logger.debug(f"[live_chart] Option chain fetch failed for {yf_sym} {expiry}: {exc}")
        if entry:
            return entry["chain_df"], entry["available_strikes"]
        return pd.DataFrame(), []


def _normalize_ticker(ticker: str) -> tuple[str, str]:
    """Returns (canonical_ticker, yf_symbol)."""
    t = ticker.strip().upper()
    if t in ("SPXW", "SPX", "$SPX", "^SPX", "^GSPC"):
        return t, "^SPX"
    if t in ("NDX", "$NDX", "^NDX"):
        return t, "^NDX"
    return t, t


def _get_historical_expiries(target_date: date, canon_ticker: str) -> List[str]:
    """
    Generates realistic historical option expiration dates starting from target_date.
    Index ETFs & major indices (SPY, SPXW, SPX, QQQ, IWM) have daily expirations (Mon-Fri).
    Individual stocks (UNH, SOXX, AAPL, NVDA, TSLA) have Friday weekly expirations.
    """
    expiries: List[str] = []
    has_daily = canon_ticker in (
        "SPY", "SPXW", "SPX", "$SPX", "^SPX", "^GSPC",
        "QQQ", "IWM", "NDX", "$NDX", "^NDX", "TLT"
    )
    # Check forward up to 45 calendar days from target_date
    for offset in range(45):
        day = target_date + timedelta(days=offset)
        weekday = day.weekday()  # 0=Monday, 4=Friday, 5=Saturday, 6=Sunday
        if weekday >= 5:
            continue  # Weekend
        if offset == 0:
            # 0 DTE is always the session date itself
            expiries.append(day.strftime("%Y-%m-%d"))
        elif has_daily:
            # Daily expirations for index products
            expiries.append(day.strftime("%Y-%m-%d"))
        else:
            # Friday weekly expirations for single stocks/equity ETFs
            if weekday == 4:
                expiries.append(day.strftime("%Y-%m-%d"))
    return expiries[:15]


def get_live_chart_data(
    ticker: str = "SPY",
    timeframe: str = "5m",
    strike: Optional[float] = None,
    option_type: Optional[str] = None,
    dte: Optional[int] = 0,
    custom_expiry: Optional[str] = None,
    extended_hours: bool = True,
    target_date: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Fetches real-time intraday candles, pre-calculated levels (PDH, PDL, SL, Mid, Targets),
    and option contract series (default 0 DTE, adjustable).
    Supports extended hours (Pre-market 4:00 AM - 9:30 AM ET & After-hours 4:00 PM - 8:00 PM ET)
    and backdated historical sessions with bar replay capability.
    """
    canon_ticker, yf_sym = _normalize_ticker(ticker)
    tf = timeframe.lower() if timeframe else "5m"
    if tf not in ("1m", "5m", "15m", "30m", "1h", "1d"):
        tf = "5m"

    now_et = datetime.now(ET)
    now_ct = datetime.now(CST)
    today_et = now_et.date()

    # Parse and validate target_date if provided (supports YYYY-MM-DD or MM/DD/YYYY)
    is_historical = False
    target_date_obj: Optional[date] = None
    target_date_clean: Optional[str] = None

    if target_date:
        try:
            clean_str = str(target_date).strip().replace("/", "-")
            parts = clean_str.split("-")
            if len(parts) == 3:
                if len(parts[0]) == 4:  # YYYY-MM-DD
                    target_date_obj = date(int(parts[0]), int(parts[1]), int(parts[2]))
                else:  # MM-DD-YYYY
                    target_date_obj = date(int(parts[2]), int(parts[0]), int(parts[1]))
                target_date_clean = target_date_obj.strftime("%Y-%m-%d")
                if target_date_obj < today_et:
                    is_historical = True
        except Exception as exc:
            logger.warning(f"[live_chart] Failed to parse target_date '{target_date}': {exc}")
            is_historical = False
            target_date_clean = None

    cache_key = f"{canon_ticker}_{tf}_{strike}_{option_type}_{dte}_{custom_expiry}_{extended_hours}_{target_date_clean}"
    cached = _get_cache(cache_key)
    if cached:
        return cached

    is_weekday = now_et.weekday() < 5
    et_minutes = now_et.hour * 60 + now_et.minute

    if is_historical and target_date_clean:
        market_session = "HISTORICAL_SESSION"
        session_label = f"Historical Session ({target_date_clean})"
    elif not is_weekday:
        market_session = "WEEKEND_CLOSED"
        session_label = "Weekend Closed"
    elif et_minutes < 570:  # Before 9:30 AM ET (8:30 AM CT)
        market_session = "PRE_MARKET"
        session_label = "Pre-Market (Extended Hours)"
    elif et_minutes < 960:  # 9:30 AM - 4:00 PM ET (8:30 AM - 3:00 PM CT)
        market_session = "REGULAR_HOURS"
        session_label = "Regular Market Hours"
    elif et_minutes < 1200:  # 4:00 PM - 8:00 PM ET (3:00 PM - 7:00 PM CT)
        market_session = "AFTER_HOURS"
        session_label = "After-Hours"
    else:
        market_session = "OVERNIGHT_CLOSED"
        session_label = "Overnight Closed"

    # 1. Fetch Key Levels and Trade Idea from Day Trading V4 (Cached for 60s)
    levels_payload = {}
    trade_idea = {}
    four_hour_focus = {}
    v4_data = _get_v4_data_cached(canon_ticker, target_date_clean, is_historical)
    if v4_data:
        levels_payload = v4_data.get("key_levels", {}) or {}
        trade_idea = v4_data.get("trade_idea", {}) or {}
        four_hour_focus = v4_data.get("four_hour_focus", {}) or trade_idea.get("four_hour_focus", {}) or {}

    # Fallback levels if needed
    pdh = float(levels_payload.get("pdh") or 0.0)
    pdl = float(levels_payload.get("pdl") or 0.0)
    pd_mid = float(levels_payload.get("pd_mid") or ((pdh + pdl) / 2.0 if pdh and pdl else 0.0))
    atr = float(levels_payload.get("atr") or 0.0)

    entry_level = trade_idea.get("entry_level") or four_hour_focus.get("trigger_level") or (pdh if "Breakout" in str(four_hour_focus.get("focus_type", "")) else pdl)
    stop_loss = trade_idea.get("stop_loss") or four_hour_focus.get("stop_loss") or (pdl - 0.5 * atr if pdl and atr else None)
    target_1 = trade_idea.get("target_1") or pd_mid or (pdh + 0.5 * atr if pdh and atr else None)
    target_2 = trade_idea.get("target_2") or (pdh if entry_level and pdh > entry_level else (pdh + atr if pdh and atr else None))
    reward_risk = trade_idea.get("reward_risk") or "2.5x"
    action = trade_idea.get("action") or four_hour_focus.get("action") or "BUY CALLS"

    # 2. Fetch Intraday Bars from yfinance
    tf_map = {
        "1m": ("1d", "1m"),
        "5m": ("2d", "5m"),
        "15m": ("5d", "15m"),
        "30m": ("1mo", "30m"),
        "1h": ("1mo", "1h"),
        "1d": ("1y", "1d"),
    }
    period, interval = tf_map.get(tf, ("5d", "5m"))
    use_prepost = extended_hours and tf in ("1m", "5m", "15m", "30m", "1h")

    tk = yf.Ticker(yf_sym)

    # Extract available trading dates from daily history (Cached for 5 mins)
    available_dates: List[str] = _get_available_dates_cached(tk, yf_sym)

    # Fetch bars: either for a backdated historical date or live range
    if is_historical and target_date_obj:
        start_str = target_date_obj.strftime("%Y-%m-%d")
        end_str = (target_date_obj + timedelta(days=2)).strftime("%Y-%m-%d")
        df = tk.history(start=start_str, end=end_str, interval=interval, prepost=use_prepost, auto_adjust=True)
        if df.empty and canon_ticker in ("SPXW", "SPX"):
            # Fallback to cash index
            df = yf.Ticker("^SPX").history(start=start_str, end=end_str, interval=interval, prepost=use_prepost, auto_adjust=True)

        # Filter strictly to target_date_obj
        if not df.empty:
            def _is_target_day(idx):
                ts = pd.Timestamp(idx)
                if ts.tzinfo:
                    return ts.tz_convert("America/New_York").date() == target_date_obj
                return ts.date() == target_date_obj
            df = df[[_is_target_day(i) for i in df.index]]
    else:
        df = tk.history(period=period, interval=interval, prepost=use_prepost, auto_adjust=True)
        if df.empty:
            df = tk.history(period="1d", interval=interval, prepost=use_prepost, auto_adjust=True)

    bars: List[Dict[str, Any]] = []
    volume_bars: List[Dict[str, Any]] = []
    current_price = 0.0

    if not df.empty:
        # Standardize columns
        df.columns = [str(c).capitalize() for c in df.columns]
        for idx, row in df.iterrows():
            ts = int(pd.Timestamp(idx).timestamp())
            o = float(row.get("Open", 0.0))
            h = float(row.get("High", 0.0))
            l = float(row.get("Low", 0.0))
            c = float(row.get("Close", 0.0))
            v = int(row.get("Volume", 0))

            bars.append({
                "time": ts,
                "open": round(o, 2),
                "high": round(h, 2),
                "low": round(l, 2),
                "close": round(c, 2),
            })
            volume_bars.append({
                "time": ts,
                "value": v,
                "color": "rgba(38, 166, 154, 0.5)" if c >= o else "rgba(239, 83, 80, 0.5)",
            })

        current_price = bars[-1]["close"] if bars else 0.0

    # For SPXW / SPX during pre-market: bridge ES=F (S&P 500 Futures) so today's live pre-market candles display
    if canon_ticker in ("SPXW", "SPX") and extended_hours and tf != "1d":
        try:
            if market_session == "PRE_MARKET":
                es_tk = yf.Ticker("ES=F")
                es_df = es_tk.history(period="1d", interval=interval, auto_adjust=True)
                if not es_df.empty and not df.empty:
                    last_spx = float(df["Close"].iloc[-1])
                    first_es = float(es_df["Open"].iloc[0])
                    es_ratio = (last_spx / first_es) if first_es > 0 else 1.0
                    today_et_date = now_et.date()
                    for idx, row in es_df.iterrows():
                        bar_dt = pd.Timestamp(idx).tz_convert("America/New_York")
                        if bar_dt.date() == today_et_date:
                            ts = int(bar_dt.timestamp())
                            if not any(b["time"] == ts for b in bars):
                                o = round(float(row["Open"]) * es_ratio, 2)
                                h = round(float(row["High"]) * es_ratio, 2)
                                l = round(float(row["Low"]) * es_ratio, 2)
                                c = round(float(row["Close"]) * es_ratio, 2)
                                bars.append({
                                    "time": ts,
                                    "open": o,
                                    "high": max(o, h, c),
                                    "low": min(o, l, c),
                                    "close": c,
                                    "is_premarket": True,
                                })
                                volume_bars.append({
                                    "time": ts,
                                    "value": int(row.get("Volume", 0)),
                                    "color": "rgba(234, 179, 8, 0.45)",
                                })
                    if bars:
                        current_price = bars[-1]["close"]
        except Exception as exc:
            logger.debug(f"[live_chart] ES futures bridge fallback failed: {exc}")

    # If PDH/PDL weren't present, compute from the daily history
    if (not pdh or not pdl) and not df.empty:
        try:
            daily_df = tk.history(period="5d", interval="1d")
            if len(daily_df) >= 2:
                prev_bar = daily_df.iloc[-2]
                pdh = round(float(prev_bar["High"]), 2)
                pdl = round(float(prev_bar["Low"]), 2)
                pd_mid = round((pdh + pdl) / 2.0, 2)
        except Exception:
            pass

    # 3. Option Expiry & Chain Handling (Default 0 DTE, Adjustable)
    available_expiries: List[str] = []
    if is_historical and target_date_obj:
        available_expiries = _get_historical_expiries(target_date_obj, canon_ticker)
    else:
        try:
            available_expiries = list(tk.options or [])
        except Exception as exc:
            logger.debug(f"[live_chart] Failed to fetch options list for {yf_sym}: {exc}")

    chosen_expiry = custom_expiry
    if is_historical and target_date_obj and target_date_clean:
        if custom_expiry and custom_expiry in available_expiries:
            chosen_expiry = custom_expiry
        elif dte is not None and dte > 0:
            target_future_date = target_date_obj + timedelta(days=dte)
            target_future_str = target_future_date.strftime("%Y-%m-%d")
            matching = [exp for exp in available_expiries if exp >= target_future_str]
            chosen_expiry = matching[0] if matching else (available_expiries[-1] if available_expiries else target_date_clean)
        else:
            # Default to 0 DTE (same date as historical session)
            chosen_expiry = target_date_clean
    elif not chosen_expiry and available_expiries:
        now_date_str = datetime.now(ET).strftime("%Y-%m-%d")
        if dte == 0 or dte is None:
            # First available expiry (today or next open session)
            chosen_expiry = available_expiries[0]
        else:
            # Find expiry closest to now + dte
            future_calc_date = (datetime.now(ET) + timedelta(days=dte)).strftime("%Y-%m-%d")
            matching = [exp for exp in available_expiries if exp >= future_calc_date]
            chosen_expiry = matching[0] if matching else available_expiries[-1]

    # Calculate actual DTE from chosen_expiry
    actual_dte = 0
    if chosen_expiry:
        try:
            exp_date = datetime.strptime(chosen_expiry, "%Y-%m-%d").date()
            base_date = target_date_obj if is_historical and target_date_obj else datetime.now(ET).date()
            actual_dte = max(0, (exp_date - base_date).days)
        except Exception:
            actual_dte = dte or 0

    # Determine Option Type (CALL vs PUT vs BOTH)
    chosen_type = "CALL"
    if option_type:
        chosen_type = option_type.upper()
    elif "PUT" in action.upper() or "SHORT" in str(four_hour_focus.get("side", "")).upper():
        chosen_type = "PUT"

    # Determine Strike: Default to ATM or closest to current / opening price
    strike_step = 5.0 if canon_ticker in ("SPXW", "SPX") else (1.0 if (current_price or 100) < 300 else 2.5)
    ref_price = current_price or (bars[0]["open"] if bars else (pdh if pdh else 100.0))

    # Fetch available strikes from option chain if live
    available_strikes: List[float] = []
    if chosen_expiry and not is_historical:
        chain_type_for_strikes = "PUT" if chosen_type == "PUT" else "CALL"
        _, available_strikes = _get_option_chain_cached(tk, yf_sym, chosen_expiry, chain_type_for_strikes)

    if not available_strikes:
        center = round(ref_price / strike_step) * strike_step
        available_strikes = sorted([round(center + i * strike_step, 2) for i in range(-15, 16)])

    chosen_strike = strike
    if chosen_strike is None or (available_strikes and chosen_strike not in available_strikes):
        chosen_strike = min(available_strikes, key=lambda s: abs(s - ref_price))

    # 4. Synthesize Synchronized Option Contract Bars (for CALL and PUT)
    risk_free_rate = 0.045
    expiry_dt = None
    if chosen_expiry:
        try:
            expiry_dt = ET.localize(datetime.strptime(f"{chosen_expiry} 16:00:00", "%Y-%m-%d %H:%M:%S"))
        except Exception:
            pass

    def _build_contract_data(opt_type: str, opt_strike: float) -> Dict[str, Any]:
        opt_type = opt_type.upper()
        c_chain_df = pd.DataFrame()
        if chosen_expiry and not is_historical:
            c_chain_df, _ = _get_option_chain_cached(tk, yf_sym, chosen_expiry, opt_type)

        c_last = 0.0
        c_bid = 0.0
        c_ask = 0.0
        c_iv = 0.15
        c_vol = 0
        c_oi = 0
        c_sym = f"{canon_ticker}_{chosen_expiry}_{opt_type}_{opt_strike}"

        if not c_chain_df.empty and opt_strike is not None:
            matched = c_chain_df[c_chain_df["strike"] == opt_strike]
            if not matched.empty:
                row = matched.iloc[0]
                c_sym = str(row.get("contractSymbol", c_sym))
                c_last = float(row.get("lastPrice") or 0.0)
                c_bid = float(row.get("bid") or 0.0)
                c_ask = float(row.get("ask") or 0.0)
                raw_iv = float(row.get("impliedVolatility") or 0.0)
                c_iv = raw_iv if raw_iv > 0.005 else 0.15
                c_vol = int(row.get("volume") or 0)
                c_oi = int(row.get("openInterest") or 0)

        raw_synth = []
        for b in bars:
            b_dt = datetime.fromtimestamp(b["time"], tz=pytz.UTC).astimezone(ET)
            if expiry_dt and expiry_dt > b_dt:
                time_remaining_sec = (expiry_dt - b_dt).total_seconds()
                t_years = max(0.0001, time_remaining_sec / (365.25 * 86400.0))
            else:
                t_years = max(0.0001, actual_dte / 365.25)

            p_open = _black_scholes_price(b["open"], opt_strike, t_years, risk_free_rate, c_iv, opt_type)
            p_high = _black_scholes_price(b["high"] if opt_type == "CALL" else b["low"], opt_strike, t_years, risk_free_rate, c_iv, opt_type)
            p_low = _black_scholes_price(b["low"] if opt_type == "CALL" else b["high"], opt_strike, t_years, risk_free_rate, c_iv, opt_type)
            p_close = _black_scholes_price(b["close"], opt_strike, t_years, risk_free_rate, c_iv, opt_type)

            raw_synth.append({
                "time": b["time"],
                "open": p_open,
                "high": max(p_open, p_high, p_close),
                "low": min(p_open, p_low, p_close),
                "close": p_close,
            })

        # Calibrate to real market quote if available
        ref_quote = c_last or ((c_bid + c_ask) / 2.0 if c_bid and c_ask else 0.0)
        cal_ratio = 1.0
        if raw_synth and ref_quote > 0.05 and raw_synth[-1]["close"] > 0.01:
            cal_ratio = ref_quote / raw_synth[-1]["close"]
            cal_ratio = max(0.4, min(2.5, cal_ratio))

        c_bars = []
        for sb in raw_synth:
            o = round(sb["open"] * cal_ratio, 2)
            h = round(sb["high"] * cal_ratio, 2)
            l = round(sb["low"] * cal_ratio, 2)
            c = round(sb["close"] * cal_ratio, 2)
            c_bars.append({
                "time": sb["time"],
                "value": c,
                "open": max(0.01, o),
                "high": max(0.01, h),
                "low": max(0.01, l),
                "close": max(0.01, c),
            })

        cur_price = ref_quote or (c_bars[-1]["close"] if c_bars else 0.0)
        t_rem = max(0.0001, actual_dte / 365.25)
        c_greeks = _calc_greeks(current_price or ref_price, opt_strike, t_rem, risk_free_rate, c_iv, opt_type)

        return {
            "symbol": c_sym,
            "description": f"{canon_ticker} {chosen_expiry} ${opt_strike:.2f} {opt_type} ({actual_dte} DTE)",
            "expiry": chosen_expiry,
            "dte": actual_dte,
            "strike": opt_strike,
            "option_type": opt_type,
            "last_price": round(cur_price, 2),
            "bid": round(c_bid, 2),
            "ask": round(c_ask, 2),
            "iv": round(c_iv * 100.0, 1),
            "delta": c_greeks.get("delta", 0.0),
            "gamma": c_greeks.get("gamma", 0.0),
            "theta": c_greeks.get("theta", 0.0),
            "volume": c_vol,
            "open_interest": c_oi,
            "bars": c_bars,
        }

    call_contract = _build_contract_data("CALL", chosen_strike)
    put_contract = _build_contract_data("PUT", chosen_strike)

    if chosen_type == "PUT":
        primary_contract = put_contract
    elif chosen_type == "BOTH":
        primary_contract = call_contract
    else:
        primary_contract = call_contract

    focus_badge = four_hour_focus.get("badge_with_levels") or four_hour_focus.get("badge") or ""
    focus_rule = four_hour_focus.get("rule") or ""

    result = {
        "ticker": canon_ticker,
        "yf_symbol": yf_sym,
        "timeframe": tf,
        "current_price": round(current_price, 2),
        "timestamp": int(time.time()),
        "target_date": target_date_clean if is_historical else None,
        "is_historical": is_historical,
        "available_dates": available_dates[:30],
        "backtest": v4_data.get("backtest") if isinstance(v4_data, dict) else None,
        "levels": {
            "pdh": round(pdh, 2) if pdh else None,
            "pdl": round(pdl, 2) if pdl else None,
            "pd_mid": round(pd_mid, 2) if pd_mid else None,
            "atr": round(atr, 2) if atr else None,
            "entry": round(entry_level, 2) if entry_level else None,
            "stop_loss": round(stop_loss, 2) if stop_loss else None,
            "target_1": round(target_1, 2) if target_1 else None,
            "target_2": round(target_2, 2) if target_2 else None,
            "reward_risk": reward_risk,
            "action": action,
            "valid_outcome": v4_data.get("valid_outcome") if isinstance(v4_data, dict) else None,
            "focus_badge": focus_badge,
            "focus_rule": focus_rule,
        },
        "bars": bars,
        "volume_bars": volume_bars,
        "contract": primary_contract,
        "call_contract": call_contract,
        "put_contract": put_contract,
        "option_mode": chosen_type,
        "available_expiries": available_expiries[:15],
        "available_strikes": available_strikes,
        "session": {
            "status": market_session,
            "label": session_label,
            "is_historical": is_historical,
            "target_date": target_date_clean if is_historical else None,
            "current_time_et": now_et.strftime("%I:%M %p ET"),
            "current_time_ct": now_ct.strftime("%I:%M %p CT"),
            "regular_open_et": "09:30 AM ET",
            "regular_open_ct": "08:30 AM CT",
            "regular_close_et": "04:00 PM ET",
            "regular_close_ct": "03:00 PM CT",
            "validation_ct": "08:35 AM CT",
            "extended_hours": extended_hours,
        },
    }

    _set_cache(cache_key, result)
    return result
