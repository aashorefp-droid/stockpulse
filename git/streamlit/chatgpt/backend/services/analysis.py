"""
Full scoring pipeline extracted from stock_pulse.py.
Matches the original: VAH/VAL/POC, Weinstein, FVG, conviction, verdict/confidence/score.
"""
import numpy as np
import pandas as pd
import pytz
from datetime import datetime, timedelta, date
from typing import Optional

FIB_RET = [0.236, 0.382, 0.5, 0.618, 0.786]
FIB_EXT = [1.0, 1.272, 1.414, 1.618, 2.0, 2.618]
FIB_NEG = [0.236, 0.382]

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


# ── Helpers ───────────────────────────────────────────────────────────────────

def _col(df: pd.DataFrame, name: str) -> str:
    """Return actual column name regardless of case."""
    for c in df.columns:
        if c.lower() == name.lower():
            return c
    return name


def calc_rsi(close: pd.Series, period: int = 14) -> pd.Series:
    delta = close.diff()
    gain  = delta.clip(lower=0).ewm(com=period - 1, min_periods=period).mean()
    loss  = (-delta.clip(upper=0)).ewm(com=period - 1, min_periods=period).mean()
    rsi   = pd.Series(
        np.where(loss == 0, 100.0, np.where(gain == 0, 0.0, 100 - 100 / (1 + gain / loss))),
        index=close.index,
    )
    rsi[gain.isna()] = np.nan
    return rsi


def calc_atr(df: pd.DataFrame, period: int = 14) -> float:
    hi = df[_col(df, "high")].values.astype(float)
    lo = df[_col(df, "low")].values.astype(float)
    cl = df[_col(df, "close")].values.astype(float)
    if len(cl) < 2:
        return float(np.mean(hi - lo))
    tr = np.maximum(hi[1:] - lo[1:],
         np.maximum(np.abs(hi[1:] - cl[:-1]),
                    np.abs(lo[1:]  - cl[:-1])))
    return float(np.mean(tr[-period:])) if len(tr) >= period else float(np.mean(tr))


def calc_weekly_atr(daily_df: pd.DataFrame, period: int = 14) -> dict:
    """ATR on weekly bars (resampled from daily). Returns {wk_atr, wk_atr_pct}."""
    out = {"wk_atr": None, "wk_atr_pct": None}
    try:
        if daily_df is None or daily_df.empty:
            return out
        df = daily_df.copy()
        df.index = pd.to_datetime(df.index)
        cc = _col(df, "close"); hc = _col(df, "high"); lc = _col(df, "low")
        wk = df.resample("W").agg({hc: "max", lc: "min", cc: "last"}).dropna()
        if len(wk) < 2:
            return out
        atr = calc_atr(wk, period)
        last_close = float(wk[cc].iloc[-1])
        out["wk_atr"] = round(atr, 2)
        out["wk_atr_pct"] = (
            round(atr / last_close * 100, 2) if last_close > 0 else None
        )
    except Exception:
        pass
    return out


def compute_seasonality(ticker: str, month: Optional[int] = None) -> dict:
    """
    Historical seasonality for a calendar month (default: current month).
    Uses ~15y of monthly bars: month return = close[m] / close[m-1] - 1.

    Returns {month, month_name, avg_pct, median_pct, win_rate, years,
             best_pct, worst_pct} — or {available: False} on insufficient data.
    """
    out = {"available": False, "month": None, "month_name": None,
           "avg_pct": None, "median_pct": None, "win_rate": None,
           "years": 0, "best_pct": None, "worst_pct": None, "months": []}
    try:
        m = month or date.today().month
        out["month"] = m
        out["month_name"] = date(2000, m, 1).strftime("%B")
        import yfinance as yf
        hist = yf.Ticker(ticker).history(period="15y", interval="1mo")
        if hist is None or hist.empty or "Close" not in hist.columns:
            return out
        close = pd.to_numeric(hist["Close"], errors="coerce").dropna()
        if len(close) < 14:
            return out
        rets = close.pct_change().dropna() * 100.0          # % monthly returns
        idx = pd.to_datetime(rets.index)

        def _month_vals(mm: int) -> list[float]:
            # de-dupe to one observation per year (guards partial/dup bars)
            by_year: dict = {}
            sel = rets[idx.month == mm]
            for ts, v in zip(pd.to_datetime(sel.index), sel.values):
                by_year[ts.year] = float(v)
            return list(by_year.values())

        # All 12 months (for the hover breakdown)
        all_months = []
        for mm in range(1, 13):
            v = _month_vals(mm)
            a = np.array(v, dtype=float) if v else None
            all_months.append({
                "m": mm,
                "name": date(2000, mm, 1).strftime("%b"),
                "avg_pct":  round(float(a.mean()), 2) if a is not None and len(a) else None,
                "win_rate": round(float((a > 0).mean() * 100), 1) if a is not None and len(a) else None,
                "years":    len(v),
            })
        out["months"] = all_months

        vals = _month_vals(m)
        if len(vals) < 3:
            return out
        arr = np.array(vals, dtype=float)
        out.update({
            "available":  True,
            "avg_pct":    round(float(np.mean(arr)), 2),
            "median_pct": round(float(np.median(arr)), 2),
            "win_rate":   round(float((arr > 0).mean() * 100), 1),
            "years":      len(vals),
            "best_pct":   round(float(arr.max()), 2),
            "worst_pct":  round(float(arr.min()), 2),
        })
        return out
    except Exception:
        return out


# ── Fibonacci ─────────────────────────────────────────────────────────────────

def calc_fib_levels(lo: float, hi: float) -> dict:
    rng = hi - lo
    lvls = {}
    for f in FIB_RET:
        lvls[f"R {f*100:.1f}%"] = round(hi - rng * f, 2)
    for f in FIB_EXT:
        lvls[f"E {f*100:.1f}%"] = round(lo + rng * f, 2)
    for f in FIB_NEG:
        lvls[f"N -{f*100:.1f}%"] = round(lo - rng * f, 2)
    return lvls


def nearest_fib(price: float, fib_levels: dict) -> tuple[str, float]:
    best_label, best_val, best_dist = "", 0.0, float("inf")
    for label, val in fib_levels.items():
        d = abs(price - val)
        if d < best_dist:
            best_dist, best_label, best_val = d, label, val
    return best_label, best_val


# ── Support / Resistance ──────────────────────────────────────────────────────

