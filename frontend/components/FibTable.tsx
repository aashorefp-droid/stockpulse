interface Props {
  levels:      Record<string, number>;
  nearestFib:  string;
  currentPrice: number;
}

export function getFibDescription(label: string): string {
  switch (label) {
    case "R 0.0%":
      return "52W High";
    case "R 23.6%":
      return "Shallow Retrace";
    case "R 38.2%":
      return "Key Retrace";
    case "R 50.0%":
      return "52W Midpoint";
    case "R 61.8%":
      return "Golden Ratio";
    case "R 78.6%":
      return "Deep Retrace";
    case "R 100.0%":
      return "52W Low";
    case "E 127.2%":
      return "Breakout Ext 1";
    case "E 141.4%":
      return "Breakout Ext 2";
    case "E 161.8%":
      return "Golden Ext";
    case "E 200.0%":
      return "2x Expansion";
    case "E 261.8%":
      return "Max Ext";
    case "N -23.6%":
      return "Breakdown Ext 1";
    case "N -38.2%":
      return "Breakdown Ext 2";
    case "N -50.0%":
      return "Breakdown Midpoint";
    case "N -61.8%":
      return "Golden Breakdown";
    case "N -100.0%":
      return "100% Breakdown Ext";
    default:
      if (label.startsWith("E ")) return "Expansion Target";
      if (label.startsWith("N -")) return "Breakdown Target";
      return "Retracement Level";
  }
}

export default function FibTable({ levels, nearestFib, currentPrice }: Props) {
  const entries = Object.entries(levels || {}).sort((a, b) => b[1] - a[1]);
  const hi52 = levels?.["R 0.0%"];
  const lo52 = levels?.["R 100.0%"];

  return (
    <div className="card">
      <div className="flex items-center justify-between mb-3">
        <h3 className="text-sm font-semibold text-muted">Fibonacci Levels (52W Range)</h3>
        {hi52 != null && lo52 != null && (
          <span className="text-[11px] font-mono text-slate-400 bg-surface/80 px-2 py-0.5 rounded border border-border/40">
            ${lo52.toFixed(2)} – ${hi52.toFixed(2)}
          </span>
        )}
      </div>
      <div className="space-y-1.5 max-h-64 overflow-y-auto pr-1">
        {entries.map(([label, val]) => {
          const isNearest = label === nearestFib;
          const above = val > currentPrice;
          const desc = getFibDescription(label);
          const isAnchor = label === "R 0.0%" || label === "R 100.0%";
          const isGolden = label === "R 50.0%" || label === "R 61.8%";

          return (
            <div
              key={label}
              className={`flex justify-between items-center px-2 py-1.5 rounded text-xs font-mono transition-all ${
                isNearest
                  ? "bg-accent/15 border border-accent/40 text-accent font-semibold shadow-sm"
                  : above
                  ? "text-red/90 hover:bg-white/[0.02]"
                  : "text-green/90 hover:bg-white/[0.02]"
              }`}
            >
              <div className="flex items-center gap-1.5 flex-wrap">
                <span className="font-bold">{label}</span>
                <span
                  className={`text-[10px] font-sans px-1.5 py-0.2 rounded border uppercase tracking-wider ${
                    isAnchor
                      ? "bg-purple-500/20 text-purple-300 border-purple-500/30 font-bold"
                      : isGolden
                      ? "bg-amber-500/20 text-amber-300 border-amber-500/30 font-semibold"
                      : "bg-surface/80 text-slate-400 border-border/40"
                  }`}
                >
                  {desc}
                </span>
                {isNearest && (
                  <span className="text-[9px] font-sans font-extrabold px-1.5 py-0.2 rounded bg-accent text-black uppercase tracking-wider">
                    Nearest
                  </span>
                )}
              </div>
              <span className="font-bold font-mono">${val.toFixed(2)}</span>
            </div>
          );
        })}
      </div>
    </div>
  );
}
