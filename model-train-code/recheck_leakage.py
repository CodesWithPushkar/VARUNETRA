"""
VARUNETRA — Leakage Re-Check (fixed version)

Your preprocessing already finished successfully and train.h5/val.h5 already contain the
correct data. The leakage check that ran at the end of preprocess_local.py had a bug: it
compared bare filenames across classes, but 'lookalike' and 'no_oil' both number their images
00000.tif-00684.tif independently, so identical filenames from DIFFERENT classes were flagged
as if they were the same image. This script re-checks correctly using (class, filename) pairs,
without touching or regenerating your existing h5 files.

Run: python recheck_leakage.py
"""

import h5py

TRAIN_H5_PATH = "./preprocessed/train.h5"
VAL_H5_PATH = "./preprocessed/val.h5"


def load_class_source_pairs(h5_path):
    with h5py.File(h5_path, "r") as hf:
        classes = [c.decode() if isinstance(c, bytes) else c for c in hf["class_labels"][:]]
        sources = [s.decode() if isinstance(s, bytes) else s for s in hf["source_image"][:]]
        n_patches = hf["images"].shape[0]
    pairs = set(zip(classes, sources))
    return pairs, n_patches


def main():
    train_pairs, n_train = load_class_source_pairs(TRAIN_H5_PATH)
    val_pairs, n_val = load_class_source_pairs(VAL_H5_PATH)

    overlap = train_pairs & val_pairs

    print(f"train.h5: {n_train} patches, {len(train_pairs)} unique (class, image) source pairs")
    print(f"val.h5:   {n_val} patches, {len(val_pairs)} unique (class, image) source pairs")

    if overlap:
        print(f"\n*** REAL LEAKAGE DETECTED: {len(overlap)} (class, image) pairs appear in "
              f"BOTH train and val ***")
        for c, s in sorted(overlap)[:20]:
            print(f"   class={c}  file={s}")
        if len(overlap) > 20:
            print(f"   ... and {len(overlap)-20} more")
        print("\nThis would be a real problem -- do not train on this data as-is.")
    else:
        print("\nNo leakage. Every (class, image) pair is exclusively in train OR val, never "
              "both. The earlier assertion failure was the false positive described above -- "
              "safe to proceed to train_local.py.")


if __name__ == "__main__":
    main()