def calc_support_resistance(daily_df: pd.DataFrame, current_price: float, n_levels: int = 5) -> dict:
    if daily_df.empty or len(daily_df) < 10:
        return {"support": [], "resistance": []}
    hi = daily_df[_col(daily_df, "high")].values.astype(float)
    lo = daily_df[_col(daily_df, "low")].values.astype(float)
    pivots = []
    for i in range(2, len(hi) - 2):
        if hi[i] == max(hi[i-2:i+3]):
            pivots.append(("R", float(hi[i])))
        if lo[i] == min(lo[i-2:i+3]):
            pivots.append(("S", float(lo[i])))
    supports    = sorted([v for t, v in pivots if t == "S" and v < current_price], reverse=True)[:n_levels]
    resistances = sorted([v for t, v in pivots if t == "R" and v > current_price])[:n_levels]
    return {"support": [round(v, 2) for v in supports],
            "resistance": [round(v, 2) for v in resistances]}


# ── Volume Profile (VAH / VAL / POC) ─────────────────────────────────────────

def analyze_volume_profile(daily_df: pd.DataFrame, lookback: int = 50, n_bins: int = 50) -> Optional[dict]:
    if daily_df is None or len(daily_df) < 20:
        return None
    df = daily_df.tail(lookback).copy()
    vol_col   = _col(df, "volume")
    close_col = _col(df, "close")
    hi_col    = _col(df, "high")
    lo_col    = _col(df, "low")
    if vol_col not in df.columns:
        return None

    current_price = float(df[close_col].iloc[-1])
    price_min = float(df[lo_col].min())
    price_max = float(df[hi_col].max())
    if price_max <= price_min:
        return None

    bin_size = (price_max - price_min) / n_bins
    vol_at_price = np.zeros(n_bins)

    lows  = df[lo_col].values.astype(float)
    highs = df[hi_col].values.astype(float)
    vols  = df[vol_col].values.astype(float)
    valid = (highs > lows) & (vols > 0)
    lo_bins = np.clip(((lows[valid]  - price_min) / bin_size).astype(int), 0, n_bins - 1)
    hi_bins = np.clip(((highs[valid] - price_min) / bin_size).astype(int), 0, n_bins - 1)
    for lb, hb, v in zip(lo_bins, hi_bins, vols[valid]):
        n = hb - lb + 1
        np.add.at(vol_at_price, range(lb, hb + 1), v / n)

    poc_bin = int(np.argmax(vol_at_price))
    poc = price_min + (poc_bin + 0.5) * bin_size

    total_vol = vol_at_price.sum()
    if total_vol == 0:
        return None
    target_vol = total_vol * 0.70
    va_vol = vol_at_price[poc_bin]
    lo_idx = hi_idx = poc_bin
    while va_vol < target_vol and (lo_idx > 0 or hi_idx < n_bins - 1):
        add_lo = vol_at_price[lo_idx - 1] if lo_idx > 0 else 0
        add_hi = vol_at_price[hi_idx + 1] if hi_idx < n_bins - 1 else 0
        if add_lo >= add_hi and lo_idx > 0:
            lo_idx -= 1; va_vol += add_lo
        elif hi_idx < n_bins - 1:
            hi_idx += 1; va_vol += add_hi
        else:
            lo_idx -= 1; va_vol += add_lo
    val = price_min + lo_idx * bin_size
    vah = price_min + (hi_idx + 1) * bin_size

    recent_vol = df[vol_col].iloc[-5:].mean()
    prior_vol  = df[vol_col].iloc[-20:-5].mean() if len(df) >= 20 else df[vol_col].mean()
    vol_ratio_trend = recent_vol / prior_vol if prior_vol > 0 else 1.0
    avg_vol_20 = df[vol_col].iloc[-20:].mean() if len(df) >= 20 else df[vol_col].mean()
    today_vol  = float(df[vol_col].iloc[-1])
    vol_ratio  = today_vol / avg_vol_20 if avg_vol_20 > 0 else 1.0
    vol_surge  = vol_ratio > 1.5

    if vol_ratio_trend > 1.2:
        vol_trend = "ACCUMULATING"
    elif vol_ratio_trend < 0.8:
        vol_trend = "DISTRIBUTING"
    else:
        vol_trend = "FLAT"

    if current_price > vah:
        vol_bias = "BULLISH" if vol_trend == "ACCUMULATING" else "NEUTRAL"
    elif current_price < val:
        vol_bias = "BEARISH" if vol_trend == "ACCUMULATING" else "NEUTRAL"
    elif current_price > poc:
        vol_bias = "BULLISH" if vol_trend != "DISTRIBUTING" else "NEUTRAL"
    elif current_price < poc:
        vol_bias = "BEARISH" if vol_trend != "DISTRIBUTING" else "NEUTRAL"
    else:
        vol_bias = "NEUTRAL"

    pos_label = ("Above VA" if current_price > vah else
                 "Below VA" if current_price < val else
                 "Above POC" if current_price > poc else
                 "Below POC" if current_price < poc else "At POC")
    detail = f"{pos_label} | POC ${poc:.2f} | VA ${val:.2f}–${vah:.2f} | {vol_trend} | Vol {vol_ratio:.1f}x"

    return {"poc": round(poc, 2), "vah": round(vah, 2), "val": round(val, 2),
            "vol_bias": vol_bias, "vol_trend": vol_trend,
            "vol_ratio": round(float(vol_ratio), 2), "vol_surge": bool(vol_surge), "detail": detail}


# ── Strategy signals (Weinstein + FVG + swing + conviction) ──────────────────

