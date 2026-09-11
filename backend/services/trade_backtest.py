"""
trade_backtest.py — Trade Simulation & Walk-Forward Backtester for Daily Entry & Exit points.
"""
from typing import Optional
import math
import pandas as pd
import yfinance as yf
from datetime import datetime, date, timedelta
from backend.services.analysis import calc_trade_levels, full_score_pipeline, _col

def _safe(fn, default, *args, **kwargs):
    try:
        return fn(*args, **kwargs)
    except Exception:
        return default


def evaluate_trade_outcome(subsequent_bars: list[dict], direction: str, entry: float, stop_loss: float, target1: float, target2: float) -> dict:
    """
    Given an entry point, stop loss, and targets, evaluate forward performance across subsequent daily bars.
    Returns outcome (TARGET 1 HIT, TARGET 2 HIT, STOPPED OUT, ACTIVE), realized return, exit date, days taken.
    """
    outcome = "ACTIVE"
    outcome_date = None
    outcome_pnl_pct = 0.0
    outcome_days = len(subsequent_bars)
    t1_hit = False
    t1_hit_date = None
    t1_hit_days = 0

    max_price = entry
    min_price = entry

    for i, bar in enumerate(subsequent_bars):
        h = float(bar["high"])
        l = float(bar["low"])
        c = float(bar["close"])
        t = bar["time"]

        if h > max_price: max_price = h
        if l < min_price: min_price = l

        if direction == "LONG":
            if not t1_hit and h >= target1:
                t1_hit = True
                t1_hit_date = t
                t1_hit_days = i + 1

            if target2 and h >= target2:
                outcome = "TARGET 2 HIT"
                outcome_date = t
                outcome_pnl_pct = round((target2 - entry) / entry * 100, 2)
                outcome_days = i + 1
                break

            if l <= stop_loss:
                outcome = "STOPPED OUT" if not t1_hit else "PARTIAL STOP (T1 HIT)"
                outcome_date = t
                outcome_pnl_pct = round((stop_loss - entry) / entry * 100, 2) if not t1_hit else round(((target1 - entry) + (stop_loss - entry)) / 2 / entry * 100, 2)
                outcome_days = i + 1
                break
        else:  # SHORT
            if not t1_hit and l <= target1:
                t1_hit = True
                t1_hit_date = t
                t1_hit_days = i + 1

            if target2 and l <= target2:
                outcome = "TARGET 2 HIT"
                outcome_date = t
                outcome_pnl_pct = round((entry - target2) / entry * 100, 2)
                outcome_days = i + 1
                break

            if h >= stop_loss:
                outcome = "STOPPED OUT" if not t1_hit else "PARTIAL STOP (T1 HIT)"
                outcome_date = t
                outcome_pnl_pct = round((entry - stop_loss) / entry * 100, 2) if not t1_hit else round(((entry - target1) + (entry - stop_loss)) / 2 / entry * 100, 2)
                outcome_days = i + 1
                break

    if outcome == "ACTIVE" and t1_hit:
        outcome = "TARGET 1 HIT"
        outcome_date = t1_hit_date
        outcome_days = t1_hit_days
        outcome_pnl_pct = round((target1 - entry) / entry * 100 if direction == "LONG" else (entry - target1) / entry * 100, 2)
    elif outcome == "ACTIVE" and len(subsequent_bars) > 0:
        last_c = subsequent_bars[-1]["close"]
        outcome_pnl_pct = round(((last_c - entry) / entry * 100) if direction == "LONG" else ((entry - last_c) / entry * 100), 2)
        outcome_days = len(subsequent_bars)

    mfe = round((max_price - entry) / entry * 100 if direction == "LONG" else (entry - min_price) / entry * 100, 2)
    mae = round((min_price - entry) / entry * 100 if direction == "LONG" else (entry - max_price) / entry * 100, 2)

    return {
        "outcome": outcome,
        "is_win": bool("TARGET" in outcome or outcome_pnl_pct > 0),
        "outcome_date": outcome_date,
        "outcome_pnl_pct": outcome_pnl_pct,
        "outcome_days": outcome_days,
        "t1_hit": t1_hit,
        "t1_hit_date": t1_hit_date,
        "mfe_pct": mfe,
        "mae_pct": mae,
    }


