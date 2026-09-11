import sys, os
from fastapi import APIRouter, Query, HTTPException
from pydantic import BaseModel
from typing import Optional, List, Dict, Any
from datetime import date, datetime
import concurrent.futures

sys.path.insert(0, r"C:\Users\malla\git\streamlit")
import future_growth_scan as fgs

router = APIRouter(prefix="/api/growth", tags=["growth"])

class GrowthScanRequest(BaseModel):
    sectors: Optional[List[str]] = None
    as_of_date: Optional[str] = None
    min_score: float = 40.0
    top_n: int = 10

@router.get("/sectors")
def get_growth_sectors():
    return {"sectors": fgs.GROWTH_SECTORS}

@router.post("/scan")
def post_growth_scan(req: GrowthScanRequest):
    as_of = datetime.strptime(req.as_of_date, "%Y-%m-%d").date() if req.as_of_date else date.today()
    selected_sectors = req.sectors or list(fgs.GROWTH_SECTORS.keys())
    
    sector_results = {}
    all_rows = []

    for sector_name in selected_sectors:
        tickers = fgs.GROWTH_SECTORS.get(sector_name, [])
        if not tickers:
            continue

        rows = []
        with concurrent.futures.ThreadPoolExecutor(max_workers=min(6, len(tickers))) as ex:
            futures = {ex.submit(fgs.scan_ticker, sym, as_of): sym for sym in tickers}
            for fut in concurrent.futures.as_completed(futures):
                try:
                    res = fut.result()
                    if res and "_error" not in res:
                        rows.append(res)
                except Exception:
                    pass

        # Filter by min_score and sort descending
        filtered = [r for r in rows if r.get("_score_raw", 0) >= req.min_score]
        filtered.sort(key=lambda x: x.get("_score_raw", 0), reverse=True)
        if req.top_n > 0:
            filtered = filtered[:req.top_n]

        sector_results[sector_name] = filtered
        all_rows.extend(rows)

    # Top Combined Picks across all sectors
    seen_tickers = set()
    combined_picks = []
    for r in sorted(all_rows, key=lambda x: x.get("_score_raw", 0), reverse=True):
        if r.get("_score_raw", 0) >= req.min_score and r["Ticker"] not in seen_tickers:
            seen_tickers.add(r["Ticker"])
            combined_picks.append(r)
            if len(combined_picks) >= 20:
                break

    return {
        "status": "ok",
        "as_of_date": str(as_of),
        "min_score": req.min_score,
        "top_n": req.top_n,
        "by_sector": sector_results,
        "top_picks": combined_picks,
        "total_scanned": len(all_rows),
    }

@router.get("/scan")
def get_growth_scan(sector: Optional[str] = Query(None), min_score: float = 40.0):
    sectors = [sector] if sector and sector in fgs.GROWTH_SECTORS else None
    return post_growth_scan(GrowthScanRequest(sectors=sectors, min_score=min_score))