def analyze_strategy_signals(daily_df: pd.DataFrame, lookback: int = 50) -> dict:
    if daily_df is None or len(daily_df) < lookback + 10:
        return {"error": "Insufficient data"}
    df = daily_df.copy().sort_index()
    cl = df[_col(df, "close")].values.astype(float)
    hi = df[_col(df, "high")].values.astype(float)
    lo = df[_col(df, "low")].values.astype(float)
    op = df[_col(df, "open")].values.astype(float)
    vol = df[_col(df, "volume")].values.astype(float)

    # Swing H/L
    hh = float(hi[-lookback:].max())
    ll = float(lo[-lookback:].min())
    win = min(lookback, len(hi))
    bar_hh = win - hi[-win:][::-1].argmax() - 1
    bar_ll = win - lo[-win:][::-1].argmin() - 1
    is_bearish_swing = bar_ll > bar_hh
    is_bullish_swing = bar_hh > bar_ll

    # Volume conviction
    avg_vol  = float(np.mean(vol[-20:])) if len(vol) >= 20 else float(np.mean(vol))
    cur_vol  = float(vol[-1])
    high_volume = cur_vol > avg_vol * 1.2

    bar_rng  = hi[-1] - lo[-1]
    close_pos = (cl[-1] - lo[-1]) / bar_rng if bar_rng > 0 else 0.5
    is_green = cl[-1] > op[-1]
    is_red   = cl[-1] < op[-1]
    buyer_conviction  = high_volume and close_pos >= 0.5 and is_green
    seller_conviction = high_volume and close_pos <  0.5 and is_red

    # Trend (10-bar)
    is_uptrend   = hi[-1] > hi[-10] and lo[-1] > lo[-10]
    is_downtrend = hi[-1] < hi[-10] and lo[-1] < lo[-10]

    # Weinstein
    close_s = pd.Series(cl)
    ma30 = close_s.rolling(30).mean()
    ma10 = close_s.rolling(10).mean()
    cur_ma30 = float(ma30.iloc[-1]) if not pd.isna(ma30.iloc[-1]) else cl[-1]
    cur_ma10 = float(ma10.iloc[-1]) if not pd.isna(ma10.iloc[-1]) else cl[-1]
    ma30_prev10 = float(ma30.iloc[-10]) if len(ma30) >= 10 and not pd.isna(ma30.iloc[-10]) else cur_ma30
    ma30_slope  = (cur_ma30 - ma30_prev10) / ma30_prev10 if ma30_prev10 > 0 else 0
    ma10_below_ma30 = cur_ma10 < cur_ma30
    ma_turning_down = (cur_ma30 < float(ma30.iloc[-2]) < float(ma30.iloc[-4])
                       if len(ma30) >= 4 else False)

    hi52 = float(hi[-252:].max()) if len(hi) >= 252 else float(hi.max())
    lo52 = float(lo[-252:].min()) if len(lo) >= 252 else float(lo.min())
    rng52 = hi52 - lo52
    price_pos  = (cl[-1] - lo52) / rng52 * 100 if rng52 > 0 else 50
    dist_hi    = (hi52 - cl[-1]) / cl[-1] * 100 if cl[-1] > 0 else 0

    avg_vol10 = float(np.mean(vol[-10:])) if len(vol) >= 10 else avg_vol
    avg_vol4  = float(np.mean(vol[-4:]))  if len(vol) >= 4  else avg_vol
    vol_building = avg_vol4 > avg_vol10 * 1.1

    breakout_score  = (0 if ma10_below_ma30 else 1) + (1 if vol_building else 0) + (0 if dist_hi > 15 else 1)
    breakdown_score = (1 if ma_turning_down else 0) + (1 if ma10_below_ma30 else 0) + (1 if vol_building else 0) + (1 if dist_hi > 15 else 0)

    # Bias
    sw_lo20 = float(lo[-20:].min()) if len(lo) >= 20 else float(lo.min())
    sw_hi20 = float(hi[-20:].max()) if len(hi) >= 20 else float(hi.max())
    mid20   = (sw_lo20 + sw_hi20) / 2
    is_bullish_bias = cl[-1] > mid20
    is_bearish_bias = cl[-1] < mid20

    # FVG detection
    fvg_details = None
    has_bullish_fvg = has_bearish_fvg = False
    if len(df) >= 4:
        for i in range(1, min(5, len(df) - 3)):
            if hi[-(i+1)] < lo[-(i+3)]:
                top = lo[-(i+3)]; bot = hi[-(i+1)]
                pct = (top - bot) / cl[-1] * 100
                if pct >= 0.5:
                    has_bearish_fvg = True
                    fvg_details = {"type": "BEARISH", "top": round(top, 2),
                                   "bottom": round(bot, 2), "size_pct": round(pct, 2)}
                    break
        if not has_bearish_fvg:
            for i in range(1, min(5, len(df) - 3)):
                if lo[-(i+1)] > hi[-(i+3)]:
                    top = lo[-(i+1)]; bot = hi[-(i+3)]
                    pct = (top - bot) / cl[-1] * 100
                    if pct >= 0.5:
                        has_bullish_fvg = True
                        fvg_details = {"type": "BULLISH", "top": round(top, 2),
                                       "bottom": round(bot, 2), "size_pct": round(pct, 2)}
                        break

    long_tier1  = is_bullish_swing and buyer_conviction and is_uptrend and is_bullish_bias
    long_tier2  = is_bullish_swing and is_bullish_bias and breakout_score >= 3 and not seller_conviction
    short_tier1 = is_bearish_swing and seller_conviction and is_downtrend and is_bearish_bias
    short_tier2 = is_bearish_swing and is_bearish_bias and breakout_score <= 3 and not buyer_conviction

    return {
        "is_bullish_swing": bool(is_bullish_swing), "is_bearish_swing": bool(is_bearish_swing),
        "buyer_conviction": bool(buyer_conviction), "seller_conviction": bool(seller_conviction),
        "is_uptrend": bool(is_uptrend), "is_downtrend": bool(is_downtrend),
        "is_bullish_bias": bool(is_bullish_bias), "is_bearish_bias": bool(is_bearish_bias),
        "ma30": round(cur_ma30, 2), "ma10": round(cur_ma10, 2),
        "ma10_below_ma30": bool(ma10_below_ma30), "ma_turning_down": bool(ma_turning_down),
        "breakout_score": int(breakout_score), "breakdown_score": int(breakdown_score),
        "price_position": round(price_pos, 1), "dist_from_high": round(dist_hi, 1),
        "vol_building": bool(vol_building),
        "has_bullish_fvg": bool(has_bullish_fvg), "has_bearish_fvg": bool(has_bearish_fvg),
        "fvg_details": fvg_details,
        "long_signal": bool(long_tier1 or long_tier2),
        "long_tier": "T1" if long_tier1 else ("T2" if long_tier2 else None),
        "short_signal": bool(short_tier1 or short_tier2),
        "short_tier": "T1" if short_tier1 else ("T2" if short_tier2 else None),
    }


# ── Verdict / Confidence / Score ──────────────────────────────────────────────

