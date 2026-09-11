import type { Signal } from "@/lib/api";
import clsx from "clsx";

const rankColor: Record<number, string> = {
  1: "border-green  bg-green/5  text-green",
  2: "border-accent bg-accent/5 text-accent",
  3: "border-yellow bg-yellow/5 text-yellow",
  4: "border-muted  bg-muted/5  text-muted",
  5: "border-border bg-card     text-muted",
};

export default function SignalBanner({ signal, direction }: { signal: Signal; direction: string }) {
  const cls = rankColor[signal.rank] ?? rankColor[5];
  const dirLabel = direction === "SHORT" ? "▼ SHORT" : "▲ LONG";
  const dirColor = direction === "SHORT" ? "text-red" : "text-green";
  return (
    <div className={clsx("border rounded-xl p-4 flex flex-col gap-1", cls)}>
      <div className="flex items-center gap-3">
        <span className={clsx("text-xs font-mono font-bold px-2 py-0.5 rounded border", dirColor,
          direction === "SHORT" ? "border-red/30 bg-red/10" : "border-green/30 bg-green/10"
        )}>
          {dirLabel}
        </span>
        <span className="text-xs font-mono opacity-60">Rank {signal.rank}</span>
        <span className="font-bold text-lg">{signal.signal}</span>
        <span className="ml-auto text-xs font-mono opacity-50">{signal.key}</span>
      </div>
      <p className="text-sm opacity-80">{signal.action}</p>
    </div>
  );
}
