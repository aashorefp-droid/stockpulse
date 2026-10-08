"""
Earnings Prediction Service based on 52-Week, Weekly, and Last Earnings Fibonacci Confluence.

Features:
- Multi-Timeframe Fibonacci Confluence Synthesis (52W Macro, Weekly Swing, Last Earnings Reaction).
- Evaluates Next Scheduled Earnings date and activates ONLY if earnings is tomorrow (or next trading session);
  otherwise strictly outputs 'N/A' as requested.
- Enriches response with Market Cap (formatted & tier) and comprehensive helpful earnings data
  (beat/miss stats, consensus estimates, P/E ratios, expected moves).
"""

from datetime import date, datetime, timedelta
from typing import Any, Dict, List, Optional
import math
import pandas as pd
import yfinance as yf


def format_market_cap(mc_val: Optional[float]) -> tuple[str, Optional[float], str]:
    """Formats numeric market cap into human-readable string and market cap tier."""
    if not mc_val or mc_val <= 0 or math.isnan(mc_val):
        return "N/A", None, "N/A"
    
    if mc_val >= 1e12:
        formatted = f"${mc_val / 1e12:.2f}T"
        category = "Mega Cap (>$200B)"
    elif mc_val >= 2e11:
        formatted = f"${mc_val / 1e9:.2f}B"
        category = "Mega Cap (>$200B)"
    elif mc_val >= 1e10:
        formatted = f"${mc_val / 1e9:.2f}B"
        category = "Large Cap ($10B-$200B)"
    elif mc_val >= 2e9:
        formatted = f"${mc_val / 1e9:.2f}B"
        category = "Mid Cap ($2B-$10B)"
    elif mc_val >= 3e8:
        formatted = f"${mc_val / 1e6:.2f}M"
        category = "Small Cap ($300M-$2B)"
    else:
        formatted = f"${mc_val / 1e6:.2f}M"
        category = "Micro Cap (<$300M)"
    
    return formatted, float(mc_val), category


