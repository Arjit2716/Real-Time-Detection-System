"""
main.py — FastAPI backend for Real-Time Face Detection

Face detection: UltraFace ONNX model via onnxruntime
               (No OpenCV, No dlib, No compilation required)
Image drawing:  Pillow (PIL)
Database:       SQLite via aiosqlite + SQLAlchemy async

Three endpoints
───────────────
  1. WS  /feed/ingest      Receive raw JPEG frames from browser
  2. GET /feed/stream       Serve annotated video as MJPEG stream
  3. GET /api/roi           Return stored ROI records from DB
  GET /api/roi/latest       Most recent ROI
  GET /health               Health check
"""

from __future__ import annotations

import asyncio
import base64
import io
import logging
import os
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncGenerator

import numpy as np
import onnxruntime as ort
from fastapi import Depends, FastAPI, Query, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse, JSONResponse
from PIL import Image, ImageDraw
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from database import AsyncSessionLocal, get_db, init_db
from models import ROI

# ── Logging ───────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
)
log = logging.getLogger("facedetect")


# ── UltraFace ONNX Detector ───────────────────────────────────────────────────
# Model: Ultra-Light-Fast-Generic-Face-Detector-1MB (RFB-320)
# Input:  1 × 3 × 240 × 320  (BGR, normalized)
# Output: [1, N, 4] boxes, [1, N, 2] scores
MODEL_PATH = Path(__file__).parent / "version-RFB-320.onnx"
INPUT_W, INPUT_H = 320, 240          # Model input dimensions
CONF_THRESHOLD = 0.75                 # Minimum face confidence


class UltraFaceDetector:
    """Thin wrapper around the UltraFace ONNX model."""

    def __init__(self, model_path: str | Path):
        self._session = ort.InferenceSession(
            str(model_path),
            providers=["CPUExecutionProvider"],
        )
        self._input_name = self._session.get_inputs()[0].name
        log.info("UltraFace ONNX model loaded from %s", model_path)

    def detect(self, pil_img: Image.Image) -> tuple[int, int, int, int] | None:
        """
        Detect the most prominent face in a PIL RGB image.
        Returns (top, right, bottom, left) in original image pixels, or None.
        No OpenCV used anywhere.
        """
        orig_w, orig_h = pil_img.size

        # 1. Resize to model input (pure Pillow — no OpenCV)
        resized = pil_img.resize((INPUT_W, INPUT_H), Image.BILINEAR)

        # 2. Convert to numpy, normalize to [-1, 1]
        arr = np.asarray(resized, dtype=np.float32)           # H×W×3  RGB
        arr = (arr / 127.5) - 1.0                             # normalize
        arr = arr.transpose(2, 0, 1)[np.newaxis, ...]         # 1×3×H×W

        # 3. Run inference
        confidences, boxes = self._session.run(None, {self._input_name: arr})
        # confidences: (1, N, 2)   boxes: (1, N, 4)  cx1,cy1,cx2,cy2 normalized

        confidences = confidences[0]   # (N, 2)
        boxes = boxes[0]               # (N, 4)

        # 4. Pick best face (class 1 = face)
        face_scores = confidences[:, 1]
        best_idx = int(np.argmax(face_scores))
        best_score = float(face_scores[best_idx])

        if best_score < CONF_THRESHOLD:
            return None

        # 5. Convert from normalized coords to original pixel coords
        x1_n, y1_n, x2_n, y2_n = boxes[best_idx]
        x1 = max(0, int(x1_n * orig_w))
        y1 = max(0, int(y1_n * orig_h))
        x2 = min(orig_w, int(x2_n * orig_w))
        y2 = min(orig_h, int(y2_n * orig_h))

        # Return as (top, right, bottom, left) — our convention
        return (y1, x2, y2, x1)


# Load detector at import time (shared across all requests)
if MODEL_PATH.exists():
    _detector = UltraFaceDetector(MODEL_PATH)
else:
    _detector = None
    log.error("ONNX model not found at %s — face detection disabled!", MODEL_PATH)


# ── Frame broker ──────────────────────────────────────────────────────────────
_latest_frame: bytes | None = None
_stream_queues: list[asyncio.Queue[bytes]] = []

# ── Visual constants ──────────────────────────────────────────────────────────
BOX_COLOUR       = (0, 255, 127, 255)
BOX_WIDTH        = 3
LABEL_BG         = (10, 15, 35, 210)
LABEL_FG         = (0, 255, 127, 255)
JPEG_QUALITY     = 80
ROI_SAVE_INTERVAL = 1.0
MJPEG_BOUNDARY   = b"--frameboundary"


