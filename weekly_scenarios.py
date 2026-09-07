from datetime import date, timedelta
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

try:
    import yfinance as yf
    YFINANCE_AVAILABLE = True
except Exception:
    YFINANCE_AVAILABLE = False

try:
    from news_sentiment import get_news_sentiment
    NEWS_SENTIMENT_AVAILABLE = True
except Exception:
    NEWS_SENTIMENT_AVAILABLE = False


SCENARIO_COLS = ["Mon", "Tue", "Wed", "Thu", "Fri"]


def _label_from_score(score: float) -> str:
    if score >= 3:
        return "BULLISH"
    if score >= 1:
        return "LEAN BULLISH"
    if score > -1:
        return "NEUTRAL"
    if score > -3:
        return "LEAN BEARISH"
    return "BEARISH"


def _compute_fundamental_bias(info: Dict) -> Tuple[str, float]:
    """Map fundamentals to (bias label, score)."""
    score = 0.0

    eg = info.get("earningsGrowth")
    if eg is not None:
        if eg > 0.10:
            score += 2
        elif eg > 0:
            score += 1
        elif eg > -0.10:
            score -= 1
        else:
            score -= 2

    rg = info.get("revenueGrowth")
    if rg is not None:
        if rg > 0.10:
            score += 1
        elif rg > 0:
            score += 0.5
        elif rg > -0.05:
            score -= 0.5
        else:
            score -= 1

    pe = info.get("trailingPE") or info.get("forwardPE")
    fpe = info.get("forwardPE")
    peg = info.get("pegRatio")
    if pe is not None:
        if pe < 15:
            score += 1
        elif pe > 40:
            score -= 1
    if fpe is not None and pe is not None and fpe < pe:
        score += 0.5
    if peg is not None:
        if 0 < peg < 1.5:
            score += 0.5
        elif peg > 3:
            score -= 0.5

    rec = (info.get("recommendationKey") or "").upper()
    if rec in ("STRONG_BUY", "BUY"):
        score += 1
    elif rec in ("SELL", "STRONG_SELL"):
        score -= 1.5

    current = info.get("currentPrice") or info.get("regularMarketPrice")
    target = info.get("targetMeanPrice")
    if current and target and current > 0:
        upside = ((target - current) / current) * 100
        if upside > 20:
            score += 1
        elif upside > 0:
            score += 0.5
        elif upside < -15:
            score -= 1

    pm = info.get("profitMargins")
    if pm is not None:
        if pm > 0.15:
            score += 1
        elif pm > 0:
            score += 0.5
        else:
            score -= 1

    roe = info.get("returnOnEquity")
    if roe is not None:
        if roe > 0.15:
            score += 0.5
        elif roe < 0:
            score -= 0.5

    de = info.get("debtToEquity")
    if de is not None:
        if de < 50:
            score += 0.5
        elif de > 150:
            score -= 1

    return _label_from_score(score), score


def _safe_float(v) -> Optional[float]:
    try:
        return float(v)
    except Exception:
        return None


def _classify_scenario(direction: str, entry: float, open_px: float, stop_px: float) -> str:
    """Map open vs entry/stop to ONE/OBE/OPS/GAP."""
    gap_thr = entry * 0.005
    if direction == "LONG":
        if (open_px - entry) > gap_thr:
            return "GAP"
        if open_px >= entry:
            return "ONE"
        if open_px > stop_px:
            return "OBE"
        return "OPS"

    if (entry - open_px) > gap_thr:
        return "GAP"
    if open_px <= entry:
        return "ONE"
    if open_px < stop_px:
        return "OBE"
    return "OPS"


def _monday_of(d: date) -> date:
    return d - timedelta(days=d.weekday())


def _next_trading_day(d: date, daily_dates: List[date]) -> Optional[date]:
    for dt in daily_dates:
        if dt > d:
            return dt
    return None


def _calc_atr_14(df: pd.DataFrame) -> Optional[float]:
    if df is None or len(df) < 15:
        return None
    high = df["High"].astype(float)
    low = df["Low"].astype(float)
    close = df["Close"].astype(float)
    prev_close = close.shift(1)
    tr = pd.concat(
        [
            high - low,
            (high - prev_close).abs(),
            (low - prev_close).abs(),
        ],
        axis=1,
    ).max(axis=1)
    atr = tr.rolling(14).mean().iloc[-1]
    if np.isnan(atr):
        return None
    return float(atr)


