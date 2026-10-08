"""
GET /api/analysis/{ticker}
Direction is derived from the signal score — no user toggle needed.
Each step is wrapped individually — partial failures return best-effort data.
"""
from typing import Optional
from fastapi import APIRouter, HTTPException, Query
from datetime import date, timedelta
import yfinance as yf
import pandas as pd

from backend.services.analysis import (
    full_score_pipeline, calc_fib_levels, calc_support_resistance,
    compute_weekly_bias, compute_daily_bias, compute_4h_bias,
    mtf_signal_action, get_multiframe_bias, get_entry_grade,
    calc_trade_levels, get_fundamentals, get_weekly_fib_and_rsi, _col,
    generate_final_judgement, calc_earnings_fib_analysis,
)
from backend.services.earnings_prediction import calc_fib_earnings_prediction
from backend.services.options import get_options_bias, get_options_strategy
from backend.services.market_data import (
    get_daily_bars_alpaca, get_hourly_bars_yfinance, get_ohlcv_for_chart,
)
from backend.config import ALPACA_API_KEY, ALPACA_API_SECRET
from backend.services.stock_verdicts import get_stock_verdict

router = APIRouter(prefix="/api/analysis", tags=["analysis"])

_EMPTY_GRADE  = {"entry_grade": "D", "entry_label": "N/A", "expected_wr": 0, "expected_avg": 0, "grade_color": "#8b949e"}
_EMPTY_TRADE  = {"entry": 0, "stop_loss": None, "target1": None, "target2": None,
                 "risk_pct": None, "rr_t1": None, "rr_t2": None,
                 "t1_days": None, "t1_days_min": None, "t1_days_max": None,
                 "t1_days_text": None, "t1_days_basis": None,
                 "t2_days": None, "t2_days_min": None, "t2_days_max": None,
                 "t2_days_text": None, "t2_days_basis": None, "atr": 0}
_EMPTY_SIGNAL = {"rank": 5, "signal": "No edge", "action": "Sit out", "key": "N/N/N"}


import math

def _safe(fn, default, *args, **kwargs):
    try:
        result = fn(*args, **kwargs)
        return result if result is not None else default
    except Exception:
        return default


def _clean_nans(obj):
    if isinstance(obj, dict):
        return {k: _clean_nans(v) for k, v in obj.items()}
    elif isinstance(obj, list):
        return [_clean_nans(v) for v in obj]
    elif isinstance(obj, float):
        if math.isnan(obj) or math.isinf(obj):
            return None
        return obj
    return obj


import time

_ANALYSIS_CACHE = {}
_WEEKLY_CACHE = {}
_DAILY_CACHE = {}
_CACHE_TTL_LIVE = 180       # 3 minutes cache for live data
_CACHE_TTL_BACKTEST = 3600  # 1 hour cache for historical backtest


