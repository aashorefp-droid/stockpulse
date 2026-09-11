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
)
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

    options_bias     = _safe(get_options_bias, {"error": "Unavailable"}, ticker,
                             current_price, ALPACA_API_KEY, ALPACA_API_SECRET)
    is_exceptional   = entry_grade.get("entry_grade") in ("S", "A", "B", "B-")
    options_strategy = None
    if is_exceptional:
        options_strategy = _safe(get_options_strategy, None, ticker, current_price,
                                 direction, ALPACA_API_KEY, ALPACA_API_SECRET, fib_zone)

    stock_verdict = _safe(get_stock_verdict, None, ticker)

    return _clean_nans({
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
        "stock_verdict":      stock_verdict,
        "options": {
            "bias":           options_bias,
            "strategy":       options_strategy,
            "is_exceptional": is_exceptional,
        },
    })


@router.get("/verdict/{ticker}")
def get_ticker_verdict(ticker: str):
    data = get_stock_verdict(ticker)
    if not data:
        raise HTTPException(404, f"No verdict found for {ticker}")
    return data


@router.get("/chart-weekly/{ticker}")
def get_weekly_chart_data(ticker: str):
    ticker = ticker.upper().strip()
    try:
        df = yf.Ticker(ticker).history(period="3y", interval="1wk")
        if df.empty:
            raise HTTPException(404, f"No weekly data for {ticker}")
        df = df.dropna(subset=["Open", "High", "Low", "Close"])
        if len(df) < 30:
            raise HTTPException(404, f"Insufficient data for 30W SMA on {ticker}")

        df["sma30"] = df["Close"].rolling(30).mean()
        df["slope"] = df["sma30"].diff()
        df["vol_sma10"] = df["Volume"].rolling(10).mean() if "Volume" in df.columns else None

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

                            label = f"30W Curl ({v_ratio:.1f}x Vol)" if is_vol_surge else "30W MA Curl Up"
                            markers.append({
                                "time": t_str,
                                "position": "belowBar",
                                "color": "#00e5a0" if is_vol_surge else "#4d9fff",
                                "shape": "arrowUp",
                                "text": label,
                                "size": 2,
                            })

        last_sma = round(float(df["sma30"].iloc[-1]), 2) if not math.isnan(df["sma30"].iloc[-1]) else None
        last_slope = round(float(df["slope"].iloc[-1]), 3) if not math.isnan(df["slope"].iloc[-1]) else 0.0
        cur_price = round(float(df["Close"].iloc[-1]), 2)
        trailing_stop = round(last_sma * 0.95, 2) if last_sma else None
        swing_low_8w = round(float(df["Low"].tail(8).min()), 2)

        return _clean_nans({
            "ticker": ticker,
            "bars": bars,
            "volume_bars": volume_bars,
            "sma30": sma_data,
            "markers": markers,
            "last_sma30": last_sma,
            "current_price": cur_price,
            "trailing_stop": trailing_stop,
            "swing_low_8w": swing_low_8w,
            "is_curling_up": bool(last_slope > 0),
            "slope": last_slope,
        })
    except HTTPException:
        raise
@router.get("/chart-daily/{ticker}")
def get_daily_chart_data(ticker: str, as_of: Optional[str] = Query(None)):
    ticker = ticker.upper().strip()
    try:
        from backend.services.trade_backtest import evaluate_trade_outcome
        period = "2y" if as_of else "1y"
        df = yf.Ticker(ticker).history(period=period, interval="1d")
        if df.empty:
            raise HTTPException(404, f"No daily data for {ticker}")
        df = df.dropna(subset=["Open", "High", "Low", "Close"])
        df.columns = [c.lower() for c in df.columns]
        if hasattr(df.index, "tz_localize") and df.index.tz is not None:
            df.index = df.index.tz_localize(None)

        # Historical slice for trade levels evaluation
        if as_of:
            target_ts = pd.Timestamp(as_of)
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
        trade = _safe(calc_trade_levels, _EMPTY_TRADE, df_hist, verdict, cur_price)
        sr = _safe(calc_support_resistance, {"support": [], "resistance": []}, df_hist, cur_price)

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

        return _clean_nans({
            "ticker": ticker,
            "current_price": cur_price,
            "live_price": live_price,
            "as_of": as_of,
            "direction": direction,
            "verdict": verdict,
            "confidence": scored.get("confidence", "N/A"),
            "score": scored.get("score", 0),
            "bars": bars,
            "volume_bars": volume_bars,
            "sma20": sma20_data,
            "sma50": sma50_data,
            "sma200": sma200_data,
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
            },
            "support_resistance": sr,
            "markers": markers,
        })
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

