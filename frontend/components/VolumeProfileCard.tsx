interface VolumeProfile {
  poc:       number;
  vah:       number;
  val:       number;
  vol_bias:  string;
  vol_trend: string;
  vol_ratio: number;
  vol_surge: boolean;
  detail:    string;
}

const trendColor: Record<string, string> = {
  ACCUMULATING: "text-green",
  DISTRIBUTING: "text-red",
  FLAT:         "text-muted",
};
const biasColor: Record<string, string> = {
  BULLISH: "badge-bullish",
  BEARISH: "badge-bearish",
  NEUTRAL: "badge-neutral",
};

export default function VolumeProfileCard({ vp, currentPrice }: { vp: VolumeProfile; currentPrice: number }) {
  const aboveVah  = currentPrice > vp.vah;
  const belowVal  = currentPrice < vp.val;
  const inVA      = !aboveVah && !belowVal;

  return (
    <div className="card">
      <div className="flex items-center justify-between mb-3">
        <h3 className="text-sm font-semibold text-muted">Volume Profile</h3>
        <div className="flex gap-2 items-center">
          <span className={biasColor[vp.vol_bias] ?? "badge-neutral"}>{vp.vol_bias}</span>
          {vp.vol_surge && (
            <span className="text-xs px-1.5 py-0.5 rounded bg-yellow/10 text-yellow border border-yellow/20 font-mono">
              SURGE {vp.vol_ratio}x
            </span>
          )}
        </div>
      </div>

      {/* Visual VA bar */}
      <div className="relative h-24 bg-surface rounded-lg border border-border overflow-hidden mb-3">
        {/* Value Area block */}
        <div className="absolute inset-x-0 flex flex-col justify-center h-full px-3">
          <div className="text-xs text-muted flex justify-between mb-1">
            <span>VAH ${vp.vah}</span>
            <span className={trendColor[vp.vol_trend]}>{vp.vol_trend}</span>
          </div>
          <div className="h-10 bg-accent/10 border border-accent/20 rounded flex items-center justify-center relative">
            <span className="text-xs text-accent font-mono">POC ${vp.poc}</span>
            {/* Current price marker */}
            {inVA && (
              <div className="absolute right-2 w-2 h-2 rounded-full bg-white border border-accent" />
            )}
          </div>
          <div className="text-xs text-muted mt-1">VAL ${vp.val}</div>
        </div>
        {/* Position label */}
        <div className="absolute top-2 right-2">
          {aboveVah && <span className="text-xs text-green font-mono">▲ Above VA</span>}
          {belowVal  && <span className="text-xs text-red   font-mono">▼ Below VA</span>}
          {inVA      && <span className="text-xs text-accent font-mono">◈ Inside VA</span>}
        </div>
      </div>

      {/* Stats */}
      <div className="grid grid-cols-3 gap-2 text-center text-xs">
        <div><div className="font-mono text-red">${vp.vah}</div><div className="text-muted">VAH</div></div>
        <div><div className="font-mono text-accent">${vp.poc}</div><div className="text-muted">POC</div></div>
        <div><div className="font-mono text-green">${vp.val}</div><div className="text-muted">VAL</div></div>
      </div>
    </div>
  );
}
