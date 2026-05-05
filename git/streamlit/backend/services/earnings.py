"""
Earnings analysis service.

Estimates:
  - Direction (bullish/bearish) via multi-factor scoring
  - Magnitude (expected move %) via ATM straddle + historical average
  - Historical move accuracy per quarter
  - Walk-forward backtest of earnings-day trades
"""
import numpy as np
import pandas as pd
import requests
import yfinance as yf
from datetime import date, datetime, timedelta
from typing import Optional


# ── Earnings date fetching ─────────────────────────────────────────────────────

def get_earnings_dates_yf(ticker: str) -> list[dict]:
    """Historical earnings dates with EPS beat/miss data from yfinance."""
    try:
        tk = yf.Ticker(ticker)
        df = tk.earnings_dates
        if df is None or df.empty:
            return _earnings_from_history(tk)
        result = []
        for ts, row in df.iterrows():
            try:
                # yfinance 1.x returns tz-aware timestamps — convert to local date
                if hasattr(ts, "tz_convert"):
                    d = ts.tz_convert("America/New_York").date()
                elif hasattr(ts, "date"):
                    d = ts.date()
                else:
                    d = ts
                result.append({
                    "date":         str(d),
                    "eps_estimate": float(row["EPS Estimate"]) if pd.notna(row.get("EPS Estimate")) else None,
                    "eps_actual":   float(row["Reported EPS"]) if pd.notna(row.get("Reported EPS")) else None,
                    "surprise_pct": float(row["Surprise(%)"]) if pd.notna(row.get("Surprise(%)")) else None,
                })
            except Exception:
                continue
        return sorted(result, key=lambda x: x["date"])
    except Exception:
        try:
            return _earnings_from_history(yf.Ticker(ticker))
        except Exception:
            return []


def _earnings_from_history(tk) -> list[dict]:
    """
    Fallback: build earnings list from earnings_history (yfinance 1.x).
    The index is quarter-end dates; actual report date ≈ quarter-end + 30-45 days.
    """
    try:
        eh = tk.earnings_history
        if eh is None or eh.empty:
            return []
        result = []
        for ts, row in eh.iterrows():
            try:
                q_date = ts.date() if hasattr(ts, "date") else ts
                report_date = q_date + timedelta(days=35)
                actual = float(row["epsActual"])   if pd.notna(row.get("epsActual"))   else None
                est    = float(row["epsEstimate"]) if pd.notna(row.get("epsEstimate")) else None
                # Compute surprise from actual/estimate to avoid surprisePercent unit ambiguity
                surp = None
                if actual is not None and est is not None and est != 0:
                    surp = round((actual - est) / abs(est) * 100, 1)
                result.append({
                    "date":         str(report_date),
                    "eps_estimate": round(est,    2) if est    is not None else None,
                    "eps_actual":   round(actual, 2) if actual is not None else None,
                    "surprise_pct": surp,
                })
            except Exception:
                continue
        return sorted(result, key=lambda x: x["date"])
    except Exception:
        return []


_YF_HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}


def get_eps_fast(ticker: str, target_date: str) -> Optional[dict]:
    """
    Fast EPS lookup — tries sources in speed order:
      1. Yahoo Finance quoteSummary  (~5–30 min lag)
      2. Benzinga public calendar API (~2–10 min lag)
      3. Finviz snapshot table       (~15–60 min lag)
    Returns dict with eps_actual, eps_estimate, surprise_pct or None.
    """
    target = datetime.strptime(target_date, "%Y-%m-%d").date()

    # ── Source 1: Yahoo Finance quoteSummary earningsHistory ──────────────────
    try:
        url = f"https://query1.finance.yahoo.com/v10/finance/quoteSummary/{ticker}"
        r = requests.get(url, params={"modules": "earningsHistory"},
                         headers=_YF_HEADERS, timeout=10)
        if r.ok:
            hist = (r.json()
                     .get("quoteSummary", {})
                     .get("result", [{}])[0]
                     .get("earningsHistory", {})
                     .get("history", []))
            for item in reversed(hist):
                actual = (item.get("actual") or {}).get("raw")
                if actual is None:
                    continue
                q_ts = (item.get("quarter") or {}).get("raw")
                if q_ts:
                    q_date = datetime.utcfromtimestamp(q_ts).date()
                    if abs((target - q_date).days) <= 120:
                        surp = (item.get("surprisePercent") or {}).get("raw")
                        est  = (item.get("estimate") or {}).get("raw")
                        return {
                            "eps_actual":   round(actual, 2),
                            "eps_estimate": round(est, 2) if est is not None else None,
                            "surprise_pct": round(surp * 100, 1) if surp is not None else None,
                            "source":       "yahoo_fast",
                        }
    except Exception:
        pass

    # ── Source 2: Benzinga public calendar API ────────────────────────────────
    # Check target_date AND next day — some AMC reporters are listed with next-day date
    try:
        from datetime import timedelta as _td
        _next_day = str((datetime.strptime(target_date, "%Y-%m-%d") + _td(days=1)).date())
        bz_headers = {
            **_YF_HEADERS,
            "Accept":  "application/json",
            "Referer": "https://www.benzinga.com/",
        }
        r = requests.get(
            "https://www.benzinga.com/api/v1/calendar/earnings",
            params={
                "dateFrom":                 target_date,
                "dateTo":                   _next_day,
                "parameters[tickers]":      ticker,
                "parameters[importance]":   0,
            },
            headers=bz_headers,
            timeout=10,
        )
        if r.ok:
            for item in r.json().get("earnings", []):
                if item.get("ticker", "").upper() != ticker.upper():
                    continue
                actual = item.get("eps")
                est    = item.get("eps_est")
                if actual in (None, "", "-"):
                    continue
                actual = float(actual)
                est    = float(est) if est not in (None, "", "-") else None
                surp   = None
                if est is not None and est != 0:
                    surp = round((actual - est) / abs(est) * 100, 1)
                return {
                    "eps_actual":   round(actual, 2),
                    "eps_estimate": round(est, 2) if est is not None else None,
                    "surprise_pct": surp,
                    "source":       "benzinga",
                }
    except Exception:
        pass

    # ── Source 3: Finviz quote page snapshot table ────────────────────────────
    try:
        from bs4 import BeautifulSoup
        r = requests.get(f"https://finviz.com/quote.ashx?t={ticker}",
                         headers=_YF_HEADERS, timeout=10)
        if r.ok:
            soup = BeautifulSoup(r.text, "html.parser")
            cells  = soup.find_all("td", {"class": "snapshot-td2"})
            labels = soup.find_all("td", {"class": "snapshot-td2-cp"})
            table  = {l.get_text(strip=True): c.get_text(strip=True)
                      for l, c in zip(labels, cells)}
            eps_ttm = table.get("EPS (ttm)")
            if eps_ttm and eps_ttm not in ("-", "N/A", ""):
                try:
                    return {
                        "eps_actual":   float(eps_ttm),
                        "eps_estimate": None,
                        "surprise_pct": None,
                        "source":       "finviz",
                    }
                except ValueError:
                    pass
    except Exception:
        pass

    return None


