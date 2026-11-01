"""
VARUNETRA — Local Preprocessing (run this BEFORE train_local.py)

Reads already-extracted Sentinel-1 SAR TIFF images + masks from disk, tiles them into
patches, and writes compact train.h5 / val.h5 files for training.

HOW TO RUN (in VS Code):
    1. Edit CLASS_DIRS below to point at your actual folders.
    2. Open a terminal in VS Code (Terminal > New Terminal), activate your venv, then:
           python preprocess_local.py
"""

import os
import glob
import random
import time
from collections import defaultdict

import numpy as np
import tifffile
import h5py

# =============================================================================
# CONFIG — EDIT THESE PATHS to match your friend's actual folders
# =============================================================================
CLASS_DIRS = {
    "oil":       {"images": r"S:\Compressed\Oil\Images",       "masks": r"S:\Compressed\Oil\Mask_oil"},
    "lookalike": {"images": r"S:\Compressed\Lookalike\Images", "masks": r"S:\Compressed\Lookalike\Mask_lookalike"},
    "no_oil":    {"images": r"S:\Compressed\No_oil\Images",    "masks": r"S:\Compressed\No_oil\Mask_no_oil"},
}

OUTPUT_DIR = "./preprocessed"
TRAIN_H5_PATH = os.path.join(OUTPUT_DIR, "train.h5")
VAL_H5_PATH = os.path.join(OUTPUT_DIR, "val.h5")

SEED = 42
PATCH_SIZE = 512
PATCH_STRIDE = 512
MIN_VALID_FRACTION = 0.60
MAX_NEG_PATCHES_PER_IMAGE = 6
VAL_FRACTION = 0.15
IN_CHANNELS = 2

# Local disk is generally much less constrained than Kaggle's — these are generous defaults.
# Raise them if your friend has plenty of free space and wants a bigger training set.
PER_CLASS_BUDGET_GB = {"oil": 20.0, "lookalike": 10.0, "no_oil": 10.0}

HDF5_COMPRESSION = "gzip"
HDF5_COMPRESSION_LEVEL = 4

# =============================================================================
RUN_START = time.time()
rng = random.Random(SEED)


def log(msg):
    print(f"[{time.time()-RUN_START:8.1f}s] {msg}", flush=True)


IMG_EXTS = (".tif", ".tiff", ".TIF", ".TIFF")


def find_image_mask_pairs(images_dir, masks_dir):
    pairs, unmatched = [], []
    if not os.path.isdir(images_dir):
        log(f"  WARNING: images dir does not exist: {images_dir}")
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
        mp = mask_lookup.get(stem)
        (pairs if mp else unmatched).append((ip, mp) if mp else ip)
    return pairs, unmatched


def read_sar_image(path):
    """NOTE: requires the 'imagecodecs' package installed — many Sentinel-1 TIFFs in this
    dataset use LZW compression, which tifffile can only decode if imagecodecs is present.
    (This was the exact bug that silently killed every image read on the Kaggle run.)"""
    arr = np.asarray(tifffile.imread(path))
    if arr.ndim == 2:
        arr = np.stack([arr, arr], axis=0)
    elif arr.ndim == 3:
        if arr.shape[0] == 2:
            pass
        elif arr.shape[-1] == 2:
            arr = np.transpose(arr, (2, 0, 1))
        elif arr.shape[0] > 2:
            arr = arr[:2]
        elif arr.shape[-1] > 2:
            arr = np.transpose(arr, (2, 0, 1))[:2]
        else:
            raise ValueError(f"Unexpected TIFF shape {arr.shape} for {path}")
    else:
        raise ValueError(f"Unexpected TIFF ndim {arr.ndim} for {path}")
    arr = arr.astype(np.float32)
    nodata_mask = ~np.isfinite(arr)
    arr[nodata_mask] = np.nan
    return arr, nodata_mask.any(axis=0)


def read_mask(path):
    m = np.asarray(tifffile.imread(path))
    if m.ndim == 3:
        m = m[..., 0] if m.shape[-1] < m.shape[0] else m[0]
    return (m > 0).astype(np.uint8)


