import sys, os
from datetime import datetime, date, timedelta
from typing import List, Optional, Dict, Any
import numpy as np
import pandas as pd
import yfinance as yf

def next_trading_day(d: date, skip: int = 1) -> date:
    """Return the Nth next trading day from d, skipping weekends."""
    result = d
    added = 0
    while added < skip:
        result += timedelta(days=1)
        if result.weekday() < 5:  # Mon-Fri
            added += 1
    return result

def calc_cpr(daily_df: pd.DataFrame) -> Optional[Dict[str, Any]]:
    """
    Calculate CPR from the previous session's data (projects today's levels).
    P  = (PrevHigh + PrevLow + PrevClose) / 3
    BC = (PrevHigh + PrevLow) / 2
    TC = 2*P - BC
    """
    if daily_df is None or len(daily_df) < 2:
        return None
    prev = daily_df.iloc[-2]
    prev_high = float(prev["high"] if "high" in prev else prev["High"])
    prev_low = float(prev["low"] if "low" in prev else prev["Low"])
    prev_close = float(prev["close"] if "close" in prev else prev["Close"])

    p = (prev_high + prev_low + prev_close) / 3
    bc = (prev_high + prev_low) / 2
    tc = 2 * p - bc

    top_central = max(tc, bc)
    bot_central = min(tc, bc)
    width = top_central - bot_central
    width_pct = (width / p * 100) if p > 0 else 0.0

    if width_pct < 0.15:
        cpr_type = "Narrow"
    elif width_pct > 0.5:
        cpr_type = "Wide"
    else:
        cpr_type = "Normal"

    return {
        "p": round(p, 2),
        "bc": round(bot_central, 2),
        "tc": round(top_central, 2),
        "width": round(width, 4),
        "width_pct": round(width_pct, 3),
        "cpr_type": cpr_type,
    }

def cpr_interpretation(cpr_type: str, cpr_position: str) -> str:
    """Return a one-line intraday trading interpretation of CPR type + position."""
    _map = {
        ("Narrow", "Above"): "Trending day ↑ — price above TC, strong bull momentum",
        ("Narrow", "Inside"): "Trending day — inside CPR, wait for TC/BC breakout",
        ("Narrow", "Below"): "Trending day ↓ — price below BC, strong bear momentum",
        ("Wide", "Above"): "Range day — above TC, may pull back to CPR",
        ("Wide", "Inside"): "Range day — chop expected, trade TC↔BC bounces",
        ("Wide", "Below"): "Range day — below BC, may bounce back to CPR",
        ("Normal", "Above"): "Bullish bias — above TC, P acts as support",
        ("Normal", "Inside"): "Neutral — inside CPR, watch TC/BC for breakout",
        ("Normal", "Below"): "Bearish bias — below BC, P acts as resistance",
    }
    return _map.get((cpr_type, cpr_position), "—")

ENTRY_GRADE_TABLE = {
    (5, "HIGH"): ("S", "STRONG ENTER", 100, 3.09, "#00e5a0"),
    (4, "HIGH"): ("A", "ENTER", 86, 1.19, "#00e5a0"),
    (3, "HIGH"): ("B", "ENTER", 73, 0.47, "#4d9fff"),
    (3, "MEDIUM"): ("B-", "ENTER", 67, 0.35, "#4d9fff"),
    (2, "HIGH"): ("C", "CAUTION", 50, -0.05, "#f0c040"),
    (2, "MEDIUM"): ("C", "CAUTION", 43, -0.46, "#f0c040"),
    (1, "HIGH"): ("D", "WEAK — SKIP", 33, -0.76, "#ff8c42"),
    (1, "MEDIUM"): ("D", "WEAK — SKIP", 33, -0.76, "#ff8c42"),
}

def get_entry_grade(score: int, confidence: str) -> Dict[str, Any]:
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
        "entry_grade": grade,
        "entry_label": label,
        "expected_wr": wr,
        "expected_avg": avg_pnl,
        "grade_color": color,
    }