def get_next_earnings_date(ticker: str) -> Optional[str]:
    """Next scheduled earnings date. Returns the earliest future date."""
    try:
        tk = yf.Ticker(ticker)
        cal = tk.calendar
        if isinstance(cal, dict):
            ed = cal.get("Earnings Date")
            if ed is not None:
                vals = list(ed) if hasattr(ed, "__iter__") and not isinstance(ed, str) else [ed]
                future_dates = sorted(
                    v.date() if hasattr(v, "date") else v
                    for v in vals
                )
                # Return the EARLIEST future date (yfinance gives a range; first = confirmed/est)
                for d in future_dates:
                    if d >= date.today():
                        return str(d)
        # fallback: first future date from earnings_dates
        df = tk.earnings_dates
        if df is not None and not df.empty:
            today = date.today()
            for ts in sorted(df.index):
                d = ts.date() if hasattr(ts, "date") else ts
                if d >= today:
                    return str(d)
    except Exception:
        pass
    return None


def get_last_reported_date(ticker: str, earnings_hist: list[dict]) -> Optional[str]:
    """
    Return the most recent past earnings date.
    Prefers eps_actual populated, but falls back to any past date in yfinance
    earnings_dates (yfinance often lags days/weeks populating eps_actual).
    """
    today = date.today()

    # First: any entry with actual EPS
    reported = [e for e in earnings_hist if e.get("eps_actual") is not None and e.get("date")]
    if reported:
        return max(reported, key=lambda e: e["date"])["date"]

    # Fallback: any past date in earnings_dates dataframe (no EPS yet)
    try:
        df = yf.Ticker(ticker).earnings_dates
        if df is not None and not df.empty:
            past = [
                ts.date() if hasattr(ts, "date") else ts
                for ts in df.index
                if (ts.date() if hasattr(ts, "date") else ts) < today
            ]
            if past:
                return str(max(past))
    except Exception:
        pass

    return None


# ── Price move calculation ─────────────────────────────────────────────────────

def _load_price_history(ticker: str) -> Optional[pd.DataFrame]:
    try:
        hist = yf.Ticker(ticker).history(period="3y", interval="1d")
        if hist.empty:
            return None
        hist.index = pd.to_datetime(hist.index).tz_localize(None)
        hist.columns = [c.lower() for c in hist.columns]
        return hist
    except Exception:
        return None


def enrich_with_price_moves(ticker: str, earnings_dates: list[dict]) -> list[dict]:
    """
    For every historical earnings date: gap %, full-day %, 5-day drift %.
    Appends prev_close, earn_open, earn_close, direction to each record.
    """
    hist = _load_price_history(ticker)
    if hist is None:
        return earnings_dates

    hist_dates = sorted(hist.index.date)
    idx_map = {d: i for i, d in enumerate(hist_dates)}

    enriched = []
    for e in earnings_dates:
        try:
            e_date = datetime.strptime(e["date"], "%Y-%m-%d").date()
            prev = [d for d in hist_dates if d < e_date]
            post = [d for d in hist_dates if d >= e_date]
            if not prev or not post:
                enriched.append(e); continue

            prev_d, earn_d = prev[-1], post[0]
            pr = hist[hist.index.date == prev_d].iloc[0]
            er = hist[hist.index.date == earn_d].iloc[0]

            prev_close = float(pr["close"])
            earn_open  = float(er["open"])
            earn_close = float(er["close"])

            gap_pct = round((earn_open - prev_close) / prev_close * 100, 2)
            day_pct = round((earn_close - prev_close) / prev_close * 100, 2)

            # 5-day post-earnings drift
            post5 = [d for d in hist_dates if d > earn_d][:5]
            five_day_pct = None
            if len(post5) == 5:
                five_close = float(hist[hist.index.date == post5[-1]].iloc[0]["close"])
                five_day_pct = round((five_close - earn_close) / earn_close * 100, 2)

            enriched.append({
                **e,
                "prev_close":   round(prev_close, 2),
                "earn_open":    round(earn_open, 2),
                "earn_close":   round(earn_close, 2),
                "gap_pct":      gap_pct,
                "day_pct":      day_pct,
                "five_day_pct": five_day_pct,
                "direction":    "UP" if day_pct > 0 else "DOWN",
            })
        except Exception:
            enriched.append(e)
    return enriched


# ── Expected move from options ─────────────────────────────────────────────────

def get_expected_move(ticker: str, earnings_date: Optional[str] = None) -> dict:
    """
    ATM straddle price ≈ expected move for the earnings event.
    Targets first expiry on-or-after earnings; falls back to nearest expiry.
    """
    try:
        tk = yf.Ticker(ticker)
        hist = tk.history(period="2d")
        if hist.empty:
            return {"error": "No price data"}
        current_price = float(hist["Close"].iloc[-1])

        exps = tk.options
        if not exps:
            return {"error": "No options"}
        today_s = date.today().isoformat()
        future = [e for e in exps if e > today_s]
        if not future:
            return {"error": "No future expirations"}

        # Pick first expiry after earnings (or nearest)
        if earnings_date:
            after = [e for e in future if e >= earnings_date]
            target_exp = after[0] if after else future[0]
        else:
            target_exp = future[0]

        chain = tk.option_chain(target_exp)
        calls, puts = chain.calls, chain.puts
        strikes = sorted(set(calls["strike"].tolist() + puts["strike"].tolist()))
        atm = min(strikes, key=lambda s: abs(s - current_price))

        c_row = calls[calls["strike"] == atm].iloc[0] if not calls[calls["strike"] == atm].empty else None
        p_row = puts[puts["strike"] == atm].iloc[0] if not puts[puts["strike"] == atm].empty else None
        if c_row is None or p_row is None:
            return {"error": "No ATM contracts"}

        def _mid(row):
            b, a = float(row.get("bid") or 0), float(row.get("ask") or 0)
            return (b + a) / 2 if b > 0 and a > 0 else float(row.get("lastPrice") or 0)

        c_mid, p_mid = _mid(c_row), _mid(p_row)
        straddle = c_mid + p_mid
        em_pct = straddle / current_price * 100
        dte = (datetime.strptime(target_exp, "%Y-%m-%d").date() - date.today()).days

        c_iv = float(c_row.get("impliedVolatility") or 0)
        p_iv = float(p_row.get("impliedVolatility") or 0)

        return {
            "expected_move_pct":    round(em_pct, 1),
            "expected_move_dollar": round(straddle, 2),
            "upside_target":        round(current_price * (1 + em_pct / 100), 2),
            "downside_target":      round(current_price * (1 - em_pct / 100), 2),
            "atm_strike":           atm,
            "exp_date":             target_exp,
            "dte":                  dte,
            "call_mid":             round(c_mid, 2),
            "put_mid":              round(p_mid, 2),
            "straddle_cost":        round(straddle, 2),
            "call_iv":              round(c_iv * 100, 1),
            "put_iv":               round(p_iv * 100, 1),
            "iv_skew":              round((c_iv - p_iv) * 100, 1),  # + = calls pricier = bullish lean
        }
    except Exception as ex:
        return {"error": str(ex)[:80]}


# ── Historical statistics ──────────────────────────────────────────────────────

