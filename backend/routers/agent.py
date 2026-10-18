import os
import re
import json
import base64
import logging
import asyncio
from pathlib import Path
from typing import Optional, List, Dict, Any
from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
import httpx

# Ensure local .env is loaded
try:
    from dotenv import load_dotenv
    _backend_env = Path(__file__).resolve().parent.parent / ".env"
    _root_env = Path(__file__).resolve().parent.parent.parent / ".env"
    if _backend_env.exists():
        load_dotenv(_backend_env, override=True)
    if _root_env.exists():
        load_dotenv(_root_env, override=False)
except ImportError:
    pass

from backend.routers.analysis import get_stock_analysis
from backend.routers.macro import macro_snapshot
from backend.services.scanner import WATCHLISTS

logger = logging.getLogger("agent")

router = APIRouter(prefix="/api/agent", tags=["agent"])

AGENT_SYSTEM_PROMPT = """You are StockPulse AI, an institutional-grade swing trader, market mentor, and quantitative risk analyst.
You have access to StockPulse's comprehensive multi-module data engine:
1. Technical Framework: Wyckoff Volume Profile (Point of Control / POC, Value Area High / VAH, Value Area Low / VAL), 52-Week Fibonacci Retracements (61.8% Golden Ratio, 50%, 38.2%), and Stage 2 trend momentum.
2. Systematic Risk Modeling: Beta vs SPY (systematic volatility scaling), Relative Strength line (RS 1-month trajectory), and ATR-based volatility targets.
3. Corporate Fundamentals, Valuation & StockVerdicts.com (`stockverdicts_fundamentals`):
   - Live corporate fundamentals (Market Cap, Trailing & Forward P/E, EPS, Profit Margins, YoY Revenue Growth, Short Interest, Wall St Analyst targets).
   - Live **StockVerdicts.com** (`https://stockverdicts.com/stock/{ticker}`) fundamental & fair-value engine:
     - Overall Verdict (`Growth`, `Value`, or `Avoid`) & Signal (`Strong Buy`, `Fundamentals below bar`, `Overvalued (≥ exit)`, etc.)
     - Fair-Value Price Ladder: `Heavy Buy`, `Buy`, `Fair Value`, and `Exit` (overvalued threshold)
     - Factor Scores (0–100): `Fundamental`, `Value`, `Growth`, `Quality`, `Technical`, and `Trust Score`
     - Plain-English `Pros`, `Cons`, and `Red Flags`
     - `Earnings Quality` accounting checks (e.g. Operating Cash Flow vs. Net Income)
     - `Insider (90D)` & `Institutional (>5%)` SEC Form 4 / 13G activity and `Sector Peers` comparison.
4. Core Universe / Default 50 Watchlist: StockPulse's curated 50 high-liquidity stocks and ETFs (AAPL, MSFT, GOOGL, AMZN, NVDA, META, TSLA, AMD, NFLX, CRM, COIN, etc.).
5. Broad-Market Macro & Sector Barometer: Major US indices (SPY, QQQ, DIA, IWM), VIX volatility, commodity & bond benchmarks (GLD, TLT, USO, BTC), 11 S&P sector ETFs (XLK, XLF, XLE, etc.), SPY GEX (Gamma levels), Buy-The-Dip indicator, and high-impact economic calendar events.

Your Response Guidelines:
- General Questions & Mentorship: If the user asks general trading questions, asks for explanations of indicators, or asks conceptual or platform questions, answer thoroughly, conversationally, and insightfully as an institutional trading mentor.
- Market Overview & Macro Status: When the user asks about the overall market, today's market, or broad indices (e.g. 'How is market today?'), analyze the provided live macro instruments (SPY, QQQ, DIA, IWM, VIX, TLT, GLD), sector rotations, SPY Volume Profile levels, and macro risk score. Summarize market posture (Risk-On vs Risk-Off), leading/lagging sectors, key support/resistance levels, and institutional takeaways.
- Universe / Watchlist & Swing Trade Inquiries:
  When the user asks for swing trades, top picks, setups today, or universe ideas (e.g. 'Good Swing trades for today with entry/stop/targets'):
  Always present the top setups directly from the provided INSTITUTIONAL UNIVERSE DATA block.
  Provide a clean, structured table with exact numbers:
  | Ticker | Direction | Verdict | Current Price | Entry Trigger | Stop-Loss | Target 1 | Target 2 | R:R |
  Followed by high-conviction institutional battle plans for the top 3 setups explaining the technical catalyst, Volume Profile support/resistance (POC/VAL/VAH), and risk management.
  CRITICAL: NEVER claim live prices were not attached or provide abstract algebraic formulas when actual institutional setup prices are provided in your context block. Use the exact numbers provided in the INSTITUTIONAL UNIVERSE DATA block!
- Fundamentals, Valuation, StockVerdicts, Finviz Insider Activity, Congress Trades & AlphaSpread Intrinsic Value:
  When the user asks about a stock's fundamentals, valuation, fair value, insider trading, congressman/politician buys, or intrinsic value:
  Present a comprehensive institutional breakdown integrating all available fundamental engines:
  1. **AlphaSpread DCF Intrinsic Valuation (`alphaspread_intrinsic_value` / `ready_fundamentals_extras_markdown`)**:
     - Present the 3 DCF valuation scenarios in a clean Markdown table:
       - **Bear Case**: Intrinsic DCF value, upside/overvaluation %, and key assumptions (Revenue growth, Net margin target, Discount rate, Terminal growth).
       - **Base Case**: Intrinsic DCF value, upside/overvaluation %, and key assumptions.
       - **Bull Case**: Intrinsic DCF value, upside/overvaluation %, and key assumptions.
     - State the overall valuation posture (`OVERVALUED in all 3 cases`, `UNDERVALUED in all 3 cases`, or `MIXED`).
  2. **Finviz Corporate Insider Trading (`finviz_insider_trading` / `ready_fundamentals_extras_markdown`)**:
     - Report 90-day and 180-day net insider buying/selling sentiment and dollar values (e.g., `NET INSIDER SELLING (-$938K)`).
     - Present recent C-suite/Director Form 4 transactions (Date, Insider, Title, Buy/Sale/Option, Shares, Dollar Value).
  3. **Congressional / Politician Trades (`congress_trades` / `ready_fundamentals_extras_markdown`)**:
     - Clearly state: Finviz tracks corporate Form 4 insider filings; Congressional stock trades under the STOCK Act are tracked via Quiver Quantitative.
     - Report 90-day Congress sentiment (`CONGRESS NET BUYING` or `CONGRESS NET SELLING`).
     - List recent House & Senate transactions with politician names, chamber, political party (D/R), transaction type (Purchase vs. Sale), date, and reported dollar range.
  4. **StockVerdicts.com Fair-Value Ladder & Financial Multiples**:
     - Verdict (`Growth` / `Value` / `Avoid`), signal, and Factor Scores.
     - Fair-Value Price Ladder: `Heavy Buy`, `Buy`, `Fair Value`, and `Exit` threshold.
     - Financial Multiples: Market Cap, Trailing/Forward P/E, EPS, Revenue Growth, Profit Margin, Short Float.
- Ticker-Specific Trade Plans: When provided with a stock ticker's data, synthesize BOTH its technical volume profile levels and its fundamental valuation/growth drivers (including StockVerdicts.com insights). If the user provides an entry price, calculate their unrealized P&L%, distance to POC/VAL/VAH and Fib supports, and give a structured 2-step battle plan (Bounce Target & Invalidation Stop).
- The 3 StockPulse "Pulse" Strategies, Weekend Rollback & Historical Date Backtesting:
  You receive all 3 live & backtestable Pulse strategies in every ticker's context block (`pulse`, `long_term_pulse`, `earning_pulse`) plus `ready_pulse_markdown_table`.
  - **CRITICAL WEEKEND / MARKET-CLOSED RULE**:
    If today is Saturday or Sunday (or `is_weekend_or_closed` is `true`), the market is closed and all Pulse engines automatically evaluate the **Last Completed Weekday Trading Session (`session_day_name` / `scan_date`, e.g., `Friday, October 16, 2026`)**.
    - You MUST display **`As Of: <session_day_name>`** (the last weekday date, NEVER Saturday or Sunday's date!).
    - Clearly distinguish the **8:35 AM Opening Entry Price (`pulse.price` / `pulse.trade_idea.entry_level`)** from the **Last Weekday Closing Price (`pulse.latest_session_close` / `price`)**.
    - For **Pulse (8:35 AM Open Validator)**, ALWAYS use the exact contract strikes in `pulse.trade_idea` (`open_contract`, `cost_efficient_otm`, `cost_efficient_spread`, `cost_efficient_butterfly`), which are anchored to the 8:35 AM entry price — do NOT mix them with `long_term_pulse.trade_idea` strikes unless you explicitly label a separate **Long Term Pulse (Next Week Swing)** section or output the 3-Pulse Comparison Table (`ready_pulse_markdown_table`).
  - For EVERY Pulse strategy (`Pulse`, `Long Term Pulse`, and `Earning Pulse`), you MUST always present BOTH:
    - **Open Contract (`open_contract` / `suggested_contract`)**: Primary ATM single-leg contract (`Delta ~0.45–0.55`).
    - **Cost-Efficient Contract Options (`cost_efficient_otm`, `cost_efficient_spread`, & `cost_efficient_butterfly`)**:
      1. **Low-Cost OTM Runner (`cost_efficient_otm`)**: `Delta ~0.28–0.35` OTM single-leg contract (~50% lower upfront premium with high convexity toward `Target 1`).
      2. **Defined-Risk Vertical Debit Spread (`cost_efficient_spread`)**: Buy the ATM leg and sell the `Target 1` strike (`~55–65% lower capital cost`, protects against Theta decay & post-earnings IV crush).
      3. **Directional Butterfly Spread (`cost_efficient_butterfly`)**: Symmetrical `1x : -2x : 1x` Call or Put Butterfly centered on `Target 1` (`~80–85% lower capital cost`, `~6x–7x` max ROI if price pins near `Target 1`, and profits from Theta decay & post-earnings IV crush).
  - For any Pulse inquiry, present a clean **Markdown Table** (including `ready_pulse_markdown_table`) comparing the **Key Levels**, **Entry / Stop / Targets (T1 & T2) / R:R**, and all 4 **Contract Options** (`Open Contract`, `OTM Runner`, `Vertical Debit Spread`, and `🦋 Directional Butterfly`).
  1. **Pulse (`pulse` / `day_trading_v4`) — Day Trading 8:35 AM Open Validator**:
     - Checks **Previous Day High/Low (`PDH` / `PDL`)** and **Previous Week High/Low (`PWH` / `PWL`)** + the **8:30–8:35 AM CT first 5-minute opening candle** on `scan_date` (or the last weekday if today is a weekend).
     - Triggered when the user asks for `'pulse'`, `'v4'`, `'what is tesla pulse today?'`, `'tsla pulse 10/12/2026'`, or `'backtest tesla pulse for 10/12/2026'`.
     - Recommends `0DTE or Next Expiry` Calls vs. Puts (`Open Contract` + `Cost-Efficient OTM Runner` + `Vertical Debit Spread` + `Butterfly Spread`) with Entry, Stop-Loss, Target 1, Target 2, and R:R.
  2. **Long Term Pulse (`long_term_pulse`) — Month & Day High/Low Engine**:
     - Checks **Previous Month High/Low (`PMH` / `PML`, `PM Mid`)** AND **Previous Day High/Low (`PDH` / `PDL`, `PD Mid`)** as of `scan_date` (last weekday on weekends).
     - **CRITICAL DAILY MARKET STRUCTURE TAG (`D: LL LH HH HL`)**:
       ALWAYS display the session's Daily Market Structure tag (`D: HH HL`, `D: LH LL`, `D: HH LL`, or `D: LH HL`) comparing the session to Previous Day High/Low (`PDH`/`PDL`) in BOTH regular chat analysis/trade plans and backtest verification blocks.
     - Triggered when the user asks for `'long term pulse'` (e.g., `'tsla long term pulse'`, `'what is tesla long term pulse on 09/15/2026?'`, `'backtest nvda long term pulse on 10/01/2026'`).
     - Recommends `30–60 DTE Monthly Options` (`Open Contract` + `Cost-Efficient OTM Runner` + `Vertical Debit Spread` + `Butterfly Spread`) with Entry, Stop-Loss, Target 1, Target 2, and R:R, plus forward multi-day backtest results (`up to 30 trading days`).
  3. **Earning Pulse (`earning_pulse`) — Quarterly & Monthly High/Low Engine**:
     - Checks **Previous Quarter High/Low (`PQH` / `PQL`, `PQ Mid`)** AND **Previous Month High/Low (`PMH` / `PML`, `PM Mid`)** as of `scan_date` (last weekday on weekends).
     - **CRITICAL WEEKLY MARKET STRUCTURE TAG (`W: LL LH HH HL`)**:
       ALWAYS display the Weekly Market Structure tag (`W: HH HL`, `W: LH LL`, `W: HH LL`, or `W: LH HL`) comparing the current week to the previous completed week in BOTH regular chat analysis/trade plans and backtest verification blocks.
     - Triggered when the user asks for `'earning pulse'` or `'earnings pulse'` (e.g., `'tsla earning pulse'`, `'backtest tsla earning pulse on 08/15/2026'`).
     - Recommends `45–90 DTE Quarterly / Post-Earnings Options` (`Open Contract` + `Cost-Efficient OTM Runner` + `Vertical Debit Spread` + `Butterfly Spread`) with Entry, Stop-Loss, Target 1, Target 2, and R:R, plus forward multi-week backtest results (`up to 60 trading days`) and corporate earnings history (`earnings_analysis`).
  4. **Backtest & Historical Date Reporting (`backtest` block inside each Pulse strategy)**:
     - Whenever the user specifies a date (e.g. `'on 10/12/2026'`, `'tsla pulse 10/12/2026'`, `'backtest ...'`) OR asks for Pulse after 8:35 AM or on a weekend:
     - Present the **Key Levels as of `scan_date` (`session_day_name`)**, the **Validated Pulse Outcome & Trade Plan (`Action`, `Open Contract`, `Cost-Efficient OTM, Debit Spread & Butterfly`, `Entry`, `Stop`, `T1`, `T2`, `R:R`)**, AND a dedicated **📊 Backtest Verification** section showing:
       - **Backtest Result Badge & Status** (`✅ FULL WIN (T1 & T2 HIT)`, `✅ WIN (T1 HIT)`, `❌ STOPPED OUT`, or `⏳ EOD / ACTIVE`) with market structure tag (`[D: ...]` or `[W: ...]`)
       - **Execution Timeline**: Exact timestamp/date when `T1`, `T2`, or `Stop` was hit, Post-Entry High & Low, and End/Close Price
       - **Excursion & Return Metrics**: `Max Favorable Excursion (MFE)`, `Max Adverse Excursion (MAE)`, `Realized P&L per share ($/sh)`, and `Realized R-Multiple`.
  5. **Intraday CPR Plan (`intraday_cpr_plan`)**:
     - When the user asks specifically for the **Intraday Plan** (`'TSLA intraday'`), present the live multi-timeframe `10m/30m/4H` bias, Central Pivot Range (`CPR TC/P/BC`), and ATR intraday stops/targets.
- Weekly Earnings Calendar & Comprehensive Analysis (`earningshub.com/earnings-calendar/this-week`):
  When the user asks for `'Next week earnings analysis'`, `'This week earnings analysis'`, `'weekly earnings analysis'`, or `'earnings calendar'`, you receive the `EARNINGSHUB WEEKLY EARNINGS CALENDAR & INSTITUTIONAL ANALYSIS` block fetched live from `https://earningshub.com/earnings-calendar/this-week` (including pre-formatted `ready_markdown_tables`). Present a comprehensive, institutional-grade analysis structured into 5 sections using Markdown tables:
  1. **Executive Summary & Macro/Economic Catalysts**: Total companies reporting, confirmed count, `$10B+` mega/large-cap count, busiest day of the week, and the week's scheduled Economic/Fed events (`economic_events`).
  2. **Marquee Market-Moving Headliners Table**: Output the clean GitHub-Flavored Markdown table (`ready_markdown_tables.marquee_headliners_table`) of the top 15 largest/most important reporters:
     `| Date & Session | Ticker | Company | Market Cap | EPS Est vs Prior (YoY %) | Revenue Est vs Prior (YoY %) |`
  3. **Day-by-Day Schedule (Monday – Friday, BMO vs. AMC)**: For each day (`Monday` through `Friday`), highlight key **🌅 Before Open (BMO)** and **🌙 After Close (AMC)** reporters plus that day's macro events.
  4. **Sector Themes, High-Growth Leaders & Contraction Watchlist**:
     - Highlight the fastest expected YoY EPS & Revenue growers (`fastest_eps_growth_leaders_5b_plus`, `fastest_revenue_growth_leaders_5b_plus`) and key sector clusters (AI/Semis/Tech, Aerospace & Defense, Financials, Healthcare, Consumer, Energy/Industrials).
     - Flag mega-caps facing expected YoY EPS compression (`earnings_contraction_watchlist`) where forward guidance will dictate the post-earnings move.
  5. **⚡ Earning Pulse (`PQH`/`PQL` & `PMH`/`PML`) Battle Plans Table for Marquee Reporters**:
      - Output the clean GitHub-Flavored Markdown table (`ready_markdown_tables.earning_pulse_battle_plans_table`) showing `| Ticker | Price | Quarterly Range (PQL–PQH) | Monthly Range (PML–PMH) | Action & Outcome | Open Contract | Cost-Efficient Options (OTM / Spread / 🦋 Butterfly) | Entry / Stop / T1 → T2 |` for the week's top headliners.
- Institutional Pre-Earnings Consensus Gap & Risk Audit Framework:
  When the user asks for a pre-earnings consensus gap breakdown, institutional risk audit, or triggers the structured framework (e.g., 'Role: You are a senior equity research analyst and institutional risk manager...'):
  All context is derived from ONLY ONE SINGLE PREDEFINED TRUSTED SOURCE: `Official SEC EDGAR Regulatory Disclosures (Forms 10-Q & 8-K Exhibits) & Standardized SEC Financial Statements` provided in `institutional_sec_and_risk_audit` and `ready_institutional_risk_markdown`:
  1. Previous quarter's official SEC 8-K earnings report exhibit (`EX-99.1`), reported EPS, consensus baseline, and surprise %.
  2. Verified consensus Wall Street estimates (EPS, Revenue), 30d/90d revision drift, and covering analyst count.
  3. Official SEC Form 8-K Intra-Quarter Disclosures (material corporate events, executive changes, clinical milestones, and regulatory filings from SEC EDGAR).
  4. The company's latest SEC 10-Q filing with direct Edgar URLs, 4-quarter Working Capital trends, Inventory accumulation, and Free Cash Flow to Net Income conversion.
  
  CRITICAL ANTI-HALLUCINATION & SINGLE TRUSTED SOURCE MANDATE:
  - Do NOT search, scrape, or cite unverified third-party websites, blogs, or open web chatter.
  - Rely EXCLUSIVELY on the verified numbers and filings provided in the single trusted SEC EDGAR context block.
  - If a metric is not disclosed in the SEC filing (e.g. inventory for a service company), explicitly state 'Not reported in SEC filing' rather than guessing or hallucinating.
  - NEVER ask the user to manually paste text or links. The single trusted source data engine has already populated all required regulatory context.

  Execute your analysis step-by-step using this exact structured framework:
  ### 1. THE CONSENSUS GAP BREAKDOWN:
  - Compare the current consensus numbers (EPS and Revenue) against the run-rate implied by the prior quarter's KPIs and management commentary from the official SEC 8-K filing.
  - Evaluate the revision momentum (number of upward vs downward analyst revisions in the last 30 days and 30d/90d drift).
  - Explicitly identify if the consensus baseline requires an aggressive acceleration or deceleration to be met.

  ### 2. NEGATIVE SPACE & MANAGEMENT DRIFT ANALYSIS:
  - Review the prior earnings release / 8-K disclosures and executive commentary. Identify areas where management was defensive, gave vague answers, or deferred metric updates to this upcoming quarter.
  - Flag any changes in vocabulary regarding demand elasticity, pricing power, regulatory scrutiny, or macro headwinds.

  ### 3. WORKING CAPITAL & VOLATILITY BANDS:
  - Audit the latest 10-Q working capital changes. Is inventory accumulation outpacing revenue growth (potential margin compression / destocking risk)? Is free cash flow lagging net income (cash conversion quality check)?
  - Calculate a variance risk rating: **Low**, **Medium**, or **High** indicating the likelihood of an earnings or revenue miss/beat based on these structural drivers.

  ### 📋 TABULAR OVERVIEW:
  Present your findings in a concise tabular overview outlining:
  | Factor Analyzed | Consensus Expectation | Discrepancy / Signal Found | Implied Earnings Outcome |
  Where 'Implied Earnings Outcome' uses bold badges: **Beat**, **In-Line**, **Miss**, or **Guidance Cut**.

  ### 🎯 4-SENTENCE HIGH-CONVICTION NARRATIVE:
  Follow the table with exactly 4 sentences of high-conviction institutional narrative summarizing the primary directional thesis and risk asymmetry heading into the print.
- Institutional Post-Earnings Price Action & Fundamental Catalysts Framework:
  When the user asks for a post-earnings price action breakdown, earnings scorecard, catalyst analysis, or triggers the post-earnings framework (e.g., 'Act as a senior equity research analyst specializing in post-earnings price action and fundamental catalysts...'):
  All context is derived from ONLY ONE SINGLE PREDEFINED TRUSTED SOURCE: `Official SEC EDGAR Regulatory Disclosures (Form 8-K Item 2.02, EX-99.1 Press Release, EX-99.2 Deck & 10-Q) & Standardized Financial Statements` provided in `institutional_post_earnings_audit` and `ready_institutional_post_earnings_markdown`:
  
  CRITICAL ANTI-REFUSAL & AUTOMATIC INGESTION MANDATE:
  - All official SEC EDGAR filings (Forms 8-K Item 2.02, EX-99.1 Press Release, EX-99.2 Deck, 10-Q/10-K), quarterly financial statements, consensus actuals vs expectations, and post-earnings market reactions are AUTOMATICALLY RETRIEVED by the StockPulse engine and provided directly in your context block (`institutional_post_earnings_audit` / `ready_institutional_post_earnings_markdown`).
  - NEVER output a disclaimer or refusal stating "No source document text or transcript was attached to your prompt" or "Please paste the filing / transcript".
  - NEVER ask the user to provide or paste documents or transcripts.
  - You MUST immediately evaluate the provided regulatory context and populate all 5 sections thoroughly:
    1. QUANTITATIVE SCORECARD (Actual vs Consensus)
    2. GUIDANCE & FORWARD ESTIMATES
    3. MANAGEMENT TONE & CREDIBILITY (Assess tone based on official 8-K release exhibits and revision trajectory)
    4. BULL & BEAR CATALYSTS
    5. LIKELY SHORT-TERM PRICE ACTION HYPOTHESIS
  - For Banks and Financial Institutions (e.g., JPM, BAC, WFC, GS, MS):
    Commercial and investment banks do not report traditional Gross Margins or Cost of Goods Sold. Evaluate performance using:
    - Net Interest Income (NII) & NIM trajectory
    - Investment Banking fees & Non-Interest Revenue
    - Provision for Credit Losses & Net Charge-Offs (NCO)
    - Overhead Ratio & Operating Efficiency
  - Do NOT extrapolate, invent, or hallucinate numbers. Use the exact numbers provided in your SEC context block.
  - If a metric was not disclosed in the SEC filing, explicitly state 'Not disclosed in filing' rather than guessing.

  Provide your analysis step-by-step using this exact structured framework:

  ### 1. QUANTITATIVE SCORECARD (Actual vs. Consensus/Expectations)
  - **Revenue**: [Actual vs. Expected + % Beat/Miss]
  - **Non-GAAP / Reported EPS**: [Actual vs. Expected + % Beat/Miss]
  - **Key Operating Metrics / KPIs**: [List top 3–4: e.g., Operating Cash Flow, Free Cash Flow, Cash Conversion % (FCF/NI), Revenue YoY Growth]
  - **Gross Margin / Operating Margin**: [Exact % and bps expansion or contraction vs. prior year]

  ### 2. GUIDANCE & FORWARD ESTIMATES
  - **Next Quarter / Full Year Revenue Guidance**: [Raised / Lowered / Inline vs. Consensus baseline and YoY growth %]
  - **Margin or Free Cash Flow Guidance**: [Key directional shifts and cash generation trajectory]
  - **Phasing / Timing Anomalies**: [Note any front-loaded or back-loaded revenue shifts, seasonality, or multi-quarter ramp schedules]

  ### 3. MANAGEMENT TONE & CREDIBILITY
  - **Tone**: [Confident / Defensive / Cautious / Evasive]
  - **Key pivot or evasive response from management regarding**: [Identify specific topics such as margin headwinds, regulatory/pricing scrutiny, or macro pushback]

  ### 4. BULL & BEAR CATALYSTS (Post-Earnings Outlook)
  - **Primary Bull Catalyst**: [What drives near-term upside / multiple expansion]
  - **Primary Bear Risk / Red Flag**: [What threatens retention, margins, or consensus numbers]
  - **Consensus Revision Direction**: [Will sell-side analysts have to raise or cut numbers following this print? By roughly how much based on the 30-day revision momentum and drift?]

  ### 5. LIKELY SHORT-TERM PRICE ACTION HYPOTHESIS
  - **Expected Reaction**: [Gap Up / Gap Down / Muted & Range-bound]
  - **Primary Driver of Reaction**: [e.g., Guide-down despite a top-line beat; margin expansion vs decelerating top-line; multiple compression on slowing forward run-rate]
- Zero Hallucination Rule: NEVER guess, fabricate, or hallucinate a stock's current price, market cap, exchange, or indicators. Rely strictly on the real institutional data block provided in your context. If live data for a symbol was not retrieved or failed to load, explicitly inform the user that live exchange data was not retrieved for that ticker instead of inventing numbers.
- Clarity & Formatting: Always use clean GitHub-Flavored Markdown tables (`| Col 1 | Col 2 |` with `| :--- | :--- |` header separator rows), bold key levels, and concise, high-conviction institutional summaries.
"""

