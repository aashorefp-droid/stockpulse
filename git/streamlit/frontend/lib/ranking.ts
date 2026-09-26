/**
 * frontend/lib/ranking.ts
 * Client-side swing trade ranking engine matching backend/services/best_pick.py
 * Provides instantaneous, 100% reliable 3-paradigm Best Picks (Strength, Emerging, Weakness)
 * both as an offline fallback and instant evaluator.
 */

export interface RankedItem {
  ticker: string;
  sector: string;
  price: number | null;
  verdict: string;
  btd: string;
  btd_zone: string;
  dist_from_high: number;
  sma30_slope: number;
  valuation_upside: number;
  swing_entry: number | null;
  swing_stop: number | null;
  swing_t1: number | null;
  swing_reward_pct: number;
  swing_risk_pct?: number;
  swing_rr: number;
  tightness_rating?: string;
  setup_status?: string;
  category?: "STRENGTH" | "EMERGING" | "WEAKNESS";
  category_badge?: string;
  category_title?: string;
  category_tagline?: string;
  score: number;
}

export interface BestPickResponse {
  best_picks?: {
    strength: RankedItem | null;
    emerging: RankedItem | null;
    weakness: RankedItem | null;
  };
  best_pick: RankedItem | null;
  ranked: RankedItem[];
  total_scanned: number;
  strict_passed_count: number;
  is_strict: boolean;
}

