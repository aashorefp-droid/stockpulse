"""
alpaca_paper.py — Alpaca Paper Trading engine for replay-session backtesting.

Tracks simulated paper trades from tracking_trades in replay sessions.
Entry conditions, exit rules, and position sizing are configured in paper_config.py.

Usage:
    from alpaca_paper import PaperTrader
    pt = PaperTrader()
    eligible, reason = pt.check_entry_conditions(track_row)
    pt.open_trade(ticker, direction, entry, stop, t1, t2, date, scenario)
    pt.check_exits(ticker, current_price)
    trades = pt.get_all_trades()
"""

import os
import re
import sqlite3
import requests
from datetime import datetime, date
from config import (
    ALPACA_PAPER_API_KEY,
    ALPACA_PAPER_API_SECRET,
    ALPACA_PAPER_BASE_URL,
)
import paper_config as pc

_DB_PATH = os.path.join(os.path.dirname(__file__), "paper_trades.db")

_CREATE_TABLE_SQL = """
    CREATE TABLE IF NOT EXISTS paper_trades (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        trade_date  TEXT NOT NULL,
        ticker      TEXT NOT NULL,
        direction   TEXT NOT NULL,
        entry_price REAL NOT NULL,
        stop_price  REAL NOT NULL,
        t1_price    REAL NOT NULL,
        t2_price    REAL NOT NULL,
        exit_price  REAL,
        shares      INTEGER NOT NULL,
        status      TEXT NOT NULL DEFAULT 'OPEN',
        outcome     TEXT,
        pnl_dollars REAL,
        pnl_pct     REAL,
        scenario    TEXT,
        confidence  TEXT,
        entry_time  TEXT,
        exit_time   TEXT,
        exit_reason TEXT,
        alpaca_order_id TEXT,
        created_at  TEXT DEFAULT (datetime('now'))
    )
"""

_CREATE_INDEXES_SQL = [
    "CREATE INDEX IF NOT EXISTS idx_pt_status ON paper_trades(status)",
    "CREATE INDEX IF NOT EXISTS idx_pt_created_at ON paper_trades(created_at DESC)",
    "CREATE INDEX IF NOT EXISTS idx_pt_trade_date ON paper_trades(trade_date DESC)",
    "CREATE INDEX IF NOT EXISTS idx_pt_ticker_status ON paper_trades(ticker, status)",
    "CREATE INDEX IF NOT EXISTS idx_pt_exit_time ON paper_trades(exit_time DESC)",
]


