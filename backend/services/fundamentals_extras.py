"""
Fundamentals add-ons for the StockPulse AI agent:

  * Finviz insider activity  (Form 4 table + ownership snapshot)  -> fetch_finviz_insider
  * Congress / politician trades (Quiver Quantitative public page) -> fetch_congress_trades
      NOTE: Finviz does NOT publish STOCK-Act (Congress) disclosures, so Congress
      trades are read from Quiver Quantitative's public per-ticker page instead.
  * AlphaSpread intrinsic value - Bear / Base / Bull DCF cases     -> fetch_alphaspread_intrinsic

All fetchers are synchronous (curl_cffi with Chrome impersonation, which is
required to get past Cloudflare on AlphaSpread / Quiver) and cached in-memory.
They never raise: failures are returned as {"error": "..."} so the chat agent can
report "not retrieved" instead of hallucinating.
"""
from __future__ import annotations

import html as html_lib
import re
import time
from datetime import date, datetime, timedelta
from typing import Any, Dict, List, Optional

try:
    from curl_cffi import requests as _cr
except Exception:  # pragma: no cover - curl_cffi is in requirements.txt
    _cr = None

import requests as _plain_requests

_CACHE: Dict[str, tuple[float, Any]] = {}
_TTL_SECONDS = 30 * 60
_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"
)


def _cache_get(key: str) -> Optional[Any]:
    hit = _CACHE.get(key)
    if hit and time.time() - hit[0] < _TTL_SECONDS:
        return hit[1]
    return None


def _cache_put(key: str, val: Any) -> Any:
    _CACHE[key] = (time.time(), val)
    return val


def _get(url: str, timeout: int = 20) -> Optional[str]:
    """GET a page; prefers Chrome-impersonating curl_cffi, falls back to requests."""
    try:
        if _cr is not None:
            r = _cr.get(url, impersonate="chrome", timeout=timeout)
        else:
            r = _plain_requests.get(url, headers={"User-Agent": _UA}, timeout=timeout)
        if r.status_code == 200:
            return r.text
    except Exception:
        pass
    return None


def _num(s: Any) -> Optional[float]:
    try:
        return float(str(s).replace(",", "").replace("$", "").replace("%", "").strip())
    except Exception:
        return None


def _fmt_money(v: Optional[float]) -> str:
    if v is None:
        return "N/A"
    a = abs(v)
    sign = "-" if v < 0 else ""
    if a >= 1e9:
        return f"{sign}${a / 1e9:.2f}B"
    if a >= 1e6:
        return f"{sign}${a / 1e6:.2f}M"
    if a >= 1e3:
        return f"{sign}${a / 1e3:.0f}K"
    return f"{sign}${a:,.0f}"


# ─────────────────────────────────────────────────────────────────────────────
# Finviz insider activity
# ─────────────────────────────────────────────────────────────────────────────

def _parse_finviz_date(raw: str) -> Optional[date]:
    """Finviz dates look like "Sep 05 '26"."""
    try:
        return datetime.strptime(raw.replace("'", "").strip(), "%b %d %y").date()
    except Exception:
        return None


