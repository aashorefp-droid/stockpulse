"""
Sector Scan — Stock scanning engine with scoring, classification, and multi-timeframe bias.

Extracted from stock_pulse.py. Contains:
  - Shared scoring engine (_compute_verdict_confidence_score)
  - Instrument classification (_classify_instrument, _is_instrument_supported)
  - Entry grading (ENTRY_GRADE_TABLE, _get_entry_grade)
  - Multi-timeframe bias (_compute_weekly_bias, _compute_4h_bias, _mtf_signal_action)
  - Single/multi stock scanning (scan_single_stock, scan_stocks)
  - SCAN_WATCHLIST default list

External dependencies (analysis functions from main script) must be configured
via configure() before calling scan_single_stock().
"""

from datetime import date, timedelta
import numpy as np
import pandas as pd

# ── yfinance availability ─────────────────────────────────────────────────────
try:
    import yfinance as yf
    YFINANCE_AVAILABLE = True
except ImportError:
    YFINANCE_AVAILABLE = False

# ── External function references (set via configure()) ────────────────────────
_deps = {
    "get_daily_bars_alpaca": None,
    "get_daily_bars": None,
    "get_hourly_bars": None,
    "get_hourly_bars_alpaca": None,
    "get_hourly_bars_yfinance": None,
    "nearest_fib": None,
    "calc_support_resistance": None,
    "analyze_volume_profile": None,
    "analyze_strategy_signals": None,
    "get_fundamentals": None,
}


def configure(**kwargs):
    """
    Set external function references required by the scanner.
    Call once at startup from the main script.

    Example:
        sector_scan.configure(
            get_daily_bars_alpaca=get_daily_bars_alpaca,
            get_daily_bars=get_daily_bars,
            ...
        )
    """
    for key, val in kwargs.items():
        if key in _deps:
            _deps[key] = val


def _dep(name):
    """Get a configured dependency; raise if not set."""
    fn = _deps.get(name)
    if fn is None:
        raise RuntimeError(f"sector_scan: dependency '{name}' not configured. Call sector_scan.configure() first.")
    return fn


# ══════════════════════════════════════════════════════════════════════════════
# SHARED SCORING ENGINE
# ══════════════════════════════════════════════════════════════════════════════

def _compute_verdict_confidence_score(signals, signal_names, primary_candle_bias, vol_profile):
    """
    Single source of truth for verdict, confidence, and score.
    Returns (verdict, confidence, score, signal_names).
    """
    vol_trend = vol_profile["vol_trend"] if vol_profile else "FLAT"
    vol_bias  = vol_profile["vol_bias"]  if vol_profile else "NEUTRAL"

    if vol_trend == "DISTRIBUTING":
        if vol_bias == "BULLISH":
            signals.append(-1); signal_names.append("VolTrend:DIST-")
        elif vol_bias == "BEARISH":
            signals.append(1);  signal_names.append("VolTrend:DIST+")

    if not signals:
        return "NEUTRAL", "N/A", 0, signal_names

    score = sum(signals)

    if score >= 3:
        verdict = "BULLISH"
    elif score <= -3:
        verdict = "BEARISH"
    elif score >= 2:
        verdict = "LEAN BULLISH"
    elif score <= -2:
        verdict = "LEAN BEARISH"
    elif score > 0:
        verdict = "LEAN BULLISH"
    elif score < 0:
        verdict = "LEAN BEARISH"
    else:
        verdict = "NEUTRAL"

    bullish_count = sum(1 for s in signals if s > 0)
    bearish_count = sum(1 for s in signals if s < 0)
    if primary_candle_bias == "BULLISH":
        divergent = bearish_count
    elif primary_candle_bias == "BEARISH":
        divergent = bullish_count
    else:
        divergent = 0

    if primary_candle_bias in ("BULLISH", "BEARISH"):
        if abs(score) >= 4 and divergent == 0:
            confidence = "HIGH"
        elif abs(score) >= 3 and divergent <= 1:
            confidence = "HIGH"
        elif divergent == 0:
            confidence = "MEDIUM"
        elif divergent == 1:
            confidence = "MEDIUM"
        else:
            confidence = "LOW"
    else:
        confidence = "N/A"

    return verdict, confidence, score, signal_names


