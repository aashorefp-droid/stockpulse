"""
Earnings Backtest — Alpaca/Polygon Edition
Strategy: Enter on earnings report day (AMC) at ~1:30 PM ET (12:30 PM CST)
          using the 9:30–1:30 ET 4H candle direction.
          Exit: next trading day close.
Data Sources:
  - Alpaca: Free real-time OHLCV bars (recommended)
  - Polygon: Free tier has ~7 day delay on hourly bars
"""

import streamlit as st
import requests
import pandas as pd
import numpy as np
import plotly.graph_objects as go
import plotly.express as px
from datetime import datetime, timedelta, date
import time
import io
import sqlite3
import os
import pytz

try:
    import yfinance as yf
    YFINANCE_AVAILABLE = True
except ImportError:
    YFINANCE_AVAILABLE = False

# ──────────────────────────────────────────────
# TRADE TRACKER DATABASE (SQLite)
# ──────────────────────────────────────────────

TRADE_DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "stockpulse_trades.db")


def _get_trade_db():
    """Get a connection to the trades database, creating tables if needed."""
    conn = sqlite3.connect(TRADE_DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("""
        CREATE TABLE IF NOT EXISTS trades (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ticker TEXT NOT NULL,
            direction TEXT NOT NULL,
            entry_price REAL NOT NULL,
            stop_loss REAL,
            target1 REAL,
            target2 REAL,
            verdict TEXT,
            confidence TEXT,
            score INTEGER,
            signals TEXT,
            t1_trading_days INTEGER,
            t2_trading_days INTEGER,
            entry_date TEXT NOT NULL,
            status TEXT DEFAULT 'OPEN',
            exit_price REAL,
            exit_date TEXT,
            pnl_pct REAL,
            outcome TEXT,
            t1_hit INTEGER DEFAULT 0,
            t2_hit INTEGER DEFAULT 0,
            stop_hit INTEGER DEFAULT 0,
            high_since_entry REAL,
            low_since_entry REAL,
            notes TEXT,
            created_at TEXT DEFAULT (datetime('now'))
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS holdings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ticker TEXT NOT NULL,
            quantity REAL NOT NULL,
            avg_cost REAL NOT NULL,
            added_date TEXT DEFAULT (date('now')),
            notes TEXT
        )
    """)
    conn.commit()
    return conn


# ── Holdings CRUD ──────────────────────────────────────
def save_holding(ticker, quantity, avg_cost, notes=None):
    conn = _get_trade_db()
    try:
        conn.execute("INSERT INTO holdings (ticker, quantity, avg_cost, notes) VALUES (?,?,?,?)",
                     (ticker.upper().strip(), quantity, avg_cost, notes))
        conn.commit()
        return True
    finally:
        conn.close()


def get_holdings():
    conn = _get_trade_db()
    try:
        rows = conn.execute("SELECT * FROM holdings ORDER BY ticker").fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def update_holding(holding_id, quantity=None, avg_cost=None, notes=None):
    conn = _get_trade_db()
    try:
        updates = {}
        if quantity is not None:
            updates["quantity"] = quantity
        if avg_cost is not None:
            updates["avg_cost"] = avg_cost
        if notes is not None:
            updates["notes"] = notes
        if not updates:
            return
        set_clause = ", ".join(f"{k} = ?" for k in updates)
        vals = list(updates.values()) + [holding_id]
        conn.execute(f"UPDATE holdings SET {set_clause} WHERE id = ?", vals)
        conn.commit()
    finally:
        conn.close()


def delete_holding(holding_id):
    conn = _get_trade_db()
    try:
        conn.execute("DELETE FROM holdings WHERE id=?", (holding_id,))
        conn.commit()
    finally:
        conn.close()


def save_trade(ticker, direction, entry_price, stop_loss=None, target1=None,
               target2=None, verdict=None, confidence=None, score=None,
               signals=None, t1_days=None, t2_days=None, notes=None):
    """Save a new trade to the database."""
    conn = _get_trade_db()
    try:
        conn.execute("""
            INSERT INTO trades (ticker, direction, entry_price, stop_loss, target1,
                target2, verdict, confidence, score, signals, t1_trading_days,
                t2_trading_days, entry_date, notes)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (ticker, direction, entry_price, stop_loss, target1, target2,
              verdict, confidence, score, signals, t1_days, t2_days,
              str(date.today()), notes))
        conn.commit()
        return True
    finally:
        conn.close()


def get_open_trades():
    """Get all open trades."""
    conn = _get_trade_db()
    try:
        rows = conn.execute(
            "SELECT * FROM trades WHERE status = 'OPEN' ORDER BY entry_date DESC"
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def get_all_trades():
    """Get all trades (open and closed)."""
    conn = _get_trade_db()
    try:
        rows = conn.execute(
            "SELECT * FROM trades ORDER BY entry_date DESC"
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def get_closed_trades():
    """Get only closed trades."""
    conn = _get_trade_db()
    try:
        rows = conn.execute(
            "SELECT * FROM trades WHERE status = 'CLOSED' ORDER BY exit_date DESC"
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def update_trade(trade_id, **kwargs):
    """Update trade fields by id."""
    conn = _get_trade_db()
    try:
        valid = {"status", "exit_price", "exit_date", "pnl_pct", "outcome",
                 "t1_hit", "t2_hit", "stop_hit", "high_since_entry",
                 "low_since_entry", "notes"}
        updates = {k: v for k, v in kwargs.items() if k in valid}
        if not updates:
            return
        set_clause = ", ".join(f"{k} = ?" for k in updates)
        vals = list(updates.values()) + [trade_id]
        conn.execute(f"UPDATE trades SET {set_clause} WHERE id = ?", vals)
        conn.commit()
    finally:
        conn.close()


def close_trade(trade_id, exit_price, outcome=None, notes=None):
    """Close a trade with exit price and calculate P&L."""
    conn = _get_trade_db()
    try:
        row = conn.execute("SELECT * FROM trades WHERE id = ?", (trade_id,)).fetchone()
        if not row:
            return
        trade = dict(row)
        entry = trade["entry_price"]
        direction = trade["direction"]
        if direction == "LONG":
            pnl_pct = round((exit_price - entry) / entry * 100, 2)
        else:
            pnl_pct = round((entry - exit_price) / entry * 100, 2)
        if outcome is None:
            outcome = "WIN" if pnl_pct > 0 else ("LOSS" if pnl_pct < 0 else "BREAKEVEN")
        update_fields = {
            "status": "CLOSED",
            "exit_price": exit_price,
            "exit_date": str(date.today()),
            "pnl_pct": pnl_pct,
            "outcome": outcome,
        }
        if notes:
            update_fields["notes"] = notes
        set_clause = ", ".join(f"{k} = ?" for k in update_fields)
        vals = list(update_fields.values()) + [trade_id]
        conn.execute(f"UPDATE trades SET {set_clause} WHERE id = ?", vals)
        conn.commit()
    finally:
        conn.close()


def delete_trade(trade_id):
    """Delete a trade by id."""
    conn = _get_trade_db()
    try:
        conn.execute("DELETE FROM trades WHERE id = ?", (trade_id,))
        conn.commit()
    finally:
        conn.close()


def check_trade_targets(trade, current_price, high_since=None, low_since=None):
    """
    Check if a trade has hit its targets or stop loss.
    Returns dict with updated fields.
    """
    entry = trade["entry_price"]
    direction = trade["direction"]
    t1 = trade.get("target1")
    t2 = trade.get("target2")
    stop = trade.get("stop_loss")

    updates = {}

    if direction == "LONG":
        if high_since and t1 and high_since >= t1:
            updates["t1_hit"] = 1
        if high_since and t2 and high_since >= t2:
            updates["t2_hit"] = 1
        if low_since and stop and low_since <= stop:
            updates["stop_hit"] = 1
        if current_price and t1 and current_price >= t1:
            updates["t1_hit"] = 1
        if current_price and t2 and current_price >= t2:
            updates["t2_hit"] = 1
        if current_price and stop and current_price <= stop:
            updates["stop_hit"] = 1
    else:  # SHORT
        if low_since and t1 and low_since <= t1:
            updates["t1_hit"] = 1
        if low_since and t2 and low_since <= t2:
            updates["t2_hit"] = 1
        if high_since and stop and high_since >= stop:
            updates["stop_hit"] = 1
        if current_price and t1 and current_price <= t1:
            updates["t1_hit"] = 1
        if current_price and t2 and current_price <= t2:
            updates["t2_hit"] = 1
        if current_price and stop and current_price >= stop:
            updates["stop_hit"] = 1

    if high_since:
        updates["high_since_entry"] = high_since
    if low_since:
        updates["low_since_entry"] = low_since

    return updates


# ──────────────────────────────────────────────
# TIMEZONE & INTRADAY TRACKING UTILITIES
# ──────────────────────────────────────────────

def get_cst_now():
    """Get current time in CST (Central Standard Time)."""
    utc_now = datetime.now(pytz.UTC)
    cst = pytz.timezone('US/Central')
    return utc_now.astimezone(cst)


def is_after_market_time(hour_cst, minute_cst=0):
    """Check if current time (CST) is at or after a specific market time."""
    now_cst = get_cst_now()
    market_time = now_cst.replace(hour=hour_cst, minute=minute_cst, second=0, microsecond=0)
    return now_cst >= market_time


def get_multiframe_bias_eval(ticker, entry_price, direction, target_date=None):
    """
    Evaluate bias using two timeframes:
      - Short: 10-min bars from 8:20 AM CST today (present bias)
      - Long:  4H candle bias from prior sessions (past bias / trend anchor)
      - After hours / weekends: falls back to daily data
    Open reference: 8:30 AM CST (9:30 AM ET) regular market open.
    Returns dict with bias info, alignment, conclusion, current_price.

    If target_date (datetime.date or datetime) is provided, evaluate bias
    as of that historical date instead of today.  Simulates "market hours"
    for the target date so intraday logic always runs.
    """
    try:
        import yfinance as yf

        cst_tz = pytz.timezone("America/Chicago")
        et_tz = pytz.timezone("America/New_York")

        is_backtest = target_date is not None
        if is_backtest:
            if isinstance(target_date, datetime):
                ref_date = target_date.date() if not hasattr(target_date, 'date') or callable(target_date.date) else target_date
                ref_date = target_date.date()
            else:
                ref_date = target_date  # already a date object
            # For backtest, always simulate market hours
            now_cst = cst_tz.localize(datetime(ref_date.year, ref_date.month, ref_date.day, 12, 0, 0))
        else:
            now_cst = get_cst_now()

        weekday = now_cst.weekday()  # 0=Mon .. 6=Sun
        hour = now_cst.hour
        minute = now_cst.minute

        is_weekend = weekday >= 5
        is_market_hours = is_backtest or ((not is_weekend) and ((hour > 8 or (hour == 8 and minute >= 20)) and hour < 15))

        tk = yf.Ticker(ticker)

        if is_weekend and not is_backtest:
            tf_short_label, tf_long_label = "1D", "5D"
            hist_short = tk.history(period="5d", interval="1d")
            hist_long  = tk.history(period="1mo", interval="1d")
            if hist_short.empty or hist_long.empty:
                return None
            short_bars = 1
            long_bars = 5
            current_price = float(hist_short["Close"].iloc[-1])
        elif is_market_hours:
            tf_short_label, tf_long_label = "10m", "4H"

            # ── Determine date range for yfinance ──
            if is_backtest:
                fetch_start = ref_date - timedelta(days=7)
                fetch_end   = ref_date + timedelta(days=1)
                hist_5m = tk.history(start=str(fetch_start), end=str(fetch_end), interval="5m", prepost=True)
            else:
                hist_5m = tk.history(period="5d", interval="5m", prepost=True)
            # Resample 5m -> 10m
            if not hist_5m.empty:
                hist_5m.index = pd.to_datetime(hist_5m.index)
                hist_10m = hist_5m.resample("10min").agg({
                    "Open": "first", "High": "max", "Low": "min", "Close": "last", "Volume": "sum"
                }).dropna()
            else:
                hist_10m = hist_5m
            if hist_10m.empty:
                return None

            today_820_cst = cst_tz.localize(datetime(now_cst.year, now_cst.month, now_cst.day, 8, 20, 0))
            today_820_et = today_820_cst.astimezone(et_tz)

            # For backtest: end-of-day cutoff so we get the full session
            if is_backtest:
                today_eod_cst = cst_tz.localize(datetime(now_cst.year, now_cst.month, now_cst.day, 15, 0, 0))
                today_eod_et = today_eod_cst.astimezone(et_tz)

            idx = hist_10m.index
            if idx.tz is None:
                idx = idx.tz_localize("America/New_York")
            else:
                idx = idx.tz_convert("America/New_York")

            if is_backtest:
                hist_short = hist_10m[(idx >= today_820_et) & (idx <= today_eod_et)]
            else:
                hist_short = hist_10m[idx >= today_820_et]

            if hist_short.empty:
                return None

            short_bars = len(hist_short)
            # Use 8:30 AM CST open as bias reference
            today_open = float(hist_short["Open"].iloc[0])
            entry_price = today_open
            current_price = float(hist_short["Close"].iloc[-1])

            # ── Long: past 4H candle bias (from hourly bars) ──
            if is_backtest:
                hist_1h = tk.history(start=str(fetch_start), end=str(fetch_end), interval="1h", prepost=True)
            else:
                hist_1h = tk.history(period="5d", interval="1h", prepost=True)
            if hist_1h.empty:
                return None
            hdf = hist_1h.copy()
            hdf.index = pd.to_datetime(hdf.index)
            bars_4h = hdf.resample("4h").agg({
                "Open": "first", "High": "max", "Low": "min", "Close": "last", "Volume": "sum"
            }).dropna()

            # Exclude current session bars — only use *past* 4H candles
            today_start_et = today_820_et
            b4h_idx = bars_4h.index
            if b4h_idx.tz is None:
                b4h_idx = b4h_idx.tz_localize("America/New_York")
            else:
                b4h_idx = b4h_idx.tz_convert("America/New_York")
            hist_long = bars_4h[b4h_idx < today_start_et]

            if hist_long.empty or len(hist_long) < 2:
                # Not enough past 4H data — use daily as fallback
                if is_backtest:
                    hist_long = tk.history(start=str(ref_date - timedelta(days=30)), end=str(ref_date), interval="1d")
                else:
                    hist_long = tk.history(period="1mo", interval="1d")
                if hist_long.empty:
                    return None
                long_bars = 3
                tf_long_label = "Daily"
            else:
                long_bars = min(len(hist_long), 3)  # Last 3 completed 4H candles
        else:
            # After hours weekday
            tf_short_label, tf_long_label = "4H", "1D"
            hist_short = tk.history(period="5d", interval="1h")
            hist_long  = tk.history(period="1mo", interval="1d")
            if hist_short.empty or hist_long.empty:
                return None
            # Resample to 4H
            hdf = hist_short.copy()
            hdf.index = pd.to_datetime(hdf.index)
            hist_short = hdf.resample("4h").agg({
                "Open": "first", "High": "max", "Low": "min", "Close": "last", "Volume": "sum"
            }).dropna()
            if hist_short.empty:
                return None
            short_bars = min(len(hist_short), 3)
            long_bars = 3
            current_price = float(hist_short["Close"].iloc[-1])

        def eval_simple_bias(df, entry, n_bars):
            """Bias: are recent n closes above/below entry?"""
            closes = df["Close"].tail(n_bars).values
            avg_close = float(np.mean(closes))
            vol = df["Volume"].tail(n_bars).values
            avg_vol = float(np.mean(vol))

            if direction == "LONG":
                bias = "BULLISH" if avg_close > entry else "BEARISH"
            else:
                bias = "BEARISH" if avg_close < entry else "BULLISH"

            vol_bias = avg_vol / closes[0] if closes[0] > 0 else 0
            return bias, vol_bias, avg_close

        bias_short, vol_bias_short, _ = eval_simple_bias(hist_short, entry_price, short_bars)
        bias_long,  vol_bias_long,  _ = eval_simple_bias(hist_long,  entry_price, long_bars)

        alignment = "CONFIRMED" if bias_short == bias_long else "DIVERGED"

        if alignment == "CONFIRMED":
            conclusion = f"✅ BIAS CONFIRMED: Both {tf_short_label} & {tf_long_label} show {bias_short}"
            color = "#00e5a0"
        else:
            conclusion = f"⚠️ BIAS DIVERGED: {tf_short_label}={bias_short}, {tf_long_label}={bias_long}"
            color = "#f5c842"

        return {
            "bias_10min": bias_short,
            "vol_bias_10min": round(vol_bias_short, 4),
            "bias_30min": bias_long,           # key kept for compat — now holds 4H bias
            "vol_bias_30min": round(vol_bias_long, 4),
            "tf_short": tf_short_label,
            "tf_long": tf_long_label,
            "alignment": alignment,
            "conclusion": conclusion,
            "color": color,
            "current_price": round(current_price, 2)
        }
    except Exception as e:
        return None


def get_830_bias_eval(ticker, direction, target_date=None):
    """
    8:30 AM CST entry confirmation bias.
    Returns 10m, 30m, and 4H biases — all anchored to the target day's 8:30 AM CST open.
    Live: only works during market hours. Returns None otherwise.
    Backtest: pass target_date (date or datetime) to evaluate a historical session.
    """
    try:
        import yfinance as yf

        cst_tz = pytz.timezone("America/Chicago")
        et_tz = pytz.timezone("America/New_York")

        is_backtest = target_date is not None
        if is_backtest:
            ref_date = target_date.date() if isinstance(target_date, datetime) else target_date
            now_cst = cst_tz.localize(datetime(ref_date.year, ref_date.month, ref_date.day, 12, 0, 0))
        else:
            now_cst = get_cst_now()

        weekday = now_cst.weekday()
        hour = now_cst.hour
        minute = now_cst.minute

        is_weekend = weekday >= 5
        # Skip weekend / pre-8:30 for live mode only
        if not is_backtest and (is_weekend or (hour < 8 or (hour == 8 and minute < 30))):
            return None

        tk = yf.Ticker(ticker)

        # Date anchors
        today_830_cst = cst_tz.localize(datetime(now_cst.year, now_cst.month, now_cst.day, 8, 30, 0))
        today_830_et = today_830_cst.astimezone(et_tz)
        today_820_cst = cst_tz.localize(datetime(now_cst.year, now_cst.month, now_cst.day, 8, 20, 0))
        today_820_et = today_820_cst.astimezone(et_tz)

        # For backtest: cap at end of regular session
        if is_backtest:
            today_eod_cst = cst_tz.localize(datetime(now_cst.year, now_cst.month, now_cst.day, 15, 0, 0))
            today_eod_et = today_eod_cst.astimezone(et_tz)
            fetch_start = ref_date - timedelta(days=7)
            fetch_end   = ref_date + timedelta(days=1)

        def _filter_from(df, cutoff_et):
            idx = df.index
            if idx.tz is None:
                idx = idx.tz_localize("America/New_York")
            else:
                idx = idx.tz_convert("America/New_York")
            if is_backtest:
                return df[(idx >= cutoff_et) & (idx <= today_eod_et)]
            return df[idx >= cutoff_et]

        # ── 10m bars ──
        if is_backtest:
            hist_5m_raw = tk.history(start=str(fetch_start), end=str(fetch_end), interval="5m", prepost=True)
        else:
            hist_5m_raw = tk.history(period="5d", interval="5m", prepost=True)
        # Resample 5m -> 10m
        if not hist_5m_raw.empty:
            hist_5m_raw.index = pd.to_datetime(hist_5m_raw.index)
            hist_10m_raw = hist_5m_raw.resample("10min").agg({
                "Open": "first", "High": "max", "Low": "min", "Close": "last", "Volume": "sum"
            }).dropna()
        else:
            hist_10m_raw = hist_5m_raw
        if hist_10m_raw.empty:
            return None
        hist_10m = _filter_from(hist_10m_raw, today_820_et)
        if hist_10m.empty:
            return None

        # Open reference = first bar's Open (8:30 AM CST market open)
        today_open = float(hist_10m["Open"].iloc[0])
        current_price = float(hist_10m["Close"].iloc[-1])

        # ── 30m bars ──
        if is_backtest:
            hist_30m_raw = tk.history(start=str(fetch_start), end=str(fetch_end), interval="30m", prepost=True)
        else:
            hist_30m_raw = tk.history(period="5d", interval="30m", prepost=True)
        hist_30m = _filter_from(hist_30m_raw, today_820_et) if not hist_30m_raw.empty else pd.DataFrame()

        # ── 4H: past completed 4H candles (prior sessions only) ──
        if is_backtest:
            hist_1h = tk.history(start=str(fetch_start), end=str(fetch_end), interval="1h", prepost=True)
        else:
            hist_1h = tk.history(period="5d", interval="1h", prepost=True)
        hist_4h = pd.DataFrame()
        if not hist_1h.empty:
            hdf = hist_1h.copy()
            hdf.index = pd.to_datetime(hdf.index)
            bars_4h = hdf.resample("4h").agg({
                "Open": "first", "High": "max", "Low": "min", "Close": "last", "Volume": "sum"
            }).dropna()
            b4h_idx = bars_4h.index
            if b4h_idx.tz is None:
                b4h_idx = b4h_idx.tz_localize("America/New_York")
            else:
                b4h_idx = b4h_idx.tz_convert("America/New_York")
            hist_4h = bars_4h[b4h_idx < today_820_et]

        def _eval_bias(df, ref_price, n_bars):
            """BULLISH if avg close > ref, else BEARISH (relative to direction)."""
            if df.empty or len(df) < 1:
                return "N/A"
            closes = df["Close"].tail(n_bars).values
            avg_close = float(np.mean(closes))
            if direction == "LONG":
                return "BULLISH" if avg_close > ref_price else "BEARISH"
            else:
                return "BEARISH" if avg_close < ref_price else "BULLISH"

        bias_10m = _eval_bias(hist_10m, today_open, len(hist_10m))
        bias_30m = _eval_bias(hist_30m, today_open, len(hist_30m)) if not hist_30m.empty else "N/A"
        bias_4h  = _eval_bias(hist_4h, today_open, min(len(hist_4h), 3)) if not hist_4h.empty else "N/A"

        # Alignment: all 3 agree = CONFIRMED, else DIVERGED
        valid_biases = [b for b in [bias_10m, bias_30m, bias_4h] if b != "N/A"]
        if len(valid_biases) >= 2 and len(set(valid_biases)) == 1:
            alignment = "CONFIRMED"
        elif len(valid_biases) >= 2:
            alignment = "DIVERGED"
        else:
            alignment = "N/A"

        return {
            "bias_10m": bias_10m,
            "bias_30m": bias_30m,
            "bias_4h":  bias_4h,
            "alignment": alignment,
            "today_open": round(today_open, 2),
            "current_price": round(current_price, 2),
        }
    except Exception:
        return None


# ──────────────────────────────────────────────
# POLYGON API HELPERS
# ──────────────────────────────────────────────

BASE = "https://api.polygon.io"

def poly_get(endpoint, params, api_key):
    """Generic Polygon GET with clear error messages."""
    p = dict(params)
    p["apiKey"] = api_key
    url = BASE + endpoint
    for attempt in range(3):
        try:
            r = requests.get(url, params=p, timeout=15)

            if r.status_code == 429:
                time.sleep(12)
                continue

            if r.status_code in (401, 403):
                try:
                    body = r.json().get("message", r.text[:200])
                except Exception:
                    body = r.text[:200]
                code = r.status_code
                hint = ("Invalid API key" if code == 401 else
                        "Access denied — this endpoint may require a paid Polygon plan")
                raise Exception(
                    f"Polygon {code}: {hint}\n"
                    f"API response: {body}\n"
                    f"Get/check your key at https://polygon.io/dashboard"
                )

            if r.status_code == 404:
                raise Exception(f"Polygon 404: Ticker not found. URL: {url}")

            if not r.ok:
                try:
                    body = r.json().get("message", r.text[:200])
                except Exception:
                    body = r.text[:200]
                raise Exception(f"Polygon {r.status_code}: {body}")

            return r.json()

        except Exception as e:
            if attempt == 2:
                raise
            err = str(e)
            if any(x in err for x in ("401", "403", "404", "API key")):
                raise  # no retry for auth/permission errors
            time.sleep(3)
    return {}


@st.cache_data(ttl=3600, show_spinner=False)
def get_daily_bars(ticker, start_date, end_date, api_key):
    """Daily adjusted OHLCV from Polygon."""
    endpoint = f"/v2/aggs/ticker/{ticker}/range/1/day/{start_date}/{end_date}"
    data = poly_get(endpoint, {"adjusted": "true", "sort": "asc", "limit": 5000}, api_key)
    results = data.get("results", [])
    if not results:
        return pd.DataFrame()
    df = pd.DataFrame(results)
    df["date"] = pd.to_datetime(df["t"], unit="ms").dt.date
    df = df.rename(columns={"o": "open", "h": "high", "l": "low", "c": "close", "v": "volume"})
    df = df.set_index("date")[["open", "high", "low", "close", "volume"]]
    return df


@st.cache_data(ttl=3600, show_spinner=False)
def get_hourly_bars(ticker, start_date, end_date, api_key):
    """Hourly adjusted bars from Polygon."""
    endpoint = f"/v2/aggs/ticker/{ticker}/range/1/hour/{start_date}/{end_date}"
    data = poly_get(endpoint, {"adjusted": "true", "sort": "asc", "limit": 5000}, api_key)
    results = data.get("results", [])
    if not results:
        return pd.DataFrame()
    df = pd.DataFrame(results)
    # Convert ms timestamp → ET datetime
    df["dt_utc"] = pd.to_datetime(df["t"], unit="ms", utc=True)
    df["dt_et"] = df["dt_utc"].dt.tz_convert("America/New_York")
    df = df.rename(columns={"o": "open", "h": "high", "l": "low", "c": "close", "v": "volume"})
    df = df.set_index("dt_et")[["open", "high", "low", "close", "volume"]]
    return df


# ──────────────────────────────────────────────
# ALPACA API HELPERS
# ──────────────────────────────────────────────

ALPACA_DATA_BASE = "https://data.alpaca.markets"

def alpaca_get(endpoint, params, api_key, api_secret):
    """Generic Alpaca GET with authentication headers."""
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
                try:
                    body = r.json().get("message", r.text[:200])
                except Exception:
                    body = r.text[:200]
                raise Exception(
                    f"Alpaca {r.status_code}: Invalid API credentials\n"
                    f"API response: {body}\n"
                    f"Get/check your key at https://app.alpaca.markets/paper/dashboard/overview"
                )

            if r.status_code == 404:
                raise Exception(f"Alpaca 404: Ticker not found. URL: {url}")

            if not r.ok:
                try:
                    body = r.json().get("message", r.text[:200])
                except Exception:
                    body = r.text[:200]
                raise Exception(f"Alpaca {r.status_code}: {body}")

            return r.json()

        except Exception as e:
            if attempt == 2:
                raise
            err = str(e)
            if any(x in err for x in ("401", "403", "404", "API")):
                raise  # no retry for auth/permission errors
            time.sleep(2)
    return {}


@st.cache_data(ttl=3600, show_spinner=False)
def get_daily_bars_alpaca(ticker, start_date, end_date, api_key, api_secret):
    """Daily adjusted OHLCV from Alpaca (IEX feed - free)."""
    endpoint = f"/v2/stocks/{ticker}/bars"
    params = {
        "timeframe": "1Day",
        "start": f"{start_date}T00:00:00Z",
        "end": f"{end_date}T23:59:59Z",
        "adjustment": "all",
        "limit": 10000,
        "sort": "asc",
        "feed": "iex",  # Free IEX feed (SIP requires paid subscription)
    }
    data = alpaca_get(endpoint, params, api_key, api_secret)
    bars = data.get("bars", [])
    if not bars:
        return pd.DataFrame()
    df = pd.DataFrame(bars)
    df["date"] = pd.to_datetime(df["t"]).dt.date
    df = df.rename(columns={"o": "open", "h": "high", "l": "low", "c": "close", "v": "volume"})
    df = df.set_index("date")[["open", "high", "low", "close", "volume"]]
    return df


@st.cache_data(ttl=3600, show_spinner=False)
def get_hourly_bars_alpaca(ticker, start_date, end_date, api_key, api_secret):
    """Hourly adjusted bars from Alpaca IEX feed (real-time, no delay!)."""
    endpoint = f"/v2/stocks/{ticker}/bars"
    params = {
        "timeframe": "1Hour",
        "start": f"{start_date}T00:00:00Z",
        "end": f"{end_date}T23:59:59Z",
        "adjustment": "all",
        "limit": 10000,
        "sort": "asc",
        "feed": "iex",  # Free IEX feed (SIP requires paid subscription)
    }
    data = alpaca_get(endpoint, params, api_key, api_secret)
    bars = data.get("bars", [])
    if not bars:
        return pd.DataFrame()
    df = pd.DataFrame(bars)
    # Convert timestamp → ET datetime
    df["dt_utc"] = pd.to_datetime(df["t"], utc=True)
    df["dt_et"] = df["dt_utc"].dt.tz_convert("America/New_York")
    df = df.rename(columns={"o": "open", "h": "high", "l": "low", "c": "close", "v": "volume"})
    df = df.set_index("dt_et")[["open", "high", "low", "close", "volume"]]
    return df


@st.cache_data(ttl=300, show_spinner=False)
def get_hourly_bars_yfinance(ticker, start_date, end_date):
    """Hourly bars from Yahoo Finance (consolidated data, matches TOS). Free, no key needed."""
    if not YFINANCE_AVAILABLE:
        return pd.DataFrame()
    try:
        tk = yf.Ticker(ticker)
        # yfinance needs datetime objects; add buffer day on end
        s = pd.to_datetime(start_date)
        e = pd.to_datetime(end_date) + timedelta(days=1)
        df = tk.history(start=s, end=e, interval="1h", auto_adjust=True)
        if df.empty:
            return pd.DataFrame()
        # Ensure timezone is ET
        if df.index.tz is None:
            from zoneinfo import ZoneInfo
            df.index = df.index.tz_localize("America/New_York")
        else:
            df.index = df.index.tz_convert("America/New_York")
        df.columns = [c.lower() for c in df.columns]
        # Keep only standard columns
        cols = [c for c in ["open", "high", "low", "close", "volume"] if c in df.columns]
        return df[cols]
    except Exception:
        return pd.DataFrame()


@st.cache_data(ttl=300, show_spinner=False)  # 5 min cache for fresher options data
def get_options_bias_alpaca(ticker, api_key, api_secret):
    """
    Fetch options chain data from Alpaca and calculate bias metrics.
    Returns dict with put/call ratio, sentiment, unusual volume, and details.
    """
    debug_info = []
    
    try:
        # Alpaca options snapshot endpoint
        endpoint = f"/v1beta1/options/snapshots/{ticker}"
        params = {"limit": 250, "feed": "indicative"}  # indicative feed is free
        
        try:
            data = alpaca_get(endpoint, params, api_key, api_secret)
            snapshots = data.get("snapshots", {})
            debug_info.append(f"Options API: {len(snapshots)} contracts")
        except Exception as e:
            debug_info.append(f"Options API error: {str(e)[:80]}")
            return {"error": f"Options API error: {str(e)[:80]}", "debug": debug_info}
        
        if not snapshots:
            return {"error": "No options data returned", "debug": debug_info}
        
        calls = []
        puts = []
        all_with_volume = []
        
        for symbol, snap in snapshots.items():
            # Parse contract type from symbol (e.g., TSLA250321C00250000)
            # Format: UNDERLYING + YYMMDD + C/P + STRIKE (8 digits, strike * 1000)
            is_call = "C" in symbol.split(ticker)[-1][:7] if ticker in symbol else None
            
            greeks = snap.get("greeks", {})
            quote = snap.get("latestQuote", {})
            trade = snap.get("latestTrade", {})
            
            vol = trade.get("s", 0) or 0  # trade size as proxy for volume
            oi = snap.get("openInterest", 0) or 0
            
            # Try to extract strike from symbol
            try:
                after_ticker = symbol.replace(ticker, "")
                if len(after_ticker) >= 15:
                    strike = int(after_ticker[7:15]) / 1000
                    exp_str = after_ticker[:6]
                    exp = f"20{exp_str[:2]}-{exp_str[2:4]}-{exp_str[4:6]}"
                else:
                    strike = 0
                    exp = ""
            except:
                strike = 0
                exp = ""
            
            contract_data = {
                "symbol": symbol,
                "oi": oi,
                "volume": vol,
                "strike": strike,
                "expiry": exp,
                "bid": quote.get("bp", 0),
                "ask": quote.get("ap", 0),
                "last_price": trade.get("p", 0),
            }
            
            if is_call:
                calls.append(contract_data)
                contract_data["type"] = "CALL"
            elif is_call is False:
                puts.append(contract_data)
                contract_data["type"] = "PUT"
            
            if oi > 0 or vol > 0:
                vol_oi_ratio = vol / oi if oi > 0 else vol
                is_unusual = (vol > 1000) or (vol_oi_ratio > 2 and vol > 100) or (vol > oi and vol > 500)
                contract_data["vol_oi_ratio"] = round(vol_oi_ratio, 1)
                contract_data["is_unusual"] = is_unusual
                all_with_volume.append(contract_data)
        
        total_calls = len(calls)
        total_puts = len(puts)
        
        call_oi = sum(c.get("oi", 0) for c in calls)
        put_oi = sum(p.get("oi", 0) for p in puts)
        call_volume = sum(c.get("volume", 0) for c in calls)
        put_volume = sum(p.get("volume", 0) for p in puts)
        
        debug_info.append(f"Calls: {total_calls}, Puts: {total_puts}")
        debug_info.append(f"Call OI: {call_oi}, Put OI: {put_oi}")
        
        # Sort by OI descending (volume often sparse in options)
        all_with_volume.sort(key=lambda x: x.get("oi", 0), reverse=True)
        top_volume = all_with_volume[:15]
        unusual_activity = [x for x in top_volume if x.get("is_unusual")]
        
        total_oi = call_oi + put_oi
        total_volume = call_volume + put_volume
        
        # Ratios
        pc_ratio = total_puts / total_calls if total_calls > 0 else 0
        oi_pc_ratio = put_oi / call_oi if call_oi > 0 else 0
        vol_pc_ratio = put_volume / call_volume if call_volume > 0 else 0
        
        # Sentiment
        if oi_pc_ratio < 0.7:
            sentiment = "BULLISH"
            sentiment_color = "#00e5a0"
            sentiment_desc = "Call-heavy OI indicates bullish sentiment"
        elif oi_pc_ratio <= 1.0:
            sentiment = "NEUTRAL"
            sentiment_color = "#f5c842"
            sentiment_desc = "Balanced put/call ratio"
        else:
            sentiment = "BEARISH"
            sentiment_color = "#ff4d6a"
            sentiment_desc = "Put-heavy OI indicates bearish sentiment"
        
        if total_volume > 0:
            if vol_pc_ratio < 0.7:
                vol_sentiment = "BULLISH"
                vol_color = "#00e5a0"
            elif vol_pc_ratio <= 1.0:
                vol_sentiment = "NEUTRAL"
                vol_color = "#f5c842"
            else:
                vol_sentiment = "BEARISH"
                vol_color = "#ff4d6a"
        else:
            vol_sentiment = "N/A"
            vol_color = "#6b7099"
        
        return {
            "total_calls": total_calls,
            "total_puts": total_puts,
            "pc_ratio": round(pc_ratio, 2),
            "call_oi": call_oi,
            "put_oi": put_oi,
            "total_oi": total_oi,
            "oi_pc_ratio": round(oi_pc_ratio, 2),
            "call_volume": call_volume,
            "put_volume": put_volume,
            "total_volume": total_volume,
            "vol_pc_ratio": round(vol_pc_ratio, 2),
            "sentiment": sentiment,
            "sentiment_color": sentiment_color,
            "sentiment_desc": sentiment_desc,
            "vol_sentiment": vol_sentiment,
            "vol_color": vol_color,
            "unusual_activity": unusual_activity,
            "top_volume": top_volume,
            "debug": debug_info,
        }
    except Exception as e:
        return {"error": str(e)[:100], "debug": debug_info if 'debug_info' in dir() else []}


def _safe_int(val):
    """Convert a value to int, treating NaN/None as 0."""
    if val is None:
        return 0
    try:
        if pd.isna(val):
            return 0
    except (TypeError, ValueError):
        pass
    try:
        return int(val)
    except (TypeError, ValueError):
        return 0


def _safe_float(val):
    """Convert a value to float, treating NaN/None as 0."""
    if val is None:
        return 0.0
    try:
        if pd.isna(val):
            return 0.0
    except (TypeError, ValueError):
        pass
    try:
        return float(val)
    except (TypeError, ValueError):
        return 0.0


def get_options_bias_yfinance(ticker):
    """
    Fetch options chain data via yfinance (free, no API key needed).
    Scans multiple expirations to build an accurate picture of OI and volume.
    Uses delta-aware analysis: classifies calls/puts by moneyness to separate
    speculative bets from hedges/covered positions for more accurate sentiment.
    Returns dict with put/call ratio, sentiment, unusual volume, delta analysis, and details.
    """
    if not YFINANCE_AVAILABLE:
        return {"error": "yfinance not installed", "debug": []}

    debug_info = []
    try:
        stock = yf.Ticker(ticker)
        expirations = stock.options
        if not expirations:
            return {"error": "No options expirations found", "debug": ["No expirations returned by yfinance"]}

        # Get current price for moneyness calculations
        current_price = None
        try:
            hist = stock.history(period="1d")
            if not hist.empty:
                current_price = float(hist["Close"].iloc[-1])
        except Exception:
            pass
        if current_price is None or current_price <= 0:
            try:
                info = stock.info
                current_price = float(info.get("regularMarketPrice") or info.get("previousClose", 0))
            except Exception:
                current_price = 0

        debug_info.append(f"Total expirations available: {len(expirations)}")
        debug_info.append(f"Current price for delta calc: ${current_price:.2f}" if current_price else "Price unavailable")

        # Skip same-day / expiring-today expirations (OI drains to 0)
        today_str = str(date.today())
        valid_exps = [e for e in expirations if e > today_str]
        if not valid_exps:
            valid_exps = expirations  # fallback if all are today or past

        # Use up to 6 nearest future expirations for a broader, more accurate view
        use_exps = valid_exps[:6]
        debug_info.append(f"Using expirations: {', '.join(use_exps)}")

        all_calls = []
        all_puts = []
        all_with_volume = []

        for exp in use_exps:
            try:
                chain = stock.option_chain(exp)
            except Exception:
                continue
            calls_df = chain.calls
            puts_df = chain.puts

            for _, row in calls_df.iterrows():
                vol = _safe_int(row.get("volume"))
                oi = _safe_int(row.get("openInterest"))
                strike = _safe_float(row.get("strike"))
                last = _safe_float(row.get("lastPrice"))
                bid = _safe_float(row.get("bid"))
                ask = _safe_float(row.get("ask"))
                iv = _safe_float(row.get("impliedVolatility"))
                itm = bool(row.get("inTheMoney", False))
                all_calls.append({"volume": vol, "oi": oi, "strike": strike,
                                  "last_price": last, "bid": bid, "ask": ask,
                                  "iv": iv, "itm": itm, "expiry": exp})
                if vol > 0 or oi > 0:
                    vol_oi_ratio = vol / oi if oi > 0 else 0
                    # Only flag unusual when OI > 0 (ratio is meaningful)
                    is_unusual = (oi > 0 and (
                        (vol > 1000) or
                        (vol_oi_ratio > 2 and vol > 100) or
                        (vol > oi and vol > 500)
                    ))
                    all_with_volume.append({
                        "strike": strike, "expiry": exp, "type": "CALL",
                        "volume": vol, "oi": oi,
                        "vol_oi_ratio": round(vol_oi_ratio, 1) if oi > 0 else 0,
                        "last_price": last, "is_unusual": is_unusual,
                        "iv": iv, "itm": itm,
                    })

            for _, row in puts_df.iterrows():
                vol = _safe_int(row.get("volume"))
                oi = _safe_int(row.get("openInterest"))
                strike = _safe_float(row.get("strike"))
                last = _safe_float(row.get("lastPrice"))
                bid = _safe_float(row.get("bid"))
                ask = _safe_float(row.get("ask"))
                iv = _safe_float(row.get("impliedVolatility"))
                itm = bool(row.get("inTheMoney", False))
                all_puts.append({"volume": vol, "oi": oi, "strike": strike,
                                 "last_price": last, "bid": bid, "ask": ask,
                                 "iv": iv, "itm": itm, "expiry": exp})
                if vol > 0 or oi > 0:
                    vol_oi_ratio = vol / oi if oi > 0 else 0
                    is_unusual = (oi > 0 and (
                        (vol > 1000) or
                        (vol_oi_ratio > 2 and vol > 100) or
                        (vol > oi and vol > 500)
                    ))
                    all_with_volume.append({
                        "strike": strike, "expiry": exp, "type": "PUT",
                        "volume": vol, "oi": oi,
                        "vol_oi_ratio": round(vol_oi_ratio, 1) if oi > 0 else 0,
                        "last_price": last, "is_unusual": is_unusual,
                        "iv": iv, "itm": itm,
                    })

        total_calls = len(all_calls)
        total_puts = len(all_puts)
        call_oi = sum(c["oi"] for c in all_calls)
        put_oi = sum(p["oi"] for p in all_puts)
        call_volume = sum(c["volume"] for c in all_calls)
        put_volume = sum(p["volume"] for p in all_puts)
        total_oi = call_oi + put_oi
        total_volume = call_volume + put_volume

        debug_info.append(f"Calls: {total_calls}, Puts: {total_puts}")
        debug_info.append(f"Call OI: {call_oi}, Put OI: {put_oi}")
        debug_info.append(f"Call Vol: {call_volume}, Put Vol: {put_volume}")

        # If total OI is 0 across all expirations, data is unreliable
        if total_oi == 0:
            debug_info.append("WARNING: Total OI is 0 — data may be stale or unavailable")

        pc_ratio = total_puts / total_calls if total_calls > 0 else 0
        oi_pc_ratio = put_oi / call_oi if call_oi > 0 else 0
        vol_pc_ratio = put_volume / call_volume if call_volume > 0 else 0

        # ── Delta-Aware Analysis ──
        # Classify options by moneyness to determine true directional intent.
        # Not all calls are bullish; not all puts are bearish:
        #   - Deep ITM calls (delta ~0.8-1.0) are often covered calls / hedges (neutral/bearish)
        #   - Far OTM puts (delta ~0.05-0.15) with high OI are often protective hedges (not bearish)
        #   - Near-ATM options (delta ~0.4-0.6) are the most directionally meaningful
        #   - OTM calls = speculative bullish; OTM puts = speculative bearish
        delta_analysis = {
            "spec_bull_oi": 0, "spec_bull_vol": 0,  # OTM calls (speculative bullish)
            "spec_bear_oi": 0, "spec_bear_vol": 0,  # Near-ATM & slightly OTM puts (speculative bearish)
            "hedge_call_oi": 0, "hedge_call_vol": 0,  # Deep ITM calls (likely covered calls)
            "hedge_put_oi": 0, "hedge_put_vol": 0,    # Far OTM puts (likely protective hedges)
            "atm_call_oi": 0, "atm_call_vol": 0,      # Near-ATM calls (directional bullish)
            "atm_put_oi": 0, "atm_put_vol": 0,        # Near-ATM puts (directional bearish)
        }

        if current_price and current_price > 0:
            for c in all_calls:
                strike = c["strike"]
                moneyness = (strike - current_price) / current_price  # +ve = OTM, -ve = ITM for calls
                if moneyness < -0.10:
                    # Deep ITM call (delta > ~0.85) — likely covered call / stock replacement
                    delta_analysis["hedge_call_oi"] += c["oi"]
                    delta_analysis["hedge_call_vol"] += c["volume"]
                elif -0.05 <= moneyness <= 0.05:
                    # Near ATM (delta ~0.4-0.6) — directional bullish
                    delta_analysis["atm_call_oi"] += c["oi"]
                    delta_analysis["atm_call_vol"] += c["volume"]
                elif moneyness > 0.05:
                    # OTM call (delta < ~0.4) — speculative bullish
                    delta_analysis["spec_bull_oi"] += c["oi"]
                    delta_analysis["spec_bull_vol"] += c["volume"]

            for p in all_puts:
                strike = p["strike"]
                moneyness = (current_price - strike) / current_price  # +ve = OTM, -ve = ITM for puts
                if moneyness > 0.15:
                    # Far OTM put (delta < ~0.15) — likely protective hedge
                    delta_analysis["hedge_put_oi"] += p["oi"]
                    delta_analysis["hedge_put_vol"] += p["volume"]
                elif -0.05 <= moneyness <= 0.10:
                    # Near ATM / slightly OTM put (delta ~0.3-0.6) — speculative bearish
                    delta_analysis["spec_bear_oi"] += p["oi"]
                    delta_analysis["spec_bear_vol"] += p["volume"]
                elif moneyness < -0.05:
                    # Deep ITM put — directional bearish or assignment risk
                    delta_analysis["atm_put_oi"] += p["oi"]
                    delta_analysis["atm_put_vol"] += p["volume"]

            # Delta-adjusted sentiment: weight speculative + ATM flow, discount hedges
            directional_bull = (delta_analysis["spec_bull_oi"] + delta_analysis["atm_call_oi"]) * 1.0
            directional_bear = (delta_analysis["spec_bear_oi"] + delta_analysis["atm_put_oi"]) * 1.0
            # Hedges get 25% weight — they indicate institutional positioning but not aggression
            hedge_adjustment = delta_analysis["hedge_call_oi"] * 0.25 + delta_analysis["hedge_put_oi"] * 0.25

            total_directional = directional_bull + directional_bear + hedge_adjustment
            if total_directional > 0:
                delta_bull_pct = directional_bull / total_directional
                delta_bear_pct = directional_bear / total_directional
            else:
                delta_bull_pct = 0.5
                delta_bear_pct = 0.5

            if delta_bull_pct > 0.60:
                delta_sentiment = "BULLISH"
                delta_color = "#00e5a0"
                delta_desc = f"Speculative + ATM call flow dominates ({delta_bull_pct*100:.0f}% bullish)"
            elif delta_bear_pct > 0.60:
                delta_sentiment = "BEARISH"
                delta_color = "#ff4d6a"
                delta_desc = f"Speculative + ATM put flow dominates ({delta_bear_pct*100:.0f}% bearish)"
            else:
                delta_sentiment = "NEUTRAL"
                delta_color = "#f5c842"
                delta_desc = f"Mixed directional flow ({delta_bull_pct*100:.0f}% bull / {delta_bear_pct*100:.0f}% bear)"

            debug_info.append(f"Delta analysis: Bull OI={directional_bull:.0f}, Bear OI={directional_bear:.0f}, "
                             f"Hedge Calls={delta_analysis['hedge_call_oi']}, Hedge Puts={delta_analysis['hedge_put_oi']}")
        else:
            delta_sentiment = "N/A"
            delta_color = "#6b7099"
            delta_desc = "Price unavailable for delta analysis"

        # ── Standard (raw) sentiment based on total OI ──
        if total_oi == 0:
            sentiment = "N/A"
            sentiment_color = "#6b7099"
            sentiment_desc = "No open interest data available"
        elif oi_pc_ratio < 0.7:
            sentiment = "BULLISH"
            sentiment_color = "#00e5a0"
            sentiment_desc = "Call-heavy OI indicates bullish sentiment"
        elif oi_pc_ratio <= 1.0:
            sentiment = "NEUTRAL"
            sentiment_color = "#f5c842"
            sentiment_desc = "Balanced put/call ratio"
        else:
            sentiment = "BEARISH"
            sentiment_color = "#ff4d6a"
            sentiment_desc = "Put-heavy OI indicates bearish sentiment"

        if total_volume > 0:
            if vol_pc_ratio < 0.7:
                vol_sentiment = "BULLISH"
                vol_color = "#00e5a0"
            elif vol_pc_ratio <= 1.0:
                vol_sentiment = "NEUTRAL"
                vol_color = "#f5c842"
            else:
                vol_sentiment = "BEARISH"
                vol_color = "#ff4d6a"
        else:
            vol_sentiment = "N/A"
            vol_color = "#6b7099"

        # Only show contracts that have real OI for the activity table
        with_real_data = [x for x in all_with_volume if x["oi"] > 0 or x["volume"] > 5]
        with_real_data.sort(key=lambda x: (x.get("oi", 0), x.get("volume", 0)), reverse=True)
        top_volume = with_real_data[:15]
        unusual_activity = [x for x in top_volume if x.get("is_unusual")]
        debug_info.append(f"Contracts with OI or vol>5: {len(with_real_data)}, unusual: {len(unusual_activity)}")

        # Add moneyness label to top volume / unusual contracts
        if current_price and current_price > 0:
            for item in top_volume:
                strike = item["strike"]
                if item["type"] == "CALL":
                    m = (strike - current_price) / current_price
                    if m < -0.10:
                        item["moneyness"] = "Deep ITM"
                        item["intent"] = "Hedge/Cover"
                    elif -0.05 <= m <= 0.05:
                        item["moneyness"] = "ATM"
                        item["intent"] = "Directional"
                    else:
                        item["moneyness"] = "OTM"
                        item["intent"] = "Speculative"
                else:  # PUT
                    m = (current_price - strike) / current_price
                    if m > 0.15:
                        item["moneyness"] = "Far OTM"
                        item["intent"] = "Hedge/Protect"
                    elif -0.05 <= m <= 0.10:
                        item["moneyness"] = "ATM/Near"
                        item["intent"] = "Directional"
                    else:
                        item["moneyness"] = "Deep ITM"
                        item["intent"] = "Directional"

        return {
            "total_calls": total_calls,
            "total_puts": total_puts,
            "pc_ratio": round(pc_ratio, 2),
            "call_oi": call_oi,
            "put_oi": put_oi,
            "total_oi": total_oi,
            "oi_pc_ratio": round(oi_pc_ratio, 2),
            "call_volume": call_volume,
            "put_volume": put_volume,
            "total_volume": total_volume,
            "vol_pc_ratio": round(vol_pc_ratio, 2),
            "sentiment": sentiment,
            "sentiment_color": sentiment_color,
            "sentiment_desc": sentiment_desc,
            "vol_sentiment": vol_sentiment,
            "vol_color": vol_color,
            "delta_sentiment": delta_sentiment,
            "delta_color": delta_color,
            "delta_desc": delta_desc,
            "delta_analysis": delta_analysis,
            "unusual_activity": unusual_activity,
            "top_volume": top_volume,
            "debug": debug_info,
        }
    except Exception as e:
        return {"error": str(e)[:100], "debug": debug_info}


@st.cache_data(ttl=300, show_spinner=False)  # 5 min cache for fresher options data
def get_options_bias(ticker, api_key):
    """
    Fetch options chain data from Polygon and calculate bias metrics.
    Returns dict with put/call ratio, sentiment, unusual volume, and details.
    """
    debug_info = []  # Track API responses for diagnostics
    
    try:
        # Get options snapshot for real-time volume data
        snapshot_endpoint = f"/v3/snapshot/options/{ticker}"
        snapshot_params = {"limit": 250}
        snapshot_results = []
        snapshot_error = None
        
        try:
            snapshot_data = poly_get(snapshot_endpoint, snapshot_params, api_key)
            snapshot_results = snapshot_data.get("results", [])
            debug_info.append(f"Snapshot API: {len(snapshot_results)} contracts")
        except Exception as e:
            snapshot_error = str(e)[:80]
            debug_info.append(f"Snapshot API error: {snapshot_error}")
        
        # Also get contracts list as fallback
        endpoint = f"/v3/reference/options/contracts"
        params = {
            "underlying_ticker": ticker,
            "expired": "false",
            "limit": 1000,
        }
        data = poly_get(endpoint, params, api_key)
        results = data.get("results", [])
        debug_info.append(f"Contracts API: {len(results)} contracts")
        
        if not results and not snapshot_results:
            return {"error": "No options data from either API", "debug": debug_info}
        
        # Use snapshot data if available (has volume), otherwise use contracts
        if snapshot_results:
            calls = [r for r in snapshot_results if r.get("details", {}).get("contract_type") == "call"]
            puts = [r for r in snapshot_results if r.get("details", {}).get("contract_type") == "put"]
            
            # Extract volume and OI from snapshot
            call_volume = sum(r.get("day", {}).get("volume", 0) for r in calls)
            put_volume = sum(r.get("day", {}).get("volume", 0) for r in puts)
            call_oi = sum(r.get("open_interest", 0) for r in calls)
            put_oi = sum(r.get("open_interest", 0) for r in puts)
            
            total_calls = len(calls)
            total_puts = len(puts)
            
            debug_info.append(f"Call vol: {call_volume}, Put vol: {put_volume}")
            
            # Find ALL contracts with volume, sorted by volume
            # Then flag the top ones as "unusual" or "high volume"
            all_with_volume = []
            for r in snapshot_results:
                details = r.get("details", {})
                day = r.get("day", {})
                vol = day.get("volume", 0)
                oi = r.get("open_interest", 0) or 1
                strike = details.get("strike_price", 0)
                exp = details.get("expiration_date", "")
                ctype = details.get("contract_type", "")
                
                if vol > 0:  # Any volume
                    vol_oi_ratio = vol / oi if oi > 0 else vol
                    
                    # Flag as unusual if high vol/OI ratio or high absolute volume
                    is_unusual = (vol > 1000) or (vol_oi_ratio > 2 and vol > 100) or (vol > oi and vol > 500)
                    
                    all_with_volume.append({
                        "strike": strike,
                        "expiry": exp,
                        "type": ctype.upper() if ctype else "?",
                        "volume": vol,
                        "oi": oi,
                        "vol_oi_ratio": round(vol_oi_ratio, 1),
                        "last_price": day.get("close", day.get("last", {}).get("price", 0)),
                        "is_unusual": is_unusual,
                    })
            
            # Sort by volume descending
            all_with_volume.sort(key=lambda x: x["volume"], reverse=True)
            
            # Top 15 by volume (mark unusual ones)
            top_volume = all_with_volume[:15]
            unusual_activity = [x for x in top_volume if x.get("is_unusual")]
            
            debug_info.append(f"Contracts with volume: {len(all_with_volume)}, unusual: {len(unusual_activity)}")
            
        else:
            # Fallback to contracts list (no volume data)
            calls = [r for r in results if r.get("contract_type") == "call"]
            puts = [r for r in results if r.get("contract_type") == "put"]
            
            total_calls = len(calls)
            total_puts = len(puts)
            
            call_oi = sum(r.get("open_interest", 0) for r in calls)
            put_oi = sum(r.get("open_interest", 0) for r in puts)
            call_volume = 0
            put_volume = 0
            unusual_activity = []
            top_volume = []
            debug_info.append("Using contracts API (no volume data available)")
        
        total_oi = call_oi + put_oi
        total_volume = call_volume + put_volume
        
        # Put/Call ratio by contracts
        pc_ratio = total_puts / total_calls if total_calls > 0 else 0
        
        # Put/Call ratio by OI
        oi_pc_ratio = put_oi / call_oi if call_oi > 0 else 0
        
        # Put/Call ratio by volume
        vol_pc_ratio = put_volume / call_volume if call_volume > 0 else 0
        
        # Determine sentiment based on put/call ratio
        if oi_pc_ratio < 0.7:
            sentiment = "BULLISH"
            sentiment_color = "#00e5a0"
            sentiment_desc = "Call-heavy flow indicates bullish sentiment"
        elif oi_pc_ratio <= 1.0:
            sentiment = "NEUTRAL"
            sentiment_color = "#f5c842"
            sentiment_desc = "Balanced put/call ratio"
        else:
            sentiment = "BEARISH"
            sentiment_color = "#ff4d6a"
            sentiment_desc = "Put-heavy flow indicates bearish sentiment"
        
        # Volume sentiment (today's flow)
        if total_volume > 0:
            if vol_pc_ratio < 0.7:
                vol_sentiment = "BULLISH"
                vol_color = "#00e5a0"
            elif vol_pc_ratio <= 1.0:
                vol_sentiment = "NEUTRAL"
                vol_color = "#f5c842"
            else:
                vol_sentiment = "BEARISH"
                vol_color = "#ff4d6a"
        else:
            vol_sentiment = "N/A"
            vol_color = "#6b7099"
        
        return {
            "total_calls": total_calls,
            "total_puts": total_puts,
            "pc_ratio": round(pc_ratio, 2),
            "call_oi": call_oi,
            "put_oi": put_oi,
            "total_oi": total_oi,
            "oi_pc_ratio": round(oi_pc_ratio, 2),
            "call_volume": call_volume,
            "put_volume": put_volume,
            "total_volume": total_volume,
            "vol_pc_ratio": round(vol_pc_ratio, 2),
            "sentiment": sentiment,
            "sentiment_color": sentiment_color,
            "sentiment_desc": sentiment_desc,
            "vol_sentiment": vol_sentiment,
            "vol_color": vol_color,
            "unusual_activity": unusual_activity,
            "top_volume": top_volume if 'top_volume' in dir() else [],
            "debug": debug_info,
        }
    except Exception as e:
        return {"error": str(e)[:100], "debug": debug_info if 'debug_info' in dir() else []}


@st.cache_data(ttl=3600, show_spinner=False)
def get_earnings_dates_polygon(ticker, api_key, limit=20):
    """
    Try Polygon vX financials endpoint (requires paid plan).
    Returns sorted list of (report_date, quarter_label, period) or [].
    """
    # Try both the given ticker and common aliases
    aliases = [ticker]
    if ticker == "GOOG":   aliases.append("GOOGL")
    if ticker == "GOOGL":  aliases.append("GOOG")
    if ticker == "BRK.B":  aliases.append("BRK/B")

    for t in aliases:
        try:
            endpoint = "/vX/reference/financials"
            params = {
                "ticker": t,
                "timeframe": "quarterly",
                "sort": "period_of_report_date",
                "order": "desc",
                "limit": limit,
            }
            data = poly_get(endpoint, params, api_key)
            results = data.get("results", [])
            if not results:
                continue
            events = []
            for r in results:
                filing = r.get("filing_date")
                period = r.get("period_of_report_date")
                fy     = r.get("fiscal_year", "")
                fq     = r.get("fiscal_period", "")
                label  = f"{fq} {fy}".strip() if fy else (period or "")
                if filing:
                    events.append((filing, label, period or filing))
            if events:
                events.sort(key=lambda x: x[0])
                return events
        except Exception:
            continue
    return []


@st.cache_data(ttl=3600, show_spinner=False)
def detect_earnings_from_prices(daily_df, min_gap_pct=3.0, min_vol_ratio=1.5):
    """
    Auto-detect likely earnings dates from daily price data.
    Looks for overnight gaps ≥ min_gap_pct% AND volume ≥ min_vol_ratio × 20d avg.
    Returns sorted list of (date_str, quarter_label, period).
    """
    if daily_df.empty or len(daily_df) < 25:
        return []

    df = daily_df.copy().reset_index()
    df = df.sort_values("date").reset_index(drop=True)

    events = []
    for i in range(1, len(df)):
        prev_close = df.loc[i-1, "close"]
        cur_open   = df.loc[i, "open"]
        cur_vol    = df.loc[i, "volume"]
        cur_date   = df.loc[i, "date"]

        # Overnight gap
        gap_pct = abs(cur_open - prev_close) / prev_close * 100
        if gap_pct < min_gap_pct:
            continue

        # Volume spike vs 20d avg
        start_idx = max(0, i - 21)
        avg_vol   = df.loc[start_idx:i-1, "volume"].mean()
        vol_ratio = cur_vol / avg_vol if avg_vol > 0 else 0
        if vol_ratio < min_vol_ratio:
            continue

        # Space events at least 45 days apart (quarterly)
        if events and (cur_date - datetime.strptime(events[-1][0], "%Y-%m-%d").date()).days < 45:
            # Keep the larger gap
            prev_gap = abs(
                df[df["date"] == datetime.strptime(events[-1][0], "%Y-%m-%d").date()]["open"].values[0]
                - df[df["date"] == datetime.strptime(events[-1][0], "%Y-%m-%d").date()].index[0]
            ) if events else 0
            continue

        # Label by approximate quarter
        yr  = cur_date.year
        mo  = cur_date.month
        if mo <= 3:   qtr = f"Q4 {yr-1}"
        elif mo <= 6: qtr = f"Q1 {yr}"
        elif mo <= 9: qtr = f"Q2 {yr}"
        else:         qtr = f"Q3 {yr}"

        # The ENTRY is cur_date (reaction day in old strategy).
        # For our strategy (enter on report day = day BEFORE the gap),
        # the report date is the previous trading day.
        report_date = str(df.loc[i-1, "date"])
        events.append((report_date, qtr, report_date))

    return events


def get_earnings_dates(ticker, api_key, limit=20, daily_df=None, manual_dates=None):
    """
    Multi-source earnings date resolver — priority order:
    1. Manual dates pasted by user
    2. Polygon vX financials (paid)
    3. Auto-detect from price/volume gaps (free)
    """
    # 1. Manual override
    if manual_dates:
        events = []
        for i, d in enumerate(manual_dates):
            try:
                dt  = datetime.strptime(d.strip(), "%Y-%m-%d").date()
                yr  = dt.year
                mo  = dt.month
                if mo <= 3:   qtr = f"Q4 {yr-1}"
                elif mo <= 6: qtr = f"Q1 {yr}"
                elif mo <= 9: qtr = f"Q2 {yr}"
                else:         qtr = f"Q3 {yr}"
                events.append((str(dt), qtr, str(dt)))
            except Exception:
                continue
        if events:
            events.sort(key=lambda x: x[0])
            return events, "manual"

    # 2. Polygon financials
    poly_events = get_earnings_dates_polygon(ticker, api_key, limit)
    if poly_events:
        return poly_events, "polygon"

    # 3. Auto-detect from price gaps
    if daily_df is not None and not daily_df.empty:
        auto_events = detect_earnings_from_prices(daily_df)
        if auto_events:
            return auto_events, "auto-detected"

    return [], "none"


def estimate_next_earnings(events):
    """Estimate next upcoming earnings date from cadence of past events."""
    if len(events) < 2:
        return None
    try:
        dates   = [datetime.strptime(e[0], "%Y-%m-%d").date() for e in events]
        gaps    = [(dates[i+1]-dates[i]).days for i in range(len(dates)-1)]
        avg_gap = int(np.mean(gaps[-4:]))  # use last 4 gaps
        nxt     = dates[-1] + timedelta(days=avg_gap)
        if nxt > date.today():
            return str(nxt)
    except Exception:
        pass
    return None


# ──────────────────────────────────────────────
# FINNHUB EARNINGS CALENDAR
# ──────────────────────────────────────────────

@st.cache_data(ttl=3600, show_spinner=False)
def fetch_earnings_calendar_finnhub(finnhub_key, from_date, to_date):
    """
    Fetch tickers reporting earnings between from_date and to_date
    via Finnhub /calendar/earnings endpoint.
    Returns list of dicts: [{symbol, date, hour(bmo/amc), epsEstimate, ...}]
    """
    url = "https://finnhub.io/api/v1/calendar/earnings"
    params = {
        "from": str(from_date),
        "to": str(to_date),
        "token": finnhub_key,
    }
    resp = requests.get(url, params=params, timeout=15)
    resp.raise_for_status()
    data = resp.json()
    events = data.get("earningsCalendar", [])
    # Filter to US-style tickers only (no dots like BRK.A except known ones)
    results = []
    for e in events:
        sym = e.get("symbol", "")
        if sym and "." not in sym and len(sym) <= 5:
            results.append({
                "symbol": sym,
                "date": e.get("date", ""),
                "hour": e.get("hour", ""),  # bmo / amc / dmh
                "epsEstimate": e.get("epsEstimate"),
                "revenueEstimate": e.get("revenueEstimate"),
            })
    results.sort(key=lambda x: (x["date"], x["symbol"]))
    return results


# ──────────────────────────────────────────────
# 4H CANDLE
# ──────────────────────────────────────────────

def get_4h_noon_candle(ticker, report_date, hourly_df):
    """
    Build the 9:00 AM – 1:00 PM ET 4H candle for a given date.
    (Standard first 4H candle: 8:00 AM – 12:00 PM CST)
    Hourly bars are timestamped at start of each hour, so bars
    at 9:00, 10:00, 11:00, 12:00 ET cover 9 AM – 1 PM ET.
    Returns dict with open, close, high, low or None.
    """
    if hourly_df is None or hourly_df.empty:
        return None
    try:
        # Convert report_date to date object if it's not already
        if isinstance(report_date, str):
            report_date = datetime.strptime(report_date, "%Y-%m-%d").date()
        elif hasattr(report_date, 'date'):
            report_date = report_date.date() if callable(getattr(report_date, 'date')) else report_date
        
        # Filter for the specific date
        day_bars = hourly_df[hourly_df.index.date == report_date]
        
        if day_bars.empty:
            return None
            
        # Keep bars at hours 9, 10, 11, 12 ET (covering 9 AM – 1 PM ET)
        window = day_bars[
            (day_bars.index.hour >= 9) & (day_bars.index.hour <= 12)
        ]
        if window.empty:
            return None
        return {
            "open":  float(window.iloc[0]["open"]),
            "close": float(window.iloc[-1]["close"]),
            "high":  float(window["high"].max()),
            "low":   float(window["low"].min()),
            "bars":  len(window),
            "first_bar_ts": window.index[0],
            "last_bar_ts": window.index[-1],
        }
    except Exception as e:
        return None


# ──────────────────────────────────────────────
# FIBONACCI
# ──────────────────────────────────────────────

FIB_RET = [0, 0.236, 0.382, 0.5, 0.618, 0.786, 1.0]
FIB_EXT = [1.272, 1.414, 1.618, 2.0, 2.618]

def calc_fib_levels(lo, hi):
    rng = hi - lo
    lvls = {}
    for f in FIB_RET:
        lvls[f"R {f*100:.1f}%"] = hi - rng * f
    for f in FIB_EXT:
        lvls[f"E {f*100:.1f}%"] = lo + rng * f
    return lvls

def nearest_fib(price, lo, hi, tol_pct):
    if not lo or not hi or hi <= lo or price <= 0:
        return None
    lvls = calc_fib_levels(lo, hi)
    best_name, best_price, best_dist = None, None, float("inf")
    for name, lvl in lvls.items():
        dist = abs(price - lvl) / lvl * 100
        if dist < best_dist:
            best_dist = dist
            best_name = name
            best_price = lvl
    return (best_name, best_price, round(best_dist, 2)) if best_dist <= tol_pct else None


def calc_support_resistance(daily_df, n_levels=5):
    """
    Calculate support and resistance levels from multiple methods:
    1. Pivot points (classic floor trader pivots)
    2. Recent swing highs/lows (fractals)
    3. Volume-weighted price clusters (VWAP-like)
    4. Round-number / psychological levels
    Returns dict with support_levels, resistance_levels (sorted, nearest first),
    and key_level (strongest confluence zone).
    """
    if daily_df is None or len(daily_df) < 20:
        return None

    current_price = float(daily_df["close"].iloc[-1])
    hi = float(daily_df["high"].iloc[-1])
    lo = float(daily_df["low"].iloc[-1])
    cl = current_price

    # ── 1. Classic Pivot Points ──
    pivot = (hi + lo + cl) / 3
    r1 = 2 * pivot - lo
    s1 = 2 * pivot - hi
    r2 = pivot + (hi - lo)
    s2 = pivot - (hi - lo)
    r3 = hi + 2 * (pivot - lo)
    s3 = lo - 2 * (hi - pivot)

    raw_supports = [s1, s2, s3]
    raw_resistances = [r1, r2, r3]

    # ── 2. Swing Highs/Lows (fractal pivots over last 60 days) ──
    lookback = min(60, len(daily_df))
    recent = daily_df.tail(lookback)
    swing_highs = []
    swing_lows = []
    highs = recent["high"].values
    lows = recent["low"].values
    for i in range(2, len(highs) - 2):
        if highs[i] > highs[i-1] and highs[i] > highs[i-2] and highs[i] > highs[i+1] and highs[i] > highs[i+2]:
            swing_highs.append(float(highs[i]))
        if lows[i] < lows[i-1] and lows[i] < lows[i-2] and lows[i] < lows[i+1] and lows[i] < lows[i+2]:
            swing_lows.append(float(lows[i]))

    for sh in swing_highs:
        if sh > current_price:
            raw_resistances.append(sh)
        else:
            raw_supports.append(sh)
    for sl in swing_lows:
        if sl < current_price:
            raw_supports.append(sl)
        else:
            raw_resistances.append(sl)

    # ── 3. Volume-Weighted Price Clusters ──
    if "volume" in daily_df.columns and len(daily_df) >= 20:
        recent_vol = daily_df.tail(20)
        # Find price level with highest volume (high-volume node)
        vwap_price = (recent_vol["close"] * recent_vol["volume"]).sum() / recent_vol["volume"].sum()
        if vwap_price < current_price:
            raw_supports.append(float(vwap_price))
        else:
            raw_resistances.append(float(vwap_price))
        # Highest volume day's price range midpoint
        max_vol_idx = recent_vol["volume"].idxmax()
        hvn = float((recent_vol.loc[max_vol_idx, "high"] + recent_vol.loc[max_vol_idx, "low"]) / 2)
        if hvn < current_price:
            raw_supports.append(hvn)
        else:
            raw_resistances.append(hvn)

    # ── 4. Round Numbers ──
    magnitude = 10 ** max(0, len(str(int(current_price))) - 2)
    round_below = int(current_price / magnitude) * magnitude
    round_above = round_below + magnitude
    if round_below > 0 and round_below < current_price:
        raw_supports.append(float(round_below))
    if round_above > current_price:
        raw_resistances.append(float(round_above))
    # Half-round
    half = magnitude / 2
    half_below = int(current_price / half) * half
    half_above = half_below + half
    if half_below > 0 and half_below < current_price:
        raw_supports.append(float(half_below))
    if half_above > current_price:
        raw_resistances.append(float(half_above))

    # ── Deduplicate & cluster nearby levels (within 0.3% of each other) ──
    def cluster_levels(levels, price, ascending=True):
        if not levels:
            return []
        levels = sorted(set(round(l, 2) for l in levels if l > 0))
        clustered = []
        for lv in levels:
            merged = False
            for i, (cv, cc) in enumerate(clustered):
                if abs(lv - cv) / cv < 0.003:  # within 0.3%
                    # Merge — keep the one closer to price, but count confluence
                    clustered[i] = ((cv * cc + lv) / (cc + 1), cc + 1)
                    merged = True
                    break
            if not merged:
                clustered.append((lv, 1))
        # Sort by distance from current price, break ties by confluence count
        clustered.sort(key=lambda x: (abs(x[0] - price), -x[1]))
        return [{"price": round(c[0], 2), "strength": c[1]} for c in clustered[:n_levels]]

    support_levels = cluster_levels([s for s in raw_supports if s < current_price], current_price)
    resistance_levels = cluster_levels([r for r in raw_resistances if r > current_price], current_price)

    # Sort supports descending (nearest first), resistances ascending (nearest first)
    support_levels.sort(key=lambda x: -x["price"])
    resistance_levels.sort(key=lambda x: x["price"])

    # Key level = strongest confluence
    all_levels = support_levels + resistance_levels
    key_level = max(all_levels, key=lambda x: x["strength"]) if all_levels else None

    return {
        "pivot": round(pivot, 2),
        "supports": support_levels[:n_levels],
        "resistances": resistance_levels[:n_levels],
        "key_level": key_level,
    }


# ──────────────────────────────────────────────
# VOLUME PROFILE ANALYSIS
# ──────────────────────────────────────────────

def analyze_volume_profile(daily_df, lookback=50, n_bins=50):
    """
    Build a volume profile over the last `lookback` days.
    Returns dict with:
      - poc       : Point of Control price (highest-volume price level)
      - vah       : Value Area High (upper boundary of 70% volume zone)
      - val       : Value Area Low  (lower boundary of 70% volume zone)
      - vol_bias  : BULLISH / BEARISH / NEUTRAL
      - vol_trend : ACCUMULATING / DISTRIBUTING / FLAT
      - vol_ratio : current volume vs 20-day avg ratio
      - vol_surge : True if today's volume > 1.5x avg
      - detail    : human-readable summary string
    """
    if daily_df is None or len(daily_df) < 20:
        return None

    df = daily_df.tail(lookback).copy()
    if df.empty or "volume" not in df.columns:
        return None

    current_price = float(df["close"].iloc[-1])
    price_min = float(df["low"].min())
    price_max = float(df["high"].max())
    if price_max <= price_min:
        return None

    # Build volume-at-price histogram
    bin_size = (price_max - price_min) / n_bins
    vol_at_price = np.zeros(n_bins)

    # Vectorized volume-at-price accumulation (replaces slow Python double loop)
    lows   = df["low"].values.astype(float)
    highs  = df["high"].values.astype(float)
    vols   = df["volume"].values.astype(float)
    valid  = (highs > lows) & (vols > 0)
    lo_bins = np.clip(((lows[valid]  - price_min) / bin_size).astype(int), 0, n_bins - 1)
    hi_bins = np.clip(((highs[valid] - price_min) / bin_size).astype(int), 0, n_bins - 1)
    valid_vols = vols[valid]
    for lo_b, hi_b, v in zip(lo_bins, hi_bins, valid_vols):
        n_covered = hi_b - lo_b + 1
        np.add.at(vol_at_price, range(lo_b, hi_b + 1), v / n_covered)

    # Point of Control = bin with most volume
    poc_bin = int(np.argmax(vol_at_price))
    poc = price_min + (poc_bin + 0.5) * bin_size

    # Value Area (70% of total volume centered on POC)
    total_vol = vol_at_price.sum()
    if total_vol == 0:
        return None
    target_vol = total_vol * 0.70
    va_vol = vol_at_price[poc_bin]
    lo_idx = poc_bin
    hi_idx = poc_bin
    while va_vol < target_vol and (lo_idx > 0 or hi_idx < n_bins - 1):
        add_lo = vol_at_price[lo_idx - 1] if lo_idx > 0 else 0
        add_hi = vol_at_price[hi_idx + 1] if hi_idx < n_bins - 1 else 0
        if add_lo >= add_hi and lo_idx > 0:
            lo_idx -= 1
            va_vol += add_lo
        elif hi_idx < n_bins - 1:
            hi_idx += 1
            va_vol += add_hi
        else:
            lo_idx -= 1
            va_vol += add_lo
    val = price_min + lo_idx * bin_size
    vah = price_min + (hi_idx + 1) * bin_size

    # Volume trend: compare last 5 avg vs prior 15 avg
    recent_vol = df["volume"].iloc[-5:].mean() if len(df) >= 5 else df["volume"].mean()
    prior_vol = df["volume"].iloc[-20:-5].mean() if len(df) >= 20 else df["volume"].mean()
    vol_ratio_trend = recent_vol / prior_vol if prior_vol > 0 else 1.0

    # Volume vs 20-day average
    avg_vol_20 = df["volume"].iloc[-20:].mean() if len(df) >= 20 else df["volume"].mean()
    today_vol = float(df["volume"].iloc[-1])
    vol_ratio = today_vol / avg_vol_20 if avg_vol_20 > 0 else 1.0
    vol_surge = vol_ratio > 1.5

    # Determine volume trend
    if vol_ratio_trend > 1.2:
        vol_trend = "ACCUMULATING"
    elif vol_ratio_trend < 0.8:
        vol_trend = "DISTRIBUTING"
    else:
        vol_trend = "FLAT"

    # Volume bias based on price position relative to POC and Value Area
    if current_price > vah:
        # Above value area — if volume is rising, bullish breakout; else fading
        vol_bias = "BULLISH" if vol_trend == "ACCUMULATING" else "NEUTRAL"
    elif current_price < val:
        # Below value area — if volume is rising, bearish breakdown; else fading
        vol_bias = "BEARISH" if vol_trend == "ACCUMULATING" else "NEUTRAL"
    elif current_price > poc:
        # Inside value area, above POC — lean bullish
        vol_bias = "BULLISH" if vol_trend != "DISTRIBUTING" else "NEUTRAL"
    elif current_price < poc:
        # Inside value area, below POC — lean bearish
        vol_bias = "BEARISH" if vol_trend != "DISTRIBUTING" else "NEUTRAL"
    else:
        vol_bias = "NEUTRAL"

    # Build detail string
    pos_label = (
        "Above VA" if current_price > vah else
        "Below VA" if current_price < val else
        "Above POC" if current_price > poc else
        "Below POC" if current_price < poc else
        "At POC"
    )
    detail = f"{pos_label} | POC ${poc:.2f} | VA ${val:.2f}-${vah:.2f} | {vol_trend} | Vol {vol_ratio:.1f}x"

    return {
        "poc": round(poc, 2),
        "vah": round(vah, 2),
        "val": round(val, 2),
        "vol_bias": vol_bias,
        "vol_trend": vol_trend,
        "vol_ratio": round(vol_ratio, 2),
        "vol_surge": vol_surge,
        "detail": detail,
    }


# ──────────────────────────────────────────────
# STRATEGY ANALYSIS (Fib + FVG + Weinstein + Bias)
# ──────────────────────────────────────────────

def analyze_strategy_signals(daily_df, lookback=50, fib_vol_threshold=1.2):
    """
    Analyze price data using Fib + FVG + Weinstein + Bias strategy.
    Returns dict with signal analysis including SHORT conditions.
    """
    if daily_df is None or len(daily_df) < lookback + 10:
        return {"error": "Insufficient data", "short_signal": False}
    
    df = daily_df.copy()
    df = df.sort_index()
    
    # Ensure we have enough data
    if len(df) < lookback:
        return {"error": "Insufficient data", "short_signal": False}
    
    # ─── SWING HIGH/LOW ───
    hh = df["high"].rolling(lookback).max().iloc[-1]
    ll = df["low"].rolling(lookback).min().iloc[-1]
    swing_range = hh - ll
    
    # Find bar positions of swing high/low
    recent_window = df.tail(lookback)
    bar_hh = len(recent_window) - recent_window["high"].values[::-1].argmax() - 1
    bar_ll = len(recent_window) - recent_window["low"].values[::-1].argmin() - 1
    
    is_bearish_swing = bar_ll > bar_hh  # Recent low is more recent than recent high
    is_bullish_swing = bar_hh > bar_ll
    
    # ─── VOLUME ANALYSIS ───
    avg_vol = df["volume"].rolling(20).mean().iloc[-1]
    current_vol = df["volume"].iloc[-1]
    high_volume = current_vol > (avg_vol * fib_vol_threshold)
    
    # ─── BUYER/SELLER CONVICTION ───
    bar_range = df["high"].iloc[-1] - df["low"].iloc[-1]
    close_position = (df["close"].iloc[-1] - df["low"].iloc[-1]) / bar_range if bar_range > 0 else 0.5
    
    # Candle direction (open vs close)
    current_open = df["open"].iloc[-1]
    current_close = df["close"].iloc[-1]
    is_green_candle = current_close > current_open  # Bullish candle
    is_red_candle = current_close < current_open    # Bearish candle
    candle_body_pct = abs(current_close - current_open) / current_open * 100 if current_open > 0 else 0
    
    # Conviction requires: high volume + close position + candle direction alignment
    buyer_conviction = high_volume and close_position >= 0.5 and is_green_candle
    seller_conviction = high_volume and close_position < 0.5 and is_red_candle
    
    # Previous bar conviction
    prev_range = df["high"].iloc[-2] - df["low"].iloc[-2]
    prev_close_pos = (df["close"].iloc[-2] - df["low"].iloc[-2]) / prev_range if prev_range > 0 else 0.5
    prev_green = df["close"].iloc[-2] > df["open"].iloc[-2]
    prev_red = df["close"].iloc[-2] < df["open"].iloc[-2]
    strong_sellers = seller_conviction and prev_close_pos < 0.5 and prev_red
    strong_buyers = buyer_conviction and prev_close_pos >= 0.5 and prev_green
    
    # ─── TREND DIRECTION (simplified ZigZag) ───
    # Check if price is making lower highs and lower lows
    recent_highs = df["high"].tail(10).values
    recent_lows = df["low"].tail(10).values
    
    is_downtrend = (recent_highs[-1] < recent_highs[0] and 
                    recent_lows[-1] < recent_lows[0])
    is_uptrend = (recent_highs[-1] > recent_highs[0] and 
                  recent_lows[-1] > recent_lows[0])
    
    # ─── WEINSTEIN ANALYSIS ───
    ma30 = df["close"].rolling(30).mean()
    ma10 = df["close"].rolling(10).mean()
    
    current_price = df["close"].iloc[-1]
    current_ma30 = ma30.iloc[-1]
    current_ma10 = ma10.iloc[-1]
    
    # MA30 slope
    ma30_slope = (current_ma30 - ma30.iloc[-10]) / ma30.iloc[-10] if ma30.iloc[-10] > 0 else 0
    ma_is_flat = abs(ma30_slope) < 0.08
    
    # 52-period high/low
    high_52 = df["high"].tail(52).max()
    low_52 = df["low"].tail(52).min()
    range_52 = high_52 - low_52
    price_position = ((current_price - low_52) / range_52 * 100) if range_52 > 0 else 0
    dist_from_high = ((high_52 - current_price) / current_price * 100) if current_price > 0 else 0
    
    # Relative Strength vs SPY (simplified - just use price change)
    stock_change = current_price / df["close"].iloc[-50] if len(df) >= 50 else 1
    rs_improving = stock_change < 1  # For shorts, we want declining RS
    
    # Volume building
    avg_vol_10 = df["volume"].tail(10).mean()
    avg_vol_4 = df["volume"].tail(4).mean()
    volume_building = avg_vol_4 > avg_vol_10 * 1.1
    
    # MA relationships
    ma10_above_ma30 = current_ma10 > current_ma30
    ma10_below_ma30 = current_ma10 < current_ma30
    ma_turning_down = current_ma30 < ma30.iloc[-2] < ma30.iloc[-4]
    near_ma30 = current_price > current_ma30 * 0.90 and current_price < current_ma30 * 1.15
    
    # Weinstein breakout score (for shorts, we want LOW score)
    score_ma30_curling = 1 if ma_turning_down else 0
    score_ma10_cross = 0 if ma10_below_ma30 else 1
    score_rs_negative = 1 if stock_change < 1 else 0
    score_vol_building = 1 if volume_building else 0
    score_near_low = 1 if dist_from_high > 15 else 0
    
    breakout_score = score_ma10_cross + score_vol_building + (1 - score_near_low)
    breakdown_score = score_ma30_curling + (1 - score_ma10_cross) + score_rs_negative + score_vol_building + score_near_low
    
    # ─── BIAS ANALYSIS ───
    # Compare current close to recent swing point
    swing_low_price = df["low"].tail(20).min()
    swing_high_price = df["high"].tail(20).max()
    
    is_bullish_bias = current_price > (swing_low_price + swing_high_price) / 2
    is_bearish_bias = current_price < (swing_low_price + swing_high_price) / 2
    
    # ─── FVG (Fair Value Gap) DETECTION ───
    # Bearish FVG: gap down (high[1] < low[3])
    has_bearish_fvg = False
    fvg_details = None
    
    if len(df) >= 4:
        for i in range(1, min(5, len(df) - 3)):
            if df["high"].iloc[-(i+1)] < df["low"].iloc[-(i+3)]:
                fvg_top = df["low"].iloc[-(i+3)]
                fvg_bottom = df["high"].iloc[-(i+1)]
                fvg_size_pct = (fvg_top - fvg_bottom) / current_price * 100
                if fvg_size_pct >= 0.5:  # At least 0.5% gap
                    has_bearish_fvg = True
                    fvg_details = {
                        "type": "BEARISH",
                        "top": fvg_top,
                        "bottom": fvg_bottom,
                        "size_pct": round(fvg_size_pct, 2)
                    }
                    break
    
    # Check for bullish FVG
    has_bullish_fvg = False
    if len(df) >= 4 and not has_bearish_fvg:
        for i in range(1, min(5, len(df) - 3)):
            if df["low"].iloc[-(i+1)] > df["high"].iloc[-(i+3)]:
                fvg_top = df["low"].iloc[-(i+1)]
                fvg_bottom = df["high"].iloc[-(i+3)]
                fvg_size_pct = (fvg_top - fvg_bottom) / current_price * 100
                if fvg_size_pct >= 0.5:
                    has_bullish_fvg = True
                    fvg_details = {
                        "type": "BULLISH",
                        "top": fvg_top,
                        "bottom": fvg_bottom,
                        "size_pct": round(fvg_size_pct, 2)
                    }
                    break
    
    # ─── SHORT SIGNAL CONDITIONS ───
    # Tier 1: All 4 core conditions aligned bearish
    short_tier1 = (is_bearish_swing and 
                   seller_conviction and 
                   is_downtrend and 
                   is_bearish_bias)
    
    # Tier 2: Trend + bias + weak breakout score
    short_tier2 = (is_bearish_swing and 
                   is_bearish_bias and 
                   breakout_score <= 3 and 
                   not buyer_conviction)
    
    short_signal = short_tier1 or short_tier2
    short_tier = "T1" if short_tier1 else ("T2" if short_tier2 else None)
    
    # ─── LONG SIGNAL CONDITIONS ───
    # Tier 1: Core conditions aligned bullish
    long_tier1 = (is_bullish_swing and 
                  buyer_conviction and 
                  is_uptrend and 
                  is_bullish_bias)
    
    # Tier 2: Trend + bias + good score
    long_tier2 = (is_bullish_swing and 
                  is_bullish_bias and 
                  breakout_score >= 3 and 
                  not seller_conviction)
    
    long_signal = long_tier1 or long_tier2
    long_tier = "T1" if long_tier1 else ("T2" if long_tier2 else None)
    
    # ─── TAKE PROFIT CHECK (if already in position) ───
    # Look back to find potential entry points
    take_profit_pct = 0.10  # 10%
    short_tp_hit = False
    long_tp_hit = False
    
    # Check last 20 bars for potential entry and TP
    for i in range(5, min(20, len(df))):
        past_price = df["close"].iloc[-i]
        # Short take profit: price dropped 10% from entry
        if current_price < past_price * (1 - take_profit_pct):
            short_tp_hit = True
            break
    
    for i in range(5, min(20, len(df))):
        past_price = df["close"].iloc[-i]
        # Long take profit: price rose 10% from entry
        if current_price > past_price * (1 + take_profit_pct):
            long_tp_hit = True
            break
    
    return {
        # Swing analysis
        "swing_high": round(hh, 2),
        "swing_low": round(ll, 2),
        "is_bearish_swing": is_bearish_swing,
        "is_bullish_swing": is_bullish_swing,
        
        # Volume & Candle
        "high_volume": high_volume,
        "volume_ratio": round(current_vol / avg_vol, 2) if avg_vol > 0 else 0,
        "buyer_conviction": buyer_conviction,
        "seller_conviction": seller_conviction,
        "strong_sellers": strong_sellers,
        "strong_buyers": strong_buyers,
        "is_green_candle": is_green_candle,
        "is_red_candle": is_red_candle,
        "candle_body_pct": round(candle_body_pct, 2),
        
        # Trend
        "is_downtrend": is_downtrend,
        "is_uptrend": is_uptrend,
        
        # Weinstein
        "ma30": round(current_ma30, 2),
        "ma10": round(current_ma10, 2),
        "ma10_below_ma30": ma10_below_ma30,
        "ma_turning_down": ma_turning_down,
        "breakout_score": breakout_score,
        "breakdown_score": breakdown_score,
        "price_position": round(price_position, 1),
        "dist_from_high": round(dist_from_high, 1),
        
        # Bias
        "is_bullish_bias": is_bullish_bias,
        "is_bearish_bias": is_bearish_bias,
        
        # FVG
        "has_bearish_fvg": has_bearish_fvg,
        "has_bullish_fvg": has_bullish_fvg,
        "fvg_details": fvg_details,
        
        # Signals
        "short_signal": short_signal,
        "short_tier": short_tier,
        "long_signal": long_signal,
        "long_tier": long_tier,
        
        # Take profit zones
        "short_tp_hit": short_tp_hit,
        "long_tp_hit": long_tp_hit,
    }


# ──────────────────────────────────────────────
# BACKTEST CORE
# ──────────────────────────────────────────────

def next_trading_day(d, daily_index):
    """Return next date in daily_index after d."""
    d = pd.Timestamp(d).date() if not isinstance(d, date) else d
    for idx_date in sorted(daily_index):
        if idx_date > d:
            return idx_date
    return None


# ──────────────────────────────────────────────
# FUNDAMENTALS (via yfinance)
# ──────────────────────────────────────────────

@st.cache_data(ttl=3600, show_spinner=False)  # Cache for 1 hour
def get_fundamentals(ticker):
    """
    Fetch fundamental data via yfinance.
    Returns dict with valuation, growth, profitability, risk, and analyst data.
    """
    if not YFINANCE_AVAILABLE:
        return None
    
    try:
        stock = yf.Ticker(ticker)
        info = stock.info
        
        if not info or "symbol" not in info:
            return None
        
        pe_ratio = info.get("trailingPE") or info.get("forwardPE")
        forward_pe = info.get("forwardPE")
        peg_ratio = info.get("pegRatio")
        market_cap = info.get("marketCap")
        current_price = info.get("currentPrice") or info.get("regularMarketPrice")
        target_price = info.get("targetMeanPrice")
        target_low = info.get("targetLowPrice")
        target_high = info.get("targetHighPrice")
        
        # Growth metrics
        revenue_growth = info.get("revenueGrowth")  # quarterly YoY
        earnings_growth = info.get("earningsGrowth")  # quarterly YoY
        revenue = info.get("totalRevenue")
        
        # Profitability
        profit_margin = info.get("profitMargins")
        gross_margin = info.get("grossMargins")
        operating_margin = info.get("operatingMargins")
        roe = info.get("returnOnEquity")
        roa = info.get("returnOnAssets")
        
        # Risk / Balance sheet
        debt_to_equity = info.get("debtToEquity")
        current_ratio = info.get("currentRatio")
        beta = info.get("beta")
        short_ratio = info.get("shortRatio")
        short_pct = info.get("shortPercentOfFloat")
        
        # Dividend
        dividend_yield = info.get("dividendYield")
        payout_ratio = info.get("payoutRatio")
        
        # Identity
        sector = info.get("sector", "N/A")
        industry = info.get("industry", "N/A")
        
        # 52-week
        week52_high = info.get("fiftyTwoWeekHigh")
        week52_low = info.get("fiftyTwoWeekLow")
        
        # Analyst recommendations
        rec_key = info.get("recommendationKey", "")  # buy, hold, sell, etc.
        rec_mean = info.get("recommendationMean")  # 1=strong buy, 5=sell
        num_analysts = info.get("numberOfAnalystOpinions")
        
        # EPS
        trailing_eps = info.get("trailingEps")
        forward_eps = info.get("forwardEps")
        
        # Calculate upside/downside to target
        if target_price and current_price and current_price > 0:
            target_upside = round(((target_price - current_price) / current_price) * 100, 1)
        else:
            target_upside = None
        
        # 52-week position
        if week52_high and week52_low and current_price:
            week52_range = week52_high - week52_low
            week52_position = ((current_price - week52_low) / week52_range * 100) if week52_range > 0 else 50
            pct_from_high = ((current_price - week52_high) / week52_high * 100) if week52_high else None
        else:
            week52_position = None
            pct_from_high = None
        
        # Format market cap
        if market_cap:
            if market_cap >= 1e12:
                market_cap_str = f"${market_cap / 1e12:.1f}T"
            elif market_cap >= 1e9:
                market_cap_str = f"${market_cap / 1e9:.1f}B"
            elif market_cap >= 1e6:
                market_cap_str = f"${market_cap / 1e6:.1f}M"
            else:
                market_cap_str = f"${market_cap:,.0f}"
        else:
            market_cap_str = "N/A"
        
        # Format revenue
        if revenue:
            if revenue >= 1e12:
                revenue_str = f"${revenue / 1e12:.1f}T"
            elif revenue >= 1e9:
                revenue_str = f"${revenue / 1e9:.1f}B"
            elif revenue >= 1e6:
                revenue_str = f"${revenue / 1e6:.0f}M"
            else:
                revenue_str = f"${revenue:,.0f}"
        else:
            revenue_str = "N/A"
        
        # Valuation assessment based on P/E
        if pe_ratio:
            if pe_ratio < 0:
                valuation = "Negative Earnings"
                valuation_color = "#6b7099"
            elif pe_ratio < 15:
                valuation = "Undervalued"
                valuation_color = "#00e5a0"
            elif pe_ratio <= 25:
                valuation = "Fair Value"
                valuation_color = "#f5c842"
            elif pe_ratio <= 40:
                valuation = "Overvalued"
                valuation_color = "#ff8c42"
            else:
                valuation = "Very Expensive"
                valuation_color = "#ff4d6a"
        else:
            valuation = "N/A"
            valuation_color = "#6b7099"
        
        # Fundamental flags (quick risk/opportunity signals)
        flags = []
        if revenue_growth and revenue_growth > 0.20:
            flags.append(("🚀 High Revenue Growth", "#00e5a0"))
        if revenue_growth and revenue_growth < -0.05:
            flags.append(("📉 Revenue Declining", "#ff4d6a"))
        if earnings_growth and earnings_growth > 0.25:
            flags.append(("💰 Strong Earnings Growth", "#00e5a0"))
        if earnings_growth and earnings_growth < -0.10:
            flags.append(("⚠️ Earnings Declining", "#ff4d6a"))
        if profit_margin and profit_margin > 0.20:
            flags.append(("✅ High Margins", "#00e5a0"))
        if profit_margin and profit_margin < 0:
            flags.append(("🔴 Unprofitable", "#ff4d6a"))
        if debt_to_equity and debt_to_equity > 200:
            flags.append(("⚠️ High Debt", "#ff4d6a"))
        if debt_to_equity is not None and debt_to_equity < 30:
            flags.append(("✅ Low Debt", "#00e5a0"))
        if short_pct and short_pct > 0.10:
            flags.append(("🔥 High Short Interest", "#ff8c42"))
        if dividend_yield and dividend_yield > 0.03:
            flags.append(("💵 Good Dividend", "#00e5a0"))
        if peg_ratio and 0 < peg_ratio < 1:
            flags.append(("🎯 PEG < 1 (Growth Bargain)", "#00e5a0"))
        if target_upside and target_upside > 20:
            flags.append(("📈 Analyst Upside >20%", "#00e5a0"))
        if target_upside and target_upside < -15:
            flags.append(("📉 Analyst Downside >15%", "#ff4d6a"))
        if week52_position and week52_position > 90:
            flags.append(("⚡ Near 52W High", "#f5c842"))
        if week52_position and week52_position < 15:
            flags.append(("📉 Near 52W Low", "#ff8c42"))
        
        return {
            "valuation": valuation,
            "valuation_color": valuation_color,
            "market_cap_str": market_cap_str,
            "target_price": round(target_price, 2) if target_price else None,
            "target_low": round(target_low, 2) if target_low else None,
            "target_high": round(target_high, 2) if target_high else None,
            "target_upside": target_upside,
            # Growth
            "revenue_growth": revenue_growth,
            "earnings_growth": earnings_growth,
            "revenue_str": revenue_str,
            # Valuation
            "pe_ratio": round(pe_ratio, 1) if pe_ratio else None,
            "forward_pe": round(forward_pe, 1) if forward_pe else None,
            "peg_ratio": round(peg_ratio, 2) if peg_ratio else None,
            # Profitability
            "profit_margin": profit_margin,
            "gross_margin": gross_margin,
            "operating_margin": operating_margin,
            "roe": roe,
            "roa": roa,
            # Risk
            "debt_to_equity": round(debt_to_equity, 1) if debt_to_equity else None,
            "current_ratio": round(current_ratio, 2) if current_ratio else None,
            "beta": round(beta, 2) if beta else None,
            "short_ratio": round(short_ratio, 1) if short_ratio else None,
            "short_pct": short_pct,
            # Dividend
            "dividend_yield": dividend_yield,
            "payout_ratio": payout_ratio,
            # Identity
            "sector": sector,
            "industry": industry,
            # 52-week
            "week52_high": round(week52_high, 2) if week52_high else None,
            "week52_low": round(week52_low, 2) if week52_low else None,
            "week52_position": round(week52_position, 1) if week52_position else None,
            "pct_from_high": round(pct_from_high, 1) if pct_from_high else None,
            # Analyst
            "rec_key": rec_key,
            "rec_mean": round(rec_mean, 1) if rec_mean else None,
            "num_analysts": num_analysts,
            # EPS
            "trailing_eps": round(trailing_eps, 2) if trailing_eps else None,
            "forward_eps": round(forward_eps, 2) if forward_eps else None,
            # Flags
            "flags": flags,
        }
    except Exception:
        return None


# ──────────────────────────────────────────────
# MACRO MARKET INDICATORS
# ──────────────────────────────────────────────

MACRO_INSTRUMENTS = [
    # (ticker, label, emoji, category)
    ("SPY",   "S&P 500",   "📈", "index"),
    ("QQQ",   "Nasdaq",    "💻", "index"),
    ("DIA",   "Dow Jones", "🏛️", "index"),
    ("IWM",   "Russell 2K","🏘️", "index"),
    ("^VIX",  "VIX",       "🌡️", "fear"),
    ("GLD",   "Gold",      "🥇", "commodity"),
    ("SLV",   "Silver",    "🥈", "commodity"),
    ("USO",   "Oil",       "⛽", "commodity"),
    ("TLT",   "Bonds 20Y", "🏦", "bonds"),
    ("DXY",   "USD Index", "💵", "currency"),
    ("BTC-USD","Bitcoin",  "₿",  "crypto"),
]

@st.cache_data(ttl=300, show_spinner=False)
def get_macro_snapshot():
    """
    Fetch current price, 1d change, 5d change, and 20d change for macro instruments.
    Uses yfinance (free, no key needed). Returns list of dicts.
    """
    if not YFINANCE_AVAILABLE:
        return []
    import yfinance as yf
    results = []
    tickers = [t for t, *_ in MACRO_INSTRUMENTS]
    try:
        data = yf.download(tickers, period="30d", interval="1d",
                           auto_adjust=True, progress=False, threads=True)
        close = data["Close"] if "Close" in data.columns else data
    except Exception:
        return []

    for ticker, label, emoji, category in MACRO_INSTRUMENTS:
        try:
            if ticker not in close.columns:
                continue
            s = close[ticker].dropna()
            if len(s) < 2:
                continue
            price      = float(s.iloc[-1])
            chg_1d     = (price - float(s.iloc[-2])) / float(s.iloc[-2]) * 100 if len(s) >= 2 else 0
            chg_5d     = (price - float(s.iloc[-6])) / float(s.iloc[-6]) * 100 if len(s) >= 6 else chg_1d
            chg_20d    = (price - float(s.iloc[-21])) / float(s.iloc[-21]) * 100 if len(s) >= 21 else chg_5d
            results.append({
                "ticker":   ticker,
                "label":    label,
                "emoji":    emoji,
                "category": category,
                "price":    round(price, 2),
                "chg_1d":   round(chg_1d, 2),
                "chg_5d":   round(chg_5d, 2),
                "chg_20d":  round(chg_20d, 2),
            })
        except Exception:
            continue
    return results


def _macro_card_html(item):
    """Render a single macro instrument as an HTML card."""
    chg = item["chg_1d"]
    color = "#00e5a0" if chg > 0 else ("#ff4d6a" if chg < 0 else "#6b7099")
    sign  = "+" if chg > 0 else ""
    chg5_color = "#00e5a0" if item["chg_5d"] > 0 else ("#ff4d6a" if item["chg_5d"] < 0 else "#6b7099")
    chg5_sign  = "+" if item["chg_5d"] > 0 else ""
    chg20_color= "#00e5a0" if item["chg_20d"]>0 else ("#ff4d6a" if item["chg_20d"]<0 else "#6b7099")
    chg20_sign = "+" if item["chg_20d"] > 0 else ""

    # Special: VIX is inverse — high VIX = fear = bearish for market
    if item["ticker"] == "^VIX":
        risk = "🔴 Fear" if item["price"] > 25 else ("🟡 Caution" if item["price"] > 18 else "🟢 Calm")
        sub_line = f'<div style="font-size:9px;color:#f0c040;margin-top:2px">{risk}</div>'
    else:
        sub_line = f'<div style="font-size:9px;color:#6b7099;margin-top:2px">5d <span style="color:{chg5_color}">{chg5_sign}{item["chg_5d"]:.1f}%</span> · 20d <span style="color:{chg20_color}">{chg20_sign}{item["chg_20d"]:.1f}%</span></div>'

    return (
        f'<div style="background:#0d0f17;border:1px solid #1a1d2e;padding:10px 12px;'
        f'border-radius:6px;min-width:110px">'
        f'<div style="font-size:10px;color:#6b7099">{item["emoji"]} {item["label"]}</div>'
        f'<div style="font-size:15px;font-weight:700;color:#e8ecff;margin-top:2px">${item["price"]:,.2f}</div>'
        f'<div style="font-size:11px;font-weight:600;color:{color}">{sign}{chg:.2f}%</div>'
        f'{sub_line}'
        f'</div>'
    )


def _render_sector_bar_chart(sector_list, score_key, label_key, detail_fn):
    """Render a horizontal bar chart for sector strength. Works with both scan and ETF data."""
    if not sector_list:
        return
    max_abs = max(abs(s[score_key]) for s in sector_list) or 1
    bar_html = ""
    for s in sector_list:
        sc   = s[score_key]
        pct  = abs(sc) / max_abs * 100
        col  = "#00e5a0" if sc > 0 else ("#ff4d6a" if sc < 0 else "#6b7099")
        icon = "▲" if sc > 0 else ("▼" if sc < 0 else "—")
        bar_html += (
            f'<div style="display:flex;align-items:center;gap:8px;margin-bottom:5px">'
            f'<div style="width:140px;font-size:11px;color:#e8ecff;text-align:right;flex-shrink:0;overflow:hidden;white-space:nowrap">'
            f'<span style="color:{col}">{icon}</span> {str(s[label_key])[:18]}</div>'
            f'<div style="flex:1;background:#1a1d2e;border-radius:3px;height:14px;overflow:hidden">'
            f'<div style="width:{pct:.0f}%;background:{col};height:100%;border-radius:3px"></div></div>'
            f'<div style="width:90px;font-size:10px;color:{col};flex-shrink:0">{detail_fn(s)}</div>'
            f'</div>'
        )
    st.markdown(f'<div style="padding:4px 0 8px">{bar_html}</div>', unsafe_allow_html=True)


def _render_macro_dashboard(macro_data, sector_str=None, etf_sector_perf=None):
    """Render the full macro dashboard with risk assessment and sector strength."""
    if not macro_data:
        st.caption("Macro data unavailable — install yfinance to enable.")
        return

    # Split into categories
    indices   = [m for m in macro_data if m["category"] == "index"]
    fear      = [m for m in macro_data if m["category"] == "fear"]
    comms     = [m for m in macro_data if m["category"] == "commodity"]
    bonds     = [m for m in macro_data if m["category"] == "bonds"]
    other     = [m for m in macro_data if m["category"] in ("currency", "crypto")]

    # ── Market risk score ──────────────────────────────────────────────────
    risk_score = 0
    risk_notes = []
    spx = next((m for m in macro_data if m["ticker"] == "SPY"), None)
    vix = next((m for m in macro_data if m["ticker"] == "^VIX"), None)
    tlt = next((m for m in macro_data if m["ticker"] == "TLT"), None)
    gld = next((m for m in macro_data if m["ticker"] == "GLD"), None)

    if vix:
        if vix["price"] > 25:   risk_score += 2; risk_notes.append(f"VIX {vix['price']:.0f} — elevated fear")
        elif vix["price"] > 18: risk_score += 1; risk_notes.append(f"VIX {vix['price']:.0f} — mild caution")
        else:                   risk_notes.append(f"VIX {vix['price']:.0f} — calm")
    if spx and spx["chg_5d"] < -2:
        risk_score += 1; risk_notes.append(f"SPY 5d: {spx['chg_5d']:+.1f}% — market under pressure")
    if tlt and tlt["chg_5d"] > 1:
        risk_score += 1; risk_notes.append("Bonds rallying — flight to safety")
    if gld and gld["chg_5d"] > 2:
        risk_score += 1; risk_notes.append(f"Gold 5d: {gld['chg_5d']:+.1f}% — safe haven demand")

    risk_label = "🟢 LOW RISK"       if risk_score == 0 else \
                 "🟡 MODERATE RISK"  if risk_score <= 2 else \
                 "🔴 HIGH RISK"
    risk_color = "#00e5a0" if risk_score == 0 else ("#f0c040" if risk_score <= 2 else "#ff4d6a")

    st.markdown(
        f'<div style="background:#0d0f17;border:1px solid #1a1d2e;padding:10px 14px;'
        f'border-radius:6px;margin-bottom:10px;display:flex;align-items:center;gap:16px">'
        f'<div><div style="font-size:11px;color:#6b7099">MARKET RISK</div>'
        f'<div style="font-size:14px;font-weight:700;color:{risk_color}">{risk_label}</div></div>'
        f'<div style="font-size:10px;color:#6b7099;line-height:1.7">'
        + " &nbsp;·&nbsp; ".join(risk_notes) +
        f'</div></div>',
        unsafe_allow_html=True,
    )

    # ── Cards grid ─────────────────────────────────────────────────────────
    for group_label, group in [
        ("Indices", indices), ("Fear / Vol", fear),
        ("Commodities", comms), ("Bonds & Currency", bonds + other)
    ]:
        if not group:
            continue
        st.markdown(f'<div style="font-size:10px;color:#6b7099;font-weight:600;margin:8px 0 4px;letter-spacing:.05em">{group_label.upper()}</div>', unsafe_allow_html=True)
        cols_html = "".join(_macro_card_html(m) for m in group)
        st.markdown(
            f'<div style="display:flex;flex-wrap:wrap;gap:8px;margin-bottom:6px">{cols_html}</div>',
            unsafe_allow_html=True,
        )

    # ── Sector Strength ────────────────────────────────────────────────────
    st.markdown('<hr style="border:none;border-top:1px solid #1a1d2e;margin:16px 0 12px">', unsafe_allow_html=True)

    # Use scan-derived strength if available, otherwise fall back to ETF momentum
    if sector_str:
        st.markdown('<div style="font-size:10px;color:#6b7099;font-weight:600;letter-spacing:.06em;margin-bottom:8px">SECTOR STRENGTH — FROM LAST SCAN</div>', unsafe_allow_html=True)
        _render_sector_bar_chart(sector_str, score_key="avg_score", label_key="sector",
                                 detail_fn=lambda s: f'avg {s["avg_score"]:+.2f} · {s["total"]}T')
        col1, col2 = st.columns(2)
        with col1:
            st.markdown("**Strongest**")
            for s in sector_str[:3]:
                st.markdown(f"🟢 **{s['sector']}** — {s['avg_score']:+.2f}, {s['bull_pct']:.0f}% bullish")
        with col2:
            st.markdown("**Weakest**")
            for s in reversed(sector_str[-3:]):
                st.markdown(f"🔴 **{s['sector']}** — {s['avg_score']:+.2f}, {s['bearish']}/{s['total']} bearish")

    elif etf_sector_perf:
        st.markdown('<div style="font-size:10px;color:#6b7099;font-weight:600;letter-spacing:.06em;margin-bottom:8px">SECTOR STRENGTH — ETF MOMENTUM (run Stock Analysis for signal-based view)</div>', unsafe_allow_html=True)
        _render_sector_bar_chart(etf_sector_perf, score_key="momentum", label_key="name",
                                 detail_fn=lambda s: f'1d {s["change_1d"]:+.1f}% · 1w {s["change_1w"]:+.1f}%')
        hot   = [s for s in etf_sector_perf if s["momentum"] > 0][:3]
        cold  = [s for s in reversed(etf_sector_perf) if s["momentum"] <= 0][:3]
        col1, col2 = st.columns(2)
        with col1:
            st.markdown("**Strongest**")
            for s in hot:
                st.markdown(f"🟢 **{s['name']}** ({s['etf']}) — {s['momentum']:+.2f} momentum")
        with col2:
            st.markdown("**Weakest**")
            for s in cold:
                st.markdown(f"🔴 **{s['name']}** ({s['etf']}) — {s['momentum']:+.2f} momentum")
    else:
        st.caption("Run Stock Analysis to see signal-based sector strength, or wait for ETF data to load.")


def _sector_strength_from_scan(scan_results):
    """
    Derive sector strength rankings from scan results.
    Returns list of dicts sorted by strength score descending.
    """
    from collections import defaultdict
    sector_data = defaultdict(lambda: {"bullish":0,"bearish":0,"total":0,"score_sum":0,"tickers":[]})

    for r in scan_results:
        sec = r.get("sector", "N/A") or "N/A"
        if sec == "N/A":
            continue
        s = sector_data[sec]
        s["total"] += 1
        s["tickers"].append(r["ticker"])
        score = r.get("score", 0) or 0
        s["score_sum"] += score
        verdict = r.get("verdict", "")
        if "BULLISH" in verdict: s["bullish"] += 1
        elif "BEARISH" in verdict: s["bearish"] += 1

    result = []
    for sec, d in sector_data.items():
        if d["total"] == 0:
            continue
        bull_pct   = d["bullish"] / d["total"] * 100
        bear_pct   = d["bearish"] / d["total"] * 100
        avg_score  = d["score_sum"] / d["total"]
        # Strength: weighted combo of avg score and bull/bear ratio
        strength   = avg_score + (bull_pct - bear_pct) / 20
        bias       = "BULLISH" if avg_score > 0.5 else ("BEARISH" if avg_score < -0.5 else "NEUTRAL")
        result.append({
            "sector":    sec,
            "total":     d["total"],
            "bullish":   d["bullish"],
            "bearish":   d["bearish"],
            "bull_pct":  round(bull_pct, 0),
            "avg_score": round(avg_score, 2),
            "strength":  round(strength, 2),
            "bias":      bias,
            "tickers":   ", ".join(d["tickers"][:6]),
        })
    result.sort(key=lambda x: x["strength"], reverse=True)
    return result


# ──────────────────────────────────────────────
# SECTOR ANALYSIS
# ──────────────────────────────────────────────

# Sector ETFs with their top holdings
SECTOR_ETFS = {
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

@st.cache_data(ttl=300, show_spinner=False)  # 5 min cache
def get_sector_performance(api_key, api_secret, data_source):
    """
    Get performance data for all sector ETFs.
    Returns list of dicts with sector info and performance metrics.
    """
    results = []
    end_date = date.today()
    start_date = end_date - timedelta(days=30)  # Need ~20 trading days
    
    for etf, info in SECTOR_ETFS.items():
        try:
            # Fetch daily data for sector ETF
            if data_source == "Alpaca":
                df = get_daily_bars_alpaca(etf, str(start_date), str(end_date), api_key, api_secret)
            else:
                df = get_daily_bars(etf, str(start_date), str(end_date), api_key)
            
            if df.empty or len(df) < 5:
                continue
            
            current_price = df["close"].iloc[-1]
            
            # 1-day change
            if len(df) >= 2:
                prev_close = df["close"].iloc[-2]
                change_1d = ((current_price - prev_close) / prev_close) * 100
            else:
                change_1d = 0
            
            # 1-week change (5 trading days)
            if len(df) >= 6:
                week_ago = df["close"].iloc[-6]
                change_1w = ((current_price - week_ago) / week_ago) * 100
            else:
                change_1w = change_1d
            
            # 1-month change
            if len(df) >= 20:
                month_ago = df["close"].iloc[-20]
                change_1m = ((current_price - month_ago) / month_ago) * 100
            else:
                change_1m = change_1w
            
            # Momentum score (weighted average)
            momentum = (change_1d * 0.3) + (change_1w * 0.5) + (change_1m * 0.2)
            
            results.append({
                "etf": etf,
                "name": info["name"],
                "emoji": info["emoji"],
                "stocks": info["stocks"],
                "price": round(current_price, 2),
                "change_1d": round(change_1d, 2),
                "change_1w": round(change_1w, 2),
                "change_1m": round(change_1m, 2),
                "momentum": round(momentum, 2),
            })
        except Exception:
            continue
    
    # Sort by momentum (best first)
    results.sort(key=lambda x: x["momentum"], reverse=True)
    return results


# ──────────────────────────────────────────────
# STOCK SCANNER
# ──────────────────────────────────────────────

# ──────────────────────────────────────────────
# SHARED SCORING ENGINE
# ──────────────────────────────────────────────

def _compute_verdict_confidence_score(signals, signal_names, primary_candle_bias, vol_profile):
    """
    Single source of truth for verdict, confidence, and score.
    Improvements vs original:
      - Score threshold raised: BULLISH requires >= 3, BEARISH requires <= -3
      - Vol trend DISTRIBUTING penalises by -1 (warns of exhaustion on borderline trades)
      - ACCUMULATING does NOT add bonus — it promoted 25% win-rate LEAN trades to BULLISH
      - Confidence incorporates score magnitude, not just divergent-count
    Returns (verdict, confidence, score, signal_names).
    """
    vol_trend = vol_profile["vol_trend"] if vol_profile else "FLAT"
    vol_bias  = vol_profile["vol_bias"]  if vol_profile else "NEUTRAL"

    # DISTRIBUTING penalises the prevailing direction — warns of exhaustion.
    # No ACCUMULATING bonus: it would promote LEAN+ACC trades (25% WR) to BULLISH.
    # ACCUMULATING confirmation is already captured via vol_surge upstream.
    if vol_trend == "DISTRIBUTING":
        if vol_bias == "BULLISH":
            signals.append(-1); signal_names.append("VolTrend:DIST-")
        elif vol_bias == "BEARISH":
            signals.append(1);  signal_names.append("VolTrend:DIST+")

    if not signals:
        return "NEUTRAL", "N/A", 0, signal_names

    score = sum(signals)

    # Raised thresholds based on backtest data (score 2 = coin flip, no edge)
    if score >= 3:
        verdict = "BULLISH"
    elif score <= -3:
        verdict = "BEARISH"
    elif score >= 2:
        verdict = "LEAN BULLISH"
    elif score <= -2:
        verdict = "LEAN BEARISH"
    elif score > 0:
        verdict = "LEAN BULLISH"
    elif score < 0:
        verdict = "LEAN BEARISH"
    else:
        verdict = "NEUTRAL"

    # Confidence: incorporates both divergent-signal count AND score magnitude
    bullish_count = sum(1 for s in signals if s > 0)
    bearish_count = sum(1 for s in signals if s < 0)
    if primary_candle_bias == "BULLISH":
        divergent = bearish_count
    elif primary_candle_bias == "BEARISH":
        divergent = bullish_count
    else:
        divergent = 0

    if primary_candle_bias in ("BULLISH", "BEARISH"):
        if abs(score) >= 4 and divergent == 0:
            confidence = "HIGH"
        elif abs(score) >= 3 and divergent <= 1:
            confidence = "HIGH"
        elif divergent == 0:
            confidence = "MEDIUM"
        elif divergent == 1:
            confidence = "MEDIUM"
        else:
            confidence = "LOW"
    else:
        confidence = "N/A"

    return verdict, confidence, score, signal_names


def _calc_rsi(close_series, period=14):
    """Fast RSI calculation using EWM. Handles all-up or all-down series cleanly."""
    delta = close_series.diff()
    gain  = delta.clip(lower=0).ewm(com=period - 1, min_periods=period).mean()
    loss  = (-delta.clip(upper=0)).ewm(com=period - 1, min_periods=period).mean()
    # When loss == 0: all gains → RSI = 100. Use np.where to avoid NaN.
    rsi = pd.Series(
        np.where(loss == 0, 100.0, np.where(gain == 0, 0.0, 100 - 100 / (1 + gain / loss))),
        index=close_series.index,
    )
    # Mask warmup period
    rsi[gain.isna()] = np.nan
    return rsi


# Popular stocks to scan
SCAN_WATCHLIST = [
    "AAPL", "MSFT", "GOOGL", "AMZN", "NVDA", "META", "TSLA", "AMD", "NFLX", "CRM",
    "ORCL", "ADBE", "INTC", "PYPL", "SQ", "SHOP", "COIN", "UBER", "ABNB", "SNOW",
    "BA", "CAT", "GS", "JPM", "V", "MA", "DIS", "NKE", "SBUX", "MCD",
    "XOM", "CVX", "PFE", "JNJ", "UNH", "MRNA", "LLY", "ABBV", "BMY", "MRK",
    "SPY", "QQQ", "DIA", "XLF", "XLE", "XLK", "ARKK", "SOXX", "SMH"
    # IWM removed: trend-following signals have no predictive power on small-cap
    # mean-reverting ETFs. 44% win rate at score 4+/HIGH conf even after all filters.
    # Break-even requires 51.9% WR at this R:R ratio — not achievable with current signals.
]

# ── Instrument exclusion list ──────────────────────────────────────────────
# Tickers where the trend-following signal stack has been proven to have no
# predictive power. Win rate below break-even after all filters applied.
# Criteria for exclusion: WR < break-even (avg_loss / (avg_win + avg_loss))
#   after applying score ≥ 4 + HIGH conf + no-short + adaptive ATR filters.
# To re-enable a ticker: backtest it first on ≥ 30 trades and confirm WR > 55%.
EXCLUDED_INSTRUMENTS = {
    "IWM",   # Mean-reverting small-cap ETF. Backtest: 44.4% WR even at score 4+/HIGH.
             # Now also caught automatically by _classify_instrument persistence check,
             # but kept here as an explicit hard block with a clear reason string.
}


def _is_instrument_supported(ticker: str, daily_df) -> tuple:
    """
    Check whether the current instrument is suitable for the trend-following model.
    Returns (supported: bool, reason: str).
    Uses _classify_instrument (data-driven) — no hardcoded ticker lists.
    """
    t = ticker.upper().strip()

    # 1. Hard exclusion list (manually backtested failures)
    if t in EXCLUDED_INSTRUMENTS:
        return False, (f"{t} is in EXCLUDED_INSTRUMENTS — "
                       f"trend signals not reliable on this instrument")

    # 2. Data-driven classification — detects mean-reverting behaviour dynamically
    if daily_df is not None and not daily_df.empty and len(daily_df) >= 40:
        profile = _classify_instrument(t, daily_df)
        if profile["is_mean_rev"]:
            pct   = profile["atr_pct"] * 100
            pers  = profile["persistence"] * 100
            qt    = profile["quote_type"]
            return False, (
                f"{t} classified as mean-reverting "
                f"(ATR%={pct:.1f}%, persistence={pers:.0f}%, type={qt}). "
                f"Trend-following signals unreliable — backtest before trading."
            )

    return True, ""


# ── Dynamic instrument classification ─────────────────────────────────────
# Replaces hardcoded ETF list. Detects mean-reverting behaviour from price data
# so any instrument — ETF or stock — is classified correctly without manual upkeep.

def _classify_instrument(ticker: str, daily_df) -> dict:
    """
    Dynamically classify an instrument's behaviour from its price history.
    Returns dict with:
      atr_14        — 14-day ATR in price units
      atr_pct       — ATR as % of current price (decimal, e.g. 0.015 = 1.5%)
      persistence   — % of days price continues prior day's direction (0–1)
      is_mean_rev   — True if instrument is mean-reverting (apply ETF-style rules)
      quote_type    — "ETF" | "EQUITY" | "UNKNOWN" (from yfinance when available)

    Classification (3-tier priority):
      1. yfinance quoteType="ETF"    → is_mean_rev=True  (hard confirm)
         yfinance quoteType="EQUITY" → is_mean_rev=False (hard confirm)
      2. No yfinance: persistence < 0.50 AND 1.0% < ATR% ≤ 2.0%
         (tight fallback — avoids mis-classifying high-vol trending stocks like NVDA)
    """
    close  = daily_df["close"].values.astype(float)
    high   = daily_df["high"].values.astype(float)
    low    = daily_df["low"].values.astype(float)

    # ATR-14
    if len(close) >= 15:
        tr = np.maximum(high[1:] - low[1:],
             np.maximum(np.abs(high[1:] - close[:-1]),
                        np.abs(low[1:]  - close[:-1])))
        atr_14 = float(np.mean(tr[-14:]))
    else:
        atr_14 = float(np.mean(high - low))
    atr_pct = atr_14 / float(close[-1]) if close[-1] > 0 else 0.0

    # Trend persistence — last 60 trading days (~3 months)
    if len(close) >= 40:
        directions = np.sign(close[1:] - close[:-1])
        d = directions[-60:]
        continuations = int(np.sum((d[1:] == d[:-1]) & (d[:-1] != 0)))
        total_moves   = int(np.sum(d[:-1] != 0))
        persistence   = continuations / total_moves if total_moves > 0 else 0.5
    else:
        persistence = 0.5

    # quoteType from yfinance (most reliable when available)
    quote_type = "UNKNOWN"
    if YFINANCE_AVAILABLE:
        try:
            import yfinance as yf
            qt = yf.Ticker(ticker).info.get("quoteType", "UNKNOWN")
            quote_type = qt.upper() if qt else "UNKNOWN"
        except Exception:
            pass

    # Classification (3-tier):
    if quote_type == "ETF":
        is_mean_rev = True
    elif quote_type == "EQUITY":
        is_mean_rev = False
    else:
        # Fallback — require strong reversal signal:
        # persistence < 0.50 (actual reversal, not just "not trending")
        # AND ATR% in 1-2% band (mid-vol instruments like IWM)
        # Very high ATR% (>2%) stocks are volatile but directional — don't flag them.
        is_mean_rev = (persistence < 0.50 and 0.010 < atr_pct <= 0.020)

    return {
        "atr_14":      round(atr_14, 4),
        "atr_pct":     round(atr_pct, 4),
        "persistence": round(persistence, 3),
        "is_mean_rev": is_mean_rev,
        "quote_type":  quote_type,
    }
# Source: 45-trade SPY backtest. Each tier shows observed win rate and avg P&L.
# Used to annotate live scan results with realistic performance expectations.
ENTRY_GRADE_TABLE = {
    # (abs_score, confidence) → (grade, label, expected_wr, expected_avg_pnl, color)
    (5, "HIGH"):   ("S",  "STRONG ENTER",  100, 3.09,  "#00e5a0"),
    (4, "HIGH"):   ("A",  "ENTER",          86, 1.19,  "#00e5a0"),
    (3, "HIGH"):   ("B",  "ENTER",          73, 0.47,  "#4d9fff"),
    (3, "MEDIUM"): ("B-", "ENTER",          67, 0.35,  "#4d9fff"),
    (2, "HIGH"):   ("C",  "CAUTION",        50,-0.05,  "#f0c040"),
    (2, "MEDIUM"): ("C",  "CAUTION",        43,-0.46,  "#f0c040"),
    (1, "HIGH"):   ("D",  "WEAK — SKIP",    33,-0.76,  "#ff8c42"),
    (1, "MEDIUM"): ("D",  "WEAK — SKIP",    33,-0.76,  "#ff8c42"),
}

def _get_entry_grade(score: int, confidence: str) -> dict:
    """
    Return backtest-derived entry grade for a given score + confidence.
    Falls back gracefully for combinations not in the table.
    """
    key = (abs(score), confidence)
    if key in ENTRY_GRADE_TABLE:
        grade, label, wr, avg_pnl, color = ENTRY_GRADE_TABLE[key]
    elif abs(score) >= 4 and confidence == "HIGH":
        grade, label, wr, avg_pnl, color = "A", "ENTER", 86, 1.19, "#00e5a0"
    elif abs(score) >= 3:
        grade, label, wr, avg_pnl, color = "B", "ENTER", 67, 0.35, "#4d9fff"
    elif abs(score) == 2:
        grade, label, wr, avg_pnl, color = "C", "CAUTION", 47, -0.25, "#f0c040"
    else:
        grade, label, wr, avg_pnl, color = "D", "WEAK — SKIP", 33, -0.76, "#ff8c42"
    return {
        "entry_grade":    grade,
        "entry_label":    label,
        "expected_wr":    wr,
        "expected_avg":   avg_pnl,
        "grade_color":    color,
    }


def _compute_weekly_bias(daily_df):
    """
    Weekly trend direction from daily data.
    Bullish: close above prior week high, or uptrend structure (higher highs/lows).
    Bearish: close below prior week low, or downtrend structure.
    """
    try:
        if daily_df.empty or len(daily_df) < 10:
            return "NEUTRAL"
        df = daily_df.copy()
        df.index = pd.to_datetime(df.index)
        weekly = df.resample("W").agg({"open": "first", "high": "max", "low": "min", "close": "last"}).dropna()
        if len(weekly) < 3:
            return "NEUTRAL"
        curr_close = float(weekly["close"].iloc[-1])
        prev_high = float(weekly["high"].iloc[-2])
        prev_low = float(weekly["low"].iloc[-2])
        # Structure check: last 3 weeks higher highs & higher lows
        hh = weekly["high"].iloc[-3:].tolist()
        hl = weekly["low"].iloc[-3:].tolist()
        uptrend = (hh[-1] > hh[-2] > hh[-3]) or (hl[-1] > hl[-2])
        downtrend = (hh[-1] < hh[-2] < hh[-3]) or (hl[-1] < hl[-2])
        if curr_close > prev_high or uptrend:
            return "BULLISH"
        elif curr_close < prev_low or downtrend:
            return "BEARISH"
        return "NEUTRAL"
    except Exception:
        return "NEUTRAL"


def _compute_4h_bias(ticker, daily_df, api_key, api_secret, data_source):
    """
    4H entry trigger — check latest 4H candle direction.
    Pullback to support = bullish; breakdown from resistance = bearish.
    Falls back to last 2 daily candles if hourly data unavailable.
    """
    try:
        end_date = date.today()
        start_4h = end_date - timedelta(days=5)
        hourly_df = pd.DataFrame()
        if YFINANCE_AVAILABLE:
            try:
                hourly_df = get_hourly_bars_yfinance(ticker, str(start_4h), str(end_date))
            except Exception:
                pass
        if hourly_df.empty:
            try:
                if data_source == "Alpaca":
                    hourly_df = get_hourly_bars_alpaca(ticker, str(start_4h), str(end_date), api_key, api_secret)
                else:
                    hourly_df = get_hourly_bars(ticker, str(start_4h), str(end_date), api_key)
            except Exception:
                pass
        if not hourly_df.empty and len(hourly_df) >= 4:
            # Build 4H bars by resampling
            hdf = hourly_df.copy()
            hdf.index = pd.to_datetime(hdf.index)
            bars_4h = hdf.resample("4h").agg({"open": "first", "high": "max", "low": "min", "close": "last"}).dropna()
            if len(bars_4h) >= 2:
                last = bars_4h.iloc[-1]
                prev = bars_4h.iloc[-2]
                last_bull = float(last["close"]) > float(last["open"])
                # Pullback to support: prev bearish, current bullish reversal
                pullback_bull = (float(prev["close"]) < float(prev["open"])) and last_bull
                # Breakout: current 4H close above prev 4H high
                breakout_bull = float(last["close"]) > float(prev["high"])
                last_bear = float(last["close"]) < float(last["open"])
                pullback_bear = (float(prev["close"]) > float(prev["open"])) and last_bear
                breakout_bear = float(last["close"]) < float(prev["low"])
                if pullback_bull or breakout_bull or last_bull:
                    return "BULLISH"
                elif pullback_bear or breakout_bear or last_bear:
                    return "BEARISH"
                return "NEUTRAL"
        # Fallback: use last 2 daily candles as proxy
        if len(daily_df) >= 2:
            last_d = daily_df.iloc[-1]
            if float(last_d["close"]) > float(last_d["open"]):
                return "BULLISH"
            elif float(last_d["close"]) < float(last_d["open"]):
                return "BEARISH"
        return "NEUTRAL"
    except Exception:
        return "NEUTRAL"


def _mtf_signal_action(w, d, h4):
    """
    Map Weekly / Daily / 4H bias combo to (rank, signal, action).
    rank: 1 = best (all aligned), 2 = 2-of-3 aligned, 3+ = weaker/conflicting.
    """
    key = (
        "B" if "BULLISH" in (w or "") else ("R" if "BEARISH" in (w or "") else "N"),
        "B" if "BULLISH" in (d or "") else ("R" if "BEARISH" in (d or "") else "N"),
        "B" if "BULLISH" in (h4 or "") else ("R" if "BEARISH" in (h4 or "") else "N"),
    )
    _MAP = {
        # ── Rank 1: All three aligned ──
        ("B","B","B"): (1, "A+ Long",  "Full size CALL — all TFs agree"),
        ("R","R","R"): (1, "A+ Short", "Full size PUT — all TFs agree"),
        # ── Rank 2: Two aligned + neutral ──
        ("B","B","N"): (2, "Strong Long, wait 4H",    "Long confirmed — wait for 4H trigger"),
        ("B","N","B"): (2, "Long pullback entry",     "Weekly up, daily pausing, 4H triggering — dip buy"),
        ("N","B","B"): (2, "Short-term Long",         "No weekly trend — smaller size, quick target"),
        ("R","R","N"): (2, "Strong Short, wait 4H",   "Short confirmed — wait for 4H breakdown"),
        ("R","N","R"): (2, "Short pullback entry",    "Weekly down, daily pausing, 4H confirming"),
        ("N","R","R"): (2, "Short-term Short",        "No weekly trend — smaller size PUT"),
        # ── Rank 3: Two aligned + one conflicting ──
        ("B","B","R"): (3, "Pullback in uptrend",     "4H dip in bull trend — buy-the-dip if support holds"),
        ("R","R","B"): (3, "Dead cat bounce",         "4H bounce in downtrend — fade rally or wait"),
        ("B","R","B"): (3, "Choppy / reversal fight", "Mixed signals — reduce size"),
        ("R","B","R"): (3, "Counter-trend failing",   "Daily bounce but 4H rejecting — likely resumes down"),
        ("B","R","R"): (3, "Trend reversal warning",  "Weekly up but D+4H selling — no longs"),
        ("R","B","B"): (3, "Counter-trend bounce",    "D+4H bouncing vs weekly down — risky long, tight stop"),
        # ── Rank 4: One signal only ──
        ("B","N","N"): (4, "Too early — Long",        "Weekly up, no confirmation — watchlist only"),
        ("N","B","N"): (4, "Unconfirmed Long",        "Only daily bullish — need weekly or 4H"),
        ("N","N","B"): (4, "Noise — Long",            "Only 4H up — likely just a bounce"),
        ("R","N","N"): (4, "Too early — Short",       "Weekly down, no confirmation — watchlist"),
        ("N","R","N"): (4, "Unconfirmed Short",       "Only daily bearish — need more"),
        ("N","N","R"): (4, "Noise — Short",           "Only 4H down — likely just a dip"),
        # ── Rank 4: Mixed with neutral ──
        ("B","R","N"): (4, "Conflicted",              "Weekly up, daily down — wait for resolution"),
        ("B","N","R"): (4, "4H selling in uptrend",   "Watch for 4H reversal candle — possible dip buy"),
        ("R","B","N"): (4, "Counter-trend attempt",   "Daily bouncing vs weekly down — risky, sit out"),
        ("R","N","B"): (4, "4H bounce in downtrend",  "Likely dead cat — wait for daily confirm"),
        ("N","B","R"): (4, "Daily up, 4H failing",    "4H rejecting — daily move may exhaust"),
        ("N","R","B"): (4, "Daily down, 4H bouncing", "Speculative bottom fish — very small size only"),
        # ── Rank 5: No edge ──
        ("N","N","N"): (5, "No edge",                 "Sit out — no directional conviction"),
    }
    return _MAP.get(key, (5, "Unknown", "No data"))


def scan_single_stock(ticker, api_key, api_secret, data_source, use_fib=True, fib_tol=2.0, use_strategy=False):
    """
    Scan a single stock and return verdict/confidence.
    Returns dict with ticker, verdict, confidence, score, signals or None on error.
    """
    try:
        end_date = date.today()
        start_date = end_date - timedelta(days=365)  # 1 year for Fib levels
        
        # Fetch daily data
        if data_source == "Alpaca":
            daily_df = get_daily_bars_alpaca(ticker, str(start_date), str(end_date), api_key, api_secret)
        else:
            daily_df = get_daily_bars(ticker, str(start_date), str(end_date), api_key)
        
        if daily_df.empty or len(daily_df) < 20:
            return None

        # ── Instrument suitability gate ──
        supported, reason = _is_instrument_supported(ticker, daily_df)
        if not supported:
            return None   # Silently skip unsupported instruments in scanner

        current_price = daily_df["close"].iloc[-1]

        # Fibonacci bias
        fib_bias = "NEUTRAL"
        if use_fib:
            try:
                hi_52 = daily_df["high"].rolling(252).max().iloc[-1]
                lo_52 = daily_df["low"].rolling(252).min().iloc[-1]
                fib_result = nearest_fib(current_price, lo_52, hi_52, fib_tol)
                if fib_result:
                    fib_name = fib_result[0]
                    fib_pct = float(fib_name.split()[1].replace("%", ""))
                    if fib_pct >= 61.8:
                        fib_bias = "BEARISH"
                    elif fib_pct <= 38.2:
                        fib_bias = "BULLISH"
            except:
                pass

        # Support / Resistance levels
        sr_data = None
        try:
            sr_data = calc_support_resistance(daily_df)
        except:
            pass

        # ── Build signals — identical to _estimator_signal_at ─────────────────
        # Daily candle direction is the PRIMARY signal (×2).
        # Matches backtest engine exactly — grades reflect real historical WR.
        signals = []
        signal_names = []

        # 1. Daily candle direction (double weight — primary, backtest-proven)
        daily_close = float(daily_df["close"].iloc[-1])
        daily_open  = float(daily_df["open"].iloc[-1])
        candle_bias = "N/A"
        if daily_close > daily_open:
            signals.append(2); signal_names.append("Day:BULL")
            candle_bias = "BULLISH"
        elif daily_close < daily_open:
            signals.append(-2); signal_names.append("Day:BEAR")
            candle_bias = "BEARISH"

        # 2. Fibonacci (with S/R confluence bonus)
        if fib_bias == "BULLISH":
            signals.append(1); signal_names.append("Fib:BULL")
            if sr_data and sr_data.get("key_level"):
                kl = sr_data["key_level"]["price"]
                if abs(kl - current_price) / current_price < 0.015:
                    signals.append(1); signal_names.append("Fib+SR:BULL")
        elif fib_bias == "BEARISH":
            signals.append(-1); signal_names.append("Fib:BEAR")
            if sr_data and sr_data.get("key_level"):
                kl = sr_data["key_level"]["price"]
                if abs(kl - current_price) / current_price < 0.015:
                    signals.append(-1); signal_names.append("Fib+SR:BEAR")
        
        # Volume Profile bias + surge
        vol_profile = None
        vol_bias = "NEUTRAL"
        try:
            vol_profile = analyze_volume_profile(daily_df, lookback=50)
            if vol_profile:
                vol_bias = vol_profile["vol_bias"]
                if vol_bias == "BULLISH":
                    signals.append(1)
                    signal_names.append("Vol:BULL")
                elif vol_bias == "BEARISH":
                    signals.append(-1)
                    signal_names.append("Vol:BEAR")
                if vol_profile["vol_surge"]:
                    if vol_bias == "BULLISH":
                        signals.append(1)
                        signal_names.append("VolSurge:BULL")
                    elif vol_bias == "BEARISH":
                        signals.append(-1)
                        signal_names.append("VolSurge:BEAR")
        except:
            pass

        # Strategy (optional)
        if use_strategy:
            try:
                strategy_data = analyze_strategy_signals(daily_df)
                if strategy_data and "error" not in strategy_data:
                    if strategy_data.get("short_signal"):
                        signals.append(-2)
                        signal_names.append("Strat:SHORT")
                    elif strategy_data.get("long_signal"):
                        signals.append(2)
                        signal_names.append("Strat:LONG")
            except:
                pass
        
        if not signals:
            skip_reason = "No signals (doji + no Fib/Vol data)"
        else:
            skip_reason = None

        # ── Centralised verdict / confidence / score ──
        if not signals:
            # Still compute minimal result for display
            verdict, confidence, score = "NEUTRAL", "N/A", 0
            vol_trend = vol_profile["vol_trend"] if vol_profile else "N/A"
            signal_names = []
        else:
            verdict, confidence, score, signal_names = _compute_verdict_confidence_score(
                signals, signal_names, candle_bias, vol_profile
            )
            vol_trend = vol_profile["vol_trend"] if vol_profile else "N/A"

            # ── Filters — record reason but don't return None ──────────────────
            if verdict == "NEUTRAL":
                skip_reason = "NEUTRAL — no directional edge"
            elif score <= -4:
                skip_reason = f"Score {score} — extreme score block"
            elif confidence == "LOW":
                skip_reason = "LOW confidence"
            elif abs(score) >= 3:
                lc = (verdict in ("BULLISH", "LEAN BULLISH")  and vol_bias == "BEARISH")
                sc_ = (verdict in ("BEARISH", "LEAN BEARISH") and vol_bias == "BULLISH")
                if lc or sc_:
                    skip_reason = f"Vol conflict — {verdict} but vol={vol_bias}"

        # ── Per-ticker adaptive rules ─────────────────────────────────────────
        profile     = _classify_instrument(ticker, daily_df)
        atr_14      = profile["atr_14"]
        atr_pct     = profile["atr_pct"]
        is_mean_rev = profile["is_mean_rev"]

        if skip_reason is None:
            if is_mean_rev and verdict in ("BEARISH", "LEAN BEARISH"):
                skip_reason = f"Mean-rev instrument — no SHORT"
            elif is_mean_rev and abs(score) < 4:
                skip_reason = f"Mean-rev instrument — score {score} < 4 required"

        entry_status = "ENTER" if skip_reason is None else f"SKIP — {skip_reason}"
        atr_mult = 0.3 if is_mean_rev else 0.5

        # ── Entry/Exit levels (computed for all — useful context even for skips) ──
        recent_low  = daily_df["low"].iloc[-10:].min()
        recent_high = daily_df["high"].iloc[-10:].max()
        avg_daily_move = atr_14 * 0.6

        if verdict in ["BULLISH", "LEAN BULLISH"]:
            entry     = round(current_price, 2)
            stop_loss = round(recent_low - atr_14 * atr_mult, 2)
            risk      = entry - stop_loss
            target1   = round(entry + risk * 2, 2)
            target2   = round(entry + risk * 3, 2)
            risk_pct  = round((risk / entry) * 100, 1)
            t1_days   = max(1, round((target1 - entry) / avg_daily_move)) if avg_daily_move > 0 else None
            t2_days   = max(1, round((target2 - entry) / avg_daily_move)) if avg_daily_move > 0 else None
        elif verdict in ["BEARISH", "LEAN BEARISH"]:
            entry     = round(current_price, 2)
            stop_loss = round(recent_high + atr_14 * atr_mult, 2)
            risk      = stop_loss - entry
            target1   = round(entry - risk * 2, 2)
            target2   = round(entry - risk * 3, 2)
            risk_pct  = round((risk / entry) * 100, 1)
            t1_days   = max(1, round((entry - target1) / avg_daily_move)) if avg_daily_move > 0 else None
            t2_days   = max(1, round((entry - target2) / avg_daily_move)) if avg_daily_move > 0 else None
        else:
            entry     = round(current_price, 2)
            stop_loss = None; target1 = None; target2 = None
            risk_pct  = None; t1_days = None; t2_days = None
        
        # Get fundamentals (valuation + growth + profitability + risk)
        fundamentals = get_fundamentals(ticker)
        valuation = fundamentals.get("valuation", "N/A") if fundamentals else "N/A"
        valuation_color = fundamentals.get("valuation_color", "#6b7099") if fundamentals else "#6b7099"
        market_cap = fundamentals.get("market_cap_str", "N/A") if fundamentals else "N/A"
        target_price_1y = fundamentals.get("target_price") if fundamentals else None
        target_upside = fundamentals.get("target_upside") if fundamentals else None

        # ── Multi-timeframe biases (Weekly / 4H) ──
        weekly_bias = _compute_weekly_bias(daily_df)
        four_h_bias = _compute_4h_bias(ticker, daily_df, api_key, api_secret, data_source)
        
        result = {
            "ticker":      ticker,
            "price":       round(current_price, 2),
            "entry_status": entry_status,
            "weekly_bias": weekly_bias,
            "daily_bias":  candle_bias,
            "4h_bias":     four_h_bias,
            "verdict":     verdict,
            "confidence":  confidence,
            "score":       score,
            "signals":     ", ".join(signal_names),
            "candle":      candle_bias,
            "fib":         fib_bias,
            "vol_action":  vol_bias,
            "vol_trend":   vol_profile["vol_trend"] if vol_profile else "N/A",
            "vol_ratio":   vol_profile["vol_ratio"] if vol_profile else None,
            "poc":         vol_profile["poc"] if vol_profile else None,
            "val":         vol_profile["val"] if vol_profile else None,
            "vah":         vol_profile["vah"] if vol_profile else None,
            "vol_detail":  vol_profile["detail"] if vol_profile else "",
            "best_setup":  "Y" if (score >= 4 and confidence == "HIGH") else "N",
            "is_mean_rev": profile["is_mean_rev"],
            "persistence": round(profile["persistence"] * 100, 1),
            "quote_type":  profile["quote_type"],
            "entry":       entry,
            "stop_loss":   stop_loss,
            "target1":     target1,
            "target2":     target2,
            "risk_pct":    risk_pct,
            "t1_days":     t1_days,
            "t2_days":     t2_days,
            "valuation":        valuation,
            "valuation_color":  valuation_color,
            "market_cap":       market_cap,
            "target_1y":        target_price_1y,
            "target_upside":    target_upside,
        }

        # ── Multi-timeframe signal & action ──
        mtf_rank, mtf_signal, mtf_action = _mtf_signal_action(weekly_bias, candle_bias, four_h_bias)
        result["mtf_rank"]   = mtf_rank
        result["mtf_signal"] = mtf_signal
        result["mtf_action"] = mtf_action

        # Entry grade — shown for all trades; grade reflects signal strength
        grade_info = _get_entry_grade(score, confidence)
        result.update(grade_info)
        # Override grade label for filtered trades so it's crystal clear
        if entry_status != "ENTER":
            result["entry_label"] = entry_status

        _, suitability_reason = _is_instrument_supported(ticker, daily_df)
        result["suitability_reason"] = suitability_reason
        # Attach support/resistance levels
        if sr_data:
            result["supports"] = sr_data.get("supports", [])
            result["resistances"] = sr_data.get("resistances", [])
            result["pivot"] = sr_data.get("pivot")
            result["key_level"] = sr_data.get("key_level")
        # Attach extra fundamental fields when available
        if fundamentals:
            for fkey in ("sector", "pe_ratio", "forward_pe", "peg_ratio",
                         "revenue_growth", "earnings_growth", "profit_margin",
                         "roe", "debt_to_equity", "beta", "rec_key", "num_analysts",
                         "dividend_yield", "week52_position", "flags"):
                result[fkey] = fundamentals.get(fkey)
        return result
    except Exception as e:
        # Re-raise with ticker context so the caller can surface the real error
        raise RuntimeError(f"scan_single_stock({ticker}): {type(e).__name__}: {e}") from e


def scan_stocks(api_key, api_secret, data_source, watchlist=None, use_fib=True, fib_tol=2.0, use_strategy=False):
    """
    Scan multiple stocks and return bullish + high confidence ones.
    Returns list of result dicts sorted by score descending.
    """
    if watchlist is None:
        watchlist = SCAN_WATCHLIST
    
    results = []
    for ticker in watchlist:
        result = scan_single_stock(ticker, api_key, api_secret, data_source, use_fib, fib_tol, use_strategy)
        if result:
            results.append(result)
    
    # Top LONG setups: BULLISH + HIGH confidence, score >= 3
    bullish_high = [
        r for r in results
        if r["verdict"] == "BULLISH" and r["confidence"] == "HIGH" and r.get("score", 0) >= 3
    ]
    bullish_high.sort(key=lambda x: x["score"], reverse=True)

    # Top SHORT setups: BEARISH + HIGH confidence, score <= -3
    bearish_high = [
        r for r in results
        if r["verdict"] == "BEARISH" and r["confidence"] == "HIGH" and r.get("score", 0) <= -3
    ]
    bearish_high.sort(key=lambda x: x["score"])

    top_setups = bullish_high[:5] + bearish_high[:5]
    return top_setups, results


# ──────────────────────────────────────────────
# ESTIMATOR BACKTEST
# ──────────────────────────────────────────────

def _estimator_signal_at(daily_df, idx, use_fib=True, fib_tol=2.0, ticker=""):
    """
    Compute the estimator verdict/score/levels for a single bar index
    using only data available up to (and including) that index.
    Improvements: raised score thresholds, vol-trend modifier,
    score-aware confidence, redefined best_setup, per-ticker adaptive rules.
    Returns dict with verdict, score, entry, stop, target1, best_setup, etc. or None.
    """
    if idx < 50:
        return None
    df = daily_df.iloc[:idx + 1].copy()
    current_price = float(df["close"].iloc[-1])

    # Fibonacci bias
    fib_bias = "NEUTRAL"
    if use_fib:
        try:
            hi_52 = df["high"].rolling(min(252, len(df))).max().iloc[-1]
            lo_52 = df["low"].rolling(min(252, len(df))).min().iloc[-1]
            fib_result = nearest_fib(current_price, lo_52, hi_52, fib_tol)
            if fib_result:
                fib_pct = float(fib_result[0].split()[1].replace("%", ""))
                if fib_pct >= 61.8:
                    fib_bias = "BEARISH"
                elif fib_pct <= 38.2:
                    fib_bias = "BULLISH"
        except:
            pass

    # Volume Profile
    vol_profile = None
    vol_bias = "NEUTRAL"
    try:
        vol_profile = analyze_volume_profile(df, lookback=50)
        if vol_profile:
            vol_bias = vol_profile["vol_bias"]
    except:
        pass

    # ── Build signals ──
    signals = []
    signal_names = []

    # Daily candle direction (double weight — primary signal)
    candle_bias = "N/A"
    if df["close"].iloc[-1] > df["open"].iloc[-1]:
        signals.append(2); signal_names.append("Day:BULL")
        candle_bias = "BULLISH"
    elif df["close"].iloc[-1] < df["open"].iloc[-1]:
        signals.append(-2); signal_names.append("Day:BEAR")
        candle_bias = "BEARISH"

    # Fibonacci
    if fib_bias == "BULLISH":
        signals.append(1); signal_names.append("Fib:BULL")
    elif fib_bias == "BEARISH":
        signals.append(-1); signal_names.append("Fib:BEAR")

    # Volume Profile bias
    if vol_bias == "BULLISH":
        signals.append(1); signal_names.append("Vol:BULL")
    elif vol_bias == "BEARISH":
        signals.append(-1); signal_names.append("Vol:BEAR")

    # Surge amplifier
    if vol_profile and vol_profile["vol_surge"]:
        if vol_bias == "BULLISH":
            signals.append(1); signal_names.append("VolSurge:BULL")
        elif vol_bias == "BEARISH":
            signals.append(-1); signal_names.append("VolSurge:BEAR")

    if not signals:
        return None

    # ── Centralised verdict / confidence / score ──
    verdict, confidence, score, signal_names = _compute_verdict_confidence_score(
        signals, signal_names, candle_bias, vol_profile
    )

    # ── Drawdown filters ────────────────────────────────────────────────────
    # Filter A: Hard block score -4
    if score <= -4:
        return None

    # Filter B: Skip LOW confidence
    if confidence == "LOW":
        return None

    # Filter C: Vol conflict — only block high-conviction signals (|score|>=3)
    #           LEAN trades (score ±2) pass through — vol profile lags daily
    #           candle direction on individual stocks by days/weeks.
    if abs(score) >= 3:
        long_vol_conflict  = (verdict in ("BULLISH", "LEAN BULLISH")  and vol_bias == "BEARISH")
        short_vol_conflict = (verdict in ("BEARISH", "LEAN BEARISH") and vol_bias == "BULLISH")
        if long_vol_conflict or short_vol_conflict:
            return None

    # ── Per-ticker adaptive rules ────────────────────────────────────────────
    profile     = _classify_instrument(ticker, df)
    atr_14      = profile["atr_14"]
    atr_pct     = profile["atr_pct"]
    is_mean_rev = profile["is_mean_rev"]

    # Block SHORT on mean-reverting instruments
    if is_mean_rev and verdict in ("BEARISH", "LEAN BEARISH"):
        return None

    # Require score ≥ 4 on mean-reverting instruments
    if is_mean_rev and abs(score) < 4:
        return None

    # Tighter ATR stop on mean-reverting instruments only
    atr_mult = 0.3 if is_mean_rev else 0.5

    # Entry / Stop / Target
    recent_low = df["low"].iloc[-10:].min()
    recent_high = df["high"].iloc[-10:].max()

    avg_daily_move = atr_14 * 0.6

    if verdict in ("BULLISH", "LEAN BULLISH"):
        entry = current_price
        stop = round(float(recent_low - atr_14 * atr_mult), 2)
        risk = entry - stop
        t1 = round(entry + risk * 2, 2)
        direction = "LONG"
        dist_to_t1 = t1 - entry
        vol_trend = vol_profile["vol_trend"] if vol_profile else "FLAT"
        hold_multiplier = 0.7 if vol_trend == "DISTRIBUTING" else 1.0
    elif verdict in ("BEARISH", "LEAN BEARISH"):
        entry = current_price
        stop = round(float(recent_high + atr_14 * atr_mult), 2)
        risk = stop - entry
        t1 = round(entry - risk * 2, 2)
        direction = "SHORT"
        dist_to_t1 = entry - t1
        vol_trend = vol_profile["vol_trend"] if vol_profile else "FLAT"
        hold_multiplier = 0.7
    else:
        return None

    t1_days = max(1, round((dist_to_t1 / avg_daily_move) * hold_multiplier)) if avg_daily_move > 0 else 5

    vol_trend = vol_profile["vol_trend"] if vol_profile else "N/A"

    # Redefined best_setup: score >= 4 and HIGH confidence (data-driven threshold)
    best_setup = (abs(score) >= 4 and confidence == "HIGH")

    return {
        "verdict": verdict,
        "confidence": confidence,
        "score": score,
        "direction": direction,
        "entry": round(entry, 2),
        "stop": stop,
        "target1": t1,
        "t1_days": t1_days,
        "vol_bias": vol_bias,
        "vol_trend": vol_trend,
        "fib_bias": fib_bias,
        "candle_bias": candle_bias,
        "best_setup": best_setup,
        "signals": ", ".join(signal_names),
    }


def backtest_estimator(daily_df, use_fib=True, fib_tol=2.0, hold_days=5,
                       filter_best_only=False,
                       use_dynamic_hold=True, ticker=""):
    """
    Walk-forward backtest of estimator signals.
    Uses daily candle + Fib + Volume Profile signals.
    If use_dynamic_hold=True, each trade uses the ATR-based t1_days from the signal
    instead of a fixed hold_days.
    ticker: passed through for per-instrument adaptive rules (IWM, QQQ, etc.)
    Returns DataFrame of trades with P&L.
    """
    if daily_df is None or len(daily_df) < 80:
        return pd.DataFrame()

    trades = []
    dates = list(daily_df.index)
    i = 60  # start after enough warmup data
    min_advance = max(1, hold_days) if not use_dynamic_hold else 1

    while i < len(daily_df) - min_advance:
        sig = _estimator_signal_at(daily_df, i, use_fib, fib_tol, ticker=ticker)
        if sig is None:
            i += (hold_days if not use_dynamic_hold else 1)
            continue

        if filter_best_only and not sig["best_setup"]:
            i += (hold_days if not use_dynamic_hold else 1)
            continue

        if sig["verdict"] == "NEUTRAL":
            i += (hold_days if not use_dynamic_hold else 1)
            continue

        entry_date = dates[i]
        entry_px = sig["entry"]
        stop_px = sig["stop"]
        t1_px = sig["target1"]
        direction = sig["direction"]

        # Use dynamic ATR-based hold or fixed hold_days
        trade_hold = sig["t1_days"] if use_dynamic_hold else hold_days

        # Walk forward up to trade_hold days to check stop/target
        exit_px = None
        exit_date = None
        exit_reason = None
        for j in range(1, trade_hold + 1):
            if i + j >= len(daily_df):
                break
            bar_high = float(daily_df["high"].iloc[i + j])
            bar_low = float(daily_df["low"].iloc[i + j])
            bar_close = float(daily_df["close"].iloc[i + j])
            bar_date = dates[i + j]

            if direction == "LONG":
                if bar_low <= stop_px:
                    exit_px = stop_px
                    exit_date = bar_date
                    exit_reason = "STOP"
                    break
                if bar_high >= t1_px:
                    exit_px = t1_px
                    exit_date = bar_date
                    exit_reason = "TARGET"
                    break
            else:  # SHORT — take target on close (enforce exit discipline; every SHORT STOP is a loser)
                if bar_high >= stop_px:
                    exit_px = stop_px
                    exit_date = bar_date
                    exit_reason = "STOP"
                    break
                # Use bar_close for target: exit when price closes at/below target, not just wicks
                if bar_close <= t1_px:
                    exit_px = t1_px
                    exit_date = bar_date
                    exit_reason = "TARGET"
                    break

        # If neither stop nor target hit, exit at close of last hold day
        if exit_px is None:
            last_j = min(i + trade_hold, len(daily_df) - 1)
            exit_px = float(daily_df["close"].iloc[last_j])
            exit_date = dates[last_j]
            exit_reason = "EXPIRE"

        if direction == "LONG":
            pnl_pct = (exit_px - entry_px) / entry_px * 100
        else:
            pnl_pct = (entry_px - exit_px) / entry_px * 100

        trades.append({
            "entry_date": str(entry_date),
            "exit_date": str(exit_date),
            "direction": direction,
            "verdict": sig["verdict"],
            "confidence": sig["confidence"],
            "best_setup": "Y" if sig["best_setup"] else "N",
            "vol_bias": sig["vol_bias"],
            "vol_trend": sig["vol_trend"],
            "fib_bias": sig["fib_bias"],
            "score": sig["score"],
            "hold_days": trade_hold,
            "entry": round(entry_px, 2),
            "stop": stop_px,
            "target1": sig["target1"],
            "exit": round(exit_px, 2),
            "exit_reason": exit_reason,
            "pnl_pct": round(pnl_pct, 2),
            "win": pnl_pct > 0,
            "signals": sig["signals"],
        })

        i += trade_hold  # advance past hold period

    return pd.DataFrame(trades) if trades else pd.DataFrame()


def run_backtest(ticker, daily_df, hourly_df, earnings_events,
                 vol_threshold, use_vol, fib_tol, use_fib, use_4h, fib_tf="Weekly"):
    trades = []
    skipped_reasons = []  # Track why events were skipped
    daily_dates = list(daily_df.index)

    # Build weekly OHLCV once for weekly fib swing option
    # daily_df index is plain date objects — must convert to DatetimeIndex for resample
    try:
        _dfw = daily_df.copy()
        _dfw.index = pd.to_datetime(_dfw.index)
        weekly_df = _dfw.resample("W").agg({
            "open":   "first",
            "high":   "max",
            "low":    "min",
            "close":  "last",
            "volume": "sum",
        }).dropna()
        weekly_df.index = weekly_df.index.date  # back to date for consistent comparisons
    except Exception:
        weekly_df = pd.DataFrame()

    for idx, (report_date_str, label, period) in enumerate(earnings_events):
        try:
            report_date = datetime.strptime(report_date_str, "%Y-%m-%d").date()

            # ── Entry day = report date ──
            entry_date = report_date
            if entry_date not in daily_df.index:
                skipped_reasons.append((report_date_str, "entry_date not in price data"))
                continue

            # ── Exit day = next trading day ──
            exit_date = next_trading_day(entry_date, daily_dates)
            if exit_date is None or exit_date not in daily_df.index:
                skipped_reasons.append((report_date_str, "no valid exit date (next trading day)"))
                continue

            # ── 4H candle signal ──
            if use_4h and not hourly_df.empty:
                candle = get_4h_noon_candle(ticker, entry_date, hourly_df)
            else:
                candle = None

            if candle:
                signal_open  = candle["open"]
                signal_close = candle["close"]
                candle_type  = "4H"
            else:
                # fallback: daily open vs close
                row          = daily_df.loc[entry_date]
                signal_open  = row["open"]
                signal_close = row["close"]
                candle_type  = "daily"

            direction  = "LONG" if signal_close > signal_open else "SHORT"
            entry_px   = signal_close
            exit_px    = float(daily_df.loc[exit_date, "close"])
            day_move   = (float(daily_df.loc[entry_date, "close"]) - signal_open) / signal_open * 100

            pnl_pct = (
                (exit_px - entry_px) / entry_px * 100
                if direction == "LONG"
                else (entry_px - exit_px) / entry_px * 100
            )

            # ── Volume filter ──
            exit_vol     = float(daily_df.loc[exit_date, "volume"])
            recent_dates = [d for d in daily_dates if d < entry_date][-20:]
            avg_vol_20   = float(daily_df.loc[recent_dates, "volume"].mean()) if len(recent_dates) >= 5 else None
            vol_ratio    = exit_vol / avg_vol_20 if avg_vol_20 else None
            passes_vol   = (vol_ratio >= vol_threshold) if (use_vol and vol_ratio is not None) else (not use_vol)

            # ── Fibonacci ──
            if idx > 0:
                prev_exit_str = earnings_events[idx - 1][0]
                prev_exit_d   = next_trading_day(
                    datetime.strptime(prev_exit_str, "%Y-%m-%d").date(), daily_dates
                )
                swing_dates = [d for d in daily_dates if prev_exit_d and prev_exit_d <= d < report_date]
            else:
                # first event: use 52-week window
                one_yr_ago  = report_date - timedelta(days=365)
                swing_dates = [d for d in daily_dates if one_yr_ago <= d < report_date]

            if swing_dates and use_fib:
                if fib_tf == "Weekly" and not weekly_df.empty:
                    # Filter weekly bars whose week-end date falls in swing window
                    # Note: weekly_df.index is already date objects after resample
                    swing_start = swing_dates[0]
                    swing_end   = swing_dates[-1]
                    w_idx = list(weekly_df.index)
                    w_mask = [(d >= swing_start and d <= swing_end) for d in w_idx]
                    w_slice = weekly_df[w_mask]
                    if not w_slice.empty:
                        swing_hi = float(w_slice["high"].max())
                        swing_lo = float(w_slice["low"].min())
                    else:
                        # fallback to daily if no weekly bars in window
                        swing_hi = float(daily_df.loc[swing_dates, "high"].max())
                        swing_lo = float(daily_df.loc[swing_dates, "low"].min())
                else:
                    swing_hi = float(daily_df.loc[swing_dates, "high"].max())
                    swing_lo = float(daily_df.loc[swing_dates, "low"].min())
                fib_hit = nearest_fib(entry_px, swing_lo, swing_hi, fib_tol)
            else:
                swing_hi, swing_lo, fib_hit = None, None, None

            passes_fib = fib_hit is not None if use_fib else True
            passes_all = passes_vol and passes_fib

            trades.append({
                "q":           label,
                "report_date": str(report_date),
                "entry_date":  str(entry_date),
                "exit_date":   str(exit_date),
                "direction":   direction,
                "candle_type": candle_type,
                "signal_open": round(signal_open, 2),
                "entry":       round(entry_px, 2),
                "exit":        round(exit_px, 2),
                "pnl_pct":     round(pnl_pct, 2),
                "win":         pnl_pct > 0,
                "day_move":    round(day_move, 2),
                "vol_ratio":   round(vol_ratio, 2) if vol_ratio else None,
                "passes_vol":  passes_vol,
                "fib_hit":     fib_hit,
                "passes_fib":  passes_fib,
                "passes_all":  passes_all,
                "swing_lo":    round(swing_lo, 2) if swing_lo else None,
                "swing_hi":    round(swing_hi, 2) if swing_hi else None,
            })

        except Exception as e:
            skipped_reasons.append((report_date_str, f"error: {str(e)[:50]}"))
            continue

    result_df = pd.DataFrame(trades) if trades else pd.DataFrame()
    result_df.attrs["skipped_reasons"] = skipped_reasons
    return result_df


# ──────────────────────────────────────────────
# STATS
# ──────────────────────────────────────────────

def calc_stats(active_df):
    if active_df.empty:
        return {
            "total_return": 0.0,
            "win_rate": 0.0,
            "wins": 0,
            "losses": 0,
            "n": 0,
            "avg_win": 0.0,
            "avg_loss": 0.0,
            "profit_factor": None,
            "max_dd": 0.0,
            "avg_trade": 0.0,
            "equity": [100.0],
            "final_eq": 100.0,
        }
    pnls  = active_df["pnl_pct"].values
    mult  = np.prod(1 + pnls / 100)
    eq    = [100.0]
    peak  = 100.0
    maxdd = 0.0
    cur   = 100.0
    for p in pnls:
        cur *= 1 + p / 100
        if cur > peak: peak = cur
        dd = (peak - cur) / peak * 100
        if dd > maxdd: maxdd = dd
        eq.append(round(cur, 2))

    wins   = active_df[active_df["win"]]
    losses = active_df[~active_df["win"]]
    avg_w  = wins["pnl_pct"].mean() if len(wins) else 0
    avg_l  = losses["pnl_pct"].mean() if len(losses) else 0
    pf     = abs(len(wins) * avg_w / (len(losses) * avg_l)) if len(losses) and avg_l else float("inf")

    return {
        "total_return": round((mult - 1) * 100, 2),
        "win_rate":     round(len(wins) / len(active_df) * 100, 1),
        "wins":         len(wins),
        "losses":       len(losses),
        "n":            len(active_df),
        "avg_win":      round(avg_w, 2),
        "avg_loss":     round(avg_l, 2),
        "profit_factor":round(pf, 2) if pf != float("inf") else None,
        "max_dd":       round(maxdd, 2),
        "avg_trade":    round(pnls.mean(), 2),
        "equity":       eq,
        "final_eq":     round(cur, 2),
    }


# ──────────────────────────────────────────────
# CHARTS
# ──────────────────────────────────────────────

DARK = dict(
    paper_bgcolor="#07080d", plot_bgcolor="#07080d",
    font_color="#c8cce8", font_family="Courier New",
)
GREEN, RED, BLUE, PURPLE, YELLOW, CYAN = "#00e5a0","#ff4d6a","#4d9fff","#a78bfa","#f5c842","#22d3ee"


def equity_chart(active_df, stats):
    labels = ["START"] + list(active_df["q"])
    equity = stats["equity"]
    colors = ["#484f58"] + [GREEN if w else RED for w in active_df["win"]]

    fig = go.Figure()
    fig.add_trace(go.Scatter(
        x=labels, y=equity, mode="lines+markers",
        line=dict(color=GREEN if stats["total_return"] >= 0 else RED, width=2.5),
        marker=dict(color=colors, size=8, line=dict(color="#07080d", width=2)),
        fill="tozeroy", fillcolor="rgba(0,229,160,0.06)",
        hovertemplate="<b>%{x}</b><br>$%{y:.2f}<extra></extra>",
    ))
    fig.add_hline(y=100, line_dash="dot", line_color="#252840")
    fig.update_layout(
        **DARK, height=300, margin=dict(l=50, r=10, t=10, b=50),
        xaxis=dict(tickangle=-40, gridcolor="#1a1d2e", showline=False),
        yaxis=dict(tickprefix="$", gridcolor="#1a1d2e", showline=False),
        showlegend=False,
    )
    return fig


def pnl_bar_chart(all_df):
    colors = []
    for _, row in all_df.iterrows():
        if not row.get("passes_all", True):
            colors.append("rgba(58,61,92,0.4)")
        elif row["win"]:
            colors.append(f"rgba(0,229,160,0.8)")
        else:
            colors.append(f"rgba(255,77,106,0.8)")

    fig = go.Figure()
    fig.add_trace(go.Bar(
        x=all_df["q"], y=all_df["pnl_pct"],
        marker_color=colors,
        hovertemplate="<b>%{x}</b><br>P&L: %{y:.2f}%<extra></extra>",
    ))
    fig.add_hline(y=0, line_color="#252840")
    fig.update_layout(
        **DARK, height=300, margin=dict(l=50, r=10, t=10, b=50),
        xaxis=dict(tickangle=-40, gridcolor="#1a1d2e"),
        yaxis=dict(ticksuffix="%", gridcolor="#1a1d2e"),
        showlegend=False,
    )
    return fig


def fib_freq_chart(all_df):
    fib_hits = all_df[all_df["fib_hit"].notna()].copy()
    if fib_hits.empty:
        return None
    counts = {}
    for _, row in fib_hits.iterrows():
        name = row["fib_hit"][0]
        counts[name] = counts.get(name, {"count": 0, "wins": 0})
        counts[name]["count"] += 1
        if row["win"]: counts[name]["wins"] += 1
    rows       = sorted(counts.items(), key=lambda x: -x[1]["count"])
    # Display name: "Ret 61.8%" or "Ext 127.2%"
    disp_names = [("Ext " if r[0][0]=="E" else "Ret ") + r[0][1:] for r in rows]
    cnts       = [r[1]["count"] for r in rows]
    bar_colors = [YELLOW if r[0][0]=="E" else BLUE for r in rows]
    hover_text = [
        f'{"Extension" if r[0][0]=="E" else "Retracement"} {r[0][1:]}<br>'
        f'Hits: {r[1]["count"]}<br>Win: {r[1]["wins"]} / Loss: {r[1]["count"]-r[1]["wins"]}'
        for r in rows
    ]
    fig = go.Figure()
    fig.add_trace(go.Bar(
        x=cnts, y=disp_names, orientation="h",
        marker_color=bar_colors,
        text=[f'{"EXT" if r[0][0]=="E" else "RET"}' for r in rows],
        textposition="inside",
        textfont=dict(size=9, color="#07080d"),
        hovertext=hover_text,
        hoverinfo="text",
    ))
    fig.update_layout(
        **DARK, height=max(200, len(rows)*28+60),
        margin=dict(l=10, r=10, t=10, b=30),
        xaxis=dict(title="Hits", gridcolor="#1a1d2e"),
        yaxis=dict(gridcolor="#1a1d2e"),
        showlegend=False,
    )
    return fig


# ──────────────────────────────────────────────
# STREAMLIT APP
# ──────────────────────────────────────────────

st.set_page_config(
    page_title="StockPulse — Earnings & Market Intelligence",
    page_icon="🎯",
    layout="wide",
)

# ──────────────────────────────────────────────
# INSPIRATIONAL QUOTES (rotates on each load)
# ──────────────────────────────────────────────
import random
QUOTES = [
    ("The stock market is a device for transferring money from the impatient to the patient.", "Warren Buffett"),
    ("In investing, what is comfortable is rarely profitable.", "Robert Arnott"),
    ("The goal of a successful trader is to make the best trades. Money is secondary.", "Alexander Elder"),
    ("Risk comes from not knowing what you're doing.", "Warren Buffett"),
    ("The trend is your friend until the end when it bends.", "Ed Seykota"),
    ("Markets can remain irrational longer than you can remain solvent.", "John Maynard Keynes"),
    ("It's not whether you're right or wrong, but how much money you make when you're right.", "George Soros"),
    ("The four most dangerous words in investing are: This time it's different.", "Sir John Templeton"),
    ("Buy when there's blood in the streets, even if the blood is your own.", "Baron Rothschild"),
    ("Know what you own, and know why you own it.", "Peter Lynch"),
    ("An investment in knowledge pays the best interest.", "Benjamin Franklin"),
    ("Wide diversification is only required when investors do not understand what they are doing.", "Warren Buffett"),
    ("The secret to investing is to figure out the value of something — and then pay a lot less.", "Joel Greenblatt"),
    ("Opportunities come infrequently. When it rains gold, put out the bucket, not the thimble.", "Warren Buffett"),
]
_quote_text, _quote_author = random.choice(QUOTES)

st.markdown("""
<style>
  @import url('https://fonts.googleapis.com/css2?family=Inter:wght@300;400;600;700;900&family=Space+Mono:wght@400;700&display=swap');
  html, body, [class*="css"] { font-family: 'Inter', 'Space Mono', sans-serif !important; }
  .block-container { padding: 0.5rem 1.5rem 2rem; }
  div[data-testid="metric-container"] {
    background: #0d0f17; border: 1px solid #1a1d2e;
    padding: 12px 16px; border-radius: 4px;
  }
  div[data-testid="metric-container"] label { font-size: 9px !important; letter-spacing: 1.5px; color: #3a3d5c !important; }
  div[data-testid="metric-container"] div[data-testid="stMetricValue"] { font-size: 22px !important; font-weight: 900 !important; }
  .stDataFrame { border: 1px solid #1a1d2e; }
  .pill { display:inline-block; padding:2px 10px; border-radius:3px; font-size:10px; font-weight:700; letter-spacing:1px; }
  .pill-green { background:rgba(0,229,160,.12); color:#00e5a0; border:1px solid rgba(0,229,160,.3); }
  .pill-red   { background:rgba(255,77,106,.12); color:#ff4d6a; border:1px solid rgba(255,77,106,.3); }
  .pill-yellow{ background:rgba(245,200,66,.12); color:#f5c842; border:1px solid rgba(245,200,66,.3); }
  .pill-blue  { background:rgba(77,159,255,.12); color:#4d9fff; border:1px solid rgba(77,159,255,.3); }
  /* Tab styling */
  .stTabs [data-baseweb="tab-list"] { gap: 2px; background: #0a0b14; border-radius: 8px; padding: 4px; }
  .stTabs [data-baseweb="tab"] { height: 44px; padding: 0 20px; font-weight: 600; font-size: 13px;
    border-radius: 6px; color: #6b7099; }
  .stTabs [aria-selected="true"] { background: linear-gradient(135deg, #131625, #1a1d2e) !important;
    color: #e8ecff !important; border-bottom: 2px solid #00e5a0 !important; }
  /* Scrolling quote ticker */
  @keyframes ticker { 0% { transform: translateX(100%); } 100% { transform: translateX(-100%); } }
  .quote-ticker { overflow: hidden; white-space: nowrap; background: linear-gradient(90deg, #0a0b14, #0d0f17, #0a0b14);
    border: 1px solid #1a1d2e20; padding: 6px 0; margin-bottom: 8px; border-radius: 4px; }
  .quote-ticker span { display: inline-block; animation: ticker 25s linear infinite; font-size: 11px;
    color: #6b7099; font-style: italic; letter-spacing: 0.5px; }
</style>
""", unsafe_allow_html=True)

# ── Branded Header ──────────────────────────────────
st.markdown(
    '<div style="display:flex;align-items:center;gap:14px;margin-bottom:2px">'
    '<div style="width:44px;height:44px;border-radius:10px;'
    'background:linear-gradient(135deg,rgba(0,229,160,.2),rgba(77,159,255,.15));'
    'border:1px solid rgba(0,229,160,.35);display:flex;align-items:center;justify-content:center;'
    'font-size:22px">🎯</div>'
    '<div>'
    '<h2 style="margin:0;letter-spacing:0.5px;color:#e8ecff;font-weight:900;font-size:26px">STOCKPULSE</h2>'
    '<p style="margin:0;font-size:9px;color:#3a3d5c;letter-spacing:2.5px;font-weight:600">'
    'EARNINGS · TECHNICAL · SECTOR INTELLIGENCE</p>'
    '</div>'
    '</div>',
    unsafe_allow_html=True,
)

# Scrolling inspirational quote
st.markdown(
    f'<div class="quote-ticker"><span>'
    f'"{_quote_text}" — {_quote_author}'
    f'&nbsp;&nbsp;&nbsp;•&nbsp;&nbsp;&nbsp;'
    f'"{_quote_text}" — {_quote_author}'
    f'</span></div>',
    unsafe_allow_html=True,
)

# ── Sidebar (slim — API keys & global settings only) ──
with st.sidebar:
    st.markdown(
        '<div style="text-align:center;margin-bottom:8px">'
        '<div style="font-size:22px">🎯</div>'
        '<div style="font-size:14px;font-weight:900;color:#e8ecff;letter-spacing:1px">STOCKPULSE</div>'
        '<div style="font-size:8px;color:#3a3d5c;letter-spacing:2px">MARKET INTELLIGENCE</div>'
        '</div>',
        unsafe_allow_html=True,
    )
    st.markdown("---")

    # Auto-populate keys from files if available
    import os
    finnhub_api_key_val = ""
    alpaca_api_key_val = ""
    alpaca_api_secret_val = ""
    polygon_key_val = ""
    try:
        env_path = os.path.expanduser("algorithmic-trading/.env")
        if os.path.exists(env_path):
            with open(env_path, "r") as f:
                for line in f:
                    if "ALPACA_API_KEY" in line:
                        alpaca_api_key_val = line.split("=")[1].strip().strip('"')
                    if "ALPACA_API_SECRET" in line:
                        alpaca_api_secret_val = line.split("=")[1].strip().strip('"')
                    if "FINNHUB_API_KEY" in line:
                        finnhub_api_key_val = line.split("=")[1].strip().strip('"')
    except Exception:
        pass
    try:
        config_path = os.path.expanduser("algorithmic-trading/config.py")
        if os.path.exists(config_path):
            import ast
            with open(config_path, "r") as f:
                tree = ast.parse(f.read())
                for node in tree.body:
                    if isinstance(node, ast.Assign):
                        for target in node.targets:
                            if hasattr(target, 'id'):
                                if target.id == "API_KEY":
                                    alpaca_api_key_val = node.value.s
                                if target.id == "API_SECRET":
                                    alpaca_api_secret_val = node.value.s
                    if isinstance(node, ast.Assign) and hasattr(node.value, 'keys'):
                        for k, v in zip(node.value.keys, node.value.values):
                            if hasattr(k, 's') and hasattr(v, 's'):
                                if k.s == "API_KEY":
                                    alpaca_api_key_val = v.s
                                if k.s == "API_SECRET":
                                    alpaca_api_secret_val = v.s
    except Exception:
        pass
    def mask_key(key):
        if not key or len(key) < 6:
            return key
        return key[:2] + "*" * (len(key)-4) + key[-2:]

    st.markdown("### 🔑 API Keys")
    data_source = st.radio(
        "Data Source",
        options=["Alpaca", "Polygon"],
        index=0,
        horizontal=True,
        help="Alpaca: Free real-time data · Polygon: Free tier has ~7 day delay on hourly bars",
    )
    
    if data_source == "Alpaca":
        api_key = st.text_input(
            "Alpaca API Key", type="password",
            value=mask_key(alpaca_api_key_val) if alpaca_api_key_val else "",
            help="Get free keys at alpaca.markets",
            placeholder="your_alpaca_api_key",
        )
        api_secret = st.text_input(
            "Alpaca Secret Key", type="password",
            value=mask_key(alpaca_api_secret_val) if alpaca_api_secret_val else "",
            placeholder="your_alpaca_secret_key",
        )
        polygon_key = st.text_input(
            "Polygon Key (earnings)", type="password",
            value=mask_key(alpaca_api_key_val) if alpaca_api_key_val else "",
            help="Optional: for auto-detecting earnings dates via Polygon financials API",
            placeholder="optional_polygon_key",
        )
    else:
        api_key = st.text_input(
            "Polygon API Key", type="password",
            help="Get a free key at polygon.io",
            placeholder="your_polygon_api_key",
        )
        api_secret = None
        polygon_key = api_key

    finnhub_api_key = st.text_input(
        "Finnhub API Key (optional)",
        value=mask_key(finnhub_api_key_val) if finnhub_api_key_val else "",
        help="For fetching upcoming earnings tickers."
    )

    st.markdown("---")
    st.markdown("### ⚙️ Global Settings")
    use_fib = st.toggle("Fibonacci Zone Filter", value=True)
    if use_fib:
        fib_tol = st.slider("Fib Tolerance ±%", 0.5, 5.0, 2.0, 0.5)
        fib_tf  = st.radio("Swing timeframe", options=["Weekly", "Daily"], index=0, horizontal=True)
    else:
        fib_tol = 2.0
        fib_tf  = "Weekly"
    use_strategy = st.toggle("Strategy (Fib+Weinstein+Bias)", value=False,
                              help="Fib zones + FVG + Weinstein Stage + Volume Bias analysis")

    st.markdown("---")
    st.markdown(
        '<div style="font-size:9px;color:#3a3d5c;line-height:1.9;margin-top:4px">'
        '<b style="color:#6b7099">STRATEGY</b><br>'
        'Entry: AMC report day<br>'
        'Signal: 4H candle at ~1:30 PM ET<br>'
        'Green candle → LONG<br>'
        'Red candle → SHORT<br>'
        'Exit: Next trading day close'
        '</div>',
        unsafe_allow_html=True,
    )


# ── Credential check ─────────────────────────────────
if data_source == "Alpaca":
    missing_creds = not api_key or not api_secret
else:
    missing_creds = not api_key

if missing_creds:
    st.markdown(
        '<div style="text-align:center;padding:80px 0">'
        '<div style="font-size:60px;margin-bottom:16px;opacity:.25">🎯</div>'
        '<div style="color:#6b7099;font-size:16px;margin-bottom:8px;font-weight:600">'
        'Welcome to StockPulse</div>'
        '<div style="color:#3a3d5c;font-size:11px;line-height:2">'
        'Enter your API credentials in the sidebar to get started.<br>'
        '<b>Alpaca</b>: Free real-time data at '
        '<a href="https://alpaca.markets" style="color:#00e5a0">alpaca.markets</a><br>'
        '<b>Polygon</b>: Free tier at '
        '<a href="https://polygon.io" style="color:#4d9fff">polygon.io</a>'
        '</div></div>',
        unsafe_allow_html=True,
    )
    st.stop()

# Initialize session state
if "fetched_data" not in st.session_state:
    st.session_state.fetched_data = None

# Default shared values
default_watchlist = "AAPL,MSFT,GOOGL,AMZN,NVDA,META,TSLA,AMD,NFLX,CRM,ORCL,ADBE,INTC,PYPL,SQ,SHOP,COIN,UBER,ABNB,SNOW,BA,CAT,GS,JPM,V,MA,DIS,NKE,SBUX,MCD,XOM,CVX,PFE,JNJ,UNH,MRNA,LLY,ABBV,BMY,MRK,SPY,QQQ,IWM,DIA,XLF,XLE,XLK,ARKK,SOXX,SMH"

# ══════════════════════════════════════════════════════════════════════════════
# MAIN TABS — 6 Pages
# ══════════════════════════════════════════════════════════════════════════════
tab_fetch, tab_estimator, tab_sector, tab_backtest, tab_plan, tab_trades, tab_holdings, tab_macro = st.tabs([
    "📅 Stock Analysis (with Options)",
    "🔬 Stock Analysis",
    "🔥 Sector Scan",
    "📊 Backtest",
    "🗓️ Intraday Planning",
    "📋 Trade Tracker",
    "💼 My Holdings",
    "🌍 Macro",
])

# ╔══════════════════════════════════════════════════════════════════════════════╗
# ║ TAB 1: STOCK ANALYSIS (with Options) — Fetch Earnings + Analysis            ║
# ╚══════════════════════════════════════════════════════════════════════════════╝
with tab_fetch:
    st.markdown(
        '<div style="background:linear-gradient(135deg,#0a0b14,#131625);border:1px solid #1a1d2e;'
        'border-radius:8px;padding:18px 24px;margin-bottom:16px">'
        '<div style="display:flex;align-items:center;gap:12px">'
        '<div style="font-size:28px">📅</div>'
        '<div>'
        '<div style="font-size:16px;font-weight:700;color:#e8ecff">Earnings Day Analysis</div>'
        '<div style="font-size:10px;color:#6b7099">Real-time technical + options flow for earnings plays. '
        'Pure technical & options analysis for the trading day.</div>'
        '</div></div></div>',
        unsafe_allow_html=True,
    )

    # Inputs inline
    fe_col1, fe_col2, fe_col3, fe_col4 = st.columns([2, 1, 1, 1])
    with fe_col1:
        symbols_raw = st.text_input("Ticker(s) — comma-separated", value="TSLA", key="fetch_ticker").upper().strip()
        symbols_list = [s.strip() for s in symbols_raw.split(",") if s.strip()]
        symbol = symbols_list[0] if symbols_list else ""
    with fe_col2:
        years = st.slider("History (years)", 1, 8, 4, key="fetch_years")
    with fe_col3:
        last_n_earnings = st.slider("Last N Earnings", 1, 20, 4, key="fetch_last_n")
    with fe_col4:
        use_4h = st.toggle("4H Candle", value=True, key="fetch_4h")

    fe_col5, fe_col6 = st.columns(2)
    with fe_col5:
        use_vol = st.toggle("Volume Filter", value=True, key="fetch_vol")
        vol_min = st.slider("Min Vol Ratio", 1.0, 3.0, 1.5, 0.1, key="fetch_vol_min") if use_vol else 1.5
    with fe_col6:
        next_earnings_input = st.date_input(
            "Next Earnings Date (AMC)", value=None,
            min_value=date.today(), max_value=date.today() + timedelta(days=180),
            key="fetch_next_earn",
        )

    with st.expander("📋 Past Earnings Dates (optional — paste YYYY-MM-DD, one per line)"):
        manual_dates_raw = st.text_area(
            "Dates", placeholder="2024-10-29\n2024-07-23\n2024-04-23",
            height=100, key="fetch_manual_dates", label_visibility="collapsed",
        )
    manual_dates_list = [l.strip() for l in manual_dates_raw.strip().splitlines() if l.strip()] if manual_dates_raw.strip() else []

    fe_btn1, fe_btn2 = st.columns(2)
    with fe_btn1:
        fetch_btn = st.button("📅 FETCH EARNINGS", use_container_width=True, type="primary", key="btn_fetch")
    with fe_btn2:
        # run_btn = st.button("▶ RUN BACKTEST", use_container_width=True, key="btn_run")
        pass
    run_btn = False  # Backtest moved to Backtest tab

    if not fetch_btn and not run_btn:
        if st.session_state.fetched_data is not None:
            data = st.session_state.fetched_data
            st.markdown(
                f'<div style="background:#0d0f1799;border:1px solid #1a1d2e;padding:12px;border-radius:4px;margin-bottom:12px">'
                f'<div style="font-size:11px;color:#6b7099">📅 <b style="color:#e8ecff">{data["symbol"]}</b> · '
                f'{len(data["earnings_events"])} earnings events loaded</div>'
                f'</div>',
                unsafe_allow_html=True,
            )
        else:
            st.markdown(
                '<div style="text-align:center;padding:50px 0">'
                '<div style="font-size:48px;margin-bottom:12px;opacity:.2">📊</div>'
                '<div style="color:#6b7099;font-size:13px">Enter a ticker and click '
                '<b style="color:#4d9fff">📅 FETCH EARNINGS</b> to analyze</div>'
                '</div>',
                unsafe_allow_html=True,
            )

# ╔══════════════════════════════════════════════════════════════════════════════╗
# ║ TAB 2: STOCK ANALYSIS (Technical + Fundamental, CSV export)                 ║
# ╚══════════════════════════════════════════════════════════════════════════════╝
with tab_estimator:
    st.markdown(
        '<div style="background:linear-gradient(135deg,#0a0b14,#131625);border:1px solid #1a1d2e;'
        'border-radius:8px;padding:18px 24px;margin-bottom:16px">'
        '<div style="display:flex;align-items:center;gap:12px">'
        '<div style="font-size:28px">🔬</div>'
        '<div>'
        '<div style="font-size:16px;font-weight:700;color:#e8ecff">Stock Analysis</div>'
        '<div style="font-size:10px;color:#6b7099">Technical + Fundamental analysis. '
        'Scan a watchlist for upcoming earnings, get verdicts, valuations, growth & risk flags, and export CSV.</div>'
        '</div></div></div>',
        unsafe_allow_html=True,
    )

    # ── Fetch upcoming earnings from Finnhub ──────────────────────
    if "finnhub_earnings_tickers" not in st.session_state:
        st.session_state.finnhub_earnings_tickers = None

    fetch_col1, fetch_col2, fetch_col3 = st.columns([1, 1, 1])
    with fetch_col1:
        earn_range = st.selectbox("📅 Earnings window", [
            "Next week (Mon–Fri)",
            "This week",
            "Next 3 days",
            "Next 7 days",
            "Next 14 days",
        ], key="earn_range")
    with fetch_col2:
        fetch_earn_btn = st.button("📥 FETCH EARNINGS TICKERS", use_container_width=True, key="btn_fetch_earn")
    with fetch_col3:
        clear_earn_btn = st.button("🗑️ Clear", use_container_width=True, key="btn_clear_earn")

    if clear_earn_btn:
        st.session_state.finnhub_earnings_tickers = None
        st.rerun()

    if fetch_earn_btn:
        if not finnhub_api_key or finnhub_api_key.startswith("*"):
            st.error("Enter your Finnhub API key in the sidebar to fetch earnings tickers.")
        else:
            today = date.today()
            if earn_range == "Next week (Mon–Fri)":
                # Next Monday
                days_until_mon = (7 - today.weekday()) % 7
                if days_until_mon == 0:
                    days_until_mon = 7
                from_dt = today + timedelta(days=days_until_mon)
                to_dt = from_dt + timedelta(days=4)  # Friday
            elif earn_range == "This week":
                # This Monday through Friday
                from_dt = today - timedelta(days=today.weekday())
                to_dt = from_dt + timedelta(days=4)
            elif earn_range == "Next 3 days":
                from_dt = today
                to_dt = today + timedelta(days=3)
            elif earn_range == "Next 14 days":
                from_dt = today
                to_dt = today + timedelta(days=14)
            else:  # Next 7 days
                from_dt = today
                to_dt = today + timedelta(days=7)

            with st.spinner(f"Fetching earnings {from_dt} → {to_dt} ..."):
                try:
                    events = fetch_earnings_calendar_finnhub(finnhub_api_key, from_dt, to_dt)
                    if events:
                        st.session_state.finnhub_earnings_tickers = events
                        st.success(f"Found {len(events)} tickers reporting {from_dt} → {to_dt}")
                    else:
                        st.warning("No earnings found for that date range.")
                        st.session_state.finnhub_earnings_tickers = None
                except Exception as exc:
                    st.error(f"Finnhub error: {exc}")
                    st.session_state.finnhub_earnings_tickers = None

    # Show fetched earnings and let user use them as watchlist
    fetched_tickers_str = ""
    if st.session_state.finnhub_earnings_tickers:
        events = st.session_state.finnhub_earnings_tickers
        # Build summary table
        earn_df = pd.DataFrame(events)
        earn_df["hour"] = earn_df["hour"].replace({"bmo": "Before Open", "amc": "After Close", "dmh": "During Mkt", "": "TBD"})
        earn_df.columns = ["Ticker", "Date", "Timing", "EPS Est", "Rev Est"]
        with st.expander(f"📋 {len(events)} Earnings Tickers Fetched — click to view", expanded=False):
            st.dataframe(earn_df, use_container_width=True, hide_index=True)
        fetched_tickers_str = ",".join([e["symbol"] for e in events])

    ee_col1, ee_col2 = st.columns([3, 1])
    with ee_col1:
        estimator_watchlist_raw = st.text_area(
            "Tickers to scan (comma-separated)",
            value=fetched_tickers_str if fetched_tickers_str else default_watchlist,
            height=80, key="est_watchlist", label_visibility="collapsed",
        )
    with ee_col2:
        earnings_days = st.number_input("Earnings in next N days", min_value=1, max_value=30, value=7, step=1, key="est_days")

    estimator_watchlist = [t.strip().upper() for t in estimator_watchlist_raw.strip().split(",") if t.strip()] if estimator_watchlist_raw.strip() else SCAN_WATCHLIST
    earnings_estimator_btn = st.button("🔬 SCAN", use_container_width=True, type="primary", key="btn_estimator")

    if not earnings_estimator_btn:
        st.markdown(
            '<div style="text-align:center;padding:50px 0">'
            '<div style="font-size:48px;margin-bottom:12px;opacity:.2">🔬</div>'
            '<div style="color:#6b7099;font-size:13px">Paste tickers above and click '
            '<b style="color:#4d9fff">🔬 SCAN</b> for fundamental + technical verdicts</div>'
            '<div style="color:#3a3d5c;font-size:10px;margin-top:8px">'
            'Includes: P/E valuation · analyst targets · sector · growth flags · entry/stop/target levels</div>'
            '</div>',
            unsafe_allow_html=True,
        )

# ╔══════════════════════════════════════════════════════════════════════════════╗
# ║ TAB 3: SECTOR SCAN                                                          ║
# ╚══════════════════════════════════════════════════════════════════════════════╝
with tab_sector:
    st.markdown(
        '<div style="background:linear-gradient(135deg,#0a0b14,#131625);border:1px solid #1a1d2e;'
        'border-radius:8px;padding:18px 24px;margin-bottom:16px">'
        '<div style="display:flex;align-items:center;gap:12px">'
        '<div style="font-size:28px">🔥</div>'
        '<div>'
        '<div style="font-size:16px;font-weight:700;color:#e8ecff">Sector Scan</div>'
        '<div style="font-size:10px;color:#6b7099">Identify hot & cold sectors. '
        'Scans all 11 S&P sectors for momentum, then finds top stocks within each.</div>'
        '</div></div></div>',
        unsafe_allow_html=True,
    )

    sector_scan_btn = st.button("🔥 SCAN ALL SECTORS", use_container_width=True, type="primary", key="btn_sector")

    if not sector_scan_btn:
        # Show sector overview cards
        st.markdown(
            '<div style="text-align:center;padding:30px 0">'
            '<div style="font-size:48px;margin-bottom:12px;opacity:.2">🔥</div>'
            '<div style="color:#6b7099;font-size:13px">Click '
            '<b style="color:#4d9fff">🔥 SCAN ALL SECTORS</b> to analyze sector momentum</div>'
            '</div>',
            unsafe_allow_html=True,
        )
        # Mini sector grid
        sect_cols = st.columns(4)
        for i, (etf, info) in enumerate(list(SECTOR_ETFS.items())[:8]):
            with sect_cols[i % 4]:
                st.markdown(
                    f'<div style="background:#0d0f17;border:1px solid #1a1d2e;padding:10px;'
                    f'border-radius:6px;margin-bottom:6px;text-align:center">'
                    f'<div style="font-size:20px">{info["emoji"]}</div>'
                    f'<div style="font-size:10px;color:#e8ecff;font-weight:600">{info["name"]}</div>'
                    f'<div style="font-size:8px;color:#3a3d5c">{etf}</div>'
                    f'</div>',
                    unsafe_allow_html=True,
                )

# ╔══════════════════════════════════════════════════════════════════════════════╗
# ║ TAB 4: BACKTEST (Walk-forward estimator signal backtest)                     ║
# ╚══════════════════════════════════════════════════════════════════════════════╝
with tab_backtest:
    st.markdown(
        '<div style="background:linear-gradient(135deg,#0a0b14,#131625);border:1px solid #1a1d2e;'
        'border-radius:8px;padding:18px 24px;margin-bottom:16px">'
        '<div style="display:flex;align-items:center;gap:12px">'
        '<div style="font-size:28px">📊</div>'
        '<div>'
        '<div style="font-size:16px;font-weight:700;color:#e8ecff">Backtest</div>'
        '<div style="font-size:10px;color:#6b7099">Walk-forward backtest — replays Fib + Volume Profile + Daily Candle signals '
        'on historical data. Enter a ticker, set hold period, and test signal accuracy.</div>'
        '</div></div></div>',
        unsafe_allow_html=True,
    )

    bt_col1, bt_col2, bt_col3 = st.columns([2, 1, 1])
    with bt_col1:
        bt_ticker = st.text_input("Backtest ticker", value="SPY", key="bt_est_ticker").upper().strip()
    with bt_col2:
        bt_years = st.number_input("Years of data", min_value=1, max_value=5, value=2, step=1, key="bt_est_years")
    with bt_col3:
        bt_use_dynamic = st.toggle("Use ATR target days", value=True, key="bt_dynamic_hold",
                                    help="ON = each trade uses the ATR-based target days from the signal (same as live scanner). "
                                         "OFF = use fixed hold period for all trades.")
    if not bt_use_dynamic:
        bt_hold = st.number_input("Fixed hold days", min_value=1, max_value=20, value=5, step=1, key="bt_est_hold")
    else:
        bt_hold = 5  # unused when dynamic
    bt_best_only = st.toggle("Best Setup only (Y)", value=False, key="bt_best_only",
                              help="Only take trades where Best Setup = Y (all signals aligned)")
    bt_run = st.button("▶ RUN BACKTEST", use_container_width=True, type="primary", key="btn_bt_est")

    # ── Date Lookup ──────────────────────────────────────────
    st.markdown("---")
    st.markdown(
        '<div style="font-size:12px;font-weight:700;color:#e8ecff;margin-bottom:8px">📅 Date Lookup</div>'
        '<div style="font-size:10px;color:#6b7099;margin-bottom:12px">'
        'Enter a date to see the estimator signal snapshot for that day.</div>',
        unsafe_allow_html=True,
    )
    dl_col1, dl_col2 = st.columns([2, 1])
    with dl_col1:
        dl_ticker = st.text_input("Lookup ticker", value="SPY", key="dl_ticker").upper().strip()
    with dl_col2:
        dl_date = st.date_input("Lookup date", value=date.today(), key="dl_date")
    dl_run = st.button("📅 LOOKUP DATE", use_container_width=True, key="btn_dl_date")

    if not bt_run and not dl_run:
        st.markdown(
            '<div style="text-align:center;padding:50px 0">'
            '<div style="font-size:48px;margin-bottom:12px;opacity:.2">📊</div>'
            '<div style="color:#6b7099;font-size:13px">Enter a ticker above and click '
            '<b style="color:#4d9fff">▶ RUN BACKTEST</b> to test estimator signals</div>'
            '</div>',
            unsafe_allow_html=True,
        )

# ╔══════════════════════════════════════════════════════════════════════════════╗
# ║ TAB: INTRADAY PLANNING                                                       ║
# ╚══════════════════════════════════════════════════════════════════════════════╝
with tab_plan:
    st.markdown(
        '<div style="background:linear-gradient(135deg,#0a0b14,#131625);border:1px solid #1a1d2e;'
        'border-radius:8px;padding:18px 24px;margin-bottom:16px">'
        '<div style="display:flex;align-items:center;gap:12px">'
        '<div style="font-size:28px">🗓️</div>'
        '<div>'
        '<div style="font-size:16px;font-weight:700;color:#e8ecff">Intraday Planning</div>'
        '<div style="font-size:10px;color:#6b7099">Run after market close — generates a trade plan for '
        'tomorrow based on today\'s signals. Shows direction, entry, stop, targets, and what to do at the open.<br>'
        'Use <b>Check Open Prices</b> the next morning to see which scenario played out.</div>'
        '</div></div></div>',
        unsafe_allow_html=True,
    )
    plan_tickers_raw = st.text_area(
        "Tickers (comma-separated)", value="SPY, QQQ, AAPL, MSFT, NVDA, TSLA, AMZN, META, GOOG",
        height=60, key="plan_tickers", label_visibility="collapsed",
    )
    plan_date = st.date_input("Plan date (close of this day)", value=date.today(), key="plan_date")
    plan_run = st.button("🗓️ GENERATE PLAN", use_container_width=True, type="primary", key="btn_plan")
    check_open_run = st.button("☀️ CHECK OPEN PRICES", use_container_width=True, key="btn_check_open")

    if not plan_run and not check_open_run:
        st.markdown(
            '<div style="text-align:center;padding:50px 0">'
            '<div style="font-size:48px;margin-bottom:12px;opacity:.2">🗓️</div>'
            '<div style="color:#6b7099;font-size:13px">Enter tickers, pick a date, and click '
            '<b style="color:#4d9fff">🗓️ GENERATE PLAN</b> to create your intraday trade plan</div>'
            '</div>',
            unsafe_allow_html=True,
        )


# ══════════════════════════════════════════════════════════════════════════════
# BUTTON HANDLERS — each runs inside its tab
# ══════════════════════════════════════════════════════════════════════════════

# ── Stock Analysis ──────────────────────────
if earnings_estimator_btn:
  with tab_estimator:
    st.markdown(f"### 🔬 Stock Analysis — Scanning {len(estimator_watchlist)} Tickers")
    progress_bar = st.progress(0)
    status_text = st.empty()
    total = len(estimator_watchlist)

    scan_results  = []      # all tickers with signals
    scan_errors   = []      # hard errors (API failures etc.)
    scan_no_data  = []      # tickers that returned no data (timeout, bad ticker, etc.)
    for i, ticker in enumerate(estimator_watchlist):
        status_text.text(f"Analyzing {ticker}... ({i+1}/{total})")
        progress_bar.progress((i + 1) / total)
        try:
            result = scan_single_stock(ticker, api_key, api_secret, data_source, use_fib, fib_tol, use_strategy)
            if result:
                fundies = get_fundamentals(ticker)
                if fundies:
                    result["sector"]          = fundies.get("sector", "N/A")
                    result["industry"]        = fundies.get("industry", "N/A")
                    result["pe_ratio"]        = fundies.get("pe_ratio")
                    result["forward_pe"]      = fundies.get("forward_pe")
                    result["peg_ratio"]       = fundies.get("peg_ratio")
                    result["analyst_target"]  = fundies.get("target_price")
                    result["revenue_growth"]  = fundies.get("revenue_growth")
                    result["earnings_growth"] = fundies.get("earnings_growth")
                    result["profit_margin"]   = fundies.get("profit_margin")
                    result["roe"]             = fundies.get("roe")
                    result["debt_to_equity"]  = fundies.get("debt_to_equity")
                    result["beta"]            = fundies.get("beta")
                    result["dividend_yield"]  = fundies.get("dividend_yield")
                    result["short_pct"]       = fundies.get("short_pct")
                    result["week52_position"] = fundies.get("week52_position")
                    result["pct_from_high"]   = fundies.get("pct_from_high")
                    result["rec_key"]         = fundies.get("rec_key", "")
                    result["num_analysts"]    = fundies.get("num_analysts")
                    result["revenue_str"]     = fundies.get("revenue_str", "N/A")
                    flags = fundies.get("flags", [])
                    result["flags"]           = " · ".join(f[0] for f in flags) if flags else ""
                else:
                    result["sector"] = "N/A"; result["industry"] = "N/A"; result["flags"] = ""
                scan_results.append(result)
            else:
                # scan_single_stock returned None — no data / insufficient bars
                scan_no_data.append(ticker)
        except Exception as e:
            err_str = str(e)
            # Classify: timeout vs other error
            is_timeout = any(kw in err_str.lower() for kw in
                             ("timeout", "timed out", "read timed", "connectionerror",
                              "connection reset", "remotedisconnected", "connection aborted"))
            scan_errors.append((ticker, err_str, is_timeout))
    progress_bar.empty()
    status_text.empty()

    # ── Timeout / no-data tickers — copy-ready for retry ──────────────────
    timeout_tickers = [t for t, _, is_to in scan_errors if is_to]
    other_errors    = [(t, e) for t, e, is_to in scan_errors if not is_to]
    all_failed      = timeout_tickers + scan_no_data

    if all_failed:
        failed_csv = ", ".join(all_failed)
        st.warning(
            f"**{len(all_failed)} ticker(s) returned no data** "
            f"({len(timeout_tickers)} timeout, {len(scan_no_data)} no data). "
            f"Copy below and re-run them individually:"
        )
        st.code(failed_csv, language=None)

    if other_errors:
        with st.expander(f"⚠️ {len(other_errors)} other error(s)"):
            for t, err in other_errors[:10]:
                st.code(f"{t}: {err}", language=None)
            if len(other_errors) > 10:
                st.caption(f"...and {len(other_errors)-10} more")

    if scan_results:
        import pandas as pd
        df_all = pd.DataFrame(scan_results)

        # Split: actionable (ENTER) vs filtered
        actionable = df_all[df_all["entry_status"] == "ENTER"].copy()
        filtered   = df_all[df_all["entry_status"] != "ENTER"].copy()

        # Format display columns — entry_status first, then grade, then signals
        display_cols = [
            "ticker", "price", "entry_status", "entry_grade", "entry_label",
            "expected_wr", "expected_avg",
            "weekly_bias", "daily_bias", "4h_bias", "mtf_signal", "mtf_action",
            "sector", "verdict", "confidence", "score", "best_setup",
            "candle", "vol_action", "vol_trend", "vol_ratio", "poc", "val", "vah",
            "persistence", "quote_type",
            "entry", "stop_loss", "target1", "target2", "t1_days", "risk_pct",
            "pe_ratio", "forward_pe", "peg_ratio", "valuation", "market_cap",
            "revenue_str", "revenue_growth", "earnings_growth",
            "profit_margin", "roe", "debt_to_equity", "beta",
            "dividend_yield", "short_pct",
            "analyst_target", "target_1y", "target_upside",
            "rec_key", "num_analysts",
            "week52_position", "pct_from_high",
            "flags",
        ]
        display_cols = [c for c in display_cols if c in df_all.columns]

        def _format_df(df):
            df = df.copy()
            for col in ["target_1y", "analyst_target"]:
                if col in df.columns:
                    df[col] = df[col].apply(lambda x: f"${x}" if pd.notnull(x) and x else "N/A")
            if "target_upside" in df.columns:
                df["target_upside"] = df["target_upside"].apply(lambda x: f"{x:+.1f}%" if pd.notnull(x) and x != "" else "")
            for c in ["pe_ratio", "forward_pe"]:
                if c in df.columns:
                    df[c] = df[c].apply(lambda x: f"{x:.1f}" if pd.notnull(x) and x else "N/A")
            if "peg_ratio" in df.columns:
                df["peg_ratio"] = df["peg_ratio"].apply(lambda x: f"{x:.2f}" if pd.notnull(x) and x else "N/A")
            for pct_col in ["revenue_growth", "earnings_growth", "profit_margin", "roe", "dividend_yield", "short_pct"]:
                if pct_col in df.columns:
                    df[pct_col] = df[pct_col].apply(lambda x: f"{x*100:.1f}%" if pd.notnull(x) and x != "" else "N/A")
            if "debt_to_equity" in df.columns:
                df["debt_to_equity"] = df["debt_to_equity"].apply(lambda x: f"{x:.0f}" if pd.notnull(x) and x != "" else "N/A")
            if "beta" in df.columns:
                df["beta"] = df["beta"].apply(lambda x: f"{x:.2f}" if pd.notnull(x) and x != "" else "N/A")
            if "week52_position" in df.columns:
                df["week52_position"] = df["week52_position"].apply(lambda x: f"{x:.0f}%" if pd.notnull(x) and x != "" else "N/A")
            if "pct_from_high" in df.columns:
                df["pct_from_high"] = df["pct_from_high"].apply(lambda x: f"{x:+.1f}%" if pd.notnull(x) and x != "" else "N/A")
            for vcol in ["poc", "val", "vah"]:
                if vcol in df.columns:
                    df[vcol] = df[vcol].apply(lambda x: f"${x:.2f}" if pd.notnull(x) and x else "")
            if "vol_ratio" in df.columns:
                df["vol_ratio"] = df["vol_ratio"].apply(lambda x: f"{x:.1f}x" if pd.notnull(x) and x else "")
            if "expected_wr" in df.columns:
                df["expected_wr"] = df["expected_wr"].apply(lambda x: f"{x:.0f}%" if pd.notnull(x) else "")
            if "expected_avg" in df.columns:
                df["expected_avg"] = df["expected_avg"].apply(lambda x: f"{x:+.2f}%" if pd.notnull(x) else "")
            if "risk_pct" in df.columns:
                df["risk_pct"] = df["risk_pct"].apply(lambda x: f"{x:.1f}%" if pd.notnull(x) and x else "")
            if "persistence" in df.columns:
                df["persistence"] = df["persistence"].apply(lambda x: f"{x:.0f}%" if pd.notnull(x) else "")
            return df

        col_rename = {
            "entry_status": "Status",
            "entry_grade":  "Grade",
            "entry_label":  "Entry Signal",
            "expected_wr":  "Exp WR%",
            "expected_avg": "Exp Avg P&L",
            "weekly_bias":  "Weekly",
            "daily_bias":   "Daily",
            "4h_bias":      "4H",
            "mtf_signal":   "Signal",
            "mtf_action":   "Action",
            "candle":       "Day Candle",
            "persistence":  "Trend Pers%",
            "quote_type":   "Type",
            "best_setup":   "Best Setup",
            "vol_action":   "Vol Bias", "vol_trend": "Vol Trend", "vol_ratio": "Vol Ratio",
            "poc": "POC", "val": "VAL", "vah": "VAH",
            "pe_ratio": "P/E", "forward_pe": "Fwd P/E", "peg_ratio": "PEG",
            "revenue_str": "Revenue", "revenue_growth": "Rev Growth",
            "earnings_growth": "EPS Growth", "profit_margin": "Margin",
            "roe": "ROE", "debt_to_equity": "D/E", "beta": "Beta",
            "dividend_yield": "Div Yield", "short_pct": "Short%",
            "analyst_target": "Analyst $", "target_1y": "1Y Target",
            "target_upside": "Upside", "rec_key": "Rating",
            "num_analysts": "# Analysts", "week52_position": "52W Pos",
            "pct_from_high": "vs 52W Hi", "market_cap": "Mkt Cap",
            "stop_loss": "Stop", "t1_days": "T1 (td)", "risk_pct": "Risk%", "flags": "Signals",
        }

        # ── Color styling for Weekly / Daily / 4H / Signal columns ───────
        _bias_cols = {"Weekly", "Daily", "4H"}
        def _style_bias_cols(df):
            """Return a Styler with green/red/grey on bias cols + signal col."""
            def _color_bias(val):
                if not isinstance(val, str):
                    return ""
                v = val.upper()
                if "BULLISH" in v:
                    return "background-color: #0a3d1f; color: #00e5a0; font-weight: 700"
                elif "BEARISH" in v:
                    return "background-color: #3d0a1a; color: #ff4d6a; font-weight: 700"
                return "background-color: #1a1d2e; color: #6b7099"
            def _color_signal(val):
                if not isinstance(val, str):
                    return ""
                v = val.upper()
                if "A+ LONG" in v or "STRONG LONG" in v or "LONG PULLBACK" in v:
                    return "background-color: #0a3d1f; color: #00e5a0; font-weight: 700"
                elif "A+ SHORT" in v or "STRONG SHORT" in v or "SHORT PULLBACK" in v:
                    return "background-color: #3d0a1a; color: #ff4d6a; font-weight: 700"
                elif "SHORT-TERM LONG" in v:
                    return "color: #00e5a0; font-weight: 600"
                elif "SHORT-TERM SHORT" in v:
                    return "color: #ff4d6a; font-weight: 600"
                elif "NO EDGE" in v or "NOISE" in v or "TOO EARLY" in v:
                    return "color: #6b7099"
                elif "WARNING" in v or "DEAD CAT" in v or "FAILING" in v:
                    return "color: #f0c040; font-weight: 600"
                return "color: #a78bfa"
            bias_present = [c for c in _bias_cols if c in df.columns]
            styler = df.style.applymap(_color_bias, subset=bias_present)
            if "Signal" in df.columns:
                styler = styler.applymap(_color_signal, subset=["Signal"])
            return styler

        # ── Summary banner ──────────────────────────────────────────────────
        enter_results  = [r for r in scan_results if r.get("entry_status") == "ENTER"]
        grade_s  = sum(1 for r in enter_results if r.get("entry_grade") == "S")
        grade_a  = sum(1 for r in enter_results if r.get("entry_grade") == "A")
        grade_b  = sum(1 for r in enter_results if r.get("entry_grade") in ("B","B-"))
        grade_c  = sum(1 for r in enter_results if r.get("entry_grade") == "C")
        bullish_count = sum(1 for r in enter_results if r.get("verdict") == "BULLISH")
        bearish_count = sum(1 for r in enter_results if r.get("verdict") == "BEARISH")
        high_conf     = sum(1 for r in enter_results if r.get("confidence") == "HIGH")
        sector_counts = {}
        for r in enter_results:
            s = r.get("sector","N/A"); sector_counts[s] = sector_counts.get(s,0)+1
        sector_summary = " · ".join(f"{s}: {c}" for s, c in sorted(sector_counts.items(), key=lambda x: -x[1])[:6])

        st.markdown(
            f'<div style="background:#0d0f17;border:1px solid #1a1d2e;padding:12px;border-radius:6px;margin-bottom:12px">'
            f'<span style="color:#6b7099;font-size:11px">Scanned <b style="color:#e8ecff">{total}</b> tickers · '
            f'<b style="color:#00e5a0">{bullish_count}</b> bullish · '
            f'<b style="color:#ff4d6a">{bearish_count}</b> bearish · '
            f'<b style="color:#4d9fff">{high_conf}</b> high confidence · '
            f'<b style="color:#e8ecff">{len(enter_results)}</b> actionable / {len(scan_results)} total</span><br>'
            f'<span style="color:#6b7099;font-size:11px">Entry grades (actionable): '
            f'<b style="color:#00e5a0">S: {grade_s}</b> · <b style="color:#00e5a0">A: {grade_a}</b> · '
            f'<b style="color:#4d9fff">B: {grade_b}</b> · <b style="color:#f0c040">C: {grade_c}</b></span><br>'
            f'<span style="color:#3a3d5c;font-size:10px">Sectors: {sector_summary}</span></div>',
            unsafe_allow_html=True,
        )

        # ── Split by MTF rank into separate tables ──────────────────────
        # Add mtf_rank to df_all for splitting
        if "mtf_rank" not in df_all.columns:
            df_all["mtf_rank"] = 5

        # Rank labels & icons
        _rank_meta = {
            1: ("🎯 Rank 1 — All Timeframes Aligned", "#00e5a0"),
            2: ("✅ Rank 2 — Two Aligned + Neutral", "#4d9fff"),
            3: ("⚠️ Rank 3 — Conflicting Signals", "#f0c040"),
            4: ("📋 Rank 4 — Weak / Mixed", "#a78bfa"),
            5: ("⬜ Rank 5 — No Edge", "#6b7099"),
        }

        # Tabs: Rank 1, Rank 2, All (ranked)
        r1_df = actionable[actionable["mtf_rank"] == 1] if not actionable.empty else pd.DataFrame()
        r2_df = actionable[actionable["mtf_rank"] == 2] if not actionable.empty else pd.DataFrame()
        rest_df = actionable[actionable["mtf_rank"] >= 3] if not actionable.empty else pd.DataFrame()

        view_tab1, view_tab2, view_tab3, view_tab4 = st.tabs([
            f"🎯 Rank 1 — Full Align ({len(r1_df)})",
            f"✅ Rank 2 — Two Aligned ({len(r2_df)})",
            f"📋 Rank 3+ — Rest ({len(rest_df)})",
            f"📊 All Tickers ({len(df_all)})",
        ])

        def _render_rank_table(df_subset, tab_container, rank_label, rank_color, empty_msg):
            with tab_container:
                if df_subset.empty:
                    st.info(empty_msg)
                    return
                st.markdown(
                    f'<div style="border-left:4px solid {rank_color};padding:4px 12px;margin-bottom:10px">'
                    f'<span style="color:{rank_color};font-weight:700;font-size:14px">{rank_label}</span></div>',
                    unsafe_allow_html=True,
                )
                cols_r = [c for c in display_cols if c in df_subset.columns]
                df_r = _format_df(df_subset)[cols_r].rename(columns=col_rename)
                df_r = df_r.sort_values(by="score", ascending=False, key=lambda s: s.abs()) if "score" in df_r.columns else df_r
                st.dataframe(_style_bias_cols(df_r), use_container_width=True, height=min(40*len(df_r)+38, 600))
                st.download_button(
                    f"📥 Download {rank_label} CSV", df_r.to_csv(index=False),
                    file_name=f"scan_rank_{rank_label[:6].strip()}_{date.today()}.csv",
                    mime="text/csv", use_container_width=True,
                    key=f"dl_{rank_label[:6]}")

        _render_rank_table(r1_df, view_tab1, *_rank_meta[1], "No Rank 1 setups (all 3 TFs aligned) found today.")
        _render_rank_table(r2_df, view_tab2, *_rank_meta[2], "No Rank 2 setups (2 TFs aligned) found today.")

        # Rank 3+ sorted by rank then score
        with view_tab3:
            if rest_df.empty:
                st.info("No additional actionable setups.")
            else:
                for rank_val in sorted(rest_df["mtf_rank"].unique()):
                    r_sub = rest_df[rest_df["mtf_rank"] == rank_val]
                    label, color = _rank_meta.get(rank_val, (f"Rank {rank_val}", "#6b7099"))
                    st.markdown(
                        f'<div style="border-left:4px solid {color};padding:4px 12px;margin:12px 0 6px">'
                        f'<span style="color:{color};font-weight:700;font-size:13px">{label} ({len(r_sub)})</span></div>',
                        unsafe_allow_html=True,
                    )
                    cols_r = [c for c in display_cols if c in r_sub.columns]
                    df_r = _format_df(r_sub)[cols_r].rename(columns=col_rename)
                    df_r = df_r.sort_values(by="score", ascending=False, key=lambda s: s.abs()) if "score" in df_r.columns else df_r
                    st.dataframe(_style_bias_cols(df_r), use_container_width=True, height=min(40*len(df_r)+38, 400))

        # All tickers tab (unchanged)
        with view_tab4:
            st.caption("All tickers including filtered ones. Status column shows why each was skipped.")
            cols_all = [c for c in display_cols if c in df_all.columns]
            df_show  = _format_df(df_all)[cols_all].rename(columns=col_rename)
            df_show = df_show.sort_values(
                by=["Status", col_rename.get("score","score")],
                ascending=[True, False],
                key=lambda col: col.map(lambda x: (0 if x=="ENTER" else 1) if col.name=="Status" else x)
                    if col.name == "Status" else col.abs() if col.name == col_rename.get("score","score") else col
            ) if "Status" in df_show.columns else df_show
            st.dataframe(_style_bias_cols(df_show), use_container_width=True, height=min(40*len(df_show)+38, 700))
            st.download_button("📥 Download Full CSV", df_show.to_csv(index=False),
                file_name=f"scan_all_{date.today()}.csv", mime="text/csv",
                use_container_width=True)

        # ── Sector Strength (from scan results) ────────────────────────────
        st.markdown("---")
        st.markdown("### 📊 Sector Strength — from this scan")
        sector_str = _sector_strength_from_scan(scan_results)
        if sector_str:
            # Bar chart
            sec_names  = [s["sector"] for s in sector_str]
            sec_scores = [s["avg_score"] for s in sector_str]
            bar_colors = ["#00e5a0" if v > 0 else "#ff4d6a" for v in sec_scores]

            bar_html = ""
            max_abs  = max(abs(v) for v in sec_scores) or 1
            for s in sector_str:
                sc    = s["avg_score"]
                pct   = abs(sc) / max_abs * 100
                col   = "#00e5a0" if sc > 0 else ("#ff4d6a" if sc < 0 else "#6b7099")
                bias_icon = "🟢" if s["bias"]=="BULLISH" else ("🔴" if s["bias"]=="BEARISH" else "⚪")
                bar_html += (
                    f'<div style="display:flex;align-items:center;gap:8px;margin-bottom:5px">'
                    f'<div style="width:130px;font-size:11px;color:#e8ecff;text-align:right;flex-shrink:0">'
                    f'{bias_icon} {s["sector"][:18]}</div>'
                    f'<div style="flex:1;background:#1a1d2e;border-radius:3px;height:16px;position:relative">'
                    f'<div style="width:{pct:.0f}%;background:{col};height:100%;border-radius:3px"></div>'
                    f'</div>'
                    f'<div style="width:80px;font-size:10px;color:{col};flex-shrink:0">'
                    f'avg {sc:+.2f} · {s["total"]}T</div>'
                    f'</div>'
                )
            st.markdown(f'<div style="padding:8px 0">{bar_html}</div>', unsafe_allow_html=True)

            # Top 3 strongest / weakest
            c1, c2 = st.columns(2)
            with c1:
                st.markdown("**Strongest sectors**")
                for s in sector_str[:3]:
                    st.markdown(f"🟢 **{s['sector']}** — avg score {s['avg_score']:+.2f}, {s['bull_pct']:.0f}% bullish")
            with c2:
                st.markdown("**Weakest sectors**")
                for s in reversed(sector_str[-3:]):
                    st.markdown(f"🔴 **{s['sector']}** — avg score {s['avg_score']:+.2f}, {s['bearish']}/{s['total']} bearish")

        # Save scan results to session state so Macro tab can read them
        st.session_state["_last_scan_results"] = scan_results
    else:
        st.info("No scan results returned. Check that your API keys are valid and tickers are correct.")

# ── Backtest Estimator Signals ──────────────────────────
if bt_run:
  with tab_backtest:
    if missing_creds:
        st.error("Enter API credentials in the sidebar first.")
    elif not bt_ticker:
        st.warning("Enter a ticker to backtest.")
    else:
        with st.spinner(f"Backtesting {bt_ticker} — {bt_years}yr, {'ATR target days' if bt_use_dynamic else f'hold {bt_hold}d'} ..."):
            bt_end = date.today()
            bt_start = bt_end - timedelta(days=bt_years * 365)
            try:
                if data_source == "Alpaca":
                    bt_daily = get_daily_bars_alpaca(bt_ticker, str(bt_start), str(bt_end), api_key, api_secret)
                else:
                    bt_daily = get_daily_bars(bt_ticker, str(bt_start), str(bt_end), api_key)

                if bt_daily is None or bt_daily.empty or len(bt_daily) < 80:
                    st.warning(f"Not enough data for {bt_ticker} ({len(bt_daily) if bt_daily is not None else 0} bars).")
                else:
                    # ── Instrument suitability check ──────────────────────────
                    supported, reason = _is_instrument_supported(bt_ticker, bt_daily)
                    if not supported:
                        st.warning(
                            f"⚠️ **{bt_ticker} is not supported by this model.**\n\n"
                            f"{reason}\n\n"
                            f"The backtest will run but results are expected to be unreliable. "
                            f"Trend-following signals have no demonstrated edge on this instrument."
                        )

                    bt_results = backtest_estimator(
                        bt_daily, use_fib=use_fib, fib_tol=fib_tol,
                        hold_days=bt_hold, filter_best_only=bt_best_only,
                        use_dynamic_hold=bt_use_dynamic,
                        ticker=bt_ticker,
                    )
                    if bt_results.empty:
                        st.info("No signals generated during the backtest period.")
                    else:
                        # Stats
                        n_trades = len(bt_results)
                        wins = bt_results[bt_results["win"]]
                        losses = bt_results[~bt_results["win"]]
                        win_rate = len(wins) / n_trades * 100
                        avg_pnl = bt_results["pnl_pct"].mean()
                        total_pnl = bt_results["pnl_pct"].sum()
                        avg_win = wins["pnl_pct"].mean() if len(wins) else 0
                        avg_loss = losses["pnl_pct"].mean() if len(losses) else 0
                        pf = abs(len(wins) * avg_win / (len(losses) * avg_loss)) if len(losses) and avg_loss else None
                        best_only_ct = (bt_results["best_setup"] == "Y").sum()

                        # Equity curve
                        equity = [100.0]
                        for p in bt_results["pnl_pct"].values:
                            equity.append(round(equity[-1] * (1 + p / 100), 2))
                        max_dd = 0
                        peak = 100.0
                        for e in equity:
                            if e > peak:
                                peak = e
                            dd = (peak - e) / peak * 100
                            if dd > max_dd:
                                max_dd = dd

                        # Summary metrics
                        m1, m2, m3, m4 = st.columns(4)
                        m1.metric("Trades", n_trades)
                        m2.metric("Win Rate", f"{win_rate:.1f}%")
                        m3.metric("Total P&L", f"{total_pnl:+.1f}%")
                        m4.metric("Max Drawdown", f"{max_dd:.1f}%")

                        m5, m6, m7, m8 = st.columns(4)
                        m5.metric("Avg Trade", f"{avg_pnl:+.2f}%")
                        m6.metric("Avg Win", f"{avg_win:+.2f}%")
                        m7.metric("Avg Loss", f"{avg_loss:+.2f}%")
                        m8.metric("Profit Factor", f"{pf:.2f}" if pf and pf != float("inf") else "∞")

                        if best_only_ct > 0 and not bt_best_only:
                            best_df = bt_results[bt_results["best_setup"] == "Y"]
                            best_wr = len(best_df[best_df["win"]]) / len(best_df) * 100 if len(best_df) else 0
                            st.info(f"💡 Best Setup trades: {best_only_ct}/{n_trades} — Win Rate: {best_wr:.0f}%")

                        # ── Backtest-derived grade reference ──────────────────
                        grade_rows = []
                        for (sc, cf), (gr, lbl, exp_wr, exp_avg, col) in ENTRY_GRADE_TABLE.items():
                            sub = bt_results[
                                (bt_results["score"].abs() == sc) &
                                (bt_results["confidence"] == cf)
                            ]
                            if len(sub) > 0:
                                actual_wr  = sub["win"].mean() * 100
                                actual_avg = sub["pnl_pct"].mean()
                                grade_rows.append({
                                    "Grade": gr,
                                    "Score": f"±{sc}",
                                    "Conf": cf,
                                    "Trades": len(sub),
                                    "Actual WR%": f"{actual_wr:.0f}%",
                                    "Actual Avg": f"{actual_avg:+.2f}%",
                                    "Model Exp WR%": f"{exp_wr}%",
                                    "Model Exp Avg": f"{exp_avg:+.2f}%",
                                })
                        if grade_rows:
                            st.markdown(
                                '<div style="font-size:11px;color:#6b7099;margin:10px 0 4px">📊 '
                                '<b style="color:#e8ecff">Entry grade breakdown</b> — '
                                'Actual results vs model expectations from SPY backtest</div>',
                                unsafe_allow_html=True,
                            )
                            st.dataframe(
                                pd.DataFrame(grade_rows),
                                use_container_width=True,
                                hide_index=True,
                            )

                        # Equity curve chart
                        eq_fig = go.Figure()
                        eq_fig.add_trace(go.Scatter(
                            y=equity, mode="lines",
                            line=dict(color="#00e5a0" if equity[-1] >= 100 else "#ff4d6a", width=2),
                            fill="tozeroy",
                            fillcolor="rgba(0,229,160,0.08)" if equity[-1] >= 100 else "rgba(255,77,106,0.08)",
                        ))
                        eq_fig.update_layout(
                            title=f"{bt_ticker} Estimator Backtest — Equity Curve",
                            yaxis_title="Equity ($100 start)",
                            height=300,
                            margin=dict(l=40, r=20, t=40, b=30),
                            template="plotly_dark",
                            paper_bgcolor="#0a0b14",
                            plot_bgcolor="#0a0b14",
                        )
                        st.plotly_chart(eq_fig, use_container_width=True)

                        # Trades table
                        bt_display = bt_results[[
                            "entry_date", "exit_date", "direction", "verdict", "confidence",
                            "best_setup", "vol_bias", "vol_trend", "fib_bias", "score",
                            "entry", "stop", "target1", "exit", "exit_reason", "pnl_pct",
                        ]].copy()
                        bt_display.columns = [
                            "Entry Date", "Exit Date", "Dir", "Verdict", "Conf",
                            "Best", "Vol Bias", "Vol Trend", "Fib", "Score",
                            "Entry $", "Stop $", "Target $", "Exit $", "Reason", "P&L %",
                        ]
                        st.dataframe(bt_display, use_container_width=True,
                                     height=min(40 * len(bt_display) + 38, 500))

                        # CSV download
                        bt_csv = bt_display.to_csv(index=False)
                        st.download_button(
                            "📥 Download Backtest CSV", bt_csv,
                            file_name=f"estimator_backtest_{bt_ticker}_{date.today()}.csv",
                            mime="text/csv", use_container_width=True,
                        )
            except Exception as exc:
                st.error(f"Backtest error: {exc}")

# # ── Scan for bullish stocks (COMMENTED OUT) ──────────────────────────
# if scan_btn:
#   with tab_scanner:
#     ... (Technical Scanner commented out)

# ── Date Lookup handler ──────────────────────────────
if dl_run:
  with tab_backtest:
    if missing_creds:
        st.error("Enter API credentials in the sidebar first.")
    elif not dl_ticker:
        st.warning("Enter a ticker to look up.")
    else:
        with st.spinner(f"Looking up {dl_ticker} on {dl_date}..."):
            try:
                # Fetch enough data before the lookup date for indicators
                lookup_start = dl_date - timedelta(days=400)
                if data_source == "Alpaca":
                    dl_daily = get_daily_bars_alpaca(dl_ticker, str(lookup_start), str(dl_date), api_key, api_secret)
                else:
                    dl_daily = get_daily_bars(dl_ticker, str(lookup_start), str(dl_date), api_key)

                if dl_daily is None or dl_daily.empty or len(dl_daily) < 60:
                    st.warning(f"Not enough data for {dl_ticker} up to {dl_date} ({len(dl_daily) if dl_daily is not None else 0} bars).")
                else:
                    # Find the bar at or just before the requested date
                    dl_date_str = str(dl_date)
                    valid_dates = [d for d in dl_daily.index if str(d)[:10] <= dl_date_str]
                    if not valid_dates:
                        st.warning(f"No trading data found on or before {dl_date} for {dl_ticker}.")
                    else:
                        target_idx = len([d for d in dl_daily.index if d <= valid_dates[-1]]) - 1
                        actual_date = str(dl_daily.index[target_idx])[:10]
                        sig = _estimator_signal_at(dl_daily, target_idx, use_fib=use_fib, fib_tol=fib_tol, ticker=dl_ticker)

                        if sig is None:
                            st.info(f"No signal generated for {dl_ticker} on {actual_date} (neutral or insufficient data).")
                        else:
                            st.markdown(f"### 📅 {dl_ticker} — Signal on {actual_date}")
                            grade_info = _get_entry_grade(sig["score"], sig["confidence"])
                            dl_row = {
                                "Date": actual_date,
                                "Ticker": dl_ticker,
                                "Grade": grade_info["entry_grade"],
                                "Entry Signal": grade_info["entry_label"],
                                "Exp WR%": f"{grade_info['expected_wr']:.0f}%",
                                "Exp Avg P&L": f"{grade_info['expected_avg']:+.2f}%",
                                "Price": f"${sig['entry']:.2f}",
                                "Verdict": sig["verdict"],
                                "Confidence": sig["confidence"],
                                "Direction": sig["direction"],
                                "Score": sig["score"],
                                "Fib Bias": sig["fib_bias"],
                                "Vol Bias": sig["vol_bias"],
                                "Vol Trend": sig["vol_trend"],
                                "Best Setup": "Y" if sig["best_setup"] else "N",
                                "Entry": f"${sig['entry']:.2f}",
                                "Stop": f"${sig['stop']:.2f}",
                                "Target 1": f"${sig['target1']:.2f}",
                                "Signals": sig["signals"],
                            }
                            dl_df = pd.DataFrame([dl_row])
                            st.dataframe(dl_df, use_container_width=True, hide_index=True)

                            # CSV download
                            dl_csv = dl_df.to_csv(index=False)
                            st.download_button(
                                "📥 Download Signal CSV", dl_csv,
                                file_name=f"signal_{dl_ticker}_{actual_date}.csv",
                                mime="text/csv", use_container_width=True,
                            )
            except Exception as exc:
                st.error(f"Lookup error: {exc}")

# ── Check Open Prices ──────────────────────────────────────
if check_open_run:
  with tab_plan:
    saved_plan = st.session_state.get("plan_data", [])
    if not saved_plan:
        # Auto-generate plan for previous trading day if date is today
        if plan_date == date.today() and not missing_creds:
            # Find previous trading day (skip weekends)
            _prev = plan_date - timedelta(days=1)
            while _prev.weekday() >= 5:  # skip Sat/Sun
                _prev -= timedelta(days=1)
            st.info(f"⏳ No plan found — auto-generating plan for **{_prev.strftime('%A, %B %d')}**...")
            _auto_tickers = [t.strip().upper() for t in plan_tickers_raw.strip().split(",") if t.strip()]
            _auto_plan_rows = []
            _auto_progress = st.progress(0)
            for _ai, _aticker in enumerate(_auto_tickers):
                _auto_progress.progress((_ai + 1) / len(_auto_tickers))
                try:
                    p_end = _prev
                    p_start = p_end - timedelta(days=400)
                    if data_source == "Alpaca":
                        p_daily = get_daily_bars_alpaca(_aticker, str(p_start), str(p_end), api_key, api_secret)
                    else:
                        p_daily = get_daily_bars(_aticker, str(p_start), str(p_end), api_key)
                    if p_daily is None or p_daily.empty or len(p_daily) < 60:
                        continue
                    daily_close = float(p_daily["close"].iloc[-1])
                    daily_open  = float(p_daily["open"].iloc[-1])
                    atr_14 = float((p_daily["high"] - p_daily["low"]).rolling(14).mean().iloc[-1])
                    if daily_close > daily_open:
                        direction = "LONG"
                    elif daily_close < daily_open:
                        direction = "SHORT"
                    else:
                        prev5 = p_daily["close"].iloc[-6:-1]
                        direction = "LONG" if daily_close >= float(prev5.iloc[0]) else "SHORT"
                    entry = round(daily_close, 2)
                    recent_low  = float(p_daily["low"].iloc[-10:].min())
                    recent_high = float(p_daily["high"].iloc[-10:].max())
                    if direction == "LONG":
                        stop_px = round(recent_low  - atr_14 * 0.3, 2)
                    else:
                        stop_px = round(recent_high + atr_14 * 0.3, 2)
                    sig_full = _estimator_signal_at(p_daily, len(p_daily) - 1,
                                                    use_fib=use_fib, fib_tol=fib_tol, ticker="")
                    if sig_full is not None:
                        verdict    = sig_full["verdict"]
                        confidence = sig_full["confidence"]
                        score      = sig_full["score"]
                        fib_bias   = sig_full["fib_bias"]
                        vol_bias   = sig_full["vol_bias"]
                        vol_trend  = sig_full["vol_trend"]
                        signals    = sig_full["signals"]
                    else:
                        verdict    = "LEAN BULLISH" if direction == "LONG" else "LEAN BEARISH"
                        confidence = "LOW"
                        score      = 2 if direction == "LONG" else -2
                        fib_bias   = "N/A"
                        vol_bias   = "N/A"
                        vol_trend  = "N/A"
                        signals    = "Day:BULL" if direction == "LONG" else "Day:BEAR"
                    atr_1d = atr_14
                    intra_stop_dist = round(atr_1d * 0.3, 2)
                    intra_t1_dist = round(atr_1d * 0.5, 2)
                    intra_t2_dist = round(atr_1d * 0.8, 2)
                    if direction == "LONG":
                        intra_stop = round(entry - intra_stop_dist, 2)
                        intra_t1 = round(entry + intra_t1_dist, 2)
                        intra_t2 = round(entry + intra_t2_dist, 2)
                    else:
                        intra_stop = round(entry + intra_stop_dist, 2)
                        intra_t1 = round(entry - intra_t1_dist, 2)
                        intra_t2 = round(entry - intra_t2_dist, 2)
                    LISTED_INCS = [0.5, 1, 2, 2.5, 5, 10]
                    spread_target = atr_14 * 0.40
                    strike_inc = next((s for s in LISTED_INCS if s >= spread_target), LISTED_INCS[-1])
                    min_inc = 0.5 if entry < 20 else (1 if entry < 50 else 2.5)
                    max_inc = next((s for s in LISTED_INCS if s >= atr_14), LISTED_INCS[-1])
                    strike_inc = max(strike_inc, min_inc)
                    strike_inc = min(strike_inc, max_inc)
                    atm_strike = round(round(entry / strike_inc) * strike_inc, 2)
                    opt_type = "CALL" if direction == "LONG" else "PUT"
                    def _next_trading_day(d, skip=1):
                        result = d
                        added = 0
                        while added < skip:
                            result += timedelta(days=1)
                            if result.weekday() < 5:
                                added += 1
                        return result
                    next_trade   = _next_trading_day(p_end, 1)
                    trade_plus2  = _next_trading_day(p_end, 3)
                    expiry_0dte  = next_trade.strftime("%m/%d")
                    expiry_2dte  = trade_plus2.strftime("%m/%d")
                    gap_threshold = round(entry * 0.005, 2)
                    if direction == "LONG":
                        open_above = f"Enter CALL at ~${entry:.2f} — stop ${intra_stop:.2f}, T1 ${intra_t1:.2f}, T2 ${intra_t2:.2f}"
                        open_between = f"Better entry between ${intra_stop:.2f}-${entry:.2f} — stop ${intra_stop:.2f}, T1 ${intra_t1:.2f}, T2 ${intra_t2:.2f}"
                        open_below_stop = f"Opens below ${intra_stop:.2f} — SKIP CALL, consider PUT"
                        big_gap = f"Gap up >${gap_threshold:.2f} above ${entry:.2f} — wait for pullback near ${entry:.2f}"
                    else:
                        open_above = f"Opens above ${intra_stop:.2f} — SKIP PUT, consider CALL"
                        open_between = f"Enter PUT at ~${entry:.2f} — stop ${intra_stop:.2f}, T1 ${intra_t1:.2f}, T2 ${intra_t2:.2f}"
                        open_below_stop = f"Better entry between ${entry:.2f}-${intra_stop:.2f} — stop ${intra_stop:.2f}, T1 ${intra_t1:.2f}, T2 ${intra_t2:.2f}"
                        big_gap = f"Gap down >${gap_threshold:.2f} below ${entry:.2f} — wait for bounce near ${entry:.2f}"
                    _auto_plan_rows.append({
                        "Ticker": _aticker, "Direction": direction, "Option": opt_type,
                        "Verdict": verdict, "Confidence": confidence,
                        "Close": round(entry, 2), "ATR": round(atr_1d, 2),
                        "Intra Stop": round(intra_stop, 2), "Intra T1": round(intra_t1, 2), "Intra T2": round(intra_t2, 2),
                        "ATM Strike": round(atm_strike, 2),
                        "0DTE Exp": expiry_0dte, "2-3DTE Exp": expiry_2dte,
                        "If opens near entry": open_above if direction == "LONG" else open_between,
                        "If opens between entry & stop": open_between if direction == "LONG" else open_above,
                        "If opens past stop": open_below_stop, "If big gap": big_gap,
                    })
                except:
                    continue
            _auto_progress.empty()
            if _auto_plan_rows:
                st.session_state["plan_data"] = _auto_plan_rows
                saved_plan = _auto_plan_rows
                st.success(f"✅ Auto-generated plan for {_prev.strftime('%m/%d')} with {len(_auto_plan_rows)} ticker(s)")
            else:
                st.warning("Could not auto-generate plan. Try generating manually.")
        else:
            st.warning("Generate a plan first, then check open prices.")
    if saved_plan and not YFINANCE_AVAILABLE:
        st.error("yfinance is required for live price checks.")
    elif saved_plan:
        check_date = plan_date
        is_live    = (check_date == date.today())
        actual_trade_date = None
        open_status = st.empty()
        fetch_errors = []
        shown = 0
        table_rows = []  # Collect all scenario results for table

        for i, r in enumerate(saved_plan):
            if not isinstance(r, dict):
                continue
            ticker = r.get("Ticker") or r.get("ticker", "UNKNOWN")
            open_status.text(f"Fetching {ticker}... ({i+1}/{len(saved_plan)})")
            try:
                # Pull every field defensively — handles stale plan rows
                entry      = r.get("Close") or r.get("entry")
                intra_stop = r.get("Intra Stop")
                intra_t1   = r.get("Intra T1")
                intra_t2   = r.get("Intra T2")
                atm_strike = r.get("ATM Strike")
                atr_val    = r.get("ATR")
                direction  = r.get("Direction")
                opt_type   = r.get("Option")
                confidence = r.get("Confidence", "N/A")
                exp_0dte   = r.get("0DTE Exp", "")
                exp_2dte   = r.get("2-3DTE Exp", "")
                txt_gap    = r.get("If big gap", "N/A")
                txt_near   = r.get("If opens near entry", "N/A")
                txt_btwn   = r.get("If opens between entry & stop", "N/A")
                txt_past   = r.get("If opens past stop", "N/A")

                missing = [k for k, v in {
                    "Close": entry, "Intra Stop": intra_stop, "Intra T1": intra_t1,
                    "ATM Strike": atm_strike, "ATR": atr_val, "Direction": direction,
                }.items() if v is None]
                if missing:
                    fetch_errors.append(f"{ticker}: missing {missing} — regenerate plan")
                    continue

                entry=float(entry); intra_stop=float(intra_stop); intra_t1=float(intra_t1)
                intra_t2=float(intra_t2) if intra_t2 is not None else intra_t1
                atm_strike=float(atm_strike); atr_val=float(atr_val)

                # Fetch live price via yfinance
                tk = yf.Ticker(ticker)
                today_hist = (tk.history(period="1d") if is_live else
                              tk.history(start=str(check_date + timedelta(days=1)),
                                         end=str(check_date + timedelta(days=7))))

                if today_hist is None or (hasattr(today_hist, "empty") and today_hist.empty):
                    fetch_errors.append(f"{ticker}: no data — market may not be open yet")
                    continue

                # ── Robust price extraction — handles all yfinance column formats ──
                # yfinance can return: plain columns, MultiIndex (field, ticker),
                # or MultiIndex (ticker, field) depending on version and ticker alias.
                def _extract_price(df, field, row_idx):
                    """Extract a single price value from a yfinance DataFrame robustly."""
                    cols = df.columns
                    # 1. Plain columns: ["Open", "High", ...]
                    if field in cols:
                        val = df[field].iloc[row_idx]
                        if val is not None and str(val) != "nan":
                            return float(val)
                    # 2. MultiIndex — try (field, *) pattern
                    if hasattr(cols, "levels"):
                        for col in cols:
                            if isinstance(col, tuple) and col[0] == field:
                                val = df[col].iloc[row_idx]
                                if val is not None and str(val) != "nan":
                                    return float(val)
                        # 3. MultiIndex — try (*, field) pattern
                        for col in cols:
                            if isinstance(col, tuple) and col[-1] == field:
                                val = df[col].iloc[row_idx]
                                if val is not None and str(val) != "nan":
                                    return float(val)
                    # 4. Last resort — positional (Open=col0, Close=col3)
                    pos = {"Open": 0, "Close": 3}
                    if field in pos and len(df.columns) > pos[field]:
                        val = df.iloc[row_idx, pos[field]]
                        if val is not None:
                            return float(val)
                    raise ValueError(f"Cannot extract {field} from DataFrame columns: {list(cols)[:6]}")

                open_price    = _extract_price(today_hist, "Open",  0)
                current_price = _extract_price(today_hist, "Close", -1)

                # Fetch option data for the strike
                opt_prev_open = opt_prev_high = opt_prev_low = opt_prev_close = opt_curr_open = None
                try:
                    # Try yfinance first
                    stock = yf.Ticker(ticker)
                    expirations = stock.options
                    if expirations:
                        exp_date = expirations[0]
                        opt_chain = stock.option_chain(exp_date)
                        calls = opt_chain.calls if dopt == "CALL" else opt_chain.puts
                        calls = calls.sort_values(by='strike')
                        closest_strike = calls.iloc[(calls['strike'] - atm_strike).abs().argsort()[0]]
                        if not closest_strike.empty:
                            opt_curr_open = closest_strike.get('lastPrice', None)
                            opt_prev_open = opt_curr_open
                            opt_prev_close = opt_curr_open
                except:
                    # Fallback to Alpaca
                    try:
                        if api_key and api_secret:
                            headers = {
                                'APCA-API-KEY-ID': api_key,
                                'APCA-API-SECRET-KEY': api_secret,
                            }
                            base_url = "https://api.alpaca.markets"
                            resp = requests.get(
                                f"{base_url}/v1beta1/options/snapshots/{ticker}",
                                headers=headers,
                                timeout=10
                            )
                            if resp.status_code == 200:
                                data = resp.json()
                                snapshots = data.get('snapshots', [])
                                for snap in snapshots:
                                    contract = snap.get('option_details', {})
                                    strike = contract.get('strike_price')
                                    if strike and abs(strike - atm_strike) < 0.5:
                                        opt_curr_open = snap.get('latest_quote', {}).get('last_quote', {}).get('ask')
                                        if opt_curr_open:
                                            opt_prev_open = opt_curr_open
                                            opt_prev_close = opt_curr_open
                                        break
                    except:
                        pass

                if actual_trade_date is None:
                    actual_trade_date = str(today_hist.index[0])[:10]
                    date_label = "Live" if is_live else actual_trade_date

                gap_thr  = round(entry * 0.005, 2)
                move_raw = open_price - entry
                move_pct = move_raw / entry * 100

                if direction == "LONG":
                    if open_price - entry > gap_thr:
                        sc,lb,col,txt = "big_gap",   "⚠️ BIG GAP UP",               "#f5c842", txt_gap
                    elif open_price >= entry:
                        sc,lb,col,txt = "near_entry","✅ OPENS NEAR ENTRY",               "#00e5a0", txt_near
                    elif open_price > intra_stop:
                        sc,lb,col,txt = "between",   "⚡ OPENS BETWEEN ENTRY & STOP",     "#4d9fff", txt_btwn
                    else:
                        sc,lb,col,txt = "past_stop", "❌ OPENS PAST STOP",                "#ff4d6a", txt_past
                else:
                    if entry - open_price > gap_thr:
                        sc,lb,col,txt = "big_gap",   "⚠️ BIG GAP DOWN",             "#f5c842", txt_gap
                    elif open_price <= entry:
                        sc,lb,col,txt = "near_entry","✅ OPENS NEAR ENTRY",               "#00e5a0", txt_near
                    elif open_price < intra_stop:
                        sc,lb,col,txt = "between",   "⚡ OPENS BETWEEN ENTRY & STOP",     "#4d9fff", txt_btwn
                    else:
                        sc,lb,col,txt = "past_stop", "❌ OPENS PAST STOP",                "#ff4d6a", txt_past

                dd=direction; dopt=opt_type or "CALL"
                ds=intra_stop; dt1=intra_t1; dt2=intra_t2; da=atm_strike

                if sc == "past_stop":
                    fd=round(atr_val*0.3,2); f1=round(atr_val*0.5,2); f2=round(atr_val*0.8,2)
                    if direction == "LONG":
                        dd="SHORT"; dopt="PUT"
                        ds=round(open_price+fd,2); dt1=round(open_price-f1,2); dt2=round(open_price-f2,2)
                    else:
                        dd="LONG";  dopt="CALL"
                        ds=round(open_price-fd,2); dt1=round(open_price+f1,2); dt2=round(open_price+f2,2)
                    si = 5 if open_price>=200 else (2.5 if open_price>=50 else (1 if open_price>=20 else 0.5))
                    da  = round(round(open_price/si)*si, 2)
                    txt = f"Flipped to {dopt} at ~${open_price:.2f} — stop ${ds:.2f}, T1 ${dt1:.2f}, T2 ${dt2:.2f}"

                dc  = "#00e5a0" if dd == "LONG" else "#ff4d6a"
                ico = "📞" if dopt == "CALL" else "📉"
                shown += 1

                # ── Per-ticker detail cards (inside collapsed expander) ──
                if '_check_open_expander' not in dir():
                    _check_open_expander = st.expander(f"☀️ Morning Open — Which Scenario? ({date_label})", expanded=False)
                with _check_open_expander:
                    st.markdown(
                        f'<div style="background:#0d0f17;border:1px solid #1a1d2e;border-left:4px solid {col};'
                        f'padding:14px 18px;border-radius:4px;margin-bottom:8px">'
                        f'<div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:8px">'
                        f'<span style="font-size:15px;font-weight:900;color:#e8ecff">{ico} {ticker} — {dopt}'
                        f'{"  🔄 FLIPPED" if sc=="past_stop" else ""}</span>'
                        f'<span style="color:{dc};font-weight:700;font-size:12px">{dd} · {confidence}</span>'
                        f'</div>'
                        f'<div style="display:flex;gap:16px;font-size:12px;margin-bottom:8px;flex-wrap:wrap">'
                        f'<span style="color:#6b7099">Prev Close: <b style="color:#e8ecff">${entry:.2f}</b></span>'
                        f'<span style="color:#6b7099">Open: <b style="color:#f5c842">${open_price:.2f}</b></span>'
                        f'<span style="color:#6b7099">Current: <b style="color:#a78bfa">${current_price:.2f}</b></span>'
                        f'<span style="color:#6b7099">Move: <b style="color:{"#00e5a0" if move_raw>=0 else "#ff4d6a"}>'
                        f'{"+" if move_raw>=0 else ""}${move_raw:.2f} ({move_pct:+.2f}%)</b></span>'
                        f'</div>'
                        f'<div style="background:{col}15;border:1px solid {col}40;'
                        f'border-radius:6px;padding:10px 14px;margin-bottom:6px">'
                        f'<div style="font-size:13px;font-weight:700;color:{col};margin-bottom:4px">{lb}</div>'
                        f'<div style="font-size:11px;color:#e8ecff">{txt}</div>'
                        f'</div>',
                        unsafe_allow_html=True,
                    )

                # ── Spread suggestion ─────────────────────────────────────
                _sinc   = 5 if da >= 200 else (2.5 if da >= 50 else (1 if da >= 20 else 0.5))
                _raw    = (da - atr_val * 2) if dopt == "PUT" else (da + atr_val * 2)
                _leg2   = round(round(_raw / _sinc) * _sinc, 2)
                _spread = f"{da:.0f}–{_leg2:.0f}"

                with _check_open_expander:
                    st.markdown(
                        f'<div style="font-size:10px;color:#6b7099;padding:4px 18px 0">'
                        f'ATR: <b style="color:#a78bfa">${atr_val:.2f}</b> &nbsp;·&nbsp; '
                        f'Stop: ${ds:.2f} &nbsp;·&nbsp; T1: ${dt1:.2f} &nbsp;·&nbsp; T2: ${dt2:.2f} &nbsp;·&nbsp; '
                        f'ATM: <b style="color:{dc}">${da:.0f} {dopt}</b> &nbsp;·&nbsp; Exp: {exp_0dte} / {exp_2dte}</div>'
                        f'<div style="font-size:11px;font-weight:600;color:#4d9fff;padding:3px 18px 12px">'
                        f'{ticker} &nbsp; {dopt} SPREAD &nbsp; {_spread} &nbsp; Exp: {exp_0dte} / {exp_2dte}'
                        f'</div>',
                        unsafe_allow_html=True,
                    )

                # Calculate RR ratios
                def _calc_table_rr(entry_val, stop_val, target_val, direction_val):
                    """Calculate risk-reward ratio."""
                    try:
                        if direction_val == "LONG":
                            risk = entry_val - stop_val
                            reward = target_val - entry_val
                        else:
                            risk = stop_val - entry_val
                            reward = entry_val - target_val
                        return round(reward / risk, 2) if risk != 0 else 0
                    except:
                        return 0
                
                rr_t1_calc = _calc_table_rr(entry, ds, dt1, dd)
                rr_t2_calc = _calc_table_rr(entry, ds, dt2, dd)
                best_rr_calc = max(rr_t1_calc, rr_t2_calc)
                
                # Append row to table
                table_rows.append({
                    "Ticker": ticker,
                    "Direction": dd,
                    "Option": dopt,
                    "Stop": f"${ds:.2f}",
                    "ATM": f"${da:.0f}",
                    "Spread": _spread,
                    "Exp 0DTE": exp_0dte,
                    "Exp 2-3DTE": exp_2dte,
                    "Confidence": confidence,
                    "ATR": f"${atr_val:.2f}",
                    "Prev Close": f"${entry:.2f}",
                    "Prev Opt O": f"${opt_prev_open:.2f}" if opt_prev_open else "N/A",
                    "Prev Opt H": f"${opt_prev_high:.2f}" if opt_prev_high else "N/A",
                    "Prev Opt L": f"${opt_prev_low:.2f}" if opt_prev_low else "N/A",
                    "Prev Opt C": f"${opt_prev_close:.2f}" if opt_prev_close else "N/A",
                    "Open": f"${open_price:.2f}",
                    "Opt Open": f"${opt_curr_open:.2f}" if opt_curr_open else "N/A",
                    "T1": f"${dt1:.2f}",
                    "T2": f"${dt2:.2f}",
                    "RR(T1)": f"{rr_t1_calc:.2f}x",
                    "RR(T2)": f"{rr_t2_calc:.2f}x",
                    "Best RR": f"{best_rr_calc:.2f}x",
                    "Current": f"${current_price:.2f}",
                    "Move": f"${move_raw:.2f} ({move_pct:+.2f}%)",
                    "Scenario": lb,
                    "Notes": txt[:50] + "..." if len(txt) > 50 else txt,
                    "_scenario_id": sc,
                    "_best_rr_sort_main": best_rr_calc,
                })
            except Exception as exc:
                fetch_errors.append(f"{ticker}: {type(exc).__name__}: {exc}")

        open_status.empty()
        if shown == 0 and not fetch_errors:
            st.info("No data returned — market may not be open yet, or regenerate plan.")
        if fetch_errors:
            with st.expander(f"⚠️ {len(fetch_errors)} issue(s)", expanded=True):
                for e in fetch_errors:
                    st.caption(e)

        # Persist table_rows so they survive st.rerun() after track-button clicks
        if table_rows:
            st.session_state["open_check_table_rows"] = table_rows

# ── Display persisted scenario tables & tracker (survives rerun) ──
if st.session_state.get("open_check_table_rows"):
  with tab_plan:
    table_rows = st.session_state["open_check_table_rows"]
    
    # ── Ensure all rows have RR columns (handles old session state without RR) ──
    def _ensure_rr(rows):
        """Inject RR(T1), RR(T2), Best RR into rows if missing, computed from price columns."""
        def _p(v):
            try: return float(str(v).replace("$","").replace(",","").replace("x",""))
            except: return None
        
        for r in rows:
            if "RR(T1)" not in r or "RR(T2)" not in r:
                entry = _p(r.get("Prev Close") or r.get("Open") or r.get("Entry")) or 0
                stop  = _p(r.get("Stop")) or 0
                t1    = _p(r.get("T1")) or 0
                t2    = _p(r.get("T2")) or 0
                dirn  = r.get("Direction", "LONG")
                
                def _rr(e, s, t, d):
                    try:
                        risk = (e - s) if d == "LONG" else (s - e)
                        rew  = (t - e) if d == "LONG" else (e - t)
                        return round(rew / risk, 2) if risk > 0 else 0
                    except: return 0
                
                rr1 = _rr(entry, stop, t1, dirn)
                rr2 = _rr(entry, stop, t2, dirn)
                r["RR(T1)"]  = f"{rr1:.2f}x"
                r["RR(T2)"]  = f"{rr2:.2f}x"
                r["Best RR"] = f"{max(rr1, rr2):.2f}x"
                r["_best_rr_sort_main"] = max(rr1, rr2)
        return rows
    
    table_rows = _ensure_rr(table_rows)
    
    # ── Sort all rows by Best RR (highest first) ──
    table_rows = sorted(table_rows, key=lambda x: x.get("_best_rr_sort_main", 0), reverse=True)
    
    st.markdown("---")
    conf_priority = {"HIGH": 0, "MEDIUM": 1, "LOW": 2}
    near_entry_rows = [r for r in table_rows if r.get("Scenario", "").strip().startswith("✅ OPENS NEAR ENTRY")]

    # Check if we should enable auto-tracking (8:30 AM CST or later)
    auto_track_enabled = is_after_market_time(8, 30)  # 8:30 AM CST
    entry_confirmation_enabled = is_after_market_time(9, 0)  # 9:00 AM CST
    early_confirmation_enabled = is_after_market_time(8, 30)  # 8:30 AM CST

    # Ensure tracking list exists
    if "tracking_trades" not in st.session_state:
        st.session_state["tracking_trades"] = []

    def _make_track_entry(trade):
        """Build a tracking dict from a table row, pre-computing RR ratios."""
        def _p(v):
            try: return float(str(v).replace("$","").replace(",",""))
            except: return 0.0
        entry = _p(trade.get("Open") or trade.get("Entry") or "0")
        stop  = _p(trade.get("Stop")  or "0")
        t1    = _p(trade.get("T1")    or "0")
        t2    = _p(trade.get("T2")    or "0")
        dirn  = trade.get("Direction", "LONG")
        def _rr(e, s, t, d):
            try:
                risk = (e - s) if d == "LONG" else (s - e)
                rew  = (t - e) if d == "LONG" else (e - t)
                return round(rew / risk, 2) if risk > 0 else 0.0
            except: return 0.0
        rr1 = _rr(entry, stop, t1, dirn)
        rr2 = _rr(entry, stop, t2, dirn)
        return {
            "ticker": trade.get("Ticker"),
            "entry": entry,
            "stop": stop,
            "t1": t1,
            "t2": t2,
            "direction": dirn,
            "confidence": trade.get("Confidence", "N/A"),
            "atr": trade.get("ATR", "$0").replace("$", ""),
            "scenario": trade.get("Scenario", ""),
            "rr_t1": rr1,
            "rr_t2": rr2,
            "best_rr": max(rr1, rr2),
            "tracking_start_time": get_cst_now().isoformat(),
        }

    def _is_already_tracked(ticker):
        return any(t["ticker"] == ticker for t in st.session_state["tracking_trades"])

    # ── AUTO-START: auto-track ALL OPENS NEAR ENTRY at 8:30 AM CST+ ──
    if auto_track_enabled and near_entry_rows and not st.session_state["tracking_trades"]:
        for trade in near_entry_rows:
            if not _is_already_tracked(trade.get("Ticker")):
                st.session_state["tracking_trades"].append(_make_track_entry(trade))

    # ── Helper: render a scenario table with Track buttons on each row ──
    def _render_trackable_table(rows, title, table_key, display_cols):
        """Display a scenario table with a Track button per row."""
        st.markdown(f"### {title}")
        if not rows:
            st.dataframe(pd.DataFrame(columns=display_cols), use_container_width=True, height=78)
            return
        
        # Sort by Best RR (highest first)
        def _parse_rr(val):
            try: return float(str(val).replace("x",""))
            except: return 0
        rows_sorted = sorted(rows, key=lambda r: _parse_rr(r.get("Best RR", 0)), reverse=True)
        
        # Only show columns that exist in the rows
        valid_cols = [c for c in display_cols if c in rows_sorted[0]]
        df = pd.DataFrame(rows_sorted)[valid_cols]
        st.dataframe(df, use_container_width=True, height=min(40 * max(len(df), 1) + 38, 400))
        # Track buttons row underneath the table
        btn_cols = st.columns(min(len(rows), 8))
        for idx, trade in enumerate(rows[:8]):
            tkr = trade.get("Ticker", "?")
            already = _is_already_tracked(tkr)
            label = f"🎯 {tkr}" if already else f"📌 {tkr}"
            with btn_cols[idx % len(btn_cols)]:
                if st.button(label, key=f"trk_{table_key}_{idx}_{tkr}", use_container_width=True, disabled=already):
                    st.session_state["tracking_trades"].append(_make_track_entry(trade))
                    st.rerun()

    # ── SCENARIO TABLES with per-row Track buttons ──
    between_rows = [r for r in table_rows if r.get("Scenario", "").strip().startswith("⚡ OPENS BETWEEN ENTRY & STOP")]
    other_rows = [r for r in table_rows if not (r.get("Scenario", "").strip().startswith("✅ OPENS NEAR ENTRY") or r.get("Scenario", "").strip().startswith("⚡ OPENS BETWEEN ENTRY & STOP"))]
    between_rows.sort(key=lambda x: conf_priority.get(x.get("Confidence", "LOW"), 999))
    other_rows.sort(key=lambda x: conf_priority.get(x.get("Confidence", "LOW"), 999))
    
    # Build display columns — explicit ordered list so RR always appears after T2
    if table_rows and len(table_rows) > 0:
        # Explicit column order — focused set, noisy/rarely-filled columns excluded
        preferred_order = [
            "Ticker", "Direction", "Option", "Scenario", "Notes",
            "Open", "Prev Close", "Stop", "T1", "T2",
            "RR(T1)", "RR(T2)", "Best RR",
            "ATM", "Spread", "ATR", "Confidence",
            "Exp 0DTE", "Exp 2-3DTE", "Current", "Move",
        ]
        all_keys = [c for c in table_rows[0].keys() if c not in ["_scenario_id", "_best_rr_sort_main"]]
        # Only show columns in preferred_order — skip noisy extras like Prev Opt O/H/L/C
        display_cols = [c for c in preferred_order if c in all_keys]
    else:
        display_cols = []

    # ── POSITION TRACKER — multi-row table with delete buttons ──────────
    tracked = st.session_state.get("tracking_trades", [])
    if tracked:
        st.markdown("---")
        st.markdown(f"### 📍 Tracking {len(tracked)} Position(s)")

        # Clear All button
        if st.button("❌ Clear All Tracking", key="clear_all_tracking"):
            st.session_state["tracking_trades"] = []
            st.rerun()

        @st.fragment(run_every=timedelta(minutes=5))
        def _live_tracker_fragment():
            """Auto-refreshes every 5 min — only this fragment, not the whole page."""
            _tracked = st.session_state.get("tracking_trades", [])
            if not _tracked:
                return
            _now = get_cst_now()
            st.caption(f"🔄 Live prices · Last refresh: {_now.strftime('%I:%M:%S %p CST')} · Auto-refreshes every 5 min")

            import yfinance as yf
            track_rows = []
            error_tickers = []
            for ti, tr in enumerate(_tracked):
                try:
                    tk = yf.Ticker(tr['ticker'])
                    hist = tk.history(period="5d")
                    if hist.empty:
                        error_tickers.append(tr['ticker'])
                        continue
                    live_price = float(hist["Close"].iloc[-1])
                    entry = tr['entry']
                    stop = tr['stop']
                    t1 = tr['t1']
                    t2 = tr['t2']
                    direction = tr['direction']

                    if direction == "LONG":
                        pnl_pct = (live_price - entry) / entry * 100
                        passed_stop = live_price <= stop
                        passed_t1 = live_price >= t1
                        passed_t2 = live_price >= t2
                    else:
                        pnl_pct = (entry - live_price) / entry * 100
                        passed_stop = live_price >= stop
                        passed_t1 = live_price <= t1
                        passed_t2 = live_price <= t2

                    # Multi-timeframe bias (10m / 30m / 4H)
                    bias_830 = get_830_bias_eval(tr['ticker'], direction)
                    bias_10m = bias_830.get("bias_10m", "N/A") if bias_830 else "N/A"
                    bias_30m = bias_830.get("bias_30m", "N/A") if bias_830 else "N/A"
                    bias_4h  = bias_830.get("bias_4h",  "N/A") if bias_830 else "N/A"
                    alignment = bias_830.get("alignment", "N/A") if bias_830 else "N/A"
                    align_icon = "✅" if alignment == "CONFIRMED" else ("⚠️" if alignment == "DIVERGED" else "")

                    # ── Flip direction if bias contradicts tracked trade ──
                    disp_direction = direction
                    disp_stop = stop
                    disp_t1 = t1
                    disp_t2 = t2
                    flipped = False
                    opn_830 = bias_830.get("today_open") if bias_830 else None

                    if alignment == "CONFIRMED" and opn_830 is not None:
                        bias_dir = None
                        if bias_10m == "BULLISH" and bias_30m == "BULLISH" and bias_4h == "BULLISH":
                            bias_dir = "LONG"
                        elif bias_10m == "BEARISH" and bias_30m == "BEARISH" and bias_4h == "BEARISH":
                            bias_dir = "SHORT"
                        if bias_dir and bias_dir != direction:
                            flipped = True
                            disp_direction = bias_dir
                            disp_stop = round(2 * opn_830 - stop, 2)
                            disp_t1   = round(2 * opn_830 - t1, 2)
                            disp_t2   = round(2 * opn_830 - t2, 2)
                            # Recalc P&L with flipped direction
                            if disp_direction == "LONG":
                                pnl_pct = (live_price - entry) / entry * 100
                                passed_stop = live_price <= disp_stop
                                passed_t1 = live_price >= disp_t1
                                passed_t2 = live_price >= disp_t2
                            else:
                                pnl_pct = (entry - live_price) / entry * 100
                                passed_stop = live_price >= disp_stop
                                passed_t1 = live_price <= disp_t1
                                passed_t2 = live_price <= disp_t2

                    # Suggested action (uses 10m + 4H for decision)
                    if passed_stop:
                        action = "🔴 EXIT — Stop Hit"
                    elif passed_t2:
                        action = "🏆 FULL PROFIT — T2"
                    elif passed_t1:
                        action = "🎉 SCALE OUT — T1"
                    elif alignment == "CONFIRMED":
                        action = "✅ HOLD — Confirmed"
                    elif bias_10m != "N/A" and bias_4h != "N/A" and bias_10m != bias_4h:
                        action = "⚠️ CAUTION — 10m vs 4H diverged"
                    elif alignment == "DIVERGED":
                        action = "⚠️ CAUTION — Diverged"
                    elif pnl_pct >= 2.0:
                        action = "📈 TRAIL STOP"
                    else:
                        action = "⏳ MONITOR"

                    # Shorten bias labels
                    _short = lambda b: "BULL" if b == "BULLISH" else ("BEAR" if b == "BEARISH" else b)
                    # Shorten scenario
                    _scn = tr.get("scenario", "")
                    if "OPENS NEAR ENTRY" in _scn.upper():
                        _scn_short = "ONE"
                    elif "OPENS BETWEEN" in _scn.upper():
                        _scn_short = "OBE"
                    elif "OPENS PAST STOP" in _scn.upper():
                        _scn_short = "OPS"
                    elif "GAP" in _scn.upper():
                        _scn_short = "GAP"
                    else:
                        _scn_short = _scn[:10]

                    # RR from stored tracking dict (pre-computed at entry time)
                    def _live_rr(e, s, t, d):
                        try:
                            risk = (e - s) if d == "LONG" else (s - e)
                            rew  = (t - e) if d == "LONG" else (e - t)
                            return round(rew / risk, 2) if risk > 0 else 0.0
                        except: return 0.0
                    _ds = disp_stop if isinstance(disp_stop, (int, float)) else stop
                    _dt1 = disp_t1  if isinstance(disp_t1,  (int, float)) else t1
                    _dt2 = disp_t2  if isinstance(disp_t2,  (int, float)) else t2
                    rr_t1    = _live_rr(entry, _ds, _dt1, disp_direction)
                    rr_t2    = _live_rr(entry, _ds, _dt2, disp_direction)
                    best_rr  = max(rr_t1, rr_t2)
                    # Fallback to stored RR if live calc gives 0
                    if best_rr == 0:
                        rr_t1   = tr.get("rr_t1", 0)
                        rr_t2   = tr.get("rr_t2", 0)
                        best_rr = tr.get("best_rr", 0)

                    track_rows.append({
                        "Ticker":     tr['ticker'],
                        "Dir":        f"{'🔄 ' if flipped else ''}{disp_direction}",
                        "Action":     action,
                        "Scen":       _scn_short,
                        "Live $":     f"${live_price:.2f}",
                        "Entry":      f"${entry:.2f}",
                        "P&L":        f"{pnl_pct:+.2f}%",
                        "Stop":       f"${_ds:.2f}",
                        "T1":         f"${_dt1:.2f}",
                        "T2":         f"${_dt2:.2f}",
                        "RR(T1)":     f"{rr_t1:.2f}x",
                        "RR(T2)":     f"{rr_t2:.2f}x",
                        "Best RR":    f"{best_rr:.2f}x",
                        "Conf":       tr.get('confidence', 'N/A'),
                        "10m":        _short(bias_10m),
                        "30m":        _short(bias_30m),
                        "4H":         _short(bias_4h),
                        "Align":      f"{align_icon} {alignment}",
                        "_best_rr_sort": best_rr,
                    })
                except Exception:
                    error_tickers.append(tr['ticker'])

            if track_rows:
                # Sort by Best RR (highest first)
                track_rows_sorted = sorted(track_rows, key=lambda x: x.get("_best_rr_sort", 0), reverse=True)
                # Fixed column order — RR always visible right after T2
                _fixed_cols = ["Ticker", "Dir", "Action", "Scen", "Live $", "Entry",
                               "P&L", "Stop", "T1", "T2", "RR(T1)", "RR(T2)", "Best RR",
                               "Conf", "10m", "30m", "4H", "Align"]
                track_df = pd.DataFrame(track_rows_sorted)
                if "_best_rr_sort" in track_df.columns:
                    track_df = track_df.drop(columns=["_best_rr_sort"])
                # Only keep columns that exist
                track_df = track_df[[c for c in _fixed_cols if c in track_df.columns]]

                def _color_trk_bias(val):
                    if not isinstance(val, str): return ""
                    v = val.upper()
                    if "BULL" in v: return "background-color: #0a3d1f; color: #00e5a0; font-weight: 700"
                    if "BEAR" in v: return "background-color: #3d0a1a; color: #ff4d6a; font-weight: 700"
                    if "CONFIRMED" in v: return "color: #00e5a0; font-weight: 700"
                    if "DIVERGED" in v: return "color: #f0c040; font-weight: 700"
                    return ""
                _trk_bias_cols = [c for c in ["10m", "30m", "4H", "Align"] if c in track_df.columns]
                styled_trk = track_df.style.applymap(_color_trk_bias, subset=_trk_bias_cols) if _trk_bias_cols else track_df
                _trk_col_config = {
                    "Ticker": st.column_config.TextColumn("Ticker", width="small"),
                    "Dir":    st.column_config.TextColumn("Dir",    width="small"),
                    "Action": st.column_config.TextColumn("Action", width="medium"),
                    "Scen":   st.column_config.TextColumn("Scen",   width="small"),
                    "Live $": st.column_config.TextColumn("Live $", width="small"),
                    "P&L":    st.column_config.TextColumn("P&L",    width="small"),
                    "RR(T1)": st.column_config.TextColumn("RR(T1)", width="small"),
                    "RR(T2)": st.column_config.TextColumn("RR(T2)", width="small"),
                    "Best RR":st.column_config.TextColumn("Best RR",width="small"),
                    "Conf":   st.column_config.TextColumn("Conf",   width="small"),
                    "10m":    st.column_config.TextColumn("10m",    width="small"),
                    "30m":    st.column_config.TextColumn("30m",    width="small"),
                    "4H":     st.column_config.TextColumn("4H",     width="small"),
                    "Align":  st.column_config.TextColumn("Align",  width="small"),
                }
                st.dataframe(styled_trk, use_container_width=True,
                             column_config=_trk_col_config,
                             height=min(40 * max(len(track_df), 1) + 38, 400))

            if error_tickers:
                st.caption(f"⚠️ Could not fetch data for: {', '.join(error_tickers)}")

        _live_tracker_fragment()

        # Per-row delete buttons (outside fragment so they don't auto-refresh)
        del_cols = st.columns(min(len(tracked), 8))
        for di, tr in enumerate(tracked[:8]):
            with del_cols[di % len(del_cols)]:
                if st.button(f"🗑️ {tr['ticker']}", key=f"del_trk_{di}_{tr['ticker']}", use_container_width=True):
                    st.session_state["tracking_trades"].pop(di)
                    st.rerun()

    # ── ENTRY CONFIRMATION TABLE (after 9 AM CST) ────────────────
    # ── Bias date: plan_date + 1 (if plan_date is today, bias_date = today) ──
    st.markdown("---")
    from datetime import date as _date_type
    if plan_date == _date_type.today():
        bias_date = _date_type.today()
    else:
        bias_date = plan_date + timedelta(days=1)
    if bias_date != _date_type.today():
        st.info(f"📆 Bias date: **{bias_date.strftime('%A, %B %d, %Y')}** (plan date + 1)")
    # Convert to target_date param: None = live, date = backtest
    _bias_target = bias_date if bias_date != _date_type.today() else None
    # Override time gates when backtesting a past date
    if _bias_target is not None:
        early_confirmation_enabled = True
        entry_confirmation_enabled = True

    # When backtesting a past date, also include tracked trades as candidates
    _confirmation_candidates = list(near_entry_rows) if near_entry_rows else []
    if _bias_target is not None:
        tracked_as_rows = []
        for tr in st.session_state.get("tracking_trades", []):
            tkr = tr.get("ticker", "?")
            # Avoid duplicates with near_entry_rows
            if any(r.get("Ticker") == tkr for r in _confirmation_candidates):
                continue
            tracked_as_rows.append({
                "Ticker": tkr,
                "Direction": tr.get("direction", "LONG"),
                "Option": "CALL" if tr.get("direction") == "LONG" else "PUT",
                "Open": f"${tr.get('entry', 0):.2f}" if isinstance(tr.get('entry'), (int, float)) else tr.get('entry', ''),
                "Stop": f"${tr.get('stop', 0):.2f}" if isinstance(tr.get('stop'), (int, float)) else tr.get('stop', ''),
                "T1": f"${tr.get('t1', 0):.2f}" if isinstance(tr.get('t1'), (int, float)) else tr.get('t1', ''),
                "T2": f"${tr.get('t2', 0):.2f}" if isinstance(tr.get('t2'), (int, float)) else tr.get('t2', ''),
                "Confidence": tr.get("confidence", "N/A"),
                "ATR": f"${tr.get('atr', '')}" if tr.get('atr') else "",
                "Scenario": tr.get("scenario", ""),
            })
        _confirmation_candidates.extend(tracked_as_rows)
        if not _confirmation_candidates:
            st.warning("📋 No trades to evaluate. Run the scanner first or add trades to tracking.")

    # ── ENTRY CONFIRMATION TABLE — 8:30 AM CST+ (10m / 30m / 4H) ──────────
    if early_confirmation_enabled and _confirmation_candidates:
        st.markdown("### 🕣 ENTRY CONFIRMATION TABLE (8:30 AM CST+)")
        st.markdown("All **OPENS NEAR ENTRY** with **10m · 30m · 4H** bias — anchored to 8:30 AM CST open")

        early_rows = []
        for trade in _confirmation_candidates:
            ticker = trade.get("Ticker", "?")
            direction = trade.get("Direction")
            bias_830 = get_830_bias_eval(ticker, direction, target_date=_bias_target)

            b10 = bias_830.get("bias_10m", "N/A") if bias_830 else "N/A"
            b30 = bias_830.get("bias_30m", "N/A") if bias_830 else "N/A"
            b4h = bias_830.get("bias_4h",  "N/A") if bias_830 else "N/A"
            aln = bias_830.get("alignment", "N/A") if bias_830 else "N/A"
            opn = bias_830.get("today_open", "N/A") if bias_830 else "N/A"
            cur = bias_830.get("current_price", "N/A") if bias_830 else "N/A"
            icon = "✅" if aln == "CONFIRMED" else ("⚠️" if aln == "DIVERGED" else "")

            # ── Flip direction if bias contradicts planned trade ──
            disp_direction = direction
            disp_option = trade.get("Option")
            disp_stop = trade.get("Stop")
            disp_t1 = trade.get("T1")
            disp_t2 = trade.get("T2")
            flipped = False

            if aln == "CONFIRMED" and isinstance(opn, (int, float)):
                bias_dir = None
                if b10 == "BULLISH" and b30 == "BULLISH" and b4h == "BULLISH":
                    bias_dir = "LONG"
                elif b10 == "BEARISH" and b30 == "BEARISH" and b4h == "BEARISH":
                    bias_dir = "SHORT"

                if bias_dir and bias_dir != direction:
                    flipped = True
                    disp_direction = bias_dir
                    disp_option = "CALL" if bias_dir == "LONG" else "PUT"
                    # Mirror stop/T1/T2 around the open price
                    def _flip_price(val_str, ref):
                        try:
                            v = float(str(val_str).replace("$", "").replace(",", ""))
                            return f"${round(2 * ref - v, 2):.2f}"
                        except Exception:
                            return val_str
                    disp_stop = _flip_price(disp_stop, opn)
                    disp_t1   = _flip_price(disp_t1, opn)
                    disp_t2   = _flip_price(disp_t2, opn)

            # Calculate RR ratios for 8:30 AM confirmation
            def _parse_price(price_str):
                """Extract float from price string."""
                try:
                    return float(str(price_str).replace("$", "").replace(",", ""))
                except:
                    return None
            
            def _calc_rr_830(entry_val, stop_val, target_val, direction):
                """Calculate risk-reward ratio."""
                try:
                    if direction == "LONG":
                        risk = entry_val - stop_val
                        reward = target_val - entry_val
                    else:
                        risk = stop_val - entry_val
                        reward = entry_val - target_val
                    return round(reward / risk, 2) if risk != 0 else 0
                except:
                    return 0
            
            entry_830 = _parse_price(disp_stop) or _parse_price(trade.get("Open", "$0")) or 0
            stop_830 = _parse_price(disp_stop) or 0
            t1_830 = _parse_price(disp_t1) or 0
            t2_830 = _parse_price(disp_t2) or 0
            
            rr_t1_830 = _calc_rr_830(entry, stop_830, t1_830, disp_direction) if all([entry, stop_830, t1_830]) else 0
            rr_t2_830 = _calc_rr_830(entry, stop_830, t2_830, disp_direction) if all([entry, stop_830, t2_830]) else 0
            best_rr_830 = max(rr_t1_830, rr_t2_830)
            
            early_rows.append({
                "Ticker": ticker,
                "Direction": f"{'🔄 ' if flipped else ''}{disp_direction}",
                "Option": disp_option,
                "Open (8:30)": f"${opn:.2f}" if isinstance(opn, (int, float)) else opn,
                "Current": f"${cur:.2f}" if isinstance(cur, (int, float)) else cur,
                "Stop": disp_stop,
                "T1": disp_t1,
                "T2": disp_t2,
                "RR(T1)": f"{rr_t1_830:.2f}x" if rr_t1_830 else "N/A",
                "RR(T2)": f"{rr_t2_830:.2f}x" if rr_t2_830 else "N/A",
                "Best RR": f"{best_rr_830:.2f}x" if best_rr_830 else "N/A",
                "10m Bias": b10,
                "30m Bias": b30,
                "4H Bias": b4h,
                "Alignment": f"{icon} {aln}",
                "Confidence": trade.get("Confidence"),
                "ATR": trade.get("ATR"),
                "_best_rr_830_sort": best_rr_830,
            })

        if early_rows:
            confirmed_rows = [r for r in early_rows if "CONFIRMED" in r.get("Alignment", "")]
            diverged_rows  = [r for r in early_rows if "DIVERGED" in r.get("Alignment", "")]
            other_rows     = [r for r in early_rows if "CONFIRMED" not in r.get("Alignment", "") and "DIVERGED" not in r.get("Alignment", "")]

            # Color helper
            def _color_830(val):
                if not isinstance(val, str): return ""
                v = val.upper()
                if "BULLISH" in v: return "background-color: #0a3d1f; color: #00e5a0; font-weight: 700"
                if "BEARISH" in v: return "background-color: #3d0a1a; color: #ff4d6a; font-weight: 700"
                if "CONFIRMED" in v: return "color: #00e5a0; font-weight: 700"
                if "DIVERGED" in v: return "color: #f0c040; font-weight: 700"
                return ""
            bias_cols_830 = ["10m Bias", "30m Bias", "4H Bias", "Alignment"]

            # ── Confirmed trades (trackable) ──
            show_confirmed = confirmed_rows + other_rows
            if show_confirmed:
                # Sort by Best RR (highest first)
                show_confirmed_sorted = sorted(show_confirmed, key=lambda x: x.get("_best_rr_830_sort", 0), reverse=True)
                conf_df = pd.DataFrame(show_confirmed_sorted)
                # Remove sort helper column
                if "_best_rr_830_sort" in conf_df.columns:
                    conf_df = conf_df.drop(columns=["_best_rr_830_sort"])
                cols_830 = [c for c in bias_cols_830 if c in conf_df.columns]
                styled_conf = conf_df.style.applymap(_color_830, subset=cols_830)
                st.dataframe(styled_conf, use_container_width=True, height=min(40 * max(len(conf_df), 1) + 38, 400))
                st.success(f"🎯 {len(confirmed_rows)} trade(s) CONFIRMED — all biases aligned!")

                # ── Auto-track confirmed 8:30 AM trades ──
                for cr in confirmed_rows:
                    _tkr = cr.get("Ticker", "?")
                    if not _is_already_tracked(_tkr):
                        _dir = cr.get("Direction", "").replace("🔄 ", "")
                        _entry_val = cr.get("Open (8:30)", "$0").replace("$", "").replace(",", "")
                        _stop_val  = cr.get("Stop", "$0").replace("$", "").replace(",", "") if isinstance(cr.get("Stop"), str) else str(cr.get("Stop", 0))
                        _t1_val    = cr.get("T1", "$0").replace("$", "").replace(",", "") if isinstance(cr.get("T1"), str) else str(cr.get("T1", 0))
                        _t2_val    = cr.get("T2", "$0").replace("$", "").replace(",", "") if isinstance(cr.get("T2"), str) else str(cr.get("T2", 0))
                        try:
                            st.session_state["tracking_trades"].append({
                                "ticker": _tkr,
                                "entry": float(_entry_val),
                                "stop": float(_stop_val),
                                "t1": float(_t1_val),
                                "t2": float(_t2_val),
                                "direction": _dir,
                                "confidence": cr.get("Confidence", "N/A"),
                                "atr": cr.get("ATR", "").replace("$", "") if isinstance(cr.get("ATR"), str) else str(cr.get("ATR", "")),
                                "scenario": "8:30 CONFIRMED",
                                "tracking_start_time": get_cst_now().isoformat(),
                            })
                        except (ValueError, TypeError):
                            pass
            else:
                st.info("⏳ No confirmed trades yet.")

            # ── Diverged trades (separate table, not tracked) ──
            if diverged_rows:
                st.markdown("#### ⚠️ DIVERGED — Do Not Track")
                st.caption("These trades have conflicting bias — removed from tracking candidates.")
                # Sort by Best RR (highest first)
                diverged_rows_sorted = sorted(diverged_rows, key=lambda x: x.get("_best_rr_830_sort", 0), reverse=True)
                div_df = pd.DataFrame(diverged_rows_sorted)
                # Remove sort helper column
                if "_best_rr_830_sort" in div_df.columns:
                    div_df = div_df.drop(columns=["_best_rr_830_sort"])
                cols_830_div = [c for c in bias_cols_830 if c in div_df.columns]
                styled_div = div_df.style.applymap(_color_830, subset=cols_830_div)
                st.dataframe(styled_div, use_container_width=True, height=min(40 * max(len(div_df), 1) + 38, 400))
        else:
            st.info("⏳ Waiting for 8:30 AM CST data...")

    # ── ENTRY CONFIRMATION TABLE — 9:00 AM CST+ (10m + 4H) ──────────
    if entry_confirmation_enabled and _confirmation_candidates:
        st.markdown("---")
        st.markdown("### ✅ ENTRY CONFIRMATION TABLE (9:00 AM CST+)")
        st.markdown("Trades with **CONFIRMED** 10m + 4H bias — ready to enter")

        confirmation_rows = []
        for trade in _confirmation_candidates:
            ticker = trade.get("Ticker", "?")
            direction = trade.get("Direction")
            entry_p = trade.get("Entry") or trade.get("Open")
            try:
                entry_val = float(str(entry_p).replace("$", "").replace(",", ""))
            except Exception:
                entry_val = 0
            bias_eval = get_multiframe_bias_eval(ticker, entry_val, direction, target_date=_bias_target)

            if bias_eval and bias_eval.get("alignment") == "CONFIRMED":
                confirmation_rows.append({
                    "Ticker": ticker,
                    "Direction": direction,
                    "Option": trade.get("Option"),
                    "Current": f"${bias_eval['current_price']:.2f}",
                    "Stop": trade.get("Stop"),
                    "T1": trade.get("T1"),
                    "T2": trade.get("T2"),
                    f"{bias_eval['tf_short']} Bias": bias_eval.get("bias_10min"),
                    f"{bias_eval['tf_long']} Bias": bias_eval.get("bias_30min"),
                    "Alignment": f"✅ CONFIRMED",
                    "Confidence": trade.get("Confidence"),
                    "ATR": trade.get("ATR"),
                })

        if confirmation_rows:
            confirm_df = pd.DataFrame(confirmation_rows)
            st.dataframe(confirm_df, use_container_width=True, height=min(40 * max(len(confirm_df), 1) + 38, 400))
            st.success(f"🎯 {len(confirmation_rows)} trade(s) ready for entry with confirmed bias!")

            # ── Auto-track confirmed 9:00 AM trades ──
            for cr9 in confirmation_rows:
                _tkr9 = cr9.get("Ticker", "?")
                if not _is_already_tracked(_tkr9):
                    _dir9 = cr9.get("Direction", "LONG")
                    _stop9 = cr9.get("Stop", "$0").replace("$", "").replace(",", "") if isinstance(cr9.get("Stop"), str) else str(cr9.get("Stop", 0))
                    _t1_9  = cr9.get("T1", "$0").replace("$", "").replace(",", "") if isinstance(cr9.get("T1"), str) else str(cr9.get("T1", 0))
                    _t2_9  = cr9.get("T2", "$0").replace("$", "").replace(",", "") if isinstance(cr9.get("T2"), str) else str(cr9.get("T2", 0))
                    _cur9  = cr9.get("Current", "$0").replace("$", "").replace(",", "") if isinstance(cr9.get("Current"), str) else str(cr9.get("Current", 0))
                    try:
                        st.session_state["tracking_trades"].append({
                            "ticker": _tkr9,
                            "entry": float(_cur9),
                            "stop": float(_stop9),
                            "t1": float(_t1_9),
                            "t2": float(_t2_9),
                            "direction": _dir9,
                            "confidence": cr9.get("Confidence", "N/A"),
                            "atr": cr9.get("ATR", "").replace("$", "") if isinstance(cr9.get("ATR"), str) else str(cr9.get("ATR", "")),
                            "scenario": "9:00 CONFIRMED",
                            "tracking_start_time": get_cst_now().isoformat(),
                        })
                    except (ValueError, TypeError):
                        pass
        else:
            st.info("⏳ No trades with confirmed bias yet. Check again in a moment...")

    _render_trackable_table(near_entry_rows, "✅ OPENS NEAR ENTRY", "near", display_cols)
    _render_trackable_table(between_rows, "⚡ OPENS BETWEEN ENTRY & STOP", "btwn", display_cols)
    _render_trackable_table(other_rows, "📊 All Other Scenarios", "other", display_cols)

# ── Next Day Planning handler ────────────────────────
if plan_run:
  with tab_plan:
    if missing_creds:
        st.error("Enter API credentials in the sidebar first.")
    else:
        plan_tickers = [t.strip().upper() for t in plan_tickers_raw.strip().split(",") if t.strip()]
        if not plan_tickers:
            st.warning("Enter at least one ticker.")
        else:
            st.markdown("### 🗓️ Tomorrow's Intraday Plan")
            plan_progress = st.progress(0)
            plan_status = st.empty()
            plan_rows = []

            for i, ticker in enumerate(plan_tickers):
                plan_status.text(f"Analyzing {ticker}... ({i+1}/{len(plan_tickers)})")
                plan_progress.progress((i + 1) / len(plan_tickers))
                try:
                    p_end = plan_date
                    p_start = p_end - timedelta(days=400)
                    if data_source == "Alpaca":
                        p_daily = get_daily_bars_alpaca(ticker, str(p_start), str(p_end), api_key, api_secret)
                    else:
                        p_daily = get_daily_bars(ticker, str(p_start), str(p_end), api_key)

                    if p_daily is None or p_daily.empty or len(p_daily) < 60:
                        continue

                    # ── Intraday signal — simpler than swing signal ───────────────
                    # For options planning we just need: direction, entry, ATR levels.
                    # We do NOT apply swing-trade filters (vol conflict, LOW conf, etc.)
                    # because intraday options don't hold overnight and every stock with
                    # a daily candle is tradeable regardless of signal strength.
                    daily_close = float(p_daily["close"].iloc[-1])
                    daily_open  = float(p_daily["open"].iloc[-1])
                    atr_14 = float((p_daily["high"] - p_daily["low"]).rolling(14).mean().iloc[-1])

                    if daily_close > daily_open:
                        direction = "LONG"
                    elif daily_close < daily_open:
                        direction = "SHORT"
                    else:
                        # True doji — use 5-day trend as tiebreaker
                        prev5 = p_daily["close"].iloc[-6:-1]
                        direction = "LONG" if daily_close >= float(prev5.iloc[0]) else "SHORT"

                    # Build a lightweight signal dict matching _estimator_signal_at output
                    recent_low  = float(p_daily["low"].iloc[-10:].min())
                    recent_high = float(p_daily["high"].iloc[-10:].max())
                    entry = round(daily_close, 2)

                    if direction == "LONG":
                        stop_px = round(recent_low  - atr_14 * 0.3, 2)
                    else:
                        stop_px = round(recent_high + atr_14 * 0.3, 2)

                    # Use the full scoring engine for informational score/verdict only
                    # (doesn't gate the entry — all stocks get a plan row)
                    sig_full = _estimator_signal_at(p_daily, len(p_daily) - 1,
                                                    use_fib=use_fib, fib_tol=fib_tol, ticker="")
                    if sig_full is not None:
                        verdict    = sig_full["verdict"]
                        confidence = sig_full["confidence"]
                        score      = sig_full["score"]
                        fib_bias   = sig_full["fib_bias"]
                        vol_bias   = sig_full["vol_bias"]
                        vol_trend  = sig_full["vol_trend"]
                        signals    = sig_full["signals"]
                        best_setup = sig_full["best_setup"]
                    else:
                        # Filtered by swing rules — show raw candle info
                        verdict    = "LEAN BULLISH" if direction == "LONG" else "LEAN BEARISH"
                        confidence = "LOW"
                        score      = 2 if direction == "LONG" else -2
                        fib_bias   = "N/A"
                        vol_bias   = "N/A"
                        vol_trend  = "N/A"
                        signals    = "Day:BULL" if direction == "LONG" else "Day:BEAR"
                        best_setup = False
                    atr_1d = atr_14  # single-day expected range
                    intra_stop_dist = round(atr_1d * 0.3, 2)   # ~30% of daily range
                    intra_t1_dist = round(atr_1d * 0.5, 2)     # ~50% of daily range (1.7:1 R:R)
                    intra_t2_dist = round(atr_1d * 0.8, 2)     # ~80% of daily range (2.7:1 R:R)

                    if direction == "LONG":
                        intra_stop = round(entry - intra_stop_dist, 2)
                        intra_t1 = round(entry + intra_t1_dist, 2)
                        intra_t2 = round(entry + intra_t2_dist, 2)
                    else:
                        intra_stop = round(entry + intra_stop_dist, 2)
                        intra_t1 = round(entry - intra_t1_dist, 2)
                        intra_t2 = round(entry - intra_t2_dist, 2)

                    # ── ATR-based strike selection ─────────────────────────────
                    # Spread width = ~40% of ATR (one intraday stop-distance).
                    # Capped at ATR so the OTM strike is always reachable on a normal day.
                    # Snapped to standard listed increments: 0.5, 1, 2, 2.5, 5, 10.
                    LISTED_INCS = [0.5, 1, 2, 2.5, 5, 10]

                    spread_target = atr_14 * 0.40
                    # Nearest listed inc >= target
                    strike_inc = next((s for s in LISTED_INCS if s >= spread_target), LISTED_INCS[-1])
                    # Floor: min sensible increment for the price level
                    min_inc = 0.5 if entry < 20 else (1 if entry < 50 else 2.5)
                    # Cap: don't pick a spread wider than the ATR (OTM would be unreachable)
                    max_inc = next((s for s in LISTED_INCS if s >= atr_14), LISTED_INCS[-1])
                    strike_inc = max(strike_inc, min_inc)
                    strike_inc = min(strike_inc, max_inc)

                    atm_strike = round(round(entry / strike_inc) * strike_inc, 2)
                    spread_pct = round(strike_inc / entry * 100, 1)

                    if direction == "LONG":
                        itm_strike = round(atm_strike - strike_inc, 2)
                        otm_strike = round(atm_strike + strike_inc, 2)
                        opt_type = "CALL"
                        aggressive   = f"${otm_strike} Call (OTM +{spread_pct}% — needs ${strike_inc:.2f} move)"
                        moderate     = f"${atm_strike} Call (ATM — balanced, spread ${strike_inc:.2f})"
                        conservative = f"${itm_strike} Call (ITM -{spread_pct}% — higher delta)"
                    else:
                        itm_strike = round(atm_strike + strike_inc, 2)
                        otm_strike = round(atm_strike - strike_inc, 2)
                        opt_type = "PUT"
                        aggressive   = f"${otm_strike} Put (OTM -{spread_pct}% — needs ${strike_inc:.2f} move)"
                        moderate     = f"${atm_strike} Put (ATM — balanced, spread ${strike_inc:.2f})"
                        conservative = f"${itm_strike} Put (ITM +{spread_pct}% — higher delta)"

                    # Expiry suggestion — skip weekends properly
                    def _next_trading_day(d, skip=1):
                        """Return the Nth next trading day from d, skipping weekends."""
                        result = d
                        added = 0
                        while added < skip:
                            result += timedelta(days=1)
                            if result.weekday() < 5:  # Mon–Fri
                                added += 1
                        return result

                    next_trade   = _next_trading_day(p_end, 1)   # next trading day (0DTE)
                    trade_plus2  = _next_trading_day(p_end, 3)   # 3 trading days out (2-3DTE)
                    expiry_0dte  = next_trade.strftime("%m/%d")
                    expiry_2dte  = trade_plus2.strftime("%m/%d")

                    gap_threshold = round(entry * 0.005, 2)

                    if direction == "LONG":
                        open_above = f"Enter CALL at ~${entry:.2f} — stop ${intra_stop:.2f}, T1 ${intra_t1:.2f}, T2 ${intra_t2:.2f}"
                        open_between = f"Better entry between ${intra_stop:.2f}-${entry:.2f} — stop ${intra_stop:.2f}, T1 ${intra_t1:.2f}, T2 ${intra_t2:.2f}"
                        open_below_stop = f"Opens below ${intra_stop:.2f} — SKIP CALL, consider PUT"
                        big_gap = f"Gap up >${gap_threshold:.2f} above ${entry:.2f} — wait for pullback near ${entry:.2f}"
                    else:
                        open_above = f"Opens above ${intra_stop:.2f} — SKIP PUT, consider CALL"
                        open_between = f"Enter PUT at ~${entry:.2f} — stop ${intra_stop:.2f}, T1 ${intra_t1:.2f}, T2 ${intra_t2:.2f}"
                        open_below_stop = f"Better entry between ${entry:.2f}-${intra_stop:.2f} — stop ${intra_stop:.2f}, T1 ${intra_t1:.2f}, T2 ${intra_t2:.2f}"
                        big_gap = f"Gap down >${gap_threshold:.2f} below ${entry:.2f} — wait for bounce near ${entry:.2f}"

                    grade_info = _get_entry_grade(score, confidence)
                    # Classify instrument for note label only
                    profile     = _classify_instrument(ticker, p_daily)
                    is_mean_rev = profile["is_mean_rev"]
                    intraday_note = "⚠️ ETF/mean-rev — intraday only, no swing" if is_mean_rev else ""
                    plan_rows.append({
                        "Ticker": ticker,
                        "Grade": grade_info["entry_grade"],
                        "Entry Signal": grade_info["entry_label"],
                        "Exp WR%": f"{grade_info['expected_wr']:.0f}%",
                        "Direction": direction,
                        "Option": opt_type,
                        "Verdict": verdict,
                        "Confidence": confidence,
                        "Note": intraday_note,
                        "Best Setup": "Y" if best_setup else "N",
                        "Close": round(entry, 2),
                        "ATR": round(atr_1d, 2),
                        "Intra Stop": round(intra_stop, 2),
                        "Intra T1": round(intra_t1, 2),
                        "Intra T2": round(intra_t2, 2),
                        "Risk $": round(intra_stop_dist, 2),
                        "T1 Reward $": round(intra_t1_dist, 2),
                        "T2 Reward $": round(intra_t2_dist, 2),
                        "ATM Strike": round(atm_strike, 2),
                        "Aggressive": aggressive,
                        "Moderate": moderate,
                        "Conservative": conservative,
                        "0DTE Exp": expiry_0dte,
                        "2-3DTE Exp": expiry_2dte,
                        "Fib": fib_bias,
                        "Vol Bias": vol_bias,
                        "Vol Trend": vol_trend,
                        "Signals": signals,
                        "If opens near entry": open_above if direction == "LONG" else open_between,
                        "If opens between entry & stop": open_between if direction == "LONG" else open_above,
                        "If opens past stop": open_below_stop,
                        "If big gap": big_gap,
                    })
                    # Add multi-timeframe bias to the last appended row
                    bias_info = get_multiframe_bias_eval(ticker, entry, direction)
                    if bias_info:
                        plan_rows[-1]["Short TF"] = bias_info.get("tf_short", "")
                        plan_rows[-1]["Short Bias"] = bias_info.get("bias_10min", "N/A")
                        plan_rows[-1]["Long TF"] = bias_info.get("tf_long", "")
                        plan_rows[-1]["Long Bias"] = bias_info.get("bias_30min", "N/A")
                        plan_rows[-1]["Bias Align"] = ("✅" if bias_info.get("alignment") == "CONFIRMED" else "⚠️") + " " + bias_info.get("alignment", "")
                    else:
                        plan_rows[-1]["Short TF"] = ""
                        plan_rows[-1]["Short Bias"] = "N/A"
                        plan_rows[-1]["Long TF"] = ""
                        plan_rows[-1]["Long Bias"] = "N/A"
                        plan_rows[-1]["Bias Align"] = "—"

                    # Add 10m / 30m / 4H bias (plan_date + 1, or live if today)
                    _plan_bias_date = None if plan_date == date.today() else plan_date + timedelta(days=1)
                    bias_830 = get_830_bias_eval(ticker, direction, target_date=_plan_bias_date)
                    plan_rows[-1]["10m Bias"] = bias_830.get("bias_10m", "N/A") if bias_830 else "N/A"
                    plan_rows[-1]["30m Bias"] = bias_830.get("bias_30m", "N/A") if bias_830 else "N/A"
                    plan_rows[-1]["4H Bias"]  = bias_830.get("bias_4h",  "N/A") if bias_830 else "N/A"
                except:
                    continue

            plan_progress.empty()
            plan_status.empty()

            if not plan_rows:
                st.info("No actionable signals generated for the given tickers.")
            else:
                plan_df = pd.DataFrame(plan_rows)

                # ── Compact symbol summary table ───────────────────────────
                summary_cols = ["Ticker", "Direction", "Option", "Grade",
                                "Verdict", "Confidence", "Close", "ATR",
                                "Intra Stop", "Intra T1", "Intra T2",
                                "ATM Strike",
                                "10m Bias", "30m Bias", "4H Bias",
                                "Short Bias", "Long Bias", "Bias Align",
                                "0DTE Exp", "2-3DTE Exp"]
                summary_cols = [c for c in summary_cols if c in plan_df.columns]
                summary_df = plan_df[summary_cols].copy()
                # Format numeric price columns to 2 decimal places
                _price_cols = ["Close", "ATR", "Intra Stop", "Intra T1", "Intra T2", "ATM Strike"]
                for _pc in _price_cols:
                    if _pc in summary_df.columns:
                        summary_df[_pc] = summary_df[_pc].apply(lambda x: f"{x:.2f}" if isinstance(x, (int, float)) else x)
                summary_df.rename(columns={
                    "Intra Stop": "Stop", "Intra T1": "T1",
                    "Intra T2": "T2", "ATM Strike": "ATM",
                    "0DTE Exp": "0DTE", "2-3DTE Exp": "2-3DTE",
                }, inplace=True)
                st.markdown("#### 📋 All Symbols")
                # Color the 10m/30m/4H bias columns
                def _color_plan_bias(val):
                    if not isinstance(val, str): return ""
                    v = val.upper()
                    if "BULLISH" in v: return "background-color: #0a3d1f; color: #00e5a0; font-weight: 700"
                    if "BEARISH" in v: return "background-color: #3d0a1a; color: #ff4d6a; font-weight: 700"
                    return ""
                _bias_style_cols = [c for c in ["10m Bias", "30m Bias", "4H Bias"] if c in summary_df.columns]
                styled_summary = summary_df.style.applymap(_color_plan_bias, subset=_bias_style_cols) if _bias_style_cols else summary_df
                st.dataframe(styled_summary, use_container_width=True,
                             hide_index=True, height=min(38*len(summary_df)+38, 320))
                longs = [r for r in plan_rows if r["Direction"] == "LONG"]
                shorts = [r for r in plan_rows if r["Direction"] == "SHORT"]
                best = [r for r in plan_rows if r["Best Setup"] == "Y"]

                sc1, sc2, sc3, sc4 = st.columns(4)
                sc1.metric("Total Signals", len(plan_rows))
                sc2.metric("LONG (Calls)", len(longs))
                sc3.metric("SHORT (Puts)", len(shorts))
                sc4.metric("Best Setups", len(best))

                # Highlight best setups
                if best:
                    st.markdown(
                        '<div style="background:#00e5a010;border:1px solid #00e5a030;padding:12px;'
                        'border-radius:6px;margin:12px 0">'
                        '<div style="font-size:11px;font-weight:700;color:#00e5a0;margin-bottom:6px">⭐ BEST SETUPS (all signals aligned)</div>'
                        + "".join(
                            f'<div style="font-size:12px;color:#e8ecff;margin-bottom:2px">'
                            f'<b>{r["Ticker"]}</b> — {r["Option"]} · ATM ${r["ATM Strike"]:.0f} · '
                            f'Stop ${r["Intra Stop"]:.2f} · T1 ${r["Intra T1"]:.2f} · T2 ${r["Intra T2"]:.2f}</div>'
                            for r in best
                        )
                        + '</div>',
                        unsafe_allow_html=True,
                    )

                # Action plan per ticker
                for r in plan_rows:
                    dir_color = "#00e5a0" if r["Direction"] == "LONG" else "#ff4d6a"
                    opt_icon = "📞" if r["Option"] == "CALL" else "📉"
                    best_badge = ' <span style="background:#00e5a020;color:#00e5a0;font-size:8px;padding:1px 5px;border-radius:2px;font-weight:700">⭐ BEST</span>' if r["Best Setup"] == "Y" else ""
                    st.markdown(
                        f'<div style="background:#0d0f17;border:1px solid #1a1d2e;border-left:3px solid {dir_color};'
                        f'padding:14px 18px;border-radius:4px;margin-bottom:8px">'
                        # Header
                        f'<div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:8px">'
                        f'<span style="font-size:15px;font-weight:900;color:#e8ecff">{opt_icon} {r["Ticker"]} — {r["Option"]}{best_badge}</span>'
                        f'<span style="color:{dir_color};font-weight:700;font-size:12px">{r["Direction"]} · {r["Verdict"]} · {r["Confidence"]}</span>'
                        f'</div>'
                        # Price levels
                        f'<div style="display:flex;gap:16px;font-size:11px;margin-bottom:8px;flex-wrap:wrap">'
                        f'<span style="color:#6b7099">Close: <b style="color:#e8ecff">${r["Close"]:.2f}</b></span>'
                        f'<span style="color:#6b7099">ATR: <b style="color:#a78bfa">${r["ATR"]:.2f}</b></span>'
                        f'<span style="color:#6b7099">Stop: <b style="color:#ff4d6a">${r["Intra Stop"]:.2f}</b> (-${r["Risk $"]:.2f})</span>'
                        f'<span style="color:#6b7099">T1: <b style="color:#00e5a0">${r["Intra T1"]:.2f}</b> (+${r["T1 Reward $"]:.2f})</span>'
                        f'<span style="color:#6b7099">T2: <b style="color:#22d3ee">${r["Intra T2"]:.2f}</b> (+${r["T2 Reward $"]:.2f})</span>'
                        f'</div>'
                        # Option strikes
                        f'<div style="background:#090b13;border:1px solid #1a1d2e;border-radius:4px;padding:10px;margin-bottom:8px">'
                        f'<div style="font-size:8px;color:#3a3d5c;letter-spacing:1.5px;margin-bottom:6px">OPTION STRIKES · Exp: {r["0DTE Exp"]} (0DTE) or {r["2-3DTE Exp"]} (2-3 DTE)</div>'
                        f'<div style="font-size:10px;line-height:2;color:#c8cce8">'
                        f'<div>🎯 Aggressive: <b style="color:#f5c842">{r["Aggressive"]}</b></div>'
                        f'<div>⚖️ Moderate: <b style="color:#4d9fff">{r["Moderate"]}</b></div>'
                        f'<div>🛡️ Conservative: <b style="color:#00e5a0">{r["Conservative"]}</b></div>'
                        f'</div></div>'
                        # Scenarios
                        f'<div style="font-size:10px;line-height:2.2;color:#c8cce8">'
                        f'<div>✅ Opens near entry → <b>{r["If opens near entry"]}</b></div>'
                        f'<div>⚡ Opens between entry & stop → <b>{r["If opens between entry & stop"]}</b></div>'
                        f'<div>❌ Opens past stop → <b>{r["If opens past stop"]}</b></div>'
                        f'<div>⚠️ Big gap → <b>{r["If big gap"]}</b></div>'
                        f'</div>'
                        f'<div style="font-size:9px;color:#3a3d5c;margin-top:6px">{r["Signals"]} · Fib:{r["Fib"]} · Vol:{r["Vol Bias"]} · Trend:{r["Vol Trend"]}</div>'
                        f'</div>',
                        unsafe_allow_html=True,
                    )

                # CSV download
                # Store plan in session for morning check
                st.session_state["plan_data"] = plan_rows

                plan_csv = plan_df.to_csv(index=False)
                st.download_button(
                    "📥 Download Tomorrow's Plan CSV", plan_csv,
                    file_name=f"intraday_plan_{date.today()}.csv",
                    mime="text/csv", use_container_width=True,
                )

# ── Sector Scan ──────────────────────────────────────
if sector_scan_btn:
  with tab_sector:
    st.markdown("### 🔥 Sector Performance Analysis")
    
    with st.spinner("Analyzing sector performance..."):
        sector_perf = get_sector_performance(api_key, api_secret, data_source)
    
    if sector_perf:
        # sector_perf is a list sorted by momentum
        # Display hot sectors (positive momentum)
        hot_sectors = [s for s in sector_perf if s["momentum"] > 0]
        cold_sectors = [s for s in sector_perf if s["momentum"] <= 0]
        
        col_hot, col_cold = st.columns(2)
        
        with col_hot:
            st.markdown(
                '<div style="background:#00e5a015;border:1px solid #00e5a040;padding:12px;border-radius:8px;margin-bottom:12px">'
                '<div style="font-size:14px;font-weight:bold;color:#00e5a0;margin-bottom:4px">🔥 HOT SECTORS</div>'
                '<div style="font-size:10px;color:#6b7099">Positive momentum (buy strength)</div>'
                '</div>',
                unsafe_allow_html=True
            )
            
            if hot_sectors:
                for rank, data in enumerate(hot_sectors[:5], 1):
                    d1_color = "#00e5a0" if data["change_1d"] >= 0 else "#ff4d6a"
                    w1_color = "#00e5a0" if data["change_1w"] >= 0 else "#ff4d6a"
                    m1_color = "#00e5a0" if data["change_1m"] >= 0 else "#ff4d6a"
                    
                    st.markdown(
                        f'<div style="background:#0d0f17;border:1px solid #1a1d2e;padding:10px;border-radius:4px;margin-bottom:6px">'
                        f'<div style="display:flex;justify-content:space-between;align-items:center">'
                        f'<span style="font-size:14px;font-weight:bold;color:#e8ecff">#{rank} {data["emoji"]} {data["name"]}</span>'
                        f'<span style="font-size:11px;color:#00e5a0;font-weight:bold">+{data["momentum"]:.1f}</span>'
                        f'</div>'
                        f'<div style="font-size:10px;color:#6b7099;margin-top:4px">'
                        f'{data["etf"]} · <span style="color:{d1_color}">1D: {data["change_1d"]:+.1f}%</span> · '
                        f'<span style="color:{w1_color}">1W: {data["change_1w"]:+.1f}%</span> · '
                        f'<span style="color:{m1_color}">1M: {data["change_1m"]:+.1f}%</span>'
                        f'</div>'
                        f'<div style="font-size:9px;color:#4d9fff;margin-top:3px">'
                        f'Top: {", ".join(data["stocks"][:5])}'
                        f'</div>'
                        f'</div>',
                        unsafe_allow_html=True
                    )
            else:
                st.info("No hot sectors found.")
        
        with col_cold:
            st.markdown(
                '<div style="background:#ff4d6a15;border:1px solid #ff4d6a40;padding:12px;border-radius:8px;margin-bottom:12px">'
                '<div style="font-size:14px;font-weight:bold;color:#ff4d6a;margin-bottom:4px">❄️ COLD SECTORS</div>'
                '<div style="font-size:10px;color:#6b7099">Negative momentum (avoid or short)</div>'
                '</div>',
                unsafe_allow_html=True
            )
            
            if cold_sectors:
                for rank, data in enumerate(cold_sectors[:5], 1):
                    d1_color = "#00e5a0" if data["change_1d"] >= 0 else "#ff4d6a"
                    w1_color = "#00e5a0" if data["change_1w"] >= 0 else "#ff4d6a"
                    m1_color = "#00e5a0" if data["change_1m"] >= 0 else "#ff4d6a"
                    
                    st.markdown(
                        f'<div style="background:#0d0f17;border:1px solid #1a1d2e;padding:10px;border-radius:4px;margin-bottom:6px">'
                        f'<div style="display:flex;justify-content:space-between;align-items:center">'
                        f'<span style="font-size:14px;font-weight:bold;color:#e8ecff">#{rank} {data["emoji"]} {data["name"]}</span>'
                        f'<span style="font-size:11px;color:#ff4d6a;font-weight:bold">{data["momentum"]:.1f}</span>'
                        f'</div>'
                        f'<div style="font-size:10px;color:#6b7099;margin-top:4px">'
                        f'{data["etf"]} · <span style="color:{d1_color}">1D: {data["change_1d"]:+.1f}%</span> · '
                        f'<span style="color:{w1_color}">1W: {data["change_1w"]:+.1f}%</span> · '
                        f'<span style="color:{m1_color}">1M: {data["change_1m"]:+.1f}%</span>'
                        f'</div>'
                        f'<div style="font-size:9px;color:#4d9fff;margin-top:3px">'
                        f'Top: {", ".join(data["stocks"][:5])}'
                        f'</div>'
                        f'</div>',
                        unsafe_allow_html=True
                    )
            else:
                st.info("No cold sectors found.")
        
        # Now scan stocks in hot sectors
        if hot_sectors:
            st.markdown("---")
            st.markdown("### 🎯 Best Stocks in Hot Sectors")
            
            # Get stocks from top 3 hot sectors
            hot_stocks = []
            for sector in hot_sectors[:3]:
                hot_stocks.extend(sector["stocks"])
            hot_stocks = list(set(hot_stocks))  # Dedupe
            
            progress_bar = st.progress(0)
            status_text = st.empty()
            
            hot_results = []
            total = len(hot_stocks)
            
            for i, ticker in enumerate(hot_stocks):
                status_text.text(f"Scanning {ticker}... ({i+1}/{total})")
                progress_bar.progress((i + 1) / total)
                
                result = scan_single_stock(ticker, api_key, api_secret, data_source, use_fib, fib_tol, use_strategy)
                if result:
                    hot_results.append(result)
            
            progress_bar.empty()
            status_text.empty()
            
            # Filter for good setups
            bullish_hot = [r for r in hot_results if r["verdict"] == "BULLISH" and r["confidence"] == "HIGH"]
            bullish_hot.sort(key=lambda x: x["score"], reverse=True)
            bullish_hot = bullish_hot[:5]
            
            bearish_hot = [r for r in hot_results if r["verdict"] == "BEARISH" and r["confidence"] == "HIGH"]
            bearish_hot.sort(key=lambda x: x["score"])
            bearish_hot = bearish_hot[:5]
            
            col_b, col_s = st.columns(2)
            
            with col_b:
                st.markdown(
                    '<div style="background:#00e5a015;border:1px solid #00e5a040;padding:8px;border-radius:8px;margin-bottom:8px">'
                    '<div style="font-size:12px;font-weight:bold;color:#00e5a0">🚀 BULLISH in Hot Sectors</div>'
                    '</div>',
                    unsafe_allow_html=True
                )
                
                if bullish_hot:
                    for rank, r in enumerate(bullish_hot, 1):
                        stop_html = f'<span style="color:#ff4d6a">${r["stop_loss"]}</span>' if r.get("stop_loss") else "N/A"
                        t1_html = f'<span style="color:#00e5a0">${r["target1"]}</span>' if r.get("target1") else "N/A"
                        t1_time = f'<span style="color:#4d9fff">~{r["t1_days"]}td</span>' if r.get("t1_days") else ""
                        val_color = r.get("valuation_color", "#6b7099")
                        
                        st.markdown(
                            f'<div style="background:#0d0f17;border:1px solid #1a1d2e;padding:8px;border-radius:4px;margin-bottom:4px">'
                            f'<div style="display:flex;justify-content:space-between;align-items:center">'
                            f'<span style="font-size:13px;font-weight:bold;color:#e8ecff">#{rank} {r["ticker"]}</span>'
                            f'<span style="font-size:10px;color:#00e5a0">+{r["score"]}</span>'
                            f'</div>'
                            f'<div style="font-size:9px;color:#6b7099;margin-top:3px">'
                            f'${r["price"]} · Stop: {stop_html} · T1: {t1_html} {t1_time}'
                            f'</div>'
                            f'<div style="font-size:9px;margin-top:2px">'
                            f'<span style="color:{val_color}">{r.get("valuation", "N/A")}</span> · {r.get("market_cap", "N/A")}'
                            f'</div>'
                            f'</div>',
                            unsafe_allow_html=True
                        )
                else:
                    st.info("No bullish setups in hot sectors.")
            
            with col_s:
                st.markdown(
                    '<div style="background:#ff4d6a15;border:1px solid #ff4d6a40;padding:8px;border-radius:8px;margin-bottom:8px">'
                    '<div style="font-size:12px;font-weight:bold;color:#ff4d6a">📉 BEARISH in Hot Sectors</div>'
                    '</div>',
                    unsafe_allow_html=True
                )
                
                if bearish_hot:
                    for rank, r in enumerate(bearish_hot, 1):
                        stop_html = f'<span style="color:#ff4d6a">${r["stop_loss"]}</span>' if r.get("stop_loss") else "N/A"
                        t1_html = f'<span style="color:#00e5a0">${r["target1"]}</span>' if r.get("target1") else "N/A"
                        t1_time = f'<span style="color:#4d9fff">~{r["t1_days"]}td</span>' if r.get("t1_days") else ""
                        val_color = r.get("valuation_color", "#6b7099")
                        
                        st.markdown(
                            f'<div style="background:#0d0f17;border:1px solid #1a1d2e;padding:8px;border-radius:4px;margin-bottom:4px">'
                            f'<div style="display:flex;justify-content:space-between;align-items:center">'
                            f'<span style="font-size:13px;font-weight:bold;color:#e8ecff">#{rank} {r["ticker"]}</span>'
                            f'<span style="font-size:10px;color:#ff4d6a">{r["score"]}</span>'
                            f'</div>'
                            f'<div style="font-size:9px;color:#6b7099;margin-top:3px">'
                            f'${r["price"]} · Stop: {stop_html} · T1: {t1_html} {t1_time}'
                            f'</div>'
                            f'<div style="font-size:9px;margin-top:2px">'
                            f'<span style="color:{val_color}">{r.get("valuation", "N/A")}</span> · {r.get("market_cap", "N/A")}'
                            f'</div>'
                            f'</div>',
                            unsafe_allow_html=True
                        )
                else:
                    st.info("No bearish setups in hot sectors.")
            
            st.markdown(
                f'<div style="font-size:10px;color:#6b7099;margin-top:12px;text-align:center">'
                f'Scanned {len(hot_stocks)} stocks from top 3 hot sectors'
                f'</div>',
                unsafe_allow_html=True
            )
    else:
        # Fallback: scan all sector stocks without performance data
        st.warning("Could not fetch sector ETF data. Scanning all sector stocks instead...")
        
        # Get all unique stocks from all sectors
        all_sector_stocks = []
        for etf, info in SECTOR_ETFS.items():
            all_sector_stocks.extend(info["stocks"])
        all_sector_stocks = list(set(all_sector_stocks))
        
        progress_bar = st.progress(0)
        status_text = st.empty()
        
        all_results = []
        total = len(all_sector_stocks)
        
        for i, ticker in enumerate(all_sector_stocks):
            status_text.text(f"Scanning {ticker}... ({i+1}/{total})")
            progress_bar.progress((i + 1) / total)
            
            result = scan_single_stock(ticker, api_key, api_secret, data_source, use_fib, fib_tol, use_strategy)
            if result:
                all_results.append(result)
        
        progress_bar.empty()
        status_text.empty()
        
        # Filter for good setups
        top_bullish = [r for r in all_results if r["verdict"] == "BULLISH" and r["confidence"] == "HIGH"]
        top_bullish.sort(key=lambda x: x["score"], reverse=True)
        top_bullish = top_bullish[:5]
        
        top_bearish = [r for r in all_results if r["verdict"] == "BEARISH" and r["confidence"] == "HIGH"]
        top_bearish.sort(key=lambda x: x["score"])
        top_bearish = top_bearish[:5]
        
        col_b, col_s = st.columns(2)
        
        with col_b:
            st.markdown(
                '<div style="background:#00e5a015;border:1px solid #00e5a040;padding:12px;border-radius:8px;margin-bottom:12px">'
                '<div style="font-size:14px;font-weight:bold;color:#00e5a0;margin-bottom:4px">🚀 TOP BULLISH (Sector Stocks)</div>'
                '<div style="font-size:10px;color:#6b7099">High confidence longs from all sectors</div>'
                '</div>',
                unsafe_allow_html=True
            )
            
            if top_bullish:
                for rank, r in enumerate(top_bullish, 1):
                    stop_html = f'<span style="color:#ff4d6a">${r["stop_loss"]}</span>' if r.get("stop_loss") else "N/A"
                    t1_html = f'<span style="color:#00e5a0">${r["target1"]}</span>' if r.get("target1") else "N/A"
                    t1_time = f'<span style="color:#4d9fff">~{r["t1_days"]}td</span>' if r.get("t1_days") else ""
                    val_color = r.get("valuation_color", "#6b7099")
                    
                    st.markdown(
                        f'<div style="background:#0d0f17;border:1px solid #1a1d2e;padding:10px;border-radius:4px;margin-bottom:6px">'
                        f'<div style="display:flex;justify-content:space-between;align-items:center">'
                        f'<span style="font-size:14px;font-weight:bold;color:#e8ecff">#{rank} {r["ticker"]}</span>'
                        f'<span style="font-size:11px;color:#00e5a0">+{r["score"]}</span>'
                        f'</div>'
                        f'<div style="font-size:10px;color:#6b7099;margin-top:4px">'
                        f'${r["price"]} · Stop: {stop_html} · T1: {t1_html} {t1_time}'
                        f'</div>'
                        f'<div style="font-size:9px;margin-top:3px">'
                        f'<span style="color:{val_color}">{r.get("valuation", "N/A")}</span> · {r.get("market_cap", "N/A")}'
                        f'</div>'
                        f'</div>',
                        unsafe_allow_html=True
                    )
            else:
                st.info("No bullish setups found.")
        
        with col_s:
            st.markdown(
                '<div style="background:#ff4d6a15;border:1px solid #ff4d6a40;padding:12px;border-radius:8px;margin-bottom:12px">'
                '<div style="font-size:14px;font-weight:bold;color:#ff4d6a;margin-bottom:4px">📉 TOP BEARISH (Sector Stocks)</div>'
                '<div style="font-size:10px;color:#6b7099">High confidence shorts from all sectors</div>'
                '</div>',
                unsafe_allow_html=True
            )
            
            if top_bearish:
                for rank, r in enumerate(top_bearish, 1):
                    stop_html = f'<span style="color:#ff4d6a">${r["stop_loss"]}</span>' if r.get("stop_loss") else "N/A"
                    t1_html = f'<span style="color:#00e5a0">${r["target1"]}</span>' if r.get("target1") else "N/A"
                    t1_time = f'<span style="color:#4d9fff">~{r["t1_days"]}td</span>' if r.get("t1_days") else ""
                    val_color = r.get("valuation_color", "#6b7099")
                    
                    st.markdown(
                        f'<div style="background:#0d0f17;border:1px solid #1a1d2e;padding:10px;border-radius:4px;margin-bottom:6px">'
                        f'<div style="display:flex;justify-content:space-between;align-items:center">'
                        f'<span style="font-size:14px;font-weight:bold;color:#e8ecff">#{rank} {r["ticker"]}</span>'
                        f'<span style="font-size:11px;color:#ff4d6a">{r["score"]}</span>'
                        f'</div>'
                        f'<div style="font-size:10px;color:#6b7099;margin-top:4px">'
                        f'${r["price"]} · Stop: {stop_html} · T1: {t1_html} {t1_time}'
                        f'</div>'
                        f'<div style="font-size:9px;margin-top:3px">'
                        f'<span style="color:{val_color}">{r.get("valuation", "N/A")}</span> · {r.get("market_cap", "N/A")}'
                        f'</div>'
                        f'</div>',
                        unsafe_allow_html=True
                    )
            else:
                st.info("No bearish setups found.")
        
        st.markdown(
            f'<div style="font-size:10px;color:#6b7099;margin-top:12px;text-align:center">'
            f'Scanned {len(all_sector_stocks)} stocks from all sectors'
            f'</div>',
            unsafe_allow_html=True
        )
    

# ── Macro Dashboard ──────────────────────────────────
with tab_macro:
    st.markdown(
        '<div style="background:linear-gradient(135deg,#0a0b14,#131625);border:1px solid #1a1d2e;'
        'border-radius:8px;padding:18px 24px;margin-bottom:16px">'
        '<div style="display:flex;align-items:center;gap:12px">'
        '<div style="font-size:28px">🌍</div>'
        '<div>'
        '<div style="font-size:16px;font-weight:700;color:#e8ecff">Macro Dashboard</div>'
        '<div style="font-size:10px;color:#6b7099">Live market context — indices, volatility, commodities, bonds. '
        'Refreshes every 5 minutes. Requires yfinance.</div>'
        '</div></div></div>',
        unsafe_allow_html=True,
    )
    if st.button("🔄 Refresh Macro Data", use_container_width=True, key="btn_macro_refresh"):
        get_macro_snapshot.clear()
        get_sector_performance.clear()

    with st.spinner("Fetching macro data..."):
        macro_data = get_macro_snapshot()

    # Sector strength — prefer scan-derived (more granular), fall back to ETF momentum
    last_scan = st.session_state.get("_last_scan_results", [])
    scan_sector_str = _sector_strength_from_scan(last_scan) if last_scan else None

    etf_perf = None
    if not scan_sector_str:
        with st.spinner("Fetching sector ETF data..."):
            try:
                etf_perf = get_sector_performance(api_key, api_secret, data_source)
            except Exception:
                etf_perf = None

    _render_macro_dashboard(macro_data, sector_str=scan_sector_str, etf_sector_perf=etf_perf)

    if last_scan:
        st.caption(f"Sector strength sourced from last Stock Analysis scan ({len(last_scan)} tickers). Re-run scan to update.")


# ── Fetch earnings data ──────────────────────────────
end_date   = date.today()
start_date = end_date - timedelta(days=365 * years + 60)

if fetch_btn:
  with tab_fetch:
    source_name = "Alpaca" if data_source == "Alpaca" else "Polygon"
    for _sym_idx, symbol in enumerate(symbols_list):
      st.markdown(f"---\n### 📊 {symbol}" if _sym_idx > 0 else "")
      with st.spinner(f"Fetching {symbol} data from {source_name}…"):
        status = st.empty()

        status.info(f"📈 Fetching daily price bars for {symbol}…")
        try:
            if data_source == "Alpaca":
                daily_df = get_daily_bars_alpaca(symbol, str(start_date - timedelta(days=90)), str(end_date), api_key, api_secret)
            else:
                daily_df = get_daily_bars(symbol, str(start_date - timedelta(days=90)), str(end_date), api_key)
        except Exception as e:
            status.empty()
            st.error(f"**{source_name} API Error**\n\n{e}")
            if data_source == "Alpaca":
                st.markdown(
                    '<div style="background:#0d0f17;border:1px solid #1a1d2e;padding:14px;border-radius:4px;font-size:10px;color:#6b7099;line-height:2">'
                    '<b style="color:#e8ecff">Troubleshooting:</b><br>'
                    '• Check your Alpaca API Key and Secret at app.alpaca.markets<br>'
                    '• Make sure you\'re using keys from your paper trading account<br>'
                    '• Ticker must be a valid US stock ticker (e.g. TSLA, AAPL, HPE, GOOG)'
                    '</div>',
                    unsafe_allow_html=True,
                )
            else:
                st.markdown(
                    '<div style="background:#0d0f17;border:1px solid #1a1d2e;padding:14px;border-radius:4px;font-size:10px;color:#6b7099;line-height:2">'
                    '<b style="color:#e8ecff">Troubleshooting:</b><br>'
                    '• Make sure your Polygon API key is correct (check polygon.io/dashboard)<br>'
                    '• Free tier supports: <code>daily bars</code>, <code>hourly bars</code> — it does NOT support <code>/vX/reference/financials</code><br>'
                    '• Ticker must be a valid US stock ticker (e.g. TSLA, AAPL, HPE, GOOG)<br>'
                    '• If you see 403: the key is wrong or expired'
                    '</div>',
                    unsafe_allow_html=True,
                )
            continue

        if daily_df.empty:
            st.error(f"No daily price data found for **{symbol}**. Check that the ticker is a valid US stock.")
            continue

        status.info("📅 Resolving earnings dates…")
        # Use Polygon key for earnings dates (even when using Alpaca for price data)
        earnings_key = polygon_key if data_source == "Alpaca" else api_key
        earnings_events, earn_source = get_earnings_dates(
            symbol, earnings_key,
            limit=years * 5 + 4,
            daily_df=daily_df,
            manual_dates=manual_dates_list if manual_dates_list else None,
        )
        next_earn = estimate_next_earnings(earnings_events) if earnings_events else None

        if not earnings_events:
            status.empty()
            st.error(
                f"Could not find earnings dates for **{symbol}**. "
                f"Try pasting known dates manually in the sidebar (YYYY-MM-DD, one per line)."
            )
            continue

        # Filter to requested date range
        cutoff = str(start_date)
        earnings_events = [(d, l, p) for d, l, p in earnings_events if d >= cutoff]

        if not earnings_events:
            status.empty()
            st.warning(f"No earnings events for **{symbol}** within the selected date range.")
            continue

        # Fetch hourly bars for 4H candle analysis
        status.info("⏱ Fetching hourly bars for 4H candle analysis…")
        hourly_start = end_date - timedelta(days=14)  # Last 14 days for display
        hourly_data_delay = None
        hourly_source_used = None
        hourly_df = pd.DataFrame()  # Initialize empty
        try:
            # Try yfinance first (consolidated data, matches TOS/ThinkorSwim)
            if YFINANCE_AVAILABLE:
                hourly_df = get_hourly_bars_yfinance(symbol, str(hourly_start), str(end_date))
                if not hourly_df.empty:
                    hourly_source_used = "Yahoo Finance"
                    print(f"[HOURLY] {symbol}: Using Yahoo Finance ({len(hourly_df)} bars)")
                else:
                    print(f"[HOURLY] {symbol}: yfinance returned empty, trying fallback")
            
            # Fallback to Alpaca/Polygon if yfinance failed
            if hourly_df is None or hourly_df.empty:
                if data_source == "Alpaca":
                    hourly_df = get_hourly_bars_alpaca(symbol, str(hourly_start), str(end_date), api_key, api_secret)
                else:
                    hourly_df = get_hourly_bars(symbol, str(hourly_start), str(end_date), api_key)
                if not hourly_df.empty:
                    hourly_source_used = f"{source_name} (IEX)"
            
            if hourly_df.empty:
                st.sidebar.warning(f"⚠️ No hourly data returned")
            else:
                # Show what data we actually got
                actual_start = hourly_df.index.min()
                actual_end = hourly_df.index.max()
                hourly_data_delay = (date.today() - actual_end.date()).days
                st.sidebar.info(f"📊 Hourly data: {actual_start.date()} to {actual_end.date()} ({hourly_source_used})")
                if hourly_data_delay > 1 and data_source == "Polygon":
                    st.sidebar.warning(f"⚠️ Polygon free tier: hourly data is {hourly_data_delay} days behind")
        except Exception as e:
            st.sidebar.warning(f"⚠️ Hourly fetch error: {str(e)[:60]}")
            hourly_df = pd.DataFrame()
        
        status.empty()

        # Store in session state
        st.session_state.fetched_data = {
            "symbol": symbol,
            "daily_df": daily_df,
            "hourly_df": hourly_df,
            "earnings_events": earnings_events,
            "earn_source": earn_source,
            "next_earn": next_earn,
            "start_date": start_date,
            "end_date": end_date,
        }

      # Display earnings and Fibonacci analysis
      st.success(f"✅ Found {len(earnings_events)} earnings events for **{symbol}**")
    
      # Source badge
      source_colors = {"manual": "#00e5a0", "polygon": "#4d9fff", "auto-detected": "#f5c842", "none": "#ff4d6a"}
      source_labels = {
          "manual":       "✏️ manual input",
          "polygon":      "🔷 Polygon financials",
          "auto-detected":"⚡ auto-detected from price gaps",
          "none":         "❌ none",
      }
      st.markdown(
          f'<div style="font-size:10px;color:{source_colors.get(earn_source,"#6b7099")};margin-bottom:12px">'
          f'Source: {source_labels.get(earn_source, earn_source)}</div>',
          unsafe_allow_html=True,
      )
    
      # ══════════════════════════════════════════════════════════════════════════════
      # COMPUTE ALL SIGNALS FIRST FOR VERDICT
      # ══════════════════════════════════════════════════════════════════════════════
    
      # ── Current Price & Fibonacci Analysis ──
      latest_close = float(daily_df["close"].iloc[-1])
      latest_date = daily_df.index[-1]
    
      # Calculate swing high/low from last earnings to now (or 90 days if no recent earnings)
      if len(earnings_events) >= 1:
          last_earn_date = datetime.strptime(earnings_events[-1][0], "%Y-%m-%d").date()
          swing_start = last_earn_date
      else:
          swing_start = latest_date - timedelta(days=90)
    
      swing_data = daily_df[daily_df.index >= swing_start]
      if len(swing_data) < 5:
          swing_data = daily_df.tail(90)  # fallback
    
      swing_hi = float(swing_data["high"].max())
      swing_lo = float(swing_data["low"].min())
      swing_range = swing_hi - swing_lo
    
      # Calculate Fibonacci levels
      fib_levels = calc_fib_levels(swing_lo, swing_hi)
    
      # Find nearest Fib level and bias
      nearest = nearest_fib(latest_close, swing_lo, swing_hi, 100)  # 100% tolerance to always find nearest
      if nearest:
          nearest_name, nearest_price, nearest_dist = nearest
      else:
          nearest_name, nearest_price, nearest_dist = "N/A", 0, 0
    
      # Determine bias based on price position
      price_position = (latest_close - swing_lo) / swing_range if swing_range > 0 else 0.5
      if price_position >= 0.618:
          fib_bias = "BULLISH"
          bias_color = "#00e5a0"
          bias_desc = "Price above 61.8% retracement - bullish structure"
      elif price_position >= 0.382:
          fib_bias = "NEUTRAL"
          bias_color = "#f5c842"
          bias_desc = "Price in consolidation zone (38.2%-61.8%)"
      else:
          fib_bias = "BEARISH"
          bias_color = "#ff4d6a"
          bias_desc = "Price below 38.2% retracement - bearish structure"
    
      # Display current price panel
      st.markdown("---")
      st.markdown("### 📊 Current Analysis")
    
      # ── 4H Candle Bias ──
      hourly_df = st.session_state.fetched_data.get("hourly_df", pd.DataFrame())
    
      # Check current time in ET to determine if 4H candle is complete
      from zoneinfo import ZoneInfo
      now_et = datetime.now(ZoneInfo("America/New_York"))
      today = date.today()
    
      # 4H candle completes at 1:00 PM ET (13:00)
      candle_complete_time = now_et.replace(hour=13, minute=0, second=0, microsecond=0)
      is_after_candle_close = now_et >= candle_complete_time
      is_market_day = now_et.weekday() < 5  # Mon-Fri
    
      # Try today's candle first
      today_4h = get_4h_noon_candle(symbol, today, hourly_df)
    
      # If today's candle exists and market time is after 1:30 PM ET, use it
      # Otherwise, try to get the last trading day's candle
      if today_4h and is_after_candle_close:
          candle_4h = today_4h
          candle_date = today
          candle_status = "COMPLETE"
      elif today_4h and not is_after_candle_close:
          # Today's candle is still forming - show partial but mark as in-progress
          candle_4h = today_4h
          candle_date = today
          candle_status = "IN PROGRESS"
      else:
          # Try yesterday or last trading day
          candle_4h = None
          candle_date = None
          checked_dates = []
          # Look back up to 10 days for last trading day (handles holidays/weekends)
          for days_back in range(1, 11):
              check_date = today - timedelta(days=days_back)
              past_candle = get_4h_noon_candle(symbol, check_date, hourly_df)
              checked_dates.append((check_date, "found" if past_candle else "no data"))
              if past_candle:
                  candle_4h = past_candle
                  candle_date = check_date
                  candle_status = "LAST SESSION"
                  break
        
          # Store checked dates for debug
          if not candle_4h:
              st.session_state["_4h_debug_dates"] = checked_dates
    
      if candle_4h:
          candle_change = (candle_4h["close"] - candle_4h["open"]) / candle_4h["open"] * 100
          print(f"[4H CANDLE] {symbol} date={candle_date} status={candle_status} O={candle_4h['open']:.2f} C={candle_4h['close']:.2f} change={candle_change:+.2f}% bars={candle_4h.get('bars','?')}")
          if candle_4h["close"] > candle_4h["open"]:
              candle_4h_bias = "BULLISH"
              candle_4h_color = "#00e5a0"
              candle_4h_desc = f"Green candle (+{candle_change:.2f}%)"
              candle_4h_signal = "LONG"
          else:
              candle_4h_bias = "BEARISH"
              candle_4h_color = "#ff4d6a"
              candle_4h_desc = f"Red candle ({candle_change:.2f}%)"
              candle_4h_signal = "SHORT"
        
          # Add status to description
          if candle_status == "IN PROGRESS":
              candle_4h_desc = f"⏳ {candle_4h_desc} (forming)"
              candle_4h_signal = f"{candle_4h_signal} (tentative)"
          elif candle_status == "LAST SESSION":
              candle_4h_desc = f"📅 {candle_4h_desc} ({candle_date})"
      else:
          candle_4h_bias = "NO DATA"
          candle_4h_color = "#6b7099"
          candle_4h_desc = "No 4H candle data available"
          candle_4h_signal = "N/A"
          candle_status = "UNAVAILABLE"
          candle_date = None
    
      # ══════════════════════════════════════════════════════════════════════════════
      # FETCH OPTIONS DATA FOR VERDICT
      # ══════════════════════════════════════════════════════════════════════════════
      with st.spinner("Analyzing options flow..."):
          # Try yfinance first (free, no API key needed), fall back to Alpaca/Polygon
          options_data = None
          if YFINANCE_AVAILABLE:
              options_data = get_options_bias_yfinance(symbol)
              if options_data and "error" in options_data:
                  options_data = None  # yfinance failed, try API fallback
          if options_data is None:
              if data_source == "Alpaca":
                  options_data = get_options_bias_alpaca(symbol, api_key, api_secret)
              elif api_key:
                  options_data = get_options_bias(symbol, api_key)
    
      # Extract options sentiment
      if options_data and "error" not in options_data:
          options_oi_bias = options_data.get("sentiment", "NEUTRAL")
          options_vol_bias = options_data.get("vol_sentiment", "N/A")
      else:
          options_oi_bias = "N/A"
          options_vol_bias = "N/A"
    
      # ══════════════════════════════════════════════════════════════════════════════
      # CALCULATE FINAL VERDICT
      # ══════════════════════════════════════════════════════════════════════════════
    
      # Score each signal: BULLISH = +1, NEUTRAL = 0, BEARISH = -1, N/A = skip
      signals = []
      signal_details = []
    
      # 4H Candle (highest weight - this is the primary entry signal)
      if candle_4h_bias == "BULLISH":
          signals.append(2)  # Double weight for 4H
          signal_details.append(("4H Candle", "BULLISH", "#00e5a0", "🟢"))
      elif candle_4h_bias == "BEARISH":
          signals.append(-2)
          signal_details.append(("4H Candle", "BEARISH", "#ff4d6a", "🔴"))
      else:
          signal_details.append(("4H Candle", "N/A", "#6b7099", "⚪"))
    
      # Fibonacci Bias
      if fib_bias == "BULLISH":
          signals.append(1)
          signal_details.append(("Fibonacci", "BULLISH", "#00e5a0", "🟢"))
      elif fib_bias == "BEARISH":
          signals.append(-1)
          signal_details.append(("Fibonacci", "BEARISH", "#ff4d6a", "🔴"))
      else:
          signals.append(0)
          signal_details.append(("Fibonacci", "NEUTRAL", "#f5c842", "🟡"))
    
      # Options OI Bias
      if options_oi_bias == "BULLISH":
          signals.append(1)
          signal_details.append(("Options OI", "BULLISH", "#00e5a0", "🟢"))
      elif options_oi_bias == "BEARISH":
          signals.append(-1)
          signal_details.append(("Options OI", "BEARISH", "#ff4d6a", "🔴"))
      elif options_oi_bias == "NEUTRAL":
          signals.append(0)
          signal_details.append(("Options OI", "NEUTRAL", "#f5c842", "🟡"))
      else:
          signal_details.append(("Options OI", "N/A", "#6b7099", "⚪"))
    
      # Options Volume Bias
      if options_vol_bias == "BULLISH":
          signals.append(1)
          signal_details.append(("Options Vol", "BULLISH", "#00e5a0", "🟢"))
      elif options_vol_bias == "BEARISH":
          signals.append(-1)
          signal_details.append(("Options Vol", "BEARISH", "#ff4d6a", "🔴"))
      elif options_vol_bias == "NEUTRAL":
          signals.append(0)
          signal_details.append(("Options Vol", "NEUTRAL", "#f5c842", "🟡"))
      else:
          signal_details.append(("Options Vol", "N/A", "#6b7099", "⚪"))
    
      # Options Delta-Adjusted Bias
      options_delta_bias = "N/A"
      if options_data and "error" not in options_data:
          options_delta_bias = options_data.get("delta_sentiment", "N/A")
      if options_delta_bias == "BULLISH":
          signals.append(1)
          signal_details.append(("Options Δ", "BULLISH", "#00e5a0", "🟢"))
      elif options_delta_bias == "BEARISH":
          signals.append(-1)
          signal_details.append(("Options Δ", "BEARISH", "#ff4d6a", "🔴"))
      elif options_delta_bias == "NEUTRAL":
          signals.append(0)
          signal_details.append(("Options Δ", "NEUTRAL", "#f5c842", "🟡"))
      else:
          signal_details.append(("Options Δ", "N/A", "#6b7099", "⚪"))

      # Strategy Signal (Fib + Weinstein + Bias) - optional
      if use_strategy:
          strategy_data = analyze_strategy_signals(daily_df)
          if strategy_data and "error" not in strategy_data:
              if strategy_data.get("short_signal"):
                  signals.append(-2)  # Double weight for full strategy signal
                  signal_details.append(("Strategy", "SHORT", "#ff4d6a", "🔴"))
              elif strategy_data.get("long_signal"):
                  signals.append(2)
                  signal_details.append(("Strategy", "LONG", "#00e5a0", "🟢"))
              else:
                  signal_details.append(("Strategy", "NEUTRAL", "#f5c842", "🟡"))
          else:
              signal_details.append(("Strategy", "N/A", "#6b7099", "⚪"))
      else:
          strategy_data = None
    
      # Calculate final verdict
      if signals:
          score = sum(signals)
          max_score = len(signals) + 1  # +1 for extra weight on 4H
        
          if score >= 2:
              final_verdict = "BULLISH"
              verdict_color = "#00e5a0"
              verdict_emoji = "🚀"
              verdict_action = "LONG"
          elif score <= -2:
              final_verdict = "BEARISH"
              verdict_color = "#ff4d6a"
              verdict_emoji = "📉"
              verdict_action = "SHORT"
          elif score > 0:
              final_verdict = "LEAN BULLISH"
              verdict_color = "#7ed4a0"
              verdict_emoji = "📈"
              verdict_action = "LONG (cautious)"
          elif score < 0:
              final_verdict = "LEAN BEARISH"
              verdict_color = "#ff8a9f"
              verdict_emoji = "📉"
              verdict_action = "SHORT (cautious)"
          else:
              final_verdict = "NEUTRAL"
              verdict_color = "#f5c842"
              verdict_emoji = "⚖️"
              verdict_action = "WAIT / NO TRADE"
        
          # ── Calculate Confidence Level ──
          # Check signal alignment
          bullish_count = sum(1 for s in signals if s > 0)
          bearish_count = sum(1 for s in signals if s < 0)
          neutral_count = sum(1 for s in signals if s == 0)
          total_signals = len(signals)
        
          # Determine direction from 4H candle (primary signal)
          candle_direction = "BULL" if candle_4h_bias == "BULLISH" else ("BEAR" if candle_4h_bias == "BEARISH" else "NONE")
        
          # Check if other signals align with 4H candle direction
          if candle_direction == "BULL":
              # Count signals that align with bullish (bullish or neutral counts as aligned)
              aligned = bullish_count + neutral_count
              divergent = bearish_count
          elif candle_direction == "BEAR":
              aligned = bearish_count + neutral_count
              divergent = bullish_count
          else:
              aligned = neutral_count
              divergent = 0
        
          # Check specific divergences
          fib_diverges = (candle_direction == "BULL" and fib_bias == "BEARISH") or \
                         (candle_direction == "BEAR" and fib_bias == "BULLISH")
          options_oi_diverges = (candle_direction == "BULL" and options_oi_bias == "BEARISH") or \
                                (candle_direction == "BEAR" and options_oi_bias == "BULLISH")
        
          # Calculate confidence
          if candle_4h_bias in ["BULLISH", "BEARISH"]:
              if divergent == 0:
                  confidence = "HIGH"
                  confidence_color = "#00e5a0"
                  confidence_emoji = "🎯"
                  confidence_desc = "All signals aligned"
              elif divergent == 1:
                  confidence = "MEDIUM"
                  confidence_color = "#f5c842"
                  confidence_emoji = "⚡"
                  if fib_diverges:
                      confidence_desc = "Fib diverges - potential reversal play"
                  elif options_oi_diverges:
                      confidence_desc = "Options OI diverges - watch positioning"
                  else:
                      confidence_desc = "Minor divergence in signals"
              else:
                  confidence = "LOW"
                  confidence_color = "#ff4d6a"
                  confidence_emoji = "⚠️"
                  confidence_desc = f"{divergent} signals diverge - high risk"
          else:
              confidence = "N/A"
              confidence_color = "#6b7099"
              confidence_emoji = "❓"
              confidence_desc = "No 4H candle signal"
        
          # Special case: reversal setup
          is_reversal = fib_diverges and candle_4h_bias in ["BULLISH", "BEARISH"]
          if is_reversal:
              reversal_note = "⚡ REVERSAL SETUP" if candle_direction == "BULL" else "⚡ REVERSAL SHORT"
          else:
              reversal_note = None
            
      else:
          final_verdict = "INSUFFICIENT DATA"
          verdict_color = "#6b7099"
          verdict_emoji = "❓"
          verdict_action = "NEED MORE DATA"
          score = 0
          confidence = "N/A"
          confidence_color = "#6b7099"
          confidence_emoji = "❓"
          confidence_desc = "Insufficient signals"
          reversal_note = None
    
      # ══════════════════════════════════════════════════════════════════════════════
      # DISPLAY FINAL VERDICT (TOP OF PAGE)
      # ══════════════════════════════════════════════════════════════════════════════
    
      # Build reversal badge if applicable
      reversal_html = ""
      if reversal_note:
          reversal_html = (
              f'<div style="background:#f5c84220;border:1px solid #f5c84260;'
              f'padding:6px 16px;border-radius:20px;display:inline-block;margin-bottom:12px">'
              f'<span style="font-size:12px;color:#f5c842;font-weight:bold">{reversal_note}</span>'
              f'</div><br>'
          )
    
      st.markdown(
          f'<div style="background:linear-gradient(135deg, #0d0f17 0%, #131625 100%);'
          f'border:2px solid {verdict_color};border-radius:12px;padding:24px;margin:20px 0;text-align:center">'
          f'<div style="font-size:48px;margin-bottom:8px">{verdict_emoji}</div>'
          f'<div style="font-size:14px;color:#6b7099;letter-spacing:2px;margin-bottom:4px">FINAL VERDICT</div>'
          f'<div style="font-size:42px;font-weight:bold;color:{verdict_color};margin-bottom:8px">{symbol}</div>'
          f'{reversal_html}'
          f'<div style="font-size:28px;font-weight:bold;color:{verdict_color};margin-bottom:12px">{final_verdict}</div>'
          f'<div style="font-size:16px;color:#e8ecff;margin-bottom:8px">Signal: <b style="color:{verdict_color}">{verdict_action}</b></div>'
          # Confidence badge
          f'<div style="display:inline-block;background:{confidence_color}20;border:1px solid {confidence_color}60;'
          f'padding:8px 20px;border-radius:24px;margin-bottom:16px">'
          f'<span style="font-size:14px">{confidence_emoji}</span> '
          f'<span style="font-size:13px;color:{confidence_color};font-weight:bold">CONFIDENCE: {confidence}</span>'
          f'<div style="font-size:10px;color:#6b7099;margin-top:2px">{confidence_desc}</div>'
          f'</div>'
          f'<div style="display:flex;justify-content:center;gap:16px;flex-wrap:wrap;margin-top:16px">'
          + ''.join([
              f'<div style="background:#0d0f1799;border:1px solid {color}40;padding:8px 16px;border-radius:20px">'
              f'<span style="font-size:12px">{emoji}</span> '
              f'<span style="font-size:11px;color:#6b7099">{name}:</span> '
              f'<span style="font-size:11px;color:{color};font-weight:bold">{bias}</span>'
              f'</div>'
              for name, bias, color, emoji in signal_details
          ])
          + f'</div>'
          f'<div style="font-size:10px;color:#3a3d5c;margin-top:16px">Score: {score} · Higher = more bullish</div>'
          f'</div>',
          unsafe_allow_html=True
      )
    
      col1, col2, col3, col4 = st.columns(4)
      with col1:
          st.metric("Current Price", f"${latest_close:.2f}", f"as of {latest_date}")
      with col2:
          st.metric("Fibonacci Bias", fib_bias, bias_desc[:30])
      with col3:
          candle_time_cst = ""
          if candle_4h and "first_bar_ts" in candle_4h:
              from zoneinfo import ZoneInfo
              first_ts = candle_4h["first_bar_ts"]
              last_ts = candle_4h["last_bar_ts"]
              if hasattr(first_ts, 'tz') and first_ts.tz is not None:
                  first_cst = first_ts.astimezone(ZoneInfo("America/Chicago"))
                  last_cst = last_ts.astimezone(ZoneInfo("America/Chicago"))
              else:
                  # Assume ET, convert to CST (subtract 1 hour)
                  import pytz
                  et = pytz.timezone("America/New_York")
                  first_cst = et.localize(first_ts.to_pydatetime()).astimezone(ZoneInfo("America/Chicago"))
                  last_cst = et.localize(last_ts.to_pydatetime()).astimezone(ZoneInfo("America/Chicago"))
              last_cst_end = last_cst + timedelta(hours=1)  # bar timestamp is start; add 1h for candle close
              bars_count = candle_4h.get("bars", "?")
              candle_time_cst = f"{first_cst.strftime('%m/%d %I:%M %p')} – {last_cst_end.strftime('%I:%M %p')} CST ({bars_count}/4 bars)"
          st.metric("4H Candle Bias", candle_4h_bias, candle_4h_desc[:35])
          if candle_time_cst:
              st.caption(f"🕐 {candle_time_cst}")
          if candle_4h and candle_date is not None:
              st.caption(f"O: {candle_4h['open']:.2f}  H: {candle_4h['high']:.2f}  L: {candle_4h['low']:.2f}  C: {candle_4h['close']:.2f}")
      with col4:
          st.metric("Nearest Fib Level", nearest_name, f"${nearest_price:.2f} ({nearest_dist:.1f}% away)")
    
      # ── Entry / Stop Loss / Targets ──
      recent_low = daily_df["low"].iloc[-10:].min()
      recent_high = daily_df["high"].iloc[-10:].max()
      atr_14 = (daily_df["high"] - daily_df["low"]).rolling(14).mean().iloc[-1]
      avg_daily_move = atr_14 * 0.6

      if final_verdict in ["BULLISH", "LEAN BULLISH"]:
          entry = round(latest_close, 2)
          stop_loss = round(recent_low - atr_14 * 0.5, 2)
          risk = entry - stop_loss
          target1 = round(entry + risk * 2, 2)
          target2 = round(entry + risk * 3, 2)
          risk_pct = round((risk / entry) * 100, 1)
          t1_days = max(1, round((target1 - entry) / avg_daily_move)) if avg_daily_move > 0 else None
          t2_days = max(1, round((target2 - entry) / avg_daily_move)) if avg_daily_move > 0 else None
          setup_dir = "LONG"
          setup_color = "#00e5a0"
      elif final_verdict in ["BEARISH", "LEAN BEARISH"]:
          entry = round(latest_close, 2)
          stop_loss = round(recent_high + atr_14 * 0.5, 2)
          risk = stop_loss - entry
          target1 = round(entry - risk * 2, 2)
          target2 = round(entry - risk * 3, 2)
          risk_pct = round((risk / entry) * 100, 1)
          t1_days = max(1, round((entry - target1) / avg_daily_move)) if avg_daily_move > 0 else None
          t2_days = max(1, round((entry - target2) / avg_daily_move)) if avg_daily_move > 0 else None
          setup_dir = "SHORT"
          setup_color = "#ff4d6a"
      else:
          entry = round(latest_close, 2)
          stop_loss = None
          target1 = None
          target2 = None
          risk_pct = None
          t1_days = None
          t2_days = None
          setup_dir = None
          setup_color = "#6b7099"

      st.markdown("---")
      st.markdown("### 🎯 Entry / Stop Loss / Targets")

      if setup_dir:
          esl_col1, esl_col2, esl_col3, esl_col4 = st.columns(4)
          with esl_col1:
              st.metric("Entry", f"${entry:.2f}", f"{setup_dir}")
          with esl_col2:
              st.metric("Stop Loss", f"${stop_loss:.2f}", f"Risk: {risk_pct}%")
          with esl_col3:
              t1_time = f"~{t1_days} trading days" if t1_days else ""
              st.metric("Target 1 (2:1)", f"${target1:.2f}", t1_time)
          with esl_col4:
              t2_time = f"~{t2_days} trading days" if t2_days else ""
              st.metric("Target 2 (3:1)", f"${target2:.2f}", t2_time)

          st.markdown(
              f'<div style="background:#0d0f1799;border:1px solid {setup_color}40;'
              f'padding:12px;border-radius:4px;margin-top:8px">'
              f'<div style="font-size:10px;color:#6b7099">TRADE LEVELS</div>'
              f'<div style="font-size:12px;color:{setup_color};font-weight:bold;margin-top:4px">'
              f'{setup_dir} Setup · Entry ${entry:.2f} · Stop ${stop_loss:.2f} · '
              f'T1 ${target1:.2f} · T2 ${target2:.2f}</div>'
              f'<div style="font-size:10px;color:#6b7099;margin-top:4px">'
              f'Risk/Reward: 1:{2 if target1 else "?"} / 1:{3 if target2 else "?"} · '
              f'Risk: {risk_pct}% · ATR(14): ${atr_14:.2f}</div>'
              f'</div>',
              unsafe_allow_html=True
          )

          # ── Track Trade Button ──
          # Store trade data in session state so the save can work across reruns
          track_key = f"track_{symbol}_{_sym_idx}"
          _trade_payload = {
              "ticker": symbol, "direction": setup_dir, "entry_price": entry,
              "stop_loss": stop_loss, "target1": target1, "target2": target2,
              "verdict": final_verdict, "confidence": confidence, "score": score,
              "signals": ", ".join(f"{nm}: {val}" for nm, val, _, _ in signal_details),
              "t1_days": t1_days, "t2_days": t2_days,
          }
          st.session_state[f"_trade_data_{track_key}"] = _trade_payload

          track_notes = st.text_input("Trade notes (optional)", key=f"notes_{track_key}",
                                      placeholder="e.g. Earnings play, breakout setup…")

          def _save_tracked_trade(tkey):
              """Callback to save trade from session state data."""
              payload = st.session_state.get(f"_trade_data_{tkey}")
              if payload:
                  notes = st.session_state.get(f"notes_{tkey}", "")
                  save_trade(**payload, notes=notes if notes else None)
                  st.session_state[f"_trade_saved_{tkey}"] = True

          st.button(
              f"📌 Track This Trade — {setup_dir} {symbol} @ ${entry:.2f}",
              key=track_key,
              on_click=_save_tracked_trade,
              args=(track_key,),
          )
          if st.session_state.get(f"_trade_saved_{track_key}"):
              st.success(f"✅ Trade saved! {setup_dir} {symbol} @ ${entry:.2f} · "
                        f"Stop ${stop_loss:.2f} · T1 ${target1:.2f} · T2 ${target2:.2f}")
              del st.session_state[f"_trade_saved_{track_key}"]
      else:
          st.info("No directional signal — Entry/Stop/Target levels require a BULLISH or BEARISH verdict.")

      # ══════════════════════════════════════════════════════════════════════════════
      # SUPPORT / RESISTANCE LEVELS
      # ══════════════════════════════════════════════════════════════════════════════
      st.markdown("---")
      st.markdown("### 🏗️ Support & Resistance Levels")
      sr_levels = calc_support_resistance(daily_df)
      if sr_levels:
          sr_col1, sr_col2 = st.columns(2)

          with sr_col1:
              st.markdown('<div style="font-size:10px;color:#ff4d6a;font-weight:700;margin-bottom:6px">▲ RESISTANCE</div>',
                         unsafe_allow_html=True)
              for i, rlev in enumerate(sr_levels.get("resistances", [])[:5]):
                  rpx = rlev["price"]
                  rdist = abs(rpx - latest_close) / latest_close * 100
                  stars = "★" * rlev["strength"] + "☆" * max(0, 3 - rlev["strength"])
                  bar_color = "#ff4d6a" if i == 0 else "#ff4d6a80"
                  st.markdown(
                      f'<div style="background:#0d0f17;border-left:3px solid {bar_color};'
                      f'padding:8px 12px;border-radius:0 4px 4px 0;margin-bottom:4px">'
                      f'<div style="display:flex;justify-content:space-between;align-items:center">'
                      f'<span style="color:#ff4d6a;font-weight:700;font-size:13px">${rpx:.2f}</span>'
                      f'<span style="color:#6b7099;font-size:10px">+{rdist:.1f}% away · {stars}</span>'
                      f'</div></div>', unsafe_allow_html=True)

          with sr_col2:
              st.markdown('<div style="font-size:10px;color:#00e5a0;font-weight:700;margin-bottom:6px">▼ SUPPORT</div>',
                         unsafe_allow_html=True)
              for i, slev in enumerate(sr_levels.get("supports", [])[:5]):
                  spx = slev["price"]
                  sdist = abs(latest_close - spx) / latest_close * 100
                  stars = "★" * slev["strength"] + "☆" * max(0, 3 - slev["strength"])
                  bar_color = "#00e5a0" if i == 0 else "#00e5a080"
                  st.markdown(
                      f'<div style="background:#0d0f17;border-left:3px solid {bar_color};'
                      f'padding:8px 12px;border-radius:0 4px 4px 0;margin-bottom:4px">'
                      f'<div style="display:flex;justify-content:space-between;align-items:center">'
                      f'<span style="color:#00e5a0;font-weight:700;font-size:13px">${spx:.2f}</span>'
                      f'<span style="color:#6b7099;font-size:10px">-{sdist:.1f}% away · {stars}</span>'
                      f'</div></div>', unsafe_allow_html=True)

          # Pivot & key level summary
          pivot_px = sr_levels.get("pivot")
          key_lev = sr_levels.get("key_level")
          pivot_str = f"Pivot: ${pivot_px:.2f}" if pivot_px else ""
          key_str = ""
          if key_lev:
              kp = key_lev["price"]
              ktype = "Support" if kp < latest_close else "Resistance"
              key_str = f" · Key {ktype}: ${kp:.2f} (confluence ×{key_lev['strength']})"
          st.markdown(
              f'<div style="background:#0d0f1799;border:1px solid #1a1d2e;'
              f'padding:10px;border-radius:4px;margin-top:8px;font-size:11px;color:#6b7099">'
              f'📍 {pivot_str}{key_str} · '
              f'Sources: pivot points, swing fractals, volume clusters, round numbers</div>',
              unsafe_allow_html=True)
      else:
          st.info("Insufficient data to calculate support/resistance levels.")

      # ══════════════════════════════════════════════════════════════════════════════
      # FUNDAMENTAL ANALYSIS PANEL
      # ══════════════════════════════════════════════════════════════════════════════
      st.markdown("---")
      st.markdown("### 📊 Fundamental Analysis")
      with st.spinner("Fetching fundamentals..."):
          fund = get_fundamentals(symbol)
      if fund:
          # ── Flags / Quick Signals ──
          if fund.get("flags"):
              flags_html = " ".join(
                  f'<span style="background:{c}20;border:1px solid {c}40;color:{c};'
                  f'padding:3px 8px;border-radius:12px;font-size:10px;font-weight:600;margin-right:4px">{t}</span>'
                  for t, c in fund["flags"]
              )
              st.markdown(f'<div style="margin-bottom:12px">{flags_html}</div>', unsafe_allow_html=True)

          # ── Row 1: Identity & Valuation ──
          f_col1, f_col2, f_col3, f_col4 = st.columns(4)
          with f_col1:
              st.markdown(
                  f'<div style="background:#0d0f17;border:1px solid #1a1d2e;padding:12px;border-radius:6px">'
                  f'<div style="font-size:9px;color:#6b7099">SECTOR / INDUSTRY</div>'
                  f'<div style="font-size:12px;color:#e8ecff;font-weight:600;margin-top:4px">{fund.get("sector","N/A")}</div>'
                  f'<div style="font-size:10px;color:#6b7099">{fund.get("industry","N/A")}</div>'
                  f'</div>', unsafe_allow_html=True)
          with f_col2:
              pe_str = f'{fund["pe_ratio"]}x' if fund.get("pe_ratio") else "N/A"
              fwd_pe_str = f'{fund["forward_pe"]}x' if fund.get("forward_pe") else "N/A"
              peg_str = f'{fund["peg_ratio"]}' if fund.get("peg_ratio") else "N/A"
              st.markdown(
                  f'<div style="background:#0d0f17;border:1px solid #1a1d2e;padding:12px;border-radius:6px">'
                  f'<div style="font-size:9px;color:#6b7099">VALUATION</div>'
                  f'<div style="font-size:12px;color:{fund["valuation_color"]};font-weight:600;margin-top:4px">{fund["valuation"]}</div>'
                  f'<div style="font-size:10px;color:#6b7099">P/E: {pe_str} · Fwd: {fwd_pe_str} · PEG: {peg_str}</div>'
                  f'</div>', unsafe_allow_html=True)
          with f_col3:
              st.markdown(
                  f'<div style="background:#0d0f17;border:1px solid #1a1d2e;padding:12px;border-radius:6px">'
                  f'<div style="font-size:9px;color:#6b7099">MARKET CAP</div>'
                  f'<div style="font-size:12px;color:#e8ecff;font-weight:600;margin-top:4px">{fund["market_cap_str"]}</div>'
                  f'<div style="font-size:10px;color:#6b7099">Revenue: {fund.get("revenue_str","N/A")}</div>'
                  f'</div>', unsafe_allow_html=True)
          with f_col4:
              eps_t = f'${fund["trailing_eps"]}' if fund.get("trailing_eps") else "N/A"
              eps_f = f'${fund["forward_eps"]}' if fund.get("forward_eps") else "N/A"
              st.markdown(
                  f'<div style="background:#0d0f17;border:1px solid #1a1d2e;padding:12px;border-radius:6px">'
                  f'<div style="font-size:9px;color:#6b7099">EPS</div>'
                  f'<div style="font-size:12px;color:#e8ecff;font-weight:600;margin-top:4px">TTM: {eps_t}</div>'
                  f'<div style="font-size:10px;color:#6b7099">Forward: {eps_f}</div>'
                  f'</div>', unsafe_allow_html=True)

          # ── Row 2: Growth & Profitability ──
          f_col5, f_col6, f_col7, f_col8 = st.columns(4)
          with f_col5:
              rg = fund.get("revenue_growth")
              rg_str = f'{rg*100:+.1f}%' if rg is not None else "N/A"
              rg_color = "#00e5a0" if rg and rg > 0 else ("#ff4d6a" if rg and rg < 0 else "#6b7099")
              eg = fund.get("earnings_growth")
              eg_str = f'{eg*100:+.1f}%' if eg is not None else "N/A"
              eg_color = "#00e5a0" if eg and eg > 0 else ("#ff4d6a" if eg and eg < 0 else "#6b7099")
              st.markdown(
                  f'<div style="background:#0d0f17;border:1px solid #1a1d2e;padding:12px;border-radius:6px">'
                  f'<div style="font-size:9px;color:#6b7099">GROWTH (QoQ YoY)</div>'
                  f'<div style="font-size:12px;margin-top:4px">'
                  f'<span style="color:{rg_color};font-weight:600">Rev: {rg_str}</span></div>'
                  f'<div style="font-size:10px"><span style="color:{eg_color}">Earnings: {eg_str}</span></div>'
                  f'</div>', unsafe_allow_html=True)
          with f_col6:
              pm = fund.get("profit_margin")
              pm_str = f'{pm*100:.1f}%' if pm is not None else "N/A"
              pm_color = "#00e5a0" if pm and pm > 0 else ("#ff4d6a" if pm and pm < 0 else "#6b7099")
              gm = fund.get("gross_margin")
              gm_str = f'{gm*100:.1f}%' if gm is not None else "N/A"
              om = fund.get("operating_margin")
              om_str = f'{om*100:.1f}%' if om is not None else "N/A"
              st.markdown(
                  f'<div style="background:#0d0f17;border:1px solid #1a1d2e;padding:12px;border-radius:6px">'
                  f'<div style="font-size:9px;color:#6b7099">MARGINS</div>'
                  f'<div style="font-size:12px;color:{pm_color};font-weight:600;margin-top:4px">Net: {pm_str}</div>'
                  f'<div style="font-size:10px;color:#6b7099">Gross: {gm_str} · Op: {om_str}</div>'
                  f'</div>', unsafe_allow_html=True)
          with f_col7:
              roe_val = fund.get("roe")
              roe_str = f'{roe_val*100:.1f}%' if roe_val is not None else "N/A"
              roe_color = "#00e5a0" if roe_val and roe_val > 0.15 else ("#f5c842" if roe_val and roe_val > 0 else "#ff4d6a" if roe_val else "#6b7099")
              roa_val = fund.get("roa")
              roa_str = f'{roa_val*100:.1f}%' if roa_val is not None else "N/A"
              st.markdown(
                  f'<div style="background:#0d0f17;border:1px solid #1a1d2e;padding:12px;border-radius:6px">'
                  f'<div style="font-size:9px;color:#6b7099">EFFICIENCY</div>'
                  f'<div style="font-size:12px;color:{roe_color};font-weight:600;margin-top:4px">ROE: {roe_str}</div>'
                  f'<div style="font-size:10px;color:#6b7099">ROA: {roa_str}</div>'
                  f'</div>', unsafe_allow_html=True)
          with f_col8:
              de = fund.get("debt_to_equity")
              de_str = f'{de}%' if de is not None else "N/A"
              de_color = "#00e5a0" if de is not None and de < 50 else ("#f5c842" if de is not None and de < 150 else "#ff4d6a" if de else "#6b7099")
              cr = fund.get("current_ratio")
              cr_str = f'{cr}' if cr is not None else "N/A"
              st.markdown(
                  f'<div style="background:#0d0f17;border:1px solid #1a1d2e;padding:12px;border-radius:6px">'
                  f'<div style="font-size:9px;color:#6b7099">BALANCE SHEET</div>'
                  f'<div style="font-size:12px;color:{de_color};font-weight:600;margin-top:4px">D/E: {de_str}</div>'
                  f'<div style="font-size:10px;color:#6b7099">Current Ratio: {cr_str}</div>'
                  f'</div>', unsafe_allow_html=True)

          # ── Row 3: Analyst & Risk ──
          f_col9, f_col10, f_col11, f_col12 = st.columns(4)
          with f_col9:
              rec = fund.get("rec_key", "N/A").upper()
              rec_colors = {"STRONG_BUY": "#00e5a0", "BUY": "#00e5a0", "HOLD": "#f5c842",
                            "SELL": "#ff4d6a", "STRONG_SELL": "#ff4d6a"}
              rec_color = rec_colors.get(rec, "#6b7099")
              n_analysts = fund.get("num_analysts", "N/A")
              tp = f'${fund["target_price"]}' if fund.get("target_price") else "N/A"
              tl = f'${fund["target_low"]}' if fund.get("target_low") else "?"
              th = f'${fund["target_high"]}' if fund.get("target_high") else "?"
              up = fund.get("target_upside")
              up_str = f'{up:+.1f}%' if up is not None else ""
              up_color = "#00e5a0" if up and up > 0 else "#ff4d6a"
              st.markdown(
                  f'<div style="background:#0d0f17;border:1px solid #1a1d2e;padding:12px;border-radius:6px">'
                  f'<div style="font-size:9px;color:#6b7099">ANALYST CONSENSUS ({n_analysts})</div>'
                  f'<div style="font-size:12px;color:{rec_color};font-weight:600;margin-top:4px">{rec.replace("_"," ")}</div>'
                  f'<div style="font-size:10px;color:#6b7099">Target: {tp} <span style="color:{up_color}">{up_str}</span> ({tl}–{th})</div>'
                  f'</div>', unsafe_allow_html=True)
          with f_col10:
              beta_val = fund.get("beta")
              beta_str = f'{beta_val}' if beta_val is not None else "N/A"
              beta_color = "#00e5a0" if beta_val and beta_val < 1 else ("#f5c842" if beta_val and beta_val < 1.5 else "#ff4d6a" if beta_val else "#6b7099")
              sr = fund.get("short_ratio")
              sr_str = f'{sr} days' if sr is not None else "N/A"
              sp = fund.get("short_pct")
              sp_str = f'{sp*100:.1f}%' if sp is not None else "N/A"
              st.markdown(
                  f'<div style="background:#0d0f17;border:1px solid #1a1d2e;padding:12px;border-radius:6px">'
                  f'<div style="font-size:9px;color:#6b7099">RISK</div>'
                  f'<div style="font-size:12px;color:{beta_color};font-weight:600;margin-top:4px">Beta: {beta_str}</div>'
                  f'<div style="font-size:10px;color:#6b7099">Short: {sp_str} ({sr_str})</div>'
                  f'</div>', unsafe_allow_html=True)
          with f_col11:
              dy = fund.get("dividend_yield")
              dy_str = f'{dy*100:.2f}%' if dy is not None else "None"
              dy_color = "#00e5a0" if dy and dy > 0.02 else "#6b7099"
              pr = fund.get("payout_ratio")
              pr_str = f'{pr*100:.0f}%' if pr is not None else "N/A"
              st.markdown(
                  f'<div style="background:#0d0f17;border:1px solid #1a1d2e;padding:12px;border-radius:6px">'
                  f'<div style="font-size:9px;color:#6b7099">DIVIDEND</div>'
                  f'<div style="font-size:12px;color:{dy_color};font-weight:600;margin-top:4px">Yield: {dy_str}</div>'
                  f'<div style="font-size:10px;color:#6b7099">Payout: {pr_str}</div>'
                  f'</div>', unsafe_allow_html=True)
          with f_col12:
              w52h = f'${fund["week52_high"]}' if fund.get("week52_high") else "N/A"
              w52l = f'${fund["week52_low"]}' if fund.get("week52_low") else "N/A"
              w52pos = fund.get("week52_position")
              w52pos_str = f'{w52pos:.0f}%' if w52pos is not None else "N/A"
              pfh = fund.get("pct_from_high")
              pfh_str = f'{pfh:+.1f}%' if pfh is not None else ""
              pfh_color = "#00e5a0" if pfh and pfh > -5 else ("#f5c842" if pfh and pfh > -20 else "#ff4d6a" if pfh else "#6b7099")
              st.markdown(
                  f'<div style="background:#0d0f17;border:1px solid #1a1d2e;padding:12px;border-radius:6px">'
                  f'<div style="font-size:9px;color:#6b7099">52-WEEK RANGE</div>'
                  f'<div style="font-size:12px;color:{pfh_color};font-weight:600;margin-top:4px">{pfh_str} from high</div>'
                  f'<div style="font-size:10px;color:#6b7099">{w52l} — {w52h} (pos: {w52pos_str})</div>'
                  f'</div>', unsafe_allow_html=True)
      else:
          st.markdown(
              '<div style="background:#0d0f17;border:1px solid #1a1d2e;padding:16px;border-radius:6px;text-align:center">'
              '<div style="color:#6b7099;font-size:11px">Fundamental data unavailable — yfinance may be rate-limited or ticker not found</div>'
              '</div>', unsafe_allow_html=True)

      # 4H Candle details
      if candle_4h:
          date_label = f"{candle_date}" if candle_date else "N/A"
          status_emoji = "✅" if candle_status == "COMPLETE" else ("⏳" if candle_status == "IN PROGRESS" else "📅")
          st.markdown(f"#### ⏱ 4H Candle (9:00 AM - 1:00 PM ET) · {status_emoji} {candle_status} · {date_label}")
          c4h_col1, c4h_col2, c4h_col3, c4h_col4 = st.columns(4)
          with c4h_col1:
              st.metric("Open", f"${candle_4h['open']:.2f}")
          with c4h_col2:
              st.metric("Close", f"${candle_4h['close']:.2f}")
          with c4h_col3:
              st.metric("High", f"${candle_4h['high']:.2f}")
          with c4h_col4:
              st.metric("Low", f"${candle_4h['low']:.2f}")
        
          # Status-specific message
          if candle_status == "IN PROGRESS":
              time_remaining = candle_complete_time - now_et
              mins_remaining = int(time_remaining.total_seconds() // 60)
              status_msg = f"⏳ Candle forming · ~{mins_remaining} min until 1:00 PM ET"
          elif candle_status == "COMPLETE":
              status_msg = f"✅ Today's candle complete"
          else:
              days_old = (today - candle_date).days
              if days_old > 1:
                  status_msg = f"⚠️ Data is {days_old} days old · Polygon free tier delay"
              else:
                  status_msg = f"📅 Last trading session ({candle_date})"
        
          st.markdown(
              f'<div style="background:#0d0f1799;border:1px solid {candle_4h_color}40;'
              f'padding:12px;border-radius:4px;margin:12px 0">'
              f'<div style="font-size:10px;color:#6b7099">4H CANDLE SIGNAL</div>'
              f'<div style="font-size:18px;color:{candle_4h_color};font-weight:bold;margin-top:4px">'
              f'{candle_4h_signal}</div>'
              f'<div style="font-size:10px;color:#6b7099;margin-top:4px">'
              f'{status_msg} · Based on {candle_4h["bars"]} hourly bars · '
              f'Range: ${candle_4h["low"]:.2f} - ${candle_4h["high"]:.2f}</div>'
              f'</div>',
              unsafe_allow_html=True
          )
        
          # Show warning if data is significantly delayed
          if candle_status == "LAST SESSION" and candle_date:
              days_old = (today - candle_date).days
              if days_old > 1:
                  st.markdown(
                      f'<div style="background:#f5c84210;border:1px solid #f5c84240;'
                      f'padding:10px;border-radius:4px;margin-top:8px;font-size:10px;color:#f5c842">'
                      f'⚠️ <b>Polygon Free Tier Limitation:</b> Hourly bar data is delayed by ~{days_old} days. '
                      f'For real-time intraday data, upgrade to a paid Polygon plan at polygon.io/pricing'
                      f'</div>',
                      unsafe_allow_html=True
                  )
      else:
          st.markdown("#### ⏱ 4H Candle (9:00 AM - 1:00 PM ET)")
        
          # Debug info
          hourly_info = []
          if hourly_df is None or hourly_df.empty:
              hourly_info.append("❌ No hourly data loaded")
          else:
              hourly_info.append(f"✓ Hourly data: {len(hourly_df)} bars")
              if len(hourly_df) > 0:
                  hourly_info.append(f"✓ Date range: {hourly_df.index.min()} to {hourly_df.index.max()}")
                  # Check available dates
                  available_dates = sorted(set(hourly_df.index.date))[-10:]
                  hourly_info.append(f"✓ Recent dates with data: {available_dates}")
          hourly_info.append(f"✓ Today: {today} ({today.strftime('%A')})")
          hourly_info.append(f"✓ Current ET time: {now_et.strftime('%Y-%m-%d %H:%M %Z')}")
        
          # Show dates that were checked
          checked = st.session_state.get("_4h_debug_dates", [])
          if checked:
              hourly_info.append(f"✓ Dates checked for 4H candle:")
              for d, status in checked:
                  hourly_info.append(f"   - {d} ({d.strftime('%A')}): {status}")
        
          with st.expander("🔍 Debug: Hourly Data Info", expanded=True):
              for info in hourly_info:
                  st.text(info)
        
          st.warning("No 4H candle data available. See debug info above.")
    
      # Fibonacci levels table
      st.markdown("#### 📐 Fibonacci Levels")
      st.markdown(f'<div style="font-size:10px;color:#6b7099;margin-bottom:8px">'
                  f'Swing: ${swing_lo:.2f} (low) → ${swing_hi:.2f} (high) · Range: ${swing_range:.2f}</div>',
                  unsafe_allow_html=True)
    
      fib_data = []
      for name, level in sorted(fib_levels.items(), key=lambda x: x[1], reverse=True):
          dist_pct = abs(latest_close - level) / level * 100
          position = "▶" if abs(latest_close - level) < swing_range * 0.02 else ""
          fib_data.append({
              "Level": name,
              "Price": f"${level:.2f}",
              "Distance": f"{dist_pct:.1f}%",
              "": position
          })
    
      fib_df = pd.DataFrame(fib_data)
      st.dataframe(fib_df, use_container_width=True, hide_index=True)
    
      # Visual bias indicator
      st.markdown(
          f'<div style="background:linear-gradient(90deg, #ff4d6a 0%, #f5c842 50%, #00e5a0 100%);'
          f'height:8px;border-radius:4px;margin:12px 0;position:relative">'
          f'<div style="position:absolute;left:{price_position*100:.1f}%;top:-4px;'
          f'width:16px;height:16px;background:{bias_color};border-radius:50%;'
          f'border:2px solid #07080d;transform:translateX(-50%)"></div>'
          f'</div>'
          f'<div style="display:flex;justify-content:space-between;font-size:9px;color:#6b7099">'
          f'<span>Bearish (0%)</span><span>Neutral (50%)</span><span>Bullish (100%)</span>'
          f'</div>',
          unsafe_allow_html=True
      )
    
      # ── Options Bias Section ──
      st.markdown("---")
      st.markdown("### 📈 Options Bias")
    
      # options_data was already fetched above for verdict calculation
    
      if options_data and "error" not in options_data:
          # Row 1: Main metrics
          opt_col1, opt_col2, opt_col3, opt_col4 = st.columns(4)
          with opt_col1:
              st.metric("OI Sentiment", options_data["sentiment"], options_data["sentiment_desc"][:30])
          with opt_col2:
              st.metric("P/C Ratio (OI)", f"{options_data['oi_pc_ratio']:.2f}", 
                       f"{options_data['put_oi']:,} P / {options_data['call_oi']:,} C")
          with opt_col3:
              st.metric("Total OI", f"{options_data['total_oi']:,}",
                       f"{options_data['total_puts']} P · {options_data['total_calls']} C")
          with opt_col4:
              if options_data.get('total_volume', 0) > 0:
                  st.metric("Today's Volume", f"{options_data['total_volume']:,}",
                           f"P/C: {options_data['vol_pc_ratio']:.2f}")
              else:
                  st.metric("Today's Volume", "N/A", "Snapshot unavailable")
        
          # Row 2: Volume flow
          if options_data.get('total_volume', 0) > 0:
              vol_col1, vol_col2, vol_col3 = st.columns(3)
              with vol_col1:
                  st.metric("Call Volume", f"{options_data['call_volume']:,}")
              with vol_col2:
                  st.metric("Put Volume", f"{options_data['put_volume']:,}")
              with vol_col3:
                  st.metric("Volume Sentiment", options_data['vol_sentiment'])
        
          # Visual options bias indicator
          pc = options_data['oi_pc_ratio']
          if pc <= 0.3:
              opt_position = 1.0
          elif pc >= 1.4:
              opt_position = 0.0
          else:
              opt_position = 1.0 - (pc - 0.3) / 1.1
        
          opt_position = max(0, min(1, opt_position))
        
          st.markdown(
              f'<div style="background:linear-gradient(90deg, #ff4d6a 0%, #f5c842 50%, #00e5a0 100%);'
              f'height:8px;border-radius:4px;margin:12px 0;position:relative">'
              f'<div style="position:absolute;left:{opt_position*100:.1f}%;top:-4px;'
              f'width:16px;height:16px;background:{options_data["sentiment_color"]};border-radius:50%;'
              f'border:2px solid #07080d;transform:translateX(-50%)"></div>'
              f'</div>'
              f'<div style="display:flex;justify-content:space-between;font-size:9px;color:#6b7099">'
              f'<span>Put Heavy (Bearish)</span><span>Balanced</span><span>Call Heavy (Bullish)</span>'
              f'</div>',
              unsafe_allow_html=True
          )
        
          # ── Unusual Options Activity ──
          unusual = options_data.get('unusual_activity', [])
          top_vol = options_data.get('top_volume', [])
        
          st.markdown("#### 🔥 Options Volume Activity")
        
          # Show debug info
          debug = options_data.get('debug', [])
          if debug:
              with st.expander("🔍 API Debug Info", expanded=False):
                  for d in debug:
                      st.text(d)
        
          if unusual:
              st.markdown('<div style="font-size:9px;color:#6b7099;margin-bottom:8px">'
                         'Contracts with high volume or unusual Vol/OI ratio (potential large bets)</div>',
                         unsafe_allow_html=True)
            
              unusual_data = []
              for u in unusual:
                  row = {
                      "Type": u['type'],
                      "Strike": f"${u['strike']:.2f}",
                      "Expiry": u['expiry'],
                      "Volume": f"{u['volume']:,}",
                      "OI": f"{u['oi']:,}",
                      "Vol/OI": f"{u['vol_oi_ratio']:.1f}x",
                      "🔥": "⚡" if u.get('is_unusual') else "",
                  }
                  if u.get("moneyness"):
                      row["Moneyness"] = u["moneyness"]
                      row["Intent"] = u.get("intent", "")
                  unusual_data.append(row)
            
              unusual_df = pd.DataFrame(unusual_data)
              st.dataframe(unusual_df, use_container_width=True, hide_index=True)
            
              # Summary of unusual activity
              call_unusual = [u for u in unusual if u['type'] == 'CALL']
              put_unusual = [u for u in unusual if u['type'] == 'PUT']
              call_vol = sum(u['volume'] for u in call_unusual)
              put_vol = sum(u['volume'] for u in put_unusual)
            
              if call_vol > put_vol * 1.5:
                  unusual_bias = "BULLISH"
                  unusual_color = "#00e5a0"
                  unusual_desc = f"Call volume dominates ({call_vol:,} vs {put_vol:,} puts)"
              elif put_vol > call_vol * 1.5:
                  unusual_bias = "BEARISH"
                  unusual_color = "#ff4d6a"
                  unusual_desc = f"Put volume dominates ({put_vol:,} vs {call_vol:,} calls)"
              else:
                  unusual_bias = "MIXED"
                  unusual_color = "#f5c842"
                  unusual_desc = f"Mixed flow ({call_vol:,} calls / {put_vol:,} puts)"
            
              st.markdown(
                  f'<div style="background:#0d0f1799;border:1px solid {unusual_color}40;'
                  f'padding:12px;border-radius:4px;margin-top:8px">'
                  f'<div style="font-size:10px;color:#6b7099">TOP VOLUME BIAS</div>'
                  f'<div style="font-size:14px;color:{unusual_color};font-weight:bold;margin-top:4px">'
                  f'🔥 {unusual_bias}</div>'
                  f'<div style="font-size:10px;color:#6b7099;margin-top:4px">{unusual_desc}</div>'
                  f'</div>',
                  unsafe_allow_html=True
              )
          elif top_vol:
              # Show top volume even if not flagged as unusual
              st.markdown('<div style="font-size:9px;color:#6b7099;margin-bottom:8px">'
                         'Top contracts by today\'s volume</div>',
                         unsafe_allow_html=True)
            
              top_data = []
              for u in top_vol[:10]:
                  row = {
                      "Type": u['type'],
                      "Strike": f"${u['strike']:.2f}",
                      "Expiry": u['expiry'],
                      "Volume": f"{u['volume']:,}",
                      "OI": f"{u['oi']:,}",
                      "Vol/OI": f"{u['vol_oi_ratio']:.1f}x",
                  }
                  if u.get("moneyness"):
                      row["Moneyness"] = u["moneyness"]
                      row["Intent"] = u.get("intent", "")
                  top_data.append(row)
            
              top_df = pd.DataFrame(top_data)
              st.dataframe(top_df, use_container_width=True, hide_index=True)
              st.info("No contracts met unusual activity thresholds (Vol>1000 or Vol/OI>2x)")
          else:
              st.warning("No volume data available. The options snapshot API may require a Polygon paid plan.")
              st.markdown('<div style="font-size:9px;color:#6b7099">'
                         'OI data is still available above from the contracts API.</div>',
                         unsafe_allow_html=True)
        
          # ── Delta-Aware Analysis Section ──
          delta_data = options_data.get("delta_analysis")
          delta_sent = options_data.get("delta_sentiment", "N/A")
          delta_clr = options_data.get("delta_color", "#6b7099")
          delta_dsc = options_data.get("delta_desc", "")
          if delta_data and delta_sent != "N/A":
              st.markdown("#### 🎯 Delta-Aware Sentiment")
              st.markdown(
                  '<div style="font-size:9px;color:#6b7099;margin-bottom:8px">'
                  'Classifies options by moneyness: deep ITM calls → hedges/covered calls, '
                  'far OTM puts → protective hedges, ATM/OTM → directional bets</div>',
                  unsafe_allow_html=True)
              da_col1, da_col2, da_col3 = st.columns(3)
              with da_col1:
                  st.markdown(
                      f'<div style="background:#0d0f17;border:1px solid #1a1d2e;padding:12px;border-radius:6px">'
                      f'<div style="font-size:9px;color:#6b7099">SPECULATIVE BULLISH</div>'
                      f'<div style="font-size:9px;color:#6b7099;margin-top:2px">(OTM + ATM Calls)</div>'
                      f'<div style="font-size:14px;color:#00e5a0;font-weight:700;margin-top:6px">'
                      f'{delta_data["spec_bull_oi"] + delta_data["atm_call_oi"]:,} OI</div>'
                      f'<div style="font-size:10px;color:#6b7099">Vol: {delta_data["spec_bull_vol"] + delta_data["atm_call_vol"]:,}</div>'
                      f'</div>', unsafe_allow_html=True)
              with da_col2:
                  st.markdown(
                      f'<div style="background:#0d0f17;border:1px solid #1a1d2e;padding:12px;border-radius:6px">'
                      f'<div style="font-size:9px;color:#6b7099">SPECULATIVE BEARISH</div>'
                      f'<div style="font-size:9px;color:#6b7099;margin-top:2px">(ATM + ITM Puts)</div>'
                      f'<div style="font-size:14px;color:#ff4d6a;font-weight:700;margin-top:6px">'
                      f'{delta_data["spec_bear_oi"] + delta_data["atm_put_oi"]:,} OI</div>'
                      f'<div style="font-size:10px;color:#6b7099">Vol: {delta_data["spec_bear_vol"] + delta_data["atm_put_vol"]:,}</div>'
                      f'</div>', unsafe_allow_html=True)
              with da_col3:
                  st.markdown(
                      f'<div style="background:#0d0f17;border:1px solid #1a1d2e;padding:12px;border-radius:6px">'
                      f'<div style="font-size:9px;color:#6b7099">HEDGES (DISCOUNTED)</div>'
                      f'<div style="font-size:9px;color:#6b7099;margin-top:2px">(Deep ITM Calls + Far OTM Puts)</div>'
                      f'<div style="font-size:14px;color:#f5c842;font-weight:700;margin-top:6px">'
                      f'{delta_data["hedge_call_oi"] + delta_data["hedge_put_oi"]:,} OI</div>'
                      f'<div style="font-size:10px;color:#6b7099">Vol: {delta_data["hedge_call_vol"] + delta_data["hedge_put_vol"]:,}</div>'
                      f'</div>', unsafe_allow_html=True)
              st.markdown(
                  f'<div style="background:#0d0f1799;border:1px solid {delta_clr}40;'
                  f'padding:12px;border-radius:4px;margin-top:8px">'
                  f'<div style="font-size:10px;color:#6b7099">DELTA-ADJUSTED SENTIMENT</div>'
                  f'<div style="font-size:14px;color:{delta_clr};font-weight:bold;margin-top:4px">'
                  f'{delta_sent}</div>'
                  f'<div style="font-size:10px;color:#6b7099;margin-top:4px">{delta_dsc}</div>'
                  f'</div>', unsafe_allow_html=True)

          # Summary box
          _delta_line = ""
          if options_data.get("delta_sentiment") and options_data["delta_sentiment"] != "N/A":
              _dc = options_data["delta_color"]
              _ds = options_data["delta_sentiment"]
              _delta_line = (f'<div style="font-size:12px;color:{_dc};font-weight:bold;margin-top:4px">'
                            f'{_ds} (Delta-Adjusted)</div>')
          st.markdown(
              f'<div style="background:#0d0f1799;border:1px solid {options_data["sentiment_color"]}40;'
              f'padding:12px;border-radius:4px;margin-top:12px">'
              f'<div style="font-size:10px;color:#6b7099">OPTIONS FLOW SUMMARY</div>'
              f'<div style="font-size:12px;color:{options_data["sentiment_color"]};font-weight:bold;margin-top:4px">'
              f'{options_data["sentiment"]} (Raw OI)</div>'
              f'{_delta_line}'
              f'<div style="font-size:10px;color:#6b7099;margin-top:4px">'
              f'P/C Ratio (OI): {options_data["oi_pc_ratio"]:.2f} · '
              f'{"<0.7 = Bullish" if options_data["oi_pc_ratio"] < 0.7 else ("0.7-1.0 = Neutral" if options_data["oi_pc_ratio"] <= 1.0 else ">1.0 = Bearish")}'
              f'</div></div>',
              unsafe_allow_html=True
          )
      elif options_data and "error" in options_data:
          st.warning(f"⚠️ Options data unavailable: {options_data['error'][:60]}...")
          debug = options_data.get('debug', [])
          if debug:
              with st.expander("🔍 API Debug Info", expanded=True):
                  for d in debug:
                      st.text(d)
          st.markdown('<div style="font-size:9px;color:#6b7099">'
                     'Options chain data may require a Polygon paid plan.</div>',
                     unsafe_allow_html=True)
      else:
          st.info("No options data available for this ticker.")
    
      # ── Strategy Analysis Section (Fib + FVG + Weinstein + Bias) ── (optional)
      if use_strategy:
          st.markdown("---")
          st.markdown("### 📊 Strategy Analysis (Fib + Weinstein + Bias)")
        
          strategy_data = analyze_strategy_signals(daily_df)
    
          if strategy_data and "error" not in strategy_data:
              # Main signal display
              if strategy_data["short_signal"]:
                  signal_color = "#ff4d6a"
                  signal_text = f"SHORT SIGNAL ({strategy_data['short_tier']})"
                  signal_emoji = "🔴"
              elif strategy_data["long_signal"]:
                  signal_color = "#00e5a0"
                  signal_text = f"LONG SIGNAL ({strategy_data['long_tier']})"
                  signal_emoji = "🟢"
              else:
                  signal_color = "#6b7099"
                  signal_text = "NO SIGNAL"
                  signal_emoji = "⚪"
            
              st.markdown(
                  f'<div style="background:linear-gradient(135deg, #0d0f17 0%, #131625 100%);'
                  f'border:2px solid {signal_color};border-radius:8px;padding:16px;margin:12px 0;text-align:center">'
                  f'<div style="font-size:24px">{signal_emoji}</div>'
                  f'<div style="font-size:18px;font-weight:bold;color:{signal_color};margin-top:8px">{signal_text}</div>'
                  f'</div>',
                  unsafe_allow_html=True
              )
            
              # Take profit alerts
              if strategy_data["short_tp_hit"]:
                  st.markdown(
                      '<div style="background:#00e5a020;border:1px solid #00e5a060;padding:12px;border-radius:4px;margin:8px 0">'
                      '<span style="font-size:14px">💰</span> '
                      '<span style="color:#00e5a0;font-weight:bold">SHORT TAKE PROFIT ZONE</span>'
                      '<span style="font-size:11px;color:#6b7099"> — Price dropped 10%+ from recent high</span>'
                      '</div>',
                      unsafe_allow_html=True
                  )
              if strategy_data["long_tp_hit"]:
                  st.markdown(
                      '<div style="background:#00e5a020;border:1px solid #00e5a060;padding:12px;border-radius:4px;margin:8px 0">'
                      '<span style="font-size:14px">💰</span> '
                      '<span style="color:#00e5a0;font-weight:bold">LONG TAKE PROFIT ZONE</span>'
                      '<span style="font-size:11px;color:#6b7099"> — Price rose 10%+ from recent low</span>'
                      '</div>',
                      unsafe_allow_html=True
                  )
            
              # Condition breakdown
              st.markdown("#### 📋 Signal Conditions")
            
              col1, col2 = st.columns(2)
            
              with col1:
                  st.markdown("**Structure**")
                  swing_icon = "🔴" if strategy_data["is_bearish_swing"] else ("🟢" if strategy_data["is_bullish_swing"] else "⚪")
                  trend_icon = "🔴" if strategy_data["is_downtrend"] else ("🟢" if strategy_data["is_uptrend"] else "⚪")
                  bias_icon = "🔴" if strategy_data["is_bearish_bias"] else ("🟢" if strategy_data["is_bullish_bias"] else "⚪")
                
                  st.markdown(f"{swing_icon} Swing: **{'Bearish' if strategy_data['is_bearish_swing'] else ('Bullish' if strategy_data['is_bullish_swing'] else 'Neutral')}**")
                  st.markdown(f"{trend_icon} Trend: **{'Down' if strategy_data['is_downtrend'] else ('Up' if strategy_data['is_uptrend'] else 'Ranging')}**")
                  st.markdown(f"{bias_icon} Bias: **{'Bearish' if strategy_data['is_bearish_bias'] else ('Bullish' if strategy_data['is_bullish_bias'] else 'Neutral')}**")
                
              with col2:
                  st.markdown("**Volume & Candle**")
                  vol_icon = "✅" if strategy_data["high_volume"] else "❌"
                  candle_icon = "🟢" if strategy_data.get("is_green_candle") else ("🔴" if strategy_data.get("is_red_candle") else "⚪")
                  seller_icon = "🔴" if strategy_data["seller_conviction"] else "⚪"
                  buyer_icon = "🟢" if strategy_data["buyer_conviction"] else "⚪"
                
                  candle_dir = "Green (Close > Open)" if strategy_data.get("is_green_candle") else ("Red (Close < Open)" if strategy_data.get("is_red_candle") else "Doji")
                  st.markdown(f"{candle_icon} Daily Candle: **{candle_dir}**")
                  st.markdown(f"{vol_icon} High Volume: **{strategy_data['volume_ratio']:.1f}x** avg")
                  st.markdown(f"{seller_icon} Seller Conviction: **{'Yes' if strategy_data['seller_conviction'] else 'No'}**")
                  st.markdown(f"{buyer_icon} Buyer Conviction: **{'Yes' if strategy_data['buyer_conviction'] else 'No'}**")
            
              # Weinstein indicators
              st.markdown("#### 📈 Weinstein Stage Analysis")
              wei_col1, wei_col2, wei_col3 = st.columns(3)
            
              with wei_col1:
                  st.metric("Price Position", f"{strategy_data['price_position']:.0f}%", 
                           f"of 52-week range")
              with wei_col2:
                  st.metric("From 52W High", f"-{strategy_data['dist_from_high']:.1f}%",
                           "MA10 < MA30" if strategy_data["ma10_below_ma30"] else "MA10 > MA30")
              with wei_col3:
                  st.metric("Breakdown Score", f"{strategy_data['breakdown_score']}/5",
                           "Higher = more bearish" if strategy_data['breakdown_score'] >= 3 else "Low score")
            
              # FVG info
              if strategy_data["fvg_details"]:
                  fvg = strategy_data["fvg_details"]
                  fvg_color = "#ff4d6a" if fvg["type"] == "BEARISH" else "#00e5a0"
                  st.markdown(
                      f'<div style="background:#0d0f1799;border:1px solid {fvg_color}40;'
                      f'padding:10px;border-radius:4px;margin-top:12px">'
                      f'<div style="font-size:10px;color:#6b7099">FAIR VALUE GAP</div>'
                      f'<div style="font-size:14px;color:{fvg_color};font-weight:bold">'
                      f'{fvg["type"]} FVG ({fvg["size_pct"]:.2f}%)</div>'
                      f'<div style="font-size:10px;color:#6b7099">'
                      f'Gap zone: ${fvg["bottom"]:.2f} - ${fvg["top"]:.2f}</div>'
                      f'</div>',
                      unsafe_allow_html=True
                  )
            
              # Short signal criteria summary
              with st.expander("📝 Short Signal Criteria", expanded=False):
                  st.markdown("""
                  **Tier 1 (Full Alignment):**
                  - ✅ Bearish Swing (recent low more recent than high)
                  - ✅ Seller Conviction (high volume + close in lower half)
                  - ✅ Downtrend (lower highs & lower lows)
                  - ✅ Bearish Bias (price below midpoint)
                
                  **Tier 2 (Trend + Bias):**
                  - ✅ Bearish Swing
                  - ✅ Bearish Bias
                  - ✅ Breakout Score ≤ 3
                  - ✅ No Buyer Conviction
                
                  **SHORT TAKE PROFIT:** Price drops 10% from entry
                  """)
          else:
              st.warning("Insufficient data for strategy analysis. Need at least 50 bars.")
    
      # Next earnings info
      if next_earn:
          days_to_earn = (datetime.strptime(next_earn, "%Y-%m-%d").date() - date.today()).days
          st.markdown(
              f'<div style="background:#0d0f1799;border:1px solid #4d9fff40;padding:12px;border-radius:4px;margin-top:16px">'
              f'<div style="font-size:10px;color:#6b7099">📅 NEXT EARNINGS (estimated)</div>'
              f'<div style="font-size:16px;color:#4d9fff;font-weight:bold">{next_earn}</div>'
              f'<div style="font-size:10px;color:#6b7099">{days_to_earn} days away</div>'
              f'</div>',
              unsafe_allow_html=True
          )
    
      # Show earnings dates table
      st.markdown("---")
      st.markdown("### 📅 Earnings History")
      earn_df = pd.DataFrame(earnings_events, columns=["Report Date", "Quarter", "Period"])
      earn_df = earn_df.sort_values("Report Date", ascending=False).reset_index(drop=True)
      st.dataframe(earn_df, use_container_width=True, height=250)
    
      # st.info("👆 Review the analysis above, then click **▶ RUN BACKTEST** to run the strategy.")

# # ── Run backtest (COMMENTED OUT — moved to Backtest tab) ─────────────────────
# if run_btn:
#   with tab_fetch:
#     if st.session_state.fetched_data is None:
#         st.warning("Please click **📅 FETCH EARNINGS** first to load earnings data.")

if False:  # Earnings Analysis backtest commented out — use the Backtest tab instead
  # if run_btn and st.session_state.fetched_data is not None:
  #   with tab_fetch:
    # Load from session state
    data = st.session_state.fetched_data
    symbol = data["symbol"]
    daily_df = data["daily_df"]
    earnings_events = data["earnings_events"]
    earn_source = data["earn_source"]
    next_earn = data["next_earn"]
    start_date = data["start_date"]
    end_date = data["end_date"]
    
    status = st.empty()
    
    # Source badge in sidebar
    source_colors = {"manual": "#00e5a0", "polygon": "#4d9fff", "auto-detected": "#f5c842", "none": "#ff4d6a"}
    source_labels = {
        "manual":       "✏️ manual input",
        "polygon":      "🔷 Polygon financials",
        "auto-detected":"⚡ auto-detected from price gaps",
        "none":         "❌ none",
    }
    st.sidebar.markdown(
        f'<div style="font-size:9px;color:{source_colors.get(earn_source,"#6b7099")};'
        f'margin-top:4px">Earnings source: {source_labels.get(earn_source, earn_source)} '
        f'({len(earnings_events)} events)</div>',
        unsafe_allow_html=True,
    )

    # Hourly bars for 4H candle analysis
    hourly_start = max(start_date, end_date - timedelta(days=730))
    if use_4h:
        status.info("⏱ Fetching hourly bars for 4H candle analysis…")
        try:
            if data_source == "Alpaca":
                hourly_df = get_hourly_bars_alpaca(symbol, str(hourly_start), str(end_date), api_key, api_secret)
            else:
                hourly_df = get_hourly_bars(symbol, str(hourly_start), str(end_date), api_key)
            if hourly_df.empty:
                st.sidebar.warning("⚠️ No hourly data returned — 4H candle will fall back to daily open/close.")
        except Exception as e:
            st.sidebar.warning(f"⚠️ Hourly bars unavailable ({str(e)[:80]}). 4H candle will fall back to daily.")
            hourly_df = pd.DataFrame()
    else:
        hourly_df = pd.DataFrame()

    status.info("⚙️ Running backtest…")
    
    # Filter to last N earnings (sorted by date, take last N)
    earnings_sorted = sorted(earnings_events, key=lambda x: x[0])
    earnings_filtered = earnings_sorted[-last_n_earnings:] if len(earnings_sorted) > last_n_earnings else earnings_sorted
    
    all_trades = run_backtest(
        symbol, daily_df, hourly_df, earnings_filtered,
        vol_min, use_vol, fib_tol, use_fib, use_4h, fib_tf,
    )
    status.empty()
    
    # Store filtered count for diagnostics
    earnings_used = earnings_filtered

    if all_trades.empty:
        skipped_reasons = all_trades.attrs.get("skipped_reasons", [])
    
        # Build diagnostic message
        diag_lines = []
        diag_lines.append(f"**Earnings events used:** {len(earnings_used)} (filtered from {len(earnings_events)} total)")
        diag_lines.append(f"**Daily price data range:** {min(daily_df.index)} to {max(daily_df.index)}")
    
        if skipped_reasons:
            diag_lines.append("\n**Skipped events:**")
            for dt, reason in skipped_reasons[:10]:  # Show first 10
                diag_lines.append(f"- {dt}: {reason}")
            if len(skipped_reasons) > 10:
                diag_lines.append(f"- ... and {len(skipped_reasons) - 10} more")
    
        st.warning("No trades could be computed. Try expanding the date range or relaxing filters.")
        st.info("\n".join(diag_lines))
    
        # Suggestions based on diagnostics
        suggestions = []
        if skipped_reasons:
            entry_issues = sum(1 for _, r in skipped_reasons if "entry_date not in" in r)
            exit_issues = sum(1 for _, r in skipped_reasons if "exit date" in r)
            if entry_issues > 0:
                suggestions.append(f"• {entry_issues} earnings dates are outside your price data range")
            if exit_issues > 0:
                suggestions.append(f"• {exit_issues} events are missing next-day exit data (possibly at end of data range)")
    
        if suggestions:
            st.markdown("**Possible causes:**\n" + "\n".join(suggestions))
    
        st.stop()

    active_trades = all_trades[all_trades["passes_all"]].reset_index(drop=True)
    skipped       = len(all_trades) - len(active_trades)
    stats         = calc_stats(active_trades)

    # ── Next earnings banner ─────────────────────────────
    display_next = str(next_earnings_input) if next_earnings_input else next_earn
    if display_next:
        days_away = (datetime.strptime(display_next, "%Y-%m-%d").date() - date.today()).days
        urgency_color = "#ff4d6a" if days_away <= 1 else ("#f5c842" if days_away <= 7 else "#4d9fff")
        days_label = "TODAY" if days_away == 0 else ("TOMORROW" if days_away == 1 else f"in {days_away} days")
        src_label = "manually set" if next_earnings_input else "estimated from filing cadence"
        st.markdown(
            f'<div style="background:rgba(245,200,66,.06);border:1px solid {urgency_color}40;'
            f'padding:10px 18px;border-radius:4px;margin-bottom:12px;display:flex;align-items:center;gap:20px">'
            f'<span style="font-size:20px">📅</span>'
            f'<div>'
            f'<div style="font-size:9px;color:#6b7099;letter-spacing:1.5px;margin-bottom:2px">NEXT EARNINGS · {src_label.upper()}</div>'
            f'<div><b style="color:{urgency_color};font-size:16px">{display_next}</b>'
            f' &nbsp;<span style="font-size:11px;color:{urgency_color};font-weight:700">{days_label}</span>'
            f' &nbsp;<span style="font-size:9px;color:#6b7099">· AMC · enter at 1:30 PM ET on this date</span></div>'
            f'</div></div>',
            unsafe_allow_html=True,
        )

    # ── Filter summary ───────────────────────────────────
    tags = []
    if use_4h:  tags.append('<span class="pill pill-blue">4H CANDLE</span>')
    if use_fib: tags.append(f'<span class="pill pill-blue">FIB {fib_tf.upper()} ±{fib_tol}%</span>')
    if use_vol: tags.append(f'<span class="pill pill-green">VOL ≥{vol_min:.1f}x</span>')
    st.markdown(
        f'<div style="margin-bottom:12px;font-size:10px;color:#6b7099">'
        f'<b style="color:#e8ecff">{len(all_trades)}</b> events · '
        f'<b style="color:#00e5a0">{len(active_trades)}</b> active · '
        f'<b style="color:#f5c842">{skipped}</b> filtered &nbsp;&nbsp;'
        + " ".join(tags) + "</div>",
        unsafe_allow_html=True,
    )

    # ── Stats row ────────────────────────────────────────
    profit = stats["total_return"] >= 0
    c1,c2,c3,c4,c5,c6,c7 = st.columns(7)
    c1.metric("Total Return",   f'{stats["total_return"]:+.2f}%', f'$100 → ${stats["final_eq"]:.0f}')
    c2.metric("Win Rate",       f'{stats["win_rate"]:.1f}%',      f'{stats["wins"]}W / {stats["losses"]}L')
    c3.metric("Profit Factor",  str(stats["profit_factor"]) if stats["profit_factor"] else "∞", f'avg W {stats["avg_win"]:+.2f}%')
    c4.metric("Max Drawdown",   f'-{stats["max_dd"]:.2f}%')
    c5.metric("Avg Trade",      f'{stats["avg_trade"]:+.2f}%')
    c6.metric("Active Trades",  str(stats["n"]))
    c7.metric("Fib Hits",       f'{all_trades["fib_hit"].notna().sum()}/{len(all_trades)}')

    st.markdown("---")

    # ── LIVE SETUP PANEL ────────────────────────────────────────────
    if display_next:
        next_date  = datetime.strptime(display_next, "%Y-%m-%d").date()
        days_away  = (next_date - date.today()).days

        last_trade    = all_trades.iloc[-1] if not all_trades.empty else None
        last_swing_lo = last_trade["swing_lo"] if last_trade is not None else None
        last_swing_hi = last_trade["swing_hi"] if last_trade is not None else None
        latest_close  = float(daily_df["close"].iloc[-1]) if not daily_df.empty else None

        long_trades  = active_trades[active_trades["direction"] == "LONG"]
        short_trades = active_trades[active_trades["direction"] == "SHORT"]
        long_wr   = long_trades["win"].mean() * 100  if len(long_trades)  else 0
        short_wr  = short_trades["win"].mean() * 100 if len(short_trades) else 0
        long_avg  = long_trades["pnl_pct"].mean()    if len(long_trades)  else 0
        short_avg = short_trades["pnl_pct"].mean()   if len(short_trades) else 0
        best_dir  = "LONG" if long_wr >= short_wr else "SHORT"
        best_wr   = max(long_wr, short_wr)

        recent_20   = daily_df["volume"].tail(20).mean() if not daily_df.empty else None
        vol_trigger = recent_20 * vol_min if (recent_20 and use_vol) else None

        fib_table_html = ""
        if last_swing_lo and last_swing_hi:
            fib_lvls    = calc_fib_levels(last_swing_lo, last_swing_hi)
            sorted_lvls = sorted(fib_lvls.items(), key=lambda x: x[1])
            fib_rows = []
            for name, lvl in sorted_lvls:
                is_ext    = name.startswith("E")
                type_label = "EXT" if is_ext else "RET"
                type_full  = "Extension" if is_ext else "Retracement"
                if latest_close:
                    dist      = (lvl - latest_close) / latest_close * 100
                    dist_str  = f"{dist:+.1f}%"
                    highlight = "#00e5a0" if abs(dist) <= fib_tol else ("#f5c842" if abs(dist) <= fib_tol * 2 else "#3a3d5c")
                else:
                    dist_str, highlight = "—", "#3a3d5c"
                color = "#f5c842" if is_ext else "#4d9fff"
                fib_rows.append(
                    f'<tr style="border-bottom:1px solid #0d0f17">'
                    f'<td style="padding:4px 10px;white-space:nowrap">'
                    f'  <span style="font-size:8px;padding:1px 5px;border-radius:2px;font-weight:700;'
                    f'  background:{color}15;color:{color};border:1px solid {color}30">{type_label}</span>'
                    f'  <span style="color:#6b7099;font-size:8px;margin-left:3px">{type_full}</span>'
                    f'</td>'
                    f'<td style="padding:4px 10px;color:{color};font-weight:700">{name[1:]}</td>'
                    f'<td style="padding:4px 10px;color:#e8ecff;font-family:monospace">${lvl:.2f}</td>'
                    f'<td style="padding:4px 10px;color:{highlight};font-family:monospace">{dist_str}</td></tr>'
                )
            fib_table_html = (
                '<table style="width:100%;border-collapse:collapse;font-size:10px">'
                '<tr style="border-bottom:1px solid #1a1d2e">'
                '<th style="padding:4px 10px;color:#3a3d5c;text-align:left;font-size:8px;letter-spacing:1px">TYPE</th>'
                '<th style="padding:4px 10px;color:#3a3d5c;text-align:left;font-size:8px;letter-spacing:1px">LEVEL</th>'
                '<th style="padding:4px 10px;color:#3a3d5c;text-align:left;font-size:8px;letter-spacing:1px">PRICE</th>'
                '<th style="padding:4px 10px;color:#3a3d5c;text-align:left;font-size:8px;letter-spacing:1px">FROM NOW</th>'
                '</tr>' + "".join(fib_rows) + '</table>'
            )

        days_label = "TODAY" if days_away == 0 else ("TOMORROW" if days_away == 1 else f"in {days_away} days")
        urgency_color = "#ff4d6a" if days_away <= 1 else ("#f5c842" if days_away <= 7 else "#4d9fff")
        lc_str  = f"${latest_close:.2f}" if latest_close else "—"
        vol_str = f"≈{vol_trigger/1e6:.1f}M shares" if vol_trigger else "vol filter off"

        st.markdown(
            f'<div style="background:#0a0b14;border:1px solid {urgency_color}40;border-radius:6px;'
            f'padding:14px 18px 6px;margin-bottom:16px">'
            f'<div style="font-size:9px;color:#6b7099;letter-spacing:2px;margin-bottom:14px">'
            f'🎯 LIVE TRADE SETUP — <b style="color:#e8ecff">{symbol}</b>'
            f' · EARNINGS <b style="color:{urgency_color}">{display_next}</b>'
            f' &nbsp;<span style="color:{urgency_color};font-weight:700">{days_label}</span>'
            f'</div></div>',
            unsafe_allow_html=True,
        )

        col_s1, col_s2, col_s3 = st.columns(3)

        with col_s1:
            st.markdown(
                f'<div style="background:#0d0f17;border:1px solid #1a1d2e;border-radius:4px;padding:14px">'
                f'<div style="font-size:8px;color:#3a3d5c;letter-spacing:1.5px;margin-bottom:10px">📋 TRADE CHECKLIST</div>'
                f'<div style="font-size:10px;line-height:2.4;color:#c8cce8">'
                f'<div>☐ &nbsp;Confirm earnings is <b style="color:#f5c842">AMC</b> on {display_next}</div>'
                f'<div>☐ &nbsp;At <b style="color:#e8ecff">9:30 AM ET</b> — note the open price</div>'
                f'<div>☐ &nbsp;At <b style="color:#00e5a0">1:30 PM ET (12:30 CST)</b> — read 4H candle</div>'
                f'<div>☐ &nbsp;Green → <b style="color:#00e5a0">LONG</b> &nbsp;&nbsp; Red → <b style="color:#ff4d6a">SHORT</b></div>'
                f'<div>☐ &nbsp;Exit day vol ≥ <b style="color:#22d3ee">{vol_min:.1f}x</b> ({vol_str})</div>'
                f'<div>☐ &nbsp;Entry near fib level <b style="color:#a78bfa">±{fib_tol}%</b></div>'
                f'<div>☐ &nbsp;Enter at 1:30 PM ET · exit = <b>next day MOC</b></div>'
                f'</div>'
                f'<div style="margin-top:10px;padding:8px;background:#090b13;border-radius:3px;font-size:9px;color:#6b7099">'
                f'Last close: <b style="color:#e8ecff">{lc_str}</b>'
                f'</div></div>',
                unsafe_allow_html=True,
            )

        with col_s2:
            st.markdown(
                f'<div style="background:#0d0f17;border:1px solid #1a1d2e;border-radius:4px;padding:14px">'
                f'<div style="font-size:8px;color:#3a3d5c;letter-spacing:1.5px;margin-bottom:12px">📊 HISTORICAL EDGE ({symbol} · {stats["n"]} active trades)</div>'
                f'<div style="margin-bottom:12px">'
                f'  <div style="font-size:9px;color:#6b7099;margin-bottom:2px">LONG ({len(long_trades)} trades)</div>'
                f'  <div style="font-size:20px;font-weight:900;color:#00e5a0;font-family:monospace">{long_wr:.0f}%</div>'
                f'  <div style="font-size:9px;color:#6b7099">win rate · avg {long_avg:+.2f}% per trade</div>'
                f'  <div style="height:4px;background:#1a1d2e;border-radius:2px;margin-top:5px">'
                f'    <div style="width:{min(long_wr,100):.0f}%;height:100%;background:#00e5a0;border-radius:2px"></div></div>'
                f'</div>'
                f'<div style="margin-bottom:12px">'
                f'  <div style="font-size:9px;color:#6b7099;margin-bottom:2px">SHORT ({len(short_trades)} trades)</div>'
                f'  <div style="font-size:20px;font-weight:900;color:#ff4d6a;font-family:monospace">{short_wr:.0f}%</div>'
                f'  <div style="font-size:9px;color:#6b7099">win rate · avg {short_avg:+.2f}% per trade</div>'
                f'  <div style="height:4px;background:#1a1d2e;border-radius:2px;margin-top:5px">'
                f'    <div style="width:{min(short_wr,100):.0f}%;height:100%;background:#ff4d6a;border-radius:2px"></div></div>'
                f'</div>'
                f'<div style="padding:8px;background:#090b13;border-radius:3px;font-size:9px">'
                f'  Strongest historical direction: '
                f'  <b style="color:{"#00e5a0" if best_dir=="LONG" else "#ff4d6a"}">{best_dir} ({best_wr:.0f}% WR)</b><br>'
                f'  <span style="color:#3a3d5c">Signal still follows 4H candle on the day.</span>'
                f'</div></div>',
                unsafe_allow_html=True,
            )

        with col_s3:
            lc_label = f" · last ${latest_close:.2f}" if latest_close else ""

            # ── Compute current fib state ──────────────────
            fib_state_html = ""
            if last_swing_lo and last_swing_hi and latest_close:
                rng        = last_swing_hi - last_swing_lo
                # Where is price as % of the swing range?
                pos_pct    = (latest_close - last_swing_lo) / rng * 100 if rng > 0 else 50

                # Which fib zone is price currently sitting in?
                fib_lvls_sorted = sorted(calc_fib_levels(last_swing_lo, last_swing_hi).items(), key=lambda x: x[1])
                zone_below = None  # closest fib level below price
                zone_above = None  # closest fib level above price
                for fname, flvl in fib_lvls_sorted:
                    if flvl <= latest_close:
                        zone_below = (fname, flvl)
                    else:
                        zone_above = (fname, flvl)
                        break

                # Bull / Bear determination:
                # BULLISH: price is in a RET zone (below swing high = pulling back, support likely)
                #          and above the 50% retracement (R50.0)
                # BEARISH: price below R50.0 retracement (deep pullback, lost momentum)
                # EXTENDED: price above swing high (in extension territory = EXT zone)
                # BREAKDOWN: price below swing low

                r50  = last_swing_hi - rng * 0.5
                r618 = last_swing_hi - rng * 0.618
                r786 = last_swing_hi - rng * 0.786

                if latest_close > last_swing_hi:
                    fib_state       = "EXTENDED BULLISH"
                    state_color     = "#00e5a0"
                    state_bg        = "rgba(0,229,160,0.06)"
                    state_border    = "rgba(0,229,160,0.3)"
                    state_icon      = "🚀"
                    state_desc      = (f"Price is <b>above the prior swing high</b> (${last_swing_hi:.2f}). "
                                       f"In <b style='color:#f5c842'>Extension territory</b> — momentum is strong but price is stretched. "
                                       f"Watch EXT 127.2% (${last_swing_lo + rng*1.272:.2f}) as next resistance.")
                elif latest_close >= r50:
                    fib_state       = "BULLISH"
                    state_color     = "#00e5a0"
                    state_bg        = "rgba(0,229,160,0.06)"
                    state_border    = "rgba(0,229,160,0.3)"
                    state_icon      = "📈"
                    state_desc      = (f"Price is holding <b>above the 50% retracement</b> (${r50:.2f}). "
                                       f"In healthy pullback zone. Buyers are in control of the prior swing. "
                                       f"Key support: R 61.8% at ${r618:.2f}.")
                elif latest_close >= r618:
                    fib_state       = "NEUTRAL / DECISION ZONE"
                    state_color     = "#f5c842"
                    state_bg        = "rgba(245,200,66,0.06)"
                    state_border    = "rgba(245,200,66,0.3)"
                    state_icon      = "⚖️"
                    state_desc      = (f"Price is between the <b>50% and 61.8% retracement</b>. "
                                       f"This is the golden pocket — a make-or-break zone. "
                                       f"Hold above ${r618:.2f} = bullish. Break below = bearish shift.")
                elif latest_close >= r786:
                    fib_state       = "BEARISH"
                    state_color     = "#ff4d6a"
                    state_bg        = "rgba(255,77,106,0.06)"
                    state_border    = "rgba(255,77,106,0.3)"
                    state_icon      = "📉"
                    state_desc      = (f"Price has retraced <b>below the 61.8%</b>. "
                                       f"Sellers are dominant. Last support at R 78.6% (${r786:.2f}). "
                                       f"A break below here signals full retracement back to swing low.")
                elif latest_close >= last_swing_lo:
                    fib_state       = "STRONG BEARISH"
                    state_color     = "#ff4d6a"
                    state_bg        = "rgba(255,77,106,0.08)"
                    state_border    = "rgba(255,77,106,0.4)"
                    state_icon      = "🔻"
                    state_desc      = (f"Price is below the <b>78.6% retracement</b> — near the swing low (${last_swing_lo:.2f}). "
                                       f"Momentum has fully reversed. Watch for breakdown below ${last_swing_lo:.2f}.")
                else:
                    fib_state       = "BREAKDOWN"
                    state_color     = "#ff4d6a"
                    state_bg        = "rgba(255,77,106,0.1)"
                    state_border    = "rgba(255,77,106,0.5)"
                    state_icon      = "⚠️"
                    state_desc      = (f"Price has broken <b>below the swing low</b> (${last_swing_lo:.2f}). "
                                       f"Prior fib levels are invalidated. Bears in full control.")

                # Position bar
                bar_pct = max(0, min(100, pos_pct))
                # Nearest fib hit
                nearest = None
                min_dist = float("inf")
                for fname, flvl in fib_lvls_sorted:
                    d = abs(latest_close - flvl) / flvl * 100
                    if d < min_dist:
                        min_dist = d
                        nearest  = (fname, flvl, d)

                nearest_html = ""
                if nearest:
                    nc    = "#f5c842" if nearest[0].startswith("E") else "#4d9fff"
                    ntype = "Extension" if nearest[0].startswith("E") else "Retracement"
                    nearest_html = (
                        f'<div style="font-size:9px;color:#6b7099;margin-top:8px">'
                        f'Nearest level: <b style="color:{nc}">{ntype} {nearest[0][1:]}</b>'
                        f' at ${nearest[1]:.2f}'
                        f' <span style="color:#3a3d5c">({nearest[2]:.1f}% away)</span>'
                        f'</div>'
                    )

                fib_state_html = (
                    f'<div style="background:{state_bg};border:1px solid {state_border};'
                    f'border-radius:4px;padding:12px;margin-bottom:10px">'
                    f'<div style="font-size:8px;color:#3a3d5c;letter-spacing:1.5px;margin-bottom:6px">CURRENT FIB STATE</div>'
                    f'<div style="font-size:16px;font-weight:900;color:{state_color};margin-bottom:4px">'
                    f'{state_icon} {fib_state}</div>'
                    f'<div style="font-size:9px;color:#c8cce8;line-height:1.7;margin-bottom:8px">{state_desc}</div>'
                    f'<!-- swing position bar -->'
                    f'<div style="font-size:8px;color:#3a3d5c;margin-bottom:3px">'
                    f'POSITION IN SWING: {pos_pct:.0f}% &nbsp;(low ${last_swing_lo:.2f} → high ${last_swing_hi:.2f})</div>'
                    f'<div style="position:relative;height:8px;background:#1a1d2e;border-radius:4px">'
                    f'  <div style="position:absolute;left:{bar_pct:.0f}%;top:-2px;width:12px;height:12px;'
                    f'  border-radius:50%;background:{state_color};transform:translateX(-50%);'
                    f'  border:2px solid #07080d"></div>'
                    f'  <!-- fib ticks on bar -->'
                    f'  <div style="position:absolute;left:23.6%;top:0;width:1px;height:100%;background:#4d9fff40"></div>'
                    f'  <div style="position:absolute;left:38.2%;top:0;width:1px;height:100%;background:#4d9fff40"></div>'
                    f'  <div style="position:absolute;left:50%;top:0;width:1px;height:100%;background:#4d9fff60"></div>'
                    f'  <div style="position:absolute;left:61.8%;top:0;width:1px;height:100%;background:#4d9fff80"></div>'
                    f'  <div style="position:absolute;left:78.6%;top:0;width:1px;height:100%;background:#4d9fff40"></div>'
                    f'</div>'
                    f'{nearest_html}'
                    f'</div>'
                )

            st.markdown(
                f'<div style="background:#0d0f17;border:1px solid #1a1d2e;border-radius:4px;padding:14px">'
                f'<div style="font-size:8px;color:#3a3d5c;letter-spacing:1.5px;margin-bottom:8px">'
                f'📐 FIB STATE &amp; LEVELS{lc_label}</div>'
                + fib_state_html
                + (fib_table_html if fib_table_html else '<div style="color:#3a3d5c;font-size:10px;padding:8px">No swing data available</div>')
                + f'<div style="font-size:8px;margin-top:10px;line-height:2;border-top:1px solid #1a1d2e;padding-top:8px">'
                f'<span style="background:#4d9fff15;color:#4d9fff;border:1px solid #4d9fff30;padding:1px 6px;border-radius:2px;font-weight:700;font-size:8px">RET</span>'
                f' <span style="color:#6b7099">Retracement</span> — price pulling back <i>into</i> prior range<br>'
                f'<span style="background:#f5c84215;color:#f5c842;border:1px solid #f5c84230;padding:1px 6px;border-radius:2px;font-weight:700;font-size:8px">EXT</span>'
                f' <span style="color:#6b7099">Extension</span> — price extended <i>beyond</i> prior range<br>'
                f'<span style="color:#00e5a0">■</span> <span style="color:#6b7099">green = within ±{fib_tol}% of entry price</span>'
                f'</div></div>',
                unsafe_allow_html=True,
            )

        st.markdown("---")

    # ── Tabs ──────────────────────────────────────────────
    tab_curve, tab_bars, tab_fib, tab_log = st.tabs([
        "📈 Equity Curve", "📊 P&L per Trade", "📐 Fib Analysis", "📋 Trade Log"
    ])

    with tab_curve:
        if not active_trades.empty:
            st.plotly_chart(equity_chart(active_trades, stats), use_container_width=True)
            st.caption(f"$100 compounded · {len(active_trades)} trades · entry = 4H candle close on report day")

    with tab_bars:
        st.plotly_chart(pnl_bar_chart(all_trades), use_container_width=True)
        st.caption("Colored = active trades · Grey = filtered out")

    with tab_fib:
        col_a, col_b = st.columns(2)
        with col_a:
            st.markdown("**Entry position within prior earnings swing**")
            for _, row in all_trades.iterrows():
                if not row["swing_lo"] or not row["swing_hi"]:
                    continue
                rng = row["swing_hi"] - row["swing_lo"]
                ep  = (row["entry"] - row["swing_lo"]) / rng * 100 if rng > 0 else 50

                dir_color = GREEN if row["direction"] == "LONG" else RED
                fib_badge = ""
                if row["fib_hit"]:
                    fname     = row["fib_hit"][0]
                    is_ext    = fname.startswith("E")
                    fc        = YELLOW if is_ext else BLUE
                    type_word = "Ext" if is_ext else "Ret"
                    fib_badge = (
                        f' <span style="font-size:8px;padding:1px 5px;border-radius:2px;font-weight:700;'
                        f'background:{fc}15;color:{fc};border:1px solid {fc}30">{type_word}</span>'
                        f' <span style="color:{fc};font-size:9px">{fname[1:]}</span>'
                    )

                opacity = 1.0 if row["passes_all"] else 0.35
                st.markdown(
                    f'<div style="opacity:{opacity};margin-bottom:6px;padding:8px 12px;'
                    f'background:#0d0f17;border-left:2px solid {"#1a1d2e" if not row["passes_all"] else dir_color};'
                    f'border:1px solid #1a1d2e;border-radius:3px">'
                    f'<div style="display:flex;justify-content:space-between;margin-bottom:5px">'
                    f'<span style="color:#e8ecff;font-size:10px;font-weight:700">{row["q"]}</span>'
                    f'<span style="color:{dir_color};font-size:9px;font-weight:700">{row["direction"]}{fib_badge}</span>'
                    f'</div>'
                    f'<div style="height:5px;background:#1a1d2e;border-radius:2px;position:relative">'
                    f'<div style="position:absolute;left:{min(100,max(0,ep)):.0f}%;top:-3px;width:11px;height:11px;'
                    f'border-radius:50%;background:{"#00e5a0" if row["fib_hit"] else "#ff4d6a"};'
                    f'transform:translateX(-50%);border:2px solid #07080d"></div>'
                    f'</div>'
                    f'<div style="display:flex;justify-content:space-between;font-size:8px;color:#3a3d5c;margin-top:4px">'
                    f'<span>${row["swing_lo"]:.2f}</span>'
                    f'<span style="color:#6b7099">${row["entry"]:.2f}</span>'
                    f'<span>${row["swing_hi"]:.2f}</span>'
                    f'</div></div>',
                    unsafe_allow_html=True,
                )

        with col_b:
            st.markdown("**Fib level hit frequency**")
            fig_fib = fib_freq_chart(all_trades)
            if fig_fib:
                st.plotly_chart(fig_fib, use_container_width=True)
            else:
                st.info(f"No fib hits within ±{fib_tol}% tolerance.")

            st.markdown("**4H candle direction accuracy (active trades)**")
            for d, dc in [("LONG", GREEN), ("SHORT", RED)]:
                dt = active_trades[active_trades["direction"] == d]
                if dt.empty: continue
                dw = dt["win"].sum()
                wr = dw / len(dt) * 100
                st.markdown(
                    f'<div style="margin-bottom:8px">'
                    f'<div style="display:flex;justify-content:space-between;font-size:9px;margin-bottom:3px">'
                    f'<span style="color:{dc};font-weight:700">{d}</span>'
                    f'<span style="color:#6b7099">{dw}W/{len(dt)-dw}L · {wr:.0f}%</span></div>'
                    f'<div style="height:4px;background:#1a1d2e;border-radius:2px">'
                    f'<div style="width:{wr:.0f}%;height:100%;background:{dc};border-radius:2px"></div>'
                    f'</div></div>',
                    unsafe_allow_html=True,
                )

    with tab_log:
        display_df = all_trades[[
            "q","report_date","entry_date","exit_date","direction","candle_type",
            "entry","exit","pnl_pct","vol_ratio","passes_all"
        ]].copy()

        def style_row(row):
            base = "color: #c8cce8; font-family: monospace; font-size: 11px;"
            if not row["passes_all"]:
                return [base + "opacity: 0.35;"] * len(row)
            if row["pnl_pct"] > 0:
                return [base] * len(row)
            return [base] * len(row)

        st.dataframe(
            display_df.style
                .format({
                    "entry":    "${:.2f}",
                    "exit":     "${:.2f}",
                    "pnl_pct":  "{:+.2f}%",
                    "vol_ratio":"{:.2f}x",
                })
                .apply(style_row, axis=1)
                .applymap(lambda v: f"color: {GREEN}; font-weight: bold" if isinstance(v, str) and "+" in v and "%" in v and float(v.replace("+","").replace("%","")) > 0 else (f"color: {RED}; font-weight: bold" if isinstance(v, str) and "%" in v and v.startswith("-") else ""), subset=["pnl_pct"])
                .applymap(lambda v: f"color: {GREEN}; font-weight: bold" if v == "LONG" else (f"color: {RED}; font-weight: bold" if v == "SHORT" else ""), subset=["direction"]),
            use_container_width=True,
            height=450,
        )

        # Footer stats
        st.markdown(
            f'<div style="margin-top:8px;padding:10px;background:#0d0f17;border:1px solid #1a1d2e;'
            f'border-radius:4px;font-size:10px;color:#6b7099;display:flex;gap:20px">'
            f'<span>Compounded ({len(active_trades)} trades):</span>'
            f'<b style="color:{"#00e5a0" if profit else "#ff4d6a"}">{stats["total_return"]:+.2f}%</b>'
            f'</div>',
            unsafe_allow_html=True,
        )

    st.markdown("---")
    st.markdown(
        '<div style="font-size:8px;color:#3a3d5c;line-height:2">'
        '⚠ Data from Polygon.io (real adjusted OHLCV). Earnings dates from SEC filing dates via Polygon vX financials — '
        'may differ slightly from actual announcement date. 4H candle = 9:30–1:30 ET on report day. '
        'Fib swing = high/low between prior earnings exit and current report date. '
        'Exit = next trading day close. No slippage/commission. Not financial advice.'
        '</div>',
        unsafe_allow_html=True,
    )


# ╔══════════════════════════════════════════════════════════════════════════════╗
# ║ TAB 5: TRADE TRACKER                                                        ║
# ╚══════════════════════════════════════════════════════════════════════════════╝
with tab_trades:
    st.markdown(
        '<div style="background:linear-gradient(135deg,#0a0b14,#131625);border:1px solid #1a1d2e;'
        'border-radius:8px;padding:18px 24px;margin-bottom:16px">'
        '<div style="display:flex;align-items:center;gap:12px">'
        '<div style="font-size:28px">📋</div>'
        '<div>'
        '<div style="font-size:16px;font-weight:700;color:#e8ecff">Trade Tracker</div>'
        '<div style="font-size:10px;color:#6b7099">Track your trades, monitor targets, and review performance. '
        'Trades are saved locally in a SQLite database.</div>'
        '</div></div></div>',
        unsafe_allow_html=True,
    )

    # ── Refresh open trades: check current prices against targets ──
    all_trades = get_all_trades()
    open_trades = [t for t in all_trades if t["status"] == "OPEN"]
    closed_trades = [t for t in all_trades if t["status"] == "CLOSED"]

    # ── Stats Summary ──
    if all_trades:
        total_trades = len(all_trades)
        n_open = len(open_trades)
        n_closed = len(closed_trades)
        n_wins = sum(1 for t in closed_trades if t.get("outcome") == "WIN")
        n_losses = sum(1 for t in closed_trades if t.get("outcome") == "LOSS")
        win_rate = (n_wins / n_closed * 100) if n_closed > 0 else 0
        avg_pnl = sum(t.get("pnl_pct", 0) for t in closed_trades) / n_closed if n_closed > 0 else 0
        total_pnl = sum(t.get("pnl_pct", 0) for t in closed_trades)
        t1_hits = sum(1 for t in all_trades if t.get("t1_hit"))
        t2_hits = sum(1 for t in all_trades if t.get("t2_hit"))
        stop_hits = sum(1 for t in all_trades if t.get("stop_hit"))

        st_col1, st_col2, st_col3, st_col4, st_col5 = st.columns(5)
        with st_col1:
            st.metric("Total Trades", total_trades, f"{n_open} open")
        with st_col2:
            wr_delta = f"{n_wins}W / {n_losses}L"
            st.metric("Win Rate", f"{win_rate:.0f}%", wr_delta)
        with st_col3:
            pnl_color = "normal" if total_pnl >= 0 else "inverse"
            st.metric("Total P&L", f"{total_pnl:+.1f}%", f"avg {avg_pnl:+.1f}%", delta_color=pnl_color)
        with st_col4:
            st.metric("T1 Hits", t1_hits, f"of {total_trades}")
        with st_col5:
            st.metric("T2 Hits", t2_hits, f"stops: {stop_hits}")

    # ── Check Targets Button ──
    if open_trades:
        st.markdown("---")
        st.markdown("### 📡 Open Positions")
        if st.button("🔄 Check All Targets (Live Prices)", key="check_targets_btn"):
            progress = st.progress(0)
            status_text = st.empty()
            for idx, trade in enumerate(open_trades):
                tkr = trade["ticker"]
                status_text.text(f"Checking {tkr}...")
                progress.progress((idx + 1) / len(open_trades))
                try:
                    if YFINANCE_AVAILABLE:
                        stock = yf.Ticker(tkr)
                        hist = stock.history(period="1mo")
                        if not hist.empty:
                            current = float(hist["Close"].iloc[-1])
                            entry_dt = trade["entry_date"]
                            # Filter history since entry date
                            since_entry = hist[hist.index >= pd.Timestamp(entry_dt)]
                            if not since_entry.empty:
                                hi = float(since_entry["High"].max())
                                lo = float(since_entry["Low"].min())
                            else:
                                hi = float(hist["High"].max())
                                lo = float(hist["Low"].min())
                            updates = check_trade_targets(trade, current, hi, lo)
                            if updates:
                                update_trade(trade["id"], **updates)
                except Exception:
                    pass
            status_text.empty()
            progress.empty()
            st.success("✅ All open trades checked against live prices!")
            st.rerun()

        # ── Open Trades Table ──
        for trade in open_trades:
            tid = trade["id"]
            tkr = trade["ticker"]
            direction = trade["direction"]
            dir_color = "#00e5a0" if direction == "LONG" else "#ff4d6a"
            dir_emoji = "🟢" if direction == "LONG" else "🔴"
            entry_px = trade["entry_price"]
            stop_px = trade.get("stop_loss")
            t1_px = trade.get("target1")
            t2_px = trade.get("target2")
            t1_hit = "✅" if trade.get("t1_hit") else "⬜"
            t2_hit = "✅" if trade.get("t2_hit") else "⬜"
            stop_hit_flag = "🛑" if trade.get("stop_hit") else ""
            hi_since = trade.get("high_since_entry")
            lo_since = trade.get("low_since_entry")
            e_date = trade.get("entry_date", "")
            notes = trade.get("notes", "") or ""
            confidence = trade.get("confidence", "")
            signals = trade.get("signals", "")
            t1d = trade.get("t1_trading_days")
            t2d = trade.get("t2_trading_days")

            # Calculate trading days elapsed
            try:
                entry_date_obj = datetime.strptime(e_date, "%Y-%m-%d").date()
                days_elapsed = sum(1 for d in range((date.today() - entry_date_obj).days + 1)
                                   if (entry_date_obj + timedelta(days=d)).weekday() < 5) - 1
                days_str = f"{days_elapsed} td"
            except Exception:
                days_str = "?"
                days_elapsed = 0

            # P&L estimate
            if hi_since and lo_since:
                if direction == "LONG":
                    unrealized = round((hi_since - entry_px) / entry_px * 100, 1) if hi_since else 0
                    pnl_str = f"High: ${hi_since:.2f} ({unrealized:+.1f}%)"
                else:
                    unrealized = round((entry_px - lo_since) / entry_px * 100, 1) if lo_since else 0
                    pnl_str = f"Low: ${lo_since:.2f} ({unrealized:+.1f}%)"
                pnl_color = "#00e5a0" if unrealized > 0 else "#ff4d6a"
            else:
                pnl_str = "Check targets to update"
                pnl_color = "#6b7099"
                unrealized = 0

            # Time estimate status
            time_status = ""
            if t1d and days_elapsed > 0:
                if days_elapsed > t1d and not trade.get("t1_hit"):
                    time_status = f' · ⚠️ Past T1 estimate ({t1d}td)'
                elif days_elapsed <= t1d:
                    time_status = f' · ⏳ {t1d - days_elapsed}td to T1 est.'

            st.markdown(
                f'<div style="background:#0d0f17;border:1px solid {dir_color}40;'
                f'border-radius:8px;padding:16px;margin-bottom:12px">'
                f'<div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:10px">'
                f'<div>'
                f'<span style="font-size:18px;font-weight:800;color:#e8ecff">{dir_emoji} {tkr}</span>'
                f'<span style="font-size:11px;color:{dir_color};font-weight:700;margin-left:10px">{direction}</span>'
                f'<span style="font-size:10px;color:#6b7099;margin-left:10px">#{tid} · {e_date} · {days_str}{time_status}</span>'
                f'</div>'
                f'<div style="font-size:10px;color:#6b7099">{stop_hit_flag} {confidence}</div>'
                f'</div>'
                f'<div style="display:flex;gap:16px;flex-wrap:wrap;margin-bottom:8px">'
                f'<div style="background:#090b13;padding:8px 14px;border-radius:4px">'
                f'<div style="font-size:9px;color:#6b7099">ENTRY</div>'
                f'<div style="font-size:14px;color:#e8ecff;font-weight:700">${entry_px:.2f}</div></div>'
                f'<div style="background:#090b13;padding:8px 14px;border-radius:4px">'
                f'<div style="font-size:9px;color:#ff4d6a">STOP</div>'
                f'<div style="font-size:14px;color:#ff4d6a;font-weight:700">'
                f'{"$" + f"{stop_px:.2f}" if stop_px else "N/A"}</div></div>'
                f'<div style="background:#090b13;padding:8px 14px;border-radius:4px;'
                f'{"border:1px solid #00e5a060" if trade.get("t1_hit") else ""}">'
                f'<div style="font-size:9px;color:#00e5a0">TARGET 1 {t1_hit}</div>'
                f'<div style="font-size:14px;color:#00e5a0;font-weight:700">'
                f'{"$" + f"{t1_px:.2f}" if t1_px else "N/A"}</div></div>'
                f'<div style="background:#090b13;padding:8px 14px;border-radius:4px;'
                f'{"border:1px solid #00e5a060" if trade.get("t2_hit") else ""}">'
                f'<div style="font-size:9px;color:#00e5a0">TARGET 2 {t2_hit}</div>'
                f'<div style="font-size:14px;color:#00e5a0;font-weight:700">'
                f'{"$" + f"{t2_px:.2f}" if t2_px else "N/A"}</div></div>'
                f'<div style="background:#090b13;padding:8px 14px;border-radius:4px">'
                f'<div style="font-size:9px;color:{pnl_color}">UNREALIZED</div>'
                f'<div style="font-size:11px;color:{pnl_color};font-weight:600">{pnl_str}</div></div>'
                f'</div>'
                f'{"<div style=" + chr(34) + "font-size:10px;color:#6b7099;margin-top:4px" + chr(34) + ">" + signals + "</div>" if signals else ""}'
                f'{"<div style=" + chr(34) + "font-size:10px;color:#a78bfa;margin-top:4px" + chr(34) + ">📝 " + notes + "</div>" if notes else ""}'
                f'</div>',
                unsafe_allow_html=True,
            )

            # Close Trade controls
            close_col1, close_col2, close_col3 = st.columns([2, 1, 1])
            with close_col1:
                exit_px = st.number_input(f"Exit price", min_value=0.01, value=float(entry_px),
                                          step=0.01, key=f"exit_px_{tid}")
            with close_col2:
                if st.button(f"✅ Close Trade", key=f"close_{tid}"):
                    close_trade(tid, exit_px)
                    st.success(f"Trade #{tid} {tkr} closed @ ${exit_px:.2f}")
                    st.rerun()
            with close_col3:
                if st.button(f"🗑️ Delete", key=f"del_{tid}"):
                    delete_trade(tid)
                    st.warning(f"Trade #{tid} deleted.")
                    st.rerun()
    else:
        if not all_trades:
            st.info("No trades tracked yet. Go to **📅 Stock Analysis (with Options)**, fetch a stock, and click **📌 Track This Trade** to get started.")
        else:
            st.success("No open positions. All trades have been closed.")

    # ── Closed Trades History ──
    if closed_trades:
        st.markdown("---")
        st.markdown("### 📊 Trade History")

        hist_data = []
        for t in closed_trades:
            pnl = t.get("pnl_pct", 0)
            pnl_color_class = "WIN" if pnl > 0 else ("LOSS" if pnl < 0 else "BE")
            hist_data.append({
                "ID": t["id"],
                "Ticker": t["ticker"],
                "Dir": t["direction"],
                "Entry": f"${t['entry_price']:.2f}",
                "Exit": f"${t['exit_price']:.2f}" if t.get("exit_price") else "—",
                "P&L": f"{pnl:+.1f}%",
                "Outcome": t.get("outcome", "—"),
                "T1 Hit": "✅" if t.get("t1_hit") else "❌",
                "T2 Hit": "✅" if t.get("t2_hit") else "❌",
                "Stop Hit": "🛑" if t.get("stop_hit") else "—",
                "Entry Date": t.get("entry_date", ""),
                "Exit Date": t.get("exit_date", ""),
                "Signals": t.get("signals", "")[:40],
            })

        hist_df = pd.DataFrame(hist_data)
        st.dataframe(hist_df, use_container_width=True, hide_index=True)

        # ── Performance Breakdown ──
        if len(closed_trades) >= 2:
            perf_col1, perf_col2 = st.columns(2)

            with perf_col1:
                # Cumulative P&L chart
                cum_pnl = []
                running = 0
                for t in reversed(closed_trades):
                    running += t.get("pnl_pct", 0)
                    cum_pnl.append({"Date": t.get("exit_date", ""), "P&L %": round(running, 2)})
                cum_df = pd.DataFrame(cum_pnl)
                fig = px.line(cum_df, x="Date", y="P&L %", title="Cumulative P&L %")
                fig.update_layout(
                    template="plotly_dark",
                    paper_bgcolor="rgba(0,0,0,0)",
                    plot_bgcolor="rgba(0,0,0,0)",
                    height=300,
                    margin=dict(l=40, r=20, t=40, b=30),
                )
                fig.update_traces(line_color="#00e5a0" if running >= 0 else "#ff4d6a")
                st.plotly_chart(fig, use_container_width=True)

            with perf_col2:
                # Win/Loss pie
                outcomes = {"WIN": n_wins, "LOSS": n_losses,
                            "BREAKEVEN": sum(1 for t in closed_trades if t.get("outcome") == "BREAKEVEN")}
                outcomes = {k: v for k, v in outcomes.items() if v > 0}
                if outcomes:
                    fig2 = px.pie(
                        names=list(outcomes.keys()),
                        values=list(outcomes.values()),
                        title="Outcomes",
                        color_discrete_map={"WIN": "#00e5a0", "LOSS": "#ff4d6a", "BREAKEVEN": "#f5c842"},
                    )
                    fig2.update_layout(
                        template="plotly_dark",
                        paper_bgcolor="rgba(0,0,0,0)",
                        height=300,
                        margin=dict(l=20, r=20, t=40, b=30),
                    )
                    st.plotly_chart(fig2, use_container_width=True)

            # Target accuracy
            st.markdown("#### 🎯 Target Accuracy")
            acc_col1, acc_col2, acc_col3 = st.columns(3)
            with acc_col1:
                t1_rate = sum(1 for t in closed_trades if t.get("t1_hit")) / n_closed * 100
                st.metric("T1 Hit Rate", f"{t1_rate:.0f}%",
                          f"{sum(1 for t in closed_trades if t.get('t1_hit'))}/{n_closed}")
            with acc_col2:
                t2_rate = sum(1 for t in closed_trades if t.get("t2_hit")) / n_closed * 100
                st.metric("T2 Hit Rate", f"{t2_rate:.0f}%",
                          f"{sum(1 for t in closed_trades if t.get('t2_hit'))}/{n_closed}")
            with acc_col3:
                stop_rate = sum(1 for t in closed_trades if t.get("stop_hit")) / n_closed * 100
                st.metric("Stop Hit Rate", f"{stop_rate:.0f}%",
                          f"{sum(1 for t in closed_trades if t.get('stop_hit'))}/{n_closed}")

    # ── Manual Trade Entry ──
    st.markdown("---")
    st.markdown("### ✏️ Add Trade Manually")
    with st.expander("Enter trade details", expanded=False):
        m_col1, m_col2, m_col3 = st.columns(3)
        with m_col1:
            m_ticker = st.text_input("Ticker", key="manual_ticker", placeholder="AAPL")
            m_direction = st.selectbox("Direction", ["LONG", "SHORT"], key="manual_dir")
        with m_col2:
            m_entry = st.number_input("Entry Price", min_value=0.01, value=100.0, step=0.01, key="manual_entry")
            m_stop = st.number_input("Stop Loss", min_value=0.0, value=0.0, step=0.01, key="manual_stop")
        with m_col3:
            m_t1 = st.number_input("Target 1", min_value=0.0, value=0.0, step=0.01, key="manual_t1")
            m_t2 = st.number_input("Target 2", min_value=0.0, value=0.0, step=0.01, key="manual_t2")
        m_notes = st.text_input("Notes", key="manual_notes", placeholder="Optional notes…")
        if st.button("💾 Save Manual Trade", key="save_manual_trade"):
            if m_ticker.strip():
                save_trade(
                    ticker=m_ticker.strip().upper(),
                    direction=m_direction,
                    entry_price=m_entry,
                    stop_loss=m_stop if m_stop > 0 else None,
                    target1=m_t1 if m_t1 > 0 else None,
                    target2=m_t2 if m_t2 > 0 else None,
                    notes=m_notes if m_notes else None,
                )
                st.success(f"✅ Saved {m_direction} {m_ticker.strip().upper()} @ ${m_entry:.2f}")
                st.rerun()
            else:
                st.warning("Please enter a ticker symbol.")

# ╔══════════════════════════════════════════════════════════════════════════════╗
# ║ TAB 6: MY HOLDINGS                                                          ║
# ╚══════════════════════════════════════════════════════════════════════════════╝
with tab_holdings:
    st.markdown(
        '<div style="background:linear-gradient(135deg,#0a0b14,#131625);border:1px solid #1a1d2e;'
        'border-radius:8px;padding:18px 24px;margin-bottom:16px">'
        '<div style="font-size:18px;font-weight:bold;color:#e8ecff;margin-bottom:4px">💼 My Holdings</div>'
        '<div style="font-size:10px;color:#6b7099">Track your portfolio positions with live technical analysis, '
        'targets, and stop-loss levels.</div>'
        '</div>',
        unsafe_allow_html=True
    )

    # ── Add New Holding ──
    st.markdown("#### ➕ Add Holding")
    h_col1, h_col2, h_col3 = st.columns(3)
    with h_col1:
        h_ticker = st.text_input("Ticker", key="hold_ticker", placeholder="e.g. AAPL")
    with h_col2:
        h_qty = st.number_input("Quantity", min_value=0.01, value=1.0, step=1.0, key="hold_qty")
    with h_col3:
        h_avg = st.number_input("Avg Cost ($)", min_value=0.01, value=100.0, step=0.01, key="hold_avg")
    h_notes = st.text_input("Notes (optional)", key="hold_notes", placeholder="e.g. Long-term hold")
    if st.button("💾 Add Holding", key="add_holding_btn"):
        if h_ticker.strip():
            save_holding(h_ticker, h_qty, h_avg, h_notes if h_notes else None)
            st.success(f"✅ Added {h_qty} shares of {h_ticker.strip().upper()} @ ${h_avg:.2f}")
            st.rerun()
        else:
            st.warning("Please enter a ticker symbol.")

    # ── Import / Export ──
    st.markdown("---")
    imp_col, exp_col = st.columns(2)
    with imp_col:
        st.markdown("#### 📥 Import Holdings")
        uploaded = st.file_uploader("Upload CSV or Excel (columns: ticker, quantity, avg_cost, notes)",
                                    type=["csv", "xlsx", "xls"], key="import_holdings_file")
        if uploaded is not None:
            try:
                if uploaded.name.endswith(".csv"):
                    imp_df = pd.read_csv(uploaded)
                else:
                    imp_df = pd.read_excel(uploaded)
                # Normalize column names
                imp_df.columns = [c.strip().lower().replace(" ", "_") for c in imp_df.columns]
                required = {"ticker", "quantity", "avg_cost"}
                if not required.issubset(set(imp_df.columns)):
                    st.error(f"Missing columns. Need: ticker, quantity, avg_cost. Got: {list(imp_df.columns)}")
                else:
                    imported = 0
                    for _, row in imp_df.iterrows():
                        t = str(row["ticker"]).strip().upper()
                        q = float(row["quantity"])
                        a = float(row["avg_cost"])
                        n = str(row.get("notes", "")).strip() if "notes" in imp_df.columns and pd.notna(row.get("notes")) else None
                        if t and q > 0 and a > 0:
                            save_holding(t, q, a, n)
                            imported += 1
                    st.success(f"✅ Imported {imported} holdings!")
                    st.rerun()
            except Exception as e:
                st.error(f"Import error: {e}")
    with exp_col:
        st.markdown("#### 📤 Export Holdings")
        current_holdings = get_holdings()
        if current_holdings:
            exp_df = pd.DataFrame(current_holdings)[["ticker", "quantity", "avg_cost", "notes", "added_date"]]
            # CSV download
            csv_data = exp_df.to_csv(index=False).encode("utf-8")
            st.download_button("⬇️ Download as CSV", csv_data, "my_holdings.csv", "text/csv", key="exp_csv")
            # Excel download
            excel_buf = io.BytesIO()
            with pd.ExcelWriter(excel_buf, engine="openpyxl") as writer:
                exp_df.to_excel(writer, index=False, sheet_name="Holdings")
            st.download_button("⬇️ Download as Excel", excel_buf.getvalue(), "my_holdings.xlsx",
                               "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", key="exp_xlsx")
        else:
            st.info("No holdings to export.")

    st.markdown("---")

    # ── Current Holdings ──
    holdings = get_holdings()
    if not holdings:
        st.info("No holdings yet. Add your first position above.")
    else:
        # Analyze button
        analyze_btn = st.button("🔍 Analyze All Holdings", key="analyze_holdings_btn")

        # Portfolio summary header
        st.markdown("#### 📊 Portfolio Positions")

        # Analyze holdings when button clicked
        if analyze_btn:
            analysis_results = {}
            progress = st.progress(0)
            for idx, h in enumerate(holdings):
                progress.progress((idx + 1) / len(holdings))
                result = scan_single_stock(h["ticker"], api_key, api_secret, data_source)
                if result:
                    analysis_results[h["ticker"]] = result
            progress.empty()
            st.session_state["_holdings_analysis"] = analysis_results

        analysis = st.session_state.get("_holdings_analysis", {})

        total_invested = 0.0
        total_current = 0.0

        # Build per-holding data with analysis
        holdings_data = []
        for h in holdings:
            ticker = h["ticker"]
            qty = h["quantity"]
            avg_cost = h["avg_cost"]
            cost_basis = qty * avg_cost
            total_invested += cost_basis

            a = analysis.get(ticker)
            current_price = a["price"] if a else None

            if current_price:
                market_val = qty * current_price
                total_current += market_val
                pnl = market_val - cost_basis
                pnl_pct = (current_price - avg_cost) / avg_cost * 100
                pnl_color = "#00e5a0" if pnl >= 0 else "#ff4d6a"
                pnl_sign = "+" if pnl >= 0 else ""
            else:
                market_val = cost_basis
                total_current += market_val
                pnl = 0
                pnl_pct = 0
                pnl_color = "#6b7099"
                pnl_sign = ""

            # Determine category
            if a:
                verdict = a["verdict"]
                if "BULLISH" in verdict:
                    category = "bullish"
                elif "BEARISH" in verdict:
                    category = "bearish"
                else:
                    category = "neutral"
            else:
                category = "unanalyzed"

            holdings_data.append({
                "h": h, "ticker": ticker, "qty": qty, "avg_cost": avg_cost,
                "cost_basis": cost_basis, "market_val": market_val, "current_price": current_price,
                "pnl": pnl, "pnl_pct": pnl_pct, "pnl_color": pnl_color, "pnl_sign": pnl_sign,
                "a": a, "category": category,
            })

        def _render_holding_card(hd):
            """Render a single holding card."""
            ticker = hd["ticker"]
            qty = hd["qty"]
            avg_cost = hd["avg_cost"]
            cost_basis = hd["cost_basis"]
            market_val = hd["market_val"]
            current_price = hd["current_price"]
            pnl = hd["pnl"]
            pnl_pct = hd["pnl_pct"]
            pnl_color = hd["pnl_color"]
            pnl_sign = hd["pnl_sign"]
            a = hd["a"]
            h = hd["h"]

            if a:
                verdict = a["verdict"]
                confidence = a["confidence"]
                score = a["score"]
                signals = a.get("signals", "")
                if "BULLISH" in verdict:
                    v_color = "#00e5a0"
                    v_icon = "🟢"
                elif "BEARISH" in verdict:
                    v_color = "#ff4d6a"
                    v_icon = "🔴"
                else:
                    v_color = "#f0c040"
                    v_icon = "🟡"

                stop_html = f'${a["stop_loss"]:.2f}' if a.get("stop_loss") else "N/A"
                t1_html = f'${a["target1"]:.2f}' if a.get("target1") else "N/A"
                t2_html = f'${a["target2"]:.2f}' if a.get("target2") else "N/A"
                t1_days = f'~{a["t1_days"]}td' if a.get("t1_days") else ""

                analysis_block = (
                    f'<div style="margin-top:6px;padding:6px;background:#0a0b14;border-radius:4px">'
                    f'<div style="color:{v_color};font-weight:bold;font-size:11px">{v_icon} {verdict} · {confidence}</div>'
                    f'<div style="font-size:9px;color:#6b7099;margin-top:2px">Score: {score}</div>'
                    f'<div style="font-size:9px;color:#6b7099;margin-top:2px">{signals}</div>'
                    f'<div style="font-size:9px;color:#6b7099;margin-top:4px">'
                    f'Stop: <span style="color:#ff4d6a">{stop_html}</span> · '
                    f'T1: <span style="color:#00e5a0">{t1_html}</span> {t1_days} · '
                    f'T2: <span style="color:#00e5a0">{t2_html}</span>'
                    f'</div>'
                    f'</div>'
                )
            else:
                analysis_block = (
                    '<div style="margin-top:6px;font-size:9px;color:#6b7099;font-style:italic">'
                    'Click Analyze to see signals</div>'
                )

            price_display = f'${current_price:.2f}' if current_price else 'N/A'

            st.markdown(
                f'<div style="background:#0d0f17;border:1px solid #1a1d2e;padding:10px;border-radius:6px;margin-bottom:6px">'
                f'<div style="display:flex;justify-content:space-between;align-items:center">'
                f'<span style="font-size:14px;font-weight:bold;color:#e8ecff">{ticker}</span>'
                f'<span style="font-size:12px;font-weight:bold;color:{pnl_color}">{pnl_sign}{pnl_pct:.1f}%</span>'
                f'</div>'
                f'<div style="font-size:10px;color:#6b7099;margin-top:2px">'
                f'{qty:.2f} @ ${avg_cost:.2f} → {price_display}'
                f'</div>'
                f'<div style="font-size:10px;color:#6b7099">'
                f'P&L: <span style="color:{pnl_color}">{pnl_sign}${abs(pnl):,.2f}</span> · '
                f'Cost: ${cost_basis:,.2f} · Val: ${market_val:,.2f}'
                f'</div>'
                f'{analysis_block}'
                f'</div>',
                unsafe_allow_html=True
            )
            if st.button("🗑️ Remove", key=f"del_hold_{h['id']}"):
                delete_holding(h["id"])
                if "_holdings_analysis" in st.session_state:
                    st.session_state["_holdings_analysis"].pop(ticker, None)
                st.rerun()

        # If analysis is available, display in 3 columns by verdict
        if analysis:
            bullish_h = [hd for hd in holdings_data if hd["category"] == "bullish"]
            neutral_h = [hd for hd in holdings_data if hd["category"] in ("neutral", "unanalyzed")]
            bearish_h = [hd for hd in holdings_data if hd["category"] == "bearish"]

            col_b, col_n, col_r = st.columns(3)
            with col_b:
                st.markdown(
                    '<div style="background:#00e5a015;border:1px solid #00e5a040;padding:10px;border-radius:8px;margin-bottom:10px">'
                    '<div style="font-size:14px;font-weight:bold;color:#00e5a0">🟢 Bullish</div>'
                    f'<div style="font-size:10px;color:#6b7099">{len(bullish_h)} position{"s" if len(bullish_h)!=1 else ""}</div>'
                    '</div>',
                    unsafe_allow_html=True
                )
                if bullish_h:
                    for hd in bullish_h:
                        _render_holding_card(hd)
                else:
                    st.caption("No bullish positions.")

            with col_n:
                st.markdown(
                    '<div style="background:#f0c04015;border:1px solid #f0c04040;padding:10px;border-radius:8px;margin-bottom:10px">'
                    '<div style="font-size:14px;font-weight:bold;color:#f0c040">🟡 Neutral</div>'
                    f'<div style="font-size:10px;color:#6b7099">{len(neutral_h)} position{"s" if len(neutral_h)!=1 else ""}</div>'
                    '</div>',
                    unsafe_allow_html=True
                )
                if neutral_h:
                    for hd in neutral_h:
                        _render_holding_card(hd)
                else:
                    st.caption("No neutral positions.")

            with col_r:
                st.markdown(
                    '<div style="background:#ff4d6a15;border:1px solid #ff4d6a40;padding:10px;border-radius:8px;margin-bottom:10px">'
                    '<div style="font-size:14px;font-weight:bold;color:#ff4d6a">🔴 Bearish</div>'
                    f'<div style="font-size:10px;color:#6b7099">{len(bearish_h)} position{"s" if len(bearish_h)!=1 else ""}</div>'
                    '</div>',
                    unsafe_allow_html=True
                )
                if bearish_h:
                    for hd in bearish_h:
                        _render_holding_card(hd)
                else:
                    st.caption("No bearish positions.")
        else:
            # Not yet analyzed — show flat list
            for hd in holdings_data:
                _render_holding_card(hd)

        # Portfolio totals
        if analysis:
            total_pnl = total_current - total_invested
            total_pnl_pct = (total_pnl / total_invested * 100) if total_invested > 0 else 0
            pnl_color = "#00e5a0" if total_pnl >= 0 else "#ff4d6a"
            pnl_sign = "+" if total_pnl >= 0 else ""
            st.markdown(
                f'<div style="background:linear-gradient(135deg,#0a0b14,#131625);border:1px solid #1a1d2e;'
                f'border-radius:8px;padding:16px;margin-top:12px">'
                f'<div style="display:flex;justify-content:space-around;text-align:center">'
                f'<div><div style="font-size:10px;color:#6b7099">TOTAL INVESTED</div>'
                f'<div style="font-size:16px;font-weight:bold;color:#e8ecff">${total_invested:,.2f}</div></div>'
                f'<div><div style="font-size:10px;color:#6b7099">CURRENT VALUE</div>'
                f'<div style="font-size:16px;font-weight:bold;color:#e8ecff">${total_current:,.2f}</div></div>'
                f'<div><div style="font-size:10px;color:#6b7099">TOTAL P&L</div>'
                f'<div style="font-size:16px;font-weight:bold;color:{pnl_color}">{pnl_sign}${abs(total_pnl):,.2f} ({pnl_sign}{total_pnl_pct:.1f}%)</div></div>'
                f'</div>'
                f'</div>',
                unsafe_allow_html=True
            )