"""
news_sentiment.py — Fetch recent headlines from Finviz and classify sentiment.

Usage:
    from news_sentiment import get_news_sentiment, get_news_details

    label = get_news_sentiment("AAPL")           # "Good", "Bad", or "No"
    details = get_news_details("AAPL")            # full headlines + scores
"""

import time
import concurrent.futures
from concurrent.futures import ThreadPoolExecutor
import requests
from requests.adapters import HTTPAdapter
from bs4 import BeautifulSoup

# ── Config ──────────────────────────────────────────────────────────────────
_NEWS_CACHE = {}           # {ticker: (timestamp, result_dict)}
_NEWS_CACHE_TTL = 600      # 10 minutes
_HEADLINES_COUNT = 15      # number of recent headlines to check

_SESSION = None

def _get_session():
    """Shared requests.Session with connection pooling for fast keep-alive reuse."""
    global _SESSION
    if _SESSION is None:
        _SESSION = requests.Session()
        adapter = HTTPAdapter(pool_connections=25, pool_maxsize=25, max_retries=1)
        _SESSION.mount("https://", adapter)
        _SESSION.mount("http://", adapter)
        _SESSION.headers.update({"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"})
    return _SESSION

_BAD_WORDS = {
    "downgrade", "downgrades", "downgraded", "cut", "cuts", "slash", "slashes",
    "miss", "misses", "missed", "loss", "losses", "decline", "declines",
    "drop", "drops", "fell", "falls", "fall", "plunge", "plunges", "plunged",
    "sink", "sinks", "crash", "crashes", "tumble", "tumbles", "slump", "slumps",
    "warn", "warns", "warning", "risk", "risks", "fear", "fears", "layoff",
    "layoffs", "recall", "recalls", "lawsuit", "sue", "sues", "sued", "fraud",
    "probe", "investigation", "investigated", "fine", "fined", "penalty",
    "bankruptcy", "default", "debt", "weak", "weaker", "worst", "sell",
    "bear", "bearish", "negative", "concern", "concerns", "delay", "delays",
    "suspend", "suspended", "halt", "halted", "disappointing", "disappoints",
    "shortfall", "deficit", "underperform",
}

_GOOD_WORDS = {
    "upgrade", "upgrades", "upgraded", "raise", "raises", "raised", "beat",
    "beats", "surge", "surges", "surged", "rally", "rallies", "rallied",
    "gain", "gains", "jump", "jumps", "jumped", "soar", "soars", "soared",
    "rise", "rises", "rose", "high", "highs", "record", "boost", "boosts",
    "boosted", "strong", "stronger", "strongest", "buy", "bull", "bullish",
    "positive", "growth", "grows", "profit", "profits", "revenue", "deal",
    "approval", "approved", "launch", "launches", "launched", "partnership",
    "outperform", "outperforms", "dividend", "buyback", "expand", "expansion",
    "win", "wins", "won", "contract", "awarded",
}


def _fetch_finviz_news(ticker: str) -> list[dict]:
    """Scrape recent headlines from Finviz. Returns list of {headline, source, time}."""
    try:
        session = _get_session()
        resp = session.get(
            f"https://finviz.com/quote.ashx?t={ticker}&ty=c&p=d&b=1",
            timeout=8,
        )
        if resp.status_code != 200:
            return []
        soup = BeautifulSoup(resp.text, "html.parser")
        news_table = soup.find(id="news-table")
        if not news_table:
            return []

        results = []
        current_date = ""
        for row in news_table.find_all("tr"):
            tds = row.find_all("td")
            if len(tds) < 2:
                continue
            date_td = tds[0].text.strip()
            if " " in date_td and len(date_td) > 8:
                # Has date + time like "Apr-02-26 08:30AM"
                current_date = date_td.split()[0]
                time_str = date_td.split()[-1] if len(date_td.split()) > 1 else ""
            else:
                time_str = date_td  # just time like "08:30AM"

            a_tag = row.find("a")
            if not a_tag:
                continue
            headline = a_tag.text.strip()
            source_span = tds[1].find("span") if len(tds) > 1 else None
            # Source is sometimes in a small span after the link
            source = ""
            for span in row.find_all("span"):
                txt = span.text.strip()
                if txt and txt != headline and len(txt) < 30:
                    source = txt
                    break

            results.append({
                "headline": headline,
                "date": current_date,
                "time": time_str,
                "source": source,
            })
            if len(results) >= _HEADLINES_COUNT:
                break
        return results
    except Exception:
        return []


