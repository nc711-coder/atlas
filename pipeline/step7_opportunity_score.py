"""Step 7: the opportunity score per hexagon (plus the parcel facts behind it).

Run from the project folder:  python pipeline/step7_opportunity_score.py

Six factors per hexagon, each scaled 0-1 by its rank among hexagons that have candidate land
(fixed before seeing results; weights are only defaults - the dashboard lets the user change them):
  underused  25%  low estimated floor-area ratio on candidate parcels (land-area weighted)
  parking    20%  share of the hexagon's tiles the CLASSIFIER calls parking (2025 imagery, step 5)
  transit    20%  distance from hexagon centre to the nearest Metro station (0 m -> 1, 1,600 m or more -> 0)
  value      15%  share of candidate land where building value < 50% of land value,
                  leaving out "check the record" parcels (tax value says no building, footprints say otherwise)
  momentum   10%  rezoning cases with a case year of 2015+ covering candidate parcels, plus footprints added or
                  re-mapped in the county's 2024 update (step 6b) - county mapping dates, not construction dates
  land       10%  total candidate land area
Protected land (step 7a, if run): parcels >= 80% inside stream buffers/floodplains are not candidates, and
only the buildable part of the rest counts as land (land, underused and value factors are weighted by it).
A hexagon is flagged NEEDS_REVIEW if > 40% of its tiles had low-confidence imagery calls, or if 3+ of its
candidate parcels carry the "check the record" flag.

Writes to <DATA>/step7/: hexes_scored.parquet, parcels_scored.parquet, step7_report.json
"""
import json

import geopandas as gpd
import h3
import numpy as np
import pandas as pd

from config import DATA, METRIC_CRS

OUT = DATA / "step7"
WEIGHTS = {"underused": 0.25, "parking": 0.20, "transit": 0.20, "value": 0.15, "momentum": 0.10, "land": 0.10}
TRANSIT_ZERO_M = 1600
MOMENTUM_SINCE_YEAR = 2015
REVIEW_LOWCONF_SHARE = 0.40
REVIEW_RECORD_CHECKS = 3
HEX_RES = 9


