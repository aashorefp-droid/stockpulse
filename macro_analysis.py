"""
Macro Market Analysis — Indices, Volatility, Commodities, Bonds, Sector Strength.
Extracted from stock_pulse.py for modularity.
"""

import streamlit as st
import pandas as pd
from datetime import date, timedelta
from collections import defaultdict

try:
    import yfinance as yf
    YFINANCE_AVAILABLE = True
except ImportError:
    YFINANCE_AVAILABLE = False


# ──────────────────────────────────────────────
# MACRO MARKET INDICATORS
# ──────────────────────────────────────────────

MACRO_INSTRUMENTS = [
    # (ticker, label, emoji, category)
    ("SPY",   "S&P 500",   "📈", "index"),
    ("QQQ",   "Nasdaq",    "💻", "index"),
    ("DIA",   "Dow Jones", "🏛️", "index"),
    ("IWM",   "Russell 2K","🏘️", "index"),
    ("^VIX",  "VIX",       "🌡️", "fear"),
    ("GLD",   "Gold",      "🥇", "commodity"),
    ("SLV",   "Silver",    "🥈", "commodity"),
    ("USO",   "Oil",       "⛽", "commodity"),
    ("TLT",   "Bonds 20Y", "🏦", "bonds"),
    ("DXY",   "USD Index", "💵", "currency"),
    ("BTC-USD","Bitcoin",  "₿",  "crypto"),
]


# Sector ETFs with their top holdings
SECTOR_ETFS = {
    # ── Broad Market Indices ──────────────────────────────────────────────────
    "SPY":  {"name": "S&P 500",        "emoji": "📈", "stocks": ["AAPL", "MSFT", "AMZN", "NVDA", "GOOGL", "META", "TSLA", "BRK-B", "UNH", "XOM"]},
    "QQQ":  {"name": "NASDAQ 100",     "emoji": "💻", "stocks": ["AAPL", "MSFT", "NVDA", "AMZN", "META", "GOOGL", "TSLA", "AVGO", "COST", "AMD"]},
    "VTI":  {"name": "Total US Market","emoji": "🌐", "stocks": ["AAPL", "MSFT", "AMZN", "NVDA", "GOOGL", "META", "TSLA", "BRK-B", "UNH", "LLY"]},
    "SOXX": {"name": "Semiconductors", "emoji": "⚡", "stocks": ["NVDA", "AVGO", "AMD", "QCOM", "TXN", "AMAT", "LRCX", "KLAC", "MRVL", "ON"]},
    "IWM":  {"name": "Small Cap (R2K)", "emoji": "🔬", "stocks": ["SMCI", "INSM", "ONTO", "IRTC", "CAVA", "CLDX", "FTDR", "TGTX", "DOCS", "ENVA"]},
    # ── S&P Sectors ──────────────────────────────────────────────────────────
    "XLK": {"name": "Technology", "emoji": "💻", "stocks": ["AAPL", "MSFT", "NVDA", "AVGO", "AMD", "CRM", "ADBE", "ORCL", "CSCO", "ACN"]},
    "XLF": {"name": "Financials", "emoji": "🏦", "stocks": ["JPM", "V", "MA", "BAC", "WFC", "GS", "MS", "SPGI", "BLK", "AXP"]},
    "XLE": {"name": "Energy", "emoji": "⛽", "stocks": ["XOM", "CVX", "COP", "EOG", "SLB", "MPC", "PSX", "VLO", "OXY", "HAL"]},
    "XLV": {"name": "Healthcare", "emoji": "🏥", "stocks": ["UNH", "JNJ", "LLY", "PFE", "ABBV", "MRK", "TMO", "ABT", "DHR", "BMY"]},
    "XLY": {"name": "Consumer Disc", "emoji": "🛒", "stocks": ["AMZN", "TSLA", "HD", "MCD", "NKE", "LOW", "SBUX", "TJX", "BKNG", "CMG"]},
    "XLI": {"name": "Industrials", "emoji": "🏭", "stocks": ["CAT", "UNP", "HON", "UPS", "BA", "RTX", "DE", "LMT", "GE", "MMM"]},
    "XLP": {"name": "Cons Staples", "emoji": "🧴", "stocks": ["PG", "KO", "PEP", "COST", "WMT", "PM", "MO", "CL", "MDLZ", "EL"]},
    "XLU": {"name": "Utilities", "emoji": "💡", "stocks": ["NEE", "DUK", "SO", "D", "AEP", "SRE", "EXC", "XEL", "PEG", "ED"]},
    "XLC": {"name": "Communication", "emoji": "📱", "stocks": ["META", "GOOGL", "GOOG", "NFLX", "DIS", "CMCSA", "VZ", "T", "TMUS", "CHTR"]},
    "XLB": {"name": "Materials", "emoji": "🧱", "stocks": ["LIN", "APD", "SHW", "ECL", "FCX", "NEM", "NUE", "DOW", "DD", "VMC"]},
    "XLRE": {"name": "Real Estate", "emoji": "🏠", "stocks": ["PLD", "AMT", "EQIX", "PSA", "CCI", "O", "WELL", "SPG", "DLR", "AVB"]},
}