def compute_history_stats(moves: list[dict]) -> dict:
    valid = [m for m in moves if m.get("day_pct") is not None]
    if not valid:
        return {}

    day_pcts  = [m["day_pct"] for m in valid]
    abs_moves = [abs(p) for p in day_pcts]
    ups       = [m for m in valid if m["day_pct"] > 0]
    beats     = [m for m in valid if (m.get("surprise_pct") or 0) > 0 and m.get("day_pct") is not None]
    beat_up   = [m for m in beats if m["day_pct"] > 0]
    five_day  = [m["five_day_pct"] for m in valid if m.get("five_day_pct") is not None]

    return {
        "count":             len(valid),
        "avg_abs_move":      round(float(np.mean(abs_moves)), 1),
        "max_move":          round(float(max(abs_moves)), 1),
        "avg_move":          round(float(np.mean(day_pcts)), 1),
        "bull_rate":         round(len(ups) / len(valid) * 100, 1),
        "beat_rate":         round(len(beats) / len(valid) * 100, 1) if valid else 0,
        "beat_then_up_rate": round(len(beat_up) / len(beats) * 100, 1) if beats else None,
        "avg_5d_drift":      round(float(np.mean(five_day)), 1) if five_day else None,
        "last4_avg_move":    round(float(np.mean([abs(m["day_pct"]) for m in valid[-4:]])), 1) if len(valid) >= 4 else None,
    }


# ── Estimate revisions & revenue beat ─────────────────────────────────────────

def get_estimate_revisions(ticker: str) -> dict:
    """
    Fetch EPS estimate revision trend and forward revenue growth via Yahoo Finance
    quoteSummary earningsTrend module.

    Returns:
        est_current   — current consensus EPS estimate for upcoming quarter
        est_90d_ago   — what that estimate was 90 days ago
        revision_pct  — % change: (current − 90d) / |90d|  (positive = raised)
        up_30d        — # analysts who raised estimate in last 30 days
        down_30d      — # analysts who cut estimate in last 30 days
        net_30d       — up_30d − down_30d
        rev_growth_est — forward revenue growth estimate %
        analyst_count — number of analysts covering earnings
    """
    try:
        r = requests.get(
            f"https://query1.finance.yahoo.com/v10/finance/quoteSummary/{ticker}",
            params={"modules": "earningsTrend"},
            headers=_YF_HEADERS,
            timeout=10,
        )
        if not r.ok:
            return {}
        trends = (r.json()
                   .get("quoteSummary", {})
                   .get("result", [{}])[0]
                   .get("earningsTrend", {})
                   .get("trend", []))

        # Take current quarter ("0q") or next quarter ("1q") — whichever is available first
        current = next((t for t in trends if t.get("period") in ("0q", "1q")), None)
        if not current:
            return {}

        def _raw(d):
            return (d or {}).get("raw")

        eps_trend = current.get("epsTrend", {})
        eps_rev   = current.get("epsRevisions", {})
        rev_est   = current.get("revenueEstimate", {})
        eps_est   = current.get("earningsEstimate", {})

        est_cur    = _raw(eps_trend.get("current"))
        est_90d    = _raw(eps_trend.get("90daysAgo"))
        est_30d    = _raw(eps_trend.get("30daysAgo"))
        up_30d     = int(_raw(eps_rev.get("upLast30days")) or 0)
        dn_30d     = int(_raw(eps_rev.get("downLast30days")) or 0)
        up_7d      = int(_raw(eps_rev.get("upLast7days")) or 0)
        dn_7d      = int(_raw(eps_rev.get("downLast7days")) or 0)
        rev_growth = _raw(rev_est.get("growth"))
        n_analysts = _raw(eps_est.get("numberOfAnalysts"))

        revision_pct = None
        if est_cur is not None and est_90d and est_90d != 0:
            revision_pct = round((est_cur - est_90d) / abs(est_90d) * 100, 1)

        return {
            "est_current":    round(est_cur, 2) if est_cur is not None else None,
            "est_90d_ago":    round(est_90d, 2) if est_90d is not None else None,
            "est_30d_ago":    round(est_30d, 2) if est_30d is not None else None,
            "revision_pct":   revision_pct,
            "up_7d":          up_7d,
            "up_30d":         up_30d,
            "down_7d":        dn_7d,
            "down_30d":       dn_30d,
            "net_30d":        up_30d - dn_30d,
            "rev_growth_est": round(rev_growth * 100, 1) if rev_growth is not None else None,
            "analyst_count":  int(n_analysts) if n_analysts is not None else None,
        }
    except Exception:
        return {}


def get_revenue_beat_rate(ticker: str, earnings_hist: list[dict]) -> dict:
    """
    Compute historical revenue beat rate using quarterly financials.
    'Beat' proxy = quarter had positive YoY revenue growth (consistent growers
    tend to beat revenue estimates).
    Returns beat_rate_pct, avg_rev_growth_pct, quarters_checked.
    """
    try:
        tk  = yf.Ticker(ticker)
        fin = tk.quarterly_financials
        if fin is None or fin.empty:
            return {}

        # Find the 'Total Revenue' row (yfinance naming varies)
        rev_row = None
        for lbl in ["Total Revenue", "Revenue", "TotalRevenue"]:
            if lbl in fin.index:
                rev_row = fin.loc[lbl].dropna()
                break
        if rev_row is None or len(rev_row) < 5:
            return {}

        # Sort ascending by date
        rev_row = rev_row.sort_index()
        dates   = rev_row.index.tolist()

        # YoY growth for each quarter (need 4-quarter lookback)
        growths, beats = [], []
        for i in range(4, len(dates)):
            curr = float(rev_row.iloc[i])
            year_ago = float(rev_row.iloc[i - 4])
            if year_ago <= 0:
                continue
            g = (curr - year_ago) / year_ago * 100
            growths.append(g)
            beats.append(g > 0)

        if not growths:
            return {}

        return {
            "rev_beat_rate":    round(sum(beats) / len(beats) * 100, 1),
            "avg_rev_growth":   round(float(np.mean(growths)), 1),
            "quarters_checked": len(growths),
        }
    except Exception:
        return {}


# ── News sentiment ────────────────────────────────────────────────────────────

_BULL_STRONG = {
    "beats", "beat", "surpasses", "record", "raises guidance", "raised guidance",
    "upgrade", "upgraded", "outperform", "blowout", "crushes", "top estimates",
    "above estimates", "above expectations", "raises forecast", "strong quarter",
    "strong results", "raises outlook", "record revenue", "record earnings",
}
_BULL_MILD = {
    "growth", "positive", "bullish", "buy", "overweight", "gains", "jumps",
    "rallies", "strong", "optimistic", "opportunity", "better than expected",
    "solid", "robust", "accelerating",
}
_BEAR_STRONG = {
    "misses", "miss", "disappoints", "disappointing", "cuts guidance", "cut guidance",
    "downgrade", "downgraded", "underperform", "below expectations", "below estimates",
    "warns", "warning", "layoffs", "restructuring", "loss widens", "guidance cut",
    "lowers guidance", "lowered guidance", "revenue miss", "eps miss",
}
_BEAR_MILD = {
    "concern", "risk", "bearish", "sell", "underweight", "slowing", "weak",
    "falling", "decline", "drops", "selloff", "headwinds", "uncertainty",
    "disappointing", "struggles",
}


