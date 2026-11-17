"""Step 3: official submarket boundaries, per-parcel land-use measures, and the analysis grids.

Run from the project folder:  python pipeline/step3_study_area.py
Needs step 2's files. Writes to <DATA>/step3/:
  boundaries.parquet  the three submarket outlines (official county boundaries where available)
  parcels.parquet     parcels inside them, with building coverage, estimated floor-area ratio,
                      parking share, land-use category, candidate and "check the record" flags
  hexes.parquet       H3 hexagons (level 9, ~0.1 km2) that the opportunity score will be built on
  tiles.parquet       60 m x 60 m image tiles (200 x 200 px of 0.3 m NAIP) for the classifier
  step3_report.json   measured counts printed at the end
"""
import io
import json

import geopandas as gpd
import h3
import numpy as np
import pandas as pd
from shapely.geometry import Polygon, box, mapping
from shapely.ops import unary_union

from config import DATA, METRIC_CRS, SUBMARKETS
from step2b_property_data import BASE, post, session

STEP2 = DATA / "step2"
OUT = DATA / "step3"
COUNTY_BOX = (-77.6, 38.6, -76.9, 39.1)
HEX_RES = 9
TILE_M = 60
FEET_PER_FLOOR = 12      # county building heights are in feet (top minus ground elevation)

# Where each submarket's official outline comes from, in order of preference:
# (county service, keyword that picks the right rows or None for all rows).
BOUNDARY_SOURCES = {
    "tysons": [("Tysons_Urban_Center_Boundary", None),
               ("TYSONSMGR_URBAN_DEVELOPMENT_AREA", None),
               ("Tysons_Districts_RC_Update", None)],
    "reston_herndon": [("Reston_Transit_Station_Areas", None)],
    "richmond_hwy": [("Commercial_Revitalization_District_and_Areas", "RICHMOND")],
}

# Land-use words (from the tax records' LUC_DESC) that put a parcel outside what a commercial
# real estate firm would buy. Step 3's printout lists what landed in each group so we can refine these.
# Note "parks" not "park": "Auto parking" is a prime candidate, not public land.
PUBLIC_WORDS = ["parks", "recreation fac", "public", "govt", "government", "school", "church",
                "religious", "cemetery", "right of way", "common area", "open space",
                "railroad", "exhibition", "golf"]
MAX_SUBMARKET_KM2 = 40
SMALL_RESIDENTIAL_WORDS = ["single fam", "townhouse", "duplex", "detached", "mobile home"]
CONDO_WORDS = ["condo"]


def fetch_whole_service(service):
    """Every feature of every layer in a (small) county service. Looks up the layer numbers
    instead of assuming layer 0, and falls back to Esri JSON if GeoJSON is refused."""
    base = f"{BASE}/{service}/FeatureServer"
    meta = get_json(base)
    frames = []
    for layer in meta.get("layers", []):
        url = f"{base}/{layer['id']}/query"
        params = {"where": "1=1", "outFields": "*", "outSR": 4326, "returnGeometry": "true"}
        try:
            data = post(url, {**params, "f": "geojson"})
            gdf = gpd.GeoDataFrame.from_features(data.get("features", []), crs=4326)
        except Exception:
            data = post(url, {**params, "f": "json"})
            gdf = gpd.read_file(io.StringIO(json.dumps(data))).set_crs(4326, allow_override=True)
        if data.get("exceededTransferLimit") or data.get("properties", {}).get("exceededTransferLimit"):
            print(f"  note: {service} layer {layer['id']} has more rows than one request returns")
        frames.append(gdf)
    if not frames:
        raise ValueError("service has no layers")
    return gpd.GeoDataFrame(pd.concat(frames, ignore_index=True), geometry="geometry", crs=4326)


def get_json(url):
    r = session.get(url, params={"f": "json"}, timeout=60)
    r.raise_for_status()
    data = r.json()
    if "error" in data:
        raise RuntimeError(data["error"])
    return data


def official_boundary(market, report):
    for service, keyword in BOUNDARY_SOURCES[market]:
        geom = try_boundary(market, service, keyword, report)
        if geom is not None:
            return geom
    print(f"  !! no official boundary worked; falling back to the step 2 box for {market}")
    report[f"{market}_boundary_source"] = "fallback box from config.py"
    return box(*SUBMARKETS[market])