def _calc_rsi(close_series, period=14):
    """Fast RSI calculation using EWM."""
    delta = close_series.diff()
    gain  = delta.clip(lower=0).ewm(com=period - 1, min_periods=period).mean()
    loss  = (-delta.clip(upper=0)).ewm(com=period - 1, min_periods=period).mean()
    rsi = pd.Series(
        np.where(loss == 0, 100.0, np.where(gain == 0, 0.0, 100 - 100 / (1 + gain / loss))),
        index=close_series.index,
    )
    rsi[gain.isna()] = np.nan
    return rsi


# ── Default scan watchlist ────────────────────────────────────────────────────
SCAN_WATCHLIST = [
    "AAPL", "MSFT", "GOOGL", "AMZN", "NVDA", "META", "TSLA", "AMD", "NFLX", "CRM",
    "ORCL", "ADBE", "INTC", "PYPL", "SQ", "SHOP", "COIN", "UBER", "ABNB", "SNOW",
    "BA", "CAT", "GS", "JPM", "V", "MA", "DIS", "NKE", "SBUX", "MCD",
    "XOM", "CVX", "PFE", "JNJ", "UNH", "MRNA", "LLY", "ABBV", "BMY", "MRK",
    "SPY", "QQQ", "DIA", "XLF", "XLE", "XLK", "ARKK", "SOXX", "SMH"
]

EXCLUDED_INSTRUMENTS = set()


# ══════════════════════════════════════════════════════════════════════════════
# INSTRUMENT CLASSIFICATION
# ══════════════════════════════════════════════════════════════════════════════

def _is_instrument_supported(ticker: str, daily_df) -> tuple:
    """
    Check whether the current instrument is suitable for the trend-following model.
    Returns (supported: bool, reason: str).
    """
    t = ticker.upper().strip()

    if t in EXCLUDED_INSTRUMENTS:
        return False, (f"{t} is in EXCLUDED_INSTRUMENTS — "
                       f"trend signals not reliable on this instrument")

    if daily_df is not None and not daily_df.empty and len(daily_df) >= 40:
        profile = _classify_instrument(t, daily_df)
        if profile["is_mean_rev"]:
            pct   = profile["atr_pct"] * 100
            pers  = profile["persistence"] * 100
            qt    = profile["quote_type"]
            return True, (
                f"{t} classified as mean-reverting "
                f"(ATR%={pct:.1f}%, persistence={pers:.0f}%, type={qt}). "
                f"Trend-following signals unreliable — backtest before trading."
            )

    return True, ""


def _classify_instrument(ticker: str, daily_df) -> dict:
    """
    Dynamically classify an instrument's behaviour from its price history.
    Returns dict with atr_14, atr_pct, persistence, is_mean_rev, quote_type.
    """
    close  = daily_df["close"].values.astype(float)
    high   = daily_df["high"].values.astype(float)
    low    = daily_df["low"].values.astype(float)

    # ATR-14
    if len(close) >= 15:
        tr = np.maximum(high[1:] - low[1:],
             np.maximum(np.abs(high[1:] - close[:-1]),
                        np.abs(low[1:]  - close[:-1])))
        atr_14 = float(np.mean(tr[-14:]))
    else:
        atr_14 = float(np.mean(high - low))
    atr_pct = atr_14 / float(close[-1]) if close[-1] > 0 else 0.0

    # Trend persistence — last 60 trading days
    if len(close) >= 40:
        directions = np.sign(close[1:] - close[:-1])
        d = directions[-60:]
        continuations = int(np.sum((d[1:] == d[:-1]) & (d[:-1] != 0)))
        total_moves   = int(np.sum(d[:-1] != 0))
        persistence   = continuations / total_moves if total_moves > 0 else 0.5
    else:
        persistence = 0.5

    # quoteType from yfinance
    quote_type = "UNKNOWN"
    if YFINANCE_AVAILABLE:
        try:
            qt = yf.Ticker(ticker).info.get("quoteType", "UNKNOWN")
            quote_type = qt.upper() if qt else "UNKNOWN"
        except Exception:
            pass

    if quote_type == "ETF":
        is_mean_rev = True
    elif quote_type == "EQUITY":
        is_mean_rev = False
    else:
        is_mean_rev = (persistence < 0.50 and 0.010 < atr_pct <= 0.020)

    return {
        "atr_14":      round(atr_14, 4),
        "atr_pct":     round(atr_pct, 4),
        "persistence": round(persistence, 3),
        "is_mean_rev": is_mean_rev,
        "quote_type":  quote_type,
    }


