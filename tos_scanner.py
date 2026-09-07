"""
tos_scanner.py — Python port of the ThinkOrSwim Fibonacci Master + AGIG + Weinstein scanner.

Runs on both WEEKLY and DAILY timeframes via yfinance, returns a dict of signals per ticker.

Usage:
    from tos_scanner import scan_ticker
    result = scan_ticker("AAPL")
"""

import numpy as np
import pandas as pd
import yfinance as yf
from datetime import datetime, timedelta


# ─── Configuration defaults (mirror TOS inputs) ─────────────────────────────
LOOKBACK_PERIOD = 50
FIB_VOL_THRESHOLD = 1.2
ATR_LENGTH = 5
ATR_REVERSAL_FACTOR = 3.0
ZIGZAG_PERCENT = 3.0
ZIGZAG_AMOUNT = 0.15
ATR_REVERSAL = 2.0
MA30_PERIOD = 30
MA10_PERIOD = 10
RS_PERIOD = 50
VOLUME_PERIOD = 10
HIGH_LOOKBACK = 52
LOW_LOOKBACK = 52
FLATNESS_PCT = 0.08
VOL_AVG_LENGTH = 50
VOL_THRESHOLD = 1.2
VOL_PRICE_AVG_LEN = 20
COMP_SYMBOL = "SPY"


# ═════════════════════════════════════════════════════════════════════════════
#  HELPER FUNCTIONS
# ═════════════════════════════════════════════════════════════════════════════

def _ema(series: pd.Series, span: int) -> pd.Series:
    return series.ewm(span=span, adjust=False).mean()


def _sma(series: pd.Series, period: int) -> pd.Series:
    return series.rolling(period, min_periods=1).mean()


def _atr(df: pd.DataFrame, length: int = 5) -> pd.Series:
    h, l, c = df["High"], df["Low"], df["Close"]
    tr = pd.concat([h - l, (h - c.shift(1)).abs(), (l - c.shift(1)).abs()], axis=1).max(axis=1)
    return tr.rolling(length, min_periods=1).mean()


def _roc(series: pd.Series, period: int = 12) -> pd.Series:
    return ((series - series.shift(period)) / series.shift(period)) * 100


def _close_position(df: pd.DataFrame) -> pd.Series:
    r = df["High"] - df["Low"]
    return np.where(r > 0, (df["Close"] - df["Low"]) / r, 0.5)


# ═════════════════════════════════════════════════════════════════════════════
#  1. FIBONACCI ZONE
# ═════════════════════════════════════════════════════════════════════════════

def compute_fibonacci(df: pd.DataFrame, lookback: int = LOOKBACK_PERIOD) -> dict:
    h = df["High"].rolling(lookback, min_periods=1).max()
    l = df["Low"].rolling(lookback, min_periods=1).min()
    hh = h.iloc[-1]
    ll = l.iloc[-1]
    swing = hh - ll
    price = df["Close"].iloc[-1]

    if swing == 0:
        fib_pos = 50.0
    else:
        fib_pos = ((price - ll) / swing) * 100

    # Fib zone label
    if fib_pos < 23.6:
        zone = "DEEP DISCOUNT"
    elif fib_pos < 38.2:
        zone = "BUY ZONE"
    elif fib_pos < 61.8:
        zone = "NEUTRAL"
    elif fib_pos < 78.6:
        zone = "SELL ZONE"
    else:
        zone = "EXTENDED"

    # Bear targets
    bear_t1 = ll
    bear_t2 = ll - swing * 0.272
    bear_t3 = ll - swing * 0.618

    # Bull targets
    bull_t1 = hh
    bull_t2 = hh + swing * 0.272
    bull_t3 = hh + swing * 0.618

    # Determine trend from swing highs/lows bar positions
    hh_idx = df["High"].iloc[-lookback:].idxmax()
    ll_idx = df["Low"].iloc[-lookback:].idxmin()
    is_bullish_swing = hh_idx > ll_idx
    is_bearish_swing = ll_idx > hh_idx

    return {
        "fib_hh": round(hh, 2),
        "fib_ll": round(ll, 2),
        "fib_swing": round(swing, 2),
        "fib_position": round(fib_pos, 1),
        "fib_zone": zone,
        "fib_bullish_swing": is_bullish_swing,
        "fib_bearish_swing": is_bearish_swing,
        "bear_t1": round(bear_t1, 2),
        "bear_t2": round(bear_t2, 2),
        "bear_t3": round(bear_t3, 2),
        "bull_t1": round(bull_t1, 2),
        "bull_t2": round(bull_t2, 2),
        "bull_t3": round(bull_t3, 2),
    }


# ═════════════════════════════════════════════════════════════════════════════
#  2. VOLUME CONVICTION
# ═════════════════════════════════════════════════════════════════════════════

