"""
day_trading/v3.py — PDH / PWH / PDL / PWL setup engine.

Edge thesis
-----------
These levels aren't signals. They're where adverse positioning concentrates —
stops just beyond, trapped longs/shorts at the extremes, every desk watching.
The asymmetry isn't in the level; it's in the *reaction* to the level. A naked
touch is a coin flip. A touch + confirmation is an asymmetric bet.

Setups detected, ranked by quality
----------------------------------
1. Sweep + Reclaim (A+) — Liquidity grab through PWH/PWL/PDH/PDL, price
   closes back inside within 1–2 candles. Fade. Highest hit rate.
2. Break + Retest        — Clean close beyond a level on >1.5× avg volume,
   then pullback that holds the broken level as new S/R. Continuation.
3. (Reserved for future) Range fade between PDH/PDL.
4. (Reserved for future) Open-drive trend day.

Decision criterion
------------------
Never enter on the bare touch. Wait one or two 5-minute candles:
- Rejection = wick, immediate snapback inside, no volume follow-through → fade.
- Acceptance = price holds outside, healthy volume, second candle confirms → trade with.

Time filter
-----------
Only act in the 09:30–11:00 ET and 13:30–15:30 ET windows. The midday
chop band (11:00–13:30 ET) produces too many false breaks.

Regime gate (optional)
----------------------
Pass `regime="Long Gamma"` or `regime="Short Gamma"` from the macro snapshot:
- Long Gamma  → dealers dampen → mean-revert → favor fades, discount continuation.
- Short Gamma → dealers amplify → trends extend → favor continuation, discount fades.

Output
------
A structured dict with the detected signal (entry / stop / targets / R:R /
grade / rationale) or `setup: "no_setup"` if nothing qualifies right now.

Run standalone:
    python -m day_trading.v3 SPY
"""
from __future__ import annotations

from dataclasses import dataclass, asdict, field
from datetime import datetime, time
from typing import Literal, Optional
from zoneinfo import ZoneInfo

import pandas as pd
import yfinance as yf

ET = ZoneInfo("America/New_York")

# Windows where level reactions are most reliable; midday is excluded.
MORNING_WINDOW   = (time(9, 30),  time(11, 0))
AFTERNOON_WINDOW = (time(13, 30), time(15, 30))

# Proximity tolerance for "near a level" and VWAP confluence (% of price).
NEAR_TOL_PCT     = 0.003   # 0.3% — tight enough that level matters
VWAP_CONFL_PCT   = 0.003

# Volume multiple required to call a break "clean".
BREAK_VOL_MULT   = 1.5

# In-process result cache. yfinance gets pounded at the open; repeat scans
# within ~1 min should hit memory, not the network. 60s is shorter than the
# 5-min bar cadence so we never miss a bar by more than its own interval.
_RESULT_CACHE: dict[tuple[str, Optional[str]], tuple[datetime, dict]] = {}
_CACHE_TTL_SEC   = 60

# Pre-open / first-bars-of-session cutoff. Sweep+reclaim needs ≥3 5m bars
# (~09:45 ET close); break+retest needs ≥8 (~10:10 ET). Before 09:50 there
# is nothing for either detector to find → skip the yfinance fetches.
_EARLY_SESSION_CUTOFF = time(9, 50)

SetupKind = Literal[
    "sweep_reclaim", "break_retest", "range_fade", "open_drive", "no_setup"
]
Side      = Literal["long", "short"]


# ── Data classes ─────────────────────────────────────────────────────────────

@dataclass
class Levels:
    pdh: float
    pdl: float
    pwh: float
    pwl: float
    pd_close: float
    pd_mid: float


@dataclass
class Signal:
    setup:     SetupKind
    side:      Optional[Side]
    grade:     str                   # A+ / A / B / C
    level:     Optional[str]         # "PDH" / "PWH" / "PDL" / "PWL"
    level_val: Optional[float]
    entry:     Optional[float]
    stop:      Optional[float]
    targets:   list[float] = field(default_factory=list)
    rr:        Optional[float] = None
    rationale: str = ""
    time:      Optional[str] = None  # detection bar timestamp


# ── Helpers ──────────────────────────────────────────────────────────────────

def _in_trading_window(t: time) -> bool:
    return (MORNING_WINDOW[0]   <= t <= MORNING_WINDOW[1] or
            AFTERNOON_WINDOW[0] <= t <= AFTERNOON_WINDOW[1])


def _near(price: float, level: float, tol_pct: float = NEAR_TOL_PCT) -> bool:
    return abs(price - level) / max(level, 1e-9) <= tol_pct