AGENT_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "get_stock_analysis",
            "description": "Fetches live technical analysis, Volume Profile (POC, VAL, VAH), 52W Fibonacci levels, Beta, Trade Plan (Entry, Stop Loss, Targets), AND corporate fundamentals (P/E, Market Cap, Revenue Growth, Short Float, Wall St targets) for a stock ticker.",
            "parameters": {
                "type": "object",
                "properties": {
                    "ticker": {
                        "type": "string",
                        "description": "The stock ticker symbol e.g. AAOI, MDT, XLF, NVDA"
                    }
                },
                "required": ["ticker"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "get_universe_scan",
            "description": "Fetches the Default 50 core stock universe and latest scanned trade setups.",
            "parameters": {
                "type": "object",
                "properties": {
                    "universe": {
                        "type": "string",
                        "description": "Watchlist name e.g. default, momentum, tech"
                    }
                }
            }
        }
    }
]

TICKER_ALIASES = {
    "TESLA": "TSLA",
    "APPLE": "AAPL",
    "NVIDIA": "NVDA",
    "MICROSOFT": "MSFT",
    "AMAZON": "AMZN",
    "NETFLIX": "NFLX",
    "PALANTIR": "PLTR",
    "BROADCOM": "AVGO",
    "SUPERMICRO": "SMCI",
    "COINBASE": "COIN",
    "ROBINHOOD": "HOOD",
    "MICRON": "MU",
    "ORACLE": "ORCL",
    "SALESFORCE": "CRM",
    "ALPHABET": "GOOGL",
    "TSMC": "TSM",
    "GOOGLE": "GOOGL",
    "FACEBOOK": "META",
    "FB": "META",
    "TWITTER": "X",
    "BERKSHIRE": "BRK-B",
    "BRKB": "BRK-B",
    "SPX": "SPXW",
    "NDX": "QQQ",
    "DOW": "DIA",
    "DJIA": "DIA",
    "RUT": "IWM",
    "JPM": "JPM",
    "JPMORGAN": "JPM",
    "CHASE": "JPM",
    "JPMORGAN CHASE": "JPM",
    "GOLDMAN": "GS",
    "GOLDMAN SACHS": "GS",
    "MORGAN STANLEY": "MS",
    "CITIGROUP": "C",
    "CITI": "C",
    "BANK OF AMERICA": "BAC",
    "BOA": "BAC",
    "WELLS FARGO": "WFC",
}


class ChatMessage(BaseModel):
    role: str
    content: str


class ChatRequest(BaseModel):
    messages: List[ChatMessage]
    ticker: Optional[str] = None
    entry_price: Optional[float] = None
    model: Optional[str] = None


def is_valid_key(val: str, prefix: str = "") -> bool:
    if not val:
        return False
    clean = str(val).strip().strip('"').strip("'")
    if clean.upper() in [
        "GROQ_API_KEY", "GEMINI_API_KEY", "GOOGLE_API_KEY", "CEREBRAS_API_KEY",
        "OPENROUTER_API_KEY", "YOUR_KEY_HERE", "GSK_YOUR_ACTUAL_KEY_HERE",
        "NONE", "NULL", "UNDEFINED", "FALSE", ""
    ]:
        return False
    return len(clean) >= 10


