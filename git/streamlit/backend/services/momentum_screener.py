"""
Momentum screener — picks the top-N momentum stocks from a candidate
universe each week and persists them to a JSON file the scanner reads.

Screening rules (Minervini / O'Neil style; conservative defaults):
  - Price > 200-day SMA          (in an uptrend)
  - 50-day SMA > 200-day SMA      (Stage 2 confirmation)
  - 20-day avg dollar volume ≥ $100M  (liquidity floor)
  - Ranked by 63-trading-day (~3 month) % return

Universe = union of WATCHLISTS["default", "tech", "mega_cap", "momentum"],
minus ETFs. Roughly 80 deduped candidates today; the universe expands
automatically as you add tickers to those lists.

Public API:
  pick_momentum_top_n(n=20) -> dict     # run the screen
  save_momentum_list(result)            # persist to JSON
  load_momentum_list() -> Optional[dict]# read latest JSON (None if none)
  JSON_PATH                             # the persisted file location
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import pandas as pd
import yfinance as yf

logger = logging.getLogger(__name__)

# ── Config ───────────────────────────────────────────────────────────────────
SCORE_LOOKBACK_DAYS = 63                 # ~3 months of trading days
SMA_FAST            = 50
SMA_SLOW            = 200
MIN_DOLLAR_VOL_20D  = 100_000_000        # $100M floor

# Watchlist keys used to assemble the candidate universe. Adding tickers
# to any of these lists automatically widens the screen.
DEFAULT_UNIVERSE_KEYS = ("default", "tech", "mega_cap", "momentum")

# Where the screened list lives on disk. Sibling to gex_history.db etc.
JSON_PATH: Path = (
    Path(__file__).resolve().parent.parent / "db" / "momentum_watchlist.json"
)


# ── Universe assembly ───────────────────────────────────────────────────────

def _gather_universe() -> list[str]:
    """Union of the configured watchlists, ETFs excluded, deduped, order
    preserved. Lazy import so this module can be imported standalone."""
    from backend.services.scanner import WATCHLISTS, ETF_SECTORS
    etfs = set(ETF_SECTORS.keys()) | set(WATCHLISTS.get("etfs", []))
    seen: set = set()
    out: list[str] = []
    for key in DEFAULT_UNIVERSE_KEYS:
        for t in WATCHLISTS.get(key, []):
            if t in etfs or t in seen:
                continue
            seen.add(t)
            out.append(t)
    return out


# ── Screen ──────────────────────────────────────────────────────────────────

def pick_momentum_top_n(n: int = 20,
                        universe: Optional[list[str]] = None) -> dict:
    """Run the momentum screen. Returns a dict with the ranked top-N plus
    diagnostics (universe size, passed-filter count, per-ticker scores,
    criteria used)."""
    now_iso = datetime.now(timezone.utc).isoformat(timespec="seconds")
    uni = universe or _gather_universe()
    if not uni:
        return {
            "tickers": [], "scores": {}, "universe_size": 0,
            "passed_filters": 0, "generated_at": now_iso,
            "criteria": _criteria_dict(),
            "error": "empty universe",
        }

    try:
        bars = yf.download(
            uni, period="9mo", interval="1d",
            auto_adjust=True, progress=False, threads=True,
            group_by="ticker",
        )
    except Exception as e:
        logger.warning(f"[momentum] yf.download failed: {e}")
        return {
            "tickers": [], "scores": {}, "universe_size": len(uni),
            "passed_filters": 0, "generated_at": now_iso,
            "criteria": _criteria_dict(),
            "error": f"yf.download: {type(e).__name__}: {str(e)[:120]}",
        }

    scored: list[tuple[str, float, float]] = []   # (ticker, 3mo_return, $-vol)
    for t in uni:
        try:
            df = bars[t].dropna(how="all") if t in bars else None
            if df is None or df.empty or len(df) < SMA_SLOW + 5:
                continue
            close  = df["Close"].dropna()
            volume = df["Volume"].dropna()
            if len(close) < SMA_SLOW + 5 or len(close) < SCORE_LOOKBACK_DAYS + 1:
                continue

            current = float(close.iloc[-1])
            sma50   = float(close.iloc[-SMA_FAST:].mean())
            sma200  = float(close.iloc[-SMA_SLOW:].mean())
            # Uptrend gate
            if current <= sma200 or sma50 <= sma200:
                continue
            # Liquidity gate
            last20 = df.iloc[-20:]
            dvol = float((last20["Close"] * last20["Volume"]).mean())
            if dvol < MIN_DOLLAR_VOL_20D:
                continue
            # Score
            ret_3m = (current / float(close.iloc[-SCORE_LOOKBACK_DAYS]) - 1) * 100
            scored.append((t, ret_3m, dvol))
        except Exception as e:
            logger.debug(f"[momentum] {t} skipped: {type(e).__name__}: {e}")
            continue

    scored.sort(key=lambda x: x[1], reverse=True)
    top = scored[:max(1, n)]
    return {
        "tickers":        [t for t, _, _ in top],
        "scores":         {t: round(r, 2) for t, r, _ in top},
        "dollar_volumes": {t: int(dv)       for t, _, dv in top},
        "universe_size":  len(uni),
        "passed_filters": len(scored),
        "generated_at":   now_iso,
        "criteria":       _criteria_dict(),
    }


def _criteria_dict() -> dict:
    return {
        "lookback_days":      SCORE_LOOKBACK_DAYS,
        "sma_fast":           SMA_FAST,
        "sma_slow":           SMA_SLOW,
        "min_dollar_vol_20d": MIN_DOLLAR_VOL_20D,
        "rules": [
            "price > 200d SMA",
            "50d SMA > 200d SMA",
            f"avg 20d $-vol ≥ ${MIN_DOLLAR_VOL_20D/1e6:.0f}M",
            f"ranked by {SCORE_LOOKBACK_DAYS}-day % return",
        ],
    }


# ── Persistence ─────────────────────────────────────────────────────────────

def save_momentum_list(result: dict) -> Path:
    JSON_PATH.parent.mkdir(parents=True, exist_ok=True)
    JSON_PATH.write_text(json.dumps(result, indent=2), encoding="utf-8")
    return JSON_PATH


def load_momentum_list() -> Optional[dict]:
    if not JSON_PATH.exists():
        return None
    try:
        return json.loads(JSON_PATH.read_text(encoding="utf-8"))
    except Exception as e:
        logger.warning(f"[momentum] failed to read {JSON_PATH}: {e}")
        return None


# ── CLI for one-shot manual refresh ─────────────────────────────────────────
if __name__ == "__main__":
    import sys
    n_arg = int(sys.argv[1]) if len(sys.argv) > 1 else 20
    r = pick_momentum_top_n(n=n_arg)
    print(json.dumps(r, indent=2))
    if r.get("tickers"):
        save_momentum_list(r)
        print(f"\nSaved to {JSON_PATH}")
