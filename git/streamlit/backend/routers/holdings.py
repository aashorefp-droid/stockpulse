import sys, os, sqlite3, math
from datetime import date, timedelta
from typing import Optional, List, Dict, Any
from concurrent.futures import ThreadPoolExecutor, as_completed

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
import pandas as pd
import yfinance as yf

_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from backend.services.analysis import (
    calc_fib_levels, get_fundamentals, full_score_pipeline,
    get_entry_grade, calc_trade_levels, compute_weekly_bias,
    compute_daily_bias, compute_4h_bias, mtf_signal_action, _col,
)
from backend.services.options import get_options_strategy
from backend.services.market_data import get_daily_bars_alpaca
from backend.config import ALPACA_API_KEY, ALPACA_API_SECRET

router = APIRouter(prefix="/api/holdings", tags=["holdings"])

_DB_PATH = os.path.join(_ROOT, "stockpulse_trades.db")
_HOLDINGS_CSV = os.path.join(_ROOT, "my_holdings.csv")
_LEGACY_CSV = os.path.join(_ROOT, "holdings.csv")


def _safe_float(val, default=None):
    if val is None or val == "" or val == "N/A":
        return default
    try:
        f = float(val)
        return f if not math.isnan(f) else default
    except Exception:
        return default


def _get_db():
    conn = sqlite3.connect(_DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("""
        CREATE TABLE IF NOT EXISTS holdings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ticker TEXT NOT NULL,
            quantity REAL NOT NULL DEFAULT 0,
            avg_cost REAL NOT NULL DEFAULT 0,
            added_date TEXT DEFAULT (date('now')),
            notes TEXT
        )
    """)
    conn.commit()
    existing_cols = [c[1] for c in conn.execute("PRAGMA table_info(holdings)").fetchall()]
    if "updated_at" not in existing_cols:
        try:
            conn.execute("ALTER TABLE holdings ADD COLUMN updated_at TEXT")
            conn.commit()
        except Exception:
            pass
    try:
        conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_holdings_ticker ON holdings (ticker)")
        conn.commit()
    except Exception:
        pass
    return conn


def _sync_csv():
    """Sync database holdings to my_holdings.csv for Streamlit parity."""
    try:
        conn = _get_db()
        rows = conn.execute("SELECT ticker, quantity AS shares, avg_cost FROM holdings ORDER BY ticker ASC").fetchall()
        conn.close()
        records = [dict(r) for r in rows]
        df = pd.DataFrame(records)
        df.to_csv(_HOLDINGS_CSV, index=False)
    except Exception:
        pass


def _seed_if_empty():
    """Seed DB from my_holdings.csv or holdings.csv if holdings table is empty."""
    try:
        conn = _get_db()
        count = conn.execute("SELECT COUNT(*) FROM holdings").fetchone()[0]
        if count == 0:
            csv_path = _HOLDINGS_CSV if os.path.exists(_HOLDINGS_CSV) else (_LEGACY_CSV if os.path.exists(_LEGACY_CSV) else None)
            if csv_path:
                try:
                    df = pd.read_csv(csv_path)
                    for _, row in df.iterrows():
                        tk = str(row.get("ticker", "")).strip().upper()
                        if not tk and "symbol" in row:
                            tk = str(row.get("symbol", "")).strip().upper()
                        if not tk:
                            continue
                        qty = float(row.get("shares", row.get("quantity", 0)) or 0)
                        cost = float(row.get("avg_cost", row.get("cost", 0)) or 0)
                        conn.execute("""
                            INSERT OR IGNORE INTO holdings (ticker, quantity, avg_cost, added_date)
                            VALUES (?, ?, ?, date('now'))
                        """, (tk, qty, cost))
                    conn.commit()
                except Exception:
                    pass
        conn.close()
    except Exception:
        pass


_seed_if_empty()


class HoldingItem(BaseModel):
    ticker: str
    quantity: Optional[float] = None
    shares: Optional[float] = None
    avg_cost: float = 0.0
    notes: Optional[str] = None


class HoldingsReportRequest(BaseModel):
    tickers: Optional[str] = None
    as_of: Optional[str] = None


@router.get("")
def list_holdings():
    conn = _get_db()
    try:
        rows = conn.execute("SELECT * FROM holdings ORDER BY ticker ASC").fetchall()
        items = []
        tickers = []
        for r in rows:
            d = dict(r)
            qty = d.get("quantity", 0)
            if qty is None:
                qty = 0.0
            d["shares"] = qty
            d["quantity"] = qty
            items.append(d)
            tickers.append(d["ticker"])

        # Fetch recent prices for live market value calculations
        prices: Dict[str, float] = {}
        if tickers:
            try:
                data = yf.download(tickers, period="1d", progress=False, auto_adjust=True)
                if hasattr(data, "columns") and "Close" in data.columns and len(data) > 0:
                    last_c = data["Close"].iloc[-1]
                    for tk in tickers:
                        try:
                            val = float(last_c[tk]) if tk in last_c.index else float(last_c)
                            if not math.isnan(val):
                                prices[tk] = round(val, 2)
                        except Exception:
                            pass
            except Exception:
                pass

            # Fallback for missing tickers
            for tk in tickers:
                if tk not in prices:
                    try:
                        p = float(yf.Ticker(tk).fast_info.last_price)
                        if not math.isnan(p):
                            prices[tk] = round(p, 2)
                    except Exception:
                        prices[tk] = 0.0

        total_cost = 0.0
        total_value = 0.0
        for item in items:
            tk = item["ticker"]
            shares = float(item.get("shares", 0) or 0)
            cost = float(item.get("avg_cost", 0) or 0)
            cur_price = prices.get(tk, cost)
            cost_basis = shares * cost
            mkt_val = shares * cur_price
            pnl_dlr = mkt_val - cost_basis
            pnl_pct = ((cur_price / cost - 1) * 100) if cost > 0 else 0.0

            item["current_price"] = cur_price
            item["cost_basis"] = round(cost_basis, 2)
            item["market_value"] = round(mkt_val, 2)
            item["pnl_dollars"] = round(pnl_dlr, 2)
            item["pnl_pct"] = round(pnl_pct, 2)

            total_cost += cost_basis
            total_value += mkt_val

        tot_pnl_dlr = total_value - total_cost
        tot_pnl_pct = ((total_value / total_cost - 1) * 100) if total_cost > 0 else 0.0

        return {
            "items": items,
            "summary": {
                "total_positions": len(items),
                "total_invested": round(total_cost, 2),
                "total_market_value": round(total_value, 2),
                "total_pnl_dollars": round(tot_pnl_dlr, 2),
                "total_pnl_pct": round(tot_pnl_pct, 2),
            }
        }
    finally:
        conn.close()


@router.post("")
def add_or_update_holding(item: HoldingItem):
    conn = _get_db()
    try:
        t = item.ticker.upper().strip()
        qty = item.shares if item.shares is not None else (item.quantity if item.quantity is not None else 0.0)
        existing = conn.execute("SELECT id FROM holdings WHERE ticker = ?", (t,)).fetchone()
        if existing:
            conn.execute("""
                UPDATE holdings
                SET quantity = ?, avg_cost = ?, notes = ?, updated_at = datetime('now')
                WHERE ticker = ?
            """, (qty, item.avg_cost, item.notes, t))
        else:
            conn.execute("""
                INSERT INTO holdings (ticker, quantity, avg_cost, notes, added_date, updated_at)
                VALUES (?, ?, ?, ?, date('now'), datetime('now'))
            """, (t, qty, item.avg_cost, item.notes))
        conn.commit()
        _sync_csv()
        return {"status": "ok", "ticker": t, "shares": qty, "avg_cost": item.avg_cost}
    finally:
        conn.close()


@router.delete("/{ticker}")
def delete_holding(ticker: str):
    conn = _get_db()
    try:
        t = ticker.upper().strip()
        conn.execute("DELETE FROM holdings WHERE ticker = ?", (t,))
        conn.commit()
        _sync_csv()
        return {"status": "ok", "deleted": t}
    finally:
        conn.close()


@router.post("/clear")
def clear_all_holdings():
    conn = _get_db()
    try:
        conn.execute("DELETE FROM holdings")
        conn.commit()
        _sync_csv()
        return {"status": "ok", "cleared": True}
    finally:
        conn.close()


def _analyze_holding_stock(ticker: str, as_of: Optional[str] = None, cost_info: Optional[dict] = None) -> dict:
    try:
        if as_of:
            end_d = date.fromisoformat(as_of)
            if end_d.weekday() == 5:
                end_d = end_d - timedelta(days=1)
            elif end_d.weekday() == 6:
                end_d = end_d - timedelta(days=2)
        else:
            end_d = date.today()

        start_d = end_d - timedelta(days=400)

        daily_df = None
        try:
            daily_df = get_daily_bars_alpaca(ticker, str(start_d), str(end_d), ALPACA_API_KEY, ALPACA_API_SECRET)
        except Exception:
            pass

        if daily_df is None or daily_df.empty:
            hist = yf.Ticker(ticker).history(start=str(start_d), end=str(end_d + timedelta(days=1)), interval="1d")
            if not hist.empty:
                hist.columns = [c.lower() for c in hist.columns]
                daily_df = hist

        if daily_df is None or len(daily_df) < 15:
            return {"ticker": ticker, "error": "Insufficient data"}

        close_c = _col(daily_df, "close")
        high_c = _col(daily_df, "high")
        low_c = _col(daily_df, "low")
        open_c = _col(daily_df, "open")
        vol_c = _col(daily_df, "volume")

        price = round(float(daily_df[close_c].iloc[-1]), 2)

        # Scoring & Bias
        scored = full_score_pipeline(daily_df)
        verdict = scored.get("verdict", "NEUTRAL")
        confidence = scored.get("confidence", "N/A")
        score = scored.get("score", 0)
        direction = "SHORT" if verdict in ("BEARISH", "LEAN BEARISH") else "LONG"

        grade_info = get_entry_grade(score, confidence)
        entry_grade = grade_info.get("entry_grade", "C")
        entry_label = grade_info.get("entry_label", "N/A")
        expected_wr = grade_info.get("expected_wr", 50)
        expected_avg = grade_info.get("expected_avg", 0.0)

        weekly_b = compute_weekly_bias(daily_df)
        daily_b = compute_daily_bias(daily_df)
        try:
            h4_b = compute_4h_bias(daily_df)
        except Exception:
            h4_b = daily_b

        sig_action = mtf_signal_action(weekly_b, daily_b, h4_b)
        mtf_rank = sig_action.get("rank", 5)
        mtf_signal = sig_action.get("signal", "No edge")
        mtf_action = sig_action.get("action", "Sit out")

        # Moving average bias (vs 30 MA)
        try:
            ma30 = float(daily_df[close_c].rolling(30).mean().iloc[-1])
            ma_bias = "Bull" if price >= ma30 else "Bear"
        except Exception:
            ma_bias = "Bull" if direction == "LONG" else "Bear"

        # Trade setup
        trade = calc_trade_levels(daily_df, verdict, price)
        entry_status = "ENTER" if mtf_rank in (1, 2) and verdict != "NEUTRAL" else ("HOLD" if mtf_rank == 3 else "SKIP")

        # Volume Profile & Trend
        vol_profile = scored.get("vol_profile") or {}
        vol_trend = scored.get("vol_trend", "NORMAL")
        vol_ratio = scored.get("vol_ratio", 1.0)

        # Fundamentals
        fund = {}
        try:
            fund = get_fundamentals(ticker) or {}
        except Exception:
            pass

        pe_ratio = _safe_float(fund.get("pe_ratio"))
        forward_pe = _safe_float(fund.get("forward_pe"))
        peg_ratio = _safe_float(fund.get("peg_ratio"))
        rev_growth = _safe_float(fund.get("revenue_growth"))
        eps_growth = _safe_float(fund.get("earnings_growth"))
        profit_margin = _safe_float(fund.get("profit_margin"))
        roe = _safe_float(fund.get("roe"))
        debt_to_equity = _safe_float(fund.get("debt_to_equity"))
        beta = _safe_float(fund.get("beta"))
        dividend_yield = _safe_float(fund.get("dividend_yield"))
        short_pct = _safe_float(fund.get("short_pct_float"))
        target_upside = _safe_float(fund.get("target_upside"))
        analyst_target = _safe_float(fund.get("analyst_target") or fund.get("target_1y"))
        sector = fund.get("sector", "N/A")

        # Fundamental Label: Strong / Weak / Neutral
        f_score = 0
        if rev_growth is not None:
            if rev_growth > 0.20: f_score += 2
            elif rev_growth > 0.05: f_score += 1
            elif rev_growth < -0.05: f_score -= 2
        if eps_growth is not None:
            if eps_growth > 0.25: f_score += 2
            elif eps_growth > 0.05: f_score += 1
            elif eps_growth < -0.10: f_score -= 2
        if profit_margin is not None:
            if profit_margin > 0.20: f_score += 1
            elif profit_margin < 0: f_score -= 2
        if roe is not None:
            if roe > 0.15: f_score += 1
            elif roe < 0: f_score -= 1
        if pe_ratio is not None:
            if 0 < pe_ratio < 15: f_score += 1
            elif pe_ratio > 40: f_score -= 1
        if debt_to_equity is not None:
            if debt_to_equity < 30: f_score += 1
            elif debt_to_equity > 200: f_score -= 1
        if target_upside is not None:
            if target_upside > 20: f_score += 1
            elif target_upside < -15: f_score -= 1

        funda_label = "Strong" if f_score >= 3 else ("Weak" if f_score <= -2 else "Neutral")

        # News sentiment
        news_sentiment = "No"
        try:
            from news_sentiment import get_news_sentiment
            news_sentiment = get_news_sentiment(ticker)
        except Exception:
            pass

        # ── Fib Calculations (10-day swing) ──────────────────────────
        hh = daily_df[high_c].squeeze() if hasattr(daily_df[high_c], 'squeeze') else daily_df[high_c]
        hl = daily_df[low_c].squeeze() if hasattr(daily_df[low_c], 'squeeze') else daily_df[low_c]
        wk_hi = float(hh.iloc[-10:].max())
        wk_lo = float(hl.iloc[-10:].min())
        wk_rng = wk_hi - wk_lo
        wk_pos = round(((price - wk_lo) / wk_rng * 100), 1) if wk_rng > 0 else 50.0

        if wk_pos >= 70:
            weekly_zone = "HIGH"
        elif wk_pos <= 30:
            weekly_zone = "LOW"
        else:
            weekly_zone = "MID"

        # Daily Zone
        day_hi = float(daily_df[high_c].iloc[-1])
        day_lo = float(daily_df[low_c].iloc[-1])
        day_rng = day_hi - day_lo
        day_pos = round(((price - day_lo) / day_rng * 100), 1) if day_rng > 0 else 50.0
        daily_zone = "HIGH" if day_pos >= 70 else ("LOW" if day_pos <= 30 else "MID")

        fib_levels = calc_fib_levels(wk_lo, wk_hi)
        fib_sorted = sorted(fib_levels.items(), key=lambda kv: kv[1])

        # Nearest Fib
        nearest_fib = min(fib_levels.items(), key=lambda kv: abs(kv[1] - price))
        nearest_fib_str = f"{nearest_fib[0]} (${nearest_fib[1]:.2f})"

        # Fib compression: 3+ levels within 3% of range
        fc_vals = sorted(fib_levels.values())
        fc_thresh = wk_rng * 0.03 if wk_rng > 0 else 0
        fib_compression = False
        if len(fc_vals) >= 3 and fc_thresh > 0:
            for fi in range(len(fc_vals) - 2):
                if fc_vals[fi + 2] - fc_vals[fi] <= fc_thresh:
                    fib_compression = True
                    break

        # Golden Zone (Price between R 38.2% and R 61.8%)
        r38 = fib_levels.get("R 38.2%", 0)
        r61 = fib_levels.get("R 61.8%", 0)
        gz_min = min(r38, r61)
        gz_max = max(r38, r61)
        in_golden_zone = (gz_min <= price <= gz_max) if (gz_min > 0 and gz_max > 0) else False

        # ── Covered Call Strategy Formulation ────────────────────────
        otm_fibs = [(n, v) for n, v in fib_sorted if v > price]
        if otm_fibs:
            cc_name, cc_raw = otm_fibs[0]
            cc_strike = round(cc_raw)
        else:
            cc_name = "Above range"
            cc_strike = round(price * 1.05)

        if len(otm_fibs) >= 2:
            cc2_name, cc2_raw = otm_fibs[1]
            cc2_strike = round(cc2_raw)
        else:
            cc2_strike = cc_strike + (round(wk_rng * 0.2) if wk_rng > 0 else 5)

        exp_weekly = (end_d + timedelta(days=7)).strftime("%Y-%m-%d")
        exp_monthly = (end_d + timedelta(days=30)).strftime("%Y-%m-%d")
        cc_pct = round(((cc_strike - price) / price * 100), 1) if price > 0 else 0.0

        if weekly_zone == "HIGH":
            cc_strategy = (
                f"Sell ${cc_strike} Call ({cc_name}, {cc_pct}% OTM) Exp {exp_weekly} — "
                f"HIGH zone, tight strike for max premium | Aggressive: Sell ${cc2_strike} Call Exp {exp_monthly}"
            )
        elif weekly_zone == "LOW":
            cc_strategy = (
                f"Sell ${cc2_strike} Call ({cc_pct}%+ OTM) Exp {exp_monthly} — "
                f"LOW zone, wider strike to allow upside | Conservative: Sell ${cc_strike} Call ({cc_name}) Exp {exp_weekly}"
            )
        else:
            cc_strategy = (
                f"Sell ${cc_strike} Call ({cc_name}, {cc_pct}% OTM) Exp {exp_monthly} — "
                f"MID zone, balanced premium vs upside | Alt: ${cc2_strike} Call Exp {exp_monthly}"
            )

        # Alpaca Options Check
        alpaca_opts = "N/A"
        try:
            strat = get_options_strategy(ticker, price, "SHORT" if weekly_zone == "HIGH" else "LONG", ALPACA_API_KEY, ALPACA_API_SECRET)
            if strat and strat.get("summary"):
                alpaca_opts = strat["summary"]
                if strat.get("alt"):
                    alpaca_opts += f" | {strat['alt']}"
        except Exception:
            pass

        # ── Enrich Portfolio Cost Basis ──────────────────────────────
        shares = float(cost_info.get("shares", 0) or 0) if cost_info else 0.0
        avg_cost = float(cost_info.get("avg_cost", 0) or 0) if cost_info else 0.0
        cost_basis = shares * avg_cost
        mkt_val = shares * price
        pnl_dlr = mkt_val - cost_basis
        pnl_pct = ((price / avg_cost - 1) * 100) if avg_cost > 0 else 0.0

        # Build Fundamental Flags
        flags = []
        if rev_growth and rev_growth > 0.20: flags.append("Rev Growth > 20%")
        elif rev_growth and rev_growth > 0.05: flags.append("Rev Growth > 5%")
        elif rev_growth and rev_growth < -0.05: flags.append("Rev Decline < -5%")

        if eps_growth and eps_growth > 0.25: flags.append("EPS Growth > 25%")
        elif eps_growth and eps_growth > 0.05: flags.append("EPS Growth > 5%")
        elif eps_growth and eps_growth < -0.10: flags.append("EPS Decline < -10%")

        if profit_margin and profit_margin > 0.20: flags.append("High Margin > 20%")
        elif profit_margin and profit_margin < 0: flags.append("Negative Margin")

        if roe and roe > 0.15: flags.append("High ROE > 15%")
        if debt_to_equity and debt_to_equity < 30: flags.append("Low Debt (<30 D/E)")
        elif debt_to_equity and debt_to_equity > 200: flags.append("High Debt (>200 D/E)")

        if pe_ratio and 0 < pe_ratio < 15: flags.append("Low P/E (<15)")
        elif pe_ratio and pe_ratio > 40: flags.append("High P/E (>40)")

        if target_upside and target_upside > 20: flags.append(f"Analyst Upside +{target_upside:.0f}%")
        elif target_upside and target_upside < -15: flags.append(f"Analyst Downside {target_upside:.0f}%")

        if dividend_yield and dividend_yield > 0.015: flags.append("Dividend Payer")

        # Rank determination
        is_exceptional_long = (confidence == "HIGH" and score >= 4 and expected_wr >= 80 and mtf_signal == "A+ Long" and vol_trend == "ACCUMULATING")
        is_exceptional_bear = (confidence == "HIGH" and score <= -4 and expected_wr >= 80 and mtf_signal == "A+ Short" and vol_trend == "DISTRIBUTING")

        # StockVerdicts lookup
        sv = None
        try:
            from backend.services.stock_verdicts import get_stock_verdict
            sv = get_stock_verdict(ticker)
        except Exception:
            pass

        return {
            "ticker": ticker,
            "price": price,
            "shares": shares,
            "avg_cost": avg_cost,
            "cost_basis": round(cost_basis, 2),
            "market_value": round(mkt_val, 2),
            "pnl_dollars": round(pnl_dlr, 2),
            "pnl_pct": round(pnl_pct, 2),
            "verdict": verdict,
            "confidence": confidence,
            "score": score,
            "sv_verdict": sv.get("verdict") if sv else None,
            "sv_signal": sv.get("signal") if sv else None,
            "sv_fair_value": sv.get("levels", {}).get("Fair") if sv else None,
            "sv_trust": sv.get("trust_score") if sv else None,
            "sv_pros": sv.get("pros", []) if sv else [],
            "sv_cons": sv.get("cons", []) if sv else [],
            "entry_grade": entry_grade,
            "entry_label": entry_label,
            "entry_status": entry_status,
            "expected_wr": expected_wr,
            "expected_avg": round(expected_avg, 2),
            "weekly_bias": weekly_b,
            "daily_bias": daily_b,
            "h4_bias": h4_b,
            "ma_bias": ma_bias,
            "mtf_signal": mtf_signal,
            "mtf_action": mtf_action,
            "mtf_rank": mtf_rank,
            "is_exceptional_long": is_exceptional_long,
            "is_exceptional_bear": is_exceptional_bear,
            "entry": trade.get("entry", price),
            "stop_loss": trade.get("stop_loss"),
            "target1": trade.get("target1"),
            "target2": trade.get("target2"),
            "t1_days": trade.get("t1_days"),
            "risk_pct": trade.get("risk_pct"),
            "rr_t1": trade.get("rr_t1"),
            "rr_t2": trade.get("rr_t2"),
            "vol_trend": vol_trend,
            "vol_ratio": vol_ratio,
            "fundamental": funda_label,
            "pe_ratio": pe_ratio,
            "forward_pe": forward_pe,
            "peg_ratio": peg_ratio,
            "rev_growth": rev_growth,
            "eps_growth": eps_growth,
            "profit_margin": profit_margin,
            "roe": roe,
            "debt_to_equity": debt_to_equity,
            "beta": beta,
            "dividend_yield": dividend_yield,
            "short_pct": short_pct,
            "target_upside": target_upside,
            "analyst_target": analyst_target,
            "sector": sector,
            "news": news_sentiment,
            "flags": flags,
            # Fib & Covered Call
            "wk_hi": round(wk_hi, 2),
            "wk_lo": round(wk_lo, 2),
            "wk_pos": wk_pos,
            "weekly_zone": weekly_zone,
            "daily_zone": daily_zone,
            "nearest_fib": nearest_fib_str,
            "fib_compression": fib_compression,
            "in_golden_zone": in_golden_zone,
            "fib_levels": {k: round(v, 2) for k, v in fib_levels.items()},
            "covered_call": cc_strategy,
            "alpaca_options": alpaca_opts,
        }
    except Exception as e:
        return {"ticker": ticker, "error": str(e)}


@router.post("/report")
def generate_holdings_report(req: HoldingsReportRequest):
    ticker_list = []
    if req.tickers and req.tickers.strip():
        ticker_list = [t.strip().upper() for t in req.tickers.replace(",", " ").split() if t.strip()]
    else:
        conn = _get_db()
        rows = conn.execute("SELECT ticker, quantity AS shares, avg_cost FROM holdings ORDER BY ticker ASC").fetchall()
        conn.close()
        ticker_list = [r["ticker"] for r in rows]

    if not ticker_list:
        return {
            "error": "No tickers provided and no saved holdings found. Add holdings or provide tickers to scan.",
            "results": [],
            "covered_calls": [],
            "golden_zone": [],
            "fib_zones": {"high": [], "mid": [], "low": []},
            "matrix": {"columns": [], "rows": []},
            "summary": {"total": 0, "bullish": 0, "bearish": 0, "high_conf": 0, "actionable": 0}
        }

    conn = _get_db()
    db_rows = conn.execute("SELECT ticker, quantity AS shares, avg_cost FROM holdings").fetchall()
    conn.close()
    cost_map = {r["ticker"].upper(): dict(r) for r in db_rows}

    results = []
    with ThreadPoolExecutor(max_workers=4) as executor:
        futures = {
            executor.submit(_analyze_holding_stock, tk, req.as_of, cost_map.get(tk)): tk
            for tk in ticker_list
        }
        for future in as_completed(futures):
            res = future.result()
            if res and not res.get("error"):
                results.append(res)

    results.sort(key=lambda x: x["ticker"])

    exceptional = [r for r in results if r.get("is_exceptional_long")]
    exceptional_bear = [r for r in results if r.get("is_exceptional_bear")]
    rank1 = [r for r in results if r.get("mtf_rank") == 1 and r.get("entry_status") == "ENTER"]
    rank2 = [r for r in results if r.get("mtf_rank") == 2 and r.get("entry_status") == "ENTER"]
    rank3 = [r for r in results if (r.get("mtf_rank") or 5) >= 3 and r.get("entry_status") == "ENTER"]

    covered_calls = [
        {
            "ticker": r["ticker"],
            "price": r["price"],
            "signal": r["mtf_signal"],
            "covered_call": r["covered_call"],
            "alpaca_options": r["alpaca_options"],
        }
        for r in results
    ]

    golden_zone = [
        {
            "ticker": r["ticker"],
            "price": r["price"],
            "r_38_2": r["fib_levels"].get("R 38.2%"),
            "r_50_0": r["fib_levels"].get("R 50.0%"),
            "r_61_8": r["fib_levels"].get("R 61.8%"),
            "weekly_zone": r["weekly_zone"],
        }
        for r in results if r.get("in_golden_zone")
    ]

    fib_high = [r for r in results if r.get("weekly_zone") == "HIGH"]
    fib_mid = [r for r in results if r.get("weekly_zone") == "MID"]
    fib_low = [r for r in results if r.get("weekly_zone") == "LOW"]

    all_flags = set()
    for r in results:
        for f in r.get("flags", []):
            all_flags.add(f)

    pos_flags = sorted([f for f in all_flags if any(k in f for k in ["Growth", "High", "Low Debt", "Low P/E", "Dividend", "Upside"])])
    neg_flags = sorted([f for f in all_flags if any(k in f for k in ["Decline", "Negative", "High Debt", "High P/E", "Downside"])])
    other_flags = sorted([f for f in all_flags if f not in pos_flags and f not in neg_flags])
    sorted_matrix_cols = pos_flags + other_flags + neg_flags

    matrix_rows = []
    for r in results:
        row_flags = set(r.get("flags", []))
        matrix_rows.append({
            "ticker": r["ticker"],
            "has_flags": {col: (col in row_flags) for col in sorted_matrix_cols}
        })

    bullish_count = sum(1 for r in results if r.get("verdict") in ("BULLISH", "LEAN BULLISH"))
    bearish_count = sum(1 for r in results if r.get("verdict") in ("BEARISH", "LEAN BEARISH"))
    high_conf_count = sum(1 for r in results if r.get("confidence") == "HIGH")
    actionable_count = sum(1 for r in results if r.get("entry_status") == "ENTER")

    return {
        "results": results,
        "subtabs": {
            "exceptional": exceptional,
            "exceptional_bear": exceptional_bear,
            "rank1": rank1,
            "rank2": rank2,
            "rank3": rank3,
            "all": results,
        },
        "covered_calls": covered_calls,
        "golden_zone": golden_zone,
        "fib_zones": {
            "high": fib_high,
            "mid": fib_mid,
            "low": fib_low,
        },
        "matrix": {
            "columns": sorted_matrix_cols,
            "rows": matrix_rows,
        },
        "summary": {
            "total_scanned": len(results),
            "bullish": bullish_count,
            "bearish": bearish_count,
            "high_conf": high_conf_count,
            "actionable": actionable_count,
        }
    }
