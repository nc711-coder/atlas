"""Step 6b: new construction from county building footprints (instead of imagery change detection).

Run from the project folder:  python pipeline/step6b_county_new_buildings.py

Why: step 6's imagery change flags were 20% precise on 75 hand-checked tiles (comparing two independent
classifications compounds their errors), so Atlas does not use them. The county Buildings layer dates each
footprint (EFFECTIVE_DATE), which can show what was added since 2018.

Rule (fixed before looking at results): a tile has a NEW BUILDING if current county footprints dated
2019-01-01 or later cover >= 5% of it (~180 m2, so sheds don't count).

Checked against your 150 hand-labelled 2018/2025 pairs from step 6. Those pairs were sampled 75 from the
image-flagged tiles and 75 from the rest, so each pair is weighted by how many tiles its group stands for.
Limit: your "changed" label also includes paving and clearing, which a buildings-only signal cannot see.

Writes to <DATA>/step6/: new_buildings.parquet (footprints dated 2019+ in the study area),
tiles_new_building.parquet, county_new_building_report.json
"""
import json

import geopandas as gpd
import numpy as np
import pandas as pd

from config import DATA, METRIC_CRS

OUT = DATA / "step6"
SINCE = pd.Timestamp("2019-01-01")
MIN_TILE_SHARE = 0.05
TILE_AREA = 60 * 60


def main():
    report = {}
    bounds = gpd.read_parquet(DATA / "step3" / "boundaries.parquet").to_crs(METRIC_CRS)
    study = bounds.union_all() if hasattr(bounds, "union_all") else bounds.unary_union
    b = gpd.read_parquet(DATA / "step2" / "raw" / "buildings_current.parquet").to_crs(METRIC_CRS)
    b["geometry"] = b.geometry.make_valid()
    b = b[b.intersects(study)].copy()
    b["date"] = pd.to_datetime(b["EFFECTIVE_DATE"], unit="ms", errors="coerce")
    b["year"] = b["date"].dt.year

    # ---- are the dates construction dates, or re-mapping dates? Look before trusting them.
    report["buildings_in_study_area"] = len(b)
    report["buildings_without_date"] = int(b["date"].isna().sum())
    report["buildings_by_year"] = {int(k): int(v) for k, v in b["year"].value_counts().sort_index().items()}
    report["sources_of_2019plus_buildings"] = {str(k): int(v) for k, v in
                                               b.loc[b["date"] >= SINCE, "SOURCE"].value_counts().head(10).items()}
    new = b[b["date"] >= SINCE]
    share_new = len(new) / max(len(b), 1)
    report["share_dated_2019_or_later"] = round(share_new, 4)
    if share_new > 0.30:
        print(f"!! {share_new:.0%} of footprints are dated 2019+. That looks like county re-mapping, not "
              f"construction. Treat the results below with caution and tell Claude.")
    new[["year", "SOURCE", "BLDG_HEIGHT", "geometry"]].to_crs(4326).to_parquet(OUT / "new_buildings.parquet")

    # ---- per tile
    tiles = gpd.read_parquet(DATA / "step3" / "tiles.parquet")
    hits = gpd.sjoin(new[["geometry"]], tiles[["tile_id", "geometry"]], predicate="intersects", how="inner")
    tile_geom = tiles.set_index("tile_id").geometry
    inter = hits.geometry.intersection(gpd.GeoSeries(tile_geom.loc[hits["tile_id"]].values, index=hits.index, crs=METRIC_CRS))
    area = inter.area.groupby(hits["tile_id"]).sum()
    tiles["new_bldg_share"] = tiles["tile_id"].map(area).fillna(0) / TILE_AREA
    tiles["county_new_building"] = tiles["new_bldg_share"] >= MIN_TILE_SHARE
    tiles[["tile_id", "new_bldg_share", "county_new_building"]].to_parquet(OUT / "tiles_new_building.parquet")
    report["tiles_with_county_new_building"] = int(tiles["county_new_building"].sum())
    report["tiles_total"] = len(tiles)

    # ---- check against your 150 hand-labelled pairs (weighted by sampling group)
    key = pd.read_csv(OUT / "change_sample_key.csv")
    lab = pd.read_csv(DATA / "step4" / "labels_change.csv")
    ch = pd.read_parquet(OUT / "change.parquet")
    d = key.merge(lab, on="tile_id").merge(tiles[["tile_id", "county_new_building"]], on="tile_id")
    d["real"] = d["label"] == "changed"
    n_flag, n_unflag = int(ch["changed_flag"].sum()), int((~ch["changed_flag"]).sum())
    n_s_flag, n_s_unflag = int(d["changed_flag"].sum()), int((~d["changed_flag"]).sum())
    d["w"] = np.where(d["changed_flag"], n_flag / n_s_flag, n_unflag / n_s_unflag)
    c, r, w = d["county_new_building"], d["real"], d["w"]
    report["check_pairs"] = len(d)
    report["check_raw_counts"] = {
        "county_flag_and_really_changed": int((c & r).sum()),
        "county_flag_but_no_change": int((c & ~r).sum()),
        "no_county_flag_but_changed": int((~c & r).sum()),
        "no_county_flag_no_change": int((~c & ~r).sum()),
    }
    report["precision_weighted"] = round(float((w * (c & r)).sum() / (w * c).sum()), 4) if c.any() else None
    report["recall_of_all_changes_weighted"] = round(float((w * (c & r)).sum() / (w * r).sum()), 4) if r.any() else None
    report["note"] = ("weighted = each checked pair stands for the tiles of its sampling group; recall counts ALL "
                      "changes you marked (incl. paving/clearing), which footprints cannot detect")

    (OUT / "county_new_building_report.json").write_text(json.dumps(report, indent=2))
    print("\n================ Step 6b report (measured) ================")
    for k, v in report.items():
        print(f"  {k}: {v}")


if __name__ == "__main__":
    main()
