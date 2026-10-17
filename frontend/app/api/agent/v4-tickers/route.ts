import { NextRequest, NextResponse } from "next/server";

export const dynamic = "force-dynamic";

const getBackendBase = () =>
  (
    process.env.NEXT_PUBLIC_API_URL ||
    process.env.BACKEND_API_URL ||
    "https://stockpulse-pkpj.onrender.com"
  ).replace(/\/+$/, "");

export async function GET() {
  try {
    const upstreamResp = await fetch(`${getBackendBase()}/api/agent/v4-tickers`, {
      method: "GET",
      cache: "no-store",
    });
    if (!upstreamResp.ok) {
      return NextResponse.json(
        { tickers: ["SPY", "SPXW"], popular_options: ["SPY", "SPXW", "QQQ", "IWM", "DIA", "NVDA", "TSLA", "AAPL", "META", "AMD"] },
        { status: 200 }
      );
    }
    const data = await upstreamResp.json();
    return NextResponse.json(data);
  } catch {
    return NextResponse.json({
      tickers: ["SPY", "SPXW"],
      popular_options: ["SPY", "SPXW", "QQQ", "IWM", "DIA", "NVDA", "TSLA", "AAPL", "META", "AMD"],
    });
  }
}

export async function POST(req: NextRequest) {
  try {
    const body = await req.json();
    const upstreamResp = await fetch(`${getBackendBase()}/api/agent/v4-tickers`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    const data = await upstreamResp.json();
    return NextResponse.json(data, { status: upstreamResp.status });
  } catch (err: any) {
    return NextResponse.json(
      { error: err?.message || "Failed to update V4 alert tickers" },
      { status: 502 }
    );
  }
}
