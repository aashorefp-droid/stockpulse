"""
News Theme Scanner — Previous-day sentiment analysis by theme/sector.
Identifies which themes had positive news sentiment yesterday,
suggests stocks to buy next trading day, and backtests the strategy.

Run:  streamlit run news_theme_scanner.py
"""

import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

try:
    import streamlit as st
except ImportError:
    class _DummyStreamlit:
        def cache_data(self, *args, **kwargs):
            def decorator(f):
                return f
            return decorator
        def cache_resource(self, *args, **kwargs):
            def decorator(f):
                return f
            return decorator
        def __getattr__(self, name):
            def noop(*args, **kwargs):
                return None
            return noop
    st = _DummyStreamlit()
import pandas as pd
import numpy as np
import requests
import time
import json
from datetime import datetime, timedelta, date
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed

try:
    import yfinance as yf
    YFINANCE_OK = True
except ImportError:
    YFINANCE_OK = False

try:
    import plotly.graph_objects as go
    import plotly.express as px
    PLOTLY_OK = True
except ImportError:
    PLOTLY_OK = False

from config import FINNHUB_API_KEY, ALPHA_VANTAGE_KEY
from news_sentiment import get_news_details, _GOOD_WORDS, _BAD_WORDS

# ═══════════════════════════════════════════════════════════════════════════════
# THEME / SECTOR DEFINITIONS
# ═══════════════════════════════════════════════════════════════════════════════
THEMES = {
    "🤖 AI / Machine Learning": {
        "tickers": ["NVDA", "AMD", "MSFT", "GOOGL", "META", "ORCL", "PLTR", "AI",
                     "SOUN", "AAPL", "TSM", "AVGO", "SMCI", "MRVL", "QCOM"],
        "keywords": ["artificial intelligence", "ai ", "machine learning", "gpu",
                      "chatgpt", "openai", "generative ai", "neural", "deep learning",
                      "llm", "large language model", "copilot", "data center"],
    },
    "🔬 Quantum Computing": {
        "tickers": ["IBM", "IONQ", "QUBT", "RGTI", "QTUM", "ARQQ", "GOOGL"],
        "keywords": ["quantum", "qubit", "quantum computing", "quantum chip"],
    },
    "💊 Biotech / Pharma": {
        "tickers": ["CRSP", "EDIT", "NTLA", "BEAM", "MRNA", "BNTX", "REGN",
                     "ILMN", "LLY", "ABBV", "PFE", "JNJ", "AMGN", "GILD", "BMY"],
        "keywords": ["fda", "drug approval", "clinical trial", "biotech", "pharma",
                      "gene therapy", "crispr", "vaccine", "drug", "treatment",
                      "breakthrough therapy", "phase 3", "new drug"],
    },
    "⚡ Clean Energy": {
        "tickers": ["ENPH", "FSLR", "SEDG", "NEE", "PLUG", "BE", "CHPT",
                     "BLDP", "ARRY", "RUN", "TAN", "ICLN"],
        "keywords": ["solar", "wind", "renewable", "clean energy", "green energy",
                      "ev", "electric vehicle", "battery", "charging", "hydrogen",
                      "carbon neutral", "sustainability"],
    },
    "🛡️ Defense / Gov Contracts": {
        "tickers": ["LMT", "RTX", "NOC", "GD", "HII", "CACI", "LDOS", "BAH",
                     "SAIC", "KTOS", "PLTR"],
        "keywords": ["defense", "military", "pentagon", "contract", "missile",
                      "cybersecurity", "government contract", "dod", "nato"],
    },
    "🚀 Space Technology": {
        "tickers": ["RKLB", "ASTS", "SPCE", "LMT", "NOC", "BA", "RTX"],
        "keywords": ["space", "satellite", "launch", "rocket", "orbit",
                      "spacex", "nasa", "constellation"],
    },
    "🏦 Financials / Banks": {
        "tickers": ["JPM", "BAC", "GS", "MS", "WFC", "C", "SCHW", "BLK",
                     "AXP", "V", "MA", "PYPL", "SQ"],
        "keywords": ["bank", "interest rate", "fed", "financial", "lending",
                      "credit", "mortgage", "fintech", "payment", "banking"],
    },
    "🏗️ Infrastructure / Industrial": {
        "tickers": ["CAT", "DE", "HON", "GE", "MMM", "UNP", "EMR", "ETN",
                     "ROK", "CMI", "URI"],
        "keywords": ["infrastructure", "construction", "industrial", "manufacturing",
                      "supply chain", "reshoring", "factory", "automation"],
    },
    "🛒 Consumer / Retail": {
        "tickers": ["AMZN", "WMT", "TGT", "COST", "HD", "LOW", "NKE",
                     "SBUX", "MCD", "DIS", "NFLX"],
        "keywords": ["consumer", "retail", "spending", "e-commerce", "earnings beat",
                      "same-store sales", "holiday", "demand"],
    },
    "💻 Semiconductors": {
        "tickers": ["NVDA", "AMD", "INTC", "TSM", "AVGO", "QCOM", "MRVL",
                     "LRCX", "AMAT", "KLAC", "MU", "TXN", "ON", "SMCI"],
        "keywords": ["chip", "semiconductor", "wafer", "fab", "chipmaker",
                      "processor", "memory", "foundry", "asic"],
    },
    "☁️ Cloud / SaaS": {
        "tickers": ["AMZN", "MSFT", "GOOGL", "CRM", "SNOW", "NET", "DDOG",
                     "ZS", "CRWD", "PANW", "OKTA", "MDB", "NOW"],
        "keywords": ["cloud", "saas", "aws", "azure", "gcp", "subscription",
                      "cloud computing", "cybersecurity", "security"],
    },
}

