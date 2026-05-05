"use client";
import { useEffect, useRef } from "react";

declare global {
  interface Window { TradingView: any }
}

export default function TradingViewChart({ ticker }: { ticker: string }) {
  const containerId = `tv_${ticker}`;
  const scriptRef = useRef<HTMLScriptElement | null>(null);

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
        hide_legend:         false,
        allow_symbol_change: false,
        save_image:          true,
        studies:             ["Volume@tv-basicstudies"],
      });
    }

    if (window.TradingView) {
      init();
    } else {
      const script = document.createElement("script");
      script.src   = "https://s3.tradingview.com/tv.js";
      script.async = true;
      script.onload = init;
      document.head.appendChild(script);
      scriptRef.current = script;
    }

    return () => {
      // Clean up the container so re-mount doesn't duplicate the widget
      const el = document.getElementById(containerId);
      if (el) el.innerHTML = "";
    };
  }, [ticker, containerId]);

  return (
    <div className="card p-0 overflow-hidden">
      <div id={containerId} style={{ height: 520 }} />
    </div>
  );
}
