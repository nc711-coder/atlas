"""Step 4b: rule-based pre-labels for every tile, then pick the 300 tiles you will label by hand.

Run from the project folder (after step 4a):  python pipeline/step4b_prelabel_and_sample.py

What it does
1. Measures, for each 60 m tile, the share covered by county building footprints, county parking lots,
   vegetation (NDVI from the 2025 image) and bare ground (bright, not vegetated, not building/parking).
2. Turns that into a rule-based "weak" label: built / parking / green / bare_or_construction / other.
   These weak labels train the classifier (step 5) and are the baseline it must beat.
   Caveat: the county shapes are older than the 2025 imagery, so some weak labels are out of date.
3. Splits the map into 600 m blocks, and picks 200 test + 100 held-out tiles from blocks that are
   never used for training, balanced across the weak-label classes so rare classes get tested.

Writes to <DATA>/step4/:
  tiles_weak.parquet   every tile: measured shares, weak label, block, split (train/test/heldout/eval_unused)
  to_label.csv         the 300 tiles to label, in random order (weak label NOT included, to avoid bias)
  previews/<tile_id>.png   the image of each of those 300 tiles
  step4b_report.json
"""
import json

import geopandas as gpd
import numpy as np
import pandas as pd
from rasterio.features import rasterize
from rasterio.transform import from_origin

from config import DATA, METRIC_CRS

STEP4 = DATA / "step4"
CHIPS = STEP4 / "chips" / "2025"
TILE_M, TILE_PX = 60, 200
BLOCK_M = 600
CLASSES = ["built", "parking", "green", "bare_or_construction", "other"]
N_TEST, N_HELDOUT = 200, 100
SEED = 42


def otsu_threshold(values, bins=256):
    hist, edges = np.histogram(values, bins=bins)
    centers = (edges[:-1] + edges[1:]) / 2
    w0 = np.cumsum(hist)
    w1 = w0[-1] - w0
    m0 = np.cumsum(hist * centers) / np.maximum(w0, 1)
    m1 = ((hist * centers).sum() - np.cumsum(hist * centers)) / np.maximum(w1, 1)
    return float(centers[np.argmax(w0 * w1 * (m0 - m1) ** 2)])


def load_chip(tile_id):
    return np.load(CHIPS / f"{tile_id}.npz")["img"].astype(np.float32)


def ndvi_and_brightness(img):
    red, green, blue, nir = img
    return (nir - red) / np.maximum(nir + red, 1e-6), (red + green + blue) / 3


def shapes_by_tile(tiles, layer):
    """For each tile, the layer's shapes that touch it."""
    hits = gpd.sjoin(layer[["geometry"]], tiles[["tile_id", "geometry"]], predicate="intersects", how="inner")
    return hits.groupby("tile_id").geometry.apply(list).to_dict()


def mask(geoms, x0, y0):
    if not geoms:
        return np.zeros((TILE_PX, TILE_PX), dtype=bool)
    transform = from_origin(x0, y0 + TILE_M, TILE_M / TILE_PX, TILE_M / TILE_PX)  # row 0 = north edge
    return rasterize(geoms, out_shape=(TILE_PX, TILE_PX), transform=transform).astype(bool)


def weak_label(b, p, v, s):
    if b >= 0.35:
        return "built"
    if p >= 0.35:
        return "parking"
    if v >= 0.50:
        return "green"
    if s >= 0.30:
        return "bare_or_construction"
    return "other"


def stratified_pick(pool, n, rng):
    """Up to n tiles, as even across weak-label classes as the pool allows."""
    picked = []
    per_class = n // len(CLASSES)
    for c in CLASSES:
        rows = pool[pool["weak_label"] == c]
        picked += list(rows.sample(min(per_class, len(rows)), random_state=rng).index)
    rest = pool.drop(index=picked)
    picked += list(rest.sample(min(n - len(picked), len(rest)), random_state=rng).index)
    return picked


