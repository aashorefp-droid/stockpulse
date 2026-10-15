import { NextRequest, NextResponse } from "next/server";

export const dynamic = "force-dynamic";

export async function GET(req: NextRequest) {
  const backendBase = (
    process.env.NEXT_PUBLIC_API_URL ||
    process.env.BACKEND_API_URL ||
    "https://stockpulse-pkpj.onrender.com"
  ).replace(/\/+$/, "");

  const forwardHeaders: Record<string, string> = {};
  const groq = process.env.GROQ_API_KEY || process.env.GROQ_KEY;
  const gemini =
    process.env.GEMINI_API_KEY ||
    process.env.GOOGLE_API_KEY ||
    process.env.GEMINI_KEY;
  const openrouter =
    process.env.OPENROUTER_API_KEY || process.env.OPENROUTER_KEY;
  const cerebras = process.env.CEREBRAS_API_KEY || process.env.CEREBRAS_KEY;

  if (groq) forwardHeaders["x-groq-api-key"] = groq.trim();
  if (gemini) forwardHeaders["x-gemini-api-key"] = gemini.trim();
  if (openrouter) forwardHeaders["x-openrouter-api-key"] = openrouter.trim();
  if (cerebras) forwardHeaders["x-cerebras-api-key"] = cerebras.trim();

  try {
    const upstreamResp = await fetch(`${backendBase}/api/agent/status`, {
      method: "GET",
      headers: forwardHeaders,
      cache: "no-store",
    });

    if (!upstreamResp.ok) {
      throw new Error(`Upstream returned ${upstreamResp.status}`);
    }

    const data = await upstreamResp.json();
    return NextResponse.json(data);
  } catch (err: any) {
    const hasVercelKey = Boolean(groq || gemini || openrouter || cerebras);
    let primary = "⚪ Connecting to AI Backend...";
    if (groq) primary = "🟢 Groq Cloud (~800 t/s · GPT-OSS 120B)";
    else if (gemini) primary = "🟢 Google Gemini (~150 t/s · Flash)";
    else if (openrouter) primary = "🟢 OpenRouter (Cloud Free)";

    return NextResponse.json({
      available: hasVercelKey,
      primary,
      primary_id: groq ? "groq" : gemini ? "gemini" : openrouter ? "openrouter" : "ollama",
      chain: [],
      fallback_count: 0,
      diagnostics: {
        backend_unreachable: true,
        error: String(err),
        has_vercel_groq: Boolean(groq),
        has_vercel_gemini: Boolean(gemini),
        has_vercel_openrouter: Boolean(openrouter),
      },
    });
  }
}
