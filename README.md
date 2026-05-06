# 🎯 Real-Time Face Detection — Video Streaming System

A fully containerized system that accepts a live webcam feed, detects a face per frame using `dlib` (via `face_recognition`) and `Pillow`, stores the bounding-box (ROI) in PostgreSQL, and streams the annotated video back to the browser. **Zero OpenCV.**

---

## Architecture

![Architecture Diagram](./architecture.png)

```
Browser (Webcam)
     │
     │  JPEG frames (WebSocket)
     ▼
┌─────────────────────────────────────────────────┐
│            Docker Environment                   │
│                                                 │
│  ┌────────────────────────────────────────────┐ │
│  │         Backend (FastAPI)                  │ │
│  │                                            │ │
│  │  [WS]  /feed/ingest  ← receive frames      │ │
│  │         │                                  │ │
│  │         ├─ Pillow decode                   │ │
│  │         ├─ dlib HOG face detect            │ │
│  │         ├─ Pillow draw bounding box        │ │
│  │         ├─ asyncio.Queue → subscribers     │ │
│  │         └─ PostgreSQL write (ROI)          │ │
│  │                                            │ │
│  │  [GET] /feed/stream  → MJPEG stream        │ │
│  │  [GET] /api/roi      → ROI JSON            │ │
│  │  [GET] /api/roi/latest → latest ROI        │ │
│  └─────────────────────────┬──────────────────┘ │
│                            │ SQL                │
│  ┌─────────────────┐       │                    │
│  │   PostgreSQL    │◄──────┘                    │
│  │   (roi table)   │                            │
│  └─────────────────┘                            │
│                                                 │
│  ┌────────────────────────────────────────────┐ │
│  │      Frontend (React + Nginx)              │ │
│  │                                            │ │
│  │  getUserMedia → canvas → WS /feed/ingest   │ │
│  │  <img src="/feed/stream">  ← MJPEG         │ │
│  │  fetch /api/roi  ← ROI history             │ │
│  └────────────────────────────────────────────┘ │
└─────────────────────────────────────────────────┘
        ▲                         ▲
   :8000 (backend)           :5173 (frontend)
```

---

## Three API Endpoints

| # | Method | Path | Purpose |
|---|--------|------|---------|
| 1 | `WS` | `/feed/ingest` | Receive raw JPEG frames from browser |
| 2 | `GET` | `/feed/stream` | Serve annotated video as MJPEG |
| 3 | `GET` | `/api/roi` | Serve stored ROI data (paginated) |
| – | `GET` | `/api/roi/latest` | Most recent ROI record |
| – | `GET` | `/health` | Health check |

---

## Tech Stack

| Layer | Choice | Why |
|---|---|---|
| API Framework | **FastAPI** | Async-native, WebSocket + HTTP in one app, auto OpenAPI docs |
| Face Detection | **face_recognition** (dlib HOG) | No OpenCV, fast, single-face mode |
| Image Drawing | **Pillow** | Draw bounding box without OpenCV |
| Stream Format | **MJPEG** | Native browser `<img>` support, no JS decoding needed |
| Database | **PostgreSQL** | Structured tabular ROI data with time-ordered queries |
| ORM | **SQLAlchemy** (async) | Non-blocking DB access |
| Frontend | **React + Vite** | Fast HMR in dev, lean production build |
| Serving | **nginx** | Serves React SPA + reverse-proxies `/api` and `/ws` |
| Containers | **Docker Compose** | Three services: `db`, `backend`, `frontend` |

---

## Quick Start

### Prerequisites
- [Docker Desktop](https://www.docker.com/products/docker-desktop/)

### Run

```bash
cd "Real-Time Face Detection Video Streaming System"
docker-compose up --build
```

> ⚠️ **First build: 10–20 minutes** — `dlib` compiles from source inside the container. All subsequent builds use the Docker layer cache and are near-instant.

| Service | URL |
|---|---|
| 🌐 Frontend | http://localhost:5173 |
| ⚙️ Backend API | http://localhost:8000 |
| 📖 Auto API Docs | http://localhost:8000/docs |

### Usage

1. Open **http://localhost:5173**
2. Click **Start Stream** → allow camera access
3. The browser sends frames to `WS /feed/ingest`
4. The `<img>` tag loads `GET /feed/stream` — the annotated MJPEG feed appears
5. A green bounding box appears around the detected face
6. ROI coordinates (top/right/bottom/left) are saved to PostgreSQL
7. The ROI History panel (powered by `GET /api/roi`) shows all records

---

## Database Schema

**Table: `roi`** (PostgreSQL)

| Column | Type | Description |
|---|---|---|
| `id` | `serial PK` | Auto-increment primary key |
| `detected_at` | `timestamptz` | UTC timestamp of detection (indexed) |
| `box_top` | `integer` | Top pixel coordinate of bounding box |
| `box_right` | `integer` | Right pixel coordinate |
| `box_bottom` | `integer` | Bottom pixel coordinate |
| `box_left` | `integer` | Left pixel coordinate |

Derived values (computed on read, not stored):
- `width = box_right - box_left`
- `height = box_bottom - box_top`
- `area = width × height`
- `center_x`, `center_y`

---

## No OpenCV — Implementation Details

| Operation | Library Used |
|---|---|
| Decode JPEG frame | `PIL.Image.open()` |
| Convert to numpy array | `numpy.asarray()` |
| Detect face location | `face_recognition.face_locations()` (dlib HOG) |
| Draw bounding rectangle | `PIL.ImageDraw.Draw.rectangle()` |
| Draw label text | `PIL.ImageDraw.Draw.text()` |
| Encode to JPEG bytes | `PIL.Image.save(buf, "JPEG")` |

---

## Project Structure

```
.
├── docker-compose.yml
├── README.md
├── architecture.png          ← Architecture diagram
├── backend/
│   ├── Dockerfile
│   ├── requirements.txt
│   ├── main.py               ← All 3 endpoints
│   ├── models.py             ← SQLAlchemy ROI model
│   └── database.py           ← Async PostgreSQL engine
└── frontend/
    ├── Dockerfile            ← Vite build → nginx
    ├── nginx.conf            ← Reverse proxy /api, /ws
    ├── package.json
    ├── vite.config.js
    ├── index.html
    └── src/
        ├── main.jsx
        ├── App.jsx           ← Webcam + WS ingest + MJPEG display
        └── index.css         ← Dark glass design system
```
