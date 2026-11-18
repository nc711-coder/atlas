"""Step 8a: export the results into small files the API serves (api/data/).

Run from the project folder:  python pipeline/step8_export_web_data.py

Writes (all public county data + our results, small enough to commit to GitHub):
  api/data/hexes.geojson       196 hexagons: score, rank, the six factors, the facts behind them, review flag
  api/data/parcels.geojson     candidate parcels (simplified outlines) with the facts shown in the dashboard
  api/data/landcover.geojson   the classifier's 2025 call for every 60 m tile (+ confidence, review flag)
  api/data/meta.json           default weights, factor descriptions, data sources, and the MEASURED results,
                               copied by code from the step reports (never typed by hand)
"""
import json

import geopandas as gpd
import pandas as pd

from config import DATA, METRIC_CRS, ROOT

OUT = ROOT / "api" / "data"


def to_geojson(gdf, path, cols, simplify_m=None, decimals=6):
    g = gdf[cols + ["geometry"]].copy()
    if simplify_m:
        g["geometry"] = g.to_crs(METRIC_CRS).geometry.simplify(simplify_m).to_crs(4326).values
    g = g.to_crs(4326)
    for c in g.columns:
        if c != "geometry" and str(g[c].dtype).startswith("float"):
            g[c] = g[c].round(4)
    path.write_text(g.to_json(drop_id=True, to_wgs84=True, show_bbox=False))
    # trim coordinate precision (~10 cm) to keep files small
    data = json.loads(path.read_text())
    for f in data["features"]:
        f["geometry"] = json.loads(json.dumps(f["geometry"]), parse_float=lambda x: round(float(x), decimals))
    path.write_text(json.dumps(data, separators=(",", ":")))
    return len(g)


def read_json(path):
    return json.loads(path.read_text()) if path.exists() else None


