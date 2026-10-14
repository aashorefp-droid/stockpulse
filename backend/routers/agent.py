import os
import re
import json
import logging
import httpx
from typing import Optional, List, Dict, Any
from fastapi import APIRouter
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from backend.routers.analysis import get_stock_analysis

logger = logging.getLogger("agent")

router = APIRouter(prefix="/api/agent", tags=["agent"])

AGENT_SYSTEM_PROMPT = """You are StockPulse AI, an institutional-grade swing trader and risk analyst.
You specialize in Wyckoff Volume Profile, Fibonacci Retracements, ATR targets, and Systematic Risk (Beta).

Your Methodology:
1. Volume Profile: Evaluate Point of Control (POC), Value Area High (VAH), and Value Area Low (VAL). Identify Bullish Accumulation vs Bearish Distribution.
2. Fibonacci Retracements: Check 52-week Golden Ratio (61.8%), 38.2%, and 50% levels.
3. Risk Management: If the user provides an entry price (e.g. "I entered at 97.78"), calculate their exact unrealized P&L%, distance to the nearest support/resistance, and give a 2-step battle plan:
   - Bounce Target: where to trim/reduce risk (usually POC or nearest Fib).
   - Invalidation Level: the line in the sand (usually below VAL).

Rules:
- Be concise, direct, and actionable.
- Format numbers in clean markdown tables and bold levels.
- Never guess or hallucinate indicators. Rely strictly on the provided technical data.
"""

AGENT_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "get_stock_technical_analysis",
            "description": "Fetches live technical analysis, Volume Profile (POC, VAL, VAH), 52W Fibonacci levels, Beta, and Trade Plan (Entry, Stop Loss, Targets) for a stock ticker.",
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


