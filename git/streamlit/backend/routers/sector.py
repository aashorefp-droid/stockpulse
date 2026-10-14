import sys, os
from fastapi import APIRouter, Query, HTTPException
from pydantic import BaseModel
from typing import Optional, List

_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)
from backend.services.sector_service import (
    get_sector_overview_service,
    scan_all_sectors_service,
    SECTOR_ETFS_DICT,
)

router = APIRouter(prefix="/api/sector", tags=["sector"])

class SectorScanRequest(BaseModel):
    as_of_date: Optional[str] = None

@router.get("/overview")
def get_sector_overview():
    try:
        items = get_sector_overview_service()
        return {"status": "ok", "items": items}
    except Exception as e:
        return {"status": "error", "items": [], "error": str(e)}

@router.post("/scan")
def scan_all_sectors(req: SectorScanRequest):
    try:
        res = scan_all_sectors_service(as_of_date=req.as_of_date)
        return res
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