def fetch_finviz_insider(ticker: str, max_rows: int = 15) -> Dict[str, Any]:
    sym = (ticker or "").strip().upper().replace(".", "-")
    if not sym:
        return {"error": "Empty ticker"}
    ck = f"finviz_insider:{sym}"
    cached = _cache_get(ck)
    if cached is not None:
        return cached

    page_url = f"https://finviz.com/quote.ashx?t={sym}"
    text = _get(page_url)
    if not text:
        return {"error": f"Finviz page not retrieved for {sym}", "source": page_url}

    try:
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(text, "lxml")
    except Exception as e:  # pragma: no cover
        return {"error": f"Finviz parse failed: {e}", "source": page_url}

    # Ownership snapshot (Insider Own / Insider Trans / Inst Own / Inst Trans / Short Float)
    snap: Dict[str, str] = {}
    try:
        cells = soup.select("td.snapshot-td2")
        labels = soup.select("td.snapshot-td2-cp")
        for lab, val in zip(labels, cells):
            snap[lab.get_text(strip=True)] = val.get_text(strip=True)
    except Exception:
        pass

    rows: List[Dict[str, Any]] = []
    today = date.today()
    for tr in soup.select("tr.fv-insider-row"):
        tds = tr.find_all("td")
        if len(tds) < 8:
            continue
        d = _parse_finviz_date(tds[2].get_text(strip=True))
        txn = tds[3].get_text(" ", strip=True)
        shares = _num(tds[5].get_text(strip=True))
        value = _num(tds[6].get_text(strip=True))
        rows.append({
            "insider": tds[0].get_text(" ", strip=True),
            "relationship": tds[1].get_text(" ", strip=True),
            "date": d.isoformat() if d else tds[2].get_text(strip=True),
            "transaction": txn,
            "cost": _num(tds[4].get_text(strip=True)),
            "shares": shares,
            "value_usd": value,
            "shares_total": _num(tds[7].get_text(strip=True)),
            "_d": d,
        })

    if not rows and not snap:
        return {"error": f"No Finviz insider data parsed for {sym}", "source": page_url}

    # Open-market buys / sells only (ignore option exercises, grants, gifts, proposed sales)
    def _kind(t: str) -> str:
        tl = t.lower()
        if tl.startswith("buy") or tl == "purchase":
            return "buy"
        if tl.startswith("sale") or tl == "sell":
            return "sell"
        return "other"

    def _window(days: int) -> Dict[str, Any]:
        cutoff = today - timedelta(days=days)
        buys = [r for r in rows if r["_d"] and r["_d"] >= cutoff and _kind(r["transaction"]) == "buy"]
        sells = [r for r in rows if r["_d"] and r["_d"] >= cutoff and _kind(r["transaction"]) == "sell"]
        bv = sum((r["value_usd"] or 0) for r in buys)
        sv = sum((r["value_usd"] or 0) for r in sells)
        return {
            "open_market_buys": len(buys),
            "open_market_sells": len(sells),
            "buy_value": _fmt_money(bv),
            "sell_value": _fmt_money(sv),
            "net_value": _fmt_money(bv - sv),
            "net_signal": (
                "NET INSIDER BUYING" if bv > sv and len(buys) > 0
                else "NET INSIDER SELLING" if sv > bv
                else "NEUTRAL"
            ),
        }

    recent = [{k: v for k, v in r.items() if k != "_d"} for r in rows[:max_rows]]
    for r in recent:
        r["value"] = _fmt_money(r.get("value_usd"))

    out = {
        "source": "finviz.com (SEC Form 4)",
        "page_url": page_url,
        "ticker": sym,
        "ownership_snapshot": {
            "insider_own": snap.get("Insider Own"),
            "insider_trans_6m": snap.get("Insider Trans"),
            "institutional_own": snap.get("Inst Own"),
            "institutional_trans": snap.get("Inst Trans"),
            "short_float": snap.get("Short Float"),
        },
        "last_90_days": _window(90),
        "last_180_days": _window(180),
        "recent_transactions": recent,
    }
    return _cache_put(ck, out)


# ─────────────────────────────────────────────────────────────────────────────
# Congress / politician trades (Quiver Quantitative public page)
# ─────────────────────────────────────────────────────────────────────────────

