import sys, os
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import Optional, List

sys.path.insert(0, r"C:\Users\malla\git\streamlit")
from alpaca_paper import PaperTrader

router = APIRouter(prefix="/api/paper", tags=["paper"])

class OpenTradeRequest(BaseModel):
    ticker: str
    direction: str = "LONG"
    entry_price: float
    stop_price: float
    t1_price: float
    t2_price: float
    trade_date: Optional[str] = None
    scenario: Optional[str] = None
    confidence: Optional[str] = None

class CloseTradeRequest(BaseModel):
    trade_id: int
    exit_price: float
    reason: Optional[str] = "MANUAL"

@router.get("/trades")
def get_paper_trades():
    pt = PaperTrader()
    try:
        trades = pt.get_all_trades()
        return {"count": len(trades), "items": trades}
    finally:
        pt.close()

@router.get("/positions")
def get_paper_positions():
    pt = PaperTrader()
    try:
        trades = pt.get_open_trades()
        return {"count": len(trades), "items": trades}
    finally:
        pt.close()

@router.post("/trade")
def open_paper_trade(req: OpenTradeRequest):
    pt = PaperTrader()
    try:
        res = pt.open_trade(
            ticker=req.ticker.upper().strip(),
            direction=req.direction.upper().strip(),
            entry_price=req.entry_price,
            stop_price=req.stop_price,
            t1_price=req.t1_price,
            t2_price=req.t2_price,
            trade_date=req.trade_date,
            scenario=req.scenario,
            confidence=req.confidence,
        )
        trade_id = res.get("id") if isinstance(res, dict) else res
        return {"status": "ok", "trade_id": trade_id, "data": res}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))
    finally:
        pt.close()

@router.post("/close")
def close_paper_trade(req: CloseTradeRequest):
    pt = PaperTrader()
    try:
        pt.force_exit(req.trade_id, req.exit_price, req.reason or "MANUAL")
        return {"status": "ok", "message": f"Trade {req.trade_id} closed"}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))
    finally:
        pt.close()

@router.get("/account")
def get_alpaca_account(mode: str = "paper"):
    pt = PaperTrader()
    try:
        acct = pt.get_alpaca_account(mode=mode)
        if "error" in acct:
            return {
                "status": "error",
                "mode": mode,
                "error": acct.get("error"),
                "status_code": acct.get("status_code"),
                "equity": 0.0,
                "buying_power": 0.0,
                "cash": 0.0,
                "last_equity": 0.0,
                "day_pl": 0.0,
                "currency": "USD",
                "account_status": "ERROR",
                "raw": acct,
            }
        equity = float(acct.get("equity", 0) or 0)
        last_equity = float(acct.get("last_equity", 0) or 0)
        buying_power = float(acct.get("buying_power", 0) or 0)
        cash = float(acct.get("cash", 0) or 0)
        day_pl = equity - last_equity
        return {
            "status": "ok",
            "mode": mode,
            "equity": equity,
            "buying_power": buying_power,
            "cash": cash,
            "last_equity": last_equity,
            "day_pl": day_pl,
            "currency": acct.get("currency", "USD"),
            "account_status": acct.get("status", "ACTIVE"),
            "account_number": acct.get("account_number", ""),
            "pattern_day_trader": acct.get("pattern_day_trader", False),
            "trading_blocked": acct.get("trading_blocked", False),
            "raw": acct,
        }
    finally:
        pt.close()

@router.get("/alpaca-positions")
def get_alpaca_positions(mode: str = "paper"):
    pt = PaperTrader()
    try:
        positions = pt.get_alpaca_positions(mode=mode)
        if isinstance(positions, dict) and "error" in positions:
            return {"status": "error", "error": positions.get("error"), "items": []}
        return {"status": "ok", "count": len(positions) if isinstance(positions, list) else 0, "items": positions or []}
    finally:
        pt.close()