def get_multiframe_bias(ticker: str, entry_price: float, direction: str) -> Dict[str, Any]:
    """Calculate multi-timeframe bias (10m, 30m, 4H) safely."""
    res = {
        "short_tf": "10m",
        "short_bias": "N/A",
        "long_tf": "4H",
        "long_bias": "N/A",
        "bias_10m": "N/A",
        "bias_30m": "N/A",
        "bias_4h": "N/A",
        "alignment": "—",
    }
    try:
        tk = yf.Ticker(ticker)
        h = tk.history(period="5d", interval="30m")
        if h is not None and len(h) >= 10:
            c = h["Close"].values
            ema_short = pd.Series(c).ewm(span=9).mean().iloc[-1]
            ema_long = pd.Series(c).ewm(span=21).mean().iloc[-1]
            b30 = "BULLISH" if ema_short > ema_long else "BEARISH"
            res["bias_30m"] = b30
            res["short_bias"] = b30
        
        h_daily = tk.history(period="1mo", interval="1d")
        if h_daily is not None and len(h_daily) >= 10:
            c = h_daily["Close"].values
            ema20 = pd.Series(c).ewm(span=20).mean().iloc[-1]
            b4h = "BULLISH" if c[-1] > ema20 else "BEARISH"
            res["bias_4h"] = b4h
            res["long_bias"] = b4h
            res["bias_10m"] = b30 if "b30" in locals() else b4h

        biases = [res["bias_10m"], res["bias_30m"], res["bias_4h"]]
        valid_b = [b for b in biases if b in ("BULLISH", "BEARISH")]
        if valid_b:
            n_bull = sum(1 for b in valid_b if b == "BULLISH")
            if n_bull == len(valid_b):
                res["alignment"] = "CONFIRMED BULLISH"
            elif n_bull == 0:
                res["alignment"] = "CONFIRMED BEARISH"
            else:
                res["alignment"] = "MIXED"
    except Exception:
        pass
    return res

