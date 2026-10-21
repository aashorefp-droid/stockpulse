"""
Day-trading V4: PDH/PDL/PWH/PWL level strategy.

This version is intentionally self-contained and scanner-friendly:
- scanner calls analyze_from_daily() with bars it already has
- standalone CLI can still fetch via yfinance for quick manual checks
- no trades are generated from a bare touch; output is a next-session plan
  plus the confirmation trigger to watch

Disable scanner integration with:
    DAY_TRADING_V4_ENABLED=0
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date, datetime
from typing import Literal, Optional

import pandas as pd

Side = Literal["long", "short", "none"]


@dataclass
class Levels:
    pdh: float
    pdl: float
    pwh: float
    pwl: float
    pd_close: float
    pd_mid: float
    atr: float


@dataclass
class V4Signal:
    setup: str
    context: str
    side: Side
    bias: str
    grade: str
    level: Optional[str]
    level_val: Optional[float]
    entry: Optional[float]
    stop: Optional[float]
    t1: Optional[float]
    t2: Optional[float]
    rr: Optional[float]
    trigger: str
    invalidation: str
    target_plan: str
    exit_plan: str
    note: str


def _round2(value: Optional[float]) -> Optional[float]:
    if value is None:
        return None
    try:
        return round(float(value), 2)
    except Exception:
        return None


def _norm_daily(daily_df: pd.DataFrame) -> pd.DataFrame:
    if daily_df is None or daily_df.empty:
        return pd.DataFrame()
    df = daily_df.copy()
    df.columns = [str(c).lower() for c in df.columns]
    needed = ["open", "high", "low", "close"]
    if any(c not in df.columns for c in needed):
        return pd.DataFrame()
    for col in needed + (["volume"] if "volume" in df.columns else []):
        df[col] = pd.to_numeric(df[col], errors="coerce")
    df = df.dropna(subset=needed)
    return df


def _idx_date(idx_value) -> date:
    ts = pd.Timestamp(idx_value)
    if ts.tzinfo is not None:
        ts = ts.tz_convert("America/New_York")
    return ts.date()


def _split_session(daily: pd.DataFrame, scan_date: date) -> tuple[pd.DataFrame, Optional[pd.Series]]:
    dates = [_idx_date(i) for i in daily.index]
    completed = daily[[d < scan_date for d in dates]]
    session_rows = daily[[d == scan_date for d in dates]]
    session = session_rows.iloc[-1] if not session_rows.empty else None
    if completed.empty and len(daily) > 1:
        completed = daily.iloc[:-1]
        session = daily.iloc[-1]
    return completed, session


def _atr(daily: pd.DataFrame, lookback: int = 14) -> float:
    if len(daily) < 2:
        return 0.0
    high = daily["high"].astype(float)
    low = daily["low"].astype(float)
    prev_close = daily["close"].astype(float).shift(1)
    tr = pd.concat([
        high - low,
        (high - prev_close).abs(),
        (low - prev_close).abs(),
    ], axis=1).max(axis=1)
    vals = tr.dropna().tail(lookback)
    return float(vals.mean()) if not vals.empty else 0.0


def _levels(completed_daily: pd.DataFrame) -> Levels:
    if len(completed_daily) < 5:
        raise ValueError("need at least 5 completed daily bars")
    prev = completed_daily.iloc[-1]
    week = completed_daily.tail(5)
    pdh = float(prev["high"])
    pdl = float(prev["low"])
    return Levels(
        pdh=pdh,
        pdl=pdl,
        pwh=float(week["high"].max()),
        pwl=float(week["low"].min()),
        pd_close=float(prev["close"]),
        pd_mid=(pdh + pdl) / 2,
        atr=_atr(completed_daily),
    )


def _buffer(levels: Levels, price: float) -> float:
    atr_part = levels.atr * 0.08 if levels.atr > 0 else 0.0
    px_part = price * 0.0015 if price > 0 else 0.0
    return max(atr_part, px_part, 0.01)


def _rr(entry: Optional[float], stop: Optional[float], target: Optional[float]) -> Optional[float]:
    if entry is None or stop is None or target is None:
        return None
    risk = abs(entry - stop)
    if risk <= 0:
        return None
    return round(abs(target - entry) / risk, 2)


def _stack_note(label: str, value: float, levels: Levels, price: float) -> tuple[bool, str]:
    tolerance = max(price * 0.0025, levels.atr * 0.06, 0.01)
    pairs = {
        "PDH": levels.pdh,
        "PDL": levels.pdl,
        "PWH": levels.pwh,
        "PWL": levels.pwl,
    }
    near = [name for name, val in pairs.items() if name != label and abs(val - value) <= tolerance]
    if not near:
        return False, ""
    return True, f"Confluence: {label} stacked with {', '.join(near)}."


def _grade(base: str, label: str, stacked: bool) -> str:
    if stacked and label in {"PWH", "PWL"}:
        return "A+"
    if label in {"PWH", "PWL"} or stacked:
        return "A"
    return base


def _long_targets(entry: float, levels: Levels) -> tuple[float, float]:
    overhead = sorted(v for v in [levels.pdh, levels.pwh] if v > entry)
    t1 = overhead[0] if overhead else entry + max(levels.atr * 0.5, entry * 0.006)
    t2 = overhead[1] if len(overhead) > 1 else entry + max(levels.atr, entry * 0.012)
    return t1, t2


def _short_targets(entry: float, levels: Levels) -> tuple[float, float]:
    lower = sorted((v for v in [levels.pdl, levels.pwl] if v < entry), reverse=True)
    t1 = lower[0] if lower else entry - max(levels.atr * 0.5, entry * 0.006)
    t2 = lower[1] if len(lower) > 1 else entry - max(levels.atr, entry * 0.012)
    return t1, t2


def _sweep_short(label: str, level: float, session_high: float, price: float, levels: Levels) -> V4Signal:
    buf = _buffer(levels, price)
    entry = level
    stop = max(session_high, level + buf) + buf
    t1, t2 = _short_targets(entry, levels)
    stacked, note = _stack_note(label, level, levels, price)
    return V4Signal(
        setup="sweep_reject_short",
        context="next_session",
        side="short",
        bias="failed breakout",
        grade=_grade("B+", label, stacked),
        level=label,
        level_val=_round2(level),
        entry=_round2(entry),
        stop=_round2(stop),
        t1=_round2(t1),
        t2=_round2(t2),
        rr=_rr(entry, stop, t1),
        trigger=f"Watch: {label} rejection",
        invalidation=f"Stop: above sweep high {session_high:.2f}.",
        target_plan=f"T1 {t1:.2f}, then trail toward {t2:.2f}.",
        exit_plan="Take partial at T1; trail over lower highs/VWAP; flat if price reclaims level.",
        note=f"Liquidity sweep above {label}; avoid chasing before rejection confirms. {note}".strip(),
    )


def _sweep_long(label: str, level: float, session_low: float, price: float, levels: Levels) -> V4Signal:
    buf = _buffer(levels, price)
    entry = level
    stop = min(session_low, level - buf) - buf
    t1, t2 = _long_targets(entry, levels)
    stacked, note = _stack_note(label, level, levels, price)
    return V4Signal(
        setup="sweep_reclaim_long",
        context="next_session",
        side="long",
        bias="failed breakdown",
        grade=_grade("B+", label, stacked),
        level=label,
        level_val=_round2(level),
        entry=_round2(entry),
        stop=_round2(stop),
        t1=_round2(t1),
        t2=_round2(t2),
        rr=_rr(entry, stop, t1),
        trigger=f"Watch: {label} reclaim",
        invalidation=f"Stop: below sweep low {session_low:.2f}.",
        target_plan=f"T1 {t1:.2f}, then trail toward {t2:.2f}.",
        exit_plan="Take partial at T1; trail under higher lows/VWAP; flat if price loses level.",
        note=f"Liquidity sweep below {label}; wait for reclaim instead of catching the flush. {note}".strip(),
    )


def _breakout_long(label: str, level: float, price: float, levels: Levels) -> V4Signal:
    buf = _buffer(levels, price)
    entry = level
    stop = level - buf
    t1, t2 = _long_targets(entry, levels)
    stacked, note = _stack_note(label, level, levels, price)
    return V4Signal(
        setup="breakout_retest_long",
        context="next_session",
        side="long",
        bias="acceptance above resistance",
        grade=_grade("B", label, stacked),
        level=label,
        level_val=_round2(level),
        entry=_round2(entry),
        stop=_round2(stop),
        t1=_round2(t1),
        t2=_round2(t2),
        rr=_rr(entry, stop, t1),
        trigger=f"Watch: {label} support",
        invalidation=f"Stop: below {label} / VWAP loss.",
        target_plan=f"T1 {t1:.2f}; T2 {t2:.2f}; skip if entry is extended >0.5 ATR.",
        exit_plan="Scale at T1; trail under retest low/higher lows; do not average below trigger.",
        note=f"Acceptance above {label}; continuation needs volume and retest hold. {note}".strip(),
    )


def _breakdown_short(label: str, level: float, price: float, levels: Levels) -> V4Signal:
    buf = _buffer(levels, price)
    entry = level
    stop = level + buf
    t1, t2 = _short_targets(entry, levels)
    stacked, note = _stack_note(label, level, levels, price)
    return V4Signal(
        setup="breakdown_retest_short",
        context="next_session",
        side="short",
        bias="acceptance below support",
        grade=_grade("B", label, stacked),
        level=label,
        level_val=_round2(level),
        entry=_round2(entry),
        stop=_round2(stop),
        t1=_round2(t1),
        t2=_round2(t2),
        rr=_rr(entry, stop, t1),
        trigger=f"Watch: {label} resistance",
        invalidation=f"Stop: above {label} / VWAP reclaim.",
        target_plan=f"T1 {t1:.2f}; T2 {t2:.2f}; skip if entry is extended >0.5 ATR.",
        exit_plan="Scale at T1; trail over retest high/lower highs; do not average above trigger.",
        note=f"Acceptance below {label}; continuation needs volume and failed retest. {note}".strip(),
    )


def _range_wait(price: float, levels: Levels) -> V4Signal:
    nearest_res = min([("PDH", levels.pdh), ("PWH", levels.pwh)], key=lambda x: abs(x[1] - price))
    nearest_sup = min([("PDL", levels.pdl), ("PWL", levels.pwl)], key=lambda x: abs(x[1] - price))
    return V4Signal(
        setup="range_wait",
        context="next_session",
        side="none",
        bias=(
            f"inside prior range "
            f"(PDL {levels.pdl:.2f}-PDH {levels.pdh:.2f}; "
            f"PWL {levels.pwl:.2f}-PWH {levels.pwh:.2f})"
        ),
        grade="WAIT",
        level=None,
        level_val=None,
        entry=None,
        stop=None,
        t1=None,
        t2=None,
        rr=None,
        trigger=(
            f"Long: sweep/reclaim {nearest_sup[0]} {nearest_sup[1]:.2f}; "
            f"Short: sweep/reject {nearest_res[0]} {nearest_res[1]:.2f}."
        ),
        invalidation="No trade on first touch; wait for close/retest confirmation.",
        target_plan="After trigger, target VWAP/mid first, then opposite range edge.",
        exit_plan="Flat quickly if reclaim/rejection fails; day trade only.",
        note="PDH/PDL range mode: let stops run first, then trade the reaction.",
    )


def _pick_signal(price: float, session_high: float, session_low: float, levels: Levels) -> V4Signal:
    resistance = [("PWH", levels.pwh), ("PDH", levels.pdh)]
    support = [("PWL", levels.pwl), ("PDL", levels.pdl)]

    for label, level in sorted(resistance, key=lambda x: x[1], reverse=True):
        if session_high > level and price < level:
            return _sweep_short(label, level, session_high, price, levels)

    for label, level in sorted(support, key=lambda x: x[1]):
        if session_low < level and price > level:
            return _sweep_long(label, level, session_low, price, levels)

    for label, level in sorted(resistance, key=lambda x: x[1], reverse=True):
        if price > level:
            return _breakout_long(label, level, price, levels)

    for label, level in sorted(support, key=lambda x: x[1]):
        if price < level:
            return _breakdown_short(label, level, price, levels)

    return _range_wait(price, levels)


def compute_outcomes(price: float, levels: Levels) -> dict:
    """Calculate concrete possible trade outcomes and price levels based on current price."""
    buf = _buffer(levels, price)
    pd_mid = levels.pd_mid
    atr = max(levels.atr, 1.0)

    dist_pdh = levels.pdh - price
    dist_pdl = price - levels.pdl
    dist_pwh = levels.pwh - price
    dist_pwl = price - levels.pwl

    range_span = max(levels.pdh - levels.pdl, 0.01)
    pos_pct = max(0.0, min(100.0, (price - levels.pdl) / range_span * 100.0))

    if price > levels.pdh:
        position_desc = f"Above PDH (+${price - levels.pdh:.2f}, +{(price - levels.pdh)/levels.pdh*100:.2f}%) — Breakout zone"
        primary_focus = f"Price is above PDH (${levels.pdh:.2f}). Watch PDH retest hold for long continuation to PWH (${levels.pwh:.2f}), or failure back below PDH for a false-breakout short."

        # Bullish 1: Retest Hold of PDH
        long_reclaim = {
            "scenario": "Retest & Hold PDH",
            "watch_level": "PDH",
            "watch_val": _round2(levels.pdh),
            "entry": _round2(levels.pdh),
            "stop": _round2(levels.pdh - buf),
            "t1": _round2(max(levels.pwh, levels.pdh + atr * 0.5)),
            "t2": _round2(levels.pdh + atr),
            "rr": _rr(levels.pdh, levels.pdh - buf, max(levels.pwh, levels.pdh + atr * 0.5)),
            "trigger": f"Pulls back to PDH (${levels.pdh:.2f}) and 5m candle confirms support hold",
            "invalidation": f"Stop loss on break back below PDH (est. ${levels.pdh - buf:.2f})",
        }
        # Bullish 2: Breakout through PWH
        t1_pwh = levels.pwh + atr * 0.5
        t2_pwh = levels.pwh + atr
        long_breakout = {
            "scenario": "Breakout & Hold PWH",
            "watch_level": "PWH",
            "watch_val": _round2(levels.pwh),
            "entry": _round2(levels.pwh),
            "stop": _round2(levels.pwh - buf),
            "t1": _round2(t1_pwh),
            "t2": _round2(t2_pwh),
            "rr": _rr(levels.pwh, levels.pwh - buf, t1_pwh),
            "trigger": f"5m candle closes above PWH (${levels.pwh:.2f}) and holds on retest",
            "invalidation": f"Stop loss on failed retest below PWH (est. ${levels.pwh - buf:.2f})",
        }
        # Bearish 1: False Breakout / Re-entry Short below PDH
        short_reject = {
            "scenario": "False Breakout Fail below PDH",
            "watch_level": "PDH",
            "watch_val": _round2(levels.pdh),
            "entry": _round2(levels.pdh),
            "stop": _round2(levels.pdh + buf),
            "t1": _round2(pd_mid),
            "t2": _round2(levels.pdl),
            "rr": _rr(levels.pdh, levels.pdh + buf, pd_mid),
            "trigger": f"Fails back below PDH (${levels.pdh:.2f}) with 5m candle close inside prior range",
            "invalidation": f"Stop loss on reclaim above PDH (est. ${levels.pdh + buf:.2f})",
        }
        # Bearish 2: Rejection at PWH
        short_breakdown = {
            "scenario": "Sweep & Reject PWH",
            "watch_level": "PWH",
            "watch_val": _round2(levels.pwh),
            "entry": _round2(levels.pwh),
            "stop": _round2(levels.pwh + buf),
            "t1": _round2(min(levels.pdh, levels.pwh - atr * 0.5)),
            "t2": _round2(pd_mid),
            "rr": _rr(levels.pwh, levels.pwh + buf, min(levels.pdh, levels.pwh - atr * 0.5)),
            "trigger": f"Tests PWH (${levels.pwh:.2f}) then 5m candle rejects back below",
            "invalidation": f"Stop loss on breakout above PWH (est. ${levels.pwh + buf:.2f})",
        }

    elif price < levels.pdl:
        position_desc = f"Below PDL (-${levels.pdl - price:.2f}, -{(levels.pdl - price)/levels.pdl*100:.2f}%) — Breakdown zone"
        primary_focus = f"Price is below PDL (${levels.pdl:.2f}). Watch PDL reclaim for bear trap long bounce to mid (${pd_mid:.2f}), or continuation breakdown toward PWL (${levels.pwl:.2f})."

        # Bullish 1: Sweep Reclaim PDL
        long_reclaim = {
            "scenario": "Sweep & Reclaim PDL",
            "watch_level": "PDL",
            "watch_val": _round2(levels.pdl),
            "entry": _round2(levels.pdl),
            "stop": _round2(levels.pdl - buf),
            "t1": _round2(pd_mid),
            "t2": _round2(levels.pdh),
            "rr": _rr(levels.pdl, levels.pdl - buf, pd_mid),
            "trigger": f"5m candle reclaims back above PDL (${levels.pdl:.2f}) after dipping below",
            "invalidation": f"Stop loss below sweep low (est. ${levels.pdl - buf:.2f})",
        }
        # Bullish 2: PWL Support Reclaim
        t1_pwl = max(levels.pdl, levels.pwl + atr * 0.5)
        long_breakout = {
            "scenario": "Sweep & Reclaim PWL",
            "watch_level": "PWL",
            "watch_val": _round2(levels.pwl),
            "entry": _round2(levels.pwl),
            "stop": _round2(levels.pwl - buf),
            "t1": _round2(t1_pwl),
            "t2": _round2(pd_mid),
            "rr": _rr(levels.pwl, levels.pwl - buf, t1_pwl),
            "trigger": f"Dips into PWL (${levels.pwl:.2f}) and reclaims with 5m bullish candle",
            "invalidation": f"Stop loss below PWL sweep low (est. ${levels.pwl - buf:.2f})",
        }
        # Bearish 1: Breakdown Retest PDL
        t1_bd_pdl = min(levels.pwl, levels.pdl - atr * 0.5)
        short_breakdown = {
            "scenario": "Breakdown Retest PDL",
            "watch_level": "PDL",
            "watch_val": _round2(levels.pdl),
            "entry": _round2(levels.pdl),
            "stop": _round2(levels.pdl + buf),
            "t1": _round2(t1_bd_pdl),
            "t2": _round2(levels.pdl - atr),
            "rr": _rr(levels.pdl, levels.pdl + buf, t1_bd_pdl),
            "trigger": f"Tests PDL (${levels.pdl:.2f}) from below and 5m candle confirms rejection",
            "invalidation": f"Stop loss on reclaim back above PDL (est. ${levels.pdl + buf:.2f})",
        }
        # Bearish 2: Breakdown Retest PWL
        t1_bd_pwl = levels.pwl - atr * 0.5
        short_reject = {
            "scenario": "Breakdown Retest PWL",
            "watch_level": "PWL",
            "watch_val": _round2(levels.pwl),
            "entry": _round2(levels.pwl),
            "stop": _round2(levels.pwl + buf),
            "t1": _round2(t1_bd_pwl),
            "t2": _round2(levels.pwl - atr),
            "rr": _rr(levels.pwl, levels.pwl + buf, t1_bd_pwl),
            "trigger": f"5m candle closes below PWL (${levels.pwl:.2f}) and rejects on retest",
            "invalidation": f"Stop loss on reclaim back above PWL (est. ${levels.pwl + buf:.2f})",
        }

    else:
        # Inside PDL-PDH Range
        if price >= pd_mid:
            position_desc = f"Upper Range ({pos_pct:.0f}% of PDL-PDH span) — Closer to Resistance PDH (+${dist_pdh:.2f})"
            primary_focus = f"Price is closer to PDH (${levels.pdh:.2f}, +${dist_pdh:.2f} away). Primary watch: Test of PDH for sweep rejection short, or clean 5m candle breakout hold long."
        else:
            position_desc = f"Lower Range ({pos_pct:.0f}% of PDL-PDH span) — Closer to Support PDL (-${dist_pdl:.2f})"
            primary_focus = f"Price is closer to PDL (${levels.pdl:.2f}, -${dist_pdl:.2f} away). Primary watch: Test of PDL for sweep reclaim bounce long, or breakdown retest short."

        # Bullish 1: Breakout & Retest PDH
        t1_bo = max(levels.pwh, levels.pdh + atr * 0.5)
        t2_bo = levels.pdh + atr
        long_breakout = {
            "scenario": "Breakout & Retest PDH",
            "watch_level": "PDH",
            "watch_val": _round2(levels.pdh),
            "entry": _round2(levels.pdh),
            "stop": _round2(levels.pdh - buf),
            "t1": _round2(t1_bo),
            "t2": _round2(t2_bo),
            "rr": _rr(levels.pdh, levels.pdh - buf, t1_bo),
            "trigger": f"5m candle closes above PDH (${levels.pdh:.2f}) and holds on retest",
            "invalidation": f"Stop loss on failed retest below PDH (est. ${levels.pdh - buf:.2f})",
        }
        # Bullish 2: Sweep & Reclaim PDL
        long_reclaim = {
            "scenario": "Sweep & Reclaim PDL",
            "watch_level": "PDL",
            "watch_val": _round2(levels.pdl),
            "entry": _round2(levels.pdl),
            "stop": _round2(levels.pdl - buf),
            "t1": _round2(pd_mid),
            "t2": _round2(levels.pdh),
            "rr": _rr(levels.pdl, levels.pdl - buf, pd_mid),
            "trigger": f"Dips below PDL (${levels.pdl:.2f}) then 5m candle reclaims back above",
            "invalidation": f"Stop loss below sweep low (est. ${levels.pdl - buf:.2f})",
        }
        # Bearish 1: Sweep & Reject PDH
        short_reject = {
            "scenario": "Sweep & Reject PDH",
            "watch_level": "PDH",
            "watch_val": _round2(levels.pdh),
            "entry": _round2(levels.pdh),
            "stop": _round2(levels.pdh + buf),
            "t1": _round2(pd_mid),
            "t2": _round2(levels.pdl),
            "rr": _rr(levels.pdh, levels.pdh + buf, pd_mid),
            "trigger": f"Pushes through PDH (${levels.pdh:.2f}) then 5m candle rejects back below",
            "invalidation": f"Stop loss above sweep high (est. ${levels.pdh + buf:.2f})",
        }
        # Bearish 2: Breakdown & Retest PDL
        t1_bd = min(levels.pwl, levels.pdl - atr * 0.5)
        t2_bd = levels.pdl - atr
        short_breakdown = {
            "scenario": "Breakdown & Retest PDL",
            "watch_level": "PDL",
            "watch_val": _round2(levels.pdl),
            "entry": _round2(levels.pdl),
            "stop": _round2(levels.pdl + buf),
            "t1": _round2(t1_bd),
            "t2": _round2(t2_bd),
            "rr": _rr(levels.pdl, levels.pdl + buf, t1_bd),
            "trigger": f"5m candle closes below PDL (${levels.pdl:.2f}) and rejects on retest",
            "invalidation": f"Stop loss on reclaim back above PDL (est. ${levels.pdl + buf:.2f})",
        }

    return {
        "position_desc": position_desc,
        "primary_focus": primary_focus,
        "pos_pct": _round2(pos_pct),
        "dist_pdh": _round2(dist_pdh),
        "dist_pdl": _round2(dist_pdl),
        "dist_pwh": _round2(dist_pwh),
        "dist_pwl": _round2(dist_pwl),
        "pd_mid": _round2(pd_mid),
        "long_reclaim": long_reclaim,
        "long_breakout": long_breakout,
        "short_reject": short_reject,
        "short_breakdown": short_breakdown,
    }


def analyze_from_daily(
    ticker: str,
    daily_df: pd.DataFrame,
    *,
    scan_date: Optional[date] = None,
    current_price: Optional[float] = None,
) -> dict:
    scan_date = scan_date or date.today()
    daily = _norm_daily(daily_df)
    if daily.empty:
        return {"ticker": ticker, "enabled": True, "error": "missing daily bars"}

    completed, session = _split_session(daily, scan_date)
    levels = _levels(completed)

    if current_price is not None and current_price > 0:
        price = float(current_price)
    elif session is not None:
        price = float(session["close"])
    else:
        price = float(completed.iloc[-1]["close"])

    session_high = float(session["high"]) if session is not None else price
    session_low = float(session["low"]) if session is not None else price
    signal = _pick_signal(price, session_high, session_low, levels)
    outcomes = compute_outcomes(price, levels)

    # 4-Hour market structure and tracking arrow
    h4_tag, h4_desc, h4_stats = _compute_4h_structure(_yf_symbol(ticker), scan_date, daily, use_prepost=True)
    h4_arr, h4_col, h4_badge = get_4h_structure_arrow(h4_tag)
    h4_focus = get_4h_narrowed_focus(h4_tag, asdict(levels), price)
    if isinstance(h4_focus, dict):
        stop_val = signal.stop if (signal is not None and signal.stop is not None) else None
        if stop_val is not None:
            h4_focus["stop_loss"] = stop_val
            h4_focus["stop_loss_str"] = f"${stop_val:.2f}"
            if "PDL" in str(h4_focus.get("badge", "")) and levels.pdl is not None:
                h4_focus["level_name"] = "PDL"
                h4_focus["level_value"] = levels.pdl
                h4_focus["badge_with_levels"] = f"{h4_focus.get('badge')} (PDL: ${levels.pdl:.2f} | SL: ${stop_val:.2f})"
                h4_focus["short_badge"] = f"{h4_focus.get('arrow', '')} {h4_focus.get('focus_type', '')} (PDL ${levels.pdl:.2f} | SL ${stop_val:.2f})".strip()
            elif "PDH" in str(h4_focus.get("badge", "")) and levels.pdh is not None:
                h4_focus["level_name"] = "PDH"
                h4_focus["level_value"] = levels.pdh
                h4_focus["badge_with_levels"] = f"{h4_focus.get('badge')} (PDH: ${levels.pdh:.2f} | SL: ${stop_val:.2f})"
                h4_focus["short_badge"] = f"{h4_focus.get('arrow', '')} {h4_focus.get('focus_type', '')} (PDH ${levels.pdh:.2f} | SL ${stop_val:.2f})".strip()
            else:
                h4_focus["badge_with_levels"] = f"{h4_focus.get('badge')} (SL: ${stop_val:.2f})"
                h4_focus["short_badge"] = f"{h4_focus.get('arrow', '')} {h4_focus.get('focus_type', '')} (SL ${stop_val:.2f})".strip()

    sig_dict = asdict(signal)
    sig_dict["four_hour_structure"] = h4_tag
    sig_dict["four_hour_structure_detail"] = h4_desc
    sig_dict["four_hour_arrow"] = h4_arr
    sig_dict["four_hour_badge"] = h4_badge
    sig_dict["four_hour_focus"] = h4_focus
    sig_dict["structure_tag"] = h4_tag

    return {
        "ticker": ticker,
        "enabled": True,
        "version": "v4",
        "as_of": datetime.now().isoformat(timespec="seconds"),
        "scan_date": scan_date.isoformat(),
        "price": _round2(price),
        "session_high": _round2(session_high),
        "session_low": _round2(session_low),
        "levels": asdict(levels),
        "signal": sig_dict,
        "outcomes": outcomes,
        "four_hour_structure": h4_tag,
        "four_hour_structure_detail": h4_desc,
        "four_hour_arrow": h4_arr,
        "four_hour_badge": h4_badge,
        "four_hour_color": h4_col,
        "four_hour_focus": h4_focus,
        "four_hour_stats": h4_stats,
        "structure_tag": h4_tag,
    }


YF_SYMBOL_MAP = {
    "SPXW": "^GSPC",
    "SPX": "^GSPC",
    "$SPX": "^GSPC",
    "^SPX": "^GSPC",
    "NDX": "^NDX",
    "$NDX": "^NDX",
    "RUT": "^RUT",
    "$RUT": "^RUT",
    "VIX": "^VIX",
    "$VIX": "^VIX",
}


def _yf_symbol(ticker: str) -> str:
    t = (ticker or "SPY").strip().upper()
    return YF_SYMBOL_MAP.get(t, t)


def _suggest_option_strike(ticker: str, price: float, side: Literal["CALLS", "PUTS"]) -> str:
    sym = (ticker or "SPY").strip().upper()
    is_index_5pt = sym in {"SPXW", "SPX", "$SPX", "^GSPC", "NDX", "$NDX", "^NDX"} or price >= 1500.0
    if is_index_5pt:
        offset = 5.0 if side == "CALLS" else -5.0
        strike = int(round((price + offset) / 5.0) * 5)
    else:
        offset = 1.0 if side == "CALLS" else -1.0
        strike = int(round(price + offset))
    return f"{sym} ${strike} {side}"


def _build_pulse_contract_options(
    ticker: str,
    price: float,
    action: str,
    t1: float,
    t2: float,
    atr: float,
    horizon: Literal["pulse", "long_term", "earning"] = "pulse",
) -> dict:
    """
    Generates both:
    1. Open / Naked Single-Leg ATM Contract (`open_contract` / `suggested_contract`)
    2. Cost-Efficient Contract Options:
       - `cost_efficient_otm`: Lower-cost OTM Runner (Delta ~0.28–0.35, ~45–60% lower premium)
       - `cost_efficient_spread`: Defined-Risk Vertical Debit Spread anchored to Target 1 (cuts cost by ~55–70% and protects against Theta decay & post-earnings IV crush)
    """
    sym = (ticker or "SPY").strip().upper()
    is_calls = "CALL" in (action or "").upper() or "LONG" in (action or "").upper()
    side: Literal["CALLS", "PUTS"] = "CALLS" if is_calls else "PUTS"
    opt_word = "CALL" if is_calls else "PUT"
    short_code = "C" if is_calls else "P"

    # Determine realistic CBOE option strike increment
    if sym in {"SPXW", "SPX", "$SPX", "^GSPC", "NDX", "$NDX", "^NDX"} or price >= 500.0:
        step = 5
    elif sym in {"SPY", "QQQ", "IWM", "DIA", "TQQQ", "SQQQ"}:
        step = 1 if horizon == "pulse" else (5 if price >= 250.0 else 2)
    elif price >= 200.0:
        step = 2 if horizon == "pulse" else 5
    elif price >= 50.0:
        step = 1 if horizon == "pulse" else (5 if price >= 110.0 else 2)
    else:
        step = 1

    if horizon == "pulse":
        dte_tag = "0DTE / Next Expiry"
        open_delta = "Delta 0.45–0.55 ATM"
        otm_delta = "Delta 0.28–0.35 OTM"
        max_spread_atr_mult = 1.4
    elif horizon == "long_term":
        dte_tag = "30–60 DTE"
        open_delta = "Delta 0.45–0.55 ATM"
        otm_delta = "Delta 0.30–0.35 OTM"
        max_spread_atr_mult = 2.6
    else:
        dte_tag = "45–90 DTE"
        open_delta = "Delta 0.45–0.60 ATM"
        otm_delta = "Delta 0.28–0.35 OTM"
        max_spread_atr_mult = 3.2

    # 1. Open / Primary ATM strike
    if is_calls:
        open_strike = int(round((price + max(step * 0.35, 0.5)) / step) * step)
        if open_strike < int(price):
            open_strike += step
    else:
        open_strike = int(round((price - max(step * 0.35, 0.5)) / step) * step)
        if open_strike > int(round(price)):
            open_strike -= step

    # 2. Cost-Efficient OTM Single-Leg Runner (positioned ~55% toward T1 or ~0.7–1.0x ATR OTM)
    dist_to_t1 = max(abs(t1 - price) * 0.55, atr * (0.65 if horizon == "pulse" else 0.95))
    if is_calls:
        otm_strike = max(open_strike + step, int(round((price + dist_to_t1) / step) * step))
    else:
        otm_strike = min(open_strike - step, int(round((price - dist_to_t1) / step) * step))

    # 3. Cost-Efficient Vertical Debit Spread (Buy open_strike, Sell Target 1 strike)
    max_width = max(step * 2, int(round((atr * max_spread_atr_mult) / step) * step))
    if is_calls:
        raw_short = int(round(t1 / step) * step)
        if raw_short <= open_strike:
            raw_short = max(open_strike + step, int(round(t2 / step) * step))
        short_strike = min(max(open_strike + step, raw_short), open_strike + max_width)
    else:
        raw_short = int(round(t1 / step) * step)
        if raw_short >= open_strike:
            raw_short = min(open_strike - step, int(round(t2 / step) * step))
        short_strike = max(min(open_strike - step, raw_short), open_strike - max_width)

    spread_width = round(float(abs(short_strike - open_strike)), 2)
    est_debit = round(max(0.25, spread_width * 0.34), 2)
    max_profit = round(spread_width - est_debit, 2)
    est_roi_pct = int(round((max_profit / est_debit) * 100)) if est_debit > 0 else 180

    # 4. Cost-Efficient Directional Butterfly (1x : -2x : 1x centered at Target 1 `short_strike`)
    wing_w = int(abs(short_strike - open_strike))
    if wing_w < step:
        wing_w = step
    bf_body = short_strike
    if is_calls:
        bf_wing1 = open_strike
        bf_wing2 = bf_body + wing_w
    else:
        bf_wing1 = open_strike
        bf_wing2 = max(step, bf_body - wing_w)
    bf_est_debit = round(max(0.15, wing_w * 0.12), 2)
    bf_max_profit = round(wing_w - bf_est_debit, 2)
    bf_roi_pct = int(round((bf_max_profit / bf_est_debit) * 100)) if bf_est_debit > 0 else 650

    open_contract_str = (
        f"{sym} ${open_strike} {side}"
        if horizon == "pulse"
        else f"{sym} ${open_strike} {side} ({dte_tag})"
    )
    open_contract_full = f"{sym} ${open_strike} {side} ({dte_tag} · {open_delta})"
    cost_efficient_otm = (
        f"{sym} ${otm_strike} {side} ({dte_tag} · {otm_delta} Runner, ~50% Lower Premium)"
    )
    cost_efficient_spread = (
        f"{sym} ${open_strike}/${short_strike} {opt_word} DEBIT SPREAD "
        f"(Buy ${open_strike}{short_code} / Sell ${short_strike}{short_code} · {dte_tag} · "
        f"Est. Debit ~${est_debit:.2f} for ${spread_width:.0f} Width, +{est_roi_pct}% Max ROI)"
    )
    cost_efficient_butterfly = (
        f"{sym} ${bf_wing1}/${bf_body}/${bf_wing2} {opt_word} BUTTERFLY "
        f"(+1 ${bf_wing1}{short_code} / -2 ${bf_body}{short_code} / +1 ${bf_wing2}{short_code} · {dte_tag} · "
        f"Pinned at T1 ${bf_body} · Est. Debit ~${bf_est_debit:.2f} for ${wing_w:.0f} Wing, +{bf_roi_pct}% Max ROI)"
    )
    cost_efficient_summary = (
        f"OTM Runner: {sym} ${otm_strike} {side} ({otm_delta}) | "
        f"Debit Spread: Buy ${open_strike}{short_code} / Sell ${short_strike}{short_code} (~${est_debit:.2f} debit) | "
        f"Butterfly: +1 ${bf_wing1}{short_code} / -2 ${bf_body}{short_code} / +1 ${bf_wing2}{short_code} (~${bf_est_debit:.2f} debit)"
    )

    return {
        "suggested_contract": open_contract_str,
        "open_contract": open_contract_full,
        "cost_efficient_otm": cost_efficient_otm,
        "cost_efficient_spread": cost_efficient_spread,
        "cost_efficient_butterfly": cost_efficient_butterfly,
        "cost_efficient_summary": cost_efficient_summary,
        "cost_efficient_contracts": {
            "otm_single_leg": cost_efficient_otm,
            "vertical_debit_spread": cost_efficient_spread,
            "butterfly_spread": cost_efficient_butterfly,
            "long_leg": f"BUY {sym} ${open_strike} {opt_word}",
            "short_leg": f"SELL {sym} ${short_strike} {opt_word}",
            "butterfly_legs": f"BUY 1x ${bf_wing1}{short_code} / SELL 2x ${bf_body}{short_code} (at T1) / BUY 1x ${bf_wing2}{short_code}",
            "spread_width": spread_width,
            "estimated_debit": est_debit,
            "max_profit_per_spread": max_profit,
            "estimated_max_roi": f"+{est_roi_pct}%",
            "butterfly_wing_width": float(wing_w),
            "butterfly_estimated_debit": bf_est_debit,
            "butterfly_max_profit": bf_max_profit,
            "butterfly_estimated_max_roi": f"+{bf_roi_pct}%",
            "benefit": (
                "Debit Spread cuts upfront capital by ~50–65%; Butterfly cuts capital by ~80–85% with ~6x–7x max payout if price pins near Target 1 while profiting from Theta & IV crush."
            ),
        },
    }


def _roll_to_last_weekday(d: date) -> date:
    """If `d` falls on Saturday (5) or Sunday (6), roll back to Friday (last weekday)."""
    from datetime import timedelta
    while d.weekday() >= 5:
        d -= timedelta(days=1)
    return d


def _format_session_as_of(scan_date: date, req_date: Optional[date] = None) -> tuple[str, str, bool]:
    """
    Returns (as_of_label, session_day_name, is_weekend_rolled) so weekend queries
    always report the Last Weekday Trading Session instead of Saturday/Sunday.
    """
    today = date.today()
    is_weekend = today.weekday() >= 5 or (req_date is not None and req_date.weekday() >= 5)
    day_full = scan_date.strftime("%A, %B %d, %Y")
    day_short = scan_date.strftime("%a, %b %d, %Y")
    if is_weekend and (req_date is None or req_date >= scan_date):
        as_of = f"{day_short} ({scan_date.isoformat()} · Last Weekday Session — Weekend Market Closed)"
        session_label = f"{day_full} ({scan_date.isoformat()} · Last Completed Weekday)"
    elif req_date and req_date != today:
        as_of = f"{day_short} ({scan_date.isoformat()} · Historical Session Backtest)"
        session_label = f"{day_full} ({scan_date.isoformat()})"
    else:
        as_of = f"{day_short} ({scan_date.isoformat()} · {datetime.now().strftime('%I:%M %p CT')})"
        session_label = f"{day_full} ({scan_date.isoformat()})"
    return as_of, session_label, is_weekend


def parse_pulse_date(raw_text: Optional[str]) -> Optional[date]:
    """
    Extracts a target date from a date string or natural-language query such as:
    '10/12/2026', '2026-10-12', '10-12-2026', '10/12', 'Oct 12, 2026',
    'what is tesla pulse on 10/12/2026?'
    If the extracted date (or 'today' on a weekend) falls on Saturday/Sunday,
    automatically rolls back to the last weekday (Friday).
    """
    if not raw_text:
        return None
    import re
    from datetime import timedelta

    s = str(raw_text).strip()
    lower = s.lower()
    today = date.today()
    if "yesterday" in lower or "last weekday" in lower or "last trading day" in lower:
        d = today - timedelta(days=1) if "yesterday" in lower else today
        return _roll_to_last_weekday(d)

    # 1. YYYY-MM-DD or YYYY/MM/DD
    m_iso = re.search(r"\b(20\d{2})[-/](\d{1,2})[-/](\d{1,2})\b", s)
    if m_iso:
        try:
            return _roll_to_last_weekday(
                date(int(m_iso.group(1)), int(m_iso.group(2)), int(m_iso.group(3)))
            )
        except ValueError:
            pass

    # 2. MM/DD/YYYY or MM-DD-YYYY or MM/DD/YY
    m_mdy = re.search(r"\b(\d{1,2})[-/](\d{1,2})[-/](20\d{2}|\d{2})\b", s)
    if m_mdy:
        try:
            yr = int(m_mdy.group(3))
            if yr < 100:
                yr += 2000
            return _roll_to_last_weekday(date(yr, int(m_mdy.group(1)), int(m_mdy.group(2))))
        except ValueError:
            pass

    # 3. Month Name DD, YYYY or Month Name DD (e.g. Oct 12, 2026 or October 12)
    month_map = {
        "jan": 1, "january": 1, "feb": 2, "february": 2, "mar": 3, "march": 3,
        "apr": 4, "april": 4, "may": 5, "jun": 6, "june": 6, "jul": 7, "july": 7,
        "aug": 8, "august": 8, "sep": 9, "sept": 9, "september": 9,
        "oct": 10, "october": 10, "nov": 11, "november": 11, "dec": 12, "december": 12,
    }
    m_named = re.search(
        r"\b(jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|jul(?:y)?|aug(?:ust)?|sep(?:t(?:ember)?)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)\s+(\d{1,2})(?:st|nd|rd|th)?(?:,?\s*(20\d{2}))?\b",
        lower,
    )
    if m_named:
        try:
            mo = month_map[m_named.group(1)]
            dy = int(m_named.group(2))
            yr = int(m_named.group(3)) if m_named.group(3) else today.year
            return _roll_to_last_weekday(date(yr, mo, dy))
        except ValueError:
            pass

    # 4. Short MM/DD (e.g. "on 10/12" or "pulse 10/12") — avoid matching option expiries unless standalone
    m_short = re.search(r"(?:on|for|date|pulse|v4)\s+(\d{1,2})/(\d{1,2})\b", lower)
    if m_short:
        try:
            return _roll_to_last_weekday(
                date(today.year, int(m_short.group(1)), int(m_short.group(2)))
            )
        except ValueError:
            pass

    return None


def _resolve_trading_session(daily_df: pd.DataFrame, requested_date: date) -> tuple[date, pd.DataFrame, Optional[pd.Series]]:
    """
    Finds the exact or nearest prior weekday trading session in daily_df for requested_date,
    returning (resolved_date, completed_before_date, session_row).
    """
    target_weekday = _roll_to_last_weekday(requested_date)
    daily = _norm_daily(daily_df)
    if daily.empty:
        return target_weekday, pd.DataFrame(), None
    idx_dates = [_idx_date(i) for i in daily.index]
    # Find exact match or latest weekday trading date <= target_weekday
    valid_dates = [d for d in idx_dates if d <= target_weekday and d.weekday() < 5]
    resolved = valid_dates[-1] if valid_dates else idx_dates[-1]
    completed = daily[[d < resolved for d in idx_dates]]
    session_rows = daily[[d == resolved for d in idx_dates]]
    session = session_rows.iloc[-1] if not session_rows.empty else None
    return resolved, completed, session


def _compute_market_structure(
    curr_high: float,
    curr_low: float,
    prev_high: float,
    prev_low: float,
    prefix: str = "4H",
    curr_close: Optional[float] = None,
    curr_open: Optional[float] = None,
    action: str = "",
    outcome: str = "",
) -> tuple[str, str]:
    """
    Computes genuine price action market structure returning exactly ONE dominant swing tag:
    - '4H: HH', '4H: HL', '4H: LH', or '4H: LL' (matching ThinkorSwim 4H chart)
    - 'D: HH', 'D: HL', 'D: LH', or 'D: LL' (Long Term Pulse)
    - 'W: HH', 'W: HL', 'W: LH', or 'W: LL' (Earning Pulse)
    Pure price action swing rules:
      HH = Higher High (curr_high > prev_high and curr_low >= prev_low, or bullish outside bar)
      LL = Lower Low (curr_low < prev_low and curr_high <= prev_high, or bearish outside bar)
      HL = Higher Low (curr_low > prev_low and curr_high <= prev_high, or bullish inside bar)
      LH = Lower High (curr_high < prev_high and curr_low >= prev_low, or bearish inside bar)
    """
    # 1. Higher High Expansion (HH):
    if curr_high > prev_high and curr_low >= prev_low:
        tag = "HH"
        desc = "Higher High Expansion"
    # 2. Lower Low Breakdown (LL):
    elif curr_low < prev_low and curr_high <= prev_high:
        tag = "LL"
        desc = "Lower Low Breakdown"
    # 3. Outside Bar (Higher High & Lower Low expansion):
    elif curr_high > prev_high and curr_low < prev_low:
        is_bull_bar = (curr_close >= curr_open) if (curr_close is not None and curr_open is not None) else (curr_close >= (curr_high + curr_low) / 2.0 if curr_close is not None else True)
        if is_bull_bar:
            tag = "HH"
            desc = "Bullish Outside Bar Expansion"
        else:
            tag = "LL"
            desc = "Bearish Outside Bar Rejection"
    # 4. Inside Bar (Lower/equal High & Higher/equal Low consolidation):
    elif curr_high <= prev_high and curr_low >= prev_low:
        is_bull_bar = (curr_close >= curr_open) if (curr_close is not None and curr_open is not None) else (curr_close >= (curr_high + curr_low) / 2.0 if curr_close is not None else True)
        if is_bull_bar:
            tag = "HL"
            desc = "Higher Low Support Hold"
        else:
            tag = "LH"
            desc = "Lower High Resistance Rejection"
    # 5. Higher Low pullback / support hold:
    elif curr_low > prev_low and curr_high <= prev_high:
        tag = "HL"
        desc = "Higher Low Support Hold"
    # 6. Lower High rejection / failed retest:
    elif curr_high < prev_high and curr_low >= prev_low:
        tag = "LH"
        desc = "Lower High Resistance Rejection"
    else:
        tag = "HL" if (curr_close is not None and curr_open is not None and curr_close >= curr_open) else "LH"
        desc = "Market Structure Consolidation"

    p_name = "4-Hour" if prefix == "4H" else ("Daily" if prefix == "D" else ("Weekly" if prefix == "W" else prefix))
    return f"{prefix}: {tag}", f"{p_name} {desc} ({tag})"


def get_4h_structure_arrow(structure_tag: str) -> tuple[str, str, str]:
    """
    Returns (arrow_symbol, color_emoji, formatted_badge) for 4H market structure:
    - 4H: HH -> ('⬆️', '🟢', '🟢 ⬆️ 4H: HH')
    - 4H: HL -> ('↗️', '🟢', '🟢 ↗️ 4H: HL')
    - 4H: LH -> ('↘️', '🔴', '🔴 ↘️ 4H: LH')
    - 4H: LL -> ('⬇️', '🔴', '🔴 ⬇️ 4H: LL')
    """
    tag_clean = str(structure_tag or "").upper()
    if "HH" in tag_clean:
        return ("⬆️", "🟢", "🟢 ⬆️ 4H: HH")
    elif "HL" in tag_clean:
        return ("↗️", "🟢", "🟢 ↗️ 4H: HL")
    elif "LH" in tag_clean:
        return ("↘️", "🔴", "🔴 ↘️ 4H: LH")
    elif "LL" in tag_clean:
        return ("⬇️", "🔴", "🔴 ⬇️ 4H: LL")
    elif "WAIT" in tag_clean or "TIMER" in tag_clean:
        return ("⏱️", "🟡", f"⏱️ 🟡 {structure_tag}")
    return ("➡️", "⚪", f"⚪ ➡️ {structure_tag or '4H: N/A'}")


def get_4h_narrowed_focus(
    structure_tag: str,
    levels: Optional[dict] = None,
    price: Optional[float] = None,
) -> dict:
    """
    Narrows down the Day Trading V4 Playbook to a single primary focus setup
    (Breakout vs Sweep/Breakdown) based on the 8:05 AM 4H Market Structure:
    - 4H: HH -> 🟢 ⬆️ Breakout Focus (Breakout & Retest PDH)
    - 4H: HL -> 🟢 ↗️ Sweep Focus (Sweep & Reclaim PDL Bear Trap Bounce)
    - 4H: LH -> 🔴 ↘️ Sweep Focus (Sweep & Reject PDH Bull Trap Rejection)
    - 4H: LL -> 🔴 ⬇️ Breakdown Focus (Breakdown & Retest PDL)
    """
    tag = str(structure_tag or "").upper()
    lvl = levels or {}
    pdh = float(lvl.get("pdh", 0) or 0)
    pdl = float(lvl.get("pdl", 0) or 0)
    pd_mid = float(lvl.get("pd_mid", 0) or 0)

    if "HH" in tag:
        return {
            "focus_type": "Breakout",
            "arrow": "⬆️",
            "color": "🟢",
            "badge": "🟢 ⬆️ BREAKOUT FOCUS: Breakout & Retest PDH",
            "short_badge": "🟢 ⬆️ Breakout (PDH)",
            "setup_name": "Breakout & Retest PDH",
            "setup_key": "long_breakout",
            "side": "LONG",
            "action": "BUY CALLS",
            "trigger_level": pdh,
            "summary": "4H Higher High (HH) indicates upward structural expansion. Primary morning focus is Breakout & Retest above PDH.",
            "rule": f"Wait for 5m candle close above PDH (${pdh:.2f}), then enter on retest holding above PDH.",
        }
    elif "HL" in tag:
        return {
            "focus_type": "Sweep",
            "arrow": "↗️",
            "color": "🟢",
            "badge": "🟢 ↗️ SWEEP FOCUS: Sweep & Reclaim PDL (Bear Trap)",
            "short_badge": "🟢 ↗️ Sweep (PDL Reclaim)",
            "setup_name": "Sweep & Reclaim PDL",
            "setup_key": "long_reclaim",
            "side": "LONG",
            "action": "BUY CALLS",
            "trigger_level": pdl,
            "summary": "4H Higher Low (HL) indicates structural support defense. Primary morning focus is Sweep & Reclaim of PDL support.",
            "rule": f"Wait for dip into/below PDL (${pdl:.2f}), then enter as 5m candle reclaims back above PDL.",
        }
    elif "LH" in tag:
        return {
            "focus_type": "Sweep",
            "arrow": "↘️",
            "color": "🔴",
            "badge": "🔴 ↘️ SWEEP FOCUS: Sweep & Reject PDH (Bull Trap)",
            "short_badge": "🔴 ↘️ Sweep (PDH Reject)",
            "setup_name": "Sweep & Reject PDH",
            "setup_key": "short_reject",
            "side": "SHORT",
            "action": "BUY PUTS",
            "trigger_level": pdh,
            "summary": "4H Lower High (LH) indicates seller dominance on rallies. Primary morning focus is Sweep & Reject of PDH for a short fade.",
            "rule": f"Wait for push into/above PDH (${pdh:.2f}), then enter as 5m candle rejects back below PDH.",
        }
    elif "LL" in tag:
        return {
            "focus_type": "Breakdown",
            "arrow": "⬇️",
            "color": "🔴",
            "badge": "🔴 ⬇️ BREAKDOWN FOCUS: Breakdown & Retest PDL",
            "short_badge": "🔴 ⬇️ Breakdown (PDL)",
            "setup_name": "Breakdown & Retest PDL",
            "setup_key": "short_breakdown",
            "side": "SHORT",
            "action": "BUY PUTS",
            "trigger_level": pdl,
            "summary": "4H Lower Low (LL) indicates downward structural breakdown. Primary morning focus is Breakdown & Retest below PDL.",
            "rule": f"Wait for 5m candle close below PDL (${pdl:.2f}), then enter on retest rejecting PDL from underneath.",
        }
    else:
        return {
            "focus_type": "Range",
            "arrow": "➡️",
            "color": "⚪",
            "badge": "⚪ ➡️ RANGE FOCUS: Wait for Confirmed Extreme Reaction",
            "short_badge": "⚪ ➡️ Range Wait",
            "setup_name": "Range Wait & React",
            "setup_key": "range_wait",
            "side": "NEUTRAL",
            "action": "WAIT",
            "trigger_level": pd_mid,
            "summary": "4H Market structure consolidating. Monitor reaction at PDH and PDL boundaries.",
            "rule": "Wait for confirmed break or sweep at outer boundaries.",
        }


def check_4h_confluence(focus_dict: dict, valid_outcome: str, action: str) -> dict:
    """
    Evaluates whether the 8:35 AM confirmed outcome is aligned with the 4H 8:05 AM Narrowed Focus.
    """
    f_side = focus_dict.get("side", "NEUTRAL")
    f_setup = focus_dict.get("setup_name", "").lower()
    f_type = focus_dict.get("focus_type", "").lower()
    v_out = str(valid_outcome or "").lower()
    act = str(action or "").upper()

    aligned_side = (f_side == "LONG" and "CALLS" in act) or (f_side == "SHORT" and "PUTS" in act)
    aligned_setup = (f_setup in v_out) or (f_type in v_out)

    if aligned_setup:
        status = "ALIGNED_SETUP"
        badge = "✅ PERFECT 4H CONFLUENCE (Setup & Direction Aligned)"
        note = f"8:35 AM opening candle confirmed the exact 4H {focus_dict.get('setup_name')} setup!"
    elif aligned_side:
        status = "ALIGNED_DIRECTION"
        badge = "✅ DIRECTIONALLY ALIGNED (4H Trend Support)"
        note = f"8:35 AM trade direction ({act}) aligns with 4H {f_side} bias."
    else:
        status = "COUNTER_STRUCTURE"
        badge = "⚠️ COUNTER-STRUCTURE TRADE (Mean Reversion)"
        note = f"8:35 AM price action triggered counter to 4H {focus_dict.get('focus_type')}; trade with strict invalidation."

    return {
        "status": status,
        "badge": badge,
        "note": note,
        "is_aligned": aligned_side,
    }


def _compute_4h_structure(
    yf_sym: str,
    scan_date: date,
    daily_df: Optional[pd.DataFrame] = None,
    use_prepost: bool = False,
) -> tuple[str, str, dict]:
    """
    Computes 4-Hour market structure matching ThinkorSwim (TOS) 4H chart aggregation:
    - RTH 4H candles: Bar 1 (09:30 - 13:30 ET) and Bar 2 (13:30 - 16:00 ET).
    - ETH Pre-market 4H candles: 04:00 - 08:00 ET and 08:00 - 12:00 ET.
    - Evaluates the latest completed/session 4H candle vs previous 4H candle into:
      '4H: HH', '4H: HL', '4H: LH', or '4H: LL' using strict ThinkorSwim price action swing rules.
    """
    from datetime import timedelta, datetime
    import yfinance as yf
    import logging
    _log = logging.getLogger(__name__)

    if isinstance(scan_date, str):
        try:
            scan_date = datetime.strptime(scan_date, "%Y-%m-%d").date()
        except Exception:
            pass

    empty = ("4H: N/A", "4H data unavailable", {})
    try:
        # Check if historical lookup is within 1h bar availability (last ~700 days)
        if isinstance(scan_date, (date, datetime)) and (date.today() - (scan_date.date() if isinstance(scan_date, datetime) else scan_date)).days <= 700:
            s_date = scan_date.date() if isinstance(scan_date, datetime) else scan_date
            start_str = (s_date - timedelta(days=12)).isoformat()
            end_str = (s_date + timedelta(days=2)).isoformat()
            include_prepost = bool(use_prepost)
            h1 = yf.Ticker(yf_sym).history(start=start_str, end=end_str, interval="1h", auto_adjust=True, prepost=include_prepost)
            if h1 is not None and not h1.empty:
                # Include bars on or before scan_date
                session_bars = h1[[i.date() <= s_date for i in h1.index]]
                if not session_bars.empty:
                    # ThinkorSwim 4H candles: offset='4h' for ETH pre-market (04:00, 08:00, 12:00, 16:00 ET), offset='9h30min' for RTH
                    offset_str = "4h" if include_prepost else "9h30min"
                    h4 = session_bars.resample("4h", origin="start_day", offset=offset_str).agg({
                        "Open": "first", "High": "max", "Low": "min", "Close": "last"
                    }).dropna()
                    if len(h4) >= 2:
                        prev = h4.iloc[-2]
                        curr = h4.iloc[-1]
                        ch, cl = float(curr["High"]), float(curr["Low"])
                        co, cc = float(curr["Open"]), float(curr["Close"])
                        ph, pl = float(prev["High"]), float(prev["Low"])
                        po, pc = float(prev["Open"]), float(prev["Close"])

                        # 4H Candlestick Pattern Detection (Inside Bar & Engulfing Bar)
                        h4_pattern = None
                        if ch <= ph and cl >= pl:
                            h4_pattern = "Inside Bar (Bullish 🟢)" if cc >= co else "Inside Bar (Bearish 🔴)"
                        elif pc < po and cc > co and co <= pc and cc >= po:
                            h4_pattern = "Bullish Engulfing 🟢"
                        elif pc > po and cc < co and co >= pc and cc <= po:
                            h4_pattern = "Bearish Engulfing 🔴"

                        tag, desc = _compute_market_structure(
                            ch, cl, ph, pl, prefix="4H", curr_close=cc, curr_open=co
                        )
                        arr, col, badge = get_4h_structure_arrow(tag)
                        stats = {
                            "curr_time": str(curr.name),
                            "curr_high": round(ch, 2),
                            "curr_low": round(cl, 2),
                            "curr_open": round(co, 2),
                            "curr_close": round(cc, 2),
                            "prev_time": str(prev.name),
                            "prev_high": round(ph, 2),
                            "prev_low": round(pl, 2),
                            "prev_open": round(po, 2),
                            "prev_close": round(pc, 2),
                            "four_hour_structure": tag,
                            "four_hour_structure_detail": desc,
                            "four_hour_arrow": arr,
                            "four_hour_badge": badge,
                            "four_hour_color": col,
                            "four_hour_pattern": h4_pattern,
                            "is_premarket_eth": include_prepost,
                        }
                        return tag, desc, stats
    except Exception as exc:
        _log.debug(f"[4H structure] Exception fetching 1h bars for {yf_sym}: {exc}")

    # Fallback to daily bars if 1h bars unavailable (e.g. historical backtest >700 days ago)
    if daily_df is not None and len(daily_df) >= 2:
        idx_dates = [_idx_date(i) for i in daily_df.index]
        completed_daily = daily_df[[d <= scan_date for d in idx_dates]]
        if len(completed_daily) >= 2:
            prev_d = completed_daily.iloc[-2]
            curr_d = completed_daily.iloc[-1]
            ch, cl = float(curr_d["high"]), float(curr_d["low"])
            co, cc = float(curr_d["open"]), float(curr_d["close"])
            ph, pl = float(prev_d["high"]), float(prev_d["low"])
            tag, desc = _compute_market_structure(ch, cl, ph, pl, prefix="4H", curr_close=cc, curr_open=co)
            arr, col, badge = get_4h_structure_arrow(tag)
            return tag, desc, {
                "fallback": "daily_bar_proxy",
                "curr_high": round(ch, 2),
                "curr_low": round(cl, 2),
                "four_hour_structure": tag,
                "four_hour_structure_detail": desc,
                "four_hour_arrow": arr,
                "four_hour_badge": badge,
                "four_hour_color": col,
            }

    return empty


def _compute_weekly_structure(daily_df: pd.DataFrame, scan_date: date, action: str = "", outcome: str = "") -> tuple[str, str, dict]:
    """
    Computes weekly market structure (W: LH, W: LL, W: HH, W: HL) for scan_date vs previous calendar week.
    Returns: (weekly_tag, weekly_detail, weekly_stats_dict)
    """
    idx_dates = [_idx_date(i) for i in daily_df.index]
    hist_daily = daily_df[[d <= scan_date for d in idx_dates]].copy()
    if len(hist_daily) < 10:
        return "W: N/A", "Insufficient bars for weekly structure", {}

    hist_dates = [_idx_date(i) for i in hist_daily.index]
    hist_daily["year_week"] = [d.isocalendar()[:2] for d in hist_dates]

    unique_weeks = hist_daily["year_week"].drop_duplicates().tolist()
    if len(unique_weeks) < 2:
        curr_week_df = hist_daily.tail(5)
        prev_week_df = hist_daily.iloc[-10:-5]
    else:
        curr_week_df = hist_daily[hist_daily["year_week"] == unique_weeks[-1]]
        prev_week_df = hist_daily[hist_daily["year_week"] == unique_weeks[-2]]

    curr_high = round(float(curr_week_df["high"].max()), 2)
    curr_low = round(float(curr_week_df["low"].min()), 2)
    curr_open = round(float(curr_week_df.iloc[0]["open"]), 2)
    curr_close = round(float(curr_week_df.iloc[-1]["close"]), 2)
    prev_high = round(float(prev_week_df["high"].max()), 2)
    prev_low = round(float(prev_week_df["low"].min()), 2)

    w_tag, w_detail = _compute_market_structure(
        curr_high, curr_low, prev_high, prev_low, prefix="W", curr_close=curr_close, curr_open=curr_open, action=action, outcome=outcome
    )
    w_stats = {
        "curr_week_high": curr_high,
        "curr_week_low": curr_low,
        "prev_week_high": prev_high,
        "prev_week_low": prev_low,
        "weekly_structure": w_tag,
        "weekly_structure_detail": w_detail,
    }
    return w_tag, w_detail, w_stats


def _simulate_trade_backtest(
    bars_df: pd.DataFrame,
    entry: float,
    stop: float,
    t1: float,
    t2: float,
    action: str,
    *,
    timeframe_label: str = "Intraday (Post-8:35 AM CT)",
    fallback_high: Optional[float] = None,
    fallback_low: Optional[float] = None,
    fallback_close: Optional[float] = None,
    structure_tag: Optional[str] = None,
) -> dict:
    """
    Simulates trade execution chronologically across `bars_df` (5m/1h intraday bars or subsequent daily bars).
    Tracks 50% scale at T1 + stop moved to breakeven, T2 hit, Stop hit, MFE, MAE, and realized R-multiple.
    """
    is_calls = "CALL" in action.upper() or "LONG" in action.upper()
    risk = abs(entry - stop) if abs(entry - stop) > 0.001 else max(entry * 0.01, 0.5)

    t1_hit = False
    t1_hit_at: Optional[str] = None
    t2_hit = False
    t2_hit_at: Optional[str] = None
    stop_hit = False
    stop_hit_at: Optional[str] = None
    runner_be_hit = False
    runner_be_at: Optional[str] = None

    post_high = entry
    post_low = entry
    last_close = entry
    bars_evaluated = 0

    if bars_df is not None and not bars_df.empty:
        active_stop = stop
        for idx, row in bars_df.iterrows():
            bars_evaluated += 1
            h = float(row.get("High", row.get("high", entry)))
            l = float(row.get("Low", row.get("low", entry)))
            c = float(row.get("Close", row.get("close", entry)))
            post_high = max(post_high, h)
            post_low = min(post_low, l)
            last_close = c

            try:
                ts = pd.Timestamp(idx)
                if ts.tzinfo is not None:
                    ts_ct = ts.tz_convert("America/Chicago")
                    ts_label = ts_ct.strftime("%Y-%m-%d %I:%M %p CT") if (ts_ct.hour or ts_ct.minute) else ts_ct.strftime("%Y-%m-%d")
                else:
                    ts_label = ts.strftime("%Y-%m-%d %H:%M") if (ts.hour or ts.minute) else ts.strftime("%Y-%m-%d")
            except Exception:
                ts_label = str(idx)

            if is_calls:
                # Check stop first if bar broke below active_stop before hitting targets
                if not t1_hit and l <= active_stop:
                    stop_hit = True
                    stop_hit_at = ts_label
                    break
                if h >= t1 and not t1_hit:
                    t1_hit = True
                    t1_hit_at = ts_label
                    active_stop = entry  # Trail stop to breakeven after T1
                if h >= t2 and not t2_hit:
                    t2_hit = True
                    t2_hit_at = ts_label
                    break
                if t1_hit and not t2_hit and l <= active_stop and t1_hit_at != ts_label:
                    runner_be_hit = True
                    runner_be_at = ts_label
                    break
            else:
                if not t1_hit and h >= active_stop:
                    stop_hit = True
                    stop_hit_at = ts_label
                    break
                if l <= t1 and not t1_hit:
                    t1_hit = True
                    t1_hit_at = ts_label
                    active_stop = entry  # Trail stop to breakeven after T1
                if l <= t2 and not t2_hit:
                    t2_hit = True
                    t2_hit_at = ts_label
                    break
                if t1_hit and not t2_hit and h >= active_stop and t1_hit_at != ts_label:
                    runner_be_hit = True
                    runner_be_at = ts_label
                    break
    elif fallback_high is not None and fallback_low is not None and fallback_close is not None:
        bars_evaluated = 1
        post_high = max(entry, float(fallback_high))
        post_low = min(entry, float(fallback_low))
        last_close = float(fallback_close)
        if is_calls:
            if post_high >= t2:
                t1_hit = True
                t2_hit = True
                t1_hit_at = "Session High"
                t2_hit_at = "Session High"
            elif post_high >= t1:
                t1_hit = True
                t1_hit_at = "Session High"
                if post_low <= entry:
                    runner_be_hit = True
                    runner_be_at = "Intraday Pullback"
            elif post_low <= stop:
                stop_hit = True
                stop_hit_at = "Session Low"
        else:
            if post_low <= t2:
                t1_hit = True
                t2_hit = True
                t1_hit_at = "Session Low"
                t2_hit_at = "Session Low"
            elif post_low <= t1:
                t1_hit = True
                t1_hit_at = "Session Low"
                if post_high >= entry:
                    runner_be_hit = True
                    runner_be_at = "Intraday Bounce"
            elif post_high >= stop:
                stop_hit = True
                stop_hit_at = "Session High"

    if is_calls:
        mfe_dlr = round(max(0.0, post_high - entry), 2)
        mae_dlr = round(max(0.0, entry - post_low), 2)
        unscaled_close_pnl = last_close - entry
    else:
        mfe_dlr = round(max(0.0, entry - post_low), 2)
        mae_dlr = round(max(0.0, post_high - entry), 2)
        unscaled_close_pnl = entry - last_close

    mfe_pct = round((mfe_dlr / entry) * 100, 2) if entry > 0 else 0.0
    mae_pct = round((mae_dlr / entry) * 100, 2) if entry > 0 else 0.0

    if t2_hit:
        status = "WIN — Target 1 & Target 2 Hit"
        badge = "✅ FULL WIN (T1 & T2 HIT)"
        realized_pnl = 0.5 * abs(t1 - entry) + 0.5 * abs(t2 - entry)
        summary = (
            f"Hit Target 1 (${t1:.2f}) at {t1_hit_at} and Target 2 (${t2:.2f}) at {t2_hit_at}. "
            f"Scaled 50% at T1 + 50% at T2 for +${realized_pnl:.2f}/sh (+{realized_pnl / risk:.2f}R)."
        )
    elif t1_hit and runner_be_hit:
        status = "WIN — Target 1 Hit (50% Scaled, Runner Stopped at Breakeven)"
        badge = "✅ WIN (T1 HIT · RUNNER BE)"
        realized_pnl = 0.5 * abs(t1 - entry)
        summary = (
            f"Hit Target 1 (${t1:.2f}) at {t1_hit_at} (50% profit locked), then runner stopped at breakeven (${entry:.2f}) at {runner_be_at} "
            f"for +${realized_pnl:.2f}/sh (+{realized_pnl / risk:.2f}R)."
        )
    elif t1_hit:
        status = "WIN — Target 1 Hit (50% Scaled, Runner Held to Close)"
        badge = "✅ WIN (T1 HIT)"
        realized_pnl = 0.5 * abs(t1 - entry) + 0.5 * unscaled_close_pnl
        summary = (
            f"Hit Target 1 (${t1:.2f}) at {t1_hit_at} (50% profit locked) and runner finished at ${last_close:.2f} "
            f"for +${realized_pnl:.2f}/sh (+{realized_pnl / risk:.2f}R)."
        )
    elif stop_hit:
        status = "LOSS — Stop Loss Hit"
        badge = "❌ STOPPED OUT"
        realized_pnl = -abs(entry - stop)
        summary = (
            f"Invalidation stop (${stop:.2f}) was hit at {stop_hit_at} before reaching Target 1 (${t1:.2f}). "
            f"Peak favorable move before/after was +${mfe_dlr:.2f} ({mfe_pct:+.2f}%). Result: -${abs(realized_pnl):.2f}/sh (-1.00R)."
        )
    elif bars_evaluated > 0:
        realized_pnl = unscaled_close_pnl
        r_mult = realized_pnl / risk
        badge = f"⏳ EOD / ACTIVE ({realized_pnl:+.2f}/sh · {r_mult:+.2f}R)"
        status = "OPEN / EOD Mark-to-Market (Neither Stop nor T1 Hit)"
        summary = (
            f"Neither Stop (${stop:.2f}) nor Target 1 (${t1:.2f}) was hit across {bars_evaluated} bars. "
            f"Range [${post_low:.2f} – ${post_high:.2f}], finished at ${last_close:.2f} ({realized_pnl:+.2f}/sh, {r_mult:+.2f}R)."
        )
    else:
        realized_pnl = 0.0
        badge = "⏳ PENDING FOLLOW-THROUGH"
        status = "No subsequent bars after entry yet"
        summary = "Entry triggered on the latest bar; awaiting forward price bars for backtest resolution."

    if structure_tag:
        badge = f"{badge} · [{structure_tag}]"
        summary = f"[{structure_tag}] {summary}"

    return {
        "timeframe": timeframe_label,
        "structure_tag": structure_tag,
        "bars_evaluated": bars_evaluated,
        "status": status,
        "result_badge": badge,
        "t1_hit": t1_hit,
        "t1_hit_at": t1_hit_at,
        "t2_hit": t2_hit,
        "t2_hit_at": t2_hit_at,
        "stop_hit": stop_hit,
        "stop_hit_at": stop_hit_at,
        "runner_breakeven_hit": runner_be_hit,
        "post_entry_high": round(post_high, 2),
        "post_entry_low": round(post_low, 2),
        "end_price": round(last_close, 2),
        "max_favorable_excursion": f"+${mfe_dlr:.2f} (+{mfe_pct:.2f}%)",
        "max_adverse_excursion": f"-${mae_dlr:.2f} (-{mae_pct:.2f}%)",
        "realized_pnl_per_share": round(realized_pnl, 2),
        "realized_r_multiple": f"{realized_pnl / risk:+.2f}R",
        "summary": summary,
    }


def analyze(ticker: str, target_date: Optional[str] = None) -> dict:
    import yfinance as yf

    clean_ticker = (ticker or "SPY").strip().upper()
    yf_sym = _yf_symbol(clean_ticker)
    req_date = parse_pulse_date(target_date) if isinstance(target_date, str) else target_date
    period = "2y" if req_date else "60d"
    daily = yf.Ticker(yf_sym).history(period=period, interval="1d", auto_adjust=True)
    if req_date and not daily.empty:
        resolved_date, _, _ = _resolve_trading_session(daily, req_date)
        return analyze_from_daily(clean_ticker, daily, scan_date=resolved_date)
    return analyze_from_daily(clean_ticker, daily)


def get_valid_outcome_and_trade_idea(
    ticker: str = "SPY",
    current_price: Optional[float] = None,
    target_date: Optional[str] = None,
) -> dict:
    """
    PULSE (Day Trading V4 Engine):
    Evaluates the 8:30–8:35 AM CT first 5-minute opening candle for any ticker (SPY, SPXW, QQQ, TSLA, etc.)
    on today's session OR any historical `target_date` (e.g. '10/12/2026') against prior day/week key levels
    (PDH, PDL, PWH, PWL, Mid) to determine the SINGLE VALID OUTCOME and performs a full intraday backtest!
    """
    import yfinance as yf
    from datetime import timedelta

    sym = (ticker or "SPY").strip().upper()
    yf_sym = _yf_symbol(sym)
    req_date = parse_pulse_date(target_date) if isinstance(target_date, str) else target_date

    period = "2y" if req_date else "60d"
    daily_raw = yf.Ticker(yf_sym).history(period=period, interval="1d", auto_adjust=True)
    daily = _norm_daily(daily_raw)
    if daily.empty:
        return {"ticker": sym, "error": "missing daily bars"}

    import pytz
    cst_tz = pytz.timezone("America/Chicago")
    now_cst = datetime.now(cst_tz)
    today_date = now_cst.date()
    is_today_weekday = now_cst.weekday() < 5
    t_805 = now_cst.replace(hour=8, minute=5, second=0, microsecond=0)
    t_835 = now_cst.replace(hour=8, minute=35, second=0, microsecond=0)

    # Detect if user requested today (or live pulse) on a weekday before the 8:35 AM CST opening candle confirmation
    is_premarket_today = (req_date is None or req_date == today_date) and is_today_weekday and (now_cst < t_835)

    sec_805 = max(0, int((t_805 - now_cst).total_seconds())) if is_premarket_today else 0
    sec_835 = max(0, int((t_835 - now_cst).total_seconds())) if is_premarket_today else 0

    def _fmt_pulse_timer(s: int) -> str:
        h = s // 3600
        m = (s % 3600) // 60
        sec = s % 60
        if h > 0:
            return f"{h:02d}h {m:02d}m {sec:02d}s"
        return f"{m:02d}m {sec:02d}s"

    timer_805_str = _fmt_pulse_timer(sec_805)
    timer_835_str = _fmt_pulse_timer(sec_835)

    if req_date and req_date != today_date:
        scan_date, completed, session_row = _resolve_trading_session(daily, req_date)
    elif is_premarket_today:
        scan_date = today_date
        completed = daily[[_idx_date(i) < today_date for i in daily.index]]
        session_row = None
    else:
        idx_dates = [_idx_date(i) for i in daily.index]
        weekday_dates = [d for d in idx_dates if d.weekday() < 5]
        scan_date = weekday_dates[-1] if weekday_dates else _roll_to_last_weekday(idx_dates[-1])
        completed, session_row = _split_session(daily, scan_date)

    if len(completed) < 5:
        return {"ticker": sym, "error": f"Insufficient historical bars prior to {scan_date}"}

    v4_data = analyze_from_daily(sym, daily, scan_date=scan_date, current_price=current_price)
    if v4_data.get("error"):
        return {"ticker": sym, "error": v4_data["error"]}

    price = float(current_price) if current_price and current_price > 0 else float(v4_data.get("price", 0))
    session_high = float(v4_data.get("session_high") or price)
    session_low = float(v4_data.get("session_low") or price)
    daily_open = float(session_row["open"]) if session_row is not None else price
    daily_high = float(session_row["high"]) if session_row is not None else session_high
    daily_low = float(session_row["low"]) if session_row is not None else session_low
    daily_close = float(session_row["close"]) if session_row is not None else price

    # Inspect intraday 5-minute bars on `scan_date` to capture the 8:30–8:35 AM CT (09:30–09:35 ET) opening candle
    open_5m = daily_open
    close_5m = price
    latest_price = daily_close
    post_entry_bars = pd.DataFrame()
    candle_source = "Daily Session Bar (Historical >60d fallback)"

    premarket_open = None
    premarket_high = None
    premarket_low = None
    premarket_price = None
    premarket_change = None
    premarket_change_pct = None
    prior_close = float(completed.iloc[-1]["close"]) if len(completed) > 0 else price

    if is_premarket_today:
        try:
            # Fetch 5m bars with prepost=True to obtain live pre-market prices (available from 4:00 AM ET / 3:00 AM CT)
            pm_bars = yf.Ticker(yf_sym).history(period="1d", interval="5m", prepost=True, auto_adjust=True)
            if pm_bars is not None and not pm_bars.empty:
                today_pm = pm_bars[[_idx_date(i) == today_date for i in pm_bars.index]]
                if not today_pm.empty:
                    premarket_open = round(float(today_pm["Open"].iloc[0]), 2)
                    premarket_high = round(float(today_pm["High"].max()), 2)
                    premarket_low = round(float(today_pm["Low"].min()), 2)
                    premarket_price = round(float(today_pm["Close"].iloc[-1]), 2)
                    price = premarket_price
                    session_high = premarket_high
                    session_low = premarket_low
                    open_5m = premarket_open
                    close_5m = premarket_price
                    candle_source = f"Live Pre-Market 5m Data ({now_cst.strftime('%I:%M %p CT')})"
                    if prior_close > 0:
                        premarket_change = round(price - prior_close, 2)
                        premarket_change_pct = round((premarket_change / prior_close) * 100, 2)
        except Exception:
            pass
    else:
        try:
            days_ago = (date.today() - scan_date).days
            if days_ago <= 58:
                if req_date:
                    start_str = scan_date.isoformat()
                    end_str = (scan_date + timedelta(days=2)).isoformat()
                    intraday = yf.Ticker(yf_sym).history(start=start_str, end=end_str, interval="5m", auto_adjust=True)
                else:
                    intraday = yf.Ticker(yf_sym).history(period="5d", interval="5m", auto_adjust=True)
                if not intraday.empty:
                    day_bars = intraday[[_idx_date(i) == scan_date for i in intraday.index]]
                    if not day_bars.empty:
                        first_bar = day_bars.iloc[0]
                        last_bar = day_bars.iloc[-1]
                        open_5m = float(first_bar["Open"])
                        close_5m = float(first_bar["Close"])
                        high_5m = float(first_bar["High"])
                        low_5m = float(first_bar["Low"])
                        latest_price = round(float(last_bar["Close"]), 2)
                        post_entry_bars = day_bars.iloc[1:]
                        candle_source = f"8:30–8:35 AM CT 5m Opening Candle ({scan_date.isoformat()})"
                        if current_price is None or current_price <= 0:
                            price = round(close_5m, 2)
                            session_high = round(high_5m, 2)
                            session_low = round(low_5m, 2)
            elif days_ago <= 720:
                start_str = scan_date.isoformat()
                end_str = (scan_date + timedelta(days=2)).isoformat()
                intraday_1h = yf.Ticker(yf_sym).history(start=start_str, end=end_str, interval="1h", auto_adjust=True)
                if not intraday_1h.empty:
                    day_bars = intraday_1h[[_idx_date(i) == scan_date for i in intraday_1h.index]]
                    if not day_bars.empty:
                        first_bar = day_bars.iloc[0]
                        last_bar = day_bars.iloc[-1]
                        open_5m = float(first_bar["Open"])
                        close_5m = float(first_bar["Close"])
                        high_5m = float(first_bar["High"])
                        low_5m = float(first_bar["Low"])
                        latest_price = round(float(last_bar["Close"]), 2)
                        post_entry_bars = day_bars.iloc[1:]
                        candle_source = f"Opening 1h Candle ({scan_date.isoformat()} — >60d lookback)"
                        if current_price is None or current_price <= 0:
                            price = round(close_5m, 2)
                            session_high = round(high_5m, 2)
                            session_low = round(low_5m, 2)
        except Exception:
            pass

    levels = v4_data.get("levels", {})
    pdh = float(levels.get("pdh", 0))
    pdl = float(levels.get("pdl", 0))
    pwh = float(levels.get("pwh", 0))
    pwl = float(levels.get("pwl", 0))
    pd_mid = float(levels.get("pd_mid", 0))
    atr = float(levels.get("atr", 5.0))
    buf = round(atr * 0.15, 2)

    candle_bullish = close_5m >= open_5m

    # 4-Hour Market Structure (4H: LL LH HH HL) comparing session high/low vs Previous Day High/Low
    eval_high = max(daily_high, session_high)
    eval_low = min(daily_low, session_low) if session_low > 0 else (daily_low if daily_low > 0 else session_low)

    # 1. Sweep & Reject PDH -> Single Decisive Outcome: BUY PUTS
    if session_high > pdh and pdl < price < pdh:
        outcome_label = "Sweep & Reject PDH (Bearish Rejection Confirmed)"
        action = "BUY PUTS"
        bias = "Bearish — Failed Breakout Above PDH"
        suggested_strike = _suggest_option_strike(sym, price, "PUTS")
        entry = round(price, 2)
        stop = round(max(session_high, pdh) + buf * 0.5, 2)
        t1 = round(pd_mid if pd_mid < entry - atr * 0.15 else pdl, 2)
        t2 = round(pdl if pdl < t1 - atr * 0.15 else entry - atr, 2)
        trigger = f"Confirmed: Swept PDH (${pdh:.2f} -> high ${session_high:.2f}) and rejected below PDH at ${price:.2f}"
        invalidation = f"5m candle close above sweep high (${stop:.2f})"
        rationale = (
            f"{sym} swept above PDH (${pdh:.2f}) to ${session_high:.2f} on the open and rejected back below to ${price:.2f}, "
            f"confirming the 8:00 AM Sweep & Reject PDH bearish setup targeting ${t1:.2f} and ${t2:.2f}."
        )

    # 2. Breakout & Hold Above PDH -> Single Decisive Outcome: BUY CALLS
    elif price >= pdh:
        outcome_label = "Breakout & Hold Above PDH (Bullish Breakout Confirmed)"
        action = "BUY CALLS"
        bias = "Bullish Continuation Above PDH"
        suggested_strike = _suggest_option_strike(sym, price, "CALLS")
        entry = round(price, 2)
        stop = round(pdh - buf, 2)
        t1 = round(max(pwh, entry + atr * 0.5), 2)
        t2 = round(max(t1 + atr * 0.5, entry + atr), 2)
        trigger = f"Confirmed: Holding above PDH (${pdh:.2f}) at ${price:.2f}"
        invalidation = f"Break back below PDH stop at ${stop:.2f}"
        rationale = (
            f"{sym} broke and held above PDH (${pdh:.2f}) after the opening 5m candle, "
            f"confirming the 8:00 AM Bullish Breakout outcome toward ${t1:.2f}."
        )

    # 3. Sweep & Reclaim PDL -> Single Decisive Outcome: BUY CALLS
    elif session_low < pdl and pdl < price < pdh:
        outcome_label = "Sweep & Reclaim PDL (Bullish Reclaim Confirmed)"
        action = "BUY CALLS"
        bias = "Bullish — Bear Trap Reclaim Above PDL"
        suggested_strike = _suggest_option_strike(sym, price, "CALLS")
        entry = round(price, 2)
        stop = round(min(session_low, pdl) - buf * 0.5, 2)
        t1 = round(pd_mid if pd_mid > entry + atr * 0.15 else pdh, 2)
        t2 = round(pdh if pdh > t1 + atr * 0.15 else entry + atr, 2)
        trigger = f"Confirmed: Swept PDL (${pdl:.2f} -> low ${session_low:.2f}) and reclaimed above PDL at ${price:.2f}"
        invalidation = f"5m candle close below sweep low (${stop:.2f})"
        rationale = (
            f"{sym} dipped below PDL (${pdl:.2f}) to ${session_low:.2f} and reclaimed back above to ${price:.2f}, "
            f"confirming the 8:00 AM Sweep & Reclaim PDL bullish setup targeting ${t1:.2f} and ${t2:.2f}."
        )

    # 4. Breakdown & Hold Below PDL -> Single Decisive Outcome: BUY PUTS
    elif price <= pdl:
        outcome_label = "Breakdown & Hold Below PDL (Bearish Breakdown Confirmed)"
        action = "BUY PUTS"
        bias = "Bearish Continuation Below PDL"
        suggested_strike = _suggest_option_strike(sym, price, "PUTS")
        entry = round(price, 2)
        stop = round(pdl + buf, 2)
        t1 = round(min(pwl if pwl < entry - atr * 0.15 else entry - atr * 0.5, entry - atr * 0.5), 2)
        t2 = round(min(t1 - atr * 0.5, entry - atr), 2)
        trigger = f"Confirmed: Trading below PDL (${pdl:.2f}) at ${price:.2f}"
        invalidation = f"Reclaim back above PDL stop at ${stop:.2f}"
        rationale = (
            f"{sym} broke and held below PDL (${pdl:.2f}) after the opening 5m candle, "
            f"confirming the 8:00 AM Bearish Breakdown outcome toward ${t1:.2f} and ${t2:.2f}."
        )

    # 5. Inside PDL–PDH Range -> Resolve to ONE decisive outcome using 5m candle direction & range location
    else:
        if price >= pd_mid:
            if not candle_bullish or (session_high >= pdh - atr * 0.25 and price < session_high - atr * 0.08):
                outcome_label = "Upper-Range Rejection Below PDH (Bearish Fade to Mid)"
                action = "BUY PUTS"
                bias = "Bearish Rejection off Upper Range / PDH Zone"
                suggested_strike = _suggest_option_strike(sym, price, "PUTS")
                entry = round(price, 2)
                stop = round(pdh + buf * 0.5, 2)
                t1 = round(pd_mid, 2)
                t2 = round(pdl, 2)
                trigger = f"Confirmed: Bearish 5m rejection below PDH (${pdh:.2f}) at ${price:.2f}"
                invalidation = f"Breakout above PDH stop at ${stop:.2f}"
                rationale = (
                    f"{sym} tested the upper range near PDH (${pdh:.2f}, high ${session_high:.2f}) and sellers stepped in "
                    f"(open ${open_5m:.2f} -> ${price:.2f}), favoring PUTS toward Midpoint (${pd_mid:.2f}) and PDL (${pdl:.2f})."
                )
            else:
                outcome_label = "Bullish Hold Above Midpoint Targeting PDH"
                action = "BUY CALLS"
                bias = "Bullish Continuation Above Midpoint"
                suggested_strike = _suggest_option_strike(sym, price, "CALLS")
                entry = round(price, 2)
                stop = round(pd_mid - buf * 0.5, 2)
                t1 = round(pdh, 2)
                t2 = round(max(pwh, pdh + atr * 0.5), 2)
                trigger = f"Confirmed: Holding above Midpoint (${pd_mid:.2f}) with bullish 5m candle at ${price:.2f}"
                invalidation = f"Break below Midpoint stop at ${stop:.2f}"
                rationale = (
                    f"{sym} held above Midpoint (${pd_mid:.2f}) on the opening 5m candle (${open_5m:.2f} -> ${price:.2f}), "
                    f"favoring CALLS targeting PDH (${pdh:.2f}) and PWH (${pwh:.2f})."
                )
        else:
            if candle_bullish or (session_low <= pdl + atr * 0.25 and price > session_low + atr * 0.08):
                outcome_label = "Lower-Range Bounce Above PDL Support Targeting Mid"
                action = "BUY CALLS"
                bias = "Bullish Bounce off Lower Range / PDL Zone"
                suggested_strike = _suggest_option_strike(sym, price, "CALLS")
                entry = round(price, 2)
                stop = round(pdl - buf * 0.5, 2)
                t1 = round(pd_mid, 2)
                t2 = round(pdh, 2)
                trigger = f"Confirmed: Bullish 5m defense above PDL (${pdl:.2f}) at ${price:.2f}"
                invalidation = f"Breakdown below PDL stop at ${stop:.2f}"
                rationale = (
                    f"{sym} defended the lower range above PDL (${pdl:.2f}, low ${session_low:.2f}) with buyers stepping in "
                    f"(${open_5m:.2f} -> ${price:.2f}), favoring CALLS toward Midpoint (${pd_mid:.2f}) and PDH (${pdh:.2f})."
                )
            else:
                outcome_label = "Bearish Breakdown Below Midpoint Targeting PDL"
                action = "BUY PUTS"
                bias = "Bearish Continuation Below Midpoint"
                suggested_strike = _suggest_option_strike(sym, price, "PUTS")
                entry = round(price, 2)
                stop = round(pd_mid + buf * 0.5, 2)
                t1 = round(pdl, 2)
                t2 = round(min(pwl, pdl - atr * 0.5), 2)
                trigger = f"Confirmed: Trading below Midpoint (${pd_mid:.2f}) with bearish 5m candle at ${price:.2f}"
                invalidation = f"Reclaim above Midpoint stop at ${stop:.2f}"
                rationale = (
                    f"{sym} rejected below Midpoint (${pd_mid:.2f}) on the opening 5m candle (${open_5m:.2f} -> ${price:.2f}), "
                    f"favoring PUTS targeting PDL (${pdl:.2f}) and PWL (${pwl:.2f})."
                )

    # Enforce strict directional consistency on Stop, T1, and T2 relative to Entry
    if "PUTS" in action:
        if t1 >= entry - atr * 0.12:
            t1 = round(entry - atr * 0.5, 2)
        if t2 >= t1 - atr * 0.12:
            t2 = round(t1 - atr * 0.5, 2)
        if stop <= entry or (stop - entry) > atr * 0.75:
            stop = round(max(pdl + buf, entry + atr * 0.3) if price <= pdl else entry + atr * 0.35, 2)
    else:
        if t1 <= entry + atr * 0.12:
            t1 = round(entry + atr * 0.5, 2)
        if t2 <= t1 + atr * 0.12:
            t2 = round(t1 + atr * 0.5, 2)
        if stop >= entry or (entry - stop) > atr * 0.75:
            stop = round(min(pdh - buf, entry - atr * 0.3) if price >= pdh else entry - atr * 0.35, 2)

    # Calculate Risk / Reward
    risk = abs(entry - stop) if entry != stop else 1.0
    reward = abs(t1 - entry)
    rr = round(reward / risk, 1) if risk > 0 else 2.0

    is_waiting_4h = is_premarket_today and (now_cst < t_805)
    live_4h_tag, live_4h_desc, live_4h_stats = _compute_4h_structure(yf_sym, today_date if is_premarket_today else scan_date, daily, use_prepost=True)
    live_arr, live_col, live_badge = get_4h_structure_arrow(live_4h_tag)
    four_hour_focus = get_4h_narrowed_focus(live_4h_tag, levels, price)

    if is_premarket_today:
        if is_waiting_4h:
            d_tag = f"4H (⏱️ Timer left for 8:05 AM CST: {timer_805_str})"
            d_detail = f"Pre-market: 4H Market Structure tracking {live_badge} · Alert locks at 8:05 AM CST (⏱️ Timer left: {timer_805_str})"
            d_h4_stats = {**live_4h_stats, "timer_left_805": timer_805_str, "status": "waiting"}
        else:
            d_tag, d_detail, d_h4_stats = live_4h_tag, live_4h_desc, live_4h_stats

        outcome_label = f"Waiting for 8:35 AM CST Confirmation (⏱️ Timer Left for 8:35 AM: {timer_835_str})"
        action = f"WAITING (⏱️ Timer Left for 8:35 AM: {timer_835_str})"
        valid_outcome = f"WAITING [{d_tag}] · (⏱️ Timer Left for 8:35 AM: {timer_835_str})"
        bias = f"Pre-Market Watch · Waiting for 8:35 AM CST Validation ({timer_835_str} remaining)"
        suggested_strike = "Pending 8:35 AM Open Validation"
        entry = round(price, 2)
        stop = round(pdl - buf * 0.5, 2)
        t1 = round(pd_mid, 2)
        t2 = round(pdh, 2)
        trigger = f"Pending 8:35 AM CST 5-Minute Opening Candle Confirmation (⏱️ Timer Left: {timer_835_str})"
        invalidation = f"Reversal beyond line in the sand stop (${stop:.2f})"
        rationale = (
            f"{sym} is currently in Pre-Market session today ({now_cst.strftime('%A, %b %d')}). "
            f"4H structure evaluates at 8:05 AM CST ({'⏱️ Timer left: ' + timer_805_str if is_waiting_4h else '4H Confirmed: ' + live_badge}), "
            f"and recommendation is in waiting state until 8:35 AM CST (⏱️ Timer Left: {timer_835_str}) "
            f"when the first 5-minute opening candle confirms. Key levels established: PDH ${pdh:.2f}, PDL ${pdl:.2f}, Mid ${pd_mid:.2f}."
        )
        backtest = {
            "result_badge": "⏱️ PENDING 8:35 AM",
            "summary": f"Waiting for market open (8:30 AM CST) and 5m confirmation (8:35 AM CST). Timer left: {timer_835_str}",
            "max_favorable_excursion": "0.0%",
            "max_adverse_excursion": "0.0%",
            "one_hour": {
                "result_badge": "⏱️ PENDING 8:35 AM",
                "summary": f"Waiting for market open (8:30 AM CST) and 5m confirmation (8:35 AM CST). Timer left: {timer_835_str}",
                "max_favorable_excursion": "0.0%",
                "max_adverse_excursion": "0.0%",
            },
            "full_day": {
                "result_badge": "⏱️ PENDING 8:35 AM",
                "summary": f"Waiting for market open (8:30 AM CST) and 5m confirmation (8:35 AM CST). Timer left: {timer_835_str}",
                "max_favorable_excursion": "0.0%",
                "max_adverse_excursion": "0.0%",
            },
            "session_window": "1h",
        }
    else:
        # If scan_date is today, preserve the live 8:05 AM 4H structure lock
        if scan_date == today_date:
            d_tag, d_detail, d_h4_stats = live_4h_tag, live_4h_desc, live_4h_stats
        else:
            # Historical scan date: 4-Hour Market Structure matching ThinkorSwim
            d_tag, d_detail, d_h4_stats = _compute_4h_structure(yf_sym, scan_date, daily, use_prepost=True)
            live_4h_tag = d_tag
            live_arr, live_col, live_badge = get_4h_structure_arrow(d_tag)
            four_hour_focus = get_4h_narrowed_focus(d_tag, levels, price)

        # Slice 1-Hour post-entry window (first 60 minutes after 8:35 AM CT entry)
        one_hour_bars = pd.DataFrame()
        if post_entry_bars is not None and not post_entry_bars.empty:
            try:
                first_ts = pd.Timestamp(post_entry_bars.index[0])
                limit_ts = first_ts + pd.Timedelta(minutes=60)
                one_hour_bars = post_entry_bars[post_entry_bars.index <= limit_ts]
                if one_hour_bars.empty:
                    one_hour_bars = post_entry_bars.iloc[:12]
            except Exception:
                one_hour_bars = post_entry_bars.iloc[:12]

        # Full-Day Intraday Backtest from 8:35 AM CT through session close
        backtest_full = _simulate_trade_backtest(
            post_entry_bars,
            entry=entry,
            stop=stop,
            t1=t1,
            t2=t2,
            action=action,
            timeframe_label=f"Full-Day Session ({scan_date.isoformat()} · Post-8:35 AM CT to Close)",
            fallback_high=daily_high,
            fallback_low=daily_low,
            fallback_close=daily_close,
            structure_tag=d_tag,
        )

        # 1-Hour Intraday Backtest (first 60 minutes: 8:35 AM – 9:35 AM CT)
        backtest_1h = _simulate_trade_backtest(
            one_hour_bars if not one_hour_bars.empty else post_entry_bars.iloc[:12],
            entry=entry,
            stop=stop,
            t1=t1,
            t2=t2,
            action=action,
            timeframe_label=f"1-Hour Session ({scan_date.isoformat()} · 8:35 AM – 9:35 AM CT)",
            fallback_high=daily_high,
            fallback_low=daily_low,
            fallback_close=daily_close,
            structure_tag=d_tag,
        )

        for bt in (backtest_full, backtest_1h):
            bt["four_hour_structure"] = d_tag
            bt["four_hour_structure_detail"] = d_detail
            bt["four_hour_arrow"] = live_arr
            bt["four_hour_badge"] = live_badge
            bt["daily_structure"] = d_tag
            bt["daily_structure_detail"] = d_detail
            bt["structure_tag"] = d_tag

        # Default backtest to 1-Hour Session as requested, while providing both one_hour and full_day
        backtest = dict(backtest_1h)
        backtest["one_hour"] = backtest_1h
        backtest["full_day"] = backtest_full
        backtest["session_window"] = "1h"
    backtest["four_hour_structure"] = d_tag
    backtest["four_hour_structure_detail"] = d_detail
    backtest["four_hour_arrow"] = live_arr
    backtest["four_hour_badge"] = live_badge
    backtest["daily_structure"] = d_tag
    backtest["daily_structure_detail"] = d_detail
    backtest["structure_tag"] = d_tag

    contract_opts = _build_pulse_contract_options(
        ticker=sym,
        price=entry,
        action=action,
        t1=t1,
        t2=t2,
        atr=atr,
        horizon="pulse",
    )

    as_of_str, session_day_name, is_weekend = _format_session_as_of(scan_date, req_date)

    if isinstance(four_hour_focus, dict):
        four_hour_focus = dict(four_hour_focus)
        if stop is not None:
            four_hour_focus["stop_loss"] = stop
            four_hour_focus["stop_loss_str"] = f"${stop:.2f}"
            if "PDL" in str(four_hour_focus.get("badge", "")) and pdl is not None:
                four_hour_focus["level_name"] = "PDL"
                four_hour_focus["level_value"] = pdl
                four_hour_focus["badge_with_levels"] = f"{four_hour_focus.get('badge')} (PDL: ${pdl:.2f} | SL: ${stop:.2f})"
                four_hour_focus["short_badge"] = f"{four_hour_focus.get('arrow', '')} {four_hour_focus.get('focus_type', '')} (PDL ${pdl:.2f} | SL ${stop:.2f})".strip()
            elif "PDH" in str(four_hour_focus.get("badge", "")) and pdh is not None:
                four_hour_focus["level_name"] = "PDH"
                four_hour_focus["level_value"] = pdh
                four_hour_focus["badge_with_levels"] = f"{four_hour_focus.get('badge')} (PDH: ${pdh:.2f} | SL: ${stop:.2f})"
                four_hour_focus["short_badge"] = f"{four_hour_focus.get('arrow', '')} {four_hour_focus.get('focus_type', '')} (PDH ${pdh:.2f} | SL ${stop:.2f})".strip()
            else:
                four_hour_focus["badge_with_levels"] = f"{four_hour_focus.get('badge')} (SL: ${stop:.2f})"
                four_hour_focus["short_badge"] = f"{four_hour_focus.get('arrow', '')} {four_hour_focus.get('focus_type', '')} (SL ${stop:.2f})".strip()

    return {
        "strategy_name": "Pulse (Day Trading 8:35 AM Open Validator)",
        "ticker": sym,
        "yf_symbol": yf_sym,
        "scan_date": scan_date.isoformat(),
        "session_day_name": f"Today · {now_cst.strftime('%A, %b %d')} (Pre-Market)" if is_premarket_today else session_day_name,
        "is_premarket_waiting": is_premarket_today,
        "is_waiting_4h": is_premarket_today and is_waiting_4h,
        "is_waiting_recommendation": is_premarket_today,
        "seconds_left_805": sec_805,
        "seconds_left_835": sec_835,
        "timer_left_805": timer_805_str if is_premarket_today else "",
        "timer_left_835": timer_835_str if is_premarket_today else "",
        "target_time_805_iso": t_805.isoformat() if is_premarket_today else None,
        "target_time_835_iso": t_835.isoformat() if is_premarket_today else None,
        "is_weekend_or_closed": is_weekend,
        "requested_date": req_date.isoformat() if req_date else None,
        "candle_source": candle_source,
        "price": price,
        "latest_session_close": round(prior_close if is_premarket_today else latest_price, 2),
        "session_high": round(session_high, 2),
        "session_low": round(session_low, 2),
        "premarket_price": premarket_price,
        "premarket_high": premarket_high,
        "premarket_low": premarket_low,
        "premarket_change": premarket_change,
        "premarket_change_pct": premarket_change_pct,
        "is_premarket_live": bool(premarket_price is not None),
        "valid_outcome": f"[{d_tag}] {outcome_label}",
        "raw_outcome": outcome_label,
        "action": action,
        "structure_tag": d_tag,
        "four_hour_structure": d_tag,
        "four_hour_structure_detail": d_detail,
        "four_hour_arrow": live_arr,
        "four_hour_badge": live_badge,
        "four_hour_color": live_col,
        "live_4h_tag": live_4h_tag,
        "live_4h_badge": live_badge,
        "live_4h_arrow": live_arr,
        "four_hour_focus": four_hour_focus,
        "four_hour_confluence": check_4h_confluence(four_hour_focus, outcome_label, action),
        "daily_structure": d_tag,
        "daily_structure_detail": d_detail,
        "trade_idea": {
            "action": action,
            "bias": f"{bias} · {d_tag}",
            "structure_tag": d_tag,
            "four_hour_structure": d_tag,
            "four_hour_structure_detail": d_detail,
            "four_hour_arrow": live_arr,
            "four_hour_badge": live_badge,
            "four_hour_focus": four_hour_focus,
            "four_hour_confluence": check_4h_confluence(four_hour_focus, outcome_label, action),
            "daily_structure": d_tag,
            "daily_structure_detail": d_detail,
            "suggested_contract": suggested_strike,
            "open_contract": contract_opts["open_contract"],
            "cost_efficient_otm": contract_opts["cost_efficient_otm"],
            "cost_efficient_spread": contract_opts["cost_efficient_spread"],
            "cost_efficient_butterfly": contract_opts["cost_efficient_butterfly"],
            "cost_efficient_summary": contract_opts["cost_efficient_summary"],
            "cost_efficient_contracts": contract_opts["cost_efficient_contracts"],
            "expiry_type": "0DTE or Next Expiry (Delta 0.40 - 0.50)",
            "entry_level": entry,
            "stop_loss": stop,
            "target_1": t1,
            "target_2": t2,
            "reward_risk": f"{rr}x",
            "trigger_condition": trigger,
            "invalidation_rule": invalidation,
            "rationale": f"[{d_tag}] {rationale}",
            "execution_strategy": "Scale 50% profit at Target 1, immediately move stop to breakeven, and let runner trail for Target 2."
        },
        "key_levels": {
            "pdh": round(pdh, 2),
            "pdl": round(pdl, 2),
            "pwh": round(pwh, 2),
            "pwl": round(pwl, 2),
            "pd_mid": round(pd_mid, 2),
            "atr": round(atr, 2),
            "structure_tag": d_tag,
            "four_hour_structure": d_tag,
            "four_hour_structure_detail": d_detail,
            "daily_structure": d_tag,
            "daily_structure_detail": d_detail,
            "four_hour_stats": d_h4_stats,
        },
        "backtest": backtest,
        "morning_signal": v4_data.get("signal", {}),
        "as_of": as_of_str,
    }


def get_long_term_pulse(ticker: str = "SPY", target_date: Optional[str] = None) -> dict:
    """
    LONG TERM PULSE (Month & Day High/Low Strategy):
    Evaluates price action against Previous Month High/Low (PMH / PML) AND Previous Day High/Low (PDH / PDL)
    on today's session or any historical `target_date`, returning a multi-week swing / options trade plan
    plus a forward multi-day backtest (up to 30 trading days after `scan_date`).
    """
    import yfinance as yf

    sym = (ticker or "SPY").strip().upper()
    yf_sym = _yf_symbol(sym)
    req_date = parse_pulse_date(target_date) if isinstance(target_date, str) else target_date

    daily_raw = yf.Ticker(yf_sym).history(period="2y", interval="1d", auto_adjust=True)
    daily = _norm_daily(daily_raw)
    if daily.empty:
        return {"ticker": sym, "error": "missing daily bars"}

    idx_dates = [_idx_date(i) for i in daily.index]
    if req_date:
        scan_date, completed, session_row = _resolve_trading_session(daily, req_date)
    else:
        weekday_dates = [d for d in idx_dates if d.weekday() < 5]
        scan_date = weekday_dates[-1] if weekday_dates else _roll_to_last_weekday(idx_dates[-1])
        completed, session_row = _split_session(daily, scan_date)

    if len(completed) < 22:
        return {"ticker": sym, "error": f"Insufficient historical bars prior to {scan_date} for Long Term Pulse"}

    # Previous Day High / Low (PDH / PDL)
    prev_day = completed.iloc[-1]
    pdh = round(float(prev_day["high"]), 2)
    pdl = round(float(prev_day["low"]), 2)
    pd_mid = round((pdh + pdl) / 2.0, 2)
    pd_close = round(float(prev_day["close"]), 2)

    # Previous Month High / Low (PMH / PML) — check prior calendar month if >=15 bars, else prior 21 trading bars
    comp_dates = [_idx_date(i) for i in completed.index]
    prev_month_year = (scan_date.year, scan_date.month - 1) if scan_date.month > 1 else (scan_date.year - 1, 12)
    cal_month_df = completed[[(d.year, d.month) == prev_month_year for d in comp_dates]]
    month_df = cal_month_df if len(cal_month_df) >= 12 else completed.tail(21)

    pmh = round(float(month_df["high"].max()), 2)
    pml = round(float(month_df["low"].min()), 2)
    pm_mid = round((pmh + pml) / 2.0, 2)
    pm_range = round(max(pmh - pml, 0.5), 2)
    atr = round(_atr(completed, 14), 2)
    buf = round(max(atr * 0.25, pm_range * 0.04), 2)

    if session_row is not None:
        open_px = round(float(session_row["open"]), 2)
        high_px = round(float(session_row["high"]), 2)
        low_px = round(float(session_row["low"]), 2)
        price = round(float(session_row["close"]), 2)
    else:
        open_px = pd_close
        high_px = pdh
        low_px = pdl
        price = pd_close

    # Determine Long Term Pulse Validated Outcome from Month (PMH/PML) + Day (PDH/PDL) Highs & Lows
    if high_px > pmh and price < pmh:
        outcome_label = "Monthly High Sweep & Daily Rejection Below PMH (Bearish Multi-Week Reversal)"
        action = "BUY PUTS"
        bias = "Bearish — Failed Breakout Above Previous Month High (PMH)"
        strike = _suggest_option_strike(sym, price, "PUTS")
        entry = price
        stop = round(max(high_px, pmh) + buf, 2)
        t1 = round(pm_mid if pm_mid < entry - atr * 0.5 else pdl, 2)
        t2 = round(pml if pml < t1 - atr * 0.5 else entry - pm_range * 0.6, 2)
        trigger = f"Swept PMH (${pmh:.2f} -> high ${high_px:.2f}) and closed back below PMH at ${price:.2f}"
        invalidation = f"Daily close above monthly sweep high (${stop:.2f})"
        rationale = (
            f"{sym} swept above Previous Month High (${pmh:.2f}) to ${high_px:.2f} and rejected back below to ${price:.2f}, "
            f"signaling institutional supply at monthly resistance targeting Monthly Mid (${t1:.2f}) and PML (${t2:.2f})."
        )
    elif price >= pmh and price >= pdh:
        outcome_label = "Monthly & Daily Breakout Above PMH & PDH (Bullish Multi-Week Expansion)"
        action = "BUY CALLS"
        bias = "Bullish — Confirmed Breakout Above Both Monthly (PMH) & Daily (PDH) Highs"
        strike = _suggest_option_strike(sym, price, "CALLS")
        entry = price
        stop = round(min(pmh, pdh) - buf, 2)
        t1 = round(max(entry + atr * 1.5, pmh + pm_range * 0.382), 2)
        t2 = round(max(t1 + atr * 1.5, pmh + pm_range * 0.618), 2)
        trigger = f"Holding above both PMH (${pmh:.2f}) and PDH (${pdh:.2f}) at ${price:.2f}"
        invalidation = f"Daily close back below PMH/PDH support stop (${stop:.2f})"
        rationale = (
            f"{sym} broke and held above both Previous Month High (${pmh:.2f}) and Previous Day High (${pdh:.2f}), "
            f"unlocking multi-week trend expansion toward ${t1:.2f} and ${t2:.2f}."
        )
    elif low_px < pml and price > pml:
        outcome_label = "Monthly Low Sweep & Daily Reclaim Above PML (Bullish Multi-Week Reversal)"
        action = "BUY CALLS"
        bias = "Bullish — Liquidity Sweep & Reclaim Above Previous Month Low (PML)"
        strike = _suggest_option_strike(sym, price, "CALLS")
        entry = price
        stop = round(min(low_px, pml) - buf, 2)
        t1 = round(pm_mid if pm_mid > entry + atr * 0.5 else pdh, 2)
        t2 = round(pmh if pmh > t1 + atr * 0.5 else entry + pm_range * 0.6, 2)
        trigger = f"Swept PML (${pml:.2f} -> low ${low_px:.2f}) and reclaimed above PML at ${price:.2f}"
        invalidation = f"Daily close below monthly sweep low (${stop:.2f})"
        rationale = (
            f"{sym} undercut Previous Month Low (${pml:.2f}) to ${low_px:.2f} and reclaimed back above to ${price:.2f}, "
            f"confirming a multi-week bear-trap reversal toward Monthly Mid (${t1:.2f}) and PMH (${t2:.2f})."
        )
    elif price <= pml and price <= pdl:
        outcome_label = "Monthly & Daily Breakdown Below PML & PDL (Bearish Multi-Week Breakdown)"
        action = "BUY PUTS"
        bias = "Bearish — Confirmed Breakdown Below Both Monthly (PML) & Daily (PDL) Lows"
        strike = _suggest_option_strike(sym, price, "PUTS")
        entry = price
        stop = round(max(pml, pdl) + buf, 2)
        t1 = round(min(entry - atr * 1.5, pml - pm_range * 0.382), 2)
        t2 = round(min(t1 - atr * 1.5, pml - pm_range * 0.618), 2)
        trigger = f"Closed below both PML (${pml:.2f}) and PDL (${pdl:.2f}) at ${price:.2f}"
        invalidation = f"Daily close back above PML/PDL stop (${stop:.2f})"
        rationale = (
            f"{sym} broke down below both Previous Month Low (${pml:.2f}) and Previous Day Low (${pdl:.2f}), "
            f"confirming multi-week downside continuation toward ${t1:.2f} and ${t2:.2f}."
        )
    elif price >= pdh:
        outcome_label = "Daily Breakout Above PDH Inside Monthly Range Targeting PMH"
        action = "BUY CALLS"
        bias = "Bullish — Daily PDH Breakout Rotating Toward Monthly High (PMH)"
        strike = _suggest_option_strike(sym, price, "CALLS")
        entry = price
        stop = round(max(pdl, pdh - atr * 0.8), 2)
        t1 = round(pmh if pmh > entry + atr * 0.5 else entry + atr * 1.5, 2)
        t2 = round(max(t1 + atr * 1.2, pmh + pm_range * 0.382), 2)
        trigger = f"Daily breakout above PDH (${pdh:.2f}) inside monthly range [${pml:.2f} – ${pmh:.2f}]"
        invalidation = f"Break below daily support stop at ${stop:.2f}"
        rationale = (
            f"{sym} cleared Previous Day High (${pdh:.2f}) while rotating inside the monthly range, "
            f"targeting Previous Month High (${pmh:.2f}) and ${t2:.2f}."
        )
    elif price <= pdl:
        outcome_label = "Daily Breakdown Below PDL Inside Monthly Range Targeting PML"
        action = "BUY PUTS"
        bias = "Bearish — Daily PDL Breakdown Rotating Toward Monthly Low (PML)"
        strike = _suggest_option_strike(sym, price, "PUTS")
        entry = price
        stop = round(min(pdh, pdl + atr * 0.8), 2)
        t1 = round(pml if pml < entry - atr * 0.5 else entry - atr * 1.5, 2)
        t2 = round(min(t1 - atr * 1.2, pml - pm_range * 0.382), 2)
        trigger = f"Daily breakdown below PDL (${pdl:.2f}) inside monthly range [${pml:.2f} – ${pmh:.2f}]"
        invalidation = f"Reclaim above daily resistance stop at ${stop:.2f}"
        rationale = (
            f"{sym} broke below Previous Day Low (${pdl:.2f}) inside the monthly range, "
            f"targeting Previous Month Low (${pml:.2f}) and ${t2:.2f}."
        )
    elif price >= pm_mid:
        outcome_label = "Holding Upper Monthly Range (Above PM Mid) & Daily Support Targeting PMH"
        action = "BUY CALLS"
        bias = "Bullish — Upper Monthly Range Consolidation"
        strike = _suggest_option_strike(sym, price, "CALLS")
        entry = price
        stop = round(min(pdl - buf * 0.5, entry - atr * 0.9), 2)
        t1 = round(max(pdh, pmh), 2)
        t2 = round(max(t1 + atr * 1.2, pmh + pm_range * 0.25), 2)
        trigger = f"Holding above Monthly Midpoint (${pm_mid:.2f}) with daily support at PDL (${pdl:.2f})"
        invalidation = f"Close below PDL / monthly midpoint stop (${stop:.2f})"
        rationale = (
            f"{sym} is holding the upper half of the monthly range above PM Mid (${pm_mid:.2f}) and PDL (${pdl:.2f}), "
            f"favoring bullish rotation toward PMH (${pmh:.2f})."
        )
    else:
        outcome_label = "Trading in Lower Monthly Range (Below PM Mid) Below PDH Targeting PML"
        action = "BUY PUTS"
        bias = "Bearish — Lower Monthly Range Weakness"
        strike = _suggest_option_strike(sym, price, "PUTS")
        entry = price
        stop = round(max(pdh + buf * 0.5, entry + atr * 0.9), 2)
        t1 = round(min(pdl, pml), 2)
        t2 = round(min(t1 - atr * 1.2, pml - pm_range * 0.25), 2)
        trigger = f"Trading below Monthly Midpoint (${pm_mid:.2f}) and capped below PDH (${pdh:.2f})"
        invalidation = f"Reclaim above PDH / monthly midpoint stop (${stop:.2f})"
        rationale = (
            f"{sym} remains in the lower half of the monthly range below PM Mid (${pm_mid:.2f}) and PDH (${pdh:.2f}), "
            f"favoring downside rotation toward PML (${pml:.2f})."
        )

    # Directional safety guard
    if "PUTS" in action:
        if t1 >= entry - atr * 0.3:
            t1 = round(entry - atr * 1.2, 2)
        if t2 >= t1 - atr * 0.3:
            t2 = round(t1 - atr * 1.2, 2)
        if stop <= entry:
            stop = round(entry + atr * 0.85, 2)
    else:
        if t1 <= entry + atr * 0.3:
            t1 = round(entry + atr * 1.2, 2)
        if t2 <= t1 + atr * 0.3:
            t2 = round(t1 + atr * 1.2, 2)
        if stop >= entry:
            stop = round(entry - atr * 0.85, 2)

    risk = abs(entry - stop) if entry != stop else 1.0
    rr = round(abs(t1 - entry) / risk, 2)

    # Daily Market Structure (D: LH, D: LL, D: HH, D: HL) for Long Term Pulse
    d_tag, d_detail = _compute_market_structure(
        high_px, low_px, pdh, pdl, prefix="D", action=action, outcome=outcome_label
    )

    # Forward multi-day backtest across up to 30 trading days after scan_date
    future_daily = daily[[d > scan_date for d in idx_dates]].head(30)
    backtest = _simulate_trade_backtest(
        future_daily,
        entry=entry,
        stop=stop,
        t1=t1,
        t2=t2,
        action=action,
        timeframe_label=f"Multi-Day Forward Backtest (Up to 30 Trading Days After {scan_date.isoformat()})",
        structure_tag=d_tag,
    )
    backtest["daily_structure"] = d_tag
    backtest["daily_structure_detail"] = d_detail
    backtest["structure_tag"] = d_tag

    lt_contracts = _build_pulse_contract_options(
        ticker=sym,
        price=entry,
        action=action,
        t1=t1,
        t2=t2,
        atr=atr,
        horizon="long_term",
    )

    lt_as_of, lt_day_name, lt_weekend = _format_session_as_of(scan_date, req_date)

    return {
        "strategy_name": "Long Term Pulse (Month & Day High/Low Engine)",
        "ticker": sym,
        "scan_date": scan_date.isoformat(),
        "session_day_name": lt_day_name,
        "is_weekend_or_closed": lt_weekend,
        "requested_date": req_date.isoformat() if req_date else None,
        "price": price,
        "session_open": open_px,
        "session_high": high_px,
        "session_low": low_px,
        "valid_outcome": f"[{d_tag}] {outcome_label}",
        "raw_outcome": outcome_label,
        "structure_tag": d_tag,
        "daily_structure": d_tag,
        "daily_structure_detail": d_detail,
        "trade_idea": {
            "action": action,
            "bias": f"{bias} · {d_tag}",
            "structure_tag": d_tag,
            "daily_structure": d_tag,
            "daily_structure_detail": d_detail,
            "suggested_contract": f"{strike} (30–60 DTE)",
            "open_contract": lt_contracts["open_contract"],
            "cost_efficient_otm": lt_contracts["cost_efficient_otm"],
            "cost_efficient_spread": lt_contracts["cost_efficient_spread"],
            "cost_efficient_butterfly": lt_contracts["cost_efficient_butterfly"],
            "cost_efficient_summary": lt_contracts["cost_efficient_summary"],
            "cost_efficient_contracts": lt_contracts["cost_efficient_contracts"],
            "expiry_type": "30–60 DTE Monthly Options or Swing Shares (Delta 0.45 - 0.55)",
            "entry_level": entry,
            "stop_loss": stop,
            "target_1": t1,
            "target_2": t2,
            "reward_risk": f"{rr}x",
            "trigger_condition": trigger,
            "invalidation_rule": invalidation,
            "rationale": f"[{d_tag}] {rationale}",
            "execution_strategy": "Scale 50% at Target 1, move stop to breakeven, and trail runner toward Target 2.",
        },
        "key_levels": {
            "pmh": pmh,
            "pml": pml,
            "pm_mid": pm_mid,
            "pm_range": pm_range,
            "pdh": pdh,
            "pdl": pdl,
            "pd_mid": pd_mid,
            "atr_14d": atr,
            "structure_tag": d_tag,
            "daily_structure": d_tag,
            "daily_structure_detail": d_detail,
        },
        "backtest": backtest,
        "as_of": lt_as_of,
    }


def get_last_4_quarter_earnings_backtest(ticker: str = "SPY", as_of_date: Optional[date] = None) -> dict:
    """
    Backtests the last 4 reported quarterly earnings releases for `ticker`:
    - Evaluates the Earning Pulse (PQH/PQL & PMH/PML) trade setup, Action, Entry, Stop, Targets,
      and forward multi-week performance for each of the last 4 quarters.
    - Computes overall Win Rate %, Realized P&L ($/sh), and Realized R-Multiple scorecard.
    """
    from datetime import datetime
    from backend.services.earnings import get_earnings_dates_yf

    sym = (ticker or "SPY").strip().upper()
    cutoff_date = as_of_date or date.today()

    # 1. Fetch historical earnings releases
    ed_list = get_earnings_dates_yf(sym)
    past = []
    for e in ed_list:
        try:
            d = datetime.strptime(e["date"], "%Y-%m-%d").date()
            if d <= cutoff_date and (e.get("eps_actual") is not None or e.get("surprise_pct") is not None):
                past.append({**e, "date_obj": d})
        except Exception:
            continue

    # Fallback for index ETFs (SPY, QQQ) or if no earnings releases populated in YF
    if len(past) < 4:
        fallback_dates = []
        year = cutoff_date.year
        q_starts = [
            (year, 10, 14), (year, 7, 14), (year, 4, 14), (year, 1, 14),
            (year - 1, 10, 14), (year - 1, 7, 14), (year - 1, 4, 14), (year - 1, 1, 14)
        ]
        for y, m, day in q_starts:
            qd = date(y, m, day)
            if qd <= cutoff_date:
                fallback_dates.append(qd)
            if len(fallback_dates) >= 4:
                break
        past = [{"date": qd.isoformat(), "date_obj": qd, "eps_actual": None, "eps_estimate": None, "surprise_pct": None} for qd in reversed(fallback_dates)]

    last_4 = past[-4:]
    quarters_detail = []
    wins = 0
    losses = 0
    total_net_pnl = 0.0
    total_net_r = 0.0

    for item in last_4:
        d_str = item["date"]
        d_obj = item["date_obj"] if "date_obj" in item else datetime.strptime(d_str, "%Y-%m-%d").date()
        q_num = (d_obj.month - 1) // 3 + 1
        quarter_label = f"Q{q_num} {d_obj.year} ({d_obj.strftime('%b %d, %Y')})"

        ep = get_earning_pulse(sym, target_date=d_str, include_last_4q_backtest=False)
        bt = ep.get("backtest") or {}
        ti = ep.get("trade_idea") or {}

        badge = bt.get("result_badge", "⏳ ACTIVE")
        is_win = "WIN" in badge
        is_loss = "STOPPED" in badge
        if is_win:
            wins += 1
        elif is_loss:
            losses += 1

        pnl_val = bt.get("realized_pnl_per_share", 0.0)
        total_net_pnl += pnl_val
        r_str = bt.get("realized_r_multiple", "+0.00R")
        try:
            r_val = float(r_str.replace("R", "").replace("+", ""))
        except Exception:
            r_val = 0.0
        total_net_r += r_val

        quarters_detail.append({
            "quarter_label": quarter_label,
            "earnings_date": d_str,
            "eps_actual": item.get("eps_actual"),
            "eps_estimate": item.get("eps_estimate"),
            "surprise_pct": item.get("surprise_pct"),
            "structure_tag": ep.get("structure_tag", "W: N/A"),
            "action": ti.get("action", "WAIT"),
            "outcome_label": ep.get("raw_outcome", ep.get("valid_outcome", "")),
            "entry": ti.get("entry_level"),
            "stop": ti.get("stop_loss"),
            "t1": ti.get("target_1"),
            "t2": ti.get("target_2"),
            "open_contract": ti.get("open_contract"),
            "cost_efficient_spread": ti.get("cost_efficient_spread"),
            "cost_efficient_butterfly": ti.get("cost_efficient_butterfly"),
            "result_badge": badge,
            "status": "WIN" if is_win else ("STOPPED" if is_loss else "ACTIVE"),
            "realized_pnl_per_share": pnl_val,
            "realized_r_multiple": r_str,
            "max_favorable_excursion": bt.get("max_favorable_excursion", "$0.00"),
            "max_adverse_excursion": bt.get("max_adverse_excursion", "$0.00"),
            "summary": bt.get("summary", ""),
        })

    tot = len(quarters_detail)
    win_rate = round((wins / tot) * 100, 1) if tot > 0 else 0.0
    win_badge = f"{wins}/{tot} WINS ({win_rate:.0f}%)"
    if total_net_r >= 0:
        win_badge += f" · NET +{total_net_r:.2f}R"
    else:
        win_badge += f" · NET {total_net_r:.2f}R"

    return {
        "ticker": sym,
        "as_of_date": cutoff_date.isoformat(),
        "total_quarters": tot,
        "wins": wins,
        "losses": losses,
        "win_rate_pct": win_rate,
        "win_rate_badge": win_badge,
        "net_pnl_per_share": round(total_net_pnl, 2),
        "net_r_multiple": f"{total_net_r:+.2f}R",
        "quarters": quarters_detail,
    }


def get_earning_pulse(
    ticker: str = "SPY",
    target_date: Optional[str] = None,
    include_last_4q_backtest: bool = True,
) -> dict:
    """
    EARNING PULSE (Quarterly & Monthly High/Low Strategy):
    Evaluates price action against Previous Quarter High/Low (PQH / PQL) AND Previous Month High/Low (PMH / PML)
    on today's session or any historical `target_date`, returning a macro/earnings swing trade plan
    plus a forward multi-week backtest (up to 60 trading days after `scan_date`) AND last 4 quarters earnings backtest.
    """
    import yfinance as yf

    sym = (ticker or "SPY").strip().upper()
    yf_sym = _yf_symbol(sym)
    req_date = parse_pulse_date(target_date) if isinstance(target_date, str) else target_date

    daily_raw = yf.Ticker(yf_sym).history(period="2y", interval="1d", auto_adjust=True)
    daily = _norm_daily(daily_raw)
    if daily.empty:
        return {"ticker": sym, "error": "missing daily bars"}

    idx_dates = [_idx_date(i) for i in daily.index]
    if req_date:
        scan_date, completed, session_row = _resolve_trading_session(daily, req_date)
    else:
        weekday_dates = [d for d in idx_dates if d.weekday() < 5]
        scan_date = weekday_dates[-1] if weekday_dates else _roll_to_last_weekday(idx_dates[-1])
        completed, session_row = _split_session(daily, scan_date)

    if len(completed) < 63:
        return {"ticker": sym, "error": f"Insufficient historical bars prior to {scan_date} for Earning Pulse"}

    comp_dates = [_idx_date(i) for i in completed.index]

    # Previous Month High / Low (PMH / PML)
    prev_month_year = (scan_date.year, scan_date.month - 1) if scan_date.month > 1 else (scan_date.year - 1, 12)
    cal_month_df = completed[[(d.year, d.month) == prev_month_year for d in comp_dates]]
    month_df = cal_month_df if len(cal_month_df) >= 12 else completed.tail(21)
    pmh = round(float(month_df["high"].max()), 2)
    pml = round(float(month_df["low"].min()), 2)
    pm_mid = round((pmh + pml) / 2.0, 2)
    pm_range = round(max(pmh - pml, 0.5), 2)

    # Previous Quarter High / Low (PQH / PQL) — prior calendar quarter if >=40 bars, else prior 63 trading days
    curr_q = (scan_date.month - 1) // 3 + 1
    prev_q = curr_q - 1 if curr_q > 1 else 4
    prev_q_year = scan_date.year if curr_q > 1 else scan_date.year - 1
    prev_q_months = {(prev_q - 1) * 3 + 1, (prev_q - 1) * 3 + 2, (prev_q - 1) * 3 + 3}
    cal_q_df = completed[[d.year == prev_q_year and d.month in prev_q_months for d in comp_dates]]
    quarter_df = cal_q_df if len(cal_q_df) >= 35 else completed.tail(63)

    pqh = round(float(quarter_df["high"].max()), 2)
    pql = round(float(quarter_df["low"].min()), 2)
    pq_mid = round((pqh + pql) / 2.0, 2)
    pq_range = round(max(pqh - pql, 1.0), 2)
    atr = round(_atr(completed, 14), 2)
    buf = round(max(atr * 0.4, pq_range * 0.03), 2)

    if session_row is not None:
        open_px = round(float(session_row["open"]), 2)
        high_px = round(float(session_row["high"]), 2)
        low_px = round(float(session_row["low"]), 2)
        price = round(float(session_row["close"]), 2)
    else:
        prev_day = completed.iloc[-1]
        open_px = round(float(prev_day["open"]), 2)
        high_px = round(float(prev_day["high"]), 2)
        low_px = round(float(prev_day["low"]), 2)
        price = round(float(prev_day["close"]), 2)

    # Weekly Market Structure (W: LL LH HH HL) comparing current week vs previous calendar week
    w_tag, w_detail, w_stats = _compute_weekly_structure(daily, scan_date)

    # Determine Earning Pulse Validated Outcome from Quarterly (PQH/PQL) + Monthly (PMH/PML) Highs & Lows
    if high_px > pqh and price < pqh:
        outcome_label = "Quarterly High Sweep & Rejection Below PQH (Bearish Earnings/Macro Exhaustion)"
        action = "BUY PUTS"
        bias = "Bearish — Failed Breakout Above Previous Quarter High (PQH)"
        strike = _suggest_option_strike(sym, price, "PUTS")
        entry = price
        stop = round(max(high_px, pqh) + buf, 2)
        t1 = round(pm_mid if pm_mid < entry - atr else pq_mid, 2)
        t2 = round(pml if pml < t1 - atr else pql, 2)
        trigger = f"Swept PQH (${pqh:.2f} -> high ${high_px:.2f}) and rejected below PQH at ${price:.2f}"
        invalidation = f"Close above quarterly sweep high (${stop:.2f})"
        rationale = (
            f"{sym} swept above Previous Quarter High (${pqh:.2f}) to ${high_px:.2f} and failed to hold, "
            f"indicating quarterly resistance exhaustion targeting ${t1:.2f} and ${t2:.2f}."
        )
    elif price >= pqh and price >= pmh:
        outcome_label = "Quarterly & Monthly Breakout Above PQH & PMH (Bullish Earnings Drift Expansion)"
        action = "BUY CALLS"
        bias = "Bullish — Confirmed Breakout Above Both Quarterly (PQH) & Monthly (PMH) Highs"
        strike = _suggest_option_strike(sym, price, "CALLS")
        entry = price
        stop = round(min(pqh, pmh) - buf, 2)
        t1 = round(max(entry + atr * 2.0, pqh + pq_range * 0.382), 2)
        t2 = round(max(t1 + atr * 2.0, pqh + pq_range * 0.618), 2)
        trigger = f"Breakout & hold above both PQH (${pqh:.2f}) and PMH (${pmh:.2f}) at ${price:.2f}"
        invalidation = f"Close back below PQH/PMH breakout stop (${stop:.2f})"
        rationale = (
            f"{sym} cleared both Previous Quarter High (${pqh:.2f}) and Previous Month High (${pmh:.2f}), "
            f"confirming a structural quarterly breakout targeting ${t1:.2f} and ${t2:.2f}."
        )
    elif low_px < pql and price > pql:
        outcome_label = "Quarterly Low Sweep & Reclaim Above PQL (Bullish Institutional Accumulation)"
        action = "BUY CALLS"
        bias = "Bullish — Liquidity Sweep & Reclaim Above Previous Quarter Low (PQL)"
        strike = _suggest_option_strike(sym, price, "CALLS")
        entry = price
        stop = round(min(low_px, pql) - buf, 2)
        t1 = round(pm_mid if pm_mid > entry + atr else pq_mid, 2)
        t2 = round(pmh if pmh > t1 + atr else pqh, 2)
        trigger = f"Swept PQL (${pql:.2f} -> low ${low_px:.2f}) and reclaimed above PQL at ${price:.2f}"
        invalidation = f"Close below quarterly sweep low (${stop:.2f})"
        rationale = (
            f"{sym} swept below Previous Quarter Low (${pql:.2f}) to ${low_px:.2f} and reclaimed back above to ${price:.2f}, "
            f"signaling institutional accumulation targeting ${t1:.2f} and ${t2:.2f}."
        )
    elif price <= pql and price <= pml:
        outcome_label = "Quarterly & Monthly Breakdown Below PQL & PML (Bearish Structural Breakdown)"
        action = "BUY PUTS"
        bias = "Bearish — Confirmed Breakdown Below Both Quarterly (PQL) & Monthly (PML) Lows"
        strike = _suggest_option_strike(sym, price, "PUTS")
        entry = price
        stop = round(max(pql, pml) + buf, 2)
        t1 = round(min(entry - atr * 2.0, pql - pq_range * 0.382), 2)
        t2 = round(min(t1 - atr * 2.0, pql - pq_range * 0.618), 2)
        trigger = f"Breakdown below both PQL (${pql:.2f}) and PML (${pml:.2f}) at ${price:.2f}"
        invalidation = f"Reclaim back above PQL/PML stop (${stop:.2f})"
        rationale = (
            f"{sym} broke below both Previous Quarter Low (${pql:.2f}) and Previous Month Low (${pml:.2f}), "
            f"opening structural downside toward ${t1:.2f} and ${t2:.2f}."
        )
    elif price >= pmh:
        outcome_label = "Monthly Breakout Above PMH Inside Quarterly Range Targeting PQH"
        action = "BUY CALLS"
        bias = "Bullish — Monthly Breakout Rotating Toward Quarterly High (PQH)"
        strike = _suggest_option_strike(sym, price, "CALLS")
        entry = price
        stop = round(max(pm_mid, pmh - atr * 1.2), 2)
        t1 = round(pqh if pqh > entry + atr * 0.8 else entry + atr * 2.0, 2)
        t2 = round(max(t1 + atr * 1.5, pqh + pq_range * 0.382), 2)
        trigger = f"Holding above PMH (${pmh:.2f}) inside quarterly range [${pql:.2f} – ${pqh:.2f}]"
        invalidation = f"Close back below monthly breakout stop (${stop:.2f})"
        rationale = (
            f"{sym} broke above Previous Month High (${pmh:.2f}) inside the quarterly structure, "
            f"targeting Previous Quarter High (${pqh:.2f}) and ${t2:.2f}."
        )
    elif price <= pml:
        outcome_label = "Monthly Breakdown Below PML Inside Quarterly Range Targeting PQL"
        action = "BUY PUTS"
        bias = "Bearish — Monthly Breakdown Rotating Toward Quarterly Low (PQL)"
        strike = _suggest_option_strike(sym, price, "PUTS")
        entry = price
        stop = round(min(pm_mid, pml + atr * 1.2), 2)
        t1 = round(pql if pql < entry - atr * 0.8 else entry - atr * 2.0, 2)
        t2 = round(min(t1 - atr * 1.5, pql - pq_range * 0.382), 2)
        trigger = f"Trading below PML (${pml:.2f}) inside quarterly range [${pql:.2f} – ${pqh:.2f}]"
        invalidation = f"Reclaim above monthly breakdown stop (${stop:.2f})"
        rationale = (
            f"{sym} broke below Previous Month Low (${pml:.2f}) inside the quarterly range, "
            f"targeting Previous Quarter Low (${pql:.2f}) and ${t2:.2f}."
        )
    elif price >= pq_mid:
        outcome_label = "Upper Quarterly Regime (Above PQ Mid) Targeting PMH & PQH"
        action = "BUY CALLS"
        bias = "Bullish — Holding Above Quarterly Midpoint"
        strike = _suggest_option_strike(sym, price, "CALLS")
        entry = price
        stop = round(max(pq_mid - buf * 0.5, entry - atr * 1.5), 2)
        t1 = round(pmh if pmh > entry + atr * 0.5 else pqh, 2)
        t2 = round(max(pqh, t1 + atr * 1.8), 2)
        trigger = f"Holding above Quarterly Midpoint (${pq_mid:.2f}) at ${price:.2f}"
        invalidation = f"Break below Quarterly Midpoint stop (${stop:.2f})"
        rationale = (
            f"{sym} is positioned in the upper half of the quarterly range above PQ Mid (${pq_mid:.2f}), "
            f"favoring upside toward PMH (${pmh:.2f}) and PQH (${pqh:.2f})."
        )
    else:
        outcome_label = "Lower Quarterly Regime (Below PQ Mid) Targeting PML & PQL"
        action = "BUY PUTS"
        bias = "Bearish — Below Quarterly Midpoint"
        strike = _suggest_option_strike(sym, price, "PUTS")
        entry = price
        stop = round(min(pq_mid + buf * 0.5, entry + atr * 1.5), 2)
        t1 = round(pml if pml < entry - atr * 0.5 else pql, 2)
        t2 = round(min(pql, t1 - atr * 1.8), 2)
        trigger = f"Trading below Quarterly Midpoint (${pq_mid:.2f}) at ${price:.2f}"
        invalidation = f"Reclaim above Quarterly Midpoint stop (${stop:.2f})"
        rationale = (
            f"{sym} is positioned in the lower half of the quarterly range below PQ Mid (${pq_mid:.2f}), "
            f"favoring downside toward PML (${pml:.2f}) and PQL (${pql:.2f})."
        )

    # Directional safety guard
    if "PUTS" in action:
        if t1 >= entry - atr * 0.4:
            t1 = round(entry - atr * 1.5, 2)
        if t2 >= t1 - atr * 0.4:
            t2 = round(t1 - atr * 1.5, 2)
        if stop <= entry:
            stop = round(entry + atr * 1.2, 2)
    else:
        if t1 <= entry + atr * 0.4:
            t1 = round(entry + atr * 1.5, 2)
        if t2 <= t1 + atr * 0.4:
            t2 = round(t1 + atr * 1.5, 2)
        if stop >= entry:
            stop = round(entry - atr * 1.2, 2)

    risk = abs(entry - stop) if entry != stop else 1.0
    rr = round(abs(t1 - entry) / risk, 2)

    # Weekly Market Structure (W: LH, W: LL, W: HH, W: HL) for Earning Pulse
    w_tag, w_detail = _compute_market_structure(
        w_stats.get("curr_week_high", high_px),
        w_stats.get("curr_week_low", low_px),
        w_stats.get("prev_week_high", pqh),
        w_stats.get("prev_week_low", pql),
        prefix="W",
        action=action,
        outcome=outcome_label,
    )
    w_stats["weekly_structure"] = w_tag
    w_stats["weekly_structure_detail"] = w_detail

    # Forward multi-week backtest across up to 60 trading days after scan_date
    future_daily = daily[[d > scan_date for d in idx_dates]].head(60)
    backtest = _simulate_trade_backtest(
        future_daily,
        entry=entry,
        stop=stop,
        t1=t1,
        t2=t2,
        action=action,
        timeframe_label=f"Multi-Week Earnings/Quarterly Backtest (Up to 60 Trading Days After {scan_date.isoformat()})",
        structure_tag=w_tag,
    )
    backtest["weekly_structure"] = w_tag
    backtest["weekly_structure_detail"] = w_detail
    backtest["structure_tag"] = w_tag

    ep_contracts = _build_pulse_contract_options(
        ticker=sym,
        price=entry,
        action=action,
        t1=t1,
        t2=t2,
        atr=atr,
        horizon="earning",
    )

    ep_as_of, ep_day_name, ep_weekend = _format_session_as_of(scan_date, req_date)

    last_4q_backtest = None
    if include_last_4q_backtest:
        try:
            last_4q_backtest = get_last_4_quarter_earnings_backtest(sym, as_of_date=scan_date)
        except Exception as _e4q:
            logger.debug(f"[Earning Pulse] 4Q backtest error for {sym}: {_e4q}")

    return {
        "strategy_name": "Earning Pulse (Quarterly & Monthly High/Low Engine)",
        "ticker": sym,
        "scan_date": scan_date.isoformat(),
        "session_day_name": ep_day_name,
        "is_weekend_or_closed": ep_weekend,
        "requested_date": req_date.isoformat() if req_date else None,
        "price": price,
        "session_open": open_px,
        "session_high": high_px,
        "session_low": low_px,
        "valid_outcome": f"[{w_tag}] {outcome_label}",
        "raw_outcome": outcome_label,
        "structure_tag": w_tag,
        "weekly_structure": w_tag,
        "weekly_structure_detail": w_detail,
        "trade_idea": {
            "action": action,
            "bias": f"{bias} · {w_tag}",
            "structure_tag": w_tag,
            "weekly_structure": w_tag,
            "weekly_structure_detail": w_detail,
            "suggested_contract": f"{strike} (45–90 DTE)",
            "open_contract": ep_contracts["open_contract"],
            "cost_efficient_otm": ep_contracts["cost_efficient_otm"],
            "cost_efficient_spread": ep_contracts["cost_efficient_spread"],
            "cost_efficient_butterfly": ep_contracts["cost_efficient_butterfly"],
            "cost_efficient_summary": ep_contracts["cost_efficient_summary"],
            "cost_efficient_contracts": ep_contracts["cost_efficient_contracts"],
            "expiry_type": "45–90 DTE Quarterly / Post-Earnings Options (Delta 0.45 - 0.60)",
            "entry_level": entry,
            "stop_loss": stop,
            "target_1": t1,
            "target_2": t2,
            "reward_risk": f"{rr}x",
            "trigger_condition": trigger,
            "invalidation_rule": invalidation,
            "rationale": f"[{w_tag}] {rationale}",
            "execution_strategy": "Scale 50% at Target 1, move stop to breakeven, and hold runner for quarterly Target 2.",
        },
        "key_levels": {
            "pqh": pqh,
            "pql": pql,
            "pq_mid": pq_mid,
            "pq_range": pq_range,
            "pmh": pmh,
            "pml": pml,
            "pm_mid": pm_mid,
            "pm_range": pm_range,
            "atr_14d": atr,
            "structure_tag": w_tag,
            "weekly_structure": w_tag,
            "weekly_structure_detail": w_detail,
            "curr_week_high": w_stats.get("curr_week_high"),
            "curr_week_low": w_stats.get("curr_week_low"),
            "prev_week_high": w_stats.get("prev_week_high"),
            "prev_week_low": w_stats.get("prev_week_low"),
        },
        "backtest": backtest,
        "earnings_backtest_last_4_quarters": last_4q_backtest,
        "as_of": ep_as_of,
    }


def get_all_pulses(ticker: str = "SPY", target_date: Optional[str] = None) -> dict:
    """
    Returns all 3 Pulse strategies for `ticker` (optionally on/backtested from `target_date`):
    1. `pulse` (Day Trading Pulse — Daily PDH/PDL & Weekly PWH/PWL + 8:35 AM 5m Opening Candle)
    2. `long_term_pulse` (Long Term Pulse — Monthly PMH/PML & Daily PDH/PDL)
    3. `earning_pulse` (Earning Pulse — Quarterly PQH/PQL & Monthly PMH/PML)
    """
    return {
        "ticker": (ticker or "SPY").strip().upper(),
        "target_date": target_date,
        "pulse": get_valid_outcome_and_trade_idea(ticker=ticker, target_date=target_date),
        "long_term_pulse": get_long_term_pulse(ticker=ticker, target_date=target_date),
        "earning_pulse": get_earning_pulse(ticker=ticker, target_date=target_date),
    }


def get_spy_valid_outcome_and_trade_idea(current_price: Optional[float] = None, ticker: str = "SPY") -> dict:
    """Backward-compatible wrapper around get_valid_outcome_and_trade_idea."""
    return get_valid_outcome_and_trade_idea(ticker=ticker, current_price=current_price)


if __name__ == "__main__":
    import json
    import sys

    symbol = sys.argv[1] if len(sys.argv) > 1 else "SPY"
    dt = sys.argv[2] if len(sys.argv) > 2 else None
    print(json.dumps(get_all_pulses(symbol, dt), indent=2, default=str))