# ══════════════════════════════════════════════════════════════════════════════
# ENTRY GRADING
# ══════════════════════════════════════════════════════════════════════════════

ENTRY_GRADE_TABLE = {
    (5, "HIGH"):   ("S",  "STRONG ENTER",  100, 3.09,  "#00e5a0"),
    (4, "HIGH"):   ("A",  "ENTER",          86, 1.19,  "#00e5a0"),
    (3, "HIGH"):   ("B",  "ENTER",          73, 0.47,  "#4d9fff"),
    (3, "MEDIUM"): ("B-", "ENTER",          67, 0.35,  "#4d9fff"),
    (2, "HIGH"):   ("C",  "CAUTION",        50,-0.05,  "#f0c040"),
    (2, "MEDIUM"): ("C",  "CAUTION",        43,-0.46,  "#f0c040"),
    (1, "HIGH"):   ("D",  "WEAK — SKIP",    33,-0.76,  "#ff8c42"),
    (1, "MEDIUM"): ("D",  "WEAK — SKIP",    33,-0.76,  "#ff8c42"),
}


def _get_entry_grade(score: int, confidence: str) -> dict:
    """Return backtest-derived entry grade for a given score + confidence."""
    key = (abs(score), confidence)
    if key in ENTRY_GRADE_TABLE:
        grade, label, wr, avg_pnl, color = ENTRY_GRADE_TABLE[key]
    elif abs(score) >= 4 and confidence == "HIGH":
        grade, label, wr, avg_pnl, color = "A", "ENTER", 86, 1.19, "#00e5a0"
    elif abs(score) >= 3:
        grade, label, wr, avg_pnl, color = "B", "ENTER", 67, 0.35, "#4d9fff"
    elif abs(score) == 2:
        grade, label, wr, avg_pnl, color = "C", "CAUTION", 47, -0.25, "#f0c040"
    else:
        grade, label, wr, avg_pnl, color = "D", "WEAK — SKIP", 33, -0.76, "#ff8c42"
    return {
        "entry_grade":    grade,
        "entry_label":    label,
        "expected_wr":    wr,
        "expected_avg":   avg_pnl,
        "grade_color":    color,
    }


# ══════════════════════════════════════════════════════════════════════════════
# MULTI-TIMEFRAME BIAS
# ══════════════════════════════════════════════════════════════════════════════

def _compute_weekly_bias(daily_df):
    """Weekly trend direction from daily data."""
    try:
        if daily_df.empty or len(daily_df) < 10:
            return "NEUTRAL"
        df = daily_df.copy()
        df.index = pd.to_datetime(df.index)
        weekly = df.resample("W").agg({"open": "first", "high": "max", "low": "min", "close": "last"}).dropna()
        if len(weekly) < 3:
            return "NEUTRAL"
        curr_close = float(weekly["close"].iloc[-1])
        prev_high = float(weekly["high"].iloc[-2])
        prev_low = float(weekly["low"].iloc[-2])
        hh = weekly["high"].iloc[-3:].tolist()
        hl = weekly["low"].iloc[-3:].tolist()
        uptrend = (hh[-1] > hh[-2] > hh[-3]) or (hl[-1] > hl[-2])
        downtrend = (hh[-1] < hh[-2] < hh[-3]) or (hl[-1] < hl[-2])
        if curr_close > prev_high or uptrend:
            return "BULLISH"
        elif curr_close < prev_low or downtrend:
            return "BEARISH"
        return "NEUTRAL"
    except Exception:
        return "NEUTRAL"