@st.cache_data(ttl=300, show_spinner=False)
def get_macro_snapshot():
    """
    Fetch current price, 1d change, 5d change, and 20d change for macro instruments.
    Uses yfinance (free, no key needed). Returns list of dicts.
    """
    if not YFINANCE_AVAILABLE:
        return []
    results = []
    tickers = [t for t, *_ in MACRO_INSTRUMENTS]
    try:
        data = yf.download(tickers, period="30d", interval="1d",
                           auto_adjust=True, progress=False, threads=True)
        close = data["Close"] if "Close" in data.columns else data
    except Exception:
        return []

    for ticker, label, emoji, category in MACRO_INSTRUMENTS:
        try:
            if ticker not in close.columns:
                continue
            s = close[ticker].dropna()
            if len(s) < 2:
                continue
            price      = float(s.iloc[-1])
            chg_1d     = (price - float(s.iloc[-2])) / float(s.iloc[-2]) * 100 if len(s) >= 2 else 0
            chg_5d     = (price - float(s.iloc[-6])) / float(s.iloc[-6]) * 100 if len(s) >= 6 else chg_1d
            chg_20d    = (price - float(s.iloc[-21])) / float(s.iloc[-21]) * 100 if len(s) >= 21 else chg_5d
            results.append({
                "ticker":   ticker,
                "label":    label,
                "emoji":    emoji,
                "category": category,
                "price":    round(price, 2),
                "chg_1d":   round(chg_1d, 2),
                "chg_5d":   round(chg_5d, 2),
                "chg_20d":  round(chg_20d, 2),
            })
        except Exception:
            continue
    return results


def _macro_card_html(item):
    """Render a single macro instrument as an HTML card."""
    chg = item["chg_1d"]
    color = "#00e5a0" if chg > 0 else ("#ff4d6a" if chg < 0 else "#6b7099")
    sign  = "+" if chg > 0 else ""
    chg5_color = "#00e5a0" if item["chg_5d"] > 0 else ("#ff4d6a" if item["chg_5d"] < 0 else "#6b7099")
    chg5_sign  = "+" if item["chg_5d"] > 0 else ""
    chg20_color= "#00e5a0" if item["chg_20d"]>0 else ("#ff4d6a" if item["chg_20d"]<0 else "#6b7099")
    chg20_sign = "+" if item["chg_20d"] > 0 else ""

    # Special: VIX is inverse — high VIX = fear = bearish for market
    if item["ticker"] == "^VIX":
        risk = "🔴 Fear" if item["price"] > 25 else ("🟡 Caution" if item["price"] > 18 else "🟢 Calm")
        sub_line = f'<div style="font-size:9px;color:#f0c040;margin-top:2px">{risk}</div>'
    else:
        sub_line = f'<div style="font-size:9px;color:#6b7099;margin-top:2px">5d <span style="color:{chg5_color}">{chg5_sign}{item["chg_5d"]:.1f}%</span> · 20d <span style="color:{chg20_color}">{chg20_sign}{item["chg_20d"]:.1f}%</span></div>'

    return (
        f'<div style="background:#0d0f17;border:1px solid #1a1d2e;padding:10px 12px;'
        f'border-radius:6px;min-width:110px">'
        f'<div style="font-size:10px;color:#6b7099">{item["emoji"]} {item["label"]}</div>'
        f'<div style="font-size:15px;font-weight:700;color:#e8ecff;margin-top:2px">${item["price"]:,.2f}</div>'
        f'<div style="font-size:11px;font-weight:600;color:{color}">{sign}{chg:.2f}%</div>'
        f'{sub_line}'
        f'</div>'
    )


