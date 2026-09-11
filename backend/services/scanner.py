"""
Single-stock scan logic — reuses the full scoring pipeline.
Designed to be called in parallel from the scanner router.
"""
import requests
import yfinance as yf
import pandas as pd
from datetime import date, timedelta
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Iterator, Optional

from backend.services.analysis import (
    full_score_pipeline, get_entry_grade, calc_trade_levels,
    compute_weekly_bias, compute_daily_bias, compute_4h_bias,
    mtf_signal_action, get_fundamentals, _col,
)
from backend.services.market_data import get_daily_bars_alpaca
from backend.services.options import get_options_strategy
from backend.config import ALPACA_API_KEY, ALPACA_API_SECRET

# ── Watchlists ────────────────────────────────────────────────────────────────

WATCHLISTS: dict[str, list[str]] = {
    "default": [
        "AAPL", "MSFT", "GOOGL", "AMZN", "NVDA", "META", "TSLA", "AMD", "NFLX", "CRM",
        "ORCL", "ADBE", "INTC", "PYPL", "SQ", "SHOP", "COIN", "UBER", "ABNB", "SNOW",
        "BA", "CAT", "GS", "JPM", "V", "MA", "DIS", "NKE", "SBUX", "MCD",
        "XOM", "CVX", "PFE", "JNJ", "UNH", "MRNA", "LLY", "ABBV", "BMY", "MRK",
        "SPY", "QQQ", "DIA", "XLF", "XLE", "XLK", "ARKK", "SOXX", "SMH", "MRVL",
    ],
    "tech": [
        "AAPL", "MSFT", "NVDA", "GOOGL", "META", "AMD", "TSLA", "ORCL", "ADBE", "CRM",
        "INTC", "QCOM", "TXN", "MU", "AMAT", "LRCX", "KLAC", "MRVL", "AVGO", "ARM",
        "PLTR", "SNOW", "DDOG", "ZS", "CRWD", "NET", "MDB", "SMCI", "DELL", "HPE",
    ],
    "mega_cap": [
        "AAPL", "MSFT", "NVDA", "GOOGL", "AMZN", "META", "TSLA", "AVGO", "LLY", "JPM",
        "V", "UNH", "XOM", "MA", "JNJ", "PG", "HD", "COST", "MRK", "ABBV",
    ],
    "etfs": [
        "SPY", "QQQ", "DIA", "IWM", "VTI", "ARKK", "XLK", "XLF", "XLE", "XLV",
        "XLI", "SOXX", "SMH", "GLD", "SLV", "TLT", "HYG", "UVXY", "SQQQ", "TQQQ",
    ],
    "momentum": [
        "NVDA", "MRVL", "AVGO", "ARM", "PLTR", "CRWD", "DDOG", "NET", "SMCI",
        "TSLA", "META", "AMZN", "GOOGL", "MSFT", "AMD", "SNOW", "ZS", "SHOP", "COIN", "UBER",
    ],
    # Fallback only — overridden at runtime by get_short_squeeze_tickers()
    "short_squeeze": [
        "TSLA", "COIN", "RIVN", "LCID", "NKLA", "BBAI", "SOUN", "MSTR", "IONQ",
        "SMCI", "BYND", "SPCE", "RIDE", "WKHS", "FFIE", "MULN", "ASTS", "RKLB",
        "ACHR", "JOBY", "LILM", "CLOV", "WISH", "SKLZ", "DKNG", "PLBY", "CVNA",
        "GME", "AMC", "BBBY", "KOSS", "EXPR",
    ],
}


# ── Live short-squeeze fetcher ─────────────────────────────────────────────────

