r"""
VARUNETRA — Test Evaluation on Part III (held-out test set)

Expects a folder structure like:
    TEST_ROOT/images/oil/...tif        TEST_ROOT/masks/oil/...tif
    TEST_ROOT/images/no_oil/...tif     TEST_ROOT/masks/no_oil/...tif
    TEST_ROOT/images/lookalike/...tif  TEST_ROOT/masks/lookalike/...tif

Edit TEST_ROOT and CLASS_FOLDER_NAMES below to match your actual folder names, then run:
    python test_local.py

Uses the SAME read_sar_image() / normalize_sar() / tile_coordinates() as training (imported
directly from preprocess_local.py) so evaluation is preprocessed identically to how the model
was trained -- this matters a lot given the per-image percentile normalization.
"""

import os
import glob
import numpy as np
import torch
import segmentation_models_pytorch as smp

from preprocess_local import (
    read_sar_image, read_mask, normalize_sar, tile_coordinates,
    PATCH_SIZE, PATCH_STRIDE, MIN_VALID_FRACTION, IMG_EXTS,
)

# =============================================================================
# CONFIG — EDIT THESE
# =============================================================================
TEST_ROOT = r"S:\Compressed\test"   # folder containing "Images" and "Mask" subfolders

# If your folder is named "no_oil" instead of "nooil" (or vice versa), fix it here.
CLASS_FOLDER_NAMES = {
    "oil": "oil",
    "no_oil": "no_oil",
    "lookalike": "lookalike",
}

BEST_CHECKPOINT_PATH = "./model/best_model.pth"
ENCODER_NAME = "resnet34"
IN_CHANNELS = 2
NUM_CLASSES = 1
SEGMENTATION_THRESHOLD = 0.5

# =============================================================================


def find_image_mask_pairs(images_dir, masks_dir):
    pairs, unmatched = [], []
    if not os.path.isdir(images_dir):
        print(f"  WARNING: images dir does not exist: {images_dir}")
        return pairs, unmatched
    image_files = []
    for ext in IMG_EXTS:
        image_files.extend(glob.glob(os.path.join(images_dir, "**", f"*{ext}"), recursive=True))
    image_files = sorted(set(image_files))
    mask_lookup = {}
    if os.path.isdir(masks_dir):
        for ext in IMG_EXTS:
            for mp in glob.glob(os.path.join(masks_dir, "**", f"*{ext}"), recursive=True):
                mask_lookup[os.path.splitext(os.path.basename(mp))[0]] = mp
    for ip in image_files:
        stem = os.path.splitext(os.path.basename(ip))[0]
        mp = mask_lookup.get(stem) or mask_lookup.get(f"{stem}_segmentation")
        (pairs if mp else unmatched).append((ip, mp) if mp else ip)
    return pairs, unmatched


def load_model(device):
    model = smp.Unet(encoder_name=ENCODER_NAME, encoder_weights=None, in_channels=IN_CHANNELS,
                      classes=NUM_CLASSES, activation=None).to(device)
    ckpt = torch.load(BEST_CHECKPOINT_PATH, map_location=device)
    model.load_state_dict(ckpt["model_state_dict"])
    model.eval()
    print(f"Loaded checkpoint from epoch {ckpt['epoch']} (val_dice={ckpt['val_dice']:.4f})")
    return model


@torch.no_grad()
def evaluate_image(model, device, image_path, mask_path):
    img, nodata = read_sar_image(image_path)
    mask = read_mask(mask_path)
    h, w = img.shape[1], img.shape[2]
    if mask.shape != (h, w):
        print(f"    ERROR: mask/image shape mismatch for {image_path}: mask={mask.shape} image={(h,w)}")
        return []

    valid_pixel_mask = ~nodata
    img_norm = normalize_sar(img, valid_pixel_mask)

    results = []
    for (y, x) in tile_coordinates(h, w, PATCH_SIZE, PATCH_STRIDE):
        vpatch = valid_pixel_mask[y:y+PATCH_SIZE, x:x+PATCH_SIZE]
        if vpatch.size == 0 or vpatch.mean() < MIN_VALID_FRACTION:
            continue
        img_patch = img_norm[:, y:y+PATCH_SIZE, x:x+PATCH_SIZE]
        mask_patch = mask[y:y+PATCH_SIZE, x:x+PATCH_SIZE]

        img_t = torch.from_numpy(img_patch).unsqueeze(0).to(device, dtype=torch.float32)
        probs = torch.sigmoid(model(img_t))[0, 0].cpu().numpy()
        preds = (probs > SEGMENTATION_THRESHOLD).astype(np.uint8)
        gt = mask_patch.astype(np.uint8)

        tp = int(((preds == 1) & (gt == 1)).sum())
        fp = int(((preds == 1) & (gt == 0)).sum())
        fn = int(((preds == 0) & (gt == 1)).sum())
        tn = int(((preds == 0) & (gt == 0)).sum())
        dice = (2 * tp + 1e-7) / (2 * tp + fp + fn + 1e-7)
        iou = (tp + 1e-7) / (tp + fp + fn + 1e-7)
        results.append(dict(dice=dice, iou=iou, tp=tp, fp=fp, fn=fn, tn=tn))
    return results