def normalize_sar(arr, valid_pixel_mask=None):
    """Per-image, per-channel percentile normalization (1st-99th), NOT a fixed dB range.

    A fixed clip range (e.g. -35..5 dB) assumes the TIFF pixel values are already calibrated
    Sigma0 backscatter in that exact range. If that assumption is wrong for this dataset, a
    fixed clip silently crushes almost every pixel to one extreme -> a near-blank patch fed to
    the model. Percentile-based stretch adapts to whatever the real value range turns out to be,
    which is also why manually contrast-stretching by percentile is what made the oil slick
    visible when checking a raw TIFF by eye.
    """
    out = np.zeros_like(arr, dtype=np.float32)
    for c in range(arr.shape[0]):
        channel = arr[c]
        if valid_pixel_mask is not None:
            valid_vals = channel[valid_pixel_mask & np.isfinite(channel)]
        else:
            valid_vals = channel[np.isfinite(channel)]
        if valid_vals.size == 0:
            out[c] = 0.0
            continue
        lo = np.percentile(valid_vals, 1)
        hi = np.percentile(valid_vals, 99)
        if hi <= lo:
            hi = lo + 1e-6
        channel_clean = np.nan_to_num(channel, nan=lo, posinf=hi, neginf=lo)
        out[c] = np.clip((channel_clean - lo) / (hi - lo), 0, 1)
    return out


def tile_coordinates(h, w, patch_size, stride):
    coords = []
    ys = list(range(0, max(h - patch_size, 0) + 1, stride))
    xs = list(range(0, max(w - patch_size, 0) + 1, stride))
    if not ys or ys[-1] + patch_size < h:
        ys.append(max(h - patch_size, 0))
    if not xs or xs[-1] + patch_size < w:
        xs.append(max(w - patch_size, 0))
    for y in sorted(set(ys)):
        for x in sorted(set(xs)):
            coords.append((y, x))
    return coords


def append_to_resizable_h5(h5_path, images_arr, masks_arr, cls_list, src_list):
    n_new = images_arr.shape[0]
    if n_new == 0:
        return
    file_exists = os.path.exists(h5_path)
    mode = "a" if file_exists else "w"
    with h5py.File(h5_path, mode) as hf:
        if not file_exists:
            hf.create_dataset("images", shape=(0, IN_CHANNELS, PATCH_SIZE, PATCH_SIZE),
                               maxshape=(None, IN_CHANNELS, PATCH_SIZE, PATCH_SIZE),
                               dtype=np.float16, chunks=(1, IN_CHANNELS, PATCH_SIZE, PATCH_SIZE),
                               compression=HDF5_COMPRESSION, compression_opts=HDF5_COMPRESSION_LEVEL)
            hf.create_dataset("masks", shape=(0, PATCH_SIZE, PATCH_SIZE),
                               maxshape=(None, PATCH_SIZE, PATCH_SIZE), dtype=np.uint8,
                               chunks=(1, PATCH_SIZE, PATCH_SIZE),
                               compression=HDF5_COMPRESSION, compression_opts=HDF5_COMPRESSION_LEVEL)
            str_dt = h5py.string_dtype(encoding="utf-8")
            hf.create_dataset("class_labels", shape=(0,), maxshape=(None,), dtype=str_dt)
            hf.create_dataset("source_image", shape=(0,), maxshape=(None,), dtype=str_dt)
        old_n = hf["images"].shape[0]
        new_n = old_n + n_new
        for name in ["images", "masks", "class_labels", "source_image"]:
            hf[name].resize(new_n, axis=0)
        hf["images"][old_n:new_n] = images_arr
        hf["masks"][old_n:new_n] = masks_arr
        hf["class_labels"][old_n:new_n] = cls_list
        hf["source_image"][old_n:new_n] = src_list


