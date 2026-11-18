// Small client for the Atlas API (Cloud Run).
const BASE = (import.meta.env.VITE_API_URL || "").replace(/\/$/, "");

async function get(path) {
  const r = await fetch(`${BASE}${path}`);
  if (!r.ok) throw new Error(`${path}: ${r.status}`);
  return r.json();
}

async function post(path, body) {
  const r = await fetch(`${BASE}${path}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!r.ok) throw new Error(`${path}: ${r.status}`);
  return r.json();
}

export const api = {
  rank: (weights) => post("/api/rank?top=200", weights),
  hexes: () => get("/api/hexes"),
  hex: (id) => get(`/api/hexes/${encodeURIComponent(id)}`),
  meta: () => get("/api/meta"),
};