def main():
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    print(f"Using device: {device}")
    model = load_model(device)

    per_class_metrics = {}

    for cls, folder_name in CLASS_FOLDER_NAMES.items():
        images_dir = os.path.join(TEST_ROOT, "Images", folder_name)
        masks_dir = os.path.join(TEST_ROOT, "Mask", folder_name)
        pairs, unmatched = find_image_mask_pairs(images_dir, masks_dir)
        print(f"\n=== {cls}: {len(pairs)} matched pairs, {len(unmatched)} unmatched ===")
        if not pairs:
            print(f"  Skipping {cls} -- no pairs found. Check TEST_ROOT/CLASS_FOLDER_NAMES.")
            continue

        all_results = []
        for i, (image_path, mask_path) in enumerate(pairs):
            all_results.extend(evaluate_image(model, device, image_path, mask_path))
            if (i + 1) % 25 == 0:
                print(f"  {i+1}/{len(pairs)} images evaluated...")

        if not all_results:
            print(f"  WARNING: no valid patches produced for class '{cls}'.")
            continue

        dices = [r["dice"] for r in all_results]
        ious = [r["iou"] for r in all_results]
        tp = sum(r["tp"] for r in all_results)
        fp = sum(r["fp"] for r in all_results)
        fn = sum(r["fn"] for r in all_results)
        tn = sum(r["tn"] for r in all_results)
        precision = tp / (tp + fp + 1e-7)
        recall = tp / (tp + fn + 1e-7)
        fpr = fp / (fp + tn + 1e-7)

        per_class_metrics[cls] = dict(
            n_images=len(pairs), n_patches=len(all_results),
            mean_dice=float(np.mean(dices)), mean_iou=float(np.mean(ious)),
            precision=precision, recall=recall, fpr=fpr,
        )
        print(f"  [{cls}] images={len(pairs)} patches={len(all_results)} "
              f"mean_dice={np.mean(dices):.4f} mean_iou={np.mean(ious):.4f} "
              f"precision={precision:.4f} recall={recall:.4f} FPR={fpr:.4f}")

    print("\n" + "=" * 70)
    print("FINAL TEST RESULTS")
    print("=" * 70)
    if per_class_metrics:
        total_patches = sum(m["n_patches"] for m in per_class_metrics.values())
        overall_dice = sum(m["mean_dice"] * m["n_patches"] for m in per_class_metrics.values()) / total_patches
        overall_iou = sum(m["mean_iou"] * m["n_patches"] for m in per_class_metrics.values()) / total_patches
        for cls, m in per_class_metrics.items():
            print(f"  {cls:12s}: Dice={m['mean_dice']:.4f}  IoU={m['mean_iou']:.4f}  "
                  f"Precision={m['precision']:.4f}  Recall={m['recall']:.4f}  FPR={m['fpr']:.4f}  "
                  f"(n_images={m['n_images']}, n_patches={m['n_patches']})")
        print(f"\n  OVERALL (patch-count weighted): Dice={overall_dice:.4f}  IoU={overall_iou:.4f}")
        if "lookalike" in per_class_metrics:
            print(f"\n  Lookalike FPR is the key number for false-positive robustness: "
                  f"{per_class_metrics['lookalike']['fpr']:.4f}")
    else:
        print("  No results -- check folder paths in TEST_ROOT / CLASS_FOLDER_NAMES.")


if __name__ == "__main__":
    main()
