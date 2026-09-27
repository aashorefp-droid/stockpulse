"""
backend.services.best_pick
Implements the swing trade ranking and scoring algorithm for Strategy Scanner.
Ranks all scanned tickers or uploaded CSVs to find the BEST swing trade pick of the day.
"""
from typing import Union, List, Dict, Any, Optional
import io
import os
import logging
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
import pandas as pd
import numpy as np

logger = logging.getLogger(__name__)


def clean_percentage(val: Any) -> float:
    """Parses percentage strings like '15.5%' or '-2.5%' into floats."""
    if pd.isna(val):
        return np.nan
    if isinstance(val, (int, float)):
        return float(val)
    val_str = str(val).replace("%", "").strip()
    try:
        return float(val_str)
    except ValueError:
        return np.nan


def clean_currency(val: Any) -> float:
    """Parses currency strings like '$222.04' or '$16.55B' into floats."""
    if pd.isna(val):
        return np.nan
    if isinstance(val, (int, float)):
        return float(val)
    val_str = str(val).replace("$", "").replace(",", "").strip()
    multiplier = 1.0
    if val_str.endswith("B") or val_str.endswith("b"):
        multiplier = 1e9
        val_str = val_str[:-1]
    elif val_str.endswith("M") or val_str.endswith("m"):
        multiplier = 1e6
        val_str = val_str[:-1]
    elif val_str.endswith("T") or val_str.endswith("t"):
        multiplier = 1e12
        val_str = val_str[:-1]
    try:
        return float(val_str) * multiplier
    except ValueError:
        return np.nan


def clean_rr(val: Any) -> float:
    """Parses risk/reward strings like '2.4x' or '3.5' into floats."""
    if val is None or pd.isna(val):
        return 1.0
    if isinstance(val, (int, float)):
        return float(val) if not np.isnan(val) else 1.0
    val_str = str(val).replace("x", "").replace("X", "").strip()
    try:
        f = float(val_str)
        return f if not np.isnan(f) else 1.0
    except ValueError:
        return 1.0


def _safe_float(val: Any, default: Optional[float] = None) -> Optional[float]:
    """Safely converts any input (str, int, float, None, NaN) to float or returns default."""
    if val is None:
        return default
    try:
        if pd.isna(val):
            return default
    except Exception:
        pass
    if isinstance(val, (int, float)):
        return float(val) if not np.isnan(val) else default
    s = str(val).replace("%", "").replace("$", "").replace(",", "").replace("x", "").replace("X", "").strip()
    try:
        f = float(s)
        return f if not np.isnan(f) else default
    except (ValueError, TypeError):
        return default



