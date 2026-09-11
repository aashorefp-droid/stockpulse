"""
Options bias helper + Alpaca strategy recommendation.
Strategy mirrors get_options_strategy_alpaca() from stock_pulse.py exactly:
  - Alpaca /v1beta1/options/snapshots only
  - Bull Call Spread (LONG) · Bear Put Spread (SHORT) · Iron Butterfly / Iron Condor (NEUTRAL)
  - zone parameter (HIGH/MID/LOW) derived from Fib position drives neutral detection
"""
import requests
import time
import os
from datetime import date, datetime, timedelta, timezone
from typing import Optional
from backend.config import ALPACA_DATA_BASE


def _env_enabled(name: str, default: str = "0") -> bool:
    return os.getenv(name, default).strip().lower() in {"1", "true", "yes", "on"}


OPTIONS_PREFER_PUTS = _env_enabled("OPTIONS_PREFER_PUTS", "1")


def _safe_float(value, default: float = 0.0) -> float:
    try:
        if value is None:
            return default
        v = float(value)
        return default if v != v else v
    except Exception:
        return default


def _norm_iv(value) -> float:
    """Return IV as a decimal, accepting either 0.42 or 42 style inputs."""
    iv = _safe_float(value)
    if iv <= 0:
        return 0.0
    return iv / 100 if iv > 3 else iv


# ── Alpaca helper ─────────────────────────────────────────────────────────────

def _alpaca_get(endpoint: str, params: dict, api_key: str, api_secret: str) -> dict:
    headers = {"APCA-API-KEY-ID": api_key, "APCA-API-SECRET-KEY": api_secret}
    url = ALPACA_DATA_BASE + endpoint
    for attempt in range(3):
        try:
            r = requests.get(url, params=params, headers=headers, timeout=15)
            if r.status_code == 429:
                time.sleep(5); continue
            r.raise_for_status()
            return r.json()
        except Exception as e:
            if attempt == 2 or any(x in str(e) for x in ("401", "403", "404")):
                raise
            time.sleep(2)
    return {}


# ── Options bias (yfinance, delta-aware) ─────────────────────────────────────

def _get_options_bias_yfinance(ticker: str) -> dict:
    try:
        import yfinance as yf
        stock = yf.Ticker(ticker)
        expirations = stock.options
        if not expirations:
            return {"error": "No options data"}

        hist = stock.history(period="1d")
        current_price = float(hist["Close"].iloc[-1]) if not hist.empty else 0

        today_str = str(date.today())
        valid_exps = [e for e in expirations if e > today_str][:6]
        if not valid_exps:
            return {"error": "No future expirations"}

        all_calls, all_puts = [], []
        for exp in valid_exps:
            try:
                chain = stock.option_chain(exp)
                for _, row in chain.calls.iterrows():
                    all_calls.append({
                        "volume": int(row.get("volume") or 0),
                        "oi":     int(row.get("openInterest") or 0),
                        "strike": float(row.get("strike") or 0),
                        "iv":     _norm_iv(row.get("impliedVolatility") or 0),
                        "itm":    bool(row.get("inTheMoney", False)),
                        "expiry": exp,
                    })
                for _, row in chain.puts.iterrows():
                    all_puts.append({
                        "volume": int(row.get("volume") or 0),
                        "oi":     int(row.get("openInterest") or 0),
                        "strike": float(row.get("strike") or 0),
                        "iv":     _norm_iv(row.get("impliedVolatility") or 0),
                        "itm":    bool(row.get("inTheMoney", False)),
                        "expiry": exp,
                    })
            except Exception:
                continue

        call_oi  = sum(c["oi"]     for c in all_calls)
        put_oi   = sum(p["oi"]     for p in all_puts)
        call_vol = sum(c["volume"] for c in all_calls)
        put_vol  = sum(p["volume"] for p in all_puts)
        oi_pc    = put_oi  / call_oi  if call_oi  > 0 else 0
        vol_pc   = put_vol / call_vol if call_vol > 0 else 0

        spec_bull_oi = spec_bear_oi = atm_call_oi = atm_put_oi = 0
        if current_price > 0:
            for c in all_calls:
                m = (c["strike"] - current_price) / current_price
                if   m > 0.05:           spec_bull_oi += c["oi"]
                elif -0.05 <= m <= 0.05: atm_call_oi  += c["oi"]
            for p in all_puts:
                m = (current_price - p["strike"]) / current_price
                if   -0.05 <= m <= 0.10: spec_bear_oi += p["oi"]
                elif m < -0.05:          atm_put_oi   += p["oi"]

        dir_bull = spec_bull_oi + atm_call_oi
        dir_bear = spec_bear_oi + atm_put_oi
        total_dir = dir_bull + dir_bear
        delta_bull_pct = dir_bull / total_dir if total_dir > 0 else 0.5
        delta_bear_pct = dir_bear / total_dir if total_dir > 0 else 0.5

        if   delta_bull_pct > 0.60: delta_sentiment = "BULLISH"
        elif delta_bear_pct > 0.60: delta_sentiment = "BEARISH"
        else:                        delta_sentiment = "NEUTRAL"

        if   oi_pc < 0.7:  oi_sentiment = "BULLISH"
        elif oi_pc <= 1.0: oi_sentiment = "NEUTRAL"
        else:               oi_sentiment = "BEARISH"

        # Build mid-price lookup for unusual notional calc (using IV as proxy if no bid/ask)
        unusual = []
        call_set = set(id(c) for c in all_calls)
        for item in all_calls + all_puts:
            vol  = item["volume"]
            oi   = item["oi"]
            strike = item["strike"]
            typ  = "CALL" if id(item) in call_set else "PUT"

            # OTM % from current price
            if current_price > 0:
                if typ == "CALL":
                    otm_pct = max(0.0, (strike - current_price) / current_price * 100)
                else:
                    otm_pct = max(0.0, (current_price - strike) / current_price * 100)
            else:
                otm_pct = 0.0

            # Approximate mid from IV if no direct mid available
            iv_mid = item["iv"] * current_price * 0.4 if item["iv"] > 0 and current_price > 0 else 0
            notional = round(vol * iv_mid * 100, 0) if iv_mid > 0 else 0

            # Classify flow type
            flow_tags = []
            if oi == 0 and vol >= 10:
                flow_tags.append("OPENING")             # fresh new position
            if oi > 0 and vol > oi:
                flow_tags.append("SWEEP")               # volume cleared all open interest
            if vol >= 1000:
                flow_tags.append("BLOCK")               # large block order
            if otm_pct >= 30 and vol >= 50:
                flow_tags.append("FAR_OTM")             # deep lottery ticket / squeeze bet
            if notional >= 10_000:
                flow_tags.append("LARGE_NOTIONAL")      # $10k+ premium regardless of vol
            if oi > 0 and vol / oi > 2 and vol >= 100:
                flow_tags.append("HIGH_RATIO")          # ratio > 2× OI

            # "Unusual" requires a directional/positioning signal — not just size.
            # BLOCK and LARGE_NOTIONAL alone are too generous on liquid names.
            QUALIFYING = {"OPENING", "SWEEP", "FAR_OTM", "HIGH_RATIO"}
            if not any(t in QUALIFYING for t in flow_tags):
                continue

            unusual.append({
                **item,
                "type": typ,
                "otm_pct": round(otm_pct, 1),
                "notional": int(notional),
                "flow_type": "/".join(flow_tags),
            })

        # Sort: far-OTM or large notional first, then by volume
        unusual.sort(key=lambda x: (
            -("FAR_OTM" in x["flow_type"] or "LARGE_NOTIONAL" in x["flow_type"]),
            -x["volume"],
        ))

        # ── OTM liquid options ──────────────────────────────────────────────
        unusual_strikes = {(u["strike"], u["type"]) for u in unusual}
        otm_liquid = []
        for item in all_calls + all_puts:
            typ = "CALL" if id(item) in call_set else "PUT"
            if item["itm"]:
                continue
            if item["volume"] < 50 or item["oi"] < 100:
                continue
            if current_price > 0:
                otm_pct = (
                    (item["strike"] - current_price) / current_price * 100
                    if typ == "CALL"
                    else (current_price - item["strike"]) / current_price * 100
                )
            else:
                otm_pct = 0.0
            vol_oi_ratio = round(item["volume"] / item["oi"], 2) if item["oi"] > 0 else 0
            is_unusual   = (item["strike"], typ) in unusual_strikes
            otm_liquid.append({
                "strike":        item["strike"],
                "type":          typ,
                "expiry":        item["expiry"],
                "volume":        item["volume"],
                "oi":            item["oi"],
                "iv":            round(item["iv"] * 100, 1),
                "otm_pct":       round(otm_pct, 1),
                "vol_oi_ratio":  vol_oi_ratio,
                "unusual":       is_unusual,
            })

        # Sort: unusual first, then by volume
        otm_liquid.sort(key=lambda x: (-x["unusual"], -x["volume"]))

        return {
            "call_oi": call_oi, "put_oi": put_oi,
            "call_vol": call_vol, "put_vol": put_vol,
            "oi_pc_ratio": round(oi_pc, 2), "vol_pc_ratio": round(vol_pc, 2),
            "oi_sentiment": oi_sentiment, "delta_sentiment": delta_sentiment,
            "delta_bull_pct": round(delta_bull_pct * 100, 1),
            "delta_bear_pct": round(delta_bear_pct * 100, 1),
            "unusual_count": len(unusual),
            "unusual_activity": unusual[:5],
            "otm_liquid": otm_liquid[:10],
            "expirations_scanned": len(valid_exps),
        }
    except Exception as e:
        return {"error": str(e)[:100]}


