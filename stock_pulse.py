"""
Earnings Backtest — Alpaca/Polygon Edition
Strategy: Enter on earnings report day (AMC) at ~1:30 PM ET (12:30 PM CST)
          using the 9:30–1:30 ET 4H candle direction.
          Exit: next trading day close.
Data Sources:
  - Alpaca: Free real-time OHLCV bars (recommended)
  - Polygon: Free tier has ~7 day delay on hourly bars
"""

import sys
import os
os.environ["MALLOC_ARENA_MAX"] = "2"
if sys.stdout and hasattr(sys.stdout, "reconfigure"):
    try: sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception: pass
if sys.stderr and hasattr(sys.stderr, "reconfigure"):
    try: sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception: pass
# Ensure local module imports work regardless of working directory
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
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
import pytz
import concurrent.futures
import gc
import ctypes

import logging
import warnings
logging.getLogger("yfinance").setLevel(logging.CRITICAL)
warnings.filterwarnings("ignore", message=".*Styler.applymap.*")
warnings.filterwarnings("ignore", message=".*use_container_width.*")

try:
    from pandas.io.formats.style import Styler
    if hasattr(Styler, "map"):
        Styler.applymap = Styler.map
    else:
        Styler.map = Styler.applymap
except Exception:
    pass


def _safe_float(val, default=0.0):
    """Safely convert scalar, single-element Series, or array to float without triggering deprecation warnings."""
    if val is None:
        return default
    try:
        arr = np.asarray(val)
        if arr.size == 0:
            return default
        scalar = arr.item() if arr.size == 1 else arr.ravel()[0]
        if pd.isna(scalar):
            return default
        return float(scalar)
    except Exception:
        try:
            return float(val)
        except Exception:
            return default


_to_scalar_float = _safe_float


def trim_memory():
    """Trigger Python GC and command glibc to trim cached arenas back to the OS."""
    gc.collect()
    try:
        ctypes.CDLL("libc.so.6").malloc_trim(0)
    except Exception:
        pass


try:
    import yfinance as yf
    YFINANCE_AVAILABLE = True
except ImportError:
    YFINANCE_AVAILABLE = False

try:
    from apscheduler.schedulers.background import BackgroundScheduler
    SCHEDULER_AVAILABLE = True
except ImportError:
    SCHEDULER_AVAILABLE = False
    print("⚠️ apscheduler not installed. Run: pip install apscheduler")

try:
    from telegram_watchlist import fetch_today_watchlist, poll_and_store as tg_poll_and_store
    TELEGRAM_WATCHLIST_AVAILABLE = True
except ImportError:
    TELEGRAM_WATCHLIST_AVAILABLE = False
    def fetch_today_watchlist(force=False): return []
    def tg_poll_and_store(): return 0
    print("⚠️ telegram_watchlist.py not found — Telegram watchlist disabled")

try:
    from tos_scanner import scan_ticker as tos_scan_ticker, scan_multiple as tos_scan_multiple
    TOS_SCANNER_AVAILABLE = True
except Exception as _tos_import_err:
    TOS_SCANNER_AVAILABLE = False
    print(f"⚠️ tos_scanner.py import failed: {_tos_import_err}")

try:
    from bubble_scanner import analyze_ticker as bubble_analyze, analyze_multiple as bubble_analyze_multi
    BUBBLE_SCANNER_AVAILABLE = True
except Exception as _bub_err:
    BUBBLE_SCANNER_AVAILABLE = False
    print(f"⚠️ bubble_scanner.py import failed: {_bub_err}")

try:
    from macro_analysis import (
        get_macro_snapshot, render_macro_dashboard,
        sector_strength_from_scan, get_sector_performance as _macro_get_sector_performance,
        SECTOR_ETFS,
    )
    MACRO_MODULE_AVAILABLE = True
except ImportError:
    MACRO_MODULE_AVAILABLE = False
    print("⚠️ macro_analysis.py not found — Macro module disabled")

try:
    from alpaca_paper import PaperTrader
    PAPER_TRADING_AVAILABLE = True
    _PAPER_IMPORT_ERROR = None
except Exception as _pie:
    PAPER_TRADING_AVAILABLE = False
    _PAPER_IMPORT_ERROR = str(_pie)
    print(f"⚠️ alpaca_paper.py import failed: {_pie}")

try:
    from weekly_scenarios import build_weekly_tables, build_weekly_only_tables
    WEEKLY_SCENARIOS_AVAILABLE = True
except Exception as _wse:
    WEEKLY_SCENARIOS_AVAILABLE = False
    print(f"⚠️ weekly_scenarios.py import failed: {_wse}")

# ──────────────────────────────────────────────
# TELEGRAM NOTIFICATION SETUP
# ──────────────────────────────────────────────
from config import TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID
TELEGRAM_ENABLED = TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID

def _in_telegram_quiet_hours() -> bool:
    """Return True if current CST time is outside trading hours (3 PM – 8 AM CST).
    During quiet hours all Telegram sends and tracking alerts are suppressed."""
    try:
        _cst_now = datetime.now(pytz.timezone('US/Central'))
        return _cst_now.hour >= 15 or _cst_now.hour < 8
    except Exception:
        return False

# Initialize session telegram settings
if "_telegram_bot_token" not in st.session_state:
    st.session_state["_telegram_bot_token"] = TELEGRAM_BOT_TOKEN
if "_telegram_chat_id" not in st.session_state:
    st.session_state["_telegram_chat_id"] = TELEGRAM_CHAT_ID

# ──────────────────────────────────────────────
# PERFORMANCE: Thread & Process Pool Executors
# Moves heavy I/O (API calls) and CPU work to
# separate threads/processes so the Streamlit
# main loop stays responsive for concurrent users.
# Ref: https://discuss.streamlit.io/t/68541
# ──────────────────────────────────────────────

@st.cache_resource
def get_thread_pool():
    """Shared ThreadPoolExecutor for I/O-bound work (API calls, Telegram sends). Capped at 4 for 512MB RAM tier."""
    return concurrent.futures.ThreadPoolExecutor(max_workers=4)

@st.cache_resource
def get_process_pool():
    """Fallback executor reusing thread pool to avoid spawning memory-heavy child processes."""
    return get_thread_pool()

# ──────────────────────────────────────────────
# TRADE TRACKER DATABASE (SQLite)
# ──────────────────────────────────────────────

TRADE_DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "stockpulse_trades.db")


def _migrate_trades_table():
    """Migrate the trades table schema to add missing columns."""
    conn = sqlite3.connect(TRADE_DB_PATH)
    try:
        # Check if columns exist
        cursor = conn.execute("PRAGMA table_info(trades)")
        columns = {row[1] for row in cursor.fetchall()}
        
        # Add open_price column if it doesn't exist
        if "open_price" not in columns:
            conn.execute("ALTER TABLE trades ADD COLUMN open_price REAL")
            conn.commit()
            print("✅ Migration: Added open_price column to trades table")
        
        # Add action column if it doesn't exist
        if "action" not in columns:
            conn.execute("ALTER TABLE trades ADD COLUMN action TEXT DEFAULT 'OPEN'")
            conn.commit()
            print("✅ Migration: Added action column to trades table")
            
    except sqlite3.OperationalError as e:
        print(f"Migration info: {e}")
    finally:
        conn.close()


def _get_trade_db():
    """Get a connection to the trades database, creating tables if needed."""
    conn = sqlite3.connect(TRADE_DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA cache_size=-500")
    conn.execute("""CREATE TABLE IF NOT EXISTS auto_scan_rank1 (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        scan_date TEXT NOT NULL,
        ticker TEXT NOT NULL,
        UNIQUE(scan_date, ticker)
    )""")
    conn.execute("""CREATE TABLE IF NOT EXISTS intraday_tickers (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        scan_date TEXT NOT NULL,
        ticker TEXT NOT NULL,
        source TEXT NOT NULL DEFAULT 'base',
        UNIQUE(scan_date, ticker)
    )""")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS trades (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ticker TEXT NOT NULL,
            direction TEXT NOT NULL,
            open_price REAL,
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
            action TEXT DEFAULT 'OPEN',
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
    conn.execute("""
        CREATE TABLE IF NOT EXISTS holdings_scan (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            scan_date TEXT NOT NULL,
            ticker TEXT NOT NULL,
            quantity REAL,
            avg_cost REAL,
            source TEXT NOT NULL DEFAULT 'csv',
            UNIQUE(scan_date, ticker)
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS tracking_sessions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            trade_id INTEGER,
            ticker TEXT NOT NULL,
            direction TEXT NOT NULL,
            entry_price REAL NOT NULL,
            stop_loss REAL,
            target1 REAL,
            target2 REAL,
            scenario TEXT,
            confidence TEXT,
            atr TEXT,
            tracking_start_time TEXT NOT NULL,
            last_price REAL,
            last_update_time TEXT,
            status TEXT DEFAULT 'TRACKING',
            telegram_entry_sent INTEGER DEFAULT 0,
            created_at TEXT DEFAULT (datetime('now')),
            updated_at TEXT DEFAULT (datetime('now'))
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS watchlist (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ticker TEXT NOT NULL,
            direction TEXT NOT NULL,
            entry_price REAL,
            stop_loss REAL,
            target1 REAL,
            target2 REAL,
            scenario TEXT,
            confidence TEXT,
            ohlc_signal TEXT,
            reason TEXT,
            added_date TEXT DEFAULT (date('now')),
            status TEXT DEFAULT 'ACTIVE',
            created_at TEXT DEFAULT (datetime('now'))
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


# ── Tracking Sessions CRUD ──────────────────────────────────────
def save_tracking_session(ticker, direction, entry_price, stop_loss, target1, target2, scenario=None, confidence=None, atr=None, trade_id=None):
    """Save a tracking session to persist tracking state across reruns."""
    conn = _get_trade_db()
    try:
        tracking_start = get_cst_now().isoformat()
        conn.execute("""
            INSERT INTO tracking_sessions (trade_id, ticker, direction, entry_price, stop_loss, target1, target2, scenario, confidence, atr, tracking_start_time)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (trade_id, ticker, direction, entry_price, stop_loss, target1, target2, scenario, confidence, atr, tracking_start))
        conn.commit()
        session_id = conn.total_changes
        return session_id
    finally:
        conn.close()

def get_active_tracking_sessions():
    """Get all active tracking sessions."""
    conn = _get_trade_db()
    try:
        rows = conn.execute(
            "SELECT * FROM tracking_sessions WHERE status = 'TRACKING' ORDER BY tracking_start_time DESC"
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()

def update_tracking_session(session_id, last_price=None, status=None, entry_sent=None):
    """Update tracking session with latest price and status."""
    conn = _get_trade_db()
    try:
        updates = {"updated_at": datetime.now(pytz.timezone('US/Central')).isoformat()}
        if last_price is not None:
            updates["last_price"] = last_price
            updates["last_update_time"] = datetime.now(pytz.timezone('US/Central')).isoformat()
        if status is not None:
            updates["status"] = status
        if entry_sent is not None:
            updates["telegram_entry_sent"] = entry_sent
        
        set_clause = ", ".join(f"{k} = ?" for k in updates)
        vals = list(updates.values()) + [session_id]
        conn.execute(f"UPDATE tracking_sessions SET {set_clause} WHERE id = ?", vals)
        conn.commit()
    finally:
        conn.close()


def _fire_and_forget_telegram(fn, *args, **kwargs):
    """Submit a Telegram send function to the thread pool (non-blocking).
    Returns the Future so the caller can optionally wait for the result."""
    return get_thread_pool().submit(fn, *args, **kwargs)


def send_telegram_notification(ticker, direction, open_price, entry_price, stop_loss, target1, target2, scenario=None, confidence=None, atr=None, rr_t1=None, rr_t2=None, notes=None, ohlc_signal=None):
    """
    Send a condensed trade entry notification via Telegram.
    """
    if not TELEGRAM_ENABLED or _in_telegram_quiet_hours():
        return False
    
    try:
        # Format prices safely
        open_str = f"${open_price:.2f}" if open_price is not None else "N/A"
        entry_str = f"${entry_price:.2f}"
        stop_str = f"${stop_loss:.2f}" if stop_loss is not None else "N/A"
        t1_str = f"${target1:.2f}" if target1 is not None else "N/A"
        t2_str = f"${target2:.2f}" if target2 is not None else "N/A"

        # Compute RR if not provided
        def _tg_rr(e, s, t, d):
            try:
                risk = (e - s) if d == "LONG" else (s - e)
                rew  = (t - e) if d == "LONG" else (e - t)
                return round(rew / risk, 2) if risk > 0 else 0.0
            except: return 0.0
        
        if rr_t1 is None:
            rr_t1 = _tg_rr(entry_price, stop_loss, target1, direction) if stop_loss and target1 else 0.0
        if rr_t2 is None:
            rr_t2 = _tg_rr(entry_price, stop_loss, target2, direction) if stop_loss and target2 else 0.0
        best_rr = max(rr_t1, rr_t2)
        date_str = datetime.now(pytz.timezone('US/Central')).strftime('%m/%d %I:%M%p CST')
        dir_emoji = "🟢" if direction == "LONG" else "🔴"

        # Shorten scenario label
        def _shorten_scen(s):
            if not s: return ""
            su = s.upper()
            if "OPENS NEAR ENTRY" in su:    return "ONE"
            if "OPENS BETWEEN" in su:       return "OBE"
            if "OPENS PAST STOP" in su:     return "OPS"
            if "GAP" in su:                 return "GAP"
            return s[:15].strip()

        scen_label = _shorten_scen(scenario)
        # Scenario-specific guidance
        if scen_label == "OPS" or scen_label == "OBE":
            scen_tip = f"{scen_label} — targets can be lower, use tight stop losses"
        elif scen_label == "GAP":
            scen_tip = f"{scen_label} — targets can extend"
        elif scen_label == "ONE":
            scen_tip = f"{scen_label} — targets can be pretty close"
        elif scen_label:
            scen_tip = scen_label
        else:
            scen_tip = ""
        notes_str = notes if notes else "Intraday-Respect SL & TP"

        # Market Risk
        try:
            _mr_label, _mr_notes = get_market_risk_data()
            _mr_str = f"{_mr_label}" + (f" — {' · '.join(_mr_notes)}" if _mr_notes else "")
        except Exception:
            _mr_str = "🟡 MODERATE RISK"

        message = (
            f"🔔 <b>NEW TRADE</b> | {date_str}\n"
            f"{dir_emoji} <b>{ticker}</b> {direction}\n"
            f"Open: {open_str} | Entry: {entry_str} | Stop: {stop_str}\n"
            f"T1: {t1_str} ({rr_t1:.2f}x) | T2: {t2_str} ({rr_t2:.2f}x) | Best: <b>{best_rr:.2f}x</b>\n"
        )
        if scen_tip:
            message += f"{scen_tip}\n"
        message += f"📝 {notes_str}\n"
        if ohlc_signal:
            _ohlc_emoji = '🐂' if 'BULL' in str(ohlc_signal).upper() else ('🐻' if 'BEAR' in str(ohlc_signal).upper() else '⚪')
            message += f"{_ohlc_emoji} OHLC Signal: {ohlc_signal}\n"
        message += f"📊 {_mr_str}"
        
        bot_token = st.session_state.get("_telegram_bot_token", TELEGRAM_BOT_TOKEN)
        chat_id = st.session_state.get("_telegram_chat_id", TELEGRAM_CHAT_ID)
        url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
        payload = {
            "chat_id": chat_id,
            "text": message,
            "parse_mode": "HTML"
        }
        
        response = requests.post(url, json=payload, timeout=5)
        success = response.status_code == 200
        print(f"📱 Telegram send status: {response.status_code} - {response.text[:100] if not success else 'OK'}")
        return success
    except Exception as e:
        print(f"❌ Telegram notification error: {type(e).__name__}: {e}")
        import traceback
        traceback.print_exc()
        return False


def send_trade_update_notification(trade, action, exit_price=None):
    """
    Send a trade update notification via Telegram when action changes.
    """
    if not TELEGRAM_ENABLED or _in_telegram_quiet_hours():
        return False
    
    try:
        ticker = trade.get("ticker", "?")
        direction = trade.get("direction", "?")
        entry = trade.get("entry_price", 0)
        stop = trade.get("stop_loss")
        t1 = trade.get("target1")
        t2 = trade.get("target2")
        
        print(f"📱 Sending trade update: {ticker} {action} (Entry: ${entry:.2f})")
        
        # Calculate P&L if exit price provided
        pnl_str = ""
        if exit_price:
            if direction == "LONG":
                pnl = round((exit_price - entry) / entry * 100, 2)
            else:
                pnl = round((entry - exit_price) / entry * 100, 2)
            pnl_str = f" | P&L: {pnl:+.2f}%"
        else:
            pnl_str = ""

        # Compute RR
        def _tg_upd_rr(e, s, t, d):
            try:
                risk = (e - s) if d == "LONG" else (s - e)
                rew  = (t - e) if d == "LONG" else (e - t)
                return round(rew / risk, 2) if risk > 0 else 0.0
            except: return 0.0
        rr_t1 = _tg_upd_rr(entry, stop, t1, direction) if stop and t1 else 0.0
        rr_t2 = _tg_upd_rr(entry, stop, t2, direction) if stop and t2 else 0.0
        best_rr = max(rr_t1, rr_t2)
        date_str = datetime.now(pytz.timezone('US/Central')).strftime('%m/%d %I:%M%p CST')
        dir_emoji = "🟢" if direction == "LONG" else "🔴"

        action_emoji = {
            "OPEN": "🟢", "CLOSE": "✅", "TAKE_PROFIT_T1": "🎁",
            "TAKE_PROFIT_T2": "🎁🎁", "STOP_LOSS": "🛑", "DELETE": "🗑️"
        }.get(action, "📊")
        
        stop_str = f"${stop:.2f}" if stop else "N/A"
        t1_str = f"${t1:.2f}" if t1 else "N/A"
        t2_str = f"${t2:.2f}" if t2 else "N/A"
        exit_str = f"\nExit: ${exit_price:.2f}{pnl_str}" if exit_price else ""

        # Market Risk
        try:
            _mr_label, _mr_notes = get_market_risk_data()
            _mr_str = f"{_mr_label}" + (f" — {' · '.join(_mr_notes)}" if _mr_notes else "")
        except Exception:
            _mr_str = "🟡 MODERATE RISK"

        message = (
            f"{action_emoji} <b>TRADE UPDATE</b> | {date_str}\n"
            f"{dir_emoji} <b>{ticker}</b> {direction} — {action.replace('_', ' ')}\n"
            f"Entry: ${entry:.2f} | Stop: {stop_str}\n"
            f"T1: {t1_str} ({rr_t1:.2f}x) | T2: {t2_str} ({rr_t2:.2f}x) | Best: <b>{best_rr:.2f}x</b>"
            f"{exit_str}\n"
            f"📝 Intraday-Respect SL & TP\n"
            f"📊 {_mr_str}"
        )
        
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
        payload = {
            "chat_id": TELEGRAM_CHAT_ID,
            "text": message,
            "parse_mode": "HTML"
        }
        
        response = requests.post(url, json=payload, timeout=5)
        success = response.status_code == 200
        
        if not success:
            print(f"Response body: {response.text[:200]}")
        
        return success
    except Exception as e:
        print(f"❌ Trade update notification error: {type(e).__name__}: {e}")
        import traceback
        traceback.print_exc()
        return False


def send_tracking_price_update(ticker, current_price, entry_price, stop_loss, target1, target2, direction, distance_to_entry=None, distance_to_tp=None, scenario=None, ohlc_signal=None):
    """Send real-time price update during tracking (e.g., near entry, approaching targets)."""
    if not TELEGRAM_ENABLED or _in_telegram_quiet_hours():
        return False
    
    try:
        date_str = datetime.now(pytz.timezone('US/Central')).strftime('%m/%d/%Y %I:%M %p CST')
        
        # Determine price action emoji
        if direction == "LONG":
            if current_price >= target1 and target1:
                status_emoji = "🎁"
                status_text = "APPROACHING TARGET 1"
            elif current_price >= entry_price:
                status_emoji = "📈"
                status_text = "ABOVE ENTRY"
            elif current_price <= stop_loss and stop_loss:
                status_emoji = "🛑"
                status_text = "AT STOP LOSS"
            else:
                status_emoji = "📉"
                status_text = "BELOW ENTRY"
        else:  # SHORT
            if current_price <= target1 and target1:
                status_emoji = "🎁"
                status_text = "APPROACHING TARGET 1"
            elif current_price <= entry_price:
                status_emoji = "📉"
                status_text = "BELOW ENTRY"
            elif current_price >= stop_loss and stop_loss:
                status_emoji = "🛑"
                status_text = "AT STOP LOSS"
            else:
                status_emoji = "📈"
                status_text = "ABOVE ENTRY"
        
        # Calculate P&L
        if direction == "LONG":
            pnl_pct = round((current_price - entry_price) / entry_price * 100, 2)
        else:
            pnl_pct = round((entry_price - current_price) / entry_price * 100, 2)
        
        scenario_str = f"📍 <b>Scenario:</b> {scenario}\n" if scenario else ""

        # Market Risk
        try:
            _mr_label, _mr_notes = get_market_risk_data()
            _mr_str = f"{_mr_label}" + (f" — {' · '.join(_mr_notes)}" if _mr_notes else "")
        except Exception:
            _mr_str = "🟡 MODERATE RISK"
        
        message = f"""
{status_emoji} <b>PRICE UPDATE - {status_text}</b>

📅 <b>Date:</b> {date_str}
📊 <b>Ticker:</b> {ticker}
🎯 <b>Direction:</b> {direction}

💰 <b>Current Price:</b> ${current_price:.2f}
📍 <b>Entry:</b> ${entry_price:.2f}
🛑 <b>Stop:</b> ${"N/A" if not stop_loss else f"{stop_loss:.2f}"}
🎁 <b>T1:</b> ${"N/A" if not target1 else f"{target1:.2f}"}
🎁 <b>T2:</b> ${"N/A" if not target2 else f"{target2:.2f}"}

💹 <b>P&L:</b> {pnl_pct:+.2f}%
{scenario_str}
{('🐂' if 'BULL' in str(ohlc_signal).upper() else ('🐻' if 'BEAR' in str(ohlc_signal).upper() else '⚪')) + ' <b>OHLC:</b> ' + str(ohlc_signal) + chr(10) if ohlc_signal else ''}📝 Keep monitoring for entry/target hits
📊 {_mr_str}
"""
        
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
        payload = {
            "chat_id": TELEGRAM_CHAT_ID,
            "text": message,
            "parse_mode": "HTML"
        }
        
        response = requests.post(url, json=payload, timeout=5)
        return response.status_code == 200
    except Exception as e:
        print(f"❌ Tracking price update error: {e}")
        return False


@st.cache_data(ttl=300, max_entries=20, show_spinner=False)  # Cache for 5 min — VIX/SPY don't change that fast
def get_market_risk_data():
    """
    Fetch real market data and calculate risk assessment.
    Returns: (risk_label, risk_notes_list)
    """
    try:
        import yfinance as yf
        
        # Fetch current data
        vix_data = yf.Ticker("^VIX").history(period="1d")
        spy_data = yf.Ticker("SPY").history(period="5d")
        tlt_data = yf.Ticker("TLT").history(period="5d")
        gld_data = yf.Ticker("GLD").history(period="5d")
        
        risk_score = 0
        risk_notes = []
        
        # VIX Analysis
        if not vix_data.empty:
            vix_price = float(vix_data["Close"].iloc[-1])
            if vix_price > 25:
                risk_score += 2
                risk_notes.append(f"VIX {vix_price:.0f} — elevated fear")
            elif vix_price > 18:
                risk_score += 1
                risk_notes.append(f"VIX {vix_price:.0f} — mild caution")
            else:
                risk_notes.append(f"VIX {vix_price:.0f} — calm market")
        
        # SPY 5-day performance
        if not spy_data.empty and len(spy_data) >= 2:
            spy_chg = ((spy_data["Close"].iloc[-1] - spy_data["Close"].iloc[0]) / spy_data["Close"].iloc[0]) * 100
            if spy_chg < -2:
                risk_score += 1
                risk_notes.append(f"SPY 5d: {spy_chg:+.1f}% — market pressure")
        
        # TLT (Bond) analysis
        if not tlt_data.empty and len(tlt_data) >= 2:
            tlt_chg = ((tlt_data["Close"].iloc[-1] - tlt_data["Close"].iloc[0]) / tlt_data["Close"].iloc[0]) * 100
            if tlt_chg > 1:
                risk_score += 1
                risk_notes.append("Bonds rallying — safe haven demand")
        
        # GLD (Gold) analysis
        if not gld_data.empty and len(gld_data) >= 2:
            gld_chg = ((gld_data["Close"].iloc[-1] - gld_data["Close"].iloc[0]) / gld_data["Close"].iloc[0]) * 100
            if gld_chg > 2:
                risk_score += 1
                risk_notes.append(f"Gold 5d: {gld_chg:+.1f}% — hedge demand")
        
        # Determine risk level
        if risk_score == 0:
            risk_label = "🟢 LOW RISK"
        elif risk_score <= 2:
            risk_label = "🟡 MODERATE RISK"
        else:
            risk_label = "🔴 HIGH RISK"
        
        return risk_label, risk_notes
    
    except Exception as e:
        print(f"❌ Market risk data error: {e}")
        return "🟡 MODERATE RISK", ["Unable to fetch live data"]


def send_market_risk_message(risk_label=None, risk_notes=None):
    """
    Send Market Risk status message at 8:00 AM CST.
    If risk_label/notes not provided, fetch real data.
    """
    if not TELEGRAM_ENABLED or _in_telegram_quiet_hours():
        return False
    
    try:
        # Fetch real data if not provided
        if risk_label is None or risk_notes is None:
            risk_label, risk_notes = get_market_risk_data()
        
        risk_details = " · ".join(risk_notes) if risk_notes else "No additional risk factors"
        
        message = f"""
📊 <b>MARKET RISK ASSESSMENT</b>

{risk_label}

{risk_details}

🕐 Time: {datetime.now(pytz.timezone('US/Central')).strftime('%I:%M %p CST')}
"""
        
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
        payload = {
            "chat_id": TELEGRAM_CHAT_ID,
            "text": message,
            "parse_mode": "HTML"
        }
        
        response = requests.post(url, json=payload, timeout=5)
        success = response.status_code == 200
        print(f"📊 Market Risk message sent: {'✅' if success else '❌'}")
        return success
    except Exception as e:
        print(f"❌ Market Risk message error: {e}")
        return False


def send_tracking_initiated_message():
    """
    Send tracking initiated message at 8:30 AM CST.
    """
    if not TELEGRAM_ENABLED or _in_telegram_quiet_hours():
        return False
    
    try:
        cst_tz = pytz.timezone('US/Central')
        now_cst = datetime.now(cst_tz)
        
        message = f"""
🤖 <b>BOT STATUS - TRACKING INITIATED</b>

✅ Bot is running and monitoring market

📅 Date: {now_cst.strftime('%B %d, %Y')}
🕐 Time: {now_cst.strftime('%I:%M %p CST')}

Looking for:
  • Open prices
  • Entry opportunities
  • Market signals

Ready to track trades!
"""
        
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
        payload = {
            "chat_id": TELEGRAM_CHAT_ID,
            "text": message,
            "parse_mode": "HTML"
        }
        
        response = requests.post(url, json=payload, timeout=5)
        success = response.status_code == 200
        print(f"🤖 Tracking initiated message sent: {'✅' if success else '❌'}")
        return success
    except Exception as e:
        print(f"❌ Tracking initiated message error: {e}")
        return False


def send_sector_swing_alert(swing_trades):
    """Send Telegram alert with sector scan swing trade setups (EOD push)."""
    if not TELEGRAM_ENABLED or not swing_trades or _in_telegram_quiet_hours():
        return False
    try:
        cst_tz = pytz.timezone('US/Central')
        now_cst = datetime.now(cst_tz)

        bullish = [t for t in swing_trades if t.get("verdict") == "BULLISH"]
        bearish = [t for t in swing_trades if t.get("verdict") == "BEARISH"]

        lines = [
            "🔥 <b>SECTOR SCAN — SWING SETUPS</b>",
            "",
            f"📅 {now_cst.strftime('%B %d, %Y')} · {len(swing_trades)} setup(s) pushed to Trade Tracker",
        ]

        if bullish:
            lines.append(f"\n🚀 <b>LONG SETUPS ({len(bullish)})</b>")
            for r in bullish:
                entry  = r.get("entry") or r.get("price") or 0
                stop   = r.get("stop_loss") or 0
                t1     = r.get("target1") or 0
                t2     = r.get("target2") or 0
                score  = r.get("score", 0)
                conf   = r.get("confidence", "")
                risk_pct   = abs((entry - stop) / entry * 100) if entry and stop else 0
                reward_pct = abs((t1 - entry) / entry * 100) if t1 and entry else 0
                rr = f"{reward_pct / risk_pct:.1f}x" if risk_pct > 0 else "N/A"
                lines.append(
                    f"\n  • <b>{r['ticker']}</b>  Score: +{score} · {conf}\n"
                    f"    Entry: ${entry:.2f} · Stop: ${stop:.2f} · T1: ${t1:.2f}"
                    + (f" · T2: ${t2:.2f}" if t2 else "")
                    + f"\n    Risk: {risk_pct:.1f}%  R:R ~{rr}"
                )

        if bearish:
            lines.append(f"\n📉 <b>SHORT SETUPS ({len(bearish)})</b>")
            for r in bearish:
                entry  = r.get("entry") or r.get("price") or 0
                stop   = r.get("stop_loss") or 0
                t1     = r.get("target1") or 0
                t2     = r.get("target2") or 0
                score  = r.get("score", 0)
                conf   = r.get("confidence", "")
                risk_pct   = abs((stop - entry) / entry * 100) if entry and stop else 0
                reward_pct = abs((entry - t1) / entry * 100) if t1 and entry else 0
                rr = f"{reward_pct / risk_pct:.1f}x" if risk_pct > 0 else "N/A"
                lines.append(
                    f"\n  • <b>{r['ticker']}</b>  Score: {score} · {conf}\n"
                    f"    Entry: ${entry:.2f} · Stop: ${stop:.2f} · T1: ${t1:.2f}"
                    + (f" · T2: ${t2:.2f}" if t2 else "")
                    + f"\n    Risk: {risk_pct:.1f}%  R:R ~{rr}"
                )

        lines.append("\n✅ Saved to Trade Tracker")

        message = "\n".join(lines)
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
        payload = {"chat_id": TELEGRAM_CHAT_ID, "text": message, "parse_mode": "HTML"}
        response = requests.post(url, json=payload, timeout=10)
        success = response.status_code == 200
        print(f"🔥 Sector swing alert sent: {'✅' if success else '❌'}")
        return success
    except Exception as e:
        print(f"❌ Sector swing alert error: {e}")
        return False


def send_swing_morning_brief():
    """8:35 AM Telegram brief — open swing trades status vs entry plan."""
    if not TELEGRAM_ENABLED or _in_telegram_quiet_hours():
        return False
    try:
        import yfinance as yf
        cst_tz = pytz.timezone('US/Central')
        now_cst = datetime.now(cst_tz)

        all_trades = get_all_trades()
        open_trades = [t for t in all_trades if t.get("status") == "OPEN"]

        if not open_trades:
            return False

        lines = [
            "🌅 <b>SWING TRADE MORNING BRIEF</b>",
            "",
            f"📅 {now_cst.strftime('%B %d, %Y')} · {now_cst.strftime('%I:%M %p CST')}",
            f"Open trades: <b>{len(open_trades)}</b>",
        ]

        for t in open_trades:
            ticker    = t["ticker"]
            direction = t["direction"]
            entry     = t.get("entry_price", 0)
            stop      = t.get("stop_loss")
            t1        = t.get("target1")

            try:
                hist    = yf.Ticker(ticker).history(period="1d")
                current = float(hist["Close"].iloc[-1]) if not hist.empty else None
            except Exception:
                current = None

            if current and entry:
                pnl_pct = ((current - entry) / entry * 100) if direction == "LONG" \
                          else ((entry - current) / entry * 100)
                icon = "🟢" if pnl_pct > 0 else "🔴"
                at_risk  = stop and (
                    (direction == "LONG"  and current <= stop * 1.01) or
                    (direction == "SHORT" and current >= stop * 0.99)
                )
                near_t1  = t1 and (
                    (direction == "LONG"  and current >= t1 * 0.98) or
                    (direction == "SHORT" and current <= t1 * 1.02)
                )
                flag = " ⚠️ NEAR STOP" if at_risk else (" 🎯 NEAR T1" if near_t1 else "")
                stop_str = f" · Stop: ${stop:.2f}" if stop else ""
                t1_str   = f" · T1: ${t1:.2f}" if t1 else ""
                lines.append(
                    f"\n{icon} <b>{ticker}</b> {direction}\n"
                    f"  Entry: ${entry:.2f} → Now: ${current:.2f} ({pnl_pct:+.1f}%){flag}\n"
                    f"  {stop_str}{t1_str}".strip()
                )
            else:
                lines.append(f"\n⬜ <b>{ticker}</b> {direction} · Entry: ${entry:.2f} (price N/A)")

        message = "\n".join(lines)
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
        payload = {"chat_id": TELEGRAM_CHAT_ID, "text": message, "parse_mode": "HTML"}
        response = requests.post(url, json=payload, timeout=10)
        success = response.status_code == 200
        print(f"🌅 Swing morning brief sent: {'✅' if success else '❌'}")
        return success
    except Exception as e:
        print(f"❌ Swing morning brief error: {e}")
        return False


def save_trade(ticker, direction, entry_price, stop_loss=None, target1=None,
               target2=None, verdict=None, confidence=None, score=None,
               signals=None, t1_days=None, t2_days=None, open_price=None, notes=None, scenario=None, ohlc_signal=None,
               in_replay=False, force=False):
    """Save a new trade to the database and send Telegram notification.
    Returns tuple: (success, telegram_sent, trade_id)
    Pass in_replay=True to suppress Telegram during replay sessions.
    Pass force=True to bypass SAVE_TO_TRADE_TRACKER config check (for manual entries).
    """
    if not force:
        try:
            import importlib, paper_config as _pc_st
            importlib.reload(_pc_st)
            if not _pc_st.SAVE_TO_TRADE_TRACKER:
                return True, False, -1
        except Exception:
            pass
    conn = _get_trade_db()
    try:
        conn.execute("""
            INSERT INTO trades (ticker, direction, open_price, entry_price, stop_loss, target1,
                target2, verdict, confidence, score, signals, t1_trading_days,
                t2_trading_days, entry_date, notes)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (ticker, direction, open_price, entry_price, stop_loss, target1, target2,
              verdict, confidence, score, signals, t1_days, t2_days,
              str(date.today()), notes))
        conn.commit()
        trade_id = conn.total_changes

        # Skip Telegram during replay sessions
        if in_replay:
            return True, False, trade_id

        # Send Telegram notification with scenario and confidence
        telegram_sent = send_telegram_notification(ticker, direction, open_price, entry_price, stop_loss, target1, target2, scenario=scenario, confidence=confidence, ohlc_signal=ohlc_signal)
        
        # Save tracking session if telegram was sent
        if telegram_sent:
            try:
                save_tracking_session(ticker, direction, entry_price, stop_loss, target1, target2, scenario=scenario, confidence=confidence, trade_id=trade_id)
            except:
                pass
        
        return True, telegram_sent, trade_id
    finally:
        conn.close()


def save_to_watchlist(ticker, direction, entry_price, stop_loss=None, target1=None,
                      target2=None, scenario=None, confidence=None, ohlc_signal=None,
                      reason=None):
    """Save a flipped/watchlist ticker to the watchlist DB table.
    Returns True if saved successfully.
    """
    conn = _get_trade_db()
    try:
        # Check for duplicate on same day
        existing = conn.execute(
            "SELECT id FROM watchlist WHERE ticker = ? AND added_date = date('now') AND status = 'ACTIVE'",
            (ticker,)
        ).fetchone()
        if existing:
            return False  # Already on watchlist today
        conn.execute("""
            INSERT INTO watchlist (ticker, direction, entry_price, stop_loss, target1,
                target2, scenario, confidence, ohlc_signal, reason)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (ticker, direction, entry_price, stop_loss, target1, target2,
              scenario, confidence, ohlc_signal, reason))
        conn.commit()
        return True
    finally:
        conn.close()


def get_watchlist(date_filter=None, status="ACTIVE"):
    """Get watchlist entries, optionally filtered by date.
    Returns list of dicts.
    """
    conn = _get_trade_db()
    try:
        if date_filter:
            rows = conn.execute(
                "SELECT * FROM watchlist WHERE added_date = ? AND status = ? ORDER BY created_at DESC",
                (str(date_filter), status)
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM watchlist WHERE status = ? ORDER BY added_date DESC, created_at DESC",
                (status,)
            ).fetchall()
        return [dict(r) for r in rows]
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
                 "low_since_entry", "action", "notes"}
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


def delete_all_trades():
    """Delete all trades from the database."""
    conn = _get_trade_db()
    try:
        conn.execute("DELETE FROM trades")
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


def is_market_hours():
    """Return True if current CST time is between 7:00 AM and 3:00 PM (inclusive start, exclusive end).
    All schedulers should sleep outside this window."""
    now_cst = get_cst_now()
    return 7 <= now_cst.hour < 15


from news_sentiment import get_news_sentiment, get_news_details


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
                if is_backtest:
                    # No 5m data — fall back to daily bars for bias
                    _mf_daily = tk.history(start=str(ref_date - timedelta(days=30)), end=str(ref_date + timedelta(days=1)), interval="1d")
                    if not _mf_daily.empty:
                        _mf_last = _mf_daily.tail(3)
                        _mf_open = float(_mf_last["Open"].iloc[-1])
                        _mf_cpx = float(_mf_last["Close"].iloc[-1])
                        _mf_avg = float(_mf_last["Close"].mean())
                        if direction == "LONG":
                            _mf_bs = "BULLISH" if _mf_cpx > _mf_open else "BEARISH"
                            _mf_bl = "BULLISH" if _mf_avg > _mf_open else "BEARISH"
                        else:
                            _mf_bs = "BEARISH" if _mf_cpx < _mf_open else "BULLISH"
                            _mf_bl = "BEARISH" if _mf_avg < _mf_open else "BULLISH"
                        _mf_aln = "CONFIRMED" if _mf_bs == _mf_bl else "DIVERGED"
                        _mf_conc = f"✅ BIAS CONFIRMED (daily): {_mf_bs}" if _mf_aln == "CONFIRMED" else f"⚠️ BIAS DIVERGED (daily): Short={_mf_bs}, Long={_mf_bl}"
                        return {
                            "bias_10min": _mf_bs, "vol_bias_10min": 0,
                            "bias_30min": _mf_bl, "vol_bias_30min": 0,
                            "tf_short": "1D", "tf_long": "3D",
                            "alignment": _mf_aln, "conclusion": _mf_conc,
                            "color": "#00e5a0" if _mf_aln == "CONFIRMED" else "#f5c842",
                            "current_price": round(_mf_cpx, 2),
                        }
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

            if hist_short.empty and is_backtest and not hist_10m.empty:
                # Target date has no intraday data (future/holiday) — fall back to last available session
                _fb_d2 = idx[-1].date()
                today_820_et = cst_tz.localize(datetime(_fb_d2.year, _fb_d2.month, _fb_d2.day, 8, 20, 0)).astimezone(et_tz)
                today_eod_et = cst_tz.localize(datetime(_fb_d2.year, _fb_d2.month, _fb_d2.day, 15, 0, 0)).astimezone(et_tz)
                hist_short = hist_10m[(idx >= today_820_et) & (idx <= today_eod_et)]
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
        # Skip weekend / pre-8:00 for live mode only
        # (Pre-confirmation table needs biases from 8:00 AM onward)
        if not is_backtest and (is_weekend or hour < 8):
            return None

        tk = yf.Ticker(ticker)

        # Date anchors
        today_830_cst = cst_tz.localize(datetime(now_cst.year, now_cst.month, now_cst.day, 8, 30, 0))
        today_830_et = today_830_cst.astimezone(et_tz)
        # Use 7:00 AM CST (8:00 AM ET) to capture premarket data for pre-confirmation
        today_premarket_cst = cst_tz.localize(datetime(now_cst.year, now_cst.month, now_cst.day, 7, 0, 0))
        today_premarket_et = today_premarket_cst.astimezone(et_tz)
        today_820_cst = cst_tz.localize(datetime(now_cst.year, now_cst.month, now_cst.day, 8, 20, 0))
        today_820_et = today_820_cst.astimezone(et_tz)
        # For live pre-8:30 calls, use premarket cutoff; for post-8:30, use 8:20
        _data_cutoff_et = today_premarket_et if (not is_backtest and hour < 8 or (hour == 8 and minute < 30)) else today_820_et

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
            if is_backtest:
                # No intraday 5m data (>60 days) — fall back to daily bars
                _daily_fb = tk.history(start=str(ref_date - timedelta(days=30)), end=str(ref_date + timedelta(days=1)), interval="1d")
                if not _daily_fb.empty:
                    _d_last = _daily_fb.tail(3)
                    today_open = float(_d_last["Open"].iloc[-1])
                    current_price = float(_d_last["Close"].iloc[-1])
                    bias_10m = "BULLISH" if current_price > today_open else "BEARISH"
                    bias_30m = bias_10m
                    _d_avg = float(_d_last["Close"].mean())
                    bias_4h = "BULLISH" if _d_avg > today_open else "BEARISH"
                    valid_biases = [bias_10m, bias_30m, bias_4h]
                    alignment = "CONFIRMED" if len(set(valid_biases)) == 1 else "DIVERGED"
                    return {
                        "bias_10m": bias_10m, "bias_30m": bias_30m, "bias_4h": bias_4h,
                        "alignment": alignment,
                        "today_open": round(today_open, 2),
                        "current_price": round(current_price, 2),
                    }
            return None
        hist_10m = _filter_from(hist_10m_raw, _data_cutoff_et)
        if hist_10m.empty and is_backtest and not hist_10m_raw.empty:
            # Target date has no intraday data (future/holiday) — fall back to last available session
            _fb_idx = hist_10m_raw.index
            if _fb_idx.tz is None:
                _fb_idx = _fb_idx.tz_localize("America/New_York")
            else:
                _fb_idx = _fb_idx.tz_convert("America/New_York")
            _fb_d = _fb_idx[-1].date()
            today_820_et = cst_tz.localize(datetime(_fb_d.year, _fb_d.month, _fb_d.day, 8, 20, 0)).astimezone(et_tz)
            today_eod_et = cst_tz.localize(datetime(_fb_d.year, _fb_d.month, _fb_d.day, 15, 0, 0)).astimezone(et_tz)
            hist_10m = hist_10m_raw[(_fb_idx >= today_820_et) & (_fb_idx <= today_eod_et)]
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
        hist_30m = _filter_from(hist_30m_raw, _data_cutoff_et) if not hist_30m_raw.empty else pd.DataFrame()

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


@st.cache_data(ttl=3600, max_entries=100, show_spinner=False)
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


@st.cache_data(ttl=3600, max_entries=100, show_spinner=False)
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
    for attempt in range(2):
        try:
            r = requests.get(url, params=params, headers=headers, timeout=8)

            if r.status_code == 429:
                time.sleep(1)
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


@st.cache_data(ttl=3600, max_entries=100, show_spinner=False)
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


@st.cache_data(ttl=3600, max_entries=100, show_spinner=False)
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


@st.cache_data(ttl=300, max_entries=100, show_spinner=False)
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
            df.index = df.index.tz_localize("America/New_York")
        else:
            df.index = df.index.tz_convert("America/New_York")
        df.columns = [c.lower() for c in df.columns]
        # Keep only standard columns
        cols = [c for c in ["open", "high", "low", "close", "volume"] if c in df.columns]
        return df[cols]
    except Exception:
        return pd.DataFrame()


@st.cache_data(ttl=300, max_entries=50, show_spinner=False)  # 5 min cache for fresher options data
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


@st.cache_data(ttl=300, max_entries=50, show_spinner=False)
def get_options_strategy_alpaca(ticker, current_price, direction, zone, api_key, api_secret):
    """
    Fetch Alpaca options chain and suggest a concrete strategy with real contracts.
    direction: 'LONG' or 'SHORT' (or None for neutral / zone-based)
    zone: 'HIGH', 'MID', or 'LOW'
    Returns dict with strategy, legs, summary text — or None on failure.
    """
    try:
        endpoint = f"/v1beta1/options/snapshots/{ticker}"
        params = {"limit": 250, "feed": "indicative"}
        data = alpaca_get(endpoint, params, api_key, api_secret)
        snapshots = data.get("snapshots", {})
        if not snapshots:
            return None

        contracts = []
        for sym, snap in snapshots.items():
            after = sym.replace(ticker, "", 1)
            if len(after) < 15:
                continue
            try:
                exp_str = after[:6]
                cp_flag = after[6]
                strike = int(after[7:15]) / 1000
                exp_date = f"20{exp_str[:2]}-{exp_str[2:4]}-{exp_str[4:6]}"
            except Exception:
                continue
            quote = snap.get("latestQuote", {})
            trade = snap.get("latestTrade", {})
            contracts.append({
                "sym": sym, "strike": strike, "exp": exp_date,
                "is_call": cp_flag == "C",
                "bid": float(quote.get("bp", 0) or 0),
                "ask": float(quote.get("ap", 0) or 0),
                "last": float(trade.get("p", 0) or 0),
                "oi": int(snap.get("openInterest", 0) or 0),
            })

        today_s = date.today().isoformat()
        contracts = [c for c in contracts if c["exp"] >= today_s]
        if not contracts:
            return None

        expirations = sorted(set(c["exp"] for c in contracts))

        # Pick two target expirations: ~7d (short) and ~21d (long)
        def _nearest_exp(target_dt):
            return min(expirations, key=lambda e: abs((datetime.strptime(e, "%Y-%m-%d").date() - target_dt).days))

        exp_short = _nearest_exp(date.today() + timedelta(days=7))
        exp_long  = _nearest_exp(date.today() + timedelta(days=21))

        # Helper: pick nearest strike to target from a list of contracts
        def _pick(clist, target_strike):
            if not clist:
                return None
            return min(clist, key=lambda c: abs(c["strike"] - target_strike))

        # Determine strategy
        is_bullish = (direction == "LONG") or (zone == "LOW" and direction != "SHORT")
        is_bearish = (direction == "SHORT") or (zone == "HIGH" and direction != "LONG")

        calls_s = [c for c in contracts if c["exp"] == exp_short and c["is_call"]]
        puts_s  = [c for c in contracts if c["exp"] == exp_short and not c["is_call"]]
        calls_l = [c for c in contracts if c["exp"] == exp_long and c["is_call"]]
        puts_l  = [c for c in contracts if c["exp"] == exp_long and not c["is_call"]]

        mid = lambda c: round((c["bid"] + c["ask"]) / 2, 2) if c else 0

        result = {"ticker": ticker, "legs": [], "exp_short": exp_short, "exp_long": exp_long}

        if is_bullish:
            # Bull Call Spread: buy lower-strike call, sell higher-strike call
            buy_c = _pick(calls_l, current_price)
            sell_c = _pick(calls_l, current_price * 1.03) if buy_c else None
            if sell_c and buy_c and sell_c["strike"] == buy_c["strike"]:
                sell_c = _pick([c for c in calls_l if c["strike"] > buy_c["strike"]], buy_c["strike"] + 1)
            if buy_c and sell_c:
                net_debit = round(mid(buy_c) - mid(sell_c), 2)
                result["strategy"] = "Bull Call Spread"
                result["legs"] = [
                    {"action": "BUY", "type": "CALL", "strike": buy_c["strike"],
                     "exp": exp_long, "bid": buy_c["bid"], "ask": buy_c["ask"], "mid": mid(buy_c)},
                    {"action": "SELL", "type": "CALL", "strike": sell_c["strike"],
                     "exp": exp_long, "bid": sell_c["bid"], "ask": sell_c["ask"], "mid": mid(sell_c)},
                ]
                max_profit = round(sell_c["strike"] - buy_c["strike"] - net_debit, 2)
                result["summary"] = (
                    f"📈 {ticker} Bull Call Spread: Buy ${buy_c['strike']:.0f}C / Sell ${sell_c['strike']:.0f}C "
                    f"Exp {exp_long} | Debit ~${net_debit:.2f} | Max Profit ~${max_profit:.2f}")
                result["alt"] = (
                    f"Alt: Long ${buy_c['strike']:.0f} Call @ ~${mid(buy_c):.2f} Exp {exp_short}")
            else:
                c = buy_c or _pick(calls_s, current_price)
                if c:
                    result["strategy"] = "Long Call"
                    result["legs"] = [{"action": "BUY", "type": "CALL", "strike": c["strike"],
                                       "exp": exp_short, "bid": c["bid"], "ask": c["ask"], "mid": mid(c)}]
                    result["summary"] = f"📈 {ticker} Long ${c['strike']:.0f} Call @ ~${mid(c):.2f} Exp {exp_short}"
                    result["alt"] = ""

        elif is_bearish:
            # Bear Put Spread: buy higher-strike put, sell lower-strike put
            buy_p = _pick(puts_l, current_price)
            sell_p = _pick(puts_l, current_price * 0.97) if buy_p else None
            if sell_p and buy_p and sell_p["strike"] == buy_p["strike"]:
                sell_p = _pick([c for c in puts_l if c["strike"] < buy_p["strike"]], buy_p["strike"] - 1)
            if buy_p and sell_p:
                net_debit = round(mid(buy_p) - mid(sell_p), 2)
                result["strategy"] = "Bear Put Spread"
                result["legs"] = [
                    {"action": "BUY", "type": "PUT", "strike": buy_p["strike"],
                     "exp": exp_long, "bid": buy_p["bid"], "ask": buy_p["ask"], "mid": mid(buy_p)},
                    {"action": "SELL", "type": "PUT", "strike": sell_p["strike"],
                     "exp": exp_long, "bid": sell_p["bid"], "ask": sell_p["ask"], "mid": mid(sell_p)},
                ]
                max_profit = round(buy_p["strike"] - sell_p["strike"] - net_debit, 2)
                result["summary"] = (
                    f"📉 {ticker} Bear Put Spread: Buy ${buy_p['strike']:.0f}P / Sell ${sell_p['strike']:.0f}P "
                    f"Exp {exp_long} | Debit ~${net_debit:.2f} | Max Profit ~${max_profit:.2f}")
                result["alt"] = (
                    f"Alt: Long ${buy_p['strike']:.0f} Put @ ~${mid(buy_p):.2f} Exp {exp_short}")
            else:
                p = buy_p or _pick(puts_s, current_price)
                if p:
                    result["strategy"] = "Long Put"
                    result["legs"] = [{"action": "BUY", "type": "PUT", "strike": p["strike"],
                                       "exp": exp_short, "bid": p["bid"], "ask": p["ask"], "mid": mid(p)}]
                    result["summary"] = f"📉 {ticker} Long ${p['strike']:.0f} Put @ ~${mid(p):.2f} Exp {exp_short}"
                    result["alt"] = ""

        else:
            # Neutral / MID → Iron Butterfly
            atm_c = _pick(calls_l, current_price)
            atm_p = _pick(puts_l, current_price)
            wing_c = _pick([c for c in calls_l if c["strike"] > current_price * 1.02], current_price * 1.03)
            wing_p = _pick([c for c in puts_l if c["strike"] < current_price * 0.98], current_price * 0.97)
            if atm_c and atm_p and wing_c and wing_p:
                credit = round(mid(atm_c) + mid(atm_p) - mid(wing_c) - mid(wing_p), 2)
                result["strategy"] = "Iron Butterfly"
                result["legs"] = [
                    {"action": "SELL", "type": "CALL", "strike": atm_c["strike"],
                     "exp": exp_long, "bid": atm_c["bid"], "ask": atm_c["ask"], "mid": mid(atm_c)},
                    {"action": "SELL", "type": "PUT", "strike": atm_p["strike"],
                     "exp": exp_long, "bid": atm_p["bid"], "ask": atm_p["ask"], "mid": mid(atm_p)},
                    {"action": "BUY", "type": "CALL", "strike": wing_c["strike"],
                     "exp": exp_long, "bid": wing_c["bid"], "ask": wing_c["ask"], "mid": mid(wing_c)},
                    {"action": "BUY", "type": "PUT", "strike": wing_p["strike"],
                     "exp": exp_long, "bid": wing_p["bid"], "ask": wing_p["ask"], "mid": mid(wing_p)},
                ]
                result["summary"] = (
                    f"🦋 {ticker} Iron Butterfly: Sell ${atm_c['strike']:.0f}C+${atm_p['strike']:.0f}P / "
                    f"Buy ${wing_c['strike']:.0f}C+${wing_p['strike']:.0f}P "
                    f"Exp {exp_long} | Credit ~${credit:.2f}")
                result["alt"] = (
                    f"Alt: Straddle Sell ${atm_c['strike']:.0f}C+${atm_p['strike']:.0f}P Exp {exp_short}")
            else:
                # Fallback: straddle
                if atm_c and atm_p:
                    result["strategy"] = "Straddle"
                    result["summary"] = (
                        f"🦋 {ticker} Straddle: ${atm_c['strike']:.0f}C @ ~${mid(atm_c):.2f} + "
                        f"${atm_p['strike']:.0f}P @ ~${mid(atm_p):.2f} Exp {exp_long}")
                    result["alt"] = ""
                else:
                    return None

        return result if result.get("summary") else None
    except Exception:
        return None


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


@st.cache_data(ttl=300, max_entries=50, show_spinner=False)  # 5 min cache for fresher options data
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


@st.cache_data(ttl=3600, max_entries=100, show_spinner=False)
def get_earnings_dates_yfinance(ticker):
    """
    Fetch earnings dates from yfinance (free, reliable, includes upcoming).
    Returns sorted list of (date_str, quarter_label, period) or [].
    """
    if not YFINANCE_AVAILABLE:
        return []
    try:
        t = yf.Ticker(ticker)
        ed = None
        try:
            ed = t.earnings_dates
        except Exception:
            return []
        if ed is None or not hasattr(ed, "empty") or ed.empty:
            return []
        events = []
        for ts, row in ed.iterrows():
            try:
                dt = ts.date() if hasattr(ts, "date") else ts
                dt_str = str(dt)
                yr = dt.year
                mo = dt.month
                if mo <= 3:   qtr = f"Q4 {yr-1}"
                elif mo <= 6: qtr = f"Q1 {yr}"
                elif mo <= 9: qtr = f"Q2 {yr}"
                else:         qtr = f"Q3 {yr}"
                events.append((dt_str, qtr, dt_str))
            except Exception:
                continue
        events.sort(key=lambda x: x[0])
        return events
    except Exception:
        return []


@st.cache_data(ttl=3600, max_entries=100, show_spinner=False)  # Cache for 1 hour
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


@st.cache_data(ttl=3600, max_entries=100, show_spinner=False)
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
    Multi-source earnings date resolver — merges all available sources.
    Priority order for fetching: yfinance (free) → Polygon (paid) → auto-detect.
    Manual dates are ADDED to (not replacing) fetched dates so the
    swing window always has the full date history to work with.
    """
    manual_events = []
    if manual_dates:
        for d in manual_dates:
            try:
                dt  = datetime.strptime(d.strip(), "%Y-%m-%d").date()
                yr  = dt.year
                mo  = dt.month
                if mo <= 3:   qtr = f"Q4 {yr-1}"
                elif mo <= 6: qtr = f"Q1 {yr}"
                elif mo <= 9: qtr = f"Q2 {yr}"
                else:         qtr = f"Q3 {yr}"
                manual_events.append((str(dt), qtr, str(dt)))
            except Exception:
                continue

    # 1. yfinance (free, includes upcoming)
    yf_events = get_earnings_dates_yfinance(ticker)

    # 2. Polygon financials (paid — supplements if yfinance is empty)
    poly_events = []
    if not yf_events:
        poly_events = get_earnings_dates_polygon(ticker, api_key, limit)

    # 3. Auto-detect from price gaps (last resort)
    auto_events = []
    if not yf_events and not poly_events and daily_df is not None and not daily_df.empty:
        auto_events = detect_earnings_from_prices(daily_df)

    # Merge: lower-priority first so higher-priority overwrites on same date
    _seen = {}
    for ev in auto_events + poly_events:
        _seen[ev[0]] = ev
    for ev in yf_events:        # yfinance overwrites auto/poly on same date
        _seen[ev[0]] = ev
    for ev in manual_events:    # manual always wins
        _seen[ev[0]] = ev

    if not _seen:
        return [], "none"

    merged = sorted(_seen.values(), key=lambda x: x[0])

    if manual_events and yf_events:   src = "manual+yfinance"
    elif manual_events and poly_events: src = "manual+polygon"
    elif manual_events and auto_events: src = "manual+auto"
    elif manual_events:                 src = "manual"
    elif yf_events:                     src = "yfinance"
    elif poly_events:                   src = "polygon"
    else:                               src = "auto-detected"

    return merged, src


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

@st.cache_data(ttl=3600, max_entries=20, show_spinner=False)
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
FIB_NEG = [0.236, 0.382, 0.5, 0.618, 1.0]   # negative retracements below swing low

def calc_fib_levels(lo, hi):
    rng = hi - lo
    lvls = {}
    for f in FIB_RET:
        lvls[f"R {f*100:.1f}%"] = hi - rng * f
    for f in FIB_EXT:
        lvls[f"E {f*100:.1f}%"] = lo + rng * f
    for f in FIB_NEG:
        lvls[f"N -{f*100:.1f}%"] = lo - rng * f
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


def get_weekly_fib_and_4h_rsi(tk_obj, check_date, current_price):
    """Return (wk_fib_label, rsi_4h_str) using current week's H/L and WTD 4-hour bars."""
    fib_label = "N/A"
    rsi_4h_val = "N/A"
    try:
        week_start = check_date - timedelta(days=check_date.weekday())  # Monday

        # ── Weekly High / Low ────────────────────────────────────────────
        _wk_hist = tk_obj.history(
            start=str(week_start - timedelta(days=1)),
            end=str(check_date + timedelta(days=1)),
        )
        if _wk_hist is not None and not _wk_hist.empty:
            # Normalise index to date for comparison
            _idx_dates = np.array([
                d.date() if hasattr(d, "date") else d for d in _wk_hist.index
            ])
            _wk_data = _wk_hist[_idx_dates >= week_start]
            if not _wk_data.empty and "High" in _wk_data.columns and "Low" in _wk_data.columns:
                wk_hi = float(_wk_data["High"].max())
                wk_lo = float(_wk_data["Low"].min())
                rng = wk_hi - wk_lo
                if rng > 0:
                    # fib_from_hi = 0 means price at weekly high, 1 means at weekly low
                    fib_from_hi = (wk_hi - current_price) / rng
                    fib_levels = [
                        (0.0, "100%"), (0.236, "78.6%"), (0.382, "61.8%"),
                        (0.5, "50%"), (0.618, "38.2%"), (0.764, "23.6%"), (1.0, "0%"),
                    ]
                    nearest = min(fib_levels, key=lambda x: abs(x[0] - fib_from_hi))
                    fib_label = nearest[1].replace("%", "")  # e.g. "61.8"

        # ── 4-Hour WTD RSI ───────────────────────────────────────────────
        _hr_hist = tk_obj.history(
            start=str(week_start - timedelta(days=1)),
            end=str(check_date + timedelta(days=1)),
            interval="1h",
        )
        if _hr_hist is not None and not _hr_hist.empty and "Close" in _hr_hist.columns:
            # Strip timezone for consistent comparison
            _hr_idx = _hr_hist.index
            if hasattr(_hr_idx, "tz") and _hr_idx.tz is not None:
                _hr_idx = _hr_idx.tz_convert("America/New_York").tz_localize(None)
            _hr_hist = _hr_hist.copy()
            _hr_hist.index = _hr_idx
            _idx_dates_hr = np.array([
                d.date() if hasattr(d, "date") else d for d in _hr_hist.index
            ])
            _wtd = _hr_hist[_idx_dates_hr >= week_start]
            if len(_wtd) >= 3:
                _4h_close = _wtd["Close"].resample("4h").last().dropna()
                if len(_4h_close) >= 3:
                    _delta = _4h_close.diff()
                    _gain = _delta.clip(lower=0)
                    _loss = (-_delta).clip(lower=0)
                    _period = min(14, len(_4h_close) - 1)
                    _avg_gain = _gain.rolling(window=_period, min_periods=1).mean()
                    _avg_loss = _loss.rolling(window=_period, min_periods=1).mean()
                    _rs = _avg_gain / _avg_loss.replace(0, np.nan)
                    _rsi = 100 - (100 / (1 + _rs))
                    _last_rsi = _rsi.iloc[-1]
                    if not np.isnan(_last_rsi):
                        rsi_4h_val = f"{_last_rsi:.0f}"
    except Exception as _e:
        print(f"⚠️ Weekly Fib/4H RSI error: {_e}")
    return fib_label, rsi_4h_val


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
# CENTRAL PIVOT RANGE (CPR)
# ──────────────────────────────────────────────

def calc_cpr(daily_df):
    """
    Calculate CPR from the previous session's data (projects today's levels).
    P  = (PrevHigh + PrevLow + PrevClose) / 3
    BC = (PrevHigh + PrevLow) / 2
    TC = 2*P - BC
    CPR type: Narrow when TC≈BC (close near midpoint of prev range), Wide when spread is large.
    """
    if daily_df is None or len(daily_df) < 2:
        return None

    prev = daily_df.iloc[-2]
    prev_high  = float(prev["high"])
    prev_low   = float(prev["low"])
    prev_close = float(prev["close"])

    p  = (prev_high + prev_low + prev_close) / 3
    bc = (prev_high + prev_low) / 2
    tc = 2 * p - bc

    # Ensure TC is always the upper band and BC the lower
    top_central = max(tc, bc)
    bot_central = min(tc, bc)

    width     = top_central - bot_central
    width_pct = (width / p * 100) if p > 0 else 0.0

    if width_pct < 0.15:
        cpr_type = "Narrow"
    elif width_pct > 0.5:
        cpr_type = "Wide"
    else:
        cpr_type = "Normal"

    return {
        "p":         round(p, 2),
        "bc":        round(bot_central, 2),
        "tc":        round(top_central, 2),
        "width":     round(width, 4),
        "width_pct": round(width_pct, 3),
        "cpr_type":  cpr_type,
    }


def cpr_interpretation(cpr_type, cpr_position):
    """Return a one-line intraday trading interpretation of the CPR type + price position."""
    _map = {
        ("Narrow", "Above"):  "Trending day ↑ — price above TC, strong bull momentum",
        ("Narrow", "Inside"): "Trending day — inside CPR, wait for TC/BC breakout",
        ("Narrow", "Below"):  "Trending day ↓ — price below BC, strong bear momentum",
        ("Wide",   "Above"):  "Range day — above TC, may pull back to CPR",
        ("Wide",   "Inside"): "Range day — chop expected, trade TC↔BC bounces",
        ("Wide",   "Below"):  "Range day — below BC, may bounce back to CPR",
        ("Normal", "Above"):  "Bullish bias — above TC, P acts as support",
        ("Normal", "Inside"): "Neutral — inside CPR, watch TC/BC for breakout",
        ("Normal", "Below"):  "Bearish bias — below BC, P acts as resistance",
    }
    return _map.get((cpr_type, cpr_position), "—")


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


def analyze_institutional_control(weekly_df, lookback=26):
    """
    Analyze weekly data to determine institutional vs retail control and market phase.
    Returns tuple: (control_str, phase_str, emoji, days_in_phase)
    
    Phases:
      - ACCUMULATION: Price <= POC, high volume, ranging (Smart money buying)
      - MARKUP: Price trending up above POC, increasing volume (Institutions pushing)
      - DISTRIBUTION: Price at/above VAH, declining volume (Institutions unloading)
      - MARKDOWN: Price below VAL, declining volume (Panic selling)
    
    Control:
      - 🏛️ INST: Institutional control (smart money direction)
      - 👥 RETAIL: Retail control (trend-chasing, weak hands)
      - ⚖️ MIXED: No clear control
    """
    try:
        if weekly_df is None or len(weekly_df) < 8:
            return "N/A", "N/A", "❓", 0
        
        df_wk = weekly_df.tail(lookback).copy()
        if df_wk.empty or len(df_wk) < 5:
            return "N/A", "N/A", "❓", 0
        
        # Ensure 1D series for all OHLCV columns (flattens any MultiIndex or (N,1) column slices)
        close_s = df_wk["Close"].squeeze()
        high_s  = df_wk["High"].squeeze()
        low_s   = df_wk["Low"].squeeze()
        vol_s   = df_wk["Volume"].squeeze()

        # Get current and previous week close, high, low
        current_close = float(np.asarray(close_s.iloc[-1]).squeeze())
        current_high = float(np.asarray(high_s.iloc[-1]).squeeze())
        current_low = float(np.asarray(low_s.iloc[-1]).squeeze())
        
        # Calculate 26-week (6-month) high/low for context
        wk_high_26 = float(np.asarray(high_s.max()).squeeze())
        wk_low_26 = float(np.asarray(low_s.min()).squeeze())
        wk_range = wk_high_26 - wk_low_26
        
        if wk_range <= 0:
            return "N/A", "N/A", "❓", 0
        
        # POC = 50% of range (mid-level volume typically congregates here)
        poc = wk_low_26 + (wk_range * 0.5)
        vah = wk_low_26 + (wk_range * 0.70)  # Value Area High
        val = wk_low_26 + (wk_range * 0.30)  # Value Area Low
        
        # Volume trend: recent vs prior
        recent_vol = float(np.asarray(vol_s.iloc[-5:].mean()).squeeze()) if len(df_wk) >= 5 else float(np.asarray(vol_s.mean()).squeeze())
        prior_vol = float(np.asarray(vol_s.iloc[-15:-5].mean()).squeeze()) if len(df_wk) >= 15 else float(np.asarray(vol_s.mean()).squeeze())
        vol_trend_ratio = recent_vol / prior_vol if prior_vol > 0 else 1.0
        
        # Price trend: is price making higher highs or lower lows?
        highs_5 = np.asarray(high_s.iloc[-5:]).ravel()
        lows_5 = np.asarray(low_s.iloc[-5:]).ravel()
        price_trend_up = bool(float(highs_5[-1]) > float(highs_5[0])) if len(highs_5) > 0 else False
        price_trend_down = bool(float(lows_5[-1]) < float(lows_5[0])) if len(lows_5) > 0 else False
        
        # Close position relative to range
        close_pct = (current_close - wk_low_26) / wk_range * 100 if wk_range > 0 else 50.0
        
        # Determine current phase and control FIRST
        # ACCUMULATION: Low prices, high volume, consolidating (Smart money buying dip)
        if (current_close <= poc) and (vol_trend_ratio > 1.1) and (not price_trend_down):
            phase = "ACCUMULATION"
            control = "🏛️ INST"
            emoji = "📦"
        
        # MARKUP: Price trending up from POC, increasing volume (Institutions pushing higher)
        elif (current_close > poc) and price_trend_up and (vol_trend_ratio > 1.0):
            phase = "MARKUP"
            control = "🏛️ INST"
            emoji = "📈"
        
        # DISTRIBUTION: High prices at VAH or above, declining volume (Smart money exiting)
        elif (current_close >= vah) and (vol_trend_ratio < 1.2):
            phase = "DISTRIBUTION"
            control = "👥 RETAIL"
            emoji = "📉"
        
        # MARKDOWN: Trending down below VAL, declining volume (Panic selling)
        elif (current_close <= val) and price_trend_down and (vol_trend_ratio <= 1.1):
            phase = "MARKDOWN"
            control = "👥 RETAIL"
            emoji = "💔"
        
        # Mixed/Uncertain phases
        elif (current_close > poc) and (current_close < vah):
            # Mid-range, unclear direction
            if price_trend_up and (vol_trend_ratio > 1.1):
                phase = "EARLY MARKUP"
                control = "🏛️ INST"
                emoji = "📈"
            elif price_trend_down and (vol_trend_ratio > 1.1):
                phase = "CORRECTION"
                control = "⚖️ MIXED"
                emoji = "⚠️"
            else:
                phase = "CONSOLIDATION"
                control = "⚖️ MIXED"
                emoji = "🔄"
        else:
            phase = "RANGING"
            control = "⚖️ MIXED"
            emoji = "➡️"
        
        # Calculate days since phase started by walking backwards
        # Find the week where phase conditions CHANGED to current phase
        days_in_phase = 7  # At minimum 1 week (current week)
        try:
            current_date = pd.Timestamp(df_wk.index[-1])
            
            # Walk backwards from second-to-last week to find phase transition point
            for i in range(len(df_wk) - 2, -1, -1):
                prev_close = float(np.asarray(close_s.iloc[i]).squeeze())
                prev_highs_5 = np.asarray(high_s.iloc[max(0, i-4):i+1]).ravel()
                prev_lows_5 = np.asarray(low_s.iloc[max(0, i-4):i+1]).ravel()
                prev_vol_recent = float(np.asarray(vol_s.iloc[max(0, i-4):i+1].mean()).squeeze())
                prev_vol_prior = float(np.asarray(vol_s.iloc[max(0, i-14):max(0, i-4)].mean()).squeeze()) if i >= 4 else prev_vol_recent
                prev_vol_ratio = prev_vol_recent / prev_vol_prior if prev_vol_prior > 0 else 1.0
                prev_trend_up = bool(float(prev_highs_5[-1]) > float(prev_highs_5[0])) if len(prev_highs_5) > 0 else False
                prev_trend_down = bool(float(prev_lows_5[-1]) < float(prev_lows_5[0])) if len(prev_lows_5) > 0 else False
                
                # Check if previous week was in same phase
                in_same_phase = False
                
                if phase == "ACCUMULATION":
                    in_same_phase = (prev_close <= poc) and (prev_vol_ratio > 1.1) and (not prev_trend_down)
                elif phase == "MARKUP":
                    in_same_phase = (prev_close > poc) and prev_trend_up and (prev_vol_ratio > 1.0)
                elif phase == "DISTRIBUTION":
                    in_same_phase = (prev_close >= vah) and (prev_vol_ratio < 1.2)
                elif phase == "MARKDOWN":
                    in_same_phase = (prev_close <= val) and prev_trend_down and (prev_vol_ratio <= 1.1)
                elif phase == "EARLY MARKUP":
                    in_same_phase = (prev_close > poc) and (prev_close < vah) and prev_trend_up and (prev_vol_ratio > 1.1)
                elif phase == "CORRECTION":
                    in_same_phase = (prev_close > poc) and (prev_close < vah) and prev_trend_down and (prev_vol_ratio > 1.1)
                elif phase == "CONSOLIDATION":
                    in_same_phase = (prev_close > poc) and (prev_close < vah) and (not prev_trend_up) and (not prev_trend_down)
                elif phase == "RANGING":
                    in_same_phase = not ((prev_close <= poc) or (prev_close >= vah) or prev_trend_down)
                
                # If phase changed, calculate days from transition week
                if not in_same_phase:
                    transition_date = pd.Timestamp(df_wk.index[i])
                    days_in_phase = (current_date - transition_date).days + 7  # +7 because week i is where transition happened
                    break
                
                # If we reach the beginning, phase has lasted entire lookback period
                if i == 0:
                    start_date = pd.Timestamp(df_wk.index[0])
                    days_in_phase = (current_date - start_date).days + 7
        except Exception as e:
            days_in_phase = 7
        
        return control, phase, emoji, int(days_in_phase)
    
    except Exception as e:
        return "N/A", f"Error", "❓", 0


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

@st.cache_data(ttl=3600, max_entries=150, show_spinner=False)  # Cache for 1 hour
def get_fundamentals(ticker):
    """
    Fetch fundamental data via yfinance.
    Returns dict with valuation, growth, profitability, risk, and analyst data.
    """
    if not YFINANCE_AVAILABLE:
        return None
    
    try:
        stock = yf.Ticker(ticker)
        info = None
        try:
            info = stock.info
        except Exception:
            info = None
        
        if not info or not isinstance(info, dict) or "symbol" not in info:
            # Fallback to fast_info if quoteSummary / crumb failed (e.g. 401 on cloud platforms)
            try:
                fi = stock.fast_info
                cp = _safe_float(getattr(fi, "last_price", None))
                if cp <= 0:
                    return None
                hi52 = _safe_float(getattr(fi, "year_high", None))
                lo52 = _safe_float(getattr(fi, "year_low", None))
                mc = _safe_float(getattr(fi, "market_cap", None))
                pos52 = ((cp - lo52) / (hi52 - lo52) * 100) if (hi52 and lo52 and hi52 > lo52) else 50
                pct_hi = ((cp - hi52) / hi52 * 100) if hi52 and hi52 > 0 else None
                mc_str = (f"${mc / 1e12:.1f}T" if mc >= 1e12 else f"${mc / 1e9:.1f}B" if mc >= 1e9 else f"${mc / 1e6:.1f}M" if mc >= 1e6 else f"${mc:,.0f}") if mc else "N/A"
                return {
                    "symbol": ticker, "pe_ratio": None, "forward_pe": None, "peg_ratio": None,
                    "market_cap": mc, "market_cap_str": mc_str, "current_price": cp,
                    "target_price": None, "target_low": None, "target_high": None, "target_upside": None,
                    "revenue_growth": None, "earnings_growth": None, "revenue": None, "revenue_str": "N/A",
                    "profit_margin": None, "gross_margin": None, "operating_margin": None,
                    "roe": None, "roa": None, "debt_to_equity": None, "current_ratio": None,
                    "beta": None, "short_ratio": None, "short_pct": None, "dividend_yield": None,
                    "payout_ratio": None, "sector": "N/A", "industry": "N/A",
                    "week52_high": hi52, "week52_low": lo52, "week52_position": pos52, "pct_from_high": pct_hi,
                    "recommendation": "", "rec_mean": None, "num_analysts": None,
                    "trailing_eps": None, "forward_eps": None, "valuation": "N/A", "valuation_color": "#6b7099",
                    "flags": [],
                }
            except Exception:
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
            "quote_type": str(info.get("quoteType", "")).upper(),
        }
    except Exception:
        return None


# ──────────────────────────────────────────────
# MACRO MARKET INDICATORS — moved to macro_analysis.py
# ──────────────────────────────────────────────
if MACRO_MODULE_AVAILABLE:
    _render_macro_dashboard = render_macro_dashboard
    _sector_strength_from_scan = sector_strength_from_scan
else:
    # Fallback stubs if macro_analysis.py is missing
    SECTOR_ETFS = {}
    def get_macro_snapshot(): return []
    def _render_macro_dashboard(*a, **kw): st.caption("Macro module not loaded.")
    def _sector_strength_from_scan(r): return []
    def _macro_get_sector_performance(*a, **kw): return []

# Compatibility wrapper: bridges old (api_key, api_secret, data_source) → new module signature
@st.cache_data(ttl=300, max_entries=20, show_spinner=False)
def get_sector_performance(api_key, api_secret, data_source):
    """Thin wrapper around macro_analysis.get_sector_performance."""
    if data_source == "Alpaca":
        return _macro_get_sector_performance(get_daily_bars_alpaca, api_key, api_secret)
    else:
        return _macro_get_sector_performance(get_daily_bars, api_key)


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
EXCLUDED_INSTRUMENTS = set()  # No hard exclusions — mean-rev instruments get SKIP status instead

# ── Watchlist Screener: ticker universe via yfinance screener ──────────────
_DOW_30 = [
    "AMZN","AMGN","AAPL","BA","CAT","CSCO","CVX","DIS","DOW","GS",
    "HD","HON","IBM","JNJ","JPM","KO","MCD","MMM","MRK","MSFT",
    "NKE","NVDA","PG","CRM","SHW","TRV","UNH","V","VZ","WMT",
]

@st.cache_data(ttl=86400, max_entries=20, show_spinner=False)
def _fetch_universe(name: str, min_price: float = 5.0, min_mcap: float = 0) -> list:
    """Fetch ticker list using yfinance screener API."""
    import yfinance as yf
    try:
        if name == "Dow 30":
            return list(_DOW_30)
        # Build market-cap thresholds per universe
        mcap_map = {
            "S&P 500":    10_000_000_000,   # $10B+ large cap
            "NASDAQ 100": 10_000_000_000,
            "Large Cap":  10_000_000_000,
            "Mid Cap":    2_000_000_000,
            "Small Cap":  300_000_000,
        }
        mcap_min = min_mcap if min_mcap > 0 else mcap_map.get(name, 2_000_000_000)
        operands = [
            yf.EquityQuery("IS-IN", ["exchange", "NMS", "NYQ"]),
            yf.EquityQuery("GTE", ["intradaymarketcap", mcap_min]),
        ]
        if min_price > 0:
            operands.append(yf.EquityQuery("GTE", ["intradayprice", min_price]))
        # NASDAQ 100: additionally filter to NASDAQ exchange only
        if name == "NASDAQ 100":
            operands = [
                yf.EquityQuery("IS-IN", ["exchange", "NMS"]),
                yf.EquityQuery("GTE", ["intradaymarketcap", mcap_min]),
            ]
            if min_price > 0:
                operands.append(yf.EquityQuery("GTE", ["intradayprice", min_price]))
        q = yf.EquityQuery("AND", operands)
        tickers = []
        for offset in range(0, 750, 250):
            result = yf.screen(q, sortField="intradaymarketcap", sortAsc=False,
                               size=250, offset=offset)
            quotes = result.get("quotes", [])
            if not quotes:
                break
            tickers.extend(s["symbol"] for s in quotes if "symbol" in s)
            if len(quotes) < 250:
                break
        # Deduplicate while preserving order
        seen = set()
        unique = []
        for t in tickers:
            if t not in seen:
                seen.add(t)
                unique.append(t)
        return unique
    except Exception:
        return []


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
    #    NOTE: mean-rev instruments still supported — they get SKIP status with
    #    entry levels so users can see them in the scanner.
    if daily_df is not None and not daily_df.empty and len(daily_df) >= 40:
        profile = _classify_instrument(t, daily_df)
        if profile["is_mean_rev"]:
            pct   = profile["atr_pct"] * 100
            pers  = profile["persistence"] * 100
            qt    = profile["quote_type"]
            return True, (
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

    # quoteType from cached fundamentals (avoids duplicate slow network call to Yahoo Finance)
    quote_type = "UNKNOWN"
    try:
        _fund = get_fundamentals(ticker)
        if _fund and _fund.get("quote_type"):
            quote_type = _fund["quote_type"]
    except Exception:
        pass
    if quote_type == "UNKNOWN" and YFINANCE_AVAILABLE:
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


@st.cache_data(ttl=300, max_entries=100, show_spinner=False)  # Cache for 5 min — same ticker won't change between tabs
def scan_single_stock(ticker, api_key, api_secret, data_source, use_fib=True, fib_tol=2.0, use_strategy=False, as_of_date=None):
    """
    Scan a single stock and return verdict/confidence.
    as_of_date: date object to backtest — truncates data to that date.
    Returns dict with ticker, verdict, confidence, score, signals or None on error.
    """
    try:
        end_date = as_of_date if as_of_date and as_of_date < date.today() else date.today()
        start_date = end_date - timedelta(days=365)  # 1 year for Fib levels
        
        # Fetch daily data
        if data_source == "Alpaca":
            daily_df = get_daily_bars_alpaca(ticker, str(start_date), str(end_date), api_key, api_secret)
        else:
            daily_df = get_daily_bars(ticker, str(start_date), str(end_date), api_key)

        # Fallback to yfinance if primary source returned no data
        if (daily_df is None or daily_df.empty or len(daily_df) < 20) and YFINANCE_AVAILABLE:
            try:
                import yfinance as yf
                _yf = yf.Ticker(ticker)
                if as_of_date and as_of_date < date.today():
                    _yf_start = as_of_date - timedelta(days=365)
                    _yf_hist = _yf.history(start=str(_yf_start), end=str(as_of_date + timedelta(days=1)), auto_adjust=True)
                else:
                    _yf_hist = _yf.history(period="1y", auto_adjust=True)
                if _yf_hist is not None and not _yf_hist.empty and len(_yf_hist) >= 20:
                    daily_df = _yf_hist.rename(columns={
                        "Open": "open", "High": "high", "Low": "low",
                        "Close": "close", "Volume": "volume"
                    })[["open", "high", "low", "close", "volume"]]
            except Exception:
                pass

        # Truncate to as_of_date if backtesting
        if as_of_date and as_of_date < date.today() and daily_df is not None and not daily_df.empty:
            # Handle both date and Timestamp index types
            if hasattr(daily_df.index[0], 'date') and not isinstance(daily_df.index[0], date):
                # Timestamp index (yfinance) — compare as Timestamp
                daily_df = daily_df[daily_df.index <= pd.Timestamp(as_of_date)]
            else:
                # date index (Alpaca) — compare as date
                daily_df = daily_df[daily_df.index <= as_of_date]

        if daily_df is None or daily_df.empty or len(daily_df) < 20:
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

        # ── Risk/Reward ratios ──
        def _scan_rr(e, s, t, d):
            try:
                risk = (e - s) if d in ("BULLISH", "LEAN BULLISH") else (s - e)
                rew  = (t - e) if d in ("BULLISH", "LEAN BULLISH") else (e - t)
                return round(rew / risk, 2) if risk > 0 else 0.0
            except: return 0.0
        _dir = verdict
        rr_t1 = _scan_rr(entry, stop_loss, target1, _dir) if stop_loss and target1 else 0.0
        rr_t2 = _scan_rr(entry, stop_loss, target2, _dir) if stop_loss and target2 else 0.0
        best_rr = max(rr_t1, rr_t2)

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

        # ── Moving average bias (price vs 30-day SMA) ──
        try:
            _ma30 = daily_df["close"].rolling(30).mean().iloc[-1]
            ma_bias = "Positive" if current_price >= _ma30 else "Negative"
        except Exception:
            ma_bias = "N/A"

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
            "rr_t1":            rr_t1,
            "rr_t2":            rr_t2,
            "best_rr":          best_rr,
            "ma_bias":          ma_bias,
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

        # CPR — Central Pivot Range
        _cpr = calc_cpr(daily_df)
        if _cpr:
            result["cpr_p"]         = _cpr["p"]
            result["cpr_tc"]        = _cpr["tc"]
            result["cpr_bc"]        = _cpr["bc"]
            result["cpr_type"]      = _cpr["cpr_type"]
            result["cpr_width_pct"] = _cpr["width_pct"]
            _pos = ("Above" if current_price > _cpr["tc"] else
                    "Below" if current_price < _cpr["bc"] else "Inside")
            result["cpr_position"]       = _pos
            result["cpr_interpretation"] = cpr_interpretation(_cpr["cpr_type"], _pos)
        else:
            result["cpr_p"] = result["cpr_tc"] = result["cpr_bc"] = None
            result["cpr_type"] = "N/A"
            result["cpr_width_pct"] = None
            result["cpr_position"] = "N/A"
            result["cpr_interpretation"] = "—"
        # Attach extra fundamental fields — always set defaults so columns never vanish
        _fund_keys = ("sector", "pe_ratio", "forward_pe", "peg_ratio",
                      "revenue_growth", "earnings_growth", "profit_margin",
                      "roe", "debt_to_equity", "beta", "rec_key", "num_analysts",
                      "dividend_yield", "short_pct", "week52_position",
                      "pct_from_high", "revenue_str", "flags")
        if fundamentals:
            for fkey in _fund_keys:
                result[fkey] = fundamentals.get(fkey)
            result["analyst_target"] = fundamentals.get("target_price")
            flags_raw = fundamentals.get("flags", [])
            result["flags"] = " · ".join(f[0] for f in flags_raw) if isinstance(flags_raw, list) and flags_raw else (flags_raw if isinstance(flags_raw, str) else "")
        else:
            for fkey in _fund_keys:
                result[fkey] = None
            result["analyst_target"] = None
            result["sector"] = "N/A"
            result["flags"] = ""
        return result
    except Exception as e:
        # Re-raise with ticker context so the caller can surface the real error
        raise RuntimeError(f"scan_single_stock({ticker}): {type(e).__name__}: {e}") from e


def scan_stocks(api_key, api_secret, data_source, watchlist=None, use_fib=True, fib_tol=2.0, use_strategy=False):
    """
    Scan multiple stocks and return bullish + high confidence ones.
    Uses ThreadPoolExecutor for parallel I/O-bound scanning.
    Returns list of result dicts sorted by score descending.
    """
    if watchlist is None:
        watchlist = SCAN_WATCHLIST
    
    results = []
    executor = get_thread_pool()
    futures = {
        executor.submit(scan_single_stock, ticker, api_key, api_secret, data_source, use_fib, fib_tol, use_strategy): ticker
        for ticker in watchlist
    }
    for future in concurrent.futures.as_completed(futures):
        try:
            result = future.result()
            if result:
                results.append(result)
        except Exception:
            pass
    
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
    trim_memory()
    return top_setups, results


def _parallel_scan_with_progress(tickers, api_key, api_secret, data_source,
                                  use_fib=True, fib_tol=2.0, use_strategy=False,
                                  progress_bar=None, status_text=None,
                                  fetch_fundamentals=False, as_of_date=None):
    """
    Scan tickers in parallel using ThreadPoolExecutor with optional UI progress.
    as_of_date: date object for backtesting — passed through to scan_single_stock.
    Returns (results, errors, no_data) lists.
    """
    results, errors, no_data = [], [], []
    total = len(tickers)
    completed = 0
    executor = get_thread_pool()

    # Submit all scan jobs
    futures = {
        executor.submit(scan_single_stock, t, api_key, api_secret, data_source, use_fib, fib_tol, use_strategy, as_of_date): t
        for t in tickers
    }

    for future in concurrent.futures.as_completed(futures):
        ticker = futures[future]
        completed += 1
        if progress_bar:
            progress_bar.progress(completed / total)
        if status_text:
            status_text.text(f"Analyzing {ticker}... ({completed}/{total})")

        try:
            result = future.result()
            if result:
                if fetch_fundamentals:
                    # scan_single_stock already fetches fundamentals;
                    # just ensure flags is a display string (not raw list)
                    flags_val = result.get("flags")
                    if isinstance(flags_val, list):
                        result["flags"] = " · ".join(f[0] for f in flags_val) if flags_val else ""
                results.append(result)
            else:
                no_data.append(ticker)
        except Exception as e:
            err_str = str(e)
            is_timeout = any(kw in err_str.lower() for kw in
                             ("timeout", "timed out", "read timed", "connectionerror",
                              "connection reset", "remotedisconnected", "connection aborted"))
            errors.append((ticker, err_str, is_timeout))

        # Periodic memory trimming to keep RSS bounded during long scans
        if completed % 10 == 0:
            trim_memory()

    trim_memory()
    return results, errors, no_data


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

# Run database migrations
_migrate_trades_table()

# ──────────────────────────────────────────────
# SCHEDULED TELEGRAM MESSAGES (8:00 AM & 8:30 AM CST)
# ──────────────────────────────────────────────
if SCHEDULER_AVAILABLE and TELEGRAM_ENABLED:
    @st.cache_resource
    def _init_telegram_scheduler():
        try:
            sched = BackgroundScheduler(daemon=True)
            sched.add_job(
                send_market_risk_message,
                "cron",
                hour=8,
                minute=0,
                timezone="US/Central",
                id="market_risk_8am",
                replace_existing=True,
            )
            sched.add_job(
                send_tracking_initiated_message,
                "cron",
                hour=8,
                minute=30,
                timezone="US/Central",
                id="tracking_initiated_830am",
                replace_existing=True,
            )
            sched.add_job(
                send_swing_morning_brief,
                "cron",
                hour=8,
                minute=35,
                timezone="US/Central",
                id="swing_morning_brief_835am",
                replace_existing=True,
            )
            sched.start()
            print("✅ Telegram scheduler started (singleton) - Messages at 8:00 AM, 8:30 AM, and 8:35 AM CST")
            return sched
        except Exception as e:
            print(f"[!] Scheduler setup error: {e}")
            return None

    _global_telegram_scheduler = _init_telegram_scheduler()
elif not SCHEDULER_AVAILABLE:
    print("⚠️ APScheduler not installed - scheduled messages disabled")

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

    # Load keys directly from config.py — no UI input needed
    from config import (
        ALPACA_API_KEY as _cfg_alpaca_key,
        ALPACA_API_SECRET as _cfg_alpaca_secret,
        POLYGON_API_KEY as _cfg_polygon_key,
        FINNHUB_API_KEY as _cfg_finnhub_key,
    )

    st.markdown("### ⚙️ Global Settings")
    data_source = st.radio(
        "Data Source",
        options=["Alpaca", "Polygon"],
        index=0,
        horizontal=True,
        help="Alpaca: Free real-time data · Polygon: Free tier has ~7 day delay on hourly bars",
    )

    if data_source == "Alpaca":
        api_key = _cfg_alpaca_key or ""
        api_secret = _cfg_alpaca_secret or ""
        polygon_key = _cfg_polygon_key or _cfg_alpaca_key or ""
    else:
        api_key = _cfg_polygon_key or ""
        api_secret = None
        polygon_key = api_key

    finnhub_api_key = _cfg_finnhub_key or ""

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
    st.markdown("---")
    st.markdown("### 📱 Telegram Configuration")
    
    tg_mode = st.radio("Telegram Setup", options=["Use Default Bot", "Use Custom Bot"], index=0, horizontal=True, key="tg_mode")
    
    if tg_mode == "Use Custom Bot":
        custom_token = st.text_input(
            "Custom Bot Token", type="password", 
            placeholder="Enter your bot token",
            key="custom_tg_token"
        )
        custom_chat_id = st.text_input(
            "Custom Chat ID", 
            placeholder="Enter your chat ID",
            key="custom_tg_chat_id"
        )
        
        if custom_token and custom_chat_id:
            st.session_state["_telegram_bot_token"] = custom_token
            st.session_state["_telegram_chat_id"] = custom_chat_id
            st.caption("✅ Custom bot configured for this session")
        else:
            st.caption("⚠️ Enter both token and Chat ID")
    else:
        st.session_state["_telegram_bot_token"] = TELEGRAM_BOT_TOKEN
        st.session_state["_telegram_chat_id"] = TELEGRAM_CHAT_ID
        st.caption(f"✅ Using default bot (Chat ID: {TELEGRAM_CHAT_ID})")
    
    # ── Telegram Test Section ──
    if TELEGRAM_ENABLED:
        st.markdown("---")
        st.markdown("### 📱 Telegram Test")
        
        col_t1, col_t2 = st.columns(2)
        with col_t1:
            if st.button("📊 Test Market Risk", key="test_market_risk", use_container_width=True):
                with st.spinner("Fetching live market data..."):
                    send_market_risk_message()  # Fetches real data
                st.success("✅ Market Risk message sent with live data!")
        
        with col_t2:
            if st.button("🤖 Test Tracking Init", key="test_tracking", use_container_width=True):
                send_tracking_initiated_message()
                st.success("✅ Tracking message sent!")
        
        st.caption("Messages scheduled for 8:00 AM & 8:30 AM CST daily")
    
    # ── Debug Section ──
    st.markdown("---")
    st.markdown("### 🔧 Debug Info")

    st.checkbox("🔍 Log fragment runs to terminal", key="_debug_fragments", value=False)

    if st.checkbox("Show all trades in database", key="show_all_trades_debug"):
        all_trades_debug = get_all_trades()
        st.write(f"**Total trades in database:** {len(all_trades_debug)}")
        
        if all_trades_debug:
            # Group by status
            open_debug = [t for t in all_trades_debug if t["status"] == "OPEN"]
            closed_debug = [t for t in all_trades_debug if t["status"] == "CLOSED"]
            
            st.write(f"**Open trades:** {len(open_debug)}")
            if open_debug:
                for t in open_debug:
                    st.write(f"  • {t['ticker']} {t['direction']} @ ${t['entry_price']:.2f} · Action: {t.get('action', 'N/A')}")
            
            st.write(f"**Closed trades:** {len(closed_debug)}")
            if closed_debug:
                for t in closed_debug[:5]:  # Show last 5
                    st.write(f"  • {t['ticker']} {t['direction']} · P&L: {t.get('pnl_pct', 0):+.2f}%")


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
_BASE_WATCHLIST = "AAPL,MSFT,GOOGL,AMZN,NVDA,META,TSLA,AMD,NFLX,CRM,ORCL,ADBE,INTC,PYPL,SQ,SHOP,COIN,UBER,ABNB,SNOW,BA,CAT,GS,JPM,V,MA,DIS,NKE,SBUX,MCD,XOM,CVX,PFE,JNJ,UNH,MRNA,LLY,ABBV,BMY,MRK,SPY,QQQ,IWM,DIA,XLF,XLE,XLK,ARKK,SOXX,SMH"

# ── Telegram Watchlist: pull tickers sent to separate bot & merge ──────────
if "_tg_watchlist_tickers" not in st.session_state:
    st.session_state["_tg_watchlist_tickers"] = []
    # Poll Telegram for new messages and persist locally, then load today's tickers
    try:
        tg_poll_and_store()
        _tg_cached = fetch_today_watchlist(force=False)
        if _tg_cached:
            st.session_state["_tg_watchlist_tickers"] = _tg_cached
    except Exception:
        pass

_tg_tickers = st.session_state.get("_tg_watchlist_tickers", [])
if _tg_tickers:
    _base_set = set(t.strip().upper() for t in _BASE_WATCHLIST.split(","))
    _tg_new = [t for t in _tg_tickers if t not in _base_set]
    default_watchlist = ",".join(_tg_new + [t.strip() for t in _BASE_WATCHLIST.split(",")])
else:
    default_watchlist = _BASE_WATCHLIST

# ──────────────────────────────────────────────
# 8 AM CST AUTO-SCAN — Rank 1 tickers → Intraday list (DB-persisted)
# ──────────────────────────────────────────────
def _save_rank1_to_db(scan_date, tickers):
    """Save Rank 1 tickers for a given date to DB. Always writes a marker row so we know scan ran."""
    conn = _get_trade_db()
    try:
        conn.execute("DELETE FROM auto_scan_rank1 WHERE scan_date = ?", (scan_date,))
        # Always insert a marker row (ticker='__SCAN_DONE__') so we know scan completed
        conn.execute("INSERT OR IGNORE INTO auto_scan_rank1 (scan_date, ticker) VALUES (?, '__SCAN_DONE__')", (scan_date,))
        for t in tickers:
            conn.execute("INSERT OR IGNORE INTO auto_scan_rank1 (scan_date, ticker) VALUES (?, ?)", (scan_date, t))
        conn.commit()
    finally:
        conn.close()

def _load_rank1_from_db(scan_date):
    """Load Rank 1 tickers for a given date from DB. Returns list of tickers (excludes marker)."""
    conn = _get_trade_db()
    try:
        rows = conn.execute("SELECT ticker FROM auto_scan_rank1 WHERE scan_date = ? AND ticker != '__SCAN_DONE__'", (scan_date,)).fetchall()
        return [r["ticker"] for r in rows]
    finally:
        conn.close()

def _scan_already_done_today(scan_date):
    """Check if the auto-scan already ran for the given date."""
    conn = _get_trade_db()
    try:
        row = conn.execute("SELECT 1 FROM auto_scan_rank1 WHERE scan_date = ? AND ticker = '__SCAN_DONE__'", (scan_date,)).fetchone()
        return row is not None
    finally:
        conn.close()

_BASE_INTRADAY_TICKERS = ["SPY", "QQQ", "AAPL", "MSFT", "NVDA", "TSLA", "AMZN", "META", "GOOG"]

def _seed_intraday_tickers_for_date(scan_date):
    """Ensure base tickers exist in intraday_tickers for the given date."""
    conn = _get_trade_db()
    try:
        for t in _BASE_INTRADAY_TICKERS:
            conn.execute(
                "INSERT OR IGNORE INTO intraday_tickers (scan_date, ticker, source) VALUES (?, ?, 'base')",
                (scan_date, t),
            )
        conn.commit()
    finally:
        conn.close()

def _add_scan_tickers_to_intraday(scan_date, tickers):
    """Append auto-scanned Rank 1 tickers to intraday_tickers (deduped)."""
    conn = _get_trade_db()
    try:
        for t in tickers:
            conn.execute(
                "INSERT OR IGNORE INTO intraday_tickers (scan_date, ticker, source) VALUES (?, ?, 'scan')",
                (scan_date, t),
            )
        conn.commit()
    finally:
        conn.close()

def _load_intraday_tickers(scan_date):
    """Load all intraday tickers for the date from DB (base first, then scan). Returns list of tickers."""
    conn = _get_trade_db()
    try:
        rows = conn.execute(
            "SELECT ticker, source FROM intraday_tickers WHERE scan_date = ? ORDER BY source ASC, id ASC",
            (scan_date,),
        ).fetchall()
        return [r["ticker"] for r in rows]
    finally:
        conn.close()

def _add_holdings_scan(scan_date, tickers_data):
    """Add holdings tickers to holdings_scan table (deduped).
    tickers_data: list of dicts with 'ticker', 'quantity', 'avg_cost' keys"""
    conn = _get_trade_db()
    try:
        for item in tickers_data:
            conn.execute(
                "INSERT OR IGNORE INTO holdings_scan (scan_date, ticker, quantity, avg_cost, source) VALUES (?, ?, ?, ?, 'csv')",
                (scan_date, item.get('ticker', '').upper().strip(), item.get('quantity'), item.get('avg_cost')),
            )
        conn.commit()
    finally:
        conn.close()

def _load_holdings_scan(scan_date):
    """Load all holdings tickers for the date from DB. Returns list of dicts."""
    conn = _get_trade_db()
    try:
        rows = conn.execute(
            "SELECT ticker, quantity, avg_cost FROM holdings_scan WHERE scan_date = ? ORDER BY id ASC",
            (scan_date,),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()

_cst_now = datetime.now(pytz.timezone("US/Central"))
_today_str = _cst_now.strftime("%Y-%m-%d")
_auto_scan_done_key = f"auto_scan_done_{_today_str}"

# Check if scan already done today (in DB)
_db_rank1 = _load_rank1_from_db(_today_str)
_scan_done_in_db = _scan_already_done_today(_today_str)
if _scan_done_in_db:
    st.session_state["auto_scan_rank1"] = _db_rank1
    st.session_state[_auto_scan_done_key] = True

# Seed base intraday tickers for today
_seed_intraday_tickers_for_date(_today_str)
# If Rank 1 tickers already in DB, also add them to intraday table
if _db_rank1:
    _add_scan_tickers_to_intraday(_today_str, _db_rank1)

if False and _cst_now.hour >= 8 and not st.session_state.get(_auto_scan_done_key, False):  # DISABLED
    _scan_tickers = [t.strip() for t in default_watchlist.split(",") if t.strip()]
    _rank1_tickers = []
    _scan_placeholder = st.empty()
    _scan_placeholder.info(f"🔬 8 AM Auto-Scan: scanning {len(_scan_tickers)} tickers for Rank 1 — Full Align...")
    for _st in _scan_tickers:
        try:
            _res = scan_single_stock(_st, api_key, api_secret, data_source)
            if _res and _res.get("mtf_rank") == 1 and _res.get("entry_status") == "ENTER":
                _rank1_tickers.append(_st)
        except Exception:
            pass
    st.session_state[_auto_scan_done_key] = True
    st.session_state["auto_scan_rank1"] = _rank1_tickers
    _save_rank1_to_db(_today_str, _rank1_tickers)
    _add_scan_tickers_to_intraday(_today_str, _rank1_tickers)
    _scan_placeholder.empty()
    if _rank1_tickers:
        st.toast(f"🎯 Auto-Scan found {len(_rank1_tickers)} Rank 1 tickers: {', '.join(_rank1_tickers)}", icon="🎯")
        print(f"🎯 Auto-Scan Rank 1 (saved to DB): {_rank1_tickers}")
    else:
        print("🔬 Auto-Scan: No Rank 1 tickers found today.")

# ── Telegram Watchlist Scheduler: fetch tickers at 7 PM CST daily ─────────
@st.fragment(run_every=timedelta(seconds=60))
def _telegram_watchlist_scheduler():
    """Polls every 60s; at 8 PM CST fetches tickers from the separate Telegram bot."""
    if st.session_state.get("_debug_fragments"):
        print(f"[FRAGMENT] _telegram_watchlist_scheduler (60s) fired at {datetime.now().strftime('%H:%M:%S')}")
    _now = get_cst_now()
    _today_key = date.today().isoformat()
    _done_key = f"_tg_wl_done_{_today_key}"

    # Only active after 8 PM and before midnight CST
    if _now.hour < 20:
        _tg_count = len(st.session_state.get("_tg_watchlist_tickers", []))
        if _tg_count:
            st.caption(f"📱 Telegram watchlist: {_tg_count} tickers loaded · Next refresh 8:00 PM CST")
        else:
            st.caption("📱 Telegram watchlist: refresh at 8:00 PM CST")
        return

    if st.session_state.get(_done_key):
        _tg_count = len(st.session_state.get("_tg_watchlist_tickers", []))
        st.caption(f"✅ Telegram watchlist refreshed: {_tg_count} tickers" if _tg_count else "✅ Telegram watchlist checked — no tickers sent today")
        return

    # 8 PM+ and not yet fetched → poll Telegram, persist, then load
    try:
        tg_poll_and_store()
        _tg_fresh = fetch_today_watchlist(force=False)
        st.session_state["_tg_watchlist_tickers"] = _tg_fresh
        st.session_state[_done_key] = True
        if _tg_fresh:
            print(f"📱 Telegram watchlist refreshed at 8 PM: {_tg_fresh}")
        st.rerun(scope="app")  # rerun to update default_watchlist
    except Exception as e:
        st.caption(f"⚠️ Telegram watchlist fetch error: {e}")

_telegram_watchlist_scheduler()

# ══════════════════════════════════════════════════════════════════════════════
# MAIN TABS — 8 Pages
# ══════════════════════════════════════════════════════════════════════════════
tab_fetch, tab_estimator, tab_sector, tab_plan, tab_scan_holdings, tab_trades, tab_holdings, tab_macro, tab_news, tab_paper, tab_growth, tab_tos, tab_bubble = st.tabs([
    "📅 Earnings Tab",
    "🔬 Stock Analysis",
    "🔥 Sector Scan",
    "🗓️ Intraday Planning",
    "📊 Scan Holdings",
    "📋 Trade Tracker",
    "💼 My Holdings",
    "🌍 Macro",
    "📰 News",
    "📄 Paper Trading",
    "🚀 Growth Scanner",
    "📡 TOS Scanner v2",
    "🫧 Bubble Detection",
])

# ╔══════════════════════════════════════════════════════════════════════════════╗
# ║ BACKGROUND MONITORING FRAGMENT (runs every 5 min, independent of active tab) ║
# ╚══════════════════════════════════════════════════════════════════════════════╝
# This fragment runs in the background regardless of which tab the user is viewing
# It monitors tracked trades, checks entry/exit conditions, and submits paper orders to Alpaca
@st.fragment(run_every=timedelta(minutes=5))
def _background_trade_monitor():
    """Background monitoring of tracked trades (independent of visible tab)."""
    _rp_mode = st.session_state.get("replay_mode", False)
    _tracked = st.session_state.get("tracking_trades", [])
    
    if not _tracked or (not _rp_mode and is_after_market_time(15, 0)):
        return
    
    if st.session_state.get("_debug_fragments"):
        print(f"[BACKGROUND MONITOR] Trade check fired at {datetime.now().strftime('%H:%M:%S')}")
    
    try:
        # Quick price fetch and order submission
        import yfinance as yf
        from alpaca_paper import PaperTrader
        import paper_config as _pc
        
        if not PAPER_TRADING_AVAILABLE:
            return
            
        _pt_engine = None
        if _rp_mode and _pc.AUTO_PAPER_IN_REPLAY:
            _pt_engine = PaperTrader(in_memory=True)
        elif not _rp_mode and _pc.SUBMIT_TO_ALPACA:
            _pt_engine = PaperTrader(in_memory=False)
        
        if not _pt_engine:
            return
        
        for tr in _tracked:
            try:
                tk = yf.Ticker(tr['ticker'])
                hist = tk.history(period="5d")
                if hist.empty:
                    continue
                
                live_price = float(hist["Close"].iloc[-1])
                entry = tr['entry']
                stop = tr['stop']
                t1 = tr['t1']
                t2 = tr['t2']
                direction = tr['direction']
                
                # Check entry conditions at/after 8:30 AM CST
                auto_track_enabled = not _rp_mode and is_after_market_time(8, 30)
                if _rp_mode:
                    #  In replay, allow based on replay time settings
                    auto_track_enabled = True
                
                if auto_track_enabled:
                    _elig, _reason = _pt_engine.check_entry_conditions({"ticker": tr['ticker']})
                    if _elig:
                        _open_res = _pt_engine.open_trade(
                            ticker=tr['ticker'],
                            direction=direction,
                            entry_price=live_price,
                            stop_price=float(tr.get('stop', entry)),
                            t1_price=float(tr.get('t1', entry)),
                            t2_price=float(tr.get('t2', entry)),
                            trade_date=str(date.today()),
                            scenario=tr.get('scenario', ''),
                            confidence=tr.get('confidence', ''),
                            submit_to_alpaca=False if _rp_mode else None,
                        )
                        if _open_res.get("status") == "opened":
                            print(f"✅ BACKGROUND: {tr['ticker']} paper trade opened @ ${live_price:.2f}")
            except Exception as e:
                print(f"⚠️ Background monitor error for {tr.get('ticker', '?')}: {e}")
    except Exception as _bg_outer_e:
        print(f"⚠️ Background monitor setup error: {_bg_outer_e}")
    finally:
        if _pt_engine:
            try:
                _pt_engine.close()
            except Exception:
                pass

_background_trade_monitor()

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
    # Show Telegram watchlist tickers as quick-pick + manual pull
    _tg_wl_fetch = st.session_state.get("_tg_watchlist_tickers", [])
    _tgf_col1, _tgf_col2 = st.columns([4, 1])
    with _tgf_col1:
        if _tg_wl_fetch:
            st.markdown(
                f'<div style="background:#1a1d2e;border-left:3px solid #4d9fff;padding:6px 12px;border-radius:4px;margin-bottom:8px;font-size:11px">'
                f'📱 <b style="color:#4d9fff">Telegram watchlist:</b> '
                f'<span style="color:#e8ecff">{", ".join(_tg_wl_fetch)}</span></div>',
                unsafe_allow_html=True,
            )
        else:
            st.caption("📱 No Telegram tickers loaded")
    with _tgf_col2:
        if st.button("📱 Pull Telegram", use_container_width=True, key="btn_tg_pull_fetch"):
            try:
                tg_poll_and_store()
                _fresh = fetch_today_watchlist(force=True)
                if _fresh:
                    st.session_state["_tg_watchlist_tickers"] = _fresh
                    st.success(f"Pulled {len(_fresh)} tickers: {', '.join(_fresh[:8])}")
                    st.rerun()
                else:
                    st.info("No tickers found in recent Telegram messages")
            except Exception as _tge:
                st.error(f"Telegram pull failed: {_tge}")
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
            "Dates", value=date.today().strftime("%Y-%m-%d"),
            placeholder="2024-10-29\n2024-07-23\n2024-04-23",
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

    # ── Inter-Quarter Swing Fib — As Of Date ─────────────────────────
    iq_fib_date = st.date_input(
        "📐 Inter-Quarter Swing Fib — As Of Date (prices as of this date, default today)",
        value=date.today(),
        key="iq_fib_date",
    )

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

    # Show Telegram watchlist indicator + manual pull button
    _tg_wl = st.session_state.get("_tg_watchlist_tickers", [])
    _tg_col1, _tg_col2 = st.columns([4, 1])
    with _tg_col1:
        if _tg_wl:
            st.markdown(
                f'<div style="background:#1a1d2e;border-left:3px solid #4d9fff;padding:6px 12px;border-radius:4px;margin-bottom:8px;font-size:11px">'
                f'📱 <b style="color:#4d9fff">{len(_tg_wl)} Telegram tickers</b> '
                f'<span style="color:#6b7099">prepended to watchlist: </span>'
                f'<span style="color:#e8ecff">{", ".join(_tg_wl[:10])}{"..." if len(_tg_wl) > 10 else ""}</span></div>',
                unsafe_allow_html=True,
            )
        else:
            st.caption("📱 No Telegram tickers loaded")
    with _tg_col2:
        if st.button("📱 Pull Telegram", use_container_width=True, key="btn_tg_pull_est"):
            try:
                tg_poll_and_store()
                _fresh = fetch_today_watchlist(force=True)
                if _fresh:
                    st.session_state["_tg_watchlist_tickers"] = _fresh
                    st.success(f"Pulled {len(_fresh)} tickers: {', '.join(_fresh[:8])}")
                    st.rerun()
                else:
                    st.info("No tickers found in recent Telegram messages")
            except Exception as _tge:
                st.error(f"Telegram pull failed: {_tge}")

    estimator_watchlist = [t.strip().upper() for t in estimator_watchlist_raw.strip().split(",") if t.strip()] if estimator_watchlist_raw.strip() else SCAN_WATCHLIST
    est_fib_backdate = st.date_input(
        "As Of Date — backdate Fib Zones + Stock Analysis",
        value=date.today(),
        key="est_fib_backdate",
    )
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
    else:
        # --- VIX Fib Scenario (standalone reference table) ---
        from math import isnan as _isnan
        _fib_col_order = [
            "E 261.8%", "E 200.0%", "E 161.8%", "E 141.4%", "E 127.2%",
            "R 0.0%", "R 23.6%", "R 38.2%", "R 50.0%", "R 61.8%", "R 78.6%", "R 100.0%",
            "N -23.6%", "N -38.2%", "N -50.0%", "N -61.8%", "N -100.0%",
        ]
        try:
            _vix_df_wk = yf.download("^VIX", period="2y", interval="1wk", progress=False)
            if _vix_df_wk is not None and not _vix_df_wk.empty:
                if isinstance(_vix_df_wk.columns, pd.MultiIndex):
                    _vix_df_wk.columns = _vix_df_wk.columns.get_level_values(0)
                _vix_bd = pd.Timestamp(est_fib_backdate)
                _vix_df_wk = _vix_df_wk[_vix_df_wk.index <= _vix_bd]
                if not _vix_df_wk.empty:
                    _vix_c_s = _vix_df_wk["Close"].squeeze() if hasattr(_vix_df_wk["Close"], 'squeeze') else _vix_df_wk["Close"]
                    _vix_h_s = _vix_df_wk["High"].squeeze() if hasattr(_vix_df_wk["High"], 'squeeze') else _vix_df_wk["High"]
                    _vix_l_s = _vix_df_wk["Low"].squeeze() if hasattr(_vix_df_wk["Low"], 'squeeze') else _vix_df_wk["Low"]
                    _vix_close = _safe_float(_vix_c_s.iloc[-1])
                    _vix_hi10 = _safe_float(_vix_h_s.iloc[-10:].max())
                    _vix_lo10 = _safe_float(_vix_l_s.iloc[-10:].min())
                    _vix_rng = _vix_hi10 - _vix_lo10
                    _vix_pos = (_vix_close - _vix_lo10) / _vix_rng * 100 if _vix_rng > 0 else 50.0
                    _vix_zone = "HIGH" if _vix_pos >= 70 else ("LOW" if _vix_pos <= 30 else "MID")
                    _vix_fib = calc_fib_levels(_vix_lo10, _vix_hi10)
                    _vix_row = {"Ticker": "^VIX", "As Of": str(est_fib_backdate),
                                "Close": f"${_vix_close:.2f}", "Weekly Zone": _vix_zone}
                    for _fn in _fib_col_order:
                        _fv = _vix_fib.get(_fn, float("nan"))
                        _vix_row[_fn] = f"${_fv:.2f}" if not _isnan(_fv) else ""
                    _vix_row["Conclusion"] = (
                        f"VIX {'elevated — risk-off' if _vix_zone == 'HIGH' else 'low — risk-on' if _vix_zone == 'LOW' else 'neutral'}"
                        f" | Hi: ${_vix_hi10:.2f} Lo: ${_vix_lo10:.2f} | Pos: {_vix_pos:.0f}%"
                    )
                    _vix_tbl = pd.DataFrame([_vix_row])
                    # VIX zone coloring
                    _vix_zone_color = "#ff4d6a" if _vix_zone == "HIGH" else ("#00e5a0" if _vix_zone == "LOW" else "#f5c842")
                    _vix_zone_icon = "🔴" if _vix_zone == "HIGH" else ("🟢" if _vix_zone == "LOW" else "🟡")
                    st.markdown(
                        f'<div style="background:linear-gradient(135deg,#0d0f17,#131625);border:1px solid #1a1d2e;'
                        f'border-radius:8px;padding:12px 18px;margin-bottom:12px">'
                        f'<span style="font-size:20px">📉</span> '
                        f'<b style="color:#e8ecff;font-size:15px">VIX Fib Scenario</b>  '
                        f'{_vix_zone_icon} <span style="color:{_vix_zone_color};font-weight:700">'
                        f'{_vix_zone} Zone</span> '
                        f'<span style="color:#6b7099;font-size:11px">— Close ${_vix_close:.2f} '
                        f'| 10W Range ${_vix_lo10:.2f}–${_vix_hi10:.2f}</span>'
                        f'</div>',
                        unsafe_allow_html=True,
                    )
                    _vix_golden_set = {"R 38.2%", "R 50.0%", "R 61.8%"}
                    def _vix_highlight(row):
                        out = []
                        for col in row.index:
                            if col in _vix_golden_set:
                                out.append("background-color:#1a1500;color:#d4a017;font-weight:600;border-bottom:2px solid #d4a01780")
                            else:
                                out.append("")
                        return out
                    _vix_styled = _vix_tbl.style.apply(_vix_highlight, axis=1)
                    if "Weekly Zone" in _vix_tbl.columns:
                        _vix_styled = _vix_styled.applymap(
                            lambda v: ("background-color:#3d0a1a;color:#ff4d6a;font-weight:700" if str(v) == "HIGH"
                                       else "background-color:#0a3d1f;color:#00e5a0;font-weight:700" if str(v) == "LOW"
                                       else "background-color:#3d3a0a;color:#f5c842;font-weight:700"),
                            subset=["Weekly Zone"])
                    st.dataframe(_vix_styled, use_container_width=True, hide_index=True, height=70)
        except Exception as _vix_e:
            st.caption(f"⚠️ VIX data unavailable: {_vix_e}")

        st.markdown("---")

        # --- Fib Scenario Report for tickers in scan (Weekly Zone + extras) ---
        st.markdown("<h4>📊 Fib Scenario Report (Weekly Zone)</h4>", unsafe_allow_html=True)
        fib_rows = []
        _fib_progress = st.progress(0)
        _fib_status = st.empty()
        _fib_total = len(estimator_watchlist)
        def _calc_fib_single(symbol):
            try:
                if not YFINANCE_AVAILABLE:
                    return None
                df_daily = yf.download(symbol, period="2y", progress=False)
                if df_daily is None or df_daily.empty:
                    return None
                if isinstance(df_daily.columns, pd.MultiIndex):
                    df_daily.columns = df_daily.columns.get_level_values(0)
                
                # Apply backdate filter
                _est_bd = pd.Timestamp(est_fib_backdate)
                df_daily = df_daily[df_daily.index <= _est_bd]
                if df_daily.empty:
                    return None
                
                # ── Get last earnings date and calculate earnings week ──
                try:
                    _est_earn_evts = get_earnings_dates_yfinance(symbol)
                    if not _est_earn_evts:
                        # Fallback to price/volume gap detection if yfinance crumb 401 or empty
                        _est_earn_evts = detect_earnings_from_prices(df_daily)
                    if _est_earn_evts and len(_est_earn_evts) >= 2:
                        _est_last_earn_date = pd.Timestamp(_est_earn_evts[-1][0])
                        _est_prev_earn_date = pd.Timestamp(_est_earn_evts[-2][0])
                    elif _est_earn_evts:
                        _est_last_earn_date = pd.Timestamp(_est_earn_evts[-1][0])
                        _est_prev_earn_date = _est_last_earn_date - pd.Timedelta(days=90)
                    else:
                        _est_last_earn_date = pd.Timestamp(est_fib_backdate)
                        _est_prev_earn_date = _est_last_earn_date - pd.Timedelta(days=90)
                except Exception:
                    _est_last_earn_date = pd.Timestamp(est_fib_backdate)
                    _est_prev_earn_date = _est_last_earn_date - pd.Timedelta(days=90)
                
                _wk_close = _safe_float(df_daily["Close"].iloc[-1])
                
                # Calculate week containing earnings date (Monday-Friday)
                _wk_start_d = _est_last_earn_date - pd.Timedelta(days=_est_last_earn_date.weekday())
                _wk_end_d = _wk_start_d + pd.Timedelta(days=4)
                
                # Get daily data for that week
                _di_dates = np.array([d.date() if hasattr(d, "date") else d for d in df_daily.index])
                _wk_slice = df_daily[(_di_dates >= _wk_start_d.date()) & (_di_dates <= _wk_end_d.date())]
                if _wk_slice.empty:
                    _wk_slice = df_daily.tail(5)
                
                _wk_hi10 = _safe_float(_wk_slice["High"].max())
                _wk_lo10 = _safe_float(_wk_slice["Low"].min())
                _wk_rng = _wk_hi10 - _wk_lo10
                _wk_pos = (_wk_close - _wk_lo10) / _wk_rng * 100 if _wk_rng > 0 else 50.0
                if _wk_pos >= 70:
                    _wk_zone = "HIGH"
                    _wk_conclusion_base = "Near Weekly Hi — Extension territory"
                elif _wk_pos <= 30:
                    _wk_zone = "LOW"
                    _wk_conclusion_base = "Near Weekly Lo — Retrace territory"
                else:
                    _wk_zone = "MID"
                    _wk_conclusion_base = "Mid Weekly Range — Balanced"
                _fib_lvls = calc_fib_levels(_wk_lo10, _wk_hi10)

                # ── News Sentiment ─────────────────────────────────────
                try:
                    _est_news = get_news_details(symbol)
                    _est_news_lbl = _est_news.get("label", "No")
                    _g = _est_news.get("good_score", 0)
                    _b = _est_news.get("bad_score", 0)
                    _est_news_txt = (
                        f"📰 POSITIVE (+{_g}/-{_b})" if _est_news_lbl == "Good"
                        else f"📰 NEGATIVE (+{_g}/-{_b})" if _est_news_lbl == "Bad"
                        else f"📰 NEUTRAL (+{_g}/-{_b})"
                    )
                except Exception:
                    _est_news_txt = "N/A"

                # ── Earnings Bias (Fundamentals) ───────────────────────
                try:
                    _est_fund = get_fundamentals(symbol)
                    _est_fb = 0
                    if _est_fund:
                        _eg = _est_fund.get("earnings_growth")
                        _rg = _est_fund.get("revenue_growth")
                        _tu = _est_fund.get("target_upside")
                        _pm = _est_fund.get("profit_margin")
                        _est_fb += (2 if _eg and _eg > 0.10 else 1 if _eg and _eg > 0 else -1 if _eg and _eg > -0.10 else -2 if _eg else 0)
                        _est_fb += (1 if _rg and _rg > 0.05 else -1 if _rg and _rg < 0 else 0)
                        _est_fb += (1 if _tu and _tu > 0.10 else -1 if _tu and _tu < 0 else 0)
                        _est_fb += (1 if _pm and _pm > 0.10 else -1 if _pm and _pm < 0 else 0)
                    if _est_fb >= 3:
                        _est_fund_txt = "🐂 BULLISH"
                    elif _est_fb >= 1:
                        _est_fund_txt = "🐂 LEAN BULLISH"
                    elif _est_fb > -1:
                        _est_fund_txt = "⚖️ NEUTRAL"
                    elif _est_fb > -3:
                        _est_fund_txt = "🐻 LEAN BEARISH"
                    else:
                        _est_fund_txt = "🐻 BEARISH"
                except Exception:
                    _est_fund_txt = "N/A"

                # ── Next Earnings ──────────────────────────────────────
                try:
                    _est_next_earn = estimate_next_earnings(_est_earn_evts) if _est_earn_evts else "N/A"
                except Exception:
                    _est_next_earn = "N/A"

                # ── Earn Zone: use close on last earnings date vs earnings swing ──
                try:
                    _earn_dates = np.array([d.date() if hasattr(d, "date") else d for d in df_daily.index])
                    _earn_date_mask = ((_earn_dates >= _est_prev_earn_date.date()) & (_earn_dates <= _est_last_earn_date.date()))
                    _earn_slice = df_daily[_earn_date_mask]
                    
                    if _earn_slice.empty:
                        _earn_slice = df_daily.tail(60)
                    
                    _earn_hi = _safe_float(_earn_slice["High"].max())
                    _earn_lo = _safe_float(_earn_slice["Low"].min())
                    _earn_rng = _earn_hi - _earn_lo
                    
                    _earn_close_slice = df_daily[_earn_dates == _est_last_earn_date.date()]
                    if not _earn_close_slice.empty:
                        _est_earn_close = _safe_float(_earn_close_slice["Close"].iloc[-1])
                    else:
                        _est_earn_close = _wk_close
                    
                    _est_earn_pos = (_est_earn_close - _earn_lo) / _earn_rng * 100 if _earn_rng > 0 else 50.0
                    _est_earn_zone = "HIGH" if _est_earn_pos >= 70 else ("LOW" if _est_earn_pos <= 30 else "MID")
                except Exception:
                    _est_earn_close = _wk_close
                    _est_earn_zone = _wk_zone

                # ── Institutional vs Retail Control (resampled weekly: 0 extra network calls) ──
                try:
                    df_wk = df_daily.resample('W-FRI').agg({
                        'Open': 'first', 'High': 'max', 'Low': 'min', 'Close': 'last', 'Volume': 'sum'
                    }).dropna()
                    if not df_wk.empty:
                        _inst_control, _inst_phase, _inst_emoji, _inst_days = analyze_institutional_control(df_wk)
                        _inst_str = f"{_inst_emoji} {_inst_control} | {_inst_phase} ({_inst_days}d)" if _inst_control != "N/A" else "N/A"
                    else:
                        _inst_str = "N/A"
                except Exception:
                    _inst_str = "N/A"

                _est_fib_row = {
                    "Ticker": symbol,
                    "As Of": str(est_fib_backdate),
                    "Close on Earn Date": f"${_est_earn_close:.2f}",
                    "Earn Zone": _est_earn_zone,
                    "Weekly Zone": _wk_zone,
                    "Inst/Retail Control": _inst_str,
                }
                for _fn in _fib_col_order:
                    _fv = _fib_lvls.get(_fn, float("nan"))
                    _est_fib_row[_fn] = f"${_fv:.2f}" if not _isnan(_fv) else ""
                _est_fib_row["Earnings Bias"] = _est_fund_txt
                _est_fib_row["News Sentiment"] = _est_news_txt
                _est_fib_row["Next Earnings"] = _est_next_earn
                _est_fib_row["Conclusion"] = (
                    f"{_wk_conclusion_base} | Hi: ${_wk_hi10:.2f} Lo: ${_wk_lo10:.2f} | "
                    f"{_est_fund_txt} | {_est_news_txt}"
                )
                return _est_fib_row
            except Exception:
                return None

        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as _fib_pool:
            _fut_map = {_fib_pool.submit(_calc_fib_single, sym): sym for sym in estimator_watchlist}
            _completed = 0
            for _fut in concurrent.futures.as_completed(_fut_map):
                _completed += 1
                _sym = _fut_map[_fut]
                _fib_status.text(f"Scanning {_sym}... ({_completed}/{_fib_total})")
                _fib_progress.progress(_completed / _fib_total)
                try:
                    res = _fut.result()
                    if res:
                        fib_rows.append(res)
                except Exception:
                    pass

        _order_map = {s: i for i, s in enumerate(estimator_watchlist)}
        fib_rows.sort(key=lambda r: _order_map.get(r["Ticker"], 999))

        _fib_progress.empty()
        _fib_status.empty()

        if fib_rows:
            _est_fib_df = pd.DataFrame(fib_rows)

            # ── Fetch live prices for current-price highlight ──────────
            try:
                _est_live_tickers = list(_est_fib_df["Ticker"].unique())
                _est_lp_raw = yf.download(_est_live_tickers, period="1d", progress=False, auto_adjust=True)
                if hasattr(_est_lp_raw, "columns") and "Close" in _est_lp_raw.columns:
                    _est_lp_series = _est_lp_raw["Close"].iloc[-1] if len(_est_lp_raw) > 0 else pd.Series(dtype=float)
                else:
                    _est_lp_series = pd.Series(dtype=float)
                _est_live_prices = {}
                for _ltk in _est_live_tickers:
                    try:
                        _est_live_prices[_ltk] = _safe_float(_est_lp_series[_ltk]) if _ltk in _est_lp_series.index else _safe_float(getattr(yf.Ticker(_ltk).fast_info, 'last_price', None))
                    except Exception:
                        _est_live_prices[_ltk] = None
            except Exception:
                _est_live_prices = {}

            def _est_color_earn_zone(v):
                v = str(v)
                if v == "HIGH": return "background-color:#3d0a1a;color:#ff4d6a;font-weight:700"
                if v == "LOW":  return "background-color:#0a3d1f;color:#00e5a0;font-weight:700"
                return "background-color:#3d3a0a;color:#f5c842;font-weight:700"
            def _est_color_wk_zone(v):
                v = str(v)
                if v == "HIGH": return "background-color:#3d0a1a;color:#ff4d6a;font-weight:700"
                if v == "LOW":  return "background-color:#0a3d1f;color:#00e5a0;font-weight:700"
                return "background-color:#3d3a0a;color:#f5c842;font-weight:700"
            def _est_color_fund(v):
                v = str(v)
                if "BULLISH" in v and "LEAN" not in v: return "color:#00e5a0;font-weight:700"
                if "LEAN BULLISH" in v: return "color:#7ccfb0;font-weight:700"
                if "BEARISH" in v and "LEAN" not in v: return "color:#ff4d6a;font-weight:700"
                if "LEAN BEARISH" in v: return "color:#ff8c8c;font-weight:700"
                return "color:#f5c842"
            def _est_color_news(v):
                v = str(v)
                if "POSITIVE" in v: return "color:#00e5a0;font-weight:700"
                if "NEGATIVE" in v: return "color:#ff4d6a;font-weight:700"
                return "color:#f5c842"
            def _est_color_inst_control(v):
                v = str(v)
                if "🏛️" in v: return "background-color:#0a2d1f;color:#00e5a0;font-weight:700"  # Institutional bullish
                if "👥" in v: return "background-color:#2d0a0a;color:#ff4d6a;font-weight:700"  # Retail bearish
                return "background-color:#0a0f1a;color:#f5c842;font-weight:700"  # Mixed

            _est_e_cols = [c for c in _fib_col_order if c.startswith("E ")]
            _est_r_cols = [c for c in _fib_col_order if c.startswith("R ")]
            _est_e_set = set(_est_e_cols)
            _est_r_set = set(_est_r_cols)
            _est_golden_set = {"R 38.2%", "R 50.0%", "R 61.8%"}

            def _est_highlight_fib(row):
                zone = str(row.get("Weekly Zone", ""))
                out = []
                for col in row.index:
                    if zone == "HIGH" and col in _est_r_set:
                        out.append("background-color:#1f1400;color:#f5c842;font-weight:700")
                    elif zone == "LOW" and col in _est_e_set:
                        out.append("background-color:#001a0a;color:#00e5a0;font-weight:700")
                    elif col in _est_golden_set:
                        out.append("background-color:#1a1500;color:#d4a017;font-weight:600;border-bottom:2px solid #d4a01780")
                    else:
                        out.append("")
                return out

            def _est_highlight_live(row):
                ticker   = str(row.get("Ticker", ""))
                live_px  = _est_live_prices.get(ticker)
                styles   = [""] * len(row)
                if live_px is None:
                    return styles
                best_col  = None
                best_diff = float("inf")
                for col in _fib_col_order:
                    if col not in row.index:
                        continue
                    try:
                        val = float(str(row[col]).replace("$", "").strip())
                        diff = abs(val - live_px)
                        if diff < best_diff:
                            best_diff = diff
                            best_col  = col
                    except Exception:
                        pass
                if best_col is not None:
                    col_idx = list(row.index).index(best_col)
                    styles[col_idx] = "background-color:#0a1f3a;color:#ffffff;font-weight:900;border:2px solid #4d9fff"
                return styles

            _est_earn_zone_config = [
                ("HIGH", "#ff4d6a", "🔴"),
                ("MID",  "#f5c842", "🟡"),
                ("LOW",  "#00e5a0", "🟢"),
            ]
            for _ez, _ez_color, _ez_icon in _est_earn_zone_config:
                _ez_mask = _est_fib_df["Earn Zone"] == _ez
                _ez_group = _est_fib_df[_ez_mask].copy()
                if _ez_group.empty:
                    continue
                st.markdown(
                    f'<div style="background:rgba(0,0,0,0.3);border-left:3px solid {_ez_color}40;'
                    f'padding:6px 12px;margin:8px 0 4px;border-radius:0 4px 4px 0">'
                    f'<span style="font-size:14px">{_ez_icon}</span> '
                    f'<b style="color:{_ez_color};font-size:13px">{_ez} Earn Zone</b> '
                    f'<span style="color:#6b7099;font-size:11px">({len(_ez_group)} ticker{"s" if len(_ez_group)!=1 else ""})</span>'
                    f'</div>',
                    unsafe_allow_html=True,
                )
                _ez_styled = _ez_group.style
                if "Earn Zone"            in _ez_group.columns: _ez_styled = _ez_styled.applymap(_est_color_earn_zone,     subset=["Earn Zone"])
                if "Weekly Zone"          in _ez_group.columns: _ez_styled = _ez_styled.applymap(_est_color_wk_zone,       subset=["Weekly Zone"])
                if "Inst/Retail Control"  in _ez_group.columns: _ez_styled = _ez_styled.applymap(_est_color_inst_control,  subset=["Inst/Retail Control"])
                if "Earnings Bias"        in _ez_group.columns: _ez_styled = _ez_styled.applymap(_est_color_fund,          subset=["Earnings Bias"])
                if "News Sentiment"       in _ez_group.columns: _ez_styled = _ez_styled.applymap(_est_color_news,          subset=["News Sentiment"])
                _ez_styled = _ez_styled.apply(_est_highlight_fib, axis=1)
                if _est_live_prices:
                    _ez_styled = _ez_styled.apply(_est_highlight_live, axis=1)
                st.dataframe(_ez_styled, use_container_width=True, hide_index=True,
                             height=min(len(_ez_group) * 35 + 48, 400))

            # ── 🥇 Golden Zone: tickers with price between R 38.2% and R 61.8% ──
            _gz1_rows = []
            for _, _gz1_r in _est_fib_df.iterrows():
                try:
                    _gz1_tk = _gz1_r["Ticker"]
                    _gz1_38 = float(str(_gz1_r.get("R 38.2%", "")).replace("$", ""))
                    _gz1_61 = float(str(_gz1_r.get("R 61.8%", "")).replace("$", ""))
                    _gz1_px = _est_live_prices.get(_gz1_tk) if _est_live_prices else None
                    if _gz1_px is None:
                        _gz1_px = float(str(_gz1_r.get("Close on Earn Date", "")).replace("$", ""))
                    _gz1_lo, _gz1_hi = min(_gz1_38, _gz1_61), max(_gz1_38, _gz1_61)
                    if _gz1_lo <= _gz1_px <= _gz1_hi:
                        _gz1_rows.append({"Ticker": _gz1_tk, "Price": f"${_gz1_px:.2f}",
                                          "R 38.2%": _gz1_r.get("R 38.2%", ""),
                                          "R 50.0%": _gz1_r.get("R 50.0%", ""),
                                          "R 61.8%": _gz1_r.get("R 61.8%", ""),
                                          "Earn Zone": _gz1_r.get("Earn Zone", ""),
                                          "Weekly Zone": _gz1_r.get("Weekly Zone", "")})
                except Exception:
                    pass
            if _gz1_rows:
                st.markdown(
                    '<div style="background:linear-gradient(135deg,#1a1500,#1f1800);border:1px solid #d4a01740;'
                    'border-radius:8px;padding:12px 18px;margin:16px 0 8px">'
                    '<span style="font-size:18px">🥇</span> '
                    '<b style="color:#d4a017;font-size:14px">Golden Zone</b> '
                    '<span style="color:#8b7d3c;font-size:11px">'
                    f'({len(_gz1_rows)} ticker{"s" if len(_gz1_rows)!=1 else ""}) — '
                    f'Price within R 38.2% – R 61.8% retracement</span>'
                    '</div>',
                    unsafe_allow_html=True,
                )
                _gz1_df = pd.DataFrame(_gz1_rows)
                _gz1_styled = _gz1_df.style.applymap(
                    lambda v: "background-color:#1a1500;color:#d4a017;font-weight:700"
                    if str(v).startswith("$") else "",
                    subset=["R 38.2%", "R 50.0%", "R 61.8%"]
                )
                st.dataframe(_gz1_styled, use_container_width=True, hide_index=True,
                             height=min(len(_gz1_rows) * 35 + 48, 300))
        else:
            st.info("No data could be retrieved for the given tickers.")
        trim_memory()

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


    sector_fib_backdate = st.date_input(
        "Fib Zones — As Of Date (backdating supported)",
        value=date.today(),
        key="sector_fib_backdate",
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
    else:
        # --- Fib Scenario Report for Sectors (Weekly only, full columns/colors) ---
        st.markdown("<h4>Fib Scenario Report (Weekly Only)</h4>", unsafe_allow_html=True)
        fib_rows = []
        for etf, info in SECTOR_ETFS.items():
            try:
                if YFINANCE_AVAILABLE:
                    df = yf.download(etf, period="2y", interval="1wk", progress=False)
                else:
                    continue
                if df is None or df.empty:
                    continue
                if isinstance(df.columns, pd.MultiIndex):
                    df.columns = df.columns.get_level_values(0)
                # Apply backdate filter — use data up to (and including) sector_fib_backdate
                _bd = pd.Timestamp(sector_fib_backdate)
                df = df[df.index <= _bd]
                if df.empty:
                    continue
                _sc = df["Close"].squeeze() if hasattr(df["Close"], 'squeeze') else df["Close"]
                _sh = df["High"].squeeze() if hasattr(df["High"], 'squeeze') else df["High"]
                _sl = df["Low"].squeeze() if hasattr(df["Low"], 'squeeze') else df["Low"]
                close = _safe_float(_sc.iloc[-1])
                wk_hi = _safe_float(_sh.iloc[-10:].max())
                wk_lo = _safe_float(_sl.iloc[-10:].min())
                wk_range = wk_hi - wk_lo
                wk_pos_pct = (close - wk_lo) / wk_range * 100 if wk_range > 0 else 50.0
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
                # Fund Bias and News Sentiment are not sector-specific, so use N/A or neutral
                fund_bias = "⚖️ NEUTRAL"
                news_sentiment = "📰 NEUTRAL"
                conclusion = wk_zone_desc
                # Calculate all Fib levels for this swing
                from math import isnan
                fib_levels = calc_fib_levels(wk_lo, wk_hi)
                fib_row = {
                    "Sector": info["name"],
                    "ETF": etf,
                    "As Of": str(sector_fib_backdate),
                    "Close": f"${close:.2f}",
                    "Weekly Hi": f"${wk_hi:.2f}",
                    "Weekly Lo": f"${wk_lo:.2f}",
                    "Weekly Range": f"${wk_range:.2f}",
                    "Weekly Pos %": round(wk_pos_pct, 1),
                    "Weekly Zone": wk_zone,
                    "Fund Bias": fund_bias,
                    "News Sentiment": news_sentiment,
                    "Conclusion": conclusion,
                }
                # Add all E, R, N columns (sorted as in main report)
                fib_names = [
                    "E 261.8%", "E 200.0%", "E 161.8%", "E 141.4%", "E 127.2%",
                    "R 0.0%", "R 23.6%", "R 38.2%", "R 50.0%", "R 61.8%", "R 78.6%", "R 100.0%",
                    "N -23.6%", "N -38.2%", "N -50.0%", "N -61.8%", "N -100.0%"
                ]
                for name in fib_names:
                    val = fib_levels.get(name, float('nan'))
                    fib_row[name] = f"${val:.2f}" if not isnan(val) else ""
                fib_rows.append(fib_row)
            except Exception as e:
                st.warning(f"{etf}: {e}")
        if fib_rows:
            fib_df = pd.DataFrame(fib_rows)
            # --- Coloring functions (reuse from main report) ---
            def _color_wk_zone(v):
                v = str(v)
                if v == "HIGH": return "background-color:#3d0a1a;color:#ff4d6a;font-weight:700"
                if v == "LOW":  return "background-color:#0a3d1f;color:#00e5a0;font-weight:700"
                return "background-color:#3d3a0a;color:#f5c842;font-weight:700"
            def _color_fund(v):
                v = str(v)
                if "BULLISH" in v and "LEAN" not in v: return "color:#00e5a0;font-weight:700"
                if "LEAN BULLISH" in v:                return "color:#7ccfb0;font-weight:700"
                if "BEARISH" in v and "LEAN" not in v: return "color:#ff4d6a;font-weight:700"
                if "LEAN BEARISH" in v:                return "color:#ff8c8c;font-weight:700"
                return "color:#f5c842"
            def _color_news(v):
                v = str(v)
                if "POSITIVE" in v: return "color:#00e5a0;font-weight:700"
                if "NEGATIVE" in v: return "color:#ff4d6a;font-weight:700"
                return "color:#f5c842"
            _sec_e_set = set(c for c in fib_names if c.startswith("E "))
            _sec_r_set = set(c for c in fib_names if c.startswith("R "))
            _sec_golden_set = {"R 38.2%", "R 50.0%", "R 61.8%"}
            def _sec_highlight_fib(row):
                zone = str(row.get("Weekly Zone", ""))
                out = []
                for col in row.index:
                    if zone == "HIGH" and col in _sec_r_set:
                        out.append("background-color:#1f1400;color:#f5c842;font-weight:700")
                    elif zone == "LOW" and col in _sec_e_set:
                        out.append("background-color:#001a0a;color:#00e5a0;font-weight:700")
                    elif col in _sec_golden_set:
                        out.append("background-color:#1a1500;color:#d4a017;font-weight:600;border-bottom:2px solid #d4a01780")
                    else:
                        out.append("")
                return out
            for zone in ["LOW", "MID", "HIGH"]:
                group = fib_df[fib_df["Weekly Zone"] == zone]
                if not group.empty:
                    st.markdown(f"<h5>{zone} Weekly Zone</h5>", unsafe_allow_html=True)
                    styled = group.style
                    if "Weekly Zone" in group.columns:
                        styled = styled.applymap(_color_wk_zone, subset=["Weekly Zone"])
                    if "Fund Bias" in group.columns:
                        styled = styled.applymap(_color_fund, subset=["Fund Bias"])
                    if "News Sentiment" in group.columns:
                        styled = styled.applymap(_color_news, subset=["News Sentiment"])
                    styled = styled.apply(_sec_highlight_fib, axis=1)
                    st.dataframe(styled, use_container_width=True, hide_index=True)

            # ── 🥇 Golden Zone ──
            _gz2_rows = []
            for _, _gz2_r in fib_df.iterrows():
                try:
                    _gz2_tk = _gz2_r["Ticker"]
                    _gz2_38 = float(str(_gz2_r.get("R 38.2%", "")).replace("$", ""))
                    _gz2_61 = float(str(_gz2_r.get("R 61.8%", "")).replace("$", ""))
                    _gz2_px = float(str(_gz2_r.get("Close", "")).replace("$", ""))
                    _gz2_lo, _gz2_hi = min(_gz2_38, _gz2_61), max(_gz2_38, _gz2_61)
                    if _gz2_lo <= _gz2_px <= _gz2_hi:
                        _gz2_rows.append({"Ticker": _gz2_tk, "Price": f"${_gz2_px:.2f}",
                                          "R 38.2%": _gz2_r.get("R 38.2%", ""),
                                          "R 50.0%": _gz2_r.get("R 50.0%", ""),
                                          "R 61.8%": _gz2_r.get("R 61.8%", ""),
                                          "Weekly Zone": _gz2_r.get("Weekly Zone", "")})
                except Exception:
                    pass
            if _gz2_rows:
                st.markdown(
                    '<div style="background:linear-gradient(135deg,#1a1500,#1f1800);border:1px solid #d4a01740;'
                    'border-radius:8px;padding:12px 18px;margin:16px 0 8px">'
                    '<span style="font-size:18px">🥇</span> '
                    '<b style="color:#d4a017;font-size:14px">Golden Zone</b> '
                    '<span style="color:#8b7d3c;font-size:11px">'
                    f'({len(_gz2_rows)} ticker{"s" if len(_gz2_rows)!=1 else ""}) — '
                    f'Price within R 38.2% – R 61.8% retracement</span>'
                    '</div>',
                    unsafe_allow_html=True,
                )
                _gz2_df = pd.DataFrame(_gz2_rows)
                _gz2_styled = _gz2_df.style.applymap(
                    lambda v: "background-color:#1a1500;color:#d4a017;font-weight:700"
                    if str(v).startswith("$") else "",
                    subset=["R 38.2%", "R 50.0%", "R 61.8%"]
                )
                st.dataframe(_gz2_styled, use_container_width=True, hide_index=True,
                             height=min(len(_gz2_rows) * 35 + 48, 300))

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
    # Build intraday ticker list from database (base + auto-scanned Rank 1)
    _intraday_from_db = _load_intraday_tickers(_today_str)
    _intraday_default = ", ".join(_intraday_from_db) if _intraday_from_db else "SPY, QQQ, AAPL, MSFT, NVDA, TSLA, AMZN, META, GOOG"
    plan_tickers_raw = st.text_area(
        "Tickers (comma-separated)", value=_intraday_default,
        height=60, key="plan_tickers", label_visibility="collapsed",
    )
    plan_date = st.date_input("Plan date (close of this day)", value=date.today(), key="plan_date")
    _today_key = date.today().isoformat()
    if st.session_state.get("_open_check_date") != _today_key:
        st.session_state.pop("open_check_table_rows", None)
        st.session_state.pop("_open_check_date", None)
    plan_run = st.button("🗓️ GENERATE PLAN", use_container_width=True, type="primary", key="btn_plan")
    _btn_cols = st.columns(2)
    with _btn_cols[0]:
        check_open_run = st.button("☀️ CHECK OPEN PRICES", use_container_width=True, key="btn_check_open")
    with _btn_cols[1]:
        replay_run = st.button("🔁 REPLAY SESSION", use_container_width=True, key="btn_replay")

    # ── Scheduler: auto-trigger Generate Plan (8:15 AM) & Check Open (8:00 AM) CST ──
    if plan_date == date.today():
        @st.fragment(run_every=timedelta(seconds=30))
        def _intraday_scheduler():
            """Polls every 30s; fires Generate Plan at 8:15 and Check Open at 8:00 without manual refresh."""
            _now = get_cst_now()
            # Only active between 7 AM and 3 PM CST
            if _now.hour < 7 or _now.hour >= 15:
                return
            if st.session_state.get("_debug_fragments"):
                print(f"[FRAGMENT] _intraday_scheduler (30s) fired at {datetime.now().strftime('%H:%M:%S')}")
            _today_key = date.today().isoformat()
            _plan_done = bool(st.session_state.get("plan_data"))
            _open_done = (
                bool(st.session_state.get("open_check_table_rows"))
                and st.session_state.get("_open_check_date") == _today_key
            )
            _now = get_cst_now()
            # Status indicator
            _pending = []
            if not _plan_done and not st.session_state.get(f"_sched_plan_done_{_today_key}"):
                _pending.append("🗓️ Plan @ 8:15 AM")
            if not _open_done and not st.session_state.get(f"_sched_open_done_{_today_key}"):
                _pending.append("☀️ Open Check @ 8:00 AM")
            if _pending:
                st.caption(f"⏰ Scheduled: {' · '.join(_pending)} · {_now.strftime('%I:%M:%S %p CST')}")
            elif _plan_done and _open_done:
                st.caption("✅ All scheduled tasks completed")
            # 8:15 AM — auto-generate plan (one attempt per day)
            if (is_after_market_time(8, 15)
                    and not _plan_done
                    and not st.session_state.get(f"_sched_plan_done_{_today_key}")):
                st.session_state[f"_sched_plan_done_{_today_key}"] = True
                st.session_state["_sched_plan_gen"] = True
                st.rerun(scope="app")
            # 8:00 AM — auto-check open prices (retry until plan exists)
            if (is_after_market_time(8, 0)
                    and not _open_done
                    and not st.session_state.get(f"_sched_open_done_{_today_key}")):
                # Only fire if a plan exists; otherwise wait for next poll
                if _plan_done:
                    st.session_state[f"_sched_open_done_{_today_key}"] = True
                    st.rerun(scope="app")
                else:
                    st.caption("⏳ Waiting for plan before open check...")
        _intraday_scheduler()

    if not plan_run and not check_open_run and not replay_run:
        st.markdown(
            '<div style="text-align:center;padding:50px 0">'
            '<div style="font-size:48px;margin-bottom:12px;opacity:.2">🗓️</div>'
            '<div style="color:#6b7099;font-size:13px">Enter tickers, pick a date, and click '
            '<b style="color:#4d9fff">🗓️ GENERATE PLAN</b> to create your intraday trade plan</div>'
            '</div>',
            unsafe_allow_html=True,
        )

# ──────────────────────────────────────────────────────────────────────────────
# ║ TAB: SCAN HOLDINGS — Free text tickers + intraday planning functionality          ║
# ──────────────────────────────────────────────────────────────────────────────
with tab_scan_holdings:
    st.markdown(
        '<div style="background:linear-gradient(135deg,#0a0b14,#131625);border:1px solid #1a1d2e;'
        'border-radius:8px;padding:18px 24px;margin-bottom:16px">'
        '<div style="display:flex;align-items:center;gap:12px">'
        '<div style="font-size:28px">📊</div>'
        '<div>'
        '<div style="font-size:16px;font-weight:700;color:#e8ecff">Scan Holdings</div>'
        '<div style="font-size:10px;color:#6b7099">Enter your holdings tickers, check alignment signals, + plan your day. '
        'Similar to Intraday Planning but focused on your portfolio positions.</div>'
        '</div></div></div>',
        unsafe_allow_html=True,
    )
    
    # Holdings ticker input (free text, comma-separated)
    holdings_tickers_raw = st.text_area(
        "Holdings tickers (comma-separated)", 
        value="SPY, QQQ",
        height=60, 
        key="holdings_tickers", 
        label_visibility="collapsed",
    )
    
    holdings_scan_date = st.date_input("Scan date", value=date.today(), key="holdings_scan_date")
    holdings_scan_run = st.button("📊 GENERATE SCAN", use_container_width=True, type="primary", key="btn_holdings_scan")
    
    _hld_btn_cols = st.columns(2)
    with _hld_btn_cols[0]:
        holdings_check_prices_run = st.button("☀️ CHECK HOLDINGS PRICES", use_container_width=True, key="btn_hld_check_prices")
    with _hld_btn_cols[1]:
        holdings_replay_run = st.button("🔁 REPLAY SCAN", use_container_width=True, key="btn_hld_replay")

    st.markdown("---")
    _hld_fib_col1, _hld_fib_col2 = st.columns([2, 1])
    with _hld_fib_col1:
        holdings_fib_backdate = st.date_input("Fib Report — As Of Date", value=date.today(), key="holdings_fib_backdate")
    with _hld_fib_col2:
        holdings_fib_run = st.button("📈 GENERATE FIB REPORT", use_container_width=True, type="secondary", key="btn_hld_fib")

    if holdings_fib_run:
        _hld_tickers = [t.strip().upper() for t in holdings_tickers_raw.replace(",", " ").split() if t.strip()]
        if not _hld_tickers:
            st.warning("Enter at least one holdings ticker.")
        else:
            from math import isnan as _hld_isnan
            _hld_fib_col_order = [
                "E 261.8%", "E 200.0%", "E 161.8%", "E 141.4%", "E 127.2%",
                "R 0.0%", "R 23.6%", "R 38.2%", "R 50.0%", "R 61.8%", "R 78.6%", "R 100.0%",
                "N -23.6%", "N -38.2%", "N -50.0%", "N -61.8%", "N -100.0%",
            ]
            _hld_fib_rows = []
            _hld_prog = st.progress(0, text="Building Fib Report...")
            for _hld_i, _hld_sym in enumerate(_hld_tickers):
                _hld_prog.progress((_hld_i + 1) / len(_hld_tickers), text=f"Processing {_hld_sym}...")
                try:
                    if not YFINANCE_AVAILABLE:
                        continue
                    # ── Weekly data for zone + fib levels ─────────────────
                    _hld_df_wk = yf.download(_hld_sym, period="2y", interval="1wk", progress=False)
                    if _hld_df_wk is None or _hld_df_wk.empty:
                        st.warning(f"{_hld_sym}: No weekly data")
                        continue
                    if isinstance(_hld_df_wk.columns, pd.MultiIndex):
                        _hld_df_wk.columns = _hld_df_wk.columns.get_level_values(0)
                    _hld_bd = pd.Timestamp(holdings_fib_backdate)
                    _hld_df_wk = _hld_df_wk[_hld_df_wk.index <= _hld_bd]
                    if _hld_df_wk.empty:
                        st.warning(f"{_hld_sym}: No data before {holdings_fib_backdate}")
                        continue
                    _hc = _hld_df_wk["Close"].squeeze() if hasattr(_hld_df_wk["Close"], 'squeeze') else _hld_df_wk["Close"]
                    _hh = _hld_df_wk["High"].squeeze() if hasattr(_hld_df_wk["High"], 'squeeze') else _hld_df_wk["High"]
                    _hl = _hld_df_wk["Low"].squeeze() if hasattr(_hld_df_wk["Low"], 'squeeze') else _hld_df_wk["Low"]
                    _hld_close = _safe_float(_hc.iloc[-1])
                    _hld_wk_hi = _safe_float(_hh.iloc[-10:].max())
                    _hld_wk_lo = _safe_float(_hl.iloc[-10:].min())
                    _hld_wk_rng = _hld_wk_hi - _hld_wk_lo
                    _hld_wk_pos = (_hld_close - _hld_wk_lo) / _hld_wk_rng * 100 if _hld_wk_rng > 0 else 50.0
                    if _hld_wk_pos >= 70:
                        _hld_wk_zone = "HIGH"
                    elif _hld_wk_pos <= 30:
                        _hld_wk_zone = "LOW"
                    else:
                        _hld_wk_zone = "MID"

                    # ── Earn Zone: use last earnings date close vs weekly range ──
                    try:
                        _hld_earn_evts = get_earnings_dates_yfinance(_hld_sym)
                        if _hld_earn_evts:
                            _hld_last_earn_date = _hld_earn_evts[-1][0]  # most recent earned
                            _hld_earn_hist = yf.download(_hld_sym, start=_hld_last_earn_date, end=_hld_last_earn_date, progress=False, auto_adjust=True)
                            if _hld_earn_hist is None or _hld_earn_hist.empty:
                                _hld_earn_close = _hld_close
                            else:
                                if isinstance(_hld_earn_hist.columns, pd.MultiIndex):
                                    _hld_earn_hist.columns = _hld_earn_hist.columns.get_level_values(0)
                                _hld_earn_close = _safe_float(_hld_earn_hist["Close"].iloc[0])
                            _hld_earn_pos = (_hld_earn_close - _hld_wk_lo) / _hld_wk_rng * 100 if _hld_wk_rng > 0 else 50.0
                            _hld_earn_zone = "HIGH" if _hld_earn_pos >= 70 else ("LOW" if _hld_earn_pos <= 30 else "MID")
                        else:
                            _hld_earn_close = _hld_close
                            _hld_earn_zone = _hld_wk_zone
                    except Exception:
                        _hld_earn_close = _hld_close
                        _hld_earn_zone = _hld_wk_zone

                    # ── Next earnings ──────────────────────────────────────
                    try:
                        _hld_next_earn = estimate_next_earnings(_hld_earn_evts) if _hld_earn_evts else "N/A"
                    except Exception:
                        _hld_next_earn = "N/A"

                    # ── Fund Bias ──────────────────────────────────────────
                    try:
                        _hld_fund = get_fundamentals(_hld_sym)
                        _hld_fb = 0
                        if _hld_fund:
                            _eg = _hld_fund.get("earnings_growth")
                            _rg = _hld_fund.get("revenue_growth")
                            _tu = _hld_fund.get("target_upside")
                            _pm = _hld_fund.get("profit_margin")
                            _hld_fb += (2 if _eg and _eg > 0.10 else 1 if _eg and _eg > 0 else -1 if _eg and _eg > -0.10 else -2 if _eg else 0)
                            _hld_fb += (1 if _rg and _rg > 0.05 else -1 if _rg and _rg < 0 else 0)
                            _hld_fb += (1 if _tu and _tu > 0.10 else -1 if _tu and _tu < 0 else 0)
                            _hld_fb += (1 if _pm and _pm > 0.10 else -1 if _pm and _pm < 0 else 0)
                        if _hld_fb >= 3:       _hld_fund_txt = "🐂 BULLISH"
                        elif _hld_fb >= 1:     _hld_fund_txt = "🐂 LEAN BULLISH"
                        elif _hld_fb > -1:     _hld_fund_txt = "⚖️ NEUTRAL"
                        elif _hld_fb > -3:     _hld_fund_txt = "🐻 LEAN BEARISH"
                        else:                  _hld_fund_txt = "🐻 BEARISH"
                    except Exception:
                        _hld_fund_txt = "N/A"

                    # ── News Sentiment ─────────────────────────────────────
                    try:
                        _hld_news = get_news_details(_hld_sym)
                        _hld_news_lbl = _hld_news.get("label", "No")
                        _g = _hld_news.get("good_score", 0)
                        _b = _hld_news.get("bad_score", 0)
                        _hld_news_txt = (
                            f"📰 POSITIVE (+{_g}/-{_b})" if _hld_news_lbl == "Good"
                            else f"📰 NEGATIVE (+{_g}/-{_b})" if _hld_news_lbl == "Bad"
                            else f"📰 NEUTRAL (+{_g}/-{_b})"
                        )
                    except Exception:
                        _hld_news_txt = "N/A"

                    # ── Fib levels ─────────────────────────────────────────
                    _hld_fib_lvls = calc_fib_levels(_hld_wk_lo, _hld_wk_hi)
                    _hld_row = {
                        "Ticker": _hld_sym,
                        "As Of": str(holdings_fib_backdate),
                        "Close on Earn Date": f"${_hld_earn_close:.2f}",
                        "Earn Zone": _hld_earn_zone,
                        "Weekly Zone": _hld_wk_zone,
                    }
                    for _fn in _hld_fib_col_order:
                        _fv = _hld_fib_lvls.get(_fn, float("nan"))
                        _hld_row[_fn] = f"${_fv:.2f}" if not _hld_isnan(_fv) else ""
                    _hld_row["Fund Bias"] = _hld_fund_txt
                    _hld_row["News Sentiment"] = _hld_news_txt
                    _hld_row["Next Earnings"] = _hld_next_earn
                    _hld_row["Conclusion"] = (
                        f"Wk: {_hld_wk_zone} | Earn: {_hld_earn_zone} | "
                        f"Hi: ${_hld_wk_hi:.2f} Lo: ${_hld_wk_lo:.2f} | "
                        f"{_hld_fund_txt} | {_hld_news_txt}"
                    )
                    _hld_fib_rows.append(_hld_row)
                except Exception as _hld_exc:
                    st.warning(f"{_hld_sym}: {_hld_exc}")
            _hld_prog.empty()

            if _hld_fib_rows:
                _hld_fib_df = pd.DataFrame(_hld_fib_rows)

                # ── Fetch live prices ──────────────────────────────────
                try:
                    _hld_live_tks = list(_hld_fib_df["Ticker"].unique())
                    _hld_lp_raw = yf.download(_hld_live_tks, period="1d", progress=False, auto_adjust=True)
                    if hasattr(_hld_lp_raw, "columns") and "Close" in _hld_lp_raw.columns:
                        _hld_lp_series = _hld_lp_raw["Close"].iloc[-1] if len(_hld_lp_raw) > 0 else pd.Series(dtype=float)
                    else:
                        _hld_lp_series = pd.Series(dtype=float)
                    _hld_live_prices = {}
                    for _ltk in _hld_live_tks:
                        try:
                            _hld_live_prices[_ltk] = _safe_float(_hld_lp_series[_ltk]) if _ltk in _hld_lp_series.index else _safe_float(getattr(yf.Ticker(_ltk).fast_info, 'last_price', None))
                        except Exception:
                            _hld_live_prices[_ltk] = None
                except Exception:
                    _hld_live_prices = {}

                # ── Coloring helpers ───────────────────────────────────
                def _hld_color_earn_zone(v):
                    v = str(v)
                    if v == "HIGH": return "background-color:#3d0a1a;color:#ff4d6a;font-weight:700"
                    if v == "LOW":  return "background-color:#0a3d1f;color:#00e5a0;font-weight:700"
                    return "background-color:#3d3a0a;color:#f5c842;font-weight:700"
                def _hld_color_wk_zone(v):
                    v = str(v)
                    if v == "HIGH": return "background-color:#3d0a1a;color:#ff4d6a;font-weight:700"
                    if v == "LOW":  return "background-color:#0a3d1f;color:#00e5a0;font-weight:700"
                    return "background-color:#3d3a0a;color:#f5c842;font-weight:700"
                def _hld_color_fund(v):
                    v = str(v)
                    if "BULLISH" in v and "LEAN" not in v: return "color:#00e5a0;font-weight:700"
                    if "LEAN BULLISH" in v: return "color:#7ccfb0;font-weight:700"
                    if "BEARISH" in v and "LEAN" not in v: return "color:#ff4d6a;font-weight:700"
                    if "LEAN BEARISH" in v: return "color:#ff8c8c;font-weight:700"
                    return "color:#f5c842"
                def _hld_color_news(v):
                    v = str(v)
                    if "POSITIVE" in v: return "color:#00e5a0;font-weight:700"
                    if "NEGATIVE" in v: return "color:#ff4d6a;font-weight:700"
                    return "color:#f5c842"

                _hld_e_set = set(c for c in _hld_fib_col_order if c.startswith("E "))
                _hld_r_set = set(c for c in _hld_fib_col_order if c.startswith("R "))
                _hld_golden_set = {"R 38.2%", "R 50.0%", "R 61.8%"}

                def _hld_highlight_fib(row):
                    zone = str(row.get("Weekly Zone", ""))
                    out = []
                    for col in row.index:
                        if zone == "HIGH" and col in _hld_r_set:
                            out.append("background-color:#1f1400;color:#f5c842;font-weight:700")
                        elif zone == "LOW" and col in _hld_e_set:
                            out.append("background-color:#001a0a;color:#00e5a0;font-weight:700")
                        elif col in _hld_golden_set:
                            out.append("background-color:#1a1500;color:#d4a017;font-weight:600;border-bottom:2px solid #d4a01780")
                        else:
                            out.append("")
                    return out

                def _hld_highlight_live(row):
                    ticker  = str(row.get("Ticker", ""))
                    live_px = _hld_live_prices.get(ticker)
                    styles  = [""] * len(row)
                    if live_px is None:
                        return styles
                    best_col  = None
                    best_diff = float("inf")
                    for col in _hld_fib_col_order:
                        if col not in row.index:
                            continue
                        try:
                            val = float(str(row[col]).replace("$", "").strip())
                            diff = abs(val - live_px)
                            if diff < best_diff:
                                best_diff = diff
                                best_col  = col
                        except Exception:
                            pass
                    if best_col is not None:
                        col_idx = list(row.index).index(best_col)
                        styles[col_idx] = "background-color:#0a1f3a;color:#ffffff;font-weight:900;border:2px solid #4d9fff"
                    return styles

                st.markdown("#### 📈 Fib Scenario Report — Holdings")
                st.caption(
                    f"{len(_hld_fib_rows)} ticker{'s' if len(_hld_fib_rows)!=1 else ''} · "
                    f"E=Extensions · R=Retracements · N=Negative · 🔵 = current price level")

                _hld_ez_config = [
                    ("HIGH", "#ff4d6a", "🔴"),
                    ("MID",  "#f5c842", "🟡"),
                    ("LOW",  "#00e5a0", "🟢"),
                ]
                for _hld_ez, _hld_ez_color, _hld_ez_icon in _hld_ez_config:
                    _hld_ez_mask = _hld_fib_df["Earn Zone"] == _hld_ez
                    _hld_ez_group = _hld_fib_df[_hld_ez_mask].copy()
                    if _hld_ez_group.empty:
                        continue
                    st.markdown(
                        f'<div style="background:rgba(0,0,0,0.3);border-left:3px solid {_hld_ez_color}40;'
                        f'padding:6px 12px;margin:8px 0 4px;border-radius:0 4px 4px 0">'
                        f'<span style="font-size:14px">{_hld_ez_icon}</span> '
                        f'<b style="color:{_hld_ez_color};font-size:13px">{_hld_ez} Earn Zone</b> '
                        f'<span style="color:#6b7099;font-size:11px">({len(_hld_ez_group)} ticker{"s" if len(_hld_ez_group)!=1 else ""})</span>'
                        f'</div>',
                        unsafe_allow_html=True,
                    )
                    _hld_styled = _hld_ez_group.style
                    if "Earn Zone"      in _hld_ez_group.columns: _hld_styled = _hld_styled.applymap(_hld_color_earn_zone, subset=["Earn Zone"])
                    if "Weekly Zone"    in _hld_ez_group.columns: _hld_styled = _hld_styled.applymap(_hld_color_wk_zone,   subset=["Weekly Zone"])
                    if "Fund Bias"      in _hld_ez_group.columns: _hld_styled = _hld_styled.applymap(_hld_color_fund,      subset=["Fund Bias"])
                    if "News Sentiment" in _hld_ez_group.columns: _hld_styled = _hld_styled.applymap(_hld_color_news,      subset=["News Sentiment"])
                    _hld_styled = _hld_styled.apply(_hld_highlight_fib, axis=1)
                    if _hld_live_prices:
                        _hld_styled = _hld_styled.apply(_hld_highlight_live, axis=1)
                    st.dataframe(_hld_styled, use_container_width=True, hide_index=True,
                                 height=min(len(_hld_ez_group) * 35 + 48, 400))

                # ── 🥇 Golden Zone ──
                _gz3_rows = []
                for _, _gz3_r in _hld_fib_df.iterrows():
                    try:
                        _gz3_tk = _gz3_r["Ticker"]
                        _gz3_38 = float(str(_gz3_r.get("R 38.2%", "")).replace("$", ""))
                        _gz3_61 = float(str(_gz3_r.get("R 61.8%", "")).replace("$", ""))
                        _gz3_px = _hld_live_prices.get(_gz3_tk) if _hld_live_prices else None
                        if _gz3_px is None:
                            _gz3_px = float(str(_gz3_r.get("Close", "")).replace("$", ""))
                        _gz3_lo, _gz3_hi = min(_gz3_38, _gz3_61), max(_gz3_38, _gz3_61)
                        if _gz3_lo <= _gz3_px <= _gz3_hi:
                            _gz3_rows.append({"Ticker": _gz3_tk, "Price": f"${_gz3_px:.2f}",
                                              "R 38.2%": _gz3_r.get("R 38.2%", ""),
                                              "R 50.0%": _gz3_r.get("R 50.0%", ""),
                                              "R 61.8%": _gz3_r.get("R 61.8%", ""),
                                              "Earn Zone": _gz3_r.get("Earn Zone", "")})
                    except Exception:
                        pass
                if _gz3_rows:
                    st.markdown(
                        '<div style="background:linear-gradient(135deg,#1a1500,#1f1800);border:1px solid #d4a01740;'
                        'border-radius:8px;padding:12px 18px;margin:16px 0 8px">'
                        '<span style="font-size:18px">🥇</span> '
                        '<b style="color:#d4a017;font-size:14px">Golden Zone</b> '
                        '<span style="color:#8b7d3c;font-size:11px">'
                        f'({len(_gz3_rows)} ticker{"s" if len(_gz3_rows)!=1 else ""}) — '
                        f'Price within R 38.2% – R 61.8% retracement</span>'
                        '</div>',
                        unsafe_allow_html=True,
                    )
                    _gz3_df = pd.DataFrame(_gz3_rows)
                    _gz3_styled = _gz3_df.style.applymap(
                        lambda v: "background-color:#1a1500;color:#d4a017;font-weight:700"
                        if str(v).startswith("$") else "",
                        subset=["R 38.2%", "R 50.0%", "R 61.8%"]
                    )
                    st.dataframe(_gz3_styled, use_container_width=True, hide_index=True,
                                 height=min(len(_gz3_rows) * 35 + 48, 300))

                # ── CSV download ──────────────────────────────────────
                import io as _hld_io
                _hld_csv_buf = _hld_io.StringIO()
                _hld_fib_df.to_csv(_hld_csv_buf, index=False)
                st.download_button(
                    label=f"📥 Download Fib Report ({len(_hld_fib_rows)} tickers)",
                    data=_hld_csv_buf.getvalue(),
                    file_name=f"holdings_fib_report_{date.today().strftime('%Y%m%d')}.csv",
                    mime="text/csv",
                    key="btn_hld_fib_dl",
                )
            else:
                st.info("No Fib data could be retrieved for the given holdings tickers.")

    if not holdings_scan_run and not holdings_check_prices_run and not holdings_replay_run and not holdings_fib_run:
        st.markdown(
            '<div style="text-align:center;padding:50px 0">'
            '<div style="font-size:48px;margin-bottom:12px;opacity:.2">📊</div>'
            '<div style="color:#6b7099;font-size:13px">Enter your holdings tickers, pick a date, and click '
            '<b style="color:#4d9fff">📊 GENERATE SCAN</b> to analyze alignment</div>'
            '</div>',
            unsafe_allow_html=True,
        )


# ──────────────────────────────────────────────────────────────────────────────
# ║ TAB: GROWTH SCANNER                                                        ║
# ──────────────────────────────────────────────────────────────────────────────
with tab_growth:
    from future_growth_scan import GROWTH_SECTORS, scan_ticker as _gs_scan_ticker, SCORE_WEIGHTS as _gs_weights
    from future_growth_scan import _color_zone as _gs_color_zone, _color_score as _gs_color_score, _color_upside as _gs_color_upside
    st.markdown(
        '<div style="background:linear-gradient(135deg,#0a0b14,#131625);border:1px solid #1a1d2e;'
        'border-radius:8px;padding:18px 24px;margin-bottom:16px">'
        '<div style="display:flex;align-items:center;gap:12px">'
        '<div style="font-size:28px">🚀</div>'
        '<div>'
        '<div style="font-size:16px;font-weight:700;color:#e8ecff">Future Growth Scanner</div>'
        '<div style="font-size:10px;color:#6b7099">Scans high-potential growth sectors: '
        'AI/ML, Quantum, Space, Biotech, Defense, Clean Energy, Longevity</div>'
        '</div></div></div>',
        unsafe_allow_html=True,
    )
    _gs_col1, _gs_col2, _gs_col3 = st.columns([2, 1, 1])
    with _gs_col1:
        _gs_sectors = st.multiselect(
            "Sectors to scan",
            options=list(GROWTH_SECTORS.keys()),
            default=list(GROWTH_SECTORS.keys()),
            key="gs_sectors",
        )
    with _gs_col2:
        _gs_asof = st.date_input("As Of Date", value=date.today(), key="gs_asof")
        _gs_min_score = st.slider("Min Score", 0, 100, 40, key="gs_min_score")
    with _gs_col3:
        _gs_top_n = st.number_input("Top N per sector (0=all)", min_value=0, max_value=50, value=10, key="gs_top_n")
        _gs_sort = st.selectbox("Sort by", ["Score", "_score_raw"], index=0, key="gs_sort")

    _gs_run = st.button("🔍 SCAN GROWTH SECTORS", use_container_width=True, type="primary", key="btn_gs_run")

    if not _gs_run:
        st.markdown(
            '<div style="text-align:center;padding:36px 0">'
            '<div style="font-size:42px;margin-bottom:12px;opacity:.2">🚀</div>'
            '<div style="color:#6b7099;font-size:13px">Select sectors and click '
            '<b style="color:#4d9fff">🔍 SCAN GROWTH SECTORS</b></div>'
            '</div>',
            unsafe_allow_html=True,
        )
        for _gs_s, _gs_tks in GROWTH_SECTORS.items():
            if _gs_s in _gs_sectors:
                st.markdown(f"**{_gs_s}** — {', '.join(_gs_tks)}")
    else:
        _gs_all = []
        for _gs_sector in _gs_sectors:
            _gs_tickers = GROWTH_SECTORS.get(_gs_sector, [])
            if not _gs_tickers:
                continue
            st.markdown(f"### {_gs_sector}")
            _gs_rows = []
            _gs_prog = st.progress(0, text=f"Scanning {_gs_sector}...")

            # ── Parallel growth scan using ThreadPoolExecutor ──
            executor = get_thread_pool()
            _gs_futures = {
                executor.submit(_gs_scan_ticker, sym, _gs_asof): sym
                for sym in _gs_tickers
            }
            _gs_done = 0
            for future in concurrent.futures.as_completed(_gs_futures):
                _gs_done += 1
                _gs_sym = _gs_futures[future]
                _gs_prog.progress(_gs_done / len(_gs_tickers), text=f"Scanning {_gs_sym}...")
                try:
                    _gs_row = future.result()
                    if _gs_row and "_error" not in _gs_row:
                        _gs_rows.append(_gs_row)
                    elif _gs_row and "_error" in _gs_row:
                        st.caption(f"⚠️ {_gs_sym}: {_gs_row['_error']}")
                except Exception:
                    pass
            _gs_prog.empty()
            if not _gs_rows:
                st.warning("No data returned for this sector.")
                continue
            _gs_df = pd.DataFrame(_gs_rows)
            _gs_df = _gs_df[_gs_df["_score_raw"] >= _gs_min_score].sort_values("_score_raw", ascending=False)
            if _gs_top_n > 0:
                _gs_df = _gs_df.head(_gs_top_n)
            _gs_df = _gs_df.drop(columns=["_score_raw"], errors="ignore")
            if _gs_df.empty:
                st.info(f"No tickers met minimum score {_gs_min_score}.")
                continue
            _gs_dcols = [c for c in _gs_df.columns if not c.startswith("_")]
            _gs_styled = _gs_df[_gs_dcols].style
            if "Weekly Zone" in _gs_dcols: _gs_styled = _gs_styled.applymap(_gs_color_zone, subset=["Weekly Zone"])
            if "Score" in _gs_dcols:       _gs_styled = _gs_styled.applymap(_gs_color_score, subset=["Score"])
            if "Upside%" in _gs_dcols:     _gs_styled = _gs_styled.applymap(_gs_color_upside, subset=["Upside%"])
            st.dataframe(_gs_styled, use_container_width=True, hide_index=True,
                         height=min(len(_gs_df) * 35 + 48, 500))
            _gs_all.extend(_gs_rows)

        if _gs_all:
            st.markdown("---")
            st.markdown("### 🏆 Top Growth Picks Across All Sectors")
            _gs_comb = pd.DataFrame(_gs_all)
            _gs_comb = _gs_comb[_gs_comb["_score_raw"] >= _gs_min_score].drop_duplicates("Ticker")
            _gs_comb = _gs_comb.sort_values("_score_raw", ascending=False).head(20)
            _gs_comb = _gs_comb.drop(columns=["_score_raw"], errors="ignore")
            _gs_cdcols = [c for c in _gs_comb.columns if not c.startswith("_")]
            _gs_cs = _gs_comb[_gs_cdcols].style
            if "Weekly Zone" in _gs_cdcols: _gs_cs = _gs_cs.applymap(_gs_color_zone, subset=["Weekly Zone"])
            if "Score" in _gs_cdcols:       _gs_cs = _gs_cs.applymap(_gs_color_score, subset=["Score"])
            if "Upside%" in _gs_cdcols:     _gs_cs = _gs_cs.applymap(_gs_color_upside, subset=["Upside%"])
            st.dataframe(_gs_cs, use_container_width=True, hide_index=True)
            st.download_button(
                "📥 Download Growth Scan CSV",
                data=_gs_comb.to_csv(index=False),
                file_name=f"growth_scan_{_gs_asof.strftime('%Y%m%d')}.csv",
                mime="text/csv", use_container_width=True,
            )

# ──────────────────────────────────────────────────────────────────────────────
# ║ TAB: SCENARIOS — Weekly + Earnings scenario grids                         ║
# ──────────────────────────────────────────────────────────────────────────────

# ══════════════════════════════════════════════════════════════════════════════
# BUTTON HANDLERS — shared handlers for both Intraday Planning & Scan Holdings
# Uses dispatch variables (_from_holdings, _active_tab, _active_tickers_raw, etc.)
# ══════════════════════════════════════════════════════════════════════════════

# ── Stock Analysis ──────────────────────────
try:
 if earnings_estimator_btn:
  with tab_estimator:
    _scan_title = f"### 🔬 Stock Analysis — Scanning {len(estimator_watchlist)} Tickers"
    if est_fib_backdate < date.today():
        _scan_title += f" (As Of {est_fib_backdate})"
    st.markdown(_scan_title)
    progress_bar = st.progress(0)
    status_text = st.empty()
    total = len(estimator_watchlist)

    scan_results  = []      # all tickers with signals
    scan_errors   = []      # hard errors (API failures etc.)
    scan_no_data  = []      # tickers that returned no data (timeout, bad ticker, etc.)

    # ── Parallel scan using ThreadPoolExecutor ──
    _scan_backdate = est_fib_backdate if est_fib_backdate < date.today() else None
    scan_results, _par_errors, scan_no_data = _parallel_scan_with_progress(
        estimator_watchlist, api_key, api_secret, data_source,
        use_fib, fib_tol, use_strategy,
        progress_bar=progress_bar, status_text=status_text,
        fetch_fundamentals=True, as_of_date=_scan_backdate,
    )
    scan_errors = _par_errors
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

        # ── Add finviz chart link column ──
        if not df_all.empty:
            df_all["chart"] = df_all["ticker"].apply(
                lambda t: f"https://finviz.com/quote.ashx?t={t}&p=d"
            )

        # ── Add news sentiment column (parallelized) ──
        if not df_all.empty:
            try:
                from news_sentiment import get_news_sentiment_batch
                _tickers_list = df_all["ticker"].tolist()
                _news_map = get_news_sentiment_batch(_tickers_list, max_workers=4)
                df_all["news"] = df_all["ticker"].map(_news_map).fillna("No")
            except Exception:
                df_all["news"] = "No"

        # ── Overall Fundamental label: Strong / Weak / Neutral ──
        def _calc_fund_label(row):
            sc = 0
            rg = row.get("revenue_growth")
            if pd.notnull(rg) and rg != "":
                if rg > 0.20: sc += 2
                elif rg > 0.05: sc += 1
                elif rg < -0.05: sc -= 2
            eg = row.get("earnings_growth")
            if pd.notnull(eg) and eg != "":
                if eg > 0.25: sc += 2
                elif eg > 0.05: sc += 1
                elif eg < -0.10: sc -= 2
            pm = row.get("profit_margin")
            if pd.notnull(pm) and pm != "":
                if pm > 0.20: sc += 1
                elif pm < 0: sc -= 2
            rv = row.get("roe")
            if pd.notnull(rv) and rv != "":
                if rv > 0.15: sc += 1
                elif rv < 0: sc -= 1
            pe = row.get("pe_ratio")
            if pd.notnull(pe) and pe != "" and isinstance(pe, (int, float)):
                if 0 < pe < 15: sc += 1
                elif pe > 40: sc -= 1
            peg = row.get("peg_ratio")
            if pd.notnull(peg) and peg != "" and isinstance(peg, (int, float)):
                if 0 < peg < 1: sc += 1
                elif peg > 3: sc -= 1
            de = row.get("debt_to_equity")
            if pd.notnull(de) and de != "" and isinstance(de, (int, float)):
                if de < 30: sc += 1
                elif de > 200: sc -= 1
            up = row.get("target_upside")
            if pd.notnull(up) and up != "" and isinstance(up, (int, float)):
                if up > 20: sc += 1
                elif up < -15: sc -= 1
            if sc >= 3: return "Strong"
            elif sc <= -2: return "Weak"
            return "Neutral"
        if not df_all.empty:
            df_all["fundamental"] = df_all.apply(_calc_fund_label, axis=1)

        # ── Alpaca Options Strategy column ──
        if not df_all.empty and api_secret:
            _opt_status = st.empty()
            _opt_status.caption("⏳ Fetching Alpaca options strategies for actionable setups...")
            def _fetch_opt(row):
                try:
                    if str(row.get("entry_status", "")).upper() != "ENTER":
                        return "—"
                    _vrd = str(row.get("verdict","")).upper()
                    if _vrd not in ("BULLISH", "BEARISH"):
                        return "—"
                    _dir = "LONG" if _vrd == "BULLISH" else "SHORT"
                    _price = float(row.get("price", 0)) if row.get("price") else 0
                    if _price <= 0:
                        return "N/A"
                    res = get_options_strategy_alpaca(
                        row["ticker"], _price, _dir, "MID", api_key, api_secret)
                    if res and res.get("summary"):
                        txt = res["summary"]
                        if res.get("alt"):
                            txt += f" | {res['alt']}"
                        return txt
                except Exception:
                    pass
                return "N/A"
            from concurrent.futures import ThreadPoolExecutor
            _opt_rows = df_all.to_dict("records")
            with ThreadPoolExecutor(max_workers=3) as _opt_pool:
                _opt_results = list(_opt_pool.map(_fetch_opt, _opt_rows))
            df_all["alpaca_options"] = _opt_results
            _opt_status.empty()
            trim_memory()

        # Split: actionable (ENTER) vs filtered
        actionable = df_all[df_all["entry_status"] == "ENTER"].copy()
        filtered   = df_all[df_all["entry_status"] != "ENTER"].copy()

        # Format display columns — entry_status first, then grade, then signals
        display_cols = [
            "chart", "ticker", "price", "fundamental", "mtf_action",
            "entry", "stop_loss", "target1", "target2", "t1_days", "alpaca_options",
            "entry_status", "entry_grade", "entry_label",
            "expected_avg",
            "weekly_bias", "daily_bias", "4h_bias", "ma_bias", "mtf_signal",
            "cpr_tc", "cpr_p", "cpr_bc", "cpr_type", "cpr_position", "cpr_interpretation",
            "sector", "verdict", "confidence", "score", "best_setup",
            "candle", "vol_action", "vol_trend", "vol_ratio", "poc", "val", "vah",
            "persistence", "quote_type",
            "risk_pct",
            "rr_t1", "rr_t2", "best_rr",
            "pe_ratio", "forward_pe", "peg_ratio", "valuation", "market_cap",
            "revenue_str", "revenue_growth", "earnings_growth",
            "profit_margin", "roe", "debt_to_equity", "beta",
            "dividend_yield", "short_pct",
            "analyst_target", "target_1y", "target_upside",
            "rec_key", "num_analysts",
            "week52_position", "pct_from_high",
            "news",
            "expected_wr",
            "flags",
        ]
        display_cols = [c for c in display_cols if c in df_all.columns]

        def _format_df(df):
            df = df.copy()
            # ── Abbreviate bias columns (just colors) ──
            _bias_map = {"BULLISH": "Bull", "BEARISH": "Bear", "NEUTRAL": "—", "POSITIVE": "Bull", "NEGATIVE": "Bear"}
            for _bc in ["weekly_bias", "daily_bias", "4h_bias", "ma_bias"]:
                if _bc in df.columns:
                    df[_bc] = df[_bc].apply(lambda x: _bias_map.get(str(x).upper().strip(), "—") if pd.notnull(x) else "—")
            # ── Abbreviate status: ENTER→E, HOLD→H, SKIP→S ──
            if "entry_status" in df.columns:
                _st_map = {"ENTER": "E", "HOLD": "H", "SKIP": "S", "WAIT": "W", "STRONG HOLD": "SH"}
                df["entry_status"] = df["entry_status"].apply(lambda x: _st_map.get(str(x).upper().strip(), str(x)[:2]) if pd.notnull(x) else "")
            # ── Shorten entry signal ──
            if "entry_label" in df.columns:
                df["entry_label"] = df["entry_label"].apply(lambda x: str(x)[:18] if pd.notnull(x) and x else "")
            # ── Fundamental: just colors ──
            if "fundamental" in df.columns:
                _f_map = {"STRONG": "S", "WEAK": "W", "NEUTRAL": "N"}
                df["fundamental"] = df["fundamental"].apply(lambda x: _f_map.get(str(x).upper().strip(), "N") if pd.notnull(x) else "N")
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
            for _rr_col in ["rr_t1", "rr_t2", "best_rr"]:
                if _rr_col in df.columns:
                    df[_rr_col] = df[_rr_col].apply(lambda x: f"{x:.2f}x" if pd.notnull(x) and isinstance(x, (int, float)) else x)
            for _pcol in ["price", "entry", "stop_loss", "target1", "target2"]:
                if _pcol in df.columns:
                    df[_pcol] = df[_pcol].apply(lambda x: f"{x:.2f}" if pd.notnull(x) and isinstance(x, (int, float)) else x)
            if "persistence" in df.columns:
                df["persistence"] = df["persistence"].apply(lambda x: f"{x:.0f}%" if pd.notnull(x) else "")
            return df

        col_rename = {
            "chart":        "Chart",
            "alpaca_options": "Alpaca Options",
            "entry_status": "Status",
            "entry_grade":  "Grade",
            "entry_label":  "Entry Signal",
            "expected_wr":  "Exp WR%",
            "expected_avg": "Exp Avg P&L",
            "weekly_bias":  "Weekly",
            "daily_bias":   "Daily",
            "4h_bias":      "4H",
            "ma_bias":      "MA Bias",
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
            "stop_loss": "Stop", "t1_days": "T1 (td)", "risk_pct": "Risk%",
            "rr_t1": "RR(T1)", "rr_t2": "RR(T2)", "best_rr": "Best RR",
            "fundamental": "Funda",
            "news": "News",
            "flags": "Signals",
        }

        # ── Color styling for Weekly / Daily / 4H / MA Bias / Signal columns ───────
        _bias_cols = {"Weekly", "Daily", "4H", "MA Bias"}
        def _style_bias_cols(df):
            """Return a Styler with green/red/grey on bias cols + signal col."""
            def _color_bias(val):
                if not isinstance(val, str):
                    return ""
                v = val.upper()
                if "BULL" in v or v == "POSITIVE":
                    return "background-color: #0a3d1f; color: #00e5a0; font-weight: 700"
                elif "BEAR" in v or v == "NEGATIVE":
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
            def _color_news(val):
                if not isinstance(val, str):
                    return ""
                v = val.upper()
                if v == "GOOD":
                    return "background-color: #0a3d1f; color: #00e5a0; font-weight: 700"
                elif v == "BAD":
                    return "background-color: #3d0a1a; color: #ff4d6a; font-weight: 700"
                return "color: #6b7099"
            bias_present = [c for c in _bias_cols if c in df.columns]
            styler = df.style.applymap(_color_bias, subset=bias_present)
            if "Signal" in df.columns:
                styler = styler.applymap(_color_signal, subset=["Signal"])
            if "News" in df.columns:
                styler = styler.applymap(_color_news, subset=["News"])
            def _color_fund(val):
                if not isinstance(val, str): return ""
                v = val.upper()
                if v in ("S", "STRONG"): return "background-color: #0a3d1f; color: #00e5a0; font-weight: 700"
                elif v in ("W", "WEAK"): return "background-color: #3d0a1a; color: #ff4d6a; font-weight: 700"
                return "color: #6b7099"
            if "Funda" in df.columns:
                styler = styler.applymap(_color_fund, subset=["Funda"])
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

        # Tabs: Exceptional, Rank 1, Rank 2, All (ranked)
        _has_rank = "mtf_rank" in actionable.columns if not actionable.empty else False
        r1_df = actionable[actionable["mtf_rank"] == 1] if _has_rank else pd.DataFrame()
        r2_df = actionable[actionable["mtf_rank"] == 2] if _has_rank else pd.DataFrame()
        rest_df = actionable[actionable["mtf_rank"] >= 3] if _has_rank else pd.DataFrame()

        # Exceptional: HIGH confidence + score>=4 + expected WR > 80% + A+ Long + Accumulating
        _exc_cols_ok = (not actionable.empty and
                        all(c in actionable.columns for c in ["confidence", "score", "expected_wr", "mtf_signal", "vol_trend"]))
        try:
            exc_df = actionable[
                (actionable["confidence"] == "HIGH") &
                (pd.to_numeric(actionable["score"], errors="coerce").abs() >= 4) &
                (pd.to_numeric(actionable["expected_wr"], errors="coerce") > 80) &
                (actionable["mtf_signal"] == "A+ Long") &
                (actionable["vol_trend"] == "ACCUMULATING")
            ] if _exc_cols_ok else pd.DataFrame()
        except Exception:
            exc_df = pd.DataFrame()

        # Exceptional Bearish: HIGH confidence + score<=-4 + expected WR > 80% + A+ Short + Distributing
        try:
            exc_bear_df = actionable[
                (actionable["confidence"] == "HIGH") &
                (pd.to_numeric(actionable["score"], errors="coerce") <= -4) &
                (pd.to_numeric(actionable["expected_wr"], errors="coerce") > 80) &
                (actionable["mtf_signal"] == "A+ Short") &
                (actionable["vol_trend"] == "DISTRIBUTING")
            ] if _exc_cols_ok else pd.DataFrame()
        except Exception:
            exc_bear_df = pd.DataFrame()

        view_tab_exc, view_tab_exc_bear, view_tab1, view_tab2, view_tab3, view_tab4 = st.tabs([
            f"⭐ Exceptional ({len(exc_df)})",
            f"💀 Exceptional Bear ({len(exc_bear_df)})",
            f"🎯 Rank 1 — Full Align ({len(r1_df)})",
            f"✅ Rank 2 — Two Aligned ({len(r2_df)})",
            f"📋 Rank 3+ — Rest ({len(rest_df)})",
            f"📊 All Tickers ({len(df_all)})",
        ])

        # Column config for finviz links — reused across all rank tables
        _chart_col_config = {
            "Chart": st.column_config.LinkColumn("Chart", display_text="📈 Chart"),
        }

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
                _vr_col = col_rename.get("vol_ratio", "Vol Ratio")
                df_r = df_r.sort_values(by=_vr_col, ascending=False, key=lambda s: pd.to_numeric(s.astype(str).str.replace('x','',regex=False), errors='coerce')) if _vr_col in df_r.columns else df_r
                st.dataframe(_style_bias_cols(df_r), use_container_width=True, height=min(40*len(df_r)+38, 600), column_config=_chart_col_config)
                st.download_button(
                    f"📥 Download {rank_label} CSV", df_r.to_csv(index=False),
                    file_name=f"scan_rank_{rank_label[:6].strip()}_{date.today()}.csv",
                    mime="text/csv", use_container_width=True,
                    key=f"dl_{rank_label[:6]}")

        _render_rank_table(exc_df, view_tab_exc, "⭐ Exceptional", "#FFD700",
                           "No exceptional setups (HIGH confidence + score ≥4 + WR >80%).")
        _render_rank_table(exc_bear_df, view_tab_exc_bear, "💀 Exceptional Bear", "#ff4d6a",
                           "No exceptional bearish setups (HIGH confidence + score ≤-4 + WR >80% + A+ Short + Distributing).")
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
                    _vr_col = col_rename.get("vol_ratio", "Vol Ratio")
                    df_r = df_r.sort_values(by=_vr_col, ascending=False, key=lambda s: pd.to_numeric(s.astype(str).str.replace('x','',regex=False), errors='coerce')) if _vr_col in df_r.columns else df_r
                    st.dataframe(_style_bias_cols(df_r), use_container_width=True, height=min(40*len(df_r)+38, 400), column_config=_chart_col_config)

        # All tickers tab
        with view_tab4:
            st.caption("All tickers including filtered ones. Status column shows why each was skipped.")
            cols_all = [c for c in display_cols if c in df_all.columns]
            df_show  = _format_df(df_all)[cols_all].rename(columns=col_rename)
            _vr_col_all = col_rename.get("vol_ratio", "Vol Ratio")
            df_show = df_show.sort_values(
                by=["Status", _vr_col_all],
                ascending=[True, False],
                key=lambda col: col.map(lambda x: (0 if x=="ENTER" else 1) if col.name=="Status" else x)
                    if col.name == "Status" else pd.to_numeric(col.astype(str).str.replace('x','',regex=False), errors='coerce')
            ) if "Status" in df_show.columns else df_show
            st.dataframe(_style_bias_cols(df_show), use_container_width=True, height=min(40*len(df_show)+38, 700), column_config=_chart_col_config)
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

        # ── Weekly Hold Win Rate — by Grade / Signal / Action ────────────────
        _scan_dt = est_fib_backdate if est_fib_backdate < date.today() else date.today()
        _wd = _scan_dt.weekday()                 # 0=Mon … 4=Fri
        _target_fri = _scan_dt + timedelta(days=(4 - _wd))

        _show_wr_table = _target_fri <= date.today()  # Friday data available?

        if _show_wr_table and enter_results:
            st.markdown("---")
            st.markdown("### 📊 Weekly Hold Win Rate — by Grade / Signal / Action")
            st.caption(f"Outcome for each ENTER signal if held until Friday {_target_fri} close")

            # Batch-download Friday closes via yfinance
            _wr_tickers = list(set(r["ticker"] for r in enter_results))
            _fri_start = str(_target_fri)
            _fri_end   = str(_target_fri + timedelta(days=3))  # buffer past weekend
            try:
                _fri_raw = yf.download(
                    _wr_tickers, start=_fri_start, end=_fri_end,
                    auto_adjust=True, progress=False, threads=False,
                )
                _fri_closes = {}
                if _fri_raw is not None and not _fri_raw.empty:
                    if len(_wr_tickers) == 1:
                        # yf.download returns flat columns for single ticker
                        if "Close" in _fri_raw.columns and not _fri_raw["Close"].empty:
                            _fri_closes[_wr_tickers[0]] = float(_fri_raw["Close"].iloc[0])
                    else:
                        _close_df = _fri_raw["Close"] if "Close" in _fri_raw.columns else _fri_raw.xs("Close", axis=1, level=0)
                        for _tk in _wr_tickers:
                            if _tk in _close_df.columns:
                                _v = _close_df[_tk].dropna()
                                if not _v.empty:
                                    _fri_closes[_tk] = float(_v.iloc[0])
            except Exception as _dl_err:
                _fri_closes = {}
                st.warning(f"Could not fetch Friday closes: {_dl_err}")

            if _fri_closes:
                _wr_rows = []
                for r in enter_results:
                    _t   = r["ticker"]
                    _fpx = _fri_closes.get(_t)
                    if _fpx is None:
                        continue
                    _epx = r.get("entry", r.get("price", 0))
                    _vrd = r.get("verdict", "")
                    if "BULLISH" in _vrd:
                        _pnl = (_fpx - _epx) / _epx * 100 if _epx else 0
                        _win = _fpx > _epx
                    elif "BEARISH" in _vrd:
                        _pnl = (_epx - _fpx) / _epx * 100 if _epx else 0
                        _win = _fpx < _epx
                    else:
                        continue
                    _wr_rows.append({
                        "Ticker": _t,
                        "Grade": r.get("entry_grade", "?"),
                        "Signal": r.get("mtf_signal", "?"),
                        "Action": r.get("mtf_action", "?"),
                        "Dir": "LONG" if "BULLISH" in _vrd else "SHORT",
                        "Entry": round(_epx, 2),
                        "Fri Close": round(_fpx, 2),
                        "P&L %": round(_pnl, 2),
                        "Result": "WIN" if _win else "LOSS",
                    })

                if _wr_rows:
                    _wr_all = pd.DataFrame(_wr_rows)
                    _total_w = _wr_all["Result"].eq("WIN").sum()
                    _total_n = len(_wr_all)
                    _total_wr = round(_total_w / _total_n * 100, 1) if _total_n else 0
                    _total_avg = round(_wr_all["P&L %"].mean(), 2)

                    st.markdown(
                        f'<div style="background:#0d0f17;border:1px solid #1a1d2e;padding:10px;border-radius:6px;margin-bottom:10px">'
                        f'<span style="color:#6b7099;font-size:12px">'
                        f'Tickers matched: <b style="color:#e8ecff">{_total_n}</b> · '
                        f'Win rate: <b style="color:{"#00e5a0" if _total_wr >= 50 else "#ff4d6a"}">{_total_wr:.1f}%</b> · '
                        f'Avg P&L: <b style="color:{"#00e5a0" if _total_avg >= 0 else "#ff4d6a"}">{_total_avg:+.2f}%</b>'
                        f'</span></div>',
                        unsafe_allow_html=True,
                    )

                    # Grouped summary by Grade + Signal + Action
                    _grp = _wr_all.groupby(["Grade", "Signal", "Action"]).agg(
                        Trades=("Result", "count"),
                        Wins=("Result", lambda x: (x == "WIN").sum()),
                        AvgPnL=("P&L %", "mean"),
                    ).reset_index()
                    _grp["Win %"] = (_grp["Wins"] / _grp["Trades"] * 100).round(1)
                    _grp["Avg P&L %"] = _grp["AvgPnL"].round(2)
                    _grp = _grp.drop(columns=["AvgPnL"]).sort_values("Win %", ascending=False)

                    def _wr_color(val):
                        try:
                            v = float(str(val).replace("%", ""))
                        except (ValueError, TypeError):
                            return ""
                        if v >= 70:
                            return "background-color: #0a3d1f; color: #00e5a0; font-weight: 700"
                        elif v >= 50:
                            return "color: #00e5a0"
                        elif v >= 30:
                            return "color: #f0c040"
                        return "color: #ff4d6a"

                    _grp_styled = _grp.style.applymap(_wr_color, subset=["Win %", "Avg P&L %"])
                    st.dataframe(_grp_styled, use_container_width=True, hide_index=True)

                    # Individual trade details — Ticker as clickable finviz link
                    with st.expander(f"📋 Individual Trades ({_total_n})"):
                        _wr_detail_cols = ["Ticker", "Grade", "Signal", "Action", "Dir", "Entry", "Fri Close", "P&L %", "Result"]
                        _wr_show = _wr_all[[c for c in _wr_detail_cols if c in _wr_all.columns]].copy()
                        _wr_show["Ticker"] = _wr_show["Ticker"].apply(
                            lambda t: f'<a href="https://finviz.com/quote.ashx?t={t}&p=d" target="_blank" '
                                      f'style="color:#4d9fff;text-decoration:none;font-weight:700">{t}</a>'
                        )
                        _wr_show["P&L %"] = _wr_show["P&L %"].apply(
                            lambda v: f'<span style="color:{"#00e5a0" if v > 0 else "#ff4d6a"}">{v:+.2f}%</span>'
                        )
                        _wr_show["Result"] = _wr_show["Result"].apply(
                            lambda v: f'<span style="color:{"#00e5a0" if v == "WIN" else "#ff4d6a"};font-weight:700">{v}</span>'
                        )
                        st.markdown(
                            _wr_show.to_html(escape=False, index=False),
                            unsafe_allow_html=True,
                        )
                else:
                    st.info("No Friday close data matched for scanned tickers.")
            else:
                st.info("Could not retrieve Friday close prices.")
        elif enter_results and not _show_wr_table:
            st.markdown("---")
            st.markdown("### 📊 Weekly Hold Win Rate — by Grade / Signal / Action")
            st.info(f"📅 Friday ({_target_fri}) hasn't passed yet — win rate data available after Friday close.")

        # Save scan results to session state so Macro tab can read them
        st.session_state["_last_scan_results"] = scan_results
        
        # ── Auto-add Rank 1 tickers to intraday planning ────────────────────
        rank1_tickers = [r.get("ticker") for r in enter_results if r.get("mtf_rank") == 1]
        if rank1_tickers:
            _add_scan_tickers_to_intraday(_today_str, rank1_tickers)
            st.toast(f"✅ {len(rank1_tickers)} Rank 1 tickers added to Intraday Planning", icon="✅")
    else:
        st.info("No scan results returned. Check that your API keys are valid and tickers are correct.")
except Exception as _estim_err:
  import traceback; print(f"⚠️ Stock Analysis handler crashed: {_estim_err}\n{traceback.format_exc()}")

# ── Backtest handler removed (tab removed) ──────────────────────────
if False:
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

# ── Date Lookup handler removed (tab removed) ──────────────────────────
if False:
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

# ── Dispatch: shared tab routing for plan / check_open / replay ────────────────
_from_holdings = holdings_check_prices_run or holdings_replay_run or holdings_scan_run
_active_tab = tab_scan_holdings if _from_holdings else tab_plan
_active_tickers_raw = holdings_tickers_raw if _from_holdings else plan_tickers_raw
_active_plan_date = holdings_scan_date if _from_holdings else plan_date
_active_plan_key = "holdings_plan_data" if _from_holdings else "plan_data"

# ── Replay Session handler ─────────────────────────────────
if replay_run or holdings_replay_run:
    # Compute replay date = next trading day after plan date
    _replay_date = _active_plan_date + timedelta(days=1)
    while _replay_date.weekday() >= 5:
        _replay_date += timedelta(days=1)
    # Clear previous session state for a fresh replay
    st.session_state["open_check_table_rows"] = []
    st.session_state["tracking_trades"] = []
    st.session_state["replay_mode"] = True
    st.session_state["replay_date"] = _replay_date
    st.session_state["replay_time_min"] = 8 * 60 + 20  # Start at 8:20 AM CST
    st.session_state["replay_playing"] = False
    st.session_state["replay_speed"] = 10  # minutes per tick

# ── Check Open Prices ──────────────────────────────────────
# Clear replay mode if user clicks Check Open Prices directly (not via replay)
if (check_open_run or holdings_check_prices_run) and not (replay_run or holdings_replay_run):
    st.session_state.pop("replay_mode", None)
    st.session_state.pop("replay_date", None)
    st.session_state.pop("replay_time_min", None)
    st.session_state.pop("replay_playing", None)
    st.session_state.pop("replay_speed", None)

# Auto-trigger at 8 AM–3 PM CST weekdays if plan exists and open check hasn't run yet
_auto_cst_now = get_cst_now()
_auto_check_open = (
    not check_open_run
    and not replay_run
    and _auto_cst_now.weekday() < 5          # Mon-Fri only
    and 8 <= _auto_cst_now.hour < 15          # 8 AM – 3 PM CST only
    and plan_date == date.today()
    and not (
        st.session_state.get("open_check_table_rows")
        and st.session_state.get("_open_check_date") == date.today().isoformat()
    )
    and (st.session_state.get("plan_data") or st.session_state.get("holdings_plan_data") or not missing_creds)
)
_is_replay = replay_run or holdings_replay_run or st.session_state.get("replay_mode", False)
if check_open_run or holdings_check_prices_run or _auto_check_open or replay_run or holdings_replay_run:
 try:
  # Store which tab is active for persisted scenario rendering
  if _from_holdings:
      st.session_state["_check_open_tab"] = "holdings"
  elif check_open_run or replay_run:
      st.session_state["_check_open_tab"] = "intraday"
  with _active_tab:
    saved_plan = st.session_state.get(_active_plan_key, [])
    if not saved_plan:
        # Auto-generate plan if none exists
        # Only auto-generate between 8:00–8:15 AM CST (or always in replay / manual button press)
        _now_cst = get_cst_now()
        _in_auto_plan_window = (8, 0) <= (_now_cst.hour, _now_cst.minute) <= (8, 15)
        _manual_trigger = check_open_run or holdings_check_prices_run
        _should_auto_plan = ((_active_plan_date == date.today() and _in_auto_plan_window) or _is_replay or _manual_trigger) and not missing_creds
        # Compute _prev (plan-from date) before branching so both paths can use it
        if _is_replay:
            _prev = _active_plan_date
        else:
            _prev = _active_plan_date - timedelta(days=1)
            while _prev.weekday() >= 5:
                _prev -= timedelta(days=1)
        if _should_auto_plan:
            st.info(f"⏳ No plan found — auto-generating plan for **{_prev.strftime('%A, %B %d')}**...")
            _auto_tickers = [t.strip().upper() for t in _active_tickers_raw.strip().split(",") if t.strip()]
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
                    # RR ratios for auto-plan
                    def _auto_rr(e, s, t, d):
                        try:
                            risk = (e - s) if d == "LONG" else (s - e)
                            rew  = (t - e) if d == "LONG" else (e - t)
                            return round(rew / risk, 2) if risk > 0 else 0.0
                        except: return 0.0
                    _rr1_ap = _auto_rr(entry, intra_stop, intra_t1, direction)
                    _rr2_ap = _auto_rr(entry, intra_stop, intra_t2, direction)
                    _best_rr_ap = max(_rr1_ap, _rr2_ap)
                    # ── Zone computation for auto-plan ───────────────────
                    _ap_zone_map = {
                        ("HIGH", "HIGH"): "⚠️ Extended — weekly+daily both high. Watch for reversal.",
                        ("HIGH", "LOW"):  "📉 Pulling back within weekly extension. Scalp opportunity.",
                        ("HIGH", "MID"):  "🔄 Weekly extended, daily balanced. Trail stop tight.",
                        ("LOW",  "HIGH"): "🚀 Bouncing off weekly low. Watch for continuation.",
                        ("LOW",  "LOW"):  "🛑 At support — both zones low. Wait for confirmation.",
                        ("LOW",  "MID"):  "⚖️ Weekly at support, daily balanced. Entry zone.",
                        ("MID",  "HIGH"): "📈 Daily extended in mid-range week. Scalp with target.",
                        ("MID",  "LOW"):  "📉 Daily dip in mid-range week. Watch for bounce.",
                        ("MID",  "MID"):  "⚖️ Mid-range both zones — no strong directional edge.",
                    }
                    try:
                        _ap_wk_hi = float(p_daily["high"].iloc[-10:].max())
                        _ap_wk_lo = float(p_daily["low"].iloc[-10:].min())
                        _ap_wk_rng = _ap_wk_hi - _ap_wk_lo
                        _ap_wk_pos = (daily_close - _ap_wk_lo) / _ap_wk_rng * 100 if _ap_wk_rng > 0 else 50.0
                        _ap_weekly_zone = "HIGH" if _ap_wk_pos >= 70 else ("LOW" if _ap_wk_pos <= 30 else "MID")
                    except Exception:
                        _ap_weekly_zone = "N/A"
                    try:
                        _ap_day_hi = float(p_daily["high"].iloc[-1])
                        _ap_day_lo = float(p_daily["low"].iloc[-1])
                        _ap_day_rng = _ap_day_hi - _ap_day_lo
                        _ap_day_pos = (daily_close - _ap_day_lo) / _ap_day_rng * 100 if _ap_day_rng > 0 else 50.0
                        _ap_daily_zone = "HIGH" if _ap_day_pos >= 70 else ("LOW" if _ap_day_pos <= 30 else "MID")
                    except Exception:
                        _ap_daily_zone = "N/A"
                    _ap_zone_conclusion = _ap_zone_map.get(
                        (_ap_weekly_zone, _ap_daily_zone),
                        f"Wk:{_ap_weekly_zone} Day:{_ap_daily_zone}" if _ap_weekly_zone != "N/A" else "N/A",
                    )
                    _auto_plan_rows.append({
                        "Ticker": _aticker, "Direction": direction, "Option": opt_type,
                        "Verdict": verdict, "Confidence": confidence,
                        "Close": round(entry, 2), "ATR": round(atr_1d, 2),
                        "Intra Stop": round(intra_stop, 2), "Intra T1": round(intra_t1, 2), "Intra T2": round(intra_t2, 2),
                        "RR(T1)": f"{_rr1_ap:.2f}x", "RR(T2)": f"{_rr2_ap:.2f}x", "Best RR": f"{_best_rr_ap:.2f}x",
                        "_best_rr_sort": _best_rr_ap,
                        "ATM Strike": round(atm_strike, 2),
                        "0DTE Exp": expiry_0dte, "2-3DTE Exp": expiry_2dte,
                        "Weekly Zone": _ap_weekly_zone,
                        "Daily Zone": _ap_daily_zone,
                        "Zone Conclusion": _ap_zone_conclusion,
                        "If opens near entry": open_above if direction == "LONG" else open_between,
                        "If opens between entry & stop": open_between if direction == "LONG" else open_above,
                        "If opens past stop": open_below_stop, "If big gap": big_gap,
                    })
                    # Alpaca options chain (auto-plan block 1)
                    _apr = _auto_plan_rows[-1]
                    _ap_dz = _apr.get("Daily Zone", "MID")
                    _ap_dir = _apr["Direction"]
                    _ap_atm = round(atm_strike)
                    _ap_alp_text = "N/A"
                    try:
                        _ap_alp = get_options_strategy_alpaca(
                            _aticker, _apr["Close"], _ap_dir, _ap_dz, api_key, api_secret)
                        if _ap_alp and _ap_alp.get("summary"):
                            _ap_alp_text = _ap_alp["summary"]
                            if _ap_alp.get("alt"):
                                _ap_alp_text += f" | {_ap_alp['alt']}"
                    except Exception:
                        pass
                    _apr["Alpaca Options"] = _ap_alp_text
                    # Options strategy based on Daily Zone (auto-plan block 1)
                    if _ap_dz == "LOW" and _ap_dir == "LONG":
                        _apr["Options Strategy"] = (
                            f"📈 {_aticker} Bull Call Spread — Buy ${_ap_atm - round(strike_inc)} Call / Sell ${_ap_atm} Call "
                            f"Exp {expiry_0dte} | Alt: Buy ${_ap_atm} Call Exp {expiry_2dte}")
                    elif _ap_dz == "HIGH" and _ap_dir == "SHORT":
                        _apr["Options Strategy"] = (
                            f"📉 {_aticker} Bear Put Spread — Buy ${_ap_atm + round(strike_inc)} Put / Sell ${_ap_atm} Put "
                            f"Exp {expiry_0dte} | Alt: Buy ${_ap_atm} Put Exp {expiry_2dte}")
                    elif _ap_dz == "HIGH" and _ap_dir == "LONG":
                        _apr["Options Strategy"] = (
                            f"⚠️ {_aticker} Caution — Daily HIGH + LONG: "
                            f"Buy ${_ap_atm} Call / Sell ${_ap_atm + round(strike_inc)} Call Exp {expiry_2dte} (hedged)")
                    elif _ap_dz == "LOW" and _ap_dir == "SHORT":
                        _apr["Options Strategy"] = (
                            f"⚠️ {_aticker} Caution — Daily LOW + SHORT: "
                            f"Buy ${_ap_atm} Put / Sell ${_ap_atm - round(strike_inc)} Put Exp {expiry_2dte} (hedged)")
                    else:
                        _apr["Options Strategy"] = (
                            f"🦋 {_aticker} Iron Butterfly — Sell ${_ap_atm} Call+Put / "
                            f"Buy ${_ap_atm + round(strike_inc)} Call + Buy ${_ap_atm - round(strike_inc)} Put Exp {expiry_2dte} "
                            f"| Alt: Buy Straddle ${_ap_atm} Call+Put Exp {expiry_0dte}")
                except:
                    continue
            _auto_progress.empty()
            if _auto_plan_rows:
                _auto_plan_rows.sort(key=lambda x: x.get("_best_rr_sort", 0), reverse=True)
                st.session_state[_active_plan_key] = _auto_plan_rows
                saved_plan = _auto_plan_rows
                st.success(f"✅ Auto-generated plan for {_prev.strftime('%m/%d')} with {len(_auto_plan_rows)} ticker(s)")
            else:
                st.warning("Could not auto-generate plan. Try generating manually.")
        else:
            # User clicked CHECK OPEN PRICES with no plan — auto-generate now
            st.info(f"⏳ No plan found — auto-generating plan for **{_prev.strftime('%A, %B %d')}**...")
            _auto_tickers = [t.strip().upper() for t in _active_tickers_raw.strip().split(",") if t.strip()]
            _auto_plan_rows = []
            _auto_progress_temp = st.progress(0)
            for _ai, _aticker in enumerate(_auto_tickers):
                _auto_progress_temp.progress((_ai + 1) / len(_auto_tickers))
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
                    sig_full = _estimator_signal_at(p_daily, len(p_daily) - 1, use_fib=use_fib, fib_tol=fib_tol, ticker="")
                    if sig_full is not None:
                        verdict    = sig_full["verdict"]
                        confidence = sig_full["confidence"]
                        score      = sig_full["score"]
                        signals    = sig_full["signals"]
                    else:
                        verdict    = "LEAN BULLISH" if direction == "LONG" else "LEAN BEARISH"
                        confidence = "LOW"
                        score      = 2 if direction == "LONG" else -2
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
                    def _next_trading_day_temp(d, skip=1):
                        result = d
                        added = 0
                        while added < skip:
                            result += timedelta(days=1)
                            if result.weekday() < 5:
                                added += 1
                        return result
                    next_trade   = _next_trading_day_temp(p_end, 1)
                    trade_plus2  = _next_trading_day_temp(p_end, 3)
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
                    # RR ratios for auto-plan (temp)
                    def _auto_rr_t(e, s, t, d):
                        try:
                            risk = (e - s) if d == "LONG" else (s - e)
                            rew  = (t - e) if d == "LONG" else (e - t)
                            return round(rew / risk, 2) if risk > 0 else 0.0
                        except: return 0.0
                    _rr1_apt = _auto_rr_t(entry, intra_stop, intra_t1, direction)
                    _rr2_apt = _auto_rr_t(entry, intra_stop, intra_t2, direction)
                    _best_rr_apt = max(_rr1_apt, _rr2_apt)
                    # ── Zone computation for auto-plan (temp) ────────────
                    _apt_zone_map = {
                        ("HIGH", "HIGH"): "⚠️ Extended — weekly+daily both high. Watch for reversal.",
                        ("HIGH", "LOW"):  "📉 Pulling back within weekly extension. Scalp opportunity.",
                        ("HIGH", "MID"):  "🔄 Weekly extended, daily balanced. Trail stop tight.",
                        ("LOW",  "HIGH"): "🚀 Bouncing off weekly low. Watch for continuation.",
                        ("LOW",  "LOW"):  "🛑 At support — both zones low. Wait for confirmation.",
                        ("LOW",  "MID"):  "⚖️ Weekly at support, daily balanced. Entry zone.",
                        ("MID",  "HIGH"): "📈 Daily extended in mid-range week. Scalp with target.",
                        ("MID",  "LOW"):  "📉 Daily dip in mid-range week. Watch for bounce.",
                        ("MID",  "MID"):  "⚖️ Mid-range both zones — no strong directional edge.",
                    }
                    try:
                        _apt_wk_hi = float(p_daily["high"].iloc[-10:].max())
                        _apt_wk_lo = float(p_daily["low"].iloc[-10:].min())
                        _apt_wk_rng = _apt_wk_hi - _apt_wk_lo
                        _apt_wk_pos = (daily_close - _apt_wk_lo) / _apt_wk_rng * 100 if _apt_wk_rng > 0 else 50.0
                        _apt_weekly_zone = "HIGH" if _apt_wk_pos >= 70 else ("LOW" if _apt_wk_pos <= 30 else "MID")
                    except Exception:
                        _apt_weekly_zone = "N/A"
                    try:
                        _apt_day_hi = float(p_daily["high"].iloc[-1])
                        _apt_day_lo = float(p_daily["low"].iloc[-1])
                        _apt_day_rng = _apt_day_hi - _apt_day_lo
                        _apt_day_pos = (daily_close - _apt_day_lo) / _apt_day_rng * 100 if _apt_day_rng > 0 else 50.0
                        _apt_daily_zone = "HIGH" if _apt_day_pos >= 70 else ("LOW" if _apt_day_pos <= 30 else "MID")
                    except Exception:
                        _apt_daily_zone = "N/A"
                    _apt_zone_conclusion = _apt_zone_map.get(
                        (_apt_weekly_zone, _apt_daily_zone),
                        f"Wk:{_apt_weekly_zone} Day:{_apt_daily_zone}" if _apt_weekly_zone != "N/A" else "N/A",
                    )
                    _auto_plan_rows.append({
                        "Ticker": _aticker, "Direction": direction, "Option": opt_type,
                        "Verdict": verdict, "Confidence": confidence,
                        "Close": round(entry, 2), "ATR": round(atr_1d, 2),
                        "Intra Stop": round(intra_stop, 2), "Intra T1": round(intra_t1, 2), "Intra T2": round(intra_t2, 2),
                        "RR(T1)": f"{_rr1_apt:.2f}x", "RR(T2)": f"{_rr2_apt:.2f}x", "Best RR": f"{_best_rr_apt:.2f}x",
                        "_best_rr_sort": _best_rr_apt,
                        "ATM Strike": round(atm_strike, 2),
                        "0DTE Exp": expiry_0dte, "2-3DTE Exp": expiry_2dte,
                        "Weekly Zone": _apt_weekly_zone,
                        "Daily Zone": _apt_daily_zone,
                        "Zone Conclusion": _apt_zone_conclusion,
                        "If opens near entry": open_above if direction == "LONG" else open_between,
                        "If opens between entry & stop": open_between if direction == "LONG" else open_above,
                        "If opens past stop": open_below_stop, "If big gap": big_gap,
                    })
                    # Alpaca options chain (auto-plan block 2)
                    _apt_r = _auto_plan_rows[-1]
                    _apt_dz = _apt_r.get("Daily Zone", "MID")
                    _apt_dir = _apt_r["Direction"]
                    _apt_atm = round(atm_strike)
                    _apt_alp_text = "N/A"
                    try:
                        _apt_alp = get_options_strategy_alpaca(
                            _aticker, _apt_r["Close"], _apt_dir, _apt_dz, api_key, api_secret)
                        if _apt_alp and _apt_alp.get("summary"):
                            _apt_alp_text = _apt_alp["summary"]
                            if _apt_alp.get("alt"):
                                _apt_alp_text += f" | {_apt_alp['alt']}"
                    except Exception:
                        pass
                    _apt_r["Alpaca Options"] = _apt_alp_text
                    # Options strategy based on Daily Zone (auto-plan block 2)
                    if _apt_dz == "LOW" and _apt_dir == "LONG":
                        _apt_r["Options Strategy"] = (
                            f"📈 {_aticker} Bull Call Spread — Buy ${_apt_atm - round(strike_inc)} Call / Sell ${_apt_atm} Call "
                            f"Exp {expiry_0dte} | Alt: Buy ${_apt_atm} Call Exp {expiry_2dte}")
                    elif _apt_dz == "HIGH" and _apt_dir == "SHORT":
                        _apt_r["Options Strategy"] = (
                            f"📉 {_aticker} Bear Put Spread — Buy ${_apt_atm + round(strike_inc)} Put / Sell ${_apt_atm} Put "
                            f"Exp {expiry_0dte} | Alt: Buy ${_apt_atm} Put Exp {expiry_2dte}")
                    elif _apt_dz == "HIGH" and _apt_dir == "LONG":
                        _apt_r["Options Strategy"] = (
                            f"⚠️ {_aticker} Caution — Daily HIGH + LONG: "
                            f"Buy ${_apt_atm} Call / Sell ${_apt_atm + round(strike_inc)} Call Exp {expiry_2dte} (hedged)")
                    elif _apt_dz == "LOW" and _apt_dir == "SHORT":
                        _apt_r["Options Strategy"] = (
                            f"⚠️ {_aticker} Caution — Daily LOW + SHORT: "
                            f"Buy ${_apt_atm} Put / Sell ${_apt_atm - round(strike_inc)} Put Exp {expiry_2dte} (hedged)")
                    else:
                        _apt_r["Options Strategy"] = (
                            f"🦋 {_aticker} Iron Butterfly — Sell ${_apt_atm} Call+Put / "
                            f"Buy ${_apt_atm + round(strike_inc)} Call + Buy ${_apt_atm - round(strike_inc)} Put Exp {expiry_2dte} "
                            f"| Alt: Buy Straddle ${_apt_atm} Call+Put Exp {expiry_0dte}")
                except:
                    continue
            _auto_progress_temp.empty()
            if _auto_plan_rows:
                _auto_plan_rows.sort(key=lambda x: x.get("_best_rr_sort", 0), reverse=True)
                st.session_state[_active_plan_key] = _auto_plan_rows
                saved_plan = _auto_plan_rows
                st.success(f"✅ Auto-generated plan for {_prev.strftime('%m/%d')} with {len(_auto_plan_rows)} ticker(s)")
                st.rerun()
            else:
                st.warning("Could not auto-generate plan. Try generating manually.")
    if saved_plan and not YFINANCE_AVAILABLE:
        st.error("yfinance is required for live price checks.")
    elif saved_plan:
        # In replay mode, use the replay date instead of _active_plan_date
        if _is_replay and st.session_state.get("replay_date"):
            check_date = st.session_state["replay_date"]
            is_live = False
            st.info(f"🔁 **REPLAY MODE** — Simulating session for **{check_date.strftime('%A, %B %d, %Y')}**")
        else:
            check_date = _active_plan_date
            is_live    = (check_date == date.today())
        
        # Add toggle to skip options data
        skip_options = st.checkbox("⏭️ Skip options data (faster)", value=False, 
                                   help="Disable options chain fetching to speed up price checks")
        
        actual_trade_date = None
        open_status = st.empty()
        fetch_errors = []
        fetch_success = []
        shown = 0
        table_rows = []  # Collect all scenario results for table
        
        status_cols = st.columns([1, 2])
        with status_cols[0]:
            skip_indicator = st.empty()
        with status_cols[1]:
            progress_text = st.empty()

        for i, r in enumerate(saved_plan):
            if not isinstance(r, dict):
                continue
            ticker = r.get("Ticker") or r.get("ticker", "UNKNOWN")
            
            # Update status indicators
            skip_indicator.info(f"⏭️ Skip options: {'ON' if skip_options else 'OFF'}")
            progress_text.text(f"📊 {shown}/{i} displayed • Fetching {ticker}... ({i+1}/{len(saved_plan)})")
            
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

                # Fetch live price via yfinance (with timeout handling)
                import signal
                def _timeout_handler(signum, frame):
                    raise TimeoutError(f"Timeout fetching {ticker} data")
                
                tk = yf.Ticker(ticker)
                today_hist = None

                # Build resilient fetch windows for live/replay/backdated checks.
                # Backdated dates can land on weekends/holidays, so we probe nearby windows.
                fetch_windows = []
                if is_live:
                    fetch_windows = [{"period": "1d"}, {"period": "5d"}]
                elif _is_replay:
                    fetch_windows = [
                        {"start": str(check_date), "end": str(check_date + timedelta(days=1))},
                        {"start": str(check_date - timedelta(days=2)), "end": str(check_date + timedelta(days=2))},
                    ]
                else:
                    # Backdated mode: target date might be weekend/holiday, so expand windows
                    fetch_windows = [
                        # 1. Exact date (if trading day)
                        {"start": str(check_date), "end": str(check_date + timedelta(days=1))},
                        # 2. ±5 days (catches nearby trading day)
                        {"start": str(check_date - timedelta(days=5)), "end": str(check_date + timedelta(days=5))},
                        # 3. ±10 days (wider window, def has data)
                        {"start": str(check_date - timedelta(days=10)), "end": str(check_date + timedelta(days=10))},
                        # 4. Last 6 months (fallback everything)
                        {"period": "6mo"},
                    ]

                try:
                    for _win in fetch_windows:
                        if "period" in _win:
                            _hist = tk.history(period=_win["period"])
                        else:
                            _hist = tk.history(start=_win["start"], end=_win["end"])
                        if _hist is not None and not _hist.empty:
                            today_hist = _hist
                            break
                except TimeoutError as te:
                    fetch_errors.append(f"{ticker}: timeout fetching stock data")
                    print(f"⏱️ {ticker}: {te}")
                    continue
                except Exception as e:
                    print(f"📡 {ticker} data fetch error: {type(e).__name__}: {e}")

                if today_hist is None or (hasattr(today_hist, "empty") and today_hist.empty):
                    fetch_errors.append(
                        f"{ticker}: no data for {check_date} (weekend/holiday/invalid ticker)"
                    )
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

                # ── Previous day OHLC average vs today's open ──
                prev_avg_ohlc = None
                ohlc_signal = "N/A"
                try:
                    # Fetch 10 trading days to reliably get previous day
                    _prev_hist = tk.history(
                        start=str(check_date - timedelta(days=10)),
                        end=str(check_date),
                    )
                    if _prev_hist is not None and len(_prev_hist) >= 1:
                        _pd_o = _extract_price(_prev_hist, "Open",  -1)
                        _pd_h = _extract_price(_prev_hist, "High",  -1)
                        _pd_l = _extract_price(_prev_hist, "Low",   -1)
                        _pd_c = _extract_price(_prev_hist, "Close", -1)
                        prev_avg_ohlc = round((_pd_o + _pd_h + _pd_l + _pd_c) / 4, 2)
                        if abs(prev_avg_ohlc - open_price) < 0.005:
                            ohlc_signal = "NEUTRAL"
                        elif prev_avg_ohlc > open_price:
                            ohlc_signal = "🐻 BEAR"
                        else:
                            ohlc_signal = "🐂 BULL"
                except Exception:
                    pass

                # Fetch option data for the strike
                opt_prev_open = opt_prev_high = opt_prev_low = opt_prev_close = opt_curr_open = None
                if not skip_options:
                    try:
                        # Try yfinance first
                        stock = yf.Ticker(ticker)
                        expirations = stock.options
                        if expirations:
                            exp_date = expirations[0]
                            opt_chain = stock.option_chain(exp_date)
                            calls = opt_chain.calls if opt_type == "CALL" else opt_chain.puts
                            calls = calls.sort_values(by='strike')
                            closest_strike = calls.iloc[(calls['strike'] - atm_strike).abs().argsort()[0]]
                            if not closest_strike.empty:
                                opt_curr_open = closest_strike.get('lastPrice', None)
                                opt_prev_open = opt_curr_open
                                opt_prev_close = opt_curr_open
                    except Exception as opt_err:
                        print(f"📊 {ticker} yfinance options error: {type(opt_err).__name__}: {opt_err}")
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
                        except Exception as alp_err:
                            print(f"📊 {ticker} Alpaca options fallback error: {type(alp_err).__name__}")
                else:
                    print(f"⏭️ {ticker}: skipping options data per user request")

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
                fetch_success.append(ticker)
                progress_text.text(f"✅ {shown}/{i+1} displayed • {ticker}")

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

                # Calculate RR ratios (moved up for card display)
                def _calc_table_rr(entry_val, stop_val, target_val, direction_val):
                    try:
                        if direction_val == "LONG":
                            risk = entry_val - stop_val
                            reward = target_val - entry_val
                        else:
                            risk = stop_val - entry_val
                            reward = entry_val - target_val
                        return round(reward / risk, 2) if risk > 0 else 0.0
                    except: return 0.0
                rr_t1_calc = _calc_table_rr(entry, ds, dt1, dd)
                rr_t2_calc = _calc_table_rr(entry, ds, dt2, dd)
                best_rr_calc = max(rr_t1_calc, rr_t2_calc)

                # Fetch weekly Fib level (based on Weekly H/L) & 4H WTD RSI
                _wk_fib, _4h_rsi = get_weekly_fib_and_4h_rsi(tk, check_date, current_price)

                # Pull Weekly/Daily Zone from the pre-computed plan (static, no refresh needed)
                _weekly_zone = r.get("Weekly Zone", "N/A")
                _daily_zone = r.get("Daily Zone", "N/A")
                _zone_conclusion = r.get("Zone Conclusion", "N/A")

                with _check_open_expander:
                    _ohlc_info = f'Prev OHLC Avg: <b style="color:#c0a0ff">${prev_avg_ohlc:.2f}</b> → <b>{ohlc_signal}</b>' if prev_avg_ohlc else 'Prev OHLC Avg: N/A'
                    st.markdown(
                        f'<div style="font-size:10px;color:#6b7099;padding:4px 18px 0">'
                        f'ATR: <b style="color:#a78bfa">${atr_val:.2f}</b> &nbsp;·&nbsp; '
                        f'Stop: ${ds:.2f} &nbsp;·&nbsp; T1: ${dt1:.2f} &nbsp;·&nbsp; T2: ${dt2:.2f} &nbsp;·&nbsp; '
                        f'RR: <b style="color:#f0c040">{rr_t1_calc:.2f}x / {rr_t2_calc:.2f}x (Best: {best_rr_calc:.2f}x)</b> &nbsp;·&nbsp; '
                        f'ATM: <b style="color:{dc}">${da:.0f} {dopt}</b> &nbsp;·&nbsp; Exp: {exp_0dte} / {exp_2dte} &nbsp;·&nbsp; '
                        f'{_ohlc_info}</div>'
                        f'<div style="font-size:11px;font-weight:600;color:#4d9fff;padding:3px 18px 12px">'
                        f'{ticker} &nbsp; {dopt} SPREAD &nbsp; {_spread} &nbsp; Exp: {exp_0dte} / {exp_2dte}'
                        f'</div>',
                        unsafe_allow_html=True,
                    )

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
                    "Prev OHLC Avg": f"${prev_avg_ohlc:.2f}" if prev_avg_ohlc else "N/A",
                    "OHLC Signal": ohlc_signal,
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
                    "Notes": txt,  # Store full notes (untruncated) for gap entry extraction
                    "Wk Fib": _wk_fib,
                    "4H RSI(WTD)": _4h_rsi,
                    "Weekly Zone": _weekly_zone,
                    "Daily Zone": _daily_zone,
                    "Zone Conclusion": _zone_conclusion,
                    "Options Strategy": r.get("Options Strategy", "N/A"),
                    "_scenario_id": sc,
                    "_best_rr_sort_main": best_rr_calc,
                })
            except Exception as exc:
                import traceback
                _tb_lines = traceback.format_exc().strip().split("\n")
                _tb_last = _tb_lines[-3] if len(_tb_lines) >= 3 else ""
                fetch_errors.append(f"{ticker}: {type(exc).__name__}: {exc} | {_tb_last.strip()}")

        progress_text.empty()
        skip_indicator.empty()
        
        # ── Summary: show tickers processed vs displayed ──
        total_plan = len([r for r in saved_plan if isinstance(r, dict)])
        missing_tickers = [r.get("Ticker") or r.get("ticker", "?") for r in saved_plan 
                          if isinstance(r, dict) and (r.get("Ticker") or r.get("ticker")) not in fetch_success]
        
        st.markdown("---")
        summary_col1, summary_col2, summary_col3 = st.columns(3)
        with summary_col1:
            st.metric("✅ Successful", len(fetch_success))
        with summary_col2:
            st.metric("❌ Failed", len(fetch_errors))
        with summary_col3:
            st.metric("📊 Skip Options", "ON" if skip_options else "OFF")
        
        if shown == 0 and not fetch_errors:
            st.info("No data returned — market may not be open yet, or try unchecking 'Skip options data'.")
        
        if fetch_errors:
            with st.expander(f"⚠️ {len(fetch_errors)} issue(s) — hidden debug details", expanded=False):
                st.markdown("**Failed Tickers:**")
                for e in fetch_errors:
                    st.code(e, language="text")
                if missing_tickers:
                    st.markdown(f"**Summary:** Missing {len(missing_tickers)} tickers: {', '.join(missing_tickers)}")
        
        if fetch_success:
            st.caption(f"✅ Successfully displayed: {', '.join(fetch_success)}")

        if not table_rows and shown == 0:
            st.error("❌ No scenarios could be classified. Check the hidden debug section above.")

        # Persist table_rows so they survive st.rerun() after track-button clicks
        if table_rows:
            st.session_state["open_check_table_rows"] = table_rows
            st.session_state["_open_check_date"] = date.today().isoformat()

            # ── Telegram: 8:00 AM Check Open Price summary (once per day) ──
            _open_tg_key = f"_open_check_tg_sent_{date.today()}"
            if not st.session_state.get(_open_tg_key, False) and TELEGRAM_ENABLED and not _in_telegram_quiet_hours() and not st.session_state.get("replay_mode", False):
                try:
                    _open_lines = ["☀️ <b>8:00 AM — CHECK OPEN PRICES</b>\n"]
                    _near = [r for r in table_rows if "OPENS NEAR ENTRY" in (r.get("Scenario") or "").upper()]
                    _btwn = [r for r in table_rows if "OPENS BETWEEN" in (r.get("Scenario") or "").upper()]
                    _past = [r for r in table_rows if "OPENS PAST STOP" in (r.get("Scenario") or "").upper()]
                    _gaps = [r for r in table_rows if "GAP" in (r.get("Scenario") or "").upper()]
                    for _grp_name, _grp in [("✅ NEAR ENTRY", _near), ("⚡ BETWEEN", _btwn), ("🛑 PAST STOP", _past), ("📊 GAP", _gaps)]:
                        if _grp:
                            _open_lines.append(f"<b>{_grp_name} ({len(_grp)}):</b>")
                            for _t in _grp:
                                _d_icon = "🟢" if "LONG" in str(_t.get("Direction", "")).upper() else "🔴"
                                _open_lines.append(
                                    f"  {_d_icon} <b>{_t.get('Ticker','?')}</b> {_t.get('Direction','')} "
                                    f"| Open:{_t.get('Open','?')} S:{_t.get('Stop','?')} T1:{_t.get('T1','?')} T2:{_t.get('T2','?')}"
                                )
                    _open_lines.append(f"\n📊 Total: {len(table_rows)} scenario(s)")
                    _open_msg = "\n".join(_open_lines)
                    _bot_token = st.session_state.get("_telegram_bot_token", TELEGRAM_BOT_TOKEN)
                    _chat_id = st.session_state.get("_telegram_chat_id", TELEGRAM_CHAT_ID)
                    _url = f"https://api.telegram.org/bot{_bot_token}/sendMessage"
                    requests.post(_url, json={"chat_id": _chat_id, "text": _open_msg, "parse_mode": "HTML"}, timeout=5)
                    st.session_state[_open_tg_key] = True
                except Exception as _tge:
                    print(f"⚠️ Open check telegram error: {_tge}")
 except Exception as _check_open_err:
  import traceback; print(f"⚠️ Check-open handler crashed: {_check_open_err}\n{traceback.format_exc()}")

# ── Display persisted scenario tables & tracker (survives rerun) ──
try:
 if st.session_state.get("open_check_table_rows"):
  _scenario_tab = tab_scan_holdings if st.session_state.get("_check_open_tab") == "holdings" else tab_plan
  with _scenario_tab:
    table_rows = st.session_state["open_check_table_rows"]
    st.markdown("---")

    # Replay time helper
    _replay_time_min = st.session_state.get("replay_time_min", 500)
    def _fmt_replay_time(m=None):
        mins = m if m is not None else _replay_time_min
        h, mi = divmod(mins, 60)
        ampm = "AM" if h < 12 else "PM"
        h12 = h if h <= 12 else h - 12
        return f"{h12}:{mi:02d} {ampm} CST"

    def _replay_clock(stage_min, label):
        """Show a time marker. stage_min = minutes-since-midnight for this stage."""
        if not st.session_state.get("replay_mode"):
            return
        reached = _replay_time_min >= stage_min
        color = "#00e5a0" if reached else "#4a4a6a"
        icon = "✅" if reached else "⏳"
        h, mi = divmod(stage_min, 60)
        ampm = "AM" if h < 12 else "PM"
        h12 = h if h <= 12 else h - 12
        time_str = f"{h12}:{mi:02d} {ampm} CST"
        st.markdown(
            f'<div style="display:flex;align-items:center;gap:10px;padding:6px 12px;'
            f'background:linear-gradient(90deg,#1a1a2e,#16213e);border-left:3px solid {color};'
            f'border-radius:4px;margin:8px 0;opacity:{"1" if reached else "0.5"}">'
            f'<span style="font-size:16px">{icon}</span>'
            f'<span style="font-family:monospace;font-size:18px;color:{color};font-weight:700">{time_str}</span>'
            f'<span style="color:#8892b0;font-size:13px">│</span>'
            f'<span style="color:{"#ccd6f6" if reached else "#5a5a7a"};font-size:13px">{label}</span></div>',
            unsafe_allow_html=True,
        )

    # Show replay banner with time controller
    if st.session_state.get("replay_mode"):
        _rp_date = st.session_state.get("replay_date")
        _rp_playing = st.session_state.get("replay_playing", False)
        _rp_speed = st.session_state.get("replay_speed", 10)
        _market_open = 8 * 60 + 20   # 8:20 AM CST
        _market_close = 15 * 60      # 3:00 PM CST

        # ── Auto-advance fragment (only when playing) ──
        if _rp_playing and _replay_time_min < _market_close:
            @st.fragment(run_every=timedelta(seconds=3))
            def _replay_auto_tick():
                if st.session_state.get("_debug_fragments"):
                    print(f"[FRAGMENT] _replay_auto_tick (3s) fired at {datetime.now().strftime('%H:%M:%S')}")
                _cur = st.session_state.get("replay_time_min", _market_open)
                _spd = st.session_state.get("replay_speed", 10)
                if st.session_state.get("replay_playing") and _cur < _market_close:
                    st.session_state["replay_time_min"] = min(_cur + _spd, _market_close)
                    if st.session_state["replay_time_min"] >= _market_close:
                        st.session_state["replay_playing"] = False
            _replay_auto_tick()

        # ── Header ──
        st.markdown(
            f'<div style="background:linear-gradient(90deg,#0a0a1a,#1a1a3e);padding:12px 16px;'
            f'border-radius:8px;border:1px solid #2a2a4a;margin-bottom:8px">'
            f'<div style="display:flex;align-items:center;justify-content:space-between">'
            f'<div><span style="font-size:14px;color:#8892b0">🔁 REPLAY</span> '
            f'<span style="font-size:14px;color:#ccd6f6;font-weight:600">{_rp_date.strftime("%A, %B %d, %Y") if _rp_date else "N/A"}</span></div>'
            f'<div style="font-family:monospace;font-size:36px;font-weight:700;color:#4d9fff;letter-spacing:2px">'
            f'{"▶" if _rp_playing else "⏸"} {_fmt_replay_time()}</div>'
            f'</div></div>',
            unsafe_allow_html=True,
        )

        # ── Time slider ──
        _slider_val = st.slider(
            "Session Time",
            min_value=_market_open,
            max_value=_market_close,
            value=_replay_time_min,
            step=5,
            format="%d",
            key="rp_slider",
            label_visibility="collapsed",
        )
        # If user dragged the slider, update time and pause
        if _slider_val != _replay_time_min:
            st.session_state["replay_time_min"] = _slider_val
            st.session_state["replay_playing"] = False
            st.rerun()

        # ── Time markers along the bar ──
        _t_labels = st.columns(8)
        _markers = [
            (8*60+20, "8:20"), (8*60+30, "8:30"), (9*60, "9:00"), (9*60+30, "9:30"),
            (10*60, "10:00"), (11*60, "11:00"), (13*60, "1:00p"), (15*60, "3:00p"),
        ]
        for i, (mmin, mlbl) in enumerate(_markers):
            _reached = _replay_time_min >= mmin
            _clr = "#00e5a0" if _reached else "#4a4a6a"
            _t_labels[i].markdown(
                f'<div style="text-align:center;font-family:monospace;font-size:11px;color:{_clr};'
                f'font-weight:{"700" if _reached else "400"}">{"●" if _reached else "○"} {mlbl}</div>',
                unsafe_allow_html=True,
            )

        # ── Control buttons ──
        _c1, _c2, _c3, _c4, _c5, _c6, _c7, _c8 = st.columns([1, 1, 1, 1, 1, 1, 1, 1])
        with _c1:
            if _rp_playing:
                if st.button("⏸ Pause", key="rp_pause", use_container_width=True):
                    st.session_state["replay_playing"] = False
                    st.rerun()
            else:
                if st.button("▶ Play", key="rp_play", use_container_width=True, type="primary"):
                    st.session_state["replay_playing"] = True
                    st.rerun()
        with _c2:
            if st.button("⏭ +10m", key="rp_fwd_10", use_container_width=True):
                st.session_state["replay_playing"] = False
                st.session_state["replay_time_min"] = min(_replay_time_min + 10, _market_close)
                st.rerun()
        with _c3:
            if st.button("⏭ +30m", key="rp_fwd_30", use_container_width=True):
                st.session_state["replay_playing"] = False
                st.session_state["replay_time_min"] = min(_replay_time_min + 30, _market_close)
                st.rerun()
        with _c4:
            if st.button("⏭ +1h", key="rp_fwd_60", use_container_width=True):
                st.session_state["replay_playing"] = False
                st.session_state["replay_time_min"] = min(_replay_time_min + 60, _market_close)
                st.rerun()
        with _c5:
            if st.button("⏩ Close", key="rp_eod", use_container_width=True):
                st.session_state["replay_playing"] = False
                st.session_state["replay_time_min"] = _market_close
                st.rerun()
        with _c6:
            if st.button("⏪ Reset", key="rp_reset", use_container_width=True):
                st.session_state["replay_playing"] = False
                st.session_state["replay_time_min"] = _market_open
                st.rerun()
        with _c7:
            _speed_opts = {5: "5m", 10: "10m", 30: "30m"}
            _speed_label = f"⚡{_speed_opts.get(_rp_speed, f'{_rp_speed}m')}/tick"
            if st.button(_speed_label, key="rp_speed_toggle", use_container_width=True):
                _speeds = [5, 10, 30]
                _next_idx = (_speeds.index(_rp_speed) + 1) % len(_speeds) if _rp_speed in _speeds else 0
                st.session_state["replay_speed"] = _speeds[_next_idx]
                st.rerun()
        with _c8:
            if st.button("❌ Exit", key="btn_exit_replay", use_container_width=True):
                st.session_state.pop("replay_mode", None)
                st.session_state.pop("replay_date", None)
                st.session_state.pop("replay_time_min", None)
                st.session_state.pop("replay_playing", None)
                st.session_state.pop("replay_speed", None)
                st.session_state["open_check_table_rows"] = []
                st.session_state["tracking_trades"] = []
                st.rerun()

    _replay_clock(8 * 60 + 20, "Pre-market — Fetching open prices & classifying scenarios")

    conf_priority = {"HIGH": 0, "MEDIUM": 1, "LOW": 2}
    near_entry_rows = [r for r in table_rows if r.get("Scenario", "").strip().startswith("✅ OPENS NEAR ENTRY")]
    
    # Extract gap scenarios - check multiple field patterns
    gap_rows = []
    for r in table_rows:
        scenario = r.get("Scenario", "")
        # Check for both emoji format and text format
        if "GAP" in scenario.upper():
            gap_rows.append(r)
    
    # BIG GAP scenarios as "secondary tracking candidates" at 8:30 AM
    secondary_tracking_candidates = gap_rows
    
    # Debug: show what was extracted
    if gap_rows:
        st.caption(f"✅ Found {len(gap_rows)} gap scenario(s)")
    else:
        st.caption(f"ℹ️ No gap scenarios found (total scenarios: {len(table_rows)})")
    _in_replay = st.session_state.get("replay_mode", False)
    if _in_replay:
        pre_confirmation_enabled = _replay_time_min >= 8 * 60  # 8:00 AM+
        pre_confirmation_live = 8 * 60 <= _replay_time_min < 8 * 60 + 30  # 8:00–8:29 = live refresh
        auto_track_enabled = _replay_time_min >= 8 * 60 + 30       # 8:30 AM
        early_confirmation_enabled = _replay_time_min >= 8 * 60 + 30  # 8:30 AM
        entry_confirmation_enabled = _replay_time_min >= 9 * 60       # 9:00 AM
    else:
        pre_confirmation_enabled = is_after_market_time(8, 0)  # Show from 8:00 AM onward
        pre_confirmation_live = is_after_market_time(8, 0) and not is_after_market_time(8, 30)  # Auto-refresh only 8:00-8:29
        auto_track_enabled = is_after_market_time(8, 30)
        entry_confirmation_enabled = is_after_market_time(9, 0)
        early_confirmation_enabled = is_after_market_time(8, 30)

    # Ensure tracking list exists
    if "tracking_trades" not in st.session_state:
        st.session_state["tracking_trades"] = []

    # ── AUTO-CLEAR tracking table at 8:00 AM CST each day ──────────────────
    _now_cst = get_cst_now()
    _clear_key = "tracking_last_clear_date"
    _last_clear = st.session_state.get(_clear_key)
    if not _in_replay and _now_cst.hour >= 8 and _last_clear != _now_cst.date():
        if st.session_state["tracking_trades"]:
            print(f"🗑️ Auto-clearing {len(st.session_state['tracking_trades'])} tracked trades at 8:00 AM CST")
            st.session_state["tracking_trades"] = []
        st.session_state[_clear_key] = _now_cst.date()

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

        # ── Auto-fix: ensure Stop/T1/T2 align with direction relative to entry ──
        # LONG: Stop < Entry < T1 < T2   |   SHORT: Stop > Entry > T1 > T2
        if entry > 0:
            _vals = sorted([v for v in [stop, t1, t2] if v > 0])
            if len(_vals) == 3:
                if dirn == "LONG":
                    _below = [v for v in _vals if v < entry]
                    _above = [v for v in _vals if v >= entry]
                    if not _below:
                        _below = [round(2 * entry - _vals[2], 2)]
                        _above = _vals[:2]
                    if len(_above) < 2:
                        _above.append(round(2 * entry - _below[0], 2))
                        _above.sort()
                    _below.sort(); _above.sort()
                    stop = _below[-1]; t1 = _above[0]; t2 = _above[-1] if len(_above) > 1 else _above[0]
                else:  # SHORT
                    _above = [v for v in _vals if v > entry]
                    _below = [v for v in _vals if v <= entry]
                    if not _above:
                        _above = [round(2 * entry - _vals[0], 2)]
                        _below = _vals[1:]
                    if len(_below) < 2:
                        _below.append(round(2 * entry - _above[-1], 2))
                        _below.sort(reverse=True)
                    _above.sort(reverse=True); _below.sort(reverse=True)
                    stop = _above[0]; t1 = _below[0]; t2 = _below[-1] if len(_below) > 1 else _below[0]

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
            "ohlc_signal": trade.get("OHLC Signal", "N/A"),
            "prev_ohlc_avg": trade.get("Prev OHLC Avg", "N/A"),
            "tracking_start_time": get_cst_now().isoformat(),
        }

    def _is_already_tracked(ticker):
        return any(t["ticker"] == ticker for t in st.session_state["tracking_trades"])
    
    def _is_near_entry_price(trade, threshold_pct=0.5):
        """Check if trade open is within threshold% of entry price."""
        try:
            # For gap scenarios, try to extract entry from Notes first
            notes = trade.get("Notes", "")
            entry = None
            
            if notes and isinstance(notes, str) and "Gap" in notes:
                # Extract price from gap notes
                import re
                if "near" in notes.lower():
                    parts = notes.lower().split("near")
                    if len(parts) > 1:
                        prices = re.findall(r'\$?([\d.]+)', parts[-1])
                        if prices:
                            try:
                                entry = float(prices[0])
                            except:
                                pass
                
                # Fallback: extract last price mentioned
                if entry is None:
                    prices = re.findall(r'\$?([\d.]+)', notes)
                    if prices:
                        try:
                            entry = float(prices[-1])
                        except:
                            pass
            
            # If still no entry, try Entry field
            if entry is None:
                entry = float(str(trade.get("Entry", "$0")).replace("$", ""))
            
            open_price = float(str(trade.get("Open", "$0")).replace("$", ""))
            direction = trade.get("Direction", "LONG")
            gap_thr = round(entry * threshold_pct / 100, 2)
            
            if direction == "LONG":
                return (entry - gap_thr) <= open_price <= (entry + gap_thr)
            else:  # SHORT
                return (entry - gap_thr) <= open_price <= (entry + gap_thr)
        except:
            return False

    # ── Pre-confirmation auto-track toggle (default OFF — rely on 8:30 confirmation table) ──
    _pre_conf_track = st.session_state.get("pre_confirmation_auto_track", False)

    # ── AUTO-START: auto-track OPENS NEAR ENTRY at 8:30 AM CST+ (skip DIVERGED) ──
    if _pre_conf_track and auto_track_enabled and near_entry_rows and not st.session_state["tracking_trades"]:
        for trade in near_entry_rows:
            if not _is_already_tracked(trade.get("Ticker")):
                # Check 8:30 bias — skip if DIVERGED
                _chk = get_830_bias_eval(trade.get("Ticker"), trade.get("Direction"))
                if _chk and _chk.get("alignment") == "DIVERGED":
                    continue
                # Auto-save to database
                try:
                    entry_val = float(str(trade.get("Open", "$0")).replace("$", ""))
                    stop_val = float(str(trade.get("Stop", "$0")).replace("$", ""))
                    t1_val = float(str(trade.get("T1", "$0")).replace("$", ""))
                    t2_val = float(str(trade.get("T2", "$0")).replace("$", ""))
                    save_trade(
                        ticker=trade.get("Ticker"),
                        direction=trade.get("Direction"),
                        entry_price=entry_val,
                        stop_loss=stop_val,
                        target1=t1_val,
                        target2=t2_val,
                        open_price=entry_val,
                        scenario=trade.get("Scenario"),
                        confidence=trade.get("Confidence"),
                        notes=f"Auto-tracked at 8:30 AM (Confidence: {trade.get('Confidence', 'N/A')})",
                        ohlc_signal=trade.get('OHLC Signal', trade.get('ohlc_signal'))
                    )
                except Exception as e:
                    print(f"⚠️ Auto-save failed for {trade.get('Ticker')}: {e}")
                st.session_state["tracking_trades"].append(_make_track_entry(trade))
    
    # ── AUTO-TRACK: Gap/PastStop if they're actually near entry price ──
    if _pre_conf_track and auto_track_enabled and secondary_tracking_candidates and not st.session_state["tracking_trades"]:
        for trade in secondary_tracking_candidates:
            if not _is_already_tracked(trade.get("Ticker")) and _is_near_entry_price(trade):
                # Check 8:30 bias — skip if DIVERGED
                _chk = get_830_bias_eval(trade.get("Ticker"), trade.get("Direction"))
                if _chk and _chk.get("alignment") == "DIVERGED":
                    continue
                # Auto-save to database
                try:
                    entry_val = float(str(trade.get("Open", "$0")).replace("$", ""))
                    stop_val = float(str(trade.get("Stop", "$0")).replace("$", ""))
                    t1_val = float(str(trade.get("T1", "$0")).replace("$", ""))
                    t2_val = float(str(trade.get("T2", "$0")).replace("$", ""))
                    save_trade(
                        ticker=trade.get("Ticker"),
                        direction=trade.get("Direction"),
                        entry_price=entry_val,
                        stop_loss=stop_val,
                        target1=t1_val,
                        target2=t2_val,
                        open_price=entry_val,
                        scenario=trade.get("Scenario"),
                        confidence=trade.get("Confidence"),
                        notes=f"Auto-tracked at 8:30 AM from {trade.get('Scenario', 'unknown')} (Confidence: {trade.get('Confidence', 'N/A')})",
                        ohlc_signal=trade.get('OHLC Signal', trade.get('ohlc_signal'))
                    )
                except Exception as e:
                    print(f"⚠️ Auto-save failed for {trade.get('Ticker')}: {e}")
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
        rows_sorted = sorted(rows, key=lambda x: _parse_rr(x.get("Best RR", "0")), reverse=True)
        df = pd.DataFrame(rows_sorted)
        # Only use columns that actually exist in the DataFrame
        cols_to_show = [c for c in display_cols if c in df.columns]
        df_display = df[cols_to_show] if cols_to_show else df
        if "_best_rr_sort_main" in df_display.columns:
            df_display = df_display.drop(columns=["_best_rr_sort_main"])

        # ── Direction-aware coloring for Wk Fib and 4H RSI(WTD) ──
        def _style_fib_rsi_scen(df):
            styles = pd.DataFrame("", index=df.index, columns=df.columns)
            if "Direction" not in df.columns:
                return styles
            for idx in df.index:
                _dir = str(df.loc[idx, "Direction"]).upper()
                _is_long = "LONG" in _dir
                if "Wk Fib" in df.columns:
                    try:
                        _fv = float(str(df.loc[idx, "Wk Fib"]))
                        if _is_long:
                            fc = "#0a3d1f; color: #00e5a0" if _fv <= 38 else ("#3d0a1a; color: #ff4d6a" if _fv >= 62 else "")
                        else:
                            fc = "#0a3d1f; color: #00e5a0" if _fv >= 62 else ("#3d0a1a; color: #ff4d6a" if _fv <= 38 else "")
                        if fc:
                            styles.loc[idx, "Wk Fib"] = f"background-color: {fc}; font-weight: 700"
                    except Exception:
                        pass
                if "4H RSI(WTD)" in df.columns:
                    try:
                        _rv = float(str(df.loc[idx, "4H RSI(WTD)"]))
                        if _is_long:
                            rc = "#0a3d1f; color: #00e5a0" if _rv >= 50 else "#3d0a1a; color: #ff4d6a"
                        else:
                            rc = "#0a3d1f; color: #00e5a0" if _rv < 50 else "#3d0a1a; color: #ff4d6a"
                        styles.loc[idx, "4H RSI(WTD)"] = f"background-color: {rc}; font-weight: 700"
                    except Exception:
                        pass
            return styles

        try:
            df_styled = df_display.style.apply(_style_fib_rsi_scen, axis=None)
            st.dataframe(df_styled, use_container_width=True, height=min(40 * max(len(df_display), 1) + 38, 400))
        except Exception:
            st.dataframe(df_display, use_container_width=True, height=min(40 * max(len(df_display), 1) + 38, 400))
        # Track buttons row underneath the table
        btn_cols = st.columns(min(len(rows), 8))
        for idx, trade in enumerate(rows[:8]):
            tkr = trade.get("Ticker", "?")
            already = _is_already_tracked(tkr)
            label = f"🎯 {tkr}" if already else f"📌 {tkr}"
            with btn_cols[idx % len(btn_cols)]:
                if st.button(label, key=f"trk_{table_key}_{idx}_{tkr}", use_container_width=True, disabled=already):
                    # Auto-save to database
                    try:
                        entry_val = float(str(trade.get("Open", "$0")).replace("$", ""))
                        stop_val = float(str(trade.get("Stop", "$0")).replace("$", ""))
                        t1_val = float(str(trade.get("T1", "$0")).replace("$", ""))
                        t2_val = float(str(trade.get("T2", "$0")).replace("$", ""))
                        saved, telegram_sent, trade_id = save_trade(
                            ticker=trade.get("Ticker"),
                            direction=trade.get("Direction"),
                            entry_price=entry_val,
                            stop_loss=stop_val,
                            target1=t1_val,
                            target2=t2_val,
                            open_price=entry_val,
                            scenario=trade.get("Scenario"),
                            confidence=trade.get("Confidence"),
                            notes=f"Tracked from Intraday Plan (Confidence: {trade.get('Confidence', 'N/A')})",
                            ohlc_signal=trade.get('OHLC Signal', trade.get('ohlc_signal')),
                            in_replay=st.session_state.get("replay_mode", False),
                        )
                        if saved:
                            st.success(f"✅ {tkr} saved to Trade Tracker")
                            if telegram_sent:
                                st.caption("📱 Telegram notification sent")
                    except Exception as e:
                        st.error(f"Failed to save {tkr}: {e}")
                    st.session_state["tracking_trades"].append(_make_track_entry(trade))
                    st.rerun()

    # ── SCENARIO TABLES with per-row Track buttons ──
    between_rows = [r for r in table_rows if r.get("Scenario", "").strip().startswith("⚡ OPENS BETWEEN ENTRY & STOP")]
    past_stop_rows = [r for r in table_rows if r.get("Scenario", "").strip().startswith("❌ OPENS PAST STOP")]
    _known_scenarios = {"✅ OPENS NEAR ENTRY", "⚡ OPENS BETWEEN ENTRY & STOP", "❌ OPENS PAST STOP"}
    scenario_other_rows = [r for r in table_rows if not any(r.get("Scenario", "").strip().startswith(s) for s in _known_scenarios)]
    between_rows.sort(key=lambda x: conf_priority.get(x.get("Confidence", "LOW"), 999))
    past_stop_rows.sort(key=lambda x: conf_priority.get(x.get("Confidence", "LOW"), 999))
    scenario_other_rows.sort(key=lambda x: conf_priority.get(x.get("Confidence", "LOW"), 999))
    
    # Only define display_cols if table_rows is not empty
    if table_rows:
        # Explicit column order — focused set, noisy columns excluded
        preferred_order = [
            "Ticker", "Direction", "Current", "Open",
            "Stop", "T1", "T2", "Best RR", "Options Strategy",
            "Option", "Scenario",
            "Weekly Zone", "Daily Zone", "Zone Conclusion",
            "Wk Fib", "4H RSI(WTD)", "Notes",
            "Prev Close", "Prev OHLC Avg", "OHLC Signal",
            "RR(T1)", "RR(T2)",
            "ATM", "Spread", "ATR", "Confidence",
            "Exp 0DTE", "Exp 2-3DTE", "Move",
        ]
        all_keys = [c for c in table_rows[0].keys() if c not in ["_scenario_id", "_best_rr_sort_main"]]
        display_cols = [c for c in preferred_order if c in all_keys]
    else:
        display_cols = []

    # ── Compute bias target date (replay or live) ─────────────────────────
    from datetime import date as _date_type
    if _in_replay and st.session_state.get("replay_date"):
        _bias_date_early = st.session_state["replay_date"]
    elif _active_plan_date == _date_type.today():
        _bias_date_early = _date_type.today()
    else:
        _bias_date_early = _active_plan_date + timedelta(days=1)
    _bias_target = _bias_date_early if _bias_date_early != _date_type.today() else None

    # ── Enrich scenario rows with 10m/30m/4H bias ──────────────────────────
    def _enrich_rows_with_bias(rows):
        """Add 10m, 30m, 4H bias + Alignment to each row."""
        enriched = []
        for r in rows:
            ticker = r.get("Ticker", "?")
            direction = r.get("Direction", "LONG")
            bias_info = get_830_bias_eval(ticker, direction, target_date=_bias_target)
            r_enr = r.copy()
            r_enr["10m Bias"] = bias_info.get("bias_10m", "N/A") if bias_info else "N/A"
            r_enr["30m Bias"] = bias_info.get("bias_30m", "N/A") if bias_info else "N/A"
            r_enr["4H Bias"]  = bias_info.get("bias_4h", "N/A") if bias_info else "N/A"
            r_enr["Alignment"] = bias_info.get("alignment", "N/A") if bias_info else "N/A"
            
            # ── ADJUST DIRECTION FOR BIG GAP SCENARIOS BASED ON BIAS ──
            # If scenario is BIG GAP DOWN/UP, check if bias strongly disagrees with direction
            scenario_text = r.get("Scenario", "").upper() if r.get("Scenario") else ""
            if "BIG GAP" in scenario_text:
                # Count bias signals
                bias_10m = r_enr.get("10m Bias", "").upper()
                bias_30m = r_enr.get("30m Bias", "").upper()
                bias_4h = r_enr.get("4H Bias", "").upper()
                
                bearish_count = sum(1 for b in [bias_10m, bias_30m, bias_4h] if "BEARISH" in b)
                bullish_count = sum(1 for b in [bias_10m, bias_30m, bias_4h] if "BULLISH" in b)
                
                # For BIG GAP DOWN: if 2+ biases are BEARISH, flag bias direction as SHORT
                if "GAP DOWN" in scenario_text and bearish_count >= 2 and direction == "LONG":
                    r_enr["BiasDirection"] = "SHORT"  # Bias suggests SHORT — display only, don't change original Direction
                    r_enr["BiasNote"] = "⚠️ Bias suggests SHORT"
                    # Re-evaluate bias_info with SHORT direction for alignment display
                    new_bias_info = get_830_bias_eval(ticker, "SHORT", target_date=_bias_target)
                    if new_bias_info:
                        r_enr["Alignment"] = new_bias_info.get("alignment", "N/A")
                
                # For BIG GAP UP: if 2+ biases are BULLISH, flag bias direction as LONG
                elif "GAP UP" in scenario_text and bullish_count >= 2 and direction == "SHORT":
                    r_enr["BiasDirection"] = "LONG"  # Bias suggests LONG — display only
                    r_enr["BiasNote"] = "⚠️ Bias suggests LONG"
                    new_bias_info = get_830_bias_eval(ticker, "LONG", target_date=_bias_target)
                    if new_bias_info:
                        r_enr["Alignment"] = new_bias_info.get("alignment", "N/A")
            
            enriched.append(r_enr)
        return enriched
    
    # Note: Enriched rows will be created fresh inside the fragment for correct _bias_target
    # (after _bias_target is updated based on plan_date)

    # Add bias columns to display list
    for _bias_col in ["10m Bias", "30m Bias", "4H Bias", "Alignment"]:
        if _bias_col not in display_cols:
            display_cols.append(_bias_col)

    # ── AUTO-TRACK ALL CONFIRMED TICKERS — DISABLED ──────────
    # Tracking now only happens via 8:30 Confirmation Table. Bias display only.
    if False and auto_track_enabled and table_rows:
        _all_enriched = _enrich_rows_with_bias(table_rows)
        for _ae_trade in _all_enriched:
            _ae_tkr = _ae_trade.get("Ticker", "?")
            if _is_already_tracked(_ae_tkr):
                continue
            _ae_align = (_ae_trade.get("Alignment") or "").upper()
            if "CONFIRMED" not in _ae_align:
                continue
            # Determine direction from biases
            _b10 = (_ae_trade.get("10m Bias") or "").upper()
            _b30 = (_ae_trade.get("30m Bias") or "").upper()
            _b4h = (_ae_trade.get("4H Bias") or "").upper()
            if "BULLISH" in _b10 and "BULLISH" in _b30 and "BULLISH" in _b4h:
                _ae_dir = "LONG"
            elif "BEARISH" in _b10 and "BEARISH" in _b30 and "BEARISH" in _b4h:
                _ae_dir = "SHORT"
            else:
                continue  # Not all biases unanimous — skip
            # GAP scenarios: skip unless bias has flipped direction
            _ae_scen = (_ae_trade.get("Scenario") or "").upper()
            _orig_dir = _ae_trade.get("Direction", "LONG")
            if "GAP" in _ae_scen:
                try:
                    import paper_config as _pc_gap
                    if getattr(_pc_gap, "GAP_REQUIRE_FLIP", False) and _ae_dir == _orig_dir:
                        continue  # GAP not yet flipped — don't track
                except Exception:
                    pass
            # Parse prices
            def _ae_pf(v):
                try: return float(str(v).replace("$", "").replace(",", ""))
                except: return 0.0
            _ae_open  = _ae_pf(_ae_trade.get("Open") or _ae_trade.get("Entry") or "0")
            _ae_stop  = _ae_pf(_ae_trade.get("Stop") or "0")
            _ae_t1    = _ae_pf(_ae_trade.get("T1") or "0")
            _ae_t2    = _ae_pf(_ae_trade.get("T2") or "0")
            _orig_dir = _ae_trade.get("Direction", "LONG")
            # Flip stop/t1/t2 around open if direction changed
            if _ae_dir != _orig_dir and _ae_open > 0:
                _ae_stop = round(2 * _ae_open - _ae_stop, 2)
                _ae_t1   = round(2 * _ae_open - _ae_t1, 2)
                _ae_t2   = round(2 * _ae_open - _ae_t2, 2)
            # Compute RR with corrected values
            def _ae_rr(e, s, t, d):
                try:
                    risk = (e - s) if d == "LONG" else (s - e)
                    rew  = (t - e) if d == "LONG" else (e - t)
                    return round(rew / risk, 2) if risk > 0 else 0.0
                except: return 0.0
            _ae_rr1 = _ae_rr(_ae_open, _ae_stop, _ae_t1, _ae_dir)
            _ae_rr2 = _ae_rr(_ae_open, _ae_stop, _ae_t2, _ae_dir)
            _ae_best = max(_ae_rr1, _ae_rr2)
            # Save to database (triggers telegram automatically)
            try:
                save_trade(
                    ticker=_ae_tkr,
                    direction=_ae_dir,
                    entry_price=_ae_open,
                    stop_loss=_ae_stop,
                    target1=_ae_t1,
                    target2=_ae_t2,
                    open_price=_ae_open,
                    scenario=_ae_trade.get("Scenario"),
                    confidence=_ae_trade.get("Confidence"),
                    notes=f"Auto-tracked CONFIRMED ({_ae_dir}) — biases aligned",
                    ohlc_signal=_ae_trade.get('OHLC Signal', _ae_trade.get('ohlc_signal')),
                    in_replay=st.session_state.get("replay_mode", False),
                )
            except Exception as e:
                print(f"⚠️ Auto-save CONFIRMED failed for {_ae_tkr}: {e}")
            # Add to session tracking
            st.session_state["tracking_trades"].append({
                "ticker": _ae_tkr,
                "entry": _ae_open,
                "stop": _ae_stop,
                "t1": _ae_t1,
                "t2": _ae_t2,
                "direction": _ae_dir,
                "confidence": _ae_trade.get("Confidence", "N/A"),
                "atr": str(_ae_trade.get("ATR", "")).replace("$", ""),
                "scenario": _ae_trade.get("Scenario", ""),
                "rr_t1": _ae_rr1,
                "rr_t2": _ae_rr2,
                "best_rr": _ae_best,
                "tracking_start_time": get_cst_now().isoformat(),
            })
            print(f"🎯 Auto-tracked CONFIRMED {_ae_tkr} as {_ae_dir} (orig: {_orig_dir})")

    # ── AUTO-REFRESH INTRADAY SCENARIO TABLES (every 5 min) ──────────────────
    @st.fragment(run_every=timedelta(minutes=5))
    def _refresh_scenario_tables():
        """Auto-refresh scenario tables every 5 min with 10m/30m/4H bias columns."""
        # ── Stop refreshing after 3:00 PM CST (live mode only) ──
        if not _in_replay and is_after_market_time(15, 0):
            return
        if st.session_state.get("_debug_fragments"):
            print(f"[FRAGMENT] _refresh_scenario_tables (5min) fired at {datetime.now().strftime('%H:%M:%S')}")
        _now = get_cst_now()
        st.caption(f"🔄 Auto-refreshes every 5 min · Last refresh: {_now.strftime('%I:%M:%S %p CST')}")
        
        # Color helper for bias/alignment/OHLC signal
        def _color_bias(val):
            if not isinstance(val, str): return ""
            v = val.upper()
            if "BULLISH" in v or "BULL" in v: return "background-color: #0a3d1f; color: #00e5a0; font-weight: 700"
            if "BEARISH" in v or "BEAR" in v: return "background-color: #3d0a1a; color: #ff4d6a; font-weight: 700"
            if "CONFIRMED" in v: return "color: #00e5a0; font-weight: 700"
            if "DIVERGED" in v: return "color: #f0c040; font-weight: 700"
            if "NEUTRAL" in v: return "color: #a0a0a0; font-weight: 700"
            return ""
        
        # Re-enrich on each refresh for fresh bias data
        _near_fresh = _enrich_rows_with_bias(near_entry_rows)
        _btwn_fresh = _enrich_rows_with_bias(between_rows)
        _past_fresh = _enrich_rows_with_bias(past_stop_rows)
        bias_style_cols = ["10m Bias", "30m Bias", "4H Bias", "Alignment", "OHLC Signal"]

        # ── AUTO-TRACK on 5-min refresh — DISABLED ──
        # Tracking now only happens via 8:30 Confirmation Table.
        # _all_refreshed kept for display purposes only.
        _all_refreshed = _near_fresh + _btwn_fresh + _past_fresh

        # ── OPENS NEAR ENTRY ────
        if _near_fresh:
            st.markdown("### ✅ OPENS NEAR ENTRY")
            df_near = pd.DataFrame(_near_fresh)
            cols_to_show = [c for c in display_cols if c in df_near.columns]
            styled_near = df_near[cols_to_show].style.applymap(_color_bias, subset=[c for c in bias_style_cols if c in cols_to_show])
            st.dataframe(styled_near, use_container_width=True, height=min(40 * len(df_near) + 38, 400))
            
            # Track buttons
            btn_cols = st.columns(min(len(_near_fresh), 8))
            for idx, trade in enumerate(_near_fresh[:8]):
                tkr = trade.get("Ticker", "?")
                already = _is_already_tracked(tkr)
                label = f"🎯 {tkr}" if already else f"📌 {tkr}"
                with btn_cols[idx % len(btn_cols)]:
                    if st.button(label, key=f"trk_near_{idx}_{tkr}", use_container_width=True, disabled=already):
                        try:
                            entry_val = float(str(trade.get("Open", "$0")).replace("$", ""))
                            stop_val = float(str(trade.get("Stop", "$0")).replace("$", ""))
                            t1_val = float(str(trade.get("T1", "$0")).replace("$", ""))
                            t2_val = float(str(trade.get("T2", "$0")).replace("$", ""))
                            saved, _, _ = save_trade(
                                ticker=trade.get("Ticker"),
                                direction=trade.get("Direction"),
                                entry_price=entry_val,
                                stop_loss=stop_val,
                                target1=t1_val,
                                target2=t2_val,
                                open_price=entry_val,
                                scenario=trade.get("Scenario"),
                                confidence=trade.get("Confidence"),
                                notes=f"Auto-tracked Near Entry: {trade.get('Confidence', 'N/A')}",
                                ohlc_signal=trade.get('OHLC Signal', trade.get('ohlc_signal'))
                            )
                            if saved:
                                st.success(f"✅ {tkr} saved to Trade Tracker")
                        except Exception as e:
                            st.error(f"Failed to save {tkr}: {e}")
                        st.session_state["tracking_trades"].append(_make_track_entry(trade))
                        st.rerun()
        
        # ── OPENS BETWEEN ENTRY & STOP ────
        if _btwn_fresh:
            st.markdown("### ⚡ OPENS BETWEEN ENTRY & STOP")
            df_btwn = pd.DataFrame(_btwn_fresh)
            cols_to_show = [c for c in display_cols if c in df_btwn.columns]
            styled_btwn = df_btwn[cols_to_show].style.applymap(_color_bias, subset=[c for c in bias_style_cols if c in cols_to_show])
            st.dataframe(styled_btwn, use_container_width=True, height=min(40 * len(df_btwn) + 38, 400))
            
            # Track buttons
            btn_cols = st.columns(min(len(_btwn_fresh), 8))
            for idx, trade in enumerate(_btwn_fresh[:8]):
                tkr = trade.get("Ticker", "?")
                already = _is_already_tracked(tkr)
                label = f"🎯 {tkr}" if already else f"📌 {tkr}"
                with btn_cols[idx % len(btn_cols)]:
                    if st.button(label, key=f"trk_btwn_{idx}_{tkr}", use_container_width=True, disabled=already):
                        try:
                            entry_val = float(str(trade.get("Open", "$0")).replace("$", ""))
                            stop_val = float(str(trade.get("Stop", "$0")).replace("$", ""))
                            t1_val = float(str(trade.get("T1", "$0")).replace("$", ""))
                            t2_val = float(str(trade.get("T2", "$0")).replace("$", ""))
                            saved, _, _ = save_trade(
                                ticker=trade.get("Ticker"),
                                direction=trade.get("Direction"),
                                entry_price=entry_val,
                                stop_loss=stop_val,
                                target1=t1_val,
                                target2=t2_val,
                                open_price=entry_val,
                                scenario=trade.get("Scenario"),
                                confidence=trade.get("Confidence"),
                                notes=f"Auto-tracked Between Entry&Stop: {trade.get('Confidence', 'N/A')}",
                                ohlc_signal=trade.get('OHLC Signal', trade.get('ohlc_signal'))
                            )
                            if saved:
                                st.success(f"✅ {tkr} saved to Trade Tracker")
                        except Exception as e:
                            st.error(f"Failed to save {tkr}: {e}")
                        st.session_state["tracking_trades"].append(_make_track_entry(trade))
                        st.rerun()

        # ── OPENS PAST STOP (live monitoring with bias) ────
        if _past_fresh:
            st.markdown("### ❌ OPENS PAST STOP")
            df_past = pd.DataFrame(_past_fresh)
            cols_to_show = [c for c in display_cols if c in df_past.columns]
            styled_past = df_past[cols_to_show].style.applymap(_color_bias, subset=[c for c in bias_style_cols if c in cols_to_show])
            st.dataframe(styled_past, use_container_width=True, height=min(40 * len(df_past) + 38, 400))
            
            # Track buttons
            btn_cols = st.columns(min(len(_past_fresh), 8))
            for idx, trade in enumerate(_past_fresh[:8]):
                tkr = trade.get("Ticker", "?")
                already = _is_already_tracked(tkr)
                label = f"🎯 {tkr}" if already else f"📌 {tkr}"
                with btn_cols[idx % len(btn_cols)]:
                    if st.button(label, key=f"trk_past_{idx}_{tkr}", use_container_width=True, disabled=already):
                        try:
                            entry_val = float(str(trade.get("Open", "$0")).replace("$", ""))
                            stop_val = float(str(trade.get("Stop", "$0")).replace("$", ""))
                            t1_val = float(str(trade.get("T1", "$0")).replace("$", ""))
                            t2_val = float(str(trade.get("T2", "$0")).replace("$", ""))
                            saved, _, _ = save_trade(
                                ticker=trade.get("Ticker"),
                                direction=trade.get("Direction"),
                                entry_price=entry_val,
                                stop_loss=stop_val,
                                target1=t1_val,
                                target2=t2_val,
                                open_price=entry_val,
                                scenario=trade.get("Scenario"),
                                confidence=trade.get("Confidence"),
                                notes=f"Auto-tracked Opens Past Stop (flipped): {trade.get('Confidence', 'N/A')}",
                                ohlc_signal=trade.get('OHLC Signal', trade.get('ohlc_signal'))
                            )
                            if saved:
                                st.success(f"✅ {tkr} saved to Trade Tracker")
                        except Exception as e:
                            st.error(f"Failed to save {tkr}: {e}")
                        st.session_state["tracking_trades"].append(_make_track_entry(trade))
                        st.rerun()

    if display_cols and (near_entry_rows or between_rows or past_stop_rows):
        pass  # Moved to render after POSITION TRACKER (see below)

    # ── POSITION TRACKER —FIRST TABLE ──────────
    tracked = st.session_state.get("tracking_trades", [])
    if tracked:
        st.markdown("---")
        _replay_clock(8 * 60 + 30, "Auto-tracking confirmed trades at market open")
        st.markdown(f"### 📍 Tracking {len(tracked)} Position(s)")

        # Clear All button
        if st.button("❌ Clear All Tracking", key="clear_all_tracking"):
            st.session_state["tracking_trades"] = []
            st.rerun()

        # Pre-confirmation auto-track toggle (default OFF)
        st.checkbox(
            "Auto-track before 8:30 confirmation",
            value=st.session_state.get("pre_confirmation_auto_track", False),
            key="pre_confirmation_auto_track",
            help="OFF = only track from 8:30 Confirmation Table. ON = also auto-track near-entry rows immediately at 8:30.",
        )

        # ── Tracking notification state (throttle: only send on action change) ──
        if "_trk_last_action" not in st.session_state:
            st.session_state["_trk_last_action"] = {}  # {ticker: last_action_string}
        if "_trk_last_send_time" not in st.session_state:
            st.session_state["_trk_last_send_time"] = {}  # {ticker: datetime}

        @st.fragment(run_every=timedelta(minutes=5))
        def _live_tracker_fragment():
            """Auto-refreshes every 5 min — only this fragment, not the whole page."""
            _rp_mode = st.session_state.get("replay_mode", False)
            _tracked = st.session_state.get("tracking_trades", [])
            # ── Fast exit: nothing to track after hours ──
            if not _rp_mode and is_after_market_time(15, 0) and not _tracked:
                return
            if st.session_state.get("_debug_fragments"):
                print(f"[FRAGMENT] _live_tracker_fragment (5min) fired at {datetime.now().strftime('%H:%M:%S')}")
            if not _tracked:
                return
            _now = get_cst_now()
            _rp_dt = st.session_state.get("replay_date")
            _rp_time_min = st.session_state.get("replay_time_min", 500)
            # ── Stop tracking after 3:00 PM CST (live mode only) ──
            if not _rp_mode and is_after_market_time(15, 0):
                # Send one final summary then stop
                _eod_key = f"_trk_eod_sent_{date.today()}"
                if not st.session_state.get(_eod_key) and TELEGRAM_ENABLED and not _in_telegram_quiet_hours():
                    try:
                        _ts = _now.strftime("%m/%d %I:%M %p CST")
                        _lines = [f"🔔 <b>TRACKING STOPPED — 3:00 PM CST</b>  —  {_ts}\n"]
                        _lines.append(f"<pre>{'Ticker':<8} {'Dir':<6} {'Status'}")
                        _lines.append(f"{'─'*8} {'─'*6} {'─'*20}")
                        for _tr in sorted(_tracked, key=lambda x: x.get("ticker", "")):
                            _lines.append(f"{_tr.get('ticker',''):<8} {_tr.get('direction',''):<6} END OF DAY")
                        _lines.append("</pre>")
                        _url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
                        requests.post(_url, json={"chat_id": TELEGRAM_CHAT_ID,
                            "text": "\n".join(_lines), "parse_mode": "HTML"}, timeout=5)
                        st.session_state[_eod_key] = True
                    except Exception:
                        pass
                # Clear all tracked positions after market close
                st.session_state["tracking_trades"] = []
                st.info("⏹️ Tracking stopped — all positions cleared at 3:00 PM CST.")
                return

            if _rp_mode and _rp_dt:
                _rp_h, _rp_m = divmod(_rp_time_min, 60)
                st.caption(f"🔄 Replay prices for **{_rp_dt.strftime('%m/%d/%Y')}** at **{_rp_h if _rp_h <= 12 else _rp_h-12}:{_rp_m:02d} {'AM' if _rp_h < 12 else 'PM'} CST**")
            else:
                st.caption(f"🔄 Live prices · Last refresh: {_now.strftime('%I:%M:%S %p CST')} · Auto-refreshes every 5 min")

            import yfinance as yf
            track_rows = []
            error_tickers = []
            # ── Paper trading engine (replay + live) ──
            _pt_engine = None
            _pt_results = []  # collect paper trade events for display
            _paper_open_tickers = set()
            if PAPER_TRADING_AVAILABLE:
                import paper_config as _pc
                if _rp_mode and _pc.AUTO_PAPER_IN_REPLAY:
                    _pt_engine = PaperTrader(in_memory=True)
                elif not _rp_mode and _pc.SUBMIT_TO_ALPACA:
                    _pt_engine = PaperTrader(in_memory=False)
                # Query open paper trades for status display
                try:
                    _paper_query_pt = _pt_engine if _pt_engine else PaperTrader(in_memory=False)
                    _paper_open_tickers = {t["ticker"] for t in _paper_query_pt.get_open_trades()}
                except Exception:
                    pass
            for ti, tr in enumerate(_tracked):
                try:
                    tk = yf.Ticker(tr['ticker'])
                    # In replay mode, fetch intraday bars and pick price at simulated time
                    if _rp_mode and _rp_dt:
                        _intra = tk.history(start=str(_rp_dt), end=str(_rp_dt + timedelta(days=1)), interval="5m", prepost=True)
                        if _intra.empty:
                            # Fallback to daily
                            hist = tk.history(start=str(_rp_dt), end=str(_rp_dt + timedelta(days=1)))
                            live_price = float(hist["Close"].iloc[-1]) if not hist.empty else None
                        else:
                            # Convert replay time to ET for matching (CST+1)
                            _et_tz = pytz.timezone("America/New_York")
                            _cst_tz = pytz.timezone("America/Chicago")
                            _sim_cst = _cst_tz.localize(datetime(_rp_dt.year, _rp_dt.month, _rp_dt.day, _rp_h, _rp_m, 0))
                            _sim_et = _sim_cst.astimezone(_et_tz)
                            # Filter bars up to simulated time
                            _idx = _intra.index
                            if _idx.tz is None:
                                _idx = _idx.tz_localize("America/New_York")
                            else:
                                _idx = _idx.tz_convert("America/New_York")
                            _bars_before = _intra[_idx <= _sim_et]
                            if not _bars_before.empty:
                                live_price = float(_bars_before["Close"].iloc[-1])
                            else:
                                # Before any intraday bar — use open of first bar
                                live_price = float(_intra["Open"].iloc[0])
                        if live_price is None:
                            error_tickers.append(tr['ticker'])
                            continue
                    else:
                        hist = tk.history(period="5d")
                        if hist.empty:
                            error_tickers.append(tr['ticker'])
                            continue
                        live_price = float(hist["Close"].iloc[-1])

                    # ── Hourly ATR (14) & VWAP (Above / Below) ──
                    _h_atr_val = None
                    _vwap_label = "N/A"
                    _prev_vwap_label = "N/A"
                    try:
                        if _rp_mode and _rp_dt:
                            _h_bars = tk.history(start=str(_rp_dt - timedelta(days=10)),
                                                 end=str(_rp_dt + timedelta(days=1)),
                                                 interval="1h")
                        else:
                            _h_bars = tk.history(period="5d", interval="1h")
                        if _h_bars is not None and len(_h_bars) >= 2:
                            # ATR-14 on hourly
                            _hh = _h_bars["High"].values
                            _hl = _h_bars["Low"].values
                            _hc = _h_bars["Close"].values
                            _trs = []
                            for _i in range(1, len(_hh)):
                                _trs.append(max(_hh[_i] - _hl[_i],
                                                abs(_hh[_i] - _hc[_i - 1]),
                                                abs(_hl[_i] - _hc[_i - 1])))
                            _atr_period = min(14, len(_trs))
                            if _atr_period > 0:
                                _h_atr_val = round(sum(_trs[-_atr_period:]) / _atr_period, 2)
                            # VWAP from today's hourly bars
                            _idx_h = _h_bars.index
                            if _idx_h.tz is None:
                                _idx_h = _idx_h.tz_localize("America/New_York")
                            else:
                                _idx_h = _idx_h.tz_convert("America/New_York")
                            _target_date = _rp_dt if (_rp_mode and _rp_dt) else date.today()
                            _target_date = _target_date if isinstance(_target_date, date) else (_target_date.date() if hasattr(_target_date, 'date') else _target_date)
                            _today_mask = _idx_h.date == _target_date
                            _today_h = _h_bars[_today_mask]
                            if len(_today_h) >= 1 and "Volume" in _today_h.columns:
                                _tp = (_today_h["High"] + _today_h["Low"] + _today_h["Close"]) / 3
                                _vol = _today_h["Volume"].replace(0, float("nan"))
                                _cum_tpv = (_tp * _vol).cumsum()
                                _cum_vol = _vol.cumsum()
                                _vwap_series = _cum_tpv / _cum_vol
                                _vwap_now = _vwap_series.dropna().iloc[-1] if not _vwap_series.dropna().empty else None
                                if _vwap_now is not None:
                                    _vwap_label = f"{'A' if live_price > _vwap_now else 'B'} (${_vwap_now:.2f})"
                            # Previous day VWAP
                            _all_dates = sorted(set(_idx_h.date))
                            _prev_dates = [d for d in _all_dates if d < _target_date]
                            if _prev_dates:
                                _prev_d = _prev_dates[-1]
                                _prev_mask = _idx_h.date == _prev_d
                                _prev_h = _h_bars[_prev_mask]
                                if len(_prev_h) >= 1 and "Volume" in _prev_h.columns:
                                    _ptp = (_prev_h["High"] + _prev_h["Low"] + _prev_h["Close"]) / 3
                                    _pvol = _prev_h["Volume"].replace(0, float("nan"))
                                    _pcum_tpv = (_ptp * _pvol).cumsum()
                                    _pcum_vol = _pvol.cumsum()
                                    _pvwap_s = _pcum_tpv / _pcum_vol
                                    _pvwap = _pvwap_s.dropna().iloc[-1] if not _pvwap_s.dropna().empty else None
                                    if _pvwap is not None:
                                        _prev_vwap_label = f"{'A' if live_price > _pvwap else 'B'} (${_pvwap:.2f})"
                    except Exception:
                        pass  # leave defaults

                    # ── RVOL (Relative Volume) ──
                    _rvol_val = None
                    _rvol_label = "N/A"
                    try:
                        # Use intraday 5m bars for precise time-of-day comparison
                        if _rp_mode and _rp_dt:
                            _iv_bars = tk.history(start=str(_rp_dt - timedelta(days=25)),
                                                  end=str(_rp_dt + timedelta(days=1)),
                                                  interval="5m")
                        else:
                            _iv_bars = tk.history(period="1mo", interval="5m")
                        if _iv_bars is not None and len(_iv_bars) > 0 and "Volume" in _iv_bars.columns:
                            _iv_idx = _iv_bars.index
                            if _iv_idx.tz is None:
                                _iv_idx = _iv_idx.tz_localize("America/New_York")
                            else:
                                _iv_idx = _iv_idx.tz_convert("America/New_York")
                            _iv_bars = _iv_bars.copy()
                            _iv_bars.index = _iv_idx

                            _rv_target = _rp_dt if (_rp_mode and _rp_dt) else date.today()
                            _rv_target = _rv_target if isinstance(_rv_target, date) else (
                                _rv_target.date() if hasattr(_rv_target, 'date') else _rv_target)

                            # Today's cumulative volume up to current time
                            _today_iv = _iv_bars[_iv_bars.index.date == _rv_target]
                            if len(_today_iv) > 0:
                                _today_cum_vol = float(_today_iv["Volume"].sum())
                                _today_last_time = _today_iv.index[-1].time()

                                # Average cum vol at same time across past 10-20 trading days
                                _past_dates = sorted(set(d for d in _iv_bars.index.date if d < _rv_target))
                                _past_dates = _past_dates[-20:]  # last 20 days
                                _hist_vols = []
                                for _pd in _past_dates:
                                    _pd_bars = _iv_bars[_iv_bars.index.date == _pd]
                                    # Only bars up to the same time-of-day
                                    _pd_bars_cut = _pd_bars[_pd_bars.index.time <= _today_last_time]
                                    if len(_pd_bars_cut) > 0:
                                        _hist_vols.append(float(_pd_bars_cut["Volume"].sum()))

                                if _hist_vols and _today_cum_vol > 0:
                                    _avg_hist_vol = sum(_hist_vols) / len(_hist_vols)
                                    if _avg_hist_vol > 0:
                                        _rvol_val = round(_today_cum_vol / _avg_hist_vol, 2)
                                        if _rvol_val >= 5.0:
                                            _rvol_label = f"🔴 {_rvol_val:.1f}x"
                                        elif _rvol_val >= 3.0:
                                            _rvol_label = f"🟠 {_rvol_val:.1f}x"
                                        elif _rvol_val >= 2.0:
                                            _rvol_label = f"🟡 {_rvol_val:.1f}x"
                                        elif _rvol_val >= 1.0:
                                            _rvol_label = f"🟢 {_rvol_val:.1f}x"
                                        else:
                                            _rvol_label = f"⚪ {_rvol_val:.1f}x"
                    except Exception:
                        pass  # leave as N/A

                    entry = tr['entry']
                    stop = tr['stop']
                    t1 = tr['t1']
                    t2 = tr['t2']
                    direction = tr['direction']

                    # ── Auto-fix inverted targets (LONG w/ targets below entry, etc.) ──
                    if entry > 0:
                        if direction == "LONG" and t1 < entry and t2 < entry and t1 > 0:
                            t1 = round(2 * entry - t1, 2)
                            t2 = round(2 * entry - t2, 2)
                            tr['t1'], tr['t2'] = t1, t2  # persist fix
                        elif direction == "SHORT" and t1 > entry and t2 > entry:
                            t1 = round(2 * entry - t1, 2)
                            t2 = round(2 * entry - t2, 2)
                            tr['t1'], tr['t2'] = t1, t2

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
                    _bias_td = _rp_dt if (_rp_mode and _rp_dt) else None
                    bias_830 = get_830_bias_eval(tr['ticker'], direction, target_date=_bias_td)
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

                    # ── SEND TELEGRAM TRACKING UPDATE (on action change or every 5 min for key events) ──
                    _tkr_name = tr['ticker']
                    _prev_action = st.session_state.get("_trk_last_action", {}).get(_tkr_name)
                    _prev_send = st.session_state.get("_trk_last_send_time", {}).get(_tkr_name)
                    _min_interval = timedelta(minutes=4, seconds=30)  # prevent dupe from fragment overlap
                    _should_send = False
                    _is_key_event = any(kw in action for kw in ["EXIT", "FULL PROFIT", "SCALE OUT", "TRAIL STOP"])

                    if _prev_action is None:
                        # First time seeing this ticker in tracker — send initial update
                        _should_send = True
                    elif action != _prev_action:
                        # Action changed — always send
                        _should_send = True
                    elif _is_key_event and (not _prev_send or (_now - _prev_send) >= _min_interval):
                        # Key event — resend every 5 min cycle
                        _should_send = True

                    if _should_send and TELEGRAM_ENABLED and not _in_telegram_quiet_hours() and (not _prev_send or (_now - _prev_send) >= _min_interval):
                        try:
                            _use_dir = disp_direction if flipped else direction
                            _use_stop = disp_stop if flipped else stop
                            _use_t1 = disp_t1 if flipped else t1
                            _use_t2 = disp_t2 if flipped else t2
                            # Build action-change alert message
                            _change_label = "🆕 NEW" if _prev_action is None else f"🔄 {_prev_action} → {action}"
                            _ohlc_for_alert = tr.get('ohlc_signal', 'N/A')
                            _atr_for_alert = f"${_h_atr_val:.2f}" if _h_atr_val else "N/A"
                            _rvol_for_alert = _rvol_label
                            _alert_msg = (
                                f"🔔 <b>ACTION UPDATE</b>  —  {_tkr_name}\n"
                                f"<pre>{_change_label}\n"
                                f"Action : {action}\n"
                                f"Dir    : {_use_dir}\n"
                                f"Price  : ${live_price:.2f}  (Entry ${entry:.2f})\n"
                                f"P&L    : {pnl_pct:+.1f}%\n"
                                f"H-ATR  : {_atr_for_alert}\n"
                                f"RVOL   : {_rvol_for_alert}\n"
                                f"VWAP   : {_vwap_label}\n"
                                f"PrevVWAP: {_prev_vwap_label}\n"
                                f"OHLC   : {_ohlc_for_alert}</pre>"
                            )
                            _url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
                            get_thread_pool().submit(
                                requests.post, _url, json={
                                    "chat_id": TELEGRAM_CHAT_ID,
                                    "text": _alert_msg,
                                    "parse_mode": "HTML"
                                }, timeout=5)
                            _fire_and_forget_telegram(
                                send_tracking_price_update,
                                ticker=_tkr_name,
                                current_price=live_price,
                                entry_price=entry,
                                stop_loss=_use_stop,
                                target1=_use_t1,
                                target2=_use_t2,
                                direction=_use_dir,
                                scenario=tr.get("scenario", ""),
                                ohlc_signal=tr.get('ohlc_signal'),
                            )
                            st.session_state.setdefault("_trk_last_send_time", {})[_tkr_name] = _now
                        except Exception as _te:
                            pass
                    # Always update last-action (even if send was throttled)
                    st.session_state.setdefault("_trk_last_action", {})[_tkr_name] = action

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

                    # RR from live values
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
                    if best_rr == 0:
                        rr_t1   = tr.get("rr_t1", 0)
                        rr_t2   = tr.get("rr_t2", 0)
                        best_rr = tr.get("best_rr", 0)

                    # ── Re-compute OHLC signal if missing ──
                    _ohlc_val = tr.get('ohlc_signal', 'N/A')
                    if _ohlc_val in ('N/A', '', None):
                        try:
                            # Fetch daily bars to get prev day OHLC + today open
                            if _rp_mode and _rp_dt:
                                _ohlc_hist = tk.history(start=str(_rp_dt - timedelta(days=10)), end=str(_rp_dt + timedelta(days=1)))
                            else:
                                _ohlc_hist = tk.history(period="5d")
                            if _ohlc_hist is not None and not _ohlc_hist.empty and len(_ohlc_hist) >= 2:
                                _prev_row = _ohlc_hist.iloc[-2]
                                _prev_avg = (_prev_row["Open"] + _prev_row["High"] + _prev_row["Low"] + _prev_row["Close"]) / 4
                                _today_open = float(_ohlc_hist["Open"].iloc[-1])
                                if abs(_prev_avg - _today_open) < 0.005:
                                    _ohlc_val = "NEUTRAL"
                                elif _prev_avg > _today_open:
                                    _ohlc_val = "🐻 BEAR"
                                else:
                                    _ohlc_val = "🐂 BULL"
                                # Update stored trade so it persists
                                tr['ohlc_signal'] = _ohlc_val
                                tr['prev_ohlc_avg'] = round(float(_prev_avg), 2)
                        except Exception:
                            pass  # leave as N/A

                    # News sentiment
                    try:
                        _news_lbl = get_news_sentiment(tr['ticker'])
                        _news_val = "🟢" if _news_lbl == "Good" else ("🔴" if _news_lbl == "Bad" else "⚪")
                    except Exception:
                        _news_val = "⚪"

                    # V-Flow: compare Live vs VWAP vs Prev VWAP directional alignment
                    import re as _re_vf
                    def _parse_price(label):
                        m = _re_vf.search(r'\$(\d+\.?\d*)', label)
                        return float(m.group(1)) if m else None
                    _vwap_price = _parse_price(_vwap_label)
                    _pvwap_price = _parse_price(_prev_vwap_label)
                    _is_long = disp_direction == "LONG"
                    if _vwap_price is not None and _pvwap_price is not None:
                        if _is_long:
                            if live_price > _vwap_price > _pvwap_price:
                                _vflow_val = "✅"
                            elif live_price > _vwap_price or live_price > _pvwap_price:
                                _vflow_val = "🟡"
                            else:
                                _vflow_val = "❌"
                        else:
                            if live_price < _vwap_price < _pvwap_price:
                                _vflow_val = "✅"
                            elif live_price < _vwap_price or live_price < _pvwap_price:
                                _vflow_val = "🟡"
                            else:
                                _vflow_val = "❌"
                    else:
                        _vflow_val = "N/A"

                    # ── Weekly Fib level (Weekly H/L) and 4H WTD RSI ────────────
                    _trk_date = _rp_dt if (_rp_mode and _rp_dt) else date.today()
                    _trk_wk_fib, _trk_4h_rsi = get_weekly_fib_and_4h_rsi(tk, _trk_date, live_price)

                    # Format tracking entry time
                    _trk_start = tr.get("tracking_start_time", "")
                    _entered_label = ""
                    if _trk_start:
                        try:
                            from datetime import datetime as _dt_cls
                            _ts_obj = _dt_cls.fromisoformat(_trk_start)
                            _entered_label = _ts_obj.strftime("%#I:%M %p") if hasattr(_ts_obj, 'strftime') else _trk_start[:5]
                        except Exception:
                            _entered_label = str(_trk_start)[:8]

                    track_rows.append({
                        "Ticker":     tr['ticker'],
                        "Dir":        f"{'🔄 ' if flipped else ''}{disp_direction}",
                        "Action":     action,
                        "Scen":       _scn_short,
                        "Entered":    _entered_label,
                        "Live $":     f"${live_price:.2f}",
                        "Wk Fib":     _trk_wk_fib,
                        "4H RSI":     _trk_4h_rsi,
                        "Entry":      f"${entry:.2f}",
                        "P&L":        f"{pnl_pct:+.2f}%",
                        "Stop":       f"${_ds:.2f}",
                        "T1":         f"${_dt1:.2f}",
                        "T2":         f"${_dt2:.2f}",
                        "RR(T1)":     f"{rr_t1:.2f}x",
                        "RR(T2)":     f"{rr_t2:.2f}x",
                        "Best RR":    f"{best_rr:.2f}x",
                        "H-ATR":      f"${_h_atr_val:.2f}" if _h_atr_val else "N/A",
                        "RVOL":       _rvol_label,
                        "VWAP":       _vwap_label,
                        "Prev VWAP":  _prev_vwap_label,
                        "V-Flow":     _vflow_val,
                        "OHLC":       _ohlc_val,
                        "News":       _news_val,
                        "Conf":       tr.get('confidence', 'N/A'),
                        "10m":        _short(bias_10m),
                        "30m":        _short(bias_30m),
                        "4H":         _short(bias_4h),
                        "Align":      align_icon if align_icon else "—",
                        "Paper":      "📄" if tr['ticker'] in _paper_open_tickers else "",
                        "_best_rr_sort": best_rr,
                    })

                    # ── Store live price for Paper Trading tab ──────────
                    st.session_state.setdefault("_paper_live_prices", {})[tr['ticker']] = live_price

                    # ── Paper trading: check exits then entries ──────────
                    if _pt_engine and live_price:
                        _rp_ts = f"{_rp_dt} {_rp_h:02d}:{_rp_m:02d}" if (_rp_mode and _rp_dt) else None
                        _cur_row = track_rows[-1]
                        # Check exits first on existing open trades
                        _exit_res = _pt_engine.check_exits(
                            tr['ticker'], live_price, exit_time=_rp_ts,
                            alignment=alignment,
                            vflow=_vflow_val,
                            best_rr=best_rr,
                        )
                        if _exit_res:
                            for _er in _exit_res:
                                _pnl_c = "#00e5a0" if _er['pnl_dollars'] >= 0 else "#ff4d6a"
                                _pt_results.append(
                                    f"📤 **{_er['ticker']}** closed — {_er['reason']} "
                                    f"| :{_pnl_c}[${_er['pnl_dollars']:+,.2f}] ({_er['pnl_pct']:+.1f}%)"
                                )
                        # Check entry conditions — only at 8:30 AM CST+
                        _pt_entry_allowed = auto_track_enabled  # True at 8:30 AM+ (both replay & live)
                        if _pt_entry_allowed:
                            _elig, _reason = _pt_engine.check_entry_conditions(_cur_row)
                        else:
                            _elig, _reason = False, "Waiting for 8:30 AM CST"
                        if _elig:
                            _dir_raw = tr.get('direction', 'LONG').upper()
                            _open_res = _pt_engine.open_trade(
                                ticker=tr['ticker'],
                                direction=_dir_raw,
                                entry_price=live_price,
                                stop_price=float(tr.get('stop', entry)),
                                t1_price=float(tr.get('t1', entry)),
                                t2_price=float(tr.get('t2', entry)),
                                trade_date=str(_rp_dt) if _rp_dt else str(date.today()),
                                scenario=tr.get('scenario', ''),
                                confidence=tr.get('confidence', ''),
                                entry_time=_rp_ts,
                                submit_to_alpaca=False if _rp_mode else None,
                            )
                            if _open_res.get("status") == "opened":
                                _pt_results.append(
                                    f"📥 **{tr['ticker']}** paper opened — "
                                    f"{_dir_raw} {_open_res['shares']} shares @ ${live_price:.2f}"
                                )
                            elif _open_res.get("status") == "duplicate":
                                pass  # already open, expected
                        else:
                            _pt_results.append(
                                f"⛔ **{tr['ticker']}** skipped — {_reason}"
                            )

                except Exception:
                    error_tickers.append(tr['ticker'])

            # ── Paper trading: session end check + display results ───
            if _pt_engine and _rp_mode and _rp_dt:
                _cst_tz = pytz.timezone("America/Chicago")
                _sim_cst_time = datetime(_rp_dt.year, _rp_dt.month, _rp_dt.day,
                                         _rp_time_min // 60, _rp_time_min % 60)
                _sim_cst_time = _cst_tz.localize(_sim_cst_time)
                def _price_getter(tkr):
                    for _r in track_rows:
                        if _r.get("Ticker") == tkr:
                            import re as _re_pt
                            _m = _re_pt.search(r'([\d.]+)', str(_r.get("Live $", "0")))
                            return float(_m.group(1)) if _m else 0.0
                    return 0.0
                _sess_res = _pt_engine.check_session_end(
                    _sim_cst_time, price_getter=_price_getter,
                    exit_time=f"{_rp_dt} {_rp_time_min//60:02d}:{_rp_time_min%60:02d}",
                )
                for _sr in _sess_res:
                    _pnl_c = "#00e5a0" if _sr['pnl_dollars'] >= 0 else "#ff4d6a"
                    _pt_results.append(
                        f"⏰ **{_sr['ticker']}** session-end close "
                        f"| :{_pnl_c}[${_sr['pnl_dollars']:+,.2f}] ({_sr['pnl_pct']:+.1f}%)"
                    )

            if _pt_results:
                with st.expander(f"📄 Paper Trading ({len(_pt_results)} events)", expanded=True):
                    for _pr in _pt_results:
                        st.markdown(_pr)

            # Flush in-memory paper trades to disk after replay cycle
            if _pt_engine:
                _pt_engine.flush_to_disk()

            if track_rows:
                # Sort by Best RR (highest first)
                track_rows_sorted = sorted(track_rows, key=lambda x: x.get("_best_rr_sort", 0), reverse=True)
                _fixed_cols = ["Ticker", "Dir", "Paper", "Live $", "Entry",
                               "Stop", "T1", "T2", "Best RR",
                               "Action", "Scen", "Entered",
                               "Wk Fib", "4H RSI",
                               "RVOL", "VWAP", "Prev VWAP", "V-Flow",
                               "10m", "30m", "4H", "Align", "News",
                               "RR(T1)", "RR(T2)",
                               "H-ATR", "OHLC", "Conf", "P&L"]
                track_df = pd.DataFrame(track_rows_sorted)
                if "_best_rr_sort" in track_df.columns:
                    track_df = track_df.drop(columns=["_best_rr_sort"])
                track_df = track_df[[c for c in _fixed_cols if c in track_df.columns]]
                # Color bias columns (basic — no direction context)
                def _color_trk_bias(val):
                    if not isinstance(val, str): return ""
                    v = val.upper()
                    if "BULL" in v: return "background-color: #0a3d1f; color: #00e5a0; font-weight: 700"
                    if "BEAR" in v: return "background-color: #3d0a1a; color: #ff4d6a; font-weight: 700"
                    if "CONFIRMED" in v: return "color: #00e5a0; font-weight: 700"
                    if "DIVERGED" in v: return "color: #f0c040; font-weight: 700"
                    if "ALIGNED" in v: return "background-color: #0a3d1f; color: #00e5a0; font-weight: 700"
                    if "PARTIAL" in v: return "background-color: #3d3a0a; color: #f0c040; font-weight: 700"
                    if "AGAINST" in v: return "background-color: #3d0a1a; color: #ff4d6a; font-weight: 700"
                    if "GOOD" in v: return "background-color: #0a3d1f; color: #00e5a0; font-weight: 700"
                    if "BAD" in v:  return "background-color: #3d0a1a; color: #ff4d6a; font-weight: 700"
                    return ""

                # Direction-aware RVOL/VWAP coloring
                def _color_rvol_vwap_row(row, is_long):
                    """Color RVOL + VWAP cells based on direction context.
                    LONG:  Above VWAP + RVOL>=1 = green; Above VWAP + RVOL<1 = yellow;
                           Below VWAP = red; Below VWAP + RVOL>=1 = orange
                    SHORT: Below VWAP + RVOL>=1 = green; Below VWAP + RVOL<1 = yellow;
                           Above VWAP = red; Above VWAP + RVOL>=1 = orange
                    """
                    styles = pd.Series([""] * len(row), index=row.index)
                    vwap_str = str(row.get("VWAP", "")).upper()
                    rvol_str = str(row.get("RVOL", ""))
                    # Parse RVOL numeric value
                    import re as _re
                    _rv_match = _re.search(r'([\d.]+)x?', rvol_str)
                    rvol_val = float(_rv_match.group(1)) if _rv_match else 0.0
                    vwap_above = vwap_str.startswith("A")
                    vwap_below = vwap_str.startswith("B")
                    # Determine signal quality
                    if is_long:
                        if vwap_above and rvol_val >= 1.0:
                            c = "background-color: #0a3d1f; color: #00e5a0; font-weight: 700"  # green — ideal
                        elif vwap_above and rvol_val < 1.0:
                            c = "background-color: #3d3a0a; color: #f0c040; font-weight: 700"  # yellow — caution
                        elif vwap_below and rvol_val >= 1.0:
                            c = "background-color: #3d2a0a; color: #ff9f43; font-weight: 700"  # orange — risky
                        elif vwap_below:
                            c = "background-color: #3d0a1a; color: #ff4d6a; font-weight: 700"  # red — avoid
                        else:
                            c = ""
                    else:  # SHORT
                        if vwap_below and rvol_val >= 1.0:
                            c = "background-color: #0a3d1f; color: #00e5a0; font-weight: 700"  # green — ideal
                        elif vwap_below and rvol_val < 1.0:
                            c = "background-color: #3d3a0a; color: #f0c040; font-weight: 700"  # yellow — caution
                        elif vwap_above and rvol_val >= 1.0:
                            c = "background-color: #3d2a0a; color: #ff9f43; font-weight: 700"  # orange — risky
                        elif vwap_above:
                            c = "background-color: #3d0a1a; color: #ff4d6a; font-weight: 700"  # red — avoid
                        else:
                            c = ""
                    if "RVOL" in row.index: styles["RVOL"] = c
                    if "VWAP" in row.index: styles["VWAP"] = c
                    if "Prev VWAP" in row.index:
                        pv = str(row.get("Prev VWAP", "")).upper()
                        if is_long:
                            styles["Prev VWAP"] = "color: #00e5a0; font-weight: 700" if pv.startswith("A") else ("color: #ff4d6a; font-weight: 700" if pv.startswith("B") else "")
                        else:
                            styles["Prev VWAP"] = "color: #00e5a0; font-weight: 700" if pv.startswith("B") else ("color: #ff4d6a; font-weight: 700" if pv.startswith("A") else "")
                    return styles

                _trk_bias_cols = [c for c in ["OHLC", "10m", "30m", "4H", "Align", "News", "V-Flow"] if c in track_df.columns]
                _trk_col_config = {
                    "Ticker": st.column_config.TextColumn("Ticker", width="small"),
                    "Dir":    st.column_config.TextColumn("Dir",    width="small"),
                    "Paper":  st.column_config.TextColumn("📄",     width=30),
                    "Action": st.column_config.TextColumn("Action"),
                    "Scen":   st.column_config.TextColumn("Scen",   width="small"),
                    "Live $": st.column_config.TextColumn("Live $", width="small"),
                    "Wk Fib": st.column_config.TextColumn("Wk Fib", width="small"),
                    "4H RSI": st.column_config.TextColumn("4H RSI", width=60),
                    "RVOL":   st.column_config.TextColumn("RVOL",   width="small"),
                    "VWAP":   st.column_config.TextColumn("VWAP"),
                    "Prev VWAP": st.column_config.TextColumn("Prev VWAP"),
                    "P&L":    st.column_config.TextColumn("P&L",    width="small"),
                    "RR(T1)": st.column_config.TextColumn("RR(T1)", width="small"),
                    "RR(T2)": st.column_config.TextColumn("RR(T2)", width="small"),
                    "Best RR":st.column_config.TextColumn("Best RR",width="small"),
                    "H-ATR":  st.column_config.TextColumn("H-ATR",  width="small"),
                    "Conf":   st.column_config.TextColumn("Conf",   width="small"),
                    "10m":    st.column_config.TextColumn("10m",    width=60),
                    "30m":    st.column_config.TextColumn("30m",    width=60),
                    "4H":     st.column_config.TextColumn("4H",     width=60),
                    "Align":  st.column_config.TextColumn("Align",  width=40),
                    "OHLC":   st.column_config.TextColumn("OHLC",   width="small"),
                    "News":   st.column_config.TextColumn("News",   width=40),
                    "V-Flow": st.column_config.TextColumn("V-Flow", width=40),
                }

                # ── Split tracker into 4 scenario tables, each split by Long/Short ──
                _scen_config = [
                    ("ONE", "✅ Opens Near Entry", "#00e5a0"),
                    ("OBE", "⚡ Opens Between Entry & Stop", "#4d9fff"),
                    ("OPS", "❌ Opens Past Stop", "#ff4d6a"),
                    ("GAP", "⚠️ Big Gap", "#f5c842"),
                ]

                def _render_trk_table(_df_sub, is_long=True):
                    _s = _df_sub.style
                    if _trk_bias_cols:
                        _s = _s.applymap(_color_trk_bias, subset=[c for c in _trk_bias_cols if c in _df_sub.columns])
                    # Direction-aware RVOL/VWAP coloring
                    _rv_vw_cols = [c for c in ["RVOL", "VWAP", "Prev VWAP"] if c in _df_sub.columns]
                    if _rv_vw_cols:
                        _s = _s.apply(_color_rvol_vwap_row, axis=1, is_long=is_long)
                    # Direction-aware Wk Fib coloring
                    if "Wk Fib" in _df_sub.columns:
                        def _color_wk_fib(val):
                            try:
                                v = float(str(val))
                                if is_long:
                                    if v <= 38: return "background-color: #0a3d1f; color: #00e5a0; font-weight: 700"
                                    if v >= 62: return "background-color: #3d0a1a; color: #ff4d6a; font-weight: 700"
                                else:
                                    if v >= 62: return "background-color: #0a3d1f; color: #00e5a0; font-weight: 700"
                                    if v <= 38: return "background-color: #3d0a1a; color: #ff4d6a; font-weight: 700"
                            except Exception:
                                pass
                            return ""
                        _s = _s.applymap(_color_wk_fib, subset=["Wk Fib"])
                    # Direction-aware 4H RSI coloring
                    if "4H RSI" in _df_sub.columns:
                        def _color_4h_rsi(val):
                            try:
                                v = float(str(val))
                                if is_long:
                                    return "background-color: #0a3d1f; color: #00e5a0; font-weight: 700" if v >= 50 else "background-color: #3d0a1a; color: #ff4d6a; font-weight: 700"
                                else:
                                    return "background-color: #0a3d1f; color: #00e5a0; font-weight: 700" if v < 50 else "background-color: #3d0a1a; color: #ff4d6a; font-weight: 700"
                            except Exception:
                                return ""
                        _s = _s.applymap(_color_4h_rsi, subset=["4H RSI"])
                    st.dataframe(_s, use_container_width=True,
                                 column_config=_trk_col_config,
                                 hide_index=True,
                                 height=min(40 * max(len(_df_sub), 1) + 38, 300))

                for _sc_key, _sc_label, _sc_color in _scen_config:
                    _sc_df = track_df[track_df["Scen"].str.strip().str.upper() == _sc_key].copy()
                    if _sc_df.empty:
                        continue
                    st.markdown(
                        f'<div style="margin:16px 0 6px 0;padding:8px 14px;border-left:4px solid {_sc_color};'
                        f'background:#0d0f17;border-radius:4px">'
                        f'<span style="font-size:17px;font-weight:800;color:{_sc_color}">{_sc_label}</span>'
                        f' <span style="font-size:13px;color:#6b7099">({len(_sc_df)} ticker{"s" if len(_sc_df) != 1 else ""})</span>'
                        f'</div>',
                        unsafe_allow_html=True,
                    )
                    _sc_long = _sc_df[_sc_df["Dir"].str.contains("LONG", case=False, na=False)]
                    _sc_short = _sc_df[_sc_df["Dir"].str.contains("SHORT", case=False, na=False)]
                    if not _sc_long.empty:
                        st.markdown(
                            f'<div style="font-size:14px;font-weight:800;color:#00e5a0;margin:6px 0 3px 14px">'
                            f'🟢 LONG ({len(_sc_long)})</div>',
                            unsafe_allow_html=True,
                        )
                        _render_trk_table(_sc_long, is_long=True)
                    if not _sc_short.empty:
                        st.markdown(
                            f'<div style="font-size:14px;font-weight:800;color:#ff4d6a;margin:6px 0 3px 14px">'
                            f'🔴 SHORT ({len(_sc_short)})</div>',
                            unsafe_allow_html=True,
                        )
                        _render_trk_table(_sc_short, is_long=False)

                # Show any rows that didn't match known scenarios
                _known_scens = {"ONE", "OBE", "OPS", "GAP"}
                _other_df = track_df[~track_df["Scen"].str.strip().str.upper().isin(_known_scens)]
                if not _other_df.empty:
                    st.markdown("**Other**")
                    _ot_long = _other_df[_other_df["Dir"].str.contains("LONG", case=False, na=False)]
                    _ot_short = _other_df[_other_df["Dir"].str.contains("SHORT", case=False, na=False)]
                    if not _ot_long.empty:
                        _render_trk_table(_ot_long, is_long=True)
                    if not _ot_short.empty:
                        _render_trk_table(_ot_short, is_long=False)

                # ── Manual Push to Paper Trading buttons ──────────────────
                if PAPER_TRADING_AVAILABLE and track_rows:
                    _paper_pt = _pt_engine if _pt_engine else PaperTrader(in_memory=False)
                    _paper_open = {t["ticker"] for t in _paper_pt.get_open_trades()}
                    _pushable = [r for r in track_rows if r.get("Ticker")]
                    if _pushable:
                        st.markdown(
                            '<div style="margin:12px 0 4px;font-size:12px;font-weight:700;color:#6b7099">'
                            '📄 Push to Paper Trading</div>',
                            unsafe_allow_html=True,
                        )
                        _pp_cols = st.columns(min(len(_pushable), 8))
                        for _pi, _pr in enumerate(_pushable[:8]):
                            _pp_tkr = _pr["Ticker"]
                            _pp_in_paper = _pp_tkr in _paper_open
                            _pp_label = f"📄 {_pp_tkr}" if _pp_in_paper else f"➡️ {_pp_tkr}"
                            with _pp_cols[_pi % len(_pp_cols)]:
                                if st.button(
                                    _pp_label,
                                    key=f"pp_push_{_pi}_{_pp_tkr}",
                                    use_container_width=True,
                                    disabled=_pp_in_paper,
                                    help="Already in paper trading" if _pp_in_paper else "Push to paper trading",
                                ):
                                    try:
                                        import re as _pp_re
                                        _pp_dir = _pr.get("Dir", "LONG").replace("🔄 ", "").strip().upper()
                                        _pp_entry = float(_pp_re.search(r'[\d.]+', str(_pr.get("Live $", _pr.get("Entry", "0")))).group())
                                        _pp_stop = float(_pp_re.search(r'[\d.]+', str(_pr.get("Stop", "0"))).group())
                                        _pp_t1 = float(_pp_re.search(r'[\d.]+', str(_pr.get("T1", "0"))).group())
                                        _pp_t2 = float(_pp_re.search(r'[\d.]+', str(_pr.get("T2", "0"))).group())
                                        _pp_res = _paper_pt.open_trade(
                                            ticker=_pp_tkr,
                                            direction="LONG" if "LONG" in _pp_dir else "SHORT",
                                            entry_price=_pp_entry,
                                            stop_price=_pp_stop,
                                            t1_price=_pp_t1,
                                            t2_price=_pp_t2,
                                            trade_date=str(date.today()),
                                            scenario=_pr.get("Scen", ""),
                                            confidence=_pr.get("Conf", ""),
                                            submit_to_alpaca=not _rp_mode,
                                        )
                                        if _pp_res.get("status") == "opened":
                                            st.success(f"📄 {_pp_tkr} pushed to paper — {_pp_res['shares']} shares @ ${_pp_entry:.2f}")
                                        elif _pp_res.get("status") == "duplicate":
                                            st.info(f"{_pp_tkr} already has open paper trade")
                                        st.rerun()
                                    except Exception as _pp_err:
                                        st.error(f"Failed: {_pp_err}")

                # ── Flip Direction buttons ──────────────────────────────
                if track_rows:
                    st.markdown(
                        '<div style="margin:12px 0 4px;font-size:12px;font-weight:700;color:#6b7099">'
                        '🔄 Flip Direction (mirror Stop / T1 / T2 around Entry)</div>',
                        unsafe_allow_html=True,
                    )
                    _flip_cols = st.columns(min(len(track_rows), 8))
                    for _fi, _fr in enumerate(track_rows[:8]):
                        _fl_tkr = _fr["Ticker"]
                        _fl_dir = _fr.get("Dir", "").replace("🔄 ", "").strip().upper()
                        _fl_new_dir = "SHORT" if "LONG" in _fl_dir else "LONG"
                        _fl_icon = "🔴" if _fl_new_dir == "SHORT" else "🟢"
                        with _flip_cols[_fi % len(_flip_cols)]:
                            if st.button(
                                f"{_fl_icon} {_fl_tkr}→{_fl_new_dir}",
                                key=f"flip_{_fi}_{_fl_tkr}",
                                use_container_width=True,
                                help=f"Flip {_fl_tkr} from {_fl_dir} to {_fl_new_dir}",
                            ):
                                # Find and update the tracking entry
                                for _ft in st.session_state.get("tracking_trades", []):
                                    if _ft["ticker"] == _fl_tkr:
                                        _ft_entry = _ft["entry"]
                                        _ft["direction"] = _fl_new_dir
                                        _ft["stop"] = round(2 * _ft_entry - _ft["stop"], 2)
                                        _ft["t1"]   = round(2 * _ft_entry - _ft["t1"], 2)
                                        _ft["t2"]   = round(2 * _ft_entry - _ft["t2"], 2)
                                        # Recompute RR
                                        def _fl_rr(e, s, t, d):
                                            try:
                                                risk = (e - s) if d == "LONG" else (s - e)
                                                rew  = (t - e) if d == "LONG" else (e - t)
                                                return round(rew / risk, 2) if risk > 0 else 0.0
                                            except: return 0.0
                                        _ft["rr_t1"]  = _fl_rr(_ft_entry, _ft["stop"], _ft["t1"], _fl_new_dir)
                                        _ft["rr_t2"]  = _fl_rr(_ft_entry, _ft["stop"], _ft["t2"], _fl_new_dir)
                                        _ft["best_rr"] = max(_ft["rr_t1"], _ft["rr_t2"])
                                        print(f"🔄 Manual flip {_fl_tkr}: {_fl_dir} → {_fl_new_dir} | Stop=${_ft['stop']}, T1=${_ft['t1']}, T2=${_ft['t2']}")
                                        break
                                st.rerun()

            # ── 30-MIN TRACKING SUMMARY TO TELEGRAM ──
            if track_rows and TELEGRAM_ENABLED:
                _summary_key = f"_trk_summary_last_{date.today()}"
                _last_summary = st.session_state.get(_summary_key)
                _send_summary = (
                    _last_summary is None
                    or (_now - _last_summary) >= timedelta(minutes=29, seconds=30)
                )
                if _send_summary:
                    try:
                        _ts = _now.strftime("%m/%d %I:%M %p CST")
                        _lines = [f"📋 <b>TRACKING SUMMARY</b>  —  {_ts}\n"]
                        _lines.append("<pre>")
                        _lines.append(f"{'Ticker':<8} {'Dir':<6} {'H-ATR':<8} {'RVOL':<8} {'VWAP':<8} {'PrVWAP':<8} {'OHLC':<10} {'Action'}")
                        _lines.append(f"{'─'*8} {'─'*6} {'─'*8} {'─'*8} {'─'*8} {'─'*8} {'─'*10} {'─'*20}")
                        for _r in sorted(track_rows, key=lambda x: x.get("Ticker", "")):
                            _vw_short = "A" if _r.get('VWAP','').startswith('A') else ("B" if _r.get('VWAP','').startswith('B') else "N/A")
                            _pvw_short = "A" if _r.get('Prev VWAP','').startswith('A') else ("B" if _r.get('Prev VWAP','').startswith('B') else "N/A")
                            _rv_short = _r.get('RVOL', 'N/A')
                            _lines.append(f"{_r.get('Ticker',''):<8} {_r.get('Dir',''):<6} {_r.get('H-ATR','N/A'):<8} {_rv_short:<8} {_vw_short:<8} {_pvw_short:<8} {_r.get('OHLC','N/A'):<10} {_r.get('Action','')}")
                        _lines.append("</pre>")
                        _summary_msg = "\n".join(_lines)
                        _url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
                        _resp = requests.post(_url, json={
                            "chat_id": TELEGRAM_CHAT_ID,
                            "text": _summary_msg,
                            "parse_mode": "HTML"
                        }, timeout=5)
                        if _resp.status_code == 200:
                            st.session_state[_summary_key] = _now
                            print(f"📋 Tracking summary sent at {_ts} — {len(track_rows)} tickers")
                        else:
                            print(f"⚠️ Tracking summary send failed: {_resp.status_code}")
                    except Exception as _se:
                        print(f"⚠️ Tracking summary error: {_se}")

            if error_tickers:
                st.caption(f"⚠️ Could not fetch data for: {', '.join(error_tickers)}")

            if '_pt_engine' in locals() and _pt_engine:
                try:
                    _pt_engine.close()
                except Exception:
                    pass
            if '_paper_query_pt' in locals() and _paper_query_pt:
                try:
                    _paper_query_pt.close()
                except Exception:
                    pass
            trim_memory()

        _live_tracker_fragment()

        # Per-row delete buttons (outside fragment so they don't auto-refresh)
        st.markdown("**Quick Actions:**")
        del_cols = st.columns(min(len(tracked), 8))
        for di, tr in enumerate(tracked[:8]):
            with del_cols[di % len(del_cols)]:
                if st.button(f"🗑️ {tr['ticker']}", key=f"del_trk_{di}_{tr['ticker']}", use_container_width=True):
                    st.session_state["tracking_trades"].pop(di)
                    st.rerun()

    # ── SCENARIO TABLES WITH BIAS (renders after Tracking) ──────────────────
    st.markdown("---")
    if display_cols and (near_entry_rows or between_rows or past_stop_rows):
        _refresh_scenario_tables()

    # ── CHECK OPEN PRICES TABLES (after tracking & scenarios) ──
    st.markdown("### ☀️ CHECK OPEN PRICES")
    st.info("See tables above — they auto-refresh every 5 minutes with live prices.")

    # ── ENTRY CONFIRMATION TABLE (after 9 AM CST) ────────────────
    # ── Bias date: plan_date + 1 (if plan_date is today, bias_date = today) ──
    st.markdown("---")
    bias_date = _bias_date_early  # Already computed above
    if bias_date != _date_type.today():
        _bias_label = "replay date" if _in_replay else "plan date + 1"
        st.info(f"📆 Bias date: **{bias_date.strftime('%A, %B %d, %Y')}** ({_bias_label})")
    # Override time gates when backtesting a past date
    if _bias_target is not None:
        early_confirmation_enabled = True
        entry_confirmation_enabled = True
        # In replay mode, pre_confirmation gates already set by _replay_time_min
        # In non-replay backtest, show pre-confirmation as frozen
        if not _in_replay:
            pre_confirmation_enabled = True
            pre_confirmation_live = False

    # When backtesting a past date or replaying, also include tracked trades as candidates
    # Use ALL table_rows so every ticker appears in pre-confirmation & confirmation tables
    _confirmation_candidates = list(table_rows) if table_rows else []
    # Deduplicate: tracked trades may not be in table_rows
    if _bias_target is not None:
        tracked_as_rows = []
        for tr in st.session_state.get("tracking_trades", []):
            tkr = tr.get("ticker", "?")
            # Avoid duplicates with near_entry_rows
            if any(r.get("Ticker") == tkr for r in _confirmation_candidates):
                continue
            _rr1_tr = tr.get("rr_t1", 0)
            _rr2_tr = tr.get("rr_t2", 0)
            _best_rr_tr = tr.get("best_rr", max(_rr1_tr, _rr2_tr))
            tracked_as_rows.append({
                "Ticker": tkr,
                "Direction": tr.get("direction", "LONG"),
                "Option": "CALL" if tr.get("direction") == "LONG" else "PUT",
                "Open": f"${tr.get('entry', 0):.2f}" if isinstance(tr.get('entry'), (int, float)) else tr.get('entry', ''),
                "Stop": f"${tr.get('stop', 0):.2f}" if isinstance(tr.get('stop'), (int, float)) else tr.get('stop', ''),
                "T1": f"${tr.get('t1', 0):.2f}" if isinstance(tr.get('t1'), (int, float)) else tr.get('t1', ''),
                "T2": f"${tr.get('t2', 0):.2f}" if isinstance(tr.get('t2'), (int, float)) else tr.get('t2', ''),
                "RR(T1)": f"{_rr1_tr:.2f}x",
                "RR(T2)": f"{_rr2_tr:.2f}x",
                "Best RR": f"{_best_rr_tr:.2f}x",
                "Confidence": tr.get("confidence", "N/A"),
                "ATR": f"${tr.get('atr', '')}" if tr.get('atr') else "",
                "Scenario": tr.get("scenario", ""),
                "_best_rr_sort_main": _best_rr_tr,
            })
        _confirmation_candidates.extend(tracked_as_rows)
        
        # ── AUTO-MOVE BIG GAP SCENARIOS WITH CONFIRMED ALIGNMENT TO TRACKING ──
        if secondary_tracking_candidates:
            sec_enriched = _enrich_rows_with_bias(secondary_tracking_candidates)
            for gap_trade in sec_enriched:
                alignment = gap_trade.get("Alignment", "").upper()
                # Only auto-move if all biases are aligned (CONFIRMED)
                if "CONFIRMED" in alignment:
                    # GAP: skip unless bias direction flipped from plan direction
                    try:
                        import paper_config as _pc_gap3
                        if getattr(_pc_gap3, "GAP_REQUIRE_FLIP", False):
                            _gt_b10 = (gap_trade.get("10m Bias") or "").upper()
                            _gt_b30 = (gap_trade.get("30m Bias") or "").upper()
                            _gt_b4h = (gap_trade.get("4H Bias") or "").upper()
                            if "BULLISH" in _gt_b10 and "BULLISH" in _gt_b30 and "BULLISH" in _gt_b4h:
                                _gt_bias_dir = "LONG"
                            elif "BEARISH" in _gt_b10 and "BEARISH" in _gt_b30 and "BEARISH" in _gt_b4h:
                                _gt_bias_dir = "SHORT"
                            else:
                                _gt_bias_dir = None
                            if _gt_bias_dir and _gt_bias_dir == gap_trade.get("Direction", "LONG"):
                                continue  # Bias hasn't flipped — skip
                    except Exception:
                        pass
                    # Check if not already in confirmation candidates
                    gap_ticker = gap_trade.get("Ticker", "?")
                    if not any(r.get("Ticker") == gap_ticker for r in _confirmation_candidates):
                        _confirmation_candidates.append(gap_trade)
                        print(f"🎯 Auto-moved BIG GAP {gap_ticker} to Tracking (Alignment: {alignment})")
        
        if not _confirmation_candidates:
            st.warning("📋 No trades to evaluate. Run the scanner first or add trades to tracking.")

    # ── SECONDARY TRACKING CANDIDATES: Big Gap scenarios (display at 8:30 AM) ──
    # Filter out any gap scenarios that were auto-moved to tracking (CONFIRMED alignment)
    display_gap_candidates = []
    if secondary_tracking_candidates:
        sec_enriched_for_filter = _enrich_rows_with_bias(secondary_tracking_candidates)
        for gap_trade in sec_enriched_for_filter:
            alignment = gap_trade.get("Alignment", "").upper()
            # Only display gap scenarios that don't have CONFIRMED alignment (those were auto-moved)
            if "CONFIRMED" not in alignment:
                display_gap_candidates.append(gap_trade)
    
    if display_gap_candidates:
        st.markdown("---")
        st.markdown("### ⚠️ BIG GAP SCENARIOS AT 8:30 AM")
        st.markdown("**Gap Up/Down scenarios** — Entry (from plan notes) | Live Price | 10m/30m/4H Bias · Auto-track only if near entry price")
        
        @st.fragment(run_every=timedelta(seconds=60))
        def _gap_scenarios_fragment():
            """Auto-refresh gap scenarios every 1 min with live prices & bias."""
            if st.session_state.get("_debug_fragments"):
                print(f"[FRAGMENT] _gap_scenarios_fragment (60s) fired at {datetime.now().strftime('%H:%M:%S')}")
            _now = get_cst_now()
            # ── Stop refreshing after 3:00 PM CST (live mode only) ──
            if not _in_replay and is_after_market_time(15, 0):
                st.info("⏹️ Gap scenario refresh stopped — market closes at 3:00 PM CST.")
                return
            st.caption(f"🔄 Auto-refreshes every 1 min · Last refresh: {_now.strftime('%I:%M:%S %p CST')}")
            
            sec_enriched = _enrich_rows_with_bias(display_gap_candidates)

            # ── AUTO-TRACK gap tickers that become CONFIRMED on refresh ──
            for _gt in sec_enriched:
                _gt_tkr = _gt.get("Ticker", "?")
                if _is_already_tracked(_gt_tkr):
                    continue
                _gt_align = (_gt.get("Alignment") or "").upper()
                if "CONFIRMED" not in _gt_align:
                    continue
                _gb10 = (_gt.get("10m Bias") or "").upper()
                _gb30 = (_gt.get("30m Bias") or "").upper()
                _gb4h = (_gt.get("4H Bias") or "").upper()
                if "BULLISH" in _gb10 and "BULLISH" in _gb30 and "BULLISH" in _gb4h:
                    _gt_dir = "LONG"
                elif "BEARISH" in _gb10 and "BEARISH" in _gb30 and "BEARISH" in _gb4h:
                    _gt_dir = "SHORT"
                else:
                    continue
                # GAP: skip unless bias direction flipped from plan direction
                _gt_orig = _gt.get("Direction", "LONG")
                try:
                    import paper_config as _pc_gap4
                    if getattr(_pc_gap4, "GAP_REQUIRE_FLIP", False) and _gt_dir == _gt_orig:
                        continue  # Bias hasn't flipped — skip
                except Exception:
                    pass
                def _gpf(v):
                    try: return float(str(v).replace("$", "").replace(",", ""))
                    except: return 0.0
                _gt_open = _gpf(_gt.get("Open") or _gt.get("Entry") or "0")
                _gt_stop = _gpf(_gt.get("Stop") or "0")
                _gt_t1   = _gpf(_gt.get("T1") or "0")
                _gt_t2   = _gpf(_gt.get("T2") or "0")
                _gt_orig = _gt.get("Direction", "LONG")
                if _gt_dir != _gt_orig and _gt_open > 0:
                    _gt_stop = round(2 * _gt_open - _gt_stop, 2)
                    _gt_t1   = round(2 * _gt_open - _gt_t1, 2)
                    _gt_t2   = round(2 * _gt_open - _gt_t2, 2)
                try:
                    save_trade(
                        ticker=_gt_tkr, direction=_gt_dir,
                        entry_price=_gt_open, stop_loss=_gt_stop,
                        target1=_gt_t1, target2=_gt_t2, open_price=_gt_open,
                        scenario=_gt.get("Scenario"),
                        confidence=_gt.get("Confidence"),
                        notes=f"Auto-tracked GAP CONFIRMED on refresh ({_gt_dir})",
                        ohlc_signal=_gt.get('OHLC Signal', _gt.get('ohlc_signal'))
                    )
                except Exception as _ge:
                    print(f"⚠️ Gap auto-save failed for {_gt_tkr}: {_ge}")
                st.session_state["tracking_trades"].append(_make_track_entry({
                    **_gt, "Direction": _gt_dir,
                    "Stop": f"${_gt_stop:.2f}", "T1": f"${_gt_t1:.2f}", "T2": f"${_gt_t2:.2f}"
                }))
                print(f"🎯 Gap refresh: auto-tracked CONFIRMED {_gt_tkr} as {_gt_dir}")
                # Send NEW TICKER alert to Telegram
                if TELEGRAM_ENABLED:
                    try:
                        _gt_ohlc = _gt.get('OHLC Signal', _gt.get('ohlc_signal', 'N/A'))
                        _new_msg = (
                            f"🆕 <b>NEW TICKER TRACKED</b>\n"
                            f"<pre>Ticker : {_gt_tkr}\n"
                            f"Dir    : {_gt_dir}\n"
                            f"Entry  : ${_gt_open:.2f}\n"
                            f"Stop   : ${_gt_stop:.2f}\n"
                            f"T1     : ${_gt_t1:.2f}\n"
                            f"T2     : ${_gt_t2:.2f}\n"
                            f"OHLC   : {_gt_ohlc}\n"
                            f"Source : Gap refresh (CONFIRMED)</pre>"
                        )
                        _url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
                        requests.post(_url, json={"chat_id": TELEGRAM_CHAT_ID, "text": _new_msg, "parse_mode": "HTML"}, timeout=5)
                    except Exception:
                        pass
            
            if sec_enriched:
                # Build gap scenario table with: Ticker, Entry (from notes), Current Price, Direction, Stop, T1, T2, 10m Bias, 30m Bias, 4H Bias, Alignment, Notes
                gap_rows_display = []
                for trade in sec_enriched:
                    try:
                        # Extract entry price from Notes field
                        # Format: "Gap down >$1.86 below $372.04 — wait for bounce near $370.00"
                        notes = trade.get("Notes", "")
                        entry_price = None
                        
                        if notes and isinstance(notes, str):
                            # Try to extract price after "near" keyword
                            if "near" in notes.lower():
                                parts = notes.lower().split("near")
                                if len(parts) > 1:
                                    # Extract numbers from the part after "near"
                                    import re
                                    prices = re.findall(r'\$?([\d.]+)', parts[-1])
                                    if prices:
                                        try:
                                            entry_price = float(prices[0])
                                        except:
                                            pass
                            
                            # Fallback: extract last price mentioned in notes
                            if entry_price is None:
                                import re
                                prices = re.findall(r'\$?([\d.]+)', notes)
                                if prices:
                                    try:
                                        entry_price = float(prices[-1])
                                    except:
                                        pass
                        
                        # If still no entry, use Open price
                        if entry_price is None:
                            entry_price = float(str(trade.get("Open", "$0")).replace("$", ""))
                        
                        open_price = float(str(trade.get("Open", "$0")).replace("$", ""))
                        ticker = trade.get("Ticker", "?")
                        
                        gap_rows_display.append({
                            "Ticker": ticker,
                            "Entry": f"${entry_price:.2f}",  # Gap entry from notes
                            "Current Price": f"${open_price:.2f}",  # Will update live
                            "Direction": trade.get("Direction", "?"),
                            "Stop": trade.get("Stop", "?"),
                            "T1": trade.get("T1", "?"),
                            "T2": trade.get("T2", "?"),
                            "RR(T1)": trade.get("RR(T1)", "N/A"),
                            "RR(T2)": trade.get("RR(T2)", "N/A"),
                            "Best RR": trade.get("Best RR", "N/A"),
                            "Prev OHLC Avg": trade.get("Prev OHLC Avg", "N/A"),
                            "OHLC Signal": trade.get("OHLC Signal", "N/A"),
                            "10m Bias": trade.get("10m Bias", "N/A"),
                            "30m Bias": trade.get("30m Bias", "N/A"),
                            "4H Bias": trade.get("4H Bias", "N/A"),
                            "Alignment": trade.get("Alignment", "N/A"),
                            "Notes": notes,
                        })
                    except:
                        pass
                
                if gap_rows_display:
                    df_gap = pd.DataFrame(gap_rows_display)
                    
                    # Reorder columns: Ticker, Direction, Current Price, Entry, Stop, T1, T2, Best RR, Options Strategy, ...
                    cols_order = ["Ticker", "Direction", "Current Price", "Entry", "Stop", "T1", "T2", "Best RR", "RR(T1)", "RR(T2)", "Prev OHLC Avg", "OHLC Signal", "10m Bias", "30m Bias", "4H Bias", "Alignment", "Notes"]
                    cols_to_show = [c for c in cols_order if c in df_gap.columns]
                    df_gap_display = df_gap[cols_to_show]
                    
                    # Color styling for bias columns
                    def _color_gap_bias(val):
                        if not isinstance(val, str): return ""
                        v = val.upper()
                        if "BULLISH" in v or "BULL" in v: return "background-color: #0a3d1f; color: #00e5a0; font-weight: 700"
                        if "BEARISH" in v or "BEAR" in v: return "background-color: #3d0a1a; color: #ff4d6a; font-weight: 700"
                        if "CONFIRMED" in v: return "color: #00e5a0; font-weight: 700"
                        if "DIVERGED" in v: return "color: #f0c040; font-weight: 700"
                        if "NEUTRAL" in v: return "color: #a0a0a0; font-weight: 700"
                        return ""
                    
                    bias_cols = ["10m Bias", "30m Bias", "4H Bias", "Alignment", "OHLC Signal"]
                    styled_gap = df_gap_display.style.applymap(_color_gap_bias, subset=[c for c in bias_cols if c in df_gap_display.columns])
                    st.dataframe(styled_gap, use_container_width=True, height=min(40 * max(len(df_gap_display), 1) + 38, 400))
        
        # ── CALL THE FRAGMENT TO DISPLAY GAP TABLE ──
        _gap_scenarios_fragment()
        
        # Track buttons for secondary candidates (outside fragment)
        st.markdown("**Track if near entry (manual):**")
        sec_enriched = _enrich_rows_with_bias(display_gap_candidates)
        btn_cols = st.columns(min(len(sec_enriched), 8))
        for idx, trade in enumerate(sec_enriched[:8]):
            tkr = trade.get("Ticker", "?")
            already = _is_already_tracked(tkr)
            near_entry = _is_near_entry_price(trade)
            label = f"🎯 {tkr}" if already else (f"📌 {tkr}*" if near_entry else f"⏸️ {tkr}")
            with btn_cols[idx % len(btn_cols)]:
                if st.button(label, key=f"trk_sec_{idx}_{tkr}", use_container_width=True, disabled=already):
                    try:
                        entry_val = float(str(trade.get("Open", "$0")).replace("$", ""))
                        stop_val = float(str(trade.get("Stop", "$0")).replace("$", ""))
                        t1_val = float(str(trade.get("T1", "$0")).replace("$", ""))
                        t2_val = float(str(trade.get("T2", "$0")).replace("$", ""))
                        saved, _, _ = save_trade(
                            ticker=trade.get("Ticker"),
                            direction=trade.get("Direction"),
                            entry_price=entry_val,
                            stop_loss=stop_val,
                            target1=t1_val,
                            target2=t2_val,
                            open_price=entry_val,
                            scenario=trade.get("Scenario"),
                            confidence=trade.get("Confidence"),
                            notes=f"Sector swing: {trade.get('Scenario', 'unknown')} (Confidence: {trade.get('Confidence', 'N/A')})"
                        )
                        if saved:
                            st.success(f"✅ {tkr} saved to Trade Tracker")
                    except Exception as e:
                        st.error(f"Failed to save {tkr}: {e}")
                    st.session_state["tracking_trades"].append(_make_track_entry(trade))
                    st.rerun()
        st.caption("(*) = within 0.5% of entry price. (⏸️) = outside entry range, tap to track anyway.")

    # ── PRE-CONFIRMATION TABLE — 8:00–8:29 AM CST (auto-refresh, freezes at 8:30) ──
    _replay_clock(8 * 60, "Pre-market bias preview — auto-refreshes until 8:30 AM")
    _pre_conf_toggle = st.checkbox(
        "Show Pre-Confirmation Table (8:00–8:29 AM)",
        value=st.session_state.get("show_pre_confirmation", True),
        key="show_pre_confirmation",
        help="Live-updating bias preview before the 8:30 snapshot. Helps you see which tickers are trending CONFIRMED vs DIVERGED.",
    )
    if _pre_conf_toggle and _confirmation_candidates:
        if pre_confirmation_enabled:
            if pre_confirmation_live:
                st.markdown("### 🔎 PRE-CONFIRMATION TABLE (8:00–8:29 AM CST)")
                st.markdown("Live bias preview — **auto-refreshes every 1 min** · Freezes at 8:30 AM CST when confirmation snapshot locks in")
            else:
                st.markdown("### 🔒 PRE-CONFIRMATION TABLE (Frozen)")
                st.markdown("Bias snapshot from pre-market window — 8:30 AM confirmation table below is the final decision")

            @st.fragment(run_every=timedelta(seconds=60) if pre_confirmation_live else None)
            def _pre_confirmation_fragment():
                """Auto-refresh pre-confirmation every 1 min until 8:30 AM."""
                if st.session_state.get("_debug_fragments"):
                    print(f"[FRAGMENT] _pre_confirmation_fragment (60s) fired at {datetime.now().strftime('%H:%M:%S')}")

                # Check if we're in the live refresh window
                if _in_replay:
                    _still_pre = _replay_time_min < 8 * 60 + 30
                else:
                    _still_pre = not is_after_market_time(8, 30)

                _now_pre = get_cst_now()
                if _still_pre:
                    st.caption(f"🔄 Auto-refreshes every 1 min · Last refresh: {_now_pre.strftime('%I:%M:%S %p CST')}")
                else:
                    st.caption("🔒 Frozen at 8:30 AM — Confirmation Table below has the final decision")

                pre_rows = []
                for trade in _confirmation_candidates:
                    ticker = trade.get("Ticker", "?")
                    direction = trade.get("Direction")
                    bias_pre = get_830_bias_eval(ticker, direction, target_date=_bias_target)

                    b10 = bias_pre.get("bias_10m", "N/A") if bias_pre else "N/A"
                    b30 = bias_pre.get("bias_30m", "N/A") if bias_pre else "N/A"
                    b4h = bias_pre.get("bias_4h", "N/A") if bias_pre else "N/A"
                    aln = bias_pre.get("alignment", "N/A") if bias_pre else "N/A"
                    opn = bias_pre.get("today_open", "N/A") if bias_pre else "N/A"
                    cur = bias_pre.get("current_price", "N/A") if bias_pre else "N/A"
                    icon = "✅" if aln == "CONFIRMED" else ("⚠️" if aln == "DIVERGED" else "🔄")

                    # Derive short scenario label
                    _scn_raw = trade.get("Scenario", "")
                    if "OPENS NEAR ENTRY" in _scn_raw.upper():
                        _scn_pre = "ONE"
                    elif "OPENS BETWEEN" in _scn_raw.upper():
                        _scn_pre = "OBE"
                    elif "OPENS PAST STOP" in _scn_raw.upper():
                        _scn_pre = "OPS"
                    elif "GAP" in _scn_raw.upper():
                        _scn_pre = "GAP"
                    else:
                        _scn_pre = _scn_raw[:10]

                    # ── Auto-fix: ensure Stop/T1/T2 align with direction ──
                    # LONG: Stop < Entry < T1 < T2   |   SHORT: Stop > Entry > T1 > T2
                    _pre_entry = opn if isinstance(opn, (int, float)) else 0
                    _pre_stop = trade.get("Stop")
                    _pre_t1 = trade.get("T1")
                    _pre_t2 = trade.get("T2")
                    if _pre_entry > 0:
                        def _ppre(v):
                            try: return float(str(v).replace("$","").replace(",",""))
                            except: return 0.0
                        _ps = _ppre(_pre_stop); _pt1 = _ppre(_pre_t1); _pt2 = _ppre(_pre_t2)
                        _vals = sorted([v for v in [_ps, _pt1, _pt2] if v > 0])
                        if len(_vals) == 3:
                            if direction == "LONG":
                                _below = [v for v in _vals if v < _pre_entry]
                                _above = [v for v in _vals if v >= _pre_entry]
                                if not _below:
                                    _below = [round(2 * _pre_entry - _vals[2], 2)]
                                    _above = _vals[:2]
                                if len(_above) < 2:
                                    _above.append(round(2 * _pre_entry - _below[0], 2))
                                    _above.sort()
                                _below.sort(); _above.sort()
                                _ps = _below[-1]; _pt1 = _above[0]; _pt2 = _above[-1] if len(_above) > 1 else _above[0]
                            else:  # SHORT
                                _above = [v for v in _vals if v > _pre_entry]
                                _below = [v for v in _vals if v <= _pre_entry]
                                if not _above:
                                    _above = [round(2 * _pre_entry - _vals[0], 2)]
                                    _below = _vals[1:]
                                if len(_below) < 2:
                                    _below.append(round(2 * _pre_entry - _above[-1], 2))
                                    _below.sort(reverse=True)
                                _above.sort(reverse=True); _below.sort(reverse=True)
                                _ps = _above[0]; _pt1 = _below[0]; _pt2 = _below[-1] if len(_below) > 1 else _below[0]
                            _pre_stop = f"${_ps:.2f}"; _pre_t1 = f"${_pt1:.2f}"; _pre_t2 = f"${_pt2:.2f}"

                    pre_rows.append({
                        "Ticker": ticker,
                        "Direction": direction,
                        "Scen": _scn_pre,
                        "Open": f"${opn:.2f}" if isinstance(opn, (int, float)) else opn,
                        "Current": f"${cur:.2f}" if isinstance(cur, (int, float)) else cur,
                        "Stop": _pre_stop,
                        "T1": _pre_t1,
                        "T2": _pre_t2,
                        "10m Bias": b10,
                        "30m Bias": b30,
                        "4H Bias": b4h,
                        "Alignment": f"{icon} {aln}",
                        "Confidence": trade.get("Confidence"),
                    })

                if pre_rows:
                    # Sort: CONFIRMED first, then DIVERGED, then others
                    def _pre_sort_key(r):
                        a = r.get("Alignment", "")
                        if "CONFIRMED" in a: return 0
                        if "DIVERGED" in a: return 2
                        return 1
                    pre_rows.sort(key=_pre_sort_key)

                    pre_df = pd.DataFrame(pre_rows)
                    bias_cols_pre = ["10m Bias", "30m Bias", "4H Bias", "Alignment"]
                    cols_pre = [c for c in bias_cols_pre if c in pre_df.columns]

                    def _style_pre_row(row):
                        """Direction-aware bias coloring — highlight conflicts."""
                        d = str(row.get("Direction", "")).upper()
                        is_long = "LONG" in d
                        styles = [""] * len(row)
                        for i, col in enumerate(row.index):
                            val = str(row[col]).upper() if row[col] else ""
                            if col in ("10m Bias", "30m Bias", "4H Bias"):
                                if "BULLISH" in val:
                                    if is_long:
                                        styles[i] = "background-color: #0a3d1f; color: #00e5a0; font-weight: 700"
                                    else:
                                        # SHORT but bias is BULLISH → conflict
                                        styles[i] = "background-color: #4a3d0a; color: #ffa500; font-weight: 700; border: 2px solid #ffa500"
                                elif "BEARISH" in val:
                                    if not is_long:
                                        styles[i] = "background-color: #3d0a1a; color: #ff4d6a; font-weight: 700"
                                    else:
                                        # LONG but bias is BEARISH → conflict
                                        styles[i] = "background-color: #4a3d0a; color: #ffa500; font-weight: 700; border: 2px solid #ffa500"
                                elif "NEUTRAL" in val:
                                    styles[i] = "color: #a0a0a0; font-weight: 700"
                            elif col == "Alignment":
                                if "CONFIRMED" in val:
                                    styles[i] = "color: #00e5a0; font-weight: 700"
                                elif "DIVERGED" in val:
                                    styles[i] = "color: #f0c040; font-weight: 700"
                        return styles

                    styled_pre = pre_df.style.apply(_style_pre_row, axis=1)
                    st.dataframe(styled_pre, use_container_width=True,
                                 height=min(40 * max(len(pre_df), 1) + 38, 400))

                    _conf_count = sum(1 for r in pre_rows if "CONFIRMED" in r.get("Alignment", ""))
                    _div_count = sum(1 for r in pre_rows if "DIVERGED" in r.get("Alignment", ""))
                    _conflict_count = sum(1 for r in pre_rows if "CONFIRMED" not in r.get("Alignment", "") and "DIVERGED" not in r.get("Alignment", ""))
                    st.caption(f"Preview: {_conf_count} trending CONFIRMED · {_div_count} trending DIVERGED · {_conflict_count} pending")

                    # ── Telegram: send pre-confirmation summary once at ~8:29 AM ──
                    _pre_tg_key = f"_pre_conf_tg_sent_{get_cst_now().date()}"
                    _send_pre_tg = False
                    if _in_replay:
                        _send_pre_tg = _replay_time_min >= 8 * 60 + 30 and not st.session_state.get(_pre_tg_key, False)
                    else:
                        _send_pre_tg = not _still_pre and not st.session_state.get(_pre_tg_key, False)
                    if _send_pre_tg and TELEGRAM_ENABLED and pre_rows:
                        try:
                            _pre_now = get_cst_now()
                            _pre_lines = [f"🌅 <b>8:20 AM TABLE LOCK SUMMARY</b>\n📅 {_pre_now.strftime('%B %d, %Y')} · {_pre_now.strftime('%#I:%M %p CST')}\n"]
                            for _pr in pre_rows:
                                _dir_e = "🟢" if "LONG" in str(_pr.get("Direction","")).upper() else "🔴"
                                _aln_e = "✅" if "CONFIRMED" in _pr.get("Alignment","") else ("⚠️" if "DIVERGED" in _pr.get("Alignment","") else "🔄")
                                # Check if bias direction conflicts with trade direction
                                _pr_dir = str(_pr.get("Direction", "")).upper()
                                _pr_b10 = str(_pr.get("10m Bias", "")).upper()
                                _pr_b30 = str(_pr.get("30m Bias", "")).upper()
                                _pr_b4h = str(_pr.get("4H Bias", "")).upper()
                                _bias_dirs = []
                                for _b in [_pr_b10, _pr_b30, _pr_b4h]:
                                    if "BULLISH" in _b: _bias_dirs.append("LONG")
                                    elif "BEARISH" in _b: _bias_dirs.append("SHORT")
                                _misalign_note = ""
                                if _bias_dirs:
                                    _majority_bias = max(set(_bias_dirs), key=_bias_dirs.count)
                                    if ("LONG" in _pr_dir and _majority_bias == "SHORT") or ("SHORT" in _pr_dir and _majority_bias == "LONG"):
                                        _misalign_note = " ⛔ NOT ALIGNED"
                                _pre_lines.append(
                                    f"{_dir_e} <b>{_pr.get('Ticker','?')}</b> {_pr.get('Direction','')} "
                                    f"| {_pr.get('Current','N/A')} | S:{_pr.get('Stop','?')} T1:{_pr.get('T1','?')} T2:{_pr.get('T2','?')} "
                                    f"| {_aln_e} {_pr.get('Alignment','')}{_misalign_note}"
                                )
                            _pre_lines.append(f"\n📊 {_conf_count} CONFIRMED · {_div_count} DIVERGED · {_conflict_count} pending")
                            _pre_lines.append("\n🔒 Locked at 8:30 AM CST")
                            _pre_msg = "\n".join(_pre_lines)
                            _bot_token = st.session_state.get("_telegram_bot_token", TELEGRAM_BOT_TOKEN)
                            _chat_id = st.session_state.get("_telegram_chat_id", TELEGRAM_CHAT_ID)
                            _url = f"https://api.telegram.org/bot{_bot_token}/sendMessage"
                            requests.post(_url, json={"chat_id": _chat_id, "text": _pre_msg, "parse_mode": "HTML"}, timeout=5)
                            st.session_state[_pre_tg_key] = True
                        except Exception as _tge:
                            print(f"⚠️ Pre-conf telegram error: {_tge}")
                else:
                    st.info("⏳ No candidates to preview yet.")

            _pre_confirmation_fragment()

        else:
            st.info("⏳ Pre-confirmation starts at 8:00 AM CST.")

    # ── ENTRY CONFIRMATION TABLE — 8:30 AM CST+ (10m / 30m / 4H) ──────────
    _replay_clock(8 * 60 + 30, "Market open — Evaluating 10m / 30m / 4H bias alignment")
    st.markdown("### 🕣 ENTRY CONFIRMATION TABLE (8:30 AM CST+)")
    st.markdown("All **OPENS NEAR ENTRY** with **10m · 30m · 4H** bias — anchored to 8:30 AM CST open")
    if early_confirmation_enabled and _confirmation_candidates:

        # ── Cache 8:30 bias snapshot so it doesn't change on page re-renders ──
        _830_cache_key = f"_830_bias_cache_{date.today()}"
        _830_bias_cache = st.session_state.get(_830_cache_key, {})
        _is_830_cached = bool(_830_bias_cache)
        if _is_830_cached:
            st.caption("🔒 Frozen 8:30 AM snapshot — biases locked at first evaluation")

        early_rows = []
        for trade in _confirmation_candidates:
            ticker = trade.get("Ticker", "?")
            direction = trade.get("Direction")

            # Use cached bias if available, otherwise fetch fresh and cache
            _cache_tkr_key = f"{ticker}_{direction}"
            if _cache_tkr_key in _830_bias_cache:
                bias_830 = _830_bias_cache[_cache_tkr_key]
            else:
                bias_830 = get_830_bias_eval(ticker, direction, target_date=_bias_target)
                _830_bias_cache[_cache_tkr_key] = bias_830
                st.session_state[_830_cache_key] = _830_bias_cache

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

            # ── Auto-fix: ensure Stop/T1/T2 align with direction relative to entry ──
            # LONG: Stop < Entry < T1 < T2   |   SHORT: Stop > Entry > T1 > T2
            if isinstance(opn, (int, float)) and opn > 0:
                def _p_fix(v):
                    try: return float(str(v).replace("$","").replace(",",""))
                    except: return 0.0
                _fs = _p_fix(disp_stop); _ft1 = _p_fix(disp_t1); _ft2 = _p_fix(disp_t2)
                _vals = sorted([v for v in [_fs, _ft1, _ft2] if v > 0])
                if len(_vals) == 3:
                    if disp_direction == "LONG":
                        # Need: Stop < Entry < T1 < T2
                        _below = [v for v in _vals if v < opn]
                        _above = [v for v in _vals if v >= opn]
                        if not _below:
                            # All on wrong side — mirror the lowest to be below entry
                            _below = [round(2 * opn - _vals[2], 2)]
                            _above = _vals[:2]
                        if len(_above) < 2:
                            # Need at least 2 above — mirror one from below
                            _above.append(round(2 * opn - _below[0], 2))
                            _above.sort()
                        _below.sort()
                        _above.sort()
                        _fs = _below[-1]  # Closest below entry = stop
                        _ft1 = _above[0]  # First above entry = T1
                        _ft2 = _above[-1] if len(_above) > 1 else _above[0]  # Furthest = T2
                    else:  # SHORT
                        # Need: Stop > Entry > T1 > T2
                        _above = [v for v in _vals if v > opn]
                        _below = [v for v in _vals if v <= opn]
                        if not _above:
                            # All on wrong side — mirror the highest to be above entry
                            _above = [round(2 * opn - _vals[0], 2)]
                            _below = _vals[1:]
                        if len(_below) < 2:
                            # Need at least 2 below — mirror one from above
                            _below.append(round(2 * opn - _above[-1], 2))
                            _below.sort(reverse=True)
                        _above.sort(reverse=True)
                        _below.sort(reverse=True)
                        _fs = _above[0]   # Highest above entry = stop
                        _ft1 = _below[0]  # First below entry = T1
                        _ft2 = _below[-1] if len(_below) > 1 else _below[0]  # Furthest = T2
                    disp_stop = f"${_fs:.2f}"
                    disp_t1 = f"${_ft1:.2f}"
                    disp_t2 = f"${_ft2:.2f}"

            # RR for 8:30 table
            def _calc_rr_830(entry_val, stop_val, target_val, direction):
                try:
                    if direction == "LONG":
                        risk = entry_val - stop_val
                        reward = target_val - entry_val
                    else:
                        risk = stop_val - entry_val
                        reward = entry_val - target_val
                    return round(reward / risk, 2) if risk > 0 else 0.0
                except: return 0.0
            def _p830(v):
                try: return float(str(v).replace("$","").replace(",",""))
                except: return 0.0
            _e830 = _p830(opn if isinstance(opn, (int, float)) else trade.get("Open", 0))
            _s830 = _p830(disp_stop)
            _t1_830 = _p830(disp_t1)
            _t2_830 = _p830(disp_t2)
            rr_t1_830 = _calc_rr_830(_e830, _s830, _t1_830, disp_direction)
            rr_t2_830 = _calc_rr_830(_e830, _s830, _t2_830, disp_direction)
            best_rr_830 = max(rr_t1_830, rr_t2_830)

            # Derive short scenario label
            _scn_raw = trade.get("Scenario", "")
            if "OPENS NEAR ENTRY" in _scn_raw.upper():
                _scn_830 = "ONE"
            elif "OPENS BETWEEN" in _scn_raw.upper():
                _scn_830 = "OBE"
            elif "OPENS PAST STOP" in _scn_raw.upper():
                _scn_830 = "OPS"
            elif "GAP" in _scn_raw.upper():
                _scn_830 = "GAP"
            else:
                _scn_830 = _scn_raw[:10]

            early_rows.append({
                "Ticker": ticker,
                "Direction": f"{'🔄 ' if flipped else ''}{disp_direction}",
                "Scen": _scn_830,
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
                "OHLC Signal": trade.get("OHLC Signal", "N/A"),
                "ATR": trade.get("ATR"),
                "_best_rr_830_sort": best_rr_830,
            })

        if early_rows:
            confirmed_rows = [r for r in early_rows if "CONFIRMED" in r.get("Alignment", "")]
            diverged_rows  = [r for r in early_rows if "DIVERGED" in r.get("Alignment", "")]
            early_other_rows = [r for r in early_rows if "CONFIRMED" not in r.get("Alignment", "") and "DIVERGED" not in r.get("Alignment", "")]

            # Direction-aware color helper
            def _style_830_row(row):
                """Highlight bias cells that conflict with direction."""
                d = str(row.get("Direction", "")).replace("🔄 ", "").upper()
                is_long = "LONG" in d
                styles = [""] * len(row)
                for i, col in enumerate(row.index):
                    val = str(row[col]).upper() if row[col] else ""
                    if col in ("10m Bias", "30m Bias", "4H Bias"):
                        if "BULLISH" in val or "BULL" in val:
                            if is_long:
                                styles[i] = "background-color: #0a3d1f; color: #00e5a0; font-weight: 700"
                            else:
                                styles[i] = "background-color: #4a3d0a; color: #ffa500; font-weight: 700; border: 2px solid #ffa500"
                        elif "BEARISH" in val or "BEAR" in val:
                            if not is_long:
                                styles[i] = "background-color: #3d0a1a; color: #ff4d6a; font-weight: 700"
                            else:
                                styles[i] = "background-color: #4a3d0a; color: #ffa500; font-weight: 700; border: 2px solid #ffa500"
                        elif "NEUTRAL" in val:
                            styles[i] = "color: #a0a0a0; font-weight: 700"
                    elif col == "Alignment":
                        if "CONFIRMED" in val: styles[i] = "color: #00e5a0; font-weight: 700"
                        elif "DIVERGED" in val: styles[i] = "color: #f0c040; font-weight: 700"
                    elif col == "OHLC Signal":
                        if "BULL" in val: styles[i] = "background-color: #0a3d1f; color: #00e5a0; font-weight: 700"
                        elif "BEAR" in val: styles[i] = "background-color: #3d0a1a; color: #ff4d6a; font-weight: 700"
                return styles
            bias_cols_830 = ["10m Bias", "30m Bias", "4H Bias", "Alignment", "OHLC Signal"]

            # ── Confirmed trades (trackable) ──
            show_confirmed = confirmed_rows + early_other_rows
            # Sort by Best RR (highest first)
            show_confirmed.sort(key=lambda x: x.get("_best_rr_830_sort", 0), reverse=True)
            if show_confirmed:
                conf_df = pd.DataFrame(show_confirmed)
                if "_best_rr_830_sort" in conf_df.columns:
                    conf_df = conf_df.drop(columns=["_best_rr_830_sort"])
                cols_830 = [c for c in bias_cols_830 if c in conf_df.columns]
                styled_conf = conf_df.style.apply(_style_830_row, axis=1)
                st.dataframe(styled_conf, use_container_width=True, height=min(40 * max(len(conf_df), 1) + 38, 400))
                st.success(f"🎯 {len(confirmed_rows)} trade(s) CONFIRMED — all biases aligned!")

                # ── Auto-track confirmed 8:30 AM trades (flipped → watchlist + tracking) ──
                _watchlist_added = []
                for cr in confirmed_rows:
                    _tkr = cr.get("Ticker", "?")
                    _is_flipped = "🔄" in str(cr.get("Direction", ""))
                    _dir = cr.get("Direction", "").replace("🔄 ", "")
                    _entry_val = cr.get("Open (8:30)", "$0").replace("$", "").replace(",", "")
                    _stop_val  = cr.get("Stop", "$0").replace("$", "").replace(",", "") if isinstance(cr.get("Stop"), str) else str(cr.get("Stop", 0))
                    _t1_val    = cr.get("T1", "$0").replace("$", "").replace(",", "") if isinstance(cr.get("T1"), str) else str(cr.get("T1", 0))
                    _t2_val    = cr.get("T2", "$0").replace("$", "").replace(",", "") if isinstance(cr.get("T2"), str) else str(cr.get("T2", 0))

                    # ── Flipped tickers → Watchlist DB (persisted) + also tracking ──
                    if _is_flipped:
                        try:
                            saved = save_to_watchlist(
                                ticker=_tkr,
                                direction=_dir,
                                entry_price=float(_entry_val),
                                stop_loss=float(_stop_val),
                                target1=float(_t1_val),
                                target2=float(_t2_val),
                                scenario=cr.get("Scen"),
                                confidence=cr.get("Confidence"),
                                ohlc_signal=cr.get("OHLC Signal", "N/A"),
                                reason=f"Direction flipped at 8:30 AM",
                            )
                            if saved:
                                _watchlist_added.append(_tkr)
                                print(f"📋 Flipped {_tkr} → Watchlist + Tracking (flipped to {_dir})")
                        except Exception as e:
                            print(f"⚠️ Watchlist save failed for {_tkr}: {e}")

                    if not _is_already_tracked(_tkr):
                        try:
                            # Auto-save to database
                            save_trade(
                                ticker=_tkr,
                                direction=_dir,
                                entry_price=float(_entry_val),
                                stop_loss=float(_stop_val),
                                target1=float(_t1_val),
                                target2=float(_t2_val),
                                open_price=float(_entry_val),
                                scenario=cr.get("Scen"),
                                confidence=cr.get("Confidence"),
                                notes=f"Auto-tracked at 8:30 AM (Confidence: {cr.get('Confidence', 'N/A')})"
                            )
                        except (ValueError, TypeError) as e:
                            print(f"⚠️ Auto-save failed for {_tkr}: {e}")
                        try:
                            _e8t = float(_entry_val)
                            _s8t = float(_stop_val)
                            _t18t = float(_t1_val)
                            _t28t = float(_t2_val)
                            def _rr8t(e, s, t, d):
                                try:
                                    risk = (e-s) if d=="LONG" else (s-e)
                                    rew  = (t-e) if d=="LONG" else (e-t)
                                    return round(rew/risk, 2) if risk > 0 else 0.0
                                except: return 0.0
                            _rr1_8t = _rr8t(_e8t, _s8t, _t18t, _dir)
                            _rr2_8t = _rr8t(_e8t, _s8t, _t28t, _dir)
                            _scn_id_830 = cr.get("Scen") or ""
                            st.session_state["tracking_trades"].append({
                                "ticker": _tkr,
                                "entry": _e8t,
                                "stop": _s8t,
                                "t1": _t18t,
                                "t2": _t28t,
                                "direction": _dir,
                                "confidence": cr.get("Confidence", "N/A"),
                                "atr": cr.get("ATR", "").replace("$", "") if isinstance(cr.get("ATR"), str) else str(cr.get("ATR", "")),
                                "scenario": _scn_id_830 if _scn_id_830 else "8:30 CONFIRMED",
                                "ohlc_signal": cr.get("OHLC Signal", "N/A"),
                                "rr_t1": _rr1_8t,
                                "rr_t2": _rr2_8t,
                                "best_rr": max(_rr1_8t, _rr2_8t),
                                "tracking_start_time": get_cst_now().isoformat(),
                            })
                        except (ValueError, TypeError):
                            pass
                if _watchlist_added:
                    st.info(f"📋 {len(_watchlist_added)} flipped ticker(s) saved to Watchlist + Tracking: {', '.join(_watchlist_added)}")
            else:
                st.info("⏳ No confirmed trades yet.")

            # ── Diverged trades (separate table, not tracked) ──
            if diverged_rows:
                st.markdown("#### ⚠️ DIVERGED — Do Not Track")
                st.caption("These trades have conflicting bias — removed from tracking candidates.")
                div_df = pd.DataFrame(diverged_rows)
                cols_830_div = [c for c in bias_cols_830 if c in div_df.columns]
                styled_div = div_df.style.apply(_style_830_row, axis=1)
                st.dataframe(styled_div, use_container_width=True, height=min(40 * max(len(div_df), 1) + 38, 400))

            # ── Telegram: send 8:30 AM confirmation summary ──
            _conf_tg_key = f"_830_conf_tg_sent_{get_cst_now().date()}"
            if not st.session_state.get(_conf_tg_key, False) and TELEGRAM_ENABLED and early_rows:
                try:
                    _830_now = get_cst_now()
                    _830_lines = [f"📸 <b>8:30 AM SNAPSHOT</b>\n📅 {_830_now.strftime('%B %d, %Y')} · {_830_now.strftime('%#I:%M %p CST')}\n"]
                    if confirmed_rows:
                        _non_flipped = [r for r in confirmed_rows if "🔄" not in str(r.get("Direction", ""))]
                        _flipped_cr = [r for r in confirmed_rows if "🔄" in str(r.get("Direction", ""))]
                        if _non_flipped:
                            _830_lines.append(f"<b>✅ CONFIRMED — TRACKING ({len(_non_flipped)}):</b>")
                            for _cr in _non_flipped:
                                _d_e = "🟢" if "LONG" in str(_cr.get("Direction","")).upper() else "🔴"
                                _830_lines.append(
                                    f"{_d_e} <b>{_cr.get('Ticker','?')}</b> {_cr.get('Direction','')} "
                                    f"| Entry:{_cr.get('Open (8:30)','?')} S:{_cr.get('Stop','?')} T1:{_cr.get('T1','?')} T2:{_cr.get('T2','?')} "
                                    f"| RR:{_cr.get('Best RR','?')} | {_cr.get('Scen','')}"
                                )
                        if _flipped_cr:
                            _830_lines.append(f"\n<b>📋 FLIPPED → WATCHLIST ({len(_flipped_cr)}):</b>")
                            for _cr in _flipped_cr:
                                _d_e = "🟢" if "LONG" in str(_cr.get("Direction","")).replace("🔄 ","").upper() else "🔴"
                                _830_lines.append(
                                    f"{_d_e} <b>{_cr.get('Ticker','?')}</b> {_cr.get('Direction','')} "
                                    f"| Entry:{_cr.get('Open (8:30)','?')} S:{_cr.get('Stop','?')} T1:{_cr.get('T1','?')} T2:{_cr.get('T2','?')} "
                                    f"| 🔄 Direction flipped"
                                )
                    if diverged_rows:
                        _830_lines.append(f"\n<b>⚠️ DIVERGED — SKIPPED ({len(diverged_rows)}):</b>")
                        for _dr in diverged_rows:
                            _d_e = "🟢" if "LONG" in str(_dr.get("Direction","")).upper() else "🔴"
                            _830_lines.append(
                                f"{_d_e} <b>{_dr.get('Ticker','?')}</b> {_dr.get('Direction','')} "
                                f"| Now:{_dr.get('Current','?')} S:{_dr.get('Stop','?')} T1:{_dr.get('T1','?')} T2:{_dr.get('T2','?')} "
                                f"| ⛔ NOT ALIGNED"
                            )
                    _tracking_count = len([r for r in confirmed_rows if "🔄" not in str(r.get("Direction", ""))])
                    _watchlist_count = len([r for r in confirmed_rows if "🔄" in str(r.get("Direction", ""))])
                    _830_lines.append(f"\n📊 {_tracking_count} tracking · {_watchlist_count} watchlist · {len(diverged_rows)} skipped")
                    _830_msg = "\n".join(_830_lines)
                    _url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
                    requests.post(_url, json={"chat_id": TELEGRAM_CHAT_ID, "text": _830_msg, "parse_mode": "HTML"}, timeout=5)
                    st.session_state[_conf_tg_key] = True
                except Exception as _tge:
                    print(f"⚠️ 8:30 conf telegram error: {_tge}")

            # ── Watchlist: show flipped tickers from DB ──
            _wl_today = get_watchlist(date_filter=date.today())
            if _wl_today:
                st.markdown("#### 📋 WATCHLIST — Flipped Direction")
                st.caption("These tickers had their direction flipped at 8:30 AM — monitoring only, not tracking.")
                _wl_rows = []
                for _w in _wl_today:
                    _wl_rows.append({
                        "Ticker": _w["ticker"],
                        "Direction": _w["direction"],
                        "Entry": f"${_w['entry_price']:.2f}" if _w.get("entry_price") else "N/A",
                        "Stop": f"${_w['stop_loss']:.2f}" if _w.get("stop_loss") else "N/A",
                        "T1": f"${_w['target1']:.2f}" if _w.get("target1") else "N/A",
                        "T2": f"${_w['target2']:.2f}" if _w.get("target2") else "N/A",
                        "Scen": _w.get("scenario", ""),
                        "Confidence": _w.get("confidence", ""),
                        "OHLC Signal": _w.get("ohlc_signal", ""),
                        "Reason": _w.get("reason", ""),
                    })
                _wl_df = pd.DataFrame(_wl_rows)
                def _style_wl_row(row):
                    """Watchlist row styling — purple theme for flipped tickers."""
                    styles = [""] * len(row)
                    for i, col in enumerate(row.index):
                        if col == "Direction":
                            val = str(row[col]).upper()
                            if "LONG" in val:
                                styles[i] = "color: #00e5a0; font-weight: 700"
                            elif "SHORT" in val:
                                styles[i] = "color: #ff4d6a; font-weight: 700"
                        elif col == "Reason":
                            styles[i] = "color: #b388ff; font-style: italic"
                    return styles
                styled_wl = _wl_df.style.apply(_style_wl_row, axis=1)
                st.dataframe(styled_wl, use_container_width=True, height=min(40 * max(len(_wl_df), 1) + 38, 300))

        else:
            st.info("⏳ Waiting for 8:30 AM CST data...")
    else:
        if not early_confirmation_enabled:
            st.info("⏳ Waiting for 8:30 AM CST to evaluate biases...")
        elif not _confirmation_candidates:
            st.info("📋 No OPENS NEAR ENTRY candidates yet. Run Check Open Prices first.")

    # ── ENTRY CONFIRMATION TABLE — 9:00 AM CST+ (10m + 4H) ──────────
    st.markdown("---")
    _replay_clock(9 * 60, "30 min into session — Re-evaluating with 10m + 4H bias")
    st.markdown("### ✅ ENTRY CONFIRMATION TABLE (9:00 AM CST+)")
    st.markdown("Trades with **CONFIRMED** 10m + 4H bias — ready to enter")
    if entry_confirmation_enabled and _confirmation_candidates:

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
                # RR for 9:00 confirmation table
                def _p9(v):
                    try: return float(str(v).replace("$","").replace(",",""))
                    except: return 0.0
                _e9 = _p9(bias_eval.get('current_price', 0))
                _s9 = _p9(trade.get("Stop", 0))
                _t19 = _p9(trade.get("T1", 0))
                _t29 = _p9(trade.get("T2", 0))
                def _rr9(e, s, t, d):
                    try:
                        risk = (e - s) if d == "LONG" else (s - e)
                        rew  = (t - e) if d == "LONG" else (e - t)
                        return round(rew / risk, 2) if risk > 0 else 0.0
                    except: return 0.0
                _rr1_9 = _rr9(_e9, _s9, _t19, direction)
                _rr2_9 = _rr9(_e9, _s9, _t29, direction)
                _best9 = max(_rr1_9, _rr2_9)
                confirmation_rows.append({
                    "Ticker": ticker,
                    "Direction": direction,
                    "Option": trade.get("Option"),
                    "Current": f"${bias_eval['current_price']:.2f}",
                    "Stop": trade.get("Stop"),
                    "T1": trade.get("T1"),
                    "T2": trade.get("T2"),
                    "RR(T1)": f"{_rr1_9:.2f}x",
                    "RR(T2)": f"{_rr2_9:.2f}x",
                    "Best RR": f"{_best9:.2f}x",
                    f"{bias_eval['tf_short']} Bias": bias_eval.get("bias_10min"),
                    f"{bias_eval['tf_long']} Bias": bias_eval.get("bias_30min"),
                    "Alignment": f"✅ CONFIRMED",
                    "Confidence": trade.get("Confidence"),
                    "ATR": trade.get("ATR"),
                    "_best_rr_sort": _best9,
                })

        if confirmation_rows:
            # Sort by Best RR (highest first)
            confirmation_rows.sort(key=lambda x: x.get("_best_rr_sort", 0), reverse=True)
            confirm_df = pd.DataFrame(confirmation_rows)
            if "_best_rr_sort" in confirm_df.columns:
                confirm_df = confirm_df.drop(columns=["_best_rr_sort"])
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
                        # Auto-save to database
                        save_trade(
                            ticker=_tkr9,
                            direction=_dir9,
                            entry_price=float(_cur9),
                            stop_loss=float(_stop9),
                            target1=float(_t1_9),
                            target2=float(_t2_9),
                            open_price=float(_cur9),
                            scenario=cr9.get("Scenario"),
                            confidence=cr9.get("Confidence"),
                            notes=f"Auto-tracked at 9:00 AM (Confidence: {cr9.get('Confidence', 'N/A')})"
                        )
                    except (ValueError, TypeError) as e:
                        print(f"⚠️ Auto-save failed for {_tkr9}: {e}")
                    try:
                        _e9t = float(_cur9)
                        _s9t = float(_stop9)
                        _t19t = float(_t1_9)
                        _t29t = float(_t2_9)
                        def _rr9t(e, s, t, d):
                            try:
                                risk = (e-s) if d=="LONG" else (s-e)
                                rew  = (t-e) if d=="LONG" else (e-t)
                                return round(rew/risk, 2) if risk > 0 else 0.0
                            except: return 0.0
                        _rr1_9t = _rr9t(_e9t, _s9t, _t19t, _dir9)
                        _rr2_9t = _rr9t(_e9t, _s9t, _t29t, _dir9)
                        st.session_state["tracking_trades"].append({
                            "ticker": _tkr9,
                            "entry": _e9t,
                            "stop": _s9t,
                            "t1": _t19t,
                            "t2": _t29t,
                            "direction": _dir9,
                            "confidence": cr9.get("Confidence", "N/A"),
                            "atr": cr9.get("ATR", "").replace("$", "") if isinstance(cr9.get("ATR"), str) else str(cr9.get("ATR", "")),
                            "scenario": "9:00 CONFIRMED",
                            "rr_t1": _rr1_9t,
                            "rr_t2": _rr2_9t,
                            "best_rr": max(_rr1_9t, _rr2_9t),
                            "tracking_start_time": get_cst_now().isoformat(),
                        })
                    except (ValueError, TypeError):
                        pass
        else:
            st.info("⏳ No trades with confirmed bias yet. Check again in a moment...")
    else:
        if not entry_confirmation_enabled:
            st.info("⏳ Waiting for 9:00 AM CST to evaluate biases...")
        elif not _confirmation_candidates:
            st.info("📋 No OPENS NEAR ENTRY candidates yet. Run Check Open Prices first.")

    _replay_clock(9 * 60 + 30, "Session in progress — Scenario tables & final classification")

    st.markdown("---")
    if display_cols and scenario_other_rows:
        _render_trackable_table(scenario_other_rows, "📊 All Other Scenarios", "other", display_cols)
    else:
        st.info("No additional scenarios to render yet.")
except Exception as _scenario_disp_err:
  import traceback; print(f"⚠️ Scenario display crashed: {_scenario_disp_err}\n{traceback.format_exc()}")

# ── Next Day Planning handler (shared: Intraday Planning + Scan Holdings) ─────────────
_auto_plan_run = st.session_state.pop("_sched_plan_gen", False)
if plan_run or holdings_scan_run or _auto_plan_run:
 try:
  with _active_tab:
    if missing_creds:
        st.error("Enter API credentials in the sidebar first.")
    else:
        plan_tickers = [t.strip().upper() for t in _active_tickers_raw.strip().split(",") if t.strip()]
        if not plan_tickers:
            st.warning("Enter at least one ticker.")
        else:
            # Plan-for date = next trading day after plan_date
            _plan_for = plan_date + timedelta(days=1)
            while _plan_for.weekday() >= 5:
                _plan_for += timedelta(days=1)
            _plan_label = _plan_for.strftime('%A, %B %d, %Y')
            st.markdown(f"### 🗓️ Intraday Plan for **{_plan_label}**")
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

                    # ── CPR for this ticker ──────────────────────────────────────
                    _plan_cpr = calc_cpr(p_daily)
                    _plan_cpr_tc   = _plan_cpr["tc"]   if _plan_cpr else None
                    _plan_cpr_p    = _plan_cpr["p"]    if _plan_cpr else None
                    _plan_cpr_bc   = _plan_cpr["bc"]   if _plan_cpr else None
                    _plan_cpr_type = _plan_cpr["cpr_type"] if _plan_cpr else "N/A"
                    _plan_cpr_wpct = _plan_cpr["width_pct"] if _plan_cpr else None

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

                    # ── Weekly Zone + Daily Zone from plan-date OHLC ─────────
                    _zone_map_pl = {
                        ("HIGH", "HIGH"): "⚠️ Extended — weekly+daily both high. Watch for reversal.",
                        ("HIGH", "LOW"):  "📉 Pulling back within weekly extension. Scalp opportunity.",
                        ("HIGH", "MID"):  "🔄 Weekly extended, daily balanced. Trail stop tight.",
                        ("LOW",  "HIGH"): "🚀 Bouncing off weekly low. Watch for continuation.",
                        ("LOW",  "LOW"):  "🛑 At support — both zones low. Wait for confirmation.",
                        ("LOW",  "MID"):  "⚖️ Weekly at support, daily balanced. Entry zone.",
                        ("MID",  "HIGH"): "📈 Daily extended in mid-range week. Scalp with target.",
                        ("MID",  "LOW"):  "📉 Daily dip in mid-range week. Watch for bounce.",
                        ("MID",  "MID"):  "⚖️ Mid-range both zones — no strong directional edge.",
                    }
                    try:
                        _pl_wk_hi = float(p_daily["high"].iloc[-10:].max())
                        _pl_wk_lo = float(p_daily["low"].iloc[-10:].min())
                        _pl_wk_rng = _pl_wk_hi - _pl_wk_lo
                        _pl_wk_pos = (daily_close - _pl_wk_lo) / _pl_wk_rng * 100 if _pl_wk_rng > 0 else 50.0
                        _plan_weekly_zone = "HIGH" if _pl_wk_pos >= 70 else ("LOW" if _pl_wk_pos <= 30 else "MID")
                    except Exception:
                        _plan_weekly_zone = "N/A"
                    try:
                        _pl_day_hi = float(p_daily["high"].iloc[-1])
                        _pl_day_lo = float(p_daily["low"].iloc[-1])
                        _pl_day_rng = _pl_day_hi - _pl_day_lo
                        _pl_day_pos = (daily_close - _pl_day_lo) / _pl_day_rng * 100 if _pl_day_rng > 0 else 50.0
                        _plan_daily_zone = "HIGH" if _pl_day_pos >= 70 else ("LOW" if _pl_day_pos <= 30 else "MID")
                    except Exception:
                        _plan_daily_zone = "N/A"
                    _plan_zone_conclusion = _zone_map_pl.get(
                        (_plan_weekly_zone, _plan_daily_zone),
                        f"Wk:{_plan_weekly_zone} Day:{_plan_daily_zone}" if _plan_weekly_zone != "N/A" else "N/A",
                    )

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
                    # RR ratios for plan
                    def _plan_rr(e, s, t, d):
                        try:
                            risk = (e - s) if d == "LONG" else (s - e)
                            rew  = (t - e) if d == "LONG" else (e - t)
                            return round(rew / risk, 2) if risk > 0 else 0.0
                        except: return 0.0
                    _rr1_pl = _plan_rr(entry, intra_stop, intra_t1, direction)
                    _rr2_pl = _plan_rr(entry, intra_stop, intra_t2, direction)
                    _best_rr_pl = max(_rr1_pl, _rr2_pl)
                    _daily_close_for_cpr = round(entry, 2)
                    _plan_cpr_pos   = ("Above" if _plan_cpr_tc and _daily_close_for_cpr > _plan_cpr_tc else
                                       "Below" if _plan_cpr_bc and _daily_close_for_cpr < _plan_cpr_bc else
                                       "Inside") if _plan_cpr else "N/A"
                    _plan_cpr_interp = cpr_interpretation(_plan_cpr_type, _plan_cpr_pos) if _plan_cpr else "—"

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
                        "RR(T1)": f"{_rr1_pl:.2f}x",
                        "RR(T2)": f"{_rr2_pl:.2f}x",
                        "Best RR": f"{_best_rr_pl:.2f}x",
                        "_best_rr_sort": _best_rr_pl,
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
                        "Weekly Zone": _plan_weekly_zone,
                        "Daily Zone": _plan_daily_zone,
                        "Zone Conclusion": _plan_zone_conclusion,
                        "CPR TC": f"${_plan_cpr_tc:.2f}" if _plan_cpr_tc else "N/A",
                        "CPR P":  f"${_plan_cpr_p:.2f}"  if _plan_cpr_p  else "N/A",
                        "CPR BC": f"${_plan_cpr_bc:.2f}" if _plan_cpr_bc else "N/A",
                        "CPR Type": _plan_cpr_type,
                        "CPR Width%": f"{_plan_cpr_wpct:.2f}%" if _plan_cpr_wpct is not None else "N/A",
                        "CPR Position": _plan_cpr_pos,
                        "CPR Interpretation": _plan_cpr_interp,
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

                    # Add 10m / 30m / 4H bias (_active_plan_date + 1, or live if today)
                    _plan_bias_date = None if _active_plan_date == date.today() else _active_plan_date + timedelta(days=1)
                    bias_830 = get_830_bias_eval(ticker, direction, target_date=_plan_bias_date)
                    plan_rows[-1]["10m Bias"] = bias_830.get("bias_10m", "N/A") if bias_830 else "N/A"
                    plan_rows[-1]["30m Bias"] = bias_830.get("bias_30m", "N/A") if bias_830 else "N/A"
                    plan_rows[-1]["4H Bias"]  = bias_830.get("bias_4h",  "N/A") if bias_830 else "N/A"

                    # ── Adjust Verdict based on bias alignment ──
                    _row = plan_rows[-1]
                    _b10 = _row.get("10m Bias", "N/A")
                    _b30 = _row.get("30m Bias", "N/A")
                    _b4h = _row.get("4H Bias", "N/A")
                    _sbi = _row.get("Short Bias", "N/A")
                    _lbi = _row.get("Long Bias", "N/A")
                    _all_biases = [b for b in [_b10, _b30, _b4h, _sbi, _lbi] if b not in ("N/A", "")]
                    if _all_biases:
                        _n_bull = sum(1 for b in _all_biases if b == "BULLISH")
                        _n_bear = sum(1 for b in _all_biases if b == "BEARISH")
                        _total  = len(_all_biases)
                        _orig_verdict = _row["Verdict"]
                        if _n_bull == _total:
                            # All biases bullish
                            _row["Verdict"] = "BULLISH"
                            _row["Direction"] = "LONG"
                            _row["Option"] = "CALL"
                        elif _n_bear == _total:
                            # All biases bearish
                            _row["Verdict"] = "BEARISH"
                            _row["Direction"] = "SHORT"
                            _row["Option"] = "PUT"
                        elif _n_bull >= _total * 0.6:
                            _row["Verdict"] = "LEAN BULLISH"
                            _row["Direction"] = "LONG"
                            _row["Option"] = "CALL"
                        elif _n_bear >= _total * 0.6:
                            _row["Verdict"] = "LEAN BEARISH"
                            _row["Direction"] = "SHORT"
                            _row["Option"] = "PUT"
                        # If direction flipped, recalculate stop/targets
                        if _row["Direction"] != direction:
                            _e = _row["Close"]
                            _a = _row["ATR"]
                            _sd = round(_a * 0.3, 2)
                            _t1d = round(_a * 0.5, 2)
                            _t2d = round(_a * 0.8, 2)
                            if _row["Direction"] == "LONG":
                                _row["Intra Stop"] = round(_e - _sd, 2)
                                _row["Intra T1"] = round(_e + _t1d, 2)
                                _row["Intra T2"] = round(_e + _t2d, 2)
                            else:
                                _row["Intra Stop"] = round(_e + _sd, 2)
                                _row["Intra T1"] = round(_e - _t1d, 2)
                                _row["Intra T2"] = round(_e - _t2d, 2)

                    # ── Options strategy based on Daily Zone — try Alpaca chain first ──
                    _dz = _row.get("Daily Zone", "MID")
                    _dir = _row["Direction"]
                    _atm = _row.get("ATM Strike", _row["Close"])
                    _exp0 = _row.get("0DTE Exp", "")
                    _exp2 = _row.get("2-3DTE Exp", "")
                    _tk = _row["Ticker"]

                    # Alpaca options chain (separate column)
                    _plan_alpaca_opt = None
                    _plan_alpaca_text = "N/A"
                    try:
                        _plan_alpaca_opt = get_options_strategy_alpaca(
                            _tk, _row["Close"], _dir, _dz, api_key, api_secret)
                    except Exception:
                        pass
                    if _plan_alpaca_opt and _plan_alpaca_opt.get("summary"):
                        _plan_alpaca_text = _plan_alpaca_opt["summary"]
                        if _plan_alpaca_opt.get("alt"):
                            _plan_alpaca_text += f" | {_plan_alpaca_opt['alt']}"
                    _row["Alpaca Options"] = _plan_alpaca_text

                    # Fib-computed strategy (always calculated)
                    if _dz == "LOW" and _dir == "LONG":
                        _row["Options Strategy"] = (
                            f"📈 {_tk} Bull Call Spread — Buy ${round(_atm - strike_inc)} Call / Sell ${round(_atm)} Call "
                            f"Exp {_exp0} | Alt: Buy ${round(_atm)} Call Exp {_exp2}")
                    elif _dz == "HIGH" and _dir == "SHORT":
                        _row["Options Strategy"] = (
                            f"📉 {_tk} Bear Put Spread — Buy ${round(_atm + strike_inc)} Put / Sell ${round(_atm)} Put "
                            f"Exp {_exp0} | Alt: Buy ${round(_atm)} Put Exp {_exp2}")
                    elif _dz == "HIGH" and _dir == "LONG":
                        _row["Options Strategy"] = (
                            f"⚠️ {_tk} Caution — Daily HIGH zone + LONG: "
                            f"Buy ${round(_atm)} Call / Sell ${round(_atm + strike_inc)} Call Exp {_exp2} (hedged)")
                    elif _dz == "LOW" and _dir == "SHORT":
                        _row["Options Strategy"] = (
                            f"⚠️ {_tk} Caution — Daily LOW zone + SHORT: "
                            f"Buy ${round(_atm)} Put / Sell ${round(_atm - strike_inc)} Put Exp {_exp2} (hedged)")
                    else:
                        _row["Options Strategy"] = (
                            f"🦋 {_tk} Iron Butterfly — Sell ${round(_atm)} Call+Put / "
                            f"Buy ${round(_atm + strike_inc)} Call + Buy ${round(_atm - strike_inc)} Put Exp {_exp2} "
                            f"| Alt: Buy Straddle ${round(_atm)} Call+Put Exp {_exp0}")

                except:
                    continue

            plan_progress.empty()
            plan_status.empty()

            if not plan_rows:
                st.info("No actionable signals generated for the given tickers.")
            else:
                # Sort plan_rows by Best RR (highest first)
                plan_rows.sort(key=lambda x: x.get("_best_rr_sort", 0), reverse=True)
                plan_df = pd.DataFrame(plan_rows)
                if "_best_rr_sort" in plan_df.columns:
                    plan_df = plan_df.drop(columns=["_best_rr_sort"])

                # ── Compact symbol summary table ───────────────────────────
                summary_cols = ["Ticker", "Direction", "Option", "Grade",
                                "Verdict", "Confidence", "Close", "ATR",
                                "Intra Stop", "Intra T1", "Intra T2",
                                "RR(T1)", "RR(T2)", "Best RR",
                                "ATM Strike",
                                "CPR TC", "CPR P", "CPR BC", "CPR Type", "CPR Width%",
                                "CPR Position", "CPR Interpretation",
                                "Weekly Zone", "Daily Zone", "Zone Conclusion",
                                "Options Strategy",
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
                if "_best_rr_sort" in summary_df.columns:
                    summary_df = summary_df.drop(columns=["_best_rr_sort"])
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
                            f'Stop ${r["Intra Stop"]:.2f} · T1 ${r["Intra T1"]:.2f} · T2 ${r["Intra T2"]:.2f} · RR {r.get("Best RR", "N/A")}</div>'
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
                    # Pre-build CPR block to avoid nested-quote issues in f-strings
                    _ct = r.get("CPR Type", "N/A")
                    if _ct != "N/A":
                        _cbg = "#3a3000" if _ct == "Narrow" else "#2a1500" if _ct == "Wide" else "#001533"
                        _cfg = "#ffe066" if _ct == "Narrow" else "#ff8c42" if _ct == "Wide" else "#4d9fff"
                        _cpos = r.get("CPR Position", "N/A")
                        _cpos_color = "#00e5a0" if _cpos == "Above" else "#ff4d6a" if _cpos == "Below" else "#f0c040"
                        _cpr_html = (
                            f'<div style="background:#090b13;border:1px solid #1a1d2e;border-radius:4px;'
                            f'padding:8px 12px;margin-bottom:8px">'
                            f'<div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:4px">'
                            f'<span style="font-size:8px;color:#3a3d5c;letter-spacing:1.5px">CPR · CENTRAL PIVOT RANGE</span>'
                            f'<span style="font-size:9px;font-weight:700;padding:1px 7px;border-radius:8px;'
                            f'background:{_cbg};color:{_cfg}">{_ct} CPR · {r.get("CPR Width%","")}</span>'
                            f'</div>'
                            f'<div style="display:flex;gap:16px;font-size:10px;margin-bottom:4px">'
                            f'<span style="color:#6b7099">TC: <b style="color:#4d9fff">{r.get("CPR TC","N/A")}</b></span>'
                            f'<span style="color:#6b7099">P: <b style="color:#c8cfe8">{r.get("CPR P","N/A")}</b></span>'
                            f'<span style="color:#6b7099">BC: <b style="color:#ff8c42">{r.get("CPR BC","N/A")}</b></span>'
                            f'<span style="color:#6b7099">Position: <b style="color:{_cpos_color}">{_cpos}</b></span>'
                            f'</div>'
                            f'<div style="font-size:9px;color:#a0a8cc">{r.get("CPR Interpretation","—")}</div>'
                            f'</div>'
                        )
                    else:
                        _cpr_html = ""
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
                        f'<span style="color:#6b7099">Best RR: <b style="color:#f0c040">{r.get("Best RR", "N/A")}</b></span>'
                        f'</div>'
                        # Option strikes
                        f'<div style="background:#090b13;border:1px solid #1a1d2e;border-radius:4px;padding:10px;margin-bottom:8px">'
                        f'<div style="font-size:8px;color:#3a3d5c;letter-spacing:1.5px;margin-bottom:6px">OPTION STRIKES · Exp: {r["0DTE Exp"]} (0DTE) or {r["2-3DTE Exp"]} (2-3 DTE)</div>'
                        f'<div style="font-size:10px;line-height:2;color:#c8cce8">'
                        f'<div>🎯 Aggressive: <b style="color:#f5c842">{r["Aggressive"]}</b></div>'
                        f'<div>⚖️ Moderate: <b style="color:#4d9fff">{r["Moderate"]}</b></div>'
                        f'<div>🛡️ Conservative: <b style="color:#00e5a0">{r["Conservative"]}</b></div>'
                        f'</div></div>'
                        + _cpr_html
                        # Scenarios
                        + f'<div style="font-size:10px;line-height:2.2;color:#c8cce8">'
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
                st.session_state[_active_plan_key] = plan_rows

                plan_csv = plan_df.to_csv(index=False)
                _csv_prefix = "holdings_plan" if _from_holdings else "intraday_plan"
                st.download_button(
                    f"📥 Download Plan CSV ({_plan_label})", plan_csv,
                    file_name=f"{_csv_prefix}_{_plan_for}.csv",
                    mime="text/csv", use_container_width=True,
                )
 except Exception as _plan_err:
  import traceback; print(f"⚠️ Plan handler crashed: {_plan_err}\n{traceback.format_exc()}")

# ── Sector Scan ──────────────────────────────────────
try:
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
            
            # ── Parallel scan using ThreadPoolExecutor ──
            hot_results, _, _ = _parallel_scan_with_progress(
                hot_stocks, api_key, api_secret, data_source,
                use_fib, fib_tol, use_strategy,
                progress_bar=progress_bar, status_text=status_text,
            )
            
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

            # Store swing candidates in session_state for the push button
            _swing_setups = bullish_hot + bearish_hot
            if _swing_setups:
                st.session_state["_sector_swing_setups"] = _swing_setups
                st.session_state["_sector_swing_date"] = str(date.today())
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
        
        # ── Parallel scan using ThreadPoolExecutor ──
        all_results, _, _ = _parallel_scan_with_progress(
            all_sector_stocks, api_key, api_secret, data_source,
            use_fib, fib_tol, use_strategy,
            progress_bar=progress_bar, status_text=status_text,
        )
        
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

        # Store swing candidates in session_state for the push button
        _swing_setups = top_bullish + top_bearish
        if _swing_setups:
            st.session_state["_sector_swing_setups"] = _swing_setups
            st.session_state["_sector_swing_date"] = str(date.today())

except Exception as _sector_err:
  import traceback; print(f"⚠️ Sector scan handler crashed: {_sector_err}\n{traceback.format_exc()}")

# ── Sector Scan: Push Swing Trades to Tracker ─────────────────────────────────
with tab_sector:
    _sector_swings     = st.session_state.get("_sector_swing_setups", [])
    _sector_swing_date = st.session_state.get("_sector_swing_date", "")
    if _sector_swings:
        st.markdown("---")
        st.markdown(
            f'<div style="background:#0a1628;border:1px solid #1a3a5c;padding:12px 16px;'
            f'border-radius:8px;margin-bottom:10px">'
            f'<span style="color:#4d9fff;font-weight:700;font-size:13px">📤 Swing Trade Push Ready</span><br>'
            f'<span style="color:#6b7099;font-size:11px">'
            f'{len(_sector_swings)} setup(s) from scan on {_sector_swing_date} • '
            f'{sum(1 for r in _sector_swings if r.get("verdict")=="BULLISH")} long · '
            f'{sum(1 for r in _sector_swings if r.get("verdict")=="BEARISH")} short'
            f'</span></div>',
            unsafe_allow_html=True,
        )
        push_col1, push_col2 = st.columns([2, 1])
        with push_col1:
            _push_selected = []
            for r in _sector_swings:
                label = f"{'🚀' if r.get('verdict')=='BULLISH' else '📉'} {r['ticker']} " \
                        f"({'LONG' if r.get('verdict')=='BULLISH' else 'SHORT'}) — " \
                        f"Score: {r.get('score', 0):+g} | {r.get('confidence','')}"
                if st.checkbox(label, value=True, key=f"_swing_chk_{r['ticker']}"):
                    _push_selected.append(r)
        with push_col2:
            if st.button(
                f"📤 Push {len(_push_selected)} Trade(s)",
                key="push_swing_btn",
                type="primary",
                use_container_width=True,
                disabled=len(_push_selected) == 0,
            ):
                _saved = 0
                for r in _push_selected:
                    direction = "LONG" if r.get("verdict") == "BULLISH" else "SHORT"
                    entry = r.get("entry") or r.get("price")
                    if not entry:
                        continue
                    ok, _, _ = save_trade(
                        ticker=r["ticker"],
                        direction=direction,
                        entry_price=float(entry),
                        stop_loss=r.get("stop_loss"),
                        target1=r.get("target1"),
                        target2=r.get("target2"),
                        verdict=r.get("verdict"),
                        confidence=r.get("confidence"),
                        score=r.get("score"),
                        scenario=r.get("setup"),
                        notes=f"Sector Scan {_sector_swing_date}",
                    )
                    if ok:
                        _saved += 1
                tg_future = _fire_and_forget_telegram(send_sector_swing_alert, _push_selected)
                if _saved > 0:
                    st.success(
                        f"✅ {_saved} trade(s) saved to tracker! · 📱 Telegram queued"
                    )
                    st.session_state["_sector_swing_setups"] = []
                else:
                    st.warning("⚠️ No trades saved — check entry price fields.")


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

try:
 if fetch_btn:
  with tab_fetch:
    source_name = "Alpaca" if data_source == "Alpaca" else "Polygon"
    st.session_state["_fib_dl_rows"] = []   # reset on every new fetch run
    _daterange_ph = st.empty()  # placeholder — date range summary above the report
    _report_ph = st.empty()   # placeholder — filled with Fib report after all symbols processed
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
      source_colors = {"manual": "#00e5a0", "polygon": "#4d9fff", "auto-detected": "#f5c842", "none": "#ff4d6a",
                       "yfinance": "#a78bfa", "manual+yfinance": "#00e5a0", "manual+polygon": "#00e5a0",
                       "manual+auto": "#f5c842"}
      source_labels = {
          "manual":           "✏️ manual input",
          "yfinance":         "📊 yfinance",
          "manual+yfinance":  "✏️ manual + 📊 yfinance",
          "polygon":          "🔷 Polygon financials",
          "manual+polygon":   "✏️ manual + 🔷 Polygon",
          "auto-detected":    "⚡ auto-detected from price gaps",
          "manual+auto":      "✏️ manual + ⚡ auto-detected",
          "none":             "❌ none",
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

      # Calculate swing high/low between previous earnings and last earnings (both <= _latest_d).
      # If the user pasted dates, use the MAX pasted date as the "as-of" cutoff so backtesting
      # anchors to that date (e.g. paste 2025-04-22 → swing_end=Apr 22, swing_start=Jan 30).
      # Default (no pasted dates): use today's last bar.
      _latest_d = latest_date.date() if hasattr(latest_date, "date") else latest_date
      if manual_dates_list:
          try:
              _manual_max = max(datetime.strptime(d.strip(), "%Y-%m-%d").date()
                                for d in manual_dates_list if d.strip())
              _latest_d = _manual_max   # anchor to latest pasted date
          except Exception:
              pass   # invalid dates — keep today's bar as cutoff
      if len(earnings_events) >= 1:
          _all_earn_dates = sorted([
              datetime.strptime(e[0], "%Y-%m-%d").date()
              for e in earnings_events
          ])
          # Earnings dates that fall on or before the last bar in daily_df
          _eligible = [d for d in _all_earn_dates if d <= _latest_d]
          if len(_eligible) >= 2:
              swing_end   = _eligible[-1]           # last earnings (e.g. Dec 09 2025)
              swing_start = _eligible[-2]           # previous earnings (e.g. Sep 09 2025)
          elif len(_eligible) == 1:
              swing_end   = _eligible[0]
              swing_start = swing_end - timedelta(days=90)   # fallback: 90 days before
          else:
              swing_end   = _latest_d
              swing_start = _latest_d - timedelta(days=90)
      else:
          swing_end   = _latest_d
          swing_start = _latest_d - timedelta(days=90)

      # ── Backtest mode: if swing_end is before the last bar, use the close ON
      # swing_end as the reference price so all Fib distances/bias reflect that date.
      _di_for_swing = np.array([d.date() if hasattr(d, "date") else d for d in daily_df.index])
      _backtest_mode = (swing_end < _latest_d)
      if _backtest_mode:
          _ref_rows = daily_df[_di_for_swing <= swing_end]
          latest_close = float(_ref_rows["close"].iloc[-1]) if not _ref_rows.empty else latest_close
          latest_date  = _ref_rows.index[-1] if not _ref_rows.empty else latest_date
      swing_data = daily_df[(_di_for_swing >= swing_start) & (_di_for_swing <= swing_end)]
      if len(swing_data) < 5:
          swing_data = daily_df[_di_for_swing >= swing_start]   # widen to swing_start→latest
      if len(swing_data) < 5:
          swing_data = daily_df.tail(90)  # last-resort fallback
    
      swing_hi = float(swing_data["high"].max())
      swing_lo = float(swing_data["low"].min())
      swing_range = swing_hi - swing_lo
      swing_hi_date = swing_data["high"].idxmax()
      swing_lo_date = swing_data["low"].idxmin()
    
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
      now_et = datetime.now(pytz.timezone("America/New_York"))
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
              _cst_tz = pytz.timezone("America/Chicago")
              first_ts = candle_4h["first_bar_ts"]
              last_ts = candle_4h["last_bar_ts"]
              if hasattr(first_ts, 'tz') and first_ts.tz is not None:
                  first_cst = first_ts.astimezone(_cst_tz)
                  last_cst = last_ts.astimezone(_cst_tz)
              else:
                  # Assume ET, convert to CST
                  et = pytz.timezone("America/New_York")
                  first_cst = et.localize(first_ts.to_pydatetime()).astimezone(_cst_tz)
                  last_cst = et.localize(last_ts.to_pydatetime()).astimezone(_cst_tz)
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
          
          # Capture today's open price
          today_open = round(float(daily_df.iloc[-1]["open"]), 2) if len(daily_df) > 0 else None
          
          _trade_payload = {
              "ticker": symbol, "direction": setup_dir, "entry_price": entry,
              "stop_loss": stop_loss, "target1": target1, "target2": target2,
              "verdict": final_verdict, "confidence": confidence, "score": score,
              "signals": ", ".join(f"{nm}: {val}" for nm, val, _, _ in signal_details),
              "t1_days": t1_days, "t2_days": t2_days, "open_price": today_open,
          }
          st.session_state[f"_trade_data_{track_key}"] = _trade_payload

          track_notes = st.text_input("Trade notes (optional)", key=f"notes_{track_key}",
                                      placeholder="e.g. Earnings play, breakout setup…")

          def _save_tracked_trade(tkey):
              """Callback to save trade from session state data."""
              payload = st.session_state.get(f"_trade_data_{tkey}")
              if payload:
                  notes = st.session_state.get(f"notes_{tkey}", "")
                  saved, telegram_sent, trade_id = save_trade(**payload, scenario=None, notes=notes if notes else None, force=True)
                  st.session_state[f"_trade_saved_{tkey}"] = True
                  st.session_state[f"_telegram_sent_{tkey}"] = telegram_sent

          st.button(
              f"📌 Track This Trade — {setup_dir} {symbol} @ ${entry:.2f}",
              key=track_key,
              on_click=_save_tracked_trade,
              args=(track_key,),
          )
          if st.session_state.get(f"_trade_saved_{track_key}"):
              telegram_sent = st.session_state.get(f"_telegram_sent_{track_key}", False)
              msg = f"✅ Trade saved! {setup_dir} {symbol} @ ${entry:.2f} · Stop ${stop_loss:.2f} · T1 ${target1:.2f} · T2 ${target2:.2f}"
              if telegram_sent:
                  msg += " | 📱 Telegram sent"
              st.success(msg)
              del st.session_state[f"_trade_saved_{track_key}"]
              if f"_telegram_sent_{track_key}" in st.session_state:
                  del st.session_state[f"_telegram_sent_{track_key}"]
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

          # ── CPR (Central Pivot Range) ──────────────────────────────────────────
          cpr = calc_cpr(daily_df)
          if cpr:
              cpr_type   = cpr["cpr_type"]
              cpr_colors = {"Narrow": ("#ffe066", "#3a3000"), "Wide": ("#ff8c42", "#2a1500"), "Normal": ("#4d9fff", "#001533")}
              badge_fg, badge_bg = cpr_colors.get(cpr_type, ("#6b7099", "#1a1d2e"))

              price_vs_cpr = ""
              if latest_close > cpr["tc"]:
                  price_vs_cpr = "Price ABOVE CPR — bullish bias"
                  pvc_color = "#00e5a0"
              elif latest_close < cpr["bc"]:
                  price_vs_cpr = "Price BELOW CPR — bearish bias"
                  pvc_color = "#ff4d6a"
              else:
                  price_vs_cpr = "Price INSIDE CPR — range / indecision"
                  pvc_color = "#f0c040"

              st.markdown(
                  f'<div style="background:#0d0f17;border:1px solid #1a1d2e;border-radius:6px;'
                  f'padding:12px 16px;margin-top:10px">'
                  f'<div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:8px">'
                  f'<span style="color:#c8cfe8;font-weight:700;font-size:13px">Central Pivot Range (CPR)</span>'
                  f'<span style="background:{badge_bg};color:{badge_fg};border:1px solid {badge_fg};'
                  f'border-radius:12px;padding:2px 10px;font-size:11px;font-weight:700">{cpr_type} CPR</span>'
                  f'</div>'
                  f'<div style="display:flex;gap:24px;margin-bottom:8px">'
                  f'<div style="text-align:center">'
                  f'<div style="color:#6b7099;font-size:9px;text-transform:uppercase;margin-bottom:2px">TC (Top Central)</div>'
                  f'<div style="color:#4d9fff;font-weight:700;font-size:14px">${cpr["tc"]:.2f}</div>'
                  f'</div>'
                  f'<div style="text-align:center">'
                  f'<div style="color:#6b7099;font-size:9px;text-transform:uppercase;margin-bottom:2px">P (Pivot)</div>'
                  f'<div style="color:#c8cfe8;font-weight:700;font-size:14px">${cpr["p"]:.2f}</div>'
                  f'</div>'
                  f'<div style="text-align:center">'
                  f'<div style="color:#6b7099;font-size:9px;text-transform:uppercase;margin-bottom:2px">BC (Bottom Central)</div>'
                  f'<div style="color:#ff8c42;font-weight:700;font-size:14px">${cpr["bc"]:.2f}</div>'
                  f'</div>'
                  f'<div style="text-align:center">'
                  f'<div style="color:#6b7099;font-size:9px;text-transform:uppercase;margin-bottom:2px">Width</div>'
                  f'<div style="color:#6b7099;font-size:13px">{cpr["width_pct"]:.2f}%</div>'
                  f'</div>'
                  f'</div>'
                  f'<div style="color:{pvc_color};font-size:11px;border-top:1px solid #1a1d2e;padding-top:6px">'
                  f'{price_vs_cpr}</div>'
                  f'</div>',
                  unsafe_allow_html=True)
      else:
          st.info("Insufficient data to calculate support/resistance levels.")

      # ══════════════════════════════════════════════════════════════════════════════
      # FUNDAMENTAL ANALYSIS PANEL
      # ══════════════════════════════════════════════════════════════════════════════
      st.markdown("---")
      with st.spinner("Fetching fundamentals..."):
          fund = get_fundamentals(symbol)
      # ── Compute Fundamental Bias ──
      _fund_bias_label = ""
      if fund:
          _fb_score = 0
          # Earnings growth (heaviest weight)
          _eg = fund.get("earnings_growth")
          if _eg is not None:
              if _eg > 0.10:   _fb_score += 2
              elif _eg > 0:    _fb_score += 1
              elif _eg > -0.10: _fb_score -= 1
              else:            _fb_score -= 2
          # Revenue growth
          _rg = fund.get("revenue_growth")
          if _rg is not None:
              if _rg > 0.10:   _fb_score += 1
              elif _rg > 0:    _fb_score += 0.5
              elif _rg > -0.05: _fb_score -= 0.5
              else:            _fb_score -= 1
          # Valuation
          _val = fund.get("valuation", "")
          if _val == "Undervalued":       _fb_score += 1.5
          elif _val == "Fair Value":      _fb_score += 0.5
          elif _val == "Overvalued":      _fb_score -= 1
          elif _val == "Very Expensive":  _fb_score -= 1.5
          elif _val == "Negative Earnings": _fb_score -= 1
          # Analyst consensus
          _rec = (fund.get("rec_key") or "").upper()
          if _rec in ("STRONG_BUY", "BUY"):  _fb_score += 1
          elif _rec == "HOLD":                _fb_score += 0
          elif _rec in ("SELL", "STRONG_SELL"): _fb_score -= 1.5
          # Analyst upside
          _up = fund.get("target_upside")
          if _up is not None:
              if _up > 20:    _fb_score += 1
              elif _up > 0:   _fb_score += 0.5
              elif _up > -15: _fb_score -= 0.5
              else:           _fb_score -= 1
          # Profitability
          _pm = fund.get("profit_margin")
          if _pm is not None:
              if _pm > 0.15:  _fb_score += 1
              elif _pm > 0:   _fb_score += 0.5
              else:           _fb_score -= 1
          # ROE
          _roe = fund.get("roe")
          if _roe is not None:
              if _roe > 0.15:  _fb_score += 0.5
              elif _roe < 0:   _fb_score -= 0.5
          # Debt
          _de = fund.get("debt_to_equity")
          if _de is not None:
              if _de < 50:     _fb_score += 0.5
              elif _de > 150:  _fb_score -= 1
          # 52-week position
          _pfh = fund.get("pct_from_high")
          if _pfh is not None:
              if _pfh > -10:   _fb_score += 0.5
              elif _pfh < -30: _fb_score -= 1
          # Short interest (bearish pressure)
          _sp = fund.get("short_pct")
          if _sp is not None and _sp > 0.10:
              _fb_score -= 0.5

          if _fb_score >= 3:
              _fund_bias_label = '<span style="background:#00e5a020;border:1px solid #00e5a0;color:#00e5a0;padding:4px 12px;border-radius:12px;font-size:12px;font-weight:700;margin-left:12px">🐂 BULLISH</span>'
          elif _fb_score >= 1:
              _fund_bias_label = '<span style="background:#00e5a020;border:1px solid #00e5a060;color:#7ccfb0;padding:4px 12px;border-radius:12px;font-size:12px;font-weight:700;margin-left:12px">🐂 LEAN BULLISH</span>'
          elif _fb_score > -1:
              _fund_bias_label = '<span style="background:#f5c84220;border:1px solid #f5c84260;color:#f5c842;padding:4px 12px;border-radius:12px;font-size:12px;font-weight:700;margin-left:12px">⚖️ NEUTRAL</span>'
          elif _fb_score > -3:
              _fund_bias_label = '<span style="background:#ff4d6a20;border:1px solid #ff4d6a60;color:#ff8c8c;padding:4px 12px;border-radius:12px;font-size:12px;font-weight:700;margin-left:12px">🐻 LEAN BEARISH</span>'
          else:
              _fund_bias_label = '<span style="background:#ff4d6a20;border:1px solid #ff4d6a;color:#ff4d6a;padding:4px 12px;border-radius:12px;font-size:12px;font-weight:700;margin-left:12px">🐻 BEARISH</span>'

      st.markdown(f'### 📊 Fundamental Analysis {_fund_bias_label}', unsafe_allow_html=True)
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
      _swing_start_label = swing_start.strftime("%b %d, %Y") if hasattr(swing_start, 'strftime') else str(swing_start)
      _swing_end_label   = swing_end.strftime("%b %d, %Y")   if hasattr(swing_end,   'strftime') else str(swing_end)
      _hi_date_label = swing_hi_date.strftime("%b %d, %Y") if hasattr(swing_hi_date, 'strftime') else str(swing_hi_date)
      _lo_date_label = swing_lo_date.strftime("%b %d, %Y") if hasattr(swing_lo_date, 'strftime') else str(swing_lo_date)
      if _backtest_mode:
          st.markdown(
              f'<div style="background:#1a1500;border:1px solid #f5c84260;padding:8px 14px;'
              f'border-radius:6px;font-size:11px;color:#f5c842;margin-bottom:8px">'
              f'📅 <b>Backtest Mode</b> — All analysis as of <b>{_swing_end_label}</b> '
              f'(close: <b>${latest_close:.2f}</b>). Live price ignored.</div>',
              unsafe_allow_html=True,
          )
      st.markdown(
          f'<div style="font-size:10px;color:#6b7099;margin-bottom:8px">'
          f'Swing window: <b style="color:#ccd6f6">{_swing_start_label}</b> → <b style="color:#ccd6f6">{_swing_end_label}</b> (prev earnings → last earnings)<br>'
          f'Low: <span style="color:#ff9f7f">${swing_lo:.2f}</span> on <b>{_lo_date_label}</b> · '
          f'High: <span style="color:#00e5a0">${swing_hi:.2f}</span> on <b>{_hi_date_label}</b> · '
          f'Range: ${swing_range:.2f}</div>',
          unsafe_allow_html=True
      )
    
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

      # ── Fib Quick Summary (Earnings + Weekly) ─────────────────────────────
      # Weekly fib: week that CONTAINS the last earnings date (swing_end)
      # e.g. swing_end = Dec 09 2025 → week of Dec 08 2025 (Mon–Fri)
      _di_dates_wk = np.array([d.date() if hasattr(d, "date") else d for d in daily_df.index])
      _wk_start_d_fib = swing_end - timedelta(days=swing_end.weekday())   # Monday of earnings week
      _wk_end_d_fib   = _wk_start_d_fib + timedelta(days=4)               # Friday of earnings week
      _wk_slice = daily_df[(_di_dates_wk >= _wk_start_d_fib) & (_di_dates_wk <= _wk_end_d_fib)]
      if _wk_slice.empty:
          _wk_slice = daily_df.tail(5)
      _wk_hi_v = float(_wk_slice["high"].max())
      _wk_lo_v = float(_wk_slice["low"].min())
      _wk_range_v = _wk_hi_v - _wk_lo_v
      _wk_start_lbl = _wk_start_d_fib.strftime("%b %d, %Y")
      # In backtest mode As Of = swing_end (the earnings date being analysed).
      # In live mode As Of = the entered date (from manual_dates_list) if backdated, else today.
      _asof_d = swing_end if _backtest_mode else _latest_d

      # Persist for Scenarios tab
      _fib_snap_map = st.session_state.setdefault("_fib_snapshot", {})
      _fib_snap_map[symbol] = {
          "live_price": latest_close,
          "earn_lo": swing_lo,
          "earn_hi": swing_hi,
          "earn_range": swing_range,
          "earn_swing_start": _swing_start_label,
          "earn_swing_end": _swing_end_label,
          "wk_lo": _wk_lo_v,
          "wk_hi": _wk_hi_v,
          "wk_range": _wk_range_v,
          "wk_start_label": _wk_start_lbl,
      }

      # ── Earnings Scenario ─────────────────────────────────────────────────
      st.markdown("---")
      st.markdown("#### 📅 Earnings Scenario")

      # Ensure date filtering is properly scoped for earnings scenario section
      _di_dates_earn = np.array([d.date() if hasattr(d, "date") else d for d in daily_df.index])
      
      # Close price on swing_end (last earnings date) — the reference price
      _swing_end_rows = daily_df[_di_dates_earn <= swing_end]
      _swing_end_close = float(_swing_end_rows["close"].iloc[-1]) if not _swing_end_rows.empty else latest_close

      # Single combined row: earnings swing + weekly, deltas from swing_end close
      _earn_to_lo = swing_lo - _swing_end_close
      _earn_to_hi = swing_hi - _swing_end_close
      _wk_to_lo   = _wk_lo_v - _swing_end_close
      _wk_to_hi   = _wk_hi_v - _swing_end_close
      _fsum_rows = [
          {
              "Earnings Window": f"{_swing_start_label} → {_swing_end_label}",
              "Close on Earn Date": f"${_swing_end_close:.2f}",
              "Earn Lo (0%)": f"${swing_lo:.2f}",
              "Earn Hi (100%)": f"${swing_hi:.2f}",
              "Earn Range": f"${swing_range:.2f}",
              "Earn Δ Lo": f"{_earn_to_lo:+.2f} ({_earn_to_lo/_swing_end_close*100:+.1f}%)",
              "Earn Δ Hi": f"{_earn_to_hi:+.2f} ({_earn_to_hi/_swing_end_close*100:+.1f}%)",
              "Weekly Window": f"Wk of {_wk_start_lbl}",
              "Wk Lo (0%)": f"${_wk_lo_v:.2f}",
              "Wk Hi (100%)": f"${_wk_hi_v:.2f}",
              "Wk Range": f"${_wk_range_v:.2f}",
              "Wk Δ Lo": f"{_wk_to_lo:+.2f} ({_wk_to_lo/_swing_end_close*100:+.1f}%)",
              "Wk Δ Hi": f"{_wk_to_hi:+.2f} ({_wk_to_hi/_swing_end_close*100:+.1f}%)",
          },
      ]
      st.dataframe(pd.DataFrame(_fsum_rows), use_container_width=True, hide_index=True)

      # ── Fib Conclusion + Fundamental Bias + News ─────────────────────────
      try:
          _fbes = _fb_score
      except NameError:
          _fbes = 0

      if _fbes >= 3:     _fb_text_es = "🐂 BULLISH"
      elif _fbes >= 1:   _fb_text_es = "🐂 LEAN BULLISH"
      elif _fbes > -1:   _fb_text_es = "⚖️ NEUTRAL"
      elif _fbes > -3:   _fb_text_es = "🐻 LEAN BEARISH"
      else:              _fb_text_es = "🐻 BEARISH"
      _fbes_color = ("#00e5a0" if _fbes >= 3 else "#7ccfb0" if _fbes >= 1
                     else "#f5c842" if _fbes > -1 else "#ff8c8c" if _fbes > -3 else "#ff4d6a")

      # News sentiment (uses cached result — no extra network call if already fetched)
      try:
          from news_sentiment import get_news_details as _gnd_es
          _news_det_es  = _gnd_es(symbol)
          _news_lbl_es  = _news_det_es["label"]
          _news_good_es = _news_det_es["good_score"]
          _news_bad_es  = _news_det_es["bad_score"]
      except Exception:
          _news_lbl_es, _news_good_es, _news_bad_es = "No", 0, 0
      _news_text_es = ("📰 POSITIVE" if _news_lbl_es == "Good"
                       else "📰 NEGATIVE" if _news_lbl_es == "Bad" else "📰 NEUTRAL")
      _news_clr_es  = ("#00e5a0" if _news_lbl_es == "Good"
                       else "#ff4d6a" if _news_lbl_es == "Bad" else "#f5c842")

      # Weekly position of close on earnings date (0% = at wk low, 100% = at wk high)
      _wk_pos_pct = (_swing_end_close - _wk_lo_v) / _wk_range_v * 100 if _wk_range_v > 0 else 50.0
      if _wk_pos_pct >= 70:
          _wk_zone, _wk_zone_desc, _wk_zone_clr, _wk_sig = "HIGH ZONE", "Near Weekly Hi — Extension territory", "#ff4d6a", "ext"
      elif _wk_pos_pct <= 30:
          _wk_zone, _wk_zone_desc, _wk_zone_clr, _wk_sig = "LOW ZONE", "Near Weekly Lo — Retrace territory", "#00e5a0", "ret"
      else:
          _wk_zone, _wk_zone_desc, _wk_zone_clr, _wk_sig = "MID ZONE", "Mid Weekly Range — Balanced", "#f5c842", "mid"

      # Nearest swing Fib level from close on earnings date
      _earn_pos_pct = (_swing_end_close - swing_lo) / swing_range * 100 if swing_range > 0 else 50.0
      if _earn_pos_pct >= 70:
          _earn_zone = "HIGH"
      elif _earn_pos_pct <= 30:
          _earn_zone = "LOW"
      else:
          _earn_zone = "MID"
      _near_fib = min(fib_levels.items(), key=lambda kv: abs(kv[1] - _swing_end_close))
      _near_fib_name, _near_fib_val = _near_fib

      # News modifier and price level suffix for conclusion
      _news_tag_es = (" + Positive news" if _news_lbl_es == "Good"
                      else " + Negative news" if _news_lbl_es == "Bad" else "")
      _pl_es = (f" | Close: ${_swing_end_close:.2f} · Nearest Fib: {_near_fib_name} ${_near_fib_val:.2f}"
                f" · Wk Hi: ${_wk_hi_v:.2f} · Wk Lo: ${_wk_lo_v:.2f}"
                f" · Earn Hi: ${swing_hi:.2f} · Earn Lo: ${swing_lo:.2f}")

      # ── Options Strategy Suggestion ──────────────────────────────────────
      # Determine expiration: next earnings if available, else ~30 days out
      try:
          _opt_exp_dt = datetime.strptime(next_earn, "%Y-%m-%d").date() if next_earn else None
      except Exception:
          _opt_exp_dt = None
      if _opt_exp_dt is None or _opt_exp_dt <= date.today():
          _opt_exp_dt = date.today() + timedelta(days=30)
      _opt_exp_str = _opt_exp_dt.strftime("%Y-%m-%d")

      # Sort fib levels by price for strike selection
      _opt_fibs_sorted = sorted(fib_levels.items(), key=lambda kv: kv[1])
      _opt_fib_prices = [p for _, p in _opt_fibs_sorted]

      # Find fibs nearest to price for center strike, then pick wings
      _opt_center_idx = min(range(len(_opt_fib_prices)),
                            key=lambda i: abs(_opt_fib_prices[i] - _swing_end_close))
      _opt_center = round(_opt_fib_prices[_opt_center_idx], 2)
      _opt_lower = round(_opt_fib_prices[max(0, _opt_center_idx - 1)], 2)
      _opt_upper = round(_opt_fib_prices[min(len(_opt_fib_prices) - 1, _opt_center_idx + 1)], 2)

      # Round strikes to nearest whole dollar for practical use
      _opt_center_rd = round(_opt_center)
      _opt_lower_rd  = round(_opt_lower)
      _opt_upper_rd  = round(_opt_upper)

      # Determine bullish / bearish / neutral bias
      _opt_is_bullish  = (_wk_sig == "ret" and _fbes >= 1) or (_wk_sig == "mid" and _fbes >= 1) or (_wk_sig == "ret" and _news_lbl_es == "Good")
      _opt_is_bearish  = (_wk_sig == "ext" and _fbes <= -1) or (_wk_sig == "mid" and _fbes <= -1) or (_wk_sig == "ext" and _news_lbl_es == "Bad")

      # Try Alpaca options chain for real contract data
      _opt_dir = "LONG" if _opt_is_bullish else ("SHORT" if _opt_is_bearish else None)
      _alpaca_opt = None
      _alpaca_opt_text = "N/A"
      try:
          _alpaca_opt = get_options_strategy_alpaca(symbol, _swing_end_close, _opt_dir, _earn_zone, api_key, api_secret)
      except Exception:
          pass

      if _alpaca_opt and _alpaca_opt.get("summary"):
          _alpaca_opt_text = _alpaca_opt["summary"]
          if _alpaca_opt.get("alt"):
              _alpaca_opt_text += f" | {_alpaca_opt['alt']}"

      if _opt_is_bullish:
          _opt_strat = (f"📈 {symbol} Options: Bull Call Spread — Buy ${_opt_lower_rd} Call / Sell ${_opt_center_rd} Call"
                        f" · Exp {_opt_exp_str}"
                        f" | Alt: Buy ${_opt_lower_rd} Call · Exp {_opt_exp_str}")
      elif _opt_is_bearish:
          _opt_strat = (f"📉 {symbol} Options: Bear Put Spread — Buy ${_opt_upper_rd} Put / Sell ${_opt_center_rd} Put"
                        f" · Exp {_opt_exp_str}"
                        f" | Alt: Buy ${_opt_upper_rd} Put · Exp {_opt_exp_str}")
      else:
          # Neutral / MID zone → Iron Butterfly or Straddle
          _opt_strat = (f"🦋 {symbol} Options: Iron Butterfly — Sell ${_opt_center_rd} Call+Put / "
                        f"Buy ${_opt_upper_rd} Call + Buy ${_opt_lower_rd} Put"
                        f" · Exp {_opt_exp_str}"
                        f" | Alt: Buy Straddle ${_opt_center_rd} Call+Put · Exp {_opt_exp_str}")

      # Composite conclusion with price levels
      if _wk_sig == "ext" and _fbes >= 1:
          _conc_es = (f"⚠️ Extended near Weekly Hi (${_wk_hi_v:.2f} / {_wk_pos_pct:.0f}% of wk range)"
                      f" + Bullish fundamentals{_news_tag_es} — possible gap-up on earnings, but"
                      f" extended near {_near_fib_name} (${_near_fib_val:.2f}). Use trailing stop.{_pl_es}")
      elif _wk_sig == "ext" and _fbes <= -1:
          _conc_es = (f"🚨 Extended near Weekly Hi (${_wk_hi_v:.2f} / {_wk_pos_pct:.0f}% of wk range)"
                      f" + Bearish fundamentals{_news_tag_es} — high reversal risk on earnings."
                      f" Near {_near_fib_name} (${_near_fib_val:.2f}). Reduce position size.{_pl_es}")
      elif _wk_sig == "ret" and _fbes >= 1:
          _conc_es = (f"✅ Retrace near Weekly Lo (${_wk_lo_v:.2f} / {_wk_pos_pct:.0f}% of wk range)"
                      f" + Bullish fundamentals{_news_tag_es} — potential long setup near"
                      f" {_near_fib_name} (${_near_fib_val:.2f}). Earnings catalyst could spark recovery.{_pl_es}")
      elif _wk_sig == "ret" and _fbes <= -1:
          _conc_es = (f"🐻 Retrace near Weekly Lo (${_wk_lo_v:.2f} / {_wk_pos_pct:.0f}% of wk range)"
                      f" + Bearish fundamentals{_news_tag_es} — downtrend likely despite"
                      f" {_near_fib_name} (${_near_fib_val:.2f}) support. Wait for confirmation.{_pl_es}")
      elif _wk_sig == "ext":
          _conc_es = (f"⚠️ Extended near Weekly Hi (${_wk_hi_v:.2f} / {_wk_pos_pct:.0f}% of wk range)"
                      f"{_news_tag_es} — watch for reversal near {_near_fib_name} (${_near_fib_val:.2f})"
                      f" post-earnings.{_pl_es}")
      elif _wk_sig == "ret":
          _conc_es = (f"👀 Retrace near Weekly Lo (${_wk_lo_v:.2f} / {_wk_pos_pct:.0f}% of wk range)"
                      f"{_news_tag_es} — possible base at {_near_fib_name} (${_near_fib_val:.2f})."
                      f" Watch for earnings catalyst.{_pl_es}")
      else:
          _conc_es = (f"⚖️ Mid-range ({_wk_pos_pct:.0f}% of wk range){_news_tag_es} — no strong"
                      f" directional edge near {_near_fib_name} (${_near_fib_val:.2f})."
                      f" Watch opening gap direction on earnings day.{_pl_es}")

      # Append options strategy to conclusion
      _conc_es = f"{_conc_es} | {_opt_strat}"

      # ── Accumulate download row (one per symbol) ──────────────────────────
      _dl_rows = st.session_state.setdefault("_fib_dl_rows", [])
      _dl_row = {
          "Ticker":              symbol,
          "As Of":               str(_asof_d),
          "Close on Earn Date":  round(_swing_end_close, 2),
          "Earn Window":         f"{_swing_start_label} → {_swing_end_label}",
          "Earn Lo":             round(swing_lo, 2),
          "Earn Hi":             round(swing_hi, 2),
          "Earn Range":          round(swing_range, 2),
          "Earn Pos %":          round(_earn_pos_pct, 1),
          "Weekly Window":       f"Wk of {_wk_start_lbl}",
          "Wk Lo":               round(_wk_lo_v, 2),
          "Wk Hi":               round(_wk_hi_v, 2),
          "Wk Range":            round(_wk_range_v, 2),
          "Wk Pos %":            round(_wk_pos_pct, 1),
          "Earn Zone":            _earn_zone,                          # HIGH / LOW / MID
          "Weekly Zone":         _wk_zone.replace(" ZONE", ""),   # HIGH / LOW / MID
          "Nearest Fib":         _near_fib_name,
          "Nearest Fib Price":   round(_near_fib_val, 2),
          "Fund Bias":           _fb_text_es,
          "Fund Score":          round(_fbes, 1),
          "News Sentiment":      _news_text_es,
          "News Bullish Signals": _news_good_es,
          "News Bearish Signals": _news_bad_es,
          "Options Strategy":    _opt_strat,
          "Alpaca Options":      _alpaca_opt_text,
          "Conclusion":          _conc_es,
      }
      # Add all individual fib levels as columns (plain key e.g. "E 127.2%", "R 61.8%", "N -23.6%")
      for _fn, _fv in sorted(fib_levels.items(), key=lambda x: x[1], reverse=True):
          _dl_row[_fn] = round(_fv, 2)

      # Fib Compression: Y if 3+ fib levels cluster within 3% of swing range
      _fc_vals = sorted(fib_levels.values())
      _fc_thresh = swing_range * 0.03 if swing_range > 0 else 0
      _fc_found = False
      if len(_fc_vals) >= 3 and _fc_thresh > 0:
          for _fc_i in range(len(_fc_vals) - 2):
              if _fc_vals[_fc_i + 2] - _fc_vals[_fc_i] <= _fc_thresh:
                  _fc_found = True
                  break
      _dl_row["Fib Compression"] = "Y" if _fc_found else "N"

      _dl_rows.append(_dl_row)

      st.markdown(
          f'<div style="background:#0d0f17;border:1px solid #1a1d2e;padding:14px;border-radius:8px;margin:10px 0">'
          f'<div style="font-size:10px;color:#6b7099;font-weight:600;letter-spacing:0.5px;margin-bottom:10px">FIB SCENARIO ANALYSIS</div>'
          f'<div style="display:flex;gap:10px;flex-wrap:wrap">'
          f'<div style="flex:1;min-width:140px;background:#12141f;border:1px solid #1a1d2e;padding:10px;border-radius:6px">'
          f'<div style="font-size:9px;color:#6b7099">WEEKLY FIB POSITION</div>'
          f'<div style="font-size:13px;color:{_wk_zone_clr};font-weight:700;margin-top:3px">{_wk_zone}</div>'
          f'<div style="font-size:10px;color:#8892b0;margin-top:2px">{_wk_zone_desc}</div>'
          f'<div style="font-size:9px;color:#6b7099;margin-top:3px">Close at {_wk_pos_pct:.0f}% of weekly range</div>'
          f'</div>'
          f'<div style="flex:1;min-width:140px;background:#12141f;border:1px solid #1a1d2e;padding:10px;border-radius:6px">'
          f'<div style="font-size:9px;color:#6b7099">NEAREST SWING FIB</div>'
          f'<div style="font-size:13px;color:#ccd6f6;font-weight:700;margin-top:3px">{_near_fib_name}</div>'
          f'<div style="font-size:10px;color:#8892b0;margin-top:2px">${_near_fib_val:.2f}</div>'
          f'<div style="font-size:9px;color:#6b7099;margin-top:3px">Close at {_earn_pos_pct:.0f}% of swing</div>'
          f'</div>'
          f'<div style="flex:1;min-width:140px;background:#12141f;border:1px solid #1a1d2e;padding:10px;border-radius:6px">'
          f'<div style="font-size:9px;color:#6b7099">FUNDAMENTAL BIAS</div>'
          f'<div style="font-size:13px;color:{_fbes_color};font-weight:700;margin-top:3px">{_fb_text_es}</div>'
          f'<div style="font-size:9px;color:#6b7099;margin-top:3px">Score: {_fbes:+.1f}</div>'
          f'</div>'
          f'<div style="flex:1;min-width:140px;background:#12141f;border:1px solid {_news_clr_es}40;padding:10px;border-radius:6px">'
          f'<div style="font-size:9px;color:#6b7099">NEWS SENTIMENT</div>'
          f'<div style="font-size:13px;color:{_news_clr_es};font-weight:700;margin-top:3px">{_news_text_es}</div>'
          f'<div style="font-size:9px;color:#6b7099;margin-top:3px">+{_news_good_es} bullish / -{_news_bad_es} bearish signals</div>'
          f'</div>'
          f'</div>'
          f'<div style="margin-top:10px;padding:10px;background:#12141f;border-left:3px solid #4d9fff;border-radius:0 6px 6px 0">'
          f'<div style="font-size:9px;color:#6b7099;margin-bottom:3px">CONCLUSION</div>'
          f'<div style="font-size:12px;color:#e8ecff;line-height:1.6">{_conc_es}</div>'
          f'</div>'
          f'</div>',
          unsafe_allow_html=True
      )

      # ── Pre-Earnings Card (if 0–1 days before earnings) ──────────────────
      _days_to_earn_es = (swing_end - _asof_d).days
      if 0 <= _days_to_earn_es <= 1:
          _em_proxy_pct = _wk_range_v / _swing_end_close * 100 if _swing_end_close > 0 else 2.0
          _em_up_es = _swing_end_close + _wk_range_v * 0.5
          _em_dn_es = _swing_end_close - _wk_range_v * 0.5
          _pre_earn_title_es = "DAY OF EARNINGS" if _days_to_earn_es == 0 else "1 DAY BEFORE EARNINGS"
          _one_bias  = ("Trend day UP likely" if _fbes >= 1 and _news_lbl_es != "Bad"
                        else "Trend day DOWN likely" if _fbes <= -1 and _news_lbl_es != "Good"
                        else "Mixed signals — watch open direction" if _news_lbl_es == "Bad" and _fbes >= 1
                        else "Range/chop day")
          _gap_bias  = ("Bullish surprise possible" if _fbes >= 1 and _news_lbl_es == "Good"
                        else "Bearish surprise possible" if _fbes <= -1 and _news_lbl_es == "Bad"
                        else "Gap up possible" if _news_lbl_es == "Good"
                        else "Gap down risk" if _news_lbl_es == "Bad"
                        else "Direction uncertain")
          st.markdown(
              f'<div style="background:#12141f;border:1px solid #f5c84250;padding:14px;border-radius:8px;margin:8px 0">'
              f'<div style="font-size:10px;color:#f5c842;font-weight:700;letter-spacing:0.5px;margin-bottom:8px">'
              f'⚡ {_pre_earn_title_es} · As of {_asof_d}</div>'
              f'<div style="font-size:11px;color:#ccd6f6;margin-bottom:10px">'
              f'Close on earn date: <b>${_swing_end_close:.2f}</b> · '
              f'Weekly range (EM proxy): <b>${_wk_range_v:.2f} ({_em_proxy_pct:.1f}%)</b></div>'
              f'<div style="display:flex;gap:10px;flex-wrap:wrap">'
              f'<div style="flex:1;min-width:140px;background:#0d0f17;border:1px solid #00e5a030;padding:10px;border-radius:6px">'
              f'<div style="font-size:9px;color:#6b7099">ONE · Opens Near Close</div>'
              f'<div style="font-size:12px;color:#00e5a0;font-weight:700;margin-top:3px">~${_swing_end_close:.2f}</div>'
              f'<div style="font-size:9px;color:#8892b0;margin-top:2px">No gap · {_one_bias}</div>'
              f'</div>'
              f'<div style="flex:1;min-width:140px;background:#0d0f17;border:1px solid #4d9fff30;padding:10px;border-radius:6px">'
              f'<div style="font-size:9px;color:#6b7099">OBE · Partial Gap</div>'
              f'<div style="font-size:12px;color:#4d9fff;font-weight:700;margin-top:3px">${_em_dn_es:.2f} – ${_em_up_es:.2f}</div>'
              f'<div style="font-size:9px;color:#8892b0;margin-top:2px">Wk Lo: ${_wk_lo_v:.2f} → Wk Hi: ${_wk_hi_v:.2f}</div>'
              f'</div>'
              f'<div style="flex:1;min-width:140px;background:#0d0f17;border:1px solid #ff4d6a30;padding:10px;border-radius:6px">'
              f'<div style="font-size:9px;color:#6b7099">OPS/GAP · Opens Past Stop</div>'
              f'<div style="font-size:12px;color:#ff4d6a;font-weight:700;margin-top:3px">&gt;${_wk_hi_v:.2f} or &lt;${_wk_lo_v:.2f}</div>'
              f'<div style="font-size:9px;color:#8892b0;margin-top:2px">{_gap_bias}</div>'
              f'</div>'
              f'</div>'
              f'<div style="margin-top:8px;font-size:10px;color:#6b7099;border-top:1px solid #1a1d2e;padding-top:8px">'
              f'💡 Key levels: Earn Close <b>${_swing_end_close:.2f}</b> · '
              f'Earn Hi <b>${swing_hi:.2f}</b> · Earn Lo <b>${swing_lo:.2f}</b> · '
              f'Nearest Fib <b>{_near_fib_name} (${_near_fib_val:.2f})</b>'
              f'</div>'
              f'</div>',
              unsafe_allow_html=True
          )

      # Mon-Fri scenario grid (computed from daily_df — no extra API call)
      st.caption("ONE=Opens Near Entry · OBE=Opens Between Entry & Stop · OPS=Opens Past Stop · GAP=Big Gap")
      try:
          # _asof_d and _wk_start_d_fib already defined above (week of latest bar)
          _wk_start_d = _wk_start_d_fib
          _weekdays_es = [_wk_start_d + timedelta(days=i) for i in range(5)]
          _day_map_es = {0: "Mon", 1: "Tue", 2: "Wed", 3: "Thu", 4: "Fri"}
          _di_es = daily_df.copy()
          _di_dates_es = np.array([d.date() if hasattr(d, "date") else d for d in _di_es.index])
          _scen_row_es = {}
          for _wd in _weekdays_es:
              _this_m = _di_dates_es == _wd
              _prev_m = _di_dates_es < _wd
              if not _this_m.any() or not _prev_m.any():
                  _scen_row_es[_day_map_es[_wd.weekday()]] = "N/A"
                  continue
              _prev_d = _di_es[_prev_m].iloc[-1]
              _this_d = _di_es[_this_m].iloc[0]
              _pc = float(_prev_d["close"])
              _po = float(_prev_d["open"]) if "open" in _prev_d.index else _pc
              _to = float(_this_d["open"]) if "open" in _this_d.index else float(_this_d["close"])
              _dir = "LONG" if _pc >= _po else "SHORT"
              _atr_src_es = _di_es[_prev_m].tail(30)
              if len(_atr_src_es) >= 2 and "high" in _di_es.columns:
                  _hi_s = _atr_src_es["high"].astype(float)
                  _lo_s = _atr_src_es["low"].astype(float)
                  _cl_s = _atr_src_es["close"].astype(float)
                  _tr_s = pd.concat([_hi_s - _lo_s, (_hi_s - _cl_s.shift(1)).abs(), (_lo_s - _cl_s.shift(1)).abs()], axis=1).max(axis=1)
                  _atr14 = float(_tr_s.rolling(14, min_periods=1).mean().iloc[-1])
                  if not np.isfinite(_atr14) or _atr14 == 0:
                      _atr14 = max(_pc * 0.01, 0.01)
              else:
                  _atr14 = max(_pc * 0.01, 0.01)
              _stop = (_pc - _atr14 * 0.3) if _dir == "LONG" else (_pc + _atr14 * 0.3)
              _gap_thr_es = _pc * 0.005
              if _dir == "LONG":
                  _cls = "GAP" if (_to - _pc) > _gap_thr_es else ("ONE" if _to >= _pc else ("OBE" if _to > _stop else "OPS"))
              else:
                  _cls = "GAP" if (_pc - _to) > _gap_thr_es else ("ONE" if _to <= _pc else ("OBE" if _to < _stop else "OPS"))
              _scen_row_es[_day_map_es[_wd.weekday()]] = _cls

          _scen_df_es = pd.DataFrame([_scen_row_es])
          _scen_cols_es = [c for c in ["Mon", "Tue", "Wed", "Thu", "Fri"] if c in _scen_df_es.columns]

          def _color_scen_es(v):
              vv = str(v).upper().strip()
              if vv == "ONE": return "background-color: #0a3d1f; color: #00e5a0; font-weight: 700"
              if vv == "OBE": return "background-color: #0b2b4a; color: #4d9fff; font-weight: 700"
              if vv == "OPS": return "background-color: #3d0a1a; color: #ff4d6a; font-weight: 700"
              if vv == "GAP": return "background-color: #3d3a0a; color: #f5c842; font-weight: 700"
              return ""

          _mon_lbl_es = _wk_start_d.strftime("%b %d")
          _fri_lbl_es = (_wk_start_d + timedelta(days=4)).strftime("%b %d, %Y")
          st.caption(f"Week: {_mon_lbl_es} \u2013 {_fri_lbl_es}")
          st.dataframe(_scen_df_es.style.applymap(_color_scen_es, subset=_scen_cols_es), use_container_width=True, hide_index=True)
      except Exception as _scen_err_es:
          st.caption(f"Scenario grid unavailable: {_scen_err_es}")

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

    # ── Fib Scenario Report — rendered at the TOP via placeholder ──────
    _dl_rows_final = st.session_state.get("_fib_dl_rows", [])
    if _dl_rows_final:
        import io as _io
        import re as _re_fib
        _dl_df = pd.DataFrame(_dl_rows_final)
        _csv_buf = _io.StringIO()
        _dl_df.to_csv(_csv_buf, index=False)
        _dl_label = f"📥 Download Fib Scenario Report ({len(_dl_rows_final)} ticker{'s' if len(_dl_rows_final)!=1 else ''})"

        # ── Sort fib columns into E (ext desc) / R (ret asc) / N (neg desc) ──
        def _fib_sort_key(name):
            try: return float(_re_fib.search(r'[-\d.]+', name.split(' ', 1)[1]).group())
            except: return 0.0
        _raw_fib_cols = [c for c in _dl_df.columns
                         if c.startswith("E ") or c.startswith("R ") or c.startswith("N -")]
        _e_cols = sorted([c for c in _raw_fib_cols if c.startswith("E ")],  key=_fib_sort_key, reverse=True)
        _r_cols = sorted([c for c in _raw_fib_cols if c.startswith("R ")],  key=_fib_sort_key, reverse=False)
        _n_cols = sorted([c for c in _raw_fib_cols if c.startswith("N -")], key=_fib_sort_key, reverse=True)
        _fib_display_cols = _e_cols + _r_cols + _n_cols

        # ── Column order: Ticker As_Of Close EarnZone WeeklyZone | E... R... N... | Fund News Conclusion ──
        _base_cols  = [c for c in ["Ticker", "As Of", "Close on Earn Date", "Earn Zone", "Weekly Zone"] if c in _dl_df.columns]
        _trail_cols = [c for c in ["Fib Compression", "Fund Bias", "News Sentiment", "Options Strategy", "Alpaca Options", "Conclusion"] if c in _dl_df.columns]
        _all_preview_cols = _base_cols + _fib_display_cols + _trail_cols

        # ── Build display df: numeric price cols → $X.XX strings ──
        _disp_df = _dl_df[_all_preview_cols].copy()
        for _pc in ["Close on Earn Date"] + _fib_display_cols:
            if _pc in _disp_df.columns:
                _disp_df[_pc] = _disp_df[_pc].apply(
                    lambda x: f"${x:.2f}" if isinstance(x, (int, float)) and pd.notna(x) else str(x))

        # ── Coloring ──
        def _color_earn_zone(v):
            v = str(v)
            if v == "HIGH": return "background-color:#3d0a1a;color:#ff4d6a;font-weight:700"
            if v == "LOW":  return "background-color:#0a3d1f;color:#00e5a0;font-weight:700"
            return "background-color:#3d3a0a;color:#f5c842;font-weight:700"
        def _color_wk_zone(v):
            v = str(v)
            if v == "HIGH": return "background-color:#3d0a1a;color:#ff4d6a;font-weight:700"
            if v == "LOW":  return "background-color:#0a3d1f;color:#00e5a0;font-weight:700"
            return "background-color:#3d3a0a;color:#f5c842;font-weight:700"
        def _color_fund(v):
            v = str(v)
            if "BULLISH" in v and "LEAN" not in v: return "color:#00e5a0;font-weight:700"
            if "LEAN BULLISH" in v:                return "color:#7ccfb0;font-weight:700"
            if "BEARISH" in v and "LEAN" not in v: return "color:#ff4d6a;font-weight:700"
            if "LEAN BEARISH" in v:                return "color:#ff8c8c;font-weight:700"
            return "color:#f5c842"
        def _color_news(v):
            v = str(v)
            if "POSITIVE" in v: return "color:#00e5a0;font-weight:700"
            if "NEGATIVE" in v: return "color:#ff4d6a;font-weight:700"
            return "color:#f5c842"
        def _color_fib_comp(v):
            return "color:#ff4d6a;font-weight:700" if str(v) == "Y" else "color:#6b7099"

        # ── Row-wise Fib zone highlighting based on Weekly Zone ──────────────
        _e_cols_set = set(_e_cols)
        _r_cols_set = set(_r_cols)
        _golden_set = {"R 38.2%", "R 50.0%", "R 61.8%"}
        def _highlight_fib_by_zone(row):
            zone = str(row.get("Weekly Zone", ""))
            out  = []
            for col in row.index:
                if zone == "HIGH" and col in _r_cols_set:
                    # HIGH zone → retrace targets: amber highlight
                    out.append("background-color:#1f1400;color:#f5c842;font-weight:700")
                elif zone == "LOW" and col in _e_cols_set:
                    # LOW zone → extension targets: green highlight
                    out.append("background-color:#001a0a;color:#00e5a0;font-weight:700")
                elif col in _golden_set:
                    out.append("background-color:#1a1500;color:#d4a017;font-weight:600;border-bottom:2px solid #d4a01780")
                else:
                    out.append("")
            return out

        _styled = _disp_df.style
        if "Earn Zone"      in _disp_df.columns: _styled = _styled.applymap(_color_earn_zone, subset=["Earn Zone"])
        if "Weekly Zone"    in _disp_df.columns: _styled = _styled.applymap(_color_wk_zone, subset=["Weekly Zone"])
        if "Fund Bias"      in _disp_df.columns: _styled = _styled.applymap(_color_fund,    subset=["Fund Bias"])
        if "News Sentiment" in _disp_df.columns: _styled = _styled.applymap(_color_news,    subset=["News Sentiment"])
        if "Fib Compression" in _disp_df.columns: _styled = _styled.applymap(_color_fib_comp, subset=["Fib Compression"])
        if "Weekly Zone"    in _disp_df.columns and (_e_cols or _r_cols):
            _styled = _styled.apply(_highlight_fib_by_zone, axis=1)

        _tbl_height = min(len(_dl_rows_final) * 35 + 48, 600)

        # ── Date ranges ABOVE the report (separate placeholder) ──────────────
        _range_parts = []
        for _rr in _dl_rows_final:
            _tk   = _rr.get("Ticker", "?")
            _asof = _rr.get("As Of", "?")
            _ew   = _rr.get("Earn Window", "?")
            _ww   = _rr.get("Weekly Window", "?")
            _range_parts.append(
                f'<span style="margin-right:18px">'
                f'<b style="color:#ccd6f6">{_tk}</b> · '
                f'<span style="color:#6b7099">As Of:</span> <b style="color:#f5c842">{_asof}</b> · '
                f'<span style="color:#6b7099">Earn window:</span> <span style="color:#8892b0">{_ew}</span> · '
                f'<span style="color:#6b7099">{_ww}</span>'
                f'</span>'
            )
        _daterange_ph.markdown(
            f'<div style="background:#0d0f17;border:1px solid #1a1d2e;padding:8px 12px;'
            f'border-radius:6px;font-size:10px;margin-bottom:4px;line-height:2">'
            f'<span style="color:#6b7099;font-weight:600;letter-spacing:0.4px">DATE RANGES · </span>'
            f'{"  ".join(_range_parts)}'
            f'</div>',
            unsafe_allow_html=True,
        )

        # ── Fetch live prices for current-price fib highlight ───────────────
        try:
            _live_tickers = list(_dl_df["Ticker"].unique())
            _lp_raw = yf.download(_live_tickers, period="1d", progress=False, auto_adjust=True)
            if hasattr(_lp_raw, "columns") and "Close" in _lp_raw.columns:
                _lp_series = _lp_raw["Close"].iloc[-1] if len(_lp_raw) > 0 else pd.Series(dtype=float)
            else:
                _lp_series = pd.Series(dtype=float)
            _live_prices = {}
            for _ltk in _live_tickers:
                try:
                    if _ltk in _lp_series.index:
                        _lv = float(_lp_series[_ltk])
                    else:
                        _lv = float(yf.Ticker(_ltk).fast_info.last_price)
                    _live_prices[_ltk] = _lv
                except Exception:
                    _live_prices[_ltk] = None
        except Exception:
            _live_prices = {}

        # ── Current-price highlighter: marks fib col nearest to live price ──
        def _highlight_live_price_row(row):
            ticker   = str(row.get("Ticker", ""))
            live_px  = _live_prices.get(ticker)
            styles   = [""] * len(row)
            if live_px is None:
                return styles
            best_col  = None
            best_diff = float("inf")
            for col in _fib_display_cols:
                if col not in row.index:
                    continue
                try:
                    val = float(str(row[col]).replace("$", "").strip())
                    diff = abs(val - live_px)
                    if diff < best_diff:
                        best_diff = diff
                        best_col  = col
                except Exception:
                    pass
            if best_col is not None:
                col_idx = list(row.index).index(best_col)
                styles[col_idx] = "background-color:#0a1f3a;color:#ffffff;font-weight:900;border:2px solid #4d9fff"
            return styles

        with _report_ph.container():
            st.markdown("#### 📥 Fib Scenario Report")
            st.caption(
                f"{len(_dl_rows_final)} ticker{'s' if len(_dl_rows_final)!=1 else ''} · "
                f"E=Extensions ({len(_e_cols)}) · R=Retracements ({len(_r_cols)}) · "
                f"N=Negative ({len(_n_cols)}) · prices as $X.XX · 🔵 = current price level")

            # ── Split by Earn Zone ─────────────────────────────────────────
            _ez_config = [
                ("HIGH", "#ff4d6a", "🔴"),
                ("MID",  "#f5c842", "🟡"),
                ("LOW",  "#00e5a0", "🟢"),
            ]
            _any_ez_col = "Earn Zone" in _dl_df.columns
            for _ez, _ez_color, _ez_icon in _ez_config:
                if _any_ez_col:
                    _ez_mask = _dl_df["Earn Zone"] == _ez
                    _ez_disp = _disp_df[_ez_mask.values].copy()
                else:
                    _ez_disp = _disp_df.copy()
                if _ez_disp.empty:
                    continue
                _ez_count = len(_ez_disp)
                st.markdown(
                    f'<div style="background:rgba(0,0,0,0.3);border-left:3px solid {_ez_color}40;'
                    f'padding:6px 12px;margin:8px 0 4px;border-radius:0 4px 4px 0">'
                    f'<span style="font-size:14px">{_ez_icon}</span> '
                    f'<b style="color:{_ez_color};font-size:13px">{_ez} Earn Zone</b> '
                    f'<span style="color:#6b7099;font-size:11px">({_ez_count} ticker{"s" if _ez_count != 1 else ""})</span>'
                    f'</div>',
                    unsafe_allow_html=True,
                )
                _ez_styled = _ez_disp.style
                if "Earn Zone"      in _ez_disp.columns: _ez_styled = _ez_styled.applymap(_color_earn_zone, subset=["Earn Zone"])
                if "Weekly Zone"    in _ez_disp.columns: _ez_styled = _ez_styled.applymap(_color_wk_zone,   subset=["Weekly Zone"])
                if "Fund Bias"      in _ez_disp.columns: _ez_styled = _ez_styled.applymap(_color_fund,      subset=["Fund Bias"])
                if "News Sentiment" in _ez_disp.columns: _ez_styled = _ez_styled.applymap(_color_news,      subset=["News Sentiment"])
                if "Fib Compression" in _ez_disp.columns: _ez_styled = _ez_styled.applymap(_color_fib_comp, subset=["Fib Compression"])
                if "Weekly Zone"    in _ez_disp.columns and (_e_cols or _r_cols):
                    _ez_styled = _ez_styled.apply(_highlight_fib_by_zone, axis=1)
                if _fib_display_cols and _live_prices:
                    _ez_styled = _ez_styled.apply(_highlight_live_price_row, axis=1)
                _ez_height = min(_ez_count * 35 + 48, 400)
                st.dataframe(_ez_styled, use_container_width=True, hide_index=True, height=_ez_height)
                if not _any_ez_col:
                    break  # no Earn Zone column → show once without split

            # ── 🥇 Golden Zone ──
            _gz4_rows = []
            for _, _gz4_r in _disp_df.iterrows():
                try:
                    _gz4_tk = _gz4_r["Ticker"]
                    _gz4_38 = float(str(_gz4_r.get("R 38.2%", "")).replace("$", ""))
                    _gz4_61 = float(str(_gz4_r.get("R 61.8%", "")).replace("$", ""))
                    _gz4_px = _live_prices.get(_gz4_tk) if _live_prices else None
                    if _gz4_px is None:
                        _gz4_px = float(str(_gz4_r.get("Close on Earn Date", "")).replace("$", ""))
                    _gz4_lo, _gz4_hi = min(_gz4_38, _gz4_61), max(_gz4_38, _gz4_61)
                    if _gz4_lo <= _gz4_px <= _gz4_hi:
                        _gz4_rows.append({"Ticker": _gz4_tk, "Price": f"${_gz4_px:.2f}",
                                          "R 38.2%": _gz4_r.get("R 38.2%", ""),
                                          "R 50.0%": _gz4_r.get("R 50.0%", ""),
                                          "R 61.8%": _gz4_r.get("R 61.8%", ""),
                                          "Earn Zone": _gz4_r.get("Earn Zone", "")})
                except Exception:
                    pass
            if _gz4_rows:
                st.markdown(
                    '<div style="background:linear-gradient(135deg,#1a1500,#1f1800);border:1px solid #d4a01740;'
                    'border-radius:8px;padding:12px 18px;margin:16px 0 8px">'
                    '<span style="font-size:18px">🥇</span> '
                    '<b style="color:#d4a017;font-size:14px">Golden Zone</b> '
                    '<span style="color:#8b7d3c;font-size:11px">'
                    f'({len(_gz4_rows)} ticker{"s" if len(_gz4_rows)!=1 else ""}) — '
                    f'Price within R 38.2% – R 61.8% retracement</span>'
                    '</div>',
                    unsafe_allow_html=True,
                )
                _gz4_df = pd.DataFrame(_gz4_rows)
                _gz4_styled = _gz4_df.style.applymap(
                    lambda v: "background-color:#1a1500;color:#d4a017;font-weight:700"
                    if str(v).startswith("$") else "",
                    subset=["R 38.2%", "R 50.0%", "R 61.8%"]
                )
                st.dataframe(_gz4_styled, use_container_width=True, hide_index=True,
                             height=min(len(_gz4_rows) * 35 + 48, 300))

            # ── Inter-Quarter Swing Fib — Current Price vs Earnings Swing ──
            st.markdown("---")
            # Read the user-chosen IQ date (defaults to today)
            _iq_asof = st.session_state.get("iq_fib_date", date.today())
            _iq_is_today = (_iq_asof == date.today())
            _iq_date_label = "Live" if _iq_is_today else str(_iq_asof)
            st.markdown(
                f'<h4>📐 Inter-Quarter Swing Fib — Where Is Price <i>{"Now" if _iq_is_today else "As Of " + str(_iq_asof)}</i>?</h4>'
                f'<p style="color:#6b7099;font-size:11px;margin-top:-8px">'
                f'Swing high &amp; low between last two quarterly earnings → Fib levels → '
                f'price zone as of <b style="color:#4d9fff">{_iq_date_label}</b>. 🔵 = nearest fib to price.</p>',
                unsafe_allow_html=True,
            )
            if (
                "Earn Hi" in _dl_df.columns
                and "Earn Lo" in _dl_df.columns
                and "Earn Window" in _dl_df.columns
            ):
                # Fetch prices as of iq_fib_date
                _iq_tickers = list(_dl_df["Ticker"].unique())
                _iq_prices = {}
                try:
                    if _iq_is_today:
                        # Use already-fetched live prices
                        _iq_prices = dict(_live_prices) if _live_prices else {}
                    else:
                        # Fetch close as of the chosen date
                        _iq_end = _iq_asof + timedelta(days=1)
                        _iq_start = _iq_asof - timedelta(days=5)  # buffer for weekends
                        _iq_hist = yf.download(
                            _iq_tickers, start=str(_iq_start), end=str(_iq_end),
                            progress=False, auto_adjust=True,
                        )
                        if _iq_hist is not None and not _iq_hist.empty:
                            if "Close" in _iq_hist.columns:
                                _iq_last = _iq_hist["Close"].iloc[-1]
                                for _ltk in _iq_tickers:
                                    try:
                                        _iq_prices[_ltk] = float(_iq_last[_ltk]) if _ltk in _iq_last.index else None
                                    except Exception:
                                        _iq_prices[_ltk] = None
                            else:
                                # Single ticker fallback
                                try:
                                    _iq_prices[_iq_tickers[0]] = float(_iq_hist["Close"].iloc[-1])
                                except Exception:
                                    pass
                except Exception:
                    _iq_prices = {}

                # Build inter-quarter rows
                _iq_rows = []
                for _, _iq_src in _dl_df.iterrows():
                    _iq_tk   = str(_iq_src.get("Ticker", ""))
                    _iq_hi   = _iq_src.get("Earn Hi")
                    _iq_lo   = _iq_src.get("Earn Lo")
                    _iq_win  = str(_iq_src.get("Earn Window", ""))
                    try:
                        _iq_hi = float(_iq_hi)
                        _iq_lo = float(_iq_lo)
                    except Exception:
                        continue
                    if _iq_hi <= _iq_lo:
                        continue
                    _iq_rng   = _iq_hi - _iq_lo
                    _iq_px    = _iq_prices.get(_iq_tk)
                    if _iq_px is None:
                        continue
                    _iq_pos   = (_iq_px - _iq_lo) / _iq_rng * 100
                    if _iq_pos >= 70:
                        _iq_zone = "HIGH"
                        _iq_zn_desc = "Near Swing Hi — extension territory"
                    elif _iq_pos <= 30:
                        _iq_zone = "LOW"
                        _iq_zn_desc = "Near Swing Lo — retracement territory"
                    else:
                        _iq_zone = "MID"
                        _iq_zn_desc = "Mid-range — balanced"
                    # Parse earnings dates from window
                    _iq_parts = _iq_win.split("→")
                    _iq_earn1 = _iq_parts[0].strip() if len(_iq_parts) >= 1 else "?"
                    _iq_earn2 = _iq_parts[1].strip() if len(_iq_parts) >= 2 else "?"
                    _iq_fib   = calc_fib_levels(_iq_lo, _iq_hi)
                    _iq_row = {
                        "Ticker":        _iq_tk,
                        "As Of":         _iq_date_label,
                        "Prev Earnings": _iq_earn1,
                        "Last Earnings": _iq_earn2,
                        "Swing Lo":      f"${_iq_lo:.2f}",
                        "Swing Hi":      f"${_iq_hi:.2f}",
                        "Price":         f"${_iq_px:.2f}",
                        "Swing Pos %":   f"{_iq_pos:.1f}%",
                        "Zone":          _iq_zone,
                    }
                    _iq_fib_col_order = [
                        "E 261.8%", "E 200.0%", "E 161.8%", "E 141.4%", "E 127.2%",
                        "R 0.0%", "R 23.6%", "R 38.2%", "R 50.0%", "R 61.8%", "R 78.6%", "R 100.0%",
                        "N -23.6%", "N -38.2%", "N -50.0%", "N -61.8%", "N -100.0%",
                    ]
                    for _fn in _iq_fib_col_order:
                        _fv = _iq_fib.get(_fn, float("nan"))
                        _iq_row[_fn] = f"${_fv:.2f}" if not (isinstance(_fv, float) and _fv != _fv) else ""
                    # Fib Compression: Y if 3+ fib levels cluster within 3% of swing range
                    _iq_fc_vals = sorted(_iq_fib.values())
                    _iq_fc_thresh = _iq_rng * 0.03 if _iq_rng > 0 else 0
                    _iq_fc_found = False
                    if len(_iq_fc_vals) >= 3 and _iq_fc_thresh > 0:
                        for _iq_fi in range(len(_iq_fc_vals) - 2):
                            if _iq_fc_vals[_iq_fi + 2] - _iq_fc_vals[_iq_fi] <= _iq_fc_thresh:
                                _iq_fc_found = True
                                break
                    _iq_row["Fib Compression"] = "Y" if _iq_fc_found else "N"

                    # Options strategy for IQ table — try Alpaca chain first
                    _iq_opt_dir = "LONG" if _iq_zone == "LOW" else ("SHORT" if _iq_zone == "HIGH" else None)
                    _iq_alpaca_opt = None
                    _iq_alpaca_text = "N/A"
                    try:
                        _iq_alpaca_opt = get_options_strategy_alpaca(_iq_tk, _iq_px, _iq_opt_dir, _iq_zone, api_key, api_secret)
                    except Exception:
                        pass

                    if _iq_alpaca_opt and _iq_alpaca_opt.get("summary"):
                        _iq_alpaca_text = _iq_alpaca_opt["summary"]
                        if _iq_alpaca_opt.get("alt"):
                            _iq_alpaca_text += f" | {_iq_alpaca_opt['alt']}"

                    # Fib-computed strategy (always calculated)
                    try:
                        _iq_opt_exp = datetime.strptime(next_earn, "%Y-%m-%d").date() if next_earn else None
                    except Exception:
                        _iq_opt_exp = None
                    if _iq_opt_exp is None or _iq_opt_exp <= date.today():
                        _iq_opt_exp = date.today() + timedelta(days=30)
                    _iq_opt_exp_s = _iq_opt_exp.strftime("%Y-%m-%d")
                    _iq_opt_fibs = sorted(_iq_fib.values())
                    _iq_opt_cidx = min(range(len(_iq_opt_fibs)),
                                       key=lambda i: abs(_iq_opt_fibs[i] - _iq_px))
                    _iq_opt_c = round(_iq_opt_fibs[_iq_opt_cidx])
                    _iq_opt_l = round(_iq_opt_fibs[max(0, _iq_opt_cidx - 1)])
                    _iq_opt_u = round(_iq_opt_fibs[min(len(_iq_opt_fibs) - 1, _iq_opt_cidx + 1)])
                    if _iq_zone == "LOW":
                        _iq_opt_s = (f"📈 {_iq_tk} Bull Call Spread — Buy ${_iq_opt_l} Call / Sell ${_iq_opt_c} Call · Exp {_iq_opt_exp_s}"
                                     f" | Alt: Buy ${_iq_opt_l} Call")
                    elif _iq_zone == "HIGH":
                        _iq_opt_s = (f"📉 {_iq_tk} Bear Put Spread — Buy ${_iq_opt_u} Put / Sell ${_iq_opt_c} Put · Exp {_iq_opt_exp_s}"
                                     f" | Alt: Buy ${_iq_opt_u} Put")
                    else:
                        _iq_opt_s = (f"🦋 {_iq_tk} Iron Butterfly — Sell ${_iq_opt_c} Call+Put / Buy ${_iq_opt_u} Call + Buy ${_iq_opt_l} Put"
                                     f" · Exp {_iq_opt_exp_s} | Alt: Buy Straddle ${_iq_opt_c} Call+Put")
                    _iq_row["Options Strategy"] = _iq_opt_s
                    _iq_row["Alpaca Options"] = _iq_alpaca_text

                    _iq_row["Conclusion"] = (
                        f"{_iq_zone} zone ({_iq_pos:.0f}%) | "
                        f"Swing: ${_iq_lo:.2f}–${_iq_hi:.2f} | "
                        f"Price ${_iq_px:.2f} as of {_iq_date_label} → {_iq_zn_desc}"
                        f" | {_iq_opt_s}"
                    )
                    _iq_rows.append(_iq_row)

                if _iq_rows:
                    _iq_df = pd.DataFrame(_iq_rows)
                    # Coloring helpers
                    def _iq_color_zone(v):
                        v = str(v)
                        if v == "HIGH": return "background-color:#3d0a1a;color:#ff4d6a;font-weight:700"
                        if v == "LOW":  return "background-color:#0a3d1f;color:#00e5a0;font-weight:700"
                        return "background-color:#3d3a0a;color:#f5c842;font-weight:700"

                    _iq_e_set = set(c for c in _iq_fib_col_order if c.startswith("E "))
                    _iq_r_set = set(c for c in _iq_fib_col_order if c.startswith("R "))
                    _iq_golden_set = {"R 38.2%", "R 50.0%", "R 61.8%"}

                    def _iq_highlight_fib(row):
                        zone = str(row.get("Zone", ""))
                        out = []
                        for col in row.index:
                            if zone == "HIGH" and col in _iq_r_set:
                                out.append("background-color:#1f1400;color:#f5c842;font-weight:700")
                            elif zone == "LOW" and col in _iq_e_set:
                                out.append("background-color:#001a0a;color:#00e5a0;font-weight:700")
                            elif col in _iq_golden_set:
                                out.append("background-color:#1a1500;color:#d4a017;font-weight:600;border-bottom:2px solid #d4a01780")
                            else:
                                out.append("")
                        return out

                    def _iq_highlight_price(row):
                        ticker  = str(row.get("Ticker", ""))
                        px      = _iq_prices.get(ticker)
                        styles  = [""] * len(row)
                        if px is None:
                            return styles
                        best_col  = None
                        best_diff = float("inf")
                        for col in _iq_fib_col_order:
                            if col not in row.index:
                                continue
                            try:
                                val = float(str(row[col]).replace("$", "").strip())
                                diff = abs(val - px)
                                if diff < best_diff:
                                    best_diff = diff
                                    best_col  = col
                            except Exception:
                                pass
                        if best_col is not None:
                            col_idx = list(row.index).index(best_col)
                            styles[col_idx] = "background-color:#0a1f3a;color:#ffffff;font-weight:900;border:2px solid #4d9fff"
                        return styles

                    # Split by zone
                    _iq_zone_config = [
                        ("HIGH", "#ff4d6a", "🔴"),
                        ("MID",  "#f5c842", "🟡"),
                        ("LOW",  "#00e5a0", "🟢"),
                    ]
                    for _iq_z, _iq_zc, _iq_zi in _iq_zone_config:
                        _iq_grp = _iq_df[_iq_df["Zone"] == _iq_z]
                        if _iq_grp.empty:
                            continue
                        st.markdown(
                            f'<div style="background:rgba(0,0,0,0.3);border-left:3px solid {_iq_zc}40;'
                            f'padding:6px 12px;margin:8px 0 4px;border-radius:0 4px 4px 0">'
                            f'<span style="font-size:14px">{_iq_zi}</span> '
                            f'<b style="color:{_iq_zc};font-size:13px">{_iq_z} Zone (as of {_iq_date_label})</b> '
                            f'<span style="color:#6b7099;font-size:11px">'
                            f'({len(_iq_grp)} ticker{"s" if len(_iq_grp) != 1 else ""})</span>'
                            f'</div>',
                            unsafe_allow_html=True,
                        )
                        _iq_styled = _iq_grp.style
                        if "Zone" in _iq_grp.columns:
                            _iq_styled = _iq_styled.applymap(_iq_color_zone, subset=["Zone"])
                        if "Fib Compression" in _iq_grp.columns:
                            _iq_styled = _iq_styled.applymap(_color_fib_comp, subset=["Fib Compression"])
                        _iq_styled = _iq_styled.apply(_iq_highlight_fib, axis=1)
                        if _iq_prices:
                            _iq_styled = _iq_styled.apply(_iq_highlight_price, axis=1)
                        st.dataframe(
                            _iq_styled, use_container_width=True, hide_index=True,
                            height=min(len(_iq_grp) * 35 + 48, 400),
                        )
                    # CSV download for inter-quarter table
                    import io as _iq_io
                    _iq_csv = _iq_io.StringIO()
                    _iq_df.to_csv(_iq_csv, index=False)

                    # ── 🥇 Golden Zone ──
                    _gz5_rows = []
                    for _, _gz5_r in _iq_df.iterrows():
                        try:
                            _gz5_tk = _gz5_r["Ticker"]
                            _gz5_38 = float(str(_gz5_r.get("R 38.2%", "")).replace("$", ""))
                            _gz5_61 = float(str(_gz5_r.get("R 61.8%", "")).replace("$", ""))
                            _gz5_px = _iq_prices.get(_gz5_tk) if _iq_prices else None
                            if _gz5_px is None:
                                _gz5_px = float(str(_gz5_r.get("Price", "")).replace("$", ""))
                            _gz5_lo, _gz5_hi = min(_gz5_38, _gz5_61), max(_gz5_38, _gz5_61)
                            if _gz5_lo <= _gz5_px <= _gz5_hi:
                                _gz5_rows.append({"Ticker": _gz5_tk, "Price": f"${_gz5_px:.2f}",
                                                  "R 38.2%": _gz5_r.get("R 38.2%", ""),
                                                  "R 50.0%": _gz5_r.get("R 50.0%", ""),
                                                  "R 61.8%": _gz5_r.get("R 61.8%", ""),
                                                  "Zone": _gz5_r.get("Zone", "")})
                        except Exception:
                            pass
                    if _gz5_rows:
                        st.markdown(
                            '<div style="background:linear-gradient(135deg,#1a1500,#1f1800);border:1px solid #d4a01740;'
                            'border-radius:8px;padding:12px 18px;margin:16px 0 8px">'
                            '<span style="font-size:18px">🥇</span> '
                            '<b style="color:#d4a017;font-size:14px">Golden Zone</b> '
                            '<span style="color:#8b7d3c;font-size:11px">'
                            f'({len(_gz5_rows)} ticker{"s" if len(_gz5_rows)!=1 else ""}) — '
                            f'Price within R 38.2% – R 61.8% retracement</span>'
                            '</div>',
                            unsafe_allow_html=True,
                        )
                        _gz5_df = pd.DataFrame(_gz5_rows)
                        _gz5_styled = _gz5_df.style.applymap(
                            lambda v: "background-color:#1a1500;color:#d4a017;font-weight:700"
                            if str(v).startswith("$") else "",
                            subset=["R 38.2%", "R 50.0%", "R 61.8%"]
                        )
                        st.dataframe(_gz5_styled, use_container_width=True, hide_index=True,
                                     height=min(len(_gz5_rows) * 35 + 48, 300))

                    st.download_button(
                        label=f"📥 Download Inter-Quarter Swing Fib ({len(_iq_rows)} tickers)",
                        data=_iq_csv.getvalue(),
                        file_name=f"inter_quarter_swing_fib_{_iq_asof.strftime('%Y%m%d')}.csv",
                        mime="text/csv",
                        key="btn_iq_fib_dl",
                    )
                else:
                    st.info("Could not build inter-quarter swing table — no valid data or prices.")
            else:
                st.info("Earn Hi / Earn Lo columns not found in report data.")

            st.markdown("---")
            st.download_button(
                label=_dl_label,
                data=_csv_buf.getvalue(),
                file_name=f"fib_scenario_report_{date.today().strftime('%Y%m%d')}.csv",
                mime="text/csv",
                key="btn_fib_dl_report",
            )
            st.markdown("---")

except Exception as _fetch_err:
  import traceback; print(f"⚠️ Fetch handler crashed: {_fetch_err}\n{traceback.format_exc()}")

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

# ╔══════════════════════════════════════════════════════════════════════════════╗
# ║ TAB 10: NEWS — Headline Sentiment per Ticker                                ║
# ╚══════════════════════════════════════════════════════════════════════════════╝
with tab_news:
    st.markdown(
        '<div style="background:linear-gradient(135deg,#0a0b14,#131625);border:1px solid #1a1d2e;'
        'border-radius:8px;padding:18px 24px;margin-bottom:16px">'
        '<div style="display:flex;align-items:center;gap:12px">'
        '<div style="font-size:28px">📰</div>'
        '<div>'
        '<div style="font-size:16px;font-weight:700;color:#e8ecff">News Sentiment</div>'
        '<div style="font-size:10px;color:#6b7099">Recent Finviz headlines scored by keyword sentiment. '
        'Enter tickers below or pull from Watchlist / Trade Tracker.</div>'
        '</div></div></div>',
        unsafe_allow_html=True,
    )

    # ── Ticker source: manual entry or auto-fill from watchlist / tracker ──
    _news_col1, _news_col2 = st.columns([3, 1])
    with _news_col1:
        _news_ticker_input = st.text_input(
            "Tickers (comma-separated)",
            placeholder="AAPL, TSLA, NVDA ...",
            key="news_ticker_input",
        )
    with _news_col2:
        _news_source = st.radio(
            "Auto-fill from",
            ["Manual", "Watchlist", "Tracker"],
            horizontal=True,
            key="news_source",
        )

    _news_tickers = []
    if _news_source == "Manual" and _news_ticker_input:
        _news_tickers = [t.strip().upper() for t in _news_ticker_input.split(",") if t.strip()]
    elif _news_source == "Watchlist":
        try:
            _wl = fetch_today_watchlist(force=False)
            _news_tickers = list({t.upper() for t in _wl if t.strip()})
        except Exception:
            _news_tickers = []
    elif _news_source == "Tracker":
        try:
            _all_tr = get_all_trades()
            _news_tickers = list({t['ticker'].upper() for t in _all_tr if t.get('status') == 'OPEN' and t.get('ticker')})
        except Exception:
            _news_tickers = []

    if _news_tickers:
        _news_tickers.sort()
        st.markdown(f"**Scanning {len(_news_tickers)} ticker(s):** {', '.join(_news_tickers)}")

        if st.button("🔄 Refresh News", key="btn_news_refresh", use_container_width=True):
            from news_sentiment import _NEWS_CACHE as _nc
            for _t in _news_tickers:
                _nc.pop(_t, None)

        for _nt in _news_tickers:
            _details = get_news_details(_nt)
            _lbl = _details["label"]
            _g = _details["good_score"]
            _b = _details["bad_score"]
            if _lbl == "Good":
                _icon = "🟢"
                _border_col = "#00e5a0"
                _bg = "#0a3d1f"
            elif _lbl == "Bad":
                _icon = "🔴"
                _border_col = "#ff4d6a"
                _bg = "#3d0a1a"
            else:
                _icon = "⚪"
                _border_col = "#3a3d5c"
                _bg = "#131625"

            st.markdown(
                f'<div style="background:{_bg};border:1px solid {_border_col};border-radius:6px;'
                f'padding:10px 16px;margin-top:12px;margin-bottom:4px">'
                f'<span style="font-size:15px;font-weight:700;color:#e8ecff">{_icon} {_nt}</span>'
                f'<span style="float:right;font-size:12px;color:#6b7099">'
                f'Good: <span style="color:#00e5a0;font-weight:700">{_g}</span> · '
                f'Bad: <span style="color:#ff4d6a;font-weight:700">{_b}</span></span></div>',
                unsafe_allow_html=True,
            )

            _hdls = _details.get("headlines", [])
            if _hdls:
                _rows_html = ""
                for _h in _hdls[:12]:
                    _s = _h.get("sentiment", "Neutral")
                    if _s == "Good":
                        _hc = "#00e5a0"
                    elif _s == "Bad":
                        _hc = "#ff4d6a"
                    else:
                        _hc = "#8890b5"
                    _src = _h.get("source", "")
                    _tm = _h.get("time", "")
                    _dt = _h.get("date", "")
                    _ts = f"{_dt} {_tm}".strip() if _dt else _tm
                    _rows_html += (
                        f'<tr>'
                        f'<td style="color:{_hc};padding:3px 8px;font-size:11px;border-bottom:1px solid #1a1d2e">'
                        f'{_h["headline"]}</td>'
                        f'<td style="color:#6b7099;padding:3px 8px;font-size:10px;border-bottom:1px solid #1a1d2e;white-space:nowrap">{_ts}</td>'
                        f'<td style="color:#6b7099;padding:3px 8px;font-size:10px;border-bottom:1px solid #1a1d2e">{_src}</td>'
                        f'</tr>'
                    )
                st.markdown(
                    f'<table style="width:100%;border-collapse:collapse;margin-bottom:8px">'
                    f'<thead><tr>'
                    f'<th style="text-align:left;color:#6b7099;font-size:10px;padding:4px 8px;border-bottom:1px solid #2a2d4e">Headline</th>'
                    f'<th style="text-align:left;color:#6b7099;font-size:10px;padding:4px 8px;border-bottom:1px solid #2a2d4e">Time</th>'
                    f'<th style="text-align:left;color:#6b7099;font-size:10px;padding:4px 8px;border-bottom:1px solid #2a2d4e">Source</th>'
                    f'</tr></thead><tbody>{_rows_html}</tbody></table>',
                    unsafe_allow_html=True,
                )
            else:
                st.caption(f"No headlines found for {_nt}")
    else:
        if _news_source == "Manual":
            st.info("Enter comma-separated tickers above to view news sentiment.")
        elif _news_source == "Watchlist":
            st.warning("No tickers found in today's watchlist.")
        else:
            st.warning("No open trades found in the Trade Tracker.")


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

    # ── Quick Actions: Refresh + Delete All ──
    _tt_qa1, _tt_qa2, _tt_qa3 = st.columns([1, 1, 6])
    with _tt_qa1:
        if st.button("🔄 Refresh", key="tt_refresh_btn"):
            st.rerun()
    with _tt_qa2:
        if st.button("🗑️ Delete All Trades", key="tt_delete_all_btn"):
            st.session_state["_tt_confirm_delete_all"] = True
    if st.session_state.get("_tt_confirm_delete_all"):
        st.warning("⚠️ Are you sure? This will permanently delete ALL trades (open + closed).")
        _cf1, _cf2, _cf3 = st.columns([1, 1, 6])
        with _cf1:
            if st.button("✅ Yes, delete all", key="tt_confirm_yes"):
                delete_all_trades()
                st.session_state["_tt_confirm_delete_all"] = False
                st.success("All trades deleted.")
                st.rerun()
        with _cf2:
            if st.button("❌ Cancel", key="tt_confirm_no"):
                st.session_state["_tt_confirm_delete_all"] = False
                st.rerun()

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

            # Action Dropdown and Close Trade controls
            current_action = trade.get("action", "OPEN")
            action_options = ["OPEN", "TAKE_PROFIT_T1", "TAKE_PROFIT_T2", "STOP_LOSS", "CLOSE", "DELETE"]
            
            action_col1, action_col2, action_col3, action_col4 = st.columns([1.5, 1, 1, 1])
            with action_col1:
                new_action = st.selectbox(f"Action", action_options, 
                                         index=action_options.index(current_action) if current_action in action_options else 0,
                                         key=f"action_{tid}")
                if new_action != current_action:
                    # Action changed - update and send notification
                    exit_px_update = None
                    if new_action == "CLOSE":
                        close_trade(tid, entry_px)  # Close at entry if not specified
                        exit_px_update = entry_px
                    elif new_action in ["TAKE_PROFIT_T1", "TAKE_PROFIT_T2"]:
                        exit_target = t1_px if new_action == "TAKE_PROFIT_T1" else t2_px
                        if exit_target:
                            close_trade(tid, exit_target)
                            exit_px_update = exit_target
                    elif new_action == "STOP_LOSS":
                        if stop_px:
                            close_trade(tid, stop_px)
                            exit_px_update = stop_px
                    elif new_action == "DELETE":
                        delete_trade(tid)
                        st.warning(f"Trade #{tid} {tkr} deleted")
                        st.rerun()
                    
                    # Update action in database
                    update_trade(tid, action=new_action)
                    
                    # Fetch fresh trade data before sending notification
                    fresh_trade = next((t for t in get_all_trades() if t["id"] == tid), None)
                    if fresh_trade:
                        _fire_and_forget_telegram(send_trade_update_notification, fresh_trade, new_action, exit_px_update)
                        st.success(f"📱 Action updated to {new_action.replace('_', ' ')} - Telegram queued ✅", icon="✅")
                    
                    st.rerun()
            
            with action_col2:
                exit_px = st.number_input(f"Exit price", min_value=0.01, value=float(entry_px),
                                          step=0.01, key=f"exit_px_{tid}")
            with action_col3:
                if st.button(f"✅ Close Trade", key=f"close_{tid}"):
                    close_trade(tid, exit_px)
                    update_trade(tid, action="CLOSE")
                    _fire_and_forget_telegram(send_trade_update_notification, trade, "CLOSE", exit_px)
                    st.success(f"Trade #{tid} {tkr} closed @ ${exit_px:.2f} | 📱 Telegram queued")
                    st.rerun()
            with action_col4:
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
        m_col1, m_col2, m_col3, m_col4 = st.columns(4)
        with m_col1:
            m_ticker = st.text_input("Ticker", key="manual_ticker", placeholder="AAPL")
            m_direction = st.selectbox("Direction", ["LONG", "SHORT"], key="manual_dir")
        with m_col2:
            m_entry = st.number_input("Entry Price", min_value=0.01, value=100.0, step=0.01, key="manual_entry")
            m_stop = st.number_input("Stop Loss", min_value=0.0, value=0.0, step=0.01, key="manual_stop")
        with m_col3:
            m_t1 = st.number_input("Target 1", min_value=0.0, value=0.0, step=0.01, key="manual_t1")
            m_t2 = st.number_input("Target 2", min_value=0.0, value=0.0, step=0.01, key="manual_t2")
        with m_col4:
            m_open = st.number_input("Open Price (optional)", min_value=0.0, value=0.0, step=0.01, key="manual_open")
        m_notes = st.text_input("Notes", key="manual_notes", placeholder="Optional notes…")
        if st.button("💾 Save Manual Trade", key="save_manual_trade"):
            if m_ticker.strip():
                saved, telegram_sent, trade_id = save_trade(
                    ticker=m_ticker.strip().upper(),
                    direction=m_direction,
                    entry_price=m_entry,
                    stop_loss=m_stop if m_stop > 0 else None,
                    target1=m_t1 if m_t1 > 0 else None,
                    target2=m_t2 if m_t2 > 0 else None,
                    open_price=m_open if m_open > 0 else None,
                    scenario=None,
                    notes=m_notes if m_notes else None,
                    force=True,
                )
                if saved:
                    msg = f"✅ Saved {m_direction} {m_ticker.strip().upper()} @ ${m_entry:.2f}"
                    if telegram_sent:
                        msg += " | 📱 Telegram notification sent"
                    else:
                        msg += " | ⚠️ Trade saved but Telegram notification failed"
                    st.success(msg)
                    st.rerun()
                else:
                    st.error("Failed to save trade")
            else:
                st.warning("Please enter a ticker symbol.")

# ╔══════════════════════════════════════════════════════════════════════════════╗
# ║ TAB 6: MY HOLDINGS — Fib Zone Report + Covered Call Strategy                  ║
# ╚══════════════════════════════════════════════════════════════════════════════╝
with tab_holdings:
    st.markdown(
        '<div style="background:linear-gradient(135deg,#0a0b14,#131625);border:1px solid #1a1d2e;'
        'border-radius:8px;padding:18px 24px;margin-bottom:16px">'
        '<div style="font-size:18px;font-weight:bold;color:#e8ecff;margin-bottom:4px">💼 My Holdings</div>'
        '<div style="font-size:10px;color:#6b7099">Enter tickers, run the report → Weekly zones, '
        'Fib levels, covered-call strategy for each position.</div>'
        '</div>',
        unsafe_allow_html=True
    )

    # ── Persistent Holdings Storage ──────────────────────────────────────────
    _HOLDINGS_CSV = os.path.join(os.path.dirname(os.path.abspath(__file__)), "my_holdings.csv")

    def _load_holdings():
        """Load holdings from CSV. Returns list of dicts with ticker, shares, avg_cost."""
        if os.path.exists(_HOLDINGS_CSV):
            try:
                df = pd.read_csv(_HOLDINGS_CSV)
                if df.empty:
                    return []
                return df.to_dict("records")
            except Exception:
                pass
        return []

    def _save_holdings(records):
        """Save holdings list of dicts to CSV."""
        df = pd.DataFrame(records)
        df.to_csv(_HOLDINGS_CSV, index=False)

    if "_mh_holdings_data" not in st.session_state:
        st.session_state["_mh_holdings_data"] = _load_holdings()

    # ── Holdings Management ──
    with st.expander("📝 Manage My Holdings (persistent)", expanded=not bool(st.session_state["_mh_holdings_data"])):
        st.caption("Add/edit your holdings below. Changes are saved to disk and persist across sessions.")

        _mh_add_col1, _mh_add_col2, _mh_add_col3, _mh_add_col4 = st.columns([2, 1, 1, 1])
        with _mh_add_col1:
            _mh_new_ticker = st.text_input("Ticker", key="_mh_new_ticker", placeholder="AAPL")
        with _mh_add_col2:
            _mh_new_shares = st.number_input("Shares", min_value=0.0, value=0.0, step=1.0, key="_mh_new_shares")
        with _mh_add_col3:
            _mh_new_cost = st.number_input("Avg Cost ($)", min_value=0.0, value=0.0, step=0.01, key="_mh_new_cost", format="%.2f")
        with _mh_add_col4:
            st.write("")  # spacer
            st.write("")
            _mh_add_btn = st.button("➕ Add", key="btn_mh_add", use_container_width=True)

        if _mh_add_btn and _mh_new_ticker.strip():
            _new_tk = _mh_new_ticker.strip().upper()
            # Update existing or add new
            _found = False
            for h in st.session_state["_mh_holdings_data"]:
                if h.get("ticker", "").upper() == _new_tk:
                    h["shares"] = _mh_new_shares
                    h["avg_cost"] = _mh_new_cost
                    _found = True
                    break
            if not _found:
                st.session_state["_mh_holdings_data"].append({
                    "ticker": _new_tk,
                    "shares": _mh_new_shares,
                    "avg_cost": _mh_new_cost,
                })
            _save_holdings(st.session_state["_mh_holdings_data"])
            st.rerun()

        # Show current holdings as editable table
        if st.session_state["_mh_holdings_data"]:
            _mh_h_df = pd.DataFrame(st.session_state["_mh_holdings_data"])
            for _col_default in ["ticker", "shares", "avg_cost"]:
                if _col_default not in _mh_h_df.columns:
                    _mh_h_df[_col_default] = "" if _col_default == "ticker" else 0.0
            _mh_h_df["ticker"] = _mh_h_df["ticker"].astype(str).str.upper()
            _mh_h_df["🗑️"] = False

            _mh_edited = st.data_editor(
                _mh_h_df,
                use_container_width=True,
                hide_index=True,
                num_rows="dynamic",
                key="_mh_holdings_editor",
                column_config={
                    "ticker": st.column_config.TextColumn("Ticker", width="small"),
                    "shares": st.column_config.NumberColumn("Shares", min_value=0, format="%.2f"),
                    "avg_cost": st.column_config.NumberColumn("Avg Cost ($)", min_value=0, format="$%.2f"),
                    "🗑️": st.column_config.CheckboxColumn("Delete?", width="small"),
                },
            )

            _mh_save_col1, _mh_save_col2 = st.columns(2)
            with _mh_save_col1:
                if st.button("💾 Save Changes", key="btn_mh_save", use_container_width=True):
                    # Remove rows marked for deletion and rows with empty ticker
                    _kept = _mh_edited[~_mh_edited["🗑️"]].copy()
                    _kept = _kept[_kept["ticker"].astype(str).str.strip() != ""]
                    _kept["ticker"] = _kept["ticker"].astype(str).str.strip().str.upper()
                    _records = _kept[["ticker", "shares", "avg_cost"]].to_dict("records")
                    st.session_state["_mh_holdings_data"] = _records
                    _save_holdings(_records)
                    st.success(f"Saved {len(_records)} holdings.")
                    st.rerun()
            with _mh_save_col2:
                if st.button("🗑️ Clear All", key="btn_mh_clear_all", use_container_width=True):
                    st.session_state["_mh_holdings_data"] = []
                    _save_holdings([])
                    st.rerun()
        else:
            st.info("No holdings saved yet. Add tickers above.")

    # ── Build ticker string from persistent holdings ──
    _mh_saved_tickers = [h["ticker"] for h in st.session_state["_mh_holdings_data"] if h.get("ticker", "").strip()]
    _mh_saved_str = ", ".join(_mh_saved_tickers)

    # ── Inputs ──
    _mh_col1, _mh_col2 = st.columns([3, 1])
    with _mh_col1:
        _mh_tickers_raw = st.text_input(
            "Tickers (comma-separated) — auto-filled from saved holdings",
            value=_mh_saved_str if _mh_saved_str else st.session_state.get("_mh_tickers_val", ""),
            key="_mh_tickers_input",
            placeholder="e.g. AAPL, MSFT, NVDA, TSLA",
        )
    with _mh_col2:
        _mh_backdate = st.date_input("As-Of Date", value=date.today(), key="_mh_backdate")
    _mh_run = st.button("🔍 Run Holdings Report", use_container_width=True, type="primary", key="btn_mh_run")

    # ── Run Stock Analysis scan when button is clicked ──
    if _mh_run and _mh_tickers_raw.strip():
        _mh_scan_tickers = [t.strip().upper() for t in _mh_tickers_raw.split(",") if t.strip()]
        if _mh_scan_tickers:
            with st.spinner(f"🔬 Scanning {len(_mh_scan_tickers)} holdings (Stock Analysis)..."):
                _mh_scan_progress = st.progress(0)
                _mh_scan_status = st.empty()
                _mh_scan_backdate = _mh_backdate if _mh_backdate < date.today() else None
                _mh_scan_res, _mh_scan_errs, _mh_scan_nodata = _parallel_scan_with_progress(
                    _mh_scan_tickers, api_key, api_secret, data_source,
                    use_fib, fib_tol, use_strategy,
                    progress_bar=_mh_scan_progress, status_text=_mh_scan_status,
                    fetch_fundamentals=True, as_of_date=_mh_scan_backdate,
                )
                _mh_scan_progress.empty()
                _mh_scan_status.empty()
                st.session_state["_mh_scan_results"] = _mh_scan_res
                st.session_state["_mh_scan_errors"] = _mh_scan_errs
                st.session_state["_mh_scan_nodata"] = _mh_scan_nodata
                st.session_state["_mh_scan_total"] = len(_mh_scan_tickers)

    # ── STOCK ANALYSIS TABLE FOR HOLDINGS (rendered at TOP) ────────────────────
    _mh_sr = st.session_state.get("_mh_scan_results")
    if _mh_sr:
        st.markdown(
            '<div style="background:linear-gradient(135deg,#0a0b14,#131625);border:1px solid #1a1d2e;'
            'border-radius:8px;padding:14px 20px;margin-bottom:12px">'
            '<div style="font-size:15px;font-weight:bold;color:#e8ecff;margin-bottom:2px">🔬 Stock Analysis — Holdings</div>'
            '<div style="font-size:10px;color:#6b7099">Full technical + fundamental scan for your holdings tickers. '
            'Same analysis as the 🔬 Stock Analysis tab.</div>'
            '</div>',
            unsafe_allow_html=True,
        )
        _mh_se = st.session_state.get("_mh_scan_errors", [])
        _mh_sn = st.session_state.get("_mh_scan_nodata", [])
        _mh_st = st.session_state.get("_mh_scan_total", len(_mh_sr))

        _mh_timeout = [t for t, _, is_to in _mh_se if is_to]
        _mh_other_err = [(t, e) for t, e, is_to in _mh_se if not is_to]
        _mh_all_fail = _mh_timeout + _mh_sn
        if _mh_all_fail:
            st.warning(f"**{len(_mh_all_fail)} ticker(s) returned no data.** Copy and re-scan:")
            st.code(", ".join(_mh_all_fail), language=None)
        if _mh_other_err:
            with st.expander(f"⚠️ {len(_mh_other_err)} error(s)"):
                for t, err in _mh_other_err[:10]:
                    st.code(f"{t}: {err}", language=None)

        _mh_sdf = pd.DataFrame(_mh_sr)
        if not _mh_sdf.empty:
            _mh_sdf["chart"] = _mh_sdf["ticker"].apply(lambda t: f"https://finviz.com/quote.ashx?t={t}&p=d")
            try:
                from news_sentiment import get_news_sentiment_batch
                _mh_tickers_list = _mh_sdf["ticker"].tolist()
                _mh_news_map = get_news_sentiment_batch(_mh_tickers_list, max_workers=4)
                _mh_sdf["news"] = _mh_sdf["ticker"].map(_mh_news_map).fillna("No")
            except Exception:
                _mh_sdf["news"] = "No"

            # ── Overall Fundamental label: Strong / Weak / Neutral ──
            def _calc_fundamental_label(row):
                score = 0
                # Growth
                rg = row.get("revenue_growth")
                if pd.notnull(rg) and rg != "":
                    if rg > 0.20: score += 2
                    elif rg > 0.05: score += 1
                    elif rg < -0.05: score -= 2
                eg = row.get("earnings_growth")
                if pd.notnull(eg) and eg != "":
                    if eg > 0.25: score += 2
                    elif eg > 0.05: score += 1
                    elif eg < -0.10: score -= 2
                # Profitability
                pm = row.get("profit_margin")
                if pd.notnull(pm) and pm != "":
                    if pm > 0.20: score += 1
                    elif pm < 0: score -= 2
                roe_v = row.get("roe")
                if pd.notnull(roe_v) and roe_v != "":
                    if roe_v > 0.15: score += 1
                    elif roe_v < 0: score -= 1
                # Valuation
                pe = row.get("pe_ratio")
                if pd.notnull(pe) and pe != "" and isinstance(pe, (int, float)):
                    if 0 < pe < 15: score += 1
                    elif pe > 40: score -= 1
                peg = row.get("peg_ratio")
                if pd.notnull(peg) and peg != "" and isinstance(peg, (int, float)):
                    if 0 < peg < 1: score += 1
                    elif peg > 3: score -= 1
                # Balance sheet
                de = row.get("debt_to_equity")
                if pd.notnull(de) and de != "" and isinstance(de, (int, float)):
                    if de < 30: score += 1
                    elif de > 200: score -= 1
                # Analyst
                up = row.get("target_upside")
                if pd.notnull(up) and up != "" and isinstance(up, (int, float)):
                    if up > 20: score += 1
                    elif up < -15: score -= 1
                if score >= 3: return "Strong"
                elif score <= -2: return "Weak"
                return "Neutral"
            _mh_sdf["fundamental"] = _mh_sdf.apply(_calc_fundamental_label, axis=1)

            # ── Enrich with holdings cost basis & P/L ──
            _mh_cost_map = {h["ticker"].upper(): h for h in st.session_state.get("_mh_holdings_data", []) if h.get("ticker")}
            if _mh_cost_map:
                _mh_sdf["shares"] = _mh_sdf["ticker"].apply(lambda t: _mh_cost_map.get(t, {}).get("shares", 0))
                _mh_sdf["avg_cost"] = _mh_sdf["ticker"].apply(lambda t: _mh_cost_map.get(t, {}).get("avg_cost", 0))
                _mh_sdf["cost_basis"] = _mh_sdf["shares"] * _mh_sdf["avg_cost"]
                _mh_sdf["mkt_value"] = _mh_sdf["shares"] * pd.to_numeric(_mh_sdf["price"], errors="coerce")
                _mh_sdf["pnl_dollars"] = _mh_sdf["mkt_value"] - _mh_sdf["cost_basis"]
                _mh_sdf["pnl_pct"] = _mh_sdf.apply(
                    lambda r: ((r["mkt_value"] / r["cost_basis"] - 1) * 100) if r["cost_basis"] > 0 else 0, axis=1)

            _mh_sa_display_cols = [
                "chart", "ticker", "shares", "avg_cost", "price", "fundamental", "mtf_action",
                "entry", "stop_loss", "target1", "target2", "t1_days",
                "mkt_value", "pnl_dollars", "pnl_pct",
                "entry_status", "entry_grade", "entry_label",
                "expected_avg",
                "weekly_bias", "daily_bias", "4h_bias", "ma_bias", "mtf_signal",
                "cpr_tc", "cpr_p", "cpr_bc", "cpr_type", "cpr_position", "cpr_interpretation",
                "sector", "verdict", "confidence", "score", "best_setup",
                "candle", "vol_action", "vol_trend", "vol_ratio", "poc", "val", "vah",
                "persistence", "quote_type",
                "risk_pct",
                "rr_t1", "rr_t2", "best_rr",
                "pe_ratio", "forward_pe", "peg_ratio", "valuation", "market_cap",
                "revenue_str", "revenue_growth", "earnings_growth",
                "profit_margin", "roe", "debt_to_equity", "beta",
                "dividend_yield", "short_pct",
                "analyst_target", "target_1y", "target_upside",
                "rec_key", "num_analysts",
                "week52_position", "pct_from_high",
                "news", "expected_wr", "flags",
            ]
            _mh_sa_display_cols = [c for c in _mh_sa_display_cols if c in _mh_sdf.columns]

            _mh_sa_col_rename = {
                "chart": "Chart", "shares": "Shares", "avg_cost": "Avg Cost",
                "mkt_value": "Mkt Value", "pnl_dollars": "P&L $", "pnl_pct": "P&L %",
                "fundamental": "Funda",
                "entry_status": "Status", "entry_grade": "Grade",
                "entry_label": "Entry Signal", "expected_wr": "Exp WR%", "expected_avg": "Exp Avg P&L",
                "weekly_bias": "Weekly", "daily_bias": "Daily", "4h_bias": "4H",
                "ma_bias": "MA Bias", "mtf_signal": "Signal", "mtf_action": "Action",
                "candle": "Day Candle", "persistence": "Trend Pers%", "quote_type": "Type",
                "best_setup": "Best Setup",
                "vol_action": "Vol Bias", "vol_trend": "Vol Trend", "vol_ratio": "Vol Ratio",
                "poc": "POC", "val": "VAL", "vah": "VAH",
                "pe_ratio": "P/E", "forward_pe": "Fwd P/E", "peg_ratio": "PEG",
                "revenue_str": "Revenue", "revenue_growth": "Rev Growth",
                "earnings_growth": "EPS Growth", "profit_margin": "Margin",
                "roe": "ROE", "debt_to_equity": "D/E", "beta": "Beta",
                "dividend_yield": "Div Yield", "short_pct": "Short%",
                "analyst_target": "Analyst $", "target_1y": "1Y Target",
                "target_upside": "Upside", "rec_key": "Rating", "num_analysts": "# Analysts",
                "week52_position": "52W Pos", "pct_from_high": "vs 52W Hi", "market_cap": "Mkt Cap",
                "stop_loss": "Stop", "t1_days": "T1 (td)", "risk_pct": "Risk%",
                "rr_t1": "RR(T1)", "rr_t2": "RR(T2)", "best_rr": "Best RR",
                "news": "News", "flags": "Signals",
            }

            def _mh_sa_fmt(df):
                df = df.copy()
                # ── Abbreviate bias columns (just colors) ──
                _bias_map = {"BULLISH": "Bull", "BEARISH": "Bear", "NEUTRAL": "—", "POSITIVE": "Bull", "NEGATIVE": "Bear"}
                for _bc in ["weekly_bias", "daily_bias", "4h_bias", "ma_bias"]:
                    if _bc in df.columns:
                        df[_bc] = df[_bc].apply(lambda x: _bias_map.get(str(x).upper().strip(), "—") if pd.notnull(x) else "—")
                # ── Abbreviate status: ENTER→E, HOLD→H, SKIP→S ──
                if "entry_status" in df.columns:
                    _st_map = {"ENTER": "E", "HOLD": "H", "SKIP": "S", "WAIT": "W", "STRONG HOLD": "SH"}
                    df["entry_status"] = df["entry_status"].apply(lambda x: _st_map.get(str(x).upper().strip(), str(x)[:2]) if pd.notnull(x) else "")
                # ── Shorten entry signal ──
                if "entry_label" in df.columns:
                    df["entry_label"] = df["entry_label"].apply(lambda x: str(x)[:18] if pd.notnull(x) and x else "")
                # ── Fundamental: just colors ──
                if "fundamental" in df.columns:
                    _f_map = {"STRONG": "S", "WEAK": "W", "NEUTRAL": "N"}
                    df["fundamental"] = df["fundamental"].apply(lambda x: _f_map.get(str(x).upper().strip(), "N") if pd.notnull(x) else "N")
                # Holdings columns
                if "shares" in df.columns:
                    df["shares"] = df["shares"].apply(lambda x: f"{x:.2f}" if pd.notnull(x) and x else "")
                if "avg_cost" in df.columns:
                    df["avg_cost"] = df["avg_cost"].apply(lambda x: f"${x:.2f}" if pd.notnull(x) and x else "")
                if "mkt_value" in df.columns:
                    df["mkt_value"] = df["mkt_value"].apply(lambda x: f"${x:,.2f}" if pd.notnull(x) and x else "")
                if "pnl_dollars" in df.columns:
                    df["pnl_dollars"] = df["pnl_dollars"].apply(lambda x: f"${x:+,.2f}" if pd.notnull(x) else "")
                if "pnl_pct" in df.columns:
                    df["pnl_pct"] = df["pnl_pct"].apply(lambda x: f"{x:+.2f}%" if pd.notnull(x) else "")
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
                for _rr in ["rr_t1", "rr_t2", "best_rr"]:
                    if _rr in df.columns:
                        df[_rr] = df[_rr].apply(lambda x: f"{x:.2f}x" if pd.notnull(x) and isinstance(x, (int, float)) else x)
                for _pc in ["price", "entry", "stop_loss", "target1", "target2"]:
                    if _pc in df.columns:
                        df[_pc] = df[_pc].apply(lambda x: f"{x:.2f}" if pd.notnull(x) and isinstance(x, (int, float)) else x)
                if "persistence" in df.columns:
                    df["persistence"] = df["persistence"].apply(lambda x: f"{x:.0f}%" if pd.notnull(x) else "")
                return df

            def _mh_sa_style_bias(df):
                def _cb(val):
                    if not isinstance(val, str): return ""
                    v = val.upper()
                    if "BULL" in v or v == "POSITIVE":
                        return "background-color: #0a3d1f; color: #00e5a0; font-weight: 700"
                    elif "BEAR" in v or v == "NEGATIVE":
                        return "background-color: #3d0a1a; color: #ff4d6a; font-weight: 700"
                    return "background-color: #1a1d2e; color: #6b7099"
                def _cs(val):
                    if not isinstance(val, str): return ""
                    v = val.upper()
                    if "A+ LONG" in v or "STRONG LONG" in v or "LONG PULLBACK" in v:
                        return "background-color: #0a3d1f; color: #00e5a0; font-weight: 700"
                    elif "A+ SHORT" in v or "STRONG SHORT" in v or "SHORT PULLBACK" in v:
                        return "background-color: #3d0a1a; color: #ff4d6a; font-weight: 700"
                    elif "SHORT-TERM LONG" in v: return "color: #00e5a0; font-weight: 600"
                    elif "SHORT-TERM SHORT" in v: return "color: #ff4d6a; font-weight: 600"
                    elif "NO EDGE" in v or "NOISE" in v or "TOO EARLY" in v: return "color: #6b7099"
                    elif "WARNING" in v or "DEAD CAT" in v or "FAILING" in v: return "color: #f0c040; font-weight: 600"
                    return "color: #a78bfa"
                def _cn(val):
                    if not isinstance(val, str): return ""
                    v = val.upper()
                    if v == "GOOD": return "background-color: #0a3d1f; color: #00e5a0; font-weight: 700"
                    elif v == "BAD": return "background-color: #3d0a1a; color: #ff4d6a; font-weight: 700"
                    return "color: #6b7099"
                def _cpnl(val):
                    if not isinstance(val, str): return ""
                    if val.startswith("$-") or val.startswith("-"): return "color: #ff4d6a; font-weight: 700"
                    if val.startswith("$+") or val.startswith("+"): return "color: #00e5a0; font-weight: 700"
                    return ""
                def _cf(val):
                    if not isinstance(val, str): return ""
                    v = val.upper()
                    if v in ("S", "STRONG"): return "background-color: #0a3d1f; color: #00e5a0; font-weight: 700"
                    elif v in ("W", "WEAK"): return "background-color: #3d0a1a; color: #ff4d6a; font-weight: 700"
                    return "color: #6b7099"
                _bcols = [c for c in ["Weekly", "Daily", "4H", "MA Bias"] if c in df.columns]
                styler = df.style.applymap(_cb, subset=_bcols)
                if "Signal" in df.columns: styler = styler.applymap(_cs, subset=["Signal"])
                if "News" in df.columns: styler = styler.applymap(_cn, subset=["News"])
                _pnl_cols = [c for c in ["P&L $", "P&L %"] if c in df.columns]
                if _pnl_cols: styler = styler.applymap(_cpnl, subset=_pnl_cols)
                if "Funda" in df.columns: styler = styler.applymap(_cf, subset=["Funda"])
                return styler

            _mh_enter = [r for r in _mh_sr if r.get("entry_status") == "ENTER"]
            _mh_bull = sum(1 for r in _mh_enter if r.get("verdict") == "BULLISH")
            _mh_bear = sum(1 for r in _mh_enter if r.get("verdict") == "BEARISH")
            _mh_hc = sum(1 for r in _mh_enter if r.get("confidence") == "HIGH")
            st.markdown(
                f'<div style="background:#0d0f17;border:1px solid #1a1d2e;padding:12px;border-radius:6px;margin-bottom:12px">'
                f'<span style="color:#6b7099;font-size:11px">Scanned <b style="color:#e8ecff">{_mh_st}</b> holdings · '
                f'<b style="color:#00e5a0">{_mh_bull}</b> bullish · '
                f'<b style="color:#ff4d6a">{_mh_bear}</b> bearish · '
                f'<b style="color:#4d9fff">{_mh_hc}</b> high confidence · '
                f'<b style="color:#e8ecff">{len(_mh_enter)}</b> actionable / {len(_mh_sr)} total</span></div>',
                unsafe_allow_html=True,
            )

            _mh_actionable = _mh_sdf[_mh_sdf["entry_status"] == "ENTER"].copy() if "entry_status" in _mh_sdf.columns else pd.DataFrame()
            if "mtf_rank" not in _mh_sdf.columns:
                _mh_sdf["mtf_rank"] = 5
            if not _mh_actionable.empty and "mtf_rank" not in _mh_actionable.columns:
                _mh_actionable["mtf_rank"] = 5

            _mh_r1 = _mh_actionable[_mh_actionable["mtf_rank"] == 1] if not _mh_actionable.empty else pd.DataFrame()
            _mh_r2 = _mh_actionable[_mh_actionable["mtf_rank"] == 2] if not _mh_actionable.empty else pd.DataFrame()
            _mh_r3 = _mh_actionable[_mh_actionable["mtf_rank"] >= 3] if not _mh_actionable.empty else pd.DataFrame()

            # Exceptional: HIGH confidence + score>=4 + expected WR > 80% + A+ Long + Accumulating
            _mh_exc_ok = (not _mh_actionable.empty and
                          all(c in _mh_actionable.columns for c in ["confidence", "score", "expected_wr", "mtf_signal", "vol_trend"]))
            try:
                _mh_exc = _mh_actionable[
                    (_mh_actionable["confidence"] == "HIGH") &
                    (pd.to_numeric(_mh_actionable["score"], errors="coerce").abs() >= 4) &
                    (pd.to_numeric(_mh_actionable["expected_wr"], errors="coerce") > 80) &
                    (_mh_actionable["mtf_signal"] == "A+ Long") &
                    (_mh_actionable["vol_trend"] == "ACCUMULATING")
                ] if _mh_exc_ok else pd.DataFrame()
            except Exception:
                _mh_exc = pd.DataFrame()

            # Exceptional Bearish
            try:
                _mh_exc_bear = _mh_actionable[
                    (_mh_actionable["confidence"] == "HIGH") &
                    (pd.to_numeric(_mh_actionable["score"], errors="coerce") <= -4) &
                    (pd.to_numeric(_mh_actionable["expected_wr"], errors="coerce") > 80) &
                    (_mh_actionable["mtf_signal"] == "A+ Short") &
                    (_mh_actionable["vol_trend"] == "DISTRIBUTING")
                ] if _mh_exc_ok else pd.DataFrame()
            except Exception:
                _mh_exc_bear = pd.DataFrame()

            _mh_vt_exc, _mh_vt_exc_bear, _mh_vt1, _mh_vt2, _mh_vt3, _mh_vt4 = st.tabs([
                f"⭐ Exceptional ({len(_mh_exc)})",
                f"💀 Exceptional Bear ({len(_mh_exc_bear)})",
                f"🎯 Rank 1 ({len(_mh_r1)})",
                f"✅ Rank 2 ({len(_mh_r2)})",
                f"📋 Rank 3+ ({len(_mh_r3)})",
                f"📊 All ({len(_mh_sdf)})",
            ])

            _mh_chart_cfg = {"Chart": st.column_config.LinkColumn("Chart", display_text="📈 Chart")}

            def _mh_sa_render(df_sub, tab_c, label, color, empty_msg, tab_key):
                with tab_c:
                    if df_sub.empty:
                        st.info(empty_msg)
                        return
                    st.markdown(
                        f'<div style="border-left:4px solid {color};padding:4px 12px;margin-bottom:10px">'
                        f'<span style="color:{color};font-weight:700;font-size:14px">{label}</span></div>',
                        unsafe_allow_html=True,
                    )
                    _cols = [c for c in _mh_sa_display_cols if c in df_sub.columns]
                    _df = _mh_sa_fmt(df_sub)[_cols].rename(columns=_mh_sa_col_rename)
                    _vrc = _mh_sa_col_rename.get("vol_ratio", "Vol Ratio")
                    if _vrc in _df.columns:
                        _df = _df.sort_values(by=_vrc, ascending=False,
                            key=lambda s: pd.to_numeric(s.astype(str).str.replace('x','',regex=False), errors='coerce'))
                    st.dataframe(_mh_sa_style_bias(_df), use_container_width=True,
                                 height=min(40*len(_df)+38, 600), column_config=_mh_chart_cfg)
                    st.download_button(f"📥 Download {label} CSV", _df.to_csv(index=False),
                        file_name=f"holdings_scan_{tab_key}_{date.today()}.csv", mime="text/csv",
                        use_container_width=True, key=f"dl_mh_{tab_key}")

            _mh_sa_render(_mh_exc, _mh_vt_exc, "⭐ Exceptional", "#FFD700",
                          "No exceptional setups (HIGH conf + score≥4 + WR>80% + A+ Long + Accumulating).", "exc")
            _mh_sa_render(_mh_exc_bear, _mh_vt_exc_bear, "💀 Exceptional Bear", "#ff4d6a",
                          "No exceptional bearish setups (HIGH conf + score≤-4 + WR>80% + A+ Short + Distributing).", "exc_bear")
            _mh_sa_render(_mh_r1, _mh_vt1, "🎯 Rank 1 — Full Align", "#00e5a0",
                          "No Rank 1 holdings.", "r1")
            _mh_sa_render(_mh_r2, _mh_vt2, "✅ Rank 2 — Two Aligned", "#4d9fff",
                          "No Rank 2 holdings.", "r2")
            _mh_sa_render(_mh_r3, _mh_vt3, "📋 Rank 3+ — Rest", "#f0c040",
                          "No Rank 3+ holdings.", "r3")

            with _mh_vt4:
                _cols_all = [c for c in _mh_sa_display_cols if c in _mh_sdf.columns]
                _df_all = _mh_sa_fmt(_mh_sdf)[_cols_all].rename(columns=_mh_sa_col_rename)
                _vrc_all = _mh_sa_col_rename.get("vol_ratio", "Vol Ratio")
                if "Status" in _df_all.columns and _vrc_all in _df_all.columns:
                    _df_all = _df_all.sort_values(
                        by=["Status", _vrc_all], ascending=[True, False],
                        key=lambda col: col.map(lambda x: (0 if x=="ENTER" else 1) if col.name=="Status" else x)
                            if col.name == "Status" else pd.to_numeric(col.astype(str).str.replace('x','',regex=False), errors='coerce'))
                st.dataframe(_mh_sa_style_bias(_df_all), use_container_width=True,
                             height=min(40*len(_df_all)+38, 700), column_config=_mh_chart_cfg)
                st.download_button("📥 Download Full Holdings Scan CSV", _df_all.to_csv(index=False),
                    file_name=f"holdings_scan_all_{date.today()}.csv", mime="text/csv",
                    use_container_width=True, key="dl_mh_all")

        st.markdown("---")
    else:
        st.caption("Click **🔍 Run Holdings Report** above to scan your holdings tickers.")

    # ── COVERED CALL REPORT ────────────────────────────────────────────────────
    _mh_cc_rows = st.session_state.get("_mh_report_rows", [])
    if _mh_cc_rows:
        st.markdown("---")
        st.markdown(
            '<div style="background:linear-gradient(135deg,#0a0b14,#131625);border:1px solid #1a1d2e;'
            'border-radius:8px;padding:14px 20px;margin-bottom:12px">'
            '<div style="font-size:15px;font-weight:bold;color:#e8ecff;margin-bottom:2px">📞 Covered Call Report</div>'
            '<div style="font-size:10px;color:#6b7099">Covered call strategies and Alpaca options for your holdings.</div>'
            '</div>',
            unsafe_allow_html=True,
        )
        # Build signal lookup from scan results
        _cc_signal_map = {}
        _cc_scan = st.session_state.get("_mh_scan_results", [])
        for _ccs in _cc_scan:
            _cc_signal_map[_ccs.get("ticker", "")] = _ccs.get("mtf_signal", "N/A")
        _cc_table_rows = []
        for _ccr in _mh_cc_rows:
            _tk = _ccr.get("Ticker", "")
            _cc_table_rows.append({
                "Ticker": _tk,
                "Signal": _cc_signal_map.get(_tk, "N/A"),
                "Covered Call": _ccr.get("Covered Call", "N/A"),
                "Alpaca Options": _ccr.get("Alpaca Options", "N/A"),
            })
        _cc_df = pd.DataFrame(_cc_table_rows)

        # Color the Signal column
        def _cc_color_signal(val):
            if not isinstance(val, str): return ""
            v = val.upper()
            if "A+ LONG" in v or "STRONG LONG" in v: return "background-color: #0a3d1f; color: #00e5a0; font-weight: 700"
            elif "A+ SHORT" in v or "STRONG SHORT" in v: return "background-color: #3d0a1a; color: #ff4d6a; font-weight: 700"
            elif "LONG" in v: return "color: #00e5a0"
            elif "SHORT" in v: return "color: #ff4d6a"
            return "color: #6b7099"

        _cc_styled = _cc_df.style.applymap(_cc_color_signal, subset=["Signal"])
        st.dataframe(_cc_styled, use_container_width=True, hide_index=True,
                     height=min(40 * len(_cc_df) + 38, 500))
        st.download_button(
            "📥 Download Covered Call Report CSV", _cc_df.to_csv(index=False),
            file_name=f"covered_call_report_{date.today()}.csv", mime="text/csv",
            use_container_width=True, key="dl_mh_cc_report",
        )

    # ── FUNDAMENTALS TABLE (pivot matrix) ─────────────────────────────────────
    _mh_fund_scan = st.session_state.get("_mh_scan_results")
    if _mh_fund_scan:
        st.markdown("---")
        st.markdown(
            '<div style="background:linear-gradient(135deg,#0a0b14,#131625);border:1px solid #1a1d2e;'
            'border-radius:8px;padding:14px 20px;margin-bottom:12px">'
            '<div style="font-size:15px;font-weight:bold;color:#e8ecff;margin-bottom:2px">📊 Fundamentals Report</div>'
            '<div style="font-size:10px;color:#6b7099">Signal matrix — each column is a fundamental flag, ✅ if ticker has it.</div>'
            '</div>',
            unsafe_allow_html=True,
        )

        # Collect all unique signal tags across all tickers
        _all_tags = set()
        _ticker_tags = {}
        for _fr in _mh_fund_scan:
            _tk = _fr.get("ticker", "")
            _flags = _fr.get("flags", "")
            _tags = [t.strip() for t in str(_flags).split(" · ") if t.strip()] if _flags else []
            _ticker_tags[_tk] = set(_tags)
            _all_tags.update(_tags)

        if _all_tags:
            # Sort: positive first, negative last
            _pos = sorted([t for t in _all_tags if any(k in t for k in ["↑", "High", "Low D/E", "Dividend", "Strong", "Under"])])
            _neg = sorted([t for t in _all_tags if any(k in t for k in ["↓", "Negative", "Weak", "Over", "High D/E", "No Div"])])
            _oth = sorted([t for t in _all_tags if t not in _pos and t not in _neg])
            _sorted_tags = _pos + _oth + _neg

            _matrix_rows = []
            for _fr in _mh_fund_scan:
                _tk = _fr.get("ticker", "")
                _row = {"Ticker": _tk}
                for _tag in _sorted_tags:
                    _row[_tag] = "✅" if _tag in _ticker_tags.get(_tk, set()) else ""
                _matrix_rows.append(_row)

            _matrix_df = pd.DataFrame(_matrix_rows)

            # Style: green background for ✅ in positive cols, red for negative
            def _matrix_style(val):
                return "background-color: #0a3d1f; color: #00e5a0; font-weight: 700; text-align: center" if val == "✅" else "text-align: center; color: #2a2d3e"

            _matrix_styled = _matrix_df.style.applymap(_matrix_style, subset=_sorted_tags)
            st.dataframe(_matrix_styled, use_container_width=True, hide_index=True,
                         height=min(40 * len(_matrix_df) + 38, 600))

            st.download_button(
                "📥 Download Fundamentals Matrix CSV", _matrix_df.to_csv(index=False),
                file_name=f"holdings_fundamentals_{date.today()}.csv", mime="text/csv",
                use_container_width=True, key="dl_mh_fundamentals",
            )
        else:
            st.info("No fundamental signals found for these tickers.")

    # ── Report placeholder ──
    _mh_report_ph = st.empty()

    if _mh_run and _mh_tickers_raw.strip():
        _mh_tickers = [t.strip().upper() for t in _mh_tickers_raw.split(",") if t.strip()]
        if not _mh_tickers:
            st.warning("Enter at least one ticker.")
        else:
            _mh_is_today = (_mh_backdate == date.today())
            _mh_date_label = "Live" if _mh_is_today else str(_mh_backdate)
            _mh_progress = st.progress(0)
            _mh_rows = []

            # ── Fetch prices for all tickers ──
            _mh_prices = {}
            try:
                if _mh_is_today:
                    _mh_dl = yf.download(_mh_tickers, period="1d", progress=False, auto_adjust=True)
                    if hasattr(_mh_dl, "columns") and "Close" in _mh_dl.columns and len(_mh_dl) > 0:
                        _mh_last = _mh_dl["Close"].iloc[-1]
                        for _tk in _mh_tickers:
                            try:
                                _mh_prices[_tk] = float(_mh_last[_tk]) if _tk in _mh_last.index else float(_mh_last)
                            except Exception:
                                try: _mh_prices[_tk] = float(yf.Ticker(_tk).fast_info.last_price)
                                except Exception: pass
                    else:
                        for _tk in _mh_tickers:
                            try: _mh_prices[_tk] = float(yf.Ticker(_tk).fast_info.last_price)
                            except Exception: pass
                else:
                    _mh_end = _mh_backdate + timedelta(days=1)
                    _mh_start = _mh_backdate - timedelta(days=5)
                    _mh_dl = yf.download(_mh_tickers, start=str(_mh_start), end=str(_mh_end),
                                         progress=False, auto_adjust=True)
                    if _mh_dl is not None and not _mh_dl.empty and "Close" in _mh_dl.columns:
                        _mh_last = _mh_dl["Close"].iloc[-1]
                        for _tk in _mh_tickers:
                            try:
                                _mh_prices[_tk] = float(_mh_last[_tk]) if _tk in _mh_last.index else float(_mh_last)
                            except Exception: pass
            except Exception:
                pass

            for _mhi, _mh_tk in enumerate(_mh_tickers):
                _mh_progress.progress((_mhi + 1) / len(_mh_tickers))
                try:
                    # Fetch ~30 days of daily data for weekly zone computation
                    _mh_p_end = _mh_backdate
                    _mh_p_start = _mh_p_end - timedelta(days=45)
                    _mh_hist = yf.download(_mh_tk, start=str(_mh_p_start), end=str(_mh_p_end + timedelta(days=1)),
                                           progress=False, auto_adjust=True)
                    if _mh_hist is None or _mh_hist.empty or len(_mh_hist) < 5:
                        continue

                    # Flatten columns if multi-level
                    if hasattr(_mh_hist.columns, 'levels') and _mh_hist.columns.nlevels > 1:
                        _mh_hist.columns = [c[0] if isinstance(c, tuple) else c for c in _mh_hist.columns]

                    _mh_close = float(_mh_hist["Close"].iloc[-1])
                    _mh_px = _mh_prices.get(_mh_tk, _mh_close)

                    # ── Weekly zone: 10-day H/L ──
                    _mhh = _mh_hist["High"].squeeze() if hasattr(_mh_hist["High"], 'squeeze') else _mh_hist["High"]
                    _mhl = _mh_hist["Low"].squeeze() if hasattr(_mh_hist["Low"], 'squeeze') else _mh_hist["Low"]
                    _mh_wk_hi = float(_mhh.iloc[-10:].max())
                    _mh_wk_lo = float(_mhl.iloc[-10:].min())
                    _mh_wk_rng = _mh_wk_hi - _mh_wk_lo
                    _mh_wk_pos = (_mh_px - _mh_wk_lo) / _mh_wk_rng * 100 if _mh_wk_rng > 0 else 50.0
                    if _mh_wk_pos >= 70:
                        _mh_wk_zone = "HIGH"
                    elif _mh_wk_pos <= 30:
                        _mh_wk_zone = "LOW"
                    else:
                        _mh_wk_zone = "MID"

                    # ── Daily zone: last day H/L ──
                    _mh_day_hi = float(_mh_hist["High"].iloc[-1])
                    _mh_day_lo = float(_mh_hist["Low"].iloc[-1])
                    _mh_day_rng = _mh_day_hi - _mh_day_lo
                    _mh_day_pos = (_mh_px - _mh_day_lo) / _mh_day_rng * 100 if _mh_day_rng > 0 else 50.0
                    if _mh_day_pos >= 70:
                        _mh_day_zone = "HIGH"
                    elif _mh_day_pos <= 30:
                        _mh_day_zone = "LOW"
                    else:
                        _mh_day_zone = "MID"

                    # ── Fib levels from 10-day swing ──
                    _mh_fib = calc_fib_levels(_mh_wk_lo, _mh_wk_hi)
                    _mh_fib_sorted = sorted(_mh_fib.items(), key=lambda kv: kv[1])

                    # ── Nearest fib ──
                    _mh_near = min(_mh_fib.items(), key=lambda kv: abs(kv[1] - _mh_px))
                    _mh_near_name, _mh_near_val = _mh_near

                    # ── Fib Compression: 3+ levels within 3% of range ──
                    _mh_fc_vals = sorted(_mh_fib.values())
                    _mh_fc_thresh = _mh_wk_rng * 0.03 if _mh_wk_rng > 0 else 0
                    _mh_fc = False
                    if len(_mh_fc_vals) >= 3 and _mh_fc_thresh > 0:
                        for _mh_fi in range(len(_mh_fc_vals) - 2):
                            if _mh_fc_vals[_mh_fi + 2] - _mh_fc_vals[_mh_fi] <= _mh_fc_thresh:
                                _mh_fc = True
                                break

                    # ── Covered Call Strategy — try Alpaca chain first ──
                    _mh_alpaca_cc = None
                    _mh_alpaca_text = "N/A"
                    try:
                        _mh_alpaca_cc = get_options_strategy_alpaca(
                            _mh_tk, _mh_px, "SHORT" if _mh_wk_zone == "HIGH" else None,
                            _mh_wk_zone, api_key, api_secret)
                    except Exception:
                        pass

                    if _mh_alpaca_cc and _mh_alpaca_cc.get("summary"):
                        _mh_alpaca_text = _mh_alpaca_cc["summary"]
                        if _mh_alpaca_cc.get("alt"):
                            _mh_alpaca_text += f" | {_mh_alpaca_cc['alt']}"

                    # Fib-computed covered call (always calculated)
                    _mh_otm_fibs = [(n, v) for n, v in _mh_fib_sorted if v > _mh_px]
                    if _mh_otm_fibs:
                        _mh_cc_name, _mh_cc_strike_raw = _mh_otm_fibs[0]
                        _mh_cc_strike = round(_mh_cc_strike_raw)
                    else:
                        _mh_cc_name = "Above range"
                        _mh_cc_strike = round(_mh_px * 1.05)

                    if len(_mh_otm_fibs) >= 2:
                        _mh_cc2_name, _mh_cc2_raw = _mh_otm_fibs[1]
                        _mh_cc2_strike = round(_mh_cc2_raw)
                    else:
                        _mh_cc2_strike = _mh_cc_strike + round(_mh_wk_rng * 0.2) if _mh_wk_rng > 0 else _mh_cc_strike + 5

                    _mh_exp_monthly = (_mh_backdate + timedelta(days=30)).strftime("%Y-%m-%d")
                    _mh_exp_weekly = (_mh_backdate + timedelta(days=7)).strftime("%Y-%m-%d")
                    _mh_cc_pct = (_mh_cc_strike - _mh_px) / _mh_px * 100 if _mh_px > 0 else 0

                    if _mh_wk_zone == "HIGH":
                        _mh_cc_strat = (
                            f"📞 {_mh_tk} Sell ${_mh_cc_strike} Call ({_mh_cc_name}, {_mh_cc_pct:.1f}% OTM) "
                            f"Exp {_mh_exp_weekly} — HIGH zone, tight strike for max premium"
                            f" | Aggressive: Sell ${_mh_cc2_strike} Call Exp {_mh_exp_monthly}")
                    elif _mh_wk_zone == "LOW":
                        _mh_cc_strat = (
                            f"📞 {_mh_tk} Sell ${_mh_cc2_strike} Call ({_mh_cc_pct:.1f}%+ OTM) "
                            f"Exp {_mh_exp_monthly} — LOW zone, wider strike to allow upside"
                            f" | Conservative: Sell ${_mh_cc_strike} Call ({_mh_cc_name}) Exp {_mh_exp_weekly}")
                    else:
                        _mh_cc_strat = (
                            f"📞 {_mh_tk} Sell ${_mh_cc_strike} Call ({_mh_cc_name}, {_mh_cc_pct:.1f}% OTM) "
                            f"Exp {_mh_exp_monthly} — MID zone, balanced premium vs upside"
                            f" | Alt: ${_mh_cc2_strike} Call Exp {_mh_exp_monthly}")

                    # ── Build row ──
                    _mh_row = {
                        "Ticker": _mh_tk,
                        "Price": f"${_mh_px:.2f}",
                        "As Of": _mh_date_label,
                        "Wk Hi": f"${_mh_wk_hi:.2f}",
                        "Wk Lo": f"${_mh_wk_lo:.2f}",
                        "Wk Pos %": f"{_mh_wk_pos:.1f}%",
                        "Weekly Zone": _mh_wk_zone,
                        "Daily Zone": _mh_day_zone,
                        "Nearest Fib": f"{_mh_near_name} (${_mh_near_val:.2f})",
                        "Fib Compression": "Y" if _mh_fc else "N",
                    }
                    # Add fib level columns
                    _mh_fib_col_order = [
                        "E 261.8%", "E 200.0%", "E 161.8%", "E 141.4%", "E 127.2%",
                        "R 0.0%", "R 23.6%", "R 38.2%", "R 50.0%", "R 61.8%", "R 78.6%", "R 100.0%",
                        "N -23.6%", "N -38.2%", "N -50.0%", "N -61.8%", "N -100.0%",
                    ]
                    for _fn in _mh_fib_col_order:
                        _fv = _mh_fib.get(_fn, float("nan"))
                        _mh_row[_fn] = f"${_fv:.2f}" if not (isinstance(_fv, float) and _fv != _fv) else ""
                    _mh_row["Covered Call"] = _mh_cc_strat
                    _mh_row["Alpaca Options"] = _mh_alpaca_text
                    _mh_rows.append(_mh_row)
                except Exception:
                    continue

            _mh_progress.empty()

            if _mh_rows:
                st.session_state["_mh_report_rows"] = _mh_rows
            else:
                st.warning("No data could be fetched for the given tickers.")

    # ── Render report from session state ──
    _mh_saved_rows = st.session_state.get("_mh_report_rows", [])
    if _mh_saved_rows:
        _mh_df = pd.DataFrame(_mh_saved_rows)

        # ── Coloring helpers ──
        def _mh_color_zone(v):
            v = str(v)
            if v == "HIGH": return "background-color:#3d0a1a;color:#ff4d6a;font-weight:700"
            if v == "LOW":  return "background-color:#0a3d1f;color:#00e5a0;font-weight:700"
            return "background-color:#3d3a0a;color:#f5c842;font-weight:700"
        def _mh_color_fc(v):
            return "color:#ff4d6a;font-weight:700" if str(v) == "Y" else "color:#6b7099"

        # ── Fib zone highlight (row-wise) ──
        _mh_fib_col_order = [
            "E 261.8%", "E 200.0%", "E 161.8%", "E 141.4%", "E 127.2%",
            "R 0.0%", "R 23.6%", "R 38.2%", "R 50.0%", "R 61.8%", "R 78.6%", "R 100.0%",
            "N -23.6%", "N -38.2%", "N -50.0%", "N -61.8%", "N -100.0%",
        ]
        _mh_e_set = set(c for c in _mh_fib_col_order if c.startswith("E "))
        _mh_r_set = set(c for c in _mh_fib_col_order if c.startswith("R "))
        _mh_golden_set = {"R 38.2%", "R 50.0%", "R 61.8%"}
        def _mh_highlight_fib(row):
            zone = str(row.get("Weekly Zone", ""))
            out = []
            for col in row.index:
                if zone == "HIGH" and col in _mh_r_set:
                    out.append("background-color:#1f1400;color:#f5c842;font-weight:700")
                elif zone == "LOW" and col in _mh_e_set:
                    out.append("background-color:#001a0a;color:#00e5a0;font-weight:700")
                elif col in _mh_golden_set:
                    out.append("background-color:#1a1500;color:#d4a017;font-weight:600;border-bottom:2px solid #d4a01780")
                else:
                    out.append("")
            return out

        # ── Current-price nearest fib highlight ──
        def _mh_highlight_price(row):
            styles = [""] * len(row)
            try:
                px = float(str(row.get("Price", "")).replace("$", ""))
            except Exception:
                return styles
            best_col, best_diff = None, float("inf")
            for col in _mh_fib_col_order:
                if col not in row.index:
                    continue
                try:
                    val = float(str(row[col]).replace("$", "").strip())
                    diff = abs(val - px)
                    if diff < best_diff:
                        best_diff = diff
                        best_col = col
                except Exception:
                    pass
            if best_col is not None:
                col_idx = list(row.index).index(best_col)
                styles[col_idx] = "background-color:#0a1f3a;color:#ffffff;font-weight:900;border:2px solid #4d9fff"
            return styles

        with _mh_report_ph.container():
            st.markdown(
                f"#### 💼 Holdings Fib & Covered Call Report"
                f"\n{len(_mh_saved_rows)} ticker{'s' if len(_mh_saved_rows) != 1 else ''} · "
                f"🔵 = nearest fib to price"
            )

            # ── Split by Weekly Zone ──
            _mh_zone_cfg = [
                ("HIGH", "#ff4d6a", "🔴"),
                ("MID",  "#f5c842", "🟡"),
                ("LOW",  "#00e5a0", "🟢"),
            ]
            for _mhz, _mhzc, _mhzi in _mh_zone_cfg:
                _mhz_mask = _mh_df["Weekly Zone"] == _mhz
                _mhz_grp = _mh_df[_mhz_mask]
                if _mhz_grp.empty:
                    continue
                st.markdown(
                    f'<div style="background:rgba(0,0,0,0.3);border-left:3px solid {_mhzc}40;'
                    f'padding:6px 12px;margin:8px 0 4px;border-radius:0 4px 4px 0">'
                    f'<span style="font-size:14px">{_mhzi}</span> '
                    f'<b style="color:{_mhzc};font-size:13px">{_mhz} Weekly Zone</b> '
                    f'<span style="color:#6b7099;font-size:11px">'
                    f'({len(_mhz_grp)} ticker{"s" if len(_mhz_grp) != 1 else ""})</span>'
                    f'</div>',
                    unsafe_allow_html=True,
                )
                _mhz_styled = _mhz_grp.style
                if "Weekly Zone" in _mhz_grp.columns:
                    _mhz_styled = _mhz_styled.applymap(_mh_color_zone, subset=["Weekly Zone"])
                if "Daily Zone" in _mhz_grp.columns:
                    _mhz_styled = _mhz_styled.applymap(_mh_color_zone, subset=["Daily Zone"])
                if "Fib Compression" in _mhz_grp.columns:
                    _mhz_styled = _mhz_styled.applymap(_mh_color_fc, subset=["Fib Compression"])
                _mhz_styled = _mhz_styled.apply(_mh_highlight_fib, axis=1)
                _mhz_styled = _mhz_styled.apply(_mh_highlight_price, axis=1)
                st.dataframe(
                    _mhz_styled, use_container_width=True, hide_index=True,
                    height=min(len(_mhz_grp) * 35 + 48, 400),
                )

            # ── 🥇 Golden Zone ──
            _gz6_rows = []
            for _, _gz6_r in _mh_df.iterrows():
                try:
                    _gz6_tk = _gz6_r["Ticker"]
                    _gz6_38 = float(str(_gz6_r.get("R 38.2%", "")).replace("$", ""))
                    _gz6_61 = float(str(_gz6_r.get("R 61.8%", "")).replace("$", ""))
                    _gz6_px = float(str(_gz6_r.get("Price", "")).replace("$", ""))
                    _gz6_lo, _gz6_hi = min(_gz6_38, _gz6_61), max(_gz6_38, _gz6_61)
                    if _gz6_lo <= _gz6_px <= _gz6_hi:
                        _gz6_rows.append({"Ticker": _gz6_tk, "Price": f"${_gz6_px:.2f}",
                                          "R 38.2%": _gz6_r.get("R 38.2%", ""),
                                          "R 50.0%": _gz6_r.get("R 50.0%", ""),
                                          "R 61.8%": _gz6_r.get("R 61.8%", ""),
                                          "Weekly Zone": _gz6_r.get("Weekly Zone", "")})
                except Exception:
                    pass
            if _gz6_rows:
                st.markdown(
                    '<div style="background:linear-gradient(135deg,#1a1500,#1f1800);border:1px solid #d4a01740;'
                    'border-radius:8px;padding:12px 18px;margin:16px 0 8px">'
                    '<span style="font-size:18px">🥇</span> '
                    '<b style="color:#d4a017;font-size:14px">Golden Zone</b> '
                    '<span style="color:#8b7d3c;font-size:11px">'
                    f'({len(_gz6_rows)} ticker{"s" if len(_gz6_rows)!=1 else ""}) — '
                    f'Price within R 38.2% – R 61.8% retracement</span>'
                    '</div>',
                    unsafe_allow_html=True,
                )
                _gz6_df = pd.DataFrame(_gz6_rows)
                _gz6_styled = _gz6_df.style.applymap(
                    lambda v: "background-color:#1a1500;color:#d4a017;font-weight:700"
                    if str(v).startswith("$") else "",
                    subset=["R 38.2%", "R 50.0%", "R 61.8%"]
                )
                st.dataframe(_gz6_styled, use_container_width=True, hide_index=True,
                             height=min(len(_gz6_rows) * 35 + 48, 300))

            # ── CSV download ──
            import io as _mh_io
            _mh_csv = _mh_io.StringIO()
            _mh_df.to_csv(_mh_csv, index=False)
            st.download_button(
                label=f"📥 Download Holdings Report ({len(_mh_saved_rows)} tickers)",
                data=_mh_csv.getvalue(),
                file_name=f"holdings_fib_report_{_mh_backdate.strftime('%Y%m%d') if hasattr(_mh_backdate, 'strftime') else date.today().strftime('%Y%m%d')}.csv",
                mime="text/csv",
                key="btn_mh_csv_dl",
            )

# ╔══════════════════════════════════════════════════════════════════════════════╗
# ║ TAB 11: PAPER TRADING — Simulated Trades from Replay Sessions               ║
# ╚══════════════════════════════════════════════════════════════════════════════╝
with tab_paper:
  try:
    st.markdown(
        '<div style="background:linear-gradient(135deg,#0a0b14,#131625);border:1px solid #1a1d2e;'
        'border-radius:8px;padding:18px 24px;margin-bottom:16px">'
        '<div style="display:flex;align-items:center;gap:12px">'
        '<div style="font-size:28px">📄</div>'
        '<div>'
        '<div style="font-size:16px;font-weight:700;color:#e8ecff">Paper Trading</div>'
        '<div style="font-size:10px;color:#6b7099">'
        f'{"🔴 LIVE — Orders submitted to Alpaca" if not st.session_state.get("replay_mode", False) else "🔄 REPLAY — Local simulation only"} · '
        'Conditions configured in paper_config.py</div>'
        '</div></div></div>',
        unsafe_allow_html=True,
    )

    if not PAPER_TRADING_AVAILABLE:
        st.error(f"⚠️ Paper Trading unavailable: {_PAPER_IMPORT_ERROR or 'alpaca_paper.py not found'}")
        # Still show config even if engine failed
        try:
            import paper_config as _pc_display
            st.markdown("---")
            st.markdown("**⚙️ Paper Config (read-only — engine not loaded)**")
            _cfg_c1, _cfg_c2 = st.columns(2)
            with _cfg_c1:
                st.code(
                    f"Position Size:     ${_pc_display.POSITION_SIZE:,}\n"
                    f"Scenarios:         {_pc_display.ALLOWED_SCENARIOS}\n"
                    f"Require Alignment: {_pc_display.REQUIRE_ALIGNMENT}\n"
                    f"Auto in Replay:    {_pc_display.AUTO_PAPER_IN_REPLAY}",
                    language="text",
                )
            with _cfg_c2:
                st.code(
                    f"Exit on Stop:      {_pc_display.EXIT_ON_STOP}\n"
                    f"Exit on T1:        {_pc_display.EXIT_ON_T1}\n"
                    f"Exit on T2:        {_pc_display.EXIT_ON_T2}\n"
                    f"Session End Exit:  {_pc_display.EXIT_AT_SESSION_END}",
                    language="text",
                )
        except Exception:
            pass
    else:
        # Cache PaperTrader instance to avoid re-opening SQLite on every render
        if "_pt_instance" not in st.session_state:
            try:
                st.session_state["_pt_instance"] = PaperTrader()
            except Exception as _pt_err:
                st.error(f"⚠️ PaperTrader init failed: {_pt_err}")
                st.session_state["_pt_instance"] = None
        _pt = st.session_state["_pt_instance"]
        try:
            import paper_config as _pc_display
        except Exception as _pcr_err:
            st.warning(f"paper_config import failed: {_pcr_err}")
            _pc_display = None

        # ── Config + Alpaca — collapsed by default so trades are visible ─────
        _top_col1, _top_col2 = st.columns([3, 2])
        with _top_col1:
            with st.expander("⚙️ Active Config", expanded=False):
                _cfg_c1, _cfg_c2 = st.columns(2)
                with _cfg_c1:
                    st.markdown("**Entry Conditions**")
                    st.code(
                        f"Position Size:     ${_pc_display.POSITION_SIZE:,}\n"
                        f"Max Open Trades:   {_pc_display.MAX_OPEN_TRADES}\n"
                        f"Max Per Ticker:    {_pc_display.MAX_TRADES_PER_TICKER}\n"
                        f"Allow Long:        {_pc_display.ALLOW_LONG}\n"
                        f"Allow Short:       {_pc_display.ALLOW_SHORT}\n"
                        f"Scenarios:         {_pc_display.ALLOWED_SCENARIOS}\n"
                        f"Require Alignment: {_pc_display.REQUIRE_ALIGNMENT}\n"
                        f"Require V-Flow:    {_pc_display.REQUIRE_VFLOW}\n"
                        f"Allow Partial VF:  {_pc_display.ALLOW_PARTIAL_VFLOW}\n"
                        f"Min RVOL:          {_pc_display.MIN_RVOL}\n"
                        f"VWAP Aligned:      {_pc_display.REQUIRE_VWAP_ALIGNED}\n"
                        f"News Filter:       {_pc_display.NEWS_FILTER}\n"
                        f"OHLC Aligned:      {_pc_display.REQUIRE_OHLC_ALIGNED}\n"
                        f"Min Confidence:    {_pc_display.MIN_CONFIDENCE}\n"
                        f"Min RR(T1):        {_pc_display.MIN_RR_T1}\n"
                        f"Min Best RR:       {_pc_display.MIN_BEST_RR}\n"
                        f"Pullback Entry:    {_pc_display.ENTRY_ON_PULLBACK}\n"
                        f"Pullback Max %:    {_pc_display.PULLBACK_MAX_DIST_PCT}\n"
                        f"Max Entry P&L %:   {_pc_display.MAX_ENTRY_PNL_PCT}",
                        language="text",
                    )
                    # Scenario entry rules
                    _ser = getattr(_pc_display, "SCENARIO_ENTRY_RULES", None)
                    if _ser:
                        st.markdown("**Scenario Entry Rules**")
                        _ser_lines = []
                        for _sk, _sv in _ser.items():
                            if isinstance(_sv, dict) and any(k in _sv for k in ("LONG", "SHORT")):
                                for _sd, _sa in _sv.items():
                                    _act = _sa if isinstance(_sa, str) else _sa.get("action", "ENTER")
                                    _ser_lines.append(f"{_sk}/{_sd}: {_act}")
                            elif isinstance(_sv, dict):
                                _ser_lines.append(f"{_sk}: {_sv.get('action', 'ENTER')} (zone={_sv.get('stop_zone_pct', '')})")
                            else:
                                _ser_lines.append(f"{_sk}: {_sv}")
                        st.code("\n".join(_ser_lines), language="text")
                with _cfg_c2:
                    st.markdown("**Exit Conditions**")
                    st.code(
                        f"Exit on Stop:      {_pc_display.EXIT_ON_STOP}\n"
                        f"Exit on T1:        {_pc_display.EXIT_ON_T1}\n"
                        f"Exit on T2:        {_pc_display.EXIT_ON_T2}\n"
                        f"Exit on Best RR:   {_pc_display.EXIT_ON_BEST_RR}\n"
                        f"Min Exit Best RR:  {_pc_display.MIN_EXIT_BEST_RR}\n"
                        f"Partial at T1:     {_pc_display.PARTIAL_EXIT_AT_T1}\n"
                        f"Trail after T1:    {_pc_display.TRAIL_STOP_AFTER_T1}\n"
                        f"Session End Exit:  {_pc_display.EXIT_AT_SESSION_END}\n"
                        f"End Time (CST):    {_pc_display.SESSION_END_TIME_CST}\n"
                        f"Exit on Diverged:  {_pc_display.EXIT_ON_DIVERGED}\n"
                        f"Exit on VFlow ❌:  {_pc_display.EXIT_ON_VFLOW_AGAINST}\n"
                        f"Max Loss %:        {_pc_display.MAX_LOSS_PCT}\n"
                        f"\nAlpaca Submit:     {_pc_display.SUBMIT_TO_ALPACA}\n"
                        f"Order Type:        {_pc_display.ORDER_TYPE}\n"
                        f"Time in Force:     {_pc_display.TIME_IN_FORCE}\n"
                        f"Auto in Replay:    {_pc_display.AUTO_PAPER_IN_REPLAY}\n"
                        f"Log All Checks:    {_pc_display.LOG_ALL_CHECKS}",
                        language="text",
                    )
                st.caption("Edit paper_config.py to change these settings.")

        with _top_col2:
            with st.expander("🏦 Alpaca Paper Account", expanded=False):
                if _pt:
                    if st.button("🔄 Load / Refresh", key="pt_alpaca_refresh"):
                        st.session_state["_pt_alpaca_data"] = _pt.get_alpaca_account()
                        st.session_state["_pt_alpaca_positions"] = _pt.get_alpaca_positions()
                        st.session_state["_pt_alpaca_orders"] = _pt.get_alpaca_orders(status="all", limit=20)
                    _acct = st.session_state.get("_pt_alpaca_data")
                    if _acct is None:
                        st.caption("Click 'Load / Refresh' to fetch Alpaca balance")
                    elif "error" in _acct:
                        st.warning(f"Alpaca: {_acct['error']}")
                    else:
                        st.metric("Equity", f"${float(_acct.get('equity', 0)):,.2f}")
                        st.metric("Buying Power", f"${float(_acct.get('buying_power', 0)):,.2f}")
                        _day_pl = float(_acct.get('equity', 0)) - float(_acct.get('last_equity', 0))
                        st.metric("Day P&L", f"${_day_pl:+,.2f}")
                        st.metric("Cash", f"${float(_acct.get('cash', 0)):,.2f}")

                    # ── Alpaca Positions ──
                    _positions = st.session_state.get("_pt_alpaca_positions")
                    if _positions and not isinstance(_positions, dict):
                        st.markdown("**📊 Open Positions**")
                        if _positions:
                            _pos_rows = []
                            for _p in _positions:
                                _unrealized = float(_p.get("unrealized_pl", 0))
                                _pos_rows.append({
                                    "Ticker": _p.get("symbol", ""),
                                    "Qty": _p.get("qty", 0),
                                    "Side": _p.get("side", "").upper(),
                                    "Entry": f"${float(_p.get('avg_entry_price', 0)):,.2f}",
                                    "Current": f"${float(_p.get('current_price', 0)):,.2f}",
                                    "P&L": f"${_unrealized:+,.2f}",
                                    "P&L %": f"{float(_p.get('unrealized_plpc', 0)) * 100:+.2f}%",
                                    "Mkt Value": f"${float(_p.get('market_value', 0)):,.2f}",
                                })
                            _pos_df = pd.DataFrame(_pos_rows)
                            def _color_pos_pnl(val):
                                if "+" in str(val): return "color: #00e5a0; font-weight: 700"
                                if "-" in str(val): return "color: #ff4d6a; font-weight: 700"
                                return ""
                            st.dataframe(
                                _pos_df.style.applymap(_color_pos_pnl, subset=["P&L", "P&L %"]),
                                use_container_width=True, hide_index=True, height=min(40 * len(_pos_df) + 38, 250),
                            )
                        else:
                            st.caption("No open positions")

                    # ── Alpaca Orders ──
                    _orders = st.session_state.get("_pt_alpaca_orders")
                    if _orders and not isinstance(_orders, dict):
                        st.markdown("**📋 Recent Orders**")
                        if _orders:
                            _ord_rows = []
                            for _o in _orders[:15]:
                                _status = _o.get("status", "").upper()
                                _icon = "✅" if _status == "FILLED" else ("⏳" if _status in ("NEW", "ACCEPTED", "PARTIALLY_FILLED") else "❌")
                                _filled_qty = _o.get("filled_qty", "0")
                                _ord_rows.append({
                                    "": _icon,
                                    "Ticker": _o.get("symbol", ""),
                                    "Side": _o.get("side", "").upper(),
                                    "Qty": f"{_o.get('qty', 0)}",
                                    "Filled": f"{_filled_qty}",
                                    "Type": _o.get("type", ""),
                                    "Status": _status,
                                    "Submitted": str(_o.get("submitted_at", ""))[:16],
                                })
                            st.dataframe(pd.DataFrame(_ord_rows), use_container_width=True, hide_index=True, height=min(40 * len(_ord_rows) + 38, 300))
                        else:
                            st.caption("No recent orders")
                else:
                    st.warning("PaperTrader not initialized")

        st.markdown("---")

        if _pt:
            # ── Quick actions row ───────────────────────────────────────────────
            _qa_col1, _qa_col2, _qa_col3 = st.columns([1, 1, 4])
            with _qa_col1:
                if st.button("🗑️ Clear All Trades", key="pt_quick_clear"):
                    _pt.clear_all()
                    for _ck in ("_pt_stats_cache", "_pt_trades_cache", "_pt_cache_ts",
                                "_pt_open_cache", "_pt_closed_cache", "_pt_daily_cache"):
                        st.session_state.pop(_ck, None)
                    st.success("All paper trades cleared")
                    st.rerun()
            with _qa_col2:
                if st.button("🔄 Refresh", key="pt_quick_refresh"):
                    for _ck in ("_pt_stats_cache", "_pt_trades_cache", "_pt_cache_ts",
                                "_pt_open_cache", "_pt_closed_cache", "_pt_daily_cache"):
                        st.session_state.pop(_ck, None)
                    st.rerun()

            # ── Overall Stats Row ───────────────────────────────────────────────
            _stats_key = "_pt_stats_cache"
            _trades_key = "_pt_trades_cache"
            _cache_ts_key = "_pt_cache_ts"
            import time as _pt_time
            _cache_age = _pt_time.time() - st.session_state.get(_cache_ts_key, 0)
            if _cache_age > 5 or _stats_key not in st.session_state:
                st.session_state[_stats_key] = _pt.get_overall_stats()
                st.session_state[_trades_key] = _pt.get_all_trades(limit=20)
                st.session_state["_pt_open_cache"] = _pt.get_open_trades()
                st.session_state["_pt_closed_cache"] = _pt.get_closed_trades(limit=200)
                st.session_state["_pt_daily_cache"] = _pt.get_daily_summary(limit=60)
                st.session_state[_cache_ts_key] = _pt_time.time()
            _stats = st.session_state[_stats_key]
            _total_pnl_pt = _stats.get("total_pnl", 0) or 0
            _win_rate = _stats.get("win_rate", 0) or 0
            _total_trades_pt = _stats.get("total_trades", 0) or 0
            _open_count = _stats.get("open_count", 0) or 0
            _wins_pt = _stats.get("wins", 0) or 0
            _losses_pt = _stats.get("losses", 0) or 0
            _best = _stats.get("best_trade") or 0
            _worst = _stats.get("worst_trade") or 0

            _stat_cols = st.columns(6)
            with _stat_cols[0]:
                st.metric("Total P&L", f"${_total_pnl_pt:,.2f}", delta=f"{'▲' if _total_pnl_pt >= 0 else '▼'}")
            with _stat_cols[1]:
                st.metric("Win Rate", f"{_win_rate:.1f}%", delta=f"{_wins_pt}W / {_losses_pt}L")
            with _stat_cols[2]:
                st.metric("Total Trades", f"{_total_trades_pt}", delta=f"{_open_count} open")
            with _stat_cols[3]:
                st.metric("Best Trade", f"${_best:+,.2f}" if _best else "—")
            with _stat_cols[4]:
                st.metric("Worst Trade", f"${_worst:+,.2f}" if _worst else "—")
            with _stat_cols[5]:
                _avg_pnl = _stats.get("avg_pnl_pct", 0) or 0
                st.metric("Avg P&L %", f"{_avg_pnl:+.2f}%")

            # ── Recent trades (always visible, no sub-tab clicking needed) ──
            _all_recent = st.session_state.get(_trades_key) or _pt.get_all_trades(limit=20)
            if _all_recent:
                st.markdown("---")
                _rec_rows = []
                for _rt in _all_recent:
                    _pnl_d = _rt.get("pnl_dollars") or 0
                    _pnl_p = _rt.get("pnl_pct") or 0
                    _oc = _rt.get("outcome", "")
                    _icon = "🟢" if _oc == "WIN" else ("🔴" if _oc == "LOSS" else ("🔵" if _rt["status"] == "OPEN" else "⚪"))
                    _rec_rows.append({
                        "#": _rt["id"],
                        "Date": _rt["trade_date"],
                        "Ticker": _rt["ticker"],
                        "Dir": _rt["direction"],
                        "Entry $": f"${_rt['entry_price']:.2f}",
                        "Exit $": f"${_rt['exit_price']:.2f}" if _rt.get("exit_price") else "OPEN",
                        "Shares": _rt["shares"],
                        "P&L $": f"${_pnl_d:+,.2f}" if _rt["status"] == "CLOSED" else "—",
                        "P&L %": f"{_pnl_p:+.2f}%" if _rt["status"] == "CLOSED" else "—",
                        "Result": f"{_icon} {_oc or _rt['status']}",
                        "Reason": _rt.get("exit_reason", ""),
                        "Scenario": _rt.get("scenario", ""),
                    })
                _rec_df = pd.DataFrame(_rec_rows)
                def _color_rec_pnl(val):
                    if "+" in str(val): return "color: #00e5a0"
                    elif "-" in str(val): return "color: #ff4d6a"
                    return ""
                _styled_rec = _rec_df.style.applymap(_color_rec_pnl, subset=["P&L $", "P&L %"])
                st.markdown(f"**Recent Trades** ({len(_all_recent)} shown)")
                st.dataframe(_styled_rec, use_container_width=True, hide_index=True)
            else:
                st.info("No paper trades yet — run a replay session with AUTO_PAPER_IN_REPLAY=True")

            st.markdown("---")

            # ── Sub-tabs ────────────────────────────────────────────────────────
            _pt_tab_closed, _pt_tab_open, _pt_tab_daily = st.tabs([
                "📋 Trade Log",
                "🟢 Open Trades",
                "📊 Daily Summary",
            ])

            # ── Open Trades ─────────────────────────────────────────
            with _pt_tab_open:
                _open_trades = st.session_state.get("_pt_open_cache") or []
                if _open_trades:
                    _live_px_map = st.session_state.get("_paper_live_prices", {})
                    _open_rows = []
                    _total_unrl = 0.0
                    for _ot in _open_trades:
                        _cur_px = _live_px_map.get(_ot["ticker"])
                        _e_px = _ot["entry_price"]
                        _sh = _ot["shares"]
                        if _cur_px is not None:
                            if _ot["direction"] == "LONG":
                                _unrl_d = (_cur_px - _e_px) * _sh
                                _unrl_p = (_cur_px - _e_px) / _e_px * 100
                            else:
                                _unrl_d = (_e_px - _cur_px) * _sh
                                _unrl_p = (_e_px - _cur_px) / _e_px * 100
                            _total_unrl += _unrl_d
                        else:
                            _unrl_d = None
                            _unrl_p = None
                        _open_rows.append({
                            "ID": _ot["id"],
                            "Date": _ot["trade_date"],
                            "Ticker": _ot["ticker"],
                            "Dir": _ot["direction"],
                            "Entry": f"${_e_px:.2f}",
                            "Current": f"${_cur_px:.2f}" if _cur_px else "—",
                            "P&L $": f"${_unrl_d:+,.2f}" if _unrl_d is not None else "—",
                            "P&L %": f"{_unrl_p:+.2f}%" if _unrl_p is not None else "—",
                            "Stop": f"${_ot['stop_price']:.2f}",
                            "T1": f"${_ot['t1_price']:.2f}",
                            "T2": f"${_ot['t2_price']:.2f}",
                            "Shares": _sh,
                            "Scenario": _ot.get("scenario", ""),
                            "Entered": _ot.get("entry_time", "")[:16] if _ot.get("entry_time") else "",
                        })
                    _open_df = pd.DataFrame(_open_rows)

                    # Unrealized P&L summary
                    _unrl_color = "#00e5a0" if _total_unrl >= 0 else "#ff4d6a"
                    st.markdown(
                        f'<div style="font-size:13px;margin-bottom:8px">'
                        f'Open: <b>{len(_open_trades)}</b> trades · '
                        f'Unrealized: <span style="color:{_unrl_color};font-weight:700">${_total_unrl:+,.2f}</span>'
                        f'</div>',
                        unsafe_allow_html=True,
                    )

                    def _color_open_pnl(val):
                        if "+" in str(val):
                            return "color: #00e5a0"
                        elif "-" in str(val):
                            return "color: #ff4d6a"
                        return ""

                    _styled_open = _open_df.style.applymap(_color_open_pnl, subset=["P&L $", "P&L %"])
                    st.dataframe(_styled_open, use_container_width=True, hide_index=True)

                    # Manual close
                    _close_col1, _close_col2, _close_col3 = st.columns([1, 1, 1])
                    with _close_col1:
                        _close_id = st.number_input("Trade ID to close", min_value=1, step=1, key="pt_close_id")
                    with _close_col2:
                        _close_px = st.number_input("Exit price", min_value=0.01, step=0.01, key="pt_close_px")
                    with _close_col3:
                        if st.button("❌ Force Close", key="pt_force_close"):
                            _res = _pt.force_exit(int(_close_id), _close_px, reason="MANUAL")
                            if _res:
                                st.session_state.pop("_pt_cache_ts", None)
                                st.success(f"Closed #{_res['id']} {_res['ticker']} — {_res['outcome']} ${_res['pnl_dollars']:+.2f}")
                                st.rerun()
                            else:
                                st.warning("Trade not found or already closed")
                else:
                    st.info("No open paper trades")

            # ── Trade Log ────────────────────────────────────────────
            with _pt_tab_closed:
                _closed = st.session_state.get("_pt_closed_cache") or []
                if _closed:
                    _close_rows = []
                    for _ct in _closed:
                        _pnl_d = _ct.get("pnl_dollars", 0) or 0
                        _pnl_p = _ct.get("pnl_pct", 0) or 0
                        _outcome = _ct.get("outcome", "")
                        if _outcome == "WIN":
                            _o_icon = "🟢"
                        elif _outcome == "LOSS":
                            _o_icon = "🔴"
                        else:
                            _o_icon = "⚪"
                        _close_rows.append({
                            "Date": _ct["trade_date"],
                            "Ticker": _ct["ticker"],
                            "Dir": _ct["direction"],
                            "Entry": f"${_ct['entry_price']:.2f}",
                            "Exit": f"${_ct['exit_price']:.2f}" if _ct.get("exit_price") else "—",
                            "Shares": _ct["shares"],
                            "P&L $": f"${_pnl_d:+,.2f}",
                            "P&L %": f"{_pnl_p:+.2f}%",
                            "Result": f"{_o_icon} {_outcome}",
                            "Reason": _ct.get("exit_reason", ""),
                            "Scenario": _ct.get("scenario", ""),
                        })
                    _close_df = pd.DataFrame(_close_rows)

                    def _color_pnl_paper(val):
                        if "+" in str(val):
                            return "color: #00e5a0"
                        elif "-" in str(val):
                            return "color: #ff4d6a"
                        return ""

                    _styled = _close_df.style.applymap(_color_pnl_paper, subset=["P&L $", "P&L %"])
                    st.dataframe(_styled, use_container_width=True, hide_index=True)
                else:
                    st.info("No closed paper trades yet")

            # ── Daily Summary ─────────────────────────────────────────
            with _pt_tab_daily:
                _daily = st.session_state.get("_pt_daily_cache") or []
                if _daily:
                    _daily_rows = []
                    for _d in _daily:
                        _d_pnl = _d.get("total_pnl", 0) or 0
                        _d_wins = _d.get("wins", 0) or 0
                        _d_losses = _d.get("losses", 0) or 0
                        _d_total = _d.get("total_trades", 0) or 0
                        _d_wr = round(_d_wins / (_d_wins + _d_losses) * 100, 1) if (_d_wins + _d_losses) > 0 else 0
                        _daily_rows.append({
                            "Date": _d["trade_date"],
                            "Trades": _d_total,
                            "Wins": _d_wins,
                            "Losses": _d_losses,
                            "Win %": f"{_d_wr:.0f}%",
                            "P&L": f"${_d_pnl:+,.2f}",
                            "Avg %": f"{_d.get('avg_pnl_pct', 0) or 0:+.2f}%",
                        })
                    _daily_df = pd.DataFrame(_daily_rows)

                    # Equity curve
                    _cumulative = []
                    _running = 0
                    for _d in reversed(_daily):
                        _running += (_d.get("total_pnl", 0) or 0)
                        _cumulative.append({"Date": _d["trade_date"], "Cumulative P&L": _running})
                    if _cumulative:
                        _eq_df = pd.DataFrame(_cumulative)
                        _eq_fig = go.Figure()
                        _eq_fig.add_trace(go.Scatter(
                            x=_eq_df["Date"], y=_eq_df["Cumulative P&L"],
                            mode="lines+markers",
                            line=dict(color="#4d9fff", width=2),
                            marker=dict(size=5),
                            fill="tozeroy",
                            fillcolor="rgba(77,159,255,0.1)",
                        ))
                        _eq_fig.update_layout(
                            title="Equity Curve",
                            template="plotly_dark",
                            paper_bgcolor="#07080d",
                            plot_bgcolor="#0a0b14",
                            height=300,
                            margin=dict(l=40, r=20, t=40, b=30),
                            yaxis_title="Cumulative P&L ($)",
                        )
                        st.plotly_chart(_eq_fig, use_container_width=True)

                    st.dataframe(_daily_df, use_container_width=True, hide_index=True)
                else:
                    st.info("No daily data yet — run a replay session with paper trading enabled")

            # ── Danger Zone ──────────────────────────────────────────
            st.markdown("---")
            with st.expander("🗑️ Danger Zone", expanded=False):
                _dz_col1, _dz_col2 = st.columns(2)
                with _dz_col1:
                    _del_id = st.number_input("Delete trade by ID", min_value=1, step=1, key="pt_del_id")
                    if st.button("🗑️ Delete Trade", key="pt_del_trade"):
                        _pt.delete_trade(int(_del_id))
                        st.session_state.pop("_pt_cache_ts", None)
                        st.success(f"Deleted trade #{int(_del_id)}")
                        st.rerun()
                with _dz_col2:
                    st.warning("This clears ALL paper trades permanently.")
                    if st.button("💀 Clear All Paper Trades", key="pt_clear_all"):
                        _pt.clear_all()
                        st.session_state.pop("_pt_cache_ts", None)
                        st.success("All paper trades cleared")
                        st.rerun()
  except Exception as _tab_paper_err:
    import traceback
    st.error(f"⚠️ Paper Trading tab error: {_tab_paper_err}")
    st.code(traceback.format_exc(), language="text")

# ╔══════════════════════════════════════════════════════════════════════════════╗
# ║ TAB: TOS SCANNER — Full Multi-Condition Scanner                             ║
# ╚══════════════════════════════════════════════════════════════════════════════╝
with tab_tos:
  try:
    st.markdown(
        '<div style="background:linear-gradient(135deg,#0a0b14,#131625);border:1px solid #1a1d2e;'
        'border-radius:8px;padding:16px 20px;margin-bottom:16px">'
        '<h2 style="margin:0;color:#e8ecff;font-size:22px">📡 TOS Scanner</h2>'
        '<p style="margin:4px 0 0;color:#6b7099;font-size:12px">'
        'Multi-condition scanner — Fibonacci · AGIG ZigZag · Weinstein · RSI · MACD · Bollinger · MA · Volume · FVG'
        '</p></div>',
        unsafe_allow_html=True,
    )

    if not TOS_SCANNER_AVAILABLE:
        st.error("tos_scanner.py import failed. Check console output.")
    else:
        # ── Import watchlist dict from tos_scanner ──
        try:
            from tos_scanner import WATCHLISTS as _TOS_WATCHLISTS, apply_filters as _tos_apply_filters
        except ImportError:
            _TOS_WATCHLISTS = {}
            _tos_apply_filters = lambda r, f: r

        # ══════════════════════════════════════════════════════════════════════
        # INPUT PANEL
        # ══════════════════════════════════════════════════════════════════════
        _inp_c1, _inp_c2, _inp_c3 = st.columns([2, 2, 1])

        with _inp_c1:
            _wl_options = ["Custom"] + list(_TOS_WATCHLISTS.keys())
            _wl_choice = st.selectbox("Watchlist Preset", _wl_options, key="tos_wl_preset")

        with _inp_c2:
            _preset_val = ", ".join(_TOS_WATCHLISTS.get(_wl_choice, [])) if _wl_choice != "Custom" else "TSLA, AAPL, MSFT, NVDA, AMD"
            _tos_tickers_raw = st.text_input(
                "Ticker(s) — comma-separated (or edit after selecting preset)",
                value=_preset_val,
                key="tos_tickers",
            ).upper().strip()
            _tos_tickers = [t.strip() for t in _tos_tickers_raw.split(",") if t.strip()]

        with _inp_c3:
            st.markdown("<br>", unsafe_allow_html=True)
            _tos_scan_btn = st.button("🔍 Run Scan", use_container_width=True, key="tos_scan_btn")

        # ══════════════════════════════════════════════════════════════════════
        # SCAN CONDITION FILTERS  (TOS Study Filter equivalent)
        # ══════════════════════════════════════════════════════════════════════
        with st.expander("🎛️ Scan Conditions (Filter Results)", expanded=False):
            _fc1, _fc2, _fc3, _fc4 = st.columns(4)

            with _fc1:
                st.markdown("**Bias & Grade**")
                _f_bias = st.selectbox("Direction", ["ANY", "BULLISH", "BEARISH"], key="f_bias")
                _f_aligned = st.checkbox("Require W+D Aligned", key="f_aligned")
                _f_grade = st.selectbox("Min Grade", ["Any", "A+", "A", "B", "C"], key="f_grade")
                _f_min_score = st.slider("Min Signal Score", 0, 12, 0, key="f_min_score")

            with _fc2:
                st.markdown("**RSI & MACD**")
                _f_rsi_min = st.number_input("RSI Min", 0, 100, 0, key="f_rsi_min")
                _f_rsi_max = st.number_input("RSI Max", 0, 100, 100, key="f_rsi_max")
                _f_rsi_os  = st.checkbox("Oversold (<30)", key="f_rsi_os")
                _f_rsi_ob  = st.checkbox("Overbought (>70)", key="f_rsi_ob")
                _f_macd_cross = st.selectbox("MACD Cross", ["ANY", "UP", "DOWN"], key="f_macd_cross")
                _f_macd_dir   = st.selectbox("MACD Direction", ["ANY", "BULLISH", "BEARISH"], key="f_macd_dir")

            with _fc3:
                st.markdown("**Moving Averages**")
                _f_ma20  = st.selectbox("Price vs MA20",  ["ANY", "ABOVE", "BELOW"], key="f_ma20")
                _f_ma50  = st.selectbox("Price vs MA50",  ["ANY", "ABOVE", "BELOW"], key="f_ma50")
                _f_ma200 = st.selectbox("Price vs MA200", ["ANY", "ABOVE", "BELOW"], key="f_ma200")
                _f_golden = st.checkbox("Golden Cross (MA50>MA200)", key="f_golden")
                _f_death  = st.checkbox("Death Cross (MA50<MA200)", key="f_death")
                st.markdown("**Bollinger Bands**")
                _f_bb_pos  = st.selectbox("BB Position", ["ANY", "ABOVE_UPPER", "BELOW_LOWER", "INSIDE"], key="f_bb_pos")
                _f_bb_sq   = st.checkbox("BB Squeeze", key="f_bb_sq")

            with _fc4:
                st.markdown("**Weinstein & Volume**")
                _f_wein_phase = st.selectbox("Weinstein Phase", ["Any", "STAGE 2", "STAGE 4", "STAGE 1", "STAGE 3"], key="f_wein_phase")
                _f_wein_min   = st.slider("Min Weinstein Score", 0, 6, 0, key="f_wein_min")
                _f_vol_trend  = st.selectbox("Volume Trend", ["ANY", "ACCUMULATING", "DISTRIBUTING", "FLAT"], key="f_vol_trend")
                st.markdown("**Fibonacci & Patterns**")
                _f_fib_zone   = st.selectbox("Fib Zone", ["ANY", "BUY ZONE", "SELL ZONE", "DEEP DISCOUNT", "EXTENDED", "NEUTRAL"], key="f_fib_zone")
                _f_bull_div   = st.checkbox("Bullish Divergence", key="f_bull_div")
                _f_bear_div   = st.checkbox("Bearish Divergence", key="f_bear_div")

        # ══════════════════════════════════════════════════════════════════════
        # RUN SCAN
        # ══════════════════════════════════════════════════════════════════════
        if _tos_scan_btn and _tos_tickers:
            _tos_bar = st.progress(0, text="Fetching SPY reference data...")
            import yfinance as _yf2
            try:
                _spy = _yf2.Ticker("SPY")
                _spy_w = _spy.history(period="5y", interval="1wk", auto_adjust=True)
                _spy_d = _spy.history(period="2y", interval="1d", auto_adjust=True)
            except Exception:
                _spy_w, _spy_d = pd.DataFrame(), pd.DataFrame()

            _tos_results = []
            _tos_executor = get_thread_pool()
            _tos_futures = {
                _tos_executor.submit(tos_scan_ticker, _tkr, _spy_w, _spy_d): _tkr
                for _tkr in _tos_tickers
            }
            _tos_done = 0
            _tos_total = len(_tos_tickers)
            for _fut in concurrent.futures.as_completed(_tos_futures):
                _tkr = _tos_futures[_fut]
                _tos_done += 1
                _tos_bar.progress(_tos_done / _tos_total, text=f"Scanning {_tkr} ({_tos_done}/{_tos_total})...")
                try:
                    _r = _fut.result()
                    if _r:
                        _tos_results.append(_r)
                except Exception as _te:
                    st.warning(f"⚠️ {_tkr}: {_te}")
            _tos_bar.empty()
            trim_memory()

            if _tos_results:
                st.session_state["_tos_results"] = _tos_results
                st.session_state["_tos_scan_count"] = len(_tos_tickers)
            else:
                st.info("No results returned.")

        # ══════════════════════════════════════════════════════════════════════
        # BUILD FILTER DICT & APPLY
        # ══════════════════════════════════════════════════════════════════════
        _tos_raw = st.session_state.get("_tos_results", [])
        if _tos_raw:
            _active_filters = {
                "bias_direction":     st.session_state.get("f_bias", "ANY"),
                "require_aligned":    st.session_state.get("f_aligned", False),
                "min_grade":          st.session_state.get("f_grade", "Any") if st.session_state.get("f_grade", "Any") != "Any" else None,
                "min_score":          st.session_state.get("f_min_score", 0) or None,
                "rsi_min":            st.session_state.get("f_rsi_min", 0) if st.session_state.get("f_rsi_min", 0) > 0 else None,
                "rsi_max":            st.session_state.get("f_rsi_max", 100) if st.session_state.get("f_rsi_max", 100) < 100 else None,
                "require_rsi_oversold":   st.session_state.get("f_rsi_os", False),
                "require_rsi_overbought": st.session_state.get("f_rsi_ob", False),
                "macd_cross":    st.session_state.get("f_macd_cross", "ANY") if st.session_state.get("f_macd_cross") != "ANY" else None,
                "macd_direction": st.session_state.get("f_macd_dir", "ANY"),
                "price_vs_ma20":  st.session_state.get("f_ma20", "ANY"),
                "price_vs_ma50":  st.session_state.get("f_ma50", "ANY"),
                "price_vs_ma200": st.session_state.get("f_ma200", "ANY"),
                "require_golden_cross": st.session_state.get("f_golden", False),
                "require_death_cross":  st.session_state.get("f_death", False),
                "bb_position":      st.session_state.get("f_bb_pos", "ANY"),
                "require_bb_squeeze": st.session_state.get("f_bb_sq", False),
                "weinstein_phase":  st.session_state.get("f_wein_phase", "Any") if st.session_state.get("f_wein_phase", "Any") != "Any" else "",
                "min_weinstein":    st.session_state.get("f_wein_min", 0) or None,
                "vol_trend":        st.session_state.get("f_vol_trend", "ANY"),
                "fib_zone":         st.session_state.get("f_fib_zone", "ANY"),
                "require_bull_div": st.session_state.get("f_bull_div", False),
                "require_bear_div": st.session_state.get("f_bear_div", False),
            }
            _tos_data = _tos_apply_filters(_tos_raw, _active_filters)
            _tos_df = pd.DataFrame(_tos_data) if _tos_data else pd.DataFrame()

            # ── Summary banner ──
            _n_scanned = len(_tos_raw)
            _n_filtered = len(_tos_data)
            _n_bull  = sum(1 for r in _tos_data if r.get("combined_bias") == "BULLISH ALIGNED")
            _n_bear  = sum(1 for r in _tos_data if r.get("combined_bias") == "BEARISH ALIGNED")
            _n_mixed = _n_filtered - _n_bull - _n_bear
            _n_aplus = sum(1 for r in _tos_data if r.get("signal_grade") == "A+")
            _n_a     = sum(1 for r in _tos_data if r.get("signal_grade") == "A")
            _n_bb_sq = sum(1 for r in _tos_data if r.get("bb_squeeze"))
            _n_gc    = sum(1 for r in _tos_data if r.get("golden_cross"))

            st.markdown(
                f'<div style="background:#0d0f17;border:1px solid #1a1d2e;padding:12px 16px;'
                f'border-radius:6px;margin-bottom:12px;display:flex;flex-wrap:wrap;gap:16px">'
                f'<span style="color:#6b7099;font-size:11px">'
                f'Scanned <b style="color:#e8ecff">{_n_scanned}</b> · '
                f'Showing <b style="color:#e8ecff">{_n_filtered}</b> after filters</span>'
                f'<span style="color:#00e5a0;font-size:11px">🟢 Bullish: <b>{_n_bull}</b></span>'
                f'<span style="color:#ff4d6a;font-size:11px">🔴 Bearish: <b>{_n_bear}</b></span>'
                f'<span style="color:#a78bfa;font-size:11px">Mixed: <b>{_n_mixed}</b></span>'
                f'<span style="color:#00e5a0;font-size:11px">A+: <b>{_n_aplus}</b></span>'
                f'<span style="color:#4d9fff;font-size:11px">A: <b>{_n_a}</b></span>'
                f'<span style="color:#ffe066;font-size:11px">BB Squeeze: <b>{_n_bb_sq}</b></span>'
                f'<span style="color:#4d9fff;font-size:11px">Golden Cross: <b>{_n_gc}</b></span>'
                f'</div>',
                unsafe_allow_html=True,
            )

            # ── Column definitions ──
            _tos_display_cols = [
                "ticker", "price",
                "combined_bias", "signal_grade", "signal_score",
                "w_bias", "d_bias",
                "d_rsi", "d_rsi_label",
                "w_rsi",
                "d_macd_label", "d_macd_cross_up", "d_macd_cross_down",
                "price_vs_ma20", "price_vs_ma50", "price_vs_ma200",
                "golden_cross", "death_cross",
                "bb_bandwidth", "bb_pct_b", "bb_squeeze", "bb_position",
                "w_ema_trend", "d_ema_trend",
                "w_fib_zone", "d_fib_zone", "d_fib_position",
                "w_conviction", "d_conviction", "d_vol_trend", "d_vol_ratio",
                "w_weinstein_score", "d_weinstein_score",
                "w_weinstein_phase", "d_weinstein_phase",
                "d_momentum", "d_roc", "d_volatility_label",
                "d_bull_div", "d_bear_div",
                "d_bull_fvg_count", "d_bear_fvg_count",
            ]
            _tos_col_rename = {
                "combined_bias": "Bias (W+D)", "signal_grade": "Grade", "signal_score": "Score",
                "w_bias": "W Bias", "d_bias": "D Bias",
                "d_rsi": "D RSI", "d_rsi_label": "RSI State", "w_rsi": "W RSI",
                "d_macd_label": "MACD", "d_macd_cross_up": "MACD↑", "d_macd_cross_down": "MACD↓",
                "price_vs_ma20": "vs MA20", "price_vs_ma50": "vs MA50", "price_vs_ma200": "vs MA200",
                "golden_cross": "Golden✓", "death_cross": "Death✗",
                "bb_bandwidth": "BB Width%", "bb_pct_b": "BB %B", "bb_squeeze": "BB Sq", "bb_position": "BB Pos",
                "w_ema_trend": "W EMA", "d_ema_trend": "D EMA",
                "w_fib_zone": "W Fib", "d_fib_zone": "D Fib", "d_fib_position": "Fib%",
                "w_conviction": "W Conv", "d_conviction": "D Conv",
                "d_vol_trend": "Vol Trend", "d_vol_ratio": "Vol Ratio",
                "w_weinstein_score": "W Wein", "d_weinstein_score": "D Wein",
                "w_weinstein_phase": "W Phase", "d_weinstein_phase": "D Phase",
                "d_momentum": "Momentum", "d_roc": "ROC",
                "d_volatility_label": "Volatility",
                "d_bull_div": "Bull Div", "d_bear_div": "Bear Div",
                "d_bull_fvg_count": "Bull FVG", "d_bear_fvg_count": "Bear FVG",
            }

            # ── Styling function ──
            def _tos_style(df):
                def _color_bias(val):
                    if not isinstance(val, str): return ""
                    v = val.upper()
                    if any(x in v for x in ("BULLISH", "UPTREND", "BUYERS", "ABOVE", "ACCUMUL", "CROSS UP", "OVERSOLD", "STRONG", "BUY")):
                        return "background-color:#0a2e18;color:#00e5a0;font-weight:700"
                    if any(x in v for x in ("BEARISH", "DOWNTREND", "SELLERS", "BELOW", "DISTRIBUT", "CROSS DOWN", "OVERBOUGHT", "WEAK", "SELL", "EXTENDED")):
                        return "background-color:#2e0a18;color:#ff4d6a;font-weight:700"
                    if "MIXED" in v: return "color:#f0c040"
                    return "color:#6b7099"

                def _color_grade(val):
                    g = {"A+": "background-color:#0a2e18;color:#00e5a0;font-weight:700",
                         "A":  "color:#00e5a0;font-weight:600",
                         "B":  "color:#4d9fff",
                         "C":  "color:#f0c040"}
                    return g.get(val, "color:#6b7099")

                def _color_rsi(val):
                    try:
                        v = float(val)
                        if v <= 30: return "background-color:#0a2e18;color:#00e5a0;font-weight:700"
                        if v >= 70: return "background-color:#2e0a18;color:#ff4d6a;font-weight:700"
                        if v >= 60: return "color:#4d9fff"
                        if v <= 40: return "color:#a78bfa"
                    except: pass
                    return "color:#6b7099"

                def _color_wein(val):
                    try:
                        v = int(val)
                        if v >= 5: return "color:#00e5a0;font-weight:700"
                        if v >= 3: return "color:#4d9fff"
                    except: pass
                    return "color:#6b7099"

                def _color_bool(val):
                    if val is True or val == "True": return "color:#00e5a0;font-weight:700"
                    if val is False or val == "False": return "color:#6b7099"
                    return ""

                bias_cols   = [c for c in ["Bias (W+D)", "W Bias", "D Bias", "W EMA", "D EMA",
                                            "W Conv", "D Conv", "Vol Trend", "MACD", "RSI State",
                                            "BB Pos", "W Fib", "D Fib",
                                            "vs MA20", "vs MA50", "vs MA200"] if c in df.columns]
                bool_cols   = [c for c in ["MACD↑", "MACD↓", "Golden✓", "Death✗", "BB Sq",
                                            "Bull Div", "Bear Div"] if c in df.columns]
                rsi_cols    = [c for c in ["D RSI", "W RSI"] if c in df.columns]
                wein_cols   = [c for c in ["W Wein", "D Wein"] if c in df.columns]

                styler = df.style
                if bias_cols:  styler = styler.applymap(_color_bias,  subset=bias_cols)
                if "Grade" in df.columns: styler = styler.applymap(_color_grade, subset=["Grade"])
                if rsi_cols:   styler = styler.applymap(_color_rsi,   subset=rsi_cols)
                if wein_cols:  styler = styler.applymap(_color_wein,  subset=wein_cols)
                if bool_cols:  styler = styler.applymap(_color_bool,  subset=bool_cols)
                return styler

            # ── Sort helper ──
            def _sorted(df_sub):
                if "Score" in df_sub.columns:
                    return df_sub.sort_values("Score", ascending=False)
                return df_sub

            # ── Render helper ──
            def _tos_render_table(df_sub, label, color, empty_msg, tab_container, dl_key=None):
                with tab_container:
                    if df_sub.empty:
                        st.info(empty_msg)
                        return
                    st.markdown(
                        f'<div style="border-left:4px solid {color};padding:4px 12px;margin-bottom:8px">'
                        f'<span style="color:{color};font-weight:700;font-size:14px">{label} ({len(df_sub)})</span></div>',
                        unsafe_allow_html=True,
                    )
                    cols = [c for c in _tos_display_cols if c in df_sub.columns]
                    show = _sorted(df_sub[cols].rename(columns=_tos_col_rename))
                    st.dataframe(_tos_style(show), use_container_width=True,
                                 height=min(40 * len(show) + 40, 650))
                    if dl_key:
                        st.download_button("📥 Download CSV", show.to_csv(index=False),
                            file_name=f"tos_scan_{date.today()}.csv",
                            mime="text/csv", use_container_width=True, key=dl_key)

            # ── Sub-tabs ──
            if not _tos_df.empty:
                _aligned_bull = _tos_df[_tos_df["combined_bias"] == "BULLISH ALIGNED"]
                _aligned_bear = _tos_df[_tos_df["combined_bias"] == "BEARISH ALIGNED"]
                _aligned_all  = _tos_df[_tos_df["combined_bias"].isin(["BULLISH ALIGNED", "BEARISH ALIGNED"])]
                _bb_squeeze   = _tos_df[_tos_df.get("bb_squeeze", pd.Series(False, index=_tos_df.index)) == True] if "bb_squeeze" in _tos_df.columns else pd.DataFrame()
                _grade_a      = _tos_df[_tos_df["signal_grade"].isin(["A+", "A"])] if "signal_grade" in _tos_df.columns else pd.DataFrame()

                _t_aligned, _t_bull, _t_bear, _t_grade, _t_squeeze, _t_all = st.tabs([
                    f"🎯 Aligned ({len(_aligned_all)})",
                    f"🟢 Bull ({len(_aligned_bull)})",
                    f"🔴 Bear ({len(_aligned_bear)})",
                    f"⭐ A/A+ ({len(_grade_a)})",
                    f"⚡ BB Squeeze ({len(_bb_squeeze)})",
                    f"📊 All ({len(_tos_df)})",
                ])

                _tos_render_table(_aligned_all,  "🎯 All Aligned Setups", "#a78bfa", "No aligned setups.", _t_aligned)
                _tos_render_table(_aligned_bull, "🟢 Bullish Aligned",    "#00e5a0", "No bullish setups.", _t_bull)
                _tos_render_table(_aligned_bear, "🔴 Bearish Aligned",    "#ff4d6a", "No bearish setups.", _t_bear)
                _tos_render_table(_grade_a,      "⭐ Grade A / A+",       "#ffe066", "No A/A+ grades.",    _t_grade)
                _tos_render_table(_bb_squeeze,   "⚡ Bollinger Squeeze",  "#f0c040", "No BB squeezes.",    _t_squeeze)
                _tos_render_table(_tos_df,       "📊 All Results",        "#6b7099", "No results.",        _t_all, dl_key="tos_dl_all")

            else:
                st.info("No results match the current filters. Adjust the Scan Conditions above.")

            # ══════════════════════════════════════════════════════════════════
            # DETAILED VIEW
            # ══════════════════════════════════════════════════════════════════
            with st.expander("🔍 Detailed Ticker View", expanded=False):
                _det_options = [r["ticker"] for r in _tos_raw]
                _sel_tkr = st.selectbox("Select ticker", _det_options, key="tos_detail_sel")
                _sel_r = next((r for r in _tos_raw if r["ticker"] == _sel_tkr), None)
                if _sel_r:
                    _grade_color = {"A+": "#00e5a0", "A": "#00e5a0", "B": "#4d9fff", "C": "#f0c040"}.get(_sel_r.get("signal_grade", "D"), "#6b7099")
                    st.markdown(
                        f'<div style="background:#0d0f17;border:1px solid #1a1d2e;border-radius:6px;'
                        f'padding:12px 16px;margin-bottom:12px;display:flex;gap:24px;align-items:center">'
                        f'<span style="font-size:20px;font-weight:700;color:#e8ecff">{_sel_r["ticker"]}</span>'
                        f'<span style="color:#6b7099;font-size:14px">${_sel_r["price"]:.2f}</span>'
                        f'<span style="color:{_grade_color};font-size:16px;font-weight:700">'
                        f'Grade {_sel_r.get("signal_grade","?")} · Score {_sel_r.get("signal_score",0)}</span>'
                        f'<span style="color:#a78bfa;font-size:13px">{_sel_r.get("combined_bias","")}</span>'
                        f'</div>',
                        unsafe_allow_html=True,
                    )

                    _dc1, _dc2, _dc3 = st.columns(3)

                    with _dc1:
                        st.markdown("**📅 Weekly**")
                        st.markdown(f"- Bias: **{_sel_r.get('w_bias','')}** (str: {_sel_r.get('w_bias_strength',0)}%, Q: {_sel_r.get('w_bias_quality',0)}/3)")
                        st.markdown(f"- RSI: **{_sel_r.get('w_rsi',0)}** — {_sel_r.get('w_rsi_label','')}")
                        st.markdown(f"- MACD: **{_sel_r.get('w_macd_label','')}**" + (" ↑Cross" if _sel_r.get("w_macd_cross_up") else "") + (" ↓Cross" if _sel_r.get("w_macd_cross_down") else ""))
                        st.markdown(f"- EMA Trend: **{_sel_r.get('w_ema_trend','')}**")
                        st.markdown(f"- Fib Zone: **{_sel_r.get('w_fib_zone','')}** ({_sel_r.get('w_fib_position',0)}%)")
                        st.markdown(f"- Conviction: **{_sel_r.get('w_conviction','')}**")
                        st.markdown(f"- Vol Trend: **{_sel_r.get('w_vol_trend','')}**")
                        st.markdown(f"- Weinstein: **{_sel_r.get('w_weinstein_score',0)}/6** — {_sel_r.get('w_weinstein_phase','')}")
                        st.markdown(f"- Momentum: **{_sel_r.get('w_momentum','')}**")
                        if _sel_r.get("w_details"):
                            wd = _sel_r["w_details"]
                            checks = ["✅ MA30↑" if wd.get("ma30_curling") else "❌ MA30↑",
                                      "✅ MA10>MA30" if wd.get("ma10_above_ma30") else "❌ MA10>MA30",
                                      "✅ RS>0" if wd.get("rs_positive") else "❌ RS>0",
                                      "✅ RS↑" if wd.get("rs_improving") else "❌ RS↑",
                                      "✅ Vol↑" if wd.get("vol_building") else "❌ Vol↑",
                                      "✅ 52W High" if wd.get("near_52w_high") else "❌ 52W High"]
                            st.caption(" · ".join(checks))

                    with _dc2:
                        st.markdown("**📈 Daily**")
                        st.markdown(f"- Bias: **{_sel_r.get('d_bias','')}** (str: {_sel_r.get('d_bias_strength',0)}%, Q: {_sel_r.get('d_bias_quality',0)}/3)")
                        st.markdown(f"- RSI: **{_sel_r.get('d_rsi',0)}** — {_sel_r.get('d_rsi_label','')}")
                        st.markdown(f"- MACD: **{_sel_r.get('d_macd_label','')}**" + (" ↑CROSS" if _sel_r.get("d_macd_cross_up") else "") + (" ↓CROSS" if _sel_r.get("d_macd_cross_down") else ""))
                        st.markdown(f"- EMA Trend: **{_sel_r.get('d_ema_trend','')}**")
                        st.markdown(f"- Fib Zone: **{_sel_r.get('d_fib_zone','')}** ({_sel_r.get('d_fib_position',0)}%)")
                        st.markdown(f"- Conviction: **{_sel_r.get('d_conviction','')}**")
                        st.markdown(f"- Vol Trend: **{_sel_r.get('d_vol_trend','')}** ({_sel_r.get('d_vol_ratio',1)}x)")
                        st.markdown(f"- Weinstein: **{_sel_r.get('d_weinstein_score',0)}/6** — {_sel_r.get('d_weinstein_phase','')}")
                        st.markdown(f"- Momentum: **{_sel_r.get('d_momentum','')}** (ROC: {_sel_r.get('d_roc',0)}%)")
                        st.markdown(f"- Volatility: **{_sel_r.get('d_volatility_label','')}** ({_sel_r.get('d_volatility',0)}%)")
                        _flags = []
                        if _sel_r.get("d_bull_div"): _flags.append("🟢 Bull Divergence")
                        if _sel_r.get("d_bear_div"): _flags.append("🔴 Bear Divergence")
                        if _sel_r.get("d_bull_fvg_count"): _flags.append(f"🟢 {_sel_r['d_bull_fvg_count']} Bull FVG")
                        if _sel_r.get("d_bear_fvg_count"): _flags.append(f"🔴 {_sel_r['d_bear_fvg_count']} Bear FVG")
                        if _flags: st.markdown("**Patterns:** " + " · ".join(_flags))
                        if _sel_r.get("d_details"):
                            dd = _sel_r["d_details"]
                            checks = ["✅ MA30↑" if dd.get("ma30_curling") else "❌ MA30↑",
                                      "✅ MA10>MA30" if dd.get("ma10_above_ma30") else "❌ MA10>MA30",
                                      "✅ RS>0" if dd.get("rs_positive") else "❌ RS>0",
                                      "✅ RS↑" if dd.get("rs_improving") else "❌ RS↑",
                                      "✅ Vol↑" if dd.get("vol_building") else "❌ Vol↑",
                                      "✅ 52W High" if dd.get("near_52w_high") else "❌ 52W High"]
                            st.caption(" · ".join(checks))

                    with _dc3:
                        st.markdown("**📐 Technicals**")
                        st.markdown(f"- vs MA20: **{_sel_r.get('price_vs_ma20','')}** (${_sel_r.get('ma20','N/A')})")
                        st.markdown(f"- vs MA50: **{_sel_r.get('price_vs_ma50','')}** (${_sel_r.get('ma50','N/A')})")
                        st.markdown(f"- vs MA200: **{_sel_r.get('price_vs_ma200','')}** (${_sel_r.get('ma200','N/A')})")
                        if _sel_r.get("golden_cross"): st.markdown("- **Golden Cross** ✅ (MA50 > MA200)")
                        if _sel_r.get("death_cross"):  st.markdown("- **Death Cross** ❌ (MA50 < MA200)")
                        st.markdown("---")
                        st.markdown(f"- BB Upper: **${_sel_r.get('bb_upper','?')}** · Mid: **${_sel_r.get('bb_mid','?')}** · Lower: **${_sel_r.get('bb_lower','?')}**")
                        st.markdown(f"- BB Width: **{_sel_r.get('bb_bandwidth',0):.1f}%** · %B: **{_sel_r.get('bb_pct_b',0.5):.2f}**")
                        if _sel_r.get("bb_squeeze"):
                            st.markdown("- ⚡ **Bollinger Squeeze Active** — breakout imminent")
                        st.markdown(f"- BB Position: **{_sel_r.get('bb_position','')}**")

  except Exception as _tos_err:
    st.error(f"TOS Scanner error: {_tos_err}")
    import traceback; st.code(traceback.format_exc())

# ╔══════════════════════════════════════════════════════════════════════════════╗
# ║ TAB: BUBBLE DETECTION                                                       ║
# ╚══════════════════════════════════════════════════════════════════════════════╝
with tab_bubble:
  try:
    st.markdown("## Bubble Detection & Risk Analysis")
    st.caption("Detects bubble-like price behavior: overvaluation vs MAs, rapid returns, high volatility, composite risk score.")

    if not BUBBLE_SCANNER_AVAILABLE:
        st.error("bubble_scanner.py not found. Place it in the same folder as stock_pulse.py.")
    else:
        _bb_col1, _bb_col2, _bb_col3, _bb_col4 = st.columns([3, 1, 1, 1])
        with _bb_col1:
            _bb_raw = st.text_input(
                "Ticker(s) - comma-separated",
                value="NVDA, TSLA, AAPL, BTC-USD",
                key="bb_tickers",
            ).upper().strip()
            _bb_tickers = [t.strip() for t in _bb_raw.split(",") if t.strip()]
        with _bb_col2:
            _bb_period = st.selectbox("Period", ["1y", "2y", "3y", "5y"], index=2, key="bb_period")
        with _bb_col3:
            _bb_backdate = st.date_input(
                "As-Of Date (backdate)",
                value=date.today(),
                key="bb_backdate",
            )
        with _bb_col4:
            st.markdown("<br>", unsafe_allow_html=True)
            _bb_scan_btn = st.button("Scan", use_container_width=True, key="bb_scan_btn")
        _bb_as_of = _bb_backdate if _bb_backdate < date.today() else None

        if _bb_scan_btn and _bb_tickers:
            _bb_bar = st.progress(0, text="Scanning...")
            _bb_results = []
            _bb_executor = get_thread_pool()
            _bb_futures = {
                _bb_executor.submit(bubble_analyze, _bt, _bb_period, as_of_date=_bb_as_of): _bt
                for _bt in _bb_tickers
            }
            _bb_done = 0
            _bb_total = len(_bb_tickers)
            for _fut in concurrent.futures.as_completed(_bb_futures):
                _bt = _bb_futures[_fut]
                _bb_done += 1
                _bb_bar.progress(_bb_done / _bb_total, text=f"Analyzing {_bt} ({_bb_done}/{_bb_total})...")
                try:
                    _br = _fut.result()
                    if _br:
                        _bb_results.append(_br)
                except Exception as _be:
                    st.warning(f"{_bt}: {_be}")
            _bb_bar.empty()
            trim_memory()
            if _bb_results:
                st.session_state["_bb_results"] = _bb_results

        _bb_data = st.session_state.get("_bb_results", [])
        if _bb_data:
            # ── Summary table ──
            _bb_summary = []
            for r in _bb_data:
                _bb_summary.append({
                    "Ticker": r["ticker"],
                    "Price": f"${r['price']:.2f}",
                    "Trend": r["trend"],
                    "Risk": r["risk_label"],
                    "Risk Score": r["risk_score"],
                    "vs SMA50": f"{r['overval_50']:+.1f}%",
                    "vs SMA200": f"{r['overval_200']:+.1f}%",
                    "20D Ret": f"{r['ret_20d']:+.1f}%",
                    "50D Ret": f"{r['ret_50d']:+.1f}%",
                    "Volatility": f"{r['volatility']:.1f}%",
                    "Bubble Periods": r["bubble_periods"],
                    "Peak Overval": f"{r['max_overval']:.1f}%",
                    "Peak 20D Ret": f"{r['max_ret_20d']:.1f}%",
                })
            _bb_sdf = pd.DataFrame(_bb_summary)

            def _bb_style_risk(val):
                if val == "HIGH":
                    return "background-color: #3d0a1a; color: #ff4d6a; font-weight: 700"
                elif val == "MEDIUM":
                    return "background-color: #3d2a0a; color: #f0c040; font-weight: 700"
                elif val == "ELEVATED":
                    return "color: #f0c040"
                return "color: #00e5a0"

            def _bb_style_trend(val):
                if val == "UPTREND":
                    return "background-color: #0a3d1f; color: #00e5a0; font-weight: 700"
                elif val == "DOWNTREND":
                    return "background-color: #3d0a1a; color: #ff4d6a; font-weight: 700"
                elif val == "RECOVERING":
                    return "color: #4d9fff"
                return "color: #f0c040"

            _bb_styler = _bb_sdf.style
            if "Risk" in _bb_sdf.columns:
                _bb_styler = _bb_styler.applymap(_bb_style_risk, subset=["Risk"])
            if "Trend" in _bb_sdf.columns:
                _bb_styler = _bb_styler.applymap(_bb_style_trend, subset=["Trend"])

            st.dataframe(_bb_styler, use_container_width=True, height=min(40 * len(_bb_sdf) + 38, 400))

            # ── Per-ticker charts ──
            _bb_sel = st.selectbox("Select ticker for chart", [r["ticker"] for r in _bb_data], key="bb_chart_sel")
            _bb_r = next((r for r in _bb_data if r["ticker"] == _bb_sel), None)

            if _bb_r and "_df" in _bb_r:
                _bdf = _bb_r["_df"]
                _bperiods = _bb_r.get("_periods", [])

                import plotly.graph_objects as go
                from plotly.subplots import make_subplots

                _bfig = make_subplots(
                    rows=3, cols=1,
                    subplot_titles=[
                        f"{_bb_sel}: Price with Bubble Periods",
                        f"{_bb_sel}: Price / SMA Ratios",
                        f"{_bb_sel}: Bubble Risk Score",
                    ],
                    vertical_spacing=0.08,
                    row_heights=[0.4, 0.3, 0.3],
                )

                # Row 1: Price + SMAs + bubble highlights
                _bfig.add_trace(go.Scatter(
                    x=_bdf.index, y=_bdf["Close"], mode="lines",
                    name="Price", line=dict(color="#4d9fff", width=2),
                ), row=1, col=1)

                _bfig.add_trace(go.Scatter(
                    x=_bdf.index, y=_bdf["SMA_50"], mode="lines",
                    name="SMA 50", line=dict(color="#f0c040", width=1, dash="dash"),
                ), row=1, col=1)

                _bfig.add_trace(go.Scatter(
                    x=_bdf.index, y=_bdf["SMA_200"], mode="lines",
                    name="SMA 200", line=dict(color="#00e5a0", width=1, dash="dash"),
                ), row=1, col=1)

                for _bs, _be_dt in _bperiods:
                    _bseg = _bdf.loc[_bs:_be_dt]
                    if not _bseg.empty:
                        _bfig.add_trace(go.Scatter(
                            x=_bseg.index, y=_bseg["Close"], mode="lines",
                            name="Bubble", line=dict(color="#ff4d6a", width=4),
                            opacity=0.9, showlegend=False,
                        ), row=1, col=1)

                # Row 2: Price/SMA ratios
                _bfig.add_trace(go.Scatter(
                    x=_bdf.index, y=_bdf["Price_SMA50_Ratio"], mode="lines",
                    name="Price/SMA50", line=dict(color="#ff4d6a", width=2),
                ), row=2, col=1)

                _bfig.add_trace(go.Scatter(
                    x=_bdf.index, y=_bdf["Price_SMA200_Ratio"], mode="lines",
                    name="Price/SMA200", line=dict(color="#4d9fff", width=2),
                ), row=2, col=1)

                _bfig.add_hline(y=1.3, line_dash="dash", line_color="#ff4d6a",
                    annotation_text="Overvaluation (1.3x)", row=2, col=1, opacity=0.7)
                _bfig.add_hline(y=1.0, line_dash="dot", line_color="#6b7099",
                    row=2, col=1, opacity=0.5)

                # Row 3: Risk score
                _bfig.add_trace(go.Scatter(
                    x=_bdf.index, y=_bdf["Bubble_Risk_Score"], mode="lines",
                    name="Risk Score", line=dict(color="#ff4d6a", width=2),
                    fill="tozeroy", fillcolor="rgba(255,77,106,0.1)",
                ), row=3, col=1)

                _bfig.add_hline(y=80, line_dash="dash", line_color="#ff4d6a",
                    annotation_text="High Risk", row=3, col=1, opacity=0.7)
                _bfig.add_hline(y=50, line_dash="dash", line_color="#f0c040",
                    annotation_text="Medium Risk", row=3, col=1, opacity=0.5)

                _bfig.update_layout(
                    height=800,
                    template="plotly_dark",
                    paper_bgcolor="#0d0f17",
                    plot_bgcolor="#0d0f17",
                    showlegend=True,
                    legend=dict(orientation="h", yanchor="bottom", y=1.02),
                    margin=dict(l=50, r=20, t=30, b=30),
                )

                st.plotly_chart(_bfig, use_container_width=True)

                # ── Bubble period details ──
                _bpd = _bb_r.get("bubble_period_details", [])
                if _bpd:
                    st.markdown(f"**Bubble periods detected: {len(_bpd)}**")
                    for _i, _bp in enumerate(_bpd, 1):
                        st.caption(f"Period {_i}: {_bp['start']} to {_bp['end']} ({_bp['days']} days)")
                else:
                    st.info("No bubble periods detected for this ticker.")

                # ── Current assessment ──
                _rc = _bb_r["risk_label"]
                _rc_color = "#ff4d6a" if _rc == "HIGH" else "#f0c040" if _rc in ("MEDIUM", "ELEVATED") else "#00e5a0"
                st.markdown(
                    f'<div style="background:#0d0f17;border:1px solid #1a1d2e;padding:12px;border-radius:6px;margin-top:12px">'
                    f'<span style="color:{_rc_color};font-weight:700;font-size:16px">Current Risk: {_rc}</span><br>'
                    f'<span style="color:#6b7099;font-size:12px">'
                    f'Risk Score: {_bb_r["risk_score"]} | '
                    f'vs SMA50: {_bb_r["overval_50"]:+.1f}% | '
                    f'vs SMA200: {_bb_r["overval_200"]:+.1f}% | '
                    f'20D Return: {_bb_r["ret_20d"]:+.1f}% | '
                    f'Volatility: {_bb_r["volatility"]:.1f}%</span></div>',
                    unsafe_allow_html=True,
                )

            # ── Download ──
            st.download_button(
                "Download Summary CSV",
                _bb_sdf.to_csv(index=False),
                file_name=f"bubble_scan_{date.today()}.csv",
                mime="text/csv",
                use_container_width=True,
                key="bb_dl",
            )
  except Exception as _bb_err:
    st.error(f"Bubble Detection error: {_bb_err}")
    import traceback; st.code(traceback.format_exc())
