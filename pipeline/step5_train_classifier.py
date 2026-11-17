"""Step 5: train the land-cover classifier on weak labels and score it on your hand labels.

Run from the project folder:
  python pipeline/step5_train_classifier.py           train + score on the TEST tiles you labelled
  python pipeline/step5_train_classifier.py --final   score once on the 100 HELD-OUT tiles (run this last)

How it works (all choices fixed before looking at any test label)
1. Better weak labels ("v2"): step 4b's rules plus the county road layer, so roads stop
   being mistaken for bare ground.
2. Features per tile: a pretrained image model (ResNet-18, ImageNet weights) turns the RGB chip into
   512 numbers; 10 simple measures add vegetation (NDVI), brightness, near-infrared and edge density.
3. Model: logistic regression. Its one setting (C) is picked by cross-validation on the TRAINING
   blocks against weak labels. Test and held-out tiles are never used for training or tuning.
4. Scores on your labels: accuracy and macro-F1 with 95% bootstrap intervals, per-class precision/
   recall, and the same scores for both rule-based baselines (v1 from step 4b, v2 with roads).
5. Confidence below 0.60 marks a tile "needs review" (a person checks it). The report shows how
   accurate confident vs flagged tiles were.

Round 2 (decided after round 1's test results, before any round-2 result; documented in the README):
  a. "bare_or_construction" is merged into "other": you labelled 0 of 200 test tiles as bare, while the
     rules called 994 tiles bare (mostly roads). Construction is detected by change over time in step 6.
  b. Rule labels are used for training only where they are clear-cut (e.g. >= 50% building footprint).
  c. ~250 hand-labelled TRAINING tiles (step 5a, training blocks only) are added, each weighted 3x.
  All scores are on 4 classes; round 1's 5-class report stays in test_report.json for the record.

Writes to <DATA>/step5/: features_2025.npz, model.joblib, predictions_2025.parquet,
test_report_r2.json, confusion_test_r2.png, and (with --final) heldout_report.json.
"""
import argparse
import json

import geopandas as gpd
import joblib
import numpy as np
import pandas as pd
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import accuracy_score, confusion_matrix, f1_score, precision_recall_fscore_support
from sklearn.model_selection import GroupKFold
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

from config import DATA, METRIC_CRS, SUBMARKETS
from step2b_property_data import fetch_map_layer
from step3_study_area import get_json, BASE
from step4b_prelabel_and_sample import mask, ndvi_and_brightness, shapes_by_tile

STEP4, OUT = DATA / "step4", DATA / "step5"
CONFIDENCE_MIN = 0.60       # below this, a prediction goes to a person
ROUND = 2
CLASSES = ["built", "parking", "green", "other"]   # round 2: bare_or_construction merged into other
HUMAN_WEIGHT = 3.0          # a hand label counts 3x a rule label (fixed in advance, not tuned)


def merge_bare(x):
    """Round 2 uses 4 classes: bare ground/construction counts as 'other'."""
    return pd.Series(x).replace({"bare_or_construction": "other"}).to_numpy()


def clear_cut(t):
    """Rule labels we trust enough to train on: the winning share is clearly dominant."""
    return (((t["weak_v2"] == "built") & (t["bldg_share"] >= 0.5))
            | ((t["weak_v2"] == "parking") & (t["parking_share"] >= 0.5))
            | ((t["weak_v2"] == "green") & (t["veg_share"] >= 0.65))
            | ((t["weak_v2"] == "other") & (t["road_share"] >= 0.5)))
ROAD_SERVICES = ["Roadways", "Roadways_and_Bridges"]
LINE_BUFFER_M = 6           # if the road layer is centre-lines, widen them to ~a 2-lane road
SEED = 42


# ---------------------------------------------------------------- 1. roads and weak labels v2

