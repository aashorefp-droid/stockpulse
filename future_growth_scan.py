"""
future_growth_scan.py — Future Growth Scanner
Scans high-potential sectors (AI/ML, Quantum, Space, Biotech, Defense) using
yfinance for price + fundamentals, with optional news sentiment scoring.
Run standalone:  python future_growth_scan.py
Or via Streamlit: streamlit run future_growth_scan.py
"""

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
import yfinance as yf
import pandas as pd
from datetime import date, timedelta
import math

# ── Growth Sectors & Candidate Tickers ──────────────────────────────────────
GROWTH_SECTORS = {
    "🤖 AI / Machine Learning": [
        "NVDA", "AMD", "MSFT", "GOOGL", "META", "ORCL", "PLTR", "AI", "SOUN",
        "BBAI", "GFAI", "AAPL", "TSM", "AVGO", "SMCI",
    ],
    "🔬 Quantum Computing": [
        "IBM", "IONQ", "QUBT", "RGTI", "QTUM", "ARQQ",
    ],
    "🚀 Space Technology": [
        "RKLB", "ASTS", "SPCE", "ASTR", "MNTS", "LMT", "NOC", "BA", "RTX",
    ],
    "🧬 Biotech / Gene Editing": [
        "CRSP", "EDIT", "NTLA", "BEAM", "VERV", "MRNA", "BNTX", "REGN",
        "ILMN", "PACB", "RXRX",
    ],
    "🛡️ Defense / Gov Contracts": [
        "LMT", "RTX", "NOC", "GD", "HII", "CACI", "LDOS", "BAH", "SAIC",
        "KTOS", "ANGI",
    ],
    "⚡ Clean Energy / Grid": [
        "ENPH", "FSLR", "SEDG", "NEE", "PLUG", "BE", "CHPT", "BLDP",
        "ARRY", "RUN",
    ],
    "💊 Longevity / Health Tech": [
        "ISRG", "DXCM", "TDOC", "VEEV", "DOCS", "MDT", "ABT", "IDXX", "PHG",
    ],
}

# ── Scoring weights ────────────────────────────────────────────────────────
SCORE_WEIGHTS = {
    "revenue_growth":   0.25,   # YoY revenue growth > 0
    "eps_trend":        0.15,   # EPS improving
    "analyst_upside":   0.20,   # Analyst target > current price
    "momentum_4w":      0.15,   # 4-week price momentum
    "weekly_zone":      0.15,   # Weekly zone (HIGH bonus, LOW opportunity)
    "vol_surge":        0.10,   # Recent volume vs 20-day avg
}

# ── Weekly Zone Helper ─────────────────────────────────────────────────────
def _compute_weekly_zone(df_wk: pd.DataFrame, current_price: float) -> tuple[str, float]:
    """Return (zone, pos_pct) based on 10-week H/L."""
    if df_wk is None or df_wk.empty:
        return "N/A", 50.0
    try:
        wk_hi = float(df_wk["High"].iloc[-10:].max())
        wk_lo = float(df_wk["Low"].iloc[-10:].min())
        rng = wk_hi - wk_lo
        if rng <= 0:
            return "MID", 50.0
        pos = (current_price - wk_lo) / rng * 100
        zone = "HIGH" if pos >= 70 else ("LOW" if pos <= 30 else "MID")
        return zone, round(pos, 1)
    except Exception:
        return "N/A", 50.0