def main():
    rng = np.random.RandomState(SEED)
    report = {}

    tiles = gpd.read_parquet(DATA / "step3" / "tiles.parquet")
    have = tiles["tile_id"].map(lambda t: (CHIPS / f"{t}.npz").exists())
    report["tiles_total"] = len(tiles)
    report["tiles_with_2025_chip"] = int(have.sum())
    if (~have).any():
        print(f"!! {int((~have).sum())} tiles have no 2025 chip yet (re-run step 4a to finish); they are skipped.")
    tiles = tiles[have].copy()
    chip_log = pd.read_csv(STEP4 / "chips_2025.csv")[["tile_id", "coverage"]]
    tiles = tiles.merge(chip_log, on="tile_id", how="left")

    # ---- thresholds from the data (a sample of chips), not guessed
    sample_ids = tiles["tile_id"].sample(min(300, len(tiles)), random_state=rng)
    nd, br = zip(*(ndvi_and_brightness(load_chip(t)) for t in sample_ids))
    nd, br = np.concatenate([a.ravel() for a in nd]), np.concatenate([a.ravel() for a in br])
    veg_t = otsu_threshold(nd)
    bright_t = otsu_threshold(br[nd <= veg_t])
    report["ndvi_threshold_otsu"] = round(veg_t, 4)
    report["brightness_threshold_otsu_nonveg"] = round(bright_t, 2)

    # ---- county shapes (only current buildings, and parking lots proper)
    buildings = gpd.read_parquet(DATA / "step2" / "raw" / "buildings_current.parquet").to_crs(METRIC_CRS)
    parking = gpd.read_parquet(DATA / "step2" / "raw" / "parking_current.parquet").to_crs(METRIC_CRS)
    parking = parking[parking["TYPE"].fillna("").str.upper().str.contains("PARKING LOT")]
    for layer in (buildings, parking):
        layer["geometry"] = layer.geometry.make_valid()
    b_by_tile, p_by_tile = shapes_by_tile(tiles, buildings), shapes_by_tile(tiles, parking)

    # ---- per-tile shares
    rows = []
    for n, t in enumerate(tiles.itertuples(index=False), 1):
        nd, br = ndvi_and_brightness(load_chip(t.tile_id))
        b = mask(b_by_tile.get(t.tile_id), t.x0, t.y0)
        p = mask(p_by_tile.get(t.tile_id), t.x0, t.y0) & ~b
        v = (nd > veg_t) & ~b & ~p
        s = (nd <= veg_t) & (br > bright_t) & ~b & ~p
        shares = dict(bldg_share=b.mean(), parking_share=p.mean(), veg_share=v.mean(), bare_share=s.mean())
        rows.append({"tile_id": t.tile_id, **{k: round(float(x), 3) for k, x in shares.items()},
                     "weak_label": weak_label(*shares.values())})
        if n % 1000 == 0:
            print(f"  measured {n}/{len(tiles)} tiles")
    tiles = tiles.merge(pd.DataFrame(rows), on="tile_id")
    report["weak_labels_all_tiles"] = {k: int(v) for k, v in tiles["weak_label"].value_counts().items()}

    # ---- spatial blocks -> train / eval; eval blocks -> test / heldout
    tiles["block"] = (tiles["submarket"] + "_" + (tiles["x0"] // BLOCK_M).astype(int).astype(str)
                      + "_" + (tiles["y0"] // BLOCK_M).astype(int).astype(str))
    blocks = np.array(sorted(tiles["block"].unique()))
    rng.shuffle(blocks)
    n_eval = int(round(len(blocks) * 0.30))
    test_blocks = set(blocks[: n_eval * 2 // 3])
    heldout_blocks = set(blocks[n_eval * 2 // 3: n_eval])
    tiles["split"] = np.where(tiles["block"].isin(test_blocks), "test_pool",
                     np.where(tiles["block"].isin(heldout_blocks), "heldout_pool", "train"))
    report["blocks"] = {"total": len(blocks), "test": len(test_blocks), "heldout": len(heldout_blocks)}

    full = tiles["coverage"].fillna(0) >= 0.99      # don't ask you to label half-empty tiles
    test_ids = stratified_pick(tiles[(tiles["split"] == "test_pool") & full], N_TEST, rng)
    held_ids = stratified_pick(tiles[(tiles["split"] == "heldout_pool") & full], N_HELDOUT, rng)
    tiles.loc[test_ids, "split"] = "test"
    tiles.loc[held_ids, "split"] = "heldout"
    tiles["split"] = tiles["split"].replace({"test_pool": "eval_unused", "heldout_pool": "eval_unused"})
    report["tiles_by_split"] = {k: int(v) for k, v in tiles["split"].value_counts().items()}
    report["weak_labels_in_train"] = {k: int(v) for k, v in tiles.loc[tiles.split == "train", "weak_label"].value_counts().items()}
    report["weak_labels_in_test"] = {k: int(v) for k, v in tiles.loc[tiles.split == "test", "weak_label"].value_counts().items()}
    report["weak_labels_in_heldout"] = {k: int(v) for k, v in tiles.loc[tiles.split == "heldout", "weak_label"].value_counts().items()}

    tiles.to_parquet(STEP4 / "tiles_weak.parquet")

    # ---- the 300 to label: random order, no weak label, with a map link for context
    lab = tiles[tiles["split"].isin(["test", "heldout"])].sample(frac=1, random_state=rng)
    centers = gpd.GeoSeries(lab.geometry.centroid, crs=METRIC_CRS).to_crs(4326)
    pd.DataFrame({"tile_id": lab["tile_id"], "lat": centers.y.round(6), "lng": centers.x.round(6)}).to_csv(
        STEP4 / "to_label.csv", index=False)

    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    prev = STEP4 / "previews"
    prev.mkdir(exist_ok=True)
    for t in lab["tile_id"]:
        rgb = np.load(CHIPS / f"{t}.npz")["img"][:3].transpose(1, 2, 0)
        plt.imsave(prev / f"{t}.png", rgb)

    (STEP4 / "step4b_report.json").write_text(json.dumps(report, indent=2))
    print("\n================ Step 4b report (measured) ================")
    for k, v in report.items():
        print(f"  {k}: {v}")
    print(f"\n{len(lab)} tiles ready to label. Next:  python pipeline\\step4c_label_server.py")


if __name__ == "__main__":
    main()