def normalize_scanner_dataframe(df: pd.DataFrame) -> pd.DataFrame:
    """
    Standardizes column names and auto-derives missing columns from raw scanner dicts.
    Ensures compatibility whether passed from the live SSE scanner or an uploaded CSV.
    """
    df = df.copy()

    # Column alias mapping: target_col -> list of possible source names
    aliases = {
        "Ticker": ["Ticker", "ticker", "Symbol", "symbol"],
        "Price": ["Price", "price", "Close", "close", "last"],
        "Sector": ["Sector", "sector"],
        "Verdict": ["Verdict", "verdict"],
        "30wk MA Slope%": [
            "30wk MA Slope%", "30W MA Slope", "30W MA Slope%", "30wk MA Slope",
            "sma30_slope", "30W Slope%", "30w_slope", "Slope%", "30W Slope"
        ],
        "Valuation Upside%": [
            "Valuation Upside%", "target_upside", "upside", "Upside%", "Target Upside%", "Valuation Upside"
        ],
        "Swing Entry": ["Swing Entry", "entry", "Entry"],
        "Swing Stop": ["Swing Stop", "stop_loss", "Stop", "stop"],
        "Swing T1": ["Swing T1", "target1", "T1", "target_1"],
        "Swing Reward%": [
            "Swing Reward%", "reward_pct", "Reward%", "target1_gain_pct", "Swing Reward"
        ],
        "Swing Risk%": [
            "Swing Risk%", "risk_pct", "Risk%", "Swing Risk"
        ],
        "Swing R/R": [
            "Swing R/R", "rr_t1", "R/R", "rr", "Reward/Risk", "Swing RR"
        ],
        "Long Term % From Entry": ["Long Term % From Entry", "dist_from_sma30", "dist_from_entry", "30W Dist%"],
        "Dist From High%": [
            "Dist From High%", "dist_from_high", "Pct From High", "pct_from_high", "dist_high", "Dist From High"
        ],
        "Breakout Score": ["Breakout Score", "breakout_score"],
        "Vol Surge": ["Vol Surge", "vol_surge"],
        "Vol Ratio": ["Vol Ratio", "vol_ratio"],
        "Vol Trend": ["Vol Trend", "vol_trend"],
        "MTF Rank": ["MTF Rank", "mtf_rank", "rank"],
        "BTD": ["BTD", "btd", "btd_state"],
        "BTD Zone": ["BTD Zone", "btd_zone"],
        "Fundamental": ["Fundamental", "fundamental", "funda", "Funda"],
        "Next Day Summary": ["Next Day Summary", "next_day_summary", "summary"],
    }

    for target, source_names in aliases.items():
        if target not in df.columns:
            for src in source_names:
                if src in df.columns:
                    df[target] = df[src]
                    break

    # Parse Volume 1D vs 20D MA% if present
    if "Volume 1D vs 20D MA%" in df.columns:
        vol_pct = df["Volume 1D vs 20D MA%"].apply(clean_percentage)
        if "Vol Surge" not in df.columns:
            df["Vol Surge"] = vol_pct >= 35.0
        if "Vol Ratio" not in df.columns:
            df["Vol Ratio"] = (1.0 + vol_pct.fillna(0.0) / 100.0).clip(lower=0.1)

    # Fill defaults for essential identifiers
    if "Ticker" not in df.columns:
        df["Ticker"] = "UNKNOWN"
    if "Price" not in df.columns:
        df["Price"] = 0.0
    if "Sector" not in df.columns:
        df["Sector"] = "N/A"
    if "Verdict" not in df.columns:
        df["Verdict"] = "NEUTRAL"
    if "Dist From High%" not in df.columns:
        df["Dist From High%"] = 5.0
    if "Breakout Score" not in df.columns:
        df["Breakout Score"] = 0.0
    if "Vol Surge" not in df.columns:
        df["Vol Surge"] = False
    if "Vol Ratio" not in df.columns:
        df["Vol Ratio"] = 1.0
    if "Vol Trend" not in df.columns:
        df["Vol Trend"] = "FLAT"
    if "MTF Rank" not in df.columns:
        df["MTF Rank"] = 3

    # Derive Swing Entry / Stop / T1 if missing
    if "Swing Entry" not in df.columns or df["Swing Entry"].isna().all():
        df["Swing Entry"] = df["Price"]
    if "Swing Stop" not in df.columns or df["Swing Stop"].isna().all():
        df["Swing Stop"] = df["Price"] * 0.95
    if "Swing T1" not in df.columns or df["Swing T1"].isna().all():
        df["Swing T1"] = df["Price"] * 1.10

    # Auto-derive Swing Reward% & Risk% if not provided
    if "Swing Reward%" not in df.columns or df["Swing Reward%"].isna().all():
        entry = pd.to_numeric(df["Swing Entry"], errors="coerce").fillna(1.0)
        t1 = pd.to_numeric(df["Swing T1"], errors="coerce").fillna(entry)
        df["Swing Reward%"] = ((t1 - entry) / entry * 100).round(1)

    if "Swing Risk%" not in df.columns or df["Swing Risk%"].isna().all():
        entry = pd.to_numeric(df["Swing Entry"], errors="coerce").fillna(1.0)
        stop = pd.to_numeric(df["Swing Stop"], errors="coerce").fillna(entry * 0.95)
        df["Swing Risk%"] = ((entry - stop) / entry * 100).abs().round(1)

    # Auto-derive Swing R/R if missing
    if "Swing R/R" not in df.columns or df["Swing R/R"].isna().all():
        reward = pd.to_numeric(df["Swing Reward%"], errors="coerce").fillna(0.0)
        risk = pd.to_numeric(df["Swing Risk%"], errors="coerce").replace(0, np.nan)
        df["Swing R/R"] = (reward / risk).round(2).fillna(1.0)

    # Auto-derive 30wk MA Slope% if missing
    if "30wk MA Slope%" not in df.columns:
        df["30wk MA Slope%"] = 0.0

    # Auto-derive Valuation Upside% if missing
    if "Valuation Upside%" not in df.columns:
        df["Valuation Upside%"] = 0.0

    # Auto-derive Long Term % From Entry if missing
    if "Long Term % From Entry" not in df.columns:
        df["Long Term % From Entry"] = 0.0

    # Auto-derive BTD and BTD Zone if missing
    if "BTD" not in df.columns or df["BTD"].isna().all():
        btd_list = []
        zone_list = []
        for _, row in df.iterrows():
            grade = str(row.get("entry_grade", "")).upper()
            status = str(row.get("entry_status", "")).upper()
            stage = str(row.get("stage2_status", "")).upper()
            dist_sma_raw = row.get("dist_from_sma30") if row.get("dist_from_sma30") is not None else row.get("Long Term % From Entry")
            dist_sma = _safe_float(dist_sma_raw, 0.0)

            if grade in ("S", "A") or status == "ENTER":
                btd_list.append("TRIGGER")
                zone_list.append("Reclaimed 20 EMA / Breakout")
            elif stage == "FRESH" or (-2.0 <= dist_sma <= 8.5):
                btd_list.append("ARMED")
                zone_list.append("Dip 20-50 EMA")
            elif dist_sma < -2.0:
                btd_list.append("ARMED-DEEP")
                zone_list.append("Deep Dip 50-200 EMA")
            else:
                btd_list.append("EXTENDED")
                zone_list.append("Late Stage")

        df["BTD"] = btd_list
        if "BTD Zone" not in df.columns:
            df["BTD Zone"] = zone_list

    if "BTD Zone" not in df.columns:
        df["BTD Zone"] = "Support Zone"

    # Auto-derive Fundamental if missing
    if "Fundamental" not in df.columns or df["Fundamental"].isna().all():
        funda_list = []
        for _, row in df.iterrows():
            pm = _safe_float(row.get("profit_margin"))
            pe = _safe_float(row.get("pe_ratio"))
            eg = _safe_float(row.get("earnings_growth"))
            if (pm is not None and pm < 0) or (pe is not None and pe < 0):
                funda_list.append("Unprofitable")
            elif eg is not None and eg < 0:
                funda_list.append("Declining")
            else:
                funda_list.append("Profitable / Healthy")
        df["Fundamental"] = funda_list

    # Auto-derive Next Day Summary if missing
    if "Next Day Summary" not in df.columns or df["Next Day Summary"].isna().all():
        summary_list = []
        for _, row in df.iterrows():
            bs = _safe_float(row.get("breakout_score"), 0.0)
            vs = bool(row.get("vol_surge", False))
            db = str(row.get("daily_bias", "")).upper()
            if bs >= 7 or vs:
                summary_list.append("Strong Bullish Close (90%+ Range)")
            elif db == "BULLISH":
                summary_list.append("Bullish Follow-Through")
            else:
                summary_list.append("Neutral / Consolidating")
        df["Next Day Summary"] = summary_list

    return df


