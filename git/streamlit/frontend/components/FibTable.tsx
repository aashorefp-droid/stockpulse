interface Props {
  levels:      Record<string, number>;
  nearestFib:  string;
  currentPrice: number;
}

export default function FibTable({ levels, nearestFib, currentPrice }: Props) {
  const entries = Object.entries(levels).sort((a, b) => b[1] - a[1]);
  return (
    <div className="card">
      <h3 className="text-sm font-semibold text-muted mb-3">Fibonacci Levels (52W Range)</h3>
      <div className="space-y-1 max-h-64 overflow-y-auto pr-1">
        {entries.map(([label, val]) => {
          const isNearest = label === nearestFib;
          const above = val > currentPrice;
          return (
            <div
              key={label}
              className={`flex justify-between items-center px-2 py-1 rounded text-xs font-mono ${
                isNearest
                  ? "bg-accent/10 border border-accent/30 text-accent"
                  : above
                  ? "text-red/80"
                  : "text-green/80"
              }`}
            >
              <span>{label}</span>
              <span>${val.toFixed(2)}</span>
            </div>
          );
        })}
      </div>
    </div>
  );
}