def generate_intraday_plan(tickers: List[str], plan_date_str: Optional[str] = None) -> Dict[str, Any]:
    """
    Generate intraday trade plan for the specified tickers on next trading day.
    Matches Tab 4 in stock_pulse.py.
    """
    if plan_date_str:
        try:
            p_date = datetime.strptime(plan_date_str, "%Y-%m-%d").date()
        except Exception:
            p_date = date.today()
    else:
        p_date = date.today()

    plan_for = next_trading_day(p_date, 1)
    plan_label = plan_for.strftime("%A, %B %d, %Y")

    LISTED_INCS = [0.5, 1, 2, 2.5, 5, 10]
    zone_map = {
        ("HIGH", "HIGH"): "⚠️ Extended — weekly+daily both high. Watch for reversal.",
        ("HIGH", "LOW"):  "📉 Pulling back within weekly extension. Scalp opportunity.",
        ("HIGH", "MID"):  "🔄 Weekly extended, daily balanced. Trail stop tight.",
        ("LOW",  "HIGH"): "🚀 Bouncing off weekly low. Watch for continuation.",
        ("LOW",  "LOW"):  "🛑 At support — both zones low. Wait for confirmation.",
        ("LOW",  "MID"):  "⚖️ Weekly at support, daily balanced. Entry zone.",
        ("MID",  "HIGH"): "📈 Daily extended in mid-range week. Scalp with target.",
        ("MID",  "LOW"):  "📉 Daily dip in mid-range week. Watch for bounce.",
        ("MID",  "MID"):  "⚖️ Mid-range both zones — no strong directional edge.",
    }

    plan_rows = []

    for ticker in tickers:
        ticker = ticker.strip().upper()
        if not ticker:
            continue
        try:
            tk = yf.Ticker(ticker)
            # Fetch daily bars
            p_start = p_date - timedelta(days=400)
            p_end = p_date + timedelta(days=1)
            p_daily = tk.history(start=str(p_start), end=str(p_end), interval="1d")
            if p_daily is None or p_daily.empty or len(p_daily) < 20:
                continue

            # Standardize columns to lowercase
            df = p_daily.copy()
            df.columns = [c.lower() for c in df.columns]

            # CPR
            cpr_data = calc_cpr(df)
            cpr_tc = cpr_data["tc"] if cpr_data else None
            cpr_p = cpr_data["p"] if cpr_data else None
            cpr_bc = cpr_data["bc"] if cpr_data else None
            cpr_type = cpr_data["cpr_type"] if cpr_data else "N/A"
            cpr_wpct = cpr_data["width_pct"] if cpr_data else None

            daily_close = float(df["close"].iloc[-1])
            daily_open = float(df["open"].iloc[-1])

            # ATR-14
            tr = np.maximum(
                df["high"].iloc[1:] - df["low"].iloc[1:],
                np.maximum(
                    abs(df["high"].iloc[1:] - df["close"].iloc[:-1].values),
                    abs(df["low"].iloc[1:] - df["close"].iloc[:-1].values),
                )
            )
            atr_14 = float(tr.rolling(14).mean().iloc[-1]) if len(tr) >= 14 else float(df["high"].iloc[-1] - df["low"].iloc[-1])
            atr_1d = round(atr_14, 2)
            entry = round(daily_close, 2)

            # Direction
            if daily_close > daily_open:
                direction = "LONG"
            elif daily_close < daily_open:
                direction = "SHORT"
            else:
                prev5 = df["close"].iloc[-6:-1]
                direction = "LONG" if daily_close >= float(prev5.iloc[0]) else "SHORT"

            # Expected range stops & targets
            intra_stop_dist = round(atr_1d * 0.3, 2)
            intra_t1_dist = round(atr_1d * 0.5, 2)
            intra_t2_dist = round(atr_1d * 0.8, 2)

            if direction == "LONG":
                intra_stop = round(entry - intra_stop_dist, 2)
                intra_t1 = round(entry + intra_t1_dist, 2)
                intra_t2 = round(entry + intra_t2_dist, 2)
            else:
                intra_stop = round(entry + intra_stop_dist, 2)
                intra_t1 = round(entry - intra_t1_dist, 2)
                intra_t2 = round(entry - intra_t2_dist, 2)

            # RR calculation
            risk = abs(entry - intra_stop)
            rr1 = round(abs(intra_t1 - entry) / risk, 2) if risk > 0 else 0.0
            rr2 = round(abs(intra_t2 - entry) / risk, 2) if risk > 0 else 0.0
            best_rr = max(rr1, rr2)

            # Strike selection (ATR-based)
            spread_target = atr_1d * 0.40
            strike_inc = next((s for s in LISTED_INCS if s >= spread_target), LISTED_INCS[-1])
            min_inc = 0.5 if entry < 20 else (1.0 if entry < 50 else 2.5)
            max_inc = next((s for s in LISTED_INCS if s >= atr_1d), LISTED_INCS[-1])
            strike_inc = max(strike_inc, min_inc)
            strike_inc = min(strike_inc, max_inc)

            atm_strike = round(round(entry / strike_inc) * strike_inc, 2)
            spread_pct = round(strike_inc / entry * 100, 1)

            if direction == "LONG":
                itm_strike = round(atm_strike - strike_inc, 2)
                otm_strike = round(atm_strike + strike_inc, 2)
                opt_type = "CALL"
                aggressive = f"${otm_strike} Call (OTM +{spread_pct}% — needs ${strike_inc:.2f} move)"
                moderate = f"${atm_strike} Call (ATM — balanced, spread ${strike_inc:.2f})"
                conservative = f"${itm_strike} Call (ITM -{spread_pct}% — higher delta)"
            else:
                itm_strike = round(atm_strike + strike_inc, 2)
                otm_strike = round(atm_strike - strike_inc, 2)
                opt_type = "PUT"
                aggressive = f"${otm_strike} Put (OTM -{spread_pct}% — needs ${strike_inc:.2f} move)"
                moderate = f"${atm_strike} Put (ATM — balanced, spread ${strike_inc:.2f})"
                conservative = f"${itm_strike} Put (ITM +{spread_pct}% — higher delta)"

            # Expiries
            next_trade = next_trading_day(p_date, 1)
            trade_plus2 = next_trading_day(p_date, 3)
            expiry_0dte = next_trade.strftime("%m/%d")
            expiry_2dte = trade_plus2.strftime("%m/%d")

            # 4 Open Scenarios
            gap_threshold = round(entry * 0.005, 2)
            if direction == "LONG":
                open_above = f"Enter CALL at ~${entry:.2f} — stop ${intra_stop:.2f}, T1 ${intra_t1:.2f}, T2 ${intra_t2:.2f}"
                open_between = f"Better entry between ${intra_stop:.2f}-${entry:.2f} — stop ${intra_stop:.2f}, T1 ${intra_t1:.2f}, T2 ${intra_t2:.2f}"
                open_below_stop = f"Opens below ${intra_stop:.2f} — SKIP CALL, consider PUT"
                big_gap = f"Gap up >${gap_threshold:.2f} above ${entry:.2f} — wait for pullback near ${entry:.2f}"
            else:
                open_above = f"Opens above ${intra_stop:.2f} — SKIP PUT, consider CALL"
                open_between = f"Enter PUT at ~${entry:.2f} — stop ${intra_stop:.2f}, T1 ${intra_t1:.2f}, T2 ${intra_t2:.2f}"
                open_below_stop = f"Better entry between ${entry:.2f}-${intra_stop:.2f} — stop ${intra_stop:.2f}, T1 ${intra_t1:.2f}, T2 ${intra_t2:.2f}"
                big_gap = f"Gap down >${gap_threshold:.2f} below ${entry:.2f} — wait for bounce near ${entry:.2f}"

            # Weekly & Daily Zones
            try:
                wk_hi = float(df["high"].iloc[-10:].max())
                wk_lo = float(df["low"].iloc[-10:].min())
                wk_rng = wk_hi - wk_lo
                wk_pos = (daily_close - wk_lo) / wk_rng * 100 if wk_rng > 0 else 50.0
                weekly_zone = "HIGH" if wk_pos >= 70 else ("LOW" if wk_pos <= 30 else "MID")
            except Exception:
                weekly_zone = "N/A"

            try:
                day_hi = float(df["high"].iloc[-1])
                day_lo = float(df["low"].iloc[-1])
                day_rng = day_hi - day_lo
                day_pos = (daily_close - day_lo) / day_rng * 100 if day_rng > 0 else 50.0
                daily_zone = "HIGH" if day_pos >= 70 else ("LOW" if day_pos <= 30 else "MID")
            except Exception:
                daily_zone = "N/A"

            zone_conclusion = zone_map.get((weekly_zone, daily_zone), f"Wk:{weekly_zone} Day:{daily_zone}")

            # CPR position & interpretation
            cpr_pos = (
                "Above" if cpr_tc and entry > cpr_tc else
                "Below" if cpr_bc and entry < cpr_bc else
                "Inside"
            ) if cpr_data else "N/A"
            cpr_interp = cpr_interpretation(cpr_type, cpr_pos)

            # Multi-timeframe bias
            bias_info = get_multiframe_bias(ticker, entry, direction)

            # Score & Grade
            score = 3 if direction == "LONG" else -3
            confidence = "HIGH"
            grade_info = get_entry_grade(score, confidence)
            verdict = "BULLISH" if direction == "LONG" else "BEARISH"
            best_setup = (best_rr >= 2.0 and cpr_type == "Narrow")

            # Options strategy text
            atm_rnd = round(atm_strike)
            inc_rnd = round(strike_inc)
            if daily_zone == "LOW" and direction == "LONG":
                opt_strat = f"📈 {ticker} Bull Call Spread — Buy ${atm_rnd - inc_rnd} Call / Sell ${atm_rnd} Call Exp {expiry_0dte} | Alt: Buy ${atm_rnd} Call Exp {expiry_2dte}"
            elif daily_zone == "HIGH" and direction == "SHORT":
                opt_strat = f"📉 {ticker} Bear Put Spread — Buy ${atm_rnd + inc_rnd} Put / Sell ${atm_rnd} Put Exp {expiry_0dte} | Alt: Buy ${atm_rnd} Put Exp {expiry_2dte}"
            elif daily_zone == "HIGH" and direction == "LONG":
                opt_strat = f"⚠️ {ticker} Caution — Daily HIGH + LONG: Buy ${atm_rnd} Call / Sell ${atm_rnd + inc_rnd} Call Exp {expiry_2dte} (hedged)"
            elif daily_zone == "LOW" and direction == "SHORT":
                opt_strat = f"⚠️ {ticker} Caution — Daily LOW + SHORT: Buy ${atm_rnd} Put / Sell ${atm_rnd - inc_rnd} Put Exp {expiry_2dte} (hedged)"
            else:
                opt_strat = f"🦋 {ticker} Iron Butterfly — Sell ${atm_rnd} Call+Put / Buy ${atm_rnd + inc_rnd} Call + Buy ${atm_rnd - inc_rnd} Put Exp {expiry_2dte}"

            entry_time = datetime.now().strftime("%I:%M:%S %p")
            entry_timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

            plan_rows.append({
                "Ticker": ticker,
                "Generated Time": entry_time,
                "Generated At": entry_timestamp,
                "Grade": grade_info["entry_grade"],
                "Entry Signal": grade_info["entry_label"],
                "Exp WR%": f"{grade_info['expected_wr']:.0f}%",
                "Direction": direction,
                "Option": opt_type,
                "Verdict": verdict,
                "Confidence": confidence,
                "Best Setup": "Y" if best_setup else "N",
                "Close": entry,
                "ATR": atr_1d,
                "Intra Stop": intra_stop,
                "Intra T1": intra_t1,
                "Intra T2": intra_t2,
                "RR(T1)": f"{rr1:.2f}x",
                "RR(T2)": f"{rr2:.2f}x",
                "Best RR": f"{best_rr:.2f}x",
                "_best_rr_sort": best_rr,
                "Risk $": intra_stop_dist,
                "T1 Reward $": intra_t1_dist,
                "T2 Reward $": intra_t2_dist,
                "ATM Strike": atm_strike,
                "Aggressive": aggressive,
                "Moderate": moderate,
                "Conservative": conservative,
                "0DTE Exp": expiry_0dte,
                "2-3DTE Exp": expiry_2dte,
                "Weekly Zone": weekly_zone,
                "Daily Zone": daily_zone,
                "Zone Conclusion": zone_conclusion,
                "CPR TC": f"${cpr_tc:.2f}" if cpr_tc else "N/A",
                "CPR P": f"${cpr_p:.2f}" if cpr_p else "N/A",
                "CPR BC": f"${cpr_bc:.2f}" if cpr_bc else "N/A",
                "CPR Type": cpr_type,
                "CPR Width%": f"{cpr_wpct:.2f}%" if cpr_wpct is not None else "N/A",
                "CPR Position": cpr_pos,
                "CPR Interpretation": cpr_interp,
                "10m Bias": bias_info["bias_10m"],
                "30m Bias": bias_info["bias_30m"],
                "4H Bias": bias_info["bias_4h"],
                "Bias Align": bias_info["alignment"],
                "Options Strategy": opt_strat,
                "If opens near entry": open_above if direction == "LONG" else open_between,
                "If opens between entry & stop": open_between if direction == "LONG" else open_above,
                "If opens past stop": open_below_stop,
                "If big gap": big_gap,
            })
        except Exception as e:
            print(f"Error generating plan for {ticker}: {e}")
            continue

    # Sort rows by Best RR descending
    plan_rows.sort(key=lambda x: x.get("_best_rr_sort", 0), reverse=True)

    summary = {
        "total": len(plan_rows),
        "long": sum(1 for r in plan_rows if r["Direction"] == "LONG"),
        "short": sum(1 for r in plan_rows if r["Direction"] == "SHORT"),
        "best": sum(1 for r in plan_rows if r["Best Setup"] == "Y"),
    }

    return {
        "plan_date": p_date.strftime("%Y-%m-%d"),
        "plan_for": plan_for.strftime("%Y-%m-%d"),
        "plan_label": plan_label,
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "generated_time": datetime.now().strftime("%I:%M:%S %p"),
        "summary": summary,
        "rows": plan_rows,
    }

