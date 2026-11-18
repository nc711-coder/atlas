"""Atlas API: serves the opportunity scores, parcels, land cover and measured results for the dashboard.

Run locally (from the project folder):  uvicorn api.main:app --reload --port 8000
Then open http://localhost:8000/docs for the interactive API docs.
"""
import json
import os
from functools import lru_cache
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import Response
from pydantic import BaseModel, Field, model_validator

DATA = Path(__file__).resolve().parent / "data"
FACTORS = ["underused", "parking", "transit", "value", "momentum", "land"]

app = FastAPI(title="Atlas API", version="1.0",
              description="Real estate opportunity scores for Larkspur Commercial Partners (fictional client).")
app.add_middleware(GZipMiddleware, minimum_size=1000)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[o.strip() for o in os.getenv("ALLOWED_ORIGINS", "http://localhost:5173").split(",") if o.strip()],
    allow_origin_regex=os.getenv("ALLOWED_ORIGIN_REGEX") or None,
    allow_methods=["GET", "POST"],
    allow_headers=["Content-Type"],
)


# ---------------------------------------------------------------- data (loaded once, kept in memory)

@lru_cache
def load(name):
    return json.loads((DATA / name).read_text())


def hex_index():
    return {f["properties"]["hex"]: f for f in load("hexes.geojson")["features"]}


def geojson(features):
    return {"type": "FeatureCollection", "features": features}


def json_response(obj):
    return Response(json.dumps(obj, separators=(",", ":")), media_type="application/json")


# ---------------------------------------------------------------- endpoints

@app.get("/api/health")
def health():
    return {"ok": True, "hexes": len(load("hexes.geojson")["features"])}


@app.get("/api/meta")
def meta():
    """Default weights, what each factor means, data sources, and the measured results."""
    return load("meta.json")


@app.get("/api/hexes")
def hexes(submarket: str | None = Query(None, description="tysons, reston_herndon or richmond_hwy")):
    """All hexagons with their score, rank, factors (0-1) and the facts behind them."""
    feats = load("hexes.geojson")["features"]
    if submarket:
        feats = [f for f in feats if f["properties"]["submarket"] == submarket]
        if not feats:
            raise HTTPException(404, f"unknown submarket '{submarket}'")
    return json_response(geojson(feats))


@app.get("/api/hexes/{hex_id}")
def hex_detail(hex_id: str):
    """One hexagon: its properties, its candidate parcels and a land-cover summary."""
    h = hex_index().get(hex_id)
    if not h:
        raise HTTPException(404, "hexagon not found")
    parcels = [f for f in load("parcels.geojson")["features"] if f["properties"]["hex"] == hex_id]
    tiles = [f["properties"] for f in load("landcover.geojson")["features"] if _tile_in_hex(f, h)]
    counts = {}
    for t in tiles:
        counts[t["pred"]] = counts.get(t["pred"], 0) + 1
    return json_response({"hex": h, "parcels": geojson(parcels),
                          "landcover_counts": counts, "landcover_tiles": len(tiles)})


@app.get("/api/parcels")
def parcels(hex: str | None = None, min_acres: float = Query(0, ge=0)):
    """Candidate parcels, optionally only those in one hexagon and/or above a minimum size."""
    feats = load("parcels.geojson")["features"]
    if hex:
        feats = [f for f in feats if f["properties"]["hex"] == hex]
    feats = [f for f in feats if (f["properties"].get("acres") or 0) >= min_acres]
    return json_response(geojson(feats))


@app.get("/api/landcover")
def landcover(submarket: str | None = None):
    """The imagery classifier's 2025 call for every 60 m tile, with confidence and review flag."""
    feats = load("landcover.geojson")["features"]
    if submarket:
        feats = [f for f in feats if f["properties"]["submarket"] == submarket]
    return json_response(geojson(feats))


class Weights(BaseModel):
    """Custom factor weights. Each 0-1; they are normalised to sum to 1."""
    underused: float = Field(0.25, ge=0, le=1)
    parking: float = Field(0.20, ge=0, le=1)
    transit: float = Field(0.20, ge=0, le=1)
    value: float = Field(0.15, ge=0, le=1)
    momentum: float = Field(0.10, ge=0, le=1)
    land: float = Field(0.10, ge=0, le=1)
    min_acres: float = Field(0, ge=0, description="only rank hexagons with at least this much candidate land")

    @model_validator(mode="after")
    def not_all_zero(self):
        if sum(getattr(self, f) for f in FACTORS) <= 0:
            raise ValueError("at least one weight must be above 0")
        return self


@app.post("/api/rank")
def rank(w: Weights, top: int = Query(20, ge=1, le=200)):
    """Re-rank hexagons with custom weights. Returns the top N with each factor's contribution."""
    total = sum(getattr(w, f) for f in FACTORS)
    norm = {f: getattr(w, f) / total for f in FACTORS}
    rows = []
    for f in load("hexes.geojson")["features"]:
        p = f["properties"]
        if (p.get("cand_land_m2") or 0) / 4046.86 < max(w.min_acres, 1e-9):
            continue
        contrib = {k: round(norm[k] * (p.get(f"f_{k}") or 0) * 100, 2) for k in FACTORS}
        rows.append({"hex": p["hex"], "submarket": p["submarket"], "score": round(sum(contrib.values()), 2),
                     "contributions": contrib, "needs_review": p["needs_review"],
                     "default_rank": p["rank"]})
    rows.sort(key=lambda r: -r["score"])
    for i, r in enumerate(rows, 1):
        r["rank"] = i
    return {"weights_used": norm, "hexes_ranked": len(rows), "top": rows[:top]}


def _tile_in_hex(tile, hexf):
    """Is the tile's centre inside the hexagon? (small point-in-polygon, no GIS library needed)"""
    ring = tile["geometry"]["coordinates"][0]
    cx = sum(p[0] for p in ring[:-1]) / (len(ring) - 1)
    cy = sum(p[1] for p in ring[:-1]) / (len(ring) - 1)
    poly = hexf["geometry"]["coordinates"][0]
    inside = False
    j = len(poly) - 1
    for i in range(len(poly)):
        xi, yi = poly[i]
        xj, yj = poly[j]
        if (yi > cy) != (yj > cy) and cx < (xj - xi) * (cy - yi) / (yj - yi) + xi:
            inside = not inside
        j = i
    return inside
