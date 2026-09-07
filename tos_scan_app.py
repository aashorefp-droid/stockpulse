"""
tos_scan_app.py — Standalone ThinkOrSwim-style multi-condition stock scanner.

Run:
    streamlit run tos_scan_app.py

Requires tos_scanner.py in the same directory.
"""

import streamlit as st
import pandas as pd
from datetime import date
import yfinance as yf

from tos_scanner import (
    scan_ticker,
    apply_filters,
    WATCHLISTS,
)

# ══════════════════════════════════════════════════════════════════════════════
# PAGE CONFIG
# ══════════════════════════════════════════════════════════════════════════════
st.set_page_config(
    page_title="TOS Scanner",
    page_icon="📡",
    layout="wide",
    initial_sidebar_state="collapsed",
)

# ── Dark theme CSS ────────────────────────────────────────────────────────────
st.markdown("""
<style>
  html, body, [class*="css"] {
    background-color: #080a12 !important;
    color: #c8cfe8 !important;
    font-family: 'Inter', 'Segoe UI', sans-serif;
  }
  .stApp { background-color: #080a12; }
  .stDataFrame, .stDataFrameContainer { background: #0d0f17 !important; }
  div[data-testid="stExpander"] { background: #0d0f17; border: 1px solid #1a1d2e; border-radius: 6px; }
  div[data-testid="stTabs"] button { color: #6b7099 !important; }
  div[data-testid="stTabs"] button[aria-selected="true"] { color: #e8ecff !important; border-bottom: 2px solid #4d9fff !important; }
  .stButton > button {
    background: linear-gradient(135deg,#1a2040,#252b4a);
    color: #e8ecff;
    border: 1px solid #2a3060;
    border-radius: 6px;
    font-weight: 600;
  }
  .stButton > button:hover { background: #2a3060; border-color: #4d9fff; }
  .stSelectbox label, .stTextInput label, .stSlider label,
  .stNumberInput label, .stCheckbox label { color: #8892b0 !important; font-size: 12px !important; }
  div[data-testid="stSelectbox"] > div,
  div[data-testid="stTextInput"] > div > input {
    background: #0d0f17 !important;
    border-color: #1a1d2e !important;
    color: #c8cfe8 !important;
  }
  .stProgress > div > div { background: #4d9fff; }
</style>
""", unsafe_allow_html=True)

# ══════════════════════════════════════════════════════════════════════════════
# HEADER
# ══════════════════════════════════════════════════════════════════════════════
st.markdown("""
<div style="background:linear-gradient(135deg,#0a0b14,#131625);
            border:1px solid #1a1d2e;border-radius:10px;
            padding:20px 28px;margin-bottom:20px">
  <div style="display:flex;justify-content:space-between;align-items:center">
    <div>
      <h1 style="margin:0;color:#e8ecff;font-size:26px;letter-spacing:-0.5px">
        📡 TOS Scanner
      </h1>
      <p style="margin:4px 0 0;color:#6b7099;font-size:12px">
        Fibonacci · AGIG ZigZag · Weinstein · RSI · MACD · Bollinger Bands · MA · Volume · FVG
        &nbsp;·&nbsp; Weekly + Daily timeframes
      </p>
    </div>
    <div style="text-align:right;color:#6b7099;font-size:11px">
      Data via Yahoo Finance<br>
      <span style="color:#4d9fff">""" + str(date.today()) + """</span>
    </div>
  </div>
</div>
""", unsafe_allow_html=True)

# ══════════════════════════════════════════════════════════════════════════════
# INPUT PANEL
# ══════════════════════════════════════════════════════════════════════════════
inp_c1, inp_c2, inp_c3 = st.columns([2, 3, 1])

with inp_c1:
    wl_options = ["Custom"] + list(WATCHLISTS.keys())
    wl_choice = st.selectbox("Watchlist Preset", wl_options, key="wl_preset")

with inp_c2:
    default_tickers = (
        ", ".join(WATCHLISTS[wl_choice])
        if wl_choice != "Custom"
        else "TSLA, AAPL, MSFT, NVDA, AMD, META, GOOGL"
    )
    tickers_raw = st.text_input(
        "Tickers — comma-separated (edit freely after choosing a preset)",
        value=default_tickers,
        key="tickers_input",
    ).upper().strip()
    tickers = [t.strip() for t in tickers_raw.split(",") if t.strip()]

with inp_c3:
    st.markdown("<br>", unsafe_allow_html=True)
    scan_btn = st.button("🔍 Run Scan", use_container_width=True, key="scan_btn")

