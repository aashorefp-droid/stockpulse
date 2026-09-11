import sys, os
from fastapi import APIRouter
from pydantic import BaseModel
from typing import Optional, List, Dict, Any

from backend.services.plan_service import generate_intraday_plan, check_open_prices, refresh_locked_prices

router = APIRouter(prefix="/api/plan", tags=["plan"])

class PlanCalculateRequest(BaseModel):
    account_size: float = 25000.0
    risk_pct: float = 1.0  # 1% risk per trade
    entry_price: float
    stop_loss: float
    target1: float
    target2: Optional[float] = None
    direction: str = "LONG"

class PlanGenerateRequest(BaseModel):
    tickers: List[str]
    plan_date: Optional[str] = None

class PlanCheckOpenRequest(BaseModel):
    plan_rows: List[Dict[str, Any]]
    check_date: Optional[str] = None

class PlanRefreshLockedRequest(BaseModel):
    locked_rows: List[Dict[str, Any]]

@router.post("/calculate")
def calculate_trade_plan(req: PlanCalculateRequest):
    risk_dollars = req.account_size * (req.risk_pct / 100.0)
    per_share_risk = abs(req.entry_price - req.stop_loss)
    
    if per_share_risk <= 0:
        return {"error": "Stop loss must be different from entry price"}
        
    shares = int(risk_dollars / per_share_risk)
    total_cost = round(shares * req.entry_price, 2)
    
    per_share_reward_t1 = abs(req.target1 - req.entry_price)
    rr_t1 = round(per_share_reward_t1 / per_share_risk, 2) if per_share_risk > 0 else 0.0
    profit_t1 = round(shares * per_share_reward_t1, 2)
    
    res = {
        "shares": shares,
        "total_cost": total_cost,
        "risk_dollars": round(risk_dollars, 2),
        "per_share_risk": round(per_share_risk, 2),
        "rr_t1": rr_t1,
        "profit_t1": profit_t1,
    }
    
    if req.target2:
        per_share_reward_t2 = abs(req.target2 - req.entry_price)
        rr_t2 = round(per_share_reward_t2 / per_share_risk, 2)
        profit_t2 = round(shares * per_share_reward_t2, 2)
        res["rr_t2"] = rr_t2
        res["profit_t2"] = profit_t2
        
    return res

@router.post("/generate")
def generate_plan_endpoint(req: PlanGenerateRequest):
    return generate_intraday_plan(req.tickers, req.plan_date)

@router.post("/check-open")
def check_open_endpoint(req: PlanCheckOpenRequest):
    return check_open_prices(req.plan_rows, req.check_date)

@router.post("/refresh-locked")
def refresh_locked_endpoint(req: PlanRefreshLockedRequest):
    return refresh_locked_prices(req.locked_rows)
