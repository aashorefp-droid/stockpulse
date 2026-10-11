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

const COOLDOWN_MS = 15 * 60 * 1000; // 15 min cooldown

let lastState: "online" | "offline" | "unknown" = "unknown";
let lastAlertTimestamp = 0;

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
    return res.ok;
  } catch (err) {
    console.error("[health-alert] Telegram send error:", err);
    return false;
  }
}

export async function POST(req: NextRequest) {
  try {
    const body = await req.json().catch(() => ({}));
    const status = body.status === "offline" ? "offline" : "online";
    const errorMsg = String(body.error || "Connection refused or timed out");
    const latency = Number(body.latency || 0);
    const force = Boolean(body.force);
    const targetUrl = `${BACKEND_URL.replace(/\/+$/, "")}/health`;

    const now = Date.now();
    const timeStr = getCentralTimeString();
    let alerted = false;
    let reason = "";

    if (status === "offline") {
      const cooldownElapsed = now - lastAlertTimestamp >= COOLDOWN_MS;
      const shouldAlert = force || lastState !== "offline" || cooldownElapsed;

      if (shouldAlert) {
        const msg = [
          "🚨 <b>StockPulse Backend ALERT — Server DOWN / Not Pingable</b>",
          "",
          "⚠️ <b>Status:</b> Offline / Unreachable (Reported by Web Client)",
          `📍 <b>Target:</b> <code>${targetUrl}</code>`,
          `❌ <b>Error:</b> ${errorMsg}`,
          latency > 0 ? `⏱ <b>Latency:</b> ${latency} ms` : "",
          `📅 <b>Detected at:</b> ${timeStr} CT`,
          "",
          "💡 <i>Action: Check Render logs or restart instance. If free tier, it may be waking up from cold sleep.</i>",
        ]
          .filter(Boolean)
          .join("\n");

        alerted = await sendTelegramMessage(msg);
        if (alerted) {
          lastAlertTimestamp = now;
          reason = force ? "forced_test" : lastState !== "offline" ? "state_transition_down" : "cooldown_expired";
        }
      } else {
        reason = "cooldown_suppressed";
      }
      lastState = "offline";
    } else {
      // online
      if (lastState === "offline" || force) {
        const msg = [
          "✅ <b>StockPulse Backend RESTORED — Server ONLINE</b>",
          "",
          "🟢 <b>Status:</b> Healthy &amp; Pingable",
          `📍 <b>Target:</b> <code>${targetUrl}</code>`,
          latency > 0 ? `⚡ <b>Latency:</b> ${latency} ms` : "",
          `📅 <b>Timestamp:</b> ${timeStr} CT`,
          "",
          "🚀 <i>All backend services, endpoints, and schedulers are reachable again.</i>",
        ]
          .filter(Boolean)
          .join("\n");

        alerted = await sendTelegramMessage(msg);
        reason = force ? "forced_test" : "state_transition_recovered";
      }
      lastState = "online";
    }

    return NextResponse.json({
      ok: true,
      alerted,
      reason,
      status: lastState,
      timestamp: timeStr,
    });
  } catch (err: any) {
    return NextResponse.json({ ok: false, error: err.message }, { status: 500 });
  }
}