@router.get("/{ticker}")
async def get_stock_analysis(ticker: str, as_of: Optional[str] = Query(None)):
    ticker = ticker.upper().strip()
    cache_key = f"{ticker}:{as_of or 'live'}"
    now = time.time()
    if cache_key in _ANALYSIS_CACHE:
        entry = _ANALYSIS_CACHE[cache_key]
        ttl = _CACHE_TTL_BACKTEST if as_of else _CACHE_TTL_LIVE
        if now - entry["time"] < ttl:
            return entry["data"]

    from datetime import datetime

    as_of_date = None
    if as_of:
        try:
            as_of_date = datetime.strptime(as_of.strip(), "%Y-%m-%d").date()
        except Exception:
            as_of_date = None

    df_future = pd.DataFrame()

    if as_of_date:
        # ── Backtest Mode: slice history up to as_of_date ──────────────────────
        target_ts = pd.Timestamp(as_of_date)
        try:
            df = yf.Ticker(ticker).history(period="3y", interval="1d")
            if df.empty:
                raise HTTPException(404, f"No price data found for {ticker}")
            df.columns = [c.lower() for c in df.columns]
            if hasattr(df.index, "tz_localize") and df.index.tz is not None:
                df.index = df.index.tz_localize(None)

            df_hist = df[df.index <= target_ts]
            if df_hist.empty or len(df_hist) < 20:
                raise HTTPException(404, f"Not enough historical data for {ticker} as of {as_of}")

            df_future = df[df.index > target_ts]
            daily_df = df_hist
            current_price = round(float(daily_df["close"].iloc[-1]), 2)

            chart_slice = df_hist.tail(126)
            chart_data = []
            for i in range(len(chart_slice)):
                chart_data.append({
                    "time": chart_slice.index[i].strftime("%Y-%m-%d"),
                    "open": round(float(chart_slice["open"].iloc[i]), 2),
                    "high": round(float(chart_slice["high"].iloc[i]), 2),
                    "low": round(float(chart_slice["low"].iloc[i]), 2),
                    "close": round(float(chart_slice["close"].iloc[i]), 2),
                    "volume": int(chart_slice["volume"].iloc[i]) if "volume" in chart_slice else 0,
                })
            hourly_df = None
        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(500, f"Error fetching backtest data for {ticker}: {str(e)}")
    else:
        # ── Live Mode (Today) ──────────────────────────────────────────────────
        chart_data = _safe(get_ohlcv_for_chart, [], ticker, "6mo")
        if not chart_data:
            raise HTTPException(404, f"No price data found for {ticker}")
        current_price = chart_data[-1]["close"]

        end = date.today(); start = end - timedelta(days=365)
        daily_df = _safe(get_daily_bars_alpaca, None, ticker, str(start), str(end), ALPACA_API_KEY, ALPACA_API_SECRET)
        if daily_df is None or daily_df.empty:
            try:
                daily_df = yf.Ticker(ticker).history(period="1y", interval="1d")
                daily_df.columns = [c.lower() for c in daily_df.columns]
            except Exception:
                daily_df = None

        hourly_df = _safe(get_hourly_bars_yfinance, None, ticker, str(end - timedelta(days=5)), str(end))

    # ── 4. Full scoring pipeline (objective — no direction input) ─────────────
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
    hi52, lo52 = None, None
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

    # ── 8b. Weekly Fibonacci levels (Week High and Low) ───────────────────────
    weekly_fib_levels, weekly_nearest_fib_label = {}, "N/A"
    wk_lo = trade_levels.get("pw_low")
    wk_hi = trade_levels.get("pw_high")
    wk_range_label = trade_levels.get("pw_range_label")

    if (wk_lo is None or wk_hi is None or wk_hi <= wk_lo) and not _daily.empty and len(_daily) >= 5:
        try:
            hi_col = _col(_daily, "high"); lo_col = _col(_daily, "low")
            wk_hi = round(float(_daily[hi_col].tail(5).max()), 2)
            wk_lo = round(float(_daily[lo_col].tail(5).min()), 2)
            if not wk_range_label:
                wk_range_label = "5-Day Range"
        except Exception:
            pass

    if wk_lo is not None and wk_hi is not None and wk_hi > wk_lo:
        weekly_fib_levels = calc_fib_levels(wk_lo, wk_hi)
        weekly_nearest_fib_label = min(weekly_fib_levels.items(), key=lambda x: abs(current_price - x[1]))[0]

    # ── 8c. Last Earnings Fibonacci Analysis (E-High & E-Low + Where We Are Now) ──
    earnings_fib = _safe(calc_earnings_fib_analysis, {"has_earnings": False}, ticker, _daily, current_price, as_of)

    # ── 8d. Multi-Fib Earnings Outcome Prediction & Helpful Data (Tomorrow filter) ──
    earnings_prediction = _safe(
        calc_fib_earnings_prediction,
        {"prediction": "N/A", "prediction_status": "N/A"},
        ticker, current_price, fib_levels, weekly_fib_levels, earnings_fib,
        hi52, lo52, wk_hi, wk_lo, as_of
    )

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

    options_bias     = _safe(get_options_bias, {"error": "Unavailable"}, ticker,
                             current_price, ALPACA_API_KEY, ALPACA_API_SECRET)
    is_exceptional   = entry_grade.get("entry_grade") in ("S", "A", "B", "B-")
    options_strategy = None
    if is_exceptional:
        options_strategy = _safe(get_options_strategy, None, ticker, current_price,
                                 direction, ALPACA_API_KEY, ALPACA_API_SECRET, fib_zone)

    stock_verdict = _safe(get_stock_verdict, None, ticker)

    backtest_outcome = None
    if as_of_date and not df_future.empty:
        try:
            from backend.services.trade_backtest import evaluate_trade_outcome
            subsequent_bars = []
            for i in range(len(df_future.head(60))):
                subsequent_bars.append({
                    "time": df_future.index[i].strftime("%Y-%m-%d"),
                    "open": round(float(df_future["open"].iloc[i]), 2),
                    "high": round(float(df_future["high"].iloc[i]), 2),
                    "low": round(float(df_future["low"].iloc[i]), 2),
                    "close": round(float(df_future["close"].iloc[i]), 2),
                    "volume": int(df_future["volume"].iloc[i]) if "volume" in df_future else 0,
                })
            backtest_outcome = evaluate_trade_outcome(
                subsequent_bars=subsequent_bars,
                direction=direction,
                entry=trade_levels.get("entry", current_price),
                stop_loss=trade_levels.get("stop_loss"),
                target1=trade_levels.get("target1"),
                target2=trade_levels.get("target2"),
            )
        except Exception as e:
            backtest_outcome = {"error": str(e)}

    res = _clean_nans({
        "ticker":            ticker,
        "is_backtest":       bool(as_of_date),
        "as_of":             as_of.strip() if as_of_date and as_of else None,
        "backtest_outcome":  backtest_outcome,
        "current_price":     current_price,
        "direction":     direction,
        "chart_data":    chart_data,
        "verdict":       verdict,
        "confidence":    confidence,
        "score":         score,
        "signal_names":  scored.get("signals", []),
        "entry_grade":   entry_grade,
        "trade":         trade_levels,
        "volume_profile": scored.get("vol_profile"),
        "final_judgement": _safe(generate_final_judgement, {}, trade_levels, scored.get("vol_profile"), verdict, entry_grade, current_price, fib_levels, nearest_fib_label),
        "strategy_signals": scored.get("strategy_signals", {}),
        "bias": {
            "weekly": weekly_bias, "daily": daily_bias,
            "h4": h4_bias, "multiframe": mtf_bias,
        },
        "signal":             signal,
        "fib_levels":         fib_levels,
        "nearest_fib":        nearest_fib_label,
        "weekly_fib_levels":  weekly_fib_levels,
        "weekly_nearest_fib": weekly_nearest_fib_label,
        "week_high":          wk_hi,
        "week_low":           wk_lo,
        "week_range_label":   wk_range_label,
        "hi_52":              hi52,
        "lo_52":              lo52,
        "earnings_fib":       earnings_fib,
        "earnings_prediction": earnings_prediction,
        "support_resistance": sr,
        "weekly_fib_rsi":     weekly_fib_rsi,
        "fundamentals":       fundamentals,
        "stock_verdict":      stock_verdict,
        "options": {
            "bias":           options_bias,
            "strategy":       options_strategy,
            "is_exceptional": is_exceptional,
        },
    })
    _ANALYSIS_CACHE[cache_key] = {"time": now, "data": res}
    return res


