"""
download_model.py — Downloads the UltraFace ONNX model required by the backend.

Run this once before starting the backend locally:
    python download_model.py

In Docker, the Dockerfile handles this automatically.
"""
import urllib.request
from pathlib import Path

MODEL_URL = (
    "https://github.com/Linzaer/Ultra-Light-Fast-Generic-Face-Detector-1MB"
    "/raw/refs/heads/master/models/onnx/version-RFB-320.onnx"
)
MODEL_PATH = Path(__file__).parent / "version-RFB-320.onnx"


def download():
    if MODEL_PATH.exists():
        print(f"Model already exists at {MODEL_PATH} — skipping download.")
        return
    print(f"Downloading UltraFace ONNX model to {MODEL_PATH} …")
    urllib.request.urlretrieve(MODEL_URL, MODEL_PATH)
    print("Done ✓")


if __name__ == "__main__":
    download()
