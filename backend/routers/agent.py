import os
import re
import json
import logging
import httpx
from typing import Optional, List, Dict, Any
from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from backend.routers.analysis import get_stock_analysis

logger = logging.getLogger("agent")

router = APIRouter(prefix="/api/agent", tags=["agent"])

OLLAMA_HOST = os.getenv("OLLAMA_HOST", "http://localhost:11434")
DEFAULT_OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "llama3.2:3b")

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
    """Checks if Groq (ultra fast free tier), OpenRouter, or local Ollama is active."""
    groq_key = os.getenv("GROQ_API_KEY", "").strip()
    openrouter_key = os.getenv("OPENROUTER_API_KEY", "").strip()

    if groq_key:
        return {
            "provider": "groq",
            "available": True,
            "recommended_model": "llama-3.3-70b-versatile",
            "label": "Groq Cloud (800 t/s · Llama 3.3 70B)",
        }

    if openrouter_key:
        return {
            "provider": "openrouter",
            "available": True,
            "recommended_model": "meta-llama/llama-3.3-70b-instruct:free",
            "label": "OpenRouter Free (Llama 3.3 70B)",
        }

    try:
        async with httpx.AsyncClient(timeout=2.0) as client:
            resp = await client.get(f"{OLLAMA_HOST}/api/tags")
            if resp.status_code == 200:
                data = resp.json()
                models = [m.get("name") for m in data.get("models", [])]
                return {
                    "provider": "ollama",
                    "available": True,
                    "host": OLLAMA_HOST,
                    "models": models,
                    "recommended_model": DEFAULT_OLLAMA_MODEL if DEFAULT_OLLAMA_MODEL in models else (models[0] if models else DEFAULT_OLLAMA_MODEL),
                    "label": f"Local Ollama ({DEFAULT_OLLAMA_MODEL})",
                }
    except Exception:
        pass

    return {
        "provider": "none",
        "available": False,
        "label": "No provider connected",
        "message": "Add free GROQ_API_KEY in backend/.env for 800 t/s speed, or run Ollama locally."
    }