def _parse_news_item(item: dict) -> tuple[str, str, float]:
    """
    Handle both yfinance <1.0 (flat) and >=1.0 (nested under 'content') structures.
    Returns (title, publisher, pub_timestamp).
    """
    if "content" in item:
        # yfinance 1.x structure
        c = item["content"]
        title     = (c.get("title") or "").strip()
        publisher = (c.get("provider") or {}).get("displayName", "")
        pub_str   = c.get("pubDate") or c.get("displayTime") or ""
        pub: float = 0.0
        if pub_str:
            try:
                from datetime import timezone as tz
                pub = datetime.fromisoformat(pub_str.replace("Z", "+00:00")).timestamp()
            except Exception:
                pass
    else:
        # yfinance legacy structure
        title     = (item.get("title") or "").strip()
        publisher = item.get("publisher", "")
        pub       = float(item.get("providerPublishTime") or 0)
    return title, publisher, pub


def get_news_sentiment(ticker: str) -> dict:
    """
    Fetch recent Yahoo Finance headlines and score sentiment via keyword heuristic.
    Each headline scored -2..+2; aggregate average mapped to factor signal.
    Handles both yfinance <1.0 (flat) and >=1.0 (nested content) structures.
    """
    try:
        from datetime import timezone
        news = yf.Ticker(ticker).news or []
        cutoff = datetime.now(timezone.utc).timestamp() - 7 * 86400  # 7-day window

        scored = []
        for item in news[:30]:
            title, publisher, pub = _parse_news_item(item)
            if pub and pub < cutoff:
                continue
            if not title:
                continue
            tl = title.lower()
            bull = sum(2 for kw in _BULL_STRONG if kw in tl) + \
                   sum(1 for kw in _BULL_MILD   if kw in tl)
            bear = sum(2 for kw in _BEAR_STRONG if kw in tl) + \
                   sum(1 for kw in _BEAR_MILD   if kw in tl)
            net = max(-2, min(2, bull - bear))
            scored.append({
                "title":     title,
                "publisher": publisher,
                "score":     net,
            })

        if not scored:
            return {}

        avg       = sum(s["score"] for s in scored) / len(scored)
        bull_cnt  = sum(1 for s in scored if s["score"] > 0)
        bear_cnt  = sum(1 for s in scored if s["score"] < 0)
        neut_cnt  = len(scored) - bull_cnt - bear_cnt

        if   avg >=  1.0: sig, label = +2, "BULLISH — strong positive coverage"
        elif avg >=  0.3: sig, label = +1, "LEAN BULL — mostly positive news"
        elif avg <= -1.0: sig, label = -2, "BEARISH — heavy negative coverage"
        elif avg <= -0.3: sig, label = -1, "LEAN BEAR — negative tone in news"
        else:             sig, label =  0, "NEUTRAL — mixed or no clear signal"

        return {
            "factor_score":  sig,
            "label":         label,
            "avg_score":     round(avg, 2),
            "bull_count":    bull_cnt,
            "bear_count":    bear_cnt,
            "neutral_count": neut_cnt,
            "total":         len(scored),
            "headlines":     scored[:5],
        }
    except Exception:
        return {}


# ── Direction signal scoring ───────────────────────────────────────────────────

