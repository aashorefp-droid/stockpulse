
# ==============================================================================
# MARKDOWN PANIC SELLING SCANNER
# ==============================================================================
# Scans for stocks currently in the MARKDOWN phase:
#   💔 MARKDOWN = Below VAL, Trending Down, Volume Declining
#   Indicates: Panic selling / distribution complete / smart money absent
#
# HOW TO USE:
#   1. Open ThinkorSwim → Scan → Stock Hacker
#   2. Add Study Filter → Edit → paste this script
#   3. Run on Daily or Weekly aggregation
#   4. Higher timeframe (Weekly) = stronger signal
# ==============================================================================

input lookback26w     = 26;     # 26 weeks = 6 months for range
input pocLevel        = 0.50;   # POC = 50% of range
input vahLevel        = 0.70;   # VAH = 70% of range
input valLevel        = 0.30;   # VAL = 30% of range

# ─── 26-WEEK RANGE LEVELS ────────────────────────────────────────────────────
def high26w  = Highest(high, lookback26w * 5);
def low26w   = Lowest(low,  lookback26w * 5);
def range26w = high26w - low26w;
def val      = low26w + (range26w * valLevel);

# ─── VOLUME TREND ─────────────────────────────────────────────────────────────
def recentVolAvg  = Average(volume, 25);
def priorVolAvg   = Average(volume[25], 25);
def volTrendRatio = if priorVolAvg > 0 then recentVolAvg / priorVolAvg else 1.0;

# ─── PRICE TREND ──────────────────────────────────────────────────────────────
def lows5wPrior   = Lowest(low[25], 25);
def trendingDown  = low < lows5wPrior;

# ─── MARKDOWN PHASE CONDITION ─────────────────────────────────────────────────
# Close below VAL (bottom 30% of 26-week range)
# Making new lows vs prior 25 bars
# Volume declining (ratio <= 1.1) — smart money absent, no buyers
def markdownPhase = (close <= val) and trendingDown and (volTrendRatio <= 1.1);

# ─── SCAN OUTPUT ──────────────────────────────────────────────────────────────
plot scan = markdownPhase;
