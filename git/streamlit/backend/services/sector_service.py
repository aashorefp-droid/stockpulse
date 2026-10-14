import os
import math
from datetime import date, datetime, timedelta
from typing import Optional, List, Dict, Any
import numpy as np
import pandas as pd
import yfinance as yf

# 11 Standard S&P Sectors
SECTOR_ETFS_DICT = {
    "XLK": {"name": "Technology", "emoji": "💻", "stocks": ["AAPL", "MSFT", "NVDA", "AVGO", "AMD", "CRM", "ADBE", "ORCL", "CSCO", "ACN"]},
    "XLF": {"name": "Financials", "emoji": "🏦", "stocks": ["JPM", "V", "MA", "BAC", "WFC", "GS", "MS", "SPGI", "BLK", "AXP"]},
    "XLE": {"name": "Energy", "emoji": "⛽", "stocks": ["XOM", "CVX", "COP", "EOG", "SLB", "MPC", "PSX", "VLO", "OXY", "HAL"]},
    "XLV": {"name": "Healthcare", "emoji": "🏥", "stocks": ["UNH", "JNJ", "LLY", "PFE", "ABBV", "MRK", "TMO", "ABT", "DHR", "BMY"]},
    "XLY": {"name": "Consumer Disc", "emoji": "🛒", "stocks": ["AMZN", "TSLA", "HD", "MCD", "NKE", "LOW", "SBUX", "TJX", "BKNG", "CMG"]},
    "XLI": {"name": "Industrials", "emoji": "🏭", "stocks": ["CAT", "UNP", "HON", "UPS", "BA", "RTX", "DE", "LMT", "GE", "MMM"]},
    "XLP": {"name": "Cons Staples", "emoji": "🧴", "stocks": ["PG", "KO", "PEP", "COST", "WMT", "PM", "MO", "CL", "MDLZ", "EL"]},
    "XLU": {"name": "Utilities", "emoji": "💡", "stocks": ["NEE", "DUK", "SO", "D", "AEP", "SRE", "EXC", "XEL", "PEG", "ED"]},
    "XLC": {"name": "Communication", "emoji": "📱", "stocks": ["META", "GOOGL", "GOOG", "NFLX", "DIS", "CMCSA", "VZ", "T", "TMUS", "CHTR"]},
    "XLB": {"name": "Materials", "emoji": "🧱", "stocks": ["LIN", "APD", "SHW", "ECL", "FCX", "NEM", "NUE", "DOW", "DD", "VMC"]},
    "XLRE": {"name": "Real Estate", "emoji": "🏠", "stocks": ["PLD", "AMT", "EQIX", "PSA", "CCI", "O", "WELL", "SPG", "DLR", "AVB"]},
}

# Fibonacci definitions
FIB_RET = [0, 0.236, 0.382, 0.5, 0.618, 0.786, 1.0]
FIB_EXT = [1.272, 1.414, 1.618, 2.0, 2.618]
FIB_NEG = [0.236, 0.382, 0.5, 0.618, 1.0]

FIB_COLUMN_NAMES = [
    "E 261.8%", "E 200.0%", "E 161.8%", "E 141.4%", "E 127.2%",
    "R 0.0%", "R 23.6%", "R 38.2%", "R 50.0%", "R 61.8%", "R 78.6%", "R 100.0%",
    "N -23.6%", "N -38.2%", "N -50.0%", "N -61.8%", "N -100.0%"
]

def calc_fib_levels(lo: float, hi: float) -> Dict[str, float]:
    rng = hi - lo
    lvls = {}
    for f in FIB_RET:
        lvls[f"R {f*100:.1f}%"] = hi - rng * f
    for f in FIB_EXT:
        lvls[f"E {f*100:.1f}%"] = lo + rng * f
    for f in FIB_NEG:
        lvls[f"N -{f*100:.1f}%"] = lo - rng * f
    return lvls

def get_sector_overview_service() -> List[Dict[str, Any]]:
    tickers = list(SECTOR_ETFS_DICT.keys())
    try:
        raw = yf.download(tickers, period="3mo", interval="1d", auto_adjust=True, progress=False, threads=True)
        close = raw["Close"] if "Close" in raw.columns else raw
    except Exception:
        return []

    items = []
    for sym, info in SECTOR_ETFS_DICT.items():
        if sym in close.columns:
            s = close[sym].dropna()
            if len(s) >= 2:
                curr = float(s.iloc[-1])
                prev = float(s.iloc[-2])
                p5 = float(s.iloc[-6]) if len(s) >= 6 else prev
                p20 = float(s.iloc[-20]) if len(s) >= 20 else p5

                chg_1d = round(((curr - prev) / prev) * 100, 2)
                chg_1w = round(((curr - p5) / p5) * 100, 2)
                chg_1m = round(((curr - p20) / p20) * 100, 2)
                momentum = round((chg_1d * 0.3) + (chg_1w * 0.5) + (chg_1m * 0.2), 2)

                items.append({
                    "ticker": sym,
                    "etf": sym,
                    "name": info["name"],
                    "emoji": info["emoji"],
                    "stocks": info["stocks"],
                    "price": round(curr, 2),
                    "change_1d": chg_1d,
                    "change_1w": chg_1w,
                    "change_1m": chg_1m,
                    "momentum": momentum,
                })
    items.sort(key=lambda x: x["momentum"], reverse=True)
    return items