def _vwap(bars: pd.DataFrame) -> pd.Series:
    """Anchored VWAP from the start of the supplied bars (session VWAP if
    bars are today's RTH session only)."""
    tp  = (bars["High"] + bars["Low"] + bars["Close"]) / 3
    vol = bars["Volume"].replace(0, 1)
    return (tp * vol).cumsum() / vol.cumsum()


def _bar_time_et(bar: pd.Series) -> time:
    ts = bar.name
    if hasattr(ts, "tz_convert") and ts.tzinfo is not None:
        return ts.tz_convert(ET).time()
    return ts.time()


def _normalize_ohlcv(df: pd.DataFrame) -> pd.DataFrame:
    """Map common lowercase OHLCV column names (Alpaca, ccxt) to the
    capitalized form v3 expects, and coerce the index to a DatetimeIndex
    so `.date` works. Idempotent; safe on yfinance frames too.

    Why the index coercion: scanner.py passes its Alpaca-format daily_df
    which uses a plain `Index` (strings/objects), so v3's later
    `daily.index.date < today_et` raises `AttributeError: 'Index' object
    has no attribute 'date'`. yfinance frames already have a
    DatetimeIndex and are unaffected.
    """
    rename = {}
    for src in ("open", "high", "low", "close", "volume"):
        if src in df.columns and src.capitalize() not in df.columns:
            rename[src] = src.capitalize()
    if rename:
        df = df.rename(columns=rename)
    if not isinstance(df.index, pd.DatetimeIndex):
        try:
            df = df.copy()
            df.index = pd.to_datetime(df.index, errors="coerce", utc=False)
            # Drop any rows whose index didn't parse to a real timestamp.
            df = df[df.index.notna()]
        except Exception:
            pass  # leave as-is; caller will hit the original error loudly
    return df


# ── Level computation ────────────────────────────────────────────────────────

def get_levels(completed_daily: pd.DataFrame) -> Levels:
    """Compute PDH/PDL/PWH/PWL from a daily-bar frame that EXCLUDES today.

    PWH/PWL use the last 5 completed trading days (the rolling week of
    most recent activity), which keeps the level meaningful even when
    the calendar week has only had 1–2 sessions so far.
    """
    daily = completed_daily.dropna(subset=["High", "Low", "Close"])
    if len(daily) < 5:
        raise ValueError("need ≥5 completed daily bars")
    pd_row    = daily.iloc[-1]
    last_week = daily.iloc[-5:]
    return Levels(
        pdh=float(pd_row["High"]),
        pdl=float(pd_row["Low"]),
        pwh=float(last_week["High"].max()),
        pwl=float(last_week["Low"].min()),
        pd_close=float(pd_row["Close"]),
        pd_mid=(float(pd_row["High"]) + float(pd_row["Low"])) / 2,
    )


# ── Setup detectors ──────────────────────────────────────────────────────────
# Levels evaluated highest-quality-first; PWH/PWL outrank PDH/PDL because
# weekly extremes attract heavier stop liquidity.

_LEVELS_SHORT = (("PWH", "pwh"), ("PDH", "pdh"))   # poke-above → fade short
_LEVELS_LONG  = (("PWL", "pwl"), ("PDL", "pdl"))   # poke-below → fade long


def _detect_sweep_reclaim(
    bars: pd.DataFrame, levels: Levels
) -> Optional[Signal]:
    """A+ setup: 1–2 candle sweep through a level then close back inside.

    Stop: a few ticks beyond the sweep extreme.
    Targets: VWAP first, opposite-side level second.
    """
    if len(bars) < 3:
        return None
    last = bars.iloc[-1]
    prev = bars.iloc[-2]
    if not _in_trading_window(_bar_time_et(last)):
        return None

    vwap_now = float(_vwap(bars).iloc[-1])

    candidates: list[tuple[str, float, Side]] = (
        [(name, getattr(levels, attr), "short") for name, attr in _LEVELS_SHORT] +
        [(name, getattr(levels, attr), "long")  for name, attr in _LEVELS_LONG]
    )

    for label, lvl, side in candidates:
        if side == "short":
            swept   = prev["High"] > lvl or last["High"] > lvl
            reclaim = last["Close"] < lvl
            if not (swept and reclaim):
                continue
            sweep_ext = float(max(prev["High"], last["High"]))
            entry     = lvl
            stop      = sweep_ext * 1.001
            tgt1      = vwap_now
            tgt2      = levels.pwl if label == "PWH" else levels.pdl
        else:
            swept   = prev["Low"] < lvl or last["Low"] < lvl
            reclaim = last["Close"] > lvl
            if not (swept and reclaim):
                continue
            sweep_ext = float(min(prev["Low"], last["Low"]))
            entry     = lvl
            stop      = sweep_ext * 0.999
            tgt1      = vwap_now
            tgt2      = levels.pwh if label == "PWL" else levels.pdh

        is_weekly  = label in ("PWH", "PWL")
        vwap_close = _near(entry, vwap_now, VWAP_CONFL_PCT)
        grade      = ("A+" if (is_weekly and vwap_close) else
                      "A"  if is_weekly or vwap_close else "B")

        risk   = abs(stop - entry)
        reward = abs(tgt1 - entry)
        rr     = (reward / risk) if risk > 0 else None

        return Signal(
            setup="sweep_reclaim",
            side=side,
            grade=grade,
            level=label,
            level_val=round(lvl, 2),
            entry=round(entry, 2),
            stop=round(stop, 2),
            targets=[round(tgt1, 2), round(tgt2, 2)],
            rr=round(rr, 2) if rr is not None else None,
            rationale=(
                f"Sweep+reclaim at {label} ${lvl:.2f} ({side}). "
                f"VWAP ${vwap_now:.2f} "
                f"{'confluent' if vwap_close else 'distant'}. "
                f"Half off at VWAP/T1, trail balance toward ${tgt2:.2f}."
            ),
            time=str(last.name),
        )
    return None


