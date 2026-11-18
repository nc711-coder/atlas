"""Step 7a: protected land (stream buffers and floodplains) that can't be built on.

Run from the project folder:  python pipeline/step7a_environmental_constraints.py
Then re-run step 7, which uses the result.

Why: spot-checking the top hexagons on satellite imagery found Scott's Run stream valley (near McLean
station) counted as "underused land". Fairfax protects stream buffers (Resource Protection Areas) and
regulates floodplains, so that land is not a development opportunity.

Rule (fixed before re-scoring): each candidate parcel gets protected_share = share of its area inside an
RPA or floodplain. Parcels >= 80% protected stop being candidates; for the rest only the buildable part
counts as land in the score.

Writes <DATA>/step7/parcels_env.parquet and step7a_report.json
"""
import json

import geopandas as gpd
import pandas as pd
from shapely.ops import unary_union

from config import DATA, METRIC_CRS, SUBMARKETS
from step2b_property_data import fetch_map_layer
from step3_study_area import BASE, get_json

OUT = DATA / "step7"
DROP_IF_PROTECTED = 0.80
# Likely county service names; the script reports which ones exist.
SOURCES = {
    "resource_protection_area": ["Resource_Protection_Areas", "Resource_Protection_Area", "RPA",
                                 "Chesapeake_Bay_Preservation_Areas"],
    "floodplain": ["Floodplains", "Floodplain", "Floodplain_Areas", "FEMA_Floodplains", "Flood_Hazard_Areas"],
}


def download(kind, report):
    path = DATA / "step2" / "raw" / f"env_{kind}.parquet"
    if path.exists():
        report[f"{kind}_source"] = "cached"
        return gpd.read_parquet(path)
    for service in SOURCES[kind]:
        try:
            meta = get_json(f"{BASE}/{service}/FeatureServer")
            parts = []
            for layer in meta.get("layers", []):
                for bbox in SUBMARKETS.values():
                    g = fetch_map_layer(service, bbox, layer=layer["id"])
                    if not g.empty:
                        parts.append(g[["geometry"]])
            if not parts:
                raise ValueError("no shapes in the study area")
            gdf = gpd.GeoDataFrame(pd.concat(parts, ignore_index=True), geometry="geometry", crs=4326)
            gdf = gdf[gdf.geom_type.str.contains("Polygon")]
            gdf.to_parquet(path)
            report[f"{kind}_source"] = service
            print(f"  {kind}: {service} ({len(gdf)} shapes)")
            return gdf
        except Exception as e:
            print(f"  {kind}: {service} not usable ({e})")
    report[f"{kind}_source"] = None
    return None


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    report = {"drop_if_protected_share": DROP_IF_PROTECTED}
    layers = [g for g in (download(k, report) for k in SOURCES) if g is not None]
    if not layers:
        raise SystemExit("No protected-land layer found under the names tried. Paste this output to Claude.")

    bounds = gpd.read_parquet(DATA / "step3" / "boundaries.parquet").to_crs(METRIC_CRS)
    study = unary_union(bounds.geometry.to_list())
    prot = pd.concat([g.to_crs(METRIC_CRS) for g in layers], ignore_index=True)
    prot["geometry"] = prot.geometry.make_valid()
    prot = prot[prot.intersects(study)]
    protected = unary_union(prot.geometry.to_list())   # merged, so overlapping RPA + floodplain isn't counted twice

    parcels = gpd.read_parquet(DATA / "step3" / "parcels.parquet").to_crs(METRIC_CRS)
    cand = parcels[parcels["candidate"]].copy()
    cand["geometry"] = cand.geometry.make_valid()
    cand["protected_share"] = (cand.geometry.intersection(protected).area / cand.geometry.area).clip(0, 1).round(3)
    cand[["PIN", "protected_share"]].to_parquet(OUT / "parcels_env.parquet")

    area = cand.geometry.area
    report["candidate_parcels"] = len(cand)
    report["candidates_with_any_protected_land"] = int((cand["protected_share"] > 0).sum())
    report["candidates_dropped_80pct_protected"] = int((cand["protected_share"] >= DROP_IF_PROTECTED).sum())
    report["candidate_acres_before"] = round(float(area.sum() / 4046.86), 1)
    report["protected_candidate_acres_removed"] = round(float((area * cand["protected_share"]).sum() / 4046.86), 1)
    (OUT / "step7a_report.json").write_text(json.dumps(report, indent=2))
    print("\n================ Step 7a report (measured) ================")
    for k, v in report.items():
        print(f"  {k}: {v}")
    print("Next:  python pipeline\\step7_opportunity_score.py")


if __name__ == "__main__":
    main()
