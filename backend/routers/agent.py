import os
import re
import json
import logging
import asyncio
from pathlib import Path
from typing import Optional, List, Dict, Any
from fastapi import APIRouter
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
from backend.services.scanner import WATCHLISTS

logger = logging.getLogger("agent")

router = APIRouter(prefix="/api/agent", tags=["agent"])

AGENT_SYSTEM_PROMPT = """You are StockPulse AI, an institutional-grade swing trader, market mentor, and quantitative risk analyst.
You have access to StockPulse's comprehensive multi-module data engine:
1. Technical Framework: Wyckoff Volume Profile (Point of Control / POC, Value Area High / VAH, Value Area Low / VAL), 52-Week Fibonacci Retracements (61.8% Golden Ratio, 50%, 38.2%), and Stage 2 trend momentum.
2. Systematic Risk Modeling: Beta vs SPY (systematic volatility scaling), Relative Strength line (RS 1-month trajectory), and ATR-based volatility targets.
3. Corporate Fundamentals & Valuation: Market Capitalization, Trailing & Forward P/E multiples, Trailing EPS, Profit Margins, YoY Revenue Growth, Short Interest (% of float & short ratio / days to cover), and Wall Street Analyst consensus & price targets.
4. Core Universe / Default 50 Watchlist: StockPulse's curated 50 high-liquidity stocks and ETFs (AAPL, MSFT, GOOGL, AMZN, NVDA, META, TSLA, AMD, NFLX, CRM, COIN, etc.).

Your Response Guidelines:
- General Questions & Mentorship: If the user asks general trading questions, asks for explanations of indicators, or asks conceptual or platform questions, answer thoroughly, conversationally, and insightfully as an institutional trading mentor.
- Universe / Watchlist Questions: If the user asks about the Default 50 universe, screeners, or top swing setups, analyze the provided universe setups and rank them by confluence, risk/reward, and trend alignment.
- Ticker-Specific Trade Plans: When provided with a stock ticker's data, synthesize BOTH its technical volume profile levels and its fundamental valuation/growth drivers. If the user provides an entry price, calculate their unrealized P&L%, distance to POC/VAL/VAH and Fib supports, and give a structured 2-step battle plan (Bounce Target & Invalidation Stop).
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
    clean = val.strip().strip('"').strip("'")
    if clean in [
        "GROQ_API_KEY", "GEMINI_API_KEY", "CEREBRAS_API_KEY",
        "OPENROUTER_API_KEY", "your_key_here", "gsk_your_actual_key_here"
    ]:
        return False
    if prefix and not clean.startswith(prefix):
        return False
    return len(clean) >= 12


def get_configured_providers() -> List[Dict[str, Any]]:
    """
    Returns active AI providers in priority order:
    1. Groq Cloud (Recommended: ~800 t/s)
    2. Google Gemini (Live tested with user key: ~150 t/s)
    3. Cerebras Cloud (~1,800 t/s)
    4. OpenRouter (Free models: nemotron-3.5-lightning:free / liquid:free)
    5. Local Ollama (Always included as offline fallback)
    """
    groq_key = os.getenv("GROQ_API_KEY", "").strip()
    gemini_key = os.getenv("GEMINI_API_KEY", "").strip()
    cerebras_key = os.getenv("CEREBRAS_API_KEY", "").strip()
    openrouter_key = os.getenv("OPENROUTER_API_KEY", "").strip()
    ollama_host = os.getenv("OLLAMA_HOST", "http://localhost:11434").strip()
    ollama_model = os.getenv("OLLAMA_MODEL", "llama3.2:3b").strip()

    chain = []

    # Priority 1: Groq Cloud
    if is_valid_key(groq_key, prefix="gsk_"):
        chain.append({
            "id": "groq",
            "name": "Groq Cloud",
            "model": "llama-3.3-70b-versatile",
            "url": "https://api.groq.com/openai/v1/chat/completions",
            "headers": {
                "Authorization": f"Bearer {groq_key}",
                "Content-Type": "application/json"
            },
            "speed": "~800 t/s",
            "is_local": False
        })

    # Priority 2: Google Gemini (Active with user's key)
    if is_valid_key(gemini_key):
        chain.append({
            "id": "gemini",
            "name": "Google Gemini",
            "model": "gemini-3.8-flash",
            "url": "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions",
            "headers": {
                "Authorization": f"Bearer {gemini_key}",
                "Content-Type": "application/json"
            },
            "speed": "~150 t/s",
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
    if is_valid_key(openrouter_key, prefix="sk-or-v1-"):
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

    return {
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
    }


def get_universe_tool(watchlist_name: str = "default") -> Dict[str, Any]:
    name = (watchlist_name or "default").lower().strip()
    tickers = WATCHLISTS.get(name, WATCHLISTS.get("default", []))

    # Read cached scan items if available
    data_dir = Path(__file__).resolve().parent.parent.parent / "data"
    cache_path = data_dir / "default50_premarket_cache.json"
    cached_setups = []
    if cache_path.exists():
        try:
            with open(cache_path, "r", encoding="utf-8") as fp:
                cdata = json.load(fp)
                cached_setups = cdata.get("items", [])
        except Exception:
            pass

    top_picks = []
    for s in cached_setups[:8]:
        top_picks.append({
            "ticker": s.get("ticker"),
            "verdict": s.get("verdict"),
            "score": s.get("score"),
            "price": s.get("price"),
            "entry": s.get("entry"),
            "stop": s.get("stop"),
            "target1": s.get("t1"),
            "reward_risk": s.get("rr_t1"),
        })

    return {
        "universe_name": f"Default 50 Core Universe ({len(tickers)} stocks)",
        "sample_tickers": tickers,
        "total_tickers": len(tickers),
        "cached_top_setups": top_picks if top_picks else "No premarket cache file found; all 50 tickers are tracked in real-time.",
        "note": "Default 50 consists of 50 high-beta and mega-cap leaders. The best setups feature Stage 2 breakout momentum, confluence score >= 2, and favorable R:R."
    }


@router.get("/status")
async def get_agent_status():
    """
    Returns active primary AI provider and all configured fallbacks.
    """
    chain = get_configured_providers()
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
    if primary and primary["id"] == "groq":
        primary_label = "🟢 Groq Cloud (~800 t/s · Llama 3.3 70B)"
    elif primary and primary["id"] == "gemini":
        primary_label = "🟢 Google Gemini (~150 t/s · Flash)"
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
    }


@router.post("/chat")
async def chat_with_agent(req: ChatRequest):
    """
    Streaming chat endpoint with 5-provider automatic fallback:
    Groq -> Gemini Flash -> Cerebras -> OpenRouter -> Local Ollama
    """
    chain = get_configured_providers()

    # Determine intent: Universe query, General/Meta query, or Ticker-specific query
    last_user_text = req.messages[-1].content if req.messages else ""
    user_lower = last_user_text.lower().strip()

    is_universe_query = any(k in user_lower for k in [
        "default 50", "default50", "watchlist", "best swing", "top pick",
        "top swing", "screener", "scanner", "rank", "which stock", "best trade",
        "best ticker", "universe"
    ])

    is_general_query = any(user_lower.startswith(w) for w in [
        "hello", "hi", "hey", "who are you", "what is", "how do", "explain",
        "help", "why is chat", "what can you do", "tell me about", "what are"
    ]) or "only aaoi" in user_lower or "general chat" in user_lower

    # Explicit ticker mentioned in text
    dollar_tickers = re.findall(r"\$([A-Za-z]{1,5})\b", last_user_text)
    explicit_ticker = None
    if dollar_tickers:
        explicit_ticker = dollar_tickers[0].upper()
    else:
        tokens = re.findall(r"\b[A-Za-z]{2,5}\b", last_user_text)
        known_stocks = set(WATCHLISTS.get("default", []) + ["AAOI", "MDT", "XLF", "SPY", "QQQ", "IWM"])
        stop_words = {
            "WHAT", "HOW", "WHEN", "WHERE", "WHY", "TODAY", "SELL", "BUY", "HOLD",
            "CALL", "PUTS", "TRADE", "PLAN", "RISK", "PRICE", "AI", "LEVEL", "STOP",
            "LOSS", "THE", "AND", "FOR", "CAN", "YOU", "SEE", "GOOD", "BAD", "ENTRY",
            "FROM", "BEST", "ONLY", "INFO", "WORK"
        }
        for token in tokens:
            up = token.upper()
            if up in known_stocks and up not in stop_words:
                explicit_ticker = up
                break

    active_ticker = explicit_ticker
    if is_general_query or is_universe_query:
        active_ticker = None
    elif not active_ticker:
        raw_req_t = (req.ticker or "").upper().strip()
        if raw_req_t not in ["", "ALL", "GENERAL", "NONE", "AUTO", "CUSTOM"]:
            active_ticker = raw_req_t

    async def sse_stream():
        tool_data = None
        universe_data = None

        if is_universe_query:
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

        if universe_data:
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
                        # Cloud provider (Groq, Gemini, Cerebras, OpenRouter)
                        # We try non-streaming first or direct streaming:
                        payload = {
                            "model": model_name,
                            "messages": base_messages,
                            "stream": False,
                        }
                        resp = await client.post(api_url, json=payload, headers=headers)
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
