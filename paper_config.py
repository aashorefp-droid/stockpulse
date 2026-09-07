"""
paper_config.py — Configurable conditions for paper trading during replay sessions.

Controls which tracking-table trades are eligible for paper execution,
position sizing, exit rules, and filter thresholds.

Edit the values below to tune your paper trading strategy.
"""

# ═══════════════════════════════════════════════════════════════════════════════
# POSITION SIZING
# ═══════════════════════════════════════════════════════════════════════════════
POSITION_SIZE = 1_000         # $ per trade
MAX_OPEN_TRADES = 5             # Max simultaneous open paper trades
MAX_TRADES_PER_TICKER = 1       # Max open trades per ticker per day

# ═══════════════════════════════════════════════════════════════════════════════
# ENTRY CONDITIONS — trade must pass ALL enabled filters to enter
# Set a filter to None or False to disable it.
# ═══════════════════════════════════════════════════════════════════════════════

# -- Direction filter: which trade directions are allowed
ALLOW_LONG = True                        # Take LONG paper trades
ALLOW_SHORT = False                       # Take SHORT paper trades

# -- Scenario filter: which scenarios are eligible for paper trades
ALLOWED_SCENARIOS = ["ONE", "OPS"]              # Empty = allow all scenarios. Options: ["ONE", "OBE", "OPS", "GAP", "8:30", "9:00"]

# -- GAP: don't auto-track GAP scenarios until bias flips direction
GAP_REQUIRE_FLIP = True                          # True = only auto-track GAP tickers when bias dir ≠ original plan dir

# -- Alignment: require bias alignment before entering
REQUIRE_ALIGNMENT = "CONFIRMED"                 # TESTING: disabled. Normal: "CONFIRMED"

# -- V-Flow: require VWAP flow alignment (Live vs VWAP vs Prev VWAP)
REQUIRE_VFLOW = False                    # TESTING: disabled. Normal: True
ALLOW_PARTIAL_VFLOW = False          # True = also allow 🟡 (partial), only used when REQUIRE_VFLOW=True

# -- RVOL: minimum relative volume to enter
MIN_RVOL = None                          # TESTING: disabled. Normal: 0.5

# -- VWAP: require price on correct side of VWAP
REQUIRE_VWAP_ALIGNED = False             # TESTING: disabled. Normal: True

# -- News sentiment filter
NEWS_FILTER = None                       # TESTING: disabled. Normal: "NO_BAD"

# -- OHLC signal filter
REQUIRE_OHLC_ALIGNED = False         # True = LONG needs BULL OHLC, SHORT needs BEAR OHLC

# -- Confidence filter
MIN_CONFIDENCE = None                # "HIGH", "MEDIUM", "LOW", or None to disable
CONFIDENCE_PRIORITY = {              # Ranking (lower = better)
    "HIGH": 1,
    "MEDIUM": 2,
    "LOW": 3,
}

# -- Minimum RR (reward/risk) to enter
MIN_RR_T1 = None                         # TESTING: disabled. Normal: 1.0
MIN_BEST_RR = None                       # TESTING: disabled. Normal: 1.5

# -- Pullback entry: wait for price to retrace toward entry instead of entering immediately
ENTRY_ON_PULLBACK = False            # True = only enter when price pulls back near entry price
PULLBACK_MAX_DIST_PCT = 0.3          # Max % distance from entry to qualify as pullback (e.g. 0.3 = within 0.3%)

# -- P&L gate: don't enter if already moved too much
MAX_ENTRY_PNL_PCT = None                 # TESTING: disabled. Normal: 1.0