def get_configured_providers() -> List[Dict[str, Any]]:
    """
    Returns the 5-provider fallback chain in priority order:
    1. Groq (~800 t/s, 100% Free): https://console.groq.com
    2. Google Gemini Flash (1,500 req/day Free): https://aistudio.google.com
    3. Cerebras (~1,800 t/s Free Tier): https://cloud.cerebras.ai
    4. OpenRouter (Free community models): https://openrouter.ai
    5. Local Ollama (Offline fallback on your machine): http://localhost:11434
    """
    groq_key = os.getenv("GROQ_API_KEY", "").strip()
    gemini_key = os.getenv("GEMINI_API_KEY", "").strip()
    cerebras_key = os.getenv("CEREBRAS_API_KEY", "").strip()
    openrouter_key = os.getenv("OPENROUTER_API_KEY", "").strip()
    ollama_host = os.getenv("OLLAMA_HOST", "http://localhost:11434").strip()
    ollama_model = os.getenv("OLLAMA_MODEL", "llama3.2:3b").strip()

    chain = []

    # Priority 1: Groq Cloud (Recommended: ~800 t/s)
    if groq_key:
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

    # Priority 2: Google Gemini Flash (1,500 req/day)
    if gemini_key:
        chain.append({
            "id": "gemini",
            "name": "Google Gemini",
            "model": "gemini-1.5-flash",
            "url": "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions",
            "headers": {
                "Authorization": f"Bearer {gemini_key}",
                "Content-Type": "application/json"
            },
            "speed": "~150 t/s",
            "is_local": False
        })

    # Priority 3: Cerebras Cloud (~1,800 t/s)
    if cerebras_key:
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
    if openrouter_key:
        chain.append({
            "id": "openrouter",
            "name": "OpenRouter",
            "model": "meta-llama/llama-3.3-70b-instruct:free",
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
    return {
        "ticker": ticker,
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
        "nearest_fib": raw.get("nearest_fib"),
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
            status = "🟢 Configured"
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
        primary_label = "🟢 Google Gemini Flash (~150 t/s)"
    elif primary and primary["id"] == "cerebras":
        primary_label = "🟢 Cerebras Cloud (~1,800 t/s)"
    elif primary and primary["id"] == "openrouter":
        primary_label = "🟢 OpenRouter (Llama 3.3 70B Free)"
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

    # Determine ticker if mentioned in prompt or request
    active_ticker = req.ticker
    if len(req.messages) > 0:
        last_user_text = req.messages[-1].content
        dollar_tickers = re.findall(r"\$([A-Za-z]{1,5})\b", last_user_text)
        if dollar_tickers:
            active_ticker = dollar_tickers[0].upper()
        elif not active_ticker:
            stop_words = {
                "WHAT", "HOW", "WHEN", "WHERE", "WHY", "TODAY", "SELL", "BUY", "HOLD",
                "CALL", "PUTS", "TRADE", "PLAN", "RISK", "PRICE", "AI", "LEVEL", "STOP",
                "LOSS", "THE", "AND", "FOR", "CAN", "YOU", "SEE", "GOOD", "BAD", "ENTRY"
            }
            caps = [w for w in re.findall(r"\b[A-Z]{2,5}\b", last_user_text) if w not in stop_words]
            if caps:
                active_ticker = caps[0].upper()

    async def sse_stream():
        # Execute stock tool if ticker is relevant
        tool_data = None
        if active_ticker:
            yield f"data: {json.dumps({'type': 'tool_start', 'tool': 'get_stock_technical_analysis', 'ticker': active_ticker})}\n\n"
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

        if tool_data:
            base_messages.append({
                "role": "system",
                "content": f"INSTITUTIONAL TECHNICAL DATA FOR {active_ticker}:\n{json.dumps(tool_data, indent=2)}"
            })

        # Determine candidate providers based on user request
        candidate_chain = chain
        if req.model:
            requested = req.model.lower().strip()
            matched = [p for p in chain if p["id"] == requested]
            if matched:
                # Put requested provider first, keeping remaining as fallback
                candidate_chain = matched + [p for p in chain if p["id"] != requested]
            else:
                # Direct model name requested (e.g. llama3.2:1b)
                for p in candidate_chain:
                    if p["is_local"]:
                        p["model"] = req.model

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
                        # Cloud OpenAI-compatible streaming (Groq, Gemini, Cerebras, OpenRouter)
                        payload = {
                            "model": model_name,
                            "messages": base_messages,
                            "stream": True,
                        }
                        async with client.stream("POST", api_url, json=payload, headers=headers) as resp:
                            if resp.status_code != 200:
                                err_body = await resp.aread()
                                last_error = f"{provider_name} HTTP {resp.status_code}: {err_body.decode(errors='ignore')[:120]}"
                                logger.warning(f"Provider {provider_name} failed: {last_error}")
                                continue

                            async for line in resp.aiter_lines():
                                if not line:
                                    continue
                                if line.startswith("data: "):
                                    chunk_str = line.replace("data: ", "").strip()
                                    if chunk_str == "[DONE]":
                                        break
                                    try:
                                        chunk = json.loads(chunk_str)
                                        delta = (chunk.get("choices") or [{}])[0].get("delta", {})
                                        delta_text = delta.get("content", "")
                                        if delta_text:
                                            stream_succeeded = True
                                            yield f"data: {json.dumps({'type': 'token', 'content': delta_text})}\n\n"
                                    except Exception:
                                        pass

                    if stream_succeeded:
                        break

                except Exception as ex:
                    last_error = f"{provider_name}: {type(ex).__name__} ({str(ex) or 'Connection error or timed out on CPU'})"
                    logger.warning(f"Exception calling {provider_name}: {ex}")
                    continue

        if not stream_succeeded:
            msg = f"All 5 AI providers failed or are unconfigured. Last error: {last_error}. To get instant (~800 t/s) responses, add a 100% free GROQ_API_KEY (from https://console.groq.com) into backend/.env and restart backend."
            yield f"data: {json.dumps({'type': 'error', 'content': msg})}\n\n"

        yield "data: [DONE]\n\n"

    return StreamingResponse(sse_stream(), media_type="text/event-stream")