def fetch_congress_trades(ticker: str, max_rows: int = 12) -> Dict[str, Any]:
    sym = (ticker or "").strip().upper().replace(".", "-")
    if not sym:
        return {"error": "Empty ticker"}
    ck = f"congress:{sym}"
    cached = _cache_get(ck)
    if cached is not None:
        return cached

    page_url = f"https://www.quiverquant.com/stock/{sym}/"
    text = _get(page_url, timeout=25)
    if not text:
        return {"error": f"Congress trading page not retrieved for {sym}", "source": page_url}

    try:
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(text, "lxml")
    except Exception as e:  # pragma: no cover
        return {"error": f"Congress parse failed: {e}", "source": page_url}

    trades: List[Dict[str, Any]] = []
    for tb in soup.find_all("table"):
        heads = [th.get_text(" ", strip=True) for th in tb.find_all("th")]
        if heads[:3] != ["Politician", "Type", "Traded"]:
            continue
        for tr in tb.find_all("tr"):
            tds = [td.get_text(" ", strip=True) for td in tr.find_all("td")]
            if len(tds) < 3:
                continue
            m_pol = re.match(r"^(.*?)\s+(House|Senate)\s*/\s*([A-Z])$", tds[0])
            m_typ = re.match(r"^(Purchase|Sale(?:\s*\((?:Full|Partial)\))?|Exchange)\s*(.*)$", tds[1])
            try:
                d = datetime.strptime(tds[2], "%b %d, %Y").date()
            except Exception:
                d = None
            trades.append({
                "politician": m_pol.group(1) if m_pol else tds[0],
                "chamber": m_pol.group(2) if m_pol else None,
                "party": m_pol.group(3) if m_pol else None,
                "transaction": m_typ.group(1) if m_typ else tds[1],
                "amount_range": (m_typ.group(2) or None) if m_typ else None,
                "traded": d.isoformat() if d else tds[2],
                "_d": d,
            })
        break

    if not trades:
        out = {
            "source": "quiverquant.com (STOCK Act disclosures)",
            "page_url": page_url,
            "ticker": sym,
            "note": "No Congress trades found for this ticker.",
            "total_trades": 0,
        }
        return _cache_put(ck, out)

    today = date.today()

    def _win(days: int) -> Dict[str, Any]:
        cutoff = today - timedelta(days=days)
        w = [t for t in trades if t["_d"] and t["_d"] >= cutoff]
        buys = [t for t in w if t["transaction"].lower().startswith("purchase")]
        sells = [t for t in w if t["transaction"].lower().startswith("sale")]
        return {
            "purchases": len(buys),
            "sales": len(sells),
            "buyers": sorted({t["politician"] for t in buys}),
            "sellers": sorted({t["politician"] for t in sells}),
            "net_signal": (
                "CONGRESS NET BUYING" if len(buys) > len(sells)
                else "CONGRESS NET SELLING" if len(sells) > len(buys)
                else "NEUTRAL" if (buys or sells) else "NO RECENT ACTIVITY"
            ),
        }

    out = {
        "source": "quiverquant.com (STOCK Act disclosures)",
        "page_url": page_url,
        "ticker": sym,
        "total_trades": len(trades),
        "last_90_days": _win(90),
        "last_180_days": _win(180),
        "last_365_days": _win(365),
        "recent_trades": [{k: v for k, v in t.items() if k != "_d"} for t in trades[:max_rows]],
    }
    return _cache_put(ck, out)


# ─────────────────────────────────────────────────────────────────────────────
# AlphaSpread intrinsic value (Bear / Base / Bull DCF)
# ─────────────────────────────────────────────────────────────────────────────

_EXCH_HINTS = {
    "NMS": "nasdaq", "NGM": "nasdaq", "NCM": "nasdaq", "NASDAQ": "nasdaq",
    "NYQ": "nyse", "NYSE": "nyse",
    "ASE": "nysemkt", "AMEX": "nysemkt",
    "PCX": "nysearca", "BTS": "nysearca",
}


def _exchange_candidates(sym: str) -> List[str]:
    cands: List[str] = []
    try:
        import yfinance as yf
        ex = str(yf.Ticker(sym).fast_info.get("exchange") or "").upper()
        if ex in _EXCH_HINTS:
            cands.append(_EXCH_HINTS[ex])
    except Exception:
        pass
    for c in ("nasdaq", "nyse", "nysearca", "nysemkt"):
        if c not in cands:
            cands.append(c)
    return cands


def _clean_text(page: str) -> str:
    t = re.sub(r"<script.*?</script>|<style.*?</style>", " ", page, flags=re.S)
    t = re.sub(r"<[^>]+>", " ", t)
    return re.sub(r"\s+", " ", html_lib.unescape(t))


