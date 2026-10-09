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
    mtf_signal_action, get_fundamentals, _col, generate_final_judgement,
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
        "NVDA", "MRVL", "AVGO", "ARM", "PLTR", "CRWD", "DDOG", "NET", "SMCI", "TSLA",
        "META", "AMZN", "GOOGL", "MSFT", "AMD", "SNOW", "ZS", "SHOP", "COIN", "UBER",
        "MSTR", "APP", "CEG", "VST", "HOOD", "TSM", "ANET", "DECK", "AXON", "HIMS",
        "SOFI", "AFRM", "RDDT", "SPOT", "DKNG", "CVNA", "SE", "WING", "BBAI", "IONQ",
        "RKLB", "ASTS", "CAVA", "PANW", "NOW", "PATH", "CELH", "DUOL", "TTD", "FSLR",
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


_V3_ALERT_LOG: dict[tuple[str, str], date] = {}


def _v3_alert_once(ticker: str, dt3: dict, tier: str = "Rank 1") -> None:
    """Send a Telegram alert for a newly detected V3 setup (deduped to 1/day)."""
    setup = dt3.get("dt3_setup")
    side = dt3.get("dt3_side")
    if setup not in {"sweep_reclaim", "break_retest"} or side not in {"long", "short"}:
        return
    if not tier:
        return
    key = (ticker.upper(), str(setup))
    today = date.today()
    if _V3_ALERT_LOG.get(key) == today:
        return

    try:
        import html
        from backend.services.telegram_svc import send_telegram
        from backend.services.scheduler import _telegram_target

        tok = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
        chat_id, thread_id = _telegram_target(
            os.getenv("TELEGRAM_SWING_MESSAGE_THREAD_ID", "")
        )
        if not (tok and chat_id):
            return

        arrow = "📈" if side == "long" else "📉"
        grade = dt3.get("dt3_grade") or ""
        level = dt3.get("dt3_level") or "?"
        lv = dt3.get("dt3_level_val")
        entry = dt3.get("dt3_entry")
        stop = dt3.get("dt3_stop")
        t1 = dt3.get("dt3_t1")
        t2 = dt3.get("dt3_t2")
        rr = dt3.get("dt3_rr")
        rationale = html.escape((dt3.get("dt3_rationale") or "")[:240])

        def _m(v): return f"${v:.2f}" if isinstance(v, (int, float)) else "—"
        def _r(v): return f"{v:.2f}×" if isinstance(v, (int, float)) else "—"

        msg = (
            f"<b>{arrow} V3 {html.escape(tier)} — {html.escape(ticker.upper())}</b>\n"
            f"<i>{html.escape(str(setup).replace('_', '+'))} · {side} · {html.escape(str(grade))}</i>\n"
            f"Lvl: {html.escape(str(level))} {_m(lv)}\n"
            f"Entry: {_m(entry)}  Stop: {_m(stop)}\n"
            f"T1: {_m(t1)}  T2: {_m(t2)}  R:R: {_r(rr)}\n"
            f"\n<i>{rationale}</i>"
        )
        send_telegram(tok, chat_id, msg, message_thread_id=thread_id)
        _V3_ALERT_LOG[key] = today
    except Exception as e:
        import logging
        logging.getLogger(__name__).warning(f"[scanner] V3 alert failed for {ticker}: {e}")


# ── Single ticker scan ────────────────────────────────────────────────────────