def get_next_earnings_details(
    ticker: str,
    as_of: Optional[str] = None,
    tk_obj: Optional[Any] = None,
) -> Dict[str, Any]:
    """
    Extracts next earnings date, session (BMO/AMC), days until report,
    and helpful earnings metrics (beat/miss history, estimates, expected move).
    """
    as_of_dt = None
    if as_of:
        try:
            as_of_dt = datetime.strptime(as_of.strip(), "%Y-%m-%d").date()
        except Exception:
            as_of_dt = None

    ref_date = as_of_dt or date.today()
    tk = tk_obj or yf.Ticker(ticker)

    info = {}
    try:
        info = tk.info or {}
    except Exception:
        info = {}

    mc_val = info.get("marketCap")
    mc_str, mc_raw, mc_cat = format_market_cap(mc_val)

    next_date = None
    next_time = "TBD"
    next_eps_est = None
    next_rev_est = None

    # Check calendar
    try:
        cal = tk.calendar
        if isinstance(cal, dict):
            ed_val = cal.get("Earnings Date")
            if ed_val is not None:
                vals = list(ed_val) if hasattr(ed_val, "__iter__") and not isinstance(ed_val, str) else [ed_val]
                for v in vals:
                    vd = v.date() if hasattr(v, "date") else v
                    if vd >= ref_date:
                        next_date = vd
                        break
            if "Earnings High" in cal or "Earnings Low" in cal or "Earnings Average" in cal:
                next_eps_est = cal.get("Earnings Average") or cal.get("Earnings Low")
            if "Revenue Average" in cal:
                rev_avg = cal.get("Revenue Average")
                if rev_avg and rev_avg > 0:
                    next_rev_est = f"${rev_avg / 1e9:.2f}B" if rev_avg >= 1e9 else f"${rev_avg / 1e6:.2f}M"
    except Exception:
        pass

    # Check earnings_dates table for timestamps (AMC vs BMO) and estimates
    df_ed = None
    try:
        df_ed = tk.earnings_dates
    except Exception:
        df_ed = None

    if df_ed is not None and not df_ed.empty:
        future_ed = [
            ts for ts in sorted(df_ed.index)
            if (ts.date() if hasattr(ts, "date") else ts) >= ref_date
        ]
        if future_ed:
            f_ts = future_ed[0]
            if next_date is None or (f_ts.date() if hasattr(f_ts, "date") else f_ts) <= next_date:
                next_date = f_ts.date() if hasattr(f_ts, "date") else f_ts
                hour = getattr(f_ts, "hour", 16)
                next_time = "AMC" if hour >= 12 else "BMO"
                row = df_ed.loc[f_ts]
                if pd.notna(row.get("EPS Estimate")) and next_eps_est is None:
                    next_eps_est = float(row.get("EPS Estimate"))

    # Compute days until report and if it is tomorrow / next session
    days_until = None
    is_tomorrow = False
    status_label = "N/A"

    if next_date:
        days_until = (next_date - ref_date).days
        # Tomorrow: delta == 1; if Friday (weekday 4), next market session is Monday (delta == 3)
        if days_until == 1 or (ref_date.weekday() == 4 and days_until == 3):
            is_tomorrow = True
            session_str = f" ({next_time})" if next_time != "TBD" else ""
            status_label = f"Earnings Tomorrow{session_str}"
        elif days_until == 0:
            # Earnings is today
            is_tomorrow = True
            status_label = f"Earnings Today ({next_time})"
        elif days_until > 1:
            status_label = f"Earnings in {days_until} days"
        else:
            status_label = "Earnings in past"

    # Historical beat/miss and last earnings details
    last_eps_act = None
    last_eps_est = None
    last_surprise_pct = None
    last_beat_status = "N/A"
    historical_beat_rate_pct = None

    if df_ed is not None and not df_ed.empty:
        past_rows = []
        for ts, row in df_ed.iterrows():
            ts_d = ts.date() if hasattr(ts, "date") else ts
            if ts_d < ref_date and pd.notna(row.get("Reported EPS")):
                past_rows.append((ts_d, row))
        
        past_rows = sorted(past_rows, key=lambda x: x[0], reverse=True)
        if past_rows:
            latest_d, latest_row = past_rows[0]
            last_eps_act = round(float(latest_row["Reported EPS"]), 2)
            if pd.notna(latest_row.get("EPS Estimate")):
                last_eps_est = round(float(latest_row["EPS Estimate"]), 2)
            if pd.notna(latest_row.get("Surprise(%)")):
                last_surprise_pct = round(float(latest_row["Surprise(%)"]), 2)
                if last_surprise_pct > 0.5:
                    last_beat_status = "BEAT"
                elif last_surprise_pct < -0.5:
                    last_beat_status = "MISS"
                else:
                    last_beat_status = "IN-LINE"

            # Compute beat rate over available quarters (up to 8)
            eval_quarters = past_rows[:8]
            beats = sum(
                1 for _, r in eval_quarters
                if pd.notna(r.get("Surprise(%)")) and float(r.get("Surprise(%)")) > 0
            )
            total = len([r for _, r in eval_quarters if pd.notna(r.get("Surprise(%)"))])
            if total > 0:
                historical_beat_rate_pct = round((beats / total) * 100, 1)

    # Implied Move from options ATM straddle or fallback
    expected_move_pct = None
    try:
        from backend.services.earnings import get_expected_move
        em_res = get_expected_move(ticker, str(next_date) if next_date else None)
        if em_res and "expected_move_pct" in em_res:
            expected_move_pct = round(float(em_res["expected_move_pct"]), 1)
    except Exception:
        pass

    pe_ratio = round(float(info["trailingPE"]), 2) if info.get("trailingPE") else None
    forward_pe = round(float(info["forwardPE"]), 2) if info.get("forwardPE") else None

    return {
        "market_cap": {
            "formatted": mc_str,
            "raw": mc_raw,
            "category": mc_cat,
        },
        "helpful_earnings_data": {
            "next_earnings_date": str(next_date) if next_date else None,
            "next_earnings_date_label": next_date.strftime("%b %d, %Y") if next_date else "Not Scheduled",
            "next_earnings_time": next_time,
            "days_until": days_until,
            "is_tomorrow": is_tomorrow,
            "status_label": status_label,
            "next_eps_estimate": round(float(next_eps_est), 2) if next_eps_est is not None else None,
            "next_revenue_estimate": next_rev_est,
            "pe_ratio": pe_ratio,
            "forward_pe": forward_pe,
            "last_earnings_eps_actual": last_eps_act,
            "last_earnings_eps_estimate": last_eps_est,
            "last_earnings_surprise_pct": last_surprise_pct,
            "last_earnings_beat_status": last_beat_status,
            "historical_beat_rate_pct": historical_beat_rate_pct,
            "expected_move_pct": expected_move_pct,
        }
    }


