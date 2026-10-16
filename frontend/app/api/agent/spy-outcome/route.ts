import { NextRequest, NextResponse } from "next/server";

export const dynamic = "force-dynamic";

export async function GET(req: NextRequest) {
  const backendBase = (
    process.env.NEXT_PUBLIC_API_URL ||
    process.env.BACKEND_API_URL ||
    "https://stockpulse-pkpj.onrender.com"
  ).replace(/\/+$/, "");

  try {
    const upstreamResp = await fetch(`${backendBase}/api/agent/spy-outcome`, {
      method: "GET",
      cache: "no-store",
    });

    if (!upstreamResp.ok) {
      return NextResponse.json(
        { error: `Upstream HTTP ${upstreamResp.status}` },
        { status: upstreamResp.status }
      );
    }

    const data = await upstreamResp.json();
    return NextResponse.json(data);
  } catch (err: any) {
    return NextResponse.json(
      { error: err?.message || "Failed to fetch SPY post-open outcome" },
      { status: 502 }
    );
  }
}