def _fetch_earnings_fib_snapshot(tk, asof_date: date) -> Tuple[Optional[float], Optional[float], Optional[float], Optional[float]]:
    """
    Mirror Stock Analysis (with Options) swing logic:
    swing window = from last earnings date to asof_date (inclusive).
    Returns (fib0=swing_lo, fib100=swing_hi, price_move, price_move_pct).
    """
    try:
        hist = tk.history(start=str(asof_date - timedelta(days=800)), end=str(asof_date + timedelta(days=1)))
        if hist is None or hist.empty:
            return None, None, None, None

        hist = hist.copy()
        hist.index = pd.to_datetime(hist.index)

        # Pull earnings report dates available up to as-of date.
        report_dates: List[date] = []
        try:
            edf = tk.earnings_dates
            if edf is not None and not edf.empty:
                for idx in edf.index:
                    d = idx.date() if hasattr(idx, "date") else idx
                    if d <= asof_date:
                        report_dates.append(d)
        except Exception:
            report_dates = []

        report_dates = sorted(set(report_dates))

        # Use last earnings date as swing start — matches Stock Analysis (with Options) logic.
        if report_dates:
            swing_start = report_dates[-1]
        else:
            # Fallback: trailing 90 days (matches Stock Analysis fallback)
            swing_start = asof_date - timedelta(days=90)

        swing_slice = hist[hist.index.date >= swing_start]
        if swing_slice.empty or len(swing_slice) < 2:
            # Secondary fallback: trailing 90 days
            swing_slice = hist.tail(90)
        if swing_slice.empty:
            return None, None, None, None

        swing_hi = float(swing_slice["High"].max())
        swing_lo = float(swing_slice["Low"].min())
        if not np.isfinite(swing_hi) or not np.isfinite(swing_lo):
            return None, None, None, None

        price_move = swing_hi - swing_lo
        price_move_pct = (price_move / swing_lo) * 100 if swing_lo > 0 else None
        return swing_lo, swing_hi, price_move, price_move_pct
    except Exception:
        return None, None, None, None


def _build_summary_row(ticker: str, asof_date: date, include_fe_fib: bool = True) -> Dict:
    tk = yf.Ticker(ticker)

    info = {}
    try:
        info = tk.info or {}
    except Exception:
        info = {}

    fundamental_bias, fundamental_score = _compute_fundamental_bias(info) if info else ("N/A", 0.0)

    news_label = "No"
    if NEWS_SENTIMENT_AVAILABLE:
        try:
            news_label = get_news_sentiment(ticker)
        except Exception:
            news_label = "No"

    wk_start = _monday_of(asof_date)
    hist = tk.history(start=str(wk_start - timedelta(days=2)), end=str(asof_date + timedelta(days=1)))

    if include_fe_fib:
        fe_fib0, fe_fib100, fe_price_move, fe_price_move_pct = _fetch_earnings_fib_snapshot(tk, asof_date)
    else:
        fe_fib0, fe_fib100, fe_price_move, fe_price_move_pct = None, None, None, None

    if hist is None or hist.empty:
        return {
            "Ticker": ticker,
            "LivePrice": np.nan,
            "FundamentalBias": fundamental_bias,
            "News": news_label,
            "EarningsBias": "N/A",
            "FE Fib(0%)": fe_fib0,
            "FE Fib(100%)": fe_fib100,
            "FE PriceMove": fe_price_move,
            "FE PriceMove%": fe_price_move_pct,
            "Fib(0%)": np.nan,
            "Fib(100%)": np.nan,
            "Price_move_to_0%": np.nan,
            "Price_move_to_100%": np.nan,
            "Price_move%_to_0%": np.nan,
            "Price_move%_to_100%": np.nan,
        }

    idx_dates = np.array([i.date() if hasattr(i, "date") else i for i in hist.index])
    wtd = hist[idx_dates >= wk_start]
    if wtd.empty:
        wtd = hist

    fib_0 = float(wtd["Low"].min())
    fib_100 = float(wtd["High"].max())
    price = float(wtd["Close"].iloc[-1])

    fib_score = 0.0
    if fib_100 > fib_0:
        fib_pos = ((price - fib_0) / (fib_100 - fib_0)) * 100.0
        # Earnings bias blend rule: stronger when both fundamentals and Fib align.
        # Near weekly low is bullish setup, near weekly high is bearish setup.
        if fib_pos <= 38.2:
            fib_score = 1.0
        elif fib_pos >= 61.8:
            fib_score = -1.0

    earnings_bias = _label_from_score(fundamental_score + fib_score)

    to_0 = fib_0 - price
    to_100 = fib_100 - price
    to_0_pct = (to_0 / price) * 100 if price else np.nan
    to_100_pct = (to_100 / price) * 100 if price else np.nan

    return {
        "Ticker": ticker,
        "LivePrice": round(price, 2),
        "FundamentalBias": fundamental_bias,
        "News": news_label,
        "EarningsBias": earnings_bias,
        "FE Fib(0%)": round(fe_fib0, 2) if fe_fib0 is not None else np.nan,
        "FE Fib(100%)": round(fe_fib100, 2) if fe_fib100 is not None else np.nan,
        "FE PriceMove": round(fe_price_move, 2) if fe_price_move is not None else np.nan,
        "FE PriceMove%": round(fe_price_move_pct, 2) if fe_price_move_pct is not None else np.nan,
        "Fib(0%)": round(fib_0, 2),
        "Fib(100%)": round(fib_100, 2),
        "Price_move_to_0%": round(to_0, 2),
        "Price_move_to_100%": round(to_100, 2),
        "Price_move%_to_0%": round(to_0_pct, 2),
        "Price_move%_to_100%": round(to_100_pct, 2),
    }


