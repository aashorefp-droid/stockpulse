import sys, os
from fastapi import APIRouter, Query
from typing import Optional, List

_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)
import news_sentiment
import news_theme_scanner

router = APIRouter(prefix="/api/news", tags=["news"])

@router.get("/ticker/{ticker}")
def get_ticker_news(ticker: str):
    ticker = ticker.upper().strip()
    score, count, label = news_sentiment.get_news_sentiment(ticker)
    details = news_sentiment.get_news_details(ticker)
    return {
        "ticker": ticker,
        "score": score,
        "count": count,
        "label": label,
        "headlines": details.get("headlines", []) if isinstance(details, dict) else []
    }

@router.get("/batch")
def get_batch_news(tickers: str = Query(...)):
    t_list = [t.strip().upper() for t in tickers.split(",") if t.strip()]
    results = news_sentiment.get_news_sentiment_batch(t_list, max_workers=4)
    return {"count": len(results), "items": results}

@router.get("/themes")
def get_news_themes():
    try:
        themes = news_theme_scanner.scan_news_themes()
        return {"themes": themes}
    except Exception as e:
        return {"themes": [], "error": str(e)}