def _compute_4h_bias(ticker, daily_df, api_key, api_secret, data_source):
    """
    4H entry trigger — check latest 4H candle direction.
    Uses configured hourly data fetchers via _deps.
    """
    try:
        end_date = date.today()
        start_4h = end_date - timedelta(days=5)
        hourly_df = pd.DataFrame()

        get_hourly_yf = _deps.get("get_hourly_bars_yfinance")
        if YFINANCE_AVAILABLE and get_hourly_yf:
            try:
                hourly_df = get_hourly_yf(ticker, str(start_4h), str(end_date))
            except Exception:
                pass

        if hourly_df.empty:
            try:
                if data_source == "Alpaca":
                    fn = _deps.get("get_hourly_bars_alpaca")
                    if fn:
                        hourly_df = fn(ticker, str(start_4h), str(end_date), api_key, api_secret)
                else:
                    fn = _deps.get("get_hourly_bars")
                    if fn:
                        hourly_df = fn(ticker, str(start_4h), str(end_date), api_key)
            except Exception:
                pass

        if not hourly_df.empty and len(hourly_df) >= 4:
            hdf = hourly_df.copy()
            hdf.index = pd.to_datetime(hdf.index)
            bars_4h = hdf.resample("4h").agg({"open": "first", "high": "max", "low": "min", "close": "last"}).dropna()
            if len(bars_4h) >= 2:
                last = bars_4h.iloc[-1]
                prev = bars_4h.iloc[-2]
                last_bull = float(last["close"]) > float(last["open"])
                pullback_bull = (float(prev["close"]) < float(prev["open"])) and last_bull
                breakout_bull = float(last["close"]) > float(prev["high"])
                last_bear = float(last["close"]) < float(last["open"])
                pullback_bear = (float(prev["close"]) > float(prev["open"])) and last_bear
                breakout_bear = float(last["close"]) < float(prev["low"])
                if pullback_bull or breakout_bull or last_bull:
                    return "BULLISH"
                elif pullback_bear or breakout_bear or last_bear:
                    return "BEARISH"
                return "NEUTRAL"

        # Fallback: use last 2 daily candles as proxy
        if len(daily_df) >= 2:
            last_d = daily_df.iloc[-1]
            if float(last_d["close"]) > float(last_d["open"]):
                return "BULLISH"
            elif float(last_d["close"]) < float(last_d["open"]):
                return "BEARISH"
        return "NEUTRAL"
    except Exception:
        return "NEUTRAL"


