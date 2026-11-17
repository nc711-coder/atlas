"""Step 1: download one NAIP aerial image of Tysons and make a vegetation (NDVI) image.

Run from the project folder:  python pipeline/step1_first_imagery.py
Writes to data/step1/: tysons_rgb.png, tysons_ndvi.png, scene.json
"""
import json
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
import planetary_computer
import pystac_client
import rasterio
from rasterio.warp import transform_bounds
from rasterio.windows import Window, from_bounds

# Small box around Tysons (west, south, east, north) in longitude/latitude, about 1.5 x 1.5 km.
BBOX = (-77.240, 38.912, -77.222, 38.926)
STAC_URL = "https://planetarycomputer.microsoft.com/api/stac/v1"
OUT = Path(__file__).resolve().parent.parent / "data" / "step1"


def find_scenes():
    """Ask the Planetary Computer catalog which NAIP scenes cover our box."""
    catalog = pystac_client.Client.open(STAC_URL, modifier=planetary_computer.sign_inplace)
    items = list(catalog.search(collections=["naip"], bbox=BBOX).items())
    if not items:
        raise SystemExit("No NAIP scenes found for this box.")
    return items


def pick_newest(items):
    """Newest year, and a scene that contains the centre of our box."""
    newest = max(it.datetime.year for it in items)
    cx, cy = (BBOX[0] + BBOX[2]) / 2, (BBOX[1] + BBOX[3]) / 2
    for it in items:
        w, s, e, n = it.bbox
        if it.datetime.year == newest and w <= cx <= e and s <= cy <= n:
            return it
    raise SystemExit(f"No {newest} scene contains the centre of the box.")


def read_box(href):
    """Read only our box out of the (large) image file, straight from the cloud."""
    with rasterio.open(href) as src:
        bounds = transform_bounds("EPSG:4326", src.crs, *BBOX)
        window = from_bounds(*bounds, transform=src.transform)
        window = window.round_offsets().round_lengths()
        window = window.intersection(Window(0, 0, src.width, src.height))  # stay inside the scene
        bands = src.read(window=window)  # shape: (4 bands, rows, cols) -> red, green, blue, near-infrared
        return bands, src.res[0], src.crs.to_string()


def otsu_threshold(values, bins=256):
    """Otsu's method: the threshold that maximises the separation between the two groups."""
    hist, edges = np.histogram(values, bins=bins)
    centers = (edges[:-1] + edges[1:]) / 2
    w0 = np.cumsum(hist)                      # pixels at or below each cut
    w1 = w0[-1] - w0                          # pixels above it
    m0 = np.cumsum(hist * centers) / np.maximum(w0, 1)
    m1 = ((hist * centers).sum() - np.cumsum(hist * centers)) / np.maximum(w1, 1)
    between = w0 * w1 * (m0 - m1) ** 2
    return float(centers[np.argmax(between)])


def main():
    OUT.mkdir(parents=True, exist_ok=True)

    items = find_scenes()
    years = sorted({it.datetime.year for it in items})
    print("NAIP years covering this box:", years)

    item = pick_newest(items)
    print("Using scene:", item.id, "taken", item.datetime.date())

    bands, pixel_m, crs = read_box(item.assets["image"].href)
    red, green, blue, nir = bands.astype("float32")
    print(f"Image size: {bands.shape[2]} x {bands.shape[1]} pixels, {pixel_m:.2f} m per pixel")

    # Normal colour picture.
    rgb = np.dstack([bands[0], bands[1], bands[2]])
    plt.imsave(OUT / "tysons_rgb.png", rgb)

    # NDVI: plants reflect lots of near-infrared and little red, so (NIR - red) / (NIR + red) is high on vegetation.
    ndvi = (nir - red) / np.maximum(nir + red, 1e-6)

    # NAIP values are raw camera brightness, not calibrated reflectance, so NDVI comes out lower
    # than textbook values. Stretch the colours to this image's own range instead of a fixed one.
    p2, p50, p98 = np.percentile(ndvi, [2, 50, 98])
    print(f"NDVI percentiles: 2%={p2:.3f}  median={p50:.3f}  98%={p98:.3f}")
    plt.imsave(OUT / "tysons_ndvi.png", ndvi, cmap="RdYlGn", vmin=p2, vmax=p98)

    # Pick the vegetation threshold from the data (Otsu's method: the cut that best splits the
    # histogram into two groups) rather than guessing one. Step 4's labels will check it.
    veg_threshold = otsu_threshold(ndvi)
    veg_share = float((ndvi > veg_threshold).mean())
    print(f"Vegetation threshold (Otsu): {veg_threshold:.3f}")
    print(f"Share of pixels above it (vegetation, unverified until step 4): {veg_share:.1%}")

    (OUT / "scene.json").write_text(json.dumps({
        "scene_id": item.id,
        "date": str(item.datetime.date()),
        "years_available": years,
        "bbox_lonlat": BBOX,
        "crs": crs,
        "pixel_size_m": pixel_m,
        "size_px": [int(bands.shape[2]), int(bands.shape[1])],
        "ndvi_percentiles_2_50_98": [round(float(p2), 4), round(float(p50), 4), round(float(p98), 4)],
        "veg_threshold_otsu": round(veg_threshold, 4),
        "veg_share_above_threshold": round(veg_share, 4),
    }, indent=2))
    print("Saved images to", OUT)


if __name__ == "__main__":
    main()
