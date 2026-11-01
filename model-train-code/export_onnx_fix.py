"""
VARUNETRA — ONNX Export Fix (no retraining needed)

Your training already finished and best_model.pth is safe. The ONNX export step crashed with
"No module named 'onnxscript'" because your PyTorch version defaults to a newer export path
that needs that package. This script forces the older, more stable export path instead
(dynamo=False), so you don't need to install anything extra.

Run: python export_onnx_fix.py
"""

import os
import json
import numpy as np
import torch
import segmentation_models_pytorch as smp
import onnx
import onnxruntime as ort

BEST_CHECKPOINT_PATH = "./model/best_model.pth"
MODEL_DIR = "./model"
ENCODER_NAME = "resnet34"
IN_CHANNELS = 2
NUM_CLASSES = 1
PATCH_SIZE = 512
SEGMENTATION_THRESHOLD = 0.5


def main():
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    print(f"Using device: {device}")

    model = smp.Unet(encoder_name=ENCODER_NAME, encoder_weights=None, in_channels=IN_CHANNELS,
                      classes=NUM_CLASSES, activation=None).to(device)
    ckpt = torch.load(BEST_CHECKPOINT_PATH, map_location=device)
    model.load_state_dict(ckpt["model_state_dict"])
    model.eval()
    print(f"Loaded checkpoint from epoch {ckpt['epoch']} (val_dice={ckpt['val_dice']:.4f})")

    final_onnx_path = os.path.join(MODEL_DIR, "varunetra_unet_resnet34.onnx")
    metadata_path = os.path.join(MODEL_DIR, "inference_metadata.json")

    dummy_input = torch.randn(1, IN_CHANNELS, PATCH_SIZE, PATCH_SIZE, device=device)

    export_kwargs = dict(
        input_names=["input"], output_names=["logits"],
        dynamic_axes={"input": {0: "batch"}, "logits": {0: "batch"}},
        opset_version=17,
    )

    print("Exporting ONNX...")
    try:
        # Newer PyTorch versions accept dynamo=False to force the older, more stable exporter
        # instead of the newer one that needs the separate 'onnxscript' package.
        torch.onnx.export(model, dummy_input, final_onnx_path, dynamo=False, **export_kwargs)
    except TypeError:
        # This PyTorch version doesn't have a 'dynamo' parameter at all -- just use the default.
        torch.onnx.export(model, dummy_input, final_onnx_path, **export_kwargs)
    except ModuleNotFoundError as e:
        if "onnxscript" in str(e):
            print("\n*** Still needs 'onnxscript'. Run this, then re-run this script: ***")
            print("    pip install onnxscript")
            raise SystemExit(1)
        raise
    print(f"Saved ONNX model to {final_onnx_path} "
          f"({os.path.getsize(final_onnx_path)/(1024**2):.1f} MB)")

    onnx_model = onnx.load(final_onnx_path)
    onnx.checker.check_model(onnx_model)
    print("ONNX model passed structural validation.")

    ort_session = ort.InferenceSession(final_onnx_path, providers=["CPUExecutionProvider"])
    with torch.no_grad():
        torch_out = model(dummy_input).cpu().numpy()
    onnx_out = ort_session.run(None, {"input": dummy_input.cpu().numpy()})[0]
    max_diff = float(np.max(np.abs(torch_out - onnx_out)))
    print(f"PyTorch vs ONNX max abs diff: {max_diff:.6e} ({'OK' if max_diff < 1e-3 else 'WARNING'})")

    metadata = {
        "model_architecture": "Unet", "encoder": ENCODER_NAME, "input_channels": IN_CHANNELS,
        "channel_order": ["VV", "VH"], "patch_size": PATCH_SIZE,
        "normalization": {"method": "per_image_per_channel_percentile",
                           "percentile_low": 1, "percentile_high": 99,
                           "note": "Each channel's 1st/99th percentile (over valid pixels) is "
                                    "computed PER IMAGE, then clipped+scaled to [0,1]."},
        "segmentation_threshold": SEGMENTATION_THRESHOLD,
        "class_definition": {"0": "not_oil", "1": "oil_spill"},
        "onnx_input_shape": [1, IN_CHANNELS, PATCH_SIZE, PATCH_SIZE],
        "best_val_dice": float(ckpt["val_dice"]),
        "dataset_citation": "https://doi.org/10.1016/j.marpolbul.2024.116549",
    }
    with open(metadata_path, "w") as f:
        json.dump(metadata, f, indent=2)
    print(f"Saved metadata to {metadata_path}")
    print("\nDone. varunetra_unet_resnet34.onnx is the portable file to copy to another machine.")


if __name__ == "__main__":
    main()