# ── Options strategy — mirrors get_options_strategy_alpaca() exactly ──────────

def get_options_bias(ticker: str, current_price: float = 0.0,
                     api_key: str = "", api_secret: str = "") -> dict:
    """Alpaca-only options positioning snapshot for the analysis page."""
    try:
        price = _safe_float(current_price)
        contracts = _fetch_contracts_alpaca(ticker, api_key, api_secret, price)
        if not contracts:
            return {"error": "No Alpaca options data"}

        def _mid(c: dict) -> float:
            b, a, l = c.get("bid", 0), c.get("ask", 0), c.get("last", 0)
            return (b + a) / 2 if b > 0 and a > 0 else _safe_float(l)

        def _otm_pct(c: dict) -> float:
            if price <= 0:
                return 0.0
            if c.get("is_call"):
                return max(0.0, (c["strike"] - price) / price * 100)
            return max(0.0, (price - c["strike"]) / price * 100)

        calls = [c for c in contracts if c.get("is_call")]
        puts = [c for c in contracts if not c.get("is_call")]
        call_oi = sum(int(c.get("oi") or 0) for c in calls)
        put_oi = sum(int(c.get("oi") or 0) for c in puts)
        call_vol = sum(int(c.get("volume") or 0) for c in calls)
        put_vol = sum(int(c.get("volume") or 0) for c in puts)

        oi_pc = put_oi / call_oi if call_oi > 0 else 0
        vol_pc = put_vol / call_vol if call_vol > 0 else 0
        if call_oi == 0 and put_oi == 0:
            oi_sentiment = "N/A"
        elif oi_pc < 0.7:
            oi_sentiment = "BULLISH"
        elif oi_pc <= 1.0:
            oi_sentiment = "NEUTRAL"
        else:
            oi_sentiment = "BEARISH"

        spec_bull_oi = spec_bear_oi = atm_call_oi = atm_put_oi = 0
        if price > 0:
            for c in calls:
                m = (c["strike"] - price) / price
                if m > 0.05:
                    spec_bull_oi += int(c.get("oi") or 0)
                elif -0.05 <= m <= 0.05:
                    atm_call_oi += int(c.get("oi") or 0)
            for p in puts:
                m = (price - p["strike"]) / price
                if -0.05 <= m <= 0.10:
                    spec_bear_oi += int(p.get("oi") or 0)
                elif m < -0.05:
                    atm_put_oi += int(p.get("oi") or 0)

        dir_bull = spec_bull_oi + atm_call_oi
        dir_bear = spec_bear_oi + atm_put_oi
        total_dir = dir_bull + dir_bear
        delta_bull_pct = dir_bull / total_dir if total_dir > 0 else 0.5
        delta_bear_pct = dir_bear / total_dir if total_dir > 0 else 0.5
        if delta_bull_pct > 0.60:
            delta_sentiment = "BULLISH"
        elif delta_bear_pct > 0.60:
            delta_sentiment = "BEARISH"
        else:
            delta_sentiment = "NEUTRAL"

        unusual = []
        for c in contracts:
            typ = "CALL" if c.get("is_call") else "PUT"
            vol = int(c.get("volume") or 0)
            oi = int(c.get("oi") or 0)
            mid = _mid(c)
            notional = round(vol * mid * 100, 0) if mid > 0 else 0
            flow_tags = []
            if oi == 0 and vol >= 10:
                flow_tags.append("OPENING")
            if oi > 0 and vol > oi:
                flow_tags.append("SWEEP")
            if vol >= 1000:
                flow_tags.append("BLOCK")
            if _otm_pct(c) >= 30 and vol >= 50:
                flow_tags.append("FAR_OTM")
            if notional >= 10_000:
                flow_tags.append("LARGE_NOTIONAL")
            if oi > 0 and vol / oi > 2 and vol >= 100:
                flow_tags.append("HIGH_RATIO")
            if not any(t in {"OPENING", "SWEEP", "FAR_OTM", "HIGH_RATIO"} for t in flow_tags):
                continue
            unusual.append({
                "volume": vol,
                "oi": oi,
                "strike": c["strike"],
                "iv": round(_norm_iv(c.get("iv")) * 100, 1),
                "itm": _otm_pct(c) == 0,
                "expiry": c["exp"],
                "type": typ,
                "otm_pct": round(_otm_pct(c), 1),
                "notional": int(notional),
                "flow_type": "/".join(flow_tags),
            })
        unusual.sort(key=lambda x: (
            -("FAR_OTM" in x["flow_type"] or "LARGE_NOTIONAL" in x["flow_type"]),
            -x["volume"],
        ))
        unusual_keys = {(u["strike"], u["type"], u["expiry"]) for u in unusual}

        otm_liquid = []
        for c in contracts:
            typ = "CALL" if c.get("is_call") else "PUT"
            vol = int(c.get("volume") or 0)
            oi = int(c.get("oi") or 0)
            otm = _otm_pct(c)
            if otm <= 0 or vol < 50 or oi < 100:
                continue
            otm_liquid.append({
                "strike": c["strike"],
                "type": typ,
                "expiry": c["exp"],
                "volume": vol,
                "oi": oi,
                "iv": round(_norm_iv(c.get("iv")) * 100, 1),
                "otm_pct": round(otm, 1),
                "vol_oi_ratio": round(vol / oi, 2) if oi > 0 else 0,
                "unusual": (c["strike"], typ, c["exp"]) in unusual_keys,
            })
        otm_liquid.sort(key=lambda x: (-x["unusual"], -x["volume"]))

        return {
            "call_oi": call_oi, "put_oi": put_oi,
            "call_vol": call_vol, "put_vol": put_vol,
            "oi_pc_ratio": round(oi_pc, 2), "vol_pc_ratio": round(vol_pc, 2),
            "oi_sentiment": oi_sentiment, "delta_sentiment": delta_sentiment,
            "delta_bull_pct": round(delta_bull_pct * 100, 1),
            "delta_bear_pct": round(delta_bear_pct * 100, 1),
            "unusual_count": len(unusual),
            "unusual_activity": unusual[:5],
            "otm_liquid": otm_liquid[:10],
            "expirations_scanned": len(set(c["exp"] for c in contracts)),
            "source": "alpaca",
        }
    except Exception as e:
        return {"error": str(e)[:100]}