# ══════════════════════════════════════════════════════════════════════════════
# SCAN CONDITIONS  (TOS Study Filter equivalent)
# ══════════════════════════════════════════════════════════════════════════════
with st.expander("🎛️ Scan Conditions — Filter Results", expanded=False):
    fc1, fc2, fc3, fc4 = st.columns(4)

    with fc1:
        st.markdown("**Bias & Grade**")
        f_bias     = st.selectbox("Direction", ["ANY", "BULLISH", "BEARISH"], key="f_bias")
        f_aligned  = st.checkbox("Require W+D Aligned", key="f_aligned")
        f_grade    = st.selectbox("Min Grade", ["Any", "A+", "A", "B", "C"], key="f_grade")
        f_min_score = st.slider("Min Signal Score", 0, 12, 0, key="f_min_score")

    with fc2:
        st.markdown("**RSI & MACD**")
        f_rsi_min  = st.number_input("RSI Min", 0, 100, 0,   key="f_rsi_min")
        f_rsi_max  = st.number_input("RSI Max", 0, 100, 100, key="f_rsi_max")
        f_rsi_os   = st.checkbox("Oversold (<30)",   key="f_rsi_os")
        f_rsi_ob   = st.checkbox("Overbought (>70)", key="f_rsi_ob")
        f_macd_cross = st.selectbox("MACD Cross",      ["ANY", "UP", "DOWN"],          key="f_macd_cross")
        f_macd_dir   = st.selectbox("MACD Direction",  ["ANY", "BULLISH", "BEARISH"],  key="f_macd_dir")

    with fc3:
        st.markdown("**Moving Averages**")
        f_ma20   = st.selectbox("Price vs MA20",  ["ANY", "ABOVE", "BELOW"], key="f_ma20")
        f_ma50   = st.selectbox("Price vs MA50",  ["ANY", "ABOVE", "BELOW"], key="f_ma50")
        f_ma200  = st.selectbox("Price vs MA200", ["ANY", "ABOVE", "BELOW"], key="f_ma200")
        f_golden = st.checkbox("Golden Cross (MA50 > MA200)", key="f_golden")
        f_death  = st.checkbox("Death Cross  (MA50 < MA200)", key="f_death")
        st.markdown("**Bollinger Bands**")
        f_bb_pos = st.selectbox("BB Position", ["ANY", "ABOVE_UPPER", "BELOW_LOWER", "INSIDE"], key="f_bb_pos")
        f_bb_sq  = st.checkbox("BB Squeeze active", key="f_bb_sq")

    with fc4:
        st.markdown("**Weinstein & Volume**")
        f_wein_phase = st.selectbox("Weinstein Phase",
                                    ["Any", "STAGE 1", "STAGE 2", "STAGE 3", "STAGE 4"],
                                    key="f_wein_phase")
        f_wein_min   = st.slider("Min Weinstein Score", 0, 6, 0, key="f_wein_min")
        f_vol_trend  = st.selectbox("Volume Trend",
                                    ["ANY", "ACCUMULATING", "DISTRIBUTING", "FLAT"],
                                    key="f_vol_trend")
        st.markdown("**Fibonacci & Patterns**")
        f_fib_zone  = st.selectbox("Fib Zone",
                                   ["ANY", "BUY ZONE", "SELL ZONE", "DEEP DISCOUNT", "EXTENDED", "NEUTRAL"],
                                   key="f_fib_zone")
        f_bull_div  = st.checkbox("Bullish Divergence", key="f_bull_div")
        f_bear_div  = st.checkbox("Bearish Divergence", key="f_bear_div")

# ══════════════════════════════════════════════════════════════════════════════
# RUN SCAN
# ══════════════════════════════════════════════════════════════════════════════
if scan_btn and tickers:
    bar = st.progress(0, text="Fetching SPY reference data…")
    try:
        _spy = yf.Ticker("SPY")
        spy_w = _spy.history(period="5y", interval="1wk", auto_adjust=True)
        spy_d = _spy.history(period="2y", interval="1d",  auto_adjust=True)
    except Exception:
        spy_w = spy_d = pd.DataFrame()

    raw = []
    errors = []
    for i, tkr in enumerate(tickers):
        bar.progress((i + 1) / len(tickers), text=f"Scanning {tkr}  ({i+1}/{len(tickers)})…")
        try:
            r = scan_ticker(tkr, spy_w, spy_d)
            if r:
                raw.append(r)
        except Exception as e:
            errors.append(f"{tkr}: {e}")

    bar.empty()
    if errors:
        for err in errors:
            st.warning(f"⚠️ {err}")

    if raw:
        st.session_state["tos_raw"] = raw
    else:
        st.info("No data returned — check tickers and try again.")