def compute_volume_conviction(df: pd.DataFrame) -> dict:
    avg_vol = _sma(df["Volume"], 20)
    high_volume = df["Volume"] > (avg_vol * FIB_VOL_THRESHOLD)
    close_pos = pd.Series(_close_position(df), index=df.index)
    prev_close_pos = close_pos.shift(1)

    buyer_conv = high_volume & (close_pos >= 0.5)
    seller_conv = high_volume & (close_pos < 0.5)
    strong_buyers = buyer_conv & (prev_close_pos >= 0.5)
    strong_sellers = seller_conv & (prev_close_pos < 0.5)

    # Latest bar conviction
    latest = len(df) - 1
    last_buyer_idx = buyer_conv[buyer_conv].index[-1] if buyer_conv.any() else None
    last_seller_idx = seller_conv[seller_conv].index[-1] if seller_conv.any() else None

    if last_buyer_idx is not None and last_seller_idx is not None:
        conv_label = "BUYERS" if last_buyer_idx > last_seller_idx else "SELLERS"
    elif last_buyer_idx is not None:
        conv_label = "BUYERS"
    elif last_seller_idx is not None:
        conv_label = "SELLERS"
    else:
        conv_label = "NONE"

    # Count recent conviction bars
    recent = df.iloc[-10:]
    close_pos_recent = pd.Series(_close_position(recent), index=recent.index)
    avg_vol_recent = _sma(recent["Volume"], 20) if len(recent) >= 20 else recent["Volume"].mean()
    hv_recent = recent["Volume"] > (avg_vol.iloc[-10:] * FIB_VOL_THRESHOLD) if len(avg_vol) >= 10 else pd.Series([False]*len(recent), index=recent.index)
    buyer_count = int((hv_recent & (close_pos_recent >= 0.5)).sum())
    seller_count = int((hv_recent & (close_pos_recent < 0.5)).sum())

    # Volume trend
    vol_sma50 = _sma(df["Volume"], VOL_AVG_LENGTH)
    vol_ratio = df["Volume"].iloc[-1] / vol_sma50.iloc[-1] if vol_sma50.iloc[-1] > 0 else 1.0
    price_ma = _sma(df["Close"], VOL_PRICE_AVG_LEN)

    # Volume trend: accumulating/distributing/flat
    if vol_ratio >= VOL_THRESHOLD and df["Close"].iloc[-1] > price_ma.iloc[-1]:
        vol_trend = "ACCUMULATING"
    elif vol_ratio >= VOL_THRESHOLD and df["Close"].iloc[-1] < price_ma.iloc[-1]:
        vol_trend = "DISTRIBUTING"
    else:
        vol_trend = "FLAT"

    return {
        "conviction": conv_label,
        "strong_buyers": bool(strong_buyers.iloc[-1]) if len(strong_buyers) > 0 else False,
        "strong_sellers": bool(strong_sellers.iloc[-1]) if len(strong_sellers) > 0 else False,
        "buyer_conv_10bar": buyer_count,
        "seller_conv_10bar": seller_count,
        "vol_ratio": round(vol_ratio, 2),
        "vol_trend": vol_trend,
    }


# ═════════════════════════════════════════════════════════════════════════════
#  3. EMA TREND
# ═════════════════════════════════════════════════════════════════════════════

def compute_ema_trend(df: pd.DataFrame) -> dict:
    ema20 = _ema(df["Close"], 20)
    ema50 = _ema(df["Close"], 50)
    price = df["Close"].iloc[-1]
    e20 = ema20.iloc[-1]
    e50 = ema50.iloc[-1]

    if e20 > e50 and price > e20:
        trend = "UPTREND"
    elif e20 < e50 and price < e20:
        trend = "DOWNTREND"
    else:
        trend = "NEUTRAL"

    return {
        "ema20": round(e20, 2),
        "ema50": round(e50, 2),
        "ema_trend": trend,
    }


# ═════════════════════════════════════════════════════════════════════════════
#  4. AGIG ZIGZAG BIAS (port of TOS section 9)
#     TOS uses: ZigZagHighLow with EMA(high,5)/EMA(low,5) as price inputs,
#     percentage_reversal=0.01, absolute_reversal=0.05, atr_length=5, atr_reversal=2.0
# ═════════════════════════════════════════════════════════════════════════════

# AGIG-specific parameters (from TOS section 9 — NOT the main zigzag)
_AGIG_PCT_REVERSAL = 0.01   # percentage reversal (TOS default for AGIG bias)
_AGIG_ABS_REVERSAL = 0.05   # absolute reversal
_AGIG_ATR_LENGTH = 5
_AGIG_ATR_REVERSAL = 2.0    # atrReversal input
_AGIG_EMA_PERIOD = 5        # EMA smoothing for high/low


def _find_agig_pivots(df: pd.DataFrame) -> list:
    """
    Find zigzag pivots matching TOS ZigZagHighLow with AGIG parameters.
    Uses EMA(high,5) / EMA(low,5) as price inputs (matching TOS biasPriceH/L).
    Returns list of (index, price_at_pivot, 'H'|'L').
    """
    if len(df) < 10:
        return []

    # TOS: biasPriceH = EMA(high, 5), biasPriceL = EMA(low, 5)
    ema_h = _ema(df["High"], _AGIG_EMA_PERIOD)
    ema_l = _ema(df["Low"], _AGIG_EMA_PERIOD)
    atr = _atr(df, _AGIG_ATR_LENGTH)

    pivots = []
    state = None  # 'up' or 'down'
    extremum_h = ema_h.iloc[0]  # track highs for peaks
    extremum_l = ema_l.iloc[0]  # track lows for troughs
    extremum_idx = df.index[0]

    for i in range(1, len(df)):
        idx = df.index[i]
        eh = ema_h.iloc[i]
        el = ema_l.iloc[i]
        price = df["Close"].iloc[i]

        # TOS reversal amount: max(price * pct_reversal / 100, abs_reversal, atr * atr_reversal)
        rev_amt = max(
            price * _AGIG_PCT_REVERSAL / 100,
            _AGIG_ABS_REVERSAL,
            atr.iloc[i] * _AGIG_ATR_REVERSAL
        )

        if state is None:
            if eh >= extremum_h + rev_amt:
                state = "up"
                extremum_h = eh
                extremum_l = el
                extremum_idx = idx
            elif el <= extremum_l - rev_amt:
                state = "down"
                extremum_h = eh
                extremum_l = el
                extremum_idx = idx
            else:
                extremum_h = max(extremum_h, eh)
                extremum_l = min(extremum_l, el)
        elif state == "up":
            if eh > extremum_h:
                extremum_h = eh
                extremum_idx = idx
            elif el <= extremum_h - rev_amt:
                # Reversal down — record the high pivot
                pivots.append((extremum_idx, extremum_h, "H"))
                state = "down"
                extremum_l = el
                extremum_h = eh
                extremum_idx = idx
        else:  # down
            if el < extremum_l:
                extremum_l = el
                extremum_idx = idx
            elif eh >= extremum_l + rev_amt:
                # Reversal up — record the low pivot
                pivots.append((extremum_idx, extremum_l, "L"))
                state = "up"
                extremum_h = eh
                extremum_l = el
                extremum_idx = idx

    # Add final pivot
    if state == "up":
        pivots.append((extremum_idx, extremum_h, "H"))
    elif state == "down":
        pivots.append((extremum_idx, extremum_l, "L"))

    return pivots