def _build_weekly_scenario_row(ticker: str, asof_date: date) -> Dict:
    week_start = _monday_of(asof_date)
    week_end = week_start + timedelta(days=4)

    # Pull extra history so ATR and previous close are stable.
    hist = yf.Ticker(ticker).history(
        start=str(week_start - timedelta(days=45)),
        end=str(week_end + timedelta(days=2)),
    )

    row = {"Ticker": ticker, "Mon": "N/A", "Tue": "N/A", "Wed": "N/A", "Thu": "N/A", "Fri": "N/A"}
    if hist is None or hist.empty:
        return row

    hist = hist.copy().sort_index()
    hist.index = pd.to_datetime(hist.index)

    weekdays = [week_start + timedelta(days=i) for i in range(5)]
    day_map = {
        0: "Mon",
        1: "Tue",
        2: "Wed",
        3: "Thu",
        4: "Fri",
    }

    for d in weekdays:
        this_day = hist[hist.index.date == d]
        prev_hist = hist[hist.index.date < d]
        if this_day.empty or prev_hist.empty:
            continue

        prev_day = prev_hist.iloc[-1]
        entry = _safe_float(prev_day.get("Close"))
        prev_open = _safe_float(prev_day.get("Open"))
        prev_close = _safe_float(prev_day.get("Close"))
        today_open = _safe_float(this_day.iloc[0].get("Open"))
        if entry is None or prev_open is None or prev_close is None or today_open is None:
            continue

        direction = "LONG" if prev_close >= prev_open else "SHORT"

        atr_src = prev_hist.tail(30)
        atr14 = _calc_atr_14(atr_src)
        if atr14 is None:
            atr14 = max(entry * 0.01, 0.01)

        stop_dist = atr14 * 0.3
        stop_px = (entry - stop_dist) if direction == "LONG" else (entry + stop_dist)

        scen = _classify_scenario(direction, entry, today_open, stop_px)
        row[day_map[d.weekday()]] = scen

    return row