# ── Single ticker scan ──────────────────────────────────────────────────────
def scan_ticker(symbol: str, as_of: date) -> dict | None:
    """Fetch fundamentals + technicals for `symbol` as of `as_of`. Returns scored row dict."""
    try:
        tk = yf.Ticker(symbol)
        info = tk.fast_info if hasattr(tk, "fast_info") else {}

        # -- Price -------------------------------------------------------
        df_daily = tk.history(start=str(as_of - timedelta(days=90)), end=str(as_of + timedelta(days=1)))
        if df_daily is None or df_daily.empty:
            return None
        current_price = float(df_daily["Close"].iloc[-1])
        price_4w_ago  = float(df_daily["Close"].iloc[-20]) if len(df_daily) >= 20 else current_price
        mom_4w = (current_price - price_4w_ago) / price_4w_ago * 100 if price_4w_ago > 0 else 0.0

        # -- Volume surge ------------------------------------------------
        vol_last  = float(df_daily["Volume"].iloc[-1]) if "Volume" in df_daily.columns else 0.0
        vol_avg20 = float(df_daily["Volume"].iloc[-20:].mean()) if len(df_daily) >= 5 else vol_last
        vol_ratio = vol_last / vol_avg20 if vol_avg20 > 0 else 1.0

        # -- Weekly zone -------------------------------------------------
        df_wk = tk.history(start=str(as_of - timedelta(days=200)), end=str(as_of + timedelta(days=1)), interval="1wk")
        wk_zone, wk_pos = _compute_weekly_zone(df_wk, current_price)

        # -- Fundamentals (robust fallbacks) -----------------------------
        rev_growth   = None
        eps_trend    = None
        analyst_tgt  = None
        market_cap   = None
        sector_name  = None
        try:
            full_info    = tk.info or {}
            rev_growth   = full_info.get("revenueGrowth")         # float or None
            eps_trend    = full_info.get("earningsQuarterlyGrowth")
            analyst_tgt  = full_info.get("targetMeanPrice")
            market_cap   = full_info.get("marketCap")
            sector_name  = full_info.get("sector", "")
        except Exception:
            pass

        analyst_upside = ((analyst_tgt - current_price) / current_price * 100
                          if analyst_tgt and current_price > 0 else None)

        # -- Score components -------------------------------------------
        score = 0.0

        # Revenue growth
        if rev_growth is not None:
            rev_s = min(max(rev_growth * 100, -50), 100)   # clamp to [-50, 100]
            score += SCORE_WEIGHTS["revenue_growth"] * (50 + rev_s / 2)

        # EPS trend
        if eps_trend is not None:
            eps_s = min(max(eps_trend * 100, -50), 100)
            score += SCORE_WEIGHTS["eps_trend"] * (50 + eps_s / 2)

        # Analyst upside
        if analyst_upside is not None:
            up_s = min(max(analyst_upside, -50), 100)
            score += SCORE_WEIGHTS["analyst_upside"] * (50 + up_s / 2)

        # 4-week momentum
        mom_s = min(max(mom_4w, -40), 60)
        score += SCORE_WEIGHTS["momentum_4w"] * (50 + mom_s)

        # Weekly zone (LOW = opportunity=70, MID=50, HIGH=40 for mean-reversion risk)
        zone_pts = {"HIGH": 40, "MID": 50, "LOW": 70, "N/A": 50}
        score += SCORE_WEIGHTS["weekly_zone"] * zone_pts.get(wk_zone, 50)

        # Volume surge (ratio >= 1.5 = 100pts, <= 0.5 = 0pts)
        vol_s = min(max((vol_ratio - 0.5) / 1.0 * 100, 0), 100)
        score += SCORE_WEIGHTS["vol_surge"] * vol_s

        # Normalise to 0–100
        total_weight = sum(
            SCORE_WEIGHTS[k] for k in SCORE_WEIGHTS
            if {
                "revenue_growth": rev_growth, "eps_trend": eps_trend,
                "analyst_upside": analyst_upside,
            }.get(k, True) is not None or k not in ("revenue_growth", "eps_trend", "analyst_upside")
        )
        final_score = round(score / total_weight, 1) if total_weight > 0 else 0.0

        return {
            "Ticker":          symbol,
            "Price":           f"${current_price:.2f}",
            "Market Cap":      f"${market_cap/1e9:.1f}B" if market_cap else "N/A",
            "Sector":          sector_name or "",
            "Rev Growth%":     f"{rev_growth*100:+.1f}%" if rev_growth is not None else "N/A",
            "EPS QoQ%":        f"{eps_trend*100:+.1f}%" if eps_trend is not None else "N/A",
            "Analyst Target":  f"${analyst_tgt:.2f}" if analyst_tgt else "N/A",
            "Upside%":         f"{analyst_upside:+.1f}%" if analyst_upside is not None else "N/A",
            "4W Mom%":         f"{mom_4w:+.1f}%",
            "Vol Ratio":       f"{vol_ratio:.2f}x",
            "Weekly Zone":     wk_zone,
            "Wk Pos%":         f"{wk_pos:.1f}%",
            "Score":           final_score,
            "_score_raw":      final_score,
        }
    except Exception as exc:
        return {"Ticker": symbol, "Score": 0, "_score_raw": 0, "_error": str(exc)}


# ── Coloring helpers ───────────────────────────────────────────────────────
def _color_zone(v):
    v = str(v)
    if v == "HIGH": return "background-color:#3d0a1a;color:#ff4d6a;font-weight:700"
    if v == "LOW":  return "background-color:#0a3d1f;color:#00e5a0;font-weight:700"
    if v == "MID":  return "background-color:#3d3a0a;color:#f5c842;font-weight:700"
    return ""

def _color_score(v):
    try:
        s = float(v)
        if s >= 70: return "background-color:#0a3d1f;color:#00e5a0;font-weight:700"
        if s >= 50: return "background-color:#1f1400;color:#f5c842;font-weight:700"
        return "background-color:#1a0808;color:#ff8c8c"
    except Exception:
        return ""

def _color_upside(v):
    v = str(v).replace("%", "").replace("+", "").strip()
    try:
        x = float(v)
        if x >= 20:  return "color:#00e5a0;font-weight:700"
        if x >= 5:   return "color:#7ccfb0"
        if x < 0:    return "color:#ff4d6a"
    except Exception:
        pass
    return ""