def get_direction_score(ticker: str, current_price: float,
                        hist: pd.DataFrame, moves: list[dict],
                        fundamentals: dict, expected_move: dict,
                        revisions: dict = None,
                        rev_beat: dict = None,
                        news_sentiment: dict = None) -> dict:
    """
    Multi-factor earnings direction score (9 factors, score ≈ −13 to +13).
    Factors 1-6: price, history, IV, analyst, EPS growth, squeeze.
    Factor 7: estimate revision trend.
    Factor 8: revenue beat rate.
    Factor 9: news sentiment (keyword heuristic on recent headlines).
    """
    signals, factors = [], []

    # ── Factor 1: Pre-earnings drift (5-day price trend) ──────────────────────
    if len(hist) >= 6:
        close = hist["close"] if "close" in hist.columns else hist["Close"]
        pre_drift = (float(close.iloc[-1]) - float(close.iloc[-6])) / float(close.iloc[-6]) * 100
        if   pre_drift >  5: sig, label = +2, "BULLISH"
        elif pre_drift >  1: sig, label = +1, "LEAN BULL"
        elif pre_drift < -5: sig, label = -2, "BEARISH"
        elif pre_drift < -1: sig, label = -1, "LEAN BEAR"
        else:                sig, label =  0, "NEUTRAL"
        signals.append(sig)
        factors.append({"name": "Pre-earnings drift (5d)", "value": f"{pre_drift:+.1f}%", "signal": label, "score": sig})

    # ── Factor 2: Historical beat→up pattern ──────────────────────────────────
    valid_moves = [m for m in moves if m.get("day_pct") is not None]
    if len(valid_moves) >= 3:
        beats    = [m for m in valid_moves if (m.get("surprise_pct") or 0) > 0]
        beat_up  = [m for m in beats if m["day_pct"] > 0]
        bull_cnt = sum(1 for m in valid_moves if m["day_pct"] > 0)
        bull_rate = bull_cnt / len(valid_moves)
        beat_up_rate = len(beat_up) / len(beats) if beats else 0.5

        if   beat_up_rate > 0.75 and len(beats) >= 3: sig, label = +2, "BULLISH"
        elif bull_rate > 0.65:                          sig, label = +1, "LEAN BULL"
        elif bull_rate < 0.35:                          sig, label = -1, "LEAN BEAR"
        elif beat_up_rate < 0.35 and len(beats) >= 3:  sig, label = -2, "BEARISH"
        else:                                           sig, label =  0, "NEUTRAL"
        signals.append(sig)
        factors.append({
            "name": "Historical beat→direction",
            "value": f"{bull_rate*100:.0f}% bull | {beat_up_rate*100:.0f}% beat→up",
            "signal": label, "score": sig,
        })

    # ── Factor 3: IV skew (calls pricier than puts = market leans bullish) ────
    skew = expected_move.get("iv_skew")
    if skew is not None and not expected_move.get("error"):
        if   skew >  3: sig, label = +1, "BULLISH"
        elif skew < -3: sig, label = -1, "BEARISH"
        else:           sig, label =  0, "NEUTRAL"
        signals.append(sig)
        factors.append({"name": "IV skew (call − put IV)", "value": f"{skew:+.1f}%", "signal": label, "score": sig})

    # ── Factor 4: Analyst consensus ───────────────────────────────────────────
    rec = (fundamentals.get("recommendation") or "").lower()
    n   = fundamentals.get("num_analysts") or 0
    if n >= 3:
        if   rec in ("buy", "strong_buy"):  sig, label = +1, "BULLISH"
        elif rec in ("sell", "strong_sell"): sig, label = -1, "BEARISH"
        else:                                sig, label =  0, "NEUTRAL"
        signals.append(sig)
        factors.append({"name": "Analyst consensus", "value": f"{rec.replace('_',' ').title()} ({n})", "signal": label, "score": sig})

    # ── Factor 5: EPS growth momentum ─────────────────────────────────────────
    eg = fundamentals.get("earnings_growth")
    if eg is not None:
        if   eg >  0.25: sig, label = +1, "BULLISH"
        elif eg < -0.15: sig, label = -1, "BEARISH"
        else:            sig, label =  0, "NEUTRAL"
        signals.append(sig)
        factors.append({"name": "Earnings growth (YoY)", "value": f"{eg*100:+.0f}%", "signal": label, "score": sig})

    # ── Factor 6: Short squeeze setup pre-earnings ───────────────────────────
    short_pct  = fundamentals.get("short_pct_float")
    days_cover = fundamentals.get("short_ratio")      # days to cover
    squeeze_setup = None

    if short_pct is not None:
        close_series = hist["close"] if "close" in hist.columns else hist["Close"]
        pre_drift_val = (float(close_series.iloc[-1]) - float(close_series.iloc[-6])) / float(close_series.iloc[-6]) * 100 \
                        if len(hist) >= 6 else 0

        # Beat rate from history
        valid_moves  = [m for m in moves if m.get("day_pct") is not None]
        hist_beats   = [m for m in valid_moves if (m.get("surprise_pct") or 0) > 0]
        hist_beat_rt = len(hist_beats) / len(valid_moves) if valid_moves else 0

        squeeze_score = 0
        squeeze_notes = []

        if short_pct >= 20:
            squeeze_score += 2
            squeeze_notes.append(f"{short_pct:.1f}% float short (very high)")
        elif short_pct >= 10:
            squeeze_score += 1
            squeeze_notes.append(f"{short_pct:.1f}% float short")

        if days_cover is not None and days_cover >= 3:
            squeeze_score += 1
            squeeze_notes.append(f"{days_cover:.1f}d to cover")

        if hist_beat_rt >= 0.65:
            squeeze_score += 1
            squeeze_notes.append(f"{hist_beat_rt*100:.0f}% hist beat rate")

        if pre_drift_val > 2:
            squeeze_score += 1
            squeeze_notes.append(f"+{pre_drift_val:.1f}% pre-drift (longs accumulating)")

        if squeeze_score >= 3:
            sig, label = +2, "SQUEEZE SETUP — high short + beat history + drift"
        elif squeeze_score >= 2:
            sig, label = +1, "POTENTIAL SQUEEZE — elevated short interest"
        else:
            sig = 0
            label = None

        if sig > 0:
            signals.append(sig)
            factors.append({
                "name":    "Pre-earnings squeeze setup",
                "value":   " | ".join(squeeze_notes),
                "signal":  label,
                "score":   sig,
                "squeeze": True,
            })
            squeeze_setup = {
                "score":      squeeze_score,
                "short_pct":  short_pct,
                "days_cover": days_cover,
                "beat_rate":  round(hist_beat_rt * 100, 1),
                "pre_drift":  round(pre_drift_val, 1),
                "notes":      squeeze_notes,
                "label":      label,
            }

    # ── Factor 7: Estimate revision trend ────────────────────────────────────
    rev = revisions or {}
    rev_pct  = rev.get("revision_pct")
    net_30d  = rev.get("net_30d", 0)
    if rev_pct is not None or net_30d:
        if   rev_pct is not None and rev_pct >  5 or net_30d >= 3:
            sig, label = +2, "BULLISH — estimates raised significantly"
        elif rev_pct is not None and rev_pct >  2 or net_30d >= 1:
            sig, label = +1, "LEAN BULL — estimates edging up"
        elif rev_pct is not None and rev_pct < -5 or net_30d <= -3:
            sig, label = -2, "BEARISH — estimates cut significantly"
        elif rev_pct is not None and rev_pct < -2 or net_30d <= -1:
            sig, label = -1, "LEAN BEAR — estimates lowered"
        else:
            sig, label =  0, "NEUTRAL — estimates stable"
        signals.append(sig)
        up   = rev.get("up_30d", 0)
        down = rev.get("down_30d", 0)
        val  = (f"{rev_pct:+.1f}% in 90d" if rev_pct is not None else "")
        if up or down:
            val += f"  ↑{up} ↓{down} in 30d"
        factors.append({"name": "EPS estimate revisions", "value": val.strip(), "signal": label, "score": sig})

    # ── Factor 8: Revenue beat rate ───────────────────────────────────────────
    rb = rev_beat or {}
    rbr = rb.get("rev_beat_rate")
    rg  = rb.get("avg_rev_growth") or rev.get("rev_growth_est")
    if rbr is not None or rg is not None:
        if   (rbr or 0) >= 80 or (rg or 0) >  15:
            sig, label = +2, "BULLISH — strong consistent revenue growth"
        elif (rbr or 0) >= 60 or (rg or 0) >   5:
            sig, label = +1, "LEAN BULL — revenue growing"
        elif (rbr or 0) <  40 or (rg or 0) <  -5:
            sig, label = -1, "LEAN BEAR — revenue declining"
        else:
            sig, label =  0, "NEUTRAL"
        signals.append(sig)
        val_parts = []
        if rbr is not None: val_parts.append(f"{rbr:.0f}% qtrs beat ({rb.get('quarters_checked',0)}q)")
        if rg  is not None: val_parts.append(f"avg growth {rg:+.1f}%")
        factors.append({"name": "Revenue beat rate", "value": " | ".join(val_parts), "signal": label, "score": sig})

    # ── Factor 9: News sentiment (keyword heuristic) ─────────────────────────
    ns = news_sentiment or {}
    ns_sig = ns.get("factor_score")
    if ns_sig is not None:
        signals.append(ns_sig)
        bull_cnt = ns.get("bull_count", 0)
        bear_cnt = ns.get("bear_count", 0)
        total_h  = ns.get("total", 0)
        val = f"{bull_cnt}↑ {bear_cnt}↓ of {total_h} headlines"
        factors.append({"name": "News sentiment (7d)", "value": val, "signal": ns.get("label", ""), "score": ns_sig})

    total = sum(signals)
    # Thresholds scaled for 9 factors (max ≈ +13)
    if   total >= 5: direction, confidence = "BULLISH",      "HIGH"
    elif total >= 3: direction, confidence = "LEAN BULLISH",  "MEDIUM"
    elif total <= -5:direction, confidence = "BEARISH",       "HIGH"
    elif total <= -3:direction, confidence = "LEAN BEARISH",  "MEDIUM"
    else:            direction, confidence = "NEUTRAL",       "LOW"

    return {
        "score":         total,
        "direction":     direction,
        "confidence":    confidence,
        "factors":       factors,
        "squeeze_setup": squeeze_setup,
    }


# ── Post-earnings analysis ────────────────────────────────────────────────────