def run_historical_backtest(ticker: str, days: int = 365, min_score: int = 3) -> dict:
    """
    Simulate daily entries across the past N days for a ticker.
    Returns win rate, total return, trade log, and metrics.
    """
    ticker = ticker.upper().strip()
    period = "2y" if days > 300 else "1y"
    df = yf.Ticker(ticker).history(period=period, interval="1d")
    if df.empty or len(df) < 50:
        return {"error": f"Insufficient data for {ticker}"}

    df = df.dropna(subset=["Open", "High", "Low", "Close"])
    df.columns = [c.lower() for c in df.columns]
    if hasattr(df.index, "tz_localize") and df.index.tz is not None:
        df.index = df.index.tz_localize(None)

    total_bars = len(df)
    start_idx = max(30, total_bars - int(days * (252 / 365)))

    trades = []
    in_trade_until = -1

    for i in range(start_idx, total_bars - 5):
        if i <= in_trade_until:
            continue

        historical_slice = df.iloc[: i + 1]
        cur_p = round(float(historical_slice["close"].iloc[-1]), 2)

        scored = _safe(full_score_pipeline, {"verdict": "NEUTRAL", "confidence": "N/A", "score": 0}, historical_slice)
        score = scored.get("score", 0)
        verdict = scored.get("verdict", "NEUTRAL")

        # Check for qualifying entry trigger
        if abs(score) < min_score or verdict == "NEUTRAL":
            continue

        trade_levels = _safe(calc_trade_levels, {}, historical_slice, verdict, cur_p)
        if not trade_levels or not trade_levels.get("entry") or not trade_levels.get("stop_loss"):
            continue

        entry_date = df.index[i].strftime("%Y-%m-%d")
        direction = "SHORT" if verdict in ("BEARISH", "LEAN BEARISH") else "LONG"
        entry_price = trade_levels["entry"]
        stop_price = trade_levels["stop_loss"]
        t1_price = trade_levels.get("target1")
        t2_price = trade_levels.get("target2")

        # Future bars
        future_df = df.iloc[i + 1 :]
        subsequent = []
        for f_ts, f_row in future_df.iterrows():
            subsequent.append({
                "time": f_ts.strftime("%Y-%m-%d"),
                "open": float(f_row["open"]),
                "high": float(f_row["high"]),
                "low": float(f_row["low"]),
                "close": float(f_row["close"]),
            })

        res = evaluate_trade_outcome(subsequent, direction, entry_price, stop_price, t1_price, t2_price)

        trades.append({
            "ticker": ticker,
            "entry_date": entry_date,
            "exit_date": res["outcome_date"],
            "direction": direction,
            "verdict": verdict,
            "score": score,
            "entry_price": entry_price,
            "stop_loss": stop_price,
            "target1": t1_price,
            "target2": t2_price,
            "outcome": res["outcome"],
            "is_win": res["is_win"],
            "pnl_pct": res["outcome_pnl_pct"],
            "days_held": res["outcome_days"],
            "mfe_pct": res["mfe_pct"],
            "mae_pct": res["mae_pct"],
        })

        if res["outcome_days"] > 0:
            in_trade_until = i + res["outcome_days"]

    wins = [t for t in trades if t["is_win"]]
    losses = [t for t in trades if not t["is_win"]]

    win_rate = round(len(wins) / len(trades) * 100, 1) if trades else 0.0
    avg_win = round(sum(t["pnl_pct"] for t in wins) / len(wins), 2) if wins else 0.0
    avg_loss = round(sum(t["pnl_pct"] for t in losses) / len(losses), 2) if losses else 0.0
    total_pnl = round(sum(t["pnl_pct"] for t in trades), 2) if trades else 0.0

    gross_profit = sum(t["pnl_pct"] for t in wins if t["pnl_pct"] > 0)
    gross_loss = abs(sum(t["pnl_pct"] for t in losses if t["pnl_pct"] < 0))
    profit_factor = round(gross_profit / gross_loss, 2) if gross_loss > 0 else (99.0 if gross_profit > 0 else 1.0)

    return {
        "ticker": ticker,
        "period_days": days,
        "total_trades": len(trades),
        "wins": len(wins),
        "losses": len(losses),
        "win_rate_pct": win_rate,
        "avg_win_pct": avg_win,
        "avg_loss_pct": avg_loss,
        "profit_factor": profit_factor,
        "total_pnl_pct": total_pnl,
        "trades": trades,
    }