def compute_verdict_confidence_score(signals: list, signal_names: list,
                                     primary_candle_bias: str,
                                     vol_profile: Optional[dict]) -> dict:
    vol_trend = vol_profile["vol_trend"] if vol_profile else "FLAT"
    vol_bias  = vol_profile["vol_bias"]  if vol_profile else "NEUTRAL"

    if vol_trend == "DISTRIBUTING":
        if vol_bias == "BULLISH":
            signals.append(-1); signal_names.append("VolTrend:DIST-")
        elif vol_bias == "BEARISH":
            signals.append(+1); signal_names.append("VolTrend:DIST+")

    if not signals:
        return {"verdict": "NEUTRAL", "confidence": "N/A", "score": 0, "signals": signal_names}

    score = sum(signals)
    if   score >= 3:  verdict = "BULLISH"
    elif score <= -3: verdict = "BEARISH"
    elif score >= 2:  verdict = "LEAN BULLISH"
    elif score <= -2: verdict = "LEAN BEARISH"
    elif score > 0:   verdict = "LEAN BULLISH"
    elif score < 0:   verdict = "LEAN BEARISH"
    else:             verdict = "NEUTRAL"

    bull_cnt = sum(1 for s in signals if s > 0)
    bear_cnt = sum(1 for s in signals if s < 0)
    divergent = bear_cnt if primary_candle_bias == "BULLISH" else (
                bull_cnt if primary_candle_bias == "BEARISH" else 0)

    if primary_candle_bias in ("BULLISH", "BEARISH"):
        if   abs(score) >= 4 and divergent == 0: confidence = "HIGH"
        elif abs(score) >= 3 and divergent <= 1: confidence = "HIGH"
        elif divergent == 0:                     confidence = "MEDIUM"
        elif divergent == 1:                     confidence = "MEDIUM"
        else:                                    confidence = "LOW"
    else:
        confidence = "N/A"

    return {"verdict": verdict, "confidence": confidence,
            "score": score, "signals": signal_names}


# ── Entry Grade ───────────────────────────────────────────────────────────────

def get_entry_grade(score: int, confidence: str) -> dict:
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
    return {"entry_grade": grade, "entry_label": label,
            "expected_wr": wr, "expected_avg": avg_pnl, "grade_color": color}


# ── Entry / Stop / Target levels ──────────────────────────────────────────────

def calc_trade_levels(daily_df: pd.DataFrame, verdict: str, current_price: float) -> dict:
    atr = calc_atr(daily_df)
    close_col = _col(daily_df, "close")
    hi_col    = _col(daily_df, "high")
    lo_col    = _col(daily_df, "low")

    # Mean-reverting if ATR% < 2%
    atr_pct  = atr / current_price if current_price > 0 else 0
    is_mean_rev = atr_pct < 0.02
    atr_mult = 0.3 if is_mean_rev else 0.5

    recent_lo = float(daily_df[lo_col].iloc[-10:].min())
    recent_hi = float(daily_df[hi_col].iloc[-10:].max())
    avg_daily_move = atr * 0.6

    entry = round(current_price, 2)

    if verdict in ("BULLISH", "LEAN BULLISH"):
        stop   = round(recent_lo - atr * atr_mult, 2)
        risk   = entry - stop
        t1     = round(entry + risk * 2, 2)
        t2     = round(entry + risk * 3, 2)
        rr1    = round((t1 - entry) / risk, 2) if risk > 0 else 0
        rr2    = round((t2 - entry) / risk, 2) if risk > 0 else 0
        t1_days = max(1, round((t1 - entry) / avg_daily_move)) if avg_daily_move > 0 else None
        t2_days = max(1, round((t2 - entry) / avg_daily_move)) if avg_daily_move > 0 else None
        swing_invalid = round(recent_lo, 2)
        swing_invalid_text = f"Close < ${swing_invalid:.2f} (10-bar low)"
    elif verdict in ("BEARISH", "LEAN BEARISH"):
        stop   = round(recent_hi + atr * atr_mult, 2)
        risk   = stop - entry
        t1     = round(entry - risk * 2, 2)
        t2     = round(entry - risk * 3, 2)
        rr1    = round((entry - t1) / risk, 2) if risk > 0 else 0
        rr2    = round((entry - t2) / risk, 2) if risk > 0 else 0
        t1_days = max(1, round((entry - t1) / avg_daily_move)) if avg_daily_move > 0 else None
        t2_days = max(1, round((entry - t2) / avg_daily_move)) if avg_daily_move > 0 else None
        swing_invalid = round(recent_hi, 2)
        swing_invalid_text = f"Close > ${swing_invalid:.2f} (10-bar high)"
    else:
        return {"entry": entry, "stop_loss": None, "target1": None, "target2": None,
                "risk_pct": None, "rr_t1": None, "rr_t2": None,
                "t1_days": None, "t2_days": None, "atr": round(atr, 2),
                "swing_invalidation": None, "swing_invalidation_text": None}

    risk_pct = round(abs(risk / entry) * 100, 2) if entry > 0 else None
    return {"entry": entry, "stop_loss": stop, "target1": t1, "target2": t2,
            "risk_pct": risk_pct, "rr_t1": rr1, "rr_t2": rr2,
            "t1_days": t1_days, "t2_days": t2_days, "atr": round(atr, 2),
            "swing_invalidation": swing_invalid,
            "swing_invalidation_text": swing_invalid_text}


# ── Full scoring pipeline (replaces simple MTF-only signal) ──────────────────