def compute_agig_bias(df: pd.DataFrame) -> dict:
    """AGIG zigzag-based bias — mirrors TOS section 9 exactly."""
    if len(df) < 20:
        return {"bias": "NEUTRAL", "bias_strength": 0.0, "quality_score": 0, "signal_date": ""}

    pivots = _find_agig_pivots(df)
    if not pivots:
        return {"bias": "NEUTRAL", "bias_strength": 0.0, "quality_score": 0, "signal_date": ""}

    # TOS logic: isLastSignalLow = lastLowBar > lastHighBar
    # Find the last H and last L pivot
    last_h_idx = None
    last_l_idx = None
    last_h_pivot = None
    last_l_pivot = None
    for piv_idx, piv_price, piv_type in pivots:
        if piv_type == "H":
            last_h_idx = piv_idx
            last_h_pivot = (piv_idx, piv_price, piv_type)
        else:
            last_l_idx = piv_idx
            last_l_pivot = (piv_idx, piv_price, piv_type)

    if last_h_idx is None and last_l_idx is None:
        return {"bias": "NEUTRAL", "bias_strength": 0.0, "quality_score": 0, "signal_date": ""}

    # Determine which was more recent
    if last_l_idx is not None and last_h_idx is not None:
        # Compare bar positions
        l_pos = df.index.get_loc(last_l_idx) if last_l_idx in df.index else -1
        h_pos = df.index.get_loc(last_h_idx) if last_h_idx in df.index else -1
        is_last_signal_low = l_pos > h_pos
    elif last_l_idx is not None:
        is_last_signal_low = True
    else:
        is_last_signal_low = False

    # Get the signal bar (most recent pivot regardless of type)
    if is_last_signal_low:
        signal_pivot = last_l_pivot
    else:
        signal_pivot = last_h_pivot

    piv_idx, piv_price, piv_type = signal_pivot
    price = df["Close"].iloc[-1]

    # Get the bar at the pivot for quality scoring
    if piv_idx in df.index:
        piv_loc = df.index.get_loc(piv_idx)
        sig_close = df["Close"].iloc[piv_loc]
        sig_open = df["Open"].iloc[piv_loc]
        sig_vol = df["Volume"].iloc[piv_loc]
        prev_vol = df["Volume"].iloc[piv_loc - 1] if piv_loc > 0 else sig_vol
    else:
        sig_close = piv_price
        sig_open = piv_price
        sig_vol = 0
        prev_vol = 0

    is_last_low = piv_type == "L"
    bullish = is_last_low and price >= sig_close
    bearish = (not is_last_low) and price <= sig_close

    if bullish:
        bias = "BULLISH"
    elif bearish:
        bias = "BEARISH"
    else:
        bias = "NEUTRAL"

    # Strength: distance from signal price as %
    strength = round(abs(price - sig_close) / sig_close * 100, 2) if sig_close > 0 else 0.0

    # Quality score (1-3): base 1 + strong price + high vol
    is_strong_price = (sig_close > sig_open) if is_last_low else (sig_close < sig_open)
    is_high_vol = sig_vol > prev_vol
    quality = 1 + int(is_strong_price) + int(is_high_vol)

    # Signal date
    if hasattr(piv_idx, 'strftime'):
        sig_date = piv_idx.strftime("%Y-%m-%d")
    else:
        sig_date = str(piv_idx)

    return {
        "bias": bias,
        "bias_strength": strength,
        "quality_score": quality,
        "signal_date": sig_date,
    }


# ═════════════════════════════════════════════════════════════════════════════
#  5. STAN WEINSTEIN BREAKOUT SCORE
# ═════════════════════════════════════════════════════════════════════════════

