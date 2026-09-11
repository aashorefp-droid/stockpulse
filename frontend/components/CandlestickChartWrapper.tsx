"use client";
import dynamic from "next/dynamic";
import type { OhlcBar } from "@/lib/api";

const CandlestickChart = dynamic(() => import("./CandlestickChart"), { ssr: false });

export default function CandlestickChartWrapper(props: {
  data: OhlcBar[];
  ticker: string;
  supportLevels?: number[];
  resistanceLevels?: number[];
}) {
  return <CandlestickChart {...props} />;
}