def full_score_pipeline(daily_df: pd.DataFrame) -> dict:
    """
    Matches stock_pulse.py scoring exactly:
      1. Daily candle direction — ×2 weight (primary, backtest-proven)
      2. Fibonacci bias          — ±1
      3. Fib + S/R confluence    — ±1 bonus when price near both a Fib level AND S/R
      4. Volume profile bias     — ±1
      5. Volume surge            — ±1
      6. Weinstein / FVG / strategy signals — ±1 each (supplementary)
      7. Weekly bias             — ±1
    Exceptional threshold: score ≥ 4 + HIGH confidence (grade A/S).
    """
    if daily_df.empty or len(daily_df) < 30:
        return {"verdict": "NEUTRAL", "confidence": "N/A", "score": 0, "signals": []}

    close_col = _col(daily_df, "close")
    open_col  = _col(daily_df, "open")
    hi_col    = _col(daily_df, "high")
    lo_col    = _col(daily_df, "low")

    current_price = float(daily_df[close_col].iloc[-1])
    last_open     = float(daily_df[open_col].iloc[-1])
    signals: list[int] = []
    signal_names: list[str] = []

    # ── 1. Daily candle direction — ×2 (primary signal, backtest-proven) ──────
    if current_price > last_open:
        signals.extend([+1, +1]); signal_names.append("Day:BULL(×2)")
        candle_bias = "BULLISH"
    elif current_price < last_open:
        signals.extend([-1, -1]); signal_names.append("Day:BEAR(×2)")
        candle_bias = "BEARISH"
    else:
        candle_bias = "NEUTRAL"

    # ── 2. Fibonacci bias ─────────────────────────────────────────────────────
    hi52 = float(daily_df[hi_col].tail(252).max())
    lo52 = float(daily_df[lo_col].tail(252).min())
    fib_levels = calc_fib_levels(lo52, hi52)
    fib_label, fib_val = nearest_fib(current_price, fib_levels)
    fib_above_mid = current_price > (lo52 + hi52) / 2

    if fib_above_mid:
        signals.append(+1); signal_names.append(f"Fib:Bull({fib_label})")
    else:
        signals.append(-1); signal_names.append(f"Fib:Bear({fib_label})")

    # ── 3. Fib + S/R confluence bonus ─────────────────────────────────────────
    # Price within 1.5% of a fib level AND near a pivot S/R level
    sr = calc_support_resistance(daily_df, current_price, n_levels=5)
    sr_levels = [v for v in sr["support"] + sr["resistance"] if v > 0]
    near_fib = abs(current_price - fib_val) / current_price < 0.015 if fib_val else False
    near_sr  = any(abs(current_price - s) / current_price < 0.015 for s in sr_levels)
    if near_fib and near_sr:
        if fib_above_mid:
            signals.append(+1); signal_names.append("Fib+SR:Bull")
        else:
            signals.append(-1); signal_names.append("Fib+SR:Bear")

    # ── 4. Volume profile bias ────────────────────────────────────────────────
    vol_profile = analyze_volume_profile(daily_df)
    if vol_profile:
        vb = vol_profile["vol_bias"]
        if vb == "BULLISH":   signals.append(+1); signal_names.append("Vol:Bull")
        elif vb == "BEARISH": signals.append(-1); signal_names.append("Vol:Bear")

        # ── 5. Volume surge ───────────────────────────────────────────────────
        if vol_profile["vol_surge"]:
            if vb == "BULLISH":   signals.append(+1); signal_names.append("VolSurge:Bull")
            elif vb == "BEARISH": signals.append(-1); signal_names.append("VolSurge:Bear")

    # ── 6. Supplementary: Weinstein, FVG, strategy signals ───────────────────
    strat = analyze_strategy_signals(daily_df)
    if "error" not in strat:
        if not strat["ma10_below_ma30"] and strat["vol_building"]:
            signals.append(+1); signal_names.append("Weinstein:Bull")
        elif strat["ma10_below_ma30"] and strat["ma_turning_down"]:
            signals.append(-1); signal_names.append("Weinstein:Bear")

        if strat["has_bullish_fvg"]:  signals.append(+1); signal_names.append("FVG:Bull")
        elif strat["has_bearish_fvg"]: signals.append(-1); signal_names.append("FVG:Bear")

        if strat["long_signal"]:   signals.append(+1); signal_names.append(f"Strategy:Long{strat['long_tier']}")
        if strat["short_signal"]:  signals.append(-1); signal_names.append(f"Strategy:Short{strat['short_tier']}")
    else:
        strat = {}

    # ── 7. Weekly bias ────────────────────────────────────────────────────────
    wb = compute_weekly_bias(daily_df)
    if wb == "BULLISH":   signals.append(+1); signal_names.append("Weekly:Bull")
    elif wb == "BEARISH": signals.append(-1); signal_names.append("Weekly:Bear")

    result = compute_verdict_confidence_score(signals, signal_names, candle_bias, vol_profile)
    result["strategy_signals"] = strat
    result["vol_profile"] = vol_profile
    result["fib_nearest"] = fib_label
    return result


# ── Weekly / Daily / 4H bias (kept for MTF display) ──────────────────────────

def compute_weekly_bias(daily_df: pd.DataFrame) -> str:
    try:
        if daily_df.empty or len(daily_df) < 10:
            return "NEUTRAL"
        df = daily_df.copy()
        df.index = pd.to_datetime(df.index)
        cc = _col(df, "close"); hc = _col(df, "high"); lc = _col(df, "low")
        weekly = df.resample("W").agg({cc: "last", hc: "max", lc: "min"}).dropna()
        if len(weekly) < 3:
            return "NEUTRAL"
        curr_close = float(weekly[cc].iloc[-1])
        prev_high  = float(weekly[hc].iloc[-2])
        prev_low   = float(weekly[lc].iloc[-2])
        hh = weekly[hc].iloc[-3:].tolist()
        hl = weekly[lc].iloc[-3:].tolist()
        uptrend   = (hh[-1] > hh[-2] > hh[-3]) or (hl[-1] > hl[-2])
        downtrend = (hh[-1] < hh[-2] < hh[-3]) or (hl[-1] < hl[-2])
        if curr_close > prev_high or uptrend:   return "BULLISH"
        elif curr_close < prev_low or downtrend: return "BEARISH"
        return "NEUTRAL"
    except Exception:
        return "NEUTRAL"


def compute_daily_bias(daily_df: pd.DataFrame) -> str:
    try:
        if daily_df.empty or len(daily_df) < 5:
            return "NEUTRAL"
        cc = _col(daily_df, "close")
        closes = daily_df[cc].tail(5).values
        if closes[-1] > closes[-2] > closes[-3]: return "BULLISH"
        elif closes[-1] < closes[-2] < closes[-3]: return "BEARISH"
        return "NEUTRAL"
    except Exception:
        return "NEUTRAL"