export function computeClientSideBestPicks(items: any[]): BestPickResponse {
  const valid = (items || []).filter(r => !r.error && r.ticker);
  if (valid.length === 0) {
    return {
      best_pick: null,
      best_picks: { strength: null, emerging: null, weakness: null },
      ranked: [],
      total_scanned: items?.length || 0,
      strict_passed_count: 0,
      is_strict: false,
    };
  }

  // Pre-process and score each candidate
  const processed = valid.map((r: any) => {
    const price = typeof r.price === "number" ? r.price : parseFloat(r.price) || 0;
    const dh = typeof r.dist_from_high === "number" ? r.dist_from_high : 10.0;
    const slope = typeof r.sma30_slope === "number" ? r.sma30_slope : 0.0;
    const entry = r.entry != null ? Number(r.entry) : price;
    const stop = r.stop_loss != null ? Number(r.stop_loss) : (price > 0 ? price * 0.95 : 0);
    const t1 = r.target1 != null ? Number(r.target1) : (price > 0 ? price * 1.10 : 0);

    const risk = r.risk_pct != null
      ? Number(r.risk_pct)
      : (entry > 0 ? Math.round(Math.abs(entry - stop) / entry * 100 * 10) / 10 : 5.0);

    const reward = entry > 0
      ? Math.round(Math.abs(t1 - entry) / entry * 100 * 10) / 10
      : 10.0;

    const rr = r.rr_t1 != null
      ? Number(r.rr_t1)
      : (risk > 0 ? Math.round((reward / risk) * 100) / 100 : 1.0);

    const grade = (r.entry_grade || "").toUpperCase();
    const status = (r.entry_status || "").toUpperCase();
    const stage = (r.stage2_status || "").toUpperCase();
    const distSma = r.dist_from_sma30 != null ? Number(r.dist_from_sma30) : 0.0;

    let btd = "EXTENDED";
    let btd_zone = "Late Stage";
    if (["S", "A"].includes(grade) || status === "ENTER") {
      btd = "TRIGGER";
      btd_zone = "Reclaimed 20 EMA / Breakout";
    } else if (stage === "FRESH" || (distSma >= -2.0 && distSma <= 8.5)) {
      btd = "ARMED";
      btd_zone = "Dip 20-50 EMA";
    } else if (distSma < -2.0) {
      btd = "ARMED-DEEP";
      btd_zone = "Deep Dip 50-200 EMA";
    }

    const verdict = (r.verdict || "NEUTRAL").toUpperCase();
    const isBullish = verdict.includes("BULLISH");

    // A. Strength Score
    let s_score = 25.0;
    if (dh <= 2.0) s_score += 35.0;
    else if (dh <= 5.0) s_score += 25.0;
    else if (dh <= 8.0) s_score += 18.0;
    else if (dh <= 12.0) s_score += 10.0;
    else s_score -= 15.0;

    if (verdict === "BULLISH") s_score += 25.0;
    else if (verdict === "LEAN BULLISH") s_score += 15.0;
    else s_score -= 30.0;

    if (slope >= 3.0) s_score += 15.0;
    else if (slope > 0.0) s_score += 8.0;
    else s_score -= 15.0;

    if (r.vol_surge) s_score += 15.0;
    if ((r.breakout_score || 0) >= 2) s_score += 10.0;
    s_score = Math.max(0, Math.min(100, Math.round(s_score * 10) / 10));

    // B. Emerging Score
    let e_score = 20.0;
    if (risk <= 3.5) e_score += 30.0;
    else if (risk <= 5.5) e_score += 22.0;
    else if (risk <= 7.0) e_score += 15.0;
    else if (risk <= 8.5) e_score += 8.0;
    else e_score -= 20.0;

    if (btd === "TRIGGER") e_score += 25.0;
    else if (btd === "ARMED") e_score += 12.0;

    if (dh <= 3.0) e_score += 20.0;
    else if (dh <= 7.0) e_score += 15.0;
    else if (dh <= 12.0) e_score += 10.0;

    if (r.vol_surge) e_score += 15.0;
    if (slope >= 3.0) e_score += 15.0;
    else if (slope > 0.0) e_score += 8.0;
    e_score = Math.max(0, Math.min(100, Math.round(e_score * 10) / 10));

    // C. Weakness Score
    let w_score = 25.0;
    if (dh <= 2.5) w_score -= 30.0;
    else if (dh >= 10.0) w_score += 20.0;
    else if (dh >= 5.0) w_score += 12.0;

    if (rr >= 3.0) w_score += 30.0;
    else if (rr >= 2.2) w_score += 20.0;
    else if (rr >= 1.8) w_score += 12.0;

    const upside = r.target_upside != null ? Number(r.target_upside) : 0.0;
    if (upside >= 25.0) w_score += 25.0;
    else if (upside >= 15.0) w_score += 18.0;
    else if (upside >= 8.0) w_score += 10.0;

    if (btd === "ARMED-DEEP") w_score += 20.0;
    else if (btd === "ARMED") w_score += 15.0;
    w_score = Math.max(0, Math.min(100, Math.round(w_score * 10) / 10));

    // Classification
    let category: "STRENGTH" | "EMERGING" | "WEAKNESS" = "EMERGING";
    if (btd === "ARMED-DEEP" || (dh >= 6.0 && w_score >= 65.0 && (btd === "ARMED" || btd === "ARMED-DEEP"))) {
      category = "WEAKNESS";
    } else if (dh <= 3.5 && s_score >= 65.0) {
      category = "STRENGTH";
    } else if (btd === "TRIGGER" && e_score >= 65.0) {
      category = "EMERGING";
    } else if (e_score >= s_score && e_score >= w_score) {
      category = "EMERGING";
    } else if (s_score >= w_score) {
      category = "STRENGTH";
    } else {
      category = "WEAKNESS";
    }

    const assignedScore = category === "STRENGTH" ? s_score : category === "EMERGING" ? e_score : w_score;

    let tightness = "Solid Base";
    if (risk <= 4.0) tightness = "⚡ Tight Coil (VCP)";
    else if (risk <= 6.5) tightness = "🎯 Constructive Base";
    else if (risk <= 8.5) tightness = "Solid Base";
    else tightness = "Wide / Volatile";

    let setup = "Consolidating";
    if (btd === "TRIGGER") setup = "🚀 Emerging Today";
    else if (btd === "ARMED") setup = "⏳ Coiled at Pivot";
    else if (btd === "ARMED-DEEP") setup = "🛡️ Dip at Support";

    const item: RankedItem = {
      ticker: String(r.ticker).toUpperCase(),
      sector: r.sector || "Equities",
      price: price > 0 ? price : null,
      verdict: r.verdict || "NEUTRAL",
      btd,
      btd_zone,
      dist_from_high: Math.round(dh * 10) / 10,
      sma30_slope: Math.round(slope * 100) / 100,
      valuation_upside: Math.round(upside * 10) / 10,
      swing_entry: entry > 0 ? entry : null,
      swing_stop: stop > 0 ? stop : null,
      swing_t1: t1 > 0 ? t1 : null,
      swing_reward_pct: reward,
      swing_risk_pct: risk,
      swing_rr: rr,
      tightness_rating: tightness,
      setup_status: setup,
      category,
      category_badge: category === "STRENGTH" ? "⚡ Strength" : category === "EMERGING" ? "🚀 Emerging" : "🛡️ Weakness",
      category_title: category === "STRENGTH" ? "BUY THE STRENGTH" : category === "EMERGING" ? "BUY THE EMERGING" : "BUY THE WEAKNESS",
      category_tagline: category === "STRENGTH" ? "⚡ 52W High Momentum Leader" : category === "EMERGING" ? "🚀 Fresh Breakout Emerging from Tight Base" : "🛡️ High R/R Dip at Key Support",
      score: assignedScore,
    };

    return {
      raw: r,
      item,
      s_score,
      e_score,
      w_score,
      isBullish,
      btd,
      dh,
      risk,
      rr,
      slope,
    };
  });

  // Pick candidates for each category
  // 1. Strength
  let poolStrength = processed
    .filter(p => p.isBullish && p.slope > 0 && p.dh <= 12.0 && p.btd !== "ARMED-DEEP")
    .sort((a, b) => b.s_score - a.s_score);
  if (poolStrength.length === 0) {
    poolStrength = processed.filter(p => p.isBullish).sort((a, b) => b.s_score - a.s_score);
  }
  if (poolStrength.length === 0) {
    poolStrength = [...processed].sort((a, b) => b.s_score - a.s_score);
  }
  const bestStrength = poolStrength.length > 0 ? { ...poolStrength[0].item, category: "STRENGTH" as const, score: poolStrength[0].s_score } : null;

  // 2. Emerging
  let poolEmerging = processed
    .filter(p => p.isBullish && p.slope > 0 && p.dh <= 15.0 && p.risk <= 8.5 && ["TRIGGER", "ARMED"].includes(p.btd) && p.rr >= 1.8)
    .sort((a, b) => b.e_score - a.e_score);
  if (poolEmerging.length === 0) {
    poolEmerging = processed.filter(p => p.isBullish && p.dh <= 18.0).sort((a, b) => b.e_score - a.e_score);
  }
  if (poolEmerging.length === 0) {
    poolEmerging = [...processed].sort((a, b) => b.e_score - a.e_score);
  }
  const candEmerging = poolEmerging.find(p => p.item.ticker !== bestStrength?.ticker) || poolEmerging[0];
  const bestEmerging = candEmerging ? { ...candEmerging.item, category: "EMERGING" as const, score: candEmerging.e_score } : null;

  // 3. Weakness
  let poolWeakness = processed
    .filter(p => p.rr >= 1.8 && (["ARMED-DEEP", "ARMED"].includes(p.btd) || p.dh >= 4.0) && p.dh >= 2.5)
    .sort((a, b) => b.w_score - a.w_score);
  if (poolWeakness.length === 0) {
    poolWeakness = processed.filter(p => p.rr >= 1.8 && p.dh >= 2.0).sort((a, b) => b.w_score - a.w_score);
  }
  if (poolWeakness.length === 0) {
    poolWeakness = [...processed].sort((a, b) => b.w_score - a.w_score);
  }
  const taken = new Set([bestStrength?.ticker, bestEmerging?.ticker].filter(Boolean));
  const candWeakness = poolWeakness.find(p => !taken.has(p.item.ticker)) || poolWeakness[0];
  const bestWeakness = candWeakness ? { ...candWeakness.item, category: "WEAKNESS" as const, score: candWeakness.w_score } : null;

  // Build full ranked records list
  const ranked = processed.map(p => p.item).sort((a, b) => b.score - a.score);

  const primary = bestEmerging || bestStrength || bestWeakness;
  const strictCount = ranked.filter(r => r.score >= 70.0).length;

  return {
    best_picks: {
      strength: bestStrength,
      emerging: bestEmerging,
      weakness: bestWeakness,
    },
    best_pick: primary,
    ranked,
    total_scanned: items.length,
    strict_passed_count: strictCount,
    is_strict: strictCount > 0,
  };
}
