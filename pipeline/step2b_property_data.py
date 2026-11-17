"""Step 2b: download Fairfax County property data for the three submarkets and join it per parcel.

Run from the project folder:  python pipeline/step2b_property_data.py
Add --refresh to re-download instead of reusing files saved by an earlier run.

Writes to <DATA>/step2/:
  raw/*.parquet          what the county returned, unchanged (only current features for parking/buildings)
  parcels_joined.parquet one row per parcel with values, land use, zoning, rezoning, last sale, Metro distance
  step2_report.json      the measured match counts printed at the end
"""
import argparse
import json
import re
import time

import geopandas as gpd
import numpy as np
import pandas as pd
import requests

from config import DATA, METRIC_CRS, SUBMARKETS

BASE = "https://services1.arcgis.com/ioennV6PpG5Xodq0/ArcGIS/rest/services"
OUT = DATA / "step2"
RAW = OUT / "raw"
PAGE = 2000          # the county server returns at most 2000 rows per request
ID_CHUNK = 200       # parcel IDs per tax-table request

session = requests.Session()


# ---------------------------------------------------------------- downloading

def post(url, params):
    """POST a query, retrying a few times if the county server hiccups."""
    for attempt in range(4):
        try:
            r = session.post(url, data=params, timeout=120)
            r.raise_for_status()
            data = r.json()
            if "error" in data:
                raise RuntimeError(data["error"])
            return data
        except Exception:
            if attempt == 3:
                raise
            time.sleep(2 ** attempt)


def fetch_map_layer(service, bbox):
    """All features of a map layer that touch a lon/lat box, page by page."""
    url = f"{BASE}/{service}/FeatureServer/0/query"
    envelope = {"xmin": bbox[0], "ymin": bbox[1], "xmax": bbox[2], "ymax": bbox[3], "spatialReference": {"wkid": 4326}}
    features, offset = [], 0
    while True:
        data = post(url, {
            "where": "1=1", "outFields": "*", "f": "geojson", "outSR": 4326,
            "geometry": json.dumps(envelope), "geometryType": "esriGeometryEnvelope",
            "inSR": 4326, "spatialRel": "esriSpatialRelIntersects",
            "orderByFields": "OBJECTID", "resultOffset": offset, "resultRecordCount": PAGE,
        })
        batch = data.get("features", [])
        features += batch
        more = data.get("properties", {}).get("exceededTransferLimit") or data.get("exceededTransferLimit")
        if not batch or (len(batch) < PAGE and not more):
            break
        offset += len(batch)
    if not features:
        return gpd.GeoDataFrame({"geometry": []}, geometry="geometry", crs=4326)
    return gpd.GeoDataFrame.from_features(features, crs=4326)