def analyze_and_rank_stocks(csv_path_or_df: Union[str, pd.DataFrame]) -> Dict[str, Any]:
    """
    Ranks stocks based on 'BUY STRENGTH EMERGING FROM TIGHTNESS'.
    Combines:
      - Macro Strength: Leaders near 52-week highs (dist_from_high <= 15%), Stage 2 rising 30W SMA
      - Micro Tightness: Volatility Contraction Pattern (VCP), compact risk stop (<= 8.5%), not overextended
      - Confirmed Emergence: Active TRIGGER (reclaimed 20 EMA / pivot breakout) with institutional volume expansion
      - Asymmetric R/R >= 1.8x and clean fundamental health
    """
    if isinstance(csv_path_or_df, str):
        if "\n" in csv_path_or_df:
            raw_df = pd.read_csv(io.StringIO(csv_path_or_df))
        else:
            raw_df = pd.read_csv(csv_path_or_df)
    else:
        raw_df = csv_path_or_df.copy()

    total_scanned = len(raw_df)
    if total_scanned == 0:
        return {
            "best_pick": None,
            "ranked": [],
            "total_scanned": 0,
            "strict_passed_count": 0,
            "is_strict": False,
        }

    # Normalize columns and auto-derive missing ones
    df = normalize_scanner_dataframe(raw_df)

    # 1. Clean Key Numerical Columns
    df["Price"] = pd.to_numeric(df["Price"].apply(clean_currency), errors="coerce").fillna(0.0)
    df["30wk MA Slope%"] = pd.to_numeric(df["30wk MA Slope%"].apply(clean_percentage), errors="coerce").fillna(0.0)
    df["Valuation Upside%"] = pd.to_numeric(df["Valuation Upside%"].apply(clean_percentage), errors="coerce").fillna(0.0)
    df["Swing Reward%"] = pd.to_numeric(df["Swing Reward%"].apply(clean_percentage), errors="coerce").fillna(0.0)
    df["Swing Risk%"] = pd.to_numeric(df["Swing Risk%"].apply(clean_percentage), errors="coerce").fillna(5.0)
    df["Swing R/R"] = pd.to_numeric(df["Swing R/R"].apply(clean_rr), errors="coerce").fillna(1.0)
    df["Long Term % From Entry"] = pd.to_numeric(df["Long Term % From Entry"].apply(clean_percentage), errors="coerce").fillna(0.0)
    df["Dist From High%"] = pd.to_numeric(df["Dist From High%"].apply(clean_percentage), errors="coerce").fillna(10.0)
    df["Breakout Score"] = pd.to_numeric(df["Breakout Score"], errors="coerce").fillna(0.0)
    df["Vol Ratio"] = pd.to_numeric(df["Vol Ratio"], errors="coerce").fillna(1.0)
    df["MTF Rank"] = pd.to_numeric(df["MTF Rank"], errors="coerce").fillna(3)
    df["Swing Entry"] = pd.to_numeric(df["Swing Entry"].apply(clean_currency), errors="coerce").fillna(df["Price"])
    df["Swing Stop"] = pd.to_numeric(df["Swing Stop"].apply(clean_currency), errors="coerce").fillna(df["Swing Entry"] * 0.95)
    df["Swing T1"] = pd.to_numeric(df["Swing T1"].apply(clean_currency), errors="coerce").fillna(df["Swing Entry"] * 1.10)

    # 2. Scoring Engines for the 3 Distinct Paradigms

    # A. Buy the Strength (Momentum Leader near 52W Highs)
    def calc_strength_score(row: pd.Series) -> float:
        score = 25.0
        # Proximity to 52W High (up to +35 pts)
        dh = _safe_float(row.get("Dist From High%"), 10.0)
        if dh <= 2.0:
            score += 35.0
        elif dh <= 5.0:
            score += 25.0
        elif dh <= 8.0:
            score += 15.0
        elif dh <= 12.0:
            score += 8.0

        # Institutional Volume Surge (up to +25 pts)
        vol_surge = bool(row.get("Vol Surge", False))
        vol_ratio = _safe_float(row.get("Vol Ratio"), 1.0)
        vol_trend = str(row.get("Vol Trend", "")).upper()
        if vol_surge or vol_ratio >= 1.5:
            score += 25.0
        elif vol_trend == "ACCUMULATING" or vol_ratio >= 1.2:
            score += 15.0

        # Trend Velocity / 30W Slope (up to +20 pts)
        slope = _safe_float(row.get("30wk MA Slope%"), 0.0)
        if slope >= 5.0:
            score += 20.0
        elif slope >= 2.5:
            score += 15.0
        elif slope > 0.0:
            score += 8.0

        # Breakout Score & Closing Strength (up to +20 pts)
        summary = str(row.get("Next Day Summary", "")).lower()
        bs = _safe_float(row.get("Breakout Score"), 0.0)
        if "strong" in summary or "9" in summary or bs >= 7:
            score += 20.0
        elif "bullish" in summary:
            score += 10.0

        return round(min(score, 100.0), 1)

    # B. Buy the Emerging (Strength Emerging from Tightness / VCP Base Breakout)
    def calc_emerging_score(row: pd.Series) -> float:
        score = 25.0
        # Base Tightness / Low Risk Invalidation (up to +25 pts)
        risk = _safe_float(row.get("Swing Risk%"), 5.0)
        if risk <= 3.5:
            score += 25.0  # Extreme VCP coil
        elif risk <= 5.5:
            score += 20.0  # Very tight base
        elif risk <= 7.5:
            score += 12.0
        elif risk <= 8.5:
            score += 6.0

        # Emergence Trigger (up to +25 pts)
        btd_val = str(row.get("BTD", "")).upper()
        if btd_val == "TRIGGER":
            score += 25.0  # Reclaimed 20 EMA / active expansion today
        elif btd_val == "ARMED":
            score += 12.0  # Tightly coiled at pivot

        # Proximity to 52W High (up to +20 pts)
        dh = _safe_float(row.get("Dist From High%"), 10.0)
        if dh <= 3.0:
            score += 20.0
        elif dh <= 7.0:
            score += 15.0
        elif dh <= 12.0:
            score += 10.0

        # Volume Expansion on Trigger (up to +15 pts)
        vol_surge = bool(row.get("Vol Surge", False))
        vol_ratio = _safe_float(row.get("Vol Ratio"), 1.0)
        if vol_surge or vol_ratio >= 1.4:
            score += 15.0
        elif vol_ratio >= 1.15:
            score += 8.0

        # Trend Velocity (up to +15 pts)
        slope = _safe_float(row.get("30wk MA Slope%"), 0.0)
        if slope >= 3.0:
            score += 15.0
        elif slope > 0.0:
            score += 8.0

        return round(min(score, 100.0), 1)

    # C. Buy the Weakness (High R/R Dip at Support / Oversold Mean Reversion)
    def calc_weakness_score(row: pd.Series) -> float:
        score = 25.0
        dh = _safe_float(row.get("Dist From High%"), 10.0)
        # If right at ATH, strongly penalize because it is not a dip/weakness buy
        if dh <= 2.5:
            score -= 30.0
        elif dh >= 10.0:
            score += 20.0
        elif dh >= 5.0:
            score += 12.0

        # Asymmetric Risk/Reward Ratio (up to +30 pts)
        rr = _safe_float(row.get("Swing R/R"), 1.0)
        if rr >= 3.0:
            score += 30.0
        elif rr >= 2.2:
            score += 20.0
        elif rr >= 1.8:
            score += 12.0

        # Valuation Upside / Fair Value Discount (up to +25 pts)
        upside = _safe_float(row.get("Valuation Upside%"), 0.0)
        if upside >= 25.0:
            score += 25.0
        elif upside >= 15.0:
            score += 18.0
        elif upside >= 8.0:
            score += 10.0

        # Support Zone Confluence (up to +20 pts)
        zone = str(row.get("BTD Zone", "")).lower()
        btd_val = str(row.get("BTD", "")).upper()
        if "support" in zone or "50-200" in zone or "20-50" in zone:
            score += 20.0
        elif btd_val in ("ARMED", "ARMED-DEEP"):
            score += 15.0

        # Swing Reward Potential (up to +15 pts)
        reward = _safe_float(row.get("Swing Reward%"), 0.0)
        if reward >= 12.0:
            score += 15.0
        elif reward >= 7.0:
            score += 10.0

        # Fundamental Health (up to +10 pts)
        funda = str(row.get("Fundamental", "")).lower()
        if "profit" in funda or "healthy" in funda:
            score += 10.0

        return round(max(0.0, min(score, 100.0)), 1)

    # 3. Calculate scores for all rows
    df["Score_Strength"] = df.apply(calc_strength_score, axis=1)
    df["Score_Emerging"] = df.apply(calc_emerging_score, axis=1)
    df["Score_Weakness"] = df.apply(calc_weakness_score, axis=1)

    # Helper function to serialize a row into a clean record
    def format_record(row: pd.Series, category: str, score_val: float) -> Dict[str, Any]:
        risk_val = round(float(row["Swing Risk%"]), 1) if pd.notna(row["Swing Risk%"]) else 5.0
        btd_str = str(row["BTD"]).upper()

        if risk_val <= 4.0:
            tightness_rating = "⚡ Tight Coil (VCP)"
        elif risk_val <= 6.5:
            tightness_rating = "🎯 Constructive Base"
        elif risk_val <= 8.5:
            tightness_rating = "Solid Base"
        else:
            tightness_rating = "Wide / Volatile"

        if btd_str == "TRIGGER":
            setup_status = "🚀 Emerging Today"
        elif btd_str == "ARMED":
            setup_status = "⏳ Coiled at Pivot"
        elif btd_str == "ARMED-DEEP":
            setup_status = "🛡️ Dip at Support"
        else:
            setup_status = "Consolidating"

        cat_upper = category.upper()
        if cat_upper == "STRENGTH":
            cat_badge = "⚡ Strength"
            cat_title = "BUY THE STRENGTH"
            cat_tagline = "⚡ 52W High Momentum Leader"
        elif cat_upper == "EMERGING":
            cat_badge = "🚀 Emerging"
            cat_title = "BUY THE EMERGING"
            cat_tagline = "🚀 Fresh Breakout Emerging from Tight Base"
        else:
            cat_badge = "🛡️ Weakness"
            cat_title = "BUY THE WEAKNESS"
            cat_tagline = "🛡️ High R/R Dip at Key Support"

        return {
            "ticker": str(row["Ticker"]),
            "sector": str(row["Sector"]) if pd.notna(row["Sector"]) else "N/A",
            "price": _safe_float(row["Price"]),
            "verdict": str(row["Verdict"]),
            "btd": str(row["BTD"]),
            "btd_zone": str(row["BTD Zone"]) if pd.notna(row["BTD Zone"]) else "—",
            "dist_from_high": round(_safe_float(row["Dist From High%"], 0.0), 1),
            "sma30_slope": round(_safe_float(row["30wk MA Slope%"], 0.0), 2),
            "valuation_upside": round(_safe_float(row["Valuation Upside%"], 0.0), 1),
            "swing_entry": _safe_float(row["Swing Entry"]),
            "swing_stop": _safe_float(row["Swing Stop"]),
            "swing_t1": _safe_float(row["Swing T1"]),
            "swing_reward_pct": round(_safe_float(row["Swing Reward%"], 0.0), 1),
            "swing_risk_pct": risk_val,
            "swing_rr": round(_safe_float(row["Swing R/R"], 1.0), 2),
            "tightness_rating": tightness_rating,
            "setup_status": setup_status,
            "category": cat_upper,
            "category_badge": cat_badge,
            "category_title": cat_title,
            "category_tagline": cat_tagline,
            "score": round(_safe_float(score_val, 0.0), 1),
        }

    # 4. Filter Candidate Pools for each of the 3 Paradigms

    # Pool 1: Strength (Leaders near highs)
    valid_verdicts = ["BULLISH", "LEAN BULLISH"]
    pool_strength = df[
        (df["Verdict"].astype(str).str.upper().isin(valid_verdicts)) &
        (df["30wk MA Slope%"] > 0) &
        (df["Dist From High%"] <= 12.0) &
        (df["BTD"] != "ARMED-DEEP")
    ].sort_values(by="Score_Strength", ascending=False)
    if pool_strength.empty:
        pool_strength = df[df["Verdict"].astype(str).str.upper().isin(valid_verdicts)].sort_values(by="Score_Strength", ascending=False)
    if pool_strength.empty:
        pool_strength = df.sort_values(by="Score_Strength", ascending=False)

    best_strength = format_record(pool_strength.iloc[0], "STRENGTH", pool_strength.iloc[0]["Score_Strength"]) if not pool_strength.empty else None

    # Pool 2: Emerging (Fresh breakouts emerging from tight bases)
    pool_emerging = df[
        (df["Verdict"].astype(str).str.upper().isin(valid_verdicts)) &
        (df["30wk MA Slope%"] > 0) &
        (df["Dist From High%"] <= 15.0) &
        (df["Swing Risk%"] <= 8.5) &
        (df["BTD"].astype(str).str.upper().isin(["TRIGGER", "ARMED"])) &
        (df["Swing R/R"] >= 1.8)
    ].sort_values(by="Score_Emerging", ascending=False)
    if pool_emerging.empty:
        pool_emerging = df[
            (df["Verdict"].astype(str).str.upper().isin(valid_verdicts)) &
            (df["Dist From High%"] <= 18.0)
        ].sort_values(by="Score_Emerging", ascending=False)
    if pool_emerging.empty:
        pool_emerging = df.sort_values(by="Score_Emerging", ascending=False)

    # Pick emerging winner, preferring distinct ticker from strength
    best_emerging = None
    if not pool_emerging.empty:
        str_ticker = best_strength["ticker"] if best_strength else None
        cand_emg = pool_emerging[pool_emerging["Ticker"] != str_ticker]
        pick_emg = cand_emg.iloc[0] if not cand_emg.empty else pool_emerging.iloc[0]
        best_emerging = format_record(pick_emg, "EMERGING", pick_emg["Score_Emerging"])

    # Pool 3: Weakness (High R/R Dips at Support / Oversold Mean Reversion)
    pool_weakness = df[
        (~df["Fundamental"].fillna("").astype(str).str.contains("Unprofitable|Declining", case=False)) &
        (df["Swing R/R"] >= 1.8) &
        (
            (df["BTD"].astype(str).str.upper().isin(["ARMED-DEEP", "ARMED"])) |
            (df["Dist From High%"] >= 4.0)
        ) &
        (df["Dist From High%"] >= 2.5)  # Must be on a dip, not right at 52W high
    ].sort_values(by="Score_Weakness", ascending=False)
    if pool_weakness.empty:
        pool_weakness = df[
            (df["Swing R/R"] >= 1.8) &
            (df["Dist From High%"] >= 2.0)
        ].sort_values(by="Score_Weakness", ascending=False)
    if pool_weakness.empty:
        pool_weakness = df.sort_values(by="Score_Weakness", ascending=False)

    # Pick weakness winner, preferring distinct ticker from strength and emerging
    best_weakness = None
    if not pool_weakness.empty:
        taken = {t for t in [best_strength["ticker"] if best_strength else None, best_emerging["ticker"] if best_emerging else None] if t}
        cand_wk = pool_weakness[~pool_weakness["Ticker"].isin(taken)]
        pick_wk = cand_wk.iloc[0] if not cand_wk.empty else pool_weakness.iloc[0]
        best_weakness = format_record(pick_wk, "WEAKNESS", pick_wk["Score_Weakness"])

    # 5. Composite Ranking of All Candidates with Category Tagging
    # Determine best category for each ticker:
    def classify_row_category(row: pd.Series) -> str:
        s_score = _safe_float(row.get("Score_Strength"), 0.0)
        e_score = _safe_float(row.get("Score_Emerging"), 0.0)
        w_score = _safe_float(row.get("Score_Weakness"), 0.0)
        btd = str(row.get("BTD", "")).upper()
        dh = _safe_float(row.get("Dist From High%"), 10.0)

        # Direct domain criteria
        if btd == "ARMED-DEEP" or (dh >= 6.0 and w_score >= 65.0 and btd in ("ARMED", "ARMED-DEEP")):
            return "WEAKNESS"
        if dh <= 3.5 and s_score >= 65.0:
            return "STRENGTH"
        if btd == "TRIGGER" and e_score >= 65.0:
            return "EMERGING"

        # Compare scores
        scores = [("STRENGTH", s_score), ("EMERGING", e_score), ("WEAKNESS", w_score)]
        scores.sort(key=lambda x: x[1], reverse=True)
        return scores[0][0]

    ranked_records = []
    for _, row in df.iterrows():
        cat = classify_row_category(row)
        score_val = row[f"Score_{cat.title()}"]
        ranked_records.append(format_record(row, cat, score_val))

    ranked_records.sort(key=lambda r: r["score"], reverse=True)

    # Primary default pick: prefer emerging, then strength, then weakness
    primary_pick = best_emerging or best_strength or best_weakness

    strict_count = len([r for r in ranked_records if r["score"] >= 70.0])

    return {
        "best_picks": {
            "strength": best_strength,
            "emerging": best_emerging,
            "weakness": best_weakness,
        },
        "best_pick": primary_pick,
        "ranked": ranked_records,
        "total_scanned": total_scanned,
        "strict_passed_count": strict_count,
        "is_strict": True if strict_count > 0 else False,
    }


