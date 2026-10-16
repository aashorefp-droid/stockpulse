import { NextRequest } from "next/server";

export const dynamic = "force-dynamic";

export async function POST(req: NextRequest) {
  const backendBase = (
    process.env.NEXT_PUBLIC_API_URL ||
    process.env.BACKEND_API_URL ||
    "https://stockpulse-pkpj.onrender.com"
  ).replace(/\/+$/, "");

  let body = {};
  try {
    body = await req.json();
  } catch {
    body = {};
  }

  const forwardHeaders: Record<string, string> = {
    "Content-Type": "application/json",
  };

  // 1. Inspect any incoming AI key headers and auto-detect by prefix
  const rawIncoming = (
    req.headers.get("x-ai-key") ||
    req.headers.get("x-gemini-api-key") ||
    req.headers.get("x-groq-api-key") ||
    req.headers.get("x-openrouter-api-key") ||
    req.headers.get("x-cerebras-api-key") ||
    ""
  ).trim();

  if (rawIncoming) {
    if (rawIncoming.startsWith("AIza")) {
      forwardHeaders["x-gemini-api-key"] = rawIncoming;
    } else if (rawIncoming.startsWith("gsk_")) {
      forwardHeaders["x-groq-api-key"] = rawIncoming;
    } else if (rawIncoming.startsWith("sk-or-")) {
      forwardHeaders["x-openrouter-api-key"] = rawIncoming;
    } else if (rawIncoming.startsWith("csk-")) {
      forwardHeaders["x-cerebras-api-key"] = rawIncoming;
    } else {
      // Forward as generic and specific candidates
      forwardHeaders["x-ai-key"] = rawIncoming;
      forwardHeaders["x-gemini-api-key"] = rawIncoming;
      forwardHeaders["x-groq-api-key"] = rawIncoming;
    }
  }

  // 2. Fallback to Vercel / server environment variables if not passed by client
  const groq = process.env.GROQ_API_KEY || process.env.GROQ_KEY;
  const gemini =
    process.env.GEMINI_API_KEY ||
    process.env.GOOGLE_API_KEY ||
    process.env.GEMINI_KEY;
  const openrouter =
    process.env.OPENROUTER_API_KEY || process.env.OPENROUTER_KEY;
  const cerebras = process.env.CEREBRAS_API_KEY || process.env.CEREBRAS_KEY;

  if (!forwardHeaders["x-gemini-api-key"] && gemini) forwardHeaders["x-gemini-api-key"] = gemini.trim();
  if (!forwardHeaders["x-groq-api-key"] && groq) forwardHeaders["x-groq-api-key"] = groq.trim();
  if (!forwardHeaders["x-openrouter-api-key"] && openrouter) forwardHeaders["x-openrouter-api-key"] = openrouter.trim();
  if (!forwardHeaders["x-cerebras-api-key"] && cerebras) forwardHeaders["x-cerebras-api-key"] = cerebras.trim();

  try {
    const upstreamResp = await fetch(`${backendBase}/api/agent/chat`, {
      method: "POST",
      headers: forwardHeaders,
      body: JSON.stringify(body),
    });

    if (!upstreamResp.ok) {
      const errText = await upstreamResp.text().catch(() => "");
      return new Response(errText || "Upstream backend error", {
        status: upstreamResp.status,
        headers: { "Content-Type": "text/plain" },
      });
    }

    return new Response(upstreamResp.body, {
      status: 200,
      headers: {
        "Content-Type": "text/event-stream; charset=utf-8",
        "Cache-Control": "no-cache, no-transform",
        "Connection": "keep-alive",
        "X-Accel-Buffering": "no",
      },
    });
  } catch (err: any) {
    return new Response(
      JSON.stringify({ error: err?.message || "Failed to reach AI backend" }),
      {
        status: 502,
        headers: { "Content-Type": "application/json" },
      }
    );
  }
}
