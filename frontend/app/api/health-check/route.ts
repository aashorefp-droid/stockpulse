import { NextRequest, NextResponse } from "next/server";

const BACKEND_URL =
  process.env.BACKEND_API_URL ||
  process.env.NEXT_PUBLIC_API_URL ||
  "https://stockpulse-pkpj.onrender.com";

const TELEGRAM_BOT_TOKEN =
  process.env.TELEGRAM_BOT_TOKEN ||
  "8777563646:AAGc1wTtz3BcZt--kmkCHwR8unRe0ir7o4Q";

const TELEGRAM_CHAT_ID =
  process.env.TELEGRAM_CHAT_ID ||
  "-1003823895758";

const TELEGRAM_MESSAGE_THREAD_ID =
  process.env.TELEGRAM_MESSAGE_THREAD_ID ||
  "18";

const COOLDOWN_MS = 15 * 60 * 1000; // 15 minutes cooldown between repeat down alerts

// In-memory state across serverless invocations in the same warm instance
let lastState: "online" | "offline" | "unknown" = "unknown";
let lastAlertTimestamp = 0;
let consecutiveFailures = 0;

function getCentralTimeString(): string {
  try {
    return new Intl.DateTimeFormat("en-US", {
      timeZone: "America/Chicago",
      year: "numeric",
      month: "2-digit",
      day: "2-digit",
      hour: "2-digit",
      minute: "2-digit",
      second: "2-digit",
      hour12: true,
    }).format(new Date());
  } catch {
    return new Date().toISOString();
  }
}

async function sendTelegramMessage(text: string): Promise<boolean> {
  if (!TELEGRAM_BOT_TOKEN || !TELEGRAM_CHAT_ID) {
    console.warn("[health-check] Telegram credentials not configured");
    return false;
  }

  const url = `https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/sendMessage`;
  const payload: Record<string, any> = {
    chat_id: TELEGRAM_CHAT_ID,
    text,
    parse_mode: "HTML",
  };

  if (TELEGRAM_MESSAGE_THREAD_ID && String(TELEGRAM_MESSAGE_THREAD_ID).trim()) {
    payload.message_thread_id = parseInt(String(TELEGRAM_MESSAGE_THREAD_ID).trim(), 10);
  }

  try {
    const res = await fetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) {
      console.error("[health-check] Telegram rejected message:", data);
      return false;
    }
    return true;
  } catch (err) {
    console.error("[health-check] Telegram send error:", err);
    return false;
  }
}

export async function GET(req: NextRequest) {
  return handleHealthCheck(req);
}

export async function POST(req: NextRequest) {
  return handleHealthCheck(req);
}

async function handleHealthCheck(req: NextRequest) {
  const { searchParams } = new URL(req.url);
  const force = searchParams.get("force") === "true";
  const simulate = searchParams.get("simulate"); // "offline" or "online" for testing

  const targetUrl = `${BACKEND_URL.replace(/\/+$/, "")}/health`;
  const startTime = Date.now();
  let isOnline = false;
  let latency = 0;
  let errorMsg = "";
  let httpStatus: number | null = null;
  let responseBody: any = null;

  if (simulate === "offline") {
    isOnline = false;
    errorMsg = "Simulated offline condition for testing";
    latency = 9999;
  } else {
    try {
      const controller = new AbortController();
      const timeoutId = setTimeout(() => controller.abort(), 12000); // 12s timeout

      const res = await fetch(targetUrl, {
        method: "GET",
        signal: controller.signal,
        cache: "no-store",
      });
      clearTimeout(timeoutId);
      latency = Date.now() - startTime;
      httpStatus = res.status;

      if (res.ok) {
        responseBody = await res.json().catch(() => ({}));
        isOnline = true;
      } else {
        isOnline = false;
        errorMsg = `HTTP ${res.status} ${res.statusText}`;
      }
    } catch (err: any) {
      latency = Date.now() - startTime;
      isOnline = false;
      if (err.name === "AbortError") {
        errorMsg = "Connection timed out after 12s (Render cold sleep or server frozen)";
      } else {
        errorMsg = err.message || "Network connection failed";
      }
    }
  }

  const now = Date.now();
  const timeStr = getCentralTimeString();
  let alerted = false;
  let alertReason = "";

  if (!isOnline) {
    consecutiveFailures += 1;
    const cooldownElapsed = now - lastAlertTimestamp >= COOLDOWN_MS;
    const shouldAlert = force || lastState !== "offline" || cooldownElapsed;

    if (shouldAlert) {
      const msg = [
        "🚨 <b>StockPulse Backend ALERT — Server DOWN / Not Pingable</b>",
        "",
        "⚠️ <b>Status:</b> Offline / Unreachable",
        `📍 <b>Target:</b> <code>${targetUrl}</code>`,
        `❌ <b>Error:</b> ${errorMsg}`,
        `⏱ <b>Latency:</b> ${latency} ms`,
        `📅 <b>Detected at:</b> ${timeStr} CT`,
        "",
        "💡 <i>Action: Check Render dashboard / logs. If on Render free tier, the container may be sleeping or restarting.</i>",
      ].join("\n");

      alerted = await sendTelegramMessage(msg);
      if (alerted) {
        lastAlertTimestamp = now;
        alertReason = force ? "forced_test" : lastState !== "offline" ? "state_transition_down" : "cooldown_expired";
      }
    }
    lastState = "offline";
  } else {
    // Server is online
    const wasOffline = lastState === "offline";
    if (wasOffline || force) {
      const msg = [
        "✅ <b>StockPulse Backend RESTORED — Server ONLINE</b>",
        "",
        "🟢 <b>Status:</b> Healthy &amp; Pingable",
        `📍 <b>Target:</b> <code>${targetUrl}</code>`,
        `⚡ <b>Latency:</b> ${latency} ms`,
        `📅 <b>Timestamp:</b> ${timeStr} CT`,
        "",
        "🚀 <i>All backend services, endpoints, and schedulers are reachable again.</i>",
      ].join("\n");

      alerted = await sendTelegramMessage(msg);
      alertReason = force ? "forced_test" : "state_transition_recovered";
    }
    lastState = "online";
    consecutiveFailures = 0;
  }

  return NextResponse.json({
    ok: isOnline,
    status: isOnline ? "online" : "offline",
    target: targetUrl,
    latency,
    httpStatus,
    error: errorMsg || null,
    alerted,
    alertReason: alertReason || null,
    lastState,
    timestamp: timeStr,
    details: responseBody,
  });
}