def get_options_strategy(ticker: str, current_price: float, direction: str,
                         api_key: str = "", api_secret: str = "",
                         zone: str = "MID") -> Optional[dict]:
    """
    Exact port of stock_pulse.get_options_strategy_alpaca().
    Uses Alpaca options snapshots only.
    zone: 'HIGH' | 'MID' | 'LOW'  (derived from Fib position by caller)
      HIGH → price above 61.8% retracement → neutral/iron-butterfly zone
      LOW  → price below 38.2% → directional
      MID  → between → directional
    """
    contracts = _fetch_contracts_alpaca(ticker, api_key, api_secret, current_price)
    if not contracts:
        return None
    result = _build_strategy(ticker, current_price, direction, zone, contracts)
    if result:
        result["source"] = "alpaca"
    return result


# ── Contract fetchers ─────────────────────────────────────────────────────────

def _fetch_contracts_alpaca(ticker: str, api_key: str, api_secret: str,
                             current_price: float = 0) -> list:
    if not api_key or not api_secret:
        return []
    try:
        today   = date.today()
        exp_min = (today + timedelta(days=5)).isoformat()
        exp_max = (today + timedelta(days=60)).isoformat()
        # Keep enough OTM strikes for four-leg structures like iron condors.
        s_lo    = current_price * 0.70 if current_price > 0 else 0
        s_hi    = current_price * 1.30 if current_price > 0 else float("inf")

        # Paginate snapshots endpoint (max 1000/page) until we have enough
        # relevant contracts or we run out of pages.
        all_snaps: dict = {}
        page_token = None
        for _ in range(4):          # cap at 4 pages = 4000 contracts
            params: dict = {"limit": 1000, "feed": "indicative"}
            if page_token:
                params["page_token"] = page_token
            data       = _alpaca_get(f"/v1beta1/options/snapshots/{ticker}",
                                     params, api_key, api_secret)
            snaps      = data.get("snapshots", {})
            all_snaps.update(snaps)
            page_token = data.get("next_page_token")

            # Stop early once we've collected contracts past our target window
            exps_seen = set()
            for sym in snaps:
                after = sym.replace(ticker, "", 1)
                if len(after) >= 6:
                    try:
                        e = after[:6]
                        exps_seen.add(f"20{e[:2]}-{e[2:4]}-{e[4:6]}")
                    except Exception:
                        pass
            if exps_seen and max(exps_seen) > exp_max:
                break
            if not page_token:
                break

        # Parse and filter to relevant expiration/strike window
        contracts = []
        for sym, snap in all_snaps.items():
            after = sym.replace(ticker, "", 1)
            if len(after) < 15:
                continue
            try:
                exp_str  = after[:6]
                cp_flag  = after[6]
                strike   = int(after[7:15]) / 1000
                exp_date = f"20{exp_str[:2]}-{exp_str[2:4]}-{exp_str[4:6]}"
            except Exception:
                continue

            # Client-side filter: expiration window + near-the-money strikes
            if not (exp_min <= exp_date <= exp_max):
                continue
            if current_price > 0 and not (s_lo <= strike <= s_hi):
                continue

            quote = snap.get("latestQuote", {})
            trade = snap.get("latestTrade", {})
            bar = snap.get("dailyBar", {}) or snap.get("daily_bar", {})
            iv = _norm_iv(snap.get("impliedVolatility")
                          or snap.get("implied_volatility")
                          or snap.get("iv"))
            contracts.append({
                "strike":   strike,
                "exp":      exp_date,
                "is_call":  cp_flag == "C",
                "bid":      float(quote.get("bp", 0) or 0),
                "ask":      float(quote.get("ap", 0) or 0),
                "last":     float(trade.get("p", 0) or 0),
                "iv":       iv,
                "oi":       int(snap.get("openInterest", 0) or 0),
                "volume":   int(_safe_float(bar.get("v") or bar.get("volume"))),
                "quote_ts": quote.get("t"),   # ISO timestamp for staleness check
            })

        return contracts
    except Exception:
        return []