def _render_sector_bar_chart(sector_list, score_key, label_key, detail_fn):
    """Render a horizontal bar chart for sector strength."""
    if not sector_list:
        return
    max_abs = max(abs(s[score_key]) for s in sector_list) or 1
    bar_html = ""
    for s in sector_list:
        sc   = s[score_key]
        pct  = abs(sc) / max_abs * 100
        col  = "#00e5a0" if sc > 0 else ("#ff4d6a" if sc < 0 else "#6b7099")
        icon = "▲" if sc > 0 else ("▼" if sc < 0 else "—")
        bar_html += (
            f'<div style="display:flex;align-items:center;gap:8px;margin-bottom:5px">'
            f'<div style="width:140px;font-size:11px;color:#e8ecff;text-align:right;flex-shrink:0;overflow:hidden;white-space:nowrap">'
            f'<span style="color:{col}">{icon}</span> {str(s[label_key])[:18]}</div>'
            f'<div style="flex:1;background:#1a1d2e;border-radius:3px;height:14px;overflow:hidden">'
            f'<div style="width:{pct:.0f}%;background:{col};height:100%;border-radius:3px"></div></div>'
            f'<div style="width:90px;font-size:10px;color:{col};flex-shrink:0">{detail_fn(s)}</div>'
            f'</div>'
        )
    st.markdown(f'<div style="padding:4px 0 8px">{bar_html}</div>', unsafe_allow_html=True)


