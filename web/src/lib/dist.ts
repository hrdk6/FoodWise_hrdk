/** Client-side reading of a forecast's quantile grid (mirrors backend domain/newsvendor.py). */

export function quantileAt(levels: number[], values: number[], p: number) {
  const v = [...values].sort((a, b) => a - b);
  if (p <= levels[0]) return v[0];
  if (p >= levels[levels.length - 1]) return v[v.length - 1];
  for (let i = 1; i < levels.length; i++) {
    if (p <= levels[i]) {
      const w = (p - levels[i - 1]) / (levels[i] - levels[i - 1]);
      return v[i - 1] + w * (v[i] - v[i - 1]);
    }
  }
  return v[v.length - 1];
}

/** P(demand <= q), linear between grid points and clamped to the grid's ends. */
export function cdfAt(levels: number[], values: number[], q: number) {
  const v = [...values].sort((a, b) => a - b);
  if (q <= v[0]) return levels[0] * Math.max(0, q / Math.max(v[0], 1e-9));
  if (q >= v[v.length - 1]) return Math.min(1, levels[levels.length - 1] + (1 - levels[levels.length - 1]) * Math.min(1, (q - v[v.length - 1]) / Math.max(v[v.length - 1] - v[v.length - 2], 1e-9)));
  for (let i = 1; i < v.length; i++) {
    if (q <= v[i]) {
      const w = (q - v[i - 1]) / Math.max(v[i] - v[i - 1], 1e-9);
      return levels[i - 1] + w * (levels[i] - levels[i - 1]);
    }
  }
  return 1;
}

export function expectedWasteShortage(levels: number[], values: number[], q: number) {
  const n = 200;
  let waste = 0;
  let short = 0;
  const lo = levels[0];
  const hi = levels[levels.length - 1];
  for (let i = 0; i < n; i++) {
    const p = lo + ((hi - lo) * (i + 0.5)) / n;
    const d = quantileAt(levels, values, p);
    waste += Math.max(q - d, 0);
    short += Math.max(d - q, 0);
  }
  return { waste: waste / n, short: short / n, pShort: 1 - cdfAt(levels, values, q) };
}

export function percentileName(p: number) {
  return `${Math.round(p * 100)}th percentile`;
}
