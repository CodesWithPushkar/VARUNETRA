"""
VARUNETRA — Local Training (run AFTER preprocess_local.py has created train.h5 / val.h5)

HOW TO RUN (in VS Code):
    1. Make sure preprocess_local.py has already run successfully.
    2. Activate your venv in the VS Code terminal, then:
           python train_local.py
    3. Watch the printed epoch lines. Best checkpoint + ONNX export land in ./model/
"""

import os
import time
import json

import numpy as np
import torch
import torch.nn as nn
from torch.utils.data import Dataset, DataLoader
import h5py
import albumentations as A
import segmentation_models_pytorch as smp

# =============================================================================
# CONFIG
# =============================================================================
TRAIN_H5_PATH = "./preprocessed/train.h5"
VAL_H5_PATH = "./preprocessed/val.h5"
MODEL_DIR = "./model"

PATCH_SIZE = 512     # must match preprocess_local.py's PATCH_SIZE
IN_CHANNELS = 2
NUM_CLASSES = 1
ENCODER_NAME = "resnet34"
ENCODER_WEIGHTS = "imagenet"

# --- VRAM-safe defaults for a 6GB card (RTX 4050) ---------------------------
# If you get a CUDA out-of-memory error, lower BATCH_SIZE further (try 2, then 1).
# If training is stable and fast, you can try raising it to 6 or 8 to speed things up.
BATCH_SIZE = 4
NUM_WORKERS = 4          # i7-13th-gen has plenty of cores for data loading
LEARNING_RATE = 1e-3
WEIGHT_DECAY = 1e-4
NUM_EPOCHS = 40
EARLY_STOP_PATIENCE = 8
LR_SCHEDULER_PATIENCE = 3
LR_SCHEDULER_FACTOR = 0.5
USE_AMP = True           # mixed precision — important for fitting in 6GB VRAM
POS_WEIGHT = 3.0
DICE_LOSS_WEIGHT = 0.5
BCE_LOSS_WEIGHT = 0.5
SEGMENTATION_THRESHOLD = 0.5
SEED = 42

# No hard time limit locally (unlike Kaggle's ~9h session cap) — this is just a generous
# safety net so a runaway process doesn't run forever unattended.
MAX_TOTAL_RUNTIME_HOURS = 48

# =============================================================================
RUN_START = time.time()
torch.manual_seed(SEED)


def elapsed():
    return time.time() - RUN_START


def log(msg):
    print(f"[{elapsed():8.1f}s] {msg}", flush=True)


# ---- Dataset (MODULE LEVEL — must NOT be defined inside main(), or Windows'
#      multiprocessing 'spawn' start method cannot pickle it for DataLoader workers) --------
train_augment = A.Compose([
    A.HorizontalFlip(p=0.5),
    A.VerticalFlip(p=0.5),
    A.RandomRotate90(p=0.5),
])


class SARPatchDataset(Dataset):
    def __init__(self, h5_path, augment=False):
        self.h5_path = h5_path
        self.augment = augment
        self._h5 = None
        with h5py.File(h5_path, "r") as hf:
            self._length = hf["images"].shape[0]

    def _ensure_open(self):
        if self._h5 is None:
            self._h5 = h5py.File(self.h5_path, "r")

    def __len__(self):
        return self._length

    def __getitem__(self, idx):
        self._ensure_open()
        img = self._h5["images"][idx].astype(np.float32)
        mask = self._h5["masks"][idx].astype(np.float32)
        if self.augment:
            img_hwc = np.transpose(img, (1, 2, 0))
            augmented = train_augment(image=img_hwc, mask=mask)
            img = np.transpose(augmented["image"], (2, 0, 1))
            mask = augmented["mask"]
        img_t = torch.from_numpy(np.ascontiguousarray(img))
        mask_t = torch.from_numpy(np.ascontiguousarray(mask)).unsqueeze(0)
        return img_t, mask_t