def scan_all_sectors_service(as_of_date: Optional[str] = None) -> Dict[str, Any]:
    check_date = as_of_date or str(date.today())
    tickers = list(SECTOR_ETFS_DICT.keys())

    # 1. Download weekly bars for all 11 Sector ETFs
    try:
        raw_wk = yf.download(tickers, period="2y", interval="1wk", progress=False, threads=True)
    except Exception:
        raw_wk = None

    fib_rows = []
    golden_zone = []

    if raw_wk is not None and not raw_wk.empty:
        for etf, info in SECTOR_ETFS_DICT.items():
            try:
                if isinstance(raw_wk.columns, pd.MultiIndex):
                    df = pd.DataFrame({
                        "Close": raw_wk["Close"][etf],
                        "High": raw_wk["High"][etf],
                        "Low": raw_wk["Low"][etf],
                    }).dropna()
                else:
                    df = raw_wk.dropna()

                if df.empty:
                    continue

                bd = pd.Timestamp(check_date)
                df = df[df.index <= bd]
                if df.empty or len(df) < 5:
                    continue

                close_s = df["Close"].squeeze()
                high_s = df["High"].squeeze()
                low_s = df["Low"].squeeze()

                close = float(close_s.iloc[-1])
                wk_hi = float(high_s.iloc[-10:].max())
                wk_lo = float(low_s.iloc[-10:].min())
                wk_range = round(wk_hi - wk_lo, 2)
                wk_pos_pct = round(((close - wk_lo) / wk_range * 100) if wk_range > 0 else 50.0, 1)

                if wk_pos_pct >= 70:
                    wk_zone = "HIGH"
                    wk_zone_desc = "Near Weekly Hi — Extension territory"
                    wk_zone_clr = "#ff4d6a"
                elif wk_pos_pct <= 30:
                    wk_zone = "LOW"
                    wk_zone_desc = "Near Weekly Lo — Retrace territory"
                    wk_zone_clr = "#00e5a0"
                else:
                    wk_zone = "MID"
                    wk_zone_desc = "Mid Weekly Range — Balanced"
                    wk_zone_clr = "#f5c842"

                fund_bias = "⚖️ NEUTRAL"
                news_sentiment = "📰 NEUTRAL"
                conclusion = wk_zone_desc

                fib_levels = calc_fib_levels(wk_lo, wk_hi)

                row: Dict[str, Any] = {
                    "sector": info["name"],
                    "etf": etf,
                    "emoji": info["emoji"],
                    "as_of": check_date,
                    "close": round(close, 2),
                    "weekly_hi": round(wk_hi, 2),
                    "weekly_lo": round(wk_lo, 2),
                    "weekly_range": wk_range,
                    "weekly_pos_pct": wk_pos_pct,
                    "weekly_zone": wk_zone,
                    "weekly_zone_desc": wk_zone_desc,
                    "weekly_zone_clr": wk_zone_clr,
                    "fund_bias": fund_bias,
                    "news_sentiment": news_sentiment,
                    "conclusion": conclusion,
                    "fib_levels": {},
                }

                for fn in FIB_COLUMN_NAMES:
                    val = fib_levels.get(fn, float("nan"))
                    row["fib_levels"][fn] = round(val, 2) if not math.isnan(val) else None

                fib_rows.append(row)

                # Golden Zone check
                r38 = fib_levels.get("R 38.2%")
                r50 = fib_levels.get("R 50.0%")
                r61 = fib_levels.get("R 61.8%")
                if r38 and r61:
                    gz_lo, gz_hi = min(r38, r61), max(r38, r61)
                    if gz_lo <= close <= gz_hi:
                        golden_zone.append({
                            "sector": info["name"],
                            "etf": etf,
                            "emoji": info["emoji"],
                            "price": round(close, 2),
                            "r_38": round(r38, 2),
                            "r_50": round(r50, 2) if r50 else None,
                            "r_61": round(r61, 2),
                            "weekly_zone": wk_zone,
                        })
            except Exception:
                continue

    # 2. Performance & Momentum for Hot/Cold Sectors
    perf = get_sector_overview_service()
    hot_sectors = [s for s in perf if s["momentum"] > 0]
    cold_sectors = [s for s in perf if s["momentum"] <= 0]
    cold_sectors.sort(key=lambda x: x["momentum"])

    # 3. Best Stocks in Top 3 Hot Sectors
    hot_stocks = []
    for s in hot_sectors[:3]:
        hot_stocks.extend(s.get("stocks", []))
    hot_stocks = list(dict.fromkeys(hot_stocks))

    bullish_hot = []
    bearish_hot = []

    if hot_stocks:
        try:
            raw_stocks = yf.download(hot_stocks, period="3mo", interval="1d", auto_adjust=True, progress=False, threads=True)
            for tk in hot_stocks:
                try:
                    if isinstance(raw_stocks.columns, pd.MultiIndex):
                        c_series = raw_stocks["Close"][tk].dropna()
                        h_series = raw_stocks["High"][tk].dropna()
                        l_series = raw_stocks["Low"][tk].dropna()
                    else:
                        c_series = raw_stocks["Close"].dropna()
                        h_series = raw_stocks["High"].dropna()
                        l_series = raw_stocks["Low"].dropna()

                    if len(c_series) < 15:
                        continue

                    px = float(c_series.iloc[-1])
                    ma20 = float(c_series.rolling(20).mean().iloc[-1]) if len(c_series) >= 20 else px
                    ma50 = float(c_series.rolling(50).mean().iloc[-1]) if len(c_series) >= 50 else ma20

                    tr = np.maximum(
                        h_series.iloc[-14:] - l_series.iloc[-14:],
                        np.maximum(
                            abs(h_series.iloc[-14:] - c_series.shift(1).iloc[-14:]),
                            abs(l_series.iloc[-14:] - c_series.shift(1).iloc[-14:])
                        )
                    )
                    atr14 = float(tr.mean()) if len(tr) > 0 else (px * 0.02)
                    if atr14 <= 0:
                        atr14 = px * 0.02

                    delta = c_series.diff()
                    gain = delta.clip(lower=0).rolling(14).mean().iloc[-1]
                    loss = (-delta.clip(upper=0)).rolling(14).mean().iloc[-1]
                    rs = gain / loss if loss != 0 else 1
                    rsi14 = 100 - (100 / (1 + rs))

                    score = 0
                    if px > ma20: score += 1
                    if ma20 > ma50: score += 1
                    if rsi14 > 50: score += 1
                    if rsi14 > 60: score += 1
                    if px < ma20: score -= 1
                    if ma20 < ma50: score -= 1
                    if rsi14 < 50: score -= 1
                    if rsi14 < 40: score -= 1

                    if score >= 2:
                        verdict = "BULLISH"
                        confidence = "HIGH" if score >= 3 else "MED"
                        stop_loss = round(px - (1.5 * atr14), 2)
                        target1 = round(px + (2.2 * atr14), 2)
                        t1_days = max(2, int(round((target1 - px) / (atr14 * 0.5))))
                        bullish_hot.append({
                            "ticker": tk,
                            "price": round(px, 2),
                            "score": score,
                            "verdict": verdict,
                            "confidence": confidence,
                            "stop_loss": stop_loss,
                            "target1": target1,
                            "t1_days": t1_days,
                            "valuation": "Fair Value" if score == 2 else "Undervalued",
                            "valuation_color": "#00e5a0" if score >= 3 else "#7ccfb0",
                            "market_cap": "Large Cap",
                        })
                    elif score <= -2:
                        verdict = "BEARISH"
                        confidence = "HIGH" if score <= -3 else "MED"
                        stop_loss = round(px + (1.5 * atr14), 2)
                        target1 = round(px - (2.2 * atr14), 2)
                        t1_days = max(2, int(round((px - target1) / (atr14 * 0.5))))
                        bearish_hot.append({
                            "ticker": tk,
                            "price": round(px, 2),
                            "score": score,
                            "verdict": verdict,
                            "confidence": confidence,
                            "stop_loss": stop_loss,
                            "target1": target1,
                            "t1_days": t1_days,
                            "valuation": "Overvalued",
                            "valuation_color": "#ff4d6a",
                            "market_cap": "Large Cap",
                        })
                except Exception:
                    continue
        except Exception:
            pass

    bullish_hot.sort(key=lambda x: x["score"], reverse=True)
    bearish_hot.sort(key=lambda x: x["score"])

    by_zone = {
        "LOW": [r for r in fib_rows if r["weekly_zone"] == "LOW"],
        "MID": [r for r in fib_rows if r["weekly_zone"] == "MID"],
        "HIGH": [r for r in fib_rows if r["weekly_zone"] == "HIGH"],
    }

    return {
        "status": "ok",
        "as_of_date": check_date,
        "fib_report": {
            "all": fib_rows,
            "by_zone": by_zone,
            "golden_zone": golden_zone,
            "column_names": FIB_COLUMN_NAMES,
        },
        "performance": {
            "hot_sectors": hot_sectors,
            "cold_sectors": cold_sectors,
            "all_sectors": perf,
        },
        "stocks_analysis": {
            "bullish": bullish_hot[:5],
            "bearish": bearish_hot[:5],
            "total_scanned": len(hot_stocks),
        }
    }