@router.get("/alpaca-orders")
def get_alpaca_orders(status: str = "all", limit: int = 20, mode: str = "paper"):
    pt = PaperTrader()
    try:
        orders = pt.get_alpaca_orders(status=status, limit=limit, mode=mode)
        if isinstance(orders, dict) and "error" in orders:
            return {"status": "error", "error": orders.get("error"), "items": []}
        return {"status": "ok", "count": len(orders) if isinstance(orders, list) else 0, "items": orders or []}
    finally:
        pt.close()

@router.get("/stats")
def get_paper_stats():
    pt = PaperTrader()
    try:
        stats = pt.get_overall_stats()
        return {"status": "ok", "stats": stats}
    finally:
        pt.close()

@router.get("/daily")
def get_paper_daily(limit: int = 60):
    pt = PaperTrader()
    try:
        daily = pt.get_daily_summary(limit=limit)
        return {"status": "ok", "count": len(daily), "items": daily}
    finally:
        pt.close()

@router.get("/closed")
def get_paper_closed(limit: int = 200):
    pt = PaperTrader()
    try:
        closed = pt.get_closed_trades(limit=limit)
        return {"status": "ok", "count": len(closed), "items": closed}
    finally:
        pt.close()

@router.post("/clear")
def clear_paper_trades():
    pt = PaperTrader()
    try:
        pt.clear_all()
        return {"status": "ok", "message": "All paper trades cleared"}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))
    finally:
        pt.close()

@router.get("/config")
def get_paper_config():
    try:
        import paper_config as pc
        return {
            "status": "ok",
            "position_size": getattr(pc, "POSITION_SIZE", 1000),
            "min_confidence": getattr(pc, "MIN_CONFIDENCE", "MEDIUM"),
            "min_rr_t1": getattr(pc, "MIN_RR_T1", 1.5),
            "min_best_rr": getattr(pc, "MIN_BEST_RR", 2.0),
            "entry_on_pullback": getattr(pc, "ENTRY_ON_PULLBACK", True),
            "pullback_max_dist_pct": getattr(pc, "PULLBACK_MAX_DIST_PCT", 0.5),
            "max_entry_pnl_pct": getattr(pc, "MAX_ENTRY_PNL_PCT", 1.0),
            "exit_on_stop": getattr(pc, "EXIT_ON_STOP", True),
            "exit_on_t1": getattr(pc, "EXIT_ON_T1", False),
            "exit_on_t2": getattr(pc, "EXIT_ON_T2", True),
            "exit_on_best_rr": getattr(pc, "EXIT_ON_BEST_RR", True),
            "min_exit_best_rr": getattr(pc, "MIN_EXIT_BEST_RR", 2.0),
            "partial_exit_at_t1": getattr(pc, "PARTIAL_EXIT_AT_T1", True),
            "trail_stop_after_t1": getattr(pc, "TRAIL_STOP_AFTER_T1", True),
            "exit_at_session_end": getattr(pc, "EXIT_AT_SESSION_END", True),
            "session_end_time_cst": getattr(pc, "SESSION_END_TIME_CST", "14:55"),
            "exit_on_diverged": getattr(pc, "EXIT_ON_DIVERGED", True),
            "exit_on_vflow_against": getattr(pc, "EXIT_ON_VFLOW_AGAINST", True),
            "max_loss_pct": getattr(pc, "MAX_LOSS_PCT", 2.0),
            "submit_to_alpaca": getattr(pc, "SUBMIT_TO_ALPACA", True),
            "order_type": getattr(pc, "ORDER_TYPE", "limit"),
            "time_in_force": getattr(pc, "TIME_IN_FORCE", "day"),
            "auto_paper_in_replay": getattr(pc, "AUTO_PAPER_IN_REPLAY", True),
            "scenario_entry_rules": getattr(pc, "SCENARIO_ENTRY_RULES", {}),
        }
    except Exception as e:
        return {"status": "error", "detail": str(e)}