@router.get("/verdict/{ticker}")
def get_ticker_verdict(ticker: str):
    data = get_stock_verdict(ticker)
    if not data:
        raise HTTPException(404, f"No verdict found for {ticker}")
    return data


@router.get("/chart-weekly/{ticker}")
def get_weekly_chart_data(ticker: str, as_of: Optional[str] = Query(None)):
    ticker = ticker.upper().strip()
    cache_key = f"{ticker}:{as_of or 'live'}"
    now = time.time()
    ttl = _CACHE_TTL_BACKTEST if as_of else _CACHE_TTL_LIVE
    if cache_key in _WEEKLY_CACHE:
        entry = _WEEKLY_CACHE[cache_key]
        if now - entry["time"] < ttl:
            return entry["data"]
    try:
        period = "5y" if as_of else "3y"
        df = yf.Ticker(ticker).history(period=period, interval="1wk")
        if df.empty:
            raise HTTPException(404, f"No weekly data for {ticker}")
        df = df.dropna(subset=["Open", "High", "Low", "Close"])
        if hasattr(df.index, "tz_localize") and df.index.tz is not None:
            df.index = df.index.tz_localize(None)
        if len(df) < 30:
            raise HTTPException(404, f"Insufficient data for 30W SMA on {ticker}")

        df["sma30"] = df["Close"].rolling(30).mean()
        df["slope"] = df["sma30"].diff()
        df["vol_sma10"] = df["Volume"].rolling(10).mean() if "Volume" in df.columns else None

        target_ts = None
        df_hist = df
        df_future = pd.DataFrame()
        if as_of:
            try:
                target_ts = pd.Timestamp(as_of)
                df_hist = df[df.index <= target_ts]
                if len(df_hist) < 30:
                    df_hist = df
                else:
                    df_future = df[df.index > target_ts]
            except Exception:
                df_hist = df

        bars = []
        volume_bars = []
        sma_data = []
        markers = []
        last_idx = -100

        for i in range(len(df)):
            t_str = df.index[i].strftime("%Y-%m-%d")
            o = round(float(df["Open"].iloc[i]), 2)
            h = round(float(df["High"].iloc[i]), 2)
            l = round(float(df["Low"].iloc[i]), 2)
            c = round(float(df["Close"].iloc[i]), 2)
            vol = float(df["Volume"].iloc[i]) if "Volume" in df.columns else 0
            v = int(vol) if not math.isnan(vol) else 0

            bars.append({"time": t_str, "open": o, "high": h, "low": l, "close": c, "volume": v})
            volume_bars.append({
                "time": t_str,
                "value": v,
                "color": "rgba(0, 229, 160, 0.4)" if c >= o else "rgba(255, 77, 79, 0.4)"
            })

            sma_val = df["sma30"].iloc[i]
            if not math.isnan(sma_val):
                sma_data.append({"time": t_str, "value": round(float(sma_val), 2)})

                # Check for 30-week MA curling up
                if i >= 32:
                    cur_slope = df["slope"].iloc[i]
                    prev_slope = df["slope"].iloc[i-1]
                    prev2_slope = df["slope"].iloc[i-2]

                    # Turning positive from flat or declining
                    if cur_slope > 0 and (prev_slope <= 0.02 or prev2_slope <= 0) and c >= (sma_val * 0.98):
                        if i - last_idx >= 6:  # at least 6 weeks spacing
                            last_idx = i

                            # Volume surge check
                            v_avg = float(df["vol_sma10"].iloc[i]) if df["vol_sma10"] is not None else 0
                            v_ratio = (v / v_avg) if v_avg > 0 else 1.0
                            is_vol_surge = v_ratio >= 1.25

                            is_future_arrow = target_ts is not None and df.index[i] > target_ts
                            if not is_future_arrow:
                                label = f"30W Curl ({v_ratio:.1f}x Vol)" if is_vol_surge else "30W MA Curl Up"
                                markers.append({
                                    "time": t_str,
                                    "position": "belowBar",
                                    "color": "#00e5a0" if is_vol_surge else "#4d9fff",
                                    "shape": "arrowUp",
                                    "text": label,
                                    "size": 2,
                                })

        hist_len = len(df_hist)
        as_of_time = df_hist.index[-1].strftime("%Y-%m-%d")
        last_sma = round(float(df_hist["sma30"].iloc[-1]), 2) if not math.isnan(df_hist["sma30"].iloc[-1]) else None
        last_slope = round(float(df_hist["slope"].iloc[-1]), 3) if not math.isnan(df_hist["slope"].iloc[-1]) else 0.0
        cur_price = round(float(df_hist["Close"].iloc[-1]), 2)
        trailing_stop = round(last_sma * 0.95, 2) if last_sma else None
        swing_low_8w = round(float(df_hist["Low"].tail(8).min()), 2)
        dist_from_sma = round(float((cur_price - last_sma) / last_sma * 100), 2) if last_sma else 0.0

        if as_of and not df_future.empty:
            markers.append({
                "time": as_of_time,
                "position": "aboveBar",
                "color": "#f59e0b",
                "shape": "circle",
                "text": f"AS OF {as_of}",
                "size": 2,
            })

        hist_bars = bars[:hist_len]
        hist_volume_bars = volume_bars[:hist_len]
        hist_sma_data = [s for s in sma_data if s["time"] <= as_of_time]

        res = _clean_nans({
            "ticker": ticker,
            "is_backtest": bool(as_of),
            "as_of": as_of if as_of else None,
            "as_of_date": as_of_time,
            "bars": bars,
            "volume_bars": volume_bars,
            "sma30": sma_data,
            "hist_bars": hist_bars,
            "hist_volume_bars": hist_volume_bars,
            "hist_sma30": hist_sma_data,
            "markers": markers,
            "last_sma30": last_sma,
            "current_price": cur_price,
            "trailing_stop": trailing_stop,
            "swing_low_8w": swing_low_8w,
            "dist_from_sma30": dist_from_sma,
            "is_curling_up": bool(last_slope > 0),
            "slope": last_slope,
            "subsequent_weeks_count": len(df_future),
        })
        _WEEKLY_CACHE[cache_key] = {"time": now, "data": res}
        return res
    except HTTPException:
        raise