def compute_4h_bias(hourly_df: pd.DataFrame, daily_df: pd.DataFrame) -> str:
    try:
        if not hourly_df.empty and len(hourly_df) >= 4:
            hdf = hourly_df.copy()
            hdf.index = pd.to_datetime(hdf.index)
            cc = _col(hdf, "close"); oc = _col(hdf, "open")
            hc = _col(hdf, "high");  lc = _col(hdf, "low")
            bars4h = hdf.resample("4h").agg({oc: "first", hc: "max", lc: "min", cc: "last"}).dropna()
            if len(bars4h) >= 2:
                last, prev = bars4h.iloc[-1], bars4h.iloc[-2]
                if float(last[cc]) > float(last[oc]) or float(last[cc]) > float(prev[hc]):
                    return "BULLISH"
                elif float(last[cc]) < float(last[oc]) or float(last[cc]) < float(prev[lc]):
                    return "BEARISH"
                return "NEUTRAL"
        if not daily_df.empty and len(daily_df) >= 2:
            cc = _col(daily_df, "close"); oc = _col(daily_df, "open")
            last = daily_df.iloc[-1]
            if float(last[cc]) > float(last[oc]):   return "BULLISH"
            elif float(last[cc]) < float(last[oc]): return "BEARISH"
        return "NEUTRAL"
    except Exception:
        return "NEUTRAL"


# ── Buy-The-Dip EMA state machine (gamma/GEX gate deferred) ──────────────────

def _ema(series: pd.Series, span: int) -> pd.Series:
    return series.ewm(span=span, adjust=False).mean()


def compute_btd(daily_df: pd.DataFrame, regime_ok: bool = True) -> dict:
    """
    EMA-only Buy-The-Dip state machine. The gamma/GEX gate from the paper
    design is NOT implemented yet — `regime_ok` stands in for it.

    Layered rule:
      GATE     close > 200EMA  AND  50EMA flat-or-rising  AND  regime_ok
      TREND    close > 50EMA
      DIP      pullback toward 20EMA (shallow) … 50EMA (deep)
      TRIGGER  close reclaims 20EMA after having dipped below it

    regime_ok: index-level regime gate. For SPY this is a VIX proxy until a
    real gamma-flip / GEX gate exists. For single names pass True — the badge
    then reflects the stock's own structure and the trader combines it with
    the market-level badge (see UI).

    States: TRIGGER · ARMED · ARMED-DEEP · DISARMED · N/A
    """
    out = {
        "btd_state": "N/A", "btd_zone": None, "btd_reason": None,
        "btd_size": None,
        "ema20": None, "ema50": None, "ema200": None, "ema50_slope_pct": None,
    }
    try:
        if daily_df is None or daily_df.empty:
            return out
        cc = _col(daily_df, "close")
        close = pd.to_numeric(daily_df[cc], errors="coerce").dropna()
        n = len(close)
        if n < 60:
            out["btd_reason"] = "insufficient history"
            return out

        ema20s  = _ema(close, 20)
        ema50s  = _ema(close, 50)
        ema200s = _ema(close, 200) if n >= 200 else None

        c   = float(close.iloc[-1])
        e20 = float(ema20s.iloc[-1])
        e50 = float(ema50s.iloc[-1])
        e200 = float(ema200s.iloc[-1]) if ema200s is not None else None

        lookback  = 10 if n >= 11 else 1
        e50_prev  = float(ema50s.iloc[-1 - lookback])
        e50_slope = (e50 - e50_prev) / e50_prev if e50_prev > 0 else 0.0
        e50_rising = e50_slope >= -0.002  # flat-or-up tolerance

        out.update({
            "ema20": round(e20, 2),
            "ema50": round(e50, 2),
            "ema200": round(e200, 2) if e200 is not None else None,
            "ema50_slope_pct": round(e50_slope * 100, 2),
        })

        if e200 is None:
            out["btd_reason"] = "need 200+ bars for regime gate"
            return out

        # ── GATE ─────────────────────────────────────────────────────────
        if not regime_ok:
            out.update(btd_state="DISARMED", btd_reason="regime risk-off (gate)")
            return out
        if c <= e200:
            out.update(btd_state="DISARMED", btd_reason="below 200EMA — regime hostile")
            return out
        if not e50_rising:
            out.update(btd_state="DISARMED",
                       btd_reason=f"50EMA flat/falling ({e50_slope * 100:+.1f}%)")
            return out

        # ── ARMED — classify dip depth + reclaim trigger ─────────────────
        above50 = c > e50
        above20 = c > e20
        if above20:
            recent     = close.iloc[-4:-1]
            e20_recent = ema20s.iloc[-4:-1]
            dipped = bool((recent < e20_recent).any())
            if dipped:
                out.update(btd_state="TRIGGER", btd_zone="reclaimed 20EMA",
                           btd_reason="closed back above 20EMA after dip",
                           btd_size="full")
            else:
                out.update(btd_state="ARMED", btd_zone="extended >20EMA",
                           btd_reason="trend OK — wait for pullback to 20EMA",
                           btd_size="full")
        elif above50:
            out.update(btd_state="ARMED", btd_zone="dip 20–50EMA",
                       btd_reason="in buy zone — wait for 20EMA reclaim",
                       btd_size="full")
        else:  # below 50EMA but above 200EMA
            out.update(btd_state="ARMED-DEEP", btd_zone="deep dip <50EMA",
                       btd_reason="degraded — half size, needs confirmation",
                       btd_size="half")
        return out
    except Exception as e:  # never break a scan over the BTD overlay
        out["btd_reason"] = f"error: {str(e)[:60]}"
        return out


def compute_w30(daily_df: pd.DataFrame) -> dict:
    """
    Weinstein 30-week SMA (classic Stage Analysis) computed on weekly bars.

    "Curling up" = the 30-week MA just inflected from flat/declining to
    rising — the Stage 1→2 transition. We flag it when the MA ticked up
    over the last week AND was flat-or-down in the ~3 weeks before that
    (a recent trough), so it catches the turn rather than a MA that has
    been climbing for a long time.
    """
    out = {
        "w30ma": None, "w30ma_curl": False,
        "w30ma_slope_pct": None, "w30ma_reason": None,
    }
    try:
        if daily_df is None or daily_df.empty:
            return out
        df = daily_df.copy()
        df.index = pd.to_datetime(df.index)
        cc = _col(df, "close")
        wclose = (
            pd.to_numeric(df[cc], errors="coerce")
            .resample("W").last().dropna()
        )
        ma = wclose.rolling(30).mean().dropna()
        if len(ma) < 5:
            out["w30ma_reason"] = "need 34+ weeks of history"
            return out

        ma_now  = float(ma.iloc[-1])
        ma_1w   = float(ma.iloc[-2])
        ma_3w   = float(ma.iloc[-4])
        ma_4w   = float(ma.iloc[-5])
        price   = float(wclose.iloc[-1])

        slope_pct = (ma_now - ma_4w) / ma_4w * 100 if ma_4w > 0 else 0.0
        rising_now      = ma_now > ma_1w
        was_not_rising  = ma_1w <= ma_3w          # flat/declining into the turn
        curl_up         = rising_now and was_not_rising

        out["w30ma"] = round(ma_now, 2)
        out["w30ma_slope_pct"] = round(slope_pct, 2)
        px_rel = "above" if price >= ma_now else "below"
        px_pct = (price - ma_now) / ma_now * 100 if ma_now > 0 else 0.0
        out["w30ma_curl"] = bool(curl_up)
        if curl_up:
            out["w30ma_reason"] = (
                f"30wk MA curling up ({slope_pct:+.1f}% / 4wk); "
                f"price {px_pct:+.1f}% {px_rel} MA"
            )
        else:
            trend = "rising" if rising_now else "flat/declining"
            out["w30ma_reason"] = (
                f"30wk MA {trend} ({slope_pct:+.1f}% / 4wk); "
                f"price {px_pct:+.1f}% {px_rel} MA"
            )
        return out
    except Exception as e:  # never break a scan over the W30 overlay
        out["w30ma_reason"] = f"error: {str(e)[:60]}"
        return out