def get_short_squeeze_tickers(min_short_pct: float = 10.0, limit: int = 40) -> list[str]:
    """
    Pull US stocks sorted by short % of float (desc) from Yahoo Finance screener.
    Filters: short float > min_short_pct%, US exchange, market cap > $50M.
    Falls back to hardcoded list on any error.
    """
    # Strategy 1: Yahoo Finance custom screener API
    try:
        url = "https://query1.finance.yahoo.com/v1/finance/screener"
        headers = {"User-Agent": "Mozilla/5.0"}
        body = {
            "offset": 0,
            "size": limit,
            "sortField": "percentofsharesfloatshort",
            "sortType": "DESC",
            "quoteType": "EQUITY",
            "query": {
                "operator": "AND",
                "operands": [
                    {"operator": "GT", "operands": ["percentofsharesfloatshort", min_short_pct / 100]},
                    {"operator": "EQ", "operands": ["region", "us"]},
                    {"operator": "GT", "operands": ["intradaymarketcap", 50_000_000]},
                ],
            },
            "userId": "",
            "userIdType": "guid",
        }
        r = requests.post(url, json=body, headers=headers, timeout=15)
        r.raise_for_status()
        quotes = r.json()["finance"]["result"][0]["quotes"]
        tickers = [q["symbol"] for q in quotes if "." not in q.get("symbol", "")]
        if tickers:
            return tickers[:limit]
    except Exception:
        pass

    # Strategy 2: Yahoo Finance predefined "most shorted" screener
    try:
        url = "https://query1.finance.yahoo.com/v1/finance/screener/predefined/saved"
        params = {"formatted": "false", "scrIds": "most_shorted_stocks", "count": limit}
        headers = {"User-Agent": "Mozilla/5.0"}
        r = requests.get(url, params=params, headers=headers, timeout=10)
        r.raise_for_status()
        quotes = r.json()["finance"]["result"][0]["quotes"]
        tickers = [q["symbol"] for q in quotes if "." not in q.get("symbol", "")]
        if tickers:
            return tickers[:limit]
    except Exception:
        pass

    # Fallback: hardcoded list
    return WATCHLISTS["short_squeeze"]


# ── Single ticker scan ────────────────────────────────────────────────────────

