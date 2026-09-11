import type { Fundamentals } from "@/lib/api";

interface Row { label: string; value: string | number }

export default function FundamentalsCard({ f }: { f: Fundamentals }) {
  const rows: Row[] = [
    { label: "Market Cap",      value: f.market_cap },
    { label: "P/E (TTM)",       value: f.pe_ratio },
    { label: "Forward P/E",     value: f.forward_pe },
    { label: "EPS",             value: f.eps },
    { label: "Profit Margin",   value: `${f.profit_margin}%` },
    { label: "Dividend Yield",  value: `${f.dividend_yield}%` },
    { label: "52W High",        value: `$${f["52w_high"]}` },
    { label: "52W Low",         value: `$${f["52w_low"]}` },
    { label: "Beta",            value: f.beta },
    { label: "Avg Volume",      value: typeof f.avg_volume === "number" ? f.avg_volume.toLocaleString() : f.avg_volume },
  ];

  return (
    <div className="card">
      <div className="mb-3">
        <h3 className="font-semibold text-white">{f.name}</h3>
        <p className="text-muted text-xs">{f.sector} · {f.industry}</p>
      </div>
      <div className="grid grid-cols-2 gap-x-4 gap-y-2">
        {rows.map(({ label, value }) => (
          <div key={label} className="flex justify-between text-xs border-b border-border/40 pb-1">
            <span className="text-muted">{label}</span>
            <span className="font-mono text-white">{value ?? "N/A"}</span>
          </div>
        ))}
      </div>
      {f.description && (
        <p className="mt-3 text-xs text-muted leading-relaxed line-clamp-3">{f.description}</p>
      )}
    </div>
  );
}
