export const acres = (a) => (a == null || isNaN(a) ? "–" : `${a < 10 ? a.toFixed(1) : Math.round(a)} ac`);
export const pct = (x) => (x == null || isNaN(x) ? "–" : `${Math.round(x * 100)}%`);
export const usd = (x) =>
  x == null || isNaN(x) ? "–" : x >= 1e6 ? `$${(x / 1e6).toFixed(1)}M` : `$${Math.round(x / 1e3)}K`;
export const meters = (m) => (m == null || isNaN(m) ? "–" : m >= 1000 ? `${(m / 1000).toFixed(1)} km` : `${Math.round(m)} m`);
export const titleCase = (s) => (s || "").toLowerCase().replace(/\b\w/g, (c) => c.toUpperCase());
export const SUBMARKET_LABEL = { tysons: "Tysons", reston_herndon: "Reston–Herndon", richmond_hwy: "Richmond Hwy" };