def scan_single(ticker: str, as_of: Optional[str] = None) -> dict:
    try:
        if as_of:
            end = date.fromisoformat(as_of)
            # Roll back to Friday if weekend
            if end.weekday() == 5:   # Saturday
                end = end - timedelta(days=1)
            elif end.weekday() == 6: # Sunday
                end = end - timedelta(days=2)
        else:
            end = date.today()
        start = end - timedelta(days=400)

        daily_df = None
        try:
            daily_df = get_daily_bars_alpaca(ticker, str(start), str(end), ALPACA_API_KEY, ALPACA_API_SECRET)
        except Exception:
            pass

        if daily_df is None or daily_df.empty:
            hist = yf.Ticker(ticker).history(start=str(start), end=str(end + timedelta(days=1)), interval="1d")
            if not hist.empty:
                hist.columns = [c.lower() for c in hist.columns]
                daily_df = hist

        if daily_df is None or len(daily_df) < 30:
            return {"ticker": ticker, "error": "Insufficient data", "score": 0}

        scored      = full_score_pipeline(daily_df)
        verdict     = scored.get("verdict", "NEUTRAL")
        confidence  = scored.get("confidence", "N/A")
        score       = scored.get("score", 0)
        direction   = "SHORT" if verdict in ("BEARISH", "LEAN BEARISH") else "LONG"

        close_col   = _col(daily_df, "close")
        price       = round(float(daily_df[close_col].iloc[-1]), 2)

        grade       = get_entry_grade(score, confidence)
        weekly      = compute_weekly_bias(daily_df)
        daily_b     = compute_daily_bias(daily_df)
        signal      = mtf_signal_action(weekly, daily_b, daily_b)
        trade       = calc_trade_levels(daily_df, verdict, price)

        vol_profile     = scored.get("vol_profile") or {}
        strategy_sig    = scored.get("strategy_signals") or {}

        # Fundamentals (best-effort)
        short_pct = None
        analyst_target = None
        target_upside = None
        pe_ratio = None
        earnings_growth = None
        profit_margin = None
        sector = "N/A"
        try:
            fund = get_fundamentals(ticker)
            if fund:
                short_pct = fund.get("short_pct_float")
                analyst_target = fund.get("analyst_target") or fund.get("target_1y")
                target_upside = fund.get("target_upside")
                pe_ratio = fund.get("pe_ratio")
                earnings_growth = fund.get("earnings_growth")
                profit_margin = fund.get("profit_margin")
                sector = fund.get("sector", "N/A")
        except Exception:
            pass

        # News sentiment (best-effort)
        news = "N/A"
        try:
            from news_sentiment import get_news_sentiment
            news = get_news_sentiment(ticker)
        except Exception:
            pass

        # Options strategy (best-effort — yfinance fallback, no Alpaca keys needed)
        opt_strategy = opt_summary = opt_debit = opt_profit = opt_source = opt_quote_ts = None
        opt_legs = opt_width = opt_exp_short = opt_exp_long = opt_alt = None
        try:
            strat = get_options_strategy(ticker, price, direction, ALPACA_API_KEY, ALPACA_API_SECRET)
            if strat and strat.get("summary"):
                opt_strategy  = strat.get("strategy")
                opt_summary   = strat.get("summary")
                opt_debit     = strat.get("net_debit")
                opt_profit    = strat.get("max_profit")
                opt_source    = strat.get("source")
                opt_quote_ts  = strat.get("quote_ts")
                opt_legs      = strat.get("legs")
                opt_width     = strat.get("width")
                opt_exp_short = strat.get("exp_short")
                opt_exp_long  = strat.get("exp_long")
                opt_alt       = strat.get("alt")
        except Exception:
            pass

        # Moving average bias
        try:
            _ma30 = daily_df[close_col].rolling(30).mean().iloc[-1]
            ma_bias = "Bull" if price >= _ma30 else "Bear"
        except Exception:
            ma_bias = "Bull" if direction == "LONG" else "Bear"

        # Day candle
        try:
            open_col = _col(daily_df, "open")
            day_open = float(daily_df[open_col].iloc[-1])
            day_candle = "Bullish" if price >= day_open else "Bearish"
        except Exception:
            day_candle = "Bullish" if direction == "LONG" else "Bearish"

        entry_status = "ENTER" if grade.get("entry_grade") in ("S", "A", "B", "B-") else "SKIP"

        # 30-Week (150-day) SMA & Curl Detection
        is_30w_curl = False
        is_fresh_stage2 = False
        sma30_val = None
        sma30_slope = 0.0
        dist_from_sma30 = None
        weeks_curling = 0
        stage2_status = "NONE"

        try:
            if len(daily_df) >= 150:
                ma150 = daily_df[close_col].rolling(150).mean()
                sma30_val = round(float(ma150.iloc[-1]), 2)
                slope_recent = (ma150.iloc[-1] - ma150.iloc[-5]) / ma150.iloc[-5] if ma150.iloc[-5] > 0 else 0
                sma30_slope = round(float(slope_recent * 100), 2)
                dist_from_sma30 = round(float((price - sma30_val) / sma30_val * 100), 1)

                # Count weeks curling (checking 5-day intervals backwards)
                w_count = 0
                for step in range(1, min(35, len(ma150) // 5)):
                    idx_cur = -1 - (step - 1) * 5
                    idx_prev = -1 - step * 5
                    if abs(idx_prev) <= len(ma150) and ma150.iloc[idx_cur] > ma150.iloc[idx_prev]:
                        w_count += 1
                    else:
                        break
                weeks_curling = w_count

                is_30w_curl = bool(slope_recent > 0 and price >= sma30_val * 0.98)
                if is_30w_curl:
                    if weeks_curling <= 8 and -2.0 <= dist_from_sma30 <= 8.5:
                        stage2_status = "FRESH"
                        is_fresh_stage2 = True
                    elif dist_from_sma30 > 15.0 or weeks_curling > 16:
                        stage2_status = "EXTENDED"
                    else:
                        stage2_status = "ADVANCING"
        except Exception:
            pass

        has_vol_surge = bool(vol_profile.get("vol_surge") or (vol_profile.get("vol_ratio", 1.0) >= 1.25) or (vol_profile.get("vol_trend") == "ACCUMULATING"))
        stage2_curl_surge = bool(is_30w_curl and has_vol_surge)

        # StockVerdicts lookup (cached)
        sv_verdict = sv_signal = sv_fair = None
        try:
            from backend.services.stock_verdicts import get_stock_verdict
            sv = get_stock_verdict(ticker)
            if sv and not sv.get("error"):
                sv_verdict = sv.get("verdict")
                sv_signal = sv.get("signal")
                sv_fair = sv.get("levels", {}).get("Fair")
        except Exception:
            pass

        return {
            "ticker":            ticker,
            "price":             price,
            "is_30w_curl":       is_30w_curl,
            "is_fresh_stage2":   is_fresh_stage2,
            "stage2_status":     stage2_status,
            "dist_from_sma30":   dist_from_sma30,
            "weeks_curling":     weeks_curling,
            "has_vol_surge":     has_vol_surge,
            "stage2_curl_surge": stage2_curl_surge,
            "sma30":             sma30_val,
            "sma30_slope":       sma30_slope,
            "verdict":      verdict,
            "sv_verdict":   sv_verdict,
            "sv_signal":    sv_signal,
            "sv_fair":      sv_fair,
            "confidence":   confidence,
            "score":        score,
            "direction":    direction,
            "entry_status": entry_status,
            "entry_grade":  grade["entry_grade"],
            "entry_label":  grade["entry_label"],
            "grade_color":  grade["grade_color"],
            "expected_wr":  grade["expected_wr"],
            "expected_avg": grade.get("expected_avg", 0),
            "mtf_rank":     signal["rank"],
            "mtf_signal":   signal["signal"],
            "mtf_action":   signal["action"],
            "mtf_key":      signal["key"],
            "weekly_bias":  weekly,
            "daily_bias":   daily_b,
            "h4_bias":      scored.get("h4_bias", daily_b),
            "ma_bias":      ma_bias,
            "day_candle":   day_candle,
            "vol_trend":    vol_profile.get("vol_trend", "N/A"),
            "vol_surge":    vol_profile.get("vol_surge", False),
            "vol_ratio":    round(vol_profile.get("vol_ratio", 1.0), 1) if vol_profile.get("vol_ratio") else None,
            "poc":          vol_profile.get("poc"),
            "val":          vol_profile.get("val"),
            "vah":          vol_profile.get("vah"),
            "breakout_score": strategy_sig.get("breakout_score", 0),
            "dist_from_high": strategy_sig.get("dist_from_high", None),
            "short_pct":    short_pct,
            "entry":        trade.get("entry", price),
            "stop_loss":    trade.get("stop_loss"),
            "target1":      trade.get("target1"),
            "target2":      trade.get("target2"),
            "t1_days":      trade.get("t1_days"),
            "t2_days":      trade.get("t2_days"),
            "risk_pct":     trade.get("risk_pct"),
            "rr_t1":        trade.get("rr_t1"),
            "rr_t2":        trade.get("rr_t2"),
            "atr":          trade.get("atr"),
            "cpr_type":     strategy_sig.get("cpr_type", "Normal"),
            "sector":       sector,
            "pe_ratio":     pe_ratio,
            "earnings_growth": earnings_growth,
            "profit_margin": profit_margin,
            "analyst_target": analyst_target,
            "target_1y":    analyst_target,
            "target_upside": target_upside,
            "news":         news,
            "flags":        scored.get("signals", []),
            "alpaca_options": opt_summary,
            "opt_strategy":  opt_strategy,
            "opt_summary":   opt_summary,
            "opt_debit":     opt_debit,
            "opt_profit":    opt_profit,
            "opt_source":    opt_source,
            "opt_quote_ts":  opt_quote_ts,
            "opt_legs":      opt_legs,
            "opt_width":     opt_width,
            "opt_exp_short": opt_exp_short,
            "opt_exp_long":  opt_exp_long,
            "opt_alt":       opt_alt,
            "error":         None,
        }
    except Exception as e:
        return {"ticker": ticker, "error": str(e)[:120], "score": 0}


# ── Parallel scan yielding results as they complete ───────────────────────────

def scan_watchlist_stream(tickers: list[str], max_workers: int = 12) -> Iterator[dict]:
    """Yields scan results one-by-one as each ticker finishes."""
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = {pool.submit(scan_single, t): t for t in tickers}
        for fut in as_completed(futures):
            yield fut.result()