def get_post_earnings_analysis(ticker: str, earnings_hist: list[dict],
                                hist: pd.DataFrame, last_reported_date: str,
                                current_price: float) -> dict:
    """
    Analyze the most recent earnings reaction and score the next 5-day move.
    Called when recently_reported=True.
    """
    # Find the matching enriched record
    last = next((e for e in reversed(earnings_hist)
                 if e.get("date") == last_reported_date and e.get("day_pct") is not None), None)

    # If yfinance hasn't added it yet, build from raw price data
    if last is None:
        try:
            hist_dates = sorted(hist.index.date)
            lr = datetime.strptime(last_reported_date, "%Y-%m-%d").date()
            earn_days  = [d for d in hist_dates if d >= lr]
            prev_days  = [d for d in hist_dates if d < lr]
            if earn_days and prev_days:
                er = hist[hist.index.date == earn_days[0]].iloc[0]
                pr = hist[hist.index.date == prev_days[-1]].iloc[0]
                prev_close = float(pr["close"])
                earn_open  = float(er["open"])
                earn_close = float(er["close"])
                gap_pct    = round((earn_open - prev_close) / prev_close * 100, 2)
                day_pct    = round((earn_close - prev_close) / prev_close * 100, 2)
                last = {"date": last_reported_date, "gap_pct": gap_pct, "day_pct": day_pct,
                        "direction": "UP" if day_pct > 0 else "DOWN", "eps_surprise": None}
        except Exception:
            pass

    if last is None:
        return {"error": "Could not retrieve recent earnings data"}

    gap_pct  = last.get("gap_pct") or 0.0
    day_pct  = last.get("day_pct") or 0.0
    surprise = last.get("surprise_pct") or last.get("eps_surprise")
    direction = "UP" if day_pct > 0 else "DOWN"

    # ── What happened ─────────────────────────────────────────────────────────
    if   day_pct >  8: reaction = "Huge bullish gap — strong beat/guidance"
    elif day_pct >  4: reaction = "Solid bullish reaction"
    elif day_pct >  1: reaction = "Mildly positive — cautious optimism"
    elif day_pct < -8: reaction = "Severe selloff — major disappointment"
    elif day_pct < -4: reaction = "Clear bearish reaction"
    elif day_pct < -1: reaction = "Mild selling — uncertainty"
    else:              reaction = "Muted reaction — market undecided"

    # ── Why it moved ──────────────────────────────────────────────────────────
    why_parts = []
    if surprise is not None:
        if   surprise >  15: why_parts.append(f"massive EPS beat (+{surprise:.1f}%)")
        elif surprise >   5: why_parts.append(f"EPS beat (+{surprise:.1f}%)")
        elif surprise >   0: why_parts.append(f"slight EPS beat (+{surprise:.1f}%)")
        elif surprise >  -5: why_parts.append(f"slight EPS miss ({surprise:.1f}%)")
        else:                why_parts.append(f"EPS miss ({surprise:.1f}%)")

    if gap_pct != 0:
        if abs(day_pct) > abs(gap_pct) * 1.2 and (gap_pct > 0) == (day_pct > 0):
            why_parts.append("bought/sold through the session (conviction)")
        elif abs(day_pct) < abs(gap_pct) * 0.5:
            why_parts.append("faded significantly from open (weak follow-through)")
        elif (gap_pct > 0) != (day_pct > 0):
            why_parts.append("gap reversal during session")

    if surprise is not None and surprise > 0 and day_pct < 0:
        why_parts.append("beat but guidance may have disappointed (sold on news)")
    elif surprise is not None and surprise < 0 and day_pct > 0:
        why_parts.append("miss but possibly beat on revenue or raised outlook")

    why = "; ".join(why_parts) if why_parts else "Exact catalyst unclear from available data"

    # ── Score next 5-day move ─────────────────────────────────────────────────
    signals, factors = [], []

    # Factor 1: Reaction quality → continuation bias
    if   day_pct >  6: sig, label = +2, "BULLISH — strong gap rarely fully reverses day 1"
    elif day_pct >  2: sig, label = +1, "LEAN BULL — positive reaction"
    elif day_pct < -6: sig, label = -2, "BEARISH — selling pressure likely continues"
    elif day_pct < -2: sig, label = -1, "LEAN BEAR — negative reaction"
    else:              sig, label =  0, "NEUTRAL — no clear momentum"
    signals.append(sig); factors.append({"name": "Earnings day reaction", "value": f"{day_pct:+.1f}%", "signal": label, "score": sig})

    # Factor 2: Historical 5d drift after same reaction direction
    same_dir = [e for e in earnings_hist if e.get("direction") == direction and e.get("five_day_pct") is not None]
    if len(same_dir) >= 2:
        avg_5d = float(np.mean([e["five_day_pct"] for e in same_dir]))
        if   avg_5d >  3: sig, label = +1, f"Hist: avg +{avg_5d:.1f}% in 5d after {direction} day"
        elif avg_5d < -3: sig, label = -1, f"Hist: avg {avg_5d:.1f}% in 5d after {direction} day"
        else:             sig, label =  0, f"Hist: flat avg {avg_5d:+.1f}% in 5d after {direction} day"
        signals.append(sig); factors.append({"name": "Historical 5d post-earnings drift", "value": f"{avg_5d:+.1f}%", "signal": label, "score": sig})

    # Factor 3: EPS beat/miss aligned with reaction → continuation
    if surprise is not None:
        if surprise > 0 and day_pct > 0:
            sig, label = +1, "Beat + up = bullish continuation setup"
        elif surprise < 0 and day_pct < 0:
            sig, label = -1, "Miss + down = bearish continuation"
        elif surprise > 5 and day_pct < 0:
            sig, label = +1, "Beat but sold — likely buy-the-dip opportunity"
        else:
            sig, label = 0, "Mixed signals"
        signals.append(sig); factors.append({"name": "Beat/miss vs reaction", "value": f"EPS surprise {surprise:+.1f}%", "signal": label, "score": sig})

    # Factor 4: Large gap fill risk
    if abs(gap_pct) > 7:
        opp = -1 if gap_pct > 0 else +1
        signals.append(opp)
        factors.append({"name": "Gap fill risk", "value": f"{gap_pct:+.1f}% gap", "signal": "Counter-pressure — large gaps often partially fill", "score": opp})

    # Factor 5: Post-earnings IV crush → directional options are cheap now
    iv_note = "IV crushed post-earnings — directional options are cheaper now"

    total = sum(signals)
    if   total >=  3: post_dir, conf = "BULLISH",      "HIGH"
    elif total >=  1: post_dir, conf = "LEAN BULLISH",  "MEDIUM"
    elif total <= -3: post_dir, conf = "BEARISH",       "HIGH"
    elif total <= -1: post_dir, conf = "LEAN BEARISH",  "MEDIUM"
    else:             post_dir, conf = "NEUTRAL",       "LOW"

    # ── Suggested play (post-IV-crush) ────────────────────────────────────────
    if conf in ("HIGH", "MEDIUM"):
        if "BULL" in post_dir:
            play = (f"Bull call spread or long call (IV crushed — premiums cheap). "
                    f"Target +{abs(day_pct)*0.5:.1f}–{abs(day_pct):.1f}% from here in 5–10d. "
                    f"Stop: close back below earnings close.")
        else:
            play = (f"Bear put spread or long put (IV crushed — premiums cheap). "
                    f"Target -{abs(day_pct)*0.5:.1f}–{abs(day_pct):.1f}% from here in 5–10d. "
                    f"Stop: close back above earnings open.")
    else:
        play = "Unclear direction — consider waiting for a 2–3 day consolidation before entering."

    return {
        "earnings_date":        last_reported_date,
        "gap_pct":              gap_pct,
        "day_pct":              day_pct,
        "eps_surprise":         surprise,
        "reaction":             reaction,
        "why":                  why,
        "next_move_score":      total,
        "next_move_direction":  post_dir,
        "next_move_confidence": conf,
        "factors":              factors,
        "iv_note":              iv_note,
        "suggested_play":       play,
    }


# ── Earnings day backtest ──────────────────────────────────────────────────────

