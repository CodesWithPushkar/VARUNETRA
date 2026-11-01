import numpy as np, rasterio, torch
from pathlib import Path
from rasterio.windows import Window
import segmentation_models_pytorch as smp
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

PROJECT_ROOT = Path(__file__).resolve().parents[2]
CKPT = PROJECT_ROOT / "model" / "best_model.pth"
ZENODO_IMAGE = PROJECT_ROOT / "temp" / "00014.tiff"
ZENODO_MASK = PROJECT_ROOT / "temp" / "00014 2.tiff"
SCENE = PROJECT_ROOT / "out" / "scene.tif"

PATCH, THRESH = 512, 0.5
device = "cuda" if torch.cuda.is_available() else "cpu"


def load_model():
    m = smp.Unet(encoder_name="resnet34", encoder_weights=None,
                 in_channels=2, classes=1, activation=None).to(device)
    ck = torch.load(CKPT, map_location=device, weights_only=False)
    m.load_state_dict(ck["model_state_dict"])
    return m.eval()


def normalize(arr):
    arr = arr.astype("float32").copy()
    bad = ~np.isfinite(arr)
    arr[bad] = np.nan
    valid = ~bad.any(axis=0)
    out = np.zeros_like(arr)
    for c in range(arr.shape[0]):
        v = arr[c][valid]
        if v.size == 0:
            continue
        lo, hi = np.percentile(v, 1), np.percentile(v, 99)
        if hi <= lo:
            hi = lo + 1e-6
        ch = np.nan_to_num(arr[c], nan=lo, posinf=hi, neginf=lo)
        out[c] = np.clip((ch - lo) / (hi - lo), 0, 1)
    return out.astype("float16").astype("float32"), valid


@torch.no_grad()
def predict(model, x):
    _, H, W = x.shape
    ys = list(range(0, max(H - PATCH, 0) + 1, PATCH))
    xs = list(range(0, max(W - PATCH, 0) + 1, PATCH))
    if ys[-1] + PATCH < H:
        ys.append(H - PATCH)
    if xs[-1] + PATCH < W:
        xs.append(W - PATCH)
    prob = np.zeros((H, W), "float32")
    cnt = np.zeros((H, W), "float32")
    for y in ys:
        for c in xs:
            t = torch.from_numpy(x[:, y:y + PATCH, c:c + PATCH][None]).to(device)
            p = torch.sigmoid(model(t))[0, 0].cpu().numpy()
            prob[y:y + PATCH, c:c + PATCH] += p
            cnt[y:y + PATCH, c:c + PATCH] += 1
    return prob / cnt


def run(model, arr, mask, out_png, label):
    x, valid = normalize(arr)
    prob = predict(model, x)
    pred = (prob > THRESH) & valid
    print(f"\n[{label}] size {arr.shape[1]}x{arr.shape[2]}, "
          f"valid {valid.mean():.1%}")
    print(f"  oil flagged: {pred[valid].mean():.2%} of valid pixels, "
          f"max prob {prob.max():.2f}")
    ncols = 3 if mask is not None else 2
    fig, ax = plt.subplots(1, ncols, figsize=(5 * ncols, 5))
    ax[0].imshow(x[1], cmap="gray"); ax[0].set_title("VV (normalized)")
    ax[1].imshow(prob, vmin=0, vmax=1); ax[1].set_title("oil probability")
    if mask is not None:
        m = mask > 0
        tp = (pred & m).sum(); fp = (pred & ~m).sum(); fn = (~pred & m).sum()
        dice = 2 * tp / (2 * tp + fp + fn + 1e-9)
        prec = tp / (tp + fp + 1e-9); rec = tp / (tp + fn + 1e-9)
        print(f"  vs mask: Dice {dice:.3f}  precision {prec:.3f}  recall {rec:.3f}")
        ax[2].imshow(m); ax[2].set_title("true mask")
    for a in ax:
        a.axis("off")
    plt.tight_layout(); plt.savefig(out_png, dpi=100); plt.close()
    print("  saved", out_png)


def find_sea_crops(path, size=2048, step=16, n=3):
    with rasterio.open(path) as src:
        H, W = src.height, src.width
        small = src.read(out_shape=(2, H // step, W // step)).astype("float32")
    vv = small[1]
    ok = np.isfinite(vv) & (vv != 0)
    w = size // step
    found = []
    for y in range(0, vv.shape[0] - w, w // 2):
        for x in range(0, vv.shape[1] - w, w // 2):
            win, okw = vv[y:y + w, x:x + w], ok[y:y + w, x:x + w]
            if okw.all() and win.mean() < -15:
                found.append((float(win.std()), y * step, x * step))
    found.sort()
    return found[:n]


if __name__ == "__main__":
    model = load_model()

    print("=== STEP 2: Zenodo patch ===")
    with rasterio.open(ZENODO_IMAGE) as s:
        arr = s.read().astype("float32")
    mask = None
    if ZENODO_MASK:
        with rasterio.open(ZENODO_MASK) as s:
            mask = s.read(1)
    run(model, arr, mask, "step2_zenodo.png", "zenodo")

    print("\n=== STEP 3: crops from our SNAP scene ===")
    crops = find_sea_crops(SCENE)
    if not crops:
        print("No fully valid sea-like 2048x2048 window found. "
              "Look at the scene in SNAP/QGIS and pick a crop by hand.")
    for i, (std, row, col) in enumerate(crops):
        with rasterio.open(SCENE) as s:
            arr = s.read(window=Window(col, row, 2048, 2048)).astype("float32")
        arr[arr == 0] = np.nan
        print(f"crop {i}: row {row}, col {col}, VV std {std:.2f}")
        for b, name in enumerate(["VH", "VV"]):
            v = arr[b][np.isfinite(arr[b])]
            print(f"  {name} mean {v.mean():.2f}  std {v.std():.2f}")
        run(model, arr, None, f"step3_crop{i}.png", f"scene crop {i}")