# ═══════════════════════════════════════════════════════════════════════════════
# FINNHUB NEWS FETCHER
# ═══════════════════════════════════════════════════════════════════════════════
@st.cache_data(ttl=1800, show_spinner=False)
def fetch_finnhub_market_news(category: str = "general") -> list[dict]:
    """Fetch market news from Finnhub. Returns list of articles."""
    if not FINNHUB_API_KEY:
        return []
    try:
        resp = requests.get(
            "https://finnhub.io/api/v1/news",
            params={"category": category, "token": FINNHUB_API_KEY},
            timeout=10,
        )
        if resp.status_code == 200:
            return resp.json()
    except Exception:
        pass
    return []


@st.cache_data(ttl=1800, show_spinner=False)
def fetch_finnhub_company_news(ticker: str, from_date: str, to_date: str) -> list[dict]:
    """Fetch company-specific news from Finnhub for a date range."""
    if not FINNHUB_API_KEY:
        return []
    try:
        resp = requests.get(
            "https://finnhub.io/api/v1/company-news",
            params={
                "symbol": ticker,
                "from": from_date,
                "to": to_date,
                "token": FINNHUB_API_KEY,
            },
            timeout=10,
        )
        if resp.status_code == 200:
            articles = resp.json()
            return articles[:20] if isinstance(articles, list) else []
    except Exception:
        pass
    return []


# ═══════════════════════════════════════════════════════════════════════════════
# SENTIMENT SCORING
# ═══════════════════════════════════════════════════════════════════════════════
def score_headline(headline: str) -> dict:
    """Score a single headline. Returns {good, bad, sentiment, headline}."""
    words = set(headline.lower().split())
    g = len(words & _GOOD_WORDS)
    b = len(words & _BAD_WORDS)
    sentiment = "Good" if g > b else ("Bad" if b > g else "Neutral")
    return {"headline": headline, "good": g, "bad": b, "sentiment": sentiment}


def classify_article_themes(headline: str, summary: str = "") -> list[str]:
    """Return list of theme names that match this article."""
    text = (headline + " " + summary).lower()
    matched = []
    for theme_name, theme_info in THEMES.items():
        for kw in theme_info["keywords"]:
            if kw in text:
                matched.append(theme_name)
                break
    return matched