def fetch_table_for_parcels(service, parids):
    """Rows of a tax table for a list of parcel IDs, in chunks."""
    url = f"{BASE}/{service}/FeatureServer/0/query"
    ids = sorted(set(parids))
    rows = []
    for i in range(0, len(ids), ID_CHUNK):
        part = ids[i:i + ID_CHUNK]
        where = "PARID IN (" + ",".join("'" + p.replace("'", "''") + "'" for p in part) + ")"
        offset = 0
        while True:
            data = post(url, {"where": where, "outFields": "*", "returnGeometry": "false", "f": "json",
                              "orderByFields": "OBJECTID", "resultOffset": offset, "resultRecordCount": PAGE})
            batch = [f["attributes"] for f in data.get("features", [])]
            rows += batch
            if not batch or not data.get("exceededTransferLimit"):
                break
            offset += len(batch)
        done = min(i + ID_CHUNK, len(ids))
        if (i // ID_CHUNK) % 25 == 0 or done == len(ids):
            print(f"    {service}: {done}/{len(ids)} parcel IDs")
    return pd.DataFrame(rows)


def cached(name, refresh, make):
    """Reuse a saved download unless --refresh is given."""
    path = RAW / f"{name}.parquet"
    if path.exists() and not refresh:
        try:
            return gpd.read_parquet(path)
        except ValueError:          # a plain table has no geometry column
            return pd.read_parquet(path)
    print(f"  downloading {name} ...")
    df = make()
    df.to_parquet(path)
    return df


def map_layer_all_submarkets(service, refresh):
    def make():
        parts = []
        for market, bbox in SUBMARKETS.items():
            gdf = fetch_map_layer(service, bbox)
            gdf["submarket"] = market
            parts.append(gdf)
        both = pd.concat(parts, ignore_index=True)
        # A feature on the edge of two boxes comes back twice: keep it once.
        return gpd.GeoDataFrame(both.drop_duplicates("OBJECTID"), geometry="geometry", crs=4326)
    return cached(service, refresh, make)


# ---------------------------------------------------------------- cleaning helpers

def newest_year(df, report, name):
    """Tax tables hold several tax years: keep each parcel's newest row and record which years were used."""
    if df.empty:
        report[f"{name}_years_used"] = {}
        return df
    df = df.sort_values(["PARID", "TAXYR"]).drop_duplicates("PARID", keep="last")
    report[f"{name}_years_used"] = {int(k): int(v) for k, v in df["TAXYR"].value_counts().sort_index().items()}
    return df


def zone_key(text):
    """'RE(Res Estate 1DU/2AC)' and 'R-E' -> 'RE', so the map and tax table can be compared."""
    if text is None or (isinstance(text, float) and np.isnan(text)):
        return None
    return re.sub(r"[^A-Z0-9]", "", str(text).split("(")[0].upper()) or None


def case_year(case_number):
    """'RZ2000-DR-047' -> 2000. The case's own date isn't published, but its number carries the year."""
    m = re.search(r"(19|20)\d{2}", str(case_number or ""))
    return int(m.group(0)) if m else None


def current_only(gdf, report, name):
    """Drop features the county has marked as removed (they have an expiry date)."""
    if "EXPIRY_DATE" not in gdf.columns:
        return gdf
    keep = gdf["EXPIRY_DATE"].isna()
    report[f"{name}_current"] = int(keep.sum())
    report[f"{name}_expired_dropped"] = int((~keep).sum())
    return gdf[keep]


def pct(part, whole):
    return round(100 * part / whole, 2) if whole else None


# ---------------------------------------------------------------- main

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--refresh", action="store_true", help="re-download instead of reusing saved files")
    args = ap.parse_args()
    RAW.mkdir(parents=True, exist_ok=True)
    report = {}

    print("Map layers:")
    parcels = map_layer_all_submarkets("Parcels", args.refresh)
    zoning = map_layer_all_submarkets("Zoning", args.refresh)
    cases = map_layer_all_submarkets("Zoning_Cases_Post_2000", args.refresh)
    parking = current_only(map_layer_all_submarkets("Driveways_and_Parking_Lots", args.refresh), report, "parking_polygons")
    buildings = current_only(map_layer_all_submarkets("Buildings", args.refresh), report, "buildings")
    metro = cached("Metrorail_Stations", args.refresh, lambda: fetch_map_layer("Metrorail_Stations", (-77.6, 38.6, -76.9, 39.1)))

    parcels = parcels[parcels["PIN"].notna()].copy()
    parcels["PIN"] = parcels["PIN"].astype(str)
    pins = parcels["PIN"].tolist()
    report["parcels_total"] = len(parcels)
    report["parcels_by_submarket"] = {k: int(v) for k, v in parcels["submarket"].value_counts().items()}
    report["parcels_by_type"] = {k: int(v) for k, v in parcels["PARCEL_TYPE"].value_counts().items()}

    print("Tax tables (by parcel ID):")
    values = newest_year(cached("assessed_values", args.refresh,
                                lambda: fetch_table_for_parcels("Tax_Administration_Real_Estate_Assessed_Values", pins)), report, "assessed_values")
    pdata = newest_year(cached("parcels_data", args.refresh,
                               lambda: fetch_table_for_parcels("Tax_Administration_Real_Estate_Parcels_Data", pins)), report, "parcels_data")
    legal = newest_year(cached("legal_data", args.refresh,
                               lambda: fetch_table_for_parcels("Tax_Administration_Real_Estate_Legal_Data", pins)), report, "legal_data")
    sales = cached("sales_data", args.refresh,
                   lambda: fetch_table_for_parcels("Tax_Administration_Real_Estate_Sales_Data", pins))

    # ---- join the tables onto the parcel shapes (parcel map PIN = tax table PARID)
    j = parcels[["PIN", "PARCEL_TYPE", "submarket", "geometry"]].copy()
    if not values.empty:
        j = j.merge(values[["PARID", "TAXYR", "APRLAND", "APRBLDG", "APRTOT"]].rename(columns={"TAXYR": "assess_year"}),
                    left_on="PIN", right_on="PARID", how="left").drop(columns="PARID")
    if not pdata.empty:
        j = j.merge(pdata[["PARID", "LUC_DESC", "ZONING_DESC", "LIVUNIT"]], left_on="PIN", right_on="PARID", how="left").drop(columns="PARID")
    if not legal.empty:
        legal = legal.copy()
        legal["address"] = (legal["ADRNO"].astype("Int64").astype(str).replace("<NA>", "") + " "
                            + legal["ADRSTR"].fillna("") + " " + legal["ADRSUF"].fillna("")).str.strip().replace("", None)
        j = j.merge(legal[["PARID", "address", "ACRES", "SQFT"]], left_on="PIN", right_on="PARID", how="left").drop(columns="PARID")

    # Match = the parcel's ID exists in that table (even if some of its fields are blank).
    for table, name in [(values, "assessed_values"), (pdata, "parcels_data"), (legal, "legal_data")]:
        n = int(j["PIN"].isin(table["PARID"]).sum()) if not table.empty else 0
        report[f"parcels_matched_{name}"] = n
        report[f"parcels_matched_{name}_pct"] = pct(n, len(j))
    if "address" in j:
        report["parcels_with_street_address"] = int(j["address"].notna().sum())

    # ---- sales: last sale with a price, and how many sales since 2015
    if not sales.empty:
        s = sales[sales["PRICE"].fillna(0) > 0].copy()
        s["sale_date"] = pd.to_datetime(s["SALEDT"], unit="ms", errors="coerce")
        s = s.dropna(subset=["sale_date"]).sort_values("sale_date")
        last = s.groupby("PARID").tail(1)[["PARID", "sale_date", "PRICE"]].rename(
            columns={"sale_date": "last_sale_date", "PRICE": "last_sale_price"})
        n_recent = s[s["sale_date"] >= "2015-01-01"].groupby("PARID").size().rename("sales_since_2015").reset_index()
        j = j.merge(last, left_on="PIN", right_on="PARID", how="left").drop(columns="PARID")
        j = j.merge(n_recent, left_on="PIN", right_on="PARID", how="left").drop(columns="PARID")
        j["sales_since_2015"] = j["sales_since_2015"].fillna(0).astype(int)
        report["parcels_with_a_priced_sale"] = int(j["last_sale_date"].notna().sum())

    j = gpd.GeoDataFrame(j, geometry="geometry", crs=4326)
    metric = j.to_crs(METRIC_CRS)
    j["area_m2"] = metric.area.round(1)
    points = gpd.GeoDataFrame(j[["PIN"]], geometry=metric.representative_point(), crs=METRIC_CRS)

    # ---- zoning from the map, checked against the tax table's zoning
    z = gpd.sjoin(points, zoning[["ZONECODE", "geometry"]].to_crs(METRIC_CRS), predicate="within", how="left")
    z = z[~z.index.duplicated(keep="first")]
    j["zone_map"] = z["ZONECODE"]
    if "ZONING_DESC" in j:
        both = j["zone_map"].notna() & j["ZONING_DESC"].notna()
        agree = both & (j["zone_map"].map(zone_key) == j["ZONING_DESC"].map(zone_key))
        report["zoning_compared_parcels"] = int(both.sum())
        report["zoning_map_vs_tax_agree"] = int(agree.sum())
        report["zoning_map_vs_tax_agree_pct"] = pct(int(agree.sum()), int(both.sum()))

    # ---- rezoning cases covering each parcel
    if not cases.empty:
        c = cases[["CASE_NUMBER", "STATUS", "geometry"]].to_crs(METRIC_CRS).copy()
        c["case_year"] = c["CASE_NUMBER"].map(case_year)
        report["rezoning_cases"] = len(c)
        report["rezoning_cases_without_year"] = int(c["case_year"].isna().sum())
        hits = gpd.sjoin(points, c, predicate="within", how="inner")
        per = hits.groupby(level=0).agg(rezoning_cases=("CASE_NUMBER", "nunique"), last_case_year=("case_year", "max"))
        j = j.join(per)
        j["rezoning_cases"] = j["rezoning_cases"].fillna(0).astype(int)
        report["parcels_in_a_rezoning_case"] = int((j["rezoning_cases"] > 0).sum())

    # ---- distance to the nearest Metro station
    if not metro.empty:
        stations = metro[["NAME", "geometry"]].to_crs(METRIC_CRS)
        near = gpd.sjoin_nearest(points, stations, how="left", distance_col="metro_dist_m")
        near = near[~near.index.duplicated(keep="first")]
        j["metro_station"] = near["NAME"]
        j["metro_dist_m"] = near["metro_dist_m"].round(0)

    # ---- the redevelopment signal: building value relative to land value
    if "APRLAND" in j:
        j["bldg_to_land"] = np.where(j["APRLAND"] > 0, j["APRBLDG"] / j["APRLAND"], np.nan)
        report["parcels_with_land_value_0_or_missing"] = int((~(j["APRLAND"] > 0)).sum())

    report["parking_polygons_saved"] = len(parking)
    report["buildings_saved"] = len(buildings)
    parking.to_parquet(RAW / "parking_current.parquet")
    buildings.to_parquet(RAW / "buildings_current.parquet")

    j.to_parquet(OUT / "parcels_joined.parquet")
    (OUT / "step2_report.json").write_text(json.dumps(report, indent=2, default=str))

    print("\n================ Step 2 report (measured) ================")
    for k, v in report.items():
        print(f"  {k}: {v}")
    print("Saved", OUT / "parcels_joined.parquet")


if __name__ == "__main__":
    main()
