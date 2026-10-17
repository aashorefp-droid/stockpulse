import type { Metadata } from "next";
import "./globals.css";
import Navbar from "@/components/Navbar";
import BackendStatusBanner from "@/components/BackendStatusBanner";
import MarketRisk from "@/components/MarketRisk";
import AnalystChatDrawer from "@/components/AnalystChatDrawer";

export const metadata: Metadata = {
  title: "StockPulse",
  description: "Professional stock analysis & earnings backtest platform",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body>
        <Navbar />
        <BackendStatusBanner />
        <MarketRisk />
        <main className="w-full max-w-none px-2 sm:px-4 lg:px-6 py-3">{children}</main>
        <AnalystChatDrawer />
      </body>
    </html>
  );
}
