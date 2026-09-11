import type { Metadata } from "next";
import "./globals.css";
import Navbar from "@/components/Navbar";
import MarketRisk from "@/components/MarketRisk";

export const metadata: Metadata = {
  title: "StockPulse",
  description: "Professional stock analysis & earnings backtest platform",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body>
        <Navbar />
        <MarketRisk />
        <main className="max-w-screen-2xl mx-auto px-4 py-6">{children}</main>
      </body>
    </html>
  );
}