def scan_theme_sentiment(theme_name: str, theme_info: dict, scan_date: date) -> dict:
    """
    Scan a single theme: fetch news for each ticker, score headlines,
    and return aggregated sentiment.
    """
    from_str = str(scan_date)
    to_str = str(scan_date)

    all_headlines = []
    ticker_scores = {}
    total_good = 0
    total_bad = 0

    for ticker in theme_info["tickers"]:
        # Finnhub company news
        articles = fetch_finnhub_company_news(ticker, from_str, to_str)
        ticker_good = 0
        ticker_bad = 0
        ticker_headlines = []

        for art in articles:
            headline = art.get("headline", "")
            if not headline:
                continue
            scored = score_headline(headline)
            scored["ticker"] = ticker
            scored["source"] = art.get("source", "")
            scored["url"] = art.get("url", "")
            scored["datetime"] = datetime.fromtimestamp(art.get("datetime", 0)).strftime("%Y-%m-%d %H:%M") if art.get("datetime") else ""
            ticker_good += scored["good"]
            ticker_bad += scored["bad"]
            ticker_headlines.append(scored)

        # Also try Finviz for extra coverage
        try:
            finviz_data = get_news_details(ticker)
            for h in finviz_data.get("headlines", []):
                if h["headline"] not in [x["headline"] for x in ticker_headlines]:
                    h["ticker"] = ticker
                    h["source"] = h.get("source", "finviz")
                    h["url"] = ""
                    h["datetime"] = h.get("date", "") + " " + h.get("time", "")
                    ticker_good += h.get("good", 0)
                    ticker_bad += h.get("bad", 0)
                    ticker_headlines.append(h)
        except Exception:
            pass

        total_good += ticker_good
        total_bad += ticker_bad
        if ticker_headlines:
            net = ticker_good - ticker_bad
            ticker_scores[ticker] = {
                "good": ticker_good,
                "bad": ticker_bad,
                "net": net,
                "headline_count": len(ticker_headlines),
                "sentiment": "Bullish" if net > 0 else ("Bearish" if net < 0 else "Neutral"),
            }
        all_headlines.extend(ticker_headlines)

    net_score = total_good - total_bad
    if net_score > 2:
        theme_sentiment = "🟢 Bullish"
    elif net_score < -2:
        theme_sentiment = "🔴 Bearish"
    else:
        theme_sentiment = "⚪ Neutral"

    # Top bullish tickers in this theme
    bullish_tickers = sorted(
        [t for t, s in ticker_scores.items() if s["net"] > 0],
        key=lambda t: ticker_scores[t]["net"],
        reverse=True,
    )

    return {
        "theme": theme_name,
        "sentiment": theme_sentiment,
        "net_score": net_score,
        "good_total": total_good,
        "bad_total": total_bad,
        "headline_count": len(all_headlines),
        "ticker_scores": ticker_scores,
        "bullish_tickers": bullish_tickers,
        "all_headlines": all_headlines,
    }


def scan_market_news_themes(scan_date: date) -> dict:
    """Scan Finnhub general market news and classify into themes."""
    articles = fetch_finnhub_market_news("general")
    theme_articles = defaultdict(list)

    for art in articles:
        headline = art.get("headline", "")
        summary = art.get("summary", "")
        if not headline:
            continue
        ts = art.get("datetime", 0)
        if ts:
            art_date = datetime.fromtimestamp(ts).date()
            if art_date != scan_date:
                continue
        themes = classify_article_themes(headline, summary)
        scored = score_headline(headline)
        scored["source"] = art.get("source", "")
        scored["url"] = art.get("url", "")
        for t in themes:
            theme_articles[t].append(scored)

    return dict(theme_articles)


# ═══════════════════════════════════════════════════════════════════════════════
# STOCK PICKING — next-day suggestions
# ═══════════════════════════════════════════════════════════════════════════════
def get_next_day_picks(theme_results: list[dict], top_n: int = 10) -> pd.DataFrame:
    """
    From scanned themes, pick top stocks from bullish themes.
    Returns DataFrame with suggested buys.
    """
    picks = []
    # Sort themes by net score descending
    bullish_themes = sorted(
        [t for t in theme_results if t["net_score"] > 0],
        key=lambda x: x["net_score"],
        reverse=True,
    )

    seen_tickers = set()
    for theme in bullish_themes:
        for ticker in theme["bullish_tickers"]:
            if ticker in seen_tickers:
                continue
            seen_tickers.add(ticker)
            ts = theme["ticker_scores"].get(ticker, {})
            picks.append({
                "Ticker": ticker,
                "Theme": theme["theme"],
                "Theme Score": theme["net_score"],
                "Ticker Net": ts.get("net", 0),
                "Headlines": ts.get("headline_count", 0),
                "Signal": ts.get("sentiment", "Neutral"),
            })
            if len(picks) >= top_n:
                break
        if len(picks) >= top_n:
            break

    if not picks:
        return pd.DataFrame()

    df = pd.DataFrame(picks)

    # Enrich with price data if yfinance available
    if YFINANCE_OK:
        prices = []
        for _, row in df.iterrows():
            try:
                tk = yf.Ticker(row["Ticker"])
                hist = tk.history(period="5d")
                if not hist.empty:
                    prices.append({
                        "Ticker": row["Ticker"],
                        "Last Close": round(hist["Close"].iloc[-1], 2),
                        "5d Chg%": round((hist["Close"].iloc[-1] / hist["Close"].iloc[0] - 1) * 100, 2),
                        "Avg Vol": int(hist["Volume"].mean()),
                    })
                else:
                    prices.append({"Ticker": row["Ticker"], "Last Close": None, "5d Chg%": None, "Avg Vol": None})
            except Exception:
                prices.append({"Ticker": row["Ticker"], "Last Close": None, "5d Chg%": None, "Avg Vol": None})
        pdf = pd.DataFrame(prices)
        df = df.merge(pdf, on="Ticker", how="left")

    return df


