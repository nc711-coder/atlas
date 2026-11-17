"""Step 4a: download a NAIP image chip (200 x 200 px, 4 bands) for every 60 m tile from step 3.

Run from the project folder:  python pipeline/step4a_download_chips.py            (2025 imagery)
                              python pipeline/step4a_download_chips.py --year 2018 (used in step 6)
Safe to stop and re-run: tiles already saved are skipped.

Writes to <DATA>/step4/chips/<year>/<tile_id>.npz  (array "img": red, green, blue, near-infrared)
and <DATA>/step4/chips_<year>.csv  (which scene and date each tile came from, and how much of it was covered)
"""
import argparse
import os
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

import geopandas as gpd
import numpy as np
import pandas as pd
import planetary_computer
import pystac_client
import rasterio
from rasterio.enums import Resampling
from rasterio.warp import transform_bounds
from rasterio.windows import from_bounds
from shapely.geometry import shape

from config import DATA, METRIC_CRS

STAC_URL = "https://planetarycomputer.microsoft.com/api/stac/v1"
TILE_PX = 200        # 60 m at 0.3 m/pixel; older, coarser years are resampled to the same size
TILES_PER_TASK = 40  # tiles read per open image file

# Make remote image reads faster and more forgiving of network hiccups.
os.environ.setdefault("GDAL_DISABLE_READDIR_ON_OPEN", "EMPTY_DIR")
os.environ.setdefault("GDAL_HTTP_MAX_RETRY", "4")
os.environ.setdefault("GDAL_HTTP_RETRY_DELAY", "2")
os.environ.setdefault("VSI_CACHE", "TRUE")


def find_scenes(tiles_ll, year):
    catalog = pystac_client.Client.open(STAC_URL)
    items = list(catalog.search(collections=["naip"], bbox=list(tiles_ll.total_bounds),
                                datetime=f"{year}-01-01/{year}-12-31").items())
    if not items:
        raise SystemExit(f"No NAIP scenes from {year} cover the study area.")
    return items


def assign_tiles(tiles_ll, items):
    """Give each tile the scene that covers most of it."""
    scenes = gpd.GeoDataFrame({"scene_i": range(len(items))},
                              geometry=[shape(it.geometry) for it in items], crs=4326)
    pairs = gpd.sjoin(tiles_ll[["tile_id", "geometry"]], scenes, predicate="intersects", how="inner")
    scene_geom = gpd.GeoSeries(scenes.geometry.loc[pairs["scene_i"]].values, index=pairs.index, crs=4326).to_crs(METRIC_CRS)
    tile_geom = pairs.geometry.to_crs(METRIC_CRS)
    pairs["coverage"] = tile_geom.intersection(scene_geom).area / tile_geom.area
    best = pairs.sort_values("coverage").groupby("tile_id").tail(1)
    return best[["tile_id", "scene_i", "coverage"]]


def read_tiles(item, rows, out_dir):
    """Open one scene once and cut out a batch of tiles from it."""
    href = planetary_computer.sign(item.assets["image"].href)   # fresh access token per batch
    done = []
    with rasterio.open(href) as src:
        for r in rows:
            b = transform_bounds(METRIC_CRS, src.crs, r.x0, r.y0, r.x0 + 60, r.y0 + 60)
            window = from_bounds(*b, transform=src.transform)
            img = src.read(indexes=[1, 2, 3, 4], window=window, out_shape=(4, TILE_PX, TILE_PX),
                           resampling=Resampling.bilinear, boundless=True, fill_value=0)
            np.savez_compressed(out_dir / f"{r.tile_id}.npz", img=img.astype(np.uint8))
            done.append({"tile_id": r.tile_id, "scene": item.id, "date": str(item.datetime.date()),
                         "pixel_m": round(src.res[0], 2), "coverage": round(r.coverage, 4)})
    return done


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--year", type=int, default=2025)
    ap.add_argument("--workers", type=int, default=8)
    args = ap.parse_args()

    tiles = gpd.read_parquet(DATA / "step3" / "tiles.parquet")
    out_dir = DATA / "step4" / "chips" / str(args.year)
    out_dir.mkdir(parents=True, exist_ok=True)
    log_path = DATA / "step4" / f"chips_{args.year}.csv"

    todo = tiles[~tiles["tile_id"].map(lambda t: (out_dir / f"{t}.npz").exists())]
    print(f"{len(tiles)} tiles, {len(tiles) - len(todo)} already saved, {len(todo)} to download ({args.year})")
    if todo.empty:
        return

    tiles_ll = todo.to_crs(4326)
    items = find_scenes(tiles_ll, args.year)
    best = assign_tiles(tiles_ll, items).merge(todo[["tile_id", "x0", "y0"]], on="tile_id")
    print(f"  {len(items)} NAIP scenes found; {len(best)} tiles fall inside them")

    tasks = []
    for scene_i, g in best.groupby("scene_i"):
        rows = list(g.itertuples(index=False))
        for k in range(0, len(rows), TILES_PER_TASK):
            tasks.append((items[scene_i], rows[k:k + TILES_PER_TASK]))

    results, failed, start = [], 0, time.time()
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = [pool.submit(read_tiles, item, rows, out_dir) for item, rows in tasks]
        for n, f in enumerate(as_completed(futures), 1):
            try:
                results += f.result()
            except Exception as e:     # a failed batch is simply retried on the next run
                failed += 1
                print(f"  batch failed ({e}); re-run the script to retry it")
            if n % 10 == 0 or n == len(futures):
                rate = len(results) / max(time.time() - start, 1)
                print(f"  {len(results)}/{len(best)} tiles  ({rate:.1f} tiles/s)")

    log = pd.DataFrame(results)
    if log_path.exists():
        log = pd.concat([pd.read_csv(log_path), log]).drop_duplicates("tile_id", keep="last")
    log.to_csv(log_path, index=False)

    print("\n================ Step 4a report (measured) ================")
    print(f"  year: {args.year}")
    print(f"  tiles saved in total: {len(log)} of {len(tiles)}")
    print(f"  failed batches this run: {failed}")
    print(f"  scene dates: {log['date'].value_counts().sort_index().to_dict()}")
    print(f"  pixel size (m): {log['pixel_m'].value_counts().to_dict()}")
    print(f"  tiles only partly covered by imagery (<99%): {int((log['coverage'] < 0.99).sum())}")


if __name__ == "__main__":
    main()
