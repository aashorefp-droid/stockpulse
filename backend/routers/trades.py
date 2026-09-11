import sys, os, sqlite3
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import Optional, List

sys.path.insert(0, r"C:\Users\malla\git\streamlit")

router = APIRouter(prefix="/api/trades", tags=["trades"])
_DB_PATH = r"C:\Users\malla\git\streamlit\stockpulse_trades.db"

def _get_db():
    conn = sqlite3.connect(_DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    return conn

class SaveTradeReq(BaseModel):
    ticker: str
    direction: str
    entry_price: float
    stop_loss: Optional[float] = None
    target1: Optional[float] = None
    target2: Optional[float] = None
    scenario: Optional[str] = None
    confidence: Optional[str] = None
    atr: Optional[float] = None
    notes: Optional[str] = None

class CloseTradeReq(BaseModel):
    trade_id: int
    exit_price: float
    outcome: Optional[str] = None
    notes: Optional[str] = None

@router.get("/open")
def get_open_trades():
    conn = _get_db()
    try:
        rows = conn.execute("SELECT * FROM trades WHERE status = 'OPEN' ORDER BY entry_date DESC").fetchall()
        return {"count": len(rows), "items": [dict(r) for r in rows]}
    except Exception:
        return {"count": 0, "items": []}
    finally:
        conn.close()

@router.get("/closed")
def get_closed_trades():
    conn = _get_db()
    try:
        rows = conn.execute("SELECT * FROM trades WHERE status = 'CLOSED' ORDER BY exit_date DESC").fetchall()
        trades = [dict(r) for r in rows]
        wins = sum(1 for t in trades if (t.get("pnl_dollars") or 0) > 0)
        win_rate = round((wins / len(trades)) * 100, 1) if trades else 0.0
        return {"count": len(trades), "win_rate": win_rate, "items": trades}
    except Exception:
        return {"count": 0, "win_rate": 0.0, "items": []}
    finally:
        conn.close()

@router.post("")
def save_trade(req: SaveTradeReq):
    conn = _get_db()
    try:
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO trades (
                entry_date, ticker, direction, entry_price, stop_loss, target1, target2,
                scenario, confidence, atr, status, notes
            ) VALUES (date('now'), ?, ?, ?, ?, ?, ?, ?, ?, ?, 'OPEN', ?)
        """, (
            req.ticker.upper().strip(), req.direction.upper(), req.entry_price,
            req.stop_loss, req.target1, req.target2, req.scenario, req.confidence,
            req.atr, req.notes
        ))
        conn.commit()
        return {"status": "ok", "trade_id": cur.lastrowid}
    finally:
        conn.close()

@router.post("/close")
def close_trade(req: CloseTradeReq):
    conn = _get_db()
    try:
        trade = conn.execute("SELECT * FROM trades WHERE id = ?", (req.trade_id,)).fetchone()
        if not trade:
            raise HTTPException(404, "Trade not found")
        trade = dict(trade)
        entry = trade["entry_price"]
        direction = trade["direction"]
        shares = trade.get("shares") or 1
        
        if direction == "BULLISH":
            pnl = (req.exit_price - entry) * shares
            pct = ((req.exit_price - entry) / entry) * 100
        else:
            pnl = (entry - req.exit_price) * shares
            pct = ((entry - req.exit_price) / entry) * 100
            
        outcome = req.outcome or ("WIN" if pnl > 0 else "LOSS")
        conn.execute("""
            UPDATE trades SET
                status = 'CLOSED',
                exit_price = ?,
                exit_date = date('now'),
                outcome = ?,
                pnl_dollars = ?,
                pnl_pct = ?,
                notes = coalesce(?, notes)
            WHERE id = ?
        """, (req.exit_price, outcome, round(pnl, 2), round(pct, 2), req.notes, req.trade_id))
        conn.commit()
        return {"status": "ok", "pnl_dollars": round(pnl, 2), "pnl_pct": round(pct, 2)}
    finally:
        conn.close()
