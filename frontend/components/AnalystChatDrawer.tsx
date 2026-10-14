"use client";

import { useState, useRef, useEffect } from "react";
import { usePathname } from "next/navigation";
import Link from "next/link";

const API_BASE = process.env.NEXT_PUBLIC_API_URL || "https://stockpulse-pkpj.onrender.com";

interface AnalystChatDrawerProps {
  currentTicker?: string;
  currentPrice?: number;
}

interface Message {
  role: "user" | "assistant";
  content: string;
  toolStatus?: string;
}

export default function AnalystChatDrawer({ currentTicker, currentPrice }: AnalystChatDrawerProps) {
  const pathname = usePathname();
  const pathTicker = pathname?.startsWith("/stock/")
    ? pathname.split("/")[2]?.toUpperCase()
    : undefined;

  const [ticker, setTicker] = useState<string>(currentTicker || pathTicker || "");
  const [isOpen, setIsOpen] = useState(false);

  useEffect(() => {
    if (currentTicker) {
      setTicker(currentTicker);
    } else if (pathTicker) {
      setTicker(pathTicker);
    }
  }, [currentTicker, pathTicker]);

  const [messages, setMessages] = useState<Message[]>([
    {
      role: "assistant",
      content: `Hello! I am **StockPulse AI**, your institutional trading assistant. ${
        currentTicker || pathTicker
          ? `I'm tracking **${currentTicker || pathTicker}**${currentPrice ? ` at **$${currentPrice.toFixed(2)}**` : ""}. How can I assist with your setup or risk plan?`
          : "Ask me to analyze any stock ticker, review an entry risk, or explain Volume Profile levels!"
      }`,
    },
  ]);
  const [input, setInput] = useState("");
  const [selectedModel, setSelectedModel] = useState<string>("auto");
  const [entryPrice, setEntryPrice] = useState<string>("");
  const [isStreaming, setIsStreaming] = useState(false);
  const [activeTool, setActiveTool] = useState<string | null>(null);
  const [modelStatus, setModelStatus] = useState<string>("Checking model...");
  const messagesEndRef = useRef<HTMLDivElement>(null);

  const scrollToBottom = () => {
    messagesEndRef.current?.scrollIntoView({ behavior: "smooth" });
  };

  useEffect(() => {
    scrollToBottom();
  }, [messages, activeTool]);

  // Check agent status on mount
  useEffect(() => {
    fetch(`${API_BASE}/api/agent/status`)
      .then((r) => r.json())
      .then((data) => {
        if (data.primary) {
          setModelStatus(data.primary);
        } else if (data.available) {
          setModelStatus("🟢 AI Active");
        } else {
          setModelStatus("⚪ Local Ollama Offline");
        }
      })
      .catch(() => {
        setModelStatus("🟢 Auto Fallback");
      });
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

    // Prepare assistant placeholder message
    setMessages((prev) => [...prev, { role: "assistant", content: "" }]);

    try {
      const endpoint = `${API_BASE}/api/agent/chat`;
      const parsedEntry = entryPrice ? parseFloat(entryPrice) : undefined;

      const response = await fetch(endpoint, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          messages: nextMessages.map((m) => ({ role: m.role, content: m.content })),
          ticker: ticker || currentTicker || pathTicker || undefined,
          entry_price: !isNaN(Number(parsedEntry)) ? parsedEntry : undefined,
          model: selectedModel !== "auto" ? selectedModel : undefined,
        }),
      });

      if (!response.ok) {
        throw new Error(`HTTP ${response.status}`);
      }

      const reader = response.body?.getReader();
      const decoder = new TextDecoder();
      let assistantReply = "";

      while (reader) {
        const { value, done } = await reader.read();
        if (done) break;

        const chunkText = decoder.decode(value);
        const lines = chunkText.split("\n");

        for (const line of lines) {
          if (line.startsWith("data: ") && !line.includes("[DONE]")) {
            try {
              const eventData = JSON.parse(line.replace("data: ", ""));
              if (eventData.type === "tool_start") {
                setActiveTool(`Fetching live Volume Profile, Fib levels & Stop Loss for ${eventData.ticker}...`);
              } else if (eventData.type === "tool_done") {
                setActiveTool(`✓ Retrieved analysis for ${eventData.ticker}`);
              } else if (eventData.type === "status_note") {
                setActiveTool(`🔄 ${eventData.content}`);
              } else if (eventData.type === "text_delta" || eventData.type === "token") {
                setActiveTool(null);
                assistantReply += eventData.content;
                setMessages((prev) => {
                  const updated = [...prev];
                  updated[updated.length - 1] = {
                    role: "assistant",
                    content: assistantReply,
                  };
                  return updated;
                });
              } else if (eventData.type === "error") {
                assistantReply += `\n\n⚠️ ${eventData.content}`;
                setMessages((prev) => {
                  const updated = [...prev];
                  updated[updated.length - 1] = {
                    role: "assistant",
                    content: assistantReply,
                  };
                  return updated;
                });
              }
            } catch {
              // ignore malformed lines
            }
          }
        }
      }
    } catch (err: any) {
      setMessages((prev) => {
        const updated = [...prev];
        updated[updated.length - 1] = {
          role: "assistant",
          content: `⚠️ Error communicating with AI analyst agent: ${err.message || "Failed to reach endpoint"}. Ensure local Ollama or backend is active.`,
        };
        return updated;
      });
    } finally {
      setIsStreaming(false);
      setActiveTool(null);
    }
  };

  // Lightweight native markdown formatter for clean chat typography
  const renderFormattedText = (raw: string) => {
    const lines = raw.split("\n");
    return lines.map((line, idx) => {
      // Bold rendering regex replacement
      const parseBold = (text: string) => {
        const parts = text.split(/(\*\*.*?\*\*)/g);
        return parts.map((part, pIdx) => {
          if (part.startsWith("**") && part.endsWith("**")) {
            return (
              <strong key={pIdx} className="text-white font-bold">
                {part.slice(2, -2)}
              </strong>
            );
          }
          return part;
        });
      };

      if (line.startsWith("### ")) {
        return (
          <h4 key={idx} className="text-sm font-bold text-[#00e5a0] mt-2 mb-1">
            {line.replace("### ", "")}
          </h4>
        );
      }
      if (line.startsWith("## ") || line.startsWith("# ")) {
        return (
          <h3 key={idx} className="text-sm font-extrabold text-white mt-2 mb-1">
            {line.replace(/^#+ /, "")}
          </h3>
        );
      }
      if (line.startsWith("* ") || line.startsWith("- ")) {
        return (
          <li key={idx} className="ml-4 list-disc text-slate-300 my-0.5">
            {parseBold(line.slice(2))}
          </li>
        );
      }
      if (line.startsWith("> ")) {
        return (
          <blockquote key={idx} className="border-l-2 border-[#00e5a0] pl-2 text-slate-400 italic my-1">
            {parseBold(line.slice(2))}
          </blockquote>
        );
      }
      if (!line.trim()) {
        return <div key={idx} className="h-1.5" />;
      }
      return (
        <p key={idx} className="my-0.5 leading-relaxed text-slate-300">
          {parseBold(line)}
        </p>
      );
    });
  };

  if (pathname === "/chat") {
    return null;
  }

  return (
    <>
      {/* ── Floating Action Trigger Button ──────────────────────────── */}
      <button
        type="button"
        onClick={() => setIsOpen(!isOpen)}
        className={`fixed bottom-6 right-6 z-50 px-4 py-3 rounded-full font-bold shadow-2xl flex items-center gap-2.5 transition-all transform hover:scale-105 active:scale-95 border ${
          isOpen
            ? "bg-[#131625] text-white border-[#38bdf8]/50 shadow-[#38bdf8]/20"
            : "bg-gradient-to-r from-emerald-500 via-[#00e5a0] to-teal-400 text-black border-white/20 shadow-emerald-500/30"
        }`}
        title="Chat with StockPulse AI Analyst"
      >
        <span className="text-lg">🤖</span>
        <span className="text-xs tracking-wide">
          {isOpen ? "Close Analyst" : ticker ? `Ask AI about ${ticker}` : "AI Trade Analyst"}
        </span>
        {!isOpen && (
          <span className="w-2 h-2 rounded-full bg-emerald-950 animate-ping" />
        )}
      </button>

      {/* ── Slide-up Floating Chat Window ────────────────────────────── */}
      {isOpen && (
        <div className="fixed bottom-20 right-6 w-[420px] max-w-[94vw] h-[580px] max-h-[82vh] bg-[#0d0f17] border border-[#1a1d2e] rounded-2xl shadow-2xl flex flex-col z-50 overflow-hidden backdrop-blur-xl">
          {/* Header */}
          <div className="p-3.5 border-b border-[#1a1d2e] bg-gradient-to-r from-[#101422] via-[#0d0f17] to-[#131625] flex items-center justify-between">
            <div className="flex items-center gap-2.5">
              <span className="w-8 h-8 rounded-lg bg-[#00e5a0]/10 border border-[#00e5a0]/30 flex items-center justify-center text-sm">
                🤖
              </span>
              <div>
                <div className="text-xs font-bold text-white flex items-center gap-1.5 font-mono">
                  <span>StockPulse AI</span>
                  {ticker ? (
                    <span className="px-1.5 py-0.2 rounded bg-[#00e5a0]/20 text-[#00e5a0] text-[10px] font-bold">
                      {ticker}
                    </span>
                  ) : (
                    <span className="text-[10px] text-slate-400 font-normal">All Tickers</span>
                  )}
                </div>
                <div className="flex items-center gap-1.5 mt-1">
                  <select
                    value={selectedModel}
                    onChange={(e) => setSelectedModel(e.target.value)}
                    className="bg-[#151824] border border-slate-700 text-[10px] text-emerald-400 font-mono rounded px-1.5 py-0.5 focus:outline-none cursor-pointer"
                    title="Select AI Engine"
                  >
                    <option value="auto" className="bg-[#151824] text-white">⚡ Auto Fallback</option>
                    <option value="groq" className="bg-[#151824] text-white">🚀 Groq (800 t/s)</option>
                    <option value="gemini" className="bg-[#151824] text-white">🧠 Gemini Flash</option>
                    <option value="cerebras" className="bg-[#151824] text-white">⚡ Cerebras (1800 t/s)</option>
                    <option value="openrouter" className="bg-[#151824] text-white">🌐 OpenRouter</option>
                    <option value="ollama" className="bg-[#151824] text-white">💻 Local Ollama</option>
                  </select>
                </div>
              </div>
            </div>

            <div className="flex items-center gap-2">
              <Link
                href="/chat"
                className="text-[11px] text-emerald-400 hover:text-white px-2 py-0.5 rounded bg-emerald-500/10 border border-emerald-500/30 hover:bg-emerald-500/20 transition-all font-mono"
                title="Open full-screen AI chat page"
              >
                Expand ↗
              </Link>
              <button
                onClick={() => setIsOpen(false)}
                className="w-7 h-7 rounded-lg bg-surface border border-border flex items-center justify-center text-slate-400 hover:text-white transition-all text-xs"
                title="Close window"
              >
                ✕
              </button>
            </div>
          </div>

          {/* Quick Action Suggestion Chips */}
          <div className="px-3 py-2 border-b border-[#1a1d2e]/60 bg-[#0a0b14]/70 flex items-center gap-1.5 overflow-x-auto text-[11px] font-mono no-scrollbar">
            {ticker ? (
              <>
                <button
                  onClick={() => handleSend(`How is ${ticker} looking today? What is the Volume Profile and Fib level?`)}
                  className="px-2.5 py-1 rounded-md bg-[#131625] text-slate-300 hover:text-[#00e5a0] hover:border-[#00e5a0]/40 border border-[#1a1d2e] whitespace-nowrap transition-all"
                >
                  📈 How is {ticker} looking?
                </button>
                <button
                  onClick={() => handleSend(`I entered ${ticker} at ${entryPrice ? `$${entryPrice}` : "current price"}. What is my risk and profit target plan?`)}
                  className="px-2.5 py-1 rounded-md bg-[#131625] text-slate-300 hover:text-[#38bdf8] hover:border-[#38bdf8]/40 border border-[#1a1d2e] whitespace-nowrap transition-all"
                >
                  🎯 Entry & Target Plan
                </button>
                <button
                  onClick={() => handleSend(`Explain the Volume Profile POC, VAL, and VAH for ${ticker}.`)}
                  className="px-2.5 py-1 rounded-md bg-[#131625] text-slate-300 hover:text-amber-400 hover:border-amber-400/40 border border-[#1a1d2e] whitespace-nowrap transition-all"
                >
                  📊 Volume Profile Breakdown
                </button>
              </>
            ) : (
              <>
                <button
                  onClick={() => handleSend("How is SPY and tech sector holding up today?")}
                  className="px-2.5 py-1 rounded-md bg-[#131625] text-slate-300 hover:text-[#00e5a0] border border-[#1a1d2e] whitespace-nowrap transition-all"
                >
                  🏛️ Market & SPY Bias
                </button>
                <button
                  onClick={() => handleSend("Analyze AAOI for swing trading.")}
                  className="px-2.5 py-1 rounded-md bg-[#131625] text-slate-300 hover:text-[#38bdf8] border border-[#1a1d2e] whitespace-nowrap transition-all"
                >
                  ⚡ Analyze AAOI
                </button>
              </>
            )}
          </div>

          {/* Messages Feed */}
          <div className="flex-1 p-3.5 overflow-y-auto space-y-3 text-xs bg-[#090b12]">
            {messages.map((m, i) => (
              <div
                key={i}
                className={`flex flex-col ${m.role === "user" ? "items-end" : "items-start"}`}
              >
                <div
                  className={`p-3 rounded-xl max-w-[88%] text-xs shadow-md ${
                    m.role === "user"
                      ? "bg-[#182338] text-white border border-[#2b3954] rounded-br-none"
                      : "bg-[#0f1322] text-slate-200 border border-[#1a1d2e] rounded-bl-none font-sans"
                  }`}
                >
                  {m.role === "assistant" && (
                    <div className="flex items-center gap-1.5 mb-1.5 text-[10px] font-mono text-[#00e5a0] font-semibold border-b border-border/30 pb-1">
                      <span>🤖 AI Analyst</span>
                    </div>
                  )}
                  {m.content ? renderFormattedText(m.content) : (
                    <span className="inline-flex items-center gap-1.5 text-slate-400 font-mono text-[11px] animate-pulse">
                      <span>Analyzing chart data</span>
                      <span className="w-1.5 h-1.5 rounded-full bg-[#00e5a0]"></span>
                    </span>
                  )}
                </div>
              </div>
            ))}

            {/* Active Tool Invocation Banner */}
            {activeTool && (
              <div className="p-2.5 rounded-lg bg-black/60 border border-cyan-500/30 text-cyan-300 text-[11px] font-mono flex items-center gap-2 animate-pulse">
                <span className="text-sm">⚡</span>
                <span>{activeTool}</span>
              </div>
            )}

            <div ref={messagesEndRef} />
          </div>

          {/* Optional Entry Price Context Helper */}
          {currentTicker && (
            <div className="px-3 py-1.5 bg-[#0d101d] border-t border-[#1a1d2e] flex items-center justify-between text-[11px] text-slate-400 font-mono">
              <span className="flex items-center gap-1">
                <span>💰</span>
                <span>Your Entry Price:</span>
              </span>
              <div className="flex items-center gap-1.5">
                <span className="text-slate-500">$</span>
                <input
                  type="number"
                  value={entryPrice}
                  onChange={(e) => setEntryPrice(e.target.value)}
                  placeholder="e.g. 116.00"
                  className="w-20 bg-[#06080f] border border-[#1a1d2e] rounded px-1.5 py-0.5 text-right text-white font-mono text-[11px] focus:outline-none focus:border-[#00e5a0]"
                />
              </div>
            </div>
          )}

          {/* Input Bar */}
          <div className="p-2.5 border-t border-[#1a1d2e] bg-[#0c0e18] flex items-center gap-2">
            <input
              type="text"
              value={input}
              onChange={(e) => setInput(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter" && !e.shiftKey) {
                  e.preventDefault();
                  handleSend();
                }
              }}
              placeholder={
                currentTicker
                  ? `Ask about ${currentTicker} (e.g. "Should I cut or hold at 116?")`
                  : "Ask about any stock ticker..."
              }
              disabled={isStreaming}
              className="flex-1 bg-[#06080f] border border-[#1a1d2e] rounded-lg px-3 py-2 text-xs text-white placeholder-slate-500 focus:outline-none focus:border-[#00e5a0] transition-all font-sans"
            />
            <button
              type="button"
              onClick={() => handleSend()}
              disabled={isStreaming || !input.trim()}
              className="px-3.5 py-2 rounded-lg bg-[#00e5a0] hover:bg-[#00e5a0]/90 disabled:opacity-40 disabled:hover:bg-[#00e5a0] text-black font-extrabold text-xs transition-all flex items-center gap-1 shadow-md shadow-[#00e5a0]/20"
            >
              <span>Send</span>
              <span>➤</span>
            </button>
          </div>
        </div>
      )}
    </>
  );
}
