"""Step 6: what changed between 2018 and 2025 (new buildings, cleared land, ...).

Run from the project folder (after downloading 2018 chips with step 4a --year 2018):
  python pipeline/step6_change_detection.py            classify 2018 tiles, flag changes, pick 150 pairs to check
  python pipeline/step4c_label_server.py --set change  label the pairs (2018 left, 2025 right)
  python pipeline/step6_change_detection.py --score    measure how good the change flags are

Rules (fixed before seeing any result)
- The step 5 model classifies every tile in 2018 as well as 2025.
- A tile is FLAGGED as changed when its class differs between the years AND both predictions have
  confidence >= 0.60. Type: new_building (-> built), vegetation_cleared (green -> anything else),
  other_change (any other class switch).
- Caveat: the model was trained on 2025 photos (0.3 m, August). 2018 photos are coarser and colour-shifted,
  so 2018 predictions may be less accurate. That is exactly why the flags get their own hand check.
- Check: 75 flagged + 75 unflagged tiles (random, full imagery coverage both years) shown side by side.
  Precision = share of flagged tiles that really changed; miss rate = share of unflagged that did.

Writes to <DATA>/step6/: features_2018.npz, change.parquet, to_label_change.csv (via step4),
change_report.json (with --score).
"""
import argparse
import json

import joblib
import numpy as np
import pandas as pd

from config import DATA, METRIC_CRS
from step5_train_classifier import CONFIDENCE_MIN, compute_features

STEP4, STEP5, OUT = DATA / "step4", DATA / "step5", DATA / "step6"
N_PER_GROUP = 75
SEED = 11


def classify_2018(ids, veg_t):
    feat_path = OUT / "features_2018.npz"
    if feat_path.exists():
        f = np.load(feat_path, allow_pickle=True)
        X, cached_ids = f["X"], list(f["ids"])
        if cached_ids == ids:
            return X
    print("Computing 2018 image features ...")
    X = compute_features(ids, veg_t, year=2018)
    np.savez_compressed(feat_path, X=X, ids=np.array(ids, dtype=object))
    return X


def change_type(c18, c25):
    if c18 == c25:
        return "none"
    if c25 == "built":
        return "new_building"
    if c18 == "green":
        return "vegetation_cleared"
    return "other_change"


def side_by_side(tile_id, path):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    a = np.load(STEP4 / "chips" / "2018" / f"{tile_id}.npz")["img"][:3].transpose(1, 2, 0)
    b = np.load(STEP4 / "chips" / "2025" / f"{tile_id}.npz")["img"][:3].transpose(1, 2, 0)
    gap = np.full((a.shape[0], 8, 3), 255, dtype=np.uint8)
    plt.imsave(path, np.concatenate([a, gap, b], axis=1))


