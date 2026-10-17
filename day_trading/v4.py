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


def analyze(ticker: str) -> dict:
    import yfinance as yf

    clean_ticker = (ticker or "SPY").strip().upper()
    yf_sym = _yf_symbol(clean_ticker)
    daily = yf.Ticker(yf_sym).history(period="30d", interval="1d", auto_adjust=True)
    return analyze_from_daily(clean_ticker, daily)


def get_valid_outcome_and_trade_idea(ticker: str = "SPY", current_price: Optional[float] = None) -> dict:
    """
    Evaluates current price action + first 5-minute opening candle for any ticker (SPY, SPXW, QQQ, etc.)
    against previous day/week key levels (PDH, PDL, PWH, PWL, Mid) to determine the SINGLE VALID OUTCOME
    from the 8:00 AM Playbook and returns ONE decisive Calls OR Puts trade idea with strike, entry, stop, and targets.
    """
    import yfinance as yf

    sym = (ticker or "SPY").strip().upper()
    yf_sym = _yf_symbol(sym)

    v4_data = analyze(sym)
    if v4_data.get("error"):
        return {"ticker": sym, "error": v4_data["error"]}

    price = float(current_price) if current_price and current_price > 0 else float(v4_data.get("price", 0))
    session_high = float(v4_data.get("session_high") or price)
    session_low = float(v4_data.get("session_low") or price)

    # Inspect intraday 5-minute bars to capture the 8:30–8:35 AM CT (09:30–09:35 ET) opening candle
    open_5m = price
    close_5m = price
    latest_price = price
    try:
        intraday = yf.Ticker(yf_sym).history(period="2d", interval="5m", auto_adjust=True)
        if not intraday.empty:
            last_date = intraday.index[-1].date()
            today_bars = intraday[intraday.index.date == last_date]
            if not today_bars.empty:
                first_bar = today_bars.iloc[0]
                last_bar = today_bars.iloc[-1]
                open_5m = float(first_bar["Open"])
                close_5m = float(first_bar["Close"])
                high_5m = float(first_bar["High"])
                low_5m = float(first_bar["Low"])
                latest_price = round(float(last_bar["Close"]), 2)
                if current_price is None or current_price <= 0:
                    price = round(close_5m, 2)
                    session_high = round(high_5m, 2)
                    session_low = round(low_5m, 2)
                else:
                    session_high = max(session_high, float(today_bars["High"].max()))
                    session_low = min(session_low, float(today_bars["Low"].min()))
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

    return {
        "ticker": sym,
        "yf_symbol": yf_sym,
        "price": price,
        "session_high": round(session_high, 2),
        "session_low": round(session_low, 2),
        "valid_outcome": outcome_label,
        "trade_idea": {
            "action": action,
            "bias": bias,
            "suggested_contract": suggested_strike,
            "expiry_type": "0DTE or Next Expiry (Delta 0.40 - 0.50)",
            "entry_level": entry,
            "stop_loss": stop,
            "target_1": t1,
            "target_2": t2,
            "reward_risk": f"{rr}x",
            "trigger_condition": trigger,
            "invalidation_rule": invalidation,
            "rationale": rationale,
            "execution_strategy": "Scale 50% profit at Target 1, immediately move stop to breakeven, and let runner trail for Target 2."
        },
        "key_levels": {
            "pdh": pdh,
            "pdl": pdl,
            "pwh": pwh,
            "pwl": pwl,
            "pd_mid": pd_mid,
            "atr": atr,
        },
        "morning_signal": v4_data.get("signal", {}),
        "as_of": datetime.now().strftime("%Y-%m-%d %I:%M %p CT")
    }


def get_spy_valid_outcome_and_trade_idea(current_price: Optional[float] = None, ticker: str = "SPY") -> dict:
    """Backward-compatible wrapper around get_valid_outcome_and_trade_idea."""
    return get_valid_outcome_and_trade_idea(ticker=ticker, current_price=current_price)


if __name__ == "__main__":
    import json
    import sys

    symbol = sys.argv[1] if len(sys.argv) > 1 else "SPY"
    print(json.dumps(analyze(symbol), indent=2, default=str))