def _mtf_signal_action(w, d, h4):
    """
    Map Weekly / Daily / 4H bias combo to (rank, signal, action).
    rank: 1 = best (all aligned), 5 = no edge.
    """
    key = (
        "B" if "BULLISH" in (w or "") else ("R" if "BEARISH" in (w or "") else "N"),
        "B" if "BULLISH" in (d or "") else ("R" if "BEARISH" in (d or "") else "N"),
        "B" if "BULLISH" in (h4 or "") else ("R" if "BEARISH" in (h4 or "") else "N"),
    )
    _MAP = {
        ("B","B","B"): (1, "A+ Long",  "Full size CALL — all TFs agree"),
        ("R","R","R"): (1, "A+ Short", "Full size PUT — all TFs agree"),
        ("B","B","N"): (2, "Strong Long, wait 4H",    "Long confirmed — wait for 4H trigger"),
        ("B","N","B"): (2, "Long pullback entry",     "Weekly up, daily pausing, 4H triggering — dip buy"),
        ("N","B","B"): (2, "Short-term Long",         "No weekly trend — smaller size, quick target"),
        ("R","R","N"): (2, "Strong Short, wait 4H",   "Short confirmed — wait for 4H breakdown"),
        ("R","N","R"): (2, "Short pullback entry",    "Weekly down, daily pausing, 4H confirming"),
        ("N","R","R"): (2, "Short-term Short",        "No weekly trend — smaller size PUT"),
        ("B","B","R"): (3, "Pullback in uptrend",     "4H dip in bull trend — buy-the-dip if support holds"),
        ("R","R","B"): (3, "Dead cat bounce",         "4H bounce in downtrend — fade rally or wait"),
        ("B","R","B"): (3, "Choppy / reversal fight", "Mixed signals — reduce size"),
        ("R","B","R"): (3, "Counter-trend failing",   "Daily bounce but 4H rejecting — likely resumes down"),
        ("B","R","R"): (3, "Trend reversal warning",  "Weekly up but D+4H selling — no longs"),
        ("R","B","B"): (3, "Counter-trend bounce",    "D+4H bouncing vs weekly down — risky long, tight stop"),
        ("B","N","N"): (4, "Too early — Long",        "Weekly up, no confirmation — watchlist only"),
        ("N","B","N"): (4, "Unconfirmed Long",        "Only daily bullish — need weekly or 4H"),
        ("N","N","B"): (4, "Noise — Long",            "Only 4H up — likely just a bounce"),
        ("R","N","N"): (4, "Too early — Short",       "Weekly down, no confirmation — watchlist"),
        ("N","R","N"): (4, "Unconfirmed Short",       "Only daily bearish — need more"),
        ("N","N","R"): (4, "Noise — Short",           "Only 4H down — likely just a dip"),
        ("B","R","N"): (4, "Conflicted",              "Weekly up, daily down — wait for resolution"),
        ("B","N","R"): (4, "4H selling in uptrend",   "Watch for 4H reversal candle — possible dip buy"),
        ("R","B","N"): (4, "Counter-trend attempt",   "Daily bouncing vs weekly down — risky, sit out"),
        ("R","N","B"): (4, "4H bounce in downtrend",  "Likely dead cat — wait for daily confirm"),
        ("N","B","R"): (4, "Daily up, 4H failing",    "4H rejecting — daily move may exhaust"),
        ("N","R","B"): (4, "Daily down, 4H bouncing", "Speculative bottom fish — very small size only"),
        ("N","N","N"): (5, "No edge",                 "Sit out — no directional conviction"),
    }
    return _MAP.get(key, (5, "Unknown", "No data"))


# ══════════════════════════════════════════════════════════════════════════════
# STOCK SCANNER
# ══════════════════════════════════════════════════════════════════════════════