def process_class(cls, images_dir, masks_dir):
    log(f"=== Processing class: {cls} ===")
    pairs, unmatched = find_image_mask_pairs(images_dir, masks_dir)
    log(f"  {len(pairs)} matched image/mask pairs, {len(unmatched)} images without a mask.")
    if not pairs:
        return dict(status="no_pairs", n_images=0, n_train=0, n_val=0)

    paths = [p[0] for p in pairs]
    mask_of = {p[0]: p[1] for p in pairs}
    rng.shuffle(paths)
    n_val = max(1, int(round(len(paths) * VAL_FRACTION)))
    val_set = set(paths[:n_val])
    split_of = {p: ("val" if p in val_set else "train") for p in paths}
    log(f"  split: {len(paths)-n_val} train images / {n_val} val images")

    bytes_per_patch = PATCH_SIZE * PATCH_SIZE * (IN_CHANNELS * 2 + 1)
    budget_bytes = PER_CLASS_BUDGET_GB[cls] * (1024**3)
    max_neg_total = int(budget_bytes / (bytes_per_patch * 0.7))
    log(f"  negative-patch budget ~= {max_neg_total} patches (target {PER_CLASS_BUDGET_GB[cls]}GB)")

    n_pos_written, n_neg_written = 0, 0
    n_train_written, n_val_written = 0, 0
    images_processed, images_failed = 0, 0

    for image_path in paths:
        mask_path = mask_of[image_path]
        split = split_of[image_path]
        try:
            img, nodata = read_sar_image(image_path)
            mask = read_mask(mask_path)
        except Exception as e:
            images_failed += 1
            log(f"    ERROR reading {image_path}: {e}")
            continue

        h, w = img.shape[1], img.shape[2]
        if mask.shape != (h, w):
            images_failed += 1
            log(f"    ERROR: mask/image shape mismatch for {image_path}: mask={mask.shape} image={(h, w)}")
            continue

        valid_pixel_mask = ~nodata
        img_norm = normalize_sar(img, valid_pixel_mask)

        buf_imgs, buf_masks, buf_src = [], [], []
        n_neg_this_image = 0
        for (y, x) in tile_coordinates(h, w, PATCH_SIZE, PATCH_STRIDE):
            vpatch = valid_pixel_mask[y:y+PATCH_SIZE, x:x+PATCH_SIZE]
            if vpatch.size == 0 or vpatch.mean() < MIN_VALID_FRACTION:
                continue
            mpatch = mask[y:y+PATCH_SIZE, x:x+PATCH_SIZE]
            is_positive = bool(mpatch.any())

            if not is_positive:
                if n_neg_written >= max_neg_total or n_neg_this_image >= MAX_NEG_PATCHES_PER_IMAGE:
                    continue
                n_neg_this_image += 1

            buf_imgs.append(img_norm[:, y:y+PATCH_SIZE, x:x+PATCH_SIZE].astype(np.float16))
            buf_masks.append(mpatch)
            buf_src.append(os.path.basename(image_path))
            if is_positive:
                n_pos_written += 1
            else:
                n_neg_written += 1

        if buf_imgs:
            h5_target = TRAIN_H5_PATH if split == "train" else VAL_H5_PATH
            append_to_resizable_h5(h5_target, np.stack(buf_imgs, axis=0), np.stack(buf_masks, axis=0),
                                    [cls] * len(buf_imgs), buf_src)
            if split == "train":
                n_train_written += len(buf_imgs)
            else:
                n_val_written += len(buf_imgs)

        images_processed += 1
        if images_processed % 50 == 0:
            log(f"  {images_processed}/{len(paths)} images done | pos={n_pos_written} neg={n_neg_written}")

    log(f"  {cls} done: {images_processed} processed, {images_failed} failed, "
        f"{n_train_written} train patches, {n_val_written} val patches.")
    return dict(status="ok", n_images=images_processed, n_train=n_train_written, n_val=n_val_written)


def main():
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    for p in [TRAIN_H5_PATH, VAL_H5_PATH]:
        if os.path.exists(p):
            log(f"Removing existing {p} (fresh preprocessing run)")
            os.remove(p)

    summary = {}
    for cls, dirs in CLASS_DIRS.items():
        summary[cls] = process_class(cls, dirs["images"], dirs["masks"])

    log("=== Summary ===")
    for cls, s in summary.items():
        log(f"  {cls}: {s}")

    # Leakage sanity check
    if os.path.exists(TRAIN_H5_PATH) and os.path.exists(VAL_H5_PATH):
        with h5py.File(TRAIN_H5_PATH, "r") as ht, h5py.File(VAL_H5_PATH, "r") as hv:
            train_src = set(s.decode() if isinstance(s, bytes) else s for s in ht["source_image"][:])
            val_src = set(s.decode() if isinstance(s, bytes) else s for s in hv["source_image"][:])
            overlap = train_src & val_src
            assert not overlap, f"LEAKAGE: {overlap}"
            log(f"Leakage check passed. train.h5: {ht['images'].shape[0]} patches | "
                f"val.h5: {hv['images'].shape[0]} patches")
    else:
        log("WARNING: train.h5/val.h5 were not created — check errors above.")

    log("Done. Next: run train_local.py")


if __name__ == "__main__":
    main()
