"""
Volume Profile Backtest Service.
Simulates systematic Volume Profile setups (VAL Bounce, VAH Rejection, VAH Breakout, VAL Breakdown)
using Auction Market Theory across rolling 50-day Volume Profiles.
"""
from __future__ import annotations

import logging
from datetime import date, datetime
from typing import Any, Dict, List, Optional

import numpy as np
import pandas as pd
import yfinance as yf

from backend.services.analysis import analyze_volume_profile

logger = logging.getLogger(__name__)


def run_volume_profile_backtest(
    ticker: str,
    days: int = 252,
    lookback_window: int = 50,
) -> Dict[str, Any]:
    """
    Simulate rolling Volume Profile trades for `ticker` over the past `days`.
    Evaluates:
      1. VAL Bounce (Long Mean Reversion to POC -> VAH)
      2. VAH Rejection (Short Mean Reversion to POC -> VAL)
      3. VAH Breakout (Long Expansion)
      4. VAL Breakdown (Short Expansion)
    """
    ticker = ticker.upper().strip()
    period = "2y" if days > 250 else "1y"

    try:
        df = yf.download(ticker, period=period, interval="1d", progress=False)
    except Exception as e:
        logger.error(f"Error downloading data for {ticker}: {e}")
        return {"error": f"Failed to download data for {ticker}: {str(e)}"}

    if df is None or df.empty or len(df) < lookback_window + 15:
        return {"error": f"Insufficient historical data for {ticker}"}

    if hasattr(df.columns, "levels"):
        df.columns = df.columns.get_level_values(0)

    df = df.dropna(subset=["Open", "High", "Low", "Close", "Volume"])
    df.columns = [c.lower() for c in df.columns]

    total_bars = len(df)
    start_idx = max(lookback_window, total_bars - days)

    trades: List[Dict[str, Any]] = []
    in_trade_until = -1

    for i in range(start_idx, total_bars - 3):
        if i <= in_trade_until:
            continue

        hist_slice = df.iloc[:i]
        vp = analyze_volume_profile(hist_slice, lookback=lookback_window)
        if not vp:
            continue

        val = float(vp.get("val", 0))
        poc = float(vp.get("poc", 0))
        vah = float(vp.get("vah", 0))

        if val <= 0 or poc <= 0 or vah <= 0 or vah <= val:
            continue

        row_i = df.iloc[i]
        c_i = float(row_i["close"])
        h_i = float(row_i["high"])
        l_i = float(row_i["low"])
        v_ratio = float(vp.get("vol_ratio", 1.0))

        setup: Optional[str] = None
        direction: Optional[str] = None
        entry = c_i
        stop: Optional[float] = None
        t1: Optional[float] = None
        t2: Optional[float] = None
        risk: float = 0.0

        # Setup 1: VAL Bounce (Mean Reversion Long)
        if l_i <= val * 1.005 and c_i > val and c_i < poc:
            setup = "VAL Bounce"
            direction = "LONG"
            risk = max(c_i - val * 0.985, c_i * 0.015)
            stop = round(c_i - risk, 2)
            t1 = round(poc, 2) if poc > c_i * 1.005 else round(c_i + 1.2 * risk, 2)
            t2 = round(vah, 2) if vah > t1 * 1.005 else round(c_i + 2.0 * risk, 2)

        # Setup 2: VAH Rejection (Mean Reversion Short)
        elif h_i >= vah * 0.995 and c_i < vah and c_i > poc:
            setup = "VAH Rejection"
            direction = "SHORT"
            risk = max(vah * 1.015 - c_i, c_i * 0.015)
            stop = round(c_i + risk, 2)
            t1 = round(poc, 2) if poc < c_i * 0.995 else round(c_i - 1.2 * risk, 2)
            t2 = round(val, 2) if val < t1 * 0.995 else round(c_i - 2.0 * risk, 2)

        # Setup 3: VAH Breakout (Initiative Expansion Long)
        elif df.iloc[i - 1]["close"] <= vah * 1.005 and c_i > vah * 1.005 and v_ratio >= 1.1:
            setup = "VAH Breakout"
            direction = "LONG"
            risk = max(c_i - vah * 0.99, c_i * 0.015)
            stop = round(c_i - risk, 2)
            t1 = round(c_i + 1.5 * risk, 2)
            t2 = round(c_i + 2.5 * risk, 2)

        # Setup 4: VAL Breakdown (Initiative Expansion Short)
        elif df.iloc[i - 1]["close"] >= val * 0.995 and c_i < val * 0.995 and v_ratio >= 1.1:
            setup = "VAL Breakdown"
            direction = "SHORT"
            risk = max(val * 1.01 - c_i, c_i * 0.015)
            stop = round(c_i + risk, 2)
            t1 = round(c_i - 1.5 * risk, 2)
            t2 = round(c_i - 2.5 * risk, 2)

        if not setup or stop is None or t1 is None or t2 is None or risk <= 0:
            continue

        # Forward simulation across next 20 trading days
        entry_date = df.index[i].strftime("%Y-%m-%d")
        fut = df.iloc[i + 1 : min(i + 22, total_bars)]
        t1_hit = False
        t1_date: Optional[str] = None
        exit_date = fut.index[-1].strftime("%Y-%m-%d") if len(fut) > 0 else entry_date
        exit_price = float(fut.iloc[-1]["close"]) if len(fut) > 0 else entry
        badge = "⏳ TIME EXPIRED"
        days_held = len(fut)
        mfe = 0.0
        mae = 0.0

        for idx, (f_ts, f_row) in enumerate(fut.iterrows()):
            f_h = float(f_row["high"])
            f_l = float(f_row["low"])
            f_date_str = f_ts.strftime("%Y-%m-%d")

            if direction == "LONG":
                mfe = max(mfe, (f_h - entry) / entry * 100)
                mae = min(mae, (f_l - entry) / entry * 100)

                # Target 2 Hit (Full Win)
                if f_h >= t2:
                    badge = "✅ FULL WIN (T1 & T2 HIT)"
                    exit_price = t2
                    exit_date = f_date_str
                    days_held = idx + 1
                    break
                # Target 1 Hit
                elif f_h >= t1 and not t1_hit:
                    t1_hit = True
                    t1_date = f_date_str
                # Stop loss Hit
                elif f_l <= stop:
                    if t1_hit:
                        badge = "✅ WIN (T1 HIT · RUNNER BE)"
                        exit_price = entry  # protected at breakeven
                    else:
                        badge = "❌ STOPPED OUT"
                        exit_price = stop
                    exit_date = f_date_str
                    days_held = idx + 1
                    break
            else:  # SHORT
                mfe = max(mfe, (entry - f_l) / entry * 100)
                mae = min(mae, (entry - f_h) / entry * 100)

                if f_l <= t2:
                    badge = "✅ FULL WIN (T1 & T2 HIT)"
                    exit_price = t2
                    exit_date = f_date_str
                    days_held = idx + 1
                    break
                elif f_l <= t1 and not t1_hit:
                    t1_hit = True
                    t1_date = f_date_str
                elif f_h >= stop:
                    if t1_hit:
                        badge = "✅ WIN (T1 HIT · RUNNER BE)"
                        exit_price = entry
                    else:
                        badge = "❌ STOPPED OUT"
                        exit_price = stop
                    exit_date = f_date_str
                    days_held = idx + 1
                    break
        else:
            # 20 bars elapsed without hitting stop or T2
            if t1_hit:
                badge = "✅ WIN (T1 HIT · EXPIRED)"
                exit_price = t1
            else:
                p_diff = (exit_price - entry) if direction == "LONG" else (entry - exit_price)
                badge = "⏳ TIME EXPIRED (GAIN)" if p_diff > 0 else "⏳ TIME EXPIRED (LOSS)"

        is_win = "WIN" in badge or "GAIN" in badge
        pnl = (exit_price - entry) if direction == "LONG" else (entry - exit_price)
        pnl_pct = (pnl / entry) * 100
        r_mult = pnl / risk if risk > 0 else 0.0

        trades.append({
            "entry_date": entry_date,
            "exit_date": exit_date,
            "setup": setup,
            "direction": direction,
            "val": round(val, 2),
            "poc": round(poc, 2),
            "vah": round(vah, 2),
            "entry": round(entry, 2),
            "stop": round(stop, 2),
            "t1": round(t1, 2),
            "t2": round(t2, 2),
            "exit_price": round(exit_price, 2),
            "result_badge": badge,
            "is_win": is_win,
            "pnl": round(pnl, 2),
            "pnl_pct": round(pnl_pct, 2),
            "r_multiple": f"{r_mult:+.2f}R",
            "r_val": round(r_mult, 2),
            "days_held": days_held,
            "mfe": f"+{mfe:.1f}%",
            "mae": f"{mae:.1f}%",
        })

        in_trade_until = i + days_held

    wins = [t for t in trades if t["is_win"]]
    losses = [t for t in trades if not t["is_win"]]
    total_trades = len(trades)
    win_rate = round(len(wins) / total_trades * 100, 1) if total_trades else 0.0
    net_r = round(sum(t["r_val"] for t in trades), 2)
    net_pnl = round(sum(t["pnl"] for t in trades), 2)
    avg_win_pct = round(sum(t["pnl_pct"] for t in wins) / len(wins), 2) if wins else 0.0
    avg_loss_pct = round(sum(t["pnl_pct"] for t in losses) / len(losses), 2) if losses else 0.0

    gross_profit = sum(t["pnl_pct"] for t in wins if t["pnl_pct"] > 0)
    gross_loss = abs(sum(t["pnl_pct"] for t in losses if t["pnl_pct"] < 0))
    profit_factor = round(gross_profit / gross_loss, 2) if gross_loss > 0 else (99.0 if gross_profit > 0 else 1.0)

    # Setup breakdown
    setup_breakdown = {}
    for stype in ["VAL Bounce", "VAH Rejection", "VAH Breakout", "VAL Breakdown"]:
        st_trades = [t for t in trades if t["setup"] == stype]
        st_wins = [t for t in st_trades if t["is_win"]]
        setup_breakdown[stype] = {
            "total": len(st_trades),
            "wins": len(st_wins),
            "win_rate_pct": round(len(st_wins) / len(st_trades) * 100, 1) if st_trades else 0.0,
            "net_r": round(sum(t["r_val"] for t in st_trades), 2),
        }

    win_rate_badge = f"{len(wins)}/{total_trades} WINS ({win_rate}%) · NET {net_r:+.2f}R"

    # Pre-formatted Markdown Table
    table_rows = [
        f"### 📊 {ticker} Volume Profile Backtest Scorecard ({win_rate_badge})",
        f"- **Scorecard:** **{len(wins)}/{total_trades} Wins ({win_rate}%)** | Net Return: **{net_r:+.2f}R** ({'+$' if net_pnl >= 0 else '-$'}{abs(net_pnl):.2f}/sh) | Profit Factor: **{profit_factor}** | Avg Win: **+{avg_win_pct}%** | Avg Loss: **{avg_loss_pct}%**",
        f"- **Setup Consistency:** VAL Bounces `{setup_breakdown.get('VAL Bounce', {}).get('wins')}/{setup_breakdown.get('VAL Bounce', {}).get('total')}` ({setup_breakdown.get('VAL Bounce', {}).get('win_rate_pct')}%) · VAH Rejections `{setup_breakdown.get('VAH Rejection', {}).get('wins')}/{setup_breakdown.get('VAH Rejection', {}).get('total')}` ({setup_breakdown.get('VAH Rejection', {}).get('win_rate_pct')}%) · VAH Breakouts `{setup_breakdown.get('VAH Breakout', {}).get('wins')}/{setup_breakdown.get('VAH Breakout', {}).get('total')}` ({setup_breakdown.get('VAH Breakout', {}).get('win_rate_pct')}%)",
        "",
        "| Entry Date → Exit | Setup & Bias | VP Levels (VAL / POC / VAH) | Entry / Stop / T1 → T2 | Result Badge | Realized P&L & R | Days Held | MFE / MAE |",
        "| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |",
    ]

    for t in trades[-15:]:  # show up to last 15 trades
        vp_lvl = f"${t['val']} / ${t['poc']} / ${t['vah']}"
        plan = f"${t['entry']} | Stop ${t['stop']} | T1 ${t['t1']} → T2 ${t['t2']}"
        pnl_str = f"{t['pnl_pct']:+.1f}% ({t['r_multiple']})"
        mfe_mae = f"MFE {t['mfe']} / MAE {t['mae']}"
        table_rows.append(
            f"| **{t['entry_date']}** → {t['exit_date']} | `{t['setup']}` ({t['direction']}) | {vp_lvl} | {plan} | {t['result_badge']} | **{pnl_str}** | {t['days_held']}d | {mfe_mae} |"
        )

    ready_markdown_table = "\n".join(table_rows)

    return {
        "ticker": ticker,
        "period_days": days,
        "total_trades": total_trades,
        "wins": len(wins),
        "losses": len(losses),
        "win_rate_pct": win_rate,
        "win_rate_badge": win_rate_badge,
        "net_r_multiple": f"{net_r:+.2f}R",
        "net_r_val": net_r,
        "net_pnl_per_share": net_pnl,
        "profit_factor": profit_factor,
        "avg_win_pct": avg_win_pct,
        "avg_loss_pct": avg_loss_pct,
        "setup_breakdown": setup_breakdown,
        "trades": trades,
        "ready_markdown_table": ready_markdown_table,
    }
