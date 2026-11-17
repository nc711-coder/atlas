"""Step 2c: look closer at the step 2 results before building on them.

Run from the project folder:  python pipeline/step2c_check_joins.py
1. Sorts parcels where the zoning map and the tax records disagree into three groups:
   - point_method: the zone covering MOST of the parcel matches the tax record, so our
     "one point inside the parcel" lookup picked the wrong zone (fixable on our side)
   - split_parcel: the tax zone covers part of the parcel (5%+), not most of it
   - real_difference: no zone on the parcel matches the tax record
2. Lists the 10 biggest Tysons parcels whose land is worth far more than the building on it,
   so you can look them up on Google Maps and judge whether they look like redevelopment sites.
"""
import json

import geopandas as gpd
import pandas as pd

from config import DATA, METRIC_CRS
from step2b_property_data import zone_key

STEP2 = DATA / "step2"


def main():
    j = gpd.read_parquet(STEP2 / "parcels_joined.parquet").reset_index(drop=True)  # one unique row number per parcel
    zoning = gpd.read_parquet(STEP2 / "raw" / "Zoning.parquet")[["ZONECODE", "geometry"]].to_crs(METRIC_CRS)
    report = {}

    # Some county shapes are invalid (e.g. edges that cross themselves), which breaks overlap maths.
    # Count them, then repair them with make_valid.
    report["invalid_parcel_shapes_repaired"] = int((~j.geometry.is_valid).sum())
    report["invalid_zoning_shapes_repaired"] = int((~zoning.geometry.is_valid).sum())
    j["geometry"] = j.geometry.make_valid()
    zoning["geometry"] = zoning.geometry.make_valid()

    # ---------------- 1. zoning disagreements
    both = j["zone_map"].notna() & j["ZONING_DESC"].notna()
    d = j[both].copy()
    d["k_map"] = d["zone_map"].map(zone_key)
    d["k_tax"] = d["ZONING_DESC"].map(zone_key)
    dis = d[d["k_map"] != d["k_tax"]].to_crs(METRIC_CRS)
    dis["geometry"] = dis.geometry.make_valid()   # re-check after changing projection
    report["compared"] = int(both.sum())
    report["disagree"] = len(dis)

    # Area of each zone inside each disagreeing parcel.
    pairs = gpd.sjoin(dis[["PIN", "k_tax", "geometry"]], zoning, predicate="intersects", how="inner")
    zone_geoms = zoning.geometry.loc[pairs["index_right"]].values
    pairs["overlap_m2"] = pairs.geometry.intersection(gpd.GeoSeries(zone_geoms, index=pairs.index, crs=METRIC_CRS)).area
    pairs["share"] = pairs["overlap_m2"] / pairs.geometry.area
    pairs["k_zone"] = pairs["ZONECODE"].map(zone_key)

    def classify(g):
        tax = g["k_tax"].iloc[0]
        largest = g.sort_values("overlap_m2").iloc[-1]["k_zone"]
        if largest == tax:
            return "point_method"
        if ((g["k_zone"] == tax) & (g["share"] >= 0.05)).any():
            return "split_parcel"
        return "real_difference"

    groups = pairs.groupby(level=0).apply(classify)
    counts = groups.value_counts()
    report["disagree_groups"] = {k: int(v) for k, v in counts.items()}
    fixed = int(counts.get("point_method", 0))
    report["agree_if_using_largest_overlap_pct"] = round(100 * (len(d) - len(dis) + fixed) / len(d), 2)

    real = dis.loc[groups[groups == "real_difference"].index]
    top = (real.groupby(["zone_map", "ZONING_DESC"]).size().sort_values(ascending=False).head(15))
    print("\nMost common real differences (zoning map  vs  tax record):")
    for (zm, zt), n in top.items():
        print(f"  {n:6d}  map {zm:<10}  tax {zt}")

    # ---------------- 2. redevelopment sense check
    t = j[(j["submarket"] == "tysons") & (j["PARCEL_TYPE"] == "ORDINARY")
          & (j["area_m2"] >= 4047) & (j["APRLAND"] > 0) & j["bldg_to_land"].notna()].copy()
    t["acres"] = (t["area_m2"] / 4047).round(1)
    cand = t[t["bldg_to_land"] < 0.25].sort_values("area_m2", ascending=False).head(10)
    print("\nBiggest Tysons parcels (1+ acre) where the building is worth under 25% of the land:")
    cols = ["PIN", "address", "acres", "LUC_DESC", "zone_map", "APRLAND", "APRBLDG", "bldg_to_land"]
    with pd.option_context("display.width", 200, "display.max_colwidth", 35):
        print(cand[cols].to_string(index=False))
    report["tysons_1acre_plus_parcels"] = len(t)
    report["tysons_1acre_plus_with_bldg_under_25pct_of_land"] = int((t["bldg_to_land"] < 0.25).sum())

    (STEP2 / "step2c_report.json").write_text(json.dumps(report, indent=2))
    print("\n================ Step 2c report (measured) ================")
    for k, v in report.items():
        print(f"  {k}: {v}")


if __name__ == "__main__":
    main()