def run_earnings_backtest(ticker: str) -> dict:
    """
    Walk-forward backtest of earnings-day trades.
    Signal: pre-earnings 5-day drift direction.
    Entry: prev close.  Exit: earnings day close.
    """
    try:
        hist = _load_price_history(ticker)
        if hist is None or len(hist) < 40:
            return {"error": "Insufficient price data"}

        earnings_dates = get_earnings_dates_yf(ticker)
        moves = enrich_with_price_moves(ticker, earnings_dates)
        valid = [m for m in moves if m.get("day_pct") is not None and m.get("prev_close")]
        if len(valid) < 2:
            return {"error": "Not enough historical earnings data"}

        hist_dates = sorted(hist.index.date)
        trades = []

        for e in valid:
            e_date = datetime.strptime(e["date"], "%Y-%m-%d").date()
            pre = [d for d in hist_dates if d < e_date]
            if len(pre) < 10:
                continue

            # Signal: 5-day pre-earnings price change
            p5 = pre[-5] if len(pre) >= 5 else pre[0]
            close_now  = float(hist[hist.index.date == pre[-1]].iloc[0]["close"])
            close_5ago = float(hist[hist.index.date == p5].iloc[0]["close"])
            pre_drift  = (close_now - close_5ago) / close_5ago * 100

            # Volume context: is vol increasing into earnings?
            vol_window = hist[hist.index.date <= pre[-1]].tail(20)["volume"]
            vol_ratio  = float(vol_window.iloc[-3:].mean() / vol_window.iloc[:-3].mean()) if len(vol_window) > 3 else 1.0

            direction  = "LONG" if pre_drift >= 0 else "SHORT"
            entry      = e["prev_close"]
            exit_p     = e["earn_close"]

            pnl = (exit_p - entry) / entry * 100 if direction == "LONG" \
                  else (entry - exit_p) / entry * 100

            win      = pnl > 0
            surprise = e.get("surprise_pct")
            gap_pct  = e.get("gap_pct") or 0
            day_pct  = e["day_pct"]

            reasons = []
            if win:
                if surprise is not None:
                    if direction == "LONG" and surprise > 0:
                        reasons.append("EPS beat aligned")
                    elif direction == "SHORT" and surprise < 0:
                        reasons.append("EPS miss aligned")
                if abs(pre_drift) >= 5:
                    reasons.append("strong pre-drift momentum")
                elif abs(pre_drift) >= 2:
                    reasons.append("clear pre-drift signal")
                if vol_ratio >= 1.5:
                    reasons.append("high volume conviction")
                if direction == "LONG" and gap_pct > 0 and day_pct > 0:
                    reasons.append("gap held & extended")
                elif direction == "SHORT" and gap_pct < 0 and day_pct < 0:
                    reasons.append("gap held & extended")
                if not reasons:
                    reasons.append("momentum followed through")
            else:
                if surprise is not None:
                    if direction == "LONG" and surprise < 0:
                        reasons.append("EPS miss")
                    elif direction == "SHORT" and surprise > 0:
                        reasons.append("EPS beat → squeeze")
                if abs(pre_drift) < 2:
                    reasons.append("weak signal")
                if direction == "LONG" and gap_pct > 0 and day_pct < 0:
                    reasons.append("gap fade")
                elif direction == "SHORT" and gap_pct < 0 and day_pct > 0:
                    reasons.append("gap fade")
                if not reasons:
                    reasons.append("counter-trend")
            trade_reason = ", ".join(reasons)

            beat = (surprise or 0) > 0
            # rev_aligned: EPS result agrees with direction AND signal was strong.
            # Proxy for "estimate revisions were positive" — best available from history.
            rev_aligned = (
                (direction == "LONG"  and beat and pre_drift > 2) or
                (direction == "SHORT" and not beat and pre_drift < -2)
            )

            trades.append({
                "date":         e["date"],
                "direction":    direction,
                "pre_drift":    round(pre_drift, 1),
                "vol_ratio":    round(vol_ratio, 2),
                "entry":        entry,
                "exit":         exit_p,
                "gap_pct":      gap_pct,
                "day_pct":      day_pct,
                "pnl_pct":      round(pnl, 2),
                "win":          win,
                "eps_surprise": surprise,
                "beat":         beat,
                "rev_aligned":  rev_aligned,
                "reason":       trade_reason,
            })

        if not trades:
            return {"error": "No valid trades built"}

        def _build_stats(subset: list[dict], eq_start: float = 100.0) -> tuple[dict, list[float]]:
            if not subset:
                return {}, [eq_start]
            w = [t for t in subset if t["win"]]
            l = [t for t in subset if not t["win"]]
            pnls    = [t["pnl_pct"] for t in subset]
            abs_day = [abs(t["day_pct"]) for t in subset]
            eq = [eq_start]
            for t in subset:
                eq.append(round(eq[-1] * (1 + t["pnl_pct"] / 100), 2))
            peak, max_dd = eq_start, 0.0
            for v in eq:
                if v > peak: peak = v
                dd = (peak - v) / peak * 100
                if dd > max_dd: max_dd = dd
            gw = sum(t["pnl_pct"] for t in w) if w else 0
            gl = abs(sum(t["pnl_pct"] for t in l)) if l else 1
            return {
                "total":         len(subset),
                "wins":          len(w),
                "losses":        len(l),
                "win_rate":      round(len(w) / len(subset) * 100, 1),
                "avg_win":       round(float(np.mean([t["pnl_pct"] for t in w])), 2) if w else 0,
                "avg_loss":      round(float(np.mean([t["pnl_pct"] for t in l])), 2) if l else 0,
                "profit_factor": round(gw / gl, 2),
                "total_return":  round(eq[-1] - eq_start, 1),
                "max_dd":        round(max_dd, 1),
                "avg_move":      round(float(np.mean(abs_day)), 1),
                "avg_pnl":       round(float(np.mean(pnls)), 2),
            }, eq

        all_stats,  all_equity  = _build_stats(trades)
        filt_trades = [t for t in trades if t["rev_aligned"]]
        filt_stats, filt_equity = _build_stats(filt_trades)

        return {
            "trades":          trades,
            "equity":          all_equity,
            "stats":           all_stats,
            "filtered_trades": filt_trades,
            "filtered_equity": filt_equity,
            "filtered_stats":  filt_stats,
        }
    except Exception as ex:
        return {"error": str(ex)[:120]}


# ── Popular tickers for earnings-calendar scanning ────────────────────────────

POPULAR_TICKERS = [
    # Mega-cap tech
    "AAPL", "MSFT", "NVDA", "AMZN", "GOOGL", "META", "TSLA",
    # Financials
    "JPM", "V", "MA", "BAC", "GS", "MS", "WFC", "AXP", "BLK", "C",
    # Healthcare
    "UNH", "LLY", "JNJ", "MRK", "ABBV", "ABT", "TMO", "DHR", "AMGN",
    # Consumer / retail
    "WMT", "PG", "KO", "PEP", "MCD", "SBUX", "NKE", "COST", "HD",
    # Energy / industrial
    "XOM", "CVX", "CAT", "DE", "HON", "BA", "LMT", "RTX", "GE", "MMM",
    # Semiconductors
    "AVGO", "TXN", "QCOM", "AMD", "INTC", "MU", "AMAT", "LRCX", "ARM", "MRVL", "SMCI",
    # Software / cloud
    "ORCL", "ADBE", "CRM", "CSCO", "INTU", "IBM", "ACN",
    # Growth / fintech / consumer
    "NFLX", "UBER", "PYPL", "SHOP", "PLTR", "DDOG", "NET", "SNOW", "PANW", "CRWD",
    "TTD", "COIN", "RBLX", "SNAP", "DIS", "CMCSA", "T", "VZ",
    # Small / mid growth
    "F", "GM", "PM", "CELH", "HIMS", "DUOL", "U",
]