def build_weekly_tables(tickers: List[str], asof_date: Optional[date] = None) -> Tuple[pd.DataFrame, pd.DataFrame]:
    """
    Return:
    1) Summary table with FundamentalBias + both weekly Fib and Earnings Fib columns.
    2) Week scenario table with ONE/OBE/OPS/GAP across Mon-Fri.
    Used by 'Generate Earnings Scenario' (shows both weekly + earnings fib).
    """
    if not YFINANCE_AVAILABLE:
        raise RuntimeError("yfinance is required for weekly scenario tables")

    if asof_date is None:
        asof_date = date.today()

    symbols = []
    for t in tickers:
        s = str(t).strip().upper()
        if s and s not in symbols:
            symbols.append(s)

    summary_rows = []
    scenario_rows = []
    for t in symbols:
        try:
            summary_rows.append(_build_summary_row(t, asof_date, include_fe_fib=True))
        except Exception:
            summary_rows.append(
                {
                    "Ticker": t,
                    "LivePrice": np.nan,
                    "FundamentalBias": "N/A",
                    "News": "No",
                    "EarningsBias": "N/A",
                    "FE Fib(0%)": np.nan,
                    "FE Fib(100%)": np.nan,
                    "FE PriceMove": np.nan,
                    "FE PriceMove%": np.nan,
                    "Fib(0%)": np.nan,
                    "Fib(100%)": np.nan,
                    "Price_move_to_0%": np.nan,
                    "Price_move_to_100%": np.nan,
                    "Price_move%_to_0%": np.nan,
                    "Price_move%_to_100%": np.nan,
                }
            )

        try:
            scenario_rows.append(_build_weekly_scenario_row(t, asof_date))
        except Exception:
            scenario_rows.append({"Ticker": t, "Mon": "N/A", "Tue": "N/A", "Wed": "N/A", "Thu": "N/A", "Fri": "N/A"})

    summary_df = pd.DataFrame(summary_rows)
    scenario_df = pd.DataFrame(scenario_rows)

    summary_cols = [
        "Ticker",
        "LivePrice",
        "FundamentalBias",
        "News",
        "EarningsBias",
        "FE Fib(0%)",
        "FE Fib(100%)",
        "FE PriceMove",
        "FE PriceMove%",
        "Fib(0%)",
        "Fib(100%)",
        "Price_move_to_0%",
        "Price_move_to_100%",
        "Price_move%_to_0%",
        "Price_move%_to_100%",
    ]
    for c in summary_cols:
        if c not in summary_df.columns:
            summary_df[c] = np.nan
    summary_df = summary_df[summary_cols]

    for c in ["Ticker"] + SCENARIO_COLS:
        if c not in scenario_df.columns:
            scenario_df[c] = "N/A"
    scenario_df = scenario_df[["Ticker"] + SCENARIO_COLS]

    return summary_df, scenario_df


def build_weekly_only_tables(tickers: List[str], asof_date: Optional[date] = None) -> Tuple[pd.DataFrame, pd.DataFrame]:
    """
    Return:
    1) Summary table with FundamentalBias + weekly Fib columns ONLY (no earnings fib, faster).
    2) Week scenario table with ONE/OBE/OPS/GAP across Mon-Fri.
    Used by 'Generate Weekly Scenarios' button.
    """
    if not YFINANCE_AVAILABLE:
        raise RuntimeError("yfinance is required for weekly scenario tables")

    if asof_date is None:
        asof_date = date.today()

    symbols = []
    for t in tickers:
        s = str(t).strip().upper()
        if s and s not in symbols:
            symbols.append(s)

    summary_rows = []
    scenario_rows = []
    for t in symbols:
        try:
            summary_rows.append(_build_summary_row(t, asof_date, include_fe_fib=False))
        except Exception:
            summary_rows.append(
                {
                    "Ticker": t,
                    "LivePrice": np.nan,
                    "FundamentalBias": "N/A",
                    "News": "No",
                    "EarningsBias": "N/A",
                    "Fib(0%)": np.nan,
                    "Fib(100%)": np.nan,
                    "Price_move_to_0%": np.nan,
                    "Price_move_to_100%": np.nan,
                    "Price_move%_to_0%": np.nan,
                    "Price_move%_to_100%": np.nan,
                }
            )

        try:
            scenario_rows.append(_build_weekly_scenario_row(t, asof_date))
        except Exception:
            scenario_rows.append({"Ticker": t, "Mon": "N/A", "Tue": "N/A", "Wed": "N/A", "Thu": "N/A", "Fri": "N/A"})

    summary_df = pd.DataFrame(summary_rows)
    scenario_df = pd.DataFrame(scenario_rows)

    weekly_cols = [
        "Ticker",
        "LivePrice",
        "FundamentalBias",
        "News",
        "EarningsBias",
        "Fib(0%)",
        "Fib(100%)",
        "Price_move_to_0%",
        "Price_move_to_100%",
        "Price_move%_to_0%",
        "Price_move%_to_100%",
    ]
    for c in weekly_cols:
        if c not in summary_df.columns:
            summary_df[c] = np.nan
    summary_df = summary_df[weekly_cols]

    for c in ["Ticker"] + SCENARIO_COLS:
        if c not in scenario_df.columns:
            scenario_df[c] = "N/A"
    scenario_df = scenario_df[["Ticker"] + SCENARIO_COLS]

    return summary_df, scenario_df
