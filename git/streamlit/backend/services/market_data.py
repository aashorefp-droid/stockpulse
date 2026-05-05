"""Market data fetching: Alpaca (real-time) and yfinance (free fallback)."""
import time
import requests
import pandas as pd
from datetime import timedelta
from backend.config import ALPACA_DATA_BASE


def alpaca_get(endpoint: str, params: dict, api_key: str, api_secret: str) -> dict:
    headers = {
        "APCA-API-KEY-ID": api_key,
        "APCA-API-SECRET-KEY": api_secret,
    }
    url = ALPACA_DATA_BASE + endpoint
    for attempt in range(3):
        try:
            r = requests.get(url, params=params, headers=headers, timeout=15)
            if r.status_code == 429:
                time.sleep(5)
                continue
            if r.status_code in (401, 403):
                raise Exception(f"Alpaca auth error {r.status_code}: check API credentials")
            if r.status_code == 404:
                raise Exception(f"Alpaca 404: ticker not found")
            r.raise_for_status()
            return r.json()
        except Exception as e:
            if attempt == 2:
                raise
            if any(x in str(e) for x in ("401", "403", "404")):
                raise
            time.sleep(2)
    return {}


def get_daily_bars_alpaca(ticker: str, start_date: str, end_date: str, api_key: str, api_secret: str) -> pd.DataFrame:
    data = alpaca_get(
        f"/v2/stocks/{ticker}/bars",
        {"timeframe": "1Day", "start": f"{start_date}T00:00:00Z", "end": f"{end_date}T23:59:59Z",
         "adjustment": "all", "limit": 10000, "sort": "asc", "feed": "iex"},
        api_key, api_secret,
    )
    bars = data.get("bars", [])
    if not bars:
        return pd.DataFrame()
    df = pd.DataFrame(bars)
    df["date"] = pd.to_datetime(df["t"]).dt.date
    df = df.rename(columns={"o": "open", "h": "high", "l": "low", "c": "close", "v": "volume"})
    return df.set_index("date")[["open", "high", "low", "close", "volume"]]


def get_hourly_bars_alpaca(ticker: str, start_date: str, end_date: str, api_key: str, api_secret: str) -> pd.DataFrame:
    data = alpaca_get(
        f"/v2/stocks/{ticker}/bars",
        {"timeframe": "1Hour", "start": f"{start_date}T00:00:00Z", "end": f"{end_date}T23:59:59Z",
         "adjustment": "all", "limit": 10000, "sort": "asc", "feed": "iex"},
        api_key, api_secret,
    )
    bars = data.get("bars", [])
    if not bars:
        return pd.DataFrame()
    df = pd.DataFrame(bars)
    df["dt_utc"] = pd.to_datetime(df["t"], utc=True)
    df["dt_et"] = df["dt_utc"].dt.tz_convert("America/New_York")
    df = df.rename(columns={"o": "open", "h": "high", "l": "low", "c": "close", "v": "volume"})
    return df.set_index("dt_et")[["open", "high", "low", "close", "volume"]]


def get_hourly_bars_yfinance(ticker: str, start_date: str, end_date: str) -> pd.DataFrame:
    try:
        import yfinance as yf
        tk = yf.Ticker(ticker)
        s = pd.to_datetime(start_date)
        e = pd.to_datetime(end_date) + timedelta(days=1)
        df = tk.history(start=s, end=e, interval="1h")
        if df.empty:
            return pd.DataFrame()
        if df.index.tz is None:
            df.index = df.index.tz_localize("America/New_York")
        else:
            df.index = df.index.tz_convert("America/New_York")
        df.columns = [c.lower() for c in df.columns]
        cols = [c for c in ["open", "high", "low", "close", "volume"] if c in df.columns]
        return df[cols]
    except Exception:
        return pd.DataFrame()


def get_ohlcv_for_chart(ticker: str, period: str = "6mo") -> list[dict]:
    """Return daily OHLCV as list of dicts for TradingView Lightweight Charts."""
    try:
        import yfinance as yf
        df = yf.Ticker(ticker).history(period=period, interval="1d")
        if df.empty:
            return []
        result = []
        for ts, row in df.iterrows():
            result.append({
                "time": ts.strftime("%Y-%m-%d"),
                "open": round(float(row["Open"]), 2),
                "high": round(float(row["High"]), 2),
                "low": round(float(row["Low"]), 2),
                "close": round(float(row["Close"]), 2),
                "volume": int(row["Volume"]),
            })
        return result
    except Exception:
        return []