def _fetch_contracts_yfinance(ticker: str) -> list:
    try:
        import yfinance as yf
        tk = yf.Ticker(ticker)
        expirations = tk.options
        if not expirations:
            return []

        today = date.today()
        future = [e for e in expirations if e > today.isoformat()]
        if not future:
            return []

        # Load the two expirations closest to 7d and 21d
        def _nearest(target_dt):
            return min(future, key=lambda e: abs((datetime.strptime(e, "%Y-%m-%d").date() - target_dt).days))

        exp_s = _nearest(today + timedelta(days=7))
        exp_l = _nearest(today + timedelta(days=21))
        target_exps = list(dict.fromkeys([exp_s, exp_l]))  # dedup, preserve order

        contracts = []
        for exp in target_exps:
            try:
                chain = tk.option_chain(exp)
                for _, row in chain.calls.iterrows():
                    b = float(row.get("bid") or 0)
                    a = float(row.get("ask") or 0)
                    l = float(row.get("lastPrice") or 0)
                    contracts.append({
                        "strike":  float(row["strike"]),
                        "exp":     exp,
                        "is_call": True,
                        "bid": b, "ask": a,
                        "last": l,
                        "iv": _norm_iv(row.get("impliedVolatility") or 0),
                        "oi": int(row.get("openInterest") or 0),
                    })
                for _, row in chain.puts.iterrows():
                    b = float(row.get("bid") or 0)
                    a = float(row.get("ask") or 0)
                    l = float(row.get("lastPrice") or 0)
                    contracts.append({
                        "strike":  float(row["strike"]),
                        "exp":     exp,
                        "is_call": False,
                        "bid": b, "ask": a,
                        "last": l,
                        "iv": _norm_iv(row.get("impliedVolatility") or 0),
                        "oi": int(row.get("openInterest") or 0),
                    })
            except Exception:
                continue
        return contracts
    except Exception:
        return []


# ── Liquidity / misprice filter ───────────────────────────────────────────────

_MAX_QUOTE_AGE_HOURS = 72   # reject quotes older than 3 calendar days (covers weekends)

def _is_liquid(c: dict,
               max_spread_pct: float = 0.50,
               min_oi: int = 10,
               min_mid: float = 0.05) -> bool:
    """
    Return True only if the contract has a valid, fresh, reasonably-priced market.

    OI check is skipped when we have a live two-sided bid/ask — Alpaca's
    indicative feed returns oi=0 but quotes are real.
    Staleness check uses quote_ts (Alpaca only); yfinance contracts skip it.
    """
    b, a, l, oi = c["bid"], c["ask"], c["last"], c["oi"]

    # Staleness — reject quotes older than 3 calendar days
    ts = c.get("quote_ts")
    if ts:
        try:
            qt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
            age_h = (datetime.now(timezone.utc) - qt).total_seconds() / 3600
            if age_h > _MAX_QUOTE_AGE_HOURS:
                return False
        except Exception:
            pass

    has_two_sided = b > 0 and a > 0

    if has_two_sided:
        m = (b + a) / 2
    elif l > 0:
        m = l
    else:
        return False

    if m < min_mid:
        return False

    if b < 0 or a < 0:
        return False

    if has_two_sided:
        spread_pct = (a - b) / m
        if spread_pct > max_spread_pct:
            return False
    else:
        if oi < min_oi:
            return False

    return True


def _validate_spread(net_debit: float, spread_width: float) -> bool:
    """Reject economically impossible spreads."""
    if net_debit <= 0:
        return False          # debit spread with zero/negative cost = mispriced data
    if net_debit >= spread_width:
        return False          # debit ≥ width means max-profit ≤ 0 — not worth taking
    return True


def _validate_credit_spread(credit: float, spread_width: float) -> bool:
    """Reject economically impossible credit spreads."""
    if credit <= 0:
        return False
    if credit >= spread_width:
        return False
    return True


