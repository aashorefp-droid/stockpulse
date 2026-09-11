"use client";
import { useState } from "react";

const PERIODS = [
  { key: "d", label: "Daily" },
  { key: "w", label: "Weekly" },
  { key: "m", label: "Monthly" },
];

export default function FinvizChart({ ticker }: { ticker: string }) {
  const [period, setPeriod] = useState("d");
  const url = `https://finviz.com/chart.ashx?t=${ticker}&ty=c&ta=1&p=${period}&s=l`;

  return (
    <div className="card p-0 overflow-hidden">
      <div className="px-4 pt-4 pb-2 flex items-center gap-3">
        <span className="text-sm font-semibold text-white">Price Chart</span>
        <div className="flex gap-1 ml-auto">
          {PERIODS.map(({ key, label }) => (
            <button
              key={key}
              onClick={() => setPeriod(key)}
              className={`px-3 py-1 text-xs rounded-md font-semibold transition-colors ${
                period === key
                  ? "bg-accent text-black"
                  : "text-muted hover:text-white"
              }`}
            >
              {label}
            </button>
          ))}
        </div>
      </div>
      {/* eslint-disable-next-line @next/next/no-img-element */}
      <img
        src={url}
        alt={`${ticker} ${period} chart`}
        className="w-full block"
        referrerPolicy="no-referrer"
      />
    </div>
  );
}