def _parse_case(page: str, case: str) -> Optional[Dict[str, Any]]:
    txt = _clean_text(page)
    m = re.search(
        rf"The DCF Value for (?P<name>.+?) \((?P<tk>[^)]+)\) under the {case} Case is "
        rf"(?P<val>-?[\d,\.]+) (?P<cur>[A-Z]{{3}})\s*\.\s*Compared with the current market price of "
        rf"(?P<px>[\d,\.]+) [A-Z]{{3}}\s*,\s*the stock appears (?P<verdict>[A-Za-z ]+?)(?: by (?P<pct>\d+)%)?\s*\.",
        txt,
    )
    if not m:
        return None
    val = _num(m.group("val"))
    px = _num(m.group("px"))
    verdict = m.group("verdict").strip()
    pct = _num(m.group("pct")) if m.group("pct") else None

    def _g(pattern: str) -> Optional[str]:
        mm = re.search(pattern, txt)
        return mm.group(1) if mm else None

    assumptions = {
        "revenue_growth_pct": _num(_g(r"DCF Assumptions Revenue Growth (-?[\d\.]+)%")),
        "net_margin_start_pct": _num(_g(r"Net Margin (-?[\d\.]+)% →")),
        "net_margin_end_pct": _num(_g(r"Net Margin -?[\d\.]+% → (-?[\d\.]+)%")),
        "cash_flow_conversion_pct": _num(_g(r"Cash Flow Conversion (-?[\d\.]+)%")),
        "discount_rate_pct": _num(_g(r"Discount Rate (-?[\d\.]+)%")),
        "terminal_growth_pct": _num(_g(r"Terminal Growth (-?[\d\.]+)%")),
        "forecast_years": _num(_g(r"Forecast Period (\d+) years")),
    }
    return {
        "case": case,
        "dcf_value": val,
        "currency": m.group("cur"),
        "current_price": px,
        "verdict": verdict,                      # Overvalued / Undervalued / Fairly valued
        "mispricing_pct": pct,                   # as printed by AlphaSpread
        "upside_vs_price_pct": round((val / px - 1) * 100, 1) if (val is not None and px) else None,
        "assumptions": assumptions,
        "_company": m.group("name"),
        "_ticker": m.group("tk"),
    }


def fetch_alphaspread_intrinsic(ticker: str) -> Dict[str, Any]:
    sym = (ticker or "").strip().upper().replace("-", ".")
    if not sym:
        return {"error": "Empty ticker"}
    ck = f"alphaspread:{sym}"
    cached = _cache_get(ck)
    if cached is not None:
        return cached

    slug = sym.lower()
    for exch in _exchange_candidates(sym.replace(".", "-")):
        base = f"https://www.alphaspread.com/security/{exch}/{slug}"
        base_page = _get(f"{base}/dcf-valuation")
        if not base_page:
            continue
        base_case = _parse_case(base_page, "Base")
        if not base_case:
            continue

        cases: Dict[str, Any] = {"base": base_case}
        for key, label in (("bear", "Bear"), ("bull", "Bull")):
            pg = _get(f"{base}/dcf-valuation/{key}-case")
            parsed = _parse_case(pg, label) if pg else None
            if parsed:
                cases[key] = parsed

        # Multiples-based (relative) value from the summary page snapshot
        relative_value = None
        sm = _get(f"{base}/summary")
        if sm:
            mrv = re.search(r'"relativeValue":\s*(-?[\d\.]+)', html_lib.unescape(sm))
            if mrv:
                relative_value = _num(mrv.group(1))

        company = base_case.pop("_company", None)
        for c in cases.values():
            c.pop("_company", None)
            c.pop("_ticker", None)
        px = base_case.get("current_price")

        vals = [cases[k]["dcf_value"] for k in ("bear", "base", "bull") if k in cases and cases[k].get("dcf_value") is not None]
        out = {
            "source": "alphaspread.com (DCF intrinsic value)",
            "page_url": f"{base}/summary",
            "ticker": sym,
            "company": company,
            "current_price": px,
            "currency": base_case.get("currency"),
            "scenarios": {
                k: {
                    "intrinsic_value": cases[k]["dcf_value"],
                    "upside_vs_price_pct": cases[k]["upside_vs_price_pct"],
                    "verdict": cases[k]["verdict"],
                    "assumptions": cases[k]["assumptions"],
                }
                for k in ("bear", "base", "bull") if k in cases
            },
            "multiples_based_relative_value": relative_value,
            "intrinsic_value_range": (
                f"{min(vals):.2f} – {max(vals):.2f} {base_case.get('currency') or ''}".strip() if vals else None
            ),
            "overall_read": (
                "UNDERVALUED in all 3 cases" if px and vals and min(vals) > px
                else "OVERVALUED in all 3 cases" if px and vals and max(vals) < px
                else "MIXED — price sits inside the bear↔bull intrinsic range" if px and vals
                else None
            ),
            "note": "Negative intrinsic value in the bear case means the DCF projects cash-flow destruction (equity worth ~0).",
        }
        return _cache_put(ck, out)

    return {"error": f"AlphaSpread intrinsic value not available for {sym}", "source": "alphaspread.com"}