# ── Strategy builder — exact logic from stock_pulse.py ───────────────────────

def _build_strategy(ticker: str, current_price: float, direction: str,
                    zone: str, contracts: list) -> Optional[dict]:
    today = date.today()

    # Apply liquidity filter before anything else
    liquid = [c for c in contracts if _is_liquid(c)]
    if not liquid:
        return None

    expirations = sorted(set(c["exp"] for c in liquid))

    def _nearest_exp(target_dt):
        return min(expirations, key=lambda e: abs(
            (datetime.strptime(e, "%Y-%m-%d").date() - target_dt).days
        ))

    exp_short = _nearest_exp(today + timedelta(days=7))
    exp_long  = _nearest_exp(today + timedelta(days=21))

    def _pick(clist, target_strike):
        if not clist:
            return None
        return min(clist, key=lambda c: abs(c["strike"] - target_strike))

    def mid(c) -> float:
        if c is None:
            return 0.0
        b, a, l = c["bid"], c["ask"], c["last"]
        return round((b + a) / 2, 2) if b > 0 and a > 0 else round(l, 2)

    def iv(c) -> float:
        return _norm_iv(c.get("iv", 0)) if c else 0.0

    def _leg(action, typ, contract, exp):
        return {
            "action": action, "type": typ,
            "strike": contract["strike"], "exp": exp,
            "bid": contract["bid"], "ask": contract["ask"], "mid": mid(contract),
            "iv": round(iv(contract) * 100, 1) if iv(contract) > 0 else None,
            "spread_pct": round((contract["ask"] - contract["bid"]) / mid(contract) * 100, 1)
                          if contract["bid"] > 0 and contract["ask"] > 0 and mid(contract) > 0
                          else None,
        }

    # Only liquid contracts in each bucket
    calls_s = [c for c in liquid if c["exp"] == exp_short and c["is_call"]]
    puts_s  = [c for c in liquid if c["exp"] == exp_short and not c["is_call"]]
    calls_l = [c for c in liquid if c["exp"] == exp_long  and c["is_call"]]
    puts_l  = [c for c in liquid if c["exp"] == exp_long  and not c["is_call"]]
    condor_liquid = [
        c for c in contracts
        if c["exp"] == exp_long
        and _is_liquid(c, max_spread_pct=1.20, min_oi=0, min_mid=0.01)
    ]
    calls_l_condor = [c for c in condor_liquid if c["is_call"]]
    puts_l_condor = [c for c in condor_liquid if not c["is_call"]]

    def _median(values):
        vals = sorted(v for v in values if v and v > 0)
        if not vals:
            return None
        middle = len(vals) // 2
        if len(vals) % 2:
            return vals[middle]
        return (vals[middle - 1] + vals[middle]) / 2

    def _nearest_ivs(bucket):
        near = sorted((c for c in bucket if iv(c) > 0),
                      key=lambda c: abs(c["strike"] - current_price))[:3]
        return [iv(c) for c in near]

    atm_iv = _median(_nearest_ivs(calls_l) + _nearest_ivs(puts_l))

    def _iv_label() -> str:
        if atm_iv is None:
            return "IV N/A"
        if atm_iv >= 0.75:
            return "High IV"
        if atm_iv >= 0.55:
            return "Elevated IV"
        if atm_iv <= 0.30:
            return "Low IV"
        return "Normal IV"

    def _iv_summary() -> str:
        if atm_iv is None:
            return "IV N/A"
        return f"IV {atm_iv * 100:.0f}% {_iv_label().replace(' IV', '')}"

    def _prefer_butterfly() -> bool:
        return atm_iv is not None and atm_iv >= 0.55

    def _zebra_fit(net_extrinsic: float, debit: float) -> str:
        if net_extrinsic <= 0:
            return "Extrinsic fit: zero/negative"
        return "Extrinsic fit: near-zero"

    def _zebra_max_extrinsic() -> float:
        return 0.05

    def _butterfly_fills(buys: list, sell_body, width: float) -> dict:
        """Estimate what the market would actually give you, not mid.

        Entry at market: pay ASK on buy legs, receive BID on sell legs.
        Close at market: sell buy legs at BID, buy back sell legs at ASK.
        The round-trip cost (`slip = natural - close`) is the all-in fill
        haircut a butterfly suffers from crossing 4 bid/ask spreads going
        in plus 4 coming out (2 of them on the doubled body). On OTM-wing
        flies this is routinely 30–50% of width on liquid names and worse
        on illiquid ones — which is why displayed Max ≠ realized exit.
        """
        def _b(c): return c["bid"] if c and c.get("bid", 0) > 0 else 0.0
        def _a(c): return c["ask"] if c and c.get("ask", 0) > 0 else 0.0
        natural = round(_a(buys[0]) + _a(buys[1]) - 2 * _b(sell_body), 2)
        close   = round(_b(buys[0]) + _b(buys[1]) - 2 * _a(sell_body), 2)
        slip    = round(natural - close, 2)
        if close <= 0 or _a(sell_body) <= 0:
            tag = "⚠ no exit liquidity"
        elif width > 0 and slip / width > 0.30:
            tag = "⚠ wide fills"
        elif width > 0 and slip / width <= 0.10:
            tag = "tight fills"
        else:
            tag = "okay fills"
        return {"natural": natural, "close": close, "slip": slip, "tag": tag}

    def _butterfly_fit(debit: float, width: float) -> str:
        if atm_iv is None:
            return "IV fit: unknown"
        debit_pct = debit / width if width > 0 else 1
        if _prefer_butterfly():
            return "IV fit: preferred"
        if debit_pct <= 0.25:
            return "IV fit: good price"
        return "IV fit: okay"

    def _condor_fit(credit: float, width: float) -> str:
        if width <= 0:
            return "Credit fit: unknown"
        credit_pct = credit / width
        if credit_pct >= 0.33:
            return "Credit fit: rich"
        if credit_pct >= 0.20:
            return "Credit fit: fair"
        return "Credit fit: thin"

    def _strike(c) -> str:
        value = float(c["strike"])
        return f"{value:.0f}" if value.is_integer() else f"{value:.1f}"

    def _call_zebra() -> Optional[str]:
        long_c = _pick([c for c in calls_l if c["strike"] < current_price], current_price * 0.95)
        if not long_c:
            long_c = _pick(calls_l, current_price)
        if not long_c:
            return None
        short_c = _pick([c for c in calls_l if c["strike"] > long_c["strike"]], current_price)
        if not short_c:
            return None
        debit = round(2 * mid(long_c) - mid(short_c), 2)
        if debit <= 0:
            return None
        breakeven = round(long_c["strike"] + debit / 2, 2)
        long_extrinsic = max(0.0, mid(long_c) - max(0.0, current_price - long_c["strike"]))
        short_extrinsic = max(0.0, mid(short_c) - max(0.0, current_price - short_c["strike"]))
        net_extrinsic = round(2 * long_extrinsic - short_extrinsic, 2)
        if net_extrinsic > _zebra_max_extrinsic():
            return None
        return (
            f"ZEBRA: Buy 2 ${_strike(long_c)}C / Sell 1 ${_strike(short_c)}C "
            f"Exp {exp_long} | Debit ~${debit:.2f} | BE ~${breakeven:.2f} | "
            f"{_iv_summary()} | {_zebra_fit(net_extrinsic, debit)} | "
            f"Net extrinsic ~${net_extrinsic:.2f}"
        )

    def _put_zebra() -> Optional[str]:
        long_p = _pick([p for p in puts_l if p["strike"] > current_price], current_price * 1.05)
        if not long_p:
            long_p = _pick(puts_l, current_price)
        if not long_p:
            return None
        short_p = _pick([p for p in puts_l if p["strike"] < long_p["strike"]], current_price)
        if not short_p:
            return None
        debit = round(2 * mid(long_p) - mid(short_p), 2)
        if debit <= 0:
            return None
        breakeven = round(long_p["strike"] - debit / 2, 2)
        long_extrinsic = max(0.0, mid(long_p) - max(0.0, long_p["strike"] - current_price))
        short_extrinsic = max(0.0, mid(short_p) - max(0.0, short_p["strike"] - current_price))
        net_extrinsic = round(2 * long_extrinsic - short_extrinsic, 2)
        if net_extrinsic > _zebra_max_extrinsic():
            return None
        return (
            f"ZEBRA: Buy 2 ${_strike(long_p)}P / Sell 1 ${_strike(short_p)}P "
            f"Exp {exp_long} | Debit ~${debit:.2f} | BE ~${breakeven:.2f} | "
            f"{_iv_summary()} | {_zebra_fit(net_extrinsic, debit)} | "
            f"Net extrinsic ~${net_extrinsic:.2f}"
        )

    def _call_butterfly() -> Optional[str]:
        body = _pick([c for c in calls_l if c["strike"] >= current_price], current_price * 1.03)
        if not body:
            body = _pick(calls_l, current_price)
        if not body:
            return None
        lower = _pick([c for c in calls_l if c["strike"] < body["strike"]], current_price)
        if not lower:
            return None
        lower_width = body["strike"] - lower["strike"]
        upper = _pick([c for c in calls_l if c["strike"] > body["strike"]], body["strike"] + lower_width)
        if not upper:
            return None
        width = round(min(body["strike"] - lower["strike"], upper["strike"] - body["strike"]), 2)
        debit = round(mid(lower) - 2 * mid(body) + mid(upper), 2)
        max_profit = round(width - debit, 2)
        if not _validate_spread(debit, width):
            return None
        debit_pct = debit / width * 100 if width > 0 else 0
        f = _butterfly_fills([lower, upper], body, width)
        return (
            f"Butterfly: Buy ${_strike(lower)}C / Sell 2x ${_strike(body)}C / "
            f"Buy ${_strike(upper)}C Exp {exp_long} | Debit ~${debit:.2f} "
            f"(natural ${f['natural']:.2f}) | Target ${_strike(body)} | "
            f"Max ~${max_profit:.2f} | Close ~${f['close']:.2f} "
            f"(slip ${f['slip']:.2f}, {f['tag']}) | "
            f"{_iv_summary()} | {_butterfly_fit(debit, width)} | "
            f"Debit {debit_pct:.0f}% of width"
        )

    def _put_butterfly() -> Optional[str]:
        body = _pick([p for p in puts_l if p["strike"] <= current_price], current_price * 0.97)
        if not body:
            body = _pick(puts_l, current_price)
        if not body:
            return None
        upper = _pick([p for p in puts_l if p["strike"] > body["strike"]], current_price)
        if not upper:
            return None
        upper_width = upper["strike"] - body["strike"]
        lower = _pick([p for p in puts_l if p["strike"] < body["strike"]], body["strike"] - upper_width)
        if not lower:
            return None
        width = round(min(upper["strike"] - body["strike"], body["strike"] - lower["strike"]), 2)
        debit = round(mid(upper) - 2 * mid(body) + mid(lower), 2)
        max_profit = round(width - debit, 2)
        if not _validate_spread(debit, width):
            return None
        debit_pct = debit / width * 100 if width > 0 else 0
        f = _butterfly_fills([upper, lower], body, width)
        return (
            f"Butterfly: Buy ${_strike(upper)}P / Sell 2x ${_strike(body)}P / "
            f"Buy ${_strike(lower)}P Exp {exp_long} | Debit ~${debit:.2f} "
            f"(natural ${f['natural']:.2f}) | Target ${_strike(body)} | "
            f"Max ~${max_profit:.2f} | Close ~${f['close']:.2f} "
            f"(slip ${f['slip']:.2f}, {f['tag']}) | "
            f"{_iv_summary()} | {_butterfly_fit(debit, width)} | "
            f"Debit {debit_pct:.0f}% of width"
        )

    def _iron_condor() -> Optional[str]:
        short_c = _pick([c for c in calls_l_condor if c["strike"] > current_price * 1.02],
                        current_price * 1.03)
        short_p = _pick([p for p in puts_l_condor if p["strike"] < current_price * 0.98],
                        current_price * 0.97)
        if not short_c or not short_p:
            return None

        call_gap = max(short_c["strike"] - current_price, current_price * 0.03)
        put_gap = max(current_price - short_p["strike"], current_price * 0.03)
        wing_gap = max(call_gap, put_gap)
        long_c = _pick([c for c in calls_l_condor if c["strike"] > short_c["strike"]],
                       short_c["strike"] + wing_gap)
        long_p = _pick([p for p in puts_l_condor if p["strike"] < short_p["strike"]],
                       short_p["strike"] - wing_gap)
        if not long_c or not long_p:
            return None

        call_width = round(long_c["strike"] - short_c["strike"], 2)
        put_width = round(short_p["strike"] - long_p["strike"], 2)
        width = max(call_width, put_width)
        credit = round(mid(short_c) + mid(short_p) - mid(long_c) - mid(long_p), 2)
        if credit <= 0 or width <= 0 or credit >= width:
            return None

        max_loss = round(width - credit, 2)
        be_low = round(short_p["strike"] - credit, 2)
        be_high = round(short_c["strike"] + credit, 2)
        credit_pct = credit / width * 100
        return (
            f"Iron Condor: Buy ${_strike(long_p)}P / Sell ${_strike(short_p)}P + "
            f"Sell ${_strike(short_c)}C / Buy ${_strike(long_c)}C Exp {exp_long} | "
            f"Credit ~${credit:.2f} | BE ${be_low:.2f}-${be_high:.2f} | "
            f"Max loss ~${max_loss:.2f} | {_iv_summary()} | "
            f"{_condor_fit(credit, width)} | Credit {credit_pct:.0f}% of width"
        )

    # Determine strategy type — mirrors original logic exactly
    is_bullish = (direction == "LONG") or (zone == "LOW" and direction != "SHORT")
    is_bearish = (direction == "SHORT") or (zone == "HIGH" and direction != "LONG")
    # zone MID + no strong direction → neutral / Iron Butterfly

    result: dict = {
        "ticker": ticker, "legs": [],
        "exp_short": exp_short, "exp_long": exp_long,
        "iv": round(atm_iv * 100, 1) if atm_iv is not None else None,
        "iv_label": _iv_label(),
    }

    if is_bullish:
        put_credit_ok = False
        if OPTIONS_PREFER_PUTS:
            sell_p = _pick([p for p in puts_l if p["strike"] < current_price], current_price * 0.97)
            buy_p = (
                _pick([p for p in puts_l if p["strike"] < sell_p["strike"]], sell_p["strike"] - current_price * 0.03)
                if sell_p else None
            )
            if sell_p and buy_p and sell_p["strike"] != buy_p["strike"]:
                credit = round(mid(sell_p) - mid(buy_p), 2)
                width = round(sell_p["strike"] - buy_p["strike"], 2)
                max_loss = round(width - credit, 2)
                if _validate_credit_spread(credit, width):
                    put_credit_ok = True
                    result.update({
                        "strategy": "Bull Put Spread",
                        "legs": [_leg("SELL", "PUT", sell_p, exp_long),
                                 _leg("BUY", "PUT", buy_p, exp_long)],
                        "net_debit":  -credit,
                        "max_profit": credit,
                        "width":      width,
                        "quote_ts":   sell_p.get("quote_ts"),
                        "summary": (f"📈 {ticker} Bull Put Spread: "
                                    f"Width: ${width:.0f}  "
                                    f"Sell ${sell_p['strike']:.0f}P / Buy ${buy_p['strike']:.0f}P "
                                    f"Exp {exp_long} | Credit ~${credit:.2f} | "
                                    f"Max Loss ~${max_loss:.2f} | {_iv_summary()}"),
                        "alt": "",
                    })

        if put_credit_ok:
            pass
        else:
            buy_c  = _pick(calls_l, current_price)
            sell_c = _pick(calls_l, current_price * 1.03) if buy_c else None
            if sell_c and buy_c and sell_c["strike"] == buy_c["strike"]:
                sell_c = _pick([c for c in calls_l if c["strike"] > buy_c["strike"]],
                               buy_c["strike"] + 1)

            spread_ok = False
            if buy_c and sell_c and sell_c["strike"] != buy_c["strike"]:
                net_debit  = round(mid(buy_c) - mid(sell_c), 2)
                width      = round(sell_c["strike"] - buy_c["strike"], 2)
                max_profit = round(width - net_debit, 2)
                if _validate_spread(net_debit, width):
                    spread_ok = True
                    result.update({
                        "strategy": "Bull Call Spread",
                        "legs": [_leg("BUY", "CALL", buy_c, exp_long),
                                 _leg("SELL", "CALL", sell_c, exp_long)],
                        "net_debit":  net_debit,
                        "max_profit": max_profit,
                        "width":      width,
                        "quote_ts":   buy_c.get("quote_ts"),
                        "summary": (f"📈 {ticker} Bull Call Spread: "
                                    f"Width: ${width:.0f}  "
                                    f"Buy ${buy_c['strike']:.0f}C / Sell ${sell_c['strike']:.0f}C "
                                    f"Exp {exp_long} | Debit ~${net_debit:.2f} | "
                                    f"Max Profit ~${max_profit:.2f} | {_iv_summary()}"),
                        "alt": (f"Alt: Long ${buy_c['strike']:.0f} Call "
                                f"@ ~${mid(buy_c):.2f} Exp {exp_short}"),
                    })
            if not spread_ok:
                c = buy_c or _pick(calls_s, current_price)
                if c:
                    m = mid(c)
                    result.update({
                        "strategy": "Long Call",
                        "legs": [_leg("BUY", "CALL", c, exp_short)],
                        "net_debit": m, "max_profit": None,
                        "quote_ts":  c.get("quote_ts"),
                        "summary": f"📈 {ticker} Long ${c['strike']:.0f} Call @ ~${m:.2f} Exp {exp_short} | {_iv_summary()}",
                        "alt": "",
                    })

    elif is_bearish:
        buy_p  = _pick(puts_l, current_price)
        sell_p = _pick(puts_l, current_price * 0.97) if buy_p else None
        if sell_p and buy_p and sell_p["strike"] == buy_p["strike"]:
            sell_p = _pick([c for c in puts_l if c["strike"] < buy_p["strike"]],
                           buy_p["strike"] - 1)

        spread_ok = False
        if buy_p and sell_p and sell_p["strike"] != buy_p["strike"]:
            net_debit  = round(mid(buy_p) - mid(sell_p), 2)
            width      = round(buy_p["strike"] - sell_p["strike"], 2)
            max_profit = round(width - net_debit, 2)
            if _validate_spread(net_debit, width):
                spread_ok = True
                result.update({
                    "strategy": "Bear Put Spread",
                    "legs": [_leg("BUY", "PUT", buy_p, exp_long),
                             _leg("SELL", "PUT", sell_p, exp_long)],
                    "net_debit":  net_debit,
                    "max_profit": max_profit,
                    "width":      width,
                    "quote_ts":   buy_p.get("quote_ts"),
                    "summary": (f"📉 {ticker} Bear Put Spread: "
                                f"Width: ${width:.0f}  "
                                f"Buy ${buy_p['strike']:.0f}P / Sell ${sell_p['strike']:.0f}P "
                                f"Exp {exp_long} | Debit ~${net_debit:.2f} | "
                                f"Max Profit ~${max_profit:.2f} | {_iv_summary()}"),
                    "alt": (f"Alt: Long ${buy_p['strike']:.0f} Put "
                            f"@ ~${mid(buy_p):.2f} Exp {exp_short}"),
                })
        if not spread_ok:
            p = buy_p or _pick(puts_s, current_price)
            if p:
                m = mid(p)
                result.update({
                    "strategy": "Long Put",
                    "legs": [_leg("BUY", "PUT", p, exp_short)],
                    "net_debit": m, "max_profit": None,
                    "quote_ts":  p.get("quote_ts"),
                    "summary": f"📉 {ticker} Long ${p['strike']:.0f} Put @ ~${m:.2f} Exp {exp_short} | {_iv_summary()}",
                    "alt": "",
                })

    else:
        # Neutral / MID zone → Iron Butterfly
        atm_c  = _pick(calls_l, current_price)
        atm_p  = _pick(puts_l,  current_price)
        wing_c = _pick([c for c in calls_l if c["strike"] > current_price * 1.02],
                       current_price * 1.03)
        wing_p = _pick([c for c in puts_l  if c["strike"] < current_price * 0.98],
                       current_price * 0.97)

        if atm_c and atm_p and wing_c and wing_p:
            credit = round(mid(atm_c) + mid(atm_p) - mid(wing_c) - mid(wing_p), 2)
            result.update({
                "strategy": "Iron Butterfly",
                "legs": [_leg("SELL", "CALL", atm_c,  exp_long),
                         _leg("SELL", "PUT",  atm_p,  exp_long),
                         _leg("BUY",  "CALL", wing_c, exp_long),
                         _leg("BUY",  "PUT",  wing_p, exp_long)],
                "net_debit":  -credit,   # negative = credit received
                "max_profit": credit,
                "quote_ts":   atm_c.get("quote_ts"),
                "summary": (f"🦋 {ticker} Iron Butterfly: "
                            f"Sell ${atm_c['strike']:.0f}C+${atm_p['strike']:.0f}P / "
                            f"Buy ${wing_c['strike']:.0f}C+${wing_p['strike']:.0f}P "
                            f"Exp {exp_long} | Credit ~${credit:.2f} | {_iv_summary()}"),
                "alt": (f"Alt: Straddle Sell ${atm_c['strike']:.0f}C"
                        f"+${atm_p['strike']:.0f}P Exp {exp_short}"),
            })
        elif atm_c and atm_p:
            result.update({
                "strategy": "Straddle",
                "legs": [_leg("BUY", "CALL", atm_c, exp_long),
                         _leg("BUY", "PUT",  atm_p, exp_long)],
                "net_debit":  round(mid(atm_c) + mid(atm_p), 2),
                "max_profit": None,
                "quote_ts":   atm_c.get("quote_ts"),
                "summary": (f"🦋 {ticker} Straddle: "
                            f"${atm_c['strike']:.0f}C @ ~${mid(atm_c):.2f} + "
                            f"${atm_p['strike']:.0f}P @ ~${mid(atm_p):.2f} Exp {exp_long} | {_iv_summary()}"),
                "alt": "",
            })

    if result.get("summary"):
        alt_lines = []
        if is_bullish:
            if OPTIONS_PREFER_PUTS and result.get("strategy") == "Bull Put Spread":
                butterfly, condor = _put_butterfly(), _iron_condor()
                ordered = (condor, butterfly)
            else:
                zebra, butterfly, condor = _call_zebra(), _call_butterfly(), _iron_condor()
                ordered = (
                    (butterfly, condor, zebra)
                    if _prefer_butterfly()
                    else (zebra, butterfly, condor)
                )
            alt_lines.extend(x for x in ordered if x)
        elif is_bearish:
            zebra, butterfly, condor = _put_zebra(), _put_butterfly(), _iron_condor()
            ordered = (
                (butterfly, condor, zebra)
                if _prefer_butterfly()
                else (zebra, butterfly, condor)
            )
            alt_lines.extend(x for x in ordered if x)
        else:
            alt_lines.extend(x for x in (_iron_condor(), _call_zebra(), _put_zebra()) if x)

        existing_alt = result.get("alt")
        if existing_alt:
            alt_lines.append(existing_alt)
        if alt_lines:
            result["alt"] = "\n".join(alt_lines)

    return result if result.get("summary") else None
