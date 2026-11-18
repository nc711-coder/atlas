// Small client for the Atlas API (Cloud Run).
const BASE = (import.meta.env.VITE_API_URL || "").replace(/\/$/, "");

async function get(path) {
  const r = await fetch(`${BASE}${path}`);
  if (!r.ok) throw new Error(`${path}: ${r.status}`);
  return r.json();
}

export const api = {
  hexes: () => get("/api/hexes"),
  hex: (id) => get(`/api/hexes/${encodeURIComponent(id)}`),
  meta: () => get("/api/meta"),
};