@router.post("/chat")
async def chat_with_agent(req: ChatRequest):
    """
    Real-time streaming agent chat with tool-calling for stock analysis.
    Supports Groq (free 800 t/s Llama 3.3 70B), OpenRouter, and Local Ollama.
    """
    groq_key = os.getenv("GROQ_API_KEY", "").strip()
    openrouter_key = os.getenv("OPENROUTER_API_KEY", "").strip()

    if groq_key:
        provider = "groq"
        api_url = "https://api.groq.com/openai/v1/chat/completions"
        auth_header = {"Authorization": f"Bearer {groq_key}", "Content-Type": "application/json"}
        model_name = req.model or "llama-3.3-70b-versatile"
    elif openrouter_key:
        provider = "openrouter"
        api_url = "https://openrouter.ai/api/v1/chat/completions"
        auth_header = {"Authorization": f"Bearer {openrouter_key}", "Content-Type": "application/json"}
        model_name = req.model or "meta-llama/llama-3.3-70b-instruct:free"
    else:
        provider = "ollama"
        api_url = f"{OLLAMA_HOST}/api/chat"
        auth_header = {"Content-Type": "application/json"}
        model_name = req.model or DEFAULT_OLLAMA_MODEL

    formatted_messages = [{"role": "system", "content": AGENT_SYSTEM_PROMPT}]
    for m in req.messages:
        formatted_messages.append({"role": m.role, "content": m.content})

    async def sse_stream():
        async with httpx.AsyncClient(timeout=60.0) as client:
            # 1. First call to determine tool-calling
            if provider in ["groq", "openrouter"]:
                payload = {
                    "model": model_name,
                    "messages": formatted_messages,
                    "tools": AGENT_TOOLS,
                    "tool_choice": "auto",
                    "stream": False,
                }
            else:
                payload = {
                    "model": model_name,
                    "messages": formatted_messages,
                    "tools": AGENT_TOOLS,
                    "stream": False,
                }

            try:
                resp = await client.post(api_url, json=payload, headers=auth_header)
            except Exception as e:
                err_msg = f"Cannot reach AI engine ({provider}): {str(e)}."
                yield f"data: {json.dumps({'type': 'error', 'content': err_msg})}\n\n"
                yield "data: [DONE]\n\n"
                return

            if resp.status_code != 200:
                yield f"data: {json.dumps({'type': 'error', 'content': f'{provider} error ({resp.status_code}): {resp.text}'})}\n\n"
                yield "data: [DONE]\n\n"
                return

            data = resp.json()

            # Parse tool calls across providers
            if provider in ["groq", "openrouter"]:
                choice = (data.get("choices") or [{}])[0]
                assistant_msg = choice.get("message", {})
                tool_calls = assistant_msg.get("tool_calls", [])
            else:
                assistant_msg = data.get("message", {})
                tool_calls = assistant_msg.get("tool_calls", [])

            # Check if user mentioned a ticker in context or query
            active_ticker = req.ticker
            if not tool_calls and len(req.messages) > 0:
                last_user_text = req.messages[-1].content
                dollar_tickers = re.findall(r"\$([A-Za-z]{1,5})\b", last_user_text)
                inferred = dollar_tickers[0].upper() if dollar_tickers else None

                if not inferred:
                    stop_words = {
                        "WHAT", "HOW", "WHEN", "WHERE", "WHY", "TODAY", "SELL", "BUY", "HOLD",
                        "CALL", "PUTS", "TRADE", "PLAN", "RISK", "PRICE", "AI", "LEVEL", "STOP",
                        "LOSS", "THE", "AND", "FOR", "CAN", "YOU", "SEE", "GOOD", "BAD", "ENTRY"
                    }
                    caps = [w for w in re.findall(r"\b[A-Z]{2,5}\b", last_user_text) if w not in stop_words]
                    if caps:
                        inferred = caps[0].upper()

                if not inferred and active_ticker:
                    inferred = active_ticker.upper()

                if inferred:
                    user_upper = last_user_text.upper()
                    trigger_keywords = [
                        "ANALYZE", "LOOKING", "SHOULD I", "BOUGHT", "ENTERED", "SUPPORT",
                        "RESISTANCE", "SETUP", "TARGET", "LEVELS", "FIB", "HOW IS", "HOW DID",
                        "WHAT ABOUT", "OPINION", "THINK OF", "REVIEW", "CHART", "STOCK"
                    ]
                    if active_ticker or any(k in user_upper for k in trigger_keywords) or dollar_tickers:
                        tool_calls = [{
                            "id": "call_stock_tool",
                            "type": "function",
                            "function": {
                                "name": "get_stock_technical_analysis",
                                "arguments": json.dumps({"ticker": inferred}) if provider != "ollama" else {"ticker": inferred}
                            }
                        }]
                        active_ticker = inferred

            # 2. Execute requested tools
            if tool_calls:
                for tc in tool_calls:
                    fn = tc.get("function", {})
                    fn_name = fn.get("name")
                    fn_args = fn.get("arguments", {})
                    if isinstance(fn_args, str):
                        try:
                            fn_args = json.loads(fn_args)
                        except Exception:
                            fn_args = {}
                    target_ticker = fn_args.get("ticker") or active_ticker or "SPY"

                    yield f"data: {json.dumps({'type': 'tool_start', 'tool': fn_name, 'ticker': target_ticker})}\n\n"

                    try:
                        tool_data = await execute_stock_tool(target_ticker)
                        if req.entry_price:
                            tool_data["user_entry_price"] = req.entry_price
                            diff = tool_data["price"] - req.entry_price
                            diff_pct = (diff / req.entry_price) * 100
                            tool_data["user_unrealized_pnl"] = f"{diff:+.2f} ({diff_pct:+.2f}%)"

                        if provider in ["groq", "openrouter"]:
                            formatted_messages.append(assistant_msg)
                            formatted_messages.append({
                                "role": "tool",
                                "tool_call_id": tc.get("id", "call_stock_tool"),
                                "content": json.dumps(tool_data)
                            })
                        else:
                            formatted_messages.append(assistant_msg if assistant_msg.get("content") or assistant_msg.get("tool_calls") else {
                                "role": "assistant",
                                "content": f"Looking up institutional technical analysis for {target_ticker}..."
                            })
                            formatted_messages.append({
                                "role": "tool",
                                "content": json.dumps(tool_data)
                            })

                        yield f"data: {json.dumps({'type': 'tool_done', 'ticker': target_ticker, 'summary': tool_data.get('final_judgement', {}).get('summary')})}\n\n"
                    except Exception as e:
                        logger.error(f"Error executing stock tool: {e}")
                        formatted_messages.append({
                            "role": "tool",
                            "content": json.dumps({"error": f"Failed to retrieve data for {target_ticker}: {str(e)}"})
                        })

                # 3. Stream final synthesized response
                synth_payload = {
                    "model": model_name,
                    "messages": formatted_messages,
                    "stream": True,
                }

                async with client.stream("POST", api_url, json=synth_payload, headers=auth_header) as stream_resp:
                    async for line in stream_resp.aiter_lines():
                        if not line:
                            continue

                        if provider in ["groq", "openrouter"]:
                            if line.startswith("data: "):
                                chunk_str = line.replace("data: ", "").strip()
                                if chunk_str == "[DONE]":
                                    break
                                try:
                                    chunk = json.loads(chunk_str)
                                    delta = (chunk.get("choices") or [{}])[0].get("delta", {})
                                    delta_text = delta.get("content", "")
                                    if delta_text:
                                        yield f"data: {json.dumps({'type': 'token', 'content': delta_text})}\n\n"
                                except Exception:
                                    pass
                        else:
                            try:
                                chunk = json.loads(line)
                                delta_text = chunk.get("message", {}).get("content", "")
                                if delta_text:
                                    yield f"data: {json.dumps({'type': 'token', 'content': delta_text})}\n\n"
                                if chunk.get("done"):
                                    break
                            except Exception:
                                pass
            else:
                # Direct reply without tools
                content = assistant_msg.get("content", "")
                if content:
                    yield f"data: {json.dumps({'type': 'token', 'content': content})}\n\n"

            yield "data: [DONE]\n\n"

    return StreamingResponse(sse_stream(), media_type="text/event-stream")
