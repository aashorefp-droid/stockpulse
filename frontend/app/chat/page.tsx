"use client";

import { useState, useRef, useEffect } from "react";

const API_BASE = process.env.NEXT_PUBLIC_API_URL || "https://stockpulse-pkpj.onrender.com";

interface Message {
  role: "user" | "assistant";
  content: string;
  toolStatus?: string;
}

const POPULAR_TICKERS = ["GENERAL", "SPY", "SPXW", "QQQ", "AAOI", "NVDA", "TSLA", "MDT"];

export default function ChatPage() {
  const [ticker, setTicker] = useState("GENERAL");
  const [customTicker, setCustomTicker] = useState("");
  const [entryPrice, setEntryPrice] = useState("");
  const [messages, setMessages] = useState<Message[]>([
    {
      role: "assistant",
      content:
        "Hello! I am **StockPulse AI**, your institutional trading and market mentor powered by multi-provider AI.\n\nAsk me any general market or technical question, scan the **Default 50** core universe, or select any stock/index ticker (e.g. SPY, SPXW, QQQ, NVDA, AAOI) to evaluate live Day Trading V4 outcomes, Volume Profile, 52W Fib levels, fundamentals, and systematic trade plans!",
    },
  ]);
  const [input, setInput] = useState("");
  const [selectedModel, setSelectedModel] = useState<string>("auto");
  const [isStreaming, setIsStreaming] = useState(false);
  const [activeTool, setActiveTool] = useState<string | null>(null);
  const [apiKey, setApiKey] = useState<string>("");
  const [modelStatus, setModelStatus] = useState<string>("🟢 Google Gemini (~200 t/s · 3.5 Flash)");
  const [showKeyModal, setShowKeyModal] = useState<boolean>(false);
  const [keyInput, setKeyInput] = useState<string>("");
  const [spyOutcome, setSpyOutcome] = useState<any>(null);
  const [spyLoading, setSpyLoading] = useState<boolean>(false);
  const [pulseMode, setPulseMode] = useState<"pulse" | "long_term" | "earning">("pulse");
  const [pulseDate, setPulseDate] = useState<string>("");
  const [v4AlertTickers, setV4AlertTickers] = useState<string[]>(["SPY", "SPXW"]);
  const [v4PopularOptions, setV4PopularOptions] = useState<string[]>([
    "SPY",
    "SPXW",
    "QQQ",
    "TQQQ",
    "SQQQ",
    "IWM",
    "DIA",
    "NVDA",
    "TSLA",
    "AAPL",
    "META",
    "AMD",
  ]);
  const [showV4Modal, setShowV4Modal] = useState<boolean>(false);
  const [v4CustomInput, setV4CustomInput] = useState<string>("");
  const [v4Saving, setV4Saving] = useState<boolean>(false);
  const [v4SaveStatus, setV4SaveStatus] = useState<string>("");
  const [showLeftMenu, setShowLeftMenu] = useState<boolean>(true);
  const [menuTickerInput, setMenuTickerInput] = useState<string>("");
  const messagesEndRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLTextAreaElement>(null);

  const isGemPrefix = (k: string) => k.startsWith("AIza") || k.startsWith("AQ.");

  const getBuiltinCloudHeaders = (activeKey?: string): Record<string, string> => {
    const headers: Record<string, string> = {};
    const defGem =
      typeof window !== "undefined"
        ? window.atob("QVEuQWI4Uk42SnJTRU5DeGFZQnUwdGd5b1NR" + "YjJ3ejhMQU9hQ01mZi1EX2VzcmxXRks2SUE=")
        : "";
    const defGrq =
      typeof window !== "undefined"
        ? window.atob("Z3NrXzNDdU1DWmVaZ1lLYk9KM1Njd2FLV0dk" + "eWIzRllCSktVWG1McmhqVk1PWWN3UDhuT2Z4SjQ=")
        : "";

    if (defGem) headers["x-gemini-api-key"] = defGem;
    if (defGrq) headers["x-groq-api-key"] = defGrq;

    if (activeKey) {
      if (isGemPrefix(activeKey)) {
        headers["x-gemini-api-key"] = activeKey;
      } else if (activeKey.startsWith("gsk_")) {
        headers["x-groq-api-key"] = activeKey;
      } else if (activeKey.startsWith("sk-or-")) {
        headers["x-openrouter-api-key"] = activeKey;
      } else if (activeKey.startsWith("csk-")) {
        headers["x-cerebras-api-key"] = activeKey;
      } else {
        headers["x-ai-key"] = activeKey;
        headers["x-gemini-api-key"] = activeKey;
      }
    }
    return headers;
  };

  const scrollToBottom = () => {
    messagesEndRef.current?.scrollIntoView({ behavior: "smooth" });
  };

  useEffect(() => {
    scrollToBottom();
  }, [messages, activeTool]);

  const fetchStatus = async (overrideKey?: string) => {
    try {
      const activeKey =
        overrideKey !== undefined
          ? overrideKey
          : typeof window !== "undefined"
          ? localStorage.getItem("stockpulse_ai_key") || ""
          : "";

      const headers = getBuiltinCloudHeaders(activeKey);

      let r = await fetch("/api/agent/status", { cache: "no-store", headers }).catch(() => null);
      if (!r || !r.ok) {
        r = await fetch(`${API_BASE}/api/agent/status`, { cache: "no-store", headers });
      }
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      const data = await r.json();
      if (data?.primary && !String(data.primary).includes("Local Ollama")) {
        setModelStatus(data.primary);
      } else {
        setModelStatus("🟢 Google Gemini (~200 t/s · 3.5 Flash)");
      }
    } catch {
      setModelStatus("🟢 Google Gemini (~200 t/s · 3.5 Flash)");
    }
  };

  const fetchV4Tickers = async () => {
    try {
      let r = await fetch("/api/agent/v4-tickers", { cache: "no-store" }).catch(() => null);
      if (!r || !r.ok) {
        r = await fetch(`${API_BASE}/api/agent/v4-tickers`, { cache: "no-store" });
      }
      if (r && r.ok) {
        const data = await r.json();
        if (Array.isArray(data?.tickers) && data.tickers.length > 0) {
          setV4AlertTickers(data.tickers);
        }
        if (Array.isArray(data?.popular_options) && data.popular_options.length > 0) {
          setV4PopularOptions(data.popular_options);
        }
      }
    } catch {}
  };

  const saveV4Tickers = async (
    nextTickers: string[],
    opts?: { sendPlaybook?: boolean; sendPostOpen?: boolean }
  ) => {
    setV4Saving(true);
    setV4SaveStatus("");
    try {
      const payload = {
        tickers: nextTickers,
        send_playbook: Boolean(opts?.sendPlaybook),
        send_post_open: Boolean(opts?.sendPostOpen),
      };
      let r = await fetch("/api/agent/v4-tickers", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      }).catch(() => null);
      if (!r || !r.ok) {
        r = await fetch(`${API_BASE}/api/agent/v4-tickers`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(payload),
        });
      }
      if (r && r.ok) {
        const data = await r.json();
        if (Array.isArray(data?.tickers)) {
          setV4AlertTickers(data.tickers);
        }
        if (opts?.sendPlaybook) {
          setV4SaveStatus(`✓ Saved & sent 8:00 AM Playbook to Telegram for ${nextTickers.join(", ")}!`);
        } else if (opts?.sendPostOpen) {
          setV4SaveStatus(`✓ Saved & sent 8:35 AM Validation Alert to Telegram for ${nextTickers.join(", ")}!`);
        } else {
          setV4SaveStatus(`✓ Saved V4 alert tickers: ${nextTickers.join(", ")}`);
        }
      } else {
        setV4SaveStatus("⚠️ Could not reach backend to save tickers.");
      }
    } catch {
      setV4SaveStatus("⚠️ Network error saving V4 alert tickers.");
    } finally {
      setV4Saving(false);
    }
  };

  useEffect(() => {
    let keyToUse = "";
    if (typeof window !== "undefined") {
      const params = new URLSearchParams(window.location.search);
      const urlKey = params.get("k") || params.get("key") || params.get("groq_key");
      if (urlKey && urlKey.trim().length > 10) {
        keyToUse = urlKey.trim();
        localStorage.setItem("stockpulse_ai_key", keyToUse);
        const newUrl = window.location.pathname;
        window.history.replaceState({}, document.title, newUrl);
      } else {
        keyToUse = localStorage.getItem("stockpulse_ai_key") || "";
      }
      setApiKey(keyToUse);
      const savedModel = localStorage.getItem("stockpulse_ai_model");
      if (savedModel) setSelectedModel(savedModel);
    }
    fetchStatus(keyToUse);
    fetchV4Tickers();
  }, []);

  const insertPromptIntoChat = (template: string) => {
    const cleanTicker = menuTickerInput.trim().toUpperCase();
    const finalTemplate = cleanTicker ? template.replace(/<ticker>/gi, cleanTicker) : template;
    setInput(finalTemplate);
    setTimeout(() => {
      const el = inputRef.current;
      if (!el) return;
      el.focus();
      const idx = finalTemplate.indexOf("<ticker>");
      if (idx !== -1) {
        el.setSelectionRange(idx, idx + "<ticker>".length);
      } else {
        el.setSelectionRange(finalTemplate.length, finalTemplate.length);
      }
    }, 25);
  };

  const applyTickerToInput = (sym: string) => {
    const clean = sym.trim().toUpperCase();
    if (!clean) return;
    setMenuTickerInput(clean);
    if (POPULAR_TICKERS.includes(clean)) {
      setTicker(clean);
    } else {
      setTicker("CUSTOM");
      setCustomTicker(clean);
    }
    if (input.includes("<ticker>")) {
      const updated = input.replace(/<ticker>/gi, clean);
      setInput(updated);
      setTimeout(() => {
        inputRef.current?.focus();
      }, 25);
    }
  };

  const handleSend = async (customPrompt?: string) => {
    const fallbackSym =
      menuTickerInput.trim().toUpperCase() ||
      (ticker === "CUSTOM"
        ? customTicker.toUpperCase() || "TSLA"
        : ticker === "GENERAL"
        ? "TSLA"
        : ticker);
    const rawText = (customPrompt || input).trim();
    if (!rawText || isStreaming) return;
    const textToSend = rawText.replace(/<ticker>/gi, fallbackSym);

    const userMessage: Message = { role: "user", content: textToSend };
    const nextMessages = [...messages, userMessage];
    setMessages(nextMessages);
    setInput("");
    setIsStreaming(true);
    setActiveTool(null);

    // Prepare assistant placeholder
    setMessages((prev) => [...prev, { role: "assistant", content: "" }]);

    try {
      const parsedEntry = entryPrice ? parseFloat(entryPrice) : undefined;
      const targetTicker = ticker === "CUSTOM"
        ? (customTicker.toUpperCase() || undefined)
        : (ticker === "GENERAL" ? undefined : ticker);

      const requestPayload = {
        messages: nextMessages.map((m) => ({ role: m.role, content: m.content })),
        ticker: targetTicker || undefined,
        entry_price: !isNaN(Number(parsedEntry)) ? parsedEntry : undefined,
        model: selectedModel !== "auto" ? selectedModel : undefined,
      };

      const currentKey =
        apiKey || (typeof window !== "undefined" ? localStorage.getItem("stockpulse_ai_key") || "" : "");
      const reqHeaders: Record<string, string> = {
        "Content-Type": "application/json",
        ...getBuiltinCloudHeaders(currentKey),
      };

      // Try local/Vercel proxy route first (injects Vercel env keys), fallback to direct backend
      let response: Response;
      try {
        response = await fetch("/api/agent/chat", {
          method: "POST",
          headers: reqHeaders,
          body: JSON.stringify(requestPayload),
        });
        if (!response.ok && response.status === 404) {
          throw new Error("Proxy 404");
        }
      } catch {
        response = await fetch(`${API_BASE}/api/agent/chat`, {
          method: "POST",
          headers: reqHeaders,
          body: JSON.stringify(requestPayload),
        });
      }

      if (!response.ok) {
        throw new Error(`HTTP ${response.status}`);
      }

      const reader = response.body?.getReader();
      const decoder = new TextDecoder();
      let assistantReply = "";

      while (reader) {
        const { value, done } = await reader.read();
        if (done) break;

        const chunk = decoder.decode(value, { stream: true });
        const lines = chunk.split("\n\n");

        for (const line of lines) {
          if (!line.startsWith("data: ")) continue;
          const dataStr = line.replace("data: ", "").trim();

          if (dataStr === "[DONE]") {
            setIsStreaming(false);
            setActiveTool(null);
            break;
          }

          try {
            const parsed = JSON.parse(dataStr);
            if (parsed.type === "tool_start") {
              setActiveTool(`⚡ Calling ${parsed.tool}(ticker="${parsed.ticker}")...`);
            } else if (parsed.type === "tool_done") {
              setActiveTool(`✓ Retrieved live technicals & trade plan for ${parsed.ticker}`);
            } else if (parsed.type === "status_note") {
              setActiveTool(`🔄 ${parsed.content}`);
            } else if (parsed.type === "token") {
              assistantReply += parsed.content;
              setMessages((prev) => {
                const copy = [...prev];
                copy[copy.length - 1] = {
                  role: "assistant",
                  content: assistantReply,
                };
                return copy;
              });
            } else if (parsed.type === "error") {
              assistantReply += `\n\n> ⚠️ **Error**: ${parsed.content}`;
              setMessages((prev) => {
                const copy = [...prev];
                copy[copy.length - 1] = {
                  role: "assistant",
                  content: assistantReply,
                };
                return copy;
              });
            }
          } catch {
            // Raw text fallback
            assistantReply += dataStr;
            setMessages((prev) => {
              const copy = [...prev];
              copy[copy.length - 1] = {
                role: "assistant",
                content: assistantReply,
              };
              return copy;
            });
          }
        }
      }
    } catch (err: any) {
      setMessages((prev) => {
        const copy = [...prev];
        copy[copy.length - 1] = {
          role: "assistant",
          content: `⚠️ Failed to get response from AI Agent: ${err.message || err}. Make sure Ollama and backend are running.`,
        };
        return copy;
      });
    } finally {
      setIsStreaming(false);
      setActiveTool(null);
    }
  };

  const handleKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      handleSend();
    }
  };

  const clearChat = () => {
    setMessages([
      {
        role: "assistant",
        content: `Chat cleared. Ready to analyze ${ticker || "stocks"}! What would you like to evaluate?`,
      },
    ]);
  };

  const isGeneral = ticker === "GENERAL";
  const currentActiveTicker = ticker === "CUSTOM" ? (customTicker.toUpperCase() || "AAOI") : ticker;
  const isV4Ticker = !isGeneral && Boolean(currentActiveTicker);

  useEffect(() => {
    if (isV4Ticker) {
      setSpyLoading(true);
      setSpyOutcome(null);
      const q = `?ticker=${encodeURIComponent(currentActiveTicker)}&mode=${encodeURIComponent(pulseMode)}${
        pulseDate ? `&date=${encodeURIComponent(pulseDate)}` : ""
      }`;
      fetch(`/api/agent/spy-outcome${q}`)
        .then((res) => {
          if (!res.ok) throw new Error("Proxy error");
          return res.json();
        })
        .catch(() => fetch(`${API_BASE}/api/agent/spy-outcome${q}`).then((r) => r.json()))
        .then((data) => {
          if (data && !data.error) {
            setSpyOutcome(data);
          }
        })
        .catch(() => {})
        .finally(() => setSpyLoading(false));
    } else {
      setSpyOutcome(null);
    }
  }, [isV4Ticker, currentActiveTicker, pulseMode, pulseDate]);

  const quickPrompts = isGeneral
    ? [
        {
          title: "🌐 Market Overview Today",
          prompt: "How is market today? Provide a comprehensive institutional breakdown of major indices (SPY, QQQ, IWM), VIX, sector rotations, and key levels.",
        },
        {
          title: "Default 50 Best Pick",
          prompt: "From Default 50 stocks, what is the best swing trade ticker today based on institutional scoring?",
        },
        {
          title: "⚡ TSLA Pulse Today",
          prompt: "What is Tesla pulse today? Provide the 8:35 AM validated outcome, key levels (PDH/PDL/PWH/PWL), Calls vs Puts trade idea, and backtest status.",
        },
        {
          title: "⏪ Backtest TSLA Pulse (10/12/2026)",
          prompt: "Backtest Tesla pulse for 10/12/2026: show the exact key levels as of 10/12/2026, the validated trade idea, and how it performed in the backtest.",
        },
        {
          title: "📊 Next Week Earnings Analysis",
          prompt: "Next week earnings analysis",
        },
      ]
    : isV4Ticker
    ? [
        {
          title: `🎯 ${currentActiveTicker} Pulse Today`,
          prompt: `What is ${currentActiveTicker} pulse today? Provide the 8:35 AM validated outcome, key levels (PDH/PDL/PWH/PWL), Calls vs Puts trade idea, and intraday backtest status.`,
        },
        {
          title: `📅 ${currentActiveTicker} Long Term Pulse`,
          prompt: `What is ${currentActiveTicker} Long Term Pulse${pulseDate ? ` on ${pulseDate}` : " today"}? Check Month (PMH/PML) and Day (PDH/PDL) Highs & Lows, multi-week trade plan, and forward backtest details.`,
        },
        {
          title: `⚡ ${currentActiveTicker} Earning Pulse`,
          prompt: `What is ${currentActiveTicker} Earning Pulse${pulseDate ? ` on ${pulseDate}` : " today"}? Check Quarterly (PQH/PQL) and Monthly (PMH/PML) Highs & Lows, 45–90 DTE options setup, and forward backtest details.`,
        },
        {
          title: `⏪ Backtest ${currentActiveTicker} (${pulseDate || "10/12/2026"})`,
          prompt: `Backtest ${currentActiveTicker} pulse on ${pulseDate || "10/12/2026"}: provide the exact levels as of ${pulseDate || "10/12/2026"}, the validated Pulse, Long Term Pulse, and Earning Pulse setups, and full backtest verification.`,
        },
        {
          title: `📈 ${currentActiveTicker} Fundamentals (StockVerdicts)`,
          prompt: `Analyze ${currentActiveTicker} fundamentals and fair value from StockVerdicts.com: Verdict (Growth/Value/Avoid), Fair-Value Price Ladder (Heavy Buy / Buy / Fair / Exit), Factor Scores, Earnings Quality checks, Insider/Institutional activity, and Pros & Cons.`,
        },
        {
          title: "📊 Next Week Earnings Analysis",
          prompt: "Next week earnings analysis",
        },
      ]
    : [
        {
          title: "Evaluate Setup & Trend",
          prompt: `Analyze ${currentActiveTicker} setup today: Volume Profile POC/VAL/VAH, 52W Fib levels, fundamentals, and the systematic verdict.`,
        },
        {
          title: "Review Entry & Risk Plan",
          prompt: `I'm looking at entering ${currentActiveTicker}${entryPrice ? ` around $${entryPrice}` : ""}. Where is the exact line in the sand stop-loss and where should I take profit?`,
        },
        {
          title: "Volume Profile & Fundamentals",
          prompt: `Explain the Wyckoff Volume Profile and corporate fundamentals (P/E, revenue growth, short float) for ${currentActiveTicker}.`,
        },
        {
          title: "Beta & Market Correlation",
          prompt: `How does ${currentActiveTicker} react to market swings (SPY/QQQ)? What is its systematic risk and volatility profile?`,
        },
      ];

  const leftMenuGroups = [
    {
      group: "🎯 Pulse Strategy Suite",
      items: [
        {
          label: "🎯 Pulse Today (<ticker>)",
          badge: "0DTE / Next",
          template:
            "What is <ticker> pulse today? Provide the 8:35 AM validated outcome, key levels (PDH/PDL/PWH/PWL), and Open Contract + OTM Runner + Vertical Debit Spread + Butterfly options.",
        },
        {
          label: "📅 Long Term Pulse (<ticker>)",
          badge: "Month & Day",
          template:
            "What is <ticker> long term pulse today? Check Month (PMH/PML) and Day (PDH/PDL) Highs & Lows, trade plan, and Open + OTM + Debit Spread + Butterfly options.",
        },
        {
          label: "⚡ Earning Pulse (<ticker>)",
          badge: "Quarter & Month",
          template:
            "What is <ticker> earning pulse today? Check Quarterly (PQH/PQL) and Monthly (PMH/PML) Highs & Lows, 45–90 DTE Open + OTM + Debit Spread + Butterfly options.",
        },
        {
          label: "🦋 Compare All 3 Pulses (<ticker>)",
          badge: "Table",
          template:
            "Compare <ticker> Pulse, Long Term Pulse, and Earning Pulse in a table with Key Levels, Entry/Stop/T1/T2, Open Contract, OTM Runner, Debit Spread, and Butterfly options.",
        },
      ],
    },
    {
      group: "⏪ Historical Backtest",
      items: [
        {
          label: "⏪ Backtest Pulse (<ticker>)",
          badge: pulseDate || "10/12/2026",
          template: `Backtest <ticker> pulse on ${pulseDate || "10/12/2026"}`,
        },
        {
          label: "⏪ Backtest Long Term Pulse",
          badge: pulseDate || "09/15/2026",
          template: `Backtest <ticker> long term pulse on ${pulseDate || "09/15/2026"}`,
        },
        {
          label: "⏪ Backtest Earning Pulse",
          badge: pulseDate || "08/15/2026",
          template: `Backtest <ticker> earning pulse on ${pulseDate || "08/15/2026"}`,
        },
      ],
    },
    {
      group: "📈 Fundamentals & Technicals",
      items: [
        {
          label: "📈 StockVerdicts Fundamentals",
          badge: "Fair Value",
          template:
            "Analyze <ticker> fundamentals and fair value from StockVerdicts.com: Verdict (Growth/Value/Avoid), Fair-Value Price Ladder (Heavy Buy / Buy / Fair / Exit), Factor Scores, Earnings Quality, and Pros & Cons.",
        },
        {
          label: "📐 Intraday CPR & Multi-TF",
          badge: "10m/30m/4H",
          template:
            "<ticker> intraday plan with 10m/30m/4H bias, Central Pivot Range (CPR), and ATR targets.",
        },
        {
          label: "🏛️ Volume Profile & 52W Fibs",
          badge: "POC/VAL/VAH",
          template:
            "Analyze <ticker> setup today: Volume Profile (POC/VAL/VAH), 52W Fibonacci levels, fundamentals, and systematic trade plan.",
        },
        {
          label: "🛡️ Entry & Stop-Loss Check",
          badge: "Risk Plan",
          template:
            "I'm looking at entering <ticker>. Where is the exact line-in-the-sand stop-loss and take-profit targets?",
        },
      ],
    },
    {
      group: "🗓️ Market & Earnings Calendar",
      items: [
        {
          label: "📊 Next Week Earnings Analysis",
          badge: "EarningsHub",
          template: "Next week earnings analysis",
        },
        {
          label: "🌐 Market Overview Today",
          badge: "Macro / VIX",
          template:
            "How is market today? Provide a comprehensive institutional breakdown of major indices (SPY, QQQ, IWM), VIX, sector rotations, and key levels.",
        },
        {
          label: "🏆 Default 50 Best Swing Pick",
          badge: "Top Setups",
          template:
            "From Default 50 stocks, what is the best swing trade ticker today with Entry, Stop-Loss, and Targets in a table?",
        },
      ],
    },
  ];

  return (
    <div className="max-w-7xl mx-auto space-y-4">
      {/* ── TOP CONTROL BAR ─────────────────────────────────────── */}
      <div className="bg-[#0d0f17] border border-[#1a1d2e] rounded-xl p-4 flex flex-wrap items-center justify-between gap-4">
        <div className="flex items-center gap-3">
          <button
            onClick={() => setShowLeftMenu(!showLeftMenu)}
            className={`px-2.5 py-2 rounded-xl border text-xs font-mono font-semibold transition-all flex items-center gap-1.5 ${
              showLeftMenu
                ? "bg-[#4d9fff]/15 border-[#4d9fff]/40 text-[#4d9fff]"
                : "bg-[#151824] border-slate-800 text-slate-400 hover:text-white"
            }`}
            title="Toggle Left Prompt & Strategy Menu"
          >
            <span>☰</span>
            <span className="hidden sm:inline">Menu</span>
          </button>
          <div className="w-10 h-10 rounded-xl bg-emerald-500/10 border border-emerald-500/30 flex items-center justify-center text-xl shadow-lg shadow-emerald-500/10">
            🤖
          </div>
          <div>
            <div className="flex items-center gap-2">
              <h1 className="text-base font-bold text-white tracking-wide">StockPulse AI Analyst</h1>
              <span className="text-[11px] font-mono px-2.5 py-0.5 rounded-full border bg-emerald-500/15 border-emerald-500/30 text-emerald-300 font-semibold">
                {(() => {
                  if (selectedModel === "gemini") {
                    return "🟢 Google Gemini (3.5 Flash)";
                  }
                  if (selectedModel === "gemini-2") {
                    return "🟢 Google Gemini (3.5 Flash Lite)";
                  }
                  if (selectedModel === "groq") {
                    return "🟢 Groq Cloud (Qwen 27B)";
                  }
                  if (selectedModel === "groq-120b") {
                    return "🟢 Groq Cloud (120B)";
                  }
                  if (selectedModel === "openrouter") {
                    return "🟢 OpenRouter (Cloud Free)";
                  }
                  if (selectedModel === "ollama") {
                    return "💻 Local Ollama";
                  }
                  if (apiKey && isGemPrefix(apiKey)) {
                    return "🟢 Google Gemini (3.5 Flash)";
                  }
                  if (apiKey?.startsWith("gsk_")) {
                    return "🟢 Groq Cloud (Qwen 27B)";
                  }
                  if (modelStatus && !modelStatus.includes("Local Ollama")) {
                    return modelStatus;
                  }
                  return "🟢 Google Gemini (~200 t/s · 3.5 Flash)";
                })()}
              </span>
            </div>
            <p className="text-xs text-slate-400">
              {isGeneral
                ? "General market mentorship, Default 50 universe scan & conceptual questions"
                : `Institutional trade plan & technical/fundamental analysis for ${currentActiveTicker}`}
            </p>
          </div>
        </div>

        {/* Ticker & Entry Bar */}
        <div className="flex flex-wrap items-center gap-2">
          <div className="flex items-center bg-[#151824] border border-slate-800 rounded-lg p-1 gap-1">
            {POPULAR_TICKERS.map((t) => (
              <button
                key={t}
                onClick={() => {
                  setTicker(t);
                  if (t !== "GENERAL" && t !== "CUSTOM") {
                    applyTickerToInput(t);
                  }
                }}
                className={`px-2.5 py-1 text-xs font-mono font-semibold rounded-md transition-all ${
                  ticker === t
                    ? "bg-[#4d9fff] text-black shadow-sm"
                    : "text-slate-400 hover:text-white hover:bg-slate-800"
                }`}
              >
                {t === "GENERAL" ? "🌐 General" : t}
              </button>
            ))}
            <button
              onClick={() => setTicker("CUSTOM")}
              className={`px-2 py-1 text-xs font-mono rounded-md transition-all ${
                ticker === "CUSTOM"
                  ? "bg-[#4d9fff] text-black font-semibold"
                  : "text-slate-400 hover:text-white"
              }`}
            >
              Other
            </button>
          </div>

          {ticker === "CUSTOM" && (
            <input
              type="text"
              placeholder="TICKER"
              value={customTicker}
              onChange={(e) => {
                const v = e.target.value.toUpperCase();
                setCustomTicker(v);
                setMenuTickerInput(v);
              }}
              className="w-24 bg-[#151824] border border-slate-700 text-white font-mono text-xs px-2.5 py-1.5 rounded-lg focus:outline-none focus:border-[#4d9fff]"
            />
          )}

          {/* V4 Alert Tickers Selector Button */}
          <button
            onClick={() => {
              setV4SaveStatus("");
              setShowV4Modal(true);
            }}
            className="text-xs border px-2.5 py-1.5 rounded-lg transition-colors flex items-center gap-1.5 font-mono bg-cyan-500/10 border-cyan-500/30 text-cyan-300 hover:bg-cyan-500/20"
            title="Choose tickers for 8:00 AM Playbook & 8:35 AM Post-Open Telegram Alerts (SPY, SPXW, QQQ, etc.)"
          >
            <span>🔔</span>
            <span>V4 Alerts: {v4AlertTickers.join(", ")}</span>
          </button>

          {/* Model Selector Dropdown */}
          <div className="flex items-center">
            <select
              value={selectedModel}
              onChange={(e) => {
                const val = e.target.value;
                setSelectedModel(val);
                if (typeof window !== "undefined") {
                  localStorage.setItem("stockpulse_ai_model", val);
                }
              }}
              className="bg-[#151824] border border-slate-700 text-xs text-emerald-400 font-mono rounded-lg px-2.5 py-1.5 focus:outline-none cursor-pointer hover:border-slate-600 transition-colors"
              title="Select AI Engine Provider"
            >
              <option value="auto" className="bg-[#151824] text-white">⚡ Auto (Gemini 3.5 + Groq)</option>
              <option value="gemini" className="bg-[#151824] text-white">🧠 Gemini 3.5 Flash</option>
              <option value="gemini-2" className="bg-[#151824] text-white">🧠 Gemini 3.5 Flash Lite</option>
              <option value="groq" className="bg-[#151824] text-white">🚀 Groq Cloud (Qwen 27B)</option>
              <option value="groq-120b" className="bg-[#151824] text-white">🚀 Groq Cloud (120B)</option>
              <option value="cerebras" className="bg-[#151824] text-white">⚡ Cerebras (1800 t/s)</option>
              <option value="openrouter" className="bg-[#151824] text-white">🌐 OpenRouter (Free)</option>
              <option value="ollama" className="bg-[#151824] text-white">💻 Local Ollama</option>
            </select>
          </div>

          <button
            onClick={() => {
              setKeyInput(apiKey);
              setShowKeyModal(true);
            }}
            className="text-xs border px-2.5 py-1.5 rounded-lg transition-colors flex items-center gap-1.5 font-mono bg-emerald-500/10 border-emerald-500/30 text-emerald-400 hover:bg-emerald-500/20"
            title="Configure AI Cloud Key (Gemini / Groq / OpenRouter)"
          >
            <span>🔑</span>
            <span>
              {apiKey && isGemPrefix(apiKey)
                ? "Gemini Active"
                : apiKey?.startsWith("gsk_")
                ? "Groq Active"
                : apiKey
                ? "Key Active"
                : "Cloud AI Active"}
            </span>
          </button>

          <button
            onClick={clearChat}
            className="text-xs text-slate-400 hover:text-red-400 border border-slate-800 hover:border-red-500/30 px-3 py-1.5 rounded-lg transition-colors flex items-center gap-1.5 font-medium ml-auto sm:ml-0"
            title="Clear Chat History"
          >
            <span>🗑️</span>
            <span>Clear</span>
          </button>
        </div>
      </div>

      {/* ── MAIN WORKSPACE: LEFT STRATEGY MENU + CHAT WORKSTATION ─────── */}
      <div className="flex flex-col lg:flex-row gap-4 items-stretch">
        {showLeftMenu && (
          <aside className="w-full lg:w-72 xl:w-80 shrink-0 bg-[#0a0c13] border border-[#1a1d2e] rounded-xl flex flex-col h-auto lg:h-[calc(100vh-130px)] lg:min-h-[600px] shadow-2xl overflow-hidden">
            {/* Left Menu Header & <ticker> Quick-Fill */}
            <div className="p-3.5 bg-[#0d111d] border-b border-[#1f273d] space-y-2.5">
              <div className="flex items-center justify-between">
                <div className="flex items-center gap-2">
                  <span className="text-sm">📋</span>
                  <h2 className="text-xs font-bold text-white uppercase tracking-wider font-mono">
                    Command Menu
                  </h2>
                </div>
                <span className="text-[10px] font-mono text-cyan-400 bg-cyan-500/10 border border-cyan-500/30 px-1.5 py-0.5 rounded">
                  Click → Chat
                </span>
              </div>

              {/* Optional <ticker> Quick Fill or Keep <ticker> Placeholder */}
              <div className="space-y-1.5">
                <div className="flex items-center gap-1.5">
                  <input
                    type="text"
                    value={menuTickerInput}
                    onChange={(e) => {
                      const val = e.target.value.toUpperCase().replace(/[^A-Z0-9.^$-]/g, "");
                      setMenuTickerInput(val);
                      if (val && input.includes("<ticker>")) {
                        setInput(input.replace(/<ticker>/gi, val));
                      }
                    }}
                    placeholder="Leave blank for <ticker> or type e.g. TSLA"
                    className="flex-1 bg-[#121626] border border-slate-700/80 rounded-lg px-2.5 py-1 text-[11px] font-mono text-white placeholder-slate-500 focus:outline-none focus:border-[#4d9fff]"
                  />
                  {menuTickerInput && (
                    <button
                      onClick={() => setMenuTickerInput("")}
                      className="text-[10px] font-mono text-amber-300 hover:text-amber-200 px-2 py-1 rounded bg-amber-500/10 border border-amber-500/30"
                      title="Reset to <ticker> placeholder"
                    >
                      Use &lt;ticker&gt;
                    </button>
                  )}
                </div>
                <div className="flex flex-wrap gap-1">
                  <button
                    type="button"
                    onClick={() => setMenuTickerInput("")}
                    className={`px-1.5 py-0.5 rounded text-[10px] font-mono border transition-all ${
                      !menuTickerInput
                        ? "bg-amber-500/20 border-amber-500/50 text-amber-300 font-bold"
                        : "bg-[#121626] border-slate-800 text-slate-400 hover:text-white"
                    }`}
                  >
                    &lt;ticker&gt;
                  </button>
                  {["SPY", "SPXW", "QQQ", "TSLA", "NVDA", "META", "AAPL", "AMD"].map((sym) => (
                    <button
                      key={sym}
                      type="button"
                      onClick={() => applyTickerToInput(sym)}
                      className={`px-1.5 py-0.5 rounded text-[10px] font-mono border transition-all ${
                        menuTickerInput === sym
                          ? "bg-[#4d9fff] border-[#4d9fff] text-black font-bold"
                          : "bg-[#121626] border-slate-800 text-slate-400 hover:text-white hover:border-slate-700"
                      }`}
                    >
                      {sym}
                    </button>
                  ))}
                </div>
              </div>
            </div>

            {/* Scrollable Command Groups */}
            <div className="flex-1 overflow-y-auto p-2.5 space-y-3">
              {leftMenuGroups.map((grp, gIdx) => (
                <div key={gIdx} className="space-y-1">
                  <div className="px-1.5 text-[10px] font-mono font-bold uppercase tracking-wider text-slate-400">
                    {grp.group}
                  </div>
                  <div className="space-y-1">
                    {grp.items.map((item, iIdx) => {
                      const displayLabel = menuTickerInput
                        ? item.label.replace(/<ticker>/gi, menuTickerInput)
                        : item.label;
                      return (
                        <div
                          key={iIdx}
                          className="group flex items-center gap-1 bg-[#111522] hover:bg-[#171d30] border border-[#1c2338] hover:border-[#4d9fff]/50 rounded-lg p-1.5 transition-all"
                        >
                          <button
                            type="button"
                            onClick={() => insertPromptIntoChat(item.template)}
                            className="flex-1 text-left px-1.5 py-0.5 flex flex-col gap-0.5 min-w-0"
                            title="Click to insert into chat input with <ticker>"
                          >
                            <div className="flex items-center justify-between gap-1.5">
                              <span className="text-xs font-semibold text-slate-200 group-hover:text-white truncate">
                                {displayLabel}
                              </span>
                              <span className="text-[9px] font-mono px-1.5 py-0.5 rounded bg-slate-800/90 text-cyan-300 border border-slate-700/60 shrink-0">
                                {item.badge}
                              </span>
                            </div>
                            <span className="text-[10px] font-mono text-slate-400 truncate">
                              {menuTickerInput
                                ? item.template.replace(/<ticker>/gi, menuTickerInput)
                                : item.template}
                            </span>
                          </button>
                          <button
                            type="button"
                            disabled={isStreaming}
                            onClick={() => handleSend(item.template)}
                            className="px-2 py-1.5 rounded-md bg-emerald-500/10 hover:bg-emerald-500/25 border border-emerald-500/30 text-emerald-300 text-[10px] font-mono font-bold shrink-0 transition-colors disabled:opacity-40"
                            title="Send immediately"
                          >
                            ▶
                          </button>
                        </div>
                      );
                    })}
                  </div>
                </div>
              ))}
            </div>
          </aside>
        )}

      {/* ── CHAT WORKSTATION ─────────────────────────────────────────── */}
      <div className="flex-1 min-w-0 bg-[#0a0c13] border border-[#1a1d2e] rounded-xl flex flex-col h-[calc(100vh-130px)] min-h-[600px] shadow-2xl relative overflow-hidden">
        {/* Pulse Strategy Suite Banner: Pulse (8:35 Day/Week) | Long Term Pulse (Month & Day) | Earning Pulse (Quarter & Month) + Backtest */}
        {isV4Ticker && (
          <div className="bg-[#0d111d] border-b border-[#1f273d] p-3 sm:p-4 px-4 sm:px-6 transition-all">
            <div className="flex flex-wrap items-center justify-between gap-2 mb-2.5 pb-2 border-b border-[#1a2035]">
              <div className="flex flex-wrap items-center gap-1.5">
                <button
                  onClick={() => setPulseMode("pulse")}
                  className={`px-2.5 py-1 text-[11px] font-mono font-semibold rounded-md border transition-all ${
                    pulseMode === "pulse"
                      ? "bg-emerald-500/20 border-emerald-500/50 text-emerald-300"
                      : "bg-[#121626] border-slate-800 text-slate-400 hover:text-white"
                  }`}
                >
                  🎯 Pulse (8:35 Day/Week)
                </button>
                <button
                  onClick={() => setPulseMode("long_term")}
                  className={`px-2.5 py-1 text-[11px] font-mono font-semibold rounded-md border transition-all ${
                    pulseMode === "long_term"
                      ? "bg-cyan-500/20 border-cyan-500/50 text-cyan-300"
                      : "bg-[#121626] border-slate-800 text-slate-400 hover:text-white"
                  }`}
                >
                  📅 Long Term Pulse (Month & Day)
                </button>
                <button
                  onClick={() => setPulseMode("earning")}
                  className={`px-2.5 py-1 text-[11px] font-mono font-semibold rounded-md border transition-all ${
                    pulseMode === "earning"
                      ? "bg-purple-500/20 border-purple-500/50 text-purple-300"
                      : "bg-[#121626] border-slate-800 text-slate-400 hover:text-white"
                  }`}
                >
                  ⚡ Earning Pulse (Quarter & Month)
                </button>
              </div>

              <div className="flex items-center gap-2">
                <span className="text-[11px] font-mono text-slate-400">⏪ Backtest Date:</span>
                <input
                  type="date"
                  value={pulseDate}
                  onChange={(e) => setPulseDate(e.target.value)}
                  className="bg-[#121626] border border-slate-700 text-white font-mono text-[11px] px-2 py-1 rounded-md focus:outline-none focus:border-[#4d9fff]"
                />
                {pulseDate && (
                  <button
                    onClick={() => setPulseDate("")}
                    className="text-[11px] font-mono px-2 py-1 rounded-md bg-slate-800 text-slate-300 hover:text-white"
                  >
                    Today
                  </button>
                )}
              </div>
            </div>

            {spyLoading && !spyOutcome ? (
              <div className="flex items-center gap-2 text-xs font-mono text-slate-400 py-1">
                <span className="w-2 h-2 rounded-full bg-emerald-400 animate-ping"></span>
                <span>
                  Calculating {currentActiveTicker}{" "}
                  {pulseMode === "long_term"
                    ? "Long Term Pulse (Month & Day High/Lows)"
                    : pulseMode === "earning"
                    ? "Earning Pulse (Quarterly & Monthly High/Lows)"
                    : "Pulse (8:35 AM Post-Open Playbook)"}
                  {pulseDate ? ` & Backtest for ${pulseDate}` : ""}...
                </span>
              </div>
            ) : spyOutcome ? (
              <div className="space-y-2.5">
                <div className="flex flex-wrap items-center justify-between gap-2">
                  <div className="flex flex-wrap items-center gap-2">
                    <span className="text-sm">
                      {pulseMode === "long_term" ? "📅" : pulseMode === "earning" ? "⚡" : "🎯"}
                    </span>
                    <span className="text-xs font-bold uppercase tracking-wider text-slate-300">
                      {currentActiveTicker}{" "}
                      {pulseMode === "long_term"
                        ? "Long Term Pulse"
                        : pulseMode === "earning"
                        ? "Earning Pulse"
                        : "8:35 AM Pulse"}{" "}
                      {spyOutcome.scan_date ? `(${spyOutcome.scan_date})` : ""}
                    </span>
                    <span
                      className={`text-[11px] font-mono px-2 py-0.5 rounded-full font-semibold border ${
                        spyOutcome.valid_outcome?.includes("Outcome 1") || spyOutcome.trade_idea?.action?.includes("CALLS")
                          ? "bg-emerald-500/15 border-emerald-500/40 text-emerald-400"
                          : spyOutcome.valid_outcome?.includes("Outcome 2") || spyOutcome.trade_idea?.action?.includes("PUTS")
                          ? "bg-red-500/15 border-red-500/40 text-red-400"
                          : "bg-amber-500/15 border-amber-500/40 text-amber-400"
                      }`}
                    >
                      {spyOutcome.valid_outcome}
                    </span>
                  </div>
                  <div className="text-[11px] font-mono text-slate-400 flex flex-wrap items-center gap-2">
                    <span>
                      {currentActiveTicker}: <b className="text-white">${spyOutcome.price?.toFixed(2)}</b>
                    </span>
                    {pulseMode === "earning" ? (
                      <>
                        <span>·</span>
                        <span>PQH: <b>${spyOutcome.key_levels?.pqh?.toFixed(2)}</b></span>
                        <span>·</span>
                        <span>PQL: <b>${spyOutcome.key_levels?.pql?.toFixed(2)}</b></span>
                        <span>·</span>
                        <span>PMH: <b>${spyOutcome.key_levels?.pmh?.toFixed(2)}</b></span>
                        <span>·</span>
                        <span>PML: <b>${spyOutcome.key_levels?.pml?.toFixed(2)}</b></span>
                      </>
                    ) : pulseMode === "long_term" ? (
                      <>
                        <span>·</span>
                        <span>PMH: <b>${spyOutcome.key_levels?.pmh?.toFixed(2)}</b></span>
                        <span>·</span>
                        <span>PML: <b>${spyOutcome.key_levels?.pml?.toFixed(2)}</b></span>
                        <span>·</span>
                        <span>PDH: <b>${spyOutcome.key_levels?.pdh?.toFixed(2)}</b></span>
                        <span>·</span>
                        <span>PDL: <b>${spyOutcome.key_levels?.pdl?.toFixed(2)}</b></span>
                      </>
                    ) : (
                      <>
                        <span>·</span>
                        <span>PDH: <b>${spyOutcome.key_levels?.pdh?.toFixed(2)}</b></span>
                        <span>·</span>
                        <span>PDL: <b>${spyOutcome.key_levels?.pdl?.toFixed(2)}</b></span>
                        <span>·</span>
                        <span>Mid: <b>${spyOutcome.key_levels?.pd_mid?.toFixed(2)}</b></span>
                      </>
                    )}
                  </div>
                </div>

                <div className="grid grid-cols-2 sm:grid-cols-4 gap-2 bg-[#121626] border border-[#1d243b] rounded-lg p-2.5 text-xs">
                  <div>
                    <span className="text-[10px] text-slate-400 block font-mono">OPEN CONTRACT (ATM)</span>
                    <span className="font-bold text-white font-mono">
                      {spyOutcome.trade_idea?.open_contract || spyOutcome.trade_idea?.suggested_contract || `${currentActiveTicker} ATM`}
                    </span>
                  </div>
                  <div>
                    <span className="text-[10px] text-slate-400 block font-mono">ENTRY TRIGGER</span>
                    <span className="font-semibold text-emerald-400 font-mono">${spyOutcome.trade_idea?.entry_level?.toFixed(2)}</span>
                  </div>
                  <div>
                    <span className="text-[10px] text-slate-400 block font-mono">STOP LOSS</span>
                    <span className="font-semibold text-red-400 font-mono">${spyOutcome.trade_idea?.stop_loss?.toFixed(2)}</span>
                  </div>
                  <div>
                    <span className="text-[10px] text-slate-400 block font-mono">TARGETS (R:R {spyOutcome.trade_idea?.reward_risk})</span>
                    <span className="font-semibold text-cyan-400 font-mono">
                      ${spyOutcome.trade_idea?.target_1?.toFixed(2)} / ${spyOutcome.trade_idea?.target_2?.toFixed(2)}
                    </span>
                  </div>
                </div>

                {(spyOutcome.trade_idea?.cost_efficient_otm || spyOutcome.trade_idea?.cost_efficient_spread || spyOutcome.trade_idea?.cost_efficient_butterfly) && (
                  <div className="bg-[#101626] border border-cyan-500/25 rounded-lg px-3 py-2 flex flex-col gap-1.5 text-[11px] font-mono">
                    <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-1.5">
                      <div className="flex flex-wrap items-center gap-2">
                        <span className="text-cyan-300 font-bold uppercase">💡 Cost-Efficient Options:</span>
                        {spyOutcome.trade_idea?.cost_efficient_otm && (
                          <span className="text-slate-200">
                            <b className="text-emerald-300">OTM Runner:</b> {spyOutcome.trade_idea.cost_efficient_otm}
                          </span>
                        )}
                      </div>
                      {spyOutcome.trade_idea?.cost_efficient_spread && (
                        <div className="text-slate-200">
                          <b className="text-purple-300">Debit Spread:</b> {spyOutcome.trade_idea.cost_efficient_spread}
                        </div>
                      )}
                    </div>
                    {spyOutcome.trade_idea?.cost_efficient_butterfly && (
                      <div className="text-slate-200 border-t border-cyan-500/15 pt-1">
                        <b className="text-amber-300">🦋 Butterfly (T1 Pin):</b> {spyOutcome.trade_idea.cost_efficient_butterfly}
                      </div>
                    )}
                  </div>
                )}

                {spyOutcome.backtest && (
                  <div className="bg-[#101524] border border-[#1e2942] rounded-lg px-3 py-2 flex flex-wrap items-center justify-between gap-2 text-[11px] font-mono">
                    <div className="flex flex-wrap items-center gap-2">
                      <span className="text-slate-400 uppercase">Backtest:</span>
                      <span
                        className={`px-2 py-0.5 rounded font-bold ${
                          spyOutcome.backtest.result_badge?.includes("WIN")
                            ? "bg-emerald-500/15 text-emerald-300 border border-emerald-500/30"
                            : spyOutcome.backtest.result_badge?.includes("STOPPED")
                            ? "bg-red-500/15 text-red-300 border border-red-500/30"
                            : "bg-amber-500/15 text-amber-300 border border-amber-500/30"
                        }`}
                      >
                        {spyOutcome.backtest.result_badge}
                      </span>
                      <span className="text-slate-300">{spyOutcome.backtest.summary}</span>
                    </div>
                    <div className="text-slate-400">
                      MFE: <b className="text-emerald-400">{spyOutcome.backtest.max_favorable_excursion}</b> · MAE:{" "}
                      <b className="text-red-400">{spyOutcome.backtest.max_adverse_excursion}</b>
                    </div>
                  </div>
                )}

                <div className="flex flex-wrap items-center justify-between gap-2 text-xs text-slate-300">
                  <p className="text-[11px] text-slate-400 line-clamp-1 italic">
                    💡 {spyOutcome.trade_idea?.rationale}
                  </p>
                  <div className="flex items-center gap-3 ml-auto">
                    <button
                      onClick={() => {
                        setV4SaveStatus("");
                        setShowV4Modal(true);
                      }}
                      className="text-[11px] font-mono text-cyan-400 hover:text-cyan-300 hover:underline"
                    >
                      ⚙️ Configure 8:00/8:35 Alerts
                    </button>
                    <button
                      onClick={() =>
                        handleSend(
                          pulseMode === "long_term"
                            ? `What is ${currentActiveTicker} Long Term Pulse${pulseDate ? ` on ${pulseDate}` : " today"}? Check Month (PMH/PML) and Day (PDH/PDL) Highs & Lows, trade plan, and backtest details.`
                            : pulseMode === "earning"
                            ? `What is ${currentActiveTicker} Earning Pulse${pulseDate ? ` on ${pulseDate}` : " today"}? Check Quarterly (PQH/PQL) and Monthly (PMH/PML) Highs & Lows, trade plan, and backtest details.`
                            : `What is ${currentActiveTicker} pulse${pulseDate ? ` on ${pulseDate}` : " today"}? Provide the validated outcome, key levels, Calls vs Puts trade idea, and backtest verification.`
                        )
                      }
                      disabled={isStreaming}
                      className="text-[11px] font-semibold text-[#4d9fff] hover:text-[#7bb7ff] flex items-center gap-1 hover:underline"
                    >
                      <span>💬 Deep Dive in Chat →</span>
                    </button>
                  </div>
                </div>
              </div>
            ) : null}
          </div>
        )}

        {/* Messages List */}
        <div className="flex-1 overflow-y-auto p-4 sm:p-6 space-y-4">
          {messages.map((m, idx) => (
            <div
              key={idx}
              className={`flex gap-3 ${m.role === "user" ? "justify-end" : "justify-start"}`}
            >
              {m.role === "assistant" && (
                <div className="w-8 h-8 rounded-lg bg-emerald-500/10 border border-emerald-500/30 flex items-center justify-center text-sm shrink-0 mt-0.5">
                  🤖
                </div>
              )}

              <div
                className={`${
                  m.role === "user"
                    ? "max-w-[85%] sm:max-w-[75%] bg-[#1f3a5f] text-white border border-[#4d9fff]/30 rounded-tr-none shadow-md"
                    : "max-w-[96%] sm:max-w-[92%] bg-[#121520] text-slate-200 border border-[#1e2337] rounded-tl-none shadow-sm overflow-hidden"
                } rounded-2xl p-4 text-sm leading-relaxed`}
              >
                {/* Formatted Content */}
                <div className="font-sans text-xs sm:text-sm">
                  {m.content ? (
                    formatChatMarkdown(m.content)
                  ) : (
                    <div className="flex items-center gap-2 text-slate-400 font-mono text-xs py-1">
                      <span className="w-2 h-2 rounded-full bg-emerald-400 animate-ping"></span>
                      <span>Thinking with institutional trade engine...</span>
                    </div>
                  )}
                </div>
              </div>

              {m.role === "user" && (
                <div className="w-8 h-8 rounded-lg bg-[#1f3a5f] border border-[#4d9fff]/40 flex items-center justify-center text-xs font-bold text-white shrink-0 mt-0.5">
                  YOU
                </div>
              )}
            </div>
          ))}

          {/* Active Tool Notification Banner */}
          {activeTool && (
            <div className="flex items-center gap-2 text-xs font-mono text-emerald-400 bg-emerald-950/30 border border-emerald-800/50 rounded-lg px-3 py-2 animate-pulse w-fit">
              <span className="animate-spin">⚙️</span>
              <span>{activeTool}</span>
            </div>
          )}

          <div ref={messagesEndRef} />
        </div>

        {/* Quick Action Suggestion Chips */}
        {messages.length <= 2 && (
          <div className="px-4 pb-3 border-t border-[#1a1d2e] pt-3 bg-[#0d0f17]/50">
            <p className="text-[11px] text-slate-400 font-medium mb-2 uppercase tracking-wider">
              Quick Suggestions for {currentActiveTicker}:
            </p>
            <div className="grid grid-cols-1 sm:grid-cols-2 gap-2">
              {quickPrompts.map((qp, i) => (
                <button
                  key={i}
                  disabled={isStreaming}
                  onClick={() => handleSend(qp.prompt)}
                  className="text-left text-xs bg-[#121520] hover:bg-[#1a2030] text-slate-300 hover:text-white border border-slate-800 hover:border-slate-700 p-2.5 rounded-lg transition-all flex flex-col gap-0.5 group"
                >
                  <span className="font-semibold text-emerald-400 group-hover:text-emerald-300">
                    {qp.title}
                  </span>
                  <span className="text-[11px] text-slate-400 line-clamp-1">{qp.prompt}</span>
                </button>
              ))}
            </div>
          </div>
        )}

        {/* Input Bar */}
        <div className="p-3 sm:p-4 bg-[#0d0f17] border-t border-[#1a1d2e] space-y-2">
          {input.includes("<ticker>") && (
            <div className="flex flex-wrap items-center justify-between gap-2 bg-amber-500/10 border border-amber-500/30 rounded-lg px-3 py-1.5 text-[11px] font-mono">
              <span className="text-amber-300 font-semibold">
                🎯 Type ticker to replace <code className="bg-black/40 px-1 py-0.5 rounded">&lt;ticker&gt;</code> or click a symbol:
              </span>
              <div className="flex flex-wrap items-center gap-1">
                {["SPY", "SPXW", "QQQ", "TSLA", "NVDA", "META", "AAPL", "AMD"].map((sym) => (
                  <button
                    key={sym}
                    type="button"
                    onClick={() => applyTickerToInput(sym)}
                    className="px-2 py-0.5 rounded bg-[#151b2e] hover:bg-[#4d9fff] text-cyan-300 hover:text-black border border-cyan-500/30 font-bold text-[10px] transition-colors"
                  >
                    {sym}
                  </button>
                ))}
              </div>
            </div>
          )}
          <div className="flex gap-2">
            <textarea
              ref={inputRef}
              rows={2}
              value={input}
              onChange={(e) => setInput(e.target.value)}
              onKeyDown={handleKeyDown}
              disabled={isStreaming}
              placeholder={`Click any command in the Left Menu (inserts <ticker>) or ask AI about ${currentActiveTicker} setup (Press Enter to send)...`}
              className="flex-1 bg-[#151824] border border-slate-800 rounded-xl px-3.5 py-2.5 text-xs sm:text-sm text-white placeholder-slate-500 focus:outline-none focus:border-[#4d9fff] resize-none disabled:opacity-50"
            />
            <button
              onClick={() => handleSend()}
              disabled={!input.trim() || isStreaming}
              className="bg-[#4d9fff] hover:bg-[#3a8eef] disabled:bg-slate-800 disabled:text-slate-600 text-black font-bold px-4 rounded-xl transition-all flex items-center justify-center shrink-0"
            >
              {isStreaming ? (
                <div className="w-5 h-5 border-2 border-black border-t-transparent rounded-full animate-spin"></div>
              ) : (
                <span className="text-base font-bold">↑</span>
              )}
            </button>
          </div>
          <div className="flex items-center justify-between mt-2 text-[10px] text-slate-500 font-mono">
            <span>StockPulse AI · Real-Time Institutional Trade Plan Engine</span>
            <span>Shift + Enter for new line</span>
          </div>
        </div>
      </div>
      </div>

      {/* ── API KEY CONFIG MODAL ────────────────────────────────────────── */}
      {showKeyModal && (
        <div className="fixed inset-0 z-50 bg-black/75 backdrop-blur-sm flex items-center justify-center p-4">
          <div className="bg-[#121520] border border-[#1e2337] rounded-2xl max-w-md w-full p-6 space-y-4 shadow-2xl animate-in fade-in zoom-in-95">
            <div className="flex items-center justify-between">
              <div className="flex items-center gap-2">
                <span className="text-xl">🔑</span>
                <h3 className="text-base font-bold text-white">AI Engine Key Setup</h3>
              </div>
              <button
                onClick={() => setShowKeyModal(false)}
                className="text-slate-400 hover:text-white text-sm p-1 rounded-md hover:bg-slate-800"
              >
                ✕
              </button>
            </div>

            <p className="text-xs text-slate-300 leading-relaxed">
              Connect your cloud AI provider (e.g. Groq ~800 t/s, Gemini Flash, or OpenRouter) for instant institutional responses. Your key is saved locally in your browser and sent securely with your inference requests.
            </p>

            <div className="space-y-1.5">
              <label className="text-[11px] font-mono text-slate-400 uppercase tracking-wider">
                API Key (Gemini / Groq / OpenRouter)
              </label>
              <input
                type="password"
                value={keyInput}
                onChange={(e) => setKeyInput(e.target.value.trim())}
                placeholder="AIzaSy... (Gemini) or gsk_... (Groq) or sk-or-..."
                className="w-full bg-[#0a0c13] border border-slate-700 rounded-lg px-3 py-2 text-xs font-mono text-white focus:outline-none focus:border-[#4d9fff]"
              />
              {keyInput && (
                <div className="text-[11px] font-mono pt-0.5">
                  {isGemPrefix(keyInput) ? (
                    <span className="text-emerald-400 font-semibold">✓ Google Gemini Key Recognized</span>
                  ) : keyInput.startsWith("gsk_") ? (
                    <span className="text-emerald-400 font-semibold">✓ Groq Cloud Key Recognized</span>
                  ) : keyInput.startsWith("sk-or-") ? (
                    <span className="text-emerald-400 font-semibold">✓ OpenRouter Key Recognized</span>
                  ) : keyInput.startsWith("csk-") ? (
                    <span className="text-emerald-400 font-semibold">✓ Cerebras Key Recognized</span>
                  ) : (
                    <span className="text-slate-400">ℹ️ Custom Key Entered</span>
                  )}
                </div>
              )}
            </div>

            <div className="flex items-center justify-between pt-2">
              {apiKey ? (
                <button
                  type="button"
                  onClick={() => {
                    localStorage.removeItem("stockpulse_ai_key");
                    setApiKey("");
                    setKeyInput("");
                    setShowKeyModal(false);
                    fetchStatus("");
                  }}
                  className="text-xs text-red-400 hover:text-red-300 font-medium px-3 py-1.5 rounded-lg border border-red-500/20 hover:border-red-500/40"
                >
                  Clear Key
                </button>
              ) : (
                <span />
              )}

              <div className="flex gap-2">
                <button
                  type="button"
                  onClick={() => setShowKeyModal(false)}
                  className="text-xs text-slate-400 hover:text-white px-3 py-1.5 rounded-lg border border-slate-800 hover:bg-slate-800"
                >
                  Cancel
                </button>
                <button
                  type="button"
                  onClick={() => {
                    if (keyInput) {
                      localStorage.setItem("stockpulse_ai_key", keyInput);
                      setApiKey(keyInput);
                      if (isGemPrefix(keyInput)) {
                        setSelectedModel("gemini");
                        localStorage.setItem("stockpulse_ai_model", "gemini");
                      } else if (keyInput.startsWith("gsk_")) {
                        setSelectedModel("groq");
                        localStorage.setItem("stockpulse_ai_model", "groq");
                      }
                      fetchStatus(keyInput);
                    }
                    setShowKeyModal(false);
                  }}
                  className="text-xs font-semibold bg-[#4d9fff] text-black px-4 py-1.5 rounded-lg hover:bg-[#3b82f6] transition-colors"
                >
                  Save & Connect
                </button>
              </div>
            </div>
          </div>
        </div>
      )}

      {/* ── V4 ALERT TICKERS CONFIG MODAL ───────────────────────────────── */}
      {showV4Modal && (
        <div className="fixed inset-0 z-50 bg-black/75 backdrop-blur-sm flex items-center justify-center p-4">
          <div className="bg-[#121520] border border-[#1e2337] rounded-2xl max-w-lg w-full p-6 space-y-4 shadow-2xl animate-in fade-in zoom-in-95">
            <div className="flex items-center justify-between">
              <div className="flex items-center gap-2">
                <span className="text-xl">🔔</span>
                <div>
                  <h3 className="text-base font-bold text-white">Day Trading V4 Telegram Alert Tickers</h3>
                  <p className="text-[11px] text-slate-400">
                    8:00 AM CT Playbook · 8:35 AM CT 5-Min Post-Open Validation · 3:40 PM CT Review
                  </p>
                </div>
              </div>
              <button
                onClick={() => setShowV4Modal(false)}
                className="text-slate-400 hover:text-white text-sm p-1 rounded-md hover:bg-slate-800"
              >
                ✕
              </button>
            </div>

            <p className="text-xs text-slate-300 leading-relaxed">
              Choose which tickers receive automated Day Trading V4 Telegram alerts (PDH/PDL/PWH/PWL levels, 8:00 AM Playbook, and 8:35 AM single-outcome Calls vs Puts validation).{" "}
              <code className="bg-slate-800 text-cyan-300 px-1 py-0.5 rounded font-mono text-[11px]">SPXW</code>{" "}
              uses live S&amp;P 500 Index (<code className="bg-slate-800 text-cyan-300 px-1 py-0.5 rounded font-mono text-[11px]">^GSPC</code>) bars with $5 strike increments.
            </p>

            <div className="space-y-2">
              <label className="text-[11px] font-mono text-slate-400 uppercase tracking-wider block">
                Toggle Tickers (Click to Enable / Disable)
              </label>
              <div className="flex flex-wrap gap-2">
                {Array.from(new Set([...v4PopularOptions, ...v4AlertTickers])).map((sym) => {
                  const active = v4AlertTickers.includes(sym);
                  return (
                    <button
                      key={sym}
                      type="button"
                      onClick={() => {
                        if (active) {
                          if (v4AlertTickers.length > 1) {
                            setV4AlertTickers(v4AlertTickers.filter((t) => t !== sym));
                          }
                        } else {
                          setV4AlertTickers([...v4AlertTickers, sym]);
                        }
                      }}
                      className={`px-3 py-1.5 rounded-lg text-xs font-mono font-semibold border transition-all flex items-center gap-1.5 ${
                        active
                          ? "bg-emerald-500/20 border-emerald-500/50 text-emerald-300 shadow-sm"
                          : "bg-[#0a0c13] border-slate-800 text-slate-400 hover:text-white hover:border-slate-700"
                      }`}
                    >
                      <span>{active ? "✓" : "+"}</span>
                      <span>{sym}</span>
                    </button>
                  );
                })}
              </div>
            </div>

            {/* Add Custom Ticker */}
            <div className="flex items-center gap-2 pt-1">
              <input
                type="text"
                value={v4CustomInput}
                onChange={(e) => setV4CustomInput(e.target.value.toUpperCase().replace(/[^A-Z0-9.^$-]/g, ""))}
                placeholder="Add custom ticker (e.g. QQQ, NDX, COIN)..."
                className="flex-1 bg-[#0a0c13] border border-slate-700 rounded-lg px-3 py-1.5 text-xs font-mono text-white focus:outline-none focus:border-[#4d9fff]"
              />
              <button
                type="button"
                onClick={() => {
                  const clean = v4CustomInput.trim().toUpperCase();
                  if (clean && !v4AlertTickers.includes(clean)) {
                    setV4AlertTickers([...v4AlertTickers, clean]);
                  }
                  setV4CustomInput("");
                }}
                className="text-xs font-mono bg-slate-800 hover:bg-slate-700 text-white px-3 py-1.5 rounded-lg border border-slate-700"
              >
                + Add Ticker
              </button>
            </div>

            {v4SaveStatus && (
              <div className="text-xs font-mono px-3 py-2 rounded-lg bg-emerald-950/40 border border-emerald-700/40 text-emerald-300">
                {v4SaveStatus}
              </div>
            )}

            <div className="flex flex-wrap items-center justify-between gap-2 pt-2 border-t border-[#1e2337]">
              <div className="flex flex-wrap gap-2">
                <button
                  type="button"
                  disabled={v4Saving}
                  onClick={() => saveV4Tickers(v4AlertTickers, { sendPlaybook: true })}
                  className="text-[11px] font-mono bg-emerald-500/15 hover:bg-emerald-500/25 text-emerald-300 border border-emerald-500/30 px-2.5 py-1.5 rounded-lg transition-colors disabled:opacity-50"
                >
                  📨 Send 8:00 AM Playbook Now
                </button>
                <button
                  type="button"
                  disabled={v4Saving}
                  onClick={() => saveV4Tickers(v4AlertTickers, { sendPostOpen: true })}
                  className="text-[11px] font-mono bg-cyan-500/15 hover:bg-cyan-500/25 text-cyan-300 border border-cyan-500/30 px-2.5 py-1.5 rounded-lg transition-colors disabled:opacity-50"
                >
                  ⚡ Send 8:35 AM Validation Now
                </button>
              </div>

              <div className="flex gap-2 ml-auto">
                <button
                  type="button"
                  onClick={() => setShowV4Modal(false)}
                  className="text-xs text-slate-400 hover:text-white px-3 py-1.5 rounded-lg border border-slate-800 hover:bg-slate-800"
                >
                  Close
                </button>
                <button
                  type="button"
                  disabled={v4Saving}
                  onClick={() => saveV4Tickers(v4AlertTickers)}
                  className="text-xs font-semibold bg-[#4d9fff] text-black px-4 py-1.5 rounded-lg hover:bg-[#3b82f6] transition-colors disabled:opacity-50"
                >
                  {v4Saving ? "Saving..." : "Save Alert Tickers"}
                </button>
              </div>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

function formatInlineMarkdown(text: string) {
  const parts = text.split(/(\*\*.*?\*\*|`[^`]+`|\[[^\]]+\]\([^)]+\))/g);
  return parts.map((part, i) => {
    if (part.startsWith("**") && part.endsWith("**") && part.length > 4) {
      return (
        <strong key={i} className="text-white font-bold">
          {part.slice(2, -2)}
        </strong>
      );
    }
    if (part.startsWith("`") && part.endsWith("`") && part.length > 2) {
      return (
        <code
          key={i}
          className="bg-slate-800/90 text-emerald-300 px-1.5 py-0.5 rounded font-mono text-[11px] border border-slate-700/60"
        >
          {part.slice(1, -1)}
        </code>
      );
    }
    const linkMatch = part.match(/^\[([^\]]+)\]\(([^)]+)\)$/);
    if (linkMatch) {
      return (
        <a
          key={i}
          href={linkMatch[2]}
          target="_blank"
          rel="noopener noreferrer"
          className="text-cyan-400 hover:text-cyan-300 underline"
        >
          {linkMatch[1]}
        </a>
      );
    }
    return part;
  });
}

function parseTableCells(rowLine: string): string[] {
  const trimmed = rowLine.trim();
  const withoutOuter = trimmed.replace(/^\|/, "").replace(/\|$/, "");
  return withoutOuter.split("|").map((c) => c.trim());
}

function isTableSeparatorLine(line: string): boolean {
  const trimmed = line.trim();
  if (!trimmed.includes("|") && !trimmed.includes("-")) return false;
  return /^\|?[\s:|-]+\|?$/.test(trimmed) && trimmed.includes("-");
}

/**
 * Full institutional Markdown renderer supporting GitHub-Flavored Markdown Tables,
 * Headings, Blockquotes, Lists, Dividers, Bold, Code ticks, and Links without external dependencies.
 */
function formatChatMarkdown(text: string) {
  const lines = text.replace(/\r\n/g, "\n").split("\n");
  const blocks: React.ReactNode[] = [];
  let i = 0;

  while (i < lines.length) {
    const line = lines[i];
    const trimmed = line.trim();

    // 1. Check for Markdown Table block (header row with `|` followed by `| :--- |` separator row)
    if (
      trimmed.includes("|") &&
      i + 1 < lines.length &&
      isTableSeparatorLine(lines[i + 1])
    ) {
      const headers = parseTableCells(trimmed);
      i += 2; // skip header + separator line
      const rows: string[][] = [];
      while (i < lines.length) {
        const rowTrim = lines[i].trim();
        if (!rowTrim || !rowTrim.includes("|")) break;
        if (isTableSeparatorLine(rowTrim)) {
          i++;
          continue;
        }
        rows.push(parseTableCells(rowTrim));
        i++;
      }

      blocks.push(
        <div
          key={`tbl-${i}`}
          className="my-3 overflow-x-auto rounded-xl border border-[#242c44] bg-[#0b0e17] shadow-inner"
        >
          <table className="w-full text-left border-collapse text-xs">
            <thead>
              <tr className="bg-[#151b2e] border-b border-[#242c44] text-cyan-300 font-mono text-[11px] uppercase tracking-wider">
                {headers.map((h, hIdx) => (
                  <th key={hIdx} className="py-2.5 px-3 font-semibold whitespace-nowrap">
                    {formatInlineMarkdown(h)}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody className="divide-y divide-[#1a2035]">
              {rows.map((row, rIdx) => (
                <tr
                  key={rIdx}
                  className={
                    rIdx % 2 === 0
                      ? "bg-[#0e121e]/90 hover:bg-[#161d31] transition-colors"
                      : "bg-[#121726]/90 hover:bg-[#161d31] transition-colors"
                  }
                >
                  {headers.map((_, cIdx) => (
                    <td key={cIdx} className="py-2 px-3 text-slate-200 align-top leading-relaxed">
                      {formatInlineMarkdown(row[cIdx] ?? "")}
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      );
      continue;
    }

    // 2. Empty line -> spacing
    if (!trimmed) {
      i++;
      continue;
    }

    // 3. Horizontal rule
    if (/^(-{3,}|\*{3,}|_{3,})$/.test(trimmed)) {
      blocks.push(<hr key={`hr-${i}`} className="border-[#1e263d] my-2.5" />);
      i++;
      continue;
    }

    // 4. Headings (#, ##, ###, ####)
    if (trimmed.startsWith("#### ")) {
      blocks.push(
        <h4 key={`h4-${i}`} className="text-xs sm:text-sm font-bold text-cyan-300 mt-3 mb-1">
          {formatInlineMarkdown(trimmed.slice(5))}
        </h4>
      );
      i++;
      continue;
    }
    if (trimmed.startsWith("### ")) {
      blocks.push(
        <h3 key={`h3-${i}`} className="text-sm sm:text-base font-bold text-emerald-300 mt-3.5 mb-1">
          {formatInlineMarkdown(trimmed.slice(4))}
        </h3>
      );
      i++;
      continue;
    }
    if (trimmed.startsWith("## ")) {
      blocks.push(
        <h2
          key={`h2-${i}`}
          className="text-sm sm:text-base font-bold text-white border-b border-[#1e263d] pb-1 mt-4 mb-1.5"
        >
          {formatInlineMarkdown(trimmed.slice(3))}
        </h2>
      );
      i++;
      continue;
    }
    if (trimmed.startsWith("# ")) {
      blocks.push(
        <h1
          key={`h1-${i}`}
          className="text-base sm:text-lg font-bold text-white border-b border-[#1e263d] pb-1.5 mt-4 mb-2"
        >
          {formatInlineMarkdown(trimmed.slice(2))}
        </h1>
      );
      i++;
      continue;
    }

    // 5. Blockquotes (> ...)
    if (trimmed.startsWith("> ")) {
      blocks.push(
        <blockquote
          key={`bq-${i}`}
          className="border-l-2 border-[#4d9fff] bg-[#151b2b]/60 pl-3 py-1.5 pr-2 rounded-r-lg text-slate-200 italic my-1.5"
        >
          {formatInlineMarkdown(trimmed.slice(2))}
        </blockquote>
      );
      i++;
      continue;
    }

    // 6. Bullet list item (- , * , • )
    const bulletMatch = line.match(/^(\s*)([-*•])\s+(.*)$/);
    if (bulletMatch) {
      const indent = Math.min(Math.floor(bulletMatch[1].length / 2), 3);
      blocks.push(
        <div
          key={`li-${i}`}
          style={{ paddingLeft: `${indent * 14}px` }}
          className="flex items-start gap-2 my-1"
        >
          <span className="text-emerald-400 font-bold select-none mt-0.5">•</span>
          <span className="flex-1 text-slate-200">{formatInlineMarkdown(bulletMatch[3])}</span>
        </div>
      );
      i++;
      continue;
    }

    // 7. Numbered list item (1. , 2. , etc.)
    const numMatch = line.match(/^(\s*)(\d+\.)\s+(.*)$/);
    if (numMatch) {
      const indent = Math.min(Math.floor(numMatch[1].length / 2), 3);
      blocks.push(
        <div
          key={`ol-${i}`}
          style={{ paddingLeft: `${indent * 14}px` }}
          className="flex items-start gap-2 my-1"
        >
          <span className="text-cyan-400 font-mono font-bold text-xs select-none mt-0.5">
            {numMatch[2]}
          </span>
          <span className="flex-1 text-slate-200">{formatInlineMarkdown(numMatch[3])}</span>
        </div>
      );
      i++;
      continue;
    }

    // 8. Standard paragraph line
    blocks.push(
      <p key={`p-${i}`} className="my-1.5 whitespace-pre-wrap">
        {formatInlineMarkdown(line)}
      </p>
    );
    i++;
  }

  return <div className="space-y-1">{blocks}</div>;
}