def _detect_break_retest(
    bars: pd.DataFrame, levels: Levels
) -> Optional[Signal]:
    """Continuation: clean close beyond a level on >1.5× avg volume in the
    last ~6 bars, then the current bar pulls back to retest the level
    and holds — broken level now flips to S/R."""
    if len(bars) < 8:
        return None
    last = bars.iloc[-1]
    if not _in_trading_window(_bar_time_et(last)):
        return None

    avg_vol = float(bars["Volume"].iloc[-20:].mean() if len(bars) >= 20
                    else bars["Volume"].mean())
    if avg_vol <= 0:
        return None
    recent_vol_ok = bars["Volume"].iloc[-8:].max() > BREAK_VOL_MULT * avg_vol

    # Order: PWH/PWL first (higher-grade), PDH/PDL second.
    longs:  list[tuple[str, float]] = [("PWH", levels.pwh), ("PDH", levels.pdh)]
    shorts: list[tuple[str, float]] = [("PWL", levels.pwl), ("PDL", levels.pdl)]

    recent = bars.iloc[-8:]
    rng    = max(levels.pdh - levels.pdl, 1e-9)

    for label, lvl in longs:
        broke  = (recent["Close"] > lvl).any() and recent_vol_ok
        retest = _near(float(last["Low"]), lvl) and last["Close"] > lvl
        if not (broke and retest):
            continue
        entry = lvl
        stop  = float(last["Low"]) * 0.999
        tgt1  = entry + rng * 0.5
        tgt2  = levels.pwh if label == "PDH" else entry + rng
        risk  = abs(stop - entry)
        rr    = (abs(tgt1 - entry) / risk) if risk > 0 else None
        return Signal(
            setup="break_retest",
            side="long",
            grade="A" if label == "PWH" else "B",
            level=label, level_val=round(lvl, 2),
            entry=round(entry, 2), stop=round(stop, 2),
            targets=[round(tgt1, 2), round(tgt2, 2)],
            rr=round(rr, 2) if rr is not None else None,
            rationale=(
                f"Broke {label} ${lvl:.2f} on >{BREAK_VOL_MULT}× volume, "
                f"retest holding as support. Measured-move T1 ${tgt1:.2f}."
            ),
            time=str(last.name),
        )

    for label, lvl in shorts:
        broke  = (recent["Close"] < lvl).any() and recent_vol_ok
        retest = _near(float(last["High"]), lvl) and last["Close"] < lvl
        if not (broke and retest):
            continue
        entry = lvl
        stop  = float(last["High"]) * 1.001
        tgt1  = entry - rng * 0.5
        tgt2  = levels.pwl if label == "PDL" else entry - rng
        risk  = abs(stop - entry)
        rr    = (abs(tgt1 - entry) / risk) if risk > 0 else None
        return Signal(
            setup="break_retest",
            side="short",
            grade="A" if label == "PWL" else "B",
            level=label, level_val=round(lvl, 2),
            entry=round(entry, 2), stop=round(stop, 2),
            targets=[round(tgt1, 2), round(tgt2, 2)],
            rr=round(rr, 2) if rr is not None else None,
            rationale=(
                f"Broke {label} ${lvl:.2f} on >{BREAK_VOL_MULT}× volume, "
                f"retest holding as resistance. Measured-move T1 ${tgt1:.2f}."
            ),
            time=str(last.name),
        )
    return None


# ── Regime bias ──────────────────────────────────────────────────────────────