# ── Lifespan ──────────────────────────────────────────────────────────────────
@asynccontextmanager
async def lifespan(app: FastAPI):
    log.info("Initialising database …")
    await init_db()
    log.info("Database ready ✓")
    yield
    log.info("Shutdown.")


# ── App ───────────────────────────────────────────────────────────────────────
app = FastAPI(
    title="Face Detection API",
    version="2.0.0",
    description=(
        "Real-time face detection via UltraFace ONNX (no OpenCV). "
        "Three endpoints: WS /feed/ingest, GET /feed/stream, GET /api/roi."
    ),
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _decode_frame(data: str | bytes) -> Image.Image:
    """Decode a base64 data-URL or raw bytes into a Pillow RGB image."""
    if isinstance(data, str):
        if "," in data:
            data = data.split(",", 1)[1]
        raw = base64.b64decode(data)
    else:
        raw = data
    return Image.open(io.BytesIO(raw)).convert("RGB")


def _annotate(img: Image.Image, loc: tuple) -> Image.Image:
    """
    Draw axis-aligned bounding box on img using Pillow only. No OpenCV.
    loc = (top, right, bottom, left)
    """
    top, right, bottom, left = loc
    draw = ImageDraw.Draw(img, "RGBA")

    # Bounding rectangle
    draw.rectangle([left, top, right, bottom], outline=BOX_COLOUR, width=BOX_WIDTH)

    # Label badge
    badge_y = max(0, top - 22)
    draw.rectangle([left, badge_y, left + 58, badge_y + 20], fill=LABEL_BG)
    draw.text((left + 4, badge_y + 3), "Face", fill=LABEL_FG)

    # Corner accent marks (purely aesthetic)
    mark = 12
    for cx, cy, dx, dy in [
        (left,  top,     1,  1),
        (right, top,    -1,  1),
        (left,  bottom,  1, -1),
        (right, bottom, -1, -1),
    ]:
        draw.line([(cx, cy), (cx + dx * mark, cy)],  fill=BOX_COLOUR, width=2)
        draw.line([(cx, cy), (cx, cy + dy * mark)],   fill=BOX_COLOUR, width=2)

    return img


def _encode_jpeg(img: Image.Image) -> bytes:
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=JPEG_QUALITY)
    return buf.getvalue()


def _make_placeholder() -> bytes:
    img = Image.new("RGB", (640, 480), (8, 11, 20))
    draw = ImageDraw.Draw(img)
    draw.rectangle([1, 1, 638, 478], outline=(30, 55, 85), width=2)
    draw.text((320, 220), "🎯 FaceDetect — Waiting for stream", fill=(70, 120, 160), anchor="mm")
    draw.text((320, 250), "Connect via  WS /feed/ingest",       fill=(45, 80, 115),  anchor="mm")
    draw.text((320, 272), "Stream at    GET /feed/stream",       fill=(45, 80, 115),  anchor="mm")
    draw.text((320, 294), "ROI data at  GET /api/roi",           fill=(45, 80, 115),  anchor="mm")
    return _encode_jpeg(img)


PLACEHOLDER = _make_placeholder()


async def _broadcast(frame: bytes):
    global _latest_frame
    _latest_frame = frame
    stale = []
    for q in _stream_queues:
        try:
            q.put_nowait(frame)
        except asyncio.QueueFull:
            stale.append(q)
    for q in stale:
        try:
            _stream_queues.remove(q)
        except ValueError:
            pass


# ─────────────────────────────────────────────────────────────────────────────
# ENDPOINT 1 — WS /feed/ingest
# ─────────────────────────────────────────────────────────────────────────────

