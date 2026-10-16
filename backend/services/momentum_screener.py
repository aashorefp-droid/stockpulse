"""
Momentum screener — picks the top-N momentum stocks from a broad candidate
universe and persists them to a JSON file (`backend/db/momentum_watchlist.json`)
that the scanner and scheduler read.

Screening rules (Minervini / O'Neil Stage 2 style):
  - Price > 200-day SMA          (long-term uptrend)
  - 50-day SMA > 200-day SMA     (Stage 2 confirmation)
  - 20-day avg dollar volume >= $100M  (institutional liquidity floor)
  - Ranked by 63-trading-day (~3 month) % return

Public API:
  pick_momentum_top_n(n=50) -> dict      # run the screen
  save_momentum_list(result)             # persist to JSON
  load_momentum_list() -> Optional[dict] # read latest JSON (None if none)
  JSON_PATH                              # the persisted file location
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

DEFAULT_UNIVERSE_KEYS = ("default", "tech", "mega_cap", "momentum")

# Broad institutional growth, AI, semi, cloud, fintech, energy & industrial pool
EXTENDED_MOMENTUM_POOL = [
    # AI / Semiconductors / Optical / Hardware
    "NVDA", "AVGO", "AMD", "TSM", "ARM", "MRVL", "MU", "AMAT", "LRCX", "KLAC",
    "ASML", "QCOM", "TXN", "ADI", "NXPI", "ON", "MPWR", "TER", "ENTG", "SNPS",
    "CDNS", "ANET", "SMCI", "DELL", "HPE", "VRT", "CLS", "ALAB", "CRDO", "COHR",
    "LITE", "AAOI", "CIEN", "FN", "IONQ", "RGTI", "QBTS", "SOUN", "BBAI", "SYM",
    # Cloud / Cyber / Enterprise Software
    "PLTR", "APP", "CRWD", "PANW", "FTNT", "ZS", "NET", "DDOG", "SNOW", "MDB",
    "NOW", "CRM", "ORCL", "MSFT", "ADBE", "INTU", "TEAM", "HUBS", "TTD", "IOT",
    "DUOL", "RDDT", "SPOT", "NFLX", "META", "GOOGL", "AMZN", "AAPL", "SHOP", "MELI",
    "SE", "CPNG", "DASH", "UBER", "ABNB", "BKNG", "CART", "RBLX", "U", "TEM",
    # Fintech / Crypto / High-Beta Financials
    "COIN", "HOOD", "MSTR", "SOFI", "AFRM", "UPST", "SQ", "PYPL", "NU", "TOST",
    "IBKR", "GS", "MS", "JPM", "BX", "KKR", "APO", "ARES", "V", "MA", "AXP",
    # Power / Nuclear / Space / Defense / Industrials
    "CEG", "VST", "GEV", "TLN", "NRG", "OKLO", "SMR", "CCJ", "LEU", "FSLR",
    "NEE", "RKLB", "ASTS", "LUNR", "JOBY", "ACHR", "AXON", "GE", "RTX", "LMT",
    "NOC", "GD", "HWM", "TDG", "HEI", "ETN", "PWR", "URI", "CAT", "DE", "TT",
    "IREN", "CORZ", "NBIS", "CIFR", "WULF", "MARA", "RIOT", "CLSK",
    # Consumer / Healthcare / Biotech Leaders
    "TSLA", "CVNA", "CAVA", "WING", "CMG", "SHAK", "BROS", "ONON", "DECK", "LULU",
    "NKE", "COST", "WMT", "TGT", "HD", "LOW", "LLY", "NVO", "VRTX", "REGN",
    "ISRG", "BSX", "SYK", "MDT", "ABBV", "AMGN", "GILD", "HIMS", "VKTX", "MRNA",
]

KNOWN_ETFS = {
    "SPY", "QQQ", "DIA", "IWM", "VTI", "ARKK", "XLK", "XLF", "XLE", "XLV",
    "XLI", "XLY", "XLP", "XLU", "XLB", "XLRE", "XLC", "SOXX", "SMH", "GLD",
    "SLV", "TLT", "HYG", "UVXY", "SQQQ", "TQQQ", "IBIT", "USO", "UNG",
}

JSON_PATH: Path = (
    Path(__file__).resolve().parent.parent / "db" / "momentum_watchlist.json"
)


def _gather_universe() -> list[str]:
    """Union of the configured watchlists + extended pool, ETFs excluded, deduped."""
    from backend.services.scanner import WATCHLISTS
    etfs = set(KNOWN_ETFS) | set(WATCHLISTS.get("etfs", []))
    seen: set[str] = set()
    out: list[str] = []
    for key in DEFAULT_UNIVERSE_KEYS:
        for t in WATCHLISTS.get(key, []):
            sym = str(t).strip().upper()
            if not sym or sym in etfs or sym in seen:
                continue
            seen.add(sym)
            out.append(sym)
    for t in EXTENDED_MOMENTUM_POOL:
        sym = str(t).strip().upper()
        if not sym or sym in etfs or sym in seen:
            continue
        seen.add(sym)
        out.append(sym)
    return out


def pick_momentum_top_n(n: int = 50,
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
            uni, period="1y", interval="1d",
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

    scored: list[tuple[str, float, float, float]] = []   # (ticker, 3mo_return, 1mo_return, $-vol)
    for t in uni:
        try:
            df = bars[t].dropna(how="all") if t in bars else None
            if df is None or df.empty or len(df) < SMA_SLOW:
                continue
            close = df["Close"].dropna()
            volume = df["Volume"].dropna()
            if len(close) < SMA_SLOW or len(close) < SCORE_LOOKBACK_DAYS + 1:
                continue

            current = float(close.iloc[-1])
            sma50 = float(close.iloc[-SMA_FAST:].mean())
            sma200 = float(close.iloc[-SMA_SLOW:].mean())
            # Uptrend gate: Stage 2 (Price > 200 SMA and 50 SMA > 200 SMA)
            if current <= sma200 or sma50 <= sma200:
                continue
            # Liquidity gate
            last20 = df.iloc[-20:]
            dvol = float((last20["Close"] * last20["Volume"]).mean())
            if dvol < MIN_DOLLAR_VOL_20D:
                continue
            # 3-month (63d) and 1-month (21d) momentum return
            ret_3m = (current / float(close.iloc[-SCORE_LOOKBACK_DAYS]) - 1) * 100
            ret_1m = (current / float(close.iloc[-21]) - 1) * 100 if len(close) >= 22 else ret_3m
            scored.append((t, ret_3m, ret_1m, dvol))
        except Exception as e:
            logger.debug(f"[momentum] {t} skipped: {type(e).__name__}: {e}")
            continue

    scored.sort(key=lambda x: x[1], reverse=True)
    top = scored[:max(1, n)]
    return {
        "tickers":        [t for t, _, _, _ in top],
        "scores":         {t: round(r3, 2) for t, r3, _, _ in top},
        "returns_1m":     {t: round(r1, 2) for t, _, r1, _ in top},
        "dollar_volumes": {t: int(dv)      for t, _, _, dv in top},
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
            "50d SMA > 200d SMA (Stage 2)",
            f"avg 20d $-vol >= ${MIN_DOLLAR_VOL_20D/1e6:.0f}M",
            f"ranked by {SCORE_LOOKBACK_DAYS}-day (~3M) % return",
        ],
    }


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


if __name__ == "__main__":
    import sys
    n_arg = int(sys.argv[1]) if len(sys.argv) > 1 else 50
    r = pick_momentum_top_n(n=n_arg)
    print(json.dumps(r, indent=2))
    if r.get("tickers"):
        save_momentum_list(r)
        print(f"\nSaved {len(r['tickers'])} momentum tickers to {JSON_PATH}")