# ═══════════════════════════════════════════════════════════════════════════════
# BACKTESTING ENGINE
# ═══════════════════════════════════════════════════════════════════════════════
def backtest_theme_strategy(
    lookback_days: int = 30,
    hold_days: int = 1,
    top_n_per_day: int = 5,
    min_theme_score: int = 2,
    progress_callback=None,
) -> pd.DataFrame:
    """
    Backtest: For each trading day in lookback, scan previous day's news,
    pick top stocks from bullish themes, buy at open, sell after hold_days.
    Returns DataFrame of all trades.
    """
    if not YFINANCE_OK:
        return pd.DataFrame()

    end_date = date.today()
    start_date = end_date - timedelta(days=lookback_days + 10)

    # Get trading days from SPY
    spy = yf.Ticker("SPY")
    spy_hist = spy.history(start=str(start_date), end=str(end_date + timedelta(days=5)))
    if spy_hist.empty:
        return pd.DataFrame()

    trading_days = [d.date() for d in spy_hist.index]
    if len(trading_days) < 5:
        return pd.DataFrame()

    # Limit to requested lookback
    test_days = trading_days[-lookback_days:] if len(trading_days) > lookback_days else trading_days

    all_trades = []
    total_steps = len(test_days)

    # Pre-download price data for all theme tickers
    all_tickers = set()
    for theme_info in THEMES.values():
        all_tickers.update(theme_info["tickers"])
    all_tickers = sorted(all_tickers)

    # Download bulk price data
    price_cache = {}
    batch_size = 20
    for i in range(0, len(all_tickers), batch_size):
        batch = all_tickers[i:i + batch_size]
        try:
            data = yf.download(
                " ".join(batch),
                start=str(start_date),
                end=str(end_date + timedelta(days=10)),
                progress=False,
                group_by="ticker",
            )
            if len(batch) == 1:
                price_cache[batch[0]] = data
            else:
                for t in batch:
                    try:
                        price_cache[t] = data[t] if t in data.columns.get_level_values(0) else pd.DataFrame()
                    except Exception:
                        price_cache[t] = pd.DataFrame()
        except Exception:
            pass

    for step, trade_day in enumerate(test_days):
        if progress_callback:
            progress_callback(step / total_steps, f"Backtesting {trade_day} ({step+1}/{total_steps})")

        # Find previous trading day
        idx = trading_days.index(trade_day) if trade_day in trading_days else -1
        if idx <= 0:
            continue
        prev_day = trading_days[idx - 1]

        # Simulate news scan for prev_day using price momentum as proxy
        # (We can't fetch historical news, so we use price action + volume as proxy)
        theme_scores = {}
        for theme_name, theme_info in THEMES.items():
            theme_good = 0
            theme_bad = 0
            ticker_momentum = {}

            for ticker in theme_info["tickers"]:
                df_t = price_cache.get(ticker)
                if df_t is None or df_t.empty:
                    continue
                try:
                    # Get prev_day data
                    mask = df_t.index.date <= prev_day
                    sub = df_t[mask]
                    if len(sub) < 3:
                        continue

                    close_prev = float(sub["Close"].iloc[-1])
                    close_2ago = float(sub["Close"].iloc[-2])
                    vol_prev = float(sub["Volume"].iloc[-1])
                    vol_avg = float(sub["Volume"].iloc[-5:].mean()) if len(sub) >= 5 else vol_prev

                    day_ret = (close_prev / close_2ago - 1) * 100
                    vol_ratio = vol_prev / vol_avg if vol_avg > 0 else 1.0

                    # Positive price + above-avg volume = "good news" proxy
                    if day_ret > 0.5 and vol_ratio > 1.1:
                        theme_good += 2
                    elif day_ret > 0:
                        theme_good += 1
                    elif day_ret < -0.5 and vol_ratio > 1.1:
                        theme_bad += 2
                    elif day_ret < 0:
                        theme_bad += 1

                    ticker_momentum[ticker] = {
                        "ret": day_ret,
                        "vol_ratio": vol_ratio,
                        "close": close_prev,
                    }
                except Exception:
                    continue

            net = theme_good - theme_bad
            theme_scores[theme_name] = {
                "net": net,
                "good": theme_good,
                "bad": theme_bad,
                "tickers": ticker_momentum,
            }

        # Pick top stocks from bullish themes
        bullish_themes = sorted(
            [(name, info) for name, info in theme_scores.items() if info["net"] >= min_theme_score],
            key=lambda x: x[1]["net"],
            reverse=True,
        )

        day_picks = []
        seen = set()
        for theme_name, theme_info in bullish_themes:
            # Sort tickers by momentum
            sorted_tickers = sorted(
                theme_info["tickers"].items(),
                key=lambda x: x[1]["ret"] * x[1]["vol_ratio"],
                reverse=True,
            )
            for ticker, tdata in sorted_tickers:
                if ticker in seen:
                    continue
                if tdata["ret"] <= 0:
                    continue
                seen.add(ticker)
                day_picks.append((ticker, theme_name, theme_info["net"], tdata))
                if len(day_picks) >= top_n_per_day:
                    break
            if len(day_picks) >= top_n_per_day:
                break

        # Execute trades: buy at open on trade_day, sell after hold_days
        for ticker, theme, t_score, tdata in day_picks:
            df_t = price_cache.get(ticker)
            if df_t is None or df_t.empty:
                continue
            try:
                future = df_t[df_t.index.date >= trade_day]
                if len(future) < 1 + hold_days:
                    continue
                buy_price = float(future["Open"].iloc[0])
                sell_price = float(future["Close"].iloc[hold_days - 1]) if hold_days == 1 else float(future["Close"].iloc[hold_days - 1])
                pnl_pct = (sell_price / buy_price - 1) * 100

                all_trades.append({
                    "Date": trade_day,
                    "Ticker": ticker,
                    "Theme": theme,
                    "Theme Score": t_score,
                    "Prev Day Ret%": round(tdata["ret"], 2),
                    "Buy Price": round(buy_price, 2),
                    "Sell Price": round(sell_price, 2),
                    "PnL%": round(pnl_pct, 2),
                    "Hold Days": hold_days,
                })
            except Exception:
                continue

    if progress_callback:
        progress_callback(1.0, "Done!")

    return pd.DataFrame(all_trades)


