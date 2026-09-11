import clsx from "clsx";

interface Props {
  label: string;
  bias:  string;
}

function biasBadge(b: string) {
  if (b === "BULLISH")   return "badge-bullish";
  if (b === "BEARISH")   return "badge-bearish";
  if (b === "CONFIRMED") return "badge-confirmed";
  if (b === "DIVERGED")  return "badge-diverged";
  return "badge-neutral";
}

export function BiasBadge({ bias }: { bias: string }) {
  return <span className={biasBadge(bias)}>{bias}</span>;
}

export default function BiasCard({ label, bias }: Props) {
  return (
    <div className="card flex items-center justify-between gap-3">
      <span className="text-muted text-sm">{label}</span>
      <BiasBadge bias={bias} />
    </div>
  );
}