def road_shapes(refresh=False):
    """County road shapes for the three submarkets (cached). Handles polygon or line layers."""
    path = DATA / "step2" / "raw" / "roads.parquet"
    if path.exists() and not refresh:
        return gpd.read_parquet(path)
    for service in ROAD_SERVICES:
        try:
            meta = get_json(f"{BASE}/{service}/FeatureServer")
            parts = []
            for layer in meta.get("layers", []):
                for market, bbox in SUBMARKETS.items():
                    gdf = fetch_map_layer(service, bbox, layer=layer["id"])
                    if not gdf.empty:
                        parts.append(gdf[["geometry"] + [c for c in ("TYPE",) if c in gdf]])
            if not parts:
                raise ValueError("no features")
            roads = gpd.GeoDataFrame(pd.concat(parts, ignore_index=True), geometry="geometry", crs=4326)
            kinds = roads.geom_type.value_counts().to_dict()
            print(f"  road layer: {service}, shapes by type: {kinds}")
            roads = roads.to_crs(METRIC_CRS)
            lines = roads.geom_type.str.contains("Line")
            roads.loc[lines, "geometry"] = roads.loc[lines].buffer(LINE_BUFFER_M)
            roads = roads[roads.geom_type.str.contains("Polygon")].to_crs(4326)
            roads["source_service"] = service
            roads.to_parquet(path)
            return roads
        except Exception as e:
            print(f"  road service {service} not usable ({e})")
    raise SystemExit("No county road layer could be downloaded; tell Claude the messages above.")


def weak_label_v2(b, p, r, v, s):
    if b >= 0.35:
        return "built"
    if p >= 0.35:
        return "parking"
    if r >= 0.35:
        return "other"           # roads
    if v >= 0.50:
        return "green"
    if s >= 0.30:
        return "bare_or_construction"
    return "other"


def chip(tile_id, year=2025):
    return np.load(STEP4 / "chips" / str(year) / f"{tile_id}.npz")["img"].astype(np.float32)


def build_weak_v2(tiles, report):
    veg_t = report["ndvi_threshold_otsu"]
    bright_t = report["brightness_threshold_otsu_nonveg"]
    buildings = gpd.read_parquet(DATA / "step2" / "raw" / "buildings_current.parquet").to_crs(METRIC_CRS)
    parking = gpd.read_parquet(DATA / "step2" / "raw" / "parking_current.parquet").to_crs(METRIC_CRS)
    parking = parking[parking["TYPE"].fillna("").str.upper().str.contains("PARKING LOT")]
    roads = road_shapes().to_crs(METRIC_CRS)
    for layer in (buildings, parking, roads):
        layer["geometry"] = layer.geometry.make_valid()
    by_b, by_p, by_r = (shapes_by_tile(tiles, x) for x in (buildings, parking, roads))
    rows = []
    for t in tiles.itertuples(index=False):
        nd, br = ndvi_and_brightness(chip(t.tile_id))
        b = mask(by_b.get(t.tile_id), t.x0, t.y0)
        p = mask(by_p.get(t.tile_id), t.x0, t.y0) & ~b
        r = mask(by_r.get(t.tile_id), t.x0, t.y0) & ~b & ~p
        v = (nd > veg_t) & ~b & ~p & ~r
        s = (nd <= veg_t) & (br > bright_t) & ~b & ~p & ~r
        shares = [float(x.mean()) for x in (b, p, r, v, s)]
        rows.append({"tile_id": t.tile_id, "road_share": round(shares[2], 3), "weak_v2": weak_label_v2(*shares)})
    return tiles.merge(pd.DataFrame(rows), on="tile_id")


# ---------------------------------------------------------------- 2. features

def simple_features(img, veg_t):
    red, green, blue, nir = img
    nd, br = ndvi_and_brightness(img)
    gy, gx = np.gradient(br)
    edges = np.hypot(gx, gy)
    return [nd.mean(), nd.std(), (nd > veg_t).mean(), br.mean(), br.std(), nir.mean(),
            red.mean() - blue.mean(), green.mean() - red.mean(), edges.mean(), (edges > 20).mean()]