# ═══════════════════════════════════════════════════════════════════════════════
# SCENARIO-SPECIFIC ENTRY RULES
# ═══════════════════════════════════════════════════════════════════════════════
# Per-scenario (and optionally per-direction) entry behavior.
# Actions:
#   "ENTER"           — Enter immediately when all other filters pass
#   "WAIT_FLIP"       — Only enter after bias flips direction (CONFIRMED in opposite dir)
#   "WAIT_STOP_ZONE"  — Only enter when price reaches within stop_zone_pct of the stop level
#   "SKIP"            — Never enter this scenario
#
# Format: { "SCENARIO": "ACTION" }
#   or nested by direction: { "SCENARIO": { "LONG": "ACTION", "SHORT": "ACTION" } }
#   Optional params: use a dict { "action": "...", "stop_zone_pct": 0.5 }
#
SCENARIO_ENTRY_RULES = {
    "ONE": "ENTER",                                      # Opens Near Entry → enter immediately
    "OBE": "ENTER",                                      # Opens Between Entry & Stop → enter
    "OPS": {"action": "WAIT_STOP_ZONE", "stop_zone_pct": 0.5},  # Opens Past Stop → wait for price near stop
    "GAP": {                                              # Big Gap → direction-dependent
        "LONG": "ENTER",                                  #   GAP LONG → enter
        "SHORT": "WAIT_FLIP",                             #   GAP SHORT → wait for bias to flip
    },
}

# ═══════════════════════════════════════════════════════════════════════════════
# EXIT CONDITIONS — checked every price update during replay
# ═══════════════════════════════════════════════════════════════════════════════

# -- Primary exits (always active)
EXIT_ON_STOP = True                  # Exit when price hits stop loss
EXIT_ON_T1 = True                    # Exit when price hits T1 (take profit)
EXIT_ON_T2 = False                   # Exit when price hits T2 (extended target)
EXIT_ON_BEST_RR = False              # Exit when live Best RR target is hit (whichever of T1/T2 gives best RR)
MIN_EXIT_BEST_RR = 2.0               # Best RR multiplier threshold to trigger exit (e.g. 2.0 = 2:1 reward:risk)

# -- Partial exit at T1
PARTIAL_EXIT_AT_T1 = False           # True = sell half at T1, trail rest to T2
PARTIAL_EXIT_PCT = 50                # % of position to exit at T1 (if PARTIAL_EXIT_AT_T1=True)

# -- Trail stop after T1 hit
TRAIL_STOP_AFTER_T1 = False          # True = move stop to entry (breakeven) after T1 hit
TRAIL_STOP_PCT = None                # Trail stop by this % below price (e.g. 0.5 = 0.5%), None = use breakeven

# -- Time-based exit
EXIT_AT_SESSION_END = True           # Force-close all open trades at session end
SESSION_END_TIME_CST = "14:55"       # CST time to force-close (14:55 = 2:55 PM, before 3 PM close)

# -- Alignment-based exit
EXIT_ON_DIVERGED = False             # Exit if alignment flips to DIVERGED during trade
EXIT_ON_VFLOW_AGAINST = False        # Exit if V-Flow goes to ❌ (against)

# -- Max loss per trade
MAX_LOSS_PCT = None                  # Force exit if loss exceeds this % (e.g. 3.0 = -3%), None = rely on stop

# ═══════════════════════════════════════════════════════════════════════════════
# ALPACA PAPER SUBMISSION
# ═══════════════════════════════════════════════════════════════════════════════
SUBMIT_TO_ALPACA = True              # True = submit to Alpaca in live mode. Automatically overridden to False in replay.
ORDER_TYPE = "limit"                 # "limit" or "market"
TIME_IN_FORCE = "day"                # "day", "gtc", "ioc"

# -- Bracket orders (native Alpaca stop loss + take profit)
USE_BRACKET_ORDERS = True            # True = submit bracket order with SL/TP legs. False = simple limit order.
BRACKET_TP_TARGET = "T1"             # Which target for take-profit leg: "T1" or "T2"

# ═══════════════════════════════════════════════════════════════════════════════
# TRADE TRACKER SETTINGS
# ═══════════════════════════════════════════════════════════════════════════════
SAVE_TO_TRADE_TRACKER = True         # True = auto-write trades to Trade Tracker DB + send Telegram during intraday
                                     # Manual entries from Trade Tracker tab and "Track This Trade" still work.

# ═══════════════════════════════════════════════════════════════════════════════
# REPLAY-SPECIFIC SETTINGS
# ═══════════════════════════════════════════════════════════════════════════════
AUTO_PAPER_IN_REPLAY = True          # Automatically paper-trade tracked tickers in replay mode
PAPER_ENTRY_DELAY_MIN = 0            # Minutes after tracking starts before placing paper trade (0 = immediate)
LOG_ALL_CHECKS = False               # Print every price check to console (verbose debug)