def _apply_regime_bias(sig: Signal, regime: Optional[str]) -> Signal:
    """Long Gamma favors fades (mean-revert). Short Gamma favors continuation.
    Discount (don't suppress) signals fighting the regime so the user still
    sees them but graded down."""
    if sig is None or not regime:
        return sig
    is_fade = sig.setup in ("sweep_reclaim", "range_fade")
    is_cont = sig.setup == "break_retest"
    if regime == "Long Gamma" and is_cont:
        sig.grade += " (regime-discount: Long Gamma favors fades)"
    elif regime == "Short Gamma" and is_fade:
        sig.grade += " (regime-discount: Short Gamma favors continuation)"
    return sig


# ── Public API ───────────────────────────────────────────────────────────────

def analyze(
    ticker: str, *,
    regime: Optional[str] = None,
    daily: Optional[pd.DataFrame] = None,
) -> dict:
    """Return the best detected day-trading setup for `ticker` right now.

    `regime`: optional macro context ("Long Gamma" | "Short Gamma"). When
    provided, signals fighting the regime are grade-discounted (not removed).
    `daily`: caller may pass already-loaded daily bars (e.g. from the
    scanner's Alpaca fetch) to skip the redundant yfinance daily call.
    """
    key      = (ticker.upper(), regime)
    now_et   = datetime.now(ET)
    cached   = _RESULT_CACHE.get(key)
    if cached and (now_et - cached[0]).total_seconds() < _CACHE_TTL_SEC:
        return cached[1]

    # Early-session guard: before 09:50 ET on a weekday no detector can
    # fire (sweep needs ≥3 bars, break needs ≥8). Skip the yfinance hit.
    if (now_et.weekday() < 5
            and time(9, 0) <= now_et.time() < _EARLY_SESSION_CUTOFF):
        result = {
            "ticker":      ticker,
            "as_of":       now_et.isoformat(timespec="seconds"),
            "regime_used": regime,
            "signal": {
                "setup": "no_setup",
                "rationale": "warming up — first ~20m of session, "
                             "not enough bars for any detector yet",
            },
        }
        _RESULT_CACHE[key] = (now_et, result)
        return result

    if daily is None:
        tk = yf.Ticker(ticker)
        daily = tk.history(period="15d", interval="1d", auto_adjust=True)
    else:
        daily = _normalize_ohlcv(daily)
    if daily.empty or len(daily) < 6:
        return {"ticker": ticker, "error": "insufficient daily history"}
    tk = yf.Ticker(ticker)  # still needed for intraday fetch below

    intraday = tk.history(period="5d", interval="5m", auto_adjust=True)
    if intraday.empty:
        return {"ticker": ticker, "error": "no intraday bars available"}

    # Normalise intraday index to ET so window/date filters are unambiguous.
    if intraday.index.tz is None:
        intraday.index = intraday.index.tz_localize("UTC").tz_convert(ET)
    else:
        intraday.index = intraday.index.tz_convert(ET)

    today_et   = datetime.now(ET).date()
    today_mask = (
        (intraday.index.date == today_et) &
        (intraday.index.time >= time(9, 30)) &
        (intraday.index.time <  time(16, 0))
    )
    today_bars = intraday[today_mask]
    if len(today_bars) < 3:
        return {"ticker": ticker,
                "error": "session not started or fewer than 3 5m bars yet"}

    # Levels from the last *completed* daily bar (exclude today's bar if yf
    # included it). Compare dates rather than indices to be robust to tz.
    if hasattr(daily.index, "tz") and daily.index.tz is not None:
        daily_dates = daily.index.tz_convert(ET).date
    else:
        daily_dates = daily.index.date
    completed_daily = daily[daily_dates < today_et]
    if len(completed_daily) < 5:
        return {"ticker": ticker,
                "error": "insufficient completed daily bars (<5)"}
    levels = get_levels(completed_daily)

    # Priority: A+ (sweep+reclaim) > continuation (break+retest).
    sig: Optional[Signal] = (_detect_sweep_reclaim(today_bars, levels)
                             or _detect_break_retest(today_bars, levels))
    sig = _apply_regime_bias(sig, regime) if sig else sig

    result = {
        "ticker":      ticker,
        "as_of":       datetime.now(ET).isoformat(timespec="seconds"),
        "regime_used": regime,
        "levels":      asdict(levels),
        "signal":      asdict(sig) if sig else {
            "setup": "no_setup",
            "rationale": "no qualifying setup at this bar — wait for "
                         "sweep+reclaim or volume break+retest in window.",
        },
    }
    _RESULT_CACHE[key] = (now_et, result)
    return result


# ── CLI ──────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import json
    import sys

    ticker = sys.argv[1] if len(sys.argv) > 1 else "SPY"
    regime = sys.argv[2] if len(sys.argv) > 2 else None
    print(json.dumps(analyze(ticker, regime=regime), indent=2, default=str))
