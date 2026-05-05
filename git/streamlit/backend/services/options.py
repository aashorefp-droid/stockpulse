"""
Options bias (yfinance, free) + strategy recommendation.
Strategy mirrors get_options_strategy_alpaca() from stock_pulse.py exactly:
  - Alpaca /v1beta1/options/snapshots first
  - yfinance chain fallback if Alpaca options not enabled
  - Bull Call Spread (LONG) · Bear Put Spread (SHORT) · Iron Butterfly (NEUTRAL)
  - zone parameter (HIGH/MID/LOW) derived from Fib position drives neutral detection
"""
import requests
import time
from datetime import date, datetime, timedelta, timezone
from typing import Optional
from backend.config import ALPACA_DATA_BASE


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

def get_options_bias(ticker: str) -> dict:
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
                        "iv":     float(row.get("impliedVolatility") or 0),
                        "itm":    bool(row.get("inTheMoney", False)),
                        "expiry": exp,
                    })
                for _, row in chain.puts.iterrows():
                    all_puts.append({
                        "volume": int(row.get("volume") or 0),
                        "oi":     int(row.get("openInterest") or 0),
                        "strike": float(row.get("strike") or 0),
                        "iv":     float(row.get("impliedVolatility") or 0),
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

            if not flow_tags:
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

        return {
            "call_oi": call_oi, "put_oi": put_oi,
            "call_vol": call_vol, "put_vol": put_vol,
            "oi_pc_ratio": round(oi_pc, 2), "vol_pc_ratio": round(vol_pc, 2),
            "oi_sentiment": oi_sentiment, "delta_sentiment": delta_sentiment,
            "delta_bull_pct": round(delta_bull_pct * 100, 1),
            "delta_bear_pct": round(delta_bear_pct * 100, 1),
            "unusual_count": len(unusual),
            "unusual_activity": unusual[:5],
            "expirations_scanned": len(valid_exps),
        }
    except Exception as e:
        return {"error": str(e)[:100]}


# ── Options strategy — mirrors get_options_strategy_alpaca() exactly ──────────

def get_options_strategy(ticker: str, current_price: float, direction: str,
                         api_key: str = "", api_secret: str = "",
                         zone: str = "MID") -> Optional[dict]:
    """
    Exact port of stock_pulse.get_options_strategy_alpaca().
    Tries Alpaca first; falls back to yfinance chain on failure.
    zone: 'HIGH' | 'MID' | 'LOW'  (derived from Fib position by caller)
      HIGH → price above 61.8% retracement → neutral/iron-butterfly zone
      LOW  → price below 38.2% → directional
      MID  → between → directional
    """
    contracts = _fetch_contracts_alpaca(ticker, api_key, api_secret, current_price)
    source = "alpaca"
    if not contracts:
        contracts = _fetch_contracts_yfinance(ticker)
        source = "yfinance"
    if not contracts:
        return None
    result = _build_strategy(ticker, current_price, direction, zone, contracts)
    if result:
        result["source"] = source
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
        s_lo    = current_price * 0.85 if current_price > 0 else 0
        s_hi    = current_price * 1.15 if current_price > 0 else float("inf")

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
            contracts.append({
                "strike":   strike,
                "exp":      exp_date,
                "is_call":  cp_flag == "C",
                "bid":      float(quote.get("bp", 0) or 0),
                "ask":      float(quote.get("ap", 0) or 0),
                "last":     float(trade.get("p", 0) or 0),
                "oi":       int(snap.get("openInterest", 0) or 0),
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

    def _leg(action, typ, contract, exp):
        return {
            "action": action, "type": typ,
            "strike": contract["strike"], "exp": exp,
            "bid": contract["bid"], "ask": contract["ask"], "mid": mid(contract),
            "spread_pct": round((contract["ask"] - contract["bid"]) / mid(contract) * 100, 1)
                          if contract["bid"] > 0 and contract["ask"] > 0 and mid(contract) > 0
                          else None,
        }

    # Only liquid contracts in each bucket
    calls_s = [c for c in liquid if c["exp"] == exp_short and c["is_call"]]
    puts_s  = [c for c in liquid if c["exp"] == exp_short and not c["is_call"]]
    calls_l = [c for c in liquid if c["exp"] == exp_long  and c["is_call"]]
    puts_l  = [c for c in liquid if c["exp"] == exp_long  and not c["is_call"]]

    # Determine strategy type — mirrors original logic exactly
    is_bullish = (direction == "LONG") or (zone == "LOW" and direction != "SHORT")
    is_bearish = (direction == "SHORT") or (zone == "HIGH" and direction != "LONG")
    # zone MID + no strong direction → neutral / Iron Butterfly

    result: dict = {"ticker": ticker, "legs": [],
                    "exp_short": exp_short, "exp_long": exp_long}

    if is_bullish:
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
                                f"Exp {exp_long} | Debit ~${net_debit:.2f} | Max Profit ~${max_profit:.2f}"),
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
                    "summary": f"📈 {ticker} Long ${c['strike']:.0f} Call @ ~${m:.2f} Exp {exp_short}",
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
                                f"Exp {exp_long} | Debit ~${net_debit:.2f} | Max Profit ~${max_profit:.2f}"),
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
                    "summary": f"📉 {ticker} Long ${p['strike']:.0f} Put @ ~${m:.2f} Exp {exp_short}",
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
                            f"Exp {exp_long} | Credit ~${credit:.2f}"),
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
                            f"${atm_p['strike']:.0f}P @ ~${mid(atm_p):.2f} Exp {exp_long}"),
                "alt": "",
            })

    return result if result.get("summary") else None