def _check_ticker_for_date(ticker: str, target_date: str) -> Optional[str]:
    """Return ticker if it reported earnings on/around target_date (±1 day), else None."""
    try:
        dates = get_earnings_dates_yf(ticker)
        target = datetime.strptime(target_date, "%Y-%m-%d").date()
        for e in dates:
            d = datetime.strptime(e["date"], "%Y-%m-%d").date()
            if abs((d - target).days) <= 1:
                return ticker
    except Exception:
        pass
    return None


def find_earnings_reporters(target_date: str) -> list[str]:
    """Scan POPULAR_TICKERS in parallel and return those that reported on target_date."""
    from concurrent.futures import ThreadPoolExecutor, as_completed
    reporters = []
    with ThreadPoolExecutor(max_workers=20) as executor:
        futures = {executor.submit(_check_ticker_for_date, t, target_date): t
                   for t in POPULAR_TICKERS}
        for future in as_completed(futures):
            result = future.result()
            if result:
                reporters.append(result)
    return reporters


def get_earnings_trade_for_date(ticker: str, target_date: str) -> dict:
    """Return the backtest trade row for ticker on/around target_date."""
    try:
        bt = run_earnings_backtest(ticker)
        if bt.get("error"):
            return {"ticker": ticker, "error": bt["error"]}
        target = datetime.strptime(target_date, "%Y-%m-%d").date()
        for trade in bt["trades"]:
            d = datetime.strptime(trade["date"], "%Y-%m-%d").date()
            if abs((d - target).days) <= 1:
                return {"ticker": ticker, **trade}
        return {"ticker": ticker, "error": "No trade found for this date"}
    except Exception as ex:
        return {"ticker": ticker, "error": str(ex)[:80]}


def get_post_earnings_batch(target_date: str, custom_tickers: list[str] = None) -> list[dict]:
    """
    Return backtest trade rows for tickers that reported on target_date.
    If custom_tickers supplied, use those; otherwise auto-discover via POPULAR_TICKERS.
    """
    from concurrent.futures import ThreadPoolExecutor, as_completed

    if custom_tickers:
        tickers = [t.upper().strip() for t in custom_tickers if t.strip()][:10]
    else:
        tickers = find_earnings_reporters(target_date)[:5]

    if not tickers:
        return []

    results = []
    with ThreadPoolExecutor(max_workers=5) as executor:
        futures = {executor.submit(get_earnings_trade_for_date, t, target_date): t
                   for t in tickers}
        for future in as_completed(futures):
            results.append(future.result())

    # Errors last, then sort by date
    results.sort(key=lambda x: (bool(x.get("error")), x.get("date", "")))
    return results


# ── Master analysis ────────────────────────────────────────────────────────────

def get_full_earnings_analysis(ticker: str) -> dict:
    try:
        tk   = yf.Ticker(ticker)
        hist = tk.history(period="2y", interval="1d")
        if hist.empty:
            return {"error": "No price data"}
        hist.columns = [c.lower() for c in hist.columns]
        current_price = float(hist["close"].iloc[-1])

        earnings_raw  = get_earnings_dates_yf(ticker)
        earnings_hist = enrich_with_price_moves(ticker, earnings_raw)

        # Detect if earnings were recently reported (within 45 days).
        # yfinance calendar often still shows the just-passed window as "upcoming".
        last_reported = get_last_reported_date(ticker, earnings_hist)
        recently_reported = False
        if last_reported:
            days_ago = (date.today() - datetime.strptime(last_reported, "%Y-%m-%d").date()).days
            if days_ago <= 45:
                recently_reported = True

        # Price-gap heuristic: if >4% gap + 1.5× avg volume in last 7 days → earnings happened
        # yfinance often doesn't add new earnings to history for days/weeks after reporting
        if not recently_reported and len(hist) >= 15:
            try:
                recent   = hist.tail(7)
                avg_vol  = float(hist.tail(30)["volume"].mean())
                prev_cls = hist["close"].iloc[-(8)]
                for i in range(1, len(recent)):
                    gap    = abs(float(recent["close"].iloc[i]) - float(recent["close"].iloc[i - 1])) / float(recent["close"].iloc[i - 1])
                    vol    = float(recent["volume"].iloc[i])
                    if gap > 0.04 and avg_vol > 0 and vol > avg_vol * 1.5:
                        recently_reported = True
                        last_reported     = str(recent.index[i].date())
                        break
            except Exception:
                pass

        next_date = get_next_earnings_date(ticker)
        # Suppress calendar date if it falls within 60 days of the last reported date
        # (covers the full gap between quarterly reports where calendar hasn't updated)
        if next_date and last_reported:
            nd = datetime.strptime(next_date, "%Y-%m-%d").date()
            lr = datetime.strptime(last_reported, "%Y-%m-%d").date()
            if nd <= lr + timedelta(days=60):
                next_date = None

        expected_move = get_expected_move(ticker, next_date)
        stats         = compute_history_stats(earnings_hist)

        from backend.services.analysis import get_fundamentals
        fundamentals   = get_fundamentals(ticker) or {}
        revisions      = get_estimate_revisions(ticker)
        rev_beat       = get_revenue_beat_rate(ticker, earnings_hist)
        news_sentiment = get_news_sentiment(ticker)

        direction = get_direction_score(
            ticker, current_price, hist,
            earnings_hist, fundamentals, expected_move,
            revisions=revisions,
            rev_beat=rev_beat,
            news_sentiment=news_sentiment,
        )

        # Best estimate summary
        avg_hist  = stats.get("last4_avg_move") or stats.get("avg_abs_move") or 0
        opt_move  = expected_move.get("expected_move_pct") or 0
        est_move  = round(opt_move * 0.6 + avg_hist * 0.4, 1) if opt_move > 0 else avg_hist

        post_earnings = None
        if recently_reported and last_reported:
            post_earnings = get_post_earnings_analysis(
                ticker, earnings_hist, hist, last_reported, current_price
            )

        short_pct  = fundamentals.get("short_pct_float")
        days_cover = fundamentals.get("short_ratio")
        squeeze    = bool(short_pct and short_pct >= 15 and (days_cover or 0) >= 5)
        short_interest = {
            "short_pct_float": short_pct,
            "days_to_cover":   days_cover,
            "squeeze":         squeeze,
        } if short_pct is not None else {}

        return {
            "ticker":             ticker,
            "current_price":      current_price,
            "next_earnings":      next_date,
            "recently_reported":  recently_reported,
            "last_reported":      last_reported,
            "expected_move":      expected_move,
            "estimated_move":     est_move,
            "history":            earnings_hist[-8:],
            "stats":              stats,
            "direction":          direction,
            "revisions":          revisions,
            "rev_beat":           rev_beat,
            "short_interest":     short_interest,
            "news_sentiment":     news_sentiment,
            "post_earnings":      post_earnings,
        }
    except Exception as ex:
        return {"error": str(ex)[:200]}