def build(args):
    OUT.mkdir(parents=True, exist_ok=True)
    report4b = json.loads((STEP4 / "step4b_report.json").read_text())
    p25 = pd.read_parquet(STEP5 / "predictions_2025.parquet")
    log18 = pd.read_csv(STEP4 / "chips_2018.csv")[["tile_id", "coverage", "date", "pixel_m"]]
    log25 = pd.read_csv(STEP4 / "chips_2025.csv")[["tile_id", "coverage"]]
    have = set(log18["tile_id"]) & set(p25["tile_id"])
    ids = [t for t in p25["tile_id"] if t in have]
    if len(ids) < len(p25):
        print(f"!! {len(p25) - len(ids)} tiles have no 2018 chip (re-run step 4a --year 2018); they are skipped.")

    model = joblib.load(STEP5 / "model.joblib")
    X18 = classify_2018(ids, report4b["ndvi_threshold_otsu"])
    proba = model.predict_proba(X18)
    ch = pd.DataFrame({"tile_id": ids, "class_2018": model.classes_[proba.argmax(1)], "conf_2018": proba.max(1).round(4)})
    ch = ch.merge(p25[["tile_id", "pred", "confidence"]].rename(columns={"pred": "class_2025", "confidence": "conf_2025"}),
                  on="tile_id")
    ch["changed_flag"] = (ch["class_2018"] != ch["class_2025"]) & (ch["conf_2018"] >= CONFIDENCE_MIN) & (ch["conf_2025"] >= CONFIDENCE_MIN)
    ch["change_type"] = [change_type(a, b) if f else "none" for a, b, f in zip(ch["class_2018"], ch["class_2025"], ch["changed_flag"])]
    cov = log18.rename(columns={"coverage": "cov18"})[["tile_id", "cov18"]].merge(
        log25.rename(columns={"coverage": "cov25"}), on="tile_id")
    ch = ch.merge(cov, on="tile_id")
    ch.to_parquet(OUT / "change.parquet")

    full = (ch["cov18"] >= 0.99) & (ch["cov25"] >= 0.99)
    rng = np.random.RandomState(SEED)
    flagged = ch[full & ch["changed_flag"]]
    unflagged = ch[full & ~ch["changed_flag"]]
    pick = pd.concat([flagged.sample(min(N_PER_GROUP, len(flagged)), random_state=rng),
                      unflagged.sample(min(N_PER_GROUP, len(unflagged)), random_state=rng)]).sample(frac=1, random_state=rng)

    import geopandas as gpd
    tiles = gpd.read_parquet(DATA / "step3" / "tiles.parquet").set_index("tile_id").loc[pick["tile_id"]]
    centers = gpd.GeoSeries(tiles.geometry.centroid, crs=METRIC_CRS).to_crs(4326)
    pd.DataFrame({"tile_id": pick["tile_id"].values, "lat": centers.y.round(6).values,
                  "lng": centers.x.round(6).values}).to_csv(STEP4 / "to_label_change.csv", index=False)
    prev = STEP4 / "previews"
    prev.mkdir(exist_ok=True)
    for t in pick["tile_id"]:
        side_by_side(t, prev / f"change_{t}.png")
    pick[["tile_id", "changed_flag"]].to_csv(OUT / "change_sample_key.csv", index=False)

    print("\n================ Step 6 (build) report (measured) ================")
    print(f"  tiles compared: {len(ch)}   2018 photo dates: {log18['date'].value_counts().sort_index().to_dict()}"
          f"   2018 pixel size (m): {log18['pixel_m'].value_counts().to_dict()}")
    print(f"  flagged as changed: {int(ch['changed_flag'].sum())} ({ch['changed_flag'].mean():.1%})")
    print(f"  by type: {ch.loc[ch['changed_flag'], 'change_type'].value_counts().to_dict()}")
    print(f"  2018 predictions confident (>= {CONFIDENCE_MIN}): {(ch['conf_2018'] >= CONFIDENCE_MIN).mean():.1%}"
          f"   (2025: {(ch['conf_2025'] >= CONFIDENCE_MIN).mean():.1%})")
    print(f"  pairs to check: {len(pick)} ({int(pick['changed_flag'].sum())} flagged, {int((~pick['changed_flag']).sum())} not)")
    print("Next:  python pipeline\\step4c_label_server.py --set change")


def score():
    ch = pd.read_parquet(OUT / "change.parquet")
    key = pd.read_csv(OUT / "change_sample_key.csv")
    labels = pd.read_csv(STEP4 / "labels_change.csv")
    d = key.merge(labels, on="tile_id")
    if len(d) < len(key):
        raise SystemExit(f"Only {len(d)} of {len(key)} pairs labelled; finish labelling first.")
    d["real"] = d["label"] == "changed"
    f, u = d[d["changed_flag"]], d[~d["changed_flag"]]
    precision = f["real"].mean()
    miss_rate = u["real"].mean()
    n_flag, n_unflag = int(ch["changed_flag"].sum()), int((~ch["changed_flag"]).sum())
    # Estimated recall over ALL tiles: weight each sample by how many tiles it stands for.
    real_caught = precision * n_flag
    real_missed = miss_rate * n_unflag
    rep = {
        "pairs_labelled": len(d),
        "flagged_sample": len(f), "unflagged_sample": len(u),
        "precision_flagged_really_changed": round(float(precision), 4),
        "miss_rate_unflagged_actually_changed": round(float(miss_rate), 4),
        "estimated_recall_all_tiles": round(float(real_caught / (real_caught + real_missed)), 4) if (real_caught + real_missed) else None,
        "estimated_recall_note": "estimated from the two samples, weighted by the number of flagged/unflagged tiles",
        "tiles_flagged_total": n_flag, "tiles_unflagged_total": n_unflag,
        "precision_by_type": {k: {"precision": round(float(g["real"].mean()), 3), "n": len(g)} for k, g in
                              f.merge(ch[["tile_id", "change_type"]], on="tile_id").groupby("change_type")},
    }
    (OUT / "change_report.json").write_text(json.dumps(rep, indent=2))
    print("\n================ Step 6 change-check report (measured) ================")
    for k, v in rep.items():
        print(f"  {k}: {v}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--score", action="store_true")
    args = ap.parse_args()
    score() if args.score else build(args)


if __name__ == "__main__":
    main()