# ── MTF signal → action ───────────────────────────────────────────────────────

def mtf_signal_action(weekly: str, daily: str, h4: str) -> dict:
    def _k(s):
        return "B" if "BULLISH" in (s or "") else ("R" if "BEARISH" in (s or "") else "N")
    key = (_k(weekly), _k(daily), _k(h4))
    MAP = {
        ("B","B","B"): (1, "A+ Long",  "Full size CALL — all TFs agree"),
        ("R","R","R"): (1, "A+ Short", "Full size PUT — all TFs agree"),
        ("B","B","N"): (2, "Strong Long, wait 4H",   "Long confirmed — wait for 4H trigger"),
        ("B","N","B"): (2, "Long pullback entry",    "Weekly up, daily pausing, 4H triggering"),
        ("N","B","B"): (2, "Short-term Long",        "No weekly trend — smaller size"),
        ("R","R","N"): (2, "Strong Short, wait 4H",  "Short confirmed — wait for 4H breakdown"),
        ("R","N","R"): (2, "Short pullback entry",   "Weekly down, daily pausing, 4H confirming"),
        ("N","R","R"): (2, "Short-term Short",       "No weekly trend — smaller size PUT"),
        ("B","B","R"): (3, "Pullback in uptrend",    "4H dip — buy-the-dip if support holds"),
        ("R","R","B"): (3, "Dead cat bounce",        "4H bounce in downtrend — fade or wait"),
        ("B","R","B"): (3, "Choppy / reversal",      "Mixed signals — reduce size"),
        ("R","B","R"): (3, "Counter-trend failing",  "Daily bounce but 4H rejecting"),
        ("B","R","R"): (3, "Trend reversal warning", "Weekly up but D+4H selling — no longs"),
        ("R","B","B"): (3, "Counter-trend bounce",   "D+4H bouncing vs weekly down — risky"),
        ("B","N","N"): (4, "Too early — Long",       "Weekly up, no confirmation — watchlist"),
        ("N","B","N"): (4, "Unconfirmed Long",       "Only daily bullish — need weekly or 4H"),
        ("N","N","B"): (4, "Noise — Long",           "Only 4H up — likely just a bounce"),
        ("R","N","N"): (4, "Too early — Short",      "Weekly down, no confirmation — watchlist"),
        ("N","R","N"): (4, "Unconfirmed Short",      "Only daily bearish — need more"),
        ("N","N","R"): (4, "Noise — Short",          "Only 4H down — likely just a dip"),
        ("B","R","N"): (4, "Conflicted",             "Weekly up, daily down — wait"),
        ("B","N","R"): (4, "4H selling in uptrend",  "Watch for 4H reversal"),
        ("R","B","N"): (4, "Counter-trend attempt",  "Daily bouncing vs weekly down — risky"),
        ("R","N","B"): (4, "4H bounce in downtrend", "Likely dead cat — wait for daily"),
        ("N","B","R"): (4, "Daily up, 4H failing",   "4H rejecting — daily may exhaust"),
        ("N","R","B"): (4, "Daily down, 4H bouncing","Speculative — very small size only"),
        ("N","N","N"): (5, "No edge",                "Sit out — no directional conviction"),
    }
    rank, signal, action = MAP.get(key, (5, "Unknown", "No data"))
    return {"rank": rank, "signal": signal, "action": action,
            "key": f"{key[0]}/{key[1]}/{key[2]}"}


# ── Multiframe bias (live) ────────────────────────────────────────────────────

def get_multiframe_bias(ticker: str) -> Optional[dict]:
    try:
        import yfinance as yf
        cst_tz = pytz.timezone("America/Chicago")
        et_tz  = pytz.timezone("America/New_York")
        now_cst = datetime.now(cst_tz)
        hour, minute = now_cst.hour, now_cst.minute
        is_weekend = now_cst.weekday() >= 5
        is_market  = (not is_weekend) and ((hour > 8 or (hour == 8 and minute >= 20)) and hour < 15)
        tk = yf.Ticker(ticker)

        if is_market:
            hist_5m = tk.history(period="5d", interval="5m", prepost=True)
            if hist_5m.empty:
                return None
            hist_5m.index = pd.to_datetime(hist_5m.index)
            hist_10m = hist_5m.resample("10min").agg(
                {"Open": "first", "High": "max", "Low": "min", "Close": "last", "Volume": "sum"}
            ).dropna()
            today_820 = cst_tz.localize(
                datetime(now_cst.year, now_cst.month, now_cst.day, 8, 20)
            ).astimezone(et_tz)
            idx = hist_10m.index
            idx = idx.tz_convert("America/New_York") if idx.tz else idx.tz_localize("America/New_York")
            hist_s = hist_10m[idx >= today_820]
            if hist_s.empty:
                return None
            ref = float(hist_s["Open"].iloc[0])
            current_price = float(hist_s["Close"].iloc[-1])
            avg_s = float(hist_s["Close"].mean())
            hist_1h = tk.history(period="5d", interval="1h", prepost=True)
            if hist_1h.empty:
                return None
            hdf = hist_1h.copy(); hdf.index = pd.to_datetime(hdf.index)
            bars_4h = hdf.resample("4h").agg(
                {"Open": "first", "High": "max", "Low": "min", "Close": "last", "Volume": "sum"}
            ).dropna()
            b4h_idx = bars_4h.index
            b4h_idx = b4h_idx.tz_convert("America/New_York") if b4h_idx.tz else b4h_idx.tz_localize("America/New_York")
            hist_l = bars_4h[b4h_idx < today_820]
            avg_l = float(hist_l["Close"].tail(3).mean()) if not hist_l.empty else ref
            tf_s, tf_l = "10m", "4H"
        else:
            hist_d = tk.history(period="1mo", interval="1d")
            if hist_d.empty:
                return None
            ref = float(hist_d["Open"].iloc[-1])
            current_price = float(hist_d["Close"].iloc[-1])
            avg_s = float(hist_d["Close"].tail(3).mean())
            avg_l = float(hist_d["Close"].tail(10).mean())
            tf_s, tf_l = "3D", "10D"

        def _bias(avg, r):
            return "BULLISH" if avg > r else "BEARISH"

        bs = _bias(avg_s, ref); bl = _bias(avg_l, ref)
        aligned = bs == bl
        return {
            "bias_short": bs, "bias_long": bl, "tf_short": tf_s, "tf_long": tf_l,
            "alignment": "CONFIRMED" if aligned else "DIVERGED",
            "conclusion": (f"✅ BIAS CONFIRMED: Both {tf_s} & {tf_l} show {bs}" if aligned
                           else f"⚠️ BIAS DIVERGED: {tf_s}={bs}, {tf_l}={bl}"),
            "color": "#00e5a0" if aligned else "#f5c842",
            "current_price": round(current_price, 2),
        }
    except Exception:
        return None


