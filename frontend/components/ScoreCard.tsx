interface EntryGrade {
  entry_grade:  string;
  entry_label:  string;
  expected_wr:  number;
  expected_avg: number;
  grade_color:  string;
}

interface Props {
  verdict:    string;
  confidence: string;
  score:      number;
  signals:    string[];
  grade:      EntryGrade;
}

const verdictColor: Record<string, string> = {
  "BULLISH":      "text-green  border-green/30  bg-green/5",
  "LEAN BULLISH": "text-accent border-accent/30 bg-accent/5",
  "NEUTRAL":      "text-muted  border-border    bg-card",
  "LEAN BEARISH": "text-yellow border-yellow/30 bg-yellow/5",
  "BEARISH":      "text-red    border-red/30    bg-red/5",
};

export default function ScoreCard({ verdict, confidence, score, signals, grade }: Props) {
  const vc = verdictColor[verdict] ?? verdictColor["NEUTRAL"];
  const scoreBar = Math.min(Math.abs(score), 5);
  const isPositive = score >= 0;

  return (
    <div className="card space-y-4">
      <h3 className="text-sm font-semibold text-muted">Signal Score</h3>

      {/* Verdict + Grade */}
      <div className="flex items-center gap-3">
        <div className={`flex-1 border rounded-lg px-3 py-2 text-center ${vc}`}>
          <div className="font-bold text-lg">{verdict}</div>
          <div className="text-xs opacity-70">{confidence} confidence</div>
        </div>
        <div className="text-center">
          <div className="text-3xl font-bold font-mono" style={{ color: grade.grade_color }}>
            {grade.entry_grade}
          </div>
          <div className="text-xs text-muted">{grade.entry_label}</div>
        </div>
      </div>

      {/* Score bar */}
      <div>
        <div className="flex justify-between text-xs text-muted mb-1">
          <span>Score</span>
          <span className={`font-mono font-bold ${isPositive ? "text-green" : "text-red"}`}>
            {score > 0 ? "+" : ""}{score}
          </span>
        </div>
        <div className="h-2 bg-surface rounded-full overflow-hidden">
          <div
            className={`h-full rounded-full transition-all ${isPositive ? "bg-green" : "bg-red"}`}
            style={{ width: `${(scoreBar / 5) * 100}%` }}
          />
        </div>
      </div>

      {/* Win-rate expectation */}
      <div className="flex justify-between text-xs border-t border-border pt-2">
        <span className="text-muted">Expected win rate</span>
        <span className="font-mono font-semibold text-white">{grade.expected_wr}%</span>
      </div>
      <div className="flex justify-between text-xs">
        <span className="text-muted">Avg P&L (backtest)</span>
        <span className={`font-mono font-semibold ${grade.expected_avg >= 0 ? "text-green" : "text-red"}`}>
          {grade.expected_avg >= 0 ? "+" : ""}{grade.expected_avg}%
        </span>
      </div>

      {/* Signal breakdown */}
      {signals.length > 0 && (
        <div className="border-t border-border pt-2 space-y-1">
          <p className="text-xs text-muted font-semibold">Signal Breakdown</p>
          <div className="flex flex-wrap gap-1">
            {signals.map((s, i) => {
              const isPos = s.includes(":Bull") || s.includes("Buyer") || s.includes("Long") || s.includes("DIST+") || s.includes("Surge") || s.includes("Weinstein:Bull") || s.includes("FVG:Bull") || s.includes("Weekly:Bull");
              const isNeg = s.includes(":Bear") || s.includes("Seller") || s.includes("Short") || s.includes("DIST-") || s.includes("Weinstein:Bear") || s.includes("FVG:Bear") || s.includes("Weekly:Bear");
              return (
                <span key={i} className={`text-xs px-1.5 py-0.5 rounded border font-mono ${
                  isPos ? "bg-green/10 text-green border-green/20" :
                  isNeg ? "bg-red/10 text-red border-red/20" :
                  "bg-card text-muted border-border"
                }`}>
                  {s}
                </span>
              );
            })}
          </div>
        </div>
      )}
    </div>
  );
}