def compute_weinstein(df: pd.DataFrame, spy_df: pd.DataFrame = None) -> dict:
    if len(df) < MA30_PERIOD:
        return {"weinstein_score": 0, "weinstein_phase": "UNKNOWN", "weinstein_details": {}}

    ma30 = _sma(df["Close"], MA30_PERIOD)
    ma10 = _sma(df["Close"], MA10_PERIOD)
    price = df["Close"].iloc[-1]

    # MA30 slope
    if len(ma30) >= 11:
        ma30_slope = (ma30.iloc[-1] - ma30.iloc[-11]) / ma30.iloc[-11] if ma30.iloc[-11] != 0 else 0
    else:
        ma30_slope = 0

    ma_is_flat = abs(ma30_slope) < FLATNESS_PCT
    ma_turning_up = ma30.iloc[-1] > ma30.iloc[-3] and ma30.iloc[-1] > ma30.iloc[-5] if len(ma30) >= 5 else False
    ma10_above = ma10.iloc[-1] > ma30.iloc[-1]

    # 52-week high/low
    n_high = min(HIGH_LOOKBACK, len(df))
    n_low = min(LOW_LOOKBACK, len(df))
    high52 = df["High"].iloc[-n_high:].max()
    low52 = df["Low"].iloc[-n_low:].min()
    range52 = high52 - low52
    price_position = ((price - low52) / range52 * 100) if range52 > 0 else 0
    dist_from_high = ((high52 - price) / price * 100) if price > 0 else 0

    # Relative strength vs SPY
    rs_positive = False
    rs_improving = False
    if spy_df is not None and len(spy_df) >= RS_PERIOD and len(df) >= RS_PERIOD:
        stock_chg = price / df["Close"].iloc[-RS_PERIOD] if df["Close"].iloc[-RS_PERIOD] > 0 else 1
        spy_chg = spy_df["Close"].iloc[-1] / spy_df["Close"].iloc[-RS_PERIOD] if spy_df["Close"].iloc[-RS_PERIOD] > 0 else 1
        rs = stock_chg / spy_chg - 1
        rs_positive = rs > 0
        # RS improving: compare current vs 4 bars ago and 8 bars ago
        if len(df) >= RS_PERIOD + 8:
            rs_4 = (df["Close"].iloc[-1-4] / df["Close"].iloc[-RS_PERIOD-4]) / \
                   (spy_df["Close"].iloc[-1-4] / spy_df["Close"].iloc[-RS_PERIOD-4]) - 1 \
                   if spy_df["Close"].iloc[-RS_PERIOD-4] > 0 and df["Close"].iloc[-RS_PERIOD-4] > 0 else 0
            rs_8 = (df["Close"].iloc[-1-8] / df["Close"].iloc[-RS_PERIOD-8]) / \
                   (spy_df["Close"].iloc[-1-8] / spy_df["Close"].iloc[-RS_PERIOD-8]) - 1 \
                   if spy_df["Close"].iloc[-RS_PERIOD-8] > 0 and df["Close"].iloc[-RS_PERIOD-8] > 0 else 0
            rs_improving = rs > rs_4 and rs > rs_8
    else:
        rs = 0.0

    # Volume building
    avg_vol10 = _sma(df["Volume"], VOLUME_PERIOD).iloc[-1]
    avg_vol4 = _sma(df["Volume"], 4).iloc[-1]
    vol_building = avg_vol4 > avg_vol10 * 1.1

    near_high = dist_from_high < 10

    # Score 0-6
    score = (int(ma_turning_up) + int(ma10_above) + int(rs_positive) +
             int(rs_improving) + int(vol_building) + int(near_high))

    # Phase
    near_ma30 = price > ma30.iloc[-1] * 0.90 and price < ma30.iloc[-1] * 1.15
    in_stage1 = near_ma30 and ma_is_flat
    if in_stage1:
        phase = "STAGE 1 — BASING"
    elif ma_turning_up and price > ma30.iloc[-1]:
        phase = "STAGE 2 — ADVANCING"
    elif not ma_turning_up and price > ma30.iloc[-1]:
        phase = "STAGE 3 — TOPPING"
    elif price < ma30.iloc[-1]:
        phase = "STAGE 4 — DECLINING"
    else:
        phase = "UNKNOWN"

    return {
        "weinstein_score": score,
        "weinstein_phase": phase,
        "weinstein_details": {
            "ma30_curling": ma_turning_up,
            "ma10_above_ma30": ma10_above,
            "rs_positive": rs_positive,
            "rs_improving": rs_improving,
            "vol_building": vol_building,
            "near_52w_high": near_high,
            "price_position_52w": round(price_position, 1),
            "dist_from_high": round(dist_from_high, 1),
        },
    }


# ═════════════════════════════════════════════════════════════════════════════
#  6a. RSI
# ═════════════════════════════════════════════════════════════════════════════

def compute_rsi(df: pd.DataFrame, period: int = 14) -> dict:
    if len(df) < period + 1:
        return {"rsi": 50.0, "rsi_label": "NEUTRAL"}
    delta = df["Close"].diff()
    gain  = delta.clip(lower=0).ewm(com=period - 1, min_periods=period).mean()
    loss  = (-delta.clip(upper=0)).ewm(com=period - 1, min_periods=period).mean()
    rs    = gain / loss.replace(0, np.nan)
    rsi   = (100 - 100 / (1 + rs)).iloc[-1]
    if np.isnan(rsi):
        rsi = 50.0
    if rsi >= 70:
        label = "OVERBOUGHT"
    elif rsi <= 30:
        label = "OVERSOLD"
    elif rsi >= 60:
        label = "STRONG"
    elif rsi <= 40:
        label = "WEAK"
    else:
        label = "NEUTRAL"
    return {"rsi": round(float(rsi), 1), "rsi_label": label}


# ═════════════════════════════════════════════════════════════════════════════
#  6b. MACD
# ═════════════════════════════════════════════════════════════════════════════

def compute_macd(df: pd.DataFrame, fast: int = 12, slow: int = 26, sig: int = 9) -> dict:
    if len(df) < slow + sig:
        return {"macd": 0.0, "macd_signal": 0.0, "macd_hist": 0.0,
                "macd_cross_up": False, "macd_cross_down": False, "macd_label": "NEUTRAL"}
    macd_line   = _ema(df["Close"], fast) - _ema(df["Close"], slow)
    signal_line = macd_line.ewm(span=sig, adjust=False).mean()
    hist        = macd_line - signal_line
    cross_up    = bool(macd_line.iloc[-2] <= signal_line.iloc[-2] and macd_line.iloc[-1] > signal_line.iloc[-1])
    cross_down  = bool(macd_line.iloc[-2] >= signal_line.iloc[-2] and macd_line.iloc[-1] < signal_line.iloc[-1])
    hist_val    = float(hist.iloc[-1])
    label = "CROSS UP" if cross_up else ("CROSS DOWN" if cross_down else ("BULLISH" if hist_val > 0 else "BEARISH"))
    return {
        "macd":           round(float(macd_line.iloc[-1]), 4),
        "macd_signal":    round(float(signal_line.iloc[-1]), 4),
        "macd_hist":      round(hist_val, 4),
        "macd_cross_up":  cross_up,
        "macd_cross_down": cross_down,
        "macd_label":     label,
    }


# ═════════════════════════════════════════════════════════════════════════════
#  6c. BOLLINGER BANDS
# ═════════════════════════════════════════════════════════════════════════════

def compute_bollinger(df: pd.DataFrame, period: int = 20, n_std: float = 2.0) -> dict:
    if len(df) < period:
        return {"bb_upper": 0.0, "bb_mid": 0.0, "bb_lower": 0.0,
                "bb_bandwidth": 0.0, "bb_pct_b": 0.5, "bb_squeeze": False, "bb_position": "INSIDE"}
    mid   = _sma(df["Close"], period)
    std   = df["Close"].rolling(period).std()
    upper = (mid + n_std * std).iloc[-1]
    lower = (mid - n_std * std).iloc[-1]
    mid_v = mid.iloc[-1]
    price = df["Close"].iloc[-1]
    bw    = (upper - lower) / mid_v * 100 if mid_v > 0 else 0.0
    pct_b = (price - lower) / (upper - lower) if (upper - lower) > 0 else 0.5
    if price > upper:
        pos = "ABOVE_UPPER"
    elif price < lower:
        pos = "BELOW_LOWER"
    else:
        pos = "INSIDE"
    return {
        "bb_upper":     round(upper, 2),
        "bb_mid":       round(mid_v, 2),
        "bb_lower":     round(lower, 2),
        "bb_bandwidth": round(bw, 2),
        "bb_pct_b":     round(pct_b, 3),
        "bb_squeeze":   bool(bw < 5.0),
        "bb_position":  pos,
    }