def _derive_options_contract(price: Any, direction: str = "LONG") -> str:
    """Calculates ATM strike and nearest Friday expiration for swing trades."""
    p = _safe_float(price, 0.0)
    if not p or p <= 0:
        return "N/A"

    if p >= 200:
        step = 5.0
    elif p >= 50:
        step = 2.5
    elif p >= 20:
        step = 1.0
    else:
        step = 0.5
    atm_strike = round(round(p / step) * step, 2)
    atm_str = f"${atm_strike:.0f}" if atm_strike.is_integer() else f"${atm_strike:.2f}"

    try:
        now_cst = datetime.now(ZoneInfo("America/Chicago"))
    except Exception:
        now_cst = datetime.now()
    days_to_fri = (4 - now_cst.weekday()) % 7
    if days_to_fri == 0 and now_cst.hour >= 15:
        days_to_fri = 7
    exp_fri = (now_cst + timedelta(days=days_to_fri)).strftime("%b %d")

    return f"Buy {atm_str} Call (Exp {exp_fri})" if str(direction).upper() != "SHORT" else f"Buy {atm_str} Put (Exp {exp_fri})"


def format_triad_telegram_message(
    best_picks: Dict[str, Any],
    total_scanned: int = 0,
    source_label: str = "TOS Scan",
) -> str:
    """
    Formats a clean, high-impact HTML alert message for Telegram featuring
    one top pick for each of the 3 swing trading categories:
      ⚡ BUY THE STRENGTH
      🚀 BUY THE EMERGING
      🛡️ BUY THE WEAKNESS
    """
    try:
        now_cst = datetime.now(ZoneInfo("America/Chicago"))
    except Exception:
        now_cst = datetime.now()
    date_str = now_cst.strftime("%b %d, %Y")
    time_str = now_cst.strftime("%I:%M %p CT")

    lines = [
        "🎯 <b>STOCKPULSE SWING TRADING TRIAD</b>",
        f"📅 <i>{date_str} · {time_str} · {source_label} ({total_scanned} scanned)</i>",
        "━━━━━━━━━━━━━━━━━━━━━━━━━━",
    ]

    # 1. ⚡ BUY THE STRENGTH
    str_pick = best_picks.get("strength")
    if str_pick:
        tk = str_pick["ticker"]
        price = _safe_float(str_pick.get("price"), 0.0)
        score = _safe_float(str_pick.get("score"), 0.0)
        verdict = str_pick.get("verdict") or "BULLISH"
        slope = _safe_float(str_pick.get("sma30_slope"), 0.0)
        dh = _safe_float(str_pick.get("dist_from_high"), 0.0)
        entry = _safe_float(str_pick.get("swing_entry"), price)
        stop = _safe_float(str_pick.get("swing_stop"), entry * 0.95)
        t1 = _safe_float(str_pick.get("swing_t1"), entry * 1.10)
        risk = _safe_float(str_pick.get("swing_risk_pct"), 5.0)
        reward = _safe_float(str_pick.get("swing_reward_pct"), 10.0)
        rr = _safe_float(str_pick.get("swing_rr"), 2.0)
        opt_contract = _derive_options_contract(price, "LONG")

        lines.extend([
            f"⚡ <b>#1 BUY THE STRENGTH: {tk}</b> · <b>${price:.2f}</b>",
            f"<i>52W High Momentum Leader · Score: {score}/100</i>",
            f"• <b>Verdict</b>: {verdict} | 30W Slope: +{slope:.1f}% | ATH: -{dh:.1f}%",
            f"• <b>Entry</b>: ${entry:.2f} | <b>Stop</b>: ${stop:.2f} (-{risk:.1f}%)",
            f"• <b>Target 1</b>: ${t1:.2f} (+{reward:.1f}%) | <b>R:R</b>: {rr:.1f}×",
            f"• 💡 <b>Options</b>: <code>{opt_contract}</code>",
            "",
        ])
    else:
        lines.extend([
            "⚡ <b>#1 BUY THE STRENGTH</b>",
            "<i>No qualifying 52W High momentum leader found in this scan.</i>",
            "",
        ])

    # 2. 🚀 BUY THE EMERGING
    emg_pick = best_picks.get("emerging")
    if emg_pick:
        tk = emg_pick["ticker"]
        price = _safe_float(emg_pick.get("price"), 0.0)
        score = _safe_float(emg_pick.get("score"), 0.0)
        verdict = emg_pick.get("verdict") or "BULLISH"
        tightness = emg_pick.get("tightness_rating") or "Tight Base"
        status = emg_pick.get("setup_status") or "Emerging"
        entry = _safe_float(emg_pick.get("swing_entry"), price)
        stop = _safe_float(emg_pick.get("swing_stop"), entry * 0.95)
        t1 = _safe_float(emg_pick.get("swing_t1"), entry * 1.10)
        risk = _safe_float(emg_pick.get("swing_risk_pct"), 5.0)
        reward = _safe_float(emg_pick.get("swing_reward_pct"), 10.0)
        rr = _safe_float(emg_pick.get("swing_rr"), 2.0)
        opt_contract = _derive_options_contract(price, "LONG")

        lines.extend([
            f"🚀 <b>#1 BUY THE EMERGING: {tk}</b> · <b>${price:.2f}</b>",
            f"<i>Coiled Breakout from Tight Base · Score: {score}/100</i>",
            f"• <b>Verdict</b>: {verdict} | Rating: {tightness}",
            f"• <b>Status</b>: {status} (Risk: {risk:.1f}%)",
            f"• <b>Entry</b>: ${entry:.2f} | <b>Stop</b>: ${stop:.2f} (-{risk:.1f}%)",
            f"• <b>Target 1</b>: ${t1:.2f} (+{reward:.1f}%) | <b>R:R</b>: {rr:.1f}×",
            f"• 💡 <b>Options</b>: <code>{opt_contract}</code>",
            "",
        ])
    else:
        lines.extend([
            "🚀 <b>#1 BUY THE EMERGING</b>",
            "<i>No qualifying tight coil VCP breakout found in this scan.</i>",
            "",
        ])

    # 3. 🛡️ BUY THE WEAKNESS
    wk_pick = best_picks.get("weakness")
    if wk_pick:
        tk = wk_pick["ticker"]
        price = _safe_float(wk_pick.get("price"), 0.0)
        score = _safe_float(wk_pick.get("score"), 0.0)
        verdict = wk_pick.get("verdict") or "LEAN BULLISH"
        zone = wk_pick.get("btd_zone") or "Support Zone"
        upside = _safe_float(wk_pick.get("valuation_upside"), 0.0)
        dh = _safe_float(wk_pick.get("dist_from_high"), 0.0)
        entry = _safe_float(wk_pick.get("swing_entry"), price)
        stop = _safe_float(wk_pick.get("swing_stop"), entry * 0.95)
        t1 = _safe_float(wk_pick.get("swing_t1"), entry * 1.10)
        risk = _safe_float(wk_pick.get("swing_risk_pct"), 5.0)
        reward = _safe_float(wk_pick.get("swing_reward_pct"), 10.0)
        rr = _safe_float(wk_pick.get("swing_rr"), 2.0)
        opt_contract = _derive_options_contract(price, "LONG")

        lines.extend([
            f"🛡️ <b>#1 BUY THE WEAKNESS: {tk}</b> · <b>${price:.2f}</b>",
            f"<i>High R/R Dip at Key Support · Score: {score}/100</i>",
            f"• <b>Verdict</b>: {verdict} | Support: {zone}",
            f"• <b>Valuation Upside</b>: +{upside:.1f}% | Pullback: -{dh:.1f}% from High",
            f"• <b>Entry</b>: ${entry:.2f} | <b>Stop</b>: ${stop:.2f} (-{risk:.1f}%)",
            f"• <b>Target 1</b>: ${t1:.2f} (+{reward:.1f}%) | <b>R:R</b>: {rr:.1f}×",
            f"• 💡 <b>Options</b>: <code>{opt_contract}</code>",
            "",
        ])
    else:
        lines.extend([
            "🛡️ <b>#1 BUY THE WEAKNESS</b>",
            "<i>No qualifying dip support setup found in this scan.</i>",
            "",
        ])

    lines.extend([
        "━━━━━━━━━━━━━━━━━━━━━━━━━━",
        "🔒 <i>Trade with disciplined risk management. Set hard stops before entry.</i>",
    ])

    return "\n".join(lines)