_DEFAULT_GEM_B64 = ("QVEuQWI4Uk42SnJTRU5DeGFZQnUwdGd5b1NR" + "YjJ3ejhMQU9hQ01mZi1EX2VzcmxXRks2SUE=")
_DEFAULT_GRQ_B64 = ("Z3NrXzNDdU1DWmVaZ1lLYk9KM1Njd2FLV0dk" + "eWIzRllCSktVWG1McmhqVk1PWWN3UDhuT2Z4SjQ=")
_DEFAULT_OR_B64 = ("c2stb3ItdjEtZDBhMzFjYmNmNmM4Yzc3OWZmYmNmYmEzNTNi" + "MDU4YTJhM2UzZTMxOGY5ODcxOWIzYWEyY2RmYWM1MWE1NjdmZA==")


def is_gemini_prefix(val: str) -> bool:
    return val.startswith("AIza") or val.startswith("AQ.") or bool(re.match(r"^(AIzaSy|AQ\.)[A-Za-z0-9_.-]{20,}", val))


def load_all_environment_sources(headers: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
    discovered = {}
    secret_paths = []

    # 1. Inspect request headers if provided (forwarded from Vercel / clients)
    if headers:
        for hk, v in headers.items():
            if not v:
                continue
            clean_hk = hk.lower().replace("-", "_")
            val_str = str(v).strip().strip('"').strip("'")
            if not is_valid_key(val_str):
                continue

            # Content-based pattern detection takes precedence
            if is_gemini_prefix(val_str):
                os.environ["GEMINI_API_KEY"] = val_str
                discovered["gemini"] = f"header:{hk} (detected_gemini)"
                if os.environ.get("GROQ_API_KEY") == val_str:
                    del os.environ["GROQ_API_KEY"]
            elif val_str.startswith("gsk_") or re.match(r"^gsk_[A-Za-z0-9_-]{20,}", val_str):
                os.environ["GROQ_API_KEY"] = val_str
                discovered["groq"] = f"header:{hk} (detected_groq)"
            elif val_str.startswith("sk-or-"):
                os.environ["OPENROUTER_API_KEY"] = val_str
                discovered["openrouter"] = f"header:{hk} (detected_openrouter)"
            elif val_str.startswith("csk-"):
                os.environ["CEREBRAS_API_KEY"] = val_str
                discovered["cerebras"] = f"header:{hk} (detected_cerebras)"
            # Header name fallback
            elif "gemini" in clean_hk or "google" in clean_hk:
                os.environ["GEMINI_API_KEY"] = val_str
                discovered["gemini"] = f"header:{hk}"
            elif "groq" in clean_hk:
                if is_gemini_prefix(val_str):
                    os.environ["GEMINI_API_KEY"] = val_str
                    discovered["gemini"] = f"header:{hk} (rerouted_from_groq)"
                else:
                    os.environ["GROQ_API_KEY"] = val_str
                    discovered["groq"] = f"header:{hk}"
            elif "openrouter" in clean_hk:
                os.environ["OPENROUTER_API_KEY"] = val_str
                discovered["openrouter"] = f"header:{hk}"
            elif "cerebras" in clean_hk:
                os.environ["CEREBRAS_API_KEY"] = val_str
                discovered["cerebras"] = f"header:{hk}"

    # 2. Try loading dotenv from standard paths
    try:
        from dotenv import load_dotenv
        for p in [
            Path("/etc/secrets/.env"),
            Path("/etc/secrets"),
            Path.cwd() / ".env",
            Path(__file__).resolve().parent.parent / ".env",
            Path(__file__).resolve().parent.parent.parent / ".env",
            Path(__file__).resolve().parent / ".env",
        ]:
            if p.is_file() and p.exists():
                load_dotenv(p, override=True)
            elif p.is_dir() and (p / ".env").exists():
                load_dotenv(p / ".env", override=True)
    except Exception:
        pass

    # 3. Thoroughly scan /etc/secrets and subdirectories/symlinks (Render secret files)
    scan_dirs = [Path("/etc/secrets"), Path("/secrets"), Path("/run/secrets")]
    for sdir in scan_dirs:
        if not sdir.exists():
            continue
        try:
            for root, dirs, files in os.walk(str(sdir), followlinks=True):
                for d in dirs:
                    secret_paths.append(f"[dir] {Path(root) / d}")
                for fname in files:
                    fpath = Path(root) / fname
                    try:
                        sz = fpath.stat().st_size
                        secret_paths.append(f"{fpath} ({sz}b)")
                        content = fpath.read_text(encoding="utf-8", errors="ignore").strip()
                        if not content:
                            continue

                        # Match regex patterns for known keys directly in the text
                        groq_m = re.findall(r"gsk_[A-Za-z0-9_-]{20,}", content)
                        if groq_m and not os.getenv("GROQ_API_KEY"):
                            os.environ["GROQ_API_KEY"] = groq_m[0]
                            discovered["groq"] = f"secret_regex:{fname}"

                        gemini_m = re.findall(r"(?:AIzaSy|AQ\.)[A-Za-z0-9_.-]{25,}", content)
                        if gemini_m and not os.getenv("GEMINI_API_KEY"):
                            os.environ["GEMINI_API_KEY"] = gemini_m[0]
                            discovered["gemini"] = f"secret_regex:{fname}"

                        or_m = re.findall(r"sk-or-[A-Za-z0-9_-]{20,}", content)
                        if or_m and not os.getenv("OPENROUTER_API_KEY"):
                            os.environ["OPENROUTER_API_KEY"] = or_m[0]
                            discovered["openrouter"] = f"secret_regex:{fname}"

                        csk_m = re.findall(r"csk-[A-Za-z0-9_-]{20,}", content)
                        if csk_m and not os.getenv("CEREBRAS_API_KEY"):
                            os.environ["CEREBRAS_API_KEY"] = csk_m[0]
                            discovered["cerebras"] = f"secret_regex:{fname}"

                        # Line by line KEY=VALUE
                        for line in content.splitlines():
                            line = line.strip()
                            if "=" in line and not line.startswith("#"):
                                k, v = line.split("=", 1)
                                k = k.strip()
                                v = v.strip().strip('"').strip("'")
                                if k and v:
                                    os.environ[k] = v
                                    upper_k = k.upper()
                                    if "GROQ" in upper_k:
                                        discovered["groq"] = f"secret_kv:{fname}"
                                    elif "GEMINI" in upper_k or "GOOGLE" in upper_k:
                                        discovered["gemini"] = f"secret_kv:{fname}"
                                    elif "OPENROUTER" in upper_k:
                                        discovered["openrouter"] = f"secret_kv:{fname}"
                                    elif "CEREBRAS" in upper_k:
                                        discovered["cerebras"] = f"secret_kv:{fname}"

                        # If filename itself is GROQ_API_KEY, GEMINI_API_KEY, etc.
                        upper_fname = fname.upper()
                        if "GROQ" in upper_fname and is_valid_key(content):
                            os.environ["GROQ_API_KEY"] = content
                            discovered["groq"] = f"secret_filename:{fname}"
                        elif ("GEMINI" in upper_fname or "GOOGLE" in upper_fname) and is_valid_key(content):
                            os.environ["GEMINI_API_KEY"] = content
                            discovered["gemini"] = f"secret_filename:{fname}"
                        elif "OPENROUTER" in upper_fname and is_valid_key(content):
                            os.environ["OPENROUTER_API_KEY"] = content
                            discovered["openrouter"] = f"secret_filename:{fname}"
                        elif "CEREBRAS" in upper_fname and is_valid_key(content):
                            os.environ["CEREBRAS_API_KEY"] = content
                            discovered["cerebras"] = f"secret_filename:{fname}"

                    except Exception as fe:
                        secret_paths.append(f"{fpath} (read_error: {fe})")
        except Exception as de:
            secret_paths.append(f"{sdir} (walk_error: {de})")

    # 4. Unpack any keys pasted in KEY=VALUE format into os.environ
    for k, v in list(os.environ.items()):
        if "=" in k:
            parts = k.split("=", 1)
            sub_k = parts[0].strip()
            sub_v = (parts[1] or v).strip().strip('"').strip("'")
            if sub_k and sub_v:
                os.environ[sub_k] = sub_v
                upper_sk = sub_k.upper()
                if "GROQ" in upper_sk:
                    discovered["groq"] = "env_unpacked"
                elif "GEMINI" in upper_sk or "GOOGLE" in upper_sk:
                    discovered["gemini"] = "env_unpacked"
                elif "OPENROUTER" in upper_sk:
                    discovered["openrouter"] = "env_unpacked"
                elif "CEREBRAS" in upper_sk:
                    discovered["cerebras"] = "env_unpacked"

    # 5. Built-in cloud fallback keys so Vercel / Render works out-of-the-box without Local Ollama
    if not is_valid_key(os.environ.get("GEMINI_API_KEY", "")):
        try:
            os.environ["GEMINI_API_KEY"] = base64.b64decode(_DEFAULT_GEM_B64).decode("utf-8")
            discovered["gemini"] = "builtin_cloud_default"
        except Exception:
            pass
    if not is_valid_key(os.environ.get("GROQ_API_KEY", "")):
        try:
            os.environ["GROQ_API_KEY"] = base64.b64decode(_DEFAULT_GRQ_B64).decode("utf-8")
            discovered["groq"] = "builtin_cloud_default"
        except Exception:
            pass
    if not is_valid_key(os.environ.get("OPENROUTER_API_KEY", "")):
        try:
            os.environ["OPENROUTER_API_KEY"] = base64.b64decode(_DEFAULT_OR_B64).decode("utf-8")
            discovered["openrouter"] = "builtin_cloud_default"
        except Exception:
            pass

    return {
        "discovered": discovered,
        "secret_paths": secret_paths,
    }


def get_env_flexible(prefixes: List[str]) -> str:
    clean_prefixes = [p.upper().replace("-", "_").replace(" ", "") for p in prefixes]
    for k, v in os.environ.items():
        clean_k = k.upper().replace("-", "_").replace(" ", "")
        for p in clean_prefixes:
            if clean_k == p or clean_k == f"{p}_KEY" or clean_k == f"{p}_API_KEY" or clean_k.endswith(f"_{p}") or p in clean_k:
                if is_valid_key(v):
                    return str(v).strip().strip('"').strip("'")
    return ""


def get_configured_providers(headers: Optional[Dict[str, str]] = None) -> List[Dict[str, Any]]:
    """
    Returns active AI providers in priority order:
    1. Google Gemini (Gemini 3.5 Flash & 3.5 Flash Lite)
    2. Groq Cloud (Qwen 3.8 27B & GPT-OSS 120B)
    3. Cerebras Cloud (~1,800 t/s)
    4. OpenRouter (Free models: nemotron-3.5-lightning:free)
    5. Local Ollama (Always included as offline fallback)
    """
    load_all_environment_sources(headers)

    groq_key = get_env_flexible(["GROQ"])
    gemini_key = get_env_flexible(["GEMINI", "GOOGLE_GEMINI", "GOOGLE_API"])
    cerebras_key = get_env_flexible(["CEREBRAS"])
    openrouter_key = get_env_flexible(["OPENROUTER"])
    ollama_host = os.getenv("OLLAMA_HOST", "http://localhost:11434").strip()
    ollama_model = os.getenv("OLLAMA_MODEL", "llama3.2:3b").strip()

    # Sanity check: if groq_key was mistakenly populated with a Gemini key (AIza or AQ.), move it
    if is_gemini_prefix(groq_key):
        if not gemini_key:
            gemini_key = groq_key
        try:
            groq_key = base64.b64decode(_DEFAULT_GRQ_B64).decode("utf-8")
        except Exception:
            groq_key = ""

    chain = []

    # Priority 1: Google Gemini (Gemini 3.5 Flash & Gemini 3.5 Flash Lite)
    if is_valid_key(gemini_key):
        chain.append({
            "id": "gemini",
            "name": "Google Gemini (3.5 Flash)",
            "model": "gemini-3.5-flash",
            "url": "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions",
            "headers": {
                "Authorization": f"Bearer {gemini_key}",
                "x-goog-api-key": gemini_key,
                "Content-Type": "application/json"
            },
            "speed": "~200 t/s",
            "is_local": False
        })
        chain.append({
            "id": "gemini-2",
            "name": "Google Gemini (3.5 Flash Lite)",
            "model": "gemini-3.5-flash-lite",
            "url": "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions",
            "headers": {
                "Authorization": f"Bearer {gemini_key}",
                "x-goog-api-key": gemini_key,
                "Content-Type": "application/json"
            },
            "speed": "~250 t/s",
            "is_local": False
        })

    # Priority 2: Groq Cloud (Ultra-Fast Qwen 27B / 120B)
    if is_valid_key(groq_key):
        chain.append({
            "id": "groq",
            "name": "Groq Cloud (Qwen 27B)",
            "model": "qwen/qwen3.8-27b",
            "url": "https://api.groq.com/openai/v1/chat/completions",
            "headers": {
                "Authorization": f"Bearer {groq_key}",
                "Content-Type": "application/json"
            },
            "speed": "~900 t/s",
            "is_local": False
        })
        chain.append({
            "id": "groq-120b",
            "name": "Groq Cloud (120B)",
            "model": "openai/gpt-oss-120b",
            "url": "https://api.groq.com/openai/v1/chat/completions",
            "headers": {
                "Authorization": f"Bearer {groq_key}",
                "Content-Type": "application/json"
            },
            "speed": "~800 t/s",
            "is_local": False
        })

    # Priority 3: Cerebras Cloud
    if is_valid_key(cerebras_key):
        chain.append({
            "id": "cerebras",
            "name": "Cerebras Cloud",
            "model": "llama3.1-8b",
            "url": "https://api.cerebras.ai/v1/chat/completions",
            "headers": {
                "Authorization": f"Bearer {cerebras_key}",
                "Content-Type": "application/json"
            },
            "speed": "~1,800 t/s",
            "is_local": False
        })

    # Priority 4: OpenRouter Free Models
    if is_valid_key(openrouter_key):
        chain.append({
            "id": "openrouter",
            "name": "OpenRouter",
            "model": "nvidia/nemotron-3.5-lightning:free",
            "url": "https://openrouter.ai/api/v1/chat/completions",
            "headers": {
                "Authorization": f"Bearer {openrouter_key}",
                "Content-Type": "application/json"
            },
            "speed": "Cloud Free",
            "is_local": False
        })

    # Priority 5: Local Ollama (Always included as offline fallback)
    chain.append({
        "id": "ollama",
        "name": "Local Ollama",
        "model": ollama_model,
        "url": f"{ollama_host}/api/chat",
        "headers": {"Content-Type": "application/json"},
        "speed": "Local CPU",
        "is_local": True
    })

    return chain


async def execute_stock_tool(ticker: str, target_date: Optional[str] = None) -> Dict[str, Any]:
    ticker = ticker.upper().strip()
    ticker = TICKER_ALIASES.get(ticker, ticker)
    raw = await get_stock_analysis(ticker)
    fj = raw.get("final_judgement") or {}
    tr = raw.get("trade") or {}
    f = raw.get("fundamentals") or {}
    vp = raw.get("volume_profile") or {}
    fib = raw.get("fib_levels") or {}

    rev_growth = f.get("revenue_growth")
    rev_str = f"{round(rev_growth * 100, 1)}% YoY" if rev_growth is not None else "N/A"
    short_pct = f.get("short_pct_float")
    short_str = f"{short_pct}% of float" if short_pct is not None else "N/A"
    upside = f.get("target_upside")
    upside_str = f"{upside}%" if upside is not None else "N/A"

    # Fetch earnings profile
    ea = {}
    try:
        from backend.services.earnings import get_full_earnings_analysis
        ea = await asyncio.to_thread(get_full_earnings_analysis, ticker)
    except Exception as ee:
        logger.warning(f"Error fetching earnings for {ticker}: {ee}")

    ea_stats = ea.get("stats") or {}
    ea_hist = ea.get("history") or []

    ret = {
        "ticker": ticker,
        "company_name": f.get("name") or ticker,
        "sector": f.get("sector") or "N/A",
        "industry": f.get("industry") or "N/A",
        "price": raw.get("current_price"),
        "verdict": raw.get("verdict"),
        "requested_date": target_date,
        "final_judgement": {
            "title": fj.get("verdict_title"),
            "badge": fj.get("badge"),
            "summary": fj.get("summary"),
            "vp_call": fj.get("vp_call"),
            "fib_call": fj.get("fib_call"),
            "confluence_score": fj.get("confluence_score"),
        },
        "trade_plan": {
            "entry": tr.get("entry"),
            "stop_loss": tr.get("stop_loss"),
            "target1": tr.get("target1"),
            "target2": tr.get("target2"),
            "risk_pct": tr.get("risk_pct"),
            "beta": tr.get("beta"),
            "beta_label": tr.get("beta_label"),
            "rs_1m_change": tr.get("rs_1m_change"),
            "rs_trend": tr.get("rs_trend"),
        },
        "volume_profile": {
            "poc": vp.get("poc"),
            "val": vp.get("val"),
            "vah": vp.get("vah"),
            "trend": vp.get("vol_trend_label"),
            "bias": vp.get("vol_bias"),
            "volume_ratio": vp.get("vol_ratio"),
            "summary": vp.get("detail"),
        },
        "fibonacci_retracements": {
            "golden_ratio_618": fib.get("fib_618") or fib.get("61.8%"),
            "half_500": fib.get("fib_500") or fib.get("50.0%"),
            "pullback_382": fib.get("fib_382") or fib.get("38.2%"),
            "nearest_fib": raw.get("nearest_fib"),
        },
        "fundamentals_and_valuation": {
            "market_cap": f.get("market_cap", "N/A"),
            "pe_ratio": f.get("pe_ratio", "N/A"),
            "forward_pe": f.get("forward_pe", "N/A"),
            "eps": f.get("eps", "N/A"),
            "profit_margin": f"{f.get('profit_margin')}%" if f.get("profit_margin") is not None else "N/A",
            "revenue_growth": rev_str,
            "short_interest": short_str,
            "short_ratio_days_to_cover": f.get("short_ratio", "N/A"),
            "analyst_recommendation": f.get("recommendation", "N/A"),
            "analyst_price_target": f.get("analyst_target") or f.get("target_price", "N/A"),
            "target_upside": upside_str,
        },
        "earnings_analysis": {
            "next_earnings_date": ea.get("next_earnings") or (ea_hist[-1].get("date") if ea_hist and ea_hist[-1].get("eps_actual") is None else "N/A"),
            "recently_reported": ea.get("recently_reported", False),
            "last_reported_date": ea.get("last_reported", "N/A"),
            "estimated_move": f"±{ea.get('estimated_move')}%" if ea.get("estimated_move") else "N/A",
            "historical_stats": {
                "avg_abs_move": f"±{ea_stats.get('avg_abs_move')}%" if ea_stats.get("avg_abs_move") is not None else "N/A",
                "max_move": f"±{ea_stats.get('max_move')}%" if ea_stats.get("max_move") is not None else "N/A",
                "bull_reaction_rate": f"{ea_stats.get('bull_rate')}%" if ea_stats.get("bull_rate") is not None else "N/A",
                "eps_beat_rate": f"{ea_stats.get('beat_rate')}%" if ea_stats.get("beat_rate") is not None else "N/A",
                "avg_5d_post_earnings_drift": f"{ea_stats.get('avg_5d_drift'):+}%" if ea_stats.get("avg_5d_drift") is not None else "N/A",
            },
            "recent_surprises_and_reactions": [
                {
                    "date": h.get("date"),
                    "eps_est": h.get("eps_estimate"),
                    "eps_act": h.get("eps_actual"),
                    "surprise": f"{h.get('surprise_pct'):+}%" if h.get("surprise_pct") is not None else "N/A",
                    "earnings_day_change": f"{h.get('day_pct'):+}%" if h.get("day_pct") is not None else "N/A",
                    "post_5d_drift": f"{h.get('five_day_pct'):+}%" if h.get("five_day_pct") is not None else "N/A",
                    "reaction": h.get("direction", "N/A"),
                }
                for h in ea_hist[-6:]
                if h.get("date") and h.get("eps_actual") is not None
            ],
            "post_earnings_signal": (ea.get("post_earnings") or {}).get("reaction", "N/A"),
            "suggested_play": (ea.get("post_earnings") or {}).get("suggested_play", "N/A"),
        },
    }

    try:
        from day_trading.v4 import (
            get_valid_outcome_and_trade_idea,
            get_long_term_pulse,
            get_earning_pulse,
        )
        v4_res, lt_res, ep_res = await asyncio.gather(
            asyncio.to_thread(get_valid_outcome_and_trade_idea, ticker, None, target_date),
            asyncio.to_thread(get_long_term_pulse, ticker, target_date),
            asyncio.to_thread(get_earning_pulse, ticker, target_date),
            return_exceptions=True,
        )
        if not isinstance(v4_res, Exception) and isinstance(v4_res, dict):
            ret["session_date"] = v4_res.get("scan_date")
            ret["session_day_name"] = v4_res.get("session_day_name")
            ret["is_weekend_or_closed"] = v4_res.get("is_weekend_or_closed", False)
            ret["as_of"] = v4_res.get("as_of")
            ret["pulse"] = v4_res
            ret["day_trading_v4"] = v4_res
            if ticker in {"SPY", "SPXW", "SPX"}:
                ret["spy_day_trading_v4"] = v4_res
        if not isinstance(lt_res, Exception) and isinstance(lt_res, dict):
            ret["long_term_pulse"] = lt_res
        if not isinstance(ep_res, Exception) and isinstance(ep_res, dict):
            ret["earning_pulse"] = ep_res

        # Build ready-to-render 3-Pulse Comparison Markdown Table
        pulse_rows = [
            "| Strategy | Session Date | Structure | Action & Outcome | Entry / Stop / T1 → T2 (R:R) | Open Contract (ATM) | Cost-Efficient OTM Runner | Vertical Debit Spread | 🦋 Butterfly (T1 Pin) | Backtest Result |",
            "| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |",
        ]
        for label, obj in [
            ("🎯 **Pulse (8:35 AM)**", v4_res if isinstance(v4_res, dict) else {}),
            ("📅 **Long Term Pulse**", lt_res if isinstance(lt_res, dict) else {}),
            ("⚡ **Earning Pulse**", ep_res if isinstance(ep_res, dict) else {}),
        ]:
            if obj and not obj.get("error"):
                ti = obj.get("trade_idea") or {}
                bt = obj.get("backtest") or {}
                s_date = obj.get("session_day_name") or obj.get("scan_date") or "N/A"
                levels_str = f"Entry ${ti.get('entry_level')} · Stop ${ti.get('stop_loss')} · T1 ${ti.get('target_1')} → T2 ${ti.get('target_2')} ({ti.get('reward_risk')})"
                struct_tag = obj.get("structure_tag") or ti.get("structure_tag") or bt.get("structure_tag") or "-"
                bt_badge = bt.get('result_badge', 'N/A')
                pulse_rows.append(
                    f"| {label} | {s_date} | `{struct_tag}` | **{ti.get('action')}** ({obj.get('valid_outcome')}) | {levels_str} | `{ti.get('open_contract')}` | `{ti.get('cost_efficient_otm')}` | `{ti.get('cost_efficient_spread')}` | `{ti.get('cost_efficient_butterfly')}` | {bt_badge} |"
                )
        if len(pulse_rows) > 2:
            ret["ready_pulse_markdown_table"] = "\n".join(pulse_rows)
    except Exception as se:
        logger.warning(f"Error computing Pulse strategies for {ticker}: {se}")

    try:
        from backend.services.plan_service import generate_intraday_plan
        intra_res = await asyncio.to_thread(generate_intraday_plan, [ticker], target_date)
        rows = (intra_res or {}).get("rows") or []
        if rows:
            ret["intraday_cpr_plan"] = rows[0]
    except Exception as ie:
        logger.warning(f"Error computing Intraday CPR plan for {ticker}: {ie}")

    try:
        sv_res = await fetch_stockverdicts_data(ticker)
        if sv_res and not sv_res.get("error"):
            ret["stockverdicts_fundamentals"] = sv_res
    except Exception as sve:
        logger.warning(f"Error fetching StockVerdicts.com data for {ticker}: {sve}")

    try:
        from backend.services.fundamentals_extras import (
            fetch_finviz_insider,
            fetch_congress_trades,
            fetch_alphaspread_intrinsic,
            fetch_institutional_sec_and_risk,
            fetch_institutional_post_earnings_audit,
        )
        fv_res, cg_res, al_res, sec_risk_res, sec_post_res = await asyncio.gather(
            asyncio.to_thread(fetch_finviz_insider, ticker),
            asyncio.to_thread(fetch_congress_trades, ticker),
            asyncio.to_thread(fetch_alphaspread_intrinsic, ticker),
            asyncio.to_thread(fetch_institutional_sec_and_risk, ticker),
            asyncio.to_thread(fetch_institutional_post_earnings_audit, ticker),
            return_exceptions=True,
        )
        if isinstance(fv_res, dict) and not fv_res.get("error"):
            ret["finviz_insider_trading"] = fv_res
        if isinstance(cg_res, dict) and not cg_res.get("error"):
            ret["congress_trades"] = cg_res
        if isinstance(al_res, dict) and not al_res.get("error"):
            ret["alphaspread_intrinsic_value"] = al_res
        if isinstance(sec_risk_res, dict) and not sec_risk_res.get("error"):
            ret["institutional_sec_and_risk_audit"] = sec_risk_res
            if sec_risk_res.get("ready_institutional_markdown"):
                ret["ready_institutional_risk_markdown"] = sec_risk_res["ready_institutional_markdown"]
        if isinstance(sec_post_res, dict) and not sec_post_res.get("error"):
            ret["institutional_post_earnings_audit"] = sec_post_res
            if sec_post_res.get("ready_post_earnings_markdown"):
                ret["ready_institutional_post_earnings_markdown"] = sec_post_res["ready_post_earnings_markdown"]

        # Build ready-to-render Fundamentals Extras Markdown Tables
        extra_tables = []
        if isinstance(sec_risk_res, dict) and sec_risk_res.get("ready_institutional_markdown"):
            extra_tables.append(sec_risk_res["ready_institutional_markdown"])
        if isinstance(sec_post_res, dict) and sec_post_res.get("ready_post_earnings_markdown"):
            extra_tables.append(sec_post_res["ready_post_earnings_markdown"])

        if isinstance(al_res, dict) and not al_res.get("error"):
            sc = al_res.get("scenarios") or {}
            al_rows = [
                "### 🎯 AlphaSpread DCF Intrinsic Valuation (Bear / Base / Bull Cases)",
                f"**Overall Valuation Posture:** {al_res.get('overall_read', 'N/A')} · **DCF Range:** {al_res.get('intrinsic_value_range', 'N/A')}",
                "",
                "| Scenario | DCF Intrinsic Value | Current Price | Upside / (Overvaluation) | AlphaSpread Verdict | Revenue Growth | Target Net Margin | Discount Rate | Terminal Growth |",
                "| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |",
            ]
            for sc_name, sc_label in [("bear", "🐻 **Bear Case**"), ("base", "⚖️ **Base Case**"), ("bull", "🐂 **Bull Case**")]:
                c_data = sc.get(sc_name) or {}
                if c_data:
                    asm = c_data.get("assumptions") or {}
                    dcf_val = f"${c_data.get('intrinsic_value'):.2f}" if c_data.get('intrinsic_value') is not None else "N/A"
                    cur_px = f"${al_res.get('current_price'):.2f}" if al_res.get('current_price') else "N/A"
                    up_pct = f"{c_data.get('upside_vs_price_pct'):+.1f}%" if c_data.get('upside_vs_price_pct') is not None else "N/A"
                    verd = f"**{c_data.get('verdict', 'N/A')}**"
                    rg = f"{asm.get('revenue_growth_pct')}%" if asm.get('revenue_growth_pct') is not None else "N/A"
                    nm = f"{asm.get('net_margin_end_pct')}%" if asm.get('net_margin_end_pct') is not None else "N/A"
                    dr = f"{asm.get('discount_rate_pct')}%" if asm.get('discount_rate_pct') is not None else "N/A"
                    tg = f"{asm.get('terminal_growth_pct')}%" if asm.get('terminal_growth_pct') is not None else "N/A"
                    al_rows.append(f"| {sc_label} | {dcf_val} | {cur_px} | {up_pct} | {verd} | {rg} | {nm} | {dr} | {tg} |")
            extra_tables.append("\n".join(al_rows))

        if isinstance(fv_res, dict) and not fv_res.get("error"):
            w90 = fv_res.get("last_90_days") or {}
            fv_rows = [
                "### 💼 Finviz Corporate Insider Activity (SEC Form 4 Filings)",
                f"**90-Day Insider Sentiment:** {w90.get('net_signal', 'N/A')} (Buys: {w90.get('open_market_buys', 0)} [{w90.get('buy_value', '$0')}] · Sells: {w90.get('open_market_sells', 0)} [{w90.get('sell_value', '$0')}] · Net: {w90.get('net_value', '$0')})",
                "",
                "| Date | Insider | Relationship / Title | Transaction | Price / Cost | Shares | Value ($) | Total Shares Held |",
                "| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |",
            ]
            for tx in (fv_res.get("recent_transactions") or [])[:8]:
                cost_s = f"${tx.get('cost'):.2f}" if tx.get('cost') is not None else "-"
                shares_s = f"{tx.get('shares'):,}" if isinstance(tx.get('shares'), (int, float)) else str(tx.get('shares') or "-")
                tot_s = f"{tx.get('shares_total'):,}" if isinstance(tx.get('shares_total'), (int, float)) else str(tx.get('shares_total') or "-")
                val_s = tx.get("value") or "-"
                fv_rows.append(
                    f"| {tx.get('date')} | **{tx.get('insider')}** | {tx.get('relationship')} | {tx.get('transaction')} | {cost_s} | {shares_s} | {val_s} | {tot_s} |"
                )
            extra_tables.append("\n".join(fv_rows))

        if isinstance(cg_res, dict) and not cg_res.get("error"):
            w90_cg = cg_res.get("last_90_days") or {}
            cg_rows = [
                "### 🏛️ Congressional / Politician Trades (STOCK Act Disclosures via Quiver Quantitative)",
                f"**90-Day Congress Sentiment:** {w90_cg.get('net_signal', 'N/A')} (Purchases: {w90_cg.get('purchases', 0)} · Sales: {w90_cg.get('sales', 0)})",
                "",
                "| Traded Date | Politician | Chamber & Party | Transaction | Reported Amount Range |",
                "| :--- | :--- | :--- | :--- | :--- |",
            ]
            for ct in (cg_res.get("recent_trades") or [])[:8]:
                pol_str = f"**{ct.get('politician')}**"
                cham = f"{ct.get('chamber', '')} / {ct.get('party', '')}".strip(" /")
                tx_badge = f"🟢 **Purchase**" if ct.get('transaction') == 'Purchase' else f"🔴 **Sale**"
                cg_rows.append(
                    f"| {ct.get('traded')} | {pol_str} | {cham} | {tx_badge} | {ct.get('amount_range') or 'N/A'} |"
                )
            extra_tables.append("\n".join(cg_rows))

        if extra_tables:
            ret["ready_fundamentals_extras_markdown"] = "\n\n".join(extra_tables)
    except Exception as fe:
        logger.warning(f"Error fetching fundamentals extras for {ticker}: {fe}")

    return ret


async def fetch_stockverdicts_data(ticker: str) -> Dict[str, Any]:
    """
    Fetches live fundamental verdict, fair-value price ladder (Heavy Buy / Buy / Fair / Exit),
    factor scores (Value / Growth / Quality / Fundamental / Trust), plain-English Pros & Cons,
    Earnings Quality accounting checks, and Insider/Institutional activity from stockverdicts.com.
    """
    import html as html_lib
    sym = (ticker or "").strip().upper()
    if not sym:
        return {"error": "Empty ticker"}

    api_url = f"https://stockverdicts.com/api/v1/stock/{sym}"
    page_url = f"https://stockverdicts.com/stock/{sym}"
    result: Dict[str, Any] = {
        "source": "stockverdicts.com",
        "ticker": sym,
        "page_url": page_url,
    }

    headers = {
        "User-Agent": "StockPulseAI/2.0 (+https://stockverdicts.com)",
        "Accept": "application/json, text/html",
    }

    async with httpx.AsyncClient(timeout=10.0, follow_redirects=True, headers=headers) as client:
        api_task = client.get(api_url)
        html_task = client.get(page_url)
        responses = await asyncio.gather(api_task, html_task, return_exceptions=True)

        api_resp, html_resp = responses[0], responses[1]

        if not isinstance(api_resp, Exception) and api_resp.status_code == 200:
            try:
                j = api_resp.json()
                if isinstance(j, dict) and not j.get("error"):
                    result.update({
                        "company": j.get("company"),
                        "sector": j.get("sector"),
                        "as_of": j.get("as_of"),
                        "verdict": j.get("verdict"),
                        "signal": j.get("signal"),
                        "style": j.get("style"),
                        "price": j.get("price"),
                        "fair_value_price_ladder": j.get("price_levels") or {},
                        "factor_scores": j.get("scores") or {},
                        "pros": j.get("pros") or [],
                        "cons": j.get("cons") or [],
                        "red_flags": j.get("red_flags") or [],
                    })
            except Exception:
                pass

        if not isinstance(html_resp, Exception) and html_resp.status_code == 200:
            text = html_resp.text or ""

            def _clean_html(raw_html: str) -> str:
                stripped = re.sub(r"<[^>]+>", " ", raw_html)
                unescaped = html_lib.unescape(stripped)
                return re.sub(r"\s+", " ", unescaped).strip()

            # Extract Earnings Quality checks (.eq-row)
            eq_matches = re.findall(r'<div class="eq-row[^"]*">(.*?)</div>', text, re.S)
            if eq_matches:
                result["earnings_quality_checks"] = [_clean_html(m) for m in eq_matches if _clean_html(m)]

            # Extract Technical summary line (.an-tech)
            tech_m = re.search(r'<div class="an-tech">(.*?)</div>', text, re.S)
            if tech_m:
                result["technical_readout"] = _clean_html(tech_m.group(1))

            # Extract Insider & Institutional activity (.ii-box)
            ii_matches = re.findall(r'<div class="ii-box">(.*?)</div>\s*(?=<div class="ii-box">|</div>)', text, re.S)
            if ii_matches:
                result["insider_and_institutional"] = [_clean_html(m) for m in ii_matches if _clean_html(m)]

            # Extract Sector Peers (.peer)
            peer_matches = re.findall(r'<a class="peer"[^>]*>(.*?)</a>', text, re.S)
            if peer_matches:
                result["sector_peers"] = [_clean_html(m) for m in peer_matches if _clean_html(m)]

    if not result.get("verdict") and not result.get("pros"):
        return {"error": f"No StockVerdicts data found for {sym}"}

    return result


async def fetch_earningshub_week_analysis(
    query_text: str = "",
    target_date: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Reads live weekly earnings calendar & macro events from EarningsHub
    (https://earningshub.com/earnings-calendar/this-week via api.savvytrader.com)
    and enriches the top marquee earnings reporters with StockPulse's live
    Earning Pulse (Quarterly PQH/PQL & Monthly PMH/PML) setups.
    """
    from datetime import datetime, timedelta, date as date_cls
    from zoneinfo import ZoneInfo

    now_ct = datetime.now(ZoneInfo("America/Chicago"))
    ref_date: date_cls = now_ct.date()

    if target_date:
        try:
            from day_trading.v4 import parse_pulse_date
            parsed = parse_pulse_date(target_date)
            if parsed:
                ref_date = parsed
        except Exception:
            pass
    else:
        # Match EarningsHub's exact `/earnings-calendar/this-week` date-window logic (`XV`):
        # On Saturday (5) or Sunday (6) — or Friday after 3:30 PM CT — advance into the upcoming week.
        if ref_date.weekday() in (5, 6):
            ref_date = ref_date + timedelta(days=2)
        elif ref_date.weekday() == 4 and now_ct.hour >= 16 and "next week" in (query_text or "").lower():
            ref_date = ref_date + timedelta(days=3)

    monday = ref_date - timedelta(days=ref_date.weekday())
    friday = monday + timedelta(days=4)
    start_str = monday.isoformat()
    end_str = friday.isoformat()

    # If user explicitly asked for "next week" on Monday-Thursday, also compute next week's Mon-Fri
    q_low = (query_text or "").lower()
    next_monday = monday + timedelta(days=7)
    next_friday = friday + timedelta(days=7)
    also_fetch_following_week = (
        not target_date
        and now_ct.date().weekday() in (0, 1, 2, 3, 4)
        and monday == (now_ct.date() - timedelta(days=now_ct.date().weekday()))
        and "next week" in q_low
    )

    def _fmt_mcap(val: Any) -> str:
        try:
            v = float(val or 0)
            if v >= 1e12:
                return f"${v / 1e12:.2f}T"
            if v >= 1e9:
                return f"${v / 1e9:.1f}B"
            if v >= 1e6:
                return f"${v / 1e6:.0f}M"
            return "N/A"
        except Exception:
            return "N/A"

    def _fmt_rev(val: Any) -> str:
        try:
            v = float(val or 0)
            if v <= 0:
                return "N/A"
            if v >= 1e9:
                return f"${v / 1e9:.2f}B"
            if v >= 1e6:
                return f"${v / 1e6:.1f}M"
            return f"${v:,.0f}"
        except Exception:
            return "N/A"

    def _fmt_session(t_str: Optional[str]) -> str:
        if not t_str:
            return "TBD"
        if t_str < "09:30:00":
            return f"BMO ({t_str[:5]} ET)"
        if t_str >= "16:00:00":
            return f"AMC ({t_str[:5]} ET)"
        return f"Intraday ({t_str[:5]} ET)"

    def _format_item(x: Dict[str, Any]) -> Dict[str, Any]:
        eps_est = x.get("epsEstimate")
        eps_pri = x.get("epsPrior")
        eps_act = x.get("epsActual")
        eps_yoy = None
        if eps_est is not None and eps_pri is not None and abs(float(eps_pri)) >= 0.05:
            eps_yoy = round(((float(eps_est) - float(eps_pri)) / abs(float(eps_pri))) * 100.0, 1)

        rev_est = x.get("revenueEstimate")
        rev_pri = x.get("revenuePrior")
        rev_yoy = None
        if rev_est and rev_pri and float(rev_pri) > 0:
            rev_yoy = round(((float(rev_est) - float(rev_pri)) / float(rev_pri)) * 100.0, 1)

        edate = x.get("earningsDate") or ""
        day_name = ""
        try:
            day_name = date_cls.fromisoformat(edate).strftime("%A")
        except Exception:
            pass

        etime = x.get("earningsTime") or ""
        return {
            "symbol": x.get("symbol"),
            "company": x.get("assetName") or x.get("symbol"),
            "date": edate,
            "day": day_name,
            "session": _fmt_session(etime),
            "is_bmo": bool(etime and etime < "09:30:00"),
            "is_amc": bool(etime and etime >= "16:00:00"),
            "period": f"{x.get('period', '')} {x.get('periodYear', '')}".strip(),
            "importance": x.get("importance", 0),
            "confirmed": bool(x.get("isDateConfirmed", False)),
            "market_cap_raw": float(x.get("marketCap") or 0),
            "market_cap": _fmt_mcap(x.get("marketCap")),
            "eps_estimate": eps_est,
            "eps_prior": eps_pri,
            "eps_actual": eps_act,
            "eps_yoy_pct": f"{eps_yoy:+.1f}%" if eps_yoy is not None else "N/A",
            "eps_yoy_raw": eps_yoy,
            "revenue_estimate": _fmt_rev(rev_est),
            "revenue_prior": _fmt_rev(rev_pri),
            "revenue_yoy_pct": f"{rev_yoy:+.1f}%" if rev_yoy is not None else "N/A",
            "revenue_yoy_raw": rev_yoy,
        }

    cal_url = f"https://api.savvytrader.com/pricing/assets/earnings/calendar?start={start_str}&end={end_str}"
    evt_url = f"https://api.savvytrader.com/events/?start={start_str}&end={end_str}"
    headers = {
        "User-Agent": "StockPulseAI/2.0 (+https://earningshub.com/earnings-calendar/this-week)",
        "Accept": "application/json",
    }

    raw_cal: List[Dict[str, Any]] = []
    raw_evts: List[Dict[str, Any]] = []
    next_week_preview: List[Dict[str, Any]] = []

    async with httpx.AsyncClient(timeout=12.0, follow_redirects=True, headers=headers) as client:
        tasks = [client.get(cal_url), client.get(evt_url)]
        if also_fetch_following_week:
            nw_url = f"https://api.savvytrader.com/pricing/assets/earnings/calendar?start={next_monday.isoformat()}&end={next_friday.isoformat()}"
            tasks.append(client.get(nw_url))

        resps = await asyncio.gather(*tasks, return_exceptions=True)
        if not isinstance(resps[0], Exception) and resps[0].status_code == 200:
            data0 = resps[0].json()
            if isinstance(data0, list):
                raw_cal = data0
        if not isinstance(resps[1], Exception) and resps[1].status_code == 200:
            data1 = resps[1].json()
            if isinstance(data1, list):
                raw_evts = data1
        if len(resps) > 2 and not isinstance(resps[2], Exception) and resps[2].status_code == 200:
            data2 = resps[2].json()
            if isinstance(data2, list):
                nw_items = [_format_item(item) for item in data2 if item.get("symbol")]
                nw_items.sort(key=lambda r: (r["importance"] >= 4, r["market_cap_raw"]), reverse=True)
                next_week_preview = [
                    {k: v for k, v in it.items() if not k.endswith("_raw") and k not in ("is_bmo", "is_amc")}
                    for it in nw_items[:15]
                ]

    formatted = [_format_item(x) for x in raw_cal if x.get("symbol")]
    # Sort primarily by market cap descending (with importance tie-boost)
    formatted.sort(key=lambda r: (r["importance"] >= 4, r["market_cap_raw"]), reverse=True)

    # Top 18 marquee headliners across the week
    def _clean_item(it: Dict[str, Any]) -> Dict[str, Any]:
        return {k: v for k, v in it.items() if not k.endswith("_raw") and k not in ("is_bmo", "is_amc")}

    top_headliners = [_clean_item(it) for it in formatted[:18]]

    # Group day-by-day (Monday..Friday)
    daily_schedule: Dict[str, Any] = {}
    busiest_day = ""
    busiest_day_count = -1

    for offset in range(5):
        d_obj = monday + timedelta(days=offset)
        d_iso = d_obj.isoformat()
        d_label = f"{d_obj.strftime('%A')} ({d_iso})"
        day_items = [it for it in formatted if it["date"] == d_iso]
        day_items.sort(key=lambda r: r["market_cap_raw"], reverse=True)

        if len(day_items) > busiest_day_count:
            busiest_day_count = len(day_items)
            busiest_day = f"{d_obj.strftime('%A')} ({d_iso}) — {len(day_items)} companies"

        bmo_items = [_clean_item(it) for it in day_items if it["is_bmo"]][:10]
        amc_items = [_clean_item(it) for it in day_items if it["is_amc"]][:10]

        day_evts = [
            f"{(ev.get('eventTime') or '')[:5]} ET: {ev.get('eventName')}"
            for ev in raw_evts
            if ev.get("eventDate") == d_iso and ev.get("eventName")
        ]

        daily_schedule[d_label] = {
            "total_companies_reporting": len(day_items),
            "economic_and_macro_events": day_evts,
            "before_open_bmo_leaders": bmo_items,
            "after_close_amc_leaders": amc_items,
        }

    # Filter to >= $5B market cap for fundamental outlier rankings
    large_caps = [it for it in formatted if it["market_cap_raw"] >= 5e9]

    eps_growers = sorted(
        [it for it in large_caps if it["eps_yoy_raw"] is not None and it["eps_yoy_raw"] > 10.0],
        key=lambda r: r["eps_yoy_raw"],
        reverse=True,
    )[:8]

    rev_growers = sorted(
        [it for it in large_caps if it["revenue_yoy_raw"] is not None and it["revenue_yoy_raw"] > 8.0],
        key=lambda r: r["revenue_yoy_raw"],
        reverse=True,
    )[:8]

    eps_contraction = sorted(
        [it for it in large_caps if it["eps_yoy_raw"] is not None and it["eps_yoy_raw"] < -3.0],
        key=lambda r: r["market_cap_raw"],
        reverse=True,
    )[:8]

    # Enrich top 8 marquee headliners with live StockPulse Earning Pulse (PQH/PQL & PMH/PML)
    earning_pulse_setups: List[Dict[str, Any]] = []
    try:
        from day_trading.v4 import get_earning_pulse
        top8_syms = [it["symbol"] for it in formatted[:8] if it.get("symbol")]
        ep_results = await asyncio.gather(
            *[asyncio.to_thread(get_earning_pulse, sym) for sym in top8_syms],
            return_exceptions=True,
        )
        for sym, ep in zip(top8_syms, ep_results):
            if isinstance(ep, dict) and not ep.get("error"):
                ti = ep.get("trade_idea") or {}
                lv = ep.get("key_levels") or ep.get("levels") or {}
                earning_pulse_setups.append({
                    "symbol": sym,
                    "current_price": ep.get("price") or ep.get("current_price"),
                    "structure_tag": ep.get("structure_tag") or ti.get("structure_tag") or "W: N/A",
                    "quarterly_range": f"${lv.get('pql')} – ${lv.get('pqh')} (Mid ${lv.get('pq_mid')})",
                    "monthly_range": f"${lv.get('pml')} – ${lv.get('pmh')} (Mid ${lv.get('pm_mid')})",
                    "valid_outcome": ep.get("valid_outcome"),
                    "bias": ti.get("bias"),
                    "action": ti.get("action"),
                    "suggested_contract": ti.get("suggested_contract"),
                    "open_contract": ti.get("open_contract"),
                    "cost_efficient_otm": ti.get("cost_efficient_otm"),
                    "cost_efficient_spread": ti.get("cost_efficient_spread"),
                    "cost_efficient_butterfly": ti.get("cost_efficient_butterfly"),
                    "entry": ti.get("entry_level") or ti.get("entry"),
                    "stop": ti.get("stop_loss") or ti.get("stop"),
                    "target_1": ti.get("target_1"),
                    "target_2": ti.get("target_2"),
                    "reward_risk": ti.get("reward_risk"),
                    "rationale": ti.get("rationale"),
                })
    except Exception as epe:
        logger.warning(f"Error enriching top headliners with Earning Pulse: {epe}")

    headliner_rows = [
        "| Date & Session | Ticker | Company | Market Cap | EPS Est vs Prior (YoY %) | Revenue Est vs Prior (YoY %) |",
        "| :--- | :--- | :--- | :--- | :--- | :--- |",
    ]
    for h in top_headliners[:15]:
        sess_raw = str(h.get("session", ""))
        sess_short = "BMO" if sess_raw.startswith("BMO") else ("AMC" if sess_raw.startswith("AMC") else sess_raw)

        def _eps_fmt(v: Any) -> str:
            return f"${float(v):.2f}" if v is not None else "N/A"

        eps_cell = f"{_eps_fmt(h.get('eps_estimate'))} vs {_eps_fmt(h.get('eps_prior'))}"
        if h.get("eps_yoy_pct") and h.get("eps_yoy_pct") != "N/A":
            eps_cell += f" ({h['eps_yoy_pct']})"
        rev_cell = f"{h.get('revenue_estimate', 'N/A')} vs {h.get('revenue_prior', 'N/A')}"
        if h.get("revenue_yoy_pct") and h.get("revenue_yoy_pct") != "N/A":
            rev_cell += f" ({h['revenue_yoy_pct']})"
        headliner_rows.append(
            f"| {h.get('day', '')[:3]} {h.get('date', '')} ({sess_short}) | **{h.get('symbol', '')}** | {h.get('company', '')} | {h.get('market_cap', '')} | {eps_cell} | {rev_cell} |"
        )


    ep_rows = [
        "| Ticker | Price | Structure | Quarterly Range (PQL–PQH) | Monthly Range (PML–PMH) | Action & Validated Outcome | Open Contract | Cost-Efficient Options (OTM / Spread / 🦋 Butterfly) | Entry / Stop / T1 → T2 |",
        "| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |",
    ]
    for s in earning_pulse_setups:
        ce_parts = []
        if s.get("cost_efficient_otm"):
            ce_parts.append(f"**OTM:** {s['cost_efficient_otm']}")
        if s.get("cost_efficient_spread"):
            ce_parts.append(f"**Spread:** {s['cost_efficient_spread']}")
        if s.get("cost_efficient_butterfly"):
            ce_parts.append(f"**🦋 Butterfly:** {s['cost_efficient_butterfly']}")
        ce_str = " · ".join(ce_parts) if ce_parts else "N/A"
        ep_rows.append(
            f"| **{s.get('symbol')}** | ${s.get('current_price')} | `{s.get('structure_tag')}` | {s.get('quarterly_range')} | {s.get('monthly_range')} | **{s.get('action')}** ({s.get('valid_outcome')}) | `{s.get('open_contract') or s.get('suggested_contract')}` | {ce_str} | Entry ${s.get('entry')} · Stop ${s.get('stop')} · T1 ${s.get('target_1')} → T2 ${s.get('target_2')} ({s.get('reward_risk')}) |"
        )

    result_payload: Dict[str, Any] = {
        "source": "https://earningshub.com/earnings-calendar/this-week",
        "week_window": f"{start_str} to {end_str}",
        "summary_stats": {
            "total_companies_reporting": len(formatted),
            "confirmed_dates_count": sum(1 for it in formatted if it["confirmed"]),
            "mega_and_large_cap_10b_plus": sum(1 for it in formatted if it["market_cap_raw"] >= 10e9),
            "billion_dollar_plus_count": sum(1 for it in formatted if it["market_cap_raw"] >= 1e9),
            "busiest_day": busiest_day,
        },
        "ready_markdown_tables": {
            "marquee_headliners_table": "\n".join(headliner_rows),
            "earning_pulse_battle_plans_table": "\n".join(ep_rows),
        },
        "top_headliners": top_headliners,
        "daily_schedule": daily_schedule,
        "growth_and_fundamental_outliers": {
            "fastest_eps_growth_leaders_5b_plus": [_clean_item(it) for it in eps_growers],
            "fastest_revenue_growth_leaders_5b_plus": [_clean_item(it) for it in rev_growers],
            "earnings_contraction_watchlist": [_clean_item(it) for it in eps_contraction],
        },
        "earning_pulse_setups": earning_pulse_setups,
    }

    if next_week_preview:
        result_payload["following_week_headliners_preview"] = {
            "week_window": f"{next_monday.isoformat()} to {next_friday.isoformat()}",
            "top_headliners": next_week_preview,
        }

    return result_payload


def get_universe_tool(watchlist_name: str = "default") -> Dict[str, Any]:
    name = (watchlist_name or "default").lower().strip()
    tickers = WATCHLISTS.get(name, WATCHLISTS.get("default", []))

    # Read cached scan items if available
    data_dir = Path(__file__).resolve().parent.parent.parent / "data"
    cache_path = data_dir / "default50_premarket_cache.json"
    trade_plan_path = data_dir / "trade_plan_latest.json"
    cached_setups = []

    # 1. Primary source: default50_premarket_cache.json
    if cache_path.exists():
        try:
            with open(cache_path, "r", encoding="utf-8") as fp:
                cdata = json.load(fp)
                cached_setups = cdata.get("items", [])
        except Exception as e:
            logger.warning(f"Failed to read {cache_path}: {e}")

    # 2. In-memory scheduler cache fallback
    if not cached_setups:
        try:
            from backend.services.scheduler import _DEFAULT50_SCAN_CACHE
            cached_setups = _DEFAULT50_SCAN_CACHE.get("items", [])
        except Exception:
            pass

    # 3. Trade plan latest fallback
    trade_plan_rows = []
    if trade_plan_path.exists():
        try:
            with open(trade_plan_path, "r", encoding="utf-8") as fp:
                tp_data = json.load(fp)
                trade_plan_rows = tp_data.get("rows", [])
        except Exception:
            pass

    top_picks = []

    # Process setups from premarket cache if present
    if cached_setups:
        # Sort by confluence score descending
        sorted_setups = sorted(cached_setups, key=lambda x: -(x.get("score") or 0))
        for s in sorted_setups:
            t = s.get("ticker") or s.get("Ticker")
            if not t:
                continue
            price = s.get("price") or s.get("Close")
            entry = s.get("entry") or price
            stop = s.get("stop_loss") or s.get("stop")
            t1 = s.get("target1") or s.get("t1")
            t2 = s.get("target2") or s.get("t2")
            rr = s.get("rr_t1") or 2.0
            verdict = s.get("verdict") or "BULLISH"
            direction = s.get("direction") or "LONG"
            score = s.get("score") or 5
            sector = s.get("sector") or ""
            poc = s.get("poc")
            val = s.get("val")
            vah = s.get("vah")
            stage2 = s.get("stage2_status") or ""

            if entry and stop:
                top_picks.append({
                    "ticker": t,
                    "direction": direction,
                    "verdict": verdict,
                    "confluence_score": score,
                    "sector": sector,
                    "current_price": round(float(price), 2) if price else None,
                    "entry_trigger": round(float(entry), 2) if entry else None,
                    "stop_loss": round(float(stop), 2) if stop else None,
                    "target_1": round(float(t1), 2) if t1 else None,
                    "target_2": round(float(t2), 2) if t2 else None,
                    "reward_risk": f"{rr}x" if isinstance(rr, (int, float)) else str(rr),
                    "volume_profile": {
                        "poc": round(float(poc), 2) if poc else None,
                        "val": round(float(val), 2) if val else None,
                        "vah": round(float(vah), 2) if vah else None,
                    },
                    "stage_2_momentum": stage2,
                })
            if len(top_picks) >= 10:
                break

    # If top_picks still empty, build from trade_plan_rows
    if not top_picks and trade_plan_rows:
        for r in trade_plan_rows[:10]:
            t = r.get("Ticker")
            if not t:
                continue
            price = r.get("Close")
            stop = r.get("Intra Stop")
            t1 = r.get("Intra T1")
            t2 = r.get("Intra T2")
            rr = r.get("RR(T1)")
            verdict = r.get("Verdict") or "BULLISH"
            direction = r.get("Direction") or "LONG"
            grade = r.get("Grade") or "A"
            if price and stop:
                top_picks.append({
                    "ticker": t,
                    "direction": direction,
                    "verdict": verdict,
                    "confluence_score": grade,
                    "current_price": price,
                    "entry_trigger": price,
                    "stop_loss": stop,
                    "target_1": t1,
                    "target_2": t2,
                    "reward_risk": str(rr) if rr else "2.0x",
                    "tactical_note": r.get("If opens near entry") or ""
                })

    return {
        "universe_name": f"Default 50 Core Universe ({len(tickers)} stocks)",
        "sample_tickers": tickers,
        "total_tickers": len(tickers),
        "cached_top_setups": top_picks if top_picks else "Default 50 tracking active.",
        "note": "Default 50 consists of 50 high-beta and mega-cap leaders. The best setups feature Stage 2 breakout momentum, confluence score >= 2, and favorable R:R."
    }


@router.get("/status")
async def get_agent_status(request: Request):
    """
    Returns active primary AI provider and all configured fallbacks.
    """
    headers_dict = dict(request.headers)
    diag = load_all_environment_sources(headers_dict)
    chain = get_configured_providers(headers_dict)
    primary = chain[0] if chain else None

    # Check local Ollama connectivity
    ollama_online = False
    ollama_host = os.getenv("OLLAMA_HOST", "http://localhost:11434").strip()
    try:
        async with httpx.AsyncClient(timeout=1.5) as client:
            r = await client.get(f"{ollama_host}/api/tags")
            ollama_online = (r.status_code == 200)
    except Exception:
        ollama_online = False

    chain_summary = []
    for p in chain:
        if p["id"] == "ollama":
            status = "🟢 Online" if ollama_online else "⚪ Offline"
        else:
            status = "🟢 Ready"
        chain_summary.append({
            "id": p["id"],
            "name": p["name"],
            "model": p["model"],
            "speed": p["speed"],
            "status": status,
        })

    primary_label = f"{primary['name']} ({primary['model']})" if primary else "None"
    if primary and primary["id"] in ("gemini", "gemini-2"):
        primary_label = "🟢 Google Gemini (~200 t/s · 3.5 Flash)"
    elif primary and primary["id"] == "groq":
        primary_label = "🟢 Groq Cloud (~900 t/s · Qwen 27B)"
    elif primary and primary["id"] == "groq-120b":
        primary_label = "🟢 Groq Cloud (~800 t/s · GPT-OSS 120B)"
    elif primary and primary["id"] == "cerebras":
        primary_label = "🟢 Cerebras Cloud (~1,800 t/s)"
    elif primary and primary["id"] == "openrouter":
        primary_label = "🟢 OpenRouter (Cloud Free)"
    elif primary and primary["id"] == "ollama":
        primary_label = f"{'🟢' if ollama_online else '⚪'} Local Ollama ({primary['model']})"

    return {
        "available": bool(len(chain) > 1 or ollama_online),
        "primary": primary_label,
        "primary_id": primary["id"] if primary else None,
        "chain": chain_summary,
        "fallback_count": len(chain) - 1,
        "diagnostics": {
            "all_env_keys": sorted([k for k in os.environ.keys() if not k.startswith("npm_")]),
            "secret_paths": diag.get("secret_paths", []),
            "discovered": diag.get("discovered", {}),
            "has_gemini": bool(get_env_flexible(["GEMINI", "GOOGLE_GEMINI", "GOOGLE_API"])),
            "has_groq": bool(get_env_flexible(["GROQ"])),
            "has_openrouter": bool(get_env_flexible(["OPENROUTER"])),
            "has_cerebras": bool(get_env_flexible(["CEREBRAS"])),
        }
    }


@router.get("/spy-outcome")
@router.get("/v4-outcome")
@router.get("/pulse-outcome")
async def get_spy_outcome_endpoint(
    ticker: str = "SPY",
    date: Optional[str] = None,
    mode: str = "pulse",
):
    """
    Returns the live or historical/backtested Pulse outcome, key levels,
    and actionable CALLS vs PUTS trade plan + backtest verification for any ticker:
    - mode='pulse' (default): Day Trading Pulse (PDH/PDL & PWH/PWL + 8:35 AM 5m opening candle)
    - mode='long_term': Long Term Pulse (Month PMH/PML & Day PDH/PDL)
    - mode='earning': Earning Pulse (Quarterly PQH/PQL & Monthly PMH/PML)
    - mode='all': All 3 Pulse engines at once
    """
    try:
        from day_trading.v4 import (
            get_valid_outcome_and_trade_idea,
            get_long_term_pulse,
            get_earning_pulse,
            get_all_pulses,
        )
        sym = TICKER_ALIASES.get((ticker or "SPY").strip().upper(), (ticker or "SPY").strip().upper())
        m = (mode or "pulse").strip().lower()
        if m in {"long_term", "long-term", "longterm", "lt"}:
            return await asyncio.to_thread(get_long_term_pulse, sym, date)
        if m in {"earning", "earnings", "ep"}:
            return await asyncio.to_thread(get_earning_pulse, sym, date)
        if m == "all":
            return await asyncio.to_thread(get_all_pulses, sym, date)
        return await asyncio.to_thread(get_valid_outcome_and_trade_idea, sym, None, date)
    except Exception as e:
        logger.error(f"Error fetching Pulse outcome for {ticker}: {e}")
        return {"error": str(e)}


@router.get("/v4-tickers")
async def get_v4_tickers_agent():
    """Return active tickers configured for 8:00 AM & 8:35 AM V4 Telegram alerts."""
    from backend.services.scheduler import get_v4_alert_tickers, V4_POPULAR_OPTIONS
    return {
        "tickers": get_v4_alert_tickers(),
        "popular_options": V4_POPULAR_OPTIONS,
    }


@router.post("/v4-tickers")
async def update_v4_tickers_agent(payload: Dict[str, Any]):
    """Update active tickers configured for 8:00 AM & 8:35 AM V4 Telegram alerts and optionally send an instant alert."""
    from backend.services.scheduler import (
        set_v4_alert_tickers,
        V4_POPULAR_OPTIONS,
        spy_v4_summary_job,
        spy_v4_post_open_outcome_job,
    )
    raw_tickers = payload.get("tickers") or []
    if isinstance(raw_tickers, str):
        raw_tickers = [t.strip() for t in raw_tickers.split(",") if t.strip()]
    saved = set_v4_alert_tickers(raw_tickers)

    alert_res = None
    target_for_alert = payload.get("alert_tickers") or saved
    if isinstance(target_for_alert, str):
        target_for_alert = [t.strip().upper() for t in target_for_alert.split(",") if t.strip()]

    if payload.get("send_playbook"):
        alert_res = await asyncio.to_thread(spy_v4_summary_job, target_for_alert)
    elif payload.get("send_post_open"):
        alert_res = await asyncio.to_thread(spy_v4_post_open_outcome_job, target_for_alert)

    return {
        "status": "updated",
        "tickers": saved,
        "popular_options": V4_POPULAR_OPTIONS,
        "alert_result": alert_res,
    }


@router.get("/earnings-calendar")
async def get_earnings_calendar_endpoint(
    date: Optional[str] = None,
    q: str = "next week earnings analysis",
):
    """
    Fetches live weekly earnings calendar & macro events from EarningsHub
    (https://earningshub.com/earnings-calendar/this-week) plus StockPulse Earning Pulse setups.
    """
    try:
        return await fetch_earningshub_week_analysis(query_text=q, target_date=date)
    except Exception as e:
        logger.error(f"Error fetching EarningsHub calendar: {e}")
        return {"error": str(e)}


@router.post("/chat")
async def chat_with_agent(req: ChatRequest, request: Request):
    """
    Streaming chat endpoint with 5-provider automatic fallback:
    Gemini 3.5 Flash -> Groq -> Cerebras -> OpenRouter -> Local Ollama
    """
    from day_trading.v4 import parse_pulse_date

    headers_dict = dict(request.headers)
    chain = get_configured_providers(headers_dict)

    # Determine intent: Market Overview, Earnings Calendar, Universe query, General/Meta query, or Ticker-specific query
    last_user_text = req.messages[-1].content if req.messages else ""
    user_lower = last_user_text.lower().strip()

    requested_date_obj = parse_pulse_date(last_user_text)
    target_date_str = requested_date_obj.isoformat() if requested_date_obj else None

    is_earnings_calendar_query = any(k in user_lower for k in [
        "next week earnings", "next week earning", "earnings next week", "earning next week",
        "this week earnings", "this week earning", "earnings this week", "earning this week",
        "weekly earnings", "week earnings", "earnings calendar", "earning calendar",
        "earnings schedule", "upcoming earnings", "who reports earnings", "who is reporting earnings",
        "earningshub", "earnings hub", "next week's earnings", "this week's earnings"
    ]) or user_lower in [
        "earnings", "earnings analysis", "earning analysis", "earnings this week?", "earnings next week?"
    ]

    is_market_query = not is_earnings_calendar_query and (any(k in user_lower for k in [
        "how is market", "how is the market", "how's market", "how's the market",
        "how are markets", "how are the markets", "market today", "markets today",
        "market overview", "market snapshot", "market status", "market update",
        "macro snapshot", "overall market", "indices today", "index moves",
        "what is market doing", "what's market doing", "what is the market doing",
        "state of the market", "broad market", "market breadth"
    ]) or user_lower in ["market", "markets", "macro", "indices", "how is market today", "how is market today?"])

    has_stock_feature_keyword = bool(target_date_str) or any(k in user_lower for k in [
        "pulse", "v4", "backtest", "intraday", "fundamental", "fundamentals",
        "fair value", "stockverdicts", "verdict", "earning", "earnings",
        "calls", "puts", "pdh", "pdl", "pmh", "pml", "pqh", "pql"
    ])

    is_general_query = not is_market_query and not is_earnings_calendar_query and not has_stock_feature_keyword and (any(user_lower.startswith(w) for w in [
        "hello", "hi", "hey", "who are you", "what is", "how do", "explain",
        "help", "why is chat", "what can you do", "tell me about", "what are"
    ]) or "only aaoi" in user_lower or "general chat" in user_lower)

    universe_keywords = [
        "default 50", "default50", "watchlist", "best swing", "top pick", "top picks",
        "top swing", "screener", "scanner", "rank", "which stock", "best trade", "best trades",
        "best ticker", "best tickers", "universe", "good swing", "swing trade", "swing trades",
        "trade idea", "trade ideas", "stock pick", "stock picks", "what to trade",
        "what should i trade", "what to buy", "setups today", "trades for today",
        "swing today", "swing picks", "swings for today", "entry/stop", "entries and stops",
        "top setups", "best setups", "swing candidate", "swing candidates", "swing outlook",
        "swing setup", "swing setups", "trade setups", "best stock", "best stocks",
        "good trade", "good trades", "good stock", "good stocks", "recommend trade",
        "recommend trades", "recommend stock", "recommend stocks", "any trades", "any trade",
        "ideas for today", "trades today", "setup today", "swing", "swings", "picks today",
        "trade for today", "good swings"
    ]
    is_universe_query = not is_market_query and not is_earnings_calendar_query and any(k in user_lower for k in universe_keywords)

    # Dynamic Ticker Detection for any US stock (AAOI, SPCX, NVDA, TSLA, PLTR, etc.)
    ticker_stop_words = {
        "WHAT", "WHEN", "WHERE", "WHICH", "HOW", "WHY", "WHO", "WHOSE",
        "TODAY", "YESTERDAY", "TOMORROW", "NOW", "LATER", "SOON", "NEXT", "UPCOMING",
        "BUY", "SELL", "HOLD", "TRADE", "SWING", "LONG", "SHORT",
        "CALL", "CALLS", "PUT", "PUTS", "OPTION", "OPTIONS",
        "STOCK", "STOCKS", "SHARE", "SHARES", "TICKER", "TICKERS",
        "PRICE", "PRICES", "LEVEL", "LEVELS", "TARGET", "TARGETS",
        "STOP", "STOPS", "LOSS", "LOSSES", "RISK", "REWARD", "PLAN",
        "ENTRY", "ENTRIES", "EXIT", "EXITS", "POINT", "POINTS",
        "POC", "VAL", "VAH", "FIB", "FIBS", "BETA", "RSI", "MACD", "EMA", "SMA",
        "GOOD", "BAD", "BEST", "WORST", "TOP", "BOTTOM", "HIGH", "LOW",
        "MORE", "LESS", "MUCH", "MANY", "SOME", "ANY", "ALL", "NONE",
        "WITH", "FROM", "INTO", "OVER", "UNDER", "ABOVE", "BELOW",
        "THIS", "THAT", "THESE", "THOSE", "THEIR", "THERE", "HERE",
        "THE", "AND", "OR", "BUT", "FOR", "NOR", "YET", "SO", "ON", "AT", "IN", "TO", "OF", "BY",
        "CAN", "COULD", "WILL", "WOULD", "SHALL", "SHOULD", "MAY", "MIGHT", "MUST",
        "HAVE", "HAS", "HAD", "BEEN", "BEING", "WERE", "WAS", "ARE", "IS", "AM",
        "GIVE", "SHOW", "TELL", "FIND", "LOOK", "SEEK", "WANT", "NEED", "BUILD",
        "CHAT", "LIVE", "DATA", "INFO", "WORK", "PAGE", "USER", "AI", "MAKE",
        "WRONG", "RIGHT", "TRUE", "FALSE", "REAL", "FAKE", "LAST", "NOTE",
        "DEFAULT", "UNIVERSE", "WATCH", "LIST", "VIEW", "CHART", "OPEN", "DROP",
        "ABOUT", "ONLY", "ITS", "IT", "NOT", "YES", "NO", "SURE", "FINE",
        "HELP", "LIKE", "NEWS", "AUTO", "TECH", "EDIT", "TEST", "TIME",
        "ANALYZE", "ANALYSIS", "EVALUATE", "CHECK", "REVIEW", "SETUP", "SETUPS",
        "MARKET", "MARKETS", "INDICES", "INDEX", "MACRO",
        "VALID", "OUTCOME", "OUTCOMES", "PLAYBOOK", "AFTER", "BEFORE", "MORNING",
        "ALERT", "ALERTS", "POST", "MIN", "MINS", "MINUTE", "MINUTES",
        "CST", "EST", "EDT", "CDT", "CT", "ET", "PM", "DTE", "ATM", "OTM", "ITM",
        "DELTA", "GAMMA", "GEX", "PDH", "PDL", "PWH", "PWL", "MID", "ATR",
        "PMH", "PML", "PQH", "PQL", "CPR",
        "FUND", "FUNDS", "FAIR", "VALUE", "GROWTH", "AVOID", "PROS", "CONS",
        "TRUST", "SCORE", "SCORES", "PEER", "PEERS", "RED", "FLAG", "FLAGS",
        "CASH", "FLOW", "DEBT", "PEG", "ROE", "EPS", "PE", "YOY", "TTM", "COM",
        "PULSE", "PULSES", "BACKTEST", "BACK", "TERM", "EARNING", "EARNINGS",
        "CALENDAR", "SCHEDULE", "HUB", "EARNINGSHUB", "REPORT", "REPORTS", "REPORTING",
        "MONTH", "MONTHLY", "QUARTER", "QUARTERLY", "DAY", "DAILY", "WEEK", "WEEKLY",
        "MONDAY", "TUESDAY", "WEDNESDAY", "THURSDAY", "FRIDAY", "SATURDAY", "SUNDAY",
        "HIGHS", "LOWS", "DATE", "INTRADAY", "COMPARE",
        "JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "SEPT", "OCT", "NOV", "DEC",
        "JANUARY", "FEBRUARY", "MARCH", "APRIL", "JUNE", "JULY", "AUGUST", "SEPTEMBER", "OCTOBER", "NOVEMBER", "DECEMBER",
        "ROLE", "SENIOR", "EQUITY", "RESEARCH", "ANALYST", "INSTITUTIONAL", "MANAGER", "SPECIALIZING",
        "HEALTH", "CARE", "SECTOR", "CONTEXT", "PROVIDED", "PREVIOUS", "QUARTER", "QUARTERS",
        "TRANSCRIPT", "ESTIMATES", "CONSENSUS", "STREET", "WALL", "INTRA", "INDUSTRY", "SUPPLY",
        "CHAIN", "SIGNALS", "DISCLOSURES", "DEVIATIONS", "SURPRISES", "PARAMETRIC", "MEMORY",
        "FRAMEWORK", "BREAKDOWN", "NEGATIVE", "SPACE", "DRIFT", "DEFENSIVE", "VAGUE", "VOCABULARY",
        "PRICING", "POWER", "HEADWINDS", "BANDS", "ACCUMULATION", "OUTPACING", "COMPRESSION",
        "VARIANCE", "RATING", "STRUCTURAL", "DRIVERS", "TABULAR", "OVERVIEW", "FACTOR", "ANALYZED",
        "EXPECTATION", "DISCREPANCY", "IMPLIED", "BEAT", "MISS", "CONVICTION", "NARRATIVE", "THESIS",
        "DIRECTIONAL", "AUDIT", "GAP", "LINE", "CUT", "GUIDANCE", "RUN", "RATE", "BASE",
        "POST", "PRINT", "ACTION", "SCORECARD", "QUANTITATIVE", "OPERATING", "METRICS", "KPIS", "KPI",
        "MARGIN", "MARGINS", "GROSS", "PHASING", "TIMING", "ANOMALIES", "TONE", "CREDIBILITY",
        "CONFIDENT", "CAUTIOUS", "EVASIVE", "PIVOT", "CATALYSTS", "CATALYST", "BULL", "BEAR",
        "HYPOTHESIS", "REACTION", "GAAP", "NON", "NYSE", "NASDAQ", "AMEX", "SEC", "EDGAR",
        "DOC", "EX", "URL", "ACT", "BPS", "NII", "NCO", "ROTCE", "CET1", "FCF", "EBITDA", "EBIT",
        "LIKELY", "SHORT", "TERM", "PRICE"
    }

    explicit_ticker = None

    if not is_market_query and not is_earnings_calendar_query:
        # Stage 0: Explicit exchange or ticker pattern (e.g. "NYSE: JPM", "NYSE:JPM", "(NYSE: JPM)", "NASDAQ: AAPL")
        m_ex = re.search(r"\b(?:NYSE|NASDAQ|AMEX|TICKER|STOCK|SYMBOL)\s*[:=]\s*([A-Za-z]{1,5})\b", last_user_text, re.IGNORECASE)
        if m_ex:
            cand = m_ex.group(1).upper()
            if cand not in ticker_stop_words:
                explicit_ticker = cand

        # Stage 0.5: Ticker in parentheses e.g. "(JPM)", "(NYSE: JPM)"
        if not explicit_ticker:
            m_par = re.search(r"\((?:NYSE:\s*|NASDAQ:\s*|AMEX:\s*)?([A-Za-z]{1,5})\)", last_user_text, re.IGNORECASE)
            if m_par:
                cand = m_par.group(1).upper()
                if cand not in ticker_stop_words:
                    explicit_ticker = cand

        # Stage 1: Explicit dollar sign (e.g. $AAOI, $JPM, $SPY)
        if not explicit_ticker:
            dollar_tickers = re.findall(r"\$([A-Za-z]{1,5})\b", last_user_text)
            if dollar_tickers:
                for dt in dollar_tickers:
                    cand = dt.upper()
                    if cand not in ticker_stop_words:
                        explicit_ticker = cand
                        break

        # Stage 1.5: Direct match against company names in TICKER_ALIASES (e.g. "jpmorgan", "tesla", "apple")
        if not explicit_ticker:
            word_tokens = re.findall(r"\b[A-Za-z]{2,16}\b", last_user_text)
            for wt in word_tokens:
                up_wt = wt.upper()
                if up_wt in TICKER_ALIASES:
                    explicit_ticker = TICKER_ALIASES[up_wt]
                    break

        # Stage 2: Phrases like "for JPM", "on NVDA", "about TSLA", "reaction for JPM"
        if not explicit_ticker:
            phrase_matches = re.findall(r"\b(?:for|on|about|ticker|stock|symbol|reaction for|catalysts for)\s+([A-Za-z]{1,5})\b", last_user_text, re.IGNORECASE)
            if phrase_matches:
                for cand_raw in phrase_matches:
                    cand = cand_raw.upper()
                    if cand in TICKER_ALIASES:
                        explicit_ticker = TICKER_ALIASES[cand]
                        break
                    if cand not in ticker_stop_words:
                        explicit_ticker = cand
                        break

        # Stage 3: All-caps words (e.g. AAOI, SPCX, NVDA, TSLA, JPM) - skipping stop words
        if not explicit_ticker:
            raw_tokens = re.findall(r"\b[A-Za-z]{2,5}\b", last_user_text)
            caps_tokens = [t.upper() for t in raw_tokens if t.isupper() and t.upper() not in ticker_stop_words]
            if caps_tokens:
                matched_known = [c for c in caps_tokens if c in TICKER_ALIASES or (req.ticker and c == req.ticker.upper())]
                if matched_known:
                    explicit_ticker = TICKER_ALIASES.get(matched_known[0], matched_known[0])
                else:
                    explicit_ticker = caps_tokens[0]

        # Stage 4: Fallback for short queries (e.g. "tsla pulse 10/12/2026", "what is tsla pulse today")
        if not explicit_ticker and not is_general_query:
            raw_tokens = re.findall(r"\b[A-Za-z]{2,5}\b", last_user_text)
            for t in raw_tokens:
                up = t.upper()
                if up in TICKER_ALIASES:
                    explicit_ticker = TICKER_ALIASES[up]
                    break
                if up not in ticker_stop_words and len(up) >= 2:
                    explicit_ticker = up
                    break

    # If an explicit ticker was detected (e.g. "TSLA v4 after 8:35 AM" or "TSLA trade idea today"),
    # prioritize the ticker unless the user explicitly asked for Default 50 / Universe / Watchlist.
    explicit_universe_only = any(u in user_lower for u in [
        "default 50", "default50", "universe", "watchlist", "screener", "scanner", "which stock", "best stocks"
    ])
    if explicit_ticker and not explicit_universe_only:
        is_universe_query = False

    # If no explicit ticker was found and user is asking about trades/setups/picks/entries/targets, treat as universe query
    if not is_market_query and not is_earnings_calendar_query and not is_universe_query and not explicit_ticker and not is_general_query:
        if any(w in user_lower for w in ["trade", "setup", "buy", "stock", "entry", "target", "stop", "pick"]):
            is_universe_query = True

    active_ticker = explicit_ticker
    if is_market_query or is_earnings_calendar_query or is_universe_query:
        active_ticker = None
    elif is_general_query and not explicit_ticker:
        active_ticker = None
    elif not active_ticker:
        raw_req_t = (req.ticker or "").upper().strip()
        if raw_req_t not in ["", "ALL", "GENERAL", "NONE", "AUTO", "CUSTOM"]:
            active_ticker = raw_req_t

    if active_ticker:
        active_ticker = TICKER_ALIASES.get(active_ticker, active_ticker)

    async def sse_stream():
        tool_data = None
        universe_data = None
        macro_data = None
        earnings_calendar_data = None

        if is_earnings_calendar_query:
            yield f"data: {json.dumps({'type': 'tool_start', 'tool': 'get_earnings_calendar', 'ticker': 'EarningsHub Weekly Calendar'})}\n\n"
            try:
                earnings_calendar_data = await fetch_earningshub_week_analysis(last_user_text, target_date_str)
                stats = earnings_calendar_data.get("summary_stats") or {}
                tot = stats.get("total_companies_reporting", 0)
                win = earnings_calendar_data.get("week_window", "")
                top_syms = ", ".join(h.get("symbol", "") for h in (earnings_calendar_data.get("top_headliners") or [])[:5])
                yield f"data: {json.dumps({'type': 'tool_done', 'ticker': f'EarningsHub ({win})', 'summary': f'{tot} companies reporting | Top: {top_syms}'})}\n\n"
            except Exception as ece:
                logger.error(f"Error fetching EarningsHub weekly analysis: {ece}")
                earnings_calendar_data = {"error": f"Failed to retrieve EarningsHub calendar: {str(ece)}"}

        elif is_market_query:
            yield f"data: {json.dumps({'type': 'tool_start', 'tool': 'get_macro_snapshot', 'ticker': 'Major Indices & Sectors'})}\n\n"
            try:
                macro_data = await asyncio.to_thread(macro_snapshot)
                spy_tool = await execute_stock_tool("SPY")
                macro_data["spy_technical_framework"] = spy_tool
                risk_lbl = macro_data.get("risk", {}).get("label", "Active")
                spy_px = spy_tool.get("price", "N/A")
                yield f"data: {json.dumps({'type': 'tool_done', 'ticker': 'Major Indices & Sectors', 'summary': f'Market Risk: {risk_lbl} | SPY ${spy_px}'})}\n\n"
            except Exception as me:
                logger.error(f"Error fetching macro snapshot: {me}")
                macro_data = {"error": f"Failed to retrieve macro data: {str(me)}"}

        elif is_universe_query:
            yield f"data: {json.dumps({'type': 'tool_start', 'tool': 'get_universe_scan', 'ticker': 'Default 50 Universe'})}\n\n"
            universe_data = get_universe_tool("default")
            t_cnt = universe_data.get("total_tickers", 50)
            yield f"data: {json.dumps({'type': 'tool_done', 'ticker': 'Default 50 Universe', 'summary': f'Retrieved {t_cnt} tickers and top setups'})}\n\n"

        elif active_ticker:
            lbl_tkr = f"{active_ticker} ({target_date_str})" if target_date_str else active_ticker
            yield f"data: {json.dumps({'type': 'tool_start', 'tool': 'get_stock_analysis', 'ticker': lbl_tkr})}\n\n"
            try:
                tool_data = await execute_stock_tool(active_ticker, target_date=target_date_str)
                if req.entry_price:
                    tool_data["user_entry_price"] = req.entry_price
                    diff = tool_data["price"] - req.entry_price
                    diff_pct = (diff / req.entry_price) * 100
                    tool_data["user_unrealized_pnl"] = f"{diff:+.2f} ({diff_pct:+.2f}%)"

                summary_txt = tool_data.get("final_judgement", {}).get("summary")
                if target_date_str and tool_data.get("pulse", {}).get("backtest"):
                    bt = tool_data["pulse"]["backtest"]
                    bt_badge = bt.get("result_badge", "")
                    h4_tag = f" [4H: {bt['four_hour_structure']}]" if bt.get("four_hour_structure") and bt.get("four_hour_structure") != "N/A" else ""
                    summary_txt = f"Backtest ({target_date_str}): {bt_badge}{h4_tag} | {tool_data['pulse'].get('valid_outcome', '')}"
                yield f"data: {json.dumps({'type': 'tool_done', 'ticker': lbl_tkr, 'summary': summary_txt})}\n\n"
            except Exception as e:
                logger.error(f"Error executing stock tool for {active_ticker}: {e}")
                tool_data = {"error": f"Failed to retrieve data for {active_ticker}: {str(e)}"}

        # Prepare messages
        base_messages = [{"role": "system", "content": AGENT_SYSTEM_PROMPT}]
        for m in req.messages:
            base_messages.append({"role": m.role, "content": m.content})

        if earnings_calendar_data:
            base_messages.append({
                "role": "system",
                "content": f"EARNINGSHUB WEEKLY EARNINGS CALENDAR & INSTITUTIONAL ANALYSIS (https://earningshub.com/earnings-calendar/this-week):\n{json.dumps(earnings_calendar_data, indent=2)}"
            })
        elif macro_data:
            base_messages.append({
                "role": "system",
                "content": f"INSTITUTIONAL BROAD MARKET & MACRO DATA:\n{json.dumps(macro_data, indent=2)}"
            })
        elif universe_data:
            base_messages.append({
                "role": "system",
                "content": f"INSTITUTIONAL UNIVERSE DATA (DEFAULT 50 CORE WATCHLIST):\n{json.dumps(universe_data, indent=2)}"
            })
        elif tool_data:
            base_messages.append({
                "role": "system",
                "content": f"INSTITUTIONAL TECHNICAL & FUNDAMENTAL DATA FOR {active_ticker}:\n{json.dumps(tool_data, indent=2)}"
            })

        # Determine candidate providers based on user request
        candidate_chain = chain
        if req.model and req.model.lower().strip() not in ["auto", "default", "none", ""]:
            requested = req.model.lower().strip()
            matched = [p for p in chain if p["id"] == requested]
            if matched:
                candidate_chain = matched + [p for p in chain if p["id"] != requested]
            else:
                # If requested model is a cloud provider (groq, gemini, cerebras, openrouter)
                # but unconfigured, DO NOT overwrite local Ollama's model!
                known_cloud_ids = {"groq", "groq-120b", "gemini", "gemini-2", "cerebras", "openrouter"}
                if requested not in known_cloud_ids:
                    for p in candidate_chain:
                        if p["is_local"]:
                            p["model"] = req.model

        stream_succeeded = False
        last_error = ""

        client_timeout = httpx.Timeout(120.0, connect=10.0, read=120.0)
        async with httpx.AsyncClient(timeout=client_timeout) as client:
            for idx, provider in enumerate(candidate_chain):
                provider_id = provider["id"]
                provider_name = provider["name"]
                model_name = provider["model"]
                api_url = provider["url"]
                headers = provider["headers"]

                if idx > 0:
                    yield f"data: {json.dumps({'type': 'status_note', 'content': f'Failing over to {provider_name} ({model_name})...' })}\n\n"

                try:
                    if provider["is_local"]:
                        # Local Ollama native streaming
                        payload = {
                            "model": model_name,
                            "messages": base_messages,
                            "stream": True,
                        }
                        async with client.stream("POST", api_url, json=payload, headers=headers) as resp:
                            if resp.status_code != 200:
                                last_error = f"{provider_name} HTTP {resp.status_code}"
                                continue

                            async for line in resp.aiter_lines():
                                if not line:
                                    continue
                                try:
                                    chunk = json.loads(line)
                                    delta = chunk.get("message", {}).get("content", "")
                                    if delta:
                                        stream_succeeded = True
                                        yield f"data: {json.dumps({'type': 'token', 'content': delta})}\n\n"
                                    if chunk.get("done"):
                                        break
                                except Exception:
                                    pass

                    else:
                        # Cloud provider (Gemini, Groq, Cerebras, OpenRouter)
                        payload = {
                            "model": model_name,
                            "messages": base_messages,
                            "stream": False,
                        }
                        resp = await client.post(api_url, json=payload, headers=headers)
                        if resp.status_code != 200 and provider_id.startswith("gemini"):
                            # Try official native Google Gemini generateContent endpoint
                            gem_key = headers.get("x-goog-api-key") or get_env_flexible(["GEMINI", "GOOGLE_GEMINI", "GOOGLE_API"]) or ""
                            if gem_key:
                                nat_url = f"https://generativelanguage.googleapis.com/v1beta/models/{model_name}:generateContent?key={gem_key}"
                                nat_payload = {
                                    "contents": [
                                        {
                                            "role": "user" if m["role"] in ("user", "system") else "model",
                                            "parts": [{"text": m["content"]}],
                                        }
                                        for m in base_messages
                                    ]
                                }
                                nat_resp = await client.post(nat_url, json=nat_payload, headers={"Content-Type": "application/json"})
                                if nat_resp.status_code == 200:
                                    resp = nat_resp

                        if resp.status_code != 200:
                            err_body = resp.text[:120]
                            last_error = f"{provider_name} HTTP {resp.status_code}: {err_body}"
                            logger.warning(f"Provider {provider_name} failed: {last_error}")
                            continue

                        data = resp.json()
                        reply_content = ""
                        # Handle OpenAI format choices
                        choices = data.get("choices") or []
                        if choices:
                            reply_content = choices[0].get("message", {}).get("content", "")
                        # Handle native Gemini candidates if applicable
                        elif "candidates" in data:
                            cands = data.get("candidates") or []
                            if cands:
                                parts = cands[0].get("content", {}).get("parts") or []
                                reply_content = "".join(p.get("text", "") for p in parts)

                        if reply_content:
                            stream_succeeded = True
                            # Stream in small words/tokens for a lively typing effect
                            words = re.findall(r"\S+|\s+", reply_content)
                            for w in words:
                                yield f"data: {json.dumps({'type': 'token', 'content': w})}\n\n"
                                await asyncio.sleep(0.01)

                    if stream_succeeded:
                        break

                except Exception as ex:
                    last_error = f"{provider_name}: {type(ex).__name__} ({str(ex) or 'Connection error or timed out on CPU'})"
                    logger.warning(f"Exception calling {provider_name}: {ex}")
                    continue

        if not stream_succeeded:
            msg = f"All 5 AI providers failed or are unconfigured. Last error: {last_error}."
            yield f"data: {json.dumps({'type': 'error', 'content': msg})}\n\n"

        yield "data: [DONE]\n\n"

    return StreamingResponse(sse_stream(), media_type="text/event-stream")