# ═════════════════════════════════════════════════════════════════════════════
#  6d. MOVING AVERAGE POSITION (20 / 50 / 200)
# ═════════════════════════════════════════════════════════════════════════════

def compute_ma_position(df: pd.DataFrame) -> dict:
    price = float(df["Close"].iloc[-1])
    ma20  = float(_sma(df["Close"], 20).iloc[-1])
    ma50  = float(_sma(df["Close"], 50).iloc[-1])
    ma200 = float(_sma(df["Close"], 200).iloc[-1]) if len(df) >= 200 else None
    golden = bool(ma200 is not None and ma50 > ma200)
    death  = bool(ma200 is not None and ma50 < ma200)
    return {
        "price_vs_ma20":  "ABOVE" if price > ma20 else "BELOW",
        "price_vs_ma50":  "ABOVE" if price > ma50 else "BELOW",
        "price_vs_ma200": ("ABOVE" if price > ma200 else "BELOW") if ma200 is not None else "N/A",
        "ma20":           round(ma20, 2),
        "ma50":           round(ma50, 2),
        "ma200":          round(ma200, 2) if ma200 is not None else None,
        "golden_cross":   golden,
        "death_cross":    death,
    }


# ═════════════════════════════════════════════════════════════════════════════
#  6. FVG DETECTION (Fair Value Gaps)
# ═════════════════════════════════════════════════════════════════════════════

def compute_fvg(df: pd.DataFrame, min_pct: float = 0.1) -> dict:
    """Detect unfilled bull/bear fair value gaps in last 10 bars."""
    if len(df) < 5:
        return {"bull_fvg": [], "bear_fvg": []}

    bull_gaps = []
    bear_gaps = []
    price = df["Close"].iloc[-1]

    for i in range(2, min(12, len(df))):
        # Bull FVG: low of bar[i-2] > high of bar[i]  (gap up)
        gap_top = df["Low"].iloc[-i+2] if (-i+2) < 0 else df["Low"].iloc[-i+2]
        gap_bot = df["High"].iloc[-i]

        # Need proper indexing
        if len(df) > i:
            bar_m2 = len(df) - i  # oldest bar
            bar_0 = len(df) - i + 2  # newest bar
            if bar_0 < len(df):
                bt = df["Low"].iloc[bar_0]
                bb = df["High"].iloc[bar_m2]
                gap_size = bt - bb
                if gap_size > 0 and (gap_size / price * 100) >= min_pct:
                    # Check if still unfilled
                    filled = False
                    for j in range(bar_0 + 1, len(df)):
                        if df["Low"].iloc[j] <= bb:
                            filled = True
                            break
                    if not filled:
                        bull_gaps.append({
                            "top": round(bt, 2),
                            "bottom": round(bb, 2),
                            "size_pct": round(gap_size / price * 100, 2),
                        })

                # Bear FVG: high of bar[0] < low of bar[-2]
                bear_bt = df["Low"].iloc[bar_m2]
                bear_bb = df["High"].iloc[bar_0]
                bear_gap = bear_bt - bear_bb
                if bear_gap > 0 and (bear_gap / price * 100) >= min_pct:
                    filled = False
                    for j in range(bar_0 + 1, len(df)):
                        if df["High"].iloc[j] >= bear_bt:
                            filled = True
                            break
                    if not filled:
                        bear_gaps.append({
                            "top": round(bear_bt, 2),
                            "bottom": round(bear_bb, 2),
                            "size_pct": round(bear_gap / price * 100, 2),
                        })

    return {
        "bull_fvg": bull_gaps[:3],
        "bear_fvg": bear_gaps[:3],
    }


# ═════════════════════════════════════════════════════════════════════════════
#  7. MOMENTUM + VOLATILITY + DIVERGENCE
# ═════════════════════════════════════════════════════════════════════════════

def compute_momentum(df: pd.DataFrame) -> dict:
    if len(df) < 15:
        return {"roc": 0.0, "roc_smooth": 0.0, "momentum_label": "FLAT"}

    roc = _roc(df["Close"], 12)
    roc_smooth = _sma(roc, 3).iloc[-1]

    if roc_smooth > 5:
        label = "STRONG UP"
    elif roc_smooth > 0:
        label = "POSITIVE"
    elif roc_smooth > -5:
        label = "WEAKENING"
    else:
        label = "STRONG DOWN"

    return {
        "roc": round(roc.iloc[-1], 2) if not np.isnan(roc.iloc[-1]) else 0.0,
        "roc_smooth": round(roc_smooth, 2) if not np.isnan(roc_smooth) else 0.0,
        "momentum_label": label,
    }


def compute_volatility(df: pd.DataFrame) -> dict:
    if len(df) < 14:
        return {"volatility": 0.0, "volatility_label": "LOW"}

    atr = _atr(df, 14)
    vol_pct = (atr.iloc[-1] / df["Close"].iloc[-1] * 100) if df["Close"].iloc[-1] > 0 else 0

    if vol_pct > 3:
        label = "HIGH"
    elif vol_pct > 2:
        label = "MEDIUM"
    else:
        label = "LOW"

    return {
        "volatility": round(vol_pct, 2),
        "volatility_label": label,
    }


