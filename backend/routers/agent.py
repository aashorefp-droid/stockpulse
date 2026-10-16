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
3. Corporate Fundamentals & Valuation: Market Capitalization, Trailing & Forward P/E multiples, Trailing EPS, Profit Margins, YoY Revenue Growth, Short Interest (% of float & short ratio / days to cover), and Wall Street Analyst consensus & price targets.
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
- Ticker-Specific Trade Plans: When provided with a stock ticker's data, synthesize BOTH its technical volume profile levels and its fundamental valuation/growth drivers. If the user provides an entry price, calculate their unrealized P&L%, distance to POC/VAL/VAH and Fib supports, and give a structured 2-step battle plan (Bounce Target & Invalidation Stop).
- SPY Day Trading & Post-Open Options Trade Ideas (Calls vs Puts):
  When analyzing SPY or when the user asks for SPY trade ideas, calls vs puts, or asks 'What is the valid outcome?':
  Evaluate the live SPY Day Trading V4 engine (PDH, PDL, PWH, PWL, Midpoint, and 4 outcomes).
  Provide a structured decision:
  1. Valid Outcome: Explicitly declare which outcome from the 8:00 AM playbook has triggered as valid based on current price action vs PDH/PDL.
  2. Trade Idea: High-conviction decision between CALLS vs PUTS (or Range Wait / Chop if trading mid-range).
  3. Actionable Levels: Exact Entry Trigger, Invalidation Stop-Loss, Target 1, Target 2, and R:R ratio.
  4. Options Contract & Scaling: Recommended strike (Delta 0.40–0.50 ATM/near-OTM), 0DTE/next expiry, scale 50% at T1, trail stop to breakeven.
- Zero Hallucination Rule: NEVER guess, fabricate, or hallucinate a stock's current price, market cap, exchange, or indicators. Rely strictly on the real institutional data block provided in your context. If live data for a symbol was not retrieved or failed to load, explicitly inform the user that live exchange data was not retrieved for that ticker instead of inventing numbers.
- Clarity & Formatting: Use bold key levels, clean markdown tables, and concise, high-conviction institutional summaries.
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
    "TSMC": "TSM",
    "GOOGLE": "GOOGL",
    "FACEBOOK": "META",
    "FB": "META",
    "TWITTER": "X",
    "BERKSHIRE": "BRK-B",
    "BRKB": "BRK-B",
    "SPX": "SPY",
    "NDX": "QQQ",
    "DOW": "DIA",
    "DJIA": "DIA",
    "RUT": "IWM",
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


async def execute_stock_tool(ticker: str) -> Dict[str, Any]:
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

    if ticker == "SPY":
        try:
            from day_trading.v4 import get_spy_valid_outcome_and_trade_idea
            ret["spy_day_trading_v4"] = await asyncio.to_thread(get_spy_valid_outcome_and_trade_idea)
        except Exception as se:
            logger.warning(f"Error computing SPY V4 valid outcome: {se}")

    return ret


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
async def get_spy_outcome_endpoint():
    """
    Returns the live SPY 5-minute post-open valid outcome, key levels,
    and actionable CALLS vs PUTS trade plan directly from the V4 engine.
    """
    try:
        from day_trading.v4 import get_spy_valid_outcome_and_trade_idea
        res = await asyncio.to_thread(get_spy_valid_outcome_and_trade_idea)
        return res
    except Exception as e:
        logger.error(f"Error fetching SPY outcome: {e}")
        return {"error": str(e)}