def _score_headlines(headlines: list[dict]) -> tuple[int, int, list[dict]]:
    """Score headlines. Returns (good_score, bad_score, scored_headlines)."""
    scored = []
    total_good = 0
    total_bad = 0
    for h in headlines:
        words = set(h["headline"].lower().split())
        g = len(words & _GOOD_WORDS)
        b = len(words & _BAD_WORDS)
        total_good += g
        total_bad += b
        if g > b:
            sentiment = "Good"
        elif b > g:
            sentiment = "Bad"
        else:
            sentiment = "Neutral"
        scored.append({**h, "good": g, "bad": b, "sentiment": sentiment})
    return total_good, total_bad, scored


def get_news_details(ticker: str) -> dict:
    """
    Full news analysis for a ticker.
    Returns: {label, good_score, bad_score, headlines: [{headline, date, time, source, sentiment, good, bad}]}
    Cached for 10 minutes.
    """
    now = time.time()
    cached = _NEWS_CACHE.get(ticker)
    if cached and (now - cached[0]) < _NEWS_CACHE_TTL:
        return cached[1]

    headlines = _fetch_finviz_news(ticker)
    good_score, bad_score, scored = _score_headlines(headlines)

    if good_score > bad_score and good_score >= 2:
        label = "Good"
    elif bad_score > good_score and bad_score >= 2:
        label = "Bad"
    else:
        label = "No"

    result = {
        "label": label,
        "good_score": good_score,
        "bad_score": bad_score,
        "headlines": scored,
    }
    # Prune expired/excess entries to cap memory usage
    if len(_NEWS_CACHE) > 100:
        expired = [t for t, (ts, _) in _NEWS_CACHE.items() if (now - ts) > _NEWS_CACHE_TTL]
        for t in expired:
            _NEWS_CACHE.pop(t, None)
        if len(_NEWS_CACHE) > 100:
            oldest = sorted(_NEWS_CACHE.items(), key=lambda x: x[1][0])[:len(_NEWS_CACHE) - 100]
            for t, _ in oldest:
                _NEWS_CACHE.pop(t, None)

    _NEWS_CACHE[ticker] = (now, result)
    return result


def get_news_sentiment(ticker: str) -> str:
    """Quick sentiment: returns 'Good', 'Bad', or 'No'."""
    return get_news_details(ticker)["label"]


def get_news_sentiment_batch(tickers: list[str], max_workers: int = 4) -> dict:
    """Fetch news sentiment for multiple tickers in parallel. Returns {ticker: label}."""
    if not tickers:
        return {}
    results = {}
    unique_tickers = list(dict.fromkeys(tickers))
    with ThreadPoolExecutor(max_workers=min(max_workers, max(1, len(unique_tickers)))) as pool:
        future_map = {pool.submit(get_news_sentiment, t): t for t in unique_tickers}
        for fut in concurrent.futures.as_completed(future_map):
            t = future_map[fut]
            try:
                results[t] = fut.result()
            except Exception:
                results[t] = "No"
    return results


if __name__ == "__main__":
    import sys
    tickers = sys.argv[1:] if len(sys.argv) > 1 else ["AAPL", "TSLA", "NVDA"]
    for t in tickers:
        d = get_news_details(t)
        print(f"\n{'='*60}")
        print(f"{t}: {d['label']}  (good={d['good_score']}, bad={d['bad_score']})")
        print(f"{'='*60}")
        for h in d["headlines"][:10]:
            icon = "🟢" if h["sentiment"] == "Good" else ("🔴" if h["sentiment"] == "Bad" else "⚪")
            print(f"  {icon} {h['headline'][:80]}")