def compute_divergence(df: pd.DataFrame) -> dict:
    """Simple price vs ROC divergence check over last 20 bars."""
    if len(df) < 25:
        return {"bullish_divergence": False, "bearish_divergence": False}

    roc = _roc(df["Close"], 12)
    last20_price = df["Close"].iloc[-20:]
    last20_roc = roc.iloc[-20:]

    # Bullish divergence: price making lower lows but ROC making higher lows
    price_low1 = last20_price.iloc[:10].min()
    price_low2 = last20_price.iloc[10:].min()
    roc_low1 = last20_roc.iloc[:10].min()
    roc_low2 = last20_roc.iloc[10:].min()

    bullish_div = (price_low2 < price_low1) and (roc_low2 > roc_low1)

    # Bearish divergence: price making higher highs but ROC making lower highs
    price_hi1 = last20_price.iloc[:10].max()
    price_hi2 = last20_price.iloc[10:].max()
    roc_hi1 = last20_roc.iloc[:10].max()
    roc_hi2 = last20_roc.iloc[10:].max()

    bearish_div = (price_hi2 > price_hi1) and (roc_hi2 < roc_hi1)

    return {
        "bullish_divergence": bool(bullish_div),
        "bearish_divergence": bool(bearish_div),
    }


# ═════════════════════════════════════════════════════════════════════════════
#  COMBINED MULTI-TIMEFRAME SCANNER
# ═════════════════════════════════════════════════════════════════════════════

def _fetch_data(ticker: str, period: str, interval: str) -> pd.DataFrame:
    try:
        tk = yf.Ticker(ticker)
        df = tk.history(period=period, interval=interval, auto_adjust=True)
        if df.empty:
            return pd.DataFrame()
        df = df.dropna(subset=["Close"])
        return df
    except Exception:
        return pd.DataFrame()


def scan_ticker(ticker: str, spy_weekly: pd.DataFrame = None,
                spy_daily: pd.DataFrame = None) -> dict | None:
    """
    Full scan of one ticker on both WEEKLY and DAILY timeframes.
    Returns a merged result dict, or None on failure.
    """
    try:
        tk = yf.Ticker(ticker)
        df_weekly = tk.history(period="5y", interval="1wk", auto_adjust=True)
        df_daily = tk.history(period="2y", interval="1d", auto_adjust=True)
    except Exception:
        return None

    if df_weekly.empty or len(df_weekly) < 20 or df_daily.empty or len(df_daily) < 20:
        return None

    price = df_daily["Close"].iloc[-1]
    result = {"ticker": ticker, "price": round(price, 2)}

    # ── Weekly analysis ──
    w_fib = compute_fibonacci(df_weekly)
    w_vol = compute_volume_conviction(df_weekly)
    w_ema = compute_ema_trend(df_weekly)
    w_bias = compute_agig_bias(df_weekly)
    w_wein = compute_weinstein(df_weekly, spy_weekly)
    w_mom = compute_momentum(df_weekly)

    result["w_bias"] = w_bias["bias"]
    result["w_bias_strength"] = w_bias["bias_strength"]
    result["w_bias_quality"] = w_bias["quality_score"]
    result["w_ema_trend"] = w_ema["ema_trend"]
    result["w_fib_zone"] = w_fib["fib_zone"]
    result["w_fib_position"] = w_fib["fib_position"]
    result["w_conviction"] = w_vol["conviction"]
    result["w_vol_trend"] = w_vol["vol_trend"]
    result["w_weinstein_score"] = w_wein["weinstein_score"]
    result["w_weinstein_phase"] = w_wein["weinstein_phase"]
    result["w_momentum"] = w_mom["momentum_label"]

    # ── Daily analysis ──
    d_fib = compute_fibonacci(df_daily)
    d_vol = compute_volume_conviction(df_daily)
    d_ema = compute_ema_trend(df_daily)
    d_bias = compute_agig_bias(df_daily)
    d_wein = compute_weinstein(df_daily, spy_daily)
    d_fvg = compute_fvg(df_daily)
    d_mom = compute_momentum(df_daily)
    d_volatility = compute_volatility(df_daily)
    d_div = compute_divergence(df_daily)
    d_rsi = compute_rsi(df_daily)
    d_macd = compute_macd(df_daily)
    d_bb = compute_bollinger(df_daily)
    d_ma = compute_ma_position(df_daily)
    w_rsi = compute_rsi(df_weekly)
    w_macd = compute_macd(df_weekly)

    result["d_bias"] = d_bias["bias"]
    result["d_bias_strength"] = d_bias["bias_strength"]
    result["d_bias_quality"] = d_bias["quality_score"]
    result["d_ema_trend"] = d_ema["ema_trend"]
    result["d_fib_zone"] = d_fib["fib_zone"]
    result["d_fib_position"] = d_fib["fib_position"]
    result["d_conviction"] = d_vol["conviction"]
    result["d_vol_trend"] = d_vol["vol_trend"]
    result["d_vol_ratio"] = d_vol["vol_ratio"]
    result["d_weinstein_score"] = d_wein["weinstein_score"]
    result["d_weinstein_phase"] = d_wein["weinstein_phase"]
    result["d_momentum"] = d_mom["momentum_label"]
    result["d_roc"] = d_mom["roc_smooth"]
    result["d_volatility"] = d_volatility["volatility"]
    result["d_volatility_label"] = d_volatility["volatility_label"]
    result["d_bull_div"] = d_div["bullish_divergence"]
    result["d_bear_div"] = d_div["bearish_divergence"]
    result["d_bull_fvg_count"] = len(d_fvg["bull_fvg"])
    result["d_bear_fvg_count"] = len(d_fvg["bear_fvg"])
    # RSI
    result["d_rsi"] = d_rsi["rsi"]
    result["d_rsi_label"] = d_rsi["rsi_label"]
    result["w_rsi"] = w_rsi["rsi"]
    result["w_rsi_label"] = w_rsi["rsi_label"]
    # MACD
    result["d_macd_hist"] = d_macd["macd_hist"]
    result["d_macd_label"] = d_macd["macd_label"]
    result["d_macd_cross_up"] = d_macd["macd_cross_up"]
    result["d_macd_cross_down"] = d_macd["macd_cross_down"]
    result["w_macd_label"] = w_macd["macd_label"]
    result["w_macd_cross_up"] = w_macd["macd_cross_up"]
    result["w_macd_cross_down"] = w_macd["macd_cross_down"]
    # Bollinger Bands
    result["bb_bandwidth"] = d_bb["bb_bandwidth"]
    result["bb_pct_b"] = d_bb["bb_pct_b"]
    result["bb_squeeze"] = d_bb["bb_squeeze"]
    result["bb_position"] = d_bb["bb_position"]
    # MA positions
    result["price_vs_ma20"] = d_ma["price_vs_ma20"]
    result["price_vs_ma50"] = d_ma["price_vs_ma50"]
    result["price_vs_ma200"] = d_ma["price_vs_ma200"]
    result["golden_cross"] = d_ma["golden_cross"]
    result["death_cross"] = d_ma["death_cross"]
    result["ma20"] = d_ma["ma20"]
    result["ma50"] = d_ma["ma50"]
    result["ma200"] = d_ma["ma200"]

    # ── Combined bias label ──
    wb = w_bias["bias"]
    db = d_bias["bias"]
    if wb == "BULLISH" and db == "BULLISH":
        combined = "BULLISH ALIGNED"
    elif wb == "BEARISH" and db == "BEARISH":
        combined = "BEARISH ALIGNED"
    elif wb == "BULLISH" and db == "BEARISH":
        combined = "MIXED W:BULL D:BEAR"
    elif wb == "BEARISH" and db == "BULLISH":
        combined = "MIXED W:BEAR D:BULL"
    elif wb == "NEUTRAL" and db == "NEUTRAL":
        combined = "NEUTRAL"
    else:
        combined = f"MIXED W:{wb[:4]} D:{db[:4]}"
    result["combined_bias"] = combined

    # ── Combined signal grade ──
    # Score: aligned bias + EMA match + Weinstein + momentum + volume
    grade_score = 0
    # Bias alignment
    if wb == db and wb != "NEUTRAL":
        grade_score += 3
    elif wb == db:
        grade_score += 1
    # EMA alignment
    we = w_ema["ema_trend"]
    de = d_ema["ema_trend"]
    if we == de and we != "NEUTRAL":
        grade_score += 2
    elif we == de:
        grade_score += 1
    # Bias + EMA match
    if wb == "BULLISH" and we == "UPTREND":
        grade_score += 1
    elif wb == "BEARISH" and we == "DOWNTREND":
        grade_score += 1
    # Weinstein
    if d_wein["weinstein_score"] >= 5:
        grade_score += 2
    elif d_wein["weinstein_score"] >= 3:
        grade_score += 1
    # Momentum
    if d_mom["momentum_label"] in ("STRONG UP", "POSITIVE") and wb == "BULLISH":
        grade_score += 1
    elif d_mom["momentum_label"] in ("STRONG DOWN", "WEAKENING") and wb == "BEARISH":
        grade_score += 1
    # Volume conviction matches bias
    if d_vol["conviction"] == "BUYERS" and db == "BULLISH":
        grade_score += 1
    elif d_vol["conviction"] == "SELLERS" and db == "BEARISH":
        grade_score += 1
    # Divergence bonus
    if d_div["bullish_divergence"] and db == "BULLISH":
        grade_score += 1
    elif d_div["bearish_divergence"] and db == "BEARISH":
        grade_score += 1

    if grade_score >= 10:
        grade = "A+"
    elif grade_score >= 8:
        grade = "A"
    elif grade_score >= 6:
        grade = "B"
    elif grade_score >= 4:
        grade = "C"
    else:
        grade = "D"

    result["signal_grade"] = grade
    result["signal_score"] = grade_score

    # ── Weinstein details for tooltip ──
    result["w_details"] = w_wein.get("weinstein_details", {})
    result["d_details"] = d_wein.get("weinstein_details", {})

    return result