def scan_single_stock(ticker, api_key, api_secret, data_source, use_fib=True, fib_tol=2.0, use_strategy=False):
    """
    Scan a single stock and return verdict/confidence.
    Returns dict with ticker, verdict, confidence, score, signals or None on error.
    """
    try:
        end_date = date.today()
        start_date = end_date - timedelta(days=365)

        # Fetch daily data
        if data_source == "Alpaca":
            daily_df = _dep("get_daily_bars_alpaca")(ticker, str(start_date), str(end_date), api_key, api_secret)
        else:
            daily_df = _dep("get_daily_bars")(ticker, str(start_date), str(end_date), api_key)

        # Fallback to yfinance
        if (daily_df is None or daily_df.empty or len(daily_df) < 20) and YFINANCE_AVAILABLE:
            try:
                _yf = yf.Ticker(ticker)
                _yf_hist = _yf.history(period="1y", auto_adjust=True)
                if _yf_hist is not None and not _yf_hist.empty and len(_yf_hist) >= 20:
                    daily_df = _yf_hist.rename(columns={
                        "Open": "open", "High": "high", "Low": "low",
                        "Close": "close", "Volume": "volume"
                    })[["open", "high", "low", "close", "volume"]]
            except Exception:
                pass

        if daily_df is None or daily_df.empty or len(daily_df) < 20:
            return None

        # Instrument suitability gate
        supported, reason = _is_instrument_supported(ticker, daily_df)
        if not supported:
            return None

        current_price = daily_df["close"].iloc[-1]

        # Fibonacci bias
        nearest_fib = _dep("nearest_fib")
        fib_bias = "NEUTRAL"
        if use_fib:
            try:
                hi_52 = daily_df["high"].rolling(252).max().iloc[-1]
                lo_52 = daily_df["low"].rolling(252).min().iloc[-1]
                fib_result = nearest_fib(current_price, lo_52, hi_52, fib_tol)
                if fib_result:
                    fib_name = fib_result[0]
                    fib_pct = float(fib_name.split()[1].replace("%", ""))
                    if fib_pct >= 61.8:
                        fib_bias = "BEARISH"
                    elif fib_pct <= 38.2:
                        fib_bias = "BULLISH"
            except:
                pass

        # Support / Resistance levels
        sr_data = None
        try:
            sr_data = _dep("calc_support_resistance")(daily_df)
        except:
            pass

        # Build signals
        analyze_volume_profile = _dep("analyze_volume_profile")
        signals = []
        signal_names = []

        # 1. Daily candle direction (double weight)
        daily_close = float(daily_df["close"].iloc[-1])
        daily_open  = float(daily_df["open"].iloc[-1])
        candle_bias = "N/A"
        if daily_close > daily_open:
            signals.append(2); signal_names.append("Day:BULL")
            candle_bias = "BULLISH"
        elif daily_close < daily_open:
            signals.append(-2); signal_names.append("Day:BEAR")
            candle_bias = "BEARISH"

        # 2. Fibonacci (with S/R confluence bonus)
        if fib_bias == "BULLISH":
            signals.append(1); signal_names.append("Fib:BULL")
            if sr_data and sr_data.get("key_level"):
                kl = sr_data["key_level"]["price"]
                if abs(kl - current_price) / current_price < 0.015:
                    signals.append(1); signal_names.append("Fib+SR:BULL")
        elif fib_bias == "BEARISH":
            signals.append(-1); signal_names.append("Fib:BEAR")
            if sr_data and sr_data.get("key_level"):
                kl = sr_data["key_level"]["price"]
                if abs(kl - current_price) / current_price < 0.015:
                    signals.append(-1); signal_names.append("Fib+SR:BEAR")

        # Volume Profile bias + surge
        vol_profile = None
        vol_bias = "NEUTRAL"
        try:
            vol_profile = analyze_volume_profile(daily_df, lookback=50)
            if vol_profile:
                vol_bias = vol_profile["vol_bias"]
                if vol_bias == "BULLISH":
                    signals.append(1)
                    signal_names.append("Vol:BULL")
                elif vol_bias == "BEARISH":
                    signals.append(-1)
                    signal_names.append("Vol:BEAR")
                if vol_profile["vol_surge"]:
                    if vol_bias == "BULLISH":
                        signals.append(1)
                        signal_names.append("VolSurge:BULL")
                    elif vol_bias == "BEARISH":
                        signals.append(-1)
                        signal_names.append("VolSurge:BEAR")
        except:
            pass

        # Strategy (optional)
        if use_strategy:
            try:
                analyze_strategy_signals = _dep("analyze_strategy_signals")
                strategy_data = analyze_strategy_signals(daily_df)
                if strategy_data and "error" not in strategy_data:
                    if strategy_data.get("short_signal"):
                        signals.append(-2)
                        signal_names.append("Strat:SHORT")
                    elif strategy_data.get("long_signal"):
                        signals.append(2)
                        signal_names.append("Strat:LONG")
            except:
                pass

        if not signals:
            skip_reason = "No signals (doji + no Fib/Vol data)"
        else:
            skip_reason = None

        # Centralised verdict / confidence / score
        if not signals:
            verdict, confidence, score = "NEUTRAL", "N/A", 0
            vol_trend = vol_profile["vol_trend"] if vol_profile else "N/A"
            signal_names = []
        else:
            verdict, confidence, score, signal_names = _compute_verdict_confidence_score(
                signals, signal_names, candle_bias, vol_profile
            )
            vol_trend = vol_profile["vol_trend"] if vol_profile else "N/A"

            if verdict == "NEUTRAL":
                skip_reason = "NEUTRAL — no directional edge"
            elif score <= -4:
                skip_reason = f"Score {score} — extreme score block"
            elif confidence == "LOW":
                skip_reason = "LOW confidence"
            elif abs(score) >= 3:
                lc = (verdict in ("BULLISH", "LEAN BULLISH")  and vol_bias == "BEARISH")
                sc_ = (verdict in ("BEARISH", "LEAN BEARISH") and vol_bias == "BULLISH")
                if lc or sc_:
                    skip_reason = f"Vol conflict — {verdict} but vol={vol_bias}"

        # Per-ticker adaptive rules
        profile     = _classify_instrument(ticker, daily_df)
        atr_14      = profile["atr_14"]
        atr_pct     = profile["atr_pct"]
        is_mean_rev = profile["is_mean_rev"]

        if skip_reason is None:
            if is_mean_rev and verdict in ("BEARISH", "LEAN BEARISH"):
                skip_reason = f"Mean-rev instrument — no SHORT"
            elif is_mean_rev and abs(score) < 4:
                skip_reason = f"Mean-rev instrument — score {score} < 4 required"

        entry_status = "ENTER" if skip_reason is None else f"SKIP — {skip_reason}"
        atr_mult = 0.3 if is_mean_rev else 0.5

        # Entry/Exit levels
        recent_low  = daily_df["low"].iloc[-10:].min()
        recent_high = daily_df["high"].iloc[-10:].max()
        avg_daily_move = atr_14 * 0.6

        if verdict in ["BULLISH", "LEAN BULLISH"]:
            entry     = round(current_price, 2)
            stop_loss = round(recent_low - atr_14 * atr_mult, 2)
            risk      = entry - stop_loss
            target1   = round(entry + risk * 2, 2)
            target2   = round(entry + risk * 3, 2)
            risk_pct  = round((risk / entry) * 100, 1)
            t1_days   = max(1, round((target1 - entry) / avg_daily_move)) if avg_daily_move > 0 else None
            t2_days   = max(1, round((target2 - entry) / avg_daily_move)) if avg_daily_move > 0 else None
        elif verdict in ["BEARISH", "LEAN BEARISH"]:
            entry     = round(current_price, 2)
            stop_loss = round(recent_high + atr_14 * atr_mult, 2)
            risk      = stop_loss - entry
            target1   = round(entry - risk * 2, 2)
            target2   = round(entry - risk * 3, 2)
            risk_pct  = round((risk / entry) * 100, 1)
            t1_days   = max(1, round((entry - target1) / avg_daily_move)) if avg_daily_move > 0 else None
            t2_days   = max(1, round((entry - target2) / avg_daily_move)) if avg_daily_move > 0 else None
        else:
            entry     = round(current_price, 2)
            stop_loss = None; target1 = None; target2 = None
            risk_pct  = None; t1_days = None; t2_days = None

        # Risk/Reward ratios
        def _scan_rr(e, s, t, d):
            try:
                risk = (e - s) if d in ("BULLISH", "LEAN BULLISH") else (s - e)
                rew  = (t - e) if d in ("BULLISH", "LEAN BULLISH") else (e - t)
                return round(rew / risk, 2) if risk > 0 else 0.0
            except: return 0.0
        _dir = verdict
        rr_t1 = _scan_rr(entry, stop_loss, target1, _dir) if stop_loss and target1 else 0.0
        rr_t2 = _scan_rr(entry, stop_loss, target2, _dir) if stop_loss and target2 else 0.0
        best_rr = max(rr_t1, rr_t2)

        # Get fundamentals
        get_fundamentals = _dep("get_fundamentals")
        fundamentals = get_fundamentals(ticker)
        valuation = fundamentals.get("valuation", "N/A") if fundamentals else "N/A"
        valuation_color = fundamentals.get("valuation_color", "#6b7099") if fundamentals else "#6b7099"
        market_cap = fundamentals.get("market_cap_str", "N/A") if fundamentals else "N/A"
        target_price_1y = fundamentals.get("target_price") if fundamentals else None
        target_upside = fundamentals.get("target_upside") if fundamentals else None

        # Multi-timeframe biases
        weekly_bias = _compute_weekly_bias(daily_df)
        four_h_bias = _compute_4h_bias(ticker, daily_df, api_key, api_secret, data_source)

        result = {
            "ticker":      ticker,
            "price":       round(current_price, 2),
            "entry_status": entry_status,
            "weekly_bias": weekly_bias,
            "daily_bias":  candle_bias,
            "4h_bias":     four_h_bias,
            "verdict":     verdict,
            "confidence":  confidence,
            "score":       score,
            "signals":     ", ".join(signal_names),
            "candle":      candle_bias,
            "fib":         fib_bias,
            "vol_action":  vol_bias,
            "vol_trend":   vol_profile["vol_trend"] if vol_profile else "N/A",
            "vol_ratio":   vol_profile["vol_ratio"] if vol_profile else None,
            "poc":         vol_profile["poc"] if vol_profile else None,
            "val":         vol_profile["val"] if vol_profile else None,
            "vah":         vol_profile["vah"] if vol_profile else None,
            "vol_detail":  vol_profile["detail"] if vol_profile else "",
            "best_setup":  "Y" if (score >= 4 and confidence == "HIGH") else "N",
            "is_mean_rev": profile["is_mean_rev"],
            "persistence": round(profile["persistence"] * 100, 1),
            "quote_type":  profile["quote_type"],
            "entry":       entry,
            "stop_loss":   stop_loss,
            "target1":     target1,
            "target2":     target2,
            "risk_pct":    risk_pct,
            "t1_days":     t1_days,
            "t2_days":     t2_days,
            "valuation":        valuation,
            "valuation_color":  valuation_color,
            "market_cap":       market_cap,
            "target_1y":        target_price_1y,
            "target_upside":    target_upside,
            "rr_t1":            rr_t1,
            "rr_t2":            rr_t2,
            "best_rr":          best_rr,
        }

        # Multi-timeframe signal & action
        mtf_rank, mtf_signal, mtf_action = _mtf_signal_action(weekly_bias, candle_bias, four_h_bias)
        result["mtf_rank"]   = mtf_rank
        result["mtf_signal"] = mtf_signal
        result["mtf_action"] = mtf_action

        # Entry grade
        grade_info = _get_entry_grade(score, confidence)
        result.update(grade_info)
        if entry_status != "ENTER":
            result["entry_label"] = entry_status

        _, suitability_reason = _is_instrument_supported(ticker, daily_df)
        result["suitability_reason"] = suitability_reason

        # Attach support/resistance levels
        if sr_data:
            result["supports"] = sr_data.get("supports", [])
            result["resistances"] = sr_data.get("resistances", [])
            result["pivot"] = sr_data.get("pivot")
            result["key_level"] = sr_data.get("key_level")

        # Attach extra fundamental fields
        if fundamentals:
            for fkey in ("sector", "pe_ratio", "forward_pe", "peg_ratio",
                         "revenue_growth", "earnings_growth", "profit_margin",
                         "roe", "debt_to_equity", "beta", "rec_key", "num_analysts",
                         "dividend_yield", "week52_position", "flags"):
                result[fkey] = fundamentals.get(fkey)
        return result
    except Exception as e:
        raise RuntimeError(f"scan_single_stock({ticker}): {type(e).__name__}: {e}") from e


