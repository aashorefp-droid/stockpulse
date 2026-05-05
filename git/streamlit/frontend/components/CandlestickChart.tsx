"use client";
import { useEffect, useRef } from "react";
import type { OhlcBar } from "@/lib/api";

interface Props {
  data: OhlcBar[];
  ticker: string;
  supportLevels?: number[];
  resistanceLevels?: number[];
}

export default function CandlestickChart({ data, ticker, supportLevels = [], resistanceLevels = [] }: Props) {
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!ref.current || data.length === 0) return;

    let chart: any, candleSeries: any;

    (async () => {
      const { createChart, CrosshairMode, LineStyle } = await import("lightweight-charts");

      ref.current!.innerHTML = "";

      chart = createChart(ref.current!, {
        width:  ref.current!.clientWidth,
        height: 420,
        layout: { background: { color: "#161b22" }, textColor: "#8b949e" },
        grid:   { vertLines: { color: "#21262d" }, horzLines: { color: "#21262d" } },
        crosshair: { mode: CrosshairMode.Normal },
        rightPriceScale: { borderColor: "#30363d" },
        timeScale: { borderColor: "#30363d", timeVisible: true },
      });

      candleSeries = chart.addCandlestickSeries({
        upColor:   "#00e5a0",
        downColor: "#ff4d4f",
        borderUpColor:   "#00e5a0",
        borderDownColor: "#ff4d4f",
        wickUpColor:   "#00e5a0",
        wickDownColor: "#ff4d4f",
      });
      candleSeries.setData(data);

      // Support lines
      supportLevels.forEach((lvl) => {
        const line = chart.addLineSeries({
          color: "#00e5a080",
          lineWidth: 1,
          lineStyle: LineStyle.Dashed,
          lastValueVisible: false,
          priceLineVisible: false,
        });
        if (data.length > 0) {
          line.setData([
            { time: data[0].time,  value: lvl },
            { time: data[data.length - 1].time, value: lvl },
          ]);
        }
      });

      // Resistance lines
      resistanceLevels.forEach((lvl) => {
        const line = chart.addLineSeries({
          color: "#ff4d4f80",
          lineWidth: 1,
          lineStyle: LineStyle.Dashed,
          lastValueVisible: false,
          priceLineVisible: false,
        });
        if (data.length > 0) {
          line.setData([
            { time: data[0].time,  value: lvl },
            { time: data[data.length - 1].time, value: lvl },
          ]);
        }
      });

      chart.timeScale().fitContent();

      const ro = new ResizeObserver(() => {
        chart.applyOptions({ width: ref.current!.clientWidth });
      });
      ro.observe(ref.current!);
    })();

    return () => {
      chart?.remove();
    };
  }, [data, supportLevels, resistanceLevels]);

  return <div ref={ref} className="w-full rounded-lg overflow-hidden" />;
}