@router.get("/chart-daily/{ticker}")
def get_daily_chart_data(ticker: str, as_of: Optional[str] = Query(None)):
    ticker = ticker.upper().strip()
    cache_key = f"{ticker}:{as_of or 'live'}"
    now = time.time()
    if cache_key in _DAILY_CACHE:
        entry = _DAILY_CACHE[cache_key]
        ttl = _CACHE_TTL_BACKTEST if as_of else _CACHE_TTL_LIVE
        if now - entry["time"] < ttl:
            return entry["data"]
    try:
        from backend.services.trade_backtest import evaluate_trade_outcome
        target_ts = None
        if as_of:
            try:
                target_ts = pd.Timestamp(as_of)
                start_date = (target_ts - pd.Timedelta(days=450)).strftime("%Y-%m-%d")
                df = yf.Ticker(ticker).history(start=start_date, interval="1d")
                if df.empty or len(df) < 20:
                    df = yf.Ticker(ticker).history(period="5y", interval="1d")
            except Exception:
                df = yf.Ticker(ticker).history(period="5y", interval="1d")
        else:
            df = yf.Ticker(ticker).history(period="1y", interval="1d")

        if df.empty:
            raise HTTPException(404, f"No daily data for {ticker}")
        df = df.dropna(subset=["Open", "High", "Low", "Close"])
        df.columns = [c.lower() for c in df.columns]
        if hasattr(df.index, "tz_localize") and df.index.tz is not None:
            df.index = df.index.tz_localize(None)

        # Historical slice for trade levels evaluation
        if target_ts is not None:
            df_hist = df[df.index <= target_ts]
            if len(df_hist) < 20:
                df_hist = df
            df_future = df[df.index > target_ts]
        else:
            df_hist = df
            df_future = pd.DataFrame()

        cur_price = round(float(df_hist["close"].iloc[-1]), 2)
        live_price = round(float(df["close"].iloc[-1]), 2)

        # Simple Moving Averages (20, 50, 200) on full series
        df["sma20"] = df["close"].rolling(20).mean()
        df["sma50"] = df["close"].rolling(50).mean()
        df["sma200"] = df["close"].rolling(200).mean()

        scored = _safe(full_score_pipeline, {"verdict": "NEUTRAL", "confidence": "N/A", "score": 0}, df_hist)
        verdict = scored.get("verdict", "NEUTRAL")
        direction = "SHORT" if verdict in ("BEARISH", "LEAN BEARISH") else "LONG"
        trade = _safe(calc_trade_levels, _EMPTY_TRADE, df_hist, verdict, cur_price, as_of)
        sr = _safe(calc_support_resistance, {"support": [], "resistance": []}, df_hist, cur_price)

        vol_profile = scored.get("vol_profile") or {}
        entry_grade = _safe(get_entry_grade, _EMPTY_GRADE, scored.get("score", 0), scored.get("confidence", "LOW"))

        hi_col = _col(df_hist, "high"); lo_col = _col(df_hist, "low")
        lookback_52w = min(252, len(df_hist))
        hi52 = float(df_hist[hi_col].tail(lookback_52w).max()) if lookback_52w > 0 else cur_price
        lo52 = float(df_hist[lo_col].tail(lookback_52w).min()) if lookback_52w > 0 else cur_price
        fib_levels = calc_fib_levels(lo52, hi52)
        nearest_fib_label = min(fib_levels.items(), key=lambda x: abs(cur_price - x[1]))[0] if fib_levels else "N/A"

        # Weekly Fibonacci levels (Week High and Low)
        weekly_fib_levels, weekly_nearest_fib_label = {}, "N/A"
        wk_lo = trade.get("pw_low")
        wk_hi = trade.get("pw_high")
        wk_range_label = trade.get("pw_range_label")

        if (wk_lo is None or wk_hi is None or wk_hi <= wk_lo) and not df_hist.empty and len(df_hist) >= 5:
            try:
                wk_hi = round(float(df_hist[hi_col].tail(5).max()), 2)
                wk_lo = round(float(df_hist[lo_col].tail(5).min()), 2)
                if not wk_range_label:
                    wk_range_label = "5-Day Range"
            except Exception:
                pass

        if wk_lo is not None and wk_hi is not None and wk_hi > wk_lo:
            weekly_fib_levels = calc_fib_levels(wk_lo, wk_hi)
            weekly_nearest_fib_label = min(weekly_fib_levels.items(), key=lambda x: abs(cur_price - x[1]))[0]

        # Last Earnings Fibonacci Analysis
        earnings_fib = _safe(calc_earnings_fib_analysis, {"has_earnings": False}, ticker, df_hist, cur_price, as_of)

        # Multi-Fib Earnings Outcome Prediction & Helpful Data
        earnings_prediction = _safe(
            calc_fib_earnings_prediction,
            {"prediction": "N/A", "prediction_status": "N/A"},
            ticker, cur_price, fib_levels, weekly_fib_levels, earnings_fib,
            hi52, lo52, wk_hi, wk_lo, as_of
        )

        final_judgement = _safe(generate_final_judgement, {}, trade, vol_profile, verdict, entry_grade, cur_price, fib_levels, nearest_fib_label)

        bars = []
        volume_bars = []
        sma20_data = []
        sma50_data = []
        sma200_data = []

        for i in range(len(df)):
            t_str = df.index[i].strftime("%Y-%m-%d")
            o = round(float(df["open"].iloc[i]), 2)
            h = round(float(df["high"].iloc[i]), 2)
            l = round(float(df["low"].iloc[i]), 2)
            c = round(float(df["close"].iloc[i]), 2)
            vol = float(df["volume"].iloc[i]) if "volume" in df.columns else 0
            v = int(vol) if not math.isnan(vol) else 0

            bars.append({"time": t_str, "open": o, "high": h, "low": l, "close": c, "volume": v})
            volume_bars.append({
                "time": t_str,
                "value": v,
                "color": "rgba(0, 229, 160, 0.4)" if c >= o else "rgba(255, 77, 79, 0.4)"
            })

            if not math.isnan(df["sma20"].iloc[i]):
                sma20_data.append({"time": t_str, "value": round(float(df["sma20"].iloc[i]), 2)})
            if not math.isnan(df["sma50"].iloc[i]):
                sma50_data.append({"time": t_str, "value": round(float(df["sma50"].iloc[i]), 2)})
            if not math.isnan(df["sma200"].iloc[i]):
                sma200_data.append({"time": t_str, "value": round(float(df["sma200"].iloc[i]), 2)})

        target_str = target_ts.strftime("%Y-%m-%d") if (target_ts is not None and not df_future.empty) else None
        hist_bars = [b for b in bars if target_str is None or b["time"] <= target_str]
        hist_volume_bars = [v for v in volume_bars if target_str is None or v["time"] <= target_str]
        hist_sma20 = [s for s in sma20_data if target_str is None or s["time"] <= target_str]
        hist_sma50 = [s for s in sma50_data if target_str is None or s["time"] <= target_str]
        hist_sma200 = [s for s in sma200_data if target_str is None or s["time"] <= target_str]

        # Trade Entry and Exit Points
        entry = trade.get("entry", cur_price)
        stop_loss = trade.get("stop_loss")
        target1 = trade.get("target1")
        target2 = trade.get("target2")
        risk_pct = trade.get("risk_pct")
        rr_t1 = trade.get("rr_t1")
        rr_t2 = trade.get("rr_t2")

        t1_gain_pct = round(abs((target1 - entry) / entry * 100), 2) if target1 and entry else None
        t2_gain_pct = round(abs((target2 - entry) / entry * 100), 2) if target2 and entry else None

        markers = []
        backtest_result = None

        if as_of and not df_future.empty:
            entry_time = df_hist.index[-1].strftime("%Y-%m-%d")
            markers.append({
                "time": entry_time,
                "position": "belowBar" if direction == "LONG" else "aboveBar",
                "color": "#00e5a0" if direction == "LONG" else "#ff4d6a",
                "shape": "arrowUp" if direction == "LONG" else "arrowDown",
                "text": f"ENTRY ${entry}",
                "size": 2,
            })

            future_bars = []
            for f_ts, f_row in df_future.iterrows():
                future_bars.append({
                    "time": f_ts.strftime("%Y-%m-%d"),
                    "open": float(f_row["open"]),
                    "high": float(f_row["high"]),
                    "low": float(f_row["low"]),
                    "close": float(f_row["close"]),
                })

            eval_res = evaluate_trade_outcome(future_bars, direction, entry, stop_loss, target1, target2)
            backtest_result = {
                "as_of": entry_time,
                "outcome": eval_res["outcome"],
                "is_win": eval_res["is_win"],
                "outcome_date": eval_res["outcome_date"],
                "outcome_pnl_pct": eval_res["outcome_pnl_pct"],
                "outcome_days": eval_res["outcome_days"],
                "t1_hit": eval_res["t1_hit"],
                "t1_hit_date": eval_res["t1_hit_date"],
                "mfe_pct": eval_res["mfe_pct"],
                "mae_pct": eval_res["mae_pct"],
                "subsequent_bars_count": len(future_bars),
            }

            if eval_res["outcome_date"]:
                markers.append({
                    "time": eval_res["outcome_date"],
                    "position": "aboveBar" if eval_res["is_win"] else "belowBar",
                    "color": "#34d399" if eval_res["is_win"] else "#ff4d6a",
                    "shape": "circle",
                    "text": f"{eval_res['outcome']} ({eval_res['outcome_pnl_pct']:+0.1f}%)",
                    "size": 2,
                })
        elif len(bars) > 0:
            last_time = bars[-1]["time"]
            markers.append({
                "time": last_time,
                "position": "belowBar" if direction == "LONG" else "aboveBar",
                "color": "#00e5a0" if direction == "LONG" else "#ff4d6a",
                "shape": "arrowUp" if direction == "LONG" else "arrowDown",
                "text": f"ENTRY ${entry}",
                "size": 2,
            })

        res = _clean_nans({
            "ticker": ticker,
            "current_price": cur_price,
            "live_price": live_price,
            "as_of": as_of,
            "is_backtest": bool(as_of and target_ts is not None and not df_future.empty),
            "as_of_date": as_of,
            "subsequent_bars_count": len(df_future),
            "direction": direction,
            "verdict": verdict,
            "confidence": scored.get("confidence", "N/A"),
            "score": scored.get("score", 0),
            "bars": bars,
            "volume_bars": volume_bars,
            "sma20": sma20_data,
            "sma50": sma50_data,
            "sma200": sma200_data,
            "hist_bars": hist_bars,
            "hist_volume_bars": hist_volume_bars,
            "hist_sma20": hist_sma20,
            "hist_sma50": hist_sma50,
            "hist_sma200": hist_sma200,
            "trade": trade,
            "backtest": backtest_result,
            "levels": {
                "entry": entry,
                "entry_zone_min": round(entry * 0.995, 2),
                "entry_zone_max": round(entry * 1.005, 2),
                "stop_loss": stop_loss,
                "risk_pct": risk_pct,
                "target1": target1,
                "target1_gain_pct": t1_gain_pct,
                "rr_t1": rr_t1,
                "target2": target2,
                "target2_gain_pct": t2_gain_pct,
                "rr_t2": rr_t2,
                "t1_days": trade.get("t1_days"),
                "t2_days": trade.get("t2_days"),
                "atr": trade.get("atr"),
                "retest_entry": trade.get("retest_entry"),
                "pw_low": trade.get("pw_low"),
                "pw_high": trade.get("pw_high"),
                "pw_latest_low": trade.get("pw_latest_low"),
                "pw_latest_date": trade.get("pw_latest_date"),
                "pw_latest_day": trade.get("pw_latest_day"),
                "pw_latest_diff_pct": trade.get("pw_latest_diff_pct"),
                "pw_latest_zone_min": trade.get("pw_latest_zone_min"),
                "pw_latest_zone_max": trade.get("pw_latest_zone_max"),
                "pw_latest_risk_pct": trade.get("pw_latest_risk_pct"),
                "pw_latest_rr_t1": trade.get("pw_latest_rr_t1"),
                "pw_latest_rr_t2": trade.get("pw_latest_rr_t2"),
                "pw_latest_t1_gain": trade.get("pw_latest_t1_gain"),
                "pw_latest_t2_gain": trade.get("pw_latest_t2_gain"),
                "pw_avg_low": trade.get("pw_avg_low"),
                "pw_avg_diff_pct": trade.get("pw_avg_diff_pct"),
                "pw_avg_zone_min": trade.get("pw_avg_zone_min"),
                "pw_avg_zone_max": trade.get("pw_avg_zone_max"),
                "pw_avg_risk_pct": trade.get("pw_avg_risk_pct"),
                "pw_avg_rr_t1": trade.get("pw_avg_rr_t1"),
                "pw_avg_rr_t2": trade.get("pw_avg_rr_t2"),
                "pw_avg_t1_gain": trade.get("pw_avg_t1_gain"),
                "pw_avg_t2_gain": trade.get("pw_avg_t2_gain"),
                "pw_latest_red_low": trade.get("pw_latest_red_low"),
                "pw_latest_red_date": trade.get("pw_latest_red_date"),
                "pw_latest_red_day": trade.get("pw_latest_red_day"),
                "pw_latest_red_diff_pct": trade.get("pw_latest_red_diff_pct"),
                "pw_latest_red_zone_min": trade.get("pw_latest_red_zone_min"),
                "pw_latest_red_zone_max": trade.get("pw_latest_red_zone_max"),
                "pw_latest_red_risk_pct": trade.get("pw_latest_red_risk_pct"),
                "pw_latest_red_rr_t1": trade.get("pw_latest_red_rr_t1"),
                "pw_latest_red_rr_t2": trade.get("pw_latest_red_rr_t2"),
                "pw_latest_red_t1_gain": trade.get("pw_latest_red_t1_gain"),
                "pw_latest_red_t2_gain": trade.get("pw_latest_red_t2_gain"),
                "pw_latest_high": trade.get("pw_latest_high"),
                "pw_latest_green_high": trade.get("pw_latest_green_high"),
                "pw_red_day_lows": trade.get("pw_red_day_lows", []),
                "pw_green_day_highs": trade.get("pw_green_day_highs", []),
                "retest_zone_min": trade.get("retest_zone_min"),
                "retest_zone_max": trade.get("retest_zone_max"),
                "retest_label": trade.get("retest_label"),
                "retest_diff_pct": trade.get("retest_diff_pct"),
                "retest_risk_pct": trade.get("retest_risk_pct"),
                "retest_rr_t1": trade.get("retest_rr_t1"),
                "retest_rr_t2": trade.get("retest_rr_t2"),
                "retest_t1_gain": trade.get("retest_t1_gain"),
                "retest_t2_gain": trade.get("retest_t2_gain"),
                "pw_range_label": trade.get("pw_range_label"),
                "last_breakout": trade.get("last_breakout"),
                "last_breakout_date": trade.get("last_breakout_date"),
                "last_breakout_date_label": trade.get("last_breakout_date_label"),
                "last_breakout_days_ago": trade.get("last_breakout_days_ago"),
                "last_breakout_price": trade.get("last_breakout_price"),
                "last_breakout_level": trade.get("last_breakout_level"),
                "last_breakout_gain_pct": trade.get("last_breakout_gain_pct"),
                "last_breakout_vol_ratio": trade.get("last_breakout_vol_ratio"),
                "last_breakout_type": trade.get("last_breakout_type"),
                "weekly_fib_levels": weekly_fib_levels,
                "weekly_nearest_fib": weekly_nearest_fib_label,
                "week_high": wk_hi,
                "week_low": wk_lo,
                "earnings_fib": earnings_fib,
                "earnings_prediction": earnings_prediction,
            },
            "support_resistance": sr,
            "markers": markers,
            "vol_profile": vol_profile,
            "entry_grade": entry_grade,
            "final_judgement": final_judgement,
            "fib_levels": fib_levels,
            "nearest_fib": nearest_fib_label,
            "weekly_fib_levels": weekly_fib_levels,
            "weekly_nearest_fib": weekly_nearest_fib_label,
            "week_high": wk_hi,
            "week_low": wk_lo,
            "week_range_label": wk_range_label,
            "earnings_fib": earnings_fib,
            "earnings_prediction": earnings_prediction,
            "hi_52": hi52,
            "lo_52": lo52,
        })
        _DAILY_CACHE[cache_key] = {"time": now, "data": res}
        return res
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, str(e))