# ── Fundamentals ──────────────────────────────────────────────────────────────

def get_fundamentals(ticker: str) -> dict:
    try:
        import yfinance as yf
        info = yf.Ticker(ticker).info
        def _s(key, default=None):
            v = info.get(key, default)
            return v if v not in (None, "N/A", float("inf"), float("-inf")) else default
        mc = _s("marketCap")
        mc_str = (f"${mc/1e12:.2f}T" if mc and mc >= 1e12 else
                  f"${mc/1e9:.2f}B"  if mc and mc >= 1e9  else
                  f"${mc/1e6:.2f}M"  if mc else "N/A")
        short_float = _s("shortPercentOfFloat")
        short_ratio = _s("shortRatio")
        debt = _s("totalDebt")
        debt_to_equity = _s("debtToEquity")
        free_cashflow = _s("freeCashflow")
        operating_cashflow = _s("operatingCashflow")
        current_price = _s("currentPrice") or _s("regularMarketPrice")
        target_mean = _s("targetMeanPrice")
        return {
            "name": _s("longName", ticker), "sector": _s("sector", "N/A"),
            "industry": _s("industry", "N/A"), "market_cap": mc_str,
            "price": current_price,
            "target_mean_price": target_mean,
            "pe_ratio": round(_s("trailingPE", 0) or 0, 2) or "N/A",
            "forward_pe": round(_s("forwardPE", 0) or 0, 2) or "N/A",
            "price_to_book": round(_s("priceToBook", 0) or 0, 2) or "N/A",
            "price_to_sales": round(_s("priceToSalesTrailing12Months", 0) or 0, 2) or "N/A",
            "enterprise_to_revenue": round(_s("enterpriseToRevenue", 0) or 0, 2) or "N/A",
            "enterprise_to_ebitda": round(_s("enterpriseToEbitda", 0) or 0, 2) or "N/A",
            "eps": _s("trailingEps", "N/A"),
            "profit_margin": round((_s("profitMargins", 0) or 0) * 100, 2),
            "dividend_yield": round((_s("dividendYield", 0) or 0) * 100, 2),
            "52w_high": _s("fiftyTwoWeekHigh", "N/A"), "52w_low": _s("fiftyTwoWeekLow", "N/A"),
            "avg_volume": _s("averageVolume", "N/A"), "beta": _s("beta", "N/A"),
            "description": (_s("longBusinessSummary", "") or "")[:400],
            "short_pct_float": round(short_float * 100, 2) if short_float else None,
            "short_ratio":     round(short_ratio, 1) if short_ratio else None,
            "recommendation":  _s("recommendationKey"),
            "num_analysts":    _s("numberOfAnalystOpinions"),
            "earnings_growth": _s("earningsGrowth"),
            "revenue_growth":  _s("revenueGrowth"),
            "total_cash":      _s("totalCash"),
            "market_cap_raw":  mc,
            "total_debt":      debt,
            "debt_to_equity":  round(float(debt_to_equity), 1) if debt_to_equity is not None else None,
            "free_cashflow":   free_cashflow,
            "operating_cashflow": operating_cashflow,
        }
    except Exception:
        return {}


# ── Weekly Fib + 4H RSI ───────────────────────────────────────────────────────

def get_weekly_fib_and_rsi(ticker: str, current_price: float) -> dict:
    try:
        import yfinance as yf
        tk = yf.Ticker(ticker)
        today = date.today()
        week_start = today - timedelta(days=today.weekday())
        wk = tk.history(start=str(week_start - timedelta(days=1)), end=str(today + timedelta(days=1)))
        fib_label = "N/A"
        if not wk.empty:
            wk_hi = float(wk["High"].max()); wk_lo = float(wk["Low"].min())
            rng = wk_hi - wk_lo
            if rng > 0:
                fib_from_hi = (wk_hi - current_price) / rng
                fib_levels = [(0.0,"100%"),(0.236,"78.6%"),(0.382,"61.8%"),
                              (0.5,"50%"),(0.618,"38.2%"),(0.764,"23.6%"),(1.0,"0%")]
                fib_label = min(fib_levels, key=lambda x: abs(x[0] - fib_from_hi))[1]
        hr = tk.history(start=str(week_start - timedelta(days=1)),
                        end=str(today + timedelta(days=1)), interval="1h")
        rsi_val = "N/A"
        if not hr.empty and len(hr) >= 3:
            c4h = hr["Close"].resample("4h").last().dropna()
            if len(c4h) >= 3:
                r = calc_rsi(c4h, min(14, len(c4h) - 1)).iloc[-1]
                if not np.isnan(r):
                    rsi_val = round(float(r), 1)
        return {"weekly_fib": fib_label, "rsi_4h": rsi_val}
    except Exception:
        return {"weekly_fib": "N/A", "rsi_4h": "N/A"}