# ═══════════════════════════════════════════════════════════════════════════════
# STREAMLIT UI
# ═══════════════════════════════════════════════════════════════════════════════
def main():
    st.set_page_config(page_title="📰 News Theme Scanner", layout="wide", page_icon="📰")
    st.title("📰 News Theme Scanner — Next-Day Prep")
    st.caption("Scan previous day's news sentiment by theme, identify bullish sectors, and get stock picks for tomorrow.")

    tab_scan, tab_backtest = st.tabs(["🔍 Theme Scanner", "📊 Backtest"])

    # ── TAB 1: THEME SCANNER ──────────────────────────────────────────────
    with tab_scan:
        col1, col2, col3 = st.columns([2, 1, 1])
        with col1:
            scan_date = st.date_input(
                "Scan Date (previous trading day)",
                value=date.today() - timedelta(days=1),
                max_value=date.today(),
            )
        with col2:
            top_n = st.slider("Max stock picks", 5, 20, 10)
        with col3:
            selected_themes = st.multiselect(
                "Filter themes",
                options=list(THEMES.keys()),
                default=[],
                placeholder="All themes",
            )

        if st.button("🔍 Scan Previous Day News", type="primary", use_container_width=True):
            themes_to_scan = {k: v for k, v in THEMES.items() if k in selected_themes} if selected_themes else THEMES

            progress = st.progress(0, text="Scanning themes...")
            theme_results = []
            total = len(themes_to_scan)

            for i, (theme_name, theme_info) in enumerate(themes_to_scan.items()):
                progress.progress((i + 1) / total, text=f"Scanning {theme_name}...")
                result = scan_theme_sentiment(theme_name, theme_info, scan_date)
                theme_results.append(result)
                time.sleep(0.1)  # Rate limit courtesy

            # Also scan general market news
            progress.progress(1.0, text="Classifying market news...")
            market_themes = scan_market_news_themes(scan_date)
            progress.empty()

            # ── Summary Dashboard ──
            st.subheader(f"📊 Theme Sentiment Summary — {scan_date}")

            # Build summary table
            summary_rows = []
            for r in sorted(theme_results, key=lambda x: x["net_score"], reverse=True):
                summary_rows.append({
                    "Theme": r["theme"],
                    "Sentiment": r["sentiment"],
                    "Net Score": r["net_score"],
                    "Good": r["good_total"],
                    "Bad": r["bad_total"],
                    "Headlines": r["headline_count"],
                    "Top Bullish": ", ".join(r["bullish_tickers"][:5]) if r["bullish_tickers"] else "—",
                })

            if summary_rows:
                df_summary = pd.DataFrame(summary_rows)
                st.dataframe(df_summary, use_container_width=True, hide_index=True)

                # Sentiment bar chart
                if PLOTLY_OK:
                    fig = go.Figure()
                    themes_sorted = sorted(theme_results, key=lambda x: x["net_score"])
                    colors = ["#2ecc71" if r["net_score"] > 0 else ("#e74c3c" if r["net_score"] < 0 else "#95a5a6") for r in themes_sorted]
                    fig.add_trace(go.Bar(
                        y=[r["theme"] for r in themes_sorted],
                        x=[r["net_score"] for r in themes_sorted],
                        orientation="h",
                        marker_color=colors,
                        text=[r["net_score"] for r in themes_sorted],
                        textposition="outside",
                    ))
                    fig.update_layout(
                        title="Theme Sentiment Scores",
                        xaxis_title="Net Sentiment Score",
                        height=max(350, len(themes_sorted) * 40),
                        margin=dict(l=200),
                    )
                    st.plotly_chart(fig, use_container_width=True)

            # ── Next-Day Picks ──
            st.subheader("🎯 Next-Day Stock Picks")
            st.caption("Stocks from bullish themes — candidates to buy at tomorrow's open")

            df_picks = get_next_day_picks(theme_results, top_n=top_n)
            if not df_picks.empty:
                # Color code the signal
                st.dataframe(
                    df_picks,
                    use_container_width=True,
                    hide_index=True,
                    column_config={
                        "Theme Score": st.column_config.NumberColumn(format="%d"),
                        "Ticker Net": st.column_config.NumberColumn(format="%d"),
                        "Last Close": st.column_config.NumberColumn(format="$%.2f"),
                        "5d Chg%": st.column_config.NumberColumn(format="%.2f%%"),
                        "Avg Vol": st.column_config.NumberColumn(format="%d"),
                    },
                )

                # Summary box
                bullish_count = len([t for t in theme_results if t["net_score"] > 0])
                bearish_count = len([t for t in theme_results if t["net_score"] < 0])
                st.info(
                    f"**{bullish_count}** bullish themes, **{bearish_count}** bearish themes. "
                    f"**{len(df_picks)}** stock picks selected from top themes."
                )
            else:
                st.warning("No bullish themes found for this date. No stock picks to suggest.")

            # ── Detailed Headlines (Expandable) ──
            st.subheader("📰 Detailed Headlines by Theme")
            for r in sorted(theme_results, key=lambda x: x["net_score"], reverse=True):
                with st.expander(f"{r['theme']} — {r['sentiment']} (score: {r['net_score']}, {r['headline_count']} headlines)"):
                    if r["all_headlines"]:
                        hdf = pd.DataFrame(r["all_headlines"])
                        display_cols = [c for c in ["ticker", "headline", "sentiment", "good", "bad", "source", "datetime"] if c in hdf.columns]
                        st.dataframe(hdf[display_cols], use_container_width=True, hide_index=True)
                    else:
                        st.write("No headlines found.")

                    # Ticker breakdown
                    if r["ticker_scores"]:
                        st.write("**Ticker Breakdown:**")
                        ts_df = pd.DataFrame([
                            {"Ticker": t, **s} for t, s in r["ticker_scores"].items()
                        ]).sort_values("net", ascending=False)
                        st.dataframe(ts_df, use_container_width=True, hide_index=True)

    # ── TAB 2: BACKTEST ───────────────────────────────────────────────────
    with tab_backtest:
        st.subheader("📊 Backtest: Buy Bullish-Theme Stocks at Open")
        st.caption(
            "Strategy: Each day, scan previous day's price momentum as a news-sentiment proxy. "
            "Buy top stocks from bullish themes at next day's open, sell at close (or after N days)."
        )

        col_a, col_b, col_c, col_d = st.columns(4)
        with col_a:
            bt_lookback = st.number_input("Lookback days", 10, 120, 30, step=5)
        with col_b:
            bt_hold = st.selectbox("Hold period", [1, 2, 3, 5], index=0, format_func=lambda x: f"{x} day{'s' if x > 1 else ''}")
        with col_c:
            bt_top_n = st.slider("Picks per day", 1, 10, 5)
        with col_d:
            bt_min_score = st.slider("Min theme score", 1, 5, 2)

        if st.button("▶️ Run Backtest", type="primary", use_container_width=True):
            if not YFINANCE_OK:
                st.error("yfinance is required for backtesting. Run: pip install yfinance")
            else:
                progress = st.progress(0, text="Starting backtest...")

                def update_progress(pct, text):
                    progress.progress(min(pct, 1.0), text=text)

                with st.spinner("Running backtest..."):
                    df_trades = backtest_theme_strategy(
                        lookback_days=bt_lookback,
                        hold_days=bt_hold,
                        top_n_per_day=bt_top_n,
                        min_theme_score=bt_min_score,
                        progress_callback=update_progress,
                    )
                progress.empty()

                if df_trades.empty:
                    st.warning("No trades generated. Try increasing lookback or reducing min theme score.")
                else:
                    # ── Performance Summary ──
                    st.subheader("📈 Backtest Results")

                    total_trades = len(df_trades)
                    win_trades = len(df_trades[df_trades["PnL%"] > 0])
                    lose_trades = len(df_trades[df_trades["PnL%"] < 0])
                    avg_pnl = df_trades["PnL%"].mean()
                    total_pnl = df_trades["PnL%"].sum()
                    win_rate = (win_trades / total_trades * 100) if total_trades > 0 else 0
                    avg_win = df_trades[df_trades["PnL%"] > 0]["PnL%"].mean() if win_trades > 0 else 0
                    avg_loss = df_trades[df_trades["PnL%"] < 0]["PnL%"].mean() if lose_trades > 0 else 0
                    profit_factor = abs(avg_win * win_trades / (avg_loss * lose_trades)) if lose_trades > 0 and avg_loss != 0 else float("inf")
                    max_win = df_trades["PnL%"].max()
                    max_loss = df_trades["PnL%"].min()

                    m1, m2, m3, m4, m5, m6 = st.columns(6)
                    m1.metric("Total Trades", total_trades)
                    m2.metric("Win Rate", f"{win_rate:.1f}%")
                    m3.metric("Avg PnL", f"{avg_pnl:.2f}%", delta_color="normal")
                    m4.metric("Total PnL", f"{total_pnl:.2f}%")
                    m5.metric("Profit Factor", f"{profit_factor:.2f}" if profit_factor != float("inf") else "∞")
                    m6.metric("Max Win / Loss", f"+{max_win:.2f}% / {max_loss:.2f}%")

                    st.divider()

                    # ── Equity Curve ──
                    if PLOTLY_OK:
                        df_trades_sorted = df_trades.sort_values("Date")
                        df_trades_sorted["Cumulative PnL%"] = df_trades_sorted["PnL%"].cumsum()

                        fig_eq = go.Figure()
                        fig_eq.add_trace(go.Scatter(
                            x=df_trades_sorted["Date"],
                            y=df_trades_sorted["Cumulative PnL%"],
                            mode="lines+markers",
                            name="Cumulative PnL%",
                            line=dict(color="#2ecc71" if total_pnl > 0 else "#e74c3c", width=2),
                            fill="tozeroy",
                            fillcolor="rgba(46,204,113,0.1)" if total_pnl > 0 else "rgba(231,76,60,0.1)",
                        ))
                        fig_eq.update_layout(
                            title="Equity Curve (Cumulative PnL%)",
                            xaxis_title="Date",
                            yaxis_title="Cumulative PnL%",
                            height=400,
                        )
                        st.plotly_chart(fig_eq, use_container_width=True)

                    # ── PnL Distribution ──
                    col_chart1, col_chart2 = st.columns(2)
                    with col_chart1:
                        if PLOTLY_OK:
                            fig_hist = px.histogram(
                                df_trades, x="PnL%", nbins=30,
                                title="PnL Distribution",
                                color_discrete_sequence=["#3498db"],
                            )
                            fig_hist.add_vline(x=0, line_dash="dash", line_color="red")
                            fig_hist.update_layout(height=350)
                            st.plotly_chart(fig_hist, use_container_width=True)

                    with col_chart2:
                        if PLOTLY_OK:
                            # PnL by Theme
                            theme_pnl = df_trades.groupby("Theme")["PnL%"].agg(["mean", "sum", "count"]).reset_index()
                            theme_pnl.columns = ["Theme", "Avg PnL%", "Total PnL%", "Trades"]
                            theme_pnl = theme_pnl.sort_values("Total PnL%", ascending=True)
                            colors = ["#2ecc71" if v > 0 else "#e74c3c" for v in theme_pnl["Total PnL%"]]
                            fig_theme = go.Figure()
                            fig_theme.add_trace(go.Bar(
                                y=theme_pnl["Theme"],
                                x=theme_pnl["Total PnL%"],
                                orientation="h",
                                marker_color=colors,
                                text=[f"{v:.1f}%" for v in theme_pnl["Total PnL%"]],
                                textposition="outside",
                            ))
                            fig_theme.update_layout(
                                title="Total PnL% by Theme",
                                height=max(300, len(theme_pnl) * 35),
                                margin=dict(l=200),
                            )
                            st.plotly_chart(fig_theme, use_container_width=True)

                    # ── Daily PnL ──
                    if PLOTLY_OK:
                        daily_pnl = df_trades.groupby("Date")["PnL%"].sum().reset_index()
                        daily_pnl.columns = ["Date", "Daily PnL%"]
                        colors_daily = ["#2ecc71" if v > 0 else "#e74c3c" for v in daily_pnl["Daily PnL%"]]
                        fig_daily = go.Figure()
                        fig_daily.add_trace(go.Bar(
                            x=daily_pnl["Date"],
                            y=daily_pnl["Daily PnL%"],
                            marker_color=colors_daily,
                        ))
                        fig_daily.update_layout(title="Daily PnL%", height=350)
                        st.plotly_chart(fig_daily, use_container_width=True)

                    # ── Trade Log ──
                    st.subheader("📋 Trade Log")
                    st.dataframe(
                        df_trades.sort_values("Date", ascending=False),
                        use_container_width=True,
                        hide_index=True,
                        column_config={
                            "PnL%": st.column_config.NumberColumn(format="%.2f%%"),
                            "Prev Day Ret%": st.column_config.NumberColumn(format="%.2f%%"),
                            "Buy Price": st.column_config.NumberColumn(format="$%.2f"),
                            "Sell Price": st.column_config.NumberColumn(format="$%.2f"),
                        },
                    )

                    # ── Top/Bottom Tickers ──
                    col_top, col_bot = st.columns(2)
                    with col_top:
                        st.write("**🏆 Best Performers**")
                        best = df_trades.groupby("Ticker")["PnL%"].agg(["mean", "sum", "count"]).sort_values("sum", ascending=False).head(10).reset_index()
                        best.columns = ["Ticker", "Avg PnL%", "Total PnL%", "Trades"]
                        st.dataframe(best, use_container_width=True, hide_index=True)
                    with col_bot:
                        st.write("**💀 Worst Performers**")
                        worst = df_trades.groupby("Ticker")["PnL%"].agg(["mean", "sum", "count"]).sort_values("sum", ascending=True).head(10).reset_index()
                        worst.columns = ["Ticker", "Avg PnL%", "Total PnL%", "Trades"]
                        st.dataframe(worst, use_container_width=True, hide_index=True)


if __name__ == "__main__":
    main()