def _get_db(in_memory=False):
    if in_memory:
        conn = sqlite3.connect(":memory:", check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute(_CREATE_TABLE_SQL)
        for idx_sql in _CREATE_INDEXES_SQL:
            conn.execute(idx_sql)
        # Load existing data from disk into memory
        try:
            disk = sqlite3.connect(_DB_PATH)
            disk.backup(conn)
            disk.close()
        except Exception:
            pass  # fresh start if disk DB doesn't exist yet
        conn.row_factory = sqlite3.Row  # re-set after backup overwrites
        conn.commit()
        return conn
    conn = sqlite3.connect(_DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA cache_size=-500")
    conn.execute(_CREATE_TABLE_SQL)
    for idx_sql in _CREATE_INDEXES_SQL:
        conn.execute(idx_sql)
    conn.commit()
    return conn


def _alpaca_headers():
    return {
        "APCA-API-KEY-ID": ALPACA_PAPER_API_KEY,
        "APCA-API-SECRET-KEY": ALPACA_PAPER_API_SECRET,
        "Content-Type": "application/json",
    }


def _alpaca_enabled():
    return bool(ALPACA_PAPER_API_KEY and ALPACA_PAPER_API_SECRET)


class PaperTrader:
    def __init__(self, position_size=None, in_memory=False):
        self.position_size = position_size or pc.POSITION_SIZE
        self._in_memory = in_memory
        self.db = _get_db(in_memory=in_memory)

    def close(self):
        """Close SQLite database connection to prevent memory leak."""
        if hasattr(self, "db") and self.db is not None:
            try:
                self.flush_to_disk()
                self.db.close()
            except Exception:
                pass
            self.db = None

    def __del__(self):
        try:
            self.close()
        except Exception:
            pass

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()

    def flush_to_disk(self):
        """Write in-memory database back to disk. No-op if already on disk."""
        if not self._in_memory:
            return
        try:
            disk = sqlite3.connect(_DB_PATH)
            self.db.backup(disk)
            disk.close()
        except Exception as e:
            print(f"⚠️ Paper flush_to_disk error: {e}")

    # ── Entry condition checks ─────────────────────────────────────────────

    def check_entry_conditions(self, track_row):
        """
        Evaluate a tracking-table row dict against paper_config entry filters.
        Returns (eligible: bool, reason: str).
        track_row keys: Ticker, Dir, Action, Scen, Live $, RVOL, VWAP, Prev VWAP,
                        V-Flow, 10m, 30m, 4H, Align, News, Entry, Stop, T1, T2,
                        RR(T1), RR(T2), Best RR, H-ATR, OHLC, Conf, P&L
        """
        reasons = []

        # Max open trades
        open_count = len(self.get_open_trades())
        if open_count >= pc.MAX_OPEN_TRADES:
            return False, f"Max open trades ({pc.MAX_OPEN_TRADES}) reached"

        ticker = track_row.get("Ticker", "")
        # Max per ticker
        open_for_ticker = self.db.execute(
            "SELECT COUNT(*) as c FROM paper_trades WHERE ticker=? AND status='OPEN'",
            (ticker,),
        ).fetchone()["c"]
        if open_for_ticker >= pc.MAX_TRADES_PER_TICKER:
            return False, f"Already {open_for_ticker} open trade(s) for {ticker}"

        # Direction filter
        direction = track_row.get("Dir", "").strip().upper()
        if "LONG" in direction and not pc.ALLOW_LONG:
            return False, "LONG trades disabled in config"
        if "SHORT" in direction and not pc.ALLOW_SHORT:
            return False, "SHORT trades disabled in config"

        # Scenario filter
        scen = track_row.get("Scen", "").strip().upper()
        if pc.ALLOWED_SCENARIOS:
            # Normalize scenario: "8:30 CONF" -> "8:30", "9:00 CONF" -> "9:00"
            scen_match = scen
            if scen.startswith("8:30"):
                scen_match = "8:30"
            elif scen.startswith("9:00"):
                scen_match = "9:00"
            if scen_match not in pc.ALLOWED_SCENARIOS:
                reasons.append(f"Scenario '{scen}' not in {pc.ALLOWED_SCENARIOS}")

        # ── Scenario-specific entry rules ───────────────────────────
        _scen_rules = getattr(pc, "SCENARIO_ENTRY_RULES", None)
        if _scen_rules and scen in _scen_rules:
            _rule = _scen_rules[scen]
            _dir_clean = "LONG" if "LONG" in direction else "SHORT"

            # Nested per-direction rules: {"GAP": {"LONG": ..., "SHORT": ...}}
            if isinstance(_rule, dict) and _dir_clean in _rule:
                _rule = _rule[_dir_clean]

            # Normalize to dict form
            if isinstance(_rule, str):
                _rule = {"action": _rule}

            _action = (_rule.get("action") or "ENTER").upper()

            if _action == "SKIP":
                return False, f"Scenario rule: {scen}/{_dir_clean} set to SKIP"

            elif _action == "WAIT_FLIP":
                # Only enter if direction was flipped (🔄 prefix in Dir column)
                _dir_raw = track_row.get("Dir", "")
                if "🔄" not in _dir_raw:
                    reasons.append(f"Scenario rule: {scen}/{_dir_clean} requires bias flip (WAIT_FLIP)")

            elif _action == "WAIT_STOP_ZONE":
                # Only enter when price is within stop_zone_pct of the stop level
                _zone_pct = _rule.get("stop_zone_pct", 0.5)
                _live_str = track_row.get("Live $", "")
                _stop_str = track_row.get("Stop", "")
                _live_m = re.search(r'([\d.]+)', str(_live_str))
                _stop_m = re.search(r'([\d.]+)', str(_stop_str))
                if _live_m and _stop_m:
                    _live_px = float(_live_m.group(1))
                    _stop_px = float(_stop_m.group(1))
                    if _stop_px > 0:
                        _dist_pct = abs(_live_px - _stop_px) / _stop_px * 100
                        if _dist_pct > _zone_pct:
                            reasons.append(
                                f"Scenario rule: {scen} WAIT_STOP_ZONE — "
                                f"price ${_live_px:.2f} is {_dist_pct:.1f}% from stop ${_stop_px:.2f} "
                                f"(max {_zone_pct}%)"
                            )
                # "ENTER" — no extra conditions

        # Alignment filter
        align = track_row.get("Align", "").strip()
        if pc.REQUIRE_ALIGNMENT == "CONFIRMED":
            if "✅" not in align:
                reasons.append("Alignment not CONFIRMED")
        elif pc.REQUIRE_ALIGNMENT == "ANY":
            pass  # accept anything

        # V-Flow filter
        vflow = track_row.get("V-Flow", "").strip()
        if pc.REQUIRE_VFLOW:
            if vflow == "✅":
                pass  # perfect
            elif vflow == "🟡" and pc.ALLOW_PARTIAL_VFLOW:
                pass  # acceptable
            else:
                reasons.append(f"V-Flow not aligned ({vflow})")

        # RVOL filter
        if pc.MIN_RVOL is not None:
            rvol_str = track_row.get("RVOL", "")
            rvol_match = re.search(r'([\d.]+)', rvol_str)
            rvol_val = float(rvol_match.group(1)) if rvol_match else 0.0
            if rvol_val < pc.MIN_RVOL:
                reasons.append(f"RVOL {rvol_val:.1f}x < min {pc.MIN_RVOL}x")

        # VWAP aligned filter
        if pc.REQUIRE_VWAP_ALIGNED:
            vwap_str = track_row.get("VWAP", "").upper()
            direction = track_row.get("Dir", "").upper()
            is_long = "LONG" in direction
            if is_long and not vwap_str.startswith("A"):
                reasons.append("LONG but below VWAP")
            elif not is_long and "SHORT" in direction and not vwap_str.startswith("B"):
                reasons.append("SHORT but above VWAP")

        # News filter
        news = track_row.get("News", "").strip()
        if pc.NEWS_FILTER == "GOOD_ONLY" and "🟢" not in news:
            reasons.append("News not Good")
        elif pc.NEWS_FILTER == "NO_BAD" and "🔴" in news:
            reasons.append("Bad news sentiment")

        # OHLC filter
        if pc.REQUIRE_OHLC_ALIGNED:
            ohlc = track_row.get("OHLC", "").upper()
            direction = track_row.get("Dir", "").upper()
            if "LONG" in direction and "BULL" not in ohlc:
                reasons.append("OHLC not BULL for LONG")
            elif "SHORT" in direction and "BEAR" not in ohlc:
                reasons.append("OHLC not BEAR for SHORT")

        # Confidence filter
        if pc.MIN_CONFIDENCE:
            conf = track_row.get("Conf", "N/A").upper()
            min_rank = pc.CONFIDENCE_PRIORITY.get(pc.MIN_CONFIDENCE, 999)
            conf_rank = pc.CONFIDENCE_PRIORITY.get(conf, 999)
            if conf_rank > min_rank:
                reasons.append(f"Confidence {conf} below min {pc.MIN_CONFIDENCE}")

        # RR filter
        def _parse_rr(s):
            m = re.search(r'([\d.]+)', str(s))
            return float(m.group(1)) if m else 0.0

        if pc.MIN_RR_T1 is not None:
            rr_t1 = _parse_rr(track_row.get("RR(T1)", "0"))
            if rr_t1 < pc.MIN_RR_T1:
                reasons.append(f"RR(T1) {rr_t1:.2f}x < min {pc.MIN_RR_T1}x")

        if pc.MIN_BEST_RR is not None:
            best_rr = _parse_rr(track_row.get("Best RR", "0"))
            if best_rr < pc.MIN_BEST_RR:
                reasons.append(f"Best RR {best_rr:.2f}x < min {pc.MIN_BEST_RR}x")

        # Pullback entry
        if pc.ENTRY_ON_PULLBACK:
            live_str = track_row.get("Live $", "")
            live_match = re.search(r'([\d.]+)', str(live_str))
            entry_str = track_row.get("Entry", "")
            entry_match = re.search(r'([\d.]+)', str(entry_str))
            if live_match and entry_match:
                live_px = float(live_match.group(1))
                entry_px = float(entry_match.group(1))
                if entry_px > 0:
                    dist_pct = abs(live_px - entry_px) / entry_px * 100
                    direction = track_row.get("Dir", "").upper()
                    # For LONG: price should be at or just above entry (pulled back from higher)
                    # For SHORT: price should be at or just below entry (pulled back from lower)
                    too_far = dist_pct > pc.PULLBACK_MAX_DIST_PCT
                    wrong_side = False
                    if "LONG" in direction and live_px < entry_px * (1 - pc.PULLBACK_MAX_DIST_PCT / 100):
                        wrong_side = True
                    elif "SHORT" in direction and live_px > entry_px * (1 + pc.PULLBACK_MAX_DIST_PCT / 100):
                        wrong_side = True
                    if too_far or wrong_side:
                        reasons.append(f"Pullback not reached (dist {dist_pct:.2f}% vs max {pc.PULLBACK_MAX_DIST_PCT}%)")

        # P&L gate
        if pc.MAX_ENTRY_PNL_PCT is not None:
            pnl_str = track_row.get("P&L", "0%")
            pnl_match = re.search(r'([+-]?[\d.]+)', pnl_str)
            pnl_val = abs(float(pnl_match.group(1))) if pnl_match else 0.0
            if pnl_val > pc.MAX_ENTRY_PNL_PCT:
                reasons.append(f"|P&L| {pnl_val:.1f}% > max {pc.MAX_ENTRY_PNL_PCT}%")

        if reasons:
            return False, "; ".join(reasons)
        return True, "All conditions met"

    def open_trade(self, ticker, direction, entry_price, stop_price, t1_price,
                   t2_price, trade_date, scenario="", confidence="",
                   entry_time=None, submit_to_alpaca=None):
        """Open a new paper trade. Optionally submits to Alpaca paper."""
        if submit_to_alpaca is None:
            submit_to_alpaca = pc.SUBMIT_TO_ALPACA
        # Check for duplicate open trade on same date
        existing = self.db.execute(
            "SELECT id FROM paper_trades WHERE ticker=? AND trade_date=? AND status='OPEN'",
            (ticker, str(trade_date)),
        ).fetchone()
        if existing:
            return {"status": "duplicate", "id": existing["id"]}

        shares = max(1, int(self.position_size / entry_price))

        alpaca_order_id = None
        if submit_to_alpaca and _alpaca_enabled():
            # Determine take-profit price based on config
            tp_price = t1_price if getattr(pc, 'BRACKET_TP_TARGET', 'T1') == 'T1' else t2_price
            alpaca_order_id = self._submit_alpaca_order(
                ticker, direction, shares, entry_price,
                stop_price=stop_price, tp_price=tp_price
            )

        cur = self.db.execute(
            """INSERT INTO paper_trades
               (trade_date, ticker, direction, entry_price, stop_price, t1_price,
                t2_price, shares, status, scenario, confidence, entry_time,
                alpaca_order_id)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'OPEN', ?, ?, ?, ?)""",
            (str(trade_date), ticker, direction, entry_price, stop_price,
             t1_price, t2_price, shares, scenario, confidence,
             entry_time or datetime.now().isoformat(), alpaca_order_id),
        )
        self.db.commit()
        return {"status": "opened", "id": cur.lastrowid, "shares": shares,
                "alpaca_order_id": alpaca_order_id}

    def check_exits(self, ticker, current_price, exit_time=None,
                    alignment=None, vflow=None, best_rr=None):
        """Check if any open trade for ticker hit exit conditions per paper_config.
        Returns list of exit results, or None."""
        rows = self.db.execute(
            "SELECT * FROM paper_trades WHERE ticker=? AND status='OPEN'",
            (ticker,),
        ).fetchall()

        results = []
        for row in rows:
            direction = row["direction"]
            entry = row["entry_price"]
            stop = row["stop_price"]
            t1 = row["t1_price"]
            t2 = row["t2_price"]

            if direction == "LONG":
                hit_stop = current_price <= stop
                hit_t1 = current_price >= t1
                hit_t2 = current_price >= t2
                pnl_pct = (current_price - entry) / entry * 100
            else:
                hit_stop = current_price >= stop
                hit_t1 = current_price <= t1
                hit_t2 = current_price <= t2
                pnl_pct = (entry - current_price) / entry * 100

            reason = None

            # Stop loss
            if pc.EXIT_ON_STOP and hit_stop:
                reason = "STOP_HIT"
            # T2 (check first — if T2 enabled and hit, take it)
            elif pc.EXIT_ON_T2 and hit_t2:
                reason = "T2_HIT"
            # T1
            elif pc.EXIT_ON_T1 and hit_t1:
                reason = "T1_HIT"
            # Best RR
            elif pc.EXIT_ON_BEST_RR and best_rr is not None and best_rr >= pc.MIN_EXIT_BEST_RR:
                reason = f"BEST_RR_{best_rr:.1f}"
            # Max loss
            elif pc.MAX_LOSS_PCT is not None and pnl_pct <= -pc.MAX_LOSS_PCT:
                reason = "MAX_LOSS"
            # Alignment exit
            elif pc.EXIT_ON_DIVERGED and alignment == "DIVERGED":
                reason = "DIVERGED"
            # V-Flow exit
            elif pc.EXIT_ON_VFLOW_AGAINST and vflow == "❌":
                reason = "VFLOW_AGAINST"

            if reason:
                if pc.LOG_ALL_CHECKS:
                    print(f"📄 Paper exit: {ticker} {reason} @ ${current_price:.2f}")
                result = self._close_trade(row, current_price, reason, exit_time)
                results.append(result)
            elif pc.LOG_ALL_CHECKS:
                print(f"📄 Paper check: {ticker} ${current_price:.2f} P&L={pnl_pct:+.1f}% — holding")

        return results if results else None

    def check_session_end(self, current_time_cst, price_getter=None, exit_time=None):
        """Force-close all open trades if session end time reached.
        price_getter: callable(ticker) -> float, returns current price.
        """
        if not pc.EXIT_AT_SESSION_END:
            return []
        h, m = map(int, pc.SESSION_END_TIME_CST.split(":"))
        end_min = h * 60 + m
        now_min = current_time_cst.hour * 60 + current_time_cst.minute
        if now_min < end_min:
            return []

        open_trades = self.get_open_trades()
        results = []
        for tr in open_trades:
            price = price_getter(tr["ticker"]) if price_getter else tr["entry_price"]
            result = self._close_trade(
                tr, price, "SESSION_END", exit_time or current_time_cst.isoformat()
            )
            results.append(result)
        return results

    def force_exit(self, trade_id, exit_price, reason="MANUAL", exit_time=None):
        """Force-close a trade by ID."""
        row = self.db.execute(
            "SELECT * FROM paper_trades WHERE id=? AND status='OPEN'",
            (trade_id,),
        ).fetchone()
        if not row:
            return None
        return self._close_trade(row, exit_price, reason, exit_time)

    def _close_trade(self, row, exit_price, reason, exit_time=None):
        direction = row["direction"]
        entry = row["entry_price"]
        shares = row["shares"]

        if direction == "LONG":
            pnl_dollars = (exit_price - entry) * shares
            pnl_pct = (exit_price - entry) / entry * 100
        else:
            pnl_dollars = (entry - exit_price) * shares
            pnl_pct = (entry - exit_price) / entry * 100

        outcome = "WIN" if pnl_dollars > 0 else ("LOSS" if pnl_dollars < 0 else "FLAT")

        # Close in Alpaca if we have an order
        if row["alpaca_order_id"] and _alpaca_enabled():
            if pc.USE_BRACKET_ORDERS:
                # Cancel the bracket order (and its child legs) first
                self._cancel_alpaca_order(row["alpaca_order_id"])
            # Submit a market order to close the position
            self._submit_alpaca_close(row["ticker"], direction, shares)

        self.db.execute(
            """UPDATE paper_trades SET
               status='CLOSED', exit_price=?, outcome=?,
               pnl_dollars=?, pnl_pct=?, exit_reason=?, exit_time=?
               WHERE id=?""",
            (exit_price, outcome, round(pnl_dollars, 2), round(pnl_pct, 2),
             reason, exit_time or datetime.now().isoformat(), row["id"]),
        )
        self.db.commit()
        return {
            "id": row["id"], "ticker": row["ticker"], "outcome": outcome,
            "pnl_dollars": round(pnl_dollars, 2), "pnl_pct": round(pnl_pct, 2),
            "reason": reason,
        }

    def _submit_alpaca_order(self, ticker, direction, shares, limit_price,
                              stop_price=None, tp_price=None):
        """Submit order to Alpaca paper. Uses bracket order if configured with SL/TP.
        Returns order_id or None."""
        try:
            side = "buy" if direction == "LONG" else "sell"
            payload = {
                "symbol": ticker,
                "qty": shares,
                "side": side,
                "type": "limit",
                "time_in_force": "day",
                "limit_price": str(round(limit_price, 2)),
            }

            # Bracket order: adds native stop loss + take profit legs
            if pc.USE_BRACKET_ORDERS and stop_price and tp_price:
                payload["order_class"] = "bracket"
                payload["take_profit"] = {
                    "limit_price": str(round(tp_price, 2)),
                }
                payload["stop_loss"] = {
                    "stop_price": str(round(stop_price, 2)),
                }

            print(f"\n🔵 [ALPACA ORDER DEBUG] Submitting {direction} order:")
            print(f"   Ticker: {ticker}, Shares: {shares}, Price: ${limit_price:.2f}")
            print(f"   Stop: ${stop_price:.2f}, TP: ${tp_price:.2f}" if stop_price and tp_price else "")
            print(f"   Payload: {payload}")
            print(f"   URL: {ALPACA_PAPER_BASE_URL}/v2/orders")
            
            resp = requests.post(
                f"{ALPACA_PAPER_BASE_URL}/v2/orders",
                headers=_alpaca_headers(),
                json=payload,
                timeout=10,
            )
            
            print(f"   Response Status: {resp.status_code}")
            print(f"   Response Body: {resp.text[:500]}")
            
            if resp.status_code in (200, 201):
                order_id = resp.json().get("id")
                print(f"✅ Order submitted successfully! ID: {order_id}")
                return order_id
            else:
                print(f"⚠️ Alpaca order failed ({resp.status_code}): {resp.text[:500]}")
                return None
        except Exception as e:
            print(f"⚠️ Alpaca order error: {e}")
            import traceback
            traceback.print_exc()
            return None

    def _submit_alpaca_close(self, ticker, direction, shares):
        """Close position in Alpaca paper."""
        try:
            side = "sell" if direction == "LONG" else "buy"
            payload = {
                "symbol": ticker,
                "qty": shares,
                "side": side,
                "type": "market",
                "time_in_force": "day",
            }
            requests.post(
                f"{ALPACA_PAPER_BASE_URL}/v2/orders",
                headers=_alpaca_headers(),
                json=payload,
                timeout=10,
            )
        except Exception as e:
            print(f"⚠️ Alpaca close error: {e}")

    def _cancel_alpaca_order(self, order_id):
        """Cancel an Alpaca order (used to cancel remaining bracket legs)."""
        try:
            resp = requests.delete(
                f"{ALPACA_PAPER_BASE_URL}/v2/orders/{order_id}",
                headers=_alpaca_headers(),
                timeout=10,
            )
            if resp.status_code in (200, 204):
                print(f"✅ Cancelled Alpaca order {order_id}")
            else:
                print(f"⚠️ Cancel order failed ({resp.status_code}): {resp.text[:200]}")
        except Exception as e:
            print(f"⚠️ Cancel order error: {e}")

    # ── Query methods ──────────────────────────────────────────────────────

    def get_open_trades(self):
        rows = self.db.execute(
            "SELECT * FROM paper_trades WHERE status='OPEN' ORDER BY created_at DESC"
        ).fetchall()
        return [dict(r) for r in rows]

    def get_closed_trades(self, limit=100):
        rows = self.db.execute(
            "SELECT * FROM paper_trades WHERE status='CLOSED' ORDER BY exit_time DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [dict(r) for r in rows]

    def get_all_trades(self, limit=200):
        rows = self.db.execute(
            "SELECT * FROM paper_trades ORDER BY created_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [dict(r) for r in rows]

    def get_trades_by_date(self, trade_date):
        rows = self.db.execute(
            "SELECT * FROM paper_trades WHERE trade_date=? ORDER BY created_at ASC",
            (str(trade_date),),
        ).fetchall()
        return [dict(r) for r in rows]

    def get_daily_summary(self, limit=30):
        """Aggregate P&L by date."""
        rows = self.db.execute("""
            SELECT trade_date,
                   COUNT(*) as total_trades,
                   SUM(CASE WHEN outcome='WIN' THEN 1 ELSE 0 END) as wins,
                   SUM(CASE WHEN outcome='LOSS' THEN 1 ELSE 0 END) as losses,
                   SUM(CASE WHEN status='OPEN' THEN 1 ELSE 0 END) as open_trades,
                   ROUND(SUM(CASE WHEN status='CLOSED' THEN pnl_dollars ELSE 0 END), 2) as total_pnl,
                   ROUND(AVG(CASE WHEN status='CLOSED' THEN pnl_pct ELSE NULL END), 2) as avg_pnl_pct
            FROM paper_trades
            GROUP BY trade_date
            ORDER BY trade_date DESC
            LIMIT ?
        """, (limit,)).fetchall()
        return [dict(r) for r in rows]

    def get_overall_stats(self):
        """Overall performance stats."""
        row = self.db.execute("""
            SELECT
                COUNT(*) as total_trades,
                SUM(CASE WHEN status='CLOSED' THEN 1 ELSE 0 END) as closed,
                SUM(CASE WHEN status='OPEN' THEN 1 ELSE 0 END) as open_count,
                SUM(CASE WHEN outcome='WIN' THEN 1 ELSE 0 END) as wins,
                SUM(CASE WHEN outcome='LOSS' THEN 1 ELSE 0 END) as losses,
                ROUND(SUM(CASE WHEN status='CLOSED' THEN pnl_dollars ELSE 0 END), 2) as total_pnl,
                ROUND(AVG(CASE WHEN status='CLOSED' THEN pnl_pct ELSE NULL END), 2) as avg_pnl_pct,
                ROUND(MAX(CASE WHEN status='CLOSED' THEN pnl_dollars ELSE NULL END), 2) as best_trade,
                ROUND(MIN(CASE WHEN status='CLOSED' THEN pnl_dollars ELSE NULL END), 2) as worst_trade
            FROM paper_trades
        """).fetchone()
        r = dict(row) if row else {}
        closed = r.get("closed", 0) or 0
        wins = r.get("wins", 0) or 0
        r["win_rate"] = round(wins / closed * 100, 1) if closed > 0 else 0
        return r

    def get_alpaca_account(self):
        """Fetch Alpaca paper account info."""
        if not _alpaca_enabled():
            return {"error": "Paper trading keys not configured"}
        try:
            resp = requests.get(
                f"{ALPACA_PAPER_BASE_URL}/v2/account",
                headers=_alpaca_headers(),
                timeout=10,
            )
            if resp.status_code == 200:
                return resp.json()
            return {"error": f"HTTP {resp.status_code}"}
        except Exception as e:
            return {"error": str(e)}

    def delete_trade(self, trade_id):
        self.db.execute("DELETE FROM paper_trades WHERE id=?", (trade_id,))
        self.db.commit()

    def clear_all(self):
        self.db.execute("DELETE FROM paper_trades")
        self.db.commit()

    def get_alpaca_positions(self):
        """Fetch open positions from Alpaca paper account."""
        if not _alpaca_enabled():
            return {"error": "Paper trading keys not configured"}
        try:
            resp = requests.get(
                f"{ALPACA_PAPER_BASE_URL}/v2/positions",
                headers=_alpaca_headers(),
                timeout=10,
            )
            if resp.status_code == 200:
                return resp.json()
            return {"error": f"HTTP {resp.status_code}"}
        except Exception as e:
            return {"error": str(e)}

    def get_alpaca_orders(self, status="open", limit=20):
        """Fetch recent orders from Alpaca paper account."""
        if not _alpaca_enabled():
            return {"error": "Paper trading keys not configured"}
        try:
            resp = requests.get(
                f"{ALPACA_PAPER_BASE_URL}/v2/orders",
                headers=_alpaca_headers(),
                params={"status": status, "limit": limit, "direction": "desc"},
                timeout=10,
            )
            if resp.status_code == 200:
                return resp.json()
            return {"error": f"HTTP {resp.status_code}"}
        except Exception as e:
            return {"error": str(e)}
