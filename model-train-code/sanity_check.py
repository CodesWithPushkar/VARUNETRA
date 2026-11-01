r"""
VARUNETRA — Sanity Check (run this BEFORE the 6-hour training run)

Loads ONE real image+mask pair, runs it through the exact same read_sar_image() +
normalize_sar() + tiling code that preprocess_local.py uses, and saves a PNG so you can
actually SEE what the model will be trained on -- both channels, plus the mask, plus a
mask-overlay -- for a few patches that contain oil, before spending hours training on it.

HOW TO RUN:
    python sanity_check.py "C:\path\to\one\oil\image.tif" "C:\path\to\its\mask.tif"

If this looks wrong (all one shade, no visible slick where the mask says there should be one,
mask misaligned with the image), STOP and fix preprocessing before running train_local.py --
don't find out after 6 hours.
"""

import sys
import numpy as np
import matplotlib.pyplot as plt

from preprocess_local import (
    read_sar_image, read_mask, normalize_sar, tile_coordinates,
    PATCH_SIZE, PATCH_STRIDE, MIN_VALID_FRACTION,
)


def main():
    if len(sys.argv) != 3:
        print('Usage: python sanity_check.py "<image.tif>" "<mask.tif>"')
        sys.exit(1)

    image_path, mask_path = sys.argv[1], sys.argv[2]

    print(f"Reading image: {image_path}")
    img, nodata = read_sar_image(image_path)
    print(f"Reading mask:  {mask_path}")
    mask = read_mask(mask_path)

    print(f"\nRaw image shape: {img.shape}, dtype: {img.dtype}")
    for c in range(img.shape[0]):
        ch = img[c][np.isfinite(img[c])]
        print(f"  Channel {c}: min={ch.min():.3f} max={ch.max():.3f} "
              f"mean={ch.mean():.3f} p1={np.percentile(ch,1):.3f} p99={np.percentile(ch,99):.3f}")
    print(f"Mask shape: {mask.shape}, unique values: {np.unique(mask)}, "
          f"oil pixels: {mask.sum()} ({100*mask.mean():.3f}% of image)")

    if mask.shape != img.shape[1:]:
        print(f"\n*** WARNING: mask shape {mask.shape} does not match image shape "
              f"{img.shape[1:]} -- these will NOT align correctly! ***")

    valid_pixel_mask = ~nodata
    img_norm = normalize_sar(img, valid_pixel_mask)
    print(f"\nAfter normalize_sar(): min={img_norm.min():.3f} max={img_norm.max():.3f} "
          f"mean={img_norm.mean():.3f}")
    if img_norm.max() - img_norm.min() < 0.05:
        print("*** WARNING: normalized image is nearly constant -- this patch would be "
              "almost blank to the model. Something is likely wrong with normalization "
              "or this image has no real signal. ***")

    # Find patches that actually contain oil, so we're looking at the interesting case
    h, w = img.shape[1], img.shape[2]
    coords = tile_coordinates(h, w, PATCH_SIZE, PATCH_STRIDE)
    positive_coords = []
    for (y, x) in coords:
        vpatch = valid_pixel_mask[y:y+PATCH_SIZE, x:x+PATCH_SIZE]
        if vpatch.size == 0 or vpatch.mean() < MIN_VALID_FRACTION:
            continue
        mpatch = mask[y:y+PATCH_SIZE, x:x+PATCH_SIZE]
        if mpatch.any():
            positive_coords.append((y, x))

    print(f"\n{len(positive_coords)} of {len(coords)} valid patches contain oil pixels.")
    if not positive_coords:
        print("No oil-containing patches found in this image -- pick a different image that "
              "you know has a visible spill, or this image's mask might be all-zero.")
        show_coords = coords[:3]
    else:
        show_coords = positive_coords[:3]

    n_show = len(show_coords)
    fig, axes = plt.subplots(n_show, 4, figsize=(16, 4 * n_show))
    if n_show == 1:
        axes = axes.reshape(1, -1)

    for i, (y, x) in enumerate(show_coords):
        img_patch = img_norm[:, y:y+PATCH_SIZE, x:x+PATCH_SIZE]
        mask_patch = mask[y:y+PATCH_SIZE, x:x+PATCH_SIZE]

        axes[i, 0].imshow(img_patch[0], cmap="gray", vmin=0, vmax=1)
        axes[i, 0].set_title(f"Channel 0 (patch @ {y},{x})")
        axes[i, 1].imshow(img_patch[1], cmap="gray", vmin=0, vmax=1)
        axes[i, 1].set_title("Channel 1")
        axes[i, 2].imshow(mask_patch, cmap="gray")
        axes[i, 2].set_title(f"Mask (oil px: {mask_patch.sum()})")

        overlay = np.stack([img_patch[0], img_patch[0], img_patch[0]], axis=-1)
        overlay[mask_patch > 0] = [1, 0, 0]  # red where mask says oil
        axes[i, 3].imshow(overlay)
        axes[i, 3].set_title("Channel 0 + mask overlay (red)")

        for j in range(4):
            axes[i, j].axis("off")

    plt.tight_layout()
    out_path = "sanity_check_output.png"
    plt.savefig(out_path, dpi=120)
    print(f"\nSaved visualization to {out_path} -- open it and check:")
    print("  1. Does channel 0 and/or channel 1 show a visible dark streak/patch (the oil)?")
    print("  2. Does the red overlay actually sit ON TOP of that dark streak (alignment correct)?")
    print("  3. Does it look like recognizable texture, not near-uniform gray (normalization OK)?")


if __name__ == "__main__":
    main()