@router.get("/backtest-history/{ticker}")
def get_backtest_history(ticker: str, days: int = Query(365)):
    from backend.services.trade_backtest import run_historical_backtest
    res = run_historical_backtest(ticker, days=days)
    return _clean_nans(res)


@router.get("/tab2/vix-scenario")
def get_vix_scenario(as_of: str = None):
    try:
        df_wk = yf.download("^VIX", period="2y", interval="1wk", progress=False)
        if df_wk is not None and not df_wk.empty:
            if isinstance(df_wk.columns, pd.MultiIndex):
                df_wk.columns = df_wk.columns.get_level_values(0)
            if as_of:
                df_wk = df_wk[df_wk.index <= pd.Timestamp(as_of)]
            if not df_wk.empty:
                c_s = df_wk["Close"].squeeze()
                h_s = df_wk["High"].squeeze()
                l_s = df_wk["Low"].squeeze()
                vix_close = round(float(c_s.iloc[-1]), 2)
                hi10 = round(float(h_s.iloc[-10:].max()), 2)
                lo10 = round(float(l_s.iloc[-10:].min()), 2)
                rng = hi10 - lo10
                pos = round((vix_close - lo10) / rng * 100, 1) if rng > 0 else 50.0
                zone = "HIGH" if pos >= 70 else ("LOW" if pos <= 30 else "MID")
                fib = calc_fib_levels(lo10, hi10)
                fib_rounded = {k: round(v, 2) for k, v in fib.items()}
                conclusion = (
                    f"VIX {'elevated — risk-off' if zone == 'HIGH' else 'low — risk-on' if zone == 'LOW' else 'neutral'} "
                    f"| Hi: ${hi10:.2f} Lo: ${lo10:.2f} | Pos: {pos:.0f}%"
                )
                return {
                    "ticker": "^VIX", "as_of": as_of or str(date.today()),
                    "close": vix_close, "hi10": hi10, "lo10": lo10,
                    "pos": pos, "zone": zone, "conclusion": conclusion, "fib": fib_rounded
                }
    except Exception as e:
        return {"error": str(e)}
    return {"ticker": "^VIX", "close": 0, "zone": "MID", "fib": {}}