def scan_multiple(tickers: list[str]) -> list[dict]:
    """Scan a list of tickers. Pre-fetches SPY for RS comparison."""
    try:
        spy = yf.Ticker("SPY")
        spy_weekly = spy.history(period="5y", interval="1wk", auto_adjust=True)
        spy_daily = spy.history(period="2y", interval="1d", auto_adjust=True)
    except Exception:
        spy_weekly = pd.DataFrame()
        spy_daily = pd.DataFrame()

    results = []
    for t in tickers:
        r = scan_ticker(t, spy_weekly, spy_daily)
        if r:
            results.append(r)
    return results


# ═════════════════════════════════════════════════════════════════════════════
#  PREDEFINED WATCHLISTS
# ═════════════════════════════════════════════════════════════════════════════

WATCHLISTS = {
    "Mag 7": [
        "AAPL", "MSFT", "GOOGL", "AMZN", "NVDA", "META", "TSLA",
    ],
    "NASDAQ 100 Top 20": [
        "NVDA", "AAPL", "MSFT", "AMZN", "META", "TSLA", "GOOGL", "GOOG",
        "AVGO", "COST", "NFLX", "AMD", "ADBE", "QCOM", "ARM",
        "AMAT", "PANW", "LRCX", "MU", "KLAC",
    ],
    "S&P 500 Leaders": [
        "AAPL", "MSFT", "NVDA", "AMZN", "META", "GOOGL", "TSLA",
        "BRK-B", "JPM", "LLY", "V", "UNH", "XOM", "MA", "JNJ",
        "PG", "AVGO", "HD", "MRK", "CVX", "ABBV", "KO", "PEP", "BAC", "COST",
    ],
    "Sector ETFs": [
        "XLK", "XLF", "XLE", "XLV", "XLI", "XLY", "XLP", "XLB", "XLU", "XLRE",
        "XLC", "GLD", "SLV", "USO", "TLT", "HYG", "VNQ",
    ],
    "Broad Market": [
        "SPY", "QQQ", "DIA", "IWM", "MDY", "VTI", "EEM", "EFA",
    ],
    "High Beta / Momentum": [
        "NVDA", "AMD", "SMCI", "PLTR", "MSTR", "COIN", "RKLB", "SNOW",
        "CRWD", "DDOG", "NET", "UBER", "ABNB", "SHOP", "SQ", "HOOD",
    ],
    "Semiconductors": [
        "NVDA", "AMD", "INTC", "AVGO", "QCOM", "MU", "AMAT", "LRCX",
        "KLAC", "TXN", "ADI", "MCHP", "ON", "WOLF", "SMCI", "ARM",
    ],
    "Financials": [
        "JPM", "BAC", "WFC", "GS", "MS", "C", "BLK", "AXP",
        "V", "MA", "PYPL", "SQ", "SCHW", "COF",
    ],
    "Healthcare / Biotech": [
        "LLY", "JNJ", "UNH", "ABBV", "MRK", "PFE", "BMY", "AMGN",
        "GILD", "REGN", "MRNA", "BIIB", "VRTX", "ISRG",
    ],
    "Energy": [
        "XOM", "CVX", "COP", "EOG", "SLB", "PSX", "MPC", "VLO",
        "OKE", "WMB", "KMI", "HAL",
    ],
}