def dispatch_triad_telegram_alert(
    items_or_df: Optional[Union[pd.DataFrame, List[Dict[str, Any]]]] = None,
    watchlist: str = "tos_email",
    subjects: str = "",
    days: int = 1,
    send_msg: bool = True,
    bot_token: Optional[str] = None,
    chat_id: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Ranks the 3 swing trading categories and sends the Triad Telegram alert.
    If items_or_df is not provided, scans the specified watchlist.
    """
    from concurrent.futures import ThreadPoolExecutor, as_completed
    from backend.services.telegram_svc import send_telegram

    # Resolve Telegram credentials
    try:
        from backend.config import TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID
        token = (bot_token or TELEGRAM_BOT_TOKEN or "").strip()
        cid = (chat_id or TELEGRAM_CHAT_ID or "").strip()
    except Exception:
        token = (bot_token or os.getenv("TELEGRAM_BOT_TOKEN", "")).strip()
        cid = (chat_id or os.getenv("TELEGRAM_CHAT_ID", "")).strip()

    source_label = "TOS Scan"
    total_scanned = 0

    if items_or_df is not None:
        if isinstance(items_or_df, pd.DataFrame):
            df = items_or_df.copy()
            total_scanned = len(df)
        else:
            items = [
                it for it in items_or_df
                if isinstance(it, dict) and not it.get("error") and (
                    it.get("ticker") or it.get("Ticker") or it.get("Symbol") or it.get("symbol")
                )
            ]
            df = pd.DataFrame(items)
            total_scanned = len(df)
        source_label = "Scanner Setup"
    else:
        # Fetch tickers
        if watchlist in ("tos_email", "tos", "gmail"):
            source_label = "TOS Email Watchlist"
            try:
                from backend.services.gmail_watchlist import fetch_today_watchlist
                ticker_list = fetch_today_watchlist(subjects=subjects, days=days)
            except Exception as e:
                logger.warning(f"Failed fetching TOS watchlist for triad alert: {e}")
                ticker_list = []
            if not ticker_list:
                from backend.services.scanner import WATCHLISTS
                ticker_list = WATCHLISTS.get("default", [])
                source_label = "Default 50 (TOS Fallback)"
        else:
            from backend.services.scanner import WATCHLISTS
            ticker_list = WATCHLISTS.get(watchlist, WATCHLISTS.get("default", []))
            source_label = f"{watchlist.capitalize()} Watchlist"

        # Concurrently scan tickers
        from backend.services.scanner import scan_single
        scanned_items = []
        with ThreadPoolExecutor(max_workers=10) as executor:
            futures = {executor.submit(scan_single, t): t for t in ticker_list}
            for fut in as_completed(futures):
                try:
                    res = fut.result()
                    if res and not res.get("error") and _safe_float(res.get("price"), 0.0) > 0:
                        scanned_items.append(res)
                except Exception as e:
                    logger.warning(f"Error scanning ticker for triad alert: {e}")

        total_scanned = len(ticker_list)
        df = pd.DataFrame(scanned_items)

    # Run ranking analysis
    ranking_res = analyze_and_rank_stocks(df) if not df.empty else {
        "best_picks": {"strength": None, "emerging": None, "weakness": None},
        "best_pick": None,
        "ranked": [],
        "total_scanned": total_scanned,
        "strict_passed_count": 0,
        "is_strict": False,
    }

    best_picks = ranking_res.get("best_picks", {})
    message_text = format_triad_telegram_message(best_picks, total_scanned=total_scanned, source_label=source_label)

    sent = False
    send_err = None
    if send_msg and token and cid:
        try:
            sent = send_telegram(token, cid, message_text)
            if sent:
                logger.info(f"Triad Telegram alert successfully sent ({total_scanned} tickers scanned)")
            else:
                send_err = "Telegram API rejected message"
        except Exception as e:
            send_err = str(e)
            logger.error(f"Error sending Triad Telegram alert: {e}")
    elif send_msg and (not token or not cid):
        send_err = "Telegram credentials not configured"

    return {
        "ok": True,
        "sent": sent,
        "error": send_err,
        "total_scanned": total_scanned,
        "source_label": source_label,
        "best_picks": best_picks,
        "message": message_text,
    }