@app.websocket("/feed/ingest")
async def feed_ingest(websocket: WebSocket):
    """
    Endpoint 1: Receive raw JPEG frames from the browser.

    Client sends:  JPEG as base64 data-URL  or  raw binary bytes
    For each frame:
      - Decode via Pillow (no OpenCV)
      - Detect face via UltraFace ONNX (no OpenCV)
      - Draw bounding box via Pillow (no OpenCV)
      - Broadcast annotated JPEG to all /feed/stream subscribers
      - Persist ROI to SQLite (rate-limited to 1/second)
    """
    await websocket.accept()
    log.info("Ingest WebSocket connected.")
    last_save = 0.0

    try:
        while True:
            try:
                msg = await asyncio.wait_for(websocket.receive(), timeout=30)
            except asyncio.TimeoutError:
                log.warning("Ingest timeout — closing.")
                break

            raw = msg.get("bytes") or msg.get("text")
            if not raw:
                continue

            # Decode
            try:
                pil = _decode_frame(raw)
            except Exception as exc:
                log.warning("Decode error: %s", exc)
                continue

            # Detect (ONNX — run in thread to not block event loop)
            loc = await asyncio.get_event_loop().run_in_executor(
                None, lambda: _detector.detect(pil.copy()) if _detector else None
            )

            # Annotate
            if loc:
                pil = _annotate(pil, loc)

            # Broadcast to MJPEG clients
            await _broadcast(_encode_jpeg(pil))

            # Persist ROI (1 per second max)
            now = time.monotonic()
            if loc and (now - last_save) >= ROI_SAVE_INTERVAL:
                top, right, bottom, left = loc
                try:
                    async with AsyncSessionLocal() as session:
                        session.add(ROI(
                            box_top=top, box_right=right,
                            box_bottom=bottom, box_left=left,
                        ))
                        await session.commit()
                    last_save = now
                except Exception as exc:
                    log.error("DB write error: %s", exc)

    except WebSocketDisconnect:
        log.info("Ingest client disconnected.")
    except Exception as exc:
        log.exception("Ingest unexpected error: %s", exc)
    finally:
        try:
            await websocket.close()
        except Exception:
            pass


# ─────────────────────────────────────────────────────────────────────────────
# ENDPOINT 2 — GET /feed/stream
# ─────────────────────────────────────────────────────────────────────────────

async def _mjpeg_gen(queue: asyncio.Queue[bytes]) -> AsyncGenerator[bytes, None]:
    try:
        while True:
            try:
                frame = await asyncio.wait_for(queue.get(), timeout=5.0)
            except asyncio.TimeoutError:
                frame = _latest_frame or PLACEHOLDER
            yield (
                MJPEG_BOUNDARY + b"\r\n"
                b"Content-Type: image/jpeg\r\n"
                b"Content-Length: " + str(len(frame)).encode() + b"\r\n"
                b"\r\n" + frame + b"\r\n"
            )
    except asyncio.CancelledError:
        pass


@app.get("/feed/stream", summary="Endpoint 2: MJPEG stream of annotated video")
async def feed_stream():
    """
    Returns an MJPEG multipart stream of annotated video frames.
    Can be loaded directly by an HTML <img> tag:

        <img src="http://localhost:8000/feed/stream">

    Each frame has the face bounding box drawn on it with Pillow.
    """
    q: asyncio.Queue[bytes] = asyncio.Queue(maxsize=4)
    _stream_queues.append(q)
    if _latest_frame:
        await q.put(_latest_frame)
    return StreamingResponse(
        _mjpeg_gen(q),
        media_type="multipart/x-mixed-replace; boundary=frameboundary",
    )


# ─────────────────────────────────────────────────────────────────────────────
# ENDPOINT 3 — GET /api/roi
# ─────────────────────────────────────────────────────────────────────────────

@app.get("/api/roi", summary="Endpoint 3: Stored ROI records")
async def get_roi(
    limit: int = Query(default=50, ge=1, le=500),
    db: AsyncSession = Depends(get_db),
):
    """
    Returns the most recent face ROI (bounding box) records from the database.

    Each record contains:
      - bounding_box: { top, right, bottom, left } (pixel coords)
      - dimensions:   { width, height, area, center_x, center_y }
      - detected_at:  ISO 8601 timestamp
    """
    result = await db.execute(
        select(ROI).order_by(ROI.detected_at.desc()).limit(limit)
    )
    rows = result.scalars().all()
    return {"count": len(rows), "data": [r.to_dict() for r in rows]}


@app.get("/api/roi/latest", summary="Most recent ROI record")
async def get_roi_latest(db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(ROI).order_by(ROI.detected_at.desc()).limit(1)
    )
    roi = result.scalar_one_or_none()
    return {"data": roi.to_dict() if roi else None}


# ─────────────────────────────────────────────────────────────────────────────
# Health
# ─────────────────────────────────────────────────────────────────────────────

@app.get("/health", include_in_schema=False)
async def health():
    return {
        "status": "ok",
        "detector": "UltraFace ONNX (onnxruntime)",
        "model_loaded": _detector is not None,
        "version": "2.0.0",
    }
