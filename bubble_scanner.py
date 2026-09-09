"""
bubble_scanner.py  --  Bubble Detection & Risk Analysis

Detects bubble-like price behavior using:
  - Price vs moving-average overvaluation
  - Rolling 20-day returns
  - Rolling volatility (annualized)
  - Composite bubble-risk score

Usage:
    from bubble_scanner import analyze_ticker, analyze_multiple
    result = analyze_ticker("NVDA")
"""

import numpy as np
import pandas as pd
import yfinance as yf


# ═════════════════════════════════════════════════════════════════════════════
#  METRICS
# ═════════════════════════════════════════════════════════════════════════════

def calculate_bubble_metrics(df: pd.DataFrame) -> pd.DataFrame:
    """Add bubble-detection columns to an OHLCV DataFrame."""
    df = df.copy()
    df["Returns"] = df["Close"].pct_change()
    df["Rolling_20D_Return"] = df["Close"].pct_change(20)
    df["Rolling_50D_Return"] = df["Close"].pct_change(50)
    df["Rolling_Volatility"] = df["Returns"].rolling(20).std() * np.sqrt(252)
    df["SMA_50"] = df["Close"].rolling(50).mean()
    df["SMA_200"] = df["Close"].rolling(200).mean()
    df["Price_SMA50_Ratio"] = df["Close"] / df["SMA_50"]
    df["Price_SMA200_Ratio"] = df["Close"] / df["SMA_200"]
    df["Log_Price"] = np.log(df["Close"])
    df["Log_Returns"] = df["Log_Price"].diff()

    # Composite risk score
    df["Bubble_Risk_Score"] = (
        (df["Price_SMA50_Ratio"] - 1) * 100
        + df["Rolling_20D_Return"] * 1000
        + df["Rolling_Volatility"] * 100
    )
    return df.dropna()


# ═════════════════════════════════════════════════════════════════════════════
#  BUBBLE PERIOD DETECTION
# ═════════════════════════════════════════════════════════════════════════════

def identify_bubble_periods(
    df: pd.DataFrame,
    return_thresh: float = 0.15,
    valuation_thresh: float = 1.3,
    vol_thresh: float = 0.4,
    gap_days: int = 30,
) -> list[tuple]:
    """
    Return list of (start_date, end_date) tuples for bubble-like periods.
    Criteria (all must be true simultaneously):
      - 20-day return  > return_thresh   (default 15 %)
      - Price/SMA50    > valuation_thresh (default 1.3)
      - Ann. volatility > vol_thresh      (default 40 %)
    Consecutive days within `gap_days` are merged into one period.
    """
    mask = (
        (df["Rolling_20D_Return"] > return_thresh)
        & (df["Price_SMA50_Ratio"] > valuation_thresh)
        & (df["Rolling_Volatility"] > vol_thresh)
    )
    if not mask.any():
        return []

    bubble_dates = df.index[mask]
    periods: list[tuple] = []
    for d in bubble_dates:
        if not periods or (d - periods[-1][1]).days > gap_days:
            periods.append((d, d))
        else:
            periods[-1] = (periods[-1][0], d)
    return periods


# ═════════════════════════════════════════════════════════════════════════════
#  SINGLE-TICKER ANALYSIS
# ═════════════════════════════════════════════════════════════════════════════

def analyze_ticker(ticker: str, period: str = "3y", as_of_date=None) -> dict | None:
    """
    Full bubble analysis for one ticker.
    as_of_date: optional date/datetime — truncate data to this date (backdate).
    Returns a dict with summary metrics + the enriched DataFrame,
    or None on failure.
    """
    try:
        tk = yf.Ticker(ticker)
        raw = tk.history(period=period, auto_adjust=True)
        if raw.empty or len(raw) < 60:
            return None
    except Exception:
        return None

    # Backdate: truncate data up to as_of_date
    if as_of_date is not None:
        as_of_ts = pd.Timestamp(as_of_date)
        if raw.index.tz is not None:
            as_of_ts = as_of_ts.tz_localize(raw.index.tz)
        raw = raw[raw.index <= as_of_ts]
        if raw.empty or len(raw) < 60:
            return None

    df = calculate_bubble_metrics(raw)
    if df.empty:
        return None

    periods = identify_bubble_periods(df)

    price = df["Close"].iloc[-1]
    sma50 = df["SMA_50"].iloc[-1]
    sma200 = df["SMA_200"].iloc[-1]
    overval_50 = (price / sma50 - 1) * 100 if sma50 else 0
    overval_200 = (price / sma200 - 1) * 100 if sma200 else 0
    ret_20d = df["Rolling_20D_Return"].iloc[-1] * 100
    ret_50d = df["Rolling_50D_Return"].iloc[-1] * 100 if not np.isnan(df["Rolling_50D_Return"].iloc[-1]) else 0
    vol = df["Rolling_Volatility"].iloc[-1] * 100
    risk_score = df["Bubble_Risk_Score"].iloc[-1]
    max_risk = df["Bubble_Risk_Score"].max()
    max_ret_20d = df["Rolling_20D_Return"].max() * 100
    max_overval = (df["Price_SMA50_Ratio"].max() - 1) * 100

    # Current risk label
    if overval_50 > 40 or risk_score > 80:
        risk_label = "HIGH"
    elif overval_50 > 20 or risk_score > 50:
        risk_label = "MEDIUM"
    elif overval_50 > 10 or risk_score > 30:
        risk_label = "ELEVATED"
    else:
        risk_label = "LOW"

    # Trend assessment
    if price > sma50 > sma200:
        trend = "UPTREND"
    elif price < sma50 < sma200:
        trend = "DOWNTREND"
    elif price > sma50:
        trend = "RECOVERING"
    else:
        trend = "WEAKENING"

    return {
        "ticker": ticker,
        "price": round(price, 2),
        "sma_50": round(sma50, 2),
        "sma_200": round(sma200, 2),
        "overval_50": round(overval_50, 1),
        "overval_200": round(overval_200, 1),
        "ret_20d": round(ret_20d, 1),
        "ret_50d": round(ret_50d, 1),
        "volatility": round(vol, 1),
        "risk_score": round(risk_score, 1),
        "max_risk_score": round(max_risk, 1),
        "max_ret_20d": round(max_ret_20d, 1),
        "max_overval": round(max_overval, 1),
        "risk_label": risk_label,
        "trend": trend,
        "bubble_periods": len(periods),
        "bubble_period_details": [
            {
                "start": s.strftime("%Y-%m-%d"),
                "end": e.strftime("%Y-%m-%d"),
                "days": (e - s).days,
            }
            for s, e in periods
        ],
        # DataFrame for charting
        "_df": df,
        "_periods": periods,
    }


# ═════════════════════════════════════════════════════════════════════════════
#  MULTI-TICKER
# ═════════════════════════════════════════════════════════════════════════════

def analyze_multiple(tickers: list[str], period: str = "3y", as_of_date=None, max_workers: int = 4) -> list[dict]:
    results = []
    if not tickers:
        return results
    import concurrent.futures
    with concurrent.futures.ThreadPoolExecutor(max_workers=min(max_workers, max(1, len(tickers)))) as pool:
        future_map = {pool.submit(analyze_ticker, t, period, as_of_date=as_of_date): t for t in tickers}
        for fut in concurrent.futures.as_completed(future_map):
            try:
                r = fut.result()
                if r:
                    results.append(r)
            except Exception:
                pass
    return results
