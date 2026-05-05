interface Trade {
  entry:     number;
  stop_loss: number | null;
  target1:   number | null;
  target2:   number | null;
  risk_pct:  number | null;
  rr_t1:     number | null;
  rr_t2:     number | null;
  t1_days:   number | null;
  t2_days:   number | null;
  atr:       number;
}

interface OptionsLeg {
  action: string; type: string; strike: number; exp: string; mid: number;
}

interface OptionsStrategy {
  strategy?: string; summary?: string; alt?: string;
  net_debit?: number; max_profit?: number | null; legs?: OptionsLeg[];
}

function Row({ label, value, color }: { label: string; value: string; color?: string }) {
  return (
    <div className="flex justify-between items-center py-1.5 border-b border-border/40 last:border-0">
      <span className="text-sm text-muted">{label}</span>
      <span className={`font-mono font-semibold ${color ?? "text-white"}`}>{value}</span>
    </div>
  );
}

export default function TradeCard({
  trade, verdict, optionsStrategy,
}: {
  trade: Trade;
  verdict: string;
  optionsStrategy?: OptionsStrategy | null;
}) {
  if (!trade.stop_loss) {
    return (
      <div className="card flex items-center justify-center min-h-[120px]">
        <p className="text-muted text-sm">No trade levels — signal too weak</p>
      </div>
    );
  }

  const isBull    = verdict.includes("BULLISH");
  const stopColor = isBull ? "text-red"   : "text-green";
  const t1Color   = isBull ? "text-green" : "text-red";

  return (
    <div className="card space-y-0">
      <h3 className="text-sm font-semibold text-muted mb-3">Trade Levels</h3>

      <Row label="Entry"     value={`$${trade.entry}`}    color="text-accent" />
      <Row label="Stop Loss" value={`$${trade.stop_loss}`} color={stopColor} />
      <Row label="Target 1"  value={`$${trade.target1}${trade.t1_days ? ` (~${trade.t1_days}d)` : ""}`} color={t1Color} />
      <Row label="Target 2"  value={`$${trade.target2}${trade.t2_days ? ` (~${trade.t2_days}d)` : ""}`} color={t1Color} />

      <div className="mt-3 pt-3 border-t border-border grid grid-cols-3 gap-2 text-center">
        <div>
          <div className="text-base font-bold font-mono text-red">{trade.risk_pct}%</div>
          <div className="text-xs text-muted">Risk</div>
        </div>
        <div>
          <div className="text-base font-bold font-mono text-accent">{trade.rr_t1}R</div>
          <div className="text-xs text-muted">R/R T1</div>
        </div>
        <div>
          <div className="text-base font-bold font-mono text-accent">{trade.rr_t2}R</div>
          <div className="text-xs text-muted">R/R T2</div>
        </div>
      </div>

      <div className="mt-2 text-xs text-muted text-center">ATR(14): ${trade.atr}</div>

      {/* Options recommendation */}
      {optionsStrategy?.legs && optionsStrategy.legs.length > 0 && (
        <div className="mt-4 pt-4 border-t border-border space-y-2">
          <div className="flex items-center justify-between">
            <span className="text-xs font-semibold text-muted">Options Play</span>
            <span className="text-xs font-semibold text-white">{optionsStrategy.strategy}</span>
          </div>

          {optionsStrategy.legs.map((leg, i) => (
            <div key={i} className={`flex justify-between text-xs font-mono px-2 py-1.5 rounded ${
              leg.action === "BUY" ? "bg-green/5 text-green border border-green/20"
                                  : "bg-red/5 text-red border border-red/20"
            }`}>
              <span>{leg.action} {leg.type} ${leg.strike}</span>
              <span className="text-muted">exp {leg.exp.slice(5)} · ~${leg.mid}</span>
            </div>
          ))}

          <div className="flex justify-between text-xs text-muted pt-1">
            <span>Net debit: <span className="text-white font-mono">${optionsStrategy.net_debit}</span></span>
            {optionsStrategy.max_profit != null && (
              <span>Max profit: <span className="text-green font-mono">${optionsStrategy.max_profit}</span></span>
            )}
          </div>

          {optionsStrategy.alt && (
            <p className="text-[11px] text-muted italic">{optionsStrategy.alt}</p>
          )}
        </div>
      )}
    </div>
  );
}