def try_boundary(market, service, keyword, report):
    try:
        gdf = fetch_whole_service(service)
        text_cols = [c for c in gdf.columns if c != "geometry"
                     and (pd.api.types.is_string_dtype(gdf[c]) or gdf[c].dtype == object)]
        print(f"\n  {service}: {len(gdf)} shapes. Text fields of each:")
        for _, row in gdf.head(12).iterrows():
            print("    ", {c: row[c] for c in text_cols if isinstance(row[c], str)})
        if keyword:
            hit = np.zeros(len(gdf), dtype=bool)
            for c in text_cols:
                hit |= gdf[c].astype(str).str.upper().str.contains(keyword).to_numpy()
            gdf = gdf[hit]
        if gdf.empty:
            raise ValueError("no matching shapes")
        geom = unary_union(gdf.geometry.make_valid().to_list())
        km2 = gpd.GeoSeries([geom], crs=4326).to_crs(METRIC_CRS).area.iloc[0] / 1e6
        if km2 > MAX_SUBMARKET_KM2:
            # e.g. a "mask" layer that covers the whole county except the area itself
            raise ValueError(f"shape covers {km2:.0f} km2, too big for one submarket (limit {MAX_SUBMARKET_KM2})")
        report[f"{market}_boundary_source"] = f"{service}" + (f" (rows containing '{keyword}')" if keyword else "")
        return geom
    except Exception as e:
        print(f"  !! {service} not usable ({e})")
        return None


def land_use_category(desc):
    d = str(desc or "").lower()
    if "parking" in d:
        return "commercial_or_other"
    if any(w in d for w in CONDO_WORDS):
        return "condo"
    if any(w in d for w in SMALL_RESIDENTIAL_WORDS):
        return "small_residential"
    if any(w in d for w in PUBLIC_WORDS):
        return "public_or_special"
    if any(w in d for w in SMALL_RESIDENTIAL_WORDS):
        return "small_residential"
    if not d or d == "none":
        return "unknown"
    return "commercial_or_other"


def overlap_by_parcel(parcels, layer, value_col=None):
    """Area of `layer` inside each parcel (optionally weighted by value_col), indexed by parcel row id."""
    near = gpd.sjoin(layer, parcels[["pid", "geometry"]], predicate="intersects", how="inner")
    if near.empty:
        return pd.Series(dtype=float)
    pg = parcels.set_index("pid").geometry
    inter = near.geometry.intersection(gpd.GeoSeries(pg.loc[near["pid"]].values, index=near.index, crs=METRIC_CRS))
    area = inter.area * (near[value_col] if value_col else 1)
    return area.groupby(near["pid"]).sum()


def hexes_for(geom, market):
    cells = set()
    for poly in getattr(geom, "geoms", [geom]):
        if poly.geom_type == "Polygon":
            cells |= set(h3.geo_to_cells(mapping(poly), HEX_RES))
    rows = [{"hex": c, "submarket": market,
             "geometry": Polygon([(lng, lat) for lat, lng in h3.cell_to_boundary(c)])} for c in cells]
    return gpd.GeoDataFrame(rows, geometry="geometry", crs=4326) if rows else None


def tiles_for(geom_m, market):
    xmin, ymin, xmax, ymax = geom_m.bounds
    xs = np.arange(np.floor(xmin / TILE_M) * TILE_M, xmax, TILE_M)
    ys = np.arange(np.floor(ymin / TILE_M) * TILE_M, ymax, TILE_M)
    gx, gy = np.meshgrid(xs, ys)
    gx, gy = gx.ravel(), gy.ravel()
    centers = gpd.GeoSeries(gpd.points_from_xy(gx + TILE_M / 2, gy + TILE_M / 2), crs=METRIC_CRS)
    keep = centers.within(geom_m).to_numpy()
    gx, gy = gx[keep], gy[keep]
    return gpd.GeoDataFrame({"submarket": market, "x0": gx, "y0": gy},
                            geometry=[box(x, y, x + TILE_M, y + TILE_M) for x, y in zip(gx, gy)], crs=METRIC_CRS)


