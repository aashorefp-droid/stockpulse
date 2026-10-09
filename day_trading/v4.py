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
        "signal": asdict(signal),
        "outcomes": outcomes,
    }


def analyze(ticker: str) -> dict:
    import yfinance as yf

    daily = yf.Ticker(ticker).history(period="30d", interval="1d", auto_adjust=True)
    return analyze_from_daily(ticker, daily)


if __name__ == "__main__":
    import json
    import sys

    symbol = sys.argv[1] if len(sys.argv) > 1 else "SPY"
    print(json.dumps(analyze(symbol), indent=2, default=str))