def main():
    os.makedirs(MODEL_DIR, exist_ok=True)

    # ---- Device check --------------------------------------------------------------------
    cuda_available = torch.cuda.is_available()
    log(f"PyTorch version: {torch.__version__} | CUDA available: {cuda_available}")
    if cuda_available:
        gpu_name = torch.cuda.get_device_name(0)
        vram_gb = torch.cuda.get_device_properties(0).total_memory / (1024**3)
        log(f"GPU: {gpu_name} | VRAM: {vram_gb:.1f} GB")
        if vram_gb < 7:
            log(f"NOTE: {vram_gb:.1f}GB VRAM detected — if you hit a CUDA out-of-memory error, "
                f"lower BATCH_SIZE (currently {BATCH_SIZE}) at the top of this file and re-run.")
        device = torch.device("cuda")
    else:
        log("WARNING: no CUDA GPU detected by PyTorch. Check that you installed the CUDA build "
            "of PyTorch (see SETUP_INSTRUCTIONS.md), not the CPU-only build. Training will be "
            "extremely slow on CPU.")
        device = torch.device("cpu")

    if not (os.path.exists(TRAIN_H5_PATH) and os.path.exists(VAL_H5_PATH)):
        raise FileNotFoundError(
            f"Could not find {TRAIN_H5_PATH} / {VAL_H5_PATH}. Run preprocess_local.py first."
        )

    # ---- Dataset / DataLoader ------------------------------------------------------------
    train_dataset = SARPatchDataset(TRAIN_H5_PATH, augment=True)
    val_dataset = SARPatchDataset(VAL_H5_PATH, augment=False)
    train_loader = DataLoader(train_dataset, batch_size=BATCH_SIZE, shuffle=True,
                               num_workers=NUM_WORKERS, pin_memory=cuda_available, drop_last=True,
                               persistent_workers=(NUM_WORKERS > 0))
    val_loader = DataLoader(val_dataset, batch_size=BATCH_SIZE, shuffle=False,
                             num_workers=NUM_WORKERS, pin_memory=cuda_available,
                             persistent_workers=(NUM_WORKERS > 0))
    log(f"Train patches: {len(train_dataset)} | Val patches: {len(val_dataset)}")

    # ---- Model -----------------------------------------------------------------------------
    model = smp.Unet(encoder_name=ENCODER_NAME, encoder_weights=ENCODER_WEIGHTS,
                      in_channels=IN_CHANNELS, classes=NUM_CLASSES, activation=None).to(device)
    first_conv = next(m for m in model.encoder.modules() if isinstance(m, torch.nn.Conv2d))
    assert first_conv.in_channels == IN_CHANNELS
    n_params = sum(p.numel() for p in model.parameters())
    log(f"Model: U-Net / {ENCODER_NAME} | params: {n_params:,}")

    # ---- Loss / metrics ----------------------------------------------------------------------
    class DiceLoss(nn.Module):
        def __init__(self, smooth=1.0):
            super().__init__()
            self.smooth = smooth

        def forward(self, logits, targets):
            probs = torch.sigmoid(logits).view(logits.size(0), -1)
            targets = targets.view(targets.size(0), -1)
            intersection = (probs * targets).sum(dim=1)
            dice = (2 * intersection + self.smooth) / (probs.sum(dim=1) + targets.sum(dim=1) + self.smooth)
            return 1 - dice.mean()

    bce_loss_fn = nn.BCEWithLogitsLoss(pos_weight=torch.tensor(POS_WEIGHT, device=device))
    dice_loss_fn = DiceLoss()

    def combined_loss(logits, targets):
        return BCE_LOSS_WEIGHT * bce_loss_fn(logits, targets) + DICE_LOSS_WEIGHT * dice_loss_fn(logits, targets)

    @torch.no_grad()
    def compute_dice_iou(logits, targets, threshold=SEGMENTATION_THRESHOLD, eps=1e-7):
        preds = (torch.sigmoid(logits) > threshold).float()
        targets = targets.float()
        intersection = (preds * targets).sum(dim=(1, 2, 3))
        union = preds.sum(dim=(1, 2, 3)) + targets.sum(dim=(1, 2, 3)) - intersection
        dice = (2 * intersection + eps) / (preds.sum(dim=(1, 2, 3)) + targets.sum(dim=(1, 2, 3)) + eps)
        iou = (intersection + eps) / (union + eps)
        return dice.mean().item(), iou.mean().item()

    optimizer = torch.optim.AdamW(model.parameters(), lr=LEARNING_RATE, weight_decay=WEIGHT_DECAY)
    scheduler = torch.optim.lr_scheduler.ReduceLROnPlateau(optimizer, mode="max",
                                                            factor=LR_SCHEDULER_FACTOR,
                                                            patience=LR_SCHEDULER_PATIENCE)
    scaler = torch.amp.GradScaler("cuda", enabled=(USE_AMP and cuda_available))

    best_checkpoint_path = os.path.join(MODEL_DIR, "best_model.pth")
    history = {"train_loss": [], "val_loss": [], "val_dice": [], "val_iou": [], "epoch_time_s": []}
    best_val_dice = -1.0
    epochs_without_improvement = 0
    stop_reason = None

    log(f"Starting training: up to {NUM_EPOCHS} epochs, batch_size={BATCH_SIZE}, "
        f"AMP={'on' if scaler.is_enabled() else 'off'}, device={device}")

    for epoch in range(1, NUM_EPOCHS + 1):
        if elapsed() / 3600 > MAX_TOTAL_RUNTIME_HOURS:
            stop_reason = f"hit MAX_TOTAL_RUNTIME_HOURS={MAX_TOTAL_RUNTIME_HOURS}"
            log(f"STOPPING: {stop_reason}")
            break

        t0 = time.time()
        model.train()
        running_loss, n_batches = 0.0, 0
        for imgs, masks in train_loader:
            imgs, masks = imgs.to(device, non_blocking=True), masks.to(device, non_blocking=True)
            optimizer.zero_grad(set_to_none=True)
            with torch.autocast(device_type="cuda", enabled=scaler.is_enabled()):
                logits = model(imgs)
                loss = combined_loss(logits, masks)
            scaler.scale(loss).backward()
            scaler.step(optimizer)
            scaler.update()
            running_loss += loss.item()
            n_batches += 1
        train_loss = running_loss / max(n_batches, 1)

        model.eval()
        val_loss_sum, val_dice_sum, val_iou_sum, val_batches = 0.0, 0.0, 0.0, 0
        with torch.no_grad():
            for imgs, masks in val_loader:
                imgs, masks = imgs.to(device, non_blocking=True), masks.to(device, non_blocking=True)
                with torch.autocast(device_type="cuda", enabled=scaler.is_enabled()):
                    logits = model(imgs)
                    loss = combined_loss(logits, masks)
                dice, iou = compute_dice_iou(logits, masks)
                val_loss_sum += loss.item(); val_dice_sum += dice; val_iou_sum += iou; val_batches += 1
        val_loss = val_loss_sum / max(val_batches, 1)
        val_dice = val_dice_sum / max(val_batches, 1)
        val_iou = val_iou_sum / max(val_batches, 1)
        epoch_time = time.time() - t0
        scheduler.step(val_dice)

        history["train_loss"].append(train_loss)
        history["val_loss"].append(val_loss)
        history["val_dice"].append(val_dice)
        history["val_iou"].append(val_iou)
        history["epoch_time_s"].append(epoch_time)

        log(f"Epoch {epoch:03d}/{NUM_EPOCHS} | train_loss={train_loss:.4f} | val_loss={val_loss:.4f} | "
            f"val_dice={val_dice:.4f} | val_iou={val_iou:.4f} | epoch_time={epoch_time:.1f}s")

        if val_dice > best_val_dice:
            best_val_dice = val_dice
            epochs_without_improvement = 0
            torch.save({"epoch": epoch, "model_state_dict": model.state_dict(),
                        "val_dice": val_dice, "val_iou": val_iou,
                        "config": {"encoder_name": ENCODER_NAME, "in_channels": IN_CHANNELS,
                                   "num_classes": NUM_CLASSES, "patch_size": PATCH_SIZE}},
                       best_checkpoint_path)
            log(f"  New best val_dice={val_dice:.4f} -> saved {best_checkpoint_path}")
        else:
            epochs_without_improvement += 1
            if epochs_without_improvement >= EARLY_STOP_PATIENCE:
                stop_reason = f"early stopping (no improvement for {EARLY_STOP_PATIENCE} epochs)"
                log(f"STOPPING: {stop_reason}")
                break

    if stop_reason is None:
        stop_reason = "completed all configured epochs"
    log(f"Training finished: {stop_reason}. Best val_dice={best_val_dice:.4f}")

    # ---- Export ------------------------------------------------------------------------------
    if os.path.exists(best_checkpoint_path):
        export_onnx(device, best_checkpoint_path)
    else:
        log("No checkpoint saved — skipping export.")

    log("Done.")