def compute_features(tile_ids, veg_t, year=2025, pretrained=True):
    import torch
    import torch.nn.functional as F
    import torchvision

    weights = torchvision.models.ResNet18_Weights.IMAGENET1K_V1 if pretrained else None
    model = torchvision.models.resnet18(weights=weights)
    model.fc = torch.nn.Identity()
    model.eval()
    mean = torch.tensor([0.485, 0.456, 0.406]).view(1, 3, 1, 1)
    std = torch.tensor([0.229, 0.224, 0.225]).view(1, 3, 1, 1)
    deep, simple = [], []
    with torch.no_grad():
        for k in range(0, len(tile_ids), 64):
            imgs = [chip(t, year) for t in tile_ids[k:k + 64]]
            simple += [simple_features(im, veg_t) for im in imgs]
            x = torch.from_numpy(np.stack([im[:3] for im in imgs]) / 255.0).float()
            x = F.interpolate(x, size=224, mode="bilinear", align_corners=False)
            deep.append(model((x - mean) / std).numpy())
            if (k // 64) % 10 == 0:
                print(f"  features {min(k + 64, len(tile_ids))}/{len(tile_ids)}")
    return np.hstack([np.vstack(deep), np.array(simple, dtype=np.float32)])


# ---------------------------------------------------------------- 3-5. train and score

def make_model(C):
    return make_pipeline(StandardScaler(),
                         LogisticRegression(C=C, max_iter=3000, class_weight="balanced"))


def pick_C(X, y, groups, w):
    """Cross-validate on training blocks only (against the training labels)."""
    scores = {}
    for C in (0.01, 0.1, 1.0):
        fold = []
        for tr, va in GroupKFold(n_splits=5).split(X, y, groups):
            m = make_model(C).fit(X[tr], y[tr], logisticregression__sample_weight=w[tr])
            fold.append(f1_score(y[va], m.predict(X[va]), average="macro", sample_weight=w[va]))
        scores[C] = round(float(np.mean(fold)), 4)
    return max(scores, key=scores.get), scores


def bootstrap_ci(y_true, y_pred, metric, n=1000, seed=SEED):
    rng = np.random.default_rng(seed)
    idx = np.arange(len(y_true))
    vals = [metric(y_true[s], y_pred[s]) for s in (rng.choice(idx, len(idx)) for _ in range(n))]
    return [round(float(np.percentile(vals, 2.5)), 4), round(float(np.percentile(vals, 97.5)), 4)]


def score(y_true, y_pred):
    y_true, y_pred = merge_bare(y_true), merge_bare(y_pred)
    macro = lambda a, b: f1_score(a, b, average="macro", labels=CLASSES, zero_division=0)
    p, r, f, sup = precision_recall_fscore_support(y_true, y_pred, labels=CLASSES, zero_division=0)
    return {
        "n": len(y_true),
        "accuracy": round(float(accuracy_score(y_true, y_pred)), 4),
        "accuracy_95ci": bootstrap_ci(y_true, y_pred, accuracy_score),
        "macro_f1": round(float(macro(y_true, y_pred)), 4),
        "macro_f1_95ci": bootstrap_ci(y_true, y_pred, macro),
        "per_class": {c: {"precision": round(float(p[i]), 3), "recall": round(float(r[i]), 3),
                          "f1": round(float(f[i]), 3), "support": int(sup[i])} for i, c in enumerate(CLASSES)},
        "confusion_rows_true_cols_pred": confusion_matrix(y_true, y_pred, labels=CLASSES).tolist(),
    }


def evaluate(split, tiles, preds, labels):
    lab = labels.merge(tiles[["tile_id", "split", "weak_label", "weak_v2"]], on="tile_id")
    lab = lab[lab["split"] == split].merge(preds, on="tile_id")
    if lab.empty:
        raise SystemExit(f"No labelled {split} tiles found yet.")
    rep = {"split": split, "round": ROUND, "classes": CLASSES, "labelled_tiles": len(lab),
           "model": score(lab["label"], lab["pred"]),
           "baseline_rules_v1": score(lab["label"], lab["weak_label"]),
           "baseline_rules_v2_with_roads": score(lab["label"], lab["weak_v2"])}
    conf = lab["confidence"] >= CONFIDENCE_MIN
    rep["confidence_threshold"] = CONFIDENCE_MIN
    rep["flagged_for_review_share"] = round(float((~conf).mean()), 4)
    rep["accuracy_confident"] = round(float((lab.loc[conf, "label"] == lab.loc[conf, "pred"]).mean()), 4) if conf.any() else None
    rep["accuracy_flagged"] = round(float((lab.loc[~conf, "label"] == lab.loc[~conf, "pred"]).mean()), 4) if (~conf).any() else None
    rep["human_label_counts"] = {k: int(v) for k, v in lab["label"].value_counts().items()}
    rep["human_label_counts_merged"] = {k: int(v) for k, v in pd.Series(merge_bare(lab["label"])).value_counts().items()}
    return rep, lab


def save_confusion_png(rep, path):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    cm = np.array(rep["model"]["confusion_rows_true_cols_pred"])
    fig, ax = plt.subplots(figsize=(6.5, 5.5))
    ax.imshow(cm, cmap="Blues")
    ax.set_xticks(range(len(CLASSES)), CLASSES, fontsize=9)
    ax.set_yticks(range(len(CLASSES)), CLASSES, fontsize=9)
    ax.set_xlabel("model says")
    ax.set_ylabel("your label")
    for i in range(len(CLASSES)):
        for j in range(len(CLASSES)):
            ax.text(j, i, cm[i, j], ha="center", va="center", color="white" if cm[i, j] > cm.max() / 2 else "black")
    ax.set_title(f"{rep['split']} tiles (n={rep['labelled_tiles']}, round {rep['round']}): accuracy {rep['model']['accuracy']:.1%}")
    fig.tight_layout()
    fig.savefig(path, dpi=120)
    plt.close(fig)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--final", action="store_true", help="score the held-out tiles (once, at the very end)")
    ap.add_argument("--rerun-final", action="store_true", help="allow --final again (it will be recorded)")
    args = ap.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)

    report4b = json.loads((STEP4 / "step4b_report.json").read_text())
    tiles = gpd.read_parquet(STEP4 / "tiles_weak.parquet")
    labels = pd.read_csv(STEP4 / "labels.csv") if (STEP4 / "labels.csv").exists() else pd.DataFrame(columns=["tile_id", "label"])

    if args.final:
        held = OUT / "heldout_report.json"
        if held.exists() and not args.rerun_final:
            raise SystemExit("The held-out set was already scored (heldout_report.json). Re-scoring it after "
                             "changes would turn it into a tuning set. Use --rerun-final only if you accept that.")
        preds = pd.read_parquet(OUT / "predictions_2025.parquet")
        tiles = tiles.merge(pd.read_parquet(OUT / "weak_v2.parquet"), on="tile_id")
        n_held = int((tiles["split"] == "heldout").sum())
        rep, _ = evaluate("heldout", tiles, preds, labels)
        if rep["labelled_tiles"] < n_held:
            raise SystemExit(f"Only {rep['labelled_tiles']} of {n_held} held-out tiles are labelled; finish labelling first.")
        rep["runs"] = (json.loads(held.read_text()).get("runs", 0) if held.exists() else 0) + 1
        held.write_text(json.dumps(rep, indent=2))
        save_confusion_png(rep, OUT / "confusion_heldout.png")
        print_report(rep)
        return

    # ---- 1. weak labels v2
    v2_path = OUT / "weak_v2.parquet"
    if v2_path.exists():
        tiles = tiles.merge(pd.read_parquet(v2_path), on="tile_id")
    else:
        print("Building weak labels v2 (with roads) ...")
        tiles = build_weak_v2(tiles, report4b)
        tiles[["tile_id", "road_share", "weak_v2"]].to_parquet(v2_path)

    # ---- 2. features (cached)
    feat_path = OUT / "features_2025.npz"
    if feat_path.exists():
        f = np.load(feat_path, allow_pickle=True)
        X_all, ids = f["X"], list(f["ids"])
    else:
        print("Computing image features (first run downloads the ResNet-18 weights, ~45 MB) ...")
        ids = tiles["tile_id"].tolist()
        X_all = compute_features(ids, report4b["ndvi_threshold_otsu"])
        np.savez_compressed(feat_path, X=X_all, ids=np.array(ids, dtype=object))
    tiles = tiles.set_index("tile_id").loc[ids].reset_index()

    # ---- 3. train on training blocks only
    # training set = clear-cut rule labels + your hand-labelled training tiles (which override the rule)
    train_lab_path = STEP4 / "labels_train.csv"
    hand = pd.read_csv(train_lab_path) if train_lab_path.exists() else pd.DataFrame(columns=["tile_id", "label"])
    hand_split = tiles.set_index("tile_id").loc[hand["tile_id"], "split"] if len(hand) else pd.Series(dtype=str)
    if (hand_split != "train").any():
        raise SystemExit("labels_train.csv contains tiles outside the training blocks; something is wrong.")
    is_train = tiles["split"] == "train"
    hand_map = dict(zip(hand["tile_id"], merge_bare(hand["label"])))
    tiles["train_label"] = None
    use_rule = is_train & clear_cut(tiles) & ~tiles["tile_id"].isin(hand_map)
    tiles.loc[use_rule, "train_label"] = tiles.loc[use_rule, "weak_v2"]
    tiles.loc[tiles["tile_id"].isin(hand_map), "train_label"] = tiles["tile_id"].map(hand_map)
    tiles["train_weight"] = np.where(tiles["tile_id"].isin(hand_map), HUMAN_WEIGHT, 1.0)
    tr = tiles["train_label"].notna().to_numpy()
    X_tr, y_tr = X_all[tr], tiles.loc[tr, "train_label"].astype(str).to_numpy()
    g_tr, w_tr = tiles.loc[tr, "block"].to_numpy(), tiles.loc[tr, "train_weight"].to_numpy()
    C, cv_scores = pick_C(X_tr, y_tr, g_tr, w_tr)
    model = make_model(C).fit(X_tr, y_tr, logisticregression__sample_weight=w_tr)
    joblib.dump(model, OUT / "model.joblib")

    proba = model.predict_proba(X_all)
    preds = pd.DataFrame({"tile_id": ids,
                          "pred": model.classes_[proba.argmax(1)],
                          "confidence": proba.max(1).round(4)})
    for k, c in enumerate(model.classes_):
        preds[f"p_{c}"] = proba[:, k].round(4)
    preds["needs_review"] = preds["confidence"] < CONFIDENCE_MIN
    preds.to_parquet(OUT / "predictions_2025.parquet")

    # ---- 4-5. score on your TEST labels
    rep, _ = evaluate("test", tiles, preds, labels)
    rep["training"] = {"tiles": int(tr.sum()), "hand_labelled": int(len(hand_map)),
                       "clear_cut_rule_labelled": int(use_rule.sum()),
                       "rule_labelled_train_tiles_not_used": int((is_train & ~use_rule & ~tiles["tile_id"].isin(hand_map)).sum()),
                       "label_counts": pd.Series(y_tr).value_counts().to_dict(), "hand_weight": HUMAN_WEIGHT,
                       "chosen_C": C, "cv_macro_f1_vs_training_labels": cv_scores,
                       "features": int(X_all.shape[1])}
    rep["weak_v1_vs_v2_all_tiles"] = pd.crosstab(tiles["weak_label"], tiles["weak_v2"]).to_dict()
    n_test = int((tiles["split"] == "test").sum())
    if rep["labelled_tiles"] < n_test:
        rep["note"] = f"PARTIAL: only {rep['labelled_tiles']} of {n_test} test tiles labelled so far"
    if len(hand_map) == 0:
        rep["note"] = (rep.get("note", "") + " NO hand-labelled training tiles found (labels_train.csv)").strip()
    (OUT / f"test_report_r{ROUND}.json").write_text(json.dumps(rep, indent=2, default=str))
    save_confusion_png(rep, OUT / f"confusion_test_r{ROUND}.png")
    print_report(rep)