def rank01(s):
    """Rank-scale to 0-1: the lowest value -> 0, the highest -> 1. Ties share the LOWEST rank of their group,
    so e.g. many hexagons with 0% parking all get 0 (not a middling 0.5). Missing stays missing."""
    r = s.rank(method="min")
    n = s.notna().sum()
    return (r - 1) / (n - 1) if n > 1 else r * 0


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    report = {"weights": WEIGHTS}

    hexes = gpd.read_parquet(DATA / "step3" / "hexes.parquet")
    parcels = gpd.read_parquet(DATA / "step3" / "parcels.parquet")
    tiles = gpd.read_parquet(DATA / "step3" / "tiles.parquet")
    preds = pd.read_parquet(DATA / "step5" / "predictions_2025.parquet")
    newb = gpd.read_parquet(DATA / "step6" / "new_buildings.parquet")
    metro = gpd.read_parquet(DATA / "step2" / "raw" / "Metrorail_Stations.parquet")

    pm = parcels.to_crs(METRIC_CRS)
    parcels["land_m2"] = pm.geometry.area.round(0)
    env_path = OUT / "parcels_env.parquet"
    if env_path.exists():
        parcels = parcels.merge(pd.read_parquet(env_path), on="PIN", how="left")
        report["protected_land_used"] = True
    else:
        print("!! step 7a not run: protected stream valleys/floodplains are NOT excluded")
        report["protected_land_used"] = False
    if "protected_share" not in parcels:
        parcels["protected_share"] = 0.0
    parcels["protected_share"] = parcels["protected_share"].fillna(0.0)
    parcels["buildable_m2"] = (parcels["land_m2"] * (1 - parcels["protected_share"])).round(0)
    was_candidate = parcels["candidate"].copy()
    parcels["candidate"] = parcels["candidate"] & (parcels["protected_share"] < 0.80)
    cand = parcels[parcels["candidate"]].copy()
    report["candidate_parcels"] = len(cand)

    # ---- per-hexagon parcel factors
    g = cand.groupby("hex")
    land = g["buildable_m2"].sum()
    far = (cand["est_far"] * cand["buildable_m2"]).groupby(cand["hex"]).sum() / land
    low_value = (cand["bldg_to_land"] < 0.5) & ~cand["record_check"]
    value_share = (cand["buildable_m2"] * low_value).groupby(cand["hex"]).sum() / land
    recent_case = cand["last_case_year"].fillna(0) >= MOMENTUM_SINCE_YEAR
    cases = (recent_case & (cand["rezoning_cases"] > 0)).groupby(cand["hex"]).sum()
    record_checks = cand["record_check"].groupby(cand["hex"]).sum()

    # ---- imagery factors from the classifier (tiles assigned to the hexagon of their centre)
    t = tiles.merge(preds[["tile_id", "pred", "confidence", "needs_review"]], on="tile_id")
    centres = gpd.GeoSeries(t.geometry.centroid, crs=METRIC_CRS).to_crs(4326)
    t["hex"] = [h3.latlng_to_cell(p.y, p.x, HEX_RES) for p in centres]
    tg = t.groupby("hex")
    parking_share = tg.apply(lambda x: ((x["pred"] == "parking") & ~x["needs_review"]).mean())
    lowconf_share = tg["needs_review"].mean()
    n_tiles = tg.size()

    # ---- footprints added / re-mapped in the county's 2024 update
    nb = newb.copy()
    nb_c = gpd.GeoSeries(nb.to_crs(METRIC_CRS).geometry.centroid, crs=METRIC_CRS).to_crs(4326)
    nb["hex"] = [h3.latlng_to_cell(p.y, p.x, HEX_RES) for p in nb_c]
    new_fp = nb.groupby("hex").size()

    # ---- transit: distance from each hexagon centre to the nearest station
    hc = gpd.GeoDataFrame(hexes[["hex"]], geometry=hexes.to_crs(METRIC_CRS).geometry.centroid, crs=METRIC_CRS)
    near = gpd.sjoin_nearest(hc, metro[["NAME", "geometry"]].to_crs(METRIC_CRS), how="left", distance_col="metro_m")
    near = near[~near.index.duplicated()].set_index("hex")

    H = hexes.set_index("hex")
    H["cand_land_m2"] = land.reindex(H.index).fillna(0)     # buildable candidate land
    orig = parcels[was_candidate]    # protected land removed from ALL would-be candidates, incl. dropped parcels
    H["protected_m2"] = (orig["land_m2"] - orig["buildable_m2"]).groupby(orig["hex"]).sum().reindex(H.index).fillna(0)
    H["cand_parcels"] = g.size().reindex(H.index).fillna(0).astype(int)
    H["est_far"] = far.reindex(H.index)
    H["low_value_share"] = value_share.reindex(H.index)
    H["recent_rezoning_parcels"] = cases.reindex(H.index).fillna(0).astype(int)
    H["footprints_2024_update"] = new_fp.reindex(H.index).fillna(0).astype(int)
    H["imagery_parking_share"] = parking_share.reindex(H.index)
    H["imagery_lowconf_share"] = lowconf_share.reindex(H.index)
    H["tiles"] = n_tiles.reindex(H.index).fillna(0).astype(int)
    H["record_check_parcels"] = record_checks.reindex(H.index).fillna(0).astype(int)
    H["metro_station"] = near["NAME"]
    H["metro_m"] = near["metro_m"].round(0)

    # ---- factors 0-1 (only hexagons with candidate land are ranked; the rest score 0)
    has = (H["cand_land_m2"] > 0) & H["est_far"].notna()
    F = pd.DataFrame(index=H.index)
    F["underused"] = 1 - rank01(H.loc[has, "est_far"])
    F["parking"] = rank01(H.loc[has, "imagery_parking_share"])
    F["transit"] = (1 - (H["metro_m"] / TRANSIT_ZERO_M)).clip(0, 1)
    F["value"] = rank01(H.loc[has, "low_value_share"])
    F["momentum"] = rank01(H.loc[has, "recent_rezoning_parcels"] + H.loc[has, "footprints_2024_update"])
    F["land"] = rank01(H.loc[has, "cand_land_m2"])
    F = F.where(has, 0).fillna(0).round(4)
    for k in WEIGHTS:
        H[f"f_{k}"] = F[k]
    H["score"] = sum(F[k] * w for k, w in WEIGHTS.items()).round(4) * 100
    H["needs_review"] = has & ((H["imagery_lowconf_share"] > REVIEW_LOWCONF_SHARE)
                               | (H["record_check_parcels"] >= REVIEW_RECORD_CHECKS))
    H["rank"] = H["score"].rank(ascending=False, method="min").astype(int)

    H = gpd.GeoDataFrame(H.reset_index(), geometry="geometry", crs=4326)
    H.to_parquet(OUT / "hexes_scored.parquet")
    parcels.merge(H[["hex", "score", "rank"]].rename(columns={"score": "hex_score", "rank": "hex_rank"}),
                  on="hex", how="left").to_parquet(OUT / "parcels_scored.parquet")

    report["hexes"] = len(H)
    report["hexes_with_candidate_land"] = int(has.sum())
    report["hexes_needs_review"] = int(H["needs_review"].sum())
    report["score_by_submarket_median"] = H[has.to_numpy()].groupby("submarket")["score"].median().round(1).to_dict()
    (OUT / "step7_report.json").write_text(json.dumps(report, indent=2, default=str))

    # ---- top 10 with the reasons, for a sense check
    top = H.sort_values("score", ascending=False).head(10)
    print("\nTop 10 hexagons (top reasons = factors contributing most to the score):")
    for _, r in top.iterrows():
        ll = h3.cell_to_latlng(r["hex"])
        strongest = sorted(WEIGHTS, key=lambda k: -r[f"f_{k}"] * WEIGHTS[k])[:3]
        print(f"  #{r['rank']:<3} {r['submarket']:15s} score {r['score']:5.1f}  "
              f"{'NEEDS REVIEW ' if r['needs_review'] else ''}top reasons: {', '.join(strongest)}")
        print(f"       {r['cand_parcels']} candidate parcels, {r['cand_land_m2'] / 4047:.1f} buildable acres, est. FAR {r['est_far']:.2f}, "
              f"imagery parking {r['imagery_parking_share']:.0%}, {r['metro_m']:.0f} m to {r['metro_station']}, "
              f"parcels in a {MOMENTUM_SINCE_YEAR}+ rezoning case {r['recent_rezoning_parcels']}, "
              f"footprints added/re-mapped in 2024 update {r['footprints_2024_update']}, "
              f"protected land excluded {r['protected_m2'] / 4047:.1f} acres")
        print(f"       map: https://www.google.com/maps/@{ll[0]:.5f},{ll[1]:.5f},400m/data=!3m1!1e3")

    print("\n================ Step 7 report (measured) ================")
    for k, v in report.items():
        print(f"  {k}: {v}")


if __name__ == "__main__":
    main()