def save_boundary_picture(bounds_m, parcels_m, path):
    """One panel per submarket: its outline, with parcels coloured by land-use group."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    colours = {"commercial_or_other": "#d95f02", "condo": "#7570b3", "small_residential": "#bbbbbb",
               "public_or_special": "#1b9e77", "unknown": "#666666"}
    fig, axes = plt.subplots(1, len(bounds_m), figsize=(6 * len(bounds_m), 6))
    for ax, (_, row) in zip(np.atleast_1d(axes), bounds_m.iterrows()):
        sub = parcels_m[parcels_m["submarket"] == row["submarket"]]
        for cat, g in sub.groupby("land_use_cat"):
            g.plot(ax=ax, color=colours.get(cat, "#666666"), linewidth=0, label=cat)
        gpd.GeoSeries([row.geometry], crs=METRIC_CRS).boundary.plot(ax=ax, color="black", linewidth=1.5)
        ax.set_title(f"{row['submarket']}  ({row.geometry.area / 1e6:.1f} km2, {len(sub)} parcels)")
        ax.set_axis_off()
    np.atleast_1d(axes)[0].legend(loc="lower left", fontsize=8)
    fig.tight_layout()
    fig.savefig(path, dpi=110)
    plt.close(fig)


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    report = {}

    # ---------------- boundaries
    print("Submarket boundaries:")
    bounds = gpd.GeoDataFrame({"submarket": list(BOUNDARY_SOURCES)},
                              geometry=[official_boundary(m, report) for m in BOUNDARY_SOURCES], crs=4326)
    bounds_m = bounds.to_crs(METRIC_CRS)
    for _, row in bounds_m.iterrows():
        m = row["submarket"]
        report[f"{m}_area_km2"] = round(row.geometry.area / 1e6, 2)
        step2_box = gpd.GeoSeries([box(*SUBMARKETS[m])], crs=4326).to_crs(METRIC_CRS).iloc[0]
        covered = row.geometry.intersection(step2_box).area / row.geometry.area
        report[f"{m}_inside_step2_download_pct"] = round(100 * covered, 2)
        if covered < 0.99:
            b = bounds[bounds.submarket == m].total_bounds
            print(f"\n  !! Only {covered:.1%} of {m} lies inside step 2's download box. In config.py set")
            print(f'     "{m}": ({b[0] - 0.005:.3f}, {b[1] - 0.005:.3f}, {b[2] + 0.005:.3f}, {b[3] + 0.005:.3f}),')
            print("     then re-run:  python pipeline\\step2b_property_data.py --refresh")
    bounds.to_parquet(OUT / "boundaries.parquet")

    # ---------------- parcels inside the boundaries
    j = gpd.read_parquet(STEP2 / "parcels_joined.parquet").reset_index(drop=True)
    j["geometry"] = j.geometry.make_valid()
    jm = j.to_crs(METRIC_CRS)
    jm["geometry"] = jm.geometry.make_valid()
    pts = gpd.GeoDataFrame(geometry=jm.representative_point(), crs=METRIC_CRS)
    inside = gpd.sjoin(pts, bounds_m, predicate="within", how="inner")
    inside = inside[~inside.index.duplicated(keep="first")]
    jm = jm.loc[inside.index].copy()
    jm["submarket"] = inside["submarket"]
    jm["pid"] = np.arange(len(jm))
    report["parcels_inside_boundaries"] = len(jm)
    report["parcels_by_submarket"] = {k: int(v) for k, v in jm["submarket"].value_counts().items()}

    # ---------------- buildings: coverage and estimated floor-area ratio
    study = bounds_m.union_all() if hasattr(bounds_m, "union_all") else bounds_m.unary_union
    b = gpd.read_parquet(STEP2 / "raw" / "buildings_current.parquet").to_crs(METRIC_CRS)
    b["geometry"] = b.geometry.make_valid()
    b = b[b.intersects(study)].copy()
    b["floors"] = np.clip(np.round(b["BLDG_HEIGHT"].fillna(FEET_PER_FLOOR) / FEET_PER_FLOOR), 1, None)
    area = jm.set_index("pid").geometry.area
    jm["bldg_coverage"] = (overlap_by_parcel(jm, b[["geometry"]]).reindex(jm["pid"]).fillna(0).to_numpy()
                           / area.to_numpy()).clip(0, 1).round(3)
    jm["est_far"] = (overlap_by_parcel(jm, b[["floors", "geometry"]], "floors").reindex(jm["pid"]).fillna(0).to_numpy()
                     / area.to_numpy()).round(3)

    # ---------------- parking: share of the parcel that is parking lot (driveways excluded)
    p = gpd.read_parquet(STEP2 / "raw" / "parking_current.parquet").to_crs(METRIC_CRS)
    p["geometry"] = p.geometry.make_valid()
    p = p[p.intersects(study)]
    report["parking_types_in_study_area"] = {k: int(v) for k, v in p["TYPE"].value_counts().items()}
    lots = p[~p["TYPE"].fillna("").str.upper().str.contains("DRIVEWAY")]
    jm["parking_share"] = (overlap_by_parcel(jm, lots[["geometry"]]).reindex(jm["pid"]).fillna(0).to_numpy()
                           / area.to_numpy()).clip(0, 1).round(3)

    # ---------------- categories and flags
    jm["land_use_cat"] = jm["LUC_DESC"].map(land_use_category)
    jm["candidate"] = (jm["land_use_cat"] == "commercial_or_other") & (jm["PARCEL_TYPE"] == "ORDINARY")
    jm["record_check"] = (jm["bldg_to_land"] < 0.10) & (jm["bldg_coverage"] >= 0.10)
    report["parcels_by_land_use_cat"] = {k: int(v) for k, v in jm["land_use_cat"].value_counts().items()}
    report["candidate_parcels"] = int(jm["candidate"].sum())
    report["candidate_parcels_with_record_check_flag"] = int((jm["candidate"] & jm["record_check"]).sum())

    print("\nMost common land uses in each group (check these look right):")
    for cat, g in jm.groupby("land_use_cat"):
        top = g["LUC_DESC"].value_counts().head(8)
        print(f"  {cat}: " + "; ".join(f"{k} ({v})" for k, v in top.items()))

    # ---------------- hexagons and image tiles
    hexes = pd.concat([hexes_for(g, m) for m, g in zip(bounds["submarket"], bounds.geometry)], ignore_index=True)
    hexes = gpd.GeoDataFrame(hexes.drop_duplicates("hex"), geometry="geometry", crs=4326)
    ll = gpd.GeoSeries(jm.representative_point(), crs=METRIC_CRS).to_crs(4326)
    jm["hex"] = [h3.latlng_to_cell(pt.y, pt.x, HEX_RES) for pt in ll]
    tiles = pd.concat([tiles_for(g, m) for m, g in zip(bounds_m["submarket"], bounds_m.geometry)], ignore_index=True)
    tiles = gpd.GeoDataFrame(tiles, geometry="geometry", crs=METRIC_CRS)
    tiles.insert(0, "tile_id", [f"{m[:3]}_{int(x)}_{int(y)}" for m, x, y in zip(tiles.submarket, tiles.x0, tiles.y0)])
    report["hexes_by_submarket"] = {k: int(v) for k, v in hexes["submarket"].value_counts().items()}
    report["tiles_by_submarket"] = {k: int(v) for k, v in tiles["submarket"].value_counts().items()}

    save_boundary_picture(bounds_m, jm, OUT / "boundaries.png")
    jm.drop(columns="pid").to_crs(4326).to_parquet(OUT / "parcels.parquet")
    hexes.to_parquet(OUT / "hexes.parquet")
    tiles.to_parquet(OUT / "tiles.parquet")   # kept in metres (UTM 18N, same as the NAIP imagery)
    (OUT / "step3_report.json").write_text(json.dumps(report, indent=2))

    # ---------------- sense check: biggest underused candidate sites in Tysons
    c = jm[jm["candidate"] & (jm["submarket"] == "tysons") & (jm.geometry.area >= 4047)].copy()
    c["acres"] = (c.geometry.area / 4047).round(1)
    c = c.sort_values(["est_far", "acres"], ascending=[True, False]).head(10)
    print("\nTysons candidate parcels (1+ acre) with the lowest estimated floor-area ratio:")
    cols = ["address", "acres", "LUC_DESC", "zone_map", "est_far", "bldg_coverage", "parking_share", "record_check"]
    with pd.option_context("display.width", 220, "display.max_colwidth", 32):
        print(c[cols].to_string(index=False))

    print("\n================ Step 3 report (measured) ================")
    for k, v in report.items():
        print(f"  {k}: {v}")


if __name__ == "__main__":
    main()