def print_report(rep):
    print(f"\n================ Step 5 report: {rep['split'].upper()} tiles, round {rep['round']}, {len(rep['classes'])} classes (measured) ================")
    if rep.get("note"):
        print("  !!", rep["note"])
    print(f"  labelled tiles scored: {rep['labelled_tiles']}   your labels (bare merged into other): {rep['human_label_counts_merged']}")
    for name in ("model", "baseline_rules_v2_with_roads", "baseline_rules_v1"):
        s = rep[name]
        print(f"  {name:30s} accuracy {s['accuracy']:.1%} {s['accuracy_95ci']}   macro-F1 {s['macro_f1']:.3f} {s['macro_f1_95ci']}")
    print("  model per class (precision / recall / support):")
    for c, v in rep["model"]["per_class"].items():
        print(f"    {c:22s} {v['precision']:.2f} / {v['recall']:.2f} / {v['support']}")
    print(f"  flagged for review (confidence < {rep['confidence_threshold']}): {rep['flagged_for_review_share']:.1%}"
          f"   accuracy when confident: {rep['accuracy_confident']}   when flagged: {rep['accuracy_flagged']}")
    if "training" in rep:
        t = rep["training"]
        print(f"  trained on {t['tiles']} tiles ({t['hand_labelled']} hand-labelled x{t['hand_weight']}, "
              f"{t['clear_cut_rule_labelled']} clear-cut rule labels; {t['rule_labelled_train_tiles_not_used']} unclear ones left out)")
        print(f"  C={t['chosen_C']} (CV macro-F1 on training blocks {t['cv_macro_f1_vs_training_labels']})")


if __name__ == "__main__":
    main()