def render_macro_dashboard(macro_data, sector_str=None, etf_sector_perf=None):
    """Render the full macro dashboard with risk assessment and sector strength."""
    if not macro_data:
        st.caption("Macro data unavailable — install yfinance to enable.")
        return

    # Split into categories
    indices   = [m for m in macro_data if m["category"] == "index"]
    fear      = [m for m in macro_data if m["category"] == "fear"]
    comms     = [m for m in macro_data if m["category"] == "commodity"]
    bonds     = [m for m in macro_data if m["category"] == "bonds"]
    other     = [m for m in macro_data if m["category"] in ("currency", "crypto")]

    # ── Market risk score ──────────────────────────────────────────────────
    risk_score = 0
    risk_notes = []
    spx = next((m for m in macro_data if m["ticker"] == "SPY"), None)
    vix = next((m for m in macro_data if m["ticker"] == "^VIX"), None)
    tlt = next((m for m in macro_data if m["ticker"] == "TLT"), None)
    gld = next((m for m in macro_data if m["ticker"] == "GLD"), None)

    if vix:
        if vix["price"] > 25:   risk_score += 2; risk_notes.append(f"VIX {vix['price']:.0f} — elevated fear")
        elif vix["price"] > 18: risk_score += 1; risk_notes.append(f"VIX {vix['price']:.0f} — mild caution")
        else:                   risk_notes.append(f"VIX {vix['price']:.0f} — calm")
    if spx and spx["chg_5d"] < -2:
        risk_score += 1; risk_notes.append(f"SPY 5d: {spx['chg_5d']:+.1f}% — market under pressure")
    if tlt and tlt["chg_5d"] > 1:
        risk_score += 1; risk_notes.append("Bonds rallying — flight to safety")
    if gld and gld["chg_5d"] > 2:
        risk_score += 1; risk_notes.append(f"Gold 5d: {gld['chg_5d']:+.1f}% — safe haven demand")

    risk_label = "🟢 LOW RISK"       if risk_score == 0 else \
                 "🟡 MODERATE RISK"  if risk_score <= 2 else \
                 "🔴 HIGH RISK"
    risk_color = "#00e5a0" if risk_score == 0 else ("#f0c040" if risk_score <= 2 else "#ff4d6a")

    st.markdown(
        f'<div style="background:#0d0f17;border:1px solid #1a1d2e;padding:10px 14px;'
        f'border-radius:6px;margin-bottom:10px;display:flex;align-items:center;gap:16px">'
        f'<div><div style="font-size:11px;color:#6b7099">MARKET RISK</div>'
        f'<div style="font-size:14px;font-weight:700;color:{risk_color}">{risk_label}</div></div>'
        f'<div style="font-size:10px;color:#6b7099;line-height:1.7">'
        + " &nbsp;·&nbsp; ".join(risk_notes) +
        f'</div></div>',
        unsafe_allow_html=True,
    )

    # ── Cards grid ─────────────────────────────────────────────────────────
    for group_label, group in [
        ("Indices", indices), ("Fear / Vol", fear),
        ("Commodities", comms), ("Bonds & Currency", bonds + other)
    ]:
        if not group:
            continue
        st.markdown(f'<div style="font-size:10px;color:#6b7099;font-weight:600;margin:8px 0 4px;letter-spacing:.05em">{group_label.upper()}</div>', unsafe_allow_html=True)
        cols_html = "".join(_macro_card_html(m) for m in group)
        st.markdown(
            f'<div style="display:flex;flex-wrap:wrap;gap:8px;margin-bottom:6px">{cols_html}</div>',
            unsafe_allow_html=True,
        )

    # ── Sector Strength ────────────────────────────────────────────────────
    st.markdown('<hr style="border:none;border-top:1px solid #1a1d2e;margin:16px 0 12px">', unsafe_allow_html=True)

    # Use scan-derived strength if available, otherwise fall back to ETF momentum
    if sector_str:
        st.markdown('<div style="font-size:10px;color:#6b7099;font-weight:600;letter-spacing:.06em;margin-bottom:8px">SECTOR STRENGTH — FROM LAST SCAN</div>', unsafe_allow_html=True)
        _render_sector_bar_chart(sector_str, score_key="avg_score", label_key="sector",
                                 detail_fn=lambda s: f'avg {s["avg_score"]:+.2f} · {s["total"]}T')
        col1, col2 = st.columns(2)
        with col1:
            st.markdown("**Strongest**")
            for s in sector_str[:3]:
                st.markdown(f"🟢 **{s['sector']}** — {s['avg_score']:+.2f}, {s['bull_pct']:.0f}% bullish")
        with col2:
            st.markdown("**Weakest**")
            for s in reversed(sector_str[-3:]):
                st.markdown(f"🔴 **{s['sector']}** — {s['avg_score']:+.2f}, {s['bearish']}/{s['total']} bearish")

    elif etf_sector_perf:
        st.markdown('<div style="font-size:10px;color:#6b7099;font-weight:600;letter-spacing:.06em;margin-bottom:8px">SECTOR STRENGTH — ETF MOMENTUM (run Stock Analysis for signal-based view)</div>', unsafe_allow_html=True)
        _render_sector_bar_chart(etf_sector_perf, score_key="momentum", label_key="name",
                                 detail_fn=lambda s: f'1d {s["change_1d"]:+.1f}% · 1w {s["change_1w"]:+.1f}%')
        hot   = [s for s in etf_sector_perf if s["momentum"] > 0][:3]
        cold  = [s for s in reversed(etf_sector_perf) if s["momentum"] <= 0][:3]
        col1, col2 = st.columns(2)
        with col1:
            st.markdown("**Strongest**")
            for s in hot:
                st.markdown(f"🟢 **{s['name']}** ({s['etf']}) — {s['momentum']:+.2f} momentum")
        with col2:
            st.markdown("**Weakest**")
            for s in cold:
                st.markdown(f"🔴 **{s['name']}** ({s['etf']}) — {s['momentum']:+.2f} momentum")
    else:
        st.caption("Run Stock Analysis to see signal-based sector strength, or wait for ETF data to load.")