_FIB_COL_ORDER = [
    "E 261.8%", "E 200.0%", "E 161.8%", "E 141.4%", "E 127.2%",
    "R 0.0%", "R 23.6%", "R 38.2%", "R 50.0%", "R 61.8%", "R 78.6%", "R 100.0%",
    "N -23.6%", "N -38.2%", "N -50.0%", "N -61.8%", "N -100.0%",
]


@router.get("/tab2/fib-report")
def get_fib_report(tickers: str = "AAPL,MSFT,NVDA,GOOGL,AMZN", as_of: str = None):
    from concurrent.futures import ThreadPoolExecutor
    import numpy as np
    try:
        from news_sentiment import get_news_details
    except Exception:
        get_news_details = None
    from backend.services.analysis import analyze_institutional_control

    t_list = [t.strip().upper() for t in tickers.split(",") if t.strip()]

    def _calc_one(symbol: str):
        try:
            df_daily = yf.download(symbol, period="2y", interval="1d", progress=False)
            if df_daily is None or df_daily.empty:
                return None
            if isinstance(df_daily.columns, pd.MultiIndex):
                df_daily.columns = df_daily.columns.get_level_values(0)

            if as_of:
                df_daily = df_daily[df_daily.index <= pd.Timestamp(as_of)]
            if len(df_daily) < 10:
                return None

            close = round(float(df_daily["Close"].iloc[-1]), 2)

            # Weekly slice (last 5 trading days)
            wk_slice = df_daily.tail(5)
            wk_hi10 = round(float(wk_slice["High"].max()), 2)
            wk_lo10 = round(float(wk_slice["Low"].min()), 2)
            wk_rng = wk_hi10 - wk_lo10
            wk_pos = round((close - wk_lo10) / wk_rng * 100, 1) if wk_rng > 0 else 50.0
            if wk_pos >= 70:
                wk_zone = "HIGH"
                wk_desc = "Near Weekly Hi — Extension territory"
            elif wk_pos <= 30:
                wk_zone = "LOW"
                wk_desc = "Near Weekly Lo — Retrace territory"
            else:
                wk_zone = "MID"
                wk_desc = "Mid Weekly Range — Balanced"

            fib_lvls = calc_fib_levels(wk_lo10, wk_hi10)
            fib_rounded = {k: round(v, 2) for k, v in fib_lvls.items()}

            # 60-day range for Earn Zone
            earn_slice = df_daily.tail(60)
            earn_hi = float(earn_slice["High"].max())
            earn_lo = float(earn_slice["Low"].min())
            earn_rng = earn_hi - earn_lo
            earn_pos = (close - earn_lo) / earn_rng * 100 if earn_rng > 0 else 50.0
            earn_zone = "HIGH" if earn_pos >= 70 else ("LOW" if earn_pos <= 30 else "MID")

            # News Sentiment
            news_txt = "N/A"
            if get_news_details:
                try:
                    nd = get_news_details(symbol)
                    nlbl = nd.get("label", "No")
                    g = nd.get("good_score", 0)
                    b = nd.get("bad_score", 0)
                    if nlbl == "Good":
                        news_txt = f"📰 POSITIVE (+{g}/-{b})"
                    elif nlbl == "Bad":
                        news_txt = f"📰 NEGATIVE (+{g}/-{b})"
                    else:
                        news_txt = f"📰 NEUTRAL (+{g}/-{b})"
                except Exception:
                    news_txt = "N/A"

            # Earnings Bias (Fundamentals)
            fund_txt = "⚖️ NEUTRAL"
            try:
                fund = get_fundamentals(symbol)
                fb = 0
                if fund:
                    eg = fund.get("earnings_growth")
                    rg = fund.get("revenue_growth")
                    tu = fund.get("target_upside")
                    pm = fund.get("profit_margin")
                    if eg and isinstance(eg, (int, float)):
                        fb += 2 if eg > 0.10 else 1 if eg > 0 else -1 if eg > -0.10 else -2
                    if rg and isinstance(rg, (int, float)):
                        fb += 1 if rg > 0.05 else -1 if rg < 0 else 0
                    if tu and isinstance(tu, (int, float)):
                        fb += 1 if tu > 0.10 else -1 if tu < 0 else 0
                    if pm and isinstance(pm, (int, float)):
                        fb += 1 if pm > 0.10 else -1 if pm < 0 else 0
                if fb >= 3: fund_txt = "🐂 BULLISH"
                elif fb >= 1: fund_txt = "🐂 LEAN BULLISH"
                elif fb > -1: fund_txt = "⚖️ NEUTRAL"
                elif fb > -3: fund_txt = "🐻 LEAN BEARISH"
                else: fund_txt = "🐻 BEARISH"
            except Exception:
                fund_txt = "⚖️ NEUTRAL"

            # Institutional vs Retail Control
            inst_str = "N/A"
            try:
                df_wk = df_daily.resample('W-FRI').agg({
                    'Open': 'first', 'High': 'max', 'Low': 'min', 'Close': 'last', 'Volume': 'sum'
                }).dropna()
                if not df_wk.empty:
                    c_str, phase, em, d = analyze_institutional_control(df_wk)
                    if c_str != "N/A":
                        inst_str = f"{em} {c_str} | {phase} ({d}d)"
            except Exception:
                inst_str = "N/A"

            conclusion = f"{wk_desc} | Hi: ${wk_hi10:.2f} Lo: ${wk_lo10:.2f} | {fund_txt} | {news_txt}"

            return {
                "ticker": symbol,
                "as_of": as_of or str(date.today()),
                "close": close,
                "close_on_earn_date": close,
                "earn_zone": earn_zone,
                "weekly_zone": wk_zone,
                "hi": wk_hi10,
                "lo": wk_lo10,
                "pos": wk_pos,
                "inst_control": inst_str,
                "earnings_bias": fund_txt,
                "news_sentiment": news_txt,
                "next_earnings": "N/A",
                "conclusion": conclusion,
                "fib_all": fib_rounded,
                "r_236": fib_rounded.get("R 23.6%", 0),
                "r_382": fib_rounded.get("R 38.2%", 0),
                "r_500": fib_rounded.get("R 50.0%", 0),
                "r_618": fib_rounded.get("R 61.8%", 0),
                "r_786": fib_rounded.get("R 78.6%", 0),
            }
        except Exception:
            return None

    rows = []
    with ThreadPoolExecutor(max_workers=5) as pool:
        results = list(pool.map(_calc_one, t_list))
    rows = [r for r in results if r is not None]

    return {"count": len(rows), "items": rows}

