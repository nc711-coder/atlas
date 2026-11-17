"""Step 2a: ask each Fairfax County data service what it contains, before we download anything.

Run from the project folder:  python pipeline/step2a_inspect_sources.py
Prints a short summary and saves the full details to data/step2/sources_schema.json
"""
import json
from pathlib import Path

import requests

BASE = "https://services1.arcgis.com/ioennV6PpG5Xodq0/ArcGIS/rest/services"
SERVICES = [
    "Parcels",
    "Zoning",
    "Zoning_Cases_Post_2000",
    "Tax_Administration_Real_Estate_Assessed_Values",
    "Tax_Administration_Real_Estate_Parcels_Data",
    "Tax_Administration_Real_Estate_Legal_Data",
    "Tax_Administration_Real_Estate_Sales_Data",
    "Driveways_and_Parking_Lots",
    "Buildings",
    "Metrorail_Stations",
]
# Same Tysons box as step 1, used to count how many map features fall inside it.
TYSONS = {"xmin": -77.240, "ymin": 38.912, "xmax": -77.222, "ymax": 38.926, "spatialReference": {"wkid": 4326}}
OUT = Path(__file__).resolve().parent.parent / "data" / "step2"

session = requests.Session()


def get(url, **params):
    params.setdefault("f", "json")
    r = session.get(url, params=params, timeout=60)
    r.raise_for_status()
    data = r.json()
    if "error" in data:
        raise RuntimeError(data["error"])
    return data


def inspect_layer(url):
    meta = get(url)
    info = {
        "url": url,
        "name": meta.get("name"),
        "type": meta.get("type"),
        "geometry": meta.get("geometryType"),
        "max_records": meta.get("maxRecordCount"),
        "formats": meta.get("supportedQueryFormats"),
        "fields": [f"{f['name']} ({f['type'].replace('esriFieldType', '')})" for f in meta.get("fields", [])],
    }
    info["total_count"] = get(url + "/query", where="1=1", returnCountOnly="true").get("count")
    if info["geometry"]:
        info["count_in_tysons_box"] = get(
            url + "/query", where="1=1", returnCountOnly="true",
            geometry=json.dumps(TYSONS), geometryType="esriGeometryEnvelope",
            inSR=4326, spatialRel="esriSpatialRelIntersects",
        ).get("count")
    sample = get(url + "/query", where="1=1", outFields="*", returnGeometry="false", resultRecordCount=2)
    info["sample_rows"] = [feat["attributes"] for feat in sample.get("features", [])]
    return info


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    report = {}
    for name in SERVICES:
        service_url = f"{BASE}/{name}/FeatureServer"
        try:
            service = get(service_url)
            parts = service.get("layers", []) + service.get("tables", [])
            report[name] = [inspect_layer(f"{service_url}/{p['id']}") for p in parts]
        except Exception as e:  # keep going so one broken service doesn't stop the rest
            report[name] = {"error": str(e)}

    (OUT / "sources_schema.json").write_text(json.dumps(report, indent=2, default=str))

    for name, layers in report.items():
        print("=" * 70)
        print(name)
        if isinstance(layers, dict):
            print("  ERROR:", layers["error"])
            continue
        for L in layers:
            where = f", {L['count_in_tysons_box']} in Tysons box" if "count_in_tysons_box" in L else ""
            print(f"  [{L['url'].rsplit('/', 1)[1]}] {L['name']} | {L['geometry'] or 'table'} | "
                  f"{L['total_count']} rows{where} | max {L['max_records']}/request | {L['formats']}")
            print("  fields:", ", ".join(L["fields"]))
            if L["sample_rows"]:
                print("  sample:", json.dumps(L["sample_rows"][0], default=str)[:400])
    print("=" * 70)
    print("Full details saved to", OUT / "sources_schema.json")


if __name__ == "__main__":
    main()