# ═════════════════════════════════════════════════════════════════════════════
#  SCAN FILTER ENGINE
# ═════════════════════════════════════════════════════════════════════════════

def apply_filters(results: list[dict], filters: dict) -> list[dict]:
    """
    Filter scan results by TOS-style conditions.

    filters keys (all optional, omit to skip):
        bias_direction:   'BULLISH' | 'BEARISH' | 'ANY'
        require_aligned:  bool — both W+D same direction
        min_grade:        'A+' | 'A' | 'B' | 'C' | 'D'
        rsi_min:          float
        rsi_max:          float
        require_rsi_oversold:   bool
        require_rsi_overbought: bool
        macd_cross:       'UP' | 'DOWN' | 'ANY' | None
        macd_direction:   'BULLISH' | 'BEARISH' | 'ANY'
        price_vs_ma20:    'ABOVE' | 'BELOW' | 'ANY'
        price_vs_ma50:    'ABOVE' | 'BELOW' | 'ANY'
        price_vs_ma200:   'ABOVE' | 'BELOW' | 'ANY'
        require_golden_cross: bool
        require_death_cross:  bool
        bb_position:      'ABOVE_UPPER' | 'BELOW_LOWER' | 'INSIDE' | 'ANY'
        require_bb_squeeze: bool
        weinstein_phase:  str  e.g. 'STAGE 2' — substring match
        min_weinstein:    int  (0-6)
        vol_trend:        'ACCUMULATING' | 'DISTRIBUTING' | 'FLAT' | 'ANY'
        fib_zone:         'BUY ZONE' | 'SELL ZONE' | 'DISCOUNT' | 'NEUTRAL' | 'ANY'
        require_bull_div: bool
        require_bear_div: bool
        require_bb_squeeze: bool
        min_score:        int
    """
    grade_order = {"A+": 5, "A": 4, "B": 3, "C": 2, "D": 1}

    def _pass(r):
        bd = filters.get("bias_direction", "ANY")
        if bd != "ANY":
            cb = r.get("combined_bias", "")
            if bd == "BULLISH" and "BULLISH" not in cb:
                return False
            if bd == "BEARISH" and "BEARISH" not in cb:
                return False

        if filters.get("require_aligned"):
            if r.get("combined_bias") not in ("BULLISH ALIGNED", "BEARISH ALIGNED"):
                return False

        mg = filters.get("min_grade")
        if mg and mg in grade_order:
            g = r.get("signal_grade", "D")
            if grade_order.get(g, 0) < grade_order[mg]:
                return False

        if filters.get("min_score") is not None:
            if r.get("signal_score", 0) < filters["min_score"]:
                return False

        rsi = r.get("d_rsi", 50.0)
        if filters.get("rsi_min") is not None and rsi < filters["rsi_min"]:
            return False
        if filters.get("rsi_max") is not None and rsi > filters["rsi_max"]:
            return False
        if filters.get("require_rsi_oversold") and rsi >= 30:
            return False
        if filters.get("require_rsi_overbought") and rsi <= 70:
            return False

        mc = filters.get("macd_cross")
        if mc == "UP" and not r.get("d_macd_cross_up"):
            return False
        if mc == "DOWN" and not r.get("d_macd_cross_down"):
            return False

        md = filters.get("macd_direction", "ANY")
        if md != "ANY":
            if r.get("d_macd_label") not in (md, "CROSS UP" if md == "BULLISH" else "CROSS DOWN"):
                if md == "BULLISH" and r.get("d_macd_label") not in ("BULLISH", "CROSS UP"):
                    return False
                if md == "BEARISH" and r.get("d_macd_label") not in ("BEARISH", "CROSS DOWN"):
                    return False

        for ma_key in ("price_vs_ma20", "price_vs_ma50", "price_vs_ma200"):
            fv = filters.get(ma_key, "ANY")
            if fv != "ANY" and r.get(ma_key) != fv and r.get(ma_key) != "N/A":
                return False

        if filters.get("require_golden_cross") and not r.get("golden_cross"):
            return False
        if filters.get("require_death_cross") and not r.get("death_cross"):
            return False

        bbp = filters.get("bb_position", "ANY")
        if bbp != "ANY" and r.get("bb_position") != bbp:
            return False
        if filters.get("require_bb_squeeze") and not r.get("bb_squeeze"):
            return False

        wp = filters.get("weinstein_phase", "")
        if wp and wp not in r.get("d_weinstein_phase", ""):
            return False
        mw = filters.get("min_weinstein")
        if mw is not None and r.get("d_weinstein_score", 0) < mw:
            return False

        vt = filters.get("vol_trend", "ANY")
        if vt != "ANY" and r.get("d_vol_trend") != vt:
            return False

        fz = filters.get("fib_zone", "ANY")
        if fz != "ANY" and r.get("d_fib_zone") != fz:
            return False

        if filters.get("require_bull_div") and not r.get("d_bull_div"):
            return False
        if filters.get("require_bear_div") and not r.get("d_bear_div"):
            return False

        return True

    return [r for r in results if _pass(r)]