def export_onnx(device, best_checkpoint_path):
    import onnx
    import onnxruntime as ort

    final_pt_path = os.path.join(MODEL_DIR, "varunetra_unet_resnet34.pt")
    final_onnx_path = os.path.join(MODEL_DIR, "varunetra_unet_resnet34.onnx")
    metadata_path = os.path.join(MODEL_DIR, "inference_metadata.json")

    model = smp.Unet(encoder_name=ENCODER_NAME, encoder_weights=None, in_channels=IN_CHANNELS,
                      classes=NUM_CLASSES, activation=None).to(device)
    ckpt = torch.load(best_checkpoint_path, map_location=device)
    model.load_state_dict(ckpt["model_state_dict"])
    model.eval()

    torch.save(model.state_dict(), final_pt_path)
    log(f"Saved PyTorch state_dict to {final_pt_path}")

    dummy_input = torch.randn(1, IN_CHANNELS, PATCH_SIZE, PATCH_SIZE, device=device)
    torch.onnx.export(model, dummy_input, final_onnx_path,
                       input_names=["input"], output_names=["logits"],
                       dynamic_axes={"input": {0: "batch"}, "logits": {0: "batch"}}, opset_version=17)
    log(f"Saved ONNX model to {final_onnx_path}")

    onnx_model = onnx.load(final_onnx_path)
    onnx.checker.check_model(onnx_model)
    ort_session = ort.InferenceSession(final_onnx_path, providers=["CPUExecutionProvider"])
    with torch.no_grad():
        torch_out = model(dummy_input).cpu().numpy()
    onnx_out = ort_session.run(None, {"input": dummy_input.cpu().numpy()})[0]
    max_diff = float(np.max(np.abs(torch_out - onnx_out)))
    log(f"PyTorch vs ONNX max abs diff: {max_diff:.6e} ({'OK' if max_diff < 1e-3 else 'WARNING'})")

    metadata = {
        "model_architecture": "Unet", "encoder": ENCODER_NAME, "input_channels": IN_CHANNELS,
        "channel_order": ["VV", "VH"], "patch_size": PATCH_SIZE,
        "normalization": {"method": "per_image_per_channel_percentile",
                           "percentile_low": 1, "percentile_high": 99,
                           "note": "Each channel's 1st/99th percentile (over valid pixels) is "
                                    "computed PER IMAGE, then clipped+scaled to [0,1]. This is "
                                    "NOT a fixed global range -- inference code must replicate "
                                    "this exact per-image percentile step, not a fixed constant."},
        "segmentation_threshold": SEGMENTATION_THRESHOLD,
        "class_definition": {"0": "not_oil", "1": "oil_spill"},
        "onnx_input_shape": [1, IN_CHANNELS, PATCH_SIZE, PATCH_SIZE],
        "dataset_citation": "https://doi.org/10.1016/j.marpolbul.2024.116549",
    }
    with open(metadata_path, "w") as f:
        json.dump(metadata, f, indent=2)
    log(f"Saved metadata to {metadata_path}")


if __name__ == "__main__":
    main()
