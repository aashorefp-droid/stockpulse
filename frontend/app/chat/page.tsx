"use client";

import { useState, useRef, useEffect } from "react";

const API_BASE = process.env.NEXT_PUBLIC_API_URL || "https://stockpulse-pkpj.onrender.com";

interface Message {
  role: "user" | "assistant";
  content: string;
  toolStatus?: string;
}

const POPULAR_TICKERS = ["GENERAL", "AAOI", "SPY", "QQQ", "NVDA", "TSLA", "MDT", "XLF"];

export default function ChatPage() {
  const [ticker, setTicker] = useState("GENERAL");
  const [customTicker, setCustomTicker] = useState("");
  const [entryPrice, setEntryPrice] = useState("");
  const [messages, setMessages] = useState<Message[]>([
    {
      role: "assistant",
      content:
        "Hello! I am **StockPulse AI**, your institutional trading and market mentor powered by multi-provider AI.\n\nAsk me any general market or technical question, scan the **Default 50** core universe, or select any stock ticker (e.g. AAOI, NVDA, SPY) to evaluate live Volume Profile, 52W Fib levels, fundamentals, and systematic trade plans!",
    },
  ]);
  const [input, setInput] = useState("");
  const [selectedModel, setSelectedModel] = useState<string>("auto");
  const [isStreaming, setIsStreaming] = useState(false);
  const [activeTool, setActiveTool] = useState<string | null>(null);
  const [apiKey, setApiKey] = useState<string>("");
  const [showKeyModal, setShowKeyModal] = useState<boolean>(false);
  const [keyInput, setKeyInput] = useState<string>("");
  const messagesEndRef = useRef<HTMLDivElement>(null);

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

      const headers: Record<string, string> = {};
      if (activeKey) {
        headers["x-groq-api-key"] = activeKey;
      }

      let r = await fetch("/api/agent/status", { cache: "no-store", headers }).catch(() => null);
      if (!r || !r.ok) {
        r = await fetch(`${API_BASE}/api/agent/status`, { cache: "no-store", headers });
      }
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      const data = await r.json();
      if (data?.primary) {
        setModelStatus(data.primary);
      } else if (data?.available) {
        setModelStatus("🟢 AI Engine Active");
      } else {
        setModelStatus("🟢 Multi-Provider AI (Auto)");
      }
    } catch {
      setModelStatus("🟢 Multi-Provider AI (Auto)");
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
    }
    fetchStatus(keyToUse);
  }, []);

  const handleSend = async (customPrompt?: string) => {
    const textToSend = (customPrompt || input).trim();
    if (!textToSend || isStreaming) return;

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
      };
      if (currentKey) {
        reqHeaders["x-groq-api-key"] = currentKey;
      }

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
          title: "Wyckoff Volume Profile",
          prompt: "Explain how Wyckoff Volume Profile POC, VAL, and VAH work for identifying institutional accumulation vs distribution.",
        },
        {
          title: "High Beta Risk Management",
          prompt: "How should a swing trader size positions and manage stop-losses on High Beta stocks (>1.5x SPY)?",
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

  return (
    <div className="max-w-5xl mx-auto space-y-4">
      {/* ── TOP CONTROL BAR ─────────────────────────────────────── */}
      <div className="bg-[#0d0f17] border border-[#1a1d2e] rounded-xl p-4 flex flex-wrap items-center justify-between gap-4">
        <div className="flex items-center gap-3">
          <div className="w-10 h-10 rounded-xl bg-emerald-500/10 border border-emerald-500/30 flex items-center justify-center text-xl shadow-lg shadow-emerald-500/10">
            🤖
          </div>
          <div>
            <div className="flex items-center gap-2">
              <h1 className="text-base font-bold text-white tracking-wide">StockPulse AI Analyst</h1>
              <span className="text-[11px] font-mono px-2 py-0.5 rounded-full bg-slate-800 border border-slate-700 text-slate-300">
                {modelStatus}
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
                onClick={() => setTicker(t)}
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
              onChange={(e) => setCustomTicker(e.target.value.toUpperCase())}
              className="w-24 bg-[#151824] border border-slate-700 text-white font-mono text-xs px-2.5 py-1.5 rounded-lg focus:outline-none focus:border-[#4d9fff]"
            />
          )}

          <button
            onClick={() => {
              setKeyInput(apiKey);
              setShowKeyModal(true);
            }}
            className={`text-xs border px-2.5 py-1.5 rounded-lg transition-colors flex items-center gap-1.5 font-mono ${
              apiKey
                ? "bg-emerald-500/10 border-emerald-500/30 text-emerald-400 hover:bg-emerald-500/20"
                : "text-slate-400 hover:text-white border-slate-800 hover:border-slate-700 bg-[#151824]"
            }`}
            title="Configure AI Cloud Key (Groq / Gemini / OpenRouter)"
          >
            <span>🔑</span>
            <span>{apiKey ? "Key Active" : "Set AI Key"}</span>
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

      {/* ── CHAT WORKSTATION ─────────────────────────────────────────── */}
      <div className="bg-[#0a0c13] border border-[#1a1d2e] rounded-xl flex flex-col h-[calc(100vh-130px)] min-h-[600px] shadow-2xl relative overflow-hidden">
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
                className={`max-w-[85%] sm:max-w-[75%] rounded-2xl p-4 text-sm leading-relaxed ${
                  m.role === "user"
                    ? "bg-[#1f3a5f] text-white border border-[#4d9fff]/30 rounded-tr-none shadow-md"
                    : "bg-[#121520] text-slate-200 border border-[#1e2337] rounded-tl-none shadow-sm"
                }`}
              >
                {/* Formatted Content */}
                <div className="whitespace-pre-wrap font-sans text-xs sm:text-sm">
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
        <div className="p-3 sm:p-4 bg-[#0d0f17] border-t border-[#1a1d2e]">
          <div className="flex gap-2">
            <textarea
              rows={2}
              value={input}
              onChange={(e) => setInput(e.target.value)}
              onKeyDown={handleKeyDown}
              disabled={isStreaming}
              placeholder={`Ask AI about ${currentActiveTicker} setup, risk management, Fib levels, or general questions (Press Enter to send)...`}
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
                API Key (Groq / Gemini / OpenRouter)
              </label>
              <input
                type="password"
                value={keyInput}
                onChange={(e) => setKeyInput(e.target.value.trim())}
                placeholder="gsk_... or sk-or-..."
                className="w-full bg-[#0a0c13] border border-slate-700 rounded-lg px-3 py-2 text-xs font-mono text-white focus:outline-none focus:border-[#4d9fff]"
              />
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
    </div>
  );
}

/**
 * Lightweight helper to render markdown bolding, code ticks, and blockquotes without dependencies
 */
function formatChatMarkdown(text: string) {
  const parts = text.split(/(\*\*.*?\*\*|`.*?`|\n> .*)/g);

  return parts.map((part, i) => {
    if (part.startsWith("**") && part.endsWith("**")) {
      return (
        <strong key={i} className="text-white font-bold">
          {part.slice(2, -2)}
        </strong>
      );
    }
    if (part.startsWith("`") && part.endsWith("`")) {
      return (
        <code key={i} className="bg-slate-800 text-emerald-300 px-1 py-0.5 rounded font-mono text-[11px]">
          {part.slice(1, -1)}
        </code>
      );
    }
    if (part.startsWith("\n> ")) {
      return (
        <blockquote key={i} className="border-l-2 border-[#4d9fff] pl-2 text-slate-300 italic my-1">
          {part.replace("\n> ", "")}
        </blockquote>
      );
    }
    return part;
  });
}
