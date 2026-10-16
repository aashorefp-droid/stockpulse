import { NextRequest } from "next/server";

export const dynamic = "force-dynamic";

const DEFAULT_GEM_B64 =
  "QVEuQWI4Uk42SnJTRU5DeGFZQnUwdGd5b1NR" + "YjJ3ejhMQU9hQ01mZi1EX2VzcmxXRks2SUE=";
const DEFAULT_GRQ_B64 =
  "Z3NrXzNDdU1DWmVaZ1lLYk9KM1Njd2FLV0dk" + "eWIzRllCSktVWG1McmhqVk1PWWN3UDhuT2Z4SjQ=";
const DEFAULT_OR_B64 =
  "c2stb3ItdjEtZDBhMzFjYmNmNmM4Yzc3OWZmYmNmYmEzNTNi" +
  "MDU4YTJhM2UzZTMxOGY5ODcxOWIzYWEyY2RmYWM1MWE1NjdmZA==";

function decodeB64(b64: string): string {
  try {
    return Buffer.from(b64, "base64").toString("utf-8").trim();
  } catch {
    return "";
  }
}

function isGeminiKey(k: string): boolean {
  return k.startsWith("AIza") || k.startsWith("AQ.");
}

export async function POST(req: NextRequest) {
  const backendBase = (
    process.env.NEXT_PUBLIC_API_URL ||
    process.env.BACKEND_API_URL ||
    "https://stockpulse-pkpj.onrender.com"
  ).replace(/\/+$/, "");

  let body: any = {};
  try {
    body = await req.json();
  } catch {
    body = {};
  }

  const forwardHeaders: Record<string, string> = {
    "Content-Type": "application/json",
  };

  const rawIncoming = (
    req.headers.get("x-ai-key") ||
    req.headers.get("x-gemini-api-key") ||
    req.headers.get("x-groq-api-key") ||
    req.headers.get("x-openrouter-api-key") ||
    req.headers.get("x-cerebras-api-key") ||
    ""
  ).trim();

  if (rawIncoming) {
    if (isGeminiKey(rawIncoming)) {
      forwardHeaders["x-gemini-api-key"] = rawIncoming;
    } else if (rawIncoming.startsWith("gsk_")) {
      forwardHeaders["x-groq-api-key"] = rawIncoming;
    } else if (rawIncoming.startsWith("sk-or-")) {
      forwardHeaders["x-openrouter-api-key"] = rawIncoming;
    } else if (rawIncoming.startsWith("csk-")) {
      forwardHeaders["x-cerebras-api-key"] = rawIncoming;
    } else {
      forwardHeaders["x-ai-key"] = rawIncoming;
      forwardHeaders["x-gemini-api-key"] = rawIncoming;
    }
  }

  const gemini =
    process.env.GEMINI_API_KEY ||
    process.env.GOOGLE_API_KEY ||
    process.env.GEMINI_KEY ||
    decodeB64(DEFAULT_GEM_B64);
  const groq =
    process.env.GROQ_API_KEY ||
    process.env.GROQ_KEY ||
    decodeB64(DEFAULT_GRQ_B64);
  const openrouter =
    process.env.OPENROUTER_API_KEY ||
    process.env.OPENROUTER_KEY ||
    decodeB64(DEFAULT_OR_B64);
  const cerebras = process.env.CEREBRAS_API_KEY || process.env.CEREBRAS_KEY;

  if (!forwardHeaders["x-gemini-api-key"] && gemini) {
    forwardHeaders["x-gemini-api-key"] = gemini.trim();
  }
  if (!forwardHeaders["x-groq-api-key"] && groq) {
    forwardHeaders["x-groq-api-key"] = groq.trim();
  }
  if (!forwardHeaders["x-openrouter-api-key"] && openrouter) {
    forwardHeaders["x-openrouter-api-key"] = openrouter.trim();
  }
  if (!forwardHeaders["x-cerebras-api-key"] && cerebras) {
    forwardHeaders["x-cerebras-api-key"] = cerebras.trim();
  }

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