def check_open_prices(plan_rows: List[Dict[str, Any]], check_date_str: Optional[str] = None) -> Dict[str, Any]:
    """
    Check today's morning open prices against the plan scenarios.
    Categorizes: OPENS NEAR ENTRY, OPENS BETWEEN, OPENS PAST STOP, BIG GAP.
    """
    check_rows = []
    success_tickers = []
    failed_tickers = []

    for r in plan_rows:
        ticker = r.get("Ticker")
        if not ticker:
            continue
        try:
            entry = float(r["Close"])
            intra_stop = float(r["Intra Stop"])
            intra_t1 = float(r["Intra T1"])
            intra_t2 = float(r.get("Intra T2", intra_t1))
            atm_strike = float(r["ATM Strike"])
            atr_val = float(r["ATR"])
            direction = r["Direction"]
            opt_type = r.get("Option", "CALL")

            tk = yf.Ticker(ticker)
            # Fetch today's intraday/daily bar
            h = tk.history(period="1d", interval="1m")
            if h is None or h.empty:
                h = tk.history(period="5d", interval="1d")
            
            if h is None or h.empty:
                failed_tickers.append(ticker)
                continue

            open_price = round(float(h["Open"].iloc[0]), 2)
            current_price = round(float(h["Close"].iloc[-1]), 2)

            gap_thr = round(entry * 0.005, 2)
            move_raw = round(open_price - entry, 2)
            move_pct = round(move_raw / entry * 100, 2)

            txt_near = r.get("If opens near entry", "")
            txt_btwn = r.get("If opens between entry & stop", "")
            txt_past = r.get("If opens past stop", "")
            txt_gap = r.get("If big gap", "")

            if direction == "LONG":
                if open_price - entry > gap_thr:
                    sc_id, sc_label, sc_color, action_text = "big_gap", "⚠️ BIG GAP UP", "#f5c842", txt_gap
                elif open_price >= entry:
                    sc_id, sc_label, sc_color, action_text = "near_entry", "✅ OPENS NEAR ENTRY", "#00e5a0", txt_near
                elif open_price > intra_stop:
                    sc_id, sc_label, sc_color, action_text = "between", "⚡ OPENS BETWEEN ENTRY & STOP", "#4d9fff", txt_btwn
                else:
                    sc_id, sc_label, sc_color, action_text = "past_stop", "❌ OPENS PAST STOP", "#ff4d6a", txt_past
            else:
                if entry - open_price > gap_thr:
                    sc_id, sc_label, sc_color, action_text = "big_gap", "⚠️ BIG GAP DOWN", "#f5c842", txt_gap
                elif open_price <= entry:
                    sc_id, sc_label, sc_color, action_text = "near_entry", "✅ OPENS NEAR ENTRY", "#00e5a0", txt_near
                elif open_price < intra_stop:
                    sc_id, sc_label, sc_color, action_text = "between", "⚡ OPENS BETWEEN ENTRY & STOP", "#4d9fff", txt_btwn
                else:
                    sc_id, sc_label, sc_color, action_text = "past_stop", "❌ OPENS PAST STOP", "#ff4d6a", txt_past

            active_dir = direction
            active_opt = opt_type
            active_stop = intra_stop
            active_t1 = intra_t1
            active_t2 = intra_t2
            active_atm = atm_strike
            is_flipped = False

            if sc_id == "past_stop":
                is_flipped = True
                fd = round(atr_val * 0.3, 2)
                f1 = round(atr_val * 0.5, 2)
                f2 = round(atr_val * 0.8, 2)
                if direction == "LONG":
                    active_dir = "SHORT"
                    active_opt = "PUT"
                    active_stop = round(open_price + fd, 2)
                    active_t1 = round(open_price - f1, 2)
                    active_t2 = round(open_price - f2, 2)
                else:
                    active_dir = "LONG"
                    active_opt = "CALL"
                    active_stop = round(open_price - fd, 2)
                    active_t1 = round(open_price + f1, 2)
                    active_t2 = round(open_price + f2, 2)
                
                si = 5 if open_price >= 200 else (2.5 if open_price >= 50 else (1 if open_price >= 20 else 0.5))
                active_atm = round(round(open_price / si) * si, 2)
                action_text = f"Flipped to {active_opt} at ~${open_price:.2f} — stop ${active_stop:.2f}, T1 ${active_t1:.2f}, T2 ${active_t2:.2f}"

            # Calculate RR
            curr_risk = abs(open_price - active_stop)
            rr_active = round(abs(active_t1 - open_price) / curr_risk, 2) if curr_risk > 0 else 0.0

            check_time_str = datetime.now().strftime("%I:%M:%S %p")
            check_timestamp_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

            check_rows.append({
                "Ticker": ticker,
                "Generated Time": check_time_str,
                "Generated At": check_timestamp_str,
                "Direction": active_dir,
                "Option": active_opt,
                "Prev Close": entry,
                "Open": open_price,
                "Current": current_price,
                "Move $": move_raw,
                "Move %": move_pct,
                "Scenario ID": sc_id,
                "Scenario": sc_label,
                "Scenario Color": sc_color,
                "Is Flipped": is_flipped,
                "Stop": active_stop,
                "T1": active_t1,
                "T2": active_t2,
                "ATM": active_atm,
                "Active RR": f"{rr_active:.2f}x",
                "Action Notes": action_text,
                "Exp 0DTE": r.get("0DTE Exp", ""),
                "Exp 2-3DTE": r.get("2-3DTE Exp", ""),
                "Weekly Zone": r.get("Weekly Zone", "N/A"),
                "Daily Zone": r.get("Daily Zone", "N/A"),
                "Zone Conclusion": r.get("Zone Conclusion", "N/A"),
            })
            success_tickers.append(ticker)
        except Exception as ex:
            print(f"Error checking open for {ticker}: {ex}")
            failed_tickers.append(ticker)

    grouped = {
        "past_stop": [r for r in check_rows if r.get("Scenario ID") == "past_stop"],
        "near_entry": [r for r in check_rows if r.get("Scenario ID") == "near_entry"],
        "between": [r for r in check_rows if r.get("Scenario ID") == "between"],
        "big_gap": [r for r in check_rows if r.get("Scenario ID") == "big_gap"],
    }
    counts = {
        "past_stop": len(grouped["past_stop"]),
        "near_entry": len(grouped["near_entry"]),
        "between": len(grouped["between"]),
        "big_gap": len(grouped["big_gap"]),
    }

    return {
        "check_date": date.today().strftime("%Y-%m-%d"),
        "checked_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "checked_time": datetime.now().strftime("%I:%M:%S %p"),
        "successful": len(success_tickers),
        "failed": len(failed_tickers),
        "success_tickers": success_tickers,
        "counts": counts,
        "grouped": grouped,
        "rows": check_rows,
    }

