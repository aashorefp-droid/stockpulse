const API_BASE = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

export interface OhlcBar {
  time: string;
  open: number;
  high: number;
  low: number;
  close: number;
  volume: number;
}

export interface MultiframeBias {
  bias_short:    string;
  bias_long:     string;
  tf_short:      string;
  tf_long:       string;
  alignment:     string;
  conclusion:    string;
  color:         string;
  current_price: number;
}

export interface Signal {
  rank:   number;
  signal: string;
  action: string;
  key:    string;
}

export interface Fundamentals {
  name:          string;
  sector:        string;
  industry:      string;
  market_cap:    string;
  pe_ratio:      number | string;
  forward_pe:    number | string;
  eps:           number | string;
  profit_margin: number;
  dividend_yield:number;
  "52w_high":    number | string;
  "52w_low":     number | string;
  avg_volume:    number | string;
  beta:          number | string;
  description:   string;
}

export interface StockAnalysis {
  ticker:        string;
  current_price: number;
  direction:     string;
  chart_data:    OhlcBar[];
  bias: {
    weekly:     string;
    daily:      string;
    h4:         string;
    multiframe: MultiframeBias | null;
  };
  signal:        Signal;
  fib_levels:    Record<string, number>;
  nearest_fib:   string;
  support_resistance: {
    support:    number[];
    resistance: number[];
  };
  weekly_fib_rsi: {
    weekly_fib: string;
    rsi_4h:     number | string;
  };
  fundamentals:  Fundamentals;
  stock_verdict?: StockVerdict | null;
}

export interface StockVerdict {
  ticker: string;
  error?: string;
  verdict: "GROWTH" | "VALUE" | "AVOID" | "PEAK" | null;
  signal: string | null;
  levels: {
    Heavy?: string;
    Buy?: string;
    Fair?: string;
    Exit?: string;
  };
  meters: {
    VALUE?: number;
    GROWTH?: number;
    QUALITY?: number;
    TECHNICAL?: number;
  };
  trust_score: number | null;
  trust_label: string | null;
  pros: string[];
  cons: string[];
  earnings_quality: string[];
  tech_summary?: string;
  peers?: Array<{
    ticker: string;
    verdict?: string;
    score?: string;
  }>;
  source_url?: string;
  fetched_at?: string;
}

export async function fetchAnalysis(ticker: string): Promise<any> {
  const res = await fetch(`${API_BASE}/api/analysis/${ticker}`, {
    next: { revalidate: 60 },
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    throw new Error(err.detail || `Failed to fetch ${ticker}`);
  }
  return res.json();
}

export async function fetchStockVerdict(ticker: string): Promise<StockVerdict | null> {
  try {
    const res = await fetch(`${API_BASE}/api/analysis/verdict/${ticker}`, {
      next: { revalidate: 3600 },
    });
    if (!res.ok) return null;
    return res.json();
  } catch {
    return null;
  }
}

