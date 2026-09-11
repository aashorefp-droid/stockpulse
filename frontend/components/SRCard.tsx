interface Props {
  support:    number[];
  resistance: number[];
  currentPrice: number;
}

export default function SRCard({ support, resistance, currentPrice }: Props) {
  return (
    <div className="card">
      <h3 className="text-sm font-semibold text-muted mb-3">Support & Resistance</h3>
      <div className="space-y-1">
        {resistance.map((v) => (
          <div key={v} className="flex justify-between text-xs font-mono px-2 py-1 rounded bg-red/5 text-red border border-red/20">
            <span>Resistance</span>
            <span>${v.toFixed(2)}</span>
          </div>
        ))}
        <div className="flex justify-between text-xs font-mono px-2 py-1 rounded bg-accent/10 text-accent border border-accent/30 font-bold">
          <span>Current</span>
          <span>${currentPrice.toFixed(2)}</span>
        </div>
        {support.map((v) => (
          <div key={v} className="flex justify-between text-xs font-mono px-2 py-1 rounded bg-green/5 text-green border border-green/20">
            <span>Support</span>
            <span>${v.toFixed(2)}</span>
          </div>
        ))}
      </div>
    </div>
  );
}