def main():
    OUT.mkdir(parents=True, exist_ok=True)

    hexes = gpd.read_parquet(DATA / "step7" / "hexes_scored.parquet")
    hex_cols = ["hex", "submarket", "score", "rank", "needs_review", "cand_parcels", "cand_land_m2", "est_far",
                "low_value_share", "recent_rezoning_parcels", "footprints_2024_update", "imagery_parking_share",
                "imagery_lowconf_share", "tiles", "record_check_parcels", "protected_m2", "metro_station", "metro_m",
                "f_underused", "f_parking", "f_transit", "f_value", "f_momentum", "f_land"]
    n_hex = to_geojson(hexes, OUT / "hexes.geojson", hex_cols)

    parcels = gpd.read_parquet(DATA / "step7" / "parcels_scored.parquet")
    cand = parcels[parcels["candidate"]].copy()
    cand["acres"] = (cand["land_m2"] / 4046.86).round(2)
    if "last_sale_date" in cand:
        cand["last_sale_date"] = pd.to_datetime(cand["last_sale_date"]).dt.strftime("%Y-%m-%d")
    parcel_cols = ["PIN", "hex", "submarket", "address", "acres", "LUC_DESC", "zone_map", "APRLAND", "APRBLDG",
                   "bldg_to_land", "est_far", "bldg_coverage", "parking_share", "protected_share", "record_check", "rezoning_cases",
                   "last_case_year", "last_sale_date", "last_sale_price", "metro_station", "metro_dist_m"]
    parcel_cols = [c for c in parcel_cols if c in cand.columns]
    n_par = to_geojson(cand, OUT / "parcels.geojson", parcel_cols, simplify_m=1.0)

    tiles = gpd.read_parquet(DATA / "step3" / "tiles.parquet")
    preds = pd.read_parquet(DATA / "step5" / "predictions_2025.parquet")
    lc = tiles.merge(preds[["tile_id", "pred", "confidence", "needs_review"]], on="tile_id")
    n_tiles = to_geojson(lc, OUT / "landcover.geojson", ["tile_id", "submarket", "pred", "confidence", "needs_review"], decimals=6)

    # ---- measured results, copied from the reports
    s2 = read_json(DATA / "step2" / "step2_report.json") or {}
    s2c = read_json(DATA / "step2" / "step2c_report.json") or {}
    s3 = read_json(DATA / "step3" / "step3_report.json") or {}
    r1 = read_json(DATA / "step5" / "test_report.json")
    r2 = read_json(DATA / "step5" / "test_report_r2.json")
    ho = read_json(DATA / "step5" / "heldout_report.json")
    ch = read_json(DATA / "step6" / "change_report.json")
    cnb = read_json(DATA / "step6" / "county_new_building_report.json")
    s7 = read_json(DATA / "step7" / "step7_report.json") or {}
    s7a = read_json(DATA / "step7" / "step7a_report.json")

    def pick(rep, *keys):
        return {k: rep.get(k) for k in keys} if rep else None

    def model_summary(rep):
        if not rep:
            return None
        return {"tiles": rep["labelled_tiles"], "classes": rep.get("classes"),
                "model_accuracy": rep["model"]["accuracy"], "model_accuracy_95ci": rep["model"]["accuracy_95ci"],
                "model_macro_f1": rep["model"]["macro_f1"],
                "rules_accuracy": rep["baseline_rules_v2_with_roads"]["accuracy"],
                "rules_accuracy_95ci": rep["baseline_rules_v2_with_roads"]["accuracy_95ci"],
                "flagged_for_review_share": rep["flagged_for_review_share"],
                "accuracy_confident": rep["accuracy_confident"], "accuracy_flagged": rep["accuracy_flagged"]}

    meta = {
        "client": "Larkspur Commercial Partners (fictional)",
        "weights_default": s7.get("weights"),
        "factors": {
            "underused": "Low estimated floor-area ratio on candidate parcels (county footprints x heights / land area)",
            "parking": "Share of the hexagon the imagery classifier calls surface parking (2025 NAIP, confident calls only)",
            "transit": "Distance from hexagon centre to the nearest Metro station (0 m = 1, 1,600 m or more = 0)",
            "value": "Share of candidate land where building value is under half the land value (tax records)",
            "momentum": "Parcels in a 2015+ rezoning case, plus footprints added or re-mapped in the county's 2024 update",
            "land": "Total candidate land area",
        },
        "data": {
            "imagery": "USDA NAIP via Microsoft Planetary Computer: Aug 2025 (0.3 m) and Oct-Nov 2018 (0.6 m)",
            "county": "Fairfax County GIS open data: parcels, zoning, rezoning cases, tax records, buildings, parking, roads, "
                      "Metro, Resource Protection Areas and floodplains",
        },
        "measured": {
            "parcel_join": pick(s2, "parcels_total", "parcels_matched_assessed_values", "parcels_matched_assessed_values_pct"),
            "shapes_repaired": pick(s2c, "invalid_parcel_shapes_repaired", "invalid_zoning_shapes_repaired"),
            "study_area": pick(s3, "parcels_inside_boundaries", "candidate_parcels", "candidate_parcels_with_record_check_flag",
                               "tysons_area_km2", "reston_herndon_area_km2", "richmond_hwy_area_km2"),
            "landcover_test_round1": model_summary(r1),
            "landcover_test_round2": model_summary(r2),
            "landcover_heldout_final": model_summary(ho),
            "imagery_change_detection_not_used": pick(ch, "pairs_labelled", "precision_flagged_really_changed",
                                                      "miss_rate_unflagged_actually_changed", "estimated_recall_all_tiles"),
            "county_new_buildings_check": pick(cnb, "check_pairs", "check_raw_counts", "precision_weighted",
                                               "recall_of_all_changes_weighted"),
            "score": pick(s7, "hexes", "hexes_with_candidate_land", "hexes_needs_review", "protected_land_used"),
            "protected_land": pick(s7a, "candidate_parcels", "candidates_with_any_protected_land",
                                   "candidates_dropped_80pct_protected", "protected_candidate_acres_removed"),
        },
    }
    (OUT / "meta.json").write_text(json.dumps(meta, indent=2, default=str))

    sizes = {p.name: f"{p.stat().st_size / 1e6:.2f} MB" for p in OUT.iterdir()}
    print(f"Exported {n_hex} hexagons, {n_par} candidate parcels, {n_tiles} land-cover tiles to {OUT}")
    print("File sizes:", sizes)


if __name__ == "__main__":
    main()