def calc_fib_earnings_prediction(
    ticker: str,
    current_price: float,
    fib_levels: Dict[str, float],
    weekly_fib_levels: Dict[str, float],
    earnings_fib: Optional[Dict[str, Any]],
    hi_52: Optional[float] = None,
    lo_52: Optional[float] = None,
    week_high: Optional[float] = None,
    week_low: Optional[float] = None,
    as_of: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Synthesizes 52-Week, Weekly, and Last Earnings Fibonacci levels to draw
    an Earnings Outcome Prediction.
    
    Rule:
    - If next earnings is tomorrow (or next market session), prediction is ACTIVE.
    - Otherwise, outputs 'N/A' (e.g., 'N/A - Earnings in 40 days (Nov 17, 2026)').
    - Includes Market Cap and Helpful Earnings Data.
    """
    # 1. Fetch helpful earnings & market cap data
    details = get_next_earnings_details(ticker, as_of=as_of)
    market_cap = details["market_cap"]
    helpful = details["helpful_earnings_data"]
    is_tomorrow = helpful["is_tomorrow"]
    days_until = helpful["days_until"]

    # 2. Evaluate 52-Week Fibonacci Macro Position
    score_52w = 0
    stance_52w = "NEUTRAL"
    desc_52w = "Consolidating in 52W range"

    if hi_52 and lo_52 and hi_52 > lo_52 and current_price > 0:
        if current_price >= hi_52:
            score_52w = 35
            stance_52w = "MACRO EXPANSION"
            pct_over = ((current_price - hi_52) / hi_52) * 100
            desc_52w = f"Trading in Blue-Sky Expansion above 52W High (+{pct_over:.1f}%)"
        else:
            pct_in_range = (current_price - lo_52) / (hi_52 - lo_52)
            if pct_in_range >= 0.75:
                score_52w = 25
                stance_52w = "UPPER RANGE RETRACE"
                desc_52w = f"Strong Uptrend: Holding upper quartile of 52W range ({pct_in_range * 100:.0f}%)"
            elif pct_in_range >= 0.40:
                score_52w = 5
                stance_52w = "MIDPOINT EQUILIBRIUM"
                desc_52w = f"Equilibrium: Consolidating near 52W midpoint ({pct_in_range * 100:.0f}%)"
            elif pct_in_range >= 0.15:
                score_52w = -20
                stance_52w = "LOWER RANGE RETRACE"
                desc_52w = f"Weak Structure: Lagging in lower quartile below Golden Ratio ({pct_in_range * 100:.0f}%)"
            else:
                score_52w = -35
                stance_52w = "MACRO BREAKDOWN"
                desc_52w = f"Vulnerable: Hovering near 52W lows ({pct_in_range * 100:.0f}%)"

    # 3. Evaluate Weekly Fibonacci Swing Momentum
    score_wk = 0
    stance_wk = "NEUTRAL"
    desc_wk = "Consolidating in weekly range"

    if week_high and week_low and week_high > week_low and current_price > 0:
        if current_price >= week_high:
            score_wk = 30
            stance_wk = "BULLISH SWING EXPANSION"
            pct_wk = ((current_price - week_high) / week_high) * 100
            desc_wk = f"Surging into Print: Trading above Previous Week High (+{pct_wk:.1f}%)"
        elif current_price <= week_low:
            score_wk = -30
            stance_wk = "BEARISH SWING BREAKDOWN"
            pct_wk = ((current_price - week_low) / week_low) * 100
            desc_wk = f"Selling Pressure: Trading below Previous Week Low ({pct_wk:.1f}%)"
        else:
            pct_wk_range = (current_price - week_low) / (week_high - week_low)
            if pct_wk_range >= 0.60:
                score_wk = 20
                stance_wk = "BULLISH SWING HOLD"
                desc_wk = f"Constructive: Holding above weekly midpoint ({pct_wk_range * 100:.0f}% of range)"
            elif pct_wk_range >= 0.40:
                score_wk = 0
                stance_wk = "WEEKLY EQUILIBRIUM"
                desc_wk = f"Range-Bound: Balanced at weekly midpoint ({pct_wk_range * 100:.0f}% of range)"
            else:
                score_wk = -20
                stance_wk = "WEAK SWING PULLBACK"
                desc_wk = f"Losing Ground: Trading below weekly 50% midpoint ({pct_wk_range * 100:.0f}% of range)"

    # 4. Evaluate Last Earnings Reaction Follow-Through ("Where We Are Now")
    score_earn = 0
    stance_earn = "NEUTRAL"
    desc_earn = "No prior earnings reaction data"

    if earnings_fib and earnings_fib.get("has_earnings"):
        e_status = earnings_fib.get("status")
        e_hi = earnings_fib.get("earnings_high")
        e_lo = earnings_fib.get("earnings_low")
        dist_hi_pct = earnings_fib.get("dist_from_high_pct") or 0.0
        dist_lo_pct = earnings_fib.get("dist_from_low_pct") or 0.0

        if e_status == "EXPANSION":
            score_earn = 35
            stance_earn = "BULLISH EXPANSION"
            desc_earn = f"Strong Institutional Follow-Through: Trading above Last Earnings High (+{dist_hi_pct:.1f}%)"
        elif e_status == "BREAKDOWN":
            score_earn = -35
            stance_earn = "BEARISH BREAKDOWN"
            desc_earn = f"Institutional Distribution: Trading below Last Earnings Low ({dist_lo_pct:.1f}%)"
        else: # CONSOLIDATING
            mid = earnings_fib.get("earnings_midpoint") or 0
            if current_price >= mid:
                score_earn = 15
                stance_earn = "UPPER RANGE CONSOLIDATION"
                desc_earn = f"Holding Support: Consolidating in upper half of prior earnings reaction"
            else:
                score_earn = -15
                stance_earn = "LOWER RANGE CONSOLIDATION"
                desc_earn = f"Under Pressure: Consolidating below prior earnings reaction midpoint"

    # 5. Total Confluence Calculation & Prediction Synthesis
    total_confluence = max(-100, min(100, score_52w + score_wk + score_earn))

    if total_confluence >= 50:
        predicted_outcome = "BULLISH EXPANSION RUN"
        outcome_bias = "BULLISH"
        outcome_color = "emerald"
        rationale = (
            "Multi-timeframe confluence shows strong institutional breakout: price has held above "
            "prior earnings highs and is breaking 52W/weekly resistance. High probability of "
            "post-earnings upside continuation towards upper expansion targets."
        )
    elif total_confluence >= 15:
        predicted_outcome = "MODERATE UPSIDE BIAS"
        outcome_bias = "LEAN BULLISH"
        outcome_color = "teal"
        rationale = (
            "Constructive price structure holding above key Fibonacci support levels. "
            "Favors upside testing, provided earnings clears immediate overhead Fib resistance."
        )
    elif total_confluence >= -14:
        predicted_outcome = "RANGE-BOUND / CHOP RISK"
        outcome_bias = "NEUTRAL"
        outcome_color = "amber"
        rationale = (
            "Price is hovering at Fibonacci equilibrium midpoints across timeframes. "
            "High probability of post-earnings volatility crush and range-bound reaction between key Fib bounds."
        )
    elif total_confluence >= -49:
        predicted_outcome = "MODERATE DOWNSIDE BIAS"
        outcome_bias = "LEAN BEARISH"
        outcome_color = "orange"
        rationale = (
            "Price is suppressed in lower Fibonacci halves and struggling below resistance into the release, "
            "creating elevated pullback risk if earnings does not decisively beat."
        )
    else:
        predicted_outcome = "BEARISH BREAKDOWN RISK"
        outcome_bias = "BEARISH"
        outcome_color = "rose"
        rationale = (
            "Severe multi-timeframe breakdown: Stock has lost prior earnings lows and weekly support into the print. "
            "High vulnerability to steep post-earnings rejection towards downside targets."
        )

    # 6. Upside & Downside Target Identification
    # Check across all 3 Fib levels for nearest resistance above and nearest support below
    all_levels = []
    if earnings_fib and earnings_fib.get("fib_levels"):
        for lbl, v in earnings_fib["fib_levels"].items():
            all_levels.append((v, f"Earnings {lbl} (${v:.2f})"))
    if weekly_fib_levels:
        for lbl, v in weekly_fib_levels.items():
            all_levels.append((v, f"Week {lbl} (${v:.2f})"))
    if fib_levels:
        for lbl, v in fib_levels.items():
            all_levels.append((v, f"52W {lbl} (${v:.2f})"))

    resistances = sorted([lvl for lvl in all_levels if lvl[0] > current_price * 1.002], key=lambda x: x[0])
    supports = sorted([lvl for lvl in all_levels if lvl[0] < current_price * 0.998], key=lambda x: x[0], reverse=True)

    upside_target_val, upside_target_label = (resistances[0] if resistances else (current_price * 1.05, "Expansion Target (+5.0%)"))
    downside_floor_val, downside_floor_label = (supports[0] if supports else (current_price * 0.95, "Support Floor (-5.0%)"))

    upside_target_pct = round(((upside_target_val - current_price) / current_price) * 100, 1) if current_price > 0 else 0.0
    downside_floor_pct = round(((downside_floor_val - current_price) / current_price) * 100, 1) if current_price > 0 else 0.0

    fib_confluence = {
        "fib_52w": {
            "stance": stance_52w,
            "score": score_52w,
            "description": desc_52w,
        },
        "fib_week": {
            "stance": stance_wk,
            "score": score_wk,
            "description": desc_wk,
        },
        "fib_earnings": {
            "stance": stance_earn,
            "score": score_earn,
            "description": desc_earn,
        },
        "total_confluence_score": total_confluence,
        "predicted_outcome": predicted_outcome,
        "outcome_bias": outcome_bias,
        "outcome_color": outcome_color,
        "rationale": rationale,
        "upside_target": round(upside_target_val, 2),
        "upside_target_label": upside_target_label,
        "upside_target_pct": upside_target_pct,
        "downside_floor": round(downside_floor_val, 2),
        "downside_floor_label": downside_floor_label,
        "downside_floor_pct": downside_floor_pct,
    }

    # 7. Formulate Final User-Facing Prediction:
    # Rule: Check next earnings; if earnings is tomorrow -> show prediction, else put earnings "N/A"
    next_date_label = helpful.get("next_earnings_date_label") or "Not Scheduled"
    session_str = f" ({helpful['next_earnings_time']})" if helpful.get("next_earnings_time") != "TBD" else ""

    if is_tomorrow:
        prediction = predicted_outcome
        prediction_status = "ACTIVE"
        prediction_badge = f"PREDICTION: {predicted_outcome}"
        prediction_color = outcome_color
        prediction_note = (
            f"Active Earnings Outcome Prediction · Reporting {helpful['status_label']} · "
            f"Fib Confluence: {total_confluence:+d}/100 ({outcome_bias})"
        )
    else:
        prediction = "N/A"
        prediction_status = "NA_NOT_TOMORROW"
        prediction_badge = "EARNINGS: N/A (NOT TOMORROW)"
        prediction_color = "slate"
        if days_until is not None and days_until > 0:
            prediction_note = (
                f"Next earnings is on {next_date_label}{session_str} ({days_until} days away). "
                f"Earnings outcome prediction automatically unlocks when earnings is tomorrow."
            )
        else:
            prediction_note = (
                f"No imminent earnings scheduled tomorrow ({next_date_label}). "
                f"Earnings outcome prediction generates when earnings is scheduled tomorrow."
            )

    return {
        "ticker": ticker,
        "prediction": prediction,
        "prediction_status": prediction_status,
        "prediction_badge": prediction_badge,
        "prediction_color": prediction_color,
        "prediction_note": prediction_note,
        "is_tomorrow": is_tomorrow,
        "days_until_earnings": days_until,
        "market_cap": market_cap,
        "helpful_earnings_data": helpful,
        "fib_confluence": fib_confluence,
    }