def sector_strength_from_scan(scan_results):
    """
    Derive sector strength rankings from scan results.
    Returns list of dicts sorted by strength score descending.
    """
    sector_data = defaultdict(lambda: {"bullish":0,"bearish":0,"total":0,"score_sum":0,"tickers":[]})

    for r in scan_results:
        sec = r.get("sector", "N/A") or "N/A"
        if sec == "N/A":
            continue
        s = sector_data[sec]
        s["total"] += 1
        s["tickers"].append(r["ticker"])
        score = r.get("score", 0) or 0
        s["score_sum"] += score
        verdict = r.get("verdict", "")
        if "BULLISH" in verdict: s["bullish"] += 1
        elif "BEARISH" in verdict: s["bearish"] += 1

    result = []
    for sec, d in sector_data.items():
        if d["total"] == 0:
            continue
        bull_pct   = d["bullish"] / d["total"] * 100
        bear_pct   = d["bearish"] / d["total"] * 100
        avg_score  = d["score_sum"] / d["total"]
        # Strength: weighted combo of avg score and bull/bear ratio
        strength   = avg_score + (bull_pct - bear_pct) / 20
        bias       = "BULLISH" if avg_score > 0.5 else ("BEARISH" if avg_score < -0.5 else "NEUTRAL")
        result.append({
            "sector":    sec,
            "total":     d["total"],
            "bullish":   d["bullish"],
            "bearish":   d["bearish"],
            "bull_pct":  round(bull_pct, 0),
            "avg_score": round(avg_score, 2),
            "strength":  round(strength, 2),
            "bias":      bias,
            "tickers":   ", ".join(d["tickers"][:6]),
        })
    result.sort(key=lambda x: x["strength"], reverse=True)
    return result


def get_sector_performance(get_daily_bars_fn, api_key, api_secret=None):
    """
    Get performance data for all sector ETFs.
    get_daily_bars_fn: callable(ticker, start_date_str, end_date_str, api_key, api_secret) -> DataFrame
    NOTE: Caller should handle caching (st.cache_data) since this receives a callable.
    """
    results = []
    end_dt = date.today()
    start_dt = end_dt - timedelta(days=30)

    for etf, info in SECTOR_ETFS.items():
        try:
            if api_secret is not None:
                df = get_daily_bars_fn(etf, str(start_dt), str(end_dt), api_key, api_secret)
            else:
                df = get_daily_bars_fn(etf, str(start_dt), str(end_dt), api_key)

            if df.empty or len(df) < 5:
                continue

            current_price = df["close"].iloc[-1]

            # 1-day change
            if len(df) >= 2:
                prev_close = df["close"].iloc[-2]
                change_1d = ((current_price - prev_close) / prev_close) * 100
            else:
                change_1d = 0

            # 1-week change (5 trading days)
            if len(df) >= 6:
                week_ago = df["close"].iloc[-6]
                change_1w = ((current_price - week_ago) / week_ago) * 100
            else:
                change_1w = change_1d

            # 1-month change
            if len(df) >= 20:
                month_ago = df["close"].iloc[-20]
                change_1m = ((current_price - month_ago) / month_ago) * 100
            else:
                change_1m = change_1w

            # Momentum score (weighted average)
            momentum = (change_1d * 0.3) + (change_1w * 0.5) + (change_1m * 0.2)

            results.append({
                "etf": etf,
                "name": info["name"],
                "emoji": info["emoji"],
                "stocks": info["stocks"],
                "price": round(current_price, 2),
                "change_1d": round(change_1d, 2),
                "change_1w": round(change_1w, 2),
                "change_1m": round(change_1m, 2),
                "momentum": round(momentum, 2),
            })
        except Exception:
            continue

    results.sort(key=lambda x: x["momentum"], reverse=True)
    return results