def scan_single(
    ticker: str,
    as_of: Optional[str] = None,
    include_news: Optional[bool] = None,
    mode: Optional[str] = None,
) -> dict:
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

        # Final Judgement confluence
        final_judgement = None
        try:
            final_judgement = generate_final_judgement(trade, vol_profile, verdict, grade, price)
        except Exception:
            pass

        # V4 day-trading: PDH/PWH/PDL/PWL plan engine
        dt4_fields = {}
        try:
            from day_trading.v4 import analyze_from_daily as _dt4_analyze
            _dt4 = _dt4_analyze(ticker, daily_df, scan_date=end, current_price=price)
            _sig = _dt4.get("signal") or {}
            _lvl = _dt4.get("levels") or {}
            dt4_fields = {
                "dt4_enabled": True,
                "dt4_setup": _sig.get("setup"),
                "dt4_context": _sig.get("context"),
                "dt4_side": _sig.get("side"),
                "dt4_bias": _sig.get("bias"),
                "dt4_grade": _sig.get("grade"),
                "dt4_level": _sig.get("level"),
                "dt4_level_val": _sig.get("level_val"),
                "dt4_entry": _sig.get("entry"),
                "dt4_stop": _sig.get("stop"),
                "dt4_t1": _sig.get("t1"),
                "dt4_t2": _sig.get("t2"),
                "dt4_rr": _sig.get("rr"),
                "dt4_trigger": _sig.get("trigger"),
                "dt4_invalidation": _sig.get("invalidation"),
                "dt4_target_plan": _sig.get("target_plan"),
                "dt4_exit_plan": _sig.get("exit_plan"),
                "dt4_note": _sig.get("note"),
                "dt4_pdh": round(_lvl.get("pdh"), 2) if _lvl.get("pdh") is not None else None,
                "dt4_pdl": round(_lvl.get("pdl"), 2) if _lvl.get("pdl") is not None else None,
                "dt4_pwh": round(_lvl.get("pwh"), 2) if _lvl.get("pwh") is not None else None,
                "dt4_pwl": round(_lvl.get("pwl"), 2) if _lvl.get("pwl") is not None else None,
                "dt4_atr": round(_lvl.get("atr"), 2) if _lvl.get("atr") is not None else None,
            }
        except Exception as _e:
            dt4_fields = {"dt4_enabled": True, "dt4_setup": "error", "dt4_note": str(_e)[:120]}

        # V3 day-trading: PDH/PWH/PDL/PWL setup engine
        dt3_fields = {}
        try:
            from day_trading.v3 import analyze as _dt3_analyze
            _dt3 = _dt3_analyze(ticker, daily=daily_df)
            _sig = _dt3.get("signal") or {}
            _lvl = _dt3.get("levels") or {}
            _tgts = _sig.get("targets") or []
            _setup_val = _sig.get("setup") or ("no_setup" if _lvl else "error")
            dt3_fields = {
                "dt3_setup": _setup_val,
                "dt3_side": _sig.get("side"),
                "dt3_grade": _sig.get("grade"),
                "dt3_level": _sig.get("level"),
                "dt3_level_val": _sig.get("level_val"),
                "dt3_entry": _sig.get("entry"),
                "dt3_stop": _sig.get("stop"),
                "dt3_t1": _tgts[0] if len(_tgts) >= 1 else None,
                "dt3_t2": _tgts[1] if len(_tgts) >= 2 else None,
                "dt3_rr": _sig.get("rr"),
                "dt3_rationale": _sig.get("rationale"),
                "dt3_pdh": _lvl.get("pdh"),
                "dt3_pdl": _lvl.get("pdl"),
                "dt3_pwh": _lvl.get("pwh"),
                "dt3_pwl": _lvl.get("pwl"),
            }
        except Exception:
            pass

        out_row = {
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
            "vol_bias":     vol_profile.get("vol_bias", "NEUTRAL"),
            "below_va":     bool(price < vol_profile.get("val", 0)) if (vol_profile.get("val") is not None and price is not None) else False,
            "vol_detail":   vol_profile.get("detail", ""),
            "poc":          vol_profile.get("poc"),
            "val":          vol_profile.get("val"),
            "vah":          vol_profile.get("vah"),
            "hi_52":        vol_profile.get("hi_52"),
            "lo_52":        vol_profile.get("lo_52"),
            "dist_hi_52":   vol_profile.get("dist_hi_52"),
            "dist_lo_52":   vol_profile.get("dist_lo_52"),
            "final_judgement": final_judgement,
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
            "retest_entry": trade.get("retest_entry"),
            "retest_zone_min": trade.get("retest_zone_min"),
            "retest_zone_max": trade.get("retest_zone_max"),
            "retest_label": trade.get("retest_label"),
            "retest_diff_pct": trade.get("retest_diff_pct"),
            "pw_low": trade.get("pw_low"),
            "pw_latest_low": trade.get("pw_latest_low"),
            "pw_latest_date": trade.get("pw_latest_date"),
            "pw_latest_day": trade.get("pw_latest_day"),
            "pw_latest_diff_pct": trade.get("pw_latest_diff_pct"),
            "pw_latest_red_low": trade.get("pw_latest_red_low"),
            "pw_latest_red_date": trade.get("pw_latest_red_date"),
            "pw_latest_red_day": trade.get("pw_latest_red_day"),
            "pw_latest_red_diff_pct": trade.get("pw_latest_red_diff_pct"),
            "pw_avg_low": trade.get("pw_avg_low"),
            "pw_avg_diff_pct": trade.get("pw_avg_diff_pct"),
            "pw_range_label": trade.get("pw_range_label"),
            "last_breakout": trade.get("last_breakout"),
            "last_breakout_date": trade.get("last_breakout_date"),
            "last_breakout_date_label": trade.get("last_breakout_date_label"),
            "last_breakout_days_ago": trade.get("last_breakout_days_ago"),
            "last_breakout_price": trade.get("last_breakout_price"),
            "last_breakout_level": trade.get("last_breakout_level"),
            "last_breakout_gain_pct": trade.get("last_breakout_gain_pct"),
            "last_breakout_vol_ratio": trade.get("last_breakout_vol_ratio"),
            "last_breakout_type": trade.get("last_breakout_type"),
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
            **dt4_fields,
            **dt3_fields,
            "error":         None,
        }
        return out_row
    except Exception as e:
        return {"ticker": ticker, "error": str(e)[:120], "score": 0}


# ── Parallel scan yielding results as they complete ───────────────────────────

def scan_watchlist_stream(tickers: list[str], max_workers: int = 12) -> Iterator[dict]:
    """Yields scan results one-by-one as each ticker finishes."""
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = {pool.submit(scan_single, t): t for t in tickers}
        for fut in as_completed(futures):
            yield fut.result()