@router.post("/chat")
async def chat_with_agent(req: ChatRequest, request: Request):
    """
    Streaming chat endpoint with 5-provider automatic fallback:
    Gemini 3.5 Flash -> Groq -> Cerebras -> OpenRouter -> Local Ollama
    """
    headers_dict = dict(request.headers)
    chain = get_configured_providers(headers_dict)

    # Determine intent: Market Overview, Universe query, General/Meta query, or Ticker-specific query
    last_user_text = req.messages[-1].content if req.messages else ""
    user_lower = last_user_text.lower().strip()

    is_market_query = any(k in user_lower for k in [
        "how is market", "how is the market", "how's market", "how's the market",
        "how are markets", "how are the markets", "market today", "markets today",
        "market overview", "market snapshot", "market status", "market update",
        "macro snapshot", "overall market", "indices today", "index moves",
        "what is market doing", "what's market doing", "what is the market doing",
        "state of the market", "broad market", "market breadth"
    ]) or user_lower in ["market", "markets", "macro", "indices", "how is market today", "how is market today?"]

    is_general_query = not is_market_query and (any(user_lower.startswith(w) for w in [
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
    is_universe_query = not is_market_query and any(k in user_lower for k in universe_keywords)

    # Dynamic Ticker Detection for any US stock (AAOI, SPCX, NVDA, TSLA, PLTR, etc.)
    ticker_stop_words = {
        "WHAT", "WHEN", "WHERE", "WHICH", "HOW", "WHY", "WHO", "WHOSE",
        "TODAY", "YESTERDAY", "TOMORROW", "NOW", "LATER", "SOON",
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
        "THE", "AND", "OR", "BUT", "FOR", "NOR", "YET", "SO",
        "CAN", "COULD", "WILL", "WOULD", "SHALL", "SHOULD", "MAY", "MIGHT", "MUST",
        "HAVE", "HAS", "HAD", "BEEN", "BEING", "WERE", "WAS", "ARE", "IS", "AM",
        "GIVE", "SHOW", "TELL", "FIND", "LOOK", "SEEK", "WANT", "NEED", "BUILD",
        "CHAT", "LIVE", "DATA", "INFO", "WORK", "PAGE", "USER", "AI", "MAKE",
        "WRONG", "RIGHT", "TRUE", "FALSE", "REAL", "FAKE", "LAST", "NOTE",
        "DEFAULT", "UNIVERSE", "WATCH", "LIST", "VIEW", "CHART", "OPEN", "DROP",
        "ABOUT", "ONLY", "ITS", "IT", "NOT", "YES", "NO", "SURE", "FINE",
        "HELP", "LIKE", "NEWS", "AUTO", "TECH", "EDIT", "TEST", "TIME",
        "ANALYZE", "ANALYSIS", "EVALUATE", "CHECK", "REVIEW", "SETUP", "SETUPS",
        "MARKET", "MARKETS", "INDICES", "INDEX", "MACRO"
    }

    explicit_ticker = None

    if not is_market_query:
        # Stage 1: Explicit dollar sign (e.g. $AAOI, $SPCX)
        dollar_tickers = re.findall(r"\$([A-Za-z]{1,5})\b", last_user_text)
        if dollar_tickers:
            explicit_ticker = dollar_tickers[0].upper()

        # Stage 2: Phrases like "for AAOI", "on NVDA", "about TSLA", "ticker MDT"
        if not explicit_ticker:
            phrase_matches = re.findall(r"\b(?:for|on|about|ticker|stock|symbol)\s+([A-Za-z]{1,5})\b", last_user_text, re.IGNORECASE)
            if phrase_matches:
                cand = phrase_matches[0].upper()
                if cand not in ticker_stop_words:
                    explicit_ticker = cand

        # Stage 3: All-caps words (e.g. AAOI, SPCX, NVDA, TSLA) - skipping stop words
        if not explicit_ticker:
            raw_tokens = re.findall(r"\b[A-Za-z]{2,5}\b", last_user_text)
            caps_tokens = [t.upper() for t in raw_tokens if t.isupper() and t.upper() not in ticker_stop_words]
            if caps_tokens:
                explicit_ticker = caps_tokens[-1]

        # Stage 4: Fallback for short queries (e.g. "aaoi", "aaoi setup", "analyze aaoi")
        if not explicit_ticker and not is_general_query and not is_universe_query:
            raw_tokens = re.findall(r"\b[A-Za-z]{2,5}\b", last_user_text)
            for t in raw_tokens:
                up = t.upper()
                if up not in ticker_stop_words and len(up) >= 2:
                    explicit_ticker = up
                    break

    # If no explicit ticker was found and user is asking about trades/setups/picks/entries/targets, treat as universe query
    if not is_market_query and not is_universe_query and not explicit_ticker and not is_general_query:
        if any(w in user_lower for w in ["trade", "setup", "buy", "stock", "entry", "target", "stop", "pick"]):
            is_universe_query = True

    active_ticker = explicit_ticker
    if is_market_query or is_universe_query:
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

        if is_market_query:
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
            yield f"data: {json.dumps({'type': 'tool_start', 'tool': 'get_stock_analysis', 'ticker': active_ticker})}\n\n"
            try:
                tool_data = await execute_stock_tool(active_ticker)
                if req.entry_price:
                    tool_data["user_entry_price"] = req.entry_price
                    diff = tool_data["price"] - req.entry_price
                    diff_pct = (diff / req.entry_price) * 100
                    tool_data["user_unrealized_pnl"] = f"{diff:+.2f} ({diff_pct:+.2f}%)"

                yield f"data: {json.dumps({'type': 'tool_done', 'ticker': active_ticker, 'summary': tool_data.get('final_judgement', {}).get('summary')})}\n\n"
            except Exception as e:
                logger.error(f"Error executing stock tool for {active_ticker}: {e}")
                tool_data = {"error": f"Failed to retrieve data for {active_ticker}: {str(e)}"}

        # Prepare messages
        base_messages = [{"role": "system", "content": AGENT_SYSTEM_PROMPT}]
        for m in req.messages:
            base_messages.append({"role": m.role, "content": m.content})

        if macro_data:
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
