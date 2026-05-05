"""
GET /api/analysis/{ticker}
Direction is derived from the signal score — no user toggle needed.
Each step is wrapped individually — partial failures return best-effort data.
"""
from fastapi import APIRouter, HTTPException
from datetime import date, timedelta
import yfinance as yf

from backend.services.analysis import (
    full_score_pipeline, calc_fib_levels, calc_support_resistance,
    compute_weekly_bias, compute_daily_bias, compute_4h_bias,
    mtf_signal_action, get_multiframe_bias, get_entry_grade,
    calc_trade_levels, get_fundamentals, get_weekly_fib_and_rsi, _col,
)
from backend.services.options import get_options_bias, get_options_strategy
from backend.services.market_data import (
    get_daily_bars_alpaca, get_hourly_bars_yfinance, get_ohlcv_for_chart,
)
from backend.config import ALPACA_API_KEY, ALPACA_API_SECRET

router = APIRouter(prefix="/api/analysis", tags=["analysis"])

_EMPTY_GRADE  = {"entry_grade": "D", "entry_label": "N/A", "expected_wr": 0, "expected_avg": 0, "grade_color": "#8b949e"}
_EMPTY_TRADE  = {"entry": 0, "stop_loss": None, "target1": None, "target2": None,
                 "risk_pct": None, "rr_t1": None, "rr_t2": None, "t1_days": None, "t2_days": None, "atr": 0}
_EMPTY_SIGNAL = {"rank": 5, "signal": "No edge", "action": "Sit out", "key": "N/N/N"}


def _safe(fn, default, *args, **kwargs):
    try:
        result = fn(*args, **kwargs)
        return result if result is not None else default
    except Exception:
        return default


@router.get("/{ticker}")
async def get_stock_analysis(ticker: str):
    ticker = ticker.upper().strip()

    # ── 1. Chart data (required — 404 if missing) ─────────────────────────────
    chart_data = _safe(get_ohlcv_for_chart, [], ticker, "6mo")
    if not chart_data:
        raise HTTPException(404, f"No price data found for {ticker}")
    current_price = chart_data[-1]["close"]

    # ── 2. Daily bars ─────────────────────────────────────────────────────────
    end = date.today(); start = end - timedelta(days=365)
    daily_df = _safe(get_daily_bars_alpaca, None, ticker, str(start), str(end), ALPACA_API_KEY, ALPACA_API_SECRET)
    if daily_df is None or daily_df.empty:
        try:
            daily_df = yf.Ticker(ticker).history(period="1y", interval="1d")
            daily_df.columns = [c.lower() for c in daily_df.columns]
        except Exception:
            daily_df = None

    # ── 3. Hourly bars ────────────────────────────────────────────────────────
    hourly_df = _safe(get_hourly_bars_yfinance, None, ticker, str(end - timedelta(days=5)), str(end))

    # ── 4. Full scoring pipeline (objective — no direction input) ─────────────
    import pandas as pd
    _daily = daily_df if daily_df is not None else pd.DataFrame()

    scored     = _safe(full_score_pipeline, {"verdict": "NEUTRAL", "confidence": "N/A", "score": 0, "signals": []}, _daily)
    verdict    = scored.get("verdict", "NEUTRAL")
    confidence = scored.get("confidence", "N/A")
    score      = scored.get("score", 0)

    # Derive direction from verdict — score drives the trade side
    direction = "SHORT" if verdict in ("BEARISH", "LEAN BEARISH") else "LONG"

    entry_grade = _safe(get_entry_grade, _EMPTY_GRADE, score, confidence)

    # ── 5. MTF bias ───────────────────────────────────────────────────────────
    _hourly     = hourly_df if hourly_df is not None else pd.DataFrame()
    weekly_bias = _safe(compute_weekly_bias, "NEUTRAL", _daily)
    daily_bias  = _safe(compute_daily_bias,  "NEUTRAL", _daily)
    h4_bias     = _safe(compute_4h_bias,     "NEUTRAL", _hourly, _daily)
    signal      = _safe(mtf_signal_action, _EMPTY_SIGNAL, weekly_bias, daily_bias, h4_bias)
    mtf_bias    = _safe(get_multiframe_bias, None, ticker)

    # ── 6. Fibonacci levels ───────────────────────────────────────────────────
    fib_levels, nearest_fib_label = {}, "N/A"
    if not _daily.empty and len(_daily) >= 20:
        try:
            hi_col = _col(_daily, "high"); lo_col = _col(_daily, "low")
            hi52 = float(_daily[hi_col].tail(252).max())
            lo52 = float(_daily[lo_col].tail(252).min())
            fib_levels = calc_fib_levels(lo52, hi52)
            nearest_fib_label = min(fib_levels.items(), key=lambda x: abs(current_price - x[1]))[0]
        except Exception:
            pass

    # ── 7. Support / Resistance ───────────────────────────────────────────────
    sr = _safe(calc_support_resistance, {"support": [], "resistance": []}, _daily, current_price)

    # ── 8. Trade levels (uses derived direction via verdict) ──────────────────
    trade_levels = _safe(calc_trade_levels, _EMPTY_TRADE, _daily, verdict, current_price)

    # ── 9. WTD Fib + RSI ─────────────────────────────────────────────────────
    weekly_fib_rsi = _safe(get_weekly_fib_and_rsi, {"weekly_fib": "N/A", "rsi_4h": "N/A"}, ticker, current_price)

    # ── 10. Fundamentals ──────────────────────────────────────────────────────
    fundamentals = _safe(get_fundamentals, {}, ticker)

    # ── 11. Options (direction + zone derived from verdict / Fib) ───────────────
    # zone: price above 61.8% retrace = HIGH, below 38.2% = LOW, else MID
    try:
        _hi = fib_levels.get("R 61.8%") or fib_levels.get("R 38.2%")
        _lo = fib_levels.get("R 38.2%") or fib_levels.get("R 61.8%")
        fib_618 = fib_levels.get("R 61.8%", current_price)
        fib_382 = fib_levels.get("R 38.2%", current_price)
        if current_price >= fib_618:   fib_zone = "HIGH"
        elif current_price <= fib_382: fib_zone = "LOW"
        else:                          fib_zone = "MID"
    except Exception:
        fib_zone = "MID"

    options_bias     = _safe(get_options_bias, {"error": "Unavailable"}, ticker)
    is_exceptional   = entry_grade.get("entry_grade") in ("S", "A", "B", "B-")
    options_strategy = None
    if is_exceptional:
        options_strategy = _safe(get_options_strategy, None, ticker, current_price,
                                 direction, ALPACA_API_KEY, ALPACA_API_SECRET, fib_zone)

    return {
        "ticker":        ticker,
        "current_price": current_price,
        "direction":     direction,
        "chart_data":    chart_data,
        "verdict":       verdict,
        "confidence":    confidence,
        "score":         score,
        "signal_names":  scored.get("signals", []),
        "entry_grade":   entry_grade,
        "trade":         trade_levels,
        "volume_profile": scored.get("vol_profile"),
        "strategy_signals": scored.get("strategy_signals", {}),
        "bias": {
            "weekly": weekly_bias, "daily": daily_bias,
            "h4": h4_bias, "multiframe": mtf_bias,
        },
        "signal":             signal,
        "fib_levels":         fib_levels,
        "nearest_fib":        nearest_fib_label,
        "support_resistance": sr,
        "weekly_fib_rsi":     weekly_fib_rsi,
        "fundamentals":       fundamentals,
        "options": {
            "bias":           options_bias,
            "strategy":       options_strategy,
            "is_exceptional": is_exceptional,
        },
    }
