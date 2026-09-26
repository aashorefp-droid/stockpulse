interface ZoneData {
  earn_zone?: string;
  weekly_zone?: string;
  near_fib_name?: string;
  near_fib_price?: number;
  week_hi?: number;
  week_lo?: number;
  wk_pos_pct?: number;
  earn_hi?: number;
  earn_lo?: number;
  earn_pos_pct?: number;
  fib_levels?: Record<string, number>;
  fib_compression?: boolean;
  current_price?: number;
}

const zoneColor = (zone?: string) => {
  switch (zone) {
    case "HIGH": return "bg-red/15 text-red border-red/30";
    case "LOW":  return "bg-green/15 text-green border-green/30";
    case "MID":  return "bg-yellow/15 text-yellow border-yellow/30";
    default:     return "bg-muted/10 text-muted border-border";
  }
};

const zoneIcon = (zone?: string) => {
  switch (zone) {
    case "HIGH": return "⚠️";
    case "LOW":  return "🟢";
    case "MID":  return "⚖️";
    default:     return "—";
  }
};

export default function ZoneCard({ data }: { data: ZoneData }) {
  const fibEntries = Object.entries(data.fib_levels ?? {})
    .map(([k, v]) => ({ name: k, price: v }))
    .sort((a, b) => b.price - a.price);

  const price = data.current_price ?? 0;

  return (
    <div className="card space-y-4">
      <div className="flex items-center justify-between">
        <h3 className="text-sm font-semibold text-muted">Zones &amp; Fibonacci Levels</h3>
        {data.fib_compression && (
          <span className="text-[10px] font-bold px-2 py-0.5 rounded bg-yellow/10 text-yellow border border-yellow/20">
            🔥 FIB COMPRESSION
          </span>
        )}
      </div>

      {/* Zone summary */}
      <div className="grid grid-cols-2 gap-3">
        <div className={`rounded-lg p-3 border ${zoneColor(data.earn_zone)}`}>
          <div className="text-[10px] uppercase tracking-wider opacity-70 mb-1">Earnings Zone (60d)</div>
          <div className="text-lg font-bold flex items-center gap-1">
            {zoneIcon(data.earn_zone)} {data.earn_zone ?? "N/A"}
          </div>
          <div className="text-[11px] font-mono opacity-80 mt-1">
            {data.earn_pos_pct?.toFixed(1)}% of range
          </div>
        </div>
        <div className={`rounded-lg p-3 border ${zoneColor(data.weekly_zone)}`}>
          <div className="text-[10px] uppercase tracking-wider opacity-70 mb-1">Weekly Zone (5d)</div>
          <div className="text-lg font-bold flex items-center gap-1">
            {zoneIcon(data.weekly_zone)} {data.weekly_zone ?? "N/A"}
          </div>
          <div className="text-[11px] font-mono opacity-80 mt-1">
            {data.wk_pos_pct?.toFixed(1)}% of range
          </div>
        </div>
      </div>

      {/* Range table */}
      <div className="space-y-1">
        <div className="grid grid-cols-4 text-[10px] text-muted px-2 pb-1 border-b border-border">
          <span>Window</span>
          <span className="text-right">Lo</span>
          <span className="text-right">Hi</span>
          <span className="text-right">Pos %</span>
        </div>
        <div className="grid grid-cols-4 text-xs font-mono px-2 py-1.5">
          <span className="text-muted">Earnings (60d)</span>
          <span className="text-right text-red">${data.earn_lo?.toFixed(2)}</span>
          <span className="text-right text-green">${data.earn_hi?.toFixed(2)}</span>
          <span className="text-right text-white">{data.earn_pos_pct?.toFixed(1)}%</span>
        </div>
        <div className="grid grid-cols-4 text-xs font-mono px-2 py-1.5">
          <span className="text-muted">Weekly (5d)</span>
          <span className="text-right text-red">${data.week_lo?.toFixed(2)}</span>
          <span className="text-right text-green">${data.week_hi?.toFixed(2)}</span>
          <span className="text-right text-white">{data.wk_pos_pct?.toFixed(1)}%</span>
        </div>
      </div>

      {/* Nearest fib + all fib levels */}
      {fibEntries.length > 0 && (
        <div className="space-y-2 pt-2 border-t border-border">
          <div className="flex items-center justify-between text-xs">
            <span className="text-muted">Nearest Fib</span>
            <span className="font-mono">
              <span className="text-yellow font-semibold">{data.near_fib_name}</span>
              <span className="text-muted ml-1">@</span>
              <span className="text-white ml-1">${data.near_fib_price?.toFixed(2)}</span>
            </span>
          </div>
          <div className="space-y-0.5">
            {fibEntries.map(({ name, price: p }) => {
              const isAbove = p > price;
              const isNear  = name === data.near_fib_name;
              return (
                <div key={name}
                  className={`grid grid-cols-[60px_1fr_auto] gap-2 text-[11px] font-mono px-2 py-1 rounded
                    ${isNear ? "bg-yellow/5" : ""}`}>
                  <span className={isNear ? "text-yellow font-semibold" : "text-muted"}>{name}</span>
                  <div className="flex items-center">
                    <div className={`h-px flex-1 ${isAbove ? "bg-red/30" : "bg-green/30"}`} />
                  </div>
                  <span className={isAbove ? "text-red" : "text-green"}>
                    ${p.toFixed(2)}
                  </span>
                </div>
              );
            })}
          </div>
          <p className="text-[10px] text-muted/60 italic">
            Levels above price (red) act as resistance · below (green) as support
          </p>
        </div>
      )}
    </div>
  );
}
