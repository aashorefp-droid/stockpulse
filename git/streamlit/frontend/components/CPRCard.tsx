interface CPRData {
  cpr_type?:           string;  // Narrow / Normal / Wide
  cpr_tc?:             number;  // top central
  cpr_p?:              number;  // pivot
  cpr_bc?:             number;  // bottom central
  cpr_position?:       string;  // Above / Inside / Below
  cpr_interpretation?: string;
  exp_move_up?:        number;
  exp_move_down?:      number;
  exp_move_pct?:       number;
  exp_move_open_up?:   number;
  exp_move_open_dn?:   number;
  exp_move_open_pct?:  number;
  day_open?:           number;
  atr?:                number;
  current_price?:      number;
  asOf?:               string;
}

const typeColor = (t?: string) =>
  t === "Narrow" ? "text-yellow border-yellow/30 bg-yellow/5"  :
  t === "Wide"   ? "text-accent border-accent/30 bg-accent/5"  :
                   "text-muted  border-border  bg-card";

const posColor = (p?: string) =>
  p === "Above"  ? "text-green"  :
  p === "Below"  ? "text-red"    :
  p === "Inside" ? "text-yellow" :
                   "text-muted";

const posIcon = (p?: string) =>
  p === "Above" ? "▲" : p === "Below" ? "▼" : p === "Inside" ? "—" : "·";

export default function CPRCard({ data }: { data: CPRData }) {
  if (!data.cpr_type) {
    return (
      <div className="card">
        <h3 className="text-sm font-semibold text-muted mb-2">CPR (Central Pivot Range)</h3>
        <p className="text-xs text-muted">Insufficient data — need at least 2 daily bars.</p>
      </div>
    );
  }

  const price = data.current_price ?? 0;
  const tc    = data.cpr_tc ?? 0;
  const p     = data.cpr_p  ?? 0;
  const bc    = data.cpr_bc ?? 0;
  const width = tc - bc;
  const widthPct = p > 0 ? (width / p * 100).toFixed(2) : "0";

  // Build the visual stack: price marker positioned vs TC/P/BC
  // Place them on a vertical scale relative to a small range around CPR
  const range = Math.max(tc - bc, 0.01);
  const padding = range * 0.5;
  const top = tc + padding;
  const bot = bc - padding;
  const fullRange = top - bot;
  const pct = (v: number) => Math.max(0, Math.min(100, ((top - v) / fullRange) * 100));

  return (
    <div className="card space-y-4">
      <div className="flex items-center justify-between">
        <h3 className="text-sm font-semibold text-muted">CPR (Central Pivot Range)</h3>
        <div className="flex items-center gap-2">
          <span className={`text-[10px] font-bold px-2 py-0.5 rounded border ${typeColor(data.cpr_type)}`}>
            {data.cpr_type?.toUpperCase()}
          </span>
          <span className={`text-[10px] font-bold ${posColor(data.cpr_position)}`}>
            {posIcon(data.cpr_position)} {data.cpr_position?.toUpperCase()}
          </span>
        </div>
      </div>

      {/* Interpretation */}
      <p className={`text-sm ${posColor(data.cpr_position)} leading-relaxed`}>
        {data.cpr_interpretation}
      </p>

      {/* Expected range from day open (ATR-based) */}
      {data.exp_move_open_up != null && data.exp_move_open_dn != null && data.exp_move_open_pct != null && (
        <div className="rounded-lg border border-yellow/20 bg-yellow/5 px-3 py-2 flex items-center justify-between gap-3">
          <div>
            <div className="text-[10px] uppercase tracking-wider text-muted">
              Expected range from open {data.day_open != null && <span className="text-yellow ml-1">(${data.day_open.toFixed(2)})</span>}
            </div>
            <div className="text-sm font-mono text-yellow">
              ±${data.atr?.toFixed(2) ?? "—"} (±{data.exp_move_open_pct.toFixed(2)}%)
            </div>
          </div>
          <div className="flex gap-3 text-xs font-mono">
            <div className="text-right">
              <div className="text-[10px] text-muted">Lower</div>
              <div className="text-red">${data.exp_move_open_dn.toFixed(2)}</div>
            </div>
            <div className="text-right">
              <div className="text-[10px] text-muted">Upper</div>
              <div className="text-green">${data.exp_move_open_up.toFixed(2)}</div>
            </div>
          </div>
        </div>
      )}

      {/* Visual ladder */}
      <div className="flex gap-4">
        <div className="relative w-1 bg-surface rounded h-32 flex-shrink-0">
          {/* TC marker */}
          <div className="absolute left-0 right-0 h-px bg-red"
               style={{ top: `${pct(tc)}%` }} />
          {/* P marker */}
          <div className="absolute left-0 right-0 h-px bg-yellow"
               style={{ top: `${pct(p)}%` }} />
          {/* BC marker */}
          <div className="absolute left-0 right-0 h-px bg-green"
               style={{ top: `${pct(bc)}%` }} />
          {/* Current price marker */}
          {price > 0 && (
            <div className="absolute -left-1 -right-1 h-1.5 bg-accent rounded-full ring-2 ring-surface"
                 style={{ top: `calc(${pct(price)}% - 3px)` }} />
          )}
        </div>

        {/* Labels */}
        <div className="flex-1 grid grid-cols-2 gap-3 text-xs">
          <div className="space-y-1.5">
            <div className="flex justify-between gap-2 px-2 py-1 rounded bg-red/5 border border-red/20">
              <span className="text-red font-semibold">TC</span>
              <span className="font-mono text-white">${tc.toFixed(2)}</span>
            </div>
            <div className="flex justify-between gap-2 px-2 py-1 rounded bg-yellow/5 border border-yellow/20">
              <span className="text-yellow font-semibold">P (Pivot)</span>
              <span className="font-mono text-white">${p.toFixed(2)}</span>
            </div>
            <div className="flex justify-between gap-2 px-2 py-1 rounded bg-green/5 border border-green/20">
              <span className="text-green font-semibold">BC</span>
              <span className="font-mono text-white">${bc.toFixed(2)}</span>
            </div>
          </div>

          <div className="space-y-1.5">
            <div className="flex justify-between gap-2 px-2 py-1 rounded bg-card border border-border">
              <span className="text-muted">Price</span>
              <span className="font-mono text-accent">${price.toFixed(2)}</span>
            </div>
            <div className="flex justify-between gap-2 px-2 py-1 rounded bg-card border border-border">
              <span className="text-muted">Width</span>
              <span className="font-mono text-white">${width.toFixed(2)} ({widthPct}%)</span>
            </div>
            <div className="flex justify-between gap-2 px-2 py-1 rounded bg-card border border-border">
              <span className="text-muted">Computed</span>
              <span className="font-mono text-muted">{data.asOf ? `${data.asOf} (PD)` : "Today (PD)"}</span>
            </div>
          </div>
        </div>
      </div>

      <p className="text-[10px] text-muted/60 italic">
        CPR uses prior day's High/Low/Close. {data.asOf
          ? `Backtest active — CPR derived from the bar before ${data.asOf}.`
          : "Use the date picker (top-right) to backtest CPR for any prior date."}
      </p>
    </div>
  );
}
