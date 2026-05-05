"use client";
import { useState, useEffect, useRef } from "react";

declare global { interface Window { TradingView: any } }

function FinvizChart({ ticker }: { ticker: string }) {
  return (
    <div className="w-full bg-black flex items-center justify-center" style={{ height: 520 }}>
      <img
        src={`https://charts2.finviz.com/chart.ashx?t=${ticker}&ty=c&ta=1&p=d`}
        alt={`${ticker} Finviz chart`}
        className="max-w-full max-h-full object-contain"
        style={{ height: 520, width: "100%" }}
      />
    </div>
  );
}

function TVChart({ ticker }: { ticker: string }) {
  const containerId = `tv_${ticker}`;
  const scriptRef   = useRef<HTMLScriptElement | null>(null);

  useEffect(() => {
    function init() {
      if (!window.TradingView) return;
      new window.TradingView.widget({
        container_id:        containerId,
        autosize:            true,
        symbol:              ticker,
        interval:            "D",
        timezone:            "America/New_York",
        theme:               "dark",
        style:               "1",
        locale:              "en",
        toolbar_bg:          "#161b22",
        backgroundColor:     "#0d1117",
        gridColor:           "rgba(255,255,255,0.04)",
        enable_publishing:   false,
        hide_top_toolbar:    false,
        allow_symbol_change: false,
        save_image:          true,
        studies:             ["Volume@tv-basicstudies"],
      });
    }

    if (window.TradingView) {
      init();
    } else {
      const script    = document.createElement("script");
      script.src      = "https://s3.tradingview.com/tv.js";
      script.async    = true;
      script.onload   = init;
      document.head.appendChild(script);
      scriptRef.current = script;
    }

    return () => {
      const el = document.getElementById(containerId);
      if (el) el.innerHTML = "";
    };
  }, [ticker, containerId]);

  return <div id={containerId} style={{ height: 520 }} />;
}

type Tab = "finviz" | "tradingview";

export default function DualChart({ ticker }: { ticker: string }) {
  const [tab, setTab] = useState<Tab>("finviz");

  return (
    <div className="card p-0 overflow-hidden">
      {/* Tab bar */}
      <div className="flex items-center gap-1 px-3 pt-3 pb-0 border-b border-border">
        {([
          { key: "finviz",       label: "Finviz",       hint: "Auto patterns · Technical overlays" },
          { key: "tradingview",  label: "TradingView",  hint: "Drawing tools · Custom indicators"  },
        ] as { key: Tab; label: string; hint: string }[]).map(t => (
          <button
            key={t.key}
            onClick={() => setTab(t.key)}
            title={t.hint}
            className={`px-3 py-2 text-xs font-semibold rounded-t-md transition-colors border-b-2 -mb-px ${
              tab === t.key
                ? "text-white border-accent"
                : "text-muted border-transparent hover:text-white"
            }`}
          >
            {t.label}
          </button>
        ))}
        <span className="ml-2 text-[10px] text-muted/60 hidden sm:block">
          {tab === "finviz" ? "Auto-drawn patterns · S/R · Technical overlays" : "Full drawing tools · Custom indicators"}
        </span>
      </div>

      {/* Chart area */}
      <div>
        {tab === "finviz"
          ? <FinvizChart      ticker={ticker} />
          : <TVChart          ticker={ticker} />
        }
      </div>
    </div>
  );
}
