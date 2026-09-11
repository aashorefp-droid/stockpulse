import sys, os
from fastapi import APIRouter, Query, HTTPException
from pydantic import BaseModel
from typing import Optional, List, Dict, Any

_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)
import tos_scanner

router = APIRouter(prefix="/api/tos", tags=["tos"])

DEFAULT_TOS_TICKERS = [
    "SPY", "QQQ", "AAPL", "MSFT", "NVDA", "AMZN", "GOOGL", "META", "TSLA", "AMD"
]

class TosScanRequest(BaseModel):
    tickers: Optional[List[str]] = None
    filters: Optional[Dict[str, Any]] = None

@router.get("/watchlists")
def get_tos_watchlists():
    return {"watchlists": getattr(tos_scanner, "WATCHLISTS", {})}

@router.get("/ticker/{ticker}")
def get_tos_ticker(ticker: str):
    ticker = ticker.upper().strip()
    res = tos_scanner.scan_ticker(ticker)
    if not res:
        raise HTTPException(status_code=404, detail=f"TOS scan unavailable for {ticker}")
    return res

@router.post("/scan")
def post_tos_scan(req: TosScanRequest):
    t_list = req.tickers or DEFAULT_TOS_TICKERS
    t_list = [t.strip().upper() for t in t_list if t.strip()]
    if not t_list:
        t_list = DEFAULT_TOS_TICKERS

    raw_results = tos_scanner.scan_multiple(t_list, max_workers=6)
    
    # Filter results if filters provided
    active_filters = req.filters or {}
    if active_filters:
        filtered = tos_scanner.apply_filters(raw_results, active_filters)
    else:
        filtered = raw_results

    # Sort by score descending
    filtered.sort(key=lambda x: x.get("signal_score", 0), reverse=True)

    # Summary metrics
    n_scanned = len(raw_results)
    n_filtered = len(filtered)
    n_bull = sum(1 for r in filtered if r.get("combined_bias") == "BULLISH ALIGNED")
    n_bear = sum(1 for r in filtered if r.get("combined_bias") == "BEARISH ALIGNED")
    n_mixed = n_filtered - n_bull - n_bear
    n_aplus = sum(1 for r in filtered if r.get("signal_grade") == "A+")
    n_a = sum(1 for r in filtered if r.get("signal_grade") == "A")
    n_bb_sq = sum(1 for r in filtered if r.get("bb_squeeze"))
    n_gc = sum(1 for r in filtered if r.get("golden_cross"))

    return {
        "status": "ok",
        "summary": {
            "scanned": n_scanned,
            "filtered": n_filtered,
            "bullish": n_bull,
            "bearish": n_bear,
            "mixed": n_mixed,
            "a_plus": n_aplus,
            "a": n_a,
            "bb_squeeze": n_bb_sq,
            "golden_cross": n_gc,
        },
        "items": filtered,
        "raw_items": raw_results,
    }

@router.get("/scan")
def get_tos_scan(tickers: Optional[str] = Query(None)):
    t_list = [t.strip().upper() for t in tickers.split(",") if t.strip()] if tickers else DEFAULT_TOS_TICKERS
    return post_tos_scan(TosScanRequest(tickers=t_list))
