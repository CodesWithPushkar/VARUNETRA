import argparse
import re, json, csv, numpy as np, rasterio
from pathlib import Path
from rasterio.windows import Window
from rasterio.features import shapes
from scipy import ndimage as ndi
from global_land_mask import globe
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

from infer_test import load_model, normalize, predict, THRESH, SCENE as DEFAULT_SCENE

parser = argparse.ArgumentParser(description="Detect oil slick polygons in a preprocessed SAR GeoTIFF.")
parser.add_argument("scene", nargs="?", default=DEFAULT_SCENE, help="Input two-band SAR GeoTIFF")
parser.add_argument("--scene-time", help="Scene acquisition time (ISO 8601 or YYYYMMDDTHHMMSS)")
args = parser.parse_args()
SCENE = args.scene
SCRIPT_DIR = Path(__file__).resolve().parent

BLOCK = 2048
MIN_VALID = 0.30
MIN_PIXELS = 500
LAND_STEP = 16
LAND_BUFFER_ITERS = 10
OUTPUT_SUFFIX = "_median_fill"

scene_match = re.search(r"\d{8}T\d{6}", Path(SCENE).name)
scene_time = args.scene_time or (scene_match.group(0) if scene_match else None)
if scene_time is None:
    parser.error("Pass --scene-time when the input filename does not contain a Sentinel scene timestamp.")


def land_mask(T, r0, c0, h, w):
    rows = np.arange(0, h, LAND_STEP) + r0 + 0.5
    cols = np.arange(0, w, LAND_STEP) + c0 + 0.5
    cc, rr = np.meshgrid(cols, rows)
    lon = T.c + cc * T.a
    lat = T.f + rr * T.e
    small = globe.is_land(lat, lon)
    small = ndi.binary_dilation(small, iterations=LAND_BUFFER_ITERS)
    big = np.kron(small.astype(np.uint8),
                  np.ones((LAND_STEP, LAND_STEP), np.uint8)).astype(bool)
    return big[:h, :w]


model = load_model()
features, rows_out = [], []
blocks_done = 0

with rasterio.open(SCENE) as src:
    H, W, T = src.height, src.width, src.transform
    for r0 in range(0, H, BLOCK):
        for c0 in range(0, W, BLOCK):
            h, w = min(BLOCK, H - r0), min(BLOCK, W - c0)
            if h < 512 or w < 512:
                continue
            win = Window(c0, r0, w, h)
            arr = src.read(window=win).astype("float32")
            nodata = (~np.isfinite(arr)).any(axis=0) | (arr == 0).any(axis=0)
            if (~nodata).mean() < MIN_VALID:
                continue
            invalid = nodata | land_mask(T, r0, c0, h, w)
            if (~invalid).mean() < MIN_VALID:
                continue

            arr[:, invalid] = np.nan
            x, valid = normalize(arr)
            for c in range(2):
                x[c][~valid] = np.median(x[c][valid])
            prob = predict(model, x)
            pred = (prob > THRESH) & valid
            blocks_done += 1

            lab, n = ndi.label(pred)
            sizes = ndi.sum(pred, lab, range(1, n + 1)) if n else []
            found = 0
            win_T = src.window_transform(win)
            for i, s in enumerate(sizes, start=1):
                if s < MIN_PIXELS:
                    continue
                m = lab == i
                ys, xs = np.nonzero(m)
                lon = T.c + (c0 + xs.mean() + 0.5) * T.a
                lat = T.f + (r0 + ys.mean() + 0.5) * T.e
                props = {
                    "area_km2": round(float(s) * 1e-4, 4),
                    "mean_prob": round(float(prob[m].mean()), 3),
                    "max_prob": round(float(prob[m].max()), 3),
                    "scene_time": scene_time,
                    "block_row": r0, "block_col": c0,
                }
                for geom, _ in shapes(m.astype("uint8"), mask=m, transform=win_T):
                    features.append({"type": "Feature", "geometry": geom,
                                     "properties": props})
                rows_out.append({"lat": round(lat, 5), "lon": round(lon, 5), **props})
                found += 1

            print(f"block r{r0} c{c0}: sea {(~invalid).mean():.0%}, "
                  f"flagged {pred[valid].mean():.3%}, detections {found}")
            if found:
                plt.figure(figsize=(8, 8))
                plt.imshow(x[1], cmap="gray")
                plt.imshow(np.where(invalid, 1.0, np.nan), cmap="Blues",
                           vmin=0, vmax=2, alpha=0.6)
                plt.contour(pred, levels=[0.5], colors="r", linewidths=0.6)
                plt.axis("off")
                plt.savefig(SCRIPT_DIR / f"sweep_r{r0}_c{c0}{OUTPUT_SUFFIX}.png",
                            dpi=90, bbox_inches="tight")
                plt.close()

with open(SCRIPT_DIR / f"detections{OUTPUT_SUFFIX}.geojson", "w") as f:
    json.dump({"type": "FeatureCollection", "features": features}, f)
if rows_out:
    with open(SCRIPT_DIR / f"detections{OUTPUT_SUFFIX}.csv", "w", newline="") as f:
        wr = csv.DictWriter(f, fieldnames=list(rows_out[0].keys()))
        wr.writeheader()
        wr.writerows(rows_out)

print(f"\nblocks processed: {blocks_done} | detections: {len(rows_out)} | "
      f"total area: {sum(r['area_km2'] for r in rows_out):.3f} km2")