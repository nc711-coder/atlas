"""Step 9c: stitch each hexagon's NAIP chips into one 2018 and one 2025 picture for the dashboard's
before/after view (our own processed imagery, not Google's).

Run from the project folder:  python pipeline/step9c_render_hex_imagery.py
Writes web/public/imagery/<year>/<hex>.jpg  (1 m per pixel; a ~0.1 km2 hexagon is roughly 450 x 450 px)

Each year gets one 2%-98% brightness stretch applied equally to all three bands, so the photo gets brighter
without its colour balance changing (display only; the classifier used the unstretched values).
"""
import geopandas as gpd
import h3
import numpy as np
from PIL import Image

from config import DATA, METRIC_CRS, ROOT

TILE_M = 60
PX_PER_TILE = 60            # 1 m per pixel
OUT = ROOT / "web" / "public" / "imagery"
YEARS = (2018, 2025)


def stretch_limits(chips_dir, tile_ids, sample=400):
    """2nd/98th percentile over all three bands of a sample of chips. The SAME limits are used for red,
    green and blue: stretching bands separately shifts the colour balance (e.g. trees turning purple)."""
    rng = np.random.default_rng(0)
    ids = rng.choice(tile_ids, min(sample, len(tile_ids)), replace=False)
    stack = np.stack([np.load(chips_dir / f"{t}.npz")["img"][:3] for t in ids])
    lo, hi = np.percentile(stack, 2), np.percentile(stack, 98)
    return np.full(3, lo), np.full(3, hi)


def main():
    tiles = gpd.read_parquet(DATA / "step3" / "tiles.parquet")
    centres = gpd.GeoSeries(tiles.geometry.centroid, crs=METRIC_CRS).to_crs(4326)
    tiles["hex"] = [h3.latlng_to_cell(p.y, p.x, 9) for p in centres]

    for year in YEARS:
        chips = DATA / "step4" / "chips" / str(year)
        have = tiles[tiles["tile_id"].map(lambda t: (chips / f"{t}.npz").exists())]
        lo, hi = stretch_limits(chips, have["tile_id"].to_numpy())
        out = OUT / str(year)
        out.mkdir(parents=True, exist_ok=True)
        n = 0
        for hx, g in have.groupby("hex"):
            x0, y0 = g["x0"].min(), g["y0"].min()
            w = int((g["x0"].max() - x0) / TILE_M + 1) * PX_PER_TILE
            h = int((g["y0"].max() - y0) / TILE_M + 1) * PX_PER_TILE
            canvas = Image.new("RGB", (w, h), (225, 224, 220))   # light grey outside the study area
            for t in g.itertuples(index=False):
                rgb = np.load(chips / f"{t.tile_id}.npz")["img"][:3].astype(np.float32)
                rgb = np.clip((rgb - lo[:, None, None]) / (hi - lo)[:, None, None], 0, 1) * 255
                im = Image.fromarray(rgb.transpose(1, 2, 0).astype(np.uint8)).resize(
                    (PX_PER_TILE, PX_PER_TILE), Image.LANCZOS)
                px = int((t.x0 - x0) / TILE_M) * PX_PER_TILE
                py = h - int((t.y0 - y0) / TILE_M + 1) * PX_PER_TILE      # image rows run north -> south
                canvas.paste(im, (px, py))
            canvas.save(out / f"{hx}.jpg", quality=80, optimize=True)
            n += 1
        size = sum(p.stat().st_size for p in out.glob("*.jpg")) / 1e6
        print(f"{year}: {n} hexagon images, {size:.1f} MB")


if __name__ == "__main__":
    main()
