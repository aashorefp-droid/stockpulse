import { NextRequest, NextResponse } from "next/server";

export const dynamic = "force-dynamic";
export const revalidate = 0;

export async function GET(req: NextRequest) {
  const backendBase = (
    process.env.NEXT_PUBLIC_API_URL ||
    process.env.BACKEND_API_URL ||
    "https://stockpulse-pkpj.onrender.com"
  ).replace(/\/+$/, "");

  const ticker = req.nextUrl.searchParams.get("ticker") || "SPY";
  const mode = req.nextUrl.searchParams.get("mode") || "pulse";
  const date = req.nextUrl.searchParams.get("date") || "";
  const t = req.nextUrl.searchParams.get("_t") || Date.now().toString();

  try {
    const qs = `ticker=${encodeURIComponent(ticker)}&mode=${encodeURIComponent(mode)}${
      date ? `&date=${encodeURIComponent(date)}` : ""
    }&_t=${encodeURIComponent(t)}`;
    const upstreamResp = await fetch(
      `${backendBase}/api/agent/spy-outcome?${qs}`,
      {
        method: "GET",
        cache: "no-store",
        headers: {
          "Cache-Control": "no-cache, no-store, must-revalidate",
          "Pragma": "no-cache",
        },
      }
    );

    if (!upstreamResp.ok) {
      return NextResponse.json(
        { error: `Upstream HTTP ${upstreamResp.status}` },
        {
          status: upstreamResp.status,
          headers: {
            "Cache-Control": "no-store, no-cache, must-revalidate, proxy-revalidate",
            "Pragma": "no-cache",
          },
        }
      );
    }

    const data = await upstreamResp.json();
    return NextResponse.json(data, {
      headers: {
        "Cache-Control": "no-store, no-cache, must-revalidate, proxy-revalidate",
        "Pragma": "no-cache",
        "Expires": "0",
      },
    });
  } catch (err: any) {
    return NextResponse.json(
      { error: err?.message || `Failed to fetch ${ticker} post-open outcome` },
      {
        status: 502,
        headers: {
          "Cache-Control": "no-store, no-cache, must-revalidate",
        },
      }
    );
  }
}
