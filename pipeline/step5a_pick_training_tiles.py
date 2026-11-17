"""Step 5a (round 2): pick ~250 TRAINING tiles for you to label by hand.

Run from the project folder:  python pipeline/step5a_pick_training_tiles.py
Then label them:               python pipeline/step4c_label_server.py --set train

These tiles come only from training blocks (never the test/held-out neighbourhoods), balanced across
the rule-based labels so every kind of tile is represented. Writes <DATA>/step4/to_label_train.csv.
"""
import geopandas as gpd
import numpy as np
import pandas as pd

from config import DATA, METRIC_CRS

STEP4, STEP5 = DATA / "step4", DATA / "step5"
N_TRAIN_LABELS = 250
SEED = 7     # different from step 4b's seed, so this is a fresh draw


def main():
    rng = np.random.RandomState(SEED)
    tiles = gpd.read_parquet(STEP4 / "tiles_weak.parquet").merge(pd.read_parquet(STEP5 / "weak_v2.parquet"), on="tile_id")
    pool = tiles[(tiles["split"] == "train") & (tiles["coverage"].fillna(0) >= 0.99)]
    classes = sorted(pool["weak_v2"].unique())
    per = N_TRAIN_LABELS // len(classes)
    picked = []
    for c in classes:
        rows = pool[pool["weak_v2"] == c]
        picked += list(rows.sample(min(per, len(rows)), random_state=rng).index)
    rest = pool.drop(index=picked)
    picked += list(rest.sample(min(N_TRAIN_LABELS - len(picked), len(rest)), random_state=rng).index)
    lab = pool.loc[picked].sample(frac=1, random_state=rng)

    centers = gpd.GeoSeries(lab.geometry.centroid, crs=METRIC_CRS).to_crs(4326)
    pd.DataFrame({"tile_id": lab["tile_id"], "lat": centers.y.round(6), "lng": centers.x.round(6)}).to_csv(
        STEP4 / "to_label_train.csv", index=False)

    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    prev = STEP4 / "previews"
    prev.mkdir(exist_ok=True)
    for t in lab["tile_id"]:
        rgb = np.load(STEP4 / "chips" / "2025" / f"{t}.npz")["img"][:3].transpose(1, 2, 0)
        plt.imsave(prev / f"{t}.png", rgb)

    print(f"{len(lab)} training tiles picked from {lab['block'].nunique()} training blocks "
          f"(rule-label mix: {lab['weak_v2'].value_counts().to_dict()})")
    print("Next:  python pipeline\\step4c_label_server.py --set train")


if __name__ == "__main__":
    main()