# ══════════════════════════════════════════════════════════════════════════════
# BUILD FILTER DICT & APPLY
# ══════════════════════════════════════════════════════════════════════════════
raw_results = st.session_state.get("tos_raw", [])

if raw_results:
    active_filters = {
        "bias_direction":       st.session_state.get("f_bias", "ANY"),
        "require_aligned":      st.session_state.get("f_aligned", False),
        "min_grade":            st.session_state.get("f_grade") if st.session_state.get("f_grade") != "Any" else None,
        "min_score":            st.session_state.get("f_min_score") or None,
        "rsi_min":              st.session_state.get("f_rsi_min") or None,
        "rsi_max":              st.session_state.get("f_rsi_max", 100) if st.session_state.get("f_rsi_max", 100) < 100 else None,
        "require_rsi_oversold":   st.session_state.get("f_rsi_os", False),
        "require_rsi_overbought": st.session_state.get("f_rsi_ob", False),
        "macd_cross":    st.session_state.get("f_macd_cross") if st.session_state.get("f_macd_cross") != "ANY" else None,
        "macd_direction": st.session_state.get("f_macd_dir", "ANY"),
        "price_vs_ma20":  st.session_state.get("f_ma20",  "ANY"),
        "price_vs_ma50":  st.session_state.get("f_ma50",  "ANY"),
        "price_vs_ma200": st.session_state.get("f_ma200", "ANY"),
        "require_golden_cross": st.session_state.get("f_golden", False),
        "require_death_cross":  st.session_state.get("f_death",  False),
        "bb_position":      st.session_state.get("f_bb_pos", "ANY"),
        "require_bb_squeeze": st.session_state.get("f_bb_sq", False),
        "weinstein_phase":  st.session_state.get("f_wein_phase") if st.session_state.get("f_wein_phase") != "Any" else "",
        "min_weinstein":    st.session_state.get("f_wein_min") or None,
        "vol_trend":        st.session_state.get("f_vol_trend", "ANY"),
        "fib_zone":         st.session_state.get("f_fib_zone", "ANY"),
        "require_bull_div": st.session_state.get("f_bull_div", False),
        "require_bear_div": st.session_state.get("f_bear_div", False),
    }
    filtered = apply_filters(raw_results, active_filters)

    # ── Summary banner ──────────────────────────────────────────────────────
    n_scan   = len(raw_results)
    n_filt   = len(filtered)
    n_bull   = sum(1 for r in filtered if r.get("combined_bias") == "BULLISH ALIGNED")
    n_bear   = sum(1 for r in filtered if r.get("combined_bias") == "BEARISH ALIGNED")
    n_mixed  = n_filt - n_bull - n_bear
    n_aplus  = sum(1 for r in filtered if r.get("signal_grade") == "A+")
    n_a      = sum(1 for r in filtered if r.get("signal_grade") == "A")
    n_bb_sq  = sum(1 for r in filtered if r.get("bb_squeeze"))
    n_gc     = sum(1 for r in filtered if r.get("golden_cross"))
    n_macd_x = sum(1 for r in filtered if r.get("d_macd_cross_up") or r.get("d_macd_cross_down"))

    st.markdown(
        f'<div style="background:#0d0f17;border:1px solid #1a1d2e;padding:12px 18px;'
        f'border-radius:6px;margin-bottom:14px;display:flex;flex-wrap:wrap;gap:20px;'
        f'align-items:center">'
        f'<span style="color:#6b7099;font-size:11px">'
        f'Scanned <b style="color:#e8ecff">{n_scan}</b> · '
        f'Showing <b style="color:#e8ecff">{n_filt}</b></span>'
        f'<span style="color:#00e5a0;font-size:11px">🟢 Bull Aligned <b>{n_bull}</b></span>'
        f'<span style="color:#ff4d6a;font-size:11px">🔴 Bear Aligned <b>{n_bear}</b></span>'
        f'<span style="color:#a78bfa;font-size:11px">Mixed <b>{n_mixed}</b></span>'
        f'<span style="color:#00e5a0;font-size:11px">A+ <b>{n_aplus}</b></span>'
        f'<span style="color:#4d9fff;font-size:11px">A <b>{n_a}</b></span>'
        f'<span style="color:#ffe066;font-size:11px">⚡ BB Squeeze <b>{n_bb_sq}</b></span>'
        f'<span style="color:#4d9fff;font-size:11px">Golden✓ <b>{n_gc}</b></span>'
        f'<span style="color:#a78bfa;font-size:11px">MACD Cross <b>{n_macd_x}</b></span>'
        f'</div>',
        unsafe_allow_html=True,
    )

    # ── Column definition ───────────────────────────────────────────────────
    DISPLAY_COLS = [
        "ticker", "price",
        "combined_bias", "signal_grade", "signal_score",
        "w_bias", "d_bias",
        "d_rsi", "d_rsi_label", "w_rsi",
        "d_macd_label", "d_macd_cross_up", "d_macd_cross_down",
        "w_macd_label", "w_macd_cross_up",
        "price_vs_ma20", "price_vs_ma50", "price_vs_ma200",
        "golden_cross", "death_cross",
        "bb_bandwidth", "bb_pct_b", "bb_squeeze", "bb_position",
        "w_ema_trend", "d_ema_trend",
        "w_fib_zone", "d_fib_zone", "d_fib_position",
        "w_conviction", "d_conviction", "d_vol_trend", "d_vol_ratio",
        "w_weinstein_score", "d_weinstein_score",
        "w_weinstein_phase", "d_weinstein_phase",
        "d_momentum", "d_roc", "d_volatility_label",
        "d_bull_div", "d_bear_div",
        "d_bull_fvg_count", "d_bear_fvg_count",
    ]
    COL_RENAME = {
        "combined_bias": "Bias W+D",   "signal_grade": "Grade",   "signal_score": "Score",
        "w_bias": "W Bias",            "d_bias": "D Bias",
        "d_rsi": "D RSI",              "d_rsi_label": "RSI State", "w_rsi": "W RSI",
        "d_macd_label": "D MACD",      "d_macd_cross_up": "MACD↑", "d_macd_cross_down": "MACD↓",
        "w_macd_label": "W MACD",      "w_macd_cross_up": "W MACD↑",
        "price_vs_ma20": "vs MA20",    "price_vs_ma50": "vs MA50", "price_vs_ma200": "vs MA200",
        "golden_cross": "Golden✓",     "death_cross": "Death✗",
        "bb_bandwidth": "BB Width%",   "bb_pct_b": "BB %B",
        "bb_squeeze": "BB Sq",         "bb_position": "BB Pos",
        "w_ema_trend": "W EMA",        "d_ema_trend": "D EMA",
        "w_fib_zone": "W Fib",         "d_fib_zone": "D Fib",    "d_fib_position": "Fib%",
        "w_conviction": "W Conv",      "d_conviction": "D Conv",
        "d_vol_trend": "Vol Trend",    "d_vol_ratio": "Vol×",
        "w_weinstein_score": "W Wein", "d_weinstein_score": "D Wein",
        "w_weinstein_phase": "W Phase","d_weinstein_phase": "D Phase",
        "d_momentum": "Momentum",      "d_roc": "ROC%",
        "d_volatility_label": "Volat",
        "d_bull_div": "Bull Div",      "d_bear_div": "Bear Div",
        "d_bull_fvg_count": "Bull FVG","d_bear_fvg_count": "Bear FVG",
    }

    # ── Styling ──────────────────────────────────────────────────────────────
    def _style(df: pd.DataFrame):
        BULL_WORDS = {"BULLISH", "UPTREND", "BUYERS", "ABOVE", "ACCUMUL", "CROSS UP",
                      "OVERSOLD", "STRONG UP", "POSITIVE", "BUY ZONE", "DEEP DISCOUNT"}
        BEAR_WORDS = {"BEARISH", "DOWNTREND", "SELLERS", "BELOW", "DISTRIBUT", "CROSS DOWN",
                      "OVERBOUGHT", "STRONG DOWN", "WEAKENING", "SELL ZONE", "EXTENDED"}

        def _bias(val):
            if not isinstance(val, str): return ""
            v = val.upper()
            if any(w in v for w in BULL_WORDS): return "background:#0a2e18;color:#00e5a0;font-weight:700"
            if any(w in v for w in BEAR_WORDS): return "background:#2e0a18;color:#ff4d6a;font-weight:700"
            if "MIXED" in v: return "color:#f0c040"
            return "color:#6b7099"

        def _grade(val):
            return {"A+": "background:#0a2e18;color:#00e5a0;font-weight:700",
                    "A":  "color:#00e5a0;font-weight:600",
                    "B":  "color:#4d9fff",
                    "C":  "color:#f0c040"}.get(val, "color:#6b7099")

        def _rsi(val):
            try:
                v = float(val)
                if v <= 30: return "background:#0a2e18;color:#00e5a0;font-weight:700"
                if v >= 70: return "background:#2e0a18;color:#ff4d6a;font-weight:700"
                if v >= 60: return "color:#4d9fff"
                if v <= 40: return "color:#a78bfa"
            except Exception: pass
            return "color:#6b7099"

        def _wein(val):
            try:
                v = int(val)
                if v >= 5: return "color:#00e5a0;font-weight:700"
                if v >= 3: return "color:#4d9fff"
            except Exception: pass
            return "color:#6b7099"

        def _bool_col(val):
            if val is True  or str(val) == "True":  return "color:#00e5a0;font-weight:700"
            if val is False or str(val) == "False": return "color:#6b7099"
            return ""

        bias_cols = [c for c in [
            "Bias W+D","W Bias","D Bias","W EMA","D EMA","W Conv","D Conv",
            "Vol Trend","D MACD","W MACD","RSI State","BB Pos","W Fib","D Fib",
            "vs MA20","vs MA50","vs MA200","Momentum",
        ] if c in df.columns]
        bool_cols = [c for c in ["MACD↑","MACD↓","W MACD↑","Golden✓","Death✗","BB Sq","Bull Div","Bear Div"] if c in df.columns]
        rsi_cols  = [c for c in ["D RSI","W RSI"] if c in df.columns]
        wein_cols = [c for c in ["W Wein","D Wein"] if c in df.columns]

        s = df.style
        if bias_cols:  s = s.applymap(_bias,  subset=bias_cols)
        if "Grade" in df.columns: s = s.applymap(_grade, subset=["Grade"])
        if rsi_cols:   s = s.applymap(_rsi,   subset=rsi_cols)
        if wein_cols:  s = s.applymap(_wein,  subset=wein_cols)
        if bool_cols:  s = s.applymap(_bool_col, subset=bool_cols)
        return s

    def _prep(df_sub: pd.DataFrame) -> pd.DataFrame:
        cols = [c for c in DISPLAY_COLS if c in df_sub.columns]
        out  = df_sub[cols].rename(columns=COL_RENAME)
        if "Score" in out.columns:
            out = out.sort_values("Score", ascending=False)
        return out

    def _render(df_sub, label, color, empty_msg, container, dl_key=None):
        with container:
            if df_sub.empty:
                st.info(empty_msg)
                return
            st.markdown(
                f'<div style="border-left:4px solid {color};padding:4px 14px;margin-bottom:8px">'
                f'<span style="color:{color};font-weight:700;font-size:14px">'
                f'{label} &nbsp;({len(df_sub)})</span></div>',
                unsafe_allow_html=True,
            )
            show = _prep(df_sub)
            st.dataframe(_style(show), use_container_width=True,
                         height=min(40 * len(show) + 40, 650))
            if dl_key:
                st.download_button(
                    "📥 Download CSV", show.to_csv(index=False),
                    file_name=f"tos_scan_{date.today()}.csv",
                    mime="text/csv", use_container_width=True, key=dl_key,
                )

    # ── Build sub-DataFrames ─────────────────────────────────────────────────
    df_all  = pd.DataFrame(filtered) if filtered else pd.DataFrame()

    if not df_all.empty:
        df_bull   = df_all[df_all["combined_bias"] == "BULLISH ALIGNED"]
        df_bear   = df_all[df_all["combined_bias"] == "BEARISH ALIGNED"]
        df_align  = df_all[df_all["combined_bias"].isin(["BULLISH ALIGNED", "BEARISH ALIGNED"])]
        df_grade  = df_all[df_all["signal_grade"].isin(["A+", "A"])] if "signal_grade" in df_all else pd.DataFrame()
        df_bbs    = df_all[df_all["bb_squeeze"] == True] if "bb_squeeze" in df_all.columns else pd.DataFrame()
        df_macdx  = df_all[df_all["d_macd_cross_up"] | df_all["d_macd_cross_down"]] if "d_macd_cross_up" in df_all.columns else pd.DataFrame()
        df_gc     = df_all[df_all["golden_cross"] == True] if "golden_cross" in df_all.columns else pd.DataFrame()

        # ── Results tabs ──────────────────────────────────────────────────────
        t_align, t_bull, t_bear, t_grade, t_bbs, t_macdx, t_gc, t_all = st.tabs([
            f"🎯 Aligned ({len(df_align)})",
            f"🟢 Bull ({len(df_bull)})",
            f"🔴 Bear ({len(df_bear)})",
            f"⭐ A / A+ ({len(df_grade)})",
            f"⚡ BB Squeeze ({len(df_bbs)})",
            f"🔀 MACD Cross ({len(df_macdx)})",
            f"✅ Golden Cross ({len(df_gc)})",
            f"📊 All ({len(df_all)})",
        ])

        _render(df_align, "🎯 Aligned Setups (W+D)",      "#a78bfa", "No aligned setups.",     t_align)
        _render(df_bull,  "🟢 Bullish Aligned",           "#00e5a0", "No bullish setups.",     t_bull)
        _render(df_bear,  "🔴 Bearish Aligned",           "#ff4d6a", "No bearish setups.",     t_bear)
        _render(df_grade, "⭐ Grade A / A+",              "#ffe066", "No A/A+ grades.",        t_grade)
        _render(df_bbs,   "⚡ Bollinger Squeeze",         "#f0c040", "No BB squeezes.",        t_bbs)
        _render(df_macdx, "🔀 MACD Crossovers (today)",  "#a78bfa", "No MACD crosses today.", t_macdx)
        _render(df_gc,    "✅ Golden Cross",              "#4d9fff", "No golden crosses.",     t_gc)
        _render(df_all,   "📊 All Results",               "#6b7099", "No results.",            t_all, dl_key="dl_all")

    else:
        st.info("No results match the current filters. Adjust conditions above or run the scan first.")

    # ══════════════════════════════════════════════════════════════════════════
    # DETAILED TICKER VIEW
    # ══════════════════════════════════════════════════════════════════════════
    st.markdown("---")
    with st.expander("🔍 Detailed Ticker View", expanded=False):
        if not raw_results:
            st.info("Run a scan first.")
        else:
            sel_tkr = st.selectbox("Ticker", [r["ticker"] for r in raw_results], key="detail_sel")
            r = next((x for x in raw_results if x["ticker"] == sel_tkr), None)
            if r:
                grade_color = {"A+": "#00e5a0", "A": "#00e5a0", "B": "#4d9fff", "C": "#f0c040"}.get(
                    r.get("signal_grade", "D"), "#6b7099"
                )
                bias_color = "#00e5a0" if "BULLISH" in r.get("combined_bias","") else (
                    "#ff4d6a" if "BEARISH" in r.get("combined_bias","") else "#f0c040"
                )
                st.markdown(
                    f'<div style="background:#0d0f17;border:1px solid #1a1d2e;border-radius:8px;'
                    f'padding:14px 20px;margin-bottom:14px">'
                    f'<div style="display:flex;gap:28px;align-items:center;flex-wrap:wrap">'
                    f'<span style="font-size:22px;font-weight:800;color:#e8ecff">{r["ticker"]}</span>'
                    f'<span style="color:#6b7099;font-size:15px">${r["price"]:.2f}</span>'
                    f'<span style="color:{grade_color};font-size:16px;font-weight:700">'
                    f'Grade {r.get("signal_grade","?")} &nbsp;·&nbsp; Score {r.get("signal_score",0)}/12</span>'
                    f'<span style="color:{bias_color};font-size:13px;font-weight:600">{r.get("combined_bias","")}</span>'
                    f'</div></div>',
                    unsafe_allow_html=True,
                )

                dc1, dc2, dc3 = st.columns(3)

                # ── Weekly ──────────────────────────────────────────────────
                with dc1:
                    st.markdown("#### 📅 Weekly")
                    def _pill(label, val, good, bad):
                        c = "#00e5a0" if val in good else ("#ff4d6a" if val in bad else "#6b7099")
                        return f'<span style="color:{c};font-weight:600">{val}</span>'

                    items_w = [
                        ("Bias",       f'{r.get("w_bias","")} (str {r.get("w_bias_strength",0):.1f}%, Q {r.get("w_bias_quality",0)}/3)'),
                        ("RSI",        f'{r.get("w_rsi",0):.1f} — {r.get("w_rsi_label","")}'),
                        ("MACD",       f'{r.get("w_macd_label","")}' + (" ↑CROSS" if r.get("w_macd_cross_up") else "")),
                        ("EMA Trend",  r.get("w_ema_trend","")),
                        ("Fib Zone",   f'{r.get("w_fib_zone","")} ({r.get("w_fib_position",0):.1f}%)'),
                        ("Conviction", r.get("w_conviction","")),
                        ("Vol Trend",  r.get("w_vol_trend","")),
                        ("Weinstein",  f'{r.get("w_weinstein_score",0)}/6 — {r.get("w_weinstein_phase","")}'),
                        ("Momentum",   r.get("w_momentum","")),
                    ]
                    for lbl, val in items_w:
                        st.markdown(f'<div style="padding:3px 0;font-size:12px">'
                                    f'<span style="color:#6b7099;width:90px;display:inline-block">{lbl}</span>'
                                    f'<b style="color:#c8cfe8">{val}</b></div>',
                                    unsafe_allow_html=True)

                    if r.get("w_details"):
                        wd = r["w_details"]
                        checks = [
                            ("MA30 Curling",  wd.get("ma30_curling")),
                            ("MA10 > MA30",   wd.get("ma10_above_ma30")),
                            ("RS > 0",        wd.get("rs_positive")),
                            ("RS Improving",  wd.get("rs_improving")),
                            ("Vol Building",  wd.get("vol_building")),
                            ("Near 52W High", wd.get("near_52w_high")),
                        ]
                        badge_html = " ".join(
                            f'<span style="background:{"#0a2e18" if ok else "#1a1d2e"};'
                            f'color:{"#00e5a0" if ok else "#6b7099"};'
                            f'border-radius:4px;padding:2px 6px;font-size:10px;margin:2px">'
                            f'{"✅" if ok else "❌"} {lbl}</span>'
                            for lbl, ok in checks
                        )
                        st.markdown(f'<div style="margin-top:6px">{badge_html}</div>', unsafe_allow_html=True)

                # ── Daily ───────────────────────────────────────────────────
                with dc2:
                    st.markdown("#### 📈 Daily")
                    flags = []
                    if r.get("d_bull_div"):       flags.append("🟢 Bull Divergence")
                    if r.get("d_bear_div"):       flags.append("🔴 Bear Divergence")
                    if r.get("d_bull_fvg_count"): flags.append(f"🟢 {r['d_bull_fvg_count']}× Bull FVG")
                    if r.get("d_bear_fvg_count"): flags.append(f"🔴 {r['d_bear_fvg_count']}× Bear FVG")

                    items_d = [
                        ("Bias",        f'{r.get("d_bias","")} (str {r.get("d_bias_strength",0):.1f}%, Q {r.get("d_bias_quality",0)}/3)'),
                        ("RSI",         f'{r.get("d_rsi",0):.1f} — {r.get("d_rsi_label","")}'),
                        ("MACD",        f'{r.get("d_macd_label","")}' + (" ↑CROSS" if r.get("d_macd_cross_up") else "") + (" ↓CROSS" if r.get("d_macd_cross_down") else "")),
                        ("EMA Trend",   r.get("d_ema_trend","")),
                        ("Fib Zone",    f'{r.get("d_fib_zone","")} ({r.get("d_fib_position",0):.1f}%)'),
                        ("Conviction",  r.get("d_conviction","")),
                        ("Vol Trend",   f'{r.get("d_vol_trend","")} ({r.get("d_vol_ratio",1):.2f}×)'),
                        ("Weinstein",   f'{r.get("d_weinstein_score",0)}/6 — {r.get("d_weinstein_phase","")}'),
                        ("Momentum",    f'{r.get("d_momentum","")} (ROC {r.get("d_roc",0):.2f}%)'),
                        ("Volatility",  f'{r.get("d_volatility_label","")} ({r.get("d_volatility",0):.2f}%)'),
                    ]
                    for lbl, val in items_d:
                        st.markdown(f'<div style="padding:3px 0;font-size:12px">'
                                    f'<span style="color:#6b7099;width:90px;display:inline-block">{lbl}</span>'
                                    f'<b style="color:#c8cfe8">{val}</b></div>',
                                    unsafe_allow_html=True)

                    if flags:
                        st.markdown('<div style="margin-top:6px;font-size:12px">' +
                                    "  ".join(f'<span style="color:#a78bfa">{f}</span>' for f in flags) +
                                    '</div>', unsafe_allow_html=True)

                    if r.get("d_details"):
                        dd = r["d_details"]
                        checks = [
                            ("MA30 Curling",  dd.get("ma30_curling")),
                            ("MA10 > MA30",   dd.get("ma10_above_ma30")),
                            ("RS > 0",        dd.get("rs_positive")),
                            ("RS Improving",  dd.get("rs_improving")),
                            ("Vol Building",  dd.get("vol_building")),
                            ("Near 52W High", dd.get("near_52w_high")),
                        ]
                        badge_html = " ".join(
                            f'<span style="background:{"#0a2e18" if ok else "#1a1d2e"};'
                            f'color:{"#00e5a0" if ok else "#6b7099"};'
                            f'border-radius:4px;padding:2px 6px;font-size:10px;margin:2px">'
                            f'{"✅" if ok else "❌"} {lbl}</span>'
                            for lbl, ok in checks
                        )
                        st.markdown(f'<div style="margin-top:6px">{badge_html}</div>', unsafe_allow_html=True)

                # ── Technicals (MA + BB) ─────────────────────────────────────
                with dc3:
                    st.markdown("#### 📐 Technicals")

                    ma_rows = [
                        ("MA20",  r.get("ma20"), r.get("price_vs_ma20")),
                        ("MA50",  r.get("ma50"), r.get("price_vs_ma50")),
                        ("MA200", r.get("ma200"), r.get("price_vs_ma200")),
                    ]
                    for ma_lbl, ma_val, ma_pos in ma_rows:
                        if ma_val is None:
                            continue
                        pos_color = "#00e5a0" if ma_pos == "ABOVE" else ("#ff4d6a" if ma_pos == "BELOW" else "#6b7099")
                        st.markdown(
                            f'<div style="padding:3px 0;font-size:12px">'
                            f'<span style="color:#6b7099;width:60px;display:inline-block">{ma_lbl}</span>'
                            f'<b style="color:#c8cfe8">${ma_val:.2f}</b>'
                            f'&nbsp;<span style="color:{pos_color};font-size:11px">{ma_pos}</span>'
                            f'</div>', unsafe_allow_html=True,
                        )

                    if r.get("golden_cross"):
                        st.markdown('<div style="color:#00e5a0;font-size:12px;margin-top:4px">✅ Golden Cross (MA50 > MA200)</div>', unsafe_allow_html=True)
                    if r.get("death_cross"):
                        st.markdown('<div style="color:#ff4d6a;font-size:12px;margin-top:4px">❌ Death Cross (MA50 < MA200)</div>', unsafe_allow_html=True)

                    st.markdown('<div style="border-top:1px solid #1a1d2e;margin:10px 0"></div>', unsafe_allow_html=True)
                    st.markdown("**Bollinger Bands**")

                    bb_items = [
                        ("Upper", r.get("bb_upper", "?")),
                        ("Mid",   r.get("bb_mid",   "?")),
                        ("Lower", r.get("bb_lower", "?")),
                    ]
                    for lbl, val in bb_items:
                        st.markdown(f'<div style="padding:2px 0;font-size:12px">'
                                    f'<span style="color:#6b7099;width:60px;display:inline-block">{lbl}</span>'
                                    f'<b style="color:#c8cfe8">${val:.2f}</b></div>',
                                    unsafe_allow_html=True)

                    bb_pos = r.get("bb_position", "")
                    pct_b  = r.get("bb_pct_b", 0.5)
                    bw     = r.get("bb_bandwidth", 0)
                    pos_c  = "#ff4d6a" if bb_pos == "ABOVE_UPPER" else ("#00e5a0" if bb_pos == "BELOW_LOWER" else "#6b7099")
                    st.markdown(
                        f'<div style="font-size:12px;margin-top:4px">'
                        f'<span style="color:#6b7099">Width </span><b>{bw:.2f}%</b>'
                        f'&nbsp;&nbsp;<span style="color:#6b7099">%B </span><b>{pct_b:.3f}</b>'
                        f'&nbsp;&nbsp;<span style="color:{pos_c}">{bb_pos}</span>'
                        f'</div>', unsafe_allow_html=True,
                    )
                    if r.get("bb_squeeze"):
                        st.markdown(
                            '<div style="background:#2a2000;border:1px solid #ffe066;border-radius:4px;'
                            'padding:6px 10px;margin-top:6px;color:#ffe066;font-size:12px;font-weight:700">'
                            '⚡ Bollinger Squeeze Active — breakout imminent</div>',
                            unsafe_allow_html=True,
                        )

elif not scan_btn:
    st.markdown(
        '<div style="background:#0d0f17;border:1px dashed #1a1d2e;border-radius:8px;'
        'padding:40px;text-align:center;color:#6b7099;font-size:14px">'
        'Select a watchlist (or type tickers), configure scan conditions, then click <b style="color:#4d9fff">Run Scan</b>.'
        '</div>',
        unsafe_allow_html=True,
    )