# ── Streamlit UI ──────────────────────────────────────────────────────────
def main():
    st.set_page_config(
        page_title="Future Growth Scanner",
        page_icon="🚀",
        layout="wide",
    )
    st.markdown(
        '<h1 style="color:#ccd6f6;font-size:28px">🚀 Future Growth Scanner</h1>'
        '<p style="color:#6b7099;font-size:12px;margin-top:-8px">'
        'Scans high-potential growth sectors: AI, Quantum, Space, Biotech, Defense, Clean Energy</p>',
        unsafe_allow_html=True,
    )

    # ── Sidebar controls ──────────────────────────────────────────────────
    st.sidebar.header("⚙️ Scan Settings")
    as_of = st.sidebar.date_input("As Of Date", value=date.today())
    selected_sectors = st.sidebar.multiselect(
        "Sectors to scan",
        options=list(GROWTH_SECTORS.keys()),
        default=list(GROWTH_SECTORS.keys()),
    )
    min_score = st.sidebar.slider("Minimum Score", 0, 100, 40)
    sort_col = st.sidebar.selectbox("Sort by", ["Score", "Upside%", "4W Mom%", "Vol Ratio"])
    top_n = st.sidebar.number_input("Top N per sector (0 = all)", min_value=0, max_value=50, value=10)

    run_scan = st.sidebar.button("🔍 RUN SCAN", type="primary", use_container_width=True)
    st.sidebar.caption(
        "Data: yfinance (free). Fundamentals may be delayed or unavailable for small-caps."
    )

    if not run_scan:
        st.info("👈 Configure settings in the sidebar and click **🔍 RUN SCAN** to start.")
        # Show sector coverage preview
        for sector, tickers in GROWTH_SECTORS.items():
            if sector in selected_sectors:
                st.markdown(f"**{sector}** — {', '.join(tickers)}")
        return

    # ── Run scan ──────────────────────────────────────────────────────────
    all_results = []
    for sector in selected_sectors:
        tickers = GROWTH_SECTORS.get(sector, [])
        if not tickers:
            continue
        st.markdown(f"### {sector}")
        rows = []
        progress = st.progress(0, text=f"Scanning {sector}...")
        for i, sym in enumerate(tickers):
            progress.progress((i + 1) / len(tickers), text=f"Scanning {sym}...")
            row = scan_ticker(sym, as_of)
            if row and "_error" not in row:
                rows.append(row)
            elif row and "_error" in row:
                st.caption(f"⚠️ {sym}: {row['_error']}")
        progress.empty()

        if not rows:
            st.warning("No data returned for this sector.")
            continue

        df = pd.DataFrame(rows)
        df = df[df["_score_raw"] >= min_score].copy()
        df = df.sort_values("_score_raw", ascending=False)
        if top_n > 0:
            df = df.head(top_n)
        df = df.drop(columns=["_score_raw"], errors="ignore")

        if df.empty:
            st.info(f"No tickers met the minimum score of {min_score}.")
            continue

        display_cols = [c for c in df.columns if not c.startswith("_")]
        styled = df[display_cols].style
        if "Weekly Zone" in display_cols:
            styled = styled.applymap(_color_zone, subset=["Weekly Zone"])
        if "Score" in display_cols:
            styled = styled.applymap(_color_score, subset=["Score"])
        if "Upside%" in display_cols:
            styled = styled.applymap(_color_upside, subset=["Upside%"])

        st.dataframe(styled, use_container_width=True, hide_index=True,
                     height=min(len(df) * 35 + 48, 500))
        all_results.extend(rows)

    # ── Combined summary ──────────────────────────────────────────────────
    if all_results:
        st.markdown("---")
        st.markdown("### 🏆 Top Growth Picks Across All Sectors")
        combined_df = pd.DataFrame(all_results)
        combined_df = combined_df[combined_df["_score_raw"] >= min_score].drop_duplicates("Ticker")
        combined_df = combined_df.sort_values("_score_raw", ascending=False).head(20)
        combined_df = combined_df.drop(columns=["_score_raw"], errors="ignore")
        display_cols = [c for c in combined_df.columns if not c.startswith("_")]
        styled_all = combined_df[display_cols].style
        if "Weekly Zone" in display_cols:
            styled_all = styled_all.applymap(_color_zone, subset=["Weekly Zone"])
        if "Score" in display_cols:
            styled_all = styled_all.applymap(_color_score, subset=["Score"])
        if "Upside%" in display_cols:
            styled_all = styled_all.applymap(_color_upside, subset=["Upside%"])
        st.dataframe(styled_all, use_container_width=True, hide_index=True)

        # CSV download
        csv = combined_df.to_csv(index=False)
        st.download_button(
            "📥 Download Full Results CSV",
            data=csv,
            file_name=f"growth_scan_{as_of.strftime('%Y%m%d')}.csv",
            mime="text/csv",
            use_container_width=True,
        )


if __name__ == "__main__":
    main()