def scan_stocks(api_key, api_secret, data_source, watchlist=None, use_fib=True, fib_tol=2.0, use_strategy=False, max_workers=12):
    """
    Scan multiple stocks in parallel and return bullish + high confidence ones.
    Returns (top_setups, all_results).
    """
    if watchlist is None:
        watchlist = SCAN_WATCHLIST

    results = []
    if not watchlist:
        return [], []

    import concurrent.futures
    with concurrent.futures.ThreadPoolExecutor(max_workers=min(max_workers, max(1, len(watchlist)))) as pool:
        future_map = {
            pool.submit(scan_single_stock, ticker, api_key, api_secret, data_source, use_fib, fib_tol, use_strategy): ticker
            for ticker in watchlist
        }
        for fut in concurrent.futures.as_completed(future_map):
            try:
                result = fut.result()
                if result:
                    results.append(result)
            except Exception:
                pass

    bullish_high = [
        r for r in results
        if r["verdict"] == "BULLISH" and r["confidence"] == "HIGH" and r.get("score", 0) >= 3
    ]
    bullish_high.sort(key=lambda x: x["score"], reverse=True)

    bearish_high = [
        r for r in results
        if r["verdict"] == "BEARISH" and r["confidence"] == "HIGH" and r.get("score", 0) <= -3
    ]
    bearish_high.sort(key=lambda x: x["score"])

    top_setups = bullish_high[:5] + bearish_high[:5]
    return top_setups, results
