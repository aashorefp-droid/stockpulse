import time
import requests
from bs4 import BeautifulSoup
from typing import Optional, Dict, Any, List

_CACHE: Dict[str, tuple[float, Dict[str, Any]]] = {}
_CACHE_TTL = 7200  # 2 hours

_session = requests.Session()
_session.headers.update({
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
})


def get_stock_verdict(ticker: str, force_refresh: bool = False) -> Optional[Dict[str, Any]]:
    """
    Scrape fundamental verdict, valuation price levels, 4-factor meters,
    trust score, and plain-English pros/cons from stockverdicts.com.
    Results are cached in-memory for 2 hours.
    """
    if not ticker:
        return None

    tk = ticker.strip().upper()
    now = time.time()

    if not force_refresh and tk in _CACHE:
        ts, cached = _CACHE[tk]
        if now - ts < _CACHE_TTL:
            return cached

    url = f"https://stockverdicts.com/stock/{tk}"
    try:
        r = _session.get(url, timeout=10)
        if r.status_code != 200:
            return None

        soup = BeautifulSoup(r.text, "html.parser")

        # 1. Verdict badge (GROWTH / VALUE / AVOID / PEAK)
        verdict = None
        for b in soup.find_all(class_="badge"):
            txt = b.get_text(strip=True).upper()
            if txt in ("GROWTH", "VALUE", "AVOID", "PEAK"):
                verdict = txt
                break

        # 2. Valuation Signal (e.g. Overvalued, Strong Buy, etc.)
        sig_el = soup.find(class_="an-sig")
        signal = sig_el.get_text(strip=True) if sig_el else None

        # 3. Valuation Price Levels Ladder (Heavy, Buy, Fair, Exit)
        levels: Dict[str, str] = {}
        for llbl in soup.find_all(class_="llbl"):
            b = llbl.find("b")
            if b:
                px = b.get_text(strip=True)
                name = llbl.get_text(strip=True).replace(px, "").strip()
                levels[name] = px

        # 4. Factor Meters (0-100: VALUE, GROWTH, QUALITY, TECHNICAL)
        meters: Dict[str, int] = {}
        for m in soup.find_all(class_="meter"):
            top = m.find(class_="m-top")
            if top:
                lbl = top.find("span")
                val = top.find("b")
                if lbl and val:
                    try:
                        meters[lbl.get_text(strip=True).upper()] = int(val.get_text(strip=True))
                    except ValueError:
                        pass

        # 5. Trust Score (0-100 gauge)
        trust_num = soup.find(class_="an-gauge-num")
        trust_lbl = soup.find(class_="an-gauge-label")
        trust_score = None
        if trust_num:
            try:
                trust_score = int(trust_num.get_text(strip=True))
            except ValueError:
                pass
        trust_label = trust_lbl.get_text(strip=True) if trust_lbl else None

        # 6. Plain-English Pros & Cons
        pros: List[str] = [li.get_text(strip=True) for li in soup.select(".pc-pros li")]
        cons: List[str] = [li.get_text(strip=True) for li in soup.select(".pc-cons li")]

        # 7. Earnings Quality / Accounting Integrity Checks
        eq_rows: List[str] = [row.get_text(strip=True) for row in soup.select(".eq-row")]

        # 8. Technical Summary
        tech_el = soup.find(class_="an-tech")
        tech_summary = tech_el.get_text(" ", strip=True) if tech_el else None

        # 9. Sector Peers
        peers: List[Dict[str, Any]] = []
        for p in soup.select(".peer"):
            ptkr = p.find(class_="peer-tkr")
            pv = p.find(class_="peer-v")
            psc = p.find(class_="peer-sc")
            if ptkr:
                peers.append({
                    "ticker": ptkr.get_text(strip=True),
                    "verdict": pv.get_text(strip=True) if pv else None,
                    "score": psc.get_text(strip=True) if psc else None
                })

        data = {
            "ticker": tk,
            "verdict": verdict,
            "signal": signal,
            "levels": levels,
            "meters": meters,
            "trust_score": trust_score,
            "trust_label": trust_label,
            "pros": pros,
            "cons": cons,
            "earnings_quality": eq_rows,
            "tech_summary": tech_summary,
            "peers": peers,
            "source_url": url,
            "fetched_at": time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime(now)),
        }

        _CACHE[tk] = (now, data)
        return data

    except Exception as e:
        return {
            "ticker": tk,
            "error": str(e),
            "source_url": url,
        }
