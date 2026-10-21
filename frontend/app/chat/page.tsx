"use client";

import { useState, useRef, useEffect, useCallback } from "react";

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
  const [seconds805, setSeconds805] = useState<number | null>(null);
  const [seconds835, setSeconds835] = useState<number | null>(null);
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
  const [showPulseCard, setShowPulseCard] = useState<boolean>(false);
  const [menuTickerInput, setMenuTickerInput] = useState<string>("");
  const messagesEndRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLTextAreaElement>(null);

  const getSecPreEarningsPrompt = (sym: string) => `Role: You are a senior equity research analyst and institutional risk manager specializing in Healthcare & Growth sectors.

Task: Analyze the pre-earnings context for ${sym} to identify potential deviations from consensus expectations (surprises), guidance adjustments, or hidden risks.

Single Trusted Source Mandate:
- Exclusively evaluate the provided single trusted source: Official SEC EDGAR Regulatory Filings (Forms 10-Q & 8-K Exhibits) & Standardized Financial Statements.
- Do NOT pull from or search the open web, blogs, or unverified rumors.
- Do NOT hallucinate metrics not reported in official SEC filings; if an item is not reported, explicitly state 'Not reported in filing'.

Execute your analysis step-by-step using this structured framework:

1. THE CONSENSUS GAP BREAKDOWN:
- Compare the current consensus numbers against the run-rate implied by the prior quarter's KPIs and management commentary from the official SEC 8-K filing.
- Identify if the consensus baseline requires an aggressive acceleration or deceleration to be met.

2. NEGATIVE SPACE & MANAGEMENT DRIFT ANALYSIS:
- Review the prior earnings release & SEC 8-K disclosures. Identify areas where management was defensive, gave vague answers, or deferred metric updates to this upcoming quarter.
- Flag any changes in vocabulary regarding demand, pricing power, or macro headwinds.

3. WORKING CAPITAL & VOLATILITY BANDS:
- Audit the latest 10-Q working capital changes. Is inventory accumulation outpacing revenue growth (potential margin compression)? Is free cash flow lagging net income?
- Calculate a variance risk rating (Low/Medium/High) indicating the likelihood of an earnings or revenue miss/beat based on these structural drivers.

Output Format: Present your findings in a concise tabular overview outlining:
- Factor Analyzed
- Consensus Expectation
- Discrepancy/Signal Found
- Implied Earnings Outcome (Beat / In-Line / Miss / Guidance Cut)

Follow the table with a 4-sentence high-conviction narrative summarizing the primary directional thesis.`;

  const getSecPostEarningsPrompt = (sym: string) => `Act as a senior equity research analyst specializing in post-earnings price action and fundamental catalysts for ${sym}.

Evaluate the latest earnings report for ${sym} using the official SEC EDGAR filings (Form 8-K Item 2.02, EX-99.1 Press Release, EX-99.2 Deck, 10-Q/10-K) and verified consensus numbers automatically attached in your institutional context block. Break down the company's report, fundamental catalysts, and post-earnings market reaction. Do not extrapolate or hallucinate numbers.

Single Trusted Source Mandate:
- Exclusively evaluate the provided single trusted source: Official SEC EDGAR Regulatory Filings & Standardized Financial Statements.
- Do NOT pull from or search unverified open-web gossip, blogs, or forums.
- Do NOT extrapolate numbers; if an item is not disclosed in the SEC filings, explicitly state 'Not disclosed in SEC filing'.
- All official regulatory data, earnings actuals vs. consensus estimates, and filing links are already retrieved and attached in your context block. Do NOT ask the user to paste filings or transcripts.

Provide your analysis in the following structured format:

1. QUANTITATIVE SCORECARD (Actual vs. Consensus/Expectations)
   - Revenue: [Actual vs. Expected + % Beat/Miss]
   - Non-GAAP EPS: [Actual vs. Expected + % Beat/Miss]
   - Key Operating Metrics / KPIs: [List top 3-4 and performance]
   - Gross Margin / Operating Margin: [Expansion or contraction vs. prior year/expectations]

2. GUIDANCE & FORWARD ESTIMATES
   - Next Quarter / Full Year Revenue Guidance: [Raised / Lowered / Inline vs. Consensus]
   - Margin or Free Cash Flow Guidance: [Key directional shifts]
   - Phasing / Timing Anomalies: [Note any front-loaded or back-loaded revenue shifts like multi-quarter ramp schedules]

3. MANAGEMENT TONE & CREDIBILITY
   - Tone: [Confident / Defensive / Cautious / Evasive]
   - Key pivot or evasive response from management during Q&A regarding: [Identify specific topics]

4. BULL & BEAR CATALYSTS (Post-Earnings Outlook)
   - Primary Bull Catalyst: [What drives near-term upside / multiple expansion]
   - Primary Bear Risk / Red Flag: [What threatens retention, margins, or consensus numbers]
   - Consensus Revision Direction: [Will sell-side analysts have to raise or cut numbers following this call? By roughly how much?]

5. LIKELY SHORT-TERM PRICE ACTION HYPOTHESIS
   - Expected Reaction: [Gap Up / Gap Down / Muted & Range-bound]
   - Primary Driver of Reaction: [e.g., Guide-down despite a top-line beat; massive acceleration in KPI backlog]`;

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

      const urlPrompt = params.get("prompt") || params.get("mode");
      if (urlPrompt === "sec_audit") {
        const urlT = params.get("ticker") || "UNH";
        setTicker(urlT);
        setMenuTickerInput(urlT);
        setInput(getSecPreEarningsPrompt(urlT));
      } else if (urlPrompt === "sec_post_earnings" || urlPrompt === "post_earnings") {
        const urlT = params.get("ticker") || "UNH";
        setTicker(urlT);
        setMenuTickerInput(urlT);
        setInput(getSecPostEarningsPrompt(urlT));
      }
    }
    fetchStatus(keyToUse);
    fetchV4Tickers();
  }, []);

  const insertPromptIntoChat = (template: string) => {
    setShowPulseCard(false);
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
    setShowPulseCard(false);
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

    setShowPulseCard(false);
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

  const target805Ref = useRef<number | null>(null);
  const target835Ref = useRef<number | null>(null);
  const isFetchingRef = useRef<boolean>(false);
  const lastFetchTimeRef = useRef<number>(0);

  const fetchSpyOutcome = useCallback((showLoading: boolean = false) => {
    if (!isV4Ticker || isFetchingRef.current) return;

    // Minimum throttle gap of 1.5s between rapid calls
    if (Date.now() - lastFetchTimeRef.current < 1500) return;

    if (showLoading) setSpyLoading(true);
    isFetchingRef.current = true;
    lastFetchTimeRef.current = Date.now();

    const ts = Date.now();
    const q = `?ticker=${encodeURIComponent(currentActiveTicker)}&mode=${encodeURIComponent(pulseMode)}${
      pulseDate ? `&date=${encodeURIComponent(pulseDate)}` : ""
    }&_t=${ts}`;

    fetch(`/api/agent/spy-outcome${q}`, {
      cache: "no-store",
      headers: {
        "Cache-Control": "no-cache, no-store, must-revalidate",
        "Pragma": "no-cache",
      },
    })
      .then((res) => {
        if (!res.ok) throw new Error("Proxy error");
        return res.json();
      })
      .catch(() =>
        fetch(`${API_BASE}/api/agent/spy-outcome${q}`, {
          cache: "no-store",
          headers: {
            "Cache-Control": "no-cache, no-store, must-revalidate",
            "Pragma": "no-cache",
          },
        }).then((r) => r.json())
      )
      .then((data) => {
        if (data && !data.error) {
          setSpyOutcome(data);
          const now = Date.now();
          if (data.is_premarket_waiting) {
            target805Ref.current = data.target_time_805_iso
              ? new Date(data.target_time_805_iso).getTime()
              : typeof data.seconds_left_805 === "number"
              ? now + data.seconds_left_805 * 1000
              : null;
            target835Ref.current = data.target_time_835_iso
              ? new Date(data.target_time_835_iso).getTime()
              : typeof data.seconds_left_835 === "number"
              ? now + data.seconds_left_835 * 1000
              : null;

            if (target805Ref.current) {
              setSeconds805(Math.max(0, Math.ceil((target805Ref.current - now) / 1000)));
            }
            if (target835Ref.current) {
              setSeconds835(Math.max(0, Math.ceil((target835Ref.current - now) / 1000)));
            }
          } else {
            target805Ref.current = null;
            target835Ref.current = null;
            setSeconds805(null);
            setSeconds835(null);
          }
        }
      })
      .catch(() => {})
      .finally(() => {
        isFetchingRef.current = false;
        if (showLoading) setSpyLoading(false);
      });
  }, [isV4Ticker, currentActiveTicker, pulseMode, pulseDate]);

  // Initial fetch on ticker/mode/date switch
  useEffect(() => {
    fetchSpyOutcome(true);
  }, [fetchSpyOutcome]);

  // Wall-clock synced 1-second countdown timer for pre-market (immune to tab throttling/sleep)
  useEffect(() => {
    if (!spyOutcome?.is_premarket_waiting) {
      setSeconds805(null);
      setSeconds835(null);
      return;
    }

    const timer = setInterval(() => {
      const now = Date.now();
      if (target805Ref.current) {
        setSeconds805(Math.max(0, Math.ceil((target805Ref.current - now) / 1000)));
      }
      if (target835Ref.current) {
        setSeconds835(Math.max(0, Math.ceil((target835Ref.current - now) / 1000)));
      }
    }, 1000);

    return () => clearInterval(timer);
  }, [spyOutcome?.is_premarket_waiting]);

  // Master Autonomous Auto-Refresh Coordinator:
  // 1. Exactly at 8:05 AM CST: polls every 3s until 4H structure alert confirms
  // 2. Exactly at 8:35 AM CST: polls every 3s until 8:35 opening candle trade recommendation confirms
  // 3. Pre-market heartbeat: polls every 15s to update live pre-market price, change, and range
  // 4. Market hours heartbeat: polls every 30s to update live price, MFE, and trade status (T1/Stop)
  useEffect(() => {
    if (!isV4Ticker || pulseDate) return;

    const coordinatorInterval = setInterval(() => {
      const now = Date.now();

      // Trigger 1: 8:05 AM CST 4H Structure Alert milestone
      if (spyOutcome?.is_waiting_4h && target805Ref.current && now >= target805Ref.current) {
        fetchSpyOutcome(false);
        return;
      }

      // Trigger 2: 8:35 AM CST Actionable Recommendation milestone
      if (spyOutcome?.is_premarket_waiting && target835Ref.current && now >= target835Ref.current) {
        fetchSpyOutcome(false);
        return;
      }

      // Trigger 3: Pre-market periodic heartbeat (every 15s)
      if (spyOutcome?.is_premarket_waiting) {
        if (now - lastFetchTimeRef.current >= 15000) {
          fetchSpyOutcome(false);
        }
        return;
      }

      // Trigger 4: Active trading session periodic heartbeat (every 30s)
      if (spyOutcome && !spyOutcome.is_weekend_or_closed) {
        if (now - lastFetchTimeRef.current >= 30000) {
          fetchSpyOutcome(false);
        }
      }
    }, 1000);

    return () => clearInterval(coordinatorInterval);
  }, [isV4Ticker, pulseDate, spyOutcome, fetchSpyOutcome]);

  const formatTimerCountdown = (sec: number | null | undefined, fallback: string = "00h 00m 00s"): string => {
    if (sec === null || sec === undefined) return fallback;
    const s = Math.max(0, Math.floor(sec));
    const h = Math.floor(s / 3600);
    const m = Math.floor((s % 3600) / 60);
    const remS = s % 60;
    return `${String(h).padStart(2, "0")}h ${String(m).padStart(2, "0")}m ${String(remS).padStart(2, "0")}s`;
  };

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
          title: "🏛️ UNH SEC Pre-Earnings Audit",
          prompt: `Role: You are a senior equity research analyst and institutional risk manager specializing in Healthcare sector.

Task: Analyze the pre-earnings context for UNH to identify potential deviations from consensus expectations (surprises), guidance adjustments, or hidden risks.

Single Trusted Source Mandate:
- Exclusively evaluate the provided single trusted source: Official SEC EDGAR Regulatory Filings (Forms 10-Q & 8-K Exhibits) & Standardized Financial Statements.
- Do NOT pull from or search the open web, blogs, or unverified rumors.
- Do NOT hallucinate metrics not reported in official SEC filings; if an item is not reported, explicitly state 'Not reported in filing'.

Execute your analysis step-by-step using this structured framework:

1. THE CONSENSUS GAP BREAKDOWN:
- Compare the current consensus numbers against the run-rate implied by the prior quarter's KPIs and management commentary from the official SEC 8-K filing.
- Identify if the consensus baseline requires an aggressive acceleration or deceleration to be met.

2. NEGATIVE SPACE & MANAGEMENT DRIFT ANALYSIS:
- Review the prior earnings release & SEC 8-K disclosures. Identify areas where management was defensive, gave vague answers, or deferred metric updates to this upcoming quarter.
- Flag any changes in vocabulary regarding demand, pricing power, or macro headwinds.

3. WORKING CAPITAL & VOLATILITY BANDS:
- Audit the latest 10-Q working capital changes. Is inventory accumulation outpacing revenue growth (potential margin compression)? Is free cash flow lagging net income?
- Calculate a variance risk rating (Low/Medium/High) indicating the likelihood of an earnings or revenue miss/beat based on these structural drivers.

Output Format: Present your findings in a concise tabular overview outlining:
- Factor Analyzed
- Consensus Expectation
- Discrepancy/Signal Found
- Implied Earnings Outcome (Beat / In-Line / Miss / Guidance Cut)

Follow the table with a 4-sentence high-conviction narrative summarizing the primary directional thesis.`,
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
          title: `🏛️ ${currentActiveTicker} SEC Pre-Earnings Audit`,
          prompt: `Role: You are a senior equity research analyst and institutional risk manager specializing in Healthcare & Growth sectors.

Task: Analyze the pre-earnings context for ${currentActiveTicker} to identify potential deviations from consensus expectations (surprises), guidance adjustments, or hidden risks.

Single Trusted Source Mandate:
- Exclusively evaluate the provided single trusted source: Official SEC EDGAR Regulatory Filings (Forms 10-Q & 8-K Exhibits) & Standardized Financial Statements.
- Do NOT pull from or search the open web, blogs, or unverified rumors.
- Do NOT hallucinate metrics not reported in official SEC filings; if an item is not reported, explicitly state 'Not reported in filing'.

Execute your analysis step-by-step using this structured framework:

1. THE CONSENSUS GAP BREAKDOWN:
- Compare the current consensus numbers against the run-rate implied by the prior quarter's KPIs and management commentary from the official SEC 8-K filing.
- Identify if the consensus baseline requires an aggressive acceleration or deceleration to be met.

2. NEGATIVE SPACE & MANAGEMENT DRIFT ANALYSIS:
- Review the prior earnings release & SEC 8-K disclosures. Identify areas where management was defensive, gave vague answers, or deferred metric updates to this upcoming quarter.
- Flag any changes in vocabulary regarding demand, pricing power, or macro headwinds.

3. WORKING CAPITAL & VOLATILITY BANDS:
- Audit the latest 10-Q working capital changes. Is inventory accumulation outpacing revenue growth (potential margin compression)? Is free cash flow lagging net income?
- Calculate a variance risk rating (Low/Medium/High) indicating the likelihood of an earnings or revenue miss/beat based on these structural drivers.

Output Format: Present your findings in a concise tabular overview outlining:
- Factor Analyzed
- Consensus Expectation
- Discrepancy/Signal Found
- Implied Earnings Outcome (Beat / In-Line / Miss / Guidance Cut)

Follow the table with a 4-sentence high-conviction narrative summarizing the primary directional thesis.`,
        },
        {
          title: `📅 ${currentActiveTicker} Long Term Pulse`,
          prompt: `What is ${currentActiveTicker} Long Term Pulse${pulseDate ? ` on ${pulseDate}` : " today"}? Check Month (PMH/PML) and Day (PDH/PDL) Highs & Lows, multi-week trade plan, and forward backtest details.`,
        },
        {
          title: `⚡ ${currentActiveTicker} Earning Pulse (Last 4Q Backtest)`,
          prompt: `What is ${currentActiveTicker} Earning Pulse? Include the complete Last 4 Quarters Earnings Backtest Scorecard (Win Rate %, Net Return R, Realized P&L $/sh, and the 4-quarter breakdown table).`,
        },
        {
          title: `⏪ Backtest ${currentActiveTicker} (${pulseDate || "10/12/2026"})`,
          prompt: `Backtest ${currentActiveTicker} pulse on ${pulseDate || "10/12/2026"}: provide the exact levels as of ${pulseDate || "10/12/2026"}, the validated Pulse, Long Term Pulse, and Earning Pulse setups, and full backtest verification.`,
        },
        {
          title: `📊 ${currentActiveTicker} Volume Profile Backtest`,
          prompt: `Backtest ${currentActiveTicker} Volume Profile setups: show the systematic Win Rate %, Net Return R-Multiple, Profit Factor, VAL Bounce / VAH Rejection / Breakout breakdown, and the recent trade log table.`,
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
      group: "🏛️ SEC Pre & Post-Earnings Audit",
      items: [
        {
          label: "🏛️ SEC Pre-Earnings Audit (<ticker>)",
          badge: "Pre-Print Risk",
          template: getSecPreEarningsPrompt("<ticker>"),
        },
        {
          label: "📋 SEC Post-Earnings Catalyst (<ticker>)",
          badge: "Post-Print 8-K",
          template: getSecPostEarningsPrompt("<ticker>"),
        },
      ],
    },
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
          label: "⏪ Backtest Earning Pulse (Last 4Q)",
          badge: "Last 4 Quarters",
          template: `Backtest <ticker> last 4 quarter earnings backtest: show the complete 4-quarter scorecard, win rate %, net R-multiple, realized P&L $/sh, and quarter-by-quarter breakdown table.`,
        },
        {
          label: "⏪ Backtest Volume Profile (<ticker>)",
          badge: "VAL/POC/VAH",
          template: `Backtest <ticker> Volume Profile setups: show the systematic Win Rate %, Net Return R-Multiple, Profit Factor, VAL Bounce / VAH Rejection / Breakout breakdown, and the recent trade log table.`,
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
          label: "🎯 AlphaSpread Intrinsic Value",
          badge: "Bear/Base/Bull",
          template:
            "What is <ticker> intrinsic value from AlphaSpread under the Bear Case, Base Case, and Bull Case? Show DCF price targets, upside/overvaluation %, and key assumptions (revenue growth, margins, discount rate, terminal growth).",
        },
        {
          label: "💼 Insider & Congress Trades",
          badge: "Form 4 & STOCK Act",
          template:
            "Analyze <ticker> insider trading activity (Finviz SEC Form 4) and Congressional stock purchases/sales (STOCK Act disclosures): net 90-day buy/sell sentiment, C-suite transactions, and House/Senate members trading the stock.",
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
    <div className="w-full max-w-none space-y-3">
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
        {/* Pulse Strategy Suite Banner: Compact 1-Line Toolbar by Default (Expandable via Show Card button) */}
        {isV4Ticker && (
          <div className="bg-[#0d111d] border-b border-[#1f273d] px-3 sm:px-5 py-2 transition-all">
            <div className="flex flex-wrap items-center justify-between gap-2">
              <div className="flex flex-wrap items-center gap-1.5">
                <button
                  onClick={() => setPulseMode("pulse")}
                  className={`px-2 py-1 text-[11px] font-mono font-semibold rounded-md border transition-all ${
                    pulseMode === "pulse"
                      ? "bg-emerald-500/20 border-emerald-500/50 text-emerald-300"
                      : "bg-[#121626] border-slate-800 text-slate-400 hover:text-white"
                  }`}
                >
                  🎯 Pulse
                </button>
                <button
                  onClick={() => setPulseMode("long_term")}
                  className={`px-2 py-1 text-[11px] font-mono font-semibold rounded-md border transition-all ${
                    pulseMode === "long_term"
                      ? "bg-cyan-500/20 border-cyan-500/50 text-cyan-300"
                      : "bg-[#121626] border-slate-800 text-slate-400 hover:text-white"
                  }`}
                >
                  📅 Long Term
                </button>
                <button
                  onClick={() => setPulseMode("earning")}
                  className={`px-2 py-1 text-[11px] font-mono font-semibold rounded-md border transition-all ${
                    pulseMode === "earning"
                      ? "bg-purple-500/20 border-purple-500/50 text-purple-300"
                      : "bg-[#121626] border-slate-800 text-slate-400 hover:text-white"
                  }`}
                >
                  ⚡ Earning
                </button>
                <button
                  type="button"
                  onClick={() => handleSend(getSecPreEarningsPrompt(currentActiveTicker))}
                  disabled={isStreaming}
                  className="px-2.5 py-1 text-[11px] font-mono font-semibold rounded-md border transition-all bg-amber-500/15 border-amber-500/40 text-amber-300 hover:bg-amber-500/30 flex items-center gap-1 shadow-sm"
                  title="Run Single-Source Official SEC EDGAR Pre-Earnings Audit"
                >
                  <span>🏛️</span>
                  <span>SEC Pre-Earnings</span>
                </button>
                <button
                  type="button"
                  onClick={() => handleSend(getSecPostEarningsPrompt(currentActiveTicker))}
                  disabled={isStreaming}
                  className="px-2.5 py-1 text-[11px] font-mono font-semibold rounded-md border transition-all bg-purple-500/15 border-purple-500/40 text-purple-300 hover:bg-purple-500/30 flex items-center gap-1 shadow-sm"
                  title="Run Single-Source Official SEC EDGAR Post-Earnings Catalyst & Price Action Audit"
                >
                  <span>📋</span>
                  <span>SEC Post-Earnings</span>
                </button>

                {/* Compact 1-Line Summary Pill */}
                {spyLoading && !spyOutcome ? (
                  <span className="text-[11px] font-mono text-slate-400 flex items-center gap-1.5 ml-1">
                    <span className="w-1.5 h-1.5 rounded-full bg-emerald-400 animate-ping"></span>
                    Loading {currentActiveTicker}...
                  </span>
                ) : spyOutcome ? (
                  <div className="flex flex-wrap items-center gap-1.5 ml-1 text-[11px] font-mono">
                    <span className="text-white font-bold flex items-center gap-1">
                      {currentActiveTicker} ${spyOutcome.price?.toFixed(2)}
                      {spyOutcome.is_premarket_waiting && (
                        <span className="px-1 py-0.2 rounded text-[9px] font-bold uppercase tracking-wider bg-purple-500/20 text-purple-300 border border-purple-500/40" title="Live Pre-Market Price">
                          Pre-Market
                        </span>
                      )}
                    </span>
                    {spyOutcome.is_premarket_waiting && spyOutcome.premarket_change !== undefined && spyOutcome.premarket_change !== null && (
                      <span className={`text-[10px] font-semibold ${spyOutcome.premarket_change >= 0 ? "text-emerald-400" : "text-red-400"}`}>
                        {spyOutcome.premarket_change >= 0 ? "+" : ""}{spyOutcome.premarket_change.toFixed(2)} ({spyOutcome.premarket_change_pct >= 0 ? "+" : ""}{spyOutcome.premarket_change_pct.toFixed(2)}%)
                      </span>
                    )}
                    {spyOutcome.is_premarket_waiting && spyOutcome.latest_session_close && (
                      <span className="text-[10px] text-slate-400 hidden lg:inline">
                        (Prior Close: ${spyOutcome.latest_session_close.toFixed(2)})
                      </span>
                    )}
                    {spyOutcome.is_premarket_waiting ? (
                      <>
                        <span className="px-1.5 py-0.5 rounded font-semibold border text-[10px] bg-amber-500/15 border-amber-500/40 text-amber-300 flex items-center gap-1 shadow-sm">
                          <span className={seconds835 !== null && seconds835 <= 0 ? "animate-spin" : "animate-pulse"}>
                            {seconds835 !== null && seconds835 <= 0 ? "🔄" : "⏱️"}
                          </span>
                          <span>
                            {seconds835 !== null && seconds835 <= 0
                              ? "CONFIRMING 8:35 AM OPEN..."
                              : `WAITING (⏱️ Rec in ${formatTimerCountdown(seconds835, spyOutcome.timer_left_835)})`}
                          </span>
                        </span>
                        <span className="px-1.5 py-0.5 rounded font-semibold border text-[10px] bg-cyan-500/15 border-cyan-500/40 text-cyan-300 hidden md:inline-flex items-center gap-1 shadow-sm">
                          <span>⏱️</span>
                          <span>
                            {seconds805 !== null && seconds805 > 0
                              ? `4H (⏱️ 8:05 CST: ${formatTimerCountdown(seconds805, spyOutcome.timer_left_805)})`
                              : spyOutcome.four_hour_badge || spyOutcome.structure_tag || "4H Confirmed"}
                          </span>
                        </span>
                        {spyOutcome.four_hour_focus?.short_badge && (
                          <span
                            className={`px-1.5 py-0.5 rounded font-semibold border text-[10px] hidden lg:inline-flex items-center gap-1 shadow-sm ${
                              spyOutcome.four_hour_focus.side === "LONG"
                                ? "bg-emerald-500/15 border-emerald-500/40 text-emerald-300"
                                : "bg-red-500/15 border-red-500/40 text-red-300"
                            }`}
                            title={spyOutcome.four_hour_focus.summary}
                          >
                            <span>🎯</span>
                            <span>{spyOutcome.four_hour_focus.short_badge}</span>
                          </span>
                        )}
                        <span className="px-1.5 py-0.5 rounded text-[10px] font-bold bg-amber-500/15 text-amber-300 border border-amber-500/30 flex items-center gap-1">
                          <span className={seconds835 !== null && seconds835 <= 0 ? "animate-spin" : ""}>
                            {seconds835 !== null && seconds835 <= 0 ? "🔄" : "⏱️"}
                          </span>
                          <span>
                            {seconds835 !== null && seconds835 <= 0 ? "CONFIRMING OPEN" : "PENDING 8:35 AM"}
                          </span>
                        </span>
                        <button
                          type="button"
                          onClick={() => fetchSpyOutcome(true)}
                          disabled={spyLoading}
                          className="px-1.5 py-0.5 rounded text-[10px] font-mono bg-slate-800 text-slate-300 hover:text-white border border-slate-700 hover:border-slate-600 flex items-center gap-1 shadow-sm cursor-pointer"
                          title="Click to refresh live recommendation"
                        >
                          <span className={spyLoading ? "animate-spin" : ""}>🔄</span>
                          <span>Refresh</span>
                        </button>
                      </>
                    ) : (
                      <>
                        <span
                          className={`px-1.5 py-0.5 rounded font-semibold border text-[10px] ${
                            spyOutcome.trade_idea?.action?.includes("CALLS")
                              ? "bg-emerald-500/15 border-emerald-500/40 text-emerald-300"
                              : "bg-red-500/15 border-red-500/40 text-red-300"
                          }`}
                        >
                          {spyOutcome.trade_idea?.action} (
                          {spyOutcome.trade_idea?.suggested_contract || "ATM"})
                        </span>
                        {spyOutcome.four_hour_focus?.short_badge && (
                          <span
                            className={`px-1.5 py-0.5 rounded font-semibold border text-[10px] hidden md:inline-flex items-center gap-1 shadow-sm ${
                              spyOutcome.four_hour_focus.side === "LONG"
                                ? "bg-emerald-500/15 border-emerald-500/40 text-emerald-300"
                                : "bg-red-500/15 border-red-500/40 text-red-300"
                            }`}
                            title={spyOutcome.four_hour_focus.summary}
                          >
                            <span>🎯</span>
                            <span>{spyOutcome.four_hour_focus.short_badge}</span>
                          </span>
                        )}
                        {spyOutcome.four_hour_confluence?.badge && (
                          <span
                            className={`px-1.5 py-0.5 rounded font-semibold border text-[10px] hidden lg:inline-flex items-center gap-1 shadow-sm ${
                              spyOutcome.four_hour_confluence.is_aligned || spyOutcome.four_hour_confluence.confluent
                                ? "bg-emerald-500/15 border-emerald-500/40 text-emerald-300"
                                : "bg-amber-500/15 border-amber-500/40 text-amber-300"
                            }`}
                            title={spyOutcome.four_hour_confluence.note || spyOutcome.four_hour_confluence.detail}
                          >
                            <span>{spyOutcome.four_hour_confluence.badge}</span>
                          </span>
                        )}
                        <span className="text-slate-300 hidden xl:inline">
                          Entry <b className="text-emerald-400">${spyOutcome.trade_idea?.entry_level?.toFixed(2)}</b> → T1{" "}
                          <b className="text-cyan-400">${spyOutcome.trade_idea?.target_1?.toFixed(2)}</b>
                        </span>
                        {pulseMode === "earning" && spyOutcome.earnings_backtest_last_4_quarters?.win_rate_badge ? (
                          <span
                            className={`px-1.5 py-0.5 rounded text-[10px] font-bold ${
                              spyOutcome.earnings_backtest_last_4_quarters.wins >= 2
                                ? "bg-emerald-500/15 text-emerald-300 border border-emerald-500/30"
                                : "bg-red-500/15 text-red-300 border border-red-500/30"
                            }`}
                          >
                            4Q Backtest: {spyOutcome.earnings_backtest_last_4_quarters.win_rate_badge} · {spyOutcome.earnings_backtest_last_4_quarters.net_r_multiple}
                          </span>
                        ) : spyOutcome.backtest?.result_badge ? (
                          <span
                            className={`px-1.5 py-0.5 rounded text-[10px] font-bold ${
                              spyOutcome.backtest.result_badge.includes("WIN")
                                ? "bg-emerald-500/15 text-emerald-300 border border-emerald-500/30"
                                : spyOutcome.backtest.result_badge.includes("STOPPED")
                                ? "bg-red-500/15 text-red-300 border border-red-500/30"
                                : "bg-amber-500/15 text-amber-300 border border-amber-500/30"
                            }`}
                          >
                            {spyOutcome.backtest.result_badge}
                          </span>
                        ) : null}
                        <button
                          type="button"
                          onClick={() => fetchSpyOutcome(true)}
                          disabled={spyLoading}
                          className="px-1.5 py-0.5 rounded text-[10px] font-mono bg-slate-800 text-slate-300 hover:text-white border border-slate-700 hover:border-slate-600 flex items-center gap-1 shadow-sm cursor-pointer ml-1"
                          title="Refresh live price & trade execution status"
                        >
                          <span className={spyLoading ? "animate-spin" : ""}>🔄</span>
                          <span>Refresh</span>
                        </button>
                      </>
                    )}
                  </div>
                ) : null}
              </div>

              <div className="flex items-center gap-2">
                <span className="text-[11px] font-mono text-slate-400 hidden sm:inline">⏪ Date:</span>
                <input
                  type="date"
                  value={pulseDate}
                  max={new Date().toISOString().split("T")[0]}
                  onChange={(e) => setPulseDate(e.target.value)}
                  className="bg-[#121626] border border-slate-700 text-white font-mono text-[11px] px-2 py-0.5 rounded-md focus:outline-none focus:border-[#4d9fff]"
                />
                {pulseDate && (
                  <button
                    onClick={() => setPulseDate("")}
                    className="text-[10px] font-mono px-1.5 py-0.5 rounded bg-slate-800 text-slate-300 hover:text-white"
                  >
                    Today
                  </button>
                )}
                <a
                  href={`/chart?ticker=${currentActiveTicker}&tf=5m`}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="px-2.5 py-1 rounded-md text-[11px] font-mono font-semibold bg-cyan-500/15 border border-cyan-500/40 text-cyan-300 hover:bg-cyan-500/25 transition-all flex items-center gap-1.5"
                  title="Open Interactive Live 5m Chart (PDH/PDL/SL pre-drawn + 0DTE Contract Overlap)"
                >
                  <span>📈</span>
                  <span>Live 5m Chart ↗</span>
                </a>
                <button
                  type="button"
                  onClick={() => setShowPulseCard(!showPulseCard)}
                  className={`px-2.5 py-1 rounded-md text-[11px] font-mono font-semibold border transition-all ${
                    showPulseCard
                      ? "bg-[#4d9fff]/20 border-[#4d9fff]/50 text-[#4d9fff]"
                      : "bg-[#121626] border-slate-700 text-slate-300 hover:text-white"
                  }`}
                >
                  {showPulseCard ? "▴ Hide Card" : "📊 Show Card ▾"}
                </button>
              </div>
            </div>

            {showPulseCard && spyOutcome ? (
              <div className="space-y-2.5 mt-2.5 pt-2.5 border-t border-[#1a2035]">
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
                      {spyOutcome.session_day_name
                        ? `· ${spyOutcome.session_day_name}`
                        : spyOutcome.scan_date
                        ? `(${spyOutcome.scan_date})`
                        : ""}
                    </span>
                    <span
                      className={`text-[11px] font-mono px-2 py-0.5 rounded-full font-semibold border ${
                        spyOutcome.is_premarket_waiting
                          ? "bg-amber-500/15 border-amber-500/40 text-amber-400"
                          : spyOutcome.valid_outcome?.includes("Outcome 1") || spyOutcome.trade_idea?.action?.includes("CALLS")
                          ? "bg-emerald-500/15 border-emerald-500/40 text-emerald-400"
                          : spyOutcome.valid_outcome?.includes("Outcome 2") || spyOutcome.trade_idea?.action?.includes("PUTS")
                          ? "bg-red-500/15 border-red-500/40 text-red-400"
                          : "bg-amber-500/15 border-amber-500/40 text-amber-400"
                      }`}
                    >
                      {spyOutcome.is_premarket_waiting
                        ? `WAITING · (⏱️ Timer Left for 8:35 AM: ${formatTimerCountdown(seconds835, spyOutcome.timer_left_835)})`
                        : spyOutcome.valid_outcome}
                    </span>
                    {(spyOutcome.structure_tag || spyOutcome.is_premarket_waiting) && (
                      <span className="text-[11px] font-mono px-2 py-0.5 rounded-full font-bold bg-cyan-500/15 border border-cyan-500/40 text-cyan-300 flex items-center gap-1">
                        {seconds805 !== null && seconds805 > 0 ? (
                          <>
                            <span className="animate-pulse">⏱️</span>
                            <span>4H (⏱️ Timer left for 8:05 AM CST: {formatTimerCountdown(seconds805, spyOutcome.timer_left_805)})</span>
                          </>
                        ) : (
                          <span>{spyOutcome.four_hour_badge || spyOutcome.structure_tag}</span>
                        )}
                      </span>
                    )}
                    {spyOutcome.four_hour_focus?.short_badge && (
                      <span
                        className={`text-[11px] font-mono px-2 py-0.5 rounded-full font-bold flex items-center gap-1 ${
                          spyOutcome.four_hour_focus.side === "LONG"
                            ? "bg-emerald-500/15 border border-emerald-500/40 text-emerald-300"
                            : "bg-red-500/15 border border-red-500/40 text-red-300"
                        }`}
                        title={spyOutcome.four_hour_focus.summary}
                      >
                        <span>🎯 Focus:</span>
                        <span>{spyOutcome.four_hour_focus.short_badge}</span>
                      </span>
                    )}
                    {spyOutcome.four_hour_confluence?.badge && !spyOutcome.is_premarket_waiting && (
                      <span
                        className={`text-[11px] font-mono px-2 py-0.5 rounded-full font-bold flex items-center gap-1 ${
                          spyOutcome.four_hour_confluence.is_aligned || spyOutcome.four_hour_confluence.confluent
                            ? "bg-emerald-500/15 border border-emerald-500/40 text-emerald-300"
                            : "bg-amber-500/15 border border-amber-500/40 text-amber-300"
                        }`}
                        title={spyOutcome.four_hour_confluence.note || spyOutcome.four_hour_confluence.detail}
                      >
                        <span>{spyOutcome.four_hour_confluence.badge}</span>
                      </span>
                    )}
                  </div>
                  <div className="text-[11px] font-mono text-slate-400 flex flex-wrap items-center gap-2">
                    <span>
                      {currentActiveTicker}: <b className="text-white">${spyOutcome.price?.toFixed(2)}</b>
                      {spyOutcome.is_premarket_waiting && (
                        <span className="ml-1 text-[10px] text-purple-300 font-semibold">(Live Pre-Market)</span>
                      )}
                    </span>
                    {pulseMode === "earning" ? (
                      <>
                        {spyOutcome.key_levels?.weekly_structure && (
                          <>
                            <span>·</span>
                            <span>Structure: <b className="text-cyan-300">{spyOutcome.key_levels.weekly_structure}</b></span>
                          </>
                        )}
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
                        {spyOutcome.key_levels?.daily_structure && (
                          <>
                            <span>·</span>
                            <span>Structure: <b className="text-cyan-300">{spyOutcome.key_levels.daily_structure}</b></span>
                          </>
                        )}
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
                        {spyOutcome.key_levels?.daily_structure && (
                          <>
                            <span>·</span>
                            <span>Structure: <b className="text-cyan-300">{spyOutcome.key_levels.daily_structure}</b></span>
                          </>
                        )}
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

                {spyOutcome.is_premarket_waiting && (
                  <div className="bg-gradient-to-r from-amber-500/10 via-amber-500/5 to-transparent border border-amber-500/30 rounded-lg p-3 text-xs font-mono space-y-2">
                    <div className="flex flex-wrap items-center justify-between gap-2 border-b border-amber-500/20 pb-2">
                      <div className="flex items-center gap-2 text-amber-300 font-bold">
                        <span className="text-base animate-pulse">⏱️</span>
                        <span>PRE-MARKET WAITING STATE · {spyOutcome.session_day_name || "TODAY"}</span>
                        <span className="px-1.5 py-0.5 rounded text-[10px] font-bold uppercase bg-purple-500/20 text-purple-300 border border-purple-500/40">
                          Live Pre-Market
                        </span>
                      </div>
                      <div className="flex flex-wrap items-center gap-3 text-slate-300 text-[11px]">
                        <span>Live PM: <b className="text-white">${spyOutcome.price?.toFixed(2)}</b></span>
                        {spyOutcome.premarket_change !== undefined && spyOutcome.premarket_change !== null && (
                          <span className={spyOutcome.premarket_change >= 0 ? "text-emerald-400 font-bold" : "text-red-400 font-bold"}>
                            {spyOutcome.premarket_change >= 0 ? "+" : ""}{spyOutcome.premarket_change.toFixed(2)} ({spyOutcome.premarket_change_pct >= 0 ? "+" : ""}{spyOutcome.premarket_change_pct.toFixed(2)}%)
                          </span>
                        )}
                        <span>·</span>
                        <span>Prior Close: <b className="text-white">${spyOutcome.latest_session_close?.toFixed(2)}</b></span>
                        <span>·</span>
                        <span>PM Range: <b className="text-cyan-300">${spyOutcome.session_low?.toFixed(2)} - ${spyOutcome.session_high?.toFixed(2)}</b></span>
                        <span>·</span>
                        <span>ATR: <b className="text-cyan-300">${spyOutcome.key_levels?.atr?.toFixed(2) || "—"}</b></span>
                      </div>
                    </div>
                    <div className="grid grid-cols-1 md:grid-cols-2 gap-2 text-[11px]">
                      <div className="bg-[#121626]/80 border border-slate-700/60 rounded p-2 flex items-center justify-between">
                        <span className="text-slate-300">4H Structure Alert (8:05 AM CST):</span>
                        <span className={seconds805 !== null && seconds805 > 0 ? "text-amber-400 font-bold" : "text-emerald-400 font-bold"}>
                          {seconds805 !== null && seconds805 > 0
                            ? `4H (⏱️ Timer left for 8:05 AM CST: ${formatTimerCountdown(seconds805, spyOutcome.timer_left_805)})`
                            : `✅ Alert Confirmed (${spyOutcome.four_hour_badge || spyOutcome.structure_tag})`}
                        </span>
                      </div>
                      <div className="bg-[#121626]/80 border border-slate-700/60 rounded p-2 flex items-center justify-between">
                        <span className="text-slate-300">Action & Recommendation (8:35 AM CST):</span>
                        <span className="text-amber-400 font-bold">
                          {seconds835 !== null && seconds835 <= 0 ? (
                            <span className="flex items-center gap-1.5 text-cyan-300">
                              <span className="animate-spin">🔄</span>
                              <span>Confirming 8:35 AM Candle...</span>
                              <button
                                type="button"
                                onClick={() => fetchSpyOutcome(true)}
                                className="px-1.5 py-0.5 rounded text-[9px] bg-cyan-500/20 text-cyan-200 border border-cyan-500/40 hover:bg-cyan-500/30 cursor-pointer"
                              >
                                Refresh Now
                              </button>
                            </span>
                          ) : (
                            `WAITING (⏱️ Timer Left for 8:35 AM: ${formatTimerCountdown(seconds835, spyOutcome.timer_left_835)})`
                          )}
                        </span>
                      </div>
                    </div>
                    {spyOutcome.four_hour_focus && (
                      <div className="bg-[#121626]/90 border border-cyan-500/30 rounded p-2 text-[11px] flex flex-col sm:flex-row sm:items-center justify-between gap-2">
                        <div className="flex items-center gap-2">
                          <span className="text-slate-300 font-medium">🎯 4H Narrowed Focus:</span>
                          <span className={`font-bold px-2 py-0.5 rounded text-[10px] ${
                            spyOutcome.four_hour_focus.side === "LONG"
                              ? "bg-emerald-500/20 text-emerald-300 border border-emerald-500/40"
                              : "bg-red-500/20 text-red-300 border border-red-500/40"
                          }`}>
                            {spyOutcome.four_hour_focus.badge_with_levels || spyOutcome.four_hour_focus.badge}
                          </span>
                        </div>
                        <span className="text-slate-400 text-[10px]">
                          {spyOutcome.four_hour_focus.rule}
                        </span>
                      </div>
                    )}
                    <p className="text-[10px] text-slate-400">
                      💡 <b>Playbook Setup:</b> {spyOutcome.four_hour_focus?.summary || "Prior session levels are mapped for today."} Actionable Call/Put contracts and execution trigger will confirm at 8:35 AM CST following the 8:30 AM market open.
                    </p>
                  </div>
                )}

                <div className="grid grid-cols-2 sm:grid-cols-4 gap-2 bg-[#121626] border border-[#1d243b] rounded-lg p-2.5 text-xs">
                  <div>
                    <span className="text-[10px] text-slate-400 block font-mono">
                      {spyOutcome.is_premarket_waiting ? "RECOMMENDATION" : "OPEN CONTRACT (ATM)"}
                    </span>
                    <span className="font-bold text-white font-mono text-[11px]">
                      {spyOutcome.is_premarket_waiting ? (
                        <span className="text-amber-300">
                          WAITING (⏱️ {formatTimerCountdown(seconds835, spyOutcome.timer_left_835)})
                        </span>
                      ) : (
                        spyOutcome.trade_idea?.open_contract || spyOutcome.trade_idea?.suggested_contract || `${currentActiveTicker} ATM`
                      )}
                    </span>
                  </div>
                  <div>
                    <span className="text-[10px] text-slate-400 block font-mono">ENTRY TRIGGER</span>
                    <span className="font-semibold text-emerald-400 font-mono">
                      {spyOutcome.is_premarket_waiting
                        ? (spyOutcome.four_hour_focus?.trigger_level ? `${spyOutcome.four_hour_focus.focus_type} at $${spyOutcome.four_hour_focus.trigger_level.toFixed(2)}` : "Awaiting 8:35 AM CST")
                        : `$${spyOutcome.trade_idea?.entry_level?.toFixed(2)}`}
                    </span>
                  </div>
                  <div>
                    <span className="text-[10px] text-slate-400 block font-mono">STOP LOSS</span>
                    <span className="font-semibold text-red-400 font-mono">
                      {spyOutcome.is_premarket_waiting
                        ? "Defined at 8:35 AM"
                        : `$${spyOutcome.trade_idea?.stop_loss?.toFixed(2)}`}
                    </span>
                  </div>
                  <div>
                    <span className="text-[10px] text-slate-400 block font-mono">
                      TARGETS (R:R {spyOutcome.trade_idea?.reward_risk || "—"})
                    </span>
                    <span className="font-semibold text-cyan-400 font-mono">
                      {spyOutcome.is_premarket_waiting
                        ? "Calculated at 8:35 AM"
                        : `$${spyOutcome.trade_idea?.target_1?.toFixed(2)} / $${spyOutcome.trade_idea?.target_2?.toFixed(2)}`}
                    </span>
                  </div>
                </div>

                {spyOutcome.four_hour_focus && (
                  <div className="bg-[#101626] border border-cyan-500/25 rounded-lg px-3 py-2 flex flex-col sm:flex-row sm:items-center justify-between gap-2 text-[11px] font-mono">
                    <div className="flex flex-wrap items-center gap-2">
                      <span className="text-cyan-300 font-bold uppercase">🎯 4H Narrowed Focus (from 8:05 AM):</span>
                      <span className={`px-2 py-0.5 rounded font-bold text-[10px] ${
                        spyOutcome.four_hour_focus.side === "LONG"
                          ? "bg-emerald-500/15 text-emerald-300 border border-emerald-500/30"
                          : "bg-red-500/15 text-red-300 border border-red-500/30"
                      }`}>
                        {spyOutcome.four_hour_focus.badge_with_levels || spyOutcome.four_hour_focus.badge}
                      </span>
                      {spyOutcome.four_hour_confluence?.badge && (
                        <span className={`px-2 py-0.5 rounded font-bold text-[10px] ${
                          spyOutcome.four_hour_confluence.is_aligned || spyOutcome.four_hour_confluence.confluent
                            ? "bg-emerald-500/15 text-emerald-300 border border-emerald-500/30"
                            : "bg-amber-500/15 text-amber-300 border border-amber-500/30"
                        }`} title={spyOutcome.four_hour_confluence.note}>
                          {spyOutcome.four_hour_confluence.badge}
                        </span>
                      )}
                    </div>
                    <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-2 border-t border-cyan-500/15 pt-1.5 mt-1">
                      {spyOutcome.four_hour_focus.rule && (
                        <span className="text-slate-400 text-[10px] italic">
                          {spyOutcome.four_hour_focus.rule}
                        </span>
                      )}
                      <a
                        href={`/chart?ticker=${currentActiveTicker}&tf=5m`}
                        target="_blank"
                        rel="noopener noreferrer"
                        className="text-cyan-400 hover:text-cyan-300 underline font-bold text-[10px] whitespace-nowrap ml-auto flex items-center gap-1"
                        title="Open Live 5m Chart with pre-drawn PDH, PDL, SL and 0DTE Contract Overlap"
                      >
                        <span>📈 Open Live 5m Chart (PDH/PDL/SL) ↗</span>
                      </a>
                    </div>
                  </div>
                )}

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

                {pulseMode === "earning" && spyOutcome.earnings_backtest_last_4_quarters ? (
                  <div className="bg-[#0e1424] border border-purple-500/30 rounded-lg p-3 space-y-2.5 text-[11px] font-mono">
                    <div className="flex flex-wrap items-center justify-between gap-2 border-b border-purple-500/20 pb-2">
                      <div className="flex flex-wrap items-center gap-2">
                        <span className="text-purple-300 font-bold uppercase flex items-center gap-1">
                          <span>📊</span>
                          <span>Last 4 Quarters Earnings Backtest Scorecard:</span>
                        </span>
                        <span
                          className={`px-2 py-0.5 rounded font-bold ${
                            spyOutcome.earnings_backtest_last_4_quarters.wins >= 2
                              ? "bg-emerald-500/20 text-emerald-300 border border-emerald-500/40"
                              : "bg-red-500/20 text-red-300 border border-red-500/40"
                          }`}
                        >
                          {spyOutcome.earnings_backtest_last_4_quarters.win_rate_badge}
                        </span>
                      </div>
                      <div className="flex items-center gap-3 text-slate-300">
                        <span>
                          Net Return:{" "}
                          <b
                            className={
                              spyOutcome.earnings_backtest_last_4_quarters.net_r_multiple?.startsWith("+")
                                ? "text-emerald-400"
                                : "text-red-400"
                            }
                          >
                            {spyOutcome.earnings_backtest_last_4_quarters.net_r_multiple}
                          </b>
                        </span>
                        <span>
                          Realized P&L:{" "}
                          <b
                            className={
                              (spyOutcome.earnings_backtest_last_4_quarters.net_pnl_per_share || 0) >= 0
                                ? "text-emerald-400"
                                : "text-red-400"
                            }
                          >
                            {(spyOutcome.earnings_backtest_last_4_quarters.net_pnl_per_share || 0) >= 0 ? "+$" : "-$"}
                            {Math.abs(spyOutcome.earnings_backtest_last_4_quarters.net_pnl_per_share || 0).toFixed(2)}/sh
                          </b>
                        </span>
                      </div>
                    </div>

                    {/* 4 Quarters Breakdown Table */}
                    {spyOutcome.earnings_backtest_last_4_quarters.quarters && (
                      <div className="overflow-x-auto">
                        <table className="w-full text-left border-collapse text-[11px]">
                          <thead>
                            <tr className="border-b border-[#1f2842] text-slate-400 uppercase text-[10px]">
                              <th className="py-1 px-1.5">Quarter (Date)</th>
                              <th className="py-1 px-1.5">EPS vs Est (Surprise)</th>
                              <th className="py-1 px-1.5">Weekly Structure</th>
                              <th className="py-1 px-1.5">Setup & Plan</th>
                              <th className="py-1 px-1.5">Result</th>
                              <th className="py-1 px-1.5">Realized P&L / R</th>
                              <th className="py-1 px-1.5">MFE / MAE</th>
                            </tr>
                          </thead>
                          <tbody className="divide-y divide-[#182138] text-slate-300">
                            {spyOutcome.earnings_backtest_last_4_quarters.quarters.map((q: any, qIdx: number) => (
                              <tr key={qIdx} className="hover:bg-[#131b30]">
                                <td className="py-1.5 px-1.5 font-semibold text-white whitespace-nowrap">
                                  {q.quarter_label}
                                </td>
                                <td className="py-1.5 px-1.5 whitespace-nowrap">
                                  {q.eps_actual != null ? (
                                    <span>
                                      ${q.eps_actual} vs ${q.eps_estimate}{" "}
                                      <span
                                        className={
                                          (q.surprise_pct || 0) >= 0 ? "text-emerald-400" : "text-red-400"
                                        }
                                      >
                                        ({(q.surprise_pct || 0) >= 0 ? "+" : ""}
                                        {q.surprise_pct}%)
                                      </span>
                                    </span>
                                  ) : (
                                    <span className="text-slate-500">—</span>
                                  )}
                                </td>
                                <td className="py-1.5 px-1.5 whitespace-nowrap">
                                  <span className="px-1.5 py-0.5 rounded bg-cyan-500/10 border border-cyan-500/30 text-cyan-300 text-[10px]">
                                    {q.structure_tag || "—"}
                                  </span>
                                </td>
                                <td className="py-1.5 px-1.5 whitespace-nowrap">
                                  <span
                                    className={`font-bold mr-1 ${
                                      q.action?.includes("CALLS") ? "text-emerald-400" : "text-red-400"
                                    }`}
                                  >
                                    {q.action}
                                  </span>
                                  <span className="text-slate-400 text-[10px]">
                                    ${q.entry} | S ${q.stop} | T1 ${q.t1}
                                  </span>
                                </td>
                                <td className="py-1.5 px-1.5 whitespace-nowrap">
                                  <span
                                    className={`px-1.5 py-0.5 rounded text-[10px] font-bold ${
                                      q.result_badge?.includes("WIN")
                                        ? "bg-emerald-500/15 text-emerald-300 border border-emerald-500/30"
                                        : q.result_badge?.includes("STOPPED")
                                        ? "bg-red-500/15 text-red-300 border border-red-500/30"
                                        : "bg-amber-500/15 text-amber-300 border border-amber-500/30"
                                    }`}
                                  >
                                    {q.result_badge}
                                  </span>
                                </td>
                                <td className="py-1.5 px-1.5 whitespace-nowrap font-semibold">
                                  <span className={(q.realized_pnl_per_share || 0) >= 0 ? "text-emerald-400" : "text-red-400"}>
                                    {q.realized_r_multiple} ({(q.realized_pnl_per_share || 0) >= 0 ? "+$" : "-$"}
                                    {Math.abs(q.realized_pnl_per_share || 0).toFixed(2)}/sh)
                                  </span>
                                </td>
                                <td className="py-1.5 px-1.5 whitespace-nowrap text-slate-400 text-[10px]">
                                  MFE <span className="text-emerald-400">{q.max_favorable_excursion}</span> / MAE{" "}
                                  <span className="text-red-400">{q.max_adverse_excursion}</span>
                                </td>
                              </tr>
                            ))}
                          </tbody>
                        </table>
                      </div>
                    )}
                  </div>
                ) : spyOutcome.backtest ? (
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
                      <span className="text-slate-300">
                        {spyOutcome.is_premarket_waiting
                          ? `Waiting for market open (8:30 AM CST) and 5m confirmation (8:35 AM CST). Timer left: ${formatTimerCountdown(seconds835, spyOutcome.timer_left_835)}`
                          : spyOutcome.backtest.summary}
                      </span>
                    </div>
                    <div className="text-slate-400">
                      MFE: <b className="text-emerald-400">{spyOutcome.backtest.max_favorable_excursion}</b> · MAE:{" "}
                      <b className="text-red-400">{spyOutcome.backtest.max_adverse_excursion}</b>
                    </div>
                  </div>
                ) : null}

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
                            ? `What is ${currentActiveTicker} Earning Pulse${pulseDate ? ` on ${pulseDate}` : ""}? Show the Quarterly (PQH/PQL) levels, trade plan, and complete Last 4 Quarters Earnings Backtest Scorecard with quarter-by-quarter breakdown.`
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
          {/* Always-Visible Quick Strategy Bar */}
          <div className="flex flex-wrap items-center gap-1.5 pb-1">
            <button
              type="button"
              disabled={isStreaming}
              onClick={() => handleSend(getSecPreEarningsPrompt(currentActiveTicker))}
              className="px-2.5 py-1 rounded-lg text-xs font-semibold bg-amber-500/15 border border-amber-500/40 text-amber-300 hover:bg-amber-500/30 flex items-center gap-1.5 transition-all shadow-sm"
              title="Run Official SEC EDGAR Pre-Earnings Audit (10-Q & 8-K Single Source)"
            >
              <span>🏛️</span>
              <span>{currentActiveTicker} Pre-Earnings</span>
            </button>
            <button
              type="button"
              disabled={isStreaming}
              onClick={() => handleSend(getSecPostEarningsPrompt(currentActiveTicker))}
              className="px-2.5 py-1 rounded-lg text-xs font-semibold bg-purple-500/15 border border-purple-500/40 text-purple-300 hover:bg-purple-500/30 flex items-center gap-1.5 transition-all shadow-sm"
              title="Run Official SEC EDGAR Post-Earnings Catalyst & Price Action Audit (8-K & 10-Q Single Source)"
            >
              <span>📋</span>
              <span>{currentActiveTicker} Post-Earnings</span>
            </button>
            <button
              type="button"
              disabled={isStreaming}
              onClick={() => handleSend(`What is ${currentActiveTicker} pulse today? Provide the 8:35 AM validated outcome, key levels (PDH/PDL/PWH/PWL), Calls vs Puts trade idea, and intraday backtest status.`)}
              className="px-2 py-1 rounded-lg text-xs font-semibold bg-emerald-500/10 border border-emerald-500/30 text-emerald-300 hover:bg-emerald-500/20 flex items-center gap-1 transition-all"
            >
              <span>🎯</span>
              <span>{currentActiveTicker} Pulse</span>
            </button>
            <button
              type="button"
              disabled={isStreaming}
              onClick={() => handleSend(`What is ${currentActiveTicker} Earning Pulse today? Check Quarterly (PQH/PQL) and Monthly (PMH/PML) Highs & Lows, options setup, and forward backtest details.`)}
              className="px-2 py-1 rounded-lg text-xs font-semibold bg-purple-500/10 border border-purple-500/30 text-purple-300 hover:bg-purple-500/20 flex items-center gap-1 transition-all"
            >
              <span>⚡</span>
              <span>Earning Pulse</span>
            </button>
            <button
              type="button"
              disabled={isStreaming}
              onClick={() => handleSend("Next week earnings analysis")}
              className="px-2 py-1 rounded-lg text-xs font-semibold bg-cyan-500/10 border border-cyan-500/30 text-cyan-300 hover:bg-cyan-500/20 flex items-center gap-1 transition-all"
            >
              <span>📊</span>
              <span>Weekly Earnings</span>
            </button>
          </div>
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
