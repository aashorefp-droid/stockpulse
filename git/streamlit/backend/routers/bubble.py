import sys, os
from fastapi import APIRouter, Query, HTTPException
from pydantic import BaseModel
from typing import Optional, List, Dict, Any
import numpy as np
import pandas as pd

_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)
import bubble_scanner

router = APIRouter(prefix="/api/bubble", tags=["bubble"])

DEFAULT_BUBBLE_TICKERS = ["NVDA", "TSLA", "AAPL", "BTC-USD"]

class BubbleScanRequest(BaseModel):
    tickers: Optional[List[str]] = None
    period: str = "3y"
    as_of_date: Optional[str] = None

def _format_ticker_result(res: dict, include_chart: bool = False) -> dict:
    if not res:
        return {}
    out = {k: v for k, v in res.items() if not k.startswith("_")}
    if include_chart and "_df" in res and isinstance(res["_df"], pd.DataFrame):
        df = res["_df"]
        chart_data = []
        # Sample or limit to last 300 points for snappy transmission
        stride = max(1, len(df) // 300)
        df_sub = df.iloc[::stride]
        for dt, row in df_sub.iterrows():
            d_str = dt.strftime("%Y-%m-%d") if hasattr(dt, "strftime") else str(dt)[:10]
            chart_data.append({
                "date": d_str,
                "price": round(float(row.get("Close", 0)), 2),
                "sma_50": round(float(row.get("SMA_50", 0)), 2) if not pd.isna(row.get("SMA_50")) else None,
                "sma_200": round(float(row.get("SMA_200", 0)), 2) if not pd.isna(row.get("SMA_200")) else None,
                "ratio_50": round(float(row.get("Price_SMA50_Ratio", 1)), 3) if not pd.isna(row.get("Price_SMA50_Ratio")) else 1.0,
                "ratio_200": round(float(row.get("Price_SMA200_Ratio", 1)), 3) if not pd.isna(row.get("Price_SMA200_Ratio")) else 1.0,
                "risk_score": round(float(row.get("Bubble_Risk_Score", 0)), 1) if not pd.isna(row.get("Bubble_Risk_Score")) else 0.0,
            })
        out["chart_data"] = chart_data
    return out

@router.get("/ticker/{ticker}")
def get_bubble_ticker(
    ticker: str,
    period: str = "3y",
    as_of_date: Optional[str] = None
):
    ticker = ticker.upper().strip()
    res = bubble_scanner.analyze_ticker(ticker, period=period, as_of_date=as_of_date)
    if not res:
        raise HTTPException(status_code=404, detail=f"Bubble analysis unavailable for {ticker}")
    return _format_ticker_result(res, include_chart=True)

@router.post("/scan")
def post_bubble_scan(req: BubbleScanRequest):
    t_list = req.tickers or DEFAULT_BUBBLE_TICKERS
    t_list = [t.strip().upper() for t in t_list if t.strip()]
    if not t_list:
        t_list = DEFAULT_BUBBLE_TICKERS

    raw_results = []
    import concurrent.futures
    with concurrent.futures.ThreadPoolExecutor(max_workers=min(4, len(t_list))) as pool:
        futures = {pool.submit(bubble_scanner.analyze_ticker, t, req.period, as_of_date=req.as_of_date): t for t in t_list}
        for fut in concurrent.futures.as_completed(futures):
            try:
                r = fut.result()
                if r:
                    raw_results.append(r)
            except Exception:
                pass

    # Sort results by risk score descending
    raw_results.sort(key=lambda x: x.get("risk_score", 0), reverse=True)

    items = [_format_ticker_result(r, include_chart=True) for r in raw_results]
    return {
        "status": "ok",
        "count": len(items),
        "period": req.period,
        "as_of_date": req.as_of_date,
        "items": items,
    }

@router.get("/scan")
def get_bubble_scan(tickers: Optional[str] = Query(None), period: str = "3y"):
    t_list = [t.strip().upper() for t in tickers.split(",") if t.strip()] if tickers else DEFAULT_BUBBLE_TICKERS
    return post_bubble_scan(BubbleScanRequest(tickers=t_list, period=period))
