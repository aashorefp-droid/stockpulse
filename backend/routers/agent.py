import os
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
3. Risk Management: If the user provides an entry price (e.g. "I entered at 116"), calculate their exact unrealized P&L%, distance to the nearest support/resistance, and give a 2-step battle plan:
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
    """Checks if local Ollama or cloud model provider is reachable."""
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
                    "recommended_model": DEFAULT_OLLAMA_MODEL if DEFAULT_OLLAMA_MODEL in models else (models[0] if models else DEFAULT_OLLAMA_MODEL)
                }
    except Exception:
        pass

    has_gemini = bool(os.getenv("GEMINI_API_KEY"))
    has_groq = bool(os.getenv("GROQ_API_KEY"))
    return {
        "provider": "cloud" if (has_gemini or has_groq) else "none",
        "available": bool(has_gemini or has_groq),
        "has_gemini": has_gemini,
        "has_groq": has_groq,
        "message": "Local Ollama not reachable; configure cloud API key on Render or start Ollama locally."
    }


@router.post("/chat")
async def chat_with_agent(req: ChatRequest):
    """
    Real-time streaming agent chat with tool-calling for stock analysis.
    Streams Server-Sent Events (SSE).
    """
    model_name = req.model or DEFAULT_OLLAMA_MODEL

    formatted_messages = [{"role": "system", "content": AGENT_SYSTEM_PROMPT}]
    for m in req.messages:
        formatted_messages.append({"role": m.role, "content": m.content})

    async def sse_stream():
        async with httpx.AsyncClient(timeout=60.0) as client:
            # 1. First call to Ollama to see if it wants to invoke a tool
            payload = {
                "model": model_name,
                "messages": formatted_messages,
                "tools": AGENT_TOOLS,
                "stream": False,
            }

            try:
                resp = await client.post(f"{OLLAMA_HOST}/api/chat", json=payload)
            except Exception as e:
                err_msg = f"Cannot connect to Ollama at {OLLAMA_HOST}: {str(e)}. Make sure Ollama is running (`ollama run {model_name}`)."
                yield f"data: {json.dumps({'type': 'error', 'content': err_msg})}\n\n"
                yield "data: [DONE]\n\n"
                return

            if resp.status_code != 200:
                yield f"data: {json.dumps({'type': 'error', 'content': f'Ollama error ({resp.status_code}): {resp.text}'})}\n\n"
                yield "data: [DONE]\n\n"
                return

            data = resp.json()
            assistant_msg = data.get("message", {})
            tool_calls = assistant_msg.get("tool_calls", [])

            # Check if user mentioned a ticker in context or query
            active_ticker = req.ticker
            if not tool_calls and active_ticker and len(req.messages) > 0:
                last_user_text = req.messages[-1].content.upper()
                if active_ticker.upper() in last_user_text or "LOOKING" in last_user_text or "SHOULD I" in last_user_text or "BOUGHT" in last_user_text:
                    tool_calls = [{
                        "function": {
                            "name": "get_stock_technical_analysis",
                            "arguments": {"ticker": active_ticker}
                        }
                    }]

            # 2. Execute any requested tools
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

                    # Emit tool notification to user
                    yield f"data: {json.dumps({'type': 'tool_start', 'tool': fn_name, 'ticker': target_ticker})}\n\n"

                    try:
                        tool_data = await execute_stock_tool(target_ticker)
                        if req.entry_price:
                            tool_data["user_entry_price"] = req.entry_price
                            diff = tool_data["price"] - req.entry_price
                            diff_pct = (diff / req.entry_price) * 100
                            tool_data["user_unrealized_pnl"] = f"{diff:+.2f} ({diff_pct:+.2f}%)"

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

                # 3. Stream final synthesized response token-by-token from Ollama
                synth_payload = {
                    "model": model_name,
                    "messages": formatted_messages,
                    "stream": True,
                }

                async with client.stream("POST", f"{OLLAMA_HOST}/api/chat", json=synth_payload) as stream_resp:
                    async for line in stream_resp.aiter_lines():
                        if line:
                            try:
                                chunk = json.loads(line)
                                delta_text = chunk.get("message", {}).get("content", "")
                                if delta_text:
                                    yield f"data: {json.dumps({'type': 'text_delta', 'content': delta_text})}\n\n"
                                if chunk.get("done"):
                                    break
                            except Exception:
                                pass
            else:
                # Direct reply without tools
                content = assistant_msg.get("content", "")
                if content:
                    yield f"data: {json.dumps({'type': 'text_delta', 'content': content})}\n\n"

            yield "data: [DONE]\n\n"

    return StreamingResponse(sse_stream(), media_type="text/event-stream")