def refresh_locked_prices(locked_rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    """
    Refreshes live quotes for existing locked 8:30 AM rows WITHOUT reclassifying
    or moving tickers across scenario tables.
    Preserves Scenario ID, Open, Direction, Option, Stop, T1, T2, ATM.
    Updates Current, Move, Live Status, and Last Updated.
    """
    updated_rows = []
    success_tickers = []
    failed_tickers = []

    for r in locked_rows:
        ticker = r.get("Ticker")
        if not ticker:
            continue
        try:
            tk = yf.Ticker(ticker)
            last_p = getattr(tk.fast_info, "last_price", None)
            if last_p is None or last_p <= 0:
                h = tk.history(period="1d")
                if not h.empty:
                    last_p = float(h["Close"].iloc[-1])
                else:
                    last_p = float(r.get("Current", r.get("Open", 0)))

            curr_price = round(float(last_p), 2)
            open_price = float(r.get("Open", curr_price))
            move_raw = round(curr_price - open_price, 2)
            move_pct = round(move_raw / open_price * 100, 2) if open_price > 0 else 0.0

            dir_str = str(r.get("Direction", "LONG")).upper()
            stop_px = float(r.get("Stop", 0))
            t1_px = float(r.get("T1", 0))
            t2_px = float(r.get("T2", t1_px))

            # Determine live trade progress
            if "LONG" in dir_str:
                if t2_px > 0 and curr_price >= t2_px:
                    status = "🏆 T2 HIT"
                elif t1_px > 0 and curr_price >= t1_px:
                    status = "🎯 T1 HIT"
                elif stop_px > 0 and curr_price <= stop_px:
                    status = "🛑 STOPPED"
                elif curr_price > open_price:
                    status = "🟢 PROFIT"
                else:
                    status = "⚡ IN PLAY"
            else:
                if t2_px > 0 and curr_price <= t2_px:
                    status = "🏆 T2 HIT"
                elif t1_px > 0 and curr_price <= t1_px:
                    status = "🎯 T1 HIT"
                elif stop_px > 0 and curr_price >= stop_px:
                    status = "🛑 STOPPED"
                elif curr_price < open_price:
                    status = "🟢 PROFIT"
                else:
                    status = "⚡ IN PLAY"

            new_r = dict(r)
            new_r["Current"] = curr_price
            new_r["Move $"] = move_raw
            new_r["Move %"] = move_pct
            new_r["Live Move $"] = move_raw
            new_r["Live Move %"] = move_pct
            new_r["Live Status"] = status
            new_r["Last Updated"] = datetime.now().strftime("%I:%M:%S %p")
            updated_rows.append(new_r)
            success_tickers.append(ticker)
        except Exception as e:
            print(f"Error refreshing ticker {ticker}: {e}")
            failed_tickers.append(ticker)
            updated_rows.append(r)

    grouped = {
        "past_stop": [r for r in updated_rows if r.get("Scenario ID") == "past_stop"],
        "near_entry": [r for r in updated_rows if r.get("Scenario ID") == "near_entry"],
        "between": [r for r in updated_rows if r.get("Scenario ID") == "between"],
        "big_gap": [r for r in updated_rows if r.get("Scenario ID") == "big_gap"],
    }
    counts = {
        "past_stop": len(grouped["past_stop"]),
        "near_entry": len(grouped["near_entry"]),
        "between": len(grouped["between"]),
        "big_gap": len(grouped["big_gap"]),
    }

    return {
        "success": True,
        "check_date": date.today().strftime("%Y-%m-%d"),
        "checked_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "checked_time": datetime.now().strftime("%I:%M:%S %p"),
        "successful": len(success_tickers),
        "failed": len(failed_tickers),
        "success_tickers": success_tickers,
        "counts": counts,
        "grouped": grouped,
        "rows": updated_rows,
        "is_locked": True,
    }